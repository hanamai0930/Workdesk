"""Original pre-V3 strict proposal checks and single ordinary-run consumption.

Inputs and boundary state come from this invocation's independent replay. The
original reply is parsed again, including INVALID outcomes and exact problems;
neither the stored proposal nor its command receipt supplies semantic values.
"""
from copy import deepcopy

from .store import digest
from .work_transitions import plan_ref
from .work_proposals import parse
from .work_coordinator_handoff_records import fields
from .work_coordinator_handoff_adjustment_records import finite
from .work_coordinator_handoff_post_round_candidates import ref
from .work_coordinator_handoff_historical_assets import _json, _uuid


SOURCE_FIELDS = ('run_id', 'task_id', 'attempt_id', 'owner', 'session_id', 'turn_id',
    'connection_fingerprint', 'result_digest', 'version')


class HistoricalProposals:
    def __init__(self, sources, timeline, ledger, replay):
        if (timeline.sources is not sources or ledger.sources is not sources or
                replay.sources is not sources or not sources.db.in_transaction):
            raise ValueError('原提案须属于本次准确SQL来源和独立运行重放。')
        self.sources, self.timeline, self.ledger, self.replay = sources, timeline, ledger, replay
        self.db = sources.db
        self.checked = {}

    def previous(self, boundary):
        previous = None
        for event in self.db.execute("SELECT * FROM events WHERE kind='coordinator_proposal_checked' AND seq<? ORDER BY seq", (boundary,)):
            value = _json(event['payload'])
            if value.get('item_id') != self.timeline.item_id:
                continue
            actual = self.ledger.events.get(event['seq'])
            row = self.ledger.rows.get(('work_proposal', value['id']))
            if (actual is None or actual[0] != (event['kind'], value, event['task_id'], event['attempt_id']) or
                    row is None or row[0] != value):
                raise ValueError('原提案previous只能引用已独立重建的上一真实检查。')
            previous = value
        return deepcopy(previous)

    @staticmethod
    def frontier(values):
        field = 'source_evidence' if values.stage == 'ROOT_V1' else 'evidence'
        return digest([{'run_id': f['run']['id'], 'evidence': f[field]['version']} for f in values.frames])

    def check(self, values, row):
        fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
        finite(row['created']); _uuid(row['command_id'])
        plan, driver, auth = values.plan, values.driver, values._auth
        event = self.sources._event(self.db, 'coordinator_proposal_checked', row,
            task=row['snapshot']['source']['task_id'], attempt=row['snapshot']['source']['attempt_id'])
        frames = self.replay.frames_before(event['seq'])
        candidates = [f for f in frames if f['run']['plan_id'] == plan['id'] and
            f['run']['generation'] == driver['generation'] and f['run']['input']['unit']['role'] == 'coordinate']
        if (values.sources is not self.sources or values.timeline is not self.timeline or
                values.boundary != event['seq'] or frames != values.frames or not candidates or
                driver['state'] != 'RESERVED' or row['item_id'] != plan['item_id'] or
                row['created'] > event['created'] or
                self.timeline.business_before(event['seq'])['revision'] != driver['item_revision']):
            raise ValueError('原检查须消费本方案最新准确协调及实际边界责任。')
        frame = candidates[-1]; run, full = frame['run'], frame['source_evidence']
        frozen = self.replay.inputs[run['id']]
        if (run['input'] != frozen or row['snapshot']['source']['run_id'] != run['id'] or
                not full['result_available'] or not full['exact_identity'] or full['blockers'] or
                frozen['unit']['member']['reference'] != plan['coordinator']['reference'] or
                plan['mode'] == 'fixed' or auth['driver_generation'] != driver['generation']):
            raise ValueError('原提案来源须为完整独立输入和准确可用退出。')
        eligible = {f['run']['unit_key']: f for f in values._eligible(plan, frames)}
        for dependency in frozen['dependencies']:
            current = eligible.get(dependency['unit_key'])
            if current is None or dependency != {'unit_key': dependency['unit_key'], **current['source_evidence']}:
                raise ValueError('原检查时冻结依赖的准确结果或文件证据已变化。')
        previous = self.previous(event['seq'])
        decision, target, adjustment, state, problems = None, None, None, 'INVALID', []
        try:
            decision = parse(full['answer'])
            if decision['action'] not in frozen['coordination_context']['allowed_actions']:
                raise ValueError('此新根冻结回合只开放准确暂停建议，普通交付及修订入口待实施。')
            if decision['action'] == 'dispatch':
                candidate = next((c for c in frozen['coordination_context']['candidates']
                    if c['unit']['key'] == decision['unit_key']), None)
                if (not candidate or not candidate['ready_after_proposal'] or
                        candidate['retry_of'] != decision['retry_of']):
                    raise ValueError('提案只能选择本次准确输入中声明可在提案完成后准入的候选。')
                unit = next((u for u in plan['units'] if u['key'] == decision['unit_key']), None)
                if not unit or unit['role'] == 'coordinate' or decision['retry_of'] is not None:
                    raise ValueError('新根只允许原声明普通单元首次运行。')
                values._admit(plan, unit, frames, values._phase(plan, frames, values.records))
                target, state = {'key': unit['key'], 'revision': digest(unit)}, 'VALID'
            elif decision['action'] == 'adjust_dependencies':
                if values.stage == 'POST_V2':
                    from .work_coordinator_handoff_post_round_v2_preview import rebuild_preview
                elif values.stage == 'POST_V1':
                    from .work_coordinator_handoff_post_round_successor_preview import rebuild_preview
                elif values.stage == 'POST_ROUND':
                    from .work_coordinator_handoff_post_round_preview import rebuild_preview
                else:
                    from .work_coordinator_handoff_adjustments import rebuild_preview
                adjustment = rebuild_preview(plan, values.history(), run, decision, observed_at=row['created'])
                state = ('POST_ROUND_ADJUSTMENT_PREVIEW' if values.stage in
                    {'POST_V2', 'POST_V1', 'POST_ROUND'} else 'ADJUSTMENT_READY')
            elif decision['action'] == 'pause':
                state = 'PAUSE_SUGGESTED'
            else:
                # No original ancestor codec in this catalog opens these actions.
                raise ValueError('历史提案codec包含未核实的原修订动作。')
        except ValueError as exc:
            problems.append(str(exc))
        expected = {'item_revision': driver['item_revision'],
            'driver': {k: driver[k] for k in ('id', 'generation', 'revision')}, 'plan': plan_ref(plan),
            'authorization': {k: auth[k] for k in ('id', 'revision')}, 'frontier': self.frontier(values),
            'state': state, 'decision': decision, 'target': target, 'problems': problems,
            'source': {k: full[k] for k in SOURCE_FIELDS}, 'answer_digest': digest(full['answer']),
            'answer': full['answer'] if len(full['answer']) <= 32000 else None,
            'handoff_context': frozen['coordination_context']['handoff_context']}
        if adjustment:
            expected['adjustment'] = adjustment
        if row['snapshot'] != expected or row['version'] != digest(expected):
            raise ValueError('原提案完整snapshot与严格回复、原阶段和实际边界不符。')
        request = {'id': plan['item_id'], 'revision': driver['item_revision'],
            'current_driver': expected['driver'], 'plan': plan_ref(plan), 'authorization': expected['authorization'],
            'frontier': expected['frontier'], 'run_id': run['id'], 'result_version': full['version'],
            'proposal': ref(previous) if previous is not None else None}
        owner = 'proposal:' + row['id']
        self.ledger.command(row['command_id'], 'work-item.proposal-check', request,
            {'proposal': row, 'no_dispatch': True}, owner, not_before=max(row['created'], event['created']))
        self.ledger.row('work_proposal', row, owner)
        self.ledger.event_at(event['seq'], event['kind'], row, owner, task=run['task_id'], attempt=run['attempt_id'])
        self.checked[row['id']] = deepcopy(row)
        return {'plan': deepcopy(plan), 'proposal': deepcopy(row), 'history': values.history(),
            'preview': deepcopy(adjustment), 'source_check_event': {k: event[k] for k in ('seq', 'kind', 'payload_digest')}}

    def consume(self, values, run):
        """Bind the latest VALID check to its one actual ordinary reservation."""
        unit = next(u for u in values.plan['units'] if u['key'] == run['unit_key'])
        if unit['role'] == 'coordinate':
            if run.get('proposal') is not None:
                raise ValueError('原协调运行不得消费普通派发提案。')
            return None
        row = self.previous(values.boundary)
        if row is None or self.checked.get(row['id']) != row or run.get('proposal') != ref(row):
            raise ValueError('原普通运行须消费本次已重建的最新准确提案。')
        b = row['snapshot']; driver, auth = values.driver, values._auth
        consumers = [r for r in self.sources.all(self.db, 'work_unit_run') if r.get('proposal') == ref(row)]
        if (consumers != [run] or b['state'] != 'VALID' or b['plan'] != plan_ref(values.plan) or
                b['driver'] != {k: driver[k] for k in ('id', 'generation', 'revision')} or
                b['authorization'] != {k: auth[k] for k in ('id', 'revision')} or
                b['item_revision'] != driver['item_revision'] or b['frontier'] != self.frontier(values) or
                b['target'] != {'key': unit['key'], 'revision': digest(unit)} or
                b['decision']['action'] != 'dispatch' or b['decision']['unit_key'] != unit['key'] or
                b['decision']['retry_of'] is not None):
            raise ValueError('原普通运行未准确唯一消费原VALID提案及检查前沿。')
        return {'reference': ref(row), 'source': deepcopy(b['source']), 'decision': deepcopy(b['decision'])}
