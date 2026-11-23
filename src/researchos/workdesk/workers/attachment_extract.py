"""User-selected local text reader. No network, OCR, macros or external content."""
import json
import base64
from io import BytesIO
from pathlib import Path
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

MAX_TEXT=1024*1024
class NoDtdTreeBuilder(ET.TreeBuilder):
    def doctype(self,name,pubid,system):
        # Parser callback runs after XML encoding recognition, before any entity
        # expansion. ASCII byte searches miss UTF-16/other supported encodings.
        raise ValueError('拒绝XML文档类型及实体声明。')
def limited(text):
    if len(text.encode('utf-8'))>MAX_TEXT:raise ValueError('提取文字超过1MiB；请缩小材料或选择PDF页码，不静默截断。')
    return text

def office(path,raw):
    with zipfile.ZipFile(BytesIO(raw)) as z:
        infos=z.infolist()
        if len(infos)>4000 or sum(i.file_size for i in infos)>32*1024*1024:raise ValueError('OOXML解压范围超过本地读取上限。')
        if any(i.flag_bits&1 or i.file_size>8*1024*1024 or i.file_size>max(1024*1024,i.compress_size*200) for i in infos):raise ValueError('OOXML加密或压缩/单项范围不支持。')
        names=z.namelist()
        if len(names)!=len(set(names)):raise ValueError('OOXML含重复条目。')
        if any('vbaProject' in n or n.startswith(('/','\\')) or '..' in n.split('/') for n in names):raise ValueError('不读取宏或越界归档路径。')
        def xml(name):
            raw=z.read(name)
            return ET.fromstring(raw,parser=ET.XMLParser(target=NoDtdTreeBuilder()))
        suffix=path.suffix.lower();parts=[]
        if suffix=='.docx':
            selected=[n for n in names if re.fullmatch(r'word/(document|footnotes|endnotes|header[0-9]+|footer[0-9]+)\.xml',n)]
            if 'word/document.xml' not in selected:raise ValueError('DOCX缺少正文。')
            for n in sorted(selected):
                root=xml(n);paragraphs=[''.join(e.itertext()) for e in root.iter() if e.tag.endswith('}p')]
                parts.append(n+'\n'+'\n'.join(paragraphs))
        elif suffix=='.xlsx':
            shared=[]
            if 'xl/sharedStrings.xml' in names:
                shared=[''.join(e.itertext()) for e in xml('xl/sharedStrings.xml') if e.tag.endswith('}si')]
            selected=sorted(n for n in names if re.fullmatch(r'xl/worksheets/sheet[0-9]+\.xml',n))
            if not selected:raise ValueError('XLSX缺少工作表。')
            for n in selected:
                lines=[]
                for cell in xml(n).iter():
                    if not cell.tag.endswith('}c'):continue
                    vals={e.tag.rsplit('}',1)[-1]:''.join(e.itertext()) for e in cell}
                    value=vals.get('v',vals.get('is',''))
                    if cell.attrib.get('t')=='s':value=shared[int(value)]
                    lines.append(cell.attrib.get('r','?')+' = '+value+(' [formula='+vals['f']+']' if 'f' in vals else ''))
                parts.append(n+'\n'+'\n'.join(lines))
        else:
            selected=sorted(n for n in names if re.fullmatch(r'ppt/(slides/slide|notesSlides/notesSlide)[0-9]+\.xml',n))
            if not selected:raise ValueError('PPTX缺少幻灯片。')
            for n in selected:parts.append(n+'\n'+'\n'.join(e.text or '' for e in xml(n).iter() if e.tag.endswith('}t')))
        return limited('\n\n'.join(parts)),{'reader':'STDLIB_OOXML_TEXT','parts':selected,'losses':['layout','images','embedded objects','external links not followed'],'ocr':False,'formulas_executed':False}

def main():
    config=json.loads(sys.stdin.readline());path=Path(config['file']);raw=base64.b64decode(config['data'],validate=True)
    if len(raw)>8*1024*1024:raise ValueError('附件超过8MiB。')
    if config['kind']=='pdf':
        from pypdf import PdfReader
        reader=PdfReader(BytesIO(raw))
        if reader.is_encrypted:raise ValueError('加密PDF未读取，不收集密码或自动解密。')
        pages=config.get('pages');count=len(reader.pages)
        if pages:
            nums=[int(i) for i in pages.split('-')];start=nums[0];end=nums[-1]
        else:start=1;end=count
        if not 1<=start<=end<=count or end-start>=50:raise ValueError('PDF范围越界或超过50页；请选择明确页码，不静默跳页。')
        text=limited('\n\n'.join('PAGE '+str(i+1)+'\n'+(reader.pages[i].extract_text() or '') for i in range(start-1,end)))
        if not any((reader.pages[i].extract_text() or '').strip() for i in range(start-1,end)):raise ValueError('PDF未提取到文字；不自动OCR扫描件。')
        provenance={'reader':'pypdf','pages':[start,end],'total_pages':count,'ocr':False,'layout_and_images_retained':False,'remaining_pages_not_read':count-(end-start+1)}
    else:text,provenance=office(path,raw)
    print(json.dumps({'text':text,'provenance':provenance},ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except Exception as exc:print(type(exc).__name__+': '+str(exc),file=sys.stderr);raise SystemExit(1)
