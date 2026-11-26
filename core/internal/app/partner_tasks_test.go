package app

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"testing"
	"workdesk/core/internal/storage"
)

func taskPartnerFixture(t *testing.T) (*App, map[string]any, string) {
	t.Helper()
	a := partnerFixture(t)
	p := map[string]any{"task_id": "parent-task", "attempt_id": "parent-attempt", "spec_hash": "parent-spec", "connection_id": "original-connection", "connection_revision": "7", "workspace": a.Profile.ProgramRoot, "prompt": "one exact original native turn"}
	spec, _ := json.Marshal(map[string]any{"execution": "partner_native", "provider": "codex", "partner_connection": map[string]any{"id": p["connection_id"], "revision": "7", "provider": "codex"}})
	if err := a.write(func(s *storage.Session) error {
		for _, sql := range []string{`CREATE TABLE tasks(id TEXT,spec TEXT,spec_hash TEXT,project_id TEXT,state TEXT)`, `CREATE TABLE attempts(id TEXT,task_id TEXT,state TEXT,workspace TEXT,stop_requested INTEGER,worker_instance TEXT)`} {
			if _, err := s.Execute(sql, nil); err != nil {
				return err
			}
		}
		if _, err := s.Execute(`INSERT INTO tasks VALUES(?,?,?,?,?)`, []any{p["task_id"], string(spec), p["spec_hash"], "project", "RUNNING"}); err != nil {
			return err
		}
		_, err := s.Execute(`INSERT INTO attempts VALUES(?,?,?,?,?,?)`, []any{p["attempt_id"], p["task_id"], "RUNNING", p["workspace"], 0, a.Process})
		return err
	}); err != nil {
		t.Fatal(err)
	}
	id := ID()
	hash := sha256.Sum256([]byte(str(p["prompt"])))
	putPartnerFixture(t, a, "run", map[string]any{"id": id, "mode": "run", "partner_id": "codex", "state": "STOP_UNKNOWN", "parent_task_id": p["task_id"], "parent_attempt_id": p["attempt_id"], "parent_spec_hash": p["spec_hash"], "parent_prompt_sha256": hex.EncodeToString(hash[:]), "connection_id": p["connection_id"], "connection_revision": 7})
	return a, p, id
}

func TestPartnerTaskIdempotentOriginalRunAndExactParentPolling(t *testing.T) {
	a, p, id := taskPartnerFixture(t)
	for i := 0; i < 2; i++ {
		value, err := a.partnerTask("partner.task.start", p)
		if err != nil || value.(map[string]any)["run"].(map[string]any)["id"] != id {
			t.Fatalf("original run: %v", err)
		}
	}
	rows, err := a.partnerObjects("run")
	if err != nil || len(rows) != 1 || len(a.partnerProcesses) != 0 {
		t.Fatal("ambiguous parent dispatch was repeated")
	}
	p["run_id"] = id
	if _, err = a.partnerTask("partner.task.poll", p); err != nil {
		t.Fatal(err)
	}
	p["run_id"] = ID()
	if _, err = a.partnerTask("partner.task.poll", p); err == nil {
		t.Fatal("foreign run was accepted")
	}
	p["run_id"] = id
	p["prompt"] = "changed prompt"
	if _, err = a.partnerTask("partner.task.start", p); err == nil {
		t.Fatal("changed message reused original dispatch")
	}
	p["spec_hash"] = "different spec"
	if _, err = a.partnerTask("partner.task.poll", p); err == nil {
		t.Fatal("stale parent spec accepted")
	}
	if !uuidPattern.MatchString(taskPartnerCommandID("start", "parent-task", "parent-attempt")) {
		t.Fatal("invalid exact command identity")
	}
}

func TestPartnerTaskStoppedBeforeStartNeverCreatesReplacement(t *testing.T) {
	a, p, id := taskPartnerFixture(t)
	if err := a.write(func(s *storage.Session) error {
		if _, err := s.Execute(`DELETE FROM core_partner_objects WHERE kind='run' AND id=?`, []any{id}); err != nil {
			return err
		}
		_, err := s.Execute(`UPDATE attempts SET stop_requested=1,state='STOP_REQUESTED'`, nil)
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := a.partnerTask("partner.task.start", p); err == nil {
		t.Fatal("stopped parent admitted work")
	}
	rows, _ := a.partnerObjects("run")
	if len(rows) != 0 || len(a.partnerProcesses) != 0 {
		t.Fatal("replacement was created")
	}
}
