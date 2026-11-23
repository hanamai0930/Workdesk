"""Versioned cooperation preparation. No execution, lease or queue activation."""
from pathlib import Path
import re
import time

from .contracts import bounded_text
from .native_permissions import catalog as permission_catalog, choice as permission_choice
from .plans import PlanCommands
from .store import digest, uid
from .work_artifacts import definition as artifact_definition


MODES={'fixed','leader','host_experts'}
ROLES={'plan','execute','review','integrate','coordinate'}


def fixed_access(value, members):
    if not isinstance(value, str):
        raise ValueError('请选择固定流程的工作端权限。')
    access = permission_choice(value)
    for provider in sorted({member['provider'] for member in members}):
        options = permission_catalog(provider)
        if not any(option['id'] == access and option['supported'] for option in options):
            raise ValueError(f'{provider} 不支持所选固定流程权限，请更换成员或权限。')
    return access


def member_permissions(value, overrides, members):
    """Freeze the explicit overrides against the already selected team."""
    default = fixed_access(value, ())
    if not isinstance(overrides, dict) or set(overrides) - set(members):
        raise ValueError('审批方式只能为本次选定成员单独设置。')
    effective = {identity: fixed_access(overrides.get(identity, default), (member,))
                 for identity, member in members.items()}
    return default, {identity: effective[identity] for identity in overrides}


def frozen_access(configuration, member):
    identity = member['reference']['member_id']
    return configuration.get('member_access', {}).get(identity, configuration.get('access', 'workspace'))


def single_step_supported(plan):
    return bool(plan and (plan['mode']=='fixed' or any(u['role']=='coordinate' for u in plan['units'])))


def write_paths(values,root):
    if not isinstance(values,list) or len(values)>40:raise ValueError('写入范围须为最多40项相对路径。')
    result=[]
    for value in values:
        if not isinstance(value,str) or not value or len(value)>500:raise ValueError('写入路径无效。')
        value=value.replace('\\','/')
        if value!='.':
            parts=value.split('/')
            if any(not part or part in {'.','..'} or part.endswith((' ','.')) or
                   re.search(r'[\x00-\x1f<>:"|?*]',part) or
                   re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',part,re.I) for part in parts):
                raise ValueError('写入范围须为工作目录内的明确相对路径，不能含通配符或路径别名。')
        try:value=(root/value).resolve().relative_to(root.resolve()).as_posix()
        except (OSError,ValueError,RuntimeError):raise ValueError('写入路径超出工作目录或无法核对。')
        if value.casefold() in {p.casefold() for p in result}:raise ValueError('写入路径重复。')
        result.append(value)
    return result


def overlaps(left,right):
    for a in left:
        for b in right:
            a=a.casefold();b=b.casefold()
            if a=='.' or b=='.' or a==b or a.startswith(b+'/') or b.startswith(a+'/'):return True
    return False


def batches(units,limit):
    """Hypothetical waves only; prior waves are not evidence of completion."""
    pending=list(units);done=set();waves=[]
    while pending:
        wave=[]
        for unit in pending:
            if not set(unit['depends'])<=done or len(wave)>=limit:continue
            if any(overlaps(unit['write_paths'],other['write_paths']) or
                   unit['member']['reference']['connection_id']==other['member']['reference']['connection_id'] for other in wave):continue
            wave.append(unit)
        if not wave:raise ValueError('依赖图无法形成结构批次。')
        keys={u['key'] for u in wave};waves.append([u['key'] for u in wave]);done.update(keys)
        pending=[u for u in pending if u['key'] not in keys]
    return waves


class Teamwork:
    def __init__(self,service):self.s=service

    def handlers(self):return {'work-item.teamwork-save':self.save}

    def current(self,db,item_id):
        rows=db.execute("SELECT data FROM collaboration WHERE kind='teamwork_current' AND id=?",('teamwork-current:'+item_id,)).fetchone()
        if not rows:return None
        import json
        return self.s.collaboration.get(db,'teamwork_plan',json.loads(rows[0])['plan_id'])

    def member(self,db,ref):
        if not isinstance(ref,dict):raise ValueError('请选择明确成员及当前连接。')
        selected=self.s.resources.selection(db,ref.get('member_id'))
        if ref!=selected['reference']:raise ValueError('成员、连接或额度来源已变化，请刷新后重新准备。')
        return {'reference':selected['reference'],**{k:selected['member'][k] for k in ('name','role','provider','model','effort','tier')}}

    def save(self,db,p,*,daily=False,pdca=None):
        if set(p)-{'id','revision','current_plan','mode','coordinator','integrator','max_parallel','hard_cash_limit_cny','units','file_handoffs','access','member_access'}:
            raise ValueError('合作方案字段不支持。')
        if 'access' in p and p.get('mode')!='fixed' and not daily:
            raise ValueError('此工作端权限选项仅适用于固定流程。')
        if 'member_access' in p and (p.get('mode') != 'fixed' or p.get('file_handoffs')):
            raise ValueError('此审批方式设置仅用于普通固定方案。')
        item=self.s.work_items.checked(db,p);self.s.work_items.project(db,item['project_id'])
        prior_root=self.current(db,item['id'])
        if prior_root and prior_root.get('coordinator_file_handoff_root'):
            raise ValueError('新关系原根已保存，普通保存不能迁移或删除身份；修订须使用专用入口。')
        if p.get('file_handoffs') and p.get('mode') in ('leader','host_experts'):
            if isinstance(p.get('units'),list) and not any(isinstance(u,dict) and u.get('role')=='coordinate' for u in p['units']):
                raise ValueError('同路径交接须使用固定流程，或在新原根中明确声明负责人／主持人的提案单元。')
            from .work_coordinator_handoff_public import save
            return save(self.s,db,p)
        self.s.work_drivers.guard(db,item['id'])
        if item['state'] in {'ACCEPTED','CANCELLED'}:raise ValueError('已接受或取消的任务须先重新打开才能准备方案。')
        prior=self.current(db,item['id']);expected={'id':prior['id'],'version':prior['version']} if prior else None
        if p.get('current_plan')!=expected:raise ValueError('合作方案已变化，请刷新后再保存。')
        if ((prior and prior.get('file_handoffs')) or p.get('file_handoffs')) and self.s.work_units.rows(db,item['id']):
            raise ValueError('同路径交接方案已有运行，普通保存不能删除、复制或更改关系。')
        mode=p.get('mode')
        if not isinstance(mode,str) or mode not in MODES:raise ValueError('请选择三种基础合作模式之一。')
        limit=p.get('max_parallel',1)
        if type(limit) is not int or not 1<=limit<=4:raise ValueError('并行数量须为1至4。')
        cash=p.get('hard_cash_limit_cny')
        if cash is not None and (type(cash) not in (int,float) or not 0<=cash<=1000000):raise ValueError('硬费用上限无效。')
        coordinator=self.member(db,p.get('coordinator'));integrator=self.member(db,p.get('integrator'))
        definitions=p.get('units')
        if not isinstance(definitions,list) or not 1<=len(definitions)<=20:raise ValueError('合作方案需要1至20项工作单元。')
        root=Path(db.execute('SELECT root FROM projects WHERE id=?',(item['project_id'],)).fetchone()[0]) if item['project_id'] else self.s.locations.data/'unfiled-work'
        units=[];keys=set()
        for definition in definitions:
            if not isinstance(definition,dict) or set(definition)-{'key','title','goal','acceptance','role','member','depends','write_paths','outputs','checks','dependency_gate','scene_preset'}:raise ValueError('工作单元字段不支持。')
            key=definition.get('key')
            if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',key) or key in keys:raise ValueError('工作单元身份无效或重复。')
            keys.add(key);role=definition.get('role')
            if not isinstance(role,str) or role not in ROLES:raise ValueError('工作单元职责无效。')
            deps=definition.get('depends',[])
            if not isinstance(deps,list) or len(deps)>20 or any(not isinstance(d,str) for d in deps) or len(set(deps))!=len(deps):raise ValueError('前置工作引用无效或重复。')
            units.append({'key':key,'title':bounded_text(definition.get('title'),'工作名称',160),
                'goal':bounded_text(definition.get('goal'),'单元目标',4000),
                'acceptance':bounded_text(definition.get('acceptance'),'完成要求',4000),
                'role':role,'member':self.member(db,definition.get('member')),'depends':deps,
                'write_paths':write_paths(definition.get('write_paths',[]),root),
                **artifact_definition(definition, allow_section_length=(mode=='fixed' or daily) and not p.get('file_handoffs'))})
            if definition.get('scene_preset') is not None:
                snapshot=self.s.scenes.freeze_selection(db,definition['scene_preset'],fixed=mode=='fixed' or daily)
                units[-1]['scene_preset']={k:snapshot[k] for k in ('id','revision','snapshot_hash')}
        PlanCommands.validate_graph({u['key']:u['depends'] for u in units})
        for unit in units:
            if unit['role']=='coordinate' and (mode=='fixed' or unit['member']['reference']!=coordinator['reference'] or
                    unit['write_paths'] or unit['outputs'] or unit['checks']):
                raise ValueError('提案单元须由非固定模式的负责人／主持人承担，写入意图为空且不声明文件产物或检查。')
        # Local import: the strict validator reuses this module's overlaps().
        from .work_file_handoff_config import definition as handoff_definition
        handoffs=handoff_definition(p.get('file_handoffs',[]),units,limit)
        if handoffs and mode!='fixed':raise ValueError('同路径交接首批只支持固定流程的明确单步派发。')
        owners={}
        for unit in units:
            for name in unit['outputs']:owners.setdefault(name.casefold(),[]).append(unit['key'])
        pair={handoffs[0][k] for k in ('source_unit','target_unit')} if handoffs else set()
        shared={n.casefold() for n in handoffs[0]['paths']} if handoffs else set()
        if any(len(keys)>1 and (name not in shared or len(keys)!=2 or set(keys)!=pair) for name,keys in owners.items()):
            raise ValueError('不同单元须使用不同产物文件名，明确交接关系仅允许准确两端复用完整文件集合。')
        for unit in units:
            for key in unit['depends']:
                previous=next(u for u in units if u['key']==key)
                if unit['dependency_gate'] in {'files','checked'} and not previous['outputs']:
                    raise ValueError('文件依赖要求前置单元声明产物。')
                if unit['dependency_gate']=='checked' and not previous['checks']:
                    raise ValueError('检查依赖要求前置单元预先声明检查。')
        value={'id':'teamwork:'+uid(),'item_id':item['id'],'item_revision':item['revision'],'project_id':item['project_id'],
            'work_requirements':{k:item[k] for k in ('title','goal','constraints_text','acceptance')},
            'mode':mode,'coordinator':coordinator,'integrator':integrator,'max_parallel':limit,'hard_cash_limit_cny':cash,
            'units':units,'previous_plan':prior['id'] if prior else None,'state':'PREPARED','created':time.time()}
        if any(unit['member']['provider']=='qwen' for unit in units):
            route=prior.get('qwen_runtime','query') if prior else self.s.independent.qwen_selection(db)['interface']
            if route=='daemon':value['qwen_runtime']=route
        if mode=='fixed':
            members = {m['reference']['member_id']: m for m in
                       (coordinator, integrator, *(unit['member'] for unit in units))}
            inherited = {key: mode for key, mode in (prior or {}).get('member_access', {}).items() if key in members}
            access, overrides = member_permissions(p.get('access', (prior or {}).get('access', 'workspace')),
                                                   p.get('member_access', inherited), members)
            if access != 'workspace':
                value['access'] = access
            if overrides:
                value['member_access'] = overrides
        if handoffs:value['file_handoffs']=handoffs
        if pdca:value['pdca']=pdca
        value['version']=digest(value)
        self.s.collaboration.put(db,'teamwork_plan',value)
        self.s.collaboration.put(db,'teamwork_current',{'id':'teamwork-current:'+item['id'],'plan_id':value['id']})
        self.s.store.event(None,None,'teamwork_plan_prepared',{'work_item_id':item['id'],'item_revision':item['revision'],
            'plan_id':value['id'],'version':value['version'],'mode':mode,'no_dispatch':True},db)
        return {'teamwork':self.view(db,item,plan=value),'no_dispatch':True,'execution_unchanged':True}

    def view(self,db,item,plan=None,*,resource_catalog=None):
        plan=plan or self.current(db,item['id'])
        if not plan:return None
        from .work_readiness_observations import resource_changes
        from .work_leader import foundation
        data=self.s.resources.catalog(db) if resource_catalog is None else resource_catalog
        supported=single_step_supported(plan)
        blockers=[] if supported else ['COOPERATIVE_DISPATCH_NOT_CONNECTED'];changed=resource_changes(plan,data)
        if changed:blockers.append('RESOURCE_CHANGED_OR_UNAVAILABLE')
        driver=self.s.work_drivers.current(db,item['id'])
        inherited=bool(driver and driver['state'] in {'RESERVED','PAUSED'} and driver['plan_id']==plan['id'] and
            driver['plan_version']==plan['version'] and driver['item_revision']==item['revision'])
        stale=(plan['item_revision']!=item['revision'] and not inherited) or plan['project_id']!=item['project_id']
        if stale:blockers.append('WORK_ITEM_CHANGED')
        if item['state']!='OPEN':blockers.append('WORK_ITEM_NOT_OPEN')
        if plan['hard_cash_limit_cny'] is not None:blockers.append('HARD_CASH_LIMIT_UNSUPPORTED')
        sources=sorted({u['member']['reference']['quota_id'] for u in plan['units']}|
            {plan[k]['reference']['quota_id'] for k in ('coordinator','integrator')})
        return {'plan':plan,'stale':stale,'resource_changes':changed,'blockers':blockers,
            'structure_batches':batches(plan['units'],plan['max_parallel']),
            'quota_sources':sources,'quota_enforcement':'INFORMATION_ONLY','write_scope_enforcement':'DECLARED_NOT_ENFORCED',
            'dispatch_supported':supported,'single_step_supported':supported,
            'continuous_supported':foundation(self.s,db,plan) and not plan.get('file_handoffs'),
            'handoff_configuration_locked':bool(plan.get('coordinator_file_handoff_root') or self.s.work_units.rows(db,item['id'])),
            'driver_acquired':self.s.work_drivers.held(db,item['id']),'no_dispatch':True}
