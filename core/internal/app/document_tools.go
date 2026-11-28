package app

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"workdesk/core/internal/storage"
)

const documentVersion = "document/1"
const documentInputLimit = 64 << 20

type toolGrant struct {
	ID                 string            `json:"authorization_id"`
	Actor              string            `json:"actor"`
	ProjectID          string            `json:"project_id"`
	ProjectRoot        string            `json:"project_root"`
	TaskID             string            `json:"task_id,omitempty"`
	AttemptID          string            `json:"attempt_id,omitempty"`
	SpecHash           string            `json:"spec_hash,omitempty"`
	Implementation     string            `json:"implementation_revision"`
	Process            string            `json:"process_instance"`
	Expires            float64           `json:"expires_at"`
	Tool               string            `json:"tool_version"`
	RepositoryVersions map[string]string `json:"repository_versions,omitempty"`
	Inputs             []Artifact        `json:"input_versions"`
	Parameters         map[string]any    `json:"parameters"`
}

func nullID(id string) any {
	if id == "" {
		return nil
	}
	return id
}

func projectRoot(s *storage.Session, project string) (string, error) {
	rows, err := s.Execute("SELECT root FROM projects WHERE id=?", []any{project})
	if err != nil {
		return "", err
	}
	if len(rows.Rows) != 1 {
		return "", errors.New("项目不存在")
	}
	return str(rows.Rows[0][0]), nil
}

// The task grant must remain current at confirmation, submission and dispatch.
const eligibleContexts = ` FROM tasks t JOIN attempts a ON a.task_id=t.id
 WHERE t.project_id=? AND json_extract(t.grant,'$.spec_hash')=t.spec_hash
 AND json_extract(t.grant,'$.expires_at')>? AND t.state NOT IN ('CANCELLED','HANDED_OFF','PAUSED')
 AND a.stop_requested=0 AND a.state IN ('RUNNING','WAITING_INPUT','WAITING_APPROVAL','COMPLETED','SUCCEEDED')
 AND a.id=(SELECT id FROM attempts WHERE task_id=t.id ORDER BY started DESC,id DESC LIMIT 1)`

func checkToolBinding(s *storage.Session, g toolGrant) error {
	root, e := projectRoot(s, g.ProjectID)
	if e != nil {
		return e
	}
	if root != g.ProjectRoot {
		return errors.New("项目位置已改变，需要重新确认")
	}
	if g.TaskID == "" && g.AttemptID == "" && g.SpecHash == "" {
		return nil
	}
	rows, e := s.Execute("SELECT t.id"+eligibleContexts+" AND t.id=? AND a.id=? AND t.spec_hash=?", []any{g.ProjectID, now(), g.TaskID, g.AttemptID, g.SpecHash})
	if e != nil {
		return e
	}
	if len(rows.Rows) != 1 {
		return errors.New("原任务、当前尝试或有效授权不符")
	}
	return nil
}
func inputBytes(root, name string) ([]byte, error) {
	if !filepath.IsLocal(name) || strings.Contains(name, ":") {
		return nil, errors.New("模板须位于所选项目内")
	}
	path := filepath.Join(root, filepath.Clean(name))
	if !inside(path, root) {
		return nil, errors.New("模板越出项目目录")
	}
	f, e := os.Open(path)
	if e != nil {
		return nil, e
	}
	defer f.Close()
	info, e := f.Stat()
	if e != nil || !info.Mode().IsRegular() || info.Size() > documentInputLimit {
		return nil, errors.New("模板不是普通文件或超过64 MiB")
	}
	data, e := io.ReadAll(io.LimitReader(f, documentInputLimit+1))
	if len(data) > documentInputLimit {
		return nil, errors.New("模板超过64 MiB")
	}
	return data, e
}
func authoritativeDocument(p map[string]any) bool {
	for _, k := range []string{"authorization_id", "parameters", "task_id", "attempt_id", "spec_hash", "input_versions"} {
		if _, ok := p[k]; ok {
			return true
		}
	}
	return p["tool_version"] != nil && p["tool_version"] != "material-summary/1"
}
func (a *App) createToolGrant(s *storage.Session, id string, p map[string]any) (map[string]any, error) {
	if p["confirm"] != true || (p["tool_version"] != documentVersion && p["tool_version"] != computerVersion) {
		return nil, errors.New("请确认本次文档操作及输入范围")
	}
	params, ok := p["parameters"].(map[string]any)
	if !ok {
		return nil, errors.New("缺少文档参数")
	}
	raw, _ := json.Marshal(params)
	if len(raw) > 512<<10 {
		return nil, errors.New("文档参数超过512 KiB")
	}
	g := toolGrant{ID: id, Actor: "Human control command", ProjectID: str(p["project_id"]), TaskID: str(p["task_id"]), AttemptID: str(p["attempt_id"]), SpecHash: str(p["spec_hash"]), Tool: str(p["tool_version"]), Parameters: params, Expires: now() + 900, Process: a.Process, Implementation: a.Implementation, Inputs: []Artifact{}}
	var e error
	g.ProjectRoot, e = projectRoot(s, g.ProjectID)
	if e != nil {
		return nil, e
	}
	if e = checkToolBinding(s, g); e != nil {
		return nil, e
	}
	if g.Tool == computerVersion {
		if e = a.freezeComputerGrant(&g, p); e != nil {
			return nil, e
		}
		return map[string]any{"authorization_id": id, "state": "AUTHORIZED", "grant": g}, nil
	}
	format := str(params["format"])
	switch format {
	case "xlsx", "csv", "docx", "pdf", "pptx", "md", "html", "tex":
	default:
		return nil, errors.New("文件类型无效")
	}
	files, ok := p["input_files"].([]any)
	if !ok && p["input_files"] != nil {
		return nil, errors.New("模板列表无效")
	}
	action := str(params["action"])
	maxInputs := 1
	if format == "pdf" {
		maxInputs = 16
	}
	if (action != "edit" && action != "create") || (action == "edit" && (len(files) < 1 || len(files) > maxInputs)) || (action == "create" && len(files) != 0) {
		return nil, errors.New("输入数量不符合所选的文档操作")
	}
	total := 0
	for _, v := range files {
		var input Artifact
		if ref, ok := v.(map[string]any); ok {
			input, e = a.priorDocumentInput(s, g.ProjectID, ref)
		} else {
			name := str(v)
			var data []byte
			data, e = inputBytes(g.ProjectRoot, name)
			digest := sha256.Sum256(data)
			input = Artifact{Path: filepath.ToSlash(filepath.Clean(name)), Bytes: int64(len(data)), SHA256: hex.EncodeToString(digest[:])}
		}
		if e != nil {
			return nil, e
		}
		total += int(input.Bytes)
		if total > documentInputLimit {
			return nil, errors.New("所选材料合计超过64 MiB")
		}
		g.Inputs = append(g.Inputs, input)
	}
	return map[string]any{"authorization_id": id, "state": "AUTHORIZED", "grant": g}, nil
}

// Resolve a project artifact version to its immutable job result. The caller
// supplies the version identity from the materials read model, never a disk path.
func (a *App) priorDocumentInput(s *storage.Session, project string, ref map[string]any) (Artifact, error) {
	invalid := errors.New("成果版本不属于本项目或已变化")
	idx, e := strconv.Atoi(str(ref["entry"]))
	if ref["kind"] != "job" || e != nil || idx < 0 {
		return Artifact{}, invalid
	}
	rows, e := s.Execute("SELECT data FROM core_tool_jobs WHERE id=? AND project_id=? AND state='COMPLETED'", []any{str(ref["source"]), project})
	if e != nil {
		return Artifact{}, e
	}
	var prior Job
	if len(rows.Rows) != 1 || json.Unmarshal([]byte(str(rows.Rows[0][0])), &prior) != nil || idx >= len(prior.Artifacts) {
		return Artifact{}, invalid
	}
	if e = a.resolveJob(&prior); e != nil {
		return Artifact{}, e
	}
	f := prior.Artifacts[idx]
	if f.SHA256 != str(ref["sha256"]) || f.Bytes < 0 || f.Bytes > documentInputLimit || !filepath.IsLocal(filepath.FromSlash(f.RelativePath)) {
		return Artifact{}, invalid
	}
	if _, e = readProjectVersion(a.Profile.DataRoot, filepath.FromSlash(f.RelativePath), f.Bytes, f.SHA256); e != nil {
		return Artifact{}, e
	}
	return Artifact{SourceJobID: prior.ID, Path: filepath.Base(f.Path), RelativePath: f.RelativePath, Bytes: f.Bytes, SHA256: f.SHA256}, nil
}
func (a *App) loadToolGrant(s *storage.Session, id string) (toolGrant, error) {
	var receipt struct {
		Grant toolGrant `json:"grant"`
	}
	rows, e := s.Execute("SELECT receipt FROM commands WHERE id=? AND kind='tool.authorize'", []any{id})
	if e != nil {
		return receipt.Grant, e
	}
	if len(rows.Rows) != 1 || json.Unmarshal([]byte(str(rows.Rows[0][0])), &receipt) != nil {
		return receipt.Grant, errors.New("文档授权不存在")
	}
	g := receipt.Grant
	if g.ID != id || g.Expires <= now() || g.Process != a.Process || g.Implementation != a.Implementation {
		return g, errors.New("文档授权已失效，请重新确认")
	}
	revoked, e := s.Execute("SELECT 1 FROM commands WHERE kind='tool.revoke' AND json_extract(receipt,'$.authorization_id')=? LIMIT 1", []any{id})
	if e != nil {
		return g, e
	}
	if len(revoked.Rows) != 0 {
		return g, errors.New("本次文档授权已撤回")
	}
	return g, checkToolBinding(s, g)
}
func (a *App) revokeToolGrant(s *storage.Session, p map[string]any) (map[string]any, toolGrant, error) {
	g, e := a.loadToolGrant(s, str(p["authorization_id"]))
	if e != nil {
		return nil, g, e
	}
	rows, e := s.Execute("SELECT id FROM core_tool_jobs WHERE json_extract(data,'$.authorization_id')=?", []any{g.ID})
	if e != nil {
		return nil, g, e
	}
	if len(rows.Rows) > 0 {
		return nil, g, errors.New("已有作业，请通过作业停止入口处理")
	}
	return map[string]any{"authorization_id": g.ID, "state": "REVOKED"}, g, nil
}
func (a *App) authorizedJob(s *storage.Session, p map[string]any) (*Job, error) {
	if len(p) != 1 {
		return nil, errors.New("已确认的调用只接受授权编号")
	}
	g, e := a.loadToolGrant(s, str(p["authorization_id"]))
	if e != nil {
		return nil, e
	}
	exists, e := s.Execute("SELECT id FROM core_tool_jobs WHERE json_extract(data,'$.authorization_id')=? LIMIT 1", []any{g.ID})
	if e != nil {
		return nil, e
	}
	if len(exists.Rows) > 0 {
		return nil, errors.New("该授权已有作业，请按原命令查询")
	}
	return a.newAuthorizedJob(g), nil
}
func (a *App) newAuthorizedJob(g toolGrant) *Job {
	id := ID()
	var names []string
	for _, f := range g.Inputs {
		names = append(names, f.Path)
	}
	return &Job{ID: id, ProjectID: g.ProjectID, ProjectRoot: g.ProjectRoot, TaskID: g.TaskID, AttemptID: g.AttemptID, SpecHash: g.SpecHash, AuthorizationID: g.ID, State: "QUEUED", InputFiles: names, InputVersions: g.Inputs, OutputDir: filepath.Join(a.Profile.DataRoot, "results", id), Artifacts: []Artifact{}, Created: now(), Updated: now(), OwnerEpoch: a.Epoch, ProcessInstance: a.Process, DataRoot: a.Profile.DataRoot, ImplementationRevision: a.Implementation, ToolVersion: g.Tool, Action: str(g.Parameters["action"])}
}

// Worker reads only version-checked copies; reopening the original after verification
// could race a subsequent user edit.
func (a *App) documentRequest(job *Job) (map[string]any, func(), error) {
	var g toolGrant
	e := a.write(func(s *storage.Session) error {
		var err error
		g, err = a.loadToolGrant(s, job.AuthorizationID)
		return err
	})
	if e != nil {
		return nil, nil, e
	}
	if !inside(job.OutputDir, a.Profile.DataRoot) {
		return nil, nil, errors.New("文档成果位置越界")
	}
	if _, err := os.Lstat(job.OutputDir); !os.IsNotExist(err) {
		return nil, nil, errors.New("文档输出目录已存在或不可用")
	}
	dir, e := os.MkdirTemp(a.Profile.DataRoot, ".document-input-")
	if e != nil {
		return nil, nil, e
	}
	clean := func() { os.RemoveAll(dir) }
	files := []string{}
	for i, v := range g.Inputs {
		var data []byte
		if v.SourceJobID != "" {
			data, e = readProjectVersion(a.Profile.DataRoot, filepath.FromSlash(v.RelativePath), v.Bytes, v.SHA256)
		} else {
			data, e = inputBytes(g.ProjectRoot, v.Path)
		}
		if e != nil {
			clean()
			return nil, nil, e
		}
		if fmt.Sprintf("%x", sha256.Sum256(data)) != v.SHA256 {
			clean()
			return nil, nil, errors.New("模板内容已变化，原授权失效")
		}
		name := filepath.Base(v.Path)
		sub := filepath.Join(dir, strconv.Itoa(i))
		if e = os.Mkdir(sub, 0700); e != nil {
			clean()
			return nil, nil, e
		}
		dest := filepath.Join(sub, name)
		if e = os.WriteFile(dest, data, 0600); e != nil {
			clean()
			return nil, nil, e
		}
		files = append(files, dest)
	}
	return map[string]any{"project_root": dir, "input_files": files, "output_dir": job.OutputDir, "parameters": g.Parameters}, clean, nil
}

func (a *App) toolDecision(s *storage.Session, id string, p map[string]any) (map[string]any, *Job, error) {
	decision := str(p["decision"])
	if p["confirm"] != true || (decision != "accept" && decision != "revise") || len(str(p["note"])) > 4000 {
		return nil, nil, errors.New("请选择明确版本并确认验收意见")
	}
	rows, e := s.Execute("SELECT data FROM core_tool_jobs WHERE id=? AND state='COMPLETED'", []any{str(p["invocation_id"])})
	if e != nil {
		return nil, nil, e
	}
	var job Job
	if len(rows.Rows) != 1 || json.Unmarshal([]byte(str(rows.Rows[0][0])), &job) != nil {
		return nil, nil, errors.New("成果未完成")
	}
	// Resolve paths on a copy for restored libraries; preserve original stored locators.
	resolved := job
	resolved.Artifacts = append([]Artifact(nil), job.Artifacts...)
	if e = a.resolveJob(&resolved); e != nil {
		return nil, nil, e
	}
	for _, f := range resolved.Artifacts {
		if f.Path != str(p["path"]) || f.SHA256 != str(p["sha256"]) {
			continue
		}
		if e = verifyArtifact(resolved.OutputDir, f); e != nil {
			return nil, nil, e
		}
		d := map[string]any{"command_id": id, "invocation_id": job.ID, "decision": decision, "note": str(p["note"]), "at": now(), "path": f.Path, "relative_path": f.RelativePath, "sha256": f.SHA256, "actor": "Human control command"}
		job.Decisions = append(job.Decisions, d)
		job.Updated = now()
		data, e := json.Marshal(job)
		if e != nil {
			return nil, nil, e
		}
		_, e = s.Execute("UPDATE core_tool_jobs SET data=?,updated=? WHERE id=?", []any{string(data), job.Updated, job.ID})
		return d, &job, e
	}
	return nil, nil, errors.New("成果路径或版本不匹配")
}
func (a *App) documentJobs(q url.Values, id string) (any, error) {
	limit := bounded(q.Get("limit"), 50, 100)
	offset := 0
	if v := q.Get("cursor"); v != "" {
		i, e := strconv.Atoi(v)
		if e != nil || i < 0 {
			return nil, errors.New("游标无效")
		}
		offset = i
	}
	args := []any{}
	sql := "SELECT data,state FROM core_tool_jobs WHERE 1=1"
	if id != "" {
		sql += " AND id=?"
		args = append(args, id)
	}
	for _, k := range []string{"task_id", "project_id", "tool_version"} {
		if v := q.Get(k); v != "" {
			sql += " AND json_extract(data,'$." + k + "')=?"
			args = append(args, v)
		}
	}
	rows, e := a.query(sql+" ORDER BY rowid DESC LIMIT ? OFFSET ?", append(args, limit+1, offset)...)
	if e != nil {
		return nil, e
	}
	more := len(rows) > limit
	if more {
		rows = rows[:limit]
	}
	jobs := []Job{}
	for _, r := range rows {
		var job Job
		if e = json.Unmarshal([]byte(str(r["data"])), &job); e != nil {
			return nil, e
		}
		job.State = str(r["state"])
		if e = a.resolveJob(&job); e != nil {
			return nil, e
		}
		jobs = append(jobs, job)
	}
	if id != "" {
		if len(jobs) != 1 {
			return nil, errors.New("作业不存在")
		}
		return jobs[0], nil
	}
	return map[string]any{"jobs": jobs, "has_more": more, "cursor": strconv.Itoa(offset + len(rows))}, nil
}

func (a *App) documentCatalog() map[string]any {
	exists := func(path string) bool { info, err := os.Stat(path); return err == nil && info.Mode().IsRegular() }
	root := filepath.Join(a.pythonSources(), "researchos", "workdesk", "professional_tools")
	docs := map[string]any{"tool_version": documentVersion, "worker_present": exists(filepath.Join(root, "document.py")) && exists(a.Profile.PythonExecutable), "actions": []string{"create", "edit"}, "formats": []string{"xlsx", "csv", "docx", "pptx", "pdf", "md", "html", "tex"}, "authorization": "tool.authorize", "model_requests": 0, "dependencies": "检查结果依实际操作与本机提供者返回"}
	providers := a.probeDocumentProviders()
	docs["capabilities"] = providers
	docs["probe_state"] = "UNKNOWN"
	if providers != nil {
		docs["probe_state"] = "CHECKED"
	}
	summary := map[string]any{"tool_version": "material-summary/1", "worker_present": exists(filepath.Join(root, "summary.py")) && exists(a.Profile.PythonExecutable), "model_requests": 0}
	return map[string]any{"foundation_contract": "/api/v2/foundation-contract", "extension_catalog": "/api/v1/extensions", "partner_catalog": "/api/v2/partners", "tools": []any{summary, docs, map[string]any{"tool_version": computerVersion, "actions": computerActions, "worker_present": exists(filepath.Join(root, "computer.py")) && exists(a.Profile.PythonExecutable), "scope": "HUMAN_EXPLICIT_PROJECT_OR_WINDOW", "execution_isolation": "NOT_ESTABLISHED_BY_PROJECT", "model_requests": 0}}}
}
