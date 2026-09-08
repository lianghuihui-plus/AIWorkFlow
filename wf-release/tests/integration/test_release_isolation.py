from __future__ import annotations

import subprocess
import unittest

from support import DEVELOP_ROOT, REPOSITORY_ROOT, SOURCE_ROOT


class ReleaseIsolationTests(unittest.TestCase):
    def test_formal_source_is_the_develop_directory(self) -> None:
        self.assertTrue(SOURCE_ROOT.is_relative_to(DEVELOP_ROOT))
        self.assertEqual(SOURCE_ROOT, DEVELOP_ROOT)

    def test_develop_source_does_not_resolve_into_release(self) -> None:
        release_root = (REPOSITORY_ROOT / "wf-release").resolve()
        for path in SOURCE_ROOT.rglob("*"):
            if path.is_symlink():
                self.assertFalse(path.resolve().is_relative_to(release_root), str(path))

    def test_release_has_no_worktree_or_staged_diff(self) -> None:
        for arguments in (
            ["diff", "--quiet", "--", "wf-release"],
            ["diff", "--cached", "--quiet", "--", "wf-release"],
        ):
            completed = subprocess.run(
                ["git", "-C", str(REPOSITORY_ROOT), *arguments],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
