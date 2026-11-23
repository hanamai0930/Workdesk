"""Official installed SDK helper. Receives only ephemeral local tool/API credentials."""
from dataclasses import asdict
import json
import sys
import threading
from deepseek_harness import DeepSeekHarness
from pydantic import BaseModel


class Receipt(BaseModel): pass
def emit(kind,data):print(json.dumps({'type':kind,'data':data},ensure_ascii=False),flush=True)
config=json.loads(sys.stdin.readline())
harness=DeepSeekHarness(cwd=config['workspace'],runtime_cwd=config['workspace'],dsh_home=config['home'],
                       profile='sdk',patches=(config['patch'],),model=config['model'],reasoning_effort=config.get('effort','off'),
                       base_url=config['url'],api_key=config['token'],
                       request_timeout_seconds=160,shutdown_timeout_seconds=3,
                       env={'WORKDESK_GATE_URL':config['url'],'WORKDESK_GATE_TOKEN':config['token'],
                            'WORKDESK_TOOL_DEFINITIONS':json.dumps(config['tools']),
                            'DSH_TELEMETRY_MODE':'OFF','DSH_PRIMARY_RUNTIME':''})


def controls():
    for line in sys.stdin:
        try:
            if json.loads(line).get('action')=='interrupt':
                result=harness.client.request('shutdown',None,response_model=Receipt,timeout_seconds=5)
                emit('shutdown_receipt',result.model_dump())
        except Exception as exc:emit('control_error',{'class':type(exc).__name__,'message':str(exc)[:500]})


try:
    with harness:
        emit('initialized',{'session_id':config['session_id']})
        threading.Thread(target=controls,daemon=True).start()
        result=harness.run(config['prompt'],session_id=config['session_id'],
                           on_notification=lambda item:emit('notification',asdict(item)))
        emit('result',{'session_id':result.session_id,'final_response':result.final_response,'finish_reason':result.finish_reason})
except Exception as exc:
    emit('error',{'class':type(exc).__name__,'message':str(exc)[:1500]})
    raise SystemExit(1)
