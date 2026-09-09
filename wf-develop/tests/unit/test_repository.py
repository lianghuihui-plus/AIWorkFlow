from __future__ import annotations

import tempfile
import unittest

import support  # noqa: F401

from aiwf_core.repository import repository_context


class RepositoryTests(unittest.TestCase):
    def test_context_contains_no_git_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = repository_context(directory)
            self.assertEqual(set(result), {"root"})


if __name__ == "__main__":
    unittest.main()
