"""Explicit capability/configuration metadata; login stays in the official profile."""
import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import sqlite3
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .paths import legacy, clean_env
from .store import canonical
from .context import describe
from .agent_runtime import PACKAGE_METADATA, local_package
from .model_catalog import snapshot, MODEL_URLS


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):return None


DEFAULTS={
 'codex':{'name':'Codex','harness':'Codex app-server','model':'gpt-6-luna','effort':'low','tier':'fast',
          'auth':'subscription','resume':'native_session','interrupt':'turn_interrupt',
          'evidence':'2026-10-16有限真实样板；通用适配器须重新验证'},
 'deepseek':{'name':'DeepSeek','harness':'DeepSeek Harness','model':'deepseek-flash','effort':'off','tier':'standard',
             'auth':'official_api_key','resume':'new_session_context_only','interrupt':'dedicated_runtime_shutdown',
             'evidence':'已安装SDK无单会话恢复/中断；专属运行时停止已有有限实测'},
 'qwen':{'name':'Qwen','harness':'Qwen Code SDK 0.1.20 / CLI 0.25.2','model':'qwen3.8-flash','effort':'off','tier':'standard',
         'auth':'official_api_key','resume':'native_session','interrupt':'sdk_interrupt_and_owned_runtime_exit',
         'evidence':'2026-10-17通用修补8/8、正常收尾、SDK中断回执及已知工具/专属运行时退出、原会话恢复实测；不证明任意外部进程或远端强制终止'},
}


class Profiles:
    def __init__(self,locations,store):
        self.locations,self.store=locations,store
        self._transaction_db=ContextVar('workdesk_profile_transaction_db',default=None)

    @contextmanager
    def transaction(self,db):
        """Read metadata through this command's real transaction, without caching."""
        from .bridge.database import trusted_connection
        if (not trusted_connection(db) or not db.in_transaction or
                self._transaction_db.get() is not None):
            raise ValueError('模型配置读取须绑定本次唯一真实事务。')
        main=[r for r in db.execute('PRAGMA database_list') if r[1]=='main']
        if len(main)!=1 or Path(main[0][2]).resolve()!=Path(self.store.path).resolve():
            raise ValueError('模型配置事务不属于本Store。')
        token=self._transaction_db.set(db)
        try:yield
        finally:self._transaction_db.reset(token)

    def credentials(self):
        path=self.locations.api_env;refs=self.locations.credential_references()
        if path is None:return {}
        reader=legacy(self.locations,'provider-harness','credentials')
        try:return reader.load_credentials(path,expected_sha256=refs['api_env_sha256'] if refs else None)
        except reader.CredentialError:raise ValueError('API凭据文件已变化或不可用；请重新选择准确引用，不回退其他Key。') from None

    def list(self, db=None):
        if db is None:
            db=self._transaction_db.get()
            if db is not None and not db.in_transaction:
                raise ValueError('模型配置读取事务已结束。')
        if db is None:
            with self.store.read() as connection:return self.list(connection)
        results=[]
        for key,default in DEFAULTS.items():
            row=db.execute('SELECT * FROM profiles WHERE id=?',(key,)).fetchone()
            current=json.loads(row['data']) if row else {}
            current.setdefault('models',[default['model']])
            catalog=snapshot(key,current)
            selection=current.get('selection') or {k:default[k] for k in ('model','effort','tier')}
            auth_error=None
            try:codex_ready=bool(self.locations.codex and (self.locations.codex_home/'auth.json').is_file()) if key=='codex' else False
            except (OSError,ValueError):codex_ready=False;auth_error='本地认证引用不可用；历史资料保留，执行前需重新选择。'
            ready=(codex_ready if key=='codex' else
                   (self.locations.runtime/'deepseek-venv/Scripts/python.exe').is_file() if key=='deepseek' else
                   self.locations.node.is_file() and (self.locations.runtime/'qwen/node_modules/@qwen-code/sdk').is_dir())
            harness=default['harness']
            if key=='deepseek':
                version=local_package(self.locations,*PACKAGE_METADATA[key])
                if version:
                    harness+=' '+version
            results.append({'id':key,**default,**current,**selection,'runtime_present':ready,'harness':harness,
                            **({'auth_status':'REFERENCE_UNAVAILABLE','auth_error':auth_error} if auth_error else {}),
                            'models':current.get('models',[default['model']]),'auth_checked_at':(row['checked'] or None) if row else None,
                            'enabled_models':[m['model'] for m in catalog['models'] if m['runnable']],
                            'model_catalog':catalog,
                            'context_by_model':{m:describe(self.locations,key,m,default['effort']) for m in current.get('models',[default['model']])},
                            'current_connection':'UNKNOWN','remaining_quota':'UNKNOWN',
                            'check_policy':'显式检查不发送模型消息、不退出账号'})
        return results

    def defaults(self,provider):
        profile=next((p for p in self.list() if p['id']==provider),None)
        if profile is None:raise ValueError('未知工作端。')
        return {k:profile[k] for k in ('model','effort','tier')}

    def select(self,db,payload):
        provider=payload.get('provider');keys=('model','effort','tier')
        self.validate(provider,*(payload.get(k) for k in keys))
        row=db.execute('SELECT data,checked FROM profiles WHERE id=?',(provider,)).fetchone()
        data=json.loads(row['data']) if row else {};previous=data.get('selection')
        data['selection']={k:payload[k] for k in keys}
        db.execute('INSERT INTO profiles VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                   (provider,canonical(data),row['checked'] if row else 0))
        self.store.event(None,None,'model_default_selected',{'provider':provider,'previous':previous,
                         'selection':data['selection'],'effect':'NEW_DRAFTS_ONLY','no_dispatch':True},db)
        return {'provider':provider,'selection':data['selection'],'effect':'NEW_DRAFTS_ONLY','no_dispatch':True}

    def validate(self,provider,model,effort,tier):
        profile=next((p for p in self.list() if p['id']==provider),None)
        if profile is None or not profile['runtime_present']:raise ValueError('工作端未安装或不可用。')
        if model not in profile['models']:raise ValueError('模型不在本机已登记目录内；请先显式检查配置。')
        entry=next(m for m in profile['model_catalog']['models'] if m['model']==model)
        if not entry['runnable']:raise ValueError('该模型尚未登记准确协议、能力与费用边界，未启用。')
        if effort not in entry.get('selection_efforts',entry['efforts']):raise ValueError('该模型/当前任务没有接通此思考档位；Ultra的原生委派不在当前任务授权内。')
        if tier not in entry['tiers']:raise ValueError('该模型/渠道没有接通此速度档位，未静默降级。')
        return profile

    def selection_snapshot(self,provider,model,effort,tier):
        profile=self.validate(provider,model,effort,tier);catalog=profile['model_catalog']
        entry=next(m for m in catalog['models'] if m['model']==model)
        return {'catalog_revision':catalog['revision'],'entry':entry,'effort':effort,'tier':tier,
                'effect':'FROZEN_FOR_TASK; REFRESH_AND_DEFAULT_CHANGE_DO_NOT_MODIFY_THIS_SNAPSHOT'}

    def install(self,provider,result,*,expected_codex_home=None):
        """Save metadata, never a selection change or a model dispatch."""
        result={**result,'catalog_checked_at':result.get('catalog_checked_at',time.time())}
        with self.store.write() as db:
            if expected_codex_home is not None and self.locations.codex_home!=expected_codex_home:
                raise ValueError('读取期间 Codex 账号已更换。请刷新当前账号目录。')
            row=db.execute('SELECT data FROM profiles WHERE id=?',(provider,)).fetchone()
            previous=json.loads(row['data']) if row else {}
            if previous.get('selection'):result['selection']=previous['selection']
            old=snapshot(provider,previous);new=snapshot(provider,result)
            db.execute('INSERT INTO profiles VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,checked=excluded.checked',
                       (provider,canonical(result),time.time()))
            self.store.event(None,None,'model_catalog_refreshed',{'provider':provider,'previous_revision':old['revision'],
                             'snapshot':new,'selection_preserved':True,'no_generation':True},db)
        return result

    @staticmethod
    def api_catalog(provider,secret):
        """Return validated official model metadata without selecting credentials."""
        request=Request(MODEL_URLS[provider],headers={'Authorization':'Bearer '+secret.reveal_for_provider(),'Accept':'application/json'})
        with build_opener(NoRedirect()).open(request,timeout=15) as response:
            raw=response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:raise ValueError('目录超过2MiB。')
            body=json.loads(raw);rows=body.get('data') if isinstance(body,dict) else None
            if not isinstance(rows,list) or not rows or len(rows)>5000:raise ValueError('模型目录格式或大小不支持。')
            keys={'id','name','context_window','max_output_tokens','input_modalities','output_modalities','effort','api_capabilities'}
            native=[{k:v for k,v in item.items() if k in keys} for item in rows if isinstance(item,dict)]
            if len(native)!=len(rows) or any(not isinstance(m.get('id'),str) or not 0<len(m['id'])<=200 or any(ord(c)<32 for c in m['id']) for m in native):raise ValueError('模型ID格式不支持。')
            if len({m['id'] for m in native})!=len(native):raise ValueError('模型目录ID重复。')
        return {'auth_status':'OFFICIAL_METADATA_ACCEPTED_GENERATION_NOT_TESTED','models':[m['id'] for m in native],
                'native_models':native,'evidence_source':MODEL_URLS[provider]+'; GET only, no generation'}

    @staticmethod
    def observe_codex(rpc,home,*,timeout=5):
        """Read the account and model inventory from the same initialized client."""
        account=rpc.request('account/read',{'refreshToken':False},timeout=timeout)
        account_type=(account.get('account') or {}).get('type')
        if account_type!='chatgpt':raise ValueError('需要原订阅登录；没有API Key回退。')
        rows=[];cursor=None;seen=set()
        for _ in range(5):
            models=rpc.request('model/list',{'limit':100,'includeHidden':False,**({'cursor':cursor} if cursor else {})},timeout=timeout)
            rows.extend(models.get('data',[]));cursor=models.get('nextCursor')
            if not cursor:break
            if cursor in seen:raise ValueError('原生目录游标重复，未覆盖旧目录。')
            seen.add(cursor)
        else:raise ValueError('原生目录超出本次500项边界，未截断为完整目录。')
        if not rows:raise ValueError('原生目录为空，未覆盖旧目录。')
        native=[]
        cache=home/'models_cache.json'
        cached=json.loads(cache.read_text(encoding='utf-8')).get('models',[]) if cache.is_file() else []
        for m in rows:
            saved=next((v for v in cached if v.get('slug')==m['model']),{})
            native.append({'id':m['model'],'name':m.get('displayName'),'context_tokens':saved.get('context_window'),
                           'input_modalities':saved.get('input_modalities'),
                           'default_effort':m.get('defaultReasoningEffort')})
        return {'auth_status':'SUBSCRIPTION_OBSERVED','models':[m['model'] for m in rows], 'native_models':native,
                'reasoning_by_model':{m['model']:[r['reasoningEffort'] for r in m.get('supportedReasoningEfforts',[])] for m in rows},
                'evidence_source':'本机account/read与model/list；未发送模型消息'}

    def check(self,provider):
        if provider not in DEFAULTS:raise ValueError('未知工作端。')
        if provider=='codex':
            transport=legacy(self.locations,'codex-harness','rpc')
            executable=self.locations.codex
            if not executable:raise ValueError('未找到本机Codex程序。')
            home=self.locations.codex_home
            env=clean_env();env['CODEX_HOME']=str(home)
            rpc=transport.Rpc(transport.codex_command(str(executable))+['-c','project_doc_max_bytes=0','-c','features.apps=false'],
                              self.locations.data,env,max_frame=None)
            try:
                rpc.request('initialize',{'clientInfo':{'name':'workdesk_backend','version':'1.0'},'capabilities':{'experimentalApi':True}},timeout=5)
                rpc.send({'method':'initialized','params':{}})
                result=self.observe_codex(rpc,home)
            finally:rpc.close()
            return self.install(provider,result,expected_codex_home=home)
        else:
            credentials=self.credentials();secret=credentials.get(provider)
            if secret is None:raise ValueError('本地未配置指定Key。')
            if provider=='qwen' and secret.billing_kind!='general_api':raise ValueError('当前仅接通千问通用API Key。')
            try:
                result=self.api_catalog(provider,secret)
            except Exception as exc:
                self.store.event(None,None,'model_catalog_refresh_failed',{'provider':provider,'error_class':type(exc).__name__,
                                 'old_snapshot_preserved':True,'no_retry':True,'no_generation':True})
                raise ValueError('官方目录刷新失败；旧目录与选择保留，未自动重试或发送生成。') from None
        return self.install(provider,result)
