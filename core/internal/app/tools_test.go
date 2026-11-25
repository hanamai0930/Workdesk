package app

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"workdesk/core/internal/instance"
	"workdesk/core/internal/storage"
)

func toolFixture(t *testing.T) *App {
	t.Helper()
	root := t.TempDir()
	p := &instance.Profile{
		ProgramRoot: filepath.Join(root, "project"), UserRoot: filepath.Join(root, "user"),
		DataRoot:         filepath.Join(root, "user", "libraries", "main"),
		PythonExecutable: filepath.Join(root, "must-not-start-python.exe"), DefaultProjectID: "project",
	}
	if err := os.MkdirAll(p.ProgramRoot, 0700); err != nil {
		t.Fatal(err)
	}
	db, err := storage.Open(filepath.Join(p.DataRoot, "tasks.sqlite3"), false)
	if err != nil {
		t.Fatal(err)
	}
	if err = db.InitializeCore(); err != nil {
		db.Close()
		t.Fatal(err)
	}
	a := &App{Profile: p, DB: db, Process: ID(), Epoch: 1, Control: "test-control", Viewer: "test-viewer",
		jobs: make(map[string]*runningJob), sessions: make(map[string]*storage.Session),
		processes: make(map[string]map[string]any), Stop: make(chan struct{}), Log: io.Discard}
	if err = a.write(func(s *storage.Session) error {
		for _, query := range []string{
			`CREATE TABLE projects(id TEXT PRIMARY KEY,root TEXT NOT NULL)`,
			`CREATE TABLE commands(id TEXT PRIMARY KEY,kind TEXT,payload_hash TEXT,receipt TEXT,created REAL)`,
			`CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,at REAL,task_id TEXT,attempt_id TEXT,kind TEXT,payload TEXT)`,
			`CREATE TABLE core_tool_jobs(id TEXT PRIMARY KEY,project_id TEXT,state TEXT,data TEXT,updated REAL)`,
			`CREATE TABLE core_command_metadata(command_id TEXT PRIMARY KEY,codec TEXT)`,
		} {
			if _, err := s.Execute(query, nil); err != nil {
				return err
			}
		}
		_, err := s.Execute(`INSERT INTO projects VALUES(?,?)`, []any{"project", p.ProgramRoot})
		return err
	}); err != nil {
		db.Close()
		t.Fatal(err)
	}
	t.Cleanup(func() {
		a.Close()
		deadline := time.Now().Add(2 * time.Second)
		for {
			a.mu.Lock()
			remaining := len(a.jobs)
			a.mu.Unlock()
			if remaining == 0 {
				break
			}
			if time.Now().After(deadline) {
				t.Error("tool worker did not finish after cancellation")
				break
			}
			time.Sleep(5 * time.Millisecond)
		}
		db.Close()
	})
	return a
}

func postToolCommand(t *testing.T, a *App, kind string, payload map[string]any) (int, map[string]any) {
	t.Helper()
	raw, err := json.Marshal(map[string]any{"id": ID(), "kind": kind, "payload": payload})
	if err != nil {
		t.Fatal(err)
	}
	r := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:18791/api/v1/command", bytes.NewReader(raw))
	r.Header.Set("Authorization", "Bearer "+a.Control)
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	a.BindPort("18791").ServeHTTP(w, r)
	var value map[string]any
	if err = json.Unmarshal(w.Body.Bytes(), &value); err != nil {
		t.Fatalf("invalid API response %d: %q", w.Code, w.Body.String())
	}
	return w.Code, value
}

func TestQueuedToolCancellationNeverStartsPython(t *testing.T) {
	a := toolFixture(t)
	localToolSlot <- struct{}{}
	held := true
	defer func() {
		if held {
			<-localToolSlot
		}
	}()
	status, result := postToolCommand(t, a, "tool.run", map[string]any{"project_id": "project"})
	if status != http.StatusOK {
		t.Fatalf("enqueue: %d %+v", status, result)
	}
	receipt, ok := result["receipt"].(map[string]any)
	if !ok || receipt["state"] != "QUEUED" {
		t.Fatalf("missing queued receipt: %+v", result)
	}
	id, ok := receipt["invocation_id"].(string)
	if !ok || id == "" {
		t.Fatal("missing invocation identity")
	}
	status, result = postToolCommand(t, a, "tool.cancel", map[string]any{"invocation_id": id})
	if status != http.StatusOK {
		t.Fatalf("cancel: %d %+v", status, result)
	}
	<-localToolSlot
	held = false
	deadline := time.Now().Add(2 * time.Second)
	for {
		rows, err := a.query(`SELECT state,data FROM core_tool_jobs WHERE id=?`, id)
		if err != nil || len(rows) != 1 {
			t.Fatalf("read queued job: %+v %v", rows, err)
		}
		state := str(rows[0]["state"])
		if state == "CANCELLED" {
			var job Job
			if err = json.Unmarshal([]byte(str(rows[0]["data"])), &job); err != nil {
				t.Fatal(err)
			}
			if job.PID != 0 || len(job.Artifacts) != 0 {
				t.Fatalf("cancelled queued tool ran a process: %+v", job)
			}
			if _, err = os.Stat(job.OutputDir); !os.IsNotExist(err) {
				t.Fatalf("cancelled tool created output: %s (%v)", job.OutputDir, err)
			}
			return
		}
		if state == "RUNNING" || state == "FAILED" || state == "COMPLETED" || time.Now().After(deadline) {
			t.Fatalf("queued cancellation did not prevent dispatch: %+v", rows[0])
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func TestRetiringCoreRejectsToolWithoutPersistentEffects(t *testing.T) {
	a := toolFixture(t)
	a.retiring.Store(true)
	status, result := postToolCommand(t, a, "tool.run", map[string]any{"project_id": "project"})
	if status != http.StatusConflict || result["explicit_rejection"] != true || result["state"] != "REJECTED" {
		t.Fatalf("retiring core admitted tool: %d %+v", status, result)
	}
	for _, table := range []string{"commands", "core_tool_jobs", "core_command_metadata", "events"} {
		rows, err := a.query("SELECT COUNT(*) AS count FROM " + table)
		if err != nil || len(rows) != 1 || number(rows[0]["count"]) != 0 {
			t.Fatalf("rejected tool changed %s: %+v %v", table, rows, err)
		}
	}
}

func TestRestoredToolArtifactUsesNewLibraryAndRejectsEscape(t *testing.T) {
	a := toolFixture(t)
	oldRoot := filepath.Join(t.TempDir(), "old-library")
	oldOutput := filepath.Join(oldRoot, "results", "invocation")
	oldPath := filepath.Join(oldOutput, "材料概要.txt")
	newOutput := filepath.Join(a.Profile.DataRoot, "results", "invocation")
	newPath := filepath.Join(newOutput, "材料概要.txt")
	content := []byte("restored material report\n")
	if err := os.MkdirAll(newOutput, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(newPath, content, 0600); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(content)
	job := Job{ID: "invocation", DataRoot: oldRoot, OutputDir: oldOutput, State: "COMPLETED",
		Artifacts: []Artifact{{Path: oldPath, RelativePath: "results/invocation/材料概要.txt", SHA256: hex.EncodeToString(digest[:]), Bytes: int64(len(content)), Mime: "text/plain"}}}
	if err := a.resolveJob(&job); err != nil {
		t.Fatal(err)
	}
	if !instance.SamePath(job.OutputDir, newOutput) || !instance.SamePath(job.Artifacts[0].Path, newPath) || job.Artifacts[0].OriginalPath != oldPath {
		t.Fatalf("restored path mapping lost original reference: %+v", job)
	}
	if _, err := os.Stat(oldPath); !os.IsNotExist(err) {
		t.Fatalf("test unexpectedly depends on original artifact: %v", err)
	}
	if err := verifyArtifact(job.OutputDir, job.Artifacts[0]); err != nil {
		t.Fatalf("restored artifact is not independently readable: %v", err)
	}
	escaped := Job{DataRoot: oldRoot, OutputDir: oldOutput, Artifacts: []Artifact{{Path: oldPath, RelativePath: "../../outside.txt"}}}
	if err := a.resolveJob(&escaped); err == nil {
		t.Fatal("restored relative artifact path escaped its invocation directory")
	}
}
