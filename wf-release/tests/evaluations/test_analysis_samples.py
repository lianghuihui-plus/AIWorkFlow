from __future__ import annotations

import json
import hashlib
import re
import unittest
from pathlib import Path

from support import DEVELOP_ROOT


class AnalysisEvaluationManifestTests(unittest.TestCase):
    def test_manifest_is_ready_for_deferred_blind_forward_evaluation(self) -> None:
        registry_path = Path(__file__).with_name("analysis_samples.json")
        registry = json.loads(registry_path.read_text(encoding="utf-8"))

        self.assertEqual(registry["schema_version"], 1)
        self.assertEqual(registry["status"], "pending_user_acceptance")
        protocol = registry["protocol"]
        self.assertEqual(protocol["baseline_skill"], "wf-release")
        self.assertEqual(protocol["candidate_skill"], "wf-develop")
        self.assertTrue(protocol["blind_variants"])
        self.assertEqual(protocol["score_scale"], {"minimum": 1, "maximum": 5})
        self.assertEqual(
            set(protocol["required_score_fields"]),
            {"sample_id", "variant", "criterion", "score", "evidence"},
        )
        self.assertGreaterEqual(len(registry["samples"]), 2)
        self.assertGreaterEqual(len(registry["rubric"]), 8)
        self.assertEqual(len({sample["id"] for sample in registry["samples"]}), 2)
        for sample in registry["samples"]:
            self.assertRegex(sample["sha256"], re.compile(r"^[0-9a-f]{64}$"))
            self.assertTrue(sample["source"].endswith(".md"))
            self.assertTrue(sample["selection_reason"].strip())
            source = (DEVELOP_ROOT / sample["source"]).resolve()
            self.assertTrue(source.is_file(), source)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), sample["sha256"])


if __name__ == "__main__":
    unittest.main()
