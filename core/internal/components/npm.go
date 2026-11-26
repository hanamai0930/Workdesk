package components

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

const nodeVersion = "v24.14.0"

func (m *Manager) ensureNode(ctx context.Context) (Dependency, error) {
	arch := map[string]string{"amd64": "x64", "arm64": "arm64"}[runtime.GOARCH]
	if arch == "" {
		return Dependency{}, errors.New("Node官方运行依赖不支持此CPU")
	}
	osName := runtime.GOOS
	if osName == "windows" {
		osName = "win"
	}
	if osName != "win" && osName != "linux" && osName != "darwin" {
		return Dependency{}, errors.New("Node依赖平台未登记")
	}
	filename := "node-" + nodeVersion + "-" + osName + "-" + arch + ".tar.gz"
	if runtime.GOOS == "windows" {
		filename = "node-" + nodeVersion + "-win-" + arch + ".zip"
	}
	base := "https://nodejs.org/dist/" + nodeVersion + "/"
	raw, e := m.get(ctx, base+"SHASUMS256.txt", 1<<20)
	if e != nil {
		return Dependency{}, e
	}
	expected := ""
	for _, line := range strings.Split(string(raw), "\n") {
		p := strings.Fields(line)
		if len(p) == 2 && strings.TrimPrefix(p[1], "*") == filename {
			expected = p[0]
		}
	}
	if len(expected) != 64 {
		return Dependency{}, errors.New("未找到官方Node摘要")
	}
	dest := filepath.Join(m.Root, ".tools", "node-"+nodeVersion+"-"+platform()+"-"+expected[:16])
	executable := filepath.Join(dest, "bin", "node")
	if runtime.GOOS == "windows" {
		executable = filepath.Join(dest, "node.exe")
	}
	if e = plain(dest); e != nil {
		return Dependency{}, e
	}
	if _, err := os.Stat(executable); err == nil {
		var saved struct {
			Schema        int        `json:"schema"`
			Dependency    Dependency `json:"dependency"`
			ArchiveSHA256 string     `json:"archive_sha256"`
		}
		if err = readJSON(filepath.Join(dest, "workdesk-tool.json"), &saved); err != nil {
			return Dependency{}, errors.New("共享Node发布清单缺失，未信任现存程序")
		}
		actual, err := fileSHA(executable)
		if err != nil {
			return Dependency{}, err
		}
		if saved.Schema != 1 || saved.ArchiveSHA256 != expected || saved.Dependency.Path != executable || saved.Dependency.Version != nodeVersion || saved.Dependency.SHA256 != actual {
			return Dependency{}, errors.New("共享Node发布内容或来源已变化")
		}
		return saved.Dependency, nil
	}
	if _, e = os.Stat(executable); os.IsNotExist(e) {
		archive, _, e := m.download(ctx, base+filename, expected, "")
		if e != nil {
			return Dependency{}, e
		}
		stage, e := os.MkdirTemp(filepath.Join(m.Root, ".staging"), "node-")
		if e != nil {
			return Dependency{}, e
		}
		if runtime.GOOS == "windows" {
			e = extractZip(archive, stage, 1, nil)
		} else {
			e = extractTarGz(archive, stage, 1, nil)
		}
		if e != nil {
			return Dependency{}, e
		}
		if e = os.MkdirAll(filepath.Dir(dest), 0700); e != nil {
			return Dependency{}, e
		}
		stageExe := filepath.Join(stage, "bin", "node")
		if runtime.GOOS == "windows" {
			stageExe = filepath.Join(stage, "node.exe")
		}
		sha, err := fileSHA(stageExe)
		if err != nil {
			return Dependency{}, err
		}
		dep := Dependency{"node-" + nodeVersion + "-" + platform(), executable, nodeVersion, sha, base + filename}
		if e = writeJSON(filepath.Join(stage, "workdesk-tool.json"), map[string]any{"schema": 1, "dependency": dep, "archive_sha256": expected}); e != nil {
			return Dependency{}, e
		}
		if e = os.Rename(stage, dest); e != nil {
			return Dependency{}, e
		}
	} else if e != nil {
		return Dependency{}, e
	}
	var saved struct {
		Dependency Dependency `json:"dependency"`
	}
	if e = readJSON(filepath.Join(dest, "workdesk-tool.json"), &saved); e != nil {
		return Dependency{}, e
	}
	return saved.Dependency, nil
}

func (m *Manager) prepareNPM(ctx context.Context, p Partner, version, program string, r *Release) error {
	meta, e := m.metadata(ctx, p.Package, version)
	if e != nil {
		return e
	}
	r.Version = meta.Version
	r.Source = "https://registry.npmjs.org/" + p.Package + "/" + meta.Version
	r.License = meta.License
	selected := meta
	if p.ID == "codex" {
		nodePlatform := map[string]string{"windows": "win32", "linux": "linux", "darwin": "darwin"}[runtime.GOOS]
		arch := map[string]string{"amd64": "x64", "arm64": "arm64"}[runtime.GOARCH]
		alias := meta.OptionalDependencies["@openai/codex-"+nodePlatform+"-"+arch]
		at := strings.LastIndex(alias, "@")
		if !strings.HasPrefix(alias, "npm:@openai/codex@") || at < 0 {
			return errors.New("官方Codex未公布该平台包")
		}
		selected, e = m.metadata(ctx, "@openai/codex", alias[at+1:])
		if e != nil {
			return e
		}
	} else if p.ID == "claude" {
		nodePlatform := map[string]string{"windows": "win32", "linux": "linux", "darwin": "darwin"}[runtime.GOOS]
		arch := map[string]string{"amd64": "x64", "arm64": "arm64"}[runtime.GOARCH]
		name := "@anthropic-ai/claude-code-" + nodePlatform + "-" + arch
		ver := meta.OptionalDependencies[name]
		if ver != "" {
			selected, e = m.metadata(ctx, name, ver)
			if e != nil {
				return e
			}
		}
	}
	archive, proof, e := m.download(ctx, selected.Dist.Tarball, "", selected.Dist.Integrity)
	if e != nil {
		return e
	}
	r.Sources = append(r.Sources, proof)
	pkgDir := filepath.Join(program, "package")
	if e = os.MkdirAll(pkgDir, 0700); e != nil {
		return e
	}
	if e = extractTarGz(archive, pkgDir, 1, nil); e != nil {
		return e
	}
	if p.ID == "codex" || p.ID == "claude" {
		base := p.ID
		if runtime.GOOS == "windows" {
			base += ".exe"
		}
		binary, e := locate(pkgDir, base)
		if e != nil {
			return e
		}
		r.Entry = []string{binary}
		return nil
	}
	node, e := m.ensureNode(ctx)
	if e != nil {
		return e
	}
	r.Dependencies = append(r.Dependencies, node)
	// Install only this official package's runtime dependencies. npm verifies
	// dependency SRI and records exact versions in package-lock.json. Package
	// lifecycle scripts retain official native modules; no blanket capability
	// trimming or inherited user npm credentials are used.
	home := filepath.Join(filepath.Dir(program), "install-home")
	if e = os.MkdirAll(home, 0700); e != nil {
		return e
	}
	for _, name := range []string{"user.npmrc", "global.npmrc"} {
		if e = os.WriteFile(filepath.Join(home, name), nil, 0600); e != nil {
			return e
		}
	}
	nodeDir := filepath.Dir(node.Path)
	npmCLI := filepath.Join(nodeDir, "node_modules", "npm", "bin", "npm-cli.js")
	if runtime.GOOS != "windows" {
		npmCLI = filepath.Join(nodeDir, "..", "lib", "node_modules", "npm", "bin", "npm-cli.js")
	}
	path := nodeDir + string(os.PathListSeparator) + os.Getenv("PATH")
	env := environment(home, map[string]string{"PATH": path, "NPM_CONFIG_USERCONFIG": filepath.Join(home, "user.npmrc"), "NPM_CONFIG_GLOBALCONFIG": filepath.Join(home, "global.npmrc"), "NPM_CONFIG_CACHE": filepath.Join(filepath.Dir(m.Root), "cache", "components", "npm"), "NPM_CONFIG_REGISTRY": "https://registry.npmjs.org/", "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"})
	args := []string{node.Path, npmCLI, "install", "--prefix", pkgDir, "--omit=dev", "--no-audit", "--no-fund", "--package-lock=true", "--registry=https://registry.npmjs.org/"}
	m.note(p.Name + "：准备官方运行依赖")
	output, e := run(ctx, args, pkgDir, env)
	_ = os.WriteFile(filepath.Join(filepath.Dir(program), "install.log"), []byte(output), 0600)
	if e != nil {
		return fmt.Errorf("npm安装失败（完整诊断保留在暂存install.log）: %w: %s", e, tail(output, 1800))
	}
	bins := map[string]string{}
	if e = json.Unmarshal(meta.Bin, &bins); e != nil {
		var bin string
		if e = json.Unmarshal(meta.Bin, &bin); e != nil {
			return errors.New("官方包未提供可解析CLI入口")
		}
		bins[p.ID] = bin
	}
	binName := map[string]string{"qwen": "qwen", "deepseek": "dsh", "gemini": "gemini", "kimi": "kimi"}[p.ID]
	rel, ok := bins[binName]
	if !ok {
		return errors.New("官方npm包没有预期CLI入口")
	}
	entry, e := child(pkgDir, rel)
	if e != nil {
		return e
	}
	if _, e = os.Stat(entry); e != nil {
		return e
	}
	r.Entry = []string{node.Path, entry}
	return nil
}

func tail(s string, n int) string {
	if len(s) > n {
		return s[len(s)-n:]
	}
	return s
}

func (m *Manager) ensure7zip(ctx context.Context) (string, error) {
	meta, e := m.metadata(ctx, "7zip-bin-full", "26.6.0")
	if e != nil {
		return "", e
	}
	archive, proof, e := m.download(ctx, meta.Dist.Tarball, "", meta.Dist.Integrity)
	if e != nil {
		return "", e
	}
	dest := filepath.Join(m.Root, ".tools", "7zip-26.6.0-"+platform()+"-"+proof.SHA256[:16])
	arch := map[string]string{"amd64": "x64", "arm64": "arm64"}[runtime.GOARCH]
	rel := "win/" + arch + "/7z.exe"
	if runtime.GOOS == "linux" {
		rel = "linux/" + arch + "/7zz"
	} else if runtime.GOOS == "darwin" {
		rel = "mac/" + arch + "/7zz"
	}
	exe := filepath.Join(dest, filepath.FromSlash(rel))
	if _, e = os.Stat(exe); e == nil {
		return exe, plain(exe)
	}
	stage, e := os.MkdirTemp(filepath.Join(m.Root, ".staging"), "7zip-")
	if e != nil {
		return "", e
	}
	keep := func(name string) bool {
		return name == rel || name == strings.TrimSuffix(rel, "7z.exe")+"7z.dll" || name == "LICENSE.txt"
	}
	if e = extractTarGz(archive, stage, 1, keep); e != nil {
		return "", e
	}
	if e = os.MkdirAll(filepath.Dir(dest), 0700); e != nil {
		return "", e
	}
	if e = os.Rename(stage, dest); e != nil {
		return "", e
	}
	if e = writeJSON(filepath.Join(dest, "workdesk-tool.json"), proof); e != nil {
		return "", e
	}
	return exe, nil
}
