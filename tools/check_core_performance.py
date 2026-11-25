"""One local, synthetic before/after observation of stage-1 bounded reads."""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid

from check_core_chain import BASE, Chain, clean_error, read_json, require
from researchos.workdesk import core_runtime
from researchos.workdesk.store import canonical


BEFORE_REVISION = '53d94e312708481c123e60513608683d6b726e89'
LOG_BYTES = 10 * 1024 * 1024
SEGMENT_BYTES = 4 * 1024 * 1024


def measured(origin, path, token):
    started = time.perf_counter()
    request = Request(origin + path, headers={'Authorization': 'Bearer ' + token})
    try:
        with urlopen(request, timeout=40) as response:
            status, raw = response.status, response.read(32 * 1024 * 1024 + 1)
    except HTTPError as error:
        status, raw = error.code, error.read(65536)
    wire_finished = time.perf_counter()
    require(len(raw) <= 32 * 1024 * 1024, '本地检查响应超过32MiB。')
    value = json.loads(raw)
    finished = time.perf_counter()
    result = {'method': 'GET', 'path': path, 'http_status': status, 'response_bytes': len(raw),
              'response_ms': round((wire_finished - started) * 1000, 3),
              'json_decode_ms': round((finished - wire_finished) * 1000, 3),
              'contains_plain_log_marker': b'PERF_LONG_LOG' in raw}
    if status != 200:
        result['error'] = value.get('error', '接口拒绝请求')
    return result, value


def archive_before(destination):
    require(not destination.exists(), '旧源码输出已存在；不覆盖此前证据。')
    destination.mkdir(parents=True)
    archive = destination.parent / 'before-src.tar'
    with archive.open('wb') as stream:
        subprocess.run(['git', '-C', str(BASE), 'archive', '--format=tar', BEFORE_REVISION,
                        'src/researchos/workdesk'], stdout=stream, check=True)
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            require((member.name.rstrip('/') in {'src', 'src/researchos'} or member.name.startswith('src/researchos/workdesk'))
                    and not member.issym() and not member.islnk(),
                    '旧源码归档出现不支持路径。')
        source.extractall(destination, filter='data')
    require(not (destination / '.git').exists(), '旧性能基线不能继承真实开发Git身份。')
    return {'revision': BEFORE_REVISION, 'source_root': str(destination),
            'archive_sha256': sha256(archive.read_bytes()).hexdigest(),
            'scope': 'src/researchos/workdesk only; no runtime, accounts, dependencies or UI build'}


def seed(chain, before_data):
    chain.seed()
    task_id = str(uuid.uuid4())
    line = b'PERF_LONG_LOG synthetic local output; no model or native agent invoked.\n'
    log = (line * (LOG_BYTES // len(line) + 1))[:LOG_BYTES]
    require(len(log) == LOG_BYTES, '日志夹具长度不符。')
    log_hash = sha256(log).hexdigest()
    stamp = 1_800_000_000.0
    spec = {'provider': 'codex', 'goal': 'Synthetic read-only performance fixture',
            'budget': {'requests': 1, 'tools': 1, 'seconds': 30, 'attempts': 1, 'cny': 1},
            'materials': {'long.log': {'sha256': log_hash, 'bytes': len(log)}},
            'writable': [], 'checks': [], 'work_mode': 'independent', 'execution': 'scoped'}
    with closing(sqlite3.connect(chain.data / 'tasks.sqlite3')) as db:
        db.executemany('INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            [(str(uuid.uuid4()), chain.project_id, f'Fixture item {i:04}', 'Short fixture goal', '',
              'Identity and bounded reads', '', 'No dispatch', 'DRAFT', 1, stamp, stamp)
             for i in range(1, 1000)])
        db.executemany('INSERT INTO collaboration VALUES(?,?,?,?)',
            [('ui_chat', identity, canonical({'id': identity, 'title': f'Fixture chat {i:04}',
              'provider': 'deepseek', 'project': chain.project_id, 'revision': 1,
              'created': stamp + 2 + i / 1000, 'updated': stamp + 2 + i / 1000,
              'messages': [], 'pending': None}), stamp + 2 + i / 1000)
             for i, identity in ((i, str(uuid.uuid4())) for i in range(1000))])
        db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',
            (task_id, chain.project_id, 'Synthetic completed task', canonical(spec),
             sha256(canonical(spec).encode()).hexdigest(), 'COMPLETED', None, stamp, stamp))
        db.execute('DELETE FROM events')
        db.execute("DELETE FROM sqlite_sequence WHERE name='events'")
        db.executemany('INSERT INTO events(at,task_id,attempt_id,kind,payload) VALUES(?,?,NULL,?,?)',
            [(stamp + i / 1000, task_id, 'perf_fixture_event', canonical({'index': i, 'text': 'synthetic event'}))
             for i in range(10000)])
        db.commit()
    before_data.mkdir(parents=True)
    seed_bytes = (chain.data / 'tasks.sqlite3').read_bytes()
    (before_data / 'tasks.sqlite3').write_bytes(seed_bytes)
    for data in (before_data, chain.data):
        path = data / 'tasks' / task_id / 'input' / 'long.log'
        path.parent.mkdir(parents=True)
        path.write_bytes(log)
    return task_id, log, {'work_items': 1000, 'ui_chats': 1000, 'tasks': 1, 'events': 10000,
                         'log_bytes': len(log), 'log_sha256': log_hash,
                         'same_initial_database_sha256': sha256(seed_bytes).hexdigest(),
                         'copies_byte_identical_before_go_migration': seed_bytes == (before_data / 'tasks.sqlite3').read_bytes()}


class Before:
    def __init__(self, root, data, user):
        self.root, self.data, self.user = root, data, user
        self.process, self.log = None, None

    def start(self):
        self.user.mkdir(parents=True, exist_ok=True)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith('WORKDESK_') and key not in {
                           'OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'QWEN_API_KEY', 'DASHSCOPE_API_KEY',
                           'ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY'}}
        environment.update(PYTHONPATH=str(self.root / 'src'), WORKDESK_HOME=str(self.user),
                           WORKDESK_DATA_DIR=str(self.data), CODEX_HOME=str(self.user / 'unused-codex'))
        self.log = (self.root.parent / 'before-server.log').open('wb')
        started = time.perf_counter()
        self.process = subprocess.Popen([sys.executable, '-B', '-X', 'utf8', '-m', 'researchos.workdesk.server',
            '--base', str(self.root), '--data', str(self.data), '--user-root', str(self.user)],
            cwd=self.root, env=environment, stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('旧Python基线退出，见before-server.log。')
            try:
                state = read_json(self.data / 'backend.json')
                origin = 'http://127.0.0.1:' + str(state['port'])
                with urlopen(origin + '/health', timeout=1) as response:
                    health = json.loads(response.read(65536))
                if (health['instance'] == state['instance']
                        and Path(health['program_base']).resolve() == self.root.resolve()
                        and Path(health['data_root']).resolve() == self.data.resolve()):
                    self.state, self.origin = state, origin
                    return {'confirmed_ms': round((time.perf_counter() - started) * 1000, 3),
                            'health_identity_checked': True, 'poll_interval_ms': 150,
                            'launcher_pid': self.process.pid, 'service_pid': state['pid'],
                            'launcher_and_service_same_pid': self.process.pid == state['pid']}
            except (OSError, ValueError):
                pass
            time.sleep(.15)
        raise RuntimeError('旧Python基线未在45秒内完成身份确认。')

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            try:
                raw = json.dumps({'id': str(uuid.uuid4()), 'kind': 'service.reload', 'payload': {}}).encode()
                request = Request(self.origin + '/api/v1/command', data=raw,
                    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.state['control']})
                with urlopen(request, timeout=4) as response:
                    response.read(65536)
                self.process.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired, AttributeError):
                self.process.terminate()  # Only the exact handle created above.
                self.process.wait(timeout=8)
        if self.log:
            self.log.close()


def install_log_job(chain, log):
    identity = str(uuid.uuid4())
    output = chain.data / 'results' / identity
    output.mkdir(parents=True)
    log_root = chain.data / 'results' / (identity + '-logs')
    log_root.mkdir(parents=True)
    segments = []
    for index, offset in enumerate(range(0, len(log), SEGMENT_BYTES)):
        path = log_root / f'stderr.{index:06d}.log'
        raw = log[offset:offset + SEGMENT_BYTES]
        path.write_bytes(raw)
        segments.append({'stream': 'stderr', 'segment': index,
                         'relative_path': path.relative_to(chain.data).as_posix(),
                         'bytes': len(raw), 'sha256': sha256(raw).hexdigest(),
                         'chunk_bytes': 65536,
                         'chunk_sha256': [sha256(raw[start:start + 65536]).hexdigest()
                                          for start in range(0, len(raw), 65536)]})
    job = {'id': identity, 'project_id': chain.project_id, 'state': 'COMPLETED', 'artifacts': [],
           'created': 1_800_000_000, 'updated': 1_800_000_000, 'owner_epoch': 0,
           'project_root': str(chain.project), 'output_dir': str(output), 'input_files': [],
           'process_instance': 'synthetic-log-fixture-not-executed', 'data_root': str(chain.data), 'logs': segments}
    with closing(sqlite3.connect(chain.data / 'tasks.sqlite3')) as db:
        db.execute('CREATE TABLE IF NOT EXISTS core_tool_jobs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,state TEXT NOT NULL,data TEXT NOT NULL,updated REAL NOT NULL)')
        db.execute('INSERT INTO core_tool_jobs VALUES(?,?,?,?,?)',
                   (identity, chain.project_id, 'COMPLETED', canonical(job), 1_800_000_000))
        db.commit()
    return identity, segments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', default=str(BASE.parent / 'artifacts/stage1/workdesk-core.exe'))
    parser.add_argument('--expected-sha', help='Optional expected candidate binary SHA256')
    args = parser.parse_args()
    run = BASE.parent / 'artifacts' / 'stage1' / 'perf' / ('run-' + uuid.uuid4().hex[:8])
    run.mkdir(parents=True)
    chain = Chain(args.executable)
    if args.expected_sha:
        require(sha256(chain.executable.read_bytes()).hexdigest() == args.expected_sha.lower(), 'Go候选摘要与指定版本不符。')
    before = Before(run / 'before-src', run / 'before-user/libraries/main', run / 'before-user')
    evidence = {'schema': 1, 'kind': 'STAGE1_ONE_PASS_SYNTHETIC_READ_OBSERVATION',
                'recorded_utc': datetime.now(timezone.utc).isoformat(), 'real_model_requests': 0,
                'go_fixture': str(chain.root), 'old_fixture': str(run), 'measurements_per_route': 1,
                'go_source_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=BASE, text=True).strip(),
                'go_source_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=BASE, text=True).strip()),
                'go_binary_sha256': sha256(chain.executable.read_bytes()).hexdigest(),
                'method': 'One sequential old/new pass; no cache clearing or statistical speed claim. Wall response time includes HTTP and bytes, JSON parsing is separate.',
                'scope_differences': [
                    'Old record-catalog includes display/archive/project scope and pinned/created ordering; Go records is a narrower typed index ordered by updated. Both return the same newest 50 chats in this fixture; general algorithms are not equivalent.',
                    'Old task detail bundles task, events, usage and control state; Go task record is a base record and events use a separate endpoint. Event transport cost is shared with old detail, not an independent old event measurement.',
                    'Old material endpoint is a whole-file API with an 8MiB bound and can reject this 10MiB fixture; that rejection is recorded, not timed as equivalent work. Go job-log endpoint reads a listed, hashed segment and a requested byte range. The log record is synthetic COMPLETED metadata and no tool/model is invoked.'
                ]}
    try:
        evidence['before_source'] = archive_before(before.root)
        task, log, evidence['fixture'] = seed(chain, before.data)
        try:
            old = {'startup': before.start()}
            metric, value = measured(before.origin, '/api/v1/record-catalog?scope=directory&limit=50', before.state['viewer'])
            metric.update(records_returned=len(value.get('records', [])), has_more=value.get('has_more'))
            require(metric['http_status'] == 200 and metric['records_returned'] == 50, '旧列表未返回50条。')
            old['list_50'], old_ids = metric, [row['id'] for row in value['records']]
            metric, value = measured(before.origin, '/api/v1/task?' + urlencode({'id': task, 'after': 0}), before.state['viewer'])
            require(metric['http_status'] == 200, '旧详情接口未通过。')
            metric.update(events_returned=len(value.get('events', [])), has_more_events=value.get('has_more_events'))
            old['detail_with_events_200'] = metric
            require(metric['events_returned'] == 200 and metric['has_more_events'], '旧事件边界不是200条。')
            metric, value = measured(before.origin, '/api/v1/material?' + urlencode({'id': task, 'file': 'long.log'}), before.state['viewer'])
            if metric['http_status'] == 200:
                metric['decoded_content_bytes'] = len(value.get('content', '').encode())
                metric['whole_file_received'] = metric['decoded_content_bytes'] == LOG_BYTES
            old['long_material'] = metric
            evidence['before'] = old
        except Exception as error:
            evidence['before_unavailable'] = clean_error(error)
        finally:
            before.stop()

        with closing(sqlite3.connect(before.data / 'tasks.sqlite3')) as db:
            evidence['before_model_request_rows'] = db.execute('SELECT COUNT(*) FROM requests').fetchone()[0]
            require(evidence['before_model_request_rows'] == 0, '旧基线意外生成模型请求。')

        chain.manifest = core_runtime.freeze(BASE, chain.data, chain.executable)
        evidence['go_implementation_revision'] = chain.manifest['implementation_revision']
        migration = core_runtime.prepare_migration(chain.profile, chain.executable, fixture=True)
        require(str(migration['storage_schema']) == '2', 'Go夹具未迁移到schema2。')
        job, segments = install_log_job(chain, log)
        evidence['log_segments'] = segments
        started = time.perf_counter()
        chain.start()
        new = {'startup': {'confirmed_ms': round((time.perf_counter() - started) * 1000, 3),
                           'health_identity_checked': True, 'poll_interval_ms': 150}}
        metric, value = measured(chain.origin, '/api/v2/records?limit=50', chain.backend['viewer'])
        metric.update(records_returned=len(value.get('records', [])), has_more=value.get('has_more'))
        require(metric['http_status'] == 200 and metric['records_returned'] == 50 and metric['has_more'], 'Go列表未按50条分页。')
        require('PERF_LONG_LOG' not in canonical(value), 'Go列表意外携带完整日志。')
        new['list_50'] = metric
        if 'before' in evidence:
            evidence['same_first_page_record_ids'] = old_ids == [row['id'] for row in value['records']]
        metric, value = measured(chain.origin, '/api/v2/records/task/' + task, chain.backend['viewer'])
        require(metric['http_status'] == 200 and value['record']['id'] == task, 'Go任务详情未承接原身份。')
        metric.update(events_embedded='events' in value, log_embedded='PERF_LONG_LOG' in canonical(value))
        new['detail_base_record'] = metric
        metric, value = measured(chain.origin, '/api/v2/events?after=0&limit=200', chain.backend['viewer'])
        metric.update(events_returned=len(value.get('events', [])), has_more=value.get('has_more'), cursor=value.get('cursor'))
        require(metric['http_status'] == 200 and metric['events_returned'] == 200 and metric['has_more'], 'Go事件未按200条增量返回。')
        new['events_200'] = metric
        query = urlencode({'stream': 'stderr', 'segment': 1, 'offset': 65536, 'limit': 65536})
        metric, value = measured(chain.origin, '/api/v2/jobs/' + job + '/log?' + query, chain.backend['viewer'])
        if metric['http_status'] == 200:
            raw = base64.b64decode(value['data'], validate=True)
            expected = log[SEGMENT_BYTES + 65536:SEGMENT_BYTES + 131072]
            require(raw == expected and len(raw) == 65536, 'Go日志块不是所请求的准确字节。')
            metric.update(decoded_block_bytes=len(raw), block_sha256=sha256(raw).hexdigest(),
                          total_segment_bytes=value['total_bytes'], offset=value['offset'],
                          next_offset=value['next_offset'], has_more=value['has_more'], exact_original_range=True,
                          verified_bytes=value.get('verified_bytes'))
            require(metric['response_bytes'] < 100000 and value['has_more'], 'Go日志响应没有保持有界读取。')
            require(value.get('verified_bytes') == 65536, 'Go日志块重新扫描了完整段，未按块校验。')
        else:
            evidence['log_chunk_gap'] = 'Product job-log endpoint not available; no script-level seek/read counted as implementation.'
        new['log_chunk_64k'] = metric
        evidence['after'] = new
        chain.stop()
        with closing(sqlite3.connect(chain.data / 'tasks.sqlite3')) as db:
            evidence['after_model_request_rows'] = db.execute('SELECT COUNT(*) FROM requests').fetchone()[0]
            require(evidence['after_model_request_rows'] == 0, '检查意外生成模型请求。')
        evidence['status'] = 'PASSED' if 'before' in evidence and 'log_chunk_gap' not in evidence else 'PARTIAL_RECORDED'
    except Exception as error:
        evidence.update(status='FAILED', error=clean_error(error))
    finally:
        before.stop()
        chain.cleanup()
        target = run / 'check.json'
        target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Evidence: ' + str(target))
        print('Status: ' + evidence.get('status', 'FAILED') + '; model requests: 0')
    return 0 if evidence.get('status') in {'PASSED', 'PARTIAL_RECORDED'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
