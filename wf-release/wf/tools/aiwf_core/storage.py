"""Workspace paths, locks, and recoverable file transactions."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .model import AIWorkflowError, SCHEMA_VERSION, now_iso, validate_document
from .memory_view import render_memory

DATA_FILES = (
    "project.json",
    "state.json",
    "requirements.json",
    "tasks.json",
    "artifacts.json",
    "decisions.json",
    "questions.json",
    "memory.json",
)
EVENTS_PATH = Path(".aiwf/events.jsonl")


class InjectedTransactionFailure(RuntimeError):
    """Test-only failure raised after a configured number of replacements."""


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def json_line(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def resolve_workspace(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    try:
        workspace = candidate.resolve(strict=True)
    except OSError as error:
        raise AIWorkflowError(
            code="invalid_workspace",
            message="Workspace directory does not exist or cannot be resolved.",
            exit_code=2,
            details={"path": str(candidate)},
        ) from error

    if not workspace.is_dir():
        raise AIWorkflowError(
            code="invalid_workspace",
            message="Workspace path is not a directory.",
            exit_code=2,
            details={"path": str(workspace)},
        )

    return workspace


class WorkspaceStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.data_root = self.root / ".aiwf"
        self._fault_after_replacements: int | None = None

    @property
    def initialized(self) -> bool:
        return self.data_root.is_dir()

    def bootstrap(
        self,
        project: Mapping[str, Any],
        *,
        prd_files: Mapping[str, bytes] | None = None,
    ) -> None:
        if not self.root.is_dir():
            raise AIWorkflowError(
                code="invalid_workspace",
                message="Workspace directory does not exist.",
                exit_code=2,
                details={"path": str(self.root)},
            )
        existing_entries = list(self.root.iterdir())
        if existing_entries:
            raise AIWorkflowError(
                code="workspace_not_empty",
                message="Workspace directory must be empty before initialization.",
                exit_code=5,
                details={
                    "path": str(self.root),
                    "entries": sorted(path.name for path in existing_entries),
                },
            )
        timestamp = now_iso()
        project_data = dict(project)
        project_data.setdefault("schema_version", SCHEMA_VERSION)
        project_data.setdefault("created_at", timestamp)
        validate_document("project.json", project_data)

        initial_documents: dict[str, dict[str, Any]] = {
            "project.json": project_data,
            "state.json": {
                "schema_version": SCHEMA_VERSION,
                "current_stage": "analysis",
                "mode": "ready",
                "active_item": None,
                "active_work": None,
                "active_work_sha256": None,
                "pending_reviews": [],
                "blocking_questions": [],
                "updated_at": timestamp,
            },
            "requirements.json": {"schema_version": SCHEMA_VERSION, "items": []},
            "tasks.json": {"schema_version": SCHEMA_VERSION, "items": []},
            "artifacts.json": {"schema_version": SCHEMA_VERSION, "items": []},
            "decisions.json": {"schema_version": SCHEMA_VERSION, "items": []},
            "questions.json": {"schema_version": SCHEMA_VERSION, "items": []},
            "memory.json": {"schema_version": SCHEMA_VERSION, "items": []},
        }
        for name, document in initial_documents.items():
            validate_document(name, document)

        copied_prd = dict(prd_files or {})
        for filename in copied_prd:
            if Path(filename).name != filename or filename in {"", ".", ".."}:
                raise AIWorkflowError(
                    code="prd_name_invalid",
                    message="PRD destination must be a plain filename.",
                    exit_code=2,
                    details={"filename": filename},
                )

        staging_root = Path(tempfile.mkdtemp(prefix=".aiwf-bootstrap-", dir=self.root))
        temporary_root = staging_root / ".aiwf"
        temporary_prd = staging_root / "prd"
        temporary_artifacts = staging_root / "artifacts"
        installed_paths: list[Path] = []
        try:
            temporary_root.mkdir()
            temporary_prd.mkdir()
            temporary_artifacts.mkdir()
            for directory in ("results", "history", "work", "transactions"):
                (temporary_root / directory).mkdir(parents=True)
            for name, document in initial_documents.items():
                (temporary_root / name).write_bytes(json_bytes(document))
            transaction_id = f"tx-{uuid.uuid4().hex}"
            event = {
                "event_id": "E-000001",
                "type": "workspace_initialized",
                "transaction_id": transaction_id,
                "command_key": "bootstrap",
                "request_digest": sha256_bytes(json_bytes(project_data)),
                "created_at": timestamp,
                "data": {"project_id": project_data["project_id"]},
            }
            (temporary_root / "events.jsonl").write_bytes(json_line(event))
            (temporary_root / "memory.md").write_text(
                render_memory(
                    initial_documents["memory.json"],
                    initial_documents["decisions.json"],
                ),
                encoding="utf-8",
            )
            (temporary_root / "workspace.lock").touch()
            for filename, content in copied_prd.items():
                (temporary_prd / filename).write_bytes(content)
            for directory in ("specs", "reports", "tests"):
                (temporary_artifacts / directory).mkdir()

            installs = [
                (temporary_prd, self.root / "prd"),
                (temporary_artifacts, self.root / "artifacts"),
                (temporary_root, self.data_root),
            ]
            for source, target in installs:
                os.replace(source, target)
                installed_paths.append(target)
                self._fsync_directory(self.root)
        except Exception:
            for installed_path in reversed(installed_paths):
                shutil.rmtree(installed_path, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

    def require_initialized(self) -> None:
        if not self.initialized:
            raise AIWorkflowError(
                code="not_initialized",
                message="Directory is not an AIWorkFlow workspace.",
                exit_code=5,
                details={"path": str(self.root)},
            )

    @contextmanager
    def lock(self, *, exclusive: bool) -> Iterator[None]:
        self.require_initialized()
        lock_path = self.data_root / "workspace.lock"
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def safe_path(self, relative_path: str | Path) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise AIWorkflowError(
                code="path_outside_workspace",
                message="Path must be relative to the workspace.",
                exit_code=4,
                details={"path": str(relative_path)},
            )
        target = (self.root / relative).resolve(strict=False)
        if not target.is_relative_to(self.root):
            raise AIWorkflowError(
                code="path_outside_workspace",
                message="Path resolves outside the workspace.",
                exit_code=4,
                details={"path": str(relative_path)},
            )
        return target

    def read_json(self, name: str) -> dict[str, Any]:
        if name not in DATA_FILES:
            raise ValueError(f"unsupported data document: {name}")
        path = self.data_root / name
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AIWorkflowError(
                code="corrupt_workspace",
                message=f"Cannot read {name}.",
                exit_code=4,
                details={"path": str(path)},
            ) from error
        return validate_document(name, value)

    def read_json_path(self, relative_path: str | Path) -> dict[str, Any]:
        path = self.safe_path(relative_path)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AIWorkflowError(
                code="corrupt_workspace",
                message="Cannot read JSON data.",
                exit_code=4,
                details={"path": str(path)},
            ) from error
        if not isinstance(value, dict):
            raise AIWorkflowError(
                code="invalid_schema",
                message="JSON document must contain an object.",
                exit_code=4,
                details={"path": str(path)},
            )
        return value

    def read_events(self) -> list[dict[str, Any]]:
        path = self.data_root / "events.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            events = [json.loads(line) for line in lines if line.strip()]
        except (OSError, json.JSONDecodeError) as error:
            raise AIWorkflowError(
                code="corrupt_workspace",
                message="Cannot read events.jsonl.",
                exit_code=4,
            ) from error
        seen_ids: set[str] = set()
        previous_number = 0
        for event in events:
            if not isinstance(event, dict):
                self._fail_event_schema("every event must be a JSON object")
            event_id = event.get("event_id")
            if not isinstance(event_id, str) or not re.fullmatch(r"E-\d{6,}", event_id):
                self._fail_event_schema("event_id must be a valid event id")
            event_number = int(event_id.rsplit("-", 1)[1])
            if event_id in seen_ids or event_number <= previous_number:
                self._fail_event_schema("event ids must be unique and increasing")
            seen_ids.add(event_id)
            previous_number = event_number
            for field_name in ("type", "transaction_id", "created_at"):
                value = event.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    self._fail_event_schema(f"{field_name} must be a non-empty string")
            command_key = event.get("command_key")
            if command_key is not None and (
                not isinstance(command_key, str) or not command_key.strip()
            ):
                self._fail_event_schema("command_key must be a string or null")
            digest = event.get("request_digest")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                self._fail_event_schema("request_digest must be a SHA-256 digest")
            if not isinstance(event.get("data"), dict):
                self._fail_event_schema("event data must be a JSON object")
        return events

    def _fail_event_schema(self, message: str) -> None:
        raise AIWorkflowError(
            code="invalid_schema",
            message=f"Invalid events.jsonl: {message}",
            exit_code=4,
            details={"document": "events.jsonl"},
        )

    def find_event(self, command_key: str) -> dict[str, Any] | None:
        for event in reversed(self.read_events()):
            if event.get("command_key") == command_key:
                return event
        return None

    def has_pending_transactions(self) -> bool:
        transactions = self.data_root / "transactions"
        return transactions.is_dir() and any(path.is_dir() for path in transactions.iterdir())

    def inject_failure_after(self, replacements: int | None) -> None:
        self._fault_after_replacements = replacements

    def commit_locked(
        self,
        changes: Mapping[str, bytes | None],
        *,
        event_type: str,
        event_data: Mapping[str, Any],
        command_key: str,
        request_digest: str,
        additional_events: Sequence[tuple[str, Mapping[str, Any]]] = (),
    ) -> dict[str, Any]:
        existing = self.find_event(command_key)
        if existing is not None:
            if existing.get("request_digest") != request_digest:
                raise AIWorkflowError(
                    code="idempotency_conflict",
                    message="Command key was already used with different content.",
                    exit_code=6,
                    details={"command_key": command_key},
                )
            return existing

        normalized_changes = dict(changes)
        if str(EVENTS_PATH) in normalized_changes:
            raise ValueError("events.jsonl is managed by WorkspaceStore")
        for relative_path in normalized_changes:
            self.safe_path(relative_path)
        self._validate_json_changes(normalized_changes)

        transaction_id = f"tx-{uuid.uuid4().hex}"
        timestamp = now_iso()
        events = self.read_events()
        next_number = max(
            (int(str(event.get("event_id", "E-000000")).split("-")[-1]) for event in events),
            default=0,
        )
        primary_event = {
            "event_id": f"E-{next_number + 1:06d}",
            "type": event_type,
            "transaction_id": transaction_id,
            "command_key": command_key,
            "request_digest": request_digest,
            "created_at": timestamp,
            "data": dict(event_data),
        }
        new_events = [primary_event]
        for offset, (extra_type, extra_data) in enumerate(additional_events, start=2):
            new_events.append(
                {
                    "event_id": f"E-{next_number + offset:06d}",
                    "type": extra_type,
                    "transaction_id": transaction_id,
                    "command_key": None,
                    "request_digest": request_digest,
                    "created_at": timestamp,
                    "data": dict(extra_data),
                }
            )
        event_bytes = b"".join(json_line(event) for event in [*events, *new_events])
        normalized_changes[str(EVENTS_PATH)] = event_bytes

        transaction_root = self.data_root / "transactions" / transaction_id
        transaction_root.mkdir(parents=True)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "transaction_id": transaction_id,
            "command_key": command_key,
            "status": "building",
            "entries": [],
        }
        self._write_manifest(transaction_root, manifest)

        for relative_path in self._ordered_paths(normalized_changes):
            target = self.safe_path(relative_path)
            before_exists = target.is_file()
            if target.exists() and not before_exists:
                raise AIWorkflowError(
                    code="transaction_target_invalid",
                    message="Transaction targets must be files.",
                    exit_code=4,
                    details={"path": relative_path},
                )
            before_bytes = target.read_bytes() if before_exists else None
            after_bytes = normalized_changes[relative_path]
            if before_bytes is not None:
                before_path = transaction_root / "before" / relative_path
                self._atomic_write(before_path, before_bytes)
            if after_bytes is not None:
                after_path = transaction_root / "after" / relative_path
                self._atomic_write(after_path, after_bytes)
            manifest["entries"].append(
                {
                    "path": relative_path,
                    "before_exists": before_exists,
                    "before_sha256": sha256_bytes(before_bytes) if before_bytes is not None else None,
                    "after_exists": after_bytes is not None,
                    "after_sha256": sha256_bytes(after_bytes) if after_bytes is not None else None,
                }
            )

        manifest["status"] = "prepared"
        self._write_manifest(transaction_root, manifest)
        manifest["status"] = "committing"
        self._write_manifest(transaction_root, manifest)
        self._apply_images(transaction_root, manifest, image="after")
        manifest["status"] = "committed"
        self._write_manifest(transaction_root, manifest)
        shutil.rmtree(transaction_root)
        return primary_event

    def recover_locked(self) -> list[str]:
        recovered: list[str] = []
        transactions_root = self.data_root / "transactions"
        if not transactions_root.is_dir():
            return recovered
        for transaction_root in sorted(path for path in transactions_root.iterdir() if path.is_dir()):
            manifest_path = transaction_root / "manifest.json"
            if not manifest_path.is_file():
                shutil.rmtree(transaction_root)
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                raise AIWorkflowError(
                    code="corrupt_transaction",
                    message="Transaction manifest is not valid JSON.",
                    exit_code=4,
                    details={"path": str(manifest_path)},
                ) from error
            status = manifest.get("status")
            transaction_id = manifest.get("transaction_id")
            if status == "building":
                shutil.rmtree(transaction_root)
                continue
            if status in {"prepared", "committing"}:
                committed_event = any(
                    event.get("transaction_id") == transaction_id for event in self.read_events()
                )
                self._apply_images(
                    transaction_root,
                    manifest,
                    image="after" if committed_event else "before",
                    inject_fault=False,
                )
                recovered.append(f"{transaction_id}:{'commit' if committed_event else 'rollback'}")
            elif status == "committed":
                self._apply_images(transaction_root, manifest, image="after", inject_fault=False)
                recovered.append(f"{transaction_id}:commit")
            else:
                raise AIWorkflowError(
                    code="corrupt_transaction",
                    message="Transaction manifest has an unknown status.",
                    exit_code=4,
                    details={"path": str(manifest_path), "status": status},
                )
            shutil.rmtree(transaction_root)
        self._cleanup_orphan_work_locked(recovered)
        return recovered

    def replace_generated_locked(self, relative_path: str | Path, content: bytes) -> None:
        target = self.safe_path(relative_path)
        if target.exists() and not target.is_file():
            raise AIWorkflowError(
                code="generated_target_invalid",
                message="Generated output target must be a file.",
                exit_code=4,
                details={"path": str(relative_path)},
            )
        self._atomic_write(target, content)

    def _cleanup_orphan_work_locked(self, recovered: list[str]) -> None:
        state_path = self.data_root / "state.json"
        work_root = self.data_root / "work"
        if not state_path.is_file() or not work_root.is_dir():
            return
        try:
            state = validate_document(
                "state.json",
                json.loads(state_path.read_text(encoding="utf-8")),
            )
        except (OSError, json.JSONDecodeError):
            return
        active_work = state["active_work"]
        for path in work_root.iterdir():
            if path.is_dir() and path.name != active_work:
                shutil.rmtree(path)
                recovered.append(f"work:{path.name}:cleanup")

    def _validate_json_changes(self, changes: Mapping[str, bytes | None]) -> None:
        for relative_path, content in changes.items():
            if content is None or not relative_path.endswith(".json"):
                continue
            try:
                value = json.loads(content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise AIWorkflowError(
                    code="invalid_schema",
                    message="Transaction contains invalid JSON.",
                    exit_code=4,
                    details={"path": relative_path},
                ) from error
            name = Path(relative_path).name
            if name in DATA_FILES:
                validate_document(name, value)
            elif not isinstance(value, dict):
                raise AIWorkflowError(
                    code="invalid_schema",
                    message="Transaction JSON must contain an object.",
                    exit_code=4,
                    details={"path": relative_path},
                )

    def _ordered_paths(self, changes: Mapping[str, bytes | None]) -> list[str]:
        return sorted(changes, key=lambda path: (path == str(EVENTS_PATH), path))

    def _write_manifest(self, transaction_root: Path, manifest: Mapping[str, Any]) -> None:
        self._atomic_write(transaction_root / "manifest.json", json_bytes(manifest))

    def _apply_images(
        self,
        transaction_root: Path,
        manifest: Mapping[str, Any],
        *,
        image: str,
        inject_fault: bool = True,
    ) -> None:
        entries = list(manifest["entries"])
        if image == "before":
            entries.reverse()
        replacements = 0
        for entry in entries:
            relative_path = entry["path"]
            target = self.safe_path(relative_path)
            exists = entry[f"{image}_exists"]
            if exists:
                source = transaction_root / image / relative_path
                try:
                    source_bytes = source.read_bytes()
                except OSError as error:
                    raise AIWorkflowError(
                        code="corrupt_transaction",
                        message="Transaction image is missing.",
                        exit_code=4,
                        details={"path": str(source)},
                    ) from error
                if sha256_bytes(source_bytes) != entry[f"{image}_sha256"]:
                    raise AIWorkflowError(
                        code="corrupt_transaction",
                        message="Transaction image hash does not match its manifest.",
                        exit_code=4,
                        details={"path": str(source)},
                    )
                self._atomic_write(target, source_bytes)
            elif target.exists():
                if not target.is_file():
                    raise AIWorkflowError(
                        code="transaction_target_invalid",
                        message="Recovery target is not a file.",
                        exit_code=4,
                        details={"path": relative_path},
                    )
                target.unlink()
                self._fsync_directory(target.parent)
            replacements += 1
            if (
                inject_fault
                and self._fault_after_replacements is not None
                and replacements >= self._fault_after_replacements
            ):
                self._fault_after_replacements = None
                raise InjectedTransactionFailure(
                    f"injected failure after {replacements} replacements"
                )

    def _atomic_write(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, path)
            self._fsync_directory(path.parent)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _fsync_directory(self, directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
