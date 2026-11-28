"""Exercise one real, task-bound stdio MCP call through the Go public API."""
from hashlib import sha256
import json
import sqlite3
import time
from urllib.parse import urlencode
from uuid import uuid4

from check_core_chain import BASE, require


def seed_extension_task(chain):
    """Seed the isolated library before the Go database owner starts."""
    require(chain.process is None and chain.data.is_relative_to(BASE.parent/'artifacts'),
            '扩展样本只能建立在尚未启动的隔离检查库内。')
    task, attempt = str(uuid4()), str(uuid4())
    spec = {'provider':'codex','goal':'验证任务 MCP 授权与回执','files':[],'writable':[],
            'budget':{'seconds':30,'attempts':1,'tools':1,'requests':0,'cny':0}}
    raw = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(',',':'))
    fingerprint = sha256(raw.encode()).hexdigest()
    now = time.time()
    with sqlite3.connect(chain.data/'tasks.sqlite3') as db:
        db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',
                   (task, chain.project_id, '扩展授权样本', raw, fingerprint, 'COMPLETED',
                    json.dumps({'actor':'fixture','spec_hash':fingerprint,'expires_at':now+1800}), now, now))
        db.execute('INSERT INTO attempts(id,task_id,request_id,owner,state,started,deadline,workspace,baseline,worker_instance) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (attempt,task,str(uuid4()),'fixture','COMPLETED',now,now+1800,str(chain.project),'{}','fixture'))
    chain.extension_scope = dict(project_id=chain.project_id, task_id=task, attempt_id=attempt, spec_hash=fingerprint)


def wait_run(chain, identity):
    deadline = time.monotonic()+25
    while time.monotonic()<deadline:
        run = chain.api('/api/v1/extensions?'+urlencode({'run_id':identity}))['run']
        if run['state'] not in {'QUEUED','RUNNING','STOP_REQUESTED'}:
            require(run['state']=='SUCCEEDED',run.get('error') or run['state'])
            return run
        time.sleep(.1)
    raise RuntimeError('MCP 样本未在时限内完成。')


def check_extension_chain(chain):
    scope = chain.extension_scope
    catalog = chain.api('/api/v1/extensions')
    require(scope in catalog['contexts'] and len({r['partner_id'] for r in catalog['partner_releases']})==8,
            '任务准入或八位伙伴发行版未在发现接口列出。')
    entry = chain.command('extension.save',{'name':'fixtureEcho','source':'local fixture',
        'config':{'command':chain.profile['python_executable'],'args':[str(BASE/'tools/fixture_mcp.py')],
                  'cwd':str(chain.project)}})['receipt']['extension']
    version = {k:entry[k] for k in ('id','revision')}
    chain.command('extension.enable',{**version,'enabled':True})
    checked = wait_run(chain, chain.command('extension.run',{**version,'operation':'check'})['receipt']['run_id'])
    require([t['name'] for t in checked['tools']]==['ping','echo'],'MCP 分页发现不符。')
    payload = {**version,'operation':'call','tool':'echo','arguments':{'text':'阶段 3 MCP 调用'}}
    grant = chain.command('extension.authorize',{**scope,**payload,'confirm':True})['receipt']['authorization_id']
    command_id = str(uuid4())
    submit = {**payload,'authorization_id':grant}
    run_id = chain.command('extension.run',submit,command_id)['receipt']['run_id']
    run = wait_run(chain,run_id)
    require(all(run[k]==value for k,value in scope.items()) and run['authorization_id']==grant
            and run['command_id']==command_id and run['extension']['revision']==version['revision']
            and run['result']['content'][0]['text']==payload['arguments']['text'],'MCP 冻结身份、输入或结果不符。')
    require(chain.api('/api/v1/receipt?'+urlencode({'id':command_id}))['receipt']['run_id']==run_id,
            'MCP 命令回执未关联原运行。')
    require([r['id'] for r in chain.api('/api/v1/extensions?'+urlencode({'task_id':scope['task_id']}))['runs']]==[run_id],
            '按任务未查回唯一调用。')
    unused = chain.command('extension.authorize',{**scope,**payload,'confirm':True})['receipt']['authorization_id']
    chain.command('extension.enable',{**version,'enabled':False})
    rejected = chain.command('extension.run',{**payload,'authorization_id':unused},expected=409)
    require('停用' in rejected.get('error',''),'停用后的授权未被明确拒绝。')
    return dict(extension_id=entry['id'], revision=version['revision'], authorization_id=grant,
                command_id=command_id, run_id=run_id, task_id=scope['task_id'], attempt_id=scope['attempt_id'],
                input_hash=run['input_hash'], result=run['result'], disabled_rejected=True,
                isolated_task_fixture=True, real_model_requests=0)
