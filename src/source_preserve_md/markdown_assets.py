"""Copy explicitly referenced local assets while keeping MD bytes unchanged."""
from pathlib import Path
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

from .model import sha256


def preserve_markdown_assets(result, assets, source_path):
    if not source_path:
        result.warn('markdown_asset_base_missing','원본 MD의 자산 경로 기준이 없어 연결 자산은 수집하지 못했습니다.')
        return
    text=result.units[0].text
    refs=[]
    for token in MarkdownIt('commonmark',{'html':True}).parse(text):
        for item in [token,*(token.children or [])]:
            for name in ('src','href'):
                value=item.attrGet(name)
                if value:refs.append(value)
            if item.type in ('html_inline','html_block'):
                soup=BeautifulSoup(item.content,'html.parser')
                refs.extend(n.get('src') or n.get('href') for n in soup.find_all(['img','a']) if n.get('src') or n.get('href'))
    base=source_path.parent.resolve()
    reserved={'manifest.json','structure.json','checks.json','integrity.json','error.json','00_원본복원.md','03_변환검증보고서.md'}
    for ref in dict.fromkeys(refs):
        parsed=urlparse(ref)
        if parsed.scheme or parsed.netloc:
            result.warn('markdown_remote_asset','MD 바이트를 유지하므로 원격 링크 자산은 그대로 남겼습니다. 로컬 복사와 구분해 주세요.')
            continue
        if not parsed.path:continue
        relative=Path(unquote(parsed.path))
        if not relative.parts:continue
        source=(base/relative).resolve();target=(assets.root/relative).resolve()
        if relative.is_absolute() or not source.is_relative_to(base) or not target.is_relative_to(assets.root.resolve()) or relative.parts[0]=='source' or str(relative) in reserved:
            result.warn('markdown_asset_boundary','원본/결과 폴더 경계 밖 또는 예약 경로 자산을 복사하지 않았습니다: '+ref)
            continue
        if not source.is_file():
            result.warn('markdown_asset_missing','연결 자산 파일이 없습니다: '+ref)
            continue
        if source.stat().st_size > 20*1024*1024:
            result.warn('markdown_asset_limit','연결 자산 개별 20MB 상한을 초과했습니다: '+ref)
            continue
        data=source.read_bytes();digest=sha256(data)
        if target.exists():
            if not target.is_file() or sha256(target.read_bytes())!=digest:
                result.warn('markdown_asset_collision','결과의 기존 경로와 충돌해 자산을 복사하지 않았습니다: '+ref)
            continue
        if assets.total_bytes+len(data)>500*1024*1024 or len(assets.records)>=25000:
            result.warn('markdown_asset_limit','연결 자산 총량 상한에 도달했습니다.')
            break
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        assets.total_bytes+=len(data)
        assets.records.append({'path':target.relative_to(assets.root.resolve()).as_posix(),'sha256':digest,'size':len(data),'source':str(source)})
    result.warnings=[w for w in result.warnings if w['code']!='markdown_external_assets']
