"""Protocol-chain checks using local upstreams and fake official processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import queue
import time
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
 elif method=='session/new':result={'sessionId':'native-session','modes':{'currentModeId':'default','availableModes':[{'id':'default','name':'Manual approvals'}]}}
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

LOGIN_FAKE = r'''
import sys,json,os
case=sys.argv[1]
def send(v):print(json.dumps(v),flush=True)
if case.startswith('claude'):
 if 'status' in sys.argv:
  send({'loggedIn':case!='claude-unauthenticated'})
  sys.exit(1 if case=='claude-unauthenticated' else 0)
 assert 'login' in sys.argv
 if os.name=='nt':assert sys.stdin.isatty() and sys.stdout.isatty(), 'Official manual-code flow requires a real terminal'
 print('Open https://claude.ai/oauth/authorize?state=LOCAL-FIXTURE',flush=True)
 os.write(2,b'Paste code here > ')
 value=sys.stdin.readline().rstrip('\n')
 assert value=='https://claude.ai/oauth-return?code=LOCAL-RETURNED-CODE'
 cut=len(value)//2
 os.write(2,value[:cut].encode());os.write(2,value[cut:].encode()+b'\n')
 print('Login successful',flush=True)
 sys.exit(0)
for raw in sys.stdin:
 m=json.loads(raw);method=m.get('method')
 if method=='initialize':result={}
 elif method=='account/login/start':
  assert m['params']['type']=='chatgpt'
  result={'type':'chatgpt','loginId':'official-login','authUrl':'https://auth.openai.com/authorize?state=LOCAL'}
  send({'id':m['id'],'result':result})
  send({'method':'account/login/completed','params':{'loginId':'official-login','success':True}})
  continue
 elif method=='account/read':result={'account':{'type':'chatgpt'}}
 elif method=='initialized':continue
 else:raise AssertionError('Login must not generate or start an Agent turn: '+str(method))
 send({'id':m['id'],'result':result})
'''


class Transports(unittest.TestCase):
    def test_native_resume_and_scene_mcp_keep_exact_boundaries(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from researchos.workdesk.partners.worker import NativeRejected, PartnerWorker
        with tempfile.TemporaryDirectory() as tmp:
            servers=[{'name':'fixture_server','config':{'transport':'http','url':'https://example.com/mcp'}}]
            def worker(partner):
                state=PartnerWorker({'partner_id':partner,'route':'account','mode':'run','resume':True,
                    'session_id':'same-session','prompt':'second turn','scene_mcp':servers},lambda e:events.append(e))
                state.spec=SimpleNamespace(cwd=tmp,route='api',partner_id=partner)
                state.rpc=Mock();return state
            events=[]
            qwen=worker('qwen')
            qwen.rpc.request.side_effect=lambda method,*args,**kwargs:{'initialize':{'agentCapabilities':{'loadSession':True,'mcpCapabilities':{'http':True}}},
                'session/load':{'modes':{'availableModes':[{'id':'default'}]}},'session/set_mode':{},'session/prompt':{'stopReason':'end_turn'}}[method]
            qwen._acp()
            self.assertEqual([c.args[0] for c in qwen.rpc.request.call_args_list],['initialize','session/load','session/set_mode','session/prompt'])
            self.assertEqual(qwen.rpc.request.call_args_list[1].args[1],{'sessionId':'same-session','cwd':tmp,
                'mcpServers':[{'type':'http','name':'fixture_server','url':'https://example.com/mcp','headers':[]}]})
            self.assertEqual(next(e for e in events if e.get('kind')=='native_scene_mcp_configured')['connection_state'],'NOT_ATTESTED')

            codex=worker('codex');codex.rpc.request.side_effect=[{}, {'thread':{'id':'wrong-session'}}]
            with self.assertRaisesRegex(NativeRejected,'不同'):
                codex._codex()
            self.assertEqual([c.args[0] for c in codex.rpc.request.call_args_list], ['initialize','thread/resume'])
            config=codex.rpc.request.call_args_list[-1].args[1]
            self.assertEqual(config['threadId'],'same-session')
            self.assertEqual(config['config'],{'mcp_servers.fixture_server':{'url':'https://example.com/mcp'}})

            dsh=worker('deepseek');dsh.rpc.request.side_effect=[{}, {}, {}, {}];dsh._deepseek()
            self.assertEqual([c.args[0] for c in dsh.rpc.request.call_args_list],['initialize','session/resume','session/query','session/wait'])
            self.assertEqual(dsh.rpc.request.call_args_list[1].args[1],{'sessionId':'same-session'})
            launch=build_launch({**dsh.config,'entry':[sys.executable],'home':str(Path(tmp)/'home'),'workspace':tmp})
            patch=Path(launch.argv[-1]);self.assertEqual(patch.name,'scene-mcp.patch.yml')
            self.assertEqual(json.loads(patch.read_text())[-1]['insert'][0]['config'],
                {'serverName':'fixture_server','transport':'streamable-http','url':'https://example.com/mcp'})

    def _fake_login(self, case, mode='login'):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fake=root/'login.py';fake.write_text(LOGIN_FAKE,encoding='utf8')
            partner='claude' if case.startswith('claude') else 'codex'
            config={'partner_id':partner,'route':'account','mode':mode,'entry':[sys.executable,str(fake),case],
                    'home':str(root/'home'),'workspace':tmp}
            env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
            process=subprocess.Popen([sys.executable,'-B','-m','researchos.workdesk.partners'],env=env,
                                     stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            events=[];lines=queue.Queue()
            def read():
                for raw in process.stdout:lines.put(json.loads(raw))
                lines.put(None)
            reader=threading.Thread(target=read,daemon=True);reader.start()
            try:
                process.stdin.write((json.dumps({'action':'start','config':config})+'\n').encode());process.stdin.flush()
                deadline=time.monotonic()+10
                while True:
                    try:event=lines.get(timeout=max(.1,deadline-time.monotonic()))
                    except queue.Empty:self.fail(('Login fixture did not finish',events,process.poll()))
                    if event is None:break
                    events.append(event)
                    if event.get('sensitive'):
                        # Ordinary input cannot enter the sensitive channel.
                        for action in ('input','login-input'):
                            process.stdin.write((json.dumps({'action':action,'request_id':event['request_id'],
                                'text':'https://claude.ai/oauth-return?code=LOCAL-RETURNED-CODE'})+'\n').encode())
                        process.stdin.flush()
                self.assertEqual(process.wait(timeout=2),0,(events,process.stderr.read()))
                return events
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=2)
                process.stdin.close();reader.join(timeout=2);process.stdout.close();process.stderr.close()

    def test_codex_browser_login_uses_official_receipt_without_turn(self):
        events=self._fake_login('codex')
        self.assertTrue(next(e for e in events if e['type']=='auth_status')['authenticated'])
        result=next(e for e in events if e['type']=='result')
        self.assertEqual(result['fact_scope'],'OFFICIAL_ACCOUNT_RECEIPT')

    def test_deepseek_login_closes_after_official_authentication(self):
        from researchos.workdesk.partners.worker import PartnerWorker
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fake=root/'web.py'
            fake.write_text("""import os,sys,json,pathlib
print('Official Web: http://127.0.0.1:12345/',flush=True)
print(json.dumps({'workdeskAuth':1,'event_token':os.environ['WORKDESK_AUTH_EVENT_TOKEN'],
 'auth_state':'AUTHENTICATED','verification':'OFFICIAL_DSH_SIGN_IN_SUCCEEDED','phase':'succeeded'}),flush=True)
sys.stdin.read()
pathlib.Path(os.environ['DSH_HOME'],'fixture-web-exited.json').write_text(json.dumps({'pid':os.getpid(),'exit_code':7}))
sys.exit(7)
""",encoding='utf8')
            config={'partner_id':'deepseek','route':'account','mode':'login','entry':[sys.executable,str(fake)],
                    'home':str(root/'home'),'workspace':tmp}
            env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
            process=subprocess.Popen([sys.executable,'-B','-m','researchos.workdesk.partners'],env=env,
                                     stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            lines=queue.Queue();events=[]
            def read():
                for raw in process.stdout:lines.put(json.loads(raw))
                lines.put(None)
            reader=threading.Thread(target=read,daemon=True);reader.start()
            try:
                process.stdin.write((json.dumps({'action':'start','config':config})+'\n').encode());process.stdin.flush()
                while True:
                    event=lines.get(timeout=6)
                    if event is None:break
                    events.append(event)
                self.assertEqual(process.wait(timeout=2),0,(events,process.stderr.read()))
                results=[e for e in events if e['type']=='result']
                self.assertEqual(len(results),1)
                self.assertTrue(results[0]['authenticated'])
                self.assertEqual(results[0]['fact_scope'],'OFFICIAL_ACCOUNT_RECEIPT')
                self.assertFalse([e for e in events if e['type']=='error'])
                self.assertEqual(json.loads((root/'home/.dsh/fixture-web-exited.json').read_text())['exit_code'],7)
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=2)
                process.stdin.close();reader.join(timeout=2);process.stdout.close();process.stderr.close()
        # A late authoritative fact may be recorded after a stop intention,
        # but it cannot manufacture a successful login-attempt completion.
        events=[];worker=PartnerWorker({'partner_id':'deepseek','mode':'login'},events.append)
        worker.stop_requested.set()
        worker._login_events([{'kind':'account-state','state':{'auth_state':'AUTHENTICATED','verification':'OFFICIAL_DSH_SIGN_IN_SUCCEEDED'}}])
        self.assertTrue(next(e for e in events if e['type']=='auth_status')['authenticated'])
        self.assertFalse([e for e in events if e['type']=='result'])
        self.assertFalse(worker.completed)

    def test_claude_unterminated_prompt_sensitive_code_and_status(self):
        events=self._fake_login('claude')
        self.assertTrue(any(e.get('sensitive') and e.get('input_type')=='password' for e in events))
        self.assertTrue(any(e.get('kind')=='control_rejected' for e in events))
        self.assertTrue(next(e for e in events if e['type']=='result')['authenticated'])
        self.assertNotIn('LOCAL-RETURNED-CODE',json.dumps(events))
        unauthenticated=self._fake_login('claude-unauthenticated',mode='inspect')
        self.assertFalse(next(e for e in unauthenticated if e['type']=='result')['authenticated'])

    def test_login_parser_device_codes_and_strict_completion(self):
        from researchos.workdesk.partners.auth import LoginOutput, redact_login_chunk
        parser=LoginOutput('grok')
        self.assertEqual(parser.feed('To sign in, open https://auth.x.ai/dev'),[])
        events=parser.feed('ice\nThen enter this code:\nABCD-EFGH\nWaiting for authorization...\n')
        self.assertTrue(any(e.get('user_code')=='ABCD-EFGH' for e in events))
        self.assertFalse(parser.success)
        parser.feed('\x1b[2KSigned in as fixture\n');self.assertTrue(parser.success)
        parser=LoginOutput('glm');parser.feed('{"status":"ready","credentialsPath":"private"}\n')
        self.assertTrue(parser.success)
        parser=LoginOutput('deepseek','private-nonce')
        self.assertEqual(parser.feed('{"workdeskAuth":1,"event_token":"wrong","auth_state":"AUTHENTICATED"}\n'),[])
        values=parser.feed('{"workdeskAuth":1,"event_token":"private-nonce","auth_state":"AUTHENTICATED"}\n')
        self.assertEqual(values[0]['kind'],'account-state')
        text,pending=redact_login_chunk('echo SECRET','',['SECRET-CODE'])
        self.assertEqual(text,'echo ')
        text,pending=redact_login_chunk('-CODE\n',pending,['SECRET-CODE'])
        self.assertEqual(text,'[REDACTED]\n');self.assertFalse(pending)

    def test_login_link_excludes_failed_api_endpoint_and_keeps_opaque_oauth(self):
        from researchos.workdesk.partners.auth import LoginOutput
        parser=LoginOutput('grok')
        events=parser.feed('Error: failed request (https://auth.x.ai/oauth2/device/code): connection failed\n')
        self.assertFalse(any(e.get('login_url') for e in events))
        parser=LoginOutput('claude')
        self.assertFalse(parser.feed('Open https://claude.ai/oauth/authorize\n'))
        url='https://claude.ai/oauth/authorize?s-opaque_native-token'
        events=parser.feed('Open '+url+'\n')
        self.assertEqual(next(e['login_url'] for e in events if e['kind']=='link'),url)
        parser=LoginOutput('claude')
        target='https://claude.ai/oauth/authorize?state=fixture&client_id=official'
        events=parser.feed('\x1b]8;;'+target+'\x1b\\https://claude.ai/oauth/authorize\x1b]8;;\x1b\\\n')
        self.assertEqual(next(e['login_url'] for e in events if e['kind']=='link'),target)

    def test_sensitive_login_input_rejects_wrong_ids_and_newlines(self):
        from researchos.workdesk.partners.worker import PartnerWorker, NativeRejected
        worker=PartnerWorker({'partner_id':'claude','mode':'login'},lambda _:None)
        worker.login_request='login-code-fixture'
        for request,text in [('wrong','code'),('login-code-fixture','a\nb'),('login-code-fixture','a\rb'),('login-code-fixture','a\0b')]:
            with self.assertRaises(NativeRejected):worker.control({'action':'login-input','request_id':request,'text':text})
        self.assertEqual(worker.login_request,'login-code-fixture')

    def test_acp_inspection_reports_unknown_without_authentication_claim(self):
        from researchos.workdesk.partners.worker import PartnerWorker
        for partner in ('qwen','gemini','grok','kimi'):
            events=[];worker=PartnerWorker({'partner_id':partner,'mode':'inspect'},events.append)
            worker.spec=type('Spec',(),{'protocol':'acp','partner_id':partner})()
            worker.rpc=type('Rpc',(),{'request':lambda _,*args,**kw:{'authMethods':[]}})()
            worker._acp()
            result=next(e for e in events if e['type']=='result')
            self.assertEqual(result['auth_state'],'UNKNOWN');self.assertNotIn('authenticated',result)

    def test_login_launch_uses_native_account_sites(self):
        with tempfile.TemporaryDirectory() as tmp:
            for partner,options,expected in [('codex',{},'app-server'),('deepseek',{},'--profile web'),
                    ('qwen',{},'--acp'),('gemini',{},'--experimental-acp'),('claude',{'auth_method':'claudeai'},'--claudeai'),
                    ('grok',{'auth_method':'oauth'},'--oauth'),('kimi',{'region':'mainland-cn'},'mainland-cn'),
                    ('glm',{'provider':'bigmodel'},'bigmodel')]:
                spec=build_launch({'partner_id':partner,'mode':'login','route':'account','entry':[sys.executable],
                                  'home':str(Path(tmp)/partner),'workspace':tmp,'auth_options':options})
                self.assertIn(expected,' '.join(spec.argv))
                self.assertNotIn('DEEPSEEK_API_KEY',spec.env)
                if partner=='glm':self.assertIn('--json',spec.argv)

    def test_deepseek_web_profile_overlay_preserves_user_patch(self):
        import yaml
        for content in ('# Official user selection\r\n- id: agent-default-model\r\n  config:\r\n    provider: deepseek-account\r\n    model: saved-account-model\r\n    extra: !!js process.env.PRIVATE_SETTING\r\n',
                        '[{"id":"agent-default-model","config":{"model":"saved-account-model"}}]\n'):
            with tempfile.TemporaryDirectory() as tmp:
                home=Path(tmp)/'home';patch=home/'.dsh/profiles/web/cordis.patch.yml'
                patch.parent.mkdir(parents=True);patch.write_bytes(content.encode('utf8'))
                config={'partner_id':'deepseek','mode':'login','route':'account','entry':[sys.executable],
                        'home':str(home),'workspace':tmp}
                spec=build_launch(config);first=patch.read_bytes().decode('utf8')
                self.assertNotIn('--patch',spec.argv)
                if content.startswith('#'):self.assertTrue(first.startswith(content))
                else:self.assertIn(content.rstrip()[:-1],first)
                self.assertEqual(len(yaml.compose(first).value),2)
                build_launch(config)
                self.assertEqual(first,patch.read_bytes().decode('utf8'))
                self.assertTrue((home/'.dsh/.workdesk/login-observer.mjs').is_file())
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
