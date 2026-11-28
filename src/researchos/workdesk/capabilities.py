"""Versioned local capability bundles. Import is data copying, never installation."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import importlib.util
import sys

from .paths import atomic_bytes
from .store import uid, digest


MAX_FILES = 500
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
PLUGIN_MANIFESTS = ('plugin.json', '.codex-plugin/plugin.json', 'qwen-extension.json')
PORTABLE_QWEN_FIELDS = {'name', 'version', 'description', 'displayName', 'author', 'homepage',
                        'repository', 'license', 'keywords', 'mcpServers'}


def native_plugin_warnings(files, manifest_name, manifest):
    warnings = []
    if any(key in manifest for key in ('hooks', 'commands', 'agents')) or any(
            name.startswith(('hooks/', 'commands/', 'agents/')) for name in files):
        warnings.append('此包含 hooks、commands 或 agents，须由官方工作端管理。')
    if manifest_name == 'qwen-extension.json':
        fields = set(manifest) - PORTABLE_QWEN_FIELDS - {'hooks', 'commands', 'agents'}
        if 'QWEN.md' in files:
            fields.add('QWEN.md')
        if any(name.startswith('workflows/') for name in files):
            fields.add('workflows')
        if fields:
            warnings.append('Qwen 原生组件须由官方工作端管理：' + '、'.join(sorted(fields)) + '。')
    if sum(name in files for name in PLUGIN_MANIFESTS) > 1:
        warnings.append('目录包含多个插件清单。请提供一个单独的可移植能力包，或由官方工作端管理原生插件。')
    return warnings


def fixed_bundle_warnings(bundle):
    if bundle['kind'] != 'plugin':
        return []
    files = bundle['files']
    manifest_name = next((name for name in PLUGIN_MANIFESTS if name in files), None)
    if manifest_name is None:
        return bundle.get('warnings', [])
    raw = (Path(bundle['path']) / manifest_name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != files[manifest_name]:
        raise ValueError('能力内容已变化：'+bundle['name']+' / '+manifest_name+'；未加载或发送。')
    manifest = json.loads(raw)
    return native_plugin_warnings(files, manifest_name, manifest) or bundle.get('warnings', [])


def local_directory(value):
    if not isinstance(value,str) or not Path(value).is_absolute() or value.startswith('\\\\'):
        raise ValueError('请选择本机文件夹。')
    root=Path(value)
    if root.is_symlink() or root.is_junction() or not root.is_dir():raise ValueError('文件夹不存在或是链接。')
    return root.resolve()


def bundle_files(root):
    result={};total=0
    for folder,dirs,files in os.walk(root,followlinks=False):
        for name in dirs+files:
            p=Path(folder)/name
            if p.is_symlink() or p.is_junction():raise ValueError('导入包不能包含符号链接或目录联接。')
        dirs[:]=[d for d in dirs if d not in {'.git','node_modules','__pycache__'}]
        for name in files:
            path = Path(folder) / name
            if not path.is_file():
                raise ValueError('能力包须使用普通文件。')
            with path.open('rb') as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            total += len(raw)
            if len(result)>=MAX_FILES or total>MAX_TOTAL_BYTES or len(raw)>MAX_FILE_BYTES:raise ValueError('能力包超过500文件、20MiB总大小或单文件5MiB。')
            result[path.relative_to(root).as_posix()] = raw
    return result


def yaml_module(locations):
    # The control service deliberately disables the user's Python site. Load
    # this pinned product dependency from its own runtime, without changing
    # native child Python policy or importing other user-installed packages.
    name='workdesk_yaml'
    if name in sys.modules:return sys.modules[name]
    root=locations.runtime/'python-site/yaml'
    if not (root/'__init__.py').is_file():raise ValueError('Workdesk缺少已打包的YAML解析依赖，请修复运行环境。')
    spec=importlib.util.spec_from_file_location(name,root/'__init__.py',submodule_search_locations=[str(root)])
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module
    try:spec.loader.exec_module(module)
    except Exception:sys.modules.pop(name,None);raise
    return module


def skill_metadata(raw,folder,yaml):
    if len(raw)>256*1024:raise ValueError('SKILL.md 超过256KiB。')
    text=raw.decode('utf-8-sig');parts=text.split('---',2)
    if len(parts)!=3 or parts[0].strip():raise ValueError('SKILL.md 需要 YAML name 和 description。')
    try:meta=yaml.safe_load(parts[1])
    except yaml.YAMLError:raise ValueError('Skill 的 YAML 格式无效。') from None
    if not isinstance(meta,dict):raise ValueError('Skill 元数据须为对象。')
    name=meta.get('name');description=meta.get('description')
    if not isinstance(name,str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',name) or len(name)>64 or name!=folder:
        raise ValueError('Skill 名称须与文件夹同名，只使用小写字母、数字和单连字符，最多64字。')
    if not isinstance(description,str) or not description.strip() or len(description)>1024:raise ValueError('Skill description 须为1至1024字。')
    return {'name':name,'description':description,'body':parts[2].strip(),'metadata':meta}


def mcp_config(value):
    if not isinstance(value,dict):raise ValueError('MCP 配置须为对象。')
    transport=value.get('transport','stdio' if value.get('command') else 'http')
    if transport in {'http','streamable-http'}:
        if set(value)-{'transport','url'}:raise ValueError('HTTP MCP 使用 url；认证由原生工作端处理，不保存令牌正文。')
        url=value.get('url','');parsed=urlsplit(url)
        if (parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in {'localhost','127.0.0.1','::1'})) or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('HTTP MCP 须使用 HTTPS，或本机回环 HTTP；不能在地址中包含用户名和密码。')
        if len(url)>2048:raise ValueError('MCP 地址过长。')
        return {'transport':'http','url':url}
    if transport!='stdio' or set(value)-{'transport','command','args','cwd'}:raise ValueError('本地 MCP 使用 command、args、cwd。')
    command=value.get('command');args=value.get('args',[]);cwd=value.get('cwd')
    if not isinstance(command,str) or not Path(command).is_absolute() or not Path(command).is_file() or command.startswith('\\\\'):
        raise ValueError('请选择已安装程序的绝对路径。')
    if not isinstance(args,list) or len(args)>64 or any(not isinstance(a,str) or len(a)>4096 for a in args):raise ValueError('程序参数须为字符串数组。')
    return {'transport':'stdio','command':command,'args':args,'cwd':str(local_directory(cwd))}


class Capabilities:
    def __init__(self,service):self.s=service;self.c=service.collaboration

    def handlers(self):
        return {'capability.import':self.import_bundle,'capability.enable':self.enable,'capability.remove':self.remove,'capability.restore':self.restore}

    def catalog(self,db):return self.c.all(db,'capability')

    def import_bundle(self,db,p):
        if set(p)-{'path','source','kind','id','revision'}:raise ValueError('能力导入字段无效。')
        old=self.c.get(db,'capability',p['id']) if p.get('id') else None
        if old and p.get('revision')!=old['revision']:raise ValueError('能力版本已变化，请刷新。')
        local = self.inspect_local(p.get('path'), p.get('source'), p.get('kind', 'skill'))
        return self.save_local(db, local, old=old)

    def import_public_skill(self, db, name, files, origin):
        return self.save_files(db, self.inspect_files(files, 'skill', name), origin['url'], origin=origin)

    def inspect_local(self, path, source, kind, *, fixed_review=False):
        root = local_directory(path)
        if not isinstance(source,str) or not source.strip() or len(source)>1000:raise ValueError('请填写来源。')
        inspected = self.inspect_files(bundle_files(root), kind, root.name, fixed_review=fixed_review)
        return {'root': root, 'source': source.strip(), 'inspected': inspected}

    def save_local(self, db, local, *, old=None):
        return self.save_files(db, local['inspected'], local['source'], source_path=str(local['root']), old=old)

    def inspect_files(self, files, kind, folder, *, fixed_review=False):
        skills=[];servers={};warnings=[]
        if kind=='skill':
            if 'SKILL.md' not in files:raise ValueError('所选文件夹没有 SKILL.md。')
            skills=[{**skill_metadata(files['SKILL.md'],folder,yaml_module(self.s.locations)),'relative':'skills/'+folder}];name=skills[0]['name']
            files={'skills/'+folder+'/'+n:raw for n,raw in files.items()}
        elif kind=='plugin':
            manifest_name=next((n for n in PLUGIN_MANIFESTS if n in files),None)
            if not manifest_name:raise ValueError('未找到 plugin.json、.codex-plugin/plugin.json 或 qwen-extension.json。')
            manifest=json.loads(files[manifest_name])
            if not isinstance(manifest, dict):raise ValueError('插件清单须为对象。')
            name=manifest.get('name')
            if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}',name):raise ValueError('插件名称格式无效。')
            warnings = native_plugin_warnings(files, manifest_name, manifest)
            # Review a native-only package by file and declaration, without
            # interpreting any of its execution settings as portable inputs.
            if not (fixed_review and warnings):
                skills, servers = self.portable_plugin(files, manifest)
        else:raise ValueError('请选择 Skill 或插件包。')
        if len(skills)>100:raise ValueError('单包最多100个 Skills。')
        hashes = {n: hashlib.sha256(raw).hexdigest() for n, raw in files.items()}
        return {'name': name, 'kind': kind, 'files': files, 'hashes': hashes, 'content_hash': digest(hashes),
                'skills': skills, 'servers': servers, 'warnings': warnings}

    def portable_plugin(self, files, manifest):
        skills = []
        for file, raw in files.items():
            if re.fullmatch(r'skills/[^/]+/SKILL.md', file):
                skills.append({**skill_metadata(raw, file.split('/')[1], yaml_module(self.s.locations)),
                               'relative': file.rsplit('/', 1)[0]})
        servers = {}
        mcp = manifest.get('mcpServers')
        config_name = mcp if isinstance(mcp, str) else None
        if config_name:
            config_name = config_name.removeprefix('./')
            if '..' in Path(config_name).parts or config_name.startswith('/') or ':' in config_name:
                raise ValueError('插件 MCP 配置路径越界。')
        config_name = config_name or next((n for n in ('mcp.json', '.mcp.json') if n in files), None)
        if config_name:
            if config_name not in files:
                raise ValueError('插件声明的 MCP 配置不存在。')
            config = json.loads(files[config_name])
            if not isinstance(config, dict):
                raise ValueError('插件 MCP 配置须为对象。')
            servers = config.get('mcpServers', {})
        elif isinstance(mcp, dict):
            servers = mcp
        if not isinstance(servers, dict) or len(servers) > 20:
            raise ValueError('插件 MCP 配置无效或超过20个服务。')
        if not skills and not servers:
            raise ValueError('此包没有可适配的 Skills 或 MCP 服务。')
        return skills, servers

    def save_files(self, db, inspected, source, *, source_path=None, old=None, origin=None):
        version = inspected['content_hash']
        dest=self.s.locations.data/'capabilities'/version
        for n,raw in inspected['files'].items():
            target=dest/n
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=inspected['hashes'][n]:raise ValueError('已保存的能力内容被修改，请先检查。')
            if not target.exists():target.parent.mkdir(parents=True,exist_ok=True);atomic_bytes(target,raw)
        normalized = self.configured_servers(inspected['servers'], dest)
        row={'id':old['id'] if old else 'capability:'+uid(),'revision':uid(),'kind':inspected['kind'],'name':inspected['name'],'source':source,
             'source_path':source_path,'path':str(dest),'content_hash':version,'files':inspected['hashes'],
             'skills':inspected['skills'],'servers':normalized,'warnings':inspected['warnings'],'enabled':False,'updated':time.time()}
        if origin:row['public_origin']=origin
        self.c.put(db,'capability_version',dict(row,id=row['revision'],capability_id=row['id']))
        self.c.put(db,'capability',row)
        return {'capability':row,'no_dispatch':True,'installation_scripts_executed':False}

    @staticmethod
    def configured_servers(servers, dest):
        normalized={}
        for key,value in servers.items():
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}',key):raise ValueError('插件 MCP 名称无效。')
            value=dict(value)
            if any(k in value for k in ('env','headers','http_headers','bearer_token')):raise ValueError('此包包含环境变量或认证字段；请使用原生工作端管理认证配置。')
            # Expand the manifest's own root only, never arbitrary environment variables.
            value={k:([a.replace('${PLUGIN_ROOT}',str(dest)) for a in v] if k=='args' and isinstance(v,list) else v.replace('${PLUGIN_ROOT}',str(dest)) if isinstance(v,str) else v) for k,v in value.items()}
            if value.get('type') in {'http','streamable-http'}:value={'transport':'http','url':value.get('url')}
            value.pop('type',None)
            if value.get('command'):value.setdefault('cwd',str(dest))
            normalized[key]=mcp_config(value)
        return normalized

    def restore(self,db,p):
        current=self.c.get(db,'capability',p.get('id'))
        previous=self.c.get(db,'capability_version',p.get('target_revision'))
        if (current['revision']!=p.get('revision') or current['id']!=previous['capability_id']
                or current['revision']==previous['revision']):
            raise ValueError('请选择此能力的历史版本，并核对当前修订。')
        verify_bundle(previous)
        row={k:v for k,v in previous.items() if k!='capability_id'}
        row.update(id=current['id'],revision=uid(),enabled=False,updated=time.time())
        self.c.put(db,'capability_version',dict(row,id=row['revision'],capability_id=row['id']))
        self.c.put(db,'capability',row)
        return {'capability':row,'restored_from':previous['revision'],'no_dispatch':True}

    def enable(self,db,p):
        row=self.c.get(db,'capability',p.get('id'))
        if row['revision']!=p.get('revision'):raise ValueError('能力版本已改变，请刷新。')
        if type(p.get('enabled')) is not bool:raise ValueError('启用状态无效。')
        if row['enabled']!=p['enabled']:row['activation']=uid()
        row.update(enabled=p['enabled'],updated=time.time());self.c.put(db,'capability',row)
        return {'capability':row,'no_dispatch':True,'existing_tasks_unchanged':True}

    def remove(self,db,p):
        row=self.c.get(db,'capability',p.get('id'))
        if row['revision']!=p.get('revision'):raise ValueError('能力版本已改变，请刷新。')
        # Remove the catalog entry only; immutable versions referenced by tasks stay.
        db.execute("DELETE FROM collaboration WHERE kind='capability' AND id=?",(row['id'],))
        return {'removed':row['id'],'frozen_versions_preserved':True,'source_files_preserved':True,'no_dispatch':True}


def verify_bundle(row):
    root=Path(row['path'])
    for name,expected in row['files'].items():
        target=root/name
        if not target.is_file() or target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest()!=expected:
            raise ValueError('能力内容已变化：'+row['name']+' / '+name+'；未加载或发送。')
