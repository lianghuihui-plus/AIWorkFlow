from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import bootstrap_engine, write_work_outputs

from aiwf_core.decision_flow import validate_decision_revision_target
from aiwf_core.model import AIWorkflowError, SCHEMA_VERSION
from aiwf_core.workflow import WorkflowEngine


def analysis_result(*, requirement_id: str | None = None, summary: str = "Save a draft") -> dict[str, object]:
    requirement: dict[str, object] = {
        "title": "Save drafts",
        "summary": summary,
        "sources": [{"kind": "prd", "ref": "prd/requirements.md#save-drafts"}],
        "platform_scope": "target",
        "change_type": "new",
        "scope_reason": "Implemented by the initialized target platform.",
        "disposition": "proposed",
    }
    if requirement_id is not None:
        requirement["id"] = requirement_id
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "analysis",
        "target_platform": "test",
        "requirements": [requirement],
        "memory_delta": [
            {
                "operation": "add",
                "type": "architecture_decision",
                "content": summary,
                "evidence": [],
                "rationale": "Confirmed by the approved analysis.",
                "validation": None,
                "target_id": None,
            }
        ],
    }


def design_result() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "design",
        "requirements": ["REQ-001"],
        "design_mode": "anchored",
        "greenfield_reason": None,
        "code_evidence": [
            {"path": "app.txt", "symbol": "ApplicationRoot", "purpose": "Application integration root"}
        ],
        "memory_delta": [],
    }


def task_plan_result() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "specification",
        "tasks": [
            {
                "key": "draft-storage",
                "title": "Implement draft storage",
                "requirements": ["REQ-001"],
                "depends_on": [],
            }
        ],
        "memory_delta": [],
    }


def task_result(stage: str) -> dict[str, object]:
    common: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "stage": stage,
        "task_id": "T-001",
        "memory_delta": [],
    }
    if stage == "implementation":
        common.update(
            {
                "changed_files": ["src/drafts.txt"],
                "validation_summary": "Checked locally",
            }
        )
    elif stage == "testing":
        common.update(
            {
                "test_files": ["tests/test_drafts.txt"],
                "execution": {"command": "test", "exit_code": 0, "summary": "Passed"},
                "uncovered": [],
            }
        )
    return common


class WorkflowCoreTests(unittest.TestCase):
    def test_decision_supersession_applies_only_after_artifact_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))
            work = engine.prepare_work(goal="Analyze requirements")
            opened = engine.open_questions(
                work["work_id"],
                [
                    {
                        "question": "Should drafts sync?",
                        "reason": "This changes scope.",
                        "recommendation": "Keep drafts local.",
                        "impact": ["analysis", "design"],
                    }
                ],
            )
            engine.decide(opened["question_ids"][0], "Keep drafts local.")
            routed = engine.route_decision(work["work_id"], outcome="resume")
            resumed = engine.prepare_work(goal="ignored")
            self.assertEqual(resumed["work_id"], routed["successor_work_id"])
            result = analysis_result()
            result["superseded_decisions"] = ["D-001"]
            write_work_outputs(
                engine,
                resumed,
                markdown="# Analysis\n\nThe latest confirmed scope replaces the earlier answer.\n",
                result=result,
            )

            engine.submit_work(resumed["work_id"])
            self.assertEqual(
                engine.store.read_json("decisions.json")["items"][0]["status"],
                "active",
            )
            engine.review_artifact("analysis", 1, outcome="approved")

            updated = engine.store.read_json("decisions.json")["items"][0]
            self.assertEqual(updated["status"], "superseded")
            self.assertEqual(updated["superseded_by"], "analysis@1")

    def test_new_engine_instance_resumes_the_same_active_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))
            prepared = engine.prepare_work(goal="Analyze requirements")

            resumed = WorkflowEngine(Path(directory)).prepare_work(goal="Different caller text")

            self.assertEqual(resumed["work_id"], prepared["work_id"])
            self.assertEqual(resumed["goal"], "Analyze requirements")

    def test_active_work_metadata_drift_blocks_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))
            prepared = engine.prepare_work(goal="Analyze requirements")
            work_path = engine.store.safe_path(
                f".aiwf/work/{prepared['work_id']}/work.json"
            )
            work = json.loads(work_path.read_text(encoding="utf-8"))
            work["artifact"]["output"] = "artifacts/other.md"
            work_path.write_text(json.dumps(work), encoding="utf-8")

            with self.assertRaises(AIWorkflowError) as raised:
                engine.prepare_work(goal="Resume")

            self.assertEqual(raised.exception.code, "workspace_health_blocked")
            self.assertIn(
                "work_drift",
                {issue["type"] for issue in raised.exception.details["issues"]},
            )

    def test_analysis_submit_and_review_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))
            work = engine.prepare_work(goal="Analyze requirements")
            write_work_outputs(
                engine,
                work,
                markdown="# Analysis\n\nUsers can save drafts.\n",
                result=analysis_result(),
            )

            submitted = engine.submit_work(work["work_id"])
            repeated_submit = engine.submit_work(work["work_id"])

            self.assertEqual(submitted, repeated_submit)
            self.assertTrue((Path(directory) / "artifacts/analysis.md").is_file())
            self.assertFalse((Path(directory) / ".aiwf/work" / work["work_id"]).exists())
            self.assertEqual(engine.store.read_json("state.json")["mode"], "review")
            self.assertEqual(
                engine.store.read_json("requirements.json")["items"][0]["disposition"],
                "proposed",
            )

            approved = engine.review_artifact("analysis", 1, outcome="approved")
            repeated_approval = engine.review_artifact("analysis", 1, outcome="approved")

            self.assertEqual(approved, repeated_approval)
            state = engine.store.read_json("state.json")
            self.assertEqual((state["current_stage"], state["mode"]), ("design", "ready"))
            self.assertEqual(
                engine.store.read_json("requirements.json")["items"][0]["disposition"],
                "accepted",
            )
            memory = engine.store.read_json("memory.json")["items"]
            self.assertEqual(memory[0]["id"], "M-001")
            self.assertIn("Save a draft", (Path(directory) / ".aiwf/memory.md").read_text())

    def test_artifact_drift_blocks_review_and_is_reported_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))
            work = engine.prepare_work(goal="Analyze requirements")
            write_work_outputs(
                engine,
                work,
                markdown="# Analysis\n",
                result=analysis_result(),
            )
            engine.submit_work(work["work_id"])
            artifact_path = Path(directory) / "artifacts/analysis.md"
            artifact_path.write_text("# Changed outside workflow\n", encoding="utf-8")

            with self.assertRaises(AIWorkflowError) as raised:
                engine.review_artifact("analysis", 1, outcome="approved")

            self.assertEqual(raised.exception.code, "artifact_drift")
            inspection = engine.inspect()
            self.assertEqual(inspection["status"], "issues_found")
            self.assertIn("artifact_drift", {issue["type"] for issue in inspection["issues"]})
            self.assertEqual(engine.store.read_json("state.json")["mode"], "review")

    def test_direct_engine_prepare_uses_the_same_health_gate_as_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            engine = bootstrap_engine(workspace)
            work = engine.prepare_work(goal="Analyze requirements")
            write_work_outputs(
                engine,
                work,
                markdown="# Analysis\n",
                result=analysis_result(),
            )
            engine.submit_work(work["work_id"])
            engine.review_artifact("analysis", 1, outcome="approved")
            (workspace / "artifacts/analysis.md").write_text(
                "# External drift\n", encoding="utf-8"
            )

            with self.assertRaises(AIWorkflowError) as raised:
                engine.prepare_work(goal="Design")

            self.assertEqual(raised.exception.code, "workspace_health_blocked")

    def test_change_request_creates_a_seeded_successor_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))
            first_work = engine.prepare_work(goal="Analyze requirements")
            write_work_outputs(
                engine,
                first_work,
                markdown="# Analysis v1\n",
                result=analysis_result(),
            )
            engine.submit_work(first_work["work_id"])

            change = engine.review_artifact(
                "analysis",
                1,
                outcome="changes_requested",
                feedback="Clarify offline behavior.",
            )
            successor = engine.prepare_work(goal="Ignored while resuming")
            self.assertEqual(successor["work_id"], change["work_id"])
            self.assertEqual(successor["feedback"], "Clarify offline behavior.")
            self.assertIn("Analysis v1", engine.store.safe_path(successor["draft_output"]).read_text())
            successor_seed = json.loads(
                engine.store.safe_path(successor["result_output"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                successor_seed["memory_delta"],
                analysis_result()["memory_delta"],
            )
            self.assertNotIn("origin_revision", successor_seed["requirements"][0])
            with self.assertRaises(AIWorkflowError) as raised:
                engine.review_artifact(
                    "analysis",
                    1,
                    outcome="changes_requested",
                    feedback="Different feedback.",
                )
            self.assertEqual(raised.exception.code, "idempotency_conflict")

            write_work_outputs(
                engine,
                successor,
                markdown="# Analysis v2\n\nOffline behavior clarified.\n",
                result=analysis_result(summary="Save a draft offline"),
            )
            submitted = engine.submit_work(successor["work_id"])

            self.assertEqual(submitted["revision"], 2)
            history = Path(directory) / ".aiwf/history/analysis/1.md"
            self.assertEqual(history.read_text(encoding="utf-8"), "# Analysis v1\n")

    def test_blocking_questions_resume_with_a_successor_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))
            work = engine.prepare_work(goal="Analyze requirements")
            engine.store.safe_path(work["draft_output"]).write_text(
                "# Partial analysis\n",
                encoding="utf-8",
            )
            opened = engine.open_questions(
                work["work_id"],
                [
                    {
                        "question": "Keep drafts offline?",
                        "reason": "Storage behavior is ambiguous.",
                        "recommendation": "Keep a local copy.",
                        "impact": ["analysis", "design"],
                    },
                    {
                        "question": "Expire drafts?",
                        "reason": "Retention is not specified.",
                        "recommendation": "Do not expire automatically.",
                        "impact": ["analysis"],
                    },
                ],
            )
            self.assertEqual(engine.open_questions(work["work_id"], [
                {
                    "question": "Keep drafts offline?",
                    "reason": "Storage behavior is ambiguous.",
                    "recommendation": "Keep a local copy.",
                    "impact": ["analysis", "design"],
                },
                {
                    "question": "Expire drafts?",
                    "reason": "Retention is not specified.",
                    "recommendation": "Do not expire automatically.",
                    "impact": ["analysis"],
                },
            ]), opened)

            first = engine.decide(opened["question_ids"][0], "Keep drafts offline.")
            self.assertFalse(first["routing_required"])
            self.assertEqual(engine.store.read_json("state.json")["mode"], "blocked")
            second = engine.decide(opened["question_ids"][1], "Drafts do not expire.")
            self.assertTrue(second["routing_required"])
            self.assertEqual(engine.store.read_json("state.json")["mode"], "decision")
            routed = engine.route_decision(work["work_id"], outcome="resume")
            resumed = engine.prepare_work(goal="Ignored while resuming")
            self.assertEqual(resumed["work_id"], routed["successor_work_id"])
            self.assertEqual(
                engine.store.safe_path(resumed["draft_output"]).read_text(encoding="utf-8"),
                "# Partial analysis\n",
            )
            memory_markdown = (Path(directory) / ".aiwf/memory.md").read_text(encoding="utf-8")
            self.assertIn("Keep drafts offline.", memory_markdown)
            self.assertIn("Drafts do not expire.", memory_markdown)

    def test_confirmed_upstream_change_routes_to_revision_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            engine = bootstrap_engine(workspace)

            analysis = engine.prepare_work(goal="Analyze")
            write_work_outputs(engine, analysis, markdown="# Analysis\n", result=analysis_result())
            engine.submit_work(analysis["work_id"])
            engine.review_artifact("analysis", 1, outcome="approved")

            design = engine.prepare_work(goal="Design", depends_on=["analysis@1"])
            write_work_outputs(engine, design, markdown="# Design\n", result=design_result())
            engine.submit_work(design["work_id"])
            engine.review_artifact("design", 1, outcome="approved")

            task_plan = engine.prepare_work(goal="Plan tasks", depends_on=["design@1"])
            engine.store.safe_path(task_plan["draft_output"]).write_text(
                "# Partial task plan\n",
                encoding="utf-8",
            )
            opened = engine.open_questions(
                task_plan["work_id"],
                [
                    {
                        "question": "Should drafts sync across devices?",
                        "reason": "The answer changes the accepted requirement and architecture.",
                        "recommendation": "Keep the first version local.",
                        "impact": ["analysis", "design"],
                    }
                ],
            )
            engine.decide(opened["question_ids"][0], "Require cross-device synchronization.")

            routed = engine.route_decision(
                task_plan["work_id"],
                outcome="revise",
                artifact_id="analysis",
                revision=1,
            )

            revision_work = engine.prepare_work(goal="Ignored while resuming")
            self.assertEqual(revision_work["work_id"], routed["successor_work_id"])
            self.assertEqual(revision_work["stage"], "analysis")
            self.assertIn("Require cross-device synchronization", revision_work["feedback"])
            self.assertTrue(
                (workspace / ".aiwf/history/abandoned" / task_plan["work_id"] / "work.json").is_file()
            )
            event_types = [item["type"] for item in engine.store.read_events()]
            self.assertIn("decision_route_selected", event_types)
            self.assertIn("work_superseded", event_types)

    def test_decision_revision_allows_an_upstream_target_beyond_predicted_impact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))
            analysis = engine.prepare_work(goal="Analyze")
            write_work_outputs(engine, analysis, markdown="# Analysis\n", result=analysis_result())
            engine.submit_work(analysis["work_id"])
            engine.review_artifact("analysis", 1, outcome="approved")
            design = engine.prepare_work(goal="Design", depends_on=["analysis@1"])
            write_work_outputs(engine, design, markdown="# Design\n", result=design_result())
            engine.submit_work(design["work_id"])
            engine.review_artifact("design", 1, outcome="approved")
            task_plan = engine.prepare_work(goal="Plan tasks", depends_on=["design@1"])
            opened = engine.open_questions(
                task_plan["work_id"],
                [
                    {
                        "question": "Clarify architecture naming?",
                        "reason": "The module name is ambiguous.",
                        "recommendation": "Keep the existing name.",
                        "impact": ["design"],
                    }
                ],
            )
            engine.decide(opened["question_ids"][0], "Rename the design module.")

            routed = engine.route_decision(
                task_plan["work_id"],
                outcome="revise",
                artifact_id="analysis",
                revision=1,
            )

            self.assertTrue(routed["impact_expanded"])
            self.assertEqual(routed["declared_impacts"], ["design"])
            self.assertEqual(routed["target_stage"], "analysis")

    def test_decision_revision_still_rejects_a_non_upstream_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))
            work = {"depends_on": ["design@1"]}
            design = {
                "id": "design",
                "revision": 1,
                "stage": "design",
                "depends_on": ["analysis@1"],
            }
            analysis = {
                "id": "analysis",
                "revision": 1,
                "stage": "analysis",
                "depends_on": [],
            }
            unrelated = {
                "id": "T-999-spec",
                "revision": 1,
                "stage": "specification",
                "depends_on": [],
            }
            resolved = [
                {
                    "question": {"impact": ["specification"]},
                    "decision": {"id": "D-001", "decision": "Revise the unrelated task."},
                }
            ]

            with self.assertRaises(AIWorkflowError) as raised:
                validate_decision_revision_target(
                    work,
                    unrelated,
                    resolved,
                    {"items": [analysis, design, unrelated]},
                )

            self.assertEqual(raised.exception.code, "invalid_decision_route")

    def test_upstream_revision_recursively_invalidates_all_downstream_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))

            analysis = engine.prepare_work(goal="Analyze")
            write_work_outputs(engine, analysis, markdown="# Analysis\n", result=analysis_result())
            engine.submit_work(analysis["work_id"])
            engine.review_artifact("analysis", 1, outcome="approved")

            design = engine.prepare_work(goal="Design", depends_on=["analysis@1"])
            write_work_outputs(engine, design, markdown="# Design\n", result=design_result())
            engine.submit_work(design["work_id"])
            engine.review_artifact("design", 1, outcome="approved")

            task_plan = engine.prepare_work(
                goal="Plan tasks",
                depends_on=["design@1"],
            )
            write_work_outputs(
                engine,
                task_plan,
                markdown="# Task Plan\n",
                result=task_plan_result(),
            )
            engine.submit_work(task_plan["work_id"])
            engine.review_artifact("task-plan", 1, outcome="approved")

            specification = engine.prepare_work(
                goal="Specify T-001",
                active_item="T-001",
                depends_on=["task-plan@1"],
            )
            write_work_outputs(
                engine,
                specification,
                markdown="# Specification\n",
                result=task_result("specification"),
            )
            engine.submit_work(specification["work_id"])
            engine.review_artifact("T-001-spec", 1, outcome="approved")

            implementation = engine.prepare_work(
                goal="Implement T-001",
                active_item="T-001",
                depends_on=["T-001-spec@1"],
            )
            write_work_outputs(
                engine,
                implementation,
                markdown="# Implementation\n",
                result=task_result("implementation"),
            )
            engine.submit_work(implementation["work_id"])
            engine.review_artifact("T-001-implementation", 1, outcome="approved")

            testing = engine.prepare_work(
                goal="Test T-001",
                active_item="T-001",
                depends_on=["T-001-implementation@1"],
            )
            write_work_outputs(
                engine,
                testing,
                markdown="# Tests\n",
                result=task_result("testing"),
            )
            engine.submit_work(testing["work_id"])
            engine.review_artifact("T-001-test", 1, outcome="approved")
            self.assertEqual(engine.store.read_json("state.json")["current_stage"], "completed")

            revision_request = engine.request_revision(
                "analysis",
                1,
                feedback="Clarify cross-device behavior.",
            )
            revision_work = engine.prepare_work(goal="Ignored while resuming")
            self.assertEqual(revision_work["work_id"], revision_request["work_id"])
            write_work_outputs(
                engine,
                revision_work,
                markdown="# Analysis\n",
                result=analysis_result(
                    requirement_id="REQ-001",
                    summary="Save and resume drafts across devices",
                ),
            )
            submitted = engine.submit_work(revision_work["work_id"])

            self.assertEqual(
                set(submitted["invalidated"]),
                {
                    "design",
                    "task-plan",
                    "T-001-spec",
                    "T-001-implementation",
                    "T-001-test",
                },
            )
            artifacts = {
                item["id"]: item for item in engine.store.read_json("artifacts.json")["items"]
            }
            for artifact_id in submitted["invalidated"]:
                self.assertEqual(artifacts[artifact_id]["status"], "stale")
            self.assertEqual(engine.store.read_json("tasks.json")["items"][0]["status"], "stale")
            self.assertTrue(engine.inspect()["can_advance"])
            engine.review_artifact("analysis", 2, outcome="approved")
            self.assertEqual(
                engine.store.read_json("state.json")["current_stage"],
                "design",
            )

    def test_projected_health_failure_does_not_commit_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))

            analysis = engine.prepare_work(goal="Analyze")
            write_work_outputs(engine, analysis, markdown="# Analysis\n", result=analysis_result())
            engine.submit_work(analysis["work_id"])
            engine.review_artifact("analysis", 1, outcome="approved")

            design = engine.prepare_work(goal="Design")
            write_work_outputs(engine, design, markdown="# Design\n", result=design_result())
            engine.submit_work(design["work_id"])
            engine.review_artifact("design", 1, outcome="approved")

            task_plan = engine.prepare_work(goal="Plan")
            write_work_outputs(
                engine,
                task_plan,
                markdown="# Task Plan\n",
                result=task_plan_result(),
            )
            engine.submit_work(task_plan["work_id"])
            before = {
                name: engine.store.safe_path(f".aiwf/{name}").read_bytes()
                for name in ("state.json", "artifacts.json", "tasks.json", "events.jsonl")
            }
            requirements = engine.store.read_json("requirements.json")
            empty_tasks = {"schema_version": SCHEMA_VERSION, "items": []}

            with patch(
                "aiwf_core.workflow.approve_indexes",
                return_value=(requirements, empty_tasks),
            ):
                with self.assertRaises(AIWorkflowError) as raised:
                    engine.review_artifact("task-plan", 1, outcome="approved")

            self.assertEqual(raised.exception.code, "workspace_health_blocked")
            self.assertEqual(
                raised.exception.details["issues"][0]["type"],
                "uncovered_requirements",
            )
            after = {
                name: engine.store.safe_path(f".aiwf/{name}").read_bytes()
                for name in before
            }
            self.assertEqual(after, before)

    def test_result_manifest_drift_also_blocks_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))
            work = engine.prepare_work(goal="Analyze requirements")
            write_work_outputs(
                engine,
                work,
                markdown="# Analysis\n",
                result=analysis_result(),
            )
            submitted = engine.submit_work(work["work_id"])
            artifact = next(
                item
                for item in engine.store.read_json("artifacts.json")["items"]
                if item["id"] == submitted["artifact_id"]
            )
            result_path = engine.store.safe_path(artifact["result_path"])
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["requirements"][0]["summary"] = "Changed outside the workflow"
            result_path.write_text(json.dumps(result), encoding="utf-8")

            with self.assertRaises(AIWorkflowError) as raised:
                engine.review_artifact("analysis", 1, outcome="approved")

            self.assertEqual(raised.exception.code, "artifact_drift")


if __name__ == "__main__":
    unittest.main()
