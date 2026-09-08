from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import run_cli


class CommandLineTests(unittest.TestCase):
    def test_help_lists_all_internal_commands(self) -> None:
        completed = run_cli(["--help"])

        self.assertEqual(completed.returncode, 0, completed.stderr)
        for command in (
            "init",
            "recover",
            "prepare",
            "submit",
            "review",
            "revise",
            "question",
            "decide",
            "route-decision",
            "route-upstream",
            "status",
            "render",
        ):
            self.assertIn(command, completed.stdout)

    def test_cli_does_not_depend_on_calling_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = run_cli(["--version"], cwd=Path(directory))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "aiwf 1.0.0rc12")

    def test_missing_workspace_returns_structured_error(self) -> None:
        completed = run_cli(["status", "--workspace", "/path/that/does/not/exist"])

        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stderr)
        self.assertEqual(payload["error"]["code"], "invalid_workspace")

    def test_uninitialized_directory_is_rejected_without_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            existing = workspace / "CONTEXT.md"
            existing.write_text("# Existing workspace\n", encoding="utf-8")

            completed = run_cli(["status", "--workspace", str(workspace)])

            self.assertEqual(completed.returncode, 5)
            payload = json.loads(completed.stderr)
            self.assertEqual(payload["error"]["code"], "not_initialized")
            self.assertEqual(existing.read_text(encoding="utf-8"), "# Existing workspace\n")
            self.assertFalse((workspace / ".aiwf").exists())

    def test_invalid_arguments_return_structured_error(self) -> None:
        completed = run_cli(["unknown-command"])

        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stderr)
        self.assertEqual(payload["error"]["code"], "invalid_arguments")


if __name__ == "__main__":
    unittest.main()
