"""Explicit local skill installation and dependency diagnostics; no downloads."""
from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .model import ConversionError, sha256

SKILL_FILES = ('SKILL.md', 'scripts/run.py', 'agents/openai.yaml',
               'references/dom-snapshot.md', 'references/legacy-and-review.md')


def skill_source() -> Path:
    bundled = Path(__file__).with_name('skill')
    if (bundled / 'SKILL.md').is_file():
        return bundled
    checkout = Path(__file__).resolve().parents[2] / 'skills/source-preserve-md'
    if (checkout / 'SKILL.md').is_file():
        return checkout
    raise ConversionError('설치 패키지에 스킬 파일이 없습니다. 배포 wheel을 다시 설치하세요.')


def install_skill(target: Path | str) -> dict:
    source = skill_source()
    target = Path(target).expanduser().absolute()
    if os.path.lexists(target):
        raise ConversionError('스킬 대상이 이미 존재합니다. 새 폴더를 지정하세요.')
    payloads = {name: (source / name).read_bytes() for name in SKILL_FILES}
    target.mkdir(parents=True, exist_ok=False)
    for name, data in payloads.items():
        file = target / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
    (target / '.engine.json').write_text(json.dumps({'schema_version': 1, 'python': sys.executable}, ensure_ascii=False) + '\n', encoding='utf-8')
    hashes = {name: sha256((target / name).read_bytes()) for name in SKILL_FILES}
    if any(hashes[name] != sha256(data) for name, data in payloads.items()):
        raise ConversionError('스킬 저장 후 해시 검사가 실패했습니다.')
    return {'skill': str(target), 'version': __version__, 'files_sha256': hashes,
            'engine_python': sys.executable, 'existing_skill_overwritten': False}


def doctor() -> dict:
    from .legacy import soffice_path
    try:
        office = str(soffice_path())
    except ConversionError:
        office = None
    ocr = sys.platform == 'darwin' and shutil.which('swiftc') is not None
    try:
        importlib.metadata.version('playwright')
        playwright = True
    except importlib.metadata.PackageNotFoundError:
        playwright = False
    return {'version': __version__, 'python': sys.version.split()[0], 'platform': sys.platform,
            'generative_ai_calls': 0, 'skill_files_available': all((skill_source()/name).is_file() for name in SKILL_FILES),
            'ocr': {'prerequisites_present': ocr, 'engine': 'apple-vision', 'actual_recognition_tested': False,
                    'setup': 'macOS + Xcode Command Line Tools (xcode-select --install)'},
            'legacy_office': {'executable': office, 'conversion_tested': False,
                              'setup': 'LibreOffice 또는 SOURCE_PRESERVE_SOFFICE'},
            'dynamic_web': {'playwright_installed': playwright, 'browser_launch_tested': False,
                            'setup': 'python -m playwright install chromium 또는 Chrome 설치'}}
