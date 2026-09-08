"""Shared command, schema, and workflow model definitions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

SCHEMA_VERSION = 9
STAGES = (
    "analysis",
    "design",
    "specification",
    "implementation",
    "testing",
    "completed",
)
MODES = ("ready", "working", "review", "blocked", "decision")
ARTIFACT_STATUSES = ("review", "approved", "changes_requested", "stale")
REQUIREMENT_DISPOSITIONS = (
    "proposed",
    "accepted",
    "deferred",
    "excluded",
    "withdrawn",
)
TASK_STATUSES = (
    "proposed",
    "planned",
    "in_progress",
    "implemented",
    "tested",
    "stale",
    "withdrawn",
)
MEMORY_OPERATIONS = ("add", "update", "retract")
MEMORY_STATUSES = ("active", "retracted")
MEMORY_TYPES = (
    "repository_fact",
    "architecture_decision",
    "engineering_default",
    "validation_item",
)
SOURCE_KINDS = (
    "prd",
    "user_feedback",
    "user_decision",
    "repository",
    "agent_inference",
)
QUESTION_STATUSES = ("open", "resolved", "superseded")

ID_PATTERNS = {
    "requirement": re.compile(r"^REQ-\d{3,}$"),
    "task": re.compile(r"^T-\d{3,}$"),
    "work": re.compile(r"^W-\d{6,}$"),
    "question": re.compile(r"^Q-\d{3,}$"),
    "decision": re.compile(r"^D-\d{3,}$"),
    "memory": re.compile(r"^M-\d{3,}$"),
}


@dataclass(frozen=True)
class CommandSpec:
    name: str
    help: str


COMMAND_SPECS = (
    CommandSpec("init", "Initialize a workspace."),
    CommandSpec("recover", "Recover an incomplete workspace transaction."),
    CommandSpec("prepare", "Prepare or resume the current work item."),
    CommandSpec("submit", "Submit a semantic artifact and result manifest."),
    CommandSpec("review", "Approve an artifact or request changes."),
    CommandSpec("revise", "Revise an approved artifact revision."),
    CommandSpec("resolve-drift", "Adopt or discard external artifact content changes."),
    CommandSpec("question", "Record blocking questions for the current work."),
    CommandSpec("decide", "Record a user decision."),
    CommandSpec("route-decision", "Resume work or revise an upstream artifact after decisions."),
    CommandSpec("route-upstream", "Revise an upstream artifact after a repository-backed factual correction."),
    CommandSpec("status", "Read workspace status without modifying it."),
    CommandSpec("render", "Render the static workspace dashboard."),
)


@dataclass(frozen=True)
class CommandRequest:
    command: str
    workspace: Path
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class AIWorkflowError(Exception):
    code: str
    message: str
    exit_code: int
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def fail_schema(document: str, message: str) -> NoReturn:
    raise AIWorkflowError(
        code="invalid_schema",
        message=f"Invalid {document}: {message}",
        exit_code=4,
        details={"document": document},
    )


def require_mapping(value: Any, document: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail_schema(document, "expected a JSON object")
    return value


def require_list(value: Any, document: str, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        fail_schema(document, f"'{field_name}' must be an array")
    return value


def require_string(value: Any, document: str, field_name: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        fail_schema(document, f"'{field_name}' must be a non-empty string")
    return value


def require_optional_string(value: Any, document: str, field_name: str) -> str | None:
    if value is not None and not isinstance(value, str):
        fail_schema(document, f"'{field_name}' must be a string or null")
    return value


def require_string_list(value: Any, document: str, field_name: str) -> list[str]:
    items = require_list(value, document, field_name)
    if any(not isinstance(item, str) or not item for item in items):
        fail_schema(document, f"'{field_name}' must contain only non-empty strings")
    return items


def require_source_list(value: Any, document: str, field_name: str) -> list[dict[str, str]]:
    items = require_list(value, document, field_name)
    normalized: list[dict[str, str]] = []
    for index, raw_item in enumerate(items):
        item = require_mapping(raw_item, f"{document}.{field_name}[{index}]")
        if set(item) != {"kind", "ref"}:
            fail_schema(document, f"'{field_name}' entries must contain only kind and ref")
        kind = require_string(item.get("kind"), document, f"{field_name}.kind")
        if kind not in SOURCE_KINDS:
            fail_schema(document, f"invalid source kind '{kind}'")
        ref = require_string(item.get("ref"), document, f"{field_name}.ref")
        normalized.append({"kind": kind, "ref": ref})
    return normalized


def require_evidence_list(
    value: Any,
    document: str,
    field_name: str,
) -> list[dict[str, str]]:
    items = require_list(value, document, field_name)
    allowed = {"path", "symbol"}
    normalized: list[dict[str, str]] = []
    for index, raw_item in enumerate(items):
        item = require_mapping(raw_item, f"{document}.{field_name}[{index}]")
        if set(item) != allowed:
            fail_schema(document, f"'{field_name}' entries must contain only {', '.join(sorted(allowed))}")
        normalized_item = {
            "path": require_string(item.get("path"), document, f"{field_name}.path"),
            "symbol": require_string(item.get("symbol"), document, f"{field_name}.symbol"),
        }
        normalized.append(normalized_item)
    return normalized


def require_schema_version(document: str, data: dict[str, Any]) -> None:
    if data.get("schema_version") != SCHEMA_VERSION:
        fail_schema(document, f"unsupported schema_version {data.get('schema_version')!r}")


def require_unique_ids(document: str, items: list[Any], kind: str) -> None:
    seen: set[str] = set()
    pattern = ID_PATTERNS[kind]
    for index, raw_item in enumerate(items):
        item = require_mapping(raw_item, f"{document}.items[{index}]")
        item_id = require_string(item.get("id"), document, "id")
        if not pattern.fullmatch(item_id):
            fail_schema(document, f"invalid {kind} id '{item_id}'")
        if item_id in seen:
            fail_schema(document, f"duplicate id '{item_id}'")
        seen.add(item_id)


def validate_project(data: dict[str, Any]) -> None:
    document = "project.json"
    require_schema_version(document, data)
    for field_name in ("project_id", "name", "platform", "created_at"):
        require_string(data.get(field_name), document, field_name)
    require_string(data.get("code_repository"), document, "code_repository")
    require_string_list(data.get("prd_files"), document, "prd_files")


def validate_state(data: dict[str, Any]) -> None:
    document = "state.json"
    require_schema_version(document, data)
    stage = data.get("current_stage")
    mode = data.get("mode")
    if stage not in STAGES:
        fail_schema(document, f"unknown current_stage '{stage}'")
    if mode not in MODES:
        fail_schema(document, f"unknown mode '{mode}'")
    require_optional_string(data.get("active_item"), document, "active_item")
    active_work = require_optional_string(data.get("active_work"), document, "active_work")
    active_work_sha256 = require_optional_string(
        data.get("active_work_sha256"), document, "active_work_sha256"
    )
    if active_work is not None and not ID_PATTERNS["work"].fullmatch(active_work):
        fail_schema(document, f"invalid active_work '{active_work}'")
    require_string_list(data.get("pending_reviews"), document, "pending_reviews")
    require_string_list(data.get("blocking_questions"), document, "blocking_questions")
    require_string(data.get("updated_at"), document, "updated_at")
    if mode in {"working", "blocked", "decision"} and active_work is None:
        fail_schema(document, f"{mode} mode requires active_work")
    if mode not in {"working", "blocked", "decision"} and active_work is not None:
        fail_schema(document, f"{mode} mode cannot retain active_work")
    if active_work is not None and (
        active_work_sha256 is None or not re.fullmatch(r"[0-9a-f]{64}", active_work_sha256)
    ):
        fail_schema(document, "active work requires active_work_sha256")
    if active_work is None and active_work_sha256 is not None:
        fail_schema(document, "active_work_sha256 requires active_work")
    if mode == "review" and not data["pending_reviews"]:
        fail_schema(document, "review mode requires pending_reviews")
    if mode == "blocked" and not data["blocking_questions"]:
        fail_schema(document, "blocked mode requires blocking_questions")
    if mode == "decision" and data["blocking_questions"]:
        fail_schema(document, "decision mode cannot retain blocking_questions")
    if stage == "completed" and mode != "ready":
        fail_schema(document, "completed stage must use ready mode")


def validate_requirements(data: dict[str, Any]) -> None:
    document = "requirements.json"
    require_schema_version(document, data)
    items = require_list(data.get("items"), document, "items")
    require_unique_ids(document, items, "requirement")
    for raw_item in items:
        item = require_mapping(raw_item, document)
        require_string(item.get("title"), document, "title")
        require_string(item.get("summary"), document, "summary")
        require_source_list(item.get("sources"), document, "sources")
        if item.get("platform_scope") not in {"target", "cross_platform", "other"}:
            fail_schema(document, f"invalid platform_scope '{item.get('platform_scope')}'")
        if item.get("change_type") not in {"new", "modify", "reuse"}:
            fail_schema(document, f"invalid change_type '{item.get('change_type')}'")
        require_string(item.get("scope_reason"), document, "scope_reason")
        if item.get("disposition") not in REQUIREMENT_DISPOSITIONS:
            fail_schema(document, f"invalid disposition '{item.get('disposition')}'")
        if type(item.get("origin_revision")) is not int or item["origin_revision"] < 1:
            fail_schema(document, "origin_revision must be a positive integer")


def validate_tasks(data: dict[str, Any]) -> None:
    document = "tasks.json"
    require_schema_version(document, data)
    items = require_list(data.get("items"), document, "items")
    require_unique_ids(document, items, "task")
    task_ids = {item["id"] for item in items}
    status_by_id = {item["id"]: item.get("status") for item in items}
    for raw_item in items:
        item = require_mapping(raw_item, document)
        require_string(item.get("title"), document, "title")
        requirements = require_string_list(item.get("requirements"), document, "requirements")
        if item.get("status") != "withdrawn" and not requirements:
            fail_schema(document, "active task must reference at least one requirement")
        if any(not ID_PATTERNS["requirement"].fullmatch(item_id) for item_id in requirements):
            fail_schema(document, "requirements contains an invalid requirement id")
        dependencies = require_string_list(item.get("depends_on"), document, "depends_on")
        if any(item_id not in task_ids for item_id in dependencies):
            fail_schema(document, "depends_on references an unknown task")
        if item["id"] in dependencies:
            fail_schema(document, "task cannot depend on itself")
        if item.get("status") not in TASK_STATUSES:
            fail_schema(document, f"invalid task status '{item.get('status')}'")
        if type(item.get("origin_revision")) is not int or item["origin_revision"] < 1:
            fail_schema(document, "origin_revision must be a positive integer")
        if item.get("status") != "withdrawn" and any(
            status_by_id[dependency] == "withdrawn"
            for dependency in dependencies
        ):
            fail_schema(document, "active task cannot depend on a withdrawn task")
    validate_task_dependency_graph({item["id"]: item for item in items})


def validate_artifacts(data: dict[str, Any]) -> None:
    document = "artifacts.json"
    require_schema_version(document, data)
    items = require_list(data.get("items"), document, "items")
    seen: set[str] = set()
    for raw_item in items:
        item = require_mapping(raw_item, document)
        artifact_id = require_string(item.get("id"), document, "id")
        if artifact_id in seen:
            fail_schema(document, f"duplicate id '{artifact_id}'")
        seen.add(artifact_id)
        for field_name in (
            "type",
            "snapshot_path",
            "path",
            "result_path",
            "work_path",
            "content_sha256",
            "result_sha256",
            "work_sha256",
            "updated_at",
        ):
            require_string(item.get(field_name), document, field_name)
        for field_name in ("content_sha256", "result_sha256", "work_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", item[field_name]):
                fail_schema(document, f"'{field_name}' must be a SHA-256 digest")
        require_string(item.get("stage"), document, "stage")
        stage = item["stage"]
        active_item = require_optional_string(item.get("active_item"), document, "active_item")
        expected_type = {
            ("analysis", False): "analysis",
            ("design", False): "design",
            ("specification", False): "task_plan",
            ("specification", True): "specification",
            ("implementation", True): "implementation_report",
            ("testing", True): "test_report",
        }.get((stage, active_item is not None))
        if expected_type is None or item["type"] != expected_type:
            fail_schema(document, "artifact type must match its stage and active_item")
        if stage in {"analysis", "design"} and active_item is not None:
            fail_schema(document, f"{stage} artifact cannot have active_item")
        if stage in {"implementation", "testing"} and (
            active_item is None or not ID_PATTERNS["task"].fullmatch(active_item)
        ):
            fail_schema(document, f"{stage} artifact requires a valid task active_item")
        if stage == "specification" and active_item is not None and not ID_PATTERNS[
            "task"
        ].fullmatch(active_item):
            fail_schema(document, "specification active_item must be a valid task id")
        if item.get("status") not in ARTIFACT_STATUSES:
            fail_schema(document, f"invalid artifact status '{item.get('status')}'")
        revision = item.get("revision")
        approved_revision = item.get("approved_revision")
        if type(revision) is not int or revision < 1:
            fail_schema(document, "revision must be a positive integer")
        if approved_revision is not None and (
            type(approved_revision) is not int
            or approved_revision < 1
            or approved_revision > revision
        ):
            fail_schema(document, "approved_revision must be null or a valid revision")
        require_string_list(item.get("depends_on"), document, "depends_on")
        require_string_list(item.get("sources"), document, "sources")


def validate_decisions(data: dict[str, Any]) -> None:
    document = "decisions.json"
    require_schema_version(document, data)
    items = require_list(data.get("items"), document, "items")
    require_unique_ids(document, items, "decision")
    known_ids = {item["id"] for item in items}
    for raw_item in items:
        item = require_mapping(raw_item, document)
        question_id = require_string(item.get("question_id"), document, "question_id")
        if not ID_PATTERNS["question"].fullmatch(question_id):
            fail_schema(document, f"invalid question_id '{question_id}'")
        require_string(item.get("decision"), document, "decision")
        impact = require_string_list(item.get("impact"), document, "impact")
        if not impact or any(stage not in STAGES[:-1] for stage in impact):
            fail_schema(document, "impact must contain workflow stages")
        if item.get("status") not in {"active", "superseded"}:
            fail_schema(document, "decision status must be active or superseded")
        supersedes = require_string_list(item.get("supersedes"), document, "supersedes")
        if any(not ID_PATTERNS["decision"].fullmatch(value) for value in supersedes):
            fail_schema(document, "supersedes must contain decision ids")
        superseded_by = require_optional_string(
            item.get("superseded_by"), document, "superseded_by"
        )
        if superseded_by is not None and not (
            ID_PATTERNS["decision"].fullmatch(superseded_by)
            or re.fullmatch(r"[a-zA-Z0-9-]+@\d+", superseded_by)
        ):
            fail_schema(
                document,
                "superseded_by must be a decision id, artifact revision, or null",
            )
        if item["status"] == "active" and superseded_by is not None:
            fail_schema(document, "active decisions cannot have superseded_by")
        if item["status"] == "superseded" and superseded_by is None:
            fail_schema(document, "superseded decisions require superseded_by")
        if any(value not in known_ids or value == item["id"] for value in supersedes):
            fail_schema(document, "supersedes must reference other known decisions")
        if (
            superseded_by is not None
            and ID_PATTERNS["decision"].fullmatch(superseded_by)
            and superseded_by not in known_ids
        ):
            fail_schema(document, "superseded_by references an unknown decision")
        require_string(item.get("created_at"), document, "created_at")


def validate_questions(data: dict[str, Any]) -> None:
    document = "questions.json"
    require_schema_version(document, data)
    items = require_list(data.get("items"), document, "items")
    require_unique_ids(document, items, "question")
    for raw_item in items:
        item = require_mapping(raw_item, document)
        for field_name in ("question", "reason", "recommendation", "stage", "work_id", "created_at"):
            require_string(item.get(field_name), document, field_name)
        if item["stage"] not in STAGES[:-1]:
            fail_schema(document, "question stage must be a workflow stage")
        if not ID_PATTERNS["work"].fullmatch(item["work_id"]):
            fail_schema(document, "question work_id must be a valid work id")
        active_item = require_optional_string(item.get("active_item"), document, "active_item")
        if active_item is not None and not ID_PATTERNS["task"].fullmatch(active_item):
            fail_schema(document, "question active_item must be a valid task id")
        impact = require_string_list(item.get("impact"), document, "impact")
        if not impact or any(stage not in STAGES[:-1] for stage in impact):
            fail_schema(document, "question impact must contain workflow stages")
        supersedes = require_string_list(
            item.get("supersedes_decisions"), document, "supersedes_decisions"
        )
        if any(not ID_PATTERNS["decision"].fullmatch(value) for value in supersedes):
            fail_schema(document, "supersedes_decisions must contain decision ids")
        if item.get("status") not in QUESTION_STATUSES:
            fail_schema(document, f"invalid question status '{item.get('status')}'")
        require_optional_string(item.get("decision_id"), document, "decision_id")


def validate_memory(data: dict[str, Any]) -> None:
    document = "memory.json"
    require_schema_version(document, data)
    items = require_list(data.get("items"), document, "items")
    require_unique_ids(document, items, "memory")
    for raw_item in items:
        item = require_mapping(raw_item, document)
        for field_name in ("type", "content", "source", "updated_at"):
            require_string(item.get(field_name), document, field_name)
        if item["type"] not in MEMORY_TYPES:
            fail_schema(document, f"invalid memory type '{item['type']}'")
        evidence = require_evidence_list(item.get("evidence"), document, "evidence")
        rationale = require_optional_string(item.get("rationale"), document, "rationale")
        validation = require_optional_string(item.get("validation"), document, "validation")
        if item["type"] == "repository_fact" and not evidence:
            fail_schema(document, "repository_fact requires evidence")
        if item["type"] in {"architecture_decision", "engineering_default"} and not rationale:
            fail_schema(document, f"{item['type']} requires rationale")
        if item["type"] in {"engineering_default", "validation_item"} and not validation:
            fail_schema(document, f"{item['type']} requires validation")
        if item.get("status") not in MEMORY_STATUSES:
            fail_schema(document, f"invalid memory status '{item.get('status')}'")


VALIDATORS = {
    "project.json": validate_project,
    "state.json": validate_state,
    "requirements.json": validate_requirements,
    "tasks.json": validate_tasks,
    "artifacts.json": validate_artifacts,
    "decisions.json": validate_decisions,
    "questions.json": validate_questions,
    "memory.json": validate_memory,
}


def validate_document(name: str, value: Any) -> dict[str, Any]:
    data = require_mapping(value, name)
    validator = VALIDATORS.get(name)
    if validator is not None:
        validator(data)
    return data


def next_id(kind: str, existing_ids: list[str]) -> str:
    pattern = ID_PATTERNS[kind]
    maximum = 0
    for item_id in existing_ids:
        if pattern.fullmatch(item_id):
            maximum = max(maximum, int(item_id.rsplit("-", 1)[1]))
    widths = {"work": 6}
    prefixes = {
        "requirement": "REQ",
        "task": "T",
        "work": "W",
        "question": "Q",
        "decision": "D",
        "memory": "M",
    }
    return f"{prefixes[kind]}-{maximum + 1:0{widths.get(kind, 3)}d}"


def validate_task_dependency_graph(tasks: dict[str, dict[str, Any]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visited:
            return
        if task_id in visiting:
            raise AIWorkflowError(
                code="task_dependency_cycle",
                message="Task dependency graph contains a cycle.",
                exit_code=4,
                details={"task_id": task_id},
            )
        visiting.add(task_id)
        for dependency in tasks[task_id]["depends_on"]:
            if dependency in tasks:
                visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in tasks:
        visit(task_id)
