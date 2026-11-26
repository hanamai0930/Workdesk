"""Frozen official API transport catalog shared with the Go owner."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path


@lru_cache(maxsize=1)
def catalog():
    raw = Path(__file__).with_name("api_providers.json").read_bytes()
    data = json.loads(raw)
    if data.get("schema") != 1:
        raise ValueError("Unsupported API provider catalog format")
    return data, hashlib.sha256(raw).hexdigest()


def provider_for(provider_id=None, *, snapshot=None, catalog_sha256=None):
    # Only a missing legacy field means DeepSeek. Unknown IDs never fall back.
    ident = "deepseek" if provider_id is None else provider_id
    data, digest = catalog()
    value = next((p for p in data["api_providers"] if p["id"] == ident), None)
    if value is None:
        raise ValueError("Unknown frozen API provider")
    if snapshot is not None and snapshot != value:
        raise ValueError("API provider snapshot differs from the frozen worker catalog")
    if catalog_sha256 is not None and catalog_sha256 != digest:
        raise ValueError("API catalog identity differs from the frozen worker")
    return value


def validate_binding(config):
    provider = provider_for(config.get("api_provider"), snapshot=config.get("api_provider_snapshot"),
                            catalog_sha256=config.get("api_catalog_sha256"))
    if config.get("driver_provider") not in (None, "", provider["id"]):
        raise ValueError("Driver and API provider identities differ")
    compatibility = provider["compatibility"].get(config["partner_id"])
    if not compatibility or compatibility["state"] != "supported":
        raise ValueError(compatibility["reason"] if compatibility else "This native API combination is not registered")
    model = config.get("model")
    if not isinstance(model, str) or not 1 <= len(model) <= 200 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in model):
        raise ValueError("An exact API model ID is required")
    return provider
