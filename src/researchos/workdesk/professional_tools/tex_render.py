"""Run a pinned pdfTeX engine in a fresh V8 isolate with memory-only TeX files."""
from hashlib import sha256
from pathlib import Path

from ..instance import load_profile

PACKAGE = 'pdftex-js-0.0.10'
ASSETS = {
    'pdftex-worker.js': 'd7e5a0a6cbd830c21975b417037906090b6a41d2241f6ad569273066ca2e8818',
    'pdftex-worker.data': '087b3277b9eed75567f02f00fc51245a6df0affc076da66f935d5636682cc9a5',
    'pdftex-worker.js.mem': 'ef9e2e3df459ffaf8323560d125adf4f3573c52bee6cfa3809d8b791cc47f48e',
}
# This emulates only the two static assets needed by the packaged browser worker.
# There is no host filesystem, network transport or Python callback in the isolate.
BOOT = """
var Module={ENVIRONMENT:"WORKER"}, location={pathname:"/"};
var resources=Object.create(null);
function XMLHttpRequest(){}
XMLHttpRequest.prototype.open=function(method,url){
    if(method!=="GET" || !Object.hasOwn(resources,url))throw Error("Unregistered resource");
    this.response=resources[url]; this.status=200;
};
XMLHttpRequest.prototype.send=function(){if(this.onload)this.onload()};
function importScripts(){throw Error("External scripts disabled")}
function addEventListener(){}
var errors=[], warnings=[];
function postMessage(msg){
    if(msg.type==="err" || msg.type==="log"){
        var line=String(msg.value).slice(0,240);
        if(/missing character|undefined references|rerun to get cross-references/i.test(line) && warnings.length<4){
            warnings.push(line);
        }
        errors.push(line);
        if(errors.length>15) errors.shift();
    }
}
"""


def engine_root():
    profile = load_profile(Path.cwd())
    if profile and profile.get('channel') == 'development':
        return Path(profile['development_root']) / 'toolchains' / PACKAGE
    return Path.cwd() / 'runtime' / PACKAGE


def compile_pdf(source, root):
    from py_mini_racer import MiniRacer, JSEvalException, JSOOMException
    assets = {name: (root / name).read_bytes() for name in ASSETS}
    if any(sha256(data).hexdigest() != ASSETS[name] for name, data in assets.items()):
        raise ValueError('TeX 引擎或预置宏包与登记版本不符。')
    if len(source) > 512 * 1024:
        raise ValueError('TeX 源码超过 512 KiB。')
    with MiniRacer() as vm:
        vm.set_hard_memory_limit(480 * 1024 * 1024)
        vm.eval(BOOT)
        for name in ('pdftex-worker.data', 'pdftex-worker.js.mem'):
            data = assets[name]
            view = vm.eval(f'resources["{name}"]=new ArrayBuffer({len(data)})')
            view[:] = data
            del view
        vm.eval(assets['pdftex-worker.js'].decode('utf-8'), timeout_sec=12)
        view = vm.eval(f'var inputBytes=new Uint8Array({len(source)});inputBytes.buffer')
        view[:] = source
        del view
        try:
            vm.eval("""
                FS.writeFile('/input.tex',inputBytes,{encoding:'binary'});
                Module.calledRun=false; shouldRunNow=true;
                Module.run(['-no-shell-escape','-halt-on-error','-interaction=nonstopmode',
                            '-output-format','pdf','input.tex']);
            """, timeout_sec=16)
            size = vm.eval("FS.stat('/input.pdf').size")
            if not 0 < size <= 32 * 1024 * 1024:
                raise ValueError('编译结果大小无效。')
            pdf = bytes(vm.eval("FS.readFile('/input.pdf').slice().buffer"))
            warnings = vm.eval('warnings.join("; ")')
        except TimeoutError as exc:
            raise ValueError('pdfLaTeX 编译超过 16 秒。') from exc
        except JSOOMException as exc:
            raise ValueError('pdfLaTeX 编译超出内存限额。') from exc
        except JSEvalException as exc:
            tail = vm.eval('errors.join("\\n").slice(-1600)')
            raise ValueError('本地 pdfLaTeX 未完成排版。' + tail) from exc
    return pdf, warnings


def render_tex(path, output_directory, params):
    from .document_pages import preview
    try:
        data, warnings = compile_pdf(path.read_bytes(), engine_root())
    except (ImportError, FileNotFoundError):
        return [('TeX 排版', 'PENDING', '缺少登记的本地 pdfTeX 引擎或 V8 组件。')], []
    except ValueError as exc:
        return [('TeX 排版', 'PENDING', str(exc))], []
    output_directory.mkdir(parents=True, exist_ok=False)
    pdf = output_directory / 'typeset.pdf'
    pdf.write_bytes(data)
    checks, images = preview(pdf, params)
    state = 'PENDING' if warnings else 'PASSED'
    detail = warnings or '用离线 pdfLaTeX 预置宏包生成 PDF。'
    return [('TeX 排版', state, detail), *checks], [pdf, *images]
