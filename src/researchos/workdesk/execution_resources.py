"""Versioned members and current official connection references; no login or dispatch on read."""
import json
import math
import time

from .contracts import bounded_text
from .store import canonical, digest, uid


PDCA_MODEL_FIELDS = {'provider', 'model', 'effort', 'tier', 'connection_id', 'connection_revision'}


class ExecutionResources:
    def __init__(self,service):
        self.s=service
        self.quota_readers=0
        self.qwen_quota_logins={}
        self.qwen_quota_states={}
        self.qwen_quota_reading=set()
        self.qwen_quota_snapshots={}

    def active(self):
        return self.quota_readers + sum(process.poll() is None for process in self.qwen_quota_logins.copy().values())

    def handlers(self):
        return {'resource.member-save':self.member_save,'resource.quota-save':self.quota_save,
                'resource.connection-save':self.connection_save,'resource.connection-register':self.connection_register,
                'resource.planning-preview':self.planning_preview}

    def planning_preview(self,db,payload):
        from .resource_planning import preview
        return preview(self,db,payload)

    def planning_context(self,db,member_id):
        from .resource_planning import context
        catalog=self.catalog(db)
        member=next(m for m in catalog['members'] if m['id']==member_id)
        connection=next(c for c in catalog['connections'] if c['id']==member['connection_id'])
        quota=next(q for q in catalog['quota_sources'] if q['id']==connection['quota_id'])
        return context(self.s,db,member,connection,quota)

    def rows(self,db,kind):return self.s.collaboration.all(db,'resource_'+kind)

    def references(self,db):
        return [{**r,'id':r['connection_id']} for r in self.rows(db,'connection_reference')]

    def remember(self,db,value):
        self.s.collaboration.put(db,'resource_connection_reference',{
            **value,'id':'connection-reference:'+value['id'],'connection_id':value['id']})

    def saved(self,db,kind,identity):return self.s.collaboration.get(db,'resource_'+kind,identity)

    def save(self,db,kind,value):
        self.s.collaboration.put(db,'resource_'+kind,value)
        self.s.store.event(None,None,'resource_'+kind+'_saved',{'id':value['id'],'revision':value['revision'],'no_dispatch':True},db)
        return {kind:value,'no_dispatch':True}

    def edit(self,db,kind,p):
        if not p.get('id'):
            if p.get('revision') is not None:raise ValueError('新资源不接收旧修订。')
            return {'id':'resource-'+kind+':'+uid(),'revision':1}
        old=self.saved(db,kind,p['id'])
        if type(p.get('revision')) is not int or p['revision']!=old['revision']:raise ValueError('资源已变化，请刷新后修改。')
        return {'id':old['id'],'revision':old['revision']+1}

    def connections(self,db,profiles):
        result=[]
        try:
            revision=self.s.locations.credential_revision
            refs=self.s.locations.credential_references()
        except (ValueError,OSError):revision=None;refs=None
        try:credentials=self.s.profiles.credentials()
        except (ValueError,OSError):credentials={}
        overrides={v['id']:v for v in self.rows(db,'connection')}
        for profile in profiles:
            provider=profile['id'];reference=None;ready=False;version=None
            try:
                if provider=='codex':
                    reference=str(self.s.locations.codex_home)
                    ready=(self.s.locations.codex_home/'auth.json').is_file()
                else:
                    reference=str(self.s.locations.api_env) if self.s.locations.api_env else None
                    version=(refs or {}).get('api_env_sha256')
                    # Legacy explicit references also freeze the exact source file.
                    if reference and version is None and provider in credentials:
                        from hashlib import sha256
                        with self.s.locations.api_env.open('rb') as stream:raw=stream.read(65537)
                        if len(raw)>65536:raise ValueError('认证文件超出支持范围。')
                        version=sha256(raw).hexdigest()
                    secret=credentials.get(provider)
                    ready=bool(secret) and (provider!='qwen' or secret.billing_kind=='general_api')
            except (ValueError,OSError):ready=False
            identity='connection:'+digest({'provider':provider,'reference':reference})[:32]
            metadata=overrides.get(identity,{})
            config={'credential_revision':revision,'file_revision':version,'reference':reference}
            result.append({'id':identity,'provider':provider,'endpoint_id':provider,
                'name':metadata.get('name',profile['name']+' 当前认证引用'),
                'revision':digest({'config':config,'metadata':metadata}),
                'settings_revision':metadata.get('revision',0),'credential_reference':config,
                'quota_id':metadata.get('quota_id') or 'quota:'+identity,
                'enabled':metadata.get('enabled',True),
                'available':ready and profile['runtime_present'] and metadata.get('enabled',True),
                'status':'LOCAL_REFERENCE_READY' if ready else 'REFERENCE_UNAVAILABLE',
                'account_identity':'CHECKED_AT_RUNTIME' if provider=='codex' else 'KEY_REFERENCE_ONLY',
                'authentication_validity':'NOT_CHECKED','parallel_routing':'ONE_ACTIVE_REFERENCE_PER_PROVIDER'})
        # Keep explicitly registered references and references pinned by a prior
        # execution discoverable even after the default reference changes.
        for registered in self.references(db):
            if any(c['id']==registered['id'] for c in result):continue
            provider=registered['provider'];metadata=overrides.get(registered['id'],{})
            profile=next(p for p in profiles if p['id']==provider)
            value={**registered,'name':metadata.get('name',registered['name']),
                   'quota_id':metadata.get('quota_id') or 'quota:'+registered['id'],
                   'enabled':metadata.get('enabled',True),'settings_revision':metadata.get('revision',0)}
            try:self.context(value).validate();ready=True
            except (ValueError,OSError):ready=False
            value.update(revision=digest({'config':value['credential_reference'],'metadata':metadata}),
                available=ready and profile['runtime_present'] and value['enabled'],
                status='LOCAL_REFERENCE_READY' if ready else 'REFERENCE_UNAVAILABLE')
            result.append(value)
        defaults={c['provider']:c['id'] for c in result[:len(profiles)]}
        for c in result:
            c['is_default']=defaults.get(c['provider'])==c['id']
            c['parallel_routing']='EXPLICIT_OFFICIAL_CONNECTION'
            c['supported_routes']=['official_independent']
        from .partner_connections import projection
        result.extend(projection(db)['connections'])
        return result

    def catalog(self,db=None,profiles=None,*,pdca_members=True):
        from .native_permissions import catalog as access_catalog
        if db is None:
            with self.s.store.read() as own:return self.catalog(own,profiles,pdca_members=pdca_members)
        profiles=self.s.profiles.list(db) if profiles is None else profiles
        connections=self.connections(db,profiles);quotas={q['id']:q for q in self.rows(db,'quota')}
        for c in connections:
            if c['quota_id'] not in quotas:
                quotas[c['quota_id']]={'id':c['quota_id'],'revision':0,'name':c['name']+' 额度来源',
                    'kind':'subscription' if c['provider']=='codex' else 'cash','remaining':None,
                    'unit':'UNKNOWN','source':'UNKNOWN','observed_at':None}
        for q in quotas.values():q['official_observations']=[]
        for c in connections:
            observation=next((o for o in self.rows(db,'quota_observation') if o['connection_id']==c['id']),None)
            if observation:
                quotas[c['quota_id']]['official_observations'].append({**observation,
                    'binding_status':'MATCH' if observation['connection_revision']==c['revision'] else 'REFERENCE_CHANGED'})
        members=self.rows(db,'member')
        from .partner_connections import model_member
        members.extend(model_member(c) for c in connections if c.get('kind') == 'partner_connection')
        if pdca_members:
            members.extend(m for m in self.rows(db,'pdca_member') if pdca_members is True or m['id'] in pdca_members)
        for m in members:
            c=next((c for c in connections if c['id']==m['connection_id']),None)
            m['available']=bool(m['enabled'] and c and c['available'])
            if m.get('managed') == 'pdca_model':
                m['available'] = m['available'] and c['revision'] == m['connection_revision']
        endpoints=[{'id':p['id'],'name':p['harness'],'execution_route':p['id']+'_native',
                    'runtime_present':p['runtime_present'],'resume':p['resume'],'interrupt':p['interrupt'],
                    'access_modes':access_catalog(p['id']),'model_catalog':p['model_catalog']} for p in profiles]
        from .partner_connections import projection
        view=projection(db)
        for partner in view['partners']:
            if any(endpoint['id']==partner['id'] for endpoint in endpoints):
                continue
            own=[c for c in view['connections'] if c['provider']==partner['id']]
            models={c['model']:{'model':c['model'],'display_name':c['model_label'],'runnable':c['available'],
                               'efforts':['default'],'selection_efforts':['default'],'tiers':['standard']} for c in own}
            endpoints.append({'id':partner['id'],'name':partner['name']+' 官方工作端','execution_route':'partner_native',
                              'runtime_present':any(c['available'] for c in own),'resume':False,
                              'interrupt':'EXACT_PARTNER_RUN','access_modes':access_catalog(partner['id']),
                              'model_catalog':{'models':list(models.values())}})
        return {'members':members,'connections':connections,'quota_sources':list(quotas.values()),
            'endpoints':endpoints,
            'quota_policy':'INFORMATION_ONLY; UNKNOWN_IS_NOT_ZERO; SHARED_SOURCE_LISTED_ONCE',
            'routing':'EXPLICIT_OFFICIAL_CONNECTIONS; LEGACY_SCOPED_SELECTION_UNSUPPORTED','no_dispatch':True}

    def public_catalog(self, db=None, profiles=None, *, item_id=None):
        if db is None:
            with self.s.store.read() as own:
                return self.public_catalog(own, profiles, item_id=item_id)
        selected = set()
        if item_id is not None:
            self.s.work_items.item(db, item_id)
            self.s.work_records.guard(db, 'item', item_id)
            plan = self.s.teamwork.current(db, item_id)
            if plan:
                from .work_readiness_observations import selections
                selected.update(member['reference']['member_id'] for _, member in selections(plan))
            else:
                for preparation in (self.s.fixed_preparation, self.s.leader_preparation, self.s.host_preparation):
                    records = preparation.records(db, item_id)
                    if records:
                        selected.update(records[-1]['members'])
        return self.catalog(db, profiles, pdca_members=selected)

    def pdca_model(self, db, ref):
        """Keep this exact model choice available to the existing member-based flow."""
        if (not isinstance(ref, dict) or set(ref) != PDCA_MODEL_FIELDS
                or any(not isinstance(value, str) or not 1 <= len(value) <= 200 for value in ref.values())):
            raise ValueError('请选择有效的工作端、模型、运行档位和当前连接。')
        provider, model = ref['provider'], ref['model']
        from .partner_connections import selected
        if any(c['id'] == ref['connection_id'] and c.get('kind') == 'partner_connection' for c in self.connections(db, self.s.profiles.list(db))):
            connection = selected(db, ref['connection_id'], ref['connection_revision'])
            if (connection['provider'] != provider or connection['model'] != model
                    or ref['effort'] != 'default' or ref['tier'] != 'standard'):
                raise ValueError('合作模型与准确伙伴连接不符，请重新选择。')
            return self.selection(db, 'partner-member:' + connection['id'])['reference']
        profile = self.s.profiles.validate(provider, model, ref['effort'], ref['tier'])
        entry = next((row for row in profile['model_catalog']['models'] if row['model'] == model), None)
        if not entry or not entry['runnable']:
            raise ValueError('所选模型不在当前可执行的官方目录内。')
        connection = self.default_connection(db, provider)
        if (not connection['available'] or connection['id'] != ref['connection_id']
                or connection['revision'] != ref['connection_revision']):
            raise ValueError('当前工作端连接已变化或不可用，请刷新后重新选择模型。')
        self.check_connection(connection, db)
        identity = 'pdca-model:' + digest(ref)
        previous = db.execute("SELECT 1 FROM collaboration WHERE id=? AND kind='resource_pdca_member'",
                              (identity,)).fetchone()
        if not previous:
            native = entry.get('native_metadata') or {}
            display = next((name.strip() for name in (entry.get('display_name'), native.get('name'),
                           entry.get('model_version')) if isinstance(name, str) and name.strip()), model)
            self.save(db, 'pdca_member', {
                **ref, 'id': identity, 'revision': 1, 'name': display[:160], 'role': 'PDCA',
                'enabled': True, 'managed': 'pdca_model',
            })
        return self.selection(db, identity)['reference']

    def quota_check(self,p):
        if set(p)-{'connection_id','connection_revision'}:raise ValueError('额度查询字段不支持。')
        with self.s.lock:
            if self.s.retiring:raise ValueError('服务正在更新，请刷新连接。')
            with self.s.store.read() as db:
                c=next((c for c in self.connections(db,self.s.profiles.list()) if c['id']==p.get('connection_id')),None)
                previous=next((o for o in self.rows(db,'quota_observation') if o['connection_id']==p.get('connection_id')),None)
            if not c or c['revision']!=p.get('connection_revision'):
                raise ValueError('额度连接已变化或不可用，请刷新后查询。')
            if c['provider'] not in {'codex','deepseek'}:
                raise ValueError('该工作端尚未接通官方剩余额度查询。')
            if not self.quota_readable(c):
                raise ValueError('额度连接已变化或不可用，请刷新后查询。')
            self.quota_readers+=1
        try:return self._quota_read(c,previous)
        finally:
            with self.s.lock:self.quota_readers-=1

    @staticmethod
    def quota_readable(connection):
        return (connection['provider']=='codex' and connection['available'] or
                connection['provider']=='deepseek' and connection['enabled'] and
                connection['status']=='LOCAL_REFERENCE_READY')

    def _qwen_login_state(self, source):
        process = self.qwen_quota_logins.get(source)
        if process is not None:
            code = process.poll()
            if code is None:
                return 'PENDING'
            del self.qwen_quota_logins[source]
            self.qwen_quota_states[source] = 'VERIFY_AUTH' if code == 0 else 'FAILED'
        if source in self.qwen_quota_reading:
            return 'CHECKING'
        return self.qwen_quota_states.get(source)

    def qwen_quota_status(self):
        from .quota_observations import qwen_console_configured

        states = {}
        for source in ('free', 'cash'):
            with self.s.lock:
                state = self._qwen_login_state(source)
            if state is None or state == 'VERIFY_AUTH':
                configured = qwen_console_configured(self.s.locations, source)
                fallback = {True: 'CONFIGURED', False: 'READY', None: 'UNKNOWN'}[configured]
                with self.s.lock:
                    current = self._qwen_login_state(source)
                    if current == 'VERIFY_AUTH' and current == state:
                        current = {True: 'CONNECTED', False: 'FAILED', None: 'UNKNOWN'}[configured]
                        self.qwen_quota_states[source] = current
                    state = 'PENDING' if current == 'VERIFY_AUTH' else current or fallback
            states[source] = state
        return states

    def _qwen_quota_source(self,payload):
        if set(payload)!={'source'} or payload.get('source') not in ('free','cash'):
            raise ValueError('请选择百炼免费额度或阿里云账户余额。')
        source=payload['source']
        if self.s.retiring:raise ValueError('服务正在更新，请刷新。')
        if self._qwen_login_state(source) == 'PENDING':
            raise ValueError('请先完成或关闭当前官方授权窗口。')
        if source in self.qwen_quota_reading:raise ValueError('此项额度正在查询，请稍候。')
        return source

    def qwen_quota_login(self,payload):
        from .quota_observations import open_qwen_console_login

        with self.s.lock:
            source=self._qwen_quota_source(payload)
            self.qwen_quota_logins[source]=open_qwen_console_login(self.s.locations,source)
            self.qwen_quota_snapshots.pop(source,None)
        return {'state':'OPENED','no_dispatch':True}

    def qwen_quota_cancel(self, payload):
        if set(payload) != {'source'} or payload['source'] not in ('free', 'cash'):
            raise ValueError('请选择要取消的授权。')
        with self.s.lock:
            source = payload['source']
            if self._qwen_login_state(source) == 'PENDING':
                process = self.qwen_quota_logins[source]
                try:
                    process.terminate()
                except OSError:
                    if process.poll() is None:
                        raise ValueError('暂时无法取消授权，请稍后重试。') from None
        return {'no_dispatch': True}

    def qwen_quota_check(self,payload):
        from .quota_observations import read_qwen_console

        with self.s.lock:
            source=self._qwen_quota_source(payload)
            self.qwen_quota_reading.add(source)
            self.quota_readers+=1
        try:
            snapshot=read_qwen_console(self.s.locations,source)
            with self.s.lock:
                self.qwen_quota_snapshots[source]=snapshot
                self.qwen_quota_states[source]='CONNECTED'
            return snapshot
        except ValueError:
            with self.s.lock:self.qwen_quota_states[source]='CHECK_FAILED'
            raise
        finally:
            with self.s.lock:
                self.qwen_quota_reading.remove(source)
                self.quota_readers-=1

    def _quota_read(self,c,previous):
        from .quota_observations import read_codex, read_deepseek
        try:
            context=self.context(c).validate()
            data=read_codex(context) if c['provider']=='codex' else read_deepseek(context.credentials()['deepseek'])
            snapshot={**data,'observed_at':time.time()}
            status='OBSERVED';error_class=None
        except Exception as exc:
            snapshot=None;status='FAILED';error_class=type(exc).__name__
        with self.s.lock:
            if self.s.retiring:raise ValueError('服务正在更新，未保存额度查询。')
            with self.s.store.write() as db:
                current=next((v for v in self.connections(db,self.s.profiles.list()) if v['id']==c['id']),None)
                old=next((o for o in self.rows(db,'quota_observation') if o['connection_id']==c['id']),None)
                if not current or current['revision']!=c['revision']:raise ValueError('查询期间连接变化，未保存额度。')
                if (old or {}).get('revision')!=(previous or {}).get('revision'):raise ValueError('已有更新的额度查询，请刷新显示。')
                # A prior success is usable only with its own original binding.
                last=snapshot or (old or {}).get('last_success')
                last_revision=c['revision'] if snapshot else (old or {}).get('last_success_connection_revision')
                value={'id':'quota-observation:'+c['id'],'connection_id':c['id'],
                    'revision':(old or {}).get('revision',0)+1,'connection_revision':c['revision'],
                    'status':status,'checked_at':time.time(),'last_success':last,
                    'last_success_connection_revision':last_revision,'error_class':error_class,'no_dispatch':True}
                self.save(db,'quota_observation',value)
        return {'observation':value,'no_dispatch':True,'real_model_requests':0}

    def member_save(self,db,p):
        if set(p)-{'id','revision','name','role','provider','model','effort','tier','connection_id','enabled'}:raise ValueError('成员字段不支持。')
        base=self.edit(db,'member',p)
        provider=p.get('provider')
        self.s.profiles.validate(provider,p.get('model'),p.get('effort'),p.get('tier'))
        c=next((c for c in self.connections(db,self.s.profiles.list()) if c['id']==p.get('connection_id')),None)
        if not c or c['provider']!=provider:raise ValueError('成员与连接工作端不一致。')
        if type(p.get('enabled',True)) is not bool:raise ValueError('成员启用状态无效。')
        value={**base,**{k:p[k] for k in ('provider','model','effort','tier','connection_id')},
            'name':bounded_text(p.get('name'),'成员名称',160),
            'role':bounded_text(p.get('role','执行'),'预职责',500),'enabled':p.get('enabled',True)}
        return self.save(db,'member',value)

    def context(self,connection):
        if connection.get('kind') == 'partner_connection':
            from .partner_connections import PartnerConnectionContext
            return PartnerConnectionContext(self.s.locations, connection)
        from .connection_context import context
        return context(self.s.locations,connection)

    def connection_register(self,db,p):
        if set(p)-{'provider','reference','name'}:raise ValueError('登记只接受工作端、已有本地引用和名称。')
        provider=p.get('provider')
        if provider not in {'codex','deepseek','qwen'}:raise ValueError('未知工作端。')
        name=bounded_text(p.get('name'),'连接名称',160)
        if provider=='codex':
            reference=str(self.s.locations.validate_codex_home(p.get('reference')));version=None
        else:
            from .paths import local_credential_path
            from hashlib import sha256
            path=local_credential_path(p.get('reference'));reference=str(path);version=sha256(path.read_bytes()).hexdigest()
        identity='connection:'+digest({'provider':provider,'reference':reference})[:32]
        if any(c['id']==identity for c in self.connections(db,self.s.profiles.list())):raise ValueError('该本地引用已登记，请使用现有连接。')
        value={'id':identity,'provider':provider,'endpoint_id':provider,'name':name,
            'credential_reference':{'credential_revision':'registered:'+uid(),'file_revision':version,'reference':reference},
            'account_identity':'CHECKED_AT_RUNTIME' if provider=='codex' else 'KEY_REFERENCE_ONLY',
            'authentication_validity':'NOT_CHECKED'}
        self.context(value).validate()
        self.remember(db,value)
        self.s.store.event(None,None,'resource_connection_registered',{'id':identity,'provider':provider,'no_dispatch':True},db)
        return {'connection':next(c for c in self.connections(db,self.s.profiles.list()) if c['id']==identity),'no_dispatch':True}

    def quota_save(self,db,p):
        if set(p)-{'id','revision','name','kind','remaining','unit'}:raise ValueError('额度字段不支持。')
        base=self.edit(db,'quota',p)
        if p.get('kind') not in {'cash','subscription','local'}:raise ValueError('额度来源类型无效。')
        amount=p.get('remaining')
        if amount is not None and (type(amount) not in (int,float) or not math.isfinite(amount) or amount<0):raise ValueError('剩余数量应为非负数或未知。')
        return self.save(db,'quota',{**base,'name':bounded_text(p.get('name'),'额度来源名称',160),
            'kind':p['kind'],'remaining':amount,'unit':bounded_text(p.get('unit','UNKNOWN'),'单位',40),
            'source':'MANUAL','observed_at':time.time(),'enforcement':'INFORMATION_ONLY'})

    def connection_save(self,db,p):
        if set(p)-{'id','revision','name','quota_id','enabled'}:raise ValueError('连接字段不支持。')
        c=next((c for c in self.connections(db,self.s.profiles.list()) if c['id']==p.get('id')),None)
        if not c or p.get('revision')!=c['revision']:raise ValueError('连接引用已变化，请刷新。')
        if p.get('quota_id')!=('quota:'+c['id']):self.saved(db,'quota',p.get('quota_id'))
        if type(p.get('enabled',True)) is not bool:raise ValueError('连接启用状态无效。')
        return self.save(db,'connection',{'id':c['id'],'revision':c['settings_revision']+1,
            'name':bounded_text(p.get('name'),'连接名称',160),'quota_id':p['quota_id'],'enabled':p.get('enabled',True)})

    def selection(self,db,member_id):
        data=self.catalog(db);m=next((m for m in data['members'] if m['id']==member_id),None)
        if not m or not m['available']:raise ValueError('成员或其当前连接不可用，请重新选择。')
        if m.get('kind') != 'partner_connection':
            self.s.profiles.validate(m['provider'],m['model'],m['effort'],m['tier'])
        c=next(c for c in data['connections'] if c['id']==m['connection_id'])
        q=next(q for q in data['quota_sources'] if q['id']==c['quota_id'])
        return {'reference':{'member_id':m['id'],'member_revision':m['revision'],'connection_id':c['id'],
                    'connection_revision':c['revision'],'quota_id':q['id'],'quota_revision':q['revision']},
            'member':m,'connection':c,'quota_source':q,'selected_at':time.time(),
            'quota_enforcement':'INFORMATION_ONLY','actual_account_binding':'RUNTIME_EVIDENCE',
            'selection_basis':'EXPLICIT_MEMBER_AND_CONNECTION; QUOTA_OBSERVATIONS_ARE_TIME_SNAPSHOTS; NO_COST_OPTIMIZATION'}

    def before(self,db,kind,payload):
        p=dict(payload);ref=p.pop('resource_selection',None)
        if '_connection_binding' in p:raise ValueError('连接上下文由服务冻结，不能手动改写。')
        selection=None
        if ref is not None:
            if kind!='independent.send' or p.get('task_id'):raise ValueError('当前成员选择用于新的独立Work，请从任务页准备。')
            if not isinstance(ref,dict):raise ValueError('成员选择引用无效。')
            selection=self.selection(db,ref.get('member_id'))
            if ref!=selection['reference']:raise ValueError('成员、连接或额度来源已变化，请重新准备草稿。')
            for key in ('provider','model','effort','tier'):
                if p.get(key)!=selection['member'][key]:raise ValueError('草稿模型配置与所选成员不一致，请重新准备。')
        if kind=='independent.send':
            if p.get('task_id') and p.get('connection_id'):
                from .partner_connections import selected
                if not p.get('connection_revision'):raise ValueError('请选择有准确版本的模型连接。')
                c=selected(db,p.pop('connection_id'),p.pop('connection_revision'))
            elif p.get('task_id'):
                c=self.task_connection(self.s.store.task(p['task_id'],db),db)
            elif p.get('connection_id'):
                from .partner_connections import selected
                c=selected(db,p.pop('connection_id'),p.pop('connection_revision',None))
            else:
                c=selection['connection'] if selection else self.default_connection(db,p.get('provider','codex'))
            self.check_connection(c,db)
            if c.get('kind') == 'partner_connection':
                if p.get('provider', c['provider']) != c['provider'] or p.get('model', c['model']) != c['model']:
                    raise ValueError('当前工作模型与所选伙伴连接不符。')
                p.update(provider=c['provider'],model=c['model'],effort='default',tier='standard',_connection_binding=c)
                return p,selection
            p['_connection_binding']={k:c[k] for k in ('id','provider','credential_reference')}
            # Retain the reference when a later global default points elsewhere.
            if not any(v['id']==c['id'] for v in self.references(db)):
                self.remember(db,{
                    **p['_connection_binding'],'endpoint_id':c['provider'],'name':c.get('name',c['provider']+' 原连接'),
                    'account_identity':c.get('account_identity','CHECKED_AT_RUNTIME'),'authentication_validity':'NOT_CHECKED'})
        return p,selection

    def complete(self,db,receipt,selection):
        launches=receipt.get('launch_attempts',[]) or ([receipt['launch_attempt']] if receipt.get('launch_attempt') else [])
        for identity in launches:
            attempt=self.s.store.attempt(identity,db);task=self.s.store.task(attempt['task_id'],db)
            if task['spec'].get('runtime_mode')=='official' or task['spec'].get('interaction')=='chat':continue
            # Legacy scoped routes retain their global grant/reference checks
            # and cannot bypass a disabled default via a non-member dispatch.
            current=self.default_connection(db,task['spec']['provider'])
            if not current['enabled']:raise ValueError('当前工作连接已停用，未启动执行。')
        if selection is None:return receipt
        identity=receipt.get('launch_attempt')
        if not identity:raise ValueError('成员派发没有准确执行身份。')
        attempt=self.s.store.attempt(identity,db);task=self.s.store.task(attempt['task_id'],db)
        spec={**task['spec'],'execution_resource':selection}
        db.execute('UPDATE tasks SET spec=?,spec_hash=? WHERE id=?',(canonical(spec),digest(spec),task['id']))
        self.s.store.event(task['id'],identity,'execution_resource_frozen',selection,db)
        return {**receipt,'spec_hash':digest(spec),'resource_selection':selection['reference']}

    def default_connection(self,db,provider):
        return next(c for c in self.connections(db,self.s.profiles.list()) if c['provider']==provider and c['is_default'])

    def task_connection(self,task,db):
        spec=task['spec'];bound=spec.get('partner_connection') or spec.get('execution_connection') or (spec.get('execution_resource') or {}).get('connection')
        if bound:
            if bound['provider']!=spec['provider']:raise ValueError('任务与原连接工作端不一致。')
            return bound
        # Older official records have no frozen connection: require their last
        # reservation to prove that the current global reference is still exact.
        row=db.execute("SELECT payload FROM events WHERE task_id=? AND kind='execution_reserved' ORDER BY seq DESC LIMIT 1",(task['id'],)).fetchone()
        if row:
            previous=json.loads(row[0])
            revision=previous.get('credential_selection_revision')
            if not revision or revision!=self.s.locations.credential_revision:
                raise ValueError('旧执行没有可核对的原连接；默认引用已变化，未恢复。')
        elif spec.get('native_fork') or db.execute('SELECT 1 FROM attempts WHERE task_id=?',(task['id'],)).fetchone():
            raise ValueError('旧执行缺少原连接证据，未恢复。')
        return self.default_connection(db,spec['provider'])

    def check_connection(self,c,db):
        if c.get('kind') == 'partner_connection':
            from .partner_connections import check_frozen
            return self.context(check_frozen(db, c)).validate()
        metadata=next((v for v in self.rows(db,'connection') if v['id']==c['id']),{})
        if not metadata.get('enabled',True):raise ValueError('原工作连接已停用，未启动执行。')
        return self.context(c).validate()

    def task_context(self,task,db=None):
        if db is None:
            with self.s.store.read() as own:return self.task_context(task,own)
        return self.check_connection(self.task_connection(task,db),db)

    def verify_run(self,task):return self.task_context(task)
