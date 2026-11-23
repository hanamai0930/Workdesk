"""Complete V3 first-coordinate owner, separate from saved/responsibility readers."""
from copy import deepcopy
import json
import time

from .store import canonical, digest
from .work_read_observation import database_observation
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_candidates import ref
from . import work_coordinator_handoff_post_round_v3_saved_records as r
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import versioned, PERMISSIONS
from .work_coordinator_handoff_mixed_runtime import WorkCoordinatorHandoffMixedRuntime
from .work_coordinator_handoff_runtime import WorkCoordinatorHandoffRuntime
from .work_coordinator_handoff_post_round_v3_responsibility_current import WorkCoordinatorHandoffPostRoundV3ResponsibilityCurrent
from . import work_coordinator_handoff_post_round_v3_successor_contracts as c
from .work_coordinator_handoff_post_round_v3_command_scope import pending, pending_request, bind_capability


class WorkCoordinatorHandoffPostRoundV3Runtime(WorkCoordinatorHandoffPostRoundV3ResponsibilityCurrent):
    def __init__(self, service, *, _application=None):
        super().__init__(service)
        from .work_coordinator_handoff_post_round_v3_successor_component import _SuccessorComponent
        self.component = _SuccessorComponent(service)

    def _once(self, db, plan):
        self._db=db
        candidates = [v for v in self.s.collaboration.all(db, r.KIND_FOR[r.CANDIDATE])
            if v.get('snapshot', {}).get('child_plan') == plan]
        if len(candidates) != 1:
            raise ValueError('V3完整树须有唯一准确child候选。')
        # _once creates a fresh actual V8 owner. Its private V2 traversal still
        # executes exactly two components per complete V2 read, without reuse.
        ready = self.component._once(db, ref(candidates[0]))
        parent = self.component._parent_reader
        self._parent_reader = parent
        self._parents = parent._parents + [ready['checkpoint']['snapshot']['plan']]
        self._layers = parent._layers + [parent]
        self._collected = self._collect(db, plan)
        if self._collected != self._parents + [plan]:
            raise ValueError('V3完整树实际祖先不等于逐层独立重建顺序。')
        self._root_plan = self._parents[0]
        self._plan_ids = {p['id'] for p in self._collected}
        self._identity_ids = {v for p in self._collected for v in plan_ref(p).values()}
        self._task_ids = set(); self._attempt_ids = set()
        self._frame_identities(ready['frames'])
        self._ready = ready; self._round = None
        self._application = ready['application']; self._registration = ready['registration']
        self._auth = ready['authorization']
        self._edge_value = ready['saved_path']['ancestors'][-1]['edge']
        original_driver = ready['driver']
        self._responsibility = deepcopy(self.component._chain)
        chain = self._responsibility
        extension = chain['extension']
        self._versions = extension['versions']
        self._successor = extension['capability']; self._opened = extension['opened']['seq']
        self._continuation=extension['continuation']; self._original=extension['original']
        self._prefix=extension['prefix']
        driver = chain['driver']
        ph = ready['observation']['history']
        current = deepcopy(extension['frames'])
        for f in current:
            f['source_evidence'] = self.s.work_units.evidence(db, f['run'])
        eligible = self._eligible(plan, current)
        records=self._records(db,plan)
        phase = self._phase(plan,self._eligible(plan,extension['frames']),records); relation = plan['file_handoffs'][0]
        key = relation['target_unit'] if phase == 'B_COMPLETED' else relation['source_unit'] if phase == 'A_CAPTURED' else None
        if key and next(f for f in reversed(current) if f['run']['unit_key'] == key)['source_evidence']['file_artifacts']['state'] != 'CURRENT':
            phase = 'BLOCKED'
        from .work_coordinator_handoff_adjustments import qualifications
        result = {**deepcopy(ph), 'protocol': 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_CURRENT_V3',
            'task': {'id': plan['item_id'], 'revision': driver['item_revision']},
            'driver': driver, 'original_driver': original_driver, 'responsibility': chain,
            'full_current_tree_verified': True, 'authorization': self._auth, 'frames': extension['frames'],
            'current_frames': eligible, 'phase': phase, 'material_observation': ready['current_material_observation'],
            'delivery':[v for v,_ in records['work_coordinator_handoff_delivery']],
            'completion':[v for v,_ in records['work_coordinator_handoff_completion']],
            'units': [{'definition': u, 'qualification': q['qualification'], **PERMISSIONS}
                for u, q in zip(plan['units'], qualifications(plan, phase, eligible))],
            'application': self._application, 'registration': self._registration, 'child_plan': plan,
            'saved_path': ready['saved_path'], 'usage': self.s.work_units.usage(db, [f['run'] for f in extension['frames']]),
            'saved_edge_verified': True, 'full_saved_tree_verified': True,
            'preparation_namespace_verified': True, 'successor_runtime_open': True, 'public_apply': False, **PERMISSIONS}
        from .work_coordinator_handoff_post_round_v3_successor_audit import audit
        result.update(frontier=[self.old.frontier_entry(f) for f in extension['frames']],
            successor_record=self._successor, successor_receipts=extension['receipt_rows'],
            continuation_record=self._continuation,continuation_receipts=extension['continuation_receipts'],
            proposal_receipts=extension['proposal_receipts'])
        audit(self, db, plan, result)
        # Observe current control/execution/resource conditions independently
        # from the immutable historical save. No observation enables controls.
        item = self.s.work_items.item(db, plan['item_id'])
        live_plan, issues = self.s.work_drivers.readiness(db, item, driver, excluding={r['attempt_id'] for r in extension['runs']})
        if live_plan != plan:
            raise ValueError('V3完整current当前资源方案已变化。')
        if phase == 'BLOCKED':
            issues.append({'code': 'V3_MATERIAL_BLOCKED', 'message': '当前文件材料受阻，不能恢复V3责任。'})
        result['responsibility_conditions'] = issues
        return deepcopy(result)

    @closed
    def read(self, db, plan):
        if not db.in_transaction:
            raise ValueError('V3完整树须属于明确数据库事务。')
        with database_observation(db):
            plans = self._collect(db, plan)
            resources = [(p['id'], self._resources(db, p)) for p in plans]
            changes = db.total_changes
            first_owner = type(self)(self.s, _application=self._responsibility_application)
            final_owner = type(self)(self.s, _application=self._responsibility_application)
            first = first_owner._once(db, plan); again = final_owner._once(db, plan)
            def comparable(value):
                value = deepcopy(value); value['usage'].pop('elapsed_observed_seconds', None)
                return value
            final_plans = self._collect(db, plan)
            final_resources = [(p['id'], self._resources(db, p)) for p in plans]
            first_owner._tail(db, first); final_owner._tail(db, again)
            if (comparable(first) != comparable(again) or final_plans != plans or
                    final_resources != resources or db.total_changes != changes):
                raise ValueError('V3完整树双遍、实际祖先、资源或DB末尾变化。')
            # Keep only this completed invocation's owner for input/receipt helpers;
            # every subsequent read still creates two fresh complete owners.
            self.__dict__.update(final_owner.__dict__)
            return deepcopy(again)

    def activate_successor(self, db, plan, unit, request):
        if present(self.s, db, plan):
            return self.activate_continuation(db,plan,unit,request)
        from .work_coordinator_handoff_post_round_v3_successor_context import observe
        obs = observe(self.s, db, plan, unit)
        body = versioned({'protocol': c.CAPABILITY, 'scope': c.SCOPE, 'observation': obs,
            'request': deepcopy(request), 'request_hash': self.s.command_payload_hash, **PERMISSIONS})
        row = {'id': c.KIND + ':' + body['version'], 'version': body['version'], 'snapshot': body,
            'item_id': plan['item_id'], 'created': time.time(), 'command_id': self.s.command_identity}
        c.validate_capability(row)
        if not pending_request(self.s,db,row['command_id'],request):
            raise ValueError('V3首次开启须属于准确原请求的唯一命令作用域。')
        from .work_coordinator_handoff_post_round_materials import track
        track(self.s, obs['material_observation']); self.s.work_file_rework_applications.preserve(obs['material_observation'])
        self.s.collaboration.put(db, c.KIND, row); self.s.store.event(None, None, c.OPENED, row, db)
        bind_capability(self.s,db,self,row)
        self.read(db, plan)

    def activate_continuation(self,db,plan,unit,request):
        from . import work_coordinator_handoff_post_round_v3_continuation_contracts as cc
        from .work_coordinator_handoff_post_round_v3_continuation_context import observation
        from .work_coordinator_handoff_post_round_v3_command_scope import bind_continuation
        h=self.read(db,plan); self.admission(db,plan,h)
        if self._continuation is not None: return
        if len(h['successor_receipts'])!=1: raise ValueError('V3续行须先完整提交首次协调。')
        obs=observation(self,db,self._original,h['frames'],self._prefix,h['driver'],self._records(db,plan),
            unit,h['material_observation'],self._successor,h['successor_receipts'][0],request.get('proposal'),request,time.time())
        body=versioned({'protocol':cc.CAPABILITY,'scope':cc.SCOPE,'observation':obs,**PERMISSIONS})
        row={'id':cc.KIND+':'+body['version'],'version':body['version'],'snapshot':body,
            'item_id':plan['item_id'],'created':time.time(),'command_id':self.s.command_identity}
        cc.validate_capability(row)
        from .work_coordinator_handoff_post_round_materials import track
        track(self.s,obs['material_observation']); self.s.work_file_rework_applications.preserve(obs['material_observation'])
        self.s.collaboration.put(db,cc.KIND,row); self.s.store.event(None,None,cc.OPENED,row,db)
        bind_continuation(self.s,db,self,row)
        self.read(db,plan)

    def admission(self,db,plan,h):
        item=self.s.work_items.item(db,plan['item_id']); self.s.work_items.project(db,item['project_id'])
        reasons=[v['message'] for v in h['responsibility_conditions'] if v['code']!='V3_LIFECYCLE_CLOSED']
        if (self.s.teamwork.current(db,item['id'])!=plan or item['revision']!=h['driver']['item_revision'] or
                self.s.work_drivers.current(db,item['id'])!=h['driver'] or h['driver']['state']!='RESERVED' or
                h['driver']['instance']!=self.s.instance or reasons or h['usage']['unknown_requests'] or
                h['usage']['attempts']>=h['authorization']['max_attempts_total'] or time.time()>=h['authorization']['dispatch_before'] or
                self.s.work_drivers.execution_issues(db,item['id'],h['driver'])):
            raise ValueError('V3当前任务、实例、原授权/累计/截止或完整前沿受阻：'+'；'.join(reasons))
        self.s.work_flows.require_stopped(db,item['id'])
        material=h['material_observation']
        if self.s.work_file_rework_applications.observe(material['root'],material['paths'])!=material:
            raise ValueError('V3准入末尾当前材料字节已变化。')

    def _path_value(self, plan, frames, material, phase, driver):
        obs = self._successor['snapshot']['observation']
        if (plan != obs['plan_snapshot'] or driver != obs['driver'] or phase != obs['phase'] or
                material != obs['material_observation'] or [self.old.frontier_entry(f) for f in frames] != obs['frontier']):
            raise ValueError('V3首次输入仅接受原开启观察的准确前缀。')
        return c.path_for(obs, self._successor)

    def _input(self, plan, unit, task, driver, auth, frames, material, records, cap, proposal=None):
        obs = self._successor['snapshot']['observation']
        if (driver!=obs['driver'] or [self.old.frontier_entry(f) for f in frames]!=obs['frontier']):
            if self._continuation is None: raise ValueError('V3续行尚未建立独立能力。')
            from .work_coordinator_handoff_post_round_v3_continuation_context import path,input_value
            saved=path(self,self._db,self._original,frames,self._prefix,driver,records,material,self._successor,self._continuation)
            return input_value(self,plan,unit,task,driver,auth,frames,material,records,saved,proposal)
        if unit != obs['selected_unit']['definition'] or proposal is not None:
            raise ValueError('V3只开放原选定coordinate首次执行。')
        path = self._path_value(plan, frames, material, obs['phase'], driver)
        deps = WorkCoordinatorHandoffMixedRuntime._admit(self, plan, unit, frames, obs['phase'])
        if deps != obs['dependencies'] or task != obs['task'] or auth != obs['authorization']:
            raise ValueError('V3冻结输入的实际依赖、任务或授权已变化。')
        old = self.old._context(plan, task, driver, auth, self._eligible(plan, frames), material)
        context = versioned({'protocol': c.PROTOCOL, 'scope': c.SCOPE, 'root': obs['root'],
            'marker': plan['coordinator_file_handoff_root'], 'plan': plan_ref(plan), 'task': task,
            'driver': driver, 'authorization': auth, 'phase': obs['phase'], 'saved_path': path,
            'prior_frontier': obs['frontier'], 'material_observation': material,
            'selected_unit': obs['selected_unit'], 'work_requirements': plan['work_requirements'],
            'configuration': old['configuration'], 'units': [{**entry, 'qualification': q['qualification']}
                for entry, q in zip(old['units'], obs['qualifications'])], 'allowed_actions': c.ACTIONS, **PERMISSIONS})
        c.validate_context(context, self._successor)
        forecast = self._forecast(plan, context, frames, unit)
        forecast.update(scope=c.SCOPE, allowed_actions=c.ACTIONS, previous_round=deepcopy(plan['coordination_revision']))
        return deepcopy({'item_id': plan['item_id'], 'item_revision': task['revision'], 'plan_id': plan['id'],
            'plan_version': plan['version'], 'work_requirements': plan['work_requirements'], 'unit': unit,
            'dependencies': deps, 'check_evidence': [{'unit_key': d['unit_key'],
                'artifact': d['file_artifacts']['reference'], 'check_status': d['check_status']} for d in deps],
            'file_artifacts': [{'unit_key': d['unit_key'], **d['file_artifacts']} for d in deps
                if d['file_artifacts']['state'] == 'CURRENT'],
            'handoff_coordination_context': context, 'coordination_context': forecast})

    def _run_protocol(self, db, plan, run, context, boundary):
        from . import work_coordinator_handoff_post_round_v3_continuation_contracts as cc
        if context['protocol']==c.PROTOCOL:
            return boundary>self._opened and run['command_id']==self._successor['command_id']
        return (self._continuation is not None and boundary>self.checkpoints._event(db,cc.OPENED,self._continuation)['seq'] and
            context['protocol']==(cc.COORDINATION if run['input']['unit']['role']=='coordinate' else cc.UNIT))

    def _source_protocol(self,db,plan,source,consumer):
        from . import work_coordinator_handoff_post_round_v3_continuation_contracts as cc
        return source['plan_id']==plan['id'] and source['input'].get('handoff_coordination_context',{}).get('protocol') in {c.PROTOCOL,cc.COORDINATION}

    def _check_proposals(self,db,plan,h):
        expected=self._parent_reader._proposal_events(db,plan['item_id'])
        expected+= [(self.checkpoints._event(db,'coordinator_proposal_checked',v,None,
            v['snapshot']['source']['task_id'],v['snapshot']['source']['attempt_id'])['seq'],v)
            for v in self._responsibility['extension']['proposals']]
        if self._proposal_events(db,plan['item_id'])!=expected: raise ValueError('V3完整提案历史存在孤立或遗漏。')
        self._same(self._related(db,'work_proposal',plan['item_id']),[v for _,v in expected],'V3完整提案记录全集不符。')

    def _intent(self,plan,frozen,frames):
        from .work_coordinator_handoff_post_round_v3_continuation_context import intent
        return intent(self,plan,frozen,frames)

    deliver=WorkCoordinatorHandoffMixedRuntime.deliver
    complete=WorkCoordinatorHandoffMixedRuntime.complete
    target=WorkCoordinatorHandoffRuntime.target
    guard_capture=WorkCoordinatorHandoffRuntime.guard_capture
    finish_capture=WorkCoordinatorHandoffRuntime.finish_capture

    def _historical_input(self, db, run, *args):
        return self._input(*args)

    reservation = WorkCoordinatorHandoffRuntime.reservation
    input_for = WorkCoordinatorHandoffMixedRuntime.input_for
    _admit = WorkCoordinatorHandoffMixedRuntime._admit

    @staticmethod
    def prompt(frozen):
        from . import work_coordinator_handoff_post_round_v3_continuation_contracts as cc
        context=frozen.get('handoff_coordination_context') or frozen.get('handoff_unit_context')
        if context.get('protocol') in {cc.COORDINATION,cc.UNIT}:
            cc.validate_context(context)
            from .work_coordinator_handoff_runtime import UNIT_PROMPT
            return ('读取完整冻结协调输入，只返回候选的 dispatch_schema 或 pause_schema 对应严格 JSON；不写文件。\n输入快照 JSON：\n'
                if frozen['unit']['role']=='coordinate' else UNIT_PROMPT)+canonical(frozen)
        if frozen.get('handoff_coordination_context', {}).get('protocol') != c.PROTOCOL:
            raise ValueError('V3首次prompt只接受独立V9协调输入。')
        return '读取完整冻结协调输入，只返回候选的 dispatch_schema 或 pause_schema 对应严格 JSON；不写文件。\n输入快照 JSON：\n' + canonical(frozen)


def present(service, db, plan):
    # Routing/denial only; unknown wrappers must reach the full rejecting owner.
    from .work_coordinator_handoff_post_round_runtime import belongs
    from .work_coordinator_handoff_post_round_v3_commands import RECEIPT_KIND, RECEIPT_PROTOCOL, RECEIPT_EVENT
    protocols = {c.CAPABILITY, c.OBSERVATION, c.PROTOCOL, c.PATH, RECEIPT_PROTOCOL}
    from . import work_coordinator_handoff_post_round_v3_continuation_contracts as cc
    from . import work_coordinator_handoff_post_round_v3_continuation_receipts as cr
    from . import work_coordinator_handoff_post_round_v3_continuation_proposals as cp
    protocols.update({cc.CAPABILITY,cc.OBSERVATION,cc.PATH,cc.COORDINATION,cc.UNIT,cc.INTENT,cr.PROTOCOL,cp.PROTOCOL})
    def marked(v):
        if isinstance(v, dict):
            return (v.get('protocol') in protocols or any(isinstance(v.get(k), str) and
                v[k].startswith((c.KIND + ':', RECEIPT_KIND + ':',cc.KIND+':',cr.KIND+':',cp.KIND+':')) for k in ('id','version')) or any(marked(x) for x in v.values()))
        return isinstance(v, list) and any(marked(x) for x in v)
    for table, column in (('collaboration','data'), ('events','payload')):
        for row in db.execute('SELECT kind,' + column + ' FROM ' + table):
            value = json.loads(row[1])
            if (row[0] in (c.KIND, c.OPENED, RECEIPT_KIND, RECEIPT_EVENT,cc.KIND,cc.OPENED,cr.KIND,cr.EVENT,cp.KIND,cp.EVENT) or marked(value)) and belongs(
                    value, plan['item_id'], (plan['id'], plan['version'])):
                return True
    return False
