from __future__ import annotations

import unittest

import support  # noqa: F401

from aiwf_core.model import AIWorkflowError, SCHEMA_VERSION, next_id, validate_document


class SchemaTests(unittest.TestCase):
    def test_state_is_only_lightweight_metadata(self) -> None:
        validate_document(
            "state.json",
            {"schema_version": SCHEMA_VERSION, "updated_at": "2026-09-08T10:00:00+08:00"},
        )
        with self.assertRaises(AIWorkflowError):
            validate_document(
                "state.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "updated_at": "2026-09-08T10:00:00+08:00",
                    "active_work": "W-000001",
                },
            )

    def test_dependency_graph_rejects_cycles(self) -> None:
        base = {
            "title": "Task",
            "requirements": ["REQ-001"],
            "status": "active",
            "origin_revision": 1,
            "revision": 1,
            "approved_revision": 1,
            "semantic_sha256": "a" * 64,
            "content_sha256": "b" * 64,
        }
        with self.assertRaises(AIWorkflowError) as raised:
            validate_document(
                "tasks.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "items": [
                        {"id": "T-001", **base, "depends_on": ["T-002"]},
                        {"id": "T-002", **base, "depends_on": ["T-001"]},
                    ],
                },
            )
        self.assertEqual(raised.exception.code, "task_dependency_cycle")

    def test_next_id_uses_stable_prefixes(self) -> None:
        self.assertEqual(next_id("task", ["T-001", "T-003"]), "T-004")

    def test_project_memory_is_not_a_managed_document(self) -> None:
        legacy = {"legacy": "ignored"}
        self.assertIs(validate_document("memory.json", legacy), legacy)


if __name__ == "__main__":
    unittest.main()
