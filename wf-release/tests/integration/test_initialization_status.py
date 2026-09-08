from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import SOURCE_ROOT, bootstrap_engine, run_cli

from aiwf_core.model import validate_document
from aiwf_core.storage import DATA_FILES, InjectedTransactionFailure, json_bytes


def workspace_snapshot(root: Path) -> dict[str, tuple[str, int, int, bytes | None]]:
    snapshot: dict[str, tuple[str, int, int, bytes | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        snapshot[relative] = (
            "file" if path.is_file() else "directory",
            stat.st_mtime_ns,
            stat.st_size,
            path.read_bytes() if path.is_file() else None,
        )
    return snapshot


class InitializationTests(unittest.TestCase):
    def test_cli_initializes_workspace_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            repository = root / "repository"
            repository.mkdir()
            prd = root / "requirements.md"
            prd.write_text("# Requirement\n", encoding="utf-8")

            completed = run_cli(
                [
                    "init",
                    "--workspace",
                    str(workspace),
                    "--name",
                    "Offline Assistant",
                    "--platform",
                    "HarmonyOS",
                    "--prd",
                    str(prd),
                    "--code-repository",
                    str(repository),
                ]
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)["result"]
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["next_action"], "analyze_requirements")
            self.assertEqual(result["counts"]["prd_files"], 1)
            self.assertEqual(result["project"]["project_id"], "offline-assistant")
            self.assertEqual(result["project"]["code_repository"], str(repository.resolve()))
            self.assertEqual((workspace / "prd/requirements.md").read_text(), "# Requirement\n")

            for name in DATA_FILES:
                document = json.loads((workspace / ".aiwf" / name).read_text(encoding="utf-8"))
                validate_document(name, document)

    def test_prd_directory_discovery_is_non_recursive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            prd_directory = root / "prd-source"
            prd_directory.mkdir()
            (prd_directory / "one.md").write_text("one", encoding="utf-8")
            (prd_directory / "two.txt").write_text("two", encoding="utf-8")
            nested = prd_directory / "nested"
            nested.mkdir()
            (nested / "ignored.md").write_text("ignored", encoding="utf-8")

            completed = run_cli(
                [
                    "init",
                    "--workspace",
                    str(workspace),
                    "--platform",
                    "web",
                    "--prd",
                    str(prd_directory),
                    "--code-repository",
                    str(root),
                ]
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                sorted(path.name for path in (workspace / "prd").iterdir()),
                ["one.md", "two.txt"],
            )
            project = json.loads((workspace / ".aiwf/project.json").read_text(encoding="utf-8"))
            self.assertEqual(project["prd_files"], ["prd/one.md", "prd/two.txt"])

    def test_prd_filename_collision_is_rejected_before_workspace_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            (first / "Spec.md").write_text("first", encoding="utf-8")
            (second / "spec.MD").write_text("second", encoding="utf-8")

            completed = run_cli(
                [
                    "init",
                    "--workspace",
                    str(workspace),
                    "--platform",
                    "web",
                    "--prd",
                    str(first),
                    "--prd",
                    str(second),
                    "--code-repository",
                    str(root),
                ]
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(json.loads(completed.stderr)["error"]["code"], "prd_name_conflict")
            self.assertEqual(list(workspace.iterdir()), [])

    def test_non_empty_workspace_is_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            existing = workspace / "keep.txt"
            existing.write_text("keep", encoding="utf-8")
            prd = root / "requirements.md"
            prd.write_text("requirements", encoding="utf-8")
            before = workspace_snapshot(workspace)

            completed = run_cli(
                [
                    "init",
                    "--workspace",
                    str(workspace),
                    "--platform",
                    "web",
                    "--prd",
                    str(prd),
                    "--code-repository",
                    str(root),
                ]
            )

            self.assertEqual(completed.returncode, 5)
            self.assertEqual(json.loads(completed.stderr)["error"]["code"], "workspace_not_empty")
            self.assertEqual(workspace_snapshot(workspace), before)

    def test_code_repository_argument_is_required_before_workspace_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            prd = root / "requirements.md"
            prd.write_text("requirements", encoding="utf-8")

            completed = run_cli(
                [
                    "init",
                    "--workspace",
                    str(workspace),
                    "--platform",
                    "web",
                    "--prd",
                    str(prd),
                ]
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(json.loads(completed.stderr)["error"]["code"], "invalid_arguments")
            self.assertEqual(list(workspace.iterdir()), [])

    def test_invalid_code_repositories_are_rejected_without_workspace_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prd = root / "requirements.md"
            prd.write_text("requirements", encoding="utf-8")
            invalid_file = root / "repository.txt"
            invalid_file.write_text("not a directory", encoding="utf-8")
            for repository, expected_code in (
                (root / "missing", "code_repository_not_found"),
                (invalid_file, "code_repository_invalid"),
            ):
                with self.subTest(code=expected_code):
                    workspace = root / f"workspace-{expected_code}"
                    workspace.mkdir()
                    completed = run_cli(
                        [
                            "init",
                            "--workspace",
                            str(workspace),
                            "--platform",
                            "web",
                            "--prd",
                            str(prd),
                            "--code-repository",
                            str(repository),
                        ]
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertEqual(json.loads(completed.stderr)["error"]["code"], expected_code)
                    self.assertEqual(list(workspace.iterdir()), [])


class StatusTests(unittest.TestCase):
    def test_status_loads_initialized_workspace_in_a_fresh_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            bootstrap_engine(workspace)

            completed = run_cli(["status", "--workspace", str(workspace)])

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)["result"]
            self.assertEqual(result["project"]["project_id"], "test-project")
            self.assertEqual(result["state"]["current_stage"], "analysis")
            self.assertEqual(result["next_action"], "analyze_requirements")

    def test_status_does_not_change_workspace_files_or_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            bootstrap_engine(workspace)
            before = workspace_snapshot(workspace)

            completed = run_cli(["status", "--workspace", str(workspace)])

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(workspace_snapshot(workspace), before)

    def test_unavailable_code_repository_is_a_blocking_health_issue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            repository = root / "repository"
            repository.mkdir()
            prd = root / "requirements.md"
            prd.write_text("requirements", encoding="utf-8")
            completed = run_cli(
                [
                    "init",
                    "--workspace",
                    str(workspace),
                    "--platform",
                    "web",
                    "--prd",
                    str(prd),
                    "--code-repository",
                    str(repository),
                ]
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            repository.rmdir()

            status = json.loads(
                run_cli(["status", "--workspace", str(workspace)]).stdout
            )["result"]

            self.assertFalse(status["can_advance"])
            self.assertEqual(status["next_action"], "resolve_health_issues")
            issue = next(
                item for item in status["issues"] if item["type"] == "code_repository_unavailable"
            )
            self.assertTrue(issue["blocking"])
            self.assertEqual(issue["recovery_action"], "restore_code_repository")

    def test_status_reports_pending_recovery_without_recovering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            engine = bootstrap_engine(workspace)
            state = engine.store.read_json("state.json")
            with engine.store.lock(exclusive=True):
                engine.store.inject_failure_after(1)
                with self.assertRaises(InjectedTransactionFailure):
                    engine.store.commit_locked(
                        {".aiwf/state.json": json_bytes(state)},
                        event_type="test_pending_recovery",
                        event_data={},
                        command_key="test:pending-recovery",
                        request_digest="pending-recovery",
                    )
            before = workspace_snapshot(workspace)

            completed = run_cli(["status", "--workspace", str(workspace)])

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["result"]["status"], "needs_recovery")
            self.assertEqual(workspace_snapshot(workspace), before)


class SkillLocationTests(unittest.TestCase):
    def test_thin_skills_resolve_the_sibling_engine(self) -> None:
        expected_engine = SOURCE_ROOT / "wf/tools/aiwf.py"
        self.assertTrue(expected_engine.is_file())
        for skill_name in ("wf-init", "wf-status"):
            skill_file = SOURCE_ROOT / skill_name / "SKILL.md"
            resolved_engine = skill_file.resolve().parent.parent / "wf/tools/aiwf.py"
            self.assertEqual(resolved_engine, expected_engine)
            content = skill_file.read_text(encoding="utf-8")
            self.assertIn(f"<{skill_name}-skill-dir>/../wf/tools/aiwf.py", content)
            self.assertNotIn("wf-release", content)


if __name__ == "__main__":
    unittest.main()
