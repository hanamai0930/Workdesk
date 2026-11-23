import json
from pathlib import Path
import time
from unittest.mock import patch

from support import LocalFlowCase
from researchos.workdesk.service import Service
from researchos.workdesk.store import uid
from researchos.workdesk.work_fixed_preparation import START, SETTLE, DISCARD, PROTOCOL, normalize_dependency_gate


def fixture_plan(task):
    inputs = json.loads(task['spec']['goal'].splitlines()[-1])
    member = inputs['members'][0]['id']
    preset = inputs['presets'][0]['id'] if inputs['presets'] else None
    return {'protocol': PROTOCOL, 'units': [
        {'key': 'write', 'title': '生成样例', 'role': 'execute', 'goal': '写样例',
         'acceptance': '文件含样例文本', 'member_id': member, 'preset_id': preset,
         'write_paths': ['draft.txt'], 'outputs': ['draft.txt'],
         'checks': [{'kind': 'utf8_contains', 'file': 'draft.txt', 'text': 'fixture content'}]},
        {'key': 'deliver', 'title': '交付样例', 'role': 'integrate', 'goal': '交付已检查的样例',
         'acceptance': '交付文件含样例文本', 'member_id': member, 'depends': ['write'],
         'dependency_gate': 'checked', 'write_paths': ['final.txt'], 'outputs': ['final.txt']},
    ]}


def fixture_result(service, task, attempt):
    key = task['spec']['cooperative'].get('unit_key')
    if key == 'write':
        (Path(attempt['workspace']) / 'draft.txt').write_text('fixture content', encoding='utf-8')
    elif key == 'deliver':
        root = Path(attempt['workspace'])
        assert 'draft.txt' in task['spec']['materials']
        (root / 'final.txt').write_text((root / 'draft.txt').read_text(encoding='utf-8'), encoding='utf-8')
    result = {'state': 'COMPLETED',
              'answer': json.dumps(fixture_plan(task)) if key is None else 'fixture content',
              'known_tools_exited': True, 'owned_job': {'active_processes': 0},
              'protocol': 'LOCAL_SYNTHETIC_ENGINE_NO_MODEL'}
    if key is None:
        thread, turn = uid(), uid()
        with service.store.write() as db:
            service.store.update_attempt(attempt['id'], db=db, session_id=thread, turn_id=turn)
            service.store.event(task['id'], attempt['id'], 'codex_session_account_bound',
                                {'session_id': thread, 'actual_thread_response_validated': True}, db)
            service.store.event(task['id'], attempt['id'], 'protocol_terminal',
                                {'thread_id': thread, 'turn_id': turn, 'status': 'completed'}, db)
        result.update(execution='codex_native', remote_model_state='completed')
    return result


class FixedPreparationTests(LocalFlowCase):
    def setUp(self):
        super().setUp()
        connection = next(c for c in self.s.resources.catalog()['connections'] if c['provider'] == 'codex')
        member = self.command('resource.member-save', {
            'name': '本地准备与执行成员', 'role': '执行', 'provider': 'codex', 'model': 'gpt-6-luna',
            'effort': 'low', 'tier': 'fast', 'connection_id': connection['id'],
        })['member']
        with self.s.store.read() as db:
            self.planner = self.s.resources.selection(db, member['id'])['reference']
        self.identity = self.command('work-item.create', {
            'title': '中性自然语言样例', 'goal': '先整理一段样例，再把检查过的内容保存为交付文件',
            'project_id': self.project_id,
        })['work_item']['id']
        self.answer = None

    def engine(self, service, task, attempt, tools, stop, resume, note):
        self.calls.append(attempt['id'])
        result = fixture_result(service, task, attempt)
        if self.answer is not None and task['spec']['cooperative'].get('preparation_id'):
            result.update(self.answer)
        return result

    def item(self):
        return self.s.work_items.catalog(identity=self.identity)['work_items'][0]

    def start_payload(self, presets=None):
        return {'id': self.identity, 'revision': self.item()['revision'], 'planner': self.planner,
                'presets': presets or [], 'max_attempts_total': 3, 'dispatch_before': time.time() + 60}

    def start_and_wait(self):
        launch = self.command(START, self.start_payload())
        self.settled(launch['launch_attempt'])
        return launch

    def test_approved_preset_and_plan_reach_delivery_under_same_task_and_budget(self):
        skill_dir = self.project / 'fixture-skill'
        skill_dir.mkdir()
        (skill_dir / 'SKILL.md').write_text(
            '---\nname: fixture-skill\ndescription: Local validation fixture\n---\nRead the local note.\n', encoding='utf-8')
        capability = self.command('capability.import', {'path': str(skill_dir), 'kind': 'skill', 'source': 'local test'})['capability']
        self.command('capability.enable', {'id': capability['id'], 'revision': capability['revision'], 'enabled': True})
        preset = self.command('scene.save', {'scene': 'research', 'name': '样例能力', 'prompt': '完成样例',
            'output': '样例文件', 'capabilities': [capability['id']], 'extensions': []})['preset']
        payload = self.start_payload([{key: preset[key] for key in ('id', 'revision')}])
        identity = uid()
        launch = self.command(START, payload, identity)
        self.settled(launch['launch_attempt'])
        self.assertEqual(self.command(START, payload, identity), launch)
        pending = self.item()
        self.assertTrue(pending['driver']['held'])
        with self.assertRaisesRegex(ValueError, '持有'):
            self.command('work-item.prepare', {'id': self.identity, 'revision': pending['revision']})
        self.s.fixed_preparation.tick()
        applied = self.item()
        self.assertEqual(applied['fixed_preparation']['record']['state'], 'APPLIED')
        self.assertEqual(applied['fixed_preparation']['record']['access'], 'workspace')
        self.assertNotIn('access', applied['teamwork']['plan'])
        proof = applied['fixed_preparation']['record']['evidence']
        self.assertNotEqual(proof['session_id'], applied['dispatches'][0]['session_id'])
        self.assertLess(proof['binding_event_seq'], proof['terminal_event_seq'])
        self.assertEqual(applied['cooperation']['authorization']['max_attempts_total'], 2)
        self.assertEqual(applied['cooperation']['authorization']['dispatch_before'], payload['dispatch_before'])
        for _ in range(6):
            self.s.work_flows.tick()
            for attempt_id in list(self.s.workers):
                self.settled(attempt_id)
        item = self.item()
        self.assertEqual(item['id'], self.identity)
        self.assertEqual(len(item['dispatches']), 3)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(item['cooperation']['flow']['record']['state'], 'RESULTS_AVAILABLE')
        self.assertEqual((self.project / 'final.txt').read_text(encoding='utf-8'), 'fixture content')
        with self.s.store.read() as db:
            self.assertEqual([self.s.store.task(run['task_id'], db)['spec']['access']
                              for run in item['dispatches']], ['workspace'] * 3)
            load = self.s.collaboration.get(db, 'scene_load', 'scene-load:' + self.calls[1])
            self.assertEqual(load['preset']['id'], preset['id'])
            self.assertIsNone(db.execute("SELECT 1 FROM collaboration WHERE id=?", ('scene-load:' + self.calls[2],)).fetchone())

    def test_auto_review_requires_support_from_every_permitted_member(self):
        lookup = self.s.teamwork.member
        planning_context = self.s.resources.planning_context
        reference = {'member_id': 'fixture-deepseek'}

        def member(db, ref):
            if ref == reference:
                return {'reference': reference, 'provider': 'deepseek'}
            return lookup(db, ref)

        with patch.object(self.s.teamwork, 'member', side_effect=member), patch.object(
                self.s.resources, 'planning_context',
                side_effect=lambda db, identity: None if identity == reference['member_id'] else planning_context(db, identity)):
            with self.assertRaisesRegex(ValueError, 'deepseek 不支持所选固定流程权限'):
                self.command(START, {**self.start_payload(), 'members': [reference], 'access': 'auto'})
        self.assertFalse(self.calls)
        self.assertIsNone(self.item()['fixed_preparation']['record'])

    def test_failed_transfer_rolls_back_plan_and_preserves_preparation_responsibility(self):
        self.start_and_wait()
        with patch.object(self.s.work_flows, 'change', side_effect=ValueError('fixture rejected flow start')):
            self.s.fixed_preparation.tick()
        item = self.item()
        self.assertEqual(item['fixed_preparation']['record']['state'], 'BLOCKED')
        self.assertTrue(item['driver']['held'])
        self.assertIsNone(item['teamwork'])
        self.assertIsNone(item['cooperation']['authorization'])
        self.assertEqual(len(self.calls), 1)

    def test_cross_unit_check_blocks_plan_and_prompt_explains_file_ownership(self):
        valid_plan = fixture_plan

        def bad_plan(task):
            plan = valid_plan(task)
            plan['units'][1]['checks'] = [{'kind': 'nonempty', 'file': 'draft.txt'}]
            return plan

        with patch(__name__ + '.fixture_plan', side_effect=bad_plan):
            launch = self.start_and_wait()
        task = self.s.store.task(launch['task_id'])
        self.assertIn('每个 checks 项的 file 必须逐字匹配本单元 outputs 中的一项', task['spec']['goal'])
        self.assertIn('在生产该文件的前置单元声明 checks', task['spec']['goal'])
        self.s.fixed_preparation.tick()
        item = self.item()
        self.assertEqual(item['fixed_preparation']['record']['state'], 'BLOCKED')
        self.assertIn('检查须引用本单元明确产物', item['fixed_preparation']['record']['reason'])
        self.assertIsNone(item['teamwork'])
        self.assertEqual(len(self.calls), 1)

    def test_native_acceptance_list_is_preserved_as_ordered_text(self):
        valid_plan = fixture_plan
        def list_plan(task):
            plan = valid_plan(task)
            plan['units'][0]['acceptance'] = ['文件包含样例', '保留原文和顺序']
            return plan
        with patch(__name__ + '.fixture_plan', side_effect=list_plan):
            self.start_and_wait()
        self.s.fixed_preparation.tick()
        item = self.item()
        self.assertEqual(item['fixed_preparation']['record']['state'], 'APPLIED')
        self.assertEqual(item['teamwork']['plan']['units'][0]['acceptance'], '文件包含样例\n保留原文和顺序')
        self.assertEqual(item['fixed_preparation']['record']['evidence']['plan_normalizations'],
                         [{'unit_key':'write', 'field':'acceptance', 'operation':'join_criteria_lines', 'items':2}])

    def test_native_field_normalization_keeps_path_and_text_boundaries(self):
        from researchos.workdesk.work_fixed_preparation import normalize_plan_fields
        from researchos.workdesk.teamwork import write_paths
        value = {'write_paths':['research/'], 'acceptance':['先读取资料', '再交付文件']}
        normalize_plan_fields(value)
        self.assertEqual(value, {'write_paths':['research'], 'acceptance':'先读取资料\n再交付文件'})
        self.assertEqual(write_paths(value['write_paths'], self.project), ['research'])
        for criteria in ([], [None], [''], ['x'*4001], ['x'*3000, 'y'*2000]):
            with self.assertRaises(ValueError):normalize_plan_fields({'acceptance':criteria})
        for path in ('../', './', 'a//', '/outside/', 'a/../', 'a/.//'):
            value = {'write_paths':[path]}
            normalize_plan_fields(value)
            with self.assertRaises(ValueError):write_paths(value['write_paths'], self.project)

    def test_structured_dependency_gate_requires_exact_predecessor_outputs(self):
        predecessors = [{'key': 'draft', 'outputs': ['draft.txt']},
                        {'key': 'sources', 'outputs': ['sources.txt']}]
        reviewer = {'depends': ['draft', 'sources'],
                    'dependency_gate': {'type': 'checked', 'files': ['sources.txt', 'draft.txt']}}
        normalize_dependency_gate(reviewer, predecessors)
        self.assertEqual(reviewer['dependency_gate'], 'checked')

        for files in (['draft.txt'], ['draft.txt', 'sources.txt', 'other.txt'], ['draft.txt', 'draft.txt']):
            with self.subTest(files=files):
                gate = {'type': 'checked', 'files': files}
                candidate = {**reviewer, 'dependency_gate': gate}
                with self.assertRaisesRegex(ValueError, '依赖条件对象'):
                    normalize_dependency_gate(candidate, predecessors)
                self.assertEqual(candidate['dependency_gate'], gate)

    def test_codex_session_change_requires_matching_original_terminal(self):
        launch = self.start_and_wait()
        with self.s.store.write() as db:
            self.s.store.update_attempt(launch['launch_attempt'], db=db, turn_id='other-turn')
        self.s.fixed_preparation.tick()
        item = self.item()
        self.assertEqual(item['fixed_preparation']['record']['state'], 'BLOCKED')
        self.assertIn('官方线程与终态', item['fixed_preparation']['record']['reason'])
        self.assertIsNone(item['teamwork'])
        self.assertEqual(len(self.calls), 1)

    def test_missing_capability_is_recorded_and_release_preserves_used_attempt(self):
        self.answer = {'answer': json.dumps({'protocol': PROTOCOL, 'units': [],
                                             'capability_requests': ['读取样例图所需能力']})}
        self.start_and_wait()
        self.s.fixed_preparation.tick()
        row = self.item()['fixed_preparation']['record']
        self.assertEqual(row['state'], 'NEEDS_CAPABILITY')
        self.assertEqual(row['requests'], ['读取样例图所需能力'])
        self.assertEqual(len(self.calls), 1)
        self.command(DISCARD, {'id': self.identity, 'preparation': row['reference']})
        self.assertEqual(self.item()['fixed_preparation']['attempts_used'], 1)
        self.assertFalse(self.item()['driver']['held'])
        with self.assertRaisesRegex(ValueError, '累计尝试上限'):
            self.command(START, {**self.start_payload(), 'max_attempts_total': 2})

    def test_structured_capability_request_keeps_purpose_and_tool_text(self):
        self.answer = {'answer': json.dumps({
            'protocol': PROTOCOL, 'units': [],
            'capability_requests': [
                '读取样例图所需能力',
                {'capability': '来源核对', 'purpose': '核对样例来源', 'tools': ['只读资料目录']},
            ],
        })}
        self.start_and_wait()
        self.s.fixed_preparation.tick()
        item = self.item()
        row = item['fixed_preparation']['record']
        self.assertEqual(row['state'], 'NEEDS_CAPABILITY')
        self.assertEqual(row['requests'], ['读取样例图所需能力', '来源核对 · 核对样例来源 · 只读资料目录'])
        self.assertIsNone(item['teamwork'])
        self.assertEqual(len(self.calls), 1)

    def test_restart_waits_for_explicit_check_of_original_completed_result(self):
        self.start_and_wait()
        self.s.close()
        self.s = Service(self.loc, engine=self.engine)
        self.s.collaboration.close()
        self.s.profiles.validate = lambda provider, *_: next(p for p in self.s.profiles.list() if p['id'] == provider)
        self.s.fixed_preparation.tick()
        row = self.item()['fixed_preparation']['record']
        self.assertEqual(row['state'], 'PAUSED_AFTER_RESTART')
        self.assertEqual(len(self.calls), 1)
        payload = {'id': self.identity, 'preparation': row['reference']}
        with self.assertRaisesRegex(ValueError, '暂停'):
            self.command(SETTLE, payload)
        receipt = self.command(SETTLE, {**payload, 'recheck': True})
        self.assertEqual(receipt['state'], 'APPLIED')
        self.assertEqual(len(self.calls), 1)

    def test_unknown_and_changed_requirements_cannot_start_execution(self):
        self.answer = {'state': 'SEND_UNKNOWN', 'remote_model_state': 'UNKNOWN'}
        self.start_and_wait()
        self.s.fixed_preparation.tick()
        row = self.item()['fixed_preparation']['record']
        self.assertEqual(row['state'], 'NEEDS_RECONCILIATION')
        with self.assertRaisesRegex(ValueError, '终态'):
            self.command(DISCARD, {'id': self.identity, 'preparation': row['reference']})
        self.identity = self.command('work-item.create', {'title': '变更检查', 'goal': '原目标',
            'project_id': self.project_id})['work_item']['id']
        self.answer = None
        self.start_and_wait()
        item = self.item()
        self.command('work-item.update', {'id': self.identity, 'revision': item['revision'], 'changes': {'goal': '新目标'}})
        self.s.fixed_preparation.tick()
        self.assertEqual(self.item()['fixed_preparation']['record']['state'], 'BLOCKED')
        self.assertEqual(len(self.calls), 2)
