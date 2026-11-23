"""Natural-language management through the selected official Agent and public handlers."""
import copy
import json
import time
from uuid import NAMESPACE_URL, uuid5

from .contracts import bounded_text
from .management_contracts import KINDS, parse, prompt
from .store import ACTIVE, UNKNOWN, canonical, digest, uid


KIND = 'management_request'


class WorkManagement:
    def __init__(self, service):
        self.s = service

    def handlers(self):
        return {'management.submit': self.submit, 'management.check': self.check,
                'management.cancel': self.cancel}

    def get(self, db, identity):
        return self.s.collaboration.get(db, KIND, identity)

    def save(self, db, row, state, **values):
        row = {**row, **values, 'state': state, 'revision': row.get('revision', 0) + 1, 'updated': time.time()}
        self.s.collaboration.put(db, KIND, row)
        self.s.store.event(row.get('task_id'), row.get('attempt_id'), 'management_' + state.lower(),
                           {'request_id': row['id'], 'revision': row['revision'],
                            'scope': row['scope'], 'reason': row.get('reason'), 'no_resend': True}, db)
        return {'request': row, 'no_dispatch': True, 'no_resend': True}

    def catalog(self):
        with self.s.store.read() as db:
            rows = self.s.collaboration.all(db, KIND)
            scenes = self.s.scenes.catalog(db)['presets']
            profiles = self.s.profiles.list(db)
            resources = self.s.resources.public_catalog(db, profiles)
            parser_models = []
            for profile in profiles:
                connection = next((item for item in resources['connections']
                                   if item['provider'] == profile['id'] and item['is_default']), None)
                if not connection:
                    continue
                for entry in profile['model_catalog']['models']:
                    efforts = entry.get('selection_efforts', entry.get('efforts', []))
                    tiers = entry.get('tiers', [])
                    if not entry.get('runnable') or not efforts or not tiers:
                        continue
                    parser_models.append({'provider': profile['id'], 'model': entry['model'],
                        'available': connection['available'], 'selection': {
                            'provider': profile['id'], 'model': entry['model'],
                            'effort': profile['effort'] if profile['effort'] in efforts else efforts[0],
                            'tier': profile['tier'] if profile['tier'] in tiers else tiers[0],
                            'connection_id': connection['id'], 'connection_revision': connection['revision']}})
            appearance = getattr(self.s.ui_features, 'appearance', None)
            return {'requests': rows[:100], 'scenes': scenes,
                    'members': resources['members'], 'connections': resources['connections'],
                    'endpoints': resources['endpoints'], 'parser_models': parser_models,
                    'settings': appearance.catalog(db) if appearance else None,
                    'scope_kinds': sorted(KINDS), 'authorization': 'ORIGINAL_USER_REQUEST',
                    'no_dispatch': True, 'no_resend': True}

    def detail(self, identity):
        with self.s.store.read() as db:
            row = self.get(db, identity)
            result = {'request': row, 'no_dispatch': True, 'no_resend': True}
            if row.get('attempt_id'):
                a = self.s.store.attempt(row['attempt_id'], db)
                result['execution'] = {key: a[key] for key in ('id', 'task_id', 'state', 'owner', 'session_id', 'turn_id')}
            return result

    def target(self, db, scope):
        kind = scope['kind']
        fields = {'kind', 'intent', 'revision'} if kind == 'settings' else (
            {'kind', 'intent', 'project_id'} if kind == 'development' else {'kind', 'intent', 'target_id', 'revision'})
        if set(scope) - fields or (kind != 'development' and set(scope) != fields):
            raise ValueError('管理作用范围须引用该类操作的准确目标与版本。')
        if kind == 'settings':
            appearance = getattr(self.s.ui_features, 'appearance', None)
            if not appearance:
                raise ValueError('外观设置管理尚未接通。')
            return appearance.current(db)
        if kind in {'scene', 'skills'}:
            return self.s.collaboration.get(db, 'scene_preset', scope.get('target_id'))
        if kind == 'member':
            return self.s.resources.saved(db, 'member', scope.get('target_id'))
        return {'project_id': scope.get('project_id')}

    def context(self, db, row):
        kind = row['scope']['kind']
        result = {'target': row['target']}
        recent = [entry for entry in self.s.collaboration.all(db, KIND)
                  if entry['state'] == 'APPLIED' and (entry['scope']['kind'] == kind
                  or kind == 'scene' and entry['scope']['kind'] == 'skills')
                  and entry['scope'].get('target_id') == row['scope'].get('target_id')][:5]
        result['recent_changes'] = [{'request_id': entry['id'], 'text': entry['text'],
                                     'restore': entry.get('outcome', {}).get('restore')} for entry in recent]
        if kind == 'skills':
            catalog = self.s.fixed_preparation.public_skills.catalog()
            result['public_skills'] = {'repository': catalog['repository'], 'skills': [
                {key: skill[key] for key in ('name', 'description', 'commit')} for skill in catalog['skills']]}
        if kind == 'settings':
            result['appearance'] = self.s.ui_features.appearance.catalog(db)
        if kind == 'member':
            result['endpoints'] = self.s.resources.catalog(db)['endpoints']
        return result

    def submit(self, db, p):
        if set(p) not in ({'text', 'scope', 'member_id'}, {'text', 'scope', 'parser_selection'}):
            raise ValueError('须提供原请求、作用范围和准确的解析模型。')
        parser_selection = p.get('parser_selection')
        if 'parser_selection' in p:
            from .execution_resources import PDCA_MODEL_FIELDS
            if (not isinstance(parser_selection, dict) or set(parser_selection) != PDCA_MODEL_FIELDS
                    or any(not isinstance(value, str) or not 1 <= len(value) <= 200
                           for value in parser_selection.values())):
                raise ValueError('请选择有效的大模型、型号和当前工作端连接。')
        text = bounded_text(p.get('text'), '管理请求', 12000)
        scope = p.get('scope')
        if (not isinstance(scope, dict) or set(scope) - {'kind', 'target_id', 'revision', 'intent', 'project_id'}
                or scope.get('kind') not in KINDS or scope.get('intent') not in {'change', 'inspect', 'install'}):
            raise ValueError('管理作用范围无效。')
        kind = scope['kind']
        if (kind != 'skills' and scope['intent'] != 'change' or
                kind == 'skills' and scope['intent'] not in {'inspect', 'install'}):
            raise ValueError('请选择查找、查找并安装或调整的准确意图。')
        if kind == 'development':
            self.s.work_items.project(db, scope.get('project_id'))
            if scope.get('target_id') or scope.get('revision') is not None:
                raise ValueError('开发工作只登记新任务，不覆盖已有工作。')
        target = self.target(db, scope)
        if kind != 'development' and scope.get('revision') != target.get('revision'):
            raise ValueError('目标版本已变化，请刷新作用范围。')
        row = {'id': 'management:' + uid(), 'text': text, 'scope': copy.deepcopy(scope),
               'target': copy.deepcopy(target), 'target_hash': digest(target),
               'member_id': p.get('member_id'), 'created': time.time(),
               'authorization': {'source': 'ORIGINAL_USER_REQUEST', 'text_hash': digest(text),
                                 'no_additional_approval': True}}
        row['scope_hash'] = digest({'scope': scope, 'target_hash': row['target_hash'], 'text_hash': digest(text)})
        # Persist exact waiting conditions. No automatic retry or account switching.
        try:
            if parser_selection is not None:
                row['parser_selection'] = copy.deepcopy(parser_selection)
                reference = self.s.resources.pdca_model(db, parser_selection)
                row['member_id'] = reference['member_id']
            selected = self.s.resources.selection(db, row['member_id'])
            self.s.resources.check_connection(selected['connection'], db)
            context = self.context(db, row)
        except (ValueError, OSError) as exc:
            return self.save(db, row, 'WAITING_INPUT', reason=str(exc))
        member = selected['member']
        payload = {key: member[key] for key in ('provider', 'model', 'effort', 'tier')}
        payload.update(goal=prompt(row, context), work_scene='development', access='workspace',
                       resource_selection=selected['reference'])
        # Use the official independent execution route, with a fresh parsing
        # record and no scene Skill injection. This folder is a working copy,
        # not a claim of filesystem or network isolation.
        payload, _ = self.s.resources.before(db, 'independent.send', payload)
        from .official_work import send
        self.s.monitor.guard(db, payload)
        receipt = send(self.s, db, payload)
        task = self.s.store.task(receipt['task_id'], db)
        attempt = self.s.store.attempt(receipt['launch_attempt'], db)
        row.update(task_id=task['id'], attempt_id=attempt['id'], owner=attempt['owner'],
                   spec_hash=task['spec_hash'], session_id=attempt['session_id'],
                   selection=selected['reference'], parser_context=context)
        saved = self.save(db, row, 'PARSING')['request']
        return {**receipt, 'request': saved}

    def checked(self, db, p):
        if set(p) != {'id', 'revision'}:
            raise ValueError('须提供准确管理请求与修订号。')
        row = self.get(db, p['id'])
        if type(p['revision']) is not int or row['revision'] != p['revision']:
            raise ValueError('管理请求已变化，请刷新。')
        return row

    def evidence(self, db, row):
        attempt = self.s.store.attempt(row['attempt_id'], db)
        task = self.s.store.task(row['task_id'], db)
        worker = self.s.workers.get(attempt['id'])
        if attempt['state'] in ACTIVE or worker and worker['thread'].is_alive():
            return None, 'RUNNING'
        if attempt['state'] in UNKNOWN:
            return None, '运行回执未知，请核对原回合；未重新发送。'
        if attempt['state'] != 'COMPLETED' or attempt['stop_requested']:
            return None, '解析回合未正常完成；未执行管理变更。'
        result = attempt.get('result') or {}
        if (attempt['owner'] != row['owner'] or attempt['task_id'] != row['task_id']
                or task['spec_hash'] != row['spec_hash'] or digest(task['spec']) != row['spec_hash']
                or not result.get('known_tools_exited') or (result.get('owned_job') or {}).get('active_processes') != 0
                or any(result.get(k) != row[k] for k in ('task_id', 'attempt_id', 'owner'))):
            raise ValueError('解析回合身份、配置或退出证据不一致。')
        event = db.execute("SELECT seq,payload FROM events WHERE task_id=? AND attempt_id=? AND kind='attempt_result_saved' ORDER BY seq DESC LIMIT 1",
                           (row['task_id'], row['attempt_id'])).fetchone()
        if not event or json.loads(event['payload']) != result or result.get('state') != 'COMPLETED':
            raise ValueError('解析回合原始结果回执不一致。')
        if db.execute("SELECT 1 FROM requests WHERE attempt_id=? AND state='UNKNOWN'", (attempt['id'],)).fetchone():
            raise ValueError('解析回合仍有未知模型回执。')
        if task['spec']['provider'] == 'codex' and result.get('execution') == 'codex_native':
            self.s.fixed_preparation.codex_terminal(db, attempt, event['seq'])
        elif attempt['session_id'] != row['session_id']:
            raise ValueError('解析回合会话身份已变化。')
        return result, {'result_hash': digest(result), 'event_seq': event['seq'],
                        'attempt_id': attempt['id'], 'session_id': attempt['session_id'], 'turn_id': attempt['turn_id']}

    def check(self, db, p):
        row = self.checked(db, p)
        if row['state'] not in {'PARSING', 'NEEDS_RECONCILIATION'}:
            return {'request': row, 'no_dispatch': True, 'no_resend': True}
        try:
            result, evidence = self.evidence(db, row)
            if result is None:
                if evidence == 'RUNNING':
                    return {'request': row, 'no_dispatch': True, 'no_resend': True}
                return self.save(db, row, 'NEEDS_RECONCILIATION', reason=evidence)
            proposal = parse(result.get('answer'), row)
            if digest(self.target(db, row['scope'])) != row['target_hash']:
                raise ValueError('目标当前版本已变化，旧提案未应用；请按新版本重新提出请求。')
            if proposal['operation'] == 'wait':
                return self.save(db, row, 'WAITING_INPUT', proposal=proposal, evidence=evidence,
                                 reason=bounded_text(proposal['arguments'].get('reason'), '等待条件', 2000))
            # One transaction covers the validated operation and its receipt.
            # Savepoint permits a durable failure record without partial DB writes.
            db.execute('SAVEPOINT management_apply')
            try:
                outcome = self.apply(db, row, proposal)
            except Exception:
                db.execute('ROLLBACK TO management_apply')
                raise
            finally:
                db.execute('RELEASE management_apply')
            return self.save(db, row, 'APPLIED', proposal=proposal, evidence=evidence, outcome=outcome,
                             reason='', effect=outcome['effect'])
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return self.save(db, row, 'FAILED', reason=str(exc), proposal=locals().get('proposal'),
                             evidence=locals().get('evidence'), no_partial_database_apply=True)

    def apply(self, db, row, proposal):
        op, args, target = proposal['operation'], proposal['arguments'], row['target']
        if op == 'management.restore':
            allowed = {entry['request_id'] for entry in row['parser_context'].get('recent_changes', [])}
            if set(args) != {'request_id'} or args['request_id'] not in allowed:
                raise ValueError('撤回必须引用本作用范围内已有准确变更。')
            source = self.get(db, args['request_id'])
            restore = source.get('outcome', {}).get('restore') or {}
            previous = restore.get('previous')
            command = restore.get('command')
            expected = {'settings': 'settings.appearance.save', 'scene': 'scene.save', 'member': 'resource.member-save'}[row['scope']['kind']]
            if source['state'] != 'APPLIED' or command != expected or not isinstance(previous, dict):
                raise ValueError('原变更没有适用的准确恢复快照。')
            if row['scope']['kind'] != 'settings' and previous.get('id') != target['id']:
                raise ValueError('恢复快照与所选目标不一致。')
            if command == 'scene.save':
                fields = ('id', 'scene', 'name', 'prompt', 'output', 'capabilities', 'extensions')
                payload = {key: previous[key] for key in fields}
                payload['revision'] = target['revision']
                receipt = self.s.scenes.save(db, payload)
            elif command == 'settings.appearance.save':
                receipt = self.s.ui_features.appearance.save(db, {'schema': 1, 'revision': target['revision'], 'themes': previous['themes']})
            else:
                fields = ('id', 'name', 'role', 'provider', 'model', 'effort', 'tier', 'connection_id', 'enabled')
                payload = {key: previous[key] for key in fields}
                payload['revision'] = target['revision']
                receipt = self.s.resources.member_save(db, payload)
            return {'command': command, 'restored_request': source['id'], 'receipt': receipt,
                    'effect': source['outcome']['effect'],
                    'restore': {'command': command, 'previous': target, 'requires_current_revision': True}}
        if op == 'settings.appearance.save':
            patches = args.get('themes')
            if not isinstance(patches, dict):
                raise ValueError('外观调整须提供主题区域字段。')
            themes = copy.deepcopy(target['themes'])
            for theme, value in patches.items():
                if theme not in themes or not isinstance(value, dict) or set(value) - {'regions'}:
                    raise ValueError('外观调整主题字段无效。')
                regions = value.get('regions', {})
                if not isinstance(regions, dict):
                    raise ValueError('外观调整区域字段无效。')
                for identity, values in regions.items():
                    if not isinstance(values, dict):
                        raise ValueError('外观调整颜色字段无效。')
                    themes[theme]['regions'][identity] = {**themes[theme]['regions'].get(identity, {}), **values}
            receipt = self.s.ui_features.appearance.save(db, {'schema': 1, 'revision': target['revision'], 'themes': themes})
            return {'command': op, 'receipt': receipt, 'effect': 'APPLICATION_APPEARANCE',
                    'restore': {'command': op, 'previous': target, 'requires_current_revision': True}}
        if op == 'scene.save':
            payload = {key: target[key] for key in ('id', 'revision', 'scene', 'name', 'prompt', 'output', 'capabilities', 'extensions')}
            receipt = self.s.scenes.save(db, {**payload, **args})
            return {'command': op, 'receipt': receipt, 'effect': 'NEW_WORK_ONLY',
                    'restore': {'command': op, 'previous': payload, 'requires_current_revision': True}}
        if op == 'resource.member-save':
            payload = {key: target[key] for key in ('id', 'revision', 'name', 'role', 'provider', 'model', 'effort', 'tier', 'connection_id', 'enabled')}
            receipt = self.s.resources.member_save(db, {**payload, **args})
            return {'command': op, 'receipt': receipt, 'effect': 'FUTURE_MEMBER_SELECTIONS',
                    'restore': {'command': op, 'previous': payload, 'requires_current_revision': True}}
        if op == 'development.register':
            receipt = self.s.work_items.create(db, {'project_id': row['scope'].get('project_id'),
                'title': bounded_text(args.get('title'), '开发工作名称', 160),
                'goal': row['text'] + '\n\n解析建议：\n' + bounded_text(args.get('goal'), '开发目标', 3000),
                'acceptance': bounded_text(args.get('acceptance', '按用户原请求完成修改与相称检查。'), '完成要求', 4000),
                'constraints_text': '程序功能修改。原请求与授权范围见管理记录 ' + row['id'],
                'next_step': '通过现有工作入口安排开发；尚未执行程序修改。'})
            return {'command': 'work-item.create', 'receipt': receipt, 'effect': 'DEVELOPMENT_WORK_REGISTERED',
                    'href': '#work-items', 'restore': {'command': 'work-item.update', 'state': 'CANCELLED'}}
        catalog = self.s.fixed_preparation.public_skills.catalog()
        if op == 'skill.search':
            query = bounded_text(args.get('query'), 'Skill 查找词', 500).casefold()
            words = query.split()
            found = [skill for skill in catalog['skills'] if any(word in (skill['name'] + ' ' + skill['description']).casefold() for word in words)]
            return {'command': op, 'matches': found, 'repository': catalog['repository'],
                    'effect': 'READ_ONLY_SEARCH', 'installed': False}
        selection = {**args, 'scene': target['scene']}
        name, files, origin = self.s.fixed_preparation.public_skills.selected(selection)
        inspected = self.s.capabilities.inspect_files(files, 'skill', name)
        preview = {'name': name, 'origin': origin, 'content_hash': inspected['content_hash'],
                   'files': [{'path': path, 'bytes': len(inspected['files'][path]), 'sha256': sha} for path, sha in sorted(inspected['hashes'].items())],
                   'target': {'id': target['id'], 'revision': target['revision']}}
        capability = self.s.capabilities.import_public_skill(db, name, files, origin)['capability']
        capability = self.s.capabilities.enable(db, {'id': capability['id'], 'revision': capability['revision'], 'enabled': True})['capability']
        payload = {key: target[key] for key in ('id', 'revision', 'scene', 'name', 'prompt', 'output', 'capabilities', 'extensions')}
        payload['capabilities'] = list(dict.fromkeys([*payload['capabilities'], capability['id']]))
        receipt = self.s.scenes.save(db, payload)
        return {'command': 'capability.import + scene.save', 'preview': preview, 'preview_hash': digest(preview),
                'authorization': row['authorization'], 'capability': capability, 'receipt': receipt,
                'effect': 'SELECTED_SCENE_NEW_WORK_ONLY',
                'restore': {'command': 'scene.save', 'previous': target, 'requires_current_revision': True,
                            'disable_command': 'capability.enable', 'capability_id': capability['id']}}

    def cancel(self, db, p):
        row = self.checked(db, p)
        if row['state'] == 'APPLIED':
            raise ValueError('该请求已生效；请按当前版本提出撤回请求。')
        receipt = {}
        if row.get('attempt_id'):
            attempt = self.s.store.attempt(row['attempt_id'], db)
            if attempt['state'] in ACTIVE:
                receipt = self.s._stop(db, {'task_id': row['task_id'], 'attempt_id': attempt['id'], 'owner': attempt['owner']})
        return {**receipt, **self.save(db, row, 'CANCELLED', reason='用户取消；未应用提案。')}

    def tick(self):
        # Settlement is a durable local command; never starts or repeats a model.
        with self.s.store.read() as db:
            pending = [row for row in self.s.collaboration.all(db, KIND) if row['state'] == 'PARSING']
        for row in pending:
            with self.s.lock:
                with self.s.store.read() as db:
                    current = self.get(db, row['id'])
                    attempt = self.s.store.attempt(row['attempt_id'], db)
                worker = self.s.workers.get(attempt['id'])
                if (current['revision'] != row['revision'] or current['state'] != 'PARSING'
                        or attempt['state'] in ACTIVE or worker and worker['thread'].is_alive()):
                    continue
                self.s.command('management.check', {'id': row['id'], 'revision': row['revision']},
                               str(uuid5(NAMESPACE_URL, 'management-settle:' + row['id'] + ':' + str(row['revision']))))
