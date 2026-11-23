"""Reconstruct saved adjustment layers from actual records and event boundaries.

The original reader remains the original-root component. Each successor has
its own run/auth/driver scope; ordinal and deadline limits remain root-wide.
"""
from copy import deepcopy
import json
import time

from .store import canonical, digest
from .work_read_observation import database_observation
from .work_coordinator_handoff_adjustments import proposal_state
from .work_transitions import plan_ref
from .work_coordinator_handoff_roots import MARKER_FIELD
from .work_coordinator_handoff_context import TERMINAL
from .work_coordinator_handoff_runtime import WorkCoordinatorHandoffRuntime, READ, UNIT_PROMPT
from .work_coordinator_handoff_adjustment_runtime import (
    WorkCoordinatorHandoffAdjustmentRuntime, PROTOCOL, UNIT, ACTIONS, SCOPE)
from .work_coordinator_handoff_records import (
    PERMISSIONS, versioned, DELIVERY_KIND, COMPLETION_KIND)
from .work_coordinator_handoff_adjustment_records import (
    WorkCoordinatorHandoffAdjustmentRecords, CHECKPOINT, CANDIDATE, APPLICATION,
    CONSUMPTION, KIND_FOR, EVENTS, SAVED_PATH, PATH_SCOPE, HISTORY_FIELDS, validate_path)
from .work_coordinator_handoff_adjustment_contracts import (
    adjacent, successor_plan, successor_driver, transferred_authorization)
from .work_coordinator_handoff_adjustment_consumption import require_unconsumed


class WorkCoordinatorHandoffAdjustmentPath(WorkCoordinatorHandoffAdjustmentRuntime):
    def __init__(self, service):
        super().__init__(service)
        self.data = WorkCoordinatorHandoffAdjustmentRecords(service)
        self._edges = []

    def read(self, db, plan):
        with database_observation(db):
            return super().read(db, plan)

    def _related(self, db, kind, item_id):
        return [r for r in self.s.collaboration.all(db,kind) if
            r.get('item_id') == item_id or r.get('snapshot',{}).get('item_id') == item_id or
            r.get('plan_id') in getattr(self,'_plan_ids',set()) or
            r.get('snapshot',{}).get('root',{}).get('id') in getattr(self,'_plan_ids',set())]

    def resolve(self, db, plan, key, rows):
        h=self.read(db,plan)
        if rows!=self.s.work_units.rows(db,plan['item_id']): raise ValueError('后继来源前沿已变化。')
        if key not in {u['key'] for u in plan['units']}: raise ValueError('后继来源单元不存在。')
        retired=plan['file_handoffs'][0]['source_unit'] if h['phase']=='B_COMPLETED' else None
        frame=next((f for f in reversed(h['current_frames']) if f['run']['unit_key']==key),None)
        local=bool(frame and frame['run']['plan_id']==plan['id'])
        inherited=key in plan['result_lineage'] and not local
        problems=['历史来源不能作为当前结果或重新派发。'] if key==retired or key in plan['historical_coordinators'] else []
        return {'run':frame['run'] if frame else None,'evidence':frame['source_evidence'] if frame else None,
                'inherited':inherited,'source':plan['result_lineage'].get(key) if inherited else None,
                'consumer_plan':plan_ref(plan),'problems':problems}

    def _chain(self, db, plan):
        chain = []; seen = set()
        while plan.get('coordinator_file_handoff_lineage'):
            successor_plan(plan)
            if plan['id'] in seen or self.s.collaboration.get(db,'teamwork_plan',plan['id']) != plan:
                raise ValueError('保存路径包含重复或非实际方案。')
            seen.add(plan['id']); chain.append(plan)
            parent = self.s.collaboration.get(db,'teamwork_plan',plan['previous_plan'])
            if plan_ref(parent) != plan['previous_plan_reference']:
                raise ValueError('保存路径父方案版本不一致。')
            plan = parent
        if not chain: raise ValueError('后继读取必须有正式保存边。')
        if len(chain)>sum(u['role']=='coordinate' for u in plan['units']):
            raise ValueError('保存边数量超过原声明首次coordinate数量。')
        return plan,list(reversed(chain))

    def _edge(self, db, parent, child, prepared):
        from .work_coordinator_handoff_adjustment_apply import WorkCoordinatorHandoffAdjustmentApplications, SAVE_EVENT
        candidate = prepared['candidate']; checkpoint = prepared['checkpoint_record']
        h = prepared['history']; body = candidate['snapshot']
        applications = [r for r in self._related(db,KIND_FOR[APPLICATION],child['item_id'])
                        if r['snapshot']['child_plan'] == plan_ref(child)]
        consumptions = [r for r in self._related(db,KIND_FOR[CONSUMPTION],child['item_id'])
                        if r['snapshot']['to_plan'] == plan_ref(child)]
        if len(applications) != 1 or len(consumptions) != 1 or prepared['child_plan'] != child:
            raise ValueError('每个后继须有唯一准确实际应用与消费。')
        application = self.data.get(db,APPLICATION,self.ref(applications[0]))
        consumption = self.data.get(db,CONSUMPTION,self.ref(consumptions[0]))
        a = application['snapshot']
        after = self.old._driver(db,child,a['driver_after'])
        auth = self.old._auth(db,child,a['authorization_after'])
        adjacent(parent,child,candidate,h['authorization'],auth,h['driver'],after)
        payload = {'root':body['root'],'parent_plan':plan_ref(parent),'child_plan':plan_ref(child),
            'candidate':self.ref(candidate),'driver':self.s.work_drivers.reference(after),
            'authorization':self.s.work_units.auth_ref(auth),'command_id':candidate['command_id'],'no_dispatch':True}
        seq = self._event(db,SAVE_EVENT,payload)
        event = {'seq':seq,'kind':SAVE_EVENT,'payload_digest':digest(payload)}
        expected = WorkCoordinatorHandoffAdjustmentApplications(self.s)._bodies(
            candidate,checkpoint,child,h['driver'],after,h['authorization'],auth,event)
        app_seq = self._event(db,EVENTS[APPLICATION],application)
        consumption_seq = self._event(db,EVENTS[CONSUMPTION],consumption)
        if (a != expected[0] or consumption['snapshot'] != expected[1] or
                not self._event(db,EVENTS[CANDIDATE],candidate) < self._driver_event(db,after) < seq < app_seq < consumption_seq or
                any(r['command_id'] != candidate['command_id'] or r['item_id'] != child['item_id'] for r in (application,consumption)) or
                not candidate['created'] <= auth['transferred_at'] <= after['updated'] <= application['created'] <= consumption['created'] < auth['dispatch_before']):
            raise ValueError('后继应用、消费、授权或真实事件顺序不一致。')
        require_unconsumed(self.s,db,body['proposal'],child['item_id'],allowed=consumption)
        edge = {'parent_plan':plan_ref(parent),'child_plan':plan_ref(child),'checkpoint':self.ref(checkpoint),
                'candidate':self.ref(candidate),'application':self.ref(application),
                'consumption':self.ref(consumption),'save_event':event}
        return edge,after,auth,consumption_seq

    def _authorization_event(self, db, auth):
        matches = [e['save_event']['seq'] for e in self._edges
                   if e['child_plan'] == {'id':auth['plan_id'],'version':auth['plan_version']}]
        if len(matches) != 1: raise ValueError('后继授权缺少唯一实际转移保存边。')
        return matches[0]

    def capability(self, db, plan):
        return WorkCoordinatorHandoffRuntime.capability(self,db,
            self._root_plan if plan.get('coordinator_file_handoff_lineage') else plan)

    def _records(self, db, plan):
        return WorkCoordinatorHandoffRuntime._records(self,db,self._root_plan)

    def _intent(self, plan, frozen, frames):
        from .work_coordinator_handoff_successor_intent import SUCCESSOR_INTENT, SCOPE, validate_intent
        unit = frozen['unit']; context = frozen['handoff_unit_context']
        relation = plan['file_handoffs'][0]
        source = next(f for f in self._eligible(plan,frames) if f['run']['unit_key'] == relation['source_unit'])
        full = source['source_evidence']; baseline = context['material_observation']
        self.s.work_artifacts.require(full, 'checked' if source['run']['input']['unit']['checks'] or
            unit['dependency_gate'] == 'checked' else 'files')
        if baseline['missing'] or baseline['manifest'] != full['file_artifacts']['manifest']:
            raise ValueError('后继B写前基线不等于准确A全部当前字节。')
        return validate_intent(versioned({'protocol':SUCCESSOR_INTENT, 'item_id':plan['item_id'],
            'item_revision':context['task']['revision'], 'root':context['root'], 'marker':plan[MARKER_FIELD],
            'plan':plan_ref(plan), 'saved_path':context['saved_path'], 'relation':relation,
            'driver':context['driver'], 'authorization':context['authorization'],
            'source_run':source['run']['id'], 'source_evidence':full, 'target_revision':digest(unit),
            'protected_history':context['prior_frontier'], 'baseline':baseline,
            'input_template_version':digest(frozen), 'frontier':context['prior_frontier'], 'scope':SCOPE}))

    def deliver(self, db, plan, run):
        # These are root-owned ledger rows for a real child-bound run/intent.
        # The original builder does not interpret or project child authority.
        self._root_plan = self._chain(db,plan)[0]
        return super().deliver(db,self._root_plan,run)

    def complete(self, db, plan, run):
        self._root_plan = self._chain(db,plan)[0]
        return super().complete(db,self._root_plan,run)

    def input_for(self, db, item, plan, unit, rows):
        h=self.read(db,plan)
        if rows!=self.s.work_units.rows(db,item['id']) or h['task']!={'id':item['id'],'revision':item['revision']}:
            raise ValueError('后继任务或完整前沿已变化。')
        driver,auth=h['driver'],h['authorization']
        if driver['state']!='RESERVED' or len(rows)>=auth['max_attempts_total'] or time.time()>=auth['dispatch_before']:
            raise ValueError('后继责任暂停、原累计不足或最初截止已到。')
        frames=deepcopy(h['frames'])
        for f in frames: f['source_evidence']=self.s.work_units.evidence(db,f['run'])
        self._admit(plan,unit,frames,h['phase'])
        proposal=None
        if unit['role']!='coordinate':
            row=self.s.work_proposals.current(db,item['id'])
            if not row or any(r.get('proposal')==self.ref(row) for r in rows): raise ValueError('后继最新提案缺失或已消费。')
            proposal={'reference':self.ref(row),**{k:row['snapshot'][k] for k in ('source','decision')}}
            self._proposal(db,plan,unit,frames,proposal,h['task'],driver,auth)
        cap,_=self.capability(db,plan)
        frozen=self._input(plan,unit,h['task'],driver,auth,frames,h['material_observation'],self._records(db,plan),cap,proposal)
        if unit['key']==plan['file_handoffs'][0]['target_unit']:
            frozen['handoff_delivery_intent']=self._intent(plan,frozen,frames)
        return deepcopy(frozen)

    def _eligible(self, plan, frames):
        carried = plan['result_lineage']
        return [f for f in frames if f['run']['input']['unit']['role'] == 'coordinate' or
                f['run']['plan_id'] == plan['id'] or
                (f['run']['unit_key'] in carried and carried[f['run']['unit_key']]['run_id'] == f['run']['id'])]

    def _dependencies(self, plan, unit, frames, phase, pending=None):
        return super()._dependencies(plan,unit,self._eligible(plan,frames),phase,pending)

    def _admit(self, plan, unit, frames, phase, pending=None):
        if unit['key'] in plan['historical_coordinators']:
            raise ValueError('历史coordinate不得跨方案重新派发。')
        return super()._admit(plan,unit,self._eligible(plan,frames),phase,pending)

    def _path(self, plan, frames, observation, phase):
        from .work_coordinator_handoff_adjustments import qualifications
        value = versioned({'protocol':SAVED_PATH,'root':plan_ref(self._root_plan),
            'plan':plan_ref(plan),'capability':self.ref(self._adjustment),'edges':self._edges,
            'history_frontier':[self.old.frontier_entry(f) for f in frames],
            'current_qualifications':qualifications(plan,phase,self._eligible(plan,frames)),
            'material_observation':observation,'problems':[],**PERMISSIONS,'scope':PATH_SCOPE})
        return validate_path(value)

    def _read(self, db, plan):
        changes = db.total_changes
        current = deepcopy(self.s.teamwork.current(db,plan['item_id']))
        result = self._walk(db,plan)
        self._whole_scope(db,plan,result)
        for frame in result['frames']:
            if self.sources._saved_files(db,frame['run'])[0] != frame['files']:
                raise ValueError('后继最终重读全部历史快照已变化。')
        for frame in result['current_frames']:
            if self.s.work_units.evidence(db,frame['run']) != frame['source_evidence']:
                raise ValueError('后继最终当前来源或真实材料已变化。')
        if (db.total_changes != changes or current != plan or self.s.teamwork.current(db,plan['item_id']) != current or
                self.s.work_file_rework_applications.observe(self._root_plan[MARKER_FIELD]['project_root'],plan['file_handoffs'][0]['paths']) != result['material_observation']):
            raise ValueError('后继最终数据库、current或真实材料读取已变化。')
        return result

    def checkpoint(self, db, reference):
        with database_observation(db):
            return self._checkpoint(db, reference)

    def _checkpoint(self, db, reference):
        checkpoint = self.data.get(db,CHECKPOINT,reference)
        seq = self._event(db,EVENTS[CHECKPOINT],checkpoint)
        plan = checkpoint['snapshot']['plan']; before = db.total_changes
        current = deepcopy(self.s.teamwork.current(db,plan['item_id']))
        h = self._walk(db,plan,boundary=seq)
        current_observation=h['material_observation']
        rows = [f['run'] for f in h['frames']]
        proposal = self.s.collaboration.get(db,'work_proposal',checkpoint['snapshot']['proposal']['id'])
        checked = self._event(db,'coordinator_proposal_checked',proposal)
        previous = self._proposal_events(db,plan['item_id'],seq)
        if (not h['frames'] or h['frames'][-1]['run']['input']['unit']['role'] != 'coordinate' or
                not previous or previous[-1][1] != proposal or checked >= seq or
                self.ref(proposal) != checkpoint['snapshot']['proposal'] or proposal['version'] != digest(proposal['snapshot']) or
                not proposal_state(proposal['snapshot']) or proposal['snapshot']['plan'] != plan_ref(plan)):
            raise ValueError('后继检查点不是截止前最新真实V3调整提案。')
        observation = h['frames'][-1]['run']['input']['handoff_coordination_context']['material_observation']
        h['material_observation'] = observation
        from .work_coordinator_handoff_adjustments import rebuild_preview
        h['usage'] = self.s.work_units.usage(db,rows)
        preview = rebuild_preview(plan,h,h['frames'][-1]['run'],proposal['snapshot']['decision'],observed_at=proposal['created'])
        frozen = checkpoint['snapshot']
        if (preview != frozen['preview'] or proposal['snapshot']['adjustment'] != preview or
                proposal['snapshot']['source'] != preview['source'] or
                {k:h[k] for k in HISTORY_FIELDS} != frozen['history'] or h['usage'] != frozen['usage'] or
                frozen['original_dispatch_before'] != h['authorization']['dispatch_before'] or
                frozen['authorization_from'] != self.s.work_units.auth_ref(h['authorization']) or
                not h['frames'][-1]['run']['created'] <= proposal['created'] <= checkpoint['created'] < frozen['original_dispatch_before']):
            raise ValueError('后继检查点不等于全层真实来源和完整预览重建。')
        for frame in h['frames']:
            run = frame['run']; attempt = self.s.store.attempt(run['attempt_id'],db)
            exits = [r['seq'] for r in db.execute("SELECT seq,payload FROM events WHERE task_id=? AND attempt_id=? AND kind='attempt_result_saved'",(run['task_id'],run['attempt_id'])) if json.loads(r['payload']) == attempt['result']]
            if len(exits) != 1 or exits[0] >= seq or any(e['seq'] >= seq for e in self.sources._capture_events(db,run,frame['files'])):
                raise ValueError('后继检查点前全层退出或快照边界无效。')
        observed = self.s.work_file_rework_applications.observe(self._root_plan[MARKER_FIELD]['project_root'],plan['file_handoffs'][0]['paths'])
        if before != db.total_changes or self.s.teamwork.current(db,plan['item_id']) != current or observed!=current_observation:
            raise ValueError('后继检查点只读期间发生变化。')
        return deepcopy({'history':{k:h[k] for k in HISTORY_FIELDS},'frames':h['frames'],
            'checkpoint':reference,'checkpoint_event_seq':seq,'current_material_observation':observed,
            'saved_edge_verified':False,'successor_layers_verified':True,'preview_semantics_verified':True,'public_apply':False,**PERMISSIONS})

    def _walk(self, db, plan, boundary=None):
        from .work_coordinator_handoff_adjustment_sources import WorkCoordinatorHandoffAdjustmentSources
        root,chain = self._chain(db,plan); self._root_plan = root
        self._plan_ids={p['id'] for p in [root]+chain}
        original = WorkCoordinatorHandoffAdjustmentSources(self.s)
        first = original.read_prepared(db,chain[0]['coordinator_file_handoff_lineage']['candidate'])
        self._adjustment,self._opened = original.adjustment_capability(db,root)
        cap,activated = original.capability(db,root)
        frames = first['frames']; self._edges = []; parent = root
        self._all_drivers = []; self._all_auths = []; self._prepared_layers = []
        for index,child in enumerate(chain):
            prepared = first if index == 0 else original.read_prepared(db,child['coordinator_file_handoff_lineage']['candidate'])
            edge,start,auth,start_seq = self._edge(db,parent,child,prepared)
            self._edges.append(edge); self._prepared_layers.append(prepared)
            limit = boundary
            if index+1 < len(chain):
                next_candidate = self.data.get(db,CANDIDATE,chain[index+1]['coordinator_file_handoff_lineage']['candidate'])
                cp = self.data.get(db,CHECKPOINT,next_candidate['snapshot']['checkpoint'])
                limit = self._event(db,EVENTS[CHECKPOINT],cp)
            revisions = self._related(db,'work_driver_revision',plan['item_id'])
            values = []
            for row in revisions:
                if row['plan_id'] != child['id']: continue
                value = self.old._driver(db,child,{'id':row['driver_id'],'generation':row['generation'],'revision':row['revision']})
                successor_driver(value); seq = self._driver_event(db,value)
                if limit is not None and seq >= limit:
                    raise ValueError('已冻结后继检查点后责任还有未核版本。')
                if seq < self._driver_event(db,start): raise ValueError('后继责任早于正式转移。')
                values.append((seq,value,row))
            values.sort(key=lambda v:v[0]); driver = values[-1][1] if values else None
            if not driver or values[0][1] != start: raise ValueError('后继缺少准确起始暂停责任。')
            self._driver_transitions(db,values)
            for _,value,_ in values:
                allowed = {'revision','updated','state','reason','instance','item_revision','task_ids','last_unit_run','revision_lineage','released'}
                if {k:v for k,v in value.items() if k not in allowed} != {k:v for k,v in start.items() if k not in allowed}:
                    raise ValueError('后继责任改变原绑定或generation。')
            transferred_authorization(auth)
            layer_rows = [r for r in self.s.work_units.rows(db,plan['item_id']) if r['plan_id'] == child['id']]
            if any(self.sources._reservation_event(db,r) <= start_seq or (limit is not None and self.sources._reservation_event(db,r) >= limit) for r in layer_rows):
                raise ValueError('后继运行不在准确保存与检查点边界内。')
            records = original._records_before(db,root,limit if limit is not None else float('inf'))
            self._inherited = deepcopy(frames)
            frames,revision,seen = self._layer_runs(db,child,prepared['history']['task'],root[MARKER_FIELD]['project_root'],cap,activated,driver,auth,[r for _,_,r in values],layer_rows,records)
            if revision != driver['item_revision']: raise ValueError('后继责任任务修订脱离完整运行绑定。')
            self._all_drivers.extend(v for _,v,_ in values); self._all_auths.append(auth)
            parent = child
        self._match_root_records(db,root,frames,records)
        observation = self.s.work_file_rework_applications.observe(root[MARKER_FIELD]['project_root'],plan['file_handoffs'][0]['paths'])
        phase = self._phase(plan,frames,records)
        current_frames = deepcopy(frames)
        if boundary is None:
            for f in current_frames: f['source_evidence'] = self.s.work_units.evidence(db,f['run'])
            expected = plan['file_handoffs'][0]['target_unit'] if phase == 'B_COMPLETED' else plan['file_handoffs'][0]['source_unit'] if phase == 'A_CAPTURED' else None
            if expected and next(f for f in reversed(current_frames) if f['run']['unit_key']==expected)['source_evidence']['file_artifacts']['state'] != 'CURRENT': phase='BLOCKED'
        current_frames = self._eligible(plan,current_frames)
        from .work_coordinator_handoff_adjustments import qualifications
        h = {'protocol':READ,'root':plan_ref(root),'marker':root[MARKER_FIELD],'capability':self.ref(cap),
            'adjustment_capability':self.ref(self._adjustment),'task':{'id':plan['item_id'],'revision':revision},
            'driver':driver,'authorization':auth,'frames':frames,'current_frames':current_frames,'phase':phase,
            'frontier':[self.old.frontier_entry(f) for f in frames],'material_observation':observation,
            'delivery':[r for r,_ in records[DELIVERY_KIND]],'completion':[r for r,_ in records[COMPLETION_KIND]],
            'units':[{'definition':u,'qualification':q['qualification'],**PERMISSIONS} for u,q in zip(plan['units'],qualifications(plan,phase,current_frames))],**PERMISSIONS}
        h['saved_path'] = self._path(plan,frames,observation,phase)
        return deepcopy(h)

    def _match_root_records(self, db, root, frames, records):
        # The protected A/B definitions never change. Their original root
        # records retain the original identity, even when B runs after a save.
        WorkCoordinatorHandoffRuntime._match_records(self,db,root,frames,records)

    def _proposal_events(self, db, item_id, boundary=None):
        return [(r['seq'],json.loads(r['payload'])) for r in db.execute("SELECT seq,payload FROM events WHERE kind='coordinator_proposal_checked' ORDER BY seq") if
            (boundary is None or r['seq']<boundary) and json.loads(r['payload']).get('item_id')==item_id]

    def _driver_transitions(self, db, values):
        for (previous_seq,previous,_),(seq,value,_) in zip(values,values[1:]):
            kind=db.execute('SELECT kind FROM events WHERE seq=?',(seq,)).fetchone()['kind']
            expected=deepcopy(previous)
            if kind=='work_driver_execution_linked':
                run=self.s.collaboration.get(db,'work_unit_run',value['last_unit_run'])
                if (previous['state']!='RESERVED' or run['driver_revision']!=previous['revision'] or
                        not previous_seq<self.sources._reservation_event(db,run)<seq or run['item_revision']!=previous['item_revision']):
                    raise ValueError('后继任务绑定不是原责任实际保留后的准确事件。')
                expected.update(item_revision=value['item_revision'],task_ids=sorted(set(previous['task_ids'])|{run['task_id']}),
                    last_unit_run=run['id'],reason='OWNED_EXECUTION_LINKED',
                    revision_lineage=[*previous.get('revision_lineage',[]),{'from':run['item_revision'],'to':value['item_revision'],'unit_run_id':run['id']}][-100:])
            elif kind=='work_driver_resume':
                if previous['state']!='PAUSED': raise ValueError('后继明确恢复前未暂停。')
                expected.update(state='RESERVED',reason='EXPLICIT_RECHECK',instance=value['instance'])
            elif kind in {'work_driver_pause','work_driver_restart_paused'}:
                if previous['state']!='RESERVED': raise ValueError('后继暂停前未持有可用责任。')
                expected.update(state='PAUSED',reason='EXPLICIT_PAUSE' if kind=='work_driver_pause' else 'CONTROLLER_RESTART')
            elif kind=='work_driver_release':
                if previous['state'] not in {'RESERVED','PAUSED'}: raise ValueError('后继释放前未持有责任。')
                expected.update(state='RELEASED',reason='EXPLICIT_RELEASE',released=value['released'])
            else:
                raise ValueError('后继有未登记的责任变化事件。')
            expected.update(revision=value['revision'],updated=value['updated'])
            if expected!=value or value['updated']<previous['updated']:
                raise ValueError('后继全部不可变责任版本不等于真实事件变换。')

    def _whole_scope(self, db, plan, h):
        root,chain = self._chain(db,plan); item_id=plan['item_id']
        if (sorted(map(digest,self._related(db,'teamwork_plan',item_id))) != sorted(map(digest,[root]+chain)) or
                sorted(map(digest,self._related(db,'work_unit_run',item_id))) != sorted(digest(f['run']) for f in h['frames']) or
                self.s.work_items.item(db,item_id)['revision'] != h['task']['revision'] or
                self.s.work_drivers.current(db,item_id) != h['driver'] or
                self.s.work_units.authorization(db,h['driver']) != h['authorization']):
            raise ValueError('全层当前方案、实际运行、任务、责任或授权不一致。')
        original = self._prepared_layers[0]['history']['authorization']
        expected_auths = [{**a,'id':'unit-auth-version:'+a['revision']} for a in [original]+self._all_auths]
        if (sorted(map(digest,self._related(db,'work_unit_authorization_revision',item_id))) != sorted(map(digest,expected_auths)) or
                self._related(db,'work_unit_authorization',item_id) != [h['authorization']] or
                self._related(db,'work_driver',item_id) != [h['driver']]):
            raise ValueError('全层授权版本或当前责任有遗漏。')
        for protocol,field in ((APPLICATION,'application'),(CONSUMPTION,'consumption')):
            rows=self._related(db,KIND_FOR[protocol],item_id)
            if sorted(self.ref(r)['id'] for r in rows) != sorted(e[field]['id'] for e in self._edges):
                raise ValueError('全层实际应用或消费包含未验证记录。')
            events=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM events WHERE kind=? ORDER BY seq',(EVENTS[protocol],)) if json.loads(r['payload']).get('item_id')==item_id]
            if sorted(map(digest,events))!=sorted(map(digest,rows)): raise ValueError('应用或消费事件有未核版本。')
        for kind,name,event in ((DELIVERY_KIND,'delivery','coordinator_handoff_delivered'),(COMPLETION_KIND,'completion','coordinator_handoff_completed')):
            actual=self._related(db,kind,item_id)
            events=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM events WHERE kind=? ORDER BY seq',(event,)) if json.loads(r['payload']).get('item_id')==item_id]
            if sorted(map(digest,actual))!=sorted(map(digest,h[name])) or sorted(map(digest,events))!=sorted(map(digest,actual)):
                raise ValueError('完整交付完成记录或事件包含遗漏。')
        allowed_drivers = [r for r in self._related(db,'work_driver_revision',item_id) if r['plan_id']==root['id']]
        before = self._prepared_layers[0]['checkpoint_event_seq']
        for r in allowed_drivers:
            value=self.old._driver(db,root,{'id':r['driver_id'],'generation':r['generation'],'revision':r['revision']})
            if self._driver_event(db,value)>=before: raise ValueError('原根检查点后责任历史改变。')
        all_values=[{**{k:v for k,v in r.items() if k!='driver_id'},'id':r['driver_id']} for r in allowed_drivers]+self._all_drivers
        if len(all_values)!=len(self._related(db,'work_driver_revision',item_id)):
            raise ValueError('存在未重建责任层。')
        events=[json.loads(r['payload']) for r in db.execute("SELECT payload FROM events WHERE kind LIKE 'work_driver_%'") if json.loads(r['payload']).get('item_id')==item_id]
        if sorted(map(digest,events))!=sorted(digest({**v,'no_dispatch':True}) for v in all_values): raise ValueError('责任事件存在遗漏或孤立版本。')
        for kind,expected in [('cooperation_authorized',[original]),('coordinator_handoff_adjustment_plan_saved',[e['save_event'] for e in self._edges])]:
            actual=[(r['seq'],json.loads(r['payload'])) for r in db.execute('SELECT seq,payload FROM events WHERE kind=?',(kind,)) if json.loads(r['payload']).get('item_id')==item_id or json.loads(r['payload']).get('root',{}).get('id')==root['id']]
            if kind=='cooperation_authorized':
                if [v for _,v in actual]!=expected: raise ValueError('原授权事件存在额外版本。')
            elif [{'seq':seq,'kind':kind,'payload_digest':digest(v)} for seq,v in actual]!=expected: raise ValueError('保存事件存在遗漏或额外边。')
        h['usage']=self.s.work_units.usage(db,[f['run'] for f in h['frames']])
        if h['usage']['attempts']>original['max_attempts_total'] or h['authorization']['dispatch_before']!=original['dispatch_before']:
            raise ValueError('全层累计或最初截止增长。')
        self._check_proposals(db,plan,h)

    def _check_proposals(self, db, plan, h):
        from .work_proposals import parse
        from .work_coordinator_handoff_adjustment_records import SOURCE_FIELDS
        rows=self._related(db,'work_proposal',plan['item_id'])
        events=self._proposal_events(db,plan['item_id'])
        if sorted(map(digest,rows))!=sorted(digest(v) for _,v in events):
            raise ValueError('全部提案记录或实际事件存在遗漏、重复或孤立版本。')
        if events and self.s.work_proposals.current(db,plan['item_id'])!=events[-1][1]:
            raise ValueError('当前提案指针不是实际最新记录。')
        _,activated=self.capability(db,self._root_plan)
        for seq,row in events:
            body=row['snapshot']
            if (set(row)!={'id','version','snapshot','item_id','created','command_id'} or row['version']!=digest(body)):
                raise ValueError('提案完整wrapper或内容身份无效。')
            source=next((f for f in h['frames'] if f['run']['id']==body['source']['run_id']),None)
            if not source or source['run']['input']['unit']['role']!='coordinate': raise ValueError('提案缺少实际协调运行来源。')
            run=source['run']; source_plan=self.s.collaboration.get(db,'teamwork_plan',run['plan_id'])
            driver=self.old._driver(db,source_plan,body['driver']); auth=self.old._auth(db,source_plan,body['authorization'])
            known=[f for f in h['frames'] if self.sources._reservation_event(db,f['run'])<seq]
            source_full=source['source_evidence']; frozen=run['input']
            if (body['plan']!=plan_ref(source_plan) or body['item_revision']!=driver['item_revision'] or
                    self._driver_event(db,driver)>=seq or self.sources._reservation_event(db,run)>=seq or
                    body['source']!={k:source_full[k] for k in SOURCE_FIELDS} or
                    body['answer_digest']!=digest(source_full['answer']) or
                    body['answer']!=(source_full['answer'] if len(source_full['answer'])<=32000 else None) or
                    body['handoff_context']!=frozen['coordination_context']['handoff_context'] or
                    body['frontier']!=digest([{'run_id':f['run']['id'],'evidence':f['evidence' if seq>activated else 'source_evidence']['version']} for f in known]) or
                    not known or [f for f in known if f['run']['input']['unit']['role']=='coordinate'][-1]['run']['id']!=run['id'] or
                    driver['generation']!=run['generation'] or auth['driver_generation']!=driver['generation']):
                raise ValueError('提案实际来源、完整前沿、原回复或责任授权不一致。')
            if body['state']=='INVALID': continue
            decision=parse(source_full['answer'])
            if decision!=body['decision']: raise ValueError('提案决定不是原回复。')
            action=decision['action']
            if action=='dispatch':
                candidate=next((v for v in frozen['coordination_context']['candidates'] if v['unit']['key']==decision['unit_key']),None)
                if (body['state']!='VALID' or not candidate or not candidate['ready_after_proposal'] or
                        decision['retry_of'] is not None or body['target']!={'key':candidate['unit']['key'],'revision':digest(candidate['unit'])}):
                    raise ValueError('派发提案不是冻结的准确可达候选。')
            elif action=='pause':
                if body['state']!='PAUSE_SUGGESTED' or body['target'] is not None: raise ValueError('暂停提案身份无效。')
            elif action=='adjust_dependencies':
                from .work_coordinator_handoff_adjustments import proposal_state
                if not proposal_state(body) or body['target'] is not None:
                    raise ValueError('调整提案身份无效。')
                # Applied previews are independently rebuilt by each actual
                # checkpoint. Unconsumed previews stay data, never a permit.
            else:
                raise ValueError('原协调输入未开放该提案操作。')

    def _layer_runs(self, db, plan, item, root, cap, activated, driver, auth, revisions, rows, records):
        """Rebuild one successor layer with the verified full inherited frontier."""
        frames, seen, revision, before_activation = deepcopy(self._inherited), set(), plan['item_revision'], []
        for run in rows:
            unit = next((u for u in plan['units'] if u['key'] == run['unit_key']), None)
            task = self.s.store.task(run['task_id'], db); attempt = self.s.store.attempt(run['attempt_id'], db)
            raw = self.s.work_units.base_evidence(db, run)
            old_driver = self.old._driver(db, plan, {'id': driver['id'], 'generation': run['generation'], 'revision': run['driver_revision']})
            old_auth = self.old._auth(db, plan, run['authorization'])
            boundary = self.sources._reservation_event(db, run)
            if self._driver_event(db, old_driver) >= boundary or self._authorization_event(db,old_auth) >= boundary:
                raise ValueError('原责任或授权晚于实际保留边界。')
            prior = [self.sources._frame_before(db, f['run'], run) for f in frames]
            earlier_records = {k: [(r, seq) for r, seq in v if seq < boundary] for k, v in records.items()}
            if (unit is None or unit['key'] in seen or run['retry_of'] is not None or run['plan_id'] != plan['id'] or
                    run['plan_version'] != plan['version'] or run['item_id'] != item['id'] or run['item_revision'] != revision or
                    run['ordinal_total'] != len(frames) + 1 or run['ordinal_total'] > auth['max_attempts_total'] or
                    run['created'] >= auth['dispatch_before'] or old_auth != auth or old_driver['state'] != 'RESERVED' or
                    old_driver['item_revision'] != revision or old_driver['generation'] != auth['driver_generation'] or
                    run['driver_id'] != driver['id'] or run['unit_revision'] != digest(unit) or
                    run['input_version'] != digest(run['input']) or not raw['exact_identity'] or
                    run['scheduling'] != 'EXPLICIT_SERIAL_STEP' or run['flow'] is not None or
                    attempt['workspace'] != root or run['task_spec_version'] != digest(task['spec'])):
                raise ValueError('阶段运行原run/spec/input/责任/授权/累计身份不一致。')
            frozen = run['input']; context = frozen.get('handoff_coordination_context') if unit['role'] == 'coordinate' else frozen.get('handoff_unit_context')
            if not context:
                raise ValueError('阶段运行缺少原协议冻结输入。')
            observation = context['material_observation']
            from .work_coordinator_handoff_records import observation as validate_observation
            validate_observation(observation)
            if observation['root'] != root or observation['paths'] != sorted(plan['file_handoffs'][0]['paths']):
                raise ValueError('冻结观察不是原目录全部关系文件。')
            self.s.work_file_rework_applications.require_baseline(observation)
            original_task = {'id': item['id'], 'revision': revision}
            legacy = context['protocol'] == 'COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V1'
            if legacy:
                if boundary > activated:
                    raise ValueError('新能力激活后不能新增旧V1运行。')
                self.old._coordinator(unit)
                old_prior = [{**f, 'evidence': f['source_evidence']} for f in prior]
                expected = self.old._input(plan, unit, self.old._context(plan, original_task, old_driver, old_auth, old_prior, observation))
                from .work_coordinator_handoff_context import PROMPT
                prompt = PROMPT + canonical(expected)
            else:
                if boundary <= activated or not self._run_protocol(db, plan, run, context, boundary):
                    raise ValueError('阶段运行协议或激活边界不符。')
                proposal = frozen.get('coordination_proposal')
                if unit['role'] != 'coordinate':
                    row = self._proposal(db, plan, unit, prior, proposal, original_task, old_driver, old_auth, run)
                    consumed = [r for r in rows if r.get('proposal') == self.ref(row)]
                    if consumed != [run]:
                        raise ValueError('普通提案没有唯一准确消费。')
                expected = self._historical_input(db, run, plan, unit, original_task, old_driver, old_auth, prior, observation, earlier_records, cap, proposal)
                if unit['key'] == plan['file_handoffs'][0]['target_unit']:
                    expected['handoff_delivery_intent'] = self._intent(plan, expected, prior)
                prompt = self.prompt(expected)
            if frozen != expected:
                raise ValueError('冻结输入不等于按实际边界完整原历史重建。')
            spec = task['spec']; selected = self.s.resources.selection(db, unit['member']['reference']['member_id'])
            binding = {k: selected['connection'][k] for k in ('id', 'provider', 'credential_reference')}
            meta = {'item_id': item['id'], 'source_item_revision': revision, 'driver_id': old_driver['id'],
                'generation': old_driver['generation'], 'driver_revision': old_driver['revision'], 'plan_id': plan['id'],
                'plan_version': plan['version'], 'unit_key': unit['key'], 'unit_revision': digest(unit),
                'input_version': digest(frozen), 'authorization': self.s.work_units.auth_ref(old_auth)}
            if frozen.get('coordination_proposal'):
                meta['proposal'] = frozen['coordination_proposal']['reference']
            from .native_permissions import choice
            resource = spec.get('execution_resource')
            resource_events = list(db.execute(
                "SELECT seq,payload FROM events WHERE task_id=? AND attempt_id=? AND kind='execution_resource_frozen'",
                (run['task_id'], run['attempt_id'])))
            if (not isinstance(resource, dict) or resource['reference'] != unit['member']['reference'] or
                    {k: resource['connection'][k] for k in binding} != binding or
                    any(resource['member'][k] != unit['member'][k] for k in ('name', 'role', 'provider', 'model', 'effort', 'tier')) or
                    resource['quota_source']['id'] != unit['member']['reference']['quota_id'] or
                    resource['quota_source']['revision'] != unit['member']['reference']['quota_revision'] or
                    len(resource_events) != 1 or json.loads(resource_events[0]['payload']) != resource or
                    resource_events[0]['seq'] >= boundary):
                raise ValueError('原实际资源冻结、准确成员/连接/额度引用或事件不一致。')
            from .attachments import normalize, freeze
            attachments = normalize([{'file': n, 'route': 'native'} for n in self.materials(frozen)])
            freeze(attachments, self.s.locations.data / 'tasks' / run['task_id'] / 'input', self.materials(frozen))
            for attachment in attachments:
                attachment['delivery'] = 'NATIVE_IMAGE_INPUT' if attachment['kind'] == 'image' and spec['provider'] != 'qwen' else 'NATIVE_FILE_TOOLS'
            if (spec['goal'] != prompt or spec['materials'] != self.materials(frozen) or
                    spec['attachments'] != attachments or
                    spec['writable'] or spec['checks'] or spec['work_mode'] != 'cooperative' or
                    spec['native'] != {'configuration': 'VENDOR_DEFAULTS'} or spec['access'] != choice(None) or
                    spec['execution_connection'] != binding or any(spec[k] != unit['member'][k] for k in ('provider', 'model', 'effort', 'tier')) or
                    run['cooperative'] != meta or any(run.get(k) != v for k, v in meta.items())):
                raise ValueError('原实际spec、附件、原生权限或完整cooperative绑定不符。')
            links = list({digest(l): l for r in revisions for l in r.get('revision_lineage', []) if l['unit_run_id'] == run['id']}.values())
            if links:
                if len(links) != 1 or links[0]['from'] != revision:
                    raise ValueError('原任务绑定历史不一致。')
                revision = links[0]['to']
            elif not (attempt['state'] == 'RESERVED' and run['command_id'] == self.s.command_identity):
                raise ValueError('运行缺少准确任务绑定历史。')
            files, current = self.sources._saved_files(db, run)
            if raw['state'] == 'COMPLETED':
                exits = [r['seq'] for r in db.execute(
                    "SELECT seq,payload FROM events WHERE task_id=? AND attempt_id=? AND kind='attempt_result_saved'",
                    (run['task_id'], run['attempt_id'])) if json.loads(r['payload']) == attempt['result']]
                captures = self.sources._capture_events(db, run, files)
                if len(exits) != 1 or exits[0] <= boundary or any(e['seq'] <= exits[0] for e in captures):
                    raise ValueError('准确完成来源缺少保留后的唯一实际退出，或捕获早于退出。')
            source = self.sources._historical_evidence(db, run, self.s.work_artifacts.reference(current))
            if unit['role'] == 'coordinate' and files['snapshots']:
                raise ValueError('coordinate不能伪造文件产物。')
            frame = {'run': run, 'task_spec': spec, 'workspace': root, 'evidence': raw,
                     'source_evidence': source['full'], 'files': files}
            frames.append(frame); seen.add(unit['key'])
            if boundary < activated:
                before_activation.append(self.old.frontier_entry(frame))
        if before_activation:
            raise ValueError('能力激活前沿不等于准确原V1历史。')
        return frames, revision, seen


    def _input(self, plan, unit, task, driver, auth, frames, observation, records, cap, proposal=None):
        phase = self._phase(plan, frames, records)
        dependencies = self._admit(plan, unit, frames, phase)
        value = {'item_id': plan['item_id'], 'item_revision': task['revision'],
            'plan_id': plan['id'], 'plan_version': plan['version'], 'work_requirements': plan['work_requirements'],
            'unit': unit, 'dependencies': dependencies,
            'check_evidence': [{'unit_key': d['unit_key'], 'artifact': d['file_artifacts']['reference'],
                                'check_status': d['check_status']} for d in dependencies],
            'file_artifacts': [{'unit_key': d['unit_key'], **d['file_artifacts']} for d in dependencies
                               if d['file_artifacts']['state'] == 'CURRENT']}
        frontier = [self.old.frontier_entry(f) for f in frames]
        if unit['role'] == 'coordinate':
            context = self.old._context(plan, task, driver, auth, frames, observation)
            relation = plan['file_handoffs'][0]
            for entry in context['units']:
                key = entry['definition']['key']
                entry['qualification'] = ('HISTORICAL_RETIRED' if phase == 'B_COMPLETED' and key == relation['source_unit']
                    else 'CURRENT_COMPLETED' if phase == 'B_COMPLETED' and key == relation['target_unit'] else
                    'PROTECTED_HISTORY' if phase in {'B_IN_FLIGHT', 'B_EXITED_UNCAPTURED'} and key == relation['source_unit']
                    else 'RECORDED' if entry['source_run'] else 'NOT_RUN')
            context.update(root=plan_ref(self._root_plan), protocol=PROTOCOL, capability=self.ref(cap), phase=phase,
                           prior_frontier=frontier, allowed_actions=['dispatch', 'pause'],
                           scope='FROZEN_STAGE_HISTORY; NO_MATERIAL_OR_EXECUTION_PERMISSION')
            context = versioned({k: v for k, v in context.items() if k != 'version'})
            value.update(handoff_coordination_context=context,
                         coordination_context=self._forecast(plan, context, frames, unit))
        else:
            if proposal is None:
                raise ValueError('普通单元缺少准确V2提案。')
            context = versioned({'protocol': UNIT, 'root': plan_ref(self._root_plan), 'marker': plan[MARKER_FIELD],
                'task': task, 'driver': driver, 'authorization': auth, 'unit_revision': digest(unit),
                'proposal': proposal, 'phase': phase, 'prior_frontier': frontier,
                'material_observation': observation, **PERMISSIONS,
                'scope': 'FROZEN_FIRST_ORDINARY_INPUT; SOURCE_IS_EXACT_V2_COORDINATOR'})
            # Successor input is checked by the independent closed codec below.
            value.update(handoff_unit_context=context, coordination_proposal=proposal)
        field = 'handoff_coordination_context' if unit['role']=='coordinate' else 'handoff_unit_context'
        context = {k:v for k,v in value[field].items() if k!='version'}
        context.update(protocol=PROTOCOL if unit['role']=='coordinate' else UNIT,
            root=plan_ref(self._root_plan),plan=plan_ref(plan),saved_path=self._path(plan,frames,observation,phase),
            adjustment_capability=self.ref(self._adjustment),scope=SCOPE)
        if unit['role']=='coordinate': context['allowed_actions']=ACTIONS
        context=versioned(context); value[field]=context
        if unit['role']=='coordinate':
            from .work_coordinator_handoff_adjustments import input_context
            value['coordination_context'].update(allowed_actions=ACTIONS,scope=SCOPE,
                handoff_context={k:context[k] for k in ('protocol','version','root')},
                adjustment_context=input_context(plan,context,self._eligible(plan,frames)),
                adjustment_prior_frontier=context['prior_frontier'])
        else:
            from .work_coordinator_handoff_adjustment_runtime import validate_unit
            validate_unit(context)
        return value
