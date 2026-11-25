//go:build windows

package processowner

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"golang.org/x/sys/windows"
)

func fixturePython(t *testing.T) string {
	t.Helper()
	path := os.Getenv("WORKDESK_TEST_PYTHON")
	if path == "" {
		path, _ = filepath.Abs(filepath.Join("..", "..", "..", ".venv", "Scripts", "python.exe"))
	}
	if _, err := os.Stat(path); err != nil {
		t.Skip("explicit local Python unavailable for owned launcher integration")
	}
	return path
}

func TestOwnedCommandNormalExitHasJobProof(t *testing.T) {
	var output bytes.Buffer
	cmd := exec.Command(fixturePython(t), "-c", "print('owned normal exit')")
	cmd.Stdout, cmd.Stderr = &output, &output
	owned, err := Start(cmd)
	if err != nil {
		if owned != nil {
			owned.Close()
		}
		t.Fatal(err)
	}
	defer owned.Close()
	if err = owned.Wait(); err != nil {
		t.Fatalf("normal owned command: %v; %s", err, output.String())
	}
	identity := owned.Identity()
	if !owned.ExitConfirmed() || identity["active_processes"] != uint32(0) || identity["creation"] == "" {
		t.Fatalf("missing owned exit proof: %+v", identity)
	}
	if !bytes.Contains(output.Bytes(), []byte("owned normal exit")) {
		t.Fatalf("owned process did not actually run: %q", output.String())
	}
}

func TestContextCancellationStopsVenvLauncherAndItsChildOnly(t *testing.T) {
	python := fixturePython(t)
	root := t.TempDir()
	marker := filepath.Join(root, "child.json")
	parentMarker := filepath.Join(root, "parent.json")
	childCode := "import json,os,sys,time\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps({'pid':os.getpid()}))\nwhile True: time.sleep(.05)"
	parentCode := "import json,os,subprocess,sys,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nPath(sys.argv[3]).write_text(json.dumps({'pid':os.getpid(),'launcher_pid':p.pid}))\nwhile True: time.sleep(.05)"
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, python, "-c", parentCode, childCode, marker, parentMarker)
	cmd.Stdout, cmd.Stderr = io.Discard, io.Discard
	owned, err := Start(cmd)
	if err != nil {
		if owned != nil {
			owned.Close()
		}
		t.Fatal(err)
	}
	defer owned.Close()
	var child, parent struct {
		PID         int `json:"pid"`
		LauncherPID int `json:"launcher_pid"`
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		childRaw, childErr := os.ReadFile(marker)
		parentRaw, parentErr := os.ReadFile(parentMarker)
		if childErr == nil && parentErr == nil && json.Unmarshal(childRaw, &child) == nil && json.Unmarshal(parentRaw, &parent) == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("owned Python child did not start")
		}
		time.Sleep(10 * time.Millisecond)
	}
	childHandle, err := windows.OpenProcess(windows.SYNCHRONIZE|windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(child.PID))
	if err != nil {
		t.Fatal(err)
	}
	defer windows.CloseHandle(childHandle)
	parentHandle, err := windows.OpenProcess(windows.SYNCHRONIZE|windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(parent.PID))
	if err != nil {
		t.Fatal(err)
	}
	defer windows.CloseHandle(parentHandle)
	if active, ok := owned.Identity()["active_processes"].(uint32); !ok || active < 2 {
		t.Fatalf("job did not include the actual child tree: %+v", owned.Identity())
	}
	// A second separately owned command proves termination is scoped to one Job.
	otherCmd := exec.Command(python, "-c", "import time; time.sleep(15)")
	otherCmd.Stdout, otherCmd.Stderr = io.Discard, io.Discard
	other, err := Start(otherCmd)
	if err != nil {
		if other != nil {
			other.Close()
		}
		t.Fatal(err)
	}
	defer other.Close()
	cancel()
	if err = owned.Wait(); err == nil {
		t.Fatal("cancelled command unexpectedly succeeded")
	}
	if !owned.ExitConfirmed() {
		t.Fatalf("launcher exit was mistaken for complete tree exit: %+v", owned.Identity())
	}
	for _, handle := range []windows.Handle{parentHandle, childHandle} {
		event, err := windows.WaitForSingleObject(handle, 3000)
		if err != nil || event != windows.WAIT_OBJECT_0 {
			t.Fatalf("actual Python process survived Job cancellation: event=%d err=%v", event, err)
		}
	}
	if active, ok := other.Identity()["active_processes"].(uint32); !ok || active == 0 {
		t.Fatalf("independent Job was terminated too: %+v", other.Identity())
	}
	if err = other.Terminate(); err != nil {
		t.Fatal(err)
	}
	_ = other.Wait()
	if !other.ExitConfirmed() {
		t.Fatal("second owned command did not clean up")
	}
	t.Logf("launcher=%d actual-parent=%d child-launcher=%d actual-child=%d; whole-tree exit confirmed", cmd.Process.Pid, parent.PID, parent.LauncherPID, child.PID)
}
