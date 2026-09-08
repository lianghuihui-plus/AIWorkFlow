"""Structured requirement-source normalization and validation."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .model import AIWorkflowError

WORK_FEEDBACK_REF = re.compile(r"^(W-\d{6,})#feedback$")


def normalize_requirement_sources(
    requirements: Sequence[Mapping[str, Any]],
    *,
    workspace_root: Path,
    work: Mapping[str, Any],
    decisions: Mapping[str, Any],
    artifact_ref: str,
    archived_work_ids: set[str],
) -> list[dict[str, Any]]:
    decision_ids = {item["id"] for item in decisions["items"]}
    normalized: list[dict[str, Any]] = []
    for requirement in requirements:
        updated = dict(requirement)
        sources: list[dict[str, str]] = []
        for source in requirement["sources"]:
            kind = source["kind"]
            ref = source["ref"]
            if kind == "prd":
                _validate_prd_ref(workspace_root, ref)
            elif kind == "user_decision":
                if ref not in decision_ids:
                    _invalid_source(kind, ref, "Decision source must reference an existing D-id.")
            elif kind == "user_feedback":
                match = WORK_FEEDBACK_REF.fullmatch(ref)
                known = archived_work_ids | {str(work["work_id"])}
                if match is None or match.group(1) not in known:
                    _invalid_source(
                        kind,
                        ref,
                        "Feedback source must use '<W-id>#feedback' for a known work item.",
                    )
                if match.group(1) == work["work_id"] and not work.get("feedback"):
                    _invalid_source(kind, ref, "Current work does not contain user feedback.")
            elif kind == "agent_inference":
                if ref != "self":
                    _invalid_source(kind, ref, "Agent inference source must use 'self'.")
                ref = artifact_ref
            sources.append({"kind": kind, "ref": ref})
        updated["sources"] = sources
        normalized.append(updated)
    return normalized


def _validate_prd_ref(workspace_root: Path, raw_ref: str) -> None:
    raw_path = raw_ref.split("#", 1)[0]
    relative = Path(raw_path)
    target = (workspace_root / relative).resolve(strict=False)
    if (
        relative.is_absolute()
        or not relative.parts
        or relative.parts[0] != "prd"
        or ".." in relative.parts
        or not target.is_relative_to(workspace_root.resolve())
        or not target.is_file()
    ):
        _invalid_source("prd", raw_ref, "PRD source must reference an existing workspace PRD file.")


def _invalid_source(kind: str, ref: str, message: str) -> None:
    raise AIWorkflowError(
        code="invalid_requirement_source",
        message=message,
        exit_code=4,
        details={"kind": kind, "ref": ref},
    )
