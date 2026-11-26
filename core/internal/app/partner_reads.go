package app

import (
	"encoding/json"
	"errors"
	"strings"
)

// Read decoration keeps both management and work views on the same run IDs.
func (a *App) decoratePartnerRuns(runs, accounts []map[string]any) error {
	if len(runs) == 0 {
		return a.decoratePartnerAccounts(accounts, nil)
	}
	marks := make([]string, len(runs))
	args := make([]any, len(runs))
	for i, run := range runs {
		marks[i] = "?"
		args[i] = run["id"]
	}
	counts, err := a.query(`SELECT run_id,COUNT(*) AS n FROM core_partner_events WHERE run_id IN (`+strings.Join(marks, ",")+`) AND json_extract(data,'$.kind')='model_request_intent' GROUP BY run_id`, args...)
	if err != nil {
		return err
	}
	byID := map[string]any{}
	for _, row := range counts {
		byID[str(row["run_id"])] = row["n"]
	}
	for _, run := range runs {
		run["model_request_intent_count"] = 0
		if n, ok := byID[str(run["id"])]; ok {
			run["model_request_intent_count"] = n
		}
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
			run["usage"] = result["usage"]
		}
		for _, account := range accounts {
			if account["id"] != run["account_id"] {
				continue
			}
			run["account_label"] = account["label"]
		}
	}
	return a.decoratePartnerAccounts(accounts, runs)
}

func (a *App) decoratePartnerAccounts(accounts, runs []map[string]any) error {
	for _, account := range accounts {
		for _, key := range []string{"login_url", "user_code", "instructions"} {
			delete(account, key)
		}
		if str(account["kind"]) != "official_login" {
			continue
		}
		var selected map[string]any
		id := str(account["login_run_id"])
		for _, run := range runs {
			if id != "" && run["id"] == id {
				selected = run
				break
			}
		}
		if selected == nil {
			query := `SELECT data FROM core_partner_objects WHERE kind='run' AND id=?`
			args := []any{id}
			if id == "" {
				query = `SELECT data FROM core_partner_objects WHERE kind='run' AND json_extract(data,'$.account_id')=? AND json_extract(data,'$.mode')='login' ORDER BY COALESCE(json_extract(data,'$.created'),updated) DESC LIMIT 1`
				args = []any{account["id"]}
			}
			rows, err := a.query(query, args...)
			if err != nil {
				return err
			}
			if len(rows) == 1 {
				if err = json.Unmarshal([]byte(str(rows[0]["data"])), &selected); err != nil {
					return err
				}
			}
		}
		if selected == nil || str(selected["mode"]) != "login" || selected["account_id"] != account["id"] || selected["partner_id"] != account["partner_id"] {
			continue
		}
		if expected := str(account["login_release_id"]); expected != "" && selected["release_id"] != expected {
			continue
		}
		account["login_run_id"], account["login_state"], account["login_release_id"] = selected["id"], selected["state"], selected["release_id"]
		if terminalPartner(str(selected["state"])) || selected["stop_requested"] == true || str(selected["auth_state"]) == "AUTHENTICATED" || account["authenticated_run"] == selected["id"] {
			continue
		}
		for _, key := range []string{"login_url", "user_code", "instructions"} {
			if selected[key] != nil {
				account[key] = selected[key]
			}
		}
	}
	return nil
}

func (a *App) partnerRun(id string) (any, error) {
	if id == "" || strings.Contains(id, "/") {
		return nil, errors.New("运行标识无效")
	}
	rows, err := a.query(`SELECT data FROM core_partner_objects WHERE kind='run' AND id=?`, id)
	if err != nil {
		return nil, err
	}
	if len(rows) != 1 {
		return nil, errors.New("所选运行不存在")
	}
	var run map[string]any
	if err = json.Unmarshal([]byte(str(rows[0]["data"])), &run); err != nil {
		return nil, err
	}
	accounts, err := a.partnerObjects("account")
	if err != nil {
		return nil, err
	}
	if err = a.decoratePartnerRuns([]map[string]any{run}, accounts); err != nil {
		return nil, err
	}
	return map[string]any{"run": run}, nil
}

// A Key change invalidates the current combination's verification, while the
// original result and credential version remain in its immutable run snapshot.
func decoratePartnerConnections(connections, accounts []map[string]any) {
	for _, connection := range connections {
		if str(connection["state"]) != "VERIFIED" || str(connection["route"]) != "api" {
			continue
		}
		for _, account := range accounts {
			if account["id"] != connection["account_id"] {
				continue
			}
			if str(account["state"]) == "DISCONNECTED" {
				connection["state"] = "DISCONNECTED"
			} else if str(connection["verified_secret_version"]) == "" || connection["verified_secret_version"] != account["secret_version"] {
				connection["state"] = "SAVED_UNVERIFIED"
				connection["verification_stale"] = true
			}
		}
	}
}
