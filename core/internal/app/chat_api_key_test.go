package app

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestChatAPIKeyWritesRequireControlOrigin(t *testing.T) {
	a := partnerFixture(t)
	for _, path := range []string{"/api/v1/chat-api-key", "/api/v1/chat-api-key/reuse", "/api/v1/chat-api-key/select"} {
		req := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:18765"+path, strings.NewReader(`{}`))
		req.Header.Set("Authorization", "Bearer "+a.Viewer)
		got := httptest.NewRecorder()
		a.BindPort("18765").ServeHTTP(got, req)
		if got.Code != 403 {
			t.Fatalf("%s accepted viewer POST: %v", path, got.Code)
		}
		a.UIOrigin = "http://127.0.0.1:18766"
		a.UIToken = "test-token"
		req = httptest.NewRequest(http.MethodPost, a.UIOrigin+"/s/"+a.UIToken+path, strings.NewReader(`{}`))
		req.Header.Set("Origin", "https://not-local.invalid")
		got = httptest.NewRecorder()
		a.UIHandler(got, req)
		if got.Code != 403 {
			t.Fatalf("%s accepted remote origin: %v", path, got.Code)
		}
	}
}

func TestChatAPIKeyExactSharingAndRotation(t *testing.T) {
	a := partnerFixture(t)
	vault := map[string]string{}
	call := func(ctx context.Context, method string, params, out any) error {
		p := params.(map[string]any)
		dest := str(p["account_id"]) + ":" + str(p["version"])
		source := str(p["source_account_id"]) + ":" + str(p["source_version"])
		switch method {
		case "partner.secret.legacy-ready":
			*out.(*map[string]any) = map[string]any{"available": false}
		case "partner.secret.valid-chat":
			*out.(*map[string]any) = map[string]any{"available": vault[dest] != ""}
		case "partner.secret.same":
			*out.(*map[string]any) = map[string]any{"matches": vault[dest] != "" && vault[dest] == vault[source]}
		case "partner.secret.matches":
			*out.(*map[string]any) = map[string]any{"matches": vault[dest] == str(p["key"])}
		case "partner.secret.store":
			vault[dest] = str(p["key"])
		case "partner.secret.reuse":
			if vault[source] == "" {
				return errors.New("missing fixture")
			}
			vault[dest] = vault[source]
		default:
			return errors.New("unexpected fixture method")
		}
		return nil
	}
	seed := func(partner, provider, secret string) (string, string) {
		id, version := ID(), ""
		item := map[string]any{"id": id, "kind": "api", "api_provider": provider, "partner_id": partner, "state": "NOT_CONFIGURED"}
		if secret != "" {
			version = ID()
			vault[id+":"+version] = secret
			item["secret_version"], item["state"], item["key_ready"] = version, "VERIFIED", true
		}
		putPartnerFixture(t, a, "account", item)
		return id, version
	}
	first, previous := seed("deepseek", "deepseek", "fixture-only-1")
	second, otherPrevious := seed("kimi", "deepseek", "fixture-only-1")
	qwen, qv := seed("qwen", "qwen", "fixture-only-other-vendor")
	mutate := func(action, provider, field, value string) (map[string]any, string) {
		id := ID()
		r, e := a.mutateChatAPIKey(context.Background(), action, map[string]any{"id": id, "provider": provider, field: value}, call)
		if e != nil {
			t.Fatalf("%s: %v", action, e)
		}
		return r, id
	}
	same, _ := mutate("save", "deepseek", "key", "fixture-only-1")
	if number(same["accounts_updated"]) != 0 || readPartnerObject(t, a, "account", first)["secret_version"] != previous || readPartnerObject(t, a, "account", second)["secret_version"] != otherPrevious {
		t.Fatal("equivalent key changed a verified Work reference")
	}
	independent, iv := seed("codex", "deepseek", "fixture-independent")
	target, _ := seed("claude", "deepseek", "")
	mutate("select", "deepseek", "source_account_id", first)
	reused, _ := mutate("reuse", "deepseek", "account_id", target)
	if number(reused["accounts_updated"]) != 1 || vault[target+":"+previous] != "fixture-only-1" {
		t.Fatal("Work did not reuse exact protected key")
	}
	changed, id := mutate("save", "deepseek", "key", "fixture-rotated-private")
	if number(changed["accounts_updated"]) != 3 || readPartnerObject(t, a, "account", independent)["secret_version"] != iv || readPartnerObject(t, a, "account", qwen)["secret_version"] != qv {
		t.Fatal("rotation missed same-key group or changed independent credentials")
	}
	for _, id2 := range []string{first, second, target} {
		if got := readPartnerObject(t, a, "account", id2)["secret_version"]; got != id {
			t.Fatal("shared reference did not rotate")
		}
	}
	if _, e := a.mutateChatAPIKey(context.Background(), "save", map[string]any{"id": id, "provider": "deepseek", "key": "fixture-different-replay"}, call); e == nil {
		t.Fatal("same command identity accepted another key")
	}
	if _, e := a.mutateChatAPIKey(context.Background(), "save", map[string]any{"id": id, "provider": "deepseek", "key": "fixture-rotated-private"}, call); e != nil {
		t.Fatal("same command and key did not recover original receipt")
	}
	rows, e := a.query(`SELECT kind,payload_hash,receipt FROM commands WHERE id=?`, id)
	if e != nil || len(rows) != 1 {
		t.Fatalf("missing receipt %v", e)
	}
	raw, _ := json.Marshal(rows)
	if strings.Contains(string(raw), "fixture-rotated-private") || strings.Contains(string(raw), "secret_version") {
		t.Fatal("private credential escaped to receipt")
	}
	if _, e = a.mutateChatAPIKey(context.Background(), "reuse", map[string]any{"id": ID(), "provider": "deepseek", "account_id": independent}, call); e == nil {
		t.Fatal("reuse overwrote independent Work key")
	}
}
