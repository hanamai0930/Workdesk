"""Loopback-only Workdesk UI. Opening/reopening never starts a model worker."""
from __future__ import annotations

import argparse
import base64
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
import webbrowser
BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE/'src'))
from researchos.workdesk.client import Client as BackendClient
from researchos.workdesk.paths import default_user_root
from researchos.workdesk import desktop_preferences, workspace as work_files
from researchos.workdesk.setup_qwen import GUIDE as QWEN_SIGN_IN_GUIDE

from model import BASE, Workspace, READ_ERRORS
from desktop_window import launch_sized

HERE = Path(__file__).resolve().parent
STATIC = HERE / 'static'
DIST = HERE / 'dist'
APP_ID = 'workdesk-ui-02'
RUNTIME = default_user_root() / 'runtime' / 'ui'
SETUP_POST = {'api/v1/connection-setup/'+operation for operation in
              ('key', 'account-start', 'account-poll', 'account-cancel')}
ASSETS = {'appearance.js': ('appearance.js', 'text/javascript; charset=utf-8'),
'appearance.css': ('appearance.css', 'text/css; charset=utf-8'),
'settings-preferences.js': ('settings-preferences.js', 'text/javascript; charset=utf-8'),
'management.js': ('management.js', 'text/javascript; charset=utf-8'),
'work-items-page.js': ('work-items-page.js', 'text/javascript; charset=utf-8'),
'cooperation-toolbar.js': ('cooperation-toolbar.js', 'text/javascript; charset=utf-8'),
'projects-page.js': ('projects-page.js', 'text/javascript; charset=utf-8'),
'planning-page.js': ('planning-page.js', 'text/javascript; charset=utf-8'),
'libraries-page.js': ('libraries-page.js', 'text/javascript; charset=utf-8'),
'support-common.js': ('support-common.js', 'text/javascript; charset=utf-8'),
'files-page.js': ('files-page.js', 'text/javascript; charset=utf-8'),
'records-page.js': ('records-page.js', 'text/javascript; charset=utf-8'),
'results-page.js': ('results-page.js', 'text/javascript; charset=utf-8'),
'monitor-page.js': ('monitor-page.js', 'text/javascript; charset=utf-8'),
'': ('index.html', 'text/html; charset=utf-8'),
          'app.js': ('app.js', 'text/javascript; charset=utf-8'),
          'control.js': ('control.js', 'text/javascript; charset=utf-8'),
          'collaboration.js': ('collaboration.js', 'text/javascript; charset=utf-8'),
          'operations.js': ('operations.js', 'text/javascript; charset=utf-8'),
          'extensions.js': ('extensions.js', 'text/javascript; charset=utf-8'),
          'ui-modules.js': ('ui-modules.js', 'text/javascript; charset=utf-8'),
          'settings.js': ('settings.js', 'text/javascript; charset=utf-8'),
          'settings.css': ('settings.css', 'text/css; charset=utf-8'),
          'workbench.js': ('workbench.js', 'text/javascript; charset=utf-8'),
          'workbench-features.js': ('workbench-features.js', 'text/javascript; charset=utf-8'),
          'independent-work.js': ('independent-work.js', 'text/javascript; charset=utf-8'),
          'chat.js': ('chat.js', 'text/javascript; charset=utf-8'),
          'chat-api.js': ('chat-api.js', 'text/javascript; charset=utf-8'),
          'chat-records.js': ('chat-records.js', 'text/javascript; charset=utf-8'),
          'work-records.js': ('work-records.js', 'text/javascript; charset=utf-8'),
          'ui-dialogs.js': ('ui-dialogs.js', 'text/javascript; charset=utf-8'),
          'workbench.css': ('workbench.css', 'text/css; charset=utf-8'),
          'style.css': ('style.css', 'text/css; charset=utf-8'),
          'icon.svg': ('icon.svg', 'image/svg+xml')}


def build_id(backend_revision=None):
    digest = sha256(str(BASE).encode())
    digest.update((backend_revision or BackendClient(BASE).expected_implementation).encode())
    for path in sorted([HERE / 'server.py', HERE / 'model.py', HERE / 'desktop_window.py', HERE / 'desktop_host.py', HERE / 'pick_directory.py', *STATIC.glob('*'), *DIST.rglob('*'), *(HERE/'desktop').glob('*')]):
        if path.is_file():
            digest.update(str(path.relative_to(HERE)).encode()); digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address=('127.0.0.1', 0), workspace=None):
        super().__init__(address, Handler)
        self.workspace = workspace or Workspace()
        self.backend = BackendClient(BASE)
        self.token = secrets.token_urlsafe(24)
        self.revision = build_id(self.backend.expected_implementation)
        self.instance = secrets.token_hex(16)
        self.origin = f'http://127.0.0.1:{self.server_port}'
        self.prefix = f'/s/{self.token}/'
        self.workspace_lock = threading.Lock()
        self.preference_root = default_user_root()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # No source bodies or local access token in logs.

    def reply(self, status, body, mime='application/json; charset=utf-8',allow_embed=False):
        if not isinstance(body, bytes): body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        ancestor="'self'" if allow_embed else "'none'"
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src 'self' https://chatgpt.com https://chat.deepseek.com https://chat.qwen.ai; frame-ancestors "+ancestor+"; base-uri 'none'; form-action 'none'")
        self.end_headers(); self.wfile.write(body)

    def valid_host(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

    def authorize(self):
        if not self.valid_host(): self.reply(403, {'error': '不支持的访问地址。'}); return False
        origin = self.headers.get('Origin')
        if origin and origin != self.server.origin:
            self.reply(403, {'error': '只接受本地工作台请求。'}); return False
        if not urlsplit(self.path).path.startswith(self.server.prefix):
            self.reply(404, {'error': '入口不存在，请重新打开 Workdesk。'}); return False
        return True

    def do_GET(self):
        if urlsplit(self.path).path == '/health' and self.valid_host():
            self.reply(200, {'app': APP_ID, 'revision': self.server.revision, 'instance': self.server.instance}); return
        if not self.authorize(): return
        parsed = urlsplit(self.path)
        route = parsed.path[len(self.server.prefix):]
        try:
            if route == '':
                if not (DIST / 'index.html').is_file():
                    self.reply(503, {'error': 'React 界面尚未构建，请在 workdesk-ui 执行 pnpm install 和 pnpm build。'}); return
                self.reply(200, (DIST / 'index.html').read_bytes(), 'text/html; charset=utf-8'); return
            if route.startswith('assets/'):
                asset = (DIST / route).resolve()
                if not asset.is_relative_to((DIST / 'assets').resolve()) or asset.suffix not in {'.js', '.css'} or not asset.is_file():
                    self.reply(404, {'error': '界面资源不存在。'}); return
                self.reply(200, asset.read_bytes(), ('text/javascript' if asset.suffix == '.js' else 'text/css') + '; charset=utf-8'); return
            if route in ASSETS:
                filename, mime = ASSETS[route]
                self.reply(200, (STATIC / filename).read_bytes(), mime); return
            args = parse_qs(parsed.query)
            if route == 'api/preferences':
                self.reply(200, {**desktop_preferences.snapshot(self.server.preference_root), 'chatApiModels': desktop_preferences.API_MODELS}); return
            if route == 'api/resource-space-pdf':
                value = self.server.backend.request('resource-space/file?' + parsed.query).get('file', {})
                if value.get('preview') != 'pdf' or args.get('sha256', [''])[0] != value.get('sha256'):
                    self.reply(409, {'error': '文件版本已变化，请重新预览。'}); return
                self.reply(200, base64.b64decode(value['data'], validate=True), mime='application/pdf', allow_embed=True); return
            pdf_routes = {'api/file-preview': 'file-inspect?', 'api/result-file-preview': 'result-preview?',
                          'api/fixed-artifact-file-preview': 'fixed-artifact-preview?'}
            if route in pdf_routes:
                value=self.server.backend.request(pdf_routes[route]+parsed.query)
                if value.get('kind')!='pdf' or args.get('sha256',[''])[0]!=value.get('sha256'):
                    self.reply(409,{'error':'PDF类型或文件版本已变化，请重新预览。'});return
                self.reply(200,base64.b64decode(value['data']),mime='application/pdf',allow_embed=True);return
            if route.startswith('api/v1/'):
                self.reply(200,self.server.backend.request(route[len('api/v1/'):]+('?' + parsed.query if parsed.query else '')))
                return
            with self.server.workspace_lock:
                if route == 'api/workspace': value = self.server.workspace.catalog()
                elif route == 'api/attempt': value = self.server.workspace.detail(args.get('id', [''])[0])
                elif route == 'api/history': value = self.server.workspace.history_detail(args.get('id', [''])[0])
                else: self.reply(404, {'error': '页面不存在。'}); return
            self.reply(200, value)
        except READ_ERRORS as exc: self.reply(409, {'error': str(exc), 'state': '未知'})

    def do_POST(self):
        if not self.authorize(): return
        if self.headers.get('Origin') != self.server.origin:
            self.reply(403, {'error': '操作必须来自本地工作台。'}); return
        new_route=urlsplit(self.path).path[len(self.server.prefix):]
        if new_route not in SETUP_POST | {'api/stop','api/v1/command','api/v1/profile-check','api/v1/resource-quota-check',
                'api/v1/settings-qwen-quota','api/v1/settings-qwen-quota-login','api/v1/settings-qwen-quota-cancel','api/v1/coordinator-text-rework-recovery',
                'api/v1/fixed-public-skills','api/v1/fixed-capability-preview','api/open-chat','api/pick-directory',
                'api/preferences','api/preferences/recover','api/open-project-file'}:
            self.reply(404, {'error': '此界面没有任务派发入口。'}); return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            maximum=16*1024*1024 if new_route.startswith('api/v1/') and new_route not in SETUP_POST else 4096
            if length<=0 or maximum is not None and length>maximum or self.headers.get_content_type() != 'application/json':
                self.reply(400, {'error': '请求格式不支持。'}); return
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict): raise ValueError('请求格式不支持。')
            if new_route=='api/v1/connection-setup/account-start':
                result=self.server.backend.request('connection-setup/account-start',data)
                if result.get('state')=='OPENED':
                    if result.get('guide') is not None:
                        if data.get('provider')!='qwen' or result['guide']!=QWEN_SIGN_IN_GUIDE:
                            raise ValueError('官方登录指南地址未通过核对。')
                        open_window(QWEN_SIGN_IN_GUIDE)
                    self.reply(200,{'state':'OPENED','message':result.get('message','')});return
                try:
                    target=urlsplit(result['url'])
                    if (target.scheme,target.netloc,target.path)!=('https','auth.openai.com','/oauth/authorize') or not target.query or target.fragment:
                        raise ValueError('官方登录地址未通过核对。')
                    open_window(result['url'])
                except (ValueError,OSError):
                    self.server.backend.request('connection-setup/account-cancel',{'id':result['id']})
                    raise ValueError('无法打开官方登录页面，请重试。') from None
                self.reply(200,{'id':result['id']});return
            if new_route=='api/preferences/recover':
                self.reply(200,{**desktop_preferences.recover(self.server.preference_root,data), 'chatApiModels': desktop_preferences.API_MODELS});return
            if new_route=='api/preferences':
                if set(data)!={'key','value'} or not isinstance(data['key'],str):
                    raise ValueError('偏好设置格式无效。')
                self.reply(200,{**desktop_preferences.save(self.server.preference_root,data['key'],data['value']), 'chatApiModels': desktop_preferences.API_MODELS});return
            if new_route=='api/open-project-file':
                self.open_project_file(data)
                self.reply(200,{'opened':True});return
            if new_route=='api/open-chat':
                targets={'codex':'https://chatgpt.com/','deepseek':'https://chat.deepseek.com/','qwen':'https://chat.qwen.ai/'}
                if set(data)-{'provider','url'} or data.get('provider') not in targets:raise ValueError('聊天入口不支持。')
                target=data.get('url') or targets[data['provider']]
                parsed=urlsplit(target);expected=urlsplit(targets[data['provider']])
                if parsed.scheme!='https' or parsed.netloc!=expected.netloc:raise ValueError('仅打开所选工作端的官方 HTTPS 页面。')
                open_window(target);self.reply(200,{'opened':True});return
            if new_route=='api/pick-directory':
                if data:raise ValueError('目录选择不接受外部命令。')
                try:
                    result=subprocess.run([sys.executable,str(HERE/'pick_directory.py')],capture_output=True,text=True,encoding='utf-8',timeout=180,
                                          creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                except subprocess.TimeoutExpired:raise ValueError('目录选择已超时，请重新选择文件夹。') from None
                if result.returncode:raise ValueError('目录选择器未能打开，请重试选择文件夹。')
                self.reply(200,{'path':json.loads(result.stdout).get('path')});return
            if new_route.startswith('api/v1/'):
                self.reply(200,self.server.backend.request(new_route[len('api/v1/'):],data));return
            with self.server.workspace_lock: result = self.server.workspace.stop(data)
            self.reply(200, result)
        except (*READ_ERRORS, json.JSONDecodeError) as exc: self.reply(409, {'error': str(exc), 'state': '未知'})

    def open_project_file(self, data):
        from urllib.parse import urlencode
        fields={'project','file','sha256'}
        if (os.name!='nt' or set(data)-fields-{'mode'} or not fields<=set(data)
                or not all(isinstance(v,str) for v in data.values()) or data.get('mode','explorer')!='explorer'):
            raise ValueError('此文件无法通过本机应用打开。')
        query=urlencode({'project':data['project'],'file':data['file']})
        inspected=self.server.backend.request('file-inspect?'+query)
        projects=self.server.backend.request('workspace?view=workbench')['projects']
        project=next((p for p in projects if p['id']==data['project'] and not p.get('removed')),None)
        if not project or inspected['sha256']!=data['sha256']:
            raise ValueError('文件或项目已经变化，请重新预览。')
        root=Path(project['root'])
        name=work_files.relative(data['file'])
        path=work_files.plain(root/name,root)
        if work_files.read_blob(root,name)['sha256']!=data['sha256']:
            raise ValueError('文件版本已经变化，请重新预览。')
        mode=data.get('mode') or desktop_preferences.read(self.server.preference_root)['opener']
        if mode=='explorer' or path.suffix.lower() not in {'.md','.txt','.pdf','.png','.jpg','.jpeg','.gif','.webp','.csv'}:
            explorer=Path(os.environ.get('WINDIR',r'C:\Windows'))/'explorer.exe'
            subprocess.Popen([str(explorer),'/select,',str(path)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        else:
            os.startfile(str(path),'open')


def open_window(url, *, workdesk=False):
    if workdesk:
        from desktop_host import launch
        if launch(url,BASE,default_user_root()):return
    if not workdesk:
        # Official Chat login flows use the user's regular browser window,
        # rather than an app-mode window with hidden navigation controls.
        if webbrowser.open(url,new=2):return
        raise OSError('默认浏览器未能打开。请使用页面上的官网链接手动打开。')
    # Separate entry and title: never discovers or activates the old Tk workbench.
    candidates = [Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe',
                  Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Microsoft/Edge/Application/msedge.exe']
    for executable in candidates:
        if executable.is_file():
            command = [str(executable), '--app=' + url, '--new-window', '--window-size=1440,900']
            def spawn(args):
                return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if workdesk and os.name == 'nt':
                launch_sized(executable, command, spawn, RUNTIME / 'window-launch.json')
            else:
                spawn(command)
            return
    webbrowser.open(url, new=1)


def existing(state, revision):
    try:
        value = json.loads(state.read_text(encoding='utf-8'))
        port = value['port']
        if type(port) is not int or not 1024 <= port <= 65535: return None
        with urlopen(f'http://127.0.0.1:{port}/health', timeout=.8) as response:
            health = json.load(response)
        if (health.get('app') == APP_ID and health.get('revision') == revision
                and health.get('instance') == value['instance']
                and isinstance(value['token'], str) and len(value['token']) == 32):
            return f"http://127.0.0.1:{port}/s/{value['token']}/"
    except (OSError, ValueError, KeyError, TypeError): pass
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    revision = build_id()
    state = RUNTIME / f'instance-{revision}.json'
    # OS lock prevents double-click races; releases automatically on process exit.
    lock = open(RUNTIME / f'instance-{revision}.lock', 'a+b')
    lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        for _ in range(20):
            url = existing(state, revision)
            if url:
                if args.open: open_window(url, workdesk=True)
                return
            time.sleep(.15)
        raise RuntimeError('新版界面服务正在启动或需要重开；未转到旧版。')
    server = Server(('127.0.0.1', args.port))
    value = {'app': APP_ID, 'revision': revision, 'port': server.server_port,
             'token': server.token, 'instance': server.instance, 'pid': os.getpid()}
    temp = state.with_suffix('.tmp'); temp.write_text(json.dumps(value), encoding='utf-8'); temp.replace(state)
    url = server.origin + server.prefix
    try:
        if args.open: open_window(url, workdesk=True)
        server.serve_forever(poll_interval=.5)
    finally:
        server.server_close(); lock.close()
        try: state.unlink()
        except OSError: pass


if __name__ == '__main__':
    try: main()
    except Exception:
        import traceback
        RUNTIME.mkdir(parents=True, exist_ok=True)
        (RUNTIME / 'startup-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, '新版工作台未能启动。诊断已保存在本地 Workdesk 运行目录。', 'Workdesk · 启动失败', 16)
        raise
