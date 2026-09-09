"""Minimal code-repository access helpers.

The workflow treats the configured repository as business context. It never
uses Git state, branches, commits, or file ownership as workflow gates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def repository_context(raw_path: str) -> dict[str, Any]:
    root = Path(raw_path).expanduser().resolve()
    return {"root": str(root)}
