from __future__ import annotations

import unittest

from aiwf_core.memory_view import render_memory


class MemoryViewTests(unittest.TestCase):
    def test_work_projection_keeps_upstream_memory_and_all_active_decisions(self) -> None:
        memory = {
            "items": [
                {
                    "id": "M-001",
                    "type": "architecture_decision",
                    "content": "Relevant architecture",
                    "source": "design@1",
                    "status": "active",
                    "evidence": [],
                    "rationale": "Relevant",
                    "validation": None,
                },
                {
                    "id": "M-002",
                    "type": "architecture_decision",
                    "content": "Unrelated task detail",
                    "source": "T-002-spec@1",
                    "status": "active",
                    "evidence": [],
                    "rationale": "Unrelated",
                    "validation": None,
                },
            ]
        }
        decisions = {
            "items": [
                {
                    "id": "D-001",
                    "question_id": "Q-001",
                    "decision": "Current user decision",
                    "status": "active",
                }
            ]
        }

        rendered = render_memory(memory, decisions, sources={"design@1"})

        self.assertIn("Relevant architecture", rendered)
        self.assertNotIn("Unrelated task detail", rendered)
        self.assertIn("Current user decision", rendered)


if __name__ == "__main__":
    unittest.main()
