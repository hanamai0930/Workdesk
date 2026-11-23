"""V2 first-B delivery intent; exact references, without dispatch authority.

The complete path/frontier live once in the frozen ordinary input. A structural
intent does not prove their existence. Runtime must rebuild both from actual
events and compare the entire frozen input, including this intent.
"""
from copy import deepcopy

from .store import digest
from .work_coordinator_handoff_records import (
    INTENT, FIELDS, fields, identity, reference, text, sha, task_revision, observation)
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_v2_saved_records import successor_driver, transferred_authorization
from .work_coordinator_handoff_post_round_v2_run_contracts import (
    PATH, validate_path, observation_ref, frontier_reference)

PROTOCOL = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_FIRST_B_INTENT_V2'
SCOPE = 'EXACT_ORDERED_V2_B_INTENT; PROTECTED_ALL_PRIOR_RUNS'


@closed
def validate_intent(value, path=None):
    fields(value, (FIELDS[INTENT] - {'protected_history', 'frontier'}) |
        {'protocol', 'version', 'plan', 'saved_path_reference', 'prior_frontier_reference', 'phase'})
    identity(value); reference(value['plan'])
    driver = successor_driver(value['driver']); auth = transferred_authorization(value['authorization'])
    ref = value['saved_path_reference']
    fields(ref, {'protocol', 'version', 'root', 'plan'})
    sha(ref['version']); reference(ref['root']); reference(ref['plan'])
    if (value['protocol'] != PROTOCOL or value['scope'] != SCOPE or
            value['version'] != digest({k: v for k, v in value.items() if k != 'version'}) or
            ref['protocol'] != PATH or ref['root'] != value['root'] or ref['plan'] != value['plan'] or
            value['prior_frontier_reference'] != {**ref, 'field': 'history_frontier'} or
            value['phase'] != 'A_CAPTURED' or value['plan'] == value['root'] or
            any(r['item_id'] != value['item_id'] or r['plan_id'] != value['plan']['id'] or
                r['plan_version'] != value['plan']['version'] for r in (driver, auth)) or
            driver['state'] != 'RESERVED' or driver['generation'] != auth['driver_generation'] or
            driver['transition_registration'] != auth['transfer_registration'] or
            driver['item_revision'] != value['item_revision']):
        raise ValueError('V2首次B意图须绑定准确有序路径引用、阶段与转移责任授权。')
    task_revision(value['item_revision']); text(value['source_run'])
    for key in ('target_revision', 'input_template_version'): sha(value[key])
    relation = value['relation']; fields(relation, {'source_unit', 'target_unit', 'paths'})
    text(relation['source_unit']); text(relation['target_unit']); observation(value['baseline'])
    if (relation['source_unit'] == relation['target_unit'] or
            value['baseline']['root'] != value['marker']['project_root'] or
            relation['paths'] != value['baseline']['paths'] or value['baseline']['missing']):
        raise ValueError('V2首次B须保留原关系及全部准确当前材料。')
    evidence = value['source_evidence']
    if (not isinstance(evidence, dict) or evidence.get('run_id') != value['source_run'] or
            evidence.get('state') != 'COMPLETED' or evidence.get('result_available') is not True or
            evidence.get('exact_identity') is not True or evidence.get('blockers') != []):
        raise ValueError('V2首次B须使用准确已完成A。')
    sha(evidence.get('version')); sha(evidence.get('result_version'))
    files = evidence.get('file_artifacts')
    if (not isinstance(files, dict) or files.get('state') != 'CURRENT' or
            files.get('manifest') != value['baseline']['manifest']):
        raise ValueError('V2首次B须使用准确A全部当前文件。')
    reference(files.get('reference'))
    if path is not None:
        path = validate_path(path)
        source = [f for f in path['history_frontier'] if f['run_id'] == value['source_run']]
        if (ref != observation_ref(path) or value['prior_frontier_reference'] != frontier_reference(path) or
                any(value[k] != path[k] for k in ('root', 'plan', 'driver', 'authorization')) or
                value['baseline'] != path['material_observation'] or
                driver['previous_plan_reference'] != path['ancestors'][-1]['edge']['parent_plan'] or
                len(source) != 1 or source[0]['evidence']['version'] != evidence['result_version'] or
                len(path['history_frontier']) >= auth['max_attempts_total']):
            raise ValueError('V2首次B意图与完整有序路径、准确A前沿或原累计余量不符。')
    return deepcopy(value)
