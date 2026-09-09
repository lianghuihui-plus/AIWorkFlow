from __future__ import annotations

import shutil
import subprocess
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


class ImplementationTestingTests(unittest.TestCase):
    def test_dependency_implementation_stays_available_after_failed_or_skipped_tests(self) -> None:
        for test_status in ("failed", "skipped"):
            with self.subTest(test_status=test_status), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory) / "workspace"
                workspace.mkdir()
                engine = bootstrap_engine(workspace)
                advance_to_tasks(
                    engine,
                    [
                        {"key": "a", "title": "Task A", "requirements": ["REQ-001"], "depends_on": []},
                        {"key": "b", "title": "Task B", "requirements": ["REQ-001"], "depends_on": ["a"]},
                    ],
                )
                approve_task_spec(engine, "T-001")
                approve_task_spec(engine, "T-002")
                approve_task_implementation(engine, "T-001")
                test_work = engine.prepare_work(active_item="T-001")
                submit_and_approve(
                    engine,
                    test_work,
                    markdown=f"# Tests\n\n{test_status}.\n",
                    result={
                        "schema_version": 11,
                        "stage": "testing",
                        "task_id": "T-001",
                        "status": test_status,
                        "test_files": [],
                        "command": None,
                        "summary": f"Tests {test_status}.",
                        "uncovered": ["Environment unavailable"] if test_status == "skipped" else [],
                    },
                )

                dependent = engine.prepare_work(active_item="T-002")

                self.assertEqual(dependent["stage"], "implementation")
    def test_implementation_must_cover_the_task_specification_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(engine, [{"key": "a", "title": "Task A", "requirements": ["REQ-001"], "depends_on": []}])
            approve_task_spec(engine, "T-001")
            work = engine.prepare_work(active_item="T-001")
            self.assertEqual(work["facts"]["acceptance_criteria"], ["Behavior is available"])
            write_work_outputs(
                engine,
                work,
                markdown="# Implementation\n\nReported a different criterion.\n",
                result={
                    "schema_version": 11,
                    "stage": "implementation",
                    "task_id": "T-001",
                    "summary": "Reported a different criterion.",
                    "changed_files": [],
                    "acceptance_results": [{"criterion": "Something else", "status": "passed", "evidence": "Reported."}],
                    "validation": ["Inspected current behavior."],
                    "risks": [],
                },
            )

            with self.assertRaises(AIWorkflowError) as raised:
                engine.submit_work(str(work["work_id"]))
            self.assertEqual(raised.exception.code, "acceptance_coverage_mismatch")

    def test_git_report_differences_and_repository_availability_do_not_gate_submission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            repository = Path(engine.store.read_json("project.json")["code_repository"])
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            advance_to_tasks(
                engine,
                [{"key": "a", "title": "Task A", "requirements": ["REQ-001"], "depends_on": []}],
            )
            approve_task_spec(engine, "T-001")
            implementation = engine.prepare_work(active_item="T-001")
            (repository / "app.txt").write_text("Changed outside workflow reporting\n", encoding="utf-8")
            write_work_outputs(
                engine,
                implementation,
                markdown="# Implementation\n\nExisting behavior satisfies the task.\n",
                result={
                    "schema_version": 11,
                    "stage": "implementation",
                    "task_id": "T-001",
                    "summary": "Existing behavior satisfies the task.",
                    "changed_files": [],
                    "acceptance_results": [{"criterion": "Behavior is available", "status": "passed", "evidence": "Inspected current behavior."}],
                    "validation": ["Manual check passed."],
                    "risks": [],
                },
            )
            shutil.rmtree(repository)
            submitted = engine.submit_work(str(implementation["work_id"]))
            self.assertEqual(submitted["artifact_id"], "T-001-implementation")

    def test_unmet_acceptance_cannot_complete_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(engine, [{"key": "a", "title": "Task A", "requirements": ["REQ-001"], "depends_on": []}])
            approve_task_spec(engine, "T-001")
            work = engine.prepare_work(active_item="T-001")
            write_work_outputs(
                engine,
                work,
                markdown="# Implementation\n\nIncomplete.\n",
                result={
                    "schema_version": 11,
                    "stage": "implementation",
                    "task_id": "T-001",
                    "summary": "The task is incomplete.",
                    "changed_files": [],
                    "acceptance_results": [{"criterion": "Behavior is available", "status": "failed", "evidence": "Missing."}],
                    "validation": ["Checked the expected behavior."],
                    "risks": ["Incomplete."],
                },
            )
            with self.assertRaises(AIWorkflowError) as raised:
                engine.submit_work(str(work["work_id"]))
            self.assertEqual(raised.exception.code, "acceptance_not_met")

    def test_unit_testing_is_optional_and_can_be_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(engine, [{"key": "a", "title": "Task A", "requirements": ["REQ-001"], "depends_on": []}])
            approve_task_spec(engine, "T-001")
            approve_task_implementation(engine, "T-001")

            before_test = engine.inspect()
            self.assertTrue(before_test["implementation_complete"])
            self.assertEqual(before_test["next_action"], "completed")
            self.assertEqual(before_test["task_progress"][0]["test_status"], "not_run")

            test_work = engine.prepare_work(active_item="T-001")
            submit_and_approve(
                engine,
                test_work,
                markdown="# Tests\n\nSkipped because the environment is unavailable.\n",
                result={
                    "schema_version": 11,
                    "stage": "testing",
                    "task_id": "T-001",
                    "status": "skipped",
                    "test_files": [],
                    "command": None,
                    "summary": "Environment unavailable.",
                    "uncovered": ["Persistence integration."],
                },
            )
            after_test = engine.inspect()
            self.assertTrue(after_test["implementation_complete"])
            self.assertEqual(after_test["task_progress"][0]["test_status"], "skipped")


if __name__ == "__main__":
    unittest.main()
