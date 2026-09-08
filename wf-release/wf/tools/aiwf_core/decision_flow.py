"""Pure question, decision-routing, and upstream graph policy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .model import AIWorkflowError


def normalize_question(raw_question: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for field_name in ("question", "reason", "recommendation"):
        value = raw_question.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise AIWorkflowError(
                code="invalid_questions",
                message=f"Question field '{field_name}' must be non-empty.",
                exit_code=4,
            )
        normalized[field_name] = value
    impact = raw_question.get("impact")
    if not isinstance(impact, list) or any(
        not isinstance(item, str) or not item for item in impact
    ):
        raise AIWorkflowError(
            code="invalid_questions",
            message="Question impact must be an array of strings.",
            exit_code=4,
        )
    normalized["impact"] = list(impact)
    supersedes = raw_question.get("supersedes_decisions", [])
    if not isinstance(supersedes, list) or any(
        not isinstance(item, str) or not item for item in supersedes
    ):
        raise AIWorkflowError(
            code="invalid_questions",
            message="Question supersedes_decisions must be an array of decision ids.",
            exit_code=4,
        )
    normalized["supersedes_decisions"] = list(supersedes)
    return normalized


def validate_decision_state(state: Mapping[str, Any], work_id: str) -> None:
    if state["mode"] != "decision" or state["active_work"] != work_id:
        raise AIWorkflowError(
            code="invalid_state_transition",
            message="Only fully answered decision work can be routed.",
            exit_code=6,
            details={"work_id": work_id, "mode": state["mode"]},
        )


def resolved_decisions_for_work(
    work_id: str,
    questions: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> list[dict[str, Any]]:
    resolved_questions = [
        item
        for item in questions["items"]
        if item["work_id"] == work_id and item["status"] == "resolved"
    ]
    decisions_by_id = {item["id"]: item for item in decisions["items"]}
    resolved = [
        {
            "question": question,
            "decision": decisions_by_id.get(question["decision_id"]),
        }
        for question in resolved_questions
    ]
    if not resolved or any(item["decision"] is None for item in resolved):
        raise AIWorkflowError(
            code="invalid_decision_route",
            message="Decision work does not have a complete set of recorded decisions.",
            exit_code=6,
            details={"work_id": work_id},
        )
    return resolved


def decision_feedback(resolved: Sequence[Mapping[str, Any]]) -> str:
    lines = ["Revise this artifact according to the confirmed decisions:"]
    for item in resolved:
        question = item["question"]
        decision = item["decision"]
        lines.append(f"- {question['id']} {question['question']}")
        lines.append(f"  Decision: {decision['decision']}")
    return "\n".join(lines)


def upstream_references(
    work: Mapping[str, Any],
    artifacts: Mapping[str, Any],
) -> set[str]:
    artifacts_by_ref = {
        f"{item['id']}@{item['revision']}": item for item in artifacts["items"]
    }
    upstream: set[str] = set()
    pending = list(work["depends_on"])
    while pending:
        reference = pending.pop()
        if reference in upstream:
            continue
        dependency = artifacts_by_ref.get(reference)
        if dependency is None:
            raise AIWorkflowError(
                code="invalid_decision_route",
                message="Work has an unresolved upstream dependency.",
                exit_code=6,
                details={"reference": reference},
            )
        upstream.add(reference)
        pending.extend(dependency["depends_on"])
    return upstream


def validate_decision_revision_target(
    work: Mapping[str, Any],
    artifact: Mapping[str, Any],
    resolved: Sequence[Mapping[str, Any]],
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    target_ref = f"{artifact['id']}@{artifact['revision']}"
    upstream = upstream_references(work, artifacts)
    impacted_stages = {
        stage for item in resolved for stage in item["question"]["impact"]
    }
    if target_ref not in upstream:
        raise AIWorkflowError(
            code="invalid_decision_route",
            message="Revision target must be an approved upstream dependency of the decision work.",
            exit_code=6,
            details={"target": target_ref, "upstream": sorted(upstream)},
        )
    return {
        "declared_impacts": sorted(impacted_stages),
        "target_stage": artifact["stage"],
        "impact_expanded": artifact["stage"] not in impacted_stages,
    }
