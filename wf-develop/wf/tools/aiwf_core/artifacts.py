"""Artifact paths, result manifests, indexes, and revision semantics."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .model import (
    AIWorkflowError,
    ID_PATTERNS,
    SCHEMA_VERSION,
    SOURCE_KINDS,
    fail_schema,
    next_id,
    require_list,
    require_mapping,
    require_optional_string,
    require_source_list,
    require_string,
    require_string_list,
    validate_task_dependency_graph,
)


def semantic_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_content(payload)


def requirement_semantic_value(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in ("summary", "platform_scope", "change_type", "disposition")
    }


def requirement_content_value(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **requirement_semantic_value(item),
        "title": item.get("title"),
        "sources": item.get("sources", []),
        "scope_reason": item.get("scope_reason"),
    }


def task_semantic_value(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in ("requirements", "depends_on", "status")
    }


def task_content_value(item: Mapping[str, Any]) -> dict[str, Any]:
    return {**task_semantic_value(item), "title": item.get("title")}


def sha256_content(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def semantic_result_hash(result: dict[str, Any]) -> str:
    stage = result["stage"]
    semantic = result_seed_from_record(
        stage,
        result,
    )
    if stage == "analysis" and isinstance(result.get("effective_requirements"), list):
        semantic["requirements"] = sorted(
            (
                {
                    "id": item.get("id"),
                    **requirement_content_value(item),
                    "disposition": (
                        "accepted"
                        if item.get("disposition") == "proposed"
                        else item.get("disposition")
                    ),
                }
                for item in result["effective_requirements"]
            ),
            key=lambda item: str(item["id"]),
        )
    elif (
        stage == "specification"
        and result.get("task_id") is None
        and isinstance(result.get("effective_tasks"), list)
    ):
        semantic["tasks"] = sorted(
            (
                {
                    "id": item.get("id"),
                    **task_content_value(item),
                    "status": "active" if item.get("status") == "proposed" else item.get("status"),
                }
                for item in result["effective_tasks"]
            ),
            key=lambda item: str(item["id"]),
        )
    payload = json.dumps(
        semantic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_content(payload)


def artifact_identity(stage: str, active_item: str | None) -> tuple[str, str, str]:
    if stage == "analysis":
        return "analysis", "analysis", "artifacts/analysis.md"
    if stage == "design":
        return "design", "design", "artifacts/design.md"
    if stage == "specification" and active_item is None:
        return "task-plan", "task_plan", "artifacts/task-plan.md"
    if active_item is None or not ID_PATTERNS["task"].fullmatch(active_item):
        raise AIWorkflowError(
            code="invalid_active_item",
            message=f"Stage '{stage}' requires a task id.",
            exit_code=4,
        )
    if stage == "specification":
        return f"{active_item}-spec", "specification", f"artifacts/specs/{active_item}.md"
    if stage == "implementation":
        return (
            f"{active_item}-implementation",
            "implementation_report",
            f"artifacts/reports/{active_item}.md",
        )
    if stage == "testing":
        return f"{active_item}-test", "test_report", f"artifacts/tests/{active_item}.md"
    raise AIWorkflowError(
        code="invalid_stage",
        message=f"Stage '{stage}' cannot produce an artifact.",
        exit_code=4,
    )


def artifact_revision_paths(artifact_id: str, revision: int) -> dict[str, str]:
    return {
        "snapshot_path": f".aiwf/history/{artifact_id}/{revision}.md",
        "result_path": f".aiwf/results/{artifact_id}/{revision}.json",
        "work_path": f".aiwf/history/{artifact_id}/{revision}.work.json",
    }


def result_schema(stage: str, active_item: str | None = None) -> dict[str, Any]:
    string = {"type": "string", "minLength": 1}
    string_array = {"type": "array", "items": string, "uniqueItems": True}
    source_array = {
        "type": "array",
        "minItems": 1,
        "items": {
            "type": "object",
            "required": ["kind", "ref"],
            "properties": {
                "kind": {"enum": list(SOURCE_KINDS)},
                "ref": string,
            },
            "additionalProperties": False,
        },
    }
    properties: dict[str, Any] = {
        "schema_version": {"const": SCHEMA_VERSION},
        "stage": {"const": stage},
    }
    required = ["schema_version", "stage"]
    if stage == "analysis":
        properties["target_platform"] = string
        properties["requirements"] = {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "title",
                    "summary",
                    "sources",
                    "platform_scope",
                    "change_type",
                    "scope_reason",
                    "disposition",
                ],
                "properties": {
                    "id": {"type": ["string", "null"], "pattern": r"^REQ-\d{3,}$"},
                    "change_kind": {"enum": ["behavior", "presentation"]},
                    "title": string,
                    "summary": string,
                    "sources": source_array,
                    "platform_scope": {"enum": ["target", "cross_platform", "other"]},
                    "change_type": {"enum": ["new", "modify", "reuse"]},
                    "scope_reason": string,
                    "disposition": {"enum": ["proposed", "deferred", "excluded"]},
                },
                "additionalProperties": False,
            },
        }
        properties["withdrawn_requirements"] = string_array
        required.extend(("target_platform", "requirements"))
    elif stage == "design":
        properties["requirements"] = string_array
        properties["design_mode"] = {"enum": ["anchored", "greenfield"]}
        properties["greenfield_reason"] = {"type": ["string", "null"]}
        properties["code_evidence"] = {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path", "symbol", "purpose"],
                "properties": {
                    "path": string,
                    "symbol": string,
                    "purpose": string,
                },
                "additionalProperties": False,
            },
        }
        required.extend(
            ("requirements", "design_mode", "greenfield_reason", "code_evidence")
        )
    elif stage == "specification" and active_item is None:
        properties["tasks"] = {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["key", "title", "requirements", "depends_on"],
                "properties": {
                    "key": string,
                    "id": {"type": ["string", "null"], "pattern": r"^T-\d{3,}$"},
                    "title": string,
                    "requirements": {**string_array, "minItems": 1},
                    "depends_on": string_array,
                },
                "additionalProperties": False,
            },
        }
        properties["withdrawn_tasks"] = string_array
        required.append("tasks")
    elif stage == "specification":
        properties["task_id"] = {"type": "string", "pattern": r"^T-\d{3,}$"}
        properties["acceptance_criteria"] = {**string_array, "minItems": 1}
        required.extend(("task_id", "acceptance_criteria"))
    elif stage == "implementation":
        properties.update(
            {
                "task_id": {"type": "string", "pattern": r"^T-\d{3,}$"},
                "summary": string,
                "changed_files": string_array,
                "acceptance_results": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["criterion", "status", "evidence"],
                        "properties": {
                            "criterion": string,
                            "status": {"enum": ["passed", "failed"]},
                            "evidence": string,
                        },
                        "additionalProperties": False,
                    },
                },
                "validation": {**string_array, "minItems": 1},
                "risks": string_array,
            }
        )
        required.extend(
            ("task_id", "summary", "changed_files", "acceptance_results", "validation", "risks")
        )
    elif stage == "testing":
        properties.update(
            {
                "task_id": {"type": "string", "pattern": r"^T-\d{3,}$"},
                "status": {"enum": ["completed", "failed", "skipped"]},
                "test_files": string_array,
                "command": {"type": ["string", "null"]},
                "summary": string,
                "uncovered": string_array,
            }
        )
        required.extend(("task_id", "status", "test_files", "command", "summary", "uncovered"))
    else:
        raise AIWorkflowError(
            code="invalid_stage",
            message=f"Stage '{stage}' has no result schema.",
            exit_code=4,
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": False,
    }


def result_seed(stage: str, active_item: str | None) -> dict[str, Any]:
    seed: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "stage": stage,
    }
    if stage == "analysis":
        seed["target_platform"] = ""
        seed["requirements"] = []
        seed["withdrawn_requirements"] = []
    elif stage == "design":
        seed["requirements"] = []
        seed["design_mode"] = "anchored"
        seed["greenfield_reason"] = None
        seed["code_evidence"] = []
    elif stage == "specification" and active_item is None:
        seed["tasks"] = []
        seed["withdrawn_tasks"] = []
    elif stage == "specification":
        seed["task_id"] = active_item
        seed["acceptance_criteria"] = []
    elif stage == "implementation":
        seed.update(
            {
                "task_id": active_item,
                "summary": "",
                "changed_files": [],
                "acceptance_results": [],
                "validation": [],
                "risks": [],
            }
        )
    elif stage == "testing":
        seed.update(
            {
                "task_id": active_item,
                "status": "completed",
                "test_files": [],
                "command": None,
                "summary": "",
                "uncovered": [],
            }
        )
    else:
        result_schema(stage, active_item)
    return seed


def result_seed_from_record(
    stage: str,
    record: dict[str, Any],
    *,
    active_item: str | None = None,
) -> dict[str, Any]:
    resolved_active_item = active_item if active_item is not None else record.get("task_id")
    seed = _project_schema_fields(record, result_schema(stage, resolved_active_item))
    if stage == "analysis":
        seed["requirements"] = []
        seed["withdrawn_requirements"] = []
    elif stage == "specification" and resolved_active_item is None:
        seed["tasks"] = []
        seed["withdrawn_tasks"] = []
    return seed


def validate_result_manifest(stage: str, value: Any, *, active_item: str | None) -> dict[str, Any]:
    document = f"{stage} result manifest"
    data = dict(require_mapping(value, document))
    _validate_closed_shape(data, result_schema(stage, active_item), document, path="$")
    if data.get("schema_version") != SCHEMA_VERSION:
        fail_schema(document, "unsupported schema_version")
    if data.get("stage") != stage:
        fail_schema(document, f"stage must be '{stage}'")
    if stage == "analysis":
        require_string(data.get("target_platform"), document, "target_platform")
        _validate_requirement_results(data.get("requirements"), document)
        require_string_list(
            data.get("withdrawn_requirements", []),
            document,
            "withdrawn_requirements",
        )
    elif stage == "design":
        require_string_list(data.get("requirements"), document, "requirements")
        if data.get("design_mode") not in {"anchored", "greenfield"}:
            fail_schema(document, "design_mode must be anchored or greenfield")
        greenfield_reason = require_optional_string(
            data.get("greenfield_reason"), document, "greenfield_reason"
        )
        evidence = _validate_design_evidence(data.get("code_evidence"), document)
        if data["design_mode"] == "anchored" and not evidence:
            fail_schema(document, "anchored design requires existing code_evidence")
        if data["design_mode"] == "greenfield" and not greenfield_reason:
            fail_schema(document, "greenfield design requires greenfield_reason")
    elif stage == "specification" and active_item is None:
        _validate_task_results(data.get("tasks"), document)
        require_string_list(data.get("withdrawn_tasks", []), document, "withdrawn_tasks")
    elif stage == "specification":
        _validate_task_id(data.get("task_id"), active_item, document)
        require_string_list(data.get("acceptance_criteria"), document, "acceptance_criteria")
    elif stage == "implementation":
        _validate_task_id(data.get("task_id"), active_item, document)
        require_string(data.get("summary"), document, "summary")
        require_string_list(data.get("changed_files"), document, "changed_files")
        acceptance = require_list(data.get("acceptance_results"), document, "acceptance_results")
        if not acceptance:
            fail_schema(document, "acceptance_results must contain at least one criterion")
        for raw_item in acceptance:
            item = require_mapping(raw_item, document)
            require_string(item.get("criterion"), document, "acceptance criterion")
            require_string(item.get("evidence"), document, "acceptance evidence")
            if item.get("status") not in {"passed", "failed"}:
                fail_schema(document, "acceptance status must be passed or failed")
        if any(item["status"] != "passed" for item in acceptance):
            raise AIWorkflowError(
                code="acceptance_not_met",
                message="Implementation cannot complete while acceptance criteria are unmet.",
                exit_code=4,
                details={
                    "criteria": [item["criterion"] for item in acceptance if item["status"] != "passed"]
                },
            )
        require_string_list(data.get("validation"), document, "validation")
        require_string_list(data.get("risks"), document, "risks")
    elif stage == "testing":
        _validate_task_id(data.get("task_id"), active_item, document)
        if data.get("status") not in {"completed", "failed", "skipped"}:
            fail_schema(document, "testing status must be completed, failed, or skipped")
        require_string_list(data.get("test_files"), document, "test_files")
        command = data.get("command")
        if command is not None and not isinstance(command, str):
            fail_schema(document, "command must be a string or null")
        require_string(data.get("summary"), document, "summary")
        require_string_list(data.get("uncovered"), document, "uncovered")
    else:
        fail_schema(document, f"unsupported stage '{stage}'")
    return data


def reconcile_requirements(
    current: dict[str, Any],
    result: dict[str, Any],
    *,
    revision: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    existing = {item["id"]: dict(item) for item in current["items"]}
    used_ids: set[str] = set()
    normalized_results: list[dict[str, Any]] = []
    known_ids = list(existing)
    withdrawn_ids = set(result.get("withdrawn_requirements", []))
    unknown_withdrawals = sorted(withdrawn_ids - set(existing))
    if unknown_withdrawals:
        raise AIWorkflowError(
            code="unknown_requirement_id",
            message="Requirement withdrawal references an unknown requirement.",
            exit_code=4,
            details={"ids": unknown_withdrawals},
        )

    for raw_item in result["requirements"]:
        item = dict(raw_item)
        item_id = item.get("id")
        if item_id is None:
            item_id = next_id("requirement", known_ids)
            known_ids.append(item_id)
        elif item_id not in existing:
            if not (
                type(item.get("revision")) is int
                and item["revision"] >= 1
                and type(item.get("origin_revision")) is int
                and item.get("approved_revision") is None
            ):
                raise AIWorkflowError(
                    code="unknown_requirement_id",
                    message="Existing requirement ids must be provided by prepare.",
                    exit_code=4,
                    details={"id": item_id},
                )
            known_ids.append(item_id)
        previous = existing.get(item_id)
        change_kind = item.get("change_kind")
        if previous is not None and change_kind not in {"behavior", "presentation"}:
            fail_schema(
                "analysis result manifest",
                "existing requirement changes require change_kind behavior or presentation",
            )
        if previous is None and change_kind is not None:
            fail_schema(
                "analysis result manifest",
                "change_kind is only valid for an existing requirement",
            )
        if item_id in used_ids:
            raise AIWorkflowError(
                code="duplicate_requirement_id",
                message="Requirement id appears more than once in the result manifest.",
                exit_code=4,
                details={"id": item_id},
            )
        used_ids.add(item_id)
        normalized = {
            "id": item_id,
            "title": item["title"],
            "summary": item["summary"],
            "disposition": item.get("disposition", "proposed"),
            "sources": list(item["sources"]),
            "platform_scope": item["platform_scope"],
            "change_type": item["change_type"],
            "scope_reason": item["scope_reason"],
            "origin_revision": revision,
        }
        revision_fields = _unit_revision_fields(
            previous,
            normalized,
            semantic_value=requirement_semantic_value,
            content_value=requirement_content_value,
        )
        if previous is not None and change_kind == "presentation":
            candidate_semantic = requirement_semantic_value(normalized)
            if candidate_semantic["disposition"] == "proposed":
                candidate_semantic["disposition"] = "accepted"
            if candidate_semantic != requirement_semantic_value(previous):
                raise AIWorkflowError(
                    code="invalid_change_kind",
                    message="Presentation changes cannot modify requirement behavior fields.",
                    exit_code=4,
                    details={"id": item_id},
                )
            revision_fields["semantic_sha256"] = previous["semantic_sha256"]
        else:
            effective_semantic = requirement_semantic_value(normalized)
            if effective_semantic["disposition"] == "proposed":
                effective_semantic["disposition"] = "accepted"
            revision_fields["semantic_sha256"] = semantic_digest(effective_semantic)
        normalized.update(revision_fields)
        existing[item_id] = normalized
        normalized_result = dict(normalized)
        if change_kind is not None:
            normalized_result["change_kind"] = change_kind
        normalized_results.append(normalized_result)

    overlap = sorted(used_ids.intersection(withdrawn_ids))
    if overlap:
        raise AIWorkflowError(
            code="requirement_patch_conflict",
            message="A requirement cannot be updated and withdrawn in the same revision.",
            exit_code=4,
            details={"ids": overlap},
        )
    for item_id in withdrawn_ids:
        item = existing[item_id]
        withdrawn = {**item, "disposition": "withdrawn", "origin_revision": revision}
        withdrawn.update(
            _unit_revision_fields(
                item,
                withdrawn,
                semantic_value=requirement_semantic_value,
                content_value=requirement_content_value,
            )
        )
        existing[item_id] = withdrawn

    if not existing:
        raise AIWorkflowError(
            code="empty_requirements",
            message="Analysis must establish at least one requirement before submission.",
            exit_code=4,
        )

    normalized_manifest = dict(result)
    normalized_manifest["requirements"] = normalized_results
    normalized_manifest["withdrawn_requirements"] = sorted(withdrawn_ids)
    index = {"schema_version": SCHEMA_VERSION, "items": sorted(existing.values(), key=lambda item: item["id"])}
    return index, normalized_manifest


def validate_design_coverage(
    requirements: dict[str, Any],
    result: dict[str, Any],
) -> None:
    accepted = {
        item["id"]
        for item in requirements["items"]
        if item["disposition"] == "accepted"
    }
    covered = set(result["requirements"])
    unknown = sorted(covered - accepted)
    missing = sorted(accepted - covered)
    if unknown or missing:
        raise AIWorkflowError(
            code="design_requirement_mismatch",
            message="Technical design must cover every accepted requirement and no unavailable requirement.",
            exit_code=4,
            details={"unknown": unknown, "missing": missing},
        )


def reconcile_tasks(
    current: dict[str, Any],
    requirements: dict[str, Any],
    result: dict[str, Any],
    *,
    revision: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    existing = {item["id"]: dict(item) for item in current["items"]}
    requirement_ids = {
        item["id"]
        for item in requirements["items"]
        if item["disposition"] == "accepted"
    }
    known_ids = list(existing)
    used_ids: set[str] = set()
    key_to_id: dict[str, str] = {}
    withdrawn_ids = set(result.get("withdrawn_tasks", []))
    unknown_withdrawals = sorted(withdrawn_ids - set(existing))
    if unknown_withdrawals:
        raise AIWorkflowError(
            code="unknown_task_id",
            message="Task withdrawal references an unknown task.",
            exit_code=4,
            details={"ids": unknown_withdrawals},
        )

    for raw_item in result["tasks"]:
        item_id = raw_item.get("id")
        if item_id is None:
            item_id = next_id("task", known_ids)
            known_ids.append(item_id)
        elif item_id not in existing:
            if not (
                type(raw_item.get("revision")) is int
                and raw_item["revision"] >= 1
                and type(raw_item.get("origin_revision")) is int
                and raw_item.get("approved_revision") is None
            ):
                raise AIWorkflowError(
                    code="unknown_task_id",
                    message="Existing task ids must be provided by prepare.",
                    exit_code=4,
                    details={"id": item_id},
                )
            known_ids.append(item_id)
        if item_id in used_ids:
            raise AIWorkflowError(
                code="duplicate_task_id",
                message="Task id appears more than once in the result manifest.",
                exit_code=4,
                details={"id": item_id},
            )
        used_ids.add(item_id)
        key_to_id[raw_item["key"]] = item_id

    normalized_results: list[dict[str, Any]] = []
    covered_requirements: set[str] = set()
    for raw_item in result["tasks"]:
        item_id = key_to_id[raw_item["key"]]
        unknown_requirements = sorted(set(raw_item["requirements"]) - requirement_ids)
        if unknown_requirements:
            raise AIWorkflowError(
                code="unknown_requirement_reference",
                message="Task references unavailable requirements.",
                exit_code=4,
                details={"ids": unknown_requirements},
            )
        covered_requirements.update(raw_item["requirements"])
        dependencies: list[str] = []
        for dependency in raw_item["depends_on"]:
            dependency_id = key_to_id.get(dependency, dependency)
            if dependency_id not in set(existing).union(used_ids):
                raise AIWorkflowError(
                    code="unknown_task_dependency",
                    message="Task dependency must remain active in the current task-plan revision.",
                    exit_code=4,
                    details={"dependency": dependency},
                )
            dependencies.append(dependency_id)
        normalized = {
            "id": item_id,
            "title": raw_item["title"],
            "requirements": list(raw_item["requirements"]),
            "depends_on": dependencies,
            "status": "proposed",
            "origin_revision": revision,
        }
        normalized.update(
            _unit_revision_fields(
                existing.get(item_id),
                normalized,
                semantic_value=task_semantic_value,
                content_value=task_content_value,
            )
        )
        existing[item_id] = normalized
        normalized_results.append({**normalized, "key": raw_item["key"]})

    overlap = sorted(used_ids.intersection(withdrawn_ids))
    if overlap:
        raise AIWorkflowError(
            code="task_patch_conflict",
            message="A task cannot be updated and withdrawn in the same revision.",
            exit_code=4,
            details={"ids": overlap},
        )
    for item_id in withdrawn_ids:
        item = existing[item_id]
        withdrawn = {**item, "status": "withdrawn", "origin_revision": revision}
        withdrawn.update(
            _unit_revision_fields(
                item,
                withdrawn,
                semantic_value=task_semantic_value,
                content_value=task_content_value,
            )
        )
        existing[item_id] = withdrawn

    for item in existing.values():
        if item["status"] != "withdrawn" and any(
            dependency in withdrawn_ids for dependency in item["depends_on"]
        ):
            raise AIWorkflowError(
                code="unknown_task_dependency",
                message="Active tasks cannot depend on a withdrawn task.",
                exit_code=4,
                details={"id": item["id"]},
            )

    covered_requirements = {
        requirement_id
        for item in existing.values()
        if item["status"] != "withdrawn"
        for requirement_id in item["requirements"]
    }
    uncovered_requirements = sorted(requirement_ids - covered_requirements)
    if uncovered_requirements:
        raise AIWorkflowError(
            code="uncovered_requirements",
            message="Every accepted requirement must be covered by an active task-plan task.",
            exit_code=4,
            details={"ids": uncovered_requirements},
        )

    validate_task_dependency_graph(existing)

    normalized_manifest = dict(result)
    normalized_manifest["tasks"] = normalized_results
    normalized_manifest["withdrawn_tasks"] = sorted(withdrawn_ids)
    index = {"schema_version": SCHEMA_VERSION, "items": sorted(existing.values(), key=lambda item: item["id"])}
    return index, normalized_manifest


def _unit_revision_fields(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    *,
    semantic_value: Any,
    content_value: Any,
) -> dict[str, Any]:
    semantic_sha256 = semantic_digest(semantic_value(current))
    content_sha256 = semantic_digest(content_value(current))
    if previous is None:
        revision = 1
        approved_revision = None
    else:
        previous_content = previous.get("content_sha256")
        if previous_content is None:
            previous_content = semantic_digest(content_value(previous))
        revision = int(previous.get("revision", 1))
        if previous_content != content_sha256:
            revision += 1
        approved_revision = previous.get("approved_revision", previous.get("revision", 1))
    return {
        "revision": revision,
        "approved_revision": approved_revision,
        "semantic_sha256": semantic_sha256,
        "content_sha256": content_sha256,
    }


def find_artifact(artifacts: dict[str, Any], artifact_id: str) -> dict[str, Any] | None:
    return next((item for item in artifacts["items"] if item["id"] == artifact_id), None)


def replace_artifact(artifacts: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    items = [dict(item) for item in artifacts["items"] if item["id"] != artifact["id"]]
    items.append(artifact)
    items.sort(key=lambda item: item["id"])
    return {"schema_version": SCHEMA_VERSION, "items": items}


def _validate_requirement_results(value: Any, document: str) -> None:
    items = require_list(value, document, "requirements")
    for raw_item in items:
        item = require_mapping(raw_item, document)
        item_id = item.get("id")
        if item_id is not None and (
            not isinstance(item_id, str) or not ID_PATTERNS["requirement"].fullmatch(item_id)
        ):
            fail_schema(document, "requirement id must be a valid REQ id or null")
        change_kind = item.get("change_kind")
        if change_kind is not None and change_kind not in {"behavior", "presentation"}:
            fail_schema(document, "change_kind must be behavior or presentation")
        require_string(item.get("title"), document, "title")
        require_string(item.get("summary"), document, "summary")
        sources = require_source_list(item.get("sources"), document, "sources")
        if not sources:
            fail_schema(document, "requirement sources must contain at least one entry")
        disposition = item.get("disposition", "proposed")
        if disposition not in {"proposed", "deferred", "excluded"}:
            fail_schema(document, f"invalid submitted disposition '{disposition}'")
        platform_scope = item.get("platform_scope")
        if platform_scope not in {"target", "cross_platform", "other"}:
            fail_schema(document, f"invalid platform_scope '{platform_scope}'")
        if item.get("change_type") not in {"new", "modify", "reuse"}:
            fail_schema(document, f"invalid change_type '{item.get('change_type')}'")
        require_string(item.get("scope_reason"), document, "scope_reason")
        if disposition == "proposed" and platform_scope == "other":
            fail_schema(document, "other-platform requirements cannot be proposed for implementation")


def _validate_task_results(value: Any, document: str) -> None:
    items = require_list(value, document, "tasks")
    keys: set[str] = set()
    for raw_item in items:
        item = require_mapping(raw_item, document)
        key = require_string(item.get("key"), document, "key")
        if key in keys:
            fail_schema(document, f"duplicate task key '{key}'")
        keys.add(key)
        item_id = item.get("id")
        if item_id is not None and (
            not isinstance(item_id, str) or not ID_PATTERNS["task"].fullmatch(item_id)
        ):
            fail_schema(document, "task id must be a valid task id or null")
        require_string(item.get("title"), document, "title")
        requirements = require_string_list(item.get("requirements"), document, "requirements")
        if not requirements:
            fail_schema(document, "task requirements must contain at least one requirement id")
        require_string_list(item.get("depends_on"), document, "depends_on")


def _validate_design_evidence(value: Any, document: str) -> list[dict[str, str]]:
    items = require_list(value, document, "code_evidence")
    normalized: list[dict[str, str]] = []
    for raw_item in items:
        item = require_mapping(raw_item, document)
        normalized.append(
            {
                "path": require_string(item.get("path"), document, "code_evidence.path"),
                "symbol": require_string(
                    item.get("symbol"), document, "code_evidence.symbol"
                ),
                "purpose": require_string(
                    item.get("purpose"), document, "code_evidence.purpose"
                ),
            }
        )
    return normalized


def _validate_task_id(value: Any, active_item: str | None, document: str) -> None:
    task_id = require_string(value, document, "task_id")
    if task_id != active_item:
        fail_schema(document, "task_id must match the active item")


def _validate_closed_shape(
    value: Any,
    schema: dict[str, Any],
    document: str,
    *,
    path: str,
) -> None:
    schema_type = schema.get("type")
    if schema_type == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        missing = sorted(set(schema.get("required", ())) - set(value))
        if missing:
            fail_schema(
                document,
                f"{path} is missing required fields: {', '.join(missing)}",
            )
        if schema.get("additionalProperties") is False:
            unsupported = sorted(set(value) - set(properties))
            if unsupported:
                fail_schema(
                    document,
                    f"{path} contains unsupported fields: {', '.join(unsupported)}",
                )
        for key, child in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                _validate_closed_shape(
                    child,
                    child_schema,
                    document,
                    path=f"{path}.{key}",
                )
    elif schema_type == "array" and isinstance(value, list):
        minimum = schema.get("minItems")
        if isinstance(minimum, int) and len(value) < minimum:
            fail_schema(document, f"{path} must contain at least {minimum} item(s)")
        if schema.get("uniqueItems") is True:
            serialized = [
                json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                for item in value
            ]
            if len(serialized) != len(set(serialized)):
                fail_schema(document, f"{path} must contain unique items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_closed_shape(
                    item,
                    item_schema,
                    document,
                    path=f"{path}[{index}]",
                )


def _project_schema_fields(value: Any, schema: dict[str, Any]) -> Any:
    schema_type = schema.get("type")
    if schema_type == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        return {
            key: _project_schema_fields(child, properties[key])
            for key, child in value.items()
            if key in properties
        }
    if schema_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return [_project_schema_fields(item, item_schema) for item in value]
        return list(value)
    return value
