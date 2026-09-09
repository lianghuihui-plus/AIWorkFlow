from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from support import advance_to_tasks, approve_task_implementation, approve_task_spec, bootstrap_engine
from aiwf_core.dashboard import _render_artifact_impact, _render_events


class DashboardTests(unittest.TestCase):
    def test_dashboard_keeps_responsive_structure_and_overflow_guards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(
                engine,
                [
                    {
                        "key": "a",
                        "title": "LongUnbrokenTaskTitle" * 20,
                        "requirements": ["REQ-001"],
                        "depends_on": [],
                    }
                ],
            )
            engine.render()
            dashboard = (workspace / "dashboard.html").read_text(encoding="utf-8")

            self.assertEqual(dashboard.count('class="pipeline-step '), 5)
            self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', dashboard)
            self.assertIn("grid-template-columns: repeat(5, minmax(0, 1fr))", dashboard)
            self.assertIn("@media (max-width: 960px)", dashboard)
            self.assertIn("@media (max-width: 560px)", dashboard)
            self.assertIn(".metrics, .pipeline, .requirement-stats { grid-template-columns: 1fr; }", dashboard)
            self.assertIn(".main-column { min-width: 0; }", dashboard)
            self.assertIn("overflow-wrap: anywhere", dashboard)
            self.assertIn(".table-wrap { overflow-x: auto", dashboard)

    def test_reconciliation_marker_has_local_wording(self) -> None:
        markup = _render_artifact_impact(
            {"needs_reconcile": ["requirement:REQ-001"]}
        )
        self.assertIn("待核对", markup)
        self.assertIn("requirement:REQ-001", markup)
        self.assertNotIn("全局", markup)

    def test_dashboard_keeps_five_stage_layout_and_separate_test_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            advance_to_tasks(engine, [{"key": "a", "title": "Task A", "requirements": ["REQ-001"], "depends_on": []}])
            approve_task_spec(engine, "T-001")
            approve_task_implementation(engine, "T-001")
            engine.render()
            content = (workspace / "dashboard.html").read_text(encoding="utf-8")

            for label in ("需求分析", "技术设计", "任务规格", "代码实现", "单元测试"):
                self.assertIn(label, content)
            self.assertIn("未生成", content)
            self.assertIn("T-001 · Task A", content)
            self.assertIn("--radius: 8px", content)
            self.assertIn("需求实现状态", content)
            self.assertIn("单元测试状态", content)
            self.assertIn("未执行：1", content)
            self.assertIn("grid-template-columns: repeat(5", content)
            self.assertNotIn("当前决策路由", content)
            self.assertNotIn("待处理决策", content)
            self.assertNotIn('id="decision-route"', content)
            self.assertNotIn("项目记忆", content)
            self.assertNotIn('id="memory"', content)

    def test_dashboard_labels_current_workflow_events(self) -> None:
        markup = _render_events(
            [
                {"event_id": "E-000001", "type": "artifact_approved", "created_at": "2026-09-09T10:00:00+08:00", "data": {}},
                {"event_id": "E-000002", "type": "revision_requested", "created_at": "2026-09-09T10:01:00+08:00", "data": {}},
                {"event_id": "E-000003", "type": "artifact_reconciled", "created_at": "2026-09-09T10:02:00+08:00", "data": {}},
            ]
        )

        self.assertIn("批准阶段产物", markup)
        self.assertIn("请求修改产物", markup)
        self.assertIn("核对阶段产物", markup)
        self.assertNotIn("artifact_approved", markup)
        self.assertNotIn("revision_requested", markup)
        self.assertNotIn("artifact_reconciled", markup)

    def test_dashboard_escapes_manual_artifact_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            engine = bootstrap_engine(workspace)
            work = engine.prepare_work()
            (workspace / str(work["draft_output"])).write_text("# <script>alert(1)</script>", encoding="utf-8")
            engine.render()
            content = (workspace / "dashboard.html").read_text(encoding="utf-8")
            self.assertNotIn("<script>alert(1)</script>", content)


if __name__ == "__main__":
    unittest.main()
