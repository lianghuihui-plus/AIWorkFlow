---
name: wf-init
description: 当用户要新建或初始化 AIWorkFlow 工作空间，并且已有 PRD 文件或目录，需要收集平台与代码仓库后创建新工作空间时使用。
---

# 初始化工作空间

在用户指定目录或当前目录创建 AIWorkFlow 工作空间。

## 收集输入

按需逐项收集，不重复询问用户已经提供的内容：

1. 工作空间目录；默认当前目录，目录必须已存在且为空。
2. 项目名称；默认使用工作空间目录名。
3. 开发平台；用户未提供时询问实际目标平台，不预设平台。
4. 一个或多个 PRD 文件或目录路径；必须提供。
5. 代码仓库目录；必须提供且当前可访问。

PRD 目录由工具扫描直接子文件，不递归扫描。不要手工复制 PRD、创建状态文件或推断 schema。

## 定位内核

解析当前 `SKILL.md` 所在目录的真实路径，再定位同级 `wf/tools/aiwf.py`：

```text
<wf-init-skill-dir>/../wf/tools/aiwf.py
```

如果文件不存在，停止并报告 AIWorkFlow 安装不完整。不要回退到其他 Skill、线上目录或复制的工具。

## 初始化

调用：

```text
python3 <aiwf.py> init \
  --workspace <workspace> \
  --name <project-name> \
  --platform <platform> \
  --prd <prd-path> \
  [--prd <another-prd-path>] \
  --code-repository <repository>
```

保留工具返回的错误原意，不删除、覆盖或迁移非空目录中的内容。初始化成功后报告项目、平台、
PRD 副本数量、代码仓库配置、工作空间绝对路径，并提示用户执行 `wf` 进入需求分析。
