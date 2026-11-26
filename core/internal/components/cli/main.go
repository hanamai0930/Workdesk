// An explicit development helper for the same component engine used by core.
// It never opens the active library or reads provider credentials. The core's
// component commands enforce library reference protection before Remove.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"
	"workdesk/core/internal/components"
)

func main() {
	root := flag.String("root", "", "absolute development components root")
	action := flag.String("action", "list", "list, catalog, prepare, inspect, verify, remove")
	component := flag.String("component", "", "official partner id")
	version := flag.String("version", "latest", "exact version or latest")
	id := flag.String("id", "", "exact release id")
	timeout := flag.Duration("timeout", 30*time.Minute, "operation timeout")
	flag.Parse()
	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()
	m := components.New(*root)
	m.Log = func(s string) { fmt.Fprintln(os.Stderr, s) }
	var result any
	var err error
	switch *action {
	case "catalog":
		result = components.Catalog()
	case "list":
		result, err = m.List()
	case "prepare":
		result, err = m.Prepare(ctx, *component, *version)
	case "inspect":
		result, err = m.Inspect(ctx, *id)
	case "verify":
		result, err = m.Verify(ctx, *id)
	case "remove":
		err = fmt.Errorf("移除需经核心引用保护；此诊断入口不提供绕过活动库引用检查的删除操作")
	default:
		err = fmt.Errorf("unknown action")
	}
	if err != nil {
		_ = json.NewEncoder(os.Stdout).Encode(map[string]any{"state": "FAILED", "error": err.Error(), "result": result})
		os.Exit(1)
	}
	_ = json.NewEncoder(os.Stdout).Encode(result)
}
