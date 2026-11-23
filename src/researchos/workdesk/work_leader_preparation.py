"""Natural leader preparation using the shared official preparation foundation."""
from pathlib import Path

from .work_fixed_preparation import WorkFixedPreparation
from .work_leader import COORDINATE
from . import workspace
from .teamwork import write_paths


class WorkLeaderPreparation(WorkFixedPreparation):
    start_kind = 'work-item.leader-prepare'
    settle_kind = 'work-item.leader-prepare-check'
    discard_kind = 'work-item.leader-prepare-release'
    resolve_kind = 'work-item.leader-capability-resolve'
    kind = 'work_leader_preparation'
    protocol = 'WORKDESK_LEADER_PLAN_V1'
    driver_kind = 'LEADER_PREPARATION'
    prefix, mode = 'leader', 'leader'

    @staticmethod
    def unit_limit(remaining):
        return max(0, min(19, (remaining - 1) // 2))

    @classmethod
    def prompt(cls, preparation):
        remaining = cls.remaining_units(preparation)
        limit = cls.unit_limit(remaining)
        prompt = super().prompt(preparation)
        prompt = prompt.replace('Workdesk 固定流程', 'Workdesk 负责人合作方案').replace(
            'WORKDESK_FIXED_PLAN_V1', cls.protocol)
        return (f'本次 units 最多 {limit} 个普通单元，包含整合。'
                '在这个上限内合并可在同一轮完成的工作，不要拆成额外的流程或文件。'
                '安排资料检索时，在该单元验收条件中明确需要证据支持的问题、交给下游的证据内容和完成条件。'
                '按任务限制和成员预职责分配普通单元，整合也属于普通单元。'
                '负责人负责准备、协调和交付确认。任务若指定执行成员，由这些成员承担普通单元。\n'
                + prompt + '\n负责人组织附加要求：不要声明coordinate或使用key '
                + COORDINATE + '。恰有一个末尾integrate单元，直接或间接依赖其余全部普通工作。'
                '准备后由负责人根据准确结果逐轮选择下一就绪单元，程序不会按列表顺序自动执行。'
                '规划阶段的剩余授权要覆盖N个普通执行、N次执行前协调及1次末尾交付协调，合计2*N+1次。'
                '这是程序组织预算的计算规则；单元goal、acceptance和checks只描述用户成果，'
                '不要把协调次数、剩余授权下限或程序准入规则写入单元成果要求。'
                '执行阶段已完成的协调不会再次计数，准确剩余次数由每轮输入给出。'
                '文件单元须逐一声明需写入的准确文件，write_paths与outputs相同，便于审阅后原范围文件返工。'
                '程序只会把已覆盖outputs的目录写入声明收窄为这些准确文件，不会扩大原写入范围。'
                '前置单元的已登记产物供后续读取。整合若可能修订前置内容或配套文件，'
                '在整合单元的 outputs 和 write_paths 预先声明独立的最终文件路径，并让最终成品引用这些文件。'
                '需要替换前置单元原产物时，由负责人安排原单元文件返工。')

    def normalize_fields(self, db, item, definition):
        changes = super().normalize_fields(db, item, definition)
        outputs, paths = definition.get('outputs', []), definition.get('write_paths', [])
        if not isinstance(outputs, list) or not isinstance(paths, list):
            raise ValueError('负责人文件产物和写入范围须为准确相对路径数组。')
        names = [workspace.relative(name) for name in outputs]
        root = (Path(db.execute('SELECT root FROM projects WHERE id=?', (item['project_id'],)).fetchone()[0])
                if item['project_id'] else self.s.locations.data / 'unfiled-work')
        allowed = write_paths(paths, root)
        if paths and not names:
            raise ValueError('负责人文件单元须明确声明写入文件的outputs。')
        if any(not any(path == '.' or name.casefold() == path.casefold()
                       or name.casefold().startswith(path.casefold() + '/') for path in allowed)
               for name in names):
            raise ValueError('负责人产物超出原写入范围，不能通过范围细化增加授权。')
        if paths != names:
            changes.append({'field': 'write_paths', 'operation': 'narrow_to_declared_outputs',
                            'from': list(paths), 'to': names})
            definition['write_paths'] = names
        return changes

    def validate_definitions(self, definitions, remaining, *, pdca=None):
        if not 1 <= len(definitions) <= self.unit_limit(remaining):
            raise ValueError('负责人剩余授权须覆盖普通单元、每次协调及最终交付协调。')
        if any(d.get('role') == 'coordinate' or d.get('key') == COORDINATE for d in definitions):
            raise ValueError('负责人协调模板由程序登记。')
        keys = [d.get('key') for d in definitions]
        if (any(not isinstance(k, str) or not k for k in keys) or len(set(keys)) != len(keys)
                or any(not isinstance(d.get('depends', []), list)
                       or any(not isinstance(k, str) for k in d.get('depends', [])) for d in definitions)):
            raise ValueError('负责人单元身份或依赖引用无效。')
        if definitions[-1].get('role') != 'integrate' or sum(d.get('role') == 'integrate' for d in definitions) != 1:
            raise ValueError('负责人方案须有唯一末尾整合单元。')
        dependencies = {d['key']: set(d.get('depends', [])) for d in definitions}
        reached = set(dependencies[definitions[-1]['key']])
        for _ in definitions:
            reached |= {p for k in list(reached) for p in dependencies.get(k, ())}
        if reached != set(dependencies) - {definitions[-1]['key']}:
            raise ValueError('负责人整合单元须直接或间接依赖全部普通工作。')

    def make_plan(self, db, scope, row, definitions):
        coordinator_id = (row.get('pdca') or {}).get('coordinator', {}).get('member_id', row['planner_id'])
        coordinator = row['members'][coordinator_id]['reference']
        template = {'key': COORDINATE, 'title': '负责人协调', 'role': 'coordinate', 'member': coordinator,
                    'goal': '阅读当前准确成果，决定下一分工、暂停或核对整合交付。',
                    'acceptance': '按冻结负责人决策协议返回一个严格JSON对象。',
                    'depends': [], 'write_paths': [], 'outputs': [], 'checks': [], 'dependency_gate': 'result'}
        return self.s.teamwork.save(db, {
            **scope, 'current_plan': (row.get('continuation') or {}).get('prior_plan'),
            'mode': 'leader', 'max_parallel': 1, 'access': row.get('access', 'workspace'),
            'coordinator': coordinator, 'integrator': definitions[-1]['member'],
            'units': [template, *definitions],
        }, daily=True, pdca=row.get('pdca'))['teamwork']['plan']

    def after_plan(self, db, plan, row, evidence):
        self.s.leader.register(db, plan, row, evidence)
        self.outcome(db, row, 'APPLIED', plan={k: plan[k] for k in ('id', 'version')}, evidence=evidence)
