from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from aiwf_core.model import AIWorkflowError
from aiwf_core.repository import (
    checkpoint_repository_session,
    compare_repository_context,
    compare_repository_session,
    inspect_repository,
    resume_repository_session,
    start_repository_session,
    validate_repository_evidence,
)


def initialize_repository(root: Path) -> Path:
    repository = root / "repository"
    repository.mkdir()
    (repository / "app.txt").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repository), "add", "app.txt"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "initial"], check=True)
    return repository


class RepositoryContextTests(unittest.TestCase):
    def test_existing_repository_evidence_requires_real_path_and_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = initialize_repository(Path(directory))

            validate_repository_evidence(
                str(repository), [{"path": "app.txt", "symbol": "initial"}]
            )
            with self.assertRaises(AIWorkflowError) as raised:
                validate_repository_evidence(
                    str(repository), [{"path": "app.txt", "symbol": "missingSymbol"}]
                )

            self.assertEqual(raised.exception.code, "repository_symbol_missing")

    def test_repository_evidence_always_requires_a_current_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = initialize_repository(Path(directory))

            with self.assertRaises(AIWorkflowError) as raised:
                validate_repository_evidence(
                    str(repository),
                    [{"path": "src/planned.py", "symbol": "PlannedType"}],
                )

            self.assertEqual(raised.exception.code, "repository_evidence_missing")

    def test_git_subdirectory_remains_the_configured_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            git_root = initialize_repository(Path(directory))
            scope = git_root / "apps" / "target"
            scope.mkdir(parents=True)
            (scope / "feature.txt").write_text("FeatureRoot\n", encoding="utf-8")

            context = inspect_repository(str(scope))

            self.assertEqual(context["root"], str(scope.resolve()))
            self.assertEqual(context["git_root"], str(git_root.resolve()))
            self.assertEqual(context["scope_prefix"], "apps/target")
            validate_repository_evidence(
                context["root"],
                [{"path": "feature.txt", "symbol": "FeatureRoot"}],
            )

    def test_git_subdirectory_rejects_changes_outside_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            git_root = initialize_repository(Path(directory))
            scope = git_root / "apps" / "target"
            scope.mkdir(parents=True)
            before = inspect_repository(str(scope))
            (git_root / "app.txt").write_text("outside change\n", encoding="utf-8")

            with self.assertRaises(AIWorkflowError) as raised:
                compare_repository_context(before, inspect_repository(str(scope)))

            self.assertEqual(raised.exception.code, "repository_scope_violation")

    def test_clean_file_change_is_observed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = initialize_repository(Path(directory))
            before = inspect_repository(str(repository))
            (repository / "app.txt").write_text("changed\n", encoding="utf-8")

            comparison = compare_repository_context(before, inspect_repository(str(repository)))

            self.assertEqual(comparison["verification_level"], "git_delta")
            self.assertEqual(comparison["changed_files"], ["app.txt"])

    def test_unchanged_preexisting_dirty_file_is_not_attributed_to_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = initialize_repository(Path(directory))
            (repository / "app.txt").write_text("user change\n", encoding="utf-8")
            before = inspect_repository(str(repository))

            comparison = compare_repository_context(before, inspect_repository(str(repository)))

            self.assertEqual(comparison["changed_files"], [])

    def test_further_change_to_preexisting_dirty_file_is_observed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = initialize_repository(Path(directory))
            (repository / "app.txt").write_text("user change\n", encoding="utf-8")
            before = inspect_repository(str(repository))
            (repository / "app.txt").write_text("agent change\n", encoding="utf-8")

            comparison = compare_repository_context(before, inspect_repository(str(repository)))

            self.assertEqual(comparison["changed_files"], ["app.txt"])

    def test_head_change_invalidates_the_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = initialize_repository(Path(directory))
            before = inspect_repository(str(repository))
            (repository / "other.txt").write_text("new\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "other.txt"], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-qm", "other"], check=True)

            with self.assertRaises(AIWorkflowError) as raised:
                compare_repository_context(before, inspect_repository(str(repository)))

            self.assertEqual(raised.exception.code, "repository_baseline_changed")

    def test_resume_absorbs_non_overlapping_external_changes_without_claiming_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = initialize_repository(Path(directory))
            session = start_repository_session(str(repository))
            (repository / "app.txt").write_text("agent change\n", encoding="utf-8")
            paused = checkpoint_repository_session(session)
            (repository / "external.txt").write_text("external change\n", encoding="utf-8")

            resumed = resume_repository_session(paused)
            (repository / "final.txt").write_text("agent final\n", encoding="utf-8")
            comparison = compare_repository_session(
                resumed, inspect_repository(str(repository))
            )

            self.assertEqual(comparison["changed_files"], ["app.txt", "final.txt"])

    def test_resume_rejects_external_changes_to_an_owned_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = initialize_repository(Path(directory))
            session = start_repository_session(str(repository))
            (repository / "app.txt").write_text("agent change\n", encoding="utf-8")
            paused = checkpoint_repository_session(session)
            (repository / "app.txt").write_text("overlapping external change\n", encoding="utf-8")

            with self.assertRaises(AIWorkflowError) as raised:
                resume_repository_session(paused)

            self.assertEqual(raised.exception.code, "repository_pause_conflict")


if __name__ == "__main__":
    unittest.main()
