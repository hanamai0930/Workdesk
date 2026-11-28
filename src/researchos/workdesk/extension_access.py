"""Versioned dependencies and one-use grants for task-scoped MCP calls."""
import hashlib
from pathlib import Path
import time

from .store import digest, uid

# Keep eligibility in step with the document-tool grant on the same task store.
_ELIGIBLE = """SELECT t.project_id,t.id AS task_id,a.id AS attempt_id,t.spec_hash
 FROM tasks t JOIN attempts a ON a.task_id=t.id
 WHERE json_extract(t.grant,'$.spec_hash')=t.spec_hash
 AND json_extract(t.grant,'$.expires_at')>? AND t.state NOT IN ('CANCELLED','HANDED_OFF','PAUSED')
 AND a.stop_requested=0 AND a.state IN ('RUNNING','WAITING_INPUT','WAITING_APPROVAL','COMPLETED','SUCCEEDED')
 AND a.id=(SELECT id FROM attempts WHERE task_id=t.id ORDER BY started DESC,id DESC LIMIT 1)"""
SCOPE = ('project_id', 'task_id', 'attempt_id', 'spec_hash')


def contexts(db):
    return [dict(row) for row in db.execute(_ELIGIBLE+' ORDER BY a.started DESC', (time.time(),))]


def check_scope(db, scope):
    if not all(isinstance(scope.get(k), str) and scope[k] for k in SCOPE):
        raise ValueError('任务调用需要项目、任务、尝试和授权规格。')
    if not db.execute(_ELIGIBLE+' AND t.project_id=? AND t.id=? AND a.id=? AND t.spec_hash=?',
                      (time.time(), *(scope[k] for k in SCOPE))).fetchone():
        raise ValueError('任务授权、当前尝试或规格已变化，请回到原任务确认。')


def dependencies(config):
    if config['transport'] == 'http':
        return [{'kind':'endpoint', 'url':config['url']}]
    cwd = Path(config['cwd'])
    paths = [Path(config['command'])]
    for arg in config['args']:
        if Path(arg).suffix.lower() in {'.py','.js','.mjs','.cjs','.ps1','.bat','.cmd','.jar','.dll'}:
            path = cwd / arg
            if path.is_file():
                paths.append(path)
    result = [{'kind':'directory','path':str(cwd)}]
    if not cwd.is_dir():
        raise ValueError('MCP 工作目录缺失。')
    for path in dict.fromkeys(paths):
        with path.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        result.append({'kind':'file', 'path':str(path), 'sha256':checksum})
    return result


def check_dependencies(service, db, entry):
    actual = dependencies(entry['config'])
    if 'dependencies' in entry and actual != entry['dependencies']:
        raise ValueError('MCP 程序或脚本已变化，请保存新版本并重新检查。')
    origin = entry.get('bundle')
    if origin:
        from .capabilities import verify_bundle
        bundle = service.collaboration.get(db, 'capability', origin['id'])
        if (not bundle['enabled'] or bundle['revision'] != origin['revision'] or
                bundle['servers'].get(origin['server']) != entry['config']):
            raise ValueError('来源插件已停用或变更，请重新登记其服务。')
        verify_bundle(bundle)
    return actual


def bundle_activation(service, db, entry):
    origin = entry.get('bundle')
    if origin:
        return service.collaboration.get(db, 'capability', origin['id']).get('activation')


def authorize(service, db, entry, payload):
    if payload.get('confirm') is not True:
        raise ValueError('需要明确确认本次任务的工具和参数。')
    check_scope(db, payload)
    scope = {k:payload[k] for k in SCOPE}
    row = {'id':'extension-auth:'+uid(), **scope, 'extension_id':entry['id'],
           'revision':entry['revision'], 'activation':entry.get('activation'),
           'bundle_activation':bundle_activation(service,db,entry),
           'tool':payload['tool'], 'arguments':payload['arguments'], 'state':'AUTHORIZED',
           'command_id':service.command_identity, 'expires_at':time.time()+900,
           'process_instance':service.instance, 'implementation_revision':service.implementation,
           'tool_definition_hash':payload['tool_definition_hash']}
    row['input_hash'] = digest({'tool':row['tool'], 'arguments':row['arguments']})
    service.collaboration.put(db,'extension_authorization',row)
    service.store.event(scope['task_id'],scope['attempt_id'],'extension.authorized',
                        {k:row[k] for k in ('id','revision','tool','input_hash','command_id')},db)
    return row


def consume(service, db, entry, payload, run):
    grant = service.collaboration.get(db,'extension_authorization',payload.get('authorization_id'))
    check_scope(db, grant)
    if (grant['state'] != 'AUTHORIZED' or grant['expires_at'] <= time.time() or
            grant['process_instance'] != service.instance or grant['implementation_revision'] != service.implementation or
            grant['tool_definition_hash'] != digest(run['tool_definition']) or
            grant['extension_id'] != entry['id'] or grant['revision'] != entry['revision'] or
            grant['activation'] != entry.get('activation') or grant['tool'] != run['tool'] or
            grant['bundle_activation'] != bundle_activation(service,db,entry) or
            grant['input_hash'] != digest({'tool':run['tool'],'arguments':run['arguments']}) or
            any(k in payload and payload[k] != grant[k] for k in SCOPE)):
        raise ValueError('扩展授权已使用、已失效或与调用不匹配。')
    grant.update(state='CONSUMED', run_id=run['id'])
    service.collaboration.put(db,'extension_authorization',grant)
    run.update({k:grant[k] for k in SCOPE}, authorization_id=grant['id'],
               authorization_command_id=grant['command_id'], authorization_expires_at=grant['expires_at'],
               input_hash=grant['input_hash'])
    service.store.event(run['task_id'],run['attempt_id'],'extension.queued',
                        {k:run[k] for k in ('id','authorization_id','command_id','tool','input_hash')},db)
