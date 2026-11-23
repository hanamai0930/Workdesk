"""Account setup for the existing official API routes, without dispatch."""

import os
import re
from urllib.error import HTTPError

from . import workspace
from .chat_accounts import connections as chat_connections
from .credential_vault import encode
from .model_catalog import entries
from .paths import legacy
from .setup_account import work_login_options
from .store import canonical, uid


_PROVIDERS = ('deepseek', 'qwen')


def status(service):
    """Report local readiness without returning credentials or contacting a provider."""
    locations = service.locations
    work = {key: False for key in ('codex', *_PROVIDERS)}
    api = dict.fromkeys(_PROVIDERS, False)
    try:
        executable = locations.codex
    except OSError:
        executable = None
    if executable:
        try:
            work['codex'] = (locations.codex_home / 'auth.json').is_file()
        except (OSError, ValueError):
            pass
    try:
        credentials = service.profiles.credentials()
        api['deepseek'] = 'deepseek' in credentials
        qwen = credentials.get('qwen')
        api['qwen'] = qwen is not None and qwen.billing_kind == 'general_api'
    except (OSError, ValueError):
        pass
    connected = {provider: work[provider] or api.get(provider, False) for provider in work}
    if not all(connected.values()):
        try:
            with service.store.read() as db:
                routes = service.resources.connections(db, service.profiles.list(db))
            for route in routes:
                if route.get('available') and route['provider'] in connected:
                    connected[route['provider']] = True
        except (OSError, ValueError):
            pass
    codex_available = bool(executable and locations.user)
    chat = chat_connections(locations)
    return {'ready': any(connected.values()) or any(chat.values()), 'account_available': codex_available,
            'connections': connected, 'work_accounts': work, 'chat_accounts': chat,
            'api_connections': api,
            'work_login': work_login_options(locations, codex_available)}


def _candidate(service, payload):
    if not isinstance(payload, dict) or set(payload) != {'provider', 'key'} or payload['provider'] not in _PROVIDERS:
        raise ValueError('请选择 DeepSeek 或 Qwen 并输入 API Key。')
    provider = payload['provider']
    key = payload['key'].strip() if isinstance(payload['key'], str) else ''
    if not re.fullmatch(r'[!-~]{1,512}', key) or any(char in key for char in "\"'$`\\"):
        raise ValueError('请输入有效的单行 API Key。')
    reader = legacy(service.locations, 'provider-harness', 'credentials')
    secret = reader.credential(provider, reader.KEY_NAMES[provider][0], key)
    if provider == 'qwen' and secret.billing_kind != 'general_api':
        raise ValueError('请选择千问通用按量 API Key。')
    return reader, secret


def _check(service, secret):
    try:
        result = service.profiles.api_catalog(secret.provider, secret)
    except HTTPError as error:
        if error.code in (401, 403):
            raise ValueError('官方服务未接受此 API Key，请核对后重试。') from None
        raise ValueError('暂时无法读取官方模型目录，请稍后重试。') from None
    except (OSError, ValueError, TypeError):
        raise ValueError('暂时无法读取官方模型目录，请稍后重试。') from None
    # This is exactly the metadata destined for the catalog and audit.
    if secret.reveal_for_provider() in canonical(result):
        raise ValueError('官方目录响应格式异常，请重新核对。')
    if not any(model['runnable'] for model in entries(secret.provider, result)):
        raise ValueError('此 API Key 的官方目录没有 Workdesk 当前可用的模型。')
    return result


def _value(credentials, provider):
    secret = credentials.get(provider)
    return secret.reveal_for_provider() if secret else None


def _assignment(name, value):
    if value.startswith(('#', '"', "'")):
        quote = "'" if "'" not in value else '"'
        value = quote + value + quote
    return f'{name}={value}\n'


def _keyset(service, reader, credentials):
    user = service.locations.user
    folder = user / 'config/provider-keysets' / uid()
    target = workspace.plain(folder / '.env', user, missing=True)
    raw = ''.join(_assignment(reader.KEY_NAMES[provider][0], _value(credentials, provider))
                  for provider in _PROVIDERS if provider in credentials).encode('utf-8')
    raw = encode(raw)
    # Write a new immutable file before activating its fingerprinted reference.
    workspace.io_path(folder).mkdir(mode=0o700, parents=True)
    fd = os.open(workspace.io_path(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return target


def save_key(service, payload):
    """Verify, protect and select just the submitted provider's API credential."""
    reader, secret = _candidate(service, payload)
    provider = secret.provider
    service.local_storage._require_user()
    with service.lock, service.store.read() as db:
        service.persistence.idle(db)
        previous = _value(service.profiles.credentials(), provider)
    catalog = _check(service, secret)
    with service.lock:
        with service.store.read() as db:
            service.persistence.idle(db)
            current = service.profiles.credentials()
        if _value(current, provider) != previous:
            raise ValueError('此服务的连接已在另一个窗口更新，请核对后重试。')
        if previous != secret.reveal_for_provider():
            target = _keyset(service, reader, {**current, provider: secret})
            # Only the new local reference goes through the existing journal.
            service.command('storage.credentials.select', {'api_env': str(target)}, uid())
        service.profiles.install(provider, catalog)
        return status(service)
