package storage

import (
	"path/filepath"
	"testing"
)

func TestPartnersMigrationIsExplicitAndPreservesRecords(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tasks.sqlite3")
	s, e := Open(path, false)
	if e != nil {
		t.Fatal(e)
	}
	if e = s.InitializeCore(); e != nil {
		t.Fatal(e)
	}
	tx, e := s.OpenSession(true)
	if e != nil {
		t.Fatal(e)
	}
	for _, q := range []string{`CREATE TABLE old_records(id TEXT PRIMARY KEY,body BLOB)`, `INSERT INTO old_records VALUES('original',X'00FF01')`} {
		if _, e = tx.Execute(q, nil); e != nil {
			t.Fatal(e)
		}
	}
	if e = tx.Commit(); e != nil {
		t.Fatal(e)
	}
	if version, _ := s.Schema(); version != "2" {
		t.Fatalf("normal startup migrated %q", version)
	}
	if e = s.MigratePartners(); e != nil {
		t.Fatal(e)
	}
	if e = s.Close(); e != nil {
		t.Fatal(e)
	}
	s, e = Open(path, false)
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	if version, _ := s.Schema(); version != "3" {
		t.Fatalf("startup rewrote migrated schema: %q", version)
	}
	read, e := s.OpenSession(false)
	if e != nil {
		t.Fatal(e)
	}
	defer read.Rollback()
	r, e := read.Execute(`SELECT hex(body) FROM old_records WHERE id='original'`, nil)
	if e != nil || len(r.Rows) != 1 || r.Rows[0][0] != "00FF01" {
		t.Fatalf("old records changed: %+v %v", r, e)
	}
}
