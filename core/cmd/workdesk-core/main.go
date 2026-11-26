package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"time"
	"workdesk/core/internal/app"
	"workdesk/core/internal/instance"
	"workdesk/core/internal/storage"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func run() error {
	config := flag.String("config", "", "Registered development instance configuration")
	base := flag.String("base", "", "Expected program root")
	implementation := flag.String("implementation", "", "Immutable implementation manifest digest")
	source := flag.String("source", "", "Source revision")
	dirty := flag.Bool("source-dirty", false, "Source tree had uncommitted changes at launch")
	migrate := flag.Bool("migrate-only", false, "Explicit offline v1 to v2 migration")
	partnersMigration := flag.Bool("migrate-partners-only", false, "Explicit backed-up offline storage v2 to v3 migration")
	fixture := flag.Bool("fixture", false, "Isolated test profile, no local Git binding")
	flag.Parse()
	if *config == "" || *base == "" {
		return errors.New("必须提供登记的开发实例配置与程序根")
	}
	p, err := instance.Load(*config, *base)
	if err != nil {
		return err
	}
	if !*fixture {
		if err = p.ValidateLocalMarks(); err != nil {
			return err
		}
	} else if !instance.Within(p.UserRoot, filepath.Join(p.DevelopmentRoot, "artifacts")) || !instance.Within(p.ConfigPath, filepath.Join(p.DevelopmentRoot, "artifacts")) {
		return errors.New("fixture配置与用户根必须位于独立检查材料目录")
	}
	if _, e := os.Stat(filepath.Join(p.UserRoot, "runtime", "development-stop.json")); e == nil && !*migrate && !*partnersMigration {
		return errors.New("实例已请求停止，请使用显式开发启动入口")
	}
	lock, err := instance.AcquireOwner(p.DataRoot)
	if err != nil {
		return err
	}
	defer lock.Close()
	db, err := storage.Open(filepath.Join(p.DataRoot, "tasks.sqlite3"), *migrate)
	if err != nil {
		return err
	}
	defer db.Close()
	if *partnersMigration {
		if err = db.MigratePartners(); err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(map[string]any{"state": "PARTNERS_MIGRATED", "storage_schema": 3, "data_root": p.DataRoot})
	}
	if *migrate {
		schema, e := db.Schema()
		if e != nil {
			return e
		}
		return json.NewEncoder(os.Stdout).Encode(map[string]any{"state": "MIGRATED", "storage_schema": schema, "data_root": p.DataRoot})
	}
	if *implementation == "" {
		return errors.New("缺少准确实现清单，未启动核心")
	}
	logRoot := filepath.Join(p.UserRoot, "runtime")
	if err = os.MkdirAll(logRoot, 0700); err != nil {
		return err
	}
	log, err := os.OpenFile(filepath.Join(logRoot, "core-diagnostic.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer log.Close()
	a, err := app.New(p, db, *implementation, *source, log)
	if err != nil {
		return err
	}
	defer a.Close()
	a.SourceDirty = *dirty
	control, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return err
	}
	defer control.Close()
	ui, err := net.Listen("tcp4", "127.0.0.1:"+strconv.Itoa(p.UIPort))
	if err != nil {
		return err
	}
	defer ui.Close()
	port := control.Addr().(*net.TCPAddr).Port
	a.UIOrigin = "http://127.0.0.1:" + strconv.Itoa(p.UIPort)
	controlServer := &http.Server{Handler: a.BindPort(strconv.Itoa(port)), ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second}
	uiServer := &http.Server{Handler: http.HandlerFunc(a.UIHandler), ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second}
	state := map[string]any{"app": "workdesk-control-v1", "protocol": 2, "instance": a.Process, "port": port, "control": a.Control, "viewer": a.Viewer, "pid": os.Getpid(), "core_engine": "go"}
	if err = atomicJSON(filepath.Join(p.DataRoot, "backend.json"), state); err != nil {
		return err
	}
	uiState := map[string]any{"app": "workdesk-ui-02", "revision": a.Implementation, "port": p.UIPort, "token": a.UIToken, "instance": a.Process, "pid": os.Getpid(), "instance_id": p.InstanceID, "channel": "development", "program_root": p.ProgramRoot, "user_root": p.UserRoot, "data_root": p.DataRoot, "core_engine": "go"}
	uiRecord := filepath.Join(p.UserRoot, "runtime", "ui", "instance-core.json")
	if err = atomicJSON(uiRecord, uiState); err != nil {
		return err
	}
	defer os.Remove(uiRecord)
	go func() {
		e := controlServer.Serve(control)
		if e != nil && !errors.Is(e, http.ErrServerClosed) {
			fmt.Fprintln(log, e)
			a.Shutdown()
		}
	}()
	go func() {
		e := uiServer.Serve(ui)
		if e != nil && !errors.Is(e, http.ErrServerClosed) {
			fmt.Fprintln(log, e)
			a.Shutdown()
		}
	}()
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt)
	defer signal.Stop(signals)
	select {
	case <-a.Stop:
	case <-signals:
		a.Shutdown()
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = uiServer.Shutdown(ctx)
	_ = controlServer.Shutdown(ctx)
	return nil
}
func atomicJSON(path string, value any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	raw, err := json.Marshal(value)
	if err != nil {
		return err
	}
	temporary := path + ".tmp-" + app.Token(8)
	if err = os.WriteFile(temporary, raw, 0600); err != nil {
		return err
	}
	if err = os.Rename(temporary, path); err != nil {
		_ = os.Remove(temporary)
	}
	return err
}
