package app

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func installPartnerPermissionFixture(t *testing.T, a *App) {
	t.Helper()
	_, source, _, _ := runtime.Caller(0)
	raw, err := os.ReadFile(filepath.Join(filepath.Dir(source), "..", "..", "..", "src", "researchos", "workdesk", "partners", "permissions.json"))
	if err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(a.pythonSources(), "researchos", "workdesk", "partners", "permissions.json")
	if err = os.MkdirAll(filepath.Dir(target), 0700); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(target, raw, 0600); err != nil {
		t.Fatal(err)
	}
}

func TestPartnerPermissionFrozenDefaultOverrideAndFullConfirmation(t *testing.T) {
	a := partnerFixture(t)
	rows, hash, err := a.partnerPermissionCatalog()
	if err != nil || len(rows) != 8 || len(hash) != 64 {
		t.Fatalf("catalog: %v", err)
	}
	for _, row := range rows {
		permission, _, err := a.freezePartnerPermission(str(row["partner_id"]), map[string]any{}, map[string]any{})
		if err != nil || permission["value"] != "workspace" || permission["catalog_sha256"] != hash {
			t.Fatalf("safe default: %v", err)
		}
	}
	if _, _, err = a.freezePartnerPermission("grok", nil, map[string]any{"work_access": "auto"}); err == nil {
		t.Fatal("unsupported auto accepted")
	}
	if _, _, err = a.freezePartnerPermission("codex", nil, map[string]any{"work_access": "full"}); err == nil {
		t.Fatal("unconfirmed full access accepted")
	}
	permission, source, err := a.freezePartnerPermission("codex", nil, map[string]any{"work_access": "full", "confirm_full_access": true})
	if err != nil || source != "COMMAND" || permission["value"] != "full" {
		t.Fatalf("confirmed: %v", err)
	}
	path := filepath.Join(a.Profile.UserRoot, "config", "desktop", "preferences.json")
	if err = os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(path, []byte(`{"schema":1,"values":{"workAccess":{"codex":"auto"}}}`), 0600); err != nil {
		t.Fatal(err)
	}
	permission, source, err = a.freezePartnerPermission("codex", nil, nil)
	if err != nil || source != "DESKTOP_DEFAULT" || permission["value"] != "auto" {
		t.Fatalf("preference: %v", err)
	}
}
