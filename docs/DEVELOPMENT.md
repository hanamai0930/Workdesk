# 从源码运行 Workdesk

推荐在 Windows 11 x64 上使用 Python 3.12、Node.js 22 和 pnpm 11。Windows 10 1809 及以上 x64 也列入兼容目标，实机状态见[安装指南](INSTALL.md#兼容范围)。以下命令从项目根目录执行。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
pnpm --dir workdesk-ui install --frozen-lockfile
pnpm --dir workdesk-ui run build
Remove-Item Env:WORKDESK_DATA_DIR -ErrorAction SilentlyContinue
$env:WORKDESK_HOME = Join-Path (Get-Location) '.workdesk-dev'
.\.venv\Scripts\python.exe .\workdesk-ui\server.py --open
```

首次启动会打开本地界面。DeepSeek 和 Qwen 的普通 API 聊天使用你自己的 Key，可在连接页保存。上面的命令将开发资料写入 `.workdesk-dev`，账号引用、运行记录和对话分别保存在该目录下。外部项目文件留在你选择的文件夹。

## 可选的桌面与工作端组件

源码仓库只提供 Workdesk 的代码和锁定的前端依赖版本。浏览器界面可用于开发、准备资料和 API Chat。官网页面的桌面内嵌、官方工作端及额度查询需要另外准备组件。

| 组件 | 开发时如何准备 |
| --- | --- |
| Windows 桌面宿主 | 从 [Microsoft.Web.WebView2 1.0.4258.31](https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.4258.31) 提取 `lib/net462` 与 `runtimes/win-x64/native` 到 `runtime/webview2-sdk`，运行 `tools/build_desktop.ps1`，并确认 Windows 已有 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) |
| Codex | 使用自己安装并认证的 [Codex 官方工作端](https://github.com/openai/codex)。当前 Windows 发现规则见 `src/researchos/workdesk/paths.py` 的 `Locations.codex` |
| DeepSeek | `runtime/deepseek-venv` 与 `runtime/deepseek-legacy-venv` 分别使用 SDK `0.2.1rc2` 与 `0.1.5rc1`。Windows 的官方 Node CLI `@deepseek-ai/dsh@0.2.1-rc.2` 放在 `runtime/deepseek-cli`，启动适配见 `tools/build_deepseek_runtime.ps1` |
| Qwen | `@qwen-code/sdk@0.1.20` 安装到 `runtime/qwen`。独立订阅授权组件使用 SDK `0.1.35`，路径和回退规则见 `src/researchos/workdesk/setup_qwen.py` |
| 额外运行组件 | Work 使用 `runtime/node-v22.23.3-win-x64/node.exe`。场景和计划的 YAML、时区数据见 `capabilities.py` 与 `planning.py`。PDF 预览和额度工具见 `pdf_visual.py` 与 `quota_observations.py` |

场景导入和相关检查从应用自己的目录读取 YAML。将开发依赖安装到这个目录：

```powershell
.\.venv\Scripts\python.exe -m pip install --target .\runtime\python-site PyYAML==6.0.3 tzdata
```

运行组件需要与具体功能所用版本相符。首次源码预览的验证范围是前端构建、本地启动和隔离测试。完整的工作端部署包与 Windows 安装测试另行整理。

## 本地检查

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

仓库中的检查使用临时资料库与模拟的工作端或响应，不要求连接付费模型。源码入口、前端模块和持久化边界分别位于 `src/researchos/workdesk`、`workdesk-ui/frontend` 和 `workdesk-ui/static`。

合作等待条件的定向回归可用 `python -m unittest discover -s tests -p test_capability_conditions.py` 运行，覆盖原任务接续、三种合作模式、旧预设、授权到期和重复提交。安装与独立修复包的构建输入见 [安装器工具](../tools/windows-installer/README.md) 和 [修复程序工具](../tools/windows-repair/README.md)。
