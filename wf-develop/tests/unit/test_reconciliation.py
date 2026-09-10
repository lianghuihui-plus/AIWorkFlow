from __future__ import annotations

import unittest

import support  # noqa: F401

from aiwf_core.reconciliation import (
    clear_reconciliation,
    mark_direct_reconciliation,
    requirement_change_sets,
)
from aiwf_core.workflow import WorkflowEngine


def artifact(artifact_id: str, task_id: str | None = None) -> dict[str, object]:
    return {
        "id": artifact_id,
        "active_item": task_id,
        "approved_revision": 1,
        "needs_reconcile": [],
    }


class ReconciliationTests(unittest.TestCase):
    def test_presentation_revision_does_not_count_as_behavior_change(self) -> None:
        before = [
            {
                "id": "REQ-001",
                "summary": "Persist drafts.",
                "disposition": "accepted",
                "semantic_sha256": "1" * 64,
            }
        ]
        after = [
            {
                "id": "REQ-001",
                "summary": "Persist user drafts.",
                "disposition": "accepted",
                "semantic_sha256": "1" * 64,
            }
        ]

        _, behavior_changes = requirement_change_sets(before, after)

        self.assertEqual(behavior_changes, set())

    def test_work_reconciliation_uses_only_direct_semantic_changes(self) -> None:
        engine = WorkflowEngine(".")
        requirement = {
            "id": "REQ-001",
            "title": "Drafts",
            "summary": "Persist drafts.",
            "platform_scope": "target",
            "change_type": "new",
            "disposition": "accepted",
        }
        task = {
            "id": "T-001",
            "title": "Task A",
            "requirements": ["REQ-001"],
            "depends_on": [],
            "status": "active",
        }

        title_only_design = engine._work_reconciliation_reasons(
            {
                "stage": "design",
                "active_item": None,
                "facts": {"requirements": [requirement]},
                "depends_on": ["analysis@1"],
            },
            requirements={"items": [{**requirement, "title": "Renamed drafts"}]},
            tasks={"items": [task]},
            artifacts={"items": [{"id": "analysis", "approved_revision": 2}]},
        )
        behavior_change_during_task_plan = engine._work_reconciliation_reasons(
            {
                "stage": "specification",
                "active_item": None,
                "facts": {"requirements": [requirement]},
                "depends_on": [],
            },
            requirements={"items": [{**requirement, "summary": "Persist for 30 days."}]},
            tasks={"items": [task]},
            artifacts={"items": []},
        )
        unrelated_task_plan_revision = engine._work_reconciliation_reasons(
            {
                "stage": "specification",
                "active_item": "T-001",
                "facts": {"requirements": [requirement], "task": task},
                "depends_on": ["task-plan@1"],
            },
            requirements={"items": [requirement]},
            tasks={"items": [task, {**task, "id": "T-002", "title": "Renamed B"}]},
            artifacts={"items": [{"id": "task-plan", "approved_revision": 2}]},
        )

        self.assertEqual(title_only_design, [])
        self.assertEqual(behavior_change_during_task_plan, [])
        self.assertEqual(unrelated_task_plan_revision, [])
    def test_new_requirement_marks_design_and_task_plan(self) -> None:
        artifacts = {
            "schema_version": 11,
            "items": [artifact("design"), artifact("task-plan")],
        }
        after = [
            {"id": "REQ-001", "summary": "new", "platform_scope": "target", "change_type": "new", "disposition": "accepted"}
        ]

        updated, affected = mark_direct_reconciliation(
            artifacts,
            stage="analysis",
            active_item=None,
            before_requirements=[],
            after_requirements=after,
        )

        by_id = {item["id"]: item for item in updated["items"]}
        self.assertEqual(affected, ["design", "task-plan"])
        self.assertEqual(by_id["design"]["needs_reconcile"], ["requirement:REQ-001"])

    def test_requirement_change_marks_only_direct_task_artifacts(self) -> None:
        artifacts = {
            "schema_version": 11,
            "items": [
                artifact("design"),
                artifact("task-plan"),
                artifact("T-001-spec", "T-001"),
                artifact("T-001-implementation", "T-001"),
                artifact("T-002-spec", "T-002"),
            ],
        }
        before = [
            {"id": "REQ-001", "summary": "old", "platform_scope": "target", "change_type": "new", "disposition": "accepted"},
            {"id": "REQ-002", "summary": "same", "platform_scope": "target", "change_type": "new", "disposition": "accepted"},
        ]
        after = [{**before[0], "summary": "new"}, before[1]]
        tasks = [
            {"id": "T-001", "requirements": ["REQ-001"]},
            {"id": "T-002", "requirements": ["REQ-002"]},
        ]

        updated, affected = mark_direct_reconciliation(
            artifacts,
            stage="analysis",
            active_item=None,
            before_requirements=before,
            after_requirements=after,
            before_tasks=tasks,
        )

        by_id = {item["id"]: item for item in updated["items"]}
        self.assertEqual(affected, ["T-001-implementation", "T-001-spec", "design"])
        self.assertEqual(by_id["task-plan"]["needs_reconcile"], [])
        self.assertEqual(by_id["T-002-spec"]["needs_reconcile"], [])
        self.assertEqual(by_id["T-001-spec"]["needs_reconcile"], ["requirement:REQ-001"])

    def test_existing_requirement_entering_scope_marks_design_and_task_plan(self) -> None:
        artifacts = {
            "schema_version": 11,
            "items": [
                artifact("design"),
                artifact("task-plan"),
                artifact("T-001-spec", "T-001"),
            ],
        }
        before = [
            {"id": "REQ-001", "summary": "same", "platform_scope": "target", "change_type": "new", "disposition": "deferred"}
        ]
        after = [{**before[0], "disposition": "accepted"}]

        updated, affected = mark_direct_reconciliation(
            artifacts,
            stage="analysis",
            active_item=None,
            before_requirements=before,
            after_requirements=after,
            before_tasks=[{"id": "T-001", "requirements": ["REQ-001"]}],
        )

        by_id = {item["id"]: item for item in updated["items"]}
        self.assertEqual(affected, ["design", "task-plan"])
        self.assertEqual(by_id["design"]["needs_reconcile"], ["requirement:REQ-001"])
        self.assertEqual(by_id["T-001-spec"]["needs_reconcile"], [])

    def test_new_requirement_outside_implementation_scope_changes_nothing(self) -> None:
        artifacts = {
            "schema_version": 11,
            "items": [artifact("design"), artifact("task-plan")],
        }
        after = [
            {"id": "REQ-001", "summary": "later", "platform_scope": "target", "change_type": "new", "disposition": "deferred"},
            {"id": "REQ-002", "summary": "other", "platform_scope": "other", "change_type": "new", "disposition": "excluded"},
        ]

        updated, affected = mark_direct_reconciliation(
            artifacts,
            stage="analysis",
            active_item=None,
            before_requirements=[],
            after_requirements=after,
        )

        self.assertEqual(affected, [])
        self.assertTrue(all(not item["needs_reconcile"] for item in updated["items"]))

    def test_requirement_leaving_scope_marks_project_artifacts_and_direct_task_only(self) -> None:
        artifacts = {
            "schema_version": 11,
            "items": [
                artifact("design"),
                artifact("task-plan"),
                artifact("T-001-spec", "T-001"),
                artifact("T-002-spec", "T-002"),
            ],
        }
        before = [
            {"id": "REQ-001", "summary": "same", "platform_scope": "target", "change_type": "new", "disposition": "accepted"}
        ]
        after = [{**before[0], "disposition": "deferred"}]

        updated, affected = mark_direct_reconciliation(
            artifacts,
            stage="analysis",
            active_item=None,
            before_requirements=before,
            after_requirements=after,
            before_tasks=[
                {"id": "T-001", "requirements": ["REQ-001"]},
                {"id": "T-002", "requirements": []},
            ],
        )

        by_id = {item["id"]: item for item in updated["items"]}
        self.assertEqual(affected, ["T-001-spec", "design", "task-plan"])
        self.assertEqual(by_id["task-plan"]["needs_reconcile"], ["requirement:REQ-001"])
        self.assertEqual(by_id["T-002-spec"]["needs_reconcile"], [])

    def test_implementation_revision_marks_only_own_test_and_direct_consumers(self) -> None:
        artifacts = {
            "schema_version": 11,
            "items": [
                artifact("T-001-test", "T-001"),
                artifact("T-002-implementation", "T-002"),
                artifact("T-002-test", "T-002"),
                artifact("T-003-implementation", "T-003"),
            ],
        }
        tasks = [
            {"id": "T-001", "status": "active", "depends_on": []},
            {"id": "T-002", "status": "active", "depends_on": ["T-001"]},
            {"id": "T-003", "status": "active", "depends_on": ["T-002"]},
        ]

        updated, affected = mark_direct_reconciliation(
            artifacts,
            stage="implementation",
            active_item="T-001",
            after_tasks=tasks,
        )

        by_id = {item["id"]: item for item in updated["items"]}
        self.assertEqual(
            affected,
            ["T-001-test", "T-002-implementation", "T-002-test"],
        )
        self.assertEqual(by_id["T-003-implementation"]["needs_reconcile"], [])

    def test_reconciliation_clear_preserves_approval(self) -> None:
        document = {"schema_version": 11, "items": [{**artifact("T-001-spec"), "needs_reconcile": ["requirement:REQ-001"]}]}
        cleared = clear_reconciliation(document, "T-001-spec")
        self.assertEqual(cleared["items"][0]["approved_revision"], 1)
        self.assertEqual(cleared["items"][0]["needs_reconcile"], [])


if __name__ == "__main__":
    unittest.main()
