"""Explicitly fetch pinned public test inputs and verify SHA-256 before saving."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def fetch_catalog(folder: Path) -> int:
    sources = json.loads((folder/'sources.json').read_text(encoding='utf-8'))
    for source in sources:
        name = source['filename']
        if Path(name).name != name:
            raise ValueError('Invalid fixture filename')
        target = folder/name
        if target.is_file():
            data = target.read_bytes()
        else:
            response = httpx.get(source['url'], timeout=30, follow_redirects=True)
            response.raise_for_status()
            data = response.content
        if len(data) > 10*1024*1024 or hashlib.sha256(data).hexdigest() != source['sha256']:
            raise ValueError('Fixture hash/size mismatch: '+name)
        if not target.exists():
            target.write_bytes(data)
    return len(sources)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=['quality','legacy','all'], default='quality')
    args = parser.parse_args()
    kinds = ['quality','legacy'] if args.kind == 'all' else [args.kind]
    result = {kind: fetch_catalog(ROOT/'examples'/f'{kind}-inputs') for kind in kinds}
    print(json.dumps({'verified_inputs':result}))


if __name__ == '__main__':
    main()
