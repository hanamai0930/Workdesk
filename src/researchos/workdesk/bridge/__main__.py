"""Go-owned compatibility worker; no HTTP listener and no autonomous scheduler."""
from email.message import Message
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit

from . import bind, binding, enabled
from .rpc import Peer


SOURCE_ROOT = Path(__file__).resolve().parents[4]


def install_profile(value):
    """A trusted parent handshake scopes all old location readers in this child."""
    profile = value.get('profile')
    if profile is None:
        return
    if (not isinstance(profile, dict) or profile.get('schema') != 1 or
            profile.get('channel') not in ('development', 'release')):
        raise ValueError('应用核心提供的实例配置无效。')
    profile = dict(profile)
    profile['config_path'] = value.get('config_path') or os.environ.get('WORKDESK_INSTANCE_CONFIG')
    if not profile['config_path'] or not Path(profile['config_path']).is_absolute():
        raise ValueError('应用核心未提供准确配置位置。')
    profile.setdefault('initial_data_root', profile['data_root'])
    expected = {'program_root': value['base'], 'data_root': value['data'], 'user_root': value.get('user')}
    for key, path in expected.items():
        if not isinstance(profile.get(key), str) or Path(profile[key]).resolve() != Path(path).resolve():
            raise ValueError('应用核心配置与兼容运行位置不符。')
    from .. import instance
    allowed = {Path(value['base']).resolve(), SOURCE_ROOT}

    def load_profile(base=None):
        if base is not None and Path(base).resolve() not in allowed:
            raise ValueError('兼容业务不能切换到另一程序实例。')
        # Never share a mutable dictionary between old callers.
        return json.loads(json.dumps(profile))

    instance.load_profile = load_profile


def headers(body=None, origin=None):
    value = Message()
    value['Host'] = '127.0.0.1:0'
    if origin:
        value['Origin'] = origin
    if body is not None:
        value['Content-Type'] = 'application/json'
        value['Content-Length'] = str(len(body))
    return value


class Legacy:
    def __init__(self):
        self.peer = None
        self.service = None
        self.ui_module = None
        self.ui_server = None
        self.init_lock = threading.Lock()
        self.closed = False

    def initialize(self, value):
        with self.init_lock:
            if self.service is not None:
                raise ValueError('兼容业务已经初始化。')
            bind(self.peer, value)
            install_profile(value)
            from .process import install
            install()
            from ..paths import Locations
            from ..service import Service
            locations = Locations.resolve(value['base'], value['data'], user=value.get('user'))
            self.service = Service(locations)
            return {'ready': True, 'process_instance': self.service.instance,
                    'implementation': self.service.implementation, 'owner_epoch': value['owner_epoch'],
                    'scheduler': 'CORE_DRIVEN', 'database_owner': 'GO'}

    def require(self):
        if self.service is None or self.closed:
            raise ValueError('兼容业务尚未初始化或已经退出。')

    def request(self, params):
        self.require()
        method, path = params.get('method', 'GET'), params.get('path', '')
        if method not in {'GET', 'POST'} or not path.startswith('/api/v1/'):
            raise ValueError('兼容接口路径不支持。')
        from ..server import Handler
        result = {}

        class Capture(Handler):
            def reply(self, status, value):
                result.update(status=status, body=value)

            def auth(self, write=False):
                return True  # Public authentication was checked by the owning Go API.

        body = json.dumps(params.get('body', {}), ensure_ascii=False).encode('utf-8')
        handler = object.__new__(Capture)
        handler.path, handler.headers = path, headers(body if method == 'POST' else None)
        handler.rfile = BytesIO(body)
        handler.server = SimpleNamespace(service=self.service, shutdown=lambda: None)
        getattr(handler, 'do_' + method)()
        result['retiring'] = self.service.retiring
        return result

    def ui_request(self, params):
        self.require()
        with self.init_lock:
            if self.ui_module is None:
                path = SOURCE_ROOT / 'workdesk-ui' / 'server.py'
                sys.path.insert(0, str(path.parent))
                spec = importlib.util.spec_from_file_location('workdesk_legacy_ui_server', path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                self.ui_module = module
        module = self.ui_module
        legacy = self

        class Backend:
            locations = legacy.service.locations
            expected_implementation = legacy.service.implementation

            def request(self, route, value=None):
                answer = legacy.request({'method': 'GET' if value is None else 'POST',
                                         'path': '/api/v1/' + route, 'body': value})
                if answer['status'] != 200:
                    raise ValueError(answer['body'].get('error', '兼容接口拒绝请求。'))
                return answer['body']

            def discover(self):
                return {'instance': legacy.service.instance}

        origin = params.get('origin')
        parsed_origin = urlsplit(origin or '')
        if parsed_origin.scheme != 'http' or parsed_origin.hostname != '127.0.0.1' or not parsed_origin.port:
            raise ValueError('兼容界面必须绑定本次本地入口。')
        if self.ui_server is None:
            self.ui_server = SimpleNamespace(backend=Backend(),
                workspace=module.Workspace(base=self.service.locations.base), workspace_lock=threading.RLock(),
                preference_root=self.service.locations.user, prefix='/', origin=origin,
                server_port=parsed_origin.port, revision=self.service.implementation[:16],
                instance=self.service.instance, shutdown=lambda: None)
        elif self.ui_server.origin != origin:
            raise ValueError('兼容界面入口已改变。')
        method, path = params.get('method', 'GET'), params.get('path', '')
        if method not in {'GET', 'POST'} or not path.startswith('/api/'):
            raise ValueError('兼容界面路径不支持。')
        if path.split('?', 1)[0] == '/api/dev-exit':
            raise ValueError('开发退出由应用核心处理。')
        result = {}

        class Capture(module.Handler):
            def reply(self, status, value, mime='application/json; charset=utf-8', allow_embed=False):
                result.update(status=status, body=value, mime=mime, allow_embed=allow_embed)

            def authorize(self):
                return True

        body = json.dumps(params.get('body', {}), ensure_ascii=False).encode('utf-8')
        handler = object.__new__(Capture)
        handler.path = path
        handler.headers = headers(body if method == 'POST' else None, origin)
        handler.rfile, handler.server = BytesIO(body), self.ui_server
        getattr(handler, 'do_' + method)()
        result['retiring'] = self.service.retiring
        return result

    def dispatch(self, method, params):
        if method == 'legacy.initialize':
            return self.initialize(params)
        self.require()
        if method == 'partner.process.inspect':
            from ..paths import process_identity
            pid = params.get('pid')
            if type(pid) is not int or pid <= 0:
                raise ValueError('进程核对需要准确PID。')
            return process_identity(pid)
        if method in {'partner.secret.same', 'partner.secret.valid-chat', 'partner.secret.legacy-ready'}:
            import hmac
            from ..chat_api_credentials import read_key
            from ..paths import legacy
            provider = params.get('provider')
            if provider not in ('deepseek','qwen'):
                raise ValueError('所选普通 Chat API 厂商无效。')
            if method == 'partner.secret.legacy-ready':
                secret = self.service.profiles.credentials().get(provider)
                return {'available': bool(secret and (provider != 'qwen' or secret.billing_kind == 'general_api'))}
            raw = read_key(self.service.locations, params['account_id'], params['version'])
            if method == 'partner.secret.same':
                source = read_key(self.service.locations, params['source_account_id'], params['source_version'])
                return {'matches': hmac.compare_digest(raw, source)}
            key = raw.decode('utf-8')
            reader = legacy(self.service.locations, 'provider-harness', 'credentials')
            secret = reader.credential(provider, reader.KEY_NAMES[provider][0], key)
            return {'available': bool(key and not any(c.isspace() for c in key) and (provider != 'qwen' or secret.billing_kind == 'general_api'))}
        if method == 'partner.secret.reuse-legacy':
            provider = params.get('provider')
            if provider not in ('deepseek','qwen'):
                raise ValueError('所选普通 Chat API 厂商无效。')
            secret = self.service.profiles.credentials().get(provider)
            if secret is None or (provider == 'qwen' and secret.billing_kind != 'general_api'):
                raise ValueError('尚未配置此厂商普通 Chat API Key。')
            return self.dispatch('partner.secret.store', {'account_id':params['account_id'], 'version':params['version'],
                                                           'key':secret.reveal_for_provider()})
        if method in {'partner.secret.store', 'partner.secret.read', 'partner.secret.matches', 'partner.secret.reuse'}:
            import uuid
            from ..credential_vault import encode, decode
            from ..paths import atomic_bytes
            identity = str(uuid.UUID(params['account_id']))
            version = str(uuid.UUID(params['version']))
            secret_root = self.service.locations.user / 'providers' / 'partner-secrets'
            secret_root.mkdir(parents=True, exist_ok=True)
            path = secret_root / (identity + '-' + version + '.vault')
            from ..workspace import plain
            plain(path, self.service.locations.user, missing=method in {'partner.secret.store', 'partner.secret.reuse'})
            if method == 'partner.secret.reuse':
                import hmac
                source_id = str(uuid.UUID(params['source_account_id']))
                source_version = str(uuid.UUID(params['source_version']))
                source = secret_root / (source_id + '-' + source_version + '.vault')
                plain(source, self.service.locations.user)
                sealed = source.read_bytes()
                # Copy the protected envelope directly; no cleartext crosses
                # the bridge and changing an alias cannot replace another key.
                if path.exists():
                    if not hmac.compare_digest(decode(path.read_bytes()), decode(sealed)):
                        raise ValueError('凭据引用已存在，保留原版本。')
                else:
                    atomic_bytes(path, sealed)
                return {'stored': True, 'version': version}
            if method == 'partner.secret.matches':
                import hmac
                key = params.get('key')
                if not isinstance(key,str) or len(key) > 8192:
                    raise ValueError('请输入有效 API Key。')
                return {'matches': hmac.compare_digest(decode(path.read_bytes()), key.strip().encode('utf-8'))}
            if method == 'partner.secret.store':
                key = params.get('key')
                if not isinstance(key, str) or not key.strip() or len(key) > 8192 or any(c in key for c in '\r\n\0'):
                    raise ValueError('请输入有效 API Key。')
                if path.exists():
                    if decode(path.read_bytes()).decode('utf-8') == key.strip():
                        return {'stored': True, 'version': version}
                    raise ValueError('凭据版本已存在，保留原版本。')
                atomic_bytes(path, encode(key.strip().encode('utf-8')))
                return {'stored': True, 'version': version}
            return {'key': decode(path.read_bytes()).decode('utf-8')}
        if method == 'legacy.request':
            return self.request(params)
        if method == 'legacy.ui_request':
            return self.ui_request(params)
        if method == 'legacy.tick':
            self.service.collaboration.tick()
            return {'retiring': self.service.retiring}
        if method == 'legacy.idle':
            with self.service.store.read() as db:
                self.service.persistence.idle(db)
            return {'idle': True, 'health': self.service.health()}
        if method == 'legacy.close':
            self.service.close()
            self.closed = True
            return {'closed': True}
        raise ValueError('兼容操作不存在。')


def main():
    if not enabled():
        raise ValueError('兼容入口只能由应用核心启动。')
    protocol_writer = sys.stdout.buffer
    sys.stdout = sys.stderr
    worker = Legacy()
    worker.peer = Peer(sys.stdin.buffer, protocol_writer, worker.dispatch)
    try:
        worker.peer.serve()
    finally:
        # A broken owner pipe makes all database requests fail closed.
        if worker.service is not None and not worker.closed:
            worker.service.close()


if __name__ == '__main__':
    main()
