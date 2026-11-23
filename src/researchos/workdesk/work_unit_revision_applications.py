"""Atomic bounded ordinary-definition revisions with exact, persistent source constraints."""
import time
from contextlib import contextmanager
from contextvars import ContextVar

from .store import digest, uid
from .work_transitions import plan_ref
from .work_unit_revisions import allowed_members, require_history


PROTOCOL = 'ORDINARY_UNIT_REVISION_APPLICATION_V1'


class WorkUnitRevisionApplications:
    def __init__(self, service):
        self.s = service
        self._snapshot = ContextVar('unit_revision_validation_snapshot', default=None)

    @contextmanager
    def validation_snapshot(self, db):
        """Share successful checks only inside one read projection and unchanged transaction."""
        token = self._snapshot.set({'db': db, 'changes': db.total_changes, 'plans': set()})
        try:
            yield
        finally:
            self._snapshot.reset(token)

    def handlers(self):
        return {'work-item.unit-revision-apply': self.apply}

    def ancestors(self, db, plan):
        plans, seen = [], set()
        while plan:
            if plan['id'] in seen or digest({k: v for k, v in plan.items() if k != 'version'}) != plan['version']:
                raise ValueError('局部修订的原方案链或版本不一致。')
            seen.add(plan['id']); plans.append(plan)
            previous = plan.get('previous_plan_reference')
            if not previous:
                break
            plan = self.s.collaboration.get(db, 'teamwork_plan', previous['id'])
            if plan_ref(plan) != previous:
                raise ValueError('局部修订的原方案引用已变化。')
        return plans

    def require_sources(self, db, plan, rows, *, consumer_plan=None):
        snapshot = self._snapshot.get()
        reusable = snapshot and snapshot['db'] is db and snapshot['changes'] == db.total_changes
        consumer_plan = consumer_plan or plan
        marker = (plan['id'], digest(plan), digest(rows), digest(consumer_plan))
        if reusable and marker in snapshot['plans']:
            return
        plans = self.ancestors(db, plan)
        self.s.work_file_rework_applications.records(db, consumer_plan, rows)
        if not any(p.get('unit_revision_lineage') or p.get('rework_lineage') for p in plans):
            if reusable:
                snapshot['plans'].add(marker)
            return
        # Earlier rework constraints continue after later changes of definition or plan.
        for original in plans:
            self.s.work_rework_applications.require_sources(db, original, rows, consumer_plan=consumer_plan)
            record = original.get('unit_revision_lineage')
            if not record:
                continue
            if record['protocol'] != PROTOCOL:
                raise ValueError('局部修订协议不一致。')
            ids = {r['run_id'] for r in record['original_frontier']}
            selected = [r for r in rows if r['id'] in ids or r['plan_id'] in record['original_plan_ids']]
            if (not self.s.work_file_rework_applications.compare(db, consumer_plan, rows, selected,
                    record['original_frontier']) and
                    self.s.work_rework_applications.frontier(db, selected) != record['original_frontier']):
                raise ValueError('局部修订的原运行、输入、结果或文件前沿已变化；不能重绑旧来源。')
            # Reuse this transaction. selection()/member() also validates model settings
            # via a second reader, which can deadlock an active worker's pending commit.
            resources = self.s.resources.catalog(db)
            for reference in record['original_resources']:
                member = next((m for m in resources['members'] if m['id'] == reference['member_id']), None)
                connection = next((c for c in resources['connections'] if c['id'] == reference['connection_id']), None)
                quota = next((q for q in resources['quota_sources'] if q['id'] == reference['quota_id']), None)
                current = ({'member_id': member['id'], 'member_revision': member['revision'],
                            'connection_id': connection['id'], 'connection_revision': connection['revision'],
                            'quota_id': quota['id'], 'quota_revision': quota['revision']}
                           if member and connection and quota else None)
                if (current != reference or not member['available'] or
                        member['connection_id'] != connection['id'] or connection['quota_id'] != quota['id']):
                    raise ValueError('局部修订的原成员、连接或额度来源已变化／不可用。')
        if reusable:
            snapshot['plans'].add(marker)

    @staticmethod
    def input_context(plan, unit):
        record = plan.get('unit_revision_lineage')
        if not record:
            return None
        entry = next((e for e in record['units'] if e['key'] == unit['key']), None)
        if not entry:
            return None
        return {'protocol': PROTOCOL, 'revision_id': record['id'], 'base_plan': record['base_plan'],
                'proposal': record['proposal'], 'origin': entry,
                'scope': 'LOCAL_DUTY_UNDER_ORIGINAL_REQUIREMENTS; NORMAL_DEPENDENCIES_REQUIRED; NOT_RETRY_OF'}

    def apply(self, db, p):
        if set(p) != {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'proposal'}:
            raise ValueError('局部修订应用只接受准确任务、责任、方案、授权、前沿和预览引用。')
        item, driver, plan = self.s.work_units.checked(db, p)
        self.s.work_flows.require_stopped(db, item['id'])
        proposals = self.s.work_proposals
        row = proposals.current(db, item['id'])
        if (not row or p['proposal'] != proposals.reference(row) or
                self.s.work_transitions.consumption(db, row) or row['snapshot']['state'] != 'UNIT_REVISION_PREVIEW'):
            raise ValueError('须使用最新、未消费的准确普通单元局部修订预览。')
        issues = proposals.live_issues(db, item, row)
        if issues:
            raise ValueError('局部修订预览已失效：' + '；'.join(issues))
        rows = self.s.work_units.rows(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        if (not auth or p['authorization'] != self.s.work_units.auth_ref(auth) or
                auth['driver_generation'] != driver['generation'] or auth['plan_id'] != plan['id'] or
                auth['plan_version'] != plan['version'] or p['frontier'] != self.s.work_units.frontier(db, item['id'])):
            raise ValueError('局部修订授权归属或准确执行前沿已变化。')
        if len(rows) >= auth['max_attempts_total'] or time.time() >= auth['dispatch_before']:
            raise ValueError('原累计次数或派发截止已耗尽，不能应用局部修订。')
        require_history(self.s, db, rows)
        self.s.work_rework_applications.require_sources(db, plan, rows)
        self.require_sources(db, plan, rows)
        preview = row['snapshot']['unit_revision']  # live_issues recomputed the complete exact candidate.
        original_units = {u['key']: u for u in plan['units']}
        # Recompute diagnostics for exact basis-unit origins, including new units without a same-key run.
        from .work_reworks import diagnostics
        observed = {d['key']: d for d in diagnostics(self.s, db, plan, rows)}
        def origin(diagnostic):
            source = diagnostic['source'] if diagnostic else None
            run = next((r for r in rows if source and r['id'] == source['run_id']), None)
            return {'source': source, 'diagnostic': diagnostic,
                    'original_input': run['input'] if run else None,
                    'original_evidence': self.s.work_units.evidence(db, run) if run else None}
        entries = []
        for entry in preview['units']:
            unit = entry['candidate_definition']
            if entry['state'] != 'AFFECTED' or unit['role'] == 'coordinate':
                continue
            if unit['outputs'] or unit['checks'] or unit['write_paths'] or unit['dependency_gate'] != 'result':
                raise ValueError('受影响文件或写入工作须另行处理。')
            basis = entry['basis']
            kind = ('NEW_FROM_DECLARED_DUTY' if entry['key'] in preview['added_units'] else
                    'REVISED_EXISTING' if entry['key'] in preview['revised_units'] else 'DEPENDENCY_AFFECTED')
            basis_origin = ({'basis': basis, 'definition': original_units[basis['unit_key']],
                             'work_requirements': plan['work_requirements'], **origin(observed[basis['unit_key']])}
                            if basis else None)
            entries.append({**entry, **origin(entry['diagnostic']), 'origin_kind': kind, 'basis_origin': basis_origin})
        record = {'id': 'unit-revision:' + uid(), 'protocol': PROTOCOL, 'base_plan': plan_ref(plan),
                  'proposal': proposals.reference(row), 'source': row['snapshot']['source'],
                  'reason': row['snapshot']['decision']['reason'], 'candidate_version': preview['candidate_version'],
                  'work_requirements': plan['work_requirements'],
                  'direct_changes': preview['direct_changes'], 'affected_units': preview['affected_units'],
                  'added_units': preview['added_units'], 'revised_units': preview['revised_units'], 'units': entries,
                  'prior_context': {k: plan[k] for k in ('rework_lineage', 'unit_revision_lineage') if k in plan},
                  'original_frontier': self.s.work_rework_applications.frontier(db, rows),
                  'original_plan_ids': sorted({plan['id'], *(r['plan_id'] for r in rows)}),
                  'original_resources': allowed_members(plan), 'command_id': self.s.command_identity, 'no_dispatch': True}
        transition = {'kind': 'EXPLICIT_ORDINARY_UNIT_REVISION', 'proposal': proposals.reference(row),
                      'source': record['source'], 'unit_revision': record['id'],
                      'usage_at_transition': self.s.work_units.usage(db, rows),
                      'authorization_from': self.s.work_units.auth_ref(auth),
                      'command_id': self.s.command_identity, 'no_dispatch': True}
        lineage = {e['key']: e['source'] for e in preview['units'] if e['state'] == 'ELIGIBLE_RESULT'}
        new, updated, transferred = self.s.work_transitions.save_plan(db, item, driver, plan, auth,
            preview['candidate_units'], lineage, transition, extra={'unit_revision_lineage': record},
            reason='UNIT_REVISED_EXPLICIT_RECHECK_REQUIRED')
        saved = {**record, 'to_plan': plan_ref(new), 'driver': self.s.work_drivers.reference(updated),
                 'authorization': self.s.work_units.auth_ref(transferred), 'created': new['created']}
        self.s.collaboration.put(db, 'work_unit_revision', saved)
        consumption = {'id': 'proposal-consumption:' + row['id'], 'proposal': proposals.reference(row),
                       'item_id': item['id'], 'kind': 'ORDINARY_UNIT_REVISION', 'from_plan': plan_ref(plan),
                       'to_plan': plan_ref(new), 'driver': self.s.work_drivers.reference(updated),
                       'authorization': self.s.work_units.auth_ref(transferred), 'created': new['created'],
                       'command_id': self.s.command_identity, 'no_dispatch': True}
        self.s.collaboration.put(db, 'work_proposal_consumption', consumption)
        self.s.store.event(None, None, 'ordinary_unit_revision_applied', saved, db)
        return {'unit_revision': saved, 'transition': consumption, 'plan': plan_ref(new),
                'driver': self.s.work_drivers.reference(updated), 'authorization': self.s.work_units.auth_ref(transferred),
                'no_dispatch': True, 'automatically_resumed': False}
