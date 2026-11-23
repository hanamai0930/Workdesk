"""Closed value contracts and immutable storage for the new root ledger.

This is the record layer, not a source verifier. In particular, valid hashes,
stored envelopes and UNIT_INPUT data never confer runtime admission. The ledger
must rebuild authoritative run/spec/input/history before using these records.
"""
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import time

from . import workspace
from .store import digest
from .work_coordinator_handoff_roots import ROOT


INTENT = 'COORDINATOR_FILE_HANDOFF_INTENT_V1'
DELIVERY = 'COORDINATOR_FILE_HANDOFF_DELIVERY_V1'
COMPLETION = 'COORDINATOR_FILE_HANDOFF_COMPLETION_V1'
UNIT_INPUT = 'COORDINATOR_FILE_HANDOFF_UNIT_INPUT_V1'
DELIVERY_KIND = 'work_coordinator_handoff_delivery'
COMPLETION_KIND = 'work_coordinator_handoff_completion'
KINDS = {DELIVERY_KIND: DELIVERY, COMPLETION_KIND: COMPLETION}
PERMISSIONS = {'save_permission': False, 'current_input_permission': False,
               'execution_permission': False, 'no_dispatch': True}
PHASES = {'BEFORE_A', 'A_CAPTURED', 'B_IN_FLIGHT', 'B_EXITED_UNCAPTURED',
          'B_COMPLETED', 'BLOCKED'}
DISPATCH_PHASES = {'BEFORE_A', 'A_CAPTURED', 'B_COMPLETED'}
FIELDS = {
    INTENT: {'item_id', 'item_revision', 'root', 'marker', 'relation', 'driver',
             'authorization', 'source_run', 'source_evidence', 'target_revision',
             'protected_history', 'baseline', 'input_template_version', 'frontier', 'scope'},
    DELIVERY: {'item_id', 'root', 'marker', 'intent', 'run_id', 'run_version',
               'input_version', 'task_spec_version', 'materials'},
    COMPLETION: {'item_id', 'root', 'marker', 'delivery', 'run_id', 'run_version',
                 'input_version', 'evidence', 'files_version'},
    UNIT_INPUT: {'root', 'marker', 'task', 'driver', 'authorization', 'unit_revision',
                 'proposal', 'phase', 'prior_frontier', 'material_observation', 'scope', *PERMISSIONS},
}
BASELINE_SCOPE = 'EXACT_DECLARED_PATHS_ONLY; NOT_DIRECTORY_BACKUP_OR_OS_ISOLATION'


def fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError('新根记录字段集合不符。')


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError('新根记录身份或范围无效。')


def sha(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('新根记录须为准确SHA256引用。')


def task_revision(value):
    if type(value) is not int or value < 1:
        raise ValueError('任务修订须为准确正整数。')


def reference(value):
    fields(value, {'id', 'version'})
    text(value['id']); sha(value['version'])


def versioned(value):
    return {**deepcopy(value), 'version': digest(value)}


def manifest(value):
    if not isinstance(value, dict) or len(value) > workspace.MAX_FILES:
        raise ValueError('新根材料manifest无效。')
    total = 0
    for name, record in value.items():
        if workspace.relative(name) != name:
            raise ValueError('新根材料须使用规范相对路径。')
        fields(record, {'sha256', 'bytes'}); sha(record['sha256'])
        if type(record['bytes']) is not int or record['bytes'] < 0:
            raise ValueError('新根材料字节数无效。')
        total += record['bytes']
    if total > workspace.MAX_ATTACHMENT_TOTAL:
        raise ValueError('新根材料超过总大小上限。')


def observation(value):
    fields(value, {'root', 'paths', 'manifest', 'missing', 'scope', 'version'})
    text(value['root']); sha(value['version'])
    if str(Path(value['root']).absolute()) != value['root']:
        raise ValueError('新根观察须为准确绝对目录。')
    names, missing = value['paths'], value['missing']
    if (not isinstance(names, list) or not names or len(names) > workspace.MAX_FILES or
            any(not isinstance(n, str) for n in names) or names != sorted(set(names)) or
            not isinstance(missing, list) or any(not isinstance(n, str) for n in missing) or
            missing != sorted(set(missing))):
        raise ValueError('新根观察须完整且唯一有序。')
    for name in names:
        if workspace.relative(name) != name:
            raise ValueError('新根观察路径不是规范相对路径。')
    manifest(value['manifest'])
    if (set(value['manifest']) | set(missing) != set(names) or
            set(value['manifest']) & set(missing) or value['scope'] != BASELINE_SCOPE or
            value['version'] != digest({k: v for k, v in value.items() if k != 'version'})):
        raise ValueError('新根观察路径全集或完整摘要不符。')


def frontier(value):
    if not isinstance(value, list) or len(value) > 1000:
        raise ValueError('新根前沿须为完整有序列表。')
    seen = set()
    for entry in value:
        fields(entry, {'run_id', 'run_version', 'input_version', 'task_spec_version',
                       'workspace', 'evidence', 'files'})
        text(entry['run_id']); text(entry['workspace'])
        if entry['run_id'] in seen:
            raise ValueError('新根前沿不能重复运行。')
        seen.add(entry['run_id'])
        for key in ('run_version', 'input_version', 'task_spec_version'):
            sha(entry[key])
        if (not isinstance(entry['evidence'], dict) or not isinstance(entry['files'], dict) or
                entry['evidence'].get('run_id') != entry['run_id'] or
                entry['files'].get('run_id') != entry['run_id'] or
                entry['files'].get('input_version') != entry['input_version']):
            raise ValueError('新根前沿原证据或输入引用不符。')
        sha(entry['evidence'].get('version'))


def identity(value):
    reference(value['root'])
    marker = value['marker']
    fields(marker, {'protocol', 'plan_id', 'item_id', 'item_revision', 'project_id',
                    'project_root', 'work_requirements_version', 'configuration_version', 'version'})
    if marker['protocol'] != ROOT or marker['plan_id'] != value['root']['id']:
        raise ValueError('新根记录不能借用旧fixed协议或其它根身份。')
    for key in ('item_id', 'project_id', 'project_root'):
        text(marker[key])
    task_revision(marker['item_revision'])
    for key in ('work_requirements_version', 'configuration_version', 'version'):
        sha(marker[key])
    if (marker['version'] != digest({k: v for k, v in marker.items() if k != 'version'}) or
            str(Path(marker['project_root']).absolute()) != marker['project_root'] or
            value.get('item_id', marker['item_id']) != marker['item_id']):
        raise ValueError('新根完整标记、归属或目录不符。')


def authority(value):
    driver, auth = value['driver'], value['authorization']
    if not isinstance(driver, dict) or not isinstance(auth, dict):
        raise ValueError('新根记录须含完整原责任与授权。')
    for record in (driver, auth):
        if (record.get('item_id') != value['marker']['item_id'] or
                record.get('plan_id') != value['root']['id'] or
                record.get('plan_version') != value['root']['version']):
            raise ValueError('新根记录的原责任或授权归属不符。')
        for key in ('id', 'revision'):
            text(record.get(key))
    text(driver.get('generation')); text(auth.get('driver_generation'))
    task_revision(driver.get('item_revision'))
    if (driver.get('generation') != auth.get('driver_generation') or
            driver.get('kind') != 'COOPERATIVE' or driver.get('state') != 'RESERVED' or
            auth.get('native_permissions') != 'VENDOR_DEFAULTS' or
            type(auth.get('max_attempts_total')) is not int or
            not 1 <= auth['max_attempts_total'] <= 1000 or
            type(auth.get('already_used')) is not int or auth['already_used'] != 0 or
            type(auth.get('created')) not in (int, float) or not math.isfinite(auth['created']) or
            type(auth.get('dispatch_before')) not in (int, float) or
            not math.isfinite(auth['dispatch_before']) or auth['created'] >= auth['dispatch_before']):
        raise ValueError('新根记录的原累计授权或责任形状不符。')


def _validate(value, protocol):
    if protocol not in FIELDS:
        raise ValueError('新根记录协议未登记。')
    fields(value, FIELDS[protocol] | {'protocol', 'version'})
    if value['protocol'] != protocol or value['version'] != digest({k: v for k, v in value.items() if k != 'version'}):
        raise ValueError('新根记录协议或完整摘要不符。')
    identity(value)
    if protocol in (INTENT, UNIT_INPUT):
        authority(value); text(value['scope'])
    if protocol == INTENT:
        task_revision(value['item_revision']); text(value['source_run'])
        for key in ('target_revision', 'input_template_version'):
            sha(value[key])
        relation = value['relation']
        fields(relation, {'source_unit', 'target_unit', 'paths'})
        text(relation['source_unit']); text(relation['target_unit'])
        observation(value['baseline']); frontier(value['protected_history']); frontier(value['frontier'])
        if (relation['source_unit'] == relation['target_unit'] or
                value['baseline']['root'] != value['marker']['project_root'] or
                relation['paths'] != value['baseline']['paths'] or value['baseline']['missing'] or
                value['frontier'] != value['protected_history'] or
                value['driver'].get('item_revision') != value['item_revision']):
            raise ValueError('新根交付关系、写前完整材料或有序前沿不符。')
        evidence = value['source_evidence']
        if (not isinstance(evidence, dict) or evidence.get('run_id') != value['source_run'] or
                evidence.get('state') != 'COMPLETED' or evidence.get('result_available') is not True or
                evidence.get('exact_identity') is not True or evidence.get('blockers') != []):
            raise ValueError('新根交付前版须为准确完成来源。')
        sha(evidence.get('version'))
        sha(evidence.get('result_version'))
        files = evidence.get('file_artifacts')
        if (not isinstance(files, dict) or files.get('state') != 'CURRENT' or
                files.get('manifest') != value['baseline']['manifest']):
            raise ValueError('新根交付前版须含准确完整文件引用。')
        reference(files.get('reference'))
        source = [r for r in value['frontier'] if r['run_id'] == value['source_run']]
        # _54's frontier contains raw base evidence; source_evidence also has
        # file status and its own combined version. These are distinct hashes.
        if (len(source) != 1 or source[0]['evidence']['version'] != evidence['result_version'] or
                len(value['frontier']) >= value['authorization']['max_attempts_total']):
            raise ValueError('新根交付前版必须包含在原完整前沿中。')
    elif protocol == DELIVERY:
        from .work_coordinator_handoff_successor_intent import SUCCESSOR_INTENT, validate_intent
        from .work_coordinator_handoff_round_intent import PROTOCOL as ROUND_INTENT, validate_intent as round_intent
        from .work_coordinator_handoff_mixed_intent import PROTOCOL as MIXED_INTENT, validate_intent as mixed_intent
        from .work_coordinator_handoff_post_round_intent import PROTOCOL as POST_INTENT, validate_intent as post_intent
        from .work_coordinator_handoff_post_round_v2_intent import PROTOCOL as POST_V2_INTENT, validate_intent as post_v2_intent
        from .work_coordinator_handoff_post_round_v3_continuation_contracts import INTENT as V3_INTENT, validate_intent as v3_intent
        if value['intent'].get('protocol') == V3_INTENT:
            v3_intent(value['intent'])
        elif value['intent'].get('protocol') == POST_V2_INTENT:
            post_v2_intent(value['intent'])
        elif value['intent'].get('protocol') == POST_INTENT:
            post_intent(value['intent'])
        elif value['intent'].get('protocol') == MIXED_INTENT:
            mixed_intent(value['intent'])
        elif value['intent'].get('protocol') == ROUND_INTENT:
            round_intent(value['intent'])
        elif value['intent'].get('protocol') == SUCCESSOR_INTENT:
            validate_intent(value['intent'])
        else:
            validate(value['intent'], INTENT)
        intent = value['intent']
        if (any(value[k] != intent[k] for k in ('item_id', 'root', 'marker')) or
                value['materials'] != intent['baseline']['manifest'] or value['run_id'] == intent['source_run']):
            raise ValueError('新根交付须绑定同根的新后继与准确原材料。')
        manifest(value['materials'])
    elif protocol == COMPLETION:
        reference(value['delivery']); sha(value['files_version'])
        if not value['delivery']['id'].startswith(DELIVERY_KIND + ':'):
            raise ValueError('新根完成只能引用新根交付记录。')
        evidence = value['evidence']
        if (not isinstance(evidence, dict) or evidence.get('run_id') != value['run_id'] or
                evidence.get('state') != 'COMPLETED' or evidence.get('result_available') is not True or
                evidence.get('exact_identity') is not True or evidence.get('blockers') != [] or
                evidence.get('file_artifacts', {}).get('state') != 'CURRENT'):
            raise ValueError('新根完成记录须绑定准确已完成的当前产物。')
        sha(evidence.get('version')); reference(evidence['file_artifacts'].get('reference'))
        manifest(evidence['file_artifacts'].get('manifest'))
    else:
        fields(value['task'], {'id', 'revision'})
        task_revision(value['task']['revision']); sha(value['unit_revision'])
        frontier(value['prior_frontier']); observation(value['material_observation'])
        fields(value['proposal'], {'reference', 'source', 'decision'})
        reference(value['proposal']['reference'])
        source = value['proposal']['source']
        fields(source, {'run_id', 'task_id', 'attempt_id', 'owner', 'session_id', 'turn_id',
                        'connection_fingerprint', 'result_digest', 'version'})
        for key in ('run_id', 'task_id', 'attempt_id', 'owner'):
            text(source[key])
        for key in ('session_id', 'turn_id'):
            if source[key] is not None:
                text(source[key])
        for key in ('connection_fingerprint', 'result_digest', 'version'):
            sha(source[key])
        decision = value['proposal']['decision']
        from .work_proposals import parse
        if (parse(json.dumps(decision)) != decision or decision['action'] != 'dispatch' or
                decision['retry_of'] is not None or value['phase'] not in DISPATCH_PHASES or
                not value['proposal']['reference']['id'].startswith('work-proposal:') or
                any(value[k] is not expected for k, expected in PERMISSIONS.items()) or
                value['task']['id'] != value['marker']['item_id'] or
                value['driver'].get('item_revision') != value['task']['revision'] or
                value['material_observation']['root'] != value['marker']['project_root']):
            raise ValueError('新根普通输入须为同根首次准确提案与无许可的冻结数据。')
    if protocol in (DELIVERY, COMPLETION):
        text(value['run_id'])
        for key in ('run_version', 'input_version'):
            sha(value[key])
        if protocol == DELIVERY:
            sha(value['task_spec_version'])
    return deepcopy(value)


def validate(value, protocol):
    try:
        return _validate(value, protocol)
    except (TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError('新根记录的嵌套字段形状无效。') from exc


def validate_row(kind, row):
    if kind not in KINDS:
        raise ValueError('新根记录kind未登记。')
    fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    value = validate(row['snapshot'], KINDS[kind])
    if (row['id'] != kind + ':' + row['version'] or row['version'] != value['version'] or
            row['item_id'] != value['item_id'] or type(row['created']) not in (int, float) or
            not math.isfinite(row['created']) or row['created'] < 0):
        raise ValueError('新根不可变记录外壳或归属不符。')
    text(row['command_id'])
    return deepcopy(row)


class WorkCoordinatorHandoffRecords:
    """Low-level storage only, like the old ledger's put/get; no handler."""
    def __init__(self, service):
        self.s = service

    @staticmethod
    def reference(row):
        return {k: row[k] for k in ('id', 'version')}

    def get(self, db, kind, ref):
        reference(ref)
        row = validate_row(kind, self.s.collaboration.get(db, kind, ref['id']))
        if self.reference(row) != ref:
            raise ValueError('新根记录须引用准确版本。')
        return row

    def put(self, db, kind, snapshot):
        if kind not in KINDS:
            raise ValueError('新根记录kind未登记。')
        snapshot = validate(snapshot, KINDS[kind])
        row = {'id': kind + ':' + snapshot['version'], 'version': snapshot['version'],
               'snapshot': snapshot, 'item_id': snapshot['item_id'],
               'created': time.time(), 'command_id': self.s.command_identity}
        validate_row(kind, row)
        if kind == COMPLETION_KIND:
            delivery = self.get(db, DELIVERY_KIND, snapshot['delivery'])['snapshot']
            if any(snapshot[k] != delivery[k] for k in
                   ('item_id', 'root', 'marker', 'run_id', 'run_version', 'input_version')):
                raise ValueError('新根完成与准确交付身份不一致。')
        # collaboration has a globally unique id, not a (kind,id) primary key.
        prior = db.execute('SELECT kind,data FROM collaboration WHERE id=?', (row['id'],)).fetchone()
        if prior:
            if prior[0] != kind:
                raise ValueError('新根记录id被其它kind占用，不能覆盖。')
            saved = validate_row(kind, json.loads(prior[1]))
            if saved['snapshot'] != snapshot:
                raise ValueError('新根已有记录不同，不能覆盖。')
            return saved
        self.s.collaboration.put(db, kind, row)
        return deepcopy(row)
