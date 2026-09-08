"""Read-only code repository context captured when task work starts."""

from __future__ import annotations

import copy
import hashlib
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .model import AIWorkflowError


def start_repository_session(raw: str | Mapping[str, Any]) -> dict[str, Any]:
    context = (
        copy.deepcopy(dict(raw))
        if isinstance(raw, Mapping)
        else inspect_repository(raw)
    )
    context["carried_changes"] = []
    context["pause_checkpoint"] = None
    return context


def checkpoint_repository_session(session: Mapping[str, Any]) -> dict[str, Any]:
    current = inspect_repository(str(session["root"]))
    comparison = compare_repository_context(dict(session), current)
    carried = set(session.get("carried_changes", []))
    if comparison["changed_files"] is not None:
        carried.update(comparison["changed_files"])
    updated = copy.deepcopy(dict(session))
    updated["carried_changes"] = sorted(carried)
    updated["pause_checkpoint"] = current
    return updated


def resume_repository_session(session: Mapping[str, Any]) -> dict[str, Any]:
    checkpoint = session.get("pause_checkpoint")
    if not isinstance(checkpoint, dict):
        raise AIWorkflowError(
            code="repository_pause_checkpoint_missing",
            message="Blocked work does not have a repository pause checkpoint.",
            exit_code=7,
        )
    current = inspect_repository(str(session["root"]))
    try:
        paused_changes = compare_repository_context(checkpoint, current)["changed_files"]
    except AIWorkflowError as error:
        raise AIWorkflowError(
            code="repository_pause_conflict",
            message="Repository identity or Git HEAD changed while work awaited a decision.",
            exit_code=7,
            details={"cause": error.code, **error.details},
        ) from error
    carried = set(session.get("carried_changes", []))
    overlap = sorted(carried & set(paused_changes or []))
    if overlap:
        raise AIWorkflowError(
            code="repository_pause_conflict",
            message="Files already owned by the active work changed while it awaited a decision.",
            exit_code=7,
            details={"paths": overlap},
        )
    resumed = copy.deepcopy(current)
    resumed["carried_changes"] = sorted(carried)
    resumed["pause_checkpoint"] = None
    return resumed


def compare_repository_session(
    session: Mapping[str, Any],
    current: Mapping[str, Any],
) -> dict[str, Any]:
    comparison = compare_repository_context(dict(session), dict(current))
    changed = comparison["changed_files"]
    if changed is not None:
        comparison["changed_files"] = sorted(
            set(changed) | set(session.get("carried_changes", []))
        )
    return comparison


def inspect_repository(raw_path: str) -> dict[str, Any]:
    repository = Path(raw_path).resolve()
    if not repository.is_dir():
        raise AIWorkflowError(
            code="code_repository_unavailable",
            message="Configured code repository is not accessible.",
            exit_code=6,
            details={"path": raw_path},
        )

    git_root = _git_output(repository, "rev-parse", "--show-toplevel")
    if git_root is None:
        return {
            "type": "directory",
            "path": str(repository),
            "root": str(repository),
            "git_root": None,
            "scope_prefix": "",
            "head": None,
            "status_lines": [],
            "verification_level": "limited",
            "status_fingerprints": {},
        }
    resolved_git_root = Path(git_root).resolve()
    try:
        scope_prefix = repository.relative_to(resolved_git_root).as_posix()
    except ValueError as error:
        raise AIWorkflowError(
            code="repository_context_changed",
            message="Configured repository is outside its detected Git root.",
            exit_code=6,
            details={"path": str(repository), "git_root": str(resolved_git_root)},
        ) from error
    if scope_prefix == ".":
        scope_prefix = ""
    head = _git_output(repository, "rev-parse", "HEAD")
    status = _git_output(
        repository,
        "-c",
        "core.quotePath=false",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    all_status_lines = status.splitlines() if status else []
    all_entries = _status_entries(all_status_lines)
    scope_entries = {
        scoped: status_code
        for path, status_code in all_entries.items()
        if (scoped := _scope_relative_path(path, scope_prefix)) is not None
    }
    scope_status_lines = [
        f"{status_code} {path}" for path, status_code in sorted(scope_entries.items())
    ]
    return {
        "type": "git",
        "path": str(repository),
        "root": str(repository),
        "git_root": str(resolved_git_root),
        "scope_prefix": scope_prefix,
        "head": head,
        "status_lines": scope_status_lines,
        "verification_level": "git_delta",
        "status_fingerprints": {
            path: {
                "status": status_code,
                "sha256": _path_fingerprint(resolved_git_root, path),
            }
            for path, status_code in all_entries.items()
        },
    }


def compare_repository_context(
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """Return repository paths whose worktree state changed after prepare."""

    if (
        baseline["root"] != current["root"]
        or baseline["type"] != current["type"]
        or baseline.get("git_root") != current.get("git_root")
        or baseline.get("scope_prefix") != current.get("scope_prefix")
    ):
        raise AIWorkflowError(
            code="repository_context_changed",
            message="Code repository identity changed while work was active.",
            exit_code=6,
            details={"before": baseline["root"], "after": current["root"]},
        )
    if baseline["type"] != "git":
        return {"verification_level": "limited", "changed_files": None}
    if baseline.get("head") != current.get("head"):
        raise AIWorkflowError(
            code="repository_baseline_changed",
            message="Git HEAD changed while work was active.",
            exit_code=6,
            details={"before": baseline.get("head"), "after": current.get("head")},
        )

    before = baseline.get("status_fingerprints", {})
    after = current.get("status_fingerprints", {})
    changed_at_git_root = sorted(
        path
        for path in set(before) | set(after)
        if before.get(path) != after.get(path)
    )
    scope_prefix = str(baseline.get("scope_prefix", ""))
    changed: list[str] = []
    outside: list[str] = []
    for path in changed_at_git_root:
        scoped = _scope_relative_path(path, scope_prefix)
        if scoped is None:
            outside.append(path)
        else:
            changed.append(scoped)
    if outside:
        raise AIWorkflowError(
            code="repository_scope_violation",
            message="Work changed files outside the configured code repository scope.",
            exit_code=6,
            details={"paths": outside, "scope": baseline["root"]},
        )
    return {"verification_level": "git_delta", "changed_files": sorted(changed)}


def validate_repository_evidence(
    raw_root: str,
    evidence: Sequence[Mapping[str, Any]],
) -> None:
    """Validate existing code references without inferring their business meaning."""

    root = Path(raw_root).resolve()
    if not root.is_dir():
        raise AIWorkflowError(
            code="code_repository_unavailable",
            message="Configured code repository is not accessible.",
            exit_code=6,
            details={"path": raw_root},
        )
    for item in evidence:
        relative = normalize_repository_path(str(item["path"]))
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise AIWorkflowError(
                code="repository_evidence_missing",
                message="Existing code evidence must reference a file inside the configured repository.",
                exit_code=4,
                details={"path": relative},
            )
        symbol = str(item["symbol"])
        try:
            found = symbol.encode("utf-8") in path.read_bytes()
        except OSError as error:
            raise AIWorkflowError(
                code="repository_evidence_unreadable",
                message="Existing code evidence could not be read.",
                exit_code=4,
                details={"path": relative},
            ) from error
        if not found:
            raise AIWorkflowError(
                code="repository_symbol_missing",
                message="Existing code evidence symbol was not found in the referenced file.",
                exit_code=4,
                details={"path": relative, "symbol": symbol},
            )


def normalize_repository_path(raw_path: str) -> str:
    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts or not path.parts or raw_path.strip() != raw_path:
        raise AIWorkflowError(
            code="invalid_repository_path",
            message="Repository evidence paths must be relative paths inside the repository.",
            exit_code=4,
            details={"path": raw_path},
        )
    return path.as_posix()


def _status_entries(lines: list[str]) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in lines:
        if len(line) < 4:
            continue
        status = line[:2]
        raw_path = line[3:]
        if " -> " in raw_path:
            old_path, new_path = raw_path.split(" -> ", 1)
            entries[old_path] = status
            entries[new_path] = status
        else:
            entries[raw_path] = status
    return entries


def repository_has_files(raw_root: str) -> bool:
    root = Path(raw_root).resolve()
    if not root.is_dir():
        raise AIWorkflowError(
            code="code_repository_unavailable",
            message="Configured code repository is not accessible.",
            exit_code=6,
            details={"path": raw_root},
        )
    return any(path.name != ".git" for path in root.iterdir())


def _scope_relative_path(path: str, scope_prefix: str) -> str | None:
    if not scope_prefix:
        return path
    prefix = f"{scope_prefix}/"
    if path == scope_prefix:
        return "."
    if path.startswith(prefix):
        return path[len(prefix) :]
    return None


def _path_fingerprint(root: Path, relative_path: str) -> str | None:
    path = root / relative_path
    try:
        if path.is_symlink():
            payload = f"symlink:{path.readlink()}".encode("utf-8")
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        elif path.is_dir():
            payload = b"directory"
        else:
            return None
    except OSError:
        return None
    return hashlib.sha256(payload).hexdigest()


def _git_output(repository: Path, *arguments: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.rstrip("\r\n")
