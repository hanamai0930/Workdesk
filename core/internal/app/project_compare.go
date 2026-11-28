package app

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/url"
	"strings"
	"unicode/utf8"
)

// Compare exact project versions, including selections whose source can change.
func (a *App) compareProjectVersions(project string, q url.Values) (any, error) {
	roots, err := a.query("SELECT root FROM projects WHERE id=?", project)
	if err != nil {
		return nil, err
	}
	if !uuidPattern.MatchString(project) || len(roots) != 1 {
		return nil, errors.New("项目不存在")
	}
	var texts [2][]byte
	versions := make([]map[string]any, 0, 2)
	for i, key := range []string{"left", "right"} {
		var ref projectVersion
		if json.Unmarshal([]byte(q.Get(key)), &ref) != nil || len(ref.SHA256) != 64 {
			return nil, errors.New("请选择两个准确版本")
		}
		row, raw, e := a.projectVersionBytes(project, str(roots[0]["root"]), ref, 64<<20)
		if e != nil {
			return nil, e
		}
		versions = append(versions, map[string]any{"version": ref, "name": row["name"], "bytes": row["bytes"], "task_id": row["task_id"], "attempt_id": row["attempt_id"]})
		texts[i] = raw
	}
	result := map[string]any{"versions": versions, "identical": bytes.Equal(texts[0], texts[1]), "mode": "bytes"}
	for _, b := range texts {
		if len(b) > 256<<10 || !utf8.Valid(b) || bytes.IndexByte(b, 0) >= 0 {
			return result, nil
		}
	}
	lines := func(b []byte) []string { return strings.Split(strings.ReplaceAll(string(b), "\r\n", "\n"), "\n") }
	left, right := lines(texts[0]), lines(texts[1])
	start := 0
	for start < len(left) && start < len(right) && left[start] == right[start] {
		start++
	}
	endLeft, endRight := len(left), len(right)
	for endLeft > start && endRight > start && left[endLeft-1] == right[endRight-1] {
		endLeft--
		endRight--
	}
	result["mode"] = "text"
	result["start_line"] = start + 1
	result["left_lines"], result["right_lines"] = endLeft-start, endRight-start
	result["left_excerpt"] = left[start:min(endLeft, start+80)]
	result["right_excerpt"] = right[start:min(endRight, start+80)]
	return result, nil
}
