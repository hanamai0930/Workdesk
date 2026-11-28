package app

import (
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"workdesk/core/internal/storage"
)

var ErrToolBudgetBlocked = errors.New("项目预算阻止派发")

type projectBudget struct {
	Project       string `json:"project_id"`
	Revision      int64  `json:"revision"`
	MaxDispatch   *int64 `json:"max_dispatches"`
	MaxConcurrent *int64 `json:"max_concurrent"`
	Unpriced      string `json:"unknown_cost_policy"`
	Command       string `json:"command_id,omitempty"`
}
type budgetEntry struct {
	ID             string  `json:"id"`
	Project        string  `json:"project_id"`
	Task           string  `json:"task_id,omitempty"`
	Attempt        string  `json:"attempt_id,omitempty"`
	Tool           string  `json:"tool_version,omitempty"`
	Partner        string  `json:"partner_id,omitempty"`
	Account        string  `json:"account_id,omitempty"`
	Source         string  `json:"source"`
	Schedule       string  `json:"schedule_id,omitempty"`
	Round          string  `json:"round_id,omitempty"`
	State          string  `json:"state"`
	Outcome        string  `json:"outcome"`
	Meter          string  `json:"model_cost_basis"`
	PolicyRevision int64   `json:"policy_revision"`
	At             float64 `json:"reserved_at"`
	Observed       float64 `json:"observed_at"`
	Started        bool    `json:"started"`
}

func budgetRow(s *storage.Session, key string, target any) (bool, error) {
	r, e := s.Execute("SELECT value FROM core_meta WHERE key=?", []any{key})
	if e != nil || len(r.Rows) == 0 {
		return false, e
	}
	return true, json.Unmarshal([]byte(str(r.Rows[0][0])), target)
}
func putBudgetRow(s *storage.Session, key string, v any) error {
	raw, e := json.Marshal(v)
	if e != nil {
		return e
	}
	_, e = s.Execute("INSERT INTO core_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", []any{key, string(raw)})
	return e
}
func readBudgetPolicy(s *storage.Session, project string) (projectBudget, error) {
	b := projectBudget{Project: project, Unpriced: "allow_unpriced"}
	_, e := budgetRow(s, "project_budget/1/"+project, &b)
	return b, e
}
func budgetInteger(v any) (*int64, error) {
	if v == nil {
		return nil, nil
	}
	n, e := strconv.ParseInt(fmt.Sprint(v), 10, 64)
	if e != nil || n < 0 || n > 10000000 {
		return nil, errors.New("预算上限须为 0 至 10000000 的整数，留空表示未设置")
	}
	return &n, nil
}
func saveProjectBudget(s *storage.Session, command string, p map[string]any) (map[string]any, error) {
	for _, key := range []string{"project_id", "revision", "max_dispatches", "max_concurrent", "unknown_cost_policy", "confirm"} {
		if _, ok := p[key]; !ok || len(p) != 6 {
			return nil, errors.New("预算字段不完整")
		}
	}
	id := str(p["project_id"])
	r, e := s.Execute("SELECT 1 FROM projects WHERE id=?", []any{id})
	if e != nil {
		return nil, e
	}
	if len(r.Rows) != 1 || p["confirm"] != true {
		return nil, errors.New("请确认生效项目与预算")
	}
	b, e := readBudgetPolicy(s, id)
	if e != nil {
		return nil, e
	}
	revision, e := budgetInteger(p["revision"])
	if e != nil || revision == nil || *revision != b.Revision {
		return nil, errors.New("预算版本已变更，请刷新")
	}
	mode := str(p["unknown_cost_policy"])
	if mode != "allow_unpriced" && mode != "block_unpriced" {
		return nil, errors.New("请选择费用无法确定时的策略")
	}
	if b.MaxDispatch, e = budgetInteger(p["max_dispatches"]); e != nil {
		return nil, e
	}
	if b.MaxConcurrent, e = budgetInteger(p["max_concurrent"]); e != nil {
		return nil, e
	}
	b.Revision++
	b.Unpriced = mode
	b.Command = command
	if e = putBudgetRow(s, "project_budget/1/"+id, b); e != nil {
		return nil, e
	}
	return map[string]any{"policy": b}, nil
}
func budgetPrefix(project string) string { return "dispatch_budget/1/" + project + "/" }
func budgetKey(b budgetEntry) string     { return budgetPrefix(b.Project) + b.Source + "/" + b.ID }

func budgetTotals(s *storage.Session, project string) (reserved, settled int64, err error) {
	prefix := budgetPrefix(project)
	r, e := s.Execute("SELECT json_extract(value,'$.state'),count(*) FROM core_meta WHERE key>=? AND key<? GROUP BY 1", []any{prefix, prefix + "\uffff"})
	if e != nil {
		return 0, 0, e
	}
	for _, row := range r.Rows {
		switch str(row[0]) {
		case "RESERVED":
			reserved = number(row[1])
		case "SETTLED":
			settled = number(row[1])
		}
	}
	// Earlier native routes keep their original attempt as the single accounting row.
	available, e := s.Execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('attempts','tasks')", nil)
	if e != nil {
		return 0, 0, e
	}
	if number(available.Rows[0][0]) != 2 {
		return reserved, settled, nil
	}
	r, e = s.Execute("SELECT a.state,count(*) FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE t.project_id=? AND coalesce(json_extract(t.spec,'$.execution'),'')!='partner_native' GROUP BY a.state", []any{project})
	if e != nil {
		return 0, 0, e
	}
	for _, row := range r.Rows {
		switch str(row[0]) {
		case "CANCELLED_NO_SEND", "FAILED_NO_SEND":
		case "COMPLETED", "FAILED", "CANCELLED", "STOPPED_RUNTIME", "INTERRUPTED", "SCOPE_VIOLATION", "TIMED_OUT", "BUDGET_EXHAUSTED":
			settled += number(row[1])
		default:
			reserved += number(row[1])
		}
	}
	return reserved, settled, nil
}
func checkBudget(s *storage.Session, project, meter string) (int64, error) {
	b, e := readBudgetPolicy(s, project)
	if e != nil {
		return 0, e
	}
	if b.Unpriced == "block_unpriced" && meter != "LOCAL_NO_MODEL" {
		return 0, fmt.Errorf("%w：本次模型费用缺少可确认上限", ErrToolBudgetBlocked)
	}
	if b.MaxDispatch == nil && b.MaxConcurrent == nil {
		return b.Revision, nil
	}
	active, used, e := budgetTotals(s, project)
	if e != nil {
		return 0, e
	}
	if b.MaxDispatch != nil && active+used >= *b.MaxDispatch {
		return 0, fmt.Errorf("%w：共享派发次数已达上限", ErrToolBudgetBlocked)
	}
	if b.MaxConcurrent != nil && active >= *b.MaxConcurrent {
		return 0, fmt.Errorf("%w：共享并发数已达上限", ErrToolBudgetBlocked)
	}
	return b.Revision, nil
}
func reserveBudget(s *storage.Session, b budgetEntry) error {
	var previous budgetEntry
	found, e := budgetRow(s, budgetKey(b), &previous)
	if e != nil || found {
		return e
	}
	revision, e := checkBudget(s, b.Project, b.Meter)
	if e != nil {
		return e
	}
	b.PolicyRevision = revision
	b.At = now()
	b.Observed = b.At
	b.State = "RESERVED"
	b.Outcome = "QUEUED"
	return putBudgetRow(s, budgetKey(b), b)
}
func updateBudgetEntry(s *storage.Session, key, state string, started, terminal, noStart bool) error {
	var b budgetEntry
	found, e := budgetRow(s, key, &b)
	if e != nil || !found || b.State != "RESERVED" {
		return e
	}
	if b.Outcome == state && (!started || b.Started) {
		return nil
	}
	b.Started = b.Started || started
	b.Outcome = state
	b.Observed = now()
	if terminal {
		b.State = "SETTLED"
		if noStart && !b.Started {
			b.State = "RELEASED"
		}
	}
	return putBudgetRow(s, key, b)
}
func reserveToolBudget(s *storage.Session, job Job) error {
	b := budgetEntry{ID: job.ID, Project: job.ProjectID, Task: job.TaskID, Attempt: job.AttemptID, Tool: job.ToolVersion, Source: "tool", Meter: "LOCAL_NO_MODEL", Schedule: job.ScheduleID, Round: job.RoundID}
	if job.Action == "terminal.run" {
		b.Meter = "UNPRICED_EXECUTION"
	}
	return reserveBudget(s, b)
}
func settleToolBudget(s *storage.Session, job Job) error {
	terminal := job.State == "COMPLETED" || job.State == "CANCELLED" || job.State == "FAILED"
	key := budgetPrefix(job.ProjectID) + "tool/" + job.ID
	return updateBudgetEntry(s, key, job.State, job.PID > 0, terminal, terminal && job.State != "COMPLETED" && job.PID == 0)
}
func accountPartnerBudget(s *storage.Session, run map[string]any) error {
	if str(run["mode"]) != "run" || str(run["project_id"]) == "" {
		return nil
	}
	b := budgetEntry{ID: str(run["id"]), Project: str(run["project_id"]), Task: str(run["parent_task_id"]), Attempt: str(run["parent_attempt_id"]), Partner: str(run["partner_id"]), Account: str(run["account_id"]), Source: "partner", Meter: "UNPRICED_EXECUTION"}
	state := str(run["state"])
	if state == "QUEUED" {
		return reserveBudget(s, b)
	}
	return updateBudgetEntry(s, budgetKey(b), state, state == "RUNNING", terminalPartner(state), state == "FAILED_NO_SEND" || state == "CANCELLED")
}
func (a *App) projectBudgetView(project string) (any, error) {
	s, e := a.DB.OpenSession(false)
	if e != nil {
		return nil, e
	}
	defer s.Rollback()
	r, e := s.Execute("SELECT 1 FROM projects WHERE id=?", []any{project})
	if e != nil {
		return nil, e
	}
	if len(r.Rows) == 0 || strings.TrimSpace(project) == "" {
		return nil, errors.New("未找到预算项目")
	}
	policy, e := readBudgetPolicy(s, project)
	if e != nil {
		return nil, e
	}
	active, used, e := budgetTotals(s, project)
	if e != nil {
		return nil, e
	}
	prefix := budgetPrefix(project)
	r, e = s.Execute("SELECT value FROM core_meta WHERE key>=? AND key<? ORDER BY json_extract(value,'$.observed_at') DESC,key LIMIT 100", []any{prefix, prefix + "\uffff"})
	if e != nil {
		return nil, e
	}
	entries := []budgetEntry{}
	for _, row := range r.Rows {
		var b budgetEntry
		if e = json.Unmarshal([]byte(str(row[0])), &b); e != nil {
			return nil, e
		}
		if b.Source == "tool" && b.State == "RESERVED" {
			current, err := s.Execute("SELECT state FROM core_tool_jobs WHERE id=?", []any{b.ID})
			if err != nil {
				return nil, err
			}
			if len(current.Rows) == 1 {
				b.Outcome = str(current.Rows[0][0])
			}
		}
		entries = append(entries, b)
	}
	return map[string]any{"policy": policy, "reserved_dispatches": active, "settled_dispatches": used, "unit": "dispatch", "period": "PROJECT_LIFETIME", "limits_supported": []string{"dispatch_count", "concurrent_dispatches", "block_unpriced"}, "project_money_cap": "NOT_ENFORCED", "project_token_cap": "NOT_ENFORCED", "entries": entries}, nil
}
