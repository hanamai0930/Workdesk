"""Observe actual provider usage while leaving native protocol bytes intact."""
from __future__ import annotations

import json


class UsageReceipt:
    def __init__(self):
        self.usage = None
        self.response_id = self.actual_model = None
        self.final = False
        self.invalid = False
        self.buffer = b""
        self.frame = []

    def observe(self, data):
        if not isinstance(data, dict):
            return
        kind = data.get("type", "")
        value = data.get("response", data.get("message", data))
        if not isinstance(value, dict):
            value = data
        if value.get("id", value.get("responseId")):
            self.response_id = value.get("id", value.get("responseId"))
        if value.get("model", value.get("modelVersion")):
            self.actual_model = value.get("model", value.get("modelVersion"))
        usage = value.get("usage", value.get("usageMetadata"))
        if isinstance(usage, dict) and usage:
            # Anthropic start/delta contains disjoint/cumulative fields. Replace
            # each received field, never sum repeated streaming snapshots.
            self.usage = {**(self.usage or {}), **usage}
        if kind in {"response.completed", "response.incomplete", "response.failed", "message_stop"}:
            self.final = True
        if any(c.get("finish_reason") is not None for c in data.get("choices", [])):
            self.final = True
        if any(c.get("finishReason") for c in data.get("candidates", [])):
            self.final = True

    def _frame(self):
        if not self.frame:
            return
        raw = b"\n".join(self.frame)
        self.frame = []
        if raw.strip() == b"[DONE]":
            self.final = True
            return
        try:
            self.observe(json.loads(raw))
        except (ValueError, TypeError):
            self.invalid = True

    def feed_sse(self, block):
        self.buffer += block
        if len(self.buffer) > 32 * 1024 * 1024:
            self.invalid = True
            self.buffer = b""
            return
        while b"\n" in self.buffer:
            raw, self.buffer = self.buffer.split(b"\n", 1)
            raw = raw.rstrip(b"\r")
            if not raw:
                self._frame()
            elif raw.startswith(b"data:"):
                self.frame.append(raw[5:].lstrip(b" "))

    def end_sse(self):
        if self.buffer:
            raw, self.buffer = self.buffer.rstrip(b"\r"), b""
            if raw.startswith(b"data:"):
                self.frame.append(raw[5:].lstrip(b" "))
        self._frame()

    def finish_json(self, raw):
        try:
            self.observe(json.loads(raw))
            self.final = True
        except (ValueError, TypeError):
            self.invalid = True

    def fields(self):
        complete = self.final and not self.invalid
        return {"usage": self.usage if complete else None, "usage_complete": complete,
                "response_id": self.response_id, "actual_model": self.actual_model,
                "usage_source": "UPSTREAM_RECEIPT"}
