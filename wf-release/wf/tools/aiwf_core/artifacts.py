"""Artifact paths, result manifests, indexes, and dependency invalidation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .model import (
    AIWorkflowError,
    ID_PATTERNS,
    MEMORY_OPERATIONS,
    MEMORY_TYPES,
    SCHEMA_VERSION,
    SOURCE_KINDS,
    fail_schema,
    next_id,
    require_evidence_list,
    require_list,
    require_mapping,
    require_optional_string,
    require_source_list,
    require_string,
    require_string_list,
    validate_task_dependency_graph,
)


def sha256_content(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def semantic_result_hash(result: dict[str, Any]) -> str:
    semantic = result_seed_from_record(
        result["stage"],
        result,
        preserve_memory_delta=False,
    )
    semantic.pop("memory_delta", None)
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
    evidence_array = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["path", "symbol"],
            "properties": {"path": string, "symbol": string},
            "additionalProperties": False,
        },
    }
    memory_delta = {
        "type": "array",
        "items": {
            "type": "object",
            "required": [
                "operation",
                "type",
                "content",
                "evidence",
                "rationale",
                "validation",
            ],
            "properties": {
                "operation": {"enum": list(MEMORY_OPERATIONS)},
                "type": {"enum": list(MEMORY_TYPES)},
                "content": string,
                "evidence": evidence_array,
                "rationale": {"type": ["string", "null"]},
                "validation": {"type": ["string", "null"]},
                "target_id": {"type": ["string", "null"], "pattern": r"^M-\d{3,}$"},
            },
            "allOf": [
                {
                    "if": {
                        "properties": {"operation": {"const": "add"}},
                        "required": ["operation"],
                    },
                    "then": {"properties": {"target_id": {"type": "null"}}},
                    "else": {
                        "required": ["target_id"],
                        "properties": {
                            "target_id": {
                                "type": "string",
                                "pattern": r"^M-\d{3,}$",
                            }
                        },
                    },
                }
            ],
            "additionalProperties": False,
        },
    }
    properties: dict[str, Any] = {
        "schema_version": {"const": SCHEMA_VERSION},
        "stage": {"const": stage},
        "memory_delta": memory_delta,
        "superseded_decisions": string_array,
    }
    required = ["schema_version", "stage", "memory_delta"]
    if stage == "analysis":
        properties["target_platform"] = string
        properties["requirements"] = {
            "type": "array",
            "minItems": 1,
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
        required.extend(("target_platform", "requirements"))
    elif stage == "design":
        properties["requirements"] = {**string_array, "minItems": 1}
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
            "minItems": 1,
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
        required.append("tasks")
    elif stage == "specification":
        properties["task_id"] = {"type": "string", "pattern": r"^T-\d{3,}$"}
        required.append("task_id")
    elif stage == "implementation":
        properties.update(
            {
                "task_id": {"type": "string", "pattern": r"^T-\d{3,}$"},
                "changed_files": string_array,
                "validation_summary": {"type": "string"},
            }
        )
        required.extend(("task_id", "changed_files", "validation_summary"))
    elif stage == "testing":
        properties.update(
            {
                "task_id": {"type": "string", "pattern": r"^T-\d{3,}$"},
                "test_files": string_array,
                "execution": {
                    "type": "object",
                    "required": ["command", "exit_code", "summary"],
                    "properties": {
                        "command": {"type": ["string", "null"]},
                        "exit_code": {"type": ["integer", "null"]},
                        "summary": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "uncovered": string_array,
            }
        )
        required.extend(("task_id", "test_files", "execution", "uncovered"))
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
        "memory_delta": [],
        "superseded_decisions": [],
    }
    if stage == "analysis":
        seed["target_platform"] = ""
        seed["requirements"] = []
    elif stage == "design":
        seed["requirements"] = []
        seed["design_mode"] = "anchored"
        seed["greenfield_reason"] = None
        seed["code_evidence"] = []
    elif stage == "specification" and active_item is None:
        seed["tasks"] = []
    elif stage == "specification":
        seed["task_id"] = active_item
    elif stage == "implementation":
        seed.update(
            {"task_id": active_item, "changed_files": [], "validation_summary": ""}
        )
    elif stage == "testing":
        seed.update(
            {
                "task_id": active_item,
                "test_files": [],
                "execution": {"command": None, "exit_code": None, "summary": ""},
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
    preserve_memory_delta: bool,
    active_item: str | None = None,
) -> dict[str, Any]:
    resolved_active_item = active_item if active_item is not None else record.get("task_id")
    seed = _project_schema_fields(record, result_schema(stage, resolved_active_item))
    if stage == "analysis":
        for requirement in seed.get("requirements", []):
            for source in requirement.get("sources", []):
                if source.get("kind") == "agent_inference":
                    source["ref"] = "self"
    if not preserve_memory_delta:
        seed["memory_delta"] = []
        seed["superseded_decisions"] = []
    return seed


def validate_result_manifest(stage: str, value: Any, *, active_item: str | None) -> dict[str, Any]:
    document = f"{stage} result manifest"
    data = require_mapping(value, document)
    _validate_closed_shape(data, result_schema(stage, active_item), document, path="$")
    if data.get("schema_version") != SCHEMA_VERSION:
        fail_schema(document, "unsupported schema_version")
    if data.get("stage") != stage:
        fail_schema(document, f"stage must be '{stage}'")
    _validate_memory_delta(data.get("memory_delta"), document)
    if "superseded_decisions" in data:
        superseded = require_string_list(
            data.get("superseded_decisions"), document, "superseded_decisions"
        )
        if any(not ID_PATTERNS["decision"].fullmatch(item) for item in superseded):
            fail_schema(document, "superseded_decisions must contain decision ids")

    if stage == "analysis":
        require_string(data.get("target_platform"), document, "target_platform")
        _validate_requirement_results(data.get("requirements"), document)
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
    elif stage == "specification":
        _validate_task_id(data.get("task_id"), active_item, document)
    elif stage == "implementation":
        _validate_task_id(data.get("task_id"), active_item, document)
        require_string_list(data.get("changed_files"), document, "changed_files")
        require_string(data.get("validation_summary"), document, "validation_summary", empty=True)
    elif stage == "testing":
        _validate_task_id(data.get("task_id"), active_item, document)
        require_string_list(data.get("test_files"), document, "test_files")
        execution = require_mapping(data.get("execution"), document)
        command = execution.get("command")
        if command is not None and not isinstance(command, str):
            fail_schema(document, "execution.command must be a string or null")
        exit_code = execution.get("exit_code")
        if exit_code is not None and type(exit_code) is not int:
            fail_schema(document, "execution.exit_code must be an integer or null")
        require_string(execution.get("summary"), document, "execution.summary", empty=True)
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

    for raw_item in result["requirements"]:
        item = dict(raw_item)
        item_id = item.get("id")
        if item_id is None:
            item_id = next_id("requirement", known_ids)
            known_ids.append(item_id)
        elif item_id not in existing:
            raise AIWorkflowError(
                code="unknown_requirement_id",
                message="Existing requirement ids must be provided by prepare.",
                exit_code=4,
                details={"id": item_id},
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
        existing[item_id] = normalized
        normalized_results.append(dict(normalized))

    for item_id, item in existing.items():
        if item_id not in used_ids:
            item["disposition"] = "withdrawn"

    normalized_manifest = dict(result)
    normalized_manifest["requirements"] = normalized_results
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

    for raw_item in result["tasks"]:
        item_id = raw_item.get("id")
        if item_id is None:
            item_id = next_id("task", known_ids)
            known_ids.append(item_id)
        elif item_id not in existing:
            raise AIWorkflowError(
                code="unknown_task_id",
                message="Existing task ids must be provided by prepare.",
                exit_code=4,
                details={"id": item_id},
            )
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
            if dependency_id not in used_ids:
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
        existing[item_id] = normalized
        normalized_results.append({**normalized, "key": raw_item["key"]})

    uncovered_requirements = sorted(requirement_ids - covered_requirements)
    if uncovered_requirements:
        raise AIWorkflowError(
            code="uncovered_requirements",
            message="Every accepted requirement must be covered by an active task-plan task.",
            exit_code=4,
            details={"ids": uncovered_requirements},
        )

    for item_id, item in existing.items():
        if item_id not in used_ids:
            item["status"] = "withdrawn"
    validate_task_dependency_graph(existing)

    normalized_manifest = dict(result)
    normalized_manifest["tasks"] = normalized_results
    index = {"schema_version": SCHEMA_VERSION, "items": sorted(existing.values(), key=lambda item: item["id"])}
    return index, normalized_manifest


def find_artifact(artifacts: dict[str, Any], artifact_id: str) -> dict[str, Any] | None:
    return next((item for item in artifacts["items"] if item["id"] == artifact_id), None)


def replace_artifact(artifacts: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    items = [dict(item) for item in artifacts["items"] if item["id"] != artifact["id"]]
    items.append(artifact)
    items.sort(key=lambda item: item["id"])
    return {"schema_version": SCHEMA_VERSION, "items": items}


def invalidate_downstream(
    artifacts: dict[str, Any],
    *,
    artifact_id: str,
    revision: int,
) -> tuple[dict[str, Any], list[str]]:
    items = [dict(item) for item in artifacts["items"]]
    invalidated: list[str] = []
    frontier = {f"{artifact_id}@{revision}"}
    while frontier:
        next_frontier: set[str] = set()
        for item in items:
            if item["id"] == artifact_id or item["status"] == "stale":
                continue
            if frontier.intersection(item["depends_on"]):
                item["status"] = "stale"
                invalidated.append(item["id"])
                next_frontier.add(f"{item['id']}@{item['revision']}")
        frontier = next_frontier
    return {"schema_version": SCHEMA_VERSION, "items": items}, invalidated


def artifact_integrity_issues(root: Path, artifact: dict[str, Any]) -> list[dict[str, str]]:
    checks = (
        ("content", artifact["path"], artifact["content_sha256"]),
        ("snapshot", artifact["snapshot_path"], artifact["content_sha256"]),
        ("result", artifact["result_path"], artifact["result_sha256"]),
        ("work", artifact["work_path"], artifact["work_sha256"]),
    )
    issues: list[dict[str, str]] = []
    for component, relative_path, expected_hash in checks:
        relative = Path(relative_path)
        path = (root / relative).resolve(strict=False)
        if relative.is_absolute() or ".." in relative.parts or not path.is_relative_to(root.resolve()):
            issues.append({"component": component, "path": relative_path, "reason": "outside"})
            continue
        try:
            actual_hash = sha256_content(path.read_bytes())
        except OSError:
            issues.append({"component": component, "path": relative_path, "reason": "missing"})
            continue
        if actual_hash != expected_hash:
            issues.append({"component": component, "path": relative_path, "reason": "changed"})
    return issues


def verify_artifact_integrity(root: Path, artifact: dict[str, Any]) -> None:
    issues = artifact_integrity_issues(root, artifact)
    if issues:
        raise AIWorkflowError(
            code="artifact_drift",
            message="Registered artifact files do not match the approved revision.",
            exit_code=7,
            details={"artifact_id": artifact["id"], "issues": issues},
        )


def _validate_requirement_results(value: Any, document: str) -> None:
    items = require_list(value, document, "requirements")
    if not items:
        fail_schema(document, "requirements must contain at least one requirement")
    for raw_item in items:
        item = require_mapping(raw_item, document)
        item_id = item.get("id")
        if item_id is not None and (
            not isinstance(item_id, str) or not ID_PATTERNS["requirement"].fullmatch(item_id)
        ):
            fail_schema(document, "requirement id must be a valid REQ id or null")
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
    if not items:
        fail_schema(document, "tasks must contain at least one executable task")
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


def _validate_memory_delta(value: Any, document: str) -> None:
    items = require_list(value, document, "memory_delta")
    for raw_item in items:
        item = require_mapping(raw_item, document)
        operation = item.get("operation")
        if operation not in MEMORY_OPERATIONS:
            fail_schema(document, f"invalid memory operation '{operation}'")
        memory_type = require_string(item.get("type"), document, "memory type")
        if memory_type not in MEMORY_TYPES:
            fail_schema(document, f"invalid memory type '{memory_type}'")
        require_string(item.get("content"), document, "memory content")
        evidence = require_evidence_list(item.get("evidence"), document, "evidence")
        rationale = require_optional_string(item.get("rationale"), document, "rationale")
        validation = require_optional_string(item.get("validation"), document, "validation")
        if memory_type == "repository_fact" and not evidence:
            fail_schema(document, "repository_fact requires evidence")
        if memory_type in {"architecture_decision", "engineering_default"} and not rationale:
            fail_schema(document, f"{memory_type} requires rationale")
        if memory_type in {"engineering_default", "validation_item"} and not validation:
            fail_schema(document, f"{memory_type} requires validation")
        target_id = item.get("target_id")
        if operation == "add" and target_id is not None:
            fail_schema(document, "add memory operation cannot have target_id")
        if operation != "add" and (
            not isinstance(target_id, str) or not ID_PATTERNS["memory"].fullmatch(target_id)
        ):
            fail_schema(document, f"{operation} memory operation requires target_id")


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
