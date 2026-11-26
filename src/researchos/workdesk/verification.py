"""Version-bound independent code review and closure of this finite rollout only."""
from hashlib import sha256
import json
from pathlib import Path
import time

from .paths import _implementation_content
from .workspace import confirmed_reads
from .store import ACTIVE,UNKNOWN,canonical,digest,uid


CAMPAIGN='backend-rollout-20261017'
INTEGRATION_FILES={f'src/researchos/workdesk/{name}.py' for name in
                   ('service','store','collaboration','operations','persistence','verification')}


class Verification:
    def __init__(self,service):self.s=service;self.store=service.store;self.c=service.collaboration

    def handlers(self):return {'audit.record':self.record,'rollout.close':self.close}

    def scope(self):
        content=_implementation_content(self.s.locations)
        ui=self.s.locations.base/'workdesk-ui'
        for path in sorted(p for p in ui.rglob('*') if p.is_file() and '__pycache__' not in p.parts):
            content[path.relative_to(self.s.locations.base).as_posix()]=path.read_bytes()
        return {name:{'sha256':sha256(raw).hexdigest(),'bytes':len(raw)} for name,raw in sorted(content.items())}

    def record(self,db,p):
        task=self.store.task(p.get('task_id'),db)
        if task['spec']['role']!='reviewer':raise ValueError('代码独立审查必须来自指定reviewer职责的新会话。')
        a=db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started DESC LIMIT 1',(task['id'],)).fetchone()
        if not a:raise ValueError('审查没有真实尝试。')
        attempt=self.store.attempt_row(a);result=attempt['result'] or {};full_scope=self.scope()
        names=p.get('scope_files',list(full_scope));kind=p.get('review_kind','full')
        if (not isinstance(names,list) or not names or any(not isinstance(n,str) for n in names)
                or len(set(names))!=len(names) or not set(names)<=full_scope.keys()
                or kind not in {'full','section','integration'}):raise ValueError('代码审查范围/类型无效。')
        scope={name:full_scope[name] for name in names}
        if kind=='full' and scope!=full_scope:raise ValueError('完整审查不能省略代码。')
        if kind=='integration' and not INTEGRATION_FILES<=scope.keys():raise ValueError('跨模块审查缺少关键控制/持久化/审查模块。')
        if attempt['state']!='COMPLETED' or not result.get('known_tools_exited') or (result.get('owned_job') or {}).get('active_processes')!=0:raise ValueError('审查没有正常独立终态/本地退出证据。')
        if result.get('implementation_revision')!=self.s.implementation:raise ValueError('审查之后执行实现已变化。')
        if any(task['spec']['materials'].get(name)!=entry for name,entry in scope.items()):raise ValueError('审查实际材料未覆盖当前后端与界面完整代码版本。')
        receipts=[]
        for row in db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='tool_completed'",(attempt['id'],)):
            event=json.loads(row[0])
            if event.get('name')=='read_file':
                receipts.append(event.get('result',{}))
        observed=confirmed_reads(receipts,scope)
        if any(observed.get(name)!=entry for name,entry in scope.items()):raise ValueError('审查会话没有完整读取全部准确代码正文/分段，不能计为完整独立代码审查。')
        answer=result.get('answer','').strip()
        if answer.startswith('```') and answer.endswith('```'):answer=answer.split('\n',1)[1].rsplit('```',1)[0].strip()
        verdict=json.loads(answer)
        if not isinstance(verdict,dict) or verdict.get('implementation_revision')!=self.s.implementation or verdict.get('verdict') not in {'pass','return'} or not isinstance(verdict.get('findings'),list):raise ValueError('代码审查结论缺失/版本不符/格式不完整。')
        for finding in verdict['findings']:
            if not isinstance(finding,dict) or finding.get('file') not in scope or finding.get('priority') not in {'P0','P1','P2','P3'} or not isinstance(finding.get('message'),str):raise ValueError('审查问题清单不符合准确代码范围。')
        synthetic=bool((result.get('owned_job') or {}).get('synthetic') or result.get('verification_kind'))
        value={'id':uid(),'task_id':task['id'],'attempt_id':attempt['id'],'session_id':attempt['session_id'],
              'implementation_revision':self.s.implementation,'scope':scope,'scope_hash':digest(scope),
              'full_scope_hash':digest(full_scope),'review_kind':kind,
              'verdict':verdict['verdict'],'findings':verdict['findings'],'at':time.time(),
              'evidence':'SYNTHETIC_COMPONENT' if synthetic else 'REAL_FRESH_REVIEW_SESSION',
              'independence':'separate worker session from implementing chat; shared model/provider and host, not independent infrastructure',
              'human_acceptance':False}
        self.c.put(db,'code_audit',value);return {'audit':value,'no_dispatch':True}

    def close(self,db,p):
        self.s.persistence.idle(db)
        identities=p.get('audit_ids',[p.get('audit_id')]);scope=self.scope()
        if not isinstance(identities,list) or not 1<=len(identities)<=8 or any(not isinstance(i,str) for i in identities) or len(set(identities))!=len(identities):raise ValueError('审查身份清单无效。')
        audits=[self.c.get(db,'code_audit',identity) for identity in identities];covered={}
        for audit in audits:
            if (audit['scope_hash']!=digest(audit['scope']) or not audit['scope']
                    or any(scope.get(n)!=entry for n,entry in audit['scope'].items())
                    or audit.get('full_scope_hash',digest(audit['scope']))!=digest(scope)
                    or audit['implementation_revision']!=self.s.implementation or audit['verdict']!='pass'
                    or audit['findings'] or audit['evidence']!='REAL_FRESH_REVIEW_SESSION'):
                raise ValueError('缺当前实现的适用真实独立代码审查通过，不能封闭全程。')
            covered.update(audit['scope'])
        full=any(a['scope']==scope for a in audits)
        integration=any(a.get('review_kind')=='integration' and INTEGRATION_FILES<=a['scope'].keys() for a in audits)
        if covered!=scope or not (full or integration):raise ValueError('分段审查必须完整覆盖全部代码，并有单独的跨模块审查。')
        if not full and len({a['session_id'] for a in audits})!=len(audits):raise ValueError('分段与跨模块审查须使用独立会话。')
        path=self.s.locations.data/'verification/backend-rollout-20261017/checks-final.json'
        report=json.loads(path.read_text(encoding='utf-8'))
        if report.get('implementation_revision')!=self.s.implementation or report.get('ui_scope_hash')!=digest(scope):raise ValueError('最终检查版本不一致。')
        required={'backend','ui-http','ui-render','ui-controls','js-syntax'}
        suites={s['suite']:s for s in report.get('suites',[])}
        if not required<=suites.keys() or any(suites[k].get('exit_code')!=0 for k in required):raise ValueError('最终必需检查未通过。')
        usage=self.store.usage(campaign=CAMPAIGN,db=db)
        if usage['reserved_cny'] is None or usage['model_requests']>60 or usage['reserved_cny']>12 or usage['codex_turns']>12:raise ValueError('实施累计费用未知或限额不符，保留问题，不能宣称通过。')
        value={'campaign':CAMPAIGN,'state':'CLOSED','at':time.time(),'audit_id':identities[0],'audit_ids':identities,
               'implementation_revision':self.s.implementation,'scope_hash':digest(scope),'usage':usage,
               'checks_sha256':sha256(path.read_bytes()).hexdigest(),'human_acceptance':False,
               'credentials_preserved':True,'ordinary_explicit_human_tasks_affected':False}
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('campaign_closed:'+CAMPAIGN,canonical(value)))
        return {'rollout':value,'no_dispatch':True}

    def catalog(self):
        scope=self.scope()
        with self.store.read() as db:
            row=db.execute('SELECT value FROM meta WHERE key=?',('campaign_closed:'+CAMPAIGN,)).fetchone()
            return {'protocol':1,'interface_status':'V1_WORKING_CONTRACT','scope':scope,
                    'scope_hash':digest(scope),'audits':self.c.all(db,'code_audit'),
                    'rollout':json.loads(row[0]) if row else {'state':'OPEN'},'no_dispatch':True,
                    'capabilities':{'coordinator':'explicit transcript in fresh session','parallel':2,
                      'handoff':'preapproved recipients; known old exit and remote protocol; shared remaining budget',
                      'backup':'allowlisted data + code + transcript evidence; credential/native SDK homes excluded',
                      'restore':'new data root; revoked grants; paused queues; additive budget debt',
                      'project_relocation':'same machine; exact selected material hashes; no automatic file moves',
                      'codex_auth':'subscription only','deepseek_resume':'new context only',
                      'qwen_restored_native_resume':'not available for pre-backup sessions; explicit new context instead',
                      'os_network_sandbox':'NOT_PROVIDED','human_acceptance':'NOT_RECORDED'}}
