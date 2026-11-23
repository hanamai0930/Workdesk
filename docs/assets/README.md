# 仓库展示素材

本组素材用于 Workdesk 仓库首页与 GitHub Social Preview，采用浅色背景、蓝紫强调色和现有 Workdesk 图标。

## 图片来源

| 文件 | 尺寸 | 来源 |
| --- | --- | --- |
| `social-preview.png` | 1280 × 640 | 使用 `workdesk-ui/static/icon.svg` 与原创工作空间卡片构成的品牌封面。卡片表达讨论、执行、资料与验收的关系。 |
| `hero-light.png` | 1920 × 1200 | 将 `screenshots/01-overview.png` 放入品牌展示框，保持完整界面比例。 |
| `workflow-overview.svg` | 1200 × 900 | 原创流程示意，说明 Workdesk、模型服务、官方工作端和人工审批验收的关系。 |

品牌封面中的卡片和流程图属于示意素材。截图使用 Workdesk 1.0 的实际前端，在本地隔离的演示环境中拍摄。界面由应用自身渲染，使用通用项目、示例正文与合成任务回执。演示期间的真实模型请求、登录与额度查询均为 0。

## 真实界面截图

以下 6 张截图统一为 1600 × 900，16∶9，采用应用原有浅色主题。

| 文件 | 展示内容 |
| --- | --- |
| `screenshots/01-overview.png` | 工作台整体布局、项目对话与右侧工作区 |
| `screenshots/02-chat.png` | API Chat 的示例对话与输入区 |
| `screenshots/03-independent-work.png` | 独立 Work 的项目任务与示例回合 |
| `screenshots/04-collaboration.png` | 合作方式、角色模型与授权安排 |
| `screenshots/05-task-review.png` | 任务成果保存与人工验收入口 |
| `screenshots/06-workspace.png` | 资料空间中的页面、链接和文档资料 |

演示使用独立临时资料库。图中可见的“示例”项目、对话和回执用于说明操作。更新截图时保留演示标注，并检查画面中的账号、密钥和私人文件路径。

## 使用方式

README 通过相对路径引用这些图片。仓库维护者可在 GitHub 仓库设置的 Social Preview 中选择 `social-preview.png`。

### 设置 GitHub Social Preview

1. 保存本目录的 `social-preview.png`。
2. 打开 [Workdesk 仓库设置](https://github.com/hanamai0930/Workdesk/settings)。
3. 在 **Social preview** 中选择 **Edit → Upload an image…**，上传该文件。
4. 确认设置中显示新的封面预览。

该图片为 1280 × 640、不透明 PNG，小于 1 MB，可用于浅色和深色页面背景。GitHub 的上传要求与操作说明见[官方文档](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview)。
