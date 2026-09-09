from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import (
    advance_to_tasks,
    approve_task_implementation,
    approve_task_spec,
    bootstrap_engine,
    submit_and_approve,
    write_work_outputs,
)
from aiwf_core.model import AIWorkflowError


class WorkflowCoreTests(unittest.TestCase):
    def test_corrupt_work_is_visible_and_blocks_only_its_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(
                engine,
                [
                    {"key": "a", "title": "Task A", "requirements": ["REQ-001"], "depends_on": []},
                    {"key": "b", "title": "Task B", "requirements": ["REQ-001"], "depends_on": []},
                ],
            )
            corrupt = engine.prepare_work(active_item="T-001")
            engine.store.safe_path(
                f".aiwf/work/{corrupt['work_id']}/work.json"
            ).write_text("{broken", encoding="utf-8")

            status = engine.inspect()
            other = engine.prepare_work(active_item="T-002")
            with self.assertRaises(AIWorkflowError) as raised:
                engine.prepare_work(active_item="T-001")

            self.assertEqual(raised.exception.code, "corrupt_work")
            self.assertEqual(other["active_item"], "T-002")
            self.assertEqual(
                [item["type"] for item in status["issues"] if item["level"] == "error"],
                ["corrupt_work"],
            )
            self.assertFalse(status["issues"][0]["blocking"])

    def test_missing_work_metadata_is_isolated_to_its_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(
                engine,
                [
                    {"key": "a", "title": "Task A", "requirements": ["REQ-001"], "depends_on": []},
                    {"key": "b", "title": "Task B", "requirements": ["REQ-001"], "depends_on": []},
                ],
            )
            corrupt = engine.prepare_work(active_item="T-001")
            engine.store.safe_path(
                f".aiwf/work/{corrupt['work_id']}/work.json"
            ).unlink()

            status = engine.inspect()
            other = engine.prepare_work(active_item="T-002")
            with self.assertRaises(AIWorkflowError) as raised:
                engine.prepare_work(active_item="T-001")

            self.assertEqual(raised.exception.code, "corrupt_work")
            self.assertEqual(other["active_item"], "T-002")
            self.assertEqual(status["issues"][0]["details"]["artifact_id"], "T-001-spec")
            self.assertFalse(status["issues"][0]["blocking"])

    def test_same_artifact_resumes_its_draft_without_owning_other_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(
                engine,
                [
                    {"key": "a", "title": "A", "requirements": ["REQ-001"], "depends_on": []},
                    {"key": "b", "title": "B", "requirements": ["REQ-001"], "depends_on": []},
                ],
            )
            first = engine.prepare_work(active_item="T-001")
            second = engine.prepare_work(active_item="T-001")
            other = engine.prepare_work(active_item="T-002")
            self.assertEqual(first["work_id"], second["work_id"])
            self.assertNotEqual(first["work_id"], other["work_id"])

    def test_zero_code_change_implementation_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(engine, [{"key": "a", "title": "A", "requirements": ["REQ-001"], "depends_on": []}])
            approve_task_spec(engine, "T-001")
            approve_task_implementation(engine, "T-001", changed_files=[])
            self.assertTrue(engine.inspect()["implementation_complete"])

    def test_pending_revision_does_not_replace_approved_content_or_block_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(engine, [{"key": "a", "title": "A", "requirements": ["REQ-001"], "depends_on": []}])
            approve_task_spec(engine, "T-001")
            approved_content = (workspace / "artifacts/analysis.md").read_text(encoding="utf-8")
            revision = engine.request_revision("analysis", 1, feedback="Clarify wording.")
            write_work_outputs(
                engine,
                revision,
                markdown="# Analysis\n\nCandidate wording.\n",
                result={
                    "schema_version": 11,
                    "stage": "analysis",
                    "target_platform": "test",
                    "requirements": [
                        {
                            "id": "REQ-001",
                            "change_kind": "presentation",
                            "title": "Save drafts clearly",
                            "summary": "Persist and restore drafts.",
                            "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                            "platform_scope": "target",
                            "change_type": "new",
                            "scope_reason": "Wording only.",
                            "disposition": "proposed",
                        }
                    ],
                    "withdrawn_requirements": [],
                },
            )
            engine.submit_work(str(revision["work_id"]))
            task = engine.prepare_work(active_item="T-001")
            self.assertEqual(task["stage"], "implementation")
            self.assertEqual((workspace / "artifacts/analysis.md").read_text(encoding="utf-8"), approved_content)

    def test_implementation_uses_approved_specification_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(engine, [{"key": "a", "title": "A", "requirements": ["REQ-001"], "depends_on": []}])
            approve_task_spec(engine, "T-001")

            revision = engine.request_revision("T-001-spec", 1, feedback="Propose a new criterion.")
            write_work_outputs(
                engine,
                revision,
                markdown="# T-001 specification\n\nCandidate criterion.\n",
                result={
                    "schema_version": 11,
                    "stage": "specification",
                    "task_id": "T-001",
                    "acceptance_criteria": ["Candidate behavior is available"],
                },
            )
            submitted = engine.submit_work(str(revision["work_id"]))

            implementation = engine.prepare_work(active_item="T-001")
            self.assertEqual(
                implementation["inputs"][0],
                ".aiwf/history/T-001-spec/1.md",
            )
            self.assertEqual(
                implementation["facts"]["acceptance_criteria"],
                ["Behavior is available"],
            )
            write_work_outputs(
                engine,
                implementation,
                markdown="# Implementation\n\nApproved criterion verified.\n",
                result={
                    "schema_version": 11,
                    "stage": "implementation",
                    "task_id": "T-001",
                    "summary": "Approved behavior is available.",
                    "changed_files": [],
                    "acceptance_results": [
                        {
                            "criterion": "Behavior is available",
                            "status": "passed",
                            "evidence": "Approved criterion was checked.",
                        }
                    ],
                    "validation": ["Approved behavior verified."],
                    "risks": [],
                },
            )
            accepted = engine.submit_work(str(implementation["work_id"]))
            self.assertEqual(accepted["artifact_id"], "T-001-implementation")
            self.assertEqual(submitted["revision"], 2)

    def test_review_changes_preserve_newer_manual_markdown_as_revision_draft(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(engine, [{"key": "a", "title": "A", "requirements": ["REQ-001"], "depends_on": []}])
            revision = engine.request_revision("design", 1, feedback="Clarify the design.")
            write_work_outputs(
                engine,
                revision,
                markdown="# Design\n\nSubmitted candidate.\n",
                result={
                    "schema_version": 11,
                    "stage": "design",
                    "requirements": ["REQ-001"],
                    "design_mode": "anchored",
                    "greenfield_reason": None,
                    "code_evidence": [
                        {"path": "app.txt", "symbol": "ApplicationRoot", "purpose": "Integration root"}
                    ],
                },
            )
            submitted = engine.submit_work(str(revision["work_id"]))
            manual_content = "# Design\n\nManual changes made during review.\n"
            (workspace / "artifacts/design.md").write_text(manual_content, encoding="utf-8")

            changes = engine.review_artifact(
                "design", int(submitted["revision"]), outcome="changes_requested", feedback="Revise it."
            )

            self.assertEqual(
                (workspace / str(changes["draft_output"])).read_text(encoding="utf-8"),
                manual_content,
            )

    def test_review_changes_rejects_an_older_approved_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(
                engine,
                [{"key": "a", "title": "A", "requirements": ["REQ-001"], "depends_on": []}],
            )
            revision = engine.request_revision("design", 1, feedback="Clarify the design.")
            write_work_outputs(
                engine,
                revision,
                markdown="# Design\n\nSubmitted candidate.\n",
                result={
                    "schema_version": 11,
                    "stage": "design",
                    "requirements": ["REQ-001"],
                    "design_mode": "anchored",
                    "greenfield_reason": None,
                    "code_evidence": [
                        {"path": "app.txt", "symbol": "ApplicationRoot", "purpose": "Integration root"}
                    ],
                },
            )
            engine.submit_work(str(revision["work_id"]))

            with self.assertRaises(AIWorkflowError) as raised:
                engine.review_artifact(
                    "design",
                    1,
                    outcome="changes_requested",
                    feedback="Reject the submitted candidate.",
                )

            self.assertEqual(raised.exception.code, "invalid_revision_target")
            design = next(
                item
                for item in engine.store.read_json("artifacts.json")["items"]
                if item["id"] == "design"
            )
            self.assertEqual(design["revision"], 2)
            self.assertEqual(design["status"], "review")

    def test_revise_rejects_a_revision_waiting_for_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(
                engine,
                [{"key": "a", "title": "A", "requirements": ["REQ-001"], "depends_on": []}],
            )
            revision = engine.request_revision("design", 1, feedback="Clarify the design.")
            write_work_outputs(
                engine,
                revision,
                markdown="# Design\n\nSubmitted candidate.\n",
                result={
                    "schema_version": 11,
                    "stage": "design",
                    "requirements": ["REQ-001"],
                    "design_mode": "anchored",
                    "greenfield_reason": None,
                    "code_evidence": [
                        {"path": "app.txt", "symbol": "ApplicationRoot", "purpose": "Integration root"}
                    ],
                },
            )
            submitted = engine.submit_work(str(revision["work_id"]))

            with self.assertRaises(AIWorkflowError) as raised:
                engine.request_revision(
                    "design",
                    int(submitted["revision"]),
                    feedback="Start another revision before review.",
                )

            self.assertEqual(raised.exception.code, "invalid_state_transition")

    def test_approval_does_not_overwrite_newer_manual_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(engine, [{"key": "a", "title": "A", "requirements": ["REQ-001"], "depends_on": []}])
            revision = engine.request_revision("design", 1, feedback="Clarify the design.")
            candidate_content = "# Design\n\nSubmitted candidate.\n"
            write_work_outputs(
                engine,
                revision,
                markdown=candidate_content,
                result={
                    "schema_version": 11,
                    "stage": "design",
                    "requirements": ["REQ-001"],
                    "design_mode": "anchored",
                    "greenfield_reason": None,
                    "code_evidence": [
                        {"path": "app.txt", "symbol": "ApplicationRoot", "purpose": "Integration root"}
                    ],
                },
            )
            submitted = engine.submit_work(str(revision["work_id"]))
            manual_content = "# Design\n\nManual changes made after submission.\n"
            (workspace / "artifacts/design.md").write_text(manual_content, encoding="utf-8")

            engine.review_artifact("design", int(submitted["revision"]), outcome="approved")

            self.assertEqual(
                (workspace / "artifacts/design.md").read_text(encoding="utf-8"),
                manual_content,
            )
            self.assertEqual(
                (workspace / ".aiwf/history/design/2.md").read_text(encoding="utf-8"),
                candidate_content,
            )

    def test_submitted_work_remains_terminal_when_cleanup_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            work = engine.prepare_work()
            write_work_outputs(
                engine,
                work,
                markdown="# Analysis\n\nSave drafts.\n",
                result={
                    "schema_version": 11,
                    "stage": "analysis",
                    "target_platform": "test",
                    "requirements": [
                        {
                            "title": "Save drafts",
                            "summary": "Persist drafts.",
                            "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                            "platform_scope": "target",
                            "change_type": "new",
                            "scope_reason": "Target behavior.",
                            "disposition": "proposed",
                        }
                    ],
                    "withdrawn_requirements": [],
                },
            )
            submitted = engine.submit_work(str(work["work_id"]))
            archived_work_path = workspace / ".aiwf/history/analysis/1.work.json"
            archived_work = json.loads(archived_work_path.read_text(encoding="utf-8"))
            self.assertEqual(archived_work["status"], "submitted")

            residual = workspace / ".aiwf/work" / str(work["work_id"])
            residual.mkdir(parents=True)
            (residual / "work.json").write_text(
                json.dumps(archived_work), encoding="utf-8"
            )
            self.assertEqual(engine.inspect()["counts"]["active_works"], 0)
            self.assertEqual(submitted["revision"], 1)

    def test_test_failure_does_not_change_implementation_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(engine, [{"key": "a", "title": "A", "requirements": ["REQ-001"], "depends_on": []}])
            approve_task_spec(engine, "T-001")
            approve_task_implementation(engine, "T-001")
            work = engine.prepare_work(active_item="T-001")
            submit_and_approve(
                engine,
                work,
                markdown="# Tests\n\nOne test failed.\n",
                result={
                    "schema_version": 11,
                    "stage": "testing",
                    "task_id": "T-001",
                    "status": "failed",
                    "test_files": ["tests/test_drafts.py"],
                    "command": "pytest tests/test_drafts.py",
                    "summary": "One assertion failed.",
                    "uncovered": [],
                },
            )
            status = engine.inspect()
            self.assertTrue(status["implementation_complete"])
            self.assertEqual(status["task_progress"][0]["test_status"], "failed")

    def test_other_task_review_and_open_question_do_not_block_selected_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(
                engine,
                [
                    {"key": "a", "title": "A", "requirements": ["REQ-001"], "depends_on": []},
                    {"key": "b", "title": "B", "requirements": ["REQ-001"], "depends_on": []},
                    {"key": "c", "title": "C", "requirements": ["REQ-001"], "depends_on": []},
                ],
            )
            review_work = engine.prepare_work(active_item="T-001")
            write_work_outputs(
                engine,
                review_work,
                markdown="# T-001 specification\n\nAwaiting review.\n",
                result={
                    "schema_version": 11,
                    "stage": "specification",
                    "task_id": "T-001",
                    "acceptance_criteria": ["A is available"],
                },
            )
            engine.submit_work(str(review_work["work_id"]))
            question_work = engine.prepare_work(active_item="T-002")
            engine.open_questions(
                str(question_work["work_id"]),
                [
                    {
                        "question": "Which B behavior?",
                        "reason": "B needs a user decision.",
                        "recommendation": "Use the default behavior.",
                    }
                ],
            )

            other = engine.prepare_work(active_item="T-003")

            self.assertEqual(other["stage"], "specification")
            self.assertEqual(other["active_item"], "T-003")


if __name__ == "__main__":
    unittest.main()
