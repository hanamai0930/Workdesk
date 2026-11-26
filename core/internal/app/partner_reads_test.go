package app

import (
	"fmt"
	"net/http/httptest"
	"strings"
	"testing"

	"workdesk/core/internal/storage"
)

func TestPartnerReadExactIDBeyondRecentList(t *testing.T) {
	a := partnerFixture(t)
	installPartnerRecordFixture(t, a)
	const old = "old-partner-run"
	if err := a.write(func(s *storage.Session) error {
		return putPartner(s, "run", map[string]any{"id": old, "mode": "run", "state": "COMPLETED", "project_id": "project-a", "prompt": "Create one probe", "connection_snapshot": map[string]any{"model": "deepseek-flash"}, "result": map[string]any{"text": "done", "usage": map[string]any{"totalTokens": 12}}})
	}); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		if err := a.partnerEvent(old, map[string]any{"type": "event", "kind": "model_request_intent"}); err != nil {
			t.Fatal(err)
		}
	}
	if err := a.write(func(s *storage.Session) error {
		for i := 0; i < 203; i++ {
			if err := putPartner(s, "run", map[string]any{"id": fmt.Sprintf("z-run-%03d", i), "mode": "run", "state": "COMPLETED", "prompt": "Other work"}); err != nil {
				return err
			}
		}
		return putPartner(s, "run", map[string]any{"id": "login-check", "mode": "inspect", "state": "COMPLETED"})
	}); err != nil {
		t.Fatal(err)
	}
	view, err := a.partnersView("/api/v2/partner-runs")
	if err != nil {
		t.Fatal(err)
	}
	for _, run := range view.(map[string]any)["runs"].([]map[string]any) {
		if run["id"] == old {
			t.Fatal("fixture must be beyond the bounded recent list")
		}
	}
	path := "/api/v2/partner-runs/" + old
	v, err := a.v2(nil, httptest.NewRequest("GET", path, nil), path)
	if err != nil {
		t.Fatal(err)
	}
	run := v.(map[string]any)["run"].(map[string]any)
	if run["id"] != old || run["project_id"] != "project-a" || number(run["model_request_intent_count"]) != 2 || run["output"] != "done" || run["usage"] == nil {
		t.Fatalf("exact read lost its binding: %+v", run)
	}
	seen := map[string]bool{}
	cursor := ""
	for page := 0; page < 10; page++ {
		v, err := a.records(cursor, 50)
		if err != nil {
			t.Fatal(err)
		}
		m := v.(map[string]any)
		for _, row := range m["records"].([]map[string]any) {
			if row["kind"] == "partner_run" {
				id := str(row["id"])
				if seen[id] {
					t.Fatal("duplicate record across cursor pages")
				}
				seen[id] = true
			}
		}
		if m["has_more"] != true {
			break
		}
		cursor = str(m["cursor"])
	}
	if len(seen) != 204 || !seen[old] || seen["login-check"] {
		t.Fatalf("unified records have wrong work coverage: %d", len(seen))
	}
	detail, err := a.record("partner_run/" + old)
	if err != nil {
		t.Fatal(err)
	}
	record := detail.(map[string]any)["record"].(map[string]any)
	if record["id"] != old || record["kind"] != "partner_run" || record["data"].(map[string]any)["project_id"] != "project-a" {
		t.Fatal("record copied or lost the original run")
	}
}

func TestPartnerUnifiedRecordsKeepSchema2Readable(t *testing.T) {
	a := toolFixture(t)
	installPartnerRecordFixture(t, a)
	if _, err := a.records("", 10); err != nil {
		t.Fatal(err)
	}
	if _, err := a.record("partner_run/missing"); err == nil {
		t.Fatal("schema 2 must not expose nonexistent partner data")
	}
}

func TestPartnerVerificationReadUsesCurrentSecret(t *testing.T) {
	connections := []map[string]any{{"id": "c", "account_id": "a", "route": "api", "state": "VERIFIED", "verified_secret_version": "old"}}
	decoratePartnerConnections(connections, []map[string]any{{"id": "a", "secret_version": "new", "state": "KEY_SAVED_UNVERIFIED"}})
	if connections[0]["state"] != "SAVED_UNVERIFIED" || connections[0]["verification_stale"] != true {
		t.Fatal("old Key result verified a new Key")
	}
}

func TestPartnerAdmissionRejectsChangedConnectionBeforeLaunch(t *testing.T) {
	a := partnerFixture(t)
	if err := a.write(func(s *storage.Session) error {
		return putPartner(s, "connection", map[string]any{"id": "selected", "state": "SAVED_UNVERIFIED"})
	}); err != nil {
		t.Fatal(err)
	}
	_, err := a.partnerCommand(map[string]any{"id": ID(), "kind": "partner.run", "payload": map[string]any{"connection_id": "selected", "connection_revision": 0, "prompt": "do not send"}})
	if err == nil || !strings.Contains(err.Error(), "连接配置已变化") {
		t.Fatalf("stale selection reached launch instead of rejection: %v", err)
	}
}

func TestPartnerLoginChoiceHasAnswerablePendingIdentity(t *testing.T) {
	a := partnerFixture(t)
	if err := a.write(func(s *storage.Session) error {
		return putPartner(s, "run", map[string]any{"id": "choice", "mode": "login", "state": "RUNNING"})
	}); err != nil {
		t.Fatal(err)
	}
	if err := a.partnerEvent("choice", map[string]any{"type": "login", "request_id": "auth-method", "instructions": "选择官方登录方式", "options": []any{map[string]any{"value": "oauth", "label": "官方账号"}}}); err != nil {
		t.Fatal(err)
	}
	run := readPartnerObject(t, a, "run", "choice")
	requests := run["pending_requests"].([]any)
	q := requests[0].(map[string]any)
	if q["id"] != "auth-method" || q["kind"] != "input" || q["options"] == nil || q["message"] != "选择官方登录方式" {
		t.Fatal("login choice lost its native identity or options")
	}
}

func TestPartnerPreviouslyVerifiedResultKeepsExactConfiguration(t *testing.T) {
	for _, changed := range []bool{false, true} {
		a, runID, _, connectionID := receivedPartnerFixture(t)
		connection := readPartnerObject(t, a, "connection", connectionID)
		connection["state"] = "VERIFIED"
		connection["verified_run"] = runID
		if changed {
			connection["model"] = "a-different-model"
		}
		putPartnerFixture(t, a, "connection", connection)
		partnerCommandReceipt(t, a, ID(), "partner.reconcile", map[string]any{"run_id": runID, "resolution": "accept_received_result"})
		current := readPartnerObject(t, a, "connection", connectionID)
		if changed && current["verified_secret_version"] != nil {
			t.Fatal("different saved combination was verified by old result")
		}
		if !changed && current["verified_secret_version"] != "saved-secret-version" {
			t.Fatal("unchanged originally verified run lost its credential proof")
		}
	}
}

func installPartnerRecordFixture(t *testing.T, a *App) {
	t.Helper()
	if err := a.write(func(s *storage.Session) error {
		for _, query := range []string{
			`CREATE TABLE IF NOT EXISTS collaboration(kind TEXT,id TEXT,data TEXT,updated REAL)`,
			`CREATE TABLE IF NOT EXISTS tasks(id TEXT,title TEXT,project_id TEXT,state TEXT,updated REAL)`,
			`CREATE TABLE IF NOT EXISTS work_items(id TEXT,title TEXT,project_id TEXT,state TEXT,revision INTEGER,updated REAL)`,
		} {
			if _, err := s.Execute(query, nil); err != nil {
				return err
			}
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}
