package app

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"workdesk/core/internal/storage"
)

func installPartnerNativeFeaturesFixture(t *testing.T, a *App) {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("..", "..", "..", "src", "researchos", "workdesk", "partners", "native_features.json"))
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(a.pythonSources(), "researchos", "workdesk", "partners", "native_features.json")
	if err = os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
}

func taskPartnerFixture(t *testing.T) (*App, map[string]any, string) {
	t.Helper()
	a := partnerFixture(t)
	installPartnerNativeFeaturesFixture(t, a)
	p := map[string]any{"task_id": "parent-task", "attempt_id": "parent-attempt", "spec_hash": "parent-spec", "connection_id": "original-connection", "connection_revision": "7", "workspace": a.Profile.ProgramRoot, "prompt": "one exact original native turn"}
	spec, _ := json.Marshal(map[string]any{"execution": "partner_native", "provider": "codex", "partner_connection": map[string]any{"id": p["connection_id"], "revision": "7", "provider": "codex"}})
	if err := a.write(func(s *storage.Session) error {
		for _, sql := range []string{`CREATE TABLE tasks(id TEXT,spec TEXT,spec_hash TEXT,project_id TEXT,state TEXT)`, `CREATE TABLE attempts(id TEXT,task_id TEXT,state TEXT,workspace TEXT,stop_requested INTEGER,worker_instance TEXT,baseline TEXT,session_id TEXT,started REAL)`} {
			if _, err := s.Execute(sql, nil); err != nil {
				return err
			}
		}
		if _, err := s.Execute(`INSERT INTO tasks VALUES(?,?,?,?,?)`, []any{p["task_id"], string(spec), p["spec_hash"], "project", "RUNNING"}); err != nil {
			return err
		}
		_, err := s.Execute(`INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?)`, []any{p["attempt_id"], p["task_id"], "RUNNING", p["workspace"], 0, a.Process, `{}`, "current-placeholder", 2})
		return err
	}); err != nil {
		t.Fatal(err)
	}
	id := ID()
	hash := sha256.Sum256([]byte(str(p["prompt"])))
	putPartnerFixture(t, a, "run", map[string]any{"id": id, "mode": "run", "partner_id": "codex", "state": "STOP_UNKNOWN", "parent_task_id": p["task_id"], "parent_attempt_id": p["attempt_id"], "parent_spec_hash": p["spec_hash"], "parent_prompt_sha256": hex.EncodeToString(hash[:]), "connection_id": p["connection_id"], "connection_revision": 7})
	return a, p, id
}

func TestPartnerTaskResumeRequiresExactCompletedOriginal(t *testing.T) {
	a, p, _ := taskPartnerFixture(t)
	exactRelease := "codex-0.172.0-windows-amd64-e80bd26204520581"
	prior := map[string]any{"id": ID(), "mode": "run", "partner_id": "codex", "state": "COMPLETED", "parent_task_id": p["task_id"], "parent_attempt_id": "old-attempt", "parent_spec_hash": "old-spec", "connection_id": p["connection_id"], "connection_revision": 7, "account_id": "old-account", "secret_version": "old-key", "release_id": exactRelease, "session_id": "original-session", "workspace": p["workspace"], "local_exit_confirmed": true, "connection_snapshot": map[string]any{"route": "api", "api_provider": "deepseek", "model": "fixture-model"}}
	prior["result"] = map[string]any{"type": "result", "completion_scope": "OFFICIAL_NATIVE_TURN", "run_id": prior["id"], "partner_id": prior["partner_id"], "session_id": prior["session_id"]}
	putPartnerFixture(t, a, "run", prior)
	if err := a.write(func(s *storage.Session) error {
		_, err := s.Execute(`INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?)`, []any{prior["parent_attempt_id"], p["task_id"], "COMPLETED", p["workspace"], 0, a.Process, `{}`, prior["session_id"], 1})
		return err
	}); err != nil {
		t.Fatal(err)
	}
	resume := map[string]any{"resume_attempt_id": prior["parent_attempt_id"], "resume_run_id": prior["id"], "resume_session": prior["session_id"]}
	baseline, _ := json.Marshal(map[string]any{"partner_resume": resume})
	for k, v := range resume {
		p[k] = v
	}
	parent := map[string]any{"baseline": string(baseline), "workspace": p["workspace"]}
	binding := map[string]any{"provider": "codex", "version": "0.172.0", "release_id": exactRelease, "route": "api", "api_provider": "deepseek"}
	connection := map[string]any{"id": p["connection_id"], "revision": 7, "release_id": prior["release_id"], "route": "api", "api_provider": "deepseek", "model": "fixture-model"}
	account := map[string]any{"id": prior["account_id"], "secret_version": prior["secret_version"]}
	if actual, err := a.partnerTaskResume(p, parent, binding, connection, account, "workspace"); err != nil || !samePartnerResume(actual, resume) {
		t.Fatalf("exact completed session: %v", err)
	}
	for _, change := range []struct {
		row map[string]any
		key string
		bad any
	}{{p, "resume_session", "another-session"}, {p, "resume_attempt_id", nil}, {parent, "workspace", "another-workspace"}, {binding, "version", "unreviewed-version"}, {binding, "release_id", "unreviewed-package"}, {account, "id", "another-account"}, {account, "secret_version", "rotated-key"}, {connection, "release_id", "changed-release"}, {connection, "revision", 8}, {connection, "route", "account"}, {connection, "api_provider", "changed-api"}, {connection, "model", "changed-model"}} {
		old := change.row[change.key]
		change.row[change.key] = change.bad
		if _, err := a.partnerTaskResume(p, parent, binding, connection, account, "workspace"); err == nil {
			t.Errorf("accepted mismatched %s", change.key)
		}
		change.row[change.key] = old
	}
	prior["state"], prior["local_exit_confirmed"] = "STOP_UNKNOWN", false
	putPartnerFixture(t, a, "run", prior)
	if _, err := a.partnerTaskResume(p, parent, binding, connection, account, "workspace"); err == nil {
		t.Fatal("resumed ambiguous turn")
	}
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

func TestPartnerTaskMCPRequiresExactTransportAndProjectScope(t *testing.T) {
	a, p, _ := taskPartnerFixture(t)
	binding := map[string]any{"provider": "grok", "version": "1.0.92", "release_id": "grok-1.0.92-windows-amd64-366e4ad2078f11e9", "route": "api", "api_provider": "deepseek"}
	config := map[string]any{"transport": "stdio", "cwd": p["workspace"]}
	servers := []any{map[string]any{"name": "local_test", "config": config}}
	if err := a.checkPartnerTaskMCP(servers, binding, "workspace", p["workspace"]); err != nil {
		t.Fatalf("recorded stdio MCP with exact parent scope: %v", err)
	}
	config["cwd"] = "another-cwd"
	if err := a.checkPartnerTaskMCP(servers, binding, "workspace", p["workspace"]); err == nil {
		t.Fatal("unrepresentable stdio scope accepted")
	}
	config["transport"] = "unreviewed"
	if err := a.checkPartnerTaskMCP(servers, binding, "workspace", p["workspace"]); err == nil {
		t.Fatal("unreviewed transport accepted")
	}
	config["transport"] = "http"
	binding["release_id"] = "grok-1.0.92-windows-amd64-unreviewed"
	if err := a.checkPartnerTaskMCP(servers, binding, "workspace", p["workspace"]); err == nil {
		t.Fatal("same version without exact release evidence was accepted")
	}
}
