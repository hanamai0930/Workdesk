"""Project registration and relocation metadata, never moving a user's folder."""
from pathlib import Path
import time

from . import workspace
from .store import digest,uid


class Projects:
    def __init__(self,service):self.s=service;self.c=service.collaboration
    def handlers(self):return {'project.location-prepare':self.prepare,'project.location-commit':self.commit}
    def known(self,db,identity):
        files={}
        for row in db.execute('SELECT spec FROM tasks WHERE project_id=? ORDER BY created',(identity,)):
            import json
            files.update(json.loads(row[0]).get('materials',{}))
        for row in sorted(self.c.all(db,'file_selection'),key=lambda r:r['created']):
            if row['project_id']==identity:files.update({f['file']:{k:f[k] for k in ('sha256','bytes')} for f in row['selected']})
        for row in sorted(self.c.all(db,'work_result'),key=lambda r:r['created']):
            task=self.s.store.task(row['task_id'],db)
            if task['project_id']==identity:files.update(row['snapshot']['manifest'])
        return files
    def catalog(self):
        with self.s.store.read() as db:
            projects=self.s.work_records.projects(db);records=self.s.work_records.catalog(db,True)['records'];drafts=self.c.all(db,'ui_draft')
            for p in projects:
                p['identity_files']=sorted(self.known(db,p['id']))
                if Path(p['root']).is_dir():
                    try:
                        p['identity_files']=sorted(set(p['identity_files'])|{f['file'] for f in self.s.ui_features.files(p['id'],db=db)['files'] if not f['directory']})
                        p['file_listing_status']='AVAILABLE'
                    except (ValueError,OSError) as exc:
                        p['file_listing_status']='UNAVAILABLE';p['file_listing_error']=str(exc)
                p['goals']=sum(g['project_id']==p['id'] and g['state']!='CANCELLED' for g in self.c.all(db,'goal_job'))
                p['exists']=Path(p['root']).is_dir();p['records']=sum(p['id'] in {r.get('project'),r.get('execution_project')} for r in records)
                p['drafts']=sum(d['value'].get('project')==p['id'] for d in drafts)
                p['work_items']=db.execute('SELECT COUNT(*) FROM work_items WHERE project_id=? AND '+
                    self.s.work_records.visible('item','work_items.id'),(p['id'],)).fetchone()[0]
                p['project_conversations']=db.execute("SELECT COUNT(*) FROM record_scopes s JOIN collaboration c ON c.kind='ui_chat' AND c.id=s.record_id WHERE s.kind='chat' AND s.project_id=?",(p['id'],)).fetchone()[0]
            return {'projects':projects,'relocations':self.c.all(db,'project_location'),'no_dispatch':True,'project_files_moved':False}
    def prepare(self,db,p):
        self.s.persistence.idle(db);project=next((x for x in self.s.work_records.projects(db) if x['id']==p.get('project_id')),None)
        if not project or project['revision']!=p.get('revision'):raise ValueError('项目版本已变化。')
        new=Path(p.get('new_root',''))
        if not new.is_absolute() or not new.is_dir():raise ValueError('请选择已经存在的新项目文件夹。')
        workspace.plain(new,new)
        if db.execute('SELECT 1 FROM projects WHERE root=? AND id!=?',(str(new),project['id'])).fetchone():raise ValueError('文件夹已属于其它项目。')
        names=set(p.get('files',[]))
        if not names:raise ValueError('请选择至少一个用于核对项目身份的文件。')
        for name in names:workspace.relative(name)
        old_available=Path(project['root']).is_dir()
        before=workspace.current_manifest(project['root'],names) if old_available else {n:v for n,v in self.known(db,project['id']).items() if n in names}
        after=workspace.current_manifest(new,names)
        if not before or len(before)!=len(names) or before!=after:raise ValueError('两个文件夹的所选文件不一致或缺失。')
        row={'id':uid(),'project_id':project['id'],'revision':project['revision'],'old_root':project['root'],'new_root':str(new),
             'manifest':before,'old_available':old_available,'created':time.time(),'state':'PREPARED','scope':'EXPLICIT_SELECTED_FILES_ONLY'}
        row['preview_hash']=digest(row);self.c.put(db,'project_location',row);return {'preview':row,'no_dispatch':True}
    def commit(self,db,p):
        self.s.persistence.idle(db);row=self.c.get(db,'project_location',p.get('id'))
        if row['state']!='PREPARED' or row['preview_hash']!=p.get('preview_hash') or p.get('confirm') is not True:raise ValueError('请确认当前目录核对预览。')
        project=next((x for x in self.s.work_records.projects(db) if x['id']==row['project_id']),None)
        if not project or project['revision']!=row['revision']:raise ValueError('项目版本已改变。')
        if not row['old_available'] and {n:v for n,v in self.known(db,row['project_id']).items() if n in row['manifest']}!=row['manifest']:raise ValueError('已保存的文件版本已变化。')
        for root in ([row['old_root'],row['new_root']] if row['old_available'] else [row['new_root']]):
            if workspace.current_manifest(root,row['manifest'])!=row['manifest']:raise ValueError('核对期间项目文件已变化。')
        db.execute('UPDATE projects SET root=? WHERE id=?',(row['new_root'],row['project_id']))
        db.execute('UPDATE tasks SET grant=NULL WHERE project_id=?',(row['project_id'],))
        row.update(state='COMMITTED',committed=time.time());self.c.put(db,'project_location',row)
        self.s.store.event(None,None,'project_location_changed',row,db)
        return {'project_id':row['project_id'],'project_files_moved':False,'historical_attempt_paths_unchanged':True,'no_dispatch':True}
