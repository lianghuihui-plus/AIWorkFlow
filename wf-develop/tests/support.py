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


def submit_and_approve(
    engine: WorkflowEngine,
    work: dict[str, object],
    *,
    markdown: str,
    result: dict[str, object],
) -> dict[str, object]:
    write_work_outputs(engine, work, markdown=markdown, result=result)
    submitted = engine.submit_work(str(work["work_id"]))
    return engine.review_artifact(
        str(submitted["artifact_id"]),
        int(submitted["revision"]),
        outcome="approved",
    )


def advance_to_tasks(
    engine: WorkflowEngine,
    tasks: list[dict[str, object]],
) -> None:
    analysis = engine.prepare_work()
    submit_and_approve(
        engine,
        analysis,
        markdown="# Analysis\n\nSave drafts.\n",
        result={
            "schema_version": 11,
            "stage": "analysis",
            "target_platform": "test",
            "requirements": [
                {
                    "title": "Save drafts",
                    "summary": "Persist and restore drafts.",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "platform_scope": "target",
                    "change_type": "new",
                    "scope_reason": "Core target behavior.",
                    "disposition": "proposed",
                }
            ],
            "withdrawn_requirements": [],
        },
    )
    design = engine.prepare_work()
    submit_and_approve(
        engine,
        design,
        markdown="# Design\n\nUse the application root.\n",
        result={
            "schema_version": 11,
            "stage": "design",
            "requirements": ["REQ-001"],
            "design_mode": "anchored",
            "greenfield_reason": None,
            "code_evidence": [
                {"path": "app.txt", "symbol": "ApplicationRoot", "purpose": "Integration root"}
            ],
        },
    )
    plan = engine.prepare_work()
    submit_and_approve(
        engine,
        plan,
        markdown="# Task plan\n\nIndependent task plan.\n",
        result={
            "schema_version": 11,
            "stage": "specification",
            "tasks": tasks,
            "withdrawn_tasks": [],
        },
    )


def approve_task_spec(engine: WorkflowEngine, task_id: str) -> None:
    work = engine.prepare_work(active_item=task_id)
    submit_and_approve(
        engine,
        work,
        markdown=f"# {task_id} specification\n\nAcceptance: behavior is available.\n",
        result={
            "schema_version": 11,
            "stage": "specification",
            "task_id": task_id,
            "acceptance_criteria": ["Behavior is available"],
        },
    )


def approve_task_implementation(
    engine: WorkflowEngine,
    task_id: str,
    *,
    changed_files: list[str] | None = None,
) -> None:
    work = engine.prepare_work(active_item=task_id)
    submit_and_approve(
        engine,
        work,
        markdown=f"# {task_id} implementation\n\nAcceptance verified.\n",
        result={
            "schema_version": 11,
            "stage": "implementation",
            "task_id": task_id,
            "summary": "The task behavior is available.",
            "changed_files": list(changed_files or []),
            "acceptance_results": [
                {
                    "criterion": "Behavior is available",
                    "status": "passed",
                    "evidence": "Current repository behavior was inspected.",
                }
            ],
            "validation": ["Targeted verification passed."],
            "risks": [],
        },
    )
