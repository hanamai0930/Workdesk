package components

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func keyringFixture(t *testing.T, change func(*asarNode)) []byte {
	t.Helper()
	root := asarNode{Files: map[string]asarNode{}}
	var data bytes.Buffer
	put := func(name string, node asarNode) {
		parts := strings.Split(name, "/")
		cursor := &root
		for _, part := range parts[:len(parts)-1] {
			child := cursor.Files[part]
			if child.Files == nil {
				child.Files = map[string]asarNode{}
				cursor.Files[part] = child
			}
			cursor = &child
		}
		cursor.Files[parts[len(parts)-1]] = node
	}
	for _, file := range []struct{ name, body string }{
		{"node_modules/@napi-rs/keyring/index.js", "module.exports = nativeBinding"},
		{"node_modules/@napi-rs/keyring/keytar.js", "module.exports = {}"},
		{"node_modules/@napi-rs/keyring/LICENSE", "MIT"},
		{"node_modules/@napi-rs/keyring/package.json", `{"name":"@napi-rs/keyring","version":"2.1.2"}`},
		{"node_modules/@napi-rs/keyring-win32-x64-msvc/package.json", `{"name":"@napi-rs/keyring-win32-x64-msvc","version":"2.1.2"}`},
		{"node_modules/@napi-rs/keyring-win32-x64-msvc/LICENSE.third-party.md", "third-party notices"},
	} {
		hash := sha256.Sum256([]byte(file.body))
		node := asarNode{Size: int64(len(file.body)), Offset: jsonNumber(data.Len())}
		node.Integrity.Algorithm, node.Integrity.Hash = "SHA256", hex.EncodeToString(hash[:])
		put(file.name, node)
		data.WriteString(file.body)
	}
	put("node_modules/@napi-rs/keyring-win32-x64-msvc/keyring.win32-x64-msvc.node", asarNode{Size: 123, Unpacked: true})
	put("../not-allowlisted", asarNode{Link: "outside"})
	if change != nil {
		change(&root)
	}
	raw, e := json.Marshal(root)
	if e != nil {
		t.Fatal(e)
	}
	padding := (4 - len(raw)%4) % 4
	headerSize := 8 + len(raw) + padding
	buf := make([]byte, 16)
	binary.LittleEndian.PutUint32(buf[:4], 4)
	binary.LittleEndian.PutUint32(buf[4:8], uint32(headerSize))
	binary.LittleEndian.PutUint32(buf[8:12], uint32(headerSize-4))
	binary.LittleEndian.PutUint32(buf[12:16], uint32(len(raw)))
	buf = append(buf, raw...)
	buf = append(buf, make([]byte, padding)...)
	return append(buf, data.Bytes()...)
}

func jsonNumber(n int) string {
	raw, _ := json.Marshal(n)
	return string(raw)
}

func alterIndex(root *asarNode, change func(*asarNode)) {
	module := root.Files["node_modules"].Files["@napi-rs"].Files["keyring"]
	index := module.Files["index.js"]
	change(&index)
	module.Files["index.js"] = index
}

func TestKeyringASARReadsOnlyVerifiedOfficialDependencies(t *testing.T) {
	archive := keyringFixture(t, nil)
	files, e := keyringFiles(bytes.NewReader(archive), int64(len(archive)), "x64")
	if e != nil {
		t.Fatal(e)
	}
	if len(files) != 6 || string(files["node_modules/@napi-rs/keyring/LICENSE"]) != "MIT" {
		t.Fatalf("incorrect allowlist recovery: %+v", files)
	}
	if _, present := files["../not-allowlisted"]; present {
		t.Fatal("unrelated Electron member extracted")
	}
}

func TestKeyringASARRejectsUnsafeOrMismatchedMetadata(t *testing.T) {
	for name, change := range map[string]func(*asarNode){
		"out-of-bounds":   func(root *asarNode) { alterIndex(root, func(n *asarNode) { n.Offset = "9223372036854775807" }) },
		"negative-offset": func(root *asarNode) { alterIndex(root, func(n *asarNode) { n.Offset = "-1" }) },
		"oversize":        func(root *asarNode) { alterIndex(root, func(n *asarNode) { n.Size = 2 << 20 }) },
		"unpacked-js":     func(root *asarNode) { alterIndex(root, func(n *asarNode) { n.Unpacked = true }) },
		"link":            func(root *asarNode) { alterIndex(root, func(n *asarNode) { n.Link = "../outside" }) },
		"bad-hash": func(root *asarNode) {
			alterIndex(root, func(n *asarNode) { n.Integrity.Hash = strings.Repeat("0", 64) })
		},
		"missing-js": func(root *asarNode) {
			delete(root.Files["node_modules"].Files["@napi-rs"].Files["keyring"].Files, "index.js")
		},
		"ancestor-link": func(root *asarNode) {
			n := root.Files["node_modules"]
			n.Link = "../other"
			root.Files["node_modules"] = n
		},
		"missing-native": func(root *asarNode) {
			delete(root.Files["node_modules"].Files["@napi-rs"].Files["keyring-win32-x64-msvc"].Files, "keyring.win32-x64-msvc.node")
		},
	} {
		t.Run(name, func(t *testing.T) {
			archive := keyringFixture(t, change)
			if _, e := keyringFiles(bytes.NewReader(archive), int64(len(archive)), "x64"); e == nil {
				t.Fatal("invalid ASAR accepted")
			}
		})
	}
	archive := keyringFixture(t, nil)
	binary.LittleEndian.PutUint32(archive[4:8], 32<<20)
	if _, e := keyringFiles(bytes.NewReader(archive), int64(len(archive)), "x64"); e == nil {
		t.Fatal("unbounded ASAR header accepted")
	}
	if _, e := keyringFiles(bytes.NewReader([]byte("short")), 5, "x64"); e == nil {
		t.Fatal("truncated ASAR accepted")
	}
}

func TestZCodeKeyringSelectionRequiresExactRegularMembers(t *testing.T) {
	limits := map[string]int64{"resources/app.asar": 512 << 20, "resources/native.node": 32 << 20}
	listing := "Path = archive.7z\nType = 7z\n\nPath = resources\\app.asar\nSize = 100\nAttributes = A\n\nPath = resources\\native.node\nSize = 10\nAttributes = A\n"
	if e := validateKeyringSelection(listing, "archive.7z", limits); e != nil {
		t.Fatal(e)
	}
	for _, bad := range []string{
		strings.Replace(listing, "resources\\native.node", "../escape", 1),
		listing + "Hard Link = target\n",
		strings.Replace(listing, "Size = 10\n", "Size = 9999999999\n", 1),
		strings.Replace(listing, "Attributes = A", "Attributes = D", 1),
		listing + "Path = resources\\native.node\nSize = 10\n",
	} {
		if e := validateKeyringSelection(bad, "archive.7z", limits); e == nil {
			t.Fatal("unsafe outer archive selection accepted")
		}
	}
}

// Optional, zero-network verification against a locally cached official
// payload. Paths are explicit test inputs, never production dependencies.
func TestZCodeKeyringOfficialArchive(t *testing.T) {
	seven, payload := os.Getenv("WORKDESK_TEST_ZCODE_7ZIP"), os.Getenv("WORKDESK_TEST_ZCODE_PAYLOAD")
	if runtime.GOOS != "windows" || seven == "" || payload == "" {
		t.Skip("requires explicit local official ZCode payload and managed 7zip")
	}
	stage := t.TempDir()
	program := filepath.Join(stage, "program")
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if e := extractZCodeKeyring(ctx, seven, payload, program, stage, environment(filepath.Join(stage, "empty-home"), nil), "x64"); e != nil {
		t.Fatal(e)
	}
	count := 0
	if e := filepath.WalkDir(program, func(path string, entry os.DirEntry, e error) error {
		if e != nil {
			return e
		}
		if !entry.IsDir() {
			count++
			if filepath.Base(path) == "app.asar" {
				t.Fatal("Electron ASAR leaked into managed release")
			}
		}
		return nil
	}); e != nil {
		t.Fatal(e)
	}
	if count != 7 {
		t.Fatalf("expected precisely 7 official dependency files, got %d", count)
	}
}
