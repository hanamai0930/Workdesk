package app

import (
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"strconv"
	"strings"

	"workdesk/core/internal/storage"
)

// Memory revisions and handoffs share the library transaction and backup boundary.
type knowledgeCitation struct {
	Project    string `json:"project_id"`
	Collection string `json:"collection_id"`
	Source     string `json:"source_id"`
	Revision   int64  `json:"source_revision"`
	Locator    string `json:"locator"`
	Hash       string `json:"text_sha256"`
}
type memorySource struct {
	Kind     string             `json:"kind"`
	ID       string             `json:"id,omitempty"`
	Revision int64              `json:"revision,omitempty"`
	Version  *projectVersion    `json:"version,omitempty"`
	Citation *knowledgeCitation `json:"citation,omitempty"`
	Title    string             `json:"title,omitempty"`
	Href     string             `json:"href,omitempty"`
}
type memoryRecord struct {
	ID       string         `json:"id"`
	Project  string         `json:"project_id"`
	Revision int64          `json:"revision"`
	State    string         `json:"state"`
	Area     string         `json:"area,omitempty"`
	Title    string         `json:"title,omitempty"`
	Body     string         `json:"body,omitempty"`
	Sources  []memorySource `json:"sources,omitempty"`
	Command  string         `json:"command_id"`
	Updated  float64        `json:"updated"`
}
type handoffLine struct {
	Text   string       `json:"text"`
	Source memorySource `json:"source"`
}

const memoryKind = "project_memory"
const memoryHistory = "project_memory_version"
const handoffKind = "project_handoff"

func putMemoryObject(s *storage.Session, kind, id string, value any) error {
	raw, err := json.Marshal(value)
	if err != nil {
		return err
	}
	_, err = s.Execute("INSERT INTO collaboration(kind,id,data,updated) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated=excluded.updated WHERE collaboration.kind=excluded.kind", []any{kind, id, string(raw), now()})
	return err
}
func memoryKey(id string, revision int64) string { return fmt.Sprintf("%s/r%d", id, revision) }
func writeMemory(s *storage.Session, row *memoryRecord, command string) error {
	row.Revision++
	row.Command = command
	row.Updated = now()
	if err := putMemoryObject(s, memoryKind, row.ID, row); err != nil {
		return err
	}
	if row.State == "DELETED" {
		return nil
	}
	return putMemoryObject(s, memoryHistory, memoryKey(row.ID, row.Revision), row)
}
func memoryScope(s *storage.Session, scope, project string) error {
	if scope == "personal" && project == "" {
		return nil
	}
	if scope != "project" || !uuidPattern.MatchString(project) {
		return errors.New("请选择个人范围或准确的项目")
	}
	rows, err := s.Execute("SELECT id FROM projects WHERE id=?", []any{project})
	if err != nil {
		return err
	}
	if len(rows.Rows) != 1 {
		return errors.New("项目不存在")
	}
	return nil
}
func getMemory(s *storage.Session, project, id string, revision int64, historical bool) (memoryRecord, error) {
	var row memoryRecord
	kind, key := memoryKind, id
	if historical {
		kind, key = memoryHistory, memoryKey(id, revision)
	}
	r, err := s.Execute("SELECT data FROM collaboration WHERE kind=? AND id=? AND json_extract(data,'$.project_id')=?", []any{kind, key, project})
	if err != nil {
		return row, err
	}
	if len(r.Rows) != 1 {
		return row, errors.New("范围内没有此记忆版本")
	}
	if err = json.Unmarshal([]byte(str(r.Rows[0][0])), &row); err != nil {
		return row, err
	}
	if row.State == "DELETED" || (revision > 0 && row.Revision != revision) {
		return row, errors.New("记忆已变更或删除，请刷新")
	}
	return row, nil
}
func memoryLink(project, id string, rev int64) string {
	q := url.Values{"id": {id}, "revision": {strconv.FormatInt(rev, 10)}}
	if project != "" {
		q.Set("project_id", project)
	}
	return "#resources/memory?" + q.Encode()
}
func sourceList(value any) ([]memorySource, error) {
	if value == nil {
		return nil, nil
	}
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	var refs []memorySource
	if json.Unmarshal(raw, &refs) != nil || len(refs) > 64 {
		return nil, errors.New("每次请选择至多64项准确来源")
	}
	return refs, nil
}
func validateMemorySources(s *storage.Session, project string, refs []memorySource) ([]memorySource, error) {
	result := make([]memorySource, 0, len(refs))
	seen := map[string]bool{}
	for _, ref := range refs {
		switch ref.Kind {
		case "memory":
			if ref.Revision <= 0 {
				return nil, errors.New("请选择记忆修订")
			}
			row, err := getMemory(s, project, ref.ID, ref.Revision, true)
			if err != nil {
				return nil, err
			}
			ref = memorySource{Kind: "memory", ID: row.ID, Revision: row.Revision, Title: row.Title, Href: memoryLink(project, row.ID, row.Revision)}
		case "work_item", "resource":
			query := "SELECT title,revision FROM work_items WHERE id=? AND project_id=?"
			args := []any{ref.ID, project}
			href := "#work-items/item/" + url.PathEscape(ref.ID)
			if ref.Kind == "resource" {
				if project != "" {
					return nil, errors.New("项目知识来源须携带集合授权、段落定位和版本")
				}
				query = "SELECT title,revision FROM resource_space_items WHERE id=? AND trashed_at IS NULL"
				args = []any{ref.ID}
				href = "#resources/item/" + url.PathEscape(ref.ID)
			}
			r, err := s.Execute(query, args)
			if err != nil {
				return nil, err
			}
			if len(r.Rows) != 1 || ref.Revision <= 0 || number(r.Rows[0][1]) != ref.Revision {
				return nil, errors.New("来源已更新或不属于当前范围")
			}
			ref = memorySource{Kind: ref.Kind, ID: ref.ID, Revision: ref.Revision, Title: str(r.Rows[0][0]), Href: href}
		case "knowledge":
			c := ref.Citation
			if c == nil || project == "" || c.Project != project || c.Locator == "" || c.Revision < 1 {
				return nil, errors.New("项目知识引用无效")
			}
			rows, err := s.Execute(`SELECT r.title,json_extract(chunk.value,'$.text') FROM resource_space_items r
                JOIN collaboration grantScope ON grantScope.kind='knowledge_scope_v1' AND grantScope.id='knowledge_scope_v1:'||?
                JOIN json_each(grantScope.data,'$.collection_ids') allowed ON allowed.value=r.collection_id
                JOIN collaboration idx ON idx.kind='knowledge_passages_v1' AND idx.id='knowledge_passages_v1:'||r.id
                    AND json_extract(idx.data,'$.source_revision')=r.revision AND json_extract(idx.data,'$.index_version')='resource-passages/1'
                JOIN json_each(idx.data,'$.chunks') chunk ON json_extract(chunk.value,'$.locator')=?
                WHERE r.id=? AND r.collection_id=? AND r.revision=? AND r.trashed_at IS NULL`,
				[]any{project, c.Locator, c.Source, c.Collection, c.Revision})
			if err != nil {
				return nil, err
			}
			if len(rows.Rows) != 1 || fmt.Sprintf("%x", sha256.Sum256([]byte(str(rows.Rows[0][1])))) != c.Hash {
				return nil, errors.New("知识段落、修订或集合授权已变化；请重建索引后重新选择")
			}
			ref = memorySource{Kind: "knowledge", ID: c.Source, Revision: c.Revision, Citation: c, Title: str(rows.Rows[0][0]) + " · " + c.Locator, Href: "#resources/item/" + url.PathEscape(c.Source)}
		case "material":
			v := ref.Version
			if project == "" || v == nil {
				return nil, errors.New("请选择当前项目材料版本")
			}
			catalogHash := v.SHA256
			if v.Kind == "reply" {
				raw, e := s.Execute(`SELECT json_extract(c.data,'$.snapshot.answer') FROM collaboration c JOIN tasks t ON t.id=json_extract(c.data,'$.task_id')
                    WHERE c.kind='work_result' AND c.id=? AND t.project_id=?`, []any{v.Source, project})
				if e != nil {
					return nil, e
				}
				if len(raw.Rows) != 1 || fmt.Sprintf("%x", sha256.Sum256([]byte(str(raw.Rows[0][0])))) != v.SHA256 {
					return nil, errors.New("任务回复版本已变化")
				}
				catalogHash = ""
			}
			r, err := s.Execute(projectMaterialRows+" WHERE kind=? AND source=? AND CAST(entry AS TEXT)=? AND sha256=?", []any{project, project, project, project, v.Kind, v.Source, v.Entry, catalogHash})
			if err != nil {
				return nil, err
			}
			if len(r.Rows) != 1 {
				return nil, errors.New("未找到该项目材料版本")
			}
			encoded, _ := json.Marshal(v)
			ref = memorySource{Kind: "material", Version: v, Title: str(r.Rows[0][3]), Href: "api/v2/projects/" + project + "/export?" + url.Values{"version": {string(encoded)}}.Encode()}
		default:
			return nil, errors.New("来源类型无效")
		}
		raw, _ := json.Marshal(ref)
		key := string(raw)
		if seen[key] {
			return nil, errors.New("来源重复")
		}
		seen[key] = true
		result = append(result, ref)
	}
	return result, nil
}
func applyMemory(s *storage.Session, command, kind string, p map[string]any) (map[string]any, error) {
	project := str(p["project_id"])
	if err := memoryScope(s, str(p["scope"]), project); err != nil {
		return nil, err
	}
	refs, err := sourceList(p["sources"])
	if err != nil {
		return nil, err
	}
	if kind == "project.handoff-create" {
		return makeProjectHandoff(s, command, project, refs)
	}
	row := memoryRecord{ID: "memory:" + ID(), Project: project, State: "ACTIVE"}
	id := str(p["id"])
	if id != "" || (kind != "memory.save" && kind != "memory.merge") {
		rev := number(p["revision"])
		if rev < 1 {
			return nil, errors.New("需要当前记忆修订号")
		}
		row, err = getMemory(s, project, id, rev, false)
		if err != nil {
			return nil, err
		}
	}
	switch kind {
	case "memory.save", "memory.merge":
		if row.State != "ACTIVE" {
			return nil, errors.New("请先恢复已归档记忆")
		}
		area, title, body := str(p["area"]), strings.TrimSpace(str(p["title"])), strings.TrimSpace(str(p["body"]))
		switch area {
		case "note", "goal", "decision", "blocker", "next_step":
		default:
			return nil, errors.New("记忆用途无效")
		}
		if title == "" || body == "" || len([]rune(title)) > 160 || len([]rune(body)) > 32768 {
			return nil, errors.New("标题须为1–160字，内容须为1–32768字")
		}
		if kind == "memory.merge" || p["sources"] != nil {
			if row.Sources, err = validateMemorySources(s, project, refs); err != nil {
				return nil, err
			}
		}
		if kind == "memory.merge" {
			if id != "" || len(refs) < 2 {
				return nil, errors.New("合并须选择至少两条当前记忆")
			}
			for _, ref := range row.Sources {
				if ref.Kind != "memory" {
					return nil, errors.New("合并仅接受记忆来源")
				}
				old, e := getMemory(s, project, ref.ID, ref.Revision, false)
				if e != nil {
					return nil, e
				}
				if old.State != "ACTIVE" {
					return nil, errors.New("合并来源必须是当前有效记忆")
				}
				old.State = "ARCHIVED"
				if e = writeMemory(s, &old, command); e != nil {
					return nil, e
				}
			}
		}
		row.Title = title
		row.Body = body
		row.Area = area
	case "memory.archive":
		archived, ok := p["archived"].(bool)
		if !ok {
			return nil, errors.New("需要指定归档或恢复")
		}
		row.State = "ACTIVE"
		if archived {
			row.State = "ARCHIVED"
		}
	case "memory.delete":
		if p["confirm"] != true {
			return nil, errors.New("删除须确认准确的记忆和修订")
		}
		_, err = s.Execute("DELETE FROM collaboration WHERE kind=? AND json_extract(data,'$.id')=?", []any{memoryHistory, row.ID})
		if err != nil {
			return nil, err
		}
		row.State = "DELETED"
		row.Area = ""
		row.Title = ""
		row.Body = ""
		row.Sources = nil
	default:
		return nil, errors.New("记忆操作无效")
	}
	if err = writeMemory(s, &row, command); err != nil {
		return nil, err
	}
	return map[string]any{"memory_id": row.ID, "revision": row.Revision, "state": row.State, "project_id": project}, nil
}
func makeProjectHandoff(s *storage.Session, command, project string, refs []memorySource) (map[string]any, error) {
	if project == "" || len(refs) == 0 {
		return nil, errors.New("请选择项目及要交接的来源")
	}
	refs, err := validateMemorySources(s, project, refs)
	if err != nil {
		return nil, err
	}
	sections := map[string][]handoffLine{}
	for _, k := range []string{"goals", "material_versions", "decisions", "open_questions", "next_steps", "notes"} {
		sections[k] = []handoffLine{}
	}
	for _, ref := range refs {
		add := func(section, text string) {
			if text != "" {
				sections[section] = append(sections[section], handoffLine{text, ref})
			}
		}
		switch ref.Kind {
		case "memory":
			row, e := getMemory(s, project, ref.ID, ref.Revision, true)
			if e != nil {
				return nil, e
			}
			add(map[string]string{"goal": "goals", "decision": "decisions", "blocker": "open_questions", "next_step": "next_steps", "note": "notes"}[row.Area], row.Body)
		case "work_item":
			rows, e := s.Execute("SELECT goal,next_step,constraints_text,progress FROM work_items WHERE id=? AND project_id=?", []any{ref.ID, project})
			if e != nil {
				return nil, e
			}
			r := rows.Rows[0]
			add("goals", str(r[0]))
			add("next_steps", str(r[1]))
			add("notes", str(r[2]))
			add("notes", str(r[3]))
		case "material":
			add("material_versions", ref.Title)
		case "resource", "knowledge":
			add("notes", ref.Title)
		}
	}
	id := "handoff:" + ID()
	row := map[string]any{"id": id, "revision": 1, "project_id": project, "command_id": command, "updated": now(), "sections": sections, "sources": refs}
	if err = putMemoryObject(s, handoffKind, id, row); err != nil {
		return nil, err
	}
	return map[string]any{"handoff_id": id, "revision": 1, "project_id": project}, nil
}
func (a *App) readProjectMemory(path string, q url.Values) (any, error) {
	s, err := a.DB.OpenSession(false)
	if err != nil {
		return nil, err
	}
	defer s.Rollback()
	project := q.Get("project_id")
	if err = memoryScope(s, q.Get("scope"), project); err != nil {
		return nil, err
	}
	id := q.Get("id")
	kind := memoryKind
	if path == "/api/v2/project-handoffs" {
		if project == "" {
			return nil, errors.New("请选择项目")
		}
		kind = handoffKind
	}
	if id != "" && kind == memoryKind {
		rev := int64(0)
		if q.Has("revision") {
			rev, err = strconv.ParseInt(q.Get("revision"), 10, 64)
			if err != nil || rev <= 0 {
				return nil, errors.New("修订号无效")
			}
		}
		row, e := getMemory(s, project, id, rev, rev > 0)
		if e != nil {
			return nil, e
		}
		history, e := s.Execute("SELECT json_extract(data,'$.revision'),json_extract(data,'$.command_id') FROM collaboration WHERE kind=? AND json_extract(data,'$.id')=? ORDER BY updated DESC", []any{memoryHistory, id})
		if e != nil {
			return nil, e
		}
		versions := []map[string]any{}
		for _, r := range history.Rows {
			versions = append(versions, map[string]any{"revision": r[0], "command_id": r[1]})
		}
		return map[string]any{"memory": row, "versions": versions}, nil
	}
	query := "SELECT data FROM collaboration WHERE kind=? AND json_extract(data,'$.project_id')=?"
	args := []any{kind, project}
	if kind == memoryKind {
		query += " AND json_extract(data,'$.state')!='DELETED'"
	}
	if id != "" {
		query += " AND id=?"
		args = append(args, id)
	}
	rows, e := s.Execute(query+" ORDER BY updated DESC", args)
	if e != nil {
		return nil, e
	}
	values := []json.RawMessage{}
	for _, r := range rows.Rows {
		values = append(values, json.RawMessage(str(r[0])))
	}
	if id != "" {
		if len(values) != 1 {
			return nil, errors.New("项目内没有此接续材料")
		}
		return map[string]any{"handoff": values[0]}, nil
	}
	if kind == memoryKind {
		return map[string]any{"memories": values}, nil
	}
	return map[string]any{"handoffs": values}, nil
}
