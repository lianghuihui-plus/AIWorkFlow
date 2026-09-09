---
name: wf
description: 当用户在 AIWorkFlow 工作空间中要求继续或推进流程、分析需求、审核或修改产物、执行指定任务、处理问题或需求变化时使用。
---

# 推进 AIWorkFlow

工作流负责准备任务上下文、保存产物、记录审核和展示进度。主要精力放在需求理解、设计、任务拆解、代码实现和单元测试，不维护全局当前任务、Git 归属或影响传播协议。

## 定位内核

从当前 `SKILL.md` 定位 `tools/aiwf.py`。文件不存在时报告安装不完整，不回退到其他目录。

## 读取状态

```text
python3 <aiwf.py> status --workspace <workspace>
```

`needs_recovery` 时调用 `recover` 后重读。其他问题只按所属任务或产物处理，不因为无关任务待审核、待决定、测试失败或存在草稿而停止。

损坏的 work 会在 `issues` 中标明所属任务和产物。只暂停该产物并报告修复需求，用户仍可选择其他任务；不要为同一产物重复创建 work。

## 选择工作

用户指定任务时，根据 `task_progress` 的 ID、标题和上下文匹配，并在内部调用：

```text
python3 <aiwf.py> prepare --workspace <workspace> --task-id <T-id> [--instruction <用户补充>]
```

不要要求用户输入命令或切换“当前任务”。用户未指定任务时可使用 `recommended_work`。推荐只是默认方向，不限制选择。

- 任务规格不受其他任务限制。
- 代码实现只要求当前任务规格已批准，且 `depends_on` 中前置任务的实现已批准并且不处于 `needs_reconcile`。
- 前置任务单元测试未执行、失败或跳过都不阻塞。
- 单元测试是可选环节，不阻塞任务或项目实现完成。
- 一个任务的草稿、审核或问题不阻塞其他任务。

`prepare` 返回已有同产物草稿时继续该草稿；不同任务的草稿可以并存。完整阅读任务包的 `stage_guide.instructions`、`facts`、`inputs` 和相关决定，只写 `draft_output`、`result_output` 以及实现所需代码。

## 实现原则

执行代码任务时先检查当前代码已经满足哪些验收标准，只补齐缺口。其他任务顺带完成的代码直接复用；若当前任务已经全部实现，允许零代码变化报告。

代码目录不可访问是状态提示，不是全局流程门禁。只有当前工作确实需要读取或修改代码时才停止并说明；`submit` 只注册已经完成的产物，不额外检查代码目录。

任务规格在 `acceptance_criteria` 中保存验收标准。实现结果必须逐项对应这些标准，记录 `summary`、验收结论、证据和至少一项实际验证，全部通过后才能提交。`changed_files` 与 `test_files` 仅作报告，不与 Git 状态比较，也不限制生产代码和测试代码的修改时机。

## 提交与审核

```text
python3 <aiwf.py> submit --workspace <workspace> --work-id <W-id>
python3 <aiwf.py> review --workspace <workspace> --artifact-id <id> --revision <n> --outcome approved
python3 <aiwf.py> review --workspace <workspace> --artifact-id <id> --revision <n> --outcome changes_requested --feedback <反馈>
```

提交后报告具体产物并等待用户审核，不自行批准。审核只改变该产物，不阻塞其他任务。

修改已批准产物时调用 `revise`。人工直接修改 Markdown 视为该产物的新草稿来源；批准前仍使用上一个已批准结构化结果，不触发漂移门禁。

## 需求变化

需求和任务使用稳定 ID。批准修订后，引擎只把直接关联任务的已有产物标记为 `needs_reconcile`，不递归传播，也不影响无关任务。

处理待核对产物时：

- 确需修改：用 `revise` 只更新必要内容。
- 当前内容仍满足最新需求：记录核对结论并清除标记。

```text
python3 <aiwf.py> reconcile --workspace <workspace> --artifact-id <id> --revision <n> --note <核对结论>
```

## 问题与决定

只有当前工作缺少关键业务答案时提交问题。问题只需 `question`、`reason` 和 `recommendation`：

```text
python3 <aiwf.py> question --workspace <workspace> --work-id <W-id> --items-json <json-array>
python3 <aiwf.py> decide --workspace <workspace> --question-id <Q-id> --decision <用户原话>
```

回答后原工作自动恢复，不需要决策路由。开放问题只暂停所属工作，用户仍可执行其他任务。
同一 work 后续仍可按实际需要继续提问；每次决定都会进入该 work 的 `decision_context`。

## 恢复

只有未完成的原子写事务需要全局恢复：

```text
python3 <aiwf.py> recover --workspace <workspace>
```
