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
	mu     sync.Mutex
	input  io.WriteCloser
	cancel context.CancelFunc
}

func (p *partnerProcess) send(v any) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.input == nil {
		return errors.New("原运行输入尚未就绪")
	}
	return json.NewEncoder(p.input).Encode(v)
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
	return a.write(func(s *storage.Session) error { return putPartner(s, kind, m) })
}
func terminalPartner(state string) bool {
	return state == "COMPLETED" || state == "FAILED" || state == "CANCELLED" || state == "STOPPED_RUNTIME" || state == "FAILED_NO_SEND"
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
				if e = putPartner(s, str(row[0]), m); e != nil {
					return e
				}
			}
		}
		return nil
	})
}
func (a *App) partnersView(path string) (any, error) {
	schema, _ := a.DB.Schema()
	if schema != "3" {
		return map[string]any{"partners": components.Catalog(), "components": []any{}, "accounts": []any{}, "connections": []any{}, "operations": []any{}, "runs": []any{}, "migration_required": true}, nil
	}
	kinds := map[string]string{"/api/v2/accounts": "account", "/api/v2/connections": "connection", "/api/v2/component-operations": "operation", "/api/v2/partner-runs": "run"}
	if k, ok := kinds[path]; ok {
		rows, e := a.partnerObjects(k)
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
	for _, run := range runs {
		if c, ok := run["connection_snapshot"].(map[string]any); ok {
			run["model"] = c["model"]
		}
		if observed := str(run["resolved_model"]); observed != "" {
			run["model"] = observed
		}
		if result, ok := run["result"].(map[string]any); ok {
			run["output"] = result["output"]
			if run["output"] == nil {
				run["output"] = result["text"]
			}
		}
		for _, account := range accounts {
			if account["id"] == run["account_id"] {
				run["account_label"] = account["label"]
				if str(run["mode"]) != "run" {
					for _, key := range []string{"login_url", "user_code", "instructions"} {
						if run[key] != nil {
							account[key] = run[key]
						}
					}
				}
			}
		}
	}
	return map[string]any{"partners": components.Catalog(), "components": visibleReleases, "accounts": accounts, "connections": connections, "operations": operations, "runs": runs, "storage_schema": 3, "api_verification": "WAITING_HUMAN_KEY"}, nil
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
	schema, _ := a.DB.Schema()
	if schema != "3" {
		return nil, errors.New("伙伴操作须先完成显式备份迁移")
	}
	id, kind := str(command["id"]), str(command["kind"])
	p, ok := command["payload"].(map[string]any)
	if !uuidPattern.MatchString(id) || !ok {
		return nil, errors.New("操作须有准确命令ID和对象参数")
	}
	raw, e := json.Marshal(p)
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
			if !validPartner(partner) || (k != "official_login" && k != "deepseek_api") || label == "" || len(label) > 120 {
				return errors.New("请选择伙伴、账号类型并填写标签")
			}
			m := map[string]any{"id": ID(), "partner_id": partner, "label": label, "kind": k, "state": "NOT_CONFIGURED", "created": now()}
			if e = putPartner(s, "account", m); e != nil {
				return e
			}
			receipt = map[string]any{"account_id": m["id"], "state": m["state"]}
		case "account.set-key":
			m, e := partnerObject(s, "account", str(p["account_id"]))
			if e != nil {
				return e
			}
			if str(m["kind"]) != "deepseek_api" {
				return errors.New("所选账号不是 DeepSeek API 凭据")
			}
			targets := []map[string]any{m}
			if p["apply_all_api"] == true {
				rows, e := s.Execute(`SELECT data FROM core_partner_objects WHERE kind='account' AND json_extract(data,'$.kind')='deepseek_api' AND json_extract(data,'$.state')!='DISCONNECTED'`, nil)
				if e != nil {
					return e
				}
				targets = []map[string]any{}
				for _, row := range rows.Rows {
					var target map[string]any
					if e = json.Unmarshal([]byte(str(row[0])), &target); e != nil {
						return e
					}
					targets = append(targets, target)
				}
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
			receipt = map[string]any{"account_id": m["id"], "state": "KEY_SAVED_UNVERIFIED", "accounts_updated": len(targets)}
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
			want := "official_login"
			if route == "api" {
				want = "deepseek_api"
			}
			if str(account["kind"]) != want {
				return errors.New("账号与连接路线不符")
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
			if route == "api" && m["model"] == "" {
				m["model"] = "deepseek-flash"
			}
			m["driver_provider"] = partner
			if route == "api" {
				m["driver_provider"] = "deepseek"
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
			op := map[string]any{"id": ID(), "kind": kind, "partner_id": partner, "release_id": releaseID, "version": str(p["version"]), "allow_restore_download": p["allow_restore_download"], "state": "QUEUED", "command_id": id, "created": now(), "owner_epoch": a.Epoch, "process_instance": a.Process}
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
			if e = releaseEnabled(s, str(release["id"])); e != nil {
				return e
			}
			if mode == "run" && str(connection["route"]) == "api" && str(account["secret_version"]) == "" {
				return errors.New("等待 Human 最后输入 DeepSeek API Key，未发送模型请求")
			}
			if mode == "run" && strings.TrimSpace(str(p["prompt"])) == "" {
				return errors.New("请输入本次连接验证任务")
			}
			if e = a.protectAccount(s, str(account["id"])); e != nil {
				return e
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
			var coreIdentity map[string]any
			if e = a.Legacy.Call(context.Background(), "partner.process.inspect", map[string]any{"pid": os.Getpid()}, &coreIdentity); e != nil {
				return e
			}
			run["owner_core_identity"] = coreIdentity
			run["owner_core_pid"] = os.Getpid()
			if e = putPartner(s, "run", run); e != nil {
				return e
			}
			receipt = map[string]any{"run_id": run["id"], "account_id": account["id"], "state": "QUEUED"}
			after = func() { go a.runPartner(run) }
		case "partner.stop", "partner.input", "partner.approve":
			run, e := partnerObject(s, "run", str(p["run_id"]))
			if e != nil {
				return e
			}
			rid := str(run["id"])
			a.mu.Lock()
			proc := a.partnerProcesses[rid]
			a.mu.Unlock()
			if proc == nil {
				if kind == "partner.stop" && str(run["state"]) == "QUEUED" {
					run["state"] = "CANCELLED"
					if e = putPartner(s, "run", run); e != nil {
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
			if kind == "partner.stop" {
				action = map[string]any{"action": "stop"}
				run["state"] = "STOP_REQUESTED"
				run["stop_requested"] = true
			} else if kind == "partner.approve" {
				action = map[string]any{"action": "approval", "request_id": p["request_id"], "decision": p["decision"]}
			}
			if e = putPartner(s, "run", run); e != nil {
				return e
			}
			receipt = map[string]any{"run_id": rid, "state": run["state"]}
			after = func() {
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
			if str(p["resolution"]) == "close_local_tracking" {
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
			if e = putPartner(s, "run", m); e != nil {
				return e
			}
			receipt = map[string]any{"run_id": m["id"], "state": m["state"], "local_exit_confirmed": localExit, "remote_state": "UNKNOWN", "no_resend": true}
		case "component.reconcile":
			op, e := partnerObject(s, "operation", str(p["operation_id"]))
			if e != nil {
				return e
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
		result, err = manager.Prepare(ctx, str(op["partner_id"]), str(op["version"]))
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
		return putPartner(s, "run", m)
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
		return putPartner(s, "run", m)
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
	config := map[string]any{"partner_id": run["partner_id"], "route": connection["route"], "mode": run["mode"], "entry": release["entry"], "home": filepath.Join(a.Profile.UserRoot, "providers", str(run["partner_id"]), "accounts", str(run["account_id"])), "workspace": run["workspace"], "model": connection["model"], "prompt": run["prompt"], "options": connection["options"], "run_id": rid, "instance_id": a.Profile.InstanceID, "owner_epoch": a.Epoch, "base_url": "https://api.deepseek.com"}
	for _, k := range []string{"account_id", "secret_version", "connection_id", "connection_revision", "session_id"} {
		config[k] = run[k]
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
	cmd.Stderr = &partnerDiagnostic{a: a, id: rid, key: key}
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
		if key != "" {
			line = strings.ReplaceAll(line, key, "[redacted]")
		}
		var event map[string]any
		if e = json.Unmarshal([]byte(line), &event); e != nil {
			event = map[string]any{"type": "diagnostic", "text": line}
		}
		if e = a.partnerEvent(rid, event); e != nil {
			cancel()
			break
		}
	}
	_ = in.Close()
	waitErr := owner.Wait()
	if scanner.Err() != nil {
		waitErr = scanner.Err()
	}
	_ = a.write(func(s *storage.Session) error {
		m, e := partnerObject(s, "run", rid)
		if e != nil {
			return e
		}
		m["owned_process"] = owner.Identity()
		if !owner.ExitConfirmed() {
			m["state"] = "STOP_UNKNOWN"
			m["error"] = "本地子进程树退出尚未核对；保留原运行"
			return putPartner(s, "run", m)
		}
		if str(m["state"]) == "RESULT_RECEIVED" && waitErr == nil {
			m["state"] = "COMPLETED"
			if str(m["mode"]) == "run" {
				c, e := partnerObject(s, "connection", str(m["connection_id"]))
				if e == nil && number(c["revision"]) == number(m["connection_revision"]) {
					c["state"] = "VERIFIED"
					c["verified_run"] = rid
					c["verified_at"] = now()
					if e = putPartner(s, "connection", c); e != nil {
						return e
					}
				}
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
		return putPartner(s, "run", m)
	})
}

type partnerDiagnostic struct {
	a       *App
	id, key string
}

func (p *partnerDiagnostic) Write(b []byte) (int, error) {
	text := string(b)
	if p.key != "" {
		text = strings.ReplaceAll(text, p.key, "[redacted]")
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
		}
		if typ == "approval" || typ == "input" {
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
					m["pending_requests"] = append(pending, map[string]any{"id": requestID, "kind": typ, "message": event["message"], "options": event["options"]})
				}
			}
		}
		if session := str(event["session_id"]); session != "" {
			m["session_id"] = session
		}
		if str(m["mode"]) != "run" && (typ == "result" || typ == "error") {
			account, e := partnerObject(s, "account", str(m["account_id"]))
			if e != nil {
				return e
			}
			if event["authenticated"] == true {
				account["state"] = "AUTHENTICATED"
			} else {
				account["state"] = "LOGIN_NOT_CONFIRMED"
			}
			account["inspection"] = event
			if e = putPartner(s, "account", account); e != nil {
				return e
			}
		}

		return putPartner(s, "run", m)
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
