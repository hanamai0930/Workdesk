package app

import "testing"

func TestConnectionSetupSharesVersionedAccountsWithoutWebsiteAuthInference(t *testing.T) {
	legacy := map[string]any{"ready": false, "work_accounts": map[string]any{"codex": false, "qwen": true}, "chat_accounts": map[string]any{"codex": false}, "connections": map[string]any{}, "api_connections": map[string]any{}}
	partners := []map[string]any{}
	for _, id := range []string{"codex", "deepseek", "qwen", "claude", "grok", "gemini", "kimi", "glm"} {
		partners = append(partners, map[string]any{"id": id})
	}
	snapshot := map[string]any{"partners": partners, "api_providers": []map[string]any{{"id": "deepseek"}, {"id": "openai"}}, "accounts": []map[string]any{
		{"id": "codex-auth", "partner_id": "codex", "kind": "official_login", "state": "AUTHENTICATED", "auth_state": "AUTHENTICATED"},
		{"id": "kimi-auth", "partner_id": "kimi", "kind": "official_login", "state": "AUTHENTICATED", "auth_state": "AUTHENTICATED"},
		{"id": "qwen-out", "partner_id": "qwen", "kind": "official_login", "state": "LOGIN_REQUIRED", "auth_state": "UNAUTHENTICATED"},
		{"id": "claude-api", "partner_id": "claude", "kind": "deepseek_api", "key_ready": true},
		{"id": "grok-api", "partner_id": "grok", "kind": "api", "api_provider": "openai", "key_ready": true},
	}, "connections": []map[string]any{
		{"partner_id": "claude", "route": "api", "account_id": "claude-api", "driver_provider": "deepseek", "state": "VERIFIED"},
		{"partner_id": "grok", "route": "api", "account_id": "grok-api", "api_provider": "openai", "state": "SAVED_UNVERIFIED"},
		{"partner_id": "glm", "route": "api", "account_id": "grok-api", "api_provider": "openai", "state": "VERIFIED"},
	}}
	value := mergeConnectionSetup(legacy, snapshot)
	work := value["work_accounts"].(map[string]bool)
	chat := value["chat_accounts"].(map[string]bool)
	api := value["api_connections"].(map[string]bool)
	if value["ready"] != true || len(work) != 8 || !work["codex"] || !work["kimi"] || work["qwen"] || chat["codex"] {
		t.Fatalf("launcher diverged from account facts: %+v", value)
	}
	if !api["deepseek"] || !api["openai"] || value["api_connections_by_partner"].(map[string]map[string]bool)["glm"] != nil {
		t.Fatal("API factory/partner binding was lost")
	}
}
