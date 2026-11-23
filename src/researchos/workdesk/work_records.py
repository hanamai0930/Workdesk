"""Work record organization is separate from frozen execution specifications.

Deletion removes the conversation entry and its drafts, retaining execution
evidence and accounting used by plans, reviews and cumulative budgets.
"""
import json
import time
from .contracts import bounded_text
from .store import ACTIVE, UNKNOWN, digest
from .workbench import SCENES


class WorkRecords:
    def __init__(self,service):self.s=service;self.c=service.collaboration

    @staticmethod
    def visible(kind, identity_column):
        return (f"NOT EXISTS(SELECT 1 FROM collaboration AS removed WHERE removed.kind='display' "
                f"AND removed.id='display:{kind}:'||{identity_column} AND json_extract(removed.data,'$.deleted'))")

    def display(self,db,kind,identity):
        row=db.execute("SELECT data FROM collaboration WHERE kind='display' AND id=?",('display:'+kind+':'+identity,)).fetchone()
        return json.loads(row[0]) if row else {}

    def guard(self,db,kind,identity):
        if not isinstance(identity,str):raise ValueError('Work记录身份无效。')
        if self.display(db,kind,identity).get('deleted'):
            raise ValueError('该Work记录已删除；原执行证据保留，不能重新发送或写入草稿。')

    def project_meta(self,db,identity):
        row=db.execute("SELECT data FROM collaboration WHERE kind='work_project' AND id=?",('work-project:'+identity,)).fetchone()
        return json.loads(row[0]) if row else {}

    def projects(self,db):
        result=[]
        for row in db.execute('SELECT * FROM projects ORDER BY created DESC'):
            project=dict(row);meta=self.project_meta(db,project['id'])
            if meta.get('removed'):continue
            result.append({**project,'scene':meta.get('scene'),'pinned':meta.get('pinned',False),
                           'revision':digest({'project':project,'display':meta})})
        return result

    def guard_project(self,db,identity):
        if self.project_meta(db,identity).get('removed'):raise ValueError('该工作区登记已删除，请重新选择文件夹添加项目。')

    def save_project_meta(self,db,identity,changes):
        self.c.put(db,'work_project',{**self.project_meta(db,identity),'id':'work-project:'+identity,
                   'project_id':identity,**changes})

    def task_rows(self,db,identity=None,all_rows=False):
        fields=('interaction','work_scene','work_mode','provider','model','cooperative')
        columns=','.join("json_extract(spec,'$."+key+"') AS "+key for key in fields)
        query='SELECT id,project_id,title,state,created,updated,spec_hash,'+columns+",(SELECT MAX(seq) FROM events WHERE task_id=tasks.id) AS cursor FROM tasks"
        return [dict(r) for r in db.execute(query+(' WHERE id=?' if identity else " WHERE coalesce(json_extract(spec,'$.interaction'),'')!='chat' ORDER BY created DESC"+('' if all_rows else ' LIMIT 200')),(identity,) if identity else ())]

    def view(self,db,kind,source,display=None):
        display=self.display(db,kind,source['id']) if display is None else display
        original_scene=source.get('work_scene');original_project=source['project_id']
        scene=display.get('scene',original_scene);project=display.get('project',original_project)
        scope=db.execute('SELECT project_id FROM record_scopes WHERE kind=? AND record_id=?',(kind,source['id'])).fetchone()
        if scope:project=scope[0]
        meta=self.project_meta(db,project) if project else {}
        group_scene=scene if scene in SCENES else ('general' if 'scene' in meta and meta['scene'] is None else 'legacy')
        return {'id':source['id'],'kind':kind,'title':display.get('title') or source['title'],
                'scene':scene,'project':project,'group_scene':group_scene,
                'execution_scene':original_scene,'execution_project':original_project,
                'execution_only':kind=='task' and (source.get('work_mode')=='cooperative' or bool(source.get('cooperative'))),
                'state':source['state'] if kind=='task' else ('RUNNING' if source.get('pending') else 'DISCUSSION'),
                'created':source['created'],'pinned':display.get('pinned',False),'archived':display.get('archived',False),
                'deleted':display.get('deleted',False),'revision':digest({'kind':kind,'source':source,'display':display})}

    def catalog(self,db,all_records=False,sidebar=False):
        dialogues=self.c.all(db,'dialogue')
        owned={m['task_id'] for d in dialogues for m in d.get('messages',[]) if m.get('task_id')}
        owned.update(d['pending'] for d in dialogues if d.get('pending'))
        displays={(d.get('kind'),d.get('record_id')):d for d in self.c.all(db,'display')}
        rows=[self.view(db,'task',t,displays.get(('task',t['id']),{})) for t in self.task_rows(db,all_rows=all_records)
              if t['id'] not in owned and t.get('interaction')!='chat']
        rows.extend(self.view(db,'dialogue',d,displays.get(('dialogue',d['id']),{})) for d in dialogues)
        rows=[r for r in rows if not r['deleted']]
        rows.sort(key=lambda r:(-int(r['pinned']),-r['created'],r['id']))
        scopes=set() if all_records else {tuple(row) for row in db.execute(
            "SELECT kind,record_id FROM record_scopes WHERE kind IN ('task','dialogue') AND project_id IS NOT NULL")}
        result={'records':[r for r in rows if (r['kind'],r['id']) not in scopes],
                'projects':self.projects(db),'no_dispatch':True,'deletion_mode':'RECORD_ONLY_EXECUTION_AUDIT_RETAINED'}
        if sidebar:
            result['sidebar_records']=[r for r in rows if not r['archived'] and not r['execution_only']]
        return result

    def source(self,db,kind,identity):
        if kind=='task':
            rows=self.task_rows(db,identity)
            if not rows:raise ValueError('Work记录不存在，请刷新列表。')
            if rows[0].get('interaction')=='chat':raise ValueError('Chat记录请使用Chat自己的整理入口。')
            return rows[0]
        if kind=='dialogue':return self.c.get(db,'dialogue',identity)
        raise ValueError('Work记录类型无效。')

    def checked(self,db,p,allow_deleted=False):
        rows=p.get('records')
        if not isinstance(rows,list) or not 1<=len(rows)<=1000:raise ValueError('请选择1至1000条Work记录。')
        result=[];seen=set()
        for expected in rows:
            if not isinstance(expected,dict) or set(expected)!={'kind','id','revision'} or not all(isinstance(expected[k],str) for k in expected):
                raise ValueError('Work记录身份或修订号无效。')
            kind,identity=expected['kind'],expected['id'];key=(kind,identity)
            if key in seen:raise ValueError('选择清单中有重复记录。')
            seen.add(key);source=self.source(db,kind,identity);view=self.view(db,kind,source)
            if view['deleted']:
                if allow_deleted:continue
                raise ValueError('所选Work记录已删除，请刷新列表。')
            if expected['revision']!=view['revision']:raise ValueError('所选记录在确认期间已变化，请刷新后重新选择。')
            result.append((kind,source,view))
        return result

    def update_display(self,db,kind,identity,changes):
        previous=self.display(db,kind,identity)
        row={**previous,'id':'display:'+kind+':'+identity,'kind':kind,'record_id':identity,
             **changes,'revision':previous.get('revision',0)+1,'updated':time.time()}
        self.c.put(db,'display',row)

    def organize(self,db,p):
        if set(p)-{'records','changes'}:raise ValueError('记录整理字段不支持。')
        changes=p.get('changes')
        if not isinstance(changes,dict) or not changes or set(changes)-{'title','pinned','scene','project'}:
            raise ValueError('请选择名称、置顶、场景或项目归属。')
        values=dict(changes)
        if 'title' in values:values['title']=bounded_text(values['title'],'记录名称',160)
        if 'pinned' in values and type(values['pinned']) is not bool:raise ValueError('置顶设置须为布尔值。')
        if 'scene' in values and values['scene'] is not None and (not isinstance(values['scene'],str) or values['scene'] not in SCENES):raise ValueError('目标场景无效。')
        if 'project' in values and values['project'] is not None and (not isinstance(values['project'],str) or not db.execute('SELECT 1 FROM projects WHERE id=?',(values['project'],)).fetchone()):
            raise ValueError('目标项目不存在。')
        if values.get('project'):self.guard_project(db,values['project'])
        chosen=self.checked(db,p)
        if 'title' in values and len(chosen)!=1:raise ValueError('请逐条重命名Work记录。')
        for kind,source,view in chosen:
            self.update_display(db,kind,source['id'],values)
            if 'project' in values:db.execute('INSERT INTO record_scopes VALUES(?,?,?) ON CONFLICT(kind,record_id) DO UPDATE SET project_id=excluded.project_id',
                (kind,source['id'],values['project']))
        return {'updated':[{'kind':kind,'id':source['id']} for kind,source,view in chosen],
                'no_dispatch':True,'execution_specifications_unchanged':True,'project_files_unchanged':True}

    def delete(self,db,p):
        if set(p)-{'records','confirm','mode','selection_count'} or p.get('confirm') is not True:raise ValueError('请确认删除所选Work记录。')
        mode=p.get('mode','single');expected=p.get('records')
        if not isinstance(expected,list):raise ValueError('请提供准确的Work记录清单。')
        if mode=='single':
            if len(expected)!=1:raise ValueError('单条删除只能删除1条记录；批量删除须先进入多选。')
        elif mode=='selected':
            if type(p.get('selection_count')) is not int or p['selection_count']!=len(expected):
                raise ValueError('批量删除须明确确认已选条数，未删除任何记录。')
        else:raise ValueError('删除方式无效。')
        chosen=self.checked(db,p,allow_deleted=True)
        # Check the complete frozen batch before changing any entry.
        for kind,source,view in chosen:
            identities=[source['id']] if kind=='task' else [m['task_id'] for m in source.get('messages',[]) if m.get('task_id')]
            if source.get('pending'):raise ValueError('所选讨论仍在处理，请先停止或等待结束。')
            if kind=='task' and source['state'] in ACTIVE|UNKNOWN:raise ValueError('所选工作仍在运行或状态未知，请先核对。')
            for identity in identities:
                for attempt in db.execute('SELECT id,state FROM attempts WHERE task_id=?',(identity,)):
                    worker=self.s.workers.get(attempt['id'])
                    if attempt['state'] in ACTIVE|UNKNOWN or worker and worker['thread'].is_alive():
                        raise ValueError('所选记录仍有在途执行或未知状态，请先核对。')
        removed={(kind,source['id']) for kind,source,view in chosen}
        for kind,identity in removed:
            self.update_display(db,kind,identity,{'deleted':True,'deleted_at':time.time(),'deleted_command':self.s.command_identity,'pinned':False})
            db.execute("DELETE FROM collaboration WHERE kind='ui_draft' AND id=?",('composer:work:'+identity,))
        for scheduled in self.c.all(db,'ui_schedule'):
            if (scheduled['target_kind'],scheduled['target_id']) in removed and scheduled['state'] not in {'COMPLETE','CANCELLED'}:
                scheduled.update(state='CANCELLED',reason='源Work记录已删除');self.c.put(db,'ui_schedule',scheduled)
        return {'deleted':[{'kind':r['kind'],'id':r['id']} for r in p['records']],
                'mode':mode,'no_dispatch':True,'project_files_unchanged':True,'execution_audit_retained':True}

    def restore_deleted(self,db,p):
        """Explicit local repair of entries from one exact deletion receipt.

        This does not reopen schedules, authorize execution or restore a database.
        No generic restore control is added to the Work UI.
        """
        if set(p)-{'delete_command_id','records','reason','confirm'} or p.get('confirm') is not True:
            raise ValueError('请确认准确的Work记录恢复清单。')
        reason=bounded_text(p.get('reason'),'恢复原因',500)
        source=db.execute("SELECT kind,receipt,created FROM commands WHERE id=?",(p.get('delete_command_id'),)).fetchone()
        if not source or source['kind']!='work.records-delete':raise ValueError('原删除回执不存在。')
        permitted={(r['kind'],r['id']) for r in json.loads(source['receipt']).get('deleted',[])}
        rows=p.get('records')
        if not isinstance(rows,list) or not 1<=len(rows)<=1000:raise ValueError('恢复清单无效。')
        checked=[];seen=set()
        for r in rows:
            if not isinstance(r,dict) or set(r)!={'kind','id','deleted_at'} or not isinstance(r['kind'],str) or not isinstance(r['id'],str):
                raise ValueError('恢复身份无效。')
            key=(r['kind'],r['id'])
            if key in seen or key not in permitted:raise ValueError('恢复清单重复或超出原删除回执。')
            seen.add(key);self.source(db,*key);marker=self.display(db,*key)
            if not marker.get('deleted') or marker.get('deleted_at')!=r['deleted_at']:
                raise ValueError('记录的删除状态已变化，未恢复。')
            if marker.get('deleted_command'):
                if marker['deleted_command']!=p['delete_command_id']:raise ValueError('记录来自另一删除回合，未恢复。')
            elif abs(marker['deleted_at']-source['created'])>2:
                raise ValueError('旧删除标记与回执时间不符，未恢复。')
            checked.append(key)
        for kind,identity in checked:
            self.update_display(db,kind,identity,{'deleted':False,'restored_at':time.time(),'restored_from':p['delete_command_id']})
        return {'restored':[{'kind':kind,'id':identity} for kind,identity in checked],
                'reason':reason,'no_dispatch':True,'schedules_not_restarted':True,'execution_audit_unchanged':True}

    def project_record(self,db,p):
        if set(p)-{'project_id','revision','title','pinned','scene','records'} or not ({'title','pinned','scene'}&set(p)):raise ValueError('项目整理字段无效。')
        project=next((x for x in self.projects(db) if x['id']==p.get('project_id')),None)
        if not project or project['revision']!=p.get('revision'):raise ValueError('项目在确认期间已变化，请刷新。')
        changes={}
        if 'scene' in p:
            from .workbench import scene
            changes['scene']=scene(p['scene'])
            if p.get('records'):
                chosen=self.checked(db,{'records':p['records']})
                if any(view['project']!=project['id'] for kind,source,view in chosen):raise ValueError('所选记录不再属于此项目。')
                for kind,source,view in chosen:self.update_display(db,kind,source['id'],{'scene':p['scene']})
        elif 'records' in p:raise ValueError('项目记录清单仅用于明确归入场景。')
        if 'title' in p:
            title=bounded_text(p['title'],'项目名称',100)
            db.execute('UPDATE projects SET name=? WHERE id=?',(title,project['id']))
        if 'pinned' in p:
            if type(p['pinned']) is not bool:raise ValueError('置顶设置须为布尔值。')
            changes['pinned']=p['pinned']
        if changes:self.save_project_meta(db,project['id'],changes)
        return {'project_id':project['id'],'no_dispatch':True,'project_files_unchanged':True}

    def delete_project_history(self, db, p):
        if set(p) != {'projects', 'items', 'records', 'confirm'} or p['confirm'] is not True:
            raise ValueError('请确认项目及其全部历史条目。')
        if (not isinstance(p['projects'], list) or not 1 <= len(p['projects']) <= 1000
                or not isinstance(p['items'], list) or not isinstance(p['records'], list)
                or any(not isinstance(ref, dict) or not isinstance(ref.get('id'), str) for ref in p['projects'])):
            raise ValueError('项目历史清单无效。')
        projects = {ref['id'] for ref in p['projects']}
        if len(projects) != len(p['projects']):
            raise ValueError('项目历史清单存在重复或无效身份。')
        slots = ','.join('?' for _ in projects)
        rows = db.execute(f"SELECT id,revision FROM work_items WHERE project_id IN ({slots}) AND "
                          + self.visible('item', 'work_items.id'), tuple(projects))
        items = sorted((dict(row) for row in rows), key=lambda row: row['id'])
        if not items or any(not isinstance(ref, dict) or set(ref) != {'id','revision'}
                            or not isinstance(ref['id'], str) or type(ref['revision']) is not int for ref in p['items']):
            raise ValueError('请提供项目中全部历史任务的准确版本。')
        if sorted(p['items'], key=lambda ref: ref['id']) != items:
            raise ValueError('项目中的任务清单已变化。')
        identities = {item['id'] for item in items}
        for identity in identities:
            flow = self.s.work_flows.current(db, identity)
            driver = self.s.work_drivers.current(db, identity)
            if (flow and flow['state'] in {'RUNNING','SCHEDULED'} or driver and driver['state'] == 'RESERVED'
                    or db.execute("""SELECT 1 FROM collaboration WHERE kind='cooperative_job'
                        AND json_extract(data,'$.item_id')=? AND json_extract(data,'$.state') NOT IN ('COMPLETE','CANCELLED')""",
                        (identity,)).fetchone()):
                raise ValueError('项目仍有合作责任或计划在途，请先处理。')
        references = self.checked(db, {'records': p['records']}) if p['records'] else []
        for kind, source, view in references:
            if view['project'] not in projects or view['execution_project'] not in projects:
                raise ValueError('所选记录与要删除的项目范围不一致。')
            if any(row[0] not in identities for row in db.execute(
                   'SELECT item_id FROM work_item_records WHERE kind=? AND record_id=?', (kind, source['id']))):
                raise ValueError('所选记录仍被其他任务引用。')
        result = self.delete(db, {'records':p['records'], 'confirm':True, 'mode':'selected',
                                 'selection_count':len(references)}) if references else {'deleted': []}
        for identity in sorted(identities):
            self.update_display(db,'item',identity,{'deleted':True,'deleted_at':time.time(),
                                                    'deleted_command':self.s.command_identity})
        removal = self.remove_projects(db, {'projects':p['projects'], 'confirm':True})
        return {**removal, 'deleted':result['deleted'], 'deleted_items':sorted(identities)}

    def remove_projects(self,db,p):
        if set(p)-{'projects','confirm'} or p.get('confirm') is not True:raise ValueError('请确认准确的闲置工作区清单。')
        expected=p.get('projects')
        if not isinstance(expected,list) or not 1<=len(expected)<=1000:raise ValueError('工作区清单无效。')
        visible={x['id']:x for x in self.projects(db)};chosen=[]
        for item in expected:
            if not isinstance(item,dict) or set(item)!={'id','revision'}:raise ValueError('工作区身份或修订号无效。')
            project=visible.get(item['id'])
            if db.execute('SELECT 1 FROM work_items WHERE project_id=? AND '+self.visible('item','work_items.id'),(item['id'],)).fetchone() or db.execute("SELECT 1 FROM record_scopes s JOIN collaboration c ON c.kind='ui_chat' AND c.id=s.record_id WHERE s.project_id=? AND s.kind='chat'",(item['id'],)).fetchone():
                raise ValueError('项目仍有工作任务或项目对话，不能移除登记。')
            if not project or project['revision']!=item['revision'] or item['id'] in chosen:raise ValueError('工作区清单已变化，未清理。')
            chosen.append(item['id'])
        used={r[k] for r in self.catalog(db,all_records=True)['records'] for k in ('project','execution_project') if r.get(k)}
        used.update(d.get('value',{}).get('project') for d in self.c.all(db,'ui_draft'))
        used.update(g['project_id'] for g in self.c.all(db,'goal_job') if g['state']!='CANCELLED')
        for identity in chosen:
            if identity in used:raise ValueError('工作区仍有记录或草稿，或被目标使用，未清理任何登记。')
            states=ACTIVE|UNKNOWN
            for task in db.execute('SELECT id,state FROM tasks WHERE project_id=?',(identity,)):
                if task['state'] in states:raise ValueError('工作区仍有运行或未知任务，不能清理。')
                for attempt in db.execute('SELECT id,state FROM attempts WHERE task_id=?',(task['id'],)):
                    worker=self.s.workers.get(attempt['id'])
                    if attempt['state'] in states or worker and worker['thread'].is_alive():raise ValueError('工作区仍有在途执行，不能清理。')
        for identity in chosen:self.save_project_meta(db,identity,{'removed':True,'removed_at':time.time(),'removed_command':self.s.command_identity})
        return {'removed':chosen,'no_dispatch':True,'project_files_unchanged':True,'execution_audit_retained':True}
