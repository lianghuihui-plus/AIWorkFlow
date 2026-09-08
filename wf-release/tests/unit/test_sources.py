from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from aiwf_core.model import AIWorkflowError, SCHEMA_VERSION
from aiwf_core.sources import normalize_requirement_sources


class RequirementSourceTests(unittest.TestCase):
    def test_sources_are_addressable_and_inference_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "prd").mkdir()
            (workspace / "prd" / "input.md").write_text("# Input\n", encoding="utf-8")
            requirements = [
                {
                    "sources": [
                        {"kind": "prd", "ref": "prd/input.md#scope"},
                        {"kind": "user_decision", "ref": "D-001"},
                        {"kind": "user_feedback", "ref": "W-000001#feedback"},
                        {"kind": "agent_inference", "ref": "self"},
                    ]
                }
            ]

            normalized = normalize_requirement_sources(
                requirements,
                workspace_root=workspace,
                work={"work_id": "W-000001", "feedback": "Latest scope"},
                decisions={
                    "schema_version": SCHEMA_VERSION,
                    "items": [{"id": "D-001"}],
                },
                artifact_ref="analysis@2",
                archived_work_ids=set(),
            )

            self.assertEqual(normalized[0]["sources"][-1]["ref"], "analysis@2")

    def test_unknown_feedback_work_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with self.assertRaises(AIWorkflowError) as raised:
                normalize_requirement_sources(
                    [{"sources": [{"kind": "user_feedback", "ref": "W-999999#feedback"}]}],
                    workspace_root=workspace,
                    work={"work_id": "W-000001", "feedback": "Latest scope"},
                    decisions={"schema_version": SCHEMA_VERSION, "items": []},
                    artifact_ref="analysis@1",
                    archived_work_ids=set(),
                )

            self.assertEqual(raised.exception.code, "invalid_requirement_source")


if __name__ == "__main__":
    unittest.main()
