"""Strict serial file-handoff configuration shape. No persistence or execution grant.

Callers supply normalized plan units. File existence, original/current bytes,
run history, authorization and dispatch must be checked separately at runtime.
"""
import re

from . import workspace
from .plans import PlanCommands
from .teamwork import overlaps


def file_paths(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise ValueError('交接路径须为1至20项明确文件。')
    for name in value:
        if workspace.relative(name) != name:
            raise ValueError('交接路径不能含别名。')
        if any(re.search(r'[\x00-\x1f<>:"|?*]', part) or
               re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I)
               for part in name.split('/')):
            raise ValueError('交接路径须为明确文件，不能含通配符或保留名。')
    if len({n.casefold() for n in value}) != len(value):
        raise ValueError('交接文件路径重复。')
    if any(overlaps([name], [other]) for i, name in enumerate(value) for other in value[i + 1:]):
        raise ValueError('交接文件集合不能包含祖先路径冲突。')
    return sorted(value)


def relation_problems(source, target, paths, units, max_parallel, *, require_target_outputs=True):
    """Report static blockers; preview may display target outputs as required configuration."""
    paths = file_paths(paths)
    problems = []
    if source['role'] == 'coordinate' or source['key'] == target['key']:
        problems.append('须选择不同的普通前版与后继整合单元')
    if target['role'] != 'integrate':
        problems.append('后继尚未明确 integrate 整合职责，须由 Human 配置')
    if source['key'] not in target['depends']:
        problems.append('后继必须直接依赖准确前版单元')
    if set(source['outputs']) != set(paths):
        problems.append('交接须包含准确前版的全部 outputs 文件集合')
    if set(target['write_paths']) != set(paths) or set(source['write_paths']) != set(paths):
        problems.append('原写入范围须与完整交接文件集合逐项相同；宽目录和仅写入范围不支持，须由 Human 配置')
    if target['dependency_gate'] not in ('files', 'checked'):
        problems.append('后继须明确文件依赖门槛，须由 Human 配置')
    if target['dependency_gate'] == 'checked' and not source['checks']:
        problems.append('检查依赖要求前版预先声明本地检查')
    if require_target_outputs and set(target['outputs']) != set(paths):
        problems.append('后继 outputs 须与完整交接文件集合逐项相同，须由 Human 明确配置')
    if type(max_parallel) is not int or max_parallel != 1:
        problems.append('首批须显式配置串行方案 max_parallel=1')
    for other in units:
        if other['key'] not in (source['key'], target['key']) and overlaps(paths, other['outputs'] + other['write_paths']):
            problems.append('其它单元存在同路径或祖先路径写入冲突：' + other['key'])
    return problems


def definition(value, units, max_parallel):
    """Validate static shape; callers separately guard history and execution."""
    if not isinstance(value, list) or len(value) > 1:
        raise ValueError('首批同路径配置只支持至多一项明确交接关系。')
    if not value:
        return []
    entry = value[0]
    if not isinstance(entry, dict) or set(entry) != {'source_unit', 'target_unit', 'paths'}:
        raise ValueError('交接关系只接受 source_unit、target_unit 和 paths。')
    for name in ('source_unit', 'target_unit'):
        if not isinstance(entry[name], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', entry[name]):
            raise ValueError('交接单元身份无效。')
    keys = [u['key'] for u in units]
    if len(set(keys)) != len(keys):
        raise ValueError('交接方案单元身份重复。')
    by_key = {u['key']: u for u in units}
    if entry['source_unit'] not in by_key or entry['target_unit'] not in by_key:
        raise ValueError('交接端点须在同一明确方案内。')
    PlanCommands.validate_graph({u['key']: u['depends'] for u in units})
    paths = file_paths(entry['paths'])
    problems = relation_problems(by_key[entry['source_unit']], by_key[entry['target_unit']], paths, units, max_parallel)
    if problems:
        raise ValueError('；'.join(problems))
    return [{'source_unit': entry['source_unit'], 'target_unit': entry['target_unit'], 'paths': paths}]
