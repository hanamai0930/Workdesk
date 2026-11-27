package components

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

type packedDep struct {
	ID            string
	Version       string
	SHA256        string
	Entry         string
	LicenseSHA256 string `json:"license_sha256"`
}
type packedHeader struct {
	Format        string
	FormatVersion int `json:"format_version"`
	ID            string
	ReleaseID     string `json:"release_id"`
	Platform      string
	Version       string
	Entry         []string
	Dependencies  []packedDep
}

// Hash a private copy first. A removable drive, rename or subsequent write to
// the user-selected file cannot change any bytes handed to the archive reader.
func (m *Manager) openPinnedBundle(ctx context.Context, source, stage string, pin bundlePin) (*os.File, error) {
	ext := strings.ToLower(filepath.Ext(source))
	if !filepath.IsAbs(source) || (ext != ".wdpa" && ext != ".wdpartner") {
		return nil, errors.New("请选择本机的 Workdesk 伙伴补充包")
	}
	src, err := os.Open(source)
	if err != nil {
		return nil, err
	}
	defer src.Close()
	stat, err := src.Stat()
	if err != nil {
		return nil, err
	}
	if !stat.Mode().IsRegular() || stat.Size() != pin.bytes {
		return nil, errors.New("补充包大小与发行清单不同")
	}
	tmp, err := os.OpenFile(filepath.Join(stage, "source.wdpa"), os.O_EXCL|os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	hash := sha256.New()
	m.progress("verify_bundle", 0, pin.bytes)
	writer := &progressWriter{ctx: ctx, target: io.MultiWriter(tmp, hash), report: func(done int64) {
		m.progress("verify_bundle", done, pin.bytes)
	}}
	n, err := io.Copy(writer, io.LimitReader(src, pin.bytes+1))
	if err == nil && (n != pin.bytes || hex.EncodeToString(hash.Sum(nil)) != pin.hash) {
		err = errors.New("补充包校验失败")
	}
	if err == nil {
		_, err = tmp.Seek(0, io.SeekStart)
	}
	if err != nil {
		tmp.Close()
		return nil, err
	}
	return tmp, nil
}

func (m *Manager) unpackPartner(ctx context.Context, path string, pin bundlePin, stage, program string, r *Release) error {
	input, err := m.openPinnedBundle(ctx, path, stage, pin)
	if err != nil {
		return err
	}
	defer input.Close()
	headerBytes := make([]byte, 10)
	if _, err = io.ReadFull(input, headerBytes); err != nil || string(headerBytes) != "WDPARTNER\x01" {
		return errors.New("伙伴包格式或版本不受支持")
	}
	gz, err := gzip.NewReader(input)
	if err != nil {
		return err
	}
	defer gz.Close()
	archive := tar.NewReader(gz)
	first, err := archive.Next()
	if err != nil || first.Name != "partner.json" || first.Size > 65536 {
		return errors.New("伙伴包描述无效")
	}
	var head packedHeader
	if err = json.NewDecoder(archive).Decode(&head); err != nil {
		return err
	}
	if head.Format != "wdpartner" || head.FormatVersion != 1 || head.ReleaseID != pin.release || head.ID != r.ComponentID || head.Version != pin.version || head.Platform != platform() {
		return errors.New("伙伴包组件身份与发行清单不一致")
	}
	list, err := archive.Next()
	if err != nil || list.Name != "component-files.json" || list.Size > 64<<20 {
		return errors.New("缺少受校验的组件文件清单")
	}
	// Whole bundle is already authenticated, and publication rehashes the installed tree.
	if _, err = io.Copy(io.Discard, archive); err != nil {
		return err
	}
	staged, err := os.OpenRoot(stage)
	if err != nil {
		return err
	}
	defer staged.Close()
	var total int64
	m.progress("extract", 0, pin.componentBytes)
	writer := &progressWriter{ctx: ctx, report: func(done int64) {
		m.progress("extract", done, pin.componentBytes)
	}}
	for {
		if err = ctx.Err(); err != nil {
			return err
		}
		h, e := archive.Next()
		if e == io.EOF {
			break
		}
		if e != nil {
			return e
		}
		if h.Typeflag != tar.TypeReg || !fs.ValidPath(h.Name) || strings.ContainsAny(h.Name, "\\:") || !filepath.IsLocal(filepath.FromSlash(h.Name)) {
			return errors.New("伙伴包成员路径或类型无效")
		}
		name := h.Name
		component := strings.HasPrefix(name, "component/")
		if component {
			name = "program/" + strings.TrimPrefix(name, "component/")
		} else if !strings.HasPrefix(name, "dependencies/") {
			return errors.New("伙伴包出现未知文件类别")
		}
		total += h.Size
		if h.Size < 0 || h.Size > 2<<30 || total > 8<<30 {
			return errors.New("伙伴包展开大小超限")
		}
		name = filepath.FromSlash(name)
		if e = staged.MkdirAll(filepath.Dir(name), 0700); e != nil {
			return e
		}
		out, e := staged.OpenFile(name, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0700)
		if e != nil {
			return e
		}
		var target io.Writer = out
		if component {
			writer.target = out
			target = writer
		}
		n, copyErr := io.Copy(target, archive)
		closeErr := out.Close()
		if copyErr != nil {
			return copyErr
		}
		if closeErr != nil {
			return closeErr
		}
		if n != h.Size {
			return io.ErrUnexpectedEOF
		}
	}
	if _, err = io.Copy(io.Discard, gz); err != nil {
		return err
	}
	depEntries := map[string]string{}
	for _, d := range head.Dependencies {
		if d.ID != "node-"+nodeVersion+"-"+platform() || d.Version != nodeVersion || !digestPattern.MatchString(d.SHA256) || !digestPattern.MatchString(d.LicenseSHA256) || d.Entry != "dependencies/"+d.ID+"/node.exe" {
			return errors.New("补充包依赖身份无效")
		}
		src := filepath.Join(stage, "dependencies", d.ID)
		dest := filepath.Join(m.Root, ".tools", d.ID+"-execution-"+d.SHA256[:16])
		if err = plain(dest); err != nil {
			return err
		}
		if err = os.MkdirAll(filepath.Dir(dest), 0700); err != nil {
			return err
		}
		if _, e := os.Stat(dest); os.IsNotExist(e) {
			if err = os.Rename(src, dest); err != nil {
				return err
			}
		} else if e != nil {
			return e
		}
		exe := filepath.Join(dest, "node.exe")
		for filename, want := range map[string]string{exe: d.SHA256, filepath.Join(dest, "LICENSE"): d.LicenseSHA256} {
			if err = plain(filename); err != nil {
				return err
			}
			actual, e := fileSHA(filename)
			if e != nil || actual != want {
				return errors.New("现有共享运行组件与固定版本不同")
			}
		}
		r.Dependencies = append(r.Dependencies, Dependency{ID: d.ID, Version: d.Version, Path: exe, SHA256: d.SHA256, Source: "Workdesk verified offline package"})
		depEntries[d.Entry] = exe
	}
	for _, entry := range head.Entry {
		if exe := depEntries[entry]; exe != "" {
			r.Entry = append(r.Entry, exe)
			continue
		}
		rel, ok := strings.CutPrefix(entry, "component/")
		if !ok {
			return errors.New("补充包入口超出组件")
		}
		file, e := child(program, rel)
		if e != nil {
			return e
		}
		info, e := os.Stat(file)
		if e != nil || !info.Mode().IsRegular() {
			return errors.New("伙伴包未包含所需入口")
		}
		r.Entry = append(r.Entry, file)
	}
	if len(r.Entry) == 0 {
		return errors.New("伙伴包缺少程序入口")
	}
	r.Version = pin.version
	r.Source = fmt.Sprintf("Workdesk v1.2 offline %s", r.ComponentID)
	r.Sources = []Source{{URL: r.Source, SHA256: pin.hash, Verification: "PINNED_WDPA_SHA256"}}
	return nil
}
