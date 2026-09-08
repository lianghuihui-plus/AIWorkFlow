from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import SOURCE_ROOT, run_cli

from integration.test_analysis_cli import initialize_workspace


def run_success(arguments: list[str]) -> dict[str, object]:
    completed = run_cli(arguments)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)["result"]


def write_outputs(
    workspace: Path,
    work: dict[str, object],
    *,
    markdown: str,
    result: dict[str, object],
) -> None:
    (workspace / str(work["draft_output"])).write_text(markdown, encoding="utf-8")
    (workspace / str(work["result_output"])).write_text(
        json.dumps(result, ensure_ascii=False),
        encoding="utf-8",
    )


def analysis_requirement(title: str, summary: str) -> dict[str, object]:
    return {
        "title": title,
        "summary": summary,
        "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
        "platform_scope": "target",
        "change_type": "new",
        "scope_reason": "Implemented by the web client.",
        "disposition": "proposed",
    }


def approve_analysis(workspace: Path) -> None:
    work = run_success(["prepare", "--workspace", str(workspace)])
    write_outputs(
        workspace,
        work,
        markdown="# Analysis\n\nUsers save drafts and resume editing.\n",
        result={
            "schema_version": 9,
            "stage": "analysis",
            "target_platform": "web",
            "requirements": [
                analysis_requirement(
                    "Resume a draft",
                    "A signed-in user saves and resumes a draft.",
                )
            ],
            "memory_delta": [],
        },
    )
    run_success(["submit", "--workspace", str(workspace), "--work-id", str(work["work_id"])])
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


def approve_design(
    workspace: Path,
    requirements: list[str],
    *,
    code_evidence: list[dict[str, str]] | None = None,
) -> None:
    work = run_success(["prepare", "--workspace", str(workspace)])
    write_outputs(
        workspace,
        work,
        markdown="# Technical Design\n\nUse the repository draft module.\n",
        result={
            "schema_version": 9,
            "stage": "design",
            "requirements": requirements,
            "design_mode": "anchored" if code_evidence else "greenfield",
            "greenfield_reason": None if code_evidence else "The configured repository is empty.",
            "code_evidence": list(code_evidence or []),
            "memory_delta": [],
        },
    )
    run_success(["submit", "--workspace", str(workspace), "--work-id", str(work["work_id"])])
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


def approve_task_plan(workspace: Path, tasks: list[dict[str, object]]) -> None:
    work = run_success(["prepare", "--workspace", str(workspace)])
    if work["stage"] != "specification" or work["active_item"] is not None:
        raise AssertionError(f"Expected task planning work, got {work}")
    write_outputs(
        workspace,
        work,
        markdown="# Task Plan\n\nExecutable production-code tasks.\n",
        result={
            "schema_version": 9,
            "stage": "specification",
            "tasks": tasks,
            "memory_delta": [],
        },
    )
    run_success(["submit", "--workspace", str(workspace), "--work-id", str(work["work_id"])])
    run_success(
        [
            "review",
            "--workspace",
            str(workspace),
            "--artifact-id",
            "task-plan",
            "--revision",
            "1",
            "--outcome",
            "approved",
        ]
    )


class DesignSpecificationCommandLineTests(unittest.TestCase):
    def test_design_work_uses_only_the_current_requirement_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            workspace = initialize_workspace(root, repository=repository)
            approve_analysis(workspace)
            requirements_path = workspace / ".aiwf/requirements.json"
            requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
            requirements["items"].append(
                {
                    **requirements["items"][0],
                    "id": "REQ-999",
                    "title": "Historical requirement",
                    "disposition": "withdrawn",
                }
            )
            requirements_path.write_text(json.dumps(requirements), encoding="utf-8")

            work = run_success(["prepare", "--workspace", str(workspace)])
            status = run_success(["status", "--workspace", str(workspace)])

            self.assertNotIn(".aiwf/requirements.json", work["inputs"])
            self.assertEqual([item["id"] for item in work["facts"]["requirements"]], ["REQ-001"])
            self.assertEqual(status["counts"]["accepted_requirements"], 1)
            self.assertEqual(status["counts"]["withdrawn_requirements"], 1)

    def test_design_submit_verifies_existing_file_and_symbol_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            workspace = initialize_workspace(root, repository=repository)
            approve_analysis(workspace)
            source = repository / "draft_store.txt"
            source.write_text("class DraftStore", encoding="utf-8")
            work = run_success(["prepare", "--workspace", str(workspace)])
            result = {
                "schema_version": 9,
                "stage": "design",
                "requirements": ["REQ-001"],
                "design_mode": "anchored",
                "greenfield_reason": None,
                "code_evidence": [
                    {
                        "path": "draft_store.txt",
                        "symbol": "MissingStore",
                        "purpose": "Draft persistence",
                    }
                ],
                "memory_delta": [],
            }
            write_outputs(workspace, work, markdown="# Design\n", result=result)

            failed = run_cli(
                ["submit", "--workspace", str(workspace), "--work-id", str(work["work_id"])]
            )
            self.assertEqual(json.loads(failed.stderr)["error"]["code"], "repository_symbol_missing")

            result["code_evidence"][0]["symbol"] = "DraftStore"
            write_outputs(workspace, work, markdown="# Design\n", result=result)
            submitted = run_cli(
                ["submit", "--workspace", str(workspace), "--work-id", str(work["work_id"])]
            )
            self.assertEqual(submitted.returncode, 0, submitted.stderr)

    def test_repository_backed_fact_routes_active_work_to_upstream_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            evidence_file = repository / "config.txt"
            evidence_file.write_text("actualEndpoint=/v2/config", encoding="utf-8")
            workspace = initialize_workspace(root, repository=repository)
            approve_analysis(workspace)
            approve_design(
                workspace,
                ["REQ-001"],
                code_evidence=[
                    {
                        "path": "config.txt",
                        "symbol": "actualEndpoint",
                        "purpose": "Configuration integration",
                    }
                ],
            )
            active = run_success(["prepare", "--workspace", str(workspace)])

            routed = run_success(
                [
                    "route-upstream",
                    "--workspace",
                    str(workspace),
                    "--work-id",
                    str(active["work_id"]),
                    "--artifact-id",
                    "analysis",
                    "--revision",
                    "1",
                    "--correction",
                    "The endpoint recorded upstream is incorrect.",
                    "--evidence-json",
                    json.dumps([{"path": "config.txt", "symbol": "/v2/config"}]),
                ]
            )
            revision = run_success(["prepare", "--workspace", str(workspace)])

            self.assertEqual(revision["work_id"], routed["successor_work_id"])
            self.assertEqual(revision["stage"], "analysis")
            self.assertIn("/v2/config", revision["feedback"])
            self.assertTrue(
                (workspace / ".aiwf/history/abandoned" / str(active["work_id"]) / "work.json").is_file()
            )
            events = [
                json.loads(line)
                for line in (workspace / ".aiwf/events.jsonl").read_text().splitlines()
            ]
            self.assertIn("upstream_correction_routed", {event["type"] for event in events})

    def test_status_reports_design_requirement_coverage_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            workspace = initialize_workspace(root, repository=repository)
            approve_analysis(workspace)
            approve_design(workspace, ["REQ-001"])

            requirements_path = workspace / ".aiwf/requirements.json"
            requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
            requirements["items"][0]["disposition"] = "deferred"
            requirements_path.write_text(json.dumps(requirements), encoding="utf-8")

            status = run_success(["status", "--workspace", str(workspace)])
            issue = next(
                item
                for item in status["issues"]
                if item["type"] == "design_requirement_mismatch"
            )
            self.assertEqual(issue["details"]["unknown"], ["REQ-001"])
            self.assertEqual(issue["details"]["missing"], [])

    def test_status_reports_task_plan_requirement_coverage_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            workspace = initialize_workspace(root, repository=repository)
            analysis = run_success(["prepare", "--workspace", str(workspace)])
            write_outputs(
                workspace,
                analysis,
                markdown="# Analysis\n\nSave and resume drafts.\n",
                result={
                    "schema_version": 9,
                    "stage": "analysis",
                    "target_platform": "web",
                    "requirements": [
                        analysis_requirement("Save drafts", "Users save drafts."),
                        analysis_requirement("Resume drafts", "Users resume drafts."),
                    ],
                    "memory_delta": [],
                },
            )
            run_success(["submit", "--workspace", str(workspace), "--work-id", str(analysis["work_id"])])
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
            approve_design(workspace, ["REQ-001", "REQ-002"])
            approve_task_plan(
                workspace,
                [
                    {
                        "key": "drafts",
                        "title": "Persist and restore drafts",
                        "requirements": ["REQ-001", "REQ-002"],
                        "depends_on": [],
                    }
                ],
            )

            tasks_path = workspace / ".aiwf/tasks.json"
            tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
            tasks["items"][0]["requirements"] = ["REQ-001"]
            tasks_path.write_text(json.dumps(tasks), encoding="utf-8")

            status = run_success(["status", "--workspace", str(workspace)])
            self.assertEqual(status["status"], "issues_found")
            coverage_issue = next(
                item for item in status["issues"] if item["type"] == "uncovered_requirements"
            )
            self.assertEqual(coverage_issue["details"]["ids"], ["REQ-002"])

    def test_design_task_plan_and_multiple_specs_advance_to_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            workspace = initialize_workspace(root, repository=repository)
            approve_analysis(workspace)

            design = run_success(["prepare", "--workspace", str(workspace)])
            self.assertEqual(design["stage"], "design")
            self.assertEqual(design["active_item"], None)
            self.assertEqual(design["depends_on"], ["analysis@1"])
            self.assertIn("artifacts/analysis.md", design["inputs"])
            self.assertIn("repository_context", design)
            write_outputs(
                workspace,
                design,
                markdown="# Design\n\nDraftStore owns persistence; DraftEditor restores state.\n",
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
            run_success(["submit", "--workspace", str(workspace), "--work-id", str(design["work_id"])])
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

            task_plan = run_success(["prepare", "--workspace", str(workspace)])
            self.assertEqual(task_plan["stage"], "specification")
            self.assertIsNone(task_plan["active_item"])
            self.assertEqual(task_plan["artifact"]["id"], "task-plan")
            self.assertEqual(task_plan["facts"]["work_kind"], "task_planning")
            self.assertEqual(task_plan["depends_on"], ["design@1"])
            self.assertIn("任务规划", task_plan["stage_guide"]["instructions"])
            write_outputs(
                workspace,
                task_plan,
                markdown="# Task Plan\n\nPersist first, then expose resume behavior.\n",
                result={
                    "schema_version": 9,
                    "stage": "specification",
                    "tasks": [
                        {
                            "key": "storage",
                            "title": "Persist drafts",
                            "requirements": ["REQ-001"],
                            "depends_on": [],
                        },
                        {
                            "key": "resume",
                            "title": "Resume draft editing",
                            "requirements": ["REQ-001"],
                            "depends_on": ["storage"],
                        },
                    ],
                    "memory_delta": [],
                },
            )
            run_success(["submit", "--workspace", str(workspace), "--work-id", str(task_plan["work_id"])])
            proposed = json.loads((workspace / ".aiwf/tasks.json").read_text(encoding="utf-8"))["items"]
            self.assertEqual([item["status"] for item in proposed], ["proposed", "proposed"])
            review = run_success(
                [
                    "review",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "task-plan",
                    "--revision",
                    "1",
                    "--outcome",
                    "approved",
                ]
            )
            self.assertEqual(review["current_stage"], "specification")
            tasks = json.loads((workspace / ".aiwf/tasks.json").read_text(encoding="utf-8"))["items"]
            self.assertEqual([item["id"] for item in tasks], ["T-001", "T-002"])
            self.assertEqual([item["status"] for item in tasks], ["planned", "planned"])
            self.assertEqual(tasks[1]["depends_on"], ["T-001"])

            for task_id in ("T-001", "T-002"):
                specification = run_success(["prepare", "--workspace", str(workspace)])
                self.assertEqual(specification["active_item"], task_id)
                self.assertEqual(specification["depends_on"], ["task-plan@1"])
                self.assertEqual(specification["facts"]["work_kind"], "task_specification")
                write_outputs(
                    workspace,
                    specification,
                    markdown=f"# {task_id} Specification\n\nProduction-code implementation guidance.\n",
                    result={
                        "schema_version": 9,
                        "stage": "specification",
                        "task_id": task_id,
                        "memory_delta": [],
                    },
                )
                run_success(["submit", "--workspace", str(workspace), "--work-id", str(specification["work_id"])])
                stage_review = run_success(
                    [
                        "review",
                        "--workspace",
                        str(workspace),
                        "--artifact-id",
                        f"{task_id}-spec",
                        "--revision",
                        "1",
                        "--outcome",
                        "approved",
                    ]
                )

            self.assertEqual(stage_review["current_stage"], "implementation")
            status = run_success(["status", "--workspace", str(workspace)])
            self.assertEqual(status["next_action"], "implement_code")

            first_implementation = run_success(["prepare", "--workspace", str(workspace)])
            self.assertEqual(first_implementation["active_item"], "T-001")
            write_outputs(
                workspace,
                first_implementation,
                markdown="# T-001 Implementation\n\nPersisted drafts.\n",
                result={
                    "schema_version": 9,
                    "stage": "implementation",
                    "task_id": "T-001",
                    "changed_files": [],
                    "validation_summary": "Reviewed production implementation.",
                    "memory_delta": [],
                },
            )
            run_success(["submit", "--workspace", str(workspace), "--work-id", str(first_implementation["work_id"])])
            run_success(
                [
                    "review",
                    "--workspace",
                    str(workspace),
                    "--artifact-id",
                    "T-001-implementation",
                    "--revision",
                    "1",
                    "--outcome",
                    "approved",
                ]
            )
            dependent = run_success(["prepare", "--workspace", str(workspace)])
            self.assertEqual(dependent["active_item"], "T-002")
            self.assertIn("T-001-implementation@1", dependent["depends_on"])

    def test_design_stage_rejects_an_explicit_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            workspace = initialize_workspace(root, repository=repository)
            approve_analysis(workspace)

            completed = run_cli(["prepare", "--workspace", str(workspace), "--task-id", "T-999"])

            self.assertEqual(completed.returncode, 4)
            self.assertEqual(json.loads(completed.stderr)["error"]["code"], "invalid_active_item")


if __name__ == "__main__":
    unittest.main()
