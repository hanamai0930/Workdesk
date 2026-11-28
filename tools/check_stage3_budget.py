"""Check real schedule and tool accounting without contacting a model provider."""
from urllib.parse import urlencode
from uuid import uuid4

from check_core_chain import require


def check_budget(chain, schedule_result):
    from check_stage3_foundation import await_job
    project = chain.project_id
    policy_route = '/api/v2/project-budget?' + urlencode({'project_id':project})
    usage_route = '/api/v1/project-usage?' + urlencode({'project_id':project,'days':1,'tool_version':'computer/1'})
    initial = chain.api(policy_route)
    original = initial['policy']

    def save(count, concurrent, unknown):
        current = chain.api(policy_route)['policy']
        return chain.command('budget.save',{'project_id':project,'revision':current['revision'],'max_dispatches':count,
                             'max_concurrent':concurrent,'unknown_cost_policy':unknown,'confirm':True})['receipt']['policy']

    target = schedule_result['job_id']
    identified = [row for row in initial['entries'] if row['id']==target]
    require(len(identified)==1 and identified[0]['state']=='SETTLED' and
            identified[0]['round_id']==schedule_result['occurrence_id'], '原轮次没有唯一结算记录。')
    held = next((row for row in initial['entries'] if row['id']==schedule_result['unknown_job_id']),None)
    require(held and held['state']=='RESERVED' and held['outcome']=='ORPHANED_UNKNOWN','重启后的原轮次未保留预留。')
    (chain.project/'budget_probe.py').write_text("print('accounting sample')\n",encoding='utf8')
    grant=chain.command('tool.authorize',{'project_id':project,'tool_version':'computer/1',
                 'parameters':{'action':'terminal.run','path':'budget_probe.py'},'confirm':True})['receipt']['authorization_id']
    request={'authorization_id':grant}
    try:
        save(initial['reserved_dispatches'] + initial['settled_dispatches'],None,'allow_unpriced')
        refused=chain.command('tool.run',request,expected=409)
        require('次数' in refused.get('error',''), '次数预算没有拒绝工作。')
        save(None,initial['reserved_dispatches'],'allow_unpriced')
        busy=chain.command('tool.run',request,expected=409)
        require('并发' in busy.get('error',''), '并发预算没有计入原来待核的预留。')
        save(None,None,'block_unpriced')
        unknown=chain.command('tool.run',request,expected=409)
        require('费用' in unknown.get('error',''), '未知费用被当成零准入。')
        require(len(chain.api(policy_route)['entries'])==len(initial['entries']), '被拒绝的派发出现预留。')
        accepted=save(None,None,'allow_unpriced')
        command_id=str(uuid4())
        receipt=chain.command('tool.run',request,command_id)['receipt']
        job=await_job(chain,receipt['invocation_id'])
        require(job['state']=='COMPLETED',job.get('error') or job['state'])
        require(chain.command('tool.run',request,command_id)['replayed'], '已执行命令未从原回执返回。')
        after=chain.api(policy_route)
        entries=[e for e in after['entries'] if e['id']==job['id']]
        require(len(entries)==1 and entries[0]['policy_revision']==accepted['revision'] and entries[0]['state']=='SETTLED',
                '同一次外部进程作业出现重复或不正确的结算。')
        require(after['settled_dispatches']==initial['settled_dispatches']+1, '派发次数未按单次结算。')
        usage=chain.api(usage_route)
        checked={r['id']:r for r in usage['rows']}
        require(checked[target]['model_cost_cny']=='0' and checked[job['id']]['model_cost_cny'] is None and
                usage['cost']['unknown_records']>=1 and usage['tokens']['tokens']['known_records']>=1 and
                usage['tokens']['tokens']['unknown_records']>=1, '已知与无法独立计量的外部执行费用未分列。')
        require(usage['cost']['estimated_cny'] is None and usage['cost']['estimated_records']==0,
                '本地样本无供应商定价回执，不应产生费用估算。')
        return {'scheduled_job_id':target,'round_id':schedule_result['occurrence_id'],'unknown_held':held['id'],
                'replayed_job_id':job['id'],'budget_revision':accepted['revision'],'count_refused':True,
                'concurrency_refused':True,'unpriced_refused':True,'known_cost_records':usage['cost']['known_records'],
                'unknown_cost_records':usage['cost']['unknown_records'],'estimated_cost_records':0,'real_model_requests':0}
    finally:
        save(original['max_dispatches'],original['max_concurrent'],original['unknown_cost_policy'])
