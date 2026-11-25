//go:build windows

package instance

import (
	"bufio"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestWindowsOwnerLockInteroperatesWithPython(t *testing.T) {
	python := os.Getenv("WORKDESK_TEST_PYTHON")
	if python == "" {
		var err error
		python, err = exec.LookPath("python")
		if err != nil {
			t.Skip("Python unavailable for CRT byte-lock integration")
		}
	}
	root := t.TempDir()
	path := filepath.Join(root, "backend.lock")
	goOwner, err := AcquireOwner(root)
	if err != nil {
		t.Fatal(err)
	}
	code := "import msvcrt,sys\nf=open(sys.argv[1],'a+b');f.seek(0)\ntry: msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)\nexcept OSError: sys.exit(23)\nsys.exit(0)"
	cmd := exec.Command(python, "-c", code, path)
	hideWindow(cmd)
	err = cmd.Run()
	goOwner.Close()
	if exit, ok := err.(*exec.ExitError); !ok || exit.ExitCode() != 23 {
		t.Fatalf("Python did not observe Go lock: %v", err)
	}
	code = "import msvcrt,sys\nf=open(sys.argv[1],'a+b');f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)\nprint('READY',flush=True)\nsys.stdin.buffer.read(1)"
	cmd = exec.Command(python, "-c", code, path)
	hideWindow(cmd)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err = cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { stdin.Close(); cmd.Wait() }()
	line, err := bufio.NewReader(stdout).ReadString('\n')
	if err != nil || line != "READY\r\n" && line != "READY\n" {
		t.Fatalf("Python lock setup: %q %v", line, err)
	}
	if second, err := AcquireOwner(root); err == nil {
		second.Close()
		t.Fatal("Go did not observe Python lock")
	}
}
