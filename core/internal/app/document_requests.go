package app

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"strconv"
	"time"
	"workdesk/core/internal/storage"
)

// The listening capability exists only while this specific native worker owns
// the run. It has no route to control commands or the viewer credential.
func (a *App) documentMCP(ctx context.Context, run map[string]any, config map[string]any, proc *partnerProcess) (func(), error) {
	listener, e := net.Listen("tcp4", "127.0.0.1:0")
	if e != nil {
		return nil, e
	}
	binding := map[string]any{}
	for _, key := range []string{"id", "project_id", "parent_task_id", "parent_attempt_id", "parent_spec_hash"} {
		binding[key] = run[key]
	}
	addr := listener.Addr().String()
	secret := Token(32)
	path := "/mcp/" + secret
	proc.secrets = append(proc.secrets, secret)
	servers, _ := config["scene_mcp"].([]any)
	for _, v := range servers {
		row, _ := v.(map[string]any)
		if row["name"] == "workdesk_documents" {
			listener.Close()
			return nil, errors.New("任务 MCP 名称 workdesk_documents 已占用")
		}
	}
	config["scene_mcp"] = append(append([]any{}, servers...), map[string]any{"name": "workdesk_documents", "config": map[string]any{"transport": "http", "url": "http://" + addr + path}})
	server := &http.Server{ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, IdleTimeout: 20 * time.Second, MaxHeaderBytes: 8192}
	server.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Host != addr || !equal(r.URL.Path, path) || r.Header.Get("Origin") != "" {
			http.NotFound(w, r)
			return
		}
		if r.Method != "POST" {
			w.WriteHeader(405)
			return
		}
		var msg struct {
			ID     json.RawMessage
			Method string
			Params struct {
				Name            string
				Arguments       map[string]any
				ProtocolVersion string
			}
		}
		data, e := io.ReadAll(http.MaxBytesReader(w, r.Body, 600<<10))
		if e != nil || Decode(data, &msg) != nil {
			w.WriteHeader(400)
			return
		}
		if len(msg.ID) == 0 {
			w.WriteHeader(202)
			return
		}
		var result any
		switch msg.Method {
		case "initialize":
			version := msg.Params.ProtocolVersion
			switch version {
			case "2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25":
			default:
				version = "2025-03-26"
			}
			result = map[string]any{"protocolVersion": version, "capabilities": map[string]any{"tools": map[string]any{}}, "serverInfo": map[string]string{"name": "Workdesk documents", "version": "1"}}
		case "ping":
			result = map[string]any{}
		case "tools/list":
			result = map[string]any{"tools": []any{map[string]any{"name": "workdesk_document", "description": "Request a Workdesk document/1 create or edit operation in this task. Human reviews the parameters before execution. For XLSX edit use parameters {action:edit,format:xlsx,name:...,sheet:...,cells:{...},expected:{...}}. input_files contains a project-relative template or an exact job version {kind:job,source:invocation_id,entry:index as string,sha256:digest}. Returns job, checks and version references.", "inputSchema": map[string]any{"type": "object", "properties": map[string]any{"parameters": map[string]string{"type": "object"}, "input_files": map[string]any{"type": "array", "items": map[string]any{"anyOf": []any{map[string]string{"type": "string"}, map[string]string{"type": "object"}}}}}, "required": []string{"parameters", "input_files"}, "additionalProperties": false}}}}
		case "tools/call":
			if msg.Params.Name != "workdesk_document" {
				e = errors.New("未知文档工具")
			} else {
				var request map[string]any
				request, e = a.proposeDocument(binding, msg.Params.Arguments)
				if e == nil {
					result, e = a.waitDocument(ctx, r.Context(), request)
				}
			}
			text := ""
			if e != nil {
				text = e.Error()
			} else {
				b, _ := json.Marshal(result)
				text = string(b)
			}
			result = map[string]any{"content": []any{map[string]string{"type": "text", "text": text}}, "isError": e != nil}
		default:
			response(w, 200, map[string]any{"jsonrpc": "2.0", "id": msg.ID, "error": map[string]any{"code": -32601, "message": "method not found"}})
			return
		}
		response(w, 200, map[string]any{"jsonrpc": "2.0", "id": msg.ID, "result": result})
	})
	go server.Serve(listener)
	return func() { server.Close() }, nil
}

func liveDocumentRun(s *storage.Session, p map[string]any) error {
	r, e := s.Execute(`SELECT 1 FROM core_partner_objects r JOIN attempts a ON a.id=? JOIN tasks t ON t.id=a.task_id WHERE r.kind='run' AND r.id=? AND a.task_id=? AND t.project_id=? AND t.spec_hash=? AND t.state NOT IN ('PAUSED','CANCELLED','HANDED_OFF') AND a.stop_requested=0 AND a.worker_instance=? AND a.state IN ('SEND_INTENT','RUNNING','WAITING_INPUT','WAITING_APPROVAL') AND a.id=(SELECT id FROM attempts WHERE task_id=t.id ORDER BY started DESC,id DESC LIMIT 1) AND json_extract(r.data,'$.process_instance')=a.worker_instance AND json_extract(r.data,'$.parent_task_id')=t.id AND json_extract(r.data,'$.project_id')=t.project_id AND json_extract(r.data,'$.parent_attempt_id')=a.id AND json_extract(r.data,'$.parent_spec_hash')=t.spec_hash AND json_extract(r.data,'$.state') IN ('QUEUED','STARTING','RUNNING','WAITING_INPUT','WAITING_APPROVAL') AND coalesce(json_extract(r.data,'$.stop_requested'),0)=0`, []any{p["attempt_id"], p["run_id"], p["task_id"], p["project_id"], p["spec_hash"], p["process_instance"]})
	if e != nil {
		return e
	}
	if len(r.Rows) != 1 {
		return errors.New("原任务或官方工作端已停止、变更或待核对")
	}
	return nil
}

const documentRequestKind = "task_document_request"

func (a *App) proposeDocument(run, args map[string]any) (map[string]any, error) {
	files, ok := args["input_files"].([]any)
	params, good := args["parameters"].(map[string]any)
	if !ok || !good || len(args) != 2 {
		return nil, errors.New("需要 input_files 和 parameters")
	}
	raw, _ := json.Marshal(args)
	// Reconnecting transports may allocate a different JSON-RPC id. Identical
	// arguments in one attempt recover the existing call and never re-dispatch.
	id := taskPartnerCommandID("doc-proposal", str(run["id"]), string(raw))
	q := map[string]any{"id": id, "run_id": run["id"], "task_id": run["parent_task_id"], "attempt_id": run["parent_attempt_id"], "project_id": run["project_id"], "spec_hash": run["parent_spec_hash"], "process_instance": a.Process, "input_files": files, "parameters": params, "state": "PENDING", "expires_at": now() + 900, "authorization_id": taskPartnerCommandID("doc-grant", id, ""), "command_id": taskPartnerCommandID("doc-run", id, "")}
	e := a.write(func(s *storage.Session) error {
		if e := liveDocumentRun(s, q); e != nil {
			return e
		}
		existing, e := s.Execute("SELECT data FROM core_partner_objects WHERE kind=? AND id=?", []any{documentRequestKind, id})
		if e != nil {
			return e
		}
		if len(existing.Rows) > 0 {
			return json.Unmarshal([]byte(str(existing.Rows[0][0])), &q)
		}
		// Resolve and fingerprint candidate inputs for the review. This does not
		// issue an authorization or submit the job.
		feedback, e := s.Execute("SELECT json_extract(payload,'$.input_version') FROM events WHERE task_id=? AND attempt_id=? AND kind='single_task_continued' AND json_extract(payload,'$.feedback_command_id') IS NOT NULL ORDER BY seq DESC LIMIT 1", []any{q["task_id"], q["attempt_id"]})
		if e != nil {
			return e
		}
		if len(feedback.Rows) > 0 {
			var wanted map[string]any
			if json.Unmarshal([]byte(str(feedback.Rows[0][0])), &wanted) != nil {
				return errors.New("退修所指版本不可读")
			}
			matched := false
			for _, v := range files {
				ref, ok := v.(map[string]any)
				if ok && ref["kind"] == "job" && ref["source"] == wanted["source"] && ref["entry"] == wanted["entry"] && ref["sha256"] == wanted["sha256"] {
					matched = true
					break
				}
			}
			if !matched {
				return errors.New("退修必须以原决定指定的成果版本作为输入")
			}
		}
		g := toolGrant{}
		root, e := projectRoot(s, str(q["project_id"]))
		if e != nil {
			return e
		}
		g.ProjectRoot = root
		g.ProjectID = str(q["project_id"])
		g.Inputs = []Artifact{}
		if e := a.freezeDocumentInputs(s, &g, params, files); e != nil {
			return e
		}
		q["input_versions"] = g.Inputs
		return putPartner(s, documentRequestKind, q)
	})
	return q, e
}
func sameDocumentValue(x, y any) bool {
	a, _ := json.Marshal(x)
	b, _ := json.Marshal(y)
	var av, bv any
	json.Unmarshal(a, &av)
	json.Unmarshal(b, &bv)
	a, _ = json.Marshal(av)
	b, _ = json.Marshal(bv)
	return string(a) == string(b)
}
func validateDocumentProposal(s *storage.Session, g toolGrant, p map[string]any) (map[string]any, error) {
	q, e := partnerObject(s, documentRequestKind, g.RequestID)
	if e != nil {
		return nil, e
	}
	if q["authorization_id"] != g.ID || q["process_instance"] != g.Process || g.Tool != documentVersion || q["state"] != "PENDING" || number(q["expires_at"]) <= int64(now()) || q["task_id"] != g.TaskID || q["attempt_id"] != g.AttemptID || q["spec_hash"] != g.SpecHash || q["project_id"] != g.ProjectID {
		return nil, errors.New("本次文档调用的原运行或授权已变化")
	}
	if p != nil {
		raw, err := json.Marshal(map[string]any{"parameters": p["parameters"], "input_files": p["input_files"]})
		if err != nil || taskPartnerCommandID("doc-proposal", str(q["run_id"]), string(raw)) != g.RequestID {
			return nil, errors.New("请求参数或模板已变化")
		}
	}
	return q, liveDocumentRun(s, q)
}
func (a *App) documentRequests(task, id string) ([]map[string]any, error) {
	if task == "" {
		return nil, errors.New("缺少原任务")
	}
	sql := `SELECT r.data,gr.id AS granted, EXISTS(SELECT 1 FROM commands rv WHERE rv.kind='tool.revoke' AND json_extract(rv.receipt,'$.authorization_id')=gr.id) AS revoked,j.id AS job_id,j.state AS job_state,j.data AS job, json_extract(n.data,'$.state') AS run_state, a.stop_requested AS stopped FROM core_partner_objects r LEFT JOIN commands gr ON gr.id=json_extract(r.data,'$.authorization_id') AND gr.kind='tool.authorize' LEFT JOIN core_tool_jobs j ON json_extract(j.data,'$.command_id')=json_extract(r.data,'$.command_id') LEFT JOIN core_partner_objects n ON n.kind='run' AND n.id=json_extract(r.data,'$.run_id') LEFT JOIN attempts a ON a.id=json_extract(r.data,'$.attempt_id') WHERE r.kind=? AND json_extract(r.data,'$.task_id')=?`
	args := []any{documentRequestKind, task}
	if id != "" {
		sql += " AND r.id=?"
		args = append(args, id)
	}
	rows, e := a.query(sql+" ORDER BY r.updated DESC", args...)
	if e != nil {
		return nil, e
	}
	out := []map[string]any{}
	for _, r := range rows {
		var q map[string]any
		if e := json.Unmarshal([]byte(str(r["data"])), &q); e != nil {
			return nil, e
		}
		switch {
		case r["job_id"] != nil:
			var j Job
			if e := json.Unmarshal([]byte(str(r["job"])), &j); e != nil {
				return nil, e
			}
			q["invocation_id"], q["state"], q["checks"], q["error"] = j.ID, j.State, j.Checks, j.Error
			versions := []any{}
			for i, f := range j.Artifacts {
				versions = append(versions, map[string]any{"kind": "job", "source": j.ID, "entry": strconv.Itoa(i), "sha256": f.SHA256, "path": f.Path})
			}
			q["versions"] = versions
		case q["state"] == "REJECTED":
		case number(r["revoked"]) != 0:
			q["state"] = "REVOKED"
		case q["process_instance"] != a.Process || number(q["expires_at"]) <= int64(now()) || number(r["stopped"]) != 0 || !documentRunOpen(str(r["run_state"])):
			q["state"] = "EXPIRED"
		case r["granted"] != nil:
			q["state"] = "AUTHORIZED"
		}
		out = append(out, q)
	}
	return out, nil
}

// The parent stop intent is already durable and admission remains locked.
func (a *App) cancelRunDocuments(run map[string]any) {
	rows, e := a.documentRequests(str(run["parent_task_id"]), "")
	if e != nil {
		fmt.Fprintln(a.Log, "无法查询原运行的文档作业", e)
		return
	}
	for _, r := range rows {
		if r["run_id"] != run["id"] || (r["state"] != "QUEUED" && r["state"] != "RUNNING") {
			continue
		}
		_, e := a.toolCommand(map[string]any{"id": taskPartnerCommandID("doc-stop", str(run["id"]), str(r["invocation_id"])), "kind": "tool.cancel", "payload": map[string]any{"invocation_id": r["invocation_id"]}})
		if e != nil {
			fmt.Fprintln(a.Log, "原文档停止结果请按作业核对", r["invocation_id"], e)
		}
	}
}
func documentRunOpen(state string) bool {
	switch state {
	case "QUEUED", "STARTING", "RUNNING", "WAITING_INPUT", "WAITING_APPROVAL":
		return true
	}
	return false
}
func (a *App) waitDocument(ctx, requestCtx context.Context, q map[string]any) (any, error) {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		rows, e := a.documentRequests(str(q["task_id"]), str(q["id"]))
		if e != nil || len(rows) != 1 {
			return nil, errors.New("查不到原工具请求")
		}
		r := rows[0]
		switch str(r["state"]) {
		case "PENDING", "AUTHORIZED", "QUEUED", "RUNNING", "STOP_REQUESTED":
		case "COMPLETED", "SUCCEEDED":
			return map[string]any{"request_id": q["id"], "invocation_id": r["invocation_id"], "checks": r["checks"], "versions": r["versions"]}, nil
		default:
			return nil, fmt.Errorf("原文档请求 %s 状态 %s %s", q["id"], r["state"], str(r["error"]))
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-requestCtx.Done():
			return nil, requestCtx.Err()
		case <-ticker.C:
		}
	}
}
func rejectDocument(s *storage.Session, id string) (map[string]any, error) {
	q, e := partnerObject(s, documentRequestKind, id)
	if e != nil {
		return nil, e
	}
	grants, e := s.Execute("SELECT id FROM commands WHERE id=?", []any{q["authorization_id"]})
	if e != nil {
		return nil, e
	}
	if len(grants.Rows) > 0 || q["state"] != "PENDING" {
		return nil, errors.New("原请求已处理")
	}
	q["state"] = "REJECTED"
	if e = putPartner(s, documentRequestKind, q); e != nil {
		return nil, e
	}
	return map[string]any{"request_id": id, "state": "REJECTED", "task_id": q["task_id"], "attempt_id": q["attempt_id"]}, nil
}
