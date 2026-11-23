"""Bounded JSON-lines transport. No model request is made by construction."""

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time


MAX_FRAME = 2 * 1024 * 1024


class RpcError(RuntimeError):
    pass


class Rpc:
    def __init__(self, command, cwd, env, on_message=lambda message: None, max_frame=MAX_FRAME):
        self.on_message = on_message
        self.max_frame = max_frame
        self.messages = queue.Queue(maxsize=256)
        self.responses = {}
        self.sequence = 0
        self.stderr_tail = bytearray()
        self.closed = False
        self.process = subprocess.Popen(
            command, cwd=cwd, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.readers = [threading.Thread(target=self._stdout, daemon=True),
                        threading.Thread(target=self._stderr, daemon=True)]
        for reader in self.readers:
            reader.start()

    def _put(self, value):
        while not self.closed:
            try:
                self.messages.put(value, timeout=.1)
                return
            except queue.Full:
                continue

    def _stdout(self):
        try:
            while not self.closed:
                raw = self.process.stdout.readline(self.max_frame + 1 if self.max_frame is not None else -1)
                if not raw:
                    self._put(RpcError("app-server stdout closed; execution may be unknown"))
                    return
                if (self.max_frame is not None and len(raw) > self.max_frame) or not raw.endswith(b"\n"):
                    raise RpcError("oversized or incomplete app-server frame")
                message = json.loads(raw.decode("utf-8"))
                if not isinstance(message, dict):
                    raise RpcError("app-server frame must be an object")
                self._put(message)
        except Exception as exc:
            self._put(RpcError(str(exc)))

    def _stderr(self):
        while not self.closed:
            raw = self.process.stderr.read1(4096)
            if not raw:
                return
            self.stderr_tail.extend(raw)
            del self.stderr_tail[:-16384]

    def send(self, message):
        raw = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        if self.max_frame is not None and len(raw) > self.max_frame:
            raise RpcError("outgoing frame too large")
        try:
            self.process.stdin.write(raw)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RpcError("write receipt unknown; do not resend") from exc

    def pump(self, timeout=.1):
        try:
            message = self.messages.get(timeout=timeout)
        except queue.Empty:
            return
        if isinstance(message, Exception):
            raise message
        if "id" in message and "method" not in message:
            self.responses[message["id"]] = message
        else:
            self.on_message(message)

    def request(self, method, params, timeout=10):
        self.sequence += 1
        request_id = f"workdesk-{self.sequence}"
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while request_id not in self.responses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RpcError(f"{method}: timeout; receipt unknown; do not resend")
            self.pump(min(.1, remaining))
        response = self.responses.pop(request_id)
        if "error" in response:
            raise RpcError(f"{method}: server rejected request: {response['error']}")
        return response.get("result", {})

    def initialize(self):
        result = self.request("initialize", {"clientInfo": {
            "name": "workdesk_local", "title": "Workdesk · 工作台", "version": "0.1.0",
        }})
        self.send({"method": "initialized", "params": {}})
        return result

    def close(self):
        """Only this child is managed; child exit is not tool-tree stop proof."""
        if self.closed:
            return
        try:
            self.process.stdin.close()
            self.process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        finally:
            self.closed = True
            for reader in self.readers:
                reader.join(timeout=1)
            self.process.stdout.close()
            self.process.stderr.close()


def codex_command(executable):
    return [str(executable), "app-server", "--listen", "stdio://",
            "-c", 'forced_login_method="chatgpt"', "-c", 'model_provider="workdesk_chatgpt"',
            "-c", 'cli_auth_credentials_store="file"',
            "-c", 'approval_policy="never"', "-c", 'sandbox_mode="read-only"',
            "-c", 'web_search="disabled"', "-c", "features.multi_agent=false",
            "-c", "features.fast_mode=true",
            "-c", 'model_providers.workdesk_chatgpt.name="OpenAI ChatGPT subscription"',
            "-c", 'model_providers.workdesk_chatgpt.base_url="https://chatgpt.com/backend-api/codex"',
            "-c", 'model_providers.workdesk_chatgpt.wire_api="responses"',
            "-c", "model_providers.workdesk_chatgpt.requires_openai_auth=true",
            "-c", "model_providers.workdesk_chatgpt.supports_websockets=false",
            "-c", "model_providers.workdesk_chatgpt.request_max_retries=0",
            "-c", "model_providers.workdesk_chatgpt.stream_max_retries=0",
            "-c", "features.plugins=false", "-c", "features.shell_tool=false"]


def isolated_env(home):
    env = dict(os.environ)
    for key in list(env):
        if (key.upper().startswith("CODEX_") and key.upper() != "CODEX_CA_CERTIFICATE"
                or key.upper() in {"OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"}):
            del env[key]
    env["CODEX_HOME"] = str(Path(home).resolve())
    return env
