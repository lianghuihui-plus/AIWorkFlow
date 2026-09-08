from __future__ import annotations

import unittest

from aiwf_core.file_roles import classify_file_role, validate_stage_file_roles
from aiwf_core.model import AIWorkflowError


class FileRoleTests(unittest.TestCase):
    def test_classifies_only_high_confidence_paths(self) -> None:
        self.assertEqual(classify_file_role("src/editor.ts"), "production")
        self.assertEqual(classify_file_role("tests/test_editor.py"), "test")
        self.assertEqual(classify_file_role("EditorTests.swift"), "test")
        self.assertEqual(classify_file_role("config/editor.json"), "ambiguous")

    def test_stage_boundaries_reject_only_clear_cross_role_changes(self) -> None:
        with self.assertRaises(AIWorkflowError):
            validate_stage_file_roles("implementation", ["tests/test_editor.py"])
        with self.assertRaises(AIWorkflowError):
            validate_stage_file_roles("testing", ["src/editor.ts"])

        validate_stage_file_roles("implementation", ["src/editor.ts", "config/app.json"])
        validate_stage_file_roles("testing", ["tests/test_editor.py", "config/test.json"])


if __name__ == "__main__":
    unittest.main()
