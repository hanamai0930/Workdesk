"""Composer setup and immutable stage assignments for a cooperative run."""
import time

from . import attachments
from .contracts import bounded_text
from .store import canonical
from .workbench import scene, scratch


START = 'work-item.pdca-start'
STAGES = ('plan', 'do', 'check', 'act')
LABELS = dict(zip(STAGES, ('计划', '执行', '检查', '改进')))


def summary(configuration):
    stages = configuration['stages']
    return {
        'rounds': configuration['rounds'],
        'stages': {stage: {'member_id': member['reference']['member_id'], 'name': member['name'],
                           'provider': member['provider'], 'model': member['model']}
                   for stage, member in stages.items()},
        'coordinator': configuration['coordinator'],
    }


def member(service, db, ref):
    if isinstance(ref, dict) and ('provider' in ref or 'model' in ref):
        ref = service.resources.pdca_model(db, ref)
    return service.teamwork.member(db, ref)


def setup(service, db, payload, *, separated=False):
    if set(payload) - {'project_id', 'goal', 'acceptance', 'mode', 'stages', 'coordinator',
                       'rounds', 'files', 'attachments', 'file_selection', 'work_scene',
                       'composer_capabilities', 'plan_only'}:
        raise ValueError('合作任务的输入字段不支持。')
    preparation = {'fixed': service.fixed_preparation, 'leader': service.leader_preparation,
                   'host_experts': service.host_preparation}
    mode = payload.get('mode')
    if not isinstance(mode, str) or mode not in preparation:
        raise ValueError('请选择合作方式。')
    rounds = payload.get('rounds')
    if type(rounds) is not int or not 1 <= rounds <= 4:
        raise ValueError('PDCA 执行轮数须为 1 至 4。')
    refs = payload.get('stages')
    if not isinstance(refs, dict) or set(refs) != set(STAGES):
        raise ValueError('请选择计划、执行、检查和改进使用的模型。')
    members = {stage: member(service, db, refs[stage]) for stage in STAGES}
    coordinator = member(service, db, payload['coordinator']) if 'coordinator' in payload else None
    planner = members['plan'] if mode == 'fixed' or separated else coordinator
    if planner is None:
        raise ValueError('请为负责人或主持人选择模型。')
    if mode == 'fixed' and coordinator and coordinator['reference'] != planner['reference']:
        raise ValueError('固定流程使用计划阶段所选模型准备方案。')
    source_scene = payload.get('work_scene')
    if source_scene is not None and not isinstance(source_scene, str):
        raise ValueError('所选工作场景无效。')
    files = payload.get('files', [])
    if not isinstance(files, list) or not all(isinstance(path, str) for path in files):
        raise ValueError('材料文件列表无效。')
    selected = payload.get('attachments', [])
    attachments.normalize(selected)
    if mode != 'fixed' and coordinator is None:
        raise ValueError('请为负责人或主持人选择模型。')
    lead = coordinator or planner
    configuration = {'rounds': rounds, 'stages': members,
                     'coordinator': {'member_id': lead['reference']['member_id'],
                                     **{key: lead[key] for key in ('name', 'provider', 'model')}},
                     'work_scene': scene(source_scene), 'files': files, 'attachments': selected}
    if separated:
        configuration['confirmed_discussion'] = True
    if payload.get('composer_capabilities'):
        configuration['composer_capabilities'] = payload['composer_capabilities']
    if type(payload.get('plan_only', False)) is not bool:
        raise ValueError('计划模式设置无效。')
    if payload.get('plan_only'):
        configuration['plan_only'] = True
    goal = bounded_text(payload.get('goal'), '任务目标', 16000)
    project = service.work_items.project(db, payload.get('project_id'))
    expected = None
    if 'file_selection' in payload:
        reference = payload['file_selection']
        if not isinstance(reference, dict) or set(reference) != {'id', 'revision'}:
            raise ValueError('材料选择版本无效。')
        chosen, expected = service.file_inspector.checked(db, reference)
        if chosen['project_id'] != project:
            raise ValueError('所选材料属于另一项目。')
        configuration['file_selection'] = reference
    refs = {row['reference']['member_id']: row['reference'] for row in (*members.values(), lead)}
    return configuration, preparation[mode], planner, list(refs.values()), project, goal, expected


def new_item(service, db, payload, project, goal):
    return service.work_items.create(db, {
        'title': goal.splitlines()[0][:100], 'goal': goal, 'project_id': project,
        'acceptance': payload.get('acceptance', ''),
    })['work_item']


def start(service, db, payload, *, item=None, authorization=None, material_manifest=None, continuation=None):
    configuration, preparation, planner, refs, project, goal, expected = setup(
        service, db, payload, separated=authorization is not None and payload['mode'] != 'fixed')
    if item is None:
        project = project if project is not None else scratch(service, db, {})['project_id']
        item = new_item(service, db, payload, project, goal)
    if material_manifest is not None:
        expected = material_manifest
    if continuation is not None:
        if (not isinstance(continuation, dict) or set(continuation) != {'prior_plan', 'round_offset'}
                or type(continuation['round_offset']) is not int or continuation['round_offset'] < 1
                or not isinstance(material_manifest, dict) or item['project_id'] != project
                or configuration.get('plan_only')):
            raise ValueError('追加 PDCA 须绑定原任务、已交付方案和准确材料。')
        configuration['round_offset'] = continuation['round_offset']
        attached = {row['file'] for row in configuration['attachments']}
        configuration['files'] = [name for name in material_manifest if name not in attached]
    rounds = configuration['rounds']
    launch = preparation.start(db, {
        'id': item['id'], 'revision': item['revision'], 'planner': planner['reference'],
        'members': refs,
        **(authorization or {'max_attempts_total': 1 + (4 * rounds if payload['mode'] == 'fixed' else 8 * rounds + 1),
                             'dispatch_before': time.time() + 86400, 'access': 'workspace'}),
    }, pdca=configuration, expected_materials=expected, continuation=continuation)
    return {**launch, 'pdca': summary(configuration)}


def structure(configuration):
    rounds = configuration['rounds']
    first = configuration.get('round_offset', 0) + 1
    units = []
    for number in range(first, first + rounds):
        for stage in STAGES:
            role = {'plan': 'plan' if number == first else 'execute', 'do': 'execute',
                    'check': 'review', 'act': 'integrate' if number == first + rounds - 1 else 'execute'}[stage]
            units.append({
                'key': f'pdca_{stage}_{number}', 'title': f'第 {number} 轮 · {LABELS[stage]}',
                'role': role, 'member_id': configuration['stages'][stage]['reference']['member_id'],
                'depends': [units[-1]['key']] if units else [],
            })
    return units


def apply_structure(configuration, definitions):
    """Require the requested graph and member identities before saving the plan."""
    expected = structure(configuration)
    if len(definitions) != len(expected):
        raise ValueError('准备方案没有覆盖选定的全部 PDCA 阶段和轮次。')
    allowed = set()
    original = {name.casefold() for name in configuration.get('manifest', {})}
    for definition, stage in zip(definitions, expected):
        if (definition.get('key') != stage['key'] or definition.get('role') != stage['role']
                or definition['member']['member_id'] != stage['member_id']):
            raise ValueError('准备方案的 PDCA 阶段或成员与所选设置不符。')
        depends = definition.get('depends', [])
        if (not isinstance(depends, list) or not all(isinstance(key, str) for key in depends)
                or not set(stage['depends']) <= set(depends) <= allowed):
            raise ValueError('PDCA 阶段须按顺序接收前一阶段的成果。')
        outputs = definition.get('outputs', [])
        if isinstance(outputs, list) and any(isinstance(name, str) and name.casefold() in original for name in outputs):
            raise ValueError('PDCA 交付文件须使用新路径，以便后续阶段继续读取原材料。')
        definition['title'] = stage['title']
        allowed.add(stage['key'])


def delivery_prompt(frozen, root):
    unit = frozen['unit']
    outputs = '、'.join(unit['outputs'])
    inputs = [f"{source['unit_key']}：{'、'.join(source['manifest'])}"
              for source in frozen['file_artifacts'] if source['manifest']]
    lines = [
        f"【{unit['title']}的文件交付】",
        f"工作目录：{root}",
        f"本阶段必须实际写入并重新读取：{outputs}",
        f"允许写入：{'、'.join(unit['write_paths'])}",
        f"阶段目标：{unit['goal']}",
        f"验收条件：{unit['acceptance']}",
    ]
    if inputs:
        lines.append('已登记的只读前置产物：' + '；'.join(inputs))
    if unit['key'].startswith('pdca_act_'):
        lines.append('以同轮执行稿为底稿，按检查结论逐项修订并另存。若检查未要求修改，仍须按声明路径保存独立版本，说明采用原内容的依据。')
    if unit['checks']:
        lines.append('保存后逐项实测这些检查：' + canonical(unit['checks']))
        if any(check['kind'] == 'utf8_section_length' for check in unit['checks']):
            lines.append('边界行只包围实际正文。han_digits 统计汉字和十进制数字，unicode_alnum 统计字母和数字。')
    lines.extend([
        '先用工作端的原生编辑工具创建上述文件。编辑工具不可用或失败时，在当前 shell 内直接写入，文本以 UTF-8 保存。',
        '文本较长时可分段写入，避免多层 shell 与嵌套引号。资料性文稿就近标明来源。图文产物按验收要求实际渲染核对。',
        '空命令、退出码为 0 或文字确认都不能证明文件已保存。空命令或写入无效时，在当前回合改用有效的写入方式。',
        '先重读每个产物，核对路径、文件内容与验收，再作最终回复。遇到权限、材料或工具阻塞时，说明具体错误和仍缺哪些文件。',
        '本段列出的交付是当前阶段的任务。第 0 轮、前置回合及场景预设中的通用说明按其原范围理解。',
    ])
    return '\n'.join(lines) + '\n\n'
