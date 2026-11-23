"""Exact cooperative file snapshots and declared local predicates; no model calls."""
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unicodedata

from . import workspace
from .work_leader import foundation
from .store import digest


def definition(value, *, allow_section_length=False):
    outputs = value.get('outputs', [])
    checks = value.get('checks', [])
    gate = value.get('dependency_gate', 'result')
    if not isinstance(outputs, list) or len(outputs) > 20:
        raise ValueError('产物须为最多20项准确文件路径。')
    outputs = [workspace.relative(n) for n in outputs]
    if len({n.casefold() for n in outputs}) != len(outputs):
        raise ValueError('产物文件路径重复。')
    if gate not in ('result', 'files', 'checked'):
        raise ValueError('依赖解除条件无效。')
    if not isinstance(checks, list) or len(checks) > 40:
        raise ValueError('本地检查最多40项。')
    normalized = []
    for c in checks:
        if not isinstance(c, dict) or c.get('file') not in outputs:
            raise ValueError('检查须引用本单元明确产物。')
        kind = c.get('kind')
        if kind == 'utf8_section_length' and allow_section_length:
            start, end = c.get('start_line'), c.get('end_line')
            minimum, maximum = c.get('min_chars'), c.get('max_chars')
            required = {'file', 'kind', 'start_line', 'end_line', 'min_chars', 'max_chars'}
            if (not required <= set(c) or set(c) - (required | {'counting'})
                    or any(not isinstance(line, str) or not line.strip() or len(line) > 200
                           or line.splitlines() != [line] for line in (start, end))
                    or start == end):
                raise ValueError('正文长度检查须指定两条不同的完整边界行。')
            if c.get('counting', 'unicode_alnum') not in ('unicode_alnum', 'han_digits'):
                raise ValueError('正文计数口径须为字母数字或汉字数字。')
            if type(minimum) is not int or type(maximum) is not int or not 1 <= minimum <= maximum <= workspace.MAX_FILE:
                raise ValueError('正文长度检查须指定有效的最少和最多字数。')
        elif kind not in ('nonempty', 'utf8_contains', 'json_parse'):
            raise ValueError('本地检查类型无效。')
        elif set(c) - {'file', 'kind', 'text'}:
            raise ValueError('本地检查包含未支持的字段。')
        elif kind == 'utf8_contains':
            if not isinstance(c.get('text'), str) or not 1 <= len(c['text']) <= 4000:
                raise ValueError('文本包含检查须提供1至4000字。')
        elif 'text' in c:
            raise ValueError('此检查类型不接受文本参数。')
        normalized.append(dict(c))
    return {'outputs': outputs, 'checks': normalized, 'dependency_gate': gate}


def section_length(text, check):
    lines = text.splitlines()
    start, end = check['start_line'], check['end_line']
    if lines.count(start) != 1 or lines.count(end) != 1:
        return None
    first, last = lines.index(start), lines.index(end)
    if first >= last:
        return None
    if check.get('counting', 'unicode_alnum') == 'han_digits':
        return sum(char.isdecimal() or unicodedata.name(char, '').startswith(
                   ('CJK UNIFIED IDEOGRAPH-', 'CJK COMPATIBILITY IDEOGRAPH-'))
                   for line in lines[first + 1:last] for char in line)
    return sum(char.isalnum() for line in lines[first + 1:last] for char in line)


def section_repeated_outside(text, check):
    """Detect a second complete prose body, allowing a partial abstract quote."""
    lines = text.splitlines()
    first, last = lines.index(check['start_line']), lines.index(check['end_line'])

    def prose(rows):
        return ''.join(char for line in rows
                       if not line.lstrip().startswith(('#', '![', '<!--'))
                       for char in line if char.isalnum())

    body = prose(lines[first + 1:last])
    outside = prose(lines[:first] + lines[last + 1:])
    return bool(body and body in outside)


class WorkArtifacts:
    def __init__(self, service):
        self.s = service

    def handlers(self):
        return {'work-item.unit-check': self.capture}

    def current(self, db, run_id):
        row = db.execute("SELECT data FROM collaboration WHERE kind='work_unit_artifact_current' AND id=?",
                         ('unit-artifact-current:' + run_id,)).fetchone()
        if not row:
            return None
        return self.s.collaboration.get(db, 'work_unit_artifact', json.loads(row[0])['artifact_id'])

    @staticmethod
    def reference(row):
        return {k: row[k] for k in ('id', 'version')} if row else None

    def folder(self, row):
        return self.s.locations.data / 'results' / ('unit-' + row['id'].split(':')[-1]) / 'files'

    def unit(self, db, run):
        plan = self.s.collaboration.get(db, 'teamwork_plan', run['plan_id'])
        unit = next(u for u in plan['units'] if u['key'] == run['unit_key'])
        if plan['version'] != run['plan_version'] or digest(unit) != run['unit_revision']:
            raise ValueError('文件产物单元版本不一致。')
        return unit

    def preview(self, item_id, run_id, version, name):
        from .result_records import preview_file

        if not isinstance(version, str) or not re.fullmatch(r'[a-f0-9]{64}', version):
            raise ValueError('固定成果版本无效。')
        name = workspace.relative(name)
        with self.s.store.read() as db:
            self.s.work_items.item(db, item_id)
            run = self.s.collaboration.get(db, 'work_unit_run', run_id)
            row = self.s.collaboration.get(db, 'work_unit_artifact', 'unit-artifact:' + version)
            snapshot = row['snapshot']
            plan = self.s.collaboration.get(db, 'teamwork_plan', run['plan_id'])
            if (run['id'] != run_id or run['item_id'] != item_id or row['item_id'] != item_id or
                    row['id'] != 'unit-artifact:' + version or not foundation(self.s, db, plan) or
                    snapshot['run_id'] != run_id or snapshot['plan_id'] != run['plan_id'] or
                    snapshot['plan_version'] != run['plan_version'] or snapshot['unit_revision'] != run['unit_revision'] or
                    any(snapshot['identity'][key] != run[key] for key in ('task_id', 'attempt_id', 'owner')) or
                    row['version'] != version or digest(snapshot) != version):
                raise ValueError('固定成果与任务、运行或快照版本不一致。')
            expected = snapshot['manifest'].get(name)
            if not expected or name not in self.unit(db, run).get('outputs', []):
                raise ValueError('文件不属于此固定成果版本。')
        return preview_file(self.folder(row), name, expected)

    def status(self, db, run, evidence):
        unit = self.unit(db, run)
        row = self.current(db, run['id'])
        issues = []
        if row:
            if (digest(row['snapshot']) != row['version'] or row['snapshot']['run_id'] != run['id'] or
                    row['snapshot']['result_version'] != evidence['version'] or not evidence['result_available']):
                issues.append('准确回合或结果版本已变化')
            try:
                manifest = row['snapshot']['manifest']
                attempt = self.s.store.attempt(run['attempt_id'], db)
                if workspace.current_manifest(self.folder(row), manifest) != manifest:
                    issues.append('保存的产物快照已变化或缺失')
                if workspace.current_manifest(attempt['workspace'], manifest) != manifest:
                    issues.append('项目产物已变化或缺失')
                if any(workspace.io_path(Path(attempt['workspace']) / name).exists()
                       for name in row['snapshot'].get('missing_outputs', [])):
                    issues.append('原缺失产物已在项目中出现，须重新核对')
            except (OSError, ValueError) as exc:
                issues.append(str(exc))
        required = bool(unit.get('outputs'))
        state = 'STALE' if issues else 'CURRENT' if row else 'MISSING' if required else 'NOT_REQUIRED'
        check_status = ('STALE' if issues else row['snapshot']['check_status']) if row else 'NOT_RECORDED'
        return {'state': state, 'reference': self.reference(row), 'problems': issues,
                'manifest': row['snapshot']['manifest'] if row else {}, 'check_status': check_status,
                'checks': row['snapshot']['observations'] if row else [],
                **({'missing_outputs': row['snapshot']['missing_outputs']}
                   if row and row['snapshot'].get('missing_outputs') else {}),
                'required_outputs': unit.get('outputs', []), 'required_checks': unit.get('checks', []),
                'independent_review': 'NOT_PERFORMED', 'human_acceptance': 'NOT_RECORDED',
                'provenance': 'OBSERVED_AFTER_EXACT_TURN_NOT_PROOF_OF_AUTHORSHIP',
                'delivery': 'ORIGINAL_PROJECT_FILES_REVALIDATED_AT_DISPATCH'}

    def require(self, evidence, gate):
        if not evidence['result_available']:
            raise ValueError('准确文字结果不可用')
        if gate in ('files', 'checked'):
            files = evidence['file_artifacts']
            if not files['required_outputs'] or files['state'] != 'CURRENT':
                raise ValueError('前置文件产物尚未登记或版本失效')
            if files.get('missing_outputs'):
                raise ValueError('前置声明产物仍缺失')
            if gate == 'checked' and files['check_status'] != 'PASS':
                raise ValueError('前置本地检查尚未全部通过')

    def capture(self, db, p):
        if set(p) - {'id', 'revision', 'current_driver', 'plan', 'run_id', 'result_version', 'artifact', 'flow'}:
            raise ValueError('文件产物检查字段不支持。')
        from .work_coordinator_handoff_post_round_v3_public import is_v3
        plan=self.s.teamwork.current(db,p.get('id'))
        if is_v3(plan):
            from .work_coordinator_handoff_post_round_v3_runtime import WorkCoordinatorHandoffPostRoundV3Runtime
            item=self.s.work_items.checked(db,p); self.s.work_items.project(db,item['project_id'])
            h=WorkCoordinatorHandoffPostRoundV3Runtime(self.s).read(db,plan); driver=h['driver']
            if (p.get('plan')!={k:plan[k] for k in ('id','version')} or p.get('current_driver')!=self.s.work_drivers.reference(driver) or
                    driver['state']!='RESERVED' or driver['instance']!=self.s.instance):
                raise ValueError('V3捕获须绑定完整当前方案、责任及实例。')
        else:
            item, driver, plan = self.s.work_units.checked(db, p)
        self.s.work_flows.guard_capture(db, item, p)
        rows = self.s.work_units.rows(db, item['id'])
        run = next((r for r in rows if r['id'] == p.get('run_id') and r['plan_id'] == plan['id']), None)
        if not run or next(r for r in reversed(rows) if r['plan_id'] == plan['id'] and r['unit_key'] == run['unit_key'])['id'] != run['id']:
            raise ValueError('请检查当前方案的最新准确单元运行。')
        self.s.work_file_handoff_ledger.guard_capture(db, run, rows)
        coordinator_runtime = None
        if plan.get('coordinator_file_handoff_root'):
            from .work_coordinator_handoff_runtime import enabled, reader
            if enabled(self.s, db, plan):
                coordinator_runtime = reader(self.s, db, plan)
                coordinator_runtime.guard_capture(db, plan, run)
        evidence = self.s.work_units.base_evidence(db, run)
        if not evidence['result_available'] or p.get('result_version') != evidence['version']:
            raise ValueError('准确结果版本已变化或回合尚未完成退出。')
        old = self.current(db, run['id'])
        if p.get('artifact') != self.reference(old):
            raise ValueError('文件产物版本已变化，请刷新。')
        unit = self.unit(db, run)
        if not unit.get('outputs'):
            raise ValueError('本单元未声明文件产物。')
        root = Path(self.s.store.attempt(run['attempt_id'], db)['workspace'])
        records = {}
        missing = []
        auth = self.s.work_units.authorization(db, driver)
        record_missing = bool(p.get('flow') and foundation(self.s, db, plan) and auth and auth.get('auto_rework_checks'))
        for name in unit['outputs']:
            try:
                workspace.select_files(root, [name], self.s.locations.data)
                records[name] = workspace.read_blob(root, name)
            except ValueError as exc:
                if (record_missing or plan.get('pdca')) and str(exc) == '文件不存在。':
                    missing.append(name)
                    continue
                raise ValueError(f'产物 {name}：{exc}') from exc
        if missing and not record_missing:
            raise ValueError('产物 ' + '、'.join(missing) + '：文件不存在。')
        if sum(r['bytes'] for r in records.values()) > workspace.MAX_ATTACHMENT_TOTAL:
            raise ValueError('文件产物合计超过32MB。')
        observations = []
        for c in unit.get('checks', []):
            if c['file'] in missing:
                observations.append({'definition': c, 'passed': False, 'missing': True})
                continue
            raw = records[c['file']]['raw']; passed = False
            measurement = {}
            try:
                if c['kind'] == 'nonempty':
                    passed = bool(raw)
                elif len(raw) <= workspace.MAX_FILE:
                    if c['kind'] == 'utf8_section_length':
                        text = raw.decode('utf-8-sig')
                        count = section_length(text, c)
                        measurement['observed_chars'] = count
                        measurement['counting'] = c.get('counting', 'unicode_alnum')
                        repeated = count is not None and section_repeated_outside(text, c)
                        measurement['body_repeated_outside'] = repeated
                        passed = count is not None and not repeated and c['min_chars'] <= count <= c['max_chars']
                    elif c['kind'] == 'utf8_contains':
                        text = raw.decode('utf-8')
                        passed = c['text'] in text
                    elif c['kind'] == 'json_parse':
                        def invalid_constant(value):
                            raise ValueError('JSON contains a non-finite constant: ' + value)
                        json.loads(raw.decode('utf-8'), parse_constant=invalid_constant); passed = True
            except (UnicodeError, ValueError, RecursionError):
                pass
            observations.append({'definition': c, 'passed': passed, **measurement})
        manifest = {n: {k: r[k] for k in ('sha256', 'bytes')} for n, r in records.items()}
        snapshot = {'run_id': run['id'], 'result_version': evidence['version'],
                    'identity': {k: evidence[k] for k in ('task_id', 'attempt_id', 'owner', 'session_id', 'turn_id', 'connection_fingerprint')},
                    'plan_id': plan['id'], 'plan_version': plan['version'], 'unit_revision': run['unit_revision'],
                    'manifest': manifest, 'observations': observations,
                    'check_status': 'FAIL' if missing else
                    (('PASS' if all(o['passed'] for o in observations) else 'FAIL') if observations else 'NOT_RECORDED'),
                    **({'missing_outputs': missing} if missing else {})}
        version = digest(snapshot)
        row = {'id': 'unit-artifact:' + version, 'version': version, 'snapshot': snapshot,
               'item_id': item['id'], 'created': time.time(), 'command_id': self.s.command_identity}
        folder = self.folder(row)
        folder.parent.mkdir(parents=True, exist_ok=True)
        if folder.exists():
            if workspace.current_manifest(folder, manifest) != manifest:
                raise ValueError('已有产物快照已变化，不能覆盖。')
        else:
            with tempfile.TemporaryDirectory(dir=folder.parent, prefix='capture-') as temporary:
                staging = Path(temporary) / 'files'; staging.mkdir()
                for name, r in records.items():
                    target = workspace.io_path(staging / name); target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(r['raw'])
                os.replace(staging, folder)
        if workspace.current_manifest(root, manifest) != manifest:
            raise ValueError('项目产物在保存期间已变化；未登记检查。')
        if any(workspace.io_path(root / name).exists() for name in missing):
            raise ValueError('原缺失产物在保存期间已出现；未登记检查。')
        existing = db.execute("SELECT data FROM collaboration WHERE kind='work_unit_artifact' AND id=?", (row['id'],)).fetchone()
        if existing:
            row = json.loads(existing[0])
        else:
            self.s.collaboration.put(db, 'work_unit_artifact', row)
        self.s.collaboration.put(db, 'work_unit_artifact_current',
                                 {'id': 'unit-artifact-current:' + run['id'], 'artifact_id': row['id']})
        completion = None
        if run['input'].get('file_handoff_intent') and snapshot['check_status'] != 'FAIL':
            delivery = self.s.work_file_handoff_ledger.delivery_for(db, run)
            completion = self.s.work_file_handoff_ledger.complete(
                db, self.s.work_file_handoff_ledger.reference(delivery), run)
        self.s.store.event(run['task_id'], run['attempt_id'], 'cooperative_files_checked',
                           {'artifact': self.reference(row), 'manifest': manifest, 'check_status': snapshot['check_status']}, db)
        if coordinator_runtime:
            coordinator_runtime.finish_capture(db, plan, run)
        from .work_file_handoff_chain_runtime import uses_file_rework
        if uses_file_rework(self.s.work_file_handoff_applications, db, plan):
            from .work_file_handoff_file_rework_path import WorkFileHandoffFileReworkPath
            WorkFileHandoffFileReworkPath(self.s).finish_capture(db, plan, run, row)
        return {'artifact': row, 'no_dispatch': True, 'project_files_written': False,
                **({'file_handoff_completion': self.s.work_file_handoff_ledger.reference(completion)} if completion else {}),
                'independent_review': 'NOT_PERFORMED', 'human_acceptance': 'NOT_RECORDED'}
