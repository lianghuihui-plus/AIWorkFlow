from __future__ import annotations

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


def advance_two_requirements(engine: object) -> None:
    analysis = engine.prepare_work()
    submit_and_approve(
        engine,
        analysis,
        markdown="# Analysis\n\nDrafts and sync.\n",
        result={
            "schema_version": 11,
            "stage": "analysis",
            "target_platform": "test",
            "requirements": [
                {"title": "Drafts", "summary": "Persist drafts.", "sources": [{"kind": "prd", "ref": "prd/requirements.md"}], "platform_scope": "target", "change_type": "new", "scope_reason": "Target behavior.", "disposition": "proposed"},
                {"title": "Sync", "summary": "Synchronize drafts.", "sources": [{"kind": "prd", "ref": "prd/requirements.md"}], "platform_scope": "target", "change_type": "new", "scope_reason": "Target behavior.", "disposition": "proposed"},
            ],
            "withdrawn_requirements": [],
        },
    )
    design = engine.prepare_work()
    submit_and_approve(
        engine,
        design,
        markdown="# Design\n\nUse ApplicationRoot.\n",
        result={
            "schema_version": 11,
            "stage": "design",
            "requirements": ["REQ-001", "REQ-002"],
            "design_mode": "anchored",
            "greenfield_reason": None,
            "code_evidence": [{"path": "app.txt", "symbol": "ApplicationRoot", "purpose": "Integration root"}],
        },
    )
    plan = engine.prepare_work()
    submit_and_approve(
        engine,
        plan,
        markdown="# Tasks\n\nThree tasks.\n",
        result={
            "schema_version": 11,
            "stage": "specification",
            "tasks": [
                {"key": "a", "title": "Task A", "requirements": ["REQ-001"], "depends_on": []},
                {"key": "b", "title": "Task B", "requirements": ["REQ-002"], "depends_on": ["a"]},
                {"key": "c", "title": "Task C", "requirements": ["REQ-002"], "depends_on": []},
            ],
            "withdrawn_tasks": [],
        },
    )


class ReconciliationIntegrationTests(unittest.TestCase):
    def test_analysis_revision_accepts_structured_change_when_markdown_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            initial = engine.prepare_work()
            submit_and_approve(
                engine,
                initial,
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
                            "scope_reason": "Core behavior.",
                            "disposition": "proposed",
                        }
                    ],
                    "withdrawn_requirements": [],
                },
            )
            revision = engine.request_revision("analysis", 1, feedback="Change retention.")
            write_work_outputs(
                engine,
                revision,
                markdown="# Analysis\n\nSave drafts.\n",
                result={
                    "schema_version": 11,
                    "stage": "analysis",
                    "target_platform": "test",
                    "requirements": [
                        {
                            "id": "REQ-001",
                            "change_kind": "behavior",
                            "title": "Save drafts",
                            "summary": "Persist drafts for 30 days.",
                            "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                            "platform_scope": "target",
                            "change_type": "modify",
                            "scope_reason": "Retention changed.",
                            "disposition": "proposed",
                        }
                    ],
                    "withdrawn_requirements": [],
                },
            )

            submitted = engine.submit_work(str(revision["work_id"]))

            self.assertEqual(submitted["revision"], 2)

    def test_presentation_change_rejects_behavior_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(
                engine,
                [{"key": "a", "title": "Task A", "requirements": ["REQ-001"], "depends_on": []}],
            )
            revision = engine.request_revision("analysis", 1, feedback="Change retention.")
            write_work_outputs(
                engine,
                revision,
                markdown="# Analysis\n\nRetain drafts for 60 days.\n",
                result={
                    "schema_version": 11,
                    "stage": "analysis",
                    "target_platform": "test",
                    "requirements": [
                        {
                            "id": "REQ-001",
                            "change_kind": "presentation",
                            "title": "Save drafts",
                            "summary": "Persist and restore drafts for 60 days.",
                            "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                            "platform_scope": "target",
                            "change_type": "new",
                            "scope_reason": "Retention changed.",
                            "disposition": "proposed",
                        }
                    ],
                    "withdrawn_requirements": [],
                },
            )

            with self.assertRaises(AIWorkflowError) as raised:
                engine.submit_work(str(revision["work_id"]))

            self.assertEqual(raised.exception.code, "invalid_change_kind")

    def test_withdrawing_task_abandons_only_that_tasks_active_work(self) -> None:
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
            task_a = engine.prepare_work(active_item="T-001")
            task_b = engine.prepare_work(active_item="T-002")
            question = engine.open_questions(
                str(task_a["work_id"]),
                [
                    {
                        "question": "Keep task A?",
                        "reason": "The task may be removed.",
                        "recommendation": "Remove it.",
                    }
                ],
            )
            revision = engine.request_revision("task-plan", 1, feedback="Remove task A.")
            write_work_outputs(
                engine,
                revision,
                markdown="# Tasks\n\nOnly task B remains.\n",
                result={
                    "schema_version": 11,
                    "stage": "specification",
                    "tasks": [],
                    "withdrawn_tasks": ["T-001"],
                },
            )
            submitted = engine.submit_work(str(revision["work_id"]))
            engine.review_artifact("task-plan", int(submitted["revision"]), outcome="approved")

            abandoned = engine.store.read_json_path(
                f".aiwf/work/{task_a['work_id']}/work.json"
            )
            status = engine.inspect()

            self.assertEqual(abandoned["status"], "abandoned")
            self.assertNotIn(task_a["work_id"], {item["work_id"] for item in status["active_works"]})
            self.assertIn(task_b["work_id"], {item["work_id"] for item in status["active_works"]})
            cancelled = next(
                item
                for item in engine.store.read_json("questions.json")["items"]
                if item["id"] == question["question_ids"][0]
            )
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(status["counts"]["open_questions"], 0)

    def test_task_plan_revision_accepts_structured_change_when_markdown_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(
                engine,
                [{"key": "a", "title": "Task A", "requirements": ["REQ-001"], "depends_on": []}],
            )
            revision = engine.request_revision("task-plan", 1, feedback="Rename task A.")
            write_work_outputs(
                engine,
                revision,
                markdown="# Task plan\n\nIndependent task plan.\n",
                result={
                    "schema_version": 11,
                    "stage": "specification",
                    "tasks": [
                        {
                            "key": "a",
                            "id": "T-001",
                            "title": "Renamed task A",
                            "requirements": ["REQ-001"],
                            "depends_on": [],
                        }
                    ],
                    "withdrawn_tasks": [],
                },
            )

            submitted = engine.submit_work(str(revision["work_id"]))

            self.assertEqual(submitted["revision"], 2)

    def test_work_prepared_before_requirement_change_stays_pending_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(
                engine,
                [{"key": "a", "title": "Task A", "requirements": ["REQ-001"], "depends_on": []}],
            )
            stale_spec = engine.prepare_work(active_item="T-001")

            revision = engine.request_revision("analysis", 1, feedback="Change draft behavior.")
            write_work_outputs(
                engine,
                revision,
                markdown="# Analysis\n\nDrafts now expire.\n",
                result={
                    "schema_version": 11,
                    "stage": "analysis",
                    "target_platform": "test",
                    "requirements": [
                        {
                            "id": "REQ-001",
                            "change_kind": "behavior",
                            "title": "Save drafts",
                            "summary": "Persist drafts for 30 days.",
                            "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                            "platform_scope": "target",
                            "change_type": "modify",
                            "scope_reason": "Retention changed.",
                            "disposition": "proposed",
                        }
                    ],
                    "withdrawn_requirements": [],
                },
            )
            submitted_analysis = engine.submit_work(str(revision["work_id"]))
            engine.review_artifact(
                "analysis", int(submitted_analysis["revision"]), outcome="approved"
            )

            write_work_outputs(
                engine,
                stale_spec,
                markdown="# T-001 specification\n\nAcceptance: behavior is available.\n",
                result={
                    "schema_version": 11,
                    "stage": "specification",
                    "task_id": "T-001",
                    "acceptance_criteria": ["Behavior is available"],
                },
            )
            submitted_spec = engine.submit_work(str(stale_spec["work_id"]))
            engine.review_artifact(
                "T-001-spec", int(submitted_spec["revision"]), outcome="approved"
            )

            spec = next(
                item
                for item in engine.store.read_json("artifacts.json")["items"]
                if item["id"] == "T-001-spec"
            )
            self.assertEqual(spec["needs_reconcile"], ["requirement:REQ-001"])

    def test_newly_accepted_requirement_recommends_plan_without_blocking_tasks(self) -> None:
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

            revision = engine.request_revision("analysis", 1, feedback="Add export support.")
            write_work_outputs(
                engine,
                revision,
                markdown="# Analysis\n\nDrafts and export.\n",
                result={
                    "schema_version": 11,
                    "stage": "analysis",
                    "target_platform": "test",
                    "requirements": [
                        {
                            "id": "REQ-001",
                            "change_kind": "presentation",
                            "title": "Save drafts",
                            "summary": "Persist and restore drafts.",
                            "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                            "platform_scope": "target",
                            "change_type": "new",
                            "scope_reason": "Core target behavior.",
                            "disposition": "proposed",
                        },
                        {
                            "title": "Export drafts",
                            "summary": "Export a saved draft.",
                            "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                            "platform_scope": "target",
                            "change_type": "new",
                            "scope_reason": "Newly approved behavior.",
                            "disposition": "proposed",
                        },
                    ],
                    "withdrawn_requirements": [],
                },
            )
            submitted = engine.submit_work(str(revision["work_id"]))
            approved = engine.review_artifact(
                "analysis", int(submitted["revision"]), outcome="approved"
            )

            status = engine.inspect()
            self.assertEqual(approved["needs_reconcile"], ["design", "task-plan"])
            self.assertEqual(status["recommended_work"]["artifact_id"], "design")
            self.assertEqual(status["next_action"], "design_solution")
            unrelated = engine.prepare_work(active_item="T-002")
            self.assertEqual(unrelated["stage"], "specification")

    def test_implementation_revision_marks_only_direct_task_consumers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(
                engine,
                [
                    {"key": "a", "title": "A", "requirements": ["REQ-001"], "depends_on": []},
                    {"key": "b", "title": "B", "requirements": ["REQ-001"], "depends_on": ["a"]},
                    {"key": "c", "title": "C", "requirements": ["REQ-001"], "depends_on": []},
                ],
            )
            for task_id in ("T-001", "T-002", "T-003"):
                approve_task_spec(engine, task_id)
            for task_id in ("T-001", "T-002", "T-003"):
                approve_task_implementation(engine, task_id)

            revision = engine.request_revision(
                "T-001-implementation",
                1,
                feedback="Update A's implementation contract.",
            )
            write_work_outputs(
                engine,
                revision,
                markdown="# T-001 implementation\n\nUpdated contract verified.\n",
                result={
                    "schema_version": 11,
                    "stage": "implementation",
                    "task_id": "T-001",
                    "summary": "The updated task behavior is available.",
                    "changed_files": [],
                    "acceptance_results": [
                        {
                            "criterion": "Behavior is available",
                            "status": "passed",
                            "evidence": "The updated contract was inspected.",
                        }
                    ],
                    "validation": ["Targeted verification passed."],
                    "risks": [],
                },
            )
            submitted = engine.submit_work(str(revision["work_id"]))
            engine.review_artifact(
                "T-001-implementation",
                int(submitted["revision"]),
                outcome="approved",
            )

            by_id = {
                item["id"]: item
                for item in engine.store.read_json("artifacts.json")["items"]
            }
            self.assertTrue(by_id["T-002-implementation"]["needs_reconcile"])
            self.assertFalse(by_id["T-003-implementation"]["needs_reconcile"])

    def test_requirement_change_marks_only_related_task_and_real_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_two_requirements(engine)
            for task_id in ("T-001", "T-002", "T-003"):
                approve_task_spec(engine, task_id)
            approve_task_implementation(engine, "T-001")

            revision = engine.request_revision("analysis", 1, feedback="Draft retention changed.")
            write_work_outputs(
                engine,
                revision,
                markdown="# Analysis\n\nDraft retention is now 60 days.\n",
                result={
                    "schema_version": 11,
                    "stage": "analysis",
                    "target_platform": "test",
                    "requirements": [
                        {
                            "id": "REQ-001",
                            "change_kind": "behavior",
                            "title": "Drafts",
                            "summary": "Persist drafts for 60 days.",
                            "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                            "platform_scope": "target",
                            "change_type": "modify",
                            "scope_reason": "Confirmed retention change.",
                            "disposition": "proposed",
                        }
                    ],
                    "withdrawn_requirements": [],
                },
            )
            submitted = engine.submit_work(str(revision["work_id"]))
            approved = engine.review_artifact("analysis", int(submitted["revision"]), outcome="approved")
            self.assertIn("T-001-implementation", approved["needs_reconcile"])
            self.assertNotIn("T-002-spec", approved["needs_reconcile"])

            by_id = {item["id"]: item for item in engine.store.read_json("artifacts.json")["items"]}
            self.assertTrue(by_id["T-001-spec"]["needs_reconcile"])
            self.assertFalse(by_id["T-002-spec"]["needs_reconcile"])
            with self.assertRaises(AIWorkflowError) as blocked:
                engine.prepare_work(active_item="T-002")
            self.assertEqual(blocked.exception.code, "task_dependency_blocked")
            unrelated = engine.prepare_work(active_item="T-003")
            self.assertEqual(unrelated["stage"], "implementation")

            engine.reconcile_artifact("T-001-spec", 1, "Specification still covers the changed retention.")
            engine.reconcile_artifact("T-001-implementation", 1, "Current implementation already supports 60 days.")
            dependent = engine.prepare_work(active_item="T-002")
            self.assertEqual(dependent["stage"], "implementation")

    def test_manual_markdown_edit_is_used_as_revision_draft_without_global_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_two_requirements(engine)
            approve_task_spec(engine, "T-003")
            (workspace / "artifacts/analysis.md").write_text(
                "# Analysis\n\nHuman-edited wording.\n", encoding="utf-8"
            )

            task_work = engine.prepare_work(active_item="T-003")
            revision = engine.request_revision("analysis", 1, feedback="Adopt the wording.")

            self.assertEqual(task_work["stage"], "implementation")
            self.assertIn(
                "Human-edited wording",
                (workspace / str(revision["draft_output"])).read_text(encoding="utf-8"),
            )
            self.assertEqual(engine.inspect()["counts"]["active_works"], 2)


if __name__ == "__main__":
    unittest.main()
