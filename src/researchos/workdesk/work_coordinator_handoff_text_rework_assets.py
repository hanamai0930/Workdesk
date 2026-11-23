"""Full typed text historical assets; no runtime Service or current permission."""
from copy import deepcopy

from .work_coordinator_handoff_historical_assets import HistoricalSources, FrozenAssets, _json
from .work_coordinator_handoff_historical_namespace import CanonicalLedger
from .work_coordinator_handoff_post_round_v3_running_assets import HistoricalCatalog
from .work_coordinator_handoff_text_rework_child_history import TextChildHistory
from .work_coordinator_handoff_text_rework_review_replay import ReviewReplay
from .work_coordinator_handoff_text_rework_saved_records import ref
from . import work_coordinator_handoff_text_rework_contracts as c


def text_items(db):
    """Malformed/nested markers select rejection, never grant an asset owner."""
    owners = {}
    for raw in db.execute('SELECT kind,id,data FROM collaboration WHERE kind=?', (c.KINDS[c.CAPABILITY],)):
        cap = c.validate_capability(_json(raw['data']))
        if cap['id'] != raw['id']:
            raise ValueError('返工资产能力须使用准确实际SQL键。')
        item = cap['item_id']
        if item in owners:
            raise ValueError('返工资产业务存在额外未支持的新边。')
        owners[item] = {item, cap['id'], cap['version'], *cap['snapshot']['parent_plan'].values()}
    active = set()
    tables = [('collaboration', 'data'), ('events', 'payload'), ('commands', 'receipt')]
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_readiness_observations'").fetchone():
        tables.append(('work_readiness_observations', 'data'))
    for table, column in tables:
        for raw in db.execute('SELECT ' + column + ' FROM ' + table):
            pending = [_json(raw[0])]; strings = set(); marked = False
            while pending:
                node = pending.pop()
                if isinstance(node, dict):
                    pending.extend(node.keys()); pending.extend(node.values())
                elif isinstance(node, list):
                    pending.extend(node)
                elif isinstance(node, str):
                    strings.add(node)
                    marked = marked or node.startswith(c.PREFIX) or node.startswith('work_coordinator_handoff_text_rework_')
            if marked:
                matches = {item for item, identities in owners.items() if identities & strings}
                if not matches:
                    raise ValueError('返工资产标记没有真实typed能力归属。')
                active.update(matches)
    if active != set(owners):
        raise ValueError('返工资产能力归属未闭合。')
    return active


class TextHistoricalCatalog:
    def __init__(self, db, asset_root):
        self.assets = FrozenAssets(asset_root); self.sources = HistoricalSources(db, self.assets)
        self.ledger = CanonicalLedger(self.sources); self.db = self.sources.db
        self.changes = self.db.total_changes; self.inputs, self.originals, self.results = {}, {}, {}

    def require_snapshot(self, db=None):
        self.sources.unchanged(db)

    require_context = HistoricalCatalog.require_context
    require_intent = HistoricalCatalog.require_intent

    def _original(self, parent):
        owner = parent['owner']; self.inputs.update(deepcopy(owner.replay.inputs))
        if parent.get('original') is not None:
            original = parent['original']
            self.originals[original['child_plan']['id']] = deepcopy(original)

    def read_all(self, items, other_v3_items):
        self.require_snapshot(); seen = set()
        for cap in self.sources.all(self.db, c.KINDS[c.CAPABILITY]):
            c.validate_capability(cap)
            item = cap['item_id']
            if item not in items:
                continue
            if item in seen:
                raise ValueError('返工资产业务只能有一条真实typed审阅边。')
            seen.add(item)
            candidates = [r for r in self.sources.all(self.db, c.KINDS[c.CANDIDATE]) if r.get('item_id') == item]
            if candidates:
                if len(candidates) != 1:
                    raise ValueError('返工资产只接受首条真实准确保存边。')
                history = TextChildHistory(self.db, self.assets.root, ref(candidates[0]),
                    _sources=self.sources, _ledger=self.ledger)
                result = history._reconstruct_children(); history._current_pointers(result)
                self._original(result['review']['parent'])
                # Every frame's complete input was independently generated and
                # compared by its chronological owner before registration.
                self.inputs.update({f['run']['id']: deepcopy(f['run']['input']) for f in result['history']['frames']})
            else:
                review = ReviewReplay(self.db, self.assets.root, ref(cap), _sources=self.sources, _ledger=self.ledger)
                result = review.replay_for_catalog(self); self._original(result['parent'])
                self.inputs[result['run']['id']] = deepcopy(result['input'])
            self.results[item] = result
        if seen != set(items):
            raise ValueError('返工资产发现的业务任务未全部独立重建。')
        if other_v3_items:
            other = HistoricalCatalog(self.db, self.assets.root, _sources=self.sources, _ledger=self.ledger).read_all(other_v3_items)
            self.inputs.update(other.inputs); self.originals.update(other.originals)
        self.ledger.close(protocol_prefixes=('COORDINATOR_FILE_HANDOFF_',))
        self.assets.recheck(); self.require_snapshot()
        return self
