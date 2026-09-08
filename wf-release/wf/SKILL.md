---
name: wf
description: 当用户在 AIWorkFlow 工作空间中要求继续或推进流程、分析需求、审核或修改产物、恢复事务、处理产物漂移、阻塞问题与决策时使用。
---

# 推进 AIWorkFlow

以工作空间数据和任务包为事实源推进当前工作，不手工维护状态、编号、版本、依赖或记忆。

## 定位内核

解析当前 `SKILL.md` 的真实路径并定位：

```text
<wf-skill-dir>/tools/aiwf.py
```

文件不存在时停止并报告安装不完整，不使用线上目录或旧工具作为回退。

## 先读状态

使用用户指定目录或当前目录调用：

```text
python3 <aiwf.py> status --workspace <workspace>
```

- `needs_recovery`：调用 `recover`，重新读取状态后继续原请求。
- `review`：只处理用户对待审核 revision 的批准或修改意见。
- `blocked`：只记录用户对开放问题的决定。
- `decision`：根据已记录决定，明确继续当前工作或修订受影响的上游产物。
- `working`：恢复同一个任务包。
- `ready`：准备当前阶段任务。

状态返回 `not_initialized` 时停止。AIWorkFlow 不兼容旧格式工作空间；提示用户新建空目录并通过 `wf-init` 初始化，不读取或转换旧版状态文件。

`can_advance=false` 时停止正常推进，按照每项 issue 的 `recovery_action`、`allowed_outcomes` 和产物状态处理。不能执行的外部恢复动作或 `manual_repair_required` 直接报告用户，不绕过门禁。需要修订已批准产物但用户尚未授权修改时，先报告目标和原因并等待确认。可恢复的 `artifact_drift` 需要用户明确选择采纳还是放弃工作流外的正文修改，确认后调用：

```text
python3 <aiwf.py> resolve-drift --workspace <workspace> --artifact-id <id> --revision <n> --outcome adopt --feedback <user-intent>
python3 <aiwf.py> resolve-drift --workspace <workspace> --artifact-id <id> --revision <n> --outcome discard
```

只有工具报告采纳会替换未完成 work，并且用户明确确认放弃该 work 时，才能追加 `--supersede-active-work`。结构化结果、work 或不可恢复的历史快照漂移直接报告，不手工猜测修复内容。

## 准备和执行

对继续、下一步或分析需求调用：

```text
python3 <aiwf.py> prepare --workspace <workspace> [--task-id <T-id>] [--instruction <current-user-instruction>]
```

开始语义工作前，完整阅读并遵循任务包中的 `stage_guide.instructions`，再读取 `memory_context.content`、`inputs`、`facts` 和必要 `sources`。任务包已经内嵌当前阶段指南和本任务相关记忆，不要另行读取其他阶段指南或整个 `requirements.json`、`decisions.json`、`memory.md`、`history`、`events`。`target_platform` 始终给出目标平台，`facts` 始终存在，`facts.requirements` 在后续阶段表示当前有效需求投影。工具已经在 `result_output` 写入 `result_seed` 作为待填写起点；完成后的内容必须符合 `result_schema`，不阅读内核代码推断格式。遵循任务包目标与边界，自主分析需求；只写 `draft_output` 和 `result_output`，不要直接修改正式产物或其他 `.aiwf` 数据。
任务阶段默认由引擎选择下一个可处理任务；只有用户明确指定任务时才传 `--task-id`。

判断信息时区分来源：目标行为按“最新用户确认 > PRD > Agent 推断”，代码现状按“仓库证据 > PRD 或口述”，平台能力按“官方文档或实际验证 > 推断”。需求未规定的局部实现细节由 Agent 自主决定；长期保留时写为 `engineering_default`，同时给出理由和验证点，不伪装成已确认业务事实。

最新用户反馈或当前产物明确替代 `memory_context.content` 中的旧决策时，在结果的 `superseded_decisions` 登记对应 `D-id`；只有产物审核通过后旧决策才会退出当前记忆。不要为了缩短上下文而失效仍然有效的决定。

`memory_delta` 只记录确实需要跨阶段复用的信息：`repository_fact` 必须带文件与符号证据，`architecture_decision` 必须带理由，`engineering_default` 必须带理由和验证点，`validation_item` 必须说明验证方式。短期思考过程和产物正文摘要不要写入长期记忆。

执行中若仓库证据证明已批准上游产物包含客观事实错误，选择最早出现错误的已批准上游 revision，并调用：

```text
python3 <aiwf.py> route-upstream --workspace <workspace> --work-id <work-id> --artifact-id <id> --revision <n> --correction <factual-correction> --evidence-json <[{"path":"...","symbol":"..."}]>
```

该命令只用于可由代码仓库验证的事实纠正，会归档当前 work 并创建上游 revision work。需求范围、目标行为、架构取舍或公开承诺发生变化时不得使用；按“阻塞问题与决定”记录问题，由用户决定后走 `route-decision`。

完成后调用：

```text
python3 <aiwf.py> submit --workspace <workspace> --work-id <work-id>
```

报告产物与 revision，等待用户审核，不自行批准。

## 审核与修改

用户明确批准唯一待审核产物时调用：

```text
python3 <aiwf.py> review --workspace <workspace> --artifact-id <id> --revision <n> --outcome approved
```

用户要求修改时保留反馈原文并调用：

```text
python3 <aiwf.py> review --workspace <workspace> --artifact-id <id> --revision <n> --outcome changes_requested --feedback <feedback>
```

修改请求会创建带原草稿和反馈的新任务包；继续完成该任务包并再次提交。

用户要求修改已批准产物时调用：

```text
python3 <aiwf.py> revise --workspace <workspace> --artifact-id <id> --revision <n> --feedback <feedback>
```

存在其他未完成 work 时不得自行覆盖。向用户说明冲突；只有用户明确确认放弃当前 work 后，才追加 `--supersede-active-work`，由引擎归档原草稿。

## 阻塞问题与决定

阶段指南判定必须停止时，一次提交本轮全部阻塞问题。每项包含 `question`、`reason`、`recommendation` 和提问时预估的受影响阶段 `impact`：

```text
python3 <aiwf.py> question --workspace <workspace> --work-id <work-id> --items-json <json-array>
```

用户回答后逐项原样记录：

```text
python3 <aiwf.py> decide --workspace <workspace> --question-id <Q-id> --decision <user-decision>
```

不要替用户补全尚未回答的选择。全部问题解决后，状态进入 `decision`。如果决定只是澄清当前工作，调用：

```text
python3 <aiwf.py> route-decision --workspace <workspace> --work-id <work-id> --outcome resume
```

如果决定改变了已批准的上游需求、设计或规格，选择真正需要修改的最上游产物并调用：

```text
python3 <aiwf.py> route-decision --workspace <workspace> --work-id <work-id> --outcome revise --artifact-id <id> --revision <n>
```

修订目标必须是任务包的已批准上游依赖。`impact` 是提问时的预估，用户回答扩大影响时选择实际需要修改的最上游产物；只有用户决定明确要求改变上游时才选择 `revise`。引擎会归档当前 work、创建 revision work，并记录预估范围与实际路由。

## 恢复

状态返回 `needs_recovery` 时调用：

```text
python3 <aiwf.py> recover --workspace <workspace>
```
