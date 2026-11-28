package app

import (
	"os"
	"path/filepath"
	"testing"
	"workdesk/core/internal/storage"
)

func TestNativeDocumentRequestBoundReviewAndStop(t *testing.T) {
	a, p, rid := taskPartnerFixture(t)
	run := map[string]any{"id": rid, "state": "RUNNING", "parent_task_id": p["task_id"], "parent_attempt_id": p["attempt_id"], "parent_spec_hash": p["spec_hash"], "project_id": "project", "process_instance": a.Process, "mode": "run"}
	putPartnerFixture(t, a, "run", run)
	path := filepath.Join(a.Profile.ProgramRoot, "budget.xlsx")
	if err := os.WriteFile(path, []byte("original template"), 0600); err != nil {
		t.Fatal(err)
	}
	args := map[string]any{"parameters": map[string]any{"action": "edit", "format": "xlsx"}, "input_files": []any{"budget.xlsx"}}
	q, err := a.proposeDocument(run, args)
	if err != nil {
		t.Fatal(err)
	}
	retry, err := a.proposeDocument(run, args)
	if err != nil || retry["id"] != q["id"] {
		t.Fatal("retried call created another request", err)
	}
	payload := map[string]any{"request_id": q["id"], "confirm": true, "project_id": "project", "task_id": q["task_id"], "attempt_id": q["attempt_id"], "spec_hash": q["spec_hash"], "tool_version": documentVersion, "input_files": args["input_files"], "parameters": args["parameters"]}
	authorize := func() error {
		_, e := a.toolCommand(map[string]any{"id": q["authorization_id"], "kind": "tool.authorize", "payload": payload})
		return e
	}
	if err := os.WriteFile(path, []byte("changed"), 0600); err != nil {
		t.Fatal(err)
	}
	if authorize() == nil {
		t.Fatal("authorized changed template")
	}
	if err := os.WriteFile(path, []byte("original template"), 0600); err != nil {
		t.Fatal(err)
	}
	payload["parameters"] = map[string]any{"action": "edit", "format": "docx"}
	if authorize() == nil {
		t.Fatal("accepted edited proposal")
	}
	payload["parameters"] = args["parameters"]
	if err := authorize(); err != nil {
		t.Fatal(err)
	}
	list, err := a.documentRequests(str(p["task_id"]), "")
	if err != nil || len(list) != 1 || list[0]["state"] != "AUTHORIZED" {
		t.Fatal(list, err)
	}
	_, err = a.toolCommand(map[string]any{"id": ID(), "kind": "tool.run", "payload": map[string]any{"authorization_id": q["authorization_id"]}})
	if err == nil {
		t.Fatal("accepted different execution command")
	}
	if err := a.write(func(s *storage.Session) error {
		_, e := s.Execute("UPDATE attempts SET stop_requested=1 WHERE id=?", []any{q["attempt_id"]})
		return e
	}); err != nil {
		t.Fatal(err)
	}
	_, err = a.toolCommand(map[string]any{"id": q["command_id"], "kind": "tool.run", "payload": map[string]any{"authorization_id": q["authorization_id"]}})
	if err == nil {
		t.Fatal("started after parent stop")
	}
	list, err = a.documentRequests(str(p["task_id"]), "")
	if err != nil || len(list) != 1 || list[0]["state"] != "EXPIRED" {
		t.Fatal(list, err)
	}
}
