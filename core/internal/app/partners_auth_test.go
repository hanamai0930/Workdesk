package app

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"workdesk/core/internal/storage"
)

type loginInputBuffer struct{ bytes.Buffer }

func (*loginInputBuffer) Close() error { return nil }

func officialLoginFixture(t *testing.T, state string) (*App, string, map[string]any) {
	t.Helper()
	a := partnerFixture(t)
	accountID := newPartnerAccount(t, a, "codex", "official_login", "Official account")
	release := localPartnerRelease(t, a, "codex")
	run := map[string]any{"id": ID(), "account_id": accountID, "partner_id": "codex", "release_id": release.ID, "mode": "login", "state": state, "process_instance": a.Process, "created": now()}
	putPartnerFixture(t, a, "run", run)
	return a, accountID, run
}

func TestPartnerInspectReusesExactLoginAttempt(t *testing.T) {
	for _, state := range []string{"QUEUED", "WAITING_INPUT", "STOP_REQUESTED"} {
		t.Run(state, func(t *testing.T) {
			a, accountID, run := officialLoginFixture(t, state)
			if state != "QUEUED" {
				a.partnerProcesses[str(run["id"])] = &partnerProcess{cancel: func() {}}
			}
			value := partnerCommandReceipt(t, a, ID(), "account.inspect", map[string]any{"account_id": accountID, "release_id": run["release_id"]})
			r := value["receipt"].(map[string]any)
			if r["run_id"] != run["id"] || r["state"] != state || r["reused_existing_login"] != true {
				t.Fatalf("inspect changed original attempt: %+v", r)
			}
			runs, _ := a.partnerObjects("run")
			if len(runs) != 1 {
				t.Fatal("inspect dispatched another login process")
			}
			other := localPartnerRelease(t, a, "claude")
			_, err := a.partnerCommand(map[string]any{"id": ID(), "kind": "account.inspect", "payload": map[string]any{"account_id": accountID, "release_id": other.ID}})
			if err == nil {
				t.Fatal("inspect accepted another partner release")
			}
			_, err = a.partnerCommand(map[string]any{"id": ID(), "kind": "account.login", "payload": map[string]any{"account_id": accountID, "release_id": run["release_id"], "auth_options": map[string]any{"auth_method": "chatgptDeviceCode"}}})
			if err == nil || readPartnerObject(t, a, "account", accountID)["auth_options"] != nil {
				t.Fatal("changing options bypassed an active attempt")
			}
		})
	}
}

func TestPartnerAuthUnknownPreservesConfirmedFact(t *testing.T) {
	a, accountID, run := officialLoginFixture(t, "RUNNING")
	rid := str(run["id"])
	confirmed := map[string]any{"type": "auth_status", "auth_state": "AUTHENTICATED", "authenticated": true, "verification": "account/read", "fact_scope": "OFFICIAL_ACCOUNT_RECEIPT", "run_id": rid, "partner_id": "codex"}
	if err := a.partnerEvent(rid, confirmed); err != nil {
		t.Fatal(err)
	}
	for _, event := range []map[string]any{
		{"type": "result", "authenticated": false, "verification": "OFFICIAL_INITIALIZE_ONLY"},
		{"type": "error", "error": "status unavailable"},
		{"type": "auth_status", "auth_state": "UNAUTHENTICATED", "authenticated": false, "fact_scope": "LOCAL_CREDENTIAL_PRESENCE", "run_id": rid, "partner_id": "codex"},
		{"type": "auth_status", "auth_state": "UNAUTHENTICATED", "authenticated": false, "fact_scope": "OFFICIAL_ACCOUNT_RECEIPT", "run_id": "wrong-run", "partner_id": "codex"},
		{"type": "auth_status", "auth_state": "UNAUTHENTICATED", "authenticated": false, "fact_scope": "OFFICIAL_ACCOUNT_RECEIPT", "run_id": rid, "partner_id": "claude"},
	} {
		if err := a.partnerEvent(rid, event); err != nil {
			t.Fatal(err)
		}
		account := readPartnerObject(t, a, "account", accountID)
		if account["state"] != "AUTHENTICATED" || account["authenticated_run"] != rid || account["auth_state"] != "AUTHENTICATED" {
			t.Fatalf("unknown observation erased official login: %+v", account)
		}
	}
	negative := map[string]any{"type": "auth_status", "auth_state": "UNAUTHENTICATED", "authenticated": false, "verification": "account/read", "fact_scope": "OFFICIAL_ACCOUNT_RECEIPT", "run_id": rid, "partner_id": "codex"}
	if err := a.partnerEvent(rid, negative); err != nil {
		t.Fatal(err)
	}
	account := readPartnerObject(t, a, "account", accountID)
	if account["state"] != "LOGIN_REQUIRED" || account["auth_state"] != "UNAUTHENTICATED" {
		t.Fatal("official negative account receipt was ignored")
	}
}

func TestPartnerLoginProgressPreservesStopAndAuthentication(t *testing.T) {
	a, accountID, run := officialLoginFixture(t, "QUEUED")
	rid := str(run["id"])
	if readPartnerObject(t, a, "account", accountID)["login_state"] != "QUEUED" {
		t.Fatal("queued login was not bound")
	}
	if err := a.partnerEvent(rid, map[string]any{"type": "login", "login_url": "https://example.invalid/login"}); err != nil {
		t.Fatal(err)
	}
	if readPartnerObject(t, a, "account", accountID)["login_state"] != "WAITING_INPUT" {
		t.Fatal("waiting progress lost")
	}
	run = readPartnerObject(t, a, "run", rid)
	run["state"], run["stop_requested"] = "STOP_REQUESTED", true
	putPartnerFixture(t, a, "run", run)
	if err := a.partnerEvent(rid, map[string]any{"type": "login", "login_url": "https://example.invalid/stale"}); err != nil {
		t.Fatal(err)
	}
	if readPartnerObject(t, a, "run", rid)["state"] != "STOP_REQUESTED" {
		t.Fatal("late login erased stop intent")
	}
	if err := a.partnerEvent(rid, map[string]any{"type": "auth_status", "auth_state": "AUTHENTICATED", "authenticated": true, "fact_scope": "OFFICIAL_ACCOUNT_RECEIPT", "run_id": rid, "partner_id": "codex"}); err != nil {
		t.Fatal(err)
	}
	run = readPartnerObject(t, a, "run", rid)
	if run["state"] != "STOP_REQUESTED" {
		t.Fatal("late authentication reopened stopped attempt")
	}
	run["state"], run["local_exit_confirmed"] = "STOPPED_RUNTIME", true
	putPartnerFixture(t, a, "run", run)
	account := readPartnerObject(t, a, "account", accountID)
	if account["login_state"] != "STOPPED_RUNTIME" || account["state"] != "AUTHENTICATED" {
		t.Fatal("stopping the local login revoked a confirmed official login")
	}
	b, bid, queued := officialLoginFixture(t, "QUEUED")
	partnerCommandReceipt(t, b, ID(), "partner.stop", map[string]any{"run_id": queued["id"]})
	if readPartnerObject(t, b, "account", bid)["login_state"] != "CANCELLED" {
		t.Fatal("queued stop did not close login progress")
	}
}

func TestPartnerLoginInputDoesNotPersistOrReplaySecret(t *testing.T) {
	a, _, run := officialLoginFixture(t, "WAITING_INPUT")
	rid, requestID := str(run["id"]), "login-code-exact"
	secret := "private-one-time-code-51d9"
	if err := a.partnerEvent(rid, map[string]any{"type": "input", "request_id": requestID, "sensitive": true, "input_type": "password", "message": "请输入官方回填码"}); err != nil {
		t.Fatal(err)
	}
	buffer := &loginInputBuffer{}
	proc := &partnerProcess{input: buffer, cancel: func() {}}
	a.partnerProcesses[rid] = proc
	for _, kind := range []string{"partner.input", "account.login-input"} {
		payload := map[string]any{"run_id": rid, "request_id": requestID, "text": secret}
		if kind == "account.login-input" {
			payload["request_id"] = "wrong-request"
		}
		if _, err := a.partnerCommand(map[string]any{"id": ID(), "kind": kind, "payload": payload}); err == nil {
			t.Fatal("unmatched or ordinary sensitive input accepted")
		}
	}
	commandID := ID()
	payload := map[string]any{"run_id": rid, "request_id": requestID, "text": secret}
	partnerCommandReceipt(t, a, commandID, "account.login-input", payload)
	var frame map[string]any
	if err := json.Unmarshal(bytes.TrimSpace(buffer.Bytes()), &frame); err != nil {
		t.Fatal(err)
	}
	if frame["action"] != "login-input" || frame["text"] != secret || frame["request_id"] != requestID {
		t.Fatal("input was not sent to the original request")
	}
	sent := buffer.Len()
	payload["text"] = "different-secret-must-not-resend"
	if result := partnerCommandReceipt(t, a, commandID, "account.login-input", payload); result["replayed"] != true || buffer.Len() != sent {
		t.Fatal("unknown sensitive command was resent")
	}
	if got := proc.redact("echo " + secret); strings.Contains(got, secret) {
		t.Fatal("process stdout redaction missed login secret")
	}
	diagnostic := &partnerDiagnostic{a: a, id: rid, process: proc}
	_, _ = diagnostic.Write([]byte("stderr echo " + secret))
	if err := a.write(func(s *storage.Session) error {
		for _, table := range []string{"commands", "events", "core_partner_events", "core_partner_objects"} {
			rows, err := s.Execute("SELECT * FROM "+table, nil)
			if err != nil {
				return err
			}
			if strings.Contains(fmt.Sprint(rows.Rows), secret) {
				return fmt.Errorf("login secret persisted in %s", table)
			}
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	rows, _ := a.query(`SELECT payload_hash FROM commands WHERE id=?`, commandID)
	canonical, _ := json.Marshal(map[string]any{"run_id": rid, "request_id": requestID, "sensitive_input_omitted": true})
	hash := sha256.Sum256(canonical)
	if rows[0]["payload_hash"] != hex.EncodeToString(hash[:]) {
		t.Fatal("command fingerprint retained a secret-derived digest")
	}
	escaped := "private-code-with-quote-\"-and-backslash-\\"
	proc.secrets = append(proc.secrets, escaped)
	wire, _ := json.Marshal(map[string]any{"text": "echo " + escaped})
	var echoed map[string]any
	_ = json.Unmarshal(wire, &echoed)
	clean := proc.redactValue(echoed).(map[string]any)
	if strings.Contains(str(clean["text"]), escaped) || clean["text"] != "echo [redacted]" {
		t.Fatal("JSON-escaped login input leaked through output redaction")
	}
}

func TestPartnerAuthOptionsUseOfficialPartnerWhitelist(t *testing.T) {
	a := partnerFixture(t)
	for _, test := range []struct{ partner, key, value string }{
		{"codex", "auth_method", "chatgptDeviceCode"}, {"qwen", "auth_method", "qwen-oauth"}, {"gemini", "auth_method", "oauth-personal"}, {"claude", "auth_method", "claudeai"}, {"grok", "auth_method", "device_auth"}, {"kimi", "region", "mainland-cn"}, {"glm", "provider", "zai"},
	} {
		result := partnerCommandReceipt(t, a, ID(), "account.create", map[string]any{"partner_id": test.partner, "kind": "official_login", "label": test.partner, "auth_options": map[string]any{test.key: test.value}})
		id := str(result["receipt"].(map[string]any)["account_id"])
		if readPartnerObject(t, a, "account", id)["auth_options"].(map[string]any)[test.key] != test.value {
			t.Fatal("official option was not saved")
		}
	}
	for _, value := range []map[string]any{{"region": "global"}, {"auth_method": "unreviewed"}, {"api_key": "never-save"}} {
		if _, err := partnerAuthOptions("codex", value); err == nil {
			t.Fatal("unreviewed partner option accepted")
		}
	}
}

func TestPartnerLoginLinksBindOnlyLatestExactAttempt(t *testing.T) {
	a, accountID, latest := officialLoginFixture(t, "WAITING_INPUT")
	latest["login_url"] = "https://example.invalid/latest"
	putPartnerFixture(t, a, "run", latest)
	old := map[string]any{"id": "old-login", "account_id": accountID, "partner_id": "codex", "release_id": latest["release_id"], "mode": "login", "state": "WAITING_INPUT", "login_url": "https://example.invalid/old"}
	accounts := []map[string]any{readPartnerObject(t, a, "account", accountID)}
	if err := a.decoratePartnerAccounts(accounts, []map[string]any{latest, old}); err != nil {
		t.Fatal(err)
	}
	if accounts[0]["login_url"] != latest["login_url"] {
		t.Fatal("older login overwrote latest link")
	}
	latest["state"] = "STOPPED_RUNTIME"
	if err := a.decoratePartnerAccounts(accounts, []map[string]any{old, latest}); err != nil {
		t.Fatal(err)
	}
	if accounts[0]["login_url"] != nil {
		t.Fatal("closed login link was still offered")
	}
}

func TestPartnerLoginSiteChangeKeepsHistoricalProofSeparate(t *testing.T) {
	for _, test := range []struct {
		partner, key, before, after string
		changed                     bool
	}{
		{"glm", "provider", "", "zai", false},
		{"glm", "provider", "zai", "bigmodel", true},
		{"kimi", "region", "", "global", true},
		{"kimi", "region", "mainland-cn", "global", true},
		{"kimi", "region", "global", "global", false},
		{"codex", "auth_method", "chatgpt", "chatgptDeviceCode", false},
	} {
		account := map[string]any{"partner_id": test.partner, "state": "AUTHENTICATED", "auth_state": "AUTHENTICATED", "authenticated_run": "historical-proof", "auth_options": map[string]any{test.key: test.before}}
		applyPartnerAuthOptions(account, map[string]any{test.key: test.after})
		if test.changed && (account["state"] == "AUTHENTICATED" || account["auth_state"] != "UNKNOWN") {
			t.Fatal("old site authenticated a new site")
		}
		if !test.changed && account["state"] != "AUTHENTICATED" {
			t.Fatal("same official site lost an established account fact")
		}
		if account["authenticated_run"] != "historical-proof" {
			t.Fatal("site change erased historical evidence")
		}
	}
}
