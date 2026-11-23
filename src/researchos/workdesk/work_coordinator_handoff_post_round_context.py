"""Read-only admission facts for a separately versioned post-ROUND capability.

Drafts carry no stored capability identity or runtime permit. Existing ROUND
readers remain authoritative for all actual history, inputs and file bytes.
"""
from copy import deepcopy
import time

from .store import digest
from .work_transitions import plan_ref, source_ref
from .work_coordinator_handoff_records import fields, reference, versioned, PERMISSIONS
from .work_coordinator_handoff_adjustment_records import hashed, permissions, source, usage, finite
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_roots import CONFIG_FIELDS, MARKER_FIELD
from .work_coordinator_handoff_adjustments import protected
from .work_coordinator_handoff_runtime import reader

OBSERVATION = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_ADJUSTMENT_OBSERVATION_V1'
DRAFT = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_ADJUSTMENT_CAPABILITY_DRAFT_V1'
PLANNED_CAPABILITY = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_ADJUSTMENT_CAPABILITY_V1'
PLANNED_COORDINATION = 'COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V6'
PLANNED_UNIT = 'COORDINATOR_FILE_HANDOFF_UNIT_INPUT_V5'
SCOPE = 'VERIFIED_CURRENT_ROUND_ADMISSION_FACTS; DRAFT_ONLY; NO_ACTIVATION_OR_INPUT'
ACTIONS = ['dispatch', 'pause', 'adjust_dependencies']
FIELDS = {'protocol', 'root', 'plan', 'plan_snapshot', 'task', 'driver', 'authorization',
    'saved_path', 'runtime_capability', 'adjustment_capability', 'round_capability',
    'phase', 'frontier', 'material_observation', 'selected_unit', 'protected_keys',
    'configuration_version', 'previous_round_source', 'existing_source', 'usage',
    'phase_sources', 'dependencies', 'observed_at', 'scope', 'version', *PERMISSIONS}


def _path(value):
    from .work_coordinator_handoff_round_runtime import PATH, validate_path as first
    from .work_coordinator_handoff_mixed_runtime import validate_path as mixed
    return (first if value.get('protocol') == PATH else mixed)(value)


def _full_evidence(value, base):
    from .work_coordinator_handoff_adjustment_records import evidence as validate_evidence
    validate_evidence(value, full=True)
    expected = {**base, 'result_version': base['version'], 'file_artifacts': value['file_artifacts'],
        'check_status': value['file_artifacts']['check_status']}
    # Preserve WorkUnits.evidence's hash of the extension with base version present.
    expected['version'] = digest(expected)
    if value != expected: raise ValueError('当前完整证据与原冻结结果不符。')


@closed
def validate_observation(value):
    fields(value, FIELDS); hashed(value); permissions(value)
    if value['protocol'] != OBSERVATION or value['scope'] != SCOPE:
        raise ValueError('调整准备须使用独立只读ROUND观察协议。')
    path = _path(value['saved_path']); plan = value['plan_snapshot']
    from .work_coordinator_handoff_round_contracts import successor_plan as first_plan
    from .work_coordinator_handoff_mixed_contracts import successor_plan as mixed_plan
    from .work_coordinator_handoff_mixed_records import LINEAGE, selected
    lineage = plan.get('coordinator_file_handoff_round_lineage', {})
    (mixed_plan if lineage.get('protocol') == LINEAGE else first_plan)(plan)
    for key in ('root', 'plan', 'runtime_capability'):
        reference(value[key])
    for key in ('adjustment_capability', 'round_capability'):
        if value[key] is not None: reference(value[key])
    for key, path_key in (('root', 'root'), ('plan', 'plan'), ('driver', 'driver'),
            ('authorization', 'authorization'), ('runtime_capability', 'runtime_capability'),
            ('adjustment_capability', 'adjustment_capability'), ('round_capability', 'round_capability'),
            ('frontier', 'history_frontier'), ('material_observation', 'material_observation'),
            ('previous_round_source', 'source')):
        if value[key] != path[path_key]:
            raise ValueError('调整观察与准确ROUND路径不符：' + key)
    selected(value['selected_unit']); unit = value['selected_unit']['definition']
    auth, driver = value['authorization'], value['driver']
    fields(value['task'], {'id', 'revision'}); usage(value['usage']); finite(value['observed_at'])
    if (plan_ref(plan) != value['plan'] or plan[MARKER_FIELD]['plan_id'] != value['root']['id'] or
            value['task'] != {'id': plan['item_id'], 'revision': driver['item_revision']} or
            driver['item_id'] != plan['item_id'] or driver['state'] != 'RESERVED' or
            auth['driver_generation'] != driver['generation'] or
            value['phase'] not in {'BEFORE_A', 'A_CAPTURED', 'B_COMPLETED'} or
            value['usage']['attempts'] != len(value['frontier']) or
            value['usage']['attempts'] + 3 > auth['max_attempts_total'] or
            not driver['updated'] <= value['observed_at'] < auth['dispatch_before'] or
            value['configuration_version'] != digest({k: plan[k] for k in CONFIG_FIELDS}) or
            value['protected_keys'] != sorted(protected(plan['units'], plan['file_handoffs'][0])) or
            unit not in plan['units'] or unit['key'] in plan['historical_coordinators'] or
            unit['member']['reference'] != plan['coordinator']['reference']):
        raise ValueError('调整观察的原方案、责任、授权余量、阶段或单元定义不符。')
    qualification = next(q for q in path['current_qualifications'] if q['key'] == unit['key'])
    if qualification['qualification'] != 'NOT_RUN' or qualification['source'] is not None:
        raise ValueError('调整新来源必须是当前方案未执行的原声明coordinate。')
    qmap = {q['key']: q for q in path['current_qualifications']}
    by_run = {f['run_id']: f for f in value['frontier']}
    for q in qmap.values():
        if q['source'] is not None and q['source'] != by_run[q['source']['run_id']]['evidence']:
            raise ValueError('当前资格须逐值绑定完整冻结前沿。')
    phases = value['phase_sources']; fields(phases, {'source', 'target'})
    relation = plan['file_handoffs'][0]
    for name, key in (('source', relation['source_unit']), ('target', relation['target_unit'])):
        base = qmap[key]['source']
        if (base is None) != (phases[name] is None): raise ValueError('阶段来源不能遗漏或新增。')
        if base is not None: _full_evidence(phases[name], base)
    a, b = phases['source'], phases['target']
    phase = ('B_COMPLETED' if b and b['file_artifacts']['state'] == 'CURRENT' and
        qmap[relation['source_unit']]['qualification'] == 'HISTORICAL_RETIRED' else
        'BLOCKED' if b else 'A_CAPTURED' if a and a['file_artifacts']['state'] == 'CURRENT' else 'BEFORE_A')
    if phase != value['phase']: raise ValueError('调整阶段须由真实A/B来源与当前资格重建。')
    dependencies = value['dependencies']
    if not isinstance(dependencies, list) or [d['unit_key'] for d in dependencies] != unit['depends']:
        raise ValueError('所选协调单元须保留完整准确依赖。')
    for dependency in dependencies:
        key = dependency['unit_key']; base = qmap[key]['source']
        if base is None or key in plan['historical_coordinators'] or (
                key == relation['source_unit'] and phase == 'B_COMPLETED') or (
                key == relation['target_unit'] and phase != 'B_COMPLETED'):
            raise ValueError('新协调单元依赖缺失或已经历史退役。')
        evidence = {k: v for k, v in dependency.items() if k != 'unit_key'}
        _full_evidence(evidence, base)
        files = evidence['file_artifacts']; gate = unit['dependency_gate']
        if (not evidence['result_available'] or (gate in {'files', 'checked'} and (
                not files['required_outputs'] or files['state'] != 'CURRENT')) or
                (gate == 'checked' and files['check_status'] != 'PASS')):
            raise ValueError('新协调单元的原依赖结果、文件或检查不可用。')
    previous = value['previous_round_source']; source(previous, full=True)
    if previous != plan['coordination_revision']['source']:
        raise ValueError('调整准备须区分前ROUND来源和当前新来源。')
    existing = value['existing_source']
    # The exact ordered frontier identifies the latest current-plan coordinator.
    definitions = {u['key']: u for u in plan['units']}
    candidates = [f for f in value['frontier'] if
        any(q['source'] and q['source']['run_id'] == f['run_id'] and
            definitions[q['key']]['role'] == 'coordinate' for q in path['current_qualifications'])]
    if existing is not None:
        fields(existing, {'source', 'evidence', 'input_protocol', 'adjustment_eligible'}); source(existing['source'], full=True)
        src = existing['source']
        if (not candidates or src['run_id'] != candidates[-1]['run_id'] or
                src['plan_id'] != plan['id'] or src['plan_version'] != plan['version'] or
                src['input_version'] != candidates[-1]['input_version'] or
                existing['input_protocol'] != ('COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V4'
                    if path['protocol'] == 'COORDINATOR_FILE_HANDOFF_ROUND_RUNTIME_PATH_V1'
                    else 'COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V5') or
                existing['adjustment_eligible'] is not False):
            raise ValueError('旧ROUND来源不能获得新调整许可或冒充另一来源。')
        base, evidence = candidates[-1]['evidence'], existing['evidence']
        _full_evidence(evidence, base)
        definition = next(u for u in plan['units'] if any(q['key'] == u['key'] and q['source'] and
            q['source']['run_id'] == src['run_id'] for q in path['current_qualifications']))
        run_identity = {'plan_id': plan['id'], 'plan_version': plan['version'],
            'unit_revision': digest(definition), 'input_version': candidates[-1]['input_version']}
        if src != source_ref(run_identity, evidence):
            raise ValueError('当前完整结果证据与原冻结结果、原定义或来源不符。')
    elif candidates:
        raise ValueError('调整准备不能遗漏已有当前协调来源。')
    return deepcopy(value)


def observation_ref(value):
    return {k: deepcopy(value[k]) for k in ('protocol', 'version', 'root', 'plan')}


def capability_draft(observation):
    obs = validate_observation(observation)
    return versioned({'protocol': DRAFT, 'planned_capability': PLANNED_CAPABILITY,
        'coordination_protocol': PLANNED_COORDINATION, 'unit_protocol': PLANNED_UNIT,
        'activation_observation': observation_ref(obs), 'root': obs['root'], 'plan': obs['plan'],
        'driver': obs['driver'], 'authorization': obs['authorization'],
        'selected_unit': obs['selected_unit'], 'activation_state': 'DRAFT_ONLY',
        'allowed_actions': ACTIONS, 'scope': SCOPE, **PERMISSIONS})


@closed
def validate_draft(value, observation):
    expected = capability_draft(observation)
    fields(value, set(expected)); hashed(value); permissions(value)
    if value != expected:
        raise ValueError('能力草案须逐值绑定当前观察，且保持只读草案状态。')
    return deepcopy(value)


class WorkCoordinatorHandoffPostRoundContext:
    def __init__(self, service): self.s = service

    @closed
    def observe(self, db, plan, coordinate_key):
        if not db.in_transaction or not plan or not plan.get('coordinator_file_handoff_round_lineage'):
            raise ValueError('调整准备须在同一明确事务读取真实当前ROUND方案。')
        before = db.total_changes
        runtime = reader(self.s, db, plan); h = runtime.read(db, plan)
        item = self.s.work_items.item(db, plan['item_id'])
        self.s.work_units.checked(db, {'id': item['id'], 'revision': item['revision'],
            'current_driver': self.s.work_drivers.reference(h['driver']), 'plan': plan_ref(plan)})
        self.s.work_flows.require_stopped(db, item['id'])
        selected = next((u for u in plan['units'] if u['key'] == coordinate_key), None)
        if selected is None or selected['role'] != 'coordinate':
            raise ValueError('调整准备只选择原声明coordinate。')
        dependencies = runtime._admit(plan, selected, h['frames'], h['phase'])
        existing = [f for f in h['current_frames'] if f['run']['plan_id'] == plan['id'] and
            f['run']['generation'] == h['driver']['generation'] and f['run']['input']['unit']['role'] == 'coordinate']
        latest = existing[-1] if existing else None
        relation = plan['file_handoffs'][0]
        phase_sources = {name: next((f['source_evidence'] for f in reversed(h['current_frames'])
            if f['run']['unit_key'] == relation[key]), None) for name, key in
            (('source', 'source_unit'), ('target', 'target_unit'))}
        obs = validate_observation(versioned({'protocol': OBSERVATION, 'root': h['root'],
            'plan': plan_ref(plan), 'plan_snapshot': plan, 'task': h['task'], 'driver': h['driver'],
            'authorization': h['authorization'], 'saved_path': h['saved_path'],
            'runtime_capability': h['capability'], 'adjustment_capability': h['adjustment_capability'],
            'round_capability': h['round_capability'], 'phase': h['phase'], 'frontier': h['frontier'],
            'material_observation': h['material_observation'],
            'phase_sources': phase_sources, 'dependencies': dependencies,
            'selected_unit': {'definition': selected, 'revision': digest(selected)},
            'protected_keys': sorted(protected(plan['units'], plan['file_handoffs'][0])),
            'configuration_version': digest({k: plan[k] for k in CONFIG_FIELDS}),
            'previous_round_source': h['saved_path']['source'],
            'existing_source': {'source': source_ref(latest['run'], latest['source_evidence']),
                'evidence': latest['source_evidence'],
                'input_protocol': latest['run']['input']['handoff_coordination_context']['protocol'],
                'adjustment_eligible': False} if latest else None,
            'usage': h['usage'], 'observed_at': time.time(), 'scope': SCOPE, **PERMISSIONS}))
        draft = validate_draft(capability_draft(obs), obs)
        # Recheck original byte sources after constructing the new value contracts.
        runtime.checkpoints._recheck(db, {'frames': h['frames'],
            'observation': runtime._ready['observation'],
            'current_material_observation': h['material_observation']})
        if before != db.total_changes or self.s.teamwork.current(db, item['id']) != plan:
            raise ValueError('调整准备末尾数据库或当前方案发生变化。')
        return deepcopy({'observation': obs, 'capability_draft': draft})
