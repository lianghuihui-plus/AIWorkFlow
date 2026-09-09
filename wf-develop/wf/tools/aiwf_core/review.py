"""Approve requirement and task index revisions."""

from __future__ import annotations

from typing import Any

from .model import SCHEMA_VERSION


def approve_indexes(
    *,
    stage: str,
    revision: int,
    active_item: str | None,
    requirements: dict[str, Any],
    tasks: dict[str, Any],
    approve_all_pending: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    requirement_items = [dict(item) for item in requirements["items"]]
    task_items = [dict(item) for item in tasks["items"]]
    if stage == "analysis":
        for item in requirement_items:
            if item["origin_revision"] == revision or approve_all_pending:
                if item["disposition"] == "proposed":
                    item["disposition"] = "accepted"
                item["approved_revision"] = item["revision"]
    elif stage == "specification" and active_item is None:
        for item in task_items:
            if item["origin_revision"] == revision or approve_all_pending:
                if item["status"] == "proposed":
                    item["status"] = "active"
                item["approved_revision"] = item["revision"]
    return (
        {"schema_version": SCHEMA_VERSION, "items": requirement_items},
        {"schema_version": SCHEMA_VERSION, "items": task_items},
    )
