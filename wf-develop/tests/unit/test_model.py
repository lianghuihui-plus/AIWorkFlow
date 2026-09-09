from __future__ import annotations

import unittest

from support import DEVELOP_ROOT, TOOLS_ROOT

from aiwf_core.model import COMMAND_SPECS, STAGES


class ModelContractTests(unittest.TestCase):
    def test_stage_vocabulary_is_stable(self) -> None:
        self.assertEqual(
            STAGES,
            ("analysis", "design", "specification", "implementation", "testing", "completed"),
        )

    def test_cli_excludes_removed_control_commands(self) -> None:
        names = {item.name for item in COMMAND_SPECS}
        self.assertIn("reconcile", names)
        self.assertNotIn("resolve-drift", names)
        self.assertNotIn("route-decision", names)
        self.assertNotIn("route-upstream", names)

    def test_development_paths_are_local(self) -> None:
        self.assertTrue((TOOLS_ROOT / "aiwf.py").is_file())
        self.assertEqual(DEVELOP_ROOT.name, "wf-develop")


if __name__ == "__main__":
    unittest.main()
