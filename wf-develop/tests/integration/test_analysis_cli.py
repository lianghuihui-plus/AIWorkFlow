from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import run_cli


def initialize_workspace(root: Path, *, repository: Path | None = None) -> Path:
    repository = repository or root / "repository"
    repository.mkdir(exist_ok=True)
    app = repository / "app.txt"
    if not app.exists():
        app.write_text("ApplicationRoot\n", encoding="utf-8")
    prd = root / "requirements.md"
    prd.write_text("# Requirements\n\nSave drafts.\n", encoding="utf-8")
    workspace = root / "workspace"
    workspace.mkdir()
    completed = run_cli(
        [
            "init", "--workspace", str(workspace), "--platform", "test",
            "--prd", str(prd), "--code-repository", str(repository),
        ]
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return workspace


def result_of(completed: object) -> dict[str, object]:
    return json.loads(completed.stdout)["result"]


class AnalysisCommandLineTests(unittest.TestCase):
    def test_analysis_round_trip_uses_lightweight_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            work = result_of(run_cli(["prepare", "--workspace", str(workspace)]))
            Path(workspace / str(work["draft_output"])).write_text(
                "# Analysis\n\nSave drafts.\n", encoding="utf-8"
            )
            Path(workspace / str(work["result_output"])).write_text(
                json.dumps(
                    {
                        "schema_version": 11,
                        "stage": "analysis",
                        "target_platform": "test",
                        "requirements": [
                            {
                                "title": "Save drafts",
                                "summary": "Persist drafts.",
                                "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                                "platform_scope": "target",
                                "change_type": "new",
                                "scope_reason": "Target behavior.",
                                "disposition": "proposed",
                            }
                        ],
                        "withdrawn_requirements": [],
                    }
                ),
                encoding="utf-8",
            )
            submitted = result_of(run_cli(["submit", "--workspace", str(workspace), "--work-id", str(work["work_id"])]))
            result_of(run_cli(["review", "--workspace", str(workspace), "--artifact-id", "analysis", "--revision", "1", "--outcome", "approved"]))
            status = result_of(run_cli(["status", "--workspace", str(workspace)]))

            self.assertEqual(submitted["artifact_id"], "analysis")
            self.assertEqual(set(status["state"]), {"schema_version", "updated_at"})
            self.assertEqual(status["current_stage"], "design")

    def test_question_blocks_only_its_work_and_decision_auto_resumes_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            work = result_of(run_cli(["prepare", "--workspace", str(workspace)]))
            opened = result_of(
                run_cli(
                    [
                        "question", "--workspace", str(workspace), "--work-id", str(work["work_id"]),
                        "--items-json", json.dumps([{"question": "Retention?", "reason": "Changes behavior.", "recommendation": "Use 30 days."}]),
                    ]
                )
            )
            decided = result_of(
                run_cli(
                    ["decide", "--workspace", str(workspace), "--question-id", str(opened["question_ids"][0]), "--decision", "Use 30 days."]
                )
            )
            status = result_of(run_cli(["status", "--workspace", str(workspace)]))
            resumed = result_of(run_cli(["prepare", "--workspace", str(workspace)]))
            question = json.loads((workspace / ".aiwf/questions.json").read_text(encoding="utf-8"))["items"][0]
            decision = json.loads((workspace / ".aiwf/decisions.json").read_text(encoding="utf-8"))["items"][0]

            self.assertNotIn("routing_required", decided)
            self.assertEqual(status["active_works"][0]["status"], "active")
            self.assertEqual(status["counts"]["open_questions"], 0)
            self.assertNotIn("impact", question)
            self.assertNotIn("supersedes_decisions", question)
            self.assertNotIn("impact", decision)
            self.assertNotIn("supersedes", decision)
            self.assertNotIn("superseded_by", decision)
            self.assertIn("Retention?: Use 30 days.", resumed["decision_context"]["content"])

            reopened = result_of(
                run_cli(
                    [
                        "question", "--workspace", str(workspace), "--work-id", str(work["work_id"]),
                        "--items-json", json.dumps([{"question": "Encryption?", "reason": "Changes storage.", "recommendation": "Use AES-256."}]),
                    ]
                )
            )
            result_of(
                run_cli(
                    ["decide", "--workspace", str(workspace), "--question-id", str(reopened["question_ids"][0]), "--decision", "Use platform encryption."]
                )
            )
            resumed_again = result_of(run_cli(["prepare", "--workspace", str(workspace)]))
            self.assertIn("Retention?: Use 30 days.", resumed_again["decision_context"]["content"])
            self.assertIn(
                "Encryption?: Use platform encryption.",
                resumed_again["decision_context"]["content"],
            )


if __name__ == "__main__":
    unittest.main()
