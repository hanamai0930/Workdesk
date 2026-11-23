"""Authoritative persisted sources for a coordinator root.

The reachable runtime is still the V1 pause-only coordinator path. This reader
does not admit ordinary runs. Historical file evidence is rebuilt from saved
bytes; it must never be interpreted as permission to attach current files.
"""
from copy import deepcopy
import json
import math

from . import workspace
from .store import digest
from .work_coordinator_handoff_context import WorkCoordinatorHandoffContext
from .work_coordinator_handoff_records import (
    DELIVERY_KIND, COMPLETION_KIND, PERMISSIONS, WorkCoordinatorHandoffRecords)
from .work_coordinator_handoff_roots import MARKER_FIELD
from .work_file_reworks import original_files
from .work_transitions import plan_ref


PROTOCOL = 'COORDINATOR_FILE_HANDOFF_AUTHORITATIVE_SOURCES_V1'


class WorkCoordinatorHandoffLedger:
    def __init__(self, service):
        self.s = service
        self.records = WorkCoordinatorHandoffRecords(service)

    @staticmethod
    def _checked(raw, definition):
        """Run the existing declarative predicates against exact saved bytes."""
        try:
            if definition['kind'] == 'nonempty':
                return bool(raw)
            if len(raw) > workspace.MAX_FILE:
                return False
            text = raw.decode('utf-8')
            if definition['kind'] == 'utf8_contains':
                return definition['text'] in text
            if definition['kind'] == 'json_parse':
                def invalid(value):
                    raise ValueError('Nonfinite JSON: ' + value)
                json.loads(text, parse_constant=invalid)
                return True
        except (UnicodeError, ValueError, RecursionError):
            return False
        raise ValueError('原保存快照含未登记检查。')

    def _saved_files(self, db, run):
        # original_files validates actual dispatch input bytes and every saved
        # snapshot, including those not selected by the current pointer.
        files = original_files(self.s, db, run)
        if files['input_problems'] or files['output_state'] == 'DAMAGED':
            raise ValueError('原实际输入或全部旧快照损坏。')
        unit = self.s.work_artifacts.unit(db, run)
        expected = []
        for saved in files['snapshots']:
            ref, snapshot = saved['reference'], saved['snapshot']
            row = self.s.collaboration.get(db, 'work_unit_artifact', ref['id'])
            if (set(row) != {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'} or
                    self.s.work_artifacts.reference(row) != ref or
                    row['snapshot'] != snapshot or row['item_id'] != run['item_id'] or
                    row['id'] != 'unit-artifact:' + digest(snapshot) or
                    type(row['created']) not in (int, float) or not math.isfinite(row['created']) or
                    row['created'] < run['created'] or
                    not isinstance(row['command_id'], str) or not row['command_id'].strip()):
                raise ValueError('原artifact外壳、归属或准确快照不一致。')
            observations = []
            for check in unit['checks']:
                blob = workspace.read_blob(self.s.work_artifacts.folder(row), check['file'])
                observations.append({'definition': check, 'passed': self._checked(blob['raw'], check)})
            status = ('PASS' if all(o['passed'] for o in observations) else 'FAIL') if observations else 'NOT_RECORDED'
            if snapshot['observations'] != observations or snapshot['check_status'] != status:
                raise ValueError('原保存检查不是准确旧快照字节的实际结果。')
            expected.append(ref)
        # current() follows a pointer; inspect its exact envelope too. A missing
        # pointer cannot quietly turn registered output into NOT_RECORDED.
        pointer_id = 'unit-artifact-current:' + run['id']
        row = db.execute('SELECT kind,data FROM collaboration WHERE id=?', (pointer_id,)).fetchone()
        current = self.s.work_artifacts.current(db, run['id'])
        if row:
            pointer = json.loads(row[1])
            if (row[0] != 'work_unit_artifact_current' or
                    set(pointer) != {'id', 'artifact_id'} or pointer['id'] != pointer_id or
                    not current or self.s.work_artifacts.reference(current) not in expected or
                    current['snapshot']['run_id'] != run['id']):
                raise ValueError('当前artifact指针不是该原运行的准确保存快照。')
        elif expected or current:
            raise ValueError('已登记原快照缺少准确当前指针。')
        captures = self._capture_events(db, run, files)
        if captures and captures[-1]['artifact'] != self.s.work_artifacts.reference(current):
            raise ValueError('当前artifact指针不是最后一次真实捕获。')
        return files, current

    def _capture_events(self, db, run, files):
        snapshots = {v['reference']['id']: v for v in files['snapshots']}
        events, seen = [], set()
        for row in db.execute("SELECT seq,payload FROM events WHERE task_id=? AND attempt_id=? "
                              "AND kind='cooperative_files_checked' ORDER BY seq",
                              (run['task_id'], run['attempt_id'])):
            value = json.loads(row['payload'])
            ref = value.get('artifact', {})
            saved = snapshots.get(ref.get('id'))
            if (set(value) != {'artifact', 'manifest', 'check_status'} or not saved or
                    ref != saved['reference'] or value['manifest'] != saved['snapshot']['manifest'] or
                    value['check_status'] != saved['snapshot']['check_status']):
                raise ValueError('实际捕获事件与准确全部保存快照不一致。')
            events.append({'seq': row['seq'], **value}); seen.add(ref['id'])
        if seen != set(snapshots):
            raise ValueError('原保存快照缺少实际捕获事件，不能补造历史。')
        return events

    def _reservation_event(self, db, run):
        if self.s.collaboration.get(db, 'work_unit_run', run['id']) != run:
            raise ValueError('历史边界须为数据库准确原run。')
        events = list(db.execute("SELECT seq,payload FROM events WHERE task_id=? AND attempt_id=? "
                                 "AND kind='cooperative_unit_reserved' ORDER BY seq",
                                 (run['task_id'], run['attempt_id'])))
        if len(events) != 1 or json.loads(events[0]['payload']) != run:
            raise ValueError('原run缺少唯一准确保留事件。')
        return events[0]['seq']

    def _frame_before(self, db, source, consumer):
        """Rebuild a prior frame at an actual persisted reservation boundary.

        This private temporal decoder still requires the root/run authority
        checks above it. It accepts neither a caller proof nor a timestamp.
        Later captures remain checked but do not enter an earlier frontier.
        """
        boundary = self._reservation_event(db, consumer)
        if self._reservation_event(db, source) >= boundary:
            raise ValueError('前沿来源不是本回合之前的准确运行。')
        attempt = self.s.store.attempt(source['attempt_id'], db)
        raw = self.s.work_units.base_evidence(db, source)
        results = [row['seq'] for row in db.execute(
            "SELECT seq,payload FROM events WHERE task_id=? AND attempt_id=? AND kind='attempt_result_saved' ORDER BY seq",
            (source['task_id'], source['attempt_id'])) if json.loads(row['payload']) == attempt['result']]
        if not results or min(results) >= boundary or not raw['exact_identity'] or raw['blockers']:
            raise ValueError('前沿来源在本回合前没有准确退出结果事件。')
        from .work_coordinator_handoff_context import TERMINAL
        if raw['state'] not in TERMINAL:
            raise ValueError('活动或未知不能伪造回合前准确终态。')
        files, _ = self._saved_files(db, source)
        captures = [v for v in self._capture_events(db, source, files) if v['seq'] < boundary]
        selected = captures[-1]['artifact'] if captures else None
        history = self._historical_evidence(db, source, selected)
        recorded = {v['artifact']['id'] for v in captures}
        files['snapshots'] = [v for v in files['snapshots'] if v['reference']['id'] in recorded]
        files['output_state'] = 'INTACT' if files['snapshots'] else 'NOT_RECORDED'
        return deepcopy({'run': source, 'task_spec': self.s.store.task(source['task_id'], db)['spec'],
                         'workspace': attempt['workspace'], 'evidence': history['raw'],
                         'source_evidence': history['full'], 'files': files})

    def _historical_evidence(self, db, run, artifact):
        """Private reconstruction, with no current directory exception/permit.

        The caller is the authoritative reader, not a consumer-provided proof.
        The selected artifact must be among ALL independently verified original
        snapshots. Its bytes and predicates have already been read above.
        """
        raw = self.s.work_units.base_evidence(db, run)
        if not raw['exact_identity']:
            raise ValueError('原run/result身份不准确。')
        unit = self.s.work_artifacts.unit(db, run)
        files, current = self._saved_files(db, run)
        if artifact is not None and (artifact not in [v['reference'] for v in files['snapshots']] or
                                     not raw['result_available']):
            raise ValueError('原完整证据不能引用不存在或不准确的保存快照。')
        selected = self.s.collaboration.get(db, 'work_unit_artifact', artifact['id']) if artifact else None
        status = {'state': 'CURRENT' if selected else 'MISSING' if unit['outputs'] else 'NOT_REQUIRED',
                  'reference': artifact, 'problems': [],
                  'manifest': selected['snapshot']['manifest'] if selected else {},
                  'check_status': selected['snapshot']['check_status'] if selected else 'NOT_RECORDED',
                  'checks': selected['snapshot']['observations'] if selected else [],
                  'required_outputs': unit['outputs'], 'required_checks': unit['checks'],
                  'independent_review': 'NOT_PERFORMED', 'human_acceptance': 'NOT_RECORDED',
                  'provenance': 'OBSERVED_AFTER_EXACT_TURN_NOT_PROOF_OF_AUTHORSHIP',
                  'delivery': 'ORIGINAL_PROJECT_FILES_REVALIDATED_AT_DISPATCH'}
        full = {**raw, 'result_version': raw['version'], 'file_artifacts': status,
                'check_status': status['check_status']}
        # Match WorkUnits.evidence's existing hash convention, including the
        # original raw version field when computing the full evidence digest.
        full['version'] = digest(full)
        return deepcopy({'raw': raw, 'full': full, 'files': files,
                         'current_artifact': self.s.work_artifacts.reference(current),
                         'current_input_permission': False})

    def _records(self, db, plan, frames):
        """Storage validation is insufficient: require actual supported runs."""
        by_id = {f['run']['id']: f for f in frames}
        found = {kind: [] for kind in (DELIVERY_KIND, COMPLETION_KIND)}
        for kind in found:
            # Also catch a record whose outer item_id was changed while its
            # snapshot still names this root/item. Do not trust the envelope.
            for row in self.s.collaboration.all(db, kind):
                value = row.get('snapshot', {})
                ref = value.get('root', {}) if isinstance(value, dict) else {}
                if (row.get('item_id') != plan['item_id'] and
                        (not isinstance(value, dict) or value.get('item_id') != plan['item_id']) and
                        (not isinstance(ref, dict) or ref.get('id') != plan['id'])):
                    continue
                saved = self.records.get(db, kind, self.records.reference(row))
                value = saved['snapshot']
                if (value['root'] != plan_ref(plan) or value['marker'] != plan[MARKER_FIELD] or
                        saved['item_id'] != plan['item_id']):
                    raise ValueError('新根记录归属或准确根身份不一致。')
                frame = by_id.get(value['run_id'])
                if not frame:
                    raise ValueError('结构记录没有权威重建的真实目标run，不能作为交付证明。')
                run = frame['run']
                if (run['input']['unit']['role'] == 'coordinate' or
                        value['run_version'] != digest(run) or
                        value['input_version'] != run['input_version']):
                    raise ValueError('结构记录目标不是准确普通交接run。')
                # New ordinary input/capability decoding is deliberately not
                # installed in this foundation. There is no hash-based bypass.
                raise ValueError('普通新根阶段历史解码尚未开放。')
        return found

    def _read(self, db, plan):
        changes = db.total_changes
        legacy = WorkCoordinatorHandoffContext(self.s).read(db, plan)
        frames = []
        for old in legacy['prior_frontier']:
            run = old['run']
            self._reservation_event(db, run)
            rebuilt = []
            for prior in frames:
                historical = self._frame_before(db, prior['run'], run)
                # V1 froze full evidence, whereas the new ledger's canonical
                # frontier uses base evidence plus a separate full source.
                rebuilt.append(WorkCoordinatorHandoffContext.frontier_entry(
                    {**historical, 'evidence': historical['source_evidence']}))
            if rebuilt != run['input']['handoff_coordination_context']['prior_frontier']:
                raise ValueError('旧冻结前沿不等于真实保留事件前的全部来源。')
            source = self._historical_evidence(db, run, None)
            if source['files']['snapshots'] or source['current_artifact']:
                raise ValueError('V1只读协调回合不能伪造普通文件产物。')
            if source['full'] != old['evidence'] or source['files'] != old['files']:
                raise ValueError('原完整证据与独立保存字节重建不一致。')
            frames.append({**old, 'evidence': source['raw'], 'source_evidence': source['full']})
        records = self._records(db, plan, frames)
        states = {f['run']['unit_key']: f for f in frames}
        units = [{'definition': u,
                  'qualification': 'HISTORICAL_COORDINATOR' if u['key'] in states else 'NOT_RUN',
                  'source_run': states[u['key']]['run']['id'] if u['key'] in states else None,
                  'raw_state': states[u['key']]['evidence']['state'] if u['key'] in states else None,
                  **PERMISSIONS} for u in plan['units']]
        if db.total_changes != changes or self.s.teamwork.current(db, plan['item_id']) != plan:
            raise ValueError('权威来源读取期间数据库或准确current变化。')
        return deepcopy({'protocol': PROTOCOL, 'root': plan_ref(plan), 'marker': plan[MARKER_FIELD],
                         'task': legacy['task'], 'driver': legacy['driver'],
                         'authorization': legacy['authorization'], 'frames': frames,
                         'frontier': [WorkCoordinatorHandoffContext.frontier_entry(f) for f in frames],
                         'units': units, 'material_observation': legacy['material_observation'],
                         'phase': 'BEFORE_A', 'delivery': records[DELIVERY_KIND],
                         'completion': records[COMPLETION_KIND], **PERMISSIONS,
                         'scope': 'AUTHORITATIVE_REACHABLE_V1_HISTORY; ORDINARY_PHASE_RUNTIME_NOT_OPEN'})

    def read(self, db, plan):
        try:
            from .work_coordinator_handoff_runtime import enabled, reader
            if enabled(self.s, db, plan):
                return reader(self.s, db, plan).read(db, plan)
            return self._read(db, plan)
        except (LookupError, KeyError, TypeError, AttributeError, StopIteration) as exc:
            raise ValueError('权威根、记录或原来源字段无效。') from exc
