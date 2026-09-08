"""Load the exact semantic guidance attached to a work packet."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .model import AIWorkflowError


GUIDE_ROOT = Path(__file__).resolve().parents[2]
STAGE_GUIDES = {
    "analysis": "references/stages/analysis.md",
    "design": "references/stages/design.md",
    "specification": "references/stages/specification.md",
    "implementation": "references/stages/implementation.md",
    "testing": "references/stages/testing.md",
}


def load_stage_guide(relative_path: str, *, stage: str) -> dict[str, Any]:
    relative = Path(relative_path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise AIWorkflowError(
            code="stage_guide_invalid",
            message="Stage guide must be a relative path inside the wf Skill.",
            exit_code=4,
            details={"path": relative_path},
        )
    expected = STAGE_GUIDES.get(stage)
    if expected is None or relative.as_posix() != expected:
        raise AIWorkflowError(
            code="stage_guide_mismatch",
            message="Stage guide does not match the active workflow stage.",
            exit_code=4,
            details={"path": relative_path, "stage": stage, "expected": expected},
        )
    path = (GUIDE_ROOT / relative).resolve()
    if not path.is_relative_to(GUIDE_ROOT) or not path.is_file():
        raise AIWorkflowError(
            code="stage_guide_missing",
            message="Configured stage guidance is unavailable.",
            exit_code=5,
            details={"path": relative_path, "stage": stage},
        )
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise AIWorkflowError(
            code="stage_guide_unreadable",
            message="Configured stage guidance cannot be read as UTF-8.",
            exit_code=5,
            details={"path": relative_path, "stage": stage},
        ) from error
    if not content.strip():
        raise AIWorkflowError(
            code="stage_guide_invalid",
            message="Configured stage guidance cannot be empty.",
            exit_code=5,
            details={"path": relative_path, "stage": stage},
        )
    return {
        "id": stage,
        "version": 1,
        "source": relative.as_posix(),
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "instructions": content,
    }
