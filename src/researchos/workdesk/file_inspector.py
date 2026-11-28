"""Local file previews and version-bound selections; native Work owns processing."""
import base64
import json
from pathlib import Path
import time

from . import workspace
from .store import digest,uid


class FileInspector:
    def __init__(self,service):self.s=service;self.c=service.collaboration
    def handlers(self):return {'file.selection-save':self.save,'file.selection-draft':self.draft}

    def project(self,db,identity):
        row=db.execute('SELECT * FROM projects WHERE id=?',(identity,)).fetchone()
        if not row:raise ValueError('项目不存在。')
        self.s.work_records.guard_project(db,identity);return row

    def inspect(self,project,name):
        with self.s.store.read() as db:p=self.project(db,project)
        workspace.select_files(p['root'],[name],self.s.locations.data)
        record=workspace.read_blob(p['root'],name);raw=record.pop('raw');suffix=Path(name).suffix.lower()
        value={**record,'file':name,'project_id':project,'no_dispatch':True,'processing':'SELECTED_OFFICIAL_RUNTIME','comprehension':'NOT_ATTESTED'}
        if suffix=='.pdf' and raw.startswith(b'%PDF-'):
            value.update(kind='pdf',mime='application/pdf',data=base64.b64encode(raw).decode())
        elif suffix in {'.png','.jpg','.jpeg','.gif','.webp'}:
            preview=self.s.ui_features.preview(project,name);value.update(kind='image' if preview.get('image') else 'binary',image=preview.get('image'))
        else:
            try:
                if b'\0' in raw or len(raw)>workspace.MAX_FILE:raise UnicodeError()
                value.update(kind='text',content=raw.decode('utf8'))
            except UnicodeError:value.update(kind='binary',content='保留原文件，由所选官方工作端按支持的格式处理。')
        return value

    def selections(self,project):
        with self.s.store.read() as db:return {'selections':[r for r in self.c.all(db,'file_selection') if r['project_id']==project],'no_dispatch':True}

    def save(self,db,p):
        self.project(db,p.get('project_id'))
        chosen=self.s.ui_features.files_select(db,{'project_id':p.get('project_id'),'files':p.get('files')})
        if not chosen['selected']:raise ValueError('请选择至少一个文件。')
        row={'id':'file-selection:'+uid(),'revision':uid(),'project_id':p['project_id'],'created':time.time(),**chosen}
        self.c.put(db,'file_selection',row)
        return {'selection':row,'no_dispatch':True}

    def checked(self,db,p):
        row=self.c.get(db,'file_selection',p.get('id'))
        if p.get('revision')!=row['revision']:raise ValueError('材料选择版本已改变。')
        project=self.project(db,row['project_id'])
        current=workspace.current_manifest(project['root'],[f['file'] for f in row['selected']])
        expected={f['file']:{k:f[k] for k in ('sha256','bytes')} for f in row['selected']}
        if current!=expected:raise ValueError('选定材料已变化或缺失，请重新核对并保存选择。')
        return row,expected

    def draft(self,db,p):
        row,_=self.checked(db,p);identity='file-draft-'+uid()
        reference={k:row[k] for k in ('id','revision')};reference['files']=[f['file'] for f in row['selected']]
        value={'mode':'work','type':'independent','project':row['project_id'],'files':'\n'.join(row['files']),
               'attachments':row['attachments'],'input':'','title':'选定文件的新工作','formDraft':{'fileSelection':reference}}
        draft=self.s.ui_features.draft_save(db,{'id':identity,'value':value})['draft']
        return {'draft':draft,'no_dispatch':True,'selection':reference}

    def delivery_selection(self, db, reference, project, files):
        row = self.c.get(db, 'file_selection', reference.get('id'))
        if row['revision'] != reference.get('revision') or row['project_id'] != project:
            raise ValueError('所选材料的版本或项目已变化，请重新选择。')
        original = {item['file']: item for item in row['selected']}
        if not isinstance(files, list) or not all(isinstance(name, str) for name in files):
            raise ValueError('材料清单无效。')
        if set(files) == set(original):
            return self.checked(db, reference)
        if not row.get('chat_source'):
            raise ValueError('发送材料与已保存选择不一致。')
        chosen = self.s.ui_features.files_select(db, {'project_id': project, 'files': files})
        for item in chosen['selected']:
            previous = original.get(item['file'])
            if previous:
                if (previous['bytes'], previous['sha256']) != (item['bytes'], item['sha256']):
                    raise ValueError('保留的聊天材料已变化，请从原聊天重新选择。')
                item.update(previous)
        revised = {**chosen, 'id': 'file-selection:' + uid(), 'revision': uid(), 'created': time.time(),
                   'project_id': project, 'chat_source': row['chat_source'],
                   'source_selection': {'id': row['id'], 'revision': row['revision']}}
        self.c.put(db, 'file_selection', revised)
        expected = {item['file']: {k: item[k] for k in ('bytes','sha256')} for item in revised['selected']}
        return revised, expected

    def send(self,db,p):
        payload=dict(p);reference=payload.pop('file_selection',None);expected=None
        if reference:
            if payload.get('task_id'):raise ValueError('已有回合的材料清单沿用原配置。')
            texts, attachments = payload.get('files',[]), payload.get('attachments',[])
            if not isinstance(texts,list) or not isinstance(attachments,list) or any(
                    not isinstance(item,dict) or not isinstance(item.get('file'),str) for item in attachments):
                raise ValueError('材料清单无效。')
            files=texts+[item['file'] for item in attachments]
            selection,expected=self.delivery_selection(db,reference,payload.get('project_id'),files)
        feedback=payload.pop('feedback_command_id',None)
        guidance=payload.pop('guidance_command_id',None)
        snapshot=payload.pop('feedback_snapshot',None)
        source={}
        if snapshot:
            if feedback or not isinstance(snapshot,dict):raise ValueError('请一次选择一个退修版本。')
            result=self.s.results.checked(db,snapshot)
            decision=result.get('decision') or {}
            if (result['task_id']!=payload.get('task_id') or result['attempt_id']!=payload.get('attempt_id')
                or decision.get('value')!='return' or decision.get('snapshot_hash')!=result['snapshot_hash']
                or self.s.results.inspect(db,result)['state']!='CURRENT'):
                raise ValueError('原任务、退修决定或快照已变化，请重新核对。')
            source={'feedback_snapshot':{**{k:result[k] for k in ('id','revision','snapshot_hash')},'decision_command_id':decision.get('command_id')}}
            payload['goal']=payload.get('goal','')+'\n退修原成果 '+result['id']+'，版本 '+result['snapshot_hash']+'：'+(decision.get('note') or '请复核并修订所交成果。')
        if feedback or guidance:
            task_id,attempt_id=payload.get('task_id'),payload.get('attempt_id')
            if not task_id or not attempt_id:
                raise ValueError('请选择原任务与尝试。')
            if feedback:
                row=db.execute("SELECT receipt FROM commands WHERE id=? AND kind='tool.decide'",(feedback,)).fetchone()
                decision=json.loads(row[0]) if row else {}
                jobrow=db.execute('SELECT data FROM core_tool_jobs WHERE id=?',(decision.get('invocation_id'),)).fetchone()
                job=json.loads(jobrow[0]) if jobrow else {}
                if job.get('task_id')!=task_id or job.get('attempt_id')!=attempt_id or decision.get('decision')!='revise':
                    raise ValueError('退修意见与原任务版本不符。')
                selected=[d for d in job.get('decisions',[]) if d['path']==decision['path'] and d['sha256']==decision['sha256']]
                if not selected or selected[-1]['command_id']!=feedback:
                    raise ValueError('该成果已有新的验收决定，请重新选择。')
                index=next((i for i,f in enumerate(job['artifacts']) if (f['path']==decision['path'] or decision.get('relative_path') and f['relative_path']==decision['relative_path']) and f['sha256']==decision['sha256']),None)
                if index is None:raise ValueError('退修版本已无法定位。')
                source={'feedback_command_id':feedback,'input_version':{'kind':'job','source':job['id'],'entry':str(index),'sha256':decision['sha256']}}
                payload['goal']=payload.get('goal','')+'\n退修意见：'+decision['note']+'\n请以此准确版本作为文档工具 input_files 的输入：'+json.dumps([source['input_version']],ensure_ascii=False)
            if guidance:
                row=db.execute("SELECT json_extract(payload,'$.receipt') FROM events WHERE task_id=? AND attempt_id=? AND kind='work.guidance' ORDER BY seq DESC LIMIT 1",(task_id,attempt_id)).fetchone()
                draft=json.loads(row[0]) if row else {}
                if draft.get('command_id')!=guidance or not draft.get('note'):
                    raise ValueError('补充要求已变化，请刷新。')
                payload['goal']=payload.get('goal','')+'\n'+draft['note']
                source['guidance_command_id']=guidance
        result=self.s.scenes.send(db,payload)
        if source:
            self.s.store.event(result['task_id'],result['launch_attempt'],'single_task_continued',{'previous_attempt_id':payload['attempt_id'],'user_message':p.get('goal',''),**source},db)
        if expected is not None:
            task=self.s.store.task(result['task_id'],db)
            materials=task['spec']['materials']
            if materials!=expected:raise ValueError('发送材料与已保存选择不一致；请返回原材料重新选择。')
            self.s.store.event(task['id'],result['launch_attempt'],'file_selection_delivery_verified',{'selection_id':selection['id'],'revision':selection['revision'],'manifest':expected,**{k:selection[k] for k in ('chat_source','source_selection') if k in selection},'model_comprehension':'NOT_ATTESTED'},db)
        return result
