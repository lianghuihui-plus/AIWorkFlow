#!/usr/bin/env python3
"""Command-line entry point for the rewritten AIWorkFlow engine."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import TextIO

from aiwf_core import __version__
from aiwf_core.model import AIWorkflowError, COMMAND_SPECS, CommandRequest
from aiwf_core.storage import resolve_workspace
from aiwf_core.workflow import execute


class StructuredArgumentParser(argparse.ArgumentParser):
    """Emit parse failures using the same JSON envelope as engine errors."""

    def error(self, message: str) -> None:
        error = AIWorkflowError(
            code="invalid_arguments",
            message=message,
            exit_code=2,
        )
        write_error(error, sys.stderr)
        raise SystemExit(error.exit_code)


def build_parser() -> argparse.ArgumentParser:
    parser = StructuredArgumentParser(
        prog="aiwf",
        description="Deterministic workflow engine for AIWorkFlow skills.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=StructuredArgumentParser,
    )

    for spec in COMMAND_SPECS:
        command_parser = subparsers.add_parser(spec.name, help=spec.help)
        command_parser.add_argument(
            "--workspace",
            default=".",
            help="Workspace directory (default: current directory).",
        )
        if spec.name == "init":
            command_parser.add_argument(
                "--name",
                help="Project name (default: workspace directory name).",
            )
            command_parser.add_argument("--platform", required=True, help="Target platform.")
            command_parser.add_argument(
                "--prd",
                action="append",
                required=True,
                help="PRD file or directory; repeat for multiple inputs.",
            )
            command_parser.add_argument(
                "--code-repository",
                required=True,
                help="Existing code repository directory.",
            )
            command_parser.add_argument("--project-id", help="Optional stable project id.")
        elif spec.name == "prepare":
            command_parser.add_argument(
                "--task-id",
                help="Optional task id; the engine selects the next task by default.",
            )
            command_parser.add_argument(
                "--instruction",
                help="Optional user instruction to append to the current stage goal.",
            )
        elif spec.name == "submit":
            command_parser.add_argument("--work-id", required=True, help="Active work id.")
        elif spec.name == "review":
            command_parser.add_argument("--artifact-id", required=True, help="Artifact id.")
            command_parser.add_argument("--revision", required=True, type=int, help="Revision number.")
            command_parser.add_argument(
                "--outcome",
                required=True,
                choices=("approved", "changes_requested"),
                help="Review outcome.",
            )
            command_parser.add_argument("--feedback", help="Required change feedback.")
        elif spec.name == "revise":
            command_parser.add_argument("--artifact-id", required=True, help="Artifact id.")
            command_parser.add_argument("--revision", required=True, type=int, help="Approved revision.")
            command_parser.add_argument("--feedback", required=True, help="Required revision feedback.")
            command_parser.add_argument(
                "--supersede-active-work",
                action="store_true",
                help="Archive conflicting unfinished work after explicit user confirmation.",
            )
        elif spec.name == "resolve-drift":
            command_parser.add_argument("--artifact-id", required=True, help="Artifact id.")
            command_parser.add_argument("--revision", required=True, type=int, help="Current revision.")
            command_parser.add_argument(
                "--outcome",
                required=True,
                choices=("adopt", "discard"),
                help="Adopt external content into successor work or restore the recorded snapshot.",
            )
            command_parser.add_argument(
                "--feedback",
                help="Required user feedback when adopting external content.",
            )
            command_parser.add_argument(
                "--supersede-active-work",
                action="store_true",
                help="Archive conflicting unfinished work after explicit user confirmation.",
            )
        elif spec.name == "question":
            command_parser.add_argument("--work-id", required=True, help="Active work id.")
            command_parser.add_argument(
                "--items-json",
                required=True,
                help="JSON array of all blocking questions for this work.",
            )
        elif spec.name == "decide":
            command_parser.add_argument("--question-id", required=True, help="Open question id.")
            command_parser.add_argument("--decision", required=True, help="User decision text.")
        elif spec.name == "route-decision":
            command_parser.add_argument("--work-id", required=True, help="Decision work id.")
            command_parser.add_argument(
                "--outcome",
                required=True,
                choices=("resume", "revise"),
                help="Resume the current work or revise an approved upstream artifact.",
            )
            command_parser.add_argument("--artifact-id", help="Approved upstream artifact id.")
            command_parser.add_argument("--revision", type=int, help="Approved revision number.")
        elif spec.name == "route-upstream":
            command_parser.add_argument("--work-id", required=True, help="Active work id.")
            command_parser.add_argument(
                "--artifact-id", required=True, help="Approved upstream artifact id."
            )
            command_parser.add_argument(
                "--revision", required=True, type=int, help="Approved revision number."
            )
            command_parser.add_argument(
                "--correction", required=True, help="Repository-backed factual correction."
            )
            command_parser.add_argument(
                "--evidence-json",
                required=True,
                help="JSON array of repository path and symbol evidence.",
            )
    return parser


def write_error(error: AIWorkflowError, stream: TextIO) -> None:
    json.dump(
        {
            "ok": False,
            "error": error.as_dict(),
        },
        stream,
        ensure_ascii=False,
        sort_keys=True,
    )
    stream.write("\n")


def write_result(payload: dict[str, object], stream: TextIO) -> None:
    json.dump(
        {
            "ok": True,
            "result": payload,
        },
        stream,
        ensure_ascii=False,
        sort_keys=True,
    )
    stream.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        workspace = resolve_workspace(args.workspace)
        options = {
            key: value
            for key, value in vars(args).items()
            if key not in {"command", "workspace"} and value is not None
        }
        result = execute(
            CommandRequest(command=args.command, workspace=workspace, options=options)
        )
    except AIWorkflowError as error:
        write_error(error, sys.stderr)
        return error.exit_code
    except Exception:
        error = AIWorkflowError(
            code="internal_error",
            message="The workflow engine failed unexpectedly.",
            exit_code=70,
        )
        write_error(error, sys.stderr)
        return error.exit_code

    write_result(result, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
