# AIWorkFlow

AIWorkFlow 是面向 Agent 的工作空间式研发流程。它保留需求分析、技术设计、任务规格、代码实现和单元测试五个阶段，把产物、审核、真实任务依赖和静态看板交给确定性 Python 内核管理，让 Agent 专注需求和代码本身。

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
  -> 用户自由选择任务 A/B/C 编写规格和实现
  -> 仅在代码实现时检查声明的前置任务实现
  -> 单元测试按需执行，状态独立展示
  -> 所有任务实现完成
```

Agent 每次通过 `prepare` 获得只包含当前阶段、当前任务、关联需求、当前任务产物、声明依赖实现和相关决定的任务包。不同任务的草稿、审核和问题可以并存，不维护全局当前任务，也不存在唯一“下一步”门禁。`submit` 保存不可变 revision 快照，用户通过 `review` 批准或要求修改。

任务规划批准后，任务规格可任意顺序编写；代码实现只检查本任务规格和 `depends_on` 中前置任务的实现，不检查前置任务单元测试。实现前先核对当前代码，只补齐未满足的验收标准，允许零代码变化实现报告。实现报告必须逐项给出验收结论和证据。

需求和任务规划 revision 继续使用稳定 ID 与局部补丁。批准需求变更后，引擎只把直接引用变化需求的任务产物标记为 `needs_reconcile`，不递归传播；无关任务保持可执行。核对后无需修改时通过 `reconcile` 清除标记，不重新审核未变内容。

问题通过 `question` 归属具体 work，用户回答由 `decide` 原样保存、刷新该任务包的决定上下文并自动恢复原 work；同一 work 支持多轮提问，不需要决策路由。正式 Markdown 被工作流外修改时不形成漂移门禁，后续修订直接以当前正文作为草稿。损坏 work 会按事件记录定位到所属产物，只影响该产物；工作空间中断时只有原子写事务需要 `recover`。

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

结构化 JSON 是流程事实源，Markdown 保存用户可读语义，`dashboard.html` 是可重新生成的只读投影。`state.json` 只保存 schema 和更新时间，不保存当前任务、执行槽或项目操作模式。当前阶段、五阶段聚合进度、任务实现状态和测试状态都从产物、work 与任务关系实时计算。

## 代码结构

```text
wf-develop/
├── wf/SKILL.md
├── wf/references/stages/
├── wf/tools/aiwf.py
├── wf/tools/aiwf_core/
├── wf-init/SKILL.md
├── wf-status/SKILL.md
└── tests/
```

阶段参考只包含语义目标、建议关注点、最低交付内容和必须停止的情况。JSON Schema、ID、状态迁移、revision 和依赖规则全部由内核生成或校验，不复制到 Skill 指令。

初始化必须提供当前可访问的代码仓库目录。后续 `status` 只提示目录不可访问，不隐藏可选任务；Agent 在真正读取或修改代码时按需确认目录，产物 `submit` 不检查代码目录。工作流不读取 Git HEAD、分支、dirty files、提交或差异，也不通过文件类型限制实现和测试。若需求分析批准后没有本端实施项，流程直接完成。

当前工作空间 Schema 为 11，不读取或迁移 Schema 10 及更早工作空间。

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
