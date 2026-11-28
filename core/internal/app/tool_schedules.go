package app

import (
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"reflect"
	"slices"
	"time"
	_ "time/tzdata"

	"workdesk/core/internal/storage"
)

var scheduledActions = []string{"file.read", "env.inspect"}

type toolSchedule struct {
	ID        string    `json:"schedule_id"`
	ProjectID string    `json:"project_id"`
	Grant     toolGrant `json:"authorization"`
	FirstAt   int64     `json:"first_at"`
	Timezone  string    `json:"timezone"`
	Repeat    string    `json:"repeat"`
	MaxRuns   int64     `json:"max_runs"`
	Issued    int64     `json:"round_count"`
	NextAt    int64     `json:"next_at"`
	State     string    `json:"state"`
	Revision  int64     `json:"revision"`
	Reason    string    `json:"reason,omitempty"`
}

type toolRound struct {
	ID       string `json:"round_id"`
	Schedule string `json:"schedule_id"`
	Project  string `json:"project_id"`
	Task     string `json:"task_id,omitempty"`
	Attempt  string `json:"attempt_id,omitempty"`
	At       int64  `json:"at"`
	State    string `json:"state"`
	Job      string `json:"job_id,omitempty"`
	Command  string `json:"command_id,omitempty"`
	Reason   string `json:"reason,omitempty"`
}

func scheduleTime(p map[string]any, zone *time.Location) (time.Time, error) {
	local, at := str(p["first_local"]), str(p["first_at"])
	var first time.Time
	var err error
	switch {
	case local != "" && at == "":
		layout := "2006-01-02T15:04"
		if len(local) == 19 {
			layout += ":05"
		}
		first, err = time.ParseInLocation(layout, local, zone)
		if err != nil || first.Format(layout) != local {
			return first, errors.New("当地日期或时间无效，可能落在夏令时跳过的时段")
		}
	case at != "" && local == "":
		first, err = time.Parse(time.RFC3339, at)
		if err != nil || first.Nanosecond() != 0 {
			return first, errors.New("首次时间需要含时区的完整秒")
		}
		_, given := first.Zone()
		_, expected := first.In(zone).Zone()
		if given != expected {
			return first, errors.New("首次时间偏移与所选时区不符")
		}
	default:
		return first, errors.New("请指定一个首次执行时间")
	}
	if first.Unix() <= time.Now().Unix() {
		return first, errors.New("首次时间应在将来")
	}
	return first, nil
}

func nextToolDue(plan *toolSchedule) int64 {
	if plan.Repeat == "once" || plan.Issued >= plan.MaxRuns {
		return 0
	}
	zone, _ := time.LoadLocation(plan.Timezone)
	due := time.Unix(plan.NextAt, 0).In(zone)
	first := time.Unix(plan.FirstAt, 0).In(zone)
	interval := 1
	if plan.Repeat == "weekly" {
		interval = 7
	}
	// Generate calendar dates from local noon. Skip a nonexistent wall time
	// and schedule just one occurrence on a repeated fall-back clock hour.
	day := time.Date(due.Year(), due.Month(), due.Day(), 12, 0, 0, 0, zone)
	for {
		day = day.AddDate(0, 0, interval)
		next := time.Date(day.Year(), day.Month(), day.Day(), first.Hour(), first.Minute(), first.Second(), 0, zone)
		if next.Day() == day.Day() && next.Hour() == first.Hour() && next.Minute() == first.Minute() {
			return next.Unix()
		}
	}
}

func (a *App) checkScheduledScope(s *storage.Session, plan *toolSchedule) error {
	g := plan.Grant
	if g.Tool != computerVersion || g.Implementation != a.Implementation || !slices.Contains(scheduledActions, str(g.Parameters["action"])) {
		return errors.New("实现或准入清单已改变，请重新建立计划")
	}
	if err := checkToolBinding(s, g); err != nil {
		return err
	}
	g.Inputs = []Artifact{}
	if err := a.freezeComputerGrant(&g, nil); err != nil {
		return err
	}
	if !reflect.DeepEqual(g.Inputs, plan.Grant.Inputs) {
		return errors.New("所选材料自计划授权后发生变化")
	}
	return nil
}

func (a *App) scheduleCommand(s *storage.Session, id, kind string, p map[string]any) (map[string]any, error) {
	var plan toolSchedule
	if kind == "schedule.tool-create" {
		params, ok := p["parameters"].(map[string]any)
		action := str(params["action"])
		if !ok || p["tool_version"] != computerVersion || !slices.Contains(scheduledActions, action) || p["confirm"] != true {
			return nil, errors.New("请确认本机已准入的只读工具与准确参数")
		}
		if (action == "file.read" && (len(params) != 2 || str(params["path"]) == "")) || (action == "env.inspect" && len(params) != 1) {
			return nil, errors.New("计划只接受所选动作的明确参数")
		}
		zoneName := str(p["timezone"])
		zone, err := time.LoadLocation(zoneName)
		if err != nil || zoneName == "" || zoneName == "Local" {
			return nil, errors.New("请指定有效 IANA 时区")
		}
		first, err := scheduleTime(p, zone)
		if err != nil {
			return nil, err
		}
		freq := str(p["repeat"])
		limit := number(p["max_runs"])
		if !slices.Contains([]string{"once", "daily", "weekly"}, freq) || limit < 1 || limit > 366 || (freq == "once" && limit != 1) {
			return nil, errors.New("计划次数须为 1 至 366，一次性计划为 1")
		}
		project := str(p["project_id"])
		if !uuidPattern.MatchString(project) {
			return nil, errors.New("请选择已登记项目")
		}
		granted, err := a.createToolGrant(s, id, p)
		if err != nil {
			return nil, err
		}
		g := granted["grant"].(toolGrant)
		g.Expires = 0 // Every due round receives a fresh, single-use execution grant.
		g.Process = ""
		g.Actor = "Human recurring schedule authorization"
		plan = toolSchedule{ID: id, ProjectID: project, Grant: g, FirstAt: first.Unix(), NextAt: first.Unix(),
			Timezone: zoneName, Repeat: freq, MaxRuns: limit, State: "ACTIVE"}
	} else {
		if len(p) != 4 || p["confirm"] != true {
			return nil, errors.New("请确认计划及当前修订")
		}
		rows, err := s.Execute("SELECT data FROM collaboration WHERE kind='tool_schedule' AND id=?", []any{p["schedule_id"]})
		if err != nil {
			return nil, err
		}
		if len(rows.Rows) != 1 {
			return nil, errors.New("计划不存在")
		}
		if err = json.Unmarshal([]byte(str(rows.Rows[0][0])), &plan); err != nil {
			return nil, err
		}
		state := str(p["state"])
		if plan.Revision != number(p["revision"]) || !slices.Contains([]string{"PAUSED", "ACTIVE", "CANCELLED"}, state) ||
			state == plan.State || !slices.Contains([]string{"ACTIVE", "PAUSED"}, plan.State) {
			return nil, errors.New("此计划修订已变化或不可执行该操作")
		}
		if state == "ACTIVE" {
			if plan.NextAt <= time.Now().Unix() {
				return nil, errors.New("下次轮次已过期，请重新选择未来时间建计划")
			}
			if err = a.checkScheduledScope(s, &plan); err != nil {
				return nil, err
			}
			unknown, err := s.Execute("SELECT 1 FROM collaboration WHERE kind='tool_schedule_round' AND json_extract(data,'$.schedule_id')=? AND json_extract(data,'$.state') IN ('ORPHANED_UNKNOWN','STOP_UNKNOWN','OUTCOME_UNKNOWN') LIMIT 1", []any{plan.ID})
			if err != nil {
				return nil, err
			}
			if len(unknown.Rows) > 0 {
				return nil, errors.New("先核对原轮次；后续授权请另建计划")
			}
		}
		plan.State = state
		plan.Reason = ""
	}
	plan.Revision++
	if err := writePlan(s, &plan); err != nil {
		return nil, err
	}
	return map[string]any{"schedule_id": plan.ID, "project_id": plan.ProjectID, "state": plan.State, "revision": plan.Revision, "next_at": plan.NextAt}, nil
}

func writePlan(s *storage.Session, plan *toolSchedule) error {
	raw, err := json.Marshal(plan)
	if err != nil {
		return err
	}
	_, err = s.Execute("INSERT INTO collaboration(kind,id,data,updated) VALUES('tool_schedule',?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated=excluded.updated WHERE collaboration.kind='tool_schedule'", []any{plan.ID, string(raw), now()})
	return err
}

func toolOccurrenceID(schedule string, due int64, purpose string) string {
	sum := sha256.Sum256([]byte(fmt.Sprintf("workdesk.tool.1/%s/%d/%s", schedule, due, purpose)))
	sum[6] = sum[6]&0x0f | 0x50
	sum[8] = sum[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", sum[:4], sum[4:6], sum[6:8], sum[8:10], sum[10:16])
}

func recordScheduleCommand(s *storage.Session, id, kind string, p, receipt map[string]any) error {
	raw, err := json.Marshal(p)
	if err != nil {
		return err
	}
	data, err := json.Marshal(receipt)
	if err != nil {
		return err
	}
	_, err = s.Execute("INSERT INTO commands VALUES(?,?,?,?,?)", []any{id, kind, fmt.Sprintf("%x", sha256.Sum256(raw)), string(data), now()})
	if err != nil {
		return err
	}
	_, err = s.Execute("INSERT INTO core_command_metadata VALUES(?,?)", []any{id, "core-json-v2"})
	return err
}

func (a *App) dispatchToolRound(s *storage.Session, p *toolSchedule, r *toolRound) (*Job, error) {
	g := p.Grant
	g.ID = toolOccurrenceID(p.ID, r.At, "grant")
	g.Expires = now() + 900
	g.Process = a.Process
	cmdID := toolOccurrenceID(p.ID, r.At, "command")
	job := a.newAuthorizedJob(g)
	job.CommandID = cmdID
	job.ScheduleID = p.ID
	job.RoundID = r.ID
	if err := insertToolJob(s, job); err != nil {
		return nil, err
	}
	source := map[string]any{"schedule_id": p.ID, "round_id": r.ID, "source_authorization_id": p.Grant.ID}
	if err := recordScheduleCommand(s, g.ID, "tool.authorize", source, map[string]any{"authorization_id": g.ID, "state": "AUTHORIZED", "grant": g, "schedule_id": p.ID, "round_id": r.ID}); err != nil {
		return nil, err
	}
	if err := recordScheduleCommand(s, cmdID, "tool.run", map[string]any{"authorization_id": g.ID}, map[string]any{"invocation_id": job.ID, "state": "QUEUED", "round_id": r.ID, "schedule_id": p.ID, "real_model_requests": 0}); err != nil {
		return nil, err
	}
	r.Job = job.ID
	r.Command = cmdID
	r.State = "QUEUED"
	return job, nil
}

func (a *App) issueToolRound(s *storage.Session, p *toolSchedule, timestamp int64) (*Job, error) {
	r := toolRound{ID: toolOccurrenceID(p.ID, p.NextAt, "round"), Schedule: p.ID, Project: p.ProjectID, Task: p.Grant.TaskID, Attempt: p.Grant.AttemptID, At: p.NextAt}
	var job *Job
	switch {
	case p.NextAt < a.startedAt || timestamp-p.NextAt > 120:
		r.State = "MISSED"
		r.Reason = "实例未在原轮次的派发窗口内运行"
	default:
		if err := a.checkScheduledScope(s, p); err != nil {
			r.State = "BLOCKED_SCOPE"
			r.Reason = err.Error()
		} else {
			// The reservation, once-only grant, invocation, command receipts,
			// occurrence and advancing the cursor share a single transaction.
			var err error
			job, err = a.dispatchToolRound(s, p, &r)
			if err != nil {
				if !errors.Is(err, ErrToolBudgetBlocked) {
					return nil, err
				}
				r.State = "BLOCKED_BUDGET"
				r.Reason = err.Error()
			}
		}
	}
	raw, err := json.Marshal(r)
	if err != nil {
		return nil, err
	}
	if _, err = s.Execute("INSERT INTO collaboration(kind,id,data,updated) VALUES('tool_schedule_round',?,?,?)", []any{r.ID, string(raw), now()}); err != nil {
		return nil, err
	}
	p.Issued++
	p.NextAt = nextToolDue(p)
	if p.NextAt == 0 {
		p.State = "MISSED"
		if job != nil {
			p.State = "DISPATCHED"
		}
	}
	if r.State == "BLOCKED_SCOPE" || r.State == "BLOCKED_BUDGET" {
		p.State = "PAUSED"
		p.Reason = r.Reason
	}
	p.Revision++
	if err = writePlan(s, p); err != nil {
		return nil, err
	}
	_, err = s.Execute("INSERT INTO events(at,task_id,attempt_id,kind,payload) VALUES(?,?,?,?,?)", []any{now(), nullID(r.Task), nullID(r.Attempt), "schedule.round", string(raw)})
	return job, err
}

func (a *App) tickToolSchedules() error {
	stamp := time.Now().Unix()
	rows, err := a.query("SELECT id FROM collaboration WHERE kind='tool_schedule' AND json_extract(data,'$.state')='ACTIVE' AND json_extract(data,'$.next_at')<=? ORDER BY updated,id LIMIT 8", stamp)
	if err != nil {
		return err
	}
	for _, row := range rows {
		a.admission.Lock()
		if a.retiring.Load() {
			a.admission.Unlock()
			return nil
		}
		var job *Job
		err = a.write(func(s *storage.Session) error {
			values, e := s.Execute("SELECT data FROM collaboration WHERE id=? AND kind='tool_schedule' AND json_extract(data,'$.state')='ACTIVE' AND json_extract(data,'$.next_at')<=?", []any{row["id"], stamp})
			if e != nil || len(values.Rows) == 0 {
				return e
			}
			var p toolSchedule
			if e = json.Unmarshal([]byte(str(values.Rows[0][0])), &p); e != nil {
				return e
			}
			job, e = a.issueToolRound(s, &p, stamp)
			return e
		})
		if err == nil && job != nil {
			a.launchToolJob(job)
		}
		a.admission.Unlock()
		if err != nil {
			return err
		}
	}
	return nil
}

func updateScheduleRound(s *storage.Session, job *Job) error {
	if job.ScheduleID == "" {
		return nil
	}
	_, err := s.Execute("UPDATE collaboration SET data=json_set(data,'$.state',?,'$.reason',?),updated=? WHERE kind='tool_schedule_round' AND id=? AND json_extract(data,'$.job_id')=?", []any{job.State, job.Error, now(), job.RoundID, job.ID})
	if err != nil {
		return err
	}
	if slices.Contains([]string{"STOP_UNKNOWN", "ORPHANED_UNKNOWN", "OUTCOME_UNKNOWN"}, job.State) {
		_, err = s.Execute("UPDATE collaboration SET data=json_set(data,'$.state','PAUSED','$.reason',?,'$.revision',json_extract(data,'$.revision')+1),updated=? WHERE kind='tool_schedule' AND id=? AND json_extract(data,'$.state') IN ('ACTIVE','DISPATCHED')", []any{"原轮次结果待核对，后续派发已暂停", now(), job.ScheduleID})
	} else if slices.Contains([]string{"COMPLETED", "FAILED", "CANCELLED"}, job.State) {
		final := "COMPLETE"
		if job.State != "COMPLETED" {
			final = job.State
		}
		_, err = s.Execute("UPDATE collaboration SET data=json_set(data,'$.state',?,'$.reason',?,'$.revision',json_extract(data,'$.revision')+1),updated=? WHERE kind='tool_schedule' AND id=? AND json_extract(data,'$.state')='DISPATCHED'", []any{final, job.Error, now(), job.ScheduleID})
	}
	return err
}

func recoverToolRounds(s *storage.Session) error {
	tables, err := s.Execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collaboration'", nil)
	if err != nil || len(tables.Rows) == 0 {
		return err
	}
	rows, err := s.Execute("SELECT j.data FROM core_tool_jobs j JOIN collaboration c ON c.kind='tool_schedule_round' AND c.id=json_extract(j.data,'$.round_id') WHERE j.state='ORPHANED_UNKNOWN' AND json_extract(c.data,'$.state') IN ('QUEUED','RUNNING','STOP_REQUESTED')", nil)
	if err != nil {
		return err
	}
	for _, row := range rows.Rows {
		var job Job
		if err = json.Unmarshal([]byte(str(row[0])), &job); err != nil {
			return err
		}
		job.State = "ORPHANED_UNKNOWN"
		job.Error = "上个进程未登记明确终态，原轮次不重发"
		if err = updateScheduleRound(s, &job); err != nil {
			return err
		}
	}
	return nil
}

func (a *App) toolScheduleView(q url.Values) (any, error) {
	project := q.Get("project_id")
	if !uuidPattern.MatchString(project) {
		return nil, errors.New("请选择已登记项目")
	}
	result := map[string]any{"actions": scheduledActions}
	if round := q.Get("round_id"); round != "" {
		rows, err := a.query("SELECT r.data AS occurrence,p.data AS plan FROM collaboration r JOIN collaboration p ON p.kind='tool_schedule' AND p.id=json_extract(r.data,'$.schedule_id') WHERE r.kind='tool_schedule_round' AND r.id=? AND json_extract(r.data,'$.project_id')=? AND json_extract(p.data,'$.project_id')=?", round, project, project)
		if err != nil {
			return nil, err
		}
		if len(rows) != 1 {
			return nil, errors.New("该轮次不属于当前项目")
		}
		for source, key := range map[string]string{"occurrence": "rounds", "plan": "schedules"} {
			var v map[string]any
			if err = json.Unmarshal([]byte(str(rows[0][source])), &v); err != nil {
				return nil, err
			}
			result[key] = []any{v}
		}
		return result, nil
	}
	limit := bounded(q.Get("limit"), 40, 200)
	for kind, key := range map[string]string{"tool_schedule": "schedules", "tool_schedule_round": "rounds"} {
		rows, err := a.query("SELECT data FROM collaboration WHERE kind=? AND json_extract(data,'$.project_id')=? ORDER BY updated DESC,id LIMIT ?", kind, project, limit)
		if err != nil {
			return nil, err
		}
		values := make([]map[string]any, 0, len(rows))
		for _, row := range rows {
			var value map[string]any
			if err = json.Unmarshal([]byte(str(row["data"])), &value); err != nil {
				return nil, err
			}
			values = append(values, value)
		}
		result[key] = values
	}
	return result, nil
}
