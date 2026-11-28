"""Execute a single, explicitly authorized local project or UI operation."""
from __future__ import annotations
from hashlib import sha256
import difflib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time


def relative(root, value, allow_root=False):
    if not isinstance(value,str) or not value.strip():
        raise ValueError('缺少项目内相对位置。')
    name=PureWindowsPath(value)
    if name.drive or name.root or ':' in value or '..' in name.parts or (not name.parts and not allow_root):
        raise ValueError('路径不属于所选项目。')
    path=root
    for part in name.parts:
        if part.casefold() in {'.git','.maintenance','auth.json','credentials.json'} or part.casefold().startswith('.env'):
            raise ValueError('此路径属于受保护的配置。')
        path=path/part
        if path.is_symlink() or path.is_junction() or path.resolve()!=path:
            raise ValueError('路径含有链接，无法核对归属。')
    return path


def read_version(path, version):
    if version is None or not path.is_file() or path.stat().st_size!=version['bytes']:
        raise ValueError('原文件版本已失效。')
    with path.open('rb') as stream:
        data=stream.read(8*1024*1024+1)
    if len(data)>8*1024*1024 or sha256(data).hexdigest()!=version['sha256']:
        raise ValueError('原文件版本已改变。')
    return data


def clean_environment():
    keys=('SystemRoot','WINDIR','SystemDrive','TEMP','TMP','PATH','PATHEXT','USERPROFILE','HOME','LOCALAPPDATA','APPDATA','PROGRAMFILES','PROGRAMFILES(X86)','COMSPEC','LANG')
    env={key:os.environ[key] for key in keys if key in os.environ}
    env.update(PYTHONIOENCODING='utf-8',PYTHONUTF8='1',GIT_TERMINAL_PROMPT='0',GIT_LITERAL_PATHSPECS='1')
    return env


def publish_new_file(path, data):
    # Publish the fully written file without overwriting an intervening creation.
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        temporary.write_bytes(data)
        if os.name == "nt":
            temporary.rename(path)
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def file_action(p,root,version):
    action=p['action']
    path=relative(root,p.get('path') or '.',action=='file.list')
    if action=='file.list':
        entries=[]
        for child in path.iterdir():
            if child.name.casefold() in {'.git','.maintenance','credentials.json','auth.json'} or child.name.casefold().startswith('.env'):
                continue
            entries.append(child.name+('/' if child.is_dir() else ''))
            if len(entries)>5000:raise ValueError('目录超过 5000 项，请缩小范围。')
        return '\n'.join(sorted(entries)),{}
    old=read_version(path,version) if version else None
    if action=='file.read':
        return old.decode('utf-8-sig'),{'sha256':version['sha256']}
    if action in {'file.copy','file.move'}:
        if old is None:raise ValueError('缺少源文件版本。')
        dest=relative(root,p['destination'])
        if dest.exists() or not dest.parent.is_dir():raise ValueError('目标已存在或父目录不可用。')
        if action=='file.copy':
            publish_new_file(dest, old)
        else:
            if os.name=='nt':os.rename(path,dest)
            else:
                os.link(path,dest)
                path.unlink()
        if sha256(dest.read_bytes()).hexdigest()!=version['sha256']:
            raise ValueError('操作后的版本待核对。')
        return str(dest.relative_to(root)),{'sha256':version['sha256']}
    content=p.get('content')
    if not isinstance(content,str):raise ValueError('编辑操作需要 UTF-8 正文。')
    was=(old or b'').decode('utf-8-sig')
    data=(b'\xef\xbb\xbf' if old and old.startswith(b'\xef\xbb\xbf') else b'')+content.encode()
    if old is None:
        publish_new_file(path, data)
    else:
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as stream:
            temp=Path(stream.name)
        try:
            temp.write_bytes(data)
            shutil.copymode(path,temp)
            read_version(path,version)
            os.replace(temp,path)
        finally:temp.unlink(missing_ok=True)
    actual=sha256(path.read_bytes()).hexdigest()
    if actual!=sha256(data).hexdigest():raise ValueError('写入后文件已变化，请核对。')
    diff=difflib.unified_diff(was.splitlines(),content.splitlines(),fromfile='原版本',tofile='当前版本',lineterm='')
    return '\n'.join(diff),{'sha256':actual,'prior_sha256':(version or {}).get('sha256')}


def verify_repository(metadata, versions):
    if not versions or "HEAD" not in versions:
        raise ValueError("缺少已授权的仓库版本。")
    for name, expected in versions.items():
        path = metadata / name
        if not path.is_relative_to(metadata) or path.resolve() != path:
            raise ValueError("Git 引用已变化。")
        if expected == "absent" and not path.exists():
            continue
        if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Git 引用或索引已变化。")
        with path.open("rb") as stream:
            actual = sha256()
            while block := stream.read(65536):
                actual.update(block)
        if actual.hexdigest() != expected:
            raise ValueError("Git 当前版本或暂存区已变化，请重新确认。")


def git_action(p,root,version,repository_versions):
    git=shutil.which('git')
    if not git:raise ValueError('尚未准备 Git。')
    # Mutations require a repository-owned .git directory. A worktree pointer
    # can refer outside the registered project; use the owning checkout for it.
    metadata=root/'.git'
    if not metadata.is_dir() or metadata.resolve()!=metadata or metadata.is_junction():
        raise ValueError('请选择包含原生 .git 目录的仓库根。')
    base=[git,'-c','core.hooksPath='+os.devnull,'-c','core.fsmonitor=false','-c','commit.gpgsign=false','-C',str(root),'--git-dir='+str(metadata),'--work-tree='+str(root)]
    a=p['action']
    commands={'git.status':['status','--short','--branch'],'git.history':['log','-30','--oneline'],
        'git.branches':['branch','--list'],'git.worktrees':['worktree','list','--porcelain']}
    if a in commands:args=commands[a]
    elif a in ('git.branch','git.worktree'):
        branch=p.get('branch','')
        if not isinstance(branch,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,119}',branch) or '..' in branch:
            raise ValueError('新分支名无效。')
        if a=='git.branch':args=['branch',branch]
        else:
            dest=relative(root,p['destination'])
            if dest.exists() or not dest.parent.is_dir():raise ValueError('工作副本目录需是未占用的新位置。')
            args=['worktree','add','-b',branch,str(dest),'HEAD']
    else:
        path=relative(root,p['path'])
        read_version(path,version)
        file=str(path.relative_to(root))
        if a=='git.diff':args=['diff','--no-ext-diff','--no-textconv','HEAD','--',file]
        elif a=='git.stage':args=['add','--',file]
        elif a=='git.restore':args=['restore','--source=HEAD','--worktree','--',file]
        else:
            message=p.get('message')
            if not isinstance(message,str) or not message.strip() or len(message)>2000:raise ValueError('请填写本次提交的说明。')
            args=['commit','--only','-m',message,'--',file]
    if a in {"git.stage", "git.restore", "git.commit", "git.branch", "git.worktree"}:
        verify_repository(metadata, repository_versions)
    with subprocess.Popen(base+args,env=clean_environment(),cwd=root,stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT) as proc:
        out=proc.stdout.read(2*1024*1024+1)
        if len(out)>2*1024*1024:
            proc.kill();raise ValueError('Git 输出超过 2 MiB，请缩小范围。')
        if proc.wait():raise ValueError('Git 未完成：'+out.decode('utf8','replace')[-1600:])
    return out.decode('utf8','replace'),{'argv':args}


# The child opens the registered file once and executes precisely those bytes
# after checking the digest. argv and local imports use the original filename.
SCRIPT_LAUNCHER = """import hashlib, pathlib, sys
filename, digest = sys.argv[1:3]
with open(filename, 'rb') as stream:
    source = stream.read(8 * 1024 * 1024 + 1)
if hashlib.sha256(source).hexdigest() != digest:
    raise SystemExit('Authorized script version has changed')
sys.argv = [filename, *sys.argv[3:]]
sys.path.insert(0, str(pathlib.Path(filename).parent))
namespace = {'__name__': '__main__', '__file__': filename, '__package__': None, '__spec__': None}
exec(compile(source, filename, 'exec'), namespace)
"""


def terminal(p,root,out,version):
    script=relative(root,p['path'])
    if script.suffix.lower()!='.py':raise ValueError('本地终端入口接受选定的 Python 脚本。')
    read_version(script,version)
    args=p.get('args',[])
    if not isinstance(args,list) or len(args)>64 or not all(isinstance(x,str) and len(x)<=1024 for x in args):raise ValueError('运行参数无效。')
    initial=p.get('stdin','')
    if not isinstance(initial,str) or len(initial.encode())>4096:raise ValueError('初始输入超过限制。')
    inbox=out/'.input';inbox.mkdir()
    transcript=out/'console.txt'
    python = sys.executable
    consumed=[]
    with transcript.open('wb',buffering=0) as console, subprocess.Popen(
            [python, '-I', '-u', '-X', 'utf8', '-c', SCRIPT_LAUNCHER, str(script), version['sha256'], *args],cwd=root,env=clean_environment(),
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT) as proc:
        def pump():
            remaining=1024*1024
            while chunk:=proc.stdout.read1(32768):
                if remaining:
                    keep=chunk[:remaining];console.write(keep);remaining-=len(keep)
                    if not remaining:console.write('\n[输出记录已达到 1 MiB 上限]\n'.encode())
        thread=threading.Thread(target=pump,daemon=True);thread.start()
        if initial:proc.stdin.write(initial.encode());proc.stdin.flush()
        while proc.poll() is None:
            for item in sorted(inbox.glob('*.txt')):
                try:
                    proc.stdin.write(item.read_bytes());proc.stdin.flush()
                except (BrokenPipeError,OSError):break
                consumed.append(item.stem.split('-',1)[1]);item.unlink()
            time.sleep(.05)
        thread.join(timeout=2)
        code=proc.returncode
    if code:raise ValueError(f'选定脚本退出码 {code}，输出在终端日志。')
    return '进程正常退出。',{'exit_code':code,'pid':proc.pid,'python':python,'args':args,'input_delivered':consumed}


def environment_python(root):
    return relative(root,'.workdesk-venv/'+('Scripts/python.exe' if os.name=='nt' else 'bin/python'))


def artifact(path,mime):
    data=path.read_bytes()
    return dict(path=str(path),bytes=len(data),mime=mime,sha256=sha256(data).hexdigest())


def execute(req):
    root=Path(req['project_root']).resolve(strict=True);out=Path(req['output_dir'])
    p=req['parameters'];action=p['action']
    out.mkdir(parents=True,exist_ok=False)
    if action.startswith(('desktop.','browser.')):
        from .computer_surface import OutcomeUnknown, execute as surface
        try:
            result = surface(p, out)
        except OutcomeUnknown as error:
            result = {'state':'OUTCOME_UNKNOWN','error':str(error),'artifacts':[]}
        return {**result, 'tool_version':'computer/1', 'real_model_requests':0}
    versions=req.get('input_versions') or []
    version=versions[0] if versions else None
    if action.startswith('file.'):text,details=file_action(p,root,version)
    elif action.startswith('git.'):text,details=git_action(p,root,version,req.get("repository_versions"))
    elif action=='terminal.run':text,details=terminal(p,root,out,version)
    elif action in ('env.prepare','env.inspect'):
        python=environment_python(root)
        if action=='env.prepare':
            dest=relative(root,'.workdesk-venv')
            dest.mkdir()
            try:
                subprocess.run([sys.executable,'-I','-m','venv','--without-pip','--copies',str(dest)],cwd=root,
                               env=clean_environment(),stdin=subprocess.DEVNULL,check=True,timeout=40)
                if not python.is_file():
                    raise ValueError('项目环境尚未准备好。')
            except (OSError, subprocess.SubprocessError, ValueError):
                if dest.resolve() == dest and not dest.is_junction() and not dest.is_symlink():
                    shutil.rmtree(dest)
                raise
        details={'python':str(sys.executable),'python_version':sys.version.split()[0],
                 'project_python':str(python) if python.is_file() else None,'git':shutil.which('git'),'pip_install_performed':False}
        text=json.dumps(details,ensure_ascii=False,indent=2)
    else:raise ValueError('不支持此电脑操作。')
    report=out/'receipt.json';result=out/'output.txt'
    report.write_text(json.dumps({'action':action,'path':p.get('path'),'project_root':str(root),**details},ensure_ascii=False,indent=2),encoding='utf8')
    result.write_text(text,encoding='utf8',newline='')
    artifacts=[artifact(report,'application/json'),artifact(result,'text/plain')]
    if (out/'console.txt').is_file():artifacts.append(artifact(out/'console.txt','text/plain'))
    return dict(state='COMPLETED',summary='已完成 '+action,artifacts=artifacts,checks=[],real_model_requests=0)


if __name__=='__main__':
    try:print(json.dumps(execute(json.load(sys.stdin)),ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'state':'FAILED','error':str(error),'artifacts':[]},ensure_ascii=False))
        sys.exit(1)
