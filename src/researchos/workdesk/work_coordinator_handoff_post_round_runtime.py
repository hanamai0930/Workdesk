"""Actual event-bounded post-ROUND capability and V6/ordinary V5 runtime.

Old layers use their original builders. The new reader adds the complete
activation namespace and reconstructs its admission from the actual prefix.
"""
from copy import deepcopy
import json
import time

from .store import canonical, digest
from .work_transitions import plan_ref, source_ref
from .work_coordinator_handoff_roots import CONFIG_FIELDS
from .work_coordinator_handoff_records import versioned, PERMISSIONS
from .work_coordinator_handoff_post_round_context import (
    WorkCoordinatorHandoffPostRoundContext, validate_observation, observation_ref, OBSERVATION,
    SCOPE as OBS_SCOPE)
from .work_coordinator_handoff_post_round_contracts import (
    KIND, OPENED, CAPABILITY, PROTOCOL, UNIT, PATH, SCOPE, ACTIONS,
    validate_capability, validate_context, validate_path)
from .work_coordinator_handoff_round_runtime import WorkCoordinatorHandoffRoundRuntime
from .work_coordinator_handoff_mixed_runtime import WorkCoordinatorHandoffMixedRuntime


def belongs(value, item, identities=()):
    pending=[value]; identities=set(identities)
    while pending:
        node=pending.pop()
        if isinstance(node,dict):
            if node.get('item_id')==item: return True
            if any(isinstance(node.get(k),str) and node[k] in identities for k in
                    ('id','plan_id','plan_version','run_id','task_id','attempt_id','version','revision')): return True
            pending.extend(node.values())
        elif isinstance(node,list): pending.extend(node)
    return False


def enabled(service, db, plan):
    if not plan: return False
    identities=(plan['id'],plan['version'])
    for r in db.execute('SELECT kind,data FROM collaboration'):
        value=json.loads(r[1])
        if (r[0]==KIND or value.get('snapshot',{}).get('protocol')==CAPABILITY) and belongs(value,plan['item_id'],identities):
            return True
    for r in db.execute('SELECT kind,payload FROM events'):
        value=json.loads(r[1])
        if (r[0]==OPENED or value.get('snapshot',{}).get('protocol')==CAPABILITY) and belongs(value,plan['item_id'],identities):
            return True
    return False


def runtime(service, plan):
    mixed=plan['coordinator_file_handoff_round_lineage'].get('protocol')=='COORDINATOR_FILE_HANDOFF_MIXED_ROUND_LINEAGE_V1'
    return (PostRoundMixedRuntime if mixed else PostRoundFirstRuntime)(service)


def activate(service, db, plan, key, request):
    if enabled(service,db,plan): raise ValueError('本方案新能力已经开启，不能再次登记。')
    obs=WorkCoordinatorHandoffPostRoundContext(service).observe(db,plan,key)['observation']
    from .work_coordinator_handoff_post_round_materials import track
    track(service,obs['material_observation'])
    service.work_file_rework_applications.preserve(obs['material_observation'])
    body=versioned({'protocol':CAPABILITY, 'observation':obs, 'coordination_protocol':PROTOCOL,
        'unit_protocol':UNIT, 'allowed_actions':ACTIONS, 'request':request, 'request_hash':service.command_payload_hash, 'scope':SCOPE, **PERMISSIONS})
    row={'id':KIND+':'+body['version'], 'version':body['version'], 'snapshot':body,
        'item_id':plan['item_id'], 'created':time.time(), 'command_id':service.command_identity}
    validate_capability(row)
    service.collaboration.put(db,KIND,row);service.store.event(None,None,OPENED,row,db)
    runtime(service,plan).read(db,plan)
    return deepcopy(row)


class PostRoundRuntime:
    def _load_post(self, db, plan):
        records=[]; events=[]
        identities=(plan['id'],plan['version'])
        for r in db.execute('SELECT kind,data FROM collaboration'):
            v=json.loads(r[1])
            if (r[0]==KIND or v.get('snapshot',{}).get('protocol')==CAPABILITY) and belongs(v,plan['item_id'],identities):
                if r[0]!=KIND: raise ValueError('新能力记录不能通过改名离开准确namespace。')
                records.append(validate_capability(v))
        for e in db.execute('SELECT * FROM events ORDER BY seq'):
            v=json.loads(e['payload'])
            if (e['kind']==OPENED or v.get('snapshot',{}).get('protocol')==CAPABILITY) and belongs(v,plan['item_id'],identities):
                if e['kind']!=OPENED or e['task_id'] is not None or e['attempt_id'] is not None:
                    raise ValueError('新能力必须有准确无task/attempt的独立开启事件。')
                events.append((e,v))
        if len(records)!=1 or len(events)!=1 or records[0]!=events[0][1]:
            raise ValueError('新能力记录与实际开启事件全集不闭合。')
        self._post=records[0];self._post_opened=events[0][0]['seq']
        obs=self._post['snapshot']['observation']
        if obs['plan_snapshot']!=plan: raise ValueError('新能力不属于准确当前方案。')

    def _once(self, db, plan):
        self._load_post(db,plan)
        h=super()._once(db,plan)
        h['post_round_capability']=self.ref(self._post)
        h['saved_path']=self._new_path(plan,h['frames'],h['material_observation'],h['phase'],h['driver'])
        h['verified_scope']=SCOPE
        return h

    def _new_path(self, plan, frames, material, phase, driver):
        value=self.legacy._path_value(self,plan,frames,material,phase,driver)
        value.pop('version')
        value.update(round_protocol=value['protocol'],round_scope=value['scope'],protocol=PATH,scope=SCOPE,
            post_round_capability=self.ref(self._post),
            activation_observation=observation_ref(self._post['snapshot']['observation']))
        return validate_path(versioned(value))

    def _run_protocol(self, db, plan, run, context, boundary):
        if boundary<self._post_opened: return super()._run_protocol(db,plan,run,context,boundary)
        return boundary>self._post_opened and context['protocol']==(PROTOCOL if run['input']['unit']['role']=='coordinate' else UNIT)

    def _historical_input(self, db, run, *args):
        if self.sources._reservation_event(db,run)<self._post_opened:
            return self.legacy._input(self,*args)
        return self._input(*args)

    def _source_protocol(self, db, plan, source, consumer):
        if consumer is not None and self.sources._reservation_event(db,consumer)<self._post_opened:
            return super()._source_protocol(db,plan,source,consumer)
        return (source['plan_id']==plan['id'] and self.sources._reservation_event(db,source)>self._post_opened and
            source['input'].get('handoff_coordination_context',{}).get('protocol')==PROTOCOL)

    def _input(self, plan, unit, task, driver, auth, frames, material, records, cap, proposal=None):
        phase=self._phase(plan,frames,records);deps=self._admit(plan,unit,frames,phase)
        path=self._new_path(plan,frames,material,phase,driver)
        from .work_coordinator_handoff_post_round_materials import track
        track(self.s,material)
        value={'item_id':plan['item_id'],'item_revision':task['revision'],'plan_id':plan['id'],
            'plan_version':plan['version'],'work_requirements':plan['work_requirements'],'unit':unit,
            'dependencies':deps,'check_evidence':[{'unit_key':d['unit_key'],'artifact':d['file_artifacts']['reference'],
                'check_status':d['check_status']} for d in deps],
            'file_artifacts':[{'unit_key':d['unit_key'],**d['file_artifacts']} for d in deps if d['file_artifacts']['state']=='CURRENT']}
        context={'protocol':PROTOCOL if unit['role']=='coordinate' else UNIT,'root':path['root'],
            'marker':plan['coordinator_file_handoff_root'],'plan':plan_ref(plan),'task':task,'driver':driver,
            'authorization':auth,'phase':phase,'prior_frontier':path['history_frontier'],
            'material_observation':material,'saved_path':path,**{k:path[k] for k in
                ('runtime_capability','adjustment_capability','round_capability','post_round_capability')},
            'scope':SCOPE,**PERMISSIONS}
        if unit['role']=='coordinate':
            old=self.old._context(plan,task,driver,auth,self._eligible(plan,frames),material)
            context.update({k:old[k] for k in ('work_requirements','configuration','units')})
            for entry,q in zip(context['units'],path['current_qualifications']): entry['qualification']=q['qualification']
            context.update(allowed_actions=ACTIONS);context=validate_context(versioned(context),self._post)
            forecast=self._forecast(plan,context,frames,unit)
            forecast.update(scope=SCOPE,previous_round=deepcopy(plan['coordination_revision']))
            from .work_coordinator_handoff_post_round_preview import input_context
            forecast.update(allowed_actions=ACTIONS, adjustment_context=input_context(plan,context,self._eligible(plan,frames)),
                adjustment_prior_frontier=context['prior_frontier'])
            value.update(handoff_coordination_context=context,coordination_context=forecast)
        else:
            if proposal is None: raise ValueError('普通V5缺少准确新V6提案。')
            context.update(unit_revision=digest(unit),proposal=proposal)
            value.update(handoff_unit_context=validate_context(versioned(context),self._post),coordination_proposal=proposal)
        return deepcopy(value)

    @staticmethod
    def prompt(frozen):
        context=frozen.get('handoff_coordination_context') or frozen.get('handoff_unit_context',{})
        if context.get('protocol') in (PROTOCOL,UNIT):
            from .work_coordinator_handoff_runtime import UNIT_PROMPT
            prompt=('读取完整冻结新协调输入，只返回dispatch_schema、pause_schema或adjustment_context.schema对应严格JSON；调整只作预览；不写文件。\n输入快照 JSON：\n'
                if frozen['unit']['role']=='coordinate' else UNIT_PROMPT)
            return prompt+canonical(frozen)
        from .work_coordinator_handoff_mixed_runtime import PROTOCOL as OLD_MIXED, UNIT as OLD_UNIT
        return (WorkCoordinatorHandoffMixedRuntime if context.get('protocol') in (OLD_MIXED,OLD_UNIT)
            else WorkCoordinatorHandoffRoundRuntime).prompt(frozen)

    def _intent(self, plan, frozen, frames):
        context=frozen['handoff_unit_context']
        if context['protocol']!=UNIT: return self.legacy._intent(self,plan,frozen,frames)
        from .work_coordinator_handoff_post_round_intent import PROTOCOL as INTENT, SCOPE as INTENT_SCOPE, validate_intent
        relation=plan['file_handoffs'][0]
        frame=next(f for f in self._eligible(plan,frames) if f['run']['unit_key']==relation['source_unit'])
        full=frame['source_evidence'];baseline=context['material_observation']
        self.s.work_artifacts.require(full,'checked' if frame['run']['input']['unit']['checks'] or frozen['unit']['dependency_gate']=='checked' else 'files')
        return validate_intent(versioned({'protocol':INTENT,'item_id':plan['item_id'],'item_revision':context['task']['revision'],
            'root':context['root'],'marker':plan['coordinator_file_handoff_root'],'plan':plan_ref(plan),'saved_path':context['saved_path'],
            'relation':relation,'driver':context['driver'],'authorization':context['authorization'],
            'source_run':frame['run']['id'],'source_evidence':full,'target_revision':digest(frozen['unit']),
            'protected_history':context['prior_frontier'],'baseline':baseline,'input_template_version':digest(frozen),
            'frontier':context['prior_frontier'],'scope':INTENT_SCOPE}))

    def _scope(self, db, plan, ready, application, registration, after, auth, save, parent_path, frames, h):
        result=super()._scope(db,plan,ready,application,registration,after,auth,save,parent_path,frames,h)
        self._audit_post(db,plan,h)
        return result

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
                from .work_coordinator_handoff_post_round_preview import proposal_state as post_state
                if frozen.get('handoff_coordination_context',{}).get('protocol') == PROTOCOL:
                    if not post_state(body) or body['target'] is not None:
                        raise ValueError('新调整预览状态或目标不符。')
                    self._preview_at(db,plan,h,row,seq,driver,auth)
                elif not proposal_state(body) or body['target'] is not None:
                    raise ValueError('调整提案身份无效。')
                # Applied previews are independently rebuilt by each actual
                # checkpoint. Unconsumed previews stay data, never a permit.
            else:
                raise ValueError('原协调输入未开放该提案操作。')

    def _audit_post(self, db, plan, h):
        obs=self._post['snapshot']['observation'];opened=self._post_opened
        versions=[(seq,v) for seq,v,_ in self._versions if seq<opened]
        if (not versions or versions[-1][1]!=obs['driver'] or h['authorization']!=obs['authorization'] or
                obs['round_capability']!=h['round_capability'] or
                self.checkpoints._event(db, self.round_opened, self._round)['seq']>=opened):
            raise ValueError('开启须绑定之前最新准确责任、原授权及真实ROUND能力。')
        prefix=[f for f in h['frames'] if self.sources._reservation_event(db,f['run'])<opened]
        later=[f for f in h['frames'] if self.sources._reservation_event(db,f['run'])>opened]
        if any(f['run']['plan_id']!=plan['id'] for f in later): raise ValueError('新能力后出现未支持保存边。')
        command=db.execute('SELECT * FROM commands WHERE id=?',(self._post['command_id'],)).fetchone()
        pending=(command is None and self.s.command_identity==self._post['command_id'] and
            self.s.command_kind=='work-item.unit-send')
        if not later and not pending: raise ValueError('开启能力必须与真实首次派发同命令保存。')
        if later:
            run=later[0]['run'];unit=obs['selected_unit']['definition']
            if (run['command_id']!=self._post['command_id'] or run['unit_key']!=unit['key'] or
                    run['input']['unit']!=unit or run['created']<self._post['created'] or
                    run['driver_revision']!=obs['driver']['revision'] or run['retry_of'] is not None):
                raise ValueError('开启能力与准确首派发/原责任/选中coordinate不符。')
            prefix=[self.sources._frame_before(db,f['run'],run) for f in prefix]
            if run['input']['handoff_coordination_context']['material_observation']!=obs['material_observation']:
                raise ValueError('开启到首次派发之间原材料发生变化。')
            if command is not None:
                receipt=json.loads(command['receipt'])
                if command['kind']!='work-item.unit-send' or receipt.get('launch_attempt')!=run['attempt_id']:
                    raise ValueError('新能力缺少同命令准确已提交派发回执。')
        if command is None and not pending: raise ValueError('新能力缺少真实开启命令。')
        actual_hash=command['payload_hash'] if command is not None else self.s.command_payload_hash
        if actual_hash!=self._post['snapshot']['request_hash']:
            raise ValueError('新能力开启请求与实际命令摘要不符。')
        # Reconstruct the complete old admission from its actual event prefix.
        records={k:[(r,seq) for r,seq in group if seq<opened] for k,group in self._records(db,plan).items()}
        phase=self._phase(plan,prefix,records);eligible=self._eligible(plan,prefix)
        path=self.legacy._path_value(self,plan,prefix,obs['material_observation'],phase,obs['driver'])
        unit=next(u for u in plan['units'] if u['key']==obs['selected_unit']['definition']['key'])
        deps=self._admit(plan,unit,prefix,phase)
        current=[f for f in eligible if f['run']['plan_id']==plan['id'] and
            f['run']['generation']==obs['driver']['generation'] and f['run']['input']['unit']['role']=='coordinate']
        latest=current[-1] if current else None;relation=plan['file_handoffs'][0]
        phases={n:next((f['source_evidence'] for f in reversed(eligible) if f['run']['unit_key']==relation[k]),None)
            for n,k in (('source','source_unit'),('target','target_unit'))}
        from .work_coordinator_handoff_adjustments import protected
        expected=validate_observation(versioned({'protocol':OBSERVATION,'root':h['root'],'plan':plan_ref(plan),
            'plan_snapshot':plan,'task':{'id':plan['item_id'],'revision':obs['driver']['item_revision']},
            'driver':obs['driver'],'authorization':h['authorization'],'saved_path':path,
            'runtime_capability':h['capability'],'adjustment_capability':h['adjustment_capability'],
            'round_capability':h['round_capability'],'phase':phase,'frontier':[self.old.frontier_entry(f) for f in prefix],
            'material_observation':obs['material_observation'],'selected_unit':{'definition':unit,'revision':digest(unit)},
            'protected_keys':sorted(protected(plan['units'],relation)),'configuration_version':digest({k:plan[k] for k in CONFIG_FIELDS}),
            'previous_round_source':path['source'],'existing_source':{'source':source_ref(latest['run'],latest['source_evidence']),
                'evidence':latest['source_evidence'],'input_protocol':latest['run']['input']['handoff_coordination_context']['protocol'],
                'adjustment_eligible':False} if latest else None,'usage':self.s.work_units.usage(db,[f['run'] for f in prefix]),
            'phase_sources':phases,'dependencies':deps,'observed_at':obs['observed_at'],'scope':OBS_SCOPE,**PERMISSIONS}))
        if expected!=obs: raise ValueError('开启观察不等于按实际事件原前沿完整重建。')
        self.s.work_file_rework_applications.require_baseline(obs['material_observation'])
        # Audit task/attempt attribution as well as payload identity, including first ROUND.
        identities={h['root']['id'],h['root']['version'],plan['id'],plan['version']}
        task_ids={f['run']['task_id'] for f in h['frames']};attempt_ids={f['run']['attempt_id'] for f in h['frames']}
        identities.update(task_ids|attempt_ids)
        expected={'attempt_result_saved':[],'cooperative_files_checked':[]}
        for frame in h['frames']:
            run=frame['run'];attempt=self.s.store.attempt(run['attempt_id'],db)
            if attempt['result'] is not None:
                expected['attempt_result_saved'].append(self.checkpoints._event(db,'attempt_result_saved',attempt['result'],None,run['task_id'],run['attempt_id']))
            for event in self.sources._capture_events(db,run,frame['files']):
                expected['cooperative_files_checked'].append(self.checkpoints.live._event(db.execute('SELECT * FROM events WHERE seq=?',(event['seq'],)).fetchone()))
        for kind,wanted in expected.items():
            actual=[self.checkpoints.live._event(e) for e in db.execute('SELECT * FROM events WHERE kind=?',(kind,))
                if belongs(json.loads(e['payload']),plan['item_id'],identities) or e['task_id'] in task_ids or e['attempt_id'] in attempt_ids]
            self._same(actual,wanted,'新运行实际退出/捕获事件存在孤立、误归属或遗漏。')

    def _preview_at(self, db, plan, h, row, seq, driver, auth):
        """Rebuild preview semantics at the actual check event, including old files."""
        known=[]
        for frame in h['frames']:
            run=frame['run']
            if self.sources._reservation_event(db,run)>=seq: continue
            attempt=self.s.store.attempt(run['attempt_id'],db)
            self.checkpoints._event(db,'attempt_result_saved',attempt['result'],seq,
                run['task_id'],run['attempt_id'])
            files,_=self.sources._saved_files(db,run)
            captures=[v for v in self.sources._capture_events(db,run,files) if v['seq']<seq]
            evidence=self.sources._historical_evidence(db,run,captures[-1]['artifact'] if captures else None)
            recorded={v['artifact']['id'] for v in captures}
            files['snapshots']=[v for v in files['snapshots'] if v['reference']['id'] in recorded]
            files['output_state']='INTACT' if files['snapshots'] else 'NOT_RECORDED'
            known.append({**frame,'evidence':evidence['raw'],'source_evidence':evidence['full'],'files':files})
        run=next(f['run'] for f in known if f['run']['id']==row['snapshot']['source']['run_id'])
        context=run['input']['handoff_coordination_context']
        records={k:[(r,s) for r,s in group if s<seq] for k,group in self._records(db,plan).items()}
        historical={**h,'task':{'id':plan['item_id'],'revision':driver['item_revision']},
            'driver':driver,'authorization':auth,'frames':known,'current_frames':self._eligible(plan,known),
            'phase':self._phase(plan,known,records),'material_observation':context['material_observation'],
            'usage':self.s.work_units.usage(db,[f['run'] for f in known])}
        from .work_coordinator_handoff_post_round_preview import rebuild_preview
        expected=rebuild_preview(plan,historical,run,row['snapshot']['decision'],observed_at=row['created'])
        if row['snapshot']['adjustment']!=expected:
            raise ValueError('新预览不等于真实检查事件之前的完整来源与语义重建。')


class PostRoundFirstRuntime(PostRoundRuntime,WorkCoordinatorHandoffRoundRuntime):
    legacy=WorkCoordinatorHandoffRoundRuntime
    from .work_coordinator_handoff_round_runtime import OPENED as round_opened


class PostRoundMixedRuntime(PostRoundRuntime,WorkCoordinatorHandoffMixedRuntime):
    legacy=WorkCoordinatorHandoffMixedRuntime
    from .work_coordinator_handoff_mixed_runtime import OPENED as round_opened
