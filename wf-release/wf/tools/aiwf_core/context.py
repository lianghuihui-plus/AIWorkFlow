"""Task packet construction and validation."""

from __future__ import annotations

import hashlib
import re
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
    memory_content: str,
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
        "memory_context": {
            "sha256": hashlib.sha256(memory_content.encode("utf-8")).hexdigest(),
            "content": memory_content,
        },
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
    memory_context = require_mapping(work.get("memory_context"), document)
    memory_content = require_string(
        memory_context.get("content"), document, "memory_context.content", empty=True
    )
    memory_sha256 = require_string(
        memory_context.get("sha256"), document, "memory_context.sha256"
    )
    if hashlib.sha256(memory_content.encode("utf-8")).hexdigest() != memory_sha256:
        fail_schema(document, "memory_context sha256 does not match its content")
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
        if repository.get("type") not in {"git", "directory"}:
            fail_schema(document, "repository_context.type must be git or directory")
        require_string(repository.get("path"), document, "repository_context.path")
        require_string(repository.get("root"), document, "repository_context.root")
        git_root = repository.get("git_root")
        if git_root is not None and not isinstance(git_root, str):
            fail_schema(document, "repository_context.git_root must be a string or null")
        require_string(
            repository.get("scope_prefix"),
            document,
            "repository_context.scope_prefix",
            empty=True,
        )
        head = repository.get("head")
        if head is not None and not isinstance(head, str):
            fail_schema(document, "repository_context.head must be a string or null")
        require_string_list(
            repository.get("status_lines"),
            document,
            "repository_context.status_lines",
        )
        if repository.get("verification_level") not in {"git_delta", "limited"}:
            fail_schema(
                document,
                "repository_context.verification_level must be git_delta or limited",
            )
        fingerprints = require_mapping(
            repository.get("status_fingerprints"),
            document,
        )
        for relative_path, raw_fingerprint in fingerprints.items():
            if not isinstance(relative_path, str) or not relative_path:
                fail_schema(document, "repository fingerprint paths must be non-empty strings")
            fingerprint = require_mapping(raw_fingerprint, document)
            require_string(
                fingerprint.get("status"),
                document,
                "repository_context.status_fingerprints.status",
            )
            digest = fingerprint.get("sha256")
            if digest is not None and (
                not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                fail_schema(document, "repository fingerprint sha256 must be a digest or null")
        require_string_list(
            repository.get("carried_changes"), document, "repository_context.carried_changes"
        )
        pause_checkpoint = repository.get("pause_checkpoint")
        if pause_checkpoint is not None and not isinstance(pause_checkpoint, dict):
            fail_schema(document, "repository_context.pause_checkpoint must be an object or null")
    require_optional_string(work.get("predecessor"), document, "predecessor")
    require_optional_string(work.get("feedback"), document, "feedback")
    return work


def copy_successor_work(
    previous: dict[str, Any],
    *,
    work_id: str,
    feedback: str | None = None,
    memory_content: str | None = None,
    repository_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_work(
        work_id=work_id,
        stage=previous["stage"],
        active_item=previous["active_item"],
        goal=previous["goal"],
        inputs=list(previous["inputs"]),
        depends_on=list(previous["depends_on"]),
        sources=list(previous["sources"]),
        stage_guide=dict(previous["stage_guide"]),
        constraints=list(previous["constraints"]),
        memory_content=(
            memory_content
            if memory_content is not None
            else previous["memory_context"]["content"]
        ),
        target_platform=previous["target_platform"],
        facts=dict(previous["facts"]),
        repository_context=(
            repository_context
            if repository_context is not None
            else dict(previous["repository_context"])
            if "repository_context" in previous
            else None
        ),
        predecessor=previous["work_id"],
        feedback=feedback if feedback is not None else previous.get("feedback"),
    )
