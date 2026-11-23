"""Actual V7/ordinary V6 runtime over the first real post-round saved edge."""
from copy import deepcopy
import json
import time
from .store import canonical, digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import versioned, PERMISSIONS
from .work_coordinator_handoff_post_round_saved_tree import WorkCoordinatorHandoffPostRoundSavedTree
from .work_coordinator_handoff_mixed_runtime import WorkCoordinatorHandoffMixedRuntime
from .work_coordinator_handoff_post_round_saved_records import *
from .work_coordinator_handoff_post_round_candidates import LINEAGE_FIELD, ref
from .work_coordinator_handoff_post_round_successor_contracts import (
    KIND, OPENED, CAPABILITY, PROTOCOL, UNIT, PATH, SCOPE, ACTIONS,
    validate_capability, validate_context, validate_path, validate_observation, observation_ref)
from .work_coordinator_handoff_post_round_contracts import PROTOCOL as OLD_POST_PROTOCOL


class WorkCoordinatorHandoffPostRoundSuccessorRuntime(WorkCoordinatorHandoffPostRoundSavedTree):
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

    def read(self, db, plan):
        from .work_read_observation import database_observation
        with database_observation(db):
            selection = lambda: {u['key']: {k: v for k, v in self.s.resources.selection(db, u['member']['reference']['member_id']).items()
                if k != 'selected_at'} for u in plan['units']}
            before = selection()
            result = super().read(db, plan)
            if selection() != before: raise ValueError('后继末尾完整成员、连接或额度数据发生变化。')
            return result

    def activate_successor(self, db, plan, unit, request):
        from .work_coordinator_handoff_post_round_successor_commands import pending
        if present(self.s, db, plan):
            self.read(db, plan); return
        from .work_coordinator_handoff_post_round_successor_context import WorkCoordinatorHandoffPostRoundSuccessorContext
        obs = WorkCoordinatorHandoffPostRoundSuccessorContext(self.s).observe(db, plan, unit['key'])['observation']
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
            'parent_kind': 'POST_ROUND_ADJUSTMENT', 'parent_path': base['parent_path'], 'edge': self._edge_value,
            'history_frontier': [self.old.frontier_entry(f) for f in frames],
            'current_qualifications': qualifications(plan, phase, self._eligible(plan, frames)),
            'material_observation': material, 'driver_history': history, 'driver': driver, 'authorization': self._auth,
            **{k: obs[k] for k in ('runtime_capability','adjustment_capability','round_capability','post_round_capability')},
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
        from .work_coordinator_handoff_post_round_successor_commands import pending, require_receipt
        if not rows and not pending(self.s, db, self._successor): raise ValueError('后继开启缺少同命令首保留。')
        if rows and (rows[0]['command_id'] != self._successor['command_id'] or
                rows[0]['driver_revision'] != obs['driver']['revision'] or rows[0]['input']['unit'] != obs['selected_unit']['definition'] or
                rows[0]['created'] < self._successor['created']):
            raise ValueError('后继开启与真实首保留、原责任或选中coordinate不符。')
        cap, activated = self.capability(db, plan); records = self._records(db, plan)
        frames, revision, _ = self._layer_runs(db, plan, {'id': plan['item_id']},
            ph['marker']['project_root'], cap, activated, driver, auth,
            [r for _, _, r in self._versions], rows, records)
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
        result = {**deepcopy(ph), 'protocol': 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_HISTORY_V1',
            'task': {'id': plan['item_id'], 'revision': revision}, 'driver': driver, 'authorization': auth,
            'frames': frames, 'current_frames': eligible, 'phase': phase, 'material_observation': material,
            'frontier': [self.old.frontier_entry(f) for f in frames],
            'usage': self.s.work_units.usage(db, [f['run'] for f in frames]),
            'units': [{'definition': u, 'qualification': q['qualification'], **PERMISSIONS}
                for u, q in zip(plan['units'], qualifications(plan, phase, eligible))],
            'application': self._application, 'registration': self._registration, 'child_plan': plan,
            'saved_edge_verified': True, 'full_saved_tree_verified': True, 'preparation_namespace_verified': True,
            'successor_runtime_open': True, 'successor_capability': self.ref(self._successor), 'public_apply': False, **PERMISSIONS}
        result['saved_path'] = self._path_value(plan, frames, material, phase, driver)
        self._audit_admission(db, plan, ready, obs, rows)
        from .work_coordinator_handoff_post_round_successor_audit import audit
        audit(self, db, plan, result); self._command(db, result); require_receipt(self, db, plan, frames)
        return deepcopy(result)

    def _audit_admission(self, db, plan, ready, obs, rows):
        from .work_coordinator_handoff_post_round_successor_contracts import OBSERVATION, OBS_SCOPE
        from .work_coordinator_handoff_roots import CONFIG_FIELDS
        from .work_coordinator_handoff_adjustments import qualifications, protected
        ph = ready['observation']['history']; prefix = deepcopy(ready['frames'])
        if rows: prefix = [self.sources._frame_before(db, f['run'], rows[0]) for f in prefix]
        current = self._eligible(plan, prefix); unit = obs['selected_unit']['definition']
        base = versioned({'protocol': 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SAVED_PATH_V1', 'root': ph['root'],
            'plan': plan_ref(plan), 'parent_path': ph['saved_path'], 'edge': self._edge_value,
            'history_frontier': ph['frontier'], 'driver': obs['driver'], 'authorization': self._auth,
            'material_observation': obs['material_observation'], **PERMISSIONS})
        expected = validate_observation(versioned({'protocol': OBSERVATION, 'root': ph['root'], 'plan': plan_ref(plan),
            'plan_snapshot': plan, 'task': {'id': plan['item_id'], 'revision': obs['driver']['item_revision']},
            'driver': obs['driver'], 'authorization': self._auth, 'saved_path': base,
            'runtime_capability': ph['capability'], **{k: ph[k] for k in ('adjustment_capability','round_capability','post_round_capability')},
            'phase': ph['phase'], 'frontier': ph['frontier'], 'material_observation': obs['material_observation'],
            'selected_unit': {'definition': unit, 'revision': digest(unit)},
            'qualifications': qualifications(plan, ph['phase'], current), 'dependencies': self._admit(plan, unit, prefix, ph['phase']),
            'usage': self.s.work_units.usage(db, [f['run'] for f in prefix]),
            'protected_keys': sorted(protected(plan['units'], plan['file_handoffs'][0])),
            'configuration_version': digest({k: plan[k] for k in CONFIG_FIELDS}),
            'observed_at': obs['observed_at'], 'scope': OBS_SCOPE, **PERMISSIONS}))
        if expected != obs or (rows and rows[0]['input']['handoff_coordination_context']['material_observation'] != obs['material_observation']):
            raise ValueError('后继开启观察不等于按实际事件完整重建的保存前沿。')
        self.s.work_file_rework_applications.require_baseline(obs['material_observation'])
    def _base_saved(self, db, plan):
        # The guard follows actual previous_plan_reference and retains the
        # original authorization ceiling and cycle checks before codec dispatch.
        self.checkpoints._guard(db, plan)
        candidates = [r for r in self.s.collaboration.all(db, KIND_FOR[CANDIDATE])
            if r.get('snapshot', {}).get('child_plan') == plan]
        if len(candidates) != 1: raise ValueError('新保存须有唯一准确完整child候选。')
        candidate = candidates[0]
        cp = self.preparation._get(db, CHECKPOINT, candidate['snapshot']['checkpoint'])
        parent = cp['snapshot']['plan']
        if parent.get(LINEAGE_FIELD):
            raise ValueError('本保存阶段尚无post-round父新运行，不能套用旧ROUND父codec。')
        ready = self.preparation._from_history(db, ref(candidate))
        parent_reader = self.preparation._parent_reader
        successor_plan(plan, ready['candidate'])
        if self.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan:
            raise ValueError('新保存方案完整实际记录已变化。')
        self._parent_reader = parent_reader; self._parent_plan = parent
        self._parents = parent_reader._parents + [parent]
        self._layers = getattr(parent_reader, '_layers', []) + [parent_reader]
        self._root_plan = self._parents[0]
        self._plan_ids = {p['id'] for p in self._parents + [plan]}
        self._identity_ids = set(self._plan_ids) | {p['version'] for p in self._parents + [plan]}
        self._task_ids = set(); self._attempt_ids = set(); self._frame_identities(ready['frames'])
        self._identity_ids.update(ready['checkpoint']['snapshot']['history']['driver'][k] for k in ('id', 'generation', 'revision'))
        self._identity_ids.update(ready['checkpoint']['snapshot']['history']['authorization'][k] for k in ('id', 'revision'))
        apps = [r for r in self._related(db, KIND_FOR[APPLICATION], plan['item_id']) if r['snapshot']['child_plan'] == plan_ref(plan)]
        regs = [r for r in self._related(db, KIND_FOR[REGISTRATION], plan['item_id']) if r['snapshot']['to_plan'] == plan_ref(plan)]
        if len(apps) != 1 or len(regs) != 1: raise ValueError('新保存缺少唯一实际应用与登记。')
        app = self.data.get(db, APPLICATION, ref(apps[0])); reg = self.data.get(db, REGISTRATION, ref(regs[0]))
        b = app['snapshot']; h = ready['observation']['history']
        after = self.old._driver(db, plan, b['driver_after']); auth = self.old._auth(db, plan, b['authorization_after'])
        adjacent(ready['checkpoint'], candidate, plan, h['driver'], after, h['authorization'], auth)
        request = b['request']
        # Recompute all seven values from actual parent facts, not just a stored
        # self-consistent request hash. The frontier contract is run/evidence based.
        expected_request = {'id': plan['item_id'], 'revision': h['task']['revision'],
            'current_driver': self.s.work_drivers.reference(h['driver']), 'plan': plan_ref(parent),
            'authorization': self.s.work_units.auth_ref(h['authorization']),
            'frontier': digest([{'run_id': f['run']['id'], 'evidence': f['evidence']['version']} for f in ready['frames']]), 'proposal': candidate['snapshot']['proposal']}
        if request != expected_request: raise ValueError('新保存七字段不是准确真实父版本。')
        save = self.preparation._event(db, SAVE_EVENT, save_payload(self.s, candidate, plan, after, auth))
        ae = self.preparation._event(db, EVENTS[APPLICATION], app); re = self.preparation._event(db, EVENTS[REGISTRATION], reg)
        if (bodies(self.s, ready['checkpoint'], candidate, plan, h['driver'], after, h['authorization'], auth, save, request)
                != (b, reg['snapshot']) or
                not ready['candidate_event']['seq'] < self._driver_event(db, after) < save['seq'] < ae['seq'] < re['seq'] or
                any(r['command_id'] != candidate['command_id'] for r in (app, reg)) or
                not after['updated'] <= app['created'] <= reg['created'] < auth['dispatch_before']):
            raise ValueError('新保存实际事件、完整登记、命令或最初截止不符。')
        self._ready = ready; self._auth = auth; self._round = None
        self._edge_value = {'parent_plan': plan_ref(parent), 'child_plan': plan_ref(plan),
            'checkpoint': ref(ready['checkpoint']), 'candidate': ref(candidate), 'application': ref(app),
            'registration': ref(reg), 'save_event': save, 'registration_event': re}
        self._application = app; self._registration = reg
        return ready, after, auth

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
            'authorization':auth,'phase':phase,'prior_frontier':path['history_frontier'],
            'material_observation':material,'saved_path':path,**{k:path[k] for k in
                ('runtime_capability','adjustment_capability','round_capability','post_round_capability','successor_capability')},
            'scope':SCOPE,**PERMISSIONS}
        if unit['role']=='coordinate':
            old=self.old._context(plan,task,driver,auth,self._eligible(plan,frames),material)
            context.update({k:old[k] for k in ('work_requirements','configuration','units')})
            for entry,q in zip(context['units'],path['current_qualifications']): entry['qualification']=q['qualification']
            context.update(allowed_actions=ACTIONS);context=validate_context(versioned(context),self._successor)
            forecast=self._forecast(plan,context,frames,unit)
            forecast.update(scope=SCOPE,previous_round=deepcopy(plan['coordination_revision']))
            from .work_coordinator_handoff_post_round_successor_preview import input_context
            forecast.update(allowed_actions=ACTIONS, adjustment_context=input_context(plan,context,self._eligible(plan,frames)),
                adjustment_prior_frontier={**{k:context[k] for k in ('protocol','version','root')},'field':'prior_frontier'})
            value.update(handoff_coordination_context=context,coordination_context=forecast)
        else:
            if proposal is None: raise ValueError('普通V5缺少准确新V6提案。')
            context.update(unit_revision=digest(unit),proposal=proposal)
            value.update(handoff_unit_context=validate_context(versioned(context),self._successor),coordination_proposal=proposal)
        return deepcopy(value)

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
                from .work_coordinator_handoff_post_round_preview import proposal_state as old_post_state
                from .work_coordinator_handoff_post_round_successor_preview import proposal_state as new_post_state
                post_state = new_post_state if frozen.get('handoff_coordination_context',{}).get('protocol') == PROTOCOL else old_post_state
                if frozen.get('handoff_coordination_context',{}).get('protocol') in (PROTOCOL, OLD_POST_PROTOCOL):
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
            from .work_coordinator_handoff_post_round_successor_preview import rebuild_preview
        else:
            from .work_coordinator_handoff_post_round_preview import rebuild_preview
        expected=rebuild_preview(plan,historical,run,row['snapshot']['decision'],observed_at=row['created'])
        if row['snapshot']['adjustment']!=expected:
            raise ValueError('新预览不等于真实检查事件之前的完整来源与语义重建。')


def present(service, db, plan):
    if db is None: return False
    from .work_coordinator_handoff_post_round_runtime import belongs
    from .work_coordinator_handoff_post_round_successor_commands import RECEIPT_PROTOCOL
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
