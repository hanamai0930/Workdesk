"""Protocol-chain checks using local upstreams and fake official processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from researchos.workdesk.partners.gateway import RunGateway, gemini_request, ProtocolUnsupported
from researchos.workdesk.partners.launch import build_launch


FAKE = r'''
import sys,json
partner=sys.argv[1]
def send(v):print(json.dumps(v),flush=True)
if partner=='deepseek':
 send({'jsonrpc':'2.0','method':'workdesk.accountSelection','params':{'authenticated':True,'state':'ACCOUNT_CATALOG_AVAILABLE',
       'selection':{'provider':'deepseek-account','model':'deepseek-flash'},'source':'OFFICIAL_WEB_PROFILE_SAVED_ACCOUNT_DEFAULT',
       'models':[{'id':'deepseek-flash','name':'DeepSeek Flash','provider':'deepseek-account'}]}})
for raw in sys.stdin:
 m=json.loads(raw); method=m.get('method')
 if method=='initialize':result={'authMethods':[{'id':'official-oauth','name':'Official account'}]}
 elif method=='authenticate':result={}
 elif method=='thread/start':result={'thread':{'id':'native-thread'}}
 elif method=='turn/start':
  send({'method':'item/agentMessage/delta','params':{'threadId':'native-thread','delta':'native-result'}})
  send({'id':m['id'],'result':{'turn':{'id':'native-turn'}}})
  send({'method':'turn/completed','params':{'threadId':'native-thread','turn':{'id':'native-turn','status':'completed'}}});continue
 elif method=='session/new':result={'sessionId':'native-session'}
 elif method=='session/prompt':
  send({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'native-session','update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'native-acp'}}}})
  result={'stopReason':'end_turn'}
 elif method=='account/read':result={'account':None}
 elif method=='session/query':
  assert m['params']['contentBlocks'][0]['text']=='hello'
  result={'kind':'prompt','messageId':'native-message'}
 elif method=='session/wait':result={}
 elif method=='shutdown':result={}
 else:
  if 'id' not in m:continue
  result={}
 if 'id' in m:send({'id':m['id'],'result':result})
'''


class Transports(unittest.TestCase):
    def test_gemini_tool_identity_and_rejection(self):
        value = gemini_request({'contents': [
            {'role': 'model', 'parts': [{'functionCall': {'id': 'first', 'name': 'read', 'args': {'path': 'a'}}},
                                       {'functionCall': {'id': 'second', 'name': 'read', 'args': {'path': 'b'}}}]},
            {'role': 'user', 'parts': [{'functionResponse': {'name': 'read', 'response': {'text': 'a'}}},
                                      {'functionResponse': {'name': 'read', 'response': {'text': 'b'}}}]}],
            'tools': [{'functionDeclarations': [{'name': 'read', 'parameters': {'type': 'OBJECT'}}]}]}, 'deepseek-flash')
        self.assertEqual([m['tool_call_id'] for m in value['messages'][1:]], ['first', 'second'])
        self.assertEqual(value['tools'][0]['function']['parameters']['type'], 'object')
        with self.assertRaises(ProtocolUnsupported):
            gemini_request({'tools': [{'googleSearch': {}}]}, 'deepseek-flash')
        # Explicit sampling choices have no equivalent in DeepSeek's API.
        with self.assertRaisesRegex(ProtocolUnsupported, 'topK'):
            gemini_request({'generationConfig': {'topK': 64}}, 'deepseek-flash')

    def test_gemini_native_preset_preserves_explicit_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'gemini'
            config = {'partner_id': 'gemini', 'mode': 'run', 'route': 'api', 'entry': [sys.executable],
                      'home': str(home), 'workspace': tmp, 'model': 'deepseek-flash',
                      'gateway_url': 'http://127.0.0.1:12345', 'gateway_token': 'EPHEMERAL'}
            spec = build_launch(config)
            path = home / '.gemini/settings.json'
            settings = json.loads(path.read_text('utf-8'))
            preset = settings['modelConfigs']['customAliases']['chat-base']['modelConfig']['generateContentConfig']
            self.assertNotIn('topK', preset)
            self.assertEqual(preset['topP'], 0.95)
            self.assertTrue(spec.limitations)
            preset['topK'] = 32
            settings['modelConfigs']['customOverrides'] = [{'match': {'model': 'x'}, 'modelConfig': {'generateContentConfig': {'topK': 12}}}]
            path.write_text(json.dumps(settings), encoding='utf-8')
            build_launch(config)
            self.assertEqual(json.loads(path.read_text('utf-8'))['modelConfigs'], settings['modelConfigs'])

    def test_native_host_reads_do_not_create_human_inputs(self):
        from researchos.workdesk.partners.worker import PartnerWorker
        class Rpc:
            def __init__(self): self.sent = []
            def send(self, value): self.sent.append(value)
        for partner, protocol, method in [('glm', 'zcode', 'session/requestRuntimePreferences'),
                                           ('glm', 'zcode', 'session/readReplyLanguage'),
                                           ('qwen', 'acp', 'craft/drainMidTurnQueue')]:
            events = []
            worker = PartnerWorker({'partner_id': partner, 'session_id': 'native-session'}, events.append)
            worker.spec = type('Spec', (), {'protocol': protocol})()
            worker.rpc = Rpc()
            worker._native_message({'id': 0, 'method': method, 'params': {'sessionId': 'native-session'}})
            response = worker.rpc.sent[-1]['result']
            self.assertFalse(worker.requests)
            self.assertFalse([e for e in events if e['type'] in ('input', 'approval')])
            if partner == 'qwen': self.assertEqual(response, {'messages': [], 'hasQueuedPrompt': False})
            elif method.endswith('readReplyLanguage'): self.assertEqual(response, {'language': None})
            else:
                self.assertTrue(response['nativeSearchEnhancementsEnabled'])
                self.assertEqual(response['modelContextBudgetStrategy'], 'preflight-v1')
            worker._native_message({'id': 1, 'method': method, 'params': {'sessionId': 'different-session'}})
            self.assertEqual(worker.rpc.sent[-1]['error']['code'], -32602)

    def test_native_approval_prefers_allow_once(self):
        from researchos.workdesk.partners.worker import PartnerWorker
        events, sent = [], []
        worker = PartnerWorker({'partner_id': 'grok'}, events.append)
        worker.spec = type('Spec', (), {'protocol': 'acp'})()
        worker.rpc = type('Rpc', (), {'send': lambda _, message: sent.append(message)})()
        worker._native_message({'id': 0, 'method': 'session/request_permission', 'params': {'options': [
            {'kind': 'allow_always', 'optionId': 'all'}, {'kind': 'allow_once', 'optionId': 'once'}]}})
        worker.control({'action': 'approval', 'request_id': '0', 'decision': 'allow'})
        self.assertEqual(sent[-1]['result']['outcome']['optionId'], 'once')

    def test_acp_login_method_choice_roundtrip(self):
        from researchos.workdesk.partners.worker import PartnerWorker, NativeRejected
        methods = [{'id': 'first', 'name': 'Personal'}, {'id': 'second', 'name': 'Organization'}]
        for value in ('second', 'Organization', '2', '"second"', '{"value":"second"}',
                      '{"auth_method":"second"}', {'id': 'second'}):
            events, calls = [], []
            worker = PartnerWorker({'partner_id': 'gemini', 'mode': 'login'}, events.append)
            worker.spec = type('Spec', (), {'partner_id': 'gemini'})()
            class Rpc:
                def request(self, method, params=None, **kwargs):
                    calls.append((method, params))
                    return {'authMethods': methods} if method == 'initialize' else {}
            worker.rpc = Rpc()
            thread = threading.Thread(target=worker._acp)
            thread.start()
            for _ in range(100):
                if any(e['type'] == 'login' for e in events): break
                threading.Event().wait(.01)
            login = next(e for e in events if e['type'] == 'login')
            self.assertEqual(login['options'][1], {'value': 'second', 'label': 'Organization'})
            with self.assertRaises(NativeRejected):
                worker.control({'action': 'input', 'request_id': 'auth-method', 'text': 'absent'})
            worker.control({'action': 'input', 'request_id': 'auth-method', 'text': value})
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(calls[-1], ('authenticate', {'methodId': 'second'}))
            self.assertTrue(next(e for e in events if e['type'] == 'result')['authenticated'])

    def test_gateway_native_and_gemini_stream(self):
        requests, events = [], []
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path,body,self.headers.get('Authorization')))
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                if self.path == '/responses':
                    self.wfile.write(b'data: {"type":"response.completed"}\n\n');return
                for value in [
                    {'id':'r','model':'deepseek-flash','choices':[{'index':0,'delta':{'content':'hello'}}]},
                    {'id':'r','model':'deepseek-flash','choices':[{'index':0,'delta':{'tool_calls':[{'index':0,'id':'call1','function':{'name':'read','arguments':'{"path":'}}]}}]},
                    {'id':'r','model':'deepseek-flash','choices':[{'index':0,'delta':{'tool_calls':[{'index':0,'function':{'arguments':'"a"}'}}]},'finish_reason':'tool_calls'}]},
                    {'id':'r','model':'deepseek-flash','choices':[],'usage':{'prompt_tokens':3,'completion_tokens':4,'total_tokens':7}}]:
                    self.wfile.write(b'data: '+json.dumps(value).encode()+b'\n\n')
                self.wfile.write(b'data: [DONE]\n\n')
        server=ThreadingHTTPServer(('127.0.0.1',0),Upstream)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        gateway=RunGateway('LOCAL-SECRET','deepseek-flash',upstream='http://127.0.0.1:'+str(server.server_port),
                           _test_upstream=True,emit=lambda **v:events.append(v)).start()
        def call(path,body,token=None):
            return urlopen(Request(gateway.url+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+(token or gateway.token)}),timeout=3).read()
        try:
            raw=call('/v1/responses',{'model':'deepseek-flash','stream':True})
            self.assertIn(b'response.completed',raw)
            raw=call('/v1beta/models/deepseek-flash:streamGenerateContent',{'contents':[{'role':'user','parts':[{'text':'go'}]}]})
            chunks=[json.loads(line[6:]) for line in raw.splitlines() if line.startswith(b'data: ')]
            parts=[p for c in chunks for can in c.get('candidates',[]) for p in can['content']['parts']]
            self.assertEqual(parts[0],{'text':'hello'})
            self.assertEqual(parts[1]['functionCall']['id'],'call1')
            self.assertEqual(parts[1]['functionCall']['args'],{'path':'a'})
            self.assertTrue(all(r[2]=='Bearer LOCAL-SECRET' for r in requests))
            with self.assertRaises(HTTPError) as ctx:call('/responses',{'model':'deepseek-flash'},'bad')
            self.assertEqual(ctx.exception.code,403)
            before=len(requests)
            with self.assertRaises(HTTPError):call('/responses',{'model':'some-other-model'})
            self.assertEqual(before,len(requests))
            self.assertNotIn('LOCAL-SECRET',json.dumps(events))
        finally:
            gateway.close();server.shutdown();server.server_close()

    def test_worker_official_rpc_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fake=root/'fake.py';fake.write_text(FAKE,encoding='utf8')
            for partner in ('codex','qwen','deepseek'):
                config={'partner_id':partner,'route':'account','mode':'run','entry':[sys.executable,str(fake),partner],
                        'home':str(root/partner),'workspace':tmp,'model':'deepseek-flash','prompt':'hello'}
                env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
                process=subprocess.Popen([sys.executable,'-B','-m','researchos.workdesk.partners'],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                process.stdin.write((json.dumps({'action':'start','config':config})+'\n').encode());process.stdin.flush()
                events=[]
                while True:
                    line=process.stdout.readline()
                    if not line:break
                    events.append(json.loads(line))
                code=process.wait(timeout=10)
                self.assertEqual(code,0,(partner,events,process.stderr.read()))
                self.assertEqual(len([e for e in events if e['type']=='result']),1,(partner,events))
                self.assertFalse([e for e in events if e['type']=='error'],events)
                process.stdin.close();process.stdout.close();process.stderr.close()

    def test_launch_eight_api_routes_store_no_real_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            for partner in ('codex','deepseek','qwen','claude','grok','gemini','kimi','glm'):
                spec=build_launch({'partner_id':partner,'mode':'run','route':'api','entry':[sys.executable],
                    'home':str(Path(tmp)/partner),'workspace':tmp,'model':'deepseek-flash',
                    'gateway_url':'http://127.0.0.1:12345','gateway_token':'EPHEMERAL-TOKEN','api_key':'REAL-KEY-MUST-NOT-BE-READ'})
                self.assertNotIn('REAL-KEY-MUST-NOT-BE-READ',json.dumps(spec.env))
            for path in Path(tmp).rglob('*'):
                if path.is_file():self.assertNotIn('REAL-KEY-MUST-NOT-BE-READ',path.read_text(encoding='utf8'))

    def test_deepseek_account_inspect_does_not_initialize_without_model(self):
        from researchos.workdesk.partners.worker import PartnerWorker
        class Rpc:
            def request(self,*args,**kwargs):raise AssertionError('Account inspection must not initialize or generate')
        config={'partner_id':'deepseek','route':'account','mode':'inspect'}
        events=[]
        worker=PartnerWorker(config,events.append)
        worker.rpc=Rpc()
        worker.spec=type('Spec',(),{'route':'account'})()
        worker.deepseek_account_selection={'state':'NOT_AUTHENTICATED','authenticated':False,'models':[],'selection':None}
        worker.deepseek_account_ready.set()
        worker._deepseek()
        result=next(e for e in events if e['type']=='result')
        self.assertEqual(result['state'],'NOT_AUTHENTICATED')
        self.assertFalse(result['authenticated'])
        self.assertEqual(result['model_requests'],0)


if __name__ == '__main__':unittest.main()
