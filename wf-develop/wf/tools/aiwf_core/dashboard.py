"""Generated human-readable views owned by the deterministic core."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from typing import Any

DASHBOARD_FILENAME = "dashboard.html"
STAGE_LABELS = {
    "analysis": "需求分析",
    "design": "技术设计",
    "specification": "任务规格",
    "implementation": "代码实现",
    "testing": "单元测试",
    "completed": "完成",
}
STAGES = tuple(STAGE_LABELS)
MODE_LABELS = {
    "ready": "可开始",
    "working": "进行中",
    "review": "待审核",
    "blocked": "已阻塞",
}
STATUS_LABELS = {
    "review": "待审核",
    "approved": "已批准",
    "changes_requested": "需修改",
    "needs_reconcile": "待核对",
    "proposed": "待确认",
    "accepted": "已纳入",
    "deferred": "暂缓",
    "excluded": "不纳入",
    "withdrawn": "已撤回",
    "in_progress": "进行中",
    "active": "有效",
    "not_started": "未开始",
    "ready": "可开始",
    "in_progress": "进行中",
    "dependency_blocked": "依赖阻塞",
    "completed": "已完成",
    "optional": "可选",
    "not_run": "未执行",
    "failed": "失败",
    "skipped": "已跳过",
    "specification": "规格中",
    "implementation": "实现中",
    "testing": "测试中",
    "open": "待决策",
    "resolved": "已解决",
    "cancelled": "已取消",
}
ACTION_LABELS = {
    "review": "审核产物",
    "decide": "处理人工决策",
    "resume": "继续当前工作",
    "plan_tasks": "拆解任务计划",
    "generate_specification": "生成任务规格",
    "analyze_requirements": "分析需求",
    "design_solution": "设计技术方案",
    "implement_code": "实现代码",
    "write_unit_tests": "编写单元测试",
    "completed": "流程已完成",
}
PLATFORM_SCOPE_LABELS = {
    "target": "目标端",
    "cross_platform": "跨端",
    "other": "其他端",
}
CHANGE_TYPE_LABELS = {"new": "新增", "modify": "修改", "reuse": "复用"}
SOURCE_KIND_LABELS = {
    "prd": "PRD",
    "user_feedback": "用户反馈",
    "user_decision": "用户决策",
    "repository": "代码仓库",
    "agent_inference": "Agent 推断",
}


def render_dashboard(
    *,
    project: dict[str, Any],
    state: dict[str, Any],
    requirements: dict[str, Any],
    tasks: dict[str, Any],
    artifacts: dict[str, Any],
    questions: dict[str, Any],
    decisions: dict[str, Any],
    events: list[dict[str, Any]],
    artifact_bodies: dict[str, str],
    next_action: str,
    current_stage: str,
    stage_progress: dict[str, str],
    task_progress: list[dict[str, Any]],
    implementation_complete: bool,
) -> str:
    open_questions = [item for item in questions["items"] if item["status"] == "open"]
    pending_reviews = set(state["pending_reviews"])

    pipeline_markup = _render_pipeline(
        state,
        current_stage=current_stage,
        stage_progress=stage_progress,
    )
    context_markup = _render_context(
        project,
        requirements,
        artifact_bodies.get("analysis", ""),
    )
    todo_markup = _render_todos(
        open_questions=open_questions,
        pending_reviews=pending_reviews,
        artifacts=artifacts["items"],
        next_action=next_action,
    )
    question_markup = _render_questions(open_questions)
    requirement_markup = _render_requirements(requirements["items"])
    task_markup = _render_tasks(
        tasks["items"],
        artifacts["items"],
        task_progress=task_progress,
        implementation_complete=implementation_complete,
    )
    artifact_markup = _render_artifacts(artifacts["items"])
    preview_markup = _render_artifact_previews(artifacts["items"], artifact_bodies)
    decision_markup = _render_decisions(decisions["items"])
    event_markup = _render_events(events)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(project['name'])}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5fbff;
      --surface: #ffffff;
      --surface-soft: #f8fcff;
      --surface-tint: #eef8ff;
      --card-bg: #f8fcff;
      --card-inner-bg: #ffffff;
      --text: #111827;
      --muted: #5f6f86;
      --line: #d8e8f6;
      --accent: #0ea5e9;
      --accent-strong: #0284c7;
      --accent-border: #7dd3fc;
      --accent-soft: #e0f2fe;
      --ok: #059669;
      --ok-border: #86efac;
      --ok-soft: #ecfdf5;
      --warn: #d6a100;
      --warn-border: #fde047;
      --warn-soft: #fefce8;
      --danger: #dc2626;
      --danger-border: #fca5a5;
      --danger-soft: #fef2f2;
      --radius: 8px;
      --radius-sm: 6px;
      --shadow: 0 18px 48px rgba(14, 116, 144, 0.10);
      --shadow-soft: 0 8px 22px rgba(2, 132, 199, 0.06);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{ margin: 0; background: linear-gradient(180deg, rgba(224, 242, 254, 0.76), rgba(245, 251, 255, 0) 360px), var(--bg); color: var(--text); font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; letter-spacing: 0; }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    h1, h2, h3, p {{ margin-top: 0; }}
    h1 {{ margin-bottom: 8px; font-size: 26px; letter-spacing: 0; }}
    h2 {{ margin-bottom: 14px; font-size: 18px; letter-spacing: 0; }}
    h3 {{ margin-bottom: 6px; font-size: 15px; letter-spacing: 0; }}
    .shell {{ max-width: 1520px; margin: 0 auto; padding: 24px; }}
    .page-layout {{ display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 18px; align-items: start; }}
    .page-layout.outline-collapsed {{ grid-template-columns: 52px minmax(0, 1fr); }}
    .main-column {{ min-width: 0; }}
    .outline {{ position: sticky; top: 18px; max-height: calc(100vh - 36px); overflow: auto; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); padding: 10px; box-shadow: var(--shadow-soft); }}
    .outline-head {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }}
    .outline-title {{ color: var(--muted); font-size: 13px; font-weight: 600; white-space: nowrap; }}
    .outline-toggle {{ width: 30px; height: 30px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface-soft); color: var(--text); cursor: pointer; font-size: 16px; }}
    .outline nav {{ display: grid; gap: 4px; }}
    .outline a {{ display: block; overflow: hidden; border-radius: 7px; padding: 8px 9px; color: var(--text); text-decoration: none; text-overflow: ellipsis; white-space: nowrap; }}
    .outline a:hover {{ background: var(--surface-soft); }}
    .outline-collapsed .outline-title, .outline-collapsed .outline a {{ display: none; }}
    .outline-collapsed .outline-head {{ justify-content: center; margin-bottom: 0; }}
    .hero {{ border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); padding: 24px; box-shadow: var(--shadow); }}
    .hero-top {{ display: grid; grid-template-columns: minmax(260px, 1fr) auto; gap: 20px; align-items: start; }}
    .hero-meta {{ color: var(--muted); text-align: right; }}
    .pipeline {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 18px; margin-top: 18px; }}
    .pipeline-step {{ position: relative; display: flex; align-items: flex-start; gap: 8px; min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface-soft); padding: 10px; }}
    .pipeline-step::after {{ content: ""; position: absolute; top: 50%; right: -15px; width: 12px; height: 12px; border-top: 2px solid var(--line); border-right: 2px solid var(--line); transform: translateY(-50%) rotate(45deg); }}
    .pipeline-step:last-child::after {{ display: none; }}
    .pipeline-dot {{ display: inline-flex; align-items: center; justify-content: center; flex: 0 0 24px; width: 24px; height: 24px; border: 1px solid var(--line); border-radius: 999px; background: var(--surface); color: var(--muted); font-size: 12px; font-weight: 700; }}
    .pipeline-step strong {{ display: block; margin-bottom: 2px; font-size: 13px; white-space: nowrap; }}
    .pipeline-step span {{ display: block; overflow-wrap: anywhere; color: var(--muted); font-size: 12px; line-height: 1.35; }}
    .pipeline-step.done, .pipeline-step.current, .pipeline-step.review {{ border-color: var(--accent-border); background: var(--accent-soft); }}
    .pipeline-step.done::after, .pipeline-step.current::after, .pipeline-step.review::after {{ border-color: var(--accent); }}
    .pipeline-step.done .pipeline-dot, .pipeline-step.current .pipeline-dot, .pipeline-step.review .pipeline-dot {{ border-color: var(--accent); background: var(--accent); color: #ffffff; }}
    .pipeline-step.blocked {{ border-color: var(--accent-border); background: var(--accent-soft); }}
    .pipeline-step.blocked::after {{ border-color: var(--accent); }}
    .pipeline-step.blocked .pipeline-dot {{ border-color: var(--accent); background: var(--accent); color: #ffffff; }}
    .metrics {{ display: grid; grid-template-columns: repeat(2, minmax(150px, 1fr)); gap: 10px; margin-top: 20px; }}
    .metric {{ display: block; min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-bg); padding: 12px; color: inherit; text-decoration: none; }}
    a.metric:hover {{ border-color: var(--accent-border); box-shadow: 0 6px 18px rgba(29, 78, 216, 0.10); text-decoration: none; }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 4px; overflow-wrap: anywhere; font-size: 22px; }}
    .content-flow {{ min-width: 0; margin-top: 18px; }}
    .panel {{ min-width: 0; margin-bottom: 18px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); padding: 20px; scroll-margin-top: 18px; box-shadow: var(--shadow-soft); }}
    .section-heading {{ display: flex; align-items: start; justify-content: space-between; gap: 18px; margin-bottom: 18px; border-bottom: 1px solid var(--line); padding-bottom: 14px; }}
    .section-heading h2 {{ margin-bottom: 0; }}
    .section-heading p {{ max-width: 560px; margin-bottom: 0; color: var(--muted); font-size: 14px; text-align: right; }}
    .context-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .context-card {{ min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-bg); padding: 16px; }}
    .summary-block {{ max-height: 360px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 15px; line-height: 1.65; }}
    .eyebrow {{ display: block; margin-bottom: 4px; color: var(--muted); font-size: 12px; }}
    .meta-card {{ display: grid; align-content: start; gap: 8px; }}
    .tag-list {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .tag, .path {{ display: inline-flex; max-width: 100%; border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--card-inner-bg); padding: 2px 7px; overflow-wrap: anywhere; font-size: 12px; }}
    .path {{ background: var(--surface-tint); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    .meta-row, .req-stat-row {{ display: flex; align-items: center; gap: 10px; min-width: 0; border-bottom: 1px solid var(--line); padding-bottom: 8px; }}
    .meta-row > span:first-child, .req-stat-row > span:first-child {{ flex: 0 0 72px; width: 72px; color: var(--muted); font-size: 13px; white-space: nowrap; }}
    .meta-row strong {{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }}
    .meta-value {{ min-width: 0; flex: 1; overflow-wrap: anywhere; }}
    .repo-row {{ align-items: flex-start; }}
    .req-stat-row {{ flex-wrap: wrap; gap: 8px; }}
    .req-stat-row strong {{ border: 1px solid var(--line); border-radius: 999px; background: var(--card-inner-bg); padding: 1px 8px; font-size: 12px; }}
    .prd-list {{ display: grid; gap: 6px; }}
    .todo-list, .record-list, .task-board, .artifact-preview-list {{ display: grid; gap: 12px; }}
    .todo-card {{ display: grid; grid-template-columns: 128px minmax(0, 1fr) auto; overflow: hidden; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-bg); box-shadow: 0 8px 22px rgba(2, 132, 199, 0.04); }}
    .todo-type {{ display: grid; align-content: center; gap: 5px; border-right: 1px solid var(--line); background: var(--surface-tint); padding: 12px; }}
    .todo-type strong {{ color: var(--text); font-size: 13px; line-height: 1.25; }}
    .todo-type span {{ color: var(--muted); font-size: 12px; line-height: 1.35; overflow-wrap: anywhere; }}
    .todo-main {{ display: flex; align-items: center; min-width: 0; padding: 12px 14px; }}
    .todo-main h3 {{ margin: 0; overflow-wrap: anywhere; font-size: 14px; line-height: 1.4; }}
    .todo-main p {{ margin: 5px 0 0; overflow-wrap: anywhere; color: var(--muted); }}
    .todo-detail {{ display: grid; gap: 4px; margin-top: 8px; }}
    .todo-detail span {{ overflow-wrap: anywhere; color: var(--muted); font-size: 12px; }}
    .todo-action {{ display: flex; align-items: center; justify-content: flex-end; flex: 0 0 auto; border-left: 1px solid var(--line); padding: 12px; }}
    .todo-action .todo-action-link {{ display: inline-flex; align-items: center; justify-content: center; width: 88px; height: 36px; border-color: var(--accent); background: var(--accent); color: #ffffff; padding: 0; font-size: 13px; line-height: 1; }}
    .todo-action .todo-action-link:hover {{ border-color: var(--accent-strong); background: var(--accent-strong); text-decoration: none; }}
    .requirement-stats {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }}
    .requirement-stats div {{ border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-bg); padding: 12px; }}
    .requirement-stats div.ok {{ border-color: var(--ok-border); }}
    .requirement-stats div.warn {{ border-color: var(--warn-border); }}
    .requirement-stats span {{ display: block; color: var(--muted); font-size: 12px; }}
    .requirement-stats strong {{ display: block; margin-top: 2px; font-size: 22px; }}
    .requirement-board {{ display: grid; gap: 14px; }}
    .requirement-group {{ overflow: hidden; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-bg); }}
    .requirement-group.ok {{ border-color: var(--ok-border); }}
    .requirement-group.warn {{ border-color: var(--warn-border); }}
    .requirement-group-head {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--line); background: var(--surface-tint); padding: 12px 14px; }}
    .requirement-group-head h3 {{ margin: 0; }}
    .requirement-card {{ display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 12px; border-bottom: 1px solid var(--line); background: var(--card-inner-bg); padding: 14px; }}
    .requirement-card:last-child {{ border-bottom: 0; }}
    .requirement-id {{ color: var(--accent); font-weight: 700; white-space: nowrap; }}
    .requirement-card.ok .requirement-id {{ color: var(--ok); }}
    .requirement-card.warn .requirement-id {{ color: var(--warn); }}
    .requirement-card.muted .requirement-id {{ color: var(--muted); }}
    .requirement-main {{ display: grid; gap: 6px; min-width: 0; }}
    .requirement-main h3 {{ margin: 0; overflow-wrap: anywhere; }}
    .requirement-main p {{ margin: 6px 0; overflow-wrap: anywhere; color: var(--muted); }}
    .requirement-meta {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .requirement-meta span {{ border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--card-bg); color: var(--muted); padding: 2px 7px; font-size: 12px; overflow-wrap: anywhere; }}
    .task-card {{ display: grid; grid-template-columns: minmax(220px, 1fr) minmax(360px, 1.5fr); gap: 12px; align-items: center; min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-bg); padding: 14px; }}
    .task-title > strong, .task-title > .task-requirements {{ display: block; overflow-wrap: anywhere; }}
    .task-title > strong {{ color: var(--accent); }}
    .task-title > .task-requirements {{ margin-top: 4px; color: var(--muted); font-size: 12px; }}
    .task-title > .pill {{ width: fit-content; max-width: 100%; margin-top: 6px; }}
    .task-status-summary {{ display: grid; grid-template-columns: minmax(180px, 0.7fr) minmax(0, 1.3fr); gap: 10px; margin-bottom: 12px; }}
    .task-status-group {{ min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-bg); padding: 12px; }}
    .task-status-group > span {{ display: block; margin-bottom: 7px; color: var(--muted); font-size: 12px; }}
    .test-status-counts {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .task-checkpoints {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }}
    .task-checkpoint {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-inner-bg); padding: 8px; color: inherit; text-decoration: none; }}
    a.task-checkpoint:hover {{ border-color: var(--accent-border); text-decoration: none; }}
    .task-checkpoint.ok {{ border-color: var(--ok-border); }}
    .task-checkpoint.warn {{ border-color: var(--warn-border); }}
    .task-checkpoint.danger {{ border-color: var(--danger-border); }}
    .task-checkpoint > span, .task-checkpoint > strong {{ overflow-wrap: anywhere; color: var(--muted); font-size: 12px; }}
    .artifact-board, .artifact-summary-board {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }}
    .artifact-card {{ min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-bg); padding: 14px; }}
    .artifact-card-head {{ display: flex; align-items: start; justify-content: space-between; gap: 10px; margin-bottom: 10px; }}
    .artifact-card-head h3 {{ margin: 0; overflow-wrap: anywhere; }}
    .artifact-meta {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; color: var(--muted); font-size: 12px; }}
    .artifact-meta span {{ border: 1px solid var(--line); border-radius: 999px; background: var(--card-inner-bg); padding: 1px 7px; overflow-wrap: anywhere; }}
    .artifact-link {{ display: inline-flex; align-items: center; min-height: 24px; border: 1px solid var(--accent-border); border-radius: var(--radius-sm); background: var(--card-inner-bg); color: var(--accent); padding: 2px 8px; font-size: 12px; font-weight: 650; white-space: nowrap; }}
    .artifact-link:hover {{ border-color: var(--accent-border); background: var(--accent-soft); text-decoration: none; }}
    .artifact-summary-card {{ min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-bg); padding: 14px; }}
    .artifact-summary-card.ok, .artifact-summary-card.warn, .artifact-summary-card.danger, .artifact-summary-card.muted {{ border-color: var(--line); }}
    .artifact-summary-head {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px; }}
    .artifact-summary-head h3 {{ margin: 0; }}
    .artifact-main {{ display: flex; align-items: center; flex-wrap: wrap; gap: 10px; min-width: 0; }}
    .artifact-main strong {{ font-size: 18px; }}
    .artifact-links {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }}
    .artifact-impact {{ display: grid; gap: 7px; margin-top: 10px; border-top: 1px solid var(--line); padding-top: 10px; }}
    .artifact-impact p {{ margin: 0; overflow-wrap: anywhere; color: var(--muted); font-size: 12px; }}
    .status-count strong {{ font-weight: 650; }}
    .artifact-preview {{ overflow: hidden; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-bg); scroll-margin-top: 18px; }}
    .artifact-preview[open] {{ border-color: var(--accent-border); }}
    .artifact-preview summary {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; background: var(--surface-tint); padding: 14px 16px; cursor: pointer; }}
    .artifact-preview-head {{ display: grid; gap: 4px; min-width: 0; }}
    .artifact-preview-head span {{ color: var(--muted); font-size: 12px; font-weight: 650; }}
    .artifact-preview-head strong {{ overflow-wrap: anywhere; font-size: 13px; }}
    .artifact-preview > .artifact-impact {{ margin: 0; padding: 12px 16px; }}
    .artifact-markdown {{ display: grid; gap: 12px; padding: 20px; font-size: 15px; line-height: 1.72; }}
    .artifact-markdown > :first-child {{ margin-top: 0; }}
    .artifact-markdown h2, .artifact-markdown h3, .artifact-markdown h4, .artifact-markdown h5 {{ margin: 14px 0 0; color: var(--text); font-weight: 700; line-height: 1.35; }}
    .artifact-markdown h2 {{ font-size: 18px; }}
    .artifact-markdown h3 {{ font-size: 17px; }}
    .artifact-markdown h4 {{ font-size: 16px; }}
    .artifact-markdown h5 {{ font-size: 15px; }}
    .artifact-markdown p, .artifact-markdown ul {{ margin: 0; overflow-wrap: anywhere; }}
    .artifact-markdown ul {{ padding-left: 22px; }}
    .artifact-markdown blockquote {{ display: grid; gap: 6px; margin: 0; border-left: 4px solid var(--accent-border); border-radius: var(--radius); background: var(--card-inner-bg); padding: 12px 14px; color: var(--muted); }}
    .artifact-markdown pre {{ margin: 0; overflow: auto; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface-tint); padding: 12px; }}
    .artifact-markdown code {{ font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    .diagram-card {{ position: relative; display: grid; min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-inner-bg); }}
    .diagram-card .mermaid {{ margin: 0; overflow-x: auto; padding: 16px; text-align: center; }}
    .diagram-open {{ position: absolute; top: 10px; right: 10px; z-index: 1; border: 1px solid var(--accent-border); border-radius: var(--radius-sm); background: rgba(255,255,255,0.92); color: var(--accent); padding: 4px 8px; font-size: 12px; font-weight: 650; cursor: pointer; }}
    .diagram-open:hover {{ background: var(--accent-soft); }}
    .diagram-viewer {{ position: fixed; inset: 0; z-index: 2000; display: none; background: rgba(12,18,28,0.88); color: #ffffff; }}
    .diagram-viewer.open {{ display: grid; grid-template-rows: auto 1fr; }}
    .diagram-viewer-toolbar {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid rgba(255,255,255,0.16); background: rgba(12,18,28,0.94); padding: 12px 16px; }}
    .diagram-viewer-title {{ font-weight: 700; }}
    .diagram-viewer-actions {{ display: flex; gap: 8px; }}
    .diagram-viewer-actions button {{ border: 1px solid rgba(255,255,255,0.28); border-radius: var(--radius-sm); background: rgba(255,255,255,0.1); color: #ffffff; padding: 6px 10px; cursor: pointer; }}
    .diagram-stage {{ position: relative; overflow: hidden; cursor: grab; }}
    .diagram-stage.dragging {{ cursor: grabbing; }}
    .diagram-canvas {{ position: absolute; top: 50%; left: 50%; display: grid; place-items: center; min-width: 240px; min-height: 160px; transform-origin: center center; border-radius: var(--radius); background: #ffffff; color: var(--text); padding: 24px; }}
    .diagram-canvas svg {{ max-width: none; height: auto; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: var(--radius); }}
    table {{ width: 100%; border-collapse: collapse; background: var(--surface); font-size: 14px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 10px; text-align: left; vertical-align: top; }}
    th {{ background: var(--surface-soft); color: var(--muted); font-weight: 600; white-space: nowrap; }}
    tr:last-child td {{ border-bottom: 0; }}
    .test-record-list {{ display: grid; gap: 12px; }}
    .test-record-card {{ min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-bg); padding: 14px; }}
    .test-record-head {{ display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 10px; align-items: start; margin-bottom: 10px; }}
    .test-record-index {{ display: inline-flex; align-items: center; justify-content: center; min-width: 28px; height: 28px; border-radius: 999px; background: var(--surface-tint); color: var(--accent); font-size: 12px; font-weight: 700; }}
    .test-record-head h4 {{ margin: 3px 0 0; overflow-wrap: anywhere; font-size: 15px; }}
    .test-record-status {{ display: inline-flex; align-items: center; min-height: 26px; border: 1px solid var(--line); border-radius: 999px; padding: 0 10px; font-size: 12px; font-weight: 700; white-space: nowrap; }}
    .test-record-status.ok {{ border-color: var(--ok-border); color: var(--ok); background: var(--ok-soft); }}
    .test-record-status.warn {{ border-color: var(--warn-border); color: var(--warn); background: var(--warn-soft); }}
    .test-record-status.danger {{ border-color: var(--danger-border); color: var(--danger); background: var(--danger-soft); }}
    .test-record-fields {{ display: grid; gap: 8px; }}
    .test-record-field {{ display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 10px; align-items: start; border-top: 1px solid var(--line); padding-top: 8px; }}
    .test-record-field span {{ color: var(--muted); font-size: 12px; white-space: nowrap; }}
    .test-record-field p {{ margin: 0; overflow-wrap: anywhere; }}
    .record-card {{ border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-bg); padding: 14px; }}
    .record-head {{ display: flex; align-items: start; justify-content: space-between; gap: 12px; }}
    .record-head h3 {{ margin: 0; overflow-wrap: anywhere; }}
    .record-card p {{ margin: 8px 0 0; overflow-wrap: anywhere; color: var(--muted); }}
    .record-meta {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }}
    .record-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }}
    .record-grid div {{ min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-inner-bg); padding: 10px; }}
    .record-grid span, .record-grid strong {{ display: block; overflow-wrap: anywhere; }}
    .record-grid span {{ margin-bottom: 4px; color: var(--muted); font-size: 12px; }}
    .timeline {{ display: grid; gap: 24px; }}
    .timeline-date-group {{ display: grid; grid-template-columns: 112px minmax(0, 1fr); gap: 12px; align-items: start; }}
    .timeline-date-label {{ position: relative; align-self: stretch; display: flex; justify-content: center; min-height: 100%; }}
    .timeline-date-label h3 {{ position: relative; z-index: 1; display: inline-flex; align-items: center; height: 28px; margin: 0; border: 1px solid var(--accent-border); border-radius: 999px; background: var(--card-inner-bg); color: var(--accent); padding: 0 10px; font-size: 13px; white-space: nowrap; }}
    .timeline-date-group:not(:last-child) .timeline-date-label::after {{ content: ""; position: absolute; top: 34px; bottom: -22px; left: 50%; border-left: 2px dashed var(--accent-border); transform: translateX(-50%); }}
    .timeline-date-group:not(:last-child) .timeline-date-label::before {{ content: ""; position: absolute; left: 50%; bottom: -22px; width: 8px; height: 8px; border-right: 2px solid var(--accent-border); border-bottom: 2px solid var(--accent-border); transform: translateX(-50%) rotate(45deg); }}
    .timeline-date-items {{ display: grid; gap: 14px; min-width: 0; }}
    .timeline-item {{ display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 14px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-bg); padding: 16px; }}
    .timeline-time {{ display: flex; align-items: flex-start; justify-content: center; }}
    .timeline-time time {{ display: inline-flex; align-items: center; justify-content: center; min-width: 52px; border: 1px solid var(--line); border-radius: 999px; background: var(--card-inner-bg); color: var(--muted); padding: 6px 8px; font-size: 12px; font-weight: 650; }}
    .timeline-body {{ min-width: 0; }}
    .timeline-body h3 {{ margin: 0; overflow-wrap: anywhere; }}
    .timeline-details {{ display: grid; gap: 10px; margin-top: 14px; }}
    .timeline-detail {{ display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 10px; align-items: start; min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); background: var(--card-inner-bg); padding: 10px 12px; }}
    .timeline-detail span {{ color: var(--muted); font-size: 12px; font-weight: 650; overflow-wrap: anywhere; }}
    .timeline-detail p {{ margin: 0; overflow-wrap: anywhere; font-size: 13px; }}
    .pill {{ display: inline-flex; align-items: center; min-height: 24px; border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; font-size: 12px; white-space: nowrap; }}
    .pill.ok {{ border-color: var(--ok-border); background: var(--card-inner-bg); color: var(--ok); }}
    .pill.warn {{ border-color: var(--warn-border); background: var(--warn-soft); color: var(--warn); }}
    .pill.danger {{ border-color: var(--danger-border); background: var(--card-inner-bg); color: var(--danger); }}
    .pill.muted {{ background: var(--surface-tint); color: var(--muted); }}
    .collapsible-panel {{ padding: 0; overflow: hidden; }}
    .collapsible-panel > summary {{ list-style: none; padding: 18px 20px 0; cursor: pointer; }}
    .collapsible-panel > summary::-webkit-details-marker {{ display: none; }}
    .collapsible-panel > summary .section-heading {{ position: relative; padding-right: 34px; }}
    .collapsible-panel > summary .section-heading::after {{ content: "⌄"; position: absolute; top: 0; right: 0; display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px; border: 1px solid var(--line); border-radius: 999px; background: var(--card-inner-bg); color: var(--accent); }}
    .collapsible-panel[open] > summary .section-heading::after {{ transform: rotate(180deg); }}
    .collapsible-body {{ padding: 0 20px 20px; }}
    .empty {{ margin: 0; border: 1px dashed var(--line); border-radius: var(--radius); background: var(--card-bg); padding: 18px; color: var(--muted); text-align: center; }}
    @media (max-width: 960px) {{
      .shell {{ padding: 14px; }}
      .page-layout, .page-layout.outline-collapsed {{ grid-template-columns: 1fr; }}
      .outline {{ position: static; max-height: none; }}
      .outline-collapsed .outline-title, .outline-collapsed .outline a {{ display: block; }}
      .outline-collapsed .outline-head {{ justify-content: space-between; margin-bottom: 8px; }}
      .hero-top, .context-grid, .task-card {{ grid-template-columns: 1fr; }}
      .task-status-summary {{ grid-template-columns: 1fr; }}
      .hero-meta {{ text-align: left; }}
      .metrics, .pipeline {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .pipeline-step::after {{ display: none; }}
      .todo-card {{ grid-template-columns: 1fr; }}
      .todo-type {{ border-right: 0; border-bottom: 1px solid var(--line); }}
      .todo-action {{ justify-content: flex-start; border-top: 1px solid var(--line); border-left: 0; }}
      .task-checkpoints {{ grid-template-columns: 1fr; }}
      .timeline-date-group, .timeline-item, .timeline-detail {{ grid-template-columns: 1fr; }}
      .test-record-head {{ grid-template-columns: auto minmax(0, 1fr); }}
      .test-record-status {{ grid-column: 2; justify-self: start; }}
      .test-record-field {{ grid-template-columns: 1fr; gap: 3px; }}
      .record-grid {{ grid-template-columns: 1fr; }}
      .timeline-date-group:not(:last-child) .timeline-date-label::after, .timeline-date-group:not(:last-child) .timeline-date-label::before {{ display: none; }}
      .timeline-date-label, .timeline-time {{ justify-content: flex-start; }}
      .section-heading {{ display: block; }}
      .section-heading p {{ margin-top: 4px; text-align: left; }}
    }}
    @media (max-width: 560px) {{
      .metrics, .pipeline, .requirement-stats {{ grid-template-columns: 1fr; }}
      .requirement-card {{ grid-template-columns: 1fr; }}
      .artifact-preview summary, .record-head {{ align-items: flex-start; flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="page-layout" id="pageLayout">
      <aside class="outline" aria-label="页面大纲">
        <div class="outline-head">
          <span class="outline-title">页面大纲</span>
          <button class="outline-toggle" id="outlineToggle" type="button" aria-label="展开或收起大纲">‹</button>
        </div>
        <nav>
          <a href="#context">项目上下文</a>
          <a href="#todos">人工待办</a>
          <a href="#issues">待决策问题</a>
          <a href="#tasks">任务进展</a>
          <a href="#artifacts">阶段产物状态</a>
          <a href="#artifact-content">产物内容</a>
          <a href="#requirements">需求纳入决策</a>
          <a href="#decisions">决策归档</a>
          <a href="#events">工作日志</a>
        </nav>
      </aside>

      <div class="main-column">
        <header class="hero">
          <div class="hero-top">
            <div>
              <h1>{_escape(project['name'])}</h1>
            </div>
            <div class="hero-meta">生成时间<br><strong>{_escape(_format_timestamp(state['updated_at']))}</strong></div>
          </div>
          {pipeline_markup}
          <div class="metrics">
            {_metric("待决策问题", len(open_questions), "#issues")}
            {_metric("待审核/需修改产物", len(pending_reviews), "#artifacts")}
          </div>
        </header>

        <main class="content-flow">
          <section class="panel" id="context">
            {_section_heading("项目上下文", "先确认项目目标和基础资料，再处理下面的待办。")}
            {context_markup}
          </section>

          <section class="panel primary" id="todos">
            {_section_heading("人工待办", "优先处理这里的内容；它们通常会阻塞下一步推进。")}
            {todo_markup}
          </section>

          <section class="panel" id="issues">
            {_section_heading("待决策问题", "需要人工确认后才能继续推进的需求或技术问题。")}
            {question_markup}
          </section>

          <section class="panel" id="tasks">
            {_section_heading("任务进展", "按任务查看规格、实现和单元测试的推进状态。")}
            {task_markup}
          </section>

          <section class="panel" id="artifacts">
            {_section_heading("阶段产物状态", "查看产物版本、审核状态和关联任务。")}
            {artifact_markup}
          </section>

          <section class="panel" id="artifact-content">
            {_section_heading("产物内容", "展开查看主要阶段产物的文档式预览。")}
            {preview_markup}
          </section>

          <section class="panel" id="requirements">
            {_section_heading("需求纳入决策", "从 PRD 提取并按目标平台过滤后的需求范围。")}
            {requirement_markup}
          </section>

          {_collapsible_section("decisions", "决策归档", "全部已确认的人工决策记录。", decision_markup)}
          {_collapsible_section("events", "工作日志", "结构化事件按最新优先展示。", event_markup)}
        </main>
      </div>
    </div>
  </div>
  <div class="diagram-viewer" id="diagramViewer" aria-hidden="true">
    <div class="diagram-viewer-toolbar">
      <div class="diagram-viewer-title">图表查看</div>
      <div class="diagram-viewer-actions">
        <button id="diagramZoomOut" type="button">-</button>
        <button id="diagramZoomReset" type="button">重置</button>
        <button id="diagramZoomIn" type="button">+</button>
        <button id="diagramClose" type="button">关闭</button>
      </div>
    </div>
    <div class="diagram-stage" id="diagramStage"><div class="diagram-canvas" id="diagramCanvas"></div></div>
  </div>
  <script>
    const pageLayout = document.getElementById('pageLayout');
    const outlineToggle = document.getElementById('outlineToggle');
    outlineToggle.addEventListener('click', () => {{
      pageLayout.classList.toggle('outline-collapsed');
      outlineToggle.textContent = pageLayout.classList.contains('outline-collapsed') ? '›' : '‹';
    }});
    function openHashTargets() {{
      if (!location.hash) return;
      const target = document.querySelector(location.hash);
      if (target && target.matches('details')) target.open = true;
    }}
    window.addEventListener('hashchange', openHashTargets);
    openHashTargets();

    const diagramViewer = document.getElementById('diagramViewer');
    const diagramStage = document.getElementById('diagramStage');
    const diagramCanvas = document.getElementById('diagramCanvas');
    const diagramState = {{ scale: 1, x: 0, y: 0, dragging: false, startX: 0, startY: 0, originX: 0, originY: 0 }};
    function applyDiagramTransform() {{
      diagramCanvas.style.transform = `translate(calc(-50% + ${{diagramState.x}}px), calc(-50% + ${{diagramState.y}}px)) scale(${{diagramState.scale}})`;
    }}
    function resetDiagramTransform() {{ diagramState.scale = 1; diagramState.x = 0; diagramState.y = 0; applyDiagramTransform(); }}
    function zoomDiagram(delta) {{ diagramState.scale = Math.min(4, Math.max(0.25, diagramState.scale + delta)); applyDiagramTransform(); }}
    function openDiagramViewer(button) {{
      const diagram = button.closest('.diagram-card')?.querySelector('.mermaid');
      if (!diagram) return;
      diagramCanvas.innerHTML = '';
      const rendered = diagram.querySelector('svg');
      diagramCanvas.appendChild((rendered || diagram).cloneNode(true));
      diagramViewer.classList.add('open');
      diagramViewer.setAttribute('aria-hidden', 'false');
      document.body.style.overflow = 'hidden';
      resetDiagramTransform();
    }}
    function closeDiagramViewer() {{ diagramViewer.classList.remove('open'); diagramViewer.setAttribute('aria-hidden', 'true'); document.body.style.overflow = ''; diagramCanvas.innerHTML = ''; }}
    document.querySelectorAll('.diagram-open').forEach((button) => button.addEventListener('click', () => openDiagramViewer(button)));
    document.getElementById('diagramClose').addEventListener('click', closeDiagramViewer);
    document.getElementById('diagramZoomIn').addEventListener('click', () => zoomDiagram(0.2));
    document.getElementById('diagramZoomOut').addEventListener('click', () => zoomDiagram(-0.2));
    document.getElementById('diagramZoomReset').addEventListener('click', resetDiagramTransform);
    diagramStage.addEventListener('wheel', (event) => {{ event.preventDefault(); zoomDiagram(event.deltaY < 0 ? 0.12 : -0.12); }}, {{ passive: false }});
    diagramStage.addEventListener('pointerdown', (event) => {{ diagramState.dragging = true; diagramState.startX = event.clientX; diagramState.startY = event.clientY; diagramState.originX = diagramState.x; diagramState.originY = diagramState.y; diagramStage.classList.add('dragging'); diagramStage.setPointerCapture(event.pointerId); }});
    diagramStage.addEventListener('pointermove', (event) => {{ if (!diagramState.dragging) return; diagramState.x = diagramState.originX + event.clientX - diagramState.startX; diagramState.y = diagramState.originY + event.clientY - diagramState.startY; applyDiagramTransform(); }});
    diagramStage.addEventListener('pointerup', (event) => {{ diagramState.dragging = false; diagramStage.classList.remove('dragging'); diagramStage.releasePointerCapture(event.pointerId); }});
    window.addEventListener('keydown', (event) => {{ if (event.key === 'Escape' && diagramViewer.classList.contains('open')) closeDiagramViewer(); }});
  </script>
  <script type="module">
    import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
    mermaid.initialize({{ startOnLoad: true, securityLevel: 'strict', theme: 'neutral' }});
  </script>
</body>
</html>
"""


def _render_pipeline(
    state: dict[str, Any],
    *,
    current_stage: str,
    stage_progress: dict[str, str],
) -> str:
    steps = []
    for index, stage in enumerate(STAGES[:-1]):
        progress = stage_progress[stage]
        css_class = {
            "completed": "done",
            "review": "review",
            "changes_requested": "blocked",
            "needs_reconcile": "blocked",
            "blocked": "blocked",
            "failed": "blocked",
            "skipped": "upcoming",
            "optional": "upcoming",
            "ready": "current",
            "in_progress": "current",
            "not_started": "upcoming",
        }[progress]
        note = STATUS_LABELS[progress]
        if stage == current_stage and state["mode"] != "ready":
            css_class = (
                "review"
                if state["mode"] == "review"
                else "blocked"
                if state["mode"] in {"blocked", "decision"}
                else "current"
            )
            note = MODE_LABELS[state["mode"]]
        steps.append(
            '<div class="pipeline-step '
            f'{css_class}" data-state="{css_class}">'
            f'<div class="pipeline-dot">{index + 1}</div>'
            f'<div><strong>{_escape(STAGE_LABELS[stage])}</strong><span>{_escape(note)}</span></div>'
            "</div>"
        )
    return '<div class="pipeline">' + "".join(steps) + "</div>"


def _render_context(
    project: dict[str, Any],
    requirements: dict[str, Any],
    analysis_body: str,
) -> str:
    prd_files = "".join(f'<span class="path">{_escape(path)}</span>' for path in project["prd_files"])
    disposition_counts: dict[str, int] = {}
    for item in requirements["items"]:
        disposition_counts[item["disposition"]] = disposition_counts.get(item["disposition"], 0) + 1
    summary = _analysis_summary(analysis_body, requirements["items"])
    return f"""
    <div class="context-grid">
      <article class="context-card summary-card">
        <span class="eyebrow">需求概要</span>
        <div class="summary-block">{_inline(summary)}</div>
      </article>
      <aside class="context-card meta-card">
        <div class="meta-row"><span>工作空间</span><div class="meta-value"><span class="pill ok">结构完整</span></div></div>
        <div class="meta-row"><span>PRD</span><strong>{len(project['prd_files'])} 份</strong></div>
        <div class="meta-row"><span>平台</span><strong>{_escape(project['platform'])}</strong></div>
        <div class="meta-row repo-row"><span>代码仓库</span><div class="meta-value path">{_escape(project['code_repository'])}</div></div>
        <div class="req-stat-row">
          <span>需求</span>
          <strong>{sum(item['disposition'] == 'accepted' for item in requirements['items'])} 已确认实施</strong>
          <strong>{sum(item['disposition'] in {'proposed', 'deferred', 'excluded'} for item in requirements['items'])} 未确认实施</strong>
          <strong>{sum(item['disposition'] == 'withdrawn' for item in requirements['items'])} 已撤回</strong>
          <strong>{disposition_counts.get('accepted', 0)} 纳入</strong>
          <strong>{disposition_counts.get('proposed', 0)} 待确认</strong>
        </div>
        <div class="prd-list"><span class="eyebrow">PRD 文件</span><div class="tag-list">{prd_files or '<span class="tag">暂无</span>'}</div></div>
      </aside>
    </div>
    """


def _analysis_summary(body: str, requirements: list[dict[str, Any]]) -> str:
    match = re.search(
        r"^#{2,4}\s+(?:分析结论|需求概要)\s*$\n(.*?)(?=^#{1,4}\s+|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    if match:
        summary = " ".join(
            line.lstrip("- ").strip()
            for line in match.group(1).splitlines()
            if line.strip()
        )
        if summary:
            return summary
    accepted = [item for item in requirements if item["disposition"] == "accepted"]
    source = accepted or requirements
    if source:
        return "\n".join(f"{item['title']}：{item['summary']}" for item in source)
    return "需求分析尚未生成。"


def _render_todos(
    *,
    open_questions: list[dict[str, Any]],
    pending_reviews: set[str],
    artifacts: list[dict[str, Any]],
    next_action: str,
) -> str:
    cards = []
    for question in open_questions:
        cards.append(
            _todo_card(
                "人工决策",
                question["id"],
                question["question"],
                [f"原因：{question['reason']}", f"建议：{question['recommendation']}"],
                status="待决策",
                tone="warn",
                target="#issues",
                action="去处理",
            )
        )
    artifacts_by_ref = {f"{item['id']}@{item['revision']}": item for item in artifacts}
    for reference in sorted(pending_reviews):
        artifact = artifacts_by_ref.get(reference)
        title = artifact["path"] if artifact else reference
        status = STATUS_LABELS.get(artifact["status"], artifact["status"]) if artifact else "待审核"
        target = f"#{_artifact_anchor(artifact['id'])}" if artifact else "#artifacts"
        cards.append(
            _todo_card(
                "产物审核",
                "" if artifact else reference,
                title,
                [],
                status=status,
                tone="warn" if status == "待审核" else "danger",
                target=target,
                action="去审核",
                title_is_path=artifact is not None,
            )
        )
    if not cards:
        message = f"当前没有人工待办，下一步：{ACTION_LABELS.get(next_action, next_action)}。"
        return _empty(message)
    return '<div class="todo-list">' + "".join(cards) + "</div>"


def _todo_card(
    kind: str,
    identifier: str,
    title: str,
    details: list[str],
    *,
    status: str,
    tone: str,
    target: str,
    action: str,
    title_is_path: bool = False,
) -> str:
    detail_markup = "".join(f"<span>{_escape(value)}</span>" for value in details if value)
    title_prefix = f"{_escape(identifier)} · " if identifier else ""
    title_markup = (
        f'<span class="path">{_escape(title)}</span>'
        if title_is_path
        else f"<h3>{title_prefix}{_escape(title)}</h3>"
    )
    return f"""
    <article class="todo-card {_escape(tone)}">
      <div class="todo-type"><strong>{_escape(kind)}</strong><span class="pill {_escape(tone)}">{_escape(status)}</span></div>
      <div class="todo-main"><div>{title_markup}<div class="todo-detail">{detail_markup}</div></div></div>
      <div class="todo-action"><a class="artifact-link todo-action-link" href="{_escape(target)}">{_escape(action)}</a></div>
    </article>
    """


def _artifact_target(
    artifact_id: str, artifacts: list[dict[str, Any]], fallback: str
) -> str:
    if any(item["id"] == artifact_id for item in artifacts):
        return f"#{_artifact_anchor(artifact_id)}"
    return fallback


def _render_questions(items: list[dict[str, Any]]) -> str:
    if not items:
        return _empty("当前没有待决策问题。")
    cards = []
    for item in items:
        cards.append(
            f"""
            <article class="record-card">
              <div class="record-head"><div><span class="eyebrow">{_escape(STAGE_LABELS[item['stage']])}</span><h3>{_escape(item['id'])} · {_escape(item['question'])}</h3></div>{_pill(item['status'])}</div>
              <p>{_escape(item['reason'])}</p>
              <div class="record-grid">
                <div><span>AI 建议</span><strong>{_escape(item['recommendation'])}</strong></div>
                <div><span>关联阶段</span><strong>{_escape(STAGE_LABELS[item['stage']])}</strong></div>
                <div><span>关联工作</span><strong>{_escape(item['work_id'])}{_escape(' · ' + item['active_item'] if item['active_item'] else '')}</strong></div>
              </div>
            </article>
            """
        )
    return '<div class="record-list">' + "".join(cards) + "</div>"


def _render_requirements(items: list[dict[str, Any]]) -> str:
    if not items:
        return _empty("暂无需求。")
    accepted = sum(item["disposition"] == "accepted" for item in items)
    proposed = sum(item["disposition"] == "proposed" for item in items)
    historical = sum(item["disposition"] == "withdrawn" for item in items)
    excluded = len(items) - accepted - proposed - historical
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(item["disposition"], []).append(item)
    group_markup = []
    for disposition in ("accepted", "proposed", "deferred", "excluded", "withdrawn"):
        group = groups.get(disposition)
        if not group:
            continue
        cards = []
        for item in group:
            tags = [
                f"平台：{PLATFORM_SCOPE_LABELS[item['platform_scope']]}",
                f"变更：{CHANGE_TYPE_LABELS[item['change_type']]}",
                f"处理：{STATUS_LABELS[item['disposition']]}",
                *(
                    f"来源：{SOURCE_KIND_LABELS[source['kind']]} · {source['ref']}"
                    for source in item["sources"]
                ),
            ]
            tone = _status_tone(item["disposition"])
            cards.append(
                f"""
                <article class="requirement-card {tone}">
                  <div class="requirement-id">{_escape(item['id'])}</div>
                  <div class="requirement-main">
                    <h3>{_escape(item['title'])}</h3>
                    <div class="requirement-meta">{''.join(f'<span>{_escape(tag)}</span>' for tag in tags)}</div>
                  </div>
                </article>
                """
            )
        group_markup.append(
            f'<section class="requirement-group {_status_tone(disposition)}">'
            f'<div class="requirement-group-head"><h3>{_escape(STATUS_LABELS[disposition])}</h3><span>{len(group)} 项</span></div>'
            + "".join(cards)
            + "</section>"
        )
    return f"""
    <div class="requirement-stats">
      <div class="ok"><span>已纳入</span><strong>{accepted}</strong></div>
      <div class="warn"><span>待确认</span><strong>{proposed}</strong></div>
      <div class="muted"><span>其他处置</span><strong>{excluded}</strong></div>
      <div class="muted"><span>历史撤回</span><strong>{historical}</strong></div>
    </div>
    <div class="requirement-board">{''.join(group_markup)}</div>
    """


def _render_tasks(
    items: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    *,
    task_progress: list[dict[str, Any]] | None = None,
    implementation_complete: bool = False,
) -> str:
    if not items:
        return _empty("暂无任务；任务将在任务规格阶段建立。")
    cards = []
    artifacts_by_task_and_stage = {
        (artifact["active_item"], artifact["stage"]): artifact
        for artifact in artifacts
        if artifact["active_item"] is not None
    }
    progress_by_id = {item["id"]: item for item in task_progress or []}
    for item in items:
        requirements = ", ".join(item["requirements"]) or "-"
        checkpoints = []
        for label, stage in (
            ("规格", "specification"),
            ("实现", "implementation"),
            ("测试", "testing"),
        ):
            artifact = artifacts_by_task_and_stage.get((item["id"], stage))
            if artifact is None:
                checkpoints.append(
                    f'<div class="task-checkpoint muted"><span>{_escape(label)}</span><strong>未生成</strong></div>'
                )
                continue
            display_status = (
                artifact.get("test_status", artifact["status"])
                if stage == "testing"
                else "needs_reconcile"
                if artifact.get("needs_reconcile")
                else artifact["status"]
            )
            tone = _status_tone(display_status)
            checkpoints.append(
                f'<a class="task-checkpoint {tone}" href="#{_artifact_anchor(artifact["id"])}">'
                f'<span>{_escape(label)}</span>{_pill(display_status)}</a>'
            )
        progress_status = progress_by_id.get(item["id"], {}).get("status")
        if progress_status is None:
            progress_status = _derived_task_status(item["id"], artifacts_by_task_and_stage)
        if item["status"] == "withdrawn":
            progress_status = "withdrawn"
        cards.append(
            f"""
            <article class="task-card">
              <div class="task-title"><strong>{_escape(item['id'])} · {_escape(item['title'])}</strong><span class="task-requirements">需求：{_escape(requirements)}</span>{_pill(progress_status)}</div>
              <div class="task-checkpoints">{''.join(checkpoints)}</div>
            </article>
            """
        )
    progress_items = task_progress or []
    test_counts = {
        status: sum(item.get("test_status") == status for item in progress_items)
        for status in ("not_run", "completed", "failed", "skipped")
    }
    test_summary = "".join(
        f'<span class="tag">{_escape(STATUS_LABELS[status])}：{count}</span>'
        for status, count in test_counts.items()
    )
    summary = (
        '<div class="task-status-summary">'
        '<div class="task-status-group"><span>需求实现状态</span>'
        + _pill("completed" if implementation_complete else "in_progress")
        + '</div><div class="task-status-group"><span>单元测试状态</span>'
        + f'<div class="test-status-counts">{test_summary}</div></div></div>'
    )
    return summary + '<div class="task-board">' + "".join(cards) + "</div>"


def _derived_task_status(
    task_id: str,
    artifacts: dict[tuple[str | None, str], dict[str, Any]],
) -> str:
    for stage in ("testing", "implementation", "specification"):
        artifact = artifacts.get((task_id, stage))
        if artifact is None:
            continue
        if artifact["status"] != "approved":
            return artifact["status"]
        return "completed" if stage == "testing" else (
            "testing" if stage == "implementation" else "implementation"
        )
    return "specification"


def _render_artifacts(items: list[dict[str, Any]]) -> str:
    if not items:
        return _empty("暂无阶段产物。")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(item["stage"], []).append(item)
    cards = []
    for stage in STAGES[:-1]:
        group = grouped.get(stage, [])
        if not group:
            continue
        approved = sum(
            item["status"] == "approved" and not item.get("needs_reconcile")
            for item in group
        )
        group_tone = (
            "danger"
            if any(
                item["status"] == "changes_requested" or item.get("needs_reconcile")
                for item in group
            )
            else "warn"
            if any(item["status"] == "review" for item in group)
            else "ok"
            if approved == len(group)
            else "muted"
        )
        if len(group) == 1 and group[0]["active_item"] is None:
            item = group[0]
            body = f"""
              <div class="artifact-main">
                <span class="path">{_escape(item['path'])}</span>
                {_pill('needs_reconcile' if item.get('needs_reconcile') else item['status'])}
                <a class="artifact-link" href="#{_artifact_anchor(item['id'])}">查看内容</a>
              </div>
              <div class="artifact-meta"><span>版本：r{item['revision']}</span><span>更新时间：{_escape(_format_timestamp(item['updated_at']))}</span></div>
              {_render_artifact_impact(item)}
            """
        else:
            links = "".join(
                f'<a class="artifact-link" href="#{_artifact_anchor(item["id"])}">{_escape(item["active_item"] or item["id"])}</a>'
                for item in group
            )
            status_counts: dict[str, int] = {}
            for item in group:
                display_status = (
                    "needs_reconcile" if item.get("needs_reconcile") else item["status"]
                )
                status_counts[display_status] = status_counts.get(display_status, 0) + 1
            counts = "".join(
                f'<span class="status-count {_status_tone(status)}"><strong>{_escape(STATUS_LABELS.get(status, status))}</strong>：{count}</span>'
                for status, count in status_counts.items()
            )
            body = f"""
              <div class="artifact-main"><strong>{approved}/{len(group)} 已批准</strong></div>
              <div class="artifact-meta">{counts}</div>
              <div class="artifact-links">{links}</div>
              {''.join(_render_artifact_impact(item) for item in group if item['status'] == 'review')}
            """
        cards.append(
            f'<article class="artifact-summary-card {group_tone}"><div class="artifact-summary-head"><h3>{_escape(STAGE_LABELS[stage])}</h3></div>{body}</article>'
        )
    return '<div class="artifact-summary-board">' + "".join(cards) + "</div>"


def _render_artifact_previews(items: list[dict[str, Any]], bodies: dict[str, str]) -> str:
    if not items:
        return _empty("暂无可预览产物。")
    previews = []
    for item in items:
        body = bodies.get(item["id"], "Artifact content is unavailable.")
        previews.append(
            f"""
            <details class="artifact-preview" id="{_artifact_anchor(item['id'])}">
              <summary>
                <div class="artifact-preview-head"><span>{_escape(STAGE_LABELS[item['stage']])}</span><strong>{_escape(item['path'])}</strong></div>
                {_pill('needs_reconcile' if item.get('needs_reconcile') else item.get('test_status', item['status']))}
              </summary>
              {_render_artifact_impact(item)}
              {_render_markdown(body)}
            </details>
            """
        )
    return '<div class="artifact-preview-list">' + "".join(previews) + "</div>"


def _render_artifact_impact(item: dict[str, Any]) -> str:
    reasons = item.get("needs_reconcile", [])
    if not reasons:
        return ""
    tags = ["状态：待核对", f"直接原因：{', '.join(reasons)}"]
    return (
        '<div class="artifact-impact"><div class="tag-list">'
        + "".join(f'<span class="tag">{_escape(tag)}</span>' for tag in tags)
        + "</div></div>"
    )


def _render_decisions(items: list[dict[str, Any]]) -> str:
    if not items:
        return _empty("暂无决策归档。")
    records = [
        {
            "created_at": item["created_at"],
            "title": f"{item['id']} · {item['question_id']}",
            "details": [
                ("决策", item["decision"]),
                ("状态", "已记录"),
            ],
        }
        for item in items
    ]
    return _render_timeline(records)


def _render_events(items: list[dict[str, Any]]) -> str:
    if not items:
        return _empty("暂无工作日志。")
    event_labels = {
        "workspace_initialized": "工作空间初始化",
        "work_prepared": "准备阶段工作",
        "artifact_submitted": "提交阶段产物",
        "artifact_approved": "批准阶段产物",
        "revision_requested": "请求修改产物",
        "question_opened": "提出阻塞问题",
        "decision_recorded": "记录人工决策",
        "artifact_reconciled": "核对阶段产物",
    }
    records = []
    for item in items:
        details = [("事件", item["event_id"])]
        for key, value in item.get("data", {}).items():
            details.append((_event_field_label(key), _display_value(value)))
        records.append(
            {
                "created_at": item["created_at"],
                "title": event_labels.get(item["type"], item["type"]),
                "details": details,
            }
        )
    return _render_timeline(records)


def _render_timeline(records: list[dict[str, Any]]) -> str:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        created_at = record["created_at"]
        date, _, time = created_at.partition("T")
        normalized = {**record, "date": date, "time": time[:5] or "--:--"}
        groups.setdefault(date, []).append(normalized)
    date_groups = []
    for date, entries in reversed(list(groups.items())):
        timeline_items = []
        for entry in reversed(entries):
            details = "".join(
                f'<div class="timeline-detail"><span>{_escape(label)}</span><p>{_escape(value)}</p></div>'
                for label, value in entry["details"]
            )
            timeline_items.append(
                f'<article class="timeline-item"><div class="timeline-time"><time>{_escape(entry["time"])}</time></div>'
                f'<div class="timeline-body"><h3>{_escape(entry["title"])}</h3><div class="timeline-details">{details}</div></div></article>'
            )
        date_groups.append(
            f'<section class="timeline-date-group"><div class="timeline-date-label"><h3>{_escape(date)}</h3></div>'
            f'<div class="timeline-date-items">{"".join(timeline_items)}</div></section>'
        )
    return '<div class="timeline">' + "".join(date_groups) + "</div>"


def _event_field_label(value: str) -> str:
    return {
        "artifact_id": "产物",
        "revision": "版本",
        "outcome": "结果",
        "question_id": "问题",
        "decision_id": "决策",
        "work_id": "Work",
        "stage": "阶段",
        "active_item": "任务",
        "source": "来源",
        "artifacts": "影响产物",
        "project_id": "项目",
    }.get(value, value)


def _display_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return "-"
    return str(value)


def _format_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return value
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _render_markdown(text: str) -> str:
    lines = text.splitlines()
    parts: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    quote_items: list[str] = []
    code_lines: list[str] = []
    code_language = ""
    in_code = False

    def flush_paragraph() -> None:
        if paragraph:
            parts.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            parts.append("<ul>" + "".join(f"<li>{_inline(item)}</li>" for item in list_items) + "</ul>")
            list_items.clear()

    def flush_quote() -> None:
        if quote_items:
            parts.append("<blockquote>" + "".join(f"<p>{_inline(item)}</p>" for item in quote_items) + "</blockquote>")
            quote_items.clear()

    def flush_code() -> None:
        nonlocal code_language
        code = "\n".join(code_lines)
        if code_language == "mermaid":
            parts.append(
                '<div class="diagram-card">'
                '<button class="diagram-open" type="button" aria-label="全屏查看图表">全屏查看</button>'
                f'<div class="mermaid">{_escape(code)}</div></div>'
            )
        else:
            parts.append("<pre><code>" + _escape(code) + "</code></pre>")
        code_lines.clear()
        code_language = ""

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            flush_list()
            flush_quote()
            if in_code:
                flush_code()
            else:
                code_language = stripped[3:].strip().split(" ", 1)[0].lower()
            in_code = not in_code
            index += 1
            continue
        if in_code:
            code_lines.append(line)
            index += 1
            continue
        if not stripped:
            flush_paragraph()
            flush_list()
            flush_quote()
            index += 1
            continue
        if (
            stripped.startswith("|")
            and index + 1 < len(lines)
            and _is_table_separator(lines[index + 1])
        ):
            flush_paragraph()
            flush_list()
            flush_quote()
            headers = _table_cells(stripped)
            rows: list[list[str]] = []
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(_table_cells(lines[index]))
                index += 1
            if _is_wide_test_record_table(headers):
                parts.append(_render_test_record_table(headers, rows))
                continue
            head = "".join(f"<th>{_inline(cell)}</th>" for cell in headers)
            body = "".join(
                "<tr>"
                + "".join(
                    f"<td>{_inline(row[column] if column < len(row) else '')}</td>"
                    for column in range(len(headers))
                )
                + "</tr>"
                for row in rows
            )
            parts.append(
                '<div class="table-wrap"><table><thead><tr>'
                + head
                + "</tr></thead><tbody>"
                + body
                + "</tbody></table></div>"
            )
            continue
        heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            flush_list()
            flush_quote()
            level = min(len(heading.group(1)) + 1, 5)
            parts.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
            continue
        if stripped.startswith("- "):
            flush_paragraph()
            flush_quote()
            list_items.append(stripped[2:])
            index += 1
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            flush_list()
            quote_items.append(stripped.lstrip("> "))
            index += 1
            continue
        flush_list()
        flush_quote()
        paragraph.append(stripped)
        index += 1
    flush_paragraph()
    flush_list()
    flush_quote()
    if in_code:
        flush_code()
    return '<div class="artifact-markdown">' + "".join(parts) + "</div>"


def _inline(value: str) -> str:
    escaped = _escape(value)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)


def _is_table_separator(value: str) -> bool:
    cells = _table_cells(value)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _table_cells(value: str) -> list[str]:
    return [cell.strip() for cell in value.strip().strip("|").split("|")]


def _is_wide_test_record_table(headers: list[str]) -> bool:
    return headers in (
        ["#", "行为", "测试点", "测试文件", "状态", "说明"],
        ["#", "验证项", "命令/方式", "结果", "说明"],
    )


def _render_test_record_table(headers: list[str], rows: list[list[str]]) -> str:
    cards = []
    for row in rows:
        values = row + [""] * max(0, len(headers) - len(row))
        record = dict(zip(headers, values))
        title_field = "行为" if "行为" in record else "验证项"
        status = record.get("状态", record.get("结果", ""))
        fields = "".join(
            f'<div class="test-record-field"><span>{_escape(header)}</span><p>{_inline(record[header])}</p></div>'
            for header in headers
            if header not in {"#", title_field, "状态", "结果"} and record.get(header)
        )
        status_markup = (
            f'<span class="test-record-status {_test_record_tone(status)}">{_inline(status)}</span>'
            if status
            else ""
        )
        cards.append(
            '<article class="test-record-card"><div class="test-record-head">'
            f'<span class="test-record-index">{_escape(record.get("#") or "-")}</span>'
            f'<h4>{_inline(record.get(title_field) or "未命名记录")}</h4>{status_markup}'
            f'</div><div class="test-record-fields">{fields}</div></article>'
        )
    return '<div class="test-record-list">' + "".join(cards) + "</div>"


def _test_record_tone(status: str) -> str:
    if status in {"已生成", "通过", "PASS"}:
        return "ok"
    if status in {"阻塞", "失败", "FAIL"}:
        return "danger"
    return "warn" if status in {"待补充", "未执行"} else "muted"


def _metric(label: str, value: Any, target: str) -> str:
    return f'<a class="metric" href="{_escape(target)}"><span>{_escape(label)}</span><strong>{_escape(value)}</strong></a>'


def _section_heading(title: str, description: str) -> str:
    return f'<div class="section-heading"><h2>{_escape(title)}</h2><p>{_escape(description)}</p></div>'


def _collapsible_section(section_id: str, title: str, description: str, body: str) -> str:
    return f"""
    <details class="panel collapsible-panel" id="{_escape(section_id)}">
      <summary>{_section_heading(title, description)}</summary>
      <div class="collapsible-body">{body}</div>
    </details>
    """


def _meta_row(label: str, value: Any) -> str:
    return f'<div class="meta-row"><dt>{_escape(label)}</dt><dd>{_escape(value)}</dd></div>'


def _pill(status: str) -> str:
    css_class = _status_tone(status)
    return f'<span class="pill {css_class}">{_escape(STATUS_LABELS.get(status, status))}</span>'


def _status_tone(status: str) -> str:
    return {
        "approved": "ok",
        "accepted": "ok",
        "active": "ok",
        "completed": "ok",
        "resolved": "ok",
        "review": "warn",
        "proposed": "warn",
        "in_progress": "warn",
        "ready": "warn",
        "specification": "warn",
        "implementation": "warn",
        "testing": "warn",
        "changes_requested": "danger",
        "needs_reconcile": "warn",
        "failed": "danger",
        "skipped": "muted",
        "not_run": "muted",
        "optional": "muted",
        "dependency_blocked": "danger",
        "open": "danger",
    }.get(status, "muted")


def _artifact_anchor(artifact_id: str) -> str:
    return "artifact-" + re.sub(r"[^a-z0-9]+", "-", artifact_id.lower()).strip("-")


def _empty(message: str) -> str:
    return f'<p class="empty">{_escape(message)}</p>'


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)
