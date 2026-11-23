"""Real parent prefix and observation reconciliation at a typed text edge.

Old immutable carriers are registered in the caller's complete ledger. Mutable
current pointers and all new command/run/receipt tails belong to the enclosing
text owner. This module never closes a partial database or grants live access.
"""
from copy import deepcopy
import itertools
from pathlib import Path

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import DELIVERY_KIND, COMPLETION_KIND
from .work_coordinator_handoff_historical_assets import HistoricalSources, _json
from .work_coordinator_handoff_historical_namespace import CanonicalLedger
from .work_coordinator_handoff_historical_timeline import HistoricalTimeline
from .work_coordinator_handoff_historical_original import HistoricalOriginal
from .work_coordinator_handoff_post_round_v3_path import plan_kind
from .work_coordinator_handoff_text_rework_parent import _TypedEdge
from .work_coordinator_handoff_text_rework_values import observation_value
from . import work_coordinator_handoff_text_rework_contracts as c


def _business_before(owner, boundary):
    """Consume every actual original business revision before the real edge."""
    timeline, sources, ledger, db = owner.timeline, owner.sources, owner.ledger, owner.db
    item_id = timeline.item_id
    initial = timeline.business(1)
    if (initial['state'] != 'OPEN' or initial['record_refs'] or
            initial['created'] != initial['updated']):
        raise ValueError('返工父业务须从准确原创建版本重建。')
    from .work_items import TEXT_FIELDS
    required = {'title', 'goal'} | {k for k in TEXT_FIELDS if initial[k]}
    if initial['project_id'] is not None:
        required.add('project_id')
    optional = sorted(({*TEXT_FIELDS, 'project_id'}) - required)
    response = {'work_item': {k: v for k, v in initial.items() if k != 'record_refs'},
        'no_dispatch': True, 'execution_unchanged': True}
    commands = list(db.execute("SELECT * FROM commands WHERE kind='work-item.create'"))
    matches = []
    for size in range(len(optional) + 1):
        for subset in itertools.combinations(optional, size):
            request = {k: initial[k] for k in required | set(subset)}
            matches.extend((raw, request) for raw in commands
                if raw['payload_hash'] == digest(request) and _json(raw['receipt']) == response)
    if len(matches) != 1:
        raise ValueError('返工父初始业务缺少唯一原规范请求及完整回执。')
    body = {'work_item_id': item_id, 'revision': 1, 'no_dispatch': True}
    event = sources._event(db, 'work_item_created', body, boundary)
    if initial['created'] > event['created']:
        raise ValueError('返工父初始业务时间晚于实际事件。')
    ledger.business_version(item_id, 1, initial, 'business:1')
    ledger.event_at(event['seq'], event['kind'], body, 'business:1')
    ledger.command(matches[0][0]['id'], 'work-item.create', matches[0][1], response,
        'business:1', not_before=event['created'])
    tail = timeline.business_before(boundary)
    previous = initial
    for revision in range(2, tail['revision'] + 1):
        current = timeline.business(revision)
        if (item_id, revision) not in ledger.versions:
            update = sources._event(db, 'work_item_updated',
                {'work_item_id': item_id, 'revision': revision, 'no_dispatch': True}, boundary)
            exits = [sources.exited(run)['exited']['seq'] for run in owner.replay.runs.values()
                if sources.exited(run)['exited']['seq'] < update['seq']]
            ledger.management_update(previous, current, after_seq=max(exits, default=0),
                owner='business:' + str(revision))
        elif ledger.versions[(item_id, revision)][0] != current:
            raise ValueError('返工父已消费业务修订与实际不可变版本不符。')
        previous = current
    return tail


def _tails_before(owner, boundary, history):
    """Require actual old driver/auth carriers without claiming new pointers."""
    sources, ledger, timeline = owner.sources, owner.ledger, owner.timeline
    for plan in timeline.plans:
        for event, driver in timeline.driver_versions(plan):
            if event['seq'] >= boundary:
                continue
            carrier = {**driver, 'id': 'driver-version:' + driver['revision'], 'driver_id': driver['id']}
            registered = ledger.rows.get(('work_driver_revision', carrier['id']))
            occurrence = ledger.events.get(event['seq'])
            if (registered is None or registered[0] != carrier or occurrence is None or
                    occurrence[0] != (event['kind'], {**driver, 'no_dispatch': True}, None, None)):
                raise ValueError('返工父责任尾包含未按原语义重建的不可变版本或事件。')
    auth = history['authorization']
    # The exact stored authorization revision envelope is owned by each old
    # save/authorize writer. Read its real key, never reconstruct an alias.
    versions = [value for value in sources.all(sources.db, 'work_unit_authorization_revision')
        if value.get('revision') == auth['revision']]
    if len(versions) != 1:
        raise ValueError('返工父缺少唯一准确不可变授权尾。')
    value = versions[0]
    if value['id'] != 'unit-auth-version:' + auth['revision'] or {**value, 'id': auth['id']} != auth:
        raise ValueError('返工父不可变授权内容不等于历史准确授权。')
    registered = ledger.rows.get(('work_unit_authorization_revision', value['id']))
    if registered is None or registered[0] != value:
        raise ValueError('返工父授权须已经由原完整保存/授权语义消费。')


def _boundary_current_frames(history, sources):
    """Rebuild live artifact status from the proved boundary's frozen bytes.

    Original replay frames retain their captured artifacts. Current frames also
    describe whether those files still match the project at the typed edge;
    in particular A's saved snapshot survives after B replaces its live bytes.
    This calculation never reads a later live project or trusts a status field
    from the new observation.
    """
    material = history['material_observation']
    sources.assets.baseline(material)
    current = deepcopy(history['current_frames'])
    for frame in current:
        evidence = frame['source_evidence']; files = evidence['file_artifacts']
        if files['reference'] is None:
            continue
        proof = sources.exited(frame['run'])
        if (Path(proof['attempt']['workspace']).resolve() != Path(material['root']).resolve() or
                not set(files['manifest']) <= set(material['paths'])):
            raise ValueError('返工父文件当前状态缺少本边界完整冻结材料覆盖。')
        if files['state'] != 'CURRENT' or files['problems']:
            raise ValueError('返工父原冻结文件须已经由历史owner完整核验。')
        if any(material['manifest'].get(name) != record for name, record in files['manifest'].items()):
            files.update(state='STALE', check_status='STALE', problems=['项目产物已变化或缺失'])
            evidence['check_status'] = 'STALE'
            # WorkUnits.evidence hashes the base result version with the full
            # artifact projection, then replaces that version with the digest.
            evidence['version'] = evidence['result_version']
            evidence['version'] = digest(evidence)
    return current


@c.closed
def parent_at_edge(db, asset_root, capability_ref, ledger, *, command_owner=None):
    if type(ledger) is not CanonicalLedger or type(ledger.sources) is not HistoricalSources:
        raise ValueError('返工父历史须共享真实来源的完整准确账本。')
    sources = ledger.sources
    if ((db.connection if hasattr(db, 'connection') else db) is not sources.db.connection or
            Path(asset_root).absolute() != sources.assets.root):
        raise ValueError('返工父历史不能跨连接或替换冻结资产根。')
    sources.unchanged(db)
    edge = _TypedEdge(sources, capability_ref, command_owner=command_owner)
    plan = edge.parent_plan
    if plan.get(c.LINEAGE_FIELD):
        raise ValueError('首批返工不接受第二条typed边。')
    if plan_kind(plan) == 'POST_V3':
        from .work_coordinator_handoff_post_round_v3_running_assets import HistoricalCatalog
        catalog = HistoricalCatalog(db, asset_root, _sources=sources, _ledger=ledger)
        result = catalog.read_before_text_edge(edge)
    else:
        timeline = HistoricalTimeline(sources, ledger, plan_ref(plan))
        original = HistoricalOriginal(sources, timeline, ledger)
        result = original.read_before_text_edge(edge)
    owner, values = result['owner'], result['values']
    history = deepcopy(result['history'])
    # Complete original dynamic fields are derived from values; no Current is
    # constructed and no serialized observation is used to seed a run input.
    history.update(root=plan_ref(owner.timeline.root), authorization=deepcopy(values._auth),
        frontier=[values.old.frontier_entry(f) for f in values.frames],
        delivery=[row for row, _ in values.records[DELIVERY_KIND]],
        completion=[row for row, _ in values.records[COMPLETION_KIND]])
    history['current_frames'] = _boundary_current_frames(history, sources)
    item = _business_before(owner, edge.boundary)
    if (history['task'] != {'id': item['id'], 'revision': item['revision']} or
            values.driver != owner.timeline.driver_before(plan, edge.boundary)):
        raise ValueError('返工父业务/责任须等于真实首readiness前的完整历史尾。')
    _tails_before(owner, edge.boundary, history)
    body, obs = edge.capability['snapshot'], edge.capability['snapshot']['observation']
    plans = {p['id']: p for p in owner.timeline.plans}
    expected = observation_value(plan, history, plans, body['request']['unit'],
        obs['readiness_observation'], obs['observed_at'])
    if (body['root'] != plan_ref(owner.timeline.root) or body['parent_type'] != values.stage or
            obs != expected or body['request'] != c.request_for(expected)):
        raise ValueError('返工父观察不等于SQL、原冻结字节及实际事件边界的独立重建值。')
    position = 'text-review:' + edge.capability['id']
    ledger.readiness_observation(edge.readiness, position)
    ledger.event_at(edge.readiness_event['seq'], edge.readiness_event['kind'],
        {'id': edge.readiness['id'], 'version': edge.readiness['version'], 'no_dispatch': True}, position)
    ledger.row(c.KINDS[c.CAPABILITY], edge.capability, position)
    ledger.event_at(edge.opened['seq'], edge.opened['kind'], edge.capability, position)
    sources.assets.recheck(); edge.require(sources, plan)
    return {**result, 'history': history, 'edge': edge, 'observation': expected,
        'boundary': edge.boundary, 'sources': sources, 'ledger': ledger}
