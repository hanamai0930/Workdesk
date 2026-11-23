"""Frontend connector. Starts only the control service, never a task or login."""
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request,urlopen

from .paths import Locations,clean_env,implementation_revision


# Existing in-flight work must remain stoppable during a code/root transition.
OWNER_CONTROLS={'service.reload','task.stop','task.pause','task.cancel','task.reconcile',
                'approval.resolve','native.resolve','chat.api-stop'}


class Client:
    def __init__(self,base=None,data=None):
        self.locations=Locations.resolve(base,data);self.lock=threading.Lock();self.dynamic_data=data is None
        self.expected_implementation=implementation_revision(self.locations)
    def discover(self):
        try:
            state=json.loads((self.locations.data/'backend.json').read_text(encoding='utf-8'))
            if type(state['port']) is not int or not 1024<=state['port']<=65535:return None
            if not all(isinstance(state.get(k),str) and len(state[k])==43 for k in ('control','viewer')):return None
            with urlopen('http://127.0.0.1:'+str(state['port'])+'/health',timeout=.7) as response:health=json.load(response)
            if health.get('app')=='workdesk-control-v1' and health.get('instance')==state['instance'] and health.get('protocol')==1:
                state['_health']=health
                return state
        except (OSError,ValueError,KeyError,TypeError):pass
        return None
    def ensure(self):
        with self.lock:
            if self.dynamic_data:self.locations=Locations.resolve(self.locations.base)
            state=self.discover()
            if state:return state
            env=clean_env();env['PYTHONPATH']=str(self.locations.base/'src')
            executable=self.locations.python
            if os.name=='nt' and executable.with_name('pythonw.exe').exists():executable=executable.with_name('pythonw.exe')
            command=[str(executable),'-B','-X','utf8','-m','researchos.workdesk.server',
                              '--base',str(self.locations.base),'--data',str(self.locations.data)]
            if self.locations.user:command+=['--user-root',str(self.locations.user)]
            backend=subprocess.Popen(command,
                             cwd=self.locations.base,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            # A clean first launch also writes the implementation snapshot.
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                state=self.discover()
                if state:return state
                if backend.poll() is not None:break
                time.sleep(.1)
            raise ConnectionError('后端未能启动或身份未核对；未启动任务，请查看后端诊断。')
    def request(self,route,value=None):
        state=self.ensure()
        if value is not None and not (route=='command' and value.get('kind') in OWNER_CONTROLS):
            self.assert_current_owner(state)
        return self.raw_request(state,route,value)

    def assert_current_owner(self,state):
        """Reads can inspect old history; new mutations require this product version.

        A legacy owner can be reloaded explicitly while idle. We never kill it,
        replay a command, or dispatch through another checkout silently.
        """
        health=state.get('_health') or {}
        base=health.get('program_base');revision=health.get('implementation')
        if not base:base=self.raw_request(state,'persistence').get('program_base')
        if not revision:revision=self.raw_request(state,'workspace').get('health',{}).get('implementation')
        if not base or Path(base).resolve()!=self.locations.base or revision!=self.expected_implementation:
            raise ValueError('控制服务来自旧工程或旧代码版本；本次操作未发送。请在设置页显式重载空闲控制服务；在途任务仍可停止或处理审批。')

    def raw_request(self,state,route,value=None):
        data=None if value is None else json.dumps(value,ensure_ascii=False).encode('utf-8')
        request=Request('http://127.0.0.1:'+str(state['port'])+'/api/v1/'+route,data=data,
                        headers={'Authorization':'Bearer '+(state['viewer'] if value is None else state['control']),
                                 'Content-Type':'application/json'})
        try:
            with urlopen(request,timeout=25) as response:
                raw=response.read(32*1024*1024+1)
                if len(raw)>32*1024*1024:raise ValueError('后端结果超过显示范围，请按事件游标读取。')
                return json.loads(raw)
        except HTTPError as exc:
            try:message=json.loads(exc.read(65536)).get('error','后端拒绝请求。')
            except ValueError:message='后端拒绝请求。'
            raise ValueError(message) from None
        # Transport failure is propagated; a command is never automatically resent.
