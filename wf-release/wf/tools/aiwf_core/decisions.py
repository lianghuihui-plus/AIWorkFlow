"""Decision lifecycle and current-context projection."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .model import AIWorkflowError, SCHEMA_VERSION


def validate_active_decision_ids(
    decisions: dict[str, Any],
    decision_ids: Sequence[str],
) -> None:
    by_id = {item["id"]: item for item in decisions["items"]}
    invalid = sorted(
        decision_id
        for decision_id in set(decision_ids)
        if decision_id not in by_id or by_id[decision_id]["status"] != "active"
    )
    if invalid:
        raise AIWorkflowError(
            code="invalid_decision_supersession",
            message="Only active decisions can be superseded.",
            exit_code=4,
            details={"ids": invalid},
        )


def append_decision(
    decisions: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    supersedes = list(decision["supersedes"])
    validate_active_decision_ids(decisions, supersedes)
    replacement_id = decision["id"]
    items: list[dict[str, Any]] = []
    for current in decisions["items"]:
        if current["id"] in supersedes:
            current = {
                **current,
                "status": "superseded",
                "superseded_by": replacement_id,
            }
        items.append(dict(current))
    items.append(dict(decision))
    return {"schema_version": SCHEMA_VERSION, "items": items}


def supersede_decisions_by_artifact(
    decisions: dict[str, Any],
    decision_ids: Sequence[str],
    *,
    artifact_ref: str,
) -> dict[str, Any]:
    validate_active_decision_ids(decisions, decision_ids)
    targets = set(decision_ids)
    return {
        "schema_version": SCHEMA_VERSION,
        "items": [
            {
                **item,
                "status": "superseded",
                "superseded_by": artifact_ref,
            }
            if item["id"] in targets
            else dict(item)
            for item in decisions["items"]
        ],
    }


def active_decisions(decisions: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in decisions["items"] if item["status"] == "active"]
