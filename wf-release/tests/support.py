"""Shared paths and subprocess helpers for AIWorkFlow tests."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

DEVELOP_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = DEVELOP_ROOT.parent
SOURCE_ROOT = DEVELOP_ROOT
TOOLS_ROOT = SOURCE_ROOT / "wf" / "tools"
CLI_PATH = TOOLS_ROOT / "aiwf.py"
sys.path.insert(0, str(TOOLS_ROOT))

from aiwf_core.workflow import WorkflowEngine  # noqa: E402


def run_cli(
    arguments: Sequence[str],
    *,
    cwd: Path = DEVELOP_ROOT,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *arguments],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def bootstrap_engine(workspace: Path) -> WorkflowEngine:
    repository = workspace.parent / f"{workspace.name}-repository"
    repository.mkdir()
    (repository / "app.txt").write_text("ApplicationRoot\n", encoding="utf-8")
    engine = WorkflowEngine(workspace)
    engine.bootstrap(
        {
            "project_id": "test-project",
            "name": "Test Project",
            "platform": "test",
            "code_repository": str(repository),
            "prd_files": [],
        }
    )
    (workspace / "prd" / "requirements.md").write_text(
        "# Requirements\n\nSave drafts.\n", encoding="utf-8"
    )
    return engine


def write_work_outputs(
    engine: WorkflowEngine,
    work: dict[str, object],
    *,
    markdown: str,
    result: dict[str, object],
) -> None:
    engine.store.safe_path(str(work["draft_output"])).write_text(markdown, encoding="utf-8")
    engine.store.safe_path(str(work["result_output"])).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
