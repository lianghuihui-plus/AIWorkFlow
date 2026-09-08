from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import run_cli

from aiwf_core.model import now_iso
from aiwf_core.storage import InjectedTransactionFailure, json_bytes, sha256_bytes
from aiwf_core.workflow import WorkflowEngine
from integration.test_analysis_cli import initialize_workspace as initialize_base_workspace
from integration.test_design_specification_cli import (
    approve_analysis,
    approve_design,
    approve_task_plan,
    run_success,
    write_outputs,
)


def initialize_workspace(root: Path) -> Path:
    repository = root / "repository"
    repository.mkdir()
    return initialize_base_workspace(root, repository=repository)


def submit_analysis_for_review(workspace: Path) -> dict[str, object]:
    work = run_success(["prepare", "--workspace", str(workspace)])
    write_outputs(
        workspace,
        work,
        markdown="# Analysis\n\nUsers save drafts.\n",
        result={
            "schema_version": 9,
            "stage": "analysis",
            "target_platform": "web",
            "requirements": [
                {
                    "title": "Save drafts",
                    "summary": "Users save drafts.",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "platform_scope": "target",
                    "change_type": "new",
                    "scope_reason": "Implemented by the web client.",
                    "disposition": "proposed",
                }
            ],
            "memory_delta": [],
        },
    )
    run_success(["submit", "--workspace", str(workspace), "--work-id", work["work_id"]])
    return work


def submit_analysis_revision(workspace: Path, *, revision: int = 1) -> dict[str, object]:
    run_success(
        [
            "revise",
            "--workspace",
            str(workspace),
            "--artifact-id",
            "analysis",
            "--revision",
            str(revision),
            "--feedback",
            "Clarify the approved requirement.",
        ]
    )
    work = run_success(["prepare", "--workspace", str(workspace)])
    result = json.loads((workspace / str(work["result_output"])).read_text(encoding="utf-8"))
    result["requirements"][0]["summary"] = "Users save drafts with clarified behavior."
    write_outputs(
        workspace,
        work,
        markdown="# Analysis\n\nUsers save drafts with clarified behavior.\n",
        result=result,
    )
    return run_success(
        ["submit", "--workspace", str(workspace), "--work-id", str(work["work_id"])]
    )


class RecoveryRevisionCommandLineTests(unittest.TestCase):
    def test_analysis_revision_can_be_approved_after_design_becomes_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            approve_analysis(workspace)
            approve_design(workspace, ["REQ-001"])

            submitted = submit_analysis_revision(workspace)
            status = run_success(["status", "--workspace", str(workspace)])

            self.assertEqual(submitted["invalidated"], ["design"])
            self.assertTrue(status["can_advance"])
            self.assertNotIn(
                "design_requirement_mismatch",
                {issue["type"] for issue in status["issues"]},
            )
            run_success(
                [
                    "review",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "analysis",
                    "--revision",
                    "2",
                    "--outcome",
                    "approved",
                ]
            )
            after = run_success(["status", "--workspace", str(workspace)])
            self.assertEqual(after["state"]["current_stage"], "design")
            self.assertEqual(after["counts"]["accepted_requirements"], 1)

    def test_stale_task_plan_and_tasks_do_not_block_analysis_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            approve_analysis(workspace)
            approve_design(workspace, ["REQ-001"])
            approve_task_plan(
                workspace,
                [
                    {
                        "key": "drafts",
                        "title": "Persist drafts",
                        "requirements": ["REQ-001"],
                        "depends_on": [],
                    }
                ],
            )

            submit_analysis_revision(workspace)
            status = run_success(["status", "--workspace", str(workspace)])

            self.assertTrue(status["can_advance"])
            self.assertFalse(
                {"design_requirement_mismatch", "task_reference_mismatch", "uncovered_requirements"}
                & {issue["type"] for issue in status["issues"]}
            )
            run_success(
                [
                    "review",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "analysis",
                    "--revision",
                    "2",
                    "--outcome",
                    "approved",
                ]
            )

    def test_review_content_drift_can_be_discarded_without_leaving_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            submit_analysis_for_review(workspace)
            artifact_path = workspace / "artifacts/analysis.md"
            recorded = "# Analysis\n\nUsers save drafts.\n"
            artifact_path.write_text("# Edited during review\n", encoding="utf-8")

            status = run_success(["status", "--workspace", str(workspace)])
            issue = next(item for item in status["issues"] if item["type"] == "artifact_drift")
            self.assertEqual(issue["recovery_action"], "resolve_review_drift")
            self.assertEqual(issue["allowed_outcomes"], ["adopt", "discard"])

            discarded = run_success(
                [
                    "resolve-drift",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "analysis",
                    "--revision",
                    "1",
                    "--outcome",
                    "discard",
                ]
            )

            self.assertEqual(discarded["artifact_status"], "review")
            self.assertEqual(artifact_path.read_text(encoding="utf-8"), recorded)
            after = run_success(["status", "--workspace", str(workspace)])
            self.assertEqual(after["state"]["mode"], "review")
            self.assertTrue(after["can_advance"])

    def test_review_content_drift_can_seed_a_successor_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            submit_analysis_for_review(workspace)
            artifact_path = workspace / "artifacts/analysis.md"
            external = "# Analysis\n\nUsers save and restore drafts offline.\n"
            artifact_path.write_text(external, encoding="utf-8")

            adopted = run_success(
                [
                    "resolve-drift",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "analysis",
                    "--revision",
                    "1",
                    "--outcome",
                    "adopt",
                    "--feedback",
                    "Use the review edit as the next draft.",
                ]
            )

            resumed = run_success(["prepare", "--workspace", str(workspace)])
            self.assertEqual(resumed["work_id"], adopted["work_id"])
            self.assertEqual(
                (workspace / resumed["draft_output"]).read_text(encoding="utf-8"),
                external,
            )
            self.assertEqual(
                artifact_path.read_text(encoding="utf-8"),
                "# Analysis\n\nUsers save drafts.\n",
            )
            submitted = run_success(
                ["submit", "--workspace", str(workspace), "--work-id", resumed["work_id"]]
            )
            self.assertEqual(submitted["revision"], 2)

    def test_structured_drift_reports_manual_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            submit_analysis_for_review(workspace)
            artifacts = json.loads(
                (workspace / ".aiwf/artifacts.json").read_text(encoding="utf-8")
            )
            result_path = workspace / artifacts["items"][0]["result_path"]
            result_path.write_text("{}\n", encoding="utf-8")

            status = run_success(["status", "--workspace", str(workspace)])
            issue = next(item for item in status["issues"] if item["type"] == "artifact_drift")
            self.assertFalse(issue["recoverable"])
            self.assertEqual(issue["allowed_outcomes"], [])
            self.assertEqual(issue["recovery_action"], "manual_repair_required")

    def test_changed_requested_drift_discard_preserves_active_revision_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            approve_analysis(workspace)
            revision = run_success(
                [
                    "revise",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "analysis",
                    "--revision",
                    "1",
                    "--feedback",
                    "Clarify offline behavior.",
                ]
            )
            active = run_success(["prepare", "--workspace", str(workspace)])
            self.assertEqual(active["work_id"], revision["work_id"])
            artifact_path = workspace / "artifacts/analysis.md"
            recorded = artifact_path.read_text(encoding="utf-8")
            artifact_path.write_text("# External edit while revising\n", encoding="utf-8")

            discarded = run_success(
                [
                    "resolve-drift",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "analysis",
                    "--revision",
                    "1",
                    "--outcome",
                    "discard",
                ]
            )

            self.assertEqual(discarded["artifact_status"], "changes_requested")
            self.assertEqual(artifact_path.read_text(encoding="utf-8"), recorded)
            resumed = run_success(["prepare", "--workspace", str(workspace)])
            self.assertEqual(resumed["work_id"], active["work_id"])

    def test_recover_command_resolves_an_incomplete_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            engine = WorkflowEngine(workspace)
            original_state = engine.store.read_json("state.json")
            changed_state = {**original_state, "updated_at": now_iso()}
            with engine.store.lock(exclusive=True):
                engine.store.inject_failure_after(1)
                with self.assertRaises(InjectedTransactionFailure):
                    engine.store.commit_locked(
                        {".aiwf/state.json": json_bytes(changed_state)},
                        event_type="test_change",
                        event_data={},
                        command_key="test:recover-cli",
                        request_digest=sha256_bytes(b"recover-cli"),
                    )

            status = run_success(["status", "--workspace", str(workspace)])
            self.assertEqual(status["status"], "needs_recovery")
            recovered = run_success(["recover", "--workspace", str(workspace)])
            self.assertEqual(recovered["status"], "recovered")
            self.assertTrue(recovered["recovered"])
            self.assertEqual(
                run_success(["status", "--workspace", str(workspace)])["status"],
                "ok",
            )

    def test_revise_command_resumes_an_approved_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            approve_analysis(workspace)

            revision_event = run_success(
                [
                    "revise",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "analysis",
                    "--revision",
                    "1",
                    "--feedback",
                    "Clarify offline behavior.",
                ]
            )
            resumed = run_success(["prepare", "--workspace", str(workspace)])
            self.assertEqual(resumed["work_id"], revision_event["work_id"])
            self.assertEqual(resumed["stage"], "analysis")
            self.assertEqual(resumed["feedback"], "Clarify offline behavior.")
            editable_result = json.loads(
                (workspace / resumed["result_output"]).read_text(encoding="utf-8")
            )
            self.assertNotIn("artifact_id", editable_result)
            self.assertLessEqual(
                set(editable_result),
                set(resumed["result_schema"]["properties"]),
            )

    def test_external_artifact_content_can_be_adopted_as_a_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            approve_analysis(workspace)
            design = run_success(["prepare", "--workspace", str(workspace)])
            write_outputs(
                workspace,
                design,
                markdown="# Design\n\nPersist drafts.\n",
                result={
                    "schema_version": 9,
                    "stage": "design",
                    "requirements": ["REQ-001"],
                    "design_mode": "greenfield",
                    "greenfield_reason": "The configured repository is empty.",
                    "code_evidence": [],
                    "memory_delta": [],
                },
            )
            run_success(
                ["submit", "--workspace", str(workspace), "--work-id", design["work_id"]]
            )
            run_success(
                [
                    "review",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "design",
                    "--revision",
                    "1",
                    "--outcome",
                    "approved",
                ]
            )
            artifact_path = workspace / "artifacts/analysis.md"
            snapshot_path = workspace / ".aiwf/history/analysis/1.md"
            approved_content = snapshot_path.read_text(encoding="utf-8")
            external_content = "# Analysis\n\nUsers also resume drafts offline.\n"
            artifact_path.write_text(external_content, encoding="utf-8")

            adopted = run_success(
                [
                    "resolve-drift",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "analysis",
                    "--revision",
                    "1",
                    "--outcome",
                    "adopt",
                    "--feedback",
                    "Adopt the user-edited analysis.",
                ]
            )

            self.assertEqual(artifact_path.read_text(encoding="utf-8"), approved_content)
            resumed = run_success(["prepare", "--workspace", str(workspace)])
            self.assertEqual(resumed["work_id"], adopted["work_id"])
            self.assertEqual(
                (workspace / resumed["draft_output"]).read_text(encoding="utf-8"),
                external_content,
            )
            submitted = run_success(
                ["submit", "--workspace", str(workspace), "--work-id", resumed["work_id"]]
            )
            self.assertEqual(submitted["revision"], 2)
            self.assertEqual(submitted["invalidated"], ["design"])
            self.assertEqual(snapshot_path.read_text(encoding="utf-8"), approved_content)
            self.assertEqual(
                (workspace / ".aiwf/history/analysis/2.md").read_text(encoding="utf-8"),
                external_content,
            )

    def test_external_artifact_content_can_be_discarded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            approve_analysis(workspace)
            artifact_path = workspace / "artifacts/analysis.md"
            snapshot_path = workspace / ".aiwf/history/analysis/1.md"
            approved_content = snapshot_path.read_text(encoding="utf-8")
            active = run_success(["prepare", "--workspace", str(workspace)])
            artifact_path.write_text("# Accidental edit\n", encoding="utf-8")

            conflict_arguments = [
                "resolve-drift",
                "--workspace",
                str(workspace),
                "--artifact-id",
                "analysis",
                "--revision",
                "1",
                "--outcome",
                "discard",
            ]
            rejected = run_cli(conflict_arguments)
            self.assertEqual(rejected.returncode, 6)
            self.assertEqual(json.loads(rejected.stderr)["error"]["code"], "active_work_conflict")
            discarded = run_success(
                [*conflict_arguments, "--supersede-active-work"]
            )

            self.assertEqual(discarded["outcome"], "discard")
            self.assertEqual(artifact_path.read_text(encoding="utf-8"), approved_content)
            self.assertEqual(run_success(["status", "--workspace", str(workspace)])["status"], "ok")
            self.assertTrue(
                (workspace / ".aiwf/history/abandoned" / active["work_id"] / "work.json").is_file()
            )

            repeated = run_success([*conflict_arguments, "--supersede-active-work"])
            self.assertEqual(repeated, discarded)

            artifact_path.write_text("# Another accidental edit\n", encoding="utf-8")
            discarded_again = run_success(conflict_arguments)
            self.assertEqual(discarded_again["outcome"], "discard")
            self.assertEqual(artifact_path.read_text(encoding="utf-8"), approved_content)

    def test_missing_artifact_content_can_only_be_discarded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            approve_analysis(workspace)
            artifact_path = workspace / "artifacts/analysis.md"
            approved_content = (
                workspace / ".aiwf/history/analysis/1.md"
            ).read_text(encoding="utf-8")
            artifact_path.unlink()

            status = run_success(["status", "--workspace", str(workspace)])
            issue = next(item for item in status["issues"] if item["type"] == "artifact_drift")
            self.assertEqual(issue["allowed_outcomes"], ["discard"])

            rejected = run_cli(
                [
                    "resolve-drift",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "analysis",
                    "--revision",
                    "1",
                    "--outcome",
                    "adopt",
                    "--feedback",
                    "Adopt deletion.",
                ]
            )
            self.assertEqual(rejected.returncode, 7)
            self.assertEqual(
                json.loads(rejected.stderr)["error"]["code"],
                "artifact_drift_unrecoverable",
            )

            discarded = run_success(
                [
                    "resolve-drift",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "analysis",
                    "--revision",
                    "1",
                    "--outcome",
                    "discard",
                ]
            )
            self.assertIsNone(discarded["external_content_sha256"])
            self.assertEqual(artifact_path.read_text(encoding="utf-8"), approved_content)

    def test_structured_artifact_drift_cannot_be_adopted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            approve_analysis(workspace)
            artifacts = json.loads(
                (workspace / ".aiwf/artifacts.json").read_text(encoding="utf-8")
            )
            result_path = workspace / artifacts["items"][0]["result_path"]
            result_path.write_text("{}\n", encoding="utf-8")

            rejected = run_cli(
                [
                    "resolve-drift",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "analysis",
                    "--revision",
                    "1",
                    "--outcome",
                    "adopt",
                    "--feedback",
                    "Adopt content.",
                ]
            )

            self.assertEqual(rejected.returncode, 7)
            self.assertEqual(
                json.loads(rejected.stderr)["error"]["code"],
                "artifact_drift_unrecoverable",
            )

    def test_approved_revision_does_not_replay_memory_additions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            work = run_success(["prepare", "--workspace", str(workspace)])
            (workspace / work["draft_output"]).write_text(
                "# Analysis\n\nUsers save drafts.\n",
                encoding="utf-8",
            )
            original_result = {
                "schema_version": 9,
                "stage": "analysis",
                "target_platform": "web",
                "requirements": [
                    {
                        "title": "Save drafts",
                        "summary": "Users save drafts.",
                        "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                        "platform_scope": "target",
                        "change_type": "new",
                        "scope_reason": "Implemented by the web client.",
                        "disposition": "proposed",
                    }
                ],
                "memory_delta": [
                    {
                        "operation": "add",
                        "type": "architecture_decision",
                        "content": "Users save drafts.",
                        "evidence": [],
                        "rationale": "Confirmed by analysis.",
                        "validation": None,
                    }
                ],
            }
            (workspace / work["result_output"]).write_text(
                json.dumps(original_result),
                encoding="utf-8",
            )
            run_success(
                ["submit", "--workspace", str(workspace), "--work-id", work["work_id"]]
            )
            run_success(
                [
                    "review",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "analysis",
                    "--revision",
                    "1",
                    "--outcome",
                    "approved",
                ]
            )

            revision_event = run_success(
                [
                    "revise",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "analysis",
                    "--revision",
                    "1",
                    "--feedback",
                    "Clarify wording without changing the confirmed fact.",
                ]
            )
            revised = run_success(["prepare", "--workspace", str(workspace)])
            self.assertEqual(revised["work_id"], revision_event["work_id"])
            seed = json.loads(
                (workspace / revised["result_output"]).read_text(encoding="utf-8")
            )
            self.assertEqual(seed["memory_delta"], [])
            self.assertNotIn("origin_revision", seed["requirements"][0])
            self.assertEqual(
                revised["facts"]["affected_memory"][0]["id"],
                "M-001",
            )
            unchanged = run_cli(
                [
                    "submit",
                    "--workspace",
                    str(workspace),
                    "--work-id",
                    revised["work_id"],
                ]
            )
            self.assertEqual(unchanged.returncode, 4)
            self.assertEqual(
                json.loads(unchanged.stderr)["error"]["code"],
                "revision_has_no_changes",
            )
            (workspace / revised["draft_output"]).write_text(
                "# Analysis\n\nUsers can save drafts.\n",
                encoding="utf-8",
            )

            run_success(
                [
                    "submit",
                    "--workspace",
                    str(workspace),
                    "--work-id",
                    revised["work_id"],
                ]
            )
            run_success(
                [
                    "review",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "analysis",
                    "--revision",
                    "2",
                    "--outcome",
                    "approved",
                ]
            )
            memory = json.loads(
                (workspace / ".aiwf/memory.json").read_text(encoding="utf-8")
            )
            self.assertEqual([item["id"] for item in memory["items"]], ["M-001"])

    def test_revise_requires_confirmation_and_archives_superseded_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            approve_analysis(workspace)
            active = run_success(["prepare", "--workspace", str(workspace)])
            (workspace / active["draft_output"]).write_text(
                "# Unfinished design\n", encoding="utf-8"
            )

            arguments = [
                "revise",
                "--workspace",
                str(workspace),
                "--artifact-id",
                "analysis",
                "--revision",
                "1",
                "--feedback",
                "Change the approved requirement.",
            ]
            rejected = run_cli(arguments)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(json.loads(rejected.stderr)["error"]["code"], "active_work_conflict")

            revised = run_success([*arguments, "--supersede-active-work"])
            archive = workspace / ".aiwf/history/abandoned" / active["work_id"]
            self.assertEqual(
                (archive / "artifact.md").read_text(encoding="utf-8"),
                "# Unfinished design\n",
            )
            self.assertEqual(
                json.loads((archive / "work.json").read_text(encoding="utf-8"))["status"],
                "abandoned",
            )
            self.assertNotEqual(revised["work_id"], active["work_id"])

    def test_memory_projection_drift_blocks_prepare_and_is_rebuilt_by_recover(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            memory_path = workspace / ".aiwf/memory.md"
            memory_path.write_text("tampered\n", encoding="utf-8")

            status = run_success(["status", "--workspace", str(workspace)])
            self.assertEqual(status["status"], "issues_found")
            issue = next(
                item for item in status["issues"] if item["type"] == "generated_view_drift"
            )
            self.assertTrue(issue["blocking"])
            self.assertEqual(issue["recovery_action"], "recover")

            blocked = run_cli(["prepare", "--workspace", str(workspace)])
            self.assertEqual(blocked.returncode, 7)
            self.assertEqual(
                json.loads(blocked.stderr)["error"]["code"],
                "workspace_health_blocked",
            )

            run_success(["recover", "--workspace", str(workspace)])
            self.assertIn("Current Decisions", memory_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
