"""Additional immutable assets referenced by the application ledger."""
import json
from pathlib import Path
import re


def required_assets(db, *, asset_root=None):
    from .work_coordinator_handoff_historical_assets import snapshot, Rows
    from .work_coordinator_handoff_post_round_v3_running_assets import HistoricalCatalog, running_items
    with snapshot(db) as observed:
        from .work_coordinator_handoff_text_rework_assets import text_items, TextHistoricalCatalog
        text = text_items(observed)
        items = running_items(observed)
        catalog = (TextHistoricalCatalog(observed, asset_root).read_all(text, items - text) if text else
            HistoricalCatalog(observed, asset_root).read_all(items) if items else None)
        required = _required_assets(db.connection if isinstance(db, Rows) else db, catalog=catalog)
        if catalog is not None:
            for name, value in catalog.assets.required.items():
                if name in required and required[name] != value:
                    raise ValueError('历史冻结资产与通用目录在同一路径出现不同期望字节。')
                required[name] = value
            catalog.assets.recheck()
        return required


def _required_assets(db, *, catalog=None):
    required={}
    def bundle(value):
        version=value['content_hash']
        if not re.fullmatch('[a-f0-9]{64}',version):raise ValueError('能力版本指纹无效。')
        for name,sha in value['files'].items():required['capabilities/'+version+'/'+name]={'sha256':sha}
    for row in db.execute("SELECT data FROM collaboration WHERE kind IN ('capability','capability_version')"):bundle(json.loads(row[0]))
    for row in db.execute('SELECT spec FROM tasks'):
        for b in (json.loads(row[0]).get('scene_snapshot') or {}).get('bundles',[]):bundle(b)
    for row in db.execute("SELECT data FROM collaboration WHERE kind='project_archive'"):
        archive = json.loads(row[0])
        if archive.get('state') == 'PREVIEW' and not archive.get('package_sha256'):
            continue
        fingerprint = archive.get('package_sha256', '')
        size = archive.get('package_bytes')
        if (not re.fullmatch(r'[a-f0-9]{64}', fingerprint)
                or type(size) is not int or not 0 < size <= 66 * 1024 * 1024):
            raise ValueError('项目归档包摘要或大小无效。')
        required['results/project-archives/' + fingerprint + '.zip'] = {'bytes': size, 'sha256': fingerprint}
    for row in db.execute("SELECT data FROM collaboration WHERE kind='work_result'"):
        r=json.loads(row[0]);version=r['id'].split(':')[-1]
        if not re.fullmatch('[a-f0-9]{64}',version):raise ValueError('成果指纹无效。')
        for name,item in r['snapshot']['manifest'].items():required['results/'+version+'/files/'+name]=item
    for row in db.execute("SELECT data FROM collaboration WHERE kind='work_unit_artifact'"):
        r=json.loads(row[0]);version=r['id'].split(':')[-1]
        if not re.fullmatch('[a-f0-9]{64}',version):raise ValueError('单元产物指纹无效。')
        from .store import digest
        if r['version']!=version or digest(r['snapshot'])!=version:raise ValueError('单元产物快照指纹不一致。')
        for name,item in r['snapshot']['manifest'].items():required['results/unit-'+version+'/files/'+name]=item
    def file_baseline(value):
        from .store import digest
        from . import workspace
        version=value['version']
        if (not re.fullmatch('[a-f0-9]{64}',version) or
                digest({k:v for k,v in value.items() if k!='version'})!=version or
                set(value['manifest'])|set(value['missing'])!=set(value['paths']) or
                set(value['manifest'])&set(value['missing'])):
            raise ValueError('文件返工基线摘要或路径集合不一致。')
        for name,item in value['manifest'].items():
            required['results/file-rework-base-'+version+'/files/'+workspace.relative(name)]=item
    for row in db.execute("SELECT data FROM collaboration WHERE kind='work_file_rework_revision'"):
        file_baseline(json.loads(row[0])['record']['baseline'])
    # New-root delivery owns its frozen baseline even before a runtime reader
    # is installed. Envelope integrity is not a proof of dispatch/completion.
    from .work_coordinator_handoff_records import (
        DELIVERY_KIND, COMPLETION_KIND, validate_row)
    from .work_coordinator_handoff_mixed_intent import PROTOCOL as MIXED_INTENT
    from .work_coordinator_handoff_mixed_assets import (required_records as mixed_records,
        require_path_assets as mixed_path_assets, require_context_assets as mixed_context_assets)
    from .work_coordinator_handoff_mixed_runtime import PROTOCOL as V5, UNIT as UNIT_V4
    deliveries={}
    for row in db.execute("SELECT data FROM collaboration WHERE kind=?",(DELIVERY_KIND,)):
        value=validate_row(DELIVERY_KIND,json.loads(row[0]))
        deliveries[value['id']]=value
        file_baseline(value['snapshot']['intent']['baseline'])
        from .work_coordinator_handoff_successor_intent import SUCCESSOR_INTENT, require_assets as successor_intent_assets
        from .work_coordinator_handoff_round_intent import PROTOCOL as ROUND_INTENT
        from .work_coordinator_handoff_round_assets import require_path_assets
        if value['snapshot']['intent']['protocol'] == MIXED_INTENT:
            mixed_path_assets(db, value['snapshot']['intent']['saved_path'])
        elif value['snapshot']['intent']['protocol'] == ROUND_INTENT:
            require_path_assets(db, value['snapshot']['intent']['saved_path'])
        elif value['snapshot']['intent']['protocol'] == SUCCESSOR_INTENT:
            successor_intent_assets(db,value['snapshot']['intent'])
    for row in db.execute("SELECT data FROM collaboration WHERE kind=?",(COMPLETION_KIND,)):
        value=validate_row(COMPLETION_KIND,json.loads(row[0]));snapshot=value['snapshot']
        delivery=deliveries.get(snapshot['delivery']['id'])
        if (not delivery or snapshot['delivery']!={k:delivery[k] for k in ('id','version')} or
                any(snapshot[k]!=delivery['snapshot'][k] for k in
                    ('item_id','root','marker','run_id','run_version','input_version'))):
            raise ValueError('新根完成记录缺少同根准确交付资产来源。')
    # Adjustment records are immutable data; asset closure grants no save edge.
    from .work_coordinator_handoff_adjustment_records import required_records
    required_records(db, file_baseline)
    from .work_coordinator_handoff_round_assets import required_records as round_records, require_path_assets, require_context_assets
    from .work_coordinator_handoff_round_runtime import PROTOCOL as V4, UNIT as UNIT_V3
    from .work_coordinator_handoff_round_intent import PROTOCOL as ROUND_INTENT
    round_records(db, file_baseline)
    mixed_records(db, file_baseline)
    from .work_coordinator_handoff_post_round_assets import required_records as post_records, require_context_assets as post_context_assets
    from .work_coordinator_handoff_post_round_contracts import UNIT as POST_UNIT, PROTOCOL as POST_COORDINATION
    post_records(db, file_baseline)
    from .work_coordinator_handoff_post_round_v3_assets import required_records as v3_records, require_successor_context
    from .work_coordinator_handoff_post_round_successor_contracts import PROTOCOL as SUCCESSOR_COORDINATION, UNIT as SUCCESSOR_UNIT
    from .work_coordinator_handoff_post_round_v2_run_contracts import PROTOCOL as V2_SUCCESSOR_COORDINATION, UNIT as V2_SUCCESSOR_UNIT
    v3_records(db, file_baseline, catalog=catalog)
    # FILE applications own a baseline before any replacement run exists.
    from .work_file_handoff_file_reworks import APPLICATION as FILE_APPLICATION
    for row in db.execute("SELECT data FROM collaboration WHERE kind='work_file_handoff_revision_application'"):
        value=json.loads(row[0]);snapshot=value['snapshot']
        if snapshot.get('protocol') != FILE_APPLICATION:continue
        from .store import digest
        baseline=snapshot.get('write_before_baseline')
        if (value['version']!=digest(snapshot) or value['id'].split(':')[-1]!=value['version'] or
                not baseline or baseline!=snapshot['lineage']['proof']['file_audit']['write_before_observation']):
            raise ValueError('FILE应用指纹或完整写前基线不一致。')
        file_baseline(baseline)
    from .work_coordinator_handoff_adjustment_runtime import (
        KIND as ADJUSTMENT_KIND, PROTOCOL as V3, UNIT as UNIT_V2, validate_capability, validate_unit)
    adjustment_capabilities = {}
    for row in db.execute('SELECT data FROM collaboration WHERE kind=?', (ADJUSTMENT_KIND,)):
        value = validate_capability(json.loads(row[0]))
        adjustment_capabilities[value['id']] = value
    for row in db.execute("SELECT data FROM collaboration WHERE kind='work_unit_run'"):
        run=json.loads(row[0]);frozen=run['input']
        if catalog is not None and run['id'] in catalog.inputs:
            context=frozen.get('handoff_coordination_context') or frozen.get('handoff_unit_context')
            catalog.require_context(run, context)
            if frozen.get('handoff_delivery_intent'):
                catalog.require_intent(run, frozen['handoff_delivery_intent'])
            file_baseline(context['material_observation'])
            if frozen.get('write_before_baseline'):file_baseline(frozen['write_before_baseline'])
            continue
        intent=frozen.get('handoff_delivery_intent')
        from .work_coordinator_handoff_successor_intent import SUCCESSOR_INTENT, require_assets as successor_intent_assets
        if intent and intent.get('protocol') in (SUCCESSOR_INTENT, ROUND_INTENT, MIXED_INTENT):
            if intent['protocol'] == MIXED_INTENT: mixed_path_assets(db, intent['saved_path'])
            elif intent['protocol'] == ROUND_INTENT: require_path_assets(db, intent['saved_path'])
            else: successor_intent_assets(db,intent)
            actual=[d for d in deliveries.values() if d['snapshot']['run_id']==run['id']]
            if len(actual)!=1 or actual[0]['snapshot']['intent']!=intent:
                raise ValueError('后继首次B冻结输入缺少唯一准确交付资产。')
        context=frozen.get('handoff_coordination_context')
        if context:
            from .work_coordinator_handoff_context import PROTOCOL
            from .store import digest
            from .work_coordinator_handoff_runtime import PROTOCOL as V2
            if context.get('protocol') not in (PROTOCOL,V2,V3,V4,V5,POST_COORDINATION,SUCCESSOR_COORDINATION,V2_SUCCESSOR_COORDINATION) or context.get('version')!=digest({k:v for k,v in context.items() if k!='version'}):
                raise ValueError('新协调根冻结观察协议或完整摘要不一致。')
            if context.get('protocol') in (SUCCESSOR_COORDINATION,V2_SUCCESSOR_COORDINATION): require_successor_context(db, context)
            if context.get('protocol') == POST_COORDINATION: post_context_assets(db, context)
            if context.get('protocol') == V5: mixed_context_assets(db, context)
            if context.get('protocol') == V4: require_context_assets(db, context)
            if context.get('protocol') == V3:
                capability = adjustment_capabilities.get(context.get('adjustment_capability', {}).get('id'))
                if not capability or context['adjustment_capability'] != {k:capability[k] for k in ('id','version')}:
                    raise ValueError('V3输入缺少准确调整能力资产。')
            file_baseline(context['material_observation'])
        context=frozen.get('handoff_unit_context')
        if context:
            from .work_coordinator_handoff_records import validate, UNIT_INPUT
            if context.get('protocol') in (SUCCESSOR_UNIT,V2_SUCCESSOR_UNIT):
                require_successor_context(db, context)
            elif context.get('protocol') == POST_UNIT:
                post_context_assets(db, context)
            elif context.get('protocol') == UNIT_V4:
                mixed_context_assets(db, context)
            elif context.get('protocol') == UNIT_V3:
                require_context_assets(db, context)
            elif context.get('protocol') == UNIT_V2:
                validate_unit(context)
                capability = adjustment_capabilities.get(context['adjustment_capability']['id'])
                if not capability or context['adjustment_capability'] != {k:capability[k] for k in ('id','version')}:
                    raise ValueError('普通V2输入缺少准确调整能力资产。')
            else:
                validate(context,UNIT_INPUT)
            file_baseline(context['material_observation'])
        value=frozen.get('write_before_baseline')
        if value:file_baseline(value)
    return required


def rebind_catalog(db,target):
    # Rebind only current, editable capability configuration. Frozen task specs
    # and their hashes remain historical; native homes are separate references.
    from .store import canonical,uid
    for row in db.execute("SELECT id,data FROM collaboration WHERE kind='capability'").fetchall():
        value=json.loads(row[1]);old=value['path'];new=str(Path(target)/'capabilities'/value['content_hash'])
        if old==new:continue
        value['path']=new;value['revision']=uid()
        for config in value['servers'].values():
            for key,item in list(config.items()):
                if isinstance(item,str):config[key]=item.replace(old,new)
                elif isinstance(item,list):config[key]=[a.replace(old,new) if isinstance(a,str) else a for a in item]
        db.execute('UPDATE collaboration SET data=? WHERE id=?',(canonical(value),row[0]))
        import time
        version=dict(value,id=value['revision'],capability_id=value['id'],restored_path_rebound=True)
        db.execute('INSERT INTO collaboration VALUES(?,?,?,?)',('capability_version',version['id'],canonical(version),time.time()))
