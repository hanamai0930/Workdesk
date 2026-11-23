"""Structured management calls to the same bundled official Qwen Code CLI."""
import json
from pathlib import Path
import subprocess,time
from .paths import clean_env


def qwen(loc,op,attempt,stop,on_start=lambda cancel:None):
    _,group,action=op['method'].split('.');p=op['params'];args=[group,action]
    inventory = group == 'extensions' and action == 'workspace'
    def text(key):
        value=p.get(key)
        if not isinstance(value,str) or not value.strip() or value.startswith('-') or '\0' in value:raise ValueError('请提供有效的'+key+'。')
        return value
    if action not in {'list', 'workspace'}:
        if group=='extensions' and action=='install':
            args+=[text('source'),'--scope','workspace','--consent']
        elif group=='mcp' and action=='add':
            args+=[text('name'),text('commandOrUrl'),'--scope','project']
            transport=p.get('transport')
            if transport:
                if transport not in {'stdio','sse','http'}:raise ValueError('请选择stdio、sse或http。')
                args+=['--transport',transport]
            extra=p.get('args',[])
            if not isinstance(extra,list) or any(not isinstance(x,str) or '\0' in x for x in extra):raise ValueError('命令参数须为文本列表。')
            if extra:args+=['--',*extra]
        else:
            if action!='reconnect' or p.get('name'):args+=[text('name')]
            if group=='extensions' and action in {'enable','disable'}:args+=['--scope','workspace']
            if group=='mcp' and action=='remove':args+=['--scope','project']
    argv=[str(loc.node),str(loc.runtime/'qwen/node_modules/@qwen-code/sdk/dist/cli/cli.js'),*args]
    if inventory:
        if p:raise ValueError('工作区扩展查询使用本次任务的原目录。')
        argv=[str(loc.node),str(Path(__file__).with_name('workers')/'qwen_extension_inventory.mjs'),
              str(loc.runtime/'qwen/node_modules/@qwen-code/sdk'),attempt['workspace']]
    env=clean_env();env.pop('PYTHONNOUSERSITE',None)
    proc=subprocess.Popen(argv,cwd=attempt['workspace'],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    from .adapters import owned
    job=owned(loc,proc);start=time.monotonic();inventory_result=None
    on_start(proc.kill)
    try:
        while True:
            if stop.is_set():raise ValueError('原生管理操作已停止；不自动重发。')
            try:out,err=proc.communicate(timeout=.2);break
            except subprocess.TimeoutExpired:
                if time.monotonic()-start>90:raise TimeoutError('官方管理命令回执超时；请核对配置，不自动重发。')
        if proc.returncode:raise ValueError((err or out)[-1800:])
        if inventory:
            result=json.loads(out)
            if (result.get('source')!='QWEN_OFFICIAL_WORKSPACE' or
                    Path(result['projection']['workspaceCwd']).resolve()!=Path(attempt['workspace']).resolve()):
                raise ValueError('官方扩展查询未返回本次任务的原工作区。')
            inventory_result=result
            return result
        scope='NATIVE_INVENTORY' if action=='list' else 'PROJECT'
        if group=='extensions':
            scope={'install':'WORKSPACE','enable':'WORKSPACE','disable':'WORKSPACE',
                   'uninstall':'NATIVE_EXTENSION_CONFIG'}.get(action,scope)
        return {'native_cli':True,'scope':scope,'effect':'NEXT_RUNTIME_START','stdout':out,'stderr':err,'exit_code':proc.returncode}
    finally:
        cleanup=job.close() if job else None
        if proc.poll() is None:proc.kill()
        proc.wait(timeout=5)
        if inventory_result is not None:
            inventory_result['native_local_exit']={'exit_code':proc.returncode,'owned_job':cleanup,
                                                 'exited':proc.poll() is not None}
