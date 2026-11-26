package app

import (
	"testing"
	"workdesk/core/internal/storage"
)

func receivedPartnerFixture(t *testing.T) (*App, string, string, string) {
	t.Helper()
	a := partnerFixture(t)
	accountID := newPartnerAccount(t, a, "codex", "deepseek_api", "exact test credential")
	account := readPartnerObject(t, a, "account", accountID)
	account["secret_version"] = "saved-secret-version"
	account["state"] = "KEY_SAVED_UNVERIFIED"
	putPartnerFixture(t, a, "account", account)
	release := localPartnerRelease(t, a, "codex")
	receipt := partnerCommandReceipt(t, a, ID(), "connection.save", map[string]any{"partner_id": "codex", "route": "api", "account_id": accountID, "release_id": release.ID, "model": "deepseek-flash"})
	connectionID := str(receipt["receipt"].(map[string]any)["connection_id"])
	connection := readPartnerObject(t, a, "connection", connectionID)
	runID := ID()
	putPartnerFixture(t, a, "run", map[string]any{
		"id": runID, "state": "STOP_UNKNOWN", "mode": "run", "partner_id": "codex", "session_id": "original-native-session",
		"account_id": accountID, "secret_version": "saved-secret-version", "connection_id": connectionID, "connection_revision": connection["revision"], "connection_snapshot": connection, "release_id": release.ID,
		"owned_process": map[string]any{"exit_confirmed": true}, "error": "old local exit observation",
		"result": map[string]any{"type": "result", "completion_scope": "OFFICIAL_NATIVE_TURN", "run_id": runID, "partner_id": "codex", "session_id": "original-native-session", "text": "previously received official result"},
	})
	return a, runID, accountID, connectionID
}

func TestPartnerReconcileAcceptsOnlyReceivedResultAndDoesNotResend(t *testing.T) {
	a, runID, accountID, connectionID := receivedPartnerFixture(t)
	commandID := ID()
	payload := map[string]any{"run_id": runID, "resolution": "accept_received_result"}
	result := partnerCommandReceipt(t, a, commandID, "partner.reconcile", payload)
	if result["receipt"].(map[string]any)["state"] != "COMPLETED" || len(a.partnerProcesses) != 0 {
		t.Fatal("saved result acceptance started work or did not complete")
	}
	run := readPartnerObject(t, a, "run", runID)
	if run["error"] != nil || run["result_acceptance"] != "EXPLICIT_ACCEPT_RECEIVED_RESULT" || run["local_exit_confirmed"] != true || run["session_id"] != "original-native-session" {
		t.Fatalf("acceptance lost original evidence: %+v", run)
	}
	account := readPartnerObject(t, a, "account", accountID)
	connection := readPartnerObject(t, a, "connection", connectionID)
	if account["state"] != "VERIFIED" || account["verified_version"] != "saved-secret-version" || account["verified_run"] != runID || connection["state"] != "VERIFIED" || connection["verified_run"] != runID {
		t.Fatal("exact credential/connection verification not updated")
	}
	accountRevision, connectionRevision := account["revision"], connection["revision"]
	if replay := partnerCommandReceipt(t, a, commandID, "partner.reconcile", payload); replay["replayed"] != true {
		t.Fatal("same command receipt was not reused")
	}
	if readPartnerObject(t, a, "account", accountID)["revision"] != accountRevision || readPartnerObject(t, a, "connection", connectionID)["revision"] != connectionRevision {
		t.Fatal("receipt lookup repeated verification mutations")
	}
}

func TestPartnerReconcileRefusesUnprovedOrMismatchedResults(t *testing.T) {
	for _, change := range []string{"no_local_exit", "missing_result", "wrong_run", "wrong_partner", "wrong_session", "wrong_scope", "stop_requested", "receipt_unknown"} {
		t.Run(change, func(t *testing.T) {
			a, runID, accountID, connectionID := receivedPartnerFixture(t)
			run := readPartnerObject(t, a, "run", runID)
			r, _ := run["result"].(map[string]any)
			switch change {
			case "no_local_exit":
				run["owned_process"] = map[string]any{"exit_confirmed": false}
			case "missing_result":
				delete(run, "result")
			case "wrong_run":
				r["run_id"] = "another-run"
			case "wrong_partner":
				r["partner_id"] = "qwen"
			case "wrong_session":
				r["session_id"] = "another-session"
			case "wrong_scope":
				r["completion_scope"] = "FINAL_TEXT_ONLY"
			case "stop_requested":
				run["stop_requested"] = true
			case "receipt_unknown":
				r["receipt_unknown"] = true
			}
			putPartnerFixture(t, a, "run", run)
			_, err := a.partnerCommand(map[string]any{"id": ID(), "kind": "partner.reconcile", "payload": map[string]any{"run_id": runID, "resolution": "accept_received_result"}})
			if err == nil {
				t.Fatal("unproved or mismatched saved result was accepted")
			}
			if readPartnerObject(t, a, "run", runID)["state"] != "STOP_UNKNOWN" || readPartnerObject(t, a, "account", accountID)["state"] != "KEY_SAVED_UNVERIFIED" || readPartnerObject(t, a, "connection", connectionID)["state"] != "SAVED_UNVERIFIED" || len(a.partnerProcesses) != 0 {
				t.Fatal("rejected acceptance changed identities or verification")
			}
		})
	}
}

func TestPartnerNormalCompletionDoesNotVerifyNewerKey(t *testing.T) {
	a, runID, accountID, connectionID := receivedPartnerFixture(t)
	account := readPartnerObject(t, a, "account", accountID)
	account["secret_version"] = "newer-secret-version"
	putPartnerFixture(t, a, "account", account)
	if err := a.write(func(s *storage.Session) error {
		run, err := partnerObject(s, "run", runID)
		if err != nil {
			return err
		}
		run["local_exit_confirmed"] = true
		if err = a.completePartnerRun(s, run, "OFFICIAL_RESULT_AND_CONFIRMED_LOCAL_EXIT"); err != nil {
			return err
		}
		return putPartner(s, "run", run)
	}); err != nil {
		t.Fatal(err)
	}
	if readPartnerObject(t, a, "run", runID)["state"] != "COMPLETED" {
		t.Fatal("historical successful run was lost")
	}
	if readPartnerObject(t, a, "account", accountID)["state"] != "KEY_SAVED_UNVERIFIED" || readPartnerObject(t, a, "connection", connectionID)["state"] != "SAVED_UNVERIFIED" {
		t.Fatal("old result verified a newer credential")
	}
}
