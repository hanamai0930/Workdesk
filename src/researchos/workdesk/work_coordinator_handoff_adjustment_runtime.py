"""Internal V3 inputs; saved paths are verified by the independent Path reader."""
from copy import deepcopy
import json
import math
import time

from .store import canonical, digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_roots import CONFIG_FIELDS, MARKER_FIELD
from .work_coordinator_handoff_records import PERMISSIONS, versioned, fields, reference
from .work_coordinator_handoff_runtime import WorkCoordinatorHandoffRuntime, PROTOCOL as V2, UNIT_PROMPT


KIND = 'work_coordinator_handoff_adjustment_capability'
CAPABILITY = 'COORDINATOR_FILE_HANDOFF_ADJUSTMENT_CAPABILITY_V1'
PROTOCOL = 'COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V3'
UNIT = 'COORDINATOR_FILE_HANDOFF_UNIT_INPUT_V2'
ACTIONS = ['dispatch', 'pause', 'adjust_dependencies']
# Historical protocol literal: preserve bytes in every frozen input/capability.
# Internal saved layers are now supported; public activation remains closed.
SCOPE = 'INTERNAL_ORIGINAL_ROOT_ADJUSTMENT_PREVIEW; SAVED_EDGE_NOT_OPEN'
PROMPT = ('读取完整冻结协调输入，按声明的 dispatch_schema、pause_schema 或 adjustment_context.schema '
          '返回严格 JSON；调整只形成预览，不写文件。\n输入快照 JSON：\n')


def enabled(service, db, plan):
    return bool(plan and any(r.get('item_id') == plan['item_id'] or
        r.get('snapshot', {}).get('root', {}).get('id') == plan['id']
        for r in service.collaboration.all(db, KIND)))


def validate_capability(row):
    fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    body = row['snapshot']
    fields(body, {'protocol', 'root', 'marker', 'runtime_capability', 'configuration_version',
        'activation_plan', 'activation_frontier', 'eligible_coordinate_keys', 'coordination_protocol',
        'unit_protocol', 'allowed_actions', *PERMISSIONS, 'scope', 'version'})
    for name in ('root', 'runtime_capability', 'activation_plan'):
        reference(body[name])
    keys = body['eligible_coordinate_keys']
    if (body['protocol'] != CAPABILITY or body['coordination_protocol'] != PROTOCOL or
            body['unit_protocol'] != UNIT or body['allowed_actions'] != ACTIONS or body['scope'] != SCOPE or
            any(body[k] != v for k, v in PERMISSIONS.items()) or
            not isinstance(keys, list) or not keys or any(not isinstance(k, str) or not k for k in keys) or
            keys != sorted(set(keys)) or not isinstance(body['activation_frontier'], list) or
            body['version'] != digest({k: v for k, v in body.items() if k != 'version'}) or
            row['version'] != body['version'] or row['id'] != KIND + ':' + row['version'] or
            type(row['created']) not in (int, float) or not math.isfinite(row['created']) or
            not isinstance(row['command_id'], str) or not row['command_id']):
        raise ValueError('调整能力的闭合字段、原输入协议或内容身份不一致。')
    return deepcopy(row)


def validate_unit(context):
    # Preserve every V1 field check, then validate only the new closed extension.
    from .work_coordinator_handoff_records import validate, UNIT_INPUT
    fields(context, {'protocol', 'root', 'marker', 'task', 'driver', 'authorization', 'unit_revision',
        'proposal', 'phase', 'prior_frontier', 'material_observation', *PERMISSIONS, 'scope', 'version',
        'adjustment_capability', 'plan', 'saved_path'})
    reference(context['adjustment_capability']); reference(context['plan'])
    if context['saved_path'] is not None:
        from .work_coordinator_handoff_adjustment_records import validate_path
        from .work_coordinator_handoff_adjustment_contracts import successor_driver, transferred_authorization
        path = validate_path(context['saved_path'])
        successor_driver(context['driver']); transferred_authorization(context['authorization'])
        if (context['protocol'] != UNIT or context['scope'] != SCOPE or
                context['version'] != digest({k:v for k,v in context.items() if k!='version'}) or
                path['root'] != context['root'] or path['plan'] != context['plan'] or
                path['capability'] != context['adjustment_capability'] or
                path['history_frontier'] != context['prior_frontier'] or
                path['material_observation'] != context['material_observation'] or
                context['marker']['plan_id'] != context['root']['id'] or
                any(context[k] is not v for k,v in PERMISSIONS.items()) or
                any(v['plan_id'] != context['plan']['id'] or v['plan_version'] != context['plan']['version'] for v in (context['driver'],context['authorization']))):
            raise ValueError('后继普通输入与完整保存路径或转移责任授权不一致。')
        from .work_coordinator_handoff_records import observation, frontier, sha, fields as exact_fields, identity, task_revision, DISPATCH_PHASES
        from .work_coordinator_handoff_adjustment_records import source
        from .work_proposals import parse
        identity(context); exact_fields(context['task'],{'id','revision'}); task_revision(context['task']['revision'])
        observation(context['material_observation']); frontier(context['prior_frontier']); sha(context['unit_revision'])
        exact_fields(context['proposal'],{'reference','source','decision'})
        reference(context['proposal']['reference']); source(context['proposal']['source'])
        decision=context['proposal']['decision']
        if (parse(json.dumps(decision))!=decision or decision['action']!='dispatch' or decision['retry_of'] is not None or
                context['phase'] not in DISPATCH_PHASES or not context['proposal']['reference']['id'].startswith('work-proposal:') or
                context['task']['id']!=context['marker']['item_id'] or context['driver']['item_revision']!=context['task']['revision'] or
                context['material_observation']['root']!=context['marker']['project_root'] or
                context['authorization']['driver_generation']!=context['driver']['generation']):
            raise ValueError('后继普通输入的原提案、任务、目录或generation不一致。')
        return deepcopy(context)
    if (context['protocol'] != UNIT or
            context['plan'] != context['root'] or context['scope'] != SCOPE or
            context['version'] != digest({k: v for k, v in context.items() if k != 'version'})):
        raise ValueError('普通V2只接受准确内部原根输入，保存边待实施。')
    old = {k: v for k, v in context.items() if k not in {'version', 'adjustment_capability', 'plan', 'saved_path'}}
    old.update(protocol=UNIT_INPUT, scope='FROZEN_FIRST_ORDINARY_INPUT; SOURCE_IS_EXACT_V2_COORDINATOR')
    validate(versioned(old), UNIT_INPUT)
    return deepcopy(context)


class WorkCoordinatorHandoffAdjustmentRuntime(WorkCoordinatorHandoffRuntime):
    def activate_adjustment(self, db, plan, coordinate_key):
        """Internal activation only. Public commands never call this yet."""
        if enabled(self.s, db, plan):
            raise ValueError('调整能力不可重复激活。')
        history = WorkCoordinatorHandoffRuntime(self.s).read(db, plan)
        unit = next((u for u in plan['units'] if u['key'] == coordinate_key), None)
        auth, driver = history['authorization'], history['driver']
        if (not unit or unit['role'] != 'coordinate' or not auth or not driver or
                driver['state'] != 'RESERVED' or len(history['frames']) >= auth['max_attempts_total'] or
                time.time() >= auth['dispatch_before']):
            raise ValueError('调整能力须绑定可达的首次声明coordinate及有效原授权。')
        self._admit(plan, unit, history['current_frames'], history['phase'])
        cap, _ = self.capability(db, plan)
        seen = {f['run']['unit_key'] for f in history['frames']}
        body = versioned({'protocol': CAPABILITY, 'root': plan_ref(plan), 'marker': plan[MARKER_FIELD],
            'runtime_capability': self.ref(cap), 'configuration_version': digest({k: plan[k] for k in CONFIG_FIELDS}),
            'activation_plan': plan_ref(plan), 'activation_frontier': history['frontier'],
            'eligible_coordinate_keys': sorted(u['key'] for u in plan['units'] if u['role'] == 'coordinate' and u['key'] not in seen),
            'coordination_protocol': PROTOCOL, 'unit_protocol': UNIT, 'allowed_actions': ACTIONS,
            **PERMISSIONS, 'scope': SCOPE})
        row = {'id': KIND + ':' + body['version'], 'version': body['version'], 'snapshot': body,
            'item_id': plan['item_id'], 'created': time.time(), 'command_id': self.s.command_identity}
        validate_capability(row)
        self.s.collaboration.put(db, KIND, row)
        self.s.store.event(None, None, 'coordinator_adjustment_opened', row, db)
        self.read(db, plan)
        return deepcopy(row)

    def adjustment_capability(self, db, plan):
        rows = [r for r in self.s.collaboration.all(db, KIND) if r.get('item_id') == plan['item_id'] or
                r.get('snapshot', {}).get('root', {}).get('id') == plan['id']]
        if len(rows) != 1:
            raise ValueError('缺少唯一准确调整能力。')
        row = validate_capability(rows[0]); body = row['snapshot']; cap, seq = self.capability(db, plan)
        if (body['root'] != plan_ref(plan) or body['activation_plan'] != plan_ref(plan) or
                body['marker'] != plan[MARKER_FIELD] or body['runtime_capability'] != self.ref(cap) or
                body['configuration_version'] != digest({k: plan[k] for k in CONFIG_FIELDS}) or
                row['item_id'] != plan['item_id'] or row['created'] < cap['created']):
            raise ValueError('调整能力不属于准确原根、配置或原运行能力。')
        opened = self._event(db, 'coordinator_adjustment_opened', row)
        if opened <= seq:
            raise ValueError('调整能力的实际事件早于原运行能力。')
        return row, opened

    def _read(self, db, plan):
        self._adjustment, self._opened = self.adjustment_capability(db, plan)
        history = super()._read(db, plan)
        before = [f for f in history['frames'] if self.sources._reservation_event(db, f['run']) < self._opened]
        after = [f for f in history['frames'] if self.sources._reservation_event(db, f['run']) > self._opened]
        prior = [self.sources._frame_before(db, f['run'], after[0]['run']) for f in before] if after else before
        body = self._adjustment['snapshot']
        seen = {f['run']['unit_key'] for f in before}
        keys = sorted(u['key'] for u in plan['units'] if u['role'] == 'coordinate' and u['key'] not in seen)
        if body['activation_frontier'] != [self.old.frontier_entry(f) for f in prior] or keys != body['eligible_coordinate_keys']:
            raise ValueError('调整能力未保存完整激活前沿或全部未运行coordinate。')
        for f in after:
            protocol = (f['run']['input'].get('handoff_coordination_context') or f['run']['input']['handoff_unit_context'])['protocol']
            expected = PROTOCOL if f['run']['input']['unit']['role'] == 'coordinate' else UNIT
            if protocol != expected:
                raise ValueError('调整能力激活后不能新增旧协议运行。')
        return {**history, 'adjustment_capability': self.ref(self._adjustment)}

    def _run_protocol(self, db, plan, run, context, boundary):
        if boundary < self._opened:
            return super()._run_protocol(db, plan, run, context, boundary)
        return context['protocol'] == (PROTOCOL if run['input']['unit']['role'] == 'coordinate' else UNIT)

    def _historical_input(self, db, run, *args):
        if self.sources._reservation_event(db, run) < self._opened:
            return WorkCoordinatorHandoffRuntime._input(self, *args)
        return self._input(*args)

    def _source_protocol(self, db, plan, source, consumer):
        boundary = self.sources._reservation_event(db, consumer) if consumer else None
        if boundary is not None and boundary < self._opened:
            return super()._source_protocol(db, plan, source, consumer)
        return (source['input'].get('handoff_coordination_context', {}).get('protocol') == PROTOCOL and
                self.sources._reservation_event(db, source) > self._opened)

    def _input(self, plan, unit, task, driver, auth, frames, observation, records, cap, proposal=None):
        value = super()._input(plan, unit, task, driver, auth, frames, observation, records, cap, proposal)
        field = 'handoff_coordination_context' if unit['role'] == 'coordinate' else 'handoff_unit_context'
        context = {k: v for k, v in value[field].items() if k != 'version'}
        context.update(protocol=PROTOCOL if unit['role'] == 'coordinate' else UNIT,
            adjustment_capability=self.ref(self._adjustment), plan=plan_ref(plan), saved_path=None, scope=SCOPE)
        if unit['role'] == 'coordinate':
            context['allowed_actions'] = ACTIONS
        context = versioned(context); value[field] = context
        if unit['role'] == 'coordinate':
            from .work_coordinator_handoff_adjustments import input_context
            forecast = value['coordination_context']
            forecast.update(allowed_actions=ACTIONS, scope=SCOPE,
                handoff_context={k: context[k] for k in ('protocol', 'version', 'root')},
                adjustment_context=input_context(plan, context, frames),
                adjustment_prior_frontier=context['prior_frontier'])
        else:
            validate_unit(context)
        return value

    @staticmethod
    def prompt(frozen):
        if frozen.get('handoff_coordination_context', {}).get('protocol') == PROTOCOL:
            return PROMPT + canonical(frozen)
        return WorkCoordinatorHandoffRuntime.prompt(frozen)
