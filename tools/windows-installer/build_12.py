"""Build the Windows 1.2 installer from the verified standalone release tree and selector."""
from __future__ import annotations

import argparse
from hashlib import file_digest
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[2]
VERSION = "1.2.1"
NAME = f"Workdesk-Setup-{VERSION}-x64.exe"
WEBVIEW = "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
WEBVIEW_HASH = "47bc51387aff95c1275ce38cfbbb5fbab99b43b33214049877bbcdbcc7a970d5"
COMPILER_HASH = "e0f9f1dc32032650c6629f6dee41eb6d20ba140e4e3c07c25d8a24f9ba781644"
NOTICE = re.compile(r"^(?:licen[sc]e|copying|notice|copyright|authors|patents)(?:[-._ ]|$)", re.I)


def digest(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def notices(old, modcache, go, destination):
    if digest(old / "ui/pnpm-lock.yaml") != digest(REPO / "workdesk-ui/pnpm-lock.yaml"):
        raise ValueError("Refresh frontend license inventory for the new lock.")
    previous = old / "stage/第三方许可与源码"
    for name in ("前端许可", "Poppler"):
        shutil.copytree(previous / name, destination / name)
    basics = destination / "基础组件许可"
    basics.mkdir()
    for name in ("Python-3.12.7-LICENSE.txt", "Inno-Setup-7.1.1-LICENSE.txt",
                 "WebView2-SDK-1.0.4258.31-LICENSE.txt", "DeepSeek-Tokenizer-LICENSE.txt",
                 "tokenizers-0.22.2-LICENSE.txt", "shadcn-ui-LICENSE.md"):
        shutil.copy2(previous / "基础组件许可" / name, basics / name)
    shutil.copy2(REPO / "LICENSE", destination / "Workdesk-LICENSE.txt")
    shutil.copy2(REPO / "THIRD_PARTY_NOTICES.md", destination)
    shutil.copy2(go / "LICENSE", basics / "Go-LICENSE.txt")
    libs = re.findall(r"^\s*([\w./-]+)\s+(v[\w.+-]+)(?:\s+//.*)?$", (REPO / "core/go.mod").read_text("utf-8"), re.M)
    for module, version in libs:
        name = module + "@" + version
        files = [f for f in (modcache / name).iterdir() if f.is_file() and NOTICE.match(f.name)]
        if not files:
            raise ValueError("Missing Go module notice: " + name)
        target = destination / "Go" / name.replace("/", "+")
        target.mkdir(parents=True)
        for f in files:
            shutil.copy2(f, target / f.name)


def checked_program(path):
    for rel in ("Workdesk.exe", "release.json", "tools/release_upgrade.py", "tools/partner_command.py",
                "runtime/python/python.exe", "runtime/python/pythonw.exe", "workdesk-ui/dist/index.html"):
        if not (path / rel).is_file():
            raise ValueError("Missing program input: " + rel)
    marker = json.loads((path / "release.json").read_text(encoding="utf-8-sig"))
    if marker.get("version") != VERSION:
        raise ValueError("Wrong program version")
    for f in path.rglob("*"):
        if f.is_symlink() or f.is_junction():
            raise ValueError("Staging link: " + str(f))
        rel = f.relative_to(path)
        if (f.name in {"tasks.sqlite3", ".git", ".maintenance", ".env", "instance.json"} or
                f.suffix.lower() in {".wdpa", ".wdpartner"} or
                rel.parts[:1] in {("user",), ("components",), ("cache",), ("recovery",)}):
            raise ValueError("Unexpected mutable state in staged program: " + str(rel))


def build(args):
    root, deps = args.root.resolve(), args.dependency_root.resolve()
    compiler = deps / "InnoSetup/ISCC.exe"
    if digest(compiler) != COMPILER_HASH or digest(deps / WEBVIEW) != WEBVIEW_HASH:
        raise ValueError("Compiler or offline WebView2 bootstrap differs from verified inputs.")
    program, selector = root / "program", root / "selector/Workdesk-Partners.exe"
    checked_program(program)
    catalog = json.loads((root / "partners/catalog.json").read_text(encoding="utf-8-sig"))
    if len(catalog["partners"]) != 8 or any(not p["file"].endswith(".wdpa") for p in catalog["partners"]):
        raise ValueError("Eight current .wdpa partners required.")
    tmpdir = root / "setup-build"
    tmpdir.mkdir(exist_ok=True)
    # Publish only a successfully compiled EXE; the previous candidate stays usable during a build.
    with tempfile.TemporaryDirectory(dir=tmpdir, prefix="iscc-") as tmp:
        temp = Path(tmp)
        license_dir, tools_dir = temp / "notices", temp / "tools"
        license_dir.mkdir()
        tools_dir.mkdir()
        notices(args.previous_inputs.resolve(), args.go_cache.resolve(), args.go_home.resolve(), license_dir)
        shutil.copy2(selector, tools_dir / selector.name)
        defs = {"ProgramRoot": program, "ToolsRoot": tools_dir, "NoticesRoot": license_dir,
                "OutputRoot": temp, "DependencyRoot": deps}
        command = [str(compiler), *[f"/D{k}={v}" for k, v in defs.items()], str(REPO / "tools/windows-installer/Workdesk12.iss")]
        log = root / "iscc.log"
        with log.open("wb") as stream:
            subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True)
        artifact = root / NAME
        (temp / NAME).replace(artifact)
    record = {"version": VERSION, "name": NAME, "sha256": digest(artifact), "bytes": artifact.stat().st_size,
              "selector_sha256": digest(selector), "catalog_sha256": digest(root / "partners/catalog.json"),
              "webview_sha256": WEBVIEW_HASH, "compiler_sha256": COMPILER_HASH}
    (root / "setup-build.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("root", "dependency-root", "previous-inputs", "go-cache", "go-home"):
        parser.add_argument("--" + key, required=True, type=Path)
    build(parser.parse_args())
