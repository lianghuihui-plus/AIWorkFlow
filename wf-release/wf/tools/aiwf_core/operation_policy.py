"""Operation-level health policy shared by CLI and direct engine callers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .model import AIWorkflowError


ADVANCING_OPERATIONS = frozenset({"prepare", "submit", "decision_resume", "approve"})


def assert_operation_allowed(
    operation: str,
    inspection: Mapping[str, Any],
) -> None:
    if operation not in ADVANCING_OPERATIONS:
        return
    if inspection.get("can_advance") is True:
        return
    raise_health_blocked(inspection.get("issues", []))


def raise_health_blocked(issues: Sequence[Mapping[str, Any]]) -> None:
    raise AIWorkflowError(
        code="workspace_health_blocked",
        message="Resolve blocking workspace health issues before advancing the workflow.",
        exit_code=7,
        details={"issues": [dict(issue) for issue in issues]},
    )
