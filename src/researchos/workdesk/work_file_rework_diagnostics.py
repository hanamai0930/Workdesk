"""File-only diagnostic continuation. Saved bytes never become current attachments."""
from . import work_file_reworks, workspace
from .work_rework_diagnostics import FILE_PROTOCOL, WorkReworkDiagnostics, file_diagnostic_unit


class WorkFileReworkDiagnostics(WorkReworkDiagnostics):
    protocol = FILE_PROTOCOL
    file_scope = True
    label = '文件'
    command = 'work-item.file-rework-diagnose'
    record_kind = 'work_file_rework_diagnostic_revision'
    event = 'file_diagnostic_round_registered'

    def protection(self, db, rows):
        files = [work_file_reworks.original_files(self.s, db, r) for r in rows]
        if any(f['input_problems'] or f['output_state'] == 'DAMAGED' for f in files):
            raise ValueError('原实际输入或已登记旧文件快照损坏，须先恢复准确历史。')
        current = []
        for run in rows:
            root = self.s.store.attempt(run['attempt_id'], db)['workspace']
            unit = self.s.work_artifacts.unit(db, run)
            observed = {}
            for name in unit['outputs']:
                try:
                    manifest = workspace.current_manifest(root, [name])
                    observed[name] = {'state': 'OBSERVED' if name in manifest else 'MISSING', 'manifest': manifest}
                except (OSError, ValueError) as exc:
                    observed[name] = {'state': 'UNAVAILABLE', 'problem': str(exc)}
            current.append({'run_id': run['id'], 'outputs': observed})
        return {'file_protection': {'original_frontier': self.s.work_rework_applications.frontier(db, rows),
                                    'original_files': files,
                                    'current_project_observations': current,
                                    'scope': 'DIAGNOSTIC_ONLY; NO_CURRENT_FILE_REPLACEMENT_PERMISSION'}}

    def validate_source(self, db, plan, unit, rows, run):
        if not file_diagnostic_unit(plan, unit):
            return
        super().validate_source(db, plan, unit, rows, run)
        frozen = run['input']['coordination_context']
        current = work_file_reworks.context(self.s, db, plan, [r for r in rows if r['id'] != run['id']])
        if frozen.get('file_rework_context') != current:
            raise ValueError('文件诊断回合的原输入、历史快照或当前文件已变化；旧回复不能重绑。')
        self.protection(db, [run])
