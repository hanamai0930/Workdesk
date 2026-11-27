"""Inspect this worker's interpreter and providers without dispatching a job."""
from importlib import import_module
from importlib.util import find_spec
import json


def capabilities():
    from .office_render import office_executable

    common = ['openpyxl', 'lxml.etree']  # Imported by the worker entry point.
    requirements = {
        'xlsx': [], 'csv': [], 'docx': ['docx'], 'pptx': ['pptx'],
        'pdf': ['pypdf', 'pypdfium2', 'PIL', 'reportlab'],
        'md': ['markdown_it', 'nh3'], 'html': ['nh3'], 'tex': ['pylatexenc', 'nh3'],
    }
    available = {}
    for name in set(common).union(*requirements.values(), ['winrt.windows.media.ocr']):
        try:
            available[name] = find_spec(name) is not None
        except (ModuleNotFoundError, ValueError):
            available[name] = False
    try:
        office = office_executable().is_file()
    except (OSError, ValueError):
        office = False
    output = {}
    for fmt, extra in requirements.items():
        missing = [name for name in common + extra if not available[name]]
        operations = {}
        if fmt == 'xlsx':
            operations['recalculate'] = office
        if fmt in ('docx', 'pptx'):
            operations['render_pages'] = office and available['pypdfium2'] and available['PIL']
        if fmt == 'tex':
            from .tex_render import engine_root, ASSETS
            root = engine_root()
            operations['typeset_pdf'] = find_spec('py_mini_racer') is not None and all((root/name).is_file() for name in ASSETS)
        if fmt == 'pdf':
            operations['ocr_languages'] = []
            if available['winrt.windows.media.ocr']:
                try:
                    engine = import_module('winrt.windows.media.ocr').OcrEngine
                    operations['ocr_languages'] = [v.language_tag for v in engine.available_recognizer_languages]
                except (ImportError, OSError, RuntimeError):
                    pass
        output[fmt] = dict(ready=not missing, missing=missing, operations=operations)
    return output


if __name__ == '__main__':
    print(json.dumps(capabilities()))
