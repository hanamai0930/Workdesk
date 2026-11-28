"""Check Stage 3 B/C/D against an independently identified development library."""
import argparse
from contextlib import contextmanager
import json
import subprocess
import time
from uuid import uuid4
from pathlib import Path
from check_stage3_extension_job import seed_extension_task, check_extension_chain
from check_stage3_browser_job import check_browser_job
from check_core_chain import BASE, Chain, core_runtime, require


@contextmanager
def fixture(executable, batch):
    chain = Chain(executable, evidence_stage='stage3/batch-'+batch.lower())
    chain.seed()
    if 'B' in batch: seed_extension_task(chain)
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


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable',required=True,type=Path)
    parser.add_argument('--batch',choices=('B',),required=True)
    args=parser.parse_args()
    with fixture(args.executable,args.batch) as chain:
        evidence={'implementation':chain.manifest['implementation_revision'],'checks':{},'fixture':True}
        for batch in args.batch:
            evidence['checks'][batch]={'B':check_b}[batch](chain)
        if 'B' in args.batch:
            subprocess.run(['node',str(BASE/'tools/check_stage3_ui.cjs'),str(chain.root)],check=True,timeout=65)
            evidence['checks']['B']['ui_read_result']=True
        output=chain.root/'foundation-check.json'
        output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')
        print(output)


if __name__=='__main__':
    main()
