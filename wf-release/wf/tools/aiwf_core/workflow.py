"""Deterministic workflow operations over the workspace store."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifacts import (
    artifact_integrity_issues,
    artifact_identity,
    find_artifact,
    invalidate_downstream,
    reconcile_requirements,
    reconcile_tasks,
    replace_artifact,
    result_seed_from_record,
    semantic_result_hash,
    sha256_content,
    validate_design_coverage,
    validate_result_manifest,
    verify_artifact_integrity,
)
from .context import build_work, copy_successor_work, validate_work
from .dashboard import DASHBOARD_FILENAME, render_dashboard
from .decision_flow import (
    decision_feedback,
    normalize_question,
    resolved_decisions_for_work,
    upstream_references,
    validate_decision_revision_target,
    validate_decision_state,
)
from .decisions import (
    append_decision,
    supersede_decisions_by_artifact,
    validate_active_decision_ids,
)
from .file_roles import validate_stage_file_roles
from .health import semantic_artifact_ids, semantic_health_issues
from .initialization import prepare_initialization
from .model import (
    AIWorkflowError,
    CommandRequest,
    SCHEMA_VERSION,
    next_id,
    now_iso,
    require_evidence_list,
)
from .memory_view import render_memory
from .operation_policy import assert_operation_allowed, raise_health_blocked
from .repository import (
    checkpoint_repository_session,
    compare_repository_session,
    inspect_repository,
    normalize_repository_path,
    repository_has_files,
    resume_repository_session,
    start_repository_session,
    validate_repository_evidence,
)
from .review import advance_after_approval, apply_memory_delta, approve_indexes
from .sources import normalize_requirement_sources
from .stage_context import build_stage_context
from .stage_guides import load_stage_guide
from .storage import WorkspaceStore, json_bytes, sha256_bytes


class WorkflowEngine:
    def __init__(self, workspace: Path | str) -> None:
        self.store = WorkspaceStore(workspace)

    def bootstrap(self, project: Mapping[str, Any]) -> None:
        self.store.bootstrap(project)

    def initialize(
        self,
        *,
        name: str,
        platform: str,
        prd_paths: Sequence[str],
        code_repository: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        prepared = prepare_initialization(
            workspace=self.store.root,
            name=name,
            platform=platform,
            prd_paths=prd_paths,
            code_repository=code_repository,
            project_id=project_id,
        )
        self.store.bootstrap(prepared.project, prd_files=prepared.prd_files)
        return self.inspect()

    def recover(self) -> list[str]:
        with self.store.lock(exclusive=True):
            return self._recover_and_sync_locked()

    def recover_workspace(self) -> dict[str, Any]:
        recovered = self.recover()
        return {
            "status": "recovered",
            "workspace": str(self.store.root),
            "recovered": recovered,
        }

    def _recover_and_sync_locked(self) -> list[str]:
        recovered = self._recover_transactions_locked()
        expected = render_memory(
            self.store.read_json("memory.json"),
            self.store.read_json("decisions.json"),
        ).encode("utf-8")
        path = self.store.safe_path(".aiwf/memory.md")
        try:
            actual = path.read_bytes()
        except OSError:
            actual = None
        if actual != expected:
            self.store.replace_generated_locked(".aiwf/memory.md", expected)
            recovered.append("generated:memory.md:rebuilt")
        return recovered

    def _recover_transactions_locked(self) -> list[str]:
        return self.store.recover_locked()

    def prepare_work(
        self,
        *,
        goal: str | None = None,
        active_item: str | None = None,
        inputs: Sequence[str] | None = None,
        depends_on: Sequence[str] | None = None,
        sources: Sequence[str] | None = None,
        stage_guide: str | None = None,
        constraints: Sequence[str] | None = None,
        instruction: str = "",
    ) -> dict[str, Any]:
        with self.store.lock(exclusive=True):
            self._recover_transactions_locked()
            self._assert_operation_allowed_locked("prepare")
            state = self.store.read_json("state.json")
            if state["mode"] == "working":
                return self._read_work(
                    state["active_work"],
                    expected_hash=state["active_work_sha256"],
                )
            if state["mode"] != "ready" or state["current_stage"] == "completed":
                raise AIWorkflowError(
                    code="invalid_state_transition",
                    message="Current workflow state cannot prepare work.",
                    exit_code=6,
                    details={"stage": state["current_stage"], "mode": state["mode"]},
                )

            stage = state["current_stage"]
            active_item = self._default_active_item(stage, active_item)
            defaults = self._default_work_context(
                stage,
                active_item=active_item,
                instruction=instruction,
            )
            goal = goal if goal is not None else defaults["goal"]
            inputs = list(defaults["inputs"] if inputs is None else inputs)
            depends_on = list(defaults["depends_on"] if depends_on is None else depends_on)
            sources = list(defaults["sources"] if sources is None else sources)
            constraints = list(
                defaults["constraints"] if constraints is None else constraints
            )
            stage_guide_path = stage_guide or defaults["stage_guide"]
            facts = defaults.get("facts")
            repository_context = start_repository_session(
                defaults["repository_context"]
            )
            self._validate_active_item(stage, active_item)
            self._validate_dependencies(depends_on)
            for path in [*inputs, *sources]:
                self.store.safe_path(path)

            work_id = self._next_work_id()
            artifact_id = artifact_identity(stage, active_item)[0]
            work = build_work(
                work_id=work_id,
                stage=stage,
                active_item=active_item,
                goal=goal,
                inputs=list(inputs),
                depends_on=list(depends_on),
                sources=list(sources),
                stage_guide=load_stage_guide(stage_guide_path, stage=stage),
                constraints=list(constraints),
                memory_content=self._work_memory_content(
                    depends_on,
                    artifact_ids={artifact_id},
                ),
                target_platform=self.store.read_json("project.json")["platform"],
                facts=facts,
                repository_context=repository_context,
                feedback=instruction.strip() or None,
            )
            artifact = find_artifact(
                self.store.read_json("artifacts.json"), work["artifact"]["id"]
            )
            memory_delta_applied = False
            if artifact is not None:
                if artifact["status"] == "approved":
                    raise AIWorkflowError(
                        code="artifact_already_approved",
                        message="Approved artifacts require an explicit revision request.",
                        exit_code=6,
                        details={"artifact_id": artifact["id"]},
                    )
                verify_artifact_integrity(self.store.root, artifact)
                memory_delta_applied = artifact["approved_revision"] == artifact["revision"]
                if memory_delta_applied:
                    work = self._with_affected_memory(work, artifact)

            updated_state = dict(state)
            work_bytes = json_bytes(work)
            updated_state.update(
                {
                    "mode": "working",
                    "active_item": active_item,
                    "active_work": work_id,
                    "active_work_sha256": sha256_content(work_bytes),
                    "updated_at": now_iso(),
                }
            )
            changes: dict[str, bytes | None] = {
                self._work_path(work_id, "work.json"): work_bytes,
                work["result_output"]: json_bytes(work["result_seed"]),
                ".aiwf/state.json": json_bytes(updated_state),
            }

            if artifact is not None:
                changes[work["draft_output"]] = self.store.safe_path(artifact["path"]).read_bytes()
                changes[work["result_output"]] = self._editable_result_bytes(
                    artifact,
                    preserve_memory_delta=not memory_delta_applied,
                )

            request_digest = self._digest({"work": work})
            self.store.commit_locked(
                changes,
                event_type="work_prepared",
                event_data={"work_id": work_id, "stage": stage, "active_item": active_item},
                command_key=f"prepare:{work_id}",
                request_digest=request_digest,
            )
            return work

    def _default_active_item(self, stage: str, requested: str | None) -> str | None:
        if stage in {"analysis", "design"}:
            if requested is not None:
                raise AIWorkflowError(
                    code="invalid_active_item",
                    message=f"Stage '{stage}' does not accept a task id.",
                    exit_code=4,
                )
            return None
        if stage == "implementation":
            return self._next_implementation_task(requested)
        if stage == "testing":
            return self._next_testing_task(requested)
        if stage != "specification":
            return requested
        task_plan = find_artifact(self.store.read_json("artifacts.json"), "task-plan")
        if task_plan is None or task_plan["status"] != "approved":
            if requested is not None:
                raise AIWorkflowError(
                    code="task_plan_required",
                    message="Approve the task plan before selecting an individual specification.",
                    exit_code=6,
                    details={"id": requested},
                )
            return None
        tasks = self.store.read_json("tasks.json")["items"]
        eligible = [item for item in tasks if item["status"] in {"planned", "stale"}]
        if requested is not None:
            if not any(item["id"] == requested for item in eligible):
                raise AIWorkflowError(
                    code="task_not_ready",
                    message="Requested task is not ready for specification.",
                    exit_code=6,
                    details={"id": requested, "stage": stage},
                )
            return requested
        if not eligible:
            raise AIWorkflowError(
                code="no_pending_task",
                message="No task is ready for specification.",
                exit_code=6,
                details={"stage": stage},
            )
        return min(item["id"] for item in eligible)

    def _next_implementation_task(self, requested: str | None) -> str:
        tasks = self.store.read_json("tasks.json")["items"]
        by_id = {item["id"]: item for item in tasks}
        eligible = [
            item
            for item in tasks
            if item["status"] in {"in_progress", "stale"}
            and all(
                by_id[dependency]["status"] in {"implemented", "tested"}
                for dependency in item["depends_on"]
            )
        ]
        return self._select_task(eligible, requested, stage="implementation")

    def _next_testing_task(self, requested: str | None) -> str:
        tasks = self.store.read_json("tasks.json")["items"]
        eligible = [item for item in tasks if item["status"] in {"implemented", "stale"}]
        return self._select_task(eligible, requested, stage="testing")

    def _select_task(
        self,
        eligible: Sequence[dict[str, Any]],
        requested: str | None,
        *,
        stage: str,
    ) -> str:
        if requested is not None:
            if not any(item["id"] == requested for item in eligible):
                raise AIWorkflowError(
                    code="task_not_ready",
                    message=f"Requested task is not ready for {stage}.",
                    exit_code=6,
                    details={"id": requested, "stage": stage},
                )
            return requested
        if not eligible:
            raise AIWorkflowError(
                code="no_pending_task",
                message=f"No task is ready for {stage}.",
                exit_code=6,
                details={"stage": stage},
            )
        return min(item["id"] for item in eligible)

    def _default_work_context(
        self,
        stage: str,
        *,
        active_item: str | None,
        instruction: str,
    ) -> dict[str, Any]:
        return build_stage_context(
            stage,
            active_item=active_item,
            instruction=instruction,
            project=self.store.read_json("project.json"),
            approved_artifact=self._approved_artifact,
            current_requirements=self._current_requirements,
            task_by_id=self._task,
            task_facts=self._task_facts,
        )

    def _assert_operation_allowed_locked(self, operation: str) -> None:
        assert_operation_allowed(operation, self._inspect_locked())

    def _work_memory_content(
        self,
        dependencies: Sequence[str],
        *,
        artifact_ids: set[str] | None = None,
        decisions: Mapping[str, Any] | None = None,
    ) -> str:
        artifacts = self.store.read_json("artifacts.json")
        sources = upstream_references(
            {"depends_on": list(dependencies)}, artifacts
        ) if dependencies else set()
        return render_memory(
            self.store.read_json("memory.json"),
            dict(decisions) if decisions is not None else self.store.read_json("decisions.json"),
            sources=sources,
            artifact_ids=artifact_ids or set(),
        )

    def _verify_repository_result(
        self,
        work: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        stage = work["stage"]
        field_name = {
            "implementation": "changed_files",
            "testing": "test_files",
        }.get(stage)
        if field_name is None:
            return None

        session = work.get("repository_context")
        if not isinstance(session, dict):
            raise AIWorkflowError(
                code="repository_context_missing",
                message="Implementation and testing work require a repository baseline.",
                exit_code=6,
            )
        declared = [normalize_repository_path(path) for path in result[field_name]]
        current = inspect_repository(session["root"])
        comparison = compare_repository_session(session, current)
        observed = comparison["changed_files"]
        if observed is not None and set(declared) != set(observed):
            raise AIWorkflowError(
                code="repository_change_mismatch",
                message="Reported files do not match repository changes made during this work.",
                exit_code=4,
                details={
                    "field": field_name,
                    "reported": sorted(declared),
                    "observed": observed,
                    "unreported": sorted(set(observed) - set(declared)),
                    "not_observed": sorted(set(declared) - set(observed)),
                },
            )
        validate_stage_file_roles(stage, declared)
        return {
            "level": comparison["verification_level"],
            "observed_files": observed,
        }

    def _task(self, task_id: str | None) -> dict[str, Any]:
        task = next(
            (
                item
                for item in self.store.read_json("tasks.json")["items"]
                if item["id"] == task_id
            ),
            None,
        )
        if task is None:
            raise AIWorkflowError(
                code="unknown_task_id",
                message="Task does not exist.",
                exit_code=4,
                details={"id": task_id},
            )
        return task

    def _task_facts(self, task: dict[str, Any]) -> dict[str, Any]:
        requirement_by_id = {
            item["id"]: item for item in self.store.read_json("requirements.json")["items"]
        }
        return {
            "task": task,
            "requirements": [requirement_by_id[item_id] for item_id in task["requirements"]],
        }

    def _current_requirements(self) -> list[dict[str, Any]]:
        return [
            item
            for item in self.store.read_json("requirements.json")["items"]
            if item["disposition"] == "accepted"
        ]

    def _verify_semantic_evidence(
        self,
        work: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        repository = work.get("repository_context")
        if not isinstance(repository, dict):
            raise AIWorkflowError(
                code="repository_context_missing",
                message="Semantic work requires repository context for evidence validation.",
                exit_code=6,
            )
        evidence: list[Mapping[str, Any]] = []
        for operation in result["memory_delta"]:
            evidence.extend(operation["evidence"])
        if work["stage"] == "analysis":
            for requirement in result["requirements"]:
                repository_sources = [
                    source
                    for source in requirement["sources"]
                    if source["kind"] == "repository"
                ]
                if (
                    requirement["change_type"] in {"modify", "reuse"}
                    and requirement["platform_scope"] != "other"
                    and not repository_sources
                ):
                    raise AIWorkflowError(
                        code="repository_source_required",
                        message="Modified or reused requirements require repository evidence.",
                        exit_code=4,
                        details={"title": requirement["title"]},
                    )
                for source in repository_sources:
                    path, separator, symbol = source["ref"].rpartition("#")
                    if not separator or not path or not symbol:
                        raise AIWorkflowError(
                            code="invalid_repository_source",
                            message="Repository requirement sources must use '<path>#<symbol>'.",
                            exit_code=4,
                            details={"ref": source["ref"]},
                        )
                    evidence.append({"path": path, "symbol": symbol})
        elif work["stage"] == "design":
            if result["design_mode"] == "greenfield":
                if repository_has_files(repository["root"]):
                    raise AIWorkflowError(
                        code="invalid_greenfield_design",
                        message="Greenfield design is only allowed for an empty configured repository.",
                        exit_code=4,
                        details={"root": repository["root"]},
                    )
            else:
                evidence.extend(result["code_evidence"])
        if evidence:
            validate_repository_evidence(repository["root"], evidence)

    def _approved_artifact(self, artifact_id: str) -> dict[str, Any]:
        artifact = find_artifact(self.store.read_json("artifacts.json"), artifact_id)
        if (
            artifact is None
            or artifact["status"] != "approved"
            or artifact["approved_revision"] is None
        ):
            raise AIWorkflowError(
                code="unavailable_dependency",
                message="Required upstream artifact is not approved.",
                exit_code=6,
                details={"artifact_id": artifact_id},
            )
        verify_artifact_integrity(self.store.root, artifact)
        return artifact

    def submit_work(self, work_id: str) -> dict[str, Any]:
        command_key = f"submit:{work_id}"
        with self.store.lock(exclusive=True):
            self._recover_transactions_locked()
            existing_event = self.store.find_event(command_key)
            if existing_event is not None:
                shutil.rmtree(self.store.data_root / "work" / work_id, ignore_errors=True)
                return dict(existing_event["data"])
            if self.store.find_event(f"question:{work_id}") is not None:
                raise AIWorkflowError(
                    code="work_already_terminated",
                    message="Blocked work cannot be submitted.",
                    exit_code=6,
                    details={"work_id": work_id},
                )
            self._assert_operation_allowed_locked("submit")

            state = self.store.read_json("state.json")
            if state["mode"] != "working" or state["active_work"] != work_id:
                raise AIWorkflowError(
                    code="invalid_state_transition",
                    message="Only the active work can be submitted.",
                    exit_code=6,
                    details={"work_id": work_id},
                )
            work = self._read_work(work_id, expected_hash=state["active_work_sha256"])
            draft_path = self.store.safe_path(work["draft_output"])
            result_path = self.store.safe_path(work["result_output"])
            try:
                draft_bytes = draft_path.read_bytes()
                raw_result_bytes = result_path.read_bytes()
                raw_result = json.loads(raw_result_bytes.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise AIWorkflowError(
                    code="incomplete_work",
                    message="Work draft and result manifest must both be valid files.",
                    exit_code=4,
                    details={"work_id": work_id},
                ) from error
            if not draft_bytes.strip():
                raise AIWorkflowError(
                    code="incomplete_work",
                    message="Semantic artifact draft cannot be empty.",
                    exit_code=4,
                    details={"work_id": work_id},
                )

            result = validate_result_manifest(
                work["stage"], raw_result, active_item=work["active_item"]
            )
            project = self.store.read_json("project.json")
            requirements = self.store.read_json("requirements.json")
            tasks = self.store.read_json("tasks.json")
            artifacts = self.store.read_json("artifacts.json")
            decisions = self.store.read_json("decisions.json")
            artifact_id = work["artifact"]["id"]
            current_artifact = find_artifact(artifacts, artifact_id)
            if current_artifact is not None:
                verify_artifact_integrity(self.store.root, current_artifact)
            revision = 1 if current_artifact is None else current_artifact["revision"] + 1
            if work["stage"] == "analysis":
                result = {
                    **result,
                    "requirements": normalize_requirement_sources(
                        result["requirements"],
                        workspace_root=self.store.root,
                        work=work,
                        decisions=decisions,
                        artifact_ref=f"{artifact_id}@{revision}",
                        archived_work_ids=self._archived_work_ids(artifacts),
                    ),
                }
            validate_active_decision_ids(
                decisions, result.get("superseded_decisions", [])
            )
            self._verify_semantic_evidence(work, result)

            if work["stage"] == "analysis":
                if result["target_platform"] != project["platform"]:
                    raise AIWorkflowError(
                        code="target_platform_mismatch",
                        message="Analysis target_platform must match the initialized project platform.",
                        exit_code=4,
                        details={
                            "expected": project["platform"],
                            "actual": result["target_platform"],
                        },
                    )
                requirements, result = reconcile_requirements(
                    requirements, result, revision=revision
                )
            elif work["stage"] == "design":
                validate_design_coverage(requirements, result)
            elif work["stage"] == "specification" and work["active_item"] is None:
                tasks, result = reconcile_tasks(
                    tasks, requirements, result, revision=revision
                )
            repository_verification = self._verify_repository_result(work, result)
            result = {
                **result,
                "artifact_id": artifact_id,
                "artifact_type": work["artifact"]["type"],
                "revision": revision,
                "depends_on": list(work["depends_on"]),
                "sources": list(work["sources"]),
            }
            if repository_verification is not None:
                result["repository_verification"] = repository_verification
            normalized_result_bytes = json_bytes(result)
            work_snapshot_bytes = json_bytes(work)

            invalidated: list[str] = []
            semantic_changed = False
            memory_changed = False
            decision_changed = False
            content_changed = False
            dependency_changed = False
            if current_artifact is not None:
                current_result = self.store.read_json_path(current_artifact["result_path"])
                semantic_changed = semantic_result_hash(current_result) != semantic_result_hash(result)
                memory_changed = bool(result["memory_delta"])
                decision_changed = bool(result.get("superseded_decisions"))
                content_changed = current_artifact["content_sha256"] != sha256_content(
                    draft_bytes
                )
                dependency_changed = current_artifact["depends_on"] != list(
                    work["depends_on"]
                )
            if (
                current_artifact is not None
                and current_artifact["approved_revision"] is not None
                and not (
                    content_changed
                    or semantic_changed
                    or memory_changed
                    or decision_changed
                    or dependency_changed
                )
            ):
                raise AIWorkflowError(
                    code="revision_has_no_changes",
                    message="A new revision must change its artifact, result, memory, decisions, or dependencies.",
                    exit_code=4,
                    details={"artifact_id": artifact_id},
                )
            if (
                current_artifact is not None
                and current_artifact["approved_revision"] is not None
                and (
                    content_changed
                    or semantic_changed
                    or memory_changed
                    or decision_changed
                    or dependency_changed
                )
            ):
                artifacts, invalidated = invalidate_downstream(
                    artifacts,
                    artifact_id=artifact_id,
                    revision=current_artifact["approved_revision"],
                )
                tasks = self._mark_tasks_stale(
                    tasks,
                    invalidated,
                    work["stage"],
                    active_item=work["active_item"],
                )

            timestamp = now_iso()
            registered = {
                "id": artifact_id,
                "type": work["artifact"]["type"],
                "stage": work["stage"],
                "active_item": work["active_item"],
                "path": work["artifact"]["output"],
                "snapshot_path": f".aiwf/history/{artifact_id}/{revision}.md",
                "result_path": f".aiwf/results/{artifact_id}/{revision}.json",
                "work_path": f".aiwf/history/{artifact_id}/{revision}.work.json",
                "content_sha256": sha256_content(draft_bytes),
                "result_sha256": sha256_content(normalized_result_bytes),
                "work_sha256": sha256_content(work_snapshot_bytes),
                "status": "review",
                "revision": revision,
                "approved_revision": (
                    current_artifact["approved_revision"] if current_artifact is not None else None
                ),
                "depends_on": list(work["depends_on"]),
                "sources": list(work["sources"]),
                "updated_at": timestamp,
            }
            artifacts = replace_artifact(artifacts, registered)
            reviewed_ref = f"{artifact_id}@{revision}"
            updated_state = dict(state)
            updated_state.update(
                {
                    "mode": "review",
                    "active_work": None,
                    "active_work_sha256": None,
                    "pending_reviews": [reviewed_ref],
                    "blocking_questions": [],
                    "updated_at": timestamp,
                }
            )
            changes: dict[str, bytes | None] = {
                ".aiwf/requirements.json": json_bytes(requirements),
                ".aiwf/tasks.json": json_bytes(tasks),
                ".aiwf/artifacts.json": json_bytes(artifacts),
                ".aiwf/state.json": json_bytes(updated_state),
                registered["path"]: draft_bytes,
                registered["snapshot_path"]: draft_bytes,
                registered["result_path"]: normalized_result_bytes,
                registered["work_path"]: work_snapshot_bytes,
            }

            request_digest = sha256_bytes(draft_bytes + b"\0" + raw_result_bytes)
            additional_events: list[tuple[str, Mapping[str, Any]]] = []
            if invalidated:
                additional_events.append(
                    (
                        "downstream_invalidated",
                        {"source": reviewed_ref, "artifacts": invalidated},
                    )
                )
            event = self.store.commit_locked(
                changes,
                event_type="artifact_submitted",
                event_data={
                    "work_id": work_id,
                    "artifact_id": artifact_id,
                    "revision": revision,
                    "invalidated": invalidated,
                },
                command_key=command_key,
                request_digest=request_digest,
                additional_events=additional_events,
            )
            shutil.rmtree(self.store.data_root / "work" / work_id, ignore_errors=True)
            return dict(event["data"])

    def review_artifact(
        self,
        artifact_id: str,
        revision: int,
        *,
        outcome: str,
        feedback: str = "",
    ) -> dict[str, Any]:
        if outcome == "changes_requested":
            return self._request_changes(
                artifact_id,
                revision,
                feedback=feedback,
                command_prefix="review",
            )
        if outcome != "approved":
            raise AIWorkflowError(
                code="invalid_review_outcome",
                message="Review outcome must be approved or changes_requested.",
                exit_code=4,
            )
        command_key = f"review:{artifact_id}@{revision}:approved"
        request_digest = self._digest(
            {"artifact_id": artifact_id, "revision": revision, "outcome": outcome}
        )
        with self.store.lock(exclusive=True):
            self._recover_transactions_locked()
            existing_event = self.store.find_event(command_key)
            if existing_event is not None:
                return dict(existing_event["data"])
            self._reject_conflicting_review(artifact_id, revision, command_key)
            state = self.store.read_json("state.json")
            artifacts = self.store.read_json("artifacts.json")
            artifact = find_artifact(artifacts, artifact_id)
            reviewed_ref = f"{artifact_id}@{revision}"
            if (
                artifact is None
                or artifact["revision"] != revision
                or artifact["status"] != "review"
                or reviewed_ref not in state["pending_reviews"]
            ):
                raise AIWorkflowError(
                    code="invalid_state_transition",
                    message="Artifact revision is not awaiting review.",
                    exit_code=6,
                    details={"artifact_id": artifact_id, "revision": revision},
                )
            verify_artifact_integrity(self.store.root, artifact)
            self._assert_operation_allowed_locked("approve")
            result = self.store.read_json_path(artifact["result_path"])
            memory = apply_memory_delta(
                self.store.read_json("memory.json"),
                result["memory_delta"],
                source=reviewed_ref,
            )
            requirements, tasks = approve_indexes(
                stage=artifact["stage"],
                revision=revision,
                active_item=artifact["active_item"],
                requirements=self.store.read_json("requirements.json"),
                tasks=self.store.read_json("tasks.json"),
            )
            approved_artifact = dict(artifact)
            approved_artifact.update(
                {
                    "status": "approved",
                    "approved_revision": revision,
                    "updated_at": now_iso(),
                }
            )
            artifacts = replace_artifact(artifacts, approved_artifact)
            updated_state, stage_advanced = advance_after_approval(
                state,
                artifacts,
                requirements,
                tasks,
                stage=artifact["stage"],
                reviewed_ref=reviewed_ref,
            )
            projected_issues = [
                self._classify_health_issue(issue)
                for issue in semantic_health_issues(
                    requirements=requirements,
                    tasks=tasks,
                    artifacts=artifacts,
                    artifact_results=self._semantic_artifact_results(
                        artifacts,
                        overrides={artifact_id: result},
                    ),
                )
            ]
            if any(issue["blocking"] for issue in projected_issues):
                raise_health_blocked(projected_issues)
            decisions = supersede_decisions_by_artifact(
                self.store.read_json("decisions.json"),
                result.get("superseded_decisions", []),
                artifact_ref=reviewed_ref,
            )
            changes = {
                ".aiwf/artifacts.json": json_bytes(artifacts),
                ".aiwf/requirements.json": json_bytes(requirements),
                ".aiwf/tasks.json": json_bytes(tasks),
                ".aiwf/memory.json": json_bytes(memory),
                ".aiwf/decisions.json": json_bytes(decisions),
                ".aiwf/memory.md": render_memory(memory, decisions).encode("utf-8"),
                ".aiwf/state.json": json_bytes(updated_state),
            }
            additional_events: list[tuple[str, Mapping[str, Any]]] = []
            if stage_advanced:
                additional_events.append(
                    (
                        "stage_advanced",
                        {
                            "from": state["current_stage"],
                            "to": updated_state["current_stage"],
                        },
                    )
                )
            event = self.store.commit_locked(
                changes,
                event_type="artifact_approved",
                event_data={
                    "artifact_id": artifact_id,
                    "revision": revision,
                    "stage": artifact["stage"],
                    "current_stage": updated_state["current_stage"],
                },
                command_key=command_key,
                request_digest=request_digest,
                additional_events=additional_events,
            )
            return dict(event["data"])

    def request_revision(
        self,
        artifact_id: str,
        revision: int,
        *,
        feedback: str,
        supersede_active_work: bool = False,
    ) -> dict[str, Any]:
        return self._request_changes(
            artifact_id,
            revision,
            feedback=feedback,
            command_prefix="revise",
            supersede_active_work=supersede_active_work,
        )

    def resolve_artifact_drift(
        self,
        artifact_id: str,
        revision: int,
        *,
        outcome: str,
        feedback: str = "",
        supersede_active_work: bool = False,
    ) -> dict[str, Any]:
        if outcome == "adopt":
            return self._request_changes(
                artifact_id,
                revision,
                feedback=feedback,
                command_prefix="resolve-drift",
                supersede_active_work=supersede_active_work,
                adopt_content_drift=True,
            )
        if outcome != "discard":
            raise AIWorkflowError(
                code="invalid_drift_outcome",
                message="Artifact drift outcome must be adopt or discard.",
                exit_code=4,
            )
        return self._discard_artifact_drift(
            artifact_id,
            revision,
            supersede_active_work=supersede_active_work,
        )

    def open_questions(
        self,
        work_id: str,
        questions: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        command_key = f"question:{work_id}"
        request_digest = self._digest({"questions": list(questions)})
        with self.store.lock(exclusive=True):
            self._recover_transactions_locked()
            existing_event = self.store.find_event(command_key)
            if existing_event is not None:
                if existing_event["request_digest"] != request_digest:
                    raise AIWorkflowError(
                        code="idempotency_conflict",
                        message="Question set changed for an already terminated work.",
                        exit_code=6,
                    )
                return dict(existing_event["data"])
            if self.store.find_event(f"submit:{work_id}") is not None:
                raise AIWorkflowError(
                    code="work_already_terminated",
                    message="Submitted work cannot open blocking questions.",
                    exit_code=6,
                )
            if not questions:
                raise AIWorkflowError(
                    code="invalid_questions",
                    message="At least one blocking question is required.",
                    exit_code=4,
                )
            state = self.store.read_json("state.json")
            if state["mode"] != "working" or state["active_work"] != work_id:
                raise AIWorkflowError(
                    code="invalid_state_transition",
                    message="Only active work can open blocking questions.",
                    exit_code=6,
                )
            work = self._read_work(work_id, expected_hash=state["active_work_sha256"])
            if "repository_context" in work:
                work = {
                    **work,
                    "repository_context": checkpoint_repository_session(
                        work["repository_context"]
                    ),
                }
            question_document = self.store.read_json("questions.json")
            existing_ids = [item["id"] for item in question_document["items"]]
            created: list[dict[str, Any]] = []
            timestamp = now_iso()
            for raw_question in questions:
                question = normalize_question(raw_question)
                validate_active_decision_ids(
                    self.store.read_json("decisions.json"),
                    question["supersedes_decisions"],
                )
                question_id = next_id("question", existing_ids)
                existing_ids.append(question_id)
                created.append(
                    {
                        "id": question_id,
                        **question,
                        "stage": work["stage"],
                        "active_item": work["active_item"],
                        "work_id": work_id,
                        "status": "open",
                        "decision_id": None,
                        "created_at": timestamp,
                    }
                )
            updated_questions = {
                "schema_version": SCHEMA_VERSION,
                "items": [*question_document["items"], *created],
            }
            updated_work = dict(work)
            updated_work["status"] = "blocked"
            updated_work_bytes = json_bytes(updated_work)
            updated_state = dict(state)
            updated_state.update(
                {
                    "mode": "blocked",
                    "blocking_questions": [item["id"] for item in created],
                    "active_work_sha256": sha256_content(updated_work_bytes),
                    "updated_at": timestamp,
                }
            )
            event = self.store.commit_locked(
                {
                    ".aiwf/questions.json": json_bytes(updated_questions),
                    self._work_path(work_id, "work.json"): updated_work_bytes,
                    ".aiwf/state.json": json_bytes(updated_state),
                },
                event_type="question_opened",
                event_data={"work_id": work_id, "question_ids": updated_state["blocking_questions"]},
                command_key=command_key,
                request_digest=request_digest,
            )
            return dict(event["data"])

    def decide(self, question_id: str, decision: str) -> dict[str, Any]:
        if not decision.strip():
            raise AIWorkflowError(
                code="invalid_decision",
                message="Decision text cannot be empty.",
                exit_code=4,
            )
        command_key = f"decide:{question_id}"
        request_digest = self._digest({"question_id": question_id, "decision": decision})
        with self.store.lock(exclusive=True):
            self._recover_transactions_locked()
            existing_event = self.store.find_event(command_key)
            if existing_event is not None:
                if existing_event["request_digest"] != request_digest:
                    raise AIWorkflowError(
                        code="idempotency_conflict",
                        message="Question already has a different recorded decision.",
                        exit_code=6,
                    )
                return dict(existing_event["data"])
            questions = self.store.read_json("questions.json")
            question = next((item for item in questions["items"] if item["id"] == question_id), None)
            if question is None or question["status"] != "open":
                raise AIWorkflowError(
                    code="invalid_state_transition",
                    message="Question is not open for a decision.",
                    exit_code=6,
                    details={"question_id": question_id},
                )
            state = self.store.read_json("state.json")
            if state["mode"] != "blocked":
                raise AIWorkflowError(
                    code="invalid_state_transition",
                    message="Decisions can only resolve the blocked active work.",
                    exit_code=6,
                )
            if question_id not in state["blocking_questions"]:
                raise AIWorkflowError(
                    code="invalid_state_transition",
                    message="Question is not blocking the active work.",
                    exit_code=6,
                )
            decisions = self.store.read_json("decisions.json")
            decision_id = next_id("decision", [item["id"] for item in decisions["items"]])
            timestamp = now_iso()
            decision_item = {
                "id": decision_id,
                "question_id": question_id,
                "decision": decision,
                "impact": list(question["impact"]),
                "status": "active",
                "supersedes": list(question["supersedes_decisions"]),
                "superseded_by": None,
                "created_at": timestamp,
            }
            updated_decisions = append_decision(decisions, decision_item)
            updated_question_items = []
            for item in questions["items"]:
                if item["id"] == question_id:
                    item = {**item, "status": "resolved", "decision_id": decision_id}
                updated_question_items.append(item)
            updated_questions = {
                "schema_version": SCHEMA_VERSION,
                "items": updated_question_items,
            }
            remaining = [item for item in state["blocking_questions"] if item != question_id]
            updated_state = dict(state)
            updated_state["blocking_questions"] = remaining
            updated_state["updated_at"] = timestamp
            updated_memory_bytes = render_memory(
                self.store.read_json("memory.json"), updated_decisions
            ).encode("utf-8")
            changes: dict[str, bytes | None] = {
                ".aiwf/questions.json": json_bytes(updated_questions),
                ".aiwf/decisions.json": json_bytes(updated_decisions),
                ".aiwf/memory.md": updated_memory_bytes,
            }
            previous_work_id = state["active_work"]
            previous_work = self._read_work(
                previous_work_id,
                expected_hash=state["active_work_sha256"],
            )
            scoped_memory = self._work_memory_content(
                previous_work["depends_on"],
                artifact_ids={previous_work["artifact"]["id"]},
                decisions=updated_decisions,
            )
            updated_work = {
                **previous_work,
                "memory_context": {
                    "sha256": sha256_content(scoped_memory.encode("utf-8")),
                    "content": scoped_memory,
                },
            }
            updated_work_bytes = json_bytes(updated_work)
            changes[self._work_path(previous_work_id, "work.json")] = updated_work_bytes
            updated_state["active_work_sha256"] = sha256_content(updated_work_bytes)
            if not remaining:
                updated_state["mode"] = "decision"
            changes[".aiwf/state.json"] = json_bytes(updated_state)
            event = self.store.commit_locked(
                changes,
                event_type="decision_recorded",
                event_data={
                    "question_id": question_id,
                    "decision_id": decision_id,
                    "work_id": previous_work_id,
                    "routing_required": not remaining,
                },
                command_key=command_key,
                request_digest=request_digest,
            )
            return dict(event["data"])

    def route_decision(
        self,
        work_id: str,
        *,
        outcome: str,
        artifact_id: str | None = None,
        revision: int | None = None,
    ) -> dict[str, Any]:
        if outcome == "resume":
            if artifact_id is not None or revision is not None:
                raise AIWorkflowError(
                    code="invalid_decision_route",
                    message="Resume does not accept an artifact target.",
                    exit_code=2,
                )
            return self._resume_after_decisions(work_id)
        if outcome != "revise":
            raise AIWorkflowError(
                code="invalid_decision_route",
                message="Decision route must be resume or revise.",
                exit_code=2,
            )
        if artifact_id is None or revision is None:
            raise AIWorkflowError(
                code="invalid_decision_route",
                message="Revision routing requires an artifact id and revision.",
                exit_code=2,
            )
        return self._request_changes(
            artifact_id,
            revision,
            feedback="",
            command_prefix="route-decision",
            supersede_active_work=True,
            decision_work_id=work_id,
        )

    def route_upstream(
        self,
        work_id: str,
        *,
        artifact_id: str,
        revision: int,
        correction: str,
        evidence: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if not correction.strip():
            raise AIWorkflowError(
                code="invalid_upstream_correction",
                message="Upstream factual correction must explain the incorrect fact.",
                exit_code=2,
            )
        if not evidence:
            raise AIWorkflowError(
                code="invalid_upstream_correction",
                message="Upstream factual correction requires repository evidence.",
                exit_code=2,
            )
        return self._request_changes(
            artifact_id,
            revision,
            feedback=correction,
            command_prefix="route-upstream",
            supersede_active_work=True,
            upstream_work_id=work_id,
            upstream_evidence=evidence,
        )

    def _resume_after_decisions(self, work_id: str) -> dict[str, Any]:
        command_key = f"route-decision:{work_id}"
        request_digest = self._digest({"work_id": work_id, "outcome": "resume"})
        with self.store.lock(exclusive=True):
            self._recover_transactions_locked()
            existing_event = self.store.find_event(command_key)
            if existing_event is not None:
                if existing_event["request_digest"] != request_digest:
                    raise AIWorkflowError(
                        code="idempotency_conflict",
                        message="Decision work already has a different route.",
                        exit_code=6,
                    )
                return dict(existing_event["data"])
            self._assert_operation_allowed_locked("decision_resume")
            state = self.store.read_json("state.json")
            validate_decision_state(state, work_id)
            decisions = resolved_decisions_for_work(
                work_id,
                self.store.read_json("questions.json"),
                self.store.read_json("decisions.json"),
            )
            previous_work = self._read_work(
                work_id,
                expected_hash=state["active_work_sha256"],
            )
            successor_work_id = self._next_work_id()
            repository_context = (
                resume_repository_session(previous_work["repository_context"])
                if "repository_context" in previous_work
                else None
            )
            successor = copy_successor_work(
                previous_work,
                work_id=successor_work_id,
                memory_content=self._work_memory_content(
                    previous_work["depends_on"],
                    artifact_ids={previous_work["artifact"]["id"]},
                ),
                repository_context=repository_context,
            )
            successor_bytes = json_bytes(successor)
            changes: dict[str, bytes | None] = {
                self._work_path(successor_work_id, "work.json"): successor_bytes,
            }
            for source_name, target_name in (
                (previous_work["draft_output"], successor["draft_output"]),
                (previous_work["result_output"], successor["result_output"]),
            ):
                source_path = self.store.safe_path(source_name)
                if source_path.is_file():
                    changes[target_name] = source_path.read_bytes()
            updated_state = {
                **state,
                "mode": "working",
                "active_work": successor_work_id,
                "active_work_sha256": sha256_content(successor_bytes),
                "updated_at": now_iso(),
            }
            changes[".aiwf/state.json"] = json_bytes(updated_state)
            event = self.store.commit_locked(
                changes,
                event_type="decision_route_selected",
                event_data={
                    "work_id": work_id,
                    "outcome": "resume",
                    "decision_ids": [item["decision"]["id"] for item in decisions],
                    "successor_work_id": successor_work_id,
                },
                command_key=command_key,
                request_digest=request_digest,
            )
            shutil.rmtree(self.store.data_root / "work" / work_id, ignore_errors=True)
            return dict(event["data"])

    def inspect(self) -> dict[str, Any]:
        with self.store.lock(exclusive=False):
            return self._inspect_locked()

    def _inspect_locked(self) -> dict[str, Any]:
        if self.store.has_pending_transactions():
            return {"status": "needs_recovery", "workspace": str(self.store.root)}
        self.store.read_events()
        documents = {name: self.store.read_json(name) for name in (
            "project.json",
            "state.json",
            "requirements.json",
            "tasks.json",
            "artifacts.json",
            "decisions.json",
            "questions.json",
            "memory.json",
        )}
        issues: list[dict[str, Any]] = []
        expected_memory = render_memory(
            documents["memory.json"], documents["decisions.json"]
        ).encode("utf-8")
        try:
            actual_memory = self.store.safe_path(".aiwf/memory.md").read_bytes()
        except OSError:
            actual_memory = None
        if actual_memory != expected_memory:
            issues.append(
                {
                    "level": "error",
                    "type": "generated_view_drift",
                    "message": "Generated memory.md does not match its structured sources.",
                    "details": {"path": ".aiwf/memory.md"},
                }
            )
        drifted_artifact_ids: set[str] = set()
        for artifact in documents["artifacts.json"]["items"]:
            artifact_issues = artifact_integrity_issues(self.store.root, artifact)
            if artifact_issues:
                drifted_artifact_ids.add(artifact["id"])
                issues.append(
                    {
                        "level": "error",
                        "type": "artifact_drift",
                        "message": "Registered artifact files do not match their recorded revision.",
                        "details": {
                            "artifact_id": artifact["id"],
                            "revision": artifact["revision"],
                            "artifact_status": artifact["status"],
                            "issues": artifact_issues,
                        },
                    }
                )
        project = documents["project.json"]
        state = documents["state.json"]
        for prd_path in project["prd_files"]:
            try:
                path = self.store.safe_path(prd_path)
            except AIWorkflowError as error:
                issues.append(
                    {
                        "level": "error",
                        "type": error.code,
                        "message": error.message,
                        "details": error.details,
                    }
                )
                continue
            if not path.is_file():
                issues.append(
                    {
                        "level": "error",
                        "type": "prd_missing",
                        "message": "Configured PRD copy is missing.",
                        "details": {"path": prd_path},
                    }
                )
        repository = project["code_repository"]
        if not Path(repository).is_dir():
            issues.append(
                {
                    "level": "error",
                    "type": "code_repository_unavailable",
                    "message": "Configured code repository is not accessible.",
                    "details": {"path": repository},
                }
            )
        artifacts_by_ref = {
            f"{item['id']}@{item['revision']}": item
            for item in documents["artifacts.json"]["items"]
        }
        for reference in state["pending_reviews"]:
            artifact = artifacts_by_ref.get(reference)
            if artifact is None or artifact["status"] != "review":
                issues.append(
                    {
                        "level": "error",
                        "type": "pending_review_mismatch",
                        "message": "Pending review does not match the artifact registry.",
                        "details": {"reference": reference},
                    }
                )
        open_questions = {
            item["id"]: item
            for item in documents["questions.json"]["items"]
            if item["status"] == "open"
        }
        for question_id in state["blocking_questions"]:
            if question_id not in open_questions:
                issues.append(
                    {
                        "level": "error",
                        "type": "blocking_question_mismatch",
                        "message": "Blocking question is not open in the question registry.",
                        "details": {"question_id": question_id},
                    }
                )
        if state["active_work"] is not None:
            try:
                self._read_work(
                    state["active_work"],
                    expected_hash=state["active_work_sha256"],
                )
            except AIWorkflowError as error:
                issues.append(
                    {
                        "level": "error",
                        "type": error.code,
                        "message": error.message,
                        "details": error.details,
                    }
                )
        issues.extend(
            semantic_health_issues(
                requirements=documents["requirements.json"],
                tasks=documents["tasks.json"],
                artifacts=documents["artifacts.json"],
                artifact_results=self._semantic_artifact_results(
                    documents["artifacts.json"],
                    drifted_artifact_ids=drifted_artifact_ids,
                ),
                drifted_artifact_ids=drifted_artifact_ids,
            )
        )
        pending_review_items = [
            artifacts_by_ref[reference]
            for reference in state["pending_reviews"]
            if reference in artifacts_by_ref
        ]
        blocking_question_items = [
            open_questions[question_id]
            for question_id in state["blocking_questions"]
            if question_id in open_questions
        ]
        decisions_by_id = {
            item["id"]: item for item in documents["decisions.json"]["items"]
        }
        decision_context = []
        if state["mode"] == "decision":
            for question in documents["questions.json"]["items"]:
                if (
                    question["work_id"] == state["active_work"]
                    and question["status"] == "resolved"
                    and question["decision_id"] in decisions_by_id
                ):
                    decision = decisions_by_id[question["decision_id"]]
                    decision_context.append(
                        {
                            "question_id": question["id"],
                            "question": question["question"],
                            "decision_id": decision["id"],
                            "decision": decision["decision"],
                            "impact": list(question["impact"]),
                        }
                    )
        issues = [self._classify_health_issue(item) for item in issues]
        can_advance = not any(item["blocking"] for item in issues)
        return {
            "status": "ok" if not issues else "issues_found",
            "workspace": str(self.store.root),
            "project": project,
            "state": state,
            "can_advance": can_advance,
            "next_action": (
                self._next_action(state) if can_advance else "resolve_health_issues"
            ),
            "counts": {
                "prd_files": len(project["prd_files"]),
                "requirements": len(documents["requirements.json"]["items"]),
                "accepted_requirements": sum(
                    item["disposition"] == "accepted"
                    for item in documents["requirements.json"]["items"]
                ),
                "not_accepted_requirements": sum(
                    item["disposition"] in {"proposed", "deferred", "excluded"}
                    for item in documents["requirements.json"]["items"]
                ),
                "withdrawn_requirements": sum(
                    item["disposition"] == "withdrawn"
                    for item in documents["requirements.json"]["items"]
                ),
                "tasks": len(documents["tasks.json"]["items"]),
                "artifacts": len(documents["artifacts.json"]["items"]),
                "open_questions": len(open_questions),
                "decisions": len(documents["decisions.json"]["items"]),
                "active_decisions": sum(
                    item["status"] == "active"
                    for item in documents["decisions.json"]["items"]
                ),
                "superseded_decisions": sum(
                    item["status"] == "superseded"
                    for item in documents["decisions.json"]["items"]
                ),
                "memory_entries": sum(
                    item["status"] == "active" for item in documents["memory.json"]["items"]
                ),
            },
            "pending_reviews": pending_review_items,
            "blocking_questions": blocking_question_items,
            "decision_context": decision_context,
            "issues": issues,
        }

    def _semantic_artifact_results(
        self,
        artifacts: Mapping[str, Any],
        *,
        drifted_artifact_ids: Sequence[str] = (),
        overrides: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Mapping[str, Any]]:
        drifted = set(drifted_artifact_ids)
        override_results = dict(overrides or {})
        results: dict[str, Mapping[str, Any]] = {}
        for artifact_id in semantic_artifact_ids(artifacts):
            if artifact_id in drifted:
                continue
            if artifact_id in override_results:
                results[artifact_id] = override_results[artifact_id]
                continue
            artifact = find_artifact(artifacts, artifact_id)
            if artifact is not None:
                results[artifact_id] = self.store.read_json_path(artifact["result_path"])
        return results

    def render(self) -> dict[str, Any]:
        self.recover()
        inspection = self.inspect()
        with self.store.lock(exclusive=True):
            documents = {
                name: self.store.read_json(name)
                for name in (
                    "project.json",
                    "state.json",
                    "requirements.json",
                    "tasks.json",
                    "artifacts.json",
                    "decisions.json",
                    "questions.json",
                    "memory.json",
                )
            }
            artifact_bodies: dict[str, str] = {}
            for artifact in documents["artifacts.json"]["items"]:
                path = self.store.safe_path(artifact["path"])
                try:
                    artifact_bodies[artifact["id"]] = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    artifact_bodies[artifact["id"]] = "Artifact content is unavailable."
            content = render_dashboard(
                project=documents["project.json"],
                state=documents["state.json"],
                requirements=documents["requirements.json"],
                tasks=documents["tasks.json"],
                artifacts=documents["artifacts.json"],
                questions=documents["questions.json"],
                decisions=documents["decisions.json"],
                memory=documents["memory.json"],
                events=self.store.read_events(),
                artifact_bodies=artifact_bodies,
                next_action=inspection["next_action"],
                can_advance=inspection["can_advance"],
                decision_context=inspection["decision_context"],
                health_issues=inspection["issues"],
            ).encode("utf-8")
            self.store.replace_generated_locked(DASHBOARD_FILENAME, content)
            return {
                "status": "rendered",
                "path": str(self.store.root / DASHBOARD_FILENAME),
                "bytes": len(content),
            }

    def _next_action(self, state: dict[str, Any]) -> str:
        if state["mode"] == "review":
            return "review"
        if state["mode"] == "blocked":
            return "decide"
        if state["mode"] == "decision":
            return "route_decision"
        if state["mode"] == "working":
            return "resume"
        if state["current_stage"] == "specification":
            task_plan = find_artifact(self.store.read_json("artifacts.json"), "task-plan")
            if task_plan is None or task_plan["status"] != "approved":
                return "plan_tasks"
            return "generate_specification"
        return {
            "analysis": "analyze_requirements",
            "design": "design_solution",
            "implementation": "implement_code",
            "testing": "write_unit_tests",
            "completed": "completed",
        }[state["current_stage"]]

    def _classify_health_issue(self, issue: Mapping[str, Any]) -> dict[str, Any]:
        recovery_actions = {
            "generated_view_drift": "recover",
            "artifact_drift": "resolve_drift",
            "design_requirement_mismatch": "revise_design",
            "task_reference_mismatch": "revise_task_plan",
            "uncovered_requirements": "revise_task_plan",
            "code_repository_unavailable": "restore_code_repository",
            "prd_missing": "reinitialize_workspace",
        }
        issue_type = str(issue["type"])
        if issue_type == "artifact_drift":
            details = issue.get("details", {})
            artifact_status = details.get("artifact_status")
            drift_items = details.get("issues", [])
            content_only = (
                len(drift_items) == 1
                and drift_items[0].get("component") == "content"
                and drift_items[0].get("reason") in {"changed", "missing"}
            )
            allowed_outcomes = (
                ["discard"]
                if content_only
                and (
                    artifact_status == "stale"
                    or drift_items[0].get("reason") == "missing"
                )
                else ["adopt", "discard"]
                if content_only
                else []
            )
            recovery_action = (
                f"resolve_{artifact_status}_drift"
                if allowed_outcomes
                else "manual_repair_required"
            )
            return {
                **issue,
                "blocking": True,
                "recoverable": bool(allowed_outcomes),
                "allowed_outcomes": allowed_outcomes,
                "recovery_action": recovery_action,
            }
        return {
            **issue,
            "blocking": issue.get("level") == "error",
            "recovery_action": recovery_actions.get(
                issue_type,
                "manual_repair_required",
            ),
        }

    def _request_changes(
        self,
        artifact_id: str,
        revision: int,
        *,
        feedback: str,
        command_prefix: str,
        supersede_active_work: bool = False,
        adopt_content_drift: bool = False,
        decision_work_id: str | None = None,
        upstream_work_id: str | None = None,
        upstream_evidence: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        decision_route = command_prefix == "route-decision"
        upstream_route = command_prefix == "route-upstream"
        if not decision_route and not feedback.strip():
            raise AIWorkflowError(
                code="invalid_feedback",
                message="Change feedback cannot be empty.",
                exit_code=4,
            )
        if decision_route:
            if decision_work_id is None:
                raise AIWorkflowError(
                    code="invalid_decision_route",
                    message="Decision revision route requires a work id.",
                    exit_code=2,
                )
            command_key = f"route-decision:{decision_work_id}"
            digest_input: dict[str, Any] = {
                "work_id": decision_work_id,
                "outcome": "revise",
                "artifact_id": artifact_id,
                "revision": revision,
            }
        elif upstream_route:
            if upstream_work_id is None:
                raise AIWorkflowError(
                    code="invalid_upstream_correction",
                    message="Upstream correction route requires an active work id.",
                    exit_code=2,
                )
            command_key = f"route-upstream:{upstream_work_id}"
            digest_input = {
                "work_id": upstream_work_id,
                "artifact_id": artifact_id,
                "revision": revision,
                "correction": feedback,
                "evidence": [dict(item) for item in upstream_evidence],
            }
        else:
            command_key = f"{command_prefix}:{artifact_id}@{revision}:changes_requested"
            digest_input = {
                "artifact_id": artifact_id,
                "revision": revision,
                "feedback": feedback,
            }
        if command_prefix in {"revise", "resolve-drift"}:
            digest_input["supersede_active_work"] = supersede_active_work
        if adopt_content_drift:
            digest_input["adopt_content_drift"] = True
        request_digest = self._digest(digest_input)
        with self.store.lock(exclusive=True):
            self._recover_transactions_locked()
            existing_event = self.store.find_event(command_key)
            if existing_event is not None:
                if existing_event["request_digest"] != request_digest:
                    raise AIWorkflowError(
                        code="idempotency_conflict",
                        message="Revision feedback changed for an existing request.",
                        exit_code=6,
                    )
                return dict(existing_event["data"])
            state = self.store.read_json("state.json")
            artifacts = self.store.read_json("artifacts.json")
            artifact = find_artifact(artifacts, artifact_id)
            if artifact is None or artifact["revision"] != revision:
                raise AIWorkflowError(
                    code="unknown_artifact",
                    message="Artifact revision does not exist.",
                    exit_code=4,
                    details={"artifact_id": artifact_id, "revision": revision},
                )
            resolved_decisions: list[dict[str, Any]] = []
            decision_route_audit: dict[str, Any] = {}
            effective_feedback = feedback
            if decision_route:
                validate_decision_state(state, decision_work_id)
                resolved_decisions = resolved_decisions_for_work(
                    decision_work_id,
                    self.store.read_json("questions.json"),
                    self.store.read_json("decisions.json"),
                )
                decision_work = self._read_work(
                    decision_work_id,
                    expected_hash=state["active_work_sha256"],
                )
                decision_route_audit = validate_decision_revision_target(
                    decision_work,
                    artifact,
                    resolved_decisions,
                    artifacts,
                )
                effective_feedback = decision_feedback(resolved_decisions)
            elif upstream_route:
                if state["mode"] != "working" or state["active_work"] != upstream_work_id:
                    raise AIWorkflowError(
                        code="invalid_state_transition",
                        message="Only active semantic work can route a repository-backed upstream correction.",
                        exit_code=6,
                        details={"work_id": upstream_work_id, "mode": state["mode"]},
                    )
                active_work = self._read_work(
                    upstream_work_id,
                    expected_hash=state["active_work_sha256"],
                )
                target_ref = f"{artifact_id}@{revision}"
                upstream = upstream_references(active_work, artifacts)
                if target_ref not in upstream:
                    raise AIWorkflowError(
                        code="invalid_upstream_target",
                        message="Factual correction target must be an approved upstream dependency of the active work.",
                        exit_code=6,
                        details={"target": target_ref, "upstream": sorted(upstream)},
                    )
                repository = active_work.get("repository_context")
                if not isinstance(repository, dict):
                    raise AIWorkflowError(
                        code="repository_context_missing",
                        message="Upstream factual correction requires repository context.",
                        exit_code=6,
                    )
                validate_repository_evidence(repository["root"], upstream_evidence)
                evidence_lines = [
                    f"- {item['path']}#{item['symbol']}" for item in upstream_evidence
                ]
                effective_feedback = "\n".join(
                    [
                        "Correct the upstream factual error using verified repository evidence:",
                        feedback.strip(),
                        "Evidence:",
                        *evidence_lines,
                    ]
                )
            if command_prefix == "review":
                reviewed_ref = f"{artifact_id}@{revision}"
                if artifact["status"] != "review" or reviewed_ref not in state["pending_reviews"]:
                    raise AIWorkflowError(
                        code="invalid_state_transition",
                        message="Artifact is not awaiting review feedback.",
                        exit_code=6,
                    )
                self._reject_conflicting_review(artifact_id, revision, command_key)
            elif adopt_content_drift and artifact["status"] == "review":
                reviewed_ref = f"{artifact_id}@{revision}"
                if state["mode"] != "review" or reviewed_ref not in state["pending_reviews"]:
                    raise AIWorkflowError(
                        code="invalid_state_transition",
                        message="Review artifact drift can only be adopted while that revision awaits review.",
                        exit_code=6,
                    )
            elif adopt_content_drift and artifact["status"] == "changes_requested":
                if state["mode"] not in {"working", "blocked", "decision"}:
                    raise AIWorkflowError(
                        code="invalid_state_transition",
                        message="Changed artifact drift requires its active successor work.",
                        exit_code=6,
                    )
                if not supersede_active_work:
                    raise AIWorkflowError(
                        code="active_work_conflict",
                        message="Adopting external content would replace unfinished revision work.",
                        exit_code=6,
                        details={"active_work": state["active_work"], "mode": state["mode"]},
                    )
            else:
                if artifact["status"] != "approved" or artifact["approved_revision"] != revision:
                    raise AIWorkflowError(
                        code="invalid_state_transition",
                        message="Only the current approved artifact revision can be revised.",
                        exit_code=6,
                    )
                if state["mode"] == "review":
                    raise AIWorkflowError(
                        code="active_review_conflict",
                        message="Resolve the pending artifact review before revising another artifact.",
                        exit_code=6,
                    )
                if state["mode"] != "ready" and not supersede_active_work:
                    raise AIWorkflowError(
                        code="active_work_conflict",
                        message="Revision would replace unfinished work; explicit confirmation is required.",
                        exit_code=6,
                        details={"active_work": state["active_work"], "mode": state["mode"]},
                    )
            adopted_draft: bytes | None = None
            approved_snapshot: bytes | None = None
            if adopt_content_drift:
                issues = artifact_integrity_issues(self.store.root, artifact)
                if not (
                    len(issues) == 1
                    and issues[0]["component"] == "content"
                    and issues[0]["reason"] == "changed"
                ):
                    raise AIWorkflowError(
                        code="artifact_drift_unrecoverable",
                        message="Only isolated semantic artifact content drift can be adopted.",
                        exit_code=7,
                        details={"artifact_id": artifact_id, "issues": issues},
                    )
                adopted_draft = self.store.safe_path(artifact["path"]).read_bytes()
                approved_snapshot = self.store.safe_path(artifact["snapshot_path"]).read_bytes()
                if not adopted_draft.strip():
                    raise AIWorkflowError(
                        code="incomplete_work",
                        message="External artifact content cannot be empty.",
                        exit_code=4,
                        details={"artifact_id": artifact_id},
                    )
            else:
                verify_artifact_integrity(self.store.root, artifact)
            previous_work = self.store.read_json_path(artifact["work_path"])
            validate_work(previous_work)
            work_id = self._next_work_id()
            successor = copy_successor_work(
                previous_work,
                work_id=work_id,
                feedback=effective_feedback,
                memory_content=self._work_memory_content(
                    previous_work["depends_on"],
                    artifact_ids={artifact_id},
                ),
                repository_context=start_repository_session(
                    self.store.read_json("project.json")["code_repository"]
                ),
            )
            validate_work(successor)
            memory_delta_applied = artifact["approved_revision"] == artifact["revision"]
            if memory_delta_applied:
                successor = self._with_affected_memory(successor, artifact)
            updated_artifact = {**artifact, "status": "changes_requested", "updated_at": now_iso()}
            artifacts = replace_artifact(artifacts, updated_artifact)
            updated_state = dict(state)
            updated_state.update(
                {
                    "current_stage": artifact["stage"],
                    "mode": "working",
                    "active_item": artifact["active_item"],
                    "active_work": work_id,
                    "active_work_sha256": sha256_content(json_bytes(successor)),
                    "pending_reviews": [],
                    "blocking_questions": [],
                    "updated_at": now_iso(),
                }
            )
            changes: dict[str, bytes | None] = {
                ".aiwf/artifacts.json": json_bytes(artifacts),
                ".aiwf/state.json": json_bytes(updated_state),
                self._work_path(work_id, "work.json"): json_bytes(successor),
                successor["draft_output"]: (
                    adopted_draft
                    if adopted_draft is not None
                    else self.store.safe_path(artifact["path"]).read_bytes()
                ),
                successor["result_output"]: self._editable_result_bytes(
                    artifact,
                    preserve_memory_delta=not memory_delta_applied,
                ),
            }
            if approved_snapshot is not None:
                changes[artifact["path"]] = approved_snapshot
            additional_events: list[tuple[str, Mapping[str, Any]]] = []
            superseded_work_id: str | None = None
            if command_prefix in {"revise", "resolve-drift", "route-decision", "route-upstream"} and state["mode"] in {
                "working",
                "blocked",
                "decision",
            }:
                superseded_work_id = state["active_work"]
                previous_active = self._read_work(
                    superseded_work_id,
                    expected_hash=state["active_work_sha256"],
                )
                abandoned = {**previous_active, "status": "abandoned"}
                archive_root = f".aiwf/history/abandoned/{superseded_work_id}"
                changes[f"{archive_root}/work.json"] = json_bytes(abandoned)
                for source_name, filename in (
                    (previous_active["draft_output"], "artifact.md"),
                    (previous_active["result_output"], "result.json"),
                ):
                    source_path = self.store.safe_path(source_name)
                    if source_path.is_file():
                        changes[f"{archive_root}/{filename}"] = source_path.read_bytes()
                if state["mode"] == "blocked":
                    questions = self.store.read_json("questions.json")
                    superseded_questions = []
                    blocking = set(state["blocking_questions"])
                    for item in questions["items"]:
                        if item["id"] in blocking and item["status"] == "open":
                            item = {**item, "status": "superseded"}
                        superseded_questions.append(item)
                    changes[".aiwf/questions.json"] = json_bytes(
                        {"schema_version": SCHEMA_VERSION, "items": superseded_questions}
                    )
                additional_events.append(
                    (
                        "work_superseded",
                        {"work_id": superseded_work_id, "replaced_by": work_id},
                    )
                )
            if adopt_content_drift:
                additional_events.append(
                    (
                        "artifact_drift_resolved",
                        {
                            "artifact_id": artifact_id,
                            "revision": revision,
                            "outcome": "adopt",
                            "artifact_status": artifact["status"],
                            "work_id": work_id,
                        },
                    )
                )
            if decision_route or upstream_route:
                additional_events.append(
                    (
                        "changes_requested",
                        {
                            "artifact_id": artifact_id,
                            "revision": revision,
                            "work_id": work_id,
                            "feedback": effective_feedback,
                            "adopted_content_drift": False,
                        },
                    )
                )
            event_type = (
                "decision_route_selected"
                if decision_route
                else "upstream_correction_routed"
                if upstream_route
                else "changes_requested"
            )
            event_data = (
                {
                    "work_id": decision_work_id,
                    "outcome": "revise",
                    "decision_ids": [
                        item["decision"]["id"] for item in resolved_decisions
                    ],
                    "artifact_id": artifact_id,
                    "revision": revision,
                    "successor_work_id": work_id,
                    **decision_route_audit,
                }
                if decision_route
                else {
                    "work_id": upstream_work_id,
                    "artifact_id": artifact_id,
                    "revision": revision,
                    "successor_work_id": work_id,
                    "correction": feedback.strip(),
                    "evidence": [dict(item) for item in upstream_evidence],
                }
                if upstream_route
                else {
                    "artifact_id": artifact_id,
                    "revision": revision,
                    "work_id": work_id,
                    "feedback": effective_feedback,
                    "adopted_content_drift": adopt_content_drift,
                }
            )
            event = self.store.commit_locked(
                changes,
                event_type=event_type,
                event_data=event_data,
                command_key=command_key,
                request_digest=request_digest,
                additional_events=additional_events,
            )
            if superseded_work_id is not None:
                shutil.rmtree(
                    self.store.data_root / "work" / superseded_work_id,
                    ignore_errors=True,
                )
            return dict(event["data"])

    def _discard_artifact_drift(
        self,
        artifact_id: str,
        revision: int,
        *,
        supersede_active_work: bool,
    ) -> dict[str, Any]:
        command_key_base = f"resolve-drift:{artifact_id}@{revision}:discard"
        request_digest = self._digest(
            {
                "artifact_id": artifact_id,
                "revision": revision,
                "outcome": "discard",
                "supersede_active_work": supersede_active_work,
            }
        )
        with self.store.lock(exclusive=True):
            self._recover_transactions_locked()
            state = self.store.read_json("state.json")
            artifact = find_artifact(self.store.read_json("artifacts.json"), artifact_id)
            if artifact is None or artifact["revision"] != revision:
                raise AIWorkflowError(
                    code="invalid_state_transition",
                    message="Only the current artifact revision can resolve drift.",
                    exit_code=6,
                    details={"artifact_id": artifact_id, "revision": revision},
                )
            artifact_status = artifact["status"]
            if artifact_status == "review":
                reviewed_ref = f"{artifact_id}@{revision}"
                if state["mode"] != "review" or reviewed_ref not in state["pending_reviews"]:
                    raise AIWorkflowError(
                        code="invalid_state_transition",
                        message="Review artifact drift can only be discarded while awaiting review.",
                        exit_code=6,
                    )
            elif artifact_status == "approved":
                if artifact["approved_revision"] != revision:
                    raise AIWorkflowError(
                        code="invalid_state_transition",
                        message="Only the current approved revision can resolve approved artifact drift.",
                        exit_code=6,
                    )
                if state["mode"] == "review":
                    raise AIWorkflowError(
                        code="active_review_conflict",
                        message="Resolve the pending artifact review before resolving another artifact.",
                        exit_code=6,
                    )
                if state["mode"] != "ready" and not supersede_active_work:
                    raise AIWorkflowError(
                        code="active_work_conflict",
                        message="Drift resolution would replace unfinished work; explicit confirmation is required.",
                        exit_code=6,
                        details={"active_work": state["active_work"], "mode": state["mode"]},
                    )
            elif artifact_status not in {"changes_requested", "stale"}:
                raise AIWorkflowError(
                    code="invalid_state_transition",
                    message="Artifact status cannot resolve content drift.",
                    exit_code=6,
                    details={"artifact_status": artifact_status},
                )

            issues = artifact_integrity_issues(self.store.root, artifact)
            prior_events = [
                event
                for event in self.store.read_events()
                if event["type"] == "artifact_drift_resolved"
                and event["data"].get("artifact_id") == artifact_id
                and event["data"].get("revision") == revision
                and event["data"].get("outcome") == "discard"
            ]
            if not issues and prior_events:
                existing_event = prior_events[-1]
                if existing_event["request_digest"] != request_digest:
                    raise AIWorkflowError(
                        code="idempotency_conflict",
                        message="Drift resolution changed for an existing request.",
                        exit_code=6,
                    )
                return dict(existing_event["data"])
            if not (
                len(issues) == 1
                and issues[0]["component"] == "content"
                and issues[0]["reason"] in {"changed", "missing"}
            ):
                raise AIWorkflowError(
                    code="artifact_drift_unrecoverable",
                    message="Only isolated artifact content drift can be discarded.",
                    exit_code=7,
                    details={"artifact_id": artifact_id, "issues": issues},
                )
            command_key = (
                command_key_base
                if not prior_events
                else f"{command_key_base}:{len(prior_events) + 1}"
            )
            external_content = (
                self.store.safe_path(artifact["path"]).read_bytes()
                if issues[0]["reason"] == "changed"
                else None
            )
            recorded_snapshot = self.store.safe_path(artifact["snapshot_path"]).read_bytes()
            changes: dict[str, bytes | None] = {artifact["path"]: recorded_snapshot}
            additional_events: list[tuple[str, Mapping[str, Any]]] = []
            superseded_work_id: str | None = None
            if artifact_status == "approved" and state["mode"] in {
                "working",
                "blocked",
                "decision",
            }:
                superseded_work_id = state["active_work"]
                previous_active = self._read_work(
                    superseded_work_id,
                    expected_hash=state["active_work_sha256"],
                )
                abandoned = {**previous_active, "status": "abandoned"}
                archive_root = f".aiwf/history/abandoned/{superseded_work_id}"
                changes[f"{archive_root}/work.json"] = json_bytes(abandoned)
                for source_name, filename in (
                    (previous_active["draft_output"], "artifact.md"),
                    (previous_active["result_output"], "result.json"),
                ):
                    source_path = self.store.safe_path(source_name)
                    if source_path.is_file():
                        changes[f"{archive_root}/{filename}"] = source_path.read_bytes()
                if state["mode"] == "blocked":
                    questions = self.store.read_json("questions.json")
                    blocking = set(state["blocking_questions"])
                    changes[".aiwf/questions.json"] = json_bytes(
                        {
                            "schema_version": SCHEMA_VERSION,
                            "items": [
                                {**item, "status": "superseded"}
                                if item["id"] in blocking and item["status"] == "open"
                                else item
                                for item in questions["items"]
                            ],
                        }
                    )
                updated_state = {
                    **state,
                    "mode": "ready",
                    "active_item": None,
                    "active_work": None,
                    "active_work_sha256": None,
                    "blocking_questions": [],
                    "updated_at": now_iso(),
                }
                changes[".aiwf/state.json"] = json_bytes(updated_state)
                additional_events.append(
                    (
                        "work_superseded",
                        {"work_id": superseded_work_id, "replaced_by": None},
                    )
                )

            event = self.store.commit_locked(
                changes,
                event_type="artifact_drift_resolved",
                event_data={
                    "artifact_id": artifact_id,
                    "revision": revision,
                    "outcome": "discard",
                    "artifact_status": artifact_status,
                    "external_content_sha256": (
                        sha256_content(external_content) if external_content is not None else None
                    ),
                    "restored_content_sha256": sha256_content(recorded_snapshot),
                },
                command_key=command_key,
                request_digest=request_digest,
                additional_events=additional_events,
            )
            if superseded_work_id is not None:
                shutil.rmtree(
                    self.store.data_root / "work" / superseded_work_id,
                    ignore_errors=True,
                )
            return dict(event["data"])

    def _read_work(
        self,
        work_id: str | None,
        *,
        expected_hash: str | None = None,
    ) -> dict[str, Any]:
        if work_id is None:
            raise AIWorkflowError(
                code="invalid_state",
                message="Workflow state does not identify active work.",
                exit_code=4,
            )
        path = self.store.safe_path(self._work_path(work_id, "work.json"))
        try:
            content = path.read_bytes()
            value = json.loads(content.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AIWorkflowError(
                code="work_drift",
                message="Active work metadata cannot be read.",
                exit_code=7,
                details={"work_id": work_id},
            ) from error
        if expected_hash is not None and sha256_content(content) != expected_hash:
            raise AIWorkflowError(
                code="work_drift",
                message="Active work metadata changed outside the workflow.",
                exit_code=7,
                details={"work_id": work_id},
            )
        work = validate_work(value)
        return work

    def _next_work_id(self) -> str:
        existing: list[str] = []
        for event in self.store.read_events():
            data = event.get("data", {})
            for field_name in ("work_id", "successor_work_id"):
                value = data.get(field_name) if isinstance(data, dict) else None
                if isinstance(value, str):
                    existing.append(value)
        work_root = self.store.data_root / "work"
        if work_root.is_dir():
            existing.extend(path.name for path in work_root.iterdir() if path.is_dir())
        return next_id("work", existing)

    def _validate_active_item(self, stage: str, active_item: str | None) -> None:
        artifact_identity(stage, active_item)
        if active_item is None:
            return
        tasks = self.store.read_json("tasks.json")
        task = next((item for item in tasks["items"] if item["id"] == active_item), None)
        if task is None or task["status"] == "withdrawn":
            raise AIWorkflowError(
                code="unknown_task_id",
                message="Active item is not an available task.",
                exit_code=4,
                details={"id": active_item},
            )
        allowed_statuses = {
            "specification": {"planned", "stale"},
            "implementation": {"in_progress", "stale"},
            "testing": {"implemented", "stale"},
        }
        if stage in allowed_statuses and task["status"] not in allowed_statuses[stage]:
            raise AIWorkflowError(
                code="task_not_ready",
                message="Task status does not allow work in the current stage.",
                exit_code=6,
                details={"id": active_item, "status": task["status"], "stage": stage},
            )

    def _validate_dependencies(self, dependencies: Sequence[str]) -> None:
        artifacts = self.store.read_json("artifacts.json")
        for reference in dependencies:
            try:
                artifact_id, raw_revision = reference.rsplit("@", 1)
                revision = int(raw_revision)
            except (ValueError, AttributeError) as error:
                raise AIWorkflowError(
                    code="invalid_dependency",
                    message="Artifact dependency must use artifact@revision.",
                    exit_code=4,
                    details={"reference": reference},
                ) from error
            artifact = find_artifact(artifacts, artifact_id)
            if (
                artifact is None
                or artifact["status"] != "approved"
                or artifact["approved_revision"] != revision
            ):
                raise AIWorkflowError(
                    code="unavailable_dependency",
                    message="Artifact dependency is not an approved revision.",
                    exit_code=4,
                    details={"reference": reference},
                )

    def _mark_tasks_stale(
        self,
        tasks: dict[str, Any],
        invalidated: Sequence[str],
        source_stage: str,
        *,
        active_item: str | None,
    ) -> dict[str, Any]:
        items = [dict(item) for item in tasks["items"]]
        if source_stage in {"analysis", "design"} and invalidated:
            for item in items:
                if item["status"] != "withdrawn":
                    item["status"] = "stale"
        elif source_stage == "specification" and active_item is None:
            pass
        else:
            affected_ids = {
                "-".join(artifact_id.split("-")[:2]) for artifact_id in invalidated
            }
            for item in items:
                if item["id"] in affected_ids:
                    item["status"] = "stale"
        return {"schema_version": SCHEMA_VERSION, "items": items}

    def _archived_work_ids(self, artifacts: Mapping[str, Any]) -> set[str]:
        work_ids: set[str] = set()
        work_paths = [artifact["work_path"] for artifact in artifacts["items"]]
        history = self.store.data_root / "history"
        if history.is_dir():
            work_paths.extend(
                str(path.relative_to(self.store.root))
                for path in history.rglob("*.work.json")
            )
        for work_path in set(work_paths):
            try:
                snapshot = self.store.read_json_path(work_path)
            except AIWorkflowError:
                continue
            work_id = snapshot.get("work_id")
            if isinstance(work_id, str):
                work_ids.add(work_id)
        abandoned = self.store.data_root / "history" / "abandoned"
        if abandoned.is_dir():
            work_ids.update(path.name for path in abandoned.iterdir() if path.is_dir())
        return work_ids

    def _reject_conflicting_review(
        self,
        artifact_id: str,
        revision: int,
        command_key: str,
    ) -> None:
        prefix = f"review:{artifact_id}@{revision}:"
        for event in self.store.read_events():
            existing_key = event.get("command_key")
            if isinstance(existing_key, str) and existing_key.startswith(prefix) and existing_key != command_key:
                raise AIWorkflowError(
                    code="review_conflict",
                    message="Artifact revision already has a different review outcome.",
                    exit_code=6,
                    details={"artifact_id": artifact_id, "revision": revision},
                )

    def _work_path(self, work_id: str, filename: str) -> str:
        return f".aiwf/work/{work_id}/{filename}"

    def _editable_result_bytes(
        self,
        artifact: Mapping[str, Any],
        *,
        preserve_memory_delta: bool,
    ) -> bytes:
        result = self.store.read_json_path(artifact["result_path"])
        return json_bytes(
            result_seed_from_record(
                artifact["stage"],
                result,
                preserve_memory_delta=preserve_memory_delta,
                active_item=artifact["active_item"],
            )
        )

    def _with_affected_memory(
        self,
        work: dict[str, Any],
        artifact: Mapping[str, Any],
    ) -> dict[str, Any]:
        affected = [
            dict(item)
            for item in self.store.read_json("memory.json")["items"]
            if item["status"] == "active"
            and item["source"].rsplit("@", 1)[0] == artifact["id"]
        ]
        if not affected:
            return work
        facts = dict(work.get("facts", {}))
        facts["affected_memory"] = affected
        updated = {**work, "facts": facts}
        validate_work(updated)
        return updated

    def _digest(self, value: Any) -> str:
        return sha256_bytes(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )


def execute(request: CommandRequest) -> dict[str, Any]:
    engine = WorkflowEngine(request.workspace)
    if request.command == "init":
        return _with_dashboard(
            engine,
            engine.initialize(
                name=request.options.get("name", request.workspace.name),
                platform=request.options["platform"],
                prd_paths=request.options["prd"],
                code_repository=request.options["code_repository"],
                project_id=request.options.get("project_id"),
            ),
        )
    if request.command == "recover":
        return _with_dashboard(engine, engine.recover_workspace())
    if request.command == "status":
        return engine.inspect()
    if request.command == "prepare":
        return _with_dashboard(
            engine,
            engine.prepare_work(
                active_item=request.options.get("task_id"),
                instruction=request.options.get("instruction", ""),
            ),
        )
    if request.command == "submit":
        return _with_dashboard(engine, engine.submit_work(request.options["work_id"]))
    if request.command == "review":
        return _with_dashboard(
            engine,
            engine.review_artifact(
                request.options["artifact_id"],
                request.options["revision"],
                outcome=request.options["outcome"],
                feedback=request.options.get("feedback", ""),
            ),
        )
    if request.command == "revise":
        return _with_dashboard(
            engine,
            engine.request_revision(
                request.options["artifact_id"],
                request.options["revision"],
                feedback=request.options["feedback"],
                supersede_active_work=bool(request.options.get("supersede_active_work")),
            ),
        )
    if request.command == "resolve-drift":
        return _with_dashboard(
            engine,
            engine.resolve_artifact_drift(
                request.options["artifact_id"],
                request.options["revision"],
                outcome=request.options["outcome"],
                feedback=request.options.get("feedback", ""),
                supersede_active_work=bool(request.options.get("supersede_active_work")),
            ),
        )
    if request.command == "question":
        try:
            questions = json.loads(request.options["items_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise AIWorkflowError(
                code="invalid_questions",
                message="Blocking questions must be a valid JSON array.",
                exit_code=2,
            ) from error
        if not isinstance(questions, list):
            raise AIWorkflowError(
                code="invalid_questions",
                message="Blocking questions must be a valid JSON array.",
                exit_code=2,
            )
        return _with_dashboard(
            engine,
            engine.open_questions(request.options["work_id"], questions),
        )
    if request.command == "decide":
        return _with_dashboard(
            engine,
            engine.decide(request.options["question_id"], request.options["decision"]),
        )
    if request.command == "route-decision":
        return _with_dashboard(
            engine,
            engine.route_decision(
                request.options["work_id"],
                outcome=request.options["outcome"],
                artifact_id=request.options.get("artifact_id"),
                revision=request.options.get("revision"),
            ),
        )
    if request.command == "route-upstream":
        try:
            raw_evidence = json.loads(request.options["evidence_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise AIWorkflowError(
                code="invalid_upstream_evidence",
                message="Upstream evidence must be a valid JSON array.",
                exit_code=2,
            ) from error
        evidence = require_evidence_list(
            raw_evidence,
            "route-upstream evidence",
            "evidence",
        )
        return _with_dashboard(
            engine,
            engine.route_upstream(
                request.options["work_id"],
                artifact_id=request.options["artifact_id"],
                revision=request.options["revision"],
                correction=request.options["correction"],
                evidence=evidence,
            ),
        )
    if request.command == "render":
        return engine.render()
    raise AIWorkflowError(
        code="command_not_implemented",
        message=f"Command '{request.command}' is not implemented.",
        exit_code=3,
        details={"command": request.command, "workspace": str(request.workspace)},
    )


def _with_dashboard(engine: WorkflowEngine, result: dict[str, Any]) -> dict[str, Any]:
    try:
        engine.render()
    except Exception as error:
        return {
            **result,
            "warnings": [
                {
                    "type": "dashboard_render_failed",
                    "error": error.code if isinstance(error, AIWorkflowError) else "render_failed",
                }
            ],
        }
    return result
