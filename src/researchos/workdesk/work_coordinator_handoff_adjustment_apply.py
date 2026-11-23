"""Internal saved adjustments, with full successor history delegated to Path.

The no-run first edge keeps its narrow reader. Public application stays closed.
"""
from copy import deepcopy
import json
import time

from .store import digest, uid
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import PERMISSIONS, versioned, DELIVERY_KIND, COMPLETION_KIND
from .work_coordinator_handoff_adjustment_records import (
    WorkCoordinatorHandoffAdjustmentRecords, CANDIDATE, APPLICATION, CONSUMPTION,
    KIND_FOR, EVENTS, PAUSE_REASON, HISTORY_FIELDS)
from .work_coordinator_handoff_adjustment_contracts import adjacent
from .work_coordinator_handoff_adjustment_sources import WorkCoordinatorHandoffAdjustmentSources
from .work_coordinator_handoff_adjustment_candidates import WorkCoordinatorHandoffAdjustmentCandidates
from .work_coordinator_handoff_adjustment_consumption import require_unconsumed


SAVE_EVENT = 'coordinator_handoff_adjustment_plan_saved'


class WorkCoordinatorHandoffAdjustmentApplications:
    def __init__(self, service):
        self.s = service
        self.records = WorkCoordinatorHandoffAdjustmentRecords(service)
        self.sources = WorkCoordinatorHandoffAdjustmentSources(service)
        self.candidates = WorkCoordinatorHandoffAdjustmentCandidates(service)

    def _rows(self, db, kind, item_id):
        return [r for r in self.s.collaboration.all(db, kind)
                if r.get('item_id') == item_id or r.get('snapshot', {}).get('item_id') == item_id or
                    r.get('snapshot', {}).get('root', {}).get('id') in getattr(self,'_plan_ids',set())]

    def _events(self, db, kind, item_id):
        result = []
        for row in db.execute('SELECT seq,payload FROM events WHERE kind=? ORDER BY seq', (kind,)):
            payload = json.loads(row['payload'])
            if (payload.get('item_id') == item_id or
                    payload.get('root', {}).get('id') in self._plan_ids or
                    payload.get('snapshot', {}).get('root', {}).get('id') in self._plan_ids):
                result.append((row['seq'], payload))
        return result

    @staticmethod
    def _common(protocol, body):
        return {k:deepcopy(body[k]) for k in
                ('item_id', 'root', 'parent_plan', 'capability', 'operation', 'scope')} | {
                    'protocol':protocol, **PERMISSIONS}

    def _bodies(self, candidate, checkpoint, child, before, after, old_auth, auth, save_event):
        body = candidate['snapshot']
        application = versioned({**self._common(APPLICATION, body),
            'candidate':self.records.ref(candidate), 'checkpoint':self.records.ref(checkpoint),
            'child_plan':plan_ref(child), 'proposal':body['proposal'], 'source':body['transition']['source'],
            'driver_before':self.s.work_drivers.reference(before),
            'driver_after':self.s.work_drivers.reference(after),
            'authorization_before':self.s.work_units.auth_ref(old_auth),
            'authorization_after':self.s.work_units.auth_ref(auth),
            'usage':body['transition']['usage_at_transition'], 'pause_reason':PAUSE_REASON,
            'save_event':save_event})
        consumption = versioned({**self._common(CONSUMPTION, body),
            'proposal':body['proposal'], 'application':{'id':KIND_FOR[APPLICATION]+':'+application['version'],
                'version':application['version']}, 'from_plan':body['parent_plan'], 'to_plan':plan_ref(child),
            'source':body['transition']['source'], 'command_id':candidate['command_id']})
        return application, consumption

    def read(self, db, plan):
        """Verify the no-run first edge or reconstruct all saved successor layers."""
        try:
            applications=self._rows(db,KIND_FOR[APPLICATION],plan['item_id'])
            if (len(applications)>1 or any(r['plan_id']==plan['id'] for r in self.s.work_units.rows(db,plan['item_id'])) or
                    (applications and self.s.work_drivers.reference(self.s.work_drivers.current(db,plan['item_id'])) != applications[0]['snapshot']['driver_after'])):
                from .work_coordinator_handoff_adjustment_path import WorkCoordinatorHandoffAdjustmentPath
                h=WorkCoordinatorHandoffAdjustmentPath(self.s).read(db,plan)
                current=next(a for a in applications if a['snapshot']['child_plan']==plan_ref(plan))
                consumption=self.records.get(db,CONSUMPTION,h['saved_path']['edges'][-1]['consumption'])
                return {**h,'history':{k:h[k] for k in HISTORY_FIELDS},
                    'current_material_observation':h['material_observation'],'saved_edge_verified':True,
                    'successor_layers_verified':True,'verified_scope':'SAVED_SUCCESSOR_LAYERS', 'public_apply':False,
                    'application':current,'consumption':consumption,
                    'candidate':self.records.get(db,CANDIDATE,current['snapshot']['candidate'])}
            return self._read(db, plan)
        except (KeyError, TypeError, AttributeError, LookupError, StopIteration) as exc:
            raise ValueError('正式调整首边真实记录或完整来源无效。') from exc

    def _read(self, db, plan):
        changes = db.total_changes
        link = plan['coordinator_file_handoff_lineage']
        historical = self.sources.read_prepared(db, link['candidate'])
        candidate = historical['candidate']; body = candidate['snapshot']
        checkpoint = historical['checkpoint_record']; history = historical['history']
        parent = checkpoint['snapshot']['plan']; item_id = parent['item_id']
        self._plan_ids = {parent['id'], plan['id']}
        if (body['parent_plan'] != body['root'] or plan != historical['child_plan'] or
                self.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan or
                self.s.teamwork.current(db, item_id) != plan or
                self._rows(db, 'teamwork_plan', item_id) != [p for p in
                    self._rows(db, 'teamwork_plan', item_id) if p in (parent, plan)] or
                len(self._rows(db, 'teamwork_plan', item_id)) != 2):
            raise ValueError('首边须绑定全部准确原根与唯一后继，不能忽略其它方案。')
        rows = self.s.work_units.rows(db, item_id)
        if rows != [f['run'] for f in historical['frames']]:
            raise ValueError('完整后继运行读取尚未实现；不能忽略后继或额外运行。')
        # read_prepared partitions original records at the checkpoint. No later
        # delivery/completion can be silently excluded from this saved scope.
        for kind, name, event in ((DELIVERY_KIND,'delivery','coordinator_handoff_delivered'),
                                 (COMPLETION_KIND,'completion','coordinator_handoff_completed')):
            expected = history[name]
            actual = self._rows(db,kind,item_id)
            if (len(actual) != len(expected) or any(r not in expected for r in actual) or
                    self._events(db,event,item_id) != [(self.sources._event(db,event,r),r) for r in expected]):
                raise ValueError('保存首边不能遗漏检查点后的正式交付或完成记录。')
        applications = self._rows(db, KIND_FOR[APPLICATION], item_id)
        consumptions = self._rows(db, KIND_FOR[CONSUMPTION], item_id)
        if len(applications) != 1 or len(consumptions) != 1:
            raise ValueError('正式首边须有唯一准确应用与消费，不能忽略其它保存层。')
        application = self.records.get(db, APPLICATION, self.records.ref(applications[0]))
        consumption = self.records.get(db, CONSUMPTION, self.records.ref(consumptions[0]))
        a = application['snapshot']
        after = self.sources.old._driver(db, plan, a['driver_after'])
        auth = self.sources.old._auth(db, plan, a['authorization_after'])
        adjacent(parent, plan, candidate, history['authorization'], auth, history['driver'], after)
        drivers = self._rows(db, 'work_driver_revision', item_id)
        original_drivers = []
        for row in drivers:
            target = parent if row['plan_id'] == parent['id'] else plan
            value = self.sources.old._driver(db, target, {'id':row['driver_id'],
                'generation':row['generation'], 'revision':row['revision']})
            event = self.sources._driver_event(db, value)
            if target == parent:
                if event >= historical['checkpoint_event_seq']:
                    raise ValueError('原根检查点后责任历史已变化。')
                original_drivers.append(value)
            elif value != after:
                raise ValueError('后继责任还有未重建版本。')
        authorizations = self._rows(db, 'work_unit_authorization_revision', item_id)
        expected_auths = [{**v, 'id':'unit-auth-version:'+v['revision']}
                          for v in (history['authorization'], auth)]
        if (len(authorizations) != 2 or any(v not in expected_auths for v in authorizations) or
                len(drivers) != len(original_drivers)+1 or
                self._rows(db,'work_driver',item_id) != [after] or
                self._rows(db,'work_unit_authorization',item_id) != [auth] or
                self.s.work_drivers.current(db,item_id) != after or
                self.s.work_units.authorization(db,after) != auth or
                self.s.work_items.item(db,item_id)['revision'] != after['item_revision']):
            raise ValueError('首边当前责任、完整原授权版本或任务修订不一致。')
        driver_events = [json.loads(r['payload']) for r in db.execute(
            "SELECT payload FROM events WHERE kind LIKE 'work_driver_%'")
            if json.loads(r['payload']).get('item_id') == item_id]
        if (sorted(map(digest,driver_events)) != sorted(digest({**v,'no_dispatch':True}) for v in original_drivers+[after]) or
                self._events(db,'cooperation_authorized',item_id) !=
                    [(self.sources._event(db,'cooperation_authorized',history['authorization']),history['authorization'])]):
            raise ValueError('全部责任或原授权事件还有未重建版本。')
        payload = {'root':body['root'], 'parent_plan':body['parent_plan'], 'child_plan':plan_ref(plan),
            'candidate':self.records.ref(candidate), 'driver':self.s.work_drivers.reference(after),
            'authorization':self.s.work_units.auth_ref(auth), 'command_id':candidate['command_id'], 'no_dispatch':True}
        saves = self._events(db,SAVE_EVENT,item_id)
        if len(saves) != 1 or saves[0][1] != payload:
            raise ValueError('缺少唯一准确正式保存事件，或存在其它保存层。')
        save_seq = saves[0][0]
        expected_application, expected_consumption = self._bodies(candidate,checkpoint,plan,
            history['driver'],after,history['authorization'],auth,
            {'seq':save_seq,'kind':SAVE_EVENT,'payload_digest':digest(payload)})
        app_seq = self.sources._event(db,EVENTS[APPLICATION],application)
        consumed_seq = self.sources._event(db,EVENTS[CONSUMPTION],consumption)
        if (a != expected_application or consumption['snapshot'] != expected_consumption or
                not self.sources._event(db,EVENTS[CANDIDATE],candidate) <
                    self.sources._driver_event(db,after) < save_seq < app_seq < consumed_seq or
                any(r['command_id'] != candidate['command_id'] or r['item_id'] != item_id
                    for r in (application,consumption)) or
                not candidate['created'] <= auth['transferred_at'] <= after['updated'] <=
                    application['created'] <= consumption['created'] < auth['dispatch_before'] or
                len(self._events(db,EVENTS[APPLICATION],item_id)) != 1 or
                len(self._events(db,EVENTS[CONSUMPTION],item_id)) != 1):
            raise ValueError('应用、唯一消费、实际事件顺序或保存时间不一致。')
        proposal = self.s.collaboration.get(db,'work_proposal',body['proposal']['id'])
        checked = self._events(db,'coordinator_proposal_checked',item_id)
        if (not checked or checked[-1][1] != proposal or
                self.s.work_proposals.current(db,item_id) != proposal):
            raise ValueError('首边后还有未重建的实际提案或提案指针。')
        require_unconsumed(self.s,db,body['proposal'],item_id,allowed=consumption)
        usage = self.s.work_units.usage(db,rows)
        if usage != a['usage'] or usage != checkpoint['snapshot']['usage']:
            raise ValueError('正式保存首边完整原累计已变化。')
        if (db.total_changes != changes or self.sources.read_prepared(db,link['candidate']) != historical or
                self.s.teamwork.current(db,item_id) != plan):
            raise ValueError('保存首边最终历史、材料或当前方案已变化。')
        return deepcopy({'root':body['root'], 'plan':plan, 'candidate':candidate,
            'application':application, 'consumption':consumption, 'history':history,
            'frames':historical['frames'], 'driver':after, 'authorization':auth, 'usage':usage,
            'current_material_observation':historical['current_material_observation'],
            'saved_edge_verified':True, 'verified_scope':'FIRST_SAVED_EDGE_WITHOUT_SUCCESSOR_RUNS',
            'successor_layers_verified':False, 'public_apply':False, **PERMISSIONS})

    def apply(self, db, candidate_ref):
        """Internal only. Same exact command replays the verified persisted edge."""
        db.execute('SAVEPOINT coordinator_adjustment_apply')
        try:
            result = self._apply(db,candidate_ref)
        except BaseException:
            db.execute('ROLLBACK TO coordinator_adjustment_apply')
            db.execute('RELEASE coordinator_adjustment_apply')
            raise
        db.execute('RELEASE coordinator_adjustment_apply')
        return result

    def _apply(self, db, candidate_ref):
        candidate = self.records.get(db,CANDIDATE,candidate_ref)
        self._plan_ids = {candidate['snapshot']['root']['id'],candidate['snapshot']['parent_plan']['id']}
        if candidate['command_id'] != self.s.command_identity:
            raise ValueError('调整保存须使用准备候选的准确command。')
        existing = self._rows(db,KIND_FOR[APPLICATION],candidate['item_id'])
        matches=[r for r in existing if r['snapshot']['candidate']==candidate_ref]
        if matches:
            if len(matches)!=1: raise ValueError('候选存在重复正式保存边。')
            plan = self.s.collaboration.get(db,'teamwork_plan',matches[0]['snapshot']['child_plan']['id'])
            return self.read(db,plan)
        ready = self.candidates.require_save(db,candidate_ref)
        h = ready['history']; child = ready['child_plan']; body = candidate['snapshot']
        self.s.work_transitions.save_plan(db,self.s.work_items.item(db,candidate['item_id']),
            h['driver'],ready['checkpoint']['snapshot']['plan'],h['authorization'],
            body['candidate_units'],body['carry_sources'],body['transition'],
            extra={k:child[k] for k in ('historical_coordinators','coordinator_file_handoff_lineage')},
            reason=PAUSE_REASON,handoff_candidate=candidate_ref)
        return self.read(db,child)

    def save(self, db, candidate_ref, item, driver, plan, auth, units, lineage, transition, extra, reason):
        """Special save_plan dispatch; never route this through the fixed kind."""
        db.execute('SAVEPOINT coordinator_adjustment_save')
        try:
            result = self._save(db,candidate_ref,item,driver,plan,auth,units,lineage,transition,extra,reason)
        except BaseException:
            db.execute('ROLLBACK TO coordinator_adjustment_save')
            db.execute('RELEASE coordinator_adjustment_save')
            raise
        db.execute('RELEASE coordinator_adjustment_save')
        return result

    def _save(self, db, candidate_ref, item, driver, plan, auth, units, lineage, transition, extra, reason):
        ready = self.candidates.require_save(db,candidate_ref)
        historical = self.sources.read_prepared(db,candidate_ref)
        candidate = ready['candidate']; body = candidate['snapshot']; child = ready['child_plan']; h = ready['history']
        self._plan_ids = {plan['id'],child['id']}
        if (candidate['command_id'] != self.s.command_identity or
                item != self.s.work_items.item(db,candidate['item_id']) or
                driver != h['driver'] or auth != h['authorization'] or plan != ready['checkpoint']['snapshot']['plan'] or
                units != body['candidate_units'] or lineage != body['carry_sources'] or transition != body['transition'] or
                extra != {k:child[k] for k in ('historical_coordinators','coordinator_file_handoff_lineage')} or
                reason != PAUSE_REASON or historical['child_plan'] != child):
            raise ValueError('专用调整保存参数不等于完整准确候选。')
        if any(r['snapshot']['proposal']==body['proposal'] for protocol in (APPLICATION,CONSUMPTION)
               for r in self._rows(db,KIND_FOR[protocol],item['id'])):
            raise ValueError('正式保存或消费已存在。')
        now = time.time()
        transferred = {**auth, 'revision':uid(), 'plan_id':child['id'],'plan_version':child['version'],
            'previous_authorization':self.s.work_units.auth_ref(auth), 'transferred_at':now,
            'transfer_proposal':body['proposal'], 'usage_at_transfer':body['transition']['usage_at_transition'],
            'command_id':self.s.command_identity}
        self.s.collaboration.put(db,'teamwork_plan',child)
        self.s.collaboration.put(db,'teamwork_current',{'id':'teamwork-current:'+item['id'],'plan_id':child['id']})
        self.s.collaboration.put(db,'work_unit_authorization',transferred)
        self.s.collaboration.put(db,'work_unit_authorization_revision',
            {**transferred,'id':'unit-auth-version:'+transferred['revision']})
        updated = self.s.work_drivers.save(db,{**driver,'plan_id':child['id'],'plan_version':child['version'],
            'state':'PAUSED','reason':PAUSE_REASON,'previous_plan_reference':plan_ref(plan),
            'transition_proposal':body['proposal']},'coordinator_handoff_adjusted')
        adjacent(plan,child,candidate,auth,transferred,driver,updated)
        payload = {'root':body['root'],'parent_plan':body['parent_plan'],'child_plan':plan_ref(child),
            'candidate':candidate_ref,'driver':self.s.work_drivers.reference(updated),
            'authorization':self.s.work_units.auth_ref(transferred),'command_id':self.s.command_identity,'no_dispatch':True}
        self.s.store.event(None,None,SAVE_EVENT,payload,db)
        seq = self.sources._event(db,SAVE_EVENT,payload)
        app_body, consumed_body = self._bodies(candidate,ready['checkpoint'],child,driver,updated,auth,transferred,
            {'seq':seq,'kind':SAVE_EVENT,'payload_digest':digest(payload)})
        for protocol, snapshot in ((APPLICATION,app_body),(CONSUMPTION,consumed_body)):
            row = self.records.put(db,protocol,snapshot,item['id'])
            self.s.store.event(None,None,EVENTS[protocol],row,db)
        verified = self.read(db,child)
        if (verified['current_material_observation'] != h['material_observation'] or
                verified['usage'] != body['transition']['usage_at_transition'] or
                time.time() >= auth['dispatch_before']):
            raise ValueError('正式保存最终真实材料、全累计或最初截止已变化。')
        return child,updated,transferred
