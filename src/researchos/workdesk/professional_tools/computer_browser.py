"""Explicit single-origin reading, local form edits, link following and bounded downloads."""
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit, urljoin, urlunsplit, quote
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request

MAX_BYTES = 8 * 1024 * 1024


def origin(url):
    if not isinstance(url,str) or len(url)>8192 or '\\' in url or any(ord(c)<=32 for c in url):
        raise ValueError('网址无效。')
    parsed=urlsplit(url)
    if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError('请选择不含凭据的 HTTP 或 HTTPS 地址。')
    return parsed.scheme, parsed.hostname.encode('idna').decode().lower(), parsed.port or (443 if parsed.scheme=='https' else 80)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args):
        return None


def read_url(url, boundary):
    if origin(url)!=boundary:
        raise ValueError('网页超出本次授权来源。')
    parsed=urlsplit(url)
    host=boundary[1]
    if ':' in host:
        host='['+host+']'
    if parsed.port is not None:
        host+=':'+str(parsed.port)
    transfer_url=urlunsplit((parsed.scheme,host,quote(parsed.path,safe="/%:@!$&'()*+,;=-._~"),
                             quote(parsed.query,safe="/%?:@!$&'()*+,;=-._~"),''))
    opener=build_opener(ProxyHandler({}),NoRedirect())
    try:
        with opener.open(Request(transfer_url,headers={'Accept-Encoding':'identity'}),timeout=12) as response:
            content=response.read(MAX_BYTES+1)
            mime=response.headers.get_content_type()
            charset=response.headers.get_content_charset() or 'utf-8'
    except HTTPError as exc:
        raise ValueError(f'目标返回 HTTP {exc.code}，未跟随重定向。') from None
    if len(content)>MAX_BYTES:
        raise ValueError('网页或下载超过 8 MiB。')
    return content,mime,charset


def operate(p,root):
    from playwright.sync_api import sync_playwright
    action=p['action']
    if action not in ('browser.read','browser.fill','browser.click','browser.download'):
        raise ValueError('不支持此浏览器操作。')
    boundary=origin(p['origin'])
    scope=urlsplit(p['origin'])
    if scope.path not in ('','/') or scope.query or scope.fragment:
        raise ValueError('授权来源只接受协议、主机和端口。')
    url=p['url']
    raw,mime,charset=read_url(url,boundary)
    receipt={'origin':p['origin'],'url':url,'profile':'EPHEMERAL','scripts':False}
    if action=='browser.download':
        target=root/'download.bin'
        target.write_bytes(raw)
        receipt.update(summary=f'已下载并保存 {len(raw)} 字节。',content_type=mime)
        return receipt,[(target,'application/octet-stream')]
    if mime not in ('text/html','text/plain','application/xhtml+xml'):
        raise ValueError('此资源不是可阅读的网页。')
    element_id=p.get('element_id')
    if action!='browser.read' and not (isinstance(element_id,str) and re.fullmatch('[A-Za-z_][A-Za-z0-9_-]{0,99}',element_id)):
        raise ValueError('请指定准确的页面元素 ID。')
    # Fetch only URLs explicitly named in the grant. Even same-origin images,
    # frames, scripts, forms, prefetches and redirects receive no transport.
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        try:
            ctx=browser.new_context(java_script_enabled=False,service_workers='block',accept_downloads=False)
            page=ctx.new_page()
            page.set_default_timeout(5000)
            doc={'html':raw,'type':mime,'charset':charset,'url':url.split('#')[0]}
            def respond(route):
                if route.request.is_navigation_request() and route.request.frame==page.main_frame and route.request.url.split('#')[0]==doc['url']:
                    media=doc['type']
                    encoding=doc['charset'] if re.fullmatch(r'[A-Za-z0-9_-]+',doc['charset']) else 'utf-8'
                    route.fulfill(body=doc['html'],headers={'Content-Type':media+'; charset='+encoding,
                        'Content-Security-Policy':"default-src 'none'; form-action 'none'; base-uri 'none'; sandbox allow-same-origin"})
                else:route.abort()
            # Use Chromium's canonical URL serialization when comparing a request.
            from urllib.parse import urldefrag
            canonical=lambda u: urldefrag(page.evaluate('(u)=>new URL(u).href',u))[0]
            doc['url']=canonical(url)
            ctx.route('**/*',respond)
            page.goto(url,wait_until='domcontentloaded',timeout=15000)
            if action!='browser.read':
                loc=page.locator('#'+element_id)
                if loc.count()!=1 or not loc.is_visible():
                    raise ValueError('未找到唯一可见的指定元素。')
                tag=loc.evaluate('(el)=>el.tagName.toLowerCase()')
                typ=(loc.get_attribute('type') or 'text').lower()
                if action=='browser.fill':
                    text=p.get('text')
                    if not isinstance(text,str) or len(text)>4096 or (tag!='textarea' and (tag!='input' or typ not in ('text','search'))):
                        raise ValueError('仅支持普通文本框或搜索框中的有限文本。')
                    loc.fill(text)
                    if loc.input_value()!=text:
                        raise ValueError('文本框回读不一致。')
                    receipt['summary']='已在本次页面填入并回读文本，未提交表单。'
                elif tag=='a':
                    href=loc.get_attribute('href')
                    if not href or loc.get_attribute('target') not in (None,'','_self') or loc.get_attribute('download') is not None:
                        raise ValueError('仅支持本页打开的普通链接。')
                    target=urljoin(url,href)
                    if target!=p.get('destination_url') or origin(target)!=boundary:
                        raise ValueError('链接目标与授权目的地址不符。')
                    data,media,encoding=read_url(target,boundary)
                    if media not in ('text/html','text/plain','application/xhtml+xml'):raise ValueError('目标不是网页。')
                    doc.update(html=data,type=media,charset=encoding,url=canonical(target))
                    # Navigate the verified link directly. No script or ambient page
                    # action is allowed to choose a different destination.
                    page.goto(target,wait_until='domcontentloaded')
                    receipt.update(summary='已打开选定链接并读取目标页面。',destination_url=page.url)
                elif tag=='input' and typ in ('checkbox','radio'):
                    loc.click()
                    receipt.update(summary='已操作选定控件并回读状态。',checked=loc.is_checked())
                elif tag=='summary':
                    loc.click()
                    receipt.update(summary='已操作选定折叠区并回读状态。',expanded=loc.evaluate('(e)=>e.parentElement.open'))
                else:raise ValueError('请选用普通同源链接、复选框、单选框或折叠区。')
                receipt['element_id']=element_id
            else:receipt['summary']='已在独立浏览器页面读取正文。'
            text=page.locator('body').inner_text()[:32000]
            receipt.update(title=page.title(),text=text)
            return receipt,[]
        finally:browser.close()
