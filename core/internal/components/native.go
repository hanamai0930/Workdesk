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

// The official npm 2.8.0 manifest references an unpublished acp-server package.
// Use the publisher's checksum-pinned standalone distribution instead of
// omitting that dependency, substituting another Agent or patching its loop.
func (m *Manager) prepareKimi(ctx context.Context, version, program string, r *Release) error {
	base := "https://code.kimi.com/kimi-code/"
	if version == "" || version == "latest" {
		raw, err := m.get(ctx, base+"latest", 4096)
		if err != nil {
			return err
		}
		version = strings.TrimSpace(string(raw))
	}
	if !versionPattern.MatchString(version) {
		return errors.New("Kimi官方版本指针无效")
	}
	raw, err := m.get(ctx, base+"binaries/"+version+"/manifest.json", 1<<20)
	if err != nil {
		return err
	}
	var manifest struct {
		Version   string `json:"version"`
		Platforms map[string]struct {
			Filename string `json:"filename"`
			Checksum string `json:"checksum"`
		} `json:"platforms"`
	}
	if err = json.Unmarshal(raw, &manifest); err != nil {
		return err
	}
	osName := runtime.GOOS
	if osName == "windows" {
		osName = "win32"
	}
	arch := map[string]string{"amd64": "x64", "arm64": "arm64"}[runtime.GOARCH]
	entry, ok := manifest.Platforms[osName+"-"+arch]
	if manifest.Version != version || !ok || !digestPattern.MatchString(entry.Checksum) || filepath.Base(entry.Filename) != entry.Filename {
		return errors.New("Kimi官方平台清单身份或摘要无效")
	}
	archive, proof, err := m.download(ctx, base+"binaries/"+version+"/"+entry.Filename, entry.Checksum, "")
	if err != nil {
		return err
	}
	name := "kimi"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	target := filepath.Join(program, name)
	if err = copyFile(archive, target, 0755); err != nil {
		return err
	}
	r.Version = version
	r.Source = base + "binaries/" + version + "/manifest.json"
	r.Sources = append(r.Sources, proof)
	r.Entry = []string{target}
	r.Notes = append(r.Notes, "使用Kimi官方安装脚本所指向、经官方manifest摘要校验的独立可执行发布；不依赖当前npm清单中的未发布ACP包。")
	return nil
}

func (m *Manager) prepareGrok(ctx context.Context, version, program string, r *Release) error {
	base := "https://x.ai/cli/"
	if version == "" || version == "latest" {
		raw, e := m.get(ctx, base+"stable", 4096)
		if e != nil {
			base = "https://storage.googleapis.com/grok-build-public-artifacts/cli/"
			raw, e = m.get(ctx, base+"stable", 4096)
		}
		if e != nil {
			return e
		}
		version = strings.TrimSpace(string(raw))
	}
	if !versionPattern.MatchString(version) {
		return errors.New("Grok官方版本指针无效")
	}
	arch := map[string]string{"amd64": "x86_64", "arm64": "aarch64"}[runtime.GOARCH]
	if arch == "" {
		return errors.New("Grok平台未登记")
	}
	osName := runtime.GOOS
	if osName == "darwin" {
		osName = "darwin"
	}
	name := "grok-" + version + "-" + osName + "-" + arch
	ext := ""
	if runtime.GOOS == "windows" {
		ext = ".exe"
	}
	archive, proof, e := m.download(ctx, base+name+ext, "", "")
	if e != nil {
		return e
	}
	r.Version = version
	r.Source = "https://x.ai/cli/install.ps1"
	r.Sources = append(r.Sources, proof)
	target := filepath.Join(program, "grok"+ext)
	if e = copyFile(archive, target, 0755); e != nil {
		return e
	}
	r.Entry = []string{target}
	r.Notes = append(r.Notes, "官方安装脚本未为主程序提供SHA256；已记录HTTPS来源和实际文件摘要，未冒称官方签名验证。")
	// Official Windows releases ship these siblings for native git hooks. They
	// are optional only for older releases; retain all when the source provides
	// them. System Git is used rather than modifying per-user bundled Git state.
	if runtime.GOOS == "windows" {
		for _, bin := range []string{"grove", "grove-fsmonitor", "grove-credential"} {
			path, proof, e := m.download(ctx, base+bin+"-"+version+"-windows-"+arch+".exe", "", "")
			if e != nil {
				return fmt.Errorf("Grok官方Git助手%s准备失败: %w", bin, e)
			}
			if e = copyFile(path, filepath.Join(program, bin+".exe"), 0755); e != nil {
				return e
			}
			r.Sources = append(r.Sources, proof)
		}
	}
	return nil
}

func copyFile(from, to string, mode os.FileMode) error {
	f, e := os.Open(from)
	if e != nil {
		return e
	}
	defer f.Close()
	info, e := f.Stat()
	if e != nil {
		return e
	}
	return copyRegular(filepath.Dir(to), filepath.Base(to), mode, f, info.Size())
}

type githubRelease struct {
	Tag    string `json:"tag_name"`
	Assets []struct {
		Name   string `json:"name"`
		URL    string `json:"browser_download_url"`
		Digest string `json:"digest"`
	} `json:"assets"`
}

func (m *Manager) prepareZCode(ctx context.Context, version, program string, r *Release) error {
	if runtime.GOOS != "windows" {
		return errors.New("ZCode官方Agent资源提取当前适配Windows；其他平台需对应发布包接线")
	}
	ref := "latest"
	if version != "" && version != "latest" {
		ref = "tags/v" + strings.TrimPrefix(version, "v")
	}
	raw, e := m.get(ctx, "https://api.github.com/repos/zai-org/ZCode/releases/"+ref, 2<<20)
	if e != nil {
		return e
	}
	var release githubRelease
	if e = json.Unmarshal(raw, &release); e != nil {
		return e
	}
	version = strings.TrimPrefix(release.Tag, "v")
	if !versionPattern.MatchString(version) {
		return errors.New("ZCode官方发布版本无效")
	}
	arch := map[string]string{"amd64": "x64", "arm64": "arm64"}[runtime.GOARCH]
	name := "ZCode-" + version + "-win-" + arch + ".exe"
	source, digest := "", ""
	for _, a := range release.Assets {
		if a.Name == name {
			source = a.URL
			digest = strings.TrimPrefix(a.Digest, "sha256:")
		}
	}
	if source == "" || len(digest) != 64 {
		return errors.New("ZCode官方发布未提供平台安装包及摘要")
	}
	archive, proof, e := m.download(ctx, source, digest, "")
	if e != nil {
		return e
	}
	seven, e := m.ensure7zip(ctx)
	if e != nil {
		return e
	}
	node, e := m.ensureNode(ctx)
	if e != nil {
		return e
	}
	stage := filepath.Dir(program)
	home := filepath.Join(stage, "extract-home")
	if e = os.MkdirAll(home, 0700); e != nil {
		return e
	}
	env := environment(home, nil)
	// NSIS is never executed. Extract its nested app archive, then ONLY the
	// official standalone Agent resource subtree. No Electron/Web client files
	// are installed or run. 7zip is a separately integrity-checked helper.
	nested := filepath.Join(stage, "payload")
	if e = os.MkdirAll(nested, 0700); e != nil {
		return e
	}
	out, e := run(ctx, []string{seven, "e", archive, "-o" + nested, "$PLUGINSDIR/app-64.7z", "-y"}, stage, env)
	if e != nil {
		return fmt.Errorf("提取ZCode官方资源归档失败: %w: %s", e, tail(out, 1000))
	}
	payload := filepath.Join(nested, "app-64.7z")
	if _, e = os.Stat(payload); e != nil {
		return errors.New("官方ZCode安装包布局变化，未猜选或运行安装器")
	}
	providerResource := "resources/config/provider/zcode-builtin.json"
	listing, e := run(ctx, []string{seven, "l", payload, "resources/glm/*", providerResource, "-slt"}, stage, env)
	if e != nil {
		return e
	}
	if !strings.Contains(listing, "resources\\glm\\zcode.cjs") && !strings.Contains(listing, "resources/glm/zcode.cjs") {
		return errors.New("官方ZCode包未找到独立Agent入口")
	}
	for _, line := range strings.Split(listing, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "Symbolic Link =") || strings.HasPrefix(line, "Hard Link =") {
			return errors.New("ZCodeAgent归档包含链接")
		}
		if strings.HasPrefix(line, "Path = ") {
			p := strings.TrimPrefix(line, "Path = ")
			if p == payload {
				continue
			}
			p = strings.ReplaceAll(p, "\\", "/")
			if !strings.HasPrefix(p, "resources/glm/") && p != providerResource {
				return errors.New("ZCode提取清单包含非Agent资源")
			}
			if _, e = child(program, p); e != nil {
				return e
			}
		}
	}
	out, e = run(ctx, []string{seven, "x", payload, "resources/glm/*", providerResource, "-o" + program, "-y"}, stage, env)
	if e != nil {
		return fmt.Errorf("提取ZCodeAgent失败: %w: %s", e, tail(out, 1000))
	}
	entry := filepath.Join(program, "resources", "glm", "zcode.cjs")
	if _, e = os.Stat(entry); e != nil {
		return e
	}
	// Electron passes the adjacent built-in provider file explicitly, whereas
	// the standalone Agent resolves <entry>/provider/zcode-builtin.json. Stage
	// the SAME published bytes at that documented standalone-relative location;
	// never synthesize provider rules from a different source revision.
	providerTarget := filepath.Join(program, "resources", "glm", "provider", "zcode-builtin.json")
	providerSource := filepath.Join(program, filepath.FromSlash(providerResource))
	if e = os.MkdirAll(filepath.Dir(providerTarget), 0700); e != nil {
		return e
	}
	if _, e = os.Stat(providerTarget); os.IsNotExist(e) {
		if e = copyFile(providerSource, providerTarget, 0600); e != nil {
			return e
		}
	} else if e != nil {
		return e
	} else {
		left, e := fileSHA(providerSource)
		if e != nil {
			return e
		}
		right, e := fileSHA(providerTarget)
		if e != nil {
			return e
		}
		if left != right {
			return errors.New("官方ZCode发布内的provider资源不一致；未覆盖")
		}
	}
	if e = extractZCodeKeyring(ctx, seven, payload, program, stage, env, arch); e != nil {
		return e
	}
	r.Version = version
	r.Source = "https://github.com/zai-org/ZCode/releases/tag/" + release.Tag
	r.Sources = append(r.Sources, proof)
	r.Dependencies = append(r.Dependencies, node)
	r.Entry = []string{node.Path, entry}
	r.Notes = append(r.Notes, "从校验通过的同一官方Windows发布包提取resources/glm、provider配置及独立Agent动态依赖的7个keyring原文件；ASAR仅在准备临时区有界读取，没有执行安装程序或安装桌面/Web客户端。")
	return nil
}
