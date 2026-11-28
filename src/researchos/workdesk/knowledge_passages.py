"""Extract bounded, addressable fragments from a stored library item."""
import csv
from hashlib import sha256
from io import BytesIO, StringIO
import json
from pathlib import PurePath
from zipfile import ZipFile, BadZipFile


def paragraphs(text):
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip():
            yield f'paragraph:{number}', line.strip()


def office_content(content, suffix):
    from openpyxl.utils import get_column_letter
    with ZipFile(BytesIO(content)) as archive:
        if len(archive.infolist()) > 3000 or sum(i.file_size for i in archive.infolist()) > 32 * 1024 * 1024:
            raise ValueError('Office 解压大小超过本地索引限制。')
    if suffix == '.docx':
        from docx import Document
        for idx, p in enumerate(Document(BytesIO(content)).paragraphs, 1):
            if p.text.strip():
                yield f'paragraph:{idx}', p.text
    elif suffix == '.pptx':
        from pptx import Presentation
        for idx, slide in enumerate(Presentation(BytesIO(content)).slides, 1):
            yield f'slide:{idx}', '\n'.join(s.text for s in slide.shapes if s.has_text_frame)
    else:
        from openpyxl import load_workbook
        book = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
        try:
            for sheet in book:
                # Bound even a malformed sparse worksheet before expanding a rectangular row.
                if (sheet.max_column or 0) > 1024 or (sheet.max_row or 0) > 20000:
                    raise ValueError('工作表行列数超过本地索引限制。')
                for r, row in enumerate(sheet.iter_rows(max_col=min(sheet.max_column or 1024, 1024),
                                                        max_row=min(sheet.max_row or 20000, 20000), values_only=True), 1):
                    for c, value in enumerate(row, 1):
                        if value is not None:
                            yield f'sheet:{sheet.title}!{get_column_letter(c)}{r}', str(value)
        finally:
            book.close()


def file_content(row):
    from openpyxl.utils import get_column_letter
    content = row['content']
    if sha256(content).hexdigest() != row['sha256']:
        raise ValueError('来源文件摘要校验失败。')
    suffix = PurePath(row['filename']).suffix.lower()
    if suffix in ('.xlsx', '.docx', '.pptx'):
        yield from office_content(content, suffix)
    elif row['preview'] == 'pdf':
        import pypdfium2
        with pypdfium2.PdfDocument(content) as pdf:
            for page_no in range(min(len(pdf), 256)):
                page = pdf[page_no]
                try:
                    text = page.get_textpage()
                    try:
                        yield f'page:{page_no+1}', text.get_text_bounded()
                    finally:
                        text.close()
                finally:
                    page.close()
            if len(pdf) > 256:
                raise ValueError('仅收录前 256 页。')
    elif row['preview'] == 'text':
        text = content.decode('utf-8-sig')
        if suffix in ('.csv', '.tsv'):
            for i, cells in enumerate(csv.reader(StringIO(text), delimiter='\t' if suffix == '.tsv' else ','), 1):
                for j, value in enumerate(cells, 1):
                    if value.strip():
                        yield f'cell:{get_column_letter(j)}{i}', value
        else:
            yield from paragraphs(text)
    else:
        raise ValueError('此附件仅索引题名和备注，未提取正文。')


def passages(row):
    def parts():
        for field in ('title', 'note', 'url', 'filename'):
            if row[field]:
                yield field, row[field]
        for idx, tag in enumerate(json.loads(row['tags']), 1):
            yield f'tag:{idx}', tag
        if row['body']:
            yield from paragraphs(row['body'])
        if row['filename']:
            yield from file_content(row)

    chunks, limits = [], []
    total = 0
    try:
        for locator, text in parts():
            text = text.strip()
            for offset in range(0, len(text), 1400):
                fragment = text[offset:offset+1400]
                total += len(fragment)
                if len(chunks) >= 256 or total > 131072:
                    return chunks, ['已达到每份资料 256 个位置或 131072 字的索引上限。']
                key = locator if not offset else f'{locator}@{offset}'
                chunks.append({'locator': key, 'text': fragment})
        if row['preview'] == 'pdf' and not any(c['locator'].startswith('page:') for c in chunks):
            limits.append('PDF 未提取到文字层。')
    except (ValueError, BadZipFile, csv.Error) as exc:
        limits.append(str(exc)[:180])
    except Exception as exc:
        limits.append('该文件正文读取失败（' + type(exc).__name__ + '）。')
    return chunks, limits
