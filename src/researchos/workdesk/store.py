"""Versioned task store and durable idempotent command receipts."""
from contextlib import contextmanager,closing
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
import uuid


TERMINAL = {'COMPLETED','FAILED','FAILED_NO_SEND','INTERRUPTED','STOPPED_RUNTIME','CANCELLED'}
UNKNOWN = {'SEND_UNKNOWN','STOP_UNKNOWN','ORPHANED_UNKNOWN'}
ACTIVE = {'RESERVED','SEND_INTENT','RUNNING','STOP_REQUESTED','WAITING_APPROVAL','WAITING_INPUT'}


def canonical(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value): return sha256(canonical(value).encode()).hexdigest()
def uid(): return str(uuid.uuid4())


class Store:
    def __init__(self, root, *, offline=False):
        self.root = Path(root)
        self.path = self.root/'tasks.sqlite3'; self.lock = threading.RLock()
        from .bridge import manages, binding
        self.managed = manages(self.root)
        self.offline_v2 = bool(offline and binding() and not self.managed)
        if self.offline_v2 and not self.root.resolve().is_relative_to(Path(binding()['data']).resolve().parent):
            raise ValueError('离线恢复库必须保留在本开发实例的资料库目录中。')
        self.root.mkdir(parents=True,exist_ok=True)
        # Additive business objects have their own version. Preserve a consistent
        # pre-migration copy of existing data without rewriting execution rows.
        if self.path.exists() and not self.managed:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as prior:
                has_meta=prior.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone()
                if has_meta:
                    core=dict(prior.execute('SELECT key,value FROM meta'))
                    if (core.get('schema','1') not in ({'1','2'} if self.offline_v2 else {'1'}) or
                            any(core.get(name,'1')!='1' for name in ('collaboration_schema','persistence_schema'))):
                        raise ValueError('不支持的后端数据版本；未覆盖或迁移已有库。')
                version=prior.execute("SELECT value FROM meta WHERE key='work_objects_schema'").fetchone() if has_meta else None
                if version and version[0]!='1':raise ValueError('不支持的工作对象数据版本。')
                if not version:
                    folder=self.root/'migrations';folder.mkdir(exist_ok=True)
                    with closing(sqlite3.connect(folder/('pre-work-objects-'+uid()+'.sqlite3'))) as backup:prior.backup(backup)
        if self.path.exists() and not self.managed:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as prior:
                present=prior.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
                metadata=dict(prior.execute('SELECT key,value FROM meta')) if present else {}
                if (metadata.get('schema','1') not in ({'1','2'} if self.offline_v2 else {'1'}) or
                        metadata.get('collaboration_schema','1')!='1' or metadata.get('persistence_schema','1')!='1'):
                    raise ValueError('不支持的后端数据版本；未覆盖或迁移已有库。')
                if present and 'persistence_schema' not in metadata:
                    folder=self.root/'migrations';folder.mkdir(exist_ok=True)
                    with closing(sqlite3.connect(folder/('pre-persistence-'+uid()+'.sqlite3'))) as backup:prior.backup(backup)
        with self.write() as db:
            schema='''
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,root TEXT NOT NULL UNIQUE,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,title TEXT NOT NULL,spec TEXT NOT NULL,
                spec_hash TEXT NOT NULL,state TEXT NOT NULL,grant TEXT,created REAL NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,request_id TEXT NOT NULL UNIQUE,
                owner TEXT NOT NULL,state TEXT NOT NULL,started REAL NOT NULL,deadline REAL NOT NULL,workspace TEXT NOT NULL,
                session_id TEXT,turn_id TEXT,stop_requested INTEGER NOT NULL DEFAULT 0,pid INTEGER,result TEXT,
                baseline TEXT NOT NULL,reconciliation TEXT,worker_instance TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload_hash TEXT NOT NULL,
                receipt TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,at REAL NOT NULL,task_id TEXT,
                attempt_id TEXT,kind TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tool_calls(id TEXT PRIMARY KEY,attempt_id TEXT NOT NULL,hash TEXT NOT NULL,
                state TEXT NOT NULL,result TEXT);
            CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,attempt_id TEXT NOT NULL,
                provider TEXT NOT NULL,state TEXT NOT NULL,reserved_cny REAL NOT NULL,body_hash TEXT NOT NULL,
                usage TEXT,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,attempt_id TEXT NOT NULL,
                tool_id TEXT NOT NULL,description TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,resolved REAL);
            CREATE TABLE IF NOT EXISTS native_requests(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,attempt_id TEXT NOT NULL,
                rpc_id TEXT NOT NULL,method TEXT NOT NULL,params TEXT NOT NULL,state TEXT NOT NULL,response TEXT,
                created REAL NOT NULL,resolved REAL,UNIQUE(attempt_id,rpc_id));
            CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY,data TEXT NOT NULL,checked REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS collaboration(kind TEXT NOT NULL,id TEXT PRIMARY KEY,data TEXT NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS chain_members(task_id TEXT PRIMARY KEY,chain_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_offsets(scope TEXT NOT NULL,id TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(scope,id));
            CREATE TABLE IF NOT EXISTS work_items(id TEXT PRIMARY KEY,project_id TEXT REFERENCES projects(id),
                title TEXT NOT NULL,goal TEXT NOT NULL,constraints_text TEXT NOT NULL,acceptance TEXT NOT NULL,
                progress TEXT NOT NULL,next_step TEXT NOT NULL,state TEXT NOT NULL,revision INTEGER NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS work_readiness_observations(id TEXT PRIMARY KEY,
                item_id TEXT NOT NULL REFERENCES work_items(id),command_id TEXT NOT NULL UNIQUE,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS work_item_versions(item_id TEXT NOT NULL REFERENCES work_items(id),revision INTEGER NOT NULL,
                snapshot TEXT NOT NULL,PRIMARY KEY(item_id,revision));
            CREATE TABLE IF NOT EXISTS work_item_records(item_id TEXT NOT NULL REFERENCES work_items(id),kind TEXT NOT NULL,
                record_id TEXT NOT NULL,source_revision TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(item_id,kind,record_id));
            CREATE TABLE IF NOT EXISTS record_scopes(kind TEXT NOT NULL,record_id TEXT NOT NULL,
                project_id TEXT REFERENCES projects(id),PRIMARY KEY(kind,record_id));
            CREATE TABLE IF NOT EXISTS resource_space_collections(id TEXT PRIMARY KEY,title TEXT NOT NULL,
                description TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS resource_space_items(id TEXT PRIMARY KEY,
                collection_id TEXT REFERENCES resource_space_collections(id),title TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('page','file','link')),category TEXT NOT NULL,
                note TEXT NOT NULL,tags TEXT NOT NULL,body TEXT,url TEXT,search_text TEXT NOT NULL,
                created REAL NOT NULL,updated REAL NOT NULL,trashed_at REAL,revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS resource_space_files(item_id TEXT PRIMARY KEY REFERENCES resource_space_items(id),
                filename TEXT NOT NULL,mime TEXT NOT NULL,preview TEXT,sha256 TEXT NOT NULL,bytes INTEGER NOT NULL,
                content BLOB NOT NULL,search_text TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS resource_space_item_list ON resource_space_items(collection_id,trashed_at,updated);
            CREATE INDEX IF NOT EXISTS work_item_project ON work_items(project_id,created);
            CREATE INDEX IF NOT EXISTS event_task ON events(task_id,seq);
            CREATE INDEX IF NOT EXISTS attempt_task ON attempts(task_id,started);
            '''
            for statement in schema.split(';'):
                if statement.strip():db.execute(statement)
            prior = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            if prior and prior[0] not in ({'1','2'} if self.managed or self.offline_v2 else {'1'}):
                raise ValueError('不支持的后端数据版本；未覆盖已有库。')
            db.execute("INSERT OR IGNORE INTO meta VALUES('schema',?)",('2' if self.managed else '1',))
            db.execute("INSERT OR IGNORE INTO meta VALUES('collaboration_schema','1')")
            db.execute("INSERT OR IGNORE INTO meta VALUES('persistence_schema','1')")
            db.execute("INSERT OR IGNORE INTO meta VALUES('store_id',?)",(uid(),))
            db.execute("INSERT OR IGNORE INTO meta VALUES('work_objects_schema','1')")

    @contextmanager
    def read(self):
        if self.managed:
            from .bridge.database import Connection
            db = Connection(write=False)
            try: yield db
            finally: db.close()
            return
        db = sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=5)
        db.row_factory = sqlite3.Row; db.execute('PRAGMA query_only=ON'); db.execute('PRAGMA trusted_schema=OFF')
        db.execute('BEGIN')
        try: yield db
        finally: db.close()

    @contextmanager
    def write(self):
        with self.lock:
            if self.managed:
                from .bridge.database import Connection
                db = Connection(write=True)
                try:
                    yield db
                    if db.in_transaction: db.commit()
                except Exception:
                    if db.in_transaction: db.rollback()
                    raise
                finally: db.close()
                return
            db = sqlite3.connect(self.path,timeout=5,isolation_level=None)
            db.row_factory = sqlite3.Row
            try:
                db.execute('PRAGMA foreign_keys=ON'); db.execute('PRAGMA trusted_schema=OFF')
                db.execute('BEGIN IMMEDIATE'); yield db
                if db.in_transaction: db.commit()
            except Exception:
                if db.in_transaction: db.rollback()
                raise
            finally: db.close()

    def event(self, task, attempt, kind, payload, db=None):
        if db is not None:
            return db.execute('INSERT INTO events(at,task_id,attempt_id,kind,payload) VALUES(?,?,?,?,?)',
                              (time.time(),task,attempt,kind,canonical(payload))).lastrowid
        with self.write() as connection: return self.event(task,attempt,kind,payload,connection)

    def command(self, identity, kind, payload, effect):
        try: uuid.UUID(identity)
        except (ValueError,TypeError,AttributeError): raise ValueError('命令必须携带有效UUID。') from None
        fingerprint = digest(payload)
        with self.write() as db:
            prior = db.execute('SELECT * FROM commands WHERE id=?',(identity,)).fetchone()
            if prior:
                if prior['kind'] != kind or prior['payload_hash'] != fingerprint:
                    raise ValueError('同一命令身份对应不同内容，未执行。')
                return json.loads(prior['receipt']),False
            receipt = effect(db)
            db.execute('INSERT INTO commands VALUES(?,?,?,?,?)',(identity,kind,fingerprint,canonical(receipt),time.time()))
            return receipt,True

    @staticmethod
    def task_row(row):
        if row is None: raise LookupError('任务不存在。')
        item = dict(row); item['spec'] = json.loads(item['spec'])
        item['grant'] = json.loads(item['grant']) if item['grant'] else None
        return item

    def attempt_row(self,row):
        if row is None: raise LookupError('尝试不存在。')
        item = dict(row)
        for key in ('baseline','result','reconciliation'):
            item[key] = json.loads(item[key]) if item[key] else None
        expected=str(self.root/'tasks'/item['task_id']/'worktree')
        if item['workspace']!=expected and (item['baseline'] or {}).get('runtime_mode')!='official':
            item['recorded_workspace']=item['workspace'];item['workspace']=expected
            item['workspace_mapping']='CURRENT_DATA_ROOT; recorded history unchanged'
        return item

    def task(self, identity, db=None):
        if db is not None: return self.task_row(db.execute('SELECT * FROM tasks WHERE id=?',(identity,)).fetchone())
        with self.read() as connection: return self.task(identity,connection)

    def attempt(self, identity, db=None):
        if db is not None: return self.attempt_row(db.execute('SELECT * FROM attempts WHERE id=?',(identity,)).fetchone())
        with self.read() as connection: return self.attempt(identity,connection)

    def attempts(self, task):
        with self.read() as db:
            return [self.attempt_row(r) for r in db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started',(task,))]

    def events(self, task, cursor=0, limit=200, db=None):
        if db is None:
            with self.read() as connection:return self.events(task,cursor,limit,connection)
        rows=db.execute('SELECT * FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?',
                        (task,int(cursor),min(500,max(1,int(limit))))).fetchall()
        return [{**dict(r),'payload':json.loads(r['payload'])} for r in rows]

    def update_attempt(self, identity, state=None, db=None, **values):
        allowed = {'session_id','turn_id','stop_requested','pid','result','reconciliation'}
        if values.keys()-allowed: raise ValueError('不支持的尝试更新。')
        if db is None:
            with self.write() as connection:return self.update_attempt(identity,state,db=connection,**values)
        row = self.attempt(identity,db)
        if state is not None: values['state'] = state
        for key in ('result','reconciliation'):
            if key in values: values[key]=canonical(values[key])
        if values:
            db.execute('UPDATE attempts SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),identity))
        if state is not None:
            db.execute('UPDATE tasks SET state=?,updated=? WHERE id=?',(state,time.time(),row['task_id']))
            self.event(row['task_id'],identity,'attempt_state',{'previous':row['state'],'state':state},db)

    def usage(self, task=None, campaign=None, db=None, attempt=None, chain=None):
        if db is None:
            with self.read() as connection: return self.usage(task,campaign,connection,attempt,chain)
        condition, args = (' WHERE task_id=?',[task]) if task else ('',[])
        if attempt:condition,args=' WHERE attempt_id=?',[attempt]
        if campaign:
            condition = ' WHERE task_id IN (SELECT id FROM tasks WHERE json_extract(spec,\'$.campaign\')=?)';args=[campaign]
        members={r[0] for r in db.execute('SELECT task_id FROM chain_members WHERE chain_id=?',(chain,))} if chain else None
        if chain:condition,args=' WHERE task_id IN (SELECT task_id FROM chain_members WHERE chain_id=?)',[chain]
        rows = db.execute('SELECT * FROM requests'+condition,args).fetchall()
        attempts = db.execute('SELECT a.*,t.spec FROM attempts a JOIN tasks t ON t.id=a.task_id').fetchall()
        selected = [a for a in attempts if (not task or a['task_id']==task) and
                    (not campaign or json.loads(a['spec']).get('campaign')==campaign) and (not attempt or a['id']==attempt) and
                    (members is None or a['task_id'] in members)]
        identities={a['id'] for a in selected}
        tool_count=sum(r['attempt_id'] in identities for r in db.execute('SELECT attempt_id FROM tool_calls'))
        usage=[json.loads(r['usage']) if r['usage'] else {} for r in rows]
        token_usage=[u for u in usage if isinstance(u,dict) and type(u.get('prompt_tokens')) is int and type(u.get('completion_tokens')) is int]
        elapsed=sum(max(0,(json.loads(a['result']).get('ended_at',time.time()) if a['result'] else min(time.time(),a['deadline']) if a['deadline'] else time.time())-a['started']) for a in selected)
        value={'model_requests':len(rows),'reserved_cny':sum(r['reserved_cny'] for r in rows),
                'tool_calls':tool_count,'elapsed_observed_seconds':round(elapsed,3),
                'api_known_input_tokens':sum(u['prompt_tokens'] for u in token_usage),
                'api_known_output_tokens':sum(u['completion_tokens'] for u in token_usage),
                'api_requests_without_token_receipt':len(rows)-len(token_usage),
                'codex_token_accounting':'RAW_NOTIFICATIONS_WHEN_AVAILABLE_OTHERWISE_UNKNOWN',
                'attempts':len(selected),'codex_turns':sum(json.loads(a['spec'])['provider']=='codex' for a in selected),
                'unknown_requests':sum(r['state']=='UNKNOWN' for r in rows),
                'account_charge':'UNKNOWN','subscription_remaining':'UNKNOWN'}
        scope,identity=('chain',chain) if chain else ('campaign',campaign) if campaign else ('task',task) if task else ('attempt',attempt) if attempt else ('global','all')
        offset=db.execute('SELECT data FROM usage_offsets WHERE scope=? AND id=?',(scope,identity)).fetchone()
        if offset:
            debt=json.loads(offset[0])
            for key,extra in debt.items():
                if key in value and type(value[key]) in (int,float):value[key]+=extra
            value['restoration_budget_debt']=debt
        return value

    def chain_guard(self, task, db, resource, increment=0):
        member=db.execute('SELECT chain_id FROM chain_members WHERE task_id=?',(task,)).fetchone()
        if not member:return
        row=db.execute("SELECT data FROM collaboration WHERE kind='chain' AND id=?",(member[0],)).fetchone()
        if row is None:raise ValueError('问题链预算记录缺失。')
        chain=json.loads(row[0]);usage=self.usage(chain=member[0],db=db);budget=chain['budget']
        if chain.get('state')=='CLOSED':raise ValueError('问题链真实执行权限已封闭；原授权与预算不重开。')
        pairs={'attempts':('attempts','attempts'),'codex':('codex_turns','codex_turns'),
               'tools':('tool_calls','tools'),'requests':('model_requests','requests'),
               'cny':('reserved_cny','cny'),'seconds':('elapsed_observed_seconds','seconds')}
        used,cap=pairs[resource]
        if usage[used]+increment>budget[cap]:raise ValueError('问题链累计'+resource+'预算不足；执行、诊断、审查和返工共用限额。')

    def reserve_request(self, attempt, body, reserve, limits):
        if not math.isfinite(reserve) or reserve<0: raise ValueError('费用预留无效。')
        with self.write() as db:
            row=self.attempt(attempt,db);task=self.task(row['task_id'],db);budget=task['spec']['budget']
            if row['state'] not in ACTIVE or row['stop_requested'] or time.time()>row['deadline']:
                raise ValueError('任务已停止或超过有效期限。')
            usage=self.usage(task=task['id'],db=db)
            pending=db.execute("SELECT COUNT(*) FROM requests WHERE attempt_id=? AND state='UNKNOWN'",(attempt,)).fetchone()[0]
            if pending:
                raise ValueError('先前模型请求回执未知，先核对，不自动重发。')
            if usage['model_requests']>=budget['requests'] or usage['reserved_cny']+reserve>budget['cny']:
                raise ValueError('任务请求或费用限额已到，未发送。')
            for resource,increment in [('requests',1),('cny',reserve),('seconds',0)]:self.chain_guard(task['id'],db,resource,increment)
            if task['spec'].get('campaign'):
                if db.execute('SELECT 1 FROM meta WHERE key=?',('campaign_closed:'+task['spec']['campaign'],)).fetchone():raise ValueError('本有限实施验证已封闭，不发送模型请求。')
                total=self.usage(campaign=task['spec']['campaign'],db=db)
                if total['model_requests']>=limits['requests'] or total['reserved_cny']+reserve>limits['cny']:
                    raise ValueError('本轮实施累计限额已到，未发送。')
            identity=uid()
            from .network_time import pricing_now
            created=pricing_now() if task['spec']['provider']=='deepseek' else time.time()
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?)',
                       (identity,task['id'],attempt,task['spec']['provider'],'UNKNOWN',reserve,digest(body),None,created))
            self.event(task['id'],attempt,'model_send_intent',{'id':identity,'reserved_cny':reserve,'body_sha256':digest(body)},db)
            return identity

    def request_result(self, identity, state, usage=None):
        with self.write() as db:
            db.execute('UPDATE requests SET state=?,usage=? WHERE id=?',(state,canonical(usage),identity))
