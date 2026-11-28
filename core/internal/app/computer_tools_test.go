package app

import (
	"os"
	"path/filepath"
	"testing"

	"workdesk/core/internal/storage"
)

func TestComputerGrantBindsScriptAndRepositoryState(t *testing.T) {
	a := toolFixture(t)
	root := t.TempDir()
	if err := a.write(func(s *storage.Session) error {
		_, e := s.Execute("UPDATE projects SET root=? WHERE id='project'", []any{root})
		return e
	}); err != nil {
		t.Fatal(err)
	}
	script := filepath.Join(root, "task.py")
	if err := os.WriteFile(script, []byte("print('v1')"), 0600); err != nil {
		t.Fatal(err)
	}
	params := map[string]any{"action": "terminal.run", "path": "task.py"}
	payload := map[string]any{"tool_version": computerVersion, "project_id": "project", "confirm": true, "parameters": params}
	authorize := func() *Job {
		t.Helper()
		status, response := postToolCommand(t, a, "tool.authorize", payload)
		if status != 200 {
			t.Fatal(response)
		}
		return &Job{AuthorizationID: response["receipt"].(map[string]any)["authorization_id"].(string), Action: str(params["action"]), OutputDir: filepath.Join(a.Profile.DataRoot, "results", ID())}
	}
	job := authorize()
	if _, e := a.computerRequest(job); e != nil {
		t.Fatal(e)
	}
	if err := os.WriteFile(script, []byte("print('v2')"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, e := a.computerRequest(job); e == nil {
		t.Fatal("script changed after confirmation")
	}
	params["path"] = "../external"
	if status, _ := postToolCommand(t, a, "tool.authorize", payload); status == 200 {
		t.Fatal("accepted external file")
	}
	params["path"] = "task.py"
	params["action"] = "git.stage"
	meta := filepath.Join(root, ".git")
	if err := os.Mkdir(meta, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(meta, "HEAD"), []byte("ref: refs/heads/main\n"), 0600); err != nil {
		t.Fatal(err)
	}
	job = authorize()
	req, e := a.computerRequest(job)
	if e != nil {
		t.Fatal(e)
	}
	versions := req["repository_versions"].(map[string]string)
	if versions["HEAD"] == "" || versions["index"] != "absent" || versions["refs/heads/main"] != "absent" {
		t.Fatalf("lost index or revision: %v", versions)
	}
}

func TestComputerSealsPartialConsole(t *testing.T) {
	a := toolFixture(t)
	job := &Job{ID: ID(), State: "CANCELLED"}
	job.OutputDir = filepath.Join(a.Profile.DataRoot, "results", job.ID)
	inbox := filepath.Join(job.OutputDir, ".input")
	if err := os.MkdirAll(inbox, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(inbox, "pending.txt"), []byte("unsent"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(job.OutputDir, "console.txt"), []byte("ready"), 0600); err != nil {
		t.Fatal(err)
	}
	a.finishTerminal(job)
	if _, err := os.Stat(inbox); !os.IsNotExist(err) {
		t.Fatal("input left on disk")
	}
	if len(job.Artifacts) != 1 || verifyArtifact(job.OutputDir, job.Artifacts[0]) != nil {
		t.Fatal("missing partial terminal output")
	}
}
