package app

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"workdesk/core/internal/bridge"
	"workdesk/core/internal/components"
	"workdesk/core/internal/storage"
)

// Partner records share the core transaction and receipt ledger. Native history
// remains native; these objects bind it to this instance/project/version.
type partnerProcess struct {
	mu      sync.Mutex
	input   io.WriteCloser
	cancel  context.CancelFunc
	secrets []string
}

func (p *partnerProcess) send(v any) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.input == nil {
		return errors.New("原运行输入尚未就绪")
	}
	if frame, ok := v.(map[string]any); ok && str(frame["action"]) == "login-input" {
		if secret := str(frame["text"]); secret != "" {
			p.secrets = append(p.secrets, secret)
		}
	}
	return json.NewEncoder(p.input).Encode(v)
}
func (p *partnerProcess) redact(text string) string {
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, secret := range p.secrets {
		if secret != "" {
			text = strings.ReplaceAll(text, secret, "[redacted]")
		}
	}
	return text
}
func (p *partnerProcess) redactValue(value any) any {
	switch value := value.(type) {
	case string:
		return p.redact(value)
	case map[string]any:
		for key, item := range value {
			value[key] = p.redactValue(item)
		}
	case []any:
		for i, item := range value {
			value[i] = p.redactValue(item)
		}
	}
	return value
}
func (a *App) componentManager() *components.Manager {
	return components.New(filepath.Join(a.Profile.DevelopmentRoot, "components"))
}
func partnerKind(kind string) bool {
	return strings.HasPrefix(kind, "component.") || strings.HasPrefix(kind, "connection.") || strings.HasPrefix(kind, "account.") || strings.HasPrefix(kind, "partner.")
}
func (a *App) partnerObjects(kind string) ([]map[string]any, error) {
	rows, e := a.query(`SELECT data FROM core_partner_objects WHERE kind=? ORDER BY updated DESC LIMIT 200`, kind)
	if e != nil {
		return nil, e
	}
	out := []map[string]any{}
	for _, r := range rows {
		var m map[string]any
		if e = json.Unmarshal([]byte(str(r["data"])), &m); e != nil {
			return nil, e
		}
		out = append(out, m)
	}
	return out, nil
}
func partnerObject(s *storage.Session, kind, id string) (map[string]any, error) {
	r, e := s.Execute(`SELECT data FROM core_partner_objects WHERE kind=? AND id=?`, []any{kind, id})
	if e != nil {
		return nil, e
	}
	if len(r.Rows) != 1 {
		return nil, errors.New("所选记录不存在")
	}
	var m map[string]any
	e = json.Unmarshal([]byte(str(r.Rows[0][0])), &m)
	return m, e
}
func putPartner(s *storage.Session, kind string, m map[string]any) error {
	m["revision"] = number(m["revision"]) + 1
	m["updated"] = now()
	raw, e := json.Marshal(m)
	if e != nil {
		return e
	}
	_, e = s.Execute(`INSERT INTO core_partner_objects VALUES(?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET revision=excluded.revision,data=excluded.data,updated=excluded.updated`, []any{kind, str(m["id"]), m["revision"], string(raw), m["updated"]})
	return e
}
func (a *App) savePartner(kind string, m map[string]any) error {
	return a.write(func(s *storage.Session) error {
		if kind == "run" {
			return a.putPartnerRun(s, m)
		}
		return putPartner(s, kind, m)
	})
}

func partnerAuthOptions(partner string, value any) (map[string]any, error) {
	allowed := map[string]map[string][]string{
		"codex":  {"auth_method": {"chatgpt", "chatgptDeviceCode"}},
		"qwen":   {"auth_method": {"qwen-oauth"}},
		"gemini": {"auth_method": {"oauth-personal"}},
		"claude": {"auth_method": {"claudeai", "console", "sso"}},
		"grok":   {"auth_method": {"device_auth", "oauth"}},
		"kimi":   {"region": {"mainland-cn", "global"}},
		"glm":    {"provider": {"bigmodel", "zai"}},
	}
	options, ok := value.(map[string]any)
	if !ok {
		return nil, errors.New("官方登录选项须为站点或登录方式")
	}
	out := map[string]any{}
	for key, value := range options {
		accepted := false
		for _, option := range allowed[partner][key] {
			if text, ok := value.(string); ok && text == option {
				accepted = true
			}
		}
		if !accepted {
			return nil, errors.New("所选伙伴不支持该官方站点或登录方式")
		}
		out[key] = value
	}
	return out, nil
}

func applyPartnerAuthOptions(account, options map[string]any) {
	previous, _ := account["auth_options"].(map[string]any)
	siteKey := ""
	if account["partner_id"] == "kimi" {
		siteKey = "region"
	}
	if account["partner_id"] == "glm" {
		siteKey = "provider"
	}
	before, after := str(previous[siteKey]), str(options[siteKey])
	if account["partner_id"] == "glm" {
		if before == "" {
			before = "zai"
		}
		if after == "" {
			after = "zai"
		}
	}
	if siteKey != "" && before != after {
		account["auth_state"], account["state"] = "UNKNOWN", "WAITING_LOGIN"
		account["authentication_context_changed"] = true
	}
	account["auth_options"] = options
}

// Progress belongs to the original attempt; authentication remains an
// independently observed official fact, including while a web login stays open.
func (a *App) putPartnerRun(s *storage.Session, run map[string]any) error {
	if err := accountPartnerBudget(s, run); err != nil {
		return err
	}
	mode := str(run["mode"])
	if (mode == "login" || mode == "inspect") && str(run["account_id"]) != "" {
		account, err := partnerObject(s, "account", str(run["account_id"]))
		if err != nil {
			return err
		}
		if str(account["kind"]) == "official_login" && str(account["partner_id"]) == str(run["partner_id"]) {
			if mode == "login" && (str(account["login_run_id"]) == "" || account["login_run_id"] == run["id"]) {
				account["login_run_id"], account["login_release_id"], account["login_state"] = run["id"], run["release_id"], run["state"]
				account["login_updated"] = now()
				if str(account["state"]) != "AUTHENTICATED" && str(account["state"]) != "DISCONNECTED" && str(account["auth_state"]) != "UNAUTHENTICATED" {
					switch str(run["state"]) {
					case "QUEUED", "RUNNING":
						account["state"] = "WAITING_LOGIN"
					case "WAITING_INPUT", "WAITING_APPROVAL":
						account["state"] = "WAITING_USER"
					default:
						account["state"] = "LOGIN_NOT_CONFIRMED"
					}
				}
			} else if mode == "inspect" {
				account["inspection_run_id"], account["inspection_state"] = run["id"], run["state"]
			}
			if err = putPartner(s, "account", account); err != nil {
				return err
			}
		}
	}
	return putPartner(s, "run", run)
}

func (a *App) observePartnerAuth(s *storage.Session, run, event map[string]any) error {
	if str(run["account_id"]) == "" {
		return nil
	}
	account, err := partnerObject(s, "account", str(run["account_id"]))
	if err != nil {
		return err
	}
	if str(account["kind"]) != "official_login" || str(account["partner_id"]) != str(run["partner_id"]) {
		return nil
	}
	state := "UNKNOWN"
	verification, scope := str(event["verification"]), str(event["fact_scope"])
	if str(event["run_id"]) == str(run["id"]) && str(event["partner_id"]) == str(run["partner_id"]) {
		if scope == "OFFICIAL_ACCOUNT_RECEIPT" {
			if str(event["auth_state"]) == "AUTHENTICATED" && event["authenticated"] == true {
				state = "AUTHENTICATED"
			}
			if str(event["auth_state"]) == "UNAUTHENTICATED" && event["authenticated"] == false {
				state = "UNAUTHENTICATED"
			}
		} else if event["authenticated"] == true && (verification == "OFFICIAL_STATUS" || verification == "OFFICIAL_AUTHENTICATE_RECEIPT" || verification == "OFFICIAL_ACCOUNT_CATALOG") {
			state = "AUTHENTICATED"
		}
	}
	observation := map[string]any{"auth_state": state, "run_id": run["id"], "release_id": run["release_id"], "at": now(), "verification": verification, "fact_scope": scope}
	account["auth_observation"], run["auth_observation"] = observation, observation
	if state != "UNKNOWN" && str(account["state"]) != "DISCONNECTED" {
		account["auth_state"], run["auth_state"] = state, state
		if state == "AUTHENTICATED" {
			account["state"], account["authenticated_run"], account["authenticated_at"], account["authenticated_release_id"] = state, run["id"], now(), run["release_id"]
			account["authenticated_options"] = run["auth_options"]
			delete(account, "authentication_context_changed")
		} else {
			account["state"] = "LOGIN_REQUIRED"
		}
	} else if str(account["auth_state"]) == "" {
		account["auth_state"] = "UNKNOWN"
	}
	return putPartner(s, "account", account)
}

func (a *App) currentAccountLogin(s *storage.Session, accountID, releaseID string) (map[string]any, error) {
	rows, err := s.Execute(`SELECT data FROM core_partner_objects WHERE kind='run' AND json_extract(data,'$.account_id')=? AND json_extract(data,'$.release_id')=? AND json_extract(data,'$.mode')='login' ORDER BY updated DESC`, []any{accountID, releaseID})
	if err != nil {
		return nil, err
	}
	for _, row := range rows.Rows {
		var run map[string]any
		if err = json.Unmarshal([]byte(str(row[0])), &run); err != nil {
			return nil, err
		}
		a.mu.Lock()
		active := a.partnerProcesses[str(run["id"])] != nil
		a.mu.Unlock()
		if active || (str(run["state"]) == "QUEUED" && run["process_instance"] == a.Process && !terminalPartner(str(run["state"]))) {
			return run, nil
		}
	}
	return nil, nil
}
func terminalPartner(state string) bool {
	return state == "COMPLETED" || state == "FAILED" || state == "CANCELLED" || state == "STOPPED_RUNTIME" || state == "FAILED_NO_SEND"
}

// A received native result can be accepted after local ownership is proved,
// but neither another session's result nor a user stop is a success receipt.
func receivedPartnerTurnResult(run map[string]any) (map[string]any, error) {
	if str(run["mode"]) != "run" || run["stop_requested"] == true || str(run["state"]) == "CANCELLED" || str(run["state"]) == "STOP_REQUESTED" {
		return nil, errors.New("本次运行有停止意图或不属于官方工作回合，不能接收为成功")
	}
	result, ok := run["result"].(map[string]any)
	if !ok || str(result["type"]) != "result" || str(result["completion_scope"]) != "OFFICIAL_NATIVE_TURN" || result["receipt_unknown"] == true {
		return nil, errors.New("没有可接受的官方回合结果；保留未知，不重新发送")
	}
	for _, key := range []string{"run_id", "partner_id", "session_id"} {
		expected := str(run[key])
		if key == "run_id" {
			expected = str(run["id"])
		}
		if expected == "" || str(result[key]) != expected {
			return nil, errors.New("保存的官方结果与原运行、伙伴或会话身份不符")
		}
	}
	return result, nil
}

// A login result has a different receipt contract from a model turn. It can
// repair a falsely orphaned login only with proved ownership and exact official
// authentication. Closing local tracking for cutover is not a user stop.
func receivedPartnerLoginResult(run map[string]any) error {
	state := str(run["state"])
	closed := state == "STOPPED_RUNTIME" && run["explicit_local_tracking_closed"] == true
	if str(run["mode"]) != "login" || run["stop_requested"] == true || (state != "ORPHANED_UNKNOWN" && state != "STOP_UNKNOWN" && !closed) {
		return errors.New("本次运行不是可核对的原官方登录，或已有停止意图")
	}
	result, _ := run["result"].(map[string]any)
	observation, _ := run["auth_observation"].(map[string]any)
	if result["type"] != "result" || result["fact_scope"] != "OFFICIAL_ACCOUNT_RECEIPT" || result["auth_state"] != "AUTHENTICATED" ||
		result["authenticated"] != true || result["receipt_unknown"] == true || str(run["id"]) == "" ||
		str(result["run_id"]) != str(run["id"]) || str(result["partner_id"]) != str(run["partner_id"]) ||
		str(run["partner_id"]) == "" || str(run["account_id"]) == "" || str(run["release_id"]) == "" ||
		observation["auth_state"] != "AUTHENTICATED" || observation["fact_scope"] != "OFFICIAL_ACCOUNT_RECEIPT" ||
		str(observation["run_id"]) != str(run["id"]) || str(observation["release_id"]) != str(run["release_id"]) {
		return errors.New("没有与此登录匹配的官方认证结果，保留原状态")
	}
	return nil
}

// Both normal shutdown and an explicit acceptance use one transaction-bound
// verification update. An old credential result never verifies a newer Key.
func (a *App) completePartnerRun(s *storage.Session, run map[string]any, acceptance string) error {
	if run["local_exit_confirmed"] != true {
		return errors.New("未取得本地进程树退出证明")
	}
	if str(run["mode"]) == "run" {
		if _, err := receivedPartnerTurnResult(run); err != nil {
			return err
		}
		run["remote_state"] = "OFFICIAL_NATIVE_TURN_COMPLETED"
	}
	run["state"] = "COMPLETED"
	run["result_acceptance"] = acceptance
	if pending, ok := run["pending_requests"].([]any); ok && len(pending) > 0 {
		run["requests_closed_by_native_completion"] = pending
		run["pending_requests"] = []any{}
	}
	delete(run, "error")
	if str(run["mode"]) != "run" {
		return nil
	}
	connection, _ := run["connection_snapshot"].(map[string]any)
	account, err := partnerObject(s, "account", str(run["account_id"]))
	if err != nil {
		return err
	}
	if str(account["partner_id"]) != str(run["partner_id"]) || str(account["state"]) == "DISCONNECTED" {
		return nil
	}
	if str(connection["route"]) == "api" {
		version := str(run["secret_version"])
		provider := accountAPIProvider(account)
		if provider == "" || provider != connectionAPIProvider(connection) || version == "" || str(account["secret_version"]) != version {
			return nil
		}
		account["state"] = "VERIFIED"
		account["verified_run"] = run["id"]
		account["verified_at"] = now()
		account["verified_version"] = version
		account["verified_secret_version"] = version
		account["verified_api_provider"] = provider
		if err = putPartner(s, "account", account); err != nil {
			return err
		}
	}
	current, err := partnerObject(s, "connection", str(run["connection_id"]))
	if err != nil {
		return err
	}
	revisionMatches := number(current["revision"]) == number(run["connection_revision"])
	// A prior implementation already verified this exact run and incremented
	// the connection revision, before recording its credential version. Reuse
	// that same result only while its saved combination is still unchanged.
	if !revisionMatches && str(current["state"]) == "VERIFIED" && current["verified_run"] == run["id"] && current["model"] == connection["model"] && current["route"] == connection["route"] && (str(current["route"]) != "api" || connectionAPIProvider(current) == connectionAPIProvider(connection)) {
		left, leftErr := json.Marshal(current["options"])
		right, rightErr := json.Marshal(connection["options"])
		revisionMatches = leftErr == nil && rightErr == nil && string(left) == string(right)
	}
	if !revisionMatches || str(current["account_id"]) != str(run["account_id"]) || str(current["partner_id"]) != str(run["partner_id"]) || str(current["release_id"]) != str(run["release_id"]) {
		return nil
	}
	if str(current["route"]) == "api" && connectionAPIProvider(current) != connectionAPIProvider(connection) {
		return nil
	}
	current["state"] = "VERIFIED"
	current["verified_run"] = run["id"]
	current["verified_at"] = now()
	if str(connection["route"]) == "api" {
		current["verified_secret_version"] = run["secret_version"]
		current["verified_api_provider"] = connectionAPIProvider(connection)
	}
	// Authentication/verification observations do not change the selected
	// connection configuration. Queued cooperation roles retain that revision.
	current["updated"] = now()
	raw, err := json.Marshal(current)
	if err != nil {
		return err
	}
	_, err = s.Execute(`UPDATE core_partner_objects SET data=?,updated=? WHERE kind='connection' AND id=?`, []any{string(raw), now(), current["id"]})
	return err
}

func (a *App) partnerBusy() bool {
	a.mu.Lock()
	active := len(a.partnerProcesses) > 0
	a.mu.Unlock()
	if active {
		return true
	}
	for _, kind := range []string{"operation", "run"} {
		rows, e := a.partnerObjects(kind)
		if e != nil {
			schema, _ := a.DB.Schema()
			return schema == "3"
		}
		for _, r := range rows {
			if !terminalPartner(str(r["state"])) {
				return true
			}
		}
	}
	return false
}
func (a *App) recoverPartners() error {
	schema, e := a.DB.Schema()
	if e != nil || schema != "3" {
		return e
	}
	return a.write(func(s *storage.Session) error {
		r, e := s.Execute(`SELECT kind,data FROM core_partner_objects WHERE kind IN ('run','operation')`, nil)
		if e != nil {
			return e
		}
		for _, row := range r.Rows {
			var m map[string]any
			if e = json.Unmarshal([]byte(str(row[1])), &m); e != nil {
				return e
			}
			if !terminalPartner(str(m["state"])) {
				m["state"] = "ORPHANED_UNKNOWN"
				m["error"] = "重启后须核对原运行或组件操作；未自动重发"
				if str(row[0]) == "run" {
					e = a.putPartnerRun(s, m)
				} else {
					e = putPartner(s, str(row[0]), m)
				}
				if e != nil {
					return e
				}
			}
		}
		return nil
	})
}
func (a *App) partnersView(path string) (any, error) {
	var apiProviders []map[string]any
	var catalogHash string
	if path == "/api/v2/partners" {
		var err error
		apiProviders, catalogHash, err = a.partnerAPICatalog()
		if err != nil {
			return nil, err
		}
	}
	schema, _ := a.DB.Schema()
	if schema != "3" {
		return map[string]any{"partners": components.Catalog(), "api_providers": apiProviders, "api_catalog_sha256": catalogHash, "components": []any{}, "accounts": []any{}, "connections": []any{}, "operations": []any{}, "runs": []any{}, "migration_required": true}, nil
	}
	kinds := map[string]string{"/api/v2/accounts": "account", "/api/v2/connections": "connection", "/api/v2/component-operations": "operation", "/api/v2/partner-runs": "run"}
	if k, ok := kinds[path]; ok {
		rows, e := a.partnerObjects(k)
		if e != nil {
			return nil, e
		}
		if k == "account" {
			e = a.decoratePartnerAccounts(rows, nil)
		}
		if k == "run" || k == "connection" {
			accounts, err := a.partnerObjects("account")
			if err != nil {
				return nil, err
			}
			if k == "run" {
				e = a.decoratePartnerRuns(rows, accounts)
			} else {
				decoratePartnerConnections(rows, accounts)
			}
		}
		key := map[string]string{"account": "accounts", "connection": "connections", "operation": "operations", "run": "runs"}[k]
		return map[string]any{key: rows}, e
	}
	releases, e := a.componentManager().List()
	if e != nil {
		return nil, e
	}
	visibleReleases := []map[string]any{}
	policies, e := a.partnerObjects("release-policy")
	if e != nil {
		return nil, e
	}
	for _, r := range releases {
		raw, _ := json.Marshal(r)
		var m map[string]any
		_ = json.Unmarshal(raw, &m)
		for _, policy := range policies {
			if policy["id"] == m["id"] && policy["state"] == "DISABLED" {
				m["state"] = "DISABLED"
			}
		}
		visibleReleases = append(visibleReleases, m)
	}
	if path == "/api/v2/components" {
		return map[string]any{"components": visibleReleases, "scope": "managed-versioned-components"}, nil
	}
	accounts, e := a.partnerObjects("account")
	if e != nil {
		return nil, e
	}
	connections, e := a.partnerObjects("connection")
	if e != nil {
		return nil, e
	}
	operations, e := a.partnerObjects("operation")
	if e != nil {
		return nil, e
	}
	runs, e := a.partnerObjects("run")
	if e != nil {
		return nil, e
	}
	if e = a.decoratePartnerRuns(runs, accounts); e != nil {
		return nil, e
	}
	decoratePartnerConnections(connections, accounts)
	byProvider := partnerAPIVerification(apiProviders, accounts, connections)
	verification := "WAITING_HUMAN_KEY"
	if deepseek, ok := byProvider["deepseek"].(map[string]any); ok {
		verification = str(deepseek["state"])
	}
	return map[string]any{"partners": components.Catalog(), "api_providers": apiProviders, "api_catalog_sha256": catalogHash, "api_catalog_source": "FROZEN_IMPLEMENTATION", "components": visibleReleases, "accounts": accounts, "connections": connections, "operations": operations, "runs": runs, "storage_schema": 3, "api_verification": verification, "api_verification_by_provider": byProvider}, nil
}

func releaseEnabled(s *storage.Session, id string) error {
	r, e := s.Execute(`SELECT data FROM core_partner_objects WHERE kind='release-policy' AND id=?`, []any{id})
	if e != nil {
		return e
	}
	if len(r.Rows) > 0 {
		var m map[string]any
		_ = json.Unmarshal([]byte(str(r.Rows[0][0])), &m)
		if m["state"] == "DISABLED" {
			return errors.New("此组件版本已停用，请先明确启用")
		}
	}
	return nil
}
func (a *App) partnerRelease(id string) (map[string]any, error) {
	releases, e := a.componentManager().List()
	if e != nil {
		return nil, e
	}
	for _, r := range releases {
		raw, _ := json.Marshal(r)
		var m map[string]any
		_ = json.Unmarshal(raw, &m)
		if str(m["id"]) == id {
			if str(m["state"]) != "AVAILABLE" {
				return nil, errors.New("组件版本当前不可用，请重新准备准确版本")
			}
			return m, nil
		}
	}
	return nil, errors.New("请先准备所选官方端的准确版本")
}
func validPartner(id string) bool {
	for _, v := range components.Catalog() {
		raw, _ := json.Marshal(v)
		var m map[string]any
		_ = json.Unmarshal(raw, &m)
		if str(m["id"]) == id {
			return true
		}
	}
	return false
}
func (a *App) partnerCommand(command map[string]any) (any, error) {
	return a.partnerCommandWithParent(command, nil)
}
func (a *App) partnerCommandWithParent(command map[string]any, parent map[string]any) (any, error) {
	schema, _ := a.DB.Schema()
	if schema != "3" {
		return nil, errors.New("伙伴操作须先完成显式备份迁移")
	}
	id, kind := str(command["id"]), str(command["kind"])
	p, ok := command["payload"].(map[string]any)
	if !uuidPattern.MatchString(id) || !ok {
		return nil, errors.New("操作须有准确命令ID和对象参数")
	}
	fingerprintPayload := p
	if kind == "account.login-input" {
		fingerprintPayload = map[string]any{"run_id": p["run_id"], "request_id": p["request_id"], "sensitive_input_omitted": true}
	}
	raw, e := json.Marshal(fingerprintPayload)
	if e != nil {
		return nil, e
	}
	hash := sha256.Sum256(raw)
	fingerprint := hex.EncodeToString(hash[:])
	var receipt map[string]any
	var after func()
	fresh := false
	e = a.write(func(s *storage.Session) error {
		prior, e := s.Execute(`SELECT kind,payload_hash,receipt FROM commands WHERE id=?`, []any{id})
		if e != nil {
			return e
		}
		if len(prior.Rows) > 0 {
			r := prior.Rows[0]
			if r[0] != kind || r[1] != fingerprint {
				return errors.New("命令ID已对应其他内容")
			}
			return json.Unmarshal([]byte(str(r[2])), &receipt)
		}
		switch kind {
		case "account.create":
			partner := str(p["partner_id"])
			k := str(p["kind"])
			label := strings.TrimSpace(str(p["label"]))
			if !validPartner(partner) || (k != "official_login" && k != "deepseek_api" && k != "api") || label == "" || len(label) > 120 {
				return errors.New("请选择伙伴、账号类型并填写标签")
			}
			m := map[string]any{"id": ID(), "partner_id": partner, "label": label, "kind": k, "state": "NOT_CONFIGURED", "created": now()}
			if k == "api" || k == "deepseek_api" {
				provider := str(p["api_provider"])
				if k == "deepseek_api" {
					if provider != "" && provider != "deepseek" {
						return errors.New("旧 DeepSeek 凭据不能改为其他 API 厂商")
					}
					provider = "deepseek"
				}
				if _, _, e = a.partnerAPIProvider(provider); e != nil {
					return e
				}
				m["api_provider"] = provider
			} else if str(p["api_provider"]) != "" {
				return errors.New("官方账号路线不接受 API 厂商凭据")
			}
			if options, supplied := p["auth_options"]; supplied {
				if k != "official_login" {
					return errors.New("官方登录选项仅用于账号路线")
				}
				m["auth_options"], e = partnerAuthOptions(partner, options)
				if e != nil {
					return e
				}
			}
			if e = putPartner(s, "account", m); e != nil {
				return e
			}
			receipt = map[string]any{"account_id": m["id"], "state": m["state"]}
		case "account.set-key":
			m, e := partnerObject(s, "account", str(p["account_id"]))
			if e != nil {
				return e
			}
			provider := accountAPIProvider(m)
			if provider == "" {
				return errors.New("所选账号不是有效的 API 凭据")
			}
			if selected := str(p["api_provider"]); selected != "" && selected != provider {
				return errors.New("API 厂商与本次准确账号不符，未保存凭据")
			}
			if _, _, e = a.partnerAPIProvider(provider); e != nil {
				return e
			}
			if m["state"] == "DISCONNECTED" {
				return errors.New("账号引用已断开")
			}
			targets, e := partnerAPICredentialTargets(s, m, p["apply_all_api"] == true)
			if e != nil {
				return e
			}
			for _, target := range targets {
				if e = a.protectAccount(s, str(target["id"])); e != nil {
					return e
				}
			}
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			for _, target := range targets {
				var stored any
				if e = a.Legacy.Call(ctx, "partner.secret.store", map[string]any{"account_id": target["id"], "version": id, "key": p["key"]}, &stored); e != nil {
					return errors.New("密钥保护保存未完成；保留原命令核对")
				}
				target["secret_version"] = id
				target["state"] = "KEY_SAVED_UNVERIFIED"
				target["key_ready"] = true
				if e = putPartner(s, "account", target); e != nil {
					return e
				}
			}
			receipt = map[string]any{"account_id": m["id"], "api_provider": provider, "state": "KEY_SAVED_UNVERIFIED", "accounts_updated": len(targets)}
		case "account.disconnect":
			m, e := partnerObject(s, "account", str(p["account_id"]))
			if e != nil {
				return e
			}
			if e = a.protectAccount(s, str(m["id"])); e != nil {
				return e
			}
			m["state"] = "DISCONNECTED"
			if e = putPartner(s, "account", m); e != nil {
				return e
			}
			receipt = map[string]any{"account_id": m["id"], "state": m["state"]}
		case "connection.save":
			partner, route := str(p["partner_id"]), str(p["route"])
			if !validPartner(partner) || (route != "api" && route != "account") {
				return errors.New("伙伴或接入方式无效")
			}
			release, e := a.partnerRelease(str(p["release_id"]))
			if e != nil {
				return e
			}
			if str(release["component_id"]) != partner {
				return errors.New("组件版本不属于所选伙伴")
			}
			if e = releaseEnabled(s, str(release["id"])); e != nil {
				return e
			}
			account, e := partnerObject(s, "account", str(p["account_id"]))
			if e != nil {
				return e
			}
			if str(account["partner_id"]) != partner {
				return errors.New("账号引用不属于此伙伴")
			}
			if (route == "account" && str(account["kind"]) != "official_login") || (route == "api" && accountAPIProvider(account) == "") {
				return errors.New("账号与连接路线不符")
			}
			if account["state"] == "DISCONNECTED" {
				return errors.New("账号引用已断开")
			}
			m := map[string]any{}
			cid := str(p["id"])
			if cid != "" {
				m, e = partnerObject(s, "connection", cid)
				if e != nil {
					return e
				}
				if number(p["revision"]) != number(m["revision"]) {
					return errors.New("连接已在另一处修改，请刷新")
				}
			} else {
				cid = ID()
			}
			for _, key := range []string{"partner_id", "release_id", "route", "account_id", "options"} {
				m[key] = p[key]
			}
			m["id"] = cid
			m["model"] = str(p["model"])
			m["driver_provider"] = partner
			if route == "api" {
				m["api_provider"], m["driver_provider"] = p["api_provider"], p["driver_provider"]
				if e = a.bindPartnerAPI(partner, account, m); e != nil {
					return e
				}
			} else {
				delete(m, "api_provider")
				delete(m, "api_provider_snapshot")
				delete(m, "api_catalog_sha256")
			}
			m["state"] = "SAVED_UNVERIFIED"
			m["release_snapshot"] = release
			if e = putPartner(s, "connection", m); e != nil {
				return e
			}
			receipt = map[string]any{"connection_id": cid, "revision": m["revision"], "state": m["state"]}
		case "connection.disable":
			m, e := partnerObject(s, "connection", str(p["connection_id"]))
			if e != nil {
				return e
			}
			m["state"] = "DISABLED"
			if e = putPartner(s, "connection", m); e != nil {
				return e
			}
			receipt = map[string]any{"connection_id": m["id"], "state": "DISABLED"}
		case "component.disable", "component.enable":
			releaseID := str(p["release_id"])
			if _, e = a.partnerRelease(releaseID); e != nil {
				return e
			}
			state := "DISABLED"
			if kind == "component.enable" {
				state = "ENABLED"
			}
			if e = putPartner(s, "release-policy", map[string]any{"id": releaseID, "state": state}); e != nil {
				return e
			}
			if kind == "component.disable" {
				rows, e := s.Execute(`SELECT data FROM core_partner_objects WHERE kind='connection'`, nil)
				if e != nil {
					return e
				}
				for _, row := range rows.Rows {
					var c map[string]any
					_ = json.Unmarshal([]byte(str(row[0])), &c)
					if str(c["release_id"]) == releaseID {
						c["state"] = "DISABLED"
						if e = putPartner(s, "connection", c); e != nil {
							return e
						}
					}
				}
			}
			receipt = map[string]any{"release_id": releaseID, "state": state}
		case "component.prepare", "component.update", "component.remove", "connection.inspect":
			partner := str(p["partner_id"])
			releaseID := str(p["release_id"])
			if kind == "connection.inspect" {
				c, e := partnerObject(s, "connection", str(p["connection_id"]))
				if e != nil {
					return e
				}
				partner = str(c["partner_id"])
				releaseID = str(c["release_id"])
			}
			if kind == "component.remove" || kind == "component.disable" {
				r, e := a.partnerRelease(releaseID)
				if e != nil {
					return e
				}
				partner = str(r["component_id"])
				if kind == "component.remove" {
					if e = a.protectRelease(s, releaseID, recoveryRemovalFlag(p)); e != nil {
						return e
					}
				}
			}
			if !validPartner(partner) {
				return errors.New("伙伴不存在")
			}
			if kind != "connection.inspect" {
				pending, e := s.Execute(`SELECT id FROM core_partner_objects WHERE kind='operation'
					AND json_extract(data,'$.partner_id')=?
					AND json_extract(data,'$.kind') IN ('component.prepare','component.update','component.remove')
					AND json_extract(data,'$.state') NOT IN ('COMPLETED','FAILED','CANCELLED','STOPPED_RUNTIME','FAILED_NO_SEND')
					LIMIT 1`, []any{partner})
				if e != nil {
					return e
				}
				if len(pending.Rows) != 0 {
					return errors.New("此伙伴已有未完成的安装或卸载，请先核对原操作")
				}
			}
			op := map[string]any{"id": ID(), "kind": kind, "partner_id": partner, "release_id": releaseID, "version": str(p["version"]), "allow_restore_download": p["allow_restore_download"], "state": "QUEUED", "command_id": id, "created": now(), "owner_epoch": a.Epoch, "process_instance": a.Process}
			if kind == "component.prepare" {
				op["bundle_path"] = str(p["bundle_path"])
				op["expected_release"] = str(p["expected_release"])
			}
			if e = putPartner(s, "operation", op); e != nil {
				return e
			}
			receipt = map[string]any{"operation_id": op["id"], "state": "QUEUED"}
			after = func() { go a.componentOperation(op) }
		case "partner.run", "account.login", "account.inspect":
			mode := "run"
			var connection, account, release map[string]any
			if kind == "partner.run" {
				connection, e = partnerObject(s, "connection", str(p["connection_id"]))
				if e != nil {
					return e
				}
				if str(connection["state"]) == "DISABLED" {
					return errors.New("连接已停用")
				}
				if revision, supplied := p["connection_revision"]; supplied && number(revision) != number(connection["revision"]) {
					return errors.New("连接配置已变化，请刷新后重新选择本次组合")
				}
				account, e = partnerObject(s, "account", str(connection["account_id"]))
				if e != nil {
					return e
				}
				release, e = a.partnerRelease(str(connection["release_id"]))
				if e != nil {
					return e
				}
			} else {
				mode = "login"
				if kind == "account.inspect" {
					mode = "inspect"
				}
				account, e = partnerObject(s, "account", str(p["account_id"]))
				if e != nil {
					return e
				}
				if str(account["kind"]) != "official_login" {
					return errors.New("官方登录仅用于账号路线")
				}
				releaseID := str(p["release_id"])
				if releaseID == "" {
					return errors.New("请选择本次官方登录使用的准确组件版本")
				}
				release, e = a.partnerRelease(releaseID)
				if e != nil {
					return e
				}
				if str(release["component_id"]) != str(account["partner_id"]) {
					return errors.New("登录组件不属于所选账号伙伴")
				}

				connection = map[string]any{"partner_id": account["partner_id"], "route": "account", "account_id": account["id"], "release_id": release["id"]}
			}
			if str(account["state"]) == "DISCONNECTED" {
				return errors.New("账号引用已断开")
			}
			if str(connection["route"]) == "api" {
				if str(account["partner_id"]) != str(connection["partner_id"]) {
					return errors.New("账号引用不属于所选工作端")
				}
				if e = a.bindPartnerAPI(str(account["partner_id"]), account, connection); e != nil {
					return e
				}
			}
			if e = releaseEnabled(s, str(release["id"])); e != nil {
				return e
			}
			if kind == "account.inspect" {
				if _, supplied := p["auth_options"]; supplied {
					return errors.New("请在官方登录时选择站点，核对沿用原登录设置")
				}
				original, err := a.currentAccountLogin(s, str(account["id"]), str(release["id"]))
				if err != nil {
					return err
				}
				if original != nil {
					receipt = map[string]any{"run_id": original["id"], "account_id": account["id"], "release_id": release["id"], "state": original["state"], "reused_existing_login": true}
					break
				}
			}
			if mode == "run" && str(connection["route"]) == "api" && str(account["secret_version"]) == "" {
				return errors.New("等待 Human 输入所选厂商的 API Key，未发送模型请求")
			}
			if mode == "run" && strings.TrimSpace(str(p["prompt"])) == "" {
				return errors.New("请输入本次连接验证任务")
			}
			if e = a.protectAccount(s, str(account["id"])); e != nil {
				return e
			}
			if kind == "account.login" {
				if options, supplied := p["auth_options"]; supplied {
					validated, err := partnerAuthOptions(str(account["partner_id"]), options)
					if err != nil {
						return err
					}
					applyPartnerAuthOptions(account, validated)
				}
			}
			project := str(p["project_id"])
			if project == "" {
				project = a.Profile.DefaultProjectID
			}
			r, e := s.Execute(`SELECT root FROM projects WHERE id=?`, []any{project})
			if e != nil {
				return e
			}
			if len(r.Rows) != 1 {
				return errors.New("请选择已登记项目")
			}
			run := map[string]any{"id": ID(), "partner_id": account["partner_id"], "account_id": account["id"], "secret_version": account["secret_version"], "connection_id": connection["id"], "connection_revision": connection["revision"], "connection_snapshot": connection, "release_id": release["id"], "release_snapshot": release, "mode": mode, "state": "QUEUED", "project_id": project, "workspace": r.Rows[0][0], "prompt": p["prompt"], "created": now(), "owner_epoch": a.Epoch, "process_instance": a.Process, "implementation_revision": a.Implementation, "data_root": a.Profile.DataRoot}
			if mode == "run" {
				permission, source, err := a.freezePartnerPermission(str(account["partner_id"]), connection, p)
				if err != nil {
					return err
				}
				run["resolved_permission"], run["permission_source"] = permission, source
				if parent != nil {
					for _, key := range []string{"parent_task_id", "parent_attempt_id", "parent_spec_hash", "parent_prompt_sha256", "workspace"} {
						run[key] = parent[key]
					}
					if parent["document_mcp"] == true {
						run["document_mcp"] = true
					}
					if sceneMCP := parent["scene_mcp"]; sceneMCP != nil {
						run["scene_mcp"] = sceneMCP
					}
					for _, key := range partnerResumeKeys {
						if parent[key] != nil {
							run[key] = parent[key]
						}
					}
				}
			}
			if str(connection["route"]) == "api" {
				run["api_provider"], run["driver_provider"] = connectionAPIProvider(connection), connectionAPIProvider(connection)
				run["api_provider_snapshot"], run["api_catalog_sha256"] = connection["api_provider_snapshot"], connection["api_catalog_sha256"]
			}
			if str(connection["route"]) == "account" {
				run["auth_options"] = account["auth_options"]
			}
			var coreIdentity map[string]any
			if e = a.Legacy.Call(context.Background(), "partner.process.inspect", map[string]any{"pid": os.Getpid()}, &coreIdentity); e != nil {
				return e
			}
			run["owner_core_identity"] = coreIdentity
			run["owner_core_pid"] = os.Getpid()
			if mode == "login" {
				account["auth_generation"] = run["id"]
				account["login_run_id"], account["login_release_id"] = run["id"], run["release_id"]
				if e = putPartner(s, "account", account); e != nil {
					return e
				}
			}
			if e = a.putPartnerRun(s, run); e != nil {
				return e
			}
			receipt = map[string]any{"run_id": run["id"], "account_id": account["id"], "state": "QUEUED"}
			after = func() { go a.runPartner(run) }
		case "partner.stop", "partner.input", "partner.approve", "account.login-input":
			run, e := partnerObject(s, "run", str(p["run_id"]))
			if e != nil {
				return e
			}
			rid := str(run["id"])
			if kind == "account.login-input" && (str(run["mode"]) != "login" || run["stop_requested"] == true || terminalPartner(str(run["state"])) || str(p["text"]) == "" || len(str(p["text"])) > 16384 || strings.ContainsAny(str(p["text"]), "\r\n\x00")) {
				return errors.New("登录回填须对应当前未停止的官方登录请求")
			}
			a.mu.Lock()
			proc := a.partnerProcesses[rid]
			a.mu.Unlock()
			if proc == nil {
				if kind == "partner.stop" && str(run["state"]) == "QUEUED" {
					run["state"] = "CANCELLED"
					run["stop_requested"] = true
					if e = a.putPartnerRun(s, run); e != nil {
						return e
					}
					receipt = map[string]any{"run_id": rid, "state": "CANCELLED"}
					break
				}
				return errors.New("本次运行无可核对的活动进程；不向旧会话重发")
			}
			if kind != "partner.stop" {
				requestID := str(p["request_id"])
				pending, _ := run["pending_requests"].([]any)
				remaining := []any{}
				found := false
				for _, item := range pending {
					q, ok := item.(map[string]any)
					if ok && str(q["id"]) == requestID && requestID != "" {
						want := "input"
						if kind == "partner.approve" {
							want = "approval"
						}
						if str(q["kind"]) != want {
							return errors.New("原生请求类型不符")
						}
						if kind == "account.login-input" && q["sensitive"] != true {
							return errors.New("此请求不接受敏感登录回填")
						}
						if kind != "account.login-input" && q["sensitive"] == true {
							return errors.New("敏感登录内容须使用专用回填入口")
						}
						found = true
					} else {
						remaining = append(remaining, item)
					}
				}
				if !found {
					return errors.New("原生请求身份已变化，请刷新核对")
				}
				if kind == "partner.approve" && str(p["decision"]) != "allow" && str(p["decision"]) != "deny" {
					return errors.New("审批只接受本次允许或拒绝")
				}
				run["pending_requests"] = remaining
			}
			action := map[string]any{"action": "input", "text": p["text"], "request_id": p["request_id"]}
			if kind == "account.login-input" {
				action["action"] = "login-input"
			} else if kind == "partner.stop" {
				action = map[string]any{"action": "stop"}
				run["state"] = "STOP_REQUESTED"
				run["stop_requested"] = true
			} else if kind == "partner.approve" {
				action = map[string]any{"action": "approval", "request_id": p["request_id"], "decision": p["decision"]}
			}
			if e = a.putPartnerRun(s, run); e != nil {
				return e
			}
			receipt = map[string]any{"run_id": rid, "state": run["state"]}
			after = func() {
				if kind == "partner.stop" && run["document_mcp"] == true {
					a.cancelRunDocuments(run)
				}
				if e := proc.send(action); e != nil {
					a.partnerFailure(rid, e)
				}
				if kind == "partner.stop" {
					go func() {
						time.Sleep(8 * time.Second)
						a.mu.Lock()
						active := a.partnerProcesses[rid]
						a.mu.Unlock()
						if active == proc {
							proc.cancel()
						}
					}()
				}
			}
		case "partner.reconcile":
			m, e := partnerObject(s, "run", str(p["run_id"]))
			if e != nil {
				return e
			}
			a.mu.Lock()
			active := a.partnerProcesses[str(m["id"])] != nil
			a.mu.Unlock()
			if active {
				return errors.New("原运行仍由当前进程控制，请使用停止")
			}
			owned, _ := m["owned_process"].(map[string]any)
			localExit := owned["exit_confirmed"] == true
			proof := map[string]any{"at": now(), "basis": "SAVED_OWNED_TREE_EXIT", "local_exit_confirmed": localExit}
			if !localExit && str(m["process_instance"]) != a.Process && owned["ownership"] == "WINDOWS_JOB_OBJECT" && owned["kill_on_close"] == true {
				oldCore, _ := m["owner_core_identity"].(map[string]any)
				var coreNow, workerNow map[string]any
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				if number(m["owner_core_pid"]) > 0 && number(m["pid"]) > 0 {
					if e = a.Legacy.Call(ctx, "partner.process.inspect", map[string]any{"pid": m["owner_core_pid"]}, &coreNow); e != nil {
						return e
					}
					if e = a.Legacy.Call(ctx, "partner.process.inspect", map[string]any{"pid": m["pid"]}, &workerNow); e != nil {
						return e
					}
					absent := func(current, original map[string]any) bool {
						return current["alive"] == false || (current["alive"] == true && str(current["creation"]) != "" && str(original["creation"]) != "" && current["creation"] != original["creation"])
					}
					localExit = absent(coreNow, oldCore) && absent(workerNow, owned)
					proof["basis"] = "OLD_OWNER_AND_WORKER_ABSENT_WITH_KILL_ON_CLOSE_JOB"
					proof["owner"] = coreNow
					proof["worker"] = workerNow
				}
			}
			proof["local_exit_confirmed"] = localExit
			m["local_exit_confirmed"] = localExit
			m["reconciliation"] = proof
			if str(p["resolution"]) == "accept_received_result" {
				if !localExit {
					return errors.New("本地进程树退出尚未核对，保留原运行")
				}
				if str(m["mode"]) == "login" {
					if e = receivedPartnerLoginResult(m); e != nil {
						return e
					}
					proof["accepted_scope"] = "OFFICIAL_ACCOUNT_RECEIPT"
					m["remote_state"] = "OFFICIAL_ACCOUNT_AUTHENTICATED"
				} else if _, e = receivedPartnerTurnResult(m); e != nil {
					return e
				}
				if e = a.completePartnerRun(s, m, "EXPLICIT_ACCEPT_RECEIVED_RESULT"); e != nil {
					return e
				}
			} else if str(p["resolution"]) == "close_local_tracking" {
				if !localExit {
					return errors.New("本地进程树退出尚未核对，保留原运行")
				}
				m["state"] = "STOPPED_RUNTIME"
				m["remote_state"] = "UNKNOWN"
				m["remote_cancel"] = "UNKNOWN"
				m["explicit_local_tracking_closed"] = true
				m["result_acceptance"] = "NOT_INFERRED"
				m["unresolved_requests"] = m["pending_requests"]
				m["pending_requests"] = []any{}
			}
			if e = a.putPartnerRun(s, m); e != nil {
				return e
			}
			remote := str(m["remote_state"])
			if remote == "" {
				remote = "UNKNOWN"
			}
			receipt = map[string]any{"run_id": m["id"], "state": m["state"], "local_exit_confirmed": localExit, "remote_state": remote, "no_resend": true}
		case "component.reconcile":
			op, e := partnerObject(s, "operation", str(p["operation_id"]))
			if e != nil {
				return e
			}
			if str(op["state"]) != "ORPHANED_UNKNOWN" {
				return errors.New("只有待核对的组件操作可以核对")
			}
			found := false
			if op["kind"] == "component.remove" {
				if r, e := a.componentManager().Get(str(op["release_id"])); e == nil && r.State == "REMOVED" {
					op["state"] = "COMPLETED"
					op["reconciliation"] = "EXACT_RELEASE_REMOVAL_RECORD"
					op["result"] = r
					found = true
				}
			}
			r, lookupErr := a.componentManager().OperationRelease(str(op["id"]))
			if !found && lookupErr == nil {
				if _, e = a.componentManager().Verify(context.Background(), r.ID); e != nil {
					return e
				}
				op["state"] = "COMPLETED"
				op["result"] = r
				op["reconciliation"] = "EXACT_OPERATION_PUBLISHED_RELEASE_VERIFIED"
				found = true
			}

			if !found {
				op["reconciliation"] = "NO_MATCHING_PUBLISHED_RELEASE; ORIGINAL_STAGING_RETAINED"
			}
			if e = putPartner(s, "operation", op); e != nil {
				return e
			}
			receipt = map[string]any{"operation_id": op["id"], "state": op["state"], "no_reinstall": true}
		default:
			return errors.New("伙伴操作不支持")
		}
		b, e := json.Marshal(receipt)
		if e != nil {
			return e
		}
		if _, e = s.Execute(`INSERT INTO commands VALUES(?,?,?,?,?)`, []any{id, kind, fingerprint, string(b), now()}); e != nil {
			return e
		}
		if _, e = s.Execute(`INSERT INTO core_command_metadata VALUES(?,?)`, []any{id, "partners-json-v1"}); e != nil {
			return e
		}
		ev, _ := json.Marshal(map[string]any{"command_id": id, "receipt": receipt, "owner_epoch": a.Epoch})
		_, e = s.Execute(`INSERT INTO events(at,task_id,attempt_id,kind,payload) VALUES(?,NULL,NULL,?,?)`, []any{now(), kind, string(ev)})
		fresh = e == nil
		return e
	})
	if e != nil {
		return nil, e
	}
	if fresh && after != nil {
		after()
	}
	return map[string]any{"receipt": receipt, "command_id": id, "replayed": !fresh}, nil
}
func (a *App) protectAccount(s *storage.Session, id string) error {
	r, e := s.Execute(`SELECT data FROM core_partner_objects WHERE kind='run'`, nil)
	if e != nil {
		return e
	}
	for _, v := range r.Rows {
		var m map[string]any
		_ = json.Unmarshal([]byte(str(v[0])), &m)
		a.mu.Lock()
		active := a.partnerProcesses[str(m["id"])] != nil
		a.mu.Unlock()
		if str(m["account_id"]) == id && (active || !terminalPartner(str(m["state"]))) {
			return errors.New("该账号仍有在途或未知运行，请先核对")
		}
	}
	return nil
}

const restoreRedownloadFlag = "@explicit-restore-redownload"

func recoveryRemovalFlag(p map[string]any) string {
	if p["allow_restore_download"] == true {
		return restoreRedownloadFlag
	}
	return ""
}
func containsRecoveryRemovalFlag(values []string) bool {
	for _, v := range values {
		if v == restoreRedownloadFlag {
			return true
		}
	}
	return false
}
func (a *App) protectRelease(s *storage.Session, id string, ignore ...string) error {
	r, e := s.Execute(`SELECT kind,data FROM core_partner_objects WHERE kind IN ('run','connection','operation','recovery')`, nil)
	if e != nil {
		return e
	}
	for _, v := range r.Rows {
		var m map[string]any
		_ = json.Unmarshal([]byte(str(v[1])), &m)
		if v[0] == "recovery" && !containsRecoveryRemovalFlag(ignore) {
			refs, _ := m["release_ids"].([]any)
			for _, ref := range refs {
				if str(ref) == id {
					return errors.New("此组件版本受已登记恢复点保护；请保留程序或按明确范围处理恢复点")
				}
			}
		}
		a.mu.Lock()
		active := a.partnerProcesses[str(m["id"])] != nil
		a.mu.Unlock()
		skip := false
		for _, value := range ignore {
			if str(m["id"]) == value {
				skip = true
			}
		}
		if !skip && str(m["release_id"]) == id && (active || (v[0] == "connection" && str(m["state"]) != "DISABLED") || (v[0] != "connection" && !terminalPartner(str(m["state"])))) {
			return errors.New("此版本仍被连接、在途或待核对记录引用；请先停用连接并处理原运行")
		}
	}
	return nil
}

func (a *App) componentOperation(op map[string]any) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()
	a.admission.Lock()
	if a.retiring.Load() || a.verifySources() != nil {
		a.admission.Unlock()
		op["state"] = "FAILED_NO_SEND"
		op["error"] = "核心已停止或版本变化，未准备组件"
		_ = a.savePartner("operation", op)
		return
	}
	op["state"] = "RUNNING"
	_ = a.savePartner("operation", op)
	a.admission.Unlock()
	var result any
	var err error
	manager := a.componentManager()
	manager.OperationID = str(op["id"])
	switch str(op["kind"]) {
	case "component.prepare", "component.update":
		var last components.Progress
		var updated time.Time
		manager.Progress = func(progress components.Progress) {
			now := time.Now()
			if progress == last || (progress.Phase == last.Phase && progress.Total == last.Total && progress.Done != progress.Total && now.Sub(updated) < 400*time.Millisecond) {
				return
			}
			op["progress"] = progress
			if a.savePartner("operation", op) == nil {
				last, updated = progress, now
			}
		}
		result, err = manager.PrepareSelected(ctx, str(op["partner_id"]), str(op["version"]), str(op["expected_release"]), str(op["bundle_path"]))
	case "component.remove":
		a.admission.Lock()
		err = a.write(func(s *storage.Session) error {
			return a.protectRelease(s, str(op["release_id"]), str(op["id"]), recoveryRemovalFlag(op))
		})
		if err == nil {
			err = manager.Remove(ctx, str(op["release_id"]))
		}
		a.admission.Unlock()
	case "component.disable":
		err = a.write(func(s *storage.Session) error {
			r, e := s.Execute(`SELECT data FROM core_partner_objects WHERE kind='connection'`, nil)
			if e != nil {
				return e
			}
			for _, row := range r.Rows {
				var c map[string]any
				_ = json.Unmarshal([]byte(str(row[0])), &c)
				if str(c["release_id"]) == str(op["release_id"]) {
					c["state"] = "DISABLED"
					if e = putPartner(s, "connection", c); e != nil {
						return e
					}
				}
			}
			return nil
		})
	case "connection.inspect":
		result, err = manager.Inspect(ctx, str(op["release_id"]))
	}
	op["state"] = "COMPLETED"
	delete(op, "progress")
	op["result"] = result
	if err != nil {
		op["state"] = "FAILED"
		op["error"] = err.Error()
	}
	_ = a.savePartner("operation", op)
}
func (a *App) partnerFailure(id string, err error) {
	_ = a.write(func(s *storage.Session) error {
		m, e := partnerObject(s, "run", id)
		if e != nil {
			return e
		}
		m["state"] = "ORPHANED_UNKNOWN"
		m["error"] = err.Error()
		return a.putPartnerRun(s, m)
	})
}
func (a *App) failPartnerBeforeStart(id string, err error) {
	_ = a.write(func(s *storage.Session) error {
		m, e := partnerObject(s, "run", id)
		if e != nil {
			return e
		}
		if terminalPartner(str(m["state"])) || m["stop_requested"] == true {
			return nil
		}
		m["state"] = "FAILED_NO_SEND"
		m["error"] = err.Error()
		return a.putPartnerRun(s, m)
	})
}
func (a *App) runPartner(run map[string]any) {
	rid := str(run["id"])
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	proc := &partnerProcess{cancel: cancel}
	defer func() { a.mu.Lock(); delete(a.partnerProcesses, rid); a.mu.Unlock() }()
	connection, _ := run["connection_snapshot"].(map[string]any)
	release, _ := run["release_snapshot"].(map[string]any)
	config := map[string]any{"partner_id": run["partner_id"], "route": connection["route"], "mode": run["mode"], "entry": release["entry"], "home": filepath.Join(a.Profile.UserRoot, "providers", str(run["partner_id"]), "accounts", str(run["account_id"])), "workspace": run["workspace"], "model": connection["model"], "prompt": run["prompt"], "options": connection["options"], "run_id": rid, "instance_id": a.Profile.InstanceID, "owner_epoch": a.Epoch}
	config["resolved_permission"], config["permission_source"] = run["resolved_permission"], run["permission_source"]
	if str(connection["route"]) == "api" {
		config["api_provider"], config["driver_provider"] = connectionAPIProvider(connection), connectionAPIProvider(connection)
		config["api_provider_snapshot"], config["api_catalog_sha256"] = connection["api_provider_snapshot"], connection["api_catalog_sha256"]
		if provider, ok := connection["api_provider_snapshot"].(map[string]any); ok {
			config["base_url"] = provider["base_url"]
		}
	}
	for _, k := range []string{"account_id", "secret_version", "connection_id", "connection_revision", "session_id"} {
		config[k] = run[k]
	}
	if session := str(run["resume_session"]); session != "" {
		config["resume"], config["session_id"] = true, session
	}
	if sceneMCP := run["scene_mcp"]; sceneMCP != nil {
		config["scene_mcp"] = sceneMCP
	}
	if run["document_mcp"] == true {
		closeServer, e := a.documentMCP(ctx, run, config, proc)
		if e != nil {
			a.failPartnerBeforeStart(rid, e)
			return
		}
		defer closeServer()
	}
	if str(connection["route"]) == "account" {
		config["auth_options"] = run["auth_options"]
	}
	key := ""
	if str(run["mode"]) == "run" && str(connection["route"]) == "api" {
		var secret map[string]any
		if e := a.Legacy.Call(ctx, "partner.secret.read", map[string]any{"account_id": run["account_id"], "version": run["secret_version"]}, &secret); e != nil {
			a.failPartnerBeforeStart(rid, errors.New("无法读取所选准确凭据版本"))
			return
		}
		key = str(secret["key"])
		config["api_key"] = key
		proc.secrets = append(proc.secrets, key)
	}
	if _, err := a.componentManager().Verify(ctx, str(run["release_id"])); err != nil {
		a.failPartnerBeforeStart(rid, err)
		return
	}
	cancelled := false
	if err := a.write(func(s *storage.Session) error {
		current, e := partnerObject(s, "run", rid)
		if e != nil {
			return e
		}
		cancelled = terminalPartner(str(current["state"]))
		return nil
	}); err != nil {
		return
	}
	if cancelled {
		return
	}
	a.admission.Lock()
	if err := a.write(func(s *storage.Session) error {
		current, e := partnerObject(s, "run", rid)
		if e != nil {
			return e
		}
		cancelled = terminalPartner(str(current["state"])) || str(current["state"]) == "STOP_REQUESTED"
		return nil
	}); err != nil || cancelled || ctx.Err() != nil {
		a.admission.Unlock()
		return
	}
	if a.retiring.Load() || a.verifySources() != nil {
		a.admission.Unlock()
		a.failPartnerBeforeStart(rid, errors.New("核心已停止或版本变化"))
		return
	}
	cmd := exec.CommandContext(ctx, a.Profile.PythonExecutable, "-B", "-X", "utf8", "-m", "researchos.workdesk.partners")
	cmd.Dir = a.Profile.ProgramRoot
	cmd.Env = bridge.Environment(map[string]string{"PYTHONPATH": a.pythonSources(), "WORKDESK_HOME": a.Profile.UserRoot, "WORKDESK_DATA_DIR": a.Profile.DataRoot})
	quietCommand(cmd)
	in, e := cmd.StdinPipe()
	if e != nil {
		a.admission.Unlock()
		a.partnerFailure(rid, e)
		return
	}
	out, e := cmd.StdoutPipe()
	if e != nil {
		a.admission.Unlock()
		a.partnerFailure(rid, e)
		return
	}
	cmd.Stderr = &partnerDiagnostic{a: a, id: rid, process: proc}
	owner, e := startOwnedCommand(cmd)
	if e != nil {
		a.admission.Unlock()
		a.failPartnerBeforeStart(rid, e)
		return
	}
	defer owner.Close()
	proc.mu.Lock()
	proc.input = in
	proc.mu.Unlock()
	a.mu.Lock()
	a.partnerProcesses[rid] = proc
	a.mu.Unlock()
	run["state"] = "RUNNING"
	run["pid"] = cmd.Process.Pid
	run["owned_process"] = owner.Identity()
	_ = a.savePartner("run", run)
	if e = proc.send(map[string]any{"action": "start", "config": config}); e != nil {
		cancel()
	}
	a.admission.Unlock()
	scanner := bufio.NewScanner(out)
	scanner.Buffer(make([]byte, 65536), 16*1024*1024)
	for scanner.Scan() {
		line := scanner.Text()
		var event map[string]any
		if e = json.Unmarshal([]byte(line), &event); e != nil {
			event = map[string]any{"type": "diagnostic", "text": line}
		}
		event = proc.redactValue(event).(map[string]any)
		if e = a.partnerEvent(rid, event); e != nil {
			cancel()
			break
		}
	}
	_ = in.Close()
	waitErr := owner.Wait()
	forcedCleanup := false
	if !owner.ExitConfirmed() {
		// The worker has exited but native descendants can outlive it. Close
		// this already-owned local tree before recording the final outcome;
		// do not turn a deferred kill-on-close into a stale UNKNOWN record.
		forcedCleanup = true
		terminateErr := owner.Terminate()
		waitErr = owner.Wait()
		if !owner.ExitConfirmed() && terminateErr != nil {
			waitErr = errors.Join(waitErr, terminateErr)
		}
	}
	if scanner.Err() != nil {
		waitErr = scanner.Err()
	}
	_ = a.write(func(s *storage.Session) error {
		m, e := partnerObject(s, "run", rid)
		if e != nil {
			return e
		}
		m["owned_process"] = owner.Identity()
		if forcedCleanup {
			m["owned_process"].(map[string]any)["forced_owned_job_cleanup"] = true
		}
		if !owner.ExitConfirmed() {
			m["state"] = "STOP_UNKNOWN"
			m["error"] = "本地子进程树退出尚未核对；保留原运行"
			return a.putPartnerRun(s, m)
		}
		m["local_exit_confirmed"] = true
		if m["stop_requested"] == true {
			m["state"] = "STOPPED_RUNTIME"
			m["remote_cancel"] = "UNKNOWN"
		} else if str(m["state"]) == "RESULT_RECEIVED" && waitErr == nil {
			if e = a.completePartnerRun(s, m, "OFFICIAL_RESULT_AND_CONFIRMED_LOCAL_EXIT"); e != nil {
				return e
			}
		}
		if str(m["state"]) == "ERROR_RECEIVED" {
			m["state"] = "FAILED"
		}
		if str(m["state"]) == "STOP_RESULT_RECEIVED" {
			m["state"] = "STOPPED_RUNTIME"
			m["remote_cancel"] = "UNKNOWN"
		}
		if !terminalPartner(str(m["state"])) {
			if str(m["state"]) == "STOP_REQUESTED" && owner.ExitConfirmed() {
				m["state"] = "STOPPED_RUNTIME"
				m["remote_cancel"] = "UNKNOWN"
			} else {
				m["state"] = "ORPHANED_UNKNOWN"
				m["error"] = "官方端退出，尚无完整结果回执"
				if waitErr != nil {
					m["error"] = waitErr.Error()
				}
			}
		}
		return a.putPartnerRun(s, m)
	})
}

type partnerDiagnostic struct {
	a       *App
	id      string
	process *partnerProcess
}

func (p *partnerDiagnostic) Write(b []byte) (int, error) {
	text := string(b)
	if p.process != nil {
		text = p.process.redact(text)
	}
	if len(text) > 8192 {
		text = text[len(text)-8192:]
	}
	_ = p.a.partnerEvent(p.id, map[string]any{"type": "diagnostic", "text": text})
	return len(b), nil
}
func (a *App) partnerEvent(id string, event map[string]any) error {
	return a.write(func(s *storage.Session) error {
		m, e := partnerObject(s, "run", id)
		if e != nil {
			return e
		}
		originalState := str(m["state"])
		raw, e := json.Marshal(event)
		if e != nil {
			return e
		}
		if _, e = s.Execute(`INSERT INTO core_partner_events(run_id,at,data) VALUES(?,?,?)`, []any{id, now(), string(raw)}); e != nil {
			return e
		}
		m["last_event"] = event
		if event["kind"] == "native_model_selected" {
			m["resolved_model"] = event["model"]
			m["resolved_provider"] = event["provider"]
			m["model_selection_source"] = event["selection_source"]
		}
		typ := str(event["type"])
		if typ == "auth_status" || (str(m["mode"]) != "run" && (typ == "result" || typ == "error")) {
			if e = a.observePartnerAuth(s, m, event); e != nil {
				return e
			}
		}
		switch typ {
		case "result":
			m["state"] = "RESULT_RECEIVED"
			m["result"] = event
		case "error":
			m["state"] = "ERROR_RECEIVED"
			if event["receipt_unknown"] == true {
				m["state"] = "ORPHANED_UNKNOWN"
			}
			m["error"] = event["error"]
			if m["error"] == nil {
				m["error"] = event["message"]
			}
		case "stopped":
			m["state"] = "STOP_RESULT_RECEIVED"
			m["remote_cancel"] = "UNKNOWN"
		case "login":
			m["state"] = "WAITING_INPUT"
			for _, k := range []string{"login_url", "user_code", "instructions"} {
				if event[k] != nil {
					m[k] = event[k]
				}
			}
		case "approval":
			m["state"] = "WAITING_APPROVAL"
			m["request_id"] = event["request_id"]
		case "input":
			m["state"] = "WAITING_INPUT"
		case "auth_status":
			observation, _ := m["auth_observation"].(map[string]any)
			if str(observation["auth_state"]) == "AUTHENTICATED" && str(m["mode"]) == "login" {
				// The worker can send a second positive status after its result
				// (for example DeepSeek's commit then sign-in-success notices).
				// Update the account fact, never discard that pending result.
				if originalState != "RESULT_RECEIVED" {
					m["state"] = "RUNNING"
				}
				for _, key := range []string{"login_url", "user_code", "instructions", "request_id"} {
					delete(m, key)
				}
				m["pending_requests"] = []any{}
			}
		}
		if typ == "approval" || typ == "input" || (typ == "login" && str(event["request_id"]) != "") {
			requestID := str(event["request_id"])
			if requestID != "" {
				pending, _ := m["pending_requests"].([]any)
				exists := false
				for _, item := range pending {
					if q, ok := item.(map[string]any); ok && str(q["id"]) == requestID {
						exists = true
					}
				}
				if !exists {
					requestKind := typ
					if requestKind == "login" {
						requestKind = "input"
					}
					message := event["message"]
					if message == nil {
						message = event["instructions"]
					}
					m["pending_requests"] = append(pending, map[string]any{"id": requestID, "kind": requestKind, "message": message, "options": event["options"], "details": event["native"], "sensitive": event["sensitive"], "input_type": event["input_type"]})
				}
			}
		}
		if session := str(event["session_id"]); session != "" {
			m["session_id"] = session
		}
		if str(m["mode"]) != "run" {
			if terminalPartner(originalState) {
				m["state"] = originalState
			} else if m["stop_requested"] == true && typ != "stopped" {
				m["state"] = "STOP_REQUESTED"
			}
		}

		return a.putPartnerRun(s, m)
	})
}

// Kept separate from the generic UI API so event bodies load only on demand.
func (a *App) partnerRunEvents(id string, after int64) (any, error) {
	rows, e := a.query(`SELECT seq,at,data FROM core_partner_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 200`, id, after)
	if e != nil {
		return nil, e
	}
	events := []any{}
	for _, r := range rows {
		var data any
		_ = json.Unmarshal([]byte(str(r["data"])), &data)
		events = append(events, map[string]any{"seq": r["seq"], "at": r["at"], "event": data})
	}
	return map[string]any{"events": events}, nil
}
