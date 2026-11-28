"""Persistent loopback control process; frontend versions reconnect without owning workers."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from contextlib import closing
import argparse
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import threading
import traceback
from urllib.parse import urlsplit,parse_qs

from .paths import Locations,atomic_json
from .service import Service
from . import workspace
from .persistence import location_fence
from .loopback import FetchSafeHTTPServer


APP='workdesk-control-v1'


class Api(FetchSafeHTTPServer):
    daemon_threads=True
    def __init__(self,service):
        self.service=service;self.control=secrets.token_urlsafe(32);self.viewer=secrets.token_urlsafe(32)
        super().__init__(('127.0.0.1',0),Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,status,value):
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
    def auth(self,write=False):
        if self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port):return False
        token=self.headers.get('Authorization','')
        allowed=[self.server.control] if write else [self.server.control,self.server.viewer]
        return any(secrets.compare_digest(token,'Bearer '+candidate) for candidate in allowed)
    def do_GET(self):
        parsed=urlsplit(self.path)
        if parsed.path=='/health':
            from .instance import load_profile
            profile=load_profile(self.server.service.locations.base)
            self.reply(200,{'app':APP,'protocol':1,'instance':self.server.service.instance,
                           'program_base':str(self.server.service.locations.base),
                           'data_root':str(self.server.service.locations.data),
                           'profile_id':profile['instance_id'] if profile else None,
                           'implementation':self.server.service.implementation});return
        if not self.auth():self.reply(403,{'error':'需要本地控制服务凭据。'});return
        args=parse_qs(parsed.query)
        try:
            if parsed.path=='/api/v1/instance':
                from .instance import snapshot
                value=snapshot(self.server.service.locations,self.server.service.instance,self.server.service.implementation)
            elif parsed.path=='/api/v1/connection-setup':
                from .setup_keys import status
                value=status(self.server.service)
            elif parsed.path=='/api/v1/workspace':value=self.server.service.catalog(args.get('view',[None])[0])
            elif parsed.path=='/api/v1/independent-work':value=self.server.service.independent.catalog()
            elif parsed.path=='/api/v1/settings-qwen-quota-status':
                value=self.server.service.resources.qwen_quota_status()
            elif parsed.path=='/api/v1/settings-network-time':
                from .network_time import clock
                value=clock.read()
            elif parsed.path=='/api/v1/settings-usage':
                from .settings_usage import overview
                value=overview(self.server.service,args.get('days',[7])[0])
            elif parsed.path=='/api/v1/settings-token-usage':
                from .usage_report import report
                value=report(self.server.service,*(args.get(key,[default])[0] for key,default in
                    (('provider','all'),('days','365'),('page','1'),('partner_id','all'),('api_provider','all'),('account_id','all'),('route','all'))))
            elif parsed.path=='/api/v1/work-usage':
                from .work_usage import report
                value=report(self.server.service,args.get('id',[None])[0])
            elif parsed.path=='/api/v1/collaboration':value=self.server.service.collaboration.catalog(args.get('view',[None])[0])
            elif parsed.path=='/api/v1/integration-preview':value=self.server.service.collaboration.integration_preview(args.get('id',[''])[0])
            elif parsed.path=='/api/v1/operations':value=self.server.service.operations.catalog()
            elif parsed.path=='/api/v1/handoff-preview':value=self.server.service.operations.preview(args.get('id',[''])[0])
            elif parsed.path=='/api/v1/persistence':value=self.server.service.persistence.catalog()
            elif parsed.path=='/api/v1/verification':value=self.server.service.verification.catalog()
            elif parsed.path=='/api/v1/extensions':value=self.server.service.extensions.catalog(args.get('run_id',[None])[0],args.get('task_id',[None])[0])
            elif parsed.path=='/api/v1/scenes':
                with self.server.service.store.read() as db:value=self.server.service.scenes.catalog(db)
            elif parsed.path=='/api/v1/ui-state':value=self.server.service.ui_features.catalog()
            elif parsed.path=='/api/v1/record-catalog':
                from .record_catalog import catalog
                value=catalog(self.server.service,query=args.get('q',[''])[0],mode=args.get('mode',['all'])[0],archive=args.get('archive',['all'])[0],cursor=args.get('cursor',[''])[0],limit=args.get('limit',[40])[0],scope=args.get('scope',['ordinary'])[0],project=args.get('project',[None])[0])
            elif parsed.path=='/api/v1/resource-space':
                value=self.server.service.resource_space.catalog(args.get('q',[''])[0],
                    args.get('collection_id',[None])[0],args.get('view',['active'])[0])
            elif parsed.path=='/api/v1/resource-space/item':
                value=self.server.service.resource_space.item(args.get('id',[''])[0])
            elif parsed.path=='/api/v1/resource-space/file':
                value=self.server.service.resource_space.file(args.get('id',[''])[0])
            elif parsed.path=='/api/v1/file-inspect':value=self.server.service.file_inspector.inspect(args.get('project',[''])[0],args.get('file',[''])[0])
            elif parsed.path=='/api/v1/file-selections':value=self.server.service.file_inspector.selections(args.get('project',[''])[0])
            elif parsed.path=='/api/v1/result-catalog':value=self.server.service.results.catalog(args.get('task',[None])[0])
            elif parsed.path=='/api/v1/result-preview':value=self.server.service.results.preview(args.get('id',[''])[0],args.get('file',[''])[0],args.get('snapshot_hash',[''])[0])
            elif parsed.path=='/api/v1/fixed-artifact-preview':
                value=self.server.service.work_artifacts.preview(*(args.get(k,[''])[0] for k in ('item','run','version','file')))
            elif parsed.path=='/api/v1/projects':value=self.server.service.projects_manager.catalog()
            elif parsed.path=='/api/v1/work-items' and args.get('view')==['controls'] and args.get('id'):value=self.server.service.work_items.control_catalog(args['id'][0])
            elif parsed.path=='/api/v1/work-items':value=self.server.service.work_items.catalog(args.get('project',[None])[0],args.get('id',[None])[0])
            elif parsed.path=='/api/v1/cooperation-conversation':
                value=self.server.service.cooperation_conversation.view(args.get('id',[''])[0])
            elif parsed.path=='/api/v1/work-items/acceptance-notices':value=self.server.service.work_items.acceptance_notices()
            elif parsed.path=='/api/v1/work-items/history':value=self.server.service.work_items.history_catalog(*(args.get(k,[None])[0] for k in ('id','project','scope')))
            elif parsed.path=='/api/v1/work-items/public-thoughts':value=self.server.service.work_items.public_thoughts(args.get('id',[None])[0],args.get('run',[None])[0])
            elif parsed.path=='/api/v1/work-items/trajectory':value=self.server.service.work_items.trajectory(*(args.get(k,[None])[0] for k in ('id','run','before')))
            elif parsed.path=='/api/v1/work-items/tool-record':value=self.server.service.work_items.tool_record(*(args.get(k,[None])[0] for k in ('id','run','call')))
            elif parsed.path=='/api/v1/execution-resources':value=self.server.service.resources.public_catalog(item_id=args.get('item',[None])[0])
            elif parsed.path=='/api/v1/management':value=self.server.service.management.catalog()
            elif parsed.path=='/api/v1/management-request':value=self.server.service.management.detail(args.get('id',[''])[0])
            elif parsed.path=='/api/v1/appearance':value=self.server.service.ui_features.appearance.catalog()
            elif parsed.path=='/api/v1/planning':value=self.server.service.planning.catalog()
            elif parsed.path=='/api/v1/libraries':value=self.server.service.libraries.catalog()
            elif parsed.path=='/api/v1/monitor':value=self.server.service.monitor.catalog(args.get('scope',['attention'])[0])
            elif parsed.path=='/api/v1/conversation':value=self.server.service.ui_features.export(args.get('kind',[''])[0],args.get('id',[''])[0])
            elif parsed.path=='/api/v1/work-files':value=self.server.service.ui_features.files(args.get('project',[''])[0],args.get('folder',[''])[0])
            elif parsed.path=='/api/v1/work-preview':value=self.server.service.ui_features.preview(args.get('project',[''])[0],args.get('file',[''])[0])
            elif parsed.path=='/api/v1/settings':value={'storage':self.server.service.local_storage.catalog(),'extensions':self.server.service.extensions.catalog(),'health':self.server.service.health(),'appearance':self.server.service.ui_features.appearance.catalog()}
            elif parsed.path=='/api/v1/task':value=self.server.service.detail(args.get('id',[''])[0],int(args.get('after',['0'])[0]))
            elif parsed.path=='/api/v1/receipt':
                with self.server.service.store.read() as db:
                    row=db.execute('SELECT kind,receipt,created FROM commands WHERE id=?',(args.get('id',[''])[0],)).fetchone()
                value={'state':'RECORDED','kind':row['kind'],'receipt':json.loads(row['receipt']),'created':row['created']} if row else {'state':'UNKNOWN_OR_NOT_COMMITTED','no_resend':True}
            elif parsed.path=='/api/v1/receipts':
                with self.server.service.store.read() as db:rows=db.execute('SELECT id,kind,receipt,created FROM commands ORDER BY created DESC LIMIT 40').fetchall()
                value={'commands':[{**dict(r),'receipt':json.loads(r['receipt'])} for r in rows],'no_dispatch':True}
            elif parsed.path=='/api/v1/material':
                task=self.server.service.store.task(args.get('id',[''])[0]);name=args.get('file',[''])[0]
                if name not in task['spec']['materials']:raise ValueError('文件不在此任务准确材料清单内。')
                value=workspace.material_view(self.server.service.locations.data/'tasks'/task['id']/'input',name)
            elif parsed.path=='/api/v1/artifact':
                attempt=self.server.service.store.attempt(args.get('attempt',[''])[0]);name=args.get('file',[''])[0]
                result=attempt['result'] or {}
                if name not in result.get('manifest',{}):raise ValueError('文件不在该尝试产物清单内。')
                root=self.server.service.locations.data/'tasks'/attempt['task_id']/'attempts'/attempt['id']/'files'
                value=workspace.material_view(root,name)
                if value['sha256']!=result['manifest'][name]['sha256']:raise ValueError('保存产物哈希不一致。')
            else:self.reply(404,{'error':'接口不存在。'});return
            self.reply(200,value)
        except (ValueError,KeyError,TypeError,LookupError,OSError) as exc:self.reply(409,{'error':str(exc),'state':'UNKNOWN'})
    def do_POST(self):
        if not self.auth(True):self.reply(403,{'error':'需要Human控制端凭据；工作端工具凭据无权调用。'});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<=0 or self.headers.get_content_type()!='application/json':raise ValueError('请求正文不支持。')
            if self.path.startswith('/api/v1/connection-setup/') and size>4096:raise ValueError('连接信息过长。')
            value=json.loads(self.rfile.read(size))
            if not isinstance(value,dict):raise ValueError('请求须为对象。')
            if self.path=='/api/v1/connection-setup/key':
                from .setup_keys import save_key
                result=save_key(self.server.service,value)
            elif self.path=='/api/v1/connection-setup/account-start':
                from .setup_account import start_work_login
                result=start_work_login(self.server.service,value)
            elif self.path in ('/api/v1/connection-setup/account-poll','/api/v1/connection-setup/account-cancel'):
                from .setup_account import update_work_login
                result=update_work_login(self.server.service,value,cancel=self.path.endswith('/account-cancel'))
            elif self.path=='/api/v1/command':result=self.server.service.command(value.get('kind'),value.get('payload'),value.get('id'))
            elif self.path=='/api/v1/coordinator-text-rework-recovery':
                from .work_coordinator_handoff_text_rework_recovery import inspect
                result=inspect(self.server.service,value)
            elif self.path=='/api/v1/post-round-v3-save-recovery':
                from .work_coordinator_handoff_post_round_v3_save_recovery import inspect
                result=inspect(self.server.service,value)
            elif self.path=='/api/v1/post-round-v3-responsibility-recovery':
                from .work_coordinator_handoff_post_round_v3_responsibility_recovery import inspect
                result=inspect(self.server.service,value)
            elif self.path=='/api/v1/profile-check':result=self.server.service.profiles.check(value.get('provider'))
            elif self.path=='/api/v1/resource-quota-check':result=self.server.service.resources.quota_check(value)
            elif self.path=='/api/v1/settings-qwen-quota':result=self.server.service.resources.qwen_quota_check(value)
            elif self.path=='/api/v1/settings-qwen-quota-login':result=self.server.service.resources.qwen_quota_login(value)
            elif self.path=='/api/v1/settings-qwen-quota-cancel':result=self.server.service.resources.qwen_quota_cancel(value)
            elif self.path=='/api/v1/fixed-public-skills':
                if value:raise ValueError('公开目录读取不接收任务内容或外部地址。')
                result=self.server.service.fixed_preparation.public_skills.catalog()
            elif self.path=='/api/v1/fixed-capability-preview':
                result=self.server.service.fixed_preparation.preview_local(value)
            else:self.reply(404,{'error':'接口不存在。'});return
            self.reply(200,result)
            if self.server.service.retiring:threading.Thread(target=self.server.shutdown,daemon=True).start()
        except (ValueError,KeyError,TypeError,LookupError,OSError) as exc:
            self.reply(409,{'error':str(exc),'state':'UNKNOWN'})
            if self.server.service.retiring:threading.Thread(target=self.server.shutdown,daemon=True).start()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--base');parser.add_argument('--data');parser.add_argument('--user-root');args=parser.parse_args()
    locations=Locations.resolve(args.base,args.data,user=args.user_root);locations.data.mkdir(parents=True,exist_ok=True)
    lock=open(locations.data/'backend.lock','a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:return  # Never start a second owner or recover its active tasks.
    location_fence(locations.base,locations.data,locations.data_pointer)
    database=locations.data/'tasks.sqlite3'
    if database.exists():
        # The exclusive OS owner lock is held. Permit SQLite to roll back a hot
        # journal left by a local crash before checking the retirement fence;
        # mode=rw never creates a missing replacement database.
        with closing(sqlite3.connect(database.as_uri()+'?mode=rw',uri=True)) as connection:
            exists=connection.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone()
            retired=connection.execute("SELECT value FROM meta WHERE key='retired_to'").fetchone() if exists else None
        if retired:raise ValueError('此旧台账已切换到另一数据目录；保留只读证据，不重新启动旧执行所有者。')
    service=Service(locations);server=Api(service)
    atomic_json(locations.data/'backend.json',{'app':APP,'protocol':1,'instance':service.instance,
                'port':server.server_port,'control':server.control,'viewer':server.viewer,'pid':os.getpid()})
    try:server.serve_forever(poll_interval=.3)
    finally:service.close();server.server_close();lock.close()


if __name__=='__main__':
    try:main()
    except Exception:
        # Invalid development configuration must not create a daily diagnostic directory.
        try:
            path=Locations.resolve().data;path.mkdir(parents=True,exist_ok=True)
            (path/'startup-error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        except (ValueError,OSError):pass
        raise
