"""Local Chat attachment preparation. No provider request or executable files."""
import base64
import binascii
import io
from pathlib import PurePath
import time
import zipfile
from xml.etree import ElementTree

from .store import uid

MAX_FILE=8*1024*1024
TEXT_EXT={'.txt','.md','.csv','.json','.log','.py','.js','.ts','.tsx','.html','.css','.xml','.yaml','.yml','.sql','.ini','.toml','.rst'}


def prepare(p):
    name=p.get('name');encoded=p.get('data')
    if not isinstance(name,str) or not 1<=len(name)<=200 or '/' in name or '\\' in name:raise ValueError('附件名称无效。')
    if not isinstance(encoded,str) or len(encoded)>((MAX_FILE+2)//3)*4:raise ValueError('每个文件最多8 MiB。')
    try:raw=base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error):raise ValueError('附件编码无效。')
    if not raw or len(raw)>MAX_FILE:raise ValueError('文件为空或超过8 MiB。')
    ext=PurePath(name).suffix.lower();mime=None
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):mime='image/png'
    elif raw.startswith(b'\xff\xd8\xff'):mime='image/jpeg'
    elif raw[:6] in (b'GIF87a',b'GIF89a'):mime='image/gif'
    elif raw[:4]==b'RIFF' and raw[8:12]==b'WEBP':mime='image/webp'
    row={'id':'chat-file-'+uid(),'name':name,'size':len(raw),'created':time.time(),'chat_id':None}
    if mime:
        from PIL import Image
        try:
            with Image.open(io.BytesIO(raw)) as im:
                if max(im.size)>8192:raise ValueError('图片每边最多8192像素。')
                im.verify()
        except Exception:raise ValueError('图片内容无效或尺寸超过8192像素。')
        row.update(kind='image',data_url='data:'+mime+';base64,'+encoded)
        return row
    try:
        if ext=='.pdf':
            from pypdf import PdfReader
            pdf=PdfReader(io.BytesIO(raw))
            if pdf.is_encrypted:raise ValueError('请先解密PDF。')
            parts=[];size=0
            for page in pdf.pages:
                part=page.extract_text() or '';size+=len(part)
                if size>6000000:raise ValueError('PDF文字过大，请分文件发送。')
                parts.append(part)
            text='\n'.join(parts)
        elif ext=='.epub':
            from .chat_epub import epub_text
            text=epub_text(raw)
        elif ext=='.docx':
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                item=z.getinfo('word/document.xml')
                if item.file_size>16*1024*1024:raise ValueError('文档解压后过大。')
                root=ElementTree.fromstring(z.read(item))
            ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            text='\n'.join(''.join(t.text or '' for t in para.iter(ns+'t')) for para in root.iter(ns+'p'))
        elif ext in TEXT_EXT:
            try:text=raw.decode('utf-8-sig')
            except UnicodeDecodeError:text=raw.decode('gb18030')
            if '\x00' in text:raise ValueError('此文件不是受支持的纯文本。')
        else:raise ValueError('支持文本、PDF文字、DOCX、EPUB及PNG/JPEG/GIF/WebP图片。')
    except ImportError:raise ValueError('本机缺少PDF读取组件，请修复Workdesk依赖。')
    except ValueError:raise
    except Exception:raise ValueError('文件解析失败；扫描PDF请改为上传图片，不会静默截取正文。')
    if not text.strip():raise ValueError('文件没有可提取文字；扫描件请改为上传图片。')
    if len(text)>6000000:raise ValueError('文件文字过大，请分文件发送。')
    row.update(kind='text',text=text);return row


def metadata(row):
    from .chat_context import text_tokens
    return {**{k:row[k] for k in ('id','name','size','kind')},'tokens':1024 if row['kind']=='image' else text_tokens(row['text'])}


class ChatFiles:
    def __init__(self,service):self.s=service;self.c=service.collaboration
    def attach(self,db,p):
        row=prepare(p);self.c.put(db,'ui_chat_attachment',row)
        return {'attachment':metadata(row),'local_only':True,'no_dispatch':True}
    def remove(self,db,p):
        row=self.c.get(db,'ui_chat_attachment',p.get('id'))
        if row['chat_id']:raise ValueError('已发送的附件随聊天记录保留。')
        db.execute("DELETE FROM collaboration WHERE kind='ui_chat_attachment' AND id=?",(row['id'],))
        return {'deleted':row['id'],'no_dispatch':True}
    def resolve(self,db,ids,chat_id,model):
        if not isinstance(ids,list) or len(ids)>10 or len(set(ids))!=len(ids):raise ValueError('每条消息最多10个不同附件。')
        rows=[self.c.get(db,'ui_chat_attachment',i) for i in ids]
        if any(r['chat_id'] not in (None,chat_id) for r in rows):raise ValueError('附件属于另一段聊天。')
        if model!='deepseek-flash' and any(r['kind']=='image' for r in rows):raise ValueError('当前 API 聊天配置不接收图片，请改用 DeepSeek Flash 或移除图片。')
        return rows

def user_content(content,files):
    if not files:return content
    text=content+'\n\n'+ '\n\n'.join('[附件：'+r['name']+'，以下为文件内容]\n'+r['text']+'\n[附件结束]' for r in files if r['kind']=='text')
    images=[{'type':'image_url','image_url':{'url':r['data_url']}} for r in files if r['kind']=='image']
    return [{'type':'text','text':text},*images] if images else text
