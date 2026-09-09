from __future__ import annotations

import unittest

import support  # noqa: F401

from aiwf_core.model import AIWorkflowError
from aiwf_core.task_flow import derive_task_flow, select_work


def artifact(artifact_id: str, stage: str, task_id: str | None = None, *, reconcile: bool = False, test_status: str | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "id": artifact_id,
        "stage": stage,
        "active_item": task_id,
        "status": "approved",
        "approved_revision": 1,
        "needs_reconcile": ["changed"] if reconcile else [],
    }
    if test_status:
        value["test_status"] = test_status
    return value


def projection(tasks: list[dict[str, object]], task_artifacts: list[dict[str, object]]) -> dict[str, object]:
    base = [
        artifact("analysis", "analysis"),
        artifact("design", "design"),
        artifact("task-plan", "specification"),
    ]
    return derive_task_flow(
        requirements={"items": [{"id": "REQ-001", "disposition": "accepted"}]},
        tasks={"items": tasks},
        artifacts={"items": [*base, *task_artifacts]},
    )


class TaskFlowTests(unittest.TestCase):
    def test_empty_accepted_scope_keeps_existing_reconciliation_work_visible(self) -> None:
        tasks = [
            {"id": "T-001", "title": "A", "status": "active", "depends_on": []},
        ]
        artifacts = [
            artifact("analysis", "analysis"),
            artifact("design", "design", reconcile=True),
            artifact("task-plan", "specification"),
            artifact("T-001-spec", "specification", "T-001", reconcile=True),
            artifact("T-001-implementation", "implementation", "T-001", reconcile=True),
        ]

        result = derive_task_flow(
            requirements={"items": [{"id": "REQ-001", "disposition": "withdrawn"}]},
            tasks={"items": tasks},
            artifacts={"items": artifacts},
        )

        self.assertFalse(result["completed"])
        self.assertEqual(result["recommended_work"]["artifact_id"], "design")
        self.assertIn("T-001-spec", {item["artifact_id"] for item in result["ready_work"]})

    def test_empty_scope_with_only_historical_artifacts_is_completed(self) -> None:
        result = derive_task_flow(
            requirements={"items": [{"id": "REQ-001", "disposition": "withdrawn"}]},
            tasks={
                "items": [
                    {"id": "T-001", "title": "A", "status": "withdrawn", "depends_on": []}
                ]
            },
            artifacts={
                "items": [
                    artifact("analysis", "analysis"),
                    artifact("design", "design"),
                    artifact("task-plan", "specification"),
                    artifact("T-001-spec", "specification", "T-001"),
                    artifact("T-001-implementation", "implementation", "T-001"),
                ]
            },
        )

        self.assertTrue(result["completed"])
        self.assertEqual(result["ready_work"], [])

    def test_independent_tasks_can_be_selected_in_any_order(self) -> None:
        tasks = [
            {"id": "T-001", "title": "A", "status": "active", "depends_on": []},
            {"id": "T-002", "title": "B", "status": "active", "depends_on": []},
        ]
        result = projection(tasks, [])
        self.assertEqual(select_work(result, "T-002")["active_item"], "T-002")

    def test_dependency_blocks_only_implementation(self) -> None:
        tasks = [
            {"id": "T-001", "title": "A", "status": "active", "depends_on": []},
            {"id": "T-002", "title": "B", "status": "active", "depends_on": ["T-001"]},
        ]
        result = projection(tasks, [])
        self.assertEqual(select_work(result, "T-002")["stage"], "specification")
        result = projection(tasks, [artifact("T-002-spec", "specification", "T-002")])
        with self.assertRaises(AIWorkflowError) as raised:
            select_work(result, "T-002")
        self.assertEqual(raised.exception.code, "task_dependency_blocked")

    def test_blocked_implementation_is_neither_ready_nor_recommended(self) -> None:
        tasks = [
            {"id": "T-001", "title": "A", "status": "active", "depends_on": []},
            {"id": "T-002", "title": "B", "status": "active", "depends_on": ["T-001"]},
        ]
        artifacts = [
            artifact("T-001-spec", "specification", "T-001"),
            {**artifact("T-001-implementation", "implementation", "T-001"), "status": "review", "approved_revision": None},
            artifact("T-002-spec", "specification", "T-002"),
        ]

        result = projection(tasks, artifacts)

        self.assertEqual(result["ready_work"], [])
        self.assertIsNone(result["recommended_work"])
        with self.assertRaises(AIWorkflowError) as raised:
            select_work(result, "T-002")
        self.assertEqual(raised.exception.code, "task_dependency_blocked")

    def test_tests_are_optional_for_completion_and_dependencies(self) -> None:
        tasks = [
            {"id": "T-001", "title": "A", "status": "active", "depends_on": []},
            {"id": "T-002", "title": "B", "status": "active", "depends_on": ["T-001"]},
        ]
        artifacts = [
            artifact("T-001-spec", "specification", "T-001"),
            artifact("T-001-implementation", "implementation", "T-001"),
            artifact("T-002-spec", "specification", "T-002"),
        ]
        result = projection(tasks, artifacts)
        self.assertEqual(select_work(result, "T-002")["stage"], "implementation")
        self.assertEqual(result["task_progress"][0]["test_status"], "not_run")

    def test_reconcile_on_dependency_implementation_blocks_dependent_only(self) -> None:
        tasks = [
            {"id": "T-001", "title": "A", "status": "active", "depends_on": []},
            {"id": "T-002", "title": "B", "status": "active", "depends_on": ["T-001"]},
            {"id": "T-003", "title": "C", "status": "active", "depends_on": []},
        ]
        artifacts = [
            artifact("T-001-spec", "specification", "T-001"),
            artifact("T-001-implementation", "implementation", "T-001", reconcile=True),
            artifact("T-002-spec", "specification", "T-002"),
            artifact("T-003-spec", "specification", "T-003"),
        ]
        result = projection(tasks, artifacts)
        with self.assertRaises(AIWorkflowError):
            select_work(result, "T-002")
        self.assertEqual(select_work(result, "T-003")["stage"], "implementation")

    def test_task_plan_reconciliation_prevents_false_project_completion(self) -> None:
        tasks = [
            {"id": "T-001", "title": "A", "status": "active", "depends_on": []},
        ]
        artifacts = [
            artifact("T-001-spec", "specification", "T-001"),
            artifact("T-001-implementation", "implementation", "T-001"),
        ]
        base = [
            artifact("analysis", "analysis"),
            artifact("design", "design"),
            artifact("task-plan", "specification", reconcile=True),
            *artifacts,
        ]

        result = derive_task_flow(
            requirements={"items": [{"id": "REQ-001", "disposition": "accepted"}]},
            tasks={"items": tasks},
            artifacts={"items": base},
        )

        self.assertFalse(result["completed"])
        self.assertEqual(result["stage_progress"]["specification"], "needs_reconcile")

    def test_task_plan_reconciliation_is_recommended_without_blocking_tasks(self) -> None:
        tasks = [
            {"id": "T-001", "title": "A", "status": "active", "depends_on": []},
            {"id": "T-002", "title": "B", "status": "active", "depends_on": []},
        ]
        artifacts = [
            artifact("analysis", "analysis"),
            artifact("design", "design"),
            artifact("task-plan", "specification", reconcile=True),
            artifact("T-001-spec", "specification", "T-001"),
        ]

        result = derive_task_flow(
            requirements={"items": [{"id": "REQ-001", "disposition": "accepted"}]},
            tasks={"items": tasks},
            artifacts={"items": artifacts},
        )

        self.assertEqual(
            result["recommended_work"],
            {
                "stage": "specification",
                "active_item": None,
                "artifact_id": "task-plan",
                "kind": "revision",
            },
        )
        self.assertIn("T-001", {item["active_item"] for item in result["ready_work"]})
        self.assertIn("T-002", {item["active_item"] for item in result["ready_work"]})
        self.assertEqual(select_work(result, "T-001")["stage"], "implementation")
        self.assertEqual(select_work(result, "T-002")["stage"], "specification")

    def test_failed_or_skipped_tests_are_not_aggregated_as_completed(self) -> None:
        tasks = [
            {"id": "T-001", "title": "A", "status": "active", "depends_on": []},
        ]
        shared = [
            artifact("T-001-spec", "specification", "T-001"),
            artifact("T-001-implementation", "implementation", "T-001"),
        ]
        failed = projection(tasks, [*shared, artifact("T-001-test", "testing", "T-001", test_status="failed")])
        skipped = projection(tasks, [*shared, artifact("T-001-test", "testing", "T-001", test_status="skipped")])

        self.assertEqual(failed["stage_progress"]["testing"], "failed")
        self.assertEqual(skipped["stage_progress"]["testing"], "skipped")


if __name__ == "__main__":
    unittest.main()
