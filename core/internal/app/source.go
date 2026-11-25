package app

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
)

// New work must not mix an old owner with freshly edited compatibility code.
func (a *App) loadSources() error {
	raw, e := os.ReadFile(filepath.Join(a.Profile.DataRoot, "implementations", a.Implementation, "manifest.json"))
	if e != nil {
		return e
	}
	var manifest map[string]string
	if e = json.Unmarshal(raw, &manifest); e != nil {
		return e
	}
	a.sourceFiles = map[string]string{}
	for name, hash := range manifest {
		if name == "core/workdesk-core.exe" {
			continue
		}
		a.sourceFiles[name] = hash
	}
	return a.verifySources()
}
func (a *App) verifySources() error {
	for name, expected := range a.sourceFiles {
		path := filepath.Join(a.Profile.ProgramRoot, filepath.FromSlash(name))
		relative, err := filepath.Rel(a.Profile.ProgramRoot, path)
		if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(os.PathSeparator)) {
			return errors.New("执行代码引用越界")
		}
		raw, e := os.ReadFile(path)
		if e != nil {
			return errors.New("执行源码缺失；保留原运行，请安全更新核心")
		}
		hash := sha256.Sum256(raw)
		if hex.EncodeToString(hash[:]) != expected {
			return errors.New("源码已变化；本次新操作未派发，请安全更新核心")
		}
	}
	return nil
}
func ownerControl(kind string) bool {
	switch kind {
	case "service.reload", "task.stop", "task.pause", "task.cancel", "task.reconcile", "approval.resolve", "native.resolve", "chat.api-stop", "tool.cancel":
		return true
	}
	return strings.HasSuffix(kind, ".reconcile")
}
