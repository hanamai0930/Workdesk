"""Official DeepSeek Harness native loop, with task-scoped presentation plugin."""
from dataclasses import asdict
import json
import sys
import threading
from deepseek_harness import DeepSeekHarness
from pydantic import BaseModel


class Receipt(BaseModel):pass
class NativeResult(BaseModel):
    model_config={'extra':'allow'}
def emit(kind,data):print(json.dumps({'type':kind,'data':data},ensure_ascii=False),flush=True)
config=json.loads(sys.stdin.readline());prompt_intent=False;phase='initialize'
harness=DeepSeekHarness(cwd=config['workspace'],runtime_cwd=config['workspace'],dsh_home=config['home'],
    **({'dsh_bin':config['dsh_bin']} if config.get('official') and config.get('dsh_bin') else {}),
    profile='sdk',patches=(config['patch'],),model=config['model'],reasoning_effort=config.get('effort','off'),
    base_url='https://api.deepseek.com/anthropic' if config.get('official') else config['url'],api_key=config['token'],request_timeout_seconds=None if config.get('official') else max(1,config['deadline']-__import__('time').time()),shutdown_timeout_seconds=3,
    env={'WORKDESK_GATE_URL':config.get('control_url',config['url']),'WORKDESK_GATE_TOKEN':config.get('control_token',config['token']),'WORKDESK_SESSION_ID':config['session_id'],
         **({'WORKDESK_OFFICIAL':'1','WORKDESK_PERMISSION_PRESET':config.get('permission_preset') or '',
             'WORKDESK_NATIVE_COMMAND_CATALOG':'1' if config.get('command_catalog') else '0'}
            if config.get('official') else {'DSH_TELEMETRY_MODE':'OFF','DSH_PRIMARY_RUNTIME':''})})


def lifecycle(phase, **details):
    if config.get('official') and not config.get('operation'):
        emit('native_sdk_lifecycle', {'phase': phase, 'session_id': config['session_id'], **details})


def controls():
    for line in sys.stdin:
        try:
            command=json.loads(line)
            if command.get('action')=='interrupt':
                result=harness.client.request('shutdown',None,response_model=Receipt,timeout_seconds=5)
                emit('shutdown_receipt',result.model_dump())
            elif command.get('action')=='native.control':
                op=command['operation']
                try:
                    result=harness.client.request(op['method'],op['params'],response_model=NativeResult)
                    emit('native_control_result',{'id':op['id'],'result':result.model_dump()})
                except Exception as exc:emit('native_control_result',{'id':op['id'],'error':str(exc)})
        except Exception as exc:emit('control_error',{'class':type(exc).__name__,'message':str(exc)[:500]})


try:
    with harness:
        lifecycle('INIT_ACKNOWLEDGED')
        resumed = None
        if config.get('resume') and config.get('official'):
            phase='resume'
            resumed = harness.resume_session(config['session_id'])
            if resumed.id != config['session_id']:
                raise ValueError('Native DeepSeek resume returned a different session')
            emit('native_resume_receipt',{'session_id':config['session_id'],'method':'session/resume','restored':True})
        emit('initialized',{'session_id':config['session_id'],'native':True})
        if config.get('operation'):
            op=config['operation']
            if not op['method'].startswith('session/'):
                # The presentation plugin calls the official core services;
                # the controller closes this owned runtime after its receipt.
                threading.Event().wait()
            else:
              try:
                result=harness.client.request(op['method'],op['params'],response_model=NativeResult)
                emit('native_control_result',{'id':op['id'],'result':result.model_dump()})
              except Exception as exc:emit('native_control_result',{'id':op['id'],'error':str(exc)})
        else:
            threading.Thread(target=controls,daemon=True).start()
            phase='prompt';prompt_intent=True
            if config.get('official'):emit('native_prompt_send_intent',{'session_id':config['session_id'],'method':'session/prompt'})
            material = config.get('input', config['prompt'])
            def notification(item):
                emit('notification', asdict(item))
            if resumed:
                result = resumed.run(material, on_notification=notification)
            else:
                result = harness.run(material, session_id=config['session_id'], on_notification=notification)
            if config.get('official'):
                if result.session_id != config['session_id']:
                    raise ValueError('Native DeepSeek main result session mismatch')
                lifecycle('MAIN_RESULT', outcome=result.finish_reason)
                lifecycle('WAIT_REQUESTED')
                harness.client.request('session/wait', {'sessionId': config['session_id']},
                                       response_model=Receipt, on_notification=notification)
                lifecycle('WAIT_ACKNOWLEDGED')
            emit('result',{'session_id':result.session_id,'final_response':result.final_response,'finish_reason':result.finish_reason})
except Exception as exc:
    if config.get('official') and not prompt_intent:emit('native_pre_send_failure',{'session_id':config['session_id'],'phase':phase,'prompt_sent':False,'class':type(exc).__name__,'message':str(exc)[:1500]})
    emit('error',{'class':type(exc).__name__,'message':str(exc)[:1500]})
    raise SystemExit(1)
