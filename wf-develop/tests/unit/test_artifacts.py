from __future__ import annotations

import unittest

import support  # noqa: F401

from aiwf_core.artifacts import (
    reconcile_requirements,
    reconcile_tasks,
    requirement_semantic_value,
    result_seed_from_record,
    semantic_digest,
    validate_design_coverage,
    validate_result_manifest,
)
from aiwf_core.model import AIWorkflowError, SCHEMA_VERSION


class ArtifactResultTests(unittest.TestCase):
    def test_result_manifest_rejects_deprecated_top_level_fields(self) -> None:
        result = {
            "schema_version": SCHEMA_VERSION,
            "stage": "testing",
            "task_id": "T-001",
            "status": "completed",
            "test_files": [],
            "command": None,
            "summary": "Tests passed.",
            "uncovered": [],
            "memory_delta": [],
        }

        with self.assertRaises(AIWorkflowError) as raised:
            validate_result_manifest("testing", result, active_item="T-001")

        self.assertEqual(raised.exception.code, "invalid_schema")

    def test_existing_requirement_revision_requires_change_kind(self) -> None:
        current = {
            "schema_version": SCHEMA_VERSION,
            "items": [
                {
                    "id": "REQ-001",
                    "title": "Save drafts",
                    "summary": "Persist drafts.",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "platform_scope": "target",
                    "change_type": "new",
                    "scope_reason": "Target behavior.",
                    "disposition": "accepted",
                    "origin_revision": 1,
                    "revision": 1,
                    "approved_revision": 1,
                    "semantic_sha256": "1" * 64,
                    "content_sha256": "2" * 64,
                }
            ],
        }
        result = {
            "schema_version": SCHEMA_VERSION,
            "stage": "analysis",
            "target_platform": "test",
            "requirements": [
                {
                    "id": "REQ-001",
                    "title": "Save drafts",
                    "summary": "Persist drafts for 30 days.",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "platform_scope": "target",
                    "change_type": "modify",
                    "scope_reason": "Retention changed.",
                    "disposition": "proposed",
                }
            ],
            "withdrawn_requirements": [],
        }

        with self.assertRaises(AIWorkflowError) as raised:
            reconcile_requirements(current, result, revision=2)

        self.assertEqual(raised.exception.code, "invalid_schema")

    def test_presentation_change_preserves_requirement_semantics(self) -> None:
        current = {
            "schema_version": SCHEMA_VERSION,
            "items": [
                {
                    "id": "REQ-001",
                    "title": "Save drafts",
                    "summary": "Persist drafts.",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "platform_scope": "target",
                    "change_type": "new",
                    "scope_reason": "Target behavior.",
                    "disposition": "accepted",
                    "origin_revision": 1,
                    "revision": 1,
                    "approved_revision": 1,
                    "semantic_sha256": "1" * 64,
                    "content_sha256": "2" * 64,
                }
            ],
        }
        result = {
            "schema_version": SCHEMA_VERSION,
            "stage": "analysis",
            "target_platform": "test",
            "requirements": [
                {
                    "id": "REQ-001",
                    "change_kind": "presentation",
                    "title": "Draft saving",
                    "summary": "Persist drafts.",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "platform_scope": "target",
                    "change_type": "new",
                    "scope_reason": "Wording clarified.",
                    "disposition": "proposed",
                }
            ],
            "withdrawn_requirements": [],
        }

        updated, normalized = reconcile_requirements(current, result, revision=2)

        self.assertEqual(updated["items"][0]["semantic_sha256"], "1" * 64)
        self.assertEqual(normalized["requirements"][0]["change_kind"], "presentation")

    def test_behavior_change_advances_requirement_semantics(self) -> None:
        requirement = {
            "id": "REQ-001",
            "title": "Save drafts",
            "summary": "Persist drafts.",
            "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
            "platform_scope": "target",
            "change_type": "new",
            "scope_reason": "Target behavior.",
            "disposition": "accepted",
            "origin_revision": 1,
            "revision": 1,
            "approved_revision": 1,
            "content_sha256": "2" * 64,
        }
        requirement["semantic_sha256"] = semantic_digest(
            requirement_semantic_value(requirement)
        )
        current = {
            "schema_version": SCHEMA_VERSION,
            "items": [requirement],
        }
        result = {
            "schema_version": SCHEMA_VERSION,
            "stage": "analysis",
            "target_platform": "test",
            "requirements": [
                {
                    "id": "REQ-001",
                    "change_kind": "behavior",
                    "title": "Draft saving",
                    "summary": "Persist drafts for 30 days.",
                    "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                    "platform_scope": "target",
                    "change_type": "modify",
                    "scope_reason": "Retention changed.",
                    "disposition": "proposed",
                }
            ],
            "withdrawn_requirements": [],
        }

        updated, _ = reconcile_requirements(current, result, revision=2)

        self.assertNotEqual(
            updated["items"][0]["semantic_sha256"],
            requirement["semantic_sha256"],
        )

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
        }

        with self.assertRaises(AIWorkflowError) as raised:
            validate_result_manifest("analysis", result, active_item=None)

        self.assertEqual(raised.exception.code, "invalid_schema")

    def test_implementation_requires_all_acceptance_criteria_to_pass(self) -> None:
        result = {
            "schema_version": SCHEMA_VERSION,
            "stage": "implementation",
            "task_id": "T-001",
            "summary": "Draft restore remains incomplete.",
            "changed_files": [],
            "acceptance_results": [
                {
                    "criterion": "Draft can be restored",
                    "status": "failed",
                    "evidence": "Restore path is missing.",
                }
            ],
            "validation": ["Checked the restore path."],
            "risks": ["Restore remains unavailable."],
        }

        with self.assertRaises(AIWorkflowError) as raised:
            validate_result_manifest("implementation", result, active_item="T-001")

        self.assertEqual(raised.exception.code, "acceptance_not_met")

    def test_testing_result_requires_an_explicit_status(self) -> None:
        with self.assertRaises(AIWorkflowError) as raised:
            validate_result_manifest(
                "testing",
                {
                    "schema_version": SCHEMA_VERSION,
                    "stage": "testing",
                    "task_id": "T-001",
                    "test_files": [],
                    "command": None,
                    "summary": "No environment.",
                    "uncovered": [],
                },
                active_item="T-001",
            )

        self.assertEqual(raised.exception.code, "invalid_schema")

    def test_result_seed_removes_engine_fields(self) -> None:
        record = {
            "schema_version": SCHEMA_VERSION,
            "stage": "analysis",
            "target_platform": "web",
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

        seed = result_seed_from_record("analysis", record)

        self.assertNotIn("artifact_id", seed)
        self.assertEqual(seed["requirements"], [])
        self.assertEqual(seed["withdrawn_requirements"], [])

    def test_analysis_revision_seed_is_an_explicit_empty_patch(self) -> None:
        record = {
            "schema_version": SCHEMA_VERSION,
            "stage": "analysis",
            "target_platform": "web",
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

        seed = result_seed_from_record("analysis", record)

        self.assertEqual(seed["requirements"], [])
        self.assertEqual(seed["withdrawn_requirements"], [])
        validate_result_manifest("analysis", seed, active_item=None)

    def test_result_manifest_rejects_nested_engine_fields(self) -> None:
        result = {
            "schema_version": SCHEMA_VERSION,
            "stage": "analysis",
            "target_platform": "web",
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

    def test_analysis_rejects_an_empty_effective_requirement_set(self) -> None:
        result = validate_result_manifest(
            "analysis",
            {
                "schema_version": SCHEMA_VERSION,
                "stage": "analysis",
                "target_platform": "web",
                "requirements": [],
                "withdrawn_requirements": [],
            },
            active_item=None,
        )

        with self.assertRaises(AIWorkflowError) as raised:
            reconcile_requirements(
                {"schema_version": SCHEMA_VERSION, "items": []},
                result,
                revision=1,
            )

        self.assertEqual(raised.exception.code, "empty_requirements")

    def test_analysis_result_rejects_an_unsupported_disposition(self) -> None:
        result = {
            "schema_version": SCHEMA_VERSION,
            "stage": "analysis",
            "target_platform": "web",
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
                    "status": "active",
                    "origin_revision": 1,
                },
                {
                    "id": "T-002",
                    "title": "Dependent",
                    "requirements": ["REQ-001"],
                    "depends_on": ["T-001"],
                    "status": "active",
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
            "tasks": [
                {
                    "key": "dependent",
                    "id": "T-002",
                    "title": "Dependent",
                    "requirements": ["REQ-001"],
                    "depends_on": ["T-001"],
                }
            ],
            "withdrawn_tasks": ["T-001"],
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
        }

        with self.assertRaises(AIWorkflowError) as raised:
            validate_result_manifest("analysis", result, active_item=None)

        self.assertIn("other-platform", raised.exception.message)


if __name__ == "__main__":
    unittest.main()
