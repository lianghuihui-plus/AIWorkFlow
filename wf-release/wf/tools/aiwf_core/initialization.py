"""Deterministic workspace initialization inputs and PRD discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .model import AIWorkflowError


@dataclass(frozen=True)
class InitializationInput:
    project: dict[str, Any]
    prd_files: dict[str, bytes]


def prepare_initialization(
    *,
    workspace: Path,
    name: str,
    platform: str,
    prd_paths: Sequence[str],
    code_repository: str,
    project_id: str | None = None,
) -> InitializationInput:
    project_name = name.strip()
    project_platform = platform.strip()
    if not project_name:
        raise AIWorkflowError(
            code="invalid_project_name",
            message="Project name cannot be empty.",
            exit_code=2,
        )
    if not project_platform:
        raise AIWorkflowError(
            code="invalid_platform",
            message="Platform cannot be empty.",
            exit_code=2,
        )

    repository = _resolve_repository(code_repository)
    discovered = discover_prd_files(prd_paths)
    copied_files: dict[str, bytes] = {}
    normalized_names: dict[str, Path] = {}
    for source in discovered:
        destination_name = source.name
        normalized_name = destination_name.casefold()
        existing = normalized_names.get(normalized_name)
        if existing is not None and existing != source:
            raise AIWorkflowError(
                code="prd_name_conflict",
                message="PRD files must have unique filenames.",
                exit_code=2,
                details={
                    "filename": destination_name,
                    "sources": [str(existing), str(source)],
                },
            )
        normalized_names[normalized_name] = source
        try:
            copied_files[destination_name] = source.read_bytes()
        except OSError as error:
            raise AIWorkflowError(
                code="prd_unreadable",
                message="PRD file cannot be read.",
                exit_code=2,
                details={"path": str(source)},
            ) from error

    resolved_project_id = project_id.strip() if project_id is not None else _default_project_id(
        project_name, workspace.name
    )
    if not resolved_project_id:
        raise AIWorkflowError(
            code="invalid_project_id",
            message="Project id cannot be empty.",
            exit_code=2,
        )
    project = {
        "project_id": resolved_project_id,
        "name": project_name,
        "platform": project_platform,
        "code_repository": str(repository),
        "prd_files": [f"prd/{name}" for name in sorted(copied_files, key=str.casefold)],
    }
    return InitializationInput(project=project, prd_files=copied_files)


def discover_prd_files(raw_paths: Sequence[str]) -> list[Path]:
    if not raw_paths:
        raise AIWorkflowError(
            code="prd_required",
            message="At least one PRD file or directory is required.",
            exit_code=2,
        )
    discovered: dict[Path, None] = {}
    for raw_path in raw_paths:
        candidate = Path(raw_path).expanduser()
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise AIWorkflowError(
                code="prd_not_found",
                message="PRD path does not exist or cannot be resolved.",
                exit_code=2,
                details={"path": str(candidate)},
            ) from error
        if resolved.is_file():
            discovered[resolved] = None
        elif resolved.is_dir():
            try:
                children = sorted(
                    (child.resolve() for child in resolved.iterdir() if child.is_file()),
                    key=lambda path: (path.name.casefold(), str(path)),
                )
            except OSError as error:
                raise AIWorkflowError(
                    code="prd_unreadable",
                    message="PRD directory cannot be scanned.",
                    exit_code=2,
                    details={"path": str(resolved)},
                ) from error
            for child in children:
                discovered[child] = None
        else:
            raise AIWorkflowError(
                code="prd_invalid_type",
                message="PRD path must be a regular file or directory.",
                exit_code=2,
                details={"path": str(resolved)},
            )
    if not discovered:
        raise AIWorkflowError(
            code="prd_empty",
            message="No regular PRD files were found.",
            exit_code=2,
        )
    return list(discovered)


def _resolve_repository(raw_path: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise AIWorkflowError(
            code="code_repository_required",
            message="A code repository directory is required.",
            exit_code=2,
        )
    candidate = Path(raw_path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise AIWorkflowError(
            code="code_repository_not_found",
            message="Code repository path does not exist or cannot be resolved.",
            exit_code=2,
            details={"path": str(candidate)},
        ) from error
    if not resolved.is_dir():
        raise AIWorkflowError(
            code="code_repository_invalid",
            message="Code repository path must be a directory.",
            exit_code=2,
            details={"path": str(resolved)},
        )
    return resolved


def _default_project_id(name: str, workspace_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    if normalized:
        return normalized
    fallback = re.sub(r"\s+", "-", workspace_name.strip()).strip("-")
    return fallback or "aiworkflow-project"
