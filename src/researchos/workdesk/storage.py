"""Account-independent local library configuration; credentials remain references."""
from hashlib import sha256
import json
import os
from pathlib import Path
import time

from .paths import Locations, atomic_json, legacy, local_credential_path
from . import workspace
from .store import uid


class LocalStorage:
    def __init__(self, service):self.s=service;self.locations=service.locations

    def initialize_library(self):
        loc=self.locations
        if loc.user is None or not loc.data.is_relative_to(loc.user) or loc.data_pointer.exists():return
        self._require_user()
        if not loc.credential_pointer.exists():
            atomic_json(loc.credential_pointer,{'schema':1,'revision':uid(),'codex_home':None,'api_env':None,'api_env_sha256':None})
        atomic_json(loc.data_pointer,{'schema':1,'active_data':str(loc.data),'initial_library':True,'at':time.time()})

    def handlers(self):
        return {'storage.prepare':self.prepare,'storage.credentials.select':self.select}

    def _require_user(self):
        if self.locations.user is None:raise ValueError('此实例使用显式测试/旧目录；未启用独立本地资料根目录。')
        workspace.plain(self.locations.user,self.locations.user,missing=True)
        workspace.plain(self.locations.credential_pointer,self.locations.user,missing=True)
        workspace.plain(self.locations.user/'config/credential-switches',self.locations.user,missing=True)

    def _api_reference(self, value):
        if value is None:return None,None
        path=local_credential_path(value)
        credentials,fingerprint=legacy(self.locations,'provider-harness','credentials').load_credentials(path,with_fingerprint=True)
        if not credentials:raise ValueError('该文件没有可识别的官方API凭据；未改变现有引用。')
        return str(path),fingerprint

    def _initial_references(self):
        # Read metadata and explicit paths only; never inspect Codex auth bodies.
        old=Locations(self.locations.base,self.locations.data)
        try:home=old.codex_home;home=str(home) if (home/'auth.json').is_file() else None
        except (ValueError,OSError):home=None
        api=self.locations.base/'.env'
        api_path,fingerprint=self._api_reference(str(api)) if api.is_file() else (None,None)
        return {'schema':1,'revision':uid(),'codex_home':home,'api_env':api_path,'api_env_sha256':fingerprint}

    def _revoke(self, db):
        db.execute('UPDATE tasks SET grant=NULL')
        db.execute("UPDATE profiles SET data=json_set(data,'$.auth_status','NOT_CHECKED_AFTER_REFERENCE_CHANGE'),checked=0")
        for plan in self.s.collaboration.all(db,'plan'):
            plan.update(queue_state='PAUSED',pause_reason='账号引用已改变，核对后重新确认任务范围。')
            self.s.collaboration.put(db,'plan',plan)

    def _save(self, db, value):
        value={**value,'schema':1,'revision':uid(),'selected_at':time.time()}
        identity=self.s.command_identity
        journal=self.locations.user/'config/credential-switches'/(identity+'.json')
        if journal.exists():raise ValueError('该账号切换操作已有持久核对记录；刷新当前选择，不重放原操作。')
        intent={'schema':1,'state':'INTENT','command_id':identity,'kind':self.s.command_kind,
                'payload_hash':self.s.command_payload_hash,'ledger':str(self.s.store.path),
                'store_id':db.execute("SELECT value FROM meta WHERE key='store_id'").fetchone()[0],
                'revision':value['revision'],'created':time.time(),'no_dispatch':True}
        # The independent intent survives a SQLite rollback and explains the
        # file effect. Reconciliation revokes grants and pauses queues without
        # sending or replaying the interrupted command.
        atomic_json(journal,intent)
        atomic_json(self.locations.credential_pointer,value)
        self._revoke(db)
        self.s.store.event(None,None,'credential_references_selected',{
            'credential_switch_id':identity,
            'revision':value['revision'],'codex_configured':bool(value.get('codex_home')),
            'api_configured':bool(value.get('api_env')),'grants_revoked':True,'no_dispatch':True},db)
        return {'revision':value['revision'],'credential_switch_id':identity,'history_preserved':True,'grants_revoked':True,'no_dispatch':True,
                'official_login_unchanged':True}

    def reconcile(self):
        if self.locations.user is None:return
        folder=self.locations.user/'config/credential-switches'
        if folder.exists():self._require_user()
        for path in sorted(folder.glob('*.json')):
            workspace.plain(path,folder)
            if path.stat().st_size>65536:raise ValueError('账号切换核对记录过大，执行保持停止。')
            intent=json.loads(path.read_text(encoding='utf-8'))
            if intent.get('state')!='INTENT' or intent.get('ledger')!=str(self.s.store.path):continue
            with self.s.store.read() as db:
                store_id=db.execute("SELECT value FROM meta WHERE key='store_id'").fetchone()[0]
                receipt=db.execute('SELECT kind,payload_hash,receipt FROM commands WHERE id=?',(intent['command_id'],)).fetchone()
            if store_id!=intent['store_id']:raise ValueError('账号切换核对台账身份不符；不派发。')
            if receipt:
                if receipt['kind']!=intent['kind'] or receipt['payload_hash']!=intent['payload_hash']:raise ValueError('账号切换命令回执身份冲突。')
                intent.update(state='COMMITTED',receipt_saved=True)
            else:
                # The explicitly selected file may already be effective. Keep
                # it visible, conservatively revoke all old grants, and record
                # the uncertain command separately; never roll back an account
                # choice silently or replay the original mutation.
                with self.s.store.write() as db:
                    self._revoke(db)
                    self.s.store.event(None,None,'credential_switch_recovered',{
                        'command_id':intent['command_id'],'revision':intent['revision'],
                        'original_command_committed':False,'grants_revoked':True,'queues_paused':True,'no_dispatch':True},db)
                intent.update(state='RECOVERED_NO_COMMAND_REPLAY',receipt_saved=False,
                              grants_revoked=True,queues_paused=True,reselection_allowed=True)
            intent['reconciled_at']=time.time();atomic_json(path,intent)

    def select(self, db, payload):
        self._require_user();self.s.persistence.idle(db)
        if set(payload)-{'codex_home','api_env'}:raise ValueError('仅接受本地账号目录或API凭据文件引用，不接受凭据正文。')
        if not payload:raise ValueError('请选择要更改或断开的认证引用。')
        try:value=self.locations.credential_references() or self._initial_references()
        except (ValueError,OSError):
            # Explicit repair of an unavailable registry starts disconnected;
            # never import an unrelated legacy account while repairing it.
            value={'schema':1,'codex_home':None,'api_env':None,'api_env_sha256':None}
        if 'codex_home' in payload:
            selected=payload['codex_home']
            value['codex_home']=str(self.locations.validate_codex_home(selected)) if selected is not None else None
        if 'api_env' in payload:
            value['api_env'],value['api_env_sha256']=self._api_reference(payload['api_env'])
        return self._save(db,value)

    def prepare(self, db, payload):
        self._require_user();self.s.persistence.idle(db)
        if payload:raise ValueError('本地保存位置由应用固定配置，此命令不接受任意迁移路径。')
        if os.environ.get('WORKDESK_DATA_DIR'):raise ValueError('当前由WORKDESK_DATA_DIR覆盖位置，先移除该覆盖再准备独立资料库。')
        if self.locations.data.is_relative_to(self.locations.user):raise ValueError('当前资料库已在独立本地目录；普通备份请使用阶段快照。')
        refs=self.locations.credential_references()
        initial=(refs or self._initial_references()) if not self.locations.credential_pointer.exists() else None
        # The SQLite online backup must read the committed source before this
        # transaction writes anything. A large grant revocation can spill the
        # writer cache and otherwise block that second connection indefinitely.
        backup=self.s.persistence.backup(db,{})
        restored=self.s.persistence.restore(db,{'backup_id':backup['backup_id']})
        selected=self._save(db,initial) if initial is not None else {}
        return {**restored,'storage_root':str(self.locations.user),'original_data_preserved':True,
                **({'credential_switch_id':selected['credential_switch_id']} if selected else {}),
                'credential_files_copied':False,'activation_required':True,'no_dispatch':True}

    def catalog(self):
        try:
            refs=self.locations.credential_references()
            references={'state':'CONFIGURED' if refs else 'LEGACY_REFERENCES',
                        'codex_home':refs.get('codex_home') if refs else None,
                        'api_env':refs.get('api_env') if refs else None,
                        'revision':refs.get('revision') if refs else None}
        except (ValueError,OSError):references={'state':'REFERENCE_UNAVAILABLE','history_readable':True}
        switches=[]
        if self.locations.user:
            for path in sorted((self.locations.user/'config/credential-switches').glob('*.json')):
                try:
                    item=json.loads(path.read_text(encoding='utf-8'))
                    switches.append({k:item.get(k) for k in ('command_id','state','reconciled_at','no_dispatch')})
                except (OSError,ValueError):switches.append({'state':'RECONCILIATION_REQUIRED'})
        return {'root':str(self.locations.user) if self.locations.user else None,
                'library':str(self.locations.data),'configuration':str(self.locations.data_pointer),
                'independent':bool(self.locations.user and self.locations.data.is_relative_to(self.locations.user)),
                'credential_references':references,'account_required_to_read_history':False,
                'credential_switches':switches,
                'ordinary_backups_include_credentials':False,'ordinary_backups_include_native_homes':False,
                'account_switch_dispatches':False,'cross_machine_migration':'OUT_OF_SCOPE'}
