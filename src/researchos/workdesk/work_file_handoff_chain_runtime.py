"""Internal current-path runtime observation; no user-supplied ancestry or grants."""
from copy import deepcopy

from .work_file_handoff_chain import WorkFileHandoffChain, APPLICATION, CANDIDATE
from .work_file_handoff_apply import APPLICATION_KIND
from .work_file_reworks import original_files
from .work_file_handoff_rework_path import WorkFileHandoffReworkPath
from .work_file_handoff_unit_revision_path import WorkFileHandoffUnitRevisionPath
from .work_file_handoff_file_rework_path import WorkFileHandoffFileReworkPath


def _uses_protocol(apps, db, plan, application_protocols, candidate_protocols):
    """Route by persisted ancestry and current candidate, never the last edge alone."""
    if not plan or not plan.get('file_handoff_lineage'):
        return False
    candidate = apps.current(db, plan['item_id'])
    if candidate and candidate['snapshot'].get('protocol') in candidate_protocols:
        return True
    seen = set()
    while plan.get('file_handoff_lineage'):
        if plan['id'] in seen or len(seen) >= 32:
            raise ValueError('交接路径循环或超过32层。')
        seen.add(plan['id'])
        if plan['file_handoff_lineage'].get('protocol') in application_protocols:
            return True
        previous = plan.get('previous_plan_reference')
        if not previous:
            break
        plan = apps.s.collaboration.get(db, 'teamwork_plan', previous['id'])
    return False


def uses_file_rework(apps, db, plan):
    from .work_file_handoff_file_reworks import APPLICATION as FILE, CANDIDATE as FILE_CANDIDATE
    return _uses_protocol(apps, db, plan, (FILE,), (FILE_CANDIDATE,))


def uses_unit_revision(apps, db, plan):
    from .work_file_handoff_unit_revisions import APPLICATION as UNIT, CANDIDATE as UNIT_CANDIDATE
    return _uses_protocol(apps, db, plan, (UNIT,), (UNIT_CANDIDATE,))


def uses_rework(apps, db, plan):
    from .work_file_handoff_text_reworks import APPLICATION as REWORK, CANDIDATE as REWORK_CANDIDATE
    return uses_unit_revision(apps, db, plan) or _uses_protocol(apps, db, plan, (REWORK,), (REWORK_CANDIDATE,))


def saved_path(service, db, plan):
    if uses_file_rework(service.work_file_handoff_applications, db, plan):
        from .work_file_handoff_file_rework_path import WorkFileHandoffFileReworkPath
        return WorkFileHandoffFileReworkPath(service)
    if uses_unit_revision(service.work_file_handoff_applications, db, plan):
        return WorkFileHandoffUnitRevisionPath(service)
    if uses_rework(service.work_file_handoff_applications, db, plan):
        from .work_file_handoff_rework_path import WorkFileHandoffReworkPath
        return WorkFileHandoffReworkPath(service)
    return WorkFileHandoffChain(service)


def uses_path(apps, db, plan):
    if not plan or not plan.get('file_handoff_lineage'):
        return False
    if uses_file_rework(apps, db, plan) or uses_rework(apps, db, plan):
        return True
    if plan['file_handoff_lineage'].get('protocol') == APPLICATION:
        return True
    candidate = apps.current(db, plan['item_id'])
    return bool(candidate and candidate['snapshot'].get('protocol') == CANDIDATE)


class _RuntimeChain(WorkFileHandoffChain):
    def __init__(self, service, reservation):
        super().__init__(service)
        self.reservation = reservation

    def _context(self, db, plan, run):
        item = self.s.work_items.item(db, plan['item_id'])
        return item, self.s.work_drivers.current(db, item['id'])

    def _terminal_rows(self, db, plan, rows):
        run = self.reservation
        if run is None:
            return rows
        item, driver = self._context(db, plan, run)
        edge = self._saved_edge(db, self._path(db, plan)[-2], plan)
        auth = self.s.work_units.authorization(db, driver)
        attempt = self.s.store.attempt(run['attempt_id'], db)
        if (self.s.work_units.reservation(db, run['attempt_id']) != run or
                run['command_id'] != self.s.command_identity or run['item_id'] != item['id'] or
                run['item_revision'] != item['revision'] or run['plan_id'] != plan['id'] or
                run['plan_version'] != plan['version'] or driver is None or driver['state'] != 'RESERVED' or
                driver['id'] != edge['paused_driver']['id'] or driver['generation'] != edge['paused_driver']['generation'] or
                driver['item_revision'] != item['revision'] or driver['plan_id'] != plan['id'] or
                driver['plan_version'] != plan['version'] or run['generation'] != driver['generation'] or
                run['driver_revision'] != driver['revision'] or auth != edge['authorization'] or
                run['driver_id'] != driver['id'] or
                any(run['cooperative'].get(k) != run[k] for k in
                    ('driver_id','generation','driver_revision','authorization')) or
                run['cooperative'].get('source_item_revision') != item['revision'] or
                run['ordinal_total'] != len(rows) or run['flow'] is not None or
                run['scheduling'] != 'EXPLICIT_SERIAL_STEP' or
                run['authorization'] != self.s.work_units.auth_ref(auth) or
                attempt['state'] != 'RESERVED' or attempt['task_id'] != run['task_id'] or
                attempt['owner'] != run['owner'] or original_files(self.s, db, run)['input_problems']):
            raise ValueError('祖先路径只排除本命令准确reservation，任务、责任、授权、权限或实际材料不一致。')
        return [r for r in rows if r['id'] != run['id']]


class _BoundRuntimeChain(_RuntimeChain):
    """The final binding has advanced the business/driver revisions, exactly once."""
    def _context(self, db, plan, run):
        item, driver = super()._context(db, plan, run)
        prior = self.s.collaboration.get(db, 'work_driver_revision', 'driver-version:' + run['driver_revision'])
        original = {k:v for k,v in prior.items() if k != 'driver_id'}
        original['id'] = prior['driver_id']
        expected = {**original, 'item_revision':item['revision'],
            'task_ids':sorted(set(original['task_ids']) | {run['task_id']}), 'last_unit_run':run['id'],
            'reason':'OWNED_EXECUTION_LINKED', 'revision':driver['revision'], 'updated':driver['updated'],
            'revision_lineage':[*original.get('revision_lineage',[]),
                {'from':run['item_revision'],'to':item['revision'],'unit_run_id':run['id']}][-100:]}
        dispatch = self.s.collaboration.get(db, 'work_dispatch', 'dispatch:' + run['attempt_id'])
        if (driver != expected or driver['revision'] == run['driver_revision'] or
                any(dispatch.get(k) != v for k,v in {'item_id':item['id'],'item_revision':item['revision'],
                    'task_id':run['task_id'],'attempt_id':run['attempt_id'],'command_id':self.s.command_identity,
                    'command_kind':'work-item.unit-send','cooperative':run['cooperative']}.items()) or
                not db.execute('SELECT 1 FROM work_item_records WHERE item_id=? AND kind=? AND record_id=?',
                    (item['id'],'task',run['task_id'])).fetchone()):
            raise ValueError('最终交接运行绑定与准确任务、责任修订或本命令派发记录不一致。')
        _, issues = self.s.work_drivers.readiness(db, item, driver, excluding={run['attempt_id']})
        if issues:
            raise ValueError('最终交接运行绑定仍有未知、人工或共享责任冲突。')
        return {**item,'revision':run['item_revision']}, original


class _RuntimeReworkPath(_RuntimeChain, WorkFileHandoffReworkPath):
    """Exact reservation contract with each saved edge's own validator."""


class _BoundRuntimeReworkPath(_BoundRuntimeChain, WorkFileHandoffReworkPath):
    """Final business binding plus the complete mixed saved path."""


class _RuntimeUnitRevisionPath(_RuntimeChain, WorkFileHandoffUnitRevisionPath):
    """Exact reservation with complete ordinary-unit revision ancestry."""


class _BoundRuntimeUnitRevisionPath(_BoundRuntimeChain, WorkFileHandoffUnitRevisionPath):
    """Final binding with each mixed edge's exact frozen context."""


class _RuntimeFileReworkPath(_RuntimeChain, WorkFileHandoffFileReworkPath):
    def verify(self, db, plan):
        # FILE's public fresh constructor has no reservation. Runtime must keep
        # only its exact internal reservation while obtaining a new observation.
        with self.s.work_unit_revision_applications.validation_snapshot(db):
            fresh = type(self)(self.s, deepcopy(self.reservation))
            try:
                return fresh._verify(db, plan)
            except (KeyError, TypeError, AttributeError, StopIteration, IndexError) as exc:
                raise ValueError('FILE运行路径完整记录或内部reservation不一致。') from exc

    def _local(self, db, plan, run, edge, root):
        super()._local(db, plan, run, edge, root)
        if self.reservation is not None and run['id'] == self.reservation['id']:
            baseline = run['input'].get('write_before_baseline')
            if baseline and self.s.work_file_rework_applications.observe(root, baseline['paths']) != baseline:
                raise ValueError('FILE运行许可或最终绑定时逐单元写前基线已变化。')


class _BoundRuntimeFileReworkPath(_BoundRuntimeChain, _RuntimeFileReworkPath):
    """Exactly one business binding plus independent FILE observation."""


class _RuntimeObservation:
    chain_type = _RuntimeChain
    rework_type = _RuntimeReworkPath
    unit_revision_type = _RuntimeUnitRevisionPath
    file_rework_type = _RuntimeFileReworkPath

    def __init__(self, apps, db, plan, *, reservation=None):
        self.apps, self.db, self.plan = apps, db, deepcopy(plan)
        self.problem, self.proof, self.evidence = None, None, {}
        try:
            if uses_file_rework(apps, db, plan):
                chain = self.file_rework_type(apps.s, reservation)
            elif uses_unit_revision(apps, db, plan):
                chain = self.unit_revision_type(apps.s, reservation)
            elif uses_rework(apps, db, plan):
                chain = self.rework_type(apps.s, reservation)
            else:
                chain = self.chain_type(apps.s, reservation)
            self.proof = chain.verify(db, plan)
            self.plans = {p['id']: p for p in chain._path(db, plan)}
            ledger = apps.ledger
            root = ledger.get(db, APPLICATION_KIND, self.proof['edges'][0])['snapshot']['lineage']['proof']
            self.proof.update(application=self.proof['edges'][-1], delivery=root['delivery'], completion=root['completion'])
            self.evidence = {e['run_id']: e['evidence'] for e in self.proof['frontier']}
        except (ValueError, KeyError, TypeError, AttributeError, StopIteration) as exc:
            self.problem = str(exc) or '祖先路径完整身份不一致。'
        self.changes = db.total_changes

    def require(self, apps, db, plan):
        if (apps is not self.apps or db is not self.db or db.total_changes != self.changes or
                apps.s.teamwork.current(db, self.plan['item_id']) != self.plan):
            raise ValueError('运行路径观察已脱离本次Service、事务或当前完整方案。')
        if self.problem:
            raise ValueError(self.problem)
        if self.plans.get(plan['id']) != plan or apps.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan:
            raise ValueError('运行来源不是本次当前消费者的准确祖先方案。')
        return deepcopy(self.proof)


def observe(apps, db, plan, *, reservation=None):
    observation = _RuntimeObservation(apps, db, plan, reservation=reservation)
    observation.require(apps, db, plan)
    return observation


class _BoundRuntimeObservation(_RuntimeObservation):
    chain_type = _BoundRuntimeChain
    rework_type = _BoundRuntimeReworkPath
    unit_revision_type = _BoundRuntimeUnitRevisionPath
    file_rework_type = _BoundRuntimeFileReworkPath


def require_bound(apps, db, plan, run):
    _BoundRuntimeObservation(apps, db, plan, reservation=run).require(apps, db, plan)
