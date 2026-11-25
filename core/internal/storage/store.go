// Package storage owns SQLite connections for the application and its trusted
// legacy bridge. A bridge session is a transaction, never a second database owner.
package storage

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"modernc.org/sqlite"
)

// DBError retains the extended SQLite code across the internal bridge.
type DBError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

func (e *DBError) Error() string { return e.Message }

func dbError(err error) error {
	if err == nil {
		return nil
	}
	var se *sqlite.Error
	if errors.As(err, &se) {
		return &DBError{Code: se.Code(), Message: se.Error()}
	}
	return err
}

type Store struct {
	path           string
	db             *sql.DB
	ctx            context.Context
	cancel         context.CancelFunc
	writer         chan struct{}
	mu             sync.Mutex
	sessions       map[*Session]struct{}
	closed         bool
	allowMigration bool
	initiallyEmpty bool
}

// Result uses native int64 and []byte values. The RPC layer must preserve those
// types rather than round-tripping them through a JavaScript number or text.
type Result struct {
	Columns       []string `json:"columns"`
	Rows          [][]any  `json:"rows"`
	LastRowID     int64    `json:"lastrowid"`
	RowCount      int64    `json:"rowcount"`
	TotalChanges  int64    `json:"total_changes"`
	InTransaction bool     `json:"in_transaction"`
}

// Open validates an existing database without dispatching or reconciling work.
// The caller must first hold the instance owner's OS lock. Migration is explicit.
func Open(path string, allowMigration bool) (*Store, error) {
	abs, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	if info, statErr := os.Lstat(abs); statErr == nil {
		if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
			return nil, errors.New("active database must be a regular file, not a link")
		}
	} else if !os.IsNotExist(statErr) {
		return nil, statErr
	}
	if err = os.MkdirAll(filepath.Dir(abs), 0700); err != nil {
		return nil, err
	}
	u := &url.URL{Scheme: "file", Path: filepath.ToSlash(abs)}
	if filepath.VolumeName(abs) != "" {
		u.Path = "/" + u.Path
	}
	q := u.Query()
	q.Add("_pragma", "busy_timeout(5000)")
	q.Add("_pragma", "foreign_keys(1)")
	q.Add("_pragma", "trusted_schema(0)")
	u.RawQuery = q.Encode()
	db, err := sql.Open("sqlite", u.String())
	if err != nil {
		return nil, dbError(err)
	}
	db.SetMaxOpenConns(5)
	db.SetMaxIdleConns(5)
	ctx, cancel := context.WithCancel(context.Background())
	s := &Store{path: abs, db: db, ctx: ctx, cancel: cancel, writer: make(chan struct{}, 1), sessions: make(map[*Session]struct{}), allowMigration: allowMigration}
	s.writer <- struct{}{}
	if err = s.validate(); err != nil {
		cancel()
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) Path() string { return s.path }

func (s *Store) validate() error {
	var count int
	if err := s.db.QueryRowContext(s.ctx, "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='meta'").Scan(&count); err != nil {
		return dbError(err)
	}
	if count == 0 {
		if err := s.db.QueryRowContext(s.ctx, "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").Scan(&count); err != nil {
			return dbError(err)
		}
		if count != 0 {
			return errors.New("database contains records without supported metadata")
		}
		s.initiallyEmpty = true
		return nil
	}
	metadata, err := s.metadata()
	if err != nil {
		return err
	}
	if _, exists := metadata["retired_to"]; exists {
		return errors.New("database is retired; refusing to acquire execution ownership")
	}
	version := metadata["schema"]
	if version != "1" && version != "2" {
		return fmt.Errorf("unsupported database schema %q", version)
	}
	for _, key := range []string{"collaboration_schema", "persistence_schema", "work_objects_schema"} {
		if value, ok := metadata[key]; ok && value != "1" {
			return fmt.Errorf("unsupported %s %q", key, value)
		}
	}
	if version == "1" && !s.allowMigration {
		return errors.New("database schema 1 requires an explicit backed-up migration")
	}
	return s.InitializeCore()
}

func (s *Store) metadata() (map[string]string, error) {
	rows, err := s.db.QueryContext(s.ctx, "SELECT key,value FROM meta")
	if err != nil {
		return nil, dbError(err)
	}
	defer rows.Close()
	values := make(map[string]string)
	for rows.Next() {
		var key, value string
		if err = rows.Scan(&key, &value); err != nil {
			return nil, dbError(err)
		}
		values[key] = value
	}
	return values, dbError(rows.Err())
}

// InitializeCore finishes first-use initialization after the legacy schema has
// been created through a Go-owned session. It never changes old object versions.
func (s *Store) InitializeCore() error {
	tx, err := s.OpenSession(true)
	if err != nil {
		return err
	}
	defer tx.Close()
	if s.initiallyEmpty {
		if _, err = tx.Execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)", nil); err != nil {
			return err
		}
		if _, err = tx.Execute("INSERT OR IGNORE INTO meta VALUES('schema','2')", nil); err != nil {
			return err
		}
	}
	r, err := tx.Execute("SELECT value FROM meta WHERE key='schema'", nil)
	if err != nil {
		return err
	}
	version := ""
	if len(r.Rows) > 0 {
		version, _ = r.Rows[0][0].(string)
	}
	if version != "1" && version != "2" {
		return fmt.Errorf("unsupported database schema %q", version)
	}
	if version == "1" && !s.allowMigration && !s.initiallyEmpty {
		return errors.New("explicit migration required")
	}
	if _, err = tx.Execute("CREATE TABLE IF NOT EXISTS core_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)", nil); err != nil {
		return err
	}
	if _, err = tx.Execute("INSERT OR IGNORE INTO core_meta VALUES('owner_epoch','0')", nil); err != nil {
		return err
	}
	if _, err = tx.Execute("UPDATE meta SET value='2' WHERE key='schema'", nil); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	s.initiallyEmpty = false
	return nil
}

func (s *Store) Schema() (string, error) {
	var value string
	err := s.db.QueryRowContext(s.ctx, "SELECT value FROM meta WHERE key='schema'").Scan(&value)
	return value, dbError(err)
}

func (s *Store) OwnerEpoch() (int64, error) {
	var value int64
	err := s.db.QueryRowContext(s.ctx, "SELECT CAST(value AS INTEGER) FROM core_meta WHERE key='owner_epoch'").Scan(&value)
	return value, dbError(err)
}

func (s *Store) AdvanceOwnerEpoch() (int64, error) {
	tx, err := s.OpenSession(true)
	if err != nil {
		return 0, err
	}
	defer tx.Close()
	if _, err = tx.Execute("UPDATE core_meta SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT) WHERE key='owner_epoch'", nil); err != nil {
		return 0, err
	}
	r, err := tx.Execute("SELECT CAST(value AS INTEGER) FROM core_meta WHERE key='owner_epoch'", nil)
	if err != nil {
		return 0, err
	}
	if len(r.Rows) != 1 {
		return 0, errors.New("owner epoch is missing")
	}
	epoch, ok := r.Rows[0][0].(int64)
	if !ok {
		return 0, errors.New("invalid owner epoch")
	}
	return epoch, tx.Commit()
}

func (s *Store) OpenSession(write bool) (*Session, error) {
	if write {
		select {
		case <-s.writer:
		case <-s.ctx.Done():
			return nil, errors.New("store is closed")
		}
	}
	releaseWriter := func() {
		if write {
			s.writer <- struct{}{}
		}
	}
	conn, err := s.db.Conn(s.ctx)
	if err != nil {
		releaseWriter()
		return nil, dbError(err)
	}
	queryOnly, begin := "ON", "BEGIN"
	if write {
		queryOnly, begin = "OFF", "BEGIN IMMEDIATE"
	}
	if _, err = conn.ExecContext(s.ctx, "PRAGMA query_only="+queryOnly); err != nil {
		conn.Close()
		releaseWriter()
		return nil, dbError(err)
	}
	if _, err = conn.ExecContext(s.ctx, begin); err != nil {
		conn.Close()
		releaseWriter()
		return nil, dbError(err)
	}
	var baseline int64
	if err = conn.QueryRowContext(s.ctx, "SELECT total_changes()").Scan(&baseline); err != nil {
		conn.ExecContext(context.Background(), "ROLLBACK")
		conn.Close()
		releaseWriter()
		return nil, dbError(err)
	}
	tx := &Session{store: s, conn: conn, write: write, transaction: true, baseline: baseline}
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		tx.Close()
		return nil, errors.New("store is closed")
	}
	s.sessions[tx] = struct{}{}
	s.mu.Unlock()
	return tx, nil
}

func (s *Store) Close() error {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return nil
	}
	s.closed = true
	s.cancel()
	active := make([]*Session, 0, len(s.sessions))
	for tx := range s.sessions {
		active = append(active, tx)
	}
	s.mu.Unlock()
	for _, tx := range active {
		tx.Close()
	}
	return s.db.Close()
}

type Session struct {
	store       *Store
	conn        *sql.Conn
	write       bool
	mu          sync.Mutex
	transaction bool
	baseline    int64
	closed      bool
}

func (tx *Session) InTransaction() bool {
	tx.mu.Lock()
	defer tx.mu.Unlock()
	return tx.transaction && !tx.closed
}

func normalizeArgs(args []any) ([]any, error) {
	values := make([]any, len(args))
	for i, value := range args {
		if number, ok := value.(json.Number); ok {
			if integer, err := number.Int64(); err == nil {
				values[i] = integer
			} else {
				if !strings.ContainsAny(string(number), ".eE") {
					return nil, errors.New("integer is outside SQLite's signed 64-bit range")
				}
				decimal, err := number.Float64()
				if err != nil {
					return nil, err
				}
				values[i] = decimal
			}
		} else {
			values[i] = value
		}
	}
	return values, nil
}

func statementVerb(query string) string {
	text := strings.TrimSpace(query)
	for strings.HasPrefix(text, "--") || strings.HasPrefix(text, "/*") {
		if strings.HasPrefix(text, "--") {
			n := strings.IndexByte(text, '\n')
			if n < 0 {
				return ""
			}
			text = strings.TrimSpace(text[n+1:])
		} else {
			n := strings.Index(text, "*/")
			if n < 0 {
				return ""
			}
			text = strings.TrimSpace(text[n+2:])
		}
	}
	fields := strings.Fields(text)
	if len(fields) == 0 {
		return ""
	}
	return strings.ToUpper(fields[0])
}

func (tx *Session) Execute(query string, args []any) (Result, error) {
	tx.mu.Lock()
	defer tx.mu.Unlock()
	result := Result{Columns: []string{}, Rows: [][]any{}, RowCount: -1, InTransaction: tx.transaction}
	if tx.closed {
		return result, errors.New("database session is closed")
	}
	values, err := normalizeArgs(args)
	if err != nil {
		return result, err
	}
	rows, err := tx.conn.QueryContext(tx.store.ctx, query, values...)
	if err != nil {
		return result, dbError(err)
	}
	result.Columns, err = rows.Columns()
	if err != nil {
		rows.Close()
		return result, dbError(err)
	}
	for rows.Next() {
		row := make([]any, len(result.Columns))
		pointers := make([]any, len(row))
		for i := range row {
			pointers[i] = &row[i]
		}
		if err = rows.Scan(pointers...); err != nil {
			rows.Close()
			return result, dbError(err)
		}
		for i, value := range row {
			if raw, ok := value.([]byte); ok {
				row[i] = append([]byte(nil), raw...)
			}
		}
		result.Rows = append(result.Rows, row)
	}
	err = rows.Err()
	closeErr := rows.Close()
	if err != nil {
		return result, dbError(err)
	}
	if closeErr != nil {
		return result, dbError(closeErr)
	}
	verb := statementVerb(query)
	switch verb {
	case "COMMIT", "END":
		tx.transaction = false
	case "ROLLBACK":
		if !strings.Contains(strings.ToUpper(query), " TO ") {
			tx.transaction = false
		}
	case "BEGIN", "SAVEPOINT":
		tx.transaction = true
	}
	var changes, total int64
	err = tx.conn.QueryRowContext(tx.store.ctx, "SELECT last_insert_rowid(),changes(),total_changes()").Scan(&result.LastRowID, &changes, &total)
	if err != nil {
		return result, dbError(err)
	}
	if verb == "INSERT" || verb == "UPDATE" || verb == "DELETE" || verb == "REPLACE" {
		result.RowCount = changes
	}
	result.TotalChanges = total - tx.baseline
	result.InTransaction = tx.transaction
	return result, nil
}

func (tx *Session) finish(commit bool) error {
	tx.mu.Lock()
	if tx.closed {
		tx.mu.Unlock()
		return nil
	}
	var err error
	if tx.transaction {
		query := "ROLLBACK"
		if commit {
			query = "COMMIT"
		}
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		_, err = tx.conn.ExecContext(ctx, query)
		if err != nil && commit {
			tx.conn.ExecContext(ctx, "ROLLBACK")
		}
		cancel()
	}
	tx.transaction = false
	tx.closed = true
	closeErr := tx.conn.Close()
	tx.mu.Unlock()
	tx.store.mu.Lock()
	delete(tx.store.sessions, tx)
	tx.store.mu.Unlock()
	if tx.write {
		tx.store.writer <- struct{}{}
	}
	if err != nil {
		return dbError(err)
	}
	return dbError(closeErr)
}

func (tx *Session) Commit() error   { return tx.finish(true) }
func (tx *Session) Rollback() error { return tx.finish(false) }
func (tx *Session) Close() error    { return tx.Rollback() }

// Backup copies a committed snapshot using SQLite's online backup API, with a
// separate connection so an open command-registration transaction is not joined.
func (s *Store) Backup(target string) error {
	abs, err := filepath.Abs(target)
	if err != nil {
		return err
	}
	if strings.EqualFold(abs, s.path) {
		return errors.New("backup target is the active database")
	}
	if _, err = os.Lstat(abs); err == nil {
		return errors.New("backup target already exists")
	} else if !os.IsNotExist(err) {
		return err
	}
	if err = os.MkdirAll(filepath.Dir(abs), 0700); err != nil {
		return err
	}
	conn, err := s.db.Conn(s.ctx)
	if err != nil {
		return dbError(err)
	}
	defer conn.Close()
	return dbError(conn.Raw(func(raw any) error {
		backuper, ok := raw.(interface {
			NewBackup(string) (*sqlite.Backup, error)
		})
		if !ok {
			return errors.New("SQLite driver does not expose its backup API")
		}
		backup, err := backuper.NewBackup(abs)
		if err != nil {
			return err
		}
		defer backup.Finish()
		for {
			more, err := backup.Step(256)
			if err != nil {
				return err
			}
			if !more {
				return nil
			}
			select {
			case <-s.ctx.Done():
				return s.ctx.Err()
			default:
			}
		}
	}))
}
