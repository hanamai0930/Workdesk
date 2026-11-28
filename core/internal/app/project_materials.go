package app

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// The identity includes the source record, so identical bytes in different
// tasks remain separately attributable. Never resolve a path supplied by a URL.
const projectMaterialRows = `WITH native_results AS (
 SELECT c.id,c.data,t.id AS task_id,c.updated FROM collaboration c JOIN tasks t ON t.id=json_extract(c.data,'$.task_id')
 WHERE c.kind='work_result' AND t.project_id=?
), versions AS (
 SELECT 'selection' AS kind,c.id AS source,f.key AS entry,json_extract(f.value,'$.file') AS name,
 json_extract(f.value,'$.sha256') AS sha256,json_extract(f.value,'$.bytes') AS bytes,
 json_extract(f.value,'$.file') AS relative_path,'' AS task_id,'' AS attempt_id,c.updated AS at
 FROM collaboration c,json_each(c.data,'$.selected') f
 WHERE c.kind='file_selection' AND json_extract(c.data,'$.project_id')=?
 UNION ALL
 SELECT CASE WHEN json_extract(f.value,'$.source_job_id') IS NULL THEN 'input' ELSE 'previous_result' END,j.id,f.key,json_extract(f.value,'$.path'),json_extract(f.value,'$.sha256'),
 json_extract(f.value,'$.bytes'),coalesce(nullif(json_extract(f.value,'$.relative_path'),''),json_extract(f.value,'$.path')),json_extract(j.data,'$.task_id'),
 json_extract(j.data,'$.attempt_id'),j.updated FROM core_tool_jobs j,json_each(j.data,'$.input_versions') f WHERE j.project_id=?
 UNION ALL
 SELECT 'result',c.id,f.key,f.key,json_extract(f.value,'$.sha256'),json_extract(f.value,'$.bytes'),
 'results/'||substr(c.id,8)||'/files/'||f.key,c.task_id,json_extract(c.data,'$.attempt_id'),c.updated
 FROM native_results c, json_each(c.data,'$.snapshot.manifest') f
 UNION ALL
 SELECT 'reply',c.id,'answer','回复.txt','',length(CAST(json_extract(c.data,'$.snapshot.answer') AS BLOB)),
 '回复.txt',c.task_id,json_extract(c.data,'$.attempt_id'),c.updated FROM native_results c
 WHERE typeof(json_extract(c.data,'$.snapshot.answer'))='text' AND length(json_extract(c.data,'$.snapshot.answer'))>0
 UNION ALL
 SELECT 'job',j.id,f.key,json_extract(f.value,'$.relative_path'),json_extract(f.value,'$.sha256'),
 json_extract(f.value,'$.bytes'),json_extract(f.value,'$.relative_path'),
 json_extract(j.data,'$.task_id'),json_extract(j.data,'$.attempt_id'),j.updated
 FROM core_tool_jobs j,json_each(j.data,'$.artifacts') f WHERE j.project_id=? AND j.state='COMPLETED'
) SELECT kind,source,CAST(entry AS TEXT) AS entry,name,sha256,bytes,relative_path,task_id,attempt_id,at FROM versions`

type projectVersion struct {
	Kind   string `json:"kind"`
	Source string `json:"source"`
	Entry  string `json:"entry"`
	SHA256 string `json:"sha256"`
}

func materialRef(row map[string]any) projectVersion {
	return projectVersion{str(row["kind"]), str(row["source"]), str(row["entry"]), str(row["sha256"])}
}
func (a *App) projectMaterials(w http.ResponseWriter, r *http.Request, path string) (any, error) {
	parts := strings.Split(strings.TrimPrefix(path, "/api/v2/projects/"), "/")
	if len(parts) != 2 || !uuidPattern.MatchString(parts[0]) {
		return nil, errors.New("项目身份无效")
	}
	project := parts[0]
	p, err := a.query("SELECT root FROM projects WHERE id=?", project)
	if err != nil {
		return nil, err
	}
	if len(p) != 1 {
		return nil, errors.New("项目不存在")
	}
	params := []any{project, project, project, project}
	q := r.URL.Query()
	switch parts[1] {
	case "materials":
		offset := 0
		if q.Has("cursor") {
			offset, err = strconv.Atoi(q.Get("cursor"))
			if err != nil || offset < 0 {
				return nil, errors.New("清单游标无效")
			}
		}
		limit := bounded(q.Get("limit"), 30, 100)
		rows, err := a.query(projectMaterialRows+" ORDER BY at DESC,kind,source,entry LIMIT ? OFFSET ?", append(params, limit+1, offset)...)
		if err != nil {
			return nil, err
		}
		more := len(rows) > limit
		if more {
			rows = rows[:limit]
		}
		for _, row := range rows {
			if row["kind"] == "reply" {
				content, err := a.projectReply(project, str(row["source"]), number(row["bytes"]))
				if err != nil {
					return nil, err
				}
				row["sha256"] = fmt.Sprintf("%x", sha256.Sum256(content))
			}
			row["version"] = materialRef(row)
			if row["kind"] == "job" {
				row["name"] = filepath.Base(str(row["name"]))
			}
			delete(row, "relative_path")
		}
		return map[string]any{"project_id": project, "materials": rows, "has_more": more, "cursor": strconv.Itoa(offset + len(rows))}, nil
	case "export":
		return nil, a.exportProjectMaterials(w, q["version"], project, str(p[0]["root"]))
	}
	return nil, errors.New("项目资料入口无效")
}

func (a *App) exportProjectMaterials(w http.ResponseWriter, refs []string, project, projectRoot string) error {
	if len(refs) == 0 || len(refs) > 32 {
		return errors.New("每次请选择 1 至 32 个明确版本")
	}
	versions := make([]projectVersion, 0, len(refs))
	for _, raw := range refs {
		var v projectVersion
		if json.Unmarshal([]byte(raw), &v) != nil {
			return errors.New("导出版本无效")
		}
		versions = append(versions, v)
	}
	var buffer bytes.Buffer
	if _, err := a.projectPackage(project, projectRoot, versions, &buffer); err != nil {
		return err
	}
	w.Header().Set("Content-Disposition", `attachment; filename="workdesk-project-files.zip"`)
	w.Header().Set("Content-Type", "application/zip")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	_, err := w.Write(buffer.Bytes())
	return err
}

// Resolve an exact version under its recorded project, read its bytes once and
// verify the recorded digest. Callers never provide filesystem locations.
func (a *App) projectVersionBytes(project, projectRoot string, v projectVersion, remaining int64) (map[string]any, []byte, error) {
	if v.Kind == "reply" {
		return a.projectReplyBytes(project, v, remaining)
	}
	if len(v.SHA256) != 64 {
		return nil, nil, errors.New("版本摘要无效")
	}
	rows, err := a.query(projectMaterialRows+" WHERE kind=? AND source=? AND CAST(entry AS TEXT)=? AND sha256=?", project, project, project, project, v.Kind, v.Source, v.Entry, v.SHA256)
	if err != nil {
		return nil, nil, err
	}
	if len(rows) != 1 {
		return nil, nil, errors.New("该版本不在当前项目清单中")
	}
	row := rows[0]
	root := projectRoot
	if v.Kind != "selection" && v.Kind != "input" {
		root = a.Profile.DataRoot
	}
	rel := filepath.FromSlash(str(row["relative_path"]))
	if !filepath.IsLocal(rel) || !inside(filepath.Join(root, rel), root) {
		return nil, nil, errors.New("材料引用已失效")
	}
	if v.Kind != "selection" && v.Kind != "input" && !inside(filepath.Join(root, rel), filepath.Join(root, "results")) {
		return nil, nil, errors.New("成果位置不属于活动库")
	}
	size := number(row["bytes"])
	if size < 0 || size > remaining {
		return nil, nil, errors.New("所选版本超出大小上限")
	}
	raw, err := readProjectVersion(root, rel, size, v.SHA256)
	return row, raw, err
}

// With no writer, produce the same manifest and verify the same source bytes
// without creating an archive. This is the preview used for confirmation.
func (a *App) projectPackage(project, root string, refs []projectVersion, dest io.Writer) ([]map[string]any, error) {
	if len(refs) == 0 || len(refs) > 32 {
		return nil, errors.New("每次请选择 1 至 32 个明确版本")
	}
	var zipper *zip.Writer
	if dest != nil {
		zipper = zip.NewWriter(dest)
	}
	seen := map[projectVersion]bool{}
	manifest := make([]map[string]any, 0, len(refs))
	remaining := int64(64 << 20)
	for _, v := range refs {
		if seen[v] {
			return nil, errors.New("选择了重复版本")
		}
		seen[v] = true
		row, raw, err := a.projectVersionBytes(project, root, v, remaining)
		if err != nil {
			return nil, err
		}
		remaining -= int64(len(raw))
		name := fmt.Sprintf("files/%02d/%s", len(manifest)+1, filepath.Base(filepath.FromSlash(str(row["relative_path"]))))
		delete(row, "relative_path")
		row["archive_path"] = name
		manifest = append(manifest, row)
		if zipper != nil {
			entry, err := zipper.Create(name)
			if err != nil {
				return nil, err
			}
			if _, err = entry.Write(raw); err != nil {
				return nil, err
			}
		}
	}
	if zipper != nil {
		index, err := zipper.Create("manifest.json")
		if err != nil {
			return nil, err
		}
		if err = json.NewEncoder(index).Encode(map[string]any{"schema": 1, "project_id": project, "versions": manifest}); err != nil {
			return nil, err
		}
		if err = zipper.Close(); err != nil {
			return nil, err
		}
	}
	return manifest, nil
}

func readProjectVersion(root, rel string, size int64, want string) ([]byte, error) {
	scope, err := os.OpenRoot(root)
	if err != nil {
		return nil, err
	}
	defer scope.Close()
	file, err := scope.Open(rel)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() != size {
		return nil, errors.New("材料文件已变化，请重新选择版本")
	}
	raw, err := io.ReadAll(io.LimitReader(file, size+1))
	if err != nil {
		return nil, err
	}
	hash := sha256.Sum256(raw)
	if int64(len(raw)) != size || hex.EncodeToString(hash[:]) != want {
		return nil, errors.New("材料摘要已变化，请重新选择版本")
	}
	return raw, nil
}
