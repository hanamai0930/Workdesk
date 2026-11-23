"""Actual V8/ordinary V7 runtime over the independently rebuilt V2 saved edge."""
from copy import deepcopy
import json
import time
from .store import canonical, digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import versioned, PERMISSIONS
from .work_coordinator_handoff_post_round_v2_saved_tree import WorkCoordinatorHandoffPostRoundV2SavedTree
from .work_coordinator_handoff_mixed_runtime import WorkCoordinatorHandoffMixedRuntime
from .work_coordinator_handoff_post_round_v2_saved_records import successor_driver
from .work_coordinator_handoff_post_round_candidates import LINEAGE_FIELD, ref
from .work_coordinator_handoff_post_round_v2_run_contracts import (
    KIND, OPENED, CAPABILITY, PROTOCOL, UNIT, PATH, SCOPE, ACTIONS,
    validate_capability, validate_context, validate_path, observation_ref, frontier_reference)
from .work_coordinator_handoff_post_round_v2_successor_contracts import validate_observation
from .work_coordinator_handoff_post_round_contracts import PROTOCOL as OLD_POST_PROTOCOL
from .work_coordinator_handoff_post_round_successor_contracts import PROTOCOL as V1_PROTOCOL



class WorkCoordinatorHandoffPostRoundV2Runtime(WorkCoordinatorHandoffPostRoundV2SavedTree):
    def read(self, db, plan):
        from .work_coordinator_handoff_post_round_v2_traversal import read
        return read(self, db, plan, super().read)

    def _drivers(self, db, plan, start, registered):
        values = []
        for row in self._related(db, 'work_driver_revision', plan['item_id']):
            if row['plan_id'] != plan['id']: continue
            driver = self.old._driver(db, plan, {'id': row['driver_id'], 'generation': row['generation'], 'revision': row['revision']})
            successor_driver(driver); values.append((self._driver_event(db, driver), driver, row))
        values.sort(key=lambda v: v[0])
        if not values or values[0][1] != start or any(seq <= registered for seq, _, _ in values[1:]):
            raise ValueError('后继责任必须由真实保存暂停版本依序恢复。')
        self._driver_transitions(db, values)
        return values

    def activate_successor(self, db, plan, unit, request):
        from .work_coordinator_handoff_post_round_v2_commands import pending
        if present(self.s, db, plan):
            self.read(db, plan); return
        from .work_coordinator_handoff_post_round_v2_successor_context import WorkCoordinatorHandoffPostRoundV2SuccessorContext
        obs = WorkCoordinatorHandoffPostRoundV2SuccessorContext(self.s).observe(db, plan, unit['key'])['observation']
        body = versioned({'protocol': CAPABILITY, 'observation': obs, 'coordination_protocol': PROTOCOL,
            'unit_protocol': UNIT, 'allowed_actions': ACTIONS, 'request': deepcopy(request),
            'request_hash': self.s.command_payload_hash, 'scope': SCOPE, **PERMISSIONS})
        row = {'id': KIND + ':' + body['version'], 'version': body['version'], 'snapshot': body,
            'item_id': plan['item_id'], 'created': time.time(), 'command_id': self.s.command_identity}
        validate_capability(row)
        if not pending(self.s, db, row): raise ValueError('后继开启只允许准确完整unit-send命令事务。')
        from .work_coordinator_handoff_post_round_materials import track
        track(self.s, obs['material_observation']); self.s.work_file_rework_applications.preserve(obs['material_observation'])
        self.s.collaboration.put(db, KIND, row); self.s.store.event(None, None, OPENED, row, db)
        self.read(db, plan)

    def _load_successor(self, db, plan):
        rows = self._related(db, KIND, plan['item_id']); events = self._events(db, OPENED, plan['item_id'])
        self._same(rows, [v for _, v in events], '后继能力记录与实际开启事件全集不符。')
        if len(rows) != 1: raise ValueError('后继须有唯一真实开启能力。')
        self._successor = validate_capability(rows[0]); self._opened = events[0][0]['seq']
        if self.s.collaboration.get(db, KIND, self._successor['id']) != self._successor:
            raise ValueError('后继能力数据库键与完整记录身份不符。')
        if self._successor['snapshot']['observation']['plan_snapshot'] != plan:
            raise ValueError('后继开启能力不属于准确完整当前方案。')

    def _path_value(self, plan, frames, material, phase, driver):
        from .work_coordinator_handoff_adjustments import qualifications
        obs = self._successor['snapshot']['observation']; base = obs['saved_path']
        seq = self._driver_event(self._db, driver)
        history = [{'event': self.checkpoints.live._event(self._db.execute('SELECT * FROM events WHERE seq=?', (s,)).fetchone()),
            'driver': v} for s, v, _ in self._versions if s <= seq]
        return validate_path(versioned({'protocol': PATH, 'root': obs['root'], 'plan': plan_ref(plan),
            'ancestors': base['ancestors'],
            'history_frontier': [self.old.frontier_entry(f) for f in frames],
            'current_qualifications': qualifications(plan, phase, self._eligible(plan, frames)),
            'material_observation': material, 'driver_history': history, 'driver': driver, 'authorization': self._auth,
            'successor_capability': self.ref(self._successor), 'activation_observation': observation_ref(obs),
            'scope': SCOPE, **PERMISSIONS}))

    def _run_protocol(self, db, plan, run, context, boundary):
        return boundary > self._opened and context['protocol'] == (PROTOCOL if run['input']['unit']['role'] == 'coordinate' else UNIT)

    def _source_protocol(self, db, plan, source, consumer):
        return source['plan_id'] == plan['id'] and self.sources._reservation_event(db, source) > self._opened and source['input'].get('handoff_coordination_context', {}).get('protocol') == PROTOCOL

    def _historical_input(self, db, run, *args): return self._input(*args)

    input_for = WorkCoordinatorHandoffMixedRuntime.input_for

    def _once(self, db, plan):
        self._db = db
        ready, start, auth = self._base_saved(db, plan)
        self._versions = self._drivers(db, plan, start, self._edge_value['registration_event']['seq'])
        driver = self._versions[-1][1]; ph = ready['observation']['history']
        self._load_successor(db, plan)
        obs = self._successor['snapshot']['observation']
        prefix_versions = [v for seq, v, _ in self._versions if seq < self._opened]
        if not prefix_versions or prefix_versions[-1] != obs['driver'] or self._opened <= self._edge_value['registration_event']['seq']:
            raise ValueError('后继开启须绑定保存后明确恢复的准确最新责任。')
        self._inherited = ready['frames']
        rows = [r for r in self.s.work_units.rows(db, plan['item_id']) if r['plan_id'] == plan['id']]
        from .work_coordinator_handoff_post_round_v2_commands import pending, require_receipt
        if not rows and not pending(self.s, db, self._successor): raise ValueError('后继开启缺少同命令首保留。')
        if rows and (rows[0]['command_id'] != self._successor['command_id'] or
                rows[0]['driver_revision'] != obs['driver']['revision'] or rows[0]['input']['unit'] != obs['selected_unit']['definition'] or
                rows[0]['created'] < self._successor['created']):
            raise ValueError('后继开启与真实首保留、原责任或选中coordinate不符。')
        cap, activated = self.capability(db, plan); records = self._records(db, plan)
        frames, revision, _ = self._layer_runs(db, plan, {'id': plan['item_id']},
            ph['marker']['project_root'], cap, activated, driver, auth,
            [r for _, _, r in self._versions], rows, records)
        self._match_root_records(db, self._root_plan, frames, records)
        # Before dispatch binding, the saved reservation still has the old task revision.
        if revision != driver['item_revision']: raise ValueError('后继实际运行绑定未到达当前准确任务版本。')
        current = deepcopy(frames)
        for f in current: f['source_evidence'] = self.s.work_units.evidence(db, f['run'])
        phase = self._phase(plan, frames, records); material = ready['current_material_observation']
        key = plan['file_handoffs'][0]['target_unit'] if phase == 'B_COMPLETED' else plan['file_handoffs'][0]['source_unit'] if phase == 'A_CAPTURED' else None
        if key and next(f for f in reversed(current) if f['run']['unit_key'] == key)['source_evidence']['file_artifacts']['state'] != 'CURRENT':
            phase = 'BLOCKED'
        eligible = self._eligible(plan, current)
        from .work_coordinator_handoff_adjustments import qualifications
        result = {**deepcopy(ph), 'protocol': 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_HISTORY_V2',
            'task': {'id': plan['item_id'], 'revision': revision}, 'driver': driver, 'authorization': auth,
            'frames': frames, 'current_frames': eligible, 'phase': phase, 'material_observation': material,
            'frontier': [self.old.frontier_entry(f) for f in frames],
            'delivery': [r for r, _ in records['work_coordinator_handoff_delivery']],
            'completion': [r for r, _ in records['work_coordinator_handoff_completion']],
            'usage': self.s.work_units.usage(db, [f['run'] for f in frames]),
            'units': [{'definition': u, 'qualification': q['qualification'], **PERMISSIONS}
                for u, q in zip(plan['units'], qualifications(plan, phase, eligible))],
            'application': self._application, 'registration': self._registration, 'child_plan': plan,
            'saved_edge_verified': True, 'full_saved_tree_verified': True, 'preparation_namespace_verified': True,
            'successor_runtime_open': True, 'successor_capability': self.ref(self._successor), 'public_apply': False, **PERMISSIONS}
        result['saved_path'] = self._path_value(plan, frames, material, phase, driver)
        self._audit_admission(db, plan, ready, obs, rows)
        from .work_coordinator_handoff_post_round_v2_runtime_audit import audit
        audit(self, db, plan, result); self._command(db, result); require_receipt(self, db, plan, frames)
        return deepcopy(result)

    def _audit_admission(self, db, plan, ready, obs, rows):
        from .work_coordinator_handoff_post_round_v2_successor_contracts import OBSERVATION, OBS_SCOPE
        from .work_coordinator_handoff_roots import CONFIG_FIELDS
        from .work_coordinator_handoff_adjustments import qualifications, protected
        ph = ready['observation']['history']; prefix = deepcopy(ready['frames'])
        if rows: prefix = [self.sources._frame_before(db, f['run'], rows[0]) for f in prefix]
        current = self._eligible(plan, prefix); unit = obs['selected_unit']['definition']
        base_history = {**ph, 'driver': obs['driver'], 'authorization': self._auth,
            'material_observation': obs['material_observation']}
        base = self._ordered_path(db, plan, base_history)
        expected = validate_observation(versioned({'protocol': OBSERVATION, 'root': ph['root'], 'plan': plan_ref(plan),
            'plan_snapshot': plan, 'task': {'id': plan['item_id'], 'revision': obs['driver']['item_revision']},
            'driver': obs['driver'], 'authorization': self._auth, 'saved_path': base,
            'phase': ph['phase'], 'frontier': ph['frontier'], 'material_observation': obs['material_observation'],
            'selected_unit': {'definition': unit, 'revision': digest(unit)},
            'qualifications': qualifications(plan, ph['phase'], current), 'dependencies': self._admit(plan, unit, prefix, ph['phase']),
            'usage': self.s.work_units.usage(db, [f['run'] for f in prefix]),
            'protected_keys': sorted(protected(plan['units'], plan['file_handoffs'][0])),
            'configuration_version': digest({k: plan[k] for k in CONFIG_FIELDS}),
            'observed_at': obs['observed_at'], 'scope': OBS_SCOPE, **PERMISSIONS}))
        if expected != obs or (rows and rows[0]['input']['handoff_coordination_context']['material_observation'] != obs['material_observation']):
            raise ValueError('V2开启观察不等于按实际事件完整重建的保存前沿。')
        self.s.work_file_rework_applications.require_baseline(obs['material_observation'])

    def _input(self, plan, unit, task, driver, auth, frames, material, records, cap, proposal=None):
        phase=self._phase(plan,frames,records);deps=self._admit(plan,unit,frames,phase)
        path=self._path_value(plan,frames,material,phase,driver)
        from .work_coordinator_handoff_post_round_materials import track
        track(self.s,material)
        value={'item_id':plan['item_id'],'item_revision':task['revision'],'plan_id':plan['id'],
            'plan_version':plan['version'],'work_requirements':plan['work_requirements'],'unit':unit,
            'dependencies':deps,'check_evidence':[{'unit_key':d['unit_key'],'artifact':d['file_artifacts']['reference'],
                'check_status':d['check_status']} for d in deps],
            'file_artifacts':[{'unit_key':d['unit_key'],**d['file_artifacts']} for d in deps if d['file_artifacts']['state']=='CURRENT']}
        context={'protocol':PROTOCOL if unit['role']=='coordinate' else UNIT,'root':path['root'],
            'marker':plan['coordinator_file_handoff_root'],'plan':plan_ref(plan),'task':task,'driver':driver,
            'authorization':auth,'phase':phase,'prior_frontier_reference':frontier_reference(path),
            'material_observation':material,'saved_path':path,'successor_capability':path['successor_capability'],
            'selected_unit':{'definition':unit,'revision':digest(unit)},
            'scope':SCOPE,**PERMISSIONS}
        if unit['role']=='coordinate':
            old=self.old._context(plan,task,driver,auth,self._eligible(plan,frames),material)
            context.update({k:old[k] for k in ('work_requirements','configuration','units')})
            for entry,q in zip(context['units'],path['current_qualifications']): entry['qualification']=q['qualification']
            context.update(allowed_actions=ACTIONS);context=validate_context(versioned(context),self._successor)
            forecast=self._forecast(plan,{**context,'prior_frontier':path['history_frontier']},frames,unit)
            forecast.update(scope=SCOPE,previous_round=deepcopy(plan['coordination_revision']))
            from .work_coordinator_handoff_post_round_v2_preview import input_context
            forecast.update(allowed_actions=ACTIONS, adjustment_context=input_context(plan,context,self._eligible(plan,frames)),
                adjustment_prior_frontier=frontier_reference(path))
            value.update(handoff_coordination_context=context,coordination_context=forecast)
        else:
            if proposal is None: raise ValueError('普通V5缺少准确新V6提案。')
            context.update(unit_revision=digest(unit),proposal=proposal)
            value.update(handoff_unit_context=validate_context(versioned(context),self._successor),coordination_proposal=proposal)
        return deepcopy(value)

    def _intent(self, plan, frozen, frames):
        from .work_coordinator_handoff_post_round_v2_intent import PROTOCOL as INTENT, SCOPE as INTENT_SCOPE, validate_intent
        from .work_coordinator_handoff_post_round_v2_run_contracts import observation_ref
        context = frozen['handoff_unit_context']; relation = plan['file_handoffs'][0]
        frame = next(f for f in self._eligible(plan, frames) if f['run']['unit_key'] == relation['source_unit'])
        full = frame['source_evidence']; path = context['saved_path']
        self.s.work_artifacts.require(full, 'checked' if frame['run']['input']['unit']['checks'] or frozen['unit']['dependency_gate'] == 'checked' else 'files')
        return validate_intent(versioned({'protocol': INTENT, 'item_id': plan['item_id'],
            'item_revision': context['task']['revision'], 'root': context['root'],
            'marker': plan['coordinator_file_handoff_root'], 'plan': plan_ref(plan),
            'saved_path_reference': observation_ref(path), 'prior_frontier_reference': context['prior_frontier_reference'],
            'phase': context['phase'], 'relation': relation, 'driver': context['driver'],
            'authorization': context['authorization'], 'source_run': frame['run']['id'], 'source_evidence': full,
            'target_revision': digest(frozen['unit']), 'baseline': context['material_observation'],
            'input_template_version': digest(frozen), 'scope': INTENT_SCOPE}), path)

    @staticmethod
    def prompt(frozen):
        context=frozen.get('handoff_coordination_context') or frozen.get('handoff_unit_context',{})
        if context.get('protocol') in (PROTOCOL,UNIT):
            from .work_coordinator_handoff_runtime import UNIT_PROMPT
            prompt=('读取完整冻结新协调输入，只返回dispatch_schema、pause_schema或adjustment_context.schema对应严格JSON；调整只作预览；不写文件。\n输入快照 JSON：\n'
                if frozen['unit']['role']=='coordinate' else UNIT_PROMPT)
            return prompt+canonical(frozen)
        raise ValueError('后继prompt不接受旧冻结协议。')

    def _check_proposals(self, db, plan, h):
        rows=self._related(db,'work_proposal',plan['item_id'])
        events=self._proposal_events(db,plan['item_id'])
        if sorted(map(digest,rows))!=sorted(digest(v) for _,v in events):
            raise ValueError('全部提案记录或实际事件存在遗漏、重复或孤立版本。')
        self._check_proposal_pointer(db, plan, events)
        self._check_proposal_values(db, plan, h, events)

    def _check_proposal_pointer(self, db, plan, events):
        if events and self.s.work_proposals.current(db,plan['item_id'])!=events[-1][1]:
            raise ValueError('当前提案指针不是实际最新记录。')

    def _check_proposal_values(self, db, plan, h, events):
        from .work_proposals import parse
        from .work_coordinator_handoff_adjustment_records import SOURCE_FIELDS
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
                from .work_coordinator_handoff_post_round_preview import proposal_state as old_post_state
                from .work_coordinator_handoff_post_round_successor_preview import proposal_state as new_post_state
                from .work_coordinator_handoff_post_round_v2_preview import proposal_state as v2_state
                protocol = frozen.get('handoff_coordination_context',{}).get('protocol')
                post_state = v2_state if protocol == PROTOCOL else new_post_state if protocol == V1_PROTOCOL else old_post_state
                if frozen.get('handoff_coordination_context',{}).get('protocol') in (PROTOCOL, V1_PROTOCOL, OLD_POST_PROTOCOL):
                    if not post_state(body) or body['target'] is not None:
                        raise ValueError('新调整预览状态或目标不符。')
                    self._preview_at(db,source_plan,h,row,seq,driver,auth)
                elif not proposal_state(body) or body['target'] is not None:
                    raise ValueError('调整提案身份无效。')
                # Applied previews are independently rebuilt by each actual
                # checkpoint. Unconsumed previews stay data, never a permit.
            else:
                raise ValueError('原协调输入未开放该提案操作。')

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
        if context['protocol'] == PROTOCOL:
            from .work_coordinator_handoff_post_round_v2_preview import rebuild_preview
        elif context['protocol'] == V1_PROTOCOL:
            from .work_coordinator_handoff_post_round_successor_preview import rebuild_preview
        else:
            from .work_coordinator_handoff_post_round_preview import rebuild_preview
        expected=rebuild_preview(plan,historical,run,row['snapshot']['decision'],observed_at=row['created'])
        if row['snapshot']['adjustment']!=expected:
            raise ValueError('新预览不等于真实检查事件之前的完整来源与语义重建。')


def present(service, db, plan):
    if db is None: return False
    from .work_coordinator_handoff_post_round_runtime import belongs
    from .work_coordinator_handoff_post_round_v2_commands import RECEIPT_PROTOCOL
    protocols = {CAPABILITY, RECEIPT_PROTOCOL}
    def marked(value):
        todo = [value]
        while todo:
            v = todo.pop()
            if isinstance(v, dict):
                identity = v.get('id', '')
                if v.get('protocol') in protocols or (isinstance(identity, str) and identity.startswith(KIND + ':')): return True
                todo.extend(v.values())
            elif isinstance(v, list): todo.extend(v)
        return False
    for table, column in (('collaboration','data'),('events','payload')):
        for row in db.execute('SELECT kind,' + column + ' FROM ' + table):
            v = json.loads(row[1])
            if (row[0] in (KIND, OPENED) or marked(v)) and belongs(v, plan['item_id'], (plan['id'],plan['version'])): return True
    return False
