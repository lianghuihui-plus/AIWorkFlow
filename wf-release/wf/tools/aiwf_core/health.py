"""Semantic health rules for the current effective workflow projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

SEMANTIC_ARTIFACT_STATUSES = frozenset({"approved", "review"})
NON_PARTICIPATING_TASK_STATUSES = frozenset({"withdrawn"})


def semantic_health_issues(
    *,
    requirements: Mapping[str, Any],
    tasks: Mapping[str, Any],
    artifacts: Mapping[str, Any],
    artifact_results: Mapping[str, Mapping[str, Any]],
    drifted_artifact_ids: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Validate relations that belong to the current, non-stale artifact graph."""
    issues: list[dict[str, Any]] = []
    drifted = set(drifted_artifact_ids)
    artifact_items = artifacts["items"]
    requirement_items = requirements["items"]
    task_items = tasks["items"]
    requirement_ids = {item["id"] for item in requirement_items}
    accepted_requirement_ids = {
        item["id"]
        for item in requirement_items
        if item["disposition"] == "accepted"
    }

    design = _find_artifact(artifact_items, "design")
    if _participates_in_semantic_projection(design, drifted):
        design_requirement_ids = set(artifact_results["design"].get("requirements", []))
        unknown = sorted(design_requirement_ids - accepted_requirement_ids)
        missing = sorted(accepted_requirement_ids - design_requirement_ids)
        if unknown or missing:
            issues.append(
                {
                    "level": "error",
                    "type": "design_requirement_mismatch",
                    "message": "Technical design coverage does not match accepted requirements.",
                    "details": {"unknown": unknown, "missing": missing},
                }
            )

    task_plan = _find_artifact(artifact_items, "task-plan")
    if not _participates_in_semantic_projection(task_plan, drifted):
        return issues

    known_task_ids = {item["id"] for item in task_items}
    covered_requirement_ids: set[str] = set()
    for task in task_items:
        if task["status"] in NON_PARTICIPATING_TASK_STATUSES:
            continue
        task_requirement_ids = set(task["requirements"])
        unknown_requirements = sorted(task_requirement_ids - requirement_ids)
        unavailable_requirements = sorted(
            (task_requirement_ids & requirement_ids) - accepted_requirement_ids
        )
        unknown_dependencies = sorted(set(task["depends_on"]) - known_task_ids)
        covered_requirement_ids.update(task_requirement_ids & accepted_requirement_ids)
        if unknown_requirements or unavailable_requirements or unknown_dependencies:
            issues.append(
                {
                    "level": "error",
                    "type": "task_reference_mismatch",
                    "message": "Task index contains unresolved references.",
                    "details": {
                        "task_id": task["id"],
                        "requirements": unknown_requirements,
                        "unavailable_requirements": unavailable_requirements,
                        "dependencies": unknown_dependencies,
                    },
                }
            )

    uncovered_requirements = sorted(accepted_requirement_ids - covered_requirement_ids)
    if uncovered_requirements:
        issues.append(
            {
                "level": "error",
                "type": "uncovered_requirements",
                "message": "Accepted requirements are not covered by active task-plan tasks.",
                "details": {"ids": uncovered_requirements},
            }
        )
    return issues


def semantic_artifact_ids(artifacts: Mapping[str, Any]) -> set[str]:
    """Return result-bearing artifacts needed by semantic health evaluation."""
    return {
        item["id"]
        for item in artifacts["items"]
        if item["id"] == "design"
        and item["status"] in SEMANTIC_ARTIFACT_STATUSES
    }


def _find_artifact(
    artifacts: Sequence[Mapping[str, Any]], artifact_id: str
) -> Mapping[str, Any] | None:
    return next((item for item in artifacts if item["id"] == artifact_id), None)


def _participates_in_semantic_projection(
    artifact: Mapping[str, Any] | None,
    drifted_artifact_ids: set[str],
) -> bool:
    return bool(
        artifact is not None
        and artifact["status"] in SEMANTIC_ARTIFACT_STATUSES
        and artifact["id"] not in drifted_artifact_ids
    )
