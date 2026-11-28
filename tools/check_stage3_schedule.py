"""Exercise real scheduled work, a lost process owner, and a missed occurrence."""
from datetime import datetime, timezone
from pathlib import Path
import time
from uuid import uuid4

from check_core_chain import BASE, require


def check_schedule(chain):
    project = chain.project_id
    view_path = '/api/v2/tool-schedules?project_id=' + project

    def schedule(repeat='once', rounds=1, delay=4, action=None, identity=None):
        when = int(time.time()) + delay
        body = {'project_id': project, 'tool_version': 'computer/1',
                'parameters': action or {'action': 'file.read', 'path': 'notes.txt'},
                'timezone': 'UTC', 'repeat': repeat, 'max_runs': rounds, 'confirm': True,
                'first_at': datetime.fromtimestamp(when, timezone.utc).isoformat(timespec='seconds')}
        return body, chain.command('schedule.tool-create', body, identity)['receipt']

    def wait_round(sid, state, timeout=25):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            for row in chain.api(view_path)['rounds']:
                if row['schedule_id'] == sid:
                    if row['state'] == state:
                        return row
                    require(row['state'] in ('QUEUED', 'RUNNING', 'STOP_REQUESTED'), str(row))
            time.sleep(.15)
        raise RuntimeError('指定轮次未进入 ' + state)

    require(chain.api(view_path)['actions'] == ['file.read', 'env.inspect'], '调度动作清单不符')
    command = str(uuid4())
    params, one = schedule(identity=command)
    again = chain.command('schedule.tool-create', params, command)
    require(again['replayed'] and again['receipt'] == one, '重复预约命令新建了计划')
    success = wait_round(one['schedule_id'], 'COMPLETED')
    job = chain.api('/api/v2/jobs/' + success['job_id'])
    result = next(a for a in job['artifacts'] if a['path'].endswith('output.txt'))
    require(Path(result['path']).read_text('utf8') == (chain.project / 'notes.txt').read_text('utf8'), '计划结果不同于原工程输入')
    receipt = chain.api('/api/v1/receipt?id=' + success['command_id'])['receipt']
    require(receipt['invocation_id'] == job['id'] and job['round_id'] == success['round_id'], '计划与工具原回执断开')

    # Keep the only local worker slot busy in a second disposable project.
    # The scheduled job below is durably queued before the exact fixture Go
    # process is terminated. No production process is touched.
    require(chain.root.is_relative_to(BASE.parent / 'artifacts') and
            chain.process.pid == chain.backend['pid'], '核验进程不属于独立夹具')
    second = chain.root / 'queue-owner'
    second.mkdir()
    second_id = chain.command('project.create', {'name':'队列检查样本', 'root':str(second)})['receipt']['project_id']
    (second/'wait.py').write_text("import time\nprint('ready',flush=True)\ntime.sleep(90)\n", encoding='utf8')
    grant = chain.command('tool.authorize', {'project_id':second_id,'confirm':True,'tool_version':'computer/1',
                          'parameters':{'action':'terminal.run','path':'wait.py'}})['receipt']['authorization_id']
    blocker = chain.command('tool.run', {'authorization_id':grant})['receipt']['invocation_id']
    until = time.monotonic()+15
    while 'ready' not in chain.api('/api/v2/jobs/'+blocker+'/console')['text']:
        if time.monotonic() > until:
            raise RuntimeError('临时工程作业没有占用执行槽')
        time.sleep(.1)
    _, recurring = schedule(repeat='daily', rounds=2, delay=3)
    queued = wait_round(recurring['schedule_id'], 'QUEUED')
    _, offline = schedule(delay=3)
    owner = chain.backend['instance']
    chain.process.kill()
    chain.process.wait(timeout=8)
    while time.time() < offline['next_at'] + 1:
        time.sleep(.15)
    chain.start()
    require(owner != chain.backend['instance'], '进程身份未更新')
    unknown = wait_round(recurring['schedule_id'], 'ORPHANED_UNKNOWN')
    missed = wait_round(offline['schedule_id'], 'MISSED')
    view = chain.api(view_path)
    require(not missed.get('job_id') and unknown['job_id'] == queued['job_id'], '重新派发了待核或漏跑轮次')
    require(next(p for p in view['schedules'] if p['schedule_id']==recurring['schedule_id'])['state']=='PAUSED',
            '存在待核轮次时仍在自动派发')
    require(sum(r['schedule_id']==recurring['schedule_id'] for r in view['rounds'])==1 and
            sum(r['schedule_id']==one['schedule_id'] for r in view['rounds'])==1, '同轮次出现重复记录')
    repeated = chain.command('tool.run', {'authorization_id':job['authorization_id']}, success['command_id'])
    require(repeated['replayed'] and repeated['receipt']['invocation_id']==job['id'], '重启后未保留原执行命令')
    _, weekly = schedule(repeat='weekly', rounds=3, delay=90, action={'action':'env.inspect'})
    for state in ('PAUSED', 'ACTIVE', 'CANCELLED'):
        weekly=chain.command('schedule.tool-state', {'schedule_id':weekly['schedule_id'], 'revision':weekly['revision'],
                             'state':state, 'confirm':True})['receipt']
    return {'project_id':project, 'schedule_id':one['schedule_id'], 'job_id':job['id'],
            'command_id':success['command_id'], 'occurrence_id':success['round_id'], 'round_id':success['round_id'],
            'unknown_round_id':unknown['round_id'], 'unknown_job_id':unknown['job_id'], 'missed_round_id':missed['round_id'],
            'replayed':True, 'owner_restart':True, 'weekly_state':weekly['state'], 'real_model_requests':0}
