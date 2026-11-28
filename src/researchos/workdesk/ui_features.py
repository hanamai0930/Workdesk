"""Durable UI actions. Model text never becomes a control command."""
import base64
import binascii
import json
import math
from pathlib import Path
import time
import uuid

from . import workspace
from .contracts import bounded_text, integer
from .store import ACTIVE, UNKNOWN, canonical, uid
from .workbench import scratch


class UIFeatures:
    def __init__(self, service):
        self.s = service
        self.c = service.collaboration
        from .chat import ChatService
        self.chats = ChatService(service)
        from .appearance import Appearance
        self.appearance = Appearance(service)
        # A missed deadline after restart must be reviewed, never replayed.
        with service.store.write() as db:
            for row in self.c.all(db, 'ui_schedule'):
                if row['state'] == 'ACTIVE':
                    row.update(state='PAUSED', reason='服务重启，请核对后继续计划。')
                    self.c.put(db, 'ui_schedule', row)

    def handlers(self):
        return {'settings.appearance.save': self.appearance.save,
                'ui.import': self.import_legacy, 'draft.save': self.draft_save,
                'draft.delete': self.draft_delete, 'chat.create': self.chat_create,
                'chat.send': self.chat_send, 'chat.record': self.chat_record,
                'chat.move': self.chats.move, 'chat.fork': self.chats.fork,
                'chat.bind-web': self.bind_web, 'work.fork': self.fork,
                'chat.api-send':self.chats.api.send,'chat.api-stop':self.chats.api.stop,'chat.delete':self.chats.delete,
                'chat.api-attach':self.chats.api.files.attach,'chat.api-remove-attachment':self.chats.api.files.remove,
                'work.upload': self.upload, 'work.files-select': self.files_select, 'schedule.save': self.schedule_save,
                'schedule.state': self.schedule_state, 'schedule.fire': self.schedule_fire}

    def catalog(self):
        services=self.chats.catalog()
        with self.s.store.read() as db:
            chats = [self.chat_view(db, c) for c in self.c.all(db, 'ui_chat')]
            scopes={r['record_id']:r['project_id'] for r in db.execute("SELECT * FROM record_scopes WHERE kind='chat'")}
            for chat in chats:chat['list_project']=scopes.get(chat['id'])
            return {'chats': chats, 'chat_services':services, 'drafts': self.c.all(db, 'ui_draft'),
                    'schedules': self.c.all(db, 'ui_schedule'), 'no_dispatch': True}

    def chat_view(self, db, row):
        return self.chats.chat_view(db, row)

    def events(self, db, identity):
        return [{**dict(e), 'payload': json.loads(e['payload'])} for e in db.execute(
            'SELECT * FROM events WHERE task_id=? ORDER BY seq', (identity,))]

    def draft_save(self, db, p):
        identity = bounded_text(p.get('id'), '草稿身份', 180)
        if identity.startswith('composer:chat:') and identity!='composer:chat:home':
            self.c.get(db,'ui_chat',identity[len('composer:chat:'):])
        if identity.startswith('composer:work:') and identity!='composer:work:home':
            target=identity[len('composer:work:'):]
            for kind in ('task','dialogue'):self.s.work_records.guard(db,kind,target)
        existing=db.execute('SELECT kind FROM collaboration WHERE id=?',(identity,)).fetchone()
        if existing and existing['kind']!='ui_draft':raise ValueError('该身份已被另一类记录使用。')
        value = p.get('value')
        maximum=24000000 if identity.startswith('composer:chat:') else 80000
        if not isinstance(value, dict) or len(canonical(value)) > maximum:
            raise ValueError('草稿须为最多80000字符的配置对象。')
        old = next((r for r in self.c.all(db, 'ui_draft') if r['id'] == identity), None)
        if old and p.get('revision') != old['revision']:
            raise ValueError('草稿在另一窗口已更新，请先刷新草稿箱；本窗口内容仍保留。')
        if not old and p.get('revision', 0) != 0:
            raise ValueError('草稿版本已变化。')
        row = {'id': identity, 'value': value, 'revision': (old or {}).get('revision', 0) + 1,
               'updated': time.time(), 'created': (old or {}).get('created', time.time())}
        self.c.put(db, 'ui_draft', row)
        return {'draft': row, 'no_dispatch': True}

    def draft_delete(self, db, p):
        row = self.c.get(db, 'ui_draft', p.get('id'))
        if p.get('revision') != row['revision']:
            raise ValueError('草稿版本已变化，请刷新后删除。')
        db.execute("DELETE FROM collaboration WHERE kind='ui_draft' AND id=?", (row['id'],))
        return {'deleted': row['id'], 'no_dispatch': True}

    def import_legacy(self, db, p):
        chats = p.get('chats', []); drafts = p.get('drafts', [])
        if not isinstance(chats, list) or not isinstance(drafts, list) or len(chats) > 1000 or len(drafts) > 100:
            raise ValueError('旧记录数量不支持。')
        existing = {r['id'] for r in self.c.all(db, 'ui_chat')}|{r['chat_id'] for r in self.c.all(db,'ui_chat_deleted')}
        imported = []
        from .chat import WEB_PROVIDERS
        for c in chats:
            identity = 'web-' + bounded_text(c.get('id'), '旧聊天身份', 80)
            if identity in existing:
                continue
            row = {'id': identity, 'legacy_id': c['id'], 'provider': c.get('provider'), 'route': 'web',
                   'title': bounded_text(c.get('title') or '网页聊天', '名称', 160),
                   'prompt': str(c.get('prompt') or '')[:16000],
                   'created': time.time(), 'pinned': c.get('pinned') is True, 'archived': c.get('archived') is True,
                   'messages': [], 'revision': 1, 'web_url': None}
            if row['provider'] not in WEB_PROVIDERS:
                raise ValueError('旧聊天工作端不支持。')
            self.c.put(db, 'ui_chat', row); existing.add(identity); imported.append(identity)
        ids = {d['id'] for d in self.c.all(db, 'ui_draft')}
        for d in drafts:
            identity = 'legacy-draft-' + bounded_text(d.get('id'), '旧草稿身份', 80)
            if identity not in ids:
                self.draft_save(db, {'id': identity, 'value': {'mode': 'work', 'input': d.get('text', ''),
                                                            'provider': d.get('provider', 'codex')}})
                ids.add(identity)
        return {'imported': imported, 'no_dispatch': True}

    def chat_create(self, db, p):
        return self.chats.chat_create(db, p)

    def chat_record(self, db, p):
        return self.chats.chat_record(db, p)

    def bind_web(self, db, p):
        return self.chats.bind_web(db, p)

    def chat_send(self, db, p):
        return self.chats.chat_send(db, p)

    def transcript(self, db, kind, identity):
        if kind == 'chat':
            c = self.chat_view(db, self.c.get(db, 'ui_chat', identity))
            return {'kind': kind, 'id': identity, 'title': c['title'], 'provider': c['provider'],
                    'messages': c['messages'] or [{'role': 'human', 'content': c.get('prompt', '')}],
                    'source': c}
        if kind == 'dialogue':
            d = self.c.get(db, 'dialogue', identity)
            return {'kind': kind, 'id': identity, 'title': d['title'], 'messages': d['messages'], 'source': d}
        if kind != 'task':
            raise ValueError('记录类型不支持。')
        detail = self.s.detail(identity)
        detail['events'] = self.events(db, identity)
        task = detail['task']; messages = [{'role': 'human', 'content': task['spec']['goal']}]
        for a in detail['attempts']:
            event = next((e for e in detail['events'] if e['kind'] == 'execution_reserved' and e['attempt_id'] == a['id']), {})
            note = event.get('payload', {}).get('human_note')
            if note:
                messages.append({'role': 'human', 'content': note})
            answer = (a['result'] or {}).get('answer') or ''.join(e['payload'].get('text', '') for e in detail['events']
                        if e['attempt_id'] == a['id'] and e['kind'] == 'model_delta')
            messages.append({'role': 'assistant', 'content': answer or '', 'state': a['state']})
        return {'kind': kind, 'id': identity, 'title': task['title'], 'messages': messages, 'source': detail}

    def export(self, kind, identity):
        with self.s.store.read() as db:
            value = self.transcript(db, kind, identity)
            display = next((r for r in self.c.all(db,'display') if r.get('kind')==kind and r.get('record_id')==identity),{})
            value['title'] = display.get('title') or value['title']
        # Export conversation and configuration, not owner tokens, grants,
        # account references, arbitrary event payloads, or runtime requests.
        source = value.pop('source')
        if kind == 'task':
            spec = source['task']['spec']
            value['configuration'] = {k: spec.get(k) for k in ('provider','model','effort','tier','work_scene','work_mode','attachments','budget','writable','checks')}
            value['materials'] = spec['materials']
        elif kind == 'dialogue':
            value['configuration'] = {k: source.get(k) for k in ('config','work_scene','work_roles','files','attachments')}
        else:
            value['configuration'] = {k: source.get(k) for k in ('provider','config','route','context_mode','web_url')}
        value['text'] = '\n\n'.join(m['role'] + ':\n' + m.get('content', '') for m in value['messages'])
        config=json.dumps(value['configuration'],ensure_ascii=False,indent=2)
        materials=json.dumps(value.get('materials',{}),ensure_ascii=False,indent=2)
        value['markdown']='# '+value['title']+'\n\n'+value['text']+'\n\n## 本次配置\n\n```json\n'+config+'\n```\n'
        if value.get('materials'):value['markdown']+='\n## 材料版本\n\n```json\n'+materials+'\n```\n'
        value['markdown']+='\n导出包含已保存的正文与适用配置；不包含认证、授权令牌或运行请求。\n'
        return value

    def fork(self, db, p):
        value = self.transcript(db, p.get('kind'), p.get('id'))
        source = value['source']
        text = '\n\n'.join(m['role'] + ':\n' + m.get('content', '') for m in value['messages'])
        draft = {'mode': 'work', 'input': text, 'source': {'kind': value['kind'], 'id': value['id']},
                 'title': value['title'] + ' · 分叉', 'type': 'independent'}
        if value['kind'] == 'task':
            t = source['task']; spec = t['spec']
            draft.update(project=t['project_id'], files='\n'.join(spec['materials']),
                         attachments=spec.get('attachments', []), scene=spec.get('work_scene','research'),
                         provider=spec['provider'], model=spec['model'],
                         formDraft={'writable': '\n'.join(spec['writable']), 'acceptance': spec['acceptance'],
                                    'checks': json.dumps(spec['checks'], ensure_ascii=False)})
        elif value['kind'] == 'dialogue':
            draft.update(project=source['project_id'], files='\n'.join(source['files']),
                         attachments=source.get('attachments', []), scene=source.get('work_scene','research'),
                         roles=source.get('work_roles', {}), type='cooperative')
        else:
            draft['provider'] = source['provider']
            draft['model'] = (source.get('config') or self.s.profiles.defaults(source['provider']))['model']
        row = self.draft_save(db, {'id': 'fork-' + uid(), 'value': draft})
        return {**row, 'native_history_restored': False, 'requires_new_grant': True}

    def files(self, project_id, folder='', *, db=None):
        if db is None:
            with self.s.store.read() as own:
                p = own.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
        else:p = db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
        if not p:
            raise ValueError('先选择工作区。')
        root = Path(p['root']); target = workspace.plain(root / workspace.relative(folder) if folder else root, root)
        if target.resolve().is_relative_to(self.s.locations.data.resolve()):
            raise ValueError('控制服务数据不能作为工作材料。')
        result = []
        for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.casefold())):
            if item.name in workspace.SKIP_DIRS:
                continue
            name = item.relative_to(root).as_posix()
            try:
                workspace.relative(name); workspace.plain(item, root)
                if not item.is_file() and not item.is_dir():
                    continue
                result.append({'file': name, 'name': item.name, 'directory': item.is_dir(), 'bytes': item.stat().st_size if item.is_file() else None})
            except (OSError, ValueError):
                continue
            if len(result) >= 500:
                break
        return {'files': result, 'folder': folder, 'limited': len(result) == 500, 'no_dispatch': True}

    def preview(self, project_id, name):
        with self.s.store.read() as db:
            p = db.execute('SELECT root FROM projects WHERE id=?', (project_id,)).fetchone()
        if not p:
            raise ValueError('先选择工作区。')
        workspace.select_files(p['root'], [name], self.s.locations.data)
        value=workspace.material_view(p['root'], name)
        if name.lower().endswith(('.png','.jpg','.jpeg','.gif','.webp')):
            raw=workspace.read_blob(p['root'],name)['raw']
            mime='image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if raw.startswith(b'\xff\xd8') else 'image/gif' if raw.startswith((b'GIF87a',b'GIF89a')) else 'image/webp' if raw.startswith(b'RIFF') and raw[8:12]==b'WEBP' else None
            if mime:value['image']='data:'+mime+';base64,'+base64.b64encode(raw).decode('ascii')
        return value

    def files_select(self,db,p):
        if set(p)-{'project_id','files'}:raise ValueError('文件选择只接受工作区与文件清单。')
        project=db.execute('SELECT * FROM projects WHERE id=?',(p.get('project_id'),)).fetchone()
        if not project:raise ValueError('请先选择工作区。')
        self.s.work_records.guard_project(db,project['id'])
        from .attachments import FORMATS,normalize
        names,excluded=workspace.select_files(project['root'],p.get('files'),self.s.locations.data)
        text=[];binary=[];rows=[];total=0
        for name in names:
            record=workspace.read_blob(project['root'],name);raw=record['raw'];total+=record['bytes']
            if total>workspace.MAX_ATTACHMENT_TOTAL:raise ValueError('所选文件合计超过32MB，请减少文件。')
            is_text=Path(name).suffix.lower() not in FORMATS and b'\0' not in raw
            if is_text:
                try:raw.decode('utf8')
                except UnicodeError:is_text=False
            if is_text:
                if record['bytes']>workspace.MAX_FILE:raise ValueError('单份文本材料超过1MB：'+name)
                text.append(name)
            else:binary.append({'file':name,'route':'native'})
            rows.append({'file':name,'bytes':record['bytes'],'sha256':record['sha256'],'original':True})
        native=normalize(binary)
        if not native and total>workspace.MAX_TOTAL:raise ValueError('文本材料合计超过8MB，请减少文件。')
        return {'files':text,'attachments':[{k:i[k] for k in ('file','route')} for i in native],
                'selected':rows,'excluded':excluded,'no_dispatch':True,'original_files_unchanged':True,
                'processing':'SELECTED_OFFICIAL_RUNTIME','controller_extraction':False,'controller_rendering':False}

    def upload(self, db, p):
        project = db.execute('SELECT * FROM projects WHERE id=?', (p.get('project_id'),)).fetchone()
        if not project:
            raise ValueError('先选择工作区。')
        self.s.work_records.guard_project(db,project['id'])
        name = bounded_text(p.get('name'), '文件名', 120)
        if '/' in name or '\\' in name or name != Path(name).name:
            raise ValueError('上传文件名不支持。')
        workspace.relative(name)
        try:
            raw = base64.b64decode(p.get('data', ''), validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('上传内容格式不支持。') from None
        if not raw or len(raw) > workspace.MAX_BINARY:
            raise ValueError('单个上传文件须为1字节至8MB。')
        folder = Path(project['root']) / 'workdesk-inputs' / self.s.command_identity
        if folder.resolve().is_relative_to(self.s.locations.data.resolve()):
            raise ValueError('当前目录用于保存工作记录。请从新对话添加附件，或选择普通项目目录。')
        workspace.plain(folder, Path(project['root']), missing=True)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        if target.exists():
            if target.read_bytes() != raw:
                raise ValueError('已存在不同内容，未覆盖。')
        else:
            target.write_bytes(raw)
        path = target.relative_to(project['root']).as_posix()
        return {'file': path, 'bytes': len(raw), 'no_dispatch': True}

    def schedule_save(self, db, p):
        kind = p.get('kind'); identity = p.get('id')
        value = self.transcript(db, kind, identity)
        due = p.get('due')
        if type(due) not in (float,int) or not math.isfinite(due) or not time.time()+2 <= due <= time.time()+86400:
            raise ValueError('请选择未来24小时内的执行时间。')
        if p.get('confirm') is not True:
            raise ValueError('请确认定时派发及原工作累计预算。')
        source = value['source']
        if kind == 'task':
            task = source['task']
            if task['state'] in ACTIVE | UNKNOWN or not source['attempts']:
                raise ValueError('计划接续需要已结束并核对的工作；未开始的工作请先确认范围。')
            if task['spec'].get('runtime_mode')=='official':
                if not self.s.independent.describe(task['spec']['provider'])['native_resume']:
                    raise ValueError('此工作端的原生会话恢复尚未接通，暂不能计划续聊。')
            elif not task['grant'] or task['grant']['expires_at'] <= due:
                raise ValueError('执行时间超过当前范围授权期限，请先在运行与控制续期。')
            binding = task['spec_hash']+':'+source['attempts'][-1]['id']
        elif kind == 'chat':
            raise ValueError('Chat当前不支持后台定时发送；API聊天请在聊天框中显式发送，官网聊天请在官网操作。')
        else:
            if source['pending']:
                raise ValueError('先结束当前讨论回合。')
            binding = str(source['revision'])
        interval = p.get('interval', 0)
        if type(interval) is not int or interval not in (0, 3600):
            raise ValueError('重复间隔仅支持一次或每小时。')
        row = {'id': 'schedule-' + uid(), 'target_kind': kind, 'target_id': identity, 'binding': binding,
               'title': value['title'], 'content': bounded_text(p.get('content'), '计划消息', 4000),
               'due': due, 'interval': interval, 'max_runs': integer(p.get('max_runs',1),1,10,'最多执行次数'),
               'runs': [], 'state': 'ACTIVE', 'created': time.time(), 'authorized_until': time.time()+86400}
        self.c.put(db, 'ui_schedule', row)
        return {'schedule': row, 'no_dispatch': True, 'service_must_be_running': True}

    def schedule_state(self, db, p):
        row = self.c.get(db, 'ui_schedule', p.get('id'))
        state = p.get('state')
        if state not in {'ACTIVE','PAUSED','CANCELLED'} or row['state'] in {'COMPLETE','CANCELLED'}:
            raise ValueError('计划状态不支持。')
        if state == 'ACTIVE' and (row['due'] <= time.time() or row['authorized_until'] <= time.time()):
            raise ValueError('计划时间或授权已过期，请新建计划，未补发。')
        row.update(state=state, reason='')
        self.c.put(db, 'ui_schedule', row)
        return {'schedule': row, 'no_dispatch': True}

    def schedule_fire(self, db, p):
        row = self.c.get(db, 'ui_schedule', p.get('id'))
        if row['state'] != 'ACTIVE' or row['due'] > time.time():
            return {'no_dispatch': True}
        expected = str(uuid.uuid5(uuid.NAMESPACE_URL, row['id'] + ':' + str(row['due'])))
        if self.s.command_identity != expected:
            raise ValueError('计划派发身份不符。')
        if time.time() > min(row['due']+300, row['authorized_until']):
            raise ValueError('计划已过期，未补发。')
        kind = row['target_kind']; identity = row['target_id']
        source = self.transcript(db, kind, identity)['source']
        if kind == 'task':
            self.s.monitor.guard(db,{'task_id':identity})
            t = source['task']
            if t['spec_hash']+':'+source['attempts'][-1]['id'] != row['binding']:
                raise ValueError('计划工作配置已变化。')
            if t['spec'].get('runtime_mode')=='official':
                from .official_work import send
                last=source['attempts'][-1]
                launch=send(self.s,db,{'task_id':identity,'attempt_id':last['id'],'owner':last['owner'],'goal':row['content']})
            else:
                launch = self.s._resume(db, {'task_id': identity, 'spec_hash': t['spec_hash'], 'mode': 'context', 'note': row['content']})
            row['binding'] = t['spec_hash']+':'+launch['launch_attempt']
        elif kind == 'chat':
            if str(source['revision']) != row['binding']:
                raise ValueError('聊天已变化，计划暂停。')
            launch = self.chat_send(db, {'id': identity, 'revision': source['revision'], 'content': row['content'], 'confirm': True})
            row['binding'] = str(launch['chat']['revision'])
        else:
            if str(source['revision']) != row['binding']:
                raise ValueError('讨论已变化，计划暂停。')
            d = self.c.dialogue_message(db, {'dialogue_id': identity, 'content': row['content']})['dialogue']
            launch = self.c.dialogue_send(db, {'dialogue_id': identity, 'revision': d['revision']})
            # Completion appends one coordinator message and increments revision.
            row['binding'] = str(d['revision']+1)
        row['runs'].append({'at': time.time(), 'task_id': launch.get('task_id'), 'attempt_id': launch.get('launch_attempt')})
        next_due = row['due'] + row['interval']
        if not row['interval'] or len(row['runs']) >= row['max_runs'] or next_due >= row['authorized_until']:
            row['state'] = 'COMPLETE'
        else:
            row['due'] = next_due
        self.c.put(db, 'ui_schedule', row)
        return {**launch, 'schedule': row}

    def tick(self):
        with self.s.lock:
            if self.s.retiring:
                return
            with self.s.store.read() as db:
                rows = self.c.all(db, 'ui_schedule')
            for row in rows:
                if row['state'] != 'ACTIVE' or row['due'] > time.time():
                    continue
                identity = str(uuid.uuid5(uuid.NAMESPACE_URL, row['id'] + ':' + str(row['due'])))
                try:
                    self.s.command('schedule.fire', {'id': row['id']}, identity)
                except (ValueError,OSError,LookupError) as exc:
                    with self.s.store.write() as db:
                        current = self.c.get(db, 'ui_schedule', row['id'])
                        current.update(state='PAUSED', reason=str(exc)[:1000])
                        self.c.put(db, 'ui_schedule', current)
