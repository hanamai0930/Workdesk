"""Internal immutable ordinary unit-revision candidates and atomic, paused application.

No public handler or dispatch grant is registered by this module.
"""
from .work_file_handoff_apply import (
    WorkFileHandoffRevisionApplications, CANDIDATE_KIND, CURRENT_KIND,
    APPLICATION_KIND, CONSUMPTION_KIND)
from .work_file_handoff_unit_revisions import (
    WorkFileHandoffUnitRevisions, CANDIDATE, APPLICATION, CONSUMPTION)
from .work_file_handoff_unit_revision_path import WorkFileHandoffUnitRevisionPath
from .work_transitions import plan_ref


PAUSE_REASON = 'FILE_HANDOFF_UNIT_REVISED_EXPLICIT_RECHECK_REQUIRED'


class WorkFileHandoffUnitRevisionApplications(WorkFileHandoffRevisionApplications):
    candidate_protocol = CANDIDATE
    application_protocol = APPLICATION
    consumption_protocol = CONSUMPTION

    def __init__(self, service):
        super().__init__(service)
        self.candidates = WorkFileHandoffUnitRevisions(service)

    def handlers(self):
        return {}

    def check(self, db, request):
        preview = self.candidates.preview(db, request)
        row = self.ledger.put(db, CANDIDATE_KIND, {'protocol': CANDIDATE,
            'item_id': request['id'], 'preview': preview})
        self.s.collaboration.put(db, CURRENT_KIND, {'id': 'file-handoff-revision-current:' + request['id'],
            'candidate': self.ledger.reference(row)})
        self.s.store.event(None, None, 'file_handoff_unit_revision_candidate_checked',
            {'candidate': self.ledger.reference(row), 'no_dispatch': True}, db)
        self.candidates.recheck(db, preview)
        return {'candidate': self.ledger.reference(row), 'record': row, 'no_dispatch': True,
                'save_permission': False, 'execution_permission': False}

    def contract(self, db, reference):
        row, preview = self.selected(db, reference)
        record = {'id': 'file-handoff-revision:' + row['version'], 'protocol': APPLICATION,
            'candidate': reference, 'base_plan': preview['base_plan'],
            'candidate_version': preview['version'], 'proof': preview['ancestry_audit'],
            'original_authorization': preview['authorization'], 'original_driver': preview['driver'],
            'reason': preview['reason'], 'command_id': self.s.command_identity, 'no_dispatch': True,
            **{k: preview['ancestry_audit'][k] for k in ('parent_application', 'root_application')}}
        transition = {'kind': 'EXPLICIT_FIXED_FILE_HANDOFF_UNIT_REVISION', 'candidate': reference,
            'file_handoff_revision': record['id'], 'usage_at_transition': preview['usage'],
            'authorization_from': self.s.work_units.auth_ref(preview['authorization']),
            'command_id': self.s.command_identity, 'no_dispatch': True}
        lineage = {e['key']: e['source'] for e in preview['units'] if e['state'] == 'ELIGIBLE_RESULT'}
        return preview, lineage, transition, {'file_handoff_lineage': record}

    def apply(self, db, request):
        fields = {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'candidate'}
        if not isinstance(request, dict) or set(request) != fields:
            raise ValueError('普通文字修订应用只接受准确任务、责任、方案、授权、前沿和候选引用。')
        _, preview = self.selected(db, request['candidate'])
        if any(request[k] != preview['request'][k] for k in fields - {'candidate'}):
            raise ValueError('普通文字修订应用的准确请求与候选绑定不一致。')
        item, driver, plan = self.s.work_units.checked(db, request)
        auth = self.s.work_units.authorization(db, driver)
        preview, lineage, transition, extra = self.contract(db, request['candidate'])
        new, paused, transferred = self.s.work_transitions.save_plan(db, item, driver, plan, auth,
            preview['candidate_units'], lineage, transition, extra=extra,
            handoff_candidate=request['candidate'], reason=PAUSE_REASON)
        application = self.ledger.put(db, APPLICATION_KIND, {'protocol': APPLICATION, 'item_id': item['id'],
            'lineage': extra['file_handoff_lineage'], 'to_plan': plan_ref(new),
            'authorization': transferred, 'paused_driver': paused, 'created': new['created'],
            'command_id': self.s.command_identity, 'no_dispatch': True})
        consumption = self.ledger.put(db, CONSUMPTION_KIND, {'protocol': CONSUMPTION,
            'item_id': item['id'], 'candidate': request['candidate'], 'application': self.ledger.reference(application),
            'from_plan': plan_ref(plan), 'to_plan': plan_ref(new),
            'driver': self.s.work_drivers.reference(paused), 'authorization': self.s.work_units.auth_ref(transferred),
            'command_id': self.s.command_identity, 'no_dispatch': True})
        self.verify(db, new)
        self.s.store.event(None, None, 'file_handoff_unit_revision_applied',
            {'application': self.ledger.reference(application), 'no_dispatch': True}, db)
        self.verify(db, new)
        return {'application': self.ledger.reference(application), 'transition': self.ledger.reference(consumption),
            'plan': plan_ref(new), 'driver': self.s.work_drivers.reference(paused),
            'authorization': self.s.work_units.auth_ref(transferred), 'no_dispatch': True,
            'automatically_resumed': False}

    def verify(self, db, plan):
        from .work_file_handoff_chain_runtime import saved_path
        return saved_path(self.s, db, plan).verify(db, plan)
