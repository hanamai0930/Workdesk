"""Read-only business projection of the versioned official partner connections."""
from copy import deepcopy
import json
from pathlib import Path

from .partner_usage import catalog, objects
from .partners.native_features import capabilities
from .store import digest


def turn_models(db, row, api, default_model):
    """Only models selectable with this exact saved connection and billing route."""
    choices = {default_model: '默认' if default_model == 'official-default' else default_model}
    if row.get('route') == 'api':
        models = api.get('models', [])
    elif row.get('partner_id') == 'deepseek' and db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_partner_events'").fetchone():
        record = db.execute("""SELECT json_extract(e.data,'$.models')
            FROM core_partner_objects r JOIN core_partner_events e ON r.id=e.run_id
            WHERE r.kind='run' AND json_extract(r.data,'$.connection_id')=?
            AND json_extract(r.data,'$.connection_revision')=?
            AND json_extract(r.data,'$.account_id')=? AND json_extract(r.data,'$.release_id')=?
            AND json_extract(e.data,'$.kind')='native_model_selected'
            ORDER BY e.seq DESC LIMIT 1""", (row['id'], row.get('revision'), row.get('account_id'),
                                            row.get('release_id'))).fetchone()
        models = json.loads(record[0]) if record and record[0] else []
    else:
        models = []
    for model in models:
        if isinstance(model, dict) and isinstance(model.get('id'), str) and model['id']:
            choices[model['id']] = model.get('name') or model['id']
    return [{'id': model, 'name': name} for model, name in choices.items()]


def projection(db):
    partners, providers = catalog()
    from .partners.permissions import catalog as permission_catalog
    permission_modes = {row['partner_id']: row['modes'] for row in permission_catalog()}
    names = {p['id']: p['name'] for p in partners}
    apis = {p['id']: p for p in providers}
    accounts = {a['id']: a for a in objects(db, 'account')}
    policies = {p['id']: p for p in objects(db, 'release-policy')}
    connections = []
    for raw in objects(db, 'connection'):
        row = deepcopy(raw)
        account = accounts.get(row.get('account_id'), {})
        partner, route = row.get('partner_id'), row.get('route')
        api = row.get('api_provider') or row.get('driver_provider') if route == 'api' else None
        if route == 'api' and not api and account.get('kind') == 'deepseek_api':
            api = 'deepseek'
        release = row.get('release_snapshot') or {}
        available, reason = True, ''
        if row.get('state') in ('DISABLED', 'REMOVED') or account.get('state') == 'DISCONNECTED':
            available, reason = False, '连接已停用'
        elif not account or account.get('partner_id') != partner:
            available, reason = False, '账号引用不匹配'
        elif not release.get('path') or not Path(release['path']).is_dir() or policies.get(row.get('release_id'), {}).get('state') in ('DISABLED', 'REMOVED'):
            available, reason = False, '工作端版本未就绪'
        elif route == 'api' and (not account.get('secret_version') or api not in apis or apis[api]['compatibility'].get(partner, {}).get('state') != 'supported'):
            available, reason = False, 'API 凭据或连接未就绪'
        elif route == 'account' and account.get('auth_state', account.get('state')) != 'AUTHENTICATED':
            available, reason = False, '官方账号待登录或核对'
        model = row.get('model') if route == 'api' else row.get('model') or 'official-default'
        source = (apis.get(api, {}).get('name', api or '') + ' API') if route == 'api' else '官方账号'
        features = capabilities({'provider':partner,'route':route,'api_provider':api,
                                 'release_id':row.get('release_id'),'version':release.get('version')})
        selectable_models = turn_models(db, row, apis.get(api, {}), model)
        row.update(turn_models=selectable_models, kind='partner_connection', execution='partner_native', provider=partner,
                   revision=str(row.get('revision', '')), api_provider=api, api_name=apis.get(api, {}).get('name'),
                   account_label=account.get('label'), account_revision=account.get('revision'),
                   auth_generation=account.get('auth_generation') or account.get('login_run_id'),
                   secret_version=account.get('secret_version'), available=available, enabled=row.get('state') != 'DISABLED',
                   reason=reason, status='CONNECTED' if available else 'UNAVAILABLE',
                   model=model, model_label='官方默认模型' if model == 'official-default' else model,
                   models=[{'id': model, 'name': '官方默认模型' if model == 'official-default' else model}],
                   name=names.get(partner, partner) + ' · ' + source + ' · ' + account.get('label', '账号待核对'),
                   version=release.get('version'), is_default=False, quota_id='partner-quota:' + row['id'],
                   credential_reference={'credential_revision': account.get('secret_version') or str(account.get('revision', '')),
                                         'reference': 'partner:' + row['id'], 'file_revision': None},
                   source_label=source, auth_options=account.get('auth_options') or {},
                   permission_modes=permission_modes.get(partner, []),
                   capabilities=features)
        connections.append(row)
    return {'partners': partners, 'api_providers': providers, 'connections': connections, 'source': 'VERSIONED_PARTNER_RECORDS', 'no_dispatch': True}


def selected(db, identity, revision=None):
    row = next((c for c in projection(db)['connections'] if c['id'] == identity), None)
    if not row or revision is not None and str(revision) != row['revision']:
        raise ValueError('伙伴连接已变化，请刷新后重新选择。')
    if not row['available']:
        raise ValueError(row['reason'])
    return row


def check_frozen(db, frozen):
    current = selected(db, frozen.get('id'), frozen.get('revision'))
    for key in ('provider', 'account_id', 'secret_version', 'auth_generation', 'release_id', 'route', 'api_provider', 'model'):
        expected = frozen.get('saved_model', frozen.get('model')) if key == 'model' else frozen.get(key)
        if expected != current.get(key):
            raise ValueError('伙伴账号、凭据、版本或模型已变化；未改用其他连接。')
    if frozen.get('saved_model') is not None:
        return {**current, 'saved_model': current['model'], 'model': frozen['model']}
    return current


def model_member(connection):
    revision = int(digest({k:connection.get(k) for k in ('id','revision','account_id','auth_generation','secret_version','release_id')})[:13],16)+1
    return {'id': 'partner-member:' + connection['id'], 'revision': revision,
            'name': connection['name'], 'role': '工作伙伴', 'provider': connection['provider'],
            'model': connection['model'], 'effort': 'default', 'tier': 'standard',
            'connection_id': connection['id'], 'connection_revision': connection['revision'],
            'kind': 'partner_connection', 'enabled': True, 'available': connection['available']}


class PartnerConnectionContext:
    def __init__(self, locations, connection):
        self.source, self.connection = locations, connection
        self.provider = connection['provider']
        self.credential_revision = digest({k: connection.get(k) for k in ('id', 'revision', 'account_id', 'secret_version', 'release_id')})

    def __getattr__(self, name):
        return getattr(self.source, name)

    def validate(self):
        return self

    def credentials(self):
        raise ValueError('伙伴凭据只由 Go 核心交给准确官方工作端，不提供给旧执行器。')
