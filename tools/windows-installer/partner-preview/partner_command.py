"""Forward one explicit partner installation to the authenticated Workdesk release owner."""
from pathlib import Path
import json
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_instance as release


class InstallNeedsReview(RuntimeError):
    """A possibly running or published operation must be checked by its owner."""


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def install(request, report=None):
    root = Path(__file__).resolve().parents[1]
    profile = release.require_profile()
    if Path(profile['program_root']).resolve() != root or profile['channel'] != 'release':
        raise ValueError('请选择与当前安装目录匹配的正式实例。')
    dev = release.dev
    backend = dev.checked_backend(profile)
    if backend is None:
        result = subprocess.run([sys.executable, '-B', '-s', '-X', 'utf8',
                                 str(root / 'tools' / 'release_instance.py'), 'start', '--no-open'],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, timeout=120, cwd=root)
        if result.returncode:
            raise RuntimeError('Workdesk 安装实例启动失败，请先从主程序查看启动状态。')
        backend = dev.required_backend(profile)

    def read(route):
        return dev.request_json(backend['origin'] + '/api/v2/' + route,
                                token=backend['state']['viewer'], timeout=20)

    partner, version, expected = (request[k] for k in ('id', 'version', 'release_id'))
    if not all(isinstance(v, str) and v and len(v) < 256 for v in (partner, version, expected)):
        raise ValueError('伙伴版本选择无效。')
    payload = {'partner_id': partner, 'version': version, 'expected_release': expected}
    bundle = request.get('bundle')
    if bundle:
        bundle_path = Path(bundle)
        if not bundle_path.is_absolute() or bundle_path.suffix.lower() not in {'.wdpa', '.wdpartner'}:
            raise ValueError('请选择 Workdesk 伙伴补充包。')
        payload['bundle_path'] = str(bundle_path)
    pending = [op for op in read('component-operations')['operations'] if op.get('partner_id') == partner
               and op.get('state') not in {'COMPLETED', 'FAILED', 'FAILED_NO_SEND', 'CANCELLED', 'STOPPED_RUNTIME'}]
    if pending:
        raise InstallNeedsReview('这个伙伴还有未结操作，请先在 Workdesk 账号与模型中核对原回执。')
    try:
        receipt = dev.command(profile, backend, 'component.prepare', payload)
        operation_id = str(uuid.UUID(receipt['operation_id']))
    except dev.RequestUncertain as exc:
        raise InstallNeedsReview(str(exc)) from None
    except (KeyError, ValueError, TypeError):
        raise InstallNeedsReview('安装回执无法识别，请先核对原操作。') from None

    deadline = time.monotonic() + 31 * 60
    previous = None
    while time.monotonic() < deadline:
        try:
            operations = read('component-operations')['operations']
            op = next((o for o in operations if o['id'] == operation_id), {})
        except (dev.RequestUncertain, dev.RequestRejected, KeyError, TypeError):
            raise InstallNeedsReview('暂时无法读取安装回执，请在 Workdesk 核对原操作。') from None
        state = op.get('state')
        if state == 'COMPLETED':
            row = op.get('result') or {}
            if row.get('id') != expected or row.get('state') != 'AVAILABLE':
                raise InstallNeedsReview('组件安装回执与所选固定版本不符，请核对原操作。')
            return {'ok': True, 'id': partner, 'release_id': expected, 'operation_id': operation_id}
        if state in {'FAILED', 'FAILED_NO_SEND', 'CANCELLED', 'STOPPED_RUNTIME'}:
            raise RuntimeError(op.get('error') or '安装未完成。')
        if state not in {'QUEUED', 'RUNNING'}:
            raise InstallNeedsReview(op.get('error') or '操作状态待核对，请查看 Workdesk 组件记录。')
        progress = op.get('progress') or {'phase': 'prepare', 'done': 0, 'total': 0}
        if report is not None and progress != previous:
            report({'type': 'progress', **progress})
            previous = progress
        time.sleep(.7)
    raise InstallNeedsReview('组件安装仍待核对，请查看原操作记录。')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        req = json.loads(sys.stdin.buffer.readline(65537).decode('utf-8-sig'))
        emit(install(req, emit if '--progress' in sys.argv[1:] else None))
        return 0
    except Exception as exc:
        emit({'ok': False, 'error': str(exc)[:700], 'retryable': not isinstance(exc, InstallNeedsReview)})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
