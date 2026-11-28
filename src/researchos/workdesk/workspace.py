"""Exact material snapshots and hash-guarded working copies; not an OS sandbox."""
from hashlib import sha256
import difflib
import os
from pathlib import Path, PurePosixPath
import stat
from contextlib import contextmanager
from contextvars import ContextVar
import shutil
import uuid

_snapshot_transaction = ContextVar('exact_task_snapshot_transaction', default=None)


@contextmanager
def task_snapshot_transaction(task_root):
    """Rollback only input directories actually created in this call context."""
    root = Path(task_root).resolve()
    state = {'root': root, 'created': {}}
    token = _snapshot_transaction.set(state)
    try:
        yield
    except BaseException:
        for target, identity in reversed(list(state['created'].items())):
            plain(target, root, missing=True)
            if target.resolve() != target or not target.is_relative_to(root) or target == root:
                raise ValueError('快照回滚目标越过准确任务目录。')
            if target.exists():
                current = target.stat()
                if (current.st_dev, current.st_ino) != identity:
                    raise ValueError('快照回滚目标已被替换。')
                shutil.rmtree(target)
        raise
    finally:
        _snapshot_transaction.reset(token)


MAX_FILE = 1024 * 1024
MAX_BINARY = 8 * MAX_FILE
MAX_ATTACHMENT_TOTAL = 32 * MAX_FILE
MAX_TOTAL = 8 * MAX_FILE
MAX_FILES = 200
PRIVATE = {'.git','.env','.codex','.qwen','auth.json','credentials.json','backend.json','backend.lock',
           'tasks.sqlite3','tasks.sqlite3-wal','tasks.sqlite3-shm'}
SKIP_DIRS = {'build','node_modules','.venv','venv','__pycache__','.pytest_cache'}


def io_path(path):
    """Use Windows extended paths for filesystem I/O, without changing saved names."""
    path=Path(path)
    if os.name=='nt':
        value=str(path.absolute())
        if not value.startswith('\\\\?\\'):
            value='\\\\?\\UNC\\'+value[2:] if value.startswith('\\\\') else '\\\\?\\'+value
        return Path(value)
    return path


def relative(name):
    if not isinstance(name,str) or not name or len(name)>240 or '\\' in name or ':' in name or '\0' in name:
        raise ValueError('文件名须为工作目录内的相对路径。')
    path=PurePosixPath(name)
    if path.is_absolute() or any(part in {'','..','.'} for part in name.split('/')):
        raise ValueError('不允许绝对路径或目录越界。')
    for part in path.parts:
        low=part.lower()
        if low in PRIVATE or low.startswith('.env.') or low.endswith(('.pem','.key')) or part.endswith((' ','.')):
            raise ValueError('该路径属于凭据、控制数据或不支持的文件。')
    return path.as_posix()


def plain(path, root, *, missing=False):
    path=Path(path);root=Path(root)
    try:path.absolute().relative_to(root.absolute())
    except ValueError:raise ValueError('路径越过工作副本。') from None
    current=path
    while True:
        physical=io_path(current)
        if physical.exists():
            value=physical.lstat()
            if stat.S_ISLNK(value.st_mode) or getattr(value,'st_file_attributes',0)&0x400:
                raise ValueError('不读取链接或重解析点。')
            if current==path and physical.is_file() and value.st_nlink!=1:
                raise ValueError('不读取有硬链接的工作文件。')
        elif not missing and current==path:raise ValueError('文件不存在。')
        if current==root:break
        parent=current.parent
        if parent==current:raise ValueError('工作副本根目录不匹配。')
        current=parent
    return path


def read_text(root, name):
    record=read_blob(root,name,MAX_FILE)
    raw=record.pop('raw')
    if b'\0' in raw:raise ValueError('此文件是二进制材料；请使用明确的附件路径，不作为UTF-8正文读取。')
    return {**record,'content':raw.decode('utf-8')}


def read_blob(root,name,maximum=MAX_BINARY):
    path=io_path(plain(Path(root)/relative(name),Path(root)))
    if not path.is_file() or path.stat().st_size>maximum:raise ValueError('文件不是常规文件或超过所选材料大小上限。')
    with path.open('rb') as stream:raw=stream.read(maximum+1)
    if len(raw)>maximum:raise ValueError('文件超过所选材料大小上限。')
    return {'file':name,'sha256':sha256(raw).hexdigest(),'bytes':len(raw),'raw':raw}


def material_view(root,name):
    record=read_blob(root,name);raw=record.pop('raw')
    try:
        if b'\0' in raw:raise UnicodeError()
        return {**record,'content':raw.decode('utf-8')}
    except UnicodeError:
        return {**record,'binary':True,'content':'二进制材料已保存。此处仅核对文件字节与指纹，不表示模型读取或理解。请查看附件处理路线及运行回执。'}


def select_files(root, names, forbidden=None, *, directories=True):
    root=Path(root).absolute()
    if not root.is_dir():raise ValueError('项目目录不存在。')
    if not isinstance(names,list) or len(names)>MAX_FILES:raise ValueError('选定材料列表过大。')
    files,excluded=set(),[]
    def add(name, explicit=True):
        try:name=relative(name)
        except ValueError:
            if explicit:raise
            excluded.append(name);return
        path=plain(root/name,root)
        if forbidden and (path.resolve()==Path(forbidden).resolve() or path.resolve().is_relative_to(Path(forbidden).resolve())):
            raise ValueError('控制服务数据不能作为任务材料。')
        if io_path(path).is_dir():
            if not directories:raise ValueError('请选择具体成果文件。')
            for child in sorted(io_path(path).iterdir()):
                if child.name in SKIP_DIRS or child.name.lower() in PRIVATE:
                    excluded.append(child.relative_to(io_path(root)).as_posix());continue
                add(child.relative_to(io_path(root)).as_posix(),False)
        else:files.add(name)
        if len(files)>MAX_FILES:raise ValueError('最多支持200份文本材料，请缩小范围。')
    for name in names:add(name)
    return sorted(files),excluded


def snapshot(root, names, destination, forbidden=None, binary_names=()):
    names,excluded=select_files(root,names,forbidden)
    destination=Path(destination)
    state = _snapshot_transaction.get(); target = None
    if state is not None:
        destination = destination.absolute()
        plain(destination, state['root'], missing=True)
        if destination.name != 'input' or destination.parent.parent != state['root']:
            raise ValueError('事务快照只允许准确新任务的input目录。')
        uuid.UUID(destination.parent.name)
        if not destination.exists(): target = destination if destination.parent.exists() else destination.parent
    try:
        destination.mkdir(parents=True,exist_ok=False)
    finally:
        if target is not None and target.exists():
            plain(target, state['root'])
            value = target.stat(); state['created'][target] = (value.st_dev, value.st_ino)
    manifest={};total=0
    for name in names:
        record=read_blob(root,name) if name in binary_names else read_text(root,name);total+=record['bytes']
        maximum=MAX_ATTACHMENT_TOTAL if binary_names else MAX_TOTAL
        if total>maximum:raise ValueError('材料合计超过当前本地快照大小上限。')
        path=io_path(destination/name);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(record['raw'] if 'raw' in record else record['content'].encode('utf-8'))
        manifest[name]={'sha256':record['sha256'],'bytes':record['bytes']}
    return manifest,excluded


def copy_manifest(source, destination, manifest):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=False)
    for name,item in manifest.items():
        record=read_blob(source,name)
        if {k:record[k] for k in ('sha256','bytes')}!=item:raise ValueError('批准的材料快照发生变化。')
        target=io_path(destination/name);target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(record['raw'])


def current_manifest(root, allowed):
    result={}
    for name in sorted(set(allowed)):
        path=io_path(Path(root)/relative(name))
        if path.exists():
            record=read_blob(root,name);result[name]={'sha256':record['sha256'],'bytes':record['bytes']}
    return result


def confirmed_reads(receipts,scope):
    """Validate full visible bodies, or contiguous pages reconstructed to source hash.

    A hash label, duplicate page, gap, wrong range or mixed version is insufficient.
    This confirms byte coverage only, never a model's comprehension or acceptance.
    """
    observed={};pages={};conflicts=set()
    for r in receipts:
        name=r.get('file');expected=scope.get(name);body=r.get('content')
        if not expected or not isinstance(body,str):continue
        if r.get('sha256')!=expected['sha256'] or r.get('bytes')!=expected['bytes']:continue
        raw=body.encode('utf-8')
        if 'offset' not in r:
            if len(raw)==expected['bytes'] and sha256(raw).hexdigest()==expected['sha256']:observed[name]=expected
            continue
        start,end,total=(r.get(k) for k in ['offset','next_offset','total_characters'])
        if any(type(x) is not int for x in [start,end,total]) or not 0<=start<=end<=total<=MAX_FILE or end-start!=len(body):continue
        if len(raw)!=r.get('chunk_bytes') or sha256(raw).hexdigest()!=r.get('chunk_sha256'):continue
        entry=pages.setdefault(name,{'total':total,'parts':{}})
        if entry['total']!=total or start in entry['parts'] and entry['parts'][start]!=body:conflicts.add(name);continue
        entry['parts'][start]=body
    for name,entry in pages.items():
        if name in conflicts:continue
        cursor=0;parts=[]
        while cursor<entry['total']:
            part=entry['parts'].get(cursor)
            if not part:break
            parts.append(part);cursor+=len(part)
        if cursor!=entry['total']:continue
        raw=''.join(parts).encode('utf-8');expected=scope[name]
        if len(raw)==expected['bytes'] and sha256(raw).hexdigest()==expected['sha256']:observed[name]=expected
    return observed


def replace(root, name, content, old_hash, allowed):
    name=relative(name)
    if name not in allowed:raise ValueError('该文件未获写入授权。')
    if not isinstance(content,str) or len(content.encode('utf-8'))>MAX_FILE:raise ValueError('替换正文超过上限。')
    path=io_path(plain(Path(root)/name,Path(root),missing=True))
    actual=read_text(root,name)['sha256'] if path.exists() else ''
    if actual!=old_hash:raise ValueError('旧版本哈希不一致，未写入。')
    path.parent.mkdir(parents=True,exist_ok=True)
    # Private temporary sibling; do not follow an existing user-created link.
    import uuid
    temporary=path.with_name('.wd-'+uuid.uuid4().hex+'.tmp')
    with temporary.open('xb') as stream:stream.write(content.encode('utf-8'))
    temporary.replace(path)
    return {'file':name,'old_sha256':actual,'sha256':sha256(content.encode()).hexdigest()}


class MutationConflict(ValueError):
    def __init__(self,message,preserved=None):
        super().__init__(message);self.preserved=preserved


def install_absent(source,target):
    """Atomic no-overwrite install; never replace a newly saved Human file."""
    import os
    if os.name=='nt':os.rename(source,target)
    else:os.link(source,target);os.unlink(source)


def replace_blob(root,name,raw,old_hash,allowed):
    """Preserve the atomically displaced version, validate it, then install.

    The initial read is an early refusal only. The version moved aside by the
    atomic rename is the comparison witness. Retain successful displacements as
    recovery evidence; concurrent edits are restored without clobbering a newer
    destination, or retained under the disclosed recovery pathname.
    """
    name=relative(name)
    if name not in allowed:raise ValueError('该文件未获写入授权。')
    if raw is not None and (not isinstance(raw,bytes) or len(raw)>MAX_BINARY):raise ValueError('候选字节超过文件上限。')
    path=io_path(plain(Path(root)/name,Path(root),missing=True))
    actual=read_blob(root,name)['sha256'] if path.exists() else ''
    if actual!=old_hash:raise ValueError('旧版本哈希不一致，未写入/删除。')
    import uuid
    temporary=None;displaced=None;witness=None
    if raw is not None:
        path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_name('.wd-'+uuid.uuid4().hex+'.tmp')
        with temporary.open('xb') as stream:stream.write(raw)
    try:
        if actual:
            displaced=io_path(Path(root)/('.wd-preserved-'+uuid.uuid4().hex+'.bin'))
            path.rename(displaced)
            observed=read_blob(root,displaced.relative_to(io_path(Path(root))).as_posix())
            witness={'path':str(displaced),'sha256':observed['sha256'],'bytes':observed['bytes'],'expected_sha256':old_hash}
            if observed['sha256']!=old_hash:raise MutationConflict('并发保存被原子移出时检出，候选未写入。',witness)
        if temporary:
            install_absent(temporary,path);temporary=None
        if displaced:
            after=read_blob(root,displaced.relative_to(io_path(Path(root))).as_posix())
            if after['sha256']!=old_hash:raise MutationConflict('保留的原子移出版本发生并发修改；保留该版本，停止声明整合成功。',{**witness,'sha256':after['sha256'],'bytes':after['bytes']})
        return {'displaced_version':witness,'operation':'delete' if raw is None else 'write'}
    except Exception as exc:
        if displaced and displaced.exists():
            try:install_absent(displaced,path)
            except OSError:
                # Another writer now owns the destination. Preserve both copies.
                raise MutationConflict('并发目标已存在；保留原子移出版本，未覆盖新目标。',witness or {'path':str(displaced)}) from exc
        raise
    finally:
        if temporary and temporary.exists():temporary.unlink()


def differences(original, worktree, manifest, allowed):
    result=[]
    for name in sorted(set(manifest)|set(allowed)):
        before_raw=read_blob(original,name)['raw'] if name in manifest else b''
        after_raw=read_blob(worktree,name)['raw'] if io_path(Path(worktree)/name).exists() else b''
        before_exists=name in manifest;after_exists=io_path(Path(worktree)/name).exists()
        if before_raw==after_raw and before_exists==after_exists:continue
        operation='delete' if not after_exists else 'create' if not before_exists else 'write'
        try:
            if b'\0' in before_raw+after_raw:raise UnicodeError()
            before=before_raw.decode('utf-8');after=after_raw.decode('utf-8')
        except UnicodeError:
            result.append({'file':name,'base_sha256':manifest.get(name,{}).get('sha256'),
                           'sha256':sha256(after_raw).hexdigest(),'diff':'二进制文件发生改变；不显示为文本差异。','binary':True,'operation':operation})
            continue
        if before!=after or before_exists!=after_exists:
            result.append({'file':name,'base_sha256':manifest.get(name,{}).get('sha256'),
                           'sha256':sha256(after.encode()).hexdigest(),'operation':operation,
                           'diff':''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),
                                                             fromfile='a/'+name,tofile='b/'+name))[:MAX_FILE]})
    return result
