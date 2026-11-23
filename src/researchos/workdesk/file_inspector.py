"""Local file previews and version-bound selections; native Work owns processing."""
import base64
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

    def send(self,db,p):
        payload=dict(p);reference=payload.pop('file_selection',None);expected=None
        if reference:
            if payload.get('task_id'):raise ValueError('已有回合的材料清单沿用原配置。')
            row,expected=self.checked(db,reference)
            if row['project_id']!=payload.get('project_id'):raise ValueError('所选材料属于另一项目。')
        result=self.s.scenes.send(db,payload)
        if expected is not None:
            task=self.s.store.task(result['task_id'],db)
            if task['spec']['materials']!=expected:raise ValueError('发送材料与已保存选择不一致；未派发。')
            self.s.store.event(task['id'],result['launch_attempt'],'file_selection_delivery_verified',{'selection_id':reference['id'],'revision':reference['revision'],'manifest':expected,'model_comprehension':'NOT_ATTESTED'},db)
        return result
