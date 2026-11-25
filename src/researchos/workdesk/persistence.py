"""Allowlisted state snapshots, same-machine restore and verified project relocation.

Program runtimes and credential homes remain references. Restoring never rolls
back consumption: missing newer usage becomes explicit additive budget debt.
"""
from hashlib import sha256
from contextlib import closing
import json
from pathlib import Path, PurePosixPath
import sqlite3
import re
import uuid
from urllib.parse import quote
import time

from . import workspace
from .paths import atomic_json,atomic_bytes,data_owner_lock
from .store import ACTIVE,UNKNOWN,Store,canonical,digest,uid


MAX_TOTAL=512*1024*1024
DATABASE_FILE_LIMIT=128*1024*1024
ORDINARY_FILE_LIMIT=64*1024*1024


def snapshot_file_limit(name):
    # The root ledger grows with immutable evidence; selected files keep their
    # original limit. The total package remains bounded separately.
    return DATABASE_FILE_LIMIT if name=='tasks.sqlite3' else ORDINARY_FILE_LIMIT
BACKUP_SECONDS=20


def safe_name(name):
    if not isinstance(name,str) or not name or '\\' in name or ':' in name or '\0' in name:raise ValueError('快照路径不合法。')
    path=PurePosixPath(name)
    if path.is_absolute() or any(p in {'','..','.'} for p in name.split('/')):raise ValueError('快照路径越界。')
    return path.as_posix()


def location_fence(base,data,pointer=None):
    """Under the OS owner lock, reconcile a switch intent without replaying work."""
    data=Path(data);marker=data/'retired-target.json'
    if not marker.exists():return
    value=json.loads(workspace.plain(marker,data).read_text(encoding='utf-8'))
    pointer=Path(pointer or Path(base)/'build/workdesk-data-location.json')
    observed=sha256(pointer.read_bytes()).hexdigest() if pointer.exists() else None
    if observed==value['pointer_before_sha256'] and value['state']=='INTENT':
        # No configuration switch occurred; retain the aborted evidence and old data.
        uuid.UUID(value['restore_id']);suffix=''
        if value.get('activation_id'):
            uuid.UUID(value['activation_id']);suffix='-'+value['activation_id']
        destination=data/('aborted-switch-'+value['restore_id']+suffix+'.json')
        if destination.exists():raise ValueError('切换核对记录冲突，保留未知。')
        marker.replace(destination);return
    raise ValueError('此旧台账已转交或切换回执待核对；不重新启动旧执行所有者。')


class Persistence:
    def __init__(self,service):
        self.s=service;self.store=service.store;self.c=service.collaboration
        self.pointer=(service.locations.data/'synthetic-data-location.json') if service.engine else service.locations.data_pointer

    @property
    def restore_root(self):
        return self.s.locations.data.parent/'synthetic-restores' if self.s.engine else self.s.locations.restore_root

    def handlers(self):return {'backup.create':self.backup,'backup.restore':self.restore,
        'backup.activate':self.activate,'project.relocate.prepare':self.relocate_prepare,
        'project.relocate.commit':self.relocate_commit}

    def idle(self,db):
        resources=getattr(self.s,'resources',None)
        if resources and resources.active():raise ValueError('额度查询仍在途，请等待结束再切换凭据、备份或恢复。')
        controls=getattr(self.s,'native_controls',None)
        if controls and controls.active():raise ValueError('原生控制操作在途，先核对回执再切换账号或资料位置。')
        features=getattr(self.s,'ui_features',None)
        if features and features.chats.api.active():raise ValueError('API聊天仍在途，请先停止或等待结束，再切换凭据或保存位置。')
        if any(w['thread'].is_alive() for w in self.s.workers.values()):raise ValueError('仍有专属执行线程，先停止并核对，再快照/恢复/移动。')
        rows=db.execute('SELECT id,state FROM attempts a WHERE state IN ('+','.join('?' for _ in ACTIVE|UNKNOWN)+') AND started=(SELECT MAX(started) FROM attempts WHERE task_id=a.task_id)',tuple(ACTIVE|UNKNOWN)).fetchall()
        if rows or self.store.usage(db=db)['unknown_requests']:raise ValueError('存在在途/未知状态，先对账；不通过恢复替代旧执行。')
        latest=db.execute('SELECT * FROM attempts a WHERE started=(SELECT MAX(started) FROM attempts WHERE task_id=a.task_id)').fetchall()
        if any(not self.s.operations.exit_proved(r) for r in latest):raise ValueError('终态不等于本地退出；已知工具/专属进程未核对，不能恢复/移动/备份。')

    def secret_values(self):
        values={v.reveal_for_provider().encode() for v in self.s.profiles.credentials().values()}
        with self.store.read() as db:references=self.s.resources.references(db)
        for c in references:
            if c['provider']=='codex':continue
            try:credentials=self.s.resources.context(c).credentials()
            except (ValueError,OSError):continue
            values.update(v.reveal_for_provider().encode() for v in credentials.values())
        return list(values)

    def files(self,db):
        names=set()
        for row in db.execute('SELECT * FROM tasks'):
            task=self.store.task_row(row);base='tasks/'+task['id']+'/'
            for name in task['spec']['materials']:names.add(base+'input/'+workspace.relative(name))
            for name in task['spec'].get('integration_base',{}):names.add(base+'integration-base/'+workspace.relative(name))
            work=self.s.locations.data/base/'worktree'
            for name in set(task['spec']['materials'])|set(task['spec']['writable']):
                if workspace.io_path(work/name).exists():names.add(base+'worktree/'+workspace.relative(name))
        for row in db.execute('SELECT * FROM attempts'):
            attempt=self.store.attempt_row(row);result=attempt['result'] or {};base='tasks/'+attempt['task_id']+'/attempts/'+attempt['id']+'/'
            if (self.s.locations.data/base/'result.json').is_file():names.add(base+'result.json')
            for name in result.get('manifest',{}):names.add(base+'files/'+workspace.relative(name))
            for image in result.get('prepared_pdf_images',[]):names.add(base+'prepared-files/'+workspace.relative(image['file']))
        from .library_assets import required_assets
        names.update(required_assets(db,asset_root=self.s.locations.data))
        implementations=set()
        for row in db.execute("SELECT payload FROM events WHERE kind='execution_reserved'"):
            revision=json.loads(row[0]).get('implementation_revision')
            if revision:implementations.add(revision)
        implementations.add(self.s.implementation)
        for revision in implementations:
            if not isinstance(revision,str) or not re.fullmatch('[a-f0-9]{64}',revision):raise ValueError('实现引用不是合法冻结指纹。')
            base='implementations/'+revision+'/';manifest=json.loads(workspace.io_path(self.s.locations.data/base/'manifest.json').read_text(encoding='utf-8'))
            names.add(base+'manifest.json')
            for name,expected in manifest.items():
                path=workspace.io_path(self.s.locations.data/base/safe_name(name))
                if sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('实现冻结代码不匹配，拒绝快照。')
                names.add(base+name)
        for path in (self.s.locations.data/'integration-journals').glob('*.json'):
            names.add(path.relative_to(self.s.locations.data).as_posix())
            journal=json.loads(path.read_text(encoding='utf-8'))
            for name,expected in self.displaced_files(journal).items():
                raw=workspace.io_path(workspace.plain(self.s.locations.data/name,self.s.locations.data)).read_bytes()
                if {'sha256':sha256(raw).hexdigest(),'bytes':len(raw)}!=expected:raise ValueError('整合保留版本证据字节不符，拒绝快照。')
                names.add(name)
        return sorted(names)

    @staticmethod
    def displaced_files(journal):
        result={}
        for entry in journal.get('displaced_versions',[]):
            name=safe_name(entry['saved_relative'])
            if not name.startswith('integration-journals/'+journal['id']+'/'):raise ValueError('整合证据路径越界。')
            result[name]={'sha256':entry['observed_sha256'],'bytes':entry['bytes']}
        return result

    def backup(self,db,p):
        self.idle(db);identity=uid();root=self.s.locations.data/'backups'/identity
        workspace.plain(root,self.s.locations.data,missing=True);root.mkdir(parents=True)
        state=root/'state';state.mkdir();manifest={};total=0;secrets=self.secret_values()
        deadline=time.monotonic()+BACKUP_SECONDS
        def progress(*unused):
            if time.monotonic()>=deadline:raise TimeoutError('本地备份等待/复制超过时限，保留失败记录，未切换数据。')
        try:
            source=sqlite3.connect(self.store.path,timeout=.05);target=sqlite3.connect(state/'tasks.sqlite3')
            try:source.backup(target,pages=256,sleep=.01,progress=progress)
            finally:source.close();target.close()
            names=self.files(db)
            if len(names)>20000:raise ValueError('快照文件数超过有界上限。')
            for name in ['tasks.sqlite3',*names]:
                progress()
                safe_name(name)
                if name=='tasks.sqlite3':raw=(state/name).read_bytes()
                else:
                    source=workspace.plain(self.s.locations.data/name,self.s.locations.data)
                    raw=workspace.io_path(source).read_bytes()
                total+=len(raw)
                if len(raw)>snapshot_file_limit(name) or total>MAX_TOTAL:raise ValueError('快照超过128MiB根数据库、64MiB普通文件或512MiB总量上限。')
                if any(secret in raw for secret in secrets):raise ValueError('快照发现指定供应商凭据，拒绝导出；未输出值。')
                if name!='tasks.sqlite3':
                    destination=workspace.io_path(state/name);destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(raw)
                manifest[name]={'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
            with closing(sqlite3.connect(state/'tasks.sqlite3')) as connection:
                if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('快照数据库完整性检查失败。')
                self.cross_check(connection,manifest,asset_root=state)
            # Detect edits during the copy; this is a verified application
            # snapshot, not an OS-wide atomic filesystem snapshot.
            for name,item in manifest.items():
                if name=='tasks.sqlite3':continue
                progress();source=workspace.io_path(workspace.plain(self.s.locations.data/name,self.s.locations.data))
                if sha256(source.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('备份期间本地文件发生改变；未发布快照。')
            value={'id':identity,'schema':1,'created':time.time(),'store_id':db.execute("SELECT value FROM meta WHERE key='store_id'").fetchone()[0],
                   'files':manifest,'total_bytes':total,'credential_files_included':False,'provider_native_homes_included':False,
                   'dependencies':'existing Python/Node/SDK and same-machine credential references; no runtime installers or auth copied',
                   'snapshot_point':'committed ledger plus per-file hashes; declared input/output versions cross-checked; not an OS-wide atomic snapshot','usage':self.store.usage(db=db)}
            value['manifest_hash']=digest(value);atomic_json(root/'manifest.json',value)
            self.c.put(db,'backup',{'id':identity,'root':str(root),'manifest_hash':value['manifest_hash'],'created':value['created'],'state':'READY'})
            return {'backup_id':identity,'root':str(root),'manifest_hash':value['manifest_hash'],'bytes':total,'state':'READY','no_dispatch':True}
        except Exception as exc:
            # Remove only newly created unpublished files, never a user directory tree.
            for name in ['tasks.sqlite3',*manifest]:
                path=workspace.io_path(state/safe_name(name))
                if path.is_file():workspace.plain(path,workspace.io_path(root)).unlink()
            atomic_json(root/'FAILED.json',{'state':'FAILED','error':type(exc).__name__+': '+str(exc),'no_activation':True});raise

    def cross_check(self,db,manifest,*,asset_root):
        for row in db.execute("SELECT data FROM collaboration WHERE kind='integration'"):
            for name,expected in self.displaced_files(json.loads(row[0])).items():
                if manifest.get(name)!=expected:raise ValueError('快照缺少整合保留版本证据或版本不符。')
        from .library_assets import required_assets
        for name,expected in required_assets(db,asset_root=asset_root).items():
            if name not in manifest or any(manifest[name].get(k)!=v for k,v in expected.items()):raise ValueError('快照缺少成果或能力文件的准确版本。')
        required={}
        for row in db.execute('SELECT id,spec FROM tasks'):
            spec=json.loads(row[1]);prefix='tasks/'+row[0]+'/'
            for name,item in spec['materials'].items():required[prefix+'input/'+name]=item
            for name,item in spec.get('integration_base',{}).items():required[prefix+'integration-base/'+name]=item
        for row in db.execute('SELECT id,task_id,result FROM attempts WHERE result IS NOT NULL'):
            result=json.loads(row[2])
            for name,item in result.get('manifest',{}).items():required['tasks/'+row[1]+'/attempts/'+row[0]+'/files/'+name]=item
            for image in result.get('prepared_pdf_images',[]):
                name=workspace.relative(image['file'])
                required['tasks/'+row[1]+'/attempts/'+row[0]+'/prepared-files/'+name]={k:image[k] for k in ('bytes','sha256')}
        if any(manifest.get(name)!=item for name,item in required.items()):raise ValueError('备份文件与台账记录的准确材料/结果版本不一致，拒绝发布。')

    def validate_package(self,root,expected=None):
        root=Path(root).absolute();workspace.plain(root,root)
        value=json.loads(workspace.io_path(workspace.plain(root/'manifest.json',root)).read_text(encoding='utf-8'))
        identity=value.get('manifest_hash');body={k:v for k,v in value.items() if k!='manifest_hash'}
        if value.get('schema')!=1 or digest(body)!=identity or expected and expected!=identity:raise ValueError('快照格式/清单指纹不匹配。')
        files=value.get('files')
        if not isinstance(files,dict) or not 1<=len(files)<=20000 or 'tasks.sqlite3' not in files:raise ValueError('快照文件清单不支持。')
        total=0;secrets=self.secret_values()
        for name,item in files.items():
            safe_name(name)
            if name!='tasks.sqlite3' and not name.startswith(('tasks/','implementations/','integration-journals/','capabilities/','results/')):raise ValueError('快照文件不在状态允许范围。')
            if any(p.lower() in {'.env','auth.json','credentials.json','backend.json','home'} for p in PurePosixPath(name).parts):raise ValueError('快照包含凭据或运行时home，拒绝导入。')
            path=workspace.io_path(workspace.plain(root/'state'/name,root/'state'))
            if not path.is_file() or path.stat().st_size!=item['bytes'] or item['bytes']>snapshot_file_limit(name):raise ValueError('快照文件大小不符。')
            raw=path.read_bytes();total+=len(raw)
            if total>MAX_TOTAL or sha256(raw).hexdigest()!=item['sha256']:raise ValueError('快照文件哈希/总量不符。')
            if any(secret in raw for secret in secrets):raise ValueError('快照包含指定凭据，拒绝导入。')
        with closing(sqlite3.connect('file:'+quote(str(workspace.io_path(root/'state/tasks.sqlite3')))+'?mode=ro',uri=True)) as source:
            if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('快照数据库损坏。')
            if source.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0]!='1':raise ValueError('不支持的快照数据库版本。')
            if source.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') OR upper(sql) LIKE 'CREATE VIRTUAL TABLE%' LIMIT 1").fetchone():raise ValueError('状态快照不能包含触发器、视图或虚拟表。')
            for table,columns in [('tasks',['id','project_id']),('attempts',['id','task_id']),('projects',['id'])]:
                for row in source.execute('SELECT '+','.join(columns)+' FROM '+table):
                    for identity in row:
                        try:uuid.UUID(identity)
                        except (ValueError,TypeError,AttributeError):raise ValueError('快照含非法任务/目录身份。') from None
            self.cross_check(source,files,asset_root=root/'state')
        for name in files:
            if name.startswith('integration-journals/') and name.endswith('.json'):
                journal=json.loads(workspace.io_path(root/'state'/name).read_text(encoding='utf-8'))
                for evidence,expected in self.displaced_files(journal).items():
                    if files.get(evidence)!=expected:raise ValueError('快照整合日志缺少保留版本字节。')
        return value

    def restore(self,db,p):
        self.idle(db)
        if p.get('backup_id'):
            backup=self.c.get(db,'backup',p['backup_id']);root=Path(backup['root']);expected=backup['manifest_hash']
        else:root=Path(p.get('source',''));expected=p.get('manifest_hash')
        if not expected:raise ValueError('恢复需要明确备份清单指纹。')
        package=self.validate_package(root,expected)
        known=db.execute("SELECT data FROM collaboration WHERE kind='backup' AND json_extract(data,'$.manifest_hash')=?",(expected,)).fetchone()
        if not known or json.loads(known[0])['id']!=package['id']:raise ValueError('只能导入当前台账已登记且指纹匹配的本机备份副本。')
        store_id=db.execute("SELECT value FROM meta WHERE key='store_id'").fetchone()[0]
        if package['store_id']!=store_id:raise ValueError('当前只恢复同一台账身份的阶段快照，不能混入另一套预算历史。')
        restore_root=self.restore_root
        identity=uid();target=restore_root/identity;target.mkdir(parents=True,exist_ok=False)
        for name,item in package['files'].items():
            raw=workspace.io_path(workspace.plain(root/'state'/name,root/'state')).read_bytes()
            if len(raw)!=item['bytes'] or sha256(raw).hexdigest()!=item['sha256']:raise ValueError('快照在复制前发生变化，暂存不激活。')
            path=workspace.io_path(target/name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        restored=Store(target)
        with restored.write() as connection:
            self.c.put(connection,'backup',{'id':package['id'],'root':str(root),'manifest_hash':package['manifest_hash'],'created':package['created'],'state':'READY'})
            from .library_assets import rebind_catalog
            rebind_catalog(connection,target)
            for kind in ('goal_job','scheduled_job','ui_schedule','cooperative_job'):
                for saved in self.c.all(connection,kind):
                    if saved['state'] in {'RUNNING','ACTIVE'}:saved.update(state='PAUSED',reason='从备份恢复，核对后手动继续。',revision=uid());self.c.put(connection,kind,saved)
            self.s.work_flows.pause_active(connection, '从备份恢复，核对责任后再明确启动', 'restore_paused')
            connection.execute('UPDATE tasks SET grant=NULL')
            connection.execute("UPDATE tasks SET state='PAUSED' WHERE state NOT IN ('CANCELLED','HANDED_OFF')")
            for row in connection.execute("SELECT id,data FROM collaboration WHERE kind IN ('plan','parallel')").fetchall():
                value=json.loads(row['data'])
                if 'queue_state' in value:value.update(queue_state='PAUSED',pause_reason='从快照恢复，须显式核对并确认范围。')
                else:value['state']='PAUSED'
                connection.execute('UPDATE collaboration SET data=? WHERE id=?',(canonical(value),row['id']))
            attempts=[r[0] for r in connection.execute('SELECT id FROM attempts')]
            restored.event(None,None,'state_restored_without_dispatch',{'restore_id':identity,'backup_id':package['id'],'grants_revoked':True},connection)
            connection.execute("UPDATE approvals SET state='CANCELLED' WHERE state='PENDING'")
            connection.execute("UPDATE native_requests SET state='CANCELLED' WHERE state IN ('PENDING','READY','REPLY_INTENT')")
            for row in connection.execute("SELECT id,data FROM collaboration WHERE kind IN ('restore','relocation')").fetchall():
                previous=json.loads(row['data']);previous['state']='HISTORICAL_RESTORED'
                connection.execute('UPDATE collaboration SET data=? WHERE id=?',(canonical(previous),row['id']))
            self.c.put(connection,'restored_history',{'id':'restored-history:'+identity,'attempt_ids':attempts,'qwen_native_history':'NOT_IN_BACKUP','credential_reference':'existing same-machine profile'})
        value={'id':identity,'backup_id':package['id'],'target':str(target),'state':'STAGED','files':package['files'],
               'db_sha256':sha256(restored.path.read_bytes()).hexdigest(),'created':time.time(),'source_store_id':store_id,
               'grants_revoked':True,'no_dispatch':True,'activation_requires_current_budget_reconciliation':True}
        self.c.put(db,'restore',value);return {'restore_id':identity,'target':str(target),'state':'STAGED','no_dispatch':True,'grants_revoked':True}

    def budgets(self,db):
        values={('global','all'):self.store.usage(db=db)}
        for row in db.execute('SELECT id,spec FROM tasks'):
            values[('task',row['id'])]=self.store.usage(task=row['id'],db=db)
            campaign=json.loads(row['spec']).get('campaign')
            if campaign:values[('campaign',campaign)]=self.store.usage(campaign=campaign,db=db)
        for row in db.execute('SELECT DISTINCT chain_id FROM chain_members'):values[('chain',row[0])]=self.store.usage(chain=row[0],db=db)
        return values

    def activate(self,db,p):
        self.idle(db);restore=self.c.get(db,'restore',p.get('restore_id'))
        if restore['state']!='STAGED':raise ValueError('恢复阶段已处理，不重复切换。')
        target=Path(restore['target']);workspace.plain(target,target)
        expected_root=self.restore_root
        if target.parent.resolve()!=expected_root.resolve() or target.name!=restore['id']:raise ValueError('恢复目标不属于本应用的独立暂存目录。')
        with data_owner_lock(target):return self.activate_locked(db,p,restore,target)

    def activate_locked(self,db,p,restore,target):
        from .instance import validate_locations
        if not self.s.engine:validate_locations(self.s.locations.base,target,self.s.locations.user)
        if self.s.locations.data==target:raise ValueError('恢复目标已是当前数据目录。')
        preimage=target/'activation-original.sqlite3';journal=target/'activation-recovery.json'
        # Recover any SQLite hot journal before checking the application image.
        with closing(sqlite3.connect((target/'tasks.sqlite3').as_uri()+'?mode=rw',uri=True)) as connection:connection.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchone()
        if journal.exists() and preimage.exists():
            state=json.loads(journal.read_text(encoding='utf-8'))
            if state.get('state')=='TARGET_PREPARED' and state.get('source')==str(self.s.locations.data) and state.get('restore_id')==restore['id']:
                raw=preimage.read_bytes()
                if sha256(raw).hexdigest()!=restore['db_sha256']:raise ValueError('恢复激活前镜像不匹配，保留待核对。')
                atomic_bytes(target/'tasks.sqlite3',raw)
        if sha256(workspace.plain(target/'tasks.sqlite3',target).read_bytes()).hexdigest()!=restore['db_sha256']:raise ValueError('暂存数据库在准备后变化，拒绝切换。')
        for name,item in restore['files'].items():
            if name=='tasks.sqlite3':continue
            path=workspace.io_path(workspace.plain(target/safe_name(name),target))
            if sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('暂存文件已变化，拒绝切换。')
        raw=(target/'tasks.sqlite3').read_bytes()
        if preimage.exists() and sha256(preimage.read_bytes()).hexdigest()!=restore['db_sha256']:raise ValueError('激活前镜像冲突，不覆盖历史。')
        if not preimage.exists():atomic_bytes(preimage,raw)
        activation={'state':'TARGET_PREPARED','source':str(self.s.locations.data),'restore_id':restore['id'],
                    'activation_id':self.s.command_identity,'before_db_sha256':restore['db_sha256'],'no_dispatch':True}
        atomic_json(journal,activation)
        restored=Store(target);debts={}
        result={'restore_id':restore['id'],'state':'ACTIVATED','new_data':str(target),'retire_service':True,
                'budget_not_reset':True,'grants_revoked':True,'no_model_dispatch':True}
        with restored.write() as connection:
            # Bind surviving task identities before computing chain debt. Raw
            # attempts already present in the snapshot must count exactly once.
            for chain in self.c.all(db,'chain'):
                if chain.get('state')=='CLOSED' or not connection.execute('SELECT 1 FROM collaboration WHERE id=?',(chain['id'],)).fetchone():self.c.put(connection,'chain',chain)
            retained_bindings=[]
            for binding in db.execute('SELECT task_id,chain_id FROM chain_members'):
                task_id,chain_id=binding
                if not connection.execute('SELECT 1 FROM tasks WHERE id=?',(task_id,)).fetchone():continue
                previous=connection.execute('SELECT chain_id FROM chain_members WHERE task_id=?',(task_id,)).fetchone()
                if previous and previous[0]!=chain_id:raise ValueError('恢复任务的预算链归属冲突，拒绝切换，不解除当前限制。')
                if not connection.execute("SELECT 1 FROM collaboration WHERE kind='chain' AND id=?",(chain_id,)).fetchone():raise ValueError('当前预算链记录缺失，拒绝恢复解除限制。')
                connection.execute('INSERT OR IGNORE INTO chain_members VALUES(?,?)',(task_id,chain_id))
                retained_bindings.append({'task_id':task_id,'chain_id':chain_id})
            for (scope,identity),usage in self.budgets(db).items():
                args={scope:identity} if scope in {'task','chain','campaign'} else {}
                old=restored.usage(db=connection,**args);existing=connection.execute('SELECT data FROM usage_offsets WHERE scope=? AND id=?',(scope,identity)).fetchone()
                offset=json.loads(existing[0]) if existing else {}
                for key,value in usage.items():
                    if type(value) in (int,float) and key in old and value>old[key]:offset[key]=offset.get(key,0)+value-old[key]
                if offset:
                    connection.execute('INSERT INTO usage_offsets VALUES(?,?,?) ON CONFLICT(scope,id) DO UPDATE SET data=excluded.data',(scope,identity,canonical(offset)))
                    debts[scope+':'+identity]=offset
            for fence in self.c.all(db,'ownership'):self.c.put(connection,'ownership',fence)
            for backup in self.c.all(db,'backup'):self.c.put(connection,'backup',backup)
            for row in db.execute("SELECT key,value FROM meta WHERE key LIKE 'campaign_closed:%'"):
                connection.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',tuple(row))
            restored.event(None,None,'restore_budget_high_water',{'restore_id':restore['id'],'offsets':debts,
                'retained_chain_bindings':retained_bindings,'account_charge':'UNKNOWN'},connection)
            restore.update(state='ACTIVATED',activated_at=time.time(),budget_offsets=debts);self.c.put(connection,'restore',restore)
            # Lost activation replies are reconciled by the same command UUID on the new instance.
            connection.execute('INSERT INTO commands VALUES(?,?,?,?,?)',(self.s.command_identity,'backup.activate',digest(p),canonical(result),time.time()))
        marker=self.s.locations.data/'retired-target.json'
        before=sha256(self.pointer.read_bytes()).hexdigest() if self.pointer.exists() else None
        try:
            atomic_json(marker,{'state':'INTENT','restore_id':restore['id'],'activation_id':self.s.command_identity,'target':str(target),'pointer_before_sha256':before,'at':time.time()})
            atomic_json(self.pointer,{'schema':1,'active_data':str(target),'restore_id':restore['id'],'previous_data':str(self.s.locations.data),'at':time.time()})
        except Exception:
            observed=json.loads(self.pointer.read_text(encoding='utf-8')) if self.pointer.exists() else {}
            if observed.get('active_data')==str(target):
                self.s.retiring=True
                raise ValueError('数据切换已落盘但回执异常；核对新实例，不重放原激活。') from None
            location_fence(self.s.locations.base,self.s.locations.data,self.pointer)
            atomic_bytes(target/'tasks.sqlite3',preimage.read_bytes())
            activation['state']='ROLLED_BACK_NO_SWITCH';atomic_json(journal,activation)
            raise
        self.s.retiring=True
        activation['state']='ACTIVATED';atomic_json(journal,activation)
        self.c.put(db,'restore',restore)
        db.execute("INSERT OR REPLACE INTO meta VALUES('retired_to',?)",(str(target),))
        return result

    def guard_resume(self,db,task,mode):
        if mode!='native' or task['spec']['provider']!='qwen':return
        latest=db.execute('SELECT id FROM attempts WHERE task_id=? ORDER BY started DESC LIMIT 1',(task['id'],)).fetchone()
        if latest and any(latest[0] in h['attempt_ids'] for h in self.c.all(db,'restored_history')):
            raise ValueError('备份未复制含认证风险的Qwen原生home；请核对后明确使用新上下文接续，不伪称原生恢复。')

    def relocation_manifest(self,db,project,root):
        names=set()
        for row in db.execute('SELECT spec FROM tasks WHERE project_id=?',(project,)):
            spec=json.loads(row[0]);names.update(spec.get('integration_base',spec['materials']));names.update(spec['writable'])
        return workspace.current_manifest(root,names),sorted(names)

    def relocate_prepare(self,db,p):
        self.idle(db);project=db.execute('SELECT * FROM projects WHERE id=?',(p.get('project_id'),)).fetchone()
        if not project:raise ValueError('项目不存在。')
        root=Path(project['root']);workspace.plain(root,root)
        manifest,paths=self.relocation_manifest(db,project['id'],root)
        if not paths:raise ValueError('项目没有可用于核对稳定身份的准确材料。')
        value={'id':uid(),'project_id':project['id'],'old_root':str(root),'manifest':manifest,'paths':paths,
               'created':time.time(),'state':'PREPARED','verified_scope':'selected task material/output paths only; other user files not read/copied'}
        value['preview_hash']=digest(value);self.c.put(db,'relocation',value)
        return {'relocation':value,'no_files_moved':True,'no_dispatch':True}

    def relocate_commit(self,db,p):
        self.idle(db);value=self.c.get(db,'relocation',p.get('relocation_id'))
        if value['state']!='PREPARED' or value['preview_hash']!=p.get('preview_hash'):raise ValueError('项目移动预览已变化/处理。')
        project=db.execute('SELECT * FROM projects WHERE id=?',(value['project_id'],)).fetchone()
        if project['root']!=value['old_root']:raise ValueError('项目操作路径已由另一动作更新。')
        root=Path(p.get('new_root','')).absolute();workspace.plain(root,root)
        if not root.is_dir() or workspace.current_manifest(root,value['paths'])!=value['manifest']:raise ValueError('新目录的准确材料/缺失状态不符，不更新项目身份。')
        if db.execute('SELECT 1 FROM projects WHERE root=? AND id!=?',(str(root),project['id'])).fetchone():raise ValueError('目标目录已属于另一项目。')
        db.execute('UPDATE projects SET root=? WHERE id=?',(str(root),project['id']))
        # Location changes never silently reuse a grant approved against a former location.
        db.execute('UPDATE tasks SET grant=NULL WHERE project_id=?',(project['id'],))
        value.update(state='RELOCATED',new_root=str(root),ended_at=time.time());self.c.put(db,'relocation',value)
        self.store.event(None,None,'project_relocated',value,db)
        return {'project_id':project['id'],'root':str(root),'identity_preserved':True,'old_evidence_rewritten':False,'no_files_moved':True,'grants_revoked':True}

    def catalog(self):
        with self.store.read() as db:
            return {'backups':self.c.all(db,'backup'),'restores':self.c.all(db,'restore'),'relocations':self.c.all(db,'relocation'),
              'current_data':str(self.s.locations.data),'program_base':str(self.s.locations.base),'no_dispatch':True,
              'storage':self.s.local_storage.catalog(),
              'format':{'protocol':1,'store':1,'snapshot':1},'credentials':'same-machine references; not included in ordinary snapshots',
              'dependencies':{'python':str(self.s.locations.python),'node':str(self.s.locations.node),'sdk_runtime':str(self.s.locations.runtime),
                              'codex_auth_ref':'existing dedicated subscription profile; do not copy auth.json'}}
