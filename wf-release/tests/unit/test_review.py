from __future__ import annotations

import unittest

import support  # noqa: F401

from aiwf_core.model import SCHEMA_VERSION
from aiwf_core.review import apply_memory_delta


class MemoryReviewTests(unittest.TestCase):
    def test_memory_entries_can_be_added_updated_and_retracted_by_stable_id(self) -> None:
        memory = {"schema_version": SCHEMA_VERSION, "items": []}
        memory = apply_memory_delta(
            memory,
            [
                {
                    "operation": "add",
                    "type": "architecture_decision",
                    "content": "Initial fact",
                    "evidence": [],
                    "rationale": "Initial rationale.",
                    "validation": None,
                    "target_id": None,
                }
            ],
            source="analysis@1",
        )
        self.assertEqual(memory["items"][0]["id"], "M-001")

        memory = apply_memory_delta(
            memory,
            [
                {
                    "operation": "update",
                    "type": "engineering_default",
                    "content": "Updated fact",
                    "evidence": [],
                    "rationale": "Updated rationale.",
                    "validation": "Verify during implementation.",
                    "target_id": "M-001",
                }
            ],
            source="design@1",
        )
        self.assertEqual(memory["items"][0]["content"], "Updated fact")
        self.assertEqual(memory["items"][0]["source"], "design@1")

        memory = apply_memory_delta(
            memory,
            [
                {
                    "operation": "retract",
                    "type": "engineering_default",
                    "content": "No longer applies",
                    "evidence": [],
                    "rationale": "Superseded.",
                    "validation": "No validation remains.",
                    "target_id": "M-001",
                }
            ],
            source="design@2",
        )
        self.assertEqual(memory["items"][0]["status"], "retracted")


if __name__ == "__main__":
    unittest.main()
