from __future__ import annotations

import unittest

import support  # noqa: F401

from aiwf_core.artifacts import (
    reconcile_tasks,
    result_seed_from_record,
    validate_design_coverage,
    validate_result_manifest,
)
from aiwf_core.model import AIWorkflowError, SCHEMA_VERSION


class ArtifactResultTests(unittest.TestCase):
    def test_analysis_requires_structured_sources(self) -> None:
        result = {
            "schema_version": SCHEMA_VERSION,
            "stage": "analysis",
            "target_platform": "web",
            "requirements": [
                {
                    "title": "Save drafts",
                    "summary": "Users save drafts.",
                    "sources": ["prd/requirements.md"],
                    "platform_scope": "target",
                    "change_type": "new",
                    "scope_reason": "Implemented by web.",
                    "disposition": "proposed",
                }
            ],
            "memory_delta": [],
        }

        with self.assertRaises(AIWorkflowError) as raised:
            validate_result_manifest("analysis", result, active_item=None)

        self.assertEqual(raised.exception.code, "invalid_schema")

    def test_memory_type_contracts_are_enforced(self) -> None:
        result = {
            "schema_version": SCHEMA_VERSION,
            "stage": "specification",
            "task_id": "T-001",
            "memory_delta": [
                {
                    "operation": "add",
                    "type": "engineering_default",
                    "content": "Use the repository default timeout.",
                    "evidence": [],
                    "rationale": "Matches the surrounding module.",
                    "validation": None,
                }
            ],
        }

        with self.assertRaises(AIWorkflowError) as raised:
            validate_result_manifest("specification", result, active_item="T-001")

        self.assertIn("engineering_default requires validation", raised.exception.message)

    def test_testing_result_rejects_boolean_exit_code(self) -> None:
        with self.assertRaises(AIWorkflowError) as raised:
            validate_result_manifest(
                "testing",
                {
                    "schema_version": SCHEMA_VERSION,
                    "stage": "testing",
                    "task_id": "T-001",
                    "memory_delta": [],
                    "test_files": [],
                    "execution": {"command": None, "exit_code": True, "summary": ""},
                    "uncovered": [],
                },
                active_item="T-001",
            )

        self.assertEqual(raised.exception.code, "invalid_schema")

    def test_result_seed_removes_engine_fields_and_applied_memory_delta(self) -> None:
        record = {
            "schema_version": SCHEMA_VERSION,
            "stage": "analysis",
            "target_platform": "web",
            "superseded_decisions": ["D-001"],
            "memory_delta": [
                {
                    "operation": "add",
                    "type": "architecture_decision",
                    "content": "Users save drafts.",
                    "evidence": [],
                    "rationale": "Confirmed by analysis.",
                    "validation": None,
                }
            ],
            "requirements": [
                {
                    "id": "REQ-001",
                    "title": "Save a draft",
                    "summary": "Users save drafts.",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "platform_scope": "target",
                    "change_type": "new",
                    "scope_reason": "Implemented by the web client.",
                    "disposition": "proposed",
                    "origin_revision": 1,
                }
            ],
            "artifact_id": "analysis",
            "artifact_type": "analysis",
            "revision": 1,
        }

        seed = result_seed_from_record(
            "analysis", record, preserve_memory_delta=False
        )

        self.assertEqual(seed["memory_delta"], [])
        self.assertEqual(seed["superseded_decisions"], [])
        self.assertNotIn("artifact_id", seed)
        self.assertNotIn("origin_revision", seed["requirements"][0])

    def test_analysis_revision_seed_restores_agent_inference_self_reference(self) -> None:
        record = {
            "schema_version": SCHEMA_VERSION,
            "stage": "analysis",
            "target_platform": "web",
            "memory_delta": [],
            "requirements": [
                {
                    "id": "REQ-001",
                    "title": "Inferred behavior",
                    "summary": "Behavior inferred during analysis.",
                    "sources": [{"kind": "agent_inference", "ref": "analysis@1"}],
                    "platform_scope": "target",
                    "change_type": "new",
                    "scope_reason": "Required by the target client.",
                    "disposition": "proposed",
                    "origin_revision": 1,
                }
            ],
        }

        seed = result_seed_from_record(
            "analysis", record, preserve_memory_delta=False
        )

        self.assertEqual(seed["requirements"][0]["sources"][0]["ref"], "self")
        validate_result_manifest("analysis", seed, active_item=None)

    def test_unapproved_result_seed_preserves_candidate_memory_delta(self) -> None:
        record = {
            "schema_version": SCHEMA_VERSION,
            "stage": "analysis",
            "target_platform": "web",
            "memory_delta": [
                {
                    "operation": "add",
                    "type": "architecture_decision",
                    "content": "Users save drafts.",
                    "evidence": [],
                    "rationale": "Confirmed by analysis.",
                    "validation": None,
                }
            ],
            "requirements": [
                {
                    "id": "REQ-001",
                    "title": "Save a draft",
                    "summary": "Users save drafts.",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "platform_scope": "target",
                    "change_type": "new",
                    "scope_reason": "Implemented by the web client.",
                    "disposition": "proposed",
                    "origin_revision": 1,
                }
            ],
        }

        seed = result_seed_from_record(
            "analysis", record, preserve_memory_delta=True
        )

        self.assertEqual(seed["memory_delta"], record["memory_delta"])
        self.assertNotIn("origin_revision", seed["requirements"][0])

    def test_result_manifest_rejects_nested_engine_fields(self) -> None:
        result = {
            "schema_version": SCHEMA_VERSION,
            "stage": "analysis",
            "target_platform": "web",
            "memory_delta": [],
            "requirements": [
                {
                    "id": "REQ-001",
                    "title": "Save a draft",
                    "summary": "Users save drafts.",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "platform_scope": "target",
                    "change_type": "new",
                    "scope_reason": "Implemented by the web client.",
                    "disposition": "proposed",
                    "origin_revision": 1,
                }
            ],
        }

        with self.assertRaises(AIWorkflowError) as raised:
            validate_result_manifest("analysis", result, active_item=None)

        self.assertEqual(raised.exception.code, "invalid_schema")
        self.assertIn("origin_revision", raised.exception.message)

    def test_analysis_allows_a_fully_filtered_prd(self) -> None:
        validate_result_manifest(
            "analysis",
            {
                "schema_version": SCHEMA_VERSION,
                "stage": "analysis",
                "target_platform": "web",
                "memory_delta": [],
                "requirements": [
                    {
                        "title": "Native-only capability",
                        "summary": "No web implementation is needed.",
                        "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                        "platform_scope": "other",
                        "change_type": "reuse",
                        "scope_reason": "The existing native client owns this capability.",
                        "disposition": "excluded",
                    }
                ],
            },
            active_item=None,
        )

    def test_analysis_rejects_an_empty_requirement_set(self) -> None:
        with self.assertRaises(AIWorkflowError):
            validate_result_manifest(
                "analysis",
                {
                    "schema_version": SCHEMA_VERSION,
                    "stage": "analysis",
                    "target_platform": "web",
                    "memory_delta": [],
                    "requirements": [],
                },
                active_item=None,
            )

    def test_analysis_result_rejects_an_unsupported_disposition(self) -> None:
        result = {
            "schema_version": SCHEMA_VERSION,
            "stage": "analysis",
            "target_platform": "web",
            "memory_delta": [],
            "requirements": [
                {
                    "title": "Unresolved",
                    "summary": "A business choice is unresolved.",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "platform_scope": "target",
                    "change_type": "new",
                    "scope_reason": "Implemented by the web client.",
                    "disposition": "unsupported",
                }
            ],
        }

        with self.assertRaises(AIWorkflowError) as raised:
            validate_result_manifest("analysis", result, active_item=None)

        self.assertEqual(raised.exception.code, "invalid_schema")

    def test_task_plan_dependencies_use_submission_keys_then_normalize_to_ids(self) -> None:
        result = {
            "schema_version": SCHEMA_VERSION,
            "stage": "specification",
            "memory_delta": [],
            "tasks": [
                {
                    "key": "base",
                    "title": "Base task",
                    "requirements": ["REQ-001"],
                    "depends_on": [],
                },
                {
                    "key": "dependent",
                    "title": "Dependent task",
                    "requirements": ["REQ-001"],
                    "depends_on": ["base"],
                },
            ],
        }
        validate_result_manifest("specification", result, active_item=None)

        tasks, normalized = reconcile_tasks(
            {"schema_version": SCHEMA_VERSION, "items": []},
            {
                "schema_version": SCHEMA_VERSION,
                "items": [
                    {
                        "id": "REQ-001",
                        "title": "Requirement",
                        "summary": "Summary",
                        "disposition": "accepted",
                        "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                        "origin_revision": 1,
                    }
                ],
            },
            result,
            revision=1,
        )

        self.assertEqual([item["id"] for item in tasks["items"]], ["T-001", "T-002"])
        self.assertEqual(normalized["tasks"][1]["depends_on"], ["T-001"])

    def test_task_plan_rejects_empty_and_incomplete_requirement_coverage(self) -> None:
        empty_reference = {
            "schema_version": SCHEMA_VERSION,
            "stage": "specification",
            "memory_delta": [],
            "tasks": [
                {
                    "key": "drafts",
                    "title": "Persist drafts",
                    "requirements": [],
                    "depends_on": [],
                }
            ],
        }
        with self.assertRaises(AIWorkflowError):
            validate_result_manifest("specification", empty_reference, active_item=None)

        accepted = {
            "schema_version": SCHEMA_VERSION,
            "items": [
                {
                    "id": requirement_id,
                    "title": requirement_id,
                    "summary": "Summary",
                    "disposition": "accepted",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "origin_revision": 1,
                }
                for requirement_id in ("REQ-001", "REQ-002")
            ],
        }
        partial = {
            **empty_reference,
            "tasks": [
                {
                    "key": "drafts",
                    "title": "Persist drafts",
                    "requirements": ["REQ-001"],
                    "depends_on": [],
                }
            ],
        }
        with self.assertRaises(AIWorkflowError) as raised:
            reconcile_tasks(
                {"schema_version": SCHEMA_VERSION, "items": []},
                accepted,
                partial,
                revision=1,
            )

        self.assertEqual(raised.exception.code, "uncovered_requirements")
        self.assertEqual(raised.exception.details["ids"], ["REQ-002"])

    def test_task_plan_does_not_require_coverage_for_deferred_requirements(self) -> None:
        requirements = {
            "schema_version": SCHEMA_VERSION,
            "items": [
                {
                    "id": "REQ-001",
                    "title": "Now",
                    "summary": "Deliver now.",
                    "disposition": "accepted",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "origin_revision": 1,
                },
                {
                    "id": "REQ-002",
                    "title": "Later",
                    "summary": "Deliver later.",
                    "disposition": "deferred",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "origin_revision": 1,
                },
            ],
        }
        result = {
            "schema_version": SCHEMA_VERSION,
            "stage": "specification",
            "memory_delta": [],
            "tasks": [
                {
                    "key": "now",
                    "title": "Deliver now",
                    "requirements": ["REQ-001"],
                    "depends_on": [],
                }
            ],
        }

        tasks, _ = reconcile_tasks(
            {"schema_version": SCHEMA_VERSION, "items": []},
            requirements,
            result,
            revision=1,
        )

        self.assertEqual(tasks["items"][0]["requirements"], ["REQ-001"])

    def test_task_dependency_cycle_is_rejected(self) -> None:
        result = {
            "schema_version": SCHEMA_VERSION,
            "stage": "specification",
            "memory_delta": [],
            "tasks": [
                {
                    "key": "a",
                    "title": "A",
                    "requirements": ["REQ-001"],
                    "depends_on": ["b"],
                },
                {
                    "key": "b",
                    "title": "B",
                    "requirements": ["REQ-001"],
                    "depends_on": ["a"],
                },
            ],
        }

        with self.assertRaises(AIWorkflowError) as raised:
            reconcile_tasks(
                {"schema_version": SCHEMA_VERSION, "items": []},
                {
                    "schema_version": SCHEMA_VERSION,
                    "items": [
                        {
                            "id": "REQ-001",
                            "title": "Requirement",
                            "summary": "Summary",
                            "disposition": "accepted",
                            "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                            "origin_revision": 1,
                        }
                    ],
                },
                result,
                revision=1,
            )

        self.assertEqual(raised.exception.code, "task_dependency_cycle")

    def test_task_plan_revision_rejects_dependency_on_withdrawn_task(self) -> None:
        current = {
            "schema_version": SCHEMA_VERSION,
            "items": [
                {
                    "id": "T-001",
                    "title": "Base",
                    "requirements": ["REQ-001"],
                    "depends_on": [],
                    "status": "planned",
                    "origin_revision": 1,
                },
                {
                    "id": "T-002",
                    "title": "Dependent",
                    "requirements": ["REQ-001"],
                    "depends_on": ["T-001"],
                    "status": "planned",
                    "origin_revision": 1,
                },
            ],
        }
        requirements = {
            "schema_version": SCHEMA_VERSION,
            "items": [
                {
                    "id": "REQ-001",
                    "title": "Requirement",
                    "summary": "Summary",
                    "disposition": "accepted",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "origin_revision": 1,
                }
            ],
        }
        revision = {
            "schema_version": SCHEMA_VERSION,
            "stage": "specification",
            "memory_delta": [],
            "tasks": [
                {
                    "key": "dependent",
                    "id": "T-002",
                    "title": "Dependent",
                    "requirements": ["REQ-001"],
                    "depends_on": ["T-001"],
                }
            ],
        }

        with self.assertRaises(AIWorkflowError) as raised:
            reconcile_tasks(current, requirements, revision, revision=2)

        self.assertEqual(raised.exception.code, "unknown_task_dependency")

    def test_design_requires_exact_accepted_requirement_coverage(self) -> None:
        requirements = {
            "schema_version": SCHEMA_VERSION,
            "items": [
                {
                    "id": "REQ-001",
                    "title": "Now",
                    "summary": "Deliver now.",
                    "platform_scope": "target",
                    "change_type": "new",
                    "scope_reason": "Implemented by the web client.",
                    "disposition": "accepted",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "origin_revision": 1,
                },
                {
                    "id": "REQ-002",
                    "title": "Later",
                    "summary": "Deliver later.",
                    "platform_scope": "target",
                    "change_type": "new",
                    "scope_reason": "Deferred by product scope.",
                    "disposition": "deferred",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "origin_revision": 1,
                },
            ],
        }
        valid = {
            "schema_version": SCHEMA_VERSION,
            "stage": "design",
            "requirements": ["REQ-001"],
            "design_mode": "anchored",
            "greenfield_reason": None,
            "code_evidence": [
                {"path": "src/app.py", "symbol": "DraftStore", "purpose": "Draft persistence"}
            ],
            "memory_delta": [],
        }

        validate_result_manifest("design", valid, active_item=None)
        validate_design_coverage(requirements, valid)

        invalid = {**valid, "requirements": ["REQ-002"]}
        with self.assertRaises(AIWorkflowError) as raised:
            validate_design_coverage(requirements, invalid)

        self.assertEqual(raised.exception.code, "design_requirement_mismatch")
        self.assertEqual(raised.exception.details["unknown"], ["REQ-002"])
        self.assertEqual(raised.exception.details["missing"], ["REQ-001"])

    def test_analysis_rejects_other_platform_work_as_proposed(self) -> None:
        result = {
            "schema_version": SCHEMA_VERSION,
            "stage": "analysis",
            "target_platform": "web",
            "requirements": [
                {
                    "title": "Native-only capability",
                    "summary": "This capability belongs only to the native app.",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "platform_scope": "other",
                    "change_type": "new",
                    "scope_reason": "The PRD assigns it to the native app.",
                    "disposition": "proposed",
                }
            ],
            "memory_delta": [],
        }

        with self.assertRaises(AIWorkflowError) as raised:
            validate_result_manifest("analysis", result, active_item=None)

        self.assertIn("other-platform", raised.exception.message)


if __name__ == "__main__":
    unittest.main()
