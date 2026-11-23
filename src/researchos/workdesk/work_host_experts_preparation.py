"""Host preparation: consultation precedes work, on the shared native foundation."""
from .work_leader_preparation import WorkLeaderPreparation
from .work_host_experts import COORDINATE


class WorkHostExpertsPreparation(WorkLeaderPreparation):
    start_kind = 'work-item.host_experts-prepare'
    settle_kind = 'work-item.host_experts-prepare-check'
    discard_kind = 'work-item.host_experts-prepare-release'
    resolve_kind = 'work-item.host_experts-capability-resolve'
    kind = 'work_host_experts_preparation'
    protocol = 'WORKDESK_HOST_EXPERTS_PLAN_V1'
    driver_kind = 'HOST_EXPERTS_PREPARATION'
    prefix, mode = 'host_experts', 'host_experts'

    @classmethod
    def prompt(cls, preparation):
        prompt = super().prompt(preparation).replace('负责人', '主持人')
        if (preparation.get('pdca') or {}).get('confirmed_discussion'):
            return prompt + '\n预讨论已经收束。按确认后的任务要求安排 PDCA，本次首轮 P 负责制定执行计划。'
        return (prompt + '\n本次是主持人—专家共同讨论模式。初始安排是待专家讨论的草案。'
                '先从授权成员中选择适合任务的讨论专家，至少安排一个 role=plan 的专家讨论单元。'
                '专家参与判断方案、参与者、分工与证据，反馈异议、不可行之处、协助请求或关键Human取舍。'
                '主持人自身可以担任讨论专家或执行成员。讨论人数按任务与剩余授权选择。'
                '所有plan单元为无文件的讨论职责；其余普通单元必须直接或间接依赖全部讨论单元，'
                '让专家意见先交给主持人收束；主持人可依据讨论用准确修订调整成员职责和依赖。'
                '直接等待无文件专家时用 result。files 或 checked 的 depends 只列有相应产物的普通前置单元；'
                '只要普通前置单元已经等待专家，整合不必再直接列出该专家。'
                '专家和主持人共用原授权与截止。不要声明coordinate或使用key ' + COORDINATE + '。')

    def normalize_definitions(self, definitions, *, pdca=None):
        if (pdca or {}).get('confirmed_discussion'):
            return []
        # Preparation has validated the plan; revisions validate their exact inputs separately.
        discussions = {d['key'] for d in definitions if d.get('role') == 'plan'}
        dependencies = {d['key']: set(d.get('depends', [])) for d in definitions}
        normalizations = []
        for unit in definitions:
            key = unit['key']
            direct = dependencies[key]
            if key in discussions or not direct & discussions or unit.get('dependency_gate') not in ('files', 'checked'):
                continue
            reached = direct - discussions
            for _ in definitions:
                reached |= {p for k in list(reached) for p in dependencies.get(k, ())}
            if not discussions <= reached:
                raise ValueError('文件依赖须通过普通前置单元等待无文件专家。')
            original = unit['depends']
            unit['depends'] = [parent for parent in original if parent not in discussions]
            dependencies[key] = set(unit['depends'])
            normalizations.append({'unit_key': key, 'field': 'depends',
                                   'operation': 'reuse_existing_expert_dependency',
                                   'from': original, 'to': unit['depends']})
        return normalizations

    def validate_definitions(self, definitions, remaining, *, pdca=None):
        super().validate_definitions(definitions, remaining, pdca=pdca)
        if any(d.get('key') == COORDINATE for d in definitions):
            raise ValueError('主持人协调身份由程序保留。')
        if (pdca or {}).get('confirmed_discussion'):
            return
        discussions = {d['key'] for d in definitions if d.get('role') == 'plan'}
        if not discussions:
            raise ValueError('主持人方案须先安排至少一个专家讨论单元。')
        dependencies = {d['key']: set(d.get('depends', [])) for d in definitions}
        for unit in definitions:
            if unit['key'] in discussions:
                if unit.get('outputs') or unit.get('write_paths') or unit.get('checks'):
                    raise ValueError('专家讨论单元只提交讨论意见，不写入交付文件。')
                if dependencies[unit['key']] - discussions:
                    raise ValueError('专家讨论不能依赖后续执行成果。')
                continue
            reached = set(dependencies[unit['key']])
            for _ in definitions:
                reached |= {p for k in list(reached) for p in dependencies.get(k, ())}
            if not discussions <= reached:
                raise ValueError('主持人普通执行须等待全部专家讨论并由主持人收束。')

    def make_plan(self, db, scope, row, definitions):
        coordinator_id = (row.get('pdca') or {}).get('coordinator', {}).get('member_id', row['planner_id'])
        coordinator = row['members'][coordinator_id]['reference']
        template = {'key': COORDINATE, 'title': '主持人组织讨论与收束', 'role': 'coordinate',
                    'member': coordinator, 'goal': '组织专家共同判断方案、回应异议和协助请求，收束分工并核对最终交付。',
                    'acceptance': '按冻结主持人决策协议引用准确专家讨论证据。',
                    'depends': [], 'write_paths': [], 'outputs': [], 'checks': [], 'dependency_gate': 'result'}
        return self.s.teamwork.save(db, {
            **scope, 'current_plan': (row.get('continuation') or {}).get('prior_plan'),
            'mode': self.mode, 'max_parallel': 1, 'access': row.get('access', 'workspace'),
            'coordinator': coordinator, 'integrator': definitions[-1]['member'],
            'units': [template, *definitions],
        }, daily=True, pdca=row.get('pdca'))['teamwork']['plan']

    def after_plan(self, db, plan, row, evidence):
        self.s.host_experts.register(db, plan, row, evidence)
        self.outcome(db, row, 'APPLIED', plan={k: plan[k] for k in ('id', 'version')}, evidence=evidence)
