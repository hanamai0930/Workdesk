package instance

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func profileFixture(t *testing.T) (string, *Profile) {
	t.Helper()
	root := t.TempDir()
	p := &Profile{Schema: 1, Channel: "development", InstanceID: "8dd726c8-35f4-494c-ba3b-6203ba103894", DevelopmentRoot: root, ProgramRoot: filepath.Join(root, "repo"), UserRoot: filepath.Join(root, "user"), DataRoot: filepath.Join(root, "user", "libraries", "main"), PythonExecutable: filepath.Join(root, "repo", "python.exe"), UIPort: 18766, SyncBranch: "codex/v2-dev", DefaultProjectID: "project"}
	os.MkdirAll(p.ProgramRoot, 0700)
	os.MkdirAll(p.DataRoot, 0700)
	config := filepath.Join(root, "instance.json")
	writeProfile(t, config, p)
	return config, p
}

func writeProfile(t *testing.T, path string, p *Profile) {
	t.Helper()
	raw, err := json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
}

func TestProfileIdentityLocationAndMissingActiveLibrary(t *testing.T) {
	config, p := profileFixture(t)
	loaded, err := Load(config, p.ProgramRoot)
	if err != nil {
		t.Fatal(err)
	}
	snapshot := loaded.Snapshot("process", "implementation", 3)
	if snapshot["instance_id"] != p.InstanceID || snapshot["protocol"] != 2 || snapshot["owner_epoch"] != int64(3) {
		t.Fatalf("snapshot %+v", snapshot)
	}
	if _, err = Load(config, filepath.Join(p.DevelopmentRoot, "other")); err == nil {
		t.Fatal("wrong source root accepted")
	}
	pointer := filepath.Join(p.UserRoot, "config", "locations.json")
	os.MkdirAll(filepath.Dir(pointer), 0700)
	raw, _ := json.Marshal(map[string]any{"schema": 1, "active_data": filepath.Join(p.UserRoot, "libraries", "missing")})
	os.WriteFile(pointer, raw, 0600)
	if _, err = Load(config, p.ProgramRoot); err == nil {
		t.Fatal("missing registered database accepted")
	}
}

func TestProfileRejectsDailyOverlapAndOutOfBoundsData(t *testing.T) {
	config, p := profileFixture(t)
	p.ProtectedRoots = []string{p.UserRoot}
	writeProfile(t, config, p)
	if _, err := Load(config, p.ProgramRoot); err == nil {
		t.Fatal("daily overlap accepted")
	}
	p.ProtectedRoots = nil
	p.DataRoot = p.ProgramRoot
	writeProfile(t, config, p)
	if _, err := Load(config, p.ProgramRoot); err == nil {
		t.Fatal("data outside development libraries accepted")
	}
}

func TestOwnerLockRejectsSecondOwnerAndReleases(t *testing.T) {
	root := t.TempDir()
	first, err := AcquireOwner(root)
	if err != nil {
		t.Fatal(err)
	}
	if second, err := AcquireOwner(root); err == nil {
		second.Close()
		t.Fatal("second owner acquired the same lock")
	}
	first.Close()
	second, err := AcquireOwner(root)
	if err != nil {
		t.Fatal(err)
	}
	second.Close()
}
