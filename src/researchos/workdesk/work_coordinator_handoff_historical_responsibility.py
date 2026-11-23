"""Original root selection/authorization and pre-V3 responsibility commands.

Drivers are rebuilt as actual transforms, not admitted by decoding revisions.
Full command admission is deferred until the independent run replay has proved
the frozen member/connection selections and every associated original exit.
"""
from copy import deepcopy

from .store import digest
from .teamwork import single_step_supported
from .work_transitions import plan_ref
from .work_coordinator_handoff_adjustment_records import original_driver, original_authorization, finite
from .work_coordinator_handoff_historical_assets import _json, _uuid


class HistoricalResponsibility:
    def __init__(self, sources, timeline, ledger, replay):
        if (timeline.sources is not sources or ledger.sources is not sources or
                replay.sources is not sources or not sources.db.in_transaction):
            raise ValueError('原责任须属于本次真实来源和独立重放事务。')
        self.sources, self.timeline, self.ledger, self.replay = sources, timeline, ledger, replay
        self.db = sources.db
        self.pending = []
        self.drivers, self.authorizations = {}, {}

    @staticmethod
    def driver_ref(value):
        return {k: value[k] for k in ('id', 'generation', 'revision')} if value else None

    def _record(self, event, driver, owner):
        self.ledger.row('work_driver_revision', {**driver, 'id': 'driver-version:' + driver['revision'],
            'driver_id': driver['id']}, owner)
        self.ledger.event_at(event['seq'], event['kind'], {**driver, 'no_dispatch': True}, owner)
        self.drivers[driver['revision']] = deepcopy(driver)

    def root(self):
        facts = self.timeline.root_facts(); plan, item = facts['plan'], facts['item']
        event, driver = self.timeline.driver_versions(plan)[0]
        original_driver(driver)
        expected = {'id': 'work-driver:' + item['id'], 'item_id': item['id'],
            'item_revision': item['revision'], 'generation': driver['generation'], 'kind': 'COOPERATIVE',
            'plan_id': plan['id'], 'plan_version': plan['version'], 'state': 'RESERVED',
            'reason': 'EXPLICIT_SELECTION', 'instance': driver['instance'], 'previous_generation': None,
            'task_ids': [], 'created': driver['created'], 'scope': 'WORKDESK_DISPATCH_GUARD; NOT_AN_OS_LOCK',
            'revision': driver['revision'], 'updated': driver['updated']}
        if (driver != expected or event['kind'] != 'work_driver_acquire' or item['record_refs'] or
                self.timeline.business_before(event['seq']) != item or
                not plan['created'] <= driver['created'] <= driver['updated'] <= event['created']):
            raise ValueError('原根责任须由无旧执行的准确初次选择生成。')
        for name in ('generation', 'instance', 'revision'):
            _uuid(driver[name])
        if facts['event'] is not None:
            self._root_command(facts)
        self.ledger.row('teamwork_plan', plan, 'root:' + plan['id'])
        if facts['event'] is not None:
            payload = {'work_item_id': item['id'], 'item_revision': item['revision'],
                'plan_id': plan['id'], 'version': plan['version'], 'mode': plan['mode'], 'no_dispatch': True}
            self.ledger.event_at(facts['event']['seq'], facts['event']['kind'], payload, 'root:' + plan['id'])
        owner = 'driver:' + driver['revision']; self._record(event, driver, owner)
        request = {'id': item['id'], 'revision': item['revision'], 'action': 'acquire',
            'current_driver': None, 'plan': plan_ref(plan)}
        self.pending.append({'plan': plan, 'driver': driver, 'event': event, 'request': request,
            'kind': 'work-item.driver', 'owner': owner})
        return deepcopy(driver)

    def authorize(self):
        plan = self.timeline.root
        found = [r for r in self.sources.all(self.db, 'work_unit_authorization_revision')
            if r.get('plan_id') == plan['id'] and r.get('item_id') == plan['item_id']]
        if len(found) != 1:
            raise ValueError('原根须有唯一不可重授的完整原授权。')
        carrier = found[0]
        auth = {**carrier, 'id': 'unit-auth:' + carrier['driver_generation']}
        original_authorization(auth)
        finite(auth['created'])
        expected = {'id': 'unit-auth:' + auth['driver_generation'], 'revision': auth['revision'],
            'item_id': plan['item_id'], 'driver_generation': auth['driver_generation'],
            'plan_id': plan['id'], 'plan_version': plan['version'],
            'max_attempts_total': auth['max_attempts_total'], 'dispatch_before': auth['dispatch_before'],
            'created': auth['created'], 'already_used': 0, 'scope': 'ALL_COOPERATIVE_ATTEMPTS_FOR_BUSINESS_ITEM',
            'deadline_enforcement': 'NEW_DISPATCH_ONLY', 'cash_enforcement': 'UNSUPPORTED',
            'native_permissions': 'VENDOR_DEFAULTS', 'command_id': auth['command_id']}
        if auth != expected:
            raise ValueError('原初次授权的完整常量和累计语义不符。')
        event = self.sources._event(self.db, 'cooperation_authorized', auth)
        driver = self.timeline.driver_before(plan, event['seq'])
        if (self.drivers.get(driver['revision']) != driver or driver['state'] != 'RESERVED' or
                auth['driver_generation'] != driver['generation'] or
                auth['plan_version'] != plan['version'] or auth['already_used'] != 0 or
                not driver['updated'] <= auth['created'] <= event['created'] < auth['dispatch_before'] or
                auth['dispatch_before'] > auth['created'] + 7 * 86400 or
                type(auth['max_attempts_total']) is not int or not 0 < auth['max_attempts_total'] <= 1000 or
                carrier['id'] != 'unit-auth-version:' + auth['revision'] or
                self.replay.frames_before(event['seq'])):
            raise ValueError('原授权须保留初次准确责任、零已用和最初累计截止。')
        _uuid(auth['revision']); _uuid(auth['command_id'])
        owner = 'authorization:' + auth['revision']
        self.ledger.row('work_unit_authorization_revision', carrier, owner)
        self.ledger.event_at(event['seq'], event['kind'], auth, owner)
        self.authorizations[plan['id']] = deepcopy(auth)
        request = {'id': plan['item_id'], 'revision': driver['item_revision'],
            'current_driver': self.driver_ref(driver), 'plan': plan_ref(plan), 'authorization': None,
            'max_attempts_total': auth['max_attempts_total'], 'dispatch_before': auth['dispatch_before']}
        self.pending.append({'plan': plan, 'driver': driver, 'event': event, 'request': request,
            'kind': 'work-item.cooperation-authorize', 'owner': owner, 'authorization': auth})
        return deepcopy(auth)

    def saved_start(self, saved):
        """A saved owner must already have proved the full edge and command."""
        plan = saved['child_plan']
        original = saved.get('original', saved)
        driver, auth = original['driver'], original['authorization']
        event, first = self.timeline.driver_versions(plan)[0]
        carrier = {**driver, 'id': 'driver-version:' + driver['revision'], 'driver_id': driver['id']}
        actual = self.ledger.rows.get(('work_driver_revision', carrier['id']))
        auth_carrier = {**auth, 'id': 'unit-auth-version:' + auth['revision']}
        actual_auth = self.ledger.rows.get(('work_unit_authorization_revision', auth_carrier['id']))
        if (first != driver or actual is None or actual[0] != carrier or
                actual_auth is None or actual_auth[0] != auth_carrier or event['seq'] not in self.ledger.events or
                saved['candidate']['command_id'] not in self.ledger.commands):
            raise ValueError('原后继责任只能起于本次已完整证明的保存转移。')
        self.drivers[driver['revision']] = deepcopy(driver)
        self.authorizations[plan['id']] = deepcopy(auth)

    def transition(self, plan, event, driver):
        versions = self.timeline.driver_versions(plan)
        index = next((i for i, pair in enumerate(versions) if pair == (event, driver)), None)
        if index is None or index == 0:
            raise ValueError('原责任变换须有准确实际前驱。')
        previous_event, previous = versions[index - 1]
        if self.drivers.get(previous['revision']) != previous:
            # Execution links belong exclusively to run:<id>, and have already
            # been independently reconstructed by HistoricalReplay.advance.
            carrier = self.ledger.rows.get(('work_driver_revision', 'driver-version:' + previous['revision']))
            if carrier is None or previous_event['seq'] not in self.ledger.events:
                raise ValueError('原责任变换缺少已重建的前一完整修订。')
        actions = {'work_driver_pause': ('pause', 'RESERVED', 'PAUSED', 'EXPLICIT_PAUSE'),
            'work_driver_resume': ('resume', 'PAUSED', 'RESERVED', 'EXPLICIT_RECHECK'),
            'work_driver_restart_paused': (None, 'RESERVED', 'PAUSED', 'CONTROLLER_RESTART'),
            'work_driver_release': ('release', None, 'RELEASED', 'EXPLICIT_RELEASE')}
        if event['kind'] not in actions:
            raise ValueError('该原责任变化须由执行link、保存边或已核实生命周期单独重建。')
        action, before, after, reason = actions[event['kind']]
        expected = {**previous, 'state': after, 'reason': reason,
            'revision': driver['revision'], 'updated': driver['updated']}
        if action == 'resume':
            expected['instance'] = driver['instance']; _uuid(driver['instance'])
        if action == 'release':
            expected['released'] = driver['released']; finite(driver['released'])
            if not previous['updated'] <= driver['released'] <= driver['updated']:
                raise ValueError('原释放时间不属于准确责任变换。')
        if (driver != expected or (previous['state'] not in {'RESERVED', 'PAUSED'} if action == 'release' else previous['state'] != before) or
                not previous['updated'] <= driver['updated'] <= event['created']):
            raise ValueError('原责任修订未按准确暂停、重启或恢复算法变换。')
        owner = 'driver:' + driver['revision']; self._record(event, driver, owner)
        if action is not None:
            request = {'id': plan['item_id'],
                'revision': self.timeline.business_before(event['seq'])['revision'],
                'action': action, 'current_driver': self.driver_ref(previous), 'plan': plan_ref(plan)}
            self.pending.append({'plan': plan, 'driver': driver, 'event': event, 'request': request,
                'kind': 'work-item.driver', 'owner': owner})
        return deepcopy(driver)

    def _resources(self, pending, command, request):
        """Replay the real command's simultaneous resource observation.

        A later dispatch only proves its own selection. Older commands without
        this carrier cannot establish historical mutable resource availability.
        """
        from .work_readiness_observations import EVENT, PROTOCOL, SCOPE, RESOURCE_FIELDS, selected_resources
        from .work_coordinator_handoff_records import fields
        plan, driver, event = (pending[k] for k in ('plan', 'driver', 'event'))
        item = self.timeline.business_before(event['seq'])
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_readiness_observations'").fetchone():
            raise ValueError('原责任命令缺少同时刻资源观察，不能以较晚派发反推历史readiness。')
        raw_observation = self.db.execute('SELECT * FROM work_readiness_observations WHERE id=?',
            ('work-readiness:' + command['id'],)).fetchone()
        if raw_observation is None:
            raise ValueError('原责任命令缺少同时刻资源观察，不能以较晚派发反推历史readiness。')
        row = _json(raw_observation['data'])
        fields(row, {'id', 'item_id', 'command_id', 'created', 'snapshot', 'version'})
        finite(row['created'])
        snapshot = row['snapshot']
        fields(snapshot, {'protocol', 'item_revision', 'plan', 'driver', 'command_kind', 'request_hash', 'resources', 'scope', 'no_dispatch'})
        resources = snapshot['resources']
        fields(resources, set(RESOURCE_FIELDS))
        for key, names in RESOURCE_FIELDS.items():
            if not isinstance(resources[key], list) or len(resources[key]) > len(plan['units']) + 2:
                raise ValueError('原责任资源观察超出其声明成员范围。')
            for value in resources[key]:
                fields(value, names)
                if not isinstance(value['id'], str) or not value['id'] or type(value['revision']) not in (str, int):
                    raise ValueError('原责任资源观察身份或修订无效。')
                if key == 'members' and type(value['available']) is not bool:
                    raise ValueError('原责任资源可用性须为同时刻真实布尔观察。')
                if any(not isinstance(value[name], str) and not (name in {'model', 'effort', 'tier'} and value[name] is None)
                        for name in names - {'revision', 'available'}):
                    raise ValueError('原责任资源观察不得包装其它协议或嵌套载体。')
        members = {member['id']: member for member in resources['members']}
        for selection in [plan['coordinator'], plan['integrator'], *(u['member'] for u in plan['units'])]:
            member = members.get(selection['reference']['member_id'])
            if member and member['revision'] == selection['reference']['member_revision'] and any(
                    member[name] != selection[name] for name in ('name', 'role', 'provider', 'model', 'effort', 'tier')):
                raise ValueError('原责任同修订成员观察须保持原声明完整配置。')
        expected = {'protocol': PROTOCOL, 'item_revision': item['revision'], 'plan': plan_ref(plan),
            'driver': self.driver_ref(driver), 'command_kind': pending['kind'], 'request_hash': digest(request),
            'resources': selected_resources(plan, resources), 'scope': SCOPE, 'no_dispatch': True}
        pointer = {'id': row['id'], 'version': row['version'], 'no_dispatch': True}
        observed = self.sources._event(self.db, EVENT, pointer)
        if (snapshot != expected or row['item_id'] != item['id'] or row['command_id'] != command['id'] or
                row['version'] != digest(expected) or not event['seq'] < observed['seq'] or
                not event['created'] <= row['created'] <= observed['created'] <= command['created'] or
                self.timeline.business_before(observed['seq']) != item or
                (self.timeline.driver_before(plan, observed['seq']) != driver if driver is not None else
                    any(e['seq'] < observed['seq'] for e, _ in self.timeline.driver_versions(plan)))):
            raise ValueError('原责任资源观察须绑定原命令及其准确同时刻责任、业务版本和事件。')
        self.ledger.readiness_observation(row, pending['owner'])
        self.ledger.event_at(observed['seq'], EVENT, pointer, pending['owner'])
        return deepcopy(resources)

    def _root_command(self, facts):
        """The public root save owns a real simultaneous catalog observation."""
        from .teamwork import batches
        from .work_readiness_observations import resource_changes
        from .work_coordinator_handoff_roots import CONFIG_FIELDS
        from .work_coordinator_handoff_runtime import CAPABILITY_KIND
        plan, item, event = facts['plan'], facts['item'], facts['event']
        caps = [row for row in self.sources.all(self.db, CAPABILITY_KIND)
            if row.get('snapshot', {}).get('root') == plan_ref(plan)]
        if len(caps) != 1:
            raise ValueError('公共原根保存须有同命令唯一原runtime能力。')
        command = self.db.execute('SELECT * FROM commands WHERE id=?', (caps[0]['command_id'],)).fetchone()
        if command is None or command['kind'] != 'work-item.teamwork-save':
            raise ValueError('公共原根保存缺少准确已提交原命令。')
        opened = self.sources._event(self.db, 'coordinator_runtime_opened', caps[0])
        if not event['seq'] < opened['seq']:
            raise ValueError('公共原根runtime开启须晚于准确准备事件。')
        configuration = deepcopy({k: plan[k] for k in CONFIG_FIELDS})
        for name in ('coordinator', 'integrator'):
            configuration[name] = configuration[name]['reference']
        for unit in configuration['units']:
            unit['member'] = unit['member']['reference']
        request = {'id': item['id'], 'revision': item['revision'], 'current_plan': None, **configuration}
        owner = 'root:' + plan['id']
        resources = self._resources({'plan': plan, 'driver': None, 'event': opened,
            'kind': command['kind'], 'owner': owner}, command, request)
        changed = resource_changes(plan, resources)
        supported = single_step_supported(plan)
        blockers = ([] if supported else ['COOPERATIVE_DISPATCH_NOT_CONNECTED'])
        if changed:
            blockers.append('RESOURCE_CHANGED_OR_UNAVAILABLE')
        if plan['hard_cash_limit_cny'] is not None:
            blockers.append('HARD_CASH_LIMIT_UNSUPPORTED')
        view = {'plan': plan, 'stale': False, 'resource_changes': changed, 'blockers': blockers,
            'structure_batches': batches(plan['units'], plan['max_parallel']),
            'quota_sources': sorted({selection['member']['reference']['quota_id'] for selection in plan['units']} |
                {plan[name]['reference']['quota_id'] for name in ('coordinator', 'integrator')}),
            'quota_enforcement': 'INFORMATION_ONLY', 'write_scope_enforcement': 'DECLARED_NOT_ENFORCED',
            'dispatch_supported': supported, 'single_step_supported': supported, 'continuous_supported': False,
            'handoff_configuration_locked': True, 'driver_acquired': False, 'no_dispatch': True}
        self.ledger.command(command['id'], command['kind'], request,
            {'teamwork': view, 'no_dispatch': True, 'execution_unchanged': True}, owner, not_before=event['created'])

    def _view(self, pending, command, request):
        from .work_drivers import WorkDrivers
        from .work_readiness_observations import resource_changes
        plan, driver, event = (pending[k] for k in ('plan', 'driver', 'event'))
        item = self.timeline.business_before(event['seq'])
        resources = self._resources(pending, command, request)
        issues = []
        if resource_changes(plan, resources):
            issues.append(WorkDrivers.issue('RESOURCE_CHANGED_OR_UNAVAILABLE'))
        inherited = (driver['state'] in {'RESERVED', 'PAUSED'} and driver['plan_id'] == plan['id'] and
            driver['plan_version'] == plan['version'] and driver['item_revision'] == item['revision'])
        if (plan['item_revision'] != item['revision'] and not inherited) or plan['project_id'] != item['project_id']:
            issues.append(WorkDrivers.issue('WORK_ITEM_CHANGED'))
        if item['state'] != 'OPEN':
            issues.append(WorkDrivers.issue('WORK_ITEM_NOT_OPEN'))
        if plan['hard_cash_limit_cny'] is not None:
            issues.append(WorkDrivers.issue('HARD_CASH_LIMIT_UNSUPPORTED'))
        # Only prior, independently reconstructed runs participate in this
        # original guard. A later PAUSED task does not replace the actual exit.
        prior = self.replay.frames_before(event['seq'])
        expected_tasks = sorted(f['run']['task_id'] for f in prior)
        if (driver['task_ids'] != expected_tasks or item['record_refs'] != sorted(
                (r for r in item['record_refs'] if r['kind'] == 'task' and r['record_id'] in expected_tasks),
                key=lambda r: (r['kind'], r['record_id'])) or
                {r['record_id'] for r in item['record_refs']} != set(expected_tasks)):
            raise ValueError('原责任回执关联执行集合未由原前沿完整证明。')
        for task in expected_tasks:
            if self.db.execute('SELECT 1 FROM work_item_records WHERE kind=? AND record_id=? AND item_id<>?',
                    ('task', task, item['id'])).fetchone() is not None or self.db.execute(
                    'SELECT 1 FROM chain_members a JOIN chain_members b ON a.chain_id=b.chain_id '
                    'WHERE a.task_id=? AND b.task_id NOT IN (' + ','.join('?' for _ in expected_tasks) + ')',
                    (task, *expected_tasks)).fetchone() is not None:
                raise ValueError('原责任存在尚未独立核实的共享执行或外部链责任。')
        for frame in prior:
            if frame['evidence']['blockers'] or not frame['evidence']['exact_identity']:
                issues.append(WorkDrivers.issue('EXIT_UNPROVED', task_id=frame['run']['task_id'],
                    attempt_id=frame['run']['attempt_id']))
        for kind in ('manual_control', 'work_manual_control', 'work_flow'):
            for row in self.sources.all(self.db, kind):
                if row.get('item_id') == plan['item_id'] or row.get('task_id') in expected_tasks:
                    raise ValueError('原责任回执包含尚未独立核实的人工或流程责任。')
        held = driver['state'] in {'RESERVED', 'PAUSED'}
        stale = driver['item_revision'] != item['revision'] or driver['plan_id'] != plan['id'] or driver['plan_version'] != plan['version']
        supported = single_step_supported(plan)
        return {'record': driver, 'reference': self.driver_ref(driver), 'held': held, 'stale': stale,
            'issues': issues, 'can_acquire': not held and not issues,
            'can_resume': bool(held and driver['state'] == 'PAUSED' and not stale and not issues),
            'can_pause': bool(held and driver['state'] == 'RESERVED'),
            'can_release': held and not any(i['code'] == 'EXIT_UNPROVED' for i in issues),
            'no_dispatch': True, 'dispatch_supported': supported, 'single_step_supported': supported,
            'continuous_supported': plan['mode'] == 'fixed'}

    def finalize_commands(self):
        for pending in self.pending:
            event = pending['event']; driver = pending['driver']; request = pending['request']
            variants = [request]
            command_id = pending.get('authorization', {}).get('command_id')
            if pending['kind'] == 'work-item.cooperation-authorize':
                variants.append({k: v for k, v in request.items() if k != 'authorization'})
            found = [(raw, variant) for raw in self.db.execute('SELECT * FROM commands WHERE kind=?', (pending['kind'],))
                for variant in variants if raw['payload_hash'] == digest(variant) and
                (command_id is None or raw['id'] == command_id)]
            if len(found) != 1:
                raise ValueError('原责任或授权须有唯一准确原请求命令。')
            raw, actual_request = found[0]
            view = self._view(pending, raw, actual_request)
            if pending['kind'] == 'work-item.driver':
                response = {'driver': view, 'no_dispatch': True, 'automatically_resumed': False}
            else:
                # checked() had the same original readiness constraints as the
                # acquire/resume command; derive them from the frozen replay.
                if view['issues']:
                    raise ValueError('原初次合作授权不满足其实际历史责任条件。')
                response = {'authorization': pending['authorization'], 'no_dispatch': True}
            self.ledger.command(raw['id'], pending['kind'], actual_request, response,
                pending['owner'], not_before=max(driver['updated'], event['created']))
        self.pending.clear()
