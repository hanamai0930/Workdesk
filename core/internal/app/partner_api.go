package app

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"unicode"
	"unicode/utf8"
	"workdesk/core/internal/components"
	"workdesk/core/internal/storage"
)

// This is a local, version-frozen directory, never a runtime discovery request.
func (a *App) partnerAPICatalog() ([]map[string]any, string, error) {
	path := filepath.Join(a.pythonSources(), "researchos", "workdesk", "partners", "api_providers.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, "", errors.New("当前冻结版本缺少 API 厂商目录，请完成安全版本更新")
	}
	if len(raw) > 1024*1024 {
		return nil, "", errors.New("冻结 API 厂商目录大小无效")
	}
	var catalog struct {
		Schema    int              `json:"schema"`
		Providers []map[string]any `json:"api_providers"`
	}
	if err = json.Unmarshal(raw, &catalog); err != nil || catalog.Schema != 1 || len(catalog.Providers) == 0 {
		return nil, "", errors.New("冻结 API 厂商目录格式无效")
	}
	seen := map[string]bool{}
	for _, provider := range catalog.Providers {
		id, model := str(provider["id"]), str(provider["default_model"])
		if id == "" || len(id) > 40 || strings.ContainsAny(id, "/\\\x00 \t\r\n") || seen[id] || str(provider["name"]) == "" || !validPartnerAPIModel(model) {
			return nil, "", errors.New("冻结 API 厂商目录身份或默认模型无效")
		}
		seen[id] = true
		models, ok := provider["models"].([]any)
		found := false
		for _, value := range models {
			if known, ok := value.(map[string]any); ok && str(known["id"]) == model {
				found = true
			}
		}
		if !ok || !found {
			return nil, "", errors.New("冻结 API 默认模型不在已知模型目录中")
		}
	}
	hash := sha256.Sum256(raw)
	return catalog.Providers, hex.EncodeToString(hash[:]), nil
}

func validPartnerAPIModel(model string) bool {
	if model == "" || !utf8.ValidString(model) || utf8.RuneCountInString(model) > 200 {
		return false
	}
	for _, char := range model {
		if !unicode.IsLetter(char) && !unicode.IsNumber(char) && !strings.ContainsRune("._:/@+-", char) {
			return false
		}
	}
	return true
}

// Older records keep their original kind and credential version. Their only
// possible upstream identity remains DeepSeek, even if a caller supplies a tag.
func accountAPIProvider(account map[string]any) string {
	switch str(account["kind"]) {
	case "deepseek_api":
		if provider := str(account["api_provider"]); provider == "" || provider == "deepseek" {
			return "deepseek"
		}
	case "api":
		return str(account["api_provider"])
	}
	return ""
}

func connectionAPIProvider(connection map[string]any) string {
	provider, driver := str(connection["api_provider"]), str(connection["driver_provider"])
	if provider != "" && driver != "" && provider != driver {
		return ""
	}
	if provider != "" {
		return provider
	}
	if driver != "" {
		return driver
	}
	// The pre-catalog format had exactly one API driver.
	if str(connection["route"]) == "api" {
		return "deepseek"
	}
	return ""
}

func (a *App) partnerAPIProvider(id string) (map[string]any, string, error) {
	providers, hash, err := a.partnerAPICatalog()
	if err != nil {
		return nil, "", err
	}
	for _, provider := range providers {
		if provider["id"] == id {
			return provider, hash, nil
		}
	}
	return nil, "", errors.New("请选择当前冻结目录中的 API 厂商")
}

func (a *App) bindPartnerAPI(partner string, account, connection map[string]any) error {
	providerID := accountAPIProvider(account)
	if providerID == "" {
		return errors.New("所选账号不是有效的 API 凭据引用")
	}
	for _, key := range []string{"api_provider", "driver_provider"} {
		if selected := str(connection[key]); selected != "" && selected != providerID {
			return errors.New("API 厂商与所选准确账号不符，请重新选择对应凭据")
		}
	}
	provider, hash, err := a.partnerAPIProvider(providerID)
	if err != nil {
		return err
	}
	compatibility, _ := provider["compatibility"].(map[string]any)
	capability, _ := compatibility[partner].(map[string]any)
	if str(capability["state"]) != "supported" {
		reason := str(capability["reason"])
		if reason == "" {
			reason = "当前冻结版本尚未接通该官方工作端与 API 协议组合"
		}
		return fmt.Errorf("%s：%s", str(provider["name"]), reason)
	}
	model := str(connection["model"])
	if model == "" {
		model = str(provider["default_model"])
	}
	if !validPartnerAPIModel(model) {
		return errors.New("模型名称须为 1—200 个字母、数字或 ._:/@+- 字符")
	}
	connection["api_provider"], connection["driver_provider"], connection["model"] = providerID, providerID, model
	connection["api_provider_snapshot"], connection["api_catalog_sha256"] = provider, hash
	return nil
}

func partnerAPICredentialTargets(s *storage.Session, selected map[string]any, all bool) ([]map[string]any, error) {
	if !all {
		return []map[string]any{selected}, nil
	}
	rows, err := s.Execute(`SELECT data FROM core_partner_objects WHERE kind='account' AND json_extract(data,'$.kind') IN ('deepseek_api','api') AND json_extract(data,'$.state')!='DISCONNECTED'`, nil)
	if err != nil {
		return nil, err
	}
	provider := accountAPIProvider(selected)
	targets := []map[string]any{}
	for _, row := range rows.Rows {
		var target map[string]any
		if err = json.Unmarshal([]byte(str(row[0])), &target); err != nil {
			return nil, err
		}
		if provider != "" && accountAPIProvider(target) == provider {
			targets = append(targets, target)
		}
	}
	return targets, nil
}

func partnerAPIVerification(providers, accounts, connections []map[string]any) map[string]any {
	statuses := map[string]any{}
	for _, provider := range providers {
		id := str(provider["id"])
		capabilities, _ := provider["compatibility"].(map[string]any)
		required := map[string]bool{}
		for _, partner := range components.Catalog() {
			capability, _ := capabilities[partner.ID].(map[string]any)
			if str(capability["state"]) == "supported" {
				required[partner.ID] = true
			}
		}
		state := "WAITING_HUMAN_KEY"
		for _, account := range accounts {
			if accountAPIProvider(account) == id && account["key_ready"] == true && account["state"] != "DISCONNECTED" {
				state = "PENDING_CHECKS"
			}
		}
		verified := map[string]bool{}
		for _, connection := range connections {
			if str(connection["route"]) == "api" && connectionAPIProvider(connection) == id && str(connection["state"]) == "VERIFIED" && required[str(connection["partner_id"])] {
				verified[str(connection["partner_id"])] = true
			}
		}
		if len(required) == 0 {
			state = "PROTOCOL_ISOLATED"
		} else if len(verified) == len(required) {
			state = "VERIFIED"
		}
		statuses[id] = map[string]any{"state": state, "verified_partners": len(verified), "required_partners": len(required), "isolated_partners": len(components.Catalog()) - len(required)}
	}
	return statuses
}
