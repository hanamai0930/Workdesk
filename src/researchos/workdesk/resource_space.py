"""Personal resources stored in the active Workdesk library and its backups."""

import base64
import binascii
import json
import re
import time
import unicodedata
from hashlib import sha256
from pathlib import PurePath
from urllib.parse import urlsplit
from uuid import UUID

from .store import canonical, uid


MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILE_BASE64 = 4 * ((MAX_FILE_BYTES + 2) // 3)
MAX_BODY = 262_144
MAX_NOTE = 4000
MAX_TITLE = 160
MIME_SYNTAX = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9.+-]{0,63}/[a-zA-Z0-9][a-zA-Z0-9.+-]{0,63}')
PRESENTATIONS = {'.ppt', '.pptx', '.pptm', '.odp', '.key'}
SPREADSHEETS = {'.xls', '.xlsx', '.xlsm', '.ods', '.csv', '.tsv', '.numbers'}
COMMON_FIELDS = {'title', 'note', 'tags', 'collection_id'}
CONTENT_FIELDS = {'page': 'body', 'link': 'url', 'file': 'file'}
SUMMARY_FIELDS = '''r.id, r.collection_id, r.title, r.kind, r.category, r.note, r.tags,
    r.created, r.updated, r.trashed_at, r.revision, f.filename, f.mime, f.bytes, f.preview'''
FILE_JOIN = 'LEFT JOIN resource_space_files AS f ON f.item_id=r.id'


def valid_id(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
    except ValueError:
        raise ValueError('资料或收藏夹标识无效。') from None
    return value


def checked_text(value, label, limit, *, trim=True, required=False):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise ValueError(f'{label}须为不超过 {limit} 字的文字。')
    value.encode('utf-8')
    if trim:
        value = value.strip()
    if required and not value:
        raise ValueError(f'请输入{label}。')
    return value


def fold(value):
    return unicodedata.normalize('NFKC', value).casefold()


def checked_tags(value):
    if not isinstance(value, list) or len(value) > 24:
        raise ValueError('每项资料最多保存 24 个标签。')
    result = {}
    for name in value:
        tag = checked_text(name, '标签', 64, required=True)
        result.setdefault(fold(tag), tag)
    return sorted(result.values(), key=fold)


def web_url(value):
    value = checked_text(value, '链接地址', 4000, required=True)
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None
            or '\\' in value or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ValueError('请输入完整的 HTTP 或 HTTPS 地址。')
    try:
        parsed.port
    except ValueError:
        raise ValueError('链接端口无效。') from None
    return value


def file_format(content):
    if content.startswith(b'\x89PNG\r\n\x1a\n') and content[12:16] == b'IHDR':
        return 'image/png', 'image', ''
    if content.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg', 'image', ''
    if content.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif', 'image', ''
    if content.startswith(b'RIFF') and content[8:12] == b'WEBP':
        return 'image/webp', 'image', ''
    if content.startswith(b'%PDF-'):
        return 'application/pdf', 'pdf', ''
    try:
        if b'\x00' not in content:
            text = content.decode('utf-8-sig')
            if not any(ord(ch) < 32 and ch not in '\t\r\n\f' for ch in text[:MAX_BODY]):
                return 'text/plain', 'text', fold(text[:MAX_BODY])
    except UnicodeDecodeError:
        pass
    return 'application/octet-stream', None, ''


def file_fields(value):
    if not isinstance(value, dict) or set(value) != {'name', 'mime', 'data'}:
        raise ValueError('请提供文件名称、类型和内容。')
    name = checked_text(value['name'], '文件名', 255, trim=False, required=True)
    if not name.strip() or name.strip() in {'.', '..'} or any(ch in '/\\:' or ord(ch) < 32 for ch in name):
        raise ValueError('文件名须为单个名称。')
    mime = value['mime']
    if not isinstance(mime, str) or mime and not MIME_SYNTAX.fullmatch(mime):
        raise ValueError('文件类型格式无效。')
    encoded = value['data']
    if not isinstance(encoded, str) or len(encoded) > MAX_FILE_BASE64:
        raise ValueError('请选择 8 MiB 以内的文件。')
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('文件内容编码无效。') from None
    if len(content) > MAX_FILE_BYTES:
        raise ValueError('请选择 8 MiB 以内的文件。')
    trusted_mime, preview, search_text = file_format(content)
    suffix = PurePath(name).suffix.lower()
    if preview == 'image':
        category = 'image'
    elif suffix in PRESENTATIONS:
        category = 'presentation'
    elif suffix in SPREADSHEETS:
        category = 'spreadsheet'
    else:
        category = 'file'
    return category, {'filename': name, 'mime': trusted_mime, 'preview': preview, 'sha256': sha256(content).hexdigest(),
                      'bytes': len(content), 'content': content, 'search_text': search_text}


def item_dict(row, *, detail=False):
    item = dict(row)
    item['tags'] = json.loads(item['tags'])
    item['trashed'] = item['trashed_at'] is not None
    if item['kind'] != 'file':
        for key in ('filename', 'mime', 'bytes', 'preview'):
            item.pop(key, None)
    if detail:
        if item['kind'] != 'page':
            del item['body']
        if item['kind'] != 'link':
            del item['url']
    return item


def item_index(row, filename=None):
    return fold('\n'.join(filter(None, (row['title'], row['note'], *row['tags'], row.get('body'), row.get('url'), filename))))


class ResourceSpace:
    def __init__(self, store):
        self.store = store

    def handlers(self):
        return {'resource-space.collection-create': self.collection_create, 'resource-space.create': self.create,
                'resource-space.update': self.update, 'resource-space.trash': self.trash}

    def collection(self, db, identity):
        if identity is not None and not db.execute('SELECT 1 FROM resource_space_collections WHERE id=?',
                                                   (valid_id(identity),)).fetchone():
            raise ValueError('收藏夹不存在。')
        return identity

    def catalog(self, query='', collection_id=None, view='active'):
        if view not in ('active', 'trash'):
            raise ValueError('资料视图无效。')
        terms = dict.fromkeys(fold(checked_text(query, '搜索词', 300)).split())
        filters = ['r.trashed_at IS NULL' if view == 'active' else 'r.trashed_at IS NOT NULL']
        params = []
        with self.store.read() as db:
            if collection_id:
                self.collection(db, collection_id)
                filters.append('r.collection_id=?')
                params.append(collection_id)
            for term in terms:
                filters.append('(instr(r.search_text,?)>0 OR instr(f.search_text,?)>0)')
                params.extend((term, term))
            order = 'r.updated DESC,r.id' if view == 'active' else 'r.trashed_at DESC,r.id'
            rows = db.execute(f'SELECT {SUMMARY_FIELDS} FROM resource_space_items AS r {FILE_JOIN} '
                              f'WHERE {" AND ".join(filters)} ORDER BY {order}', params)
            items = [item_dict(row) for row in rows]
            collections = [dict(row) for row in db.execute('SELECT * FROM resource_space_collections ORDER BY created,id')]
        return {'items': items, 'collections': collections}

    def read_item(self, db, identity):
        row = db.execute(f'SELECT {SUMMARY_FIELDS},r.body,r.url FROM resource_space_items AS r {FILE_JOIN} WHERE r.id=?',
                         (valid_id(identity),)).fetchone()
        if not row:
            raise ValueError('资料不存在。')
        return item_dict(row, detail=True)

    def item(self, identity):
        with self.store.read() as db:
            return {'item': self.read_item(db, identity)}

    def file(self, identity):
        with self.store.read() as db:
            row = db.execute('SELECT filename,mime,preview,sha256,bytes,content FROM resource_space_files WHERE item_id=?',
                             (valid_id(identity),)).fetchone()
        if not row:
            raise ValueError('资料文件不存在。')
        content = row['content']
        if len(content) != row['bytes'] or sha256(content).hexdigest() != row['sha256']:
            raise ValueError('保存的资料文件未通过完整性检查。')
        return {'file': {'name': row['filename'], 'mime': row['mime'], 'preview': row['preview'], 'sha256': row['sha256'],
                         'bytes': row['bytes'], 'data': base64.b64encode(content).decode('ascii')}}

    def collection_create(self, db, payload):
        if set(payload) - {'title', 'description'}:
            raise ValueError('收藏夹字段无效。')
        now = time.time()
        row = {'id': uid(), 'title': checked_text(payload.get('title'), '收藏夹名称', MAX_TITLE, required=True),
               'description': checked_text(payload.get('description', ''), '收藏夹说明', 1000),
               'revision': 1, 'created': now, 'updated': now}
        db.execute('INSERT INTO resource_space_collections(id,title,description,revision,created,updated) '
                   'VALUES (:id,:title,:description,:revision,:created,:updated)', row)
        return {'collection': row, 'no_dispatch': True}

    def fields(self, db, payload):
        changes = {}
        if 'title' in payload:
            changes['title'] = checked_text(payload['title'], '标题', MAX_TITLE, required=True)
        if 'note' in payload:
            changes['note'] = checked_text(payload['note'], '备注', MAX_NOTE)
        if 'tags' in payload:
            changes['tags'] = checked_tags(payload['tags'])
        if 'collection_id' in payload:
            changes['collection_id'] = self.collection(db, payload['collection_id'])
        if 'body' in payload:
            changes['body'] = checked_text(payload['body'], '页面正文', MAX_BODY, trim=False)
        if 'url' in payload:
            changes['url'] = web_url(payload['url'])
        return changes

    def create(self, db, payload):
        kind = payload.get('kind')
        if (not isinstance(kind, str) or kind not in CONTENT_FIELDS
                or set(payload) - (COMMON_FIELDS | {'kind', CONTENT_FIELDS[kind]})):
            raise ValueError('资料类型或字段无效。')
        now = time.time()
        row = {'id': uid(), 'kind': kind, 'category': kind, 'note': '', 'tags': [], 'collection_id': None,
               'body': '' if kind == 'page' else None, 'url': None, 'revision': 1, 'created': now, 'updated': now,
               **self.fields(db, payload)}
        if 'title' not in row:
            raise ValueError('请输入标题。')
        if kind == 'link' and row['url'] is None:
            raise ValueError('请输入链接地址。')
        file = None
        if kind == 'file':
            row['category'], file = file_fields(payload.get('file'))
        row['search_text'] = item_index(row, file['filename'] if file else None)
        row['tags'] = canonical(row['tags'])
        db.execute('''INSERT INTO resource_space_items(id,kind,category,title,note,tags,collection_id,body,url,
            search_text,revision,created,updated) VALUES (:id,:kind,:category,:title,:note,:tags,:collection_id,:body,:url,
            :search_text,:revision,:created,:updated)''', row)
        if file:
            db.execute('''INSERT INTO resource_space_files(item_id,filename,mime,preview,sha256,bytes,content,search_text)
                VALUES (:item_id,:filename,:mime,:preview,:sha256,:bytes,:content,:search_text)''',
                       {'item_id': row['id'], **file})
        return {'item': self.read_item(db, row['id']), 'no_dispatch': True}

    def checked_item(self, db, payload):
        row = self.read_item(db, payload['id'])
        if type(payload['revision']) is not int or row['revision'] != payload['revision']:
            raise ValueError('资料已更改，请刷新后重试。')
        return row

    def update(self, db, payload):
        if set(payload) != {'id', 'revision', 'changes'} or not isinstance(payload['changes'], dict) or not payload['changes']:
            raise ValueError('资料更新字段无效。')
        row = self.checked_item(db, payload)
        if row['trashed']:
            raise ValueError('请从回收站恢复后再编辑。')
        allowed = COMMON_FIELDS | ({CONTENT_FIELDS[row['kind']]} if row['kind'] != 'file' else set())
        if set(payload['changes']) - allowed:
            raise ValueError('该资料类型不支持这些更新。')
        changes = self.fields(db, payload['changes'])
        changes['search_text'] = item_index({**row, **changes}, row.get('filename'))
        if 'tags' in changes:
            changes['tags'] = canonical(changes['tags'])
        fields = ','.join(f'{field}=?' for field in changes)
        db.execute(f'UPDATE resource_space_items SET {fields},updated=?,revision=revision+1 WHERE id=?',
                   (*changes.values(), time.time(), row['id']))
        return {'item': self.read_item(db, row['id']), 'no_dispatch': True}

    def trash(self, db, payload):
        if set(payload) != {'id', 'revision', 'trashed'} or type(payload['trashed']) is not bool:
            raise ValueError('资料回收或恢复字段无效。')
        row = self.checked_item(db, payload)
        if row['trashed'] != payload['trashed']:
            now = time.time()
            db.execute('UPDATE resource_space_items SET trashed_at=?,updated=?,revision=revision+1 WHERE id=?',
                       (now if payload['trashed'] else None, now, row['id']))
            row = self.read_item(db, row['id'])
        return {'item': row, 'no_dispatch': True}
