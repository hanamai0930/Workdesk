"""Event-ordered historical V3 catalog over real SQL and frozen bytes only.

One catalog belongs to one caller-owned read snapshot. All input values are
generated from proved earlier boundaries; current execution permission is never
returned. The library caller performs the final frozen-byte recheck.
"""
from copy import deepcopy
import itertools
from types import SimpleNamespace

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import fields, versioned, PERMISSIONS, DELIVERY_KIND
from .work_coordinator_handoff_post_round_candidates import ref
from .work_coordinator_handoff_adjustment_records import finite
from .work_coordinator_handoff_historical_assets import HistoricalSources, FrozenAssets, _json
from .work_coordinator_handoff_historical_namespace import CanonicalLedger
from .work_coordinator_handoff_historical_timeline import HistoricalTimeline
from .work_coordinator_handoff_historical_original import HistoricalOriginal
from .work_coordinator_handoff_historical_values import HistoricalValues
from .work_coordinator_handoff_historical_parent import event_value
from . import work_coordinator_handoff_post_round_v3_saved_records as saved
from . import work_coordinator_handoff_post_round_v3_successor_contracts as first
from . import work_coordinator_handoff_post_round_v3_continuation_contracts as later
from . import work_coordinator_handoff_post_round_v3_continuation_proposals as proposals


def running_items(db):
    """Discover typed runtime responsibility through real nested values/keys.

    Renaming a database kind or removing its outer item label cannot select
    the legacy route. An unattributable runtime marker is rejected outright.
    """
    from . import work_coordinator_handoff_post_round_v3_responsibility_records as responsibility
    from . import work_coordinator_handoff_post_round_v3_commands as commands
    from . import work_coordinator_handoff_post_round_v3_continuation_receipts as receipts
    modules = (first, later, proposals, responsibility, commands, receipts)
    protocols = {value for module in modules for name, value in vars(module).items()
        if name.isupper() and isinstance(value, str) and value.startswith('COORDINATOR_FILE_HANDOFF_')}
    prefixes = tuple(getattr(module, name) + ':' for module in modules for name in ('KIND', 'RECEIPT_KIND') if hasattr(module, name))
    owners = {}
    for raw in db.execute('SELECT data FROM collaboration WHERE kind=?', (saved.KIND_FOR[saved.CANDIDATE],)):
        row = _json(raw[0]); saved.validate_row(row, saved.CANDIDATE)
        plan = row['snapshot']['child_plan']; owners.setdefault(plan['item_id'], set()).update((plan['item_id'], plan['id'], plan['version']))
    active = set()
    tables = [('collaboration', 'data'), ('events', 'payload'), ('commands', 'receipt')]
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_readiness_observations'").fetchone():
        tables.append(('work_readiness_observations', 'data'))
    for table, field in tables:
        for raw in db.execute('SELECT ' + field + ' FROM ' + table):
            pending = [_json(raw[0])]; marked = False; strings = set()
            while pending:
                node = pending.pop()
                if isinstance(node, dict):
                    protocol = node.get('protocol')
                    marked = marked or isinstance(protocol, str) and protocol in protocols
                    pending.extend(node.keys()); pending.extend(node.values())
                elif isinstance(node, list):
                    pending.extend(node)
                elif isinstance(node, str):
                    strings.add(node); marked = marked or node in protocols or node.startswith(prefixes)
            if marked:
                found = {item for item, identities in owners.items() if identities & strings}
                if not found:
                    raise ValueError('V3运行或责任外包装缺少可独立重建的准确原保存归属。')
                active.update(found)
    return active


class HistoricalCatalog:
    def __init__(self, db, asset_root, *, _sources=None, _ledger=None):
        if _sources is None and _ledger is None:
            self.assets = FrozenAssets(asset_root)
            self.sources = HistoricalSources(db, self.assets)
            self.ledger = CanonicalLedger(self.sources)
        else:
            from pathlib import Path
            if (type(_sources) is not HistoricalSources or type(_ledger) is not CanonicalLedger or
                    _ledger.sources is not _sources or
                    (db.connection if hasattr(db, 'connection') else db) is not _sources.db.connection or
                    Path(asset_root).absolute() != _sources.assets.root):
                raise ValueError('共享V3前缀须属于真实同连接来源、冻结根和唯一账本。')
            _sources.unchanged(db)
            self.sources, self.assets, self.ledger = _sources, _sources.assets, _ledger
        self.db = self.sources.db
        self.changes = self.db.total_changes
        self.inputs, self.originals = {}, {}

    def require_snapshot(self, db=None):
        raw = self.db.connection
        actual = db.connection if hasattr(db, 'connection') else db
        if (not self.db.in_transaction or self.db.total_changes != self.changes or
                db is not None and actual is not raw):
            raise ValueError('运行目录只能在本次准确不变SQLite读取快照内使用。')

    def require_context(self, run, context):
        self.require_snapshot()
        original = self.inputs.get(run['id'])
        actual = self.sources.get(self.db, 'work_unit_run', run['id'])
        expected = None if original is None else original.get('handoff_coordination_context') or original.get('handoff_unit_context')
        if actual != run or original != run['input'] or expected != context:
            raise ValueError('运行资产context必须属于本次独立重建的准确完整run和输入。')

    def require_intent(self, run, intent):
        self.require_snapshot()
        original = self.inputs.get(run['id'])
        if (self.sources.get(self.db, 'work_unit_run', run['id']) != run or original != run['input'] or
                original is None or original.get('handoff_delivery_intent') != intent):
            raise ValueError('运行资产intent必须属于本次准确独立重建的完整普通输入。')

    def _row(self, kind, row, owner, *, task=None, attempt=None):
        event_kind = first.OPENED if kind == first.KIND else later.OPENED if kind == later.KIND else None
        if event_kind is None:
            raise ValueError('运行能力只接受明确首次或续行codec。')
        self.ledger.row(kind, row, owner)
        return self.ledger.event(event_kind, row, owner, task=task, attempt=attempt)

    def _prefix(self, original, timeline):
        from .work_coordinator_handoff_post_round_v3_successor_prefix import reconstruct_prefix
        from .work_coordinator_handoff_post_round_v3_responsibility_records import KIND, EVENT
        prefix = reconstruct_prefix(None, self.db, original)
        for row in prefix['transitions']:
            owner = 'driver:' + row['snapshot']['driver_after']['revision']
            self.ledger.row(KIND, row, owner)
            self.ledger.event(EVENT, row, owner)
            driver = row['snapshot']['driver_after']
            event, actual = next((e, d) for e, d in timeline.driver_versions(original['child_plan']) if d == driver)
            if actual != driver:
                raise ValueError('运行资产责任前缀须等于准确实际修订。')
            self.ledger.row('work_driver_revision', {**driver, 'id': 'driver-version:' + driver['revision'], 'driver_id': driver['id']}, owner)
            self.ledger.event_at(event['seq'], event['kind'], {**driver, 'no_dispatch': True}, owner)
            if row['command_id'] is not None:
                from .work_coordinator_handoff_post_round_v3_responsibility_records import COMMAND, receipt
                self.ledger.command(row['command_id'], COMMAND, row['snapshot']['request'], receipt(row, original['registration']),
                    owner, not_before=max(row['created'], self.sources._event(self.db, EVENT, row)['created']))
        return prefix

    def _values(self, owner, original, boundary, material):
        plan = original['child_plan']
        self.assets.baseline(material)
        if material['root'] != owner.timeline.root['coordinator_file_handoff_root']['project_root'] or material['paths'] != sorted(plan['file_handoffs'][0]['paths']):
            raise ValueError('V3边界baseline必须保持原根及完整声明路径。')
        values = HistoricalValues(self.sources, owner.timeline, original, boundary,
            owner.replay.frames_before(boundary), owner.ab.prove_before(boundary), material)
        if owner.timeline.business_before(boundary)['revision'] != values.driver['item_revision']:
            raise ValueError('V3原派发或检查边界须保持自己的准确业务修订。')
        for event, driver in owner.timeline.driver_versions(plan):
            if event['seq'] >= boundary:
                continue
            recorded = self.ledger.rows.get(('work_driver_revision', 'driver-version:' + driver['revision']))
            if recorded is None or recorded[0] != {**driver, 'id': 'driver-version:' + driver['revision'], 'driver_id': driver['id']}:
                raise ValueError('V3边界责任必须来自已经完整独立重建的前缀。')
        return values

    def _next_run(self, plan, capability, boundary):
        found = [(self.sources.run(run['id'])[-1]['seq'], run) for run in self.sources.all(self.db, 'work_unit_run')
            if run.get('plan_id') == plan['id'] and run.get('command_id') == capability['command_id']]
        if len(found) != 1 or found[0][0] <= boundary:
            raise ValueError('V3开启能力须在同一真实命令唯一保留之前。')
        return found[0][1]

    def _witness(self, values, run, driver, capability, response, *, is_first):
        if is_first:
            from . import work_coordinator_handoff_post_round_v3_commands as codec
            kind, event_kind = codec.RECEIPT_KIND, codec.RECEIPT_EVENT
            body = codec.body_for(SimpleNamespace(ref=ref, _successor=capability), values.plan, run, driver, response)
        else:
            from . import work_coordinator_handoff_post_round_v3_continuation_receipts as codec
            kind, event_kind = codec.KIND, codec.EVENT
            body = codec.body_for(values, values.plan, run, driver, capability, response)
        rows = [v for v in self.sources.all(self.db, kind) if v.get('command_id') == run['command_id']]
        if len(rows) != 1:
            raise ValueError('V3每次运行须有唯一原完整回执见证。')
        row = rows[0]; fields(row, {'id', 'version', 'snapshot', 'item_id', 'command_id', 'created'}); finite(row['created'])
        event = self.sources._event(self.db, event_kind, row)
        command = self.ledger.commands[run['command_id']][0]
        expected = {'id': kind + ':' + body['version'], 'version': body['version'], 'snapshot': body,
            'item_id': run['item_id'], 'command_id': run['command_id'], 'created': row['created']}
        de = values._driver_event(self.db, driver)
        if row != expected or event['seq'] <= de or not driver['updated'] <= row['created'] <= event['created'] <= command['created']:
            raise ValueError('V3原完整回执须保持准确codec、执行绑定和提交顺序。')
        self.ledger.row(kind, row, 'run:' + run['id'])
        self.ledger.event_at(event['seq'], event_kind, row, 'run:' + run['id'])
        return deepcopy(row)

    def _advance(self, owner, values, run, frozen, request, capability, *, is_first):
        replay = owner.replay
        if run['id'] in replay.runs or run['ordinal_total'] != len(replay.runs) + 1:
            raise ValueError('V3原保留须接续准确连续且唯一的祖先运行。')
        self.sources.verify_input(run, frozen, values.prompt(frozen), values.driver, values._auth, len(replay.runs) + 1)
        if run['input'] != frozen:
            raise ValueError('V3冻结输入不等于实际较早状态独立生成的完整值。')
        proof = self.sources.exited(run); position = 'run:' + run['id']
        self.ledger.row('work_unit_run', run, position)
        self.ledger.row('work_dispatch', proof['dispatch'], position)
        self.ledger.execution(proof, position)
        deliveries = [v for v, _ in owner.ab._records[DELIVERY_KIND] if v['snapshot']['run_id'] == run['id']]
        if len(deliveries) > 1:
            raise ValueError('V3普通输入不能重复拥有正式交付。')
        link = owner.timeline.execution_link(run, values.driver, delivery=deliveries[0] if deliveries else None, owner=position)
        from .work_coordinator_handoff_post_round_v3_continuation_receipts import response_for
        response = response_for(SimpleNamespace(store=self.sources), self.db, run, proof['dispatch'])
        self.ledger.command(run['command_id'], 'work-item.unit-send', request, response, position,
            not_before=max(link['event']['created'], proof['dispatch']['created']))
        # _driver_event reads the actual SQL revision, even though its event is
        # after this reservation boundary. Only the receipt formatter uses it.
        witness = self._witness(values, run, link['driver'], capability, response, is_first=is_first)
        replay.runs[run['id']], replay.inputs[run['id']] = deepcopy(run), deepcopy(frozen)
        replay.links[run['id']], replay.receipts[run['id']] = deepcopy(link), deepcopy(response)
        return witness

    def _proposal(self, owner, original, row, seq):
        witnesses = [w for w in self.sources.all(self.db, proposals.KIND) if w.get('snapshot', {}).get('proposal') == row]
        if len(witnesses) != 1:
            raise ValueError('V3每次检查须有唯一完整原回执。')
        witness = proposals.validate_receipt(witnesses[0]); body = witness['snapshot']
        values = self._values(owner, original, seq, body['material_observation'])
        previous = owner.proposals.previous(seq)
        result = proposals.reconstruct(values, self.db, values.history(), witness, previous, pending=False)
        source = row['snapshot']['source']; event = self.sources._event(self.db, 'coordinator_proposal_checked', row,
            task=source['task_id'], attempt=source['attempt_id'])
        observed = self.sources._event(self.db, proposals.EVENT, witness)
        if event['seq'] != seq or observed['created'] > result['commands'][0]['created']:
            raise ValueError('V3检查须属于准确原事件及完整提交。')
        position = 'proposal:' + row['id']
        self.ledger.row('work_proposal', row, position)
        self.ledger.event_at(seq, event['kind'], row, position, task=source['task_id'], attempt=source['attempt_id'])
        self.ledger.row(proposals.KIND, witness, position)
        self.ledger.event_at(observed['seq'], proposals.EVENT, witness, position)
        self.ledger.command(row['command_id'], proposals.COMMAND, body['request'], body['response'], position,
            not_before=observed['created'])
        owner.proposals.checked[row['id']] = deepcopy(row)

    def read_history(self, leaf):
        return self._read_history(leaf)

    def read_before_text_edge(self, edge):
        """Private actual V3 prefix; no current pointer or catalog admission.

        The new enclosing owner must prove the new suffix, own every actual
        current pointer and call ledger.close over the complete real database.
        Default read_history/read_all still close only their original V3 tail.
        """
        from .work_coordinator_handoff_text_rework_parent import _TypedEdge
        if type(edge) is not _TypedEdge:
            raise ValueError('V3父历史只接受真实已核文字返工typed边。')
        edge.require(self.sources, edge.parent_plan)
        return self._read_history(plan_ref(edge.parent_plan), edge)

    def _read_history(self, leaf, edge=None):
        timeline = HistoricalTimeline(self.sources, self.ledger, leaf)
        boundary = edge.require(self.sources, timeline.plans[-1]) if edge is not None else None
        owner = HistoricalOriginal(self.sources, timeline, self.ledger)
        original = owner.read_original(); plan = original['child_plan']
        prefix = self._prefix(original, timeline)
        if boundary is not None and prefix['boundary_seq'] >= boundary:
            raise ValueError('V3父原责任前缀不能跨过文字返工边。')
        capability, continuation, first_receipt = None, None, None
        first_run = None
        for raw in self.db.execute('SELECT * FROM events WHERE seq>? ORDER BY seq', (original['events']['registration_event']['seq'],)):
            seq, kind, body = raw['seq'], raw['kind'], _json(raw['payload'])
            if boundary is not None and seq >= boundary:
                break
            if kind in {first.OPENED, later.OPENED} and body.get('item_id') == plan['item_id']:
                is_first = kind == first.OPENED; codec = first if is_first else later
                row = codec.validate_capability(body)
                if (is_first and capability is not None or not is_first and (capability is None or continuation is not None or first_receipt is None) or
                        self.sources.get(self.db, codec.KIND, row['id']) != row):
                    raise ValueError('V3首次及续行能力须准确唯一按实际顺序开启。')
                run = self._next_run(plan, row, seq)
                unit = next(u for u in plan['units'] if u['key'] == run['unit_key'])
                obs = row['snapshot']['observation']
                values = self._values(owner, original, seq, obs['material_observation'])
                if not prefix['boundary_seq'] < seq or not prefix['boundary_time'] <= obs['observed_at'] <= row['created'] <= raw['at']:
                    raise ValueError('V3能力须晚于完整责任前缀并保持原观察时间。')
                proposal = owner.proposals.consume(values, run)
                request = owner.replay.request(plan, run, values.frames)
                if is_first:
                    expected_obs = values.first_observation(prefix, unit, obs['observed_at'])
                    expected = versioned({'protocol': first.CAPABILITY, 'scope': first.SCOPE,
                        'observation': expected_obs, 'request': request, 'request_hash': digest(request), **PERMISSIONS})
                    first_run = run['id']
                else:
                    if first_receipt['created'] > row['created']:
                        raise ValueError('V3续行开启须在首次完整回执之后。')
                    expected_obs = values.continuation_observation(prefix, unit, capability, first_receipt,
                        ref(owner.proposals.previous(seq)) if proposal else None, request, obs['observed_at'])
                    expected = versioned({'protocol': later.CAPABILITY, 'scope': later.SCOPE, 'observation': expected_obs, **PERMISSIONS})
                if row['snapshot'] != expected:
                    raise ValueError('V3开启观察不等于准确原边界独立值。')
                self._row(codec.KIND, row, 'run:' + run['id'])
                if is_first:
                    capability = row
                else:
                    continuation = row
            elif kind == 'cooperative_unit_reserved' and body.get('item_id') == plan['item_id']:
                run = body
                if run['plan_id'] != plan['id'] or capability is None:
                    raise ValueError('原V3保存之后不能出现未开启或其它方案的运行。')
                context = run['input'].get('handoff_coordination_context') or run['input']['handoff_unit_context']
                values = self._values(owner, original, seq, context['material_observation'])
                proposal = owner.proposals.consume(values, run)
                unit = next(u for u in plan['units'] if u['key'] == run['unit_key'])
                is_first = run['id'] == first_run
                if is_first:
                    frozen = values.first_input(capability)
                elif continuation is not None:
                    frozen = values.continuation_input(prefix, unit, capability, continuation, proposal)
                else:
                    raise ValueError('V3后续保留缺少准确唯一续行能力。')
                request = owner.replay.request(plan, run, values.frames)
                receipt = self._advance(owner, values, run, frozen, request, capability if is_first else continuation, is_first=is_first)
                if is_first:
                    first_receipt = receipt
            elif kind == 'coordinator_proposal_checked' and body.get('item_id') == plan['item_id']:
                if body['snapshot']['plan'] != plan_ref(plan):
                    raise ValueError('原V3保存后不能新增祖先方案提案。')
                self._proposal(owner, original, body, seq)
        # Every capture and A/B occurrence is consumed under its original run
        # owner, including captures not selected by an earlier history boundary.
        end = boundary if boundary is not None else self.db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events').fetchone()[0]
        owner.ab.prove_before(end)
        for run in owner.replay.runs.values():
            if boundary is not None:
                if self.sources.exited(run)['exited']['seq'] >= boundary:
                    raise ValueError('文字返工前全部V3及祖先尝试须已准确退出。')
                if self.db.execute('SELECT 1 FROM events WHERE seq>=? AND (task_id=? OR attempt_id=?)',
                        (boundary, run['task_id'], run['attempt_id'])).fetchone():
                    raise ValueError('文字返工后出现旧V3任务的迟到事件。')
            owner.ab.captures(run)
        self.inputs.update(deepcopy(owner.replay.inputs))
        self.originals[plan['id']] = deepcopy(original)
        result = {'original': deepcopy(original), 'prefix': prefix, 'capability': capability,
            'continuation': continuation, 'owner': owner}
        if edge is not None:
            result['values'] = self._values(owner, original, boundary,
                edge.capability['snapshot']['observation']['material_observation'])
            result['history'] = result['values'].history()
            edge.require(self.sources, plan)
        return result

    def collect_baselines(self, callback):
        for original in self.inputs.values():
            context = original.get('handoff_coordination_context') or original.get('handoff_unit_context')
            callback(context['material_observation'])

    def _business_and_tails(self, result):
        owner, original = result['owner'], result['original']
        timeline = owner.timeline; plan = original['child_plan']; item_id = plan['item_id']
        initial = timeline.business(1)
        if initial['revision'] != 1 or initial['state'] != 'OPEN' or initial['record_refs'] or initial['created'] != initial['updated']:
            raise ValueError('运行资产业务根须保持原创建完整初始值。')
        from .work_items import TEXT_FIELDS
        required = {'title', 'goal'} | {key for key in TEXT_FIELDS if initial[key]}
        if initial['project_id'] is not None:
            required.add('project_id')
        optional = sorted(({*TEXT_FIELDS, 'project_id'}) - required)
        response = {'work_item': {k: v for k, v in initial.items() if k != 'record_refs'},
            'no_dispatch': True, 'execution_unchanged': True}
        found = []
        for size in range(len(optional) + 1):
            for subset in itertools.combinations(optional, size):
                request = {k: initial[k] for k in required | set(subset)}
                found.extend((raw, request) for raw in self.db.execute("SELECT * FROM commands WHERE kind='work-item.create'")
                    if raw['payload_hash'] == digest(request) and _json(raw['receipt']) == response)
        if len(found) != 1:
            raise ValueError('原业务创建须有唯一可还原规范请求及完整真实回执。')
        body = {'work_item_id': item_id, 'revision': 1, 'no_dispatch': True}
        event = self.sources._event(self.db, 'work_item_created', body)
        if initial['created'] > event['created']:
            raise ValueError('原业务创建晚于其实际事件。')
        self.ledger.business_version(item_id, 1, initial, 'business:1')
        self.ledger.event_at(event['seq'], event['kind'], body, 'business:1')
        self.ledger.command(found[0][0]['id'], 'work-item.create', found[0][1], response, 'business:1', not_before=event['created'])
        last_exit = max(self.sources.exited(run)['exited']['seq'] for run in owner.replay.runs.values())
        previous = initial
        for raw in self.db.execute('SELECT revision FROM work_item_versions WHERE item_id=? ORDER BY revision', (item_id,)):
            revision = raw['revision']; value = timeline.business(revision)
            if revision != previous['revision'] and revision != previous['revision'] + 1:
                raise ValueError('原业务修订必须连续且完整。')
            if (item_id, revision) not in self.ledger.versions:
                self.ledger.management_update(previous, value, after_seq=last_exit, owner='business:' + str(revision))
            previous = value
        driver = timeline.driver_versions(plan)[-1][1]
        self.ledger.row('teamwork_current', {'id': 'teamwork-current:' + item_id, 'plan_id': plan['id']}, 'current:' + item_id)
        self.ledger.row('work_driver', driver, 'current:' + item_id)
        self.ledger.row('work_unit_authorization', original['authorization'], 'current:' + item_id)
        proposal = owner.proposals.previous(self.db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events').fetchone()[0])
        if proposal is not None:
            self.ledger.row('work_proposal_current', {'id': 'proposal-current:' + item_id, 'proposal_id': proposal['id']}, 'current:' + item_id)

    def read_all(self, items):
        candidates = self.sources.all(self.db, saved.KIND_FOR[saved.CANDIDATE])
        seen = set()
        for candidate in candidates:
            saved.validate_row(candidate, saved.CANDIDATE)
            plan = candidate['snapshot']['child_plan']
            if plan['item_id'] not in items:
                continue
            if plan['item_id'] in seen:
                raise ValueError('同一业务任务不能包含未知的额外V3保存层。')
            seen.add(plan['item_id'])
            result = self.read_history(plan_ref(plan))
            self._business_and_tails(result)
        if seen != set(items):
            raise ValueError('运行资产发现的业务任务未全部匹配准确原V3保存。')
        protocols = {value for module in (first, later, proposals) for name, value in vars(module).items()
            if name.isupper() and isinstance(value, str) and value.startswith('COORDINATOR_FILE_HANDOFF_')}
        self.ledger.close(protocol_prefixes=tuple(protocols))
        self.assets.recheck()
        return self
