#!/usr/bin/env python3
"""Run the local engine from a checkout or an explicitly installed skill."""
import json
import os
import subprocess
import sys
from pathlib import Path


def interpreter():
    skill = Path(__file__).resolve().parents[1]
    override = os.environ.get('SOURCE_PRESERVE_MD_ROOT')
    if override:
        roots = [Path(override).expanduser().resolve()]
    else:
        config = skill / '.engine.json'
        if config.is_file():
            value = json.loads(config.read_text(encoding='utf-8'))
            python = Path(value['python']).expanduser()
            if not python.is_file():
                raise ValueError('스킬에 연결된 Python이 없습니다. install-skill로 다시 연결하세요.')
            return python
        roots = list(skill.parents)
    for root in roots:
        python = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        if (root / 'pyproject.toml').is_file() and (root / 'src/source_preserve_md').is_dir() and python.is_file():
            return python
    raise ValueError('엔진을 찾지 못했습니다. install-skill을 실행하거나 SOURCE_PRESERVE_MD_ROOT에 체크아웃 경로를 지정하세요.')


def main():
    try:
        python = interpreter()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    # Preserve the caller's cwd: relative input/output paths keep their meaning.
    return subprocess.run([str(python), '-m', 'source_preserve_md', *sys.argv[1:]]).returncode


if __name__ == "__main__":
    raise SystemExit(main())

