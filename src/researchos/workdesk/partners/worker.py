"""JSON-lines host for official Agents, never a model/tool execution loop.

The Go owner admits this process to its Job before supplying its first frame.
All subprocesses remain descendants; closing a stream is not remote-stop proof.
"""
from __future__ import annotations

import json
import codecs
import io
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import uuid

from .gateway import RunGateway
from .launch import build_launch, scene_mcp
from .permissions import checked as checked_permission, apply_acp, validate_codex_receipt
from .auth import LoginOutput, redact_login_chunk
from .providers import validate_binding

MAX_FRAME = 32 * 1024 * 1024
SENSITIVE_NAMES = {"api_key", "apikey", "access_token", "refresh_token", "authorization", "auth_token", "id_token", "password", "credential", "credentials"}


class NativeRejected(RuntimeError):
    pass


class ReceiptUnknown(RuntimeError):
    pass


class NativeRpc:
    def __init__(self, process, protocol, notify):
        self.process, self.protocol, self.notify = process, protocol, notify
        self.pending, self.lock, self.write_lock = {}, threading.Lock(), threading.Lock()
        self.closed = False
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def send(self, message):
        if self.protocol not in {"zcode", "codex", "claude"}:
            message = {"jsonrpc": "2.0", **message}
        raw = json.dumps(message, ensure_ascii=False).encode() + b"\n"
        if len(raw) > MAX_FRAME:
            raise ValueError("Outgoing native frame exceeds the transport limit")
        with self.write_lock:
            try:
                self.process.stdin.write(raw)
                self.process.stdin.flush()
            except (OSError, BrokenPipeError) as exc:
                raise ReceiptUnknown("Native send receipt unknown; do not resend") from exc

    def request(self, method, params=None, timeout=30):
        request_id = "workdesk-" + uuid.uuid4().hex
        waiter = queue.Queue(maxsize=1)
        with self.lock:
            if self.closed:
                raise ReceiptUnknown("Native transport is closed")
            self.pending[request_id] = waiter
        try:
            message = {"id": request_id, "method": method}
            if params is not None:
                message["params"] = params
            self.send(message)
            try:
                result = waiter.get(timeout=timeout)
            except queue.Empty as exc:
                raise ReceiptUnknown(method + ": receipt unknown; do not resend") from exc
            if isinstance(result, Exception):
                raise result
            if "error" in result:
                error = result["error"]
                raise NativeRejected(error.get("message", str(error)) if isinstance(error, dict) else str(error))
            return result.get("result", {})
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def _read(self):
        reason = ReceiptUnknown("Native protocol EOF; completion is unknown")
        try:
            while True:
                raw = self.process.stdout.readline(MAX_FRAME + 1)
                if not raw:
                    break
                if len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
                    raise ReceiptUnknown("Native frame oversized or incomplete")
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ReceiptUnknown("Native protocol did not return an object")
                with self.lock:
                    pending = self.pending.get(message.get("id")) if "method" not in message else None
                if pending is not None:
                    pending.put(message)
                else:
                    self.notify(message)
        except Exception as exc:
            reason = ReceiptUnknown("Native protocol failed: " + type(exc).__name__)
        finally:
            with self.lock:
                self.closed = True
                pending = list(self.pending.values())
            for waiter in pending:
                try:
                    waiter.put_nowait(reason)
                except queue.Full:
                    pass
            self.notify({"_transport_closed": True})


class PartnerWorker:
    def __init__(self, config, emit):
        self.config, self.output = dict(config), emit
        self.secret_values = [str(config.get("api_key", ""))]
        self.gateway = None
        self.process = None
        self.rpc = None
        self.spec = None
        self.permission = None
        self.session_id = config.get("session_id") or (("sess_" + uuid.uuid4().hex) if config.get("partner_id") == "glm" else str(uuid.uuid4()))
        self.thread_id = self.turn_id = None
        self.requests = {}
        self.pending_auth_methods = []
        self.done = threading.Event()
        self.stop_requested = threading.Event()
        self.completed = False
        self.failed = False
        self.last_text = []
        self.native_usage = None
        self.run_thread = None
        self.session_revision = None
        self.deepseek_account_selection = None
        self.deepseek_account_ready = threading.Event()
        self.login_output = None
        self.login_lock = threading.RLock()
        self.login_request = None
        self.login_output_suppressed = False
        self.stderr_thread = None
        self.codex_login_id = None
        self.codex_login_receipt = None
        self.codex_login_ready = threading.Event()

    def clean(self, value):
        if isinstance(value, dict):
            return {k: ("[REDACTED]" if k.lower() in SENSITIVE_NAMES else self.clean(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if isinstance(value, str):
            for secret in self.secret_values:
                if secret:
                    value = value.replace(secret, "[REDACTED]")
            return re.sub(r"\bsk-[A-Za-z0-9_-]{16,}", "[REDACTED]", value)
        return value

    def emit(self, **data):
        data.setdefault("partner_id", self.config["partner_id"])
        data.setdefault("run_id", self.config.get("run_id"))
        data.setdefault("session_id", self.session_id)
        self.output(self.clean(data))

    def start(self):
        if self.config.get("mode", "run") == "run":
            self.permission = checked_permission(self.config)
        if self.config.get("route") == "api" and self.config.get("mode", "run") == "run":
            provider = validate_binding(self.config)
            self.config["api_provider"] = provider["id"]
            key = self.config.pop("api_key", None)
            if not key:
                raise NativeRejected("The selected API provider key is required; no model request was sent")
            self.gateway = RunGateway(key, self.config["model"], emit=self.emit,
                api_provider=provider["id"], provider_snapshot=self.config.get("api_provider_snapshot"),
                catalog_sha256=self.config.get("api_catalog_sha256"),
                binding={k: self.config.get(k) for k in ("run_id", "instance_id", "owner_epoch", "connection_id", "account_id", "secret_version", "api_catalog_sha256")},
                allowed_models=[self.config["model"], *(self.config.get("options") or {}).get("model_aliases", [])]).start()
            self.config.update(gateway_url=self.gateway.url, gateway_token=self.gateway.token)
            self.secret_values.append(self.gateway.token)
        self.spec = build_launch(self.config)
        if self.config.get("mode") == "login":
            event_token = self.spec.env.get("WORKDESK_AUTH_EVENT_TOKEN", "")
            self.login_output = LoginOutput(self.config["partner_id"], event_token)
        if os.name == "nt" and self.config["partner_id"] == "claude" and self.config.get("mode") == "login":
            from .terminal import WindowsLoginProcess
            self.process = WindowsLoginProcess(self.spec.argv, cwd=self.spec.cwd, env=self.spec.env)
        else:
            self.process = subprocess.Popen(self.spec.argv, cwd=self.spec.cwd, env=self.spec.env, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.emit(type="event", kind="native_process_started", pid=self.process.pid, native_protocol=self.spec.protocol,
                  limitations=self.spec.limitations, route=self.spec.route)
        if self.permission:
            self.emit(type="event", kind="native_permission_configured", resolved_permission=self.permission,
                      permission_source=self.config.get("permission_source", "SAFE_DEFAULT"), source="FROZEN_NATIVE_CONFIGURATION")
        self.stderr_thread = threading.Thread(target=self._stderr, daemon=True)
        self.stderr_thread.start()
        self.run_thread = threading.Thread(target=self._run, daemon=True)
        self.run_thread.start()

    def _stderr(self):
        if self.config.get("mode") == "login":
            self._read_login_pipe(self.process.stderr, "stderr")
            return
        while self.process and self.process.stderr:
            block = self.process.stderr.readline(MAX_FRAME + 1)
            if not block:
                break
            text = block.decode("utf8", "replace").rstrip()
            self.emit(type="event", kind="native_diagnostic", message=text)

    def _login_text(self, text):
        with self.login_lock:
            if self.login_output is None:
                self.login_output = LoginOutput(self.config["partner_id"])
            self._login_events(self.login_output.feed(self.clean(text), "stderr"))

    def _auth_status(self, state, verification, **extra):
        fields = {"auth_state": state, "verification": verification, "model_requests": 0, **extra}
        if state in {"AUTHENTICATED", "UNAUTHENTICATED"}:
            fields.update(authenticated=state == "AUTHENTICATED", fact_scope="OFFICIAL_ACCOUNT_RECEIPT")
        self.emit(type="auth_status", **fields)
        return fields

    def _read_login_pipe(self, stream, source):
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        pending = ""
        while True:
            block = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
            if not block:
                with self.login_lock:
                    if self.login_output_suppressed:
                        return
                    text, _ = redact_login_chunk(decoder.decode(b"", final=True), pending, self.secret_values, final=True)
                    self._login_events(self.login_output.feed(text, source, final=True))
                return
            # Register returned authorization codes before writing stdin; any
            # native echo is removed before it reaches parsing or persistence.
            with self.login_lock:
                if self.login_output_suppressed:
                    # Terminal wrapping/editing can split a returned code with
                    # ANSI/CRLF. After submission, drain without parsing any
                    # terminal content; only official auth status is published.
                    pending = ""
                    continue
                text, pending = redact_login_chunk(decoder.decode(block), pending, self.secret_values)
                self._login_events(self.login_output.feed(text, source))

    def _login_events(self, events):
        for event in events:
            kind = event["kind"]
            if kind == "account-state":
                state = event["state"]
                auth_state = state.get("auth_state")
                if auth_state in {"AUTHENTICATED", "UNAUTHENTICATED", "UNKNOWN"}:
                    fields = self._auth_status(auth_state, state.get("verification", "OFFICIAL_DSH_ACCOUNT_STATE"),
                                               login_phase=state.get("phase"))
                    if (auth_state == "AUTHENTICATED" and self.config.get("mode") == "login"
                            and self.config["partner_id"] == "deepseek"
                            and not self.completed and not self.stop_requested.is_set()):
                        self.completed = True
                        self.emit(type="result", **fields)
                        # The owner may now close this login wrapper (with its
                        # normal grace period) and release the account for Work.
                        self.done.set()
            elif kind == "code-prompt":
                if self.login_request is None:
                    self.login_request = "login-code-" + uuid.uuid4().hex
                    self.emit(type="input", request_id=self.login_request, sensitive=True, input_type="password",
                              message="在官方页面完成登录后，将页面给出的授权码填到这里", purpose="OFFICIAL_LOGIN_CODE")
            else:
                data = {k: v for k, v in event.items() if k != "kind" and v is not None}
                self.emit(type="login", instructions="在官方页面完成登录授权", **data)

    def _run(self):
        try:
            if self.spec.protocol in {"login-text", "auth-status"}:
                return self._plain()
            self.rpc = NativeRpc(self.process, self.spec.protocol, self._native_message)
            if self.spec.protocol == "acp":
                self._acp()
            elif self.spec.protocol == "codex":
                self._codex()
            elif self.spec.protocol == "claude":
                self._claude()
            elif self.spec.protocol == "deepseek":
                self._deepseek()
            elif self.spec.protocol == "zcode":
                self._zcode()
        except Exception as exc:
            if not self.stop_requested.is_set():
                self.failed = True
                if self.config.get("mode") in {"login", "inspect"}:
                    self._auth_status("UNKNOWN", "OFFICIAL_AUTH_OPERATION_NOT_CONFIRMED")
                self.emit(type="error", error=str(exc), error_class=type(exc).__name__,
                          receipt_unknown=isinstance(exc, ReceiptUnknown), no_resend=True)
            self.done.set()

    def _plain(self):
        if self.spec.protocol == "login-text":
            self._read_login_pipe(self.process.stdout, "stdout")
            code = self.process.wait()
            if self.stderr_thread:
                self.stderr_thread.join(timeout=3)
            if self.stop_requested.is_set() or self.completed:
                return
            self.login_request = None
            if code:
                message = (None if self.login_output_suppressed else self.login_output.failure) or "Official login command exited with code " + str(code)
                raise NativeRejected(self.clean(message))
            partner = self.config["partner_id"]
            if partner == "claude":
                fields = self._claude_status()
            elif self.login_output.success:
                fields = self._auth_status("AUTHENTICATED", "OFFICIAL_LOGIN_COMMAND_COMPLETION")
            else:
                fields = self._auth_status("UNKNOWN", "LOGIN_COMMAND_EXITED_AUTH_NOT_CONFIRMED")
            self.emit(type="result", **fields)
            self.completed = True
            self.done.set()
            return
        chunks = []
        for raw in self.process.stdout:
            line = raw.decode("utf8", "replace").rstrip()
            chunks.append(line)
            self.emit(type="event", kind="official_login_output", message=line)
        code = self.process.wait()
        if self.stop_requested.is_set():
            return
        fields = None
        if self.spec.protocol == "auth-status":
            try:
                data = json.loads("\n".join(chunks))
                fields = self._claude_status_fields(data)
            except ValueError:
                pass
        if fields is None:
            if code:
                raise NativeRejected("Official status command exited with code " + str(code))
            fields = self._auth_status("UNKNOWN", "OFFICIAL_STATUS_UNRECOGNIZED")
        self.emit(type="result", **fields)
        self.completed = True
        self.done.set()

    def _claude_status_fields(self, data):
        value = data.get("loggedIn", data.get("authenticated"))
        if isinstance(value, bool):
            return self._auth_status("AUTHENTICATED" if value else "UNAUTHENTICATED", "OFFICIAL_STATUS")
        return self._auth_status("UNKNOWN", "OFFICIAL_STATUS_UNRECOGNIZED")

    def _claude_status(self):
        # Same official home, after the login process has exited. No generation.
        result = subprocess.run(self.config["entry"] + ["auth", "status", "--json"], cwd=self.spec.cwd,
                                env=self.spec.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            return self._claude_status_fields(json.loads(result.stdout))
        except (ValueError, TypeError):
            return self._auth_status("UNKNOWN", "OFFICIAL_STATUS_UNRECOGNIZED")

    def _acp(self):
        initialized = self.rpc.request("initialize", {"protocolVersion": 1, "clientInfo": {"name": "workdesk", "version": "2.0"},
             "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}})
        methods = initialized.get("authMethods", [])
        self.emit(type="ready", native=initialized, auth_methods=methods)
        if self.config.get("mode") == "inspect":
            fields = self._auth_status("UNKNOWN", "OFFICIAL_INITIALIZE_ONLY")
            self.emit(type="result", **fields, auth_methods=methods)
            self.completed = True
            self.done.set()
            return
        if self.config.get("mode") == "login":
            selected = (self.config.get("auth_options") or {}).get("auth_method") or (self.config.get("options") or {}).get("auth_method")
            # Qwen 0.28.4's initialize advertises only openai, while its official
            # authenticate implementation and user docs retain qwen-oauth.
            if not selected and self.spec.partner_id == "qwen":
                selected = "qwen-oauth"
            if not selected:
                candidates = [m for m in methods if not any(word in (str(m.get("id", "")) + str(m.get("name", ""))).lower() for word in ("api-key", "api key", "vertex"))]
                preferred = [m for m in candidates if any(word in (str(m.get("id", "")) + str(m.get("name", ""))).lower() for word in ("oauth", "account", "google", "qwen", "登录"))]
                if preferred:
                    selected = preferred[0]["id"]
                elif len(candidates) == 1:
                    selected = candidates[0]["id"]
            if not selected:
                self.pending_auth_methods = candidates
                if not self.pending_auth_methods:
                    raise NativeRejected("This official version advertises no account authentication method")
                self.emit(type="login", instructions="选择官方账号登录方式，可填写名称或序号", auth_methods=candidates,
                          options=[{"value": m["id"], "label": m.get("name") or m["id"]} for m in candidates],
                          request_id="auth-method")
                while not self.done.wait(.1):
                    selected = self.config.get("selected_auth_method")
                    if selected:
                        break
                self.pending_auth_methods = []
            if not selected:
                return
            reviewed_qwen_oauth = self.spec.partner_id == "qwen" and selected == "qwen-oauth"
            if selected not in {m["id"] for m in methods} and not reviewed_qwen_oauth:
                raise NativeRejected("Authentication method not advertised by this official version")
            result = self.rpc.request("authenticate", {"methodId": selected}, timeout=900)
            fields = self._auth_status("AUTHENTICATED", "OFFICIAL_AUTHENTICATE_RECEIPT")
            self.emit(type="result", **fields)
            self.completed = True
            self.done.set()
            return
        servers = scene_mcp(self.config, self.spec.cwd)
        mcp_servers = [({'type': 'http', 'name': name, 'url': cfg['url'], 'headers': []}
                        if cfg['transport'] == 'http' else {'name': name, 'command': cfg['command'], 'args': cfg['args'], 'env': []})
                       for name, cfg in servers.items()]
        if mcp_servers and any(server.get('type') == 'http' for server in mcp_servers) and not (initialized.get('agentCapabilities') or {}).get('mcpCapabilities', {}).get('http'):
            raise NativeRejected('官方 ACP 未声明 HTTP MCP 支持；未发送消息')
        if self.config.get("resume"):
            if not (initialized.get("agentCapabilities") or {}).get("loadSession"):
                raise NativeRejected("官方工作端未声明原生会话加载能力；未新建会话或发送消息")
            result = self.rpc.request("session/load", {"sessionId": self.session_id, "cwd": self.spec.cwd, "mcpServers": mcp_servers})
            if result.get("sessionId", self.session_id) != self.session_id:
                raise NativeRejected("官方 ACP 返回不同会话；未发送消息")
        else:
            result = self.rpc.request("session/new", {"cwd": self.spec.cwd, "mcpServers": mcp_servers})
            self.session_id = result["sessionId"]
        self._mcp_configured(servers, 'OFFICIAL_ACP_SESSION_CONFIGURATION')
        permission = self.permission or checked_permission(self.config)
        if permission["native"]["transport"] == "acp":
            receipt = apply_acp(self.rpc, result, self.session_id, permission)
            self.emit(type="event", kind="native_permission_applied", resolved_permission=permission, native=receipt)
        if self.spec.partner_id == "gemini" and self.gateway:
            aliases = {m.get("modelId") for m in (result.get("models") or {}).get("availableModels", [])}
            aliases.discard(None)
            # This fixed runtime advertises the aliases its native helper and
            # compaction requests can use. Bind all of them explicitly to the
            # selected driver and retain the observed map as an execution fact.
            self.gateway.allowed_models.update(aliases)
            self.emit(type="event", kind="native_driver_alias_map", source="OFFICIAL_SESSION_MODEL_CATALOG",
                      mapping={model: self.config["model"] for model in sorted(self.gateway.allowed_models)})
        self.emit(type="event", kind="native_session_bound", native=result)
        result = self.rpc.request("session/prompt", {"sessionId": self.session_id,
            "prompt": [{"type": "text", "text": self.config.get("prompt", "")}]}, timeout=86400)
        if self.stop_requested.is_set() or result.get("stopReason") == "cancelled":
            self.emit(type="stopped", native=result, remote_state="NATIVE_PROMPT_CANCELLED")
        else:
            self._finish(native=result)
        self.done.set()

    def _codex(self):
        result = self.rpc.request("initialize", {"clientInfo": {"name": "workdesk", "version": "2.0"}, "capabilities": {"experimentalApi": True}})
        self.rpc.send({"method": "initialized", "params": {}})
        self.emit(type="ready", native=result)
        if self.config.get("mode") == "login":
            method = (self.config.get("auth_options") or {}).get("auth_method", "chatgpt")
            if method not in {"chatgpt", "chatgptDeviceCode"}:
                raise NativeRejected("Unsupported official Codex account login method")
            receipt = self.rpc.request("account/login/start", {"type": method})
            self.codex_login_id = receipt["loginId"]
            self.emit(type="login", login_url=receipt.get("authUrl", receipt.get("verificationUrl")),
                      user_code=receipt.get("userCode"), instructions="在官方页面登录并完成授权")
            while not self.done.wait(.1):
                if self.codex_login_ready.is_set():
                    break
            if self.stop_requested.is_set():
                return
            completed = self.codex_login_receipt or {}
            if completed.get("loginId") != self.codex_login_id or completed.get("success") is not True:
                raise NativeRejected("Official Codex login was not completed for this login attempt")
            account = self.rpc.request("account/read", {"refreshToken": False}).get("account")
            if not isinstance(account, dict) or account.get("type") != "chatgpt":
                raise NativeRejected("Official Codex account status did not confirm ChatGPT authentication")
            fields = self._auth_status("AUTHENTICATED", "OFFICIAL_CODEX_LOGIN_COMPLETED_AND_ACCOUNT_READ")
            self.emit(type="result", **fields)
            self.completed = True
            self.done.set()
            return
        if self.config.get("mode") == "inspect":
            result = self.rpc.request("account/read", {"refreshToken": False})
            account = result.get("account")
            state = "AUTHENTICATED" if isinstance(account, dict) and account.get("type") == "chatgpt" else "UNAUTHENTICATED" if account is None else "UNKNOWN"
            fields = self._auth_status(state, "OFFICIAL_CODEX_ACCOUNT_READ")
            self.emit(type="result", **fields)
            self.completed = True
            self.done.set()
            return
        options = self.config.get("options") or {}
        params = {"cwd": self.spec.cwd, "model": self.config.get("model")}
        for key in ("serviceTier", "personality"):
            if key in options:
                params[key] = options[key]
        servers = scene_mcp(self.config, self.spec.cwd)
        if servers:
            params['config'] = {'mcp_servers.'+name:({'url':cfg['url']} if cfg['transport']=='http' else {k:cfg[k] for k in ('command','args','cwd')})
                                for name,cfg in servers.items()}
        permission = self.permission or checked_permission(self.config)
        params.update(permission["native"]["thread"])
        if self.config.get("resume"):
            params["threadId"] = self.session_id
        result = self.rpc.request("thread/resume" if self.config.get("resume") else "thread/start", params)
        validate_codex_receipt(result, permission)
        if self.config.get("resume") and result["thread"]["id"] != self.session_id:
            raise NativeRejected("Codex 恢复回执与原线程不同；未发送消息")
        self.emit(type="event", kind="native_permission_applied", resolved_permission=permission, source="OFFICIAL_THREAD_CONFIGURATION_RECEIPT")
        self.thread_id = self.session_id = result["thread"]["id"]
        self._mcp_configured(servers, 'OFFICIAL_CODEX_THREAD_CONFIGURATION')
        self.emit(type="event", kind="native_session_bound", native=result)
        result = self.rpc.request("turn/start", {"threadId": self.thread_id,
            "input": [{"type": "text", "text": self.config.get("prompt", "")}], "model": self.config.get("model")})
        self.turn_id = result["turn"]["id"]
        self.emit(type="event", kind="native_turn_bound", turn_id=self.turn_id)
        self.done.wait()

    def _claude(self):
        self.rpc.send({"type": "control_request", "request_id": "workdesk-initialize", "request": {"subtype": "initialize"}})
        self.emit(type="ready", native_protocol="claude-sdk-stream-json")
        self.rpc.send({"type": "user", "session_id": self.session_id, "parent_tool_use_id": None,
                       "message": {"role": "user", "content": self.config.get("prompt", "")}})
        self.done.wait()

    def _deepseek(self):
        api_id = self.config.get("api_provider", "deepseek")
        provider = ("deepseek-official" if api_id == "deepseek" else "workdesk_api_" + api_id) if self.spec.route == "api" else "deepseek-account"
        selection = None
        if self.spec.route == "account":
            if not self.deepseek_account_ready.wait(45):
                raise ReceiptUnknown("DeepSeek官方账号目录核对未完成；未发送模型请求")
            facts = self.deepseek_account_selection or {}
            self.emit(type="ready", account_selection=facts)
            if self.config.get("mode") == "inspect":
                state = "AUTHENTICATED" if facts.get("authenticated") is True else "UNAUTHENTICATED" if facts.get("state") == "NOT_AUTHENTICATED" else "UNKNOWN"
                fields = self._auth_status(state, facts.get("verification", "OFFICIAL_ACCOUNT_CATALOG"))
                self.emit(type="result", **fields, state=facts.get("state"),
                          models=facts.get("models", []), selection=facts.get("selection"),
                          selection_source=facts.get("source"))
                self.completed = True
                self.done.set()
                return
            if not facts.get("authenticated"):
                raise NativeRejected("DeepSeek官方账号尚未登录，或账号目录当前不可用；请先从官方登录入口连接账号")
            models = facts.get("models", [])
            requested = self.config.get("model")
            if requested:
                if not any(model.get("id") == requested for model in models):
                    raise NativeRejected("所选模型不在本次官方账号目录中；未改用其他模型")
                selection = {"provider": provider, "model": requested}
                source = "EXPLICIT_CONNECTION_ACCOUNT_MODEL"
            else:
                selection = facts.get("selection")
                source = facts.get("source")
                if facts.get("state") == "MODEL_SELECTION_REQUIRED":
                    raise NativeRejected("官方已保存的账号默认模型当前不可用；请在官方模型设置中调整后重试，未覆盖原默认")
            if not selection or selection.get("provider") != provider or not selection.get("model"):
                raise NativeRejected("官方账号没有可用模型选择；请在官方模型设置中选择后重试")
            self.config["model"] = selection["model"]
            self.emit(type="event", kind="native_model_selected", model=selection["model"], provider=provider,
                      selection_source=source, models=models, profile_source=facts.get("profile_source"))
        params = {"cwd": self.spec.cwd, "provider": provider, "model": self.config.get("model"),
                  "approvalRequests": True, "capabilities": {"userQuestions": True}}
        if selection and selection.get("reasoningEffort"):
            params["reasoningEffort"] = selection["reasoningEffort"]
        result = self.rpc.request("initialize", params)
        self.emit(type="ready", native=result)
        if self.config.get("mode") == "inspect":
            self.emit(type="result", authenticated=False, verification="SDK_INITIALIZE_IS_NOT_ACCOUNT_VERIFICATION", model_requests=0)
            self.completed = True
            self.done.set()
            return
        if self.config.get("resume"):
            restored = self.rpc.request("session/resume", {"sessionId": self.session_id})
            if restored.get("sessionId", self.session_id) != self.session_id:
                raise NativeRejected("DeepSeek 恢复回执与原会话不同；未发送消息")
        self._mcp_configured(scene_mcp(self.config, self.spec.cwd), 'OFFICIAL_DEEPSEEK_SDK_PROFILE_PATCH')
        query = self.rpc.request("session/query", {"sessionId": self.session_id,
            "contentBlocks": [{"type": "text", "text": self.config.get("prompt", "")}]}, timeout=86400)
        self.emit(type="event", kind="native_prompt_receipt", native=query)
        self.rpc.request("session/wait", {"sessionId": self.session_id}, timeout=86400)
        if not self.stop_requested.is_set():
            self._finish(native=query)
        self.done.set()

    def _zcode(self):
        if self.config.get("mode") == "inspect":
            # This is a protocol call, not a probe prompt or token-file inference.
            result = self.rpc.request("session/list", {})
            self.emit(type="ready", native=result)
            fields = self._auth_status("UNKNOWN", "MODEL_CATALOG_NOT_LOGIN_PROOF")
            self.emit(type="result", **fields)
            self.completed = True
            self.done.set()
            return
        workspace = {"workspaceKey": str(uuid.uuid5(uuid.NAMESPACE_URL, self.spec.cwd)), "workspacePath": self.spec.cwd}
        params = {"sessionId": self.session_id, "workspace": workspace}
        permission = self.permission or checked_permission(self.config)
        if not self.config.get("resume"):
            params["mode"] = permission["native"]["mode"]
        result = self.rpc.request("session/resume" if self.config.get("resume") else "session/create", params)
        self.session_revision = result.get("revision")
        if self.config.get("resume"):
            result = self.rpc.request("session/setMode", {"sessionId": self.session_id, "expectedRevision": self.session_revision,
                                                          "mode": permission["native"]["mode"]})
            self.session_revision = result.get("revision", self.session_revision)
        self.emit(type="event", kind="native_permission_applied", resolved_permission=permission, source="OFFICIAL_SESSION_MODE_RECEIPT")
        self.emit(type="ready", native=result)
        self.rpc.request("session/subscribe", {"sessionId": self.session_id, "deliveryKind": "desktop-continuous", "includeSnapshot": False})
        self.rpc.request("session/send", {"sessionId": self.session_id, "content": self.config.get("prompt", ""), "inputId": str(uuid.uuid4())})
        self.done.wait()

    def _mcp_configured(self, servers, source):
        if servers:
            self.emit(type='event', kind='native_scene_mcp_configured', source=source, servers=list(servers), connection_state='NOT_ATTESTED')

    def _finish(self, **extra):
        if self.stop_requested.is_set() or self.completed:
            return
        self.completed = True
        self.emit(type="result", text="".join(self.last_text), model=self.config.get("model"), usage=self.native_usage,
                  completion_scope="OFFICIAL_NATIVE_TURN", **extra)

    def _native_message(self, message):
        if message.get("_transport_closed"):
            if not self.done.is_set():
                self.failed = True
                self.emit(type="error", error="Official transport closed without a final receipt", receipt_unknown=True, no_resend=True)
                self.done.set()
            return
        self.emit(type="event", kind="native_event", native=message)
        method, params = message.get("method", ""), message.get("params") or {}
        if self.spec.protocol == "codex" and method == "account/login/completed":
            self.codex_login_receipt = params
            self.codex_login_ready.set()
        if self.spec.protocol == "deepseek" and method == "workdesk.accountSelection":
            self.deepseek_account_selection = params
            self.deepseek_account_ready.set()
        if method == "authenticate/update" and params.get("_meta", {}).get("authUri"):
            self.emit(type="login", login_url=params["_meta"]["authUri"], instructions="在官方页面登录并完成授权")
        if "id" in message and method:
            if self._host_callback(message, method, params):
                return
            self.requests[str(message["id"])] = message
            kind = "approval" if any(v in method.lower() for v in ("approval", "permission")) else "input"
            self.emit(type=kind, request_id=str(message["id"]), message=method, options=params.get("options"), native=params)
        protocol = self.spec.protocol
        if protocol == "claude":
            if message.get("session_id"):
                self.session_id = message["session_id"]
            if message.get("type") == "control_request":
                ident = str(message["request_id"])
                self.requests[ident] = message
                request = message.get("request") or {}
                question = request.get("tool_name") == "AskUserQuestion"
                self.emit(type="input" if question else "approval", request_id=ident,
                          message=request.get("subtype", "Official tool request"), native=request)
            if message.get("type") == "result":
                if message.get("is_error") or message.get("subtype") not in {"success", None}:
                    self.failed = True
                    self.emit(type="error", error=message.get("result", str(message.get("errors", message.get("subtype")))), native=message)
                elif not self.stop_requested.is_set():
                    self.native_usage = message.get("usage")
                    self.last_text = [message.get("result", "")]
                    self._finish(native=message)
                self.done.set()
        elif protocol == "codex":
            if method == "item/agentMessage/delta":
                self.last_text.append(params.get("delta", ""))
            if method == "thread/tokenUsage/updated":
                self.native_usage = params.get("tokenUsage")
            if method == "turn/completed":
                turn = params.get("turn", {})
                if self.thread_id and params.get("threadId") != self.thread_id:
                    return
                if turn.get("status") == "completed":
                    self._finish(native=turn)
                elif turn.get("status") == "interrupted":
                    self.emit(type="stopped", native=turn)
                else:
                    self.failed = True
                    self.emit(type="error", error=str(turn.get("error", "Native turn failed")), native=turn)
                self.done.set()
        elif protocol == "acp" and method == "session/update":
            update = params.get("update") or {}
            if update.get("sessionUpdate") == "agent_message_chunk":
                content = update.get("content", {})
                if content.get("type") == "text":
                    self.last_text.append(content.get("text", ""))
        elif protocol == "zcode" and method == "session/event":
            if params.get("sessionId") != self.session_id:
                return
            if params.get("type") == "turn.completed":
                payload = params.get("payload") or {}
                self.native_usage = payload.get("usage")
                if payload.get("resultType") == "cancelled":
                    self.emit(type="stopped", native=payload)
                elif payload.get("resultType") == "success":
                    self.last_text = [payload.get("response", "")]
                    self._finish(native=payload)
                else:
                    self.failed = True
                    self.emit(type="error", error="Official ZCode execution did not succeed", native=payload)
                self.done.set()
            elif params.get("type") == "turn.failed":
                self.failed = True
                self.emit(type="error", error="Official ZCode turn failed", native=params)
                self.done.set()

    def _host_callback(self, message, method, params):
        """Answer native host reads; these are neither prompts nor approvals."""
        result = None
        if self.spec.protocol == "zcode" and method == "session/requestRuntimePreferences":
            # Exact fixed-release ZCode schema and its explicit old-host
            # fallback defaults. Native search stays available and no operation
            # permission is granted by this preferences response.
            result = {"nativeSearchEnhancementsEnabled": True, "toolSearchMode": "off",
                      "totalTokensReminderEnabled": False, "agentAlwaysBackgroundEnabled": True,
                      "memoryEnabled": False, "askUserQuestionAutoResolutionEnabled": True,
                      "modelContextBudgetStrategy": "preflight-v1"}
        elif self.spec.protocol == "zcode" and method == "session/readReplyLanguage":
            # Official protocol specifies null for a host without this setting.
            result = {"language": None}
        elif self.config.get("partner_id") == "qwen" and method == "craft/drainMidTurnQueue":
            # Workdesk has no queued mid-turn guidance. This does not schedule
            # another native turn or synthesize model content.
            result = {"messages": [], "hasQueuedPrompt": False}
        if result is None:
            return False
        if params.get("sessionId") != self.session_id:
            self.rpc.send({"id": message["id"], "error": {"code": -32602, "message": "Host callback session identity mismatch"}})
        else:
            self.rpc.send({"id": message["id"], "result": result})
            self.emit(type="event", kind="native_host_callback", method=method, response=result,
                      source="OFFICIAL_FIXED_RELEASE_HOST_DEFAULTS")
        return True

    def control(self, command):
        action = command.get("action")
        if action == "stop":
            self.stop_requested.set()
            if self.rpc and not self.rpc.closed:
                if self.spec.protocol == "acp":
                    self.rpc.send({"method": "session/cancel", "params": {"sessionId": self.session_id}})
                elif self.spec.protocol == "codex":
                    if self.config.get("mode") == "login" and self.codex_login_id:
                        self.rpc.request("account/login/cancel", {"loginId": self.codex_login_id}, timeout=5)
                    elif self.turn_id:
                        self.rpc.request("turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id}, timeout=5)
                elif self.spec.protocol == "deepseek":
                    self.rpc.request("shutdown", timeout=5)
                elif self.spec.protocol == "zcode":
                    self.rpc.request("session/stop", {"sessionId": self.session_id}, timeout=5)
                elif self.spec.protocol == "claude":
                    self.rpc.send({"type": "control_request", "request_id": "workdesk-stop", "request": {"subtype": "interrupt"}})
            self.emit(type="stopped", remote_state="STOP_REQUESTED_UNTIL_NATIVE_RECEIPT", local_exit_confirmed=False)
            self.done.set()
        elif action == "login-input":
            with self.login_lock:
                text = command.get("text")
                if self.config.get("mode") != "login" or not self.login_request or command.get("request_id") != self.login_request:
                    raise NativeRejected("The sensitive official login request is absent or already resolved")
                if not isinstance(text, str) or not text.strip() or len(text) > 16384 or any(c in text for c in "\r\n\0"):
                    raise NativeRejected("Only one official authorization code may be supplied")
                if self.spec.protocol != "login-text" or self.config["partner_id"] != "claude" or not self.process or self.process.poll() is not None:
                    raise NativeRejected("This native login process cannot accept an authorization code")
                self.secret_values.append(text)
                self.login_request = None
                self.login_output_suppressed = True
                stream = self.process.stdin
            # Synchronous terminal input/output must remain independent. Never
            # hold the parser lock while a full input pipe waits for the child.
            try:
                stream.write((text + "\n").encode("utf-8"))
                stream.flush()
            except (OSError, BrokenPipeError) as exc:
                raise ReceiptUnknown("Official authorization-code receipt is unknown; do not resend") from exc
        elif action == "input" and self.login_request and command.get("request_id") == self.login_request:
            raise NativeRejected("Official authorization codes require the dedicated sensitive login input")
        elif action == "input" and command.get("request_id") == "auth-method":
            self.config["selected_auth_method"] = self._auth_method_selection(command.get("auth_method") or command.get("text"))
        elif action in {"approval", "input"} and command.get("request_id"):
            ident = str(command["request_id"])
            message = self.requests.pop(ident, None)
            if message is None:
                raise NativeRejected("Native request is absent or already resolved")
            accepted = command.get("decision") in {"accept", "allow", "allowed-once"}
            params = message.get("params") or {}
            if self.spec.protocol == "claude":
                request = message.get("request") or {}
                if action == "input" and request.get("tool_name") == "AskUserQuestion":
                    tool_input = request.get("input") or {}
                    questions = tool_input.get("questions", [])
                    answers = command.get("answers")
                    if answers is None and len(questions) == 1 and command.get("text") is not None:
                        answers = {questions[0].get("question", ""): str(command["text"])}
                    if answers is None:
                        self.requests[ident] = message
                        raise NativeRejected("Each official question needs a named answer")
                    response = {"behavior": "allow", "updatedInput": {**tool_input, "answers": answers}}
                else:
                    response = {"behavior": "allow", "updatedInput": request.get("input", {})} if accepted else {"behavior": "deny", "message": "User did not grant this operation"}
                self.rpc.send({"type": "control_response", "response": {"subtype": "success", "request_id": ident, "response": response}})
            elif message.get("method") == "session/request_permission":
                options = params.get("options") or []
                option = next((v for v in options if v.get("kind") == ("allow_once" if accepted else "reject_once")), None)
                result = {"outcome": {"outcome": "selected", "optionId": option["optionId"]}} if option else {"outcome": {"outcome": "cancelled"}}
                self.rpc.send({"id": message["id"], "result": result})
            elif message.get("method") == "approval/request":
                self.rpc.send({"id": message["id"], "result": {"outcome": "allowed-once" if accepted else "rejected"}})
            elif message.get("method") == "user-question/ask":
                answers = command.get("answers")
                questions = params.get("questions") or []
                if answers is None and len(questions) == 1 and command.get("text") is not None:
                    answers = [{"id": questions[0]["id"], "selected": [], "custom": str(command["text"])}]
                if answers is None and command.get("text"):
                    self.requests[ident] = message
                    raise NativeRejected("Each official question needs a named answer")
                result = {"answers": answers} if answers else {"cancelled": True}
                self.rpc.send({"id": message["id"], "result": result})
            elif self.spec.protocol == "codex" and "requestApproval" in message.get("method", ""):
                self.rpc.send({"id": message["id"], "result": {"decision": "accept" if accepted else "decline"}})
            elif self.spec.protocol == "codex" and "requestUserInput" in message.get("method", ""):
                answers = command.get("answers")
                questions = params.get("questions") or []
                if answers is None and len(questions) == 1 and command.get("text") is not None:
                    answers = {questions[0]["id"]: {"answers": [str(command["text"])]}}
                if answers is None:
                    self.requests[ident] = message
                    raise NativeRejected("Each official question needs a named answer")
                self.rpc.send({"id": message["id"], "result": {"answers": answers}})
            elif self.spec.protocol == "zcode" and message.get("method") == "interaction/requestPermission":
                self.rpc.send({"id": message["id"], "result": {"decision": "allow" if accepted else "deny"}})
            elif self.spec.protocol == "zcode" and message.get("method") == "interaction/requestUserInput":
                content = command.get("response") or {"text": str(command.get("text", ""))}
                self.rpc.send({"id": message["id"], "result": {"action": "accept", "content": content}})
            elif command.get("response") is not None:
                self.rpc.send({"id": message["id"], "result": command["response"]})
            else:
                raise NativeRejected("This native request needs its protocol-specific response")
        elif action == "native" and self.rpc:
            # Go owner must gate this route to a reviewed native capability.
            method = command.get("method")
            if not isinstance(method, str) or not method:
                raise ValueError("Native method required")
            result = self.rpc.request(method, command.get("params"), timeout=30)
            self.emit(type="event", kind="native_control_receipt", command_id=command.get("id"), native=result)
        elif action == "input":
            raise NativeRejected("Uncorrelated live input requires the official native steer operation")
        else:
            raise NativeRejected("Unknown worker control action")

    def _auth_method_selection(self, value):
        methods = self.pending_auth_methods
        if not methods:
            raise NativeRejected("No official authentication choice is pending")
        if isinstance(value, str):
            value = value.strip()
            try:
                parsed = json.loads(value)
                if isinstance(parsed, (dict, str, int)) and not isinstance(parsed, bool):
                    value = parsed
            except ValueError:
                pass
        if isinstance(value, dict):
            value = value.get("auth_method") or value.get("id") or value.get("value")
        token = str(value).strip() if value is not None else ""
        matches = [m for m in methods if token == m["id"]]
        if not matches and token.isdecimal() and 1 <= int(token) <= len(methods):
            matches = [methods[int(token) - 1]]
        if not matches:
            matches = [m for m in methods if token.casefold() == str(m.get("name", "")).casefold()]
        if len(matches) != 1:
            raise NativeRejected("请选择所列官方登录方式的名称或序号")
        return matches[0]["id"]

    def close(self):
        if self.process and self.process.poll() is None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
        if self.gateway:
            self.gateway.close()
        if self.process and hasattr(self.process, "close_transport"):
            self.process.close_transport()


def main():
    # The Go wire is UTF-8 even when Windows' interactive locale is CP936.
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    lock = threading.Lock()
    def emit(data):
        with lock:
            print(json.dumps(data, ensure_ascii=False), flush=True)
    worker = None
    try:
        # Raw duplicated fd avoids a daemon holding BufferedReader's shutdown lock.
        input_stream = io.FileIO(os.dup(sys.stdin.fileno()), "rb", closefd=True)
        raw = input_stream.readline(MAX_FRAME + 1)
        if len(raw) > MAX_FRAME:
            raise ValueError("Initial frame exceeds transport limit")
        first = json.loads(raw)
        if first.get("action") != "start":
            raise ValueError("First frame must be start/config")
        worker = PartnerWorker(first["config"], emit)
        worker.start()
        def controls():
            while not worker.done.is_set():
                raw = input_stream.readline(MAX_FRAME + 1)
                if not raw:
                    try:
                        worker.control({"action": "stop"})
                    except Exception:
                        worker.done.set()
                    break
                try:
                    if len(raw) > MAX_FRAME:
                        raise ValueError("Control frame exceeds transport limit")
                    worker.control(json.loads(raw))
                except Exception as exc:
                    worker.emit(type="event", kind="control_rejected", error=str(exc),
                                receipt_unknown=isinstance(exc, ReceiptUnknown), no_resend=isinstance(exc, ReceiptUnknown))
        threading.Thread(target=controls, daemon=True).start()
        worker.done.wait()
        return 1 if worker.failed else 0
    except Exception as exc:
        if worker:
            worker.emit(type="error", error=str(exc), error_class=type(exc).__name__, no_resend=True)
        else:
            emit({"type": "error", "error": type(exc).__name__ + ": invalid launch configuration"})
        return 1
    finally:
        if worker:
            worker.close()
