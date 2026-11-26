"""Frozen native permission choices; no model, login, or user-preference reads."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path


def _catalog():
    raw = Path(__file__).with_name('permissions.json').read_bytes()
    value = json.loads(raw)
    partners = json.loads(Path(__file__).with_name('api_providers.json').read_text(encoding='utf-8'))['partners']
    by_id = {row['partner_id']: row for row in value['partners']}
    if value.get('schema') != 1 or len(by_id) != len(value['partners']) or set(by_id) != {row['id'] for row in partners}:
        raise ValueError('官方工作端权限目录不完整，请安全更新程序。')
    rows = []
    for partner in partners:
        row = deepcopy(by_id[partner['id']])
        row['name'] = partner['name']
        if row.get('default') != 'workspace' or not any(mode.get('id') == 'workspace' and mode.get('supported') is True for mode in row['modes']):
            raise ValueError('官方工作端缺少安全的默认审批方式。')
        rows.append(row)
    return rows, sha256(raw).hexdigest()


def catalog():
    return _catalog()[0]


def defaults():
    return {row['partner_id']: row['default'] for row in catalog()}


def resolve(partner, value='workspace'):
    rows, digest = _catalog()
    row = next((row for row in rows if row['partner_id'] == partner), None)
    if row is None:
        raise ValueError('未知官方工作端。')
    mode = next((mode for mode in row['modes'] if mode['id'] == value), None)
    if mode is None:
        raise ValueError('请选择该工作端支持的审批方式。')
    if mode.get('supported') is not True:
        raise ValueError(mode.get('reason') or '该官方工作端不支持此审批方式。')
    return {'partner_id': partner, 'value': value, 'native': deepcopy(mode['native']), 'catalog_sha256': digest}


def checked(config):
    """Validate the owner-frozen choice; never reread a changed default at run time."""
    frozen = config.get('resolved_permission')
    options = config.get('options') or {}
    value = frozen.get('value') if isinstance(frozen, dict) else options.get('work_access', 'workspace')
    expected = resolve(config['partner_id'], value)
    if frozen is not None and frozen != expected:
        raise ValueError('冻结的原生权限选择与当前准确目录不符；未发送模型请求。')
    native = expected['native']
    direct = native.get('thread', {})
    for key in ('approvalPolicy','approvalsReviewer','sandbox'):
        if key in options and options[key] != direct.get(key):
            raise ValueError('原生权限参数与已选择的审批方式冲突。')
    if 'mode' in options and native.get('transport') == 'zcode' and options['mode'] != native['mode']:
        raise ValueError('GLM 原生模式与已选择的审批方式冲突。')
    return expected


def apply_acp(rpc, session, session_id, permission):
    target = permission['native']['mode_id']
    advertised = {row.get('id') for row in (session.get('modes') or {}).get('availableModes', [])}
    for option in session.get('configOptions') or []:
        if option.get('id') == 'mode' or option.get('category') == 'mode':
            advertised.update(row.get('value') for row in option.get('options', []))
    if target not in advertised:
        raise ValueError('当前官方工作端未广告所选审批模式；未发送模型请求。')
    receipt = rpc.request('session/set_mode', {'sessionId': session_id, 'modeId': target})
    observed = (receipt.get('modes') or {}).get('currentModeId', receipt.get('currentModeId'))
    if observed is not None and observed != target:
        raise ValueError('官方工作端未接受所选审批模式；未发送模型请求。')
    return receipt


def validate_codex_receipt(receipt, permission):
    expected = permission['native']['thread']
    for key in ('approvalPolicy','approvalsReviewer'):
        if key in receipt and receipt[key] != expected[key]:
            raise ValueError('Codex 官方生效审批设置与本次选择不符；未发送模型请求。')
    sandbox = receipt.get('sandbox')
    if isinstance(sandbox, dict):
        native = {'workspace-write':'workspaceWrite','danger-full-access':'dangerFullAccess'}[expected['sandbox']]
        if sandbox.get('type') != native:
            raise ValueError('Codex 官方生效文件权限与本次选择不符；未发送模型请求。')
