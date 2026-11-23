"""Actual complete text-save edge, sharing original/review/new carrier ledger."""
from copy import deepcopy

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import versioned
from .work_coordinator_handoff_historical_assets import HistoricalSources, FrozenAssets
from .work_coordinator_handoff_historical_namespace import CanonicalLedger
from .work_coordinator_handoff_text_rework_review_replay import ReviewReplay
from .work_coordinator_handoff_text_rework_saved_records import _SaveEdge, validate, ref, SAVE_EVENT, DRIVER_EVENT
from . import work_coordinator_handoff_text_rework_saved_values as v
from . import work_coordinator_handoff_text_rework_contracts as c


class TextSavedHistory:
    def __init__(self, db, asset_root, candidate, *, save_owner=None, responsibility_owner=None, _sources=None, _ledger=None):
        if _sources is None and _ledger is None:
            self.sources = HistoricalSources(db, FrozenAssets(asset_root))
            self.ledger = CanonicalLedger(self.sources)
        else:
            from pathlib import Path
            raw = db.connection if hasattr(db, 'connection') else db
            if (type(_sources) is not HistoricalSources or type(_ledger) is not CanonicalLedger or
                    _ledger.sources is not _sources or _sources.db.connection is not raw or
                    Path(asset_root).absolute() != _sources.assets.root):
                raise ValueError('后继历史须共享同连接真实来源、冻结根及完整账本。')
            _sources.unchanged(db)
            self.sources, self.ledger = _sources, _ledger
        self.db = self.sources.db
        self.edge = _SaveEdge(self.sources, candidate, command_owner=save_owner)
        self.save_owner = save_owner; self.asset_root = asset_root
        self.responsibility_owner = responsibility_owner

    def _one(self, protocol):
        rows = [r for r in self.sources.all(self.db, c.KINDS[protocol])
            if r.get('command_id') == self.edge.candidate['command_id']]
        if len(rows) != 1:
            raise ValueError('文字返工保存须有唯一准确原命令载体：' + protocol)
        row = c.validate_receipt(rows[0]) if protocol == c.RECEIPT else validate(rows[0], protocol)
        if row['item_id'] != self.edge.candidate['item_id']:
            raise ValueError('文字返工保存载体业务归属不符。')
        event = self.sources._event(self.db, c.EVENTS[protocol], row)
        return row, event

    def read(self):
        result = self._reconstruct()
        from .work_coordinator_handoff_text_rework_responsibility import replay_changes
        replay_changes(self, result, self.responsibility_owner)
        self.close(result)
        return result

    def _reconstruct(self):
        """Private saved semantic prefix; the enclosing owner must close SQL."""
        sources, ledger, edge = self.sources, self.ledger, self.edge
        candidate = edge.candidate; b = candidate['snapshot']; owner = 'text-save:' + candidate['id']
        replay = ReviewReplay(self.db, self.asset_root, b['capability'], _sources=sources, _ledger=ledger)
        original = replay.read_before_save(edge); h = original['history']; proposal = original['proposal']
        if proposal is None or ref(proposal) != b['proposal']:
            raise ValueError('保存须使用准确审阅前缀的最后真实提案。')
        run = original['run']
        checked = sources._event(self.db, 'coordinator_proposal_checked', proposal, edge.boundary,
            run['task_id'], run['attempt_id'])
        check_ref = {k: checked[k] for k in ('seq', 'kind', 'payload_digest')}
        expected = v.candidate_body(replay.capability, h, proposal, check_ref, b['request'], b['child_seed'],
            b['driver_after'], b['authorization_after'], b['prepared_at'], candidate['command_id'])
        child = expected['child_plan']
        if (b != expected or sources.get(self.db, 'teamwork_plan', child['id']) != child or
                not checked['seq'] < edge.boundary or
                not checked['created'] <= b['child_seed']['created'] <= b['prepared_at'] <= candidate['created'] <= edge.event['created']):
            raise ValueError('保存candidate不等于独立原请求、完整预览、父历史及同定义child。')
        ledger.row(c.KINDS[c.CANDIDATE], candidate, owner)
        ledger.event_at(edge.event['seq'], edge.event['kind'], candidate, owner)
        ledger.row('teamwork_plan', child, owner)
        driver_key = 'driver-version:' + b['driver_after']['revision']
        driver_row = sources.get(self.db, 'work_driver_revision', driver_key)
        driver = {k: val for k, val in driver_row.items() if k != 'driver_id'}
        driver['id'] = driver_row['driver_id']
        auth_row = sources.get(self.db, 'work_unit_authorization_revision', 'unit-auth-version:' + b['authorization_after']['revision'])
        auth = {**auth_row, 'id': b['authorization_after']['id']}
        expected_driver, expected_auth = v.transferred(candidate, transferred_at=auth['transferred_at'], driver_updated=driver['updated'])
        driver_event = sources._event(self.db, DRIVER_EVENT, {**driver, 'no_dispatch': True})
        payload = v.save_payload(candidate, driver, auth); saved = sources._event(self.db, SAVE_EVENT, payload)
        if (driver != expected_driver or auth != expected_auth or
                driver_row != {**driver, 'id': driver_key, 'driver_id': driver['id']} or
                auth_row != {**auth, 'id': 'unit-auth-version:' + auth['revision']} or
                not edge.event['seq'] < driver_event['seq'] < saved['seq'] or
                not edge.event['created'] <= auth['transferred_at'] <= driver['updated'] <= driver_event['created'] <= saved['created']):
            raise ValueError('保存后的同generation暂停责任、原累计/最初截止转移或实际保存顺序不符。')
        ledger.row('work_driver_revision', driver_row, owner); ledger.row('work_unit_authorization_revision', auth_row, owner)
        ledger.event_at(driver_event['seq'], DRIVER_EVENT, {**driver, 'no_dispatch': True}, owner)
        ledger.event_at(saved['seq'], SAVE_EVENT, payload, owner)
        app, applied = self._one(c.APPLICATION)
        consumed = app['snapshot']['consumption']
        if (consumed != v.consumption(candidate, driver, auth, consumed['created']) or
                app['snapshot'] != v.application_body(candidate, driver, auth, consumed) or
                not saved['seq'] < applied['seq'] or not saved['created'] <= consumed['created'] <= app['created'] <= applied['created']):
            raise ValueError('保存application及唯一proposal消费不等于实际完整保存。')
        ledger.row('work_proposal_consumption', consumed, owner)
        ledger.row(c.KINDS[c.APPLICATION], app, owner); ledger.event_at(applied['seq'], applied['kind'], app, owner)
        registration, registered = self._one(c.REGISTRATION)
        events = [{k: e[k] for k in ('seq', 'kind', 'payload_digest')} for e in (edge.event, driver_event, saved, applied)]
        if (registration['snapshot'] != v.registration_body(candidate, app, events) or
                not applied['seq'] < registered['seq'] or not applied['created'] <= registration['created'] <= registered['created']):
            raise ValueError('保存registration须引用全部准确已发生事件。')
        ledger.row(c.KINDS[c.REGISTRATION], registration, owner)
        ledger.event_at(registered['seq'], registered['kind'], registration, owner)
        response = v.response_for(candidate, app, registration, driver, auth)
        receipt, receipted = self._one(c.RECEIPT)
        expected_receipt = versioned({'protocol': c.RECEIPT, 'operation': 'SAVE', 'request': b['request'],
            'request_hash': b['request_hash'], 'capability': b['capability'], 'registration': ref(registration),
            'run': None, 'driver': v.driver_ref(driver), 'receipt': response, **c.PERMISSIONS})
        if (receipt['snapshot'] != expected_receipt or not registered['seq'] < receipted['seq'] or
                not registered['created'] <= receipt['created'] <= receipted['created']):
            raise ValueError('保存完整receipt须在登记之后精确见证实际返回对象。')
        ledger.row(c.KINDS[c.RECEIPT], receipt, owner); ledger.event_at(receipted['seq'], receipted['kind'], receipt, owner)
        if self.save_owner is None:
            ledger.command(candidate['command_id'], c.APPLY, b['request'], response, owner, not_before=receipted['created'])
        else:
            self.save_owner.require_receipt(self, receipt, response)
        result = {'candidate': candidate, 'application': app, 'registration': registration, 'receipt': receipt,
            'child_plan': child, 'driver': driver, 'authorization': auth, 'history': h,
            'capability': replay.capability, 'proposal': proposal, 'response': response,
            'sources': sources, 'ledger': ledger, 'review': original}
        return result

    def _current_pointers(self, result):
        owner = 'current:' + result['child_plan']['item_id']; item = result['child_plan']['item_id']
        for kind, row in (('teamwork_current', {'id': 'teamwork-current:' + item, 'plan_id': result['child_plan']['id']}),
                ('work_driver', result['driver']), ('work_unit_authorization', result['authorization']),
                ('work_proposal_current', {'id': 'proposal-current:' + item, 'proposal_id': result['proposal']['id']})):
            self.ledger.row(kind, row, owner)

    def close(self, result):
        self._current_pointers(result)
        self.ledger.close(protocol_prefixes=('COORDINATOR_FILE_HANDOFF_',))
        self.sources.assets.recheck(); self.edge.require(self.sources)
