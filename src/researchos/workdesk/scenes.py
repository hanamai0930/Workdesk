"""Scenes are versioned instructions and capability selections, not agent loops."""
import copy
import json
import time

from .store import canonical, digest, uid
from .workbench import SCENES, scene
from .capabilities import fixed_bundle_warnings, verify_bundle

DEFAULTS={
 'research':('科研探索','围绕问题开展研究，区分资料事实、推论与未验证的假设。','交付可核对的结论、依据和仍未知的事项。'),
 'documents':('文档处理','根据目标阅读、整理或制作文档，保留原文含义和必要结构。','交付所需文档或总结，说明实际处理结果。'),
 'computer':('电脑操控','根据目标使用工作端已有工具操作电脑，逐步核对操作结果。','说明完成的操作、产物位置和未完成事项。'),
 'development':('应用开发','根据目标理解代码、实施必要修改，进行与修改相称的检查。','交付修改和最小验证结果，说明未验证事项。')}
UNSET=object()


class Scenes:
    def __init__(self,service):
        self.s=service;self.c=service.collaboration
        with self.s.store.write() as db:
            if not self.c.all(db,'scene_initialized'):
                for key,(name,prompt,output) in DEFAULTS.items():
                    row={'id':'scene:'+key,'revision':uid(),'scene':key,'name':name,'prompt':prompt,'output':output,
                         'capabilities':[],'extensions':[],'enabled':True,'default':True,'builtin':True,'updated':time.time()}
                    self.c.put(db,'scene_preset',row)
                self.c.put(db,'scene_initialized',{'id':'scene-initialized','at':time.time()})

    def handlers(self):return {'scene.save':self.save,'scene.enable':self.enable,'scene.default':self.set_default,'scene.remove':self.remove}

    def catalog(self,db):
        return {'presets':self.c.all(db,'scene_preset'),'categories':[{'id':key,'name':DEFAULTS[key][0]} for key in DEFAULTS],
                'loads':self.c.all(db,'scene_load')[:50],'effect':'NEW_WORK_ONLY','no_dispatch':True}

    def current(self,db,key):return next((p for p in self.c.all(db,'scene_preset') if p['scene']==key and p['default'] and p['enabled']),None)

    def save(self,db,p):
        if set(p)-{'id','revision','scene','name','prompt','output','capabilities','extensions'}:raise ValueError('场景预设字段无效。')
        old=self.c.get(db,'scene_preset',p['id']) if p.get('id') else None
        if old and p.get('revision')!=old['revision']:raise ValueError('预设已在其他窗口更新，请刷新。')
        key=scene(p.get('scene'))
        if not key:raise ValueError('请选择所属场景。')
        values={}
        for field,limit in [('name',100),('prompt',16000),('output',4000)]:
            value=p.get(field,'')
            if not isinstance(value,str) or len(value)>limit or (field=='name' and not value.strip()):raise ValueError('预设名称或说明长度无效。')
            values[field]=value.strip()
        for field,kind in [('capabilities','capability'),('extensions','extension')]:
            ids=p.get(field,[])
            if not isinstance(ids,list) or len(ids)>30 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):raise ValueError('能力选择格式无效。')
            for identity in ids:self.c.get(db,kind,identity)
            values[field]=ids
        row={**values,'id':old['id'] if old else 'scene:'+uid(),'revision':uid(),'scene':key,
             'enabled':old['enabled'] if old else True,'default':old['default'] if old and old['scene']==key else False,
             'builtin':old.get('builtin',False) if old else False,'updated':time.time()}
        self.c.put(db,'scene_preset',row)
        return {'preset':row,'existing_tasks_unchanged':True,'no_dispatch':True}

    def entry(self,db,p):
        row=self.c.get(db,'scene_preset',p.get('id'))
        if row['revision']!=p.get('revision'):raise ValueError('预设版本已改变，请刷新。')
        return row

    def enable(self,db,p):
        row=self.entry(db,p)
        if type(p.get('enabled')) is not bool:raise ValueError('启用状态无效。')
        row.update(enabled=p['enabled'],updated=time.time());self.c.put(db,'scene_preset',row)
        return {'preset':row,'no_dispatch':True}

    def set_default(self,db,p):
        row=self.entry(db,p)
        if not row['enabled']:raise ValueError('请先启用预设。')
        for entry in self.c.all(db,'scene_preset'):
            if entry['scene']==row['scene']:
                entry['default']=entry['id']==row['id'];self.c.put(db,'scene_preset',entry)
        return {'default':row['id'],'no_dispatch':True}

    def remove(self,db,p):
        row=self.entry(db,p)
        db.execute("DELETE FROM collaboration WHERE kind='scene_preset' AND id=?",(row['id'],))
        return {'removed':row['id'],'frozen_task_versions_preserved':True,'no_dispatch':True}

    def freeze(self,db,key,expected=UNSET,composer=()):
        row=self.current(db,key)
        identity={'id':row['id'],'revision':row['revision']} if row else None
        if expected is not UNSET and expected!=identity:raise ValueError('场景预设已改变，请重新查看后发送。')
        if not row:
            if not composer:return None
            row={'id':'composer','revision':'1','name':'本次工作能力','prompt':'','output':'',
                 'capabilities':[],'extensions':[]}
        return self.freeze_row(db,row,composer)

    def freeze_selection(self,db,reference,*,fixed=False,composer=()):
        if (not isinstance(reference,dict) or set(reference)-{'id','revision','snapshot_hash'}
                or not {'id','revision'}<=set(reference)):
            raise ValueError('请选择准确能力预设版本。')
        if reference['id']=='composer' and reference['revision']=='1' and composer:
            snapshot=self.freeze(db,None,composer=composer)
        else:
            row=self.entry(db,reference)
            if not row['enabled']:raise ValueError('所选能力预设已停用。')
            snapshot=self.freeze_row(db,row,composer)
        if 'snapshot_hash' in reference and reference['snapshot_hash']!=snapshot['snapshot_hash']:
            raise ValueError('能力预设或依赖版本已变化，请重新准备方案。')
        if fixed:self.check_fixed_snapshot(snapshot)
        return snapshot

    @staticmethod
    def check_fixed_snapshot(snapshot):
        for bundle in snapshot['bundles']:
            warnings = fixed_bundle_warnings(bundle)
            if warnings:
                raise ValueError('固定任务预设「'+snapshot['name']+'」含原生插件「'+bundle['name']+'」。'
                                 + ' '.join(warnings) + '请另选可移植 Skill 或 MCP 预设。')

    def freeze_extension(self,db,item,wire):
        from .extension_access import check_dependencies
        return {'name':wire,'label':item['name'],'extension_id':item['id'],'config':item['config'],
                'revision':item['revision'],'dependencies':check_dependencies(self.s,db,item)}

    def freeze_row(self,db,row,composer=()):
        snapshot=copy.deepcopy(row);snapshot['bundles']=[];snapshot['mcp']=[]
        names=set();skill_names=set()
        for identity in row['capabilities']:
            item=self.c.get(db,'capability',identity)
            if not item['enabled']:raise ValueError('预设中的能力已停用：'+item['name']+'。请调整预设后发送。')
            verify_bundle(item);snapshot['bundles'].append(copy.deepcopy(item))
            for skill in item['skills']:
                if skill['name'] in skill_names:raise ValueError('所选能力含同名 Skill：'+skill['name'])
                skill_names.add(skill['name'])
            for name,config in item['servers'].items():
                wire='wd_'+digest([item['id'],name])[:16]
                snapshot['mcp'].append({'name':wire,'label':item['name']+' / '+name,'config':config,'revision':item['revision']});names.add(wire)
        for identity in row['extensions']:
            item=self.c.get(db,'extension',identity)
            if not item['enabled']:raise ValueError('预设中的 MCP 已停用：'+item['name'])
            wire='wd_'+digest(identity)[:16]
            snapshot['mcp'].append(self.freeze_extension(db,item,wire))
        if not isinstance(composer,(list,tuple)) or len(composer)>30:
            raise ValueError('能力选择格式无效。')
        for reference in composer:
            if (not isinstance(reference,dict) or set(reference)!={'kind','id','revision','name'}
                    or not all(isinstance(value,str) and value for value in reference.values())
                    or reference['kind'] not in {'skill','mcp'}):
                raise ValueError('能力选择格式无效。')
            is_skill=reference['kind']=='skill'
            item=self.c.get(db,'capability' if is_skill else 'extension',reference['id'])
            if not item['enabled'] or item['revision']!=reference['revision']:
                raise ValueError('能力已停用或更新，请重新选择。')
            if is_skill:
                skill=next((s for s in item['skills'] if s['name']==reference['name']),None)
                if not skill:raise ValueError('所选 Skill 已不可用。')
                existing=next((b for b in snapshot['bundles'] if b['id']==item['id']),None)
                if existing and skill in existing['skills']:continue
                if skill['name'] in skill_names:raise ValueError('所选能力含同名 Skill：'+skill['name'])
                skill_names.add(skill['name'])
                if existing:
                    existing['skills'].append(copy.deepcopy(skill))
                else:
                    verify_bundle(item)
                    snapshot['bundles'].append({**copy.deepcopy(item),'skills':[copy.deepcopy(skill)]})
            else:
                if reference['name']!=item['name']:raise ValueError('所选 MCP 已不可用。')
                wire='wd_'+digest(item['id'])[:16]
                if not any(server['name']==wire for server in snapshot['mcp']):
                    snapshot['mcp'].append(self.freeze_extension(db,item,wire))
        if composer:snapshot['composer_capabilities']=copy.deepcopy(composer)
        snapshot['snapshot_hash']=digest(snapshot)
        return snapshot

    def send(self,db,p,*,cooperative=None,selected_preset=None,fixed=False):
        from .official_work import send
        self.s.monitor.guard(db,p)
        payload=dict(p);expected=payload.pop('scene_preset',UNSET)
        composer=payload.pop('composer_capabilities',[])
        if composer and payload.get('task_id'):raise ValueError('已有工作沿用启动时加载的能力。')
        snapshot=(self.freeze_selection(db,selected_preset,composer=composer) if selected_preset is not None
                  else self.freeze(db,payload.get('work_scene'),expected,composer)) if not payload.get('task_id') else None
        if fixed and snapshot:self.check_fixed_snapshot(snapshot)
        result=send(self.s,db,payload,**({'cooperative':cooperative} if cooperative is not None else {}));task=self.s.store.task(result['task_id'],db)
        if not payload.get('task_id') and snapshot:
            task['spec']['scene_snapshot']=snapshot
            version=digest(task['spec']);result['spec_hash']=version
            db.execute('UPDATE tasks SET spec=?,spec_hash=? WHERE id=?',(canonical(task['spec']),version,task['id']))
        snapshot=task['spec'].get('scene_snapshot')
        if snapshot:
            for bundle in snapshot['bundles']:verify_bundle(bundle)
            row={'id':'scene-load:'+result['launch_attempt'],'task_id':task['id'],'attempt_id':result['launch_attempt'],
                 'preset':{'id':snapshot['id'],'revision':snapshot['revision'],'name':snapshot['name'],'snapshot_hash':snapshot['snapshot_hash']},
                 'provider':task['spec']['provider'],'state':'FROZEN','updated':time.time(),
                 'skills':[s['name'] for b in snapshot['bundles'] for s in b['skills']],
                 'mcp':[{'name':m['name'],'label':m['label']} for m in snapshot['mcp']]}
            self.c.put(db,'scene_load',row)
            self.s.store.event(task['id'],result['launch_attempt'],'scene_configuration_frozen',row,db)
        return result
