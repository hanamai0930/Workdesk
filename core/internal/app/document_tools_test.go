package app

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"workdesk/core/internal/storage"
)

func TestDocumentGrantChecksOriginalInputAndParentBeforeDispatch(t *testing.T) {
	a := toolFixture(t)
	path := filepath.Join(a.Profile.ProgramRoot, "budget.xlsx")
	if err := os.WriteFile(path, []byte("template"), 0600); err != nil {
		t.Fatal(err)
	}
	err := a.write(func(s *storage.Session) error {
		for _, sql := range []string{"CREATE TABLE tasks(id TEXT,project_id TEXT,spec_hash TEXT,state TEXT,grant TEXT)", "CREATE TABLE attempts(id TEXT,task_id TEXT,started REAL,state TEXT,stop_requested INTEGER)", "INSERT INTO attempts VALUES('a','t',1,'RUNNING',0)"} {
			if _, err := s.Execute(sql, nil); err != nil {
				return err
			}
		}
		_, err := s.Execute("INSERT INTO tasks VALUES('t','project','h','RUNNING',?)", []any{fmt.Sprintf(`{"spec_hash":"h","expires_at":%f}`, now()+600)})
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	p := map[string]any{"task_id": "t", "attempt_id": "other", "spec_hash": "h", "project_id": "project", "confirm": true, "tool_version": documentVersion, "input_files": []any{"budget.xlsx"}, "parameters": map[string]any{"action": "edit", "format": "xlsx"}}
	if status, _ := postToolCommand(t, a, "tool.authorize", p); status == 200 {
		t.Fatal("accepted another attempt")
	}
	p["attempt_id"] = "a"
	status, r := postToolCommand(t, a, "tool.authorize", p)
	if status != 200 {
		t.Fatalf("authorize: %v", r)
	}
	id := r["receipt"].(map[string]any)["authorization_id"].(string)
	job := &Job{AuthorizationID: id, OutputDir: filepath.Join(a.Profile.DataRoot, "results", "job")}
	req, done, err := a.documentRequest(job)
	if err != nil {
		t.Fatal(err)
	}
	files := req["input_files"].([]string)
	data, err := os.ReadFile(files[0])
	done()
	if err != nil || string(data) != "template" || path == files[0] {
		t.Fatal("worker did not receive exact isolated template copy")
	}
	if err = os.WriteFile(path, []byte("drift"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, _, err = a.documentRequest(job); err == nil {
		t.Fatal("accepted modified template")
	}
	if status, _ = postToolCommand(t, a, "tool.revoke", map[string]any{"authorization_id": id}); status != 200 {
		t.Fatal("revoke failed")
	}
	if status, _ = postToolCommand(t, a, "tool.run", map[string]any{"authorization_id": id}); status == 200 {
		t.Fatal("revoked grant accepted")
	}
	status, r = postToolCommand(t, a, "tool.authorize", p)
	if status != 200 {
		t.Fatal(r)
	}
	id = r["receipt"].(map[string]any)["authorization_id"].(string)
	if err = a.write(func(s *storage.Session) error {
		_, e := s.Execute("UPDATE tasks SET grant=NULL WHERE id='t'", nil)
		return e
	}); err != nil {
		t.Fatal(err)
	}
	if status, _ = postToolCommand(t, a, "tool.run", map[string]any{"authorization_id": id}); status == 200 {
		t.Fatal("accepted revoked parent grant")
	}
}

func TestDocumentDecisionRejectsChangedBytes(t *testing.T) {
	a := toolFixture(t)
	j := Job{ID: ID(), ProjectID: "project", State: "COMPLETED", TaskID: "task", AttemptID: "attempt", DataRoot: a.Profile.DataRoot}
	j.OutputDir = filepath.Join(j.DataRoot, "results", j.ID)
	if err := os.MkdirAll(j.OutputDir, 0700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(j.OutputDir, "report.md")
	f := Artifact{Path: path, SHA256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", RelativePath: "results/" + j.ID + "/report.md"}
	if err := os.WriteFile(path, nil, 0600); err != nil {
		t.Fatal(err)
	}
	j.Artifacts = []Artifact{f}
	raw, _ := json.Marshal(j)
	if err := a.write(func(s *storage.Session) error {
		_, e := s.Execute("INSERT INTO core_tool_jobs VALUES(?,?,?,?,?)", []any{j.ID, j.ProjectID, j.State, string(raw), now()})
		return e
	}); err != nil {
		t.Fatal(err)
	}
	payload := map[string]any{"confirm": true, "decision": "accept", "sha256": f.SHA256, "path": path, "invocation_id": j.ID}
	status, r := postToolCommand(t, a, "tool.decide", payload)
	if status != 200 {
		t.Fatalf("accept: %v", r)
	}
	if err := os.WriteFile(path, []byte("new version"), 0600); err != nil {
		t.Fatal(err)
	}
	if status, _ = postToolCommand(t, a, "tool.decide", payload); status == 200 {
		t.Fatal("accepted changed artifact")
	}
	rows, e := a.query("SELECT task_id,attempt_id FROM events WHERE kind='tool.decide'")
	if e != nil || len(rows) != 1 || rows[0]["attempt_id"] != "attempt" || rows[0]["task_id"] != "task" {
		t.Fatalf("unbound decision: %v %v", rows, e)
	}
}
