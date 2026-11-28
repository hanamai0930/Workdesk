"""Check Stage 3 B/C/D against an independently identified development library."""
import argparse
from hashlib import sha256
from io import BytesIO
import sqlite3
from urllib.parse import urlencode
from zipfile import ZipFile
from contextlib import contextmanager
import json
import subprocess
import time
from uuid import uuid4
from pathlib import Path
from check_stage3_extension_job import seed_extension_task, check_extension_chain
from check_stage3_browser_job import check_browser_job
from check_core_chain import BASE, Chain, core_runtime, request, require


@contextmanager
def fixture(executable, batch):
    chain = Chain(executable, evidence_stage='stage3/batch-'+batch.lower())
    chain.seed()
    if 'B' in batch or 'C' in batch: seed_extension_task(chain)
    if 'C' in batch:
        # Historical reply-only result, created in the pre-existing data format.
        scope=chain.extension_scope
        chain.old_reply='第一版项目说明\n'
        chain.reply_id='result:'+sha256(scope['task_id'].encode()).hexdigest()
        saved={'id':chain.reply_id,'task_id':scope['task_id'],'attempt_id':scope['attempt_id'],
               'created':time.time(),'snapshot':{'answer':chain.old_reply,'manifest':{}}}
        with sqlite3.connect(chain.data/'tasks.sqlite3') as db:
            db.execute('INSERT INTO collaboration VALUES(?,?,?,?)',('work_result',chain.reply_id,json.dumps(saved),time.time()))
    chain.manifest = core_runtime.freeze(BASE, chain.data, chain.executable)
    core_runtime.prepare_migration(chain.profile, chain.executable, fixture=True)
    subprocess.run([str(chain.executable), '--config', chain.profile['config_path'], '--base', str(BASE),
                    '--fixture', '--migrate-partners-only'], check=True, capture_output=True, timeout=30)
    try:
        chain.start()
        yield chain
    finally:
        chain.cleanup()


def await_job(chain, job_id, timeout=45):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=chain.api('/api/v2/jobs/'+job_id)
        if result['state'] not in ('QUEUED','RUNNING','STOP_REQUESTED'):
            return result
        time.sleep(.25)
    raise RuntimeError('作业未在检查时限内返回')


def submit(chain, version, parameters, files=None):
    grant=chain.command('tool.authorize',dict(project_id=chain.project_id,tool_version=version,
                        input_files=files or [],parameters=parameters,confirm=True))['receipt']['authorization_id']
    command=str(uuid4())
    job=chain.command('tool.run',{'authorization_id':grant},command)['receipt']['invocation_id']
    recovered=chain.api('/api/v1/receipt?id='+command)
    require(recovered['receipt']['invocation_id']==job,'命令与作业不匹配')
    return job


def check_b(chain):
    def git(*args):
        subprocess.run(['git','-C',str(chain.project),*args],check=True,capture_output=True)
    git('init','--quiet')
    git('add','notes.txt')
    git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
        'commit','--quiet','-m','Initial fixture')
    job = await_job(chain, submit(chain,'computer/1',
        dict(action='file.edit',path='notes.txt',content='updated by the project tool')))
    require(job['state']=='COMPLETED',job.get('error') or job['state'])
    diff = await_job(chain,submit(chain,'computer/1',dict(action='git.diff',path='notes.txt')))
    require(diff['state']=='COMPLETED',diff.get('error') or diff['state'])
    output=next(Path(a['path']) for a in diff['artifacts'] if a['path'].endswith('output.txt'))
    require('+updated by the project tool' in output.read_text('utf8'),'Git 差异与文件不符')
    refusal=chain.command('tool.authorize',dict(project_id=chain.project_id,
        tool_version='computer/1',parameters=dict(action='file.read',path='../outside.txt'),
        confirm=True),expected=409)
    require(refusal['no_dispatch'],'越界请求未明确拒绝')
    (chain.project/'wait.py').write_text("import time\nprint('ready',flush=True)\ntime.sleep(90)\n",encoding='utf8')
    proc=submit(chain,'computer/1',dict(action='terminal.run',path='wait.py'))
    deadline=time.monotonic()+25
    while time.monotonic()<deadline:
        state=chain.api('/api/v2/jobs/'+proc)
        if state['state']=='RUNNING' and (chain.data/'results'/proc/'console.txt').exists():
            console=chain.api('/api/v2/jobs/'+proc+'/console')
            if 'ready' in console['text']:break
        time.sleep(.2)
    else: raise RuntimeError('未读到进程就绪输出')
    chain.command('tool.cancel',dict(invocation_id=proc))
    cancelled=await_job(chain,proc)
    require(cancelled['state']=='CANCELLED',cancelled['state'])
    return dict(file_edit=job['id'],diff=diff['id'],terminal=proc,terminal_state=cancelled['state'],
                extension=check_extension_chain(chain),outside_rejected=True,browser=check_browser_job(chain,submit,await_job),real_model_requests=0)


def check_c(chain):
    from check_stage3_archive import check_archive
    from check_stage3_knowledge import check_knowledge
    from check_stage3_memory import check_memory
    base='/api/v2/projects/'+chain.project_id
    reply=next(row for row in chain.api(base+'/materials')['materials'] if row['kind']=='reply')
    version=json.dumps(reply['version'])
    expected=chain.old_reply.encode('utf8')
    require(reply['sha256']==sha256(expected).hexdigest(),'历史回复版本摘要不符')
    comparison=chain.api(base+'/compare?'+urlencode({'left':version,'right':version}))
    require(comparison['identical'] and comparison['mode']=='text','项目回复比较失败')
    exported=request(chain.origin,base+'/export?'+urlencode({'version':version}),token=chain.backend['viewer'],raw=True)
    with ZipFile(BytesIO(exported)) as package:
        file=json.loads(package.read('manifest.json'))['versions'][0]
        require(file['attempt_id']==chain.extension_scope['attempt_id'] and package.read(file['archive_path'])==expected,
                '归档回复与原任务的 UTF-8 字节不符')
    knowledge=check_knowledge(chain)
    memory=check_memory(chain)
    sources=[{'kind':'knowledge','citation':knowledge['current_reference']},
             {'kind':'material','version':reply['version']}]
    bridge=chain.command('project.handoff-create',
        {'scope':'project','project_id':chain.project_id,'sources':sources})['receipt']
    require(bool(bridge['handoff_id']),'知识和历史回复未形成项目接续')
    contract=chain.api('/api/v2/foundation-contract')
    require(contract['implementation_revision']==chain.manifest['implementation_revision']
            and contract['contract']['knowledge']['search_command']=='knowledge.search','运行契约版本不符')
    return {'legacy_reply_sha256':reply['sha256'],'knowledge':knowledge,'memory_and_handoff':memory,
            'knowledge_to_handoff':bridge['handoff_id'],'archive':check_archive(chain)}


def check_d(chain):
    from check_stage3_schedule import check_schedule
    from check_stage3_notifications import check_notifications
    from check_stage3_budget import check_budget
    scheduled=check_schedule(chain)
    absent=chain.api('/api/v1/receipt?id='+str(uuid4()))
    require(absent['state']=='UNKNOWN_OR_NOT_COMMITTED' and absent['no_resend'],
            '回执缺失时未返回待核对状态')
    return {'missing_receipt':absent,'schedule':scheduled,'budget':check_budget(chain,scheduled),
            'notifications':check_notifications(chain,scheduled)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable',required=True,type=Path)
    parser.add_argument('--batch',choices=('B','C','D','BCD'),required=True)
    args=parser.parse_args()
    with fixture(args.executable,args.batch) as chain:
        evidence={'implementation':chain.manifest['implementation_revision'],'checks':{},'fixture':True}
        for batch in args.batch:
            evidence['checks'][batch]={'B':check_b,'C':check_c,'D':check_d}[batch](chain)
            if batch in ('B','C','D'):
                extra=[evidence['checks']['D']['schedule']['round_id']] if batch=='D' else []
                subprocess.run(['node',str(BASE/'tools/check_stage3_ui.cjs'),str(chain.root),batch,*extra],check=True,timeout=100)
                evidence['checks'][batch]['browser_entry_verified']=True
        output=chain.root/'foundation-check.json'
        output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')
        print(output)


if __name__=='__main__':
    main()
