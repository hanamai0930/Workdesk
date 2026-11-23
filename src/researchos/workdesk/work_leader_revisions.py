"""Daily leader adapters for existing ordinary previews and atomic applications."""
from . import work_adjustments, work_reworks, work_unit_revisions, work_file_reworks
from .store import digest
from .work_leader import organization, KIND, strategy, revisions_enabled

PROTOCOL = 'WORKDESK_LEADER_DAILY_DECISION_V2'
ACTIONS = {
    'adjust_dependencies': ('ADJUSTMENT_READY', 'adjustment', work_adjustments),
    'rework_units': ('REWORK_PREVIEW', 'rework', work_reworks),
    'revise_units': ('UNIT_REVISION_PREVIEW', 'unit_revision', work_unit_revisions),
    'file_rework_units': ('FILE_REWORK_PREVIEW', 'file_rework', work_file_reworks),
}


class WorkLeaderRevisions:
    def __init__(self, service):
        self.s = service

    def handlers(self):
        return {'work-item.leader-revision-apply': self.apply, 'work-item.host_experts-revision-apply': self.apply}

    def history(self, db, plan):
        row = organization(self.s, db, plan)
        result = []
        while row and row.get('parent'):
            parent = row['parent']
            proposal = self.s.collaboration.get(db, 'work_proposal', parent['proposal']['id'])
            if self.s.work_proposals.reference(proposal) != parent['proposal']:
                raise ValueError('负责人修订历史的提案版本已变化。')
            result.append({'action': proposal['snapshot']['decision']['action'],
                           'reason': proposal['snapshot']['decision']['reason'], 'plan': row['plan']})
            old = self.s.collaboration.get(db, 'teamwork_plan', parent['plan']['id'])
            row = organization(self.s, db, old)
        return list(reversed(result))

    def context(self, db, plan, rows):
        return {
            'adjustment_context': work_adjustments.context(plan),
            'adjustment_prior_frontier': work_adjustments.prior_frontier(self.s, db, rows),
            'rework_context': work_reworks.context(self.s, db, plan, rows),
            'unit_revision_context': work_unit_revisions.context(self.s, db, plan, rows),
            'file_rework_context': work_file_reworks.context(self.s, db, plan, rows),
        }

    @staticmethod
    def schemas():
        return {action: {k:v for k,v in owner.SCHEMA.items() if k != 'schema_version'} | {'protocol':PROTOCOL}
                for action, (_, _, owner) in ACTIONS.items()}

    def preview(self, db, item, driver, plan, rows, run, decision):
        state, field, owner = ACTIONS[decision['action']]
        preview = owner.preview(self.s, db, item, driver, plan, rows, run, decision)
        candidate = preview.get('candidate_units', plan['units'])
        # Every revised daily plan retains a single complete integration duty.
        ordinary = [u for u in candidate if u['role'] != 'coordinate']
        ordered = [u for u in ordinary if u['role'] != 'integrate'] + [u for u in ordinary if u['role'] == 'integrate']
        preparation = self.s.host_preparation if plan['mode'] == 'host_experts' else self.s.leader_preparation
        preparation.validate_definitions(ordered, 1000)
        return state, field, preview

    def adopt(self, db, parent, child, transition):
        original = organization(self.s, db, parent)
        if not original:
            return
        proposal = transition.get('proposal')
        if not proposal:
            raise ValueError('负责人修订须绑定准确消费提案。')
        row = {**{k: original[k] for k in ('preparation_id', 'evidence', 'access')},
               'id': strategy(self.s, parent).prefix + '-organization:' + child['id'],
               'plan': {k: child[k] for k in ('id', 'version')},
               'decision_protocol': original.get('decision_protocol', 'WORKDESK_LEADER_DAILY_DECISION_V1'),
               'parent': {'plan': original['plan'], 'version': original['version'], 'proposal': proposal,
                          'consumption_id': 'proposal-consumption:' + proposal['id']},
               'command_id': self.s.command_identity}
        if original.get('member_access'):
            row['member_access'] = original['member_access']
        row['version'] = digest(row)
        self.s.collaboration.put(db, strategy(self.s, parent).kind, row)

    def apply(self, db, p):
        if set(p) != {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'flow', 'proposal'}:
            raise ValueError('负责人自动修订只接受准确流程、前沿和提案引用。')
        item, driver, plan = self.s.work_units.checked(db, p)
        org = organization(self.s, db, plan)
        if not org or not revisions_enabled(org):
            raise ValueError('此负责人组织未声明自动修订协议。')
        flow = self.s.work_flows.current(db, item['id'])
        if (not flow or flow['state'] != 'RUNNING' or flow['instance'] != self.s.instance
                or p['flow'] != self.s.work_flows.reference(flow)):
            raise ValueError('负责人自动修订流程已暂停或变化。')
        state, reason, candidate = self.s.work_flows.decision(db, item, flow)
        if state != 'REVISION_READY' or p['proposal'] != candidate['proposal']:
            raise ValueError('负责人当前不能应用该修订：' + reason)
        row = self.s.work_proposals.current(db, item['id'])
        action = row['snapshot']['decision']['action']
        self.s.work_flows.change(db, {'id': item['id'], 'revision': item['revision'],
                                    'flow': p['flow'], 'action': 'pause'})
        owners = {'adjust_dependencies': self.s.work_transitions,
                  'rework_units': self.s.work_rework_applications,
                  'revise_units': self.s.work_unit_revision_applications,
                  'file_rework_units': self.s.work_file_rework_applications}
        applied = owners[action].apply(db, {k: v for k, v in p.items() if k != 'flow'})
        scope = {'id': item['id'], 'revision': item['revision'], 'plan': applied['plan'],
                 'current_driver': applied['driver']}
        driver_ref = self.s.work_drivers.change(db, {**scope, 'action': 'resume'})['driver']['reference']
        scope['current_driver'] = driver_ref
        running = self.s.work_flows.change(db, {
            **scope, 'authorization': applied['authorization'],
            'frontier': self.s.work_units.frontier(db, item['id']),
            'flow': self.s.work_flows.reference(self.s.work_flows.current(db, item['id'])), 'action': 'start'})['flow']
        return {**applied, 'flow': running, 'automatically_resumed': True}
