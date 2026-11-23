"""Closed first-B intent for an actual saved path; no dispatch authority.

The root remains the immutable A/B ledger identity. Authority belongs to the
real successor plan. Neither identity is projected into the original codec.
"""
from copy import deepcopy
import json

from .store import digest
from .work_coordinator_handoff_records import (
    INTENT, FIELDS, fields, identity, reference, text, sha, task_revision,
    observation, frontier)
from .work_coordinator_handoff_adjustment_contracts import (
    closed, successor_driver, transferred_authorization)
from .work_coordinator_handoff_adjustment_records import validate_path


SUCCESSOR_INTENT = 'COORDINATOR_FILE_HANDOFF_SAVED_FIRST_B_INTENT_V1'
SCOPE = 'EXACT_SAVED_FIRST_B_INTENT; PROTECTED_ALL_LAYERS'


@closed
def validate_intent(value):
    fields(value, FIELDS[INTENT] | {'protocol', 'version', 'plan', 'saved_path'})
    identity(value); reference(value['plan'])
    path = validate_path(value['saved_path'])
    driver, auth = value['driver'], value['authorization']
    successor_driver(driver); transferred_authorization(auth)
    if (value['protocol'] != SUCCESSOR_INTENT or value['scope'] != SCOPE or
            value['version'] != digest({k:v for k,v in value.items() if k != 'version'}) or
            path['root'] != value['root'] or path['plan'] != value['plan'] or
            value['plan'] == value['root'] or path['problems'] or
            path['history_frontier'] != value['protected_history'] or
            path['material_observation'] != value['baseline'] or
            any(r['item_id'] != value['item_id'] or r['plan_id'] != value['plan']['id'] or
                r['plan_version'] != value['plan']['version'] for r in (driver, auth)) or
            driver['state'] != 'RESERVED' or driver['generation'] != auth['driver_generation'] or
            driver['transition_proposal'] != auth['transfer_proposal'] or
            driver['previous_plan_reference'] != path['edges'][-1]['parent_plan']):
        raise ValueError('后继首次B意图须绑定真实保存路径及当前转移责任授权。')
    task_revision(value['item_revision']); text(value['source_run'])
    for key in ('target_revision', 'input_template_version'): sha(value[key])
    relation = value['relation']
    fields(relation, {'source_unit', 'target_unit', 'paths'})
    text(relation['source_unit']); text(relation['target_unit'])
    observation(value['baseline']); frontier(value['protected_history']); frontier(value['frontier'])
    if (relation['source_unit'] == relation['target_unit'] or
            value['baseline']['root'] != value['marker']['project_root'] or
            relation['paths'] != value['baseline']['paths'] or value['baseline']['missing'] or
            value['frontier'] != value['protected_history'] or
            driver['item_revision'] != value['item_revision']):
        raise ValueError('后继首次B关系、完整基线或全部历史前沿不符。')
    evidence = value['source_evidence']
    if (not isinstance(evidence, dict) or evidence.get('run_id') != value['source_run'] or
            evidence.get('state') != 'COMPLETED' or evidence.get('result_available') is not True or
            evidence.get('exact_identity') is not True or evidence.get('blockers') != []):
        raise ValueError('后继首次B须使用准确完成A。')
    sha(evidence.get('version')); sha(evidence.get('result_version'))
    files = evidence.get('file_artifacts')
    if (not isinstance(files, dict) or files.get('state') != 'CURRENT' or
            files.get('manifest') != value['baseline']['manifest']):
        raise ValueError('后继首次B须使用准确A全部当前文件。')
    reference(files.get('reference'))
    source = [r for r in value['frontier'] if r['run_id'] == value['source_run']]
    if (len(source) != 1 or source[0]['evidence']['version'] != evidence['result_version'] or
            len(value['frontier']) >= auth['max_attempts_total']):
        raise ValueError('后继首次B意图缺少准确A完整历史或原累计余量。')
    return deepcopy(value)


@closed
def require_assets(db, value):
    """Resolve the saved reference graph for backup; no runtime/event proof."""
    validate_intent(value)
    from .work_coordinator_handoff_adjustment_records import (
        WorkCoordinatorHandoffAdjustmentRecords, CHECKPOINT, CANDIDATE,
        APPLICATION, CONSUMPTION, KIND_FOR, validate_row)
    from .work_coordinator_handoff_adjustment_contracts import successor_plan
    from .work_coordinator_handoff_roots import MARKER_FIELD, PLAN_FIELDS

    def get(kind, ref):
        row = db.execute('SELECT kind,data FROM collaboration WHERE id=?',(ref['id'],)).fetchone()
        if not row or row[0] != kind:
            raise ValueError('后继首次B资产缺少准确保存路径引用。')
        raw = json.loads(row[1])
        if kind == 'teamwork_plan':
            if raw.get('coordinator_file_handoff_lineage'): successor_plan(raw)
            else: fields(raw,PLAN_FIELDS)
            if raw['version'] != digest({k:v for k,v in raw.items() if k!='version'}):
                raise ValueError('后继首次B资产方案完整摘要不符。')
        else: validate_row(kind,raw)
        if WorkCoordinatorHandoffAdjustmentRecords.ref(raw) != ref:
            raise ValueError('后继首次B资产保存路径版本不符。')
        return raw

    path=value['saved_path'];root=get('teamwork_plan',value['root'])
    if root[MARKER_FIELD] != value['marker']:
        raise ValueError('后继首次B资产原根标记不符。')
    for edge in path['edges']:
        parent=get('teamwork_plan',edge['parent_plan']);child=get('teamwork_plan',edge['child_plan'])
        checkpoint=get(KIND_FOR[CHECKPOINT],edge['checkpoint'])['snapshot']
        candidate=get(KIND_FOR[CANDIDATE],edge['candidate'])['snapshot']
        application=get(KIND_FOR[APPLICATION],edge['application'])['snapshot']
        consumption=get(KIND_FOR[CONSUMPTION],edge['consumption'])['snapshot']
        if (any(r['root']!=path['root'] or r['parent_plan']!=edge['parent_plan'] or
                r['capability']!=path['capability'] or r['item_id']!=value['item_id']
                for r in (checkpoint,candidate,application,consumption)) or
                checkpoint['plan']!=parent or child[MARKER_FIELD]!=value['marker'] or
                child['coordinator_file_handoff_lineage']['candidate']!=edge['candidate'] or
                child['previous_plan_reference']!=edge['parent_plan'] or
                candidate['checkpoint']!=edge['checkpoint'] or
                application['candidate']!=edge['candidate'] or application['checkpoint']!=edge['checkpoint'] or
                application['child_plan']!=edge['child_plan'] or application['save_event']!=edge['save_event'] or
                consumption['application']!=edge['application'] or consumption['from_plan']!=edge['parent_plan'] or
                consumption['to_plan']!=edge['child_plan'] or
                any(r['proposal']!=candidate['proposal'] for r in (checkpoint,application,consumption))):
            raise ValueError('后继首次B资产保存路径不等于准确原记录图。')
    if application['authorization_after']!={k:value['authorization'][k] for k in ('id','revision')}:
        raise ValueError('后继首次B资产转移授权引用不符。')
