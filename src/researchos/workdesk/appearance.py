"""Versioned UI appearance only: no credentials, chat contents or browser data."""
from copy import deepcopy
import math
import re
import time

from .contracts import integer

REGIONS = (
    ('shell', '应用背景'), ('toolbar', '顶部工具栏'), ('rail', '左侧图标栏'),
    ('brand', '品牌与搜索'), ('new-chat', '新聊天按钮'), ('chat-list', '聊天列表'),
    ('selection', '选中项'), ('mode-tabs', 'Chat / Work 切换'), ('main', '主聊天区'),
    ('composer', '输入框'), ('user-bubble', '用户气泡'),
    ('chat.api', 'DeepSeek API'), ('chat.codex', 'ChatGPT 官网'),
    ('chat.deepseek', 'DeepSeek 官网'), ('chat.qwen', 'Qwen 官网'),
)
COLORS = {'chat.codex': '#10A37F', 'chat.qwen': '#6B50E8'}
FIELDS = {'background', 'backgroundOpacity', 'border', 'borderOpacity'}


def defaults():
    regions = {}
    for identity, _ in REGIONS:
        color = COLORS.get(identity, '#4D6BFE')
        opacity = .05 if identity.startswith('chat.') else .04 if identity in {'new-chat', 'mode-tabs'} else .03
        regions[identity] = {'background': color, 'backgroundOpacity': opacity,
                             'border': color, 'borderOpacity': .05}
    return {'schema': 1, 'revision': 0,
            'themes': {theme: {'regions': deepcopy(regions)} for theme in ('light', 'dark')}}


def normalize(value):
    if not isinstance(value, dict) or set(value) - {'schema', 'revision', 'themes'}:
        raise ValueError('配色只接受版本、主题与区域颜色。')
    if type(value.get('schema')) is not int or value['schema'] != 1:
        raise ValueError('配色格式版本不支持。')
    integer(value.get('revision'), 0, 2147483647, '配色修订号')
    themes = value.get('themes')
    if not isinstance(themes, dict) or set(themes) - {'light', 'dark'}:
        raise ValueError('配色主题须为浅色或深色。')
    result = defaults()
    result['revision'] = value['revision']
    for theme, config in themes.items():
        if not isinstance(config, dict) or set(config) - {'regions'} or not isinstance(config.get('regions'), dict):
            raise ValueError('主题须包含区域配色。')
        if len(config['regions']) > 100:
            raise ValueError('配色区域过多。')
        for identity, patch in config['regions'].items():
            # A newer/older client may know different regions. Only the server
            # registry can contribute CSS; missing and unknown regions default.
            if identity not in result['themes'][theme]['regions']:
                continue
            if not isinstance(patch, dict) or set(patch) - FIELDS:
                raise ValueError('区域配色只接受背景、边框颜色与透明度。')
            for field, raw in patch.items():
                if field in {'background', 'border'}:
                    if not isinstance(raw, str) or re.fullmatch(r'#[0-9a-fA-F]{6}', raw) is None:
                        raise ValueError('颜色须为 #RRGGBB。')
                    result['themes'][theme]['regions'][identity][field] = raw.upper()
                else:
                    if type(raw) not in (int, float) or not math.isfinite(raw) or not 0 <= raw <= 1:
                        raise ValueError('透明度须为0至100%。')
                    result['themes'][theme]['regions'][identity][field] = raw
    return result


class Appearance:
    def __init__(self, service):
        self.s = service

    def current(self, db):
        rows = self.s.collaboration.all(db, 'ui_appearance')
        row = next((r for r in rows if r['id'] == 'ui-appearance'), None)
        return normalize({k: row[k] for k in ('schema', 'revision', 'themes')}) if row else defaults()

    def catalog(self, db=None):
        if db is None:
            with self.s.store.read() as connection:
                return self.catalog(connection)
        value = self.current(db)
        return {'appearance': value, 'defaults': defaults(),
                'regions': [{'id': identity, 'label': label} for identity, label in REGIONS],
                'native_scope': 'EXISTING_POINTER_TRANSPARENT_OVERLAY', 'no_dispatch': True}

    def save(self, db, payload):
        value = normalize(payload)
        occupied = db.execute('SELECT kind FROM collaboration WHERE id=?', ('ui-appearance',)).fetchone()
        if occupied and occupied['kind'] != 'ui_appearance':
            raise ValueError('配色记录身份已被另一类记录使用，未覆盖。')
        old = self.current(db)
        if value['revision'] != old['revision']:
            raise ValueError('配色在另一窗口已更新，请重新读取后再保存；本窗口修改仍保留。')
        if old['revision'] >= 2147483647:
            raise ValueError('配色修订号已达上限。')
        value['revision'] += 1
        self.s.collaboration.put(db, 'ui_appearance', {'id': 'ui-appearance', **value, 'updated': time.time()})
        return {'appearance': value, 'no_dispatch': True}
