"""Task packet construction and validation."""

from __future__ import annotations

import hashlib
from typing import Any

from .artifacts import artifact_identity, result_schema, result_seed
from .model import (
    ID_PATTERNS,
    SCHEMA_VERSION,
    fail_schema,
    now_iso,
    require_mapping,
    require_optional_string,
    require_string,
    require_string_list,
)


def build_work(
    *,
    work_id: str,
    stage: str,
    active_item: str | None,
    goal: str,
    inputs: list[str],
    depends_on: list[str],
    sources: list[str],
    stage_guide: dict[str, Any],
    constraints: list[str],
    decision_content: str,
    target_platform: str,
    facts: dict[str, Any] | None = None,
    repository_context: dict[str, Any] | None = None,
    predecessor: str | None = None,
    feedback: str | None = None,
) -> dict[str, Any]:
    artifact_id, artifact_type, output = artifact_identity(stage, active_item)
    work = {
        "schema_version": SCHEMA_VERSION,
        "work_id": work_id,
        "status": "active",
        "stage": stage,
        "active_item": active_item,
        "goal": goal,
        "target_platform": target_platform,
        "artifact": {
            "id": artifact_id,
            "type": artifact_type,
            "output": output,
        },
        "inputs": inputs,
        "depends_on": depends_on,
        "sources": sources,
        "decision_context": build_decision_context(decision_content),
        "draft_output": f".aiwf/work/{work_id}/artifact.md",
        "result_output": f".aiwf/work/{work_id}/result.json",
        "result_schema": result_schema(stage, active_item),
        "result_seed": result_seed(stage, active_item),
        "stage_guide": dict(stage_guide),
        "constraints": constraints,
        "facts": dict(facts or {}),
        "predecessor": predecessor,
        "feedback": feedback,
        "created_at": now_iso(),
    }
    if repository_context is not None:
        work["repository_context"] = repository_context
    validate_work(work)
    return work


def build_decision_context(content: str) -> dict[str, str]:
    return {
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content": content,
    }


def validate_work(value: Any) -> dict[str, Any]:
    document = "work.json"
    work = require_mapping(value, document)
    if work.get("schema_version") != SCHEMA_VERSION:
        fail_schema(document, "unsupported schema_version")
    work_id = require_string(work.get("work_id"), document, "work_id")
    if not ID_PATTERNS["work"].fullmatch(work_id):
        fail_schema(document, f"invalid work_id '{work_id}'")
    if work.get("status") not in {"active", "blocked", "submitted", "abandoned"}:
        fail_schema(document, f"invalid status '{work.get('status')}'")
    require_string(work.get("stage"), document, "stage")
    require_optional_string(work.get("active_item"), document, "active_item")
    require_string(work.get("goal"), document, "goal")
    require_string(work.get("target_platform"), document, "target_platform")
    artifact = require_mapping(work.get("artifact"), document)
    for field_name in ("id", "type", "output"):
        require_string(artifact.get(field_name), document, f"artifact.{field_name}")
    require_string_list(work.get("inputs"), document, "inputs")
    require_string_list(work.get("depends_on"), document, "depends_on")
    require_string_list(work.get("sources"), document, "sources")
    for field_name in (
        "draft_output",
        "result_output",
        "created_at",
    ):
        require_string(work.get(field_name), document, field_name)
    decision_context = require_mapping(work.get("decision_context"), document)
    decision_content = require_string(
        decision_context.get("content"), document, "decision_context.content", empty=True
    )
    decision_sha256 = require_string(
        decision_context.get("sha256"), document, "decision_context.sha256"
    )
    if hashlib.sha256(decision_content.encode("utf-8")).hexdigest() != decision_sha256:
        fail_schema(document, "decision_context sha256 does not match its content")
    require_mapping(work.get("result_schema"), document)
    require_mapping(work.get("result_seed"), document)
    stage_guide = require_mapping(work.get("stage_guide"), document)
    if set(stage_guide) != {"id", "version", "source", "sha256", "instructions"}:
        fail_schema(document, "stage_guide has unsupported fields")
    guide_id = require_string(stage_guide.get("id"), document, "stage_guide.id")
    if guide_id != work["stage"]:
        fail_schema(document, "stage_guide.id must match work stage")
    if type(stage_guide.get("version")) is not int or stage_guide["version"] < 1:
        fail_schema(document, "stage_guide.version must be a positive integer")
    require_string(stage_guide.get("source"), document, "stage_guide.source")
    guide_content = require_string(
        stage_guide.get("instructions"), document, "stage_guide.instructions"
    )
    guide_sha256 = require_string(stage_guide.get("sha256"), document, "stage_guide.sha256")
    if hashlib.sha256(guide_content.encode("utf-8")).hexdigest() != guide_sha256:
        fail_schema(document, "stage_guide sha256 does not match its instructions")
    require_string_list(work.get("constraints"), document, "constraints")
    require_mapping(work.get("facts"), document)
    if "repository_context" in work:
        repository = require_mapping(work.get("repository_context"), document)
        require_string(repository.get("root"), document, "repository_context.root")
        if set(repository) != {"root"}:
            fail_schema(document, "repository_context only supports the root field")
    require_optional_string(work.get("predecessor"), document, "predecessor")
    require_optional_string(work.get("feedback"), document, "feedback")
    return work
