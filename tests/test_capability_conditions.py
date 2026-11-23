"""Resume an installed user's capability wait with isolated official-work fixtures."""
import json
import time

import test_cooperation_conversation as fixture
from researchos.workdesk.service import Service
from researchos.workdesk.store import uid


REQUESTS = ['访问测试数据源', '持续运行环境', '实体设备验证']
CONDITIONS = [
    {'kind': 'external_resource', 'note': '先核对项目中已提供的数据源说明，访问权限需实际验证。'},
    {'kind': 'later_stage', 'note': '先完成本地开发，部署阶段再核对持续运行条件，保留原验收要求。'},
    {'kind': 'human_input', 'note': '交付页面后由用户使用实体设备验证，并提供结果。'},
]


class CapabilityConditionsTests(fixture.CooperationConversationTests):
    # Reuse the native-work fixture without running its unrelated cases again.
    for _base_test in tuple(name for name in dir(fixture.CooperationConversationTests)
                            if name.startswith('test_')):
        locals()[_base_test] = None
    del _base_test

    def engine(self, service, task, attempt, tools, stop, resume, note):
        result = super().engine(service, task, attempt, tools, stop, resume, note)
        if self.dispatches[-1][0] == 'preparation':
            inputs = next(json.loads(line) for line in task['spec']['goal'].splitlines()
                          if line.startswith('{') and '"pdca_units"' in line)
            answer = json.loads(result['answer'])
            if (not inputs.get('capability_responses') and not inputs.get('required_presets')
                    or getattr(self, 'still_missing', False)):
                answer['capability_requests'] = REQUESTS
            elif inputs.get('required_presets'):
                for unit, preset in zip(answer['units'], inputs['required_presets']):
                    unit['preset_id'] = preset
            result['answer'] = json.dumps(answer, ensure_ascii=False)
        return result

    def preparer(self, mode):
        return {'fixed': self.s.fixed_preparation, 'leader': self.s.leader_preparation,
                'host_experts': self.s.host_preparation}[mode]

    def preparation_row(self, identity, mode):
        with self.s.store.read() as db:
            return self.preparer(mode).records(db, identity)[-1]

    def waiting(self, mode='fixed'):
        opened = self.command(fixture.OPEN, self.payload(mode, max_work_rounds=30, access='full'))
        identity = opened['work_item']['id']
        if mode != 'fixed':
            view = self.discussion(identity)
            self.command(fixture.CONFIRM, {'id': identity, 'revision': view['revision'], 'brief': view['brief']})
        self.settle_workers()
        self.preparer(mode).tick()
        row = self.preparation_row(identity, mode)
        self.assertEqual(row['state'], 'NEEDS_CAPABILITY')
        # Existing 1.0 records have no bindings until the user handles a request.
        self.assertEqual(row.get('capability_bindings', {}), {})
        return identity, row

    def resolve(self, identity, mode, index, condition, command_id=None, preparation=None):
        row = self.preparation_row(identity, mode)
        return self.command('work-item.' + mode + '-capability-resolve', {
            'id': identity, 'preparation': preparation or self.preparer(mode).reference(row),
            'request_index': index, 'condition': condition,
        }, command_id)

    def resolve_all(self, identity, mode):
        for index, condition in enumerate(CONDITIONS):
            self.resolve(identity, mode, index, condition)

    def continue_request(self, identity, mode):
        with self.s.store.read() as db:
            item = self.s.work_items.item(db, identity)
        return {'id': identity, 'revision': item['revision'],
                'continue_from': self.preparer(mode).reference(self.preparation_row(identity, mode))}

    def test_all_three_modes_show_wait_and_replan_same_task_without_a_fake_preset(self):
        for mode in ('fixed', 'leader', 'host_experts'):
            with self.subTest(mode=mode):
                identity, before = self.waiting(mode)
                view = self.s.cooperation_conversation.view(identity)
                self.assertEqual(view['state'], 'PAUSED')
                self.assertEqual(view['capability_wait']['state'], 'NEEDS_CAPABILITY')
                self.assertEqual(view['capability_wait']['requests'], REQUESTS)
                self.assertEqual(view['capability_wait']['bindings'], {})
                self.assertEqual(view['capability_wait']['preparation'], self.preparer(mode).reference(before))
                self.assertEqual(view['capability_wait']['details_href'], '#work-items/item/' + identity)
                self.assertIn('3', view['problem'])
                old_result = self.s.store.attempt(before['attempt_id'])['result']
                before_sent = len(self.calls)
                self.resolve_all(identity, mode)
                self.assertEqual(len(self.calls), before_sent)
                request = self.continue_request(identity, mode)
                command_id = uid()
                launch = self.command('work-item.' + mode + '-prepare', request, command_id)
                self.assertEqual(self.command('work-item.' + mode + '-prepare', request, command_id), launch)
                self.formal_preparation(identity)
                after = self.preparation_row(identity, mode)
                self.assertEqual(len(self.calls), before_sent + 1)
                self.assertEqual(after['state'], 'APPLIED')
                self.assertEqual(after['preparations_used'], 2)
                self.assertEqual(after['requirements'], before['requirements'])
                # The completed preparation legitimately adds a timing sample;
                # member identities, models and permission-bearing fields stay frozen.
                member_config = lambda members: {
                    identity: {key: value for key, value in member.items() if key != 'planning_context'}
                    for identity, member in members.items()}
                self.assertEqual(member_config(after['members']), member_config(before['members']))
                self.assertEqual(after['access'], before['access'])
                self.assertEqual(after['max_attempts_total'], before['max_attempts_total'])
                self.assertEqual(after['dispatch_before'], before['dispatch_before'])
                self.assertEqual(after['required_presets'], [])
                self.assertEqual([entry['request'] for entry in after['capability_responses']], REQUESTS)
                self.assertEqual([{key: entry['condition'][key] for key in ('kind', 'note')}
                                  for entry in after['capability_responses']], CONDITIONS)
                self.assertEqual(self.s.store.attempt(before['attempt_id'])['result'], old_result)
                task = self.s.store.task(launch['task_id'])
                inputs = next(json.loads(line) for line in task['spec']['goal'].splitlines()
                              if line.startswith('{') and '"pdca_units"' in line)
                self.assertEqual(inputs['capability_responses'], after['capability_responses'])
                self.assertEqual(inputs['presets'], [])
                self.assertIsNone(self.s.cooperation_conversation.view(identity)['capability_wait'])
                completed = self.complete_flow(identity)
                self.assertEqual(completed['id'], identity)
                self.assertEqual(len(completed['stages']), 4)
                self.assertEqual(completed['authorization']['deadline'], before['dispatch_before'])
                self.assertEqual(completed['authorization']['limit'], before['max_attempts_total'])
                with self.s.store.read() as db:
                    authorization = self.s.work_units.authorization(
                        db, self.s.work_drivers.current(db, identity))
                self.assertEqual(authorization['max_attempts_total'], before['max_attempts_total'] - 2)

    def test_invalid_stale_and_incomplete_responses_cannot_dispatch(self):
        identity, before = self.waiting()
        sent = len(self.calls)
        for condition in ({'kind': 'external_resource', 'note': ''},
                          {'kind': 'external_resource', 'note': 'x' * 4001},
                          {'kind': 'verified', 'note': 'unsupported verification claim'},
                          {'kind': 'later_stage', 'note': '保留验收', 'verified': True}):
            with self.subTest(condition_kind=condition['kind'], keys=tuple(condition)):
                with self.assertRaises(ValueError):
                    self.resolve(identity, 'fixed', 0, condition)
        original_ref = self.s.fixed_preparation.reference(before)
        command_id = uid()
        receipt = self.resolve(identity, 'fixed', 0, CONDITIONS[0], command_id)
        self.assertEqual(self.resolve(identity, 'fixed', 0, CONDITIONS[0], command_id,
                                      preparation=original_ref), receipt)
        with self.assertRaises(ValueError):
            self.resolve(identity, 'fixed', 1, CONDITIONS[1], preparation=original_ref)
        with self.assertRaises(ValueError):
            self.command('work-item.fixed-prepare', self.continue_request(identity, 'fixed'))
        current = self.preparation_row(identity, 'fixed')
        self.assertEqual(current['state'], 'NEEDS_CAPABILITY')
        self.assertEqual(list(current['capability_bindings']), ['0'])
        self.assertEqual(len(self.calls), sent)

    def test_expired_replanning_keeps_old_wait_and_original_authorization(self):
        identity, before = self.waiting()
        self.resolve_all(identity, 'fixed')
        sent = len(self.calls)
        with self.s.store.write() as db:
            row = self.s.fixed_preparation.records(db, identity)[-1]
            row['dispatch_before'] = time.time() - 1
            self.s.collaboration.put(db, self.s.fixed_preparation.kind, row)
        request = self.continue_request(identity, 'fixed')
        with self.assertRaises(ValueError):
            self.command('work-item.fixed-prepare', request)
        after = self.preparation_row(identity, 'fixed')
        self.assertEqual(after['state'], 'NEEDS_CAPABILITY')
        self.assertEqual(after['revision'], request['continue_from']['revision'])
        self.assertEqual(after['max_attempts_total'], before['max_attempts_total'])
        self.assertEqual(len(self.calls), sent)
        with self.s.store.read() as db:
            self.assertTrue(self.s.work_drivers.held(db, identity))
            self.assertIsNone(self.s.teamwork.current(db, identity))

    def test_existing_wait_survives_service_restart_and_accepts_explicit_replanning(self):
        identity, before = self.waiting('leader')
        old_instance = self.s.instance
        old_result = self.s.store.attempt(before['attempt_id'])['result']
        self.s.close()
        self.s = Service(self.loc, engine=self.engine)
        self.s.collaboration.close()
        self.s.profiles.validate = lambda provider, *_: next(
            row for row in self.s.profiles.list() if row['id'] == provider)
        self.assertNotEqual(self.s.instance, old_instance)
        self.assertEqual(self.s.cooperation_conversation.view(identity)['capability_wait']['requests'], REQUESTS)
        with self.s.store.read() as db:
            self.assertEqual(self.s.work_drivers.current(db, identity)['state'], 'PAUSED')
        self.resolve_all(identity, 'leader')
        sent = len(self.calls)
        launch = self.command('work-item.leader-prepare', self.continue_request(identity, 'leader'))
        self.formal_preparation(identity)
        self.assertEqual(len(self.calls), sent + 1)
        after = self.preparation_row(identity, 'leader')
        self.assertEqual(after['instance'], self.s.instance)
        self.assertEqual(after['dispatch_before'], before['dispatch_before'])
        self.assertEqual(self.s.store.attempt(before['attempt_id'])['result'], old_result)
        self.assertNotEqual(launch['launch_attempt'], before['attempt_id'])
        self.assertIsNone(self.s.cooperation_conversation.view(identity)['capability_wait'])

    def test_user_explanations_do_not_override_a_repeated_missing_condition(self):
        identity, before = self.waiting()
        self.resolve_all(identity, 'fixed')
        self.still_missing = True
        launch = self.command('work-item.fixed-prepare', self.continue_request(identity, 'fixed'))
        self.settle_workers()
        self.s.fixed_preparation.tick()
        after = self.preparation_row(identity, 'fixed')
        self.assertEqual(after['state'], 'NEEDS_CAPABILITY')
        self.assertEqual(after['preparations_used'], 2)
        self.assertEqual(len(after['capability_responses']), 3)
        self.assertEqual(after.get('capability_bindings', {}), {})
        self.assertEqual(self.s.cooperation_conversation.view(identity)['capability_wait']['requests'], REQUESTS)
        with self.s.store.read() as db:
            self.assertIsNone(self.s.teamwork.current(db, identity))
            self.assertIsNone(self.s.work_flows.current(db, identity))
        self.assertEqual(self.s.store.attempt(launch['launch_attempt'])['state'], 'COMPLETED')
        self.assertEqual(self.s.store.attempt(before['attempt_id'])['state'], 'COMPLETED')

    def test_legacy_tool_preset_and_conditions_can_be_mixed_and_withdrawn(self):
        identity, before = self.waiting()
        skill = self.project / 'local-fixture-skill'
        skill.mkdir()
        (skill / 'SKILL.md').write_text('---\nname: local-fixture-skill\ndescription: Local fixture\n---\nRead the note.\n',
                                       encoding='utf-8')
        capability = self.command('capability.import', {
            'path': str(skill), 'kind': 'skill', 'source': 'isolated regression fixture'})['capability']
        self.command('capability.enable', {'id': capability['id'], 'revision': capability['revision'], 'enabled': True})
        preset = self.command('scene.save', {'scene': 'research', 'name': '旧式工具预设',
            'capabilities': [capability['id']], 'extensions': []})['preset']
        reference = {key: preset[key] for key in ('id', 'revision')}
        self.command('work-item.fixed-capability-resolve', {
            'id': identity, 'preparation': self.s.fixed_preparation.reference(before),
            'request_index': 0, 'preset': reference})
        for index in (1, 2):
            self.resolve(identity, 'fixed', index, CONDITIONS[index])
        row = self.preparation_row(identity, 'fixed')
        self.assertIn('reference', row['capability_bindings']['0'])
        self.assertNotIn('reference', row['capability_bindings']['1'])
        self.command('work-item.fixed-capability-resolve', {
            'id': identity, 'preparation': self.s.fixed_preparation.reference(row),
            'request_index': 1, 'preset': None})
        with self.assertRaises(ValueError):
            self.command('work-item.fixed-prepare', self.continue_request(identity, 'fixed'))
        self.resolve(identity, 'fixed', 1, CONDITIONS[1])
        launch = self.command('work-item.fixed-prepare', self.continue_request(identity, 'fixed'))
        self.formal_preparation(identity)
        after = self.preparation_row(identity, 'fixed')
        self.assertEqual(after['state'], 'APPLIED')
        self.assertEqual(after['required_presets'], [reference['id']])
        self.assertEqual(len(after['capability_responses']), 2)
        inputs = next(json.loads(line) for line in self.s.store.task(launch['task_id'])['spec']['goal'].splitlines()
                      if line.startswith('{') and '"pdca_units"' in line)
        self.assertEqual(inputs['required_presets'], [reference['id']])
        self.assertEqual(inputs['presets'][0]['id'], reference['id'])


if __name__ == '__main__':
    import unittest
    unittest.main()
