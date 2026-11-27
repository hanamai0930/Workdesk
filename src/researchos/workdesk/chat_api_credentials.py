"""Resolve the exact protected credential selected for direct API Chat."""
from pathlib import Path
import json
import uuid
import hmac
import os

from .credential_vault import decode, PREFIX
from .paths import legacy
from .workspace import plain

PROVIDERS=('deepseek','qwen')


def canonical_uuid(value):
    try: return str(uuid.UUID(value)) == value
    except (TypeError, AttributeError, ValueError): return False


def _provider(row):
    if row.get('kind') == 'deepseek_api' and row.get('api_provider', '') in ('', 'deepseek'):
        return 'deepseek'
    if row.get('kind') == 'api':
        return row.get('api_provider')


def shared_reference(db, provider, locations):
    """Return an internal reference and whether Chat explicitly selected it."""
    selected = db.execute("SELECT data FROM core_partner_objects WHERE kind='chat-api-key' AND id=?", (provider,)).fetchone()
    selected = json.loads(selected['data']) if selected else None
    if selected and selected.get('mode') == 'chat':
        return ('chat', selected.get('account_id'), selected.get('secret_version')), True
    rows = (json.loads(row['data']) for row in db.execute("SELECT data FROM core_partner_objects WHERE kind='account' ORDER BY id"))
    choices = [row for row in rows if _provider(row) == provider and row.get('state') != 'DISCONNECTED'
               and row.get('key_ready') is True and canonical_uuid(row.get('id')) and canonical_uuid(row.get('secret_version'))]
    if selected:
        if selected.get('mode') != 'work':
            return None, True
        choices = [row for row in choices if row['id'] == selected.get('account_id')]
        if not choices:
            return None, True
    if not choices:
        return None, selected is not None
    anchor = choices[0]
    if len({row['secret_version'] for row in choices}) > 1:
        try:
            first = read_key(locations, anchor['id'], anchor['secret_version'])
            if any(not hmac.compare_digest(first, read_key(locations, row['id'], row['secret_version']))
                   for row in choices if row['secret_version'] != anchor['secret_version']):
                return None, False
        except (OSError, ValueError):
            return None, False
    return ('work', anchor['id'], anchor['secret_version']), selected is not None


def read_key(locations, owner, version):
    if not canonical_uuid(owner) or not canonical_uuid(version):
        raise ValueError('凭据引用无效。')
    path = Path(locations.user) / 'providers' / 'partner-secrets' / f'{owner}-{version}.vault'
    plain(path, locations.user)
    if path.stat().st_size > 64 * 1024:
        raise ValueError('凭据文件格式无效。')
    sealed = path.read_bytes()
    if os.name == 'nt' and not sealed.startswith(PREFIX):
        raise ValueError('凭据文件未受保护。')
    return decode(sealed)


def resolve(service, provider, db=None):
    if provider not in PROVIDERS:
        raise ValueError('当前普通 Chat 不支持所选 API 厂商。')
    reference, explicit = None, False
    if service.store.managed:
        if db is None:
            with service.store.read() as connection:
                return resolve(service, provider, connection)
        # Stage 1 did not have a partner store. Its existing Chat key path
        # stays supported until a stage 2 schema is explicitly selected.
        schema = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if schema and schema[0] == '3':
            reference, explicit = shared_reference(db, provider, service.locations)
    if not explicit:
        credentials = service.profiles.credentials()
        found = credentials.get(provider)
        if found and (provider != 'qwen' or found.billing_kind == 'general_api'):
            return found, {'source':'legacy','revision':service.locations.credential_revision}
    if reference:
        source, owner, version = reference
        if canonical_uuid(owner) and canonical_uuid(version):
            try:
                key = read_key(service.locations, owner, version).decode('utf-8')
                reader = legacy(service.locations, 'provider-harness', 'credentials')
                found = reader.credential(provider, reader.KEY_NAMES[provider][0], key)
                if not key or any(char.isspace() for char in key) or provider == 'qwen' and found.billing_kind != 'general_api':
                    raise ValueError('未配置通用 API Key。')
                return found, {'source':source,'account_id':owner,'version':version}
            except (OSError, ValueError):
                raise ValueError('所选普通 Chat API 凭据不可读或不适用，请重新选择。') from None
    raise ValueError('普通 Chat 尚未配置可用的同厂商通用 API Key。')
