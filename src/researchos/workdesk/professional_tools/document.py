"""Local document/1 worker. Reads only selected versions and emits verified files."""
from hashlib import sha256
import html
import json
import mimetypes
from pathlib import Path, PureWindowsPath
import re
import sys
from zipfile import ZipFile

from .document_pages import office, pdf_file
from .document_sheet import csv_file, xlsx_file

FORMATS={'xlsx','csv','docx','pptx','pdf','md','tex','html'}
MIME={'xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      'md':'text/markdown','tex':'application/x-tex','csv':'text/csv','html':'text/html',
      'pdf':'application/pdf','png':'image/png'}


def inspect_inputs(root,names,fmt):
    files=[]
    for name in names:
        source=(root/name).resolve(strict=True)
        if not source.is_relative_to(root) or not source.is_file() or source.suffix.lower()!='.'+fmt:
            raise ValueError('输入应为已授权的同格式文件。')
        if source.stat().st_size>64*1024*1024:
            raise ValueError('单个模板超过 64 MiB。')
        if fmt in {'xlsx','docx','pptx'}:
            with ZipFile(source) as archive:
                if len(archive.infolist())>16000 or sum(i.file_size for i in archive.infolist())>160*1024*1024:
                    raise ValueError('模板解压后的内容超过处理限额。')
                if len(set(archive.namelist()))!=len(archive.infolist()):
                    raise ValueError('模板有重复部件，无法核实版本。')
        files.append(source)
    return files


def text_files(path,template,p):
    if template:
        old=template.read_text(encoding='utf-8-sig')
        rep=p.get('replace') or {}
        if not rep.get('from') or rep['from'] not in old or not isinstance(rep.get('to'),str):
            raise ValueError('指定的替换文字无效或未找到。')
        source=old.replace(rep['from'],rep['to'])
    else:
        source=p['content']
    path.write_text(source,encoding='utf-8',newline='')
    if path.read_bytes()!=source.encode('utf-8'):
        raise ValueError('源码回读不一致。')
    checks=[('源码回读','PASSED','已保存 UTF-8 源文件。')]
    fmt=p['format']
    if fmt=='md':
        from markdown_it import MarkdownIt
        body=MarkdownIt('commonmark',{'html':False}).render(source)
    elif fmt=='tex':
        from pylatexenc.latex2text import LatexNodes2Text
        body='<pre>'+html.escape(LatexNodes2Text().latex_to_text(source))+'</pre>'

    else:
        body=source
    import nh3
    def local_attribute(tag, attr, val):
        if tag=='input' and attr=='type':
            return val if val in {'checkbox','radio','text','number','range'} else 'text'
        return val
    body=nh3.clean(body,tags={'p','br','div','span','b','i','u','code','pre','em','strong','ol','ul','li',
                                'details','summary','input','label','textarea','select','option','table','thead','tbody','tr','th','td','blockquote','hr',*('h'+str(i) for i in range(1,7))},
                   attributes={'input':{'type','checked','value','placeholder','min','max','step'},
                               'details':{'open'},'option':{'selected'},'textarea':{'placeholder'}},
                   attribute_filter=local_attribute)
    preview=path.parent/'preview'/'index.html'
    preview.parent.mkdir()
    preview.write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" '
                       'content="default-src \'none\'"><main>'+body+'</main>',encoding='utf-8')
    checks.append(('静态预览','PASSED','HTML 预览已导出。'))
    if fmt=='tex':
        from .tex_render import render_tex
        steps, extras = render_tex(path,path.parent/'typeset',p)
        return checks+steps,[preview,*extras]
    return checks,[preview]


def execute(request):
    p=request.get('parameters') or {}
    fmt=p.get('format')
    if fmt not in FORMATS or p.get('action') not in ('create','edit'):
        raise ValueError('文档格式或动作不支持。')
    root=Path(request['project_root']).resolve(strict=True)
    output=Path(request['output_dir']).resolve()
    name=p.get('name') or ('成果.'+fmt)
    if (not isinstance(name,str) or len(name)>140 or re.search(r'[<>:"/\\|?*\x00-\x1f]',name)
        or Path(name).suffix.lower()!='.'+fmt or name.startswith('.') or PureWindowsPath(name).is_reserved()):
        raise ValueError('请指定与文档格式一致的普通文件名。')
    inputs=inspect_inputs(root,request.get('input_files') or [],fmt)
    if p['action']=='create' and inputs or p['action']=='edit' and not inputs:
        raise ValueError('创建使用空模板，修改须指定输入版本。')
    if len(inputs)>1 and fmt!='pdf':
        raise ValueError('此动作只支持一个模板。')
    if p.get('content') is not None and not isinstance(p['content'],str):
        raise ValueError('正文应为文本。')
    if p['action']=='create' and fmt not in ('xlsx','csv') and not p.get('content'):
        raise ValueError('请填写待写入的正文。')
    template=inputs[0] if inputs else None
    output.mkdir(parents=True,exist_ok=False)
    path=output/name
    if fmt=='xlsx':
        checks,extra=xlsx_file(path,template,p),[]
    elif fmt=='csv':
        checks,extra=csv_file(path,template,p),[]
    elif fmt in ('pptx','docx'):
        checks,extra=office(path,template,p)
    elif fmt=='pdf':
        checks,extra=pdf_file(path,inputs,p)
    else:
        checks,extra=text_files(path,template,p)
    artifacts=[]
    for item in [path,*extra]:
        raw=item.read_bytes()
        artifacts.append({'path':str(item),'bytes':len(raw),'sha256':sha256(raw).hexdigest(),
                          'mime':MIME.get(item.suffix.lower()[1:],mimetypes.guess_type(item.name)[0] or 'application/octet-stream')})
    details=[{'kind':k,'state':s,'detail':v,'sha256':artifacts[0]['sha256']} for k,s,v in checks]
    return {'state':'COMPLETED','tool_version':'document/1','real_model_requests':0,
            'summary':f'已保存 {name}，附 {len(extra)} 份预览或导出材料。','checks':details,'artifacts':artifacts}


def main():
    try:
        raw=sys.stdin.buffer.read(4*1024*1024+1)
        if len(raw)>4*1024*1024:
            raise ValueError('文档操作参数超过 4 MiB。')
        result=execute(json.loads(raw))
    except Exception as exc:
        print(json.dumps({'state':'FAILED','error':str(exc),'artifacts':[]},ensure_ascii=False))
        return 1
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0


if __name__=='__main__':
    sys.exit(main())
