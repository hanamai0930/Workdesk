"""Exact immutable file replacements; live dependencies remain ordinary evidence."""
from pathlib import Path
import time

from . import workspace, work_file_sources
from .store import digest, uid
from .work_file_reworks import file_work, original_files
from .work_transitions import plan_ref
from .work_unit_revisions import require_history


PROTOCOL = 'ORDINARY_FILE_REWORK_APPLICATION_V1'


def scope(plan, affected):
    targets, declarations = {}, []
    for unit in plan['units']:
        if unit['role'] == 'coordinate':
            continue
        names = sorted(set(unit['outputs']) | set(unit['write_paths']))
        for name in names:
            workspace.relative(name)
            declarations.append((unit['key'], name.casefold()))
        if unit['key'] not in affected or not file_work(unit):
            continue
        if not unit['outputs'] and not unit['write_paths'] and not unit['checks']:
            continue  # A read-only downstream file gate needs no write baseline.
        if not unit['outputs'] or set(unit['write_paths']) != set(unit['outputs']):
            raise ValueError('文件应用须逐一声明相同的 outputs/write_paths；目录或仅写入范围待细化：' + unit['key'])
        targets[unit['key']] = sorted(unit['outputs'])
    for key, name in declarations:
        for other, path in declarations:
            if key != other and (key in affected or other in affected) and (
                    name == path or name.startswith(path + '/') or path.startswith(name + '/')):
                raise ValueError('跨单元同路径或目录写入重叠须另行整合：' + name)
    if sum(map(len, targets.values())) > workspace.MAX_FILES:
        raise ValueError('写前基线最多200份准确文件。')
    return targets


class WorkFileReworkApplications:
    def __init__(self, service):
        self.s = service

    def handlers(self):
        return {'work-item.file-rework-apply': self.apply}

    def root(self, db, item):
        row = db.execute('SELECT root FROM projects WHERE id=?', (item['project_id'],)).fetchone()
        if not row:
            raise ValueError('文件返工项目不存在。')
        return str(Path(row[0]).absolute())

    def observe(self, root, names):
        if not workspace.io_path(Path(root)).is_dir():
            raise ValueError('写前基线的项目目录不可用。')
        manifest, total = {}, 0
        for name in names:
            path = workspace.plain(Path(root) / workspace.relative(name), Path(root), missing=True)
            data = self.s.locations.data.resolve()
            if path.resolve() == data or path.resolve().is_relative_to(data):
                raise ValueError('控制服务数据不能作为写前基线。')
            if workspace.io_path(path).is_symlink():
                raise ValueError('写前基线拒绝链接。')
            if workspace.io_path(path).exists():
                value = workspace.read_blob(root, name)
                manifest[name] = {k: value[k] for k in ('sha256', 'bytes')}
                total += value['bytes']
        if total > workspace.MAX_ATTACHMENT_TOTAL:
            raise ValueError('写前基线合计超过32MB。')
        body = {'root': root, 'paths': sorted(names), 'manifest': manifest,
                'missing': sorted(set(names) - set(manifest)),
                'scope': 'EXACT_DECLARED_PATHS_ONLY; NOT_DIRECTORY_BACKUP_OR_OS_ISOLATION'}
        return {**body, 'version': digest(body)}

    def folder(self, baseline):
        return self.s.locations.data / 'results' / ('file-rework-base-' + baseline['version']) / 'files'

    def preserve(self, baseline):
        folder = self.folder(baseline)
        if not folder.exists():
            workspace.copy_manifest(baseline['root'], folder, baseline['manifest'])
        self.require_baseline(baseline)
        if self.observe(baseline['root'], baseline['paths']) != baseline:
            raise ValueError('写前基线在保存期间变化；未应用或派发。')

    def require_baseline(self, baseline):
        if (baseline['version'] != digest({k: v for k, v in baseline.items() if k != 'version'}) or
                workspace.current_manifest(self.folder(baseline), baseline['manifest']) != baseline['manifest']):
            raise ValueError('保存的文件返工写前基线损坏。')

    def records(self, db, consumer, rows):
        """Validate formal records before returning any historical-only relation."""
        snapshot = self.s.work_unit_revision_applications._snapshot.get()
        reusable = snapshot and snapshot['db'] is db and snapshot['changes'] == db.total_changes
        marker = (digest(consumer), digest(rows))
        cached = snapshot.setdefault('file_records', {}) if reusable else {}
        if marker in cached:
            return cached[marker]
        plans = self.s.work_unit_revision_applications.ancestors(db, consumer)
        for run in rows:
            if run['input'].get('write_before_baseline'):
                self.require_baseline(run['input']['write_before_baseline'])
        records = []
        for origin in reversed(plans):
            record = origin.get('file_rework_lineage')
            if not record:
                continue
            if record['protocol'] != PROTOCOL or record['version'] != digest({k:v for k,v in record.items() if k != 'version'}):
                raise ValueError('正式文件应用记录协议或摘要不一致。')
            saved = self.s.collaboration.get(db, 'work_file_rework_revision', record['id'])
            if (saved.get('record') != record or saved['to_plan'] != plan_ref(origin) or
                    origin['previous_plan_reference'] != record['base_plan'] or
                    origin['transition'].get('file_rework_revision') != record['id']):
                raise ValueError('正式文件应用记录脱离准确新旧方案。')
            proposal = self.s.collaboration.get(db, 'work_proposal', record['proposal']['id'])
            consumed = self.s.work_transitions.consumption(db, proposal)
            preview = proposal['snapshot'].get('file_rework', {})
            if (self.s.work_proposals.reference(proposal) != record['proposal'] or
                    digest(proposal['snapshot']) != proposal['version'] or
                    proposal['snapshot']['file_rework']['candidate_version'] != record['candidate_version'] or
                    not consumed or consumed != saved['consumption'] or consumed['to_plan'] != plan_ref(origin) or
                    consumed['from_plan'] != record['base_plan'] or consumed['command_id'] != record['command_id']):
                raise ValueError('正式文件应用提案或消费记录不一致。')
            if (any(record[k] != preview.get(k) for k in ('direct_changes','affected_units','write_scope')) or
                    record['baseline'] != preview.get('write_before_observation') or
                    record['protected_history'] != preview['history_source_audit']['protected_history'] or
                    record['replacement_candidates'] != preview['history_source_audit']['replacement_candidates']):
                raise ValueError('正式文件应用范围、替代关系或基线脱离准确预览。')
            auth = self.s.collaboration.get(db, 'work_unit_authorization_revision',
                'unit-auth-version:' + saved['authorization']['revision'])
            if auth['id'] != 'unit-auth-version:' + saved['authorization']['revision']:
                raise ValueError('文件应用历史授权版本记录身份不一致。')
            auth = {**auth, 'id': saved['authorization']['id']}
            old = record['authorization_from']
            if (self.s.work_units.auth_ref(auth) != saved['authorization'] or auth['plan_id'] != origin['id'] or
                    auth['plan_version'] != origin['version'] or auth['previous_authorization'] != self.s.work_units.auth_ref(old) or
                    any(auth[k] != old[k] for k in ('id','created','max_attempts_total','dispatch_before',
                                                   'native_permissions','driver_generation'))):
                raise ValueError('文件应用必须保留原累计授权、截止、权限和责任。')
            selected = [r for r in rows if r['id'] in record['original_run_ids'] or
                        r['plan_id'] in record['original_plan_ids']]
            files = [original_files(self.s, db, r) for r in selected]
            require_history(self.s, db, selected)
            base = self.s.collaboration.get(db, 'teamwork_plan', record['base_plan']['id'])
            current = work_file_sources.source_context(self.s, db, base, selected, files)
            if current != record['protected_history'] or any(f['input_problems'] or f['output_state'] == 'DAMAGED' for f in files):
                raise ValueError('正式文件应用的原运行、原输入、全部旧快照或资源已变化。')
            if any(not r['valid'] for r in current['resources']):
                raise ValueError('正式文件应用原资源已变化或不可用。')
            expected = work_file_sources.replacement_candidates(self.s, db, base, selected,
                                                                set(record['affected_units']), files)
            if expected != record['replacement_candidates']:
                raise ValueError('正式文件替代关系脱离原准确声明和版本。')
            self.require_baseline(record['baseline'])
            records.append(record)
        if reusable:
            cached[marker] = records
        return records

    def compare(self, db, consumer, rows, selected, expected):
        records = self.records(db, consumer, rows)
        if not records:
            return False
        snapshot = self.s.work_unit_revision_applications._snapshot.get()
        reusable = snapshot and snapshot['db'] is db and snapshot['changes'] == db.total_changes
        marker = (digest(consumer), digest(selected), digest(expected))
        cached = snapshot.setdefault('file_frontiers', set()) if reusable else set()
        if marker in cached:
            return True
        bindings = {b['run_id']: b for record in records for b in record['replacement_candidates']}
        files = [original_files(self.s, db, r) for r in selected]
        work_file_sources.compare_frontier(self.s, db, selected, expected, list(bindings.values()), files)
        if reusable:
            cached.add(marker)
        return True

    def input_context(self, db, plan, unit, rows):
        records = self.records(db, plan, rows)
        record = plan.get('file_rework_lineage')
        if not record or unit['role'] == 'coordinate' or unit['key'] not in record['affected_units']:
            return None
        entry = next(e for e in record['units'] if e['key'] == unit['key'])
        return {'protocol': PROTOCOL, 'revision_id': record['id'], 'revision_version': record['version'],
                'base_plan': record['base_plan'], 'proposal': record['proposal'], 'origin': entry,
                'scope': 'ORIGINAL_DIAGNOSTIC_DATA; LIVE_FULL_DEPENDENCIES_REQUIRED; NOT_RETRY_OF'}

    def dispatch_baseline(self, db, item, plan, unit, rows):
        record = plan.get('file_rework_lineage')
        if not record or unit['key'] not in record['write_scope']:
            return None
        names = record['write_scope'][unit['key']]
        current = self.observe(self.root(db, item), names)
        prior = [r for r in rows if r['plan_id'] == plan['id'] and r['unit_key'] == unit['key']]
        if not prior:
            application = record['baseline']
            expected = {n: v for n, v in application['manifest'].items() if n in names}
            if current['root'] != application['root'] or current['manifest'] != expected:
                raise ValueError('首次文件返工派发的当前写前基线已变化；须重新明确诊断和应用。')
        self.preserve(current)
        return current

    def apply(self, db, p):
        if set(p) != {'id','revision','current_driver','plan','authorization','frontier','proposal'}:
            raise ValueError('文件应用只接受准确任务、责任、方案、授权、前沿和预览引用。')
        item, driver, plan = self.s.work_units.checked(db, p)
        self.s.work_flows.require_stopped(db, item['id'])
        proposals = self.s.work_proposals
        row = proposals.current(db, item['id'])
        if (not row or p['proposal'] != proposals.reference(row) or self.s.work_transitions.consumption(db, row) or
                row['snapshot']['state'] != 'FILE_REWORK_PREVIEW'):
            raise ValueError('须使用最新、未消费的准确文件返工预览。')
        issues = proposals.live_issues(db, item, row)
        if issues:
            raise ValueError('文件返工预览已失效：' + '；'.join(issues))
        rows = self.s.work_units.rows(db, item['id'])
        self.records(db, plan, rows)
        auth = self.s.work_units.authorization(db, driver)
        if (not auth or p['authorization'] != self.s.work_units.auth_ref(auth) or
                auth['driver_generation'] != driver['generation'] or auth['plan_id'] != plan['id'] or
                auth['plan_version'] != plan['version'] or p['frontier'] != self.s.work_units.frontier(db, item['id'])):
            raise ValueError('文件应用的准确授权或前沿已变化。')
        if len(rows) >= auth['max_attempts_total'] or time.time() >= auth['dispatch_before']:
            raise ValueError('原累计次数或派发截止已耗尽。')
        preview = row['snapshot']['file_rework']
        if preview['execution_blockers']:
            raise ValueError('文件应用受阻：' + '；'.join(preview['execution_blockers']))
        require_history(self.s, db, rows)
        write_scope = scope(plan, set(preview['affected_units']))
        baseline = self.observe(self.root(db, item), sorted({n for names in write_scope.values() for n in names}))
        if baseline != preview['write_before_observation']:
            raise ValueError('应用期间写前基线已变化；旧预览不能重绑。')
        self.preserve(baseline)
        entries = []
        for entry in preview['units']:
            if entry['state'] != 'AFFECTED' or next(u for u in plan['units'] if u['key'] == entry['key'])['role'] == 'coordinate':
                continue
            run = next((r for r in rows if entry['source'] and r['id'] == entry['source']['run_id']), None)
            entries.append({**entry, 'original_input': run['input'] if run else None,
                            'original_evidence': self.s.work_units.evidence(db, run) if run else None})
        audit = preview['history_source_audit']
        record = {'id': 'file-rework:' + uid(), 'protocol': PROTOCOL, 'base_plan': plan_ref(plan),
                  'proposal': proposals.reference(row), 'candidate_version': preview['candidate_version'],
                  'reason': row['snapshot']['decision']['reason'], 'source': row['snapshot']['source'],
                  'direct_changes': preview['direct_changes'], 'affected_units': preview['affected_units'],
                  'units': entries, 'replacement_candidates': audit['replacement_candidates'],
                  'protected_history': audit['protected_history'], 'original_run_ids': [r['id'] for r in rows],
                  'original_plan_ids': sorted({p['id'] for p in audit['protected_history']['plans']}),
                  'authorization_from': auth, 'write_scope': write_scope, 'baseline': baseline,
                  'command_id': self.s.command_identity, 'no_dispatch': True}
        record['version'] = digest(record)
        transition = {'kind': 'EXPLICIT_FILE_REWORK_REVISION', 'file_rework_revision': record['id'],
                      'proposal': proposals.reference(row), 'source': record['source'],
                      'usage_at_transition': self.s.work_units.usage(db, rows),
                      'authorization_from': self.s.work_units.auth_ref(auth),
                      'command_id': self.s.command_identity, 'no_dispatch': True}
        lineage = {e['key']: e['source'] for e in preview['units'] if e['state'] == 'ELIGIBLE_RESULT'}
        new, updated, transferred = self.s.work_transitions.save_plan(db, item, driver, plan, auth,
            plan['units'], lineage, transition, extra={'file_rework_lineage': record},
            reason='FILE_REWORK_EXPLICIT_RECHECK_REQUIRED')
        consumption = {'id': 'proposal-consumption:' + row['id'], 'proposal': proposals.reference(row),
                       'item_id': item['id'], 'kind': 'FILE_REWORK_TRANSITION', 'from_plan': plan_ref(plan),
                       'to_plan': plan_ref(new), 'driver': self.s.work_drivers.reference(updated),
                       'authorization': self.s.work_units.auth_ref(transferred), 'created': new['created'],
                       'command_id': self.s.command_identity, 'no_dispatch': True}
        saved = {'id': record['id'], 'record': record, 'to_plan': plan_ref(new), 'consumption': consumption,
                 'authorization': self.s.work_units.auth_ref(transferred), 'created': new['created']}
        self.s.collaboration.put(db, 'work_file_rework_revision', saved)
        self.s.collaboration.put(db, 'work_proposal_consumption', consumption)
        self.s.store.event(None, None, 'file_rework_applied', saved, db)
        return {'file_rework_revision': saved, 'transition': consumption, 'plan': plan_ref(new),
                'driver': self.s.work_drivers.reference(updated), 'authorization': self.s.work_units.auth_ref(transferred),
                'no_dispatch': True, 'automatically_resumed': False}
