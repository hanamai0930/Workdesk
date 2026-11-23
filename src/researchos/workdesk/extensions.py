"""User-selected local MCP connections, separate from native provider capabilities.

Saving, enabling and reading never launch a process. Each explicit check/call is
one durable, finite operation; restart and transport failures never replay it.
"""
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

from .paths import clean_env
from .store import uid


class Extensions:
    def __init__(self, service):
        self.s=service;self.c=service.collaboration;self.workers={}
        with service.store.write() as db:
            for run in self.c.all(db,'extension_run'):
                if run['state'] in {'QUEUED','RUNNING','STOP_REQUESTED'}:
                    run.update(state='UNKNOWN',error='服务重启；原调用未自动重发。',ended=time.time())
                    self.c.put(db,'extension_run',run)

    def handlers(self):
        return {'extension.save':self.save,'extension.enable':self.enable,
                'extension.run':self.prepare,'extension.stop':self.stop,'extension.remove':self.remove}

    def catalog(self):
        with self.s.store.read() as db:
            entries=self.c.all(db,'extension');runs=self.c.all(db,'extension_run')[:40]
            capabilities=self.s.capabilities.catalog(db);scenes=self.s.scenes.catalog(db)
        return {'extensions':entries,'runs':runs,'capabilities':capabilities,'scenes':scenes,'no_dispatch':True,
                'execution_path':'EXPLICIT_LOCAL_MCP_OPERATION',
                'task_auto_mount':'EXPLICIT_SCENE_SELECTION','credentials':'NOT_READ_OR_FORWARDED',
                'native':[{'provider':p,'source':'官方SDK / CLI原生路径',
                           'management':'插件 / Skills 随原生工作端配置与当前任务权限；用户插件仍属用户来源，不由此处覆盖。'}
                          for p in ('codex','qwen','deepseek')]}

    def save(self,db,p):
        from .capabilities import mcp_config
        config=mcp_config(p.get('config'))
        name=p.get('name');source=p.get('source')
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',name):
            raise ValueError('名称须以英文字母开头，只含字母、数字、下划线或连字符，最多64字符。')
        if not isinstance(source,str) or not source.strip() or len(source)>1000:raise ValueError('请填写扩展来源。')
        entries=self.c.all(db,'extension');identity=p.get('id') or 'extension:'+uid()
        if p.get('id'):
            old=self.c.get(db,'extension',identity)
            if p.get('revision')!=old['revision']:raise ValueError('MCP 配置版本已改变，请刷新。')
        if any(e['name']==name and e['id']!=identity for e in entries):raise ValueError('扩展名称已存在。')
        entry={'id':identity,'name':name,'source':source.strip(),'kind':'USER_MCP',
               'config':config,'revision':uid(),
               'enabled':False,'updated':time.time()}
        self.c.put(db,'extension',entry)
        return {'extension':entry,'no_dispatch':True,'configuration_change_disables':True}

    def remove(self,db,p):
        e=self.c.get(db,'extension',p.get('id'))
        if p.get('revision')!=e['revision']:raise ValueError('MCP 配置版本已改变，请刷新。')
        if any(r['extension']['id']==e['id'] and r['state'] in {'QUEUED','RUNNING','STOP_REQUESTED'} for r in self.c.all(db,'extension_run')):raise ValueError('此服务还有操作在途。')
        db.execute("DELETE FROM collaboration WHERE kind='extension' AND id=?",(e['id'],))
        return {'removed':e['id'],'history_preserved':True,'no_dispatch':True}

    def enable(self,db,p):
        e=self.c.get(db,'extension',p.get('id'))
        if type(p.get('enabled')) is not bool:raise ValueError('启停状态须为布尔值。')
        if p.get('revision')!=e['revision']:raise ValueError('扩展配置已改变，请刷新。')
        e.update(enabled=p['enabled'],updated=time.time());self.c.put(db,'extension',e)
        return {'extension':e,'no_dispatch':True,'in_flight':'Existing operations retain their frozen configuration.'}

    def prepare(self,db,p):
        e=self.c.get(db,'extension',p.get('id'))
        if not e['enabled']:raise ValueError('扩展已停用。')
        if p.get('revision')!=e['revision']:raise ValueError('扩展配置已改变，请刷新后重新确认。')
        if any(r['state'] in {'QUEUED','RUNNING','STOP_REQUESTED'} for r in self.c.all(db,'extension_run')):
            raise ValueError('仍有扩展操作在途，先停止或等待回收。')
        operation=p.get('operation','check');tool=p.get('tool');arguments=p.get('arguments',{})
        if operation not in {'check','call'}:raise ValueError('未知扩展操作。')
        if operation=='call':
            if not isinstance(arguments,dict) or len(json.dumps(arguments).encode())>65536:raise ValueError('工具参数须为最多64KiB的JSON对象。')
            prior=next((r for r in self.c.all(db,'extension_run') if r['extension']['id']==e['id'] and r['extension']['revision']==e['revision'] and r['state']=='SUCCEEDED'),None)
            if not prior or tool not in [t['name'] for t in prior.get('tools',[])]:raise ValueError('请先检查当前版本，并选择已列出的准确工具名。')
        run={'id':'extension-run:'+uid(),'extension':e,'operation':operation,'tool':tool if operation=='call' else None,
             'arguments':arguments if operation=='call' else {},'state':'QUEUED','created':time.time(),
             'limits':{'seconds':15,'tool_calls':1 if operation=='call' else 0,'output_bytes':1048576,'model_requests':0},
             'material_recipient':e['name'],'no_automatic_retry':True}
        self.c.put(db,'extension_run',run)
        return {'run_id':run['id'],'launch_extension':run['id'],'state':'QUEUED'}

    def stop(self,db,p):
        run=self.c.get(db,'extension_run',p.get('run_id'));worker=self.workers.get(run['id'])
        if run['state'] not in {'QUEUED','RUNNING','STOP_REQUESTED'}:return {'run_id':run['id'],'state':run['state']}
        if worker:worker['stop'].set()
        run.update(state='STOP_REQUESTED');self.c.put(db,'extension_run',run)
        return {'run_id':run['id'],'state':'STOP_REQUESTED','remote_side_effects':'UNKNOWN'}

    def active(self):return any(w['thread'].is_alive() for w in self.workers.values())

    def launch(self,identity):
        stop=threading.Event();thread=threading.Thread(target=self.run,args=(identity,stop),daemon=True)
        self.workers[identity]={'stop':stop,'thread':thread}
        try:thread.start()
        except RuntimeError:
            with self.s.store.write() as db:
                run=self.c.get(db,'extension_run',identity);run.update(state='FAILED_NO_START',ended=time.time())
                self.c.put(db,'extension_run',run)

    def close(self):
        for w in self.workers.values():w['stop'].set()
        for w in self.workers.values():w['thread'].join(timeout=3)

    def run(self,identity,stop):
        process=None;job=None;run=None;sent=False;tools=[];result=None;reader_thread=None
        try:
            with self.s.store.write() as db:
                run=self.c.get(db,'extension_run',identity);run.update(state='RUNNING',started=time.time())
                self.c.put(db,'extension_run',run)
            config=run['extension']['config'];deadline=time.monotonic()+15;messages=queue.Queue(maxsize=256)
            env=clean_env()
            # Proxy settings are not implicitly granted to third-party extensions.
            for key in list(env):
                if key.upper().endswith('_PROXY'):env.pop(key)
            if config.get('transport')=='http':
                argv=[str(self.s.locations.node),str(Path(__file__).with_name('workers')/'mcp_http_proxy.mjs'),
                      str(self.s.locations.runtime/'qwen/node_modules/@modelcontextprotocol/sdk'),config['url']]
                cwd=self.s.locations.data
            else:argv=[config['command'],*config['args']];cwd=config['cwd']
            process=subprocess.Popen(argv,cwd=cwd,env=env,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            from .adapters import owned
            job=owned(self.s.locations,process)
            def reader():
                total=0
                try:
                    while True:
                        line=process.stdout.readline(1048577)
                        if not line:break
                        total+=len(line)
                        if total>1048576:raise ValueError('扩展输出超过1MiB上限。')
                        messages.put_nowait(json.loads(line))
                except (ValueError,queue.Full):
                    try:messages.put_nowait({'_failure':'MCP输出不是有效JSON行，或超过限制。'})
                    except queue.Full:pass
                finally:
                    try:messages.put_nowait({'_closed':True})
                    except queue.Full:pass
            reader_thread=threading.Thread(target=reader,daemon=True);reader_thread.start()
            def send(value):
                if stop.is_set():raise InterruptedError('扩展操作已请求停止。')
                if time.monotonic()>=deadline:raise TimeoutError('扩展操作超过15秒。')
                failure=[]
                def write():
                    try:
                        process.stdin.write((json.dumps(value,ensure_ascii=False)+'\n').encode('utf-8'));process.stdin.flush()
                    except Exception as exc:failure.append(exc)
                writer=threading.Thread(target=write,daemon=True);writer.start()
                while writer.is_alive():
                    if stop.is_set():raise InterruptedError('扩展操作已请求停止。')
                    if time.monotonic()>=deadline:raise TimeoutError('扩展操作超过15秒。')
                    writer.join(timeout=.1)
                if failure:raise failure[0]
            def request(number,method,params):
                nonlocal sent
                if method=='tools/call':sent=True  # Partial writes are conservatively unknown too.
                send({'jsonrpc':'2.0','id':number,'method':method,'params':params})
                while True:
                    if stop.is_set():raise InterruptedError('扩展操作已请求停止。')
                    if time.monotonic()>=deadline:raise TimeoutError('扩展操作超过15秒。')
                    try:message=messages.get(timeout=.1)
                    except queue.Empty:continue
                    if not isinstance(message,dict):raise ValueError('MCP响应格式无效。')
                    if message.get('_failure') or message.get('_closed'):raise ValueError(message.get('_failure','扩展进程已退出。'))
                    if 'method' in message and 'id' in message:
                        send({'jsonrpc':'2.0','id':message['id'],'error':{'code':-32601,'message':'Workdesk does not grant server-initiated requests.'}})
                    if message.get('id')==number and 'method' not in message:
                        if 'error' in message:raise ValueError('MCP返回错误：'+json.dumps(message['error'],ensure_ascii=False)[:2000])
                        return message.get('result')
            initialized=request(1,'initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'workdesk','version':'1'}})
            if not isinstance(initialized,dict) or not isinstance(initialized.get('protocolVersion'),str):raise ValueError('MCP初始化未返回协议版本。')
            send({'jsonrpc':'2.0','method':'notifications/initialized'})
            listing=request(2,'tools/list',{})
            if not isinstance(listing,dict) or not isinstance(listing.get('tools'),list):raise ValueError('工具列表无效。')
            tools=listing['tools'];cursors=set()
            while listing.get('nextCursor'):
                cursor=listing['nextCursor']
                if cursor in cursors or len(cursors)>=30:raise ValueError('工具目录分页循环或超过30页。')
                cursors.add(cursor);listing=request(10+len(cursors),'tools/list',{'cursor':cursor})
                if not isinstance(listing,dict) or not isinstance(listing.get('tools'),list):raise ValueError('工具目录分页无效。')
                tools+=listing['tools']
            if any(not isinstance(t,dict) or not isinstance(t.get('name'),str) for t in tools):raise ValueError('工具名无效。')
            if run['operation']=='call':
                if run['tool'] not in [t['name'] for t in tools]:raise ValueError('当前进程未提供选定工具。')
                result=request(3,'tools/call',{'name':run['tool'],'arguments':run['arguments']})
                if not isinstance(result,dict):raise ValueError('工具返回格式无效。')
            state='TOOL_ERROR' if isinstance(result,dict) and result.get('isError') else 'SUCCEEDED';error=None
        except Exception as exc:
            state='UNKNOWN' if sent else 'STOPPED' if stop.is_set() else 'FAILED';error=str(exc)[:2000]
        finally:
            cleanup='NO_PROCESS_STARTED'
            try:
                if job:
                    closed=job.close();cleanup='OWNED_PROCESS_SCOPE_CLOSED' if closed.get('active_processes')==0 else 'UNKNOWN'
                elif process:
                    process.kill();cleanup='DIRECT_PROCESS_TERMINATED'
                if process:process.wait(timeout=2)
            except Exception:cleanup='UNKNOWN'
            if reader_thread:reader_thread.join(timeout=.2)
            if process:
                for stream in (process.stdin,process.stdout):
                    try:stream.close()
                    except OSError:pass
            if run:
                with self.s.store.write() as db:
                    run.update(state=state,error=error,tools=tools,result=result,ended=time.time(),
                               process_cleanup=cleanup,tool_request_sent=sent,remote_side_effects='UNKNOWN' if sent else 'NO_TOOL_CALL_SENT')
                    if cleanup=='UNKNOWN':run['state']='UNKNOWN'
                    self.c.put(db,'extension_run',run)
