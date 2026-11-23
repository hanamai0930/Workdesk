"""Evidence-bound host/expert discussion over native member execution."""
import json
import time

from .contracts import bounded_text
from .store import canonical, digest, uid
from .work_leader import WorkLeader, unique_fields, organization

PROTOCOL = 'WORKDESK_HOST_EXPERTS_DAILY_DECISION_V1'
EXPERT_PROTOCOL = 'WORKDESK_EXPERT_DISCUSSION_V1'
COORDINATE = 'host_coordinate'


class WorkHostExperts(WorkLeader):
    coordinate = COORDINATE
    prefix, kind = 'host-experts', 'work_host_experts_organization'
    decision_protocol = PROTOCOL

    def handlers(self):
        return {'work-item.host_experts-human-response': self.human_response}

    def host_request(self, db, plan):
        proposal = self.s.work_proposals.current(db, plan['item_id'])
        snapshot = proposal['snapshot'] if proposal else {}
        decision = snapshot.get('decision') or {}
        return (self.s.work_proposals.reference(proposal) if snapshot.get('protocol') == PROTOCOL
                and snapshot['plan'] == {k:plan[k] for k in ('id', 'version')}
                and decision.get('action') == 'pause' and decision.get('discussion', {}).get('requires_human') else None)

    def response(self, db, item, plan, sources, required_sources, host_request):
        driver = self.s.work_drivers.current(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        rows = self.s.collaboration.all(db, 'work_host_experts_human_response')
        return next((r for r in rows if r['item_id'] == item['id']
                     and r['plan'] == {k: plan[k] for k in ('id', 'version')}
                     and r['driver_generation'] == driver['generation']
                     and r['authorization'] == self.s.work_units.auth_ref(auth)
                     and all(ref in sources for ref in r['sources']) and all(ref in r['sources'] for ref in required_sources)
                     and (not host_request or r.get('host_request') == host_request)
                     and digest({k:v for k,v in r.items() if k != 'version'}) == r['version']
                     and self.response_lineage(db, r, item, driver, plan)), None)

    def response_lineage(self, db, response, item, driver, plan):
        """Only owned dispatches may advance the revision of an answered issue."""
        revision = response['item_revision']
        links = {link['from']: link for link in driver.get('revision_lineage', [])}
        while revision < item['revision']:
            link = links.get(revision)
            if not link or link['to'] <= revision or link['to'] > item['revision']:
                return False
            run = self.s.collaboration.get(db, 'work_unit_run', link['unit_run_id'])
            if (run['item_id'] != item['id'] or run['item_revision'] != revision
                    or run['generation'] != response['driver_generation']
                    or run['plan_id'] != plan['id'] or run['plan_version'] != plan['version']
                    or run['cooperative']['authorization'] != response['authorization']
                    or digest(run['input']) != run['input_version']):
                return False
            revision = link['to']
        return revision == item['revision']

    def human_response(self, db, p):
        if set(p) != {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'flow', 'sources', 'note'}:
            raise ValueError('主持人关键取舍回复须绑定准确任务、方案、原授权及专家来源。')
        item, driver, plan = self.s.work_units.checked(db, p)
        if plan['mode'] != 'host_experts' or not organization(self.s, db, plan):
            raise ValueError('只有日常主持人合作可登记关键取舍回复。')
        flow = self.s.work_flows.current(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        if (not flow or flow['state'] != 'PAUSED' or self.s.work_flows.reference(flow) != p['flow']
                or p['authorization'] != self.s.work_units.auth_ref(auth)
                or p['frontier'] != self.s.work_units.frontier(db, item['id'])
                or self.s.work_drivers.execution_issues(db, item['id'], driver)):
            raise ValueError('先暂停并核对准确原授权、流程与所有关联回合退出。')
        context = self.context(db, item, plan, self.s.work_units.rows(db, item['id']))
        sources = [e['source'] for e in context['discussion']['experts'] if e['source']]
        if not context['discussion']['requires_human'] or p['sources'] != sources:
            raise ValueError('当前关键取舍或专家意见版本已变化。')
        row = {'id': 'host-human-response:' + uid(), 'item_id': item['id'], 'item_revision': item['revision'],
               'plan': {k: plan[k] for k in ('id', 'version')}, 'driver_generation': driver['generation'],
               'authorization': p['authorization'], 'sources': sources, 'host_request': self.host_request(db, plan),
               'note': bounded_text(p['note'], '关键取舍回复', 4000),
               'created': time.time(), 'command_id': self.s.command_identity}
        row['version'] = digest(row)
        self.s.collaboration.put(db, 'work_host_experts_human_response', row)
        self.s.store.event(None, None, 'host_experts_human_response', row, db)
        return {'response': row, 'no_dispatch': True, 'original_authorization_unchanged': True}

    def decision(self, *args):
        state, reason, candidate = super().decision(*args)
        return state, reason.replace('负责人', '主持人'), candidate

    def next_round(self, *args):
        state, reason, candidate = super().next_round(*args)
        return state, reason.replace('负责人', '主持人'), candidate

    @staticmethod
    def expert_feedback(answer):
        try:
            value = json.loads(answer, object_pairs_hook=unique_fields,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非有限值')))
        except (ValueError, TypeError, RecursionError):
            raise ValueError('专家讨论须返回严格讨论JSON。') from None
        if (not isinstance(value, dict) or set(value) != {'protocol', 'proposal', 'objections', 'assistance', 'requires_human'}
                or value['protocol'] != EXPERT_PROTOCOL or type(value['requires_human']) is not bool):
            raise ValueError('专家讨论协议或字段无效。')
        value['proposal'] = bounded_text(value['proposal'], '专家方案建议', 4000)
        for field in ('objections', 'assistance'):
            if not isinstance(value[field], list) or len(value[field]) > 20:
                raise ValueError('专家异议或协助请求须为最多20项文字数组。')
            value[field] = [bounded_text(v, '专家讨论意见', 2000) for v in value[field]]
        return value

    def context(self, db, item, plan, rows):
        result = super().context(db, item, plan, rows)
        confirmed = (plan.get('pdca') or {}).get('confirmed_discussion', False)
        discussions = []
        for candidate in result['candidates']:
            if confirmed or candidate['unit']['role'] != 'plan':
                continue
            evidence = candidate['latest_result']
            entry = {'unit_key': candidate['unit']['key'], 'member': candidate['unit']['member']['reference'],
                     'source': None, 'feedback': None, 'problem': None}
            if evidence and evidence['result_available']:
                entry['source'] = {'unit_key': entry['unit_key'], 'result_version': evidence['version']}
                try:
                    entry['feedback'] = self.expert_feedback(evidence['answer'])
                except ValueError as exc:
                    entry['problem'] = str(exc)
            discussions.append(entry)
        result['discussion'] = {
            'experts': discussions,
            'complete': confirmed or bool(discussions) and all(e['feedback'] for e in discussions),
            **({'confirmed': True} if confirmed else {}),
            'requires_human': any(e['feedback'] and e['feedback']['requires_human'] for e in discussions),
            'rule': 'Read expert proposals, objections and assistance; resolve ordinary disagreements with evidence. Pause for key Human decisions. Revise duties with exact original-authority previews.'}
        considered = [e['source'] for e in discussions if e['source']]
        required = [e['source'] for e in discussions if e['feedback'] and e['feedback']['requires_human']]
        host_request = self.host_request(db, plan)
        result['discussion']['requires_human'] = bool(required or host_request)
        response = self.response(db, item, plan, considered, required, host_request)
        result['discussion']['human_response'] = response
        if response:
            result['discussion']['requires_human'] = False
        for schema in result['schemas'].values():
            schema['protocol'] = PROTOCOL
            schema['discussion'] = {'considered': considered, 'resolution': '回应已有专家建议、异议与协助，并说明当前分工依据',
                                    'requires_human': False}
        return result

    @staticmethod
    def parse(answer, protocol=PROTOCOL):
        try:
            value = json.loads(answer, object_pairs_hook=unique_fields,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非有限值')))
        except (ValueError, TypeError, RecursionError):
            raise ValueError('主持人须返回严格决策JSON。') from None
        if not isinstance(value, dict) or value.get('protocol') != PROTOCOL or protocol != PROTOCOL:
            raise ValueError('主持人决策协议不符。')
        discussion = value.pop('discussion', None)
        if (isinstance(discussion, dict) and discussion.get('protocol') == protocol
                and set(discussion) == {'protocol', 'considered', 'resolution', 'requires_human'}):
            del discussion['protocol']
        if (not isinstance(discussion, dict) or set(discussion) != {'considered', 'resolution', 'requires_human'}
                or type(discussion['requires_human']) is not bool or not isinstance(discussion['considered'], list)):
            raise ValueError('主持人须明确记录专家证据与讨论收束。')
        discussion['resolution'] = bounded_text(discussion['resolution'], '主持人讨论收束', 4000)
        refs = discussion['considered']
        if (len(refs) > 19 or any(not isinstance(ref, dict) or set(ref) != {'unit_key', 'result_version'}
                or not all(isinstance(v, str) and v for v in ref.values()) for ref in refs)
                or len({ref['unit_key'] for ref in refs}) != len(refs)):
            raise ValueError('主持人讨论证据引用无效或重复。')
        value['protocol'] = 'WORKDESK_LEADER_DAILY_DECISION_V2'
        decision = WorkLeader.parse(canonical(value), value['protocol'])
        return {**decision, 'protocol': PROTOCOL, 'discussion': discussion}

    def validate_decision(self, context, decision):
        experts = context['discussion']['experts']
        expected = [e['source'] for e in experts if e['source']]
        if sorted(decision['discussion']['considered'], key=lambda r: r['unit_key']) != sorted(expected, key=lambda r: r['unit_key']):
            raise ValueError('主持人须引用本轮全部准确专家意见，不能遗漏或替换版本。')
        if decision['action'] == 'pause':
            return
        if context['discussion']['requires_human'] or decision['discussion']['requires_human']:
            raise ValueError('专家或主持人提出关键Human取舍，须暂停并交给Human。')
        if decision['action'] == 'dispatch':
            unit = next((c['unit'] for c in context['candidates'] if c['unit']['key'] == decision['unit_key']), None)
            if unit and unit['role'] == 'plan':
                return
        # Exact revisions may repair a failed discussion or add an expert.
        if decision['action'] in {'adjust_dependencies', 'rework_units', 'revise_units', 'file_rework_units'}:
            return
        if not context['discussion']['complete']:
            raise ValueError('专家讨论尚未齐备，主持人不能跳过讨论开展执行或交付。')

    def coordinator_prompt(self, frozen):
        discussion = frozen['coordination_context']['discussion']
        if discussion.get('confirmed'):
            return ('你是主持人。用户已经确认需求，专家预讨论已经收束。按候选单元顺序推进 PDCA。'
                    '根据准确结果组织下一步，全部阶段完成后核对交付。需要用户取舍时返回 pause。'
                    '保持原要求、权限、预算和文件范围。按 coordination_context.schemas 输出严格 JSON，'
                    'discussion.considered 为 []，附简短 resolution。不写文件。输入快照：\n' + canonical(frozen))
        sources = [expert['source'] for expert in discussion['experts'] if expert['source'] is not None]
        citation = ('没有已完成的专家结果。必须提交 "considered":[]，不为尚未返回的专家、候选或空来源造占位引用。'
                    if not sources else '把下方已冻结的数组逐字作为 considered 值。每项只有 unit_key 与 result_version；'
                    '不要写摘要、额外字段、null 或路径，不用自然语言替代引用。')
        evidence = 'discussion.considered 的准确JSON值：' + canonical(sources) + '。' + citation
        return ('你是本任务主持人，也是可参与工作的专家。先组织选定专家共同判断问题、参与者、方案和分工。'
                + evidence + '\n' +
                '逐项阅读discussion中的准确建议、异议和协助请求，普通分歧依据目标与证据收束，关键取舍暂停交给Human。'
                '需要增援、变更职责或重新讨论时，用准确修订协议调整分工；保留已有成果和最初授权。'
                '每次决策在discussion.considered引用本轮全部已有专家来源，resolution明确回应意见、异议与协助。'
                '若discussion.human_response已给出用户对准确问题的回复，在原任务范围内据此重新收束；新关键问题再请求Human。'
                '全部讨论齐备并收束后再安排执行，最终阅读检查与整合文件并交付。'
                '协调计入同一累计预算，当前协调已计数；原生Agent保留自己的工具与执行组织。'
                '不得扩大候选write_paths；修改前置文件须按原路径文件返工协议。'
                '最终回复只输出 coordination_context.schemas 中所选 schema 的严格 JSON 对象。逐字段核对必填项。'
                'protocol 只放在顶层。discussion 恰有 considered、resolution、requires_human 三个字段；'
                'reason 和 discussion.resolution 都用简短单行文字，resolution 用两三句回应实际意见即可。'
                '字符串结束后直接写逗号或右括号，勿多写引号、尾逗号或 Markdown。不写文件。输入快照：\n'
                + canonical(frozen))

    def view(self, db, plan, rows):
        """Read persisted discussion and decisions without composing a new round."""
        experts = []
        for unit in plan['units']:
            if (plan.get('pdca') or {}).get('confirmed_discussion') or unit['role'] != 'plan':
                continue
            resolved = self.s.work_units.resolve(db, plan, unit['key'], rows)
            evidence = resolved['evidence']
            feedback, problem = None, None
            if evidence and evidence['result_available']:
                try:
                    feedback = self.expert_feedback(evidence['answer'])
                except ValueError as exc:
                    problem = str(exc)
            experts.append({'unit_key': unit['key'], 'member_name': unit['member']['name'],
                            'result_version': evidence['version'] if evidence else None,
                            'feedback': feedback, 'problem': problem})
        proposals = sorted([p for p in self.s.collaboration.all(db, 'work_proposal')
                     if p['item_id'] == plan['item_id'] and p['snapshot'].get('protocol') == PROTOCOL]
                     , key=lambda p: p['created'])
        item = self.s.work_items.item(db, plan['item_id'])
        sources = [{'unit_key': e['unit_key'], 'result_version': e['result_version']} for e in experts if e['feedback'] or e['problem']]
        required_sources = [{'unit_key': e['unit_key'], 'result_version': e['result_version']} for e in experts if e['feedback'] and e['feedback']['requires_human']]
        host_request = self.host_request(db, plan)
        response = self.response(db, item, plan, sources, required_sources, host_request)
        flow = self.s.work_flows.current(db, item['id'])
        required = bool(required_sources or host_request) and not response
        driver = self.s.work_drivers.current(db, item['id'])
        return {'protocol': PROTOCOL, 'experts': experts, 'sources': sources, 'human_response': response,
                'requires_human': required, 'can_respond': bool(required and flow and flow['state'] == 'PAUSED' and driver and driver['state'] == 'RESERVED'),
                'decisions': [{'proposal': self.s.work_proposals.reference(p), 'plan': p['snapshot']['plan'],
                               'state': p['snapshot']['state'], 'decision': p['snapshot']['decision'],
                               'problems': p['snapshot']['problems']} for p in proposals]}

    @staticmethod
    def expert_prompt(prompt):
        schema = {'protocol': EXPERT_PROTOCOL, 'proposal': '建议方案、参与者与分工及其依据',
                  'objections': [], 'assistance': [], 'requires_human': False}
        return ('你作为讨论专家，与主持人共同形成方案和分工。请判断目标、证据、参与者是否合适。'
                '此轮只提交讨论意见，不写文件。\n' + prompt
                + '\n以上输入里的角色、历史决策和文件要求不改变本轮输出格式。'
                  '最后只输出一个 JSON 对象，必须原样保留下面全部五个字段，不能省略 protocol 或 requires_human。'
                  '仅关键用户取舍将 requires_human 设为 true；否则明确写 false。'
                  '用简短文字填写 proposal，用字符串数组填写 objections、assistance，无则写 []。不要附解释或围栏。\n'
                + canonical(schema))
