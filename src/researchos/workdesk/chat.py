"""Official website entries and explicitly selected API Chat."""
import json
import time
from .contracts import bounded_text
from .store import ACTIVE, UNKNOWN, canonical, uid
from .workbench import scratch


class ChatService:
    def __init__(self,service):
        self.s=service;self.c=service.collaboration
        from .chat_api import ChatAPI
        self.api=ChatAPI(service)

    def events(self,db,identity):
        return self.s.ui_features.events(db,identity)

    @staticmethod
    def limits(provider):
        return {'turns':12,'context_chars':48000,'message_chars':12000,
                'seconds_per_turn':180,'seconds':2400,'requests':30,'cny':20,
                'requests_per_turn':0 if provider=='codex' else 3,
                'cny_per_turn':0 if provider=='codex' else 8 if provider=='deepseek' else 2}

    def catalog(self):
        names={'codex':'ChatGPT','deepseek':'DeepSeek Chat','qwen':'Qwen Chat'}
        urls={'codex':'https://chatgpt.com/','deepseek':'https://chat.deepseek.com/','qwen':'https://chat.qwen.ai/'}
        greetings={'codex':'今天想聊点什么？','deepseek':'欢迎回来，随时开始吧','qwen':'有什么可以帮你的吗？'}
        colors={'codex':'16,163,127','deepseek':'77,107,254','qwen':'107,80,232'}
        return {'services':[{'provider':key,'name':names[key],'web_url':url,'default_route':'web',
                             'greeting':greetings[key],'placeholder':{'codex':'询问 ChatGPT','deepseek':'给 DeepSeek 发送消息','qwen':'有什么我能帮你的吗？'}[key],
                             'brand_rgb':colors[key],'brand_opacity':0.05,'view_mode':'DESKTOP_OFFICIAL_CHAT',
                             'context_mode':'OFFICIAL_WEBSITE','models_managed_by_website':True,
                             'agent_dispatch':False,'api_billing':False,'free_quota':'MANAGED_BY_OFFICIAL_ACCOUNT'}
                            for key,url in urls.items()], 'api':self.api.describe(),
                'api_services':[self.api.describe(provider) for provider in ('deepseek','qwen')],
                'default_route':'api','no_dispatch':True}

    def chat_view(self, db, row):
        row = json.loads(canonical(row))
        row.setdefault('limits',{**self.limits(row['provider']),'cny_per_turn':0 if row['provider']=='codex' else 2})
        if row.get('route')=='api':
            if not row.get('context'):
                from .chat_context import count,status
                from .chat_api import CONTEXT_WINDOWS
                row['context']=status(count([{'role':m['role'],'content':m['content']} for m in row['messages'] if m.get('state')=='COMPLETED']),
                                      CONTEXT_WINDOWS[row['provider']])
            return row
        row['pending'] = None
        for message in row.get('messages', []):
            if message.get('task_id'):
                task = self.s.store.task(message['task_id'], db)
                attempts = db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started', (task['id'],)).fetchall()
                last = self.s.store.attempt_row(attempts[-1]) if attempts else None
                message['state'] = task['state']
                message['content'] = (last.get('result') or {}).get('answer', '') if last else ''
                if last and not message['content']:
                    message['content'] = ''.join(e['payload'].get('text', '') for e in self.events(db, task['id'])
                                                 if e['attempt_id'] == last['id'] and e['kind'] == 'model_delta')
                message['errors'] = (last.get('result') or {}).get('errors', []) if last else []
                if task['state'] in ACTIVE | UNKNOWN:
                    row['pending'] = task['id']
        return row

    def chat_create(self, db, p):
        provider=p.get('provider','codex')
        if provider not in {'codex','deepseek','qwen'}:raise ValueError('未知聊天工作端。')
        route=p.get('route','web')
        from .chat_api import API_MODELS
        if route not in {'web','api'} or route=='api' and provider not in API_MODELS:raise ValueError('当前仅接入 DeepSeek 和 Qwen API 聊天。')
        if route=='api' and provider=='qwen':self.api.secret(provider)
        row=self.new_record(provider,route,bounded_text(p.get('title') or '新聊天','名称',160))
        self.c.put(db,'ui_chat',row)
        return {'chat':row,'no_dispatch':True,'api_billing':False}

    @staticmethod
    def new_record(provider,route,title):
        from .chat_api import API_MODELS
        return {'id':'chat-'+uid(),'provider':provider,'route':route,
                'config':{'model':API_MODELS[provider][0],'thinking':False} if route=='api' else {},
                'title':title,'prompt':'','created':time.time(),'messages':[],'revision':1,
                'pinned':False,'archived':False,'web_url':None,'pending':None,
                'context_mode':'completed_api_turns' if route=='api' else 'official_website',
                'model_source':'OFFICIAL_API' if route=='api' else 'OFFICIAL_WEBSITE_NOT_READ'}

    def checked(self,db,p):
        row=self.c.get(db,'ui_chat',p.get('id'))
        if type(p.get('revision')) is not int or p['revision']!=row['revision']:
            raise ValueError('聊天已更新，请刷新后重试。')
        return row

    @staticmethod
    def project_scope(db,identity):
        row=db.execute("SELECT project_id FROM record_scopes WHERE kind='chat' AND record_id=?",(identity,)).fetchone()
        return row[0] if row else None

    def move(self,db,p):
        if set(p)!={'id','revision','from_project','project_id'}:
            raise ValueError('聊天项目归属字段无效。')
        row=self.checked(db,p)
        if p['from_project']!=self.project_scope(db,row['id']):
            raise ValueError('聊天项目归属已变化，请刷新后重新选择。')
        project=p['project_id']
        if project is not None:
            if not isinstance(project,str) or not db.execute('SELECT 1 FROM projects WHERE id=?',(project,)).fetchone():
                raise ValueError('目标项目不存在。')
            self.s.work_records.guard_project(db,project)
        if project!=p['from_project']:
            db.execute("INSERT INTO record_scopes VALUES('chat',?,?) ON CONFLICT(kind,record_id) DO UPDATE SET project_id=excluded.project_id",(row['id'],project))
            row['revision']+=1
            self.c.put(db,'ui_chat',row)
        return {'chat_id':row['id'],'project_id':project,'revision':row['revision'],'no_dispatch':True}

    def fork(self,db,p):
        if set(p)!={'id','revision'}:
            raise ValueError('聊天分支字段无效。')
        from .chat_files import metadata
        source=self.chat_view(db,self.checked(db,p))
        if source.get('pending'):
            raise ValueError('请等待当前回复结束或先停止回复，再创建分支。')
        route=source['route']
        child=self.new_record(source['provider'],route,('分支 · '+source['title'])[:160])
        child['forked_from']=source['id']
        child['prompt']=source.get('prompt','')
        if route not in {'api','web'}:
            child.update(context_mode='local_history',model_source=source.get('model_source','SAVED_HISTORY'))
        child['config']={k:source.get('config',{})[k] for k in ('model','thinking') if k in source.get('config',{})}
        attachments={}
        for message in source['messages'] if route!='web' else []:
            snapshot={k:message[k] for k in ('role','content','state','api_text','reasoning','error','sources','search_state') if k in message}
            snapshot['id']=uid()
            if message.get('attachment_ids'):
                files=[]
                for identity in message['attachment_ids']:
                    if identity not in attachments:
                        original=self.c.get(db,'ui_chat_attachment',identity)
                        if original.get('chat_id')!=source['id']:
                            raise ValueError('不能复制其他聊天的附件。')
                        file={**original,'id':'chat-file-'+uid(),'chat_id':child['id'],'created':time.time()}
                        self.c.put(db,'ui_chat_attachment',file)
                        attachments[identity]=metadata(file)
                    files.append(attachments[identity])
                snapshot.update(attachment_ids=[item['id'] for item in files],attachments=files)
            child['messages'].append(snapshot)
        self.c.put(db,'ui_chat',child)
        project=self.project_scope(db,source['id'])
        if project:
            self.s.work_records.guard_project(db,project)
            db.execute("INSERT INTO record_scopes VALUES('chat',?,?)",(child['id'],project))
        return {'chat_id':child['id'],'route':route,'no_dispatch':True}

    def chat_record(self, db, p):
        row = self.c.get(db, 'ui_chat', p.get('id'))
        if 'title' in p:
            row['title'] = bounded_text(p['title'], '名称', 160)
        for k in ('pinned', 'archived'):
            if k in p:
                if type(p[k]) is not bool:
                    raise ValueError('记录设置须为布尔值。')
                row[k] = p[k]
        self.c.put(db, 'ui_chat', row)
        return {'chat': row, 'no_dispatch': True}

    def delete(self,db,p):
        rows=p.get('chats')
        if p.get('confirm') is not True or not isinstance(rows,list) or not 1<=len(rows)<=1000:
            raise ValueError('请选择并确认要删除的聊天。')
        if any(not isinstance(r,dict) or not isinstance(r.get('id'),str) for r in rows):raise ValueError('聊天身份无效。')
        ids={r['id'] for r in rows}
        if len(ids)!=len(rows):raise ValueError('删除清单中有重复聊天。')
        existing={r['id']:r for r in self.c.all(db,'ui_chat')}
        tombstones={r['chat_id'] for r in self.c.all(db,'ui_chat_deleted')}
        for expected in rows:
            row=existing.get(expected['id'])
            if row is None:
                if expected['id'] in tombstones:continue
                raise ValueError('聊天已变化，请刷新列表后再删除。')
            if row['revision']!=expected.get('revision'):raise ValueError('聊天在确认期间已有新消息，请重新选择后删除。')
            if self.chat_view(db,row).get('pending'):raise ValueError('所选聊天仍在回复，请先停止或等待结束后再删除。')
        turns=self.c.all(db,'ui_chat_turn')
        for identity in ids:
            if identity not in existing:continue
            own=[t for t in turns if t['chat_id']==identity]
            # Retain only accounting metadata, not conversation bodies. The
            # tombstone also prevents legacy browser imports resurrecting it.
            self.c.put(db,'ui_chat_deleted',{'id':'deleted:'+identity,'chat_id':identity,'deleted_at':time.time(),
                'usage':[{**{key:t.get(key) for key in ('id','state','usage','created','model_requested_at','provider_requested_at',
                    'search_requested_at','search_state')},'provider':t.get('provider') or existing[identity]['provider'],
                    'model':t['config']['model']} for t in own]})
            for t in own:db.execute("DELETE FROM collaboration WHERE kind='ui_chat_turn' AND id=?",(t['id'],))
            for f in self.c.all(db,'ui_chat_attachment'):
                if f.get('chat_id')==identity:db.execute("DELETE FROM collaboration WHERE kind='ui_chat_attachment' AND id=?",(f['id'],))
            db.execute("DELETE FROM collaboration WHERE kind='ui_draft' AND id=?",('composer:chat:'+identity,))
            db.execute("DELETE FROM collaboration WHERE kind='ui_chat' AND id=?",(identity,))
        # Older receipts include record/draft snapshots. Remove their body
        # copies while keeping command identities and no-replay semantics.
        draft_ids={'composer:chat:'+i for i in ids}
        for command in db.execute("SELECT id,receipt FROM commands WHERE kind IN ('chat.create','chat.record','chat.send','chat.bind-web','chat.api-send','draft.save')").fetchall():
            receipt=json.loads(command['receipt']);changed=False
            if isinstance(receipt.get('chat'),dict) and receipt['chat'].get('id') in ids:
                receipt['chat']={'id':receipt['chat']['id'],'deleted':True};changed=True
            if isinstance(receipt.get('draft'),dict) and receipt['draft'].get('id') in draft_ids:
                receipt['draft']={'id':receipt['draft']['id'],'deleted':True};changed=True
            if changed:db.execute('UPDATE commands SET receipt=? WHERE id=?',(canonical(receipt),command['id']))
        return {'deleted':sorted(ids),'no_dispatch':True,'official_websites_unchanged':True}

    def bind_web(self, db, p):
        from urllib.parse import urlsplit
        row = self.c.get(db, 'ui_chat', p.get('id'))
        if row.get('route')!='web':raise ValueError('仅官网聊天入口可绑定网页会话链接。')
        url = bounded_text(p.get('url'), '官方聊天链接', 2000)
        parsed = urlsplit(url)
        hosts = {'codex': 'chatgpt.com', 'deepseek': 'chat.deepseek.com', 'qwen': 'chat.qwen.ai'}
        if parsed.scheme != 'https' or parsed.netloc != hosts[row['provider']] or parsed.username or parsed.password:
            raise ValueError('须填写所选工作端的 HTTPS 官方聊天链接。')
        if parsed.path in {'', '/'}:
            raise ValueError('请粘贴具体聊天链接，首页不能绑定为已恢复的会话。')
        row.update(web_url=url, web_binding='USER_SUPPLIED_NOT_VERIFIED')
        self.c.put(db, 'ui_chat', row)
        return {'chat': row, 'no_dispatch': True}

    def chat_send(self, db, p):
        row=self.c.get(db,'ui_chat',p.get('id'))
        if row['route']!='web':raise ValueError('这是旧agent文本聊天记录，已保留历史；请改用官方网页，未发送付费请求。')
        if p.get('revision')!=row['revision']:raise ValueError('聊天已更新，请刷新后保存。')
        content=bounded_text(p.get('content'),'开场文字',12000)
        row['prompt']=content;row['title']=content[:60] if row['title']=='新聊天' else row['title']
        row['revision']+=1;self.c.put(db,'ui_chat',row)
        return {'chat':row,'no_dispatch':True,'api_billing':False,'send_on_official_page':True}
