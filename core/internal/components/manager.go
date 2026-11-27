package components

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"
	"workdesk/core/internal/processowner"
)

var versionPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._+-]{0,100}$`)
var idPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9._+-]{0,180}$`)
var digestPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)
var platformPattern = regexp.MustCompile(`^(windows|linux|darwin)-(amd64|arm64)$`)
var operationPattern = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)
var locks sync.Map

type Release struct {
	Schema       int          `json:"schema"`
	ID           string       `json:"id"`
	ComponentID  string       `json:"component_id"`
	Version      string       `json:"version"`
	Platform     string       `json:"platform"`
	Path         string       `json:"path"`
	SHA256       string       `json:"sha256"`
	Entry        []string     `json:"entry"`
	State        string       `json:"state"`
	Source       string       `json:"source"`
	Sources      []Source     `json:"sources"`
	Managed      bool         `json:"managed"`
	License      string       `json:"license,omitempty"`
	PreparedAt   string       `json:"prepared_at"`
	OperationID  string       `json:"operation_id,omitempty"`
	Files        int          `json:"files"`
	Bytes        int64        `json:"bytes"`
	Dependencies []Dependency `json:"dependencies,omitempty"`
	Notes        []string     `json:"notes,omitempty"`
}
type Dependency struct {
	ID      string `json:"id"`
	Path    string `json:"path"`
	Version string `json:"version"`
	SHA256  string `json:"sha256"`
	Source  string `json:"source"`
}
type Manager struct {
	Root        string
	Client      *http.Client
	Log         func(string)
	Progress    func(Progress)
	OperationID string
	rootErr     error
}

func New(root string) *Manager {
	abs, e := filepath.Abs(root)
	if e == nil && (root == "" || !filepath.IsAbs(root) || filepath.Base(abs) != "components") {
		e = errors.New("组件根必须是显式绝对components目录")
	}
	return &Manager{Root: abs, Client: defaultClient(), rootErr: e}
}
func nonce() string { var b [8]byte; _, _ = rand.Read(b[:]); return hex.EncodeToString(b[:]) }
func (m *Manager) lock() func() {
	v, _ := locks.LoadOrStore(strings.ToLower(m.Root), &sync.Mutex{})
	mu := v.(*sync.Mutex)
	mu.Lock()
	return mu.Unlock
}
func (m *Manager) ready() error {
	if m.rootErr != nil {
		return m.rootErr
	}
	if e := plain(m.Root); e != nil {
		return e
	}
	return os.MkdirAll(m.Root, 0700)
}
func (m *Manager) note(s string) {
	if m.Log != nil {
		m.Log(s)
	}
}
func platform() string { return runtime.GOOS + "-" + runtime.GOARCH }

func (m *Manager) List() ([]Release, error) {
	if m.rootErr != nil {
		return nil, m.rootErr
	}
	if e := plain(m.Root); e != nil {
		return nil, e
	}
	records := filepath.Join(m.Root, ".records")
	entries, e := os.ReadDir(records)
	if os.IsNotExist(e) {
		return []Release{}, nil
	}
	if e != nil {
		return nil, e
	}
	result := []Release{}
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") || strings.HasSuffix(entry.Name(), ".files.json") {
			continue
		}
		var r Release
		if e = readJSON(filepath.Join(records, entry.Name()), &r); e != nil {
			return nil, e
		}
		if e = m.validate(r); e != nil {
			return nil, e
		}
		if r.State != "REMOVED" {
			if _, e = os.Stat(r.Path); os.IsNotExist(e) {
				r.State = "MISSING"
			} else if e != nil {
				return nil, e
			}
		}
		result = append(result, r)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].ID < result[j].ID })
	return result, nil
}
func (m *Manager) validate(r Release) error {
	if r.Schema != 1 || !idPattern.MatchString(r.ID) || !versionPattern.MatchString(r.Version) || !r.Managed || r.ComponentID == "" || !digestPattern.MatchString(r.SHA256) || !platformPattern.MatchString(r.Platform) {
		return errors.New("组件发布记录身份无效")
	}
	if _, ok := partner(r.ComponentID); !ok {
		return errors.New("组件记录不是已登记官方端")
	}
	expected := filepath.Join(m.Root, r.ComponentID, r.Version+"-"+r.Platform+"-"+r.SHA256[:16])
	if !strings.EqualFold(filepath.Clean(expected), filepath.Clean(r.Path)) {
		return errors.New("组件发布位置不属于该管理根")
	}
	for _, entry := range r.Entry {
		if !filepath.IsAbs(entry) {
			return errors.New("组件入口必须是明确绝对路径")
		}
		inRelease, err := filepath.Rel(r.Path, entry)
		inTools, toolErr := filepath.Rel(filepath.Join(m.Root, ".tools"), entry)
		belongs := func(value string, err error) bool {
			return err == nil && value != "." && value != ".." && !strings.HasPrefix(value, ".."+string(os.PathSeparator))
		}
		if !belongs(inRelease, err) && !belongs(inTools, toolErr) {
			return errors.New("组件入口越出该发布及其受管依赖")
		}
	}
	return plain(r.Path)
}
func (m *Manager) Get(id string) (Release, error) {
	if m.rootErr != nil {
		return Release{}, m.rootErr
	}
	if !idPattern.MatchString(id) {
		return Release{}, errors.New("组件发布ID无效")
	}
	var r Release
	e := readJSON(filepath.Join(m.Root, ".records", id+".json"), &r)
	if e == nil {
		e = m.validate(r)
	}
	return r, e
}

// OperationRelease reads the exact original prepare receipt without selecting
// another version or replaying package scripts. The caller must Verify(r.ID)
// before resolving an unknown operation as locally complete.
func (m *Manager) OperationRelease(operationID string) (Release, error) {
	if m.rootErr != nil {
		return Release{}, m.rootErr
	}
	if !operationPattern.MatchString(operationID) {
		return Release{}, errors.New("组件操作需要准确UUID")
	}
	var r Release
	if err := readJSON(filepath.Join(m.Root, ".operations", operationID, "release.json"), &r); err != nil {
		return Release{}, err
	}
	if r.OperationID != operationID {
		return Release{}, errors.New("组件操作回执身份不符")
	}
	if err := m.validate(r); err != nil {
		return Release{}, err
	}
	return r, nil
}

// Verify hashes the immutable program tree explicitly. List stays metadata-only
// so opening settings never scans tens of thousands of dependency files.
func (m *Manager) Verify(ctx context.Context, id string) (Release, error) {
	r, e := m.Get(id)
	if e != nil {
		return r, e
	}
	if r.State != "AVAILABLE" {
		return r, errors.New("组件版本当前不可用")
	}
	if e = ctx.Err(); e != nil {
		return r, e
	}
	_, digest, e := tree(r.Path)
	if e == nil && digest != r.SHA256 {
		e = errors.New("组件程序已变化；拒绝按旧版本启动")
	}
	for _, dependency := range r.Dependencies {
		if e != nil {
			break
		}
		if err := plain(dependency.Path); err != nil {
			return r, err
		}
		actual, err := fileSHA(dependency.Path)
		if err != nil {
			return r, err
		}
		if actual != dependency.SHA256 {
			return r, errors.New("组件共享运行依赖已变化；拒绝按旧版本启动")
		}
	}
	return r, e
}

func (m *Manager) Prepare(ctx context.Context, componentID, version string) (Release, error) {
	return m.PrepareSelected(ctx, componentID, version, "", "")
}

// PrepareSelected keeps online preparation and authenticated local import behind
// the same component manager, staging transaction, immutable records and owner.
func (m *Manager) PrepareSelected(ctx context.Context, componentID, version, expectedRelease, bundle string) (Release, error) {
	pin, pinned := bundlePins[componentID]
	if bundle != "" && expectedRelease == "" && (version == "" || version == "latest") {
		version, expectedRelease = pin.version, pin.release
	}
	if (expectedRelease != "" || bundle != "") && (!pinned || pin.release != expectedRelease || pin.version != version || platform() != "windows-amd64") {
		return Release{}, errors.New("此组件不符合本次发行的固定版本")
	}
	unlock := m.lock()
	defer unlock()
	if m.OperationID != "" && !operationPattern.MatchString(m.OperationID) {
		return Release{}, errors.New("组件操作需要准确UUID")
	}
	if e := m.ready(); e != nil {
		return Release{}, e
	}
	p, ok := partner(componentID)
	if !ok {
		return Release{}, errors.New("请选择八位伙伴的官方组件")
	}
	if version != "" && !versionPattern.MatchString(version) {
		return Release{}, errors.New("组件版本无效")
	}
	if e := ctx.Err(); e != nil {
		return Release{}, e
	}
	stageBase := filepath.Join(m.Root, ".staging")
	if e := plain(stageBase); e != nil {
		return Release{}, e
	}
	if e := os.MkdirAll(stageBase, 0700); e != nil {
		return Release{}, e
	}
	stage, e := os.MkdirTemp(stageBase, componentID+"-")
	if e != nil {
		return Release{}, e
	}
	// Failed staging is retained with its exact operation evidence. A new Prepare
	// never replays a previous package script or deletes that interrupted work.
	program := filepath.Join(stage, "program")
	if e = os.MkdirAll(program, 0700); e != nil {
		return Release{}, e
	}
	r := Release{Schema: 1, ComponentID: componentID, Platform: platform(), Managed: true, State: "AVAILABLE", PreparedAt: time.Now().UTC().Format(time.RFC3339), OperationID: m.OperationID, Sources: []Source{}, Entry: []string{}}
	m.note("正在准备 " + p.Endpoint)
	m.progress("prepare", 0, 0)
	if bundle != "" {
		e = m.unpackPartner(ctx, bundle, pin, stage, program, &r)
	} else {
		switch componentID {
		case "grok":
			e = m.prepareGrok(ctx, version, program, &r)
		case "kimi":
			e = m.prepareKimi(ctx, version, program, &r)
		case "glm":
			e = m.prepareZCode(ctx, version, program, &r)
		default:
			e = m.prepareNPM(ctx, p, version, program, &r)
		}
	}
	if e != nil {
		_ = writeJSON(filepath.Join(stage, "failed.json"), map[string]any{"component_id": componentID, "operation_id": m.OperationID, "at": time.Now().UTC().Format(time.RFC3339), "error": e.Error(), "no_model_request": true})
		return Release{}, fmt.Errorf("%s 准备失败：%w", p.Name, e)
	}
	if e = ctx.Err(); e != nil {
		return Release{}, e
	}
	var verifyTotal int64
	if bundle != "" {
		verifyTotal = pin.componentBytes
	}
	m.progress("verify_files", 0, verifyTotal)
	files, digest, e := treeWithProgress(program, ctx, func(done int64) {
		m.progress("verify_files", done, verifyTotal)
	})
	if e != nil {
		return Release{}, e
	}
	r.SHA256 = digest
	r.Files = len(files)
	for _, f := range files {
		r.Bytes += f.Bytes
	}
	r.ID = componentID + "-" + r.Version + "-" + r.Platform + "-" + digest[:16]
	if expectedRelease != "" && r.ID != expectedRelease {
		return Release{}, errors.New("官方源的准备结果与验收版本不同；请导入此版本的伙伴补充包")
	}
	r.Path = filepath.Join(m.Root, componentID, r.Version+"-"+r.Platform+"-"+digest[:16])
	for i, arg := range r.Entry {
		if arg == program || strings.HasPrefix(arg, program+string(os.PathSeparator)) {
			rel, _ := filepath.Rel(program, arg)
			r.Entry[i] = filepath.Join(r.Path, rel)
		}
	}
	if e = m.validate(r); e != nil {
		return Release{}, e
	}
	m.progress("publish", 0, 0)
	if e = os.MkdirAll(filepath.Dir(r.Path), 0700); e != nil {
		return Release{}, e
	}
	if _, e = os.Stat(r.Path); e == nil {
		_, existing, e := tree(r.Path)
		if e != nil || existing != digest {
			return Release{}, errors.New("同一发布位置已存在不同文件；未覆盖")
		}
		if e = m.safeRemoveStage(program); e != nil {
			return Release{}, e
		}
	} else if os.IsNotExist(e) {
		if e = os.Rename(program, r.Path); e != nil {
			return Release{}, e
		}
	} else {
		return Release{}, e
	}
	if e = writeJSON(filepath.Join(m.Root, ".records", r.ID+".files.json"), files); e != nil {
		return Release{}, e
	}
	if e = writeJSON(filepath.Join(m.Root, ".records", r.ID+".json"), r); e != nil {
		return Release{}, e
	}
	operationDir := filepath.Join(m.Root, ".operations", r.ID)
	if e = writeJSON(filepath.Join(operationDir, "prepare.json"), r); e != nil {
		return Release{}, e
	}
	if m.OperationID != "" {
		if e = writeJSON(filepath.Join(m.Root, ".operations", m.OperationID, "release.json"), r); e != nil {
			return Release{}, e
		}
	}
	if _, err := os.Stat(filepath.Join(stage, "install.log")); err == nil {
		if e = copyFile(filepath.Join(stage, "install.log"), filepath.Join(operationDir, "install-"+nonce()+".log"), 0600); e != nil {
			return Release{}, e
		}
	}
	// Successful expansion scratch is not a user record. Keep the exact source
	// archive in the shared verified download cache and the operation receipt,
	// then remove only this checked staging directory (never failed operations).
	if e = m.safeRemoveStage(stage); e != nil {
		return Release{}, e
	}
	return r, nil
}

func (m *Manager) safeRemoveStage(path string) error {
	rel, e := filepath.Rel(filepath.Join(m.Root, ".staging"), path)
	if e != nil || rel == "." || rel == ".." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
		return errors.New("暂存清理目标越界")
	}
	if e = plain(path); e != nil {
		return e
	}
	return os.RemoveAll(path)
}

// Remove requires the caller to hold the core's reference/admission guard. It
// removes only this release's program files, retaining provenance and history.
func (m *Manager) Remove(ctx context.Context, id string) error {
	unlock := m.lock()
	defer unlock()
	if e := m.ready(); e != nil {
		return e
	}
	r, e := m.Get(id)
	if e != nil {
		return e
	}
	if r.State == "REMOVED" {
		return nil
	}
	if e = ctx.Err(); e != nil {
		return e
	}
	if _, e = os.Stat(r.Path); e == nil {
		if e = m.validate(r); e != nil {
			return e
		}
		trash := filepath.Join(m.Root, ".trash", r.ID+"-"+nonce())
		if e = plain(trash); e != nil {
			return e
		}
		if e = os.MkdirAll(filepath.Dir(trash), 0700); e != nil {
			return e
		}
		if e = os.Rename(r.Path, trash); e != nil {
			return fmt.Errorf("组件可能仍被使用，未移除: %w", e)
		}
		if e = os.RemoveAll(trash); e != nil {
			return e
		}
	} else if !os.IsNotExist(e) {
		return e
	}
	r.State = "REMOVED"
	return writeJSON(filepath.Join(m.Root, ".records", id+".json"), r)
}

type limitedBuffer struct {
	bytes.Buffer
	limit     int
	truncated bool
}

func (b *limitedBuffer) Write(p []byte) (int, error) {
	n := len(p)
	if b.Len()+n > b.limit {
		b.truncated = true
	}
	if b.Len() < b.limit {
		take := b.limit - b.Len()
		if take > n {
			take = n
		}
		_, _ = b.Buffer.Write(p[:take])
	}
	return n, nil
}

func run(ctx context.Context, argv []string, dir string, env []string) (string, error) {
	if len(argv) == 0 || !filepath.IsAbs(argv[0]) {
		return "", errors.New("组件命令必须使用绝对入口")
	}
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	cmd.Dir = dir
	cmd.Env = env
	out := &limitedBuffer{limit: 8 << 20}
	cmd.Stdout = out
	cmd.Stderr = out
	owned, e := processowner.Start(cmd)
	if e != nil {
		return out.String(), e
	}
	defer owned.Close()
	e = owned.Wait()
	if !owned.ExitConfirmed() {
		return out.String(), errors.New("组件准备或诊断进程退出未知")
	}
	if out.truncated {
		return out.String(), errors.New("组件诊断输出超过8MiB，拒绝使用截断清单")
	}
	return out.String(), e
}

func environment(home string, extra map[string]string) []string {
	allow := map[string]bool{"SYSTEMROOT": true, "WINDIR": true, "COMSPEC": true, "PATH": true, "PATHEXT": true, "TEMP": true, "TMP": true, "PROGRAMFILES": true, "PROGRAMFILES(X86)": true, "PROGRAMDATA": true, "PROCESSOR_ARCHITECTURE": true, "NUMBER_OF_PROCESSORS": true, "HTTP_PROXY": true, "HTTPS_PROXY": true, "NO_PROXY": true}
	values := map[string]string{}
	for _, v := range os.Environ() {
		p := strings.SplitN(v, "=", 2)
		if len(p) == 2 && allow[strings.ToUpper(p[0])] {
			values[strings.ToUpper(p[0])] = p[1]
		}
	}
	values["HOME"] = home
	values["USERPROFILE"] = home
	values["APPDATA"] = filepath.Join(home, "AppData", "Roaming")
	values["LOCALAPPDATA"] = filepath.Join(home, "AppData", "Local")
	values["CI"] = "1"
	values["NO_COLOR"] = "1"
	values["DO_NOT_TRACK"] = "1"
	for k, v := range extra {
		values[strings.ToUpper(k)] = v
	}
	result := make([]string, 0, len(values))
	for k, v := range values {
		result = append(result, k+"="+v)
	}
	return result
}

func (m *Manager) Inspect(ctx context.Context, id string) (map[string]any, error) {
	r, e := m.Verify(ctx, id)
	if e != nil {
		return nil, e
	}
	home, e := os.MkdirTemp(filepath.Join(m.Root, ".staging"), "inspect-")
	if e != nil {
		return nil, e
	}
	defer m.safeRemoveStage(home)
	for _, name := range []string{"codex", "dsh", "kimi", "claude", "AppData/Local", "AppData/Roaming"} {
		if e := os.MkdirAll(filepath.Join(home, filepath.FromSlash(name)), 0700); e != nil {
			return nil, e
		}
	}
	args := append(append([]string{}, r.Entry...), "--version")
	output, e := run(ctx, args, home, environment(home, map[string]string{"DSH_HOME": filepath.Join(home, "dsh"), "CODEX_HOME": filepath.Join(home, "codex"), "KIMI_CODE_HOME": filepath.Join(home, "kimi"), "ZCODE_DATA_BASE_DIR": home, "GEMINI_CLI_HOME": home, "CLAUDE_CONFIG_DIR": filepath.Join(home, "claude"), "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1"}))
	result := map[string]any{"release": r, "version_output": strings.TrimSpace(output), "exit_confirmed": true, "model_requests": 0, "credentials_used": false, "component_integrity": "VERIFIED"}
	if e != nil {
		result["state"] = "DIAGNOSTIC_FAILED"
		result["error"] = e.Error()
		return result, e
	}
	result["state"] = "LOCAL_READY"
	return result, nil
}

func locate(root, base string) (string, error) {
	found := []string{}
	e := filepath.WalkDir(root, func(p string, d os.DirEntry, e error) error {
		if e != nil {
			return e
		}
		if !d.IsDir() && strings.EqualFold(d.Name(), base) {
			found = append(found, p)
		}
		return nil
	})
	if e != nil {
		return "", e
	}
	if len(found) != 1 {
		return "", fmt.Errorf("官方组件入口 %s 数量为%d，未猜选", base, len(found))
	}
	return found[0], nil
}

var _ io.Writer = (*limitedBuffer)(nil)
var _ = json.RawMessage{}
