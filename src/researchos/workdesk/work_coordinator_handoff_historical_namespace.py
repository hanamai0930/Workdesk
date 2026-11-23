"""Exact carrier accounting for one historical catalog invocation.

Registration is performed only by the semantic reconstruction that owns a
carrier. References never authorize an entire SQL row, event or command.
"""
from copy import deepcopy
import itertools
import re

from .store import digest
from .work_coordinator_handoff_adjustment_records import finite
from .work_coordinator_handoff_historical_assets import _json, _uuid


class CanonicalLedger:
    def __init__(self, sources):
        self.sources = sources
        self.db = sources.db
        self.rows = {}
        self.events = {}
        self.commands = {}
        self.versions = {}
        self.business_records = {}
        self.execution_rows = {}
        self.observations = {}
        self.identities = set()
        self.items = set()
        self.tasks = set()
        self.attempts = set()

    def identify(self, *values):
        for value in values:
            if not isinstance(value, str) or not value:
                raise ValueError('历史归属标记须为已证明的准确字符串。')
            self.identities.add(value)

    @staticmethod
    def _register(target, key, value, owner):
        if not isinstance(owner, str) or not owner:
            raise ValueError('历史载体缺少唯一重建位置。')
        old = target.get(key)
        if old is not None and old != (value, owner):
            raise ValueError('历史载体被不同重建位置重复消费。')
        target[key] = (deepcopy(value), owner)

    def row(self, kind, value, owner):
        if self.sources.get(self.db, kind, value['id']) != value:
            raise ValueError('历史载体不等于实际完整数据库行。')
        self._register(self.rows, (kind, value['id']), value, owner)
        self.identify(value['id'])
        # Digests and revisions are bare references in existing protocols.
        for name in ('version', 'revision', 'generation'):
            if isinstance(value.get(name), str):
                self.identify(value[name])
        return deepcopy(value)

    def event(self, kind, body, owner, *, task=None, attempt=None, before=None):
        event = self.sources._event(self.db, kind, body, before, task, attempt)
        return self.event_at(event['seq'], kind, body, owner, task=task, attempt=attempt)

    def event_at(self, seq, kind, body, owner, *, task=None, attempt=None):
        """Consume a proven exact occurrence, including repeated captures.

        A capture can legitimately repeat an identical payload. Its original
        seq and full command identify that occurrence; payload uniqueness is
        still required by event() for all singleton protocol events.
        """
        raw = self.db.execute('SELECT * FROM events WHERE seq=?', (seq,)).fetchone()
        if (type(seq) is not int or raw is None or raw['kind'] != kind or _json(raw['payload']) != body or
                raw['task_id'] != task or raw['attempt_id'] != attempt):
            raise ValueError('历史载体须消费准确事件序号和原完整归属。')
        finite(raw['at'])
        event = {'seq': seq, 'kind': kind, 'payload_digest': digest(body), 'created': raw['at']}
        self._register(self.events, event['seq'], (kind, body, task, attempt), owner)
        if task is not None:
            self.tasks.add(task)
            self.identify(task)
        if attempt is not None:
            self.attempts.add(attempt)
            self.identify(attempt)
        return event

    def execution(self, proof, owner):
        # proof is the replay result from the actual HistoricalSources instance.
        for (kind, seq), (body, task, attempt) in proof['events'].items():
            event = self.event(kind, body, owner, task=task, attempt=attempt)
            if event['seq'] != seq:
                raise ValueError('历史退出重放事件序号不符。')
        for table, rows in proof['ledgers'].items():
            for row in rows:
                self._register(self.execution_rows, (table, row['id']), row, owner)

    def command(self, identity, kind, request, response, owner, *, not_before):
        raw = self.db.execute('SELECT * FROM commands WHERE id=?', (_uuid(identity),)).fetchone()
        if (raw is None or raw['kind'] != kind or raw['payload_hash'] != digest(request) or
                _json(raw['receipt']) != response):
            raise ValueError('历史命令缺少原kind、完整请求摘要或完整回执。')
        finite(raw['created'])
        if raw['created'] < not_before:
            raise ValueError('历史命令早于其完整实际事件。')
        value = dict(raw)
        self._register(self.commands, identity, value, owner)
        self.identify(identity)
        return value

    def business_version(self, item, revision, expected, owner):
        raw = self.db.execute('SELECT * FROM work_item_versions WHERE item_id=? AND revision=?',
            (item, revision)).fetchone()
        if (raw is None or expected['id'] != item or expected['revision'] != revision or
                _json(raw['snapshot']) != expected):
            raise ValueError('历史业务修订须与完整不可变snapshot相等。')
        self._register(self.versions, (item, revision), expected, owner)
        self.items.add(item)
        self.identify(item)
        return deepcopy(expected)

    def readiness_observation(self, value, owner):
        raw = self.db.execute('SELECT * FROM work_readiness_observations WHERE id=?', (value['id'],)).fetchone()
        if raw is None or dict(raw) != {'id': value['id'], 'item_id': value['item_id'],
                'command_id': value['command_id'], 'data': raw['data']} or _json(raw['data']) != value:
            raise ValueError('原责任资源观察须等于实际数据库完整行及键。')
        self._register(self.observations, value['id'], dict(raw), owner)
        self.identify(value['id'], value['version'], value['command_id'], value['item_id'])

    def business_record(self, expected, owner):
        key = (expected['item_id'], expected['kind'], expected['record_id'])
        raw = self.db.execute('SELECT * FROM work_item_records WHERE item_id=? AND kind=? AND record_id=?',
            key).fetchone()
        if raw is None or dict(raw) != expected:
            raise ValueError('历史业务关联须保持完整原记录行及创建事实。')
        self._register(self.business_records, key, expected, owner)
        self.items.add(expected['item_id'])
        self.identify(expected['item_id'], expected['record_id'])

    def capture_command(self, run, event, request, owner):
        """Use the owner's actual pre-capture business/driver/pointer boundary."""
        required = {'id', 'revision', 'current_driver', 'plan', 'run_id', 'result_version', 'artifact'}
        if (set(request) != required or request['id'] != run['item_id'] or request['run_id'] != run['id'] or
                request['plan'] != {'id': run['plan_id'], 'version': run['plan_version']}):
            raise ValueError('历史捕获请求未绑定原准确七字段。')
        artifact = self.sources.get(self.db, 'work_unit_artifact', event['artifact']['id'])
        if request['result_version'] != artifact['snapshot']['result_version']:
            raise ValueError('历史捕获原result版本不符。')
        response = {'artifact': artifact, 'no_dispatch': True, 'project_files_written': False,
            'independent_review': 'NOT_PERFORMED', 'human_acceptance': 'NOT_RECORDED'}
        # Coordinator roots never carry the older file_handoff_completion key.
        next_capture = self.db.execute("SELECT at FROM events WHERE kind='cooperative_files_checked' "
            'AND task_id=? AND attempt_id=? AND seq>? ORDER BY seq LIMIT 1',
            (run['task_id'], run['attempt_id'], event['seq'])).fetchone()
        requests = [request]
        if request['artifact'] is None:
            requests.append({k: v for k, v in request.items() if k != 'artifact'})
        matches = [(raw, candidate) for raw in self.db.execute("SELECT * FROM commands WHERE kind='work-item.unit-check'")
            for candidate in requests if raw['payload_hash'] == digest(candidate) and _json(raw['receipt']) == response and
                raw['created'] >= event['created'] and (next_capture is None or raw['created'] <= next_capture['at'])]
        if len(matches) != 1:
            raise ValueError('每次历史捕获须有唯一已提交原完整回执。')
        raw, exact_request = matches[0]
        earlier = [row for row in self.db.execute("SELECT payload FROM events WHERE kind='cooperative_files_checked' "
            'AND task_id=? AND attempt_id=? AND seq<?', (run['task_id'], run['attempt_id'], event['seq']))
            if _json(row['payload']).get('artifact') == event['artifact']]
        if not earlier and raw['id'] != artifact['command_id']:
            raise ValueError('历史产物原command_id须属于其首次实际捕获。')
        command = self.command(raw['id'], 'work-item.unit-check', exact_request, response, owner,
            not_before=event['created'])
        self.event_at(event['seq'], 'cooperative_files_checked',
            {k: event[k] for k in ('artifact', 'manifest', 'check_status')},
            owner, task=run['task_id'], attempt=run['attempt_id'])
        return command

    def management_update(self, previous, current, *, after_seq, owner):
        """Prove a later canonical WorkItems.update without driver admission.

        The existing ledger stores request hashes, not raw update requests.
        Enumerate the bounded canonical subsets (including unchanged fields)
        and require a hash match, rather than inventing a changes object.
        Non-canonical raw-text requests cannot be recovered from their hash.
        """
        from .work_items import TEXT_FIELDS, STATES
        allowed = {*TEXT_FIELDS, 'state', 'project_id'}
        if (current['id'] != previous['id'] or current['revision'] != previous['revision'] + 1 or
                current['record_refs'] != previous['record_refs'] or current['created'] != previous['created'] or
                set(current) != set(previous) or current['state'] not in STATES or
                any(current[k] != previous[k] for k in current if k not in allowed | {'revision', 'updated'})):
            raise ValueError('后续管理修订不等于原工作任务更新语义。')
        finite(current['updated'])
        if current['updated'] < previous['updated']:
            raise ValueError('后续管理修订时间早于旧版本。')
        for key, limit in TEXT_FIELDS.items():
            value = current[key]
            if (not isinstance(value, str) or value != value.strip() or len(value) > limit or
                    key in {'title', 'goal'} and not value):
                raise ValueError('后续管理修订文字不是合法规范值。')
        if current['project_id'] is not None and self.db.execute('SELECT 1 FROM projects WHERE id=?',
                (current['project_id'],)).fetchone() is None:
            raise ValueError('后续管理修订缺少实际项目。')
        changed = {k for k in allowed if current[k] != previous[k]}
        optional = sorted(allowed - changed)
        response = {'work_item': {k: v for k, v in current.items() if k != 'record_refs'},
            'no_dispatch': True, 'execution_unchanged': True}
        commands = [raw for raw in self.db.execute("SELECT * FROM commands WHERE kind='work-item.update'")
            if _json(raw['receipt']) == response]
        event_body = {'work_item_id': current['id'], 'revision': current['revision'], 'no_dispatch': True}
        event = self.sources._event(self.db, 'work_item_updated', event_body)
        if event['seq'] <= after_seq or current['updated'] > event['created']:
            raise ValueError('后续管理更新须在原已知退出之后。')
        matches = []
        for size in range(len(optional) + 1):
            for subset in itertools.combinations(optional, size):
                keys = changed | set(subset)
                if not keys:
                    continue
                request = {'id': previous['id'], 'revision': previous['revision'],
                    'changes': {k: current[k] for k in keys}}
                for raw in commands:
                    if raw['payload_hash'] == digest(request):
                        matches.append((raw, request))
        if len(matches) != 1:
            raise ValueError('后续管理更新缺少唯一可重建的原请求和完整回执。')
        raw, request = matches[0]
        self.command(raw['id'], 'work-item.update', request, response, owner, not_before=event['created'])
        self.event('work_item_updated', event_body, owner)
        self.business_version(current['id'], current['revision'], current, 'business:' + str(current['revision']))
        return event

    def _mentions(self, value):
        pending = [value]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                pending.extend(node.keys())
                pending.extend(node.values())
            elif isinstance(node, list):
                pending.extend(node)
            elif isinstance(node, str) and node in self.identities:
                return True
        return False

    def _restored_management(self):
        """Account for the staged restore's own no-dispatch management facts."""
        for raw in self.db.execute("SELECT * FROM events WHERE kind='state_restored_without_dispatch' ORDER BY seq"):
            body = _json(raw['payload'])
            if (set(body) != {'restore_id', 'backup_id', 'grants_revoked'} or body['grants_revoked'] is not True):
                raise ValueError('暂存恢复事件须保持完整撤权且无派发的管理格式。')
            identity = _uuid(body['restore_id']); _uuid(body['backup_id'])
            row = self.sources.get(self.db, 'restored_history', 'restored-history:' + identity)
            # The original writer records every attempt present at this event.
            # Newer attempts must have their own later reservation events.
            attempts = [r['id'] for r in self.db.execute('SELECT id FROM attempts') if self.db.execute(
                "SELECT 1 FROM events WHERE attempt_id=? AND kind='execution_reserved' AND seq<?",
                (r['id'], raw['seq'])).fetchone() is not None]
            expected = {'id': 'restored-history:' + identity, 'attempt_ids': attempts,
                'qwen_native_history': 'NOT_IN_BACKUP', 'credential_reference': 'existing same-machine profile'}
            if row != expected:
                raise ValueError('暂存恢复历史须保持当时完整attempt集合及独立管理声明。')
            backup = self.sources.get(self.db, 'backup', body['backup_id'])
            if (set(backup) != {'id', 'root', 'manifest_hash', 'created', 'state'} or backup['state'] != 'READY' or
                    not isinstance(backup['root'], str) or not isinstance(backup['manifest_hash'], str) or
                    re.fullmatch('[a-f0-9]{64}', backup['manifest_hash']) is None):
                raise ValueError('暂存恢复管理记录缺少准确已登记本机包。')
            finite(backup['created'])
            if backup['created'] > raw['at']:
                raise ValueError('暂存恢复不能先于准确原包发布。')
            for task in self.tasks:
                value = self.db.execute('SELECT state,grant FROM tasks WHERE id=?', (task,)).fetchone()
                if value['state'] != 'PAUSED' or value['grant'] is not None:
                    raise ValueError('暂存恢复的原合作任务须暂停且撤销grant。')
            owner = 'restore:' + identity
            self.identify(identity, body['backup_id'])
            self.row('restored_history', row, owner)
            self.row('backup', backup, owner)
            self.event_at(raw['seq'], raw['kind'], body, owner)

    def close(self, *, protocol_prefixes=()):
        """Call after all owners have registered their exact semantic carriers."""
        self._restored_management()
        def marked(value):
            if self._mentions(value):
                return True
            pending = [value]
            while pending:
                part = pending.pop()
                if isinstance(part, dict):
                    protocol = part.get('protocol')
                    if isinstance(protocol, str) and protocol.startswith(protocol_prefixes):
                        return True
                    pending.extend(part.values())
                elif isinstance(part, list):
                    pending.extend(part)
            return False
        for raw in self.db.execute('SELECT kind,id,data FROM collaboration'):
            value = _json(raw['data'])
            key = (raw['kind'], raw['id'])
            expected = self.rows.get(key)
            if (expected is not None or raw['id'] in self.identities or marked(value)) and (
                    expected is None or expected[0] != value):
                raise ValueError('历史资产存在未重建、改名、嵌套或错误key载体。')
        for raw in self.db.execute('SELECT * FROM events ORDER BY seq'):
            value = _json(raw['payload'])
            expected = self.events.get(raw['seq'])
            relevant = (expected is not None or raw['task_id'] in self.tasks or
                raw['attempt_id'] in self.attempts or marked(value))
            if relevant and (expected is None or expected[0] !=
                    (raw['kind'], value, raw['task_id'], raw['attempt_id'])):
                raise ValueError('历史资产存在未重建、迟到、嵌套或错归属事件。')
        for raw in self.db.execute('SELECT * FROM commands'):
            value = _json(raw['receipt'])
            expected = self.commands.get(raw['id'])
            if (expected is not None or raw['id'] in self.identities or marked(value)) and (
                    expected is None or expected[0] != dict(raw)):
                raise ValueError('历史资产存在别名、未提交或未重建的完整命令。')
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_readiness_observations'").fetchone():
            for raw in self.db.execute('SELECT * FROM work_readiness_observations'):
                value = _json(raw['data']); expected = self.observations.get(raw['id'])
                if (expected is not None or raw['item_id'] in self.items or raw['id'] in self.identities or
                        marked(value)) and (expected is None or expected[0] != dict(raw)):
                    raise ValueError('原责任资源观察包含错key、改名、嵌套或未重建归属。')
        for raw in self.db.execute('SELECT * FROM work_item_versions'):
            expected = self.versions.get((raw['item_id'], raw['revision']))
            if raw['item_id'] in self.items and (expected is None or expected[0] != _json(raw['snapshot'])):
                raise ValueError('历史资产完整业务版本集合不闭合。')
        for raw in self.db.execute('SELECT * FROM work_item_records'):
            expected = self.business_records.get((raw['item_id'], raw['kind'], raw['record_id']))
            if (raw['item_id'] in self.items or raw['record_id'] in self.tasks) and (
                    expected is None or expected[0] != dict(raw)):
                raise ValueError('历史资产完整业务关联记录集合不闭合。')
        for item in self.items:
            versions = [value[0] for (identity, _), value in self.versions.items() if identity == item]
            current = self.db.execute('SELECT * FROM work_items WHERE id=?', (item,)).fetchone()
            if not versions or current is None:
                raise ValueError('历史资产缺少准确当前业务尾。')
            tail = max(versions, key=lambda value: value['revision'])
            if dict(current) != {k: v for k, v in tail.items() if k != 'record_refs'}:
                raise ValueError('当前业务任务不等于最后独立重建的完整版本。')
        for table in ('requests', 'native_requests', 'tool_calls', 'approvals'):
            for raw in self.db.execute('SELECT * FROM ' + table):
                value = dict(raw)
                for name in ('params', 'response', 'usage', 'result'):
                    if value.get(name) is not None:
                        value[name] = _json(value[name])
                expected = self.execution_rows.get((table, raw['id']))
                relevant = (expected is not None or raw['id'] in self.identities or
                    value.get('task_id') in self.tasks or value.get('attempt_id') in self.attempts or marked(value))
                if relevant and (expected is None or expected[0] != dict(raw)):
                    raise ValueError('历史资产请求、原生交互或工具完整账本不闭合。')
