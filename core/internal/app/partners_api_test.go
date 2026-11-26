package app

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"workdesk/core/internal/components"
	"workdesk/core/internal/storage"
)

// The fixture models catalog provenance and binding, not provider networking.
func installPartnerAPICatalogFixture(t *testing.T, a *App) {
	t.Helper()
	providers := []map[string]any{}
	for _, id := range []string{"openai", "deepseek", "qwen", "anthropic", "xai", "gemini", "kimi", "glm"} {
		model := id + "-known-model"
		if id == "deepseek" {
			model = "deepseek-flash"
		}
		compatibility := map[string]any{}
		for _, partner := range components.Catalog() {
			compatibility[partner.ID] = map[string]any{"state": "supported", "wire": "fixture", "validation": "LOCAL_PROTOCOL_ONLY"}
		}
		providers = append(providers, map[string]any{"id": id, "name": id, "base_url": "https://fixture.invalid/" + id, "default_model": model, "models": []any{map[string]any{"id": model, "name": model}}, "compatibility": compatibility, "sources": []any{}})
	}
	writePartnerAPICatalogFixture(t, a, providers)
}

func writePartnerAPICatalogFixture(t *testing.T, a *App, providers []map[string]any) {
	t.Helper()
	path := filepath.Join(a.pythonSources(), "researchos", "workdesk", "partners", "api_providers.json")
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(map[string]any{"schema": 1, "api_providers": providers})
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
}

func newGenericPartnerAPIAccount(t *testing.T, a *App, partner, provider string) string {
	t.Helper()
	value := partnerCommandReceipt(t, a, ID(), "account.create", map[string]any{"partner_id": partner, "kind": "api", "api_provider": provider, "label": provider + " credential"})
	return str(value["receipt"].(map[string]any)["account_id"])
}

func TestPartnerAPIProvidersReadFrozenCatalogAndPreserveLegacyRecords(t *testing.T) {
	a := partnerFixture(t)
	legacyID := newPartnerAccount(t, a, "codex", "deepseek_api", "existing key")
	legacy := readPartnerObject(t, a, "account", legacyID)
	delete(legacy, "api_provider")
	legacy["state"], legacy["secret_version"], legacy["key_ready"] = "VERIFIED", "original-key-version", true
	putPartnerFixture(t, a, "account", legacy)
	path := filepath.Join(a.Profile.ProgramRoot, "src", "researchos", "workdesk", "partners", "api_providers.json")
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(`{"schema":1,"api_providers":[]}`), 0600); err != nil {
		t.Fatal(err)
	}
	view, err := a.partnersView("/api/v2/partners")
	if err != nil {
		t.Fatal(err)
	}
	m := view.(map[string]any)
	if len(m["api_providers"].([]map[string]any)) != 8 || m["api_catalog_source"] != "FROZEN_IMPLEMENTATION" || str(m["api_catalog_sha256"]) == "" {
		t.Fatal("API catalog did not use the frozen implementation")
	}
	found := false
	for _, account := range m["accounts"].([]map[string]any) {
		if account["id"] == legacyID {
			found = account["api_provider"] == "deepseek" && account["state"] == "VERIFIED" && account["secret_version"] == "original-key-version"
		}
	}
	if !found {
		t.Fatal("legacy account lost its exact provider or verified key")
	}
	stored := readPartnerObject(t, a, "account", legacyID)
	if stored["api_provider"] != nil || stored["secret_version"] != "original-key-version" || stored["state"] != "VERIFIED" {
		t.Fatal("read decoration rewrote existing credentials")
	}
	for _, provider := range m["api_providers"].([]map[string]any) {
		id := newGenericPartnerAPIAccount(t, a, "codex", str(provider["id"]))
		if accountAPIProvider(readPartnerObject(t, a, "account", id)) != provider["id"] {
			t.Fatal("generic account lost provider identity")
		}
	}
	for _, payload := range []map[string]any{{"kind": "api"}, {"kind": "api", "api_provider": "unknown"}, {"kind": "deepseek_api", "api_provider": "openai"}} {
		payload["partner_id"], payload["label"] = "codex", "must reject"
		if _, err := a.partnerCommand(map[string]any{"id": ID(), "kind": "account.create", "payload": payload}); err == nil {
			t.Fatal("ambiguous or mismatched API account created")
		}
	}
}

func TestPartnerAPIActualCatalogUsesSingleFrozenSource(t *testing.T) {
	a := partnerFixture(t)
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "src", "researchos", "workdesk", "partners", "api_providers.json"))
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(a.pythonSources(), "researchos", "workdesk", "partners", "api_providers.json")
	if err = os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	providers, hash, err := a.partnerAPICatalog()
	if err != nil || len(providers) != 8 || hash == "" {
		t.Fatalf("actual frozen provider catalog rejected: %v", err)
	}
	supported, isolated := 0, 0
	for _, provider := range providers {
		for _, partner := range components.Catalog() {
			account := map[string]any{"kind": "api", "api_provider": provider["id"]}
			connection := map[string]any{"route": "api"}
			err := a.bindPartnerAPI(partner.ID, account, connection)
			matrix := provider["compatibility"].(map[string]any)[partner.ID].(map[string]any)
			if matrix["state"] == "supported" {
				supported++
				if err != nil || connection["model"] != provider["default_model"] || connection["api_catalog_sha256"] != hash {
					t.Fatalf("supported official pair did not freeze: %s/%s: %v", partner.ID, provider["id"], err)
				}
			} else {
				isolated++
				if err == nil || !strings.Contains(err.Error(), str(matrix["reason"])) {
					t.Fatal("official protocol isolation lost its stated reason")
				}
			}
		}
	}
	if supported+isolated != 64 || supported == 0 {
		t.Fatal("actual eight by eight catalog did not cover all pairs")
	}
}

func TestPartnerAPIConnectionFreezesExactProviderAndAllowsCustomModel(t *testing.T) {
	a := partnerFixture(t)
	accountID := newGenericPartnerAPIAccount(t, a, "codex", "openai")
	release := localPartnerRelease(t, a, "codex")
	payload := map[string]any{"partner_id": "codex", "route": "api", "release_id": release.ID, "account_id": accountID, "api_provider": "openai", "driver_provider": "openai"}
	value := partnerCommandReceipt(t, a, ID(), "connection.save", payload)
	id := str(value["receipt"].(map[string]any)["connection_id"])
	connection := readPartnerObject(t, a, "connection", id)
	if connection["model"] != "openai-known-model" || connection["api_provider"] != "openai" || connection["driver_provider"] != "openai" || connection["api_provider_snapshot"] == nil || str(connection["api_catalog_sha256"]) == "" {
		t.Fatal("connection did not freeze the selected API identity")
	}
	payload["model"] = "future-provider/model-v42:latest"
	partnerCommandReceipt(t, a, ID(), "connection.save", payload)
	for _, key := range []string{"api_provider", "driver_provider", "model"} {
		prior := payload[key]
		payload[key] = "deepseek"
		if key == "model" {
			payload[key] = "model with whitespace"
		}
		if _, err := a.partnerCommand(map[string]any{"id": ID(), "kind": "connection.save", "payload": payload}); err == nil {
			t.Fatalf("incorrect %s accepted", key)
		}
		payload[key] = prior
	}
	providers, _, _ := a.partnerAPICatalog()
	for _, provider := range providers {
		if provider["id"] == "openai" {
			provider["compatibility"].(map[string]any)["codex"] = map[string]any{"state": "isolated", "reason": "fixture exact official wire unavailable"}
		}
	}
	writePartnerAPICatalogFixture(t, a, providers)
	_, err := a.partnerCommand(map[string]any{"id": ID(), "kind": "connection.save", "payload": payload})
	if err == nil || !strings.Contains(err.Error(), "fixture exact official wire unavailable") {
		t.Fatal("isolated combination did not explain its exact limitation")
	}
}

func TestPartnerAPICredentialTargetsNeverCrossProvider(t *testing.T) {
	a := partnerFixture(t)
	selected := newGenericPartnerAPIAccount(t, a, "codex", "deepseek")
	legacy := newPartnerAccount(t, a, "kimi", "deepseek_api", "existing same provider")
	other := newGenericPartnerAPIAccount(t, a, "codex", "openai")
	if err := a.write(func(s *storage.Session) error {
		account, err := partnerObject(s, "account", selected)
		if err != nil {
			return err
		}
		targets, err := partnerAPICredentialTargets(s, account, true)
		if err != nil {
			return err
		}
		seen := map[string]bool{}
		for _, target := range targets {
			seen[str(target["id"])] = true
			if accountAPIProvider(target) != "deepseek" {
				t.Fatal("Key apply-all crossed provider boundary")
			}
		}
		if len(seen) != 2 || !seen[selected] || !seen[legacy] || seen[other] {
			t.Fatal("provider-specific credential targets changed identity")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	_, err := a.partnerCommand(map[string]any{"id": ID(), "kind": "account.set-key", "payload": map[string]any{"account_id": selected, "api_provider": "openai", "key": "NEVER-SENT"}})
	if err == nil {
		t.Fatal("wrong-provider Key reached secret storage")
	}
	putPartnerFixture(t, a, "run", map[string]any{"id": "active-same-provider", "account_id": legacy, "state": "ORPHANED_UNKNOWN"})
	_, err = a.partnerCommand(map[string]any{"id": ID(), "kind": "account.set-key", "payload": map[string]any{"account_id": selected, "api_provider": "deepseek", "apply_all_api": true, "key": "NEVER-SENT"}})
	if err == nil || !strings.Contains(err.Error(), "在途或未知运行") {
		t.Fatal("batch Key update bypassed same-provider active protection")
	}
}

func TestPartnerAPIOldResultCannotVerifyAnotherProvider(t *testing.T) {
	a, runID, accountID, _ := receivedPartnerFixture(t)
	account := readPartnerObject(t, a, "account", accountID)
	account["kind"], account["api_provider"] = "api", "openai"
	putPartnerFixture(t, a, "account", account)
	partnerCommandReceipt(t, a, ID(), "partner.reconcile", map[string]any{"run_id": runID, "resolution": "accept_received_result"})
	if current := readPartnerObject(t, a, "account", accountID); current["state"] == "VERIFIED" || current["verified_run"] != nil {
		t.Fatal("DeepSeek historical result verified another provider's Key")
	}
}

func TestPartnerAPIVerificationSummarySeparatesProviders(t *testing.T) {
	a := partnerFixture(t)
	providers, _, _ := a.partnerAPICatalog()
	connections := []map[string]any{}
	for _, partner := range components.Catalog() {
		connections = append(connections, map[string]any{"partner_id": partner.ID, "route": "api", "state": "VERIFIED", "driver_provider": "deepseek"})
	}
	connections = append(connections, map[string]any{"partner_id": "codex", "route": "api", "state": "VERIFIED", "api_provider": "openai", "driver_provider": "openai"})
	statuses := partnerAPIVerification(providers, []map[string]any{{"kind": "api", "api_provider": "openai", "key_ready": true}}, connections)
	if statuses["deepseek"].(map[string]any)["state"] != "VERIFIED" || statuses["openai"].(map[string]any)["state"] == "VERIFIED" || statuses["openai"].(map[string]any)["verified_partners"] != 1 {
		t.Fatal("provider verification mixed other credentials or model routes")
	}
	account := map[string]any{"id": "same-id", "kind": "api", "api_provider": "openai", "secret_version": "same-key-version"}
	changed := []map[string]any{{"account_id": "same-id", "route": "api", "state": "VERIFIED", "api_provider": "openai", "driver_provider": "openai", "verified_api_provider": "deepseek", "verified_secret_version": "same-key-version"}}
	decoratePartnerConnections(changed, []map[string]any{account})
	if changed[0]["state"] == "VERIFIED" {
		t.Fatal("read view let an old provider proof verify the current route")
	}
}
