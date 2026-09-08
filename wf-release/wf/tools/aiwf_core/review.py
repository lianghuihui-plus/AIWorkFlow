"""Review transitions, memory application, and stage progression."""

from __future__ import annotations

from typing import Any

from .artifacts import find_artifact
from .model import AIWorkflowError, SCHEMA_VERSION, next_id, now_iso


def apply_memory_delta(
    memory: dict[str, Any],
    delta: list[dict[str, Any]],
    *,
    source: str,
) -> dict[str, Any]:
    items = {item["id"]: dict(item) for item in memory["items"]}
    known_ids = list(items)
    touched: set[str] = set()
    timestamp = now_iso()

    for operation in delta:
        action = operation["operation"]
        target_id = operation.get("target_id")
        if action == "add":
            target_id = next_id("memory", known_ids)
            known_ids.append(target_id)
            items[target_id] = {
                "id": target_id,
                "type": operation["type"],
                "content": operation["content"],
                "evidence": list(operation["evidence"]),
                "rationale": operation["rationale"],
                "validation": operation["validation"],
                "source": source,
                "status": "active",
                "updated_at": timestamp,
            }
            touched.add(target_id)
            continue
        if target_id not in items:
            raise AIWorkflowError(
                code="unknown_memory_id",
                message="Memory update references an unknown entry.",
                exit_code=4,
                details={"id": target_id},
            )
        if target_id in touched:
            raise AIWorkflowError(
                code="memory_delta_conflict",
                message="A memory entry can be changed only once per revision.",
                exit_code=4,
                details={"id": target_id},
            )
        touched.add(target_id)
        current = items[target_id]
        if action == "update":
            current.update(
                {
                    "type": operation["type"],
                    "content": operation["content"],
                    "evidence": list(operation["evidence"]),
                    "rationale": operation["rationale"],
                    "validation": operation["validation"],
                    "source": source,
                    "status": "active",
                    "updated_at": timestamp,
                }
            )
        else:
            current.update(
                {
                    "source": source,
                    "status": "retracted",
                    "updated_at": timestamp,
                }
            )

    return {"schema_version": SCHEMA_VERSION, "items": sorted(items.values(), key=lambda item: item["id"])}


def approve_indexes(
    *,
    stage: str,
    revision: int,
    active_item: str | None,
    requirements: dict[str, Any],
    tasks: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    requirement_items = [dict(item) for item in requirements["items"]]
    task_items = [dict(item) for item in tasks["items"]]
    if stage == "analysis":
        for item in requirement_items:
            if item["origin_revision"] == revision and item["disposition"] == "proposed":
                item["disposition"] = "accepted"
    elif stage == "specification" and active_item is None:
        for item in task_items:
            if item["origin_revision"] == revision and item["status"] == "proposed":
                item["status"] = "planned"
    elif active_item is not None:
        target = next((item for item in task_items if item["id"] == active_item), None)
        if target is None:
            raise AIWorkflowError(
                code="unknown_task_id",
                message="Artifact review references an unknown task.",
                exit_code=4,
                details={"id": active_item},
            )
        status_by_stage = {
            "specification": "in_progress",
            "implementation": "implemented",
            "testing": "tested",
        }
        target["status"] = status_by_stage[stage]
    return (
        {"schema_version": SCHEMA_VERSION, "items": requirement_items},
        {"schema_version": SCHEMA_VERSION, "items": task_items},
    )


def advance_after_approval(
    state: dict[str, Any],
    artifacts: dict[str, Any],
    requirements: dict[str, Any],
    tasks: dict[str, Any],
    *,
    stage: str,
    reviewed_ref: str,
) -> tuple[dict[str, Any], bool]:
    updated = dict(state)
    updated["pending_reviews"] = [
        reference for reference in state["pending_reviews"] if reference != reviewed_ref
    ]
    updated["active_item"] = None
    updated["active_work"] = None
    updated["active_work_sha256"] = None
    previous_stage = state["current_stage"]

    if updated["pending_reviews"]:
        updated["mode"] = "review"
        updated["updated_at"] = now_iso()
        return updated, False

    next_stage = stage
    if stage == "analysis":
        next_stage = (
            "design"
            if any(item["disposition"] == "accepted" for item in requirements["items"])
            else "completed"
        )
    elif stage == "design":
        next_stage = "specification"
    elif stage == "specification":
        if reviewed_ref.startswith("task-plan@"):
            next_stage = "specification"
        elif _all_task_artifacts_approved(tasks, artifacts, "specification"):
            next_stage = "implementation"
    elif stage == "implementation" and _all_task_artifacts_approved(tasks, artifacts, "implementation"):
        next_stage = "testing"
    elif stage == "testing" and _all_task_artifacts_approved(tasks, artifacts, "testing"):
        next_stage = "completed"

    updated["current_stage"] = next_stage
    updated["mode"] = "ready"
    updated["blocking_questions"] = []
    updated["updated_at"] = now_iso()
    return updated, next_stage != previous_stage


def _all_task_artifacts_approved(
    tasks: dict[str, Any],
    artifacts: dict[str, Any],
    stage: str,
) -> bool:
    suffix_by_stage = {
        "specification": "-spec",
        "implementation": "-implementation",
        "testing": "-test",
    }
    eligible = [item for item in tasks["items"] if item["status"] != "withdrawn"]
    if not eligible:
        return False
    suffix = suffix_by_stage[stage]
    for task in eligible:
        artifact = find_artifact(artifacts, f"{task['id']}{suffix}")
        if artifact is None or artifact["status"] != "approved":
            return False
    return True
