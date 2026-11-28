package app

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"workdesk/core/internal/instance"
	"workdesk/core/internal/storage"
)

type chatCopy struct {
	name, source, representation string
	bytes                        []byte
	native                       bool
}

func chatObject(s *storage.Session, kind, id string) (map[string]any, error) {
	r, e := s.Execute("SELECT data FROM collaboration WHERE kind=? AND id=?", []any{kind, id})
	if e != nil {
		return nil, e
	}
	if len(r.Rows) != 1 {
		return nil, errors.New("选择的聊天或附件已不可读取")
	}
	var row map[string]any
	e = json.Unmarshal([]byte(str(r.Rows[0][0])), &row)
	return row, e
}
func chatSelection(v any) (map[string]bool, error) {
	vals, ok := v.([]any)
	if !ok || len(vals) > 64 {
		return nil, errors.New("请选择至多 64 项材料")
	}
	ids := map[string]bool{}
	for _, v := range vals {
		id := str(v)
		if id == "" || ids[id] {
			return nil, errors.New("材料标识无效或重复")
		}
		ids[id] = true
	}
	return ids, nil
}
func selectedChatCopies(s *storage.Session, chat, p map[string]any) ([]chatCopy, error) {
	messages, e := chatSelection(p["message_ids"])
	if e != nil {
		return nil, e
	}
	files, e := chatSelection(p["attachment_ids"])
	if e != nil {
		return nil, e
	}
	list := []chatCopy{}
	var transcript strings.Builder
	for _, v := range chat["messages"].([]any) {
		m, _ := v.(map[string]any)
		id := str(m["id"])
		if messages[id] {
			if str(m["content"]) == "" {
				return nil, errors.New("消息正文不可用")
			}
			fmt.Fprintf(&transcript, "%s [%s]\n%s\n\n", str(m["role"]), id, str(m["content"]))
			delete(messages, id)
		}
		refs, _ := m["attachment_ids"].([]any)
		for _, key := range refs {
			fid := str(key)
			if !files[fid] {
				continue
			}
			a, e := chatObject(s, "ui_chat_attachment", fid)
			if e != nil {
				return nil, e
			}
			if a["chat_id"] != chat["id"] {
				return nil, errors.New("附件归属已变化")
			}
			name := str(a["name"])
			if name == "" || !filepath.IsLocal(name) || filepath.Base(name) != name || strings.ContainsAny(name, ":/\\") {
				return nil, errors.New("附件名称无效")
			}
			c := chatCopy{name: name + ".txt", source: fid, representation: "extracted_text", bytes: []byte(str(a["text"]))}
			if strings.HasSuffix(strings.ToLower(name), ".txt") {
				c.name = name
			}
			if a["kind"] == "image" {
				_, encoded, ok := strings.Cut(str(a["data_url"]), ";base64,")
				if !ok {
					return nil, errors.New("图片内容不可读")
				}
				c.bytes, e = base64.StdEncoding.DecodeString(encoded)
				if e != nil {
					return nil, e
				}
				c.native = true
				c.name = name
				c.representation = "image_bytes"
			}
			list = append(list, c)
			delete(files, fid)
		}
	}
	if len(messages)+len(files) > 0 {
		return nil, errors.New("消息或附件不属于所选聊天")
	}
	if p["include_prompt"] == true && str(chat["prompt"]) != "" {
		fmt.Fprintln(&transcript, str(chat["prompt"]))
	}
	if p["include_link"] == true && str(chat["web_url"]) != "" {
		fmt.Fprintln(&transcript, str(chat["web_url"]))
	}
	if note := str(p["pasted"]); note != "" {
		if len([]rune(note)) > 12000 {
			return nil, errors.New("粘贴材料超过 12000 字")
		}
		fmt.Fprintln(&transcript, "用户提供：", note)
	}
	if supplied, ok := p["local_files"].([]any); ok {
		if len(supplied) > 8 {
			return nil, errors.New("每次最多补充 8 份原始文件")
		}
		for _, v := range supplied {
			row, ok := v.(map[string]any)
			if !ok {
				return nil, errors.New("补充文件无效")
			}
			name := str(row["name"])
			if !filepath.IsLocal(name) || filepath.Base(name) != name || strings.ContainsAny(name, ":/\\") || strings.HasSuffix(name, ".") || strings.HasSuffix(name, " ") {
				return nil, errors.New("补充文件名称无效")
			}
			b, e := base64.StdEncoding.DecodeString(str(row["data"]))
			if e != nil || len(b) == 0 || len(b) > 8<<20 {
				return nil, errors.New("补充文件须为 1 字节至 8 MiB")
			}
			list = append(list, chatCopy{name: name, source: name, representation: "user_file", bytes: b, native: true})
		}
	}
	if transcript.Len() > 0 {
		list = append(list, chatCopy{name: "聊天选段.txt", source: str(chat["id"]), representation: "selected_text", bytes: []byte(transcript.String())})
	}
	if len(list) == 0 {
		return nil, errors.New("请选择至少一段消息、附件或网页材料")
	}
	return list, nil
}

// Publish all selected bytes together. An interrupted transaction may leave a complete
// directory, which is reusable only when every requested digest still matches.
func saveChatCopies(root, id string, list []chatCopy) ([]map[string]any, []string, []map[string]string, error) {
	var selected []map[string]any
	texts := []string{}
	attachments := []map[string]string{}
	base := filepath.Join(root, "workdesk-inputs")
	dir := filepath.Join(base, id)
	if !inside(dir, root) {
		return nil, nil, nil, errors.New("项目材料位置不可用")
	}
	if e := os.MkdirAll(base, 0700); e != nil {
		return nil, nil, nil, e
	}
	tmp, e := os.MkdirTemp(base, ".chat-transfer-")
	if e != nil {
		return nil, nil, nil, e
	}
	defer os.RemoveAll(tmp)
	total := 0
	for i, c := range list {
		total += len(c.bytes)
		if len(c.bytes) == 0 || len(c.bytes) > 8<<20 || (!c.native && len(c.bytes) > 1<<20) || total > 8<<20 {
			return nil, nil, nil, errors.New("选段与附件合计最多 8 MiB，每份可读文本最多 1 MiB，请缩小范围")
		}
		if c.native && len(attachments) >= 8 {
			return nil, nil, nil, errors.New("每个任务最多接收 8 份原始附件")
		}
		name := fmt.Sprintf("%02d-%s", i+1, c.name)
		if len([]rune(filepath.ToSlash(filepath.Join("workdesk-inputs", id, name)))) > 240 {
			return nil, nil, nil, errors.New("材料路径超过 240 字符，请缩短名称")
		}
		rel := filepath.ToSlash(filepath.Join("workdesk-inputs", id, name))
		if e = os.WriteFile(filepath.Join(tmp, name), c.bytes, 0600); e != nil {
			return nil, nil, nil, e
		}
		selected = append(selected, map[string]any{"file": rel, "bytes": len(c.bytes), "sha256": fmt.Sprintf("%x", sha256.Sum256(c.bytes)), "source_id": c.source, "representation": c.representation})
		if c.native {
			attachments = append(attachments, map[string]string{"file": rel, "route": "native"})
		} else {
			texts = append(texts, rel)
		}
	}
	if _, e = os.Lstat(dir); os.IsNotExist(e) {
		e = os.Rename(tmp, dir)
	}
	if e != nil {
		return nil, nil, nil, e
	}
	for _, r := range selected {
		data, e := inputBytes(root, str(r["file"]))
		if e != nil || fmt.Sprintf("%x", sha256.Sum256(data)) != r["sha256"] {
			return nil, nil, nil, errors.New("项目材料副本与选定版本不符")
		}
	}
	return selected, texts, attachments, nil
}
func (a *App) chatHandoff(s *storage.Session, id string, p map[string]any) (map[string]any, error) {
	project := str(p["project_id"])
	goal := strings.TrimSpace(str(p["goal"]))
	if p["confirm"] != true || goal == "" || len([]rune(goal)) > 12000 {
		return nil, errors.New("请输入工作目标并确认所选材料")
	}
	root, e := projectRoot(s, project)
	if e != nil {
		return nil, e
	}
	for _, r := range append(append([]string{}, a.Profile.ProtectedRoots...), a.Profile.ProgramRoot, a.Profile.DataRoot) {
		if r != "" && (instance.SamePath(root, r) || instance.Within(root, r) || instance.Within(r, root)) {
			return nil, errors.New("请选择独立项目目录")
		}
	}
	removed, e := s.Execute("SELECT 1 FROM collaboration WHERE kind='work_project' AND id=? AND json_extract(data,'$.removed')=1", []any{"work-project:" + project})
	if e != nil {
		return nil, e
	}
	if len(removed.Rows) > 0 {
		return nil, errors.New("项目已移除")
	}
	chat, e := chatObject(s, "ui_chat", str(p["chat_id"]))
	if e != nil {
		return nil, e
	}
	if number(chat["revision"]) != number(p["revision"]) || chat["pending"] != nil {
		return nil, errors.New("聊天仍在运行或版本已变化")
	}
	if chat["messages"] == nil {
		chat["messages"] = []any{}
	}
	if _, ok := chat["messages"].([]any); !ok {
		return nil, errors.New("聊天消息不可读取")
	}
	list, e := selectedChatCopies(s, chat, p)
	if e != nil {
		return nil, e
	}
	selected, texts, att, e := saveChatCopies(root, id, list)
	if e != nil {
		return nil, e
	}
	names := []string{}
	for _, f := range selected {
		names = append(names, str(f["file"]))
	}
	source := map[string]any{"command_id": id, "chat_id": chat["id"], "revision": chat["revision"], "forked_from": chat["forked_from"], "message_ids": p["message_ids"], "attachment_ids": p["attachment_ids"], "include_link": p["include_link"], "include_prompt": p["include_prompt"]}
	selectionID := "file-selection:" + id
	selection := map[string]any{"id": selectionID, "revision": id, "project_id": project, "selected": selected, "files": texts, "attachments": att, "chat_source": source, "created": now()}
	if e = putMemoryObject(s, "file_selection", selectionID, selection); e != nil {
		return nil, e
	}
	draftID := "chat-work-" + id
	form := map[string]any{"fileSelection": map[string]any{"id": selectionID, "revision": id, "files": names}, "chatSource": source}
	draft := map[string]any{"id": draftID, "revision": 1, "created": now(), "updated": now(), "value": map[string]any{"mode": "work", "type": "independent", "title": "来自 Chat · " + str(chat["title"]), "project": project, "input": goal, "files": strings.Join(texts, "\n"), "attachments": att, "formDraft": form}}
	if e = putMemoryObject(s, "ui_draft", draftID, draft); e != nil {
		return nil, e
	}
	return map[string]any{"draft_id": draftID, "selection_id": selectionID, "chat_source": source, "no_dispatch": true}, nil
}
