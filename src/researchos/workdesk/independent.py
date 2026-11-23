"""Independent work connects only model vendors' own official agent runtimes.

This is a service adapter/metadata boundary. Planning and tool execution stay
inside Codex, DeepSeek Harness and Qwen Code; task authority stays in Service.
"""
from copy import deepcopy

QWEN_ROUTE_ID = 'qwen-work-runtime'


def qwen_runtime(spec):
    if spec.get('provider') == 'qwen' and spec.get('runtime_mode') == 'official':
        return spec.get('qwen_runtime', 'query')
    return None


SERVICES = {
    'codex': {'name': 'Codex', 'harness': 'Codex app-server',
              'execution': 'codex_native', 'native_resume': True,
              'interrupt': 'turn_interrupt',
              'source': 'https://developers.openai.com/codex/app-server'},
    'deepseek': {'name': 'DeepSeek', 'harness': 'DeepSeek Harness',
                 'execution': 'deepseek_native', 'native_resume': True,
                 'interrupt': 'owned_runtime_shutdown',
                 'source': 'https://github.com/deepseek-ai/deepseek-harness'},
    'qwen': {'name': 'Qwen', 'harness': 'Qwen Code SDK',
             'execution': 'qwen_native', 'native_resume': True,
             'interrupt': 'sdk_interrupt_and_owned_runtime_exit',
             'source': 'https://qwenlm.github.io/qwen-code-docs/en/developers/sdk-typescript/'},
}


class IndependentWork:
    def __init__(self, service):
        self.s = service

    @staticmethod
    def describe(provider, interface=None):
        if provider not in SERVICES:
            raise ValueError('独立工作只接入已登记的模型自家官方运行服务。')
        from .native_permissions import catalog
        from .native_controls import catalog as controls
        return {**deepcopy(SERVICES[provider]), **({'harness':'Qwen Code Daemon','interface':'daemon'} if provider=='qwen' and interface=='daemon' else {}),
                'provider': provider,'access_modes':catalog(provider), 'native_controls':controls(provider, interface),
                'origin': 'MODEL_VENDOR_OFFICIAL', 'agent_loop': 'VENDOR_RUNTIME',
                'context_resume': True, 'native_tools': True,
                'delegation_enabled': None,
                'delegation_note': '新独立Work沿用官方工作端配置；原生子代理的完整交互接入仍需逐端核对。',
                'scope': 'INDEPENDENT_WORK', 'third_party_client': False}

    def catalog(self, profiles=None):
        profiles = self.s.profiles.list() if profiles is None else profiles
        with self.s.store.read() as db:
            route=self.qwen_selection(db)
        return {'services': [{**self.describe(p['id'],route['interface'] if p['id']=='qwen' else None),
                             'runtime_present': p['runtime_present'],
                             **({'route_selection':route,'daemon_present':self.qwen_daemon_present()} if p['id']=='qwen' else {}),
                             'auth_status': p.get('auth_status', 'NOT_CHECKED'),
                             'model': p['model'], 'effort': p['effort'], 'tier': p['tier']}
                            for p in profiles if p['id'] in SERVICES],
                'no_dispatch': True, 'capability_check_is_not_generation': True}

    def qwen_daemon_present(self):
        sdk=self.s.locations.runtime/'qwen/node_modules/@qwen-code/sdk/dist'
        return all((sdk/name).is_file() for name in ('index.mjs','cli/cli.js','daemon/DaemonClient.d.ts'))

    def qwen_selection(self, db):
        import json
        row=db.execute("SELECT data FROM collaboration WHERE kind='official_runtime_selection' AND id=?",(QWEN_ROUTE_ID,)).fetchone()
        return json.loads(row[0]) if row else {'id':QWEN_ROUTE_ID,'interface':'query','revision':None}

    def select_qwen(self, db, payload):
        from .store import uid
        current=self.qwen_selection(db)
        if set(payload)!={'interface','revision','confirm'} or payload['confirm'] is not True or payload['revision']!=current['revision']:
            raise ValueError('请核对当前工作端选择并明确确认。')
        interface=payload['interface']
        if interface not in ('query','daemon') or interface=='daemon' and not self.qwen_daemon_present():
            raise ValueError('所选 Qwen 官方接口在当前安装中不可用。')
        row={'id':QWEN_ROUTE_ID,'interface':interface,'revision':uid()}
        self.s.collaboration.put(db,'official_runtime_selection',row)
        return {'selection':row,'no_dispatch':True}

    def create(self, db, payload):
        provider = payload.get('provider', 'codex')
        service = self.describe(provider)
        if payload.get('work_mode', 'independent') != 'independent' or payload.get('chain_id'):
            raise ValueError('独立工作不接收合作角色或问题链编排。')
        if payload.get('role', 'executor') != 'executor':
            raise ValueError('独立工作不采用Workdesk的统筹、评审或自检角色编排。')
        if payload.get('execution', service['execution']) != service['execution']:
            raise ValueError('独立工作须使用所选模型自家的官方运行服务。')
        if payload.get('interaction'):
            raise ValueError('普通Chat使用独立的聊天入口。')
        return self.s._create(db, {**payload, 'provider': provider,
                                  'execution': service['execution'],
                                  'work_mode': 'independent', 'campaign': None})
