// Package components prepares immutable official Agent releases. It owns only
// program files; accounts, sessions and active-library references belong to the
// application core and are never removed by this package.
package components

type Partner struct {
	ID             string   `json:"id"`
	Name           string   `json:"name"`
	Endpoint       string   `json:"endpoint"`
	AccountSupport string   `json:"account_support"`
	APISupport     string   `json:"api_support"`
	Sources        []string `json:"sources"`
	Package        string   `json:"package,omitempty"`
	Notes          string   `json:"notes,omitempty"`
}

func Catalog() []Partner {
	return []Partner{
		{ID: "codex", Name: "Codex", Endpoint: "Codex app-server", AccountSupport: "OFFICIAL_LOGIN", APISupport: "CUSTOM_PROVIDER", Package: "@openai/codex", Sources: []string{"https://github.com/openai/codex", "https://developers.openai.com/codex/auth/"}},
		{ID: "deepseek", Name: "DeepSeek", Endpoint: "DeepSeek Harness", AccountSupport: "OFFICIAL_LOGIN", APISupport: "OFFICIAL_API", Package: "@deepseek-ai/dsh", Sources: []string{"https://github.com/deepseek-ai/deepseek-harness", "https://deepseek-harness.github.io/deepseek-harness/en/guide/providers"}},
		{ID: "qwen", Name: "Qwen", Endpoint: "Qwen Code", AccountSupport: "OFFICIAL_LOGIN", APISupport: "CUSTOM_PROVIDER", Package: "@qwen-code/qwen-code", Sources: []string{"https://qwenlm.github.io/qwen-code-docs/en/users/quickstart/", "https://qwenlm.github.io/qwen-code-docs/en/users/configuration/auth/"}, Notes: "官网Quickstart提供chat.qwen.ai Plus/Pro账号路线；旧OAuth终止与当前官方账号登录分别记录。"},
		{ID: "claude", Name: "Claude", Endpoint: "Claude Code", AccountSupport: "OFFICIAL_LOGIN", APISupport: "ANTHROPIC_COMPATIBLE", Package: "@anthropic-ai/claude-code", Sources: []string{"https://code.claude.com/docs/en/authentication", "https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/"}},
		{ID: "grok", Name: "Grok", Endpoint: "Grok Build", AccountSupport: "OFFICIAL_LOGIN", APISupport: "CUSTOM_PROVIDER", Sources: []string{"https://github.com/xai-org/grok-build", "https://x.ai/cli/install.ps1"}},
		{ID: "gemini", Name: "Gemini", Endpoint: "Gemini CLI", AccountSupport: "OFFICIAL_LOGIN", APISupport: "LOCAL_PROTOCOL_ADAPTER", Package: "@google/gemini-cli", Sources: []string{"https://geminicli.com/docs/get-started/authentication/", "https://geminicli.com/docs/reference/configuration/"}, Notes: "DeepSeek 替换驾驶使用本地 Gemini 协议适配，兼容状态按实际组合记录。"},
		{ID: "kimi", Name: "Kimi", Endpoint: "Kimi Code CLI", AccountSupport: "OFFICIAL_LOGIN", APISupport: "CUSTOM_PROVIDER", Package: "@moonshot-ai/kimi-code", Sources: []string{"https://github.com/MoonshotAI/kimi-code", "https://moonshotai.github.io/kimi-code/en/guides/getting-started.html"}},
		{ID: "glm", Name: "GLM", Endpoint: "ZCode Agent", AccountSupport: "OFFICIAL_LOGIN", APISupport: "CUSTOM_PROVIDER", Sources: []string{"https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/README.md", "https://zcode.z.ai/cn/docs/configuration"}, Notes: "只准备官方 Agent 运行资源，不运行 ZCode 桌面或 Web 客户端。"},
	}
}

func partner(id string) (Partner, bool) {
	for _, p := range Catalog() {
		if p.ID == id {
			return p, true
		}
	}
	return Partner{}, false
}
