"""Read-only presentation of existing Workdesk records. No worker or credentials."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import sys
import time

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'workdesk-ui' / 'archive_sources'))
from harness_source import HarnessReader, TERMINAL, protocol_label, stamp
from observation_source import CompositeTaskSource
from task_records import observation_pages

READ_ERRORS = (OSError, ValueError, TypeError, KeyError, sqlite3.Error, LookupError)
PROVIDERS = {
    'codex': {'id': 'codex', 'name': 'Codex', 'mark': 'C', 'auth': '订阅授权',
              'engine': 'Codex app-server', 'model': 'gpt-6-luna',
              'summary': '读、改、测与停止、恢复已有真实样板。',
              'reservation': '仅在限定工具范围内验证；订阅剩余额度未知。'},
    'deepseek': {'id': 'deepseek', 'name': 'DeepSeek', 'mark': 'D', 'auth': '官方 API',
                 'engine': 'DeepSeek Harness', 'model': 'deepseek-flash',
                 'summary': '自主修补与 8 项检查通过，正常结束。',
                 'reservation': '当前 SDK 不支持恢复和单会话中断；已验证专属运行时停止。'},
    'qwen': {'id': 'qwen', 'name': 'Qwen', 'mark': 'Q', 'auth': '官方 API',
             'engine': 'Qwen Code', 'model': 'qwen3.8-flash',
             'summary': '修补与 8 项检查完成，独立收尾复验通过。',
             'reservation': '原工程回合收尾失败仍保留；完整修复版与专门真实停止尚未复验。'},
}


def identity(path, item):
    return sha256((str(path.absolute()) + '\0' + item).encode('utf-8')).hexdigest()[:32]


def event_summary(event):
    kind, data = event['kind'], event['data']
    names = {'tool_result': '工具返回', 'tool_call': '调用工具', 'tool_call_started': '调用工具', 'tool_start': '开始工具',
             'agent_message_complete': '模型回复', 'authorization_reserved': '登记授权',
             'turn_completed': '回合结束', 'stop_requested': '请求停止',
             'workdesk_ui_stop_requested': '工作台登记停止请求', 'runtime_started': '启动运行环境',
             'qwen_closeout_smoke': '独立收尾复验', 'sdk_event': '工作端事件', 'gate_rejected': '请求被门禁拦截'}
    label = names.get(kind, kind.replace('_', ' '))
    tool = data.get('name') or data.get('tool') or data.get('method')
    if isinstance(tool, str): label += ' · ' + tool[:100]
    return {'kind': kind, 'label': label, 'time': stamp(event['at']), 'data': data}


class Workspace:
    def __init__(self, base=BASE):
        self.base = Path(base).absolute()
        self.reader = HarnessReader(self.base / 'build' / 'stage-c')
        self.history = CompositeTaskSource(self.base / 'build' / 'collaboration-a',
                                          self.base / 'build' / 'collaboration-b')

    def catalog(self):
        entries, errors, sources = [], [], []
        try: paths = self.reader.sources()
        except READ_ERRORS as exc: paths = []; errors.append({'source': '后台任务', 'error': str(exc)})
        for path in paths:
            try:
                snap = self.reader.load(path)
                source = str(path.relative_to(self.base)).replace('\\', '/')
                sources.append({'path': source, 'title': snap.spec['title'], 'closed': snap.closed})
                for row in snap.attempts:
                    item = self.present(snap, row)
                    entries.append(item)
            except READ_ERRORS as exc:
                errors.append({'source': str(path.relative_to(self.base)), 'error': str(exc)})
        entries.sort(key=lambda a: a['started'], reverse=True)
        history, history_errors = self.history_catalog()
        return {'name': 'Workdesk', 'product': 'Workdesk · 工作台', 'revision': '界面 03',
                'records': entries, 'sources': sources, 'errors': errors + history_errors,
                'history': history, 'providers': list(PROVIDERS.values()),
                'loaded_at': stamp(time.time()), 'live_state': '未知', 'dispatch_available': False}

    def present(self, snap, row):
        provider = row.get('provider', 'codex')
        config = row.get('data', snap.spec)
        closeout = bool(snap.spec.get('closeout_only'))
        mode = config.get('mode', '')
        title = snap.spec['title']
        if provider != 'codex':
            title = (PROVIDERS[provider]['name'] + ' · ' +
                     ('正常收尾复验' if closeout else '专属运行时停止验证' if mode == 'stop' else '合成函数修补与检查'))
        state = row['state']
        tone = ('success' if state == 'COMPLETED' else 'danger' if state.startswith('FAILED') else
                'neutral' if state in {'INTERRUPTED', 'STOPPED_RUNTIME'} else 'warning')
        return {'id': identity(snap.path, row['id']), 'attempt_id': row['id'],
                'title': title, 'batch_title': snap.spec['title'], 'provider': provider,
                'model': config.get('model', '未知'), 'state': state, 'state_label': protocol_label(row),
                'display_status': {'FAILED_LIMIT_RECONCILED': '轮数限制失败 · 已对账',
                                   'STOPPED_RUNTIME': '专属运行时已停止'}.get(state, protocol_label(row)),
                'tone': tone, 'started': row.get('started', 0), 'time': stamp(row.get('started')),
                'source': str(snap.path.relative_to(self.base)).replace('\\', '/'),
                'closed': snap.closed, 'closeout_only': closeout,
                'stop_requested': bool(row['stop_requested']),
                'can_request_stop': bool(not snap.closed and snap.spec.get('stop_supported', True) and state not in TERMINAL
                                         and not row['stop_requested']),
                'effort': snap.spec.get('effort', '非思考'),
                'service_tier': snap.spec.get('service_tier', '官方 API')}

    def locate(self, key):
        # A caller supplies an opaque ID, never a filesystem path.
        for path in self.reader.sources():
            try: snap = self.reader.load(path)
            except READ_ERRORS: continue
            for row in snap.attempts:
                if identity(path, row['id']) == key: return snap, row
        raise LookupError('原记录已不可读取，请刷新核对。')

    def detail(self, key):
        snap, row = self.locate(key)
        item = self.present(snap, row)
        config = row.get('data', snap.spec)
        pages = snap.pages(row['id'])
        result = getattr(snap, 'results', {}).get(row['id'], {})
        candidate = row.get('candidate', '')
        protocol = result.get('protocol_result')
        if not candidate and isinstance(protocol, dict):
            candidate = protocol.get('final_response') or protocol.get('result') or protocol.get('text') or ''
            if not isinstance(candidate, str): candidate = json.dumps(candidate, ensure_ascii=False, indent=2)
        events = [e for e in snap.events if e['attempt_id'] == row['id']]
        prompt = config.get('prompt') or config.get('prompts', {})
        if not isinstance(prompt, str): prompt = json.dumps(prompt, ensure_ascii=False, indent=2)
        # Facts, not a synthetic assistant answer when no final message exists.
        checks = [e['data']['result'] for e in events if e['kind'] == 'tool_result'
                  and e['data'].get('name') == 'check_cases' and isinstance(e['data'].get('result'), dict)]
        item.update({'pages': pages, 'events': [event_summary(e) for e in events], 'checks': checks,
                     'prompt': prompt, 'answer': candidate,
                     'thread_id': row.get('thread_id') or '未知', 'owner': row['owner'],
                     'request_id': row['request_id'], 'spec_hash': snap.spec_hash,
                     'loaded_at': stamp(snap.loaded_at), 'live_state': '未知',
                     'harness': PROVIDERS[item['provider']]['engine'],
                     'review': '本聊天自审', 'independent_review': '未进行', 'human_acceptance': '未验收'})
        return item

    def stop(self, payload):
        snap, row = self.locate(payload.get('id'))
        if payload.get('spec_hash') != snap.spec_hash or payload.get('owner') != row['owner']:
            raise ValueError('任务版本或归属已变化，未登记停止请求。')
        if not self.present(snap, row)['can_request_stop']:
            return {'registered': False, 'reason': '本任务包已封闭或当前状态不支持停止；未改写历史记录。'}
        return self.reader.request_stop(snap.path, snap.spec_hash, row['id'], row['owner'])

    def history_catalog(self):
        entries, errors = [], []
        try: paths = self.history.sources()
        except READ_ERRORS as exc: return [], [{'source': '历史交接', 'error': str(exc)}]
        for path in paths:
            try:
                snap = self.history.load(path)
                for task_id in snap.task_ids:
                    entries.append({'id': identity(path, task_id), 'title': snap.task_name(task_id),
                                    'batch': snap.batch_id, 'source': str(path.relative_to(self.base)),
                                    'label': snap.source_label, 'candidates': sum(
                                        s.candidate is not None for s in snap.steps if s.task_id == task_id)})
            except READ_ERRORS as exc: errors.append({'source': str(path.relative_to(self.base)), 'error': str(exc)})
        return entries, errors

    def history_detail(self, key):
        for path in self.history.sources():
            try: snap = self.history.load(path)
            except READ_ERRORS: continue
            for task_id in snap.task_ids:
                if identity(path, task_id) == key:
                    pages = observation_pages(snap, 'task', task_id)
                    pages.update(observation_pages(snap, 'results', task_id))
                    for step in snap.steps:
                        if step.task_id != task_id: continue
                        for name, text in observation_pages(snap, 'materials', task_id, step.slot_id).items():
                            pages[f'{step.label} / {name}'] = text
                    return {'id': key, 'title': snap.task_name(task_id), 'label': snap.source_label,
                            'pages': pages, 'source': str(path.relative_to(self.base))}
        raise LookupError('历史来源已不可读取，请刷新核对。')
