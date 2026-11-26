package components

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha512"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

type transportFunc func(*http.Request) (*http.Response, error)

func (f transportFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func archive(t *testing.T, entries []*tar.Header, bodies []string) []byte {
	t.Helper()
	var out bytes.Buffer
	g := gzip.NewWriter(&out)
	w := tar.NewWriter(g)
	for i, h := range entries {
		if e := w.WriteHeader(h); e != nil {
			t.Fatal(e)
		}
		if _, e := io.WriteString(w, bodies[i]); e != nil {
			t.Fatal(e)
		}
	}
	if e := w.Close(); e != nil {
		t.Fatal(e)
	}
	if e := g.Close(); e != nil {
		t.Fatal(e)
	}
	return out.Bytes()
}

func TestArchivesRejectEscapeAndLinks(t *testing.T) {
	for _, h := range []*tar.Header{{Name: "package/../../escaped", Mode: 0644, Typeflag: tar.TypeReg, Size: 1}, {Name: "package/link", Typeflag: tar.TypeSymlink, Linkname: "../../escaped"}, {Name: "package/CON.txt", Mode: 0644, Typeflag: tar.TypeReg, Size: 1}} {
		t.Run(h.Name, func(t *testing.T) {
			base := t.TempDir()
			path := filepath.Join(base, "archive.tgz")
			body := ""
			if h.Typeflag == tar.TypeReg {
				body = "x"
			}
			if e := os.WriteFile(path, archive(t, []*tar.Header{h}, []string{body}), 0600); e != nil {
				t.Fatal(e)
			}
			if e := extractTarGz(path, filepath.Join(base, "target"), 1, nil); e == nil {
				t.Fatal("unsafe archive accepted")
			}
			if _, e := os.Stat(filepath.Join(base, "escaped")); !os.IsNotExist(e) {
				t.Fatal("archive escaped")
			}
		})
	}
}

func TestManagedReleaseLifecycleAndTampering(t *testing.T) {
	root := filepath.Join(t.TempDir(), "components")
	m := New(root)
	m.OperationID = "00000000-0000-4000-8000-000000000001"
	filename := "codex"
	nodePlatform := runtime.GOOS
	if nodePlatform == "windows" {
		nodePlatform = "win32"
		filename += ".exe"
	}
	arch := map[string]string{"amd64": "x64", "arm64": "arm64"}[runtime.GOARCH]
	payload := "fake native binary"
	blob := archive(t, []*tar.Header{{Name: "package/vendor/target/codex/" + filename, Typeflag: tar.TypeReg, Mode: 0755, Size: int64(len(payload))}}, []string{payload})
	sum := sha512.Sum512(blob)
	sri := "sha512-" + base64.StdEncoding.EncodeToString(sum[:])
	version := "1.2.3"
	nativeVersion := version + "-" + nodePlatform + "-" + arch
	m.Client = &http.Client{Transport: transportFunc(func(r *http.Request) (*http.Response, error) {
		var raw []byte
		if strings.HasSuffix(r.URL.Path, ".tgz") {
			raw = blob
		} else {
			v := version
			if strings.HasSuffix(r.URL.Path, nativeVersion) {
				v = nativeVersion
			}
			raw, _ = json.Marshal(map[string]any{"name": "@openai/codex", "version": v, "optionalDependencies": map[string]string{"@openai/codex-" + nodePlatform + "-" + arch: "npm:@openai/codex@" + nativeVersion}, "dist": map[string]string{"tarball": "https://registry.npmjs.org/@openai/codex/-/test.tgz", "integrity": sri}})
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(raw)), Header: make(http.Header)}, nil
	})}
	r, e := m.Prepare(context.Background(), "codex", version)
	if e != nil {
		t.Fatal(e)
	}
	if len(r.Entry) != 1 || !strings.HasPrefix(r.Entry[0], r.Path) {
		t.Fatal("entry not rebound to final release")
	}
	if _, e = m.Verify(context.Background(), r.ID); e != nil {
		t.Fatal(e)
	}
	firstOperation := m.OperationID
	m.OperationID = "00000000-0000-4000-8000-000000000002"
	second, e := m.Prepare(context.Background(), "codex", version)
	if e != nil || r.ID != second.ID {
		t.Fatalf("immutable preparation not reused: %v", e)
	}
	prior, e := m.OperationRelease(firstOperation)
	if e != nil || prior.ID != r.ID || prior.OperationID != firstOperation {
		t.Fatalf("reusing a release lost original operation provenance: %+v %v", prior, e)
	}
	if _, e := m.OperationRelease("../../outside"); e == nil {
		t.Fatal("operation receipt path accepted traversal")
	}
	neighbor := filepath.Join(filepath.Dir(root), "account-keep")
	if e = os.WriteFile(neighbor, []byte("retained"), 0600); e != nil {
		t.Fatal(e)
	}
	if e = os.WriteFile(r.Entry[0], []byte("tampered"), 0600); e != nil {
		t.Fatal(e)
	}
	if _, e = m.Verify(context.Background(), r.ID); e == nil {
		t.Fatal("tampered release accepted")
	}
	bad := r
	bad.Path = filepath.Dir(root)
	if e = writeJSON(filepath.Join(root, ".records", r.ID+".json"), bad); e != nil {
		t.Fatal(e)
	}
	if e = m.Remove(context.Background(), r.ID); e == nil {
		t.Fatal("forged delete path accepted")
	}
	if e = writeJSON(filepath.Join(root, ".records", r.ID+".json"), r); e != nil {
		t.Fatal(e)
	}
	if e = m.Remove(context.Background(), r.ID); e != nil {
		t.Fatal(e)
	}
	if _, e = os.Stat(neighbor); e != nil {
		t.Fatal("uninstall altered unrelated account file")
	}
	saved, e := m.Get(r.ID)
	if e != nil || saved.State != "REMOVED" {
		t.Fatal("uninstall did not retain history")
	}
}

func TestDiagnosticEnvironmentDoesNotInheritCredentials(t *testing.T) {
	t.Setenv("OPENAI_API_KEY", "not-a-real-key")
	t.Setenv("DEEPSEEK_API_KEY", "not-a-real-key")
	t.Setenv("ANTHROPIC_AUTH_TOKEN", "not-a-real-key")
	t.Setenv("NPM_TOKEN", "not-a-real-key")
	env := environment(t.TempDir(), nil)
	joined := strings.Join(env, "\n")
	for _, name := range []string{"OPENAI_API_KEY=", "DEEPSEEK_API_KEY=", "ANTHROPIC_AUTH_TOKEN=", "NPM_TOKEN="} {
		if strings.Contains(joined, name) {
			t.Fatalf("credential inherited: %s", name)
		}
	}
}
