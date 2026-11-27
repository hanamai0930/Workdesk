package app

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"slices"
)

type partnerNativeRule struct {
	Method          string   `json:"method"`
	Automatic       bool     `json:"automatic"`
	Routes          []string `json:"routes"`
	APIProviders    []string `json:"api_providers"`
	PermissionModes []string `json:"permission_modes"`
	Transports      []string `json:"transports"`
	StdioCWD        string   `json:"stdio_cwd"`
}

type partnerNativeRelease struct {
	Partner string            `json:"partner_id"`
	Version string            `json:"version"`
	Resume  partnerNativeRule `json:"resume"`
	MCP     partnerNativeRule `json:"mcp"`
}

func (r partnerNativeRule) supports(binding map[string]any, access string) bool {
	route := str(binding["route"])
	return slices.Contains(r.Routes, route) &&
		(route != "api" || len(r.APIProviders) == 0 || slices.Contains(r.APIProviders, str(binding["api_provider"]))) &&
		(len(r.PermissionModes) == 0 || access == "" || slices.Contains(r.PermissionModes, access))
}

func (a *App) partnerNativeRelease(binding map[string]any) (partnerNativeRelease, error) {
	raw, err := os.ReadFile(filepath.Join(a.pythonSources(), "researchos", "workdesk", "partners", "native_features.json"))
	if err != nil || len(raw) > 1024*1024 {
		return partnerNativeRelease{}, errors.New("缺少当前冻结原生能力目录")
	}
	var catalog struct {
		Schema   int                             `json:"schema"`
		Releases map[string]partnerNativeRelease `json:"releases"`
	}
	if json.Unmarshal(raw, &catalog) != nil || catalog.Schema != 1 || catalog.Releases == nil {
		return partnerNativeRelease{}, errors.New("无法核对当前冻结原生能力目录")
	}
	release := catalog.Releases[str(binding["release_id"])]
	if release.Partner != str(binding["provider"]) || release.Version != str(binding["version"]) {
		return partnerNativeRelease{}, nil
	}
	return release, nil
}

func (a *App) checkPartnerTaskMCP(servers []any, binding map[string]any, access string, workdir any) error {
	release, err := a.partnerNativeRelease(binding)
	if err != nil {
		return err
	}
	if !release.MCP.supports(binding, access) || len(release.MCP.Transports) == 0 {
		return errors.New("当前官方工作端版本、连接或权限尚未接入每任务 MCP，未派发")
	}
	for _, v := range servers {
		server, _ := v.(map[string]any)
		config, _ := server["config"].(map[string]any)
		transport := str(config["transport"])
		if !slices.Contains(release.MCP.Transports, transport) {
			return errors.New("当前官方工作端未核实这种每任务 MCP 传输方式，未派发")
		}
		if transport == "stdio" && release.MCP.StdioCWD == "project" && !samePartnerWorkspace(config["cwd"], workdir) {
			return errors.New("当前官方工作端的本机 MCP 须使用本次项目目录，未派发")
		}
	}
	return nil
}
