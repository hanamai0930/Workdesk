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
from .work_coordinator_handoff_mixed_contracts import (
    closed, successor_driver, transferred_authorization)
from .work_coordinator_handoff_post_round_contracts import validate_path


PROTOCOL = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_FIRST_B_INTENT_V1'
SCOPE = 'EXACT_POST_ROUND_B_INTENT; PROTECTED_ALL_PRIOR_RUNS'


@closed
def validate_intent(value):
    fields(value, FIELDS[INTENT] | {'protocol', 'version', 'plan', 'saved_path'})
    identity(value); reference(value['plan'])
    path = validate_path(value['saved_path'])
    driver, auth = value['driver'], value['authorization']
    if path['round_protocol'] == 'COORDINATOR_FILE_HANDOFF_ROUND_RUNTIME_PATH_V1':
        from .work_coordinator_handoff_round_contracts import successor_driver as first_driver, transferred_authorization as first_auth
        first_driver(driver); first_auth(auth)
    else:
        successor_driver(driver); transferred_authorization(auth)
    if (value['protocol'] != PROTOCOL or value['scope'] != SCOPE or
            value['version'] != digest({k:v for k,v in value.items() if k != 'version'}) or
            path['root'] != value['root'] or path['plan'] != value['plan'] or
            path['driver'] != driver or path['authorization'] != auth or
            value['plan'] == value['root'] or
            path['history_frontier'] != value['protected_history'] or
            path['material_observation'] != value['baseline'] or
            any(r['item_id'] != value['item_id'] or r['plan_id'] != value['plan']['id'] or
                r['plan_version'] != value['plan']['version'] for r in (driver, auth)) or
            driver['state'] != 'RESERVED' or driver['generation'] != auth['driver_generation'] or
            driver['transition_registration'] != auth['transfer_registration'] or
            driver['previous_plan_reference'] != path['edge']['parent_plan']):
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
