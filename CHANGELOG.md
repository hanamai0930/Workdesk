# Changelog

## [1.0.1] - 2026-11-23

通用兼容安装包通过[百度网盘统一入口](https://pan.baidu.com/s/1c6fh-cs804YLCSTst1_Kyg?pwd=g6hg)分发，提取码 **g6hg**，进入 **v1.0.1** 文件夹。GitHub 不新增本轮 EXE 附件，原 v1.0.0 附件保留。

- **合作条件等待**：聊天直接显示待处理条件和入口；支持外部资源、人工配合、后续阶段核对说明，以及原有 Skill/MCP 预设。
- **原任务接续**：逐项保存说明并明确确认后重新规划，保留原目标、成员、权限、累计上限、派发截止和原回复。旧等待记录、服务重启、重复提交与过期授权均有回归覆盖。
- **Windows 兼容**：安装目标扩展到 Win10 1809（17763）及以后 x64 和各版本 Win11 x64，含对应 LTSC 2019、2021；排除早期 Win10、32 位与 ARM。Win10 实机验收待确认。
- **分发**：完整安装包统一为 `Workdesk-Setup-Win10-Win11-x64.exe`；已安装 1.0.0 的用户可使用 `Workdesk-Repair-x64.exe` 单独修复合作条件等待。完整包已包含该修复。
- **文档与工具**：更新安装、兼容和条件处理说明，公开对应安装器与独立修复工具源码。

## [1.0.0] - 2026-11-23

首个公开发布版本，面向 Windows 11 x64。

- **Chat**：使用自己的 DeepSeek、Qwen API Key 对话，或打开厂商官方网页；支持附件、历史搜索和继续对话。
- **独立 Work**：连接 Codex、DeepSeek、Qwen 官方工作端，在指定项目中处理任务，查看回合、审批操作并保存成果。
- **合作 Work**：提供固定流程、负责人带队、主持人—专家三种方式，组织 PDCA 阶段记录与人工验收。
- **资料与项目**：整理文件、链接、页面和演示稿，关联项目记录与成果，支持资料库备份和恢复。
- **Windows 安装版**：发布 `Workdesk-Setup-x64.exe`，随附离线指南、第三方许可与对应源码材料；当前安装包未签名。
- **源码公开**：Workdesk 自有源码和文档采用 MIT 许可证。从源码运行时，按[开发说明](docs/DEVELOPMENT.md)准备所需运行组件。

下载与安装要求见[发行页](https://github.com/hanamai0930/Workdesk/releases/tag/v1.0.0)，操作方法见[用户指南](docs/USER_GUIDE.md)。

[1.0.1]: https://github.com/hanamai0930/Workdesk/releases/tag/v1.0.1
[1.0.0]: https://github.com/hanamai0930/Workdesk/releases/tag/v1.0.0
