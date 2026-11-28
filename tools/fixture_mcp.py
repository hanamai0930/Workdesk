"""Disposable two-page MCP fixture; never calls a model or touches user files."""
import json
import sys
for line in sys.stdin:
    msg=json.loads(line);method=msg.get('method')
    if 'id' not in msg:continue
    if method=='initialize':result={'protocolVersion':msg['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'workdesk-fixture','version':'1'}}
    elif method=='tools/list':
        result={'tools':[{'name':'echo' if msg.get('params',{}).get('cursor') else 'ping','description':'Local fixture only','inputSchema':{'type':'object','properties':{'text':{'type':'string'}}}}]}
        if not msg.get('params',{}).get('cursor'):result['nextCursor']='second'
    elif method=='tools/call':result={'content':[{'type':'text','text':msg['params'].get('arguments',{}).get('text','pong')}]}
    else:result={}
    print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result}),flush=True)
