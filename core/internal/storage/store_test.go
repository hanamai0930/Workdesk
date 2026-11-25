package storage

import (
	"bytes"
	"encoding/json"
	"errors"
	"path/filepath"
	"testing"
)

func initializedStore(t *testing.T) *Store {
	t.Helper()
	s, err := Open(filepath.Join(t.TempDir(), "tasks.sqlite3"), false)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	tx, err := s.OpenSession(true)
	if err != nil {
		t.Fatal(err)
	}
	for _, query := range []string{"CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)", "INSERT INTO meta VALUES('schema','1')", "CREATE TABLE records(id INTEGER PRIMARY KEY,title TEXT,body BLOB)"} {
		if _, err = tx.Execute(query, nil); err != nil {
			tx.Close()
			t.Fatal(err)
		}
	}
	if err = tx.Commit(); err != nil {
		t.Fatal(err)
	}
	if err = s.InitializeCore(); err != nil {
		t.Fatal(err)
	}
	return s
}

func TestSessionsPreserveValuesAndTransactions(t *testing.T) {
	s := initializedStore(t)
	tx, err := s.OpenSession(true)
	if err != nil {
		t.Fatal(err)
	}
	row, err := tx.Execute("INSERT INTO records(title,body) VALUES(?,?)", []any{"中文标题", []byte{0, 1, 255}})
	if err != nil {
		t.Fatal(err)
	}
	if row.LastRowID != 1 || row.RowCount != 1 || row.TotalChanges != 1 || !row.InTransaction {
		t.Fatalf("unexpected insert result: %+v", row)
	}
	const exactID int64 = 9007199254740993
	if _, err = tx.Execute("INSERT INTO records(id,title) VALUES(?,?)", []any{exactID, "exact integer"}); err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(); err != nil {
		t.Fatal(err)
	}
	read, err := s.OpenSession(false)
	if err != nil {
		t.Fatal(err)
	}
	defer read.Close()
	row, err = read.Execute("SELECT id,title,body FROM records ORDER BY id", nil)
	if err != nil {
		t.Fatal(err)
	}
	if row.Rows[1][0].(int64) != exactID || row.Rows[0][1] != "中文标题" || !bytes.Equal(row.Rows[0][2].([]byte), []byte{0, 1, 255}) {
		t.Fatalf("value loss: %+v", row)
	}
	if row.RowCount != -1 || row.TotalChanges != 0 {
		t.Fatalf("read metadata changed: %+v", row)
	}
	if _, err = read.Execute("SELECT ?", []any{json.Number("9223372036854775808")}); err == nil {
		t.Fatal("out-of-range integer was silently rounded")
	}
	_, err = read.Execute("DELETE FROM records", nil)
	var sqliteErr *DBError
	if !errors.As(err, &sqliteErr) || sqliteErr.Code&255 != 8 {
		t.Fatalf("read-only session accepted mutation: %v", err)
	}
}

func TestFreshCoreInitializesBeforeLegacyBusinessTables(t *testing.T) {
	s, err := Open(filepath.Join(t.TempDir(), "tasks.sqlite3"), false)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err = s.InitializeCore(); err != nil {
		t.Fatal(err)
	}
	version, err := s.Schema()
	if err != nil || version != "2" {
		t.Fatalf("new core schema %q: %v", version, err)
	}
	if epoch, err := s.AdvanceOwnerEpoch(); err != nil || epoch != 1 {
		t.Fatalf("new owner epoch %d: %v", epoch, err)
	}
}

func TestRollbackAndBackupCommittedSnapshot(t *testing.T) {
	s := initializedStore(t)
	tx, err := s.OpenSession(true)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = tx.Execute("INSERT INTO records(title) VALUES('committed')", nil); err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(); err != nil {
		t.Fatal(err)
	}
	tx, err = s.OpenSession(true)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = tx.Execute("INSERT INTO records(title) VALUES('not committed')", nil); err != nil {
		t.Fatal(err)
	}
	backup := filepath.Join(t.TempDir(), "snapshot.sqlite3")
	if err = s.Backup(backup); err != nil {
		t.Fatal(err)
	}
	if err = tx.Rollback(); err != nil {
		t.Fatal(err)
	}
	restored, err := Open(backup, false)
	if err != nil {
		t.Fatal(err)
	}
	defer restored.Close()
	read, err := restored.OpenSession(false)
	if err != nil {
		t.Fatal(err)
	}
	defer read.Close()
	row, err := read.Execute("SELECT title FROM records", nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(row.Rows) != 1 || row.Rows[0][0] != "committed" {
		t.Fatalf("backup captured uncommitted changes: %+v", row)
	}
}

func TestExplicitMigrationRetirementAndEpoch(t *testing.T) {
	s := initializedStore(t)
	path := s.Path()
	first, err := s.AdvanceOwnerEpoch()
	if err != nil || first != 1 {
		t.Fatalf("epoch %d: %v", first, err)
	}
	tx, _ := s.OpenSession(true)
	if _, err = tx.Execute("UPDATE meta SET value='1' WHERE key='schema'", nil); err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(); err != nil {
		t.Fatal(err)
	}
	s.Close()
	if denied, err := Open(path, false); err == nil {
		denied.Close()
		t.Fatal("schema 1 opened without explicit migration")
	}
	s, err = Open(path, true)
	if err != nil {
		t.Fatal(err)
	}
	second, err := s.AdvanceOwnerEpoch()
	if err != nil || second != 2 {
		t.Fatalf("durable epoch %d: %v", second, err)
	}
	tx, _ = s.OpenSession(true)
	if _, err = tx.Execute("INSERT INTO meta VALUES('retired_to','another-library')", nil); err != nil {
		t.Fatal(err)
	}
	tx.Commit()
	s.Close()
	if denied, err := Open(path, true); err == nil {
		denied.Close()
		t.Fatal("retired database acquired an owner")
	}
}

func TestConstraintCodeAndWholeTransactionRollback(t *testing.T) {
	s := initializedStore(t)
	tx, _ := s.OpenSession(true)
	if _, err := tx.Execute("INSERT INTO records(id,title) VALUES(1,'first')", nil); err != nil {
		t.Fatal(err)
	}
	_, err := tx.Execute("INSERT INTO records(id,title) VALUES(1,'duplicate')", nil)
	var sqliteErr *DBError
	if !errors.As(err, &sqliteErr) || sqliteErr.Code&255 != 19 {
		t.Fatalf("constraint code was lost: %v", err)
	}
	tx.Rollback()
	read, _ := s.OpenSession(false)
	defer read.Close()
	row, err := read.Execute("SELECT COUNT(*) FROM records", nil)
	if err != nil || row.Rows[0][0].(int64) != 0 {
		t.Fatalf("partial transaction persisted: %+v %v", row, err)
	}
}
