from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import bootstrap_engine

from aiwf_core.model import AIWorkflowError, now_iso
from aiwf_core.storage import InjectedTransactionFailure, json_bytes, sha256_bytes


class TransactionTests(unittest.TestCase):
    def test_status_rejects_malformed_event_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))
            events_path = engine.store.data_root / "events.jsonl"
            with events_path.open("a", encoding="utf-8") as events:
                events.write(json.dumps({"event_id": "bad"}) + "\n")

            with self.assertRaises(AIWorkflowError) as raised:
                engine.inspect()

            self.assertEqual(raised.exception.code, "invalid_schema")

    def test_failure_before_event_rolls_back_all_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))
            store = engine.store
            original_state = store.read_json("state.json")
            original_requirements = store.read_json("requirements.json")
            changed_state = {**original_state, "updated_at": now_iso()}
            changed_requirements = {
                "schema_version": 9,
                "items": [
                    {
                        "id": "REQ-001",
                        "title": "Draft",
                        "summary": "Draft",
                        "platform_scope": "target",
                        "change_type": "new",
                        "scope_reason": "Implemented by the target platform.",
                        "disposition": "proposed",
                        "sources": [{"kind": "prd", "ref": "prd/input.md"}],
                        "origin_revision": 1,
                    }
                ],
            }
            with store.lock(exclusive=True):
                store.inject_failure_after(1)
                with self.assertRaises(InjectedTransactionFailure):
                    store.commit_locked(
                        {
                            ".aiwf/state.json": json_bytes(changed_state),
                            ".aiwf/requirements.json": json_bytes(changed_requirements),
                        },
                        event_type="test_change",
                        event_data={},
                        command_key="test:rollback",
                        request_digest=sha256_bytes(b"rollback"),
                    )

            self.assertEqual(engine.inspect()["status"], "needs_recovery")
            self.assertEqual(engine.recover()[0].split(":")[-1], "rollback")
            self.assertEqual(store.read_json("state.json"), original_state)
            self.assertEqual(store.read_json("requirements.json"), original_requirements)

    def test_failure_after_event_finishes_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))
            store = engine.store
            changed_state = {**store.read_json("state.json"), "updated_at": now_iso()}
            changed_requirements = {
                "schema_version": 9,
                "items": [
                    {
                        "id": "REQ-001",
                        "title": "Committed",
                        "summary": "Committed",
                        "platform_scope": "target",
                        "change_type": "new",
                        "scope_reason": "Implemented by the target platform.",
                        "disposition": "proposed",
                        "sources": [{"kind": "prd", "ref": "prd/input.md"}],
                        "origin_revision": 1,
                    }
                ],
            }
            with store.lock(exclusive=True):
                store.inject_failure_after(3)
                with self.assertRaises(InjectedTransactionFailure):
                    store.commit_locked(
                        {
                            ".aiwf/state.json": json_bytes(changed_state),
                            ".aiwf/requirements.json": json_bytes(changed_requirements),
                        },
                        event_type="test_change",
                        event_data={},
                        command_key="test:commit",
                        request_digest=sha256_bytes(b"commit"),
                    )

            self.assertEqual(engine.recover()[0].split(":")[-1], "commit")
            self.assertEqual(store.read_json("requirements.json")["items"][0]["title"], "Committed")
            self.assertIsNotNone(store.find_event("test:commit"))

    def test_command_key_rejects_different_request_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))
            store = engine.store
            with store.lock(exclusive=True):
                store.commit_locked(
                    {},
                    event_type="test_change",
                    event_data={},
                    command_key="test:idempotent",
                    request_digest=sha256_bytes(b"first"),
                )
                with self.assertRaises(AIWorkflowError) as raised:
                    store.commit_locked(
                        {},
                        event_type="test_change",
                        event_data={},
                        command_key="test:idempotent",
                        request_digest=sha256_bytes(b"second"),
                    )

            self.assertEqual(raised.exception.code, "idempotency_conflict")

    def test_recovery_removes_orphaned_work_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = bootstrap_engine(Path(directory))
            orphan = engine.store.data_root / "work" / "W-999999"
            orphan.mkdir()
            (orphan / "draft.md").write_text("orphan", encoding="utf-8")

            recovered = engine.recover()

            self.assertIn("work:W-999999:cleanup", recovered)
            self.assertFalse(orphan.exists())


if __name__ == "__main__":
    unittest.main()
