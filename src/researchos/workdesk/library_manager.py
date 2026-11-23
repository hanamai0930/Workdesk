"""Verified backup copying/import and legacy browser records, without activating on import."""
from pathlib import Path
import json

from . import workspace
from .paths import atomic_json
from .store import uid


class Libraries:
    def __init__(self,service):self.s=service;self.c=service.collaboration
    def handlers(self):return {'library.export':self.export,'library.import':self.import_package,'library.legacy-import':self.legacy}
    def catalog(self):
        value=self.s.persistence.catalog();value['coverage']={'included':['本地记录、草稿、计划和目标','冻结材料与成果副本','已导入的Skills/插件内容','用量台账与执行审计'],
          'excluded':['项目文件夹全部内容','官方原生会话目录和网页登录','账号凭据与运行程序'],'restore':'暂存核对后单独激活；不合并不同台账的费用历史'}
        return value
    def export(self,db,p):
        self.s.persistence.idle(db);row=self.c.get(db,'backup',p.get('backup_id'));package=self.s.persistence.validate_package(row['root'],row['manifest_hash'])
        parent=Path(p.get('directory',''))
        if not parent.is_absolute() or not workspace.io_path(parent).is_dir():raise ValueError('请选择备份导出文件夹。')
        workspace.plain(parent,parent);target=parent/('Workdesk-backup-'+package['id'])
        workspace.plain(target,parent,missing=True);workspace.io_path(target).mkdir(exist_ok=False)
        for name,item in package['files'].items():
            raw=workspace.io_path(workspace.plain(Path(row['root'])/'state'/name,Path(row['root'])/'state')).read_bytes()
            from hashlib import sha256
            if len(raw)!=item['bytes'] or sha256(raw).hexdigest()!=item['sha256']:raise ValueError('复制时备份已变化。')
            dest=workspace.io_path(target/'state'/name);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
        atomic_json(target/'manifest.json',package);self.s.persistence.validate_package(target,row['manifest_hash'])
        return {'directory':str(target),'no_dispatch':True,'credentials_copied':False}
    def import_package(self,db,p):
        self.s.persistence.idle(db);source=Path(p.get('directory',''))
        if not source.is_absolute():raise ValueError('请选择包含manifest.json的备份文件夹。')
        package=self.s.persistence.validate_package(source)
        store_id=db.execute("SELECT value FROM meta WHERE key='store_id'").fetchone()[0]
        if package['store_id']!=store_id:raise ValueError('该备份属于另一套资料台账，当前不混合导入；原资料不变。')
        known=db.execute("SELECT data FROM collaboration WHERE kind='backup' AND id=?",(package['id'],)).fetchone()
        if not known or json.loads(known[0])['manifest_hash']!=package['manifest_hash']:raise ValueError('这不是当前台账已登记的备份版本，未导入或覆盖登记。')
        self.c.put(db,'backup',{'id':package['id'],'root':str(source),'manifest_hash':package['manifest_hash'],'created':package['created'],'state':'READY'})
        return {'backup_id':package['id'],'no_dispatch':True,'activation_required':True}
    def legacy(self,db,p):
        document=p.get('document')
        if not isinstance(document,dict) or set(document)-{'schema','source','chats','drafts'}:raise ValueError('旧来源文件格式不支持。')
        result=self.s.ui_features.import_legacy(db,{'chats':document.get('chats',[]),'drafts':document.get('drafts',[])})
        self.c.put(db,'legacy_import',{'id':uid(),'source':str(document.get('source','用户选择的旧来源文件'))[:300],'imported':result['imported'],'native_history_restored':False})
        return {**result,'source_records_preserved':True,'native_history_restored':False}
