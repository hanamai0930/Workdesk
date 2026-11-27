"""Run the stage-1 core chain in a disposable, independently identified instance."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid


BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'src'))
from researchos.workdesk import core_runtime
from researchos.workdesk.paths import Locations
from researchos.workdesk.store import Store, canonical


class CheckError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise CheckError(message)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def clean_error(value):
    return re.sub(r'/s/[^/\s]+', '/s/<redacted>', str(value))[:1600]


def request(origin, path, *, token=None, body=None, expected=200, ui=False, raw=False):
    headers = {}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = None
    if body is not None:
        headers.update({'Content-Type': 'application/json', 'Origin': origin})
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    try:
        with urlopen(Request(origin + path, data=data, headers=headers), timeout=35) as response:
            payload = response.read(32 * 1024 * 1024)
            status = response.status
    except HTTPError as error:
        status = error.code
        payload = error.read(65536)
    except (URLError, OSError) as error:
        raise CheckError('本地请求回执未知；未重发：' + clean_error(type(error).__name__)) from None
    value = payload if raw else json.loads(payload)
    if status != expected:
        error = value.get('error', '接口拒绝') if isinstance(value, dict) else '接口未返回预期内容'
        raise CheckError(f'{path.split("?")[0].replace(token or "<none>", "<redacted>")}: HTTP {status}: {clean_error(error)}')
    return value


def free_port():
    with socket.socket() as stream:
        stream.bind(('127.0.0.1', 0))
        return stream.getsockname()[1]


class Chain:
    def __init__(self, executable, evidence_stage="stage1"):
        self.root = BASE.parent / 'artifacts' / evidence_stage / ('c-' + uuid.uuid4().hex[:8])
        self.root.mkdir(parents=True, exist_ok=False)
        self.user = self.root / 'user'
        self.data = self.user / 'libraries' / 'main'
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'notes.txt').write_text('阶段 1 本地工具核对\nA reproducible local result.\n', encoding='utf-8')
        (self.project / 'measurements.csv').write_text('sample,value\na,1\nb,2\n', encoding='utf-8')
        self.project_id, self.item_id = str(uuid.uuid4()), str(uuid.uuid4())
        self.profile = {'schema': 1, 'channel': 'development', 'instance_id': str(uuid.uuid4()),
            'development_root': str(BASE.parent), 'program_root': str(BASE), 'user_root': str(self.user),
            'data_root': str(self.data), 'python_executable': str(BASE / '.venv/Scripts/python.exe'),
            'ui_port': free_port(), 'sync_branch': 'codex/v2-dev', 'default_project_id': self.project_id,
            'protected_roots': [r'C:\Workdesk', str(Path.home() / '.workdesk'), r'E:\Workdesk分发'],
            'config_path': str(self.root / 'instance.json')}
        Path(self.profile['config_path']).write_text(json.dumps(self.profile, ensure_ascii=False, indent=2), encoding='utf-8')
        self.locations = Locations(BASE, self.data, self.user)
        self.executable = Path(executable).resolve()
        require(self.executable.is_file(), 'Go候选不存在。')
        self.process = None
        self.backend = None
        self.evidence = {'schema': 1, 'kind': 'STAGE1_ISOLATED_CORE_CHAIN', 'fixture': str(self.root),
            'recorded_utc': datetime.now(timezone.utc).isoformat(), 'real_model_requests': 0,
            'instance_id': self.profile['instance_id'], 'program_root': str(BASE), 'data_root': str(self.data),
            'binary_sha256': sha256(self.executable.read_bytes()).hexdigest(), 'checks': {}}

    def seed(self):
        store = Store(self.data)
        stamp = time.time()
        with store.write() as db:
            db.execute('INSERT INTO projects VALUES(?,?,?,?)',
                       (self.project_id, '阶段1隔离核对项目', str(self.project), stamp))
            db.execute('INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (self.item_id, self.project_id, '保留旧工作记录', '核对Go迁移和原记录', '',
                        '记录身份不变', '', '继续下一阶段', 'DRAFT', 1, stamp, stamp))
            store.event(None, None, 'stage1_fixture_seeded', {'project_id': self.project_id}, db)
        self.evidence['checks']['seed_schema1'] = True

    def start(self):
        self.process = core_runtime.start(self.locations, self.profile, fixture=True, manifest=self.manifest)
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise CheckError('Go候选启动退出；诊断位于隔离实例 user/runtime。')
            try:
                state = read_json(self.data / 'backend.json')
                if state['pid'] == self.process.pid:
                    origin = 'http://127.0.0.1:' + str(state['port'])
                    health = request(origin, '/health')
                    if health['instance'] == state['instance']:
                        self.backend, self.origin = state, origin
                        return
            except (OSError, ValueError, CheckError):
                pass
            time.sleep(.15)
        raise CheckError('Go候选启动身份在时限内未核对。')

    def api(self, path, body=None, expected=200):
        token = self.backend['viewer'] if body is None else self.backend['control']
        return request(self.origin, path, token=token, body=body, expected=expected)

    def command(self, kind, payload, identity=None, expected=200):
        return self.api('/api/v1/command', {'id': identity or str(uuid.uuid4()), 'kind': kind,
                                          'payload': payload}, expected=expected)

    def stop(self):
        if self.process is None or self.process.poll() is not None:
            return
        self.command('service.reload', {})
        try:
            self.process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            raise CheckError('空闲停止未在时限内退出。') from None

    def check_restored_core(self, restored, original_job):
        """Read actual staged bytes while the source artifact paths are unavailable."""
        self.stop()
        previous = {key: getattr(self, key) for key in ('profile', 'locations', 'data', 'process', 'backend', 'origin')}
        pointer = self.user / 'config/locations.json'
        pointer_bytes = pointer.read_bytes() if pointer.exists() else None
        held = []
        try:
            for artifact in original_job['artifacts']:
                source = (previous['data'] / artifact['relative_path']).resolve()
                require(source.is_relative_to(previous['data'].resolve() / 'results'), '源成果不属于隔离库。')
                moved = source.with_name(source.name + '.source-held-' + uuid.uuid4().hex[:8])
                source.rename(moved)
                held.append((source, moved))
            self.data = Path(restored['target'])
            self.profile = {**previous['profile'], 'instance_id': str(uuid.uuid4()), 'data_root': str(self.data),
                            'ui_port': free_port(), 'config_path': str(self.root / 'restored-instance.json')}
            Path(self.profile['config_path']).write_text(json.dumps(self.profile, ensure_ascii=False), encoding='utf-8')
            pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer.write_text(json.dumps({'schema': 1, 'active_data': str(self.data)}), encoding='utf-8')
            self.locations = Locations(BASE, self.data, self.user)
            self.process, self.backend = None, None
            self.start()
            identity = self.api('/api/v1/instance')
            require(Path(identity['data_root']).resolve() == self.data.resolve(), '恢复副本核心未打开暂存库。')
            runtime = self.api('/api/v2/runtime')
            job = next((item for item in runtime['jobs'] if item['id'] == original_job['id']), None)
            require(job is not None and job['state'] == 'COMPLETED', '恢复副本未读到原工具记录。')
            checked = []
            for artifact in job['artifacts']:
                expected = self.data / artifact['relative_path']
                require(Path(artifact['path']).resolve() == expected.resolve(), '恢复成果仍指向原资料库。')
                raw = request(
                    self.origin, '/api/v2/jobs/' + job['id'] + '/artifact?' + urlencode({'path': str(expected)}),
                    token=self.backend['viewer'], raw=True)
                require(sha256(raw).hexdigest() == artifact['sha256'], '恢复核心返回的成果字节不符。')
                checked.append({'relative_path': artifact['relative_path'], 'sha256': artifact['sha256']})
            self.evidence['checks']['restored_core_artifact_reads'] = {'data_root': str(self.data),
                'source_artifacts_temporarily_unavailable': True, 'artifacts': checked}
            self.stop()
        finally:
            self.cleanup()
            for key, value in previous.items():
                setattr(self, key, value)
            if pointer_bytes is not None:
                pointer.write_bytes(pointer_bytes)
            elif pointer.exists():
                pointer.unlink()
            for source, moved in held:
                if moved.exists():
                    moved.rename(source)

    def run(self):
        self.seed()
        self.manifest = core_runtime.freeze(BASE, self.data, self.executable)
        self.evidence['implementation_revision'] = self.manifest['implementation_revision']
        migration = core_runtime.prepare_migration(self.profile, self.executable, fixture=True)
        require(str(migration['storage_schema']) == '2', '迁移后格式不是2。')
        self.evidence['checks']['migration'] = migration
        self.start()
        identity = self.api('/api/v1/instance')
        require(identity['instance_id'] == self.profile['instance_id'] and identity['protocol'] == 2,
                'Go实例身份或协议不符。')
        require(Path(identity['data_root']).resolve() == self.data.resolve(), '活动库指向错误。')
        first_process = identity['process_instance']
        self.evidence['checks']['identity'] = identity
        workspace = self.api('/api/v1/workspace?view=workbench')
        require(any(p['id'] == self.project_id for p in workspace['projects']), '旧workspace找不到项目。')
        projects = self.api('/api/v1/projects')
        require(any(p['id'] == self.project_id for p in projects['projects']), '旧项目接口找不到项目。')
        records = self.api('/api/v2/records')
        require(self.item_id in canonical(records), '新记录摘要未承接旧工作记录。')
        original_record = self.api('/api/v2/records/work_item/' + self.item_id)
        self.evidence['checks']['legacy_and_v2_records'] = True

        ui_state = read_json(self.user / 'runtime/ui/instance-core.json')
        ui_origin = 'http://127.0.0.1:' + str(ui_state['port'])
        prefix = '/s/' + ui_state['token'] + '/'
        html = request(ui_origin, prefix, raw=True)
        require(b'<html' in html.lower(), '未返回真实界面HTML。')
        preferences = request(ui_origin, prefix + 'api/preferences')
        require('chatApiModels' in preferences, '旧界面偏好接口未接通。')
        self.evidence['checks']['headless_ui'] = {'html_sha256': sha256(html).hexdigest(), 'preferences': True}

        draft_id, draft_command = 'stage1-fixture-draft', str(uuid.uuid4())
        draft_payload = {'id': draft_id, 'revision': 0, 'value': {'mode': 'work', 'project': self.project_id,
                         'input': '本地隔离草稿：保留中文与配置。'}}
        draft = self.command('draft.save', draft_payload, draft_command)
        require(draft['receipt']['draft']['revision'] == 1, '旧草稿未保存。')
        again = self.command('draft.save', draft_payload, draft_command)
        require(again['replayed'] and again['receipt'] == draft['receipt'], '旧命令未复用原回执。')
        self.command('draft.save', draft_payload, expected=409)
        self.evidence['checks']['legacy_command_idempotence_and_conflict'] = True

        command_id = str(uuid.uuid4())
        payload = {'project_id': self.project_id, 'input_files': ['notes.txt', 'measurements.csv']}
        queued = self.command('tool.run', payload, command_id)
        job_id = queued['receipt']['invocation_id']
        again = self.command('tool.run', payload, command_id)
        require(again['replayed'] and again['receipt'] == queued['receipt'], '工具命令产生重复运行。')
        deadline = time.monotonic() + 30
        job = None
        while time.monotonic() < deadline:
            runtime = self.api('/api/v2/runtime')
            jobs = runtime.get('jobs', runtime.get('tool_jobs', []))
            job = next((j for j in jobs if j['id'] == job_id), None)
            if job and job['state'] not in {'QUEUED', 'RUNNING'}:
                break
            time.sleep(.15)
        require(job is not None and job['state'] == 'COMPLETED', '真实Python工具未完成：' + clean_error(job))
        require(len(job['artifacts']) >= 2, '工具未产生真实成果文件。')
        for artifact in job['artifacts']:
            path = Path(artifact['path'])
            require(path.is_file() and sha256(path.read_bytes()).hexdigest() == artifact['sha256'], '成果摘要不匹配。')
        self.evidence['checks']['real_python_tool'] = {'id': job_id, 'state': job['state'], 'artifacts': job['artifacts']}
        events = self.api('/api/v2/events?after=0&limit=500')
        require(any(event['kind'] == 'stage1_fixture_seeded' for event in events['events']), '事件接口未读取原事件。')
        require(job_id in canonical(events), '工具事件未回到同一事件序列。')
        tail = self.api('/api/v2/events?after=' + events['cursor'])
        require(not any(int(event['seq']) <= int(events['cursor']) for event in tail['events']), '事件游标返回重复历史。')
        self.evidence['checks']['incremental_events'] = {'cursor': events['cursor'], 'events': len(events['events'])}
        unknown = self.api('/api/v1/receipt?id=' + str(uuid.uuid4()))
        require(unknown['state'] == 'UNKNOWN_OR_NOT_COMMITTED' and unknown['no_resend'], '未知回执语义改变。')
        self.evidence['checks']['unknown_no_resend'] = True

        backup = self.command('backup.create', {})['receipt']
        require(backup['state'] == 'READY', '备份未就绪。')
        restored = self.command('backup.restore', {'backup_id': backup['backup_id']})['receipt']
        require(restored['state'] == 'STAGED' and Path(restored['target']).is_relative_to(self.user), '恢复未留在隔离开发侧暂存。')
        with closing(sqlite3.connect((Path(restored['target']) / 'tasks.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
            require(db.execute('SELECT id FROM projects WHERE id=?', (self.project_id,)).fetchone() is not None, '暂存恢复丢失项目。')
            require(db.execute('SELECT id FROM work_items WHERE id=?', (self.item_id,)).fetchone() is not None, '暂存恢复丢失工作记录。')
        for artifact in job['artifacts']:
            restored_file = Path(restored['target']) / artifact['relative_path']
            require(restored_file.is_file() and sha256(restored_file.read_bytes()).hexdigest() == artifact['sha256'],
                    '暂存恢复未保留工具成果的准确字节。')
        self.evidence['checks']['backup_and_staged_restore'] = {'backup': backup, 'restore': restored}
        self.stop()
        self.start()
        current = self.api('/api/v1/instance')
        require(current['instance_id'] == identity['instance_id'] and current['process_instance'] != first_process,
                '重启身份未正确区分。')
        require(current['owner_epoch'] > identity['owner_epoch'], '重启没有更新所有者代次。')
        require(self.item_id in canonical(self.api('/api/v2/records')), '重启后旧工作记录丢失。')
        require(self.api('/api/v2/records/work_item/' + self.item_id) == original_record, '重启改变旧工作记录正文或版本。')
        ui_state_value = self.api('/api/v1/ui-state')
        require(any(d['id'] == draft_id and d['revision'] == 1 for d in ui_state_value['drafts']), '重启后草稿丢失或冲突覆盖。')
        require(self.api('/api/v1/receipt?id=' + command_id)['receipt'] == queued['receipt'], '重启后准确命令回执丢失。')
        self.evidence['checks']['restart_persistence'] = {'instance_id_preserved': True, 'new_process_instance': current['process_instance'],
                                                        'owner_epoch': current['owner_epoch']}
        self.stop()
        self.check_restored_core(restored, job)
        self.evidence['status'] = 'PASSED'

    def cleanup(self):
        if self.process is not None and self.process.poll() is None:
            try:
                self.stop()
            except Exception:
                # This is the exact process handle created for this isolated check.
                self.process.terminate()
                self.process.wait(timeout=8)

    def save(self):
        path = self.root / 'check.json'
        path.write_text(json.dumps(self.evidence, ensure_ascii=False, indent=2), encoding='utf-8')
        return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', default=str(BASE / 'core/workdesk-core.exe'))
    args = parser.parse_args()
    chain = Chain(args.executable)
    try:
        chain.run()
    except Exception as error:
        chain.evidence['status'] = 'FAILED'
        chain.evidence['error'] = clean_error(error)
        print('FAILED: ' + clean_error(error))
    finally:
        chain.cleanup()
        path = chain.save()
        print('Evidence: ' + str(path))
    if chain.evidence['status'] == 'PASSED':
        print('PASSED: isolated core chain; real model requests: 0')
        return 0
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
