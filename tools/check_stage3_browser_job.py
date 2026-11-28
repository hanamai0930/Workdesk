"""Real Go authorization and browser job on a single disposable local origin."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlencode

from check_core_chain import request, require


def check_browser_job(chain, submit, await_job):
    visits = []
    class Sample(BaseHTTPRequestHandler):
        def do_GET(self):
            visits.append(self.path)
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write('<title>资料核对</title><body>授权范围内的页面<input id="note"><img src="/unselected.png"></body>'.encode('utf8'))

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(('127.0.0.1',0),Sample) as server:
        thread = Thread(target=server.serve_forever,daemon=True)
        thread.start()
        origin = 'http://127.0.0.1:'+str(server.server_port)
        url = origin+'/资料'
        try:
            job_id = submit(chain, 'computer/1', {'action':'browser.fill','origin':origin,'url':url,
                                                'element_id':'note','text':'受限样本'})
            job = await_job(chain, job_id)
            require(job['state']=='COMPLETED', job.get('error') or job['state'])
            grant_id = job['authorization_id']
            grant = chain.api('/api/v1/receipt?id='+grant_id)['receipt']['grant']
            require(grant['parameters']['url']==url and grant['parameters']['action']=='browser.fill','作业关联的授权范围错误')
            artifact = next(a for a in job['artifacts'] if a['path'].endswith('surface-receipt.json'))
            data = request(chain.origin,'/api/v2/jobs/'+job_id+'/artifact?'+urlencode({'path':artifact['path']}),
                           token=chain.backend['viewer'],raw=True)
            result = json.loads(data)
            require(result['origin']==origin and result['element_id']=='note' and '授权范围内' in result['text'],
                    '实际浏览器的操作回执不符')
            require(visits==['/%E8%B5%84%E6%96%99'], '页面请求不在指定范围')
            return {'job_id':job_id,'authorization_id':grant_id,'result_sha256':artifact['sha256'],
                    'title':result['title'],'action':result['action'],'http_requests':len(visits)}
        finally:
            server.shutdown()
            thread.join()
