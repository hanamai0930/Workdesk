"""Editable page documents and bounded page previews."""
from io import BytesIO
from pathlib import Path
import textwrap
from zipfile import ZipFile

from .document_sheet import write_zip, xml

def replace_group(nodes, old, new):
    # Update from right to left so offsets from the original runs stay valid.
    from bisect import bisect_right
    spans=[]
    starts=[]
    text=''
    for node in nodes:
        if node.text:
            starts.append(len(text))
            spans.append(node)
            text+=node.text
    hits=[]
    pos=0
    while (pos:=text.find(old,pos))>=0:
        hits.append(pos)
        pos+=len(old)
    for pos in reversed(hits):
        last=pos+len(old)-1
        a,b=bisect_right(starts,pos)-1,bisect_right(starts,last)-1
        left=spans[a].text[:pos-starts[a]]
        right=spans[b].text[last-starts[b]+1:]
        if a==b:
            spans[a].text=left+new+right
        else:
            spans[a].text=left+new
            for node in spans[a+1:b]:
                node.text=''
            spans[b].text=right
        for node in (spans[a],spans[b]):
            node.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
    return len(hits)


def replace_paragraphs(tree, ns, old, new):
    changes=0
    field_depth=0
    # A paragraph or nontext item (line break, hyperlink, field, drawing)
    # separates the matchable runs; retain every run's original formatting.
    for paragraph in tree.iter(ns+'p'):
        group=[]
        for child in paragraph:
            if child.tag!=ns+'r':
                changes+=replace_group(group,old,new)
                group=[]
                continue
            for item in child:
                if item.tag==ns+'rPr':
                    continue
                if item.tag==ns+'t' and field_depth==0:
                    group.append(item)
                    continue
                changes+=replace_group(group,old,new)
                group=[]
                if item.tag==ns+'fldChar':
                    kind=item.get(ns+'fldCharType')
                    if kind=='begin':
                        field_depth+=1
                    elif kind=='end':
                        field_depth=max(0,field_depth-1)
        changes+=replace_group(group,old,new)
    return changes


def rewrite_office(template,path,params):
    ppt=path.suffix.lower()=='.pptx'
    word='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    drawing='{http://schemas.openxmlformats.org/drawingml/2006/main}'
    rep=params.get('replace') or {}
    old,new=rep.get('from'),rep.get('to')
    if not old or not isinstance(old,str) or not isinstance(new,str):
        raise ValueError('请指定需要替换的文字和新文字。')
    from lxml import etree
    changes={}
    matches=0
    with ZipFile(template) as archive:
        for name in archive.namelist():
            in_scope=name.startswith(('ppt/slides/slide','ppt/notesSlides/notesSlide')) if ppt else name.startswith(('word/document.xml','word/header','word/footer'))
            if not in_scope or not name.endswith('.xml'):
                continue
            tree=xml(archive.read(name))
            hits=replace_paragraphs(tree,drawing if ppt else word,old,new)
            if hits:
                matches+=hits
                changes[name]=etree.tostring(tree,encoding='utf-8')
    if not matches:
        raise ValueError('在同一段的连续文字中未找到指定原文。')
    write_zip(template,path,changes)
    return matches


def office(path,template,p):
    from .office_render import convert
    if template:
        n=rewrite_office(template,path,p)
        details=f'已替换 {n} 处，保留模板其他部件。'
    else:
        text=p['content']
        if path.suffix.lower()=='.docx':
            from docx import Document
            doc=Document()
            for line in text.splitlines():
                doc.add_paragraph(line)
            doc.save(path)
        else:
            from pptx import Presentation
            prs=Presentation()
            for segment in text.split('\n---\n'):
                parts=segment.splitlines() or ['']
                slide=prs.slides.add_slide(prs.slide_layouts[1])
                slide.shapes.title.text=parts[0]
                slide.placeholders[1].text='\n'.join(parts[1:])
            prs.save(path)
        details='生成可编辑文件。'
    if path.suffix.lower()=='.docx':
        from docx import Document
        Document(path)
    else:
        from pptx import Presentation
        if not Presentation(path).slides:
            raise ValueError('演示文稿无幻灯片。')
    checks=[('结构回读','PASSED',details)]
    try:
        pdf=convert(path,path.parent/'preview','pdf')
    except FileNotFoundError:
        return checks+[('页面渲染','PENDING','缺少 Office 转换器。')], []
    extra,images=preview(pdf,p)
    return checks+extra,[pdf,*images]


def make_pdf(text):
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    stream=BytesIO()
    cv=Canvas(stream,pagesize=(595,842))
    y=790
    cv.setFont('STSong-Light',12)
    for para in text.splitlines() or ['']:
        for line in textwrap.wrap(para,width=40) or ['']:
            if y<50:
                cv.showPage()
                cv.setFont('STSong-Light',12)
                y=790
            cv.drawString(45,y,line)
            y-=18
    cv.save()
    stream.seek(0)
    return stream


def pdf_file(path,inputs,p):
    from pypdf import PdfReader,PdfWriter
    writer=PdfWriter()
    if inputs:
        for idx,source in enumerate(inputs):
            reader=PdfReader(source)
            if idx==0 and 'pages' in p:
                pages=p['pages']
                if not pages or any(type(i)!=int or not 1<=i<=len(reader.pages) for i in pages):
                    raise ValueError('提取页码无效。')
                writer.append(reader,pages=[i-1 for i in pages])
            else:
                writer.append(reader)
        if 'fields' in p:
            fields=p['fields']
            if not fields or not set(fields)<=set(writer.get_fields() or {}):
                raise ValueError('指定的表单字段不存在。')
            writer.update_page_form_field_values(None,fields,auto_regenerate=False)
    if p.get('content'):
        writer.append(PdfReader(make_pdf(p['content'])))
    if not len(writer.pages):
        raise ValueError('请提供正文或源 PDF。')
    writer.write(path)
    writer.close()
    reader=PdfReader(path)
    if 'fields' in p and any(str((reader.get_fields() or {}).get(k,{}).get('/V'))!=str(v) for k,v in p['fields'].items()):
        raise ValueError('PDF 表单回读不一致。')
    checks=[('页面回读','PASSED',f'{len(reader.pages)} 页')]
    extra,images=preview(path,p)
    if p.get('ocr'):
        if type(p['ocr']) != bool:
            raise ValueError('OCR 参数应为布尔值。')
        from .document_ocr import recognize
        texts, language = recognize(images, p.get('ocr_language'))
        target = path.parent/'ocr.txt'
        target.write_text('\n\n'.join(f'[{image.stem}]\n{text}' for image,text in zip(images,texts)),encoding='utf-8')
        images.append(target)
        checks.append(('OCR','PASSED',f'Windows OCR {language}，处理 {len(texts)} 页。'))
    if p.get('extract_text'):
        textfile=path.parent/'extracted.txt'
        textfile.write_text('\n'.join(pg.extract_text() or '' for pg in reader.pages),encoding='utf-8')
        images.append(textfile)
    return checks+extra,images


def preview(pdf,p):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(pdf) as doc:
        pages=p.get('preview_pages',[1])
        if not isinstance(pages,list) or not 1<=len(pages)<=10 or any(type(i)!=int or not 1<=i<=len(doc) for i in pages):
            raise ValueError('预览页必须是有效的 1 至 10 个页码。')
        images=[]
        sizes=[]
        for i in dict.fromkeys(pages):
            page=doc[i-1]
            w,h=page.get_size()
            if not (0<w<=14400 and 0<h<=14400):
                raise ValueError('页面尺寸超限。')
            bitmap=page.render(scale=min(1.5,1600/max(w,h)))
            try:
                image=bitmap.to_pil()
                dest=pdf.parent/f'page-{i}.png'
                image.save(dest)
                sizes.append(f'{i}: {image.width}×{image.height}')
                images.append(dest)
            finally:
                bitmap.close()
                page.close()
    return [('页面渲染','PASSED','已生成可检查页面 '+', '.join(sizes))],images
