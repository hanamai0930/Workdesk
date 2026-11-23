import io
import json
import threading
import time
from unittest.mock import patch

from support import LocalFlowCase
from researchos.workdesk.chat_api import deepseek_stream, ENDPOINT
from researchos.workdesk.service import Service
from researchos.workdesk.store import uid


class Secret:
    def reveal_for_provider(self):return 'fixture-only-never-a-real-key'


class ChatAPIFlows(LocalFlowCase):
    def setUp(self):
        super().setUp();self.api=self.s.ui_features.chats.api;self.bodies=[]
        self.s.profiles.credentials=lambda:{'deepseek':Secret()}
        def stream(secret,body,stop,opened):
            self.bodies.append(body)
            yield {'created':int(time.time()),'choices':[{'delta':{'content':'local '},'finish_reason':None}]}
            yield {'choices':[{'delta':{'content':'reply'},'finish_reason':'stop'}], 'usage':{'prompt_tokens':7,'completion_tokens':2,'total_tokens':9}}
            yield {'done':True}
        self.api.transport=stream

    def create(self):return self.command('chat.create',{'provider':'deepseek','route':'api'})['chat']
    def row(self,identity):return next(c for c in self.s.ui_features.catalog()['chats'] if c['id']==identity)
    def send(self,row,content='local question',identity=None):
        return self.command('chat.api-send',{'id':row['id'],'revision':row['revision'],'content':content,'model':'deepseek-flash','thinking':False,'billing':'official_api'},identity)
    def wait(self,identity):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            row=self.row(identity)
            if not row.get('pending'):return row
            time.sleep(.01)
        self.fail('API fixture did not settle')

    def test_direct_two_turn_context_stream_and_receipt_idempotence(self):
        c=self.create();command=uid();self.send(c,identity=command);self.send(c,identity=command);c=self.wait(c['id'])
        self.assertEqual(c['messages'][-1]['content'],'local reply');self.assertEqual(len(self.bodies),1)
        self.send(c,'follow up');c=self.wait(c['id'])
        self.assertEqual([m['role'] for m in self.bodies[1]['messages']],['system','user','assistant','user'])
        self.assertEqual(c['messages'][-1]['usage']['total_tokens'],9)
        with self.s.store.read() as db:
            turn=self.s.collaboration.get(db,'ui_chat_turn',c['messages'][-1]['turn_id'])
            self.assertAlmostEqual(turn['provider_requested_at'], time.time(), delta=10)
        self.assertFalse(self.bodies[0]['thinking']['type']=='enabled');self.assertNotIn('tools',self.bodies[0])
        self.assertEqual(self.calls,[]);self.assertEqual(self.s.catalog()['tasks'],[])
        with self.assertRaises(ValueError):self.command('chat.bind-web',{'id':c['id'],'url':'https://chat.deepseek.com/a/test'})

    def test_auth_and_model_route_fail_before_dispatch(self):
        for provider in ('codex','qwen'):
            with self.assertRaises(ValueError):self.command('chat.create',{'provider':provider,'route':'api'})
        c=self.create();self.s.profiles.credentials=lambda:{}
        with self.assertRaises(ValueError):self.send(c)
        self.assertEqual(self.row(c['id'])['messages'],[]);self.assertEqual(self.bodies,[])

    def test_qwen_uses_its_own_transport_and_records_usage(self):
        class QwenSecret(Secret):
            billing_kind='general_api'

        qwen=QwenSecret()
        self.s.profiles.credentials=lambda:{'deepseek':Secret(),'qwen':qwen}
        def stream(secret,body,stop,opened):
            self.assertIs(secret,qwen)
            self.assertIs(body['enable_thinking'],True)
            self.assertNotIn('thinking',body)
            yield {'choices':[{'delta':{'content':'Qwen fixture'},'finish_reason':'stop'}],
                   'usage':{'prompt_tokens':8,'completion_tokens':4,'prompt_tokens_details':{'cached_tokens':3}}}
            yield {'done':True}
        self.api.qwen_transport=stream
        row=self.command('chat.create',{'provider':'qwen','route':'api'})['chat']
        self.command('chat.api-send',{'id':row['id'],'revision':row['revision'],'content':'Qwen question',
                                     'model':'qwen3.8-flash','thinking':True,'billing':'official_api'})
        self.assertEqual(self.wait(row['id'])['messages'][-1]['content'],'Qwen fixture')
        from researchos.workdesk.settings_usage import overview
        quota=next(q for q in overview(self.s)['quotas'] if q['provider']=='qwen')
        self.assertEqual(quota['usage'],{'input_tokens':8,'output_tokens':4})
        from researchos.workdesk.usage_report import report
        totals=report(self.s,'qwen',7)['totals']
        self.assertEqual([totals[key] for key in ('calls','cache_hit','cache_miss','output','tokens')],[1,3,5,4,12])
        row=self.row(row['id'])
        self.command('chat.delete',{'confirm':True,'chats':[{'id':row['id'],'revision':row['revision']}]})
        self.assertEqual(report(self.s,'qwen',7)['totals'],totals)
        self.assertEqual(self.bodies,[])

    def test_exact_stop_partial_history_reload_guard_and_record_edit(self):
        entered=threading.Event()
        def slow(secret,body,stop,opened):
            self.bodies.append(body);yield {'choices':[{'delta':{'content':'partial'}}]};entered.set();stop.wait(3)
        self.api.transport=slow;c=self.create();r=self.send(c);self.assertTrue(entered.wait(2))
        c=self.row(c['id'])
        with self.assertRaises(ValueError):self.send(c)
        with self.assertRaises(ValueError):self.command('chat.api-stop',{'id':c['id'],'turn_id':'wrong-request'})
        with self.assertRaises(ValueError):self.command('service.reload',{})
        with self.assertRaises(ValueError):self.command('storage.credentials.select',{'api_env':None})
        self.command('chat.record',{'id':c['id'],'title':'renamed during reply'})
        self.command('chat.api-stop',{'id':c['id'],'turn_id':r['launch_chat_turn']});c=self.wait(c['id'])
        self.assertEqual(c['messages'][-1]['state'],'INTERRUPTED');self.assertEqual(c['messages'][-1]['content'],'partial')
        self.assertEqual(c['title'],'renamed during reply');self.assertEqual(len(self.bodies),1)

    def test_dropped_stream_never_retries_or_enters_completed_context(self):
        def dropped(secret,body,stop,opened):
            self.bodies.append(body);yield {'choices':[{'delta':{'content':'unfinished'}}]}
        normal=self.api.transport;self.api.transport=dropped;c=self.create();self.send(c);c=self.wait(c['id'])
        self.assertEqual(c['messages'][-1]['state'],'SEND_UNKNOWN');self.assertEqual(len(self.bodies),1)
        self.api.transport=normal;self.send(c,'new explicit question');self.wait(c['id'])
        self.assertEqual(len(self.bodies[1]['messages']),2)

    def test_restart_preserves_partial_and_marks_unknown_without_replay(self):
        c=self.create();self.send(c);c=self.wait(c['id']);identity=c['messages'][-1]['turn_id']
        with self.s.store.write() as db:
            t=self.s.collaboration.get(db,'ui_chat_turn',identity);t.update(state='RUNNING',text='saved partial')
            c['pending']=identity;self.s.collaboration.put(db,'ui_chat',c);self.api._save(db,t)
        self.s.close();self.s=Service(self.loc,engine=self.engine);self.s.collaboration.close();self.s.profiles.credentials=lambda:{}
        c=self.row(c['id']);self.assertIsNone(c['pending']);self.assertEqual(c['messages'][-1]['state'],'ORPHANED_UNKNOWN')
        self.assertEqual(c['messages'][-1]['content'],'saved partial');self.assertEqual(len(self.bodies),1)

    def test_wire_targets_only_official_endpoint_and_parses_sse(self):
        wire=io.BytesIO(('data: '+json.dumps({'choices':[{'delta':{'content':'local'}}]})+'\n\ndata: [DONE]\n\n').encode())
        class Opener:
            def open(inner,request,timeout):
                self.assertEqual(request.full_url,ENDPOINT);self.assertEqual(request.get_method(),'POST')
                self.assertEqual(request.get_header('Authorization'),'Bearer fixture-only-never-a-real-key')
                return wire
        with patch('researchos.workdesk.chat_api.build_opener',return_value=Opener()):
            chunks=list(deepseek_stream(Secret(),{'messages':[]},threading.Event(),lambda abort:None))
        self.assertEqual(chunks[-1],{'done':True});self.assertEqual(chunks[0]['choices'][0]['delta']['content'],'local')
