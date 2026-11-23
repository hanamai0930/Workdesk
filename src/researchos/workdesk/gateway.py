"""Loopback SDK gateway: exact tools, official endpoints and durable request budgets."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request,build_opener,HTTPRedirectHandler

from .store import canonical, digest
from .tools import DEFINITIONS
from .context import request_reserve
from .model_catalog import ENDPOINTS
from .loopback import FetchSafeHTTPServer


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def urlopen(request,timeout):
    # Never forward an Authorization header to an unapproved redirect recipient.
    return build_opener(NoRedirect()).open(request,timeout=timeout)
FIELDS={'model','messages','tools','tool_choice','parallel_tool_calls','stream','stream_options','temperature','top_p',
        'max_tokens','max_completion_tokens','thinking','enable_thinking','reasoning_effort','frequency_penalty','presence_penalty','stop'}


class Gateway:
    def __init__(self,store,task,attempt,tools,stop,secret,limits,fake=None):
        self.store,self.task,self.attempt,self.tools,self.stop=store,task,attempt,tools,stop
        self.secret,self.limits,self.fake=secret,limits,fake
        self.token=secrets.token_urlsafe(24);self.responses=[];self.lock=threading.Lock()
        self.configuration_mismatches=[]
        self.refusals=[]
        gate=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def end_headers(self):
                self.response_started=True;super().end_headers()
            def answer(self,code,value):
                raw=canonical(value).encode();self.send_response(code);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_POST(self):
                if self.headers.get('Authorization')!='Bearer '+gate.token:
                    self.answer(403,{'error':'Local task capability required'});return
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if size<=0:raise ValueError('Expected nonempty request')
                    if size>48*1024*1024:raise ValueError('Native request exceeds local transport admission, no truncation or fallback')
                    if self.path=='/tool' and size>2*1024*1024:raise ValueError('Tool payload outside file-tool scope')
                    data=json.loads(self.rfile.read(size))
                    if not isinstance(data,dict):raise ValueError('Expected object')
                    if self.path=='/tool':
                        self.answer(200,gate.tools.execute(data.get('name'),data.get('arguments',{}),data.get('call_id')))
                    elif self.path.startswith('/native/') and getattr(gate,'native_bridge',None):
                        gate.tools.check_cancel()
                        bridge=gate.native_bridge
                        if self.path=='/native/session':bridge.register_session(data);self.answer(200,{'registered':True})
                        elif self.path=='/native/configuration':gate.store.event(task['id'],attempt['id'],'native_configuration_receipt',data);self.answer(200,{'saved':True})
                        elif self.path=='/native/scene':
                            from .scene_runtime import receipt
                            receipt(gate.store,task,attempt,'FAILED' if data.get('missing') else 'NATIVE_OBSERVED',data)
                            self.answer(200,{'saved':True})
                        elif self.path=='/native/tool':self.answer(200,bridge.before_tool(data))
                        elif self.path=='/native/interaction':bridge.http_interaction(data,lambda value:self.answer(200,value))
                        elif self.path=='/native/cancelled':
                            if not bridge.official or task['spec']['provider']!='deepseek':
                                raise ValueError('Native interaction cancellation requires the bound official DeepSeek attempt')
                            bridge.cancelled(data);self.answer(200,{'saved':True})
                        elif self.path=='/native/applied':bridge.applied(data);self.answer(200,{'saved':True})
                        elif self.path=='/native/controls':
                            controls=getattr(gate.store,'native_controls',None)
                            if getattr(gate,'control_operation',None):
                                op=gate.control_operation;gate.control_operation=None;operations=[op]
                            else:operations=controls.claim(gate.attempt,plugin=True) if controls else []
                            self.answer(200,{'operations':operations})
                        elif self.path=='/native/control-result':
                            controls=getattr(gate.store,'native_controls',None)
                            if not controls:raise ValueError('原生控制服务未载入。')
                            with gate.store.read() as db:op=controls.s.collaboration.get(db,'native_control',data.get('id'))
                            if op['attempt_id']!=gate.attempt['id'] or op['owner']!=gate.attempt['owner']:raise ValueError('原生操作归属不匹配。')
                            controls.finish(data['id'],data.get('result'),data.get('error'));self.answer(200,{'saved':True})
                        else:self.answer(404,{'error':'Unsupported native control endpoint'})
                    elif self.path in {'/v1/chat/completions','/chat/completions'}:gate.generate(self,data)
                    else:self.answer(404,{'error':'Unsupported task endpoint'})
                except (ValueError,KeyError,TypeError) as exc:
                    gate.refusals.append(str(exc)[:300])
                    gate.store.event(task['id'],attempt['id'],'gateway_refused',{'reason':str(exc)[:300]})
                    try:
                        if not getattr(self,'response_started',False):self.answer(400,{'error':str(exc)[:300]})
                        else:self.close_connection=True
                    except OSError:pass
                except (OSError,ConnectionError):pass
        self.server=FetchSafeHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)

    def __enter__(self):self.thread.start();return self
    def __exit__(self,*args):
        self.stop.set()
        for response in list(self.responses):
            try:
                sock=getattr(getattr(getattr(response,'fp',None),'raw',None),'_sock',None)
                if sock:sock.shutdown(2)
                response.close()
            except OSError:pass
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def generate(self,handler,body):
        with self.lock:
            self.tools.check_cancel()
            if set(body)-FIELDS:raise ValueError('Unreviewed model request fields')
            if body.get('model')!=self.task['spec']['model']:raise ValueError('Model differs from approved task')
            from .attachments import media_summary
            media=media_summary(self.task,body,self.attempt)
            native=self.task['spec'].get('execution') in {'qwen_native','deepseek_native'}
            for tool in body.get('tools',[]):
                function=tool.get('function',{});name=function.get('name','')
                if not native and name!='todo_write' and name.split('__')[-1] not in {d['name'] for d in DEFINITIONS}:
                    self.store.event(self.task['id'],self.attempt['id'],'unapproved_sdk_schema',
                                     {'tool_name':str(name)[:80],'body_sha256':digest(body),'no_forward':True})
                    raise ValueError('Unapproved model tool schema')
                if name!='todo_write' and 'parameters' not in function:
                    function['parameters']={'type':'object','properties':{},'additionalProperties':False}
            if native:
                from .sdk_native import QWEN_DENIED
                names=[t.get('function',{}).get('name','') for t in body.get('tools',[])]
                if any(n.startswith('mcp__') or n in QWEN_DENIED or n in {'web','web_search','web_fetch','subagent','workflow'} for n in names):raise ValueError('当前原生任务未授权该网络、外部工具或委派接收方。')
                self.store.event(self.task['id'],self.attempt['id'],'native_sdk_tool_schemas',{'names':names,'source':'official_sdk_request','workdesk_mcp_replacement':False})
            if self.secret and self.secret.reveal_for_provider() in canonical(body):raise ValueError('Provider credential found in outgoing material; refused before send')
            # This is the explicitly selected execution mode, separate from any
            # context/output policy. Native compression remains enabled.
            mode=self.task['spec']['effort'];provider=self.task['spec']['provider']
            if provider=='deepseek':
                body.pop('enable_thinking',None);body['thinking']={'type':'disabled' if mode=='off' else 'enabled'}
                if mode=='off':body.pop('reasoning_effort',None)
                elif mode in {'low','high','max'}:body['reasoning_effort']=mode
                else:raise ValueError('Unsupported approved DeepSeek thinking mode')
            else:
                if mode not in {'off','thinking'}:raise ValueError('Unsupported approved Qwen thinking mode')
                body.pop('thinking',None);body.pop('reasoning_effort',None);body['enable_thinking']=mode!='off'
            selection=self.task['spec'].get('model_selection')
            if selection and selection['entry'].get('endpoint')!=ENDPOINTS[provider]:raise ValueError('Catalog endpoint differs from approved official recipient')
            reserve=request_reserve(provider,body,selection)
            identity=self.store.reserve_request(self.attempt['id'],body,reserve,self.limits)
            if media:self.store.event(self.task['id'],self.attempt['id'],'attachment_wire_observation',{'request_id':identity,'image_parts':media,'comprehension':'NOT_ATTESTED','no_extra_recipient':True})
            from .attachments import native_document_bindings
            _,documents=native_document_bindings(self.task,body,self.attempt)
            if documents:self.store.event(self.task['id'],self.attempt['id'],'attachment_native_document_wire_observation',{'request_id':identity,'documents':documents,'comprehension':'NOT_ATTESTED'})
            self.store.event(self.task['id'],self.attempt['id'],'model_wire_configuration_intent',
                             {'request_id':identity,'requested_model':body['model'],'selected_effort':mode,
                              'wire_mode':{k:body[k] for k in ('enable_thinking','thinking','reasoning_effort') if k in body},
                              'endpoint':ENDPOINTS[provider],'catalog_revision':selection.get('catalog_revision') if selection else 'LEGACY_TASK',
                              'body_sha256':digest(body),'remote_mode_attestation':'UNKNOWN'})
            if self.fake:
                return self.fake(handler,body,identity,self)
            if self.secret is None:raise ValueError('Missing provider credential')
            raw=canonical(body).encode()
            request=Request(ENDPOINTS[self.task['spec']['provider']],data=raw,
                            headers={'Content-Type':'application/json','Authorization':'Bearer '+self.secret.reveal_for_provider()})
            try:
                remaining=self.attempt['deadline']-time.time()-5
                if remaining<=0:raise ValueError('Task deadline before send')
                response=urlopen(request,timeout=min(25,remaining));self.responses.append(response)
                mime=response.headers.get('Content-Type','application/json')
                handler.send_response(200);handler.send_header('Content-Type',mime);handler.end_headers()
                data=bytearray();finished=False;usage=None;client_open=True;termination=[];content=[];content_size=0;served_models=set();reasoning_observed=False
                if body.get('stream'):
                    while True:
                        line=response.readline()
                        if not line:break
                        if line.startswith(b'data:'):
                            text=line[5:].strip()
                            if text==b'[DONE]':finished=True
                            else:
                                try:
                                    item=json.loads(text)
                                    if isinstance(item.get('model'),str):served_models.add(item['model'][:200])
                                    if item.get('usage'):usage=item['usage']
                                    for choice in item.get('choices',[]):
                                        if choice.get('finish_reason') is not None:
                                            finished=True;termination.append(choice['finish_reason'])
                                        piece=(choice.get('delta') or {}).get('content')
                                        if (choice.get('delta') or {}).get('reasoning_content'):reasoning_observed=True
                                        if isinstance(piece,str) and content_size+len(piece)<=32768:
                                            content.append(piece);content_size+=len(piece)
                                except ValueError:pass
                        if client_open:
                            try:handler.wfile.write(line);handler.wfile.flush()
                            except OSError:client_open=False
                else:
                    data.extend(response.read())
                    item=json.loads(data);finished=bool(item.get('choices'));usage=item.get('usage')
                    if isinstance(item.get('model'),str):served_models.add(item['model'][:200])
                    reasoning_observed=any((c.get('message') or {}).get('reasoning_content') for c in item.get('choices',[]))
                    try:handler.wfile.write(data)
                    except OSError:pass
                if finished:self.store.request_result(identity,'COMPLETE',usage)
                self.store.event(self.task['id'],self.attempt['id'],'model_request_receipt',
                                 {'id':identity,'state':'COMPLETE' if finished else 'UNKNOWN','usage':usage,
                                  'finish_reasons':termination,'stream_content_observed':''.join(content),'content_capture_limit':32768})
                self.observe_served(identity,body['model'],served_models,reasoning_observed,selection)
                response.close();self.responses.remove(response)
            except HTTPError as exc:
                self.store.request_result(identity,'HTTP_ERROR',{'status':exc.code})
                handler.send_response(exc.code);handler.send_header('Content-Type','application/json');handler.end_headers()
                handler.wfile.write(canonical({'error':{'message':'Official provider HTTP '+str(exc.code),'type':'provider_error'}}).encode())
                self.store.event(self.task['id'],self.attempt['id'],'model_request_http_error',{'id':identity,'status':exc.code})
            except Exception as exc:
                self.store.event(self.task['id'],self.attempt['id'],'model_request_unknown',{'id':identity,'class':type(exc).__name__})
                # UNKNOWN reservation remains, so SDK retries are refused locally.
                raise

    def observe_served(self,identity,requested,labels,reasoning,selection):
        allowed=(selection or {}).get('entry',{}).get('served_model_labels',[requested])
        mismatch=bool(labels) and any(label.casefold() not in {v.casefold() for v in allowed} for label in labels)
        value={'request_id':identity,'requested_model':requested,'served_model_labels':sorted(labels),
               'label_match':'MISMATCH' if mismatch else 'MATCH' if labels else 'UNKNOWN',
               'reasoning_content_observed':bool(reasoning),'actual_effort_and_weights_attestation':'UNKNOWN',
               'catalog_revision':selection.get('catalog_revision') if selection else 'LEGACY_TASK'}
        self.store.event(self.task['id'],self.attempt['id'],'model_served_configuration_observation',value)
        if mismatch:
            self.configuration_mismatches.append(value);self.stop.set()
            self.store.event(self.task['id'],self.attempt['id'],'model_configuration_mismatch',
                             {**value,'no_more_requests':True,'no_automatic_model_fallback':True})
