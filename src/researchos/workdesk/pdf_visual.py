"""Explicit local PDF page rendering, followed by the selected native image route."""
import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from . import workspace
from .paths import clean_env,legacy

MAX_PAGES=10
MAX_TASK_PAGES=16
MAX_PAGE_PIXELS=16_000_000
MAX_TASK_PIXELS=32_000_000
DPI=144


def runtime(locations=None):
    from .attachments import pdf_python,native_pdf_runtime
    python=pdf_python(locations)
    renderer=native_pdf_runtime(locations)['pdftoppm']
    if not python or not renderer:return None
    return {'python':python,'renderer':renderer,'renderer_sha256':sha256(Path(renderer).read_bytes()).hexdigest(),
            'dpi':DPI,'max_pages':MAX_PAGES,'max_page_pixels':MAX_PAGE_PIXELS,
            'route':'EXPLICIT_LOCAL_PDF_PAGES_TO_NATIVE_IMAGES'}


def render(locations,attempt,item,stop,execution,raw):
    selected=item['visual_runtime'];actual=runtime(locations)
    if actual!=selected:raise ValueError('PDF视觉运行时已缺失或改变；先修订并重新确认，不自动换渲染器。')
    revision=execution.get('implementation_revision')
    worker=(locations.data/'implementations'/revision/'src/researchos/workdesk/workers/pdf_visual_render.py') if revision else Path(__file__).with_name('workers')/'pdf_visual_render.py'
    scratch=locations.data/'tasks'/attempt['task_id']/'attempts'/attempt['id']/'pdf-render'
    workspace.io_path(scratch).mkdir(parents=True,exist_ok=True)
    request={'data':base64.b64encode(raw).decode('ascii'),'pages':item.get('pages'),**selected,'scratch':str(workspace.io_path(scratch))}
    proc=subprocess.Popen([selected['python'],'-B','-X','utf8',str(worker)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          env=clean_env(),cwd=attempt['workspace'],creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    job=None
    try:
        if os.name=='nt':job=legacy(locations,'provider-harness','owned_job').OwnedJob(proc)
        payload=json.dumps(request).encode();deadline=min(attempt['deadline']-3,time.time()+20);first=True
        while True:
            if stop.is_set() or time.time()>=deadline:raise ValueError('PDF页面渲染已停止或超时；没有发送模型。')
            try:out,err=proc.communicate(payload if first else None,timeout=.1);break
            except subprocess.TimeoutExpired:first=False
        if proc.returncode:raise ValueError('PDF页面渲染失败：'+err.decode('utf-8','replace')[-900:])
        if len(out)>45*1024*1024:raise ValueError('派生页面超出本机传输范围，不静默省略。')
        value=json.loads(out);images=value.get('images')
        if not isinstance(images,list) or not 1<=len(images)<=MAX_PAGES:raise ValueError('PDF页面输出数量无效。')
        pages=value['provenance']['pages']
        if [i.get('page') for i in images]!=list(range(pages[0],pages[1]+1)):raise ValueError('PDF页面输出有跳页或重复。')
        for i in images:
            data=base64.b64decode(i.pop('data'),validate=True)
            if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data)>workspace.MAX_BINARY:raise ValueError('PDF页面图像无效或超限。')
            width=int.from_bytes(data[16:20],'big');height=int.from_bytes(data[20:24],'big')
            if not width or not height or width*height>MAX_PAGE_PIXELS:raise ValueError('PDF页面像素超限，不静默缩放。')
            i.update(raw=data,sha256=sha256(data).hexdigest(),bytes=len(data),width=width,height=height,mime='image/png')
        return value
    finally:
        if job:job.close()
        if proc.poll() is None:proc.kill()
        proc.wait(timeout=5)
        for stream in (proc.stdin,proc.stdout,proc.stderr):
            if stream:stream.close()


def manifest(attempt):
    return {i['file']:{k:i[k] for k in ('sha256','bytes')} for i in attempt.get('_attachment_images',[])}


def image_metadata(attempt):
    return [{k:v for k,v in i.items() if k not in {'data','path','raw'}} for i in attempt.get('_attachment_images',[])]


def prepare(locations,store,task,attempt,item,raw,stop,execution):
    value=render(locations,attempt,item,stop,execution,raw)
    existing=attempt.setdefault('_attachment_images',[])
    count=len(existing)+len(value['images'])
    size=sum(i['bytes'] for i in existing)+sum(i['bytes'] for i in value['images'])
    pixels=sum(i['width']*i['height'] for i in existing+value['images'])
    if count>MAX_TASK_PAGES or size>workspace.MAX_ATTACHMENT_TOTAL or pixels>MAX_TASK_PIXELS:
        raise ValueError('本任务PDF派生页面超过16张、32MiB或3200万像素；请选择更小页码，不静默省略。')
    if task['spec']['provider']=='qwen':
        remaining=task['spec']['budget']['tools']-store.usage(task=task['id'])['tool_calls']
        other_images=sum(a['kind']=='image' and a['route']=='native' for a in task['spec'].get('attachments',[]))
        if remaining<count+other_images:raise ValueError('Qwen逐页原生读取工具余量不足；未发送模型，请缩小页码或明确修订工具额度。')
    item['rendering']=value['provenance'];item['rendering']['source_sha256']=item['sha256']
    # Names are deterministic across native resume. Never overwrite a changed
    # prior page or a user file, and never let the model write generated pages.
    folder='pdf-pages/'+item['sha256'][:16]
    for image in value['images']:
        name=folder+'/page-'+str(image['page'])+'.png'
        path=workspace.plain(Path(attempt['workspace'])/name,Path(attempt['workspace']),missing=True)
        raw_image=image.pop('raw');physical=workspace.io_path(path);physical.parent.mkdir(parents=True,exist_ok=True)
        if physical.exists():
            current=workspace.read_blob(attempt['workspace'],name)
            if current['sha256']!=image['sha256']:raise ValueError('既有派生页发生变化，未覆写或发送。')
        else:
            with physical.open('xb') as stream:stream.write(raw_image)
        record={**image,'file':name,'source_file':item['file'],'source_sha256':item['sha256'],'path':str(path),'delivery':item['delivery']}
        if task['spec']['provider']=='deepseek':record['data']=base64.b64encode(raw_image).decode('ascii')
        existing.append(record)
        # Preserve exact derived bytes before any model/tool can alter the copy.
        evidence=workspace.io_path(locations.data/'tasks'/task['id']/'attempts'/attempt['id']/'prepared-files'/name)
        evidence.parent.mkdir(parents=True,exist_ok=True)
        if evidence.exists() and evidence.read_bytes()!=raw_image:raise ValueError('派生页证据冲突。')
        if not evidence.exists():evidence.write_bytes(raw_image)
    item['page_images']=[i for i in image_metadata(attempt) if i['source_file']==item['file']]
