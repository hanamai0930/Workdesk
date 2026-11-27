package app

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"testing"

	"workdesk/core/internal/storage"
)

func TestProjectVersionsExportOnlySelectedBytes(t *testing.T) {
	a := toolFixture(t)
	project, other := ID(), ID()
	root := a.Profile.ProgramRoot
	content := []byte("sample")
	sum := sha256.Sum256(content)
	selected := map[string]any{"project_id": project, "selected": []any{map[string]any{"file": "notes.txt", "bytes": len(content), "sha256": hex.EncodeToString(sum[:])}}}
	data, _ := json.Marshal(selected)
	file := filepath.Join(root, "notes.txt")
	if err := os.WriteFile(file, content, 0600); err != nil {
		t.Fatal(err)
	}
	err := a.write(func(s *storage.Session) error {
		for _, sql := range []string{
			"CREATE TABLE IF NOT EXISTS collaboration(kind TEXT,id TEXT,data TEXT,updated REAL)",
			"CREATE TABLE IF NOT EXISTS tasks(id TEXT,project_id TEXT)"} {
			if _, e := s.Execute(sql, nil); e != nil {
				return e
			}
		}
		if _, e := s.Execute("INSERT INTO projects(id,root) VALUES(?,?),(?,?)", []any{project, root, other, root}); e != nil {
			return e
		}
		_, e := s.Execute("INSERT INTO collaboration(kind,id,data,updated) VALUES('file_selection','selection',?,1)", []any{string(data)})
		return e
	})
	if err != nil {
		t.Fatal(err)
	}
	path := "/api/v2/projects/" + project + "/materials"
	list, err := a.projectMaterials(httptest.NewRecorder(), httptest.NewRequest("GET", path, nil), path)
	if err != nil {
		t.Fatal(err)
	}
	rows := list.(map[string]any)["materials"].([]map[string]any)
	if len(rows) != 1 {
		t.Fatal(rows)
	}
	ref, _ := json.Marshal(rows[0]["version"])
	export := func(id string) (*httptest.ResponseRecorder, error) {
		route := "/api/v2/projects/" + id + "/export"
		w := httptest.NewRecorder()
		_, e := a.projectMaterials(w, httptest.NewRequest("GET", route+"?version="+url.QueryEscape(string(ref)), nil), route)
		return w, e
	}
	w, err := export(project)
	if err != nil {
		t.Fatal(err)
	}
	z, err := zip.NewReader(bytes.NewReader(w.Body.Bytes()), int64(w.Body.Len()))
	if err != nil {
		t.Fatal(err)
	}
	if len(z.File) != 2 || z.File[1].Name != "manifest.json" {
		t.Fatal("export lacks file or index")
	}
	stream, err := z.File[0].Open()
	if err != nil {
		t.Fatal(err)
	}
	got, err := io.ReadAll(stream)
	stream.Close()
	if err != nil || !bytes.Equal(got, content) {
		t.Fatal("wrong exported version")
	}
	if _, err = export(other); err == nil {
		t.Fatal("accepted another project's version")
	}
	if err = os.WriteFile(file, []byte("CHANGE"), 0600); err != nil {
		t.Fatal(err)
	}
	if w, err = export(project); err == nil || w.Body.Len() != 0 {
		t.Fatal("returned a changed or partial archive")
	}
}
