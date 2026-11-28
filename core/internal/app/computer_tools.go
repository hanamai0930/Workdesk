package app

import (
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"workdesk/core/internal/instance"
	"workdesk/core/internal/storage"
)

const computerVersion = "computer/1"

var computerActions = []string{"file.list", "file.read", "file.edit", "file.copy", "file.move", "git.status", "git.history", "git.branches", "git.worktrees", "git.diff", "git.stage", "git.commit", "git.restore", "git.branch", "git.worktree", "env.inspect", "env.prepare", "terminal.run", "browser.read", "browser.fill", "browser.click", "browser.download", "desktop.inspect", "desktop.screenshot", "desktop.set", "desktop.invoke"}

func computerPath(root, name string) (string, error) {
	if !filepath.IsLocal(name) || strings.Contains(name, ":") || strings.TrimSpace(name) == "" {
		return "", errors.New("请指定项目内相对路径")
	}
	for _, c := range strings.FieldsFunc(name, func(r rune) bool { return r == '/' || r == '\\' }) {
		v := strings.ToLower(c)
		if v == ".." || strings.HasSuffix(c, " ") || strings.HasSuffix(c, ".") && c != "." || v == ".git" || v == ".maintenance" || strings.HasPrefix(v, ".env") || v == "credentials.json" || v == "auth.json" {
			return "", errors.New("文件路径不可用于本次工具操作")
		}
	}
	path := filepath.Join(root, name)
	if !inside(path, root) {
		return "", errors.New("路径不在项目范围内")
	}
	return path, nil
}

// Freeze the index and currently referenced revision. Changes to either invalidate
// a write that was confirmed for a different working state.
func freezeRepository(root string) (map[string]string, error) {
	meta := filepath.Join(root, ".git")
	info, err := os.Lstat(meta)
	if err != nil || !info.IsDir() || !inside(meta, root) {
		return nil, errors.New("请选择原生 Git 仓库根目录")
	}
	versions := map[string]string{}
	names := []string{"HEAD", "index", "packed-refs"}
	for i := 0; i < len(names); i++ {
		name := names[i]
		if !filepath.IsLocal(name) || strings.Contains(name, ":") {
			return nil, errors.New("Git 引用位置无效")
		}
		path := filepath.Join(meta, name)
		if !inside(path, meta) {
			return nil, errors.New("Git 引用位置无效")
		}
		f, err := os.Open(path)
		if os.IsNotExist(err) && name != "HEAD" {
			versions[name] = "absent"
			continue
		}
		if err != nil {
			return nil, err
		}
		info, err := f.Stat()
		if err != nil || !info.Mode().IsRegular() || info.Size() > 64<<20 {
			f.Close()
			return nil, errors.New("Git 引用或索引无法核对")
		}
		hash := sha256.New()
		if name == "HEAD" {
			data, err := io.ReadAll(io.LimitReader(f, 1025))
			if err != nil || len(data) > 1024 {
				f.Close()
				return nil, errors.New("Git HEAD 无效")
			}
			hash.Write(data)
			ref := strings.TrimSpace(string(data))
			if strings.HasPrefix(ref, "ref: refs/") {
				names = append(names, strings.TrimPrefix(ref, "ref: "))
			}
		} else if _, err = io.Copy(hash, io.LimitReader(f, 64<<20+1)); err != nil {
			f.Close()
			return nil, err
		}
		f.Close()
		versions[name] = fmt.Sprintf("%x", hash.Sum(nil))
	}
	return versions, nil
}
func (a *App) freezeComputerGrant(g *toolGrant, p map[string]any) error {
	if files := p["input_files"]; files != nil {
		list, ok := files.([]any)
		if !ok || len(list) > 0 {
			return errors.New("电脑工具在动作参数中选择材料")
		}
	}
	action := str(g.Parameters["action"])
	if !slices.Contains(computerActions, action) {
		return errors.New("未知电脑工具动作")
	}
	for _, r := range append(append([]string{}, a.Profile.ProtectedRoots...), a.Profile.ProgramRoot, a.Profile.DataRoot) {
		if r != "" && (instance.SamePath(g.ProjectRoot, r) || instance.Within(g.ProjectRoot, r) || instance.Within(r, g.ProjectRoot)) {
			return errors.New("此操作需使用独立的项目目录")
		}
	}
	if _, e := computerPath(g.ProjectRoot, "."); e != nil {
		return e
	}
	name := str(g.Parameters["path"])
	switch action {
	case "file.list":
		if name != "" {
			_, e := computerPath(g.ProjectRoot, name)
			return e
		}
	case "file.read", "file.edit", "file.copy", "file.move", "git.diff", "git.stage", "git.commit", "git.restore", "terminal.run":
		path, e := computerPath(g.ProjectRoot, name)
		if e != nil {
			return e
		}
		info, e := os.Lstat(path)
		if !(os.IsNotExist(e) && action == "file.edit") {
			if e != nil || !info.Mode().IsRegular() || info.Size() > 8<<20 {
				return errors.New("所选文件应是项目内不超过 8 MiB 的普通文件")
			}
			data, e := inputBytes(g.ProjectRoot, name)
			if e != nil {
				return e
			}
			g.Inputs = append(g.Inputs, Artifact{Path: filepath.ToSlash(filepath.Clean(name)), Bytes: int64(len(data)), SHA256: fmt.Sprintf("%x", sha256.Sum256(data))})
		}
	}
	switch action {
	case "git.stage", "git.restore", "git.commit", "git.branch", "git.worktree":
		var err error
		g.RepositoryVersions, err = freezeRepository(g.ProjectRoot)
		if err != nil {
			return err
		}
	}
	if action == "file.copy" || action == "file.move" || action == "git.worktree" {
		dest, e := computerPath(g.ProjectRoot, str(g.Parameters["destination"]))
		if e != nil {
			return e
		}
		if _, e = os.Lstat(dest); !os.IsNotExist(e) {
			return errors.New("目标位置已经存在或无法核对")
		}
	}
	return nil
}
func (a *App) computerRequest(job *Job) (map[string]any, error) {
	var g toolGrant
	err := a.write(func(s *storage.Session) error { var e error; g, e = a.loadToolGrant(s, job.AuthorizationID); return e })
	if err != nil {
		return nil, err
	}
	if g.Tool != computerVersion || !inside(job.OutputDir, filepath.Join(a.Profile.DataRoot, "results")) {
		return nil, errors.New("电脑作业归属无效")
	}
	for _, v := range g.Inputs {
		data, e := inputBytes(g.ProjectRoot, v.Path)
		if e != nil || fmt.Sprintf("%x", sha256.Sum256(data)) != v.SHA256 {
			return nil, errors.New("授权文件版本已改变")
		}
	}
	if len(g.Inputs) == 0 && job.Action == "file.edit" {
		path, e := computerPath(g.ProjectRoot, str(g.Parameters["path"]))
		if e != nil {
			return nil, e
		}
		if _, e = os.Lstat(path); !os.IsNotExist(e) {
			return nil, errors.New("新建文件位置已被占用")
		}
	}
	return map[string]any{"parameters": g.Parameters, "project_root": g.ProjectRoot, "output_dir": job.OutputDir, "input_versions": g.Inputs, "repository_versions": g.RepositoryVersions}, nil
}
func (a *App) queueTerminalInput(s *storage.Session, p map[string]any) (map[string]any, string, string, error) {
	fail := errors.New("原终端作业未就绪或输入无效")
	id := str(p["invocation_id"])
	msg := str(p["text"])
	if !uuidPattern.MatchString(id) || len(msg) == 0 || len(msg) > 4096 {
		return nil, "", "", fail
	}
	rows, e := s.Execute("SELECT data FROM core_tool_jobs WHERE id=? AND state='RUNNING'", []any{id})
	if e != nil {
		return nil, "", "", e
	}
	var job Job
	if len(rows.Rows) != 1 || json.Unmarshal([]byte(str(rows.Rows[0][0])), &job) != nil || job.ToolVersion != computerVersion || job.Action != "terminal.run" || job.OwnerEpoch != a.Epoch || job.ProcessInstance != a.Process {
		return nil, "", "", fail
	}
	base := filepath.Join(a.Profile.DataRoot, "results", id, ".input")
	if _, e = os.Stat(base); e != nil {
		return nil, "", "", fail
	}
	return map[string]any{"invocation_id": id, "state": "INPUT_QUEUED", "bytes": len(msg), "text_sha256": fmt.Sprintf("%x", sha256.Sum256([]byte(msg)))}, job.TaskID, job.AttemptID, nil
}
func (a *App) sendTerminalInput(id, commandID, value string) error {
	root := filepath.Join(a.Profile.DataRoot, "results", id, ".input")
	if !inside(root, filepath.Join(a.Profile.DataRoot, "results")) {
		return errors.New("终端输入位置无效")
	}
	f, e := os.CreateTemp(root, ".writing-")
	if e != nil {
		return e
	}
	defer os.Remove(f.Name())
	_, e = f.WriteString(value)
	if closed := f.Close(); e == nil {
		e = closed
	}
	if e != nil {
		return e
	}
	return os.Rename(f.Name(), filepath.Join(root, fmt.Sprintf("%020.6f-%s.txt", now(), commandID)))
}
func (a *App) terminalConsole(id string) (any, error) {
	if !uuidPattern.MatchString(id) {
		return nil, errors.New("作业身份无效")
	}
	rows, e := a.query("SELECT data FROM core_tool_jobs WHERE id=?", id)
	if e != nil {
		return nil, e
	}
	var j Job
	if len(rows) != 1 || json.Unmarshal([]byte(str(rows[0]["data"])), &j) != nil || j.ToolVersion != computerVersion || j.Action != "terminal.run" {
		return nil, errors.New("未找到终端作业")
	}
	path := filepath.Join(a.Profile.DataRoot, "results", id, "console.txt")
	if !inside(path, filepath.Join(a.Profile.DataRoot, "results", id)) {
		return nil, errors.New("终端日志位置无效")
	}
	f, e := os.Open(path)
	if os.IsNotExist(e) {
		return map[string]any{"text": "", "state": j.State}, nil
	}
	if e != nil {
		return nil, e
	}
	defer f.Close()
	info, e := f.Stat()
	if e != nil {
		return nil, e
	}
	start := max(int64(0), info.Size()-65536)
	raw, e := io.ReadAll(io.NewSectionReader(f, start, 65536))
	if e != nil {
		return nil, e
	}
	return map[string]any{"state": j.State, "text": string(raw), "offset": start, "bytes": info.Size()}, nil
}

// When the worker has exited, seal any partial terminal output and discard
// pending input, including a staged write that the job never consumed.
func (a *App) finishTerminal(job *Job) {
	base := filepath.Join(a.Profile.DataRoot, "results", job.ID)
	if !instance.SamePath(base, job.OutputDir) || !inside(base, a.Profile.DataRoot) {
		return
	}
	inbox := filepath.Join(base, ".input")
	if inside(inbox, base) {
		if entries, err := os.ReadDir(inbox); err == nil {
			for _, entry := range entries {
				if entry.Type().IsRegular() && (strings.HasSuffix(entry.Name(), ".txt") || strings.HasPrefix(entry.Name(), ".writing-")) {
					_ = os.Remove(filepath.Join(inbox, entry.Name()))
				}
			}
			_ = os.Remove(inbox)
		}
	}
	if job.State == "COMPLETED" {
		return
	}
	name := filepath.Join(base, "console.txt")
	if !inside(name, base) {
		return
	}
	f, err := os.Open(name)
	if err != nil {
		return
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 2<<20 {
		return
	}
	hash := sha256.New()
	n, err := io.Copy(hash, io.LimitReader(f, 2<<20+1))
	if err != nil || n != info.Size() {
		return
	}
	job.Artifacts = append(job.Artifacts, Artifact{Path: name, RelativePath: filepath.ToSlash(filepath.Join("results", job.ID, "console.txt")), Mime: "text/plain", Bytes: n, SHA256: fmt.Sprintf("%x", hash.Sum(nil))})
}
