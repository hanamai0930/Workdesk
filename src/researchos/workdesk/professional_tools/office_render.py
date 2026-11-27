"""Render Office artifacts in a private LibreOffice profile for each job."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
from xml.etree import ElementTree
from zipfile import ZipFile

from ..instance import load_profile


def office_executable():
    candidates = []
    override = os.environ.get('WORKDESK_SOFFICE')
    if override:
        if not Path(override).is_absolute() or not Path(override).is_file():
            raise FileNotFoundError('配置的 LibreOffice 程序不存在。')
        return Path(override)
    profile = load_profile(Path.cwd())
    if profile and profile.get('channel') == 'development':
        candidates.append(Path(profile['development_root']) / 'toolchains/libreoffice-26.8.3/program/soffice.com')
    for env in ('ProgramFiles', 'ProgramFiles(x86)'):
        if os.environ.get(env):
            candidates.append(Path(os.environ[env]) / 'LibreOffice/program/soffice.com')
    if os.name != 'nt':
        executable = shutil.which('soffice')
        if executable:
            candidates.append(Path(executable))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError('未发现 LibreOffice，无法重算或渲染该版本。')


def check_render_input(source):
    # Active content and references that can fetch data require separate authority.
    with ZipFile(source) as package:
        entries = package.infolist()
        if len(entries) > 12000 or sum(i.file_size for i in entries) > 192 * 1024 ** 2:
            raise ValueError('Office 文件展开后超过渲染上限。')
        for item in entries:
            name = item.filename.lower()
            if any(part in name for part in ('vbaproject', '/activex/', '/externallinks/', '/embeddings/', '/querytables/', 'xl/connections.xml')):
                raise ValueError('文档包含活动内容或外部数据源，需要单独处理。')
            if name.endswith('.rels'):
                for rel in ElementTree.fromstring(package.read(item)):
                    if rel.get('TargetMode','').lower() == 'external' and not rel.get('Type','').endswith('/hyperlink'):
                        raise ValueError('文档引用外部资源，无法在本次本地渲染中读取。')
            if name.endswith('.xml') and name.startswith(('xl/worksheets/', 'xl/workbook', 'word/')):
                data = package.read(item)
                if re.search(rb'\b(DDEAUTO|DDE|WEBSERVICE|RTD|INCLUDEPICTURE|INCLUDETEXT|IMPORTDATA|IMPORTXML|IMPORTHTML)\b', data, re.I):
                    # Only inspect instructions and formulas. Ordinary prose is harmless.
                    for node in ElementTree.fromstring(data).iter():
                        tag = node.tag.rsplit('}',1)[-1]
                        if tag not in ('f','definedName','instrText','fldSimple','ddeLink'):
                            continue
                        instruction = (node.text or '') + ' ' + ' '.join(node.attrib.values())
                        if tag == 'ddeLink' or re.search(r'\b(DDEAUTO|DDE|WEBSERVICE|RTD|INCLUDEPICTURE|INCLUDETEXT|IMPORTDATA|IMPORTXML|IMPORTHTML)\b', instruction, re.I):
                            raise ValueError('文档含需要外部访问的字段或公式，本次渲染已停止。')


def convert(source: Path, outdir: Path, extension: str) -> Path:
    source = Path(source).resolve(strict=True)
    outdir = Path(outdir).resolve()
    suffix = source.suffix.lower()
    if not (extension == 'pdf' and suffix in {'.docx', '.pptx', '.xlsx'} or extension == 'xlsx' and suffix == '.xlsx'):
        raise ValueError('不支持这组 Office 转换格式。')
    program = office_executable()
    check_render_input(source)
    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / (source.stem + '.' + extension)
    if target.exists():
        raise FileExistsError('目标版本已存在。')
    with tempfile.TemporaryDirectory(prefix='workdesk-office-') as temporary:
        root = Path(temporary)
        user = root / 'profile/user'
        user.mkdir(parents=True)
        (user / 'registrymodifications.xcu').write_text(
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
            '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>'
            '</oor:items>', encoding='utf-8')
        result_dir = root / 'converted'
        result_dir.mkdir()
        result = subprocess.run(
            [str(program), '-env:UserInstallation=' + user.parent.as_uri(), '--headless', '--nologo',
             '--norestore', '--nodefault', '--nofirststartwizard', '--convert-to', extension,
             '--outdir', str(result_dir), str(source)],
            cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        rendered = result_dir / target.name
        if result.returncode or not rendered.is_file() or rendered.stat().st_size == 0:
            raise ValueError('LibreOffice 未生成有效的转换文件。')
        with rendered.open('rb') as src, target.open('xb') as dst:
            shutil.copyfileobj(src, dst)
    return target
