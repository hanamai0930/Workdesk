"""Build fixed official-runtime launches without reading user credentials."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import uuid

from .providers import validate_binding
from .permissions import checked as checked_permission

PARTNERS = ("codex", "deepseek", "qwen", "claude", "grok", "gemini", "kimi", "glm")


@dataclass
class LaunchSpec:
    argv: list[str]
    env: dict[str, str]
    cwd: str
    protocol: str
    partner_id: str
    route: str
    mode: str
    source: str
    limitations: list[str] = field(default_factory=list)


def _text(value):
    return json.dumps(str(value), ensure_ascii=False)


def _write(path: Path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    temp.write_text(json.dumps(content, ensure_ascii=False, indent=2) if isinstance(content, dict) else content, encoding="utf-8")
    if os.name != "nt":
        temp.chmod(0o600)
    temp.replace(path)


def _environment(home: Path, entry: list[str]):
    allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP", "PROGRAMFILES", "PROGRAMFILES(X86)",
               "PROGRAMDATA", "PROCESSOR_ARCHITECTURE", "OS", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "AppData/Roaming"),
               LOCALAPPDATA=str(home / "AppData/Local"), NO_COLOR="1")
    env["PATH"] = str(Path(entry[0]).parent) + os.pathsep + env.get("PATH", "")
    env["NO_PROXY"] = ",".join(filter(None, [env.get("NO_PROXY", ""), "127.0.0.1", "localhost"]))
    env["CODEX_HOME"] = str(home / ".codex")
    env["CLAUDE_CONFIG_DIR"] = str(home / ".claude")
    env["GROK_HOME"] = str(home / ".grok")
    env["KIMI_CODE_HOME"] = str(home / ".kimi-code")
    env["DSH_HOME"] = str(home / ".dsh")
    env["ZCODE_DATA_BASE_DIR"] = str(home)
    return env


def _file_uri(path: Path):
    helper_path = str(path.resolve())
    # Frozen Windows snapshots use the extended-length path prefix.
    if helper_path.startswith("\\\\?\\"):
        helper_path = helper_path[4:]
    return Path(helper_path).as_uri()


def _module_uri(name: str):
    return _file_uri(Path(__file__).with_name(name))


def _deepseek_web_observer(home: Path):
    # Web's official CLI has no --patch switch. Its profile loader automatically
    # reads this user layer. Keep original user YAML bytes and edit only our own
    # entry; compose inspects syntax without constructing !!js/custom values.
    import yaml
    helper = home / ".dsh/.workdesk/login-observer.mjs"
    _write(helper, Path(__file__).with_name("deepseek_login.mjs").read_text(encoding="utf-8"))
    uri = _file_uri(helper)
    path = home / ".dsh/profiles/web/cordis.patch.yml"
    original = path.read_bytes().decode("utf-8") if path.exists() else ""
    def write_patch(text):
        # Text-mode I/O would normalize or duplicate Windows CRLF sequences.
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name("." + path.name + "." + uuid.uuid4().hex)
        temp.write_bytes(text.encode("utf-8"))
        if os.name != "nt":
            temp.chmod(0o600)
        temp.replace(path)
    document = yaml.compose(original, Loader=yaml.SafeLoader)
    if document is not None and not isinstance(document, yaml.SequenceNode):
        raise ValueError("The official DeepSeek Web profile patch must be a patch list")
    for patch in document.value if document else []:
        if not isinstance(patch, yaml.MappingNode):
            continue
        for key, inserts in patch.value:
            if key.value != "insert" or not isinstance(inserts, yaml.SequenceNode):
                continue
            for entry in inserts.value:
                if not isinstance(entry, yaml.MappingNode):
                    continue
                fields = {key.value: value for key, value in entry.value}
                if fields.get("id") and fields["id"].value == "workdesk-login-state":
                    name = fields.get("name")
                    if not isinstance(name, yaml.ScalarNode):
                        raise ValueError("The Workdesk DeepSeek login observer entry is invalid")
                    if name.value != uri:
                        write_patch(original[:name.start_mark.index] + json.dumps(uri) + original[name.end_mark.index:])
                    return
    patch = json.dumps({"insert": [{"id": "workdesk-login-state", "name": uri}]})
    if document is not None and document.flow_style:
        position = document.end_mark.index - 1
        updated = original[:position] + (", " if document.value else "") + patch + original[position:]
    else:
        position = document.end_mark.index if document is not None else len(original)
        updated = original[:position] + "\n# Workdesk official login observer\n- " + patch + "\n" + original[position:]
    write_patch(updated)


def build_launch(config: dict) -> LaunchSpec:
    partner, mode, route = config["partner_id"], config.get("mode", "run"), config.get("route", "account")
    if partner not in PARTNERS or mode not in {"run", "login", "inspect"} or route not in {"api", "account"}:
        raise ValueError("Unknown official partner, route or mode")
    permission = checked_permission(config) if mode == "run" else None
    entry = config["entry"]
    if not isinstance(entry, list) or not entry or any(not isinstance(v, str) or not v or "\0" in v for v in entry):
        raise ValueError("A fixed official component argv is required")
    if not Path(entry[0]).is_absolute() or not Path(entry[0]).is_file() or Path(entry[0]).suffix.lower() in {".cmd", ".bat", ".ps1"}:
        raise ValueError("The official entry must be an existing absolute executable, not a shell script")
    home, workspace = Path(config["home"]), Path(config["workspace"])
    if not home.is_absolute() or not workspace.is_absolute() or not workspace.is_dir():
        raise ValueError("Home and existing workspace must be absolute")
    home.mkdir(parents=True, exist_ok=True)
    # Account-mode startup also needs the official home to exist. API preset
    # writers happened to create it; login/inspection must prepare it explicitly.
    native_folder = {"codex": ".codex", "claude": ".claude", "qwen": ".qwen", "gemini": ".gemini",
                     "kimi": ".kimi-code", "grok": ".grok", "deepseek": ".dsh", "glm": ".zcode"}[partner]
    (home / native_folder).mkdir(parents=True, exist_ok=True)
    env = _environment(home, entry)
    options = config.get("options") or {}
    auth_options = config.get("auth_options") or {}
    allowed_auth = {"codex": {"auth_method": {"chatgpt", "chatgptDeviceCode"}},
                   "claude": {"auth_method": {"claudeai", "console", "sso"}},
                   "grok": {"auth_method": {"device_auth", "oauth"}},
                   "qwen": {"auth_method": {"qwen-oauth"}},
                   "gemini": {"auth_method": {"oauth-personal"}},
                   "kimi": {"region": {"mainland-cn", "global"}},
                   "glm": {"provider": {"bigmodel", "zai"}}, "deepseek": {}}[partner]
    if not isinstance(auth_options, dict) or any(k not in allowed_auth or v not in allowed_auth[k] for k, v in auth_options.items()):
        raise ValueError("Unsupported official account option")
    # Optional shell selection is an explicit dependency reference, not PATH discovery.
    if options.get("shell_path"):
        shell = Path(options["shell_path"])
        if not shell.is_absolute() or not shell.is_file():
            raise ValueError("The configured shell dependency is missing")
        env.update(CLAUDE_CODE_GIT_BASH_PATH=str(shell), KIMI_SHELL_PATH=str(shell))
    argv, protocol, limitations = list(entry), "acp", []
    model = config.get("model")
    url, token = config.get("gateway_url"), config.get("gateway_token")
    if route == "api" and mode == "run":
        api = validate_binding(config)
        api_id, api_name = api["id"], api["name"]
        model_meta = next((row for row in api["models"] if row["id"] == model), {})
        if not model or not url or not token or not url.startswith("http://127.0.0.1:"):
            raise ValueError("API runs require the frozen model and a running bound loopback gateway")
        if partner == "codex":
            provider = "workdesk_" + api_id
            config_text = '\n'.join([
                'model = ' + _text(model), 'model_provider = ' + _text(provider),
                'forced_login_method = "api"',
                '[model_providers.' + provider + ']', 'name = ' + _text(api_name),
                'base_url = ' + _text(url), 'wire_api = "responses"',
                'env_key = "WORKDESK_RUN_TOKEN"', 'requires_openai_auth = false',
                'request_max_retries = 0', 'stream_max_retries = 0', ''])
            _write(home / ".codex/config.toml", config_text)
            env["WORKDESK_RUN_TOKEN"] = token
        elif partner == "deepseek":
            if api_id == "deepseek":
                env.update(DEEPSEEK_API_KEY=token, DEEPSEEK_BASE_URL=url)
            else:
                env["WORKDESK_RUN_TOKEN"] = token
                overlay = home / ".dsh/.workdesk/api-provider.patch.yml"
                _write(overlay, json.dumps([{"id": "llm-pi-ai", "config": {"providers": {"workdesk_api_" + api_id: {
                    "apiKeyEnv": "WORKDESK_RUN_TOKEN", "displayName": api_name,
                    "api": "openai-completions", "baseURL": url + "/v1", "models": [{"id": model, "name": model,
                    **({"contextWindow": model_meta["context_tokens"]} if model_meta.get("context_tokens") else {})}],
                    "compat": {"supportsUsageInStreaming": True}}}}}]))
        elif partner == "qwen":
            env.update(OPENAI_API_KEY=token, OPENAI_BASE_URL=url + "/v1", OPENAI_MODEL=model)
            _write(home / ".qwen/settings.json", {"security": {"auth": {"selectedType": "openai"}},
                   "model": {"name": model}, "modelProviders": {"openai": [{"id": model, "name": api_name + " via Workdesk",
                   "baseUrl": url + "/v1", "envKey": "OPENAI_API_KEY"}]}})
        elif partner == "claude":
            env.update(ANTHROPIC_BASE_URL=url + "/anthropic", ANTHROPIC_AUTH_TOKEN=token,
                       ANTHROPIC_MODEL=model, ANTHROPIC_DEFAULT_OPUS_MODEL=model,
                       ANTHROPIC_DEFAULT_SONNET_MODEL=model, ANTHROPIC_DEFAULT_HAIKU_MODEL=model,
                       ANTHROPIC_DEFAULT_FABLE_MODEL=model,
                       CLAUDE_CODE_SUBAGENT_MODEL=model)
        elif partner == "grok":
            native_id = "workdesk_" + api_id
            _write(home / ".grok/config.toml", '\n'.join(['[models]', 'default = ' + _text(native_id),
                   '[model.' + native_id + ']', 'model = ' + _text(model), 'base_url = ' + _text(url + "/v1"),
                   'env_key = "WORKDESK_RUN_TOKEN"', 'api_backend = "chat_completions"', '']))
            env["WORKDESK_RUN_TOKEN"] = token
        elif partner == "gemini":
            env.update(GOOGLE_GEMINI_BASE_URL=url, GEMINI_API_KEY=token)
            settings_path = home / ".gemini/settings.json"
            settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
            settings.setdefault("security", {}).setdefault("auth", {})["selectedType"] = "gemini-api-key"
            settings.setdefault("model", {})["name"] = model
            # Official customAliases replace a built-in alias as a whole. The
            # 0.71.1 chat-base preset includes topK=64, which DeepSeek cannot
            # represent. Configure that default out before native serialization;
            # preserve explicit user aliases/overrides so unsupported topK is
            # still rejected by the gateway rather than silently discarded.
            if api_id != "gemini":
                aliases = settings.setdefault("modelConfigs", {}).setdefault("customAliases", {})
                if api_id == "deepseek":
                    aliases.setdefault("chat-base", {"extends": "base", "modelConfig": {"generateContentConfig": {
                        "thinkingConfig": {"includeThoughts": True}, "temperature": 1, "topP": 0.95}}})
                else:
                    # Official presets can configure defaults out before native
                    # serialization. Explicit user aliases are left intact and
                    # unsupported choices are rejected by the wire adapter.
                    aliases.setdefault("base", {"modelConfig": {"generateContentConfig": {}}})
                    for alias, parent in (("chat-base", "base"), ("chat-base-2.5", "chat-base"), ("chat-base-3", "chat-base")):
                        aliases.setdefault(alias, {"extends": parent, "modelConfig": {"generateContentConfig": {}}})
            _write(settings_path, settings)
            if api_id != "gemini":
                limitations.append("GEMINI_NATIVE_VENDOR_SAMPLING_DEFAULTS_CONFIGURED_FOR_API_BRIDGE; EXPLICIT_UNSUPPORTED_OPTIONS_REJECTED")
                limitations += ["Google-hosted search/embedding are not substituted", "Token preflight is a labelled UTF-8 byte estimate"]
        elif partner == "kimi":
            native_id = "workdesk-" + api_id
            _write(home / ".kimi-code/config.toml", '\n'.join(['default_model = ' + _text(native_id),
                   '[providers.' + native_id + ']', 'type = "openai"', 'base_url = ' + _text(url + "/v1"),
                   'api_key_env = "WORKDESK_RUN_TOKEN"', '[models.' + native_id + ']',
                   'provider = ' + _text(native_id), 'model = ' + _text(model),
                   'max_context_size = ' + str(int(options.get("context_tokens", model_meta.get("context_tokens", 1048576)))), '']))
            env["WORKDESK_RUN_TOKEN"] = token
            if not model_meta.get("context_tokens") and not options.get("context_tokens"):
                limitations.append("CUSTOM_API_MODEL_CONTEXT_USES_NATIVE_DEFAULT; PROVIDER_LIMIT_UNVERIFIED")
        elif partner == "glm":
            native_id = "workdesk_" + api_id
            _write(home / ".zcode/v2/provider_config.json", {"schemaVersion": 1, "config": {
                "providerConfigRules": {"providerRules": [{"providerId": native_id, "providerName": api_name + " via Workdesk",
                    "config": {"group": "standard-personal", "access": {"type": "api-key", "apiKey": token},
                               "api": {"type": "openai-chat-completions", "baseUrl": url + "/v1"}, "personalModelIds": [model]}}]},
                "modelConfigRules": {"providerModelRules": [], "manualProviderModelRules": []},
                "defaultModelSelection": {"providerId": native_id, "modelId": model}}})
    if partner == "codex":
        argv += ["app-server", "--listen", "stdio://"]
        protocol = "codex"
    elif partner == "deepseek":
        if mode == "login":
            argv += ["--profile", "web", "--host", "127.0.0.1", "--port", "0", "--no-open"]
            _deepseek_web_observer(home)
            env["WORKDESK_AUTH_EVENT_TOKEN"] = uuid.uuid4().hex
            protocol = "login-text"
            limitations.append("Official Web profile provides account login; process stays open until explicitly stopped")
        else:
            argv += ["--profile", "sdk"]
            protocol = "deepseek"
            if route == "api" and mode == "run" and api_id != "deepseek":
                argv += ["--patch", str(home / ".dsh/.workdesk/api-provider.patch.yml")]
            if route == "account":
                overlay = home / ".dsh/.workdesk/account-selection.patch.yml"
                _write(overlay, json.dumps([{"insert": [{"id": "workdesk-account-selection",
                    "name": _module_uri("deepseek_account.mjs")}]}]))
                argv += ["--patch", str(overlay)]
    elif partner == "qwen":
        argv += ["--acp"]
    elif partner == "claude":
        if mode == "login":
            argv += ["auth", "login"]
            if auth_options.get("auth_method"):
                argv += ["--" + auth_options["auth_method"]]
            protocol = "login-text"
        elif mode == "inspect":
            argv += ["auth", "status", "--json"]
            protocol = "auth-status"
        else:
            argv += ["--print", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--permission-prompt-tool", "stdio"]
            argv += ["--permission-mode", permission["native"]["permission_mode"], "--permission-prompts", "host"]
            if permission["value"] == "full":
                argv += ["--allow-dangerously-skip-permissions"]
            if config.get("resume") and config.get("session_id"):
                argv += ["--resume=" + config["session_id"]]
            elif config.get("session_id"):
                argv += ["--session-id=" + config["session_id"]]
            protocol = "claude"
    elif partner == "grok":
        if mode == "login":
            argv += ["login", "--oauth" if auth_options.get("auth_method") == "oauth" else "--device-auth"]
            protocol = "login-text"
        else:
            argv += ["agent", "--no-leader", *permission["native"]["argv"], "stdio"] if permission else ["agent", "--no-leader", "stdio"]
    elif partner == "gemini":
        argv += ["--experimental-acp"]
        if model and mode == "run":
            argv += ["--model", model]
    elif partner == "kimi":
        if mode == "login":
            # The installed server advertises terminal-auth: run its exact
            # official login command, rather than inventing authenticate RPC.
            argv += ["login"]
            if auth_options.get("region"):
                argv += ["--region", auth_options["region"]]
            protocol = "login-text"
        else:
            argv += ["acp"]
    elif partner == "glm":
        if mode == "login":
            argv += ["login", auth_options.get("provider", "zai"), "--no-browser", "--json"]
            protocol = "login-text"
        else:
            argv += ["app-server"]
            protocol = "zcode"
    if partner == "deepseek" and mode == "run":
        native = permission["native"]
        overlay = home / ".dsh/.workdesk/permissions.patch.yml"
        _write(overlay, json.dumps([{"id": "sandbox-policy", "config": {"mode": native["sandbox"]}},
                                   {"id": "approval", "config": {"policy": native["approval"]}},
                                   {"id": "permission", "config": {"defaultPreset": native["preset"]}}]))
        argv += ["--patch", str(overlay)]
    return LaunchSpec(argv, env, str(workspace), protocol, partner, route, mode,
                      "OFFICIAL_NATIVE_RUNTIME", limitations)
