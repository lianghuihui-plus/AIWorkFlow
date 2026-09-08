from __future__ import annotations

import unittest

import support  # noqa: F401

from aiwf_core.health import semantic_health_issues


def artifact(artifact_id: str, status: str) -> dict[str, object]:
    return {"id": artifact_id, "status": status}


def requirement(disposition: str = "accepted") -> dict[str, str]:
    return {"id": "REQ-001", "disposition": disposition}


def task(status: str = "planned") -> dict[str, object]:
    return {
        "id": "T-001",
        "status": status,
        "requirements": ["REQ-001"],
        "depends_on": [],
    }


class SemanticHealthTests(unittest.TestCase):
    def test_stale_downstream_is_excluded_from_current_semantic_projection(self) -> None:
        issues = semantic_health_issues(
            requirements={"items": [requirement("proposed")]},
            tasks={"items": [task("stale")]},
            artifacts={
                "items": [artifact("design", "stale"), artifact("task-plan", "stale")]
            },
            artifact_results={},
        )

        self.assertEqual(issues, [])

    def test_changes_requested_downstream_is_not_current_semantic_truth(self) -> None:
        issues = semantic_health_issues(
            requirements={"items": [requirement()]},
            tasks={"items": []},
            artifacts={"items": [artifact("design", "changes_requested")]},
            artifact_results={},
        )

        self.assertEqual(issues, [])

    def test_active_design_and_task_plan_still_enforce_current_coverage(self) -> None:
        issues = semantic_health_issues(
            requirements={"items": [requirement()]},
            tasks={"items": []},
            artifacts={
                "items": [artifact("design", "approved"), artifact("task-plan", "approved")]
            },
            artifact_results={"design": {"requirements": []}},
        )

        self.assertEqual(
            [issue["type"] for issue in issues],
            ["design_requirement_mismatch", "uncovered_requirements"],
        )

    def test_stale_task_still_belongs_to_an_active_task_plan(self) -> None:
        issues = semantic_health_issues(
            requirements={"items": [requirement()]},
            tasks={"items": [task("stale")]},
            artifacts={"items": [artifact("task-plan", "approved")]},
            artifact_results={},
        )

        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
