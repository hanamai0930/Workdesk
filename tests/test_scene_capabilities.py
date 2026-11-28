import json
from pathlib import Path
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from support import BASE, LocalFlowCase
from researchos.workdesk import scene_runtime


class SceneCapabilities(LocalFlowCase):
    def skill(self):
        folder=self.root/'example-skill';folder.mkdir()
        (folder/'SKILL.md').write_text('---\nname: example-skill\ndescription: A local connectivity fixture\n---\nRead the selected note.\n',encoding='utf-8')
        (folder/'references').mkdir();(folder/'references/info.txt').write_text('local resource',encoding='utf-8')
        return folder

    def import_skill(self):
        return self.command('capability.import',{'path':str(self.skill()),'source':'local test fixture','kind':'skill'})['capability']

    def enable(self,item):self.command('capability.enable',{'id':item['id'],'revision':item['revision'],'enabled':True})

    def preset(self,cap=None,extensions=None):
        row=self.command('scene.save',{'scene':'research','name':'Connectivity preset','prompt':'Fixture instructions','output':'A local result',
                   'capabilities':[cap['id']] if cap else [],'extensions':extensions or []})['preset']
        self.command('scene.default',{'id':row['id'],'revision':row['revision']})
        return row

    def test_immutable_bundle_scene_send_resume_and_revision_conflict(self):
        cap=self.import_skill();self.enable(cap);preset=self.preset(cap)
        started=self.command('independent.send',{'project_id':self.project_id,'provider':'codex','goal':'local fixture','work_scene':'research',
                          'scene_preset':{'id':preset['id'],'revision':preset['revision']}})
        self.settled(started['launch_attempt']);task=self.s.store.task(started['task_id'])
        frozen=task['spec']['scene_snapshot'];self.assertEqual(frozen['bundles'][0]['content_hash'],cap['content_hash'])
        inputs=scene_runtime.codex_input(task,[]);self.assertEqual(inputs[0]['type'],'skill');self.assertTrue(Path(inputs[0]['path']).is_file())
        self.assertIn('Fixture instructions',scene_runtime.prompt(task,'message'))
        self.assertEqual(scene_runtime.prompt(task,'follow up',True),'follow up')
        self.command('scene.save',{**{k:preset[k] for k in ('id','revision','scene','name','capabilities','extensions')},'prompt':'changed','output':''})
        with self.assertRaises(ValueError):self.command('scene.save',{**preset,'prompt':'stale'})
        (Path(cap['source_path'])/'SKILL.md').write_text('source changed',encoding='utf-8')
        self.command('capability.enable',{'id':cap['id'],'revision':cap['revision'],'enabled':False})
        row=self.command('independent.send',{**self.scope(task['id']),'goal':'follow up'})
        self.settled(row['launch_attempt']);self.assertEqual(self.s.store.task(task['id'])['spec']['scene_snapshot'],frozen)
        with self.assertRaises(ValueError):self.command('independent.send',{'project_id':self.project_id,'provider':'codex','goal':'blocked','work_scene':'research'})
        self.assertEqual(len(self.calls),2)

    def test_plugin_import_adapters_and_content_tamper(self):
        root=self.root/'bundle';(root/'skills/example-skill').mkdir(parents=True)
        skill=self.skill();(root/'skills/example-skill/SKILL.md').write_bytes((skill/'SKILL.md').read_bytes())
        (root/'plugin.json').write_text(json.dumps({'name':'fixture-plugin','version':'1.0.0'}))
        (root/'mcp.json').write_text(json.dumps({'mcpServers':{'fixture':{'command':sys.executable,'args':[str(BASE/'tools/fixture_mcp.py')],'cwd':str(self.project)}}}))
        cap=self.command('capability.import',{'path':str(root),'source':'local fixture','kind':'plugin'})['capability'];self.enable(cap);self.preset(cap)
        with self.s.store.read() as db:snapshot=self.s.scenes.freeze(db,'research')
        task={'id':'fixture-task','spec':{'scene_snapshot':snapshot}}
        self.assertEqual(len(scene_runtime.servers(task,'qwen')),1)
        native=scene_runtime.codex_thread(task,{'config':{'preserved':1}});self.assertEqual(native['config']['preserved'],1)
        self.assertIn('customSkillDirs',scene_runtime.deepseek_patch(task))
        config={};env={};scene_runtime.qwen(self.loc,task,config,env)
        defaults=json.loads(Path(env['QWEN_CODE_SYSTEM_DEFAULTS_PATH']).read_text())
        self.assertEqual(len(defaults['skills']['directories']),1)
        self.command('capability.remove',{'id':cap['id'],'revision':cap['revision']})
        self.assertTrue(Path(cap['path']).is_dir());self.assertEqual(len(scene_runtime.skills(task)),1)
        path=Path(cap['path'])/'skills/example-skill/SKILL.md';path.write_text('tampered')
        with self.assertRaises(ValueError):scene_runtime.skills(task)

    def wait_extension(self,identity):
        deadline=time.monotonic()+18
        while time.monotonic()<deadline:
            with self.s.store.read() as db:row=self.s.collaboration.get(db,'extension_run',identity)
            if row['state'] not in {'QUEUED','RUNNING','STOP_REQUESTED'}:return row
            time.sleep(.03)
        self.fail('extension did not terminate')

    def test_stdio_catalog_pagination_exact_call_and_stale_version(self):
        e=self.command('extension.save',{'name':'fixture','source':'test','config':{'command':sys.executable,'args':[str(BASE/'tools/fixture_mcp.py')],'cwd':str(self.project)}})['extension']
        version={k:e[k] for k in ('id','revision')};self.command('extension.enable',{**version,'enabled':True})
        check=self.command('extension.run',{**version,'operation':'check'})
        result=self.wait_extension(check['run_id']);self.assertEqual(result['state'],'SUCCEEDED');self.assertEqual([t['name'] for t in result['tools']],['ping','echo']);self.assertFalse(result['tool_request_sent'])
        call=self.command('extension.run',{**version,'operation':'call','tool':'echo','arguments':{'text':'local echo'}})
        result=self.wait_extension(call['run_id']);self.assertEqual(result['state'],'SUCCEEDED');self.assertEqual(result['result']['content'][0]['text'],'local echo')
        self.command('extension.save',{**version,'name':'fixture','source':'updated','config':e['config']})
        with self.assertRaises(ValueError):self.command('extension.run',{**version,'operation':'call','tool':'echo','arguments':{}})
        self.assertEqual(self.calls,[])

    def test_task_mcp_grant_result_revoke_and_restore(self):
        source=self.root/'server.py'
        source.write_bytes((BASE/'tools/fixture_mcp.py').read_bytes())
        config={'command':sys.executable,'args':[str(source)],'cwd':str(self.project)}
        e=self.command('extension.save',{'name':'fixture','source':'local','config':config})['extension']
        version={k:e[k] for k in ('id','revision')}
        self.command('extension.enable',{**version,'enabled':True})
        check=self.wait_extension(self.command('extension.run',{**version,'operation':'check'})['run_id'])
        self.assertEqual(check['state'],'SUCCEEDED',check.get('error'))
        task=self.draft()
        started=self.start(task)
        self.settled(started['launch_attempt'])
        scope=next(c for c in self.s.extensions.catalog()['contexts'] if c['task_id']==task['task_id'])
        call={**version,'operation':'call','tool':'echo','arguments':{'text':'stage 3 task'}}
        def approve():
            return self.command('extension.authorize',{**scope,**call,'confirm':True})['authorization_id']
        with self.assertRaises(ValueError):
            self.command('extension.run',{**scope,**call})
        grant=approve()
        with self.assertRaises(ValueError):
            self.command('extension.run',{**call,'authorization_id':grant,'arguments':{'text':'changed'}})
        submit={**call,'authorization_id':grant}
        from uuid import uuid4
        command_id=str(uuid4())
        receipt=self.command('extension.run',submit,command_id)
        self.assertEqual(self.command('extension.run',submit,command_id),receipt)
        run=self.wait_extension(receipt['run_id'])
        self.assertEqual(run['result']['content'][0]['text'],'stage 3 task')
        self.assertEqual(run['state'],'SUCCEEDED')
        self.assertEqual(self.s.extensions.catalog(run_id=run['id'])['run'],run)
        self.assertEqual(self.s.extensions.catalog(task_id=scope['task_id'])['runs'],[run])
        self.assertEqual((run['command_id'],run['authorization_id'],run['extension']['revision']),
                         (command_id,grant,version['revision']))
        self.assertEqual(len(run['dependencies']),3)
        with self.assertRaises(ValueError):self.command('extension.run',submit)
        second=approve()
        self.command('extension.enable',{**version,'enabled':False})
        with self.assertRaises(ValueError):
            self.command('extension.run',{**call,'authorization_id':second})
        self.command('extension.enable',{**version,'enabled':True})
        self.wait_extension(self.command('extension.run',{**version,'operation':'check'})['run_id'])
        with self.assertRaises(ValueError):
            self.command('extension.run',{**call,'authorization_id':second})
        source.write_text('# changed',encoding='utf-8')
        with self.assertRaises(ValueError):approve()
        source.write_bytes((BASE/'tools/fixture_mcp.py').read_bytes())
        updated=self.command('extension.save',{**version,'name':'fixture','source':'updated','config':config})['extension']
        restored=self.command('extension.restore',{'id':e['id'],'revision':updated['revision'],
                              'target_revision':version['revision']})['extension']
        self.assertFalse(restored['enabled'])
        self.assertNotEqual(restored['revision'],version['revision'])
        self.assertEqual(restored['source'],'local')

    def test_streamable_http_check_and_tool_call(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):self.send_error(405)
            def do_POST(self):
                msg=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if 'id' not in msg:self.send_response(202);self.end_headers();return
                if msg['method']=='initialize':result={'protocolVersion':msg['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'http-fixture','version':'1'}}
                elif msg['method']=='tools/list':result={'tools':[{'name':'echo','inputSchema':{'type':'object'}}]}
                else:result={'content':[{'type':'text','text':'http echo'}]}
                raw=json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        e=self.command('extension.save',{'name':'httpFixture','source':'test','config':{'transport':'http','url':'http://127.0.0.1:'+str(server.server_port)+'/mcp'}})['extension']
        version={k:e[k] for k in ('id','revision')};self.command('extension.enable',{**version,'enabled':True})
        check=self.command('extension.run',{**version,'operation':'check'});result=self.wait_extension(check['run_id']);self.assertEqual(result['state'],'SUCCEEDED',result.get('error'))
        run=self.command('extension.run',{**version,'operation':'call','tool':'echo','arguments':{}});result=self.wait_extension(run['run_id']);self.assertEqual(result['state'],'SUCCEEDED',result.get('error'));self.assertEqual(result['result']['content'][0]['text'],'http echo')
        self.assertEqual(self.calls,[])
