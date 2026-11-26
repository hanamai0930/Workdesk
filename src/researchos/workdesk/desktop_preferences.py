"""Preferences shared between the local workbench window and its controller."""
from copy import deepcopy
import json
from hashlib import sha256
import re
import time
from pathlib import Path

from .chat_api import API_MODELS
from .paths import atomic_bytes, atomic_json, data_owner_lock
from .workspace import plain
from .partners.permissions import catalog as permission_catalog, defaults as permission_defaults, resolve as resolve_permission


DEFAULTS = {
    'launch': 'chat',
    'close': 'keyboard',
    'opener': 'system',
    'userNickname': '',
    'assistantNickname': '',
    'workDirectory': '',
    'notifications': {'approval': True, 'failure': True, 'review': True},
    'connectionNoticeSeconds': 10,
    'workAccess': permission_defaults(),
    'workProvider': 'codex',
    'chatDefaults': None,
    'lastLocation': 'chat',
}
_ENUMS = {'launch': {'chat', 'work', 'last'}, 'close': {'keyboard', 'always', 'never'},
          'opener': {'system', 'explorer'}, 'workProvider': set(DEFAULTS['workAccess'])}
_ROUTE = re.compile(r'(chat|work|work-items|resources|projects|records|results|files|monitor|control|operations|planning|libraries|extensions|management|collab|drafts|archives|schedules)(/[A-Za-z0-9._:%-]+){0,5}')


def _directory(value):
    path = Path(value)
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')) or path == Path(path.anchor):
        raise ValueError('请选择本机的工作文件夹。')
    path = plain(path, Path(path.anchor))
    if not path.is_dir():
        raise ValueError('项目外工作目录不存在，请在设置中重新选择。')
    return path.resolve()


def _metadata():
    from .chat import web_providers
    return {'workAccessOptions': permission_catalog(), 'chatWebProviders': web_providers()}


def _checked(key, value):
    if key in _ENUMS:
        if not isinstance(value, str) or value not in _ENUMS[key]:
            raise ValueError('不支持此偏好选项。')
    elif key in ('userNickname', 'assistantNickname'):
        if not isinstance(value, str) or re.search(r'[\x00-\x1f\x7f\u2028\u2029\ud800-\udfff]', value):
            raise ValueError('称呼必须是单行文字。')
        value = value.strip()
        if len(value) > 40:
            raise ValueError('称呼最多 40 个字符。')
    elif key == 'workDirectory':
        if not isinstance(value, str) or len(value) > 1024 or any(ord(ch) < 32 for ch in value):
            raise ValueError('工作目录格式无效。')
    elif key == 'notifications':
        if not isinstance(value, dict) or set(value) != set(DEFAULTS[key]) or not all(type(v) is bool for v in value.values()):
            raise ValueError('任务通知设置无效。')
    elif key == 'connectionNoticeSeconds':
        if type(value) is not int or value not in (0, 5, 10, 15, 30):
            raise ValueError('请选择可用的连接提示时长。')
    elif key == 'workAccess':
        if not isinstance(value, dict) or not value or not set(value).issubset(DEFAULTS[key]):
            raise ValueError('请选择工作端支持的默认审批方式。')
        for provider, mode in value.items():
            resolve_permission(provider, mode)
    elif key == 'chatDefaults':
        from .chat import web_providers
        if isinstance(value, dict) and value.get('model') == 'deepseek-pro':
            value = {**value, 'model': 'deepseek-v4-pro'}
        if value is not None and (not isinstance(value, dict) or set(value) not in ({'route','model','provider'}, {'route','model','provider','apiProvider'})
                or value.get('route') not in ('api','web') or value.get('apiProvider', 'deepseek') not in API_MODELS
                or value.get('model') not in API_MODELS.get(value.get('apiProvider', 'deepseek'), ())
                or value.get('provider') not in ({row['id'] for row in web_providers()} if value.get('route') == 'web' else {'codex','deepseek','qwen'})):
            raise ValueError('请选择支持的 Chat 渠道与模型。')
    elif key == 'lastLocation':
        if not isinstance(value, str) or len(value) > 280 or not _ROUTE.fullmatch(value):
            raise ValueError('该页面不能作为启动位置。')
    else:
        raise ValueError('偏好设置不存在。')
    return value


def _decode(raw):
    stored = json.loads(raw.decode('utf-8'))
    if not isinstance(stored, dict) or stored.get('schema') != 1 or not isinstance(stored.get('values'), dict):
        raise ValueError('工作台偏好文件格式无效。')
    values = deepcopy(DEFAULTS)
    for key, value in stored['values'].items():
        if key in DEFAULTS:
            checked = _checked(key, value)
            values[key] = {**values[key], **checked} if key == 'workAccess' else checked
    return values


def read(user_root):
    if user_root:
        path = Path(user_root) / 'config/desktop/preferences.json'
        if path.is_file():
            try:
                return _decode(path.read_bytes())
            except (ValueError, TypeError):
                raise ValueError('工作台偏好已损坏。请在设置中恢复偏好并重新核对工作目录与权限。') from None
    return deepcopy(DEFAULTS)


def snapshot(user_root):
    """Preview damaged preferences without changing either file."""
    if not user_root:
        return {**deepcopy(DEFAULTS), **_metadata()}
    directory = Path(user_root) / 'config/desktop'
    active = directory / 'preferences.json'
    if not active.is_file():
        return {**deepcopy(DEFAULTS), **_metadata()}
    raw = active.read_bytes()
    try:
        values = _decode(raw)
        receipt = json.loads(raw.decode('utf-8')).get('lastRecovery')
        if (isinstance(receipt, dict) and receipt.get('source') in ('backup', 'defaults')
                and type(receipt.get('at')) in (int, float)
                and re.fullmatch(r'[0-9a-f]{64}', str(receipt.get('original')))):
            values['lastRecovery'] = receipt
        return {**values, **_metadata()}
    except (ValueError, TypeError):
        backup = None
        previous = directory / 'preferences.previous.json'
        if previous.is_file():
            candidate = previous.read_bytes()
            try:
                saved = _decode(candidate)
                backup = {'revision': sha256(candidate).hexdigest(), 'workDirectory': saved['workDirectory'],
                          'workAccess': saved['workAccess']}
            except (ValueError, TypeError):
                pass
        return {**deepcopy(DEFAULTS), **_metadata(), 'recovery': {'revision': sha256(raw).hexdigest(), 'backup': backup}}


def recover(user_root, request):
    directory = Path(user_root) / 'config/desktop'
    path = directory / 'preferences.json'
    if set(request) != {'source', 'revision', 'backupRevision'} or request['source'] not in ('defaults', 'backup'):
        raise ValueError('请选择恢复来源。')
    with data_owner_lock(directory):
        state = snapshot(user_root).get('recovery')
        if not state or state['revision'] != request['revision']:
            raise ValueError('偏好已变化，请刷新后核对。')
        raw = path.read_bytes()
        values = deepcopy(DEFAULTS)
        if request['source'] == 'backup':
            if not state['backup'] or state['backup']['revision'] != request['backupRevision']:
                raise ValueError('备份已变化，请刷新后重新核对。')
            values = _decode((directory / 'preferences.previous.json').read_bytes())
            if values['workDirectory']:
                folder = _directory(values['workDirectory'])
                if folder.is_relative_to(Path(user_root).resolve()):
                    raise ValueError('备份中的工作目录不可用，请选择恢复默认后重新设置。')
        archive = directory / ('preferences.damaged-' + state['revision'] + '.bin')
        if not archive.exists():
            atomic_bytes(archive, raw)
        if archive.read_bytes() != raw:
            raise ValueError('原件核对失败，请保留现有文件。')
        receipt = {'source': request['source'], 'at': time.time(), 'original': state['revision']}
        atomic_json(path, {'schema': 1, 'values': values, 'lastRecovery': receipt})
        return snapshot(user_root)


def save(user_root, key, value):
    value = _checked(key, value)
    if key == 'workDirectory' and value:
        value = str(_directory(value))
        root = Path(user_root).resolve()
        if Path(value).is_relative_to(root):
            raise ValueError('请选择应用资料目录以外的工作文件夹。')
    directory = Path(user_root) / 'config' / 'desktop'
    directory.mkdir(parents=True, exist_ok=True)
    with data_owner_lock(directory):
        values = read(user_root)
        path = directory / 'preferences.json'
        original = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'schema': 1, 'values': {}}
        if path.is_file():
            atomic_bytes(directory / 'preferences.previous.json', path.read_bytes())
        values[key] = {**values[key], **value} if key == 'workAccess' else value
        original['values'].update(values)
        atomic_json(path, original)
        return snapshot(user_root)


def scratch_project(service, db, expected=None):
    configured = read(service.locations.user)['workDirectory']
    if expected is not None and expected != configured:
        raise ValueError('项目外工作目录已变化，请重新打开 Workdesk 并确认工作权限。')
    if configured:
        root = _directory(configured)
        if root.is_relative_to(service.locations.data.resolve()):
            raise ValueError('请在设置中选择控制服务数据以外的工作目录。')
        prior = db.execute('SELECT id FROM projects WHERE root=?', (str(root),)).fetchone()
        if prior:
            service.work_records.guard_project(db, prior['id'])
            return {'project_id': prior['id']}
    else:
        root = service.locations.data / 'unfiled-work'
        root.mkdir(exist_ok=True)
    return service._project(db, {'root': str(root), 'name': '未归入项目的工作'})
