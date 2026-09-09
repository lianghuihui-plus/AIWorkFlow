from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import run_cli
from integration.test_analysis_cli import initialize_workspace, result_of


def run_success(arguments: list[str]) -> dict[str, object]:
    completed = run_cli(arguments)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return result_of(completed)


def write_outputs(workspace: Path, work: dict[str, object], *, markdown: str, result: dict[str, object]) -> None:
    (workspace / str(work["draft_output"])).write_text(markdown, encoding="utf-8")
    (workspace / str(work["result_output"])).write_text(json.dumps(result), encoding="utf-8")


def approve_analysis(workspace: Path) -> None:
    work = run_success(["prepare", "--workspace", str(workspace)])
    write_outputs(
        workspace,
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
    run_success(["submit", "--workspace", str(workspace), "--work-id", str(work["work_id"])])
    run_success(["review", "--workspace", str(workspace), "--artifact-id", "analysis", "--revision", "1", "--outcome", "approved"])


def approve_design(workspace: Path) -> None:
    work = run_success(["prepare", "--workspace", str(workspace)])
    write_outputs(
        workspace,
        work,
        markdown="# Design\n\nUse ApplicationRoot.\n",
        result={
            "schema_version": 11,
            "stage": "design",
            "requirements": ["REQ-001"],
            "design_mode": "anchored",
            "greenfield_reason": None,
            "code_evidence": [{"path": "app.txt", "symbol": "ApplicationRoot", "purpose": "Integration root"}],
        },
    )
    run_success(["submit", "--workspace", str(workspace), "--work-id", str(work["work_id"])])
    run_success(["review", "--workspace", str(workspace), "--artifact-id", "design", "--revision", "1", "--outcome", "approved"])


def approve_task_plan(workspace: Path, tasks: list[dict[str, object]]) -> None:
    work = run_success(["prepare", "--workspace", str(workspace)])
    write_outputs(
        workspace,
        work,
        markdown="# Task plan\n\nTasks.\n",
        result={"schema_version": 11, "stage": "specification", "tasks": tasks, "withdrawn_tasks": []},
    )
    run_success(["submit", "--workspace", str(workspace), "--work-id", str(work["work_id"])])
    run_success(["review", "--workspace", str(workspace), "--artifact-id", "task-plan", "--revision", "1", "--outcome", "approved"])


class DesignSpecificationCommandLineTests(unittest.TestCase):
    def test_design_submit_validates_manifest_without_rereading_repository_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            approve_analysis(workspace)
            work = run_success(["prepare", "--workspace", str(workspace)])
            write_outputs(
                workspace,
                work,
                markdown="# Design\n\nUse the repository evidence captured by the agent.\n",
                result={
                    "schema_version": 11,
                    "stage": "design",
                    "requirements": ["REQ-001"],
                    "design_mode": "anchored",
                    "greenfield_reason": None,
                    "code_evidence": [
                        {"path": "missing.py", "symbol": "MissingRoot", "purpose": "Captured evidence"}
                    ],
                },
            )

            submitted = run_success(
                ["submit", "--workspace", str(workspace), "--work-id", str(work["work_id"])]
            )
            self.assertEqual(submitted["artifact_id"], "design")

    def test_independent_task_drafts_can_coexist_and_be_selected_freely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            approve_analysis(workspace)
            approve_design(workspace)
            approve_task_plan(
                workspace,
                [
                    {"key": "a", "title": "Task A", "requirements": ["REQ-001"], "depends_on": []},
                    {"key": "b", "title": "Task B", "requirements": ["REQ-001"], "depends_on": []},
                ],
            )
            task_b = run_success(["prepare", "--workspace", str(workspace), "--task-id", "T-002"])
            task_a = run_success(["prepare", "--workspace", str(workspace), "--task-id", "T-001"])
            status = run_success(["status", "--workspace", str(workspace)])

            self.assertEqual(task_b["active_item"], "T-002")
            self.assertEqual(task_a["active_item"], "T-001")
            self.assertEqual(status["counts"]["active_works"], 2)
            self.assertEqual({item["active_item"] for item in status["active_works"]}, {"T-001", "T-002"})

    def test_dependency_does_not_block_specification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            approve_analysis(workspace)
            approve_design(workspace)
            approve_task_plan(
                workspace,
                [
                    {"key": "a", "title": "Task A", "requirements": ["REQ-001"], "depends_on": []},
                    {"key": "b", "title": "Task B", "requirements": ["REQ-001"], "depends_on": ["a"]},
                ],
            )
            task_b = run_success(["prepare", "--workspace", str(workspace), "--task-id", "T-002"])
            self.assertEqual(task_b["stage"], "specification")


if __name__ == "__main__":
    unittest.main()
