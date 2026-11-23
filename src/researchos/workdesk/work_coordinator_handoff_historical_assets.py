"""SQL and frozen-byte sources for historical asset reconstruction.

This module has no runtime owner, current-resource lookup or project-file read.
Its frames are private historical values; the enclosing catalog must reconstruct
the original input/spec and close the complete row/event/command namespace.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from uuid import UUID

from . import workspace
from .store import digest
from .work_coordinator_handoff_adjustment_records import finite
from .work_coordinator_handoff_context import TERMINAL
from .work_coordinator_handoff_ledger import WorkCoordinatorHandoffLedger


def _json(value):
    def invalid(value):
        raise ValueError('历史资产不接受非有限JSON。')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('历史资产JSON包含重复键。')
            result[key] = value
        return result
    return json.loads(value, parse_constant=invalid, object_pairs_hook=pairs)


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('历史资产命令及任务身份须为原准确UUID。')
    return value


class Rows:
    """Per-cursor keyed rows, preserving the caller's connection row_factory."""
    def __init__(self, db):
        self.connection = db

    def execute(self, query, args=()):
        cursor = self.connection.cursor()
        cursor.row_factory = sqlite3.Row
        return cursor.execute(query, args)

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    @property
    def total_changes(self):
        return self.connection.total_changes


@contextmanager
def snapshot(db):
    """Own only a new read transaction; never end a caller's transaction."""
    raw = db.connection if isinstance(db, Rows) else db
    owned = not raw.in_transaction
    changes, factory = raw.total_changes, raw.row_factory
    if owned:
        raw.execute('BEGIN')
    try:
        rows = Rows(raw)
        # Establish the actual SQLite read snapshot before any external reads.
        rows.execute('SELECT COUNT(*) FROM collaboration').fetchone()
        yield rows
        if raw.total_changes != changes or raw.row_factory is not factory or not raw.in_transaction:
            raise ValueError('历史资产读取改变了数据库或调用方读取状态。')
    finally:
        if owned and raw.in_transaction:
            raw.rollback()


class FrozenAssets:
    """Explicit data/state root, exact relative names and conflict-free manifest."""
    def __init__(self, root):
        if root is None:
            raise ValueError('已运行历史资产需要明确的冻结资产根。')
        self.root = Path(root).absolute()
        workspace.plain(self.root, self.root)
        if not workspace.io_path(self.root).is_dir():
            raise ValueError('历史冻结资产根须为实际目录。')
        self.required = {}

    @staticmethod
    def _name(name):
        from .persistence import safe_name
        name = safe_name(name)
        parts = name.split('/')
        valid = (len(parts) >= 4 and parts[0] == 'tasks' and parts[2] == 'input')
        if valid:
            _uuid(parts[1])
        elif len(parts) >= 4 and parts[0] == 'results' and parts[2] == 'files':
            valid = bool(re.fullmatch(r'(?:unit-|file-rework-base-)[a-f0-9]{64}', parts[1]))
        if not valid:
            raise ValueError('历史资产只读取冻结input、unit或baseline文件。')
        return name

    def read(self, name, expected):
        name = self._name(name)
        if (not isinstance(expected, dict) or set(expected) != {'sha256', 'bytes'} or
                not isinstance(expected['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', expected['sha256']) or
                type(expected['bytes']) is not int or not 0 <= expected['bytes'] <= workspace.MAX_BINARY):
            raise ValueError('历史资产文件须包含准确摘要和有界字节数。')
        if name in self.required and self.required[name] != expected:
            raise ValueError('同一冻结资产路径出现不同版本。')
        # Keep the existing per-material 240-character bound on the material
        # name, not on the additional tasks/results storage prefix.
        parts = name.split('/')
        folder = self.root.joinpath(*parts[:3])
        workspace.plain(folder, self.root)
        record = workspace.read_blob(folder, '/'.join(parts[3:]))
        if {k: record[k] for k in ('sha256', 'bytes')} != expected:
            raise ValueError('历史资产冻结文件字节不符。')
        self.required[name] = deepcopy(expected)
        return record['raw']

    def manifest(self, prefix, manifest):
        if not isinstance(manifest, dict) or len(manifest) > workspace.MAX_FILES:
            raise ValueError('历史资产材料清单无效或超过上限。')
        if len({name.casefold() for name in manifest}) != len(manifest):
            raise ValueError('历史资产材料路径重复。')
        total = 0
        blobs = {}
        for name, expected in manifest.items():
            if workspace.relative(name) != name:
                raise ValueError('历史资产材料路径不是准确规范路径。')
            blobs[name] = self.read(prefix + name, expected)
            total += len(blobs[name])
        if total > workspace.MAX_ATTACHMENT_TOTAL:
            raise ValueError('历史资产材料合计超过32MiB。')
        return blobs

    def baseline(self, value):
        if (not isinstance(value, dict) or set(value) != {'root', 'paths', 'manifest', 'missing', 'scope', 'version'} or
                value['scope'] != 'EXACT_DECLARED_PATHS_ONLY; NOT_DIRECTORY_BACKUP_OR_OS_ISOLATION' or
                value['version'] != digest({k: v for k, v in value.items() if k != 'version'}) or
                not isinstance(value['paths'], list) or not isinstance(value['missing'], list) or
                value['paths'] != sorted(set(value['paths'])) or
                value['missing'] != sorted(set(value['missing'])) or
                set(value['manifest']) | set(value['missing']) != set(value['paths']) or
                set(value['manifest']) & set(value['missing'])):
            raise ValueError('历史冻结baseline摘要及路径集合不闭合。')
        for name in value['paths']:
            if workspace.relative(name) != name:
                raise ValueError('历史冻结baseline路径不规范。')
        self.manifest('results/file-rework-base-' + value['version'] + '/files/', value['manifest'])

    def recheck(self):
        # Re-read real bytes at the end of the same database observation.
        for name, expected in list(self.required.items()):
            self.read(name, expected)


class HistoricalSources:
    """Original rows and actual execution replay, with no availability facade."""
    def __init__(self, db, assets):
        if not db.in_transaction:
            raise ValueError('历史资产来源须在同一次明确SQLite读取快照内。')
        self.db = db if isinstance(db, Rows) else Rows(db)
        self.assets = assets
        self._changes = self.db.total_changes
        self._exits = {}
        self._events = {}
        # Only actual SQL data methods needed by execution_events are exposed.
        self._data = SimpleNamespace(store=self, collaboration=self)

    def get(self, db, kind, identity):
        row = db.execute('SELECT kind,id,data FROM collaboration WHERE id=?', (identity,)).fetchone()
        if row is None or row['kind'] != kind:
            raise ValueError('历史资产缺少准确类型及数据库主键。')
        value = _json(row['data'])
        if not isinstance(value, dict) or value.get('id') != row['id']:
            raise ValueError('历史资产数据库主键与内容身份不符。')
        return value

    def all(self, db, kind):
        return [self.get(db, kind, r['id']) for r in
            db.execute('SELECT id FROM collaboration WHERE kind=? ORDER BY id', (kind,))]

    def task(self, identity, db):
        row = db.execute('SELECT * FROM tasks WHERE id=?', (_uuid(identity),)).fetchone()
        if row is None:
            raise ValueError('历史资产缺少原task。')
        value = dict(row)
        value['spec'] = _json(value['spec'])
        value['grant'] = _json(value['grant']) if value['grant'] else None
        if value['spec_hash'] != digest(value['spec']):
            raise ValueError('历史task完整spec摘要不符。')
        return value

    def attempt(self, identity, db):
        row = db.execute('SELECT * FROM attempts WHERE id=?', (_uuid(identity),)).fetchone()
        if row is None:
            raise ValueError('历史资产缺少原attempt。')
        value = dict(row)
        for key in ('baseline', 'result', 'reconciliation'):
            value[key] = _json(value[key]) if value[key] else None
        return value

    def _event(self, db, kind, payload, cutoff=None, task=None, attempt=None):
        # Generic execution payloads (creation/state/attachment observations)
        # legitimately repeat across tasks. Identify their exact occurrence by
        # its original task/attempt as well as the full body. The final ledger
        # still rejects every unconsumed event for any owned task or attempt.
        self.unchanged(db)
        if kind not in self._events:
            self._events[kind] = [(dict(row), _json(row['payload'])) for row in
                db.execute('SELECT * FROM events WHERE kind=? ORDER BY seq', (kind,))]
        found = [row for row, body in self._events[kind]
            if body == payload and row['task_id'] == task and row['attempt_id'] == attempt]
        if (len(found) != 1 or
                cutoff is not None and found[0]['seq'] >= cutoff):
            raise ValueError('历史资产事件须唯一、准确归属并在原边界之前：' + kind)
        row = found[0]
        finite(row['at'])
        return {'seq': row['seq'], 'kind': kind, 'payload_digest': digest(payload), 'created': row['at']}

    def unchanged(self, db=None):
        actual = self.db if db is None else db
        actual = actual.connection if isinstance(actual, Rows) else actual
        if actual is not self.db.connection or not actual.in_transaction or actual.total_changes != self._changes:
            raise ValueError('历史读取复用只能属于本次准确不变SQLite快照。')

    def run(self, identity):
        db = self.db
        run = self.get(db, 'work_unit_run', identity)
        task, attempt = self.task(run['task_id'], db), self.attempt(run['attempt_id'], db)
        plan = self.get(db, 'teamwork_plan', run['plan_id'])
        units = [u for u in plan['units'] if u['key'] == run['unit_key']]
        _uuid(run['command_id']); _uuid(run['owner'])
        if (run['id'] != 'unit-run:' + attempt['id'] or len(units) != 1 or
                plan['version'] != digest({k: v for k, v in plan.items() if k != 'version'}) or
                run['plan_version'] != plan['version'] or run['unit_revision'] != digest(units[0]) or
                task['project_id'] != plan['project_id'] or run['item_id'] != plan['item_id'] or
                run['input_version'] != digest(run['input']) or run['task_spec_version'] != task['spec_hash'] or
                run['input']['plan_id'] != plan['id'] or run['input']['plan_version'] != plan['version'] or
                run['input']['unit'] != units[0] or run['input']['item_id'] != run['item_id'] or
                run['input']['item_revision'] != run['item_revision'] or
                run['input']['work_requirements'] != plan['work_requirements'] or
                any(run['cooperative'].get(k) != run[k] for k in
                    ('item_id', 'plan_id', 'plan_version', 'unit_key', 'unit_revision', 'input_version')) or
                task['spec'].get('cooperative') != run['cooperative'] or
                attempt['task_id'] != task['id'] or attempt['owner'] != run['owner'] or
                run['connection_fingerprint'] != digest(task['spec'].get('execution_connection'))):
            raise ValueError('历史run、task、attempt、方案和完整冻结输入身份不符。')
        reserved = self._event(db, 'cooperative_unit_reserved', run, None, task['id'], attempt['id'])
        return run, task, attempt, plan, units[0], reserved

    def exited(self, run):
        return self._exited(run, 'work-item.unit-send')

    def text_review_exited(self, run, edge):
        """A separate exact typed command; original exited() stays closed."""
        from .work_coordinator_handoff_text_rework_parent import _TypedEdge
        from .work_coordinator_handoff_text_rework_values import review_input, review_prompt
        from . import work_coordinator_handoff_text_rework_contracts as c
        if type(edge) is not _TypedEdge:
            raise ValueError('返工退出只接受实际已核typed边。')
        edge.require(self, edge.parent_plan)
        cap = edge.capability
        obs = cap['snapshot']['observation']
        frozen = review_input(cap)
        if (run['command_id'] != cap['command_id'] or run['input'] != frozen or
                run['item_id'] != cap['item_id'] or run['retry_of'] is not None):
            raise ValueError('返工退出须属于本边唯一真实REWORK_REVIEW保留。')
        self.verify_input(run, frozen, review_prompt(frozen), obs['driver'],
            obs['authorization'], obs['usage']['attempts'] + 1)
        return self._exited(run, c.SEND)

    def _exited(self, run, command_kind):
        db = self.db
        if not db.in_transaction or db.total_changes != self._changes:
            raise ValueError('原退出证明只能复用本次准确不变读取快照。')
        cache_key = (run['id'], command_kind)
        cached = self._exits.get(cache_key)
        if cached is not None:
            if cached[0] != run:
                raise ValueError('同一读取快照不能为不同原run复用退出证明。')
            return deepcopy(cached[1])
        actual, task, attempt, plan, unit, reserved = self.run(run['id'])
        if actual != run:
            raise ValueError('历史退出须读取实际完整run。')
        result = attempt.get('result') or {}
        if (attempt['state'] not in TERMINAL or not result or result.get('state') != attempt['state'] or
                result.get('known_tools_exited') is not True or
                type((result.get('owned_job') or {}).get('active_processes')) is not int or
                result['owned_job']['active_processes'] != 0 or attempt.get('reconciliation') or
                result.get('remote_model_state', 'UNREPORTED') not in
                    {'completed', 'failed', 'interrupted', 'NOT_SENT', 'UNREPORTED'} or
                any(result.get(k) != v for k, v in (('attempt_id', attempt['id']),
                    ('task_id', task['id']), ('owner', attempt['owner']))) or
                attempt['deadline'] != 0 or attempt['baseline'] != {'runtime_mode': 'official'} or
                db.execute('SELECT COUNT(*) FROM attempts WHERE task_id=?', (task['id'],)).fetchone()[0] != 1):
            raise ValueError('历史资产需要已核实的原独立已知退出。')
        if task['state'] not in {attempt['state'], 'PAUSED'}:
            raise ValueError('历史原task仍处于活动、未知或不支持状态。')
        finite(attempt['started']); finite(result['ended_at'])
        if result['ended_at'] < attempt['started']:
            raise ValueError('历史原退出时间早于原attempt开始。')
        # The bounded native replay owns native requests/tools exactly. SDK
        # request accounting has no verified codec in this historical branch.
        if db.execute('SELECT 1 FROM requests WHERE task_id=? OR attempt_id=? LIMIT 1',
                (task['id'], attempt['id'])).fetchone():
            raise ValueError('历史资产尚未核实此SDK请求账本。')
        if db.execute('SELECT 1 FROM approvals WHERE task_id=? OR attempt_id=? LIMIT 1',
                (task['id'], attempt['id'])).fetchone():
            raise ValueError('历史资产尚未核实此受限工具审批账本。')
        if result.get('execution') != 'codex_native':
            if (result.get('protocol') != 'LOCAL_SYNTHETIC_ENGINE_NO_MODEL' or
                    result.get('execution') is not None or result.get('remote_model_state') is not None):
                raise ValueError('历史资产只支持已核实的直接本地模拟退出或完整Codex退出。')
            for table in ('tool_calls', 'native_requests'):
                if db.execute('SELECT 1 FROM ' + table + ' WHERE attempt_id=? LIMIT 1', (attempt['id'],)).fetchone():
                    raise ValueError('本地直接退出不能携带未重建的原生或工具账本。')
            if db.execute('SELECT 1 FROM native_requests WHERE task_id=? LIMIT 1', (task['id'],)).fetchone():
                raise ValueError('本地直接退出不能携带错attempt的原生账本。')
            if any(v.get('task_id') == task['id'] or v.get('attempt_id') == attempt['id']
                    for v in self.all(db, 'native_control')):
                raise ValueError('本地直接退出不能携带原生控制。')
        dispatch = self.get(db, 'work_dispatch', 'dispatch:' + attempt['id'])
        from .execution_constraints import snapshot as constraints
        from .work_coordinator_handoff_post_round_v3_execution_events import execution_events, dispatch_session
        frame = {'run': run, 'task_spec': task['spec'], 'workspace': attempt['workspace']}
        session = dispatch_session(self._data, db, self, frame, dispatch)
        if session != run['initial_session_id']:
            raise ValueError('历史原dispatch须保留准确初始预留session。')
        expected = {'id': 'dispatch:' + attempt['id'], 'item_id': run['item_id'],
            'item_revision': run['item_revision'] + 1, 'task_id': task['id'], 'attempt_id': attempt['id'],
            'session_id': session, 'command_id': run['command_id'], 'command_kind': command_kind,
            'spec_hash': task['spec_hash'], 'provider': task['spec']['provider'],
            'credential_revision': dispatch['credential_revision'],
            'execution_connection': task['spec']['execution_connection'],
            'execution_resource': task['spec']['execution_resource'], 'cooperative': run['cooperative'],
            'constraints': constraints(task, attempt), 'responsibility': 'OFFICIAL_RUNTIME',
            'handoff_id': None, 'created': dispatch['created']}
        if dispatch != expected:
            raise ValueError('历史原dispatch完整身份和约束不符。')
        finite(dispatch['created'])
        registry = execution_events(self._data, db, self, frame, dispatch)
        exited = self._event(db, 'attempt_result_saved', result, None, task['id'], attempt['id'])
        if result['ended_at'] > exited['created']:
            raise ValueError('历史结果退出时间晚于实际保存事件。')
        for kind, body in (('cooperative_unit_reserved', run), ('work_dispatch_bound', dispatch),
                ('attempt_result_saved', result)):
            event = self._event(db, kind, body, None, task['id'], attempt['id'])
            registry[(kind, event['seq'])] = (deepcopy(body), task['id'], attempt['id'])
        ledgers = {}
        for table in ('requests', 'native_requests', 'approvals'):
            ledgers[table] = [dict(row) for row in db.execute(
                'SELECT * FROM ' + table + ' WHERE task_id=? OR attempt_id=?', (task['id'], attempt['id']))]
        ledgers['tool_calls'] = [dict(row) for row in db.execute('SELECT * FROM tool_calls WHERE attempt_id=?',
            (attempt['id'],))]
        proof = {'task': task, 'attempt': attempt, 'plan': plan, 'unit': unit, 'dispatch': dispatch,
            'reserved': reserved, 'exited': exited, 'events': registry, 'ledgers': ledgers}
        self._exits[cache_key] = (deepcopy(run), deepcopy(proof))
        return proof

    def usage(self, runs, boundary):
        """Original per-attempt accounting, scoped to an already exited prefix."""
        if len({run['id'] for run in runs}) != len(runs):
            raise ValueError('历史累计不能重复计算同一原run。')
        from .store import Store
        usages = []
        for run in runs:
            proof = self.exited(run)
            if proof['exited']['seq'] >= boundary:
                raise ValueError('历史累计包含原边界尚未退出的attempt。')
            # With db explicitly supplied Store.usage is a SQL-only algorithm;
            # no Store instance, initialization, migration or read() is used.
            usages.append(Store.usage(None, attempt=run['attempt_id'], db=self.db))
        numeric = ('model_requests', 'reserved_cny', 'tool_calls', 'elapsed_observed_seconds',
            'api_known_input_tokens', 'api_known_output_tokens', 'api_requests_without_token_receipt', 'unknown_requests')
        value = {key: round(sum(usage[key] for usage in usages), 3) for key in numeric}
        for key in numeric:
            finite(value[key])
            if value[key] < 0:
                raise ValueError('历史累计或恢复预算债务不能为负。')
        value.update(attempts=len(runs), account_charge='UNKNOWN', subscription_remaining='UNKNOWN',
            native_model_requests='UNKNOWN', request_count_scope='LOCAL_REQUEST_LEDGER_ONLY_NOT_NATIVE_TOTAL',
            scope='ALL_COOPERATIVE_ATTEMPTS_FOR_BUSINESS_ITEM')
        return value

    def verify_input(self, run, frozen, prompt, driver, authorization, ordinal):
        """Compare independently built historical input with the full dispatch.

        frozen/prompt/driver/auth are supplied by the enclosing event replay,
        never read back from run.input as substitute historical authority.
        """
        actual, task, attempt, plan, unit, reserved = self.run(run['id'])
        spec = task['spec']
        meta = {'item_id': plan['item_id'], 'source_item_revision': driver['item_revision'],
            'driver_id': driver['id'], 'generation': driver['generation'], 'driver_revision': driver['revision'],
            'plan_id': plan['id'], 'plan_version': plan['version'], 'unit_key': unit['key'],
            'unit_revision': digest(unit), 'input_version': digest(frozen),
            'authorization': {k: authorization[k] for k in ('id', 'revision')}}
        if frozen.get('coordination_proposal'):
            meta['proposal'] = frozen['coordination_proposal']['reference']
        if (actual != run or run['input'] != frozen or run['cooperative'] != meta or
                any(run.get(k) != v for k, v in meta.items()) or run['item_revision'] != driver['item_revision'] or
                run['ordinal_total'] != ordinal or run.get('retry_of') is not None or run.get('flow') is not None or
                run['scheduling'] != 'EXPLICIT_SERIAL_STEP' or attempt['worker_instance'] != driver['instance'] or
                driver['state'] != 'RESERVED' or driver['generation'] != authorization['driver_generation'] or
                attempt['workspace'] != plan['coordinator_file_handoff_root']['project_root']):
            raise ValueError('历史派发输入、原责任授权、累计或执行实例不符。')
        for time in (task['created'], attempt['started'], run['created']):
            finite(time)
        if not task['created'] <= attempt['started'] <= run['created'] <= reserved['created']:
            raise ValueError('历史task、attempt和保留事件时间不符。')
        resource = spec.get('execution_resource')
        reference = unit['member']['reference']
        if not isinstance(resource, dict) or resource.get('reference') != reference:
            raise ValueError('历史派发资源缺少原准确成员引用。')
        connection, member, quota = resource['connection'], resource['member'], resource['quota_source']
        binding = {k: connection[k] for k in ('id', 'provider', 'credential_reference')}
        if (member['id'] != reference['member_id'] or member['revision'] != reference['member_revision'] or
                connection['id'] != reference['connection_id'] or connection['revision'] != reference['connection_revision'] or
                member['connection_id'] != connection['id'] or connection['quota_id'] != reference['quota_id'] or
                quota['id'] != reference['quota_id'] or quota['revision'] != reference['quota_revision'] or
                any(member[k] != unit['member'][k] for k in ('name', 'role', 'provider', 'model', 'effort', 'tier')) or
                connection['provider'] != member['provider'] or spec['execution_connection'] != binding):
            raise ValueError('历史资源完整成员、连接、额度或credential引用不符。')
        finite(resource['selected_at'])
        if resource['selected_at'] > task['created']:
            raise ValueError('历史派发资源选择晚于原task。')
        self._event(self.db, 'execution_resource_frozen', resource, reserved['seq'], task['id'], attempt['id'])
        manifest = {}
        for dependency in frozen['file_artifacts']:
            for name, record in dependency['manifest'].items():
                if name in manifest and manifest[name] != record:
                    raise ValueError('历史独立输入材料存在路径版本冲突。')
                manifest[name] = record
        blobs = self.assets.manifest('tasks/' + run['task_id'] + '/input/', manifest)
        from .attachments import normalize, verify_format
        attachments = normalize([{'file': name, 'route': 'native'} for name in manifest])
        for attachment in attachments:
            verify_format(attachment, blobs[attachment['file']])
            attachment.update(manifest[attachment['file']], status='SAVED_NOT_READ',
                comprehension='NOT_ATTESTED', fallback='NONE')
            attachment['delivery'] = ('NATIVE_IMAGE_INPUT' if attachment['kind'] == 'image' and
                spec['provider'] != 'qwen' else 'NATIVE_FILE_TOOLS')
        from .native_permissions import choice
        if (spec['goal'] != prompt or spec['materials'] != manifest or spec['attachments'] != attachments or
                spec['writable'] != [] or spec['checks'] != [] or spec['work_mode'] != 'cooperative' or
                spec['native'] != {'configuration': 'VENDOR_DEFAULTS'} or spec['access'] != choice(None) or
                any(spec[k] != unit['member'][k] for k in ('provider', 'model', 'effort', 'tier'))):
            raise ValueError('历史实际spec、附件冻结字节或原生权限不等于原独立输入。')
        context = frozen.get('handoff_coordination_context') or frozen['handoff_unit_context']
        baseline = context['material_observation']
        if baseline['root'] != attempt['workspace'] or baseline['paths'] != sorted(plan['file_handoffs'][0]['paths']):
            raise ValueError('历史材料观察脱离原关系文件及原workspace。')
        self.assets.baseline(baseline)
        if frozen.get('write_before_baseline'):
            self.assets.baseline(frozen['write_before_baseline'])

    @staticmethod
    def raw_evidence(run, proof):
        """Called only after exited() has replayed the actual complete ledger."""
        attempt, result = proof['attempt'], proof['attempt']['result']
        answer = result.get('answer')
        value = {'run_id': run['id'], 'task_id': proof['task']['id'], 'attempt_id': attempt['id'],
            'owner': attempt['owner'], 'session_id': attempt['session_id'], 'turn_id': attempt['turn_id'],
            'state': attempt['state'], 'exact_identity': True,
            'connection_fingerprint': run['connection_fingerprint'], 'result_digest': digest(result),
            'answer': answer if isinstance(answer, str) else '',
            'result_available': bool(attempt['state'] == 'COMPLETED' and isinstance(answer, str) and answer.strip()),
            'check_status': 'NOT_RECORDED', 'independent_review': 'NOT_PERFORMED',
            'human_acceptance': 'NOT_RECORDED', 'blockers': []}
        value['version'] = digest(value)
        return value

    def files(self, run, proof, raw, dependencies):
        """Dependencies must be reconstructed by the enclosing event owner."""
        db = self.db
        unit, spec = proof['unit'], proof['task']['spec']
        if run['input']['dependencies'] != dependencies:
            raise ValueError('历史冻结输入依赖不等于原边界独立重建值。')
        file_evidence = [{'unit_key': d['unit_key'], **d['file_artifacts']} for d in dependencies
            if d['file_artifacts']['state'] == 'CURRENT']
        if run['input']['file_artifacts'] != file_evidence:
            raise ValueError('历史冻结文件证据与准确依赖不符。')
        manifest = {}
        for dependency in file_evidence:
            for name, record in dependency['manifest'].items():
                if name in manifest and manifest[name] != record:
                    raise ValueError('历史冻结依赖包含同路径的不同版本。')
                manifest[name] = record
        if spec['materials'] != manifest:
            raise ValueError('历史实际input材料与独立依赖不符。')
        self.assets.manifest('tasks/' + run['task_id'] + '/input/', manifest)
        snapshots = []
        for row in db.execute("SELECT id FROM collaboration WHERE kind='work_unit_artifact' "
                "AND json_extract(data,'$.snapshot.run_id')=? ORDER BY id", (run['id'],)):
            artifact = self.get(db, 'work_unit_artifact', row['id'])
            saved = artifact['snapshot']
            if (set(artifact) != {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'} or
                    artifact['id'] != 'unit-artifact:' + digest(saved) or artifact['version'] != digest(saved) or
                    artifact['item_id'] != run['item_id'] or not raw['result_available']):
                raise ValueError('历史产物外壳、摘要或原结果不符。')
            finite(artifact['created']); _uuid(artifact['command_id'])
            if artifact['created'] < run['created'] or set(saved['manifest']) != set(unit['outputs']):
                raise ValueError('历史产物时间及原声明集合不符。')
            blobs = self.assets.manifest('results/unit-' + artifact['version'] + '/files/', saved['manifest'])
            observations = [{'definition': c, 'passed': WorkCoordinatorHandoffLedger._checked(blobs[c['file']], c)}
                for c in unit['checks']]
            status = ('PASS' if all(o['passed'] for o in observations) else 'FAIL') if observations else 'NOT_RECORDED'
            expected = {'run_id': run['id'], 'result_version': raw['version'],
                'identity': {k: raw[k] for k in ('task_id', 'attempt_id', 'owner', 'session_id', 'turn_id', 'connection_fingerprint')},
                'plan_id': run['plan_id'], 'plan_version': run['plan_version'], 'unit_revision': run['unit_revision'],
                'manifest': saved['manifest'], 'observations': observations, 'check_status': status}
            if saved != expected:
                raise ValueError('历史产物完整值不等于原身份及冻结字节谓词。')
            snapshots.append({'reference': {k: artifact[k] for k in ('id', 'version')}, 'snapshot': saved,
                'state': 'INTACT', 'problems': []})
        return {'run_id': run['id'], 'input_version': run['input_version'],
            'actual_input_manifest': manifest, 'frozen_dependency_manifest': manifest,
            'input_state': 'INTACT', 'input_problems': [], 'snapshots': snapshots,
            'output_state': 'INTACT' if snapshots else 'NOT_RECORDED',
            'scope': 'HISTORICAL_BYTES_AND_DISPATCH_MATERIALS; NOT_CURRENT_INPUT_OR_AUTHORSHIP'}

    def captures(self, run, proof, files):
        db = self.db
        snapshots = {v['reference']['id']: v for v in files['snapshots']}
        captures, seen = [], set()
        for row in db.execute("SELECT * FROM events WHERE kind='cooperative_files_checked' ORDER BY seq"):
            value = _json(row['payload'])
            if (row['task_id'] != run['task_id'] and row['attempt_id'] != run['attempt_id'] and
                    value.get('artifact', {}).get('id') not in snapshots):
                continue
            saved = snapshots.get(value.get('artifact', {}).get('id'))
            if (saved is None or row['task_id'] != run['task_id'] or row['attempt_id'] != run['attempt_id'] or
                    row['seq'] <= proof['exited']['seq'] or value != {'artifact': saved['reference'],
                        'manifest': saved['snapshot']['manifest'], 'check_status': saved['snapshot']['check_status']}):
                raise ValueError('历史捕获事件与准确产物、退出边界及任务归属不符。')
            finite(row['at'])
            artifact = self.get(db, 'work_unit_artifact', saved['reference']['id'])
            if not proof['exited']['created'] <= artifact['created'] <= row['at']:
                raise ValueError('历史产物创建须在原退出与实际捕获事件之间。')
            captures.append({'seq': row['seq'], 'kind': row['kind'], 'created': row['at'], **value})
            seen.add(saved['reference']['id'])
        if seen != set(snapshots):
            raise ValueError('历史产物缺少完整真实捕获事件。')
        # Enclosing root records are independently rebuilt by the catalog.
        # Even before that acceptance, their actual event boundaries cannot
        # authorize captures forbidden by the original one-way A/B protocol.
        for row in db.execute("SELECT * FROM events WHERE kind IN "
                "('coordinator_handoff_delivered','coordinator_handoff_completed') ORDER BY seq"):
            value = _json(row['payload'])
            body = value.get('snapshot', {})
            protects_a = (row['kind'] == 'coordinator_handoff_delivered' and
                body.get('intent', {}).get('source_run') == run['id'])
            completes_b = row['kind'] == 'coordinator_handoff_completed' and body.get('run_id') == run['id']
            if (protects_a or completes_b) and any(capture['seq'] > row['seq'] for capture in captures):
                raise ValueError('A正式交付或B准确完成之后不能追加历史捕获。')
        pointer_id = 'unit-artifact-current:' + run['id']
        pointer = db.execute('SELECT kind,id,data FROM collaboration WHERE id=?', (pointer_id,)).fetchone()
        expected = {'id': pointer_id, 'artifact_id': captures[-1]['artifact']['id']} if captures else None
        if (pointer is None) != (expected is None) or pointer is not None and (
                pointer['kind'] != 'work_unit_artifact_current' or _json(pointer['data']) != expected):
            raise ValueError('历史产物当前指针须是最后一次真实捕获。')
        return captures

    def frame_at(self, run, boundary, dependencies):
        """Private historical frame, strictly before an actual event seq."""
        if type(boundary) is not int or self.db.execute('SELECT 1 FROM events WHERE seq=?', (boundary,)).fetchone() is None:
            raise ValueError('历史frame边界须为实际事件序号。')
        return self._frame_at(run, boundary, dependencies)

    def latest_frame(self, run, dependencies):
        """Full end of this real read snapshot, including its last exit/capture.

        Historical boundaries still require an actual seq via frame_at(). The
        end fence is derived here from this same SQLite snapshot, never from a
        caller's claimed future event or a wall-clock observation.
        """
        boundary = self.db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events').fetchone()[0]
        return self._frame_at(run, boundary, dependencies)

    def _frame_at(self, run, boundary, dependencies):
        proof = self.exited(run)
        if not proof['reserved']['seq'] < proof['exited']['seq'] < boundary:
            raise ValueError('历史frame在原边界前尚未准确退出。')
        raw = self.raw_evidence(run, proof)
        files = self.files(run, proof, raw, dependencies)
        captures = self.captures(run, proof, files)
        prior = [v for v in captures if v['seq'] < boundary]
        selected = prior[-1]['artifact'] if prior else None
        recorded = {v['artifact']['id'] for v in prior}
        files['snapshots'] = [v for v in files['snapshots'] if v['reference']['id'] in recorded]
        files['output_state'] = 'INTACT' if files['snapshots'] else 'NOT_RECORDED'
        saved = next((v['snapshot'] for v in files['snapshots'] if v['reference'] == selected), None)
        unit = proof['unit']
        status = {'state': 'CURRENT' if selected else 'MISSING' if unit['outputs'] else 'NOT_REQUIRED',
            'reference': selected, 'problems': [], 'manifest': saved['manifest'] if saved else {},
            'check_status': saved['check_status'] if saved else 'NOT_RECORDED',
            'checks': saved['observations'] if saved else [], 'required_outputs': unit['outputs'],
            'required_checks': unit['checks'], 'independent_review': 'NOT_PERFORMED', 'human_acceptance': 'NOT_RECORDED',
            'provenance': 'OBSERVED_AFTER_EXACT_TURN_NOT_PROOF_OF_AUTHORSHIP',
            'delivery': 'ORIGINAL_PROJECT_FILES_REVALIDATED_AT_DISPATCH'}
        full = {**raw, 'result_version': raw['version'], 'file_artifacts': status, 'check_status': status['check_status']}
        full['version'] = digest(full)
        # exited() already returned an independent full proof. Everything else
        # above is freshly built from that proof; only the supplied run needs
        # its own copy. Do not copy the large spec/input tree a second time.
        return {'run': deepcopy(run), 'task_spec': proof['task']['spec'], 'workspace': proof['attempt']['workspace'],
            'evidence': raw, 'source_evidence': full, 'files': files}
