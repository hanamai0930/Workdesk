"""Verify real scheduled and restart outcomes in the inbox, plus Human goal control."""
from time import time
from urllib.parse import urlencode
from uuid import uuid4

from check_core_chain import require


def check_notifications(chain, schedule_result):
    project=chain.project_id
    def lookup(**q):
        return chain.api('/api/v2/notifications?'+urlencode({'project_id':project,**q}))
    def one_round(identity):
        rows=lookup(round_id=identity)['notifications']
        require(len(rows)==1,'同一轮次通知数量异常')
        return rows[0]
    round_id=schedule_result.get('occurrence_id') or schedule_result['round_id']
    note=one_round(round_id)
    require(note['job_id']==schedule_result['job_id'] and note['schedule_id']==schedule_result['schedule_id']
            and note['category']=='completion' and note['state']=='OPEN','完成通知与实际作业不符')
    require(round_id in note['href'] and project in note['href'],'轮次回跳未绑定准确项目与轮次')
    cmd=str(uuid4());decision={'id':note['id'],'revision':note['revision'],'state':'HANDLED','note':'结果已查回'}
    answer=chain.command('notice.state',decision,cmd)
    require(chain.command('notice.state',decision,cmd)['replayed'],'处理回执未复用')
    after=lookup(id=note['id'])['notice']
    require(after['state']=='HANDLED' and after['handling_command_id']==cmd and after['handling_note']=='结果已查回'
            and chain.api('/api/v1/receipt?id='+cmd)['receipt']==answer['receipt'],'未回读同一个处理回执')
    chain.command('notice.state',{**decision,'state':'OPEN'},expected=409)
    reopen=chain.command('notice.state',{'id':note['id'],'revision':after['revision'],'state':'OPEN'})
    recovered=lookup(id=note['id'])
    require(recovered['notice']['handling_command_id']==reopen['command_id'] and
            [h['command_id'] for h in recovered['history']]==[cmd,reopen['command_id']],'重新打开未留下处理历史')
    unknown=one_round(schedule_result['unknown_round_id'])
    require(unknown['category']=='review' and 'UNKNOWN' in unknown['source_state']
            and unknown['job_id']==schedule_result['unknown_job_id'],'重启轮次未关联原待核作业')
    manual=[]
    for kind in ('reminder','todo'):
        p={'category':kind,'title':'阶段 D '+kind,'project_id':project}
        if kind=='reminder':p['due_at']=int(time())-1
        row=chain.command('notice.create',p)
        saved=lookup(id=row['receipt']['notice_id'])['notice']
        require(saved['source_command_id']==row['command_id'] and (kind!='reminder' or saved['due']),
                '待办或到期提醒缺少来源')
        manual.append(saved['id'])
    goal=chain.command('planning.goal-save',{'project_id':project,'provider':'codex','title':'阶段 D 目标',
                       'goal':'核对样本任务','criteria':'Human 核对样本结果','access':'workspace',
                       'checks':[],'max_rounds':2,'dispatch_before':int(time())+3600,'confirm':True})
    row=goal['receipt']['job']
    blocked=chain.command('planning.goal-blocker',{'id':row['id'],'revision':row['revision'],'blocker':'等待样本确认'})
    g=blocked['receipt']['job']
    require(g['state']=='PAUSED' and g['origin_command_id']==goal['command_id'] and not g['rounds'],'持续目标未保留来源、阻碍与暂停状态')
    chain.command('planning.state',{'kind':'goal_job','id':g['id'],'revision':g['revision'],'state':'RUNNING','confirm':True},expected=409)
    link=next(n for n in lookup()['notifications'] if n.get('goal_id')==g['id'])
    require(g['id'] in link['href'] and link['source_command_id']==blocked['command_id'],'目标阻碍通知未关联操作')
    return {'notice_id':note['id'],'schedule_id':note['schedule_id'],'round_id':round_id,'job_id':note['job_id'],
            'handled_command_id':cmd,'reopened_command_id':reopen['command_id'],'unknown_notice_id':unknown['id'],
            'unknown_job_id':unknown['job_id'],'manual_notices':manual,'goal_id':g['id'],'goal_blocked':True,'real_model_requests':0}
