# AIWorkFlow

AIWorkFlow 是面向 Agent 的工作空间式研发流程。它保留需求分析、技术设计、任务规格、代码实现和单元测试五个阶段，把状态、版本、审核、依赖、记忆和静态看板交给确定性 Python 内核管理，让 Agent 专注语义工作。

## 入口

| Skill | 职责 |
|---|---|
| `wf-init` | 在空目录创建工作空间并复制 PRD。 |
| `wf` | 推进阶段、处理审核与修订、记录决策和恢复事务。 |
| `wf-status` | 只读查看状态、待审核项、阻塞问题和健康检查。 |

三个 Skill 作为同一发行单元使用，共享 `wf/tools/aiwf.py` 和 `wf/tools/aiwf_core/`。

## 工作方式

```text
wf-init
  -> 需求分析 -> 人工审核
  -> 技术设计 -> 人工审核
  -> 任务规划 -> 人工审核
  -> 逐任务规格 -> 人工审核
  -> 代码实现 -> 人工审核
  -> 单元测试 -> 人工审核
  -> 完成
```

Agent 每次通过 `prepare` 获得自描述任务包，其中直接内嵌当前阶段指南和按传递上游筛选的相关记忆，只读取任务相关输入并写入工作区草稿和结果 seed。`result_seed` 是待填写起点，`result_schema` 是提交契约。`submit` 在事务中提升正式产物并保存不可变 revision 快照，用户通过 `review` 决定批准或要求修改。已批准产物通过 `revise` 创建新 revision，实际变化会使相关下游产物失效。健康检查继续验证 stale 历史文件的完整性，但需求覆盖和任务引用只校验当前有效产物图；全部推进门禁在内核独占锁中执行，批准操作校验批准后的目标投影，避免合法过渡态形成循环门禁。

阻塞语义工作的问题通过 `question` 一次性提交，用户回答由 `decide` 原样保存。新决定或审核通过的 revision 可以显式替代旧决定；完整历史保留在 `decisions.json`，正常 Agent 上下文只读取当前有效投影。全部问题回答后不会自动继续：`route-decision` 根据用户决定显式恢复当前 work，或归档当前 work 并创建受影响上游产物的 revision；问题 `impact` 是预估信息，实际路由仍严格限制在已批准传递上游。下游工作发现可由仓库证明的上游事实错误时，`route-upstream` 会归档当前 work 并创建最早错误产物的 revision；范围和架构变化仍走人工决策。工作空间发生中断时，`recover` 根据事务日志恢复。正式 Markdown 被工作流外修改时，用户可通过状态化 `resolve-drift` 采纳到 successor work 或恢复记录快照。新内核不读取或迁移旧格式工作空间；旧项目需要在空目录中重新初始化。

## 工作空间

```text
workspace/
├── .aiwf/
│   ├── project.json
│   ├── state.json
│   ├── requirements.json
│   ├── tasks.json
│   ├── artifacts.json
│   ├── decisions.json
│   ├── questions.json
│   ├── memory.json
│   ├── memory.md
│   ├── events.jsonl
│   ├── results/
│   ├── history/
│   ├── work/
│   └── transactions/
├── prd/
├── artifacts/
│   ├── analysis.md
│   ├── design.md
│   ├── task-plan.md
│   ├── specs/
│   ├── reports/
│   └── tests/
└── dashboard.html
```

结构化 JSON 是流程事实源，Markdown 保存用户可读语义，`memory.md` 和 `dashboard.html` 是可重新生成的只读投影。状态命令不会修复或渲染文件；状态返回 `can_advance` 和每个问题的 `recovery_action`，存在阻塞错误时正常推进命令会被拒绝。

## 代码结构

```text
wf-develop/
├── wf/SKILL.md
├── wf/references/stages/
├── wf/tools/aiwf.py
├── wf/tools/aiwf_core/
├── wf-init/SKILL.md
├── wf-status/SKILL.md
├── tests/
└── REFACTOR_TECHNICAL_PLAN.md
```

阶段参考只包含语义目标、建议关注点、最低交付内容和必须停止的情况。JSON Schema、ID、状态迁移、revision 和依赖规则全部由内核生成或校验，不复制到 Skill 指令。

初始化必须提供当前可访问的代码仓库目录；该目录就是业务作用域，即使它位于更大的 monorepo 中，也不会扩展到 Git 根目录。需求分析从混合平台 PRD 中筛选目标端实施点，只做范围级代码调查，并为 PRD、用户信息、仓库事实和推断分别登记可回读来源；纯其他端排除项不强求目标仓库证据。若审核后没有本端实施项，流程直接完成，不强迫 Agent 虚构后续工作。技术设计必须读取真实代码仓库，只负责模块、文件、类、职责和交互设计；`code_evidence` 只接受真实文件与符号，空仓库才允许 greenfield。任务规格阶段内部先批准 `task-plan`，再逐任务生成实现规格，两者都不设计单元测试。后续任务包只携带当前有效需求投影，不默认加载 withdrawn 历史。实现阶段只落生产代码并处理局部设计冲突；单元测试阶段基于真实实现编写和执行测试。Git 仓库在 work 开始时记录基线，阻塞前记录已归属变化，恢复时吸收不重叠的等待期外部变化；提交时核对 Agent 报告的文件清单与本 work 实际差异，拒绝作用域外变化和高置信度的生产/测试职责越界。非 Git 目录明确标记为有限校验。

当前工作空间 Schema 为 9，不提供旧 Schema 迁移或兼容入口。

## 验证

```bash
cd /Users/cm/GitProj/AIWorkflow/wf-develop
python3 -m unittest discover -s tests -p 'test_*.py'
python3 wf/tools/aiwf.py --version
python3 /Users/cm/.codex/skills/.system/skill-creator/scripts/quick_validate.py wf
python3 /Users/cm/.codex/skills/.system/skill-creator/scripts/quick_validate.py wf-init
python3 /Users/cm/.codex/skills/.system/skill-creator/scripts/quick_validate.py wf-status
```

当前目录是开发版本。线上目录 `wf-release` 必须保持不变，只有完成统一效果验收并得到明确发布授权后才能同步。
