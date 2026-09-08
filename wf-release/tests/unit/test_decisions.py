from __future__ import annotations

import unittest

import support  # noqa: F401

from aiwf_core.decisions import append_decision, supersede_decisions_by_artifact
from aiwf_core.model import SCHEMA_VERSION, validate_document
from aiwf_core.memory_view import render_memory


def decision(decision_id: str, *, supersedes: list[str] | None = None) -> dict[str, object]:
    return {
        "id": decision_id,
        "question_id": f"Q-{decision_id.split('-')[1]}",
        "decision": f"Decision {decision_id}",
        "impact": ["analysis"],
        "status": "active",
        "supersedes": list(supersedes or []),
        "superseded_by": None,
        "created_at": "2026-08-27T10:00:00+08:00",
    }


class DecisionLifecycleTests(unittest.TestCase):
    def test_new_decision_supersedes_old_decision_and_memory_shows_only_current(self) -> None:
        current = {"schema_version": SCHEMA_VERSION, "items": [decision("D-001")]}

        updated = append_decision(current, decision("D-002", supersedes=["D-001"]))

        validate_document("decisions.json", updated)
        self.assertEqual(updated["items"][0]["status"], "superseded")
        self.assertEqual(updated["items"][0]["superseded_by"], "D-002")
        memory = render_memory(
            {"schema_version": SCHEMA_VERSION, "items": []}, updated
        )
        self.assertNotIn("Decision D-001", memory)
        self.assertIn("Decision D-002", memory)

    def test_approved_artifact_can_supersede_an_active_decision(self) -> None:
        current = {"schema_version": SCHEMA_VERSION, "items": [decision("D-001")]}

        updated = supersede_decisions_by_artifact(
            current, ["D-001"], artifact_ref="analysis@2"
        )

        validate_document("decisions.json", updated)
        self.assertEqual(updated["items"][0]["superseded_by"], "analysis@2")


if __name__ == "__main__":
    unittest.main()
