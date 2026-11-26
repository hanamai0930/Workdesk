package app

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"workdesk/core/internal/components"
	"workdesk/core/internal/storage"
)

// All partner fixtures are local records and inert program files. Their Python
// executable is toolFixture's intentionally missing path; no provider is used.
func partnerFixture(t *testing.T) *App {
	t.Helper()
	a := toolFixture(t)
	a.Profile.DevelopmentRoot = filepath.Join(a.Profile.UserRoot, "development")
	a.partnerProcesses = map[string]*partnerProcess{}
	if err := a.DB.MigratePartners(); err != nil {
		t.Fatal(err)
	}
	installPartnerAPICatalogFixture(t, a)
	return a
}

func partnerCommandReceipt(t *testing.T, a *App, id, kind string, payload map[string]any) map[string]any {
	t.Helper()
	value, err := a.partnerCommand(map[string]any{"id": id, "kind": kind, "payload": payload})
	if err != nil {
		t.Fatalf("%s: %v", kind, err)
	}
	result, ok := value.(map[string]any)
	if !ok {
		t.Fatalf("%s result is not an object", kind)
	}
	return result
}

func newPartnerAccount(t *testing.T, a *App, partner, kind, label string) string {
	t.Helper()
	result := partnerCommandReceipt(t, a, ID(), "account.create", map[string]any{"partner_id": partner, "kind": kind, "label": label})
	receipt := result["receipt"].(map[string]any)
	id := str(receipt["account_id"])
	if id == "" {
		t.Fatal("account receipt lost its identity")
	}
	return id
}

func readPartnerObject(t *testing.T, a *App, kind, id string) map[string]any {
	t.Helper()
	s, err := a.DB.OpenSession(false)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Rollback()
	m, err := partnerObject(s, kind, id)
	if err != nil {
		t.Fatal(err)
	}
	return m
}

func putPartnerFixture(t *testing.T, a *App, kind string, row map[string]any) {
	t.Helper()
	if err := a.savePartner(kind, row); err != nil {
		t.Fatal(err)
	}
}

func localPartnerRelease(t *testing.T, a *App, partner string) components.Release {
	t.Helper()
	body := []byte("Inert official-component fixture; never executed.\n")
	fileHash := sha256.Sum256(body)
	manifest, err := json.Marshal([]components.File{{Path: "fixture.txt", SHA256: hex.EncodeToString(fileHash[:]), Bytes: int64(len(body))}})
	if err != nil {
		t.Fatal(err)
	}
	treeHash := sha256.Sum256(manifest)
	digest := hex.EncodeToString(treeHash[:])
	platform := runtime.GOOS + "-" + runtime.GOARCH
	root := filepath.Join(a.Profile.DevelopmentRoot, "components")
	location := filepath.Join(root, partner, "1.0.0-"+platform+"-"+digest[:16])
	release := components.Release{Schema: 1, ID: partner + "-1.0.0-" + platform + "-" + digest[:16], ComponentID: partner, Version: "1.0.0", Platform: platform, Path: location, SHA256: digest, Entry: []string{filepath.Join(location, "must-not-run.exe")}, State: "AVAILABLE", Managed: true, Files: 1, Bytes: int64(len(body)), Source: "local-test-fixture"}
	if err = os.MkdirAll(location, 0700); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(location, "fixture.txt"), body, 0600); err != nil {
		t.Fatal(err)
	}
	if err = os.MkdirAll(filepath.Join(root, ".records"), 0700); err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(release)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(root, ".records", release.ID+".json"), raw, 0600); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(root, ".records", release.ID+".files.json"), manifest, 0600); err != nil {
		t.Fatal(err)
	}
	return release
}

func TestPartnerSchema2RefusesWriteWithoutImplicitMigration(t *testing.T) {
	a := toolFixture(t)
	_, err := a.partnerCommand(map[string]any{"id": ID(), "kind": "account.create", "payload": map[string]any{"partner_id": "codex", "kind": "official_login", "label": "must not create"}})
	if err == nil {
		t.Fatal("schema 2 accepted a partner command")
	}
	schema, err := a.DB.Schema()
	if err != nil || schema != "2" {
		t.Fatalf("rejected command changed the storage schema: %q %v", schema, err)
	}
	rows, err := a.query(`SELECT COUNT(*) AS total FROM commands`)
	if err != nil || number(rows[0]["total"]) != 0 {
		t.Fatalf("rejected command left a receipt: %+v %v", rows, err)
	}
}

func TestPartnerPolicyRecoveryReferencesAndExplicitReconciliation(t *testing.T) {
	a := partnerFixture(t)
	release := localPartnerRelease(t, a, "codex")
	account := newPartnerAccount(t, a, "codex", "deepseek_api", "dummy API reference")
	partnerCommandReceipt(t, a, ID(), "component.disable", map[string]any{"release_id": release.ID})
	payload := map[string]any{"partner_id": "codex", "release_id": release.ID, "route": "api", "account_id": account, "model": "deepseek-flash"}
	if _, e := a.partnerCommand(map[string]any{"id": ID(), "kind": "connection.save", "payload": payload}); e == nil {
		t.Fatal("disabled component accepted new connection")
	}
	partnerCommandReceipt(t, a, ID(), "component.enable", map[string]any{"release_id": release.ID})
	partnerCommandReceipt(t, a, ID(), "connection.save", payload)
	putPartnerFixture(t, a, "recovery", map[string]any{"id": ID(), "release_ids": []string{release.ID}, "state": "PROTECTED"})
	tx, _ := a.DB.OpenSession(false)
	if e := a.protectRelease(tx, release.ID); e == nil {
		t.Fatal("recovery dependency was not protected")
	}
	_ = tx.Rollback()
	run := ID()
	putPartnerFixture(t, a, "run", map[string]any{"id": run, "state": "ORPHANED_UNKNOWN", "session_id": "exact-native-session", "secret_version": "unchanged-key-version", "owned_process": map[string]any{"exit_confirmed": true}})
	partnerCommandReceipt(t, a, ID(), "partner.reconcile", map[string]any{"run_id": run})
	current := readPartnerObject(t, a, "run", run)
	if current["state"] != "ORPHANED_UNKNOWN" || current["local_exit_confirmed"] != true {
		t.Fatal("inspection silently closed unknown state")
	}
	partnerCommandReceipt(t, a, ID(), "partner.reconcile", map[string]any{"run_id": run, "resolution": "close_local_tracking"})
	current = readPartnerObject(t, a, "run", run)
	if current["state"] != "STOPPED_RUNTIME" || current["remote_state"] != "UNKNOWN" || current["session_id"] != "exact-native-session" || current["secret_version"] != "unchanged-key-version" {
		t.Fatal("explicit local closure changed remote outcome or identity")
	}
}

func TestPartnerNativeReceiptDoesNotProveLocalTreeExit(t *testing.T) {
	a := partnerFixture(t)
	run := ID()
	putPartnerFixture(t, a, "run", map[string]any{"id": run, "state": "RUNNING", "mode": "run", "release_id": "active-release"})
	if e := a.partnerEvent(run, map[string]any{"type": "error", "error": "explicit native failure"}); e != nil {
		t.Fatal(e)
	}
	if current := readPartnerObject(t, a, "run", run); current["state"] != "ERROR_RECEIVED" || !a.partnerBusy() {
		t.Fatal("error receipt prematurely released local execution ownership")
	}
	if e := a.partnerEvent(run, map[string]any{"type": "result", "text": "native turn ended"}); e != nil {
		t.Fatal(e)
	}
	if current := readPartnerObject(t, a, "run", run); current["state"] != "RESULT_RECEIVED" {
		t.Fatal("native result was marked complete without exit proof")
	}
}

func TestPartnerRecoveryDependencyRemovalNeedsExplicitScope(t *testing.T) {
	a := partnerFixture(t)
	r := localPartnerRelease(t, a, "codex")
	putPartnerFixture(t, a, "recovery", map[string]any{"id": ID(), "state": "PROTECTED", "release_ids": []string{r.ID}})
	tx, _ := a.DB.OpenSession(false)
	defer tx.Rollback()
	if e := a.protectRelease(tx, r.ID); e == nil {
		t.Fatal("unconfirmed removal bypassed recovery retention")
	}
	if e := a.protectRelease(tx, r.ID, recoveryRemovalFlag(map[string]any{"allow_restore_download": true})); e != nil {
		t.Fatal("explicit program-only uninstall could not retain recovery metadata:", e)
	}
}

func TestPartnerAccountCommandIdentityAndTypeIsolation(t *testing.T) {
	a := partnerFixture(t)
	commandID := ID()
	payload := map[string]any{"partner_id": "codex", "kind": "official_login", "label": "first official account"}
	first := partnerCommandReceipt(t, a, commandID, "account.create", payload)
	replayed := partnerCommandReceipt(t, a, commandID, "account.create", payload)
	if first["replayed"] != false || replayed["replayed"] != true {
		t.Fatalf("receipt replay status: first=%+v replay=%+v", first, replayed)
	}
	officialID := str(first["receipt"].(map[string]any)["account_id"])
	if replayed["receipt"].(map[string]any)["account_id"] != officialID {
		t.Fatal("a replay created a different account")
	}
	if _, err := a.partnerCommand(map[string]any{"id": commandID, "kind": "account.create", "payload": map[string]any{"partner_id": "codex", "kind": "deepseek_api", "label": "different content"}}); err == nil {
		t.Fatal("same command identity accepted different content")
	}
	apiID := newPartnerAccount(t, a, "codex", "deepseek_api", "API account")
	otherID := newPartnerAccount(t, a, "kimi", "official_login", "another partner")
	secondOfficialID := newPartnerAccount(t, a, "codex", "official_login", "second official account")
	if officialID == apiID || officialID == secondOfficialID {
		t.Fatal("different accounts collapsed into one identity")
	}
	rows, err := a.partnerObjects("account")
	if err != nil || len(rows) != 4 {
		t.Fatalf("multi-account records: %+v %v", rows, err)
	}
	release := localPartnerRelease(t, a, "codex")
	for _, tc := range []struct{ name, account, route string }{
		{"official account used as API", officialID, "api"},
		{"API key used as official login", apiID, "account"},
		{"other partner account", otherID, "account"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, err := a.partnerCommand(map[string]any{"id": ID(), "kind": "connection.save", "payload": map[string]any{"partner_id": "codex", "release_id": release.ID, "route": tc.route, "account_id": tc.account, "model": "deepseek-flash"}})
			if err == nil {
				t.Fatal("connection accepted a mismatched account identity/type")
			}
		})
	}
	connections, err := a.partnerObjects("connection")
	if err != nil || len(connections) != 0 {
		t.Fatalf("rejected account combination persisted a connection: %+v %v", connections, err)
	}
}

func TestPartnerActiveAndUnknownRunProtectAccountAndRelease(t *testing.T) {
	for _, state := range []string{"RUNNING", "STOP_REQUESTED", "ORPHANED_UNKNOWN"} {
		t.Run(state, func(t *testing.T) {
			a := partnerFixture(t)
			accountID := newPartnerAccount(t, a, "codex", "official_login", "protected account")
			runID := ID()
			putPartnerFixture(t, a, "run", map[string]any{"id": runID, "account_id": accountID, "release_id": "protected-release", "state": state})
			if !a.partnerBusy() {
				t.Fatal("active/unknown partner run did not block unsafe lifecycle changes")
			}
			if _, err := a.partnerCommand(map[string]any{"id": ID(), "kind": "account.disconnect", "payload": map[string]any{"account_id": accountID}}); err == nil {
				t.Fatal("active/unknown account was disconnected")
			}
			if err := a.write(func(s *storage.Session) error { return a.protectRelease(s, "protected-release") }); err == nil {
				t.Fatal("active/unknown component reference was removable")
			}
			row := readPartnerObject(t, a, "account", accountID)
			if str(row["state"]) == "DISCONNECTED" {
				t.Fatal("rejected disconnect mutated account state")
			}
		})
	}
}

func TestPartnerRecoveryRetainsUnknownIdentityAndDoesNotReplay(t *testing.T) {
	a := partnerFixture(t)
	runID, operationID, commandID := ID(), ID(), ID()
	payload := map[string]any{"connection_id": "old-connection", "prompt": "already submitted once"}
	putPartnerFixture(t, a, "run", map[string]any{"id": runID, "state": "WAITING_APPROVAL", "account_id": "account-before-restart", "secret_version": "credential-version", "release_id": "old-release", "session_id": "native-session-before-restart", "process_instance": "previous-owner", "owner_epoch": 4})
	putPartnerFixture(t, a, "operation", map[string]any{"id": operationID, "state": "QUEUED", "release_id": "old-release", "command_id": commandID})
	putPartnerFixture(t, a, "run", map[string]any{"id": "finished-run", "state": "COMPLETED"})
	raw, _ := json.Marshal(payload)
	hash := sha256.Sum256(raw)
	receipt, _ := json.Marshal(map[string]any{"run_id": runID, "state": "QUEUED"})
	if err := a.write(func(s *storage.Session) error {
		_, err := s.Execute(`INSERT INTO commands VALUES(?,?,?,?,?)`, []any{commandID, "partner.run", hex.EncodeToString(hash[:]), string(receipt), now()})
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if err := a.recoverPartners(); err != nil {
		t.Fatal(err)
	}
	recovered := readPartnerObject(t, a, "run", runID)
	if str(recovered["state"]) != "ORPHANED_UNKNOWN" || recovered["session_id"] != "native-session-before-restart" || recovered["secret_version"] != "credential-version" || number(recovered["owner_epoch"]) != 4 {
		t.Fatalf("recovery lost original run identity: %+v", recovered)
	}
	if str(readPartnerObject(t, a, "operation", operationID)["state"]) != "ORPHANED_UNKNOWN" || str(readPartnerObject(t, a, "run", "finished-run")["state"]) != "COMPLETED" {
		t.Fatal("recovery did not distinguish unknown and completed work")
	}
	result := partnerCommandReceipt(t, a, commandID, "partner.run", payload)
	if result["replayed"] != true || len(a.partnerProcesses) != 0 {
		t.Fatal("recovered command started a new worker")
	}
	if str(readPartnerObject(t, a, "run", runID)["state"]) != "ORPHANED_UNKNOWN" {
		t.Fatal("replayed receipt reset unknown run state")
	}
	if _, err := a.partnerCommand(map[string]any{"id": ID(), "kind": "partner.input", "payload": map[string]any{"run_id": runID, "request_id": "old-request", "text": "do not send"}}); err == nil {
		t.Fatal("input was sent toward an orphaned old session")
	}
}

func TestPartnerComponentRemovalDoesNotProtectItsOwnOperation(t *testing.T) {
	a := partnerFixture(t)
	release := localPartnerRelease(t, a, "codex")
	result := partnerCommandReceipt(t, a, ID(), "component.remove", map[string]any{"release_id": release.ID})
	opID := str(result["receipt"].(map[string]any)["operation_id"])
	deadline := time.Now().Add(3 * time.Second)
	for {
		op := readPartnerObject(t, a, "operation", opID)
		if str(op["state"]) == "COMPLETED" {
			break
		}
		if terminalPartner(str(op["state"])) || time.Now().After(deadline) {
			t.Fatalf("unreferenced component removal self-blocked: %+v", op)
		}
		time.Sleep(5 * time.Millisecond)
	}
	if _, err := os.Stat(release.Path); !os.IsNotExist(err) {
		t.Fatalf("component program remained after removal: %v", err)
	}
	recorded, err := a.componentManager().Get(release.ID)
	if err != nil || recorded.State != "REMOVED" {
		t.Fatalf("removal discarded provenance or failed to mark release: %+v %v", recorded, err)
	}
}
