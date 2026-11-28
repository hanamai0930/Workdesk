package app

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"workdesk/core/internal/storage"
)

// A completed turn, a Human completion declaration and an accepted file are
// independent. An older attempt cannot complete a task that has moved on.
func TestDeliveryDecisionsAndGuidanceStayOnOriginalAttempt(t *testing.T) {
	a := toolFixture(t)
	job := Job{ID: ID(), TaskID: "task", AttemptID: "turn", SpecHash: "spec", ProjectID: "project", State: "COMPLETED", DataRoot: a.Profile.DataRoot}
	job.OutputDir = filepath.Join(job.DataRoot, "results", job.ID)
	if err := os.MkdirAll(job.OutputDir, 0700); err != nil {
		t.Fatal(err)
	}
	f := Artifact{Path: filepath.Join(job.OutputDir, "report.md"), RelativePath: "results/" + job.ID + "/report.md", SHA256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}
	if err := os.WriteFile(f.Path, nil, 0600); err != nil {
		t.Fatal(err)
	}
	job.Artifacts = []Artifact{f}
	raw, _ := json.Marshal(job)
	if err := a.write(func(s *storage.Session) error {
		for _, sql := range []string{
			"CREATE TABLE core_partner_objects(kind TEXT,id TEXT,revision INTEGER,data TEXT,updated REAL)",
			"CREATE TABLE tasks(id TEXT,project_id TEXT,spec_hash TEXT,spec TEXT)",
			"CREATE TABLE attempts(id TEXT,task_id TEXT,started REAL,state TEXT,result TEXT,stop_requested INTEGER,session_id TEXT)",
			`INSERT INTO tasks VALUES('task','project','spec','{"work_mode":"independent","execution":"partner_native"}')`,
			"INSERT INTO attempts VALUES('turn','task',1,'COMPLETED','{}',0,'original-session')",
			`INSERT INTO core_partner_objects VALUES('run','run',1,'{"parent_task_id":"task","parent_attempt_id":"turn","parent_spec_hash":"spec","state":"COMPLETED","local_exit_confirmed":true}',0)`,
		} {
			if _, e := s.Execute(sql, nil); e != nil {
				return e
			}
		}
		_, e := s.Execute("INSERT INTO core_tool_jobs VALUES(?,?,?,?,?)", []any{job.ID, "project", job.State, string(raw), now()})
		return e
	}); err != nil {
		t.Fatal(err)
	}
	p := map[string]any{"task_id": "task", "attempt_id": "turn", "spec_hash": "spec", "run_id": "run", "invocation_id": job.ID, "path": f.Path, "sha256": f.SHA256, "confirm": true, "criteria": "Read back the reviewed file."}
	check := func(state string) {
		t.Helper()
		v, e := a.workDeliveryView("task")
		if e != nil || v.(map[string]any)["state"] != state {
			t.Fatalf("%s: %v %v", state, v, e)
		}
	}
	check("OPEN")
	cmd := func(kind string, ok bool) {
		t.Helper()
		code, r := postToolCommand(t, a, kind, p)
		if (code == 200) != ok {
			t.Fatalf("%s: %d %v", kind, code, r)
		}
	}
	p["note"] = "one"
	cmd("work.guidance", true)
	p["note"] = "stale"
	cmd("work.guidance", false)
	cmd("work.complete", true)
	check("COMPLETED")
	p["decision"] = "revise"
	cmd("tool.decide", true)
	check("REVISION_REQUIRED")
	cmd("work.complete", false)
	if e := a.write(func(s *storage.Session) error {
		_, e := s.Execute("INSERT INTO attempts VALUES('next','task',2,'RUNNING','{}',0,'original-session')", nil)
		return e
	}); e != nil {
		t.Fatal(e)
	}
	check("OPEN")
	cmd("work.complete", false)
}
