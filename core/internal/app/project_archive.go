package app

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"workdesk/core/internal/storage"
)

type materialArchive struct {
	ID           string           `json:"archive_id"`
	Project      string           `json:"project_id"`
	SourceRoot   string           `json:"source_root"`
	Versions     []projectVersion `json:"versions"`
	Manifest     []map[string]any `json:"manifest"`
	ScopeHash    string           `json:"scope_sha256"`
	Retention    string           `json:"retention"`
	State        string           `json:"state"`
	Revision     int              `json:"revision"`
	PackageSHA   string           `json:"package_sha256,omitempty"`
	PackageBytes int64            `json:"package_bytes,omitempty"`
	Updated      float64          `json:"updated"`
}

// Scope and package records live alongside the project's existing collaboration
// records. Only the explicitly selected material versions enter the package.
func archiveRow(s *storage.Session, project, id string) (materialArchive, error) {
	var archive materialArchive
	if !uuidPattern.MatchString(id) || !uuidPattern.MatchString(project) {
		return archive, errors.New("项目或归档身份无效")
	}
	rows, err := s.Execute("SELECT data FROM collaboration WHERE kind='project_archive' AND id=? AND json_extract(data,'$.project_id')=?", []any{id, project})
	if err != nil {
		return archive, err
	}
	if len(rows.Rows) != 1 {
		return archive, errors.New("本项目没有该归档范围")
	}
	err = json.Unmarshal([]byte(str(rows.Rows[0][0])), &archive)
	return archive, err
}

func archiveScopeDigest(project, root string, manifest []map[string]any) string {
	data, _ := json.Marshal([]any{project, root, manifest})
	return fmt.Sprintf("%x", sha256.Sum256(data))
}

func archivePackagePath(digest string) string {
	return filepath.Join("results", "project-archives", digest+".zip")
}

func (a *App) storedArchive(archive materialArchive) ([]byte, error) {
	if len(archive.PackageSHA) != 64 || archive.PackageBytes <= 0 || archive.PackageBytes > 66<<20 {
		return nil, errors.New("此范围尚无已封存的导出包")
	}
	rel := archivePackagePath(archive.PackageSHA)
	if !inside(filepath.Join(a.Profile.DataRoot, rel), filepath.Join(a.Profile.DataRoot, "results", "project-archives")) {
		return nil, errors.New("归档包位置无效")
	}
	return readProjectVersion(a.Profile.DataRoot, rel, archive.PackageBytes, archive.PackageSHA)
}

// Publish a new package atomically under its digest. A failed DB transaction
// can be retried with the original command without duplicating an export.
func (a *App) sealArchive(archive *materialArchive) error {
	var buffer bytes.Buffer
	manifest, err := a.projectPackage(archive.Project, archive.SourceRoot, archive.Versions, &buffer)
	if err != nil {
		return err
	}
	if archiveScopeDigest(archive.Project, archive.SourceRoot, manifest) != archive.ScopeHash {
		return errors.New("选择范围自预览后已变化，请重新预览")
	}
	digest := fmt.Sprintf("%x", sha256.Sum256(buffer.Bytes()))
	if archive.PackageSHA != "" && archive.PackageSHA != digest {
		return errors.New("导出版本已变化，请重新预览")
	}
	archive.PackageSHA = digest
	archive.PackageBytes = int64(buffer.Len())
	rel := archivePackagePath(digest)
	target := filepath.Join(a.Profile.DataRoot, rel)
	dir := filepath.Dir(target)
	if !inside(target, a.Profile.DataRoot) {
		return errors.New("归档包位置越界")
	}
	if err = os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	if _, err = os.Lstat(target); err == nil {
		_, err = a.storedArchive(*archive)
		return err
	} else if !os.IsNotExist(err) {
		return err
	}
	temp, err := os.CreateTemp(dir, ".package-")
	if err != nil {
		return err
	}
	defer os.Remove(temp.Name())
	_, err = temp.Write(buffer.Bytes())
	if err == nil {
		err = temp.Sync()
	}
	if closed := temp.Close(); err == nil {
		err = closed
	}
	if err != nil {
		return err
	}
	if err = os.Link(temp.Name(), target); err != nil && !os.IsExist(err) {
		return err
	}
	_, err = a.storedArchive(*archive)
	return err
}

func (a *App) archiveMutation(s *storage.Session, command, kind string, p map[string]any) (map[string]any, error) {
	project := str(p["project_id"])
	if !uuidPattern.MatchString(project) {
		return nil, errors.New("请选择准确项目")
	}
	root, err := projectRoot(s, project)
	if err != nil {
		return nil, err
	}
	var archive materialArchive
	if kind == "project.archive.preview" {
		if len(p) != 2 {
			return nil, errors.New("预览只接受项目与所选版本")
		}
		raw, err := json.Marshal(p["versions"])
		if err != nil {
			return nil, err
		}
		if err = json.Unmarshal(raw, &archive.Versions); err != nil {
			return nil, errors.New("材料版本格式无效")
		}
		archive.Manifest, err = a.projectPackage(project, root, archive.Versions, nil)
		if err != nil {
			return nil, err
		}
		archive.ID = command
		archive.Project = project
		archive.SourceRoot = root
		archive.ScopeHash = archiveScopeDigest(project, root, archive.Manifest)
		archive.Retention = "KEEP_ORIGINALS_IN_PLACE"
		archive.State = "PREVIEW"
	} else {
		archive, err = archiveRow(s, project, str(p["archive_id"]))
		if err != nil {
			return nil, err
		}
		if len(p) != 4 || p["confirm"] != true || number(p["revision"]) != int64(archive.Revision) {
			return nil, errors.New("请确认原范围与当前修订")
		}
		switch kind {
		case "project.archive.export", "project.archive.confirm":
			if archive.State != "PREVIEW" && !(kind == "project.archive.confirm" && archive.State == "EXPORTED") {
				return nil, errors.New("该修订已处理，请读取原回执")
			}
			if archive.SourceRoot != root {
				return nil, errors.New("项目位置已变化，请重新预览")
			}
			if err = a.sealArchive(&archive); err != nil {
				return nil, err
			}
			if kind == "project.archive.export" {
				archive.State = "EXPORTED"
			} else {
				archive.State = "ARCHIVED"
			}
		case "project.archive.reopen":
			if archive.State != "ARCHIVED" {
				return nil, errors.New("请选择已归档范围")
			}
			if _, err = a.storedArchive(archive); err != nil {
				return nil, err
			}
			archive.State = "REOPENED"
		default:
			return nil, errors.New("未知归档操作")
		}
	}
	archive.Revision++
	archive.Updated = now()
	data, err := json.Marshal(archive)
	if err != nil {
		return nil, err
	}
	if kind == "project.archive.preview" {
		_, err = s.Execute("INSERT INTO collaboration(kind,id,data,updated) VALUES('project_archive',?,?,?)", []any{archive.ID, string(data), archive.Updated})
	} else {
		_, err = s.Execute("UPDATE collaboration SET data=?,updated=? WHERE kind='project_archive' AND id=?", []any{string(data), archive.Updated, archive.ID})
	}
	if err != nil {
		return nil, err
	}
	var receipt map[string]any
	err = json.Unmarshal(data, &receipt)
	return receipt, err
}

func (a *App) archiveRead(w http.ResponseWriter, r *http.Request, path string) (any, error) {
	parts := strings.Split(strings.TrimPrefix(path, "/api/v2/projects/"), "/")
	if len(parts) != 2 || !uuidPattern.MatchString(parts[0]) {
		return nil, errors.New("项目身份无效")
	}
	project := parts[0]
	s, err := a.DB.OpenSession(false)
	if err != nil {
		return nil, err
	}
	defer s.Rollback()
	if _, err = projectRoot(s, project); err != nil {
		return nil, err
	}
	if parts[1] == "archives" {
		q := r.URL.Query()
		offset := 0
		if q.Has("cursor") {
			offset, err = strconv.Atoi(q.Get("cursor"))
			if err != nil || offset < 0 {
				return nil, errors.New("归档游标无效")
			}
		}
		limit := bounded(q.Get("limit"), 20, 100)
		rows, err := s.Execute("SELECT data FROM collaboration WHERE kind='project_archive' AND json_extract(data,'$.project_id')=? ORDER BY updated DESC,id LIMIT ? OFFSET ?", []any{project, limit + 1, offset})
		if err != nil {
			return nil, err
		}
		result := make([]materialArchive, 0, len(rows.Rows))
		for _, row := range rows.Rows[:min(limit, len(rows.Rows))] {
			var archive materialArchive
			if err = json.Unmarshal([]byte(str(row[0])), &archive); err != nil {
				return nil, err
			}
			result = append(result, archive)
		}
		return map[string]any{"archives": result, "has_more": len(rows.Rows) > limit, "cursor": strconv.Itoa(offset + len(result))}, nil
	}
	archive, err := archiveRow(s, project, r.URL.Query().Get("archive_id"))
	if err != nil {
		return nil, err
	}
	data, err := a.storedArchive(archive)
	if err != nil {
		return nil, err
	}
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="workdesk-%s.zip"`, archive.ID))
	w.Header().Set("Content-Type", "application/zip")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	_, err = w.Write(data)
	return nil, err
}
