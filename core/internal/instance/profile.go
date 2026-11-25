// Package instance validates the development identity before opening its data.
package instance

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
)

type Profile struct {
	Schema           int      `json:"schema"`
	Channel          string   `json:"channel"`
	InstanceID       string   `json:"instance_id"`
	DevelopmentRoot  string   `json:"development_root"`
	ProgramRoot      string   `json:"program_root"`
	UserRoot         string   `json:"user_root"`
	DataRoot         string   `json:"data_root"`
	PythonExecutable string   `json:"python_executable"`
	UIPort           int      `json:"ui_port"`
	SyncBranch       string   `json:"sync_branch"`
	DefaultProjectID string   `json:"default_project_id"`
	ProtectedRoots   []string `json:"protected_roots"`
	InitialDataRoot  string   `json:"-"`
	ConfigPath       string   `json:"-"`
}

func readJSON(path string, value any) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return errors.New("configuration must be a regular file")
	}
	if info.Size() > 1024*1024 {
		return errors.New("configuration exceeds one MiB")
	}
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	d := json.NewDecoder(io.LimitReader(f, 1024*1024+1))
	if err = d.Decode(value); err != nil {
		return err
	}
	var trailing any
	if d.Decode(&trailing) != io.EOF {
		return errors.New("configuration has trailing data")
	}
	return nil
}

func resolvePath(value, field string) (string, error) {
	if !filepath.IsAbs(value) {
		return "", fmt.Errorf("%s requires an absolute path", field)
	}
	value = filepath.Clean(value)
	probe := value
	tail := []string{}
	for {
		resolved, err := filepath.EvalSymlinks(probe)
		if err == nil {
			for i := len(tail) - 1; i >= 0; i-- {
				resolved = filepath.Join(resolved, tail[i])
			}
			return filepath.Clean(resolved), nil
		}
		if !os.IsNotExist(err) {
			return "", fmt.Errorf("resolve %s: %w", field, err)
		}
		parent := filepath.Dir(probe)
		if parent == probe {
			return "", fmt.Errorf("resolve %s: %w", field, err)
		}
		tail = append(tail, filepath.Base(probe))
		probe = parent
	}
}

func comparable(path string) string {
	path = filepath.Clean(path)
	if runtime.GOOS == "windows" {
		return strings.ToLower(path)
	}
	return path
}

func SamePath(left, right string) bool { return comparable(left) == comparable(right) }

// ResolvePath resolves existing directory links, including when the final file
// has not been created yet. Callers must resolve paths before containment checks.
func ResolvePath(path string) (string, error) { return resolvePath(path, "path") }

func Within(path, parent string) bool {
	rel, err := filepath.Rel(comparable(parent), comparable(path))
	return err == nil && rel != "." && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel)
}

func overlap(a, b string) bool { return SamePath(a, b) || Within(a, b) || Within(b, a) }

func (p *Profile) validate() error {
	if p.Schema != 1 || p.Channel != "development" {
		return errors.New("unsupported development instance format or channel")
	}
	if !regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`).MatchString(p.InstanceID) {
		return errors.New("invalid stable instance identity")
	}
	fields := map[string]*string{"development_root": &p.DevelopmentRoot, "program_root": &p.ProgramRoot, "user_root": &p.UserRoot, "data_root": &p.DataRoot, "python_executable": &p.PythonExecutable}
	for name, field := range fields {
		resolved, err := resolvePath(*field, name)
		if err != nil {
			return err
		}
		*field = resolved
	}
	if !Within(p.ProgramRoot, p.DevelopmentRoot) || !Within(p.UserRoot, p.DevelopmentRoot) {
		return errors.New("development program and user roots must stay inside the registered development root")
	}
	if overlap(p.ProgramRoot, p.UserRoot) {
		return errors.New("program and user roots overlap")
	}
	if !Within(p.DataRoot, filepath.Join(p.UserRoot, "libraries")) {
		return errors.New("development database is outside the registered libraries directory")
	}
	if p.UIPort < 1024 || p.UIPort > 65535 {
		return errors.New("invalid development UI port")
	}
	if p.SyncBranch != "codex/v2-dev" {
		return errors.New("development publishing must target codex/v2-dev")
	}
	protected := append([]string(nil), p.ProtectedRoots...)
	if home, err := os.UserHomeDir(); err == nil {
		protected = append(protected, filepath.Join(home, ".workdesk"))
	}
	if local := os.Getenv("LOCALAPPDATA"); local != "" {
		protected = append(protected, filepath.Join(local, "Workdesk"))
	}
	// Only location registries are inspected. Daily databases and credentials are
	// never opened to decide whether a development path is safe.
	for _, root := range append([]string(nil), protected...) {
		resolved, err := resolvePath(root, "protected_root")
		if err != nil {
			return err
		}
		pointer := filepath.Join(resolved, "config", "locations.json")
		if _, err = os.Stat(pointer); err == nil {
			var locations struct {
				ActiveData   string `json:"active_data"`
				PreviousData string `json:"previous_data"`
			}
			if err = readJSON(pointer, &locations); err != nil {
				return fmt.Errorf("protected location registry: %w", err)
			}
			if locations.ActiveData != "" {
				protected = append(protected, locations.ActiveData)
			}
			if locations.PreviousData != "" {
				protected = append(protected, locations.PreviousData)
			}
		} else if !os.IsNotExist(err) {
			return err
		}
	}
	for _, root := range protected {
		resolved, err := resolvePath(root, "protected_root")
		if err != nil {
			return err
		}
		for _, candidate := range []string{p.ProgramRoot, p.UserRoot, p.DataRoot} {
			if overlap(candidate, resolved) {
				return errors.New("development location overlaps a protected daily location")
			}
		}
	}
	return nil
}

// Load reads only configuration and location metadata; it creates no directory
// and starts no process. The configured source root is checked against the caller.
func Load(configPath, programRoot string) (*Profile, error) {
	config, err := resolvePath(configPath, "instance_config")
	if err != nil {
		return nil, err
	}
	var p Profile
	if err = readJSON(config, &p); err != nil {
		return nil, fmt.Errorf("instance configuration unavailable; no fallback: %w", err)
	}
	p.ConfigPath = config
	if err = p.validate(); err != nil {
		return nil, err
	}
	program, err := resolvePath(programRoot, "program_root")
	if err != nil {
		return nil, err
	}
	if !SamePath(program, p.ProgramRoot) {
		return nil, errors.New("instance configuration belongs to another source root")
	}
	p.InitialDataRoot = p.DataRoot
	pointer := filepath.Join(p.UserRoot, "config", "locations.json")
	if _, err = os.Stat(pointer); err == nil {
		var locations struct {
			Schema     int    `json:"schema"`
			ActiveData string `json:"active_data"`
		}
		if err = readJSON(pointer, &locations); err != nil {
			return nil, fmt.Errorf("invalid active-library pointer: %w", err)
		}
		if locations.Schema != 1 {
			return nil, errors.New("unsupported active-library pointer")
		}
		p.DataRoot, err = resolvePath(locations.ActiveData, "active_data")
		if err != nil {
			return nil, err
		}
		if err = p.validate(); err != nil {
			return nil, err
		}
		info, err := os.Stat(filepath.Join(p.DataRoot, "tasks.sqlite3"))
		if err != nil || !info.Mode().IsRegular() {
			return nil, errors.New("registered active database is missing; refusing to replace it with an empty database")
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	return &p, nil
}

// ValidateLocalMarks binds an explicitly loaded profile to this checkout's local
// Git identity. Kept separate so isolated contract tests need no Git repository.
func (p *Profile) ValidateLocalMarks() error {
	read := func(key string) (string, error) {
		cmd := exec.Command("git", "-C", p.ProgramRoot, "config", "--local", "--get", key)
		hideWindow(cmd)
		raw, err := cmd.Output()
		return strings.TrimSpace(string(raw)), err
	}
	channel, err := read("workdesk.channel")
	if err != nil || channel != p.Channel {
		return errors.New("checkout development channel marker is missing or mismatched")
	}
	config, err := read("workdesk.instanceconfig")
	if err != nil {
		return errors.New("checkout instance configuration marker is missing")
	}
	resolved, err := resolvePath(config, "instance_config")
	if err != nil || !SamePath(resolved, p.ConfigPath) {
		return errors.New("checkout refers to another instance configuration")
	}
	return nil
}

func (p *Profile) Snapshot(processInstance, implementationRevision string, epoch int64) map[string]any {
	return map[string]any{"schema": 1, "channel": p.Channel, "instance_id": p.InstanceID, "process_instance": processInstance, "program_root": p.ProgramRoot, "user_root": p.UserRoot, "data_root": p.DataRoot, "source_revision": "", "source_dirty": false, "implementation_revision": implementationRevision, "protocol": 2, "core_engine": "go", "storage_schema": 2, "owner_epoch": epoch, "legacy_bridge_protocol": 1}
}
