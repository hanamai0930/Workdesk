"""Lookup exact supported native features from the frozen managed-release catalog."""
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def catalog():
    raw = Path(__file__).with_name('native_features.json').read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError('本机原生能力目录无法核对。')
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get('schema') != 1 or not isinstance(value.get('releases'), dict):
        raise ValueError('本机原生能力目录无法核对。')
    return value['releases']


def feature(binding, key, access=None):
    row = catalog().get(binding.get('release_id')) or {}
    if row.get('partner_id') != binding.get('provider') or row.get('version') != binding.get('version'):
        return {}
    value = row.get(key) or {}
    route = binding.get('route')
    if route not in value.get('routes', ()):
        return {}
    if route == 'api' and value.get('api_providers') and binding.get('api_provider') not in value['api_providers']:
        return {}
    if access and value.get('permission_modes') and access not in value['permission_modes']:
        return {}
    return value


def capabilities(binding):
    native, mcp = feature(binding, 'resume'), feature(binding, 'mcp')
    return {'native_resume': bool(native.get('method')), 'auto_continue': bool(native.get('method') and native.get('automatic') is True),
            'per_task_mcp': bool(mcp.get('transports')), 'mcp_transports': list(mcp.get('transports', ())),
            'mcp_stdio_cwd': mcp.get('stdio_cwd'), 'mcp_project_trust': mcp.get('project_trust') is True,
            'skills': 'document_paths', 'native_controls': 'PARTNER_RUN_ONLY'}
