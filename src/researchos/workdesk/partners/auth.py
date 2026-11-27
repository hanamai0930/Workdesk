"""Small, bounded parser for official login output, never credential storage."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")


def redact_login_chunk(text, pending, secrets, *, final=False):
    """Withhold possible secret prefixes across arbitrary native byte chunks."""
    value = pending + text
    values = [s for s in secrets if s]
    for secret in values:
        value = value.replace(secret, "[REDACTED]")
    overlap = 0
    for secret in values:
        for size in range(min(len(secret) - 1, len(value)), overlap, -1):
            if value.endswith(secret[:size]):
                overlap = size
                break
    if not overlap:
        return value, ""
    return value[:-overlap] + ("[REDACTED]" if final else ""), "" if final else value[-overlap:]


class LoginOutput:
    def __init__(self, partner, event_token=""):
        self.partner, self.event_token = partner, event_token
        self.tails, self.lines, self.offsets, self.prompts = {}, {}, {}, {}
        self.last_link = None
        self.last_device_code = None
        self.success = False
        self.failure = None

    def _allowed_url(self, url):
        value = urlsplit(url)
        host = (value.hostname or "").lower()
        if value.username or value.password:
            return False
        if self.partner == "deepseek":
            return value.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"}
        domains = {"claude": ("claude.ai", "claude.com", "anthropic.com"),
                   "grok": ("x.ai", "grok.com"), "kimi": ("kimi.com", "kimi.ai"),
                   "glm": ("z.ai", "bigmodel.cn"), "qwen": ("qwen.ai", "qwenlm.ai"),
                   "gemini": ("accounts.google.com",)}.get(self.partner, ())
        if self.partner == "grok" and value.path.rstrip(".,):") == "/oauth2/device/code":
            # A failed device-authorization POST mentions its API endpoint.
            # That endpoint is not the browser verification page.
            return False
        if self.partner == "claude" and value.path.endswith("/oauth/authorize"):
            # A terminal soft-wrap must not publish the first URL fragment as
            # a usable login page. The official OAuth request owns these values.
            # Current official releases can use an opaque query token instead
            # of the older named OAuth parameters. Preserve that native URL.
            if not value.query:
                return False
        return value.scheme == "https" and any(host == d or host.endswith("." + d) for d in domains)

    def feed(self, text, source="stdout", *, final=False):
        events = []
        line_buffer = self.lines.get(source, "") + text
        complete = line_buffer.split("\n")
        self.lines[source] = complete.pop()[-65536:]
        if final and self.lines[source]:
            complete.append(self.lines.pop(source))
        for line in complete:
            clean = ANSI.sub("", line).strip()
            try:
                message = json.loads(clean)
            except ValueError:
                message = None
            if isinstance(message, dict):
                if self.partner == "deepseek" and message.get("workdeskAuth") == 1:
                    if self.event_token and message.get("event_token") == self.event_token:
                        events.append({"kind": "account-state", "state": message})
                    continue
                if self.partner == "glm" and message.get("status") == "ready":
                    self.success = True
                # JSON login messages can carry token and private storage fields.
                # They are used for strict completion only, never forwarded raw.
            if re.match(r"(?i)(?:error:|login failed:|login cancelled)", clean):
                self.failure = clean[:2000]
        before = self.tails.get(source, "")
        combined = before + text
        offset = self.offsets.get(source, 0) - len(before)
        self.offsets[source] = self.offsets.get(source, 0) + len(text)
        self.tails[source] = combined[-65536:]
        plain = ANSI.sub("", combined) + ("\n" if final else "")
        if self.partner == "grok" and re.search(r"(?:^|[\r\n])\s*Signed in(?: as\b[^\r\n]*)?\s*(?:[\r\n]|$)", plain):
            self.success = True
        elif self.partner == "kimi" and re.search(r"(?:^|[\r\n])Logged in to [^\r\n]+", plain):
            self.success = True
        # OSC 8 carries the actual link; its visible terminal label can omit
        # OAuth query values. Extract the target before ANSI removes the OSC.
        hyperlinks = re.findall(r"\x1b\]8;[^;\x1b\x07]*;(https?://[^\x1b\x07]+)(?:\x07|\x1b\\)", combined)
        urls = hyperlinks + [u.rstrip(".,)") for u in re.findall(r"https?://[^\s\x1b<>\"']+(?=[\s\x1b<>\"'])", plain)]
        for url in urls:
            if not self._allowed_url(url):
                continue
            if url != self.last_link:
                self.last_link = url
                events.append({"kind": "link", "login_url": url})
            query = parse_qs(urlsplit(url).query)
            for key in ("user_code", "userCode"):
                if query.get(key):
                    self._device_code(query[key][0], events)
        for pattern in (r"(?i:then enter this code|confirm this code in your browser|enter code)\s*:\s*([A-Z0-9][A-Z0-9-]{3,31})(?=\s)",):
            for match in re.finditer(pattern, plain):
                self._device_code(match[1], events)
        if self.partner == "claude":
            # The official prompt is deliberately not newline terminated.
            for match in re.finditer(r"(?i)Paste code here(?: if prompted)?\s*>", plain):
                end = offset + match.end()
                if end > self.prompts.get(source, -1):
                    self.prompts[source] = end
                    events.append({"kind": "code-prompt"})
        return events

    def _device_code(self, code, events):
        if re.fullmatch(r"[A-Za-z0-9-]{4,32}", code) and code != self.last_device_code:
            self.last_device_code = code
            events.append({"kind": "device-code", "user_code": code, "login_url": self.last_link})
