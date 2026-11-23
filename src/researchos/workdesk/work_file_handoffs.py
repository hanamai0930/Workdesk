"""Exact read-only same-path handoff previews. No configuration or execution grant."""
import json
import time

from . import workspace, work_file_reworks, work_file_sources
from .store import digest
from .work_file_handoff_config import file_paths, relation_problems
from .work_transitions import plan_ref
from .work_unit_revisions import require_history


PROTOCOL = 'SERIAL_FILE_HANDOFF_PREVIEW_V1'


class WorkFileHandoffs:
    def __init__(self, service):
        self.s = service

    def handlers(self):
        return {'work-item.file-handoff-preview': self.preview}

    @staticmethod
    def reference(row):
        return {k: row[k] for k in ('id', 'version')} if row else None

    def current(self, db, item_id):
        row = db.execute("SELECT data FROM collaboration WHERE kind='work_file_handoff_preview_current' AND id=?",
                         ('file-handoff-current:' + item_id,)).fetchone()
        return self.s.collaboration.get(db, 'work_file_handoff_preview', json.loads(row[0])['preview_id']) if row else None

    def candidate(self, db, p):
        item, driver, plan = self.s.work_units.checked(db, p)
        self.s.work_flows.require_stopped(db, item['id'])
        rows = self.s.work_units.rows(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        if (p.get('authorization') != self.s.work_units.auth_ref(auth) or
                p.get('frontier') != self.s.work_units.frontier(db, item['id'])):
            raise ValueError('交接预览的授权或完整前沿已变化，请刷新。')
        source = p.get('source')
        target = p.get('target')
        if (not isinstance(source, dict) or set(source) != {'run_id', 'evidence_version', 'artifact'} or
                not isinstance(source['run_id'], str) or not isinstance(source['evidence_version'], str) or
                not isinstance(target, dict) or set(target) != {'key', 'revision'} or
                not isinstance(target['key'], str)):
            raise ValueError('交接预览须选择准确前版与后继单元。')
        run = next((r for r in rows if r['id'] == source['run_id'] and r['plan_id'] == plan['id']), None)
        unit = next((u for u in plan['units'] if u['key'] == target['key']), None)
        if not run or not unit or digest(unit) != target['revision']:
            raise ValueError('前版运行或后继单元版本已变化。')
        original = self.s.work_artifacts.unit(db, run)
        latest = next(r for r in reversed(rows) if r['plan_id'] == plan['id'] and r['unit_key'] == run['unit_key'])
        evidence = self.s.work_units.evidence(db, run)
        if (latest['id'] != run['id'] or source['evidence_version'] != evidence['version'] or
                source['artifact'] != evidence['file_artifacts']['reference']):
            raise ValueError('前版结果或当前文件快照已变化；旧选择不能重新绑定。')
        paths = p.get('paths')
        if (not isinstance(paths, list) or not paths or len(paths) > 20 or
                any(not isinstance(n, str) or workspace.relative(n) != n for n in paths) or
                len({n.casefold() for n in paths}) != len(paths) or
                set(paths) != set(original['outputs'])):
            raise ValueError('首批交接路径须为准确前版的全部 outputs 文件集合。')
        paths = file_paths(paths)
        blockers = relation_problems(original, unit, paths, plan['units'], plan['max_parallel'],
                                     require_target_outputs=False)
        configuration = []
        if set(unit['outputs']) != set(paths):
            configuration.append({'field': 'outputs', 'current': unit['outputs'], 'required': paths,
                                  'state': 'EXPLICIT_PLAN_CONFIGURATION_REQUIRED'})
        if any(r['plan_id'] == plan['id'] and r['unit_key'] == unit['key'] for r in rows):
            blockers.append('后继已有运行；首批只预览尚未运行的整合单元')
        try:
            self.s.work_artifacts.require(evidence, 'checked' if original['checks'] or unit['dependency_gate'] == 'checked' else 'files')
            require_history(self.s, db, rows)
            self.s.work_units.input_for(db, item, plan, unit, rows)
        except ValueError as exc:
            blockers.append(str(exc))
        files = [work_file_reworks.original_files(self.s, db, r) for r in rows]
        if any(f['input_problems'] or f['output_state'] == 'DAMAGED' for f in files):
            blockers.append('原实际输入或全部已登记旧快照损坏，须恢复准确历史')
        context = work_file_sources.source_context(self.s, db, plan, rows, files)
        if any(not r['valid'] for r in context['resources']):
            blockers.append('原成员、连接或额度来源已变化')
        if not auth or len(rows) >= auth['max_attempts_total']:
            blockers.append('原累计授权次数不足；预览不增加授权')
        if not auth or time.time() >= auth['dispatch_before']:
            blockers.append('原新增派发截止已到期；预览不延长截止')
        return {'protocol': PROTOCOL, 'item_id': item['id'], 'item_revision': item['revision'],
                'plan': plan_ref(plan), 'driver': driver, 'authorization': auth,
                'frontier': p['frontier'], 'work_requirements': plan['work_requirements'],
                'source': {'run_id': run['id'], 'run_version': digest(run), 'unit': original,
                           'evidence': evidence}, 'target': unit, 'paths': paths,
                'original_files': files, 'history_context': context,
                'current_delivery': evidence['file_artifacts'],
                'actual_source_input': next(f for f in files if f['run_id'] == run['id'])['actual_input_manifest'],
                'required_configuration': configuration, 'blockers': list(dict.fromkeys(blockers)),
                'state': 'BLOCKED' if blockers else 'REVIEW_ONLY',
                'usage': self.s.work_units.usage(db, rows), 'execution_permission': False,
                'runtime_state': 'FORMAL_DELIVERY_AND_COMPLETION_RECORDS_NOT_IMPLEMENTED',
                'no_dispatch': True, 'project_files_written': False,
                'scope': 'PREVIEW_ONLY; ORIGINAL_REQUIREMENTS_RESOURCES_CAP_DEADLINE_PERMISSIONS'}

    def preview(self, db, p):
        if set(p) != {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier',
                      'source', 'target', 'paths', 'preview'}:
            raise ValueError('同路径交接预览字段不支持。')
        if p['preview'] != self.reference(self.current(db, p['id'])):
            raise ValueError('交接预览版本已变化，请刷新。')
        snapshot = self.candidate(db, p)
        version = digest({'request': p, 'snapshot': snapshot})
        row = {'id': 'file-handoff-preview:' + version, 'version': version,
               'request': p, 'snapshot': snapshot, 'item_id': p['id'],
               'created': time.time(), 'command_id': self.s.command_identity}
        self.s.collaboration.put(db, 'work_file_handoff_preview', row)
        self.s.collaboration.put(db, 'work_file_handoff_preview_current',
                                 {'id': 'file-handoff-current:' + p['id'], 'preview_id': row['id']})
        return {'file_handoff_preview': row, 'no_dispatch': True, 'project_files_written': False,
                'execution_permission': False}

    def view(self, db, item):
        plan = self.s.teamwork.current(db, item['id'])
        rows = self.s.work_units.rows(db, item['id'])
        sources = []
        if plan:
            for unit in plan['units']:
                run = next((r for r in reversed(rows) if r['plan_id'] == plan['id'] and r['unit_key'] == unit['key']), None)
                if not run or not unit['outputs']:
                    continue
                e = self.s.work_units.evidence(db, run)
                sources.append({'title': unit['title'], 'unit_key': unit['key'], 'paths': unit['outputs'],
                                'reference': {'run_id': run['id'], 'evidence_version': e['version'],
                                              'artifact': e['file_artifacts']['reference']}})
        row = self.current(db, item['id'])
        problems = []
        if row:
            try:
                if (digest({'request': row['request'], 'snapshot': row['snapshot']}) != row['version'] or
                        row['id'] != 'file-handoff-preview:' + row['version'] or row['item_id'] != item['id'] or
                        self.candidate(db, row['request']) != row['snapshot']):
                    raise ValueError('原预览绑定的来源、配置或历史已变化；须重新选择核对')
            except (ValueError, OSError) as exc:
                problems.append(str(exc))
        return {'protocol': PROTOCOL, 'sources': sources,
                'targets': [{'key': u['key'], 'revision': digest(u), 'title': u['title']} for u in plan['units']] if plan else [],
                'record': row, 'reference': self.reference(row),
                'state': 'STALE' if problems else row['snapshot']['state'] if row else 'NOT_RECORDED',
                'problems': problems, 'execution_permission': False}
