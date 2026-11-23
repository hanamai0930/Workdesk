"""Read explicit local provider credentials without changing process environment.

This is a small literal .env reader, not a shell or a full dotenv interpreter.
The status entrypoint never imports an SDK or makes network requests.
"""

import argparse
from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
KEY_NAMES = {
    "deepseek": ("WORKDESK_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
    "qwen": ("WORKDESK_QWEN_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY"),
}
KNOWN_NAMES = frozenset(name for names in KEY_NAMES.values() for name in names)
ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


class CredentialError(ValueError):
    """Safe message only: never include file contents or secret values."""


@dataclass(frozen=True)
class Credential:
    provider: str
    variable: str
    billing_kind: str
    _value: str = field(repr=False)

    def reveal_for_provider(self):
        """Only the selected provider adapter should call this method."""
        return self._value


def credential(provider, name, value):
    billing = 'official_api'
    if provider == 'qwen':
        billing = 'subscription' if value.startswith('sk-sp-') else 'general_api' if value.startswith('sk-') else 'unknown'
    return Credential(provider, name, billing, value)


def _literal_value(raw, line_number):
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        return ""
    if raw[0] in ("'", '"'):
        quote = raw[0]
        end = raw.find(quote, 1)
        if end < 0 or (raw[end + 1:].strip() and not raw[end + 1:].strip().startswith("#")):
            raise CredentialError(f"Invalid credential quoting at line {line_number}.")
        value = raw[1:end]
    else:
        value = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
    # No shell interpolation, escapes, multiline values or whitespace in keys.
    if any(char.isspace() for char in value) or any(char in value for char in ('$', '`', '\\', '\x00')):
        raise CredentialError(f"Unsupported credential syntax at line {line_number}.")
    return value


def load_credentials(path=ENV_PATH, *, expected_sha256=None, with_fingerprint=False):
    """Read only recognized entries from one explicit UTF-8 file.

    No system/user environment fallback, mutation, shell evaluation, SDK import,
    file discovery, credential validation request or implicit provider dispatch.
    """
    path = Path(path)
    if not path.is_file():
        if expected_sha256 is not None:raise CredentialError('Selected credential file is unavailable.')
        return ({},None) if with_fingerprint else {}
    if path.is_symlink() or path.stat().st_size > 65536:
        raise CredentialError("Credential file must be a small regular local file.")
    try:
        with path.open('rb') as stream:raw=stream.read(65537)
        if len(raw)>65536:raise CredentialError('Credential file must be a small regular local file.')
        fingerprint=sha256(raw).hexdigest()
        if expected_sha256 is not None and fingerprint!=expected_sha256:raise CredentialError('Selected credential file version changed.')
        if raw.startswith(b'WORKDESK-DPAPI-V1\0'):
            from researchos.workdesk.credential_vault import decode
            raw = decode(raw)
        content = raw.decode("utf-8-sig")
    except (OSError, UnicodeError):
        raise CredentialError("Cannot read credential file as UTF-8.") from None
    except ValueError as exc:
        if isinstance(exc, CredentialError):
            raise
        raise CredentialError("Cannot read this user's protected credential file.") from None
    values = {}
    for number, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ASSIGNMENT.fullmatch(stripped)
        if match is None:
            raise CredentialError(f"Invalid .env assignment at line {number}.")
        name, raw = match.groups()
        if name not in KNOWN_NAMES:
            continue
        if name in values:
            raise CredentialError(f"Duplicate credential variable at line {number}.")
        values[name] = _literal_value(raw, number)
    result = {}
    for provider, names in KEY_NAMES.items():
        configured = [(name, values[name]) for name in names if values.get(name)]
        if not configured:
            continue
        if len({value for _, value in configured}) != 1:
            raise CredentialError(f"Conflicting credential aliases for {provider}.")
        name, value = configured[0]
        result[provider] = credential(provider, name, value)
    return (result,fingerprint) if with_fingerprint else result


def credential_status(path=ENV_PATH):
    credentials = load_credentials(path)
    return {
        "file_exists": Path(path).is_file(),
        "network_requests": 0,
        "providers": {
            provider: {
                "configured": provider in credentials,
                "billing_kind_hint": credentials[provider].billing_kind if provider in credentials else "unknown",
                "credential_validity": "NOT_TESTED",
            }
            for provider in KEY_NAMES
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Local-only credential presence check; no network requests.")
    parser.parse_args()
    try:
        status = credential_status()
    except CredentialError as exc:
        print(json.dumps({"error": str(exc), "network_requests": 0}, ensure_ascii=False))
        return 1
    except OSError:
        print(json.dumps({"error": "Cannot inspect local credential file.", "network_requests": 0}))
        return 1
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
