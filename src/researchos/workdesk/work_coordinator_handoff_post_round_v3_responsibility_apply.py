"""Atomic same-generation responsibility changes; successor runtime stays closed."""
from contextvars import ContextVar
from copy import deepcopy
import time

from .store import digest, uid
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import versioned, PERMISSIONS
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_candidates import ref
from . import work_coordinator_handoff_post_round_v3_responsibility_records as r

_pending = ContextVar('post_round_v3_responsibility_application', default=None)


def pending(service, db, application, row, registration):
    """Only the last exact transition in this active writer may be uncommitted."""
    state = _pending.get()
    return bool(type(application) is WorkCoordinatorHandoffPostRoundV3ResponsibilityApplications and
        state is not None and state['application'] is application and application.s is service and
        state['service'] is service and state['db'] is db and db.in_transaction and
        state['changes'] == db.total_changes and state['instance'] == service.instance and
        state['identity'] == row['command_id'] == service.command_identity and
        state['kind'] == service.command_kind == r.COMMAND and
        state['request'] == row['snapshot']['request'] and
        state['request_hash'] == digest(row['snapshot']['request']) == service.command_payload_hash and
        state['row'] == row and state['registration'] == registration and
        state['receipt'] == r.receipt(row, registration) and
        db.execute('SELECT 1 FROM commands WHERE id=?', (row['command_id'],)).fetchone() is None)


class WorkCoordinatorHandoffPostRoundV3ResponsibilityApplications:
    def __init__(self, service):
        self.s = service

    @closed
    def change(self, db, request):
        r.request_shape(request)
        if (not db.in_transaction or self.s.command_kind != r.COMMAND or
                self.s.command_payload_hash != digest(request) or _pending.get() is not None):
            raise ValueError('V3责任变更须属于准确原请求的唯一拥有者事务。')
        r._uuid(self.s.command_identity)
        if db.execute('SELECT 1 FROM commands WHERE id=?', (self.s.command_identity,)).fetchone():
            raise ValueError('V3责任变更命令已存在；须由Store核对重放。')
        db.execute('SAVEPOINT post_round_v3_responsibility')
        token = None
        try:
            from .work_coordinator_handoff_post_round_v3_responsibility_current import WorkCoordinatorHandoffPostRoundV3ResponsibilityCurrent as Current
            item = self.s.work_items.checked(db, request)
            self.s.work_items.project(db, item['project_id'])
            plan = self.s.teamwork.current(db, item['id'])
            if not plan or plan_ref(plan) != request['plan']:
                raise ValueError('V3责任方案已变化。')
            original = Current(self.s).read(db, plan)
            self._source(db, request, original)
            if request['action'] == 'resume' and any(i['code'] != 'V3_LIFECYCLE_CLOSED'
                    for i in original['responsibility_conditions']):
                raise ValueError('V3责任恢复须核对当前任务、资源及全部关联执行。')
            row = self._row(original, request)
            # Persist exact values, not generic driver events or a temporary command.
            after = row['snapshot']['driver_after']
            self.s.collaboration.put(db, 'work_driver', after)
            self.s.collaboration.put(db, 'work_driver_revision', {**after,
                'id': 'driver-version:' + after['revision'], 'driver_id': after['id']})
            self.s.store.event(None, None, r.DRIVER_EVENTS[request['action']], {**after, 'no_dispatch': True}, db)
            self.s.collaboration.put(db, r.KIND, row)
            self.s.store.event(None, None, r.EVENT, row, db)
            receipt = r.receipt(row, original['registration'])
            state = {'application': self, 'service': self.s, 'db': db,
                'instance': self.s.instance, 'identity': self.s.command_identity, 'kind': r.COMMAND,
                'request': deepcopy(request), 'request_hash': digest(request), 'row': deepcopy(row),
                'registration': deepcopy(original['registration']), 'receipt': deepcopy(receipt),
                'changes': db.total_changes}
            token = _pending.set(state)
            current = Current(self.s, _application=self).read(db, plan)
            if (current['driver'] != after or current['authorization'] != original['authorization'] or
                    current['registration'] != original['registration'] or
                    current['responsibility']['transitions'] != original['responsibility']['transitions'] + [row] or
                    current['material_observation'] != original['material_observation'] or
                    current['saved_path'] != original['saved_path'] or
                    self._usage(current) != self._usage(original) or
                    not pending(self.s, db, self, row, original['registration'])):
                raise ValueError('V3责任结束完整来源、累计、材料或拥有者发生变化。')
            self._source(db, request, current, after=after)
            if request['action'] == 'resume' and time.time() >= original['authorization']['dispatch_before']:
                raise ValueError('V3责任恢复结束已超过原最初截止。')
        except BaseException:
            db.execute('ROLLBACK TO post_round_v3_responsibility')
            db.execute('RELEASE post_round_v3_responsibility')
            raise
        else:
            db.execute('RELEASE post_round_v3_responsibility')
        finally:
            if token is not None: _pending.reset(token)
        return deepcopy(receipt)

    @staticmethod
    def _usage(value):
        return {k: v for k, v in value['usage'].items() if k != 'elapsed_observed_seconds'}

    def _source(self, db, request, current, *, after=None):
        driver = current['driver']; item = self.s.work_items.checked(db, request)
        if (self.s.teamwork.current(db, item['id']) != current['child_plan'] or
                self.s.work_drivers.current(db, item['id']) != driver or
                self.s.work_units.authorization(db, driver) != current['authorization'] or
                (self.s.work_drivers.reference(driver) != request['current_driver'] if after is None else driver != after) or
                self.s.work_units.auth_ref(current['authorization']) != request['authorization'] or
                ref(current['registration']) != request['saved_registration'] or
                r.frontier(current) != request['frontier'] or
                self.s.work_drivers.execution_issues(db, item['id'], driver)):
            raise ValueError('V3责任准确八字段来源或关联执行已变化。')

    def _row(self, original, request):
        before = original['driver']; action = request['action']; now = time.time()
        after = {**deepcopy(before), 'revision': uid(), 'updated': now,
            'state': r.STATES[action][1], 'reason': r.REASONS[action]}
        if action == 'resume': after['instance'] = self.s.instance
        if action == 'release': after['released'] = now
        previous = original['responsibility']['transitions']
        body = versioned({'protocol': r.TRANSITION, 'scope': r.SCOPE, 'action': action,
            'item_id': before['item_id'], 'plan': plan_ref(original['child_plan']),
            'saved_registration': ref(original['registration']),
            'original_driver': self.s.work_drivers.reference(original['original_driver']),
            'previous_transition': ref(previous[-1]) if previous else None,
            'driver_before': before, 'driver_after': after, 'authorization': original['authorization'],
            'usage': original['usage'], 'original_dispatch_before': original['authorization']['dispatch_before'],
            'request': request, 'command_id': self.s.command_identity, 'restart_id': None,
            'instance': self.s.instance, 'reason': r.REASONS[action], 'automatically_resumed': False, **PERMISSIONS})
        row = {'id': r.KIND + ':' + body['version'], 'version': body['version'], 'snapshot': body,
            'item_id': before['item_id'], 'created': now, 'command_id': self.s.command_identity}
        r.adjacent(row, {**original, 'driver': original['original_driver']}, previous[-1] if previous else None)
        return row
