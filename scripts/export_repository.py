"""Export an allowlisted, portable repository without private extraction history."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOP = ('README.md','pyproject.toml','uv.lock','.gitignore','LICENSE','THIRD_PARTY_NOTICES.md','SECURITY.md','시작.command','install.sh','_source_md_build.py','_source_md_wheel.py')
SCRIPTS = ('quality_check.py','fetch_quality_inputs.py','export_repository.py','verify_remaining.py','verify_distribution.py','benchmark_handwriting.py','benchmark_handwriting_extra.py','prepare_korean_strokes.py')
SKILL = ('SKILL.md','scripts/run.py','agents/openai.yaml','references/dom-snapshot.md','references/legacy-and-review.md')
TABLE_FIXTURE = Path('tests/fixtures/shadowed-merged-table.png')
FORBIDDEN = (r'/' + r'Users/[^/\s]+/', r'/' + r'home/[^/\s]+/', r'gh[pousr]_[A-Za-z0-9]{30,}',
             r'github_pat_[A-Za-z0-9_]{30,}', r'AKIA[0-9A-Z]{16}', r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')


def export(target: Path) -> dict:
    target = target.expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise ValueError('Export destination must be a new folder')
    files = [Path(name) for name in TOP]
    files += [p.relative_to(ROOT) for p in (ROOT/'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    files += [p.relative_to(ROOT) for p in (ROOT/'tests').glob('test_*.py')]
    if hashlib.sha256((ROOT/TABLE_FIXTURE).read_bytes()).hexdigest() != json.loads((ROOT/TABLE_FIXTURE.with_suffix('.json')).read_text())['sha256']:
        raise ValueError('Shadowed table fixture hash mismatch')
    files += [TABLE_FIXTURE, TABLE_FIXTURE.with_suffix('.json')]
    files += [Path('scripts')/name for name in SCRIPTS]
    files += [Path('skills/source-preserve-md')/name for name in SKILL]
    files += [p.relative_to(ROOT) for directory in ['docs','.github'] for p in (ROOT/directory).rglob('*') if p.is_file()]
    files += [Path('examples/구조보존_시험.html'),Path('examples/quality-inputs/sources.json'),
              Path('examples/quality-inputs/python-controlflow.html'),Path('examples/quality-inputs/PYTHON_LICENSE.txt'),
              Path('examples/handwriting-inputs/sources.json'),Path('examples/handwriting-inputs/korean-strokes.json')]
    catalog=Path('examples/legacy-inputs/sources.json')
    files.append(catalog)
    for entry in json.loads((ROOT/catalog).read_text(encoding='utf-8')):
        name=entry['filename']
        expected_url=r'https://raw\.githubusercontent\.com/mete0r/pyhwp/[0-9a-f]{40}/tests/hwp5_tests/fixtures/'+re.escape(name)
        if Path(name).name!=name or not re.fullmatch(expected_url,entry['url']):
            raise ValueError('Unexpected legacy fixture provenance')
        relative=catalog.parent/name
        if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=entry['sha256']:
            raise ValueError('Legacy fixture hash mismatch: '+name)
        files.append(relative)
    payloads = {}
    for relative in sorted(set(files)):
        data = (ROOT/relative).read_bytes()
        # Binary test fixtures keep their upstream provenance and byte hashes.
        if relative.suffix not in ('.hwp',) and relative != TABLE_FIXTURE and relative.as_posix()!='src/source_preserve_md/install_probe.png':
            text = data.decode('utf-8')
            for pattern in FORBIDDEN:
                # Scanner's regex definitions are patterns, not private values.
                if re.search(pattern,text):
                    raise ValueError(f'Private path/credential pattern: {relative}')
        payloads[relative] = data
    target.mkdir(parents=True)
    hashes = {}
    for relative,data in payloads.items():
        dest = target/relative; dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
        shutil.copymode(ROOT/relative,dest)
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        if digest != hashlib.sha256(data).hexdigest():raise ValueError('Copy hash mismatch')
        hashes[relative.as_posix()] = digest
    return {'files':len(hashes),'sha256':hashes,'private_patterns_found':0,'symlinks':0,
            'excluded':['outputs','virtualenv','caches','local skill binding','personal documents','downloaded quality binaries']}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();report=export(args.out)
    print(json.dumps(report,ensure_ascii=False,indent=2))
