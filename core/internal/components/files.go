package components

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type File struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
	Bytes  int64  `json:"bytes"`
}

// plain also rejects Windows reparse points, since Go reports junctions as
// symlinks on supported Windows versions. Resolve every existing ancestor before
// using a recorded path, including the root itself.
func plain(path string) error {
	path, err := filepath.Abs(path)
	if err != nil {
		return err
	}
	for p := path; p != filepath.Dir(p); p = filepath.Dir(p) {
		i, e := os.Lstat(p)
		if os.IsNotExist(e) {
			continue
		}
		if e != nil {
			return e
		}
		if i.Mode()&os.ModeSymlink != 0 {
			return errors.New("组件位置包含链接；保留原文件")
		}
	}
	return nil
}

func child(root, name string) (string, error) {
	if name == "" || strings.ContainsAny(name, "\\:\x00") || strings.HasPrefix(name, "/") {
		return "", errors.New("组件归档路径无效")
	}
	clean := filepath.Clean(filepath.FromSlash(name))
	if clean == "." || clean == ".." || strings.HasPrefix(clean, ".."+string(os.PathSeparator)) || filepath.IsAbs(clean) {
		return "", errors.New("组件归档路径越界")
	}
	for _, part := range strings.Split(filepath.ToSlash(clean), "/") {
		stem := strings.ToUpper(strings.SplitN(part, ".", 2)[0])
		if strings.HasSuffix(part, ".") || strings.HasSuffix(part, " ") || stem == "CON" || stem == "NUL" || stem == "PRN" || stem == "AUX" || (len(stem) == 4 && (strings.HasPrefix(stem, "COM") || strings.HasPrefix(stem, "LPT")) && stem[3] >= '0' && stem[3] <= '9') {
			return "", errors.New("组件归档包含设备或歧义文件名")
		}
	}
	target := filepath.Join(root, clean)
	if err := plain(target); err != nil {
		return "", err
	}
	return target, nil
}

func copyRegular(root, name string, mode fs.FileMode, r io.Reader, size int64) error {
	if size < 0 || size > 2<<30 {
		return errors.New("组件归档成员大小无效")
	}
	target, err := child(root, name)
	if err != nil {
		return err
	}
	if err = os.MkdirAll(filepath.Dir(target), 0700); err != nil {
		return err
	}
	f, err := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, mode.Perm()&0755|0600)
	if err != nil {
		return err
	}
	n, e := io.Copy(f, io.LimitReader(r, size+1))
	closeErr := f.Close()
	if e != nil {
		return e
	}
	if closeErr != nil {
		return closeErr
	}
	if n != size {
		return errors.New("组件归档成员长度不符")
	}
	return nil
}

func extractTarGz(path, root string, strip int, keep func(string) bool) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	g, err := gzip.NewReader(f)
	if err != nil {
		return err
	}
	defer g.Close()
	t := tar.NewReader(g)
	var total int64
	for {
		h, e := t.Next()
		if e == io.EOF {
			return nil
		}
		if e != nil {
			return e
		}
		// Validate original names before stripping, including skipped members.
		if _, e = child(root, h.Name); e != nil {
			return e
		}
		parts := strings.Split(strings.TrimSuffix(h.Name, "/"), "/")
		if len(parts) <= strip {
			continue
		}
		name := strings.Join(parts[strip:], "/")
		if h.Typeflag == tar.TypeSymlink || h.Typeflag == tar.TypeLink {
			return errors.New("组件归档含链接；未解包")
		}
		if keep != nil && !keep(name) {
			continue
		}
		if h.Typeflag == tar.TypeDir {
			p, e := child(root, name)
			if e != nil {
				return e
			}
			if e = os.MkdirAll(p, 0700); e != nil {
				return e
			}
			continue
		}
		if h.Typeflag != tar.TypeReg && h.Typeflag != tar.TypeRegA {
			return errors.New("组件归档含非普通成员")
		}
		total += h.Size
		if total > 8<<30 {
			return errors.New("组件归档展开超过8GiB")
		}
		if e = copyRegular(root, name, fs.FileMode(h.Mode), t, h.Size); e != nil {
			return e
		}
	}
}

func extractZip(path, root string, strip int, keep func(string) bool) error {
	z, err := zip.OpenReader(path)
	if err != nil {
		return err
	}
	defer z.Close()
	var total int64
	for _, f := range z.File {
		if _, err = child(root, f.Name); err != nil {
			return err
		}
		parts := strings.Split(strings.TrimSuffix(f.Name, "/"), "/")
		if len(parts) <= strip {
			continue
		}
		name := strings.Join(parts[strip:], "/")
		if f.Mode()&os.ModeSymlink != 0 {
			return errors.New("组件ZIP含链接")
		}
		if keep != nil && !keep(name) {
			continue
		}
		if f.FileInfo().IsDir() {
			p, e := child(root, name)
			if e != nil {
				return e
			}
			if e = os.MkdirAll(p, 0700); e != nil {
				return e
			}
			continue
		}
		if f.UncompressedSize64 > 2<<30 {
			return errors.New("组件ZIP成员过大")
		}
		size := int64(f.UncompressedSize64)
		total += size
		if total > 8<<30 {
			return errors.New("组件ZIP展开超过8GiB")
		}
		r, e := f.Open()
		if e != nil {
			return e
		}
		e = copyRegular(root, name, f.Mode(), r, size)
		r.Close()
		if e != nil {
			return e
		}
	}
	return nil
}

func tree(root string) ([]File, string, error) {
	files := []File{}
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, e error) error {
		if e != nil {
			return e
		}
		if d.Type()&os.ModeSymlink != 0 {
			return errors.New("组件树含链接")
		}
		if d.IsDir() {
			return nil
		}
		if !d.Type().IsRegular() {
			return errors.New("组件树含非普通文件")
		}
		rel, e := filepath.Rel(root, path)
		if e != nil {
			return e
		}
		f, e := os.Open(path)
		if e != nil {
			return e
		}
		h := sha256.New()
		n, e := io.Copy(h, f)
		f.Close()
		if e != nil {
			return e
		}
		files = append(files, File{filepath.ToSlash(rel), hex.EncodeToString(h.Sum(nil)), n})
		return nil
	})
	if err != nil {
		return nil, "", err
	}
	sort.Slice(files, func(i, j int) bool { return files[i].Path < files[j].Path })
	raw, _ := json.Marshal(files)
	h := sha256.Sum256(raw)
	return files, hex.EncodeToString(h[:]), nil
}

func writeJSON(path string, value any) error {
	if err := plain(path); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	raw, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp-" + nonce()
	if err = os.WriteFile(tmp, raw, 0600); err != nil {
		return err
	}
	defer os.Remove(tmp)
	return os.Rename(tmp, path)
}

func readJSON(path string, value any) error {
	if err := plain(path); err != nil {
		return err
	}
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	dec := json.NewDecoder(io.LimitReader(f, 32<<20))
	if err = dec.Decode(value); err != nil {
		return fmt.Errorf("组件记录无效: %w", err)
	}
	return nil
}
