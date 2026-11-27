package app

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
	"workdesk/core/internal/bridge"
	"workdesk/core/internal/storage"
)

type runningJob struct{ cancel context.CancelFunc }
type Job struct {
	CommandID              string           `json:"command_id,omitempty"`
	TaskID                 string           `json:"task_id,omitempty"`
	AttemptID              string           `json:"attempt_id,omitempty"`
	SpecHash               string           `json:"spec_hash,omitempty"`
	AuthorizationID        string           `json:"authorization_id,omitempty"`
	InputVersions          []Artifact       `json:"input_versions,omitempty"`
	Checks                 []map[string]any `json:"checks,omitempty"`
	Decisions              []map[string]any `json:"decisions,omitempty"`
	ID                     string           `json:"id"`
	ProjectID              string           `json:"project_id"`
	State                  string           `json:"state"`
	Summary                string           `json:"summary,omitempty"`
	Error                  string           `json:"error,omitempty"`
	Artifacts              []Artifact       `json:"artifacts"`
	Logs                   []LogSegment     `json:"logs"`
	Created                float64          `json:"created"`
	Updated                float64          `json:"updated"`
	OwnerEpoch             int64            `json:"owner_epoch"`
	ProjectRoot            string           `json:"project_root"`
	OutputDir              string           `json:"output_dir"`
	InputFiles             []string         `json:"input_files"`
	PID                    int              `json:"pid,omitempty"`
	ProcessInstance        string           `json:"process_instance"`
	DataRoot               string           `json:"data_root"`
	ImplementationRevision string           `json:"implementation_revision"`
	ToolVersion            string           `json:"tool_version"`
	OwnedProcess           map[string]any   `json:"owned_process,omitempty"`
}
type Artifact struct {
	SourceJobID  string `json:"source_job_id,omitempty"`
	Path         string `json:"path"`
	SHA256       string `json:"sha256"`
	Bytes        int64  `json:"bytes"`
	Mime         string `json:"mime"`
	RelativePath string `json:"relative_path"`
	OriginalPath string `json:"original_path,omitempty"`
}

var uuidPattern = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)
var localToolSlot = make(chan struct{}, 1)

func now() float64 { return float64(time.Now().UnixMilli()) / 1000 }
func (a *App) toolCommand(command map[string]any) (any, error) {
	id, kind := str(command["id"]), str(command["kind"])
	p, ok := command["payload"].(map[string]any)
	if !uuidPattern.MatchString(id) || !ok {
		return nil, errors.New("命令需要准确UUID和对象参数")
	}
	raw, _ := json.Marshal(p)
	hash := sha256.Sum256(raw)
	fingerprint := hex.EncodeToString(hash[:])
	var receipt map[string]any
	fresh := false
	var job *Job
	var cancelID, taskID, attemptID string
	err := a.write(func(s *storage.Session) error {
		prior, e := s.Execute(`SELECT kind,payload_hash,receipt FROM commands WHERE id=?`, []any{id})
		if e != nil {
			return e
		}
		if len(prior.Rows) > 0 {
			row := prior.Rows[0]
			if row[0] != kind || row[1] != fingerprint {
				return errors.New("同一命令身份对应不同内容，未执行")
			}
			return json.Unmarshal([]byte(str(row[2])), &receipt)
		}
		switch kind {
		case "tool.authorize":
			receipt, e = a.createToolGrant(s, id, p)
			if e != nil {
				return e
			}
			taskID, attemptID = str(p["task_id"]), str(p["attempt_id"])
		case "tool.revoke":
			var grant toolGrant
			receipt, grant, e = a.revokeToolGrant(s, p)
			if e != nil {
				return e
			}
			taskID, attemptID = grant.TaskID, grant.AttemptID
		case "tool.decide":
			var current *Job
			receipt, current, e = a.toolDecision(s, id, p)
			if e != nil {
				return e
			}
			taskID, attemptID = current.TaskID, current.AttemptID
		case "tool.run":
			if authoritativeDocument(p) {
				job, e = a.authorizedJob(s, p)
				if e != nil {
					return e
				}
				taskID, attemptID = job.TaskID, job.AttemptID
			} else {
				project := str(p["project_id"])
				if project == "" {
					project = a.Profile.DefaultProjectID
				}
				r, e := s.Execute(`SELECT root FROM projects WHERE id=?`, []any{project})
				if e != nil {
					return e
				}
				if len(r.Rows) != 1 {
					return errors.New("请选择已登记项目")
				}
				root := str(r.Rows[0][0])
				if info, e := os.Stat(root); e != nil || !info.IsDir() {
					return errors.New("项目目录不存在")
				}
				jobID := ID()
				output := str(p["output_dir"])
				if output == "" {
					output = filepath.Join(a.Profile.DataRoot, "results", jobID)
				}
				if !filepath.IsAbs(output) || !inside(output, filepath.Join(a.Profile.DataRoot, "results")) {
					return errors.New("概要成果应保存在开发资料的results目录")
				}
				if _, e = os.Lstat(output); !os.IsNotExist(e) {
					return errors.New("成果目标已存在或无法核对，不覆盖")
				}
				files := []string{}
				if v, exists := p["input_files"]; exists {
					arr, ok := v.([]any)
					if !ok || len(arr) > 200 {
						return errors.New("材料列表无效")
					}
					for _, x := range arr {
						value, ok := x.(string)
						if !ok {
							return errors.New("材料路径无效")
						}
						name := value
						if !filepath.IsAbs(name) {
							name = filepath.Join(root, name)
						}
						if !inside(name, root) {
							return errors.New("材料不属于所选项目")
						}
						files = append(files, value)
					}
				}
				job = &Job{ID: jobID, ProjectID: project, State: "QUEUED", Artifacts: []Artifact{}, Created: now(), Updated: now(), OwnerEpoch: a.Epoch, ProjectRoot: root, OutputDir: output, InputFiles: files, ProcessInstance: a.Process, DataRoot: a.Profile.DataRoot, ImplementationRevision: a.Implementation, ToolVersion: "material-summary/1"}
			}
			job.CommandID = id
			data, _ := json.Marshal(job)
			_, e = s.Execute(`INSERT INTO core_tool_jobs VALUES(?,?,?,?,?)`, []any{job.ID, job.ProjectID, job.State, string(data), job.Updated})
			if e != nil {
				return e
			}
			receipt = map[string]any{"invocation_id": job.ID, "state": "QUEUED", "real_model_requests": 0}
		case "tool.cancel":
			cancelID = str(p["invocation_id"])
			r, e := s.Execute(`SELECT state,data FROM core_tool_jobs WHERE id=?`, []any{cancelID})
			if e != nil {
				return e
			}
			if len(r.Rows) != 1 {
				return errors.New("工具运行不存在")
			}
			state := str(r.Rows[0][0])
			if state != "QUEUED" && state != "RUNNING" && state != "STOP_REQUESTED" {
				return errors.New("工具已结束或状态待核对，不能重复停止")
			}
			var current Job
			if e = json.Unmarshal([]byte(str(r.Rows[0][1])), &current); e != nil {
				return e
			}
			if current.OwnerEpoch != a.Epoch {
				return errors.New("原工具归属不同，先核对")
			}
			taskID, attemptID = current.TaskID, current.AttemptID
			current.State = "STOP_REQUESTED"
			current.Updated = now()
			data, _ := json.Marshal(current)
			if _, e = s.Execute(`UPDATE core_tool_jobs SET state=?,data=?,updated=? WHERE id=?`, []any{current.State, string(data), current.Updated, cancelID}); e != nil {
				return e
			}
			receipt = map[string]any{"invocation_id": cancelID, "state": "STOP_REQUESTED"}
		default:
			return errors.New("工具命令不支持")
		}
		data, _ := json.Marshal(receipt)
		if _, e = s.Execute(`INSERT INTO commands VALUES(?,?,?,?,?)`, []any{id, kind, fingerprint, string(data), now()}); e != nil {
			return e
		}
		if _, e = s.Execute(`INSERT INTO core_command_metadata VALUES(?,?)`, []any{id, "core-json-v2"}); e != nil {
			return e
		}
		eventReceipt := receipt
		if kind == "tool.authorize" {
			eventReceipt = map[string]any{"authorization_id": id, "state": "AUTHORIZED"}
		}
		payload, _ := json.Marshal(map[string]any{"command_id": id, "receipt": eventReceipt, "owner_epoch": a.Epoch})
		_, e = s.Execute(`INSERT INTO events(at,task_id,attempt_id,kind,payload) VALUES(?,?,?,?,?)`, []any{now(), nullID(taskID), nullID(attemptID), kind, string(payload)})
		fresh = true
		return e
	})
	if err != nil {
		return nil, err
	}
	if fresh && job != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
		a.mu.Lock()
		a.jobs[job.ID] = &runningJob{cancel: cancel}
		a.mu.Unlock()
		go a.runTool(ctx, cancel, job)
	}
	if fresh && cancelID != "" {
		a.mu.Lock()
		if j := a.jobs[cancelID]; j != nil {
			j.cancel()
		}
		a.mu.Unlock()
	}
	return map[string]any{"receipt": receipt, "replayed": !fresh, "command_id": id}, nil
}
func (a *App) saveJob(job *Job, kind string) error {
	job.Updated = now()
	data, _ := json.Marshal(job)
	return a.write(func(s *storage.Session) error {
		if _, e := s.Execute(`UPDATE core_tool_jobs SET state=?,data=?,updated=? WHERE id=?`, []any{job.State, string(data), job.Updated, job.ID}); e != nil {
			return e
		}
		event, _ := json.Marshal(map[string]any{"invocation_id": job.ID, "state": job.State, "owner_epoch": a.Epoch})
		_, e := s.Execute(`INSERT INTO events(at,task_id,attempt_id,kind,payload) VALUES(?,?,?,?,?)`, []any{now(), nullID(job.TaskID), nullID(job.AttemptID), kind, string(event)})
		return e
	})
}
func (a *App) runTool(ctx context.Context, cancel context.CancelFunc, job *Job) {
	defer cancel()
	defer func() { a.mu.Lock(); delete(a.jobs, job.ID); a.mu.Unlock() }()
	select {
	case localToolSlot <- struct{}{}:
		defer func() { <-localToolSlot }()
	case <-ctx.Done():
		job.State = "CANCELLED"
		_ = a.saveJob(job, "tool.cancelled")
		return
	}
	a.admission.Lock()
	if ctx.Err() != nil || a.retiring.Load() {
		a.admission.Unlock()
		job.State = "CANCELLED"
		_ = a.saveJob(job, "tool.cancelled")
		return
	}
	job.State = "RUNNING"
	if e := a.verifySources(); e != nil {
		a.admission.Unlock()
		job.State = "FAILED"
		job.Error = e.Error()
		_ = a.saveJob(job, "tool.failed_no_start")
		return
	}
	if e := a.saveJob(job, "tool.started"); e != nil {
		a.admission.Unlock()
		fmt.Fprintln(a.Log, e)
		return
	}
	req := map[string]any{"project_root": job.ProjectRoot, "input_files": job.InputFiles, "output_dir": job.OutputDir}
	module := "researchos.workdesk.professional_tools.summary"
	if job.ToolVersion == documentVersion {
		frozen, cleanup, e := a.documentRequest(job)
		if e != nil {
			a.admission.Unlock()
			job.State = "FAILED"
			job.Error = e.Error()
			_ = a.saveJob(job, "tool.failed_no_start")
			return
		}
		defer cleanup()
		module = "researchos.workdesk.professional_tools.document"
		req = frozen
	}
	request, _ := json.Marshal(req)
	cmd := exec.CommandContext(ctx, a.Profile.PythonExecutable, "-B", "-X", "utf8", "-m", module)
	cmd.Dir = a.Profile.ProgramRoot
	cmd.Env = bridge.Environment(map[string]string{"PYTHONPATH": a.pythonSources(), "WORKDESK_HOME": a.Profile.UserRoot, "WORKDESK_DATA_DIR": a.Profile.DataRoot})
	cmd.Stdin = bytes.NewReader(request)
	var stdout limitedBuffer
	logRoot := filepath.Join(a.Profile.DataRoot, "results", job.ID+"-logs")
	outLog := &segmentWriter{data: a.Profile.DataRoot, root: logRoot, stream: "stdout"}
	errLog := &segmentWriter{data: a.Profile.DataRoot, root: logRoot, stream: "stderr"}
	cmd.Stdout = io.MultiWriter(&stdout, outLog)
	cmd.Stderr = errLog
	quietCommand(cmd)
	owner, err := startOwnedCommand(cmd)
	if owner != nil {
		defer owner.Close()
		job.OwnedProcess = owner.Identity()
	}
	if err == nil {
		job.PID = cmd.Process.Pid
		_ = a.saveJob(job, "tool.process_started")
	}
	a.admission.Unlock()
	if err == nil {
		err = owner.Wait()
		job.OwnedProcess = owner.Identity()
	}
	if ctx.Err() != nil {
		if owner == nil || owner.ExitConfirmed() {
			job.State = "CANCELLED"
			job.Error = "本地工具及其子进程已退出，未调用模型"
		} else {
			job.State = "STOP_UNKNOWN"
			job.Error = "本地进程退出尚未核对；未自动重发"
		}
	} else {
		var result struct {
			State     string           `json:"state"`
			Summary   string           `json:"summary"`
			Error     string           `json:"error"`
			Artifacts []Artifact       `json:"artifacts"`
			Checks    []map[string]any `json:"checks"`
		}
		decodeErr := json.Unmarshal(stdout.Bytes(), &result)
		if err != nil || decodeErr != nil || result.State != "COMPLETED" {
			job.State = "FAILED"
			job.Error = result.Error
			if job.Error == "" {
				job.Error = "工具未返回有效完成回执"
			}
		} else {
			job.State = "COMPLETED"
			job.Summary = result.Summary
			job.Artifacts = result.Artifacts
			job.Checks = result.Checks
			if len(job.Artifacts) == 0 && job.ToolVersion == documentVersion {
				job.State = "FAILED"
				job.Error = "文档工具未返回成果"
			}
			for i := range job.Artifacts {
				artifact := job.Artifacts[i]
				if e := verifyArtifact(job.OutputDir, artifact); e != nil {
					job.State = "FAILED"
					job.Error = e.Error()
					job.Artifacts = []Artifact{}
					break
				}
				relative, e := filepath.Rel(a.Profile.DataRoot, artifact.Path)
				if e != nil {
					job.State = "FAILED"
					job.Error = e.Error()
					break
				}
				job.Artifacts[i].RelativePath = filepath.ToSlash(relative)
			}
		}
	}
	logErr := outLog.Close()
	if e := errLog.Close(); logErr == nil {
		logErr = e
	}
	job.Logs = append(outLog.segments, errLog.segments...)
	if logErr != nil {
		job.State = "FAILED"
		job.Error = "日志封存未通过：" + logErr.Error()
	}
	if owner != nil && !owner.ExitConfirmed() {
		job.State = "ORPHANED_UNKNOWN"
		if ctx.Err() != nil {
			job.State = "STOP_UNKNOWN"
		}
		job.Error = "进程树退出尚未核对；保留原运行，不自动重发"
	}
	if e := a.saveJob(job, "tool.finished"); e != nil {
		fmt.Fprintln(a.Log, "tool completion requires reconciliation:", e)
	}
}

type limitedBuffer struct {
	mu sync.Mutex
	bytes.Buffer
}

func (b *limitedBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	n := len(p)
	if b.Len()+n > 2*1024*1024 {
		return 0, errors.New("工具协议输出超过2MiB")
	}
	return b.Buffer.Write(p)
}
func verifiedArtifactBytes(root string, artifact Artifact) ([]byte, error) {
	if !inside(artifact.Path, root) {
		return nil, errors.New("工具成果路径越界")
	}
	f, e := os.Open(artifact.Path)
	if e != nil {
		return nil, e
	}
	defer f.Close()
	info, e := f.Stat()
	if e != nil {
		return nil, e
	}
	if !info.Mode().IsRegular() || info.Size() != artifact.Bytes {
		return nil, errors.New("工具成果版本不一致")
	}
	raw, e := io.ReadAll(io.LimitReader(f, artifact.Bytes+1))
	if e != nil {
		return nil, e
	}
	digest := sha256.Sum256(raw)
	if int64(len(raw)) != artifact.Bytes || hex.EncodeToString(digest[:]) != artifact.SHA256 {
		return nil, errors.New("工具成果版本不一致")
	}
	return raw, nil
}
func verifyArtifact(root string, artifact Artifact) error {
	_, e := verifiedArtifactBytes(root, artifact)
	return e
}
func (a *App) serveArtifact(w http.ResponseWriter, r *http.Request, id, path string) error {
	rows, e := a.query(`SELECT data,state FROM core_tool_jobs WHERE id=?`, id)
	if e != nil {
		return e
	}
	if len(rows) != 1 || rows[0]["state"] != "COMPLETED" {
		return errors.New("成果尚未明确完成")
	}
	var job Job
	if e = json.Unmarshal([]byte(str(rows[0]["data"])), &job); e != nil {
		return e
	}
	if e = a.resolveJob(&job); e != nil {
		return e
	}
	for _, artifact := range job.Artifacts {
		if artifact.Path != path {
			continue
		}
		raw, e := verifiedArtifactBytes(job.OutputDir, artifact)
		if e != nil {
			return e
		}
		// Only inert text or signature-checked raster images can be served inline.
		contentType := "application/octet-stream"
		inline := false
		switch strings.ToLower(filepath.Ext(path)) {
		case ".txt", ".md", ".csv", ".log", ".json", ".tex":
			contentType = "text/plain; charset=utf-8"
			inline = true
		case ".png", ".jpg", ".jpeg":
			if detected := http.DetectContentType(raw); detected == "image/png" || detected == "image/jpeg" {
				contentType = detected
				inline = true
			}
		}
		w.Header().Set("Content-Type", contentType)
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Security-Policy", "sandbox; default-src 'none'")
		if r.URL.Query().Get("download") == "1" || !inline {
			name := urlPathEscape(filepath.Base(path))
			fallback := "workdesk-result"
			if ext := filepath.Ext(path); len(ext) <= 12 && safeExtension.MatchString(ext) {
				fallback += ext
			}
			w.Header().Set("Content-Disposition", "attachment; filename=\""+fallback+"\"; filename*=UTF-8''"+name)
		}
		_, e = w.Write(raw)
		return e
	}
	return errors.New("文件不属于本次成果清单")
}

var safeExtension = regexp.MustCompile(`^\.[a-zA-Z0-9]+$`)

func urlPathEscape(s string) string { return strings.ReplaceAll(url.QueryEscape(s), "+", "%20") }

func (a *App) resolveJob(job *Job) error {
	if job.DataRoot == "" {
		return nil
	}
	relative, e := filepath.Rel(job.DataRoot, job.OutputDir)
	if e != nil {
		return e
	}
	output := filepath.Join(a.Profile.DataRoot, relative)
	if !inside(output, filepath.Join(a.Profile.DataRoot, "results")) {
		return errors.New("成果目录引用越界")
	}
	job.OutputDir = output
	for i := range job.Artifacts {
		artifact := &job.Artifacts[i]
		if artifact.RelativePath == "" {
			return errors.New("成果缺少资料库相对位置")
		}
		path := filepath.Join(a.Profile.DataRoot, filepath.FromSlash(artifact.RelativePath))
		if !inside(path, job.OutputDir) {
			return errors.New("成果文件引用越界")
		}
		if !strings.EqualFold(path, artifact.Path) {
			artifact.OriginalPath = artifact.Path
			artifact.Path = path
		}
	}
	return nil
}
