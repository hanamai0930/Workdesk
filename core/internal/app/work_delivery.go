package app

import (
	"encoding/json"
	"errors"
	"strings"
	"workdesk/core/internal/storage"
)

// Use existing command receipts and task events as the journal. Starting another
// attempt cannot inherit a previous turn's completion or queued guidance.
func (a *App) workDelivery(s *storage.Session, id, kind string, p map[string]any) (map[string]any, error) {
	task, attempt, hash := str(p["task_id"]), str(p["attempt_id"]), str(p["spec_hash"])
	r, e := s.Execute(`SELECT a.state,a.result,a.stop_requested,a.session_id FROM tasks t JOIN attempts a ON a.task_id=t.id WHERE t.id=? AND t.spec_hash=? AND json_extract(t.spec,'$.work_mode')='independent' AND json_extract(t.spec,'$.execution')='partner_native' AND a.id=? AND a.id=(SELECT id FROM attempts WHERE task_id=t.id ORDER BY started DESC,id DESC LIMIT 1)`, []any{task, hash, attempt})
	if e != nil {
		return nil, e
	}
	if len(r.Rows) != 1 {
		return nil, errors.New("原任务或尝试已变化，请刷新")
	}
	state := str(r.Rows[0][0])
	run := str(p["run_id"])
	rr, e := s.Execute(`SELECT data FROM core_partner_objects WHERE kind='run' AND id=? AND json_extract(data,'$.parent_task_id')=? AND json_extract(data,'$.parent_attempt_id')=? AND json_extract(data,'$.parent_spec_hash')=?`, []any{run, task, attempt, hash})
	if e != nil {
		return nil, e
	}
	if len(rr.Rows) != 1 {
		return nil, errors.New("当前运行与原任务不符")
	}
	var native map[string]any
	if e = json.Unmarshal([]byte(str(rr.Rows[0][0])), &native); e != nil {
		return nil, e
	}
	out := map[string]any{"task_id": task, "attempt_id": attempt, "spec_hash": hash, "run_id": run, "command_id": id}
	if kind == "work.guidance" {
		note, ok := p["note"].(string)
		if !ok || len(note) > 8000 {
			return nil, errors.New("补充要求过长或格式不正确")
		}
		var prev string
		last, e := s.Execute("SELECT json_extract(payload,'$.command_id') FROM events WHERE task_id=? AND attempt_id=? AND kind='work.guidance' ORDER BY seq DESC LIMIT 1", []any{task, attempt})
		if e != nil {
			return nil, e
		}
		if len(last.Rows) > 0 {
			prev = str(last.Rows[0][0])
		}
		if str(p["previous"]) != prev {
			return nil, errors.New("补充要求已有新版本，请刷新")
		}
		if (state != "RUNNING" && state != "WAITING_INPUT" && state != "WAITING_APPROVAL" && state != "COMPLETED") || number(r.Rows[0][2]) != 0 {
			return nil, errors.New("此回合当前不能排队补充")
		}
		out["note"] = strings.TrimSpace(note)
		return out, nil
	}
	if state != "COMPLETED" || native["state"] != "COMPLETED" || native["local_exit_confirmed"] != true || number(r.Rows[0][2]) != 0 {
		return nil, errors.New("请先核对原回合完成和本机退出")
	}
	criteria := strings.TrimSpace(str(p["criteria"]))
	if criteria == "" || len(criteria) > 8000 || p["confirm"] != true {
		return nil, errors.New("请明确确认完成依据")
	}
	jobs, e := s.Execute("SELECT data FROM core_tool_jobs WHERE id=? AND state='COMPLETED'", []any{str(p["invocation_id"])})
	if e != nil {
		return nil, e
	}
	var job Job
	if len(jobs.Rows) != 1 || json.Unmarshal([]byte(str(jobs.Rows[0][0])), &job) != nil || job.TaskID != task || job.AttemptID != attempt || job.SpecHash != hash {
		return nil, errors.New("成果作业与本回合不符")
	}
	if e = a.resolveJob(&job); e != nil {
		return nil, e
	}
	for _, check := range job.Checks {
		if check["state"] == "FAILED" || check["state"] == "PENDING" {
			return nil, errors.New("成果检查尚未通过")
		}
	}
	for _, f := range job.Artifacts {
		if f.Path != str(p["path"]) || f.SHA256 != str(p["sha256"]) {
			continue
		}
		if e := verifyArtifact(job.OutputDir, f); e != nil {
			return nil, e
		}
		for i := len(job.Decisions) - 1; i >= 0; i-- {
			d := job.Decisions[i]
			if d["sha256"] == f.SHA256 && (d["path"] == f.Path || f.RelativePath != "" && d["relative_path"] == f.RelativePath) {
				if d["decision"] == "revise" {
					return nil, errors.New("此版本已退回，请先修订")
				}
				break
			}
		}
		out["state"] = "COMPLETED"
		out["criteria"] = criteria
		out["invocation_id"] = job.ID
		out["path"] = f.Path
		out["sha256"] = f.SHA256
		return out, nil
	}
	return nil, errors.New("交付文件与版本不符")
}

func (a *App) workDeliveryView(task string) (any, error) {
	rows, e := a.query(`SELECT kind,seq,coalesce(json_extract(payload,'$.receipt'),payload) AS receipt FROM events WHERE task_id=? AND attempt_id=(SELECT id FROM attempts WHERE task_id=? ORDER BY started DESC,id DESC LIMIT 1) AND kind IN ('work.complete','work.guidance','tool.decide','result.task-complete','work_result_human_decision') ORDER BY seq`, task, task)
	if e != nil {
		return nil, e
	}
	out := map[string]any{"state": "OPEN"}
	for _, row := range rows {
		var entry map[string]any
		if e = json.Unmarshal([]byte(str(row["receipt"])), &entry); e != nil {
			return nil, e
		}
		switch row["kind"] {
		case "work_result_human_decision":
			if entry["value"] == "return" {
				if c, ok := out["completion"].(map[string]any); !ok || c["result_id"] == entry["result_id"] {
					out["state"] = "REVISION_REQUIRED"
				}
			}
		case "work.complete", "result.task-complete":
			out["completion"] = entry
			out["state"] = "COMPLETED"
		case "tool.decide":
			if entry["decision"] == "revise" {
				if c, ok := out["completion"].(map[string]any); !ok || c["invocation_id"] == entry["invocation_id"] && c["sha256"] == entry["sha256"] {
					out["state"] = "REVISION_REQUIRED"
				}
			}
		case "work.guidance":
			out["guidance"] = entry
		}
	}
	return out, nil
}
