# 第三方声明

`workdesk-ui/frontend/components/ui` 中源自 shadcn/ui 的组件保留原 [MIT 许可证](workdesk-ui/frontend/components/ui/LICENSE.md)。其余前端依赖由 `workdesk-ui/package.json` 与锁文件声明，安装时由包管理器取得。Python 依赖见 `pyproject.toml`，按各依赖项目的许可证使用。

Codex、DeepSeek 和 Qwen 是各自提供方的产品。Workdesk 源码包含连接这些服务或工作端的适配代码。官方工作端、账户工具、WebView2 和 PDF 渲染运行组件由各自提供方分发，适用其各自的许可与服务条款。

Go 核心的模块版本与校验见 `core/go.mod` 和 `core/go.sum`，包含 [modernc.org/sqlite](https://pkg.go.dev/modernc.org/sqlite) 与 [golang.org/x/sys](https://pkg.go.dev/golang.org/x/sys) 等依赖。依赖源码及其原始许可由 Go 模块工具取得；分发核心二进制时须随分发材料保留 Go 及所含依赖要求的版权和许可声明。
