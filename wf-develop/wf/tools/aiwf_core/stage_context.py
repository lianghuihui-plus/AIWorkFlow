"""Five-stage semantic task context construction."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .repository import repository_context


def build_stage_context(
    stage: str,
    *,
    active_item: str | None,
    instruction: str,
    project: dict[str, Any],
    approved_artifact: Callable[[str], dict[str, Any]],
    current_requirements: Callable[[], list[dict[str, Any]]],
    task_by_id: Callable[[str | None], dict[str, Any]],
    task_facts: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    if stage == "analysis":
        context = {
            "goal": f"从全部 PRD 中提取属于 {project['platform']} 且确实需要本端实施的功能点。",
            "inputs": [".aiwf/project.json", *project["prd_files"]],
            "sources": list(project["prd_files"]),
            "depends_on": [],
            "stage_guide": "references/stages/analysis.md",
            "facts": {"requirements": current_requirements()},
            "repository_context": repository_context(project["code_repository"]),
            "constraints": [
                "以目标平台过滤为主线，区分本端、跨端和其他端内容。",
                "区分新增、修改、复用以及无需本端实施的已有能力。",
                "只有缺少答案会阻止安全推进时才创建阻塞问题。",
                "只写任务包指定的草稿和结果文件。",
            ],
        }
    elif stage == "design":
        analysis = approved_artifact("analysis")
        context = {
            "goal": "结合真实代码仓库形成模块、文件、类、职责和交互层面的技术架构方案。",
            "inputs": [".aiwf/project.json", analysis["snapshot_path"]],
            "sources": [analysis["snapshot_path"]],
            "depends_on": [f"analysis@{analysis['approved_revision']}"],
            "stage_guide": "references/stages/design.md",
            "facts": {"requirements": current_requirements()},
            "repository_context": repository_context(project["code_repository"]),
            "constraints": [
                "设计必须基于仓库中的真实模块和代码证据。",
                "设计只描述架构和代码组织，不拆分执行任务，不编写正式代码。",
                "允许使用伪代码，不设计单元测试。",
                "只写任务包指定的草稿和结果文件。",
            ],
        }
    elif stage == "specification":
        analysis = approved_artifact("analysis")
        design = approved_artifact("design")
        code_repository_context = repository_context(project["code_repository"])
        if active_item is None:
            context = {
                "goal": "根据已批准技术方案拆分可独立编码的任务及其硬依赖。",
                "inputs": [analysis["snapshot_path"], design["snapshot_path"]],
                "sources": [analysis["snapshot_path"], design["snapshot_path"]],
                "depends_on": [f"design@{design['approved_revision']}"],
                "stage_guide": "references/stages/specification.md",
                "facts": {
                    "requirements": current_requirements(),
                    "tasks": [],
                    "work_kind": "task_planning",
                },
                "repository_context": code_repository_context,
                "constraints": [
                    "任务按可执行编码边界拆分，并表达真正的硬依赖。",
                    "任务规划不编写正式代码，也不设计单元测试。",
                    "只写任务包指定的草稿和结果文件。",
                ],
            }
        else:
            task_plan = approved_artifact("task-plan")
            task = task_by_id(active_item)
            context = {
                "goal": f"为 {task['id']}（{task['title']}）生成可直接指导编码的任务规格。",
                "inputs": [
                    analysis["snapshot_path"],
                    design["snapshot_path"],
                    task_plan["snapshot_path"],
                ],
                "sources": [design["snapshot_path"], task_plan["snapshot_path"]],
                "depends_on": [f"task-plan@{task_plan['approved_revision']}"],
                "stage_guide": "references/stages/specification.md",
                "facts": {**task_facts(task), "work_kind": "task_specification"},
                "repository_context": code_repository_context,
                "constraints": [
                    "规格范围不得超出当前 active item。",
                    "说明做什么、怎么做和达到什么目标，但不编写正式代码。",
                    "不设计测试文件、测试用例、Mock 或断言。",
                    "只写任务包指定的草稿和结果文件。",
                ],
            }
    elif stage == "implementation":
        design = approved_artifact("design")
        specification = approved_artifact(f"{active_item}-spec")
        task = task_by_id(active_item)
        dependency_implementations = [
            approved_artifact(f"{dependency}-implementation")
            for dependency in task["depends_on"]
        ]
        context = {
            "goal": f"按照已批准规格实现 {task['id']}（{task['title']}），并记录实际验证结果。",
            "inputs": [
                specification["snapshot_path"],
                design["snapshot_path"],
                *[artifact["snapshot_path"] for artifact in dependency_implementations],
            ],
            "sources": [
                specification["snapshot_path"],
                design["snapshot_path"],
                *[artifact["snapshot_path"] for artifact in dependency_implementations],
            ],
            "depends_on": [
                f"{specification['id']}@{specification['approved_revision']}",
                *[
                    f"{artifact['id']}@{artifact['approved_revision']}"
                    for artifact in dependency_implementations
                ],
            ],
            "stage_guide": "references/stages/implementation.md",
            "facts": task_facts(task),
            "repository_context": repository_context(project["code_repository"]),
            "constraints": [
                "先检查当前代码已经满足哪些验收标准，只补齐剩余缺口。",
                "允许其他任务已经顺带实现当前任务的一部分或全部内容。",
                "只判断当前任务是否落地，不追踪代码归属。",
                "只写任务包指定的报告草稿、结果文件和必要代码。",
            ],
        }
    elif stage == "testing":
        specification = approved_artifact(f"{active_item}-spec")
        implementation = approved_artifact(f"{active_item}-implementation")
        task = task_by_id(active_item)
        context = {
            "goal": f"基于真实实现为 {task['id']}（{task['title']}）编写并执行单元测试。",
            "inputs": [specification["snapshot_path"], implementation["snapshot_path"]],
            "sources": [implementation["snapshot_path"], specification["snapshot_path"]],
            "depends_on": [f"{implementation['id']}@{implementation['approved_revision']}"],
            "stage_guide": "references/stages/testing.md",
            "facts": task_facts(task),
            "repository_context": repository_context(project["code_repository"]),
            "constraints": [
                "只承担单元测试设计、测试代码和执行，不重新实现业务功能。",
                "测试设计基于真实实现和仓库现有单元测试模式。",
                "单元测试可以完成、失败或跳过，不影响其他任务执行。",
                "只写任务包指定的报告草稿、结果文件和必要测试代码。",
            ],
        }
    else:
        raise ValueError(f"Unsupported semantic stage: {stage}")

    if instruction.strip():
        context["goal"] = (
            f"{context['goal']} 当前用户补充要求：{instruction.strip()}"
        )
    return context
