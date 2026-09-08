"""Conservative repository file-role classification for stage boundaries."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from .model import AIWorkflowError


TEST_DIRECTORIES = {
    "test",
    "tests",
    "__tests__",
    "unittest",
    "unittests",
    "ohostest",
    "instrumentationtest",
}


def classify_file_role(relative_path: str) -> str:
    path = PurePosixPath(relative_path)
    lowered_parts = {part.lower() for part in path.parts[:-1]}
    filename = path.name.lower()
    if lowered_parts & TEST_DIRECTORIES:
        return "test"
    if (
        filename.startswith("test_")
        or re.search(r"(?:^|[._-])tests?(?:[._-]|$)", filename)
        or re.search(r"(?:^|[._-])spec(?:[._-]|$)", filename)
        or filename.endswith("tests.swift")
    ):
        return "test"
    production_directories = {"src", "source", "sources", "app", "lib", "library"}
    if any(part.lower() in production_directories for part in path.parts[:-1]):
        return "production"
    return "ambiguous"


def validate_stage_file_roles(stage: str, paths: list[str]) -> None:
    violations = [
        path
        for path in paths
        if (stage == "implementation" and classify_file_role(path) == "test")
        or (stage == "testing" and classify_file_role(path) == "production")
    ]
    if not violations:
        return
    raise AIWorkflowError(
        code="repository_stage_scope_violation",
        message=(
            "Implementation cannot modify clear test files."
            if stage == "implementation"
            else "Testing cannot modify clear production files."
        ),
        exit_code=4,
        details={"stage": stage, "paths": sorted(violations)},
    )
