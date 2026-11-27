"""Verify task-bound workbook editing and project export in a disposable Go instance."""
from hashlib import sha256
from io import BytesIO
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import time
from urllib.parse import urlencode
from uuid import uuid4
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook
from check_core_chain import BASE, Chain, core_runtime, request, require


def check(executable, browser):
    chain = Chain(executable, evidence_stage='stage3')
    template = chain.project / 'template.xlsx'
    book = Workbook()
    book.active.title = '预算'
    book.active['B2'], book.active['B3'], book.active['B4'] = 1, 2, '=SUM(B2:B3)'
    book.active['D4'] = '保留模板说明'
    book.save(template)
    original = sha256(template.read_bytes()).hexdigest()
    task, attempt, spec_hash = str(uuid4()), str(uuid4()), sha256(b'fixture-spec').hexdigest()
    chain.seed()
    # A historical attempt with a finite synthetic grant exercises the parent binding.
    # All subsequent commands use the public HTTP path; no model starts in this check.
    stamp = time.time()
    with sqlite3.connect(chain.data / 'tasks.sqlite3') as db:
        db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',
                   (task, chain.project_id, '预算样本', '{}', spec_hash, 'COMPLETED',
                    json.dumps({'actor':'Human control command','spec_hash': spec_hash,'expires_at': stamp+1800}), stamp, stamp))
        db.execute('INSERT INTO attempts(id,task_id,request_id,owner,state,started,deadline,workspace,baseline,worker_instance) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (attempt,task,str(uuid4()),'test','COMPLETED',stamp,stamp+1800,str(chain.project),'{}','test'))
    chain.manifest = core_runtime.freeze(BASE, chain.data, chain.executable)
    core_runtime.prepare_migration(chain.profile, chain.executable, fixture=True)
    subprocess.run([str(chain.executable), '--config',chain.profile['config_path'],'--base',str(BASE),
                    '--fixture','--migrate-partners-only'], check=True, capture_output=True, timeout=30)
    try:
        chain.start()
        identity = chain.api('/api/v1/instance')
        require(identity['implementation_revision']==chain.manifest['implementation_revision'], '候选实现不符')
        require(any(x['task_id']==task for x in chain.api('/api/v2/tool-contexts?project_id='+chain.project_id)['contexts']), '父任务不可见')
        authorize = chain.command('tool.authorize', dict(project_id=chain.project_id,task_id=task,attempt_id=attempt,spec_hash=spec_hash,
            tool_version='document/1',input_files=[template.name],confirm=True,parameters={'format':'xlsx','action':'edit',
            'name':'预算.xlsx','sheet':'预算','cells':{'B2':120,'B3':80},'expected':{'B4':200}}))
        grant = authorize['receipt']['authorization_id']
        call = str(uuid4())
        job_id = chain.command('tool.run', {'authorization_id':grant},call)['receipt']['invocation_id']
        deadline=time.monotonic()+145
        while time.monotonic()<deadline:
            job=chain.api('/api/v2/jobs/'+job_id)
            if job['state'] not in ('QUEUED','RUNNING'):break
            time.sleep(.4)
        require(job['state']=='COMPLETED', job.get('error') or job['state'])
        require(job['attempt_id']==attempt and job['task_id']==task and job['authorization_id']==grant,'作业来源不符')
        require(chain.api('/api/v1/receipt?id='+call)['receipt']['invocation_id']==job_id,'原回执不符')
        require(sha256(template.read_bytes()).hexdigest()==original,'模板原件变化')
        artifact=next(a for a in job['artifacts'] if a['path'].endswith('.xlsx'))
        raw=request(chain.origin,'/api/v2/jobs/'+job_id+'/artifact?'+urlencode({'path':artifact['path'],'download':1}),token=chain.backend['viewer'],raw=True)
        for values,b4 in ((False,'=SUM(B2:B3)'),(True,200)):
            sheet=load_workbook(BytesIO(raw),data_only=values).active
            require((sheet['B4'].value,sheet['D4'].value)==(b4,'保留模板说明'),'公式／缓存或模板保留不符')
        choice=chain.command('tool.decide',dict(invocation_id=job_id,path=artifact['path'],sha256=artifact['sha256'],
                                  confirm=True,decision='accept',note='独立检查库的工程样本'))
        route='/api/v2/projects/'+chain.project_id
        materials=chain.api(route+'/materials')['materials']
        ref=next(x['version'] for x in materials if x['kind']=='job' and x['sha256']==artifact['sha256'])
        archive=request(chain.origin,route+'/export?'+urlencode({'version':json.dumps(ref)}),token=chain.backend['viewer'],raw=True)
        with ZipFile(BytesIO(archive)) as package:
            item=json.loads(package.read('manifest.json'))['versions'][0]
            require(item['task_id']==task and package.read(item['archive_path'])==raw,'导出来源或字节不符')
        evidence={'implementation':chain.manifest['implementation_revision'],'project_id':chain.project_id,
            'fixture_task_id':task,'attempt_id':attempt,'authorization_id':grant,'command_id':call,'invocation_id':job_id,
            'input_sha256':original,'output_sha256':artifact['sha256'],'cached_value':200,'formula':'=SUM(B2:B3)',
            'checks':job['checks'],'decision_command':choice['command_id'],'source_unchanged':True,
            'export_matches':True,'real_model_requests':0,'fixture_only':True}
        if browser:
            subprocess.run(['node',str(BASE/'tools/check_document_ui.cjs'),str(chain.root)],check=True,timeout=210)
            revisions = chain.api('/api/v2/jobs?project_id='+chain.project_id)['jobs']
            revised = next(j for j in revisions if j['id'] != job_id and j['input_versions'][0].get('source_job_id') == job_id)
            version = next(a for a in revised['artifacts'] if a['path'].endswith('.xlsx'))
            next_bytes = request(chain.origin, '/api/v2/jobs/'+revised['id']+'/artifact?'+urlencode({'path':version['path']}),
                                 token=chain.backend['viewer'], raw=True)
            require(load_workbook(BytesIO(next_bytes),data_only=True).active['B4'].value == 220,'修订版回读失败')
            require(any(d['decision']=='accept' and d['sha256']==version['sha256'] for d in revised['decisions']), '验收无法对应修订版本')
            evidence.update(ui_revision_job=revised['id'], ui_cached_value=220, ui_accepted_sha256=version['sha256'])
        (chain.root/'document-check.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')
        print(chain.root/'document-check.json')
    finally:
        chain.cleanup()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable',type=Path,required=True)
    parser.add_argument('--browser', action='store_true')
    args=parser.parse_args()
    check(args.executable,args.browser)
