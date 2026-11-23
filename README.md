<p align="center">
  <img src="workdesk-ui/static/icon.svg" width="64" height="64" alt="Workdesk 图标">
</p>

<h1 align="center">Workdesk · 工作台</h1>

<p align="center"><strong>一个工作空间，连接不同的 AI 能力。</strong></p>

<p align="center">面向 Windows 的本地 AI 工作台，让聊天、项目执行、多模型协作与成果管理自然衔接。</p>

<p align="center">
  <a href="docs/INSTALL.md#兼容范围"><img src="https://img.shields.io/badge/Windows-10_1809%2B_%7C_11_x64-4F7DF3?style=flat-square" alt="Windows 10 1809+ / Windows 11 x64"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-16A085?style=flat-square" alt="MIT License"></a>
  <a href="#认识-workdesk"><img src="https://img.shields.io/badge/Local-AI_Workspace-8B7CF6?style=flat-square" alt="Local AI Workspace"></a>
</p>

<p align="center">
  <strong><a href="https://pan.baidu.com/s/1c6fh-cs804YLCSTst1_Kyg?pwd=g6hg">下载 Windows 安装版</a></strong> ·
  <a href="docs/INSTALL.md">安装指南</a> ·
  <a href="docs/INSTALL.md#常见问题">常见问题</a> ·
  <a href="#从源码开发">源码</a>
</p>

![Workdesk 整体工作台，展示项目对话与右侧文档工作区](docs/assets/hero-light.png)

截图来自实际应用界面，使用独立演示资料。对话正文和任务回执为操作示例，素材来源见[图片说明](docs/assets/README.md)。

## 认识 Workdesk

Workdesk 把 AI 聊天、项目任务、模型分工和资料整理放进同一个工作空间。从讨论目标、处理项目文件，到查看执行过程和验收成果，工作可以围绕同一个项目持续展开。

Workdesk 复用已有模型服务与官方工作端。模型推理和原生工具执行由所连接的服务及工作端负责，Workdesk 专注于项目组织、接口衔接、任务协调与成果验收。执行权限由你授权，关键操作可以审批，成果由你确认。

## 核心功能

### Chat · 日常对话与资料阅读

带着问题和材料开始讨论，在原对话里继续推进。

- 使用自己的 DeepSeek、Qwen API Key 聊天，或在桌面视图打开厂商官方网页。
- 添加附件、保留草稿，在原对话中追问，也可以搜索、整理和归档历史对话。

![Chat 对话界面，展示材料讨论、结构化回复与输入区](docs/assets/screenshots/02-chat.png)

### Independent Work · 独立完成项目任务

选择一个官方工作端，让它在指定项目中读写文件、运行工具并交回成果。

- 连接 Codex、DeepSeek Harness、Qwen Code，选择项目、型号和权限后开始工作。
- 查看回合、处理审批与任务接续，按工作端支持的能力使用计划模式、Skills 和 MCP。

![独立 Work 的项目任务与工作端回复](docs/assets/screenshots/03-independent-work.png)

### Collaboration · 多模型分工与合作流程

把复杂目标拆成有分工、有阶段、可检查的合作任务。

- 按目标选择合作方式，核对角色、型号、PDCA 轮数和本次授权。
- 跟踪执行纪要、阶段轨迹与用量，根据中间结果调整下一步并验收成果。

![合作 Work 的合作方式、角色分工与授权设置](docs/assets/screenshots/04-collaboration.png)

### Workspace · 项目资料、文档与成果

把工作需要的资料和工作产生的成果，放回可查找的项目与资料空间。

- 收录文件、链接、页面和知识库，在右栏编辑 Markdown、整理演示文稿并预览文件。
- 关联项目、记录和成果版本，保留验收决定，并通过备份恢复资料。

![资料空间中的页面、模板与参考资料](docs/assets/screenshots/06-workspace.png)

## 合作如何推进

合作方式决定谁来安排工作、谁来执行、怎样汇总。可以根据目标清晰程度和所需分工选择。

| 合作方式 | 适合的任务 | 推进方式 |
| --- | --- | --- |
| 固定流程 | 步骤清楚、可以重复的工作 | 按核对后的方案依次推进各阶段 |
| 负责人带队 | 需要统一安排和汇总的项目 | 先讨论需求，由负责人协调分工 |
| 主持人—专家 | 需要多个方向参与和判断的问题 | 先讨论需求，由主持人组织专家意见与后续工作 |

**Plan 规划 → Do 执行 → Check 检查 → Act 改进与整理交付**

开始前核对目标、分工和完成要求，执行中保留工作端回合与阶段记录；检查交付文件后，整理成果或继续改进。你保留授权、检查和最终验收权。

![任务详情中的执行记录、成果版本与验收入口](docs/assets/screenshots/05-task-review.png)

## 工作方式

Workdesk 组织对话和任务。Chat 通过模型服务返回回复，Work 通过官方工作端处理项目文件与工具，过程和成果回到工作台供你查看、审批与验收。

![Workdesk 协调层、模型服务与官方工作端，以及人工审批和验收的关系](docs/assets/workflow-overview.svg)

## 适用场景

| 场景 | 可以怎样用 |
| --- | --- |
| 个人开发与项目管理 | 讨论方案、修改代码、整理文档，把回合记录与项目成果放在一起 |
| 科研项目和实验工作 | 整理文献与实验材料、编写脚本、记录检查过程和待确认问题 |
| 多模型协作与复杂任务组织 | 根据角色安排不同工作端，跟踪阶段进度，汇总并验收交付 |

## 快速开始

### 使用 Windows 安装版

**1.0.1 通用兼容包**文件名为 **Workdesk-Setup-Win10-Win11-x64.exe**，面向 Windows 10 1809 及以上 x64 和 Windows 11 x64，已包含合作条件等待修复。安装后从开始菜单打开 Workdesk，连接自己的模型账号或 API Key。

打开[百度网盘统一下载入口](https://pan.baidu.com/s/1c6fh-cs804YLCSTst1_Kyg?pwd=g6hg)，提取码 **g6hg**，进入 **v1.0.1** 文件夹，下载上面的完整安装包。历史版本按版本号保存在同一根目录，以后继续使用这个入口。兼容范围和文件校验见[安装指南](docs/INSTALL.md)。

[安装与文件校验](docs/INSTALL.md) · [常见问题](docs/INSTALL.md#常见问题) · [操作指南](docs/USER_GUIDE.md)

### 从源码开发

可以[下载当前源码](https://github.com/hanamai0930/Workdesk/archive/refs/heads/main.zip)，或克隆本仓库。

准备 **Windows 11 x64、Python 3.12、Node.js 22 和 pnpm 11**。取得源码后，在项目根目录运行以下命令。命令与[开发说明](docs/DEVELOPMENT.md)一致。

<details>
<summary>展开源码运行命令</summary>

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
pnpm --dir workdesk-ui install --frozen-lockfile
pnpm --dir workdesk-ui run build
Remove-Item Env:WORKDESK_DATA_DIR -ErrorAction SilentlyContinue
$env:WORKDESK_HOME = Join-Path (Get-Location) '.workdesk-dev'
.\.venv\Scripts\python.exe .\workdesk-ui\server.py --open
```

</details>

首次启动打开本地界面，在连接页保存自己的 API Key。新建开发资料默认写入 `.workdesk-dev`，实际位置可在设置中核对，项目原件保存在你选择的文件夹。官网内嵌视图、官方工作端和额度查询所需组件，按[开发说明中的准备步骤](docs/DEVELOPMENT.md#可选的桌面与工作端组件)配置。

提交改动前的检查方法见[本地检查](docs/DEVELOPMENT.md#本地检查)，协作方式见[贡献说明](CONTRIBUTING.md)。

## 文档入口

| 文档 | 内容 |
| --- | --- |
| [安装指南](docs/INSTALL.md) | Windows 安装、运行库、文件校验和常见问题 |
| [用户指南](docs/USER_GUIDE.md) | 账号连接、Chat、独立与合作 Work、资料管理、备份恢复 |
| [开发说明](docs/DEVELOPMENT.md) | 源码运行、桌面与工作端组件、本地检查 |
| [贡献说明](CONTRIBUTING.md) | 提交问题、功能建议与 Pull Request |
| [版本记录](CHANGELOG.md) | 已发布版本的变化 |

## 项目状态

- 1.0.1 的兼容目标为 **Windows 10 1809+ x64 / Windows 11 x64**，已完成 Win11 本机安装检查；Win10 实机验收待确认。具体排除版本见[兼容范围](docs/INSTALL.md#兼容范围)。
- 实际模型和工具能力取决于连接的服务、组件版本及授权。
- 使用模型、官网与外部工具时，相关请求由选中的提供方处理，费用按提供方规则结算。

问题与功能建议通过 [GitHub Issues](https://github.com/hanamai0930/Workdesk/issues/new/choose)提交。提交前请移除密钥、账号信息与私有项目内容。

## 许可证

Workdesk 自有源码和文档采用 [MIT License](LICENSE)。第三方内容按各自许可使用，详见[第三方声明](THIRD_PARTY_NOTICES.md)。
