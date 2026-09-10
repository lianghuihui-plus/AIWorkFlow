"""Task-local workflow operations over the workspace store."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifacts import (
    artifact_identity,
    artifact_revision_paths,
    find_artifact,
    reconcile_requirements,
    reconcile_tasks,
    replace_artifact,
    result_seed_from_record,
    semantic_result_hash,
    semantic_digest,
    sha256_content,
    task_semantic_value,
    validate_design_coverage,
    validate_result_manifest,
)
from .context import build_decision_context, build_work, validate_work
from .dashboard import DASHBOARD_FILENAME, render_dashboard
from .initialization import prepare_initialization
from .model import AIWorkflowError, CommandRequest, SCHEMA_VERSION, next_id, now_iso
from .reconciliation import (
    clear_reconciliation,
    mark_direct_reconciliation,
    requirement_change_sets,
)
from .review import approve_indexes
from .sources import normalize_requirement_sources
from .stage_context import build_stage_context
from .stage_guides import load_stage_guide
from .storage import WorkspaceStore, json_bytes, sha256_bytes
from .task_flow import derive_task_flow, select_work


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
        return {
            "status": "recovered",
            "workspace": str(self.store.root),
            "recovered": self.recover(),
        }

    def _recover_and_sync_locked(self) -> list[str]:
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
            self.store.recover_locked()
            works, corrupt_works = self._scan_works()
            projection = self._derive_task_flow(works=works)
            selected = select_work(projection, active_item)
            corrupt = next(
                (
                    item
                    for item in corrupt_works
                    if item.get("artifact_id") == selected.get("artifact_id")
                ),
                None,
            )
            if corrupt is not None:
                self._raise_corrupt_work(corrupt)
            if selected.get("work_id"):
                existing_work = self._read_work(str(selected["work_id"]))
                instruction_text = instruction.strip()
                if not instruction_text:
                    return existing_work
                prior_feedback = existing_work.get("feedback") or ""
                if f"\n{instruction_text}\n" in f"\n{prior_feedback}\n":
                    return existing_work
                updated_work = {
                    **existing_work,
                    "goal": (
                        f"{existing_work['goal']} "
                        f"当前用户补充要求：{instruction_text}"
                    ),
                    "feedback": "\n".join(
                        item for item in (prior_feedback, instruction_text) if item
                    ),
                }
                timestamp = now_iso()
                self.store.commit_locked(
                    {
                        self._work_path(updated_work["work_id"], "work.json"): json_bytes(
                            updated_work
                        ),
                        ".aiwf/state.json": self._updated_state_bytes(timestamp),
                    },
                    event_type="work_instruction_added",
                    event_data={
                        "work_id": updated_work["work_id"],
                        "stage": updated_work["stage"],
                        "active_item": updated_work["active_item"],
                    },
                    command_key=(
                        f"instruction:{updated_work['work_id']}:"
                        f"{self._digest({'instruction': instruction_text})}"
                    ),
                    request_digest=self._digest({"instruction": instruction_text}),
                )
                return self._read_work(str(updated_work["work_id"]))

            stage = str(selected["stage"])
            task_id = selected.get("active_item")
            defaults = self._default_work_context(
                stage,
                active_item=task_id,
                instruction=instruction,
            )
            work_id = self._next_work_id()
            artifact_id = artifact_identity(stage, task_id)[0]
            work = build_work(
                work_id=work_id,
                stage=stage,
                active_item=task_id,
                goal=goal if goal is not None else defaults["goal"],
                inputs=list(defaults["inputs"] if inputs is None else inputs),
                depends_on=list(defaults["depends_on"] if depends_on is None else depends_on),
                sources=list(defaults["sources"] if sources is None else sources),
                stage_guide=load_stage_guide(
                    stage_guide or defaults["stage_guide"], stage=stage
                ),
                constraints=list(
                    defaults["constraints"] if constraints is None else constraints
                ),
                decision_content=self._related_decisions(task_id),
                target_platform=self.store.read_json("project.json")["platform"],
                facts=defaults.get("facts"),
                repository_context=defaults.get("repository_context"),
                feedback=instruction.strip() or None,
            )
            for path in [*work["inputs"], *work["sources"]]:
                self.store.safe_path(path)

            changes: dict[str, bytes | None] = {
                self._work_path(work_id, "work.json"): json_bytes(work),
                work["result_output"]: json_bytes(work["result_seed"]),
                ".aiwf/state.json": self._updated_state_bytes(),
            }
            artifact = find_artifact(self.store.read_json("artifacts.json"), artifact_id)
            if artifact is not None:
                content_path = self.store.safe_path(artifact["path"])
                if not content_path.is_file():
                    content_path = self.store.safe_path(
                        self._artifact_record_for_seed(artifact)["snapshot_path"]
                    )
                changes[work["draft_output"]] = content_path.read_bytes()
                record = self.store.read_json_path(
                    self._artifact_record_for_seed(artifact)["result_path"]
                )
                changes[work["result_output"]] = json_bytes(
                    result_seed_from_record(
                        stage,
                        record,
                        active_item=task_id,
                    )
                )

            self.store.commit_locked(
                changes,
                event_type="work_prepared",
                event_data={"work_id": work_id, "stage": stage, "active_item": task_id},
                command_key=f"prepare:{work_id}",
                request_digest=self._digest({"work": work}),
            )
            return work

    def submit_work(self, work_id: str) -> dict[str, Any]:
        command_key = f"submit:{work_id}"
        with self.store.lock(exclusive=True):
            self.store.recover_locked()
            existing = self.store.find_event(command_key)
            if existing is not None:
                return dict(existing["data"])
            work = self._read_work(work_id)
            if self._open_questions_for_work(work_id):
                raise AIWorkflowError(
                    code="work_blocked",
                    message="Resolve this work item's open questions before submitting it.",
                    exit_code=6,
                    details={"work_id": work_id},
                )
            self._assert_work_still_ready(work)

            draft_path = self.store.safe_path(work["draft_output"])
            result_path = self.store.safe_path(work["result_output"])
            try:
                draft_bytes = draft_path.read_bytes()
                raw_result_bytes = result_path.read_bytes()
                raw_result = json.loads(raw_result_bytes.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise AIWorkflowError(
                    code="incomplete_work",
                    message="Work draft and result must both be valid files.",
                    exit_code=4,
                    details={"work_id": work_id},
                ) from error
            if not draft_bytes.strip():
                raise AIWorkflowError(
                    code="incomplete_work",
                    message="Artifact draft cannot be empty.",
                    exit_code=4,
                    details={"work_id": work_id},
                )

            result = validate_result_manifest(
                work["stage"], raw_result, active_item=work["active_item"]
            )
            if work["stage"] == "implementation":
                self._validate_implementation_acceptance(work, result)
            project = self.store.read_json("project.json")
            requirements = self.store.read_json("requirements.json")
            tasks = self.store.read_json("tasks.json")
            projected_requirements = requirements
            projected_tasks = tasks
            artifacts = self.store.read_json("artifacts.json")
            artifact_id = work["artifact"]["id"]
            current = find_artifact(artifacts, artifact_id)
            revision = 1 if current is None else int(current["revision"]) + 1

            if work["stage"] == "analysis":
                if result["target_platform"] != project["platform"]:
                    raise AIWorkflowError(
                        code="target_platform_mismatch",
                        message="Analysis target_platform must match the project platform.",
                        exit_code=4,
                    )
                result = {
                    **result,
                    "requirements": normalize_requirement_sources(
                        result["requirements"],
                        workspace_root=self.store.root,
                        work=work,
                        decisions=self.store.read_json("decisions.json"),
                        artifact_ref=f"{artifact_id}@{revision}",
                        archived_work_ids=set(),
                    ),
                }
                projected_requirements, result = reconcile_requirements(
                    requirements, result, revision=revision
                )
            elif work["stage"] == "design":
                validate_design_coverage(requirements, result)
            elif work["stage"] == "specification" and work["active_item"] is None:
                projected_tasks, result = reconcile_tasks(
                    tasks, requirements, result, revision=revision
                )

            result = {
                **result,
                "artifact_id": artifact_id,
                "artifact_type": work["artifact"]["type"],
                "revision": revision,
                "depends_on": list(work["depends_on"]),
                "sources": list(work["sources"]),
            }
            if work["stage"] == "analysis":
                result["effective_requirements"] = list(projected_requirements["items"])
            if work["stage"] == "specification" and work["active_item"] is None:
                result["effective_tasks"] = list(projected_tasks["items"])

            if current is not None and current.get("approved_revision") is not None:
                previous = self.store.read_json_path(current["result_path"])
                content_changed = sha256_content(draft_bytes) != current["content_sha256"]
                if not content_changed and semantic_result_hash(previous) == semantic_result_hash(result):
                    raise AIWorkflowError(
                        code="revision_has_no_changes",
                        message="Use reconcile when review confirms that no artifact change is needed.",
                        exit_code=4,
                        details={"artifact_id": artifact_id},
                    )

            normalized_result = json_bytes(result)
            submitted_work = {**work, "status": "submitted"}
            work_snapshot = json_bytes(submitted_work)
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
                "result_sha256": sha256_content(normalized_result),
                "work_sha256": sha256_content(work_snapshot),
                "status": "review",
                "revision": revision,
                "approved_revision": current.get("approved_revision") if current else None,
                "depends_on": list(work["depends_on"]),
                "sources": list(work["sources"]),
                "needs_reconcile": list(current.get("needs_reconcile", [])) if current else [],
                "updated_at": timestamp,
            }
            if work["stage"] == "testing":
                registered["test_status"] = result["status"]
            artifacts = replace_artifact(artifacts, registered)
            changes = {
                ".aiwf/artifacts.json": json_bytes(artifacts),
                ".aiwf/state.json": self._updated_state_bytes(timestamp),
                self._work_path(work_id, "work.json"): work_snapshot,
                registered["snapshot_path"]: draft_bytes,
                registered["result_path"]: normalized_result,
                registered["work_path"]: work_snapshot,
            }
            if current is None or current.get("approved_revision") is None:
                changes[registered["path"]] = draft_bytes
            event = self.store.commit_locked(
                changes,
                event_type="artifact_submitted",
                event_data={
                    "work_id": work_id,
                    "artifact_id": artifact_id,
                    "revision": revision,
                },
                command_key=command_key,
                request_digest=sha256_bytes(draft_bytes + b"\0" + raw_result_bytes),
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
                artifact_id, revision, feedback=feedback, command_prefix="review"
            )
        if outcome != "approved":
            raise AIWorkflowError(
                code="invalid_review_outcome",
                message="Review outcome must be approved or changes_requested.",
                exit_code=4,
            )
        command_key = f"review:{artifact_id}@{revision}:approved"
        with self.store.lock(exclusive=True):
            self.store.recover_locked()
            existing = self.store.find_event(command_key)
            if existing is not None:
                return dict(existing["data"])
            artifacts = self.store.read_json("artifacts.json")
            artifact = find_artifact(artifacts, artifact_id)
            if artifact is None or artifact["revision"] != revision or artifact["status"] != "review":
                raise AIWorkflowError(
                    code="invalid_state_transition",
                    message="Artifact revision is not awaiting review.",
                    exit_code=6,
                    details={"artifact_id": artifact_id, "revision": revision},
                )

            revision_paths = artifact_revision_paths(artifact_id, revision)
            result = self.store.read_json_path(revision_paths["result_path"])
            requirements = self.store.read_json("requirements.json")
            tasks = self.store.read_json("tasks.json")
            before_requirements = list(requirements["items"])
            before_tasks = list(tasks["items"])
            if artifact["stage"] == "analysis":
                requirements = {
                    "schema_version": SCHEMA_VERSION,
                    "items": [dict(item) for item in result["effective_requirements"]],
                }
            elif artifact["stage"] == "specification" and artifact["active_item"] is None:
                tasks = {
                    "schema_version": SCHEMA_VERSION,
                    "items": [dict(item) for item in result["effective_tasks"]],
                }
            requirements, tasks = approve_indexes(
                stage=artifact["stage"],
                revision=revision,
                active_item=artifact["active_item"],
                requirements=requirements,
                tasks=tasks,
                approve_all_pending=artifact.get("approved_revision") is None,
            )
            previous_approved = artifact.get("approved_revision")
            work = self.store.read_json_path(revision_paths["work_path"])
            pending_reconciliation = self._work_reconciliation_reasons(
                work,
                requirements=requirements,
                tasks=tasks,
                artifacts=artifacts,
            )
            approved = {
                **artifact,
                "status": "approved",
                "approved_revision": revision,
                "needs_reconcile": pending_reconciliation,
                "updated_at": now_iso(),
            }
            artifacts = replace_artifact(artifacts, approved)
            affected: list[str] = []
            if previous_approved is not None:
                artifacts, affected = mark_direct_reconciliation(
                    artifacts,
                    stage=artifact["stage"],
                    active_item=artifact["active_item"],
                    before_requirements=before_requirements,
                    after_requirements=requirements["items"],
                    before_tasks=before_tasks,
                    after_tasks=tasks["items"],
                )
            before_task_status = {item["id"]: item["status"] for item in before_tasks}
            withdrawn_task_ids = {
                item["id"]
                for item in tasks["items"]
                if item["status"] == "withdrawn"
                and before_task_status.get(item["id"]) != "withdrawn"
            }
            active_works = self._active_works()
            abandoned_works = [
                work for work in active_works if work.get("active_item") in withdrawn_task_ids
            ]
            abandoned_work_ids = {work["work_id"] for work in abandoned_works}
            remaining_works = [
                work for work in active_works if work.get("active_item") not in withdrawn_task_ids
            ]
            timestamp = now_iso()
            projection = self._derive_task_flow(
                requirements=requirements,
                tasks=tasks,
                artifacts=artifacts,
                works=remaining_works,
            )
            current_stage = self._current_stage(projection)
            snapshot = self.store.safe_path(revision_paths["snapshot_path"]).read_bytes()
            approval_changes: dict[str, bytes | None] = {
                ".aiwf/artifacts.json": json_bytes(artifacts),
                ".aiwf/requirements.json": json_bytes(requirements),
                ".aiwf/tasks.json": json_bytes(tasks),
                ".aiwf/state.json": self._updated_state_bytes(timestamp),
            }
            for work in abandoned_works:
                approval_changes[self._work_path(work["work_id"], "work.json")] = json_bytes(
                    {**work, "status": "abandoned"}
                )
            if abandoned_work_ids:
                questions = self.store.read_json("questions.json")
                approval_changes[".aiwf/questions.json"] = json_bytes(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "items": [
                            {**item, "status": "cancelled"}
                            if item["work_id"] in abandoned_work_ids
                            and item["status"] == "open"
                            else item
                            for item in questions["items"]
                        ],
                    }
                )
            if not self._has_newer_manual_content(
                artifact,
                submitted_snapshot=snapshot,
                previous_approved=previous_approved,
            ):
                approval_changes[artifact["path"]] = snapshot
            event = self.store.commit_locked(
                approval_changes,
                event_type="artifact_approved",
                event_data={
                    "artifact_id": artifact_id,
                    "revision": revision,
                    "stage": artifact["stage"],
                    "current_stage": current_stage,
                    "needs_reconcile": affected,
                    "abandoned_works": [work["work_id"] for work in abandoned_works],
                },
                command_key=command_key,
                request_digest=self._digest({"artifact_id": artifact_id, "revision": revision}),
            )
            return dict(event["data"])

    def request_revision(
        self,
        artifact_id: str,
        revision: int,
        *,
        feedback: str,
    ) -> dict[str, Any]:
        return self._request_changes(
            artifact_id, revision, feedback=feedback, command_prefix="revise"
        )

    def _request_changes(
        self,
        artifact_id: str,
        revision: int,
        *,
        feedback: str,
        command_prefix: str,
    ) -> dict[str, Any]:
        if not feedback.strip():
            raise AIWorkflowError(
                code="feedback_required",
                message="Revision feedback cannot be empty.",
                exit_code=4,
            )
        command_key = f"{command_prefix}:{artifact_id}@{revision}:changes_requested"
        with self.store.lock(exclusive=True):
            self.store.recover_locked()
            existing = self.store.find_event(command_key)
            if existing is not None:
                work_id = existing["data"].get("work_id")
                if work_id and (self.store.data_root / "work" / work_id).is_dir():
                    return self._read_work(str(work_id))
                return dict(existing["data"])
            artifacts = self.store.read_json("artifacts.json")
            artifact = find_artifact(artifacts, artifact_id)
            if artifact is None or revision != artifact["revision"]:
                raise AIWorkflowError(
                    code="invalid_revision_target",
                    message="Revision target is not the current artifact revision.",
                    exit_code=6,
                )
            if command_prefix == "review" and artifact["status"] != "review":
                raise AIWorkflowError(
                    code="invalid_state_transition",
                    message="Only a pending review can request review changes.",
                    exit_code=6,
                )
            if command_prefix == "revise" and (
                artifact["status"] != "approved"
                or artifact.get("approved_revision") != revision
            ):
                raise AIWorkflowError(
                    code="invalid_state_transition",
                    message="Only the current approved revision can be revised.",
                    exit_code=6,
                )
            existing_work = self._work_for_artifact(artifact_id)
            if existing_work is not None:
                return existing_work

            stage = artifact["stage"]
            task_id = artifact["active_item"]
            defaults = self._default_work_context(stage, active_item=task_id, instruction=feedback)
            work_id = self._next_work_id()
            work = build_work(
                work_id=work_id,
                stage=stage,
                active_item=task_id,
                goal=defaults["goal"],
                inputs=list(defaults["inputs"]),
                depends_on=list(defaults["depends_on"]),
                sources=list(defaults["sources"]),
                stage_guide=load_stage_guide(defaults["stage_guide"], stage=stage),
                constraints=list(defaults["constraints"]),
                decision_content=self._related_decisions(task_id),
                target_platform=self.store.read_json("project.json")["platform"],
                facts=defaults.get("facts"),
                repository_context=defaults.get("repository_context"),
                predecessor=self.store.read_json_path(
                    artifact_revision_paths(artifact_id, revision)["work_path"]
                )["work_id"],
                feedback=feedback,
            )
            content_path = self.store.safe_path(artifact["path"])
            revision_paths = artifact_revision_paths(artifact_id, revision)
            if not content_path.is_file() or (
                command_prefix == "review"
                and not self._has_newer_manual_content(
                    artifact,
                    submitted_snapshot=self.store.safe_path(
                        revision_paths["snapshot_path"]
                    ).read_bytes(),
                    previous_approved=artifact.get("approved_revision"),
                )
            ):
                content_path = self.store.safe_path(revision_paths["snapshot_path"])
            updated_artifact = dict(artifact)
            if command_prefix == "review":
                updated_artifact["status"] = "changes_requested"
                artifacts = replace_artifact(artifacts, updated_artifact)
            self.store.commit_locked(
                {
                    ".aiwf/artifacts.json": json_bytes(artifacts),
                    ".aiwf/state.json": self._updated_state_bytes(),
                    self._work_path(work_id, "work.json"): json_bytes(work),
                    work["draft_output"]: content_path.read_bytes(),
                    work["result_output"]: json_bytes(
                        result_seed_from_record(
                            stage,
                            self.store.read_json_path(revision_paths["result_path"]),
                            active_item=task_id,
                        )
                    ),
                },
                event_type="revision_requested",
                event_data={"artifact_id": artifact_id, "revision": revision, "work_id": work_id},
                command_key=command_key,
                request_digest=self._digest({"feedback": feedback}),
            )
            return work

    def open_questions(
        self, work_id: str, questions: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        if not questions:
            raise AIWorkflowError(
                code="invalid_questions",
                message="At least one question is required.",
                exit_code=4,
            )
        with self.store.lock(exclusive=True):
            self.store.recover_locked()
            work = self._read_work(work_id)
            document = self.store.read_json("questions.json")
            request_digest = self._digest({"questions": list(questions)})
            previous_batches = [
                event
                for event in self.store.read_events()
                if event.get("type") == "question_opened"
                and event.get("data", {}).get("work_id") == work_id
            ]
            current_by_id = {item["id"]: item for item in document["items"]}
            repeated = next(
                (
                    event
                    for event in reversed(previous_batches)
                    if event.get("request_digest") == request_digest
                    and any(
                        current_by_id.get(question_id, {}).get("status") == "open"
                        for question_id in event.get("data", {}).get("question_ids", [])
                    )
                ),
                None,
            )
            if repeated is not None:
                return dict(repeated["data"])
            existing_ids = [item["id"] for item in document["items"]]
            created = []
            timestamp = now_iso()
            for raw in questions:
                values = {}
                for field in ("question", "reason", "recommendation"):
                    value = raw.get(field)
                    if not isinstance(value, str) or not value.strip():
                        raise AIWorkflowError(
                            code="invalid_questions",
                            message=f"Question field '{field}' must be non-empty.",
                            exit_code=4,
                        )
                    values[field] = value
                question_id = next_id("question", existing_ids)
                existing_ids.append(question_id)
                created.append(
                    {
                        "id": question_id,
                        **values,
                        "stage": work["stage"],
                        "active_item": work["active_item"],
                        "work_id": work_id,
                        "status": "open",
                        "decision_id": None,
                        "created_at": timestamp,
                    }
                )
            updated_work = {**work, "status": "blocked"}
            event = self.store.commit_locked(
                {
                    ".aiwf/questions.json": json_bytes(
                        {"schema_version": SCHEMA_VERSION, "items": [*document["items"], *created]}
                    ),
                    ".aiwf/state.json": self._updated_state_bytes(timestamp),
                    self._work_path(work_id, "work.json"): json_bytes(updated_work),
                },
                event_type="question_opened",
                event_data={"work_id": work_id, "question_ids": [item["id"] for item in created]},
                command_key=f"question:{work_id}:{len(previous_batches) + 1}",
                request_digest=request_digest,
            )
            return dict(event["data"])

    def decide(self, question_id: str, decision: str) -> dict[str, Any]:
        if not decision.strip():
            raise AIWorkflowError(
                code="invalid_decision", message="Decision text cannot be empty.", exit_code=4
            )
        with self.store.lock(exclusive=True):
            self.store.recover_locked()
            command_key = f"decide:{question_id}"
            existing = self.store.find_event(command_key)
            if existing is not None:
                return dict(existing["data"])
            questions = self.store.read_json("questions.json")
            question = next((item for item in questions["items"] if item["id"] == question_id), None)
            if question is None or question["status"] != "open":
                raise AIWorkflowError(
                    code="invalid_state_transition",
                    message="Question is not open.",
                    exit_code=6,
                    details={"question_id": question_id},
                )
            decisions = self.store.read_json("decisions.json")
            decision_id = next_id("decision", [item["id"] for item in decisions["items"]])
            timestamp = now_iso()
            decision_item = {
                "id": decision_id,
                "question_id": question_id,
                "decision": decision,
                "status": "active",
                "created_at": timestamp,
            }
            updated_questions = {
                "schema_version": SCHEMA_VERSION,
                "items": [
                    {**item, "status": "resolved", "decision_id": decision_id}
                    if item["id"] == question_id
                    else item
                    for item in questions["items"]
                ],
            }
            updated_decisions = {
                "schema_version": SCHEMA_VERSION,
                "items": [*decisions["items"], decision_item],
            }
            changes: dict[str, bytes | None] = {
                ".aiwf/questions.json": json_bytes(updated_questions),
                ".aiwf/decisions.json": json_bytes(updated_decisions),
                ".aiwf/state.json": self._updated_state_bytes(timestamp),
            }
            work_id = question["work_id"]
            remaining = [
                item for item in updated_questions["items"]
                if item["work_id"] == work_id and item["status"] == "open"
            ]
            work = self._read_work(work_id)
            decision_content = self._related_decisions(
                question.get("active_item"),
                questions=updated_questions,
                decisions=updated_decisions,
            )
            changes[self._work_path(work_id, "work.json")] = json_bytes(
                {
                    **work,
                    "status": "blocked" if remaining else "active",
                    "decision_context": build_decision_context(decision_content),
                }
            )
            event = self.store.commit_locked(
                changes,
                event_type="decision_recorded",
                event_data={
                    "question_id": question_id,
                    "decision_id": decision_id,
                    "work_id": work_id,
                },
                command_key=command_key,
                request_digest=self._digest({"decision": decision}),
            )
            return dict(event["data"])

    def reconcile_artifact(self, artifact_id: str, revision: int, note: str) -> dict[str, Any]:
        if not note.strip():
            raise AIWorkflowError(
                code="reconciliation_note_required",
                message="Reconciliation note cannot be empty.",
                exit_code=4,
            )
        with self.store.lock(exclusive=True):
            self.store.recover_locked()
            artifacts = self.store.read_json("artifacts.json")
            artifact = find_artifact(artifacts, artifact_id)
            if (
                artifact is None
                or artifact.get("approved_revision") != revision
                or not artifact.get("needs_reconcile")
            ):
                raise AIWorkflowError(
                    code="artifact_not_pending_reconciliation",
                    message="Artifact is not an approved revision awaiting reconciliation.",
                    exit_code=6,
                    details={"artifact_id": artifact_id, "revision": revision},
                )
            previous_reasons = list(artifact["needs_reconcile"])
            artifacts = clear_reconciliation(artifacts, artifact_id)
            event = self.store.commit_locked(
                {
                    ".aiwf/artifacts.json": json_bytes(artifacts),
                    ".aiwf/state.json": self._updated_state_bytes(),
                },
                event_type="artifact_reconciled",
                event_data={
                    "artifact_id": artifact_id,
                    "revision": revision,
                    "reasons": previous_reasons,
                    "note": note,
                },
                command_key=f"reconcile:{artifact_id}@{revision}",
                request_digest=self._digest({"note": note}),
            )
            return dict(event["data"])

    def inspect(self) -> dict[str, Any]:
        self.store.require_initialized()
        with self.store.lock(exclusive=False):
            if self.store.has_pending_transactions():
                return {
                    "status": "needs_recovery",
                    "workspace": str(self.store.root),
                    "next_action": "recover",
                    "issues": [
                        {
                            "level": "error",
                            "type": "transaction_recovery_required",
                            "message": "Recover the incomplete metadata transaction.",
                            "blocking": True,
                        }
                    ],
                }
            return self._inspect_locked()

    def _inspect_locked(self) -> dict[str, Any]:
        documents = {
            name: self.store.read_json(name)
            for name in (
                "project.json", "state.json", "requirements.json", "tasks.json",
                "artifacts.json", "questions.json", "decisions.json",
            )
        }
        works, corrupt_works = self._scan_works()
        events = self.store.read_events()
        repository = Path(documents["project.json"]["code_repository"])
        issues: list[dict[str, Any]] = [
            {
                "level": "error",
                "type": "corrupt_work",
                "message": "A work item is unreadable; only its artifact is unavailable.",
                "blocking": False,
                "recovery_action": "repair_or_remove_work",
                "details": {
                    key: item.get(key)
                    for key in ("work_id", "artifact_id", "active_item", "path")
                    if item.get(key) is not None
                },
            }
            for item in corrupt_works
        ]
        if not repository.is_dir():
            issues.append(
                {
                    "level": "warning",
                    "type": "code_repository_unavailable",
                    "message": "Configured code repository is not accessible.",
                    "blocking": False,
                    "recovery_action": "restore_code_repository",
                    "details": {"path": str(repository)},
                }
            )
        projection = self._derive_task_flow(
            requirements=documents["requirements.json"],
            tasks=documents["tasks.json"],
            artifacts=documents["artifacts.json"],
            works=works,
        )
        pending_reviews = [
            item for item in documents["artifacts.json"]["items"] if item["status"] == "review"
        ]
        open_questions = [
            item for item in documents["questions.json"]["items"] if item["status"] == "open"
        ]
        current_stage = self._current_stage(projection)
        next_action = self._next_action(projection)
        return {
            "status": "ok",
            "workspace": str(self.store.root),
            "project": documents["project.json"],
            "state": documents["state.json"],
            "current_stage": current_stage,
            "stage_progress": projection["stage_progress"],
            "task_progress": projection["task_progress"],
            "implementation_complete": projection["completed"],
            "ready_work": projection["ready_work"],
            "optional_work": projection["optional_work"],
            "recommended_work": projection["recommended_work"],
            "active_works": [
                {
                    "work_id": item["work_id"],
                    "stage": item["stage"],
                    "active_item": item["active_item"],
                    "status": item["status"],
                }
                for item in works
            ],
            "next_action": next_action,
            "counts": {
                "prd_files": len(documents["project.json"]["prd_files"]),
                "requirements": len(documents["requirements.json"]["items"]),
                "tasks": len(documents["tasks.json"]["items"]),
                "artifacts": len(documents["artifacts.json"]["items"]),
                "pending_reviews": len(pending_reviews),
                "open_questions": len(open_questions),
                "active_works": len(works),
            },
            "pending_reviews": pending_reviews,
            "needs_reconcile": [
                {
                    "artifact_id": item["id"],
                    "task_id": item["active_item"],
                    "reasons": list(item.get("needs_reconcile", [])),
                }
                for item in documents["artifacts.json"]["items"]
                if item.get("needs_reconcile")
            ],
            "open_questions": open_questions,
            "issues": issues,
        }

    def render(self) -> dict[str, Any]:
        with self.store.lock(exclusive=True):
            self.store.recover_locked()
            inspection = self._inspect_locked()
            artifacts = self.store.read_json("artifacts.json")
            bodies: dict[str, str] = {}
            for artifact in artifacts["items"]:
                path = self.store.safe_path(artifact["path"])
                if not path.is_file():
                    path = self.store.safe_path(artifact["snapshot_path"])
                try:
                    bodies[artifact["id"]] = path.read_text(encoding="utf-8")
                except OSError:
                    bodies[artifact["id"]] = ""
            pending_refs = [
                f"{item['id']}@{item['revision']}" for item in inspection["pending_reviews"]
            ]
            content = render_dashboard(
                project=self.store.read_json("project.json"),
                state={
                    "mode": "ready",
                    "pending_reviews": pending_refs,
                    "updated_at": self.store.read_json("state.json")["updated_at"],
                },
                requirements=self.store.read_json("requirements.json"),
                tasks=self.store.read_json("tasks.json"),
                artifacts=artifacts,
                questions=self.store.read_json("questions.json"),
                decisions=self.store.read_json("decisions.json"),
                events=self.store.read_events(),
                artifact_bodies=bodies,
                next_action=inspection["next_action"],
                current_stage=inspection["current_stage"],
                stage_progress=inspection["stage_progress"],
                task_progress=inspection["task_progress"],
                implementation_complete=inspection["implementation_complete"],
            ).encode("utf-8")
            self.store.replace_generated_locked(DASHBOARD_FILENAME, content)
            return {"status": "rendered", "path": DASHBOARD_FILENAME, "bytes": len(content)}

    def _derive_task_flow(
        self,
        *,
        requirements: Mapping[str, Any] | None = None,
        tasks: Mapping[str, Any] | None = None,
        artifacts: Mapping[str, Any] | None = None,
        works: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        return derive_task_flow(
            requirements=requirements or self.store.read_json("requirements.json"),
            tasks=tasks or self.store.read_json("tasks.json"),
            artifacts=artifacts or self.store.read_json("artifacts.json"),
            works=works,
        )

    def _default_work_context(
        self, stage: str, *, active_item: str | None, instruction: str
    ) -> dict[str, Any]:
        context = build_stage_context(
            stage,
            active_item=active_item,
            instruction=instruction,
            project=self.store.read_json("project.json"),
            approved_artifact=self._approved_artifact,
            current_requirements=self._current_requirements,
            task_by_id=self._task,
            task_facts=self._task_facts,
        )
        if stage == "specification" and active_item is None:
            context["facts"]["tasks"] = list(
                self.store.read_json("tasks.json")["items"]
            )
        elif stage == "implementation" and active_item is not None:
            specification = self._approved_artifact(f"{active_item}-spec")
            result = self.store.read_json_path(specification["result_path"])
            context["facts"]["acceptance_criteria"] = list(
                result["acceptance_criteria"]
            )
        return context

    def _approved_artifact(self, artifact_id: str) -> dict[str, Any]:
        artifact = find_artifact(self.store.read_json("artifacts.json"), artifact_id)
        if artifact is None or artifact.get("approved_revision") is None:
            raise AIWorkflowError(
                code="unavailable_dependency",
                message="Required upstream artifact has no approved revision.",
                exit_code=6,
                details={"artifact_id": artifact_id},
            )
        return {
            **artifact,
            **artifact_revision_paths(artifact_id, int(artifact["approved_revision"])),
        }

    def _artifact_record_for_seed(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        if artifact.get("approved_revision") is None:
            return dict(artifact)
        return {
            **artifact,
            **artifact_revision_paths(
                str(artifact["id"]), int(artifact["approved_revision"])
            ),
        }

    def _task(self, task_id: str | None) -> dict[str, Any]:
        task = next(
            (item for item in self.store.read_json("tasks.json")["items"] if item["id"] == task_id),
            None,
        )
        if task is None or task["status"] != "active":
            raise AIWorkflowError(
                code="unknown_task_id", message="Task is not active.", exit_code=4,
                details={"id": task_id},
            )
        return task

    def _task_facts(self, task: Mapping[str, Any]) -> dict[str, Any]:
        by_id = {item["id"]: item for item in self.store.read_json("requirements.json")["items"]}
        return {
            "task": dict(task),
            "requirements": [by_id[item_id] for item_id in task["requirements"]],
        }

    def _current_requirements(self) -> list[dict[str, Any]]:
        return [
            item for item in self.store.read_json("requirements.json")["items"]
            if item["disposition"] == "accepted"
        ]

    def _related_decisions(
        self,
        task_id: str | None,
        *,
        questions: Mapping[str, Any] | None = None,
        decisions: Mapping[str, Any] | None = None,
    ) -> str:
        question_items = (
            questions if questions is not None else self.store.read_json("questions.json")
        )["items"]
        decision_items = (
            decisions if decisions is not None else self.store.read_json("decisions.json")
        )["items"]
        questions_by_id = {item["id"]: item for item in question_items}
        lines = []
        for decision in decision_items:
            if decision["status"] != "active":
                continue
            question = questions_by_id.get(decision["question_id"])
            if question is None or question.get("active_item") != task_id:
                continue
            lines.append(f"- {question['question']}: {decision['decision']}")
        return "\n".join(lines)

    def _validate_implementation_acceptance(
        self, work: Mapping[str, Any], result: Mapping[str, Any]
    ) -> None:
        task_id = str(work["active_item"])
        specification = self._approved_artifact(f"{task_id}-spec")
        if specification.get("needs_reconcile"):
            raise AIWorkflowError(
                code="task_specification_needs_reconcile",
                message="Reconcile the task specification before completing implementation.",
                exit_code=6,
                details={"id": task_id},
            )
        specification_result = self.store.read_json_path(specification["result_path"])
        expected = list(specification_result["acceptance_criteria"])
        actual = [item["criterion"] for item in result["acceptance_results"]]
        duplicates = sorted({criterion for criterion in actual if actual.count(criterion) > 1})
        missing = sorted(set(expected) - set(actual))
        unexpected = sorted(set(actual) - set(expected))
        if duplicates or missing or unexpected:
            raise AIWorkflowError(
                code="acceptance_coverage_mismatch",
                message="Implementation results must cover each task acceptance criterion exactly once.",
                exit_code=4,
                details={
                    "id": task_id,
                    "missing": missing,
                    "unexpected": unexpected,
                    "duplicates": duplicates,
                },
            )

    def _assert_work_still_ready(self, work: Mapping[str, Any]) -> None:
        if work["active_item"] is None:
            return
        projection = self._derive_task_flow(works=self._active_works())
        progress = next(
            (item for item in projection["task_progress"] if item["id"] == work["active_item"]),
            None,
        )
        if progress is None:
            raise AIWorkflowError(
                code="unknown_task_id", message="Task is no longer active.", exit_code=6
            )
        if work["stage"] == "implementation" and progress["blocked_by"]:
            raise AIWorkflowError(
                code="task_dependency_blocked",
                message="Task dependencies no longer have current approved implementations.",
                exit_code=6,
                details={"id": work["active_item"], "blocked_by": progress["blocked_by"]},
            )

    def _work_reconciliation_reasons(
        self,
        work: Mapping[str, Any],
        *,
        requirements: Mapping[str, Any],
        tasks: Mapping[str, Any],
        artifacts: Mapping[str, Any],
    ) -> list[str]:
        if work["stage"] == "analysis":
            return []

        reasons: set[str] = set()
        facts = work.get("facts", {})
        current_requirements = {
            item["id"]: item for item in requirements["items"]
        }
        work_requirements = {
            item["id"]: item for item in facts.get("requirements", [])
        }

        if work.get("active_item") is None:
            relevant_requirements = {
                item_id: item
                for item_id, item in current_requirements.items()
                if item.get("disposition") == "accepted"
            }
        else:
            current_task = next(
                (
                    item
                    for item in tasks["items"]
                    if item["id"] == work["active_item"]
                ),
                None,
            )
            work_task = facts.get("task")
            if (
                current_task is None
                or work_task is None
                or semantic_digest(task_semantic_value(current_task))
                != semantic_digest(task_semantic_value(work_task))
            ):
                reasons.add(f"task:{work['active_item']}")
            requirement_ids = (
                current_task.get("requirements", []) if current_task else []
            )
            relevant_requirements = {
                item_id: current_requirements[item_id]
                for item_id in requirement_ids
                if item_id in current_requirements
            }

        scope_changes, behavior_changes = requirement_change_sets(
            list(work_requirements.values()), list(relevant_requirements.values())
        )
        if work["stage"] == "design":
            requirement_changes = scope_changes.union(behavior_changes)
        elif work["stage"] == "specification" and work.get("active_item") is None:
            requirement_changes = scope_changes
        else:
            requirement_changes = behavior_changes
        reasons.update(
            f"requirement:{requirement_id}"
            for requirement_id in requirement_changes
        )

        upstream_id = (
            f"{work['active_item']}-spec"
            if work["stage"] == "implementation"
            else f"{work['active_item']}-implementation"
            if work["stage"] == "testing"
            else None
        )
        if upstream_id is not None:
            reference = next(
                (
                    item
                    for item in work.get("depends_on", [])
                    if item.rpartition("@")[0] == upstream_id
                ),
                None,
            )
            dependency = next(
                (item for item in artifacts["items"] if item["id"] == upstream_id),
                None,
            )
            expected_revision = (
                int(reference.rpartition("@")[2])
                if reference and reference.rpartition("@")[2].isdigit()
                else None
            )
            if (
                dependency is None
                or expected_revision is None
                or dependency.get("approved_revision") != expected_revision
                or dependency.get("needs_reconcile")
            ):
                reasons.add(f"artifact:{upstream_id}")
        return sorted(reasons)

    def _active_works(self) -> list[dict[str, Any]]:
        return self._scan_works()[0]

    def _scan_works(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        root = self.store.data_root / "work"
        works: list[dict[str, Any]] = []
        corrupt_works: list[dict[str, Any]] = []
        if not root.is_dir():
            return works, corrupt_works
        identities = self._work_identities()
        for directory in sorted(path for path in root.iterdir() if path.is_dir()):
            path = directory / "work.json"
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                work = validate_work(value)
                if work["status"] in {"active", "blocked"}:
                    works.append(work)
            except (OSError, json.JSONDecodeError, AIWorkflowError) as error:
                work_id = path.parent.name
                identity = identities.get(work_id, {})
                corrupt_works.append(
                    {
                        "work_id": work_id,
                        "artifact_id": identity.get("artifact_id"),
                        "stage": identity.get("stage"),
                        "active_item": identity.get("active_item"),
                        "path": str(path.relative_to(self.store.root)),
                        "error": str(error),
                    }
                )
        return works, corrupt_works

    def _work_identities(self) -> dict[str, dict[str, Any]]:
        artifacts = {
            item["id"]: item for item in self.store.read_json("artifacts.json")["items"]
        }
        identities: dict[str, dict[str, Any]] = {}
        for event in self.store.read_events():
            data = event.get("data", {})
            work_id = data.get("work_id")
            if not isinstance(work_id, str):
                continue
            if event.get("type") == "work_prepared":
                stage = data.get("stage")
                active_item = data.get("active_item")
                if isinstance(stage, str):
                    identities[work_id] = {
                        "artifact_id": artifact_identity(stage, active_item)[0],
                        "stage": stage,
                        "active_item": active_item,
                    }
            elif event.get("type") == "revision_requested":
                artifact = artifacts.get(data.get("artifact_id"))
                if artifact is not None:
                    identities[work_id] = {
                        "artifact_id": artifact["id"],
                        "stage": artifact["stage"],
                        "active_item": artifact["active_item"],
                    }
        return identities

    def _raise_corrupt_work(self, corrupt: Mapping[str, Any]) -> None:
        raise AIWorkflowError(
            code="corrupt_work",
            message="The existing work item is unreadable and must be repaired or removed.",
            exit_code=4,
            details={
                key: corrupt.get(key)
                for key in ("work_id", "artifact_id", "path")
                if corrupt.get(key) is not None
            },
        )

    def _has_newer_manual_content(
        self,
        artifact: Mapping[str, Any],
        *,
        submitted_snapshot: bytes,
        previous_approved: int | None,
    ) -> bool:
        content_path = self.store.safe_path(artifact["path"])
        if not content_path.is_file():
            return False
        content = content_path.read_bytes()
        if content == submitted_snapshot:
            return False
        if previous_approved is None:
            return True
        approved_snapshot = artifact_revision_paths(
            str(artifact["id"]), previous_approved
        )["snapshot_path"]
        return content != self.store.safe_path(approved_snapshot).read_bytes()

    def _work_for_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        works, corrupt_works = self._scan_works()
        corrupt = next(
            (item for item in corrupt_works if item.get("artifact_id") == artifact_id),
            None,
        )
        if corrupt is not None:
            self._raise_corrupt_work(corrupt)
        return next(
            (item for item in works if item["artifact"]["id"] == artifact_id),
            None,
        )

    def _open_questions_for_work(self, work_id: str) -> list[dict[str, Any]]:
        return [
            item for item in self.store.read_json("questions.json")["items"]
            if item["work_id"] == work_id and item["status"] == "open"
        ]

    def _read_work(self, work_id: str) -> dict[str, Any]:
        path = self.store.safe_path(self._work_path(work_id, "work.json"))
        if not path.is_file():
            raise AIWorkflowError(
                code="unknown_work_id",
                message="Work item does not exist.",
                exit_code=4,
                details={"work_id": work_id},
            )
        try:
            return validate_work(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, AIWorkflowError) as error:
            raise AIWorkflowError(
                code="corrupt_work",
                message="Work item is unreadable or does not match the current schema.",
                exit_code=4,
                details={"work_id": work_id},
            ) from error

    def _next_work_id(self) -> str:
        ids = [item["work_id"] for item in self._active_works()]
        for event in self.store.read_events():
            work_id = event["data"].get("work_id")
            if isinstance(work_id, str):
                ids.append(work_id)
        return next_id("work", ids)

    def _current_stage(self, projection: Mapping[str, Any]) -> str:
        if projection["completed"]:
            return "completed"
        recommended = projection["recommended_work"]
        if recommended is not None:
            return str(recommended["stage"])
        for stage in ("analysis", "design", "specification", "implementation"):
            if projection["stage_progress"][stage] != "completed":
                return stage
        return "testing"

    def _next_action(self, projection: Mapping[str, Any]) -> str:
        recommended = projection["recommended_work"]
        if recommended is None:
            return "completed" if projection["completed"] else "review"
        if recommended["stage"] == "specification" and recommended["active_item"] is None:
            return "plan_tasks"
        return {
            "analysis": "analyze_requirements",
            "design": "design_solution",
            "specification": "generate_specification",
            "implementation": "implement_code",
            "testing": "write_unit_tests",
        }[recommended["stage"]]

    def _updated_state_bytes(self, timestamp: str | None = None) -> bytes:
        return json_bytes({"schema_version": SCHEMA_VERSION, "updated_at": timestamp or now_iso()})

    def _work_path(self, work_id: str, filename: str) -> str:
        return f".aiwf/work/{work_id}/{filename}"

    def _digest(self, value: Any) -> str:
        return sha256_bytes(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
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
            ),
        )
    if request.command == "question":
        try:
            items = json.loads(request.options["items_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise AIWorkflowError(
                code="invalid_questions",
                message="Questions must be a valid JSON array.",
                exit_code=2,
            ) from error
        if not isinstance(items, list):
            raise AIWorkflowError(
                code="invalid_questions", message="Questions must be an array.", exit_code=2
            )
        return _with_dashboard(engine, engine.open_questions(request.options["work_id"], items))
    if request.command == "decide":
        return _with_dashboard(
            engine, engine.decide(request.options["question_id"], request.options["decision"])
        )
    if request.command == "reconcile":
        return _with_dashboard(
            engine,
            engine.reconcile_artifact(
                request.options["artifact_id"],
                request.options["revision"],
                request.options["note"],
            ),
        )
    if request.command == "render":
        return engine.render()
    raise AIWorkflowError(
        code="command_not_implemented",
        message=f"Command '{request.command}' is not implemented.",
        exit_code=3,
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
