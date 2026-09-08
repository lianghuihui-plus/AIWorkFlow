from __future__ import annotations

import json
import unittest
from pathlib import Path


class WorkflowOutcomeRubricTests(unittest.TestCase):
    def test_rubric_separates_visible_inputs_and_quality_dimensions(self) -> None:
        path = Path(__file__).with_name("workflow_outcome_rubric.json")
        rubric = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(rubric["schema_version"], 1)
        self.assertEqual(
            {unit["id"] for unit in rubric["evaluation_units"]},
            {"initial_analysis", "feedback_convergence", "technical_design", "final_consistency"},
        )
        criteria = {
            criterion
            for unit in rubric["evaluation_units"]
            for criterion in unit["criteria"]
        }
        self.assertTrue(
            {
                "prd_fidelity",
                "platform_filtering",
                "repository_fact_accuracy",
                "latest_intent_priority",
                "architecture_quality",
                "code_evidence_accuracy",
                "upstream_correction_closure",
                "withdrawn_context_isolation",
            }.issubset(criteria)
        )
        self.assertIn("visible_input_basis", rubric["required_score_fields"])
        self.assertTrue(all(unit["visible_inputs"] for unit in rubric["evaluation_units"]))


if __name__ == "__main__":
    unittest.main()
