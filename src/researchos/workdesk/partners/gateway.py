"""One-run official API transport with native wires and Gemini translation.

No model request is made at construction. The caller supplies a fresh loopback
token and a frozen run binding; credentials are held only in this process.
"""
from __future__ import annotations

import base64
import http.client
import json
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlsplit, unquote, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .providers import provider_for
from .usage import UsageReceipt


class ProtocolUnsupported(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def gemini_request(body: dict, model: str, api_provider="deepseek") -> dict:
    """Translate ordinary Gemini content/functions without inventing tools."""
    messages = []
    instruction = body.get("systemInstruction", body.get("system_instruction"))
    if instruction:
        parts = instruction.get("parts", [])
        if any(set(p) - {"text"} for p in parts):
            raise ProtocolUnsupported("Non-text system instruction is unsupported")
        messages.append({"role": "system", "content": "\n".join(p.get("text", "") for p in parts)})
    calls = {}
    for item in body.get("contents", []):
        role = "assistant" if item.get("role") == "model" else "user"
        text, tool_calls, results, content = [], [], [], []
        for part in item.get("parts", []):
            if "text" in part:
                if not part.get("thought"):
                    text.append(part["text"])
                    content.append({"type": "text", "text": part["text"]})
            elif "inlineData" in part or "inline_data" in part:
                data = part.get("inlineData", part.get("inline_data"))
                mime = data.get("mimeType", data.get("mime_type", ""))
                if not mime.startswith("image/"):
                    raise ProtocolUnsupported("Only supported image inline data can be forwarded")
                content.append({"type": "image_url", "image_url": {"url": "data:" + mime + ";base64," + data["data"]}})
            elif "functionCall" in part or "function_call" in part:
                call = part.get("functionCall", part.get("function_call"))
                call_id = call.get("id") or "call_" + uuid.uuid4().hex
                calls.setdefault(call["name"], []).append(call_id)
                tool_calls.append({"id": call_id, "type": "function", "function": {
                    "name": call["name"], "arguments": json.dumps(call.get("args", {}), ensure_ascii=False)}})
            elif "functionResponse" in part or "function_response" in part:
                response = part.get("functionResponse", part.get("function_response"))
                ids = calls.get(response["name"], [])
                call_id = response.get("id") or (ids.pop(0) if ids else None)
                if not call_id:
                    raise ProtocolUnsupported("Function response has no corresponding call identity")
                results.append({"role": "tool", "tool_call_id": call_id,
                                "content": json.dumps(response.get("response", {}), ensure_ascii=False)})
            else:
                raise ProtocolUnsupported("Unsupported Gemini content part: " + ",".join(part))
        if content or tool_calls:
            message = {"role": role, "content": content if any(p["type"] != "text" for p in content) else "\n".join(text)}
            if tool_calls:
                message["tool_calls"] = tool_calls
            messages.append(message)
        messages.extend(results)
    result = {"model": model, "messages": messages}
    tools = []
    for group in body.get("tools", []):
        unknown = set(group) - {"functionDeclarations", "function_declarations"}
        if unknown:
            raise ProtocolUnsupported("Provider-hosted Gemini tools unsupported: " + ",".join(unknown))
        for item in group.get("functionDeclarations", group.get("function_declarations", [])):
            tool = {"name": item["name"], "description": item.get("description", "")}
            schema = item.get("parametersJsonSchema", item.get("parameters", {"type": "object"}))
            tool["parameters"] = normalize_schema(schema)
            tools.append({"type": "function", "function": tool})
    if tools:
        result["tools"] = tools
    gen = body.get("generationConfig", body.get("generation_config", {}))
    mapping = {"temperature": "temperature", "topP": "top_p", "top_p": "top_p",
               "maxOutputTokens": "max_tokens", "max_output_tokens": "max_tokens", "stopSequences": "stop"}
    for key, value in gen.items():
        if key in mapping:
            result[mapping[key]] = value
        elif key in {"thinkingConfig", "thinking_config"}:
            if api_provider != "deepseek" and value:
                raise ProtocolUnsupported("Explicit Gemini thinking configuration has no faithful mapping for this API")
            if value.get("includeThoughts", value.get("include_thoughts")):
                result["thinking"] = {"type": "enabled"}
        elif key == "responseMimeType" and value == "application/json":
            result["response_format"] = {"type": "json_object"}
        elif key == "responseMimeType" and value == "text/plain":
            pass
        elif key in {"responseSchema", "responseJsonSchema"}:
            raise ProtocolUnsupported("Structured Gemini response schema requires explicit provider support")
        else:
            raise ProtocolUnsupported("Unsupported generation option: " + key)
    config = body.get("toolConfig", {}).get("functionCallingConfig", {})
    mode = config.get("mode", "AUTO")
    if mode == "NONE":
        result["tool_choice"] = "none"
    elif mode == "ANY":
        names = config.get("allowedFunctionNames", [])
        if len(names) == 1:
            result["tool_choice"] = {"type": "function", "function": {"name": names[0]}}
        elif names:
            raise ProtocolUnsupported("Multiple allowedFunctionNames cannot be represented faithfully")
        else:
            result["tool_choice"] = "required"
    elif mode != "AUTO":
        raise ProtocolUnsupported("Unsupported function calling mode")
    return result


def normalize_schema(value):
    if isinstance(value, dict):
        return {k: (v.lower() if k == "type" and isinstance(v, str) else normalize_schema(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_schema(v) for v in value]
    return value


def gemini_response(body: dict) -> dict:
    candidates = []
    for choice in body.get("choices", []):
        message, parts = choice.get("message", {}), []
        if message.get("reasoning_content"):
            parts.append({"text": message["reasoning_content"], "thought": True})
        if message.get("content"):
            parts.append({"text": message["content"]})
        for call in message.get("tool_calls", []):
            args = call["function"].get("arguments", "{}")
            parts.append({"functionCall": {"id": call["id"], "name": call["function"]["name"],
                                           "args": json.loads(args)}})
        reason = {"stop": "STOP", "tool_calls": "STOP", "length": "MAX_TOKENS", "content_filter": "SAFETY"}.get(choice.get("finish_reason"))
        candidate = {"index": choice.get("index", 0), "content": {"role": "model", "parts": parts}}
        if reason:
            candidate["finishReason"] = reason
        candidates.append(candidate)
    usage = body.get("usage") or {}
    return {"candidates": candidates, "modelVersion": body.get("model"), "responseId": body.get("id"),
            "usageMetadata": {"promptTokenCount": usage.get("prompt_tokens", 0),
                              "candidatesTokenCount": usage.get("completion_tokens", 0),
                              "totalTokenCount": usage.get("total_tokens", 0)}}


class RunGateway:
    def __init__(self, api_key: str, model: str, *, emit=lambda **v: None, binding=None,
                 allowed_models=None, upstream=None, _test_upstream=False, api_provider=None,
                 provider_snapshot=None, catalog_sha256=None):
        self.provider = provider_for(api_provider, snapshot=provider_snapshot, catalog_sha256=catalog_sha256)
        self.provider_id = self.provider["id"]
        parsed = urlsplit(upstream or self.provider["base_url"])
        if _test_upstream:
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
                raise ValueError("Local protocol substitutes require a loopback upstream")
        elif upstream is not None and upstream.rstrip("/") != self.provider["base_url"].rstrip("/"):
            raise ValueError("The gateway only connects to frozen official API endpoints")
        self.key, self.model, self.emit = api_key, model, emit
        self.binding = {**dict(binding or {}), "api_provider": self.provider_id}
        self.upstream = upstream.rstrip("/") if _test_upstream else None
        self.allowed_models = set(allowed_models or [model])
        self.token = secrets.token_urlsafe(32)
        self.server = None
        self.thread = None
        self.closed = threading.Event()
        self.opener = build_opener(NoRedirect)
        self.active = set()
        self.lock = threading.Lock()

    def _endpoint(self, wire, *, operation=None):
        endpoint = self.provider["endpoints"].get(wire)
        if not endpoint:
            raise ProtocolUnsupported("The frozen API provider has no registered " + wire + " endpoint")
        endpoint = endpoint.replace("{model}", quote(self.model, safe="")).replace("{operation}", operation or "generateContent")
        if self.upstream:
            endpoint = self.upstream + urlsplit(endpoint).path
        if wire == "generate_content" and operation == "streamGenerateContent":
            endpoint += "?alt=sse"
        return endpoint

    def start(self):
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                self.dispatch()

            def do_POST(self):
                self.dispatch()

            def dispatch(self):
                try:
                    gateway.handle(self)
                except (BrokenPipeError, ConnectionResetError):
                    gateway.emit(type="event", kind="gateway_client_disconnected", remote_state="UNKNOWN")
                except Exception as exc:
                    self.send_json(502, {"error": {"message": type(exc).__name__, "type": "gateway_error"}})

            def send_json(self, code, data):
                raw = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    @property
    def url(self):
        return "http://127.0.0.1:" + str(self.server.server_port)

    def close(self):
        self.closed.set()
        with self.lock:
            active = list(self.active)
        for response in active:
            response.close()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.key = ""

    def handle(self, handler):
        host = "127.0.0.1:" + str(self.server.server_port)
        if handler.headers.get("Host") != host or handler.headers.get("Origin") not in (None, "", "http://" + host):
            return handler.send_json(403, {"error": "Loopback origin mismatch"})
        auth = handler.headers.get("Authorization", "").removeprefix("Bearer ")
        auth = auth or handler.headers.get("x-api-key", "") or handler.headers.get("x-goog-api-key", "")
        if self.closed.is_set() or not secrets.compare_digest(auth, self.token):
            return handler.send_json(403, {"error": "Run token is absent, expired or incorrect"})
        path = urlsplit(handler.path).path
        size = int(handler.headers.get("Content-Length", "0"))
        if size < 0 or size > 32 * 1024 * 1024:
            return handler.send_json(413, {"error": "Native request exceeds transport limit"})
        body = json.loads(handler.rfile.read(size)) if size else None
        google = path.startswith("/v1beta/models/") or path.startswith("/v1/models/")
        translated = False
        operation = None
        if google:
            selected, operation = unquote(path.split("/models/", 1)[1]).split(":", 1)
            if selected not in self.allowed_models:
                return handler.send_json(400, {"error": {"message": "Model alias is not in the frozen connection map"}})
            if operation not in {"countTokens", "generateContent", "streamGenerateContent"}:
                return handler.send_json(501, {"error": {"message": "Unregistered Gemini service operation"}})
            if self.provider_id == "gemini":
                wire = "generate_content"
                stream = operation == "streamGenerateContent"
            elif operation == "countTokens":
                estimate = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
                self.emit(type="event", kind="token_count_estimate", api_provider=self.provider_id,
                          source="UTF8_BYTES_UPPER_ESTIMATE", value=estimate)
                return handler.send_json(200, {"totalTokens": estimate, "workdeskEstimate": True})
            else:
                try:
                    body = gemini_request(body, self.model, self.provider_id)
                except ProtocolUnsupported as exc:
                    return handler.send_json(400, {"error": {"message": str(exc)}})
                stream = operation == "streamGenerateContent"
                body["stream"] = stream
                wire, translated = "chat_completions", True
        else:
            routes = {"/chat/completions": "chat_completions", "/v1/chat/completions": "chat_completions",
                      "/responses": "responses", "/v1/responses": "responses",
                      "/messages": "messages", "/v1/messages": "messages", "/anthropic/messages": "messages",
                      "/anthropic/v1/messages": "messages", "/models": "models", "/v1/models": "models",
                      "/search": "search", "/v1/search": "search", "/fetch": "fetch", "/v1/fetch": "fetch"}
            wire = routes.get(path)
            if wire is None:
                return handler.send_json(404, {"error": {"message": "Unknown provider operation"}})
            stream = isinstance(body, dict) and body.get("stream") is True
            if isinstance(body, dict):
                requested = body.get("model", self.model)
                if requested.removesuffix("[1m]") != self.model:
                    return handler.send_json(400, {"error": {"message": "Model differs from the frozen connection"}})
        try:
            endpoint = self._endpoint(wire, operation=operation)
        except ProtocolUnsupported as exc:
            return handler.send_json(501, {"error": {"message": str(exc)}})
        if self.provider_id == "anthropic" and wire == "chat_completions":
            # Anthropic documents that strict function schemas are ignored by
            # its Chat compatibility layer. Refuse that guarantee explicitly.
            if any((tool.get("function") or {}).get("strict") for tool in (body or {}).get("tools", [])):
                return handler.send_json(400, {"error": {"message": "Anthropic Chat compatibility does not enforce strict function schemas; use its Messages route"}})
        attempt = uuid.uuid4().hex
        common = {"request_id": attempt, "api_provider": self.provider_id, "provider": self.provider_id,
                  "model": self.model, "protocol": wire, "binding": self.binding}
        self.emit(type="event", kind="model_request_intent", **common)
        headers = {"Content-Type": "application/json"}
        auth_type = self.provider.get("auth_by_wire", {}).get(wire, self.provider["auth"]["type"])
        if auth_type == "bearer":
            headers["Authorization"] = "Bearer " + self.key
        elif auth_type == "x-api-key":
            headers["x-api-key"] = self.key
        elif auth_type == "x-goog-api-key":
            headers["x-goog-api-key"] = self.key
        else:
            raise ValueError("Unknown frozen API authentication method")
        if wire == "messages":
            headers["anthropic-version"] = handler.headers.get("anthropic-version", "2023-06-01")
            if handler.headers.get("anthropic-beta"):
                headers["anthropic-beta"] = handler.headers["anthropic-beta"]
        request = Request(endpoint, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                          headers=headers, method=handler.command)
        try:
            response = self.opener.open(request, timeout=600)
        except HTTPError as exc:
            raw = exc.read(1024 * 1024)
            raw = raw.replace(self.key.encode(), b"[REDACTED]") if self.key else raw
            self.emit(type="event", kind="model_request_rejected", **common, status=exc.code)
            return handler.send_json(exc.code, {"error": {"message": raw.decode("utf8", "replace")}})
        with self.lock:
            self.active.add(response)
        usage = UsageReceipt()
        upstream_request_id = response.headers.get("x-request-id", response.headers.get("request-id"))
        try:
            handler.send_response(response.status)
            handler.send_header("Content-Type", "text/event-stream" if stream else "application/json")
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.close_connection = True
            if translated and stream:
                self._gemini_stream(response, handler, attempt, usage)
            elif translated:
                raw = response.read()
                usage.finish_json(raw)
                handler.wfile.write(json.dumps(gemini_response(json.loads(raw)), ensure_ascii=False).encode())
            elif stream:
                while not self.closed.is_set():
                    block = response.read1(64 * 1024)
                    if not block:
                        break
                    usage.feed_sse(block)
                    handler.wfile.write(block)
                    handler.wfile.flush()
                usage.end_sse()
            else:
                raw = response.read()
                usage.finish_json(raw)
                handler.wfile.write(raw)
            self.emit(type="event", kind="model_transport_ended", **common, remote_completion="CONSULT_NATIVE_RESULT")
        finally:
            # Exactly one receipt per HTTP request. Missing or incomplete usage
            # stays unknown; an official numeric zero is retained as zero.
            self.emit(type="event", kind="model_usage", **common, upstream_request_id=upstream_request_id, **usage.fields())
            with self.lock:
                self.active.discard(response)
            response.close()

    def _gemini_stream(self, response, handler, attempt, receipt):
        calls = {}
        def send(value):
            handler.wfile.write(b"data: " + json.dumps(value, ensure_ascii=False).encode() + b"\n\n")
            handler.wfile.flush()
        for raw in response:
            if self.closed.is_set():
                return
            if not raw.startswith(b"data:"):
                continue
            payload = raw[5:].strip()
            if payload == b"[DONE]":
                receipt.final = True
                break
            data = json.loads(payload)
            receipt.observe(data)
            usage = data.get("usage")
            if usage:
                send(gemini_response({**data, "choices": []}))
            for choice in data.get("choices", []):
                delta = choice.get("delta", {})
                parts = []
                if delta.get("content"):
                    parts.append({"text": delta["content"]})
                if delta.get("reasoning_content"):
                    parts.append({"text": delta["reasoning_content"], "thought": True})
                for call in delta.get("tool_calls", []):
                    key = (choice.get("index", 0), call.get("index", 0))
                    target = calls.setdefault(key, {"id": "", "name": "", "arguments": ""})
                    target["id"] += call.get("id", "")
                    target["name"] += call.get("function", {}).get("name", "")
                    target["arguments"] += call.get("function", {}).get("arguments", "")
                candidate = {"index": choice.get("index", 0), "content": {"role": "model", "parts": parts}}
                if choice.get("finish_reason"):
                    for key, call in list(calls.items()):
                        if key[0] == candidate["index"]:
                            parts.append({"functionCall": {"id": call["id"], "name": call["name"], "args": json.loads(call["arguments"] or "{}")}})
                            del calls[key]
                    candidate["finishReason"] = "MAX_TOKENS" if choice["finish_reason"] == "length" else "STOP"
                if parts or candidate.get("finishReason"):
                    send({"candidates": [candidate], "modelVersion": data.get("model"), "responseId": data.get("id")})
