package app

import (
	"encoding/json"
	"errors"
	"net/url"
	"strconv"
	"strings"

	"workdesk/core/internal/storage"
)

// Source identities belong to the original round, request, attempt or Human command.
// Handling a notice updates its inbox disposition independently of the source operation.
const noticeRows = `WITH rounds AS (
 SELECT c.id AS round_id,c.data AS round,j.id AS job_id,
 CASE WHEN j.state IN ('COMPLETED','FAILED','CANCELLED','ORPHANED_UNKNOWN','OUTCOME_UNKNOWN','STOP_UNKNOWN') THEN j.state
 ELSE json_extract(c.data,'$.state') END AS outcome,coalesce(j.updated,c.updated) AS at
 FROM collaboration c LEFT JOIN core_tool_jobs j ON j.id=json_extract(c.data,'$.job_id') WHERE c.kind='tool_schedule_round'
), sources(data) AS (
 SELECT json_object('id','notice:round:'||round_id||':'||outcome,'category',
   CASE WHEN outcome='COMPLETED' THEN 'completion' WHEN outcome LIKE '%UNKNOWN%' THEN 'review'
        WHEN outcome='MISSED' THEN 'reminder' ELSE 'failure' END,
   'title',CASE WHEN outcome='COMPLETED' THEN '预约工具已完成' WHEN outcome LIKE '%UNKNOWN%' THEN '预约轮次待核对'
         WHEN outcome='MISSED' THEN '预约时间已错过' ELSE '预约工具未完成' END,
   'reason',json_extract(round,'$.reason'),'source_kind','tool_schedule_round','source_state',outcome,
   'project_id',json_extract(round,'$.project_id'),'schedule_id',json_extract(round,'$.schedule_id'),
   'round_id',round_id,'job_id',COALESCE(job_id,json_extract(round,'$.job_id')),
   'source_command_id',json_extract(round,'$.command_id'),'task_id',json_extract(round,'$.task_id'),
   'attempt_id',json_extract(round,'$.attempt_id'),'at',at,
   'href','#planning/round/'||round_id||'?project_id='||json_extract(round,'$.project_id'))
 FROM rounds WHERE outcome IN ('COMPLETED','FAILED','CANCELLED','ORPHANED_UNKNOWN','OUTCOME_UNKNOWN','STOP_UNKNOWN','MISSED','BLOCKED_SCOPE','BLOCKED_BUDGET')
 UNION ALL SELECT data FROM collaboration WHERE kind='inbox_notice_v1'
 UNION ALL SELECT json_object('id','notice:approval:'||r.id,'category','approval','title',t.title||' · 审批请求',
   'project_id',t.project_id,'task_id',r.task_id,'attempt_id',r.attempt_id,'approval_id',r.id,
   'source_kind','approval','source_state',r.state,'at',r.created,'href','#control/task/'||t.id)
 FROM approvals r JOIN tasks t ON t.id=r.task_id
 UNION ALL SELECT json_object('id','notice:native:'||r.id,'category','approval','title',t.title||' · 原生请求',
   'project_id',t.project_id,'task_id',r.task_id,'attempt_id',r.attempt_id,'request_id',r.id,
   'source_kind','native_request','source_state',r.state,'at',r.created,'href','#control/task/'||t.id)
 FROM native_requests r JOIN tasks t ON t.id=r.task_id
 UNION ALL SELECT json_object('id','notice:attempt:'||a.id||':'||a.state,'category',
   CASE WHEN a.state IN ('COMPLETED','SUCCEEDED') THEN 'completion' WHEN a.state LIKE '%UNKNOWN%' THEN 'review' ELSE 'failure' END,
   'title',t.title,'project_id',t.project_id,'task_id',t.id,'attempt_id',a.id,'source_kind','attempt','source_state',a.state,
   'at',coalesce(json_extract(a.result,'$.ended_at'),a.started),'href','#control/task/'||t.id)
 FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE a.state IN ('COMPLETED','SUCCEEDED','FAILED','FAILED_NO_START','INTERRUPTED','CANCELLED','ORPHANED_UNKNOWN','SEND_UNKNOWN','STOP_UNKNOWN','OUTCOME_UNKNOWN','UNKNOWN')
 UNION ALL SELECT json_object('id','notice:partner:'||r.id||':'||json_extract(pe.data,'$.request_id'),'category','approval',
   'title',json_extract(r.data,'$.partner_id')||' · 原生处理请求','project_id',json_extract(r.data,'$.project_id'),
   'task_id',json_extract(r.data,'$.parent_task_id'),'attempt_id',json_extract(r.data,'$.parent_attempt_id'),
   'request_id',json_extract(pe.data,'$.request_id'),'source_kind','partner_request','source_state',json_extract(r.data,'$.state'),
   'at',min(pe.at),'href','#work/partners/run/'||r.id)
 FROM core_partner_events pe JOIN core_partner_objects r ON r.kind='run' AND r.id=pe.run_id
 WHERE json_extract(pe.data,'$.type') IN ('approval','input') AND coalesce(json_extract(pe.data,'$.request_id'),'')!=''
 GROUP BY r.id,json_extract(pe.data,'$.request_id')
 UNION ALL SELECT json_object('id','notice:item:'||w.id||':'||w.revision,'category','approval','title',w.title||' · 等待验收',
   'project_id',w.project_id,'item_id',w.id,'source_kind','work_item','source_state',w.state,'at',w.updated,
   'href','#work-items/item/'||w.id||'/delivery') FROM work_items w WHERE w.state='READY_FOR_ACCEPTANCE'
), all_sources(data) AS (
 SELECT data FROM sources UNION ALL SELECT json_extract(m.data,'$.notice') FROM collaboration m WHERE m.kind='inbox_state_v1'
 AND NOT EXISTS(SELECT 1 FROM sources n WHERE json_extract(n.data,'$.id')=json_extract(m.data,'$.notice.id'))
) SELECT n.data,coalesce(json_extract(m.data,'$.state'),'OPEN'),coalesce(json_extract(m.data,'$.revision'),0),
 json_extract(m.data,'$.command_id'),json_extract(m.data,'$.note'),json_extract(m.data,'$.updated') FROM all_sources n
 LEFT JOIN collaboration m ON m.id='inbox-state:'||json_extract(n.data,'$.id') AND m.kind='inbox_state_v1' WHERE 1=1`

func notices(s *storage.Session, q url.Values) ([]map[string]any, error) {
	query, args := noticeRows, []any{}
	for _, filter := range []struct{ key, col string }{{"id", "id"}, {"project_id", "project_id"}, {"round_id", "round_id"}} {
		if value := q.Get(filter.key); value != "" {
			query += " AND json_extract(n.data,'$." + filter.col + "')=?"
			args = append(args, value)
		}
	}
	if state := q.Get("state"); state != "" {
		if state != "OPEN" && state != "HANDLED" {
			return nil, errors.New("收件状态无效")
		}
		query += " AND coalesce(json_extract(m.data,'$.state'),'OPEN')=?"
		args = append(args, state)
	}
	offset := 0
	if q.Has("cursor") {
		i, err := strconv.Atoi(q.Get("cursor"))
		if err != nil || i < 0 {
			return nil, errors.New("通知游标无效")
		}
		offset = i
	}
	args = append(args, bounded(q.Get("limit"), 50, 200)+1, offset)
	rows, err := s.Execute(query+" ORDER BY json_extract(n.data,'$.at') DESC,json_extract(n.data,'$.id') LIMIT ? OFFSET ?", args)
	if err != nil {
		return nil, err
	}
	result := []map[string]any{}
	for _, r := range rows.Rows {
		var item map[string]any
		if err = json.Unmarshal([]byte(str(r[0])), &item); err != nil {
			return nil, err
		}
		item["state"] = r[1]
		item["revision"] = number(r[2])
		item["handling_command_id"] = r[3]
		item["handling_note"] = r[4]
		item["handled_updated"] = r[5]
		if due, ok := item["due_at"].(float64); ok {
			item["due"] = due <= now()
		}
		result = append(result, item)
	}
	return result, nil
}

func (a *App) inbox(q url.Values) (any, error) {
	s, e := a.DB.OpenSession(false)
	if e != nil {
		return nil, e
	}
	defer s.Rollback()
	items, e := notices(s, q)
	if e != nil {
		return nil, e
	}
	if q.Get("id") != "" {
		if len(items) != 1 {
			return nil, errors.New("此范围未找到通知")
		}
		history, err := s.Execute("SELECT data FROM collaboration WHERE kind='inbox_transition_v1' AND json_extract(data,'$.notice_id')=? ORDER BY json_extract(data,'$.revision')", []any{items[0]["id"]})
		if err != nil {
			return nil, err
		}
		trail := make([]json.RawMessage, 0, len(history.Rows))
		for _, row := range history.Rows {
			trail = append(trail, json.RawMessage(str(row[0])))
		}
		return map[string]any{"notice": items[0], "history": trail}, nil
	}
	limit := bounded(q.Get("limit"), 50, 200)
	more := len(items) > limit
	if more {
		items = items[:limit]
	}
	off, _ := strconv.Atoi(q.Get("cursor"))
	return map[string]any{"notifications": items, "cursor": strconv.Itoa(off + len(items)), "has_more": more}, nil
}

func noticeCommand(s *storage.Session, command, kind string, p map[string]any) (map[string]any, error) {
	if kind == "notice.create" {
		category, title, project, task, attempt := str(p["category"]), strings.TrimSpace(str(p["title"])), str(p["project_id"]), str(p["task_id"]), str(p["attempt_id"])
		if (category != "todo" && category != "reminder") || title == "" || len([]rune(title)) > 240 {
			return nil, errors.New("请选择待办或提醒，并填写240字以内的标题")
		}
		if err := memoryScope(s, "project", project); err != nil {
			return nil, err
		}
		href := "#settings/notifications"
		if task != "" {
			r, e := s.Execute("SELECT id FROM tasks WHERE id=? AND project_id=?", []any{task, project})
			if e != nil {
				return nil, e
			}
			if len(r.Rows) != 1 {
				return nil, errors.New("任务不属于所选项目")
			}
			href = "#control/task/" + task
		}
		if attempt != "" {
			r, e := s.Execute("SELECT id FROM attempts WHERE task_id=? AND id=?", []any{task, attempt})
			if e != nil {
				return nil, e
			}
			if len(r.Rows) != 1 {
				return nil, errors.New("尝试与所选任务不符")
			}
		}
		id := "notice:manual:" + command
		item := map[string]any{"id": id, "category": category, "title": title, "project_id": project, "task_id": task, "attempt_id": attempt,
			"source_kind": "manual", "source_state": "RECORDED", "source_command_id": command, "href": href, "at": now()}
		if category == "reminder" {
			due := number(p["due_at"])
			if due <= 0 || due > 253402300799 {
				return nil, errors.New("请指定有效提醒时间")
			}
			item["due_at"] = due
		}
		if e := putMemoryObject(s, "inbox_notice_v1", id, item); e != nil {
			return nil, e
		}
		return map[string]any{"notice_id": id, "state": "OPEN", "revision": 0}, nil
	}
	id, state, revision := str(p["id"]), str(p["state"]), number(p["revision"])
	revValue, validRevision := p["revision"].(json.Number)
	_, parseErr := revValue.Int64()
	if id == "" || !validRevision || parseErr != nil || revision < 0 || (state != "OPEN" && state != "HANDLED") {
		return nil, errors.New("请指定通知、准确修订与处理状态")
	}
	rows, e := notices(s, url.Values{"id": {id}})
	if e != nil {
		return nil, e
	}
	if len(rows) != 1 || number(rows[0]["revision"]) != revision || rows[0]["state"] == state {
		return nil, errors.New("通知状态或修订已变化，请刷新")
	}
	item := rows[0]
	for _, k := range []string{"state", "revision", "handling_command_id", "handling_note", "handled_updated", "due"} {
		delete(item, k)
	}
	note := strings.TrimSpace(str(p["note"]))
	if len([]rune(note)) > 1000 {
		return nil, errors.New("处理说明最多1000字")
	}
	updated := map[string]any{"notice": item, "revision": revision + 1, "state": state, "command_id": command, "note": note, "updated": now()}
	if e = putMemoryObject(s, "inbox_state_v1", "inbox-state:"+id, updated); e != nil {
		return nil, e
	}
	event := map[string]any{"notice_id": id, "state": state, "revision": revision + 1, "note": note, "command_id": command, "updated": updated["updated"]}
	if e = putMemoryObject(s, "inbox_transition_v1", "inbox-transition:"+command, event); e != nil {
		return nil, e
	}
	return map[string]any{"notice_id": id, "state": state, "revision": revision + 1, "source_command_id": item["source_command_id"], "job_id": item["job_id"], "round_id": item["round_id"]}, nil
}
