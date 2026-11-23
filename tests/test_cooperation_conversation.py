"""Exercise the new cooperation entry point with an isolated native-engine fixture."""
import json
import time

from support import LocalFlowCase

from researchos.workdesk.store import uid
from researchos.workdesk.work_pdca import STAGES


OPEN = 'work-item.cooperation-open'
MESSAGE = 'work-item.cooperation-message'
CONFIRM = 'work-item.cooperation-confirm'
FOLLOWUP_PREVIEW = 'work-item.cooperation-followup-preview'
FOLLOWUP_CONFIRM = 'work-item.cooperation-followup-confirm'
REQUIREMENTS = 'WORKDESK_COOPERATION_REQUIREMENTS_V1'
EXPERT = 'WORKDESK_COOPERATION_EXPERT_V1'
PRIVATE_ADVICE = 'private expert proposal for the local fixture'
USER_GOAL = '整理一份新手可用的计算电磁学综述'
FOLLOWUP = '请聚焦仿真方法并附可核对的引用'
PUBLIC_REPLY = '我已把综述范围和验收条件整理到任务说明中。'


class CooperationConversationTests(LocalFlowCase):
    def setUp(self):
        super().setUp()
        connection = next(c for c in self.s.resources.catalog()['connections'] if c['provider'] == 'codex')
        self.members = {}
        for role in (*STAGES, 'coordinator', 'expert_a', 'expert_b'):
            member = self.command('resource.member-save', {
                'name': role, 'role': '合作验收样例', 'provider': 'codex', 'model': 'gpt-6-luna',
                'effort': 'low', 'tier': 'fast', 'connection_id': connection['id'],
            })['member']
            with self.s.store.read() as db:
                self.members[role] = self.s.resources.selection(db, member['id'])['reference']
        self.dispatches = []

    def payload(self, mode, **settings):
        selection = self.command('file.selection-save', {'project_id': self.project_id, 'files': ['note.txt']})['selection']
        return {
            'mode': mode, 'project_id': self.project_id, 'goal': USER_GOAL, 'rounds': 1,
            'stages': {stage: self.members[stage] for stage in STAGES},
            **({'coordinator': self.members['coordinator']} if mode != 'fixed' else {}),
            **({'discussion_experts': [self.members[role] for role in ('expert_a', 'expert_b')]}
               if mode == 'host_experts' else {}),
            'files': ['note.txt'], 'attachments': [], 'work_scene': 'documents',
            'file_selection': {key: selection[key] for key in ('id', 'revision')},
            **settings,
        }

    def engine(self, service, task, attempt, tools, stop, resume, note):
        self.calls.append(attempt['id'])
        spec = task['spec']
        goal = spec['goal']
        key = (spec.get('cooperative') or {}).get('unit_key')
        if EXPERT in goal:
            phase, answer = 'expert', PRIVATE_ADVICE
        elif REQUIREMENTS in goal:
            phase = 'discussion'
            answer = {'protocol': REQUIREMENTS, 'message': PUBLIC_REPLY, 'brief': {
                'goal': USER_GOAL, 'deliverables': '附引用的综述', 'constraints': '两千字',
                'acceptance': '引用可核对',
            }}
        elif key in {'leader_coordinate', 'host_coordinate'}:
            phase = 'coordination'
            context = json.loads(goal.split('输入快照：\n', 1)[1])['coordination_context']
            ready = [candidate for candidate in context['candidates'] if candidate['ready_after_proposal']]
            answer = {'protocol': context['protocol'], 'reason': '已核对本次上游样例'}
            if ready:
                answer.update(action='dispatch', unit_key=ready[0]['unit']['key'], retry_of=None)
            else:
                answer.update(action='deliver', unit_keys=context['finals'])
            if 'discussion' in context:
                answer['discussion'] = {
                    'considered': [item['source'] for item in context['discussion']['experts'] if item['source']],
                    'resolution': '讨论已收束', 'requires_human': False,
                }
        elif key and key.startswith('pdca_'):
            phase, answer = key, f'{key} 的已保存阶段结果'
        else:
            phase = 'preparation'
            protocol = next(name for name in ('WORKDESK_FIXED_PLAN_V1', 'WORKDESK_LEADER_PLAN_V1',
                                               'WORKDESK_HOST_EXPERTS_PLAN_V1') if name in goal)
            inputs = next(value for line in goal.splitlines() if line.startswith('{')
                          for value in (json.loads(line),) if 'pdca_units' in value)
            units = [{**unit, 'goal': '完成本阶段验收样例', 'acceptance': '返回准确结果',
                      'dependency_gate': 'result'} for unit in inputs['pdca_units']]
            answer = {'protocol': protocol, 'units': units, 'capability_requests': []}
        self.dispatches.append((phase, spec))
        thread, turn = uid(), uid()
        with service.store.write() as db:
            service.store.update_attempt(attempt['id'], db=db, session_id=thread, turn_id=turn)
            service.store.event(task['id'], attempt['id'], 'codex_session_account_bound',
                                {'session_id': thread, 'actual_thread_response_validated': True}, db)
            service.store.event(task['id'], attempt['id'], 'protocol_terminal',
                                {'thread_id': thread, 'turn_id': turn, 'status': 'completed'}, db)
        return {'state': 'COMPLETED', 'answer': json.dumps(answer, ensure_ascii=False) if isinstance(answer, dict) else answer,
                'known_tools_exited': True, 'owned_job': {'active_processes': 0},
                'execution': 'codex_native', 'remote_model_state': 'completed',
                'protocol': 'LOCAL_SYNTHETIC_ENGINE_NO_MODEL'}

    def settle_workers(self):
        for identity in list(self.s.workers):
            self.assertEqual(self.settled(identity)['state'], 'COMPLETED')

    def discussion(self, identity):
        for _ in range(8):
            self.settle_workers()
            self.s.cooperation_conversation.tick()
            view = self.s.cooperation_conversation.view(identity)
            if not view['pending']:
                return view
        self.fail('local cooperation discussion did not settle')

    def formal_preparation(self, identity):
        self.settle_workers()
        for provider in (self.s.fixed_preparation, self.s.leader_preparation, self.s.host_preparation):
            provider.tick()
            with self.s.store.read() as db:
                records = provider.records(db, identity)
                if records:
                    self.assertEqual(records[-1]['state'], 'APPLIED', records[-1].get('reason'))
                    return records[-1]
        self.fail('missing formal preparation')

    def complete_flow(self, identity, rounds=1):
        for _ in range(24 * rounds):
            self.s.work_flows.tick()
            self.settle_workers()
            view = self.s.cooperation_conversation.view(identity)
            if view['state'] in ('COMPLETED','PAUSED'):
                break
        self.assertEqual(view['state'],'COMPLETED',view.get('problem'))
        return view

    def test_followup_uses_same_chat_and_cumulative_grant(self):
        opened = self.command(OPEN,self.payload('fixed',rounds=2,max_work_rounds=30))
        identity = opened['work_item']['id']
        self.formal_preparation(identity)
        before = self.complete_flow(identity,2)
        self.assertEqual(before['authorization']['used'],9)
        prior = before['handoff']
        sent = len(self.calls)
        preview = self.command(FOLLOWUP_PREVIEW,{'id':identity,'revision':before['revision'],
            'text':'补充图表说明。再加两轮决策，再加十个回合'})['proposal']
        self.assertEqual((preview['rounds_before'],preview['rounds_after'],preview['limit_before'],preview['limit_after']),
                         (2,4,30,40))
        self.assertEqual(len(self.calls),sent)
        request = {'id':identity,'revision':before['revision'],'proposal_id':preview['id'],'confirm_full':False}
        command_id = uid()
        resumed = self.command(FOLLOWUP_CONFIRM,request,command_id)
        self.assertEqual(self.command(FOLLOWUP_CONFIRM,request,command_id),resumed)
        running = self.s.cooperation_conversation.view(identity)
        self.assertEqual(running['state'],'RUNNING')
        self.assertEqual(running['handoff_history'],[{**prior,'after_round':2}])
        self.assertEqual(running['stages'],before['stages'])
        self.formal_preparation(identity)
        after = self.complete_flow(identity,2)
        self.assertEqual([stage['round'] for stage in after['stages']],[1]*4+[2]*4+[3]*4+[4]*4)
        self.assertEqual((after['authorization']['used'],after['authorization']['limit']),(18,40))
        self.assertEqual(len(self.calls)-sent,9)

    def test_running_grant_and_queued_round_keep_current_plan(self):
        opened = self.command(OPEN,self.payload('fixed',max_work_rounds=7))
        identity = opened['work_item']['id']
        self.formal_preparation(identity)
        view = self.s.cooperation_conversation.view(identity)
        preview = self.command('work-item.cooperation-intervene',{'id':identity,'revision':view['revision'],
            'action':'adjust','note':'再加一轮 PDCA，再加十个工作回合'})['proposal']
        self.assertTrue(preview['queue'])
        receipt = self.command(FOLLOWUP_CONFIRM,{'id':identity,'revision':view['revision'],
            'proposal_id':preview['id'],'confirm_full':False})
        self.assertTrue(receipt['no_dispatch'])
        self.assertEqual(self.s.cooperation_conversation.view(identity)['authorization']['limit'],17)
        self.complete_flow(identity)
        self.s.cooperation_conversation.tick()
        self.assertEqual(self.s.cooperation_conversation.view(identity)['state'],'RUNNING')
        self.formal_preparation(identity)
        final = self.complete_flow(identity)
        self.assertEqual([stage['round'] for stage in final['stages']],[1]*4+[2]*4)
        self.assertEqual(final['authorization']['used'],10)

    def test_host_keeps_private_advice_out_of_chat_and_uses_distinct_planner(self):
        payload = self.payload('host_experts', dispatch_hours=36, max_work_rounds=20, access='full')
        command_id = uid()
        opened = self.command(OPEN, payload, command_id)
        self.assertEqual(self.command(OPEN, payload, command_id), opened)
        identity = opened['work_item']['id']
        view = self.discussion(identity)
        self.assertEqual(view['state'], 'DISCUSSING')
        self.assertEqual(view['stages'], [])
        self.assertTrue(view['can_send'])
        self.assertTrue(view['can_confirm'])
        self.assertEqual([phase for phase, _ in self.dispatches], ['expert', 'expert', 'discussion'])
        self.assertTrue(all(spec['plan_only'] and spec['access'] == 'workspace'
                            for _, spec in self.dispatches))
        self.assertEqual([(m['role'], m['text']) for m in view['messages']],
                         [('user', USER_GOAL), ('host', PUBLIC_REPLY)])
        self.assertNotIn(PRIVATE_ADVICE, json.dumps(view, ensure_ascii=False))
        with self.s.store.read() as db:
            self.assertIsNone(self.s.teamwork.current(db, identity))
            self.assertIsNone(self.s.work_flows.current(db, identity))

        version = view['revision']
        self.command(MESSAGE, {'id': identity, 'revision': version, 'text': FOLLOWUP})
        view = self.discussion(identity)
        self.assertEqual([(m['role'], m['text']) for m in view['messages']][-2:],
                         [('user', FOLLOWUP), ('host', PUBLIC_REPLY)])
        sent = len(self.calls)
        with self.assertRaises(ValueError):
            self.command(MESSAGE, {'id': identity, 'revision': version, 'text': 'stale message'})
        self.assertEqual(len(self.calls), sent)

        brief = {**view['brief'], 'deliverables': '已确认的仿真方法综述和文献清单'}
        before = time.time()
        self.command(CONFIRM, {'id': identity, 'revision': view['revision'], 'brief': brief})
        prepared = self.formal_preparation(identity)
        self.assertGreaterEqual(prepared['dispatch_before'], before + 36 * 3600)
        self.assertLessEqual(prepared['dispatch_before'], time.time() + 36 * 3600)
        self.assertEqual(prepared['max_attempts_total'], 20)
        self.assertEqual(prepared['preparations_used'], 1)
        self.assertEqual(prepared['planner_id'], self.members['plan']['member_id'])
        self.assertEqual(prepared['access'], 'full')
        preparation = next(spec for phase, spec in self.dispatches if phase == 'preparation')
        self.assertIn(brief['deliverables'], preparation['goal'])
        self.assertEqual(self.s.cooperation_conversation.view(identity)['brief'], brief)
        with self.s.store.read() as db:
            plan = self.s.teamwork.current(db, identity)
            self.assertEqual(plan['coordinator']['reference']['member_id'], self.members['coordinator']['member_id'])
        for _ in range(24):
            self.s.work_flows.tick()
            self.settle_workers()
            view = self.s.cooperation_conversation.view(identity)
            if view['state'] == 'COMPLETED':
                break
        self.assertEqual(view['state'], 'COMPLETED', view)
        self.assertEqual([(r['round'], r['stage']) for r in view['stages']], [(1, stage) for stage in STAGES])
        self.assertTrue(all(r['task_id'] and '已保存阶段结果' in r['text'] for r in view['stages']))
        self.assertEqual([spec['access'] for phase, spec in self.dispatches if phase.startswith('pdca_')],
                         ['full'] * len(STAGES))
        self.assertEqual([(m['role'], m['text']) for m in view['messages']][-2:],
                         [('user', FOLLOWUP), ('host', PUBLIC_REPLY)])
        self.assertNotIn(PRIVATE_ADVICE, json.dumps(view, ensure_ascii=False))

        preview = self.command(FOLLOWUP_PREVIEW,{'id':identity,'revision':view['revision'],
            'text':'请补充验证方法'})['proposal']
        scope = {'id':identity,'revision':view['revision'],'proposal_id':preview['id']}
        with self.assertRaisesRegex(ValueError,'完全访问权限'):
            self.command(FOLLOWUP_CONFIRM,{**scope,'confirm_full':False})
        self.command(FOLLOWUP_CONFIRM,{**scope,'confirm_full':True})
        self.formal_preparation(identity)
        successor = self.complete_flow(identity)
        self.assertEqual([stage['round'] for stage in successor['stages']],[1]*4+[2]*4)
        self.assertEqual((successor['authorization']['used'],successor['authorization']['limit']),(20,20))

    def test_fixed_plan_only_uses_default_deadline_and_skips_discussion(self):
        for settings in ({'dispatch_hours': 0}, {'max_work_rounds': 4}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.command(OPEN, self.payload('fixed', **settings))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.s.work_items.catalog(self.project_id)['work_items'], [])

        before = time.time()
        opened = self.command(OPEN, self.payload('fixed', max_work_rounds=7, plan_only=True))
        identity = opened['work_item']['id']
        prepared = self.formal_preparation(identity)
        view = self.s.cooperation_conversation.view(identity)
        self.assertEqual([phase for phase, _ in self.dispatches], ['preparation'])
        self.assertEqual(view['state'], 'PLAN_READY')
        self.assertFalse(view['can_send'])
        self.assertFalse(view['can_confirm'])
        self.assertEqual(view['stages'], [])
        self.assertGreaterEqual(prepared['dispatch_before'], before + 24 * 3600)
        self.assertLessEqual(prepared['dispatch_before'], time.time() + 24 * 3600)
        self.assertEqual(prepared['max_attempts_total'], 7)
        with self.s.store.read() as db:
            self.assertIsNone(self.s.work_flows.current(db, identity))
