package app

import (
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

func equal(a, b string) bool { return subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1 }

// BindPort supplies the actual listener identity without trusting proxy headers.
func (a *App) BindPort(port string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Host != "127.0.0.1:"+port {
			response(w, 403, map[string]any{"error": "本地服务地址不符"})
			return
		}
		if r.URL.Path == "/health" {
			response(w, 200, map[string]any{"app": "workdesk-control-v1", "protocol": 2, "instance": a.Process, "program_base": a.Profile.ProgramRoot, "data_root": a.Profile.DataRoot, "profile_id": a.Profile.InstanceID, "implementation": a.Implementation, "core_engine": "go"})
			return
		}
		token := r.Header.Get("Authorization")
		if !equal(token, "Bearer "+a.Control) && !(r.Method == "GET" && equal(token, "Bearer "+a.Viewer)) {
			response(w, 403, map[string]any{"error": "需要本地控制服务凭据"})
			return
		}
		if origin := r.Header.Get("Origin"); origin != "" && origin != "http://127.0.0.1:"+port {
			response(w, 403, map[string]any{"error": "请求来源不符"})
			return
		}
		a.api(w, r, r.URL.Path)
	})
}
func (a *App) api(w http.ResponseWriter, r *http.Request, path string) {
	if r.Method == "POST" {
		a.admission.Lock()
		defer a.admission.Unlock()
		if a.retiring.Load() {
			failed(w, errors.New("核心正在停止；未接纳新操作"))
			return
		}
		select {
		case <-a.Stop:
			failed(w, errors.New("核心已经停止"))
			return
		default:
		}
	}
	if r.Method != "GET" && r.Method != "POST" {
		response(w, 405, map[string]any{"error": "方法不支持"})
		return
	}
	var body map[string]any
	if r.Method == "POST" {
		if !strings.HasPrefix(r.Header.Get("Content-Type"), "application/json") {
			response(w, 400, map[string]any{"error": "请求须为JSON"})
			return
		}
		raw, e := readBody(w, r)
		if e != nil {
			failed(w, e)
			return
		}
		if e = Decode(raw, &body); e != nil || body == nil {
			failed(w, errors.New("请求须为JSON对象"))
			return
		}
	}
	if path == "/api/v1/instance" && r.Method == "GET" {
		response(w, 200, a.Identity())
		return
	}
	if path == "/api/v1/connection-setup" && r.Method == "GET" {
		value, err := a.connectionSetup(r.Context())
		if err != nil {
			failed(w, err)
		} else {
			response(w, 200, value)
		}
		return
	}
	if r.Method == "POST" {
		kind := str(body["kind"])
		if !ownerControl(kind) {
			if e := a.verifySources(); e != nil {
				failed(w, e)
				return
			}
		}
	}
	if r.Method == "POST" {
		action := map[string]string{"/api/v1/chat-api-key": "save", "/api/v1/chat-api-key/reuse": "reuse", "/api/v1/chat-api-key/select": "select"}[path]
		if action != "" {
			result, err := a.chatAPIKeyMutation(r.Context(), action, body)
			if err != nil {
				failed(w, err)
			} else {
				response(w, 200, result)
			}
			return
		}
	}
	if path == "/api/v1/command" && r.Method == "POST" {
		kind := str(body["kind"])
		if partnerKind(kind) {
			result, e := a.partnerCommand(body)
			if e != nil {
				failed(w, e)
			} else {
				response(w, 200, result)
			}
			return
		}
		if (kind == "service.reload" || strings.HasPrefix(kind, "backup.") || strings.HasPrefix(kind, "storage.")) && a.partnerBusy() {
			failed(w, errors.New("伙伴安装、登录或工作仍在途或结果未知，请先停止或核对"))
			return
		}
		if kind == "notice.create" || kind == "notice.state" || strings.HasPrefix(kind, "schedule.tool-") || strings.HasPrefix(kind, "project.archive.") || strings.HasPrefix(kind, "memory.") || kind == "project.handoff-create" || kind == "tool.run" || kind == "tool.cancel" || kind == "tool.input" || kind == "budget.save" || kind == "tool.authorize" || kind == "tool.revoke" || kind == "tool.decide" {
			result, e := a.toolCommand(body)
			if e != nil {
				failed(w, e)
			} else {
				response(w, 200, result)
			}
			return
		}
		if kind == "service.reload" {
			rows, e := a.query(`SELECT id FROM core_tool_jobs WHERE state IN ('QUEUED','RUNNING','STOP_REQUESTED') LIMIT 1`)
			if e != nil || len(rows) > 0 {
				failed(w, errors.New("本地工具仍在途，请先停止或核对"))
				return
			}
		}
	}
	if strings.HasPrefix(path, "/api/v2/") {
		if r.Method != "GET" {
			response(w, 405, map[string]any{"error": "方法不支持"})
			return
		}
		value, e := a.v2(w, r, path)
		if e != nil {
			failed(w, e)
		} else if value != nil {
			response(w, 200, value)
		}
		return
	}
	if !strings.HasPrefix(path, "/api/v1/") {
		response(w, 404, map[string]any{"error": "接口不存在"})
		return
	}
	status, value, e := a.legacyRequest(r.Context(), r.Method, path+querySuffix(r.URL), body)
	if e != nil {
		uncertain(w, e)
		return
	}
	if status == 0 {
		status = 500
	}
	response(w, status, value)
}
func querySuffix(u *url.URL) string {
	if u.RawQuery != "" {
		return "?" + u.RawQuery
	}
	return ""
}
func readBody(w http.ResponseWriter, r *http.Request) ([]byte, error) {
	r.Body = http.MaxBytesReader(w, r.Body, 16*1024*1024)
	defer r.Body.Close()
	return ioReadAll(r.Body)
}
func (a *App) v2(w http.ResponseWriter, r *http.Request, path string) (any, error) {
	q := r.URL.Query()
	if path == "/api/v2/notifications" {
		return a.inbox(q)
	}
	if path == "/api/v2/memories" || path == "/api/v2/project-handoffs" {
		return a.readProjectMemory(path, q)
	}
	if path == "/api/v2/tool-schedules" {
		return a.toolScheduleView(q)
	}
	if strings.HasPrefix(path, "/api/v2/projects/") && (strings.HasSuffix(path, "/archives") || strings.HasSuffix(path, "/archive-download")) {
		return a.archiveRead(w, r, path)
	}
	if strings.HasPrefix(path, "/api/v2/projects/") && strings.HasSuffix(path, "/compare") {
		project := strings.TrimSuffix(strings.TrimPrefix(path, "/api/v2/projects/"), "/compare")
		return a.compareProjectVersions(project, q)
	}
	if strings.HasPrefix(path, "/api/v2/projects/") && (strings.HasSuffix(path, "/materials") || strings.HasSuffix(path, "/export")) {
		return a.projectMaterials(w, r, path)
	}
	if path == "/api/v2/foundation-contract" {
		var contract map[string]any
		data, err := os.ReadFile(filepath.Join(a.Profile.DataRoot, "implementations", a.Implementation, "contracts", "core-v2.json"))
		if err != nil {
			return nil, errors.New("当前实例的能力契约不可读")
		}
		if err = Decode(data, &contract); err != nil {
			return nil, err
		}
		return map[string]any{"implementation_revision": a.Implementation, "contract": contract}, nil
	}
	if path == "/api/v2/tools" {
		return a.documentCatalog(), nil
	}
	if strings.HasPrefix(path, "/api/v2/jobs/") && strings.HasSuffix(path, "/console") {
		return a.terminalConsole(strings.TrimSuffix(strings.TrimPrefix(path, "/api/v2/jobs/"), "/console"))
	}
	if path == "/api/v2/tool-contexts" {
		rows, e := a.query("SELECT t.title,t.id AS task_id,t.spec_hash,a.id AS attempt_id"+eligibleContexts+" ORDER BY a.started DESC LIMIT 200", q.Get("project_id"), now())
		return map[string]any{"contexts": rows}, e
	}
	if path == "/api/v2/jobs" || (strings.HasPrefix(path, "/api/v2/jobs/") && strings.Count(path, "/") == 4) {
		return a.documentJobs(q, strings.TrimPrefix(strings.TrimPrefix(path, "/api/v2/jobs"), "/"))
	}
	if path == "/api/v2/partners" || path == "/api/v2/components" || path == "/api/v2/accounts" || path == "/api/v2/connections" || path == "/api/v2/component-operations" || path == "/api/v2/partner-runs" {
		return a.partnersView(path)
	}
	if strings.HasPrefix(path, "/api/v2/partner-runs/") && strings.HasSuffix(path, "/events") {
		id := strings.TrimSuffix(strings.TrimPrefix(path, "/api/v2/partner-runs/"), "/events")
		after, _ := strconv.ParseInt(q.Get("after"), 10, 64)
		return a.partnerRunEvents(id, after)
	}
	if strings.HasPrefix(path, "/api/v2/partner-runs/") {
		return a.partnerRun(strings.TrimPrefix(path, "/api/v2/partner-runs/"))
	}
	limit := bounded(q.Get("limit"), 50, 200)
	switch {
	case path == "/api/v2/project-budget":
		return a.projectBudgetView(q.Get("project_id"))
	case path == "/api/v2/projects":
		offset, _ := strconv.Atoi(q.Get("cursor"))
		if offset < 0 {
			offset = 0
		}
		rows, e := a.query(`SELECT id,name,root,created FROM projects ORDER BY created,id LIMIT ? OFFSET ?`, limit+1, offset)
		if e != nil {
			return nil, e
		}
		more := len(rows) > limit
		if more {
			rows = rows[:limit]
		}
		return map[string]any{"projects": rows, "cursor": strconv.Itoa(offset + len(rows)), "has_more": more}, nil
	case path == "/api/v2/records":
		return a.records(q.Get("cursor"), limit)
	case strings.HasPrefix(path, "/api/v2/records/"):
		return a.record(strings.TrimPrefix(path, "/api/v2/records/"))
	case path == "/api/v2/events":
		after, _ := strconv.ParseInt(q.Get("after"), 10, 64)
		if after < 0 {
			return nil, errors.New("事件游标无效")
		}
		limit = bounded(q.Get("limit"), 200, 500)
		rows, e := a.query(`SELECT seq,at,task_id,attempt_id,kind,payload FROM events WHERE seq>? ORDER BY seq LIMIT ?`, after, limit+1)
		if e != nil {
			return nil, e
		}
		more := len(rows) > limit
		if more {
			rows = rows[:limit]
		}
		for _, row := range rows {
			var payload any
			_ = json.Unmarshal([]byte(str(row["payload"])), &payload)
			row["payload"] = payload
			after = number(row["seq"])
		}
		return map[string]any{"events": rows, "cursor": strconv.FormatInt(after, 10), "has_more": more, "owner_epoch": a.Epoch}, nil
	case path == "/api/v2/runtime":
		rows, e := a.query(`SELECT id,state,data FROM core_tool_jobs ORDER BY updated DESC LIMIT 100`)
		if e != nil {
			return nil, e
		}
		jobs := []any{}
		for _, row := range rows {
			var job Job
			if json.Unmarshal([]byte(str(row["data"])), &job) == nil {
				job.State = str(row["state"])
				if e = a.resolveJob(&job); e != nil {
					return nil, e
				}
				jobs = append(jobs, job)
			}
		}
		a.mu.Lock()
		processes := []any{map[string]any{"id": "legacy-bridge", "pid": a.Legacy.PID(), "state": "RUNNING", "owner_epoch": a.Epoch}}
		for _, v := range a.processes {
			copy := map[string]any{}
			for k, x := range v {
				copy[k] = x
			}
			processes = append(processes, copy)
		}
		a.mu.Unlock()
		return map[string]any{"jobs": jobs, "processes": processes, "owner_epoch": a.Epoch, "process_instance": a.Process}, nil
	case strings.HasPrefix(path, "/api/v2/jobs/") && strings.HasSuffix(path, "/artifact"):
		id := strings.TrimSuffix(strings.TrimPrefix(path, "/api/v2/jobs/"), "/artifact")
		return nil, a.serveArtifact(w, r, id, q.Get("path"))
	case strings.HasPrefix(path, "/api/v2/jobs/") && strings.HasSuffix(path, "/log"):
		id := strings.TrimSuffix(strings.TrimPrefix(path, "/api/v2/jobs/"), "/log")
		return a.serveLog(w, r, id)
	}
	return nil, errors.New("接口不存在")
}

const recordUnion = `SELECT id,'task' AS kind,title,project_id,state,1 AS revision,updated FROM tasks UNION ALL SELECT id,'work_item' AS kind,title,project_id,state,revision,updated FROM work_items UNION ALL SELECT id,'chat' AS kind,COALESCE(json_extract(data,'$.title'),'Chat') AS title,json_extract(data,'$.project') AS project_id,'SAVED' AS state,COALESCE(json_extract(data,'$.revision'),1) AS revision,updated FROM collaboration WHERE kind='ui_chat' AND COALESCE(json_extract(data,'$.deleted'),0)=0`

const partnerRecordUnion = ` UNION ALL SELECT id,'partner_run' AS kind,substr(COALESCE(json_extract(data,'$.prompt'),'伙伴工作'),1,120) AS title,json_extract(data,'$.project_id') AS project_id,json_extract(data,'$.state') AS state,revision,updated FROM core_partner_objects WHERE kind='run' AND json_extract(data,'$.mode')='run' AND coalesce(json_extract(data,'$.parent_task_id'),'')=''`

type recordCursor struct {
	Time float64 `json:"time"`
	Kind string  `json:"kind"`
	ID   string  `json:"id"`
}

func (a *App) records(cursor string, limit int) (any, error) {
	where := ""
	args := []any{}
	if cursor != "" && cursor != "0" {
		raw, e := base64.RawURLEncoding.DecodeString(cursor)
		if e != nil {
			return nil, errors.New("记录游标无效")
		}
		var c recordCursor
		if e = json.Unmarshal(raw, &c); e != nil {
			return nil, e
		}
		where = " WHERE updated<? OR (updated=? AND (kind<? OR (kind=? AND id<?)))"
		args = append(args, c.Time, c.Time, c.Kind, c.Kind, c.ID)
	}
	args = append(args, limit+1)
	union := recordUnion
	schema, e := a.DB.Schema()
	if e != nil {
		return nil, e
	}
	if schema == "3" {
		union += partnerRecordUnion
	}
	rows, e := a.query("SELECT * FROM ("+union+")"+where+" ORDER BY updated DESC,kind DESC,id DESC LIMIT ?", args...)
	if e != nil {
		return nil, e
	}
	more := len(rows) > limit
	if more {
		rows = rows[:limit]
	}
	next := cursor
	if len(rows) > 0 {
		last := rows[len(rows)-1]
		var t float64
		switch v := last["updated"].(type) {
		case float64:
			t = v
		case int64:
			t = float64(v)
		}
		raw, _ := json.Marshal(recordCursor{t, str(last["kind"]), str(last["id"])})
		next = base64.RawURLEncoding.EncodeToString(raw)
	}
	return map[string]any{"records": rows, "cursor": next, "has_more": more}, nil
}
func (a *App) record(identity string) (any, error) {
	parts := strings.SplitN(identity, "/", 2)
	kind, id := "", identity
	if len(parts) == 2 {
		kind, id = parts[0], parts[1]
	}
	queries := map[string]string{"task": "SELECT * FROM tasks WHERE id=?", "work_item": "SELECT * FROM work_items WHERE id=?", "chat": "SELECT * FROM collaboration WHERE kind='ui_chat' AND id=?", "partner_run": "SELECT * FROM core_partner_objects WHERE kind='run' AND json_extract(data,'$.mode')='run' AND id=?"}
	candidates := []string{"task", "work_item", "chat"}
	schema, e := a.DB.Schema()
	if e != nil {
		return nil, e
	}
	if schema == "3" {
		candidates = append(candidates, "partner_run")
	}
	for _, candidate := range candidates {
		if kind != "" && kind != candidate {
			continue
		}
		rows, e := a.query(queries[candidate], id)
		if e != nil {
			return nil, e
		}
		if len(rows) == 1 {
			row := rows[0]
			row["kind"] = candidate
			for _, key := range []string{"data", "spec"} {
				if s, ok := row[key].(string); ok {
					var x any
					if json.Unmarshal([]byte(s), &x) == nil {
						row[key] = x
					}
				}
			}
			return map[string]any{"record": row, "source": "existing-store"}, nil
		}
	}
	return nil, errors.New("记录不存在")
}
func (a *App) UIHandler(w http.ResponseWriter, r *http.Request) {
	if r.Host != strings.TrimPrefix(a.UIOrigin, "http://") {
		response(w, 403, map[string]any{"error": "本地界面地址不符"})
		return
	}
	if r.URL.Path == "/health" {
		value := a.Identity()
		value["app"] = "workdesk-ui-02"
		value["instance"] = a.Process
		value["revision"] = a.Implementation
		response(w, 200, value)
		return
	}
	prefix := "/s/" + a.UIToken + "/"
	if !strings.HasPrefix(r.URL.Path, prefix) {
		response(w, 403, map[string]any{"error": "本地界面入口不符"})
		return
	}
	route := strings.TrimPrefix(r.URL.Path, prefix)
	if r.Method == "POST" && r.Header.Get("Origin") != a.UIOrigin {
		response(w, 403, map[string]any{"error": "操作必须来自本地工作台"})
		return
	}
	if strings.HasPrefix(route, "api/v1/") || strings.HasPrefix(route, "api/v2/") {
		a.api(w, r, "/"+route)
		return
	}
	if route == "api/dev-exit" {
		response(w, 409, map[string]any{"error": "Go核心与界面共用进程，请先完成核心停止"})
		return
	}
	if strings.HasPrefix(route, "api/") {
		var body any
		if r.Method == "POST" {
			raw, e := readBody(w, r)
			if e != nil {
				failed(w, e)
				return
			}
			if e = Decode(raw, &body); e != nil {
				failed(w, e)
				return
			}
		}
		var result struct {
			Status int    `json:"status"`
			Body   any    `json:"body"`
			Mime   string `json:"mime"`
			Embed  bool   `json:"allow_embed"`
		}
		e := a.Legacy.Call(r.Context(), "legacy.ui_request", map[string]any{"method": r.Method, "path": "/" + route + querySuffix(r.URL), "body": body, "origin": a.UIOrigin}, &result)
		if e != nil {
			failed(w, e)
			return
		}
		if result.Status == 0 {
			result.Status = 500
		}
		if object, ok := result.Body.(map[string]any); ok {
			if encoded, ok := object["$bytes"].(string); ok {
				raw, e := base64.StdEncoding.DecodeString(encoded)
				if e != nil {
					failed(w, e)
					return
				}
				w.Header().Set("Content-Type", result.Mime)
				w.Header().Set("X-Content-Type-Options", "nosniff")
				w.Header().Set("Cache-Control", "no-store")
				if result.Embed {
					w.Header().Set("Content-Security-Policy", "frame-ancestors 'self'; object-src 'none'")
				}
				w.WriteHeader(result.Status)
				_, _ = w.Write(raw)
				return
			}
		}
		response(w, result.Status, result.Body)
		return
	}
	if r.Method != "GET" {
		response(w, 405, map[string]any{"error": "方法不支持"})
		return
	}
	base := filepath.Join(a.Profile.DataRoot, "implementations", a.Implementation, "workdesk-ui")
	name := filepath.Join(base, "static", route)
	if route == "" {
		name = filepath.Join(base, "dist", "index.html")
	} else if strings.HasPrefix(route, "assets/") {
		name = filepath.Join(base, "dist", route)
	}
	if !inside(name, base) || strings.Contains(route, "\\") || strings.Contains(route, "..") {
		response(w, 404, map[string]any{"error": "资源不存在"})
		return
	}
	raw, e := os.ReadFile(name)
	if e != nil {
		response(w, 404, map[string]any{"error": "资源不存在"})
		return
	}
	ext := filepath.Ext(name)
	if ext != ".js" && ext != ".css" && ext != ".html" && ext != ".svg" && ext != ".png" && ext != ".ico" {
		response(w, 404, map[string]any{"error": "资源类型不支持"})
		return
	}
	if route == "" {
		marker := fmt.Sprintf(`<html data-workdesk-channel="%s" data-workdesk-default-project="%s"`, a.Profile.Channel, html.EscapeString(a.Profile.DefaultProjectID))
		raw = []byte(strings.Replace(string(raw), "<html", marker, 1))
	}
	w.Header().Set("Content-Type", mime.TypeByExtension(ext))
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-src 'self' blob: https://chatgpt.com https://chat.deepseek.com https://chat.qwen.ai; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.Header().Set("Cache-Control", "no-store")
	_, _ = w.Write(raw)
}
