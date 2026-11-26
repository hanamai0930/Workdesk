package app

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
)

func (a *App) partnerPermissionCatalog() ([]map[string]any, string, error) {
	raw, err := os.ReadFile(filepath.Join(a.pythonSources(), "researchos", "workdesk", "partners", "permissions.json"))
	if err != nil || len(raw) > 1024*1024 {
		return nil, "", errors.New("当前冻结版本缺少准确权限目录")
	}
	var catalog struct {
		Schema   int              `json:"schema"`
		Partners []map[string]any `json:"partners"`
	}
	if json.Unmarshal(raw, &catalog) != nil || catalog.Schema != 1 || len(catalog.Partners) != 8 {
		return nil, "", errors.New("当前冻结权限目录格式无效")
	}
	digest := sha256.Sum256(raw)
	return catalog.Partners, hex.EncodeToString(digest[:]), nil
}

func resolvePartnerPermission(rows []map[string]any, hash, partner, value string) (map[string]any, error) {
	for _, row := range rows {
		if str(row["partner_id"]) != partner {
			continue
		}
		for _, mode := range connectionObjects(row["modes"]) {
			if str(mode["id"]) != value {
				continue
			}
			if mode["supported"] != true {
				return nil, errors.New(str(mode["reason"]))
			}
			return map[string]any{"partner_id": partner, "value": value, "native": mode["native"], "catalog_sha256": hash}, nil
		}
	}
	return nil, errors.New("所选官方工作端不支持此权限方式")
}

func (a *App) freezePartnerPermission(partner string, connection, payload map[string]any) (map[string]any, string, error) {
	rows, hash, err := a.partnerPermissionCatalog()
	if err != nil {
		return nil, "", err
	}
	value, source := "workspace", "SAFE_DEFAULT"
	raw, readErr := os.ReadFile(filepath.Join(a.Profile.UserRoot, "config", "desktop", "preferences.json"))
	if readErr == nil {
		var prefs struct {
			Schema int `json:"schema"`
			Values struct {
				WorkAccess map[string]string `json:"workAccess"`
			} `json:"values"`
		}
		if len(raw) > 1024*1024 || json.Unmarshal(raw, &prefs) != nil || prefs.Schema != 1 {
			return nil, "", errors.New("权限偏好无法准确读取，请先在设置中恢复")
		}
		if mode, ok := prefs.Values.WorkAccess[partner]; ok {
			value, source = mode, "DESKTOP_DEFAULT"
		}
	} else if !os.IsNotExist(readErr) {
		return nil, "", readErr
	}
	if options, ok := connection["options"].(map[string]any); ok {
		if mode, exists := options["work_access"]; exists {
			value, source = str(mode), "CONNECTION"
		}
	}
	if mode, exists := payload["work_access"]; exists {
		value, source = str(mode), "COMMAND"
	}
	if value == "full" && payload["confirm_full_access"] != true {
		return nil, "", errors.New("完全访问须在本次工作中明确确认")
	}
	permission, err := resolvePartnerPermission(rows, hash, partner, value)
	return permission, source, err
}
