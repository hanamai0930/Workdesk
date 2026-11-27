package components

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// ASAR is read only to recover these externalized standalone-Agent imports
// from the very same checksum-verified official installer. Electron and the
// rest of its node_modules are never installed into the managed release.
type asarNode struct {
	Files     map[string]asarNode `json:"files"`
	Size      int64               `json:"size"`
	Offset    string              `json:"offset"`
	Link      string              `json:"link"`
	Unpacked  bool                `json:"unpacked"`
	Integrity struct {
		Algorithm string `json:"algorithm"`
		Hash      string `json:"hash"`
	} `json:"integrity"`
}

func asarMember(tree asarNode, name string) (asarNode, error) {
	for _, part := range strings.Split(name, "/") {
		if tree.Link != "" {
			return asarNode{}, errors.New("ASAR依赖路径含链接")
		}
		var ok bool
		tree, ok = tree.Files[part]
		if !ok {
			return asarNode{}, fmt.Errorf("官方ASAR缺少独立Agent依赖: %s", name)
		}
	}
	if tree.Link != "" || tree.Files != nil {
		return asarNode{}, errors.New("ASAR依赖不是普通文件")
	}
	return tree, nil
}

func keyringFiles(reader io.ReaderAt, size int64, arch string) (map[string][]byte, error) {
	if arch != "x64" && arch != "arm64" {
		return nil, errors.New("ZCode keyring平台未登记")
	}
	var prefix [16]byte
	if _, e := reader.ReadAt(prefix[:], 0); e != nil {
		return nil, e
	}
	headerSize := int64(binary.LittleEndian.Uint32(prefix[4:8]))
	jsonSize := int64(binary.LittleEndian.Uint32(prefix[12:16]))
	if binary.LittleEndian.Uint32(prefix[:4]) != 4 || headerSize < 8 || headerSize > 16<<20 ||
		int64(binary.LittleEndian.Uint32(prefix[8:12])) != headerSize-4 || jsonSize < 2 ||
		jsonSize > headerSize-8 || headerSize-8-jsonSize > 3 || 8+headerSize > size {
		return nil, errors.New("官方ASAR头长度或格式无效")
	}
	raw := make([]byte, jsonSize)
	if _, e := reader.ReadAt(raw, 16); e != nil {
		return nil, e
	}
	var tree asarNode
	if e := json.Unmarshal(raw, &tree); e != nil {
		return nil, fmt.Errorf("读取官方ASAR索引失败: %w", e)
	}
	platform := "keyring-win32-" + arch + "-msvc"
	limits := map[string]int64{
		"node_modules/@napi-rs/keyring/index.js":                        1 << 20,
		"node_modules/@napi-rs/keyring/keytar.js":                       1 << 20,
		"node_modules/@napi-rs/keyring/LICENSE":                         1 << 20,
		"node_modules/@napi-rs/keyring/package.json":                    64 << 10,
		"node_modules/@napi-rs/" + platform + "/package.json":           64 << 10,
		"node_modules/@napi-rs/" + platform + "/LICENSE.third-party.md": 1 << 20,
	}
	result := make(map[string][]byte, len(limits))
	dataOffset := 8 + headerSize
	for name, limit := range limits {
		node, e := asarMember(tree, name)
		if e != nil {
			return nil, e
		}
		offset, e := strconv.ParseInt(node.Offset, 10, 64)
		if e != nil || node.Unpacked || node.Size <= 0 || node.Size > limit || offset < 0 ||
			offset > size-dataOffset || node.Size > size-dataOffset-offset ||
			node.Integrity.Algorithm != "SHA256" || !digestPattern.MatchString(node.Integrity.Hash) {
			return nil, fmt.Errorf("官方ASAR依赖边界或摘要无效: %s", name)
		}
		body := make([]byte, node.Size)
		if _, e = reader.ReadAt(body, dataOffset+offset); e != nil {
			return nil, e
		}
		hash := sha256.Sum256(body)
		if hex.EncodeToString(hash[:]) != node.Integrity.Hash {
			return nil, fmt.Errorf("官方ASAR依赖摘要不匹配: %s", name)
		}
		result[name] = body
	}
	nativeName := "node_modules/@napi-rs/" + platform + "/keyring.win32-" + arch + "-msvc.node"
	native, e := asarMember(tree, nativeName)
	if e != nil || !native.Unpacked || native.Size <= 0 || native.Size > 32<<20 {
		return nil, errors.New("官方ASAR未声明对应平台的外部keyring模块")
	}
	var keyring, platformPackage struct{ Name, Version string }
	if json.Unmarshal(result["node_modules/@napi-rs/keyring/package.json"], &keyring) != nil ||
		json.Unmarshal(result["node_modules/@napi-rs/"+platform+"/package.json"], &platformPackage) != nil ||
		keyring.Name != "@napi-rs/keyring" || platformPackage.Name != "@napi-rs/"+platform ||
		!versionPattern.MatchString(keyring.Version) || keyring.Version != platformPackage.Version {
		return nil, errors.New("官方keyring依赖包身份或版本不一致")
	}
	return result, nil
}

// Validate every selected 7zip member before extracting the two exact paths.
// The surrounding archive is already pinned to the publisher's SHA256.
func validateKeyringSelection(listing, archive string, limits map[string]int64) error {
	seen := map[string]bool{}
	var name string
	var size int64 = -1
	var bad bool
	finish := func() error {
		if name == "" || name == archive {
			return nil
		}
		limit, ok := limits[name]
		if !ok || seen[name] || bad || size <= 0 || size > limit {
			return errors.New("官方ZCode keyring提取清单包含未知、链接或超限成员")
		}
		seen[name] = true
		return nil
	}
	for _, line := range strings.Split(listing, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "Path = ") {
			if e := finish(); e != nil {
				return e
			}
			name = strings.TrimPrefix(line, "Path = ")
			if name != archive {
				name = strings.ReplaceAll(name, "\\", "/")
			}
			size, bad = -1, false
		} else if strings.HasPrefix(line, "Size = ") {
			n, e := strconv.ParseInt(strings.TrimPrefix(line, "Size = "), 10, 64)
			size = n
			bad = bad || e != nil
		} else if strings.HasPrefix(line, "Symbolic Link =") || strings.HasPrefix(line, "Hard Link =") ||
			(strings.HasPrefix(line, "Attributes = ") && strings.Contains(strings.TrimPrefix(line, "Attributes = "), "D")) {
			bad = true
		}
	}
	if e := finish(); e != nil {
		return e
	}
	if len(seen) != len(limits) {
		return errors.New("官方ZCode归档缺少keyring资源")
	}
	return nil
}

func extractZCodeKeyring(ctx context.Context, seven, payload, program, stage string, env []string, arch string) error {
	if arch != "x64" && arch != "arm64" {
		return errors.New("ZCode keyring平台未登记")
	}
	archivePath := "resources/app.asar"
	nativePath := "resources/app.asar.unpacked/node_modules/@napi-rs/keyring-win32-" + arch + "-msvc/keyring.win32-" + arch + "-msvc.node"
	listing, e := run(ctx, []string{seven, "l", payload, archivePath, nativePath, "-slt"}, stage, env)
	if e != nil {
		return e
	}
	if e = validateKeyringSelection(listing, payload, map[string]int64{archivePath: 512 << 20, nativePath: 32 << 20}); e != nil {
		return e
	}
	scratch := filepath.Join(stage, "keyring-source")
	if e = os.MkdirAll(scratch, 0700); e != nil {
		return e
	}
	out, e := run(ctx, []string{seven, "x", payload, archivePath, nativePath, "-o" + scratch, "-y"}, stage, env)
	if e != nil {
		return fmt.Errorf("提取官方keyring资源失败: %w: %s", e, tail(out, 1000))
	}
	from, e := child(scratch, archivePath)
	if e != nil {
		return e
	}
	f, e := os.Open(from)
	if e != nil {
		return e
	}
	defer f.Close()
	info, e := f.Stat()
	if e != nil || !info.Mode().IsRegular() || info.Size() > 512<<20 {
		return errors.New("官方ASAR文件无效或超限")
	}
	files, e := keyringFiles(f, info.Size(), arch)
	if e != nil {
		return e
	}
	base := filepath.Join(program, "resources", "glm")
	for name, body := range files {
		if e = copyRegular(base, name, 0600, bytes.NewReader(body), int64(len(body))); e != nil {
			return e
		}
	}
	from, e = child(scratch, nativePath)
	if e != nil {
		return e
	}
	info, e = os.Stat(from)
	if e != nil || !info.Mode().IsRegular() || info.Size() <= 0 || info.Size() > 32<<20 {
		return errors.New("官方keyring原生模块无效或超限")
	}
	// The official installer signs .node after ASAR construction, changing its
	// size/hash. For unpacked bytes use the SAME verified parent installer, not
	// stale pre-sign ASAR integrity. The final immutable release hashes them.
	return copyFile(from, filepath.Join(base, "node_modules", "@napi-rs", "keyring-win32-"+arch+"-msvc", filepath.Base(from)), 0600)
}
