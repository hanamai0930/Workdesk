"""Explicit program, project and persistent data locations."""
from dataclasses import dataclass
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import sys


def default_user_root():
    # Stable across packaged Codex and standalone Workdesk launches. Packaged
    # Windows apps can virtualize LOCALAPPDATA into their own disposable cache.
    from .instance import load_profile
    profile=load_profile()
    if profile:return Path(profile['user_root'])
    return Path(os.environ['WORKDESK_HOME']) if os.environ.get('WORKDESK_HOME') else Path.home()/'.workdesk'


def legacy_user_root():
    if os.environ.get('WORKDESK_HOME'):
        return None
    local=os.environ.get('LOCALAPPDATA')
    return (Path(local)/'Workdesk').resolve() if local else None


def local_credential_path(value):
    if not isinstance(value,str) or not Path(value).is_absolute():raise ValueError('API凭据文件引用必须是本机绝对路径。')
    path=Path(value)
    if str(path).startswith('\\\\'):raise ValueError('API凭据必须来自本机文件，不能使用网络共享路径。')
    if os.name=='nt':
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) not in {2,3,6}:raise ValueError('API凭据必须来自本机文件系统。')
    from .workspace import plain
    plain(path,path.parent)
    if not path.is_file() or path.stat().st_size>65536:raise ValueError('API凭据文件缺失或过大；未回退旧Key。')
    return path


@dataclass(frozen=True)
class Locations:
    base: Path
    data: Path
    user: Path | None = None

    @classmethod
    def resolve(cls, base=None, data=None, user=None):
        base = Path(base or Path(__file__).resolve().parents[3]).resolve()
        from .instance import load_profile,validate_locations
        profile=load_profile(base)
        if profile:
            selected_user=Path(user).resolve() if user is not None else Path(profile['user_root'])
            selected_data=Path(data).resolve() if data is not None else Path(profile['data_root'])
            validate_locations(base,selected_data,selected_user,profile)
            return cls(base,selected_data,selected_user)
        if user is None and data is None:
            user=default_user_root()
        if user is not None:
            user=Path(user)
            if not user.is_absolute():raise ValueError('Workdesk本地资料根目录必须是绝对路径。')
            user=user.resolve()
        configured=os.environ.get('WORKDESK_DATA_DIR')
        pointer=base/'build/workdesk-data-location.json'
        old_user=legacy_user_root()
        if user==default_user_root().resolve() and old_user and (old_user/'config/locations.json').exists():pointer=old_user/'config/locations.json'
        if user and (user/'config/locations.json').exists():pointer=user/'config/locations.json'
        if data is None and not configured and pointer.exists():
            value=json.loads(pointer.read_text(encoding='utf-8'))
            if not isinstance(value,dict) or value.get('schema')!=1 or not isinstance(value.get('active_data'),str) or not Path(value['active_data']).is_absolute():raise ValueError('数据位置配置无效；不自动重建旧库。')
            configured=value['active_data']
            if not (Path(configured)/'tasks.sqlite3').is_file():raise ValueError('已登记的本地资料库缺失；保留配置，不创建空库替代。')
        default_data=user/'libraries/main' if user else base/'build/workdesk-backend'
        # Keep the prior store discoverable until the explicit prepare/activate
        # operation has safely adopted it; clean installs start outside code.
        if not pointer.exists() and (base/'build/workdesk-backend/tasks.sqlite3').is_file():default_data=base/'build/workdesk-backend'
        data = Path(data or configured or default_data).resolve()
        return cls(base, data, user)

    @property
    def data_pointer(self):return self.user/'config/locations.json' if self.user else self.base/'build/workdesk-data-location.json'

    @property
    def restore_root(self):return self.user/'libraries' if self.user else self.base/'build/workdesk-restores'

    @property
    def credential_pointer(self):return self.user/'config/credential-references.json' if self.user else None

    def credential_references(self):
        pointer=self.credential_pointer
        if pointer is None:return None
        if not pointer.exists():
            if self.data_pointer.exists():raise ValueError('独立资料库的认证引用文件缺失；不回退工程内账号。')
            old_user=legacy_user_root()
            if self.user==default_user_root().resolve() and old_user and self.data.is_relative_to(old_user):
                pointer=old_user/'config/credential-references.json'
                if not pointer.exists():raise ValueError('待接续资料库的认证引用缺失；不回退旧账号。')
            else:return None
        from .workspace import plain
        plain(pointer,pointer.parent)
        if not pointer.is_file() or pointer.stat().st_size>65536:raise ValueError('本地认证引用配置无效；不回退其他账号。')
        value=json.loads(pointer.read_text(encoding='utf-8'))
        if not isinstance(value,dict) or value.get('schema')!=1 or not isinstance(value.get('revision'),str):raise ValueError('本地认证引用配置无效；不回退其他账号。')
        return value

    @property
    def api_env(self):
        value=self.credential_references()
        if value is None:return (self.user/'config/provider-keys.env') if self.user else self.base/'.env'
        path=value.get('api_env')
        if path is None:return None
        fingerprint=value.get('api_env_sha256')
        if not isinstance(fingerprint,str) or len(fingerprint)!=64 or any(c not in '0123456789abcdef' for c in fingerprint):raise ValueError('API凭据版本引用无效；未回退旧Key。')
        return local_credential_path(path)

    @property
    def credential_revision(self):
        value=self.credential_references()
        return value['revision'] if value else None

    @property
    def runtime(self): return self.base/'runtime'
    @property
    def codex_home(self):
        refs=self.credential_references()
        if refs is not None:
            if refs.get('codex_home') is None:raise ValueError('Codex认证引用已断开；本地资料仍可查看，不自动回退账号。')
            return self.validate_codex_home(refs['codex_home'])
        pointer=(self.user/'config/codex-profile.json') if self.user else (self.base/'config/codex-profile.json')
        if not pointer.exists():return (self.user or self.base)/'providers/codex/profiles/unconfigured'
        value=json.loads(pointer.read_text(encoding='utf-8'))
        if value.get('schema')!=1:raise ValueError('Codex专用认证引用无效；不静默退回旧账号。')
        return self.validate_codex_home(value.get('active_home',''))

    def validate_codex_home(self,value):
        if not isinstance(value,str):raise ValueError('Codex认证目录引用无效。')
        home=Path(value)
        roots=[self.user/'providers/codex/profiles'] if self.user else [self.base/'providers/codex/profiles']
        root=next((r for r in roots if home.is_absolute() and home.parent.resolve()==r.resolve()),None)
        if root is None:
            raise ValueError('Codex专用认证引用无效；不静默退回旧账号。')
        import uuid
        try:
            if uuid.UUID(home.name).hex!=home.name:raise ValueError()
        except ValueError:raise ValueError('Codex专用认证目录身份无效。') from None
        from .workspace import plain
        plain(home,root)
        if not plain(home/'auth.json',root).is_file():raise ValueError('指定新账号尚无官方认证文件；不自动回退旧账号。')
        return home
    @property
    def python(self):
        from .instance import load_profile
        profile=load_profile(self.base)
        if profile:
            executable=Path(profile['python_executable'])
            if not executable.is_file():raise ValueError('登记的开发解释器缺失；不回落系统解释器。')
            return executable
        return Path(sys.executable)
    @property
    def node(self): return self.runtime/'node-v22.23.3-win-x64/node.exe'
    @property
    def codex(self):
        binaries = list((Path(os.environ.get('LOCALAPPDATA',''))/'OpenAI/Codex/bin').glob('*/codex.exe'))
        return max(binaries,key=lambda p:p.stat().st_mtime_ns) if binaries else None


def legacy(locations, folder, name):
    """Load a migrated helper from the product's compatibility package."""
    helper={'provider-harness':'owned_job' if name=='owned_job' else 'credentials',
            'codex-harness':'rpc'}.get(folder)
    if helper != name:raise ValueError('Workdesk helper is not part of the migrated runtime.')
    path=Path(__file__).parent/'_compat'/(helper+'.py')
    spec = importlib.util.spec_from_file_location('workdesk_legacy_'+name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def clean_env():
    permitted = {'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','LOCALAPPDATA',
                 'APPDATA','PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMDATA','NUMBER_OF_PROCESSORS',
                 'PROCESSOR_ARCHITECTURE','OS','USERPROFILE','HOMEDRIVE','HOMEPATH','HTTPS_PROXY','HTTP_PROXY','NO_PROXY'}
    env = {k:v for k,v in os.environ.items() if k.upper() in permitted}
    env.update(PYTHONIOENCODING='utf-8',PYTHONUTF8='1',PYTHONNOUSERSITE='1')
    return env


def atomic_json(path, value):
    import uuid
    from .workspace import io_path
    path = io_path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('xb') as stream:stream.write(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8'))
        temp.replace(path)
    finally:
        if temp.exists():temp.unlink()


def atomic_bytes(path, raw):
    import uuid
    from .workspace import io_path
    path=io_path(path);temp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('xb') as stream:stream.write(raw)
        temp.replace(path)
    finally:
        if temp.exists():temp.unlink()


@contextmanager
def data_owner_lock(data):
    from .workspace import plain
    path=plain(Path(data)/'backend.lock',Path(data),missing=True)
    stream=path.open('a+b')
    try:
        if stream.seek(0,2)==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise ValueError('恢复目标已有本地执行所有者；不修改其数据库。') from None
        yield
    finally:stream.close()


def _implementation_content(locations):
    from hashlib import sha256
    sources=[p for p in Path(__file__).parent.rglob('*') if p.suffix in {'.py','.mjs'} and '__pycache__' not in p.parts]
    return {p.relative_to(locations.base).as_posix():p.read_bytes() for p in sources}


def implementation_revision(locations):
    """Read current product code identity without creating a frozen snapshot."""
    from .core_runtime import expected_implementation
    selected=expected_implementation(locations)
    if selected is not None:return selected
    from hashlib import sha256
    manifest={name:sha256(raw).hexdigest() for name,raw in sorted(_implementation_content(locations).items())}
    return sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()


def freeze_implementation(locations):
    """Freeze only explicitly named code and verify existing snapshots before use."""
    from .core_runtime import bound_revision, snapshot_matches
    selected=bound_revision(locations)
    if selected is not None:
        if not snapshot_matches(locations,selected):raise ValueError('Go组合执行快照不匹配，拒绝派发。')
        return selected
    from hashlib import sha256
    content=_implementation_content(locations)
    manifest={name:sha256(raw).hexdigest() for name,raw in sorted(content.items())}
    revision=sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    from .workspace import io_path
    root=io_path(locations.data/'implementations'/revision)
    if (root/'manifest.json').is_file():
        saved=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        if saved!=manifest or any(not (root/name).is_file() or sha256((root/name).read_bytes()).hexdigest()!=expected for name,expected in manifest.items()):
            raise ValueError('冻结执行代码/清单已被改变，拒绝加载，不覆盖旧证据。')
    else:
        for name,raw in content.items():
            destination=root/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(raw)
        atomic_json(root/'manifest.json',manifest)
    return revision


def implementation_matches(locations, revision):
    from .core_runtime import bound_revision, snapshot_matches
    selected=bound_revision(locations)
    if selected is not None:return selected==revision and snapshot_matches(locations,revision)
    from hashlib import sha256
    from .workspace import io_path
    root=io_path(locations.data/'implementations'/revision)
    saved=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    current={name:sha256(raw).hexdigest() for name,raw in sorted(_implementation_content(locations).items())}
    if saved!=current or sha256(json.dumps(current,sort_keys=True).encode()).hexdigest()!=revision:return False
    return all((root/name).is_file() and sha256((root/name).read_bytes()).hexdigest()==expected for name,expected in saved.items())


def process_identity(pid):
    """Read-only PID+creation identity. A reused PID is never terminated."""
    if os.name!='nt':
        try:
            os.kill(pid,0)
            value=Path('/proc')/str(pid)/'stat'
            return {'pid':pid,'alive':True,'creation':value.read_text().split()[21] if value.exists() else None}
        except ProcessLookupError:return {'pid':pid,'alive':False,'creation':None}
        except OSError:return {'pid':pid,'alive':None,'creation':None}
    import ctypes as c
    from ctypes import wintypes as w
    kernel=c.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.GetProcessTimes.argtypes=[w.HANDLE,c.POINTER(w.FILETIME),c.POINTER(w.FILETIME),c.POINTER(w.FILETIME),c.POINTER(w.FILETIME)]
    kernel.GetExitCodeProcess.argtypes=[w.HANDLE,c.POINTER(w.DWORD)];kernel.CloseHandle.argtypes=[w.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,int(pid))
    if not handle:return {'pid':pid,'alive':False if c.get_last_error()==87 else None,'creation':None}
    try:
        created,ended,kernel_time,user_time=w.FILETIME(),w.FILETIME(),w.FILETIME(),w.FILETIME();code=w.DWORD()
        if not kernel.GetProcessTimes(handle,c.byref(created),c.byref(ended),c.byref(kernel_time),c.byref(user_time)):
            return {'pid':pid,'alive':None,'creation':None}
        if not kernel.GetExitCodeProcess(handle,c.byref(code)):return {'pid':pid,'alive':None,'creation':None}
        return {'pid':pid,'alive':code.value==259,'creation':str((created.dwHighDateTime<<32)|created.dwLowDateTime)}
    finally:kernel.CloseHandle(handle)
