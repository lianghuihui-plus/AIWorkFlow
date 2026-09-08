from __future__ import annotations

import sys
import unittest

from support import DEVELOP_ROOT, TOOLS_ROOT

sys.path.insert(0, str(TOOLS_ROOT))

from aiwf_core.model import (  # noqa: E402
    AIWorkflowError,
    COMMAND_SPECS,
    MODES,
    SCHEMA_VERSION,
    STAGES,
    TASK_STATUSES,
)
from aiwf_core.stage_guides import load_stage_guide  # noqa: E402


class WorkflowModelTests(unittest.TestCase):
    def test_design_guide_requires_mermaid_for_diagrams(self) -> None:
        guide = (DEVELOP_ROOT / "wf/references/stages/design.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("流程图、类图、关系图", guide)
        self.assertIn("以 `mermaid` 标记的 Markdown 围栏代码块", guide)
        self.assertIn("不要求为简单内容机械补图", guide)

    def test_command_surface_matches_approved_plan(self) -> None:
        self.assertEqual(
            [spec.name for spec in COMMAND_SPECS],
            [
                "init",
                "recover",
                "prepare",
                "submit",
                "review",
                "revise",
                "resolve-drift",
                "question",
                "decide",
                "route-decision",
                "route-upstream",
                "status",
                "render",
            ],
        )

    def test_stage_and_mode_vocabulary_is_stable(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 9)
        self.assertNotIn("deferred", TASK_STATUSES)
        self.assertEqual(
            STAGES,
            (
                "analysis",
                "design",
                "specification",
                "implementation",
                "testing",
                "completed",
            ),
        )
        self.assertEqual(MODES, ("ready", "working", "review", "blocked", "decision"))

    def test_stage_guide_cannot_be_swapped_across_stages(self) -> None:
        with self.assertRaises(AIWorkflowError) as raised:
            load_stage_guide("references/stages/design.md", stage="analysis")

        self.assertEqual(raised.exception.code, "stage_guide_mismatch")


if __name__ == "__main__":
    unittest.main()
