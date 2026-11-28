package app

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
)

func taskPartnerCommandID(action, task, attempt string) string {
	h := sha256.Sum256([]byte("partner-task:" + action + ":" + task + ":" + attempt))
	x := hex.EncodeToString(h[:16])
	return x[:8] + "-" + x[8:12] + "-4" + x[13:16] + "-a" + x[17:20] + "-" + x[20:]
}

func (a *App) partnerTaskParent(p map[string]any) (map[string]any, map[string]any, error) {
	rows, err := a.query(`SELECT t.id,t.spec,t.spec_hash,t.project_id,t.state AS task_state,a.id AS attempt_id,a.state AS attempt_state,a.workspace,a.stop_requested,a.worker_instance,a.baseline FROM tasks t JOIN attempts a ON a.task_id=t.id WHERE t.id=? AND a.id=?`, p["task_id"], p["attempt_id"])
	if err != nil || len(rows) != 1 {
		return nil, nil, errors.New("原任务与尝试无法准确核对")
	}
	parent := rows[0]
	if str(parent["spec_hash"]) == "" || parent["spec_hash"] != p["spec_hash"] {
		return nil, nil, errors.New("原任务版本已变化；未新建伙伴运行")
	}
	var spec map[string]any
	if json.Unmarshal([]byte(str(parent["spec"])), &spec) != nil || spec["execution"] != "partner_native" {
		return nil, nil, errors.New("原任务未冻结伙伴执行路线")
	}
	return parent, spec, nil
}

var partnerResumeKeys = []string{"resume_session", "resume_run_id", "resume_attempt_id"}

func samePartnerResume(a, b map[string]any) bool {
	for _, key := range partnerResumeKeys {
		if str(a[key]) != str(b[key]) {
			return false
		}
	}
	return true
}

func (a *App) partnerTaskResume(p, parent, binding, connection, account map[string]any, access string) (map[string]any, error) {
	var baseline, resume map[string]any
	if raw := str(parent["baseline"]); raw != "" {
		if json.Unmarshal([]byte(raw), &baseline) != nil {
			return nil, errors.New("本次尝试的预订记录无法核对")
		}
		resume, _ = baseline["partner_resume"].(map[string]any)
	}
	if !samePartnerResume(p, resume) {
		return nil, errors.New("原生续跑与本次预订记录不符，未派发")
	}
	rows, e := a.query(`SELECT id,state,workspace,session_id,stop_requested FROM attempts WHERE task_id=? AND id<>? ORDER BY started DESC,id DESC LIMIT 1`, p["task_id"], p["attempt_id"])
	if e != nil {
		return nil, e
	}
	if len(rows) == 0 && len(resume) == 0 {
		return nil, nil
	}
	if handoff, ok := baseline["partner_handoff"].(map[string]any); ok {
		if len(resume) != 0 || len(rows) != 1 || rows[0]["id"] != handoff["prior_attempt_id"] || rows[0]["session_id"] != handoff["prior_session_id"] || rows[0]["state"] != "COMPLETED" || number(rows[0]["stop_requested"]) != 0 || !samePartnerWorkspace(rows[0]["workspace"], parent["workspace"]) || binding["id"] != handoff["connection_id"] || fmt.Sprint(binding["revision"]) != fmt.Sprint(handoff["connection_revision"]) {
			return nil, errors.New("新模型接续记录与原尝试或所选连接不符")
		}
		old, err := a.boundPartnerTaskRun(map[string]any{"task_id": p["task_id"], "attempt_id": rows[0]["id"], "run_id": handoff["prior_run_id"]})
		if err != nil || old == nil || old["id"] != handoff["prior_run_id"] || old["session_id"] != rows[0]["session_id"] || old["state"] != "COMPLETED" || old["local_exit_confirmed"] != true || !samePartnerWorkspace(old["workspace"], parent["workspace"]) || (old["connection_id"] == binding["id"] && fmt.Sprint(old["connection_revision"]) == fmt.Sprint(binding["revision"])) {
			return nil, errors.New("原模型的完成回执与切换记录不符")
		}
		_, err = receivedPartnerTurnResult(old)
		return nil, err
	}
	release, e := a.partnerNativeRelease(binding)
	if e != nil {
		return nil, e
	}
	if len(rows) != 1 || release.Resume.Method == "" || !release.Resume.supports(binding, access) {
		return nil, errors.New("该准确原生会话的恢复尚未核实，未改为新会话")
	}
	prior := rows[0]
	if str(resume["resume_run_id"]) == "" || str(resume["resume_session"]) == "" || prior["id"] != resume["resume_attempt_id"] || prior["session_id"] != resume["resume_session"] || prior["state"] != "COMPLETED" || number(prior["stop_requested"]) != 0 || !samePartnerWorkspace(prior["workspace"], parent["workspace"]) {
		return nil, errors.New("原尝试状态、工作目录或原生会话不符，未改为新会话")
	}
	old, e := a.boundPartnerTaskRun(map[string]any{"task_id": p["task_id"], "attempt_id": prior["id"], "run_id": resume["resume_run_id"]})
	if e != nil || old == nil || old["state"] != "COMPLETED" || old["local_exit_confirmed"] != true || old["session_id"] != resume["resume_session"] || old["partner_id"] != binding["provider"] || old["account_id"] != account["id"] || old["secret_version"] != account["secret_version"] || old["connection_id"] != connection["id"] || number(old["connection_revision"]) != number(connection["revision"]) || old["release_id"] != connection["release_id"] || !samePartnerWorkspace(old["workspace"], parent["workspace"]) {
		return nil, errors.New("原已完成工作、账号、凭据、版本或目录不符，未续跑")
	}
	oldConnection, _ := old["connection_snapshot"].(map[string]any)
	if oldConnection == nil || oldConnection["route"] != connection["route"] || oldConnection["model"] != connection["model"] || (connection["route"] == "api" && connectionAPIProvider(oldConnection) != connectionAPIProvider(connection)) {
		return nil, errors.New("原会话的路线、API 厂商或模型已变化，未续跑")
	}
	if _, e = receivedPartnerTurnResult(old); e != nil {
		return nil, e
	}
	return resume, nil
}

func samePartnerWorkspace(left, right any) bool {
	return str(left) != "" && str(right) != "" && strings.EqualFold(filepath.Clean(str(left)), filepath.Clean(str(right)))
}

func (a *App) boundPartnerTaskRun(p map[string]any) (map[string]any, error) {
	rows, err := a.query(`SELECT data FROM core_partner_objects WHERE kind='run' AND json_extract(data,'$.parent_task_id')=? AND json_extract(data,'$.parent_attempt_id')=?`, p["task_id"], p["attempt_id"])
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, nil
	}
	if len(rows) != 1 {
		return nil, errors.New("原尝试关联的伙伴运行不唯一，保留未知且不重发")
	}
	var run map[string]any
	if json.Unmarshal([]byte(str(rows[0]["data"])), &run) != nil {
		return nil, errors.New("原伙伴运行记录无法读取")
	}
	if (str(p["spec_hash"]) != "" && run["parent_spec_hash"] != p["spec_hash"]) || (str(p["run_id"]) != "" && run["id"] != p["run_id"]) {
		return nil, errors.New("伙伴运行与原尝试身份不符")
	}
	return run, nil
}

func (a *App) partnerTaskRead(run map[string]any, after int64) (any, error) {
	value, err := a.partnerRun(str(run["id"]))
	if err != nil {
		return nil, err
	}
	result := value.(map[string]any)
	events, err := a.partnerRunEvents(str(run["id"]), after)
	if err != nil {
		return nil, err
	}
	page := events.(map[string]any)
	result["events"], result["cursor"] = page["events"], page["cursor"]
	return result, nil
}

func (a *App) partnerTask(method string, p map[string]any) (any, error) {
	if method != "partner.task.poll" {
		a.admission.Lock()
		defer a.admission.Unlock()
	}
	parent, spec, err := a.partnerTaskParent(p)
	if err != nil {
		return nil, err
	}
	run, err := a.boundPartnerTaskRun(p)
	if err != nil {
		return nil, err
	}
	if method == "partner.task.start" {
		if run != nil {
			digest := sha256.Sum256([]byte(str(p["prompt"])))
			if run["parent_prompt_sha256"] != hex.EncodeToString(digest[:]) || !samePartnerResume(run, p) {
				return nil, errors.New("原尝试已登记不同消息；未重发")
			}
			return a.partnerTaskRead(run, 0)
		}
		if a.retiring.Load() {
			return nil, errors.New("核心正在停止，未接纳伙伴工作")
		}
		if err = a.verifySources(); err != nil {
			return nil, err
		}
		if number(parent["stop_requested"]) != 0 || (str(parent["attempt_state"]) != "SEND_INTENT" && str(parent["attempt_state"]) != "RUNNING") {
			return nil, errors.New("原尝试未处于可派发状态，未创建伙伴工作")
		}
		if parent["worker_instance"] != a.Process {
			return nil, errors.New("原尝试属于旧执行所有者；只核对原回执，不重新派发")
		}
		if !samePartnerWorkspace(p["workspace"], parent["workspace"]) {
			return nil, errors.New("运行目录与原尝试不符")
		}
		binding, _ := spec["partner_connection"].(map[string]any)
		if binding == nil {
			binding, _ = spec["execution_connection"].(map[string]any)
		}
		if binding == nil || binding["id"] != p["connection_id"] || fmt.Sprint(binding["revision"]) != fmt.Sprint(p["connection_revision"]) || binding["provider"] != spec["provider"] {
			return nil, errors.New("原任务的伙伴连接绑定不符")
		}
		current, err := a.partnerObjects("connection")
		if err != nil {
			return nil, err
		}
		var connection map[string]any
		for _, c := range current {
			if c["id"] == binding["id"] {
				connection = c
			}
		}
		if connection == nil || fmt.Sprint(connection["revision"]) != fmt.Sprint(binding["revision"]) {
			return nil, errors.New("原伙伴连接已修改，未改用其他配置")
		}
		if connection["partner_id"] != binding["provider"] || connection["route"] != binding["route"] || (connection["route"] == "api" && connectionAPIProvider(connection) != str(binding["api_provider"])) {
			return nil, errors.New("原官方端、账号路线或 API 厂商不符")
		}
		accounts, err := a.partnerObjects("account")
		if err != nil {
			return nil, err
		}
		var account map[string]any
		for _, c := range accounts {
			if c["id"] == connection["account_id"] {
				account = c
			}
		}
		if account == nil || account["id"] != binding["account_id"] || account["secret_version"] != binding["secret_version"] || connection["release_id"] != binding["release_id"] {
			return nil, errors.New("原账号、凭据或工作端版本已变化")
		}
		if connection["route"] == "account" && str(account["auth_state"]) != "AUTHENTICATED" && str(account["state"]) != "AUTHENTICATED" {
			return nil, errors.New("官方账号尚未确认登录，未发起模型工作")
		}
		authGeneration := account["auth_generation"]
		if authGeneration == nil {
			authGeneration = account["login_run_id"]
		}
		if connection["route"] == "account" && authGeneration != binding["auth_generation"] {
			return nil, errors.New("原官方登录身份已重新选择，请重新准备分工")
		}
		if str(binding["model"]) != "official-default" && binding["model"] != connection["model"] {
			return nil, errors.New("原模型绑定与保存的连接不符")
		}
		value := str(spec["access"])
		if value == "" || value == "inherit" {
			value = "workspace"
		}
		resume, err := a.partnerTaskResume(p, parent, binding, connection, account, value)
		if err != nil {
			return nil, err
		}
		prompt := str(p["prompt"])
		if strings.TrimSpace(prompt) == "" || len(prompt) > 2*1024*1024 {
			return nil, errors.New("原生工作消息无效")
		}
		digest := sha256.Sum256([]byte(prompt))
		link := map[string]any{"parent_task_id": p["task_id"], "parent_attempt_id": p["attempt_id"], "parent_spec_hash": p["spec_hash"], "parent_prompt_sha256": hex.EncodeToString(digest[:]), "workspace": parent["workspace"]}
		for key, value := range resume {
			link[key] = value
		}
		if spec["work_mode"] == "independent" && spec["interaction"] != "chat" && (binding["provider"] == "kimi" || binding["provider"] == "deepseek") {
			server := map[string]any{"config": map[string]any{"transport": "http"}}
			if a.checkPartnerTaskMCP([]any{server}, binding, value, parent["workspace"]) == nil {
				link["document_mcp"] = true
			}
		}
		if scene, _ := spec["scene_snapshot"].(map[string]any); scene != nil {
			if servers, _ := scene["mcp"].([]any); len(servers) != 0 {
				if err := a.checkPartnerTaskMCP(servers, binding, value, parent["workspace"]); err != nil {
					return nil, err
				}
				link["scene_mcp"] = servers
			}
		}
		command := map[string]any{"id": taskPartnerCommandID("start", str(p["task_id"]), str(p["attempt_id"])), "kind": "partner.run", "payload": map[string]any{"connection_id": binding["id"], "connection_revision": connection["revision"], "project_id": parent["project_id"], "prompt": prompt, "work_access": value, "confirm_full_access": value == "full"}}
		if _, err = a.partnerCommandWithParent(command, link); err != nil {
			return nil, err
		}
		run, err = a.boundPartnerTaskRun(p)
		if err != nil || run == nil {
			return nil, errors.New("伙伴启动回执未知，请核对同一尝试，不重发")
		}
	} else if method == "partner.task.stop" {
		if run == nil {
			return nil, errors.New("原伙伴启动结果尚未登记，不新建或重发")
		}
		if !terminalPartner(str(run["state"])) && str(run["state"]) != "STOP_REQUESTED" {
			_, err = a.partnerCommand(map[string]any{"id": taskPartnerCommandID("stop", str(p["task_id"]), str(p["attempt_id"])), "kind": "partner.stop", "payload": map[string]any{"run_id": run["id"]}})
			if err != nil {
				return nil, err
			}
		}
	} else if method != "partner.task.poll" {
		return nil, errors.New("伙伴任务接口不存在")
	}
	if run == nil {
		return nil, errors.New("原伙伴运行不存在，不创建替代运行")
	}
	return a.partnerTaskRead(run, number(p["after_seq"]))
}
