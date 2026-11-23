"""User-initiated Codex browser authentication, isolated until verified."""

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock, Timer
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

from . import workspace
from .paths import clean_env, legacy
from .store import uid


LOGIN_TIMEOUT = 600


def work_login_options(locations, codex_available):
    from . import setup_deepseek_account, setup_qwen

    return {'codex': 'connect' if codex_available else 'unavailable',
            'deepseek': 'launch' if setup_deepseek_account.available(locations) else 'unavailable',
            'qwen': 'launch' if setup_qwen.available(locations) else 'guide'}


def start_work_login(service, payload):
    from . import setup_deepseek_account, setup_qwen

    if set(payload) - {'provider'}:
        raise ValueError('请选择官方工作端。')
    provider = payload.get('provider', 'codex')
    if provider == 'codex':
        return service.account_setup.start({})
    if provider == 'deepseek':
        return {**setup_deepseek_account.launch(service.locations),
                'message': '请在官方 DeepSeek Harness 内完成授权。Workdesk 的 DeepSeek Work 当前使用 API Key。'}
    if provider == 'qwen':
        if setup_qwen.available(service.locations):
            return {**setup_qwen.launch(service.locations),
                    'message': '请在官方 Qwen Code 完成授权。Workdesk 的 Qwen Work 当前使用 API Key。'}
        return {'state': 'OPENED', 'guide': setup_qwen.GUIDE,
                'message': '已打开官方登录指南。Qwen Code 的账号授权需要支持订阅登录的版本。'}
    raise ValueError('请选择官方工作端。')


def update_work_login(service, payload, *, cancel=False):
    if set(payload) - {'id', 'provider'} or payload.get('provider', 'codex') != 'codex' or 'id' not in payload:
        raise ValueError('本次登录已失效，请重新开始。')
    method = service.account_setup.cancel if cancel else service.account_setup.poll
    return method({'id': payload['id']})


def _authorize_url(value):
    if not isinstance(value, str) or len(value) > 8192 or any(ord(char) <= 32 or ord(char) >= 127 for char in value):
        raise ValueError('官方登录地址无效。')
    url = urlsplit(value)
    if (url.scheme, url.netloc, url.path) != ('https', 'auth.openai.com', '/oauth/authorize') or not url.query or url.fragment:
        raise ValueError('官方登录地址无效。')
    return value


@dataclass
class _Login:
    id: str
    home: Path
    previous_home: str | None
    expires: float
    rpc: object = field(default=None, repr=False)
    login_id: str = field(default='', repr=False)
    catalog: dict | None = field(default=None, repr=False)
    completions: deque = field(default_factory=lambda: deque(maxlen=8), repr=False)
    timer: Timer | None = field(default=None, repr=False)
    state: str = 'PENDING'
    message: str | None = None

    def receive(self, packet):
        if packet.get('method') == 'account/login/completed':
            params = packet.get('params')
            if isinstance(params, dict):
                self.completions.append((params.get('loginId'), params.get('success') is True))

    def result(self):
        return {'state': self.state, **({'message': self.message} if self.message else {})}


class AccountSetup:
    def __init__(self, service):
        self.s = service
        self._lock = RLock()
        self._login = None
        self._closed = False

    def _selected_home(self):
        locations = self.s.locations
        refs = locations.credential_references()
        if refs is not None:
            return refs.get('codex_home')
        home = locations.codex_home
        return str(home) if (home / 'auth.json').is_file() else None

    def start(self, payload):
        if payload != {}:
            raise ValueError('请在打开的 OpenAI 页面登录。')
        with self._lock:
            if self._closed or self.s.retiring:
                raise ValueError('工作台正在重启，请稍后重新登录。')
            if self._login and self._login.state == 'PENDING':
                raise ValueError('已有一次登录正在等待。请完成或取消它后再开始。')
            locations = self.s.locations
            self.s.local_storage._require_user()
            executable = locations.codex
            if not executable:
                raise ValueError('本机尚未安装 Codex 工作端。')
            with self.s.lock:
                selected = self._selected_home()
            identity = uuid4().hex
            try:
                home = workspace.plain(locations.user / 'providers/codex/profiles' / identity, locations.user, missing=True)
                home.mkdir(mode=0o700, parents=True, exist_ok=False)
            except (OSError, ValueError):
                raise ValueError('无法建立本次本地登录目录。') from None
            login = _Login(identity, home, selected, monotonic() + LOGIN_TIMEOUT)
            self._login = login
            try:
                transport = legacy(locations, 'codex-harness', 'rpc')
                env = clean_env()
                env['CODEX_HOME'] = str(home)
                command = transport.codex_command(str(executable)) + ['-c', 'project_doc_max_bytes=0', '-c', 'features.apps=false']
                rpc = login.rpc = transport.Rpc(command, home, env, on_message=login.receive)
                rpc.request('initialize', {'clientInfo': {'name': 'workdesk_login', 'version': '1.0'}}, timeout=5)
                rpc.send({'method': 'initialized', 'params': {}})
                response = rpc.request('account/login/start', {'type': 'chatgpt'}, timeout=5)
                if (response.get('type') != 'chatgpt' or not isinstance(response.get('loginId'), str)
                        or not 1 <= len(response['loginId']) <= 128):
                    raise ValueError('官方工作端未返回浏览器登录回执。')
                login.login_id = response['loginId']
                url = _authorize_url(response.get('authUrl'))
                login.timer = Timer(max(0, login.expires - monotonic()), self._expire, args=(identity,))
                login.timer.daemon = True
                login.timer.start()
                return {'id': identity, 'url': url}
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                self._finish(login, 'FAILED')
                raise ValueError('未能启动 OpenAI 登录。请稍后重试。') from None

    def _lookup(self, payload):
        if (not isinstance(payload, dict) or set(payload) != {'id'} or self._login is None
                or payload['id'] != self._login.id):
            raise ValueError('本次登录已失效，请重新开始。')
        return self._login

    @staticmethod
    def _close_rpc(login):
        rpc, login.rpc = login.rpc, None
        if rpc is not None:
            try:
                rpc.close()
            except Exception:
                # The official client may have exited before we could close its pipes.
                pass

    def _finish(self, login, state, message=None):
        login.state, login.message = state, message
        if login.timer is not None:
            login.timer.cancel()
        self._close_rpc(login)
        login.catalog = None
        login.completions.clear()
        if state != 'CONNECTED':
            self._discard_staged_auth(login)
        return login.result()

    def _discard_staged_auth(self, login):
        # If credential selection reached disk before an uncertain command
        # receipt, the storage journal owns recovery and the profile must stay.
        try:
            with self.s.lock:
                if self._selected_home() == str(login.home):
                    return
                auth = workspace.plain(login.home / 'auth.json', self.s.locations.user, missing=True)
                auth.unlink(missing_ok=True)
                if login.home.is_dir() and not any(login.home.iterdir()):
                    login.home.rmdir()
        except (OSError, ValueError):
            # Keep an uncertain file intact instead of deleting another profile.
            pass

    def _expire(self, identity):
        with self._lock:
            if self._login and self._login.id == identity and self._login.state == 'PENDING':
                self._finish(self._login, 'FAILED', '本次登录等待已超时，请重新开始。')

    def _verify(self, login):
        rpc = login.rpc
        for _ in range(32):
            if login.completions:
                identity, success = login.completions.popleft()
                if identity != login.login_id:
                    continue
                if not success:
                    raise ValueError('官方登录未完成。')
                self.s.locations.validate_codex_home(str(login.home))
                login.catalog = self.s.profiles.observe_codex(rpc, login.home, timeout=2)
                self._close_rpc(login)
                return
            rpc.pump(timeout=0)

    def _activate(self, login):
        with self.s.lock:
            if self.s.retiring or self._selected_home() != login.previous_home:
                return self._finish(login, 'FAILED', '工作台或账号选择已变化，请重新开始登录。')
            with self.s.store.read() as db:
                try:
                    self.s.persistence.idle(db)
                except ValueError:
                    login.message = '官方登录已完成。请等待运行结束或核对待处理任务后保存连接。'
                    return login.result()
            self.s.command('storage.credentials.select', {'codex_home': str(login.home)}, uid())
            try:
                self.s.profiles.install('codex', login.catalog, expected_codex_home=login.home)
            except (OSError, RuntimeError, ValueError):
                return self._finish(login, 'CONNECTED', '账号已保存。暂未更新模型目录，可在账号设置中刷新。')
        return self._finish(login, 'CONNECTED')

    def poll(self, payload):
        with self._lock:
            login = self._lookup(payload)
            if login.state != 'PENDING':
                return login.result()
            if monotonic() >= login.expires:
                return self._finish(login, 'FAILED', '本次登录等待已超时，请重新开始。')
            try:
                if login.catalog is None:
                    self._verify(login)
                return self._activate(login) if login.catalog is not None else login.result()
            except (AttributeError, LookupError, OSError, RuntimeError, TypeError, ValueError):
                return self._finish(login, 'FAILED', '未能核对并保存本次登录。请重新开始。')

    def cancel(self, payload):
        with self._lock:
            login = self._lookup(payload)
            if login.state == 'CONNECTED':
                raise ValueError('此账号已经连接。')
            if login.rpc is not None and login.login_id:
                try:
                    login.rpc.request('account/login/cancel', {'loginId': login.login_id}, timeout=3)
                except (OSError, RuntimeError, ValueError):
                    pass
            return self._finish(login, 'CANCELLED')

    def close(self):
        with self._lock:
            self._closed = True
            if self._login and self._login.state == 'PENDING':
                self._finish(self._login, 'CANCELLED')
