"""Additive adapters for an explicitly frozen scene; vendor agent loops stay intact."""
import json
import os
from pathlib import Path
import time

from .capabilities import verify_bundle
from .paths import atomic_json


def selected(task):
    snapshot=task['spec'].get('scene_snapshot')
    if snapshot:
        for bundle in snapshot['bundles']:verify_bundle(bundle)
    return snapshot


def prompt(task,text,resume=False):
    data=selected(task)
    if task['spec'].get('plan_only'):
        text='【计划模式】本回合只分析与形成方案。请勿修改项目、运行会改变状态的操作或开始实施。\n\n'+text
    if not data or resume:return text
    text += '\n\n[Workdesk 已选择的场景预设：'+data['name']+']\n'+data['prompt']+'\n预期交付：'+data['output']
    choices=data.get('composer_capabilities',[])
    if choices:
        text += '\n本次选用：'+'、'.join(ref['kind']+' '+ref['name'] for ref in choices)
        if any(ref['kind']=='mcp' for ref in choices):
            text += '\n已加载的 MCP：'+'、'.join(item['label']+'（'+item['name']+'）' for item in data['mcp'])
    return text


def skills(task):
    data=selected(task)
    return [{'name':skill['name'],'path':str(Path(bundle['path'])/skill['relative']/'SKILL.md')}
            for bundle in data['bundles'] for skill in bundle['skills']] if data else []


def servers(task,provider):
    data=selected(task);result={}
    for item in data['mcp'] if data else []:
        c=item['config']
        result[item['name']]=({'httpUrl' if provider=='qwen' else 'url':c['url']} if c.get('transport')=='http'
                             else {k:c[k] for k in ('command','args','cwd')})
    return result


def codex_thread(task,config):
    # Dotted process-local config keys preserve other native MCP entries.
    mcp=servers(task,'codex')
    if mcp:config['config']={**config.get('config',{}),**{'mcp_servers.'+name:value for name,value in mcp.items()}}
    return config


def codex_input(task,native_input):
    return native_input+[{'type':'skill',**item} for item in skills(task)]


def qwen(locations,task,config,env):
    data=selected(task)
    if not data:return
    config['scene_mcp']=servers(task,'qwen');config['scene_skills']=skills(task)
    if config['scene_skills']:
        original=Path(os.environ.get('QWEN_CODE_SYSTEM_DEFAULTS_PATH') or 'C:/ProgramData/qwen-code/system-defaults.json')
        defaults=json.loads(original.read_text(encoding='utf-8-sig')) if original.is_file() else {}
        if not isinstance(defaults,dict):raise ValueError('Qwen 系统默认配置格式无效。')
        dirs=defaults.setdefault('skills',{}).setdefault('directories',[])
        if not isinstance(dirs,list):raise ValueError('Qwen skills.directories 格式无效。')
        # Each root holds one or more original skill directory bundles.
        for item in config['scene_skills']:
            root=str(Path(item['path']).parent.parent)
            if root not in dirs:dirs.append(root)
        path=locations.data/'tasks'/task['id']/'scene-qwen-defaults.json'
        atomic_json(path,defaults)
        env['QWEN_CODE_SYSTEM_DEFAULTS_PATH']=str(path)
        config['scene_env']={'QWEN_CODE_SYSTEM_DEFAULTS_PATH':str(path)}


def deepseek_patch(task,workers=None):
    data=selected(task)
    if not data:return ''
    entries=[]
    roots=sorted({str(Path(s['path']).parent.parent) for s in skills(task)})
    if roots:
        entries.append({'id':'workdesk-scene-skills','name':'@deepseek-ai/dsh-skill-filesystem',
                        'config':{'providerName':'workdesk-scene','includeDefaultRoots':False,'customSkillDirs':roots,'watch':False}})
    for item in data['mcp']:
        c=item['config'];config={'serverName':item['name']}
        if c.get('transport')=='http':config.update(transport='streamable-http',url=c['url'])
        else:config.update(transport='stdio',**{k:c[k] for k in ('command','args','cwd')})
        entries.append({'id':'workdesk-'+item['name'],'name':'@deepseek-ai/dsh-mcp-client','config':config})
    if workers:
        entries.append({'id':'workdesk-scene-observer','name':(Path(workers)/'scene_observer.mjs').as_uri(),
                        'config':{'skills':[s['name'] for s in skills(task)],'mcp':[m['name'] for m in data['mcp']]}})
    # JSON objects are valid YAML flow mappings; avoid interpolating executable YAML tags.
    return '\n- insert:\n'+''.join('    - '+json.dumps(item,ensure_ascii=False)+'\n' for item in entries) if entries else ''


def receipt(store,task,attempt,state,details):
    if not task['spec'].get('scene_snapshot'):return
    with store.write() as db:
        if 'session_id' in details and details['session_id'] != store.attempt(attempt['id'], db)['session_id']:
            raise ValueError('能力回执与本次原生会话不匹配。')
        row=db.execute("SELECT data FROM collaboration WHERE kind='scene_load' AND id=?",('scene-load:'+attempt['id'],)).fetchone()
        if not row:return
        value=json.loads(row[0]);value.update(state=state,details=details,updated=time.time())
        db.execute("UPDATE collaboration SET data=?,updated=? WHERE kind='scene_load' AND id=?",
                   (json.dumps(value,ensure_ascii=False),time.time(),value['id']))
        store.event(task['id'],attempt['id'],'scene_native_load_observation',{'state':state,'details':details},db)


def finish(store,task,attempt):
    with store.read() as db:
        row=db.execute("SELECT data FROM collaboration WHERE kind='scene_load' AND id=?",('scene-load:'+attempt['id'],)).fetchone()
    if not row:return
    value=json.loads(row[0]);latest=store.attempt(attempt['id']);result=latest.get('result') or {}
    state=value['state']
    if state!='FAILED' and latest['state'] in {'FAILED_NO_SEND','FAILED','SEND_UNKNOWN','ORPHANED_UNKNOWN'}:state='FAILED' if latest['state'].startswith('FAILED') else 'UNKNOWN'
    receipt(store,task,attempt,state,{**value.get('details',{}),'turn_state':latest['state'],'errors':result.get('errors',[]),'known_tools_exited':result.get('known_tools_exited')})
