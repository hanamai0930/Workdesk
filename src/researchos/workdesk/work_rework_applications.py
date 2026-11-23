"""Atomic text rework revisions with immutable origins and normal dependency gates."""
import time

from .store import digest, uid
from .work_reworks import TERMINAL
from .work_transitions import plan_ref


PROTOCOL = 'ORDINARY_TEXT_REWORK_REVISION_V1'


class WorkReworkApplications:
    def __init__(self, service):
        self.s = service

    def handlers(self):
        return {'work-item.rework-apply': self.apply}

    def frontier(self, db, rows):
        return [{'run_id': r['id'], 'run_version': digest(r),
                 'evidence_version': self.s.work_units.evidence(db, r)['version']} for r in rows]

    def require_sources(self, db, plan, rows, *, consumer_plan=None):
        record = plan.get('rework_lineage')
        if not record:
            return
        if (record['protocol'] != PROTOCOL or
                digest({k: v for k, v in plan.items() if k != 'version'}) != plan['version']):
            raise ValueError('返工修订或方案版本不一致。')
        snapshot = self.s.work_unit_revision_applications._snapshot.get()
        reusable = snapshot and snapshot['db'] is db and snapshot['changes'] == db.total_changes
        marker = (digest(plan), digest(consumer_plan or plan), digest(rows))
        cached = snapshot.setdefault('text_frontiers', set()) if reusable else set()
        if marker in cached:
            return
        ids = {r['run_id'] for r in record['original_frontier']}
        originals = [r for r in rows if r['id'] in ids or r['plan_id'] in record['original_plan_ids']]
        if (not self.s.work_file_rework_applications.compare(db, consumer_plan or plan, rows,
                originals, record['original_frontier']) and self.frontier(db, originals) != record['original_frontier']):
            raise ValueError('返工登记的原运行、输入、结果或文件前沿已变化；须重新明确诊断和应用。')
        if reusable:
            cached.add(marker)

    @staticmethod
    def input_context(plan, unit):
        record = plan.get('rework_lineage')
        if not record:
            return None
        entry = next((e for e in record['units'] if e['key'] == unit['key']), None)
        if not entry:
            return None
        return {'protocol': PROTOCOL, 'revision_id': record['id'], 'base_plan': record['base_plan'],
                'proposal': record['proposal'], 'origin': entry,
                'scope': 'ORIGINAL_DIAGNOSTIC_DATA; NORMAL_DEPENDENCIES_REQUIRED; NOT_RETRY_OF'}

    def apply(self, db, p):
        row = self.s.work_proposals.current(db, p.get('id'))
        if row and row['snapshot'].get('state') == 'COORDINATOR_TEXT_REWORK_PREVIEW':
            from .work_coordinator_handoff_text_rework_apply import apply
            return apply(self.s, db, p)
        if set(p) != {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'proposal'}:
            raise ValueError('应用返工只接受当前准确任务、责任、方案、授权、前沿和提案引用。')
        item, driver, plan = self.s.work_units.checked(db, p)
        self.s.work_flows.require_stopped(db, item['id'])
        proposals = self.s.work_proposals
        row = proposals.current(db, item['id'])
        if (not row or p['proposal'] != proposals.reference(row) or
                self.s.work_transitions.consumption(db, row) or row['snapshot']['state'] != 'REWORK_PREVIEW'):
            raise ValueError('须使用最新、未消费的准确文字返工预览。')
        issues = proposals.live_issues(db, item, row)
        if issues:
            raise ValueError('返工预览已失效：' + '；'.join(issues))
        rows = self.s.work_units.rows(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        if (not auth or p['authorization'] != self.s.work_units.auth_ref(auth) or
                auth['driver_generation'] != driver['generation'] or auth['plan_id'] != plan['id'] or
                auth['plan_version'] != plan['version'] or p['frontier'] != self.s.work_units.frontier(db, item['id'])):
            raise ValueError('返工授权归属或准确执行前沿已变化。')
        if len(rows) >= auth['max_attempts_total'] or time.time() >= auth['dispatch_before']:
            raise ValueError('原累计次数或派发截止已耗尽，不能应用返工。')
        for run in rows:
            raw = self.s.work_units.evidence(db, run)
            original = self.s.collaboration.get(db, 'teamwork_plan', run['plan_id'])
            unit = next((u for u in original['units'] if u['key'] == run['unit_key']), None)
            if (not raw['exact_identity'] or raw['blockers'] or raw['state'] not in TERMINAL or not unit or
                    digest({k: v for k, v in original.items() if k != 'version'}) != original['version'] or
                    run['plan_version'] != original['version'] or run['input']['unit'] != unit or
                    run['unit_revision'] != digest(unit)):
                raise ValueError('原运行身份、终态或退出证据不足；未知不应用返工。')
        preview = row['snapshot']['rework']  # live_issues has recomputed this exact preview.
        entries = []
        for entry in preview['units']:
            unit = next(u for u in plan['units'] if u['key'] == entry['key'])
            if entry['state'] != 'AFFECTED' or unit['role'] == 'coordinate':
                continue
            if unit['outputs'] or unit['checks'] or unit['write_paths'] or unit['dependency_gate'] != 'result':
                raise ValueError('受影响文件或写入工作须另行处理。')
            source = entry['source']
            original_run = next((r for r in rows if source and r['id'] == source['run_id']), None)
            entries.append({**entry, 'original_input': original_run['input'] if original_run else None,
                            'original_evidence': self.s.work_units.evidence(db, original_run) if original_run else None})
        record = {'id': 'rework-revision:' + uid(), 'protocol': PROTOCOL, 'base_plan': plan_ref(plan),
                  'proposal': proposals.reference(row), 'source': row['snapshot']['source'],
                  'reason': row['snapshot']['decision']['reason'], 'candidate_version': preview['candidate_version'],
                  'direct_changes': preview['direct_changes'], 'affected_units': preview['affected_units'],
                  'units': entries, 'original_frontier': self.frontier(db, rows),
                  'original_plan_ids': sorted({plan['id'], *(r['plan_id'] for r in rows)}),
                  'command_id': self.s.command_identity, 'no_dispatch': True}
        transition = {'kind': 'EXPLICIT_TEXT_REWORK_REVISION', 'proposal': proposals.reference(row),
                      'source': record['source'], 'rework_revision': record['id'],
                      'usage_at_transition': self.s.work_units.usage(db, rows),
                      'authorization_from': self.s.work_units.auth_ref(auth),
                      'command_id': self.s.command_identity, 'no_dispatch': True}
        lineage = {e['key']: e['source'] for e in preview['units'] if e['state'] == 'ELIGIBLE_RESULT'}
        new, updated, transferred = self.s.work_transitions.save_plan(db, item, driver, plan, auth,
            plan['units'], lineage, transition, extra={'rework_lineage': record},
            reason='TEXT_REWORK_EXPLICIT_RECHECK_REQUIRED')
        saved = {**record, 'to_plan': plan_ref(new), 'driver': self.s.work_drivers.reference(updated),
                 'authorization': self.s.work_units.auth_ref(transferred), 'created': new['created']}
        self.s.collaboration.put(db, 'work_rework_revision', saved)
        consumption = {'id': 'proposal-consumption:' + row['id'], 'proposal': proposals.reference(row),
                       'item_id': item['id'], 'kind': 'TEXT_REWORK_TRANSITION', 'from_plan': plan_ref(plan),
                       'to_plan': plan_ref(new), 'driver': self.s.work_drivers.reference(updated),
                       'authorization': self.s.work_units.auth_ref(transferred), 'created': new['created'],
                       'command_id': self.s.command_identity, 'no_dispatch': True}
        self.s.collaboration.put(db, 'work_proposal_consumption', consumption)
        self.s.store.event(None, None, 'text_rework_applied', saved, db)
        return {'rework_revision': saved, 'transition': consumption, 'plan': plan_ref(new),
                'driver': self.s.work_drivers.reference(updated), 'authorization': self.s.work_units.auth_ref(transferred),
                'no_dispatch': True, 'automatically_resumed': False}
