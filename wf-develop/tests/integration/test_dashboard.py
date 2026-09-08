from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import run_cli

from aiwf_core.dashboard import _render_markdown, _render_tasks
from integration.test_analysis_cli import initialize_workspace


def structured_snapshot(workspace: Path) -> dict[str, bytes]:
    return {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in sorted((workspace / ".aiwf").rglob("*"))
        if path.is_file()
    }


class DashboardTests(unittest.TestCase):
    def test_dashboard_shows_current_decision_route_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            prepared = run_cli(["prepare", "--workspace", str(workspace)])
            work = json.loads(prepared.stdout)["result"]
            (workspace / work["draft_output"]).write_text("# Partial analysis\n", encoding="utf-8")
            opened = run_cli(
                [
                    "question",
                    "--workspace",
                    str(workspace),
                    "--work-id",
                    work["work_id"],
                    "--items-json",
                    json.dumps(
                        [
                            {
                                "question": "Should drafts sync?",
                                "reason": "The PRD is ambiguous.",
                                "recommendation": "Keep drafts local.",
                                "impact": ["analysis", "design"],
                            }
                        ]
                    ),
                ]
            )
            question_id = json.loads(opened.stdout)["result"]["question_ids"][0]
            blocked_content = (workspace / "dashboard.html").read_text(encoding="utf-8")
            self.assertIn('<span class="pill danger">待决策</span>', blocked_content)
            self.assertIn('href="#issues">去处理</a>', blocked_content)
            decided = run_cli(
                [
                    "decide",
                    "--workspace",
                    str(workspace),
                    "--question-id",
                    question_id,
                    "--decision",
                    "Synchronize drafts across devices.",
                ]
            )
            self.assertEqual(decided.returncode, 0, decided.stderr)

            content = (workspace / "dashboard.html").read_text(encoding="utf-8")
            self.assertIn("当前决策路由", content)
            self.assertIn("Should drafts sync?", content)
            self.assertIn("Synchronize drafts across devices.", content)
            self.assertIn("预估影响：analysis, design", content)
            self.assertIn('<span class="pill warn">待路由</span>', content)
            self.assertIn('href="#decision-route">去处理</a>', content)

    def test_dashboard_shows_precise_drift_recovery_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            prepared = run_cli(["prepare", "--workspace", str(workspace)])
            work = json.loads(prepared.stdout)["result"]
            (workspace / work["draft_output"]).write_text("# Analysis\n", encoding="utf-8")
            (workspace / work["result_output"]).write_text(
                json.dumps(
                    {
                        "schema_version": 9,
                        "stage": "analysis",
                        "target_platform": "web",
                        "requirements": [
                            {
                                "title": "Draft",
                                "summary": "Save a draft.",
                                "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                                "platform_scope": "target",
                                "change_type": "new",
                                "scope_reason": "Implemented by the web client.",
                                "disposition": "proposed",
                            }
                        ],
                        "memory_delta": [],
                    }
                ),
                encoding="utf-8",
            )
            submitted = run_cli(
                ["submit", "--workspace", str(workspace), "--work-id", work["work_id"]]
            )
            self.assertEqual(submitted.returncode, 0, submitted.stderr)
            (workspace / "artifacts/analysis.md").write_text("# External edit\n", encoding="utf-8")
            rendered = run_cli(["render", "--workspace", str(workspace)])
            self.assertEqual(rendered.returncode, 0, rendered.stderr)

            content = (workspace / "dashboard.html").read_text(encoding="utf-8")
            self.assertIn("恢复动作：resolve_review_drift", content)
            self.assertIn("允许结果：adopt, discard", content)
            self.assertIn('href="#artifact-analysis">去处理</a>', content)

    def test_dashboard_is_generated_and_escapes_artifact_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            dashboard = workspace / "dashboard.html"
            self.assertTrue(dashboard.is_file())

            prepared = run_cli(["prepare", "--workspace", str(workspace)])
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            work = json.loads(prepared.stdout)["result"]
            (workspace / work["draft_output"]).write_text(
                "# Analysis\n\n## 分析结论\n- Target **web**.\n\n## Details\n\n"
                "| Item | Status |\n| --- | --- |\n| <script> | safe |\n\n"
                "```mermaid\nflowchart LR\n  A --> B\n```\n",
                encoding="utf-8",
            )
            (workspace / work["result_output"]).write_text(
                json.dumps(
                    {
                        "schema_version": 9,
                        "stage": "analysis",
                        "target_platform": "web",
                        "requirements": [
                            {
                                "title": "Draft",
                                "summary": "Save a draft.",
                                "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                                "platform_scope": "target",
                                "change_type": "new",
                                "scope_reason": "Implemented by the web client.",
                                "disposition": "proposed",
                            }
                        ],
                        "memory_delta": [],
                    }
                ),
                encoding="utf-8",
            )
            submitted = run_cli(
                ["submit", "--workspace", str(workspace), "--work-id", work["work_id"]]
            )
            self.assertEqual(submitted.returncode, 0, submitted.stderr)

            content = dashboard.read_text(encoding="utf-8")
            self.assertIn('aria-label="页面大纲"', content)
            self.assertIn('class="hero"', content)
            self.assertNotIn('class="stage-line"', content)
            self.assertEqual(content.count('class="metric"'), 3)
            self.assertRegex(
                content,
                r"生成时间<br><strong>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}</strong>",
            )
            self.assertIn('class="pipeline-step review"', content)
            self.assertIn('id="context"', content)
            self.assertIn("项目上下文", content)
            self.assertIn(
                '<div class="summary-block">Target <strong>web</strong>.</div>', content
            )
            self.assertIn("人工待办", content)
            self.assertIn("需求纳入决策", content)
            self.assertIn("任务进展", content)
            self.assertIn("阶段产物状态", content)
            self.assertIn("产物内容", content)
            self.assertIn('<span class="pill warn">待审核</span>', content)
            self.assertIn('href="#artifact-analysis">去审核</a>', content)
            self.assertIn('<span class="path">artifacts/analysis.md</span>', content)
            self.assertNotIn("analysis@1 · artifacts/analysis.md", content)
            self.assertIn('class="table-wrap"', content)
            self.assertIn("&lt;script&gt;", content)
            self.assertNotIn("<td><script>", content)
            self.assertIn('class="diagram-card"', content)
            self.assertIn('class="mermaid"', content)
            self.assertIn('id="diagramViewer"', content)
            self.assertIn("mermaid@11", content)

    def test_wide_test_table_renders_as_record_cards(self) -> None:
        markup = _render_markdown(
            "| # | 行为 | 测试点 | 测试文件 | 状态 | 说明 |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 保存草稿 | 验证本地持久化 | tests/test_draft.py | 通过 | 已覆盖 |\n"
        )

        self.assertIn('class="test-record-list"', markup)
        self.assertIn('class="test-record-card"', markup)
        self.assertIn('class="test-record-status ok">通过</span>', markup)
        self.assertIn("保存草稿", markup)
        self.assertNotIn('class="table-wrap"', markup)

    def test_task_checkpoints_link_to_generated_artifacts(self) -> None:
        markup = _render_tasks(
            [
                {
                    "id": "T-001",
                    "title": "Persist drafts",
                    "requirements": ["REQ-001"],
                    "depends_on": [],
                    "status": "planned",
                    "origin_revision": 1,
                }
            ],
            [
                {
                    "id": "T-001-spec",
                    "stage": "specification",
                    "active_item": "T-001",
                    "status": "approved",
                },
                {
                    "id": "T-001-implementation",
                    "stage": "implementation",
                    "active_item": "T-001",
                    "status": "review",
                },
            ],
        )

        self.assertIn('href="#artifact-t-001-spec"', markup)
        self.assertIn('href="#artifact-t-001-implementation"', markup)
        self.assertIn('<span class="task-requirements">需求：REQ-001</span>', markup)
        self.assertIn('<span class="pill warn">已规划</span>', markup)
        self.assertIn("已批准", markup)
        self.assertIn("待审核", markup)
        self.assertIn("测试</span><strong>未生成", markup)

    def test_task_status_pill_keeps_content_width(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            content = (workspace / "dashboard.html").read_text(encoding="utf-8")

        self.assertNotIn(".task-title span {", content)
        self.assertIn(
            ".task-title > .pill { width: fit-content; max-width: 100%; margin-top: 6px; }",
            content,
        )

    def test_explicit_render_does_not_change_structured_workspace_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            before = structured_snapshot(workspace)

            rendered = run_cli(["render", "--workspace", str(workspace)])

            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            result = json.loads(rendered.stdout)["result"]
            self.assertEqual(result["status"], "rendered")
            self.assertGreater(result["bytes"], 1000)
            self.assertEqual(structured_snapshot(workspace), before)

    def test_render_failure_does_not_roll_back_successful_submission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = initialize_workspace(Path(directory))
            prepared = run_cli(["prepare", "--workspace", str(workspace)])
            work = json.loads(prepared.stdout)["result"]
            (workspace / work["draft_output"]).write_text("# Analysis\n", encoding="utf-8")
            (workspace / work["result_output"]).write_text(
                json.dumps(
                    {
                        "schema_version": 9,
                        "stage": "analysis",
                        "target_platform": "web",
                        "requirements": [
                            {
                                "title": "Draft",
                                "summary": "Save a draft.",
                                "sources": [{"kind": "prd", "ref": "prd/requirements.md"}],
                                "platform_scope": "target",
                                "change_type": "new",
                                "scope_reason": "Implemented by the web client.",
                                "disposition": "proposed",
                            }
                        ],
                        "memory_delta": [],
                    }
                ),
                encoding="utf-8",
            )
            dashboard = workspace / "dashboard.html"
            dashboard.unlink()
            dashboard.mkdir()

            submitted = run_cli(
                ["submit", "--workspace", str(workspace), "--work-id", work["work_id"]]
            )

            self.assertEqual(submitted.returncode, 0, submitted.stderr)
            result = json.loads(submitted.stdout)["result"]
            self.assertEqual(result["artifact_id"], "analysis")
            self.assertEqual(result["warnings"][0]["type"], "dashboard_render_failed")
            state = json.loads((workspace / ".aiwf/state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["mode"], "review")
            self.assertTrue((workspace / "artifacts/analysis.md").is_file())


if __name__ == "__main__":
    unittest.main()
