package app

import (
	"context"
	"errors"
)

func connectionObjects(value any) []map[string]any {
	if rows, ok := value.([]map[string]any); ok {
		return rows
	}
	rows := []map[string]any{}
	if items, ok := value.([]any); ok {
		for _, item := range items {
			if row, ok := item.(map[string]any); ok {
				rows = append(rows, row)
			}
		}
	}
	return rows
}

func connectionFlags(value any) map[string]bool {
	out := map[string]bool{}
	switch values := value.(type) {
	case map[string]bool:
		for key, state := range values {
			out[key] = state
		}
	case map[string]any:
		for key, state := range values {
			out[key] = state == true
		}
	}
	return out
}

// The original launcher and the settings page observe the same account facts.
// Website sign-in confirmations remain independent from native Work login.
func mergeConnectionSetup(legacy, snapshot map[string]any) map[string]any {
	out := map[string]any{}
	for key, value := range legacy {
		out[key] = value
	}
	connections := connectionFlags(legacy["connections"])
	work := connectionFlags(legacy["work_accounts"])
	chat := connectionFlags(legacy["chat_accounts"])
	api := connectionFlags(legacy["api_connections"])
	login := map[string]string{}
	partners := connectionObjects(snapshot["partners"])
	for _, partner := range partners {
		id := str(partner["id"])
		if _, ok := work[id]; !ok {
			work[id] = false
		}
		if _, ok := chat[id]; !ok {
			chat[id] = false
		}
		if _, ok := connections[id]; !ok {
			connections[id] = false
		}
		login[id] = "connect"
	}
	for _, provider := range connectionObjects(snapshot["api_providers"]) {
		id := str(provider["id"])
		if _, ok := api[id]; !ok {
			api[id] = false
		}
	}
	accounts := map[string]map[string]any{}
	seenOfficial := map[string]bool{}
	for _, account := range connectionObjects(snapshot["accounts"]) {
		if account["kind"] == "official_login" {
			id := str(account["partner_id"])
			if !seenOfficial[id] {
				work[id] = false
				seenOfficial[id] = true
			}
		}
	}
	for _, account := range connectionObjects(snapshot["accounts"]) {
		accounts[str(account["id"])] = account
		if account["kind"] == "official_login" && account["state"] != "DISCONNECTED" && (account["auth_state"] == "AUTHENTICATED" || account["state"] == "AUTHENTICATED") {
			work[str(account["partner_id"])] = true
		}
	}
	apiByPartner := map[string]map[string]bool{}
	for _, connection := range connectionObjects(snapshot["connections"]) {
		if connection["route"] != "api" || connection["state"] == "DISABLED" {
			continue
		}
		account := accounts[str(connection["account_id"])]
		if account == nil || account["partner_id"] != connection["partner_id"] || account["state"] == "DISCONNECTED" || account["key_ready"] != true {
			continue
		}
		provider := str(connection["api_provider"])
		if provider == "" {
			provider = str(connection["driver_provider"])
		}
		if provider == "" {
			provider = str(account["api_provider"])
		}
		if provider == "" && account["kind"] == "deepseek_api" {
			provider = "deepseek"
		}
		if provider == "" {
			continue
		}
		if account["kind"] == "deepseek_api" && provider != "deepseek" {
			continue
		}
		if declared := str(account["api_provider"]); declared != "" && declared != provider {
			continue
		}
		id := str(connection["partner_id"])
		if apiByPartner[id] == nil {
			apiByPartner[id] = map[string]bool{}
		}
		apiByPartner[id][provider] = true
		api[provider] = true
		connections[id] = true
	}
	ready := legacy["ready"] == true
	for _, partner := range partners {
		id := str(partner["id"])
		connections[id] = connections[id] || work[id] || chat[id]
		ready = ready || connections[id]
	}
	out["ready"], out["account_available"] = ready, len(partners) > 0 || legacy["account_available"] == true
	out["connections"], out["work_accounts"], out["chat_accounts"], out["api_connections"] = connections, work, chat, api
	out["api_connections_by_partner"], out["work_login"] = apiByPartner, login
	out["partners"], out["api_providers"] = snapshot["partners"], snapshot["api_providers"]
	out["account_source"] = "VERSIONED_PARTNER_RECORDS_WITH_LEGACY_COMPATIBILITY"
	return out
}

func (a *App) connectionSetup(ctx context.Context) (any, error) {
	status, value, err := a.legacyRequest(ctx, "GET", "/api/v1/connection-setup", nil)
	if err != nil {
		return nil, err
	}
	legacy, ok := value.(map[string]any)
	if !ok || status != 200 {
		return nil, errors.New("暂时无法读取已有连接状态")
	}
	value, err = a.partnersView("/api/v2/partners")
	if err != nil {
		return nil, err
	}
	snapshot, ok := value.(map[string]any)
	if !ok {
		return nil, errors.New("伙伴连接状态格式无效")
	}
	return mergeConnectionSetup(legacy, snapshot), nil
}
