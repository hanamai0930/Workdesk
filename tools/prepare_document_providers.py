"""Prepare pinned, local document providers for the registered Windows development instance."""
import argparse
from base64 import b64decode
from hashlib import file_digest
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
from urllib.request import urlopen

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE/'src'))
from researchos.workdesk.instance import load_profile
from researchos.workdesk.professional_tools.tex_render import ASSETS, PACKAGE

OFFICE = 'LibreOffice_26.8.3_Win_x86-64.msi'
OFFICE_URL = 'https://download.documentfoundation.org/libreoffice/stable/26.8.3/win/x86_64/'+OFFICE
OFFICE_SHA = '0e6918c273eabbdbbd800ee0dacbbf130a14c13c2e4cec861b06dd59e015bf94'
TEX_URL = 'https://registry.npmjs.org/pdftex.js/-/pdftex.js-0.0.10.tgz'
TEX_SHA = b64decode('5sr4+TiJICq/DOONPMrUWk6M/d6SiVIZZXfy6d2LOYZbwNFKs0nXXrlPdOSP6/wM+iOGFXS4YnTqa1PWGwP3dg==').hex()


def checked_download(cache, url, algorithm, digest):
    target = cache/url.rsplit('/',1)[1]
    if not target.exists():
        with TemporaryDirectory(dir=cache) as temp:
            partial=Path(temp)/target.name
            with urlopen(url,timeout=90) as response, partial.open('xb') as output:
                shutil.copyfileobj(response,output,1024*1024)
            with partial.open('rb') as f:
                if file_digest(f,algorithm).hexdigest()!=digest:
                    raise ValueError('下载文件与发布摘要不符。')
            partial.replace(target)
    else:
        with target.open('rb') as f:
            if file_digest(f,algorithm).hexdigest()!=digest:
                raise ValueError('缓存的安装包与发布摘要不符。')
    return target


def prepare(root, provider):
    cache=root/'cache/documents'
    tools=root/'toolchains'
    cache.mkdir(parents=True,exist_ok=True)
    tools.mkdir(parents=True,exist_ok=True)
    dest=tools/('libreoffice-26.8.3' if provider=='office' else PACKAGE)
    if not dest.exists():
        with TemporaryDirectory(dir=tools) as temp:
            staged=Path(temp)/'provider'
            if provider=='office':
                package=checked_download(cache,OFFICE_URL,'sha256',OFFICE_SHA)
                subprocess.run(['msiexec.exe','/a',str(package),'/qn','/norestart','TARGETDIR='+str(staged)],
                               check=True,timeout=480,creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                package=checked_download(cache,TEX_URL,'sha512',TEX_SHA)
                staged.mkdir()
                with tarfile.open(package,'r:gz') as archive:
                    for name in [*ASSETS,'LICENSE','package.json']:
                        member=archive.getmember('package/'+name)
                        if not member.isfile():
                            raise ValueError('排版工具包缺少文件。')
                        with archive.extractfile(member) as f,(staged/name).open('xb') as output:
                            shutil.copyfileobj(f,output)
            verify(staged,provider)
            staged.rename(dest)
    verify(dest,provider)
    print(dest)


def verify(dest,provider):
    if provider=='office':
        if not (dest/'program/soffice.com').is_file():
            raise ValueError('开发目录内未找到 LibreOffice。')
    else:
        for name,digest in ASSETS.items():
            with (dest/name).open('rb') as f:
                if file_digest(f,'sha256').hexdigest()!=digest:
                    raise ValueError('排版组件摘要不符：'+name)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('provider',choices=('office','tex','both'))
    requested=parser.parse_args().provider
    profile=load_profile(BASE)
    if os.name!='nt' or not profile or profile['channel']!='development' or Path(profile['program_root'])!=BASE:
        raise SystemExit('此工具仅准备已登记的 Windows 开发实例。')
    for name in (['office','tex'] if requested=='both' else [requested]):
        prepare(Path(profile['development_root']),name)
