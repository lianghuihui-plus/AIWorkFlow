from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import TOOLS_ROOT

sys.path.insert(0, str(TOOLS_ROOT))

from aiwf_core.model import AIWorkflowError  # noqa: E402
from aiwf_core.storage import WorkspaceStore, resolve_workspace  # noqa: E402


class WorkspaceResolutionTests(unittest.TestCase):
    def test_resolves_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(resolve_workspace(directory), Path(directory).resolve())

    def test_rejects_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            with self.assertRaises(AIWorkflowError) as raised:
                resolve_workspace(str(missing))

        self.assertEqual(raised.exception.code, "invalid_workspace")
        self.assertEqual(raised.exception.exit_code, 2)
        self.assertEqual(str(raised.exception), raised.exception.message)


class WorkspaceBootstrapTests(unittest.TestCase):
    def test_partial_install_is_removed_when_bootstrap_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            store = WorkspaceStore(workspace)
            project = {
                "project_id": "bootstrap-test",
                "name": "Bootstrap Test",
                "platform": "test",
                "code_repository": str(workspace),
                "prd_files": ["prd/input.md"],
            }
            import aiwf_core.storage as storage_module

            real_replace = storage_module.os.replace
            replacements = 0

            def fail_second_replace(source: Path, target: Path) -> None:
                nonlocal replacements
                replacements += 1
                if replacements == 2:
                    raise OSError("injected bootstrap failure")
                real_replace(source, target)

            with patch.object(storage_module.os, "replace", side_effect=fail_second_replace):
                with self.assertRaises(OSError):
                    store.bootstrap(project, prd_files={"input.md": b"input"})

            self.assertEqual(list(workspace.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
