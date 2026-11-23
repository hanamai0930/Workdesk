"""Scoped tools retain agent autonomy; check programs require explicit host permission."""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from .paths import clean_env, legacy, process_identity
from .store import canonical, digest, uid
from . import workspace


DEFINITIONS = [
 {'name':'list_files','description':'List approved material and permitted output filenames.',
  'schema':{'type':'object','properties':{},'additionalProperties':False}},
 {'name':'read_file','description':'Read approved UTF-8 text. For bounded output use offset="0", limit="12000" (Unicode characters); follow next_offset until total_characters. sha256/bytes identify the full file; chunk_sha256/chunk_bytes identify the returned page.',
  'schema':{'type':'object','properties':{'file':{'type':'string'},'offset':{'type':'string'},'limit':{'type':'string'}},'required':['file'],'additionalProperties':False}},
 {'name':'search_files','description':'Search a literal string within approved UTF-8 files.',
  'schema':{'type':'object','properties':{'query':{'type':'string'}},'required':['query'],'additionalProperties':False}},
 {'name':'write_file','description':'Replace an approved writable file with an observed old SHA256; empty hash only for a new approved file.',
  'schema':{'type':'object','properties':{'file':{'type':'string'},'old_sha256':{'type':'string'},'content':{'type':'string'}},
            'required':['file','old_sha256','content'],'additionalProperties':False}},
 {'name':'run_check','description':'Run one exact Human-approved check by its id. A per-call Human approval may be required.',
  'schema':{'type':'object','properties':{'check_id':{'type':'string'}},'required':['check_id'],'additionalProperties':False}},
]


class ToolRunner:
    def __init__(self, store, locations, task, attempt, stop):
        self.store,self.locations,self.task,self.attempt,self.stop=store,locations,task,attempt,stop
        self.root=Path(attempt['workspace']);self.spec=task['spec'];self.lock=threading.Lock();self.job=None
        self.allowed=sorted(set(self.spec['materials'])|set(self.spec['writable']))
        with self.store.read() as db:
            row=db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='execution_reserved' ORDER BY seq LIMIT 1",(attempt['id'],)).fetchone()
        self.execution=json.loads(row['payload']) if row else {}
        self.allowed_tools=self.execution.get('allowed_tools',[d['name'] for d in DEFINITIONS])

    def event(self,kind,data):return self.store.event(self.task['id'],self.attempt['id'],kind,data)

    def check_cancel(self):
        if self.stop.is_set() or time.time()>=self.attempt['deadline']:
            raise ValueError('已请求停止或任务期限已到。')
        current=self.store.attempt(self.attempt['id'])
        with self.store.read() as db:
            retired=db.execute("SELECT data FROM collaboration WHERE kind='ownership' AND id=?",('owner:'+self.task['id'],)).fetchone()
        if retired and json.loads(retired[0]).get('state')=='RETIRED':raise ValueError('原执行归属已转交，旧工具能力失效。')
        if current['owner']!=self.attempt['owner'] or current['worker_instance']!=self.attempt['worker_instance'] or current['state'] in {'ORPHANED_UNKNOWN','SEND_UNKNOWN','STOP_UNKNOWN'}:
            raise ValueError('执行归属或旧状态未核对，工具不执行。')

    def execute(self,name,args,call_id=None):
        with self.lock:
            self.check_cancel()
            if name not in self.allowed_tools:raise ValueError('本次接续未授权此工具；原任务范围不能覆盖本次收窄限制。')
            definition=next((item for item in DEFINITIONS if item['name']==name),None)
            if definition is None or not isinstance(args,dict):raise ValueError('工具不在授权范围内。')
            schema=definition['schema']
            if set(args)-set(schema['properties']) or set(schema.get('required',[]))-set(args):
                raise ValueError('工具参数不符合准确规格。')
            if any(not isinstance(value,str) for value in args.values()):raise ValueError('工具参数须为文本。')
            call_id=self.attempt['id']+':'+str(call_id or uid());fingerprint=digest({'name':name,'args':args})
            with self.store.write() as db:
                prior=db.execute('SELECT * FROM tool_calls WHERE id=?',(call_id,)).fetchone()
                if prior:
                    if prior['hash']!=fingerprint:raise ValueError('重复工具身份的参数已改变。')
                    if prior['state']=='COMPLETED':return json.loads(prior['result'])
                    raise ValueError('原工具调用未确认或已失败，不重复执行。')
                count=self.store.usage(task=self.task['id'],db=db)['tool_calls']
                if count>=self.spec['budget']['tools']:raise ValueError('任务累计工具调用上限已到。')
                self.store.chain_guard(self.task['id'],db,'tools',1)
                self.store.chain_guard(self.task['id'],db,'seconds')
                db.execute('INSERT INTO tool_calls VALUES(?,?,?,?,?)',(call_id,self.attempt['id'],fingerprint,'INTENT',None))
                self.store.event(self.task['id'],self.attempt['id'],'tool_started',{'name':name,'arguments':args,'call_id':call_id},db)
            try:
                if name=='list_files':result={'files':[f for f in self.allowed if (self.root/f).is_file()],
                                             'writable':self.spec['writable'],'checks':[c['id'] for c in self.spec['checks']]}
                elif name=='read_file':
                    if args['file'] not in self.allowed:raise ValueError('文件不在材料或授权输出清单内。')
                    result=workspace.read_text(self.root,args['file'])
                    if 'offset' in args or 'limit' in args:
                        start=args.get('offset','0');limit=args.get('limit','12000')
                        if not start.isascii() or not start.isdigit() or len(start)>10 or not limit.isascii() or not limit.isdigit() or len(limit)>5:
                            raise ValueError('分段位置和长度须为非负十进制字符位置。')
                        start=int(start);limit=int(limit);content=result['content'];total=len(content)
                        if not 1<=limit<=12000 or not 0<=start<=total:raise ValueError('分段长度须1–12000字符，位置不能越过文件。')
                        part=content[start:start+limit];raw=part.encode('utf-8');end=start+len(part)
                        result.update(content=part,offset=start,next_offset=end,total_characters=total,
                                      chunk_sha256=sha256(raw).hexdigest(),chunk_bytes=len(raw),complete=start==0 and end==total)
                elif name=='search_files':
                    if not args['query'] or len(args['query'])>2000:raise ValueError('检索字符串长度不支持。')
                    hits=[]
                    for file in self.allowed:
                        if not (self.root/file).exists():continue
                        for number,line in enumerate(workspace.read_text(self.root,file)['content'].splitlines(),1):
                            if args['query'] in line:hits.append({'file':file,'line':number,'text':line[:1000]})
                            if len(hits)>=100:break
                        if len(hits)>=100:break
                    result={'matches':hits,'limited':len(hits)>=100}
                elif name=='write_file':result=workspace.replace(self.root,args['file'],args['content'],args['old_sha256'],self.spec['writable'])
                else:result=self.run_check(args['check_id'],call_id)
                with self.store.write() as db:
                    db.execute("UPDATE tool_calls SET state='COMPLETED',result=? WHERE id=?",(canonical(result),call_id))
                    self.store.event(self.task['id'],self.attempt['id'],'tool_completed',{'name':name,'call_id':call_id,'result':result},db)
                return result
            except Exception as exc:
                with self.store.write() as db:
                    db.execute("UPDATE tool_calls SET state='FAILED',result=? WHERE id=?",(canonical({'error':str(exc)}),call_id))
                    self.store.event(self.task['id'],self.attempt['id'],'tool_failed',{'name':name,'call_id':call_id,'error':str(exc)},db)
                raise

    def run_check(self,identity,call_id):
        check=next((c for c in self.spec['checks'] if c['id']==identity),None)
        if check is None:raise ValueError('检查命令未获授权。')
        if not self.spec['host_command_permission']:raise ValueError('未批准以本机用户权限运行检查命令。')
        if check['approval']=='each':
            approval=uid()
            with self.store.write() as db:
                db.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?)',
                           (approval,self.task['id'],self.attempt['id'],call_id,canonical(check),'PENDING',time.time(),None))
                self.store.event(self.task['id'],self.attempt['id'],'approval_requested',{'id':approval,'check':check},db)
            self.store.update_attempt(self.attempt['id'],'WAITING_APPROVAL')
            while True:
                self.check_cancel()
                with self.store.read() as db:status=db.execute('SELECT state FROM approvals WHERE id=?',(approval,)).fetchone()[0]
                if status!='PENDING':break
                self.stop.wait(.15)
            self.store.update_attempt(self.attempt['id'],'RUNNING')
            if status!='APPROVED':raise ValueError('Human已拒绝该次检查。')
        self.check_cancel()
        argv=[str(self.locations.python) if value=='{python}' else value for value in check['argv']]
        env=clean_env();env['PYTHONDONTWRITEBYTECODE']='1'
        # Only exact approved argv, shell=False. This is not network/OS isolation.
        proc=subprocess.Popen(argv,cwd=self.root,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        job=None
        try:
            if os.name=='nt':job=legacy(self.locations,'provider-harness','owned_job').OwnedJob(proc)
            self.job=job
            self.event('check_process_started',{'check_id':identity,**process_identity(proc.pid),'argv':check['argv'],
                                               'job_controller':process_identity(os.getpid()),'kill_on_job_close':bool(job),
                                               'os_isolation':'NOT_PROVIDED','network_isolation':'UNKNOWN'})
            captured={'stdout':bytearray(),'stderr':bytearray()}
            def drain(stream,label):
                while True:
                    chunk=stream.read1(2048)
                    if not chunk:return
                    remaining=65536-len(captured[label])
                    if remaining>0:
                        chunk=chunk[:remaining];captured[label].extend(chunk)
                        self.event('tool_output',{'check_id':identity,'channel':label,'text':chunk.decode('utf-8','replace')})
            threads=[threading.Thread(target=drain,args=(proc.stdout,'stdout'),daemon=True),
                     threading.Thread(target=drain,args=(proc.stderr,'stderr'),daemon=True)]
            for thread in threads:thread.start()
            deadline=min(self.attempt['deadline'],time.time()+check['seconds'])
            cancelled=False
            while proc.poll() is None:
                if self.stop.wait(.1) or time.time()>=deadline:cancelled=True;break
            scope=job.close() if job else None;job=None;self.job=None
            if proc.poll() is None:proc.terminate()
            proc.wait(timeout=5)
            for thread in threads:thread.join(2)
            result={'check_id':identity,'exit_code':proc.returncode,'cancelled':cancelled,
                    **{k:v.decode('utf-8','replace') for k,v in captured.items()},'owned_job':scope,
                    'arbitrary_external_processes':'UNKNOWN'}
            self.event('check_process_exited',result)
            return result
        finally:
            if job:job.close()
            if proc.poll() is None:proc.terminate();proc.wait(timeout=5)
            for stream in (proc.stdout,proc.stderr):stream.close()
            self.job=None
