"""Explicit, restart-paused fixed workflows using the ordinary unit command."""
import json
import time
import uuid

from .work_leader import organization, strategy
from .store import ACTIVE, digest, uid

ACTIVE_FLOWS = {'SCHEDULED', 'RUNNING'}
READY_TO_START = {'READY', 'CAPTURE_READY', 'REWORK_READY', 'RECOVERY_READY', 'PROPOSAL_READY', 'REVISION_READY'}
SCHEDULE_WINDOW_SECONDS = 60


class WorkFlows:
    def __init__(self, service):
        self.s = service
        with self.s.store.write() as db:
            self.pause_active(db, '服务重启，核对责任后再明确启动', 'restart_paused')

    def pause_active(self, db, reason, action):
        for row in self.s.collaboration.all(db, 'work_flow'):
            if row['state'] in ACTIVE_FLOWS:
                self.save(db, {**row, 'state': 'PAUSED', 'reason': reason}, action)

    def schedule_catalog(self, db):
        rows = db.execute(
            "SELECT w.id,w.title,c.data FROM collaboration c JOIN work_items w ON w.id=json_extract(c.data,'$.item_id') "
            "WHERE c.kind='work_flow' AND json_extract(c.data,'$.start_at') IS NOT NULL "
            "ORDER BY json_extract(c.data,'$.start_at') DESC")
        result = []
        for item_id, title, raw in rows:
            row = json.loads(raw)
            result.append({'item_id': item_id, 'title': title, 'state': row['state'],
                           'reason': row['reason'], 'start_at': row['start_at']})
        return result

    def handlers(self):
        return {'work-item.flow': self.change}

    def current(self, db, item_id):
        row = db.execute("SELECT data FROM collaboration WHERE kind='work_flow' AND id=?", ('work-flow:' + item_id,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def reference(row):
        return {k: row[k] for k in ('id', 'generation', 'revision')} if row else None

    def save(self, db, row, action):
        row = {**row, 'revision': uid(), 'updated': time.time()}
        self.s.collaboration.put(db, 'work_flow', row)
        self.s.collaboration.put(db, 'work_flow_revision', {**row, 'id': 'flow-version:' + row['revision'], 'flow_id': row['id']})
        self.s.store.event(None, None, 'work_flow_' + action, row, db)
        return row

    def require_stopped(self, db, item_id):
        row = self.current(db, item_id)
        if row and row['state'] == 'SCHEDULED':
            raise ValueError('本任务已预约固定流程；先暂停预约，再调整授权或操作单元。')
        if row and row['state'] == 'RUNNING':
            raise ValueError('连续流程仍在运行；先暂停新派发，再调整授权或明确单步操作。')

    def pause_for_driver(self, db, item_id):
        row = self.current(db, item_id)
        if row and row['state'] in ACTIVE_FLOWS:
            self.save(db, {**row, 'state': 'PAUSED', 'reason': '合作责任已暂停或释放'}, 'driver_paused')

    def scope(self, db, item, driver, plan):
        return {'id': item['id'], 'revision': item['revision'], 'current_driver': self.s.work_drivers.reference(driver),
                'plan': {k: plan[k] for k in ('id', 'version')}}

    @staticmethod
    def delivered_unit(unit, run, result_version, files):
        return {'key': unit['key'], 'title': unit['title'], 'run_id': run['id'], 'task_id': run['task_id'],
                'result_version': result_version, 'artifact': files['reference'], 'manifest': files['manifest'],
                'check_status': files['check_status']}

    def check_rework(self, db, plan, unit, run, evidence, runs, auth):
        """Bind one repair to the exact, unconsumed failing delivery."""
        files = evidence['file_artifacts']
        missing = files.get('missing_outputs', [])
        source = self.s.work_artifacts.current(db, run['id'])
        if (not run.get('flow') or run['flow']['id'] != 'work-flow:' + run['item_id'] or
                run['cooperative']['authorization'] != self.s.work_units.auth_ref(auth) or
                run['generation'] != auth['driver_generation']):
            raise ValueError('原运行未使用本次连续检查返工授权')
        if any(r['plan_id'] == plan['id'] and r['unit_key'] == unit['key'] and r['input'].get('check_rework')
               for r in runs):
            raise ValueError('该单元已使用一次自动检查返工')
        if (not evidence['result_available'] or files['state'] != 'CURRENT' or files['check_status'] != 'FAIL' or
                not source or source['snapshot']['unit_revision'] != digest(unit) or
                set(files['manifest']) & set(missing) or
                set(files['manifest']) | set(missing) != set(unit['outputs']) or
                [check['definition'] for check in files['checks']] != unit['checks'] or
                not (missing or any(check['passed'] is False for check in files['checks']))):
            raise ValueError('原文件版本或失败检查记录已变化')
        write_paths = [path.casefold() for path in unit['write_paths']]
        failed_paths = [check['definition']['file'].casefold() for check in files['checks'] if not check['passed']]
        failed_paths.extend(name.casefold() for name in missing)
        if not all(any(path == '.' or file == path or file.startswith(path + '/') for path in write_paths)
                   for file in failed_paths):
            raise ValueError('失败产物超出本单元声明的写入范围')
        # Check both reserved downstream units and actual historical inputs.
        downstream = {unit['key']}
        for _ in plan['units']:
            added = {u['key'] for u in plan['units'] if downstream.intersection(u['depends'])} - downstream
            if not added:
                break
            downstream.update(added)
        downstream.remove(unit['key'])
        if any((r['plan_id'] == plan['id'] and r['unit_key'] in downstream) or
               any(d.get('run_id') == run['id'] for d in r['input'].get('dependencies', ())) for r in runs):
            raise ValueError('原结果已有后续派发，须先核对交接')
        return {'run_id': run['id'], 'artifact': files['reference'], 'result_version': evidence['result_version'],
                'input_version': run['input_version'], 'identity': source['snapshot']['identity'],
                'manifest': files['manifest'], 'checks': files['checks'],
                **({'missing_outputs': missing} if missing else {})}

    @staticmethod
    def dispatch_block(auth, runs):
        if time.time() >= auth['dispatch_before']:
            return '新增派发授权已到期；在途回合仍可核对或停止'
        if len(runs) >= auth['max_attempts_total']:
            return '合作累计尝试次数已到授权上限'
        return None

    def decision(self, db, item, row, *, driver_override=None):
        if hasattr(self.s, 'planning'):
            blocked = self.s.planning.cooperative.flow_block(db, row)
            if blocked:
                return 'BLOCKED', blocked, None
        driver = driver_override or self.s.work_drivers.current(db, item['id'])
        plan = self.s.teamwork.current(db, item['id'])
        if plan and plan.get('file_handoffs'):
            return 'BLOCKED', '同路径交接首批只支持明确单步派发，连续流程尚未开放', None
        auth = self.s.work_units.authorization(db, driver)
        if (not driver or driver['state'] != 'RESERVED' or driver['generation'] != row['driver_generation'] or
                driver['revision'] != row['driver_revision'] or driver['item_revision'] != item['revision'] or
                row['item_revision'] != item['revision'] or not plan or (plan['mode'] != 'fixed' and not organization(self.s, db, plan)) or
                row['plan'] != {k: plan[k] for k in ('id', 'version')} or
                row['authorization'] != self.s.work_units.auth_ref(auth)):
            return 'BLOCKED', '任务、责任、方案或授权版本已变化', None
        _, issues = self.s.work_drivers.readiness(db, item, driver)
        runs = self.s.work_units.rows(db, item['id'])
        current = [r for r in runs if r['plan_id'] == plan['id']]
        waiting = set()
        # Only this instance's exact in-flight unit may be waited for. All other
        # unknowns, historical blockers and unproved exits stop new dispatch.
        for run in current:
            a = self.s.store.attempt(run['attempt_id'], db)
            worker = self.s.workers.get(a['id'])
            stopped_here = (a['stop_requested'] and run.get('flow') and
                            run['flow']['generation'] == row.get('generation'))
            if stopped_here or a['state'] == 'STOP_REQUESTED':
                return 'BLOCKED', '单元已请求停止，先核对终态并明确安排下一步', None
            if worker and worker['thread'].is_alive() and (a['state'] in ACTIVE or a['state'] == 'COMPLETED'):
                waiting.add(a['id'])
        unsafe = [i for i in issues if not (i.get('attempt_id') in waiting and
                  i['code'] in {'CURRENT_TURN', 'LOCAL_WORKER', 'EXIT_UNPROVED', 'NONTERMINAL'})]
        if unsafe:
            return 'BLOCKED', '；'.join(dict.fromkeys(i['message'] for i in unsafe)), None
        if not auth:
            return 'BLOCKED', '缺少当前派发授权', None
        if waiting:
            return 'WAITING', '等待准确单元回合、审批或输入完成', None
        recovery = row.get('chat_recovery')
        if recovery:
            blocked = self.dispatch_block(auth,runs)
            if blocked:
                return 'BLOCKED',blocked,None
            from .cooperation_recovery import target
            if target(self.s,db,item,plan,runs)!=recovery:
                return 'BLOCKED','续跑所依据的回合或产物已变化，请重新核对。',None
            return 'RECOVERY_READY','从确认的中断阶段续跑', {
                'unit':recovery['unit'],'retry_of':recovery['retry_of'],'recovery':digest(recovery)}
        if organization(self.s, db, plan):
            return strategy(self.s, plan).decision(db, item, row, plan, auth, runs)
        unfinished = []
        delivered = []
        for unit in plan['units']:
            history = [r for r in current if r['unit_key'] == unit['key']]
            if not history:
                unfinished.append(unit)
                continue
            run = history[-1]
            evidence = self.s.work_units.evidence(db, run)
            if not evidence['result_available']:
                return 'BLOCKED', '单元 ' + unit['title'] + ' 的准确文字结果不可用；失败或停止须明确重试', None
            if unit.get('outputs'):
                if evidence['file_artifacts']['state'] == 'MISSING':
                    return 'CAPTURE_READY', '保存准确单元产物并执行声明检查', {
                        'run_id': run['id'], 'result_version': evidence['result_version'], 'artifact': None}
                if (auth.get('auto_rework_checks') and (unit.get('checks') or evidence['file_artifacts'].get('missing_outputs')) and
                        evidence['file_artifacts']['check_status'] == 'FAIL'):
                    blocked = self.dispatch_block(auth, runs)
                    if blocked:
                        return 'BLOCKED', blocked, None
                    try:
                        context = self.check_rework(db, plan, unit, run, evidence, runs, auth)
                    except ValueError as exc:
                        return 'BLOCKED', '单元 ' + unit['title'] + '：' + str(exc), None
                    return 'REWORK_READY', '按原授权返工一次', {
                        'unit': {'key': unit['key'], 'revision': digest(unit)}, 'retry_of': run['id'],
                        'check_rework': digest(context), 'context': context}
                try:
                    self.s.work_artifacts.require(evidence,
                                                  'checked' if unit.get('checks') else 'files')
                except ValueError as exc:
                    return 'BLOCKED', '单元 ' + unit['title'] + '：' + str(exc) + '；暂停后保存并检查文件，再明确启动', None
            delivered.append(self.delivered_unit(unit, run, evidence['result_version'], evidence['file_artifacts']))
        if not unfinished:
            return 'RESULTS_AVAILABLE', '全部单元准确结果及声明产物证据齐备；独立审查与使用验收分别记录', delivered
        blocked = self.dispatch_block(auth, runs)
        if blocked:
            return 'BLOCKED', blocked, None
        reasons = []
        for unit in unfinished:
            try:
                self.s.work_units.input_for(db, item, plan, unit, runs)
                return 'READY', '下一单元前置准确结果已具备', {'unit': {'key': unit['key'], 'revision': digest(unit)}}
            except ValueError as exc:
                reasons.append(str(exc))
        return 'BLOCKED', '；'.join(dict.fromkeys(reasons)) or '没有具备准确前置结果的单元', None

    def change(self, db, p, *, recovery=None):
        action = p.get('action')
        extra = {'start_at'} if action == 'schedule' else set()
        if set(p) - ({'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'flow', 'action'} | extra):
            raise ValueError('连续流程操作字段不支持。')
        item = self.s.work_items.checked(db, p)
        old = self.current(db, item['id'])
        if 'flow' not in p or p['flow'] != self.reference(old):
            raise ValueError('连续流程版本已变化，请刷新后操作。')
        if action == 'pause':
            if not old or old['state'] not in ACTIVE_FLOWS:
                raise ValueError('当前没有待启动或运行中的连续流程。')
            reason = '已暂停预约，可重新安排时间或明确启动' if old['state'] == 'SCHEDULED' else '明确暂停新派发；在途回合继续保留'
            row = self.save(db, {**old, 'state': 'PAUSED', 'reason': reason}, 'paused')
        elif action in ('start', 'schedule'):
            self.require_stopped(db, item['id'])
            item, driver, plan = self.s.work_units.checked(db, {k: v for k, v in p.items() if k not in {'action', 'flow'}})
            if plan.get('file_handoffs'):
                raise ValueError('同路径交接首批只支持明确单步派发，连续流程尚未开放。')
            auth = self.s.work_units.authorization(db, driver)
            if not auth or p.get('authorization') != self.s.work_units.auth_ref(auth):
                raise ValueError('连续流程须绑定当前明确派发授权。')
            if p.get('frontier') != self.s.work_units.frontier(db, item['id']):
                raise ValueError('单元执行前沿已变化，请刷新。')
            row = {'id': 'work-flow:' + item['id'], 'generation': uid(), 'item_id': item['id'],
                   'item_revision': item['revision'], 'driver_generation': driver['generation'], 'driver_revision': driver['revision'],
                   'plan': {k: plan[k] for k in ('id', 'version')}, 'authorization': self.s.work_units.auth_ref(auth),
                   'state': 'RUNNING', 'instance': self.s.instance, 'started_frontier': p['frontier'],
                   'previous_generation': old['generation'] if old else None, 'created': time.time(),
                   'reason': ('明确启动主持人—专家连续合作' if plan['mode'] == 'host_experts' and organization(self.s, db, plan)
                              else '明确启动负责人连续协调' if organization(self.s, db, plan)
                              else '明确启动固定流程，串行派发就绪单元'), 'last_unit_run': None,
                   'command_id': self.s.command_identity, 'scheduling': 'CONTINUOUS_SERIAL', 'automatic_retry': False}
            if recovery:
                row['chat_recovery'] = recovery
            state, reason, _ = self.decision(db, item, row)
            if state not in READY_TO_START | {'PROPOSAL_READY','REVISION_READY'}:
                raise ValueError('连续流程尚不可启动：' + reason)
            if action == 'schedule':
                start_at = p.get('start_at')
                if type(start_at) not in (int, float) or not time.time() + 2 <= start_at < auth['dispatch_before']:
                    raise ValueError('预约时间须至少在两秒后，并早于当前派发授权截止。')
                row.update(state='SCHEDULED', start_at=start_at, credential_revision=self.s.locations.credential_revision,
                           reason='已预约一次固定流程，等待到点核对')
            row = self.save(db, row, 'scheduled' if action == 'schedule' else 'started')
        else:
            raise ValueError('连续流程操作无效。')
        return {'flow': row, 'no_dispatch': True}

    def guard_send(self, db, item, p):
        row = self.current(db, item['id'])
        if row and row['state'] == 'SCHEDULED':
            raise ValueError('本任务已预约固定流程；先暂停预约，再派发单元。')
        if row and row['state'] == 'RUNNING':
            if row['instance'] != self.s.instance or p.get('flow') != self.reference(row):
                raise ValueError('连续流程版本已变化或仍持有派发；先暂停再明确单步操作。')
            state, reason, candidate = self.decision(db, item, row)
            if state not in {'READY', 'REWORK_READY', 'RECOVERY_READY'} or any(
                    p.get(k) != candidate.get(k) for k in ('unit', 'retry_of', 'check_rework', 'proposal', 'leader_round', 'recovery')):
                raise ValueError('连续流程当前不能派发该单元：' + reason)
            return row, candidate.get('context'), row.get('chat_recovery') if state=='RECOVERY_READY' else None
        if p.get('flow') is not None or 'check_rework' in p or 'recovery' in p:
            raise ValueError('连续流程已暂停或版本已变化。')
        return None, None, None

    def guard_proposal(self, db, item, p):
        if p.get('flow') is None:
            self.require_stopped(db, item['id'])
            return
        row = self.current(db, item['id'])
        if (not row or self.reference(row) != p['flow'] or row['state'] != 'RUNNING'
                or row['instance'] != self.s.instance):
            raise ValueError('负责人提案的连续流程已暂停或变化。')
        state, reason, candidate = self.decision(db, item, row)
        if state != 'PROPOSAL_READY' or any(p.get(k) != v for k, v in candidate.items()):
            raise ValueError('当前不能保存负责人提案：' + reason)

    def guard_capture(self, db, item, p):
        if p.get('flow') is None:
            self.require_stopped(db, item['id'])
            return
        row = self.current(db, item['id'])
        if (not row or self.reference(row) != p['flow'] or row['state'] != 'RUNNING'
                or row['instance'] != self.s.instance):
            raise ValueError('自动产物保存的连续流程版本已变化或已暂停。')
        state, reason, candidate = self.decision(db, item, row)
        if state != 'CAPTURE_READY' or any(p.get(k) != v for k, v in candidate.items()):
            raise ValueError('连续流程当前不能保存该产物：' + reason)

    def bound(self, db, run, item_revision, driver):
        if not run.get('flow'):
            return
        row = self.current(db, run['item_id'])
        if self.reference(row) != run['flow'] or row['state'] != 'RUNNING':
            raise ValueError('连续流程事务归属已变化。')
        if run['input'].get('resume_from'):
            del row['chat_recovery']
        self.save(db, {**row, 'item_revision': item_revision, 'driver_revision': driver['revision'],
                       'last_unit_run': run['id'], 'reason': ('已派发检查返工，完成后重新检查产物'
                           if run['input'].get('check_rework') else '单元已由相同命令事务派发')}, 'unit_linked')

    def latest_delivery(self, db, row):
        if not row or row['state'] == 'RESULTS_AVAILABLE':
            return row
        saved = db.execute(
            "SELECT data FROM collaboration WHERE kind='work_flow_revision' "
            "AND json_extract(data,'$.item_id')=? AND json_extract(data,'$.state')='RESULTS_AVAILABLE' "
            "ORDER BY json_extract(data,'$.updated') DESC,rowid DESC LIMIT 1", (row['item_id'],)).fetchone()
        if saved:
            prior = json.loads(saved[0])
            return {**prior, 'id': prior['flow_id']}
        return None

    def legacy_delivery(self, db, record, plan, runs):
        """Recover pre-upgrade versions using the recorded settlement boundary."""
        entries = []
        for unit in plan['units']:
            run = next((r for r in reversed(runs) if r['plan_id'] == plan['id'] and r['unit_key'] == unit['key']
                        and r['created'] <= record['updated']), None)
            if not run:
                return []
            if not unit.get('outputs'):
                version = self.s.work_units.base_evidence(db, run)['version']
                files = {'reference': None, 'manifest': {}, 'check_status': 'NOT_RECORDED'}
            else:
                raw = db.execute(
                    "SELECT data FROM collaboration WHERE kind='work_unit_artifact' "
                    "AND json_extract(data,'$.snapshot.run_id')=? AND json_extract(data,'$.created')<=? "
                    "ORDER BY json_extract(data,'$.created') DESC,rowid DESC LIMIT 1",
                    (run['id'], record['updated'])).fetchone()
                if not raw:
                    return []
                artifact = json.loads(raw[0])
                snapshot = artifact['snapshot']
                if (digest(snapshot) != artifact['version'] or snapshot['unit_revision'] != run['unit_revision'] or
                        snapshot['check_status'] != ('PASS' if unit.get('checks') else 'NOT_RECORDED')):
                    return []
                version = snapshot['result_version']
                files = {'reference': self.s.work_artifacts.reference(artifact), 'manifest': snapshot['manifest'],
                         'check_status': snapshot['check_status']}
            entries.append(self.delivered_unit(unit, run, version, files))
        return entries

    @staticmethod
    def delivery_units(plan, entries):
        upstream = {key for unit in plan['units'] for key in unit['depends']}
        roles = {unit['key']: unit['role'] for unit in plan['units']}
        return [{**entry, 'final': entry['key'] not in upstream and roles[entry['key']] != 'coordinate'}
                for entry in entries if entry['key'] in roles]

    def saved_delivery(self, db, completed, runs):
        if not completed:
            return None
        plan = self.s.collaboration.get(db, 'teamwork_plan', completed['plan']['id'])
        if plan['version'] != completed['plan']['version']:
            return None
        entries = completed.get('delivery')
        if entries is None:
            entries = self.legacy_delivery(db, completed, plan, runs)
        if not entries:
            return None
        return {'state': 'SAVED', 'recorded_at': completed['updated'],
                'units': self.delivery_units(plan, entries)}

    def view(self, db, item):
        row = self.current(db, item['id'])
        delivery = None
        result_issues = []
        completed = self.latest_delivery(db, row)
        if completed:
            plan = self.s.collaboration.get(db, 'teamwork_plan', completed['plan']['id'])
            runs = self.s.work_units.rows(db, item['id'])
            entries = completed.get('delivery')
            if entries is None:
                entries = self.legacy_delivery(db, completed, plan, runs)
            current_plan = self.s.teamwork.current(db, item['id'])
            if (not current_plan or completed['plan'] != {k: current_plan[k] for k in ('id', 'version')} or
                    completed['item_revision'] != item['revision']):
                result_issues.append('任务或方案版本已变化')
            elif row['state'] == 'RESULTS_AVAILABLE':
                exact = {entry['key']: entry for entry in entries}
                for unit in plan['units']:
                    history = [r for r in runs if r['plan_id'] == plan['id'] and r['unit_key'] == unit['key']]
                    try:
                        resolved = self.s.work_units.resolve(db, plan, unit['key'], runs) if organization(self.s, db, plan) else None
                        if not history and not (resolved and resolved['run']):
                            raise ValueError('缺少准确运行')
                        gate = 'checked' if unit.get('checks') else 'files' if unit.get('outputs') else 'result'
                        run = resolved['run'] if resolved else history[-1]
                        if resolved and resolved['problems']:
                            raise ValueError('；'.join(resolved['problems']))
                        evidence = resolved['evidence'] if resolved else self.s.work_units.evidence(db, run)
                        problems = evidence['file_artifacts']['problems']
                        if problems:
                            raise ValueError('；'.join(problems))
                        self.s.work_artifacts.require(evidence, gate)
                        if self.delivered_unit(unit, run, evidence['result_version'], evidence['file_artifacts']) != exact.get(unit['key']):
                            raise ValueError('当前结果与原交付版本不同')
                    except ValueError as exc:
                        result_issues.append(unit['title'] + '：' + str(exc))
            if entries:
                delivery = {'state': 'CURRENT' if row['state'] == 'RESULTS_AVAILABLE' and not result_issues else 'HISTORICAL',
                            'recorded_at': completed['updated'],
                            'units': self.delivery_units(plan, entries)}
        return {'record': row, 'reference': self.reference(row), 'running': bool(row and row['state'] == 'RUNNING'),
                'holds_dispatch': bool(row and row['state'] in ACTIVE_FLOWS),
                'result_evidence_current': not result_issues if row and row['state'] == 'RESULTS_AVAILABLE' else None,
                'result_evidence_issues': result_issues,
                'delivery': delivery,
                'can_pause': bool(row and row['state'] in ACTIVE_FLOWS), 'restart_policy': 'PAUSE_AND_EXPLICIT_RECHECK',
                'automatic_retry': False}

    def schedule_block(self, db, item, row):
        if time.time() >= row['start_at'] + SCHEDULE_WINDOW_SECONDS:
            return '已错过预约启动窗口，请重新安排时间或明确启动'
        if row['credential_revision'] != self.s.locations.credential_revision:
            return '工作端凭据引用已变化，预约已暂停'
        driver = self.s.work_drivers.current(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        if not auth or time.time() >= auth['dispatch_before']:
            return '原派发授权已失效，预约已暂停'
        if row['started_frontier'] != self.s.work_units.frontier(db, item['id']):
            return '单元执行前沿已变化，预约已暂停'
        return None

    def tick(self):
        # The existing scheduler supplies wakeups; only Service.command starts
        # attempts. The process lock and persisted references cover pause races.
        with self.s.lock:
            if self.s.retiring or (self.s.locations.data / 'retired-target.json').exists():
                return
            with self.s.store.read() as db:
                candidates = [r for r in self.s.collaboration.all(db, 'work_flow')
                              if r['state'] in ACTIVE_FLOWS and r['instance'] == self.s.instance
                              and (r['state'] == 'RUNNING' or r['start_at'] <= time.time())]
            for row in candidates:
                command = None
                try:
                    with self.s.store.write() as db:
                        current = self.current(db, row['item_id'])
                        if self.reference(current) != self.reference(row) or current['state'] != row['state']:
                            continue
                        if row['state'] == 'SCHEDULED' and time.time() < row['start_at']:
                            continue
                        item = self.s.work_items.item(db, row['item_id'])
                        state, reason, unit = self.decision(db, item, row)
                        if row['state'] == 'SCHEDULED':
                            blocked = self.schedule_block(db, item, row)
                            if blocked or state not in READY_TO_START:
                                self.save(db, {**row, 'state': 'PAUSED', 'reason': blocked or reason}, 'schedule_paused')
                                continue
                            row = self.save(db, {**row, 'state': 'RUNNING', 'started_at': time.time(),
                                                 'reason': '预约已启动，按原授权继续'}, 'schedule_started')
                        if state in {'BLOCKED', 'RESULTS_AVAILABLE'}:
                            settled = {**row, 'state': 'PAUSED' if state == 'BLOCKED' else state, 'reason': reason}
                            if state == 'RESULTS_AVAILABLE':
                                settled['delivery'] = unit
                            self.save(db, settled, 'settled')
                            continue
                        if state == 'WAITING':
                            continue
                        driver = self.s.work_drivers.current(db, item['id'])
                        plan = self.s.teamwork.current(db, item['id'])
                        if state == 'REVISION_READY':
                            command = ('work-item.host_experts-revision-apply' if plan['mode'] == 'host_experts'
                                       else 'work-item.leader-revision-apply')
                            payload = {**self.scope(db, item, driver, plan), 'flow': self.reference(row),
                                       'authorization': row['authorization'],
                                       'frontier': self.s.work_units.frontier(db, item['id']), **unit}
                        elif state == 'PROPOSAL_READY':
                            command = 'work-item.proposal-check'
                            payload = {**self.scope(db, item, driver, plan), 'flow': self.reference(row),
                                       'authorization': row['authorization'],
                                       'frontier': self.s.work_units.frontier(db, item['id']), **unit}
                        elif state == 'CAPTURE_READY':
                            command = 'work-item.unit-check'
                            payload = {**self.scope(db, item, driver, plan), 'flow': self.reference(row), **unit}
                        else:
                            command = 'work-item.unit-send'
                            payload = {**self.scope(db, item, driver, plan), 'authorization': row['authorization'],
                                       'frontier': self.s.work_units.frontier(db, item['id']), 'flow': self.reference(row),
                                       **{k: v for k, v in unit.items() if k != 'context'}}
                    prefix = ('workdesk-flow-revision:' if command in {'work-item.leader-revision-apply', 'work-item.host_experts-revision-apply'} else
                              'workdesk-flow-proposal:' if command == 'work-item.proposal-check' else
                              'workdesk-flow-capture:' if command == 'work-item.unit-check' else 'workdesk-flow-step:')
                    command_id = str(uuid.uuid5(uuid.NAMESPACE_URL, prefix + digest(payload)))
                    self.s.command(command, payload, command_id)
                except Exception as exc:
                    with self.s.store.write() as db:
                        current = self.current(db, row['item_id'])
                        if self.reference(current) == self.reference(row) and current['state'] in ACTIVE_FLOWS:
                            action = '产物保存未完成' if command == 'work-item.unit-check' else '流程推进未完成'
                            self.save(db, {**row, 'state': 'PAUSED', 'reason': f'{action}：{str(exc)[:1200]}'}, 'error_paused')
