"""Direct, task-local reconciliation after approved artifact changes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .artifacts import requirement_semantic_value, semantic_digest, task_semantic_value
from .model import SCHEMA_VERSION


def changed_ids(
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
    *,
    kind: str,
) -> tuple[list[str], list[str]]:
    semantic_value = requirement_semantic_value if kind == "requirement" else task_semantic_value
    before_by_id = {str(item["id"]): item for item in before}
    after_by_id = {str(item["id"]): item for item in after}
    added = sorted(set(after_by_id) - set(before_by_id))
    changed = sorted(
        item_id
        for item_id in set(before_by_id).intersection(after_by_id)
        if semantic_digest(semantic_value(before_by_id[item_id]))
        != semantic_digest(semantic_value(after_by_id[item_id]))
    )
    return added, changed


def requirement_change_sets(
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
) -> tuple[set[str], set[str]]:
    before_by_id = {str(item["id"]): item for item in before}
    after_by_id = {str(item["id"]): item for item in after}
    before_accepted = {
        item_id
        for item_id, item in before_by_id.items()
        if item.get("disposition") == "accepted"
    }
    after_accepted = {
        item_id
        for item_id, item in after_by_id.items()
        if item.get("disposition") == "accepted"
    }
    scope_added = after_accepted - before_accepted
    scope_removed = before_accepted - after_accepted
    behavior_changes = {
        item_id
        for item_id in before_accepted.intersection(after_accepted)
        if _requirement_behavior_digest(before_by_id[item_id])
        != _requirement_behavior_digest(after_by_id[item_id])
    }.union(scope_removed)
    return scope_added, behavior_changes


def mark_direct_reconciliation(
    artifacts: Mapping[str, Any],
    *,
    stage: str,
    active_item: str | None,
    before_requirements: Sequence[Mapping[str, Any]] = (),
    after_requirements: Sequence[Mapping[str, Any]] = (),
    before_tasks: Sequence[Mapping[str, Any]] = (),
    after_tasks: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], list[str]]:
    """Mark only artifacts directly owned by changed requirements or tasks."""
    reasons_by_artifact: dict[str, set[str]] = {}

    if stage == "analysis":
        scope_added, behavior_changes = requirement_change_sets(
            before_requirements, after_requirements
        )
        if scope_added:
            reasons_by_artifact.setdefault("task-plan", set()).update(
                f"requirement:{item_id}" for item_id in scope_added
            )
        if behavior_changes:
            reasons_by_artifact.setdefault("design", set()).update(
                f"requirement:{item_id}" for item_id in behavior_changes
            )
        before_task_items = list(before_tasks)
        for task in before_task_items:
            related = behavior_changes.intersection(task.get("requirements", []))
            for requirement_id in related:
                for suffix in ("spec", "implementation", "test"):
                    reasons_by_artifact.setdefault(
                        f"{task['id']}-{suffix}", set()
                    ).add(f"requirement:{requirement_id}")

    elif stage == "specification" and active_item is None:
        _, changed = changed_ids(before_tasks, after_tasks, kind="task")
        for task_id in changed:
            for suffix in ("spec", "implementation", "test"):
                reasons_by_artifact.setdefault(f"{task_id}-{suffix}", set()).add(
                    f"task:{task_id}"
                )

    elif stage == "specification" and active_item is not None:
        for suffix in ("implementation", "test"):
            reasons_by_artifact.setdefault(f"{active_item}-{suffix}", set()).add(
                f"specification:{active_item}"
            )

    elif stage == "implementation" and active_item is not None:
        reasons_by_artifact.setdefault(f"{active_item}-test", set()).add(
            f"implementation:{active_item}"
        )

    changed_artifacts: list[str] = []
    items: list[dict[str, Any]] = []
    for raw_item in artifacts["items"]:
        item = dict(raw_item)
        reasons = reasons_by_artifact.get(item["id"])
        if reasons and item.get("approved_revision") is not None:
            item["needs_reconcile"] = sorted(
                set(item.get("needs_reconcile", [])).union(reasons)
            )
            changed_artifacts.append(item["id"])
        items.append(item)
    return {"schema_version": SCHEMA_VERSION, "items": items}, sorted(changed_artifacts)


def _requirement_behavior(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in ("summary", "platform_scope", "change_type")
    }


def _requirement_behavior_digest(item: Mapping[str, Any]) -> str:
    stored = item.get("semantic_sha256")
    if isinstance(stored, str):
        return stored
    return semantic_digest(_requirement_behavior(item))


def clear_reconciliation(
    artifacts: Mapping[str, Any], artifact_id: str
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "items": [
            {**item, "needs_reconcile": []} if item["id"] == artifact_id else dict(item)
            for item in artifacts["items"]
        ],
    }
