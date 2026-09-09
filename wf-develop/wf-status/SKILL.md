---
name: wf-status
description: 当用户只想只读查看 AIWorkFlow 工作空间的实现进度、单元测试状态、待审核产物、开放问题或下一步，并且不希望推进流程或写入文件时使用。
---

# 查看工作空间状态

只读取工作空间，不生成、修复、恢复或渲染文件。

## 定位内核

从当前 `SKILL.md` 定位：

```text
<wf-status-skill-dir>/../wf/tools/aiwf.py
```

文件不存在时报告安装不完整。

## 读取状态

```text
python3 <aiwf.py> status --workspace <workspace>
```

以 JSON 结果为事实源，简洁报告：

- 当前五阶段聚合进度；
- 各任务的需求实现状态和独立单元测试状态；
- 用户当前可选择的 `ready_work`、可选测试 `optional_work` 和仅作建议的 `recommended_work`；
- 只由前置任务实现未完成造成的依赖阻塞；
- 各任务独立草稿、待审核产物和开放问题；
- `needs_reconcile` 产物及其直接原因；
- 损坏 work 的所属任务和产物、工作空间问题，以及代码目录不可访问等非阻塞提示。

不要把代码目录状态提示、待审核、开放问题、测试未执行/失败、其他任务草稿、损坏的无关 work 或 Git 状态描述成项目级门禁，也不要因此隐藏仍可选择的任务。`implementation_complete=true` 表示需求实现已完成，不代表所有单元测试都通过。

状态为 `needs_recovery` 时说明存在未完成元数据事务，并建议使用 `wf` 恢复；本 Skill 不执行恢复。
