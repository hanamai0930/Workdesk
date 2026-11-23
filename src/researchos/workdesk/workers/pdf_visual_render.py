"""Owned, bounded local Poppler renderer. No OCR, JavaScript or network calls."""
import base64
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def main():
    c=json.loads(sys.stdin.readline());raw=base64.b64decode(c['data'],validate=True)
    if len(raw)>8*1024*1024:raise ValueError('PDF超过8MiB。')
    renderer=Path(c['renderer'])
    if sha256(renderer.read_bytes()).hexdigest()!=c['renderer_sha256']:raise ValueError('渲染器指纹改变。')
    from pypdf import PdfReader
    reader=PdfReader(BytesIO(raw))
    if reader.is_encrypted:raise ValueError('加密PDF不收集密码、不自动解密。')
    count=len(reader.pages);pages=c.get('pages')
    nums=[int(i) for i in pages.split('-')] if pages else [1,count];start,end=nums[0],nums[-1]
    if not 1<=start<=end<=count or end-start>=10:raise ValueError('PDF视觉页码越界或超过10页；请明确选择范围。')
    total_pixels=0
    for n in range(start-1,end):
        page=reader.pages[n];box=page.cropbox
        width=float(box.width)*2;height=float(box.height)*2
        if page.get('/UserUnit',1)!=1:raise ValueError('此PDF使用未核对的页面单位，不自动缩放。')
        if not all(math.isfinite(v) and 0<v<=16000 for v in (width,height)) or math.ceil(width)*math.ceil(height)>16_000_000:
            raise ValueError('PDF单页超过1600万像素，不静默缩放。')
        total_pixels+=math.ceil(width)*math.ceil(height)
    if total_pixels>32_000_000:raise ValueError('PDF选页超过3200万像素。')
    with tempfile.TemporaryDirectory(prefix='owned-pdf-',dir=c['scratch']) as temp:
        root=Path(temp);source=root/'frozen.pdf';source.write_bytes(raw)
        proc=subprocess.Popen([str(renderer),'-q','-r','144','-cropbox','-png','-f',str(start),'-l',str(end),str(source),str(root/'page')],
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+14
            while True:
                outputs=list(root.glob('page-*.png'))
                if len(outputs)>10 or sum(p.stat().st_size for p in outputs)>32*1024*1024:raise ValueError('派生PNG超过32MiB。')
                if time.monotonic()>=deadline:raise ValueError('本机渲染超时。')
                try:out,err=proc.communicate(timeout=.05);break
                except subprocess.TimeoutExpired:pass
            if proc.returncode:raise ValueError('Poppler拒绝PDF：'+err.decode('utf-8','replace')[-500:])
        finally:
            if proc.poll() is None:proc.kill()
            proc.wait(timeout=3)
            for stream in (proc.stdout,proc.stderr):stream.close()
        outputs={int(p.stem.split('-')[-1]):p for p in root.glob('page-*.png')}
        if sorted(outputs)!=list(range(start,end+1)):raise ValueError('Poppler未返回全部指定页面。')
        images=[]
        for page in range(start,end+1):
            path=outputs[page]
            if path.stat().st_size>8*1024*1024:raise ValueError('单页PNG超过8MiB。')
            images.append({'page':page,'data':base64.b64encode(path.read_bytes()).decode('ascii')})
        print(json.dumps({'images':images,'provenance':{'renderer':'Poppler pdftoppm','renderer_sha256':c['renderer_sha256'],
            'route':'EXPLICIT_LOCAL_PDF_PAGE_RENDERING','dpi':144,'pages':[start,end],'total_pages':count,
            'remaining_pages_not_read':count-(end-start+1),'ocr':False,'text_extracted':False,'annotations_rendered':True,
            'comprehension':'NOT_ATTESTED','extra_model_recipients':False}}))

if __name__=='__main__':
    try:main()
    except Exception as exc:print(type(exc).__name__+': '+str(exc),file=sys.stderr);raise SystemExit(1)
