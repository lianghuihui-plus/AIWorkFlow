"""Derive task-local progress without a global execution slot."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .model import AIWorkflowError

TASK_STAGES = ("specification", "implementation", "testing")
_STAGE_SUFFIX = {
    "specification": "-spec",
    "implementation": "-implementation",
    "testing": "-test",
}


def derive_task_flow(
    *,
    requirements: Mapping[str, Any],
    tasks: Mapping[str, Any],
    artifacts: Mapping[str, Any],
    works: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    artifact_by_id = {item["id"]: item for item in artifacts["items"]}
    work_by_artifact = {
        item["artifact"]["id"]: item
        for item in works
        if item.get("status") in {"active", "blocked"}
    }
    stage_progress = {stage: "not_started" for stage in (
        "analysis", "design", "specification", "implementation", "testing"
    )}

    analysis = artifact_by_id.get("analysis")
    stage_progress["analysis"] = _project_artifact_progress(analysis, work_by_artifact.get("analysis"))
    if not _usable(analysis):
        ready = _pending_work("analysis", None, analysis, work_by_artifact)
        return _projection([ready] if ready else [], [], stage_progress, False, [])

    accepted = [
        item for item in requirements["items"] if item["disposition"] == "accepted"
    ]
    has_pending_downstream = any(
        item.get("status") == "active" for item in tasks["items"]
    ) or bool(works) or any(
        item["id"] != "analysis"
        and (item.get("needs_reconcile") or item.get("status") != "approved")
        for item in artifacts["items"]
    )
    if not accepted and not has_pending_downstream:
        stage_progress["analysis"] = "completed"
        return _projection([], [], stage_progress, True, [])
    stage_progress["analysis"] = "completed"

    design = artifact_by_id.get("design")
    stage_progress["design"] = _project_artifact_progress(design, work_by_artifact.get("design"))
    if not _usable(design):
        ready = _pending_work("design", None, design, work_by_artifact)
        return _projection([ready] if ready else [], [], stage_progress, False, [])
    stage_progress["design"] = "completed" if not design.get("needs_reconcile") else "needs_reconcile"

    task_plan = artifact_by_id.get("task-plan")
    stage_progress["specification"] = _project_artifact_progress(
        task_plan, work_by_artifact.get("task-plan")
    )
    if task_plan is None or task_plan.get("approved_revision") is None:
        ready = _pending_work("specification", None, task_plan, work_by_artifact)
        return _projection([ready] if ready else [], [], stage_progress, False, [])

    progress = _task_progress(tasks["items"], artifact_by_id, work_by_artifact)
    required_work = [
        dict(item["ready_work"])
        for item in progress
        if item.get("ready_work") is not None
        and item["ready_work"]["stage"] != "testing"
    ]
    optional_work = [
        dict(item["ready_work"])
        for item in progress
        if item.get("ready_work") is not None
        and item["ready_work"]["stage"] == "testing"
    ]
    required_work.sort(key=_work_priority)
    optional_work.sort(key=_work_priority)
    project_work: list[dict[str, Any]] = []
    if design.get("needs_reconcile"):
        design_work = _pending_work("design", None, design, work_by_artifact)
        if design_work is not None:
            project_work.append(design_work)
    if task_plan.get("needs_reconcile"):
        plan_work = _pending_work(
            "specification", None, task_plan, work_by_artifact
        )
        if plan_work is not None:
            project_work.append(plan_work)
    required_work = [*project_work, *required_work]
    for stage in TASK_STAGES:
        task_stage_progress = _aggregate_task_stage(progress, stage)
        if stage != "specification" or not task_plan.get("needs_reconcile"):
            stage_progress[stage] = task_stage_progress
    implementation_complete = (
        not design.get("needs_reconcile")
        and not task_plan.get("needs_reconcile")
        and bool(progress)
        and all(
        item["implementation_status"] == "completed" for item in progress
        )
    )
    return _projection(
        [*required_work, *optional_work],
        progress,
        stage_progress,
        implementation_complete,
        optional_work,
    )


def derive_task_progress(
    *, tasks: Mapping[str, Any], artifacts: Mapping[str, Any]
) -> list[dict[str, Any]]:
    return _task_progress(
        tasks["items"],
        {item["id"]: item for item in artifacts["items"]},
        {},
    )


def select_work(projection: Mapping[str, Any], task_id: str | None) -> dict[str, Any]:
    if task_id is None:
        recommended = projection.get("recommended_work")
        if recommended is None:
            raise AIWorkflowError(
                code="workflow_completed" if projection.get("completed") else "no_pending_task",
                message=(
                    "Required implementation work is complete."
                    if projection.get("completed")
                    else "No work item is currently ready."
                ),
                exit_code=6,
            )
        return dict(recommended)

    progress_by_id = {item["id"]: item for item in projection["task_progress"]}
    progress = progress_by_id.get(task_id)
    if progress is None:
        if not projection["task_progress"]:
            raise AIWorkflowError(
                code="task_plan_required",
                message="Approve the task plan before selecting an individual task.",
                exit_code=6,
                details={"id": task_id},
            )
        raise AIWorkflowError(
            code="unknown_task_id",
            message="Requested task is not active in the task plan.",
            exit_code=4,
            details={"id": task_id},
        )
    if progress["next_stage"] == "implementation" and progress["blocked_by"]:
        raise AIWorkflowError(
            code="task_dependency_blocked",
            message="Task implementation requires approved implementations for its declared dependencies.",
            exit_code=6,
            details={"id": task_id, "blocked_by": list(progress["blocked_by"])},
        )
    ready = progress.get("ready_work")
    if ready is None:
        raise AIWorkflowError(
            code="task_not_ready",
            message="Requested task has no pending work.",
            exit_code=6,
            details={"id": task_id, "status": progress["status"]},
        )
    return dict(ready)


def artifact_id_for(stage: str, task_id: str) -> str:
    return f"{task_id}{_STAGE_SUFFIX[stage]}"


def _task_progress(
    tasks: Sequence[Mapping[str, Any]],
    artifact_by_id: Mapping[str, Mapping[str, Any]],
    work_by_artifact: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    active_tasks = [item for item in tasks if item["status"] == "active"]
    completed_implementations = {
        task["id"]
        for task in active_tasks
        if _current(artifact_by_id.get(artifact_id_for("implementation", task["id"])))
    }
    progress: list[dict[str, Any]] = []
    for task in sorted(active_tasks, key=lambda item: item["id"]):
        task_artifacts = {
            stage: artifact_by_id.get(artifact_id_for(stage, task["id"]))
            for stage in TASK_STAGES
        }
        spec_current = _current(task_artifacts["specification"])
        impl_current = _current(task_artifacts["implementation"])
        next_stage = "specification" if not spec_current else "implementation" if not impl_current else "testing"
        blocked_by = (
            sorted(set(task["depends_on"]) - completed_implementations)
            if next_stage == "implementation"
            else []
        )
        artifact_id = artifact_id_for(next_stage, task["id"])
        ready_work = _pending_work(next_stage, task["id"], task_artifacts[next_stage], work_by_artifact)
        if next_stage == "implementation" and blocked_by:
            ready_work = None
        if ready_work is not None:
            ready_work = {**ready_work, "title": task.get("title", task["id"])}
        test_result = task_artifacts["testing"].get("test_status") if task_artifacts["testing"] else None
        implementation_status = "completed" if spec_current and impl_current else "needs_reconcile" if (
            (task_artifacts["specification"] and task_artifacts["specification"].get("needs_reconcile"))
            or (task_artifacts["implementation"] and task_artifacts["implementation"].get("needs_reconcile"))
        ) else "pending"
        status = (
            "completed" if implementation_status == "completed"
            else "dependency_blocked" if blocked_by
            else _local_status(task_artifacts[next_stage], work_by_artifact.get(artifact_id), next_stage)
        )
        progress.append(
            {
                "id": task["id"],
                "title": task.get("title", task["id"]),
                "status": status,
                "implementation_status": implementation_status,
                "test_status": test_result or ("not_run" if task_artifacts["testing"] is None else _artifact_status(task_artifacts["testing"])),
                "next_stage": next_stage,
                "blocked_by": blocked_by,
                "artifacts": {stage: _artifact_status(task_artifacts[stage]) for stage in TASK_STAGES},
                "ready_work": ready_work,
            }
        )
    return progress


def _pending_work(
    stage: str,
    active_item: str | None,
    artifact: Mapping[str, Any] | None,
    work_by_artifact: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    artifact_id = (
        {"analysis": "analysis", "design": "design", "specification": "task-plan"}[stage]
        if active_item is None
        else artifact_id_for(stage, active_item)
    )
    existing_work = work_by_artifact.get(artifact_id)
    if existing_work is not None:
        return {
            "stage": stage,
            "active_item": active_item,
            "artifact_id": artifact_id,
            "kind": "draft",
            "work_id": existing_work["work_id"],
            "work_status": existing_work["status"],
        }
    if artifact is not None and artifact["status"] == "review":
        return None
    if artifact is not None and _current(artifact):
        if stage != "testing" or artifact.get("test_status") in {"completed", "failed", "skipped"}:
            return None
    return {
        "stage": stage,
        "active_item": active_item,
        "artifact_id": artifact_id,
        "kind": "revision" if artifact is not None else "new",
    }


def _projection(
    ready_work: list[dict[str, Any]],
    task_progress: list[dict[str, Any]],
    stage_progress: dict[str, str],
    completed: bool,
    optional_work: list[dict[str, Any]],
) -> dict[str, Any]:
    required = [item for item in ready_work if item["stage"] != "testing"]
    return {
        "completed": completed,
        "ready_work": ready_work,
        "optional_work": optional_work,
        "recommended_work": dict(required[0]) if required else None,
        "task_progress": task_progress,
        "stage_progress": stage_progress,
    }


def _work_priority(work: Mapping[str, Any]) -> tuple[int, str]:
    return ({"implementation": 0, "specification": 1, "testing": 2}[work["stage"]], str(work.get("active_item") or ""))


def _usable(artifact: Mapping[str, Any] | None) -> bool:
    return bool(artifact is not None and artifact.get("approved_revision") is not None)


def _current(artifact: Mapping[str, Any] | None) -> bool:
    return bool(_usable(artifact) and not artifact.get("needs_reconcile"))


def _artifact_status(artifact: Mapping[str, Any] | None) -> str:
    if artifact is None:
        return "not_started"
    if artifact.get("needs_reconcile"):
        return "needs_reconcile"
    return str(artifact["status"])


def _project_artifact_progress(
    artifact: Mapping[str, Any] | None,
    work: Mapping[str, Any] | None,
) -> str:
    if work is not None:
        return "blocked" if work.get("status") == "blocked" else "in_progress"
    if artifact is None:
        return "ready"
    if artifact.get("needs_reconcile"):
        return "needs_reconcile"
    if artifact.get("approved_revision") is not None:
        return "completed"
    return str(artifact["status"])


def _local_status(
    artifact: Mapping[str, Any] | None,
    work: Mapping[str, Any] | None,
    stage: str,
) -> str:
    if work is not None:
        return "blocked" if work.get("status") == "blocked" else stage
    if artifact is not None and artifact.get("needs_reconcile"):
        return "needs_reconcile"
    if artifact is not None and artifact.get("approved_revision") is None:
        return str(artifact["status"])
    return stage


def _aggregate_task_stage(progress: Sequence[Mapping[str, Any]], stage: str) -> str:
    if not progress:
        return "not_started"
    statuses = [item["artifacts"][stage] for item in progress]
    if stage == "testing":
        test_statuses = [item["test_status"] for item in progress]
        if "failed" in test_statuses:
            return "failed"
        if all(status == "completed" for status in test_statuses):
            return "completed"
        if "skipped" in test_statuses and all(
            status in {"completed", "skipped"} for status in test_statuses
        ):
            return "skipped"
        if any(status != "not_started" for status in statuses):
            return "in_progress"
        return "optional"
    if all(status == "approved" for status in statuses):
        return "completed"
    if "needs_reconcile" in statuses:
        return "needs_reconcile"
    if "review" in statuses:
        return "review"
    if "changes_requested" in statuses:
        return "changes_requested"
    if any(status != "not_started" for status in statuses):
        return "in_progress"
    return "ready" if stage == "specification" else "not_started"
