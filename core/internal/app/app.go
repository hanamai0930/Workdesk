package app

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"workdesk/core/internal/bridge"
	"workdesk/core/internal/instance"
	"workdesk/core/internal/storage"
)

type App struct {
	Profile                         *instance.Profile
	DB                              *storage.Store
	Legacy                          *bridge.Peer
	Process, Implementation, Source string
	SourceDirty                     bool
	Epoch                           int64
	Control, Viewer, UIToken        string
	mu                              sync.Mutex
	admission                       sync.Mutex
	retiring                        atomic.Bool
	sourceFiles                     map[string]string
	sessions                        map[string]*storage.Session
	processes                       map[string]map[string]any
	jobs                            map[string]*runningJob
	partnerProcesses                map[string]*partnerProcess
	Stop                            chan struct{}
	stopOnce                        sync.Once
	Log                             io.Writer
	UIOrigin                        string
}

func Token(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return base64.RawURLEncoding.EncodeToString(b)
}
func ID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	b[6] = (b[6] & 15) | 64
	b[8] = (b[8] & 63) | 128
	s := hex.EncodeToString(b)
	return s[:8] + "-" + s[8:12] + "-" + s[12:16] + "-" + s[16:20] + "-" + s[20:]
}
func Decode(raw []byte, out any) error {
	d := json.NewDecoder(strings.NewReader(string(raw)))
	d.UseNumber()
	return d.Decode(out)
}
func params(raw json.RawMessage) (map[string]any, error) {
	m := map[string]any{}
	err := Decode(raw, &m)
	return m, err
}
func str(v any) string { s, _ := v.(string); return s }
func number(v any) int64 {
	switch n := v.(type) {
	case json.Number:
		i, _ := n.Int64()
		return i
	case int64:
		return n
	case float64:
		return int64(n)
	case int:
		return int64(n)
	}
	return 0
}
func normalize(v any) any {
	switch x := v.(type) {
	case json.Number:
		return x // storage validates signed integers before any floating point conversion.
	case map[string]any:
		if len(x) == 1 && x["$bytes"] != nil {
			b, _ := base64.StdEncoding.DecodeString(str(x["$bytes"]))
			return b
		}
		for k, y := range x {
			x[k] = normalize(y)
		}
	case []any:
		for k, y := range x {
			x[k] = normalize(y)
		}
	}
	return v
}
func wire(v any) any {
	switch x := v.(type) {
	case []byte:
		return map[string]any{"$bytes": base64.StdEncoding.EncodeToString(x)}
	case [][]any:
		r := make([][]any, len(x))
		for i, row := range x {
			r[i] = make([]any, len(row))
			for j, value := range row {
				r[i][j] = wire(value)
			}
		}
		return r
	}
	return v
}

func New(p *instance.Profile, db *storage.Store, implementation, source string, log io.Writer) (*App, error) {
	a := &App{Profile: p, DB: db, Process: ID(), Implementation: implementation, Source: source, Control: Token(32), Viewer: Token(32), UIToken: Token(24), sessions: map[string]*storage.Session{}, processes: map[string]map[string]any{}, jobs: map[string]*runningJob{}, partnerProcesses: map[string]*partnerProcess{}, Stop: make(chan struct{}), Log: log}
	if err := a.loadSources(); err != nil {
		return nil, err
	}
	freshRelease := p.Channel == "release" && db.InitiallyEmpty()
	if err := db.InitializeCore(); err != nil {
		return nil, err
	}
	if freshRelease {
		if err := db.MigratePartners(); err != nil {
			return nil, err
		}
	}
	if p.Channel == "release" {
		if version, err := db.Schema(); err != nil || version != "3" {
			return nil, errors.New("installed database requires an explicit backed-up migration")
		}
	}
	epoch, err := db.AdvanceOwnerEpoch()
	if err != nil {
		return nil, err
	}
	a.Epoch = epoch
	if err = a.prepareTables(); err != nil {
		return nil, err
	}
	if err = a.recoverPartners(); err != nil {
		return nil, err
	}
	env := bridge.Environment(map[string]string{"WORKDESK_CORE_BRIDGE": "1", "WORKDESK_HOME": p.UserRoot, "WORKDESK_DATA_DIR": p.DataRoot, "WORKDESK_INSTANCE_CONFIG": p.ConfigPath, "PYTHONPATH": a.pythonSources()})
	a.Legacy, err = bridge.Start(p.PythonExecutable, p.ProgramRoot, env, log, a.callback)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	var initialized any
	err = a.Legacy.Call(ctx, "legacy.initialize", map[string]any{"base": p.ProgramRoot, "data": p.DataRoot, "user": p.UserRoot, "process_instance": a.Process, "implementation": implementation, "owner_epoch": a.Epoch, "profile": p}, &initialized)
	if err != nil {
		a.Legacy.Close()
		return nil, fmt.Errorf("初始化兼容业务: %w", err)
	}
	go a.watchBridge()
	go a.tick()
	return a, nil
}
func (a *App) Shutdown() { a.stopOnce.Do(func() { close(a.Stop) }) }
func (a *App) watchBridge() {
	select {
	case <-a.Legacy.Done():
		a.Shutdown()
	case <-a.Stop:
	}
}
func (a *App) tick() {
	t := time.NewTicker(time.Second)
	defer t.Stop()
	for {
		select {
		case <-a.Stop:
			return
		case <-t.C:
			if a.verifySources() != nil {
				continue
			}
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			var result any
			err := a.Legacy.Call(ctx, "legacy.tick", map[string]any{}, &result)
			cancel()
			if err != nil {
				fmt.Fprintln(a.Log, "legacy tick:", err)
			}
		}
	}
}
func (a *App) Close() {
	a.Shutdown()
	if a.Legacy != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		_ = a.Legacy.Call(ctx, "legacy.close", map[string]any{}, nil)
		cancel()
		a.Legacy.Close()
	}
	a.mu.Lock()
	for _, p := range a.partnerProcesses {
		p.cancel()
	}
	for _, j := range a.jobs {
		if j.cancel != nil {
			j.cancel()
		}
	}
	for id, s := range a.sessions {
		_ = s.Rollback()
		delete(a.sessions, id)
	}
	a.mu.Unlock()
}
func (a *App) prepareTables() error {
	return a.write(func(s *storage.Session) error {
		for _, q := range []string{
			`CREATE TABLE IF NOT EXISTS core_tool_jobs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,state TEXT NOT NULL,data TEXT NOT NULL,updated REAL NOT NULL)`,
			`CREATE TABLE IF NOT EXISTS core_command_metadata(command_id TEXT PRIMARY KEY,codec TEXT NOT NULL)`,
			`CREATE TABLE IF NOT EXISTS core_process_registry(id TEXT PRIMARY KEY,data TEXT NOT NULL)`} {
			if _, e := s.Execute(q, nil); e != nil {
				return e
			}
		}
		_, e := s.Execute(`UPDATE core_tool_jobs SET state='ORPHANED_UNKNOWN' WHERE state IN ('QUEUED','RUNNING','STOP_REQUESTED')`, nil)
		if e != nil {
			return e
		}
		tables, e := s.Execute(`SELECT name FROM sqlite_master WHERE type='table' AND name='attempts'`, nil)
		if e != nil || len(tables.Rows) == 0 {
			return e
		}
		rows, e := s.Execute(`SELECT id,task_id,worker_instance FROM attempts WHERE state IN ('RESERVED','SEND_INTENT','RUNNING','STOP_REQUESTED','WAITING_APPROVAL','WAITING_INPUT')`, nil)
		if e != nil {
			return e
		}
		for _, row := range rows.Rows {
			attempt, task, old := row[0], row[1], row[2]
			for _, statement := range []struct {
				q    string
				args []any
			}{
				{`UPDATE attempts SET state='ORPHANED_UNKNOWN' WHERE id=?`, []any{attempt}},
				{`UPDATE tasks SET state='ORPHANED_UNKNOWN',updated=? WHERE id=?`, []any{now(), task}},
				{`UPDATE approvals SET state='CANCELLED',resolved=? WHERE attempt_id=? AND state='PENDING'`, []any{now(), attempt}},
				{`UPDATE native_requests SET state='CANCELLED',resolved=? WHERE attempt_id=? AND state IN ('PENDING','READY','REPLY_INTENT')`, []any{now(), attempt}},
			} {
				if _, e = s.Execute(statement.q, statement.args); e != nil {
					return e
				}
			}
			payload, _ := json.Marshal(map[string]any{"old_worker_instance": old, "no_resend": true, "core_engine": "go"})
			if _, e = s.Execute(`INSERT INTO events(at,task_id,attempt_id,kind,payload) VALUES(?,?,?,?,?)`, []any{now(), task, attempt, "restart_requires_reconciliation", string(payload)}); e != nil {
				return e
			}
		}
		return nil
	})
}
func (a *App) write(fn func(*storage.Session) error) error {
	s, e := a.DB.OpenSession(true)
	if e != nil {
		return e
	}
	defer s.Rollback()
	if e = fn(s); e != nil {
		return e
	}
	return s.Commit()
}
func (a *App) query(q string, args ...any) ([]map[string]any, error) {
	s, e := a.DB.OpenSession(false)
	if e != nil {
		return nil, e
	}
	defer s.Rollback()
	r, e := s.Execute(q, args)
	if e != nil {
		return nil, e
	}
	rows := make([]map[string]any, 0, len(r.Rows))
	for _, row := range r.Rows {
		m := map[string]any{}
		for i, k := range r.Columns {
			m[k] = row[i]
		}
		rows = append(rows, m)
	}
	return rows, nil
}
func (a *App) callback(method string, raw json.RawMessage) (any, error) {
	p, e := params(raw)
	if e != nil {
		return nil, e
	}
	if str(p["process_instance"]) != a.Process || number(p["owner_epoch"]) != a.Epoch {
		return nil, errors.New("旧运行身份或所有者代次；拒绝操作")
	}
	switch method {
	case "partner.task.start", "partner.task.poll", "partner.task.stop":
		return a.partnerTask(method, p)
	case "db.begin":
		s, e := a.DB.OpenSession(p["write"] == true)
		if e != nil {
			return nil, sqlError(e)
		}
		id := ID()
		a.mu.Lock()
		a.sessions[id] = s
		a.mu.Unlock()
		return map[string]any{"session": id}, nil
	case "db.execute", "db.commit", "db.rollback":
		id := str(p["session"])
		a.mu.Lock()
		s := a.sessions[id]
		if method != "db.execute" {
			delete(a.sessions, id)
		}
		a.mu.Unlock()
		if s == nil {
			return nil, errors.New("存储事务已结束")
		}
		switch method {
		case "db.execute":
			args, _ := normalize(p["args"]).([]any)
			r, e := s.Execute(str(p["sql"]), args)
			if e != nil {
				return nil, sqlError(e)
			}
			return map[string]any{"columns": r.Columns, "rows": wire(r.Rows), "lastrowid": r.LastRowID, "rowcount": r.RowCount, "total_changes": r.TotalChanges, "in_transaction": r.InTransaction}, nil
		case "db.commit":
			return map[string]any{"ok": true}, sqlError(s.Commit())
		default:
			return map[string]any{"ok": true}, sqlError(s.Rollback())
		}
	case "db.backup":
		target := str(p["target"])
		if !inside(target, a.Profile.UserRoot) {
			return nil, errors.New("备份目标越出开发资料根")
		}
		return map[string]any{"ok": true}, sqlError(a.DB.Backup(target))
	case "process.intent":
		id := ID()
		record := map[string]any{"id": id, "state": "INTENT", "command": p["command"], "owner_epoch": a.Epoch, "process_instance": a.Process, "created": float64(time.Now().UnixMilli()) / 1000}
		a.mu.Lock()
		a.processes[id] = record
		a.mu.Unlock()
		return map[string]any{"id": id}, nil
	case "process.started", "process.exited":
		id := str(p["id"])
		a.mu.Lock()
		defer a.mu.Unlock()
		record := a.processes[id]
		if record == nil {
			return nil, errors.New("进程意图不存在")
		}
		for k, v := range p {
			if k != "command" {
				record[k] = v
			}
		}
		record["state"] = "RUNNING"
		if method == "process.exited" {
			record["state"] = "EXITED"
		}
		return map[string]any{"ok": true}, nil
	}
	return nil, fmt.Errorf("未知内部调用 %s", method)
}
func sqlError(err error) error {
	if err == nil {
		return nil
	}
	var db *storage.DBError
	if errors.As(err, &db) {
		return &bridge.RPCError{Message: err.Error(), Type: "OperationalError", Code: db.Code}
	}
	return err
}
func inside(path, root string) bool {
	path, e := instance.ResolvePath(path)
	if e != nil {
		return false
	}
	root, e = instance.ResolvePath(root)
	if e != nil {
		return false
	}
	rel, e := filepath.Rel(root, path)
	if e != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
		return false
	}
	for parent := path; parent != filepath.Dir(parent); parent = filepath.Dir(parent) {
		info, e := os.Lstat(parent)
		if e == nil && info.Mode()&os.ModeSymlink != 0 {
			return false
		}
		if strings.EqualFold(parent, root) {
			break
		}
	}
	return true
}
func (a *App) Identity() map[string]any {
	schema, _ := a.DB.Schema()
	schemaNumber, _ := strconv.Atoi(schema)
	return map[string]any{"schema": 1, "channel": a.Profile.Channel, "instance_id": a.Profile.InstanceID, "process_instance": a.Process, "program_root": a.Profile.ProgramRoot, "user_root": a.Profile.UserRoot, "data_root": a.Profile.DataRoot, "source_revision": a.Source, "source_dirty": a.SourceDirty, "implementation_revision": a.Implementation, "protocol": 2, "core_engine": "go", "storage_schema": schemaNumber, "owner_epoch": a.Epoch, "legacy_bridge_protocol": 1, "default_project_id": a.Profile.DefaultProjectID}
}
func (a *App) legacyRequest(ctx context.Context, method, path string, body any) (int, any, error) {
	var result struct {
		Status   int  `json:"status"`
		Body     any  `json:"body"`
		Retiring bool `json:"retiring"`
	}
	err := a.Legacy.Call(ctx, "legacy.request", map[string]any{"method": method, "path": path, "body": body}, &result)
	if result.Retiring {
		a.retiring.Store(true)
		go func() { time.Sleep(200 * time.Millisecond); a.Shutdown() }()
	}
	return result.Status, result.Body, err
}
func bounded(q string, def, max int) int {
	n, e := strconv.Atoi(q)
	if e != nil || n < 1 {
		return def
	}
	if n > max {
		return max
	}
	return n
}

func (a *App) pythonSources() string {
	path := filepath.Join(a.Profile.DataRoot, "implementations", a.Implementation, "src")
	if runtime.GOOS == "windows" && !strings.HasPrefix(path, `\\?\`) {
		return `\\?\` + path
	}
	return path
}
func response(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
func failed(w http.ResponseWriter, err error) {
	response(w, 409, map[string]any{"error": err.Error(), "state": "REJECTED", "explicit_rejection": true, "no_dispatch": true})
}
func uncertain(w http.ResponseWriter, err error) {
	response(w, 503, map[string]any{"error": err.Error(), "state": "UNKNOWN", "no_resend": true})
}
