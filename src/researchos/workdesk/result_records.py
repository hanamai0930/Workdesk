"""Explicit result snapshots for direct native Work; no second integration write."""
import json
from pathlib import Path
import time
import tempfile
import os
import base64
import difflib
import re
from urllib.parse import unquote

from . import workspace
from .store import ACTIVE, UNKNOWN, canonical, digest, uid


def _deepseek_turn_events(db, attempt, event_type):
    result = attempt.get('result') or {}
    run_id = result.get('partner_run_id')
    if not run_id or result.get('partner_id') != 'deepseek' or attempt['state'] != 'COMPLETED':
        return
    # Older stores and local standalone endpoints can have no Go event ledger.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_partner_events'").fetchone():
        return
    query = """
        SELECT json_extract(e.data,'$.native.params.event.data')
        FROM core_partner_events e JOIN core_partner_objects r ON r.kind='run' AND r.id=e.run_id
        WHERE e.run_id=? AND json_extract(r.data,'$.parent_attempt_id')=?
          AND json_extract(r.data,'$.parent_task_id')=?
          AND json_extract(e.data,'$.native.params.sessionId')=?
          AND json_extract(e.data,'$.native.params.event.type')=?
          AND (?!='assistant/message' OR json_extract(e.data,'$.native.params.event.data.message.role')='assistant')
          AND json_extract(e.data,'$.native.params.event.data.turn')=(
              SELECT json_extract(end.data,'$.native.params.event.data.turn')
              FROM core_partner_events end WHERE end.run_id=e.run_id
              AND json_extract(end.data,'$.native.params.sessionId')=?
              AND json_extract(end.data,'$.native.params.event.type')='turn/end'
              ORDER BY end.seq DESC LIMIT 1)
        ORDER BY e.seq DESC"""
    if event_type == 'assistant/message':
        query += ' LIMIT 1'
    for row in db.execute(query, (run_id, attempt['id'], attempt['task_id'],
                          attempt.get('session_id'), event_type, event_type, attempt.get('session_id'))):
        if row[0]:
            yield json.loads(row[0])


def delivered_answer(db, attempt):
    answer = (attempt.get('result') or {}).get('answer') or ''
    if answer:
        return answer
    event = next(_deepseek_turn_events(db, attempt, 'assistant/message'), {})
    message = event.get('message', {})
    return '\n'.join(b['text'] for b in message.get('content', []) if isinstance(b, dict)
                      and b.get('type') == 'text' and isinstance(b.get('text'), str))


def delivered_files(db, task, attempt, answer):
    # Prefer the native presentation receipt. Where the endpoint has none,
    # accept only explicit filename links in its final answer, never a directory listing.
    presented = [f.get('path') for event in _deepseek_turn_events(db, attempt, 'deliverables/presented')
                 for f in event.get('files', []) if isinstance(f, dict)]
    paths = presented or _answer_file_links(answer)
    root = Path(attempt['workspace'])
    ended = (attempt.get('result') or {}).get('ended_at') or 0
    names = []
    matched = set()
    for path in paths:
        if not isinstance(path, str) or not path or len(path) > 1024 or path.startswith(('\\\\', '//')):
            continue
        try:
            target = Path(path)
            if target.drive and not target.is_absolute():
                continue
            name = workspace.relative((target.relative_to(root) if target.is_absolute() else target).as_posix())
            parts = [part.casefold() for part in Path(name).parts]
            if (any(part.startswith('.') or part in workspace.SKIP_DIRS for part in parts)
                    or 'workdesk-inputs' in parts or name in task['spec'].get('materials', {})):
                continue
            file = workspace.io_path(workspace.plain(root / name, root))
            if file.is_file() and attempt['started'] <= file.stat().st_mtime <= ended:
                matched.add(path)
                if name not in names:
                    names.append(name)
        except (ValueError, OSError):
            continue
    return names, any(not isinstance(path, str) or path not in matched for path in presented)


def _answer_file_links(answer):
    # A filename as the visible label distinguishes file delivery from prose,
    # filenames mentioned in code blocks, and links to general documentation.
    fenced = None
    pattern = r'(?<!!)\[([^\]\n]{1,240})\]\((?:<([^<>\n]{1,1024})>|([^()\n]{1,1024}))\)'
    for line in answer.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(('```', '~~~')):
            marker = stripped[:3]
            if fenced is None or marker == fenced:
                fenced = None if fenced else marker
            continue
        if fenced:
            continue
        for match in re.finditer(pattern, line):
            path = unquote(match[2] or match[3])
            if Path(path).name == match[1] and Path(path).suffix:
                yield path


def preview_file(folder, name, expected):
    value = workspace.read_blob(folder, name)
    raw = value.pop('raw')
    if any(value[k] != expected[k] for k in ('sha256', 'bytes')):
        raise ValueError('保存的成果文件已变化，不能作为原成果预览。')
    value.update(file=name, kind='binary', data=base64.b64encode(raw).decode(), no_dispatch=True)
    if raw.startswith(b'%PDF-'):
        value.update(kind='pdf', mime='application/pdf')
    else:
        mime = ('image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else
                'image/jpeg' if raw.startswith(b'\xff\xd8') else
                'image/gif' if raw.startswith((b'GIF87a', b'GIF89a')) else
                'image/webp' if raw.startswith(b'RIFF') and raw[8:12] == b'WEBP' else None)
        if mime:
            value.update(kind='image', image='data:' + mime + ';base64,' + value['data'])
        elif b'\0' not in raw and len(raw) <= workspace.MAX_FILE:
            try:
                value.update(kind='text', content=raw.decode('utf8'))
            except UnicodeError:
                pass
    return value


class Results:
    def __init__(self,service):self.s=service;self.c=service.collaboration
    def handlers(self):return {'result.capture':self.capture,'result.verify':self.verify,'result.decide':self.decide,'result.task-complete':self.complete_task}

    def complete_task(self,db,p):
        row=self.checked(db,p)
        task=self.s.store.task(row['task_id'],db)
        check=self.inspect(db,row)
        criteria=p.get('criteria')
        if task['spec'].get('work_mode')!='independent' or check['state']!='CURRENT' or p.get('confirm') is not True or not isinstance(criteria,str) or not criteria.strip() or len(criteria)>4000:
            raise ValueError('请复核本回合成果并确认完成依据。')
        if (row.get('decision') or {}).get('value')=='return':
            raise ValueError('该版本已退回，请先修订。')
        required=task['spec'].get('deliverable_files',[])
        if any(name not in row['snapshot']['manifest'] for name in required):
            raise ValueError('此快照缺少声明交付文件。')
        completion={'task_id':row['task_id'],'attempt_id':row['attempt_id'],'result_id':row['id'],'snapshot_hash':row['snapshot_hash'],'criteria':criteria.strip(),'state':'COMPLETED'}
        self.s.store.event(row['task_id'],row['attempt_id'],'result.task-complete',{'receipt':completion},db)
        return completion

    def catalog(self,task_id=None):
        with self.s.store.read() as db:
            rows=self.c.all(db,'work_result')
            if task_id:rows=[r for r in rows if r['task_id']==task_id]
            tasks=[dict(r) for r in db.execute("SELECT t.id,coalesce(json_extract(d.data,'$.title'),t.title) AS title,t.state,t.project_id,json_extract(t.spec,'$.runtime_mode') AS runtime_mode FROM tasks t LEFT JOIN collaboration d ON d.kind='display' AND d.id='display:task:'||t.id WHERE coalesce(json_extract(t.spec,'$.interaction'),'')!='chat' AND coalesce(json_extract(d.data,'$.deleted'),0)=0 ORDER BY t.created DESC")]
        return {'results':rows,'tasks':tasks,'no_dispatch':True,'acceptance':'EXPLICIT_HUMAN_DECISION_ONLY'}

    def capture_delivered(self,db,task,attempt):
        names=task['spec'].get('deliverable_files',[])
        try:
            if not names:
                answer=delivered_answer(db,attempt)
                names,missing=delivered_files(db,task,attempt,answer)
                if missing:
                    self.s.store.event(task['id'],attempt['id'],'work_result_capture_pending',
                                       {'files':names,'reason':'工作端已报告交付，但部分文件未能在本回合的工作目录中核实。'},db)
                if not names and not answer:return
            self.capture(db,{'task_id':task['id'],'attempt_id':attempt['id'],'owner':attempt['owner'],'files':names},finishing=True)
        except (ValueError,OSError) as exc:
            self.s.store.event(task['id'],attempt['id'],'work_result_capture_pending',{'files':names,'reason':str(exc)},db)

    def capture(self,db,p,*,finishing=False):
        task,attempt=self.s._latest(db,p);self.s.work_records.guard(db,'task',task['id'])
        if task['spec'].get('runtime_mode')!='official':raise ValueError('有限任务继续使用原候选、检查和整合流程。')
        if not attempt or attempt['state']!='COMPLETED' or not self.s.operations.exit_proved(attempt):raise ValueError('请等待回合正常完成并确认本地退出，再保存成果。')
        if not finishing and self.s.workers.get(attempt['id']) and self.s.workers[attempt['id']]['thread'].is_alive():raise ValueError('工作端仍在收尾。')
        if self.s.store.usage(task=task['id'],db=db)['unknown_requests']:raise ValueError('仍有未知请求，先核对。')
        names=p.get('files',[])
        if not isinstance(names,list) or any(not isinstance(n,str) for n in names):raise ValueError('请选择成果文件清单。')
        root=Path(attempt['workspace']);files,_=workspace.select_files(root,names,self.s.locations.data,directories=not finishing)
        manifest=workspace.current_manifest(root,files)
        if finishing and set(manifest)!=set(names):raise ValueError('未取得全部交付文件。')
        if sum(f['bytes'] for f in manifest.values())>workspace.MAX_ATTACHMENT_TOTAL:raise ValueError('成果文件合计超过32MB，请减少选择。')
        answer=delivered_answer(db,attempt)
        if not manifest and not answer:raise ValueError('没有可保存的回复或成果文件。')
        identity='result:'+digest({'task':task['id'],'attempt':attempt['id'],'manifest':manifest,'answer':answer,'spec':task['spec_hash']})
        existing=db.execute("SELECT data FROM collaboration WHERE kind='work_result' AND id=?",(identity,)).fetchone()
        if existing:return {'result':json.loads(existing[0]),'existing':True,'no_dispatch':True}
        folder=self.s.locations.data/'results'/identity.split(':')[1]/'files'
        folder.parent.mkdir(parents=True,exist_ok=True)
        if folder.exists():
            if workspace.current_manifest(folder,manifest)!=manifest:raise ValueError('已保存成果文件发生变化，请先检查。')
        else:
            with tempfile.TemporaryDirectory(dir=folder.parent,prefix='capture-') as temporary:
                staging=Path(temporary)/'files'
                workspace.copy_manifest(root,staging,manifest)
                os.replace(staging,folder)
        snapshot={'task_id':task['id'],'attempt_id':attempt['id'],'spec_hash':task['spec_hash'],'manifest':manifest,'answer':answer,
                  'baseline_manifest':{name:value for name,value in task['spec'].get('materials',{}).items() if name in manifest},
                  'configuration':{k:task['spec'].get(k) for k in ('provider','model','effort','tier','work_scene','access')},
                  'native_checks':(attempt['result'] or {}).get('validation'),'independent_review':'NOT_RECORDED'}
        row={'id':identity,'revision':uid(),'title':task['title'],'task_id':task['id'],'attempt_id':attempt['id'],
             'snapshot':snapshot,'snapshot_hash':digest(snapshot),'folder':str(folder),'created':time.time(),
             'verification':None,'decision':None,'integration':'ALREADY_EXECUTED_IN_PROJECT; NO_ADDITIONAL_WRITE'}
        self.c.put(db,'work_result',row)
        self.s.store.event(task['id'],attempt['id'],'work_result_captured',{'result_id':identity,'snapshot_hash':row['snapshot_hash'],'files':manifest,'human_acceptance':False},db)
        return {'result':row,'no_dispatch':True,'project_files_written':False}

    def preview(self,identity,name,snapshot_hash):
        with self.s.store.read() as db:row=self.c.get(db,'work_result',identity)
        if row['snapshot_hash']!=snapshot_hash or digest(row['snapshot'])!=snapshot_hash:raise ValueError('成果快照版本不一致。')
        expected=row['snapshot']['manifest'].get(name)
        if not expected:raise ValueError('文件不属于该成果快照。')
        value=preview_file(self.s.locations.data/'results'/row['id'].split(':')[-1]/'files',name,expected)
        value['baseline']='NOT_SELECTED_OR_UNAVAILABLE'
        baseline=row['snapshot'].get('baseline_manifest',{}).get(name)
        if baseline:
            try:
                before=workspace.read_blob(self.s.locations.data/'tasks'/row['task_id']/'input',name)
                if any(before[k]!=baseline[k] for k in ('sha256','bytes')):raise ValueError()
                value['baseline']='UNCHANGED' if before['sha256']==value['sha256'] else 'CHANGED'
                if value['kind']=='text' and len(before['raw'])<=workspace.MAX_FILE and b'\0' not in before['raw']:
                    lines=list(difflib.unified_diff(before['raw'].decode('utf8').splitlines(),value['content'].splitlines(),fromfile='开始前材料',tofile='成果快照',lineterm=''))
                    value.update(difference='\n'.join(lines[:200]),diff_truncated=len(lines)>200)
            except (ValueError,OSError,UnicodeError):pass
        return value

    def checked(self,db,p):
        row=self.c.get(db,'work_result',p.get('id'))
        if p.get('revision')!=row['revision'] or p.get('snapshot_hash')!=row['snapshot_hash']:raise ValueError('成果版本已变化，请刷新后确认。')
        return row

    def inspect(self,db,row):
        snap=row['snapshot'];task=self.s.store.task(row['task_id'],db)
        self.s.work_records.guard(db,'task',task['id'])
        latest=db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started DESC LIMIT 1',(task['id'],)).fetchone()
        latest=self.s.store.attempt_row(latest) if latest else None
        problems=[]
        if digest(snap)!=row['snapshot_hash']:problems.append('成果快照内容已变化')
        if not latest or latest['id']!=row['attempt_id'] or latest['state']!='COMPLETED':problems.append('任务已进入其它回合或回合未正常完成')
        if task['spec_hash']!=snap['spec_hash']:problems.append('任务配置已变化')
        try:
            if workspace.current_manifest(self.s.locations.data/'results'/row['id'].split(':')[-1]/'files',snap['manifest'])!=snap['manifest']:problems.append('保存的成果文件已变化')
            if latest and workspace.current_manifest(latest['workspace'],snap['manifest'])!=snap['manifest']:problems.append('项目中的成果文件已变化')
        except (ValueError,OSError) as exc:problems.append(str(exc))
        return {'state':'STALE' if problems else 'CURRENT','problems':problems,'checked_at':time.time(),'no_model_call':True,
                'meaning':'版本与文件字节核对，不是质量评审或命令检查通过'}

    def verify(self,db,p):
        row=self.checked(db,p);row['verification']=self.inspect(db,row);row['revision']=uid();self.c.put(db,'work_result',row)
        return {'result':row,'no_dispatch':True}

    def decide(self,db,p):
        row=self.checked(db,p);check=self.inspect(db,row)
        if check['state']!='CURRENT':raise ValueError('成果版本已失效：'+'；'.join(check['problems']))
        if p.get('decision') not in {'accept','return'} or p.get('confirm') is not True:raise ValueError('请明确接受或退回此版本。')
        note=p.get('note','')
        if not isinstance(note,str) or len(note)>4000:raise ValueError('验收说明最多4000字。')
        decision={'actor':'HUMAN','command_id':self.s.command_identity,'value':p['decision'],'note':note,'snapshot_hash':row['snapshot_hash'],'at':time.time(),
                  'independent_review_replaced':False,'model_dispatched':False}
        self.c.put(db,'result_decision',{'id':uid(),'result_id':row['id'],**decision})
        row.update(decision=decision,verification=check,revision=uid());self.c.put(db,'work_result',row)
        self.s.store.event(row['task_id'],row['attempt_id'],'work_result_human_decision',{'result_id':row['id'],**decision},db)
        return {'result':row,'no_dispatch':True}
