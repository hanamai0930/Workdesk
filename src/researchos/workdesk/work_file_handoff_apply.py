"""Immutable explicit fixed candidates, atomic application and saved-source verification.

Original-plan audit keeps its current-original-plan contract. Runtime admission
is independent of immutable historical verification; public UI is separate.
"""
from copy import deepcopy
import time

from .store import digest
from .work_file_handoff_ancestry import PROTOCOL as AUDIT
from .work_file_handoff_revisions import PROTOCOL as PREVIEW, WorkFileHandoffRevisions
from .work_file_sources import resource_status
from .work_transitions import plan_ref, source_ref


CANDIDATE = 'FIXED_FILE_HANDOFF_SAVED_CANDIDATE_V1'
APPLICATION = 'FIXED_FILE_HANDOFF_APPLICATION_V1'
CONSUMPTION = 'FIXED_FILE_HANDOFF_CANDIDATE_CONSUMPTION_V1'
CANDIDATE_KIND = 'work_file_handoff_revision_candidate'
APPLICATION_KIND = 'work_file_handoff_revision_application'
CONSUMPTION_KIND = 'work_file_handoff_revision_consumption'
CURRENT_KIND = 'work_file_handoff_revision_current'
REMOVED_CONTEXT = ('coordination_revision', 'historical_coordinators', 'diagnostic_revision',
                   'rework_lineage', 'unit_revision_lineage', 'file_rework_lineage')


class _ReadProjection:
    """Explicit evidence for one detail assembly; never installed as ambient state."""
    def __init__(self, apps, db, plan):
        self.apps, self.db, self.plan = apps, db, deepcopy(plan)
        self.changes = db.total_changes
        self.problem = None
        self.proof = None
        self.evidence = {}
        try:
            self.proof = apps.verify(db, plan)
            self.evidence = {e['run_id']: e['evidence'] for e in self.proof['frontier']}
        except ValueError as exc:
            self.problem = str(exc)

    def require(self, apps, db, plan):
        if (apps is not self.apps or db is not self.db or self.plan != plan or
                self.changes != db.total_changes):
            raise ValueError('详情观察已脱离本次准确方案或事务，须重新读取。')
        if self.problem:
            raise ValueError(self.problem)
        return deepcopy(self.proof)


class WorkFileHandoffRevisionApplications:
    candidate_protocol = CANDIDATE
    application_protocol = APPLICATION
    consumption_protocol = CONSUMPTION

    def __init__(self, service):
        self.s = service
        self.candidates = WorkFileHandoffRevisions(service)

    @property
    def ledger(self):
        return self.s.work_file_handoff_ledger

    def handlers(self):
        from .work_file_handoff_public import WorkFileHandoffPublic
        public = WorkFileHandoffPublic(self.s)
        return {'work-item.file-handoff-revision-check': public.check,
                'work-item.file-handoff-revision-apply': public.apply,
                'work-item.file-handoff-text-rework-check': public.text_check,
                'work-item.file-handoff-text-rework-apply': public.text_apply,
                'work-item.file-handoff-unit-revision-check': public.unit_check,
                'work-item.file-handoff-unit-revision-apply': public.unit_apply,
                'work-item.file-handoff-file-rework-check': public.file_check,
                'work-item.file-handoff-file-rework-apply': public.file_apply}

    def view(self, db, item):
        from .work_file_handoff_public import WorkFileHandoffPublic
        return WorkFileHandoffPublic(self.s).view(db, item)

    def _original_view(self, db, item):
        plan = self.s.teamwork.current(db, item['id'])
        if not plan or not plan.get('file_handoffs'):
            return None
        driver = self.s.work_drivers.current(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        original = plan['mode'] == 'fixed' and not plan.get('previous_plan_reference')
        from .work_file_handoff_ancestry import WorkFileHandoffAncestry
        protected = WorkFileHandoffAncestry.protected_keys(plan['units'],
            [k for r in plan['file_handoffs'] for k in (r['source_unit'], r['target_unit'])])
        editable = [u for u in plan['units'] if original and u['key'] not in protected and
                    u['role'] != 'coordinate' and not (u['outputs'] or u['checks'] or u['write_paths']) and
                    u['dependency_gate'] == 'result']
        _, issues = self.s.work_drivers.readiness(db, item, driver)
        can_check = bool(editable and driver and driver['state'] == 'RESERVED' and
                         auth and not issues and driver['item_revision'] == item['revision'] and
                         driver['plan_id'] == plan['id'] and driver['plan_version'] == plan['version'] and
                         not (self.s.work_flows.current(db, item['id']) or {}).get('state') == 'RUNNING')
        value = {'state': 'NOT_CHECKED', 'record': None, 'reference': None, 'problems': [],
                 'can_check': can_check, 'can_apply': False, 'protected_keys': sorted(protected),
                 'work_requirements_version': digest(plan['work_requirements']),
                 'edit_units': [{**{k: u[k] for k in ('key','title','goal','acceptance','depends')},
                                'revision': digest(u), 'member': u['member']['reference']} for u in editable],
                 'no_dispatch': True}
        try:
            row = self.current(db, item['id'])
            if not row:
                return value
            value.update(record=row, reference=self.ledger.reference(row))
            if self.consumptions(db, value['reference']):
                value.update(state='CONSUMED', can_check=False)
            else:
                preview = self.candidates.recheck(db, row['snapshot']['preview'])
                value.update(state='VALID', can_apply=bool(can_check and not preview['execution_blockers']),
                             problems=preview['execution_blockers'])
        except ValueError as exc:
            value.update(state='STALE', problems=[str(exc)])
        return value

    def current(self, db, item_id):
        rows = db.execute('SELECT data FROM collaboration WHERE kind=? AND id=?',
                          (CURRENT_KIND, 'file-handoff-revision-current:' + item_id)).fetchall()
        if not rows:
            return None
        pointer = self.s.collaboration.get(db, CURRENT_KIND, 'file-handoff-revision-current:' + item_id)
        row = self.ledger.get(db, CANDIDATE_KIND, pointer['candidate'])
        if row['item_id'] != item_id:
            raise ValueError('交接候选当前指针归属不一致。')
        return row

    def check(self, db, request):
        preview = self.candidates.preview(db, request)
        row = self.ledger.put(db, CANDIDATE_KIND, {'protocol': self.candidate_protocol,
            'item_id': request['id'], 'preview': preview})
        self.s.collaboration.put(db, CURRENT_KIND, {'id': 'file-handoff-revision-current:' + request['id'],
            'candidate': self.ledger.reference(row)})
        self.s.store.event(None, None, 'file_handoff_revision_candidate_checked',
                           {'candidate': self.ledger.reference(row), 'no_dispatch': True}, db)
        return {'candidate': self.ledger.reference(row), 'record': row, 'no_dispatch': True,
                'save_permission': False, 'execution_permission': False}

    def consumptions(self, db, reference):
        return [r for r in self.s.collaboration.all(db, CONSUMPTION_KIND)
                if r['snapshot'].get('candidate') == reference]

    def selected(self, db, reference):
        row = self.ledger.get(db, CANDIDATE_KIND, reference)
        if (row['snapshot'].get('protocol') != self.candidate_protocol or
                self.current(db, row['item_id']) != row or self.consumptions(db, reference)):
            raise ValueError('须使用最新、未消费的准确明确交接候选。')
        preview = self.candidates.recheck(db, row['snapshot']['preview'])
        if preview['execution_blockers']:
            raise ValueError('原累计授权或派发截止不足，不能应用交接候选。')
        return row, preview

    def contract(self, db, reference):
        row, preview = self.selected(db, reference)
        record = {'id': 'file-handoff-revision:' + row['version'], 'protocol': self.application_protocol,
                  'candidate': reference, 'base_plan': preview['base_plan'],
                  'candidate_version': preview['version'], 'proof': preview['ancestry_audit'],
                  'original_authorization': preview['authorization'], 'original_driver': preview['driver'],
                  'reason': preview['reason'], 'command_id': self.s.command_identity, 'no_dispatch': True}
        transition = {'kind': 'EXPLICIT_FIXED_FILE_HANDOFF_REVISION', 'candidate': reference,
                      'file_handoff_revision': record['id'], 'usage_at_transition': preview['usage'],
                      'authorization_from': self.s.work_units.auth_ref(preview['authorization']),
                      'command_id': self.s.command_identity, 'no_dispatch': True}
        lineage = {e['key']: e['source'] for e in preview['units'] if e['state'] == 'ELIGIBLE_RESULT'}
        return preview, lineage, transition, {'file_handoff_lineage': record}

    def require_save(self, db, reference, item, driver, plan, auth, units, lineage, transition, extra):
        """A reference alone is insufficient: independently recompute every save argument."""
        preview, expected_lineage, expected_transition, expected_extra = self.contract(db, reference)
        if (item != self.s.work_items.item(db, preview['request']['id']) or
                item['revision'] != preview['item_revision'] or plan_ref(plan) != preview['base_plan'] or
                self.s.teamwork.current(db, item['id']) != plan or
                self.s.work_drivers.reference(driver) != preview['driver'] or
                self.s.work_drivers.current(db, item['id']) != driver or auth != preview['authorization'] or
                units != preview['candidate_units'] or lineage != expected_lineage or
                transition != expected_transition or extra != expected_extra):
            raise ValueError('交接保存的单元、来源、责任、授权、切换或完整证明与重算候选不一致。')

    def apply(self, db, p):
        fields = {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'candidate'}
        if not isinstance(p, dict) or set(p) != fields:
            raise ValueError('交接应用只接受准确任务、责任、方案、授权、前沿和候选引用。')
        row, preview = self.selected(db, p['candidate'])
        if {k: p[k] for k in fields - {'candidate'}} != {k: preview['request'][k] for k in fields - {'candidate'}}:
            raise ValueError('交接应用的准确请求与候选绑定不一致。')
        item, driver, plan = self.s.work_units.checked(db, p)
        auth = self.s.work_units.authorization(db, driver)
        preview, lineage, transition, extra = self.contract(db, p['candidate'])
        new, paused, transferred = self.s.work_transitions.save_plan(db, item, driver, plan, auth,
            preview['candidate_units'], lineage, transition, extra=extra,
            handoff_candidate=p['candidate'], reason='FILE_HANDOFF_REVISED_EXPLICIT_RECHECK_REQUIRED')
        # Final plan identity lives outside its own digest; no to_plan cycle.
        application = self.ledger.put(db, APPLICATION_KIND, {'protocol': self.application_protocol, 'item_id': item['id'],
            'lineage': extra['file_handoff_lineage'], 'to_plan': plan_ref(new),
            'authorization': transferred, 'paused_driver': paused, 'created': new['created'],
            'command_id': self.s.command_identity, 'no_dispatch': True})
        consumption = self.ledger.put(db, CONSUMPTION_KIND, {'protocol': self.consumption_protocol,
            'item_id': item['id'], 'candidate': p['candidate'], 'application': self.ledger.reference(application),
            'from_plan': plan_ref(plan), 'to_plan': plan_ref(new),
            'driver': self.s.work_drivers.reference(paused), 'authorization': self.s.work_units.auth_ref(transferred),
            'command_id': self.s.command_identity, 'no_dispatch': True})
        self.verify(db, new)
        self.s.store.event(None, None, 'file_handoff_revision_applied',
                           {'application': self.ledger.reference(application), 'no_dispatch': True}, db)
        return {'application': self.ledger.reference(application), 'transition': self.ledger.reference(consumption),
                'plan': plan_ref(new), 'driver': self.s.work_drivers.reference(paused),
                'authorization': self.s.work_units.auth_ref(transferred), 'no_dispatch': True,
                'automatically_resumed': False}

    def verify(self, db, plan):
        # No success cache crosses observations, writes or independent uses.
        with self.s.work_unit_revision_applications.validation_snapshot(db):
            return self._verify(db, plan)

    def read_projection(self, db, plan):
        from .work_file_handoff_chain_runtime import uses_path, _RuntimeObservation
        if uses_path(self, db, plan):
            return _RuntimeObservation(self, db, plan)
        return _ReadProjection(self, db, plan) if plan and plan.get('file_handoff_lineage') else None

    def require_sources(self, db, plan, *, _read=None):
        if not plan.get('file_handoff_lineage'):
            return None
        from .work_file_handoff_chain_runtime import uses_path, observe
        if _read is None and uses_path(self, db, plan):
            _read = observe(self, db, plan)
        return _read.require(self, db, plan) if _read is not None else self.verify(db, plan)

    def require_local_identity(self, db, plan, run):
        """Reject malformed/new local identities before any file-history lookup."""
        if not plan.get('file_handoff_lineage') or run['plan_id'] != plan['id']:
            return
        try:
            unit = next((u for u in plan['units'] if u['key'] == run['unit_key']), None)
            task = self.s.store.task(run['task_id'], db)
            spec = task['spec']
            valid = (unit is not None and run['item_id'] == plan['item_id'] and
                run['plan_version'] == plan['version'] and run['unit_revision'] == digest(unit) and
                run['input']['unit'] == unit and run['input_version'] == digest(run['input']) and
                isinstance(run['input']['dependencies'], list) and isinstance(run['input']['file_artifacts'], list) and
                run['input']['work_requirements'] == plan['work_requirements'] and
                all(run['cooperative'].get(k) == run[k] for k in
                    ('item_id', 'plan_id', 'plan_version', 'unit_key', 'unit_revision', 'input_version')) and
                all(isinstance(d, dict) and isinstance(d.get('unit_key'), str) and 'version' in d and
                    isinstance(d.get('file_artifacts'), dict) and 'state' in d['file_artifacts'] and
                    isinstance(d['file_artifacts'].get('manifest'), dict) for d in run['input']['dependencies']) and
                all(isinstance(d, dict) and isinstance(d.get('manifest'), dict) for d in run['input']['file_artifacts']) and
                isinstance(spec.get('materials'), dict) and spec.get('cooperative') == run['cooperative'] and
                run.get('task_spec_version') == digest(spec) and task['spec_hash'] == digest(spec))
        except (KeyError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise ValueError('交接修订新运行的完整spec身份、单元或冻结输入版本不一致。')

    def admit(self, db, item, plan, unit, *, reservation=None, _read=None):
        """Fresh sources plus current authority; exclude only the checked reservation."""
        from .work_file_handoff_chain_runtime import uses_path, observe
        if uses_path(self, db, plan) and (_read is None or reservation is not None):
            _read = observe(self, db, plan, reservation=reservation)
        proof = self.require_sources(db, plan, _read=_read)
        if not proof:
            return
        if unit['key'] in {proof['retired_source'], proof['completed_key']} or unit['role'] == 'coordinate':
            raise ValueError('交接两端已保留为历史前版及完成后继，不能重新派发或捕获。')
        driver = self.s.work_drivers.current(db, item['id'])
        application = self.ledger.get(db, APPLICATION_KIND, proof['application'])['snapshot']
        paused = application['paused_driver']
        auth = self.s.work_units.authorization(db, driver)
        if (not driver or driver['id'] != paused['id'] or driver['generation'] != paused['generation'] or
                driver['state'] != 'RESERVED' or driver['item_revision'] != item['revision'] or
                driver['plan_id'] != plan['id'] or driver['plan_version'] != plan['version'] or
                auth != application['authorization'] or auth['item_id'] != item['id'] or
                auth['driver_generation'] != driver['generation']):
            raise ValueError('交接修订当前责任或原累计授权已变化，不能扩大或重授。')
        rows = self.s.work_units.rows(db, item['id'])
        excluding = set()
        if reservation is not None:
            if (self.s.work_units.reservation(db, reservation['attempt_id']) != reservation or
                    reservation['command_id'] != self.s.command_identity or reservation['item_id'] != item['id'] or
                    reservation['plan_id'] != plan['id'] or reservation['plan_version'] != plan['version'] or
                    reservation['unit_key'] != unit['key'] or reservation['authorization'] != self.s.work_units.auth_ref(auth) or
                    reservation['generation'] != driver['generation'] or reservation['driver_revision'] != driver['revision']):
                raise ValueError('交接修订当前准确reservation与任务、责任或授权不一致。')
            excluding.add(reservation['attempt_id'])
        from .work_file_reworks import original_files
        from .native_permissions import choice
        current_rows = [r for r in rows if r['plan_id'] == plan['id']]
        for run in current_rows:
            self.require_local_identity(db, plan, run)
        completed_rows = [r for r in current_rows if not reservation or r['id'] != reservation['id']]
        self.ledger.history(db, completed_rows)
        seen = set()
        for run in current_rows:
            definition = next((u for u in plan['units'] if u['key'] == run['unit_key']), None)
            task = self.s.store.task(run['task_id'], db)
            spec = task['spec']
            if (not definition or run['unit_key'] in seen or run['unit_key'] in {proof['retired_source'], proof['completed_key']} or
                    definition['role'] == 'coordinate' or run['retry_of'] is not None or
                    run['plan_version'] != plan['version'] or run['unit_revision'] != digest(definition) or
                    run['authorization'] != self.s.work_units.auth_ref(auth) or run['generation'] != driver['generation'] or
                    run['input'].get('file_handoff_intent') or run['input']['unit'] != definition or
                    run['input_version'] != digest(run['input']) or spec.get('cooperative') != run['cooperative'] or
                    run.get('task_spec_version') != digest(spec) or task['spec_hash'] != digest(spec) or
                    task['project_id'] != item['project_id'] or spec.get('runtime_mode') != 'official' or
                    spec.get('work_mode') != 'cooperative' or spec.get('access') != choice(None) or
                    spec.get('native') != {'configuration':'VENDOR_DEFAULTS'} or spec.get('budget') != {} or
                    spec.get('writable') != [] or spec.get('checks') != [] or
                    self.s.store.attempt(run['attempt_id'], db)['workspace'] !=
                        self.s.work_file_rework_applications.root(db, item) or
                    original_files(self.s, db, run)['input_problems']):
                raise ValueError('交接修订新运行的完整身份、原权限、目录、材料或原累计授权不一致。')
            seen.add(run['unit_key'])
            self.s.work_transitions.validate_run(db, plan, definition, run, rows, frozenset(), _read=_read)
        count = len(rows) - bool(reservation)
        if count >= auth['max_attempts_total'] or time.time() >= auth['dispatch_before']:
            raise ValueError('交接修订原累计上限已耗尽或新增派发截止已到期。')
        self.s.work_flows.require_stopped(db, item['id'])
        _, issues = self.s.work_drivers.readiness(db, item, driver, excluding=excluding)
        if issues:
            raise ValueError('交接修订运行准入阻塞：' + '；'.join(dict.fromkeys(i['message'] for i in issues)))
        if reservation is not None:
            frozen = self.s.work_units.input_for(db, item, plan, unit, rows, _read=_read)
            if reservation['input'] != frozen or reservation['input_version'] != digest(frozen):
                raise ValueError('交接修订捕获后的冻结输入与最新准确来源不一致。')
            task = self.s.store.task(reservation['task_id'], db)
            if (self.s.store.attempt(reservation['attempt_id'], db)['workspace'] !=
                    self.s.work_file_rework_applications.root(db, item) or
                    task['spec'].get('cooperative') != reservation['cooperative'] or
                    original_files(self.s, db, reservation)['input_problems']):
                raise ValueError('交接修订捕获后的原目录、实际材料或reservation身份已变化。')

    def _verify(self, db, plan):
        record = plan.get('file_handoff_lineage')
        if not record:
            raise ValueError('当前方案没有正式文件交接修订记录。')
        if (record.get('protocol') != APPLICATION or
                digest({k: v for k, v in plan.items() if k != 'version'}) != plan['version'] or
                self.s.teamwork.current(db, plan['item_id']) != plan or
                self.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan):
            raise ValueError('正式交接修订方案、版本或当前归属不一致。')
        candidate = self.ledger.get(db, CANDIDATE_KIND, record['candidate'])
        preview = candidate['snapshot']['preview']
        self.ledger.require_version(preview, PREVIEW)
        audit = record['proof']
        self.ledger.require_version(audit, AUDIT)
        original = self.s.collaboration.get(db, 'teamwork_plan', record['base_plan']['id'])
        if (candidate['snapshot']['protocol'] != CANDIDATE or candidate['item_id'] != plan['item_id'] or
                self.current(db, plan['item_id']) != candidate or
                preview['version'] != record['candidate_version'] or audit != preview['ancestry_audit'] or
                preview['base_plan'] != record['base_plan'] or plan_ref(original) != record['base_plan'] or
                preview['authorization'] != record['original_authorization'] or
                preview['driver'] != record['original_driver'] or preview['reason'] != record['reason'] or
                preview['execution_blockers'] or audit['state'] != 'COMPLETED_VALID' or not audit['carry_candidate']):
            raise ValueError('正式交接记录未准确绑定原方案、完整候选和完成证明。')
        if self.ledger.relation(original, audit['relation']['target_unit']) != audit['relation']:
            raise ValueError('正式交接证明与准确原关系不一致。')
        saved = [r for r in self.s.collaboration.all(db, APPLICATION_KIND)
                 if r['snapshot'].get('lineage', {}).get('id') == record['id']]
        if len(saved) != 1:
            raise ValueError('正式交接修订须有唯一不可变应用记录。')
        application = self.ledger.get(db, APPLICATION_KIND, self.ledger.reference(saved[0]))
        value = application['snapshot']
        if (value['protocol'] != APPLICATION or value['lineage'] != record or
                value['item_id'] != plan['item_id'] or value['to_plan'] != plan_ref(plan) or
                value['created'] != plan['created'] or value['command_id'] != record['command_id'] or
                value['no_dispatch'] is not True):
            raise ValueError('不可变应用记录与最终方案或嵌入证明不一致。')
        transition = {'kind': 'EXPLICIT_FIXED_FILE_HANDOFF_REVISION', 'candidate': record['candidate'],
                      'file_handoff_revision': record['id'], 'usage_at_transition': preview['usage'],
                      'authorization_from': self.s.work_units.auth_ref(preview['authorization']),
                      'command_id': record['command_id'], 'no_dispatch': True}
        lineage = {e['key']: e['source'] for e in preview['units'] if e['state'] == 'ELIGIBLE_RESULT'}
        expected = {**original, 'id': plan['id'], 'item_revision': preview['item_revision'],
                    'units': preview['candidate_units'], 'previous_plan': original['id'],
                    'previous_plan_reference': plan_ref(original), 'created': plan['created'],
                    'state': 'PREPARED', 'result_lineage': lineage, 'transition': transition}
        for key in (*REMOVED_CONTEXT, 'version'):
            expected.pop(key, None)
        expected['file_handoff_lineage'] = record
        expected['version'] = digest(expected)
        if plan != expected or preview['retired_source'] in lineage:
            raise ValueError('正式交接修订完整方案或准确成果沿袭不一致。')
        auth, paused = value['authorization'], value['paused_driver']
        expected_auth = {**preview['authorization'], 'revision': auth['revision'],
            'plan_id': plan['id'], 'plan_version': plan['version'],
            'previous_authorization': self.s.work_units.auth_ref(preview['authorization']),
            'transferred_at': plan['created'], 'transfer_proposal': None,
            'usage_at_transfer': preview['usage'], 'command_id': record['command_id']}
        old_auth = self.s.collaboration.get(db, 'work_unit_authorization_revision',
                            'unit-auth-version:' + preview['authorization']['revision'])
        new_auth = self.s.collaboration.get(db, 'work_unit_authorization_revision',
                            'unit-auth-version:' + auth['revision'])
        old_driver = self.s.collaboration.get(db, 'work_driver_revision',
                            'driver-version:' + preview['driver']['revision'])
        paused_history = self.s.collaboration.get(db, 'work_driver_revision', 'driver-version:' + paused['revision'])
        expected_paused = {**old_driver, 'id': preview['driver']['id'], 'revision': paused['revision'],
            'updated': paused['updated'], 'plan_id': plan['id'], 'plan_version': plan['version'],
            'state': 'PAUSED', 'reason': 'FILE_HANDOFF_REVISED_EXPLICIT_RECHECK_REQUIRED',
            'previous_plan_reference': plan_ref(original), 'transition_proposal': None}
        expected_paused.pop('driver_id', None)
        if (auth != expected_auth or old_auth != {**preview['authorization'], 'id': old_auth['id']} or
                new_auth != {**auth, 'id': new_auth['id']} or paused != expected_paused or
                self.s.work_drivers.reference({**old_driver, 'id': old_driver['driver_id']}) != preview['driver'] or
                paused_history != {**paused, 'id': paused_history['id'], 'driver_id': paused['id']}):
            raise ValueError('原授权转移或同generation暂停责任的完整历史不一致。')
        consumed = self.consumptions(db, record['candidate'])
        if len(consumed) != 1:
            raise ValueError('正式交接候选须且仅能消费一次。')
        consumed = self.ledger.get(db, CONSUMPTION_KIND, self.ledger.reference(consumed[0]))
        expected_consumption = {'protocol': CONSUMPTION, 'item_id': plan['item_id'],
            'candidate': record['candidate'], 'application': self.ledger.reference(application),
            'from_plan': plan_ref(original), 'to_plan': plan_ref(plan),
            'driver': self.s.work_drivers.reference(paused), 'authorization': self.s.work_units.auth_ref(auth),
            'command_id': record['command_id'], 'no_dispatch': True}
        if consumed['snapshot'] != expected_consumption:
            raise ValueError('正式消费记录的应用、责任、授权或最终方案不一致。')
        item = self.s.work_items.item(db, plan['item_id'])
        if (item['project_id'] != original['project_id'] or
                {k: item[k] for k in ('title', 'goal', 'constraints_text', 'acceptance')} != original['work_requirements'] or
                self.s.work_file_rework_applications.root(db, item) != audit['project_root']):
            raise ValueError('交接修订原任务要求、项目或实际目录已变化。')
        rows = self.s.work_units.rows(db, plan['item_id'])
        old_rows = [r for r in rows if r['plan_id'] == original['id']]
        if (any(r['plan_id'] not in {original['id'], plan['id']} for r in rows) or
                self.ledger.history(db, old_rows) != audit['protected_history'] or
                resource_status(self.s, db, [original]) != audit['resources']):
            raise ValueError('交接修订全部原运行、输入、旧快照、目录或资源已变化。')
        if any(not r['valid'] for r in audit['resources']):
            raise ValueError('交接修订原资源已不可用。')
        frontier = [{'run_id': r['id'], 'evidence': self.s.work_units.evidence(db, r)} for r in old_rows]
        if frontier != audit['frontier']:
            raise ValueError('交接修订准确原前沿或当前文件已变化。')
        if self.s.work_units.usage(db, old_rows) != preview['usage']:
            raise ValueError('交接修订原累计消耗已变化。')
        old_units = {u['key']: u for u in original['units']}
        for run in old_rows:
            self.s.work_transitions.validate_run(db, original, old_units[run['unit_key']], run,
                                                rows, frozenset(), consumer_plan=original)
        source = next(r for r in old_rows if r['id'] == audit['historical_source']['run_id'])
        target = next(r for r in old_rows if r['id'] == audit['completed_target']['source']['run_id'])
        proof = self.ledger.match(db, audit['completion'], target, source,
                                  audit['historical_source']['evidence']['version'])
        target_evidence = next(e['evidence'] for e in frontier if e['run_id'] == target['id'])
        if (proof['delivery'] != audit['delivery'] or proof['completion'] != audit['completion'] or
                source_ref(target, target_evidence) != audit['completed_target']['source'] or
                target_evidence != audit['completed_target']['evidence']):
            raise ValueError('交接修订B来源与原正式交付及完成证明不一致。')
        return deepcopy({'application': self.ledger.reference(application), 'candidate': record['candidate'],
            'original_plan': plan_ref(original), 'consumer_plan': plan_ref(plan),
            'historical_source': audit['historical_source'], 'completed_target': audit['completed_target'],
            'retired_source': preview['retired_source'], 'completed_key': preview['completed_target'],
            'frontier': frontier, 'delivery': audit['delivery'], 'completion': audit['completion'],
            'no_dispatch': True, 'execution_permission': False})
