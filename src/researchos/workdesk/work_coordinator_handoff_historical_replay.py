"""Independent ordered run/input/dispatch/receipt reconstruction.

The saved-history owner supplies admitted ancestor value boundaries, never a
runtime owner. This replay generates the input before comparing the actual
run, and keeps the sole proved-input map used by parent and A/B components.
It has no current-resource, live-file, Store-construction or dispatch path.
"""
from copy import deepcopy
from types import SimpleNamespace
import itertools

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import fields, versioned
from .work_coordinator_handoff_adjustment_records import finite
from .work_coordinator_handoff_historical_assets import _json


class HistoricalReplay:
    def __init__(self, sources, timeline, ledger):
        if (timeline.sources is not sources or ledger.sources is not sources or
                not sources.db.in_transaction):
            raise ValueError('原历史重放须属于同次真实SQL快照。')
        self.sources, self.timeline, self.ledger = sources, timeline, ledger
        self.db = sources.db
        self.inputs, self.runs, self.links, self.receipts = {}, {}, {}, {}
        self._reservations = None

    def frames_before(self, boundary):
        """Every earlier reservation must already have an independent input."""
        self.sources.unchanged()
        if self._reservations is None:
            actual_rows = []
            for run in self.sources.all(self.db, 'work_unit_run'):
                if run.get('item_id') == self.timeline.item_id:
                    actual, _, _, plan, _, reserved = self.sources.run(run['id'])
                    actual_rows.append((reserved['seq'], actual, plan))
            self._reservations = actual_rows
        found = []
        for seq, run, plan in self._reservations:
            if seq >= boundary:
                continue
            if plan not in self.timeline.plans or self.runs.get(run['id']) != run:
                raise ValueError('原历史前沿包含未独立重建或非祖先的运行。')
            found.append((seq, run))
        found.sort(key=lambda v: v[0])
        if [run['ordinal_total'] for _, run in found] != list(range(1, len(found) + 1)):
            raise ValueError('原历史前沿必须是完整连续祖先保留前缀。')
        return [self.sources.frame_at(run, boundary, self.inputs[run['id']]['dependencies']) for _, run in found]

    @staticmethod
    def request(plan, run, frames, *, full_evidence=False):
        return {'id': plan['item_id'], 'revision': run['item_revision'],
            'current_driver': {'id': run['driver_id'], 'generation': run['generation'], 'revision': run['driver_revision']},
            'plan': plan_ref(plan), 'authorization': run['authorization'],
            'frontier': digest([{'run_id': f['run']['id'],
                'evidence': f['source_evidence' if full_evidence else 'evidence']['version']} for f in frames]),
            'unit': {'key': run['unit_key'], 'revision': run['unit_revision']},
            'retry_of': None, 'proposal': run.get('proposal')}

    def advance(self, run, values, *, proposal=None, delivery=None):
        """Consume one real original reservation; no input is copied as proof."""
        if run['id'] in self.runs:
            raise ValueError('原运行只能按其实际事件消费一次。')
        actual, task, attempt, plan, unit, reserved = self.sources.run(run['id'])
        if (values.sources is not self.sources or values.timeline is not self.timeline or
                values.plan != plan or values.boundary != reserved['seq'] or actual != run):
            raise ValueError('原运行值须绑定本次来源及准确保留边界。')
        frames = self.frames_before(reserved['seq'])
        if values.frames != frames or run['ordinal_total'] != len(self.runs) + 1:
            raise ValueError('原运行输入必须消费全部准确较早保留。')
        frozen = values.input(unit, proposal=proposal)
        self.sources.verify_input(run, frozen, values.prompt(frozen), values.driver,
            values._auth, len(frames) + 1)
        proof = self.sources.exited(run)
        owner = 'run:' + run['id']
        self.ledger.row('work_unit_run', run, owner)
        self.ledger.row('work_dispatch', proof['dispatch'], owner)
        self.ledger.execution(proof, owner)
        link = self.timeline.execution_link(run, values.driver, delivery=delivery, owner=owner)
        request = self.command_request(values, run, frames)
        from .work_coordinator_handoff_post_round_v3_continuation_receipts import response_for
        # response_for uses only the actual task.spec_hash and pure run/dispatch
        # values. This data facade has no runtime or availability methods.
        response = response_for(SimpleNamespace(store=self.sources), self.db, run, proof['dispatch'])
        command = self.ledger.command(run['command_id'], 'work-item.unit-send', request, response,
            owner, not_before=max(link['event']['created'], proof['dispatch']['created']))
        # Original V7/V8 committed send witnesses have separate exact codecs.
        # The first V9 and later V3 witnesses remain their own owner's job.
        if values.stage in {'POST_V1', 'POST_V2'}:
            self._ancestor_witness(values, run, link, request, response, command, owner)
        self.runs[run['id']] = deepcopy(run)
        self.inputs[run['id']] = deepcopy(frozen)
        self.links[run['id']] = deepcopy(link)
        self.receipts[run['id']] = deepcopy(response)
        return {'input': deepcopy(frozen), 'proof': proof, 'link': link,
            'request': request, 'response': response}

    def command_request(self, values, run, frames, *, post_activation=False):
        """Recover only legal optional-null spellings from the original hash.

        V1 callers could omit proposal/retry_of. A V6 opening additionally has
        its actual explicit post_round_adjustment=True. Entropy never supplies
        inputs or semantic values; the stored hash chooses a legal spelling.
        """
        request = self.request(values.plan, run, frames, full_evidence=values.stage == 'ROOT_V1')
        command = self.db.execute('SELECT * FROM commands WHERE id=?', (run['command_id'],)).fetchone()
        if command is None or command['kind'] != 'work-item.unit-send':
            raise ValueError('原派发须有实际完整已提交命令。')
        if values.stage in {'POST_V1', 'POST_V2'}:
            variants = [request]
        else:
            optional = [name for name in ('retry_of', 'proposal') if request[name] is None]
            variants = []
            unit = next(u for u in values.plan['units'] if u['key'] == run['unit_key'])
            flags = [True] if post_activation else [None, True] if values.stage == 'POST_ROUND' and unit['role'] == 'coordinate' else [None]
            for keep in itertools.product((False, True), repeat=len(optional)):
                excluded = {name for name, present in zip(optional, keep) if not present}
                base = {k: v for k, v in request.items() if k not in excluded}
                for flow in (False, True):
                    for marker in flags:
                        candidate = {**base, **({'flow': None} if flow else {}),
                            **({'post_round_adjustment': True} if marker else {})}
                        variants.append(candidate)
        matches = [v for v in variants if digest(v) == command['payload_hash']]
        if len(matches) != 1:
            raise ValueError('原派发请求不能由准确原字段和合法可选空字段唯一重建。')
        return matches[0]

    def _ancestor_witness(self, values, run, link, request, response, command, owner):
        if values.stage == 'POST_V2':
            from . import work_coordinator_handoff_post_round_v2_commands as codec
        else:
            from . import work_coordinator_handoff_post_round_successor_commands as codec
        found = [row for row in self.sources.all(self.db, codec.RECEIPT_KIND)
            if row.get('command_id') == run['command_id']]
        if len(found) != 1:
            raise ValueError('祖先运行须有唯一原完整回执见证。')
        row = found[0]
        fields(row, {'id', 'version', 'snapshot', 'item_id', 'command_id', 'created'})
        finite(row['created'])
        body = versioned({'protocol': codec.RECEIPT_PROTOCOL, 'item_id': run['item_id'],
            'plan': plan_ref(values.plan), 'capability': values.ref(values._successor),
            'run': run, 'driver': link['driver'], 'request': request, 'receipt': response})
        expected = {'id': codec.RECEIPT_KIND + ':' + body['version'], 'version': body['version'],
            'snapshot': body, 'item_id': run['item_id'], 'command_id': run['command_id'], 'created': row['created']}
        event = self.sources._event(self.db, codec.RECEIPT_EVENT, row)
        if (row != expected or not link['event']['seq'] < event['seq'] or
                not link['driver']['updated'] <= row['created'] <= event['created'] <= command['created']):
            raise ValueError('祖先原完整回执见证、link、事件或提交顺序不符。')
        self.ledger.row(codec.RECEIPT_KIND, row, owner)
        self.ledger.event_at(event['seq'], event['kind'], row, owner)
