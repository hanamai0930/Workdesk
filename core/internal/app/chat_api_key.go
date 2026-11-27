package app

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"sort"
	"strings"
	"time"
	"unicode"
	"workdesk/core/internal/storage"
)

// Key storage is addressed by an exact owner and version. The catalog and
// command ledger contain references only. Never emit key text or a key hash.
const chatKeyKind = "chat-api-key"
const chatKeySaveProof = "chat-api-key-save-proof"

func chatAPIProvider(id string) bool { return id == "deepseek" || id == "qwen" }

func chatKeyAccounts(provider string, rows []map[string]any) []map[string]any {
	out := []map[string]any{}
	for _, row := range rows {
		if accountAPIProvider(row) == provider && str(row["state"]) != "DISCONNECTED" && str(row["id"]) != "" {
			out = append(out, row)
		}
	}
	sort.Slice(out, func(i, j int) bool { return str(out[i]["id"]) < str(out[j]["id"]) })
	return out
}

func readyChatKeyAccount(row map[string]any) bool {
	return row["key_ready"] == true && uuidPattern.MatchString(str(row["secret_version"]))
}

func resolveChatKeyAccount(accounts []map[string]any, selection map[string]any, same func(a, b map[string]any) bool) map[string]any {
	if selection != nil {
		if str(selection["mode"]) != "work" {
			return nil
		}
		for _, a := range accounts {
			if a["id"] == selection["account_id"] && readyChatKeyAccount(a) {
				return a
			}
		}
		return nil
	}
	var chosen map[string]any
	for _, a := range accounts {
		if !readyChatKeyAccount(a) {
			continue
		}
		if chosen != nil && !same(chosen, a) {
			return nil
		}
		if chosen == nil {
			chosen = a
		}
	}
	return chosen
}

func readChatKeySelection(s *storage.Session, provider string) (map[string]any, error) {
	rows, err := s.Execute(`SELECT data FROM core_partner_objects WHERE kind=? AND id=?`, []any{chatKeyKind, provider})
	if err != nil || len(rows.Rows) == 0 {
		return nil, err
	}
	var selection map[string]any
	err = json.Unmarshal([]byte(str(rows.Rows[0][0])), &selection)
	return selection, err
}

func saveChatKeyAccount(s *storage.Session, a map[string]any, version string) error {
	a["secret_version"], a["key_ready"], a["state"] = version, true, "KEY_SAVED_UNVERIFIED"
	return putPartner(s, "account", a)
}

func (a *App) chatAPIKeyMutation(ctx context.Context, action string, in map[string]any) (map[string]any, error) {
	return a.mutateChatAPIKey(ctx, action, in, a.Legacy.Call)
}

func (a *App) mutateChatAPIKey(ctx context.Context, action string, in map[string]any, call func(context.Context, string, any, any) error) (map[string]any, error) {
	provider, id := str(in["provider"]), strings.ToLower(str(in["id"]))
	if !chatAPIProvider(provider) || !uuidPattern.MatchString(id) || len(in) != 3 {
		return nil, errors.New("请选择普通 Chat 的 API 厂商和有效操作身份")
	}
	var subject, providedKey string
	if action == "save" {
		providedKey = strings.TrimSpace(str(in["key"]))
		if providedKey == "" || len(providedKey) > 8192 || (strings.ContainsRune(providedKey, 0) || strings.ContainsFunc(providedKey, unicode.IsSpace)) ||
			(provider == "qwen" && (!strings.HasPrefix(providedKey, "sk-") || strings.HasPrefix(providedKey, "sk-sp-"))) {
			return nil, errors.New("请输入此厂商有效的通用 API Key")
		}
	} else if action == "reuse" {
		subject = strings.ToLower(str(in["account_id"]))
		if !uuidPattern.MatchString(subject) {
			return nil, errors.New("请选择准确 Work API 凭据引用")
		}
	} else if action == "select" {
		subject = strings.ToLower(str(in["source_account_id"]))
		if !uuidPattern.MatchString(subject) {
			return nil, errors.New("请选择同厂商准确的 Work Key 来源")
		}
	} else {
		return nil, errors.New("API 凭据操作不支持")
	}
	// The replay comparison is about the intent and the chosen reference. The
	// credential is not in this hash, command body or audit ledger.
	kind := "chat.api-key." + action
	digest := sha256.Sum256([]byte(provider + ":" + subject))
	intent := hex.EncodeToString(digest[:])
	var receipt map[string]any
	err := a.write(func(s *storage.Session) error {
		rows, err := s.Execute(`SELECT kind,payload_hash,receipt FROM commands WHERE id=?`, []any{id})
		if err != nil {
			return err
		}
		if len(rows.Rows) != 0 {
			prev := rows.Rows[0]
			if prev[0] != kind || prev[1] != intent {
				return errors.New("命令ID已对应其他操作")
			}
			if action == "save" {
				proof, err := partnerObject(s, chatKeySaveProof, id)
				if err != nil {
					return errors.New("原命令密钥版本暂无法核对，请保持原回执")
				}
				var compared map[string]any
				if e := call(ctx, "partner.secret.matches", map[string]any{"account_id": proof["account_id"], "version": proof["secret_version"], "key": providedKey}, &compared); e != nil || compared["matches"] != true {
					return errors.New("同一命令ID与原保存的 API Key 不符，未执行")
				}
			}
			if err := json.Unmarshal([]byte(str(prev[2])), &receipt); err != nil {
				return err
			}
			return nil
		}
		rows, err = s.Execute(`SELECT data FROM core_partner_objects WHERE kind='account'`, nil)
		if err != nil {
			return err
		}
		all := []map[string]any{}
		for _, row := range rows.Rows {
			var item map[string]any
			if err := json.Unmarshal([]byte(str(row[0])), &item); err != nil {
				return err
			}
			all = append(all, item)
		}
		accounts := chatKeyAccounts(provider, all)
		selection, err := readChatKeySelection(s, provider)
		if err != nil {
			return err
		}
		same := func(one, two map[string]any) bool {
			if one["secret_version"] == two["secret_version"] {
				return true
			}
			var facts map[string]any
			err := call(ctx, "partner.secret.same", map[string]any{"provider": provider, "account_id": one["id"], "version": one["secret_version"], "source_account_id": two["id"], "source_version": two["secret_version"]}, &facts)
			return err == nil && facts["matches"] == true
		}
		valid := func(owner, version any) bool {
			var facts map[string]any
			err := call(ctx, "partner.secret.valid-chat", map[string]any{"provider": provider, "account_id": owner, "version": version}, &facts)
			return err == nil && facts["available"] == true
		}
		selected := resolveChatKeyAccount(accounts, selection, same)
		// A v1 credential explicitly selected by Human stays separate. When
		// it is the current Chat key, reusing it must use that exact value.
		var legacyFacts map[string]any
		if selection == nil && action != "select" {
			legacyErr := call(ctx, "partner.secret.legacy-ready", map[string]any{"provider": provider}, &legacyFacts)
			if legacyErr != nil && action != "save" {
				return errors.New("原 Chat 凭据引用暂时不可读取，请核对后重试")
			}
			if legacyErr != nil || legacyFacts["available"] == true {
				selected = nil
			}
		}
		if selected != nil && !valid(selected["id"], selected["secret_version"]) {
			selected = nil
		}
		next := map[string]any{"id": provider, "provider": provider}
		if selection != nil {
			next["revision"] = selection["revision"]
		}
		updated := 0
		if action == "save" {
			// An explicit, unambiguous shared group may be rotated. Independent
			// versions are left alone, even when their provider is the same.
			group := []map[string]any{}
			if selected != nil {
				for _, item := range accounts {
					if readyChatKeyAccount(item) && same(item, selected) {
						if err := a.protectAccount(s, str(item["id"])); err != nil {
							return err
						}
						group = append(group, item)
					}
				}
			}
			owner := id // Standalone Chat versions have their own vault namespace.
			if selected != nil {
				owner = str(selected["id"])
			}
			if selection != nil && selection["mode"] == "chat" && uuidPattern.MatchString(str(selection["account_id"])) {
				owner = str(selection["account_id"])
			}
			// A second click with the same value should preserve existing Work
			// verification, not change the secret revision or cause a recheck.
			oldVersion := ""
			if selected != nil {
				oldVersion = str(selected["secret_version"])
			}
			if selection != nil && selection["mode"] == "chat" {
				oldVersion = str(selection["secret_version"])
			}
			same := false
			if oldVersion != "" {
				var compared map[string]any
				if e := call(ctx, "partner.secret.matches", map[string]any{"account_id": owner, "version": oldVersion, "key": providedKey}, &compared); e == nil {
					same = compared["matches"] == true
				}
			}
			if !same {
				var sealed map[string]any
				if e := call(ctx, "partner.secret.store", map[string]any{"account_id": owner, "version": id, "key": providedKey}, &sealed); e != nil {
					return errors.New("密钥保护保存未完成；保留原命令核对")
				}
				for _, item := range group {
					target := str(item["id"])
					if target != owner {
						var reused any
						if e := call(ctx, "partner.secret.reuse", map[string]any{"source_account_id": owner, "source_version": id, "account_id": target, "version": id}, &reused); e != nil {
							return errors.New("共用密钥保护保存未完成；保留原命令核对")
						}
					}
					if err := saveChatKeyAccount(s, item, id); err != nil {
						return err
					}
					updated++
				}
			}
			savedVersion := id
			if same {
				savedVersion = oldVersion
			}
			if e := putPartner(s, chatKeySaveProof, map[string]any{"id": id, "account_id": owner, "secret_version": savedVersion}); e != nil {
				return e
			}
			next["account_id"] = owner
			if selected == nil {
				next["mode"], next["secret_version"] = "chat", id
				if same {
					next["secret_version"] = oldVersion
				}
			} else {
				next["mode"] = "work"
			}
		} else if action == "select" {
			for _, item := range accounts {
				if item["id"] == subject && readyChatKeyAccount(item) && valid(item["id"], item["secret_version"]) {
					next["mode"], next["account_id"] = "work", subject
					break
				}
			}
			if next["mode"] == nil {
				return errors.New("该 Work 引用没有同厂商 Key，未调整 Chat")
			}
		} else {
			var target map[string]any
			for _, item := range accounts {
				if item["id"] == subject {
					target = item
					break
				}
			}
			if target == nil {
				return errors.New("该 Work 账号不属于所选 API 厂商")
			}
			if str(target["secret_version"]) != "" || target["key_ready"] == true {
				return errors.New("已保存独立 Key，未覆盖现有 Work 凭据")
			}
			if err := a.protectAccount(s, subject); err != nil {
				return err
			}
			owner, version := "", ""
			if selected != nil {
				owner, version = str(selected["id"]), str(selected["secret_version"])
			}
			if selection != nil && selection["mode"] == "chat" {
				owner, version = str(selection["account_id"]), str(selection["secret_version"])
			}
			var reused any
			if owner == "" || version == "" {
				if selection != nil {
					return errors.New("所选 Chat Key 已不可用，请重新配置")
				}
				version = id
				if e := call(ctx, "partner.secret.reuse-legacy", map[string]any{"provider": provider, "account_id": subject, "version": version}, &reused); e != nil {
					return errors.New("暂无确定同厂商 Chat Key 来源，请在 Chat API 中保存或选择已有 Work Key")
				}
			} else {
				if !valid(owner, version) {
					return errors.New("共用 Key 暂时无法读取，Work 引用未更改")
				}
				if e := call(ctx, "partner.secret.reuse", map[string]any{"source_account_id": owner, "source_version": version, "account_id": subject, "version": version}, &reused); e != nil {
					return errors.New("共用 Key 暂时无法读取，Work 引用未更改")
				}
			}
			if err := saveChatKeyAccount(s, target, version); err != nil {
				return err
			}
			updated = 1
			if selected != nil {
				subject = str(selected["id"])
			}
			next["mode"], next["account_id"] = "work", subject
		}
		if err := putPartner(s, chatKeyKind, next); err != nil {
			return err
		}
		receipt = map[string]any{"provider": provider, "ready": true, "state": "KEY_SAVED", "accounts_updated": updated}
		raw, err := json.Marshal(receipt)
		if err != nil {
			return err
		}
		_, err = s.Execute(`INSERT INTO commands VALUES(?,?,?,?,?)`, []any{id, kind, intent, string(raw), float64(time.Now().UnixMilli()) / 1000})
		return err
	})
	return receipt, err
}
