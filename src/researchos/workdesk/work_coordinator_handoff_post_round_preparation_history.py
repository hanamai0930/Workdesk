"""Bounded reconstruction of actual post-ROUND preparation after pointer changes.

This is a parent component, never a saved-tree audit or a dispatch permission.
The old current-parent candidate/runtime readers retain their original gates.
"""
from copy import deepcopy
import json

from .store import digest
from .work_transitions import plan_ref, source_ref
from .work_coordinator_handoff_records import PERMISSIONS, versioned
from .work_coordinator_handoff_roots import CONFIG_FIELDS
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_candidates import (
    WorkCoordinatorHandoffPostRoundCandidates, CHECKPOINT, CANDIDATE,
    CHECKPOINT_EVENT, EVENTS, HISTORY_FIELDS, ref)
from .work_coordinator_handoff_post_round_history import _First, _Mixed
from .work_coordinator_handoff_post_round_runtime import belongs
from .work_coordinator_handoff_post_round_contracts import (
    KIND, OPENED, CAPABILITY, PROTOCOL, validate_capability)
from .work_coordinator_handoff_post_round_context import (
    OBSERVATION, SCOPE as OBS_SCOPE, validate_observation)
from .work_coordinator_handoff_post_round_preview import proposal_state, rebuild_preview
from .work_read_observation import database_observation


class _HistoricalPost:
    def _load_post(self, db, plan):
        # Only this parent's actual opening is part of this bounded component.
        # The saved successor must audit all later/orphan capability namespaces.
        records = []
        events = []
        for row in db.execute('SELECT kind,data FROM collaboration'):
            value = json.loads(row['data'])
            obs = value.get('snapshot', {}).get('observation', {})
            if obs.get('plan_snapshot') != plan:
                continue
            if row['kind'] == KIND or value.get('snapshot', {}).get('protocol') == CAPABILITY:
                if row['kind'] != KIND:
                    raise ValueError('父能力记录的原namespace已改名。')
                records.append(validate_capability(value))
        for event in db.execute('SELECT * FROM events WHERE seq<? ORDER BY seq', (self._cutoff,)):
            value = json.loads(event['payload'])
            obs = value.get('snapshot', {}).get('observation', {})
            if obs.get('plan_snapshot') != plan:
                continue
            if event['kind'] == OPENED or value.get('snapshot', {}).get('protocol') == CAPABILITY:
                if event['kind'] != OPENED or event['task_id'] is not None or event['attempt_id'] is not None:
                    raise ValueError('父能力开启事件归属不符。')
                events.append((event, validate_capability(value)))
        if len(records) != 1 or len(events) != 1 or records[0] != events[0][1]:
            raise ValueError('父能力实际记录和截止前唯一开启事件不符。')
        self._post = records[0]
        self._post_opened = events[0][0]['seq']

    def _check_proposals(self, db, plan, h):
        from .work_proposals import parse
        from .work_coordinator_handoff_adjustment_records import SOURCE_FIELDS
        rows=self._related(db,'work_proposal',plan['item_id'])
        events=self._proposal_events(db,plan['item_id'])
        if sorted(map(digest,rows))!=sorted(digest(v) for _,v in events):
            raise ValueError('全部提案记录或实际事件存在遗漏、重复或孤立版本。')
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
                if e['seq'] < self._cutoff and (belongs(json.loads(e['payload']),plan['item_id'],identities) or e['task_id'] in task_ids or e['attempt_id'] in attempt_ids)]
            self._same(actual,wanted,'新运行实际退出/捕获事件存在孤立、误归属或遗漏。')


class _FirstHistory(_HistoricalPost, _First):
    pass


class _MixedHistory(_HistoricalPost, _Mixed):
    pass


class WorkCoordinatorHandoffPostRoundPreparationHistory(WorkCoordinatorHandoffPostRoundCandidates):
    def _from_history(self, db, identity):
        """One full traversal; a complete successor reader repeats and audits it."""
        candidate = self._get(db, CANDIDATE, identity)
        body = candidate['snapshot']
        checkpoint = self._get(db, CHECKPOINT, body['checkpoint'])
        cp = checkpoint['snapshot']
        plan = cp['plan']
        if self.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan:
            raise ValueError('截止父方案的完整实际记录已变化。')
        event = self._event(db, CHECKPOINT_EVENT, checkpoint)
        candidate_event = self._event(db, EVENTS[CANDIDATE], candidate)
        mixed = plan['coordinator_file_handoff_round_lineage']['protocol'] == 'COORDINATOR_FILE_HANDOFF_MIXED_ROUND_LINEAGE_V1'
        helper = (_MixedHistory if mixed else _FirstHistory)(self.s)
        h, actual = helper.reconstruct(db, plan, event, cp['history']['material_observation'])
        self._parent_reader = helper  # This traversal only; never a cached complete proof.
        proposal = self.s.collaboration.get(db, 'work_proposal', body['proposal']['id'])
        checks = helper._proposal_events(db, plan['item_id'])
        if (ref(proposal) != body['proposal'] or not checks or checks[-1][1] != proposal or
                not proposal_state(proposal['snapshot']) or h['driver']['state'] != 'RESERVED' or
                h['post_round_capability'] != proposal['snapshot']['adjustment']['capability']):
            raise ValueError('截止父提案不是当时准确最新检查或原责任能力不符。')
        source = next((f for f in h['frames'] if f['run']['id'] == proposal['snapshot']['source']['run_id']), None)
        if source is None:
            raise ValueError('截止父提案缺少真实协调来源。')
        preview = rebuild_preview(plan, h, source['run'], proposal['snapshot']['decision'], observed_at=proposal['created'])
        facts = {'proposal': proposal, 'plan': plan, 'history': h, 'preview': preview}
        if (preview != proposal['snapshot']['adjustment'] or
                cp != self._checkpoint(facts, cp['cutoff']) or
                body != self._candidate(facts, checkpoint, body['child_seed']) or
                event['seq'] != cp['cutoff'] + 1 or candidate_event['seq'] != event['seq'] + 1 or
                candidate['command_id'] != checkpoint['command_id'] or
                not proposal['created'] <= checkpoint['created'] <= body['child_seed']['created'] <= candidate['created'] < h['authorization']['dispatch_before']):
            raise ValueError('历史准备完整语义、命令、原截止或真实事件顺序不符。')
        if {k: h[k] for k in HISTORY_FIELDS} != cp['history']:
            raise ValueError('历史检查点不等于真实截止父层完整重建。')
        return {'checkpoint': checkpoint, 'candidate': candidate, 'child_plan': body['child_plan'],
            'checkpoint_event': event, 'candidate_event': candidate_event,
            'frames': h['frames'], 'observation': {'history': cp['history']},
            'current_material_observation': actual, 'candidate_data_verified': True,
            'bounded_parent_components_verified': True, 'preparation_namespace_verified': False,
            'saved_edge_verified': False, 'public_apply': False, **PERMISSIONS}

    @closed
    def read(self, db, identity):
        if not db.in_transaction:
            raise ValueError('历史准备重建须属于明确数据库事务。')
        with database_observation(db):
            changes = db.total_changes
            first = self._from_history(db, identity)
            again = self._from_history(db, identity)
            from .work_coordinator_handoff_round_checkpoints import WorkCoordinatorHandoffRoundCheckpoints
            WorkCoordinatorHandoffRoundCheckpoints(self.s)._recheck(db, again)
            if first != again or changes != db.total_changes:
                raise ValueError('历史准备两次独立完整重建、末尾材料或数据库发生变化。')
            return deepcopy(again)
