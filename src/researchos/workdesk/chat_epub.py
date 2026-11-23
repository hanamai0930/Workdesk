"""Extract local text chapters in EPUB spine order, without rendering content."""
import io
import posixpath
import zipfile
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET


class ChapterText(HTMLParser):
    blocks = {'p', 'div', 'section', 'article', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
    hidden = {'script', 'style', 'noscript', 'svg', 'object', 'iframe'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.body = False
        self.skip = []
        self.text = io.StringIO()

    def handle_starttag(self, tag, attrs):
        tag = tag.split(':')[-1]
        if tag == 'body':
            self.body = True
        if tag in self.hidden:
            self.skip.append(tag)
        if tag in self.blocks:
            self.handle_data('\n')

    def handle_endtag(self, tag):
        tag = tag.split(':')[-1]
        if tag in self.blocks:
            self.handle_data('\n')
        if self.skip and self.skip[-1] == tag:
            self.skip.pop()
        if tag == 'body':
            self.body = False

    def handle_data(self, data):
        if self.body and not self.skip:
            self.text.write(data)


def _member(base, href):
    url = urlsplit(href)
    path = unquote(url.path)
    if url.scheme or url.netloc or not path or path.startswith('/') or '\\' in path:
        raise ValueError('EPUB 章节必须引用书内文件。')
    name = posixpath.normpath(posixpath.join(base, path))
    if name == '..' or name.startswith('../'):
        raise ValueError('EPUB 章节路径超出书内目录。')
    return name


def _decode(raw):
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16')
    return raw.decode('utf-8-sig')


def _read_book(book):
    remaining = 32 * 1024 * 1024
    def read(name):
        nonlocal remaining
        info = book.getinfo(name)
        if info.flag_bits & 1:
            raise ValueError('EPUB 章节已加密，请使用未加密的文本版本。')
        if info.file_size > min(remaining, 16 * 1024 * 1024):
            raise ValueError('EPUB 目录或正文解压后过大，请按章节拆分。')
        remaining -= info.file_size
        return _decode(book.read(info))

    def xml(name):
        value = read(name)
        if '<!ENTITY' in value.upper() or '\x00' in value:
            raise ValueError('EPUB 目录包含不支持的 XML 声明。')
        return ET.fromstring(value)

    container = xml('META-INF/container.xml')
    root = container.find('./{*}rootfiles/{*}rootfile')
    package = _member('', root.attrib['full-path'])
    document = xml(package)
    items = {item.attrib['id']: item for item in document.findall('./{*}manifest/{*}item')}
    encrypted = set()
    if 'META-INF/encryption.xml' in book.namelist():
        encrypted = {_member('', entry.attrib['URI']) for entry in
                     xml('META-INF/encryption.xml').findall('.//{*}CipherReference')}
    chapters = []
    length = 0
    for ref in document.findall('./{*}spine/{*}itemref'):
        item = items[ref.attrib['idref']]
        if item.get('media-type', '').startswith('image/'):
            continue
        if item.get('media-type') not in {'application/xhtml+xml', 'text/html'}:
            raise ValueError('此 EPUB 含有无法读取的正文格式，请改用文本版本。')
        name = _member(posixpath.dirname(package), item.attrib['href'])
        if name in encrypted:
            raise ValueError('EPUB 章节已加密，请使用未加密的文本版本。')
        parser = ChapterText()
        parser.feed(read(name))
        parser.close()
        text = '\n'.join(line.strip() for line in parser.text.getvalue().splitlines() if line.strip())
        if text:
            length += len(text) + (2 if chapters else 0)
            if length > 6000000:
                raise ValueError('EPUB 正文过长，请按章节拆分。')
            chapters.append(text)
    if not chapters:
        raise ValueError('EPUB 中没有可提取的章节文字；图片版请上传相应页面图片。')
    return '\n\n'.join(chapters)


def epub_text(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as book:
            return _read_book(book)
    except (zipfile.BadZipFile, KeyError, AttributeError, ET.ParseError, UnicodeError, NotImplementedError, OSError):
        raise ValueError('EPUB 目录或章节损坏，或编码不受支持。') from None
