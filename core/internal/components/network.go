package components

import (
	"context"
	"crypto/sha256"
	"crypto/sha512"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"hash"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type npmMetadata struct {
	Name    string          `json:"name"`
	Version string          `json:"version"`
	License string          `json:"license"`
	Bin     json.RawMessage `json:"bin"`
	Dist    struct {
		Tarball   string `json:"tarball"`
		Integrity string `json:"integrity"`
		Shasum    string `json:"shasum"`
	} `json:"dist"`
	OptionalDependencies map[string]string `json:"optionalDependencies"`
	Engines              map[string]string `json:"engines"`
}

type Source struct {
	URL          string `json:"url"`
	SHA256       string `json:"sha256,omitempty"`
	Integrity    string `json:"integrity,omitempty"`
	Verification string `json:"verification"`
}

func allowedURL(raw string) bool {
	u, e := url.Parse(raw)
	if e != nil || u.Scheme != "https" || u.User != nil || u.Port() != "" {
		return false
	}
	switch strings.ToLower(u.Hostname()) {
	case "registry.npmjs.org", "nodejs.org", "x.ai", "api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com":
		return true
	case "storage.googleapis.com":
		return strings.HasPrefix(u.Path, "/grok-build-public-artifacts/cli/")
	case "code.kimi.com", "cdn.kimi.com":
		return strings.HasPrefix(u.Path, "/kimi-code/")
	}
	return false
}

func defaultClient() *http.Client {
	return &http.Client{Timeout: 15 * time.Minute, CheckRedirect: func(r *http.Request, via []*http.Request) error {
		if len(via) > 8 || !allowedURL(r.URL.String()) {
			return errors.New("官方组件下载跳转越出允许来源")
		}
		return nil
	}}
}

func (m *Manager) get(ctx context.Context, raw string, limit int64) ([]byte, error) {
	if !allowedURL(raw) {
		return nil, errors.New("组件来源不在官方允许清单")
	}
	req, e := http.NewRequestWithContext(ctx, "GET", raw, nil)
	if e != nil {
		return nil, e
	}
	req.Header.Set("User-Agent", "Workdesk-Components/1")
	res, e := m.Client.Do(req)
	if e != nil {
		return nil, fmt.Errorf("官方组件读取失败: %w", e)
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return nil, fmt.Errorf("官方来源返回 HTTP %d", res.StatusCode)
	}
	b, e := io.ReadAll(io.LimitReader(res.Body, limit+1))
	if e != nil {
		return nil, e
	}
	if int64(len(b)) > limit {
		return nil, errors.New("官方元数据超过读取范围")
	}
	return b, nil
}

func (m *Manager) metadata(ctx context.Context, pkg, version string) (npmMetadata, error) {
	if version == "" || version == "latest" {
		version = "latest"
	}
	if !versionPattern.MatchString(version) {
		return npmMetadata{}, errors.New("请选择明确组件版本或latest")
	}
	raw, e := m.get(ctx, "https://registry.npmjs.org/"+pkg+"/"+url.PathEscape(version), 4<<20)
	if e != nil {
		return npmMetadata{}, e
	}
	var meta npmMetadata
	e = json.Unmarshal(raw, &meta)
	if e == nil && (meta.Name != pkg || meta.Version == "" || meta.Dist.Integrity == "" || !allowedURL(meta.Dist.Tarball)) {
		e = errors.New("官方npm元数据身份或完整性缺失")
	}
	return meta, e
}

func integrityHasher(integrity string) (hash.Hash, []byte, error) {
	for _, v := range strings.Fields(integrity) {
		parts := strings.SplitN(v, "-", 2)
		if len(parts) != 2 {
			continue
		}
		expected, e := base64.StdEncoding.DecodeString(parts[1])
		if e != nil {
			continue
		}
		switch parts[0] {
		case "sha512":
			if len(expected) == 64 {
				return sha512.New(), expected, nil
			}
		case "sha256":
			if len(expected) == 32 {
				return sha256.New(), expected, nil
			}
		}
	}
	return nil, nil, errors.New("未取得支持的官方包完整性校验")
}

func fileSHA(path string) (string, error) {
	f, e := os.Open(path)
	if e != nil {
		return "", e
	}
	defer f.Close()
	h := sha256.New()
	_, e = io.Copy(h, f)
	return hex.EncodeToString(h.Sum(nil)), e
}

// download stores source archives separately from releases, enabling reinstall
// without copying program trees. No request is authenticated by this manager.
func (m *Manager) download(ctx context.Context, raw, expectedSHA, integrity string) (string, Source, error) {
	if !allowedURL(raw) {
		return "", Source{}, errors.New("组件下载来源无效")
	}
	key := sha256.Sum256([]byte(raw + expectedSHA + integrity))
	cache := filepath.Join(filepath.Dir(m.Root), "cache", "components", "downloads")
	if e := plain(cache); e != nil {
		return "", Source{}, e
	}
	if e := os.MkdirAll(cache, 0700); e != nil {
		return "", Source{}, e
	}
	target := filepath.Join(cache, hex.EncodeToString(key[:]))
	verify := func(path string) (Source, error) {
		f, e := os.Open(path)
		if e != nil {
			return Source{}, e
		}
		defer f.Close()
		sha := sha256.New()
		writers := []io.Writer{sha}
		var h hash.Hash
		var expected []byte
		if integrity != "" {
			h, expected, e = integrityHasher(integrity)
			if e != nil {
				return Source{}, e
			}
			writers = append(writers, h)
		}
		_, e = io.Copy(io.MultiWriter(writers...), f)
		if e != nil {
			return Source{}, e
		}
		actual := hex.EncodeToString(sha.Sum(nil))
		if expectedSHA != "" && !strings.EqualFold(actual, expectedSHA) {
			return Source{}, errors.New("官方组件SHA256不符")
		}
		if h != nil && !strings.EqualFold(hex.EncodeToString(h.Sum(nil)), hex.EncodeToString(expected)) {
			return Source{}, errors.New("官方npm包完整性不符")
		}
		proof := "HTTPS_SOURCE_AND_LOCAL_SHA256"
		if expectedSHA != "" {
			proof = "PUBLISHED_SHA256"
		}
		if integrity != "" {
			proof = "NPM_REGISTRY_INTEGRITY"
		}
		return Source{raw, actual, integrity, proof}, nil
	}
	if _, e := os.Stat(target); e == nil {
		proof, e := verify(target)
		return target, proof, e
	}
	req, e := http.NewRequestWithContext(ctx, "GET", raw, nil)
	if e != nil {
		return "", Source{}, e
	}
	req.Header.Set("User-Agent", "Workdesk-Components/1")
	res, e := m.Client.Do(req)
	if e != nil {
		return "", Source{}, e
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return "", Source{}, fmt.Errorf("官方组件下载 HTTP %d", res.StatusCode)
	}
	tmp := target + ".part-" + nonce()
	f, e := os.OpenFile(tmp, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return "", Source{}, e
	}
	defer os.Remove(tmp)
	n, e := io.Copy(f, io.LimitReader(res.Body, 2<<30+1))
	closeErr := f.Close()
	if e != nil {
		return "", Source{}, e
	}
	if closeErr != nil {
		return "", Source{}, closeErr
	}
	if n > 2<<30 {
		return "", Source{}, errors.New("组件下载超过2GiB")
	}
	proof, e := verify(tmp)
	if e != nil {
		return "", Source{}, e
	}
	if e = os.Rename(tmp, target); e != nil {
		return "", Source{}, e
	}
	return target, proof, nil
}
