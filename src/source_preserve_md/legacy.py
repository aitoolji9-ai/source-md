"""Bounded, local legacy document import. Originals remain the source of record."""
from __future__ import annotations

import io
import html
import os
import re
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import zlib
import unicodedata
from collections import Counter

import olefile
from bs4 import BeautifulSoup
from defusedxml import ElementTree as ET

from .model import ConversionError,MAX_EXPANDED,MAX_INPUT,sha256


def inspect_ole(data, *, validate_hwp=False):
    try:
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            paths=ole.listdir(streams=True,storages=False)
            if len(paths)>15000:raise ConversionError('OLE 스트림 개수 상한을 초과했습니다.')
            if any(part in ('.','..') or '/' in part or '\\' in part for path in paths for part in path):
                raise ConversionError('OLE 내부에 위험한 경로가 있습니다.')
            if sum(ole.get_size(p) for p in paths)>MAX_EXPANDED:raise ConversionError('OLE 내부 크기 상한을 초과했습니다.')
            names={'/'.join(p) for p in paths}
            if 'EncryptionInfo' in names or 'EncryptedPackage' in names:raise ConversionError('암호화된 Office 문서는 암호 해제 원본이 필요합니다.')
            kind='hwp' if 'FileHeader' in names and ole.openstream('FileHeader').read(32).startswith(b'HWP Document File') else 'doc' if 'WordDocument' in names else 'xls' if names.intersection(('Workbook','Book')) else 'ppt' if 'PowerPoint Document' in names else 'legacy_binary'
            flags=0
            if kind=='hwp':
                header=ole.openstream('FileHeader').read(256)
                if len(header)<40:raise ConversionError('손상된 HWP 헤더입니다.')
                flags=struct.unpack_from('<I',header,36)[0]
                if flags&6:raise ConversionError('암호/배포용 HWP는 자동 해제하지 않습니다. 열람 가능한 일반 원본이 필요합니다.')
                if validate_hwp and flags&1:
                    expanded=0
                    for path in paths:
                        if path[0] not in ('BodyText','DocInfo','BinData'):continue
                        raw=ole.openstream(path).read();decoder=zlib.decompressobj(-15)
                        try:
                            unpacked=decoder.decompress(raw,MAX_EXPANDED-expanded+1)
                        except zlib.error:
                            if path[0]=='BinData':continue
                            raise ConversionError('HWP 압축 본문을 해석할 수 없습니다.')
                        expanded+=len(unpacked)
                        if expanded>MAX_EXPANDED or decoder.unconsumed_tail:raise ConversionError('HWP 압축 해제 크기 상한을 초과했습니다.')
            return kind,[{'path':'/'.join(p),'size':ole.get_size(p)} for p in paths],flags
    except ConversionError:raise
    except Exception as error:raise ConversionError('OLE 파일 구조를 읽지 못했습니다: '+type(error).__name__) from error


def run_local(command, *, cwd=None, timeout=60):
    with tempfile.TemporaryFile() as log:
        process=subprocess.Popen(command,cwd=cwd,stdout=log,stderr=log,start_new_session=True)
        try:process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL);process.wait()
            raise ConversionError('로컬 문서 변환 시간 상한을 초과했습니다.')
        log.seek(0);message=log.read(8192).decode(errors='replace')
        if process.returncode:raise ConversionError('로컬 변환 도구가 실패했습니다: '+message[-1000:])
        return message


def soffice_path():
    override=os.environ.get('SOURCE_PRESERVE_SOFFICE','')
    candidates=[override,shutil.which('soffice'),'/Applications/LibreOffice.app/Contents/MacOS/soffice',
                str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/soffice')]
    found=next((Path(p) for p in candidates if p and Path(p).is_file()),None)
    if found is None:raise ConversionError('구형 Office 변환에 LibreOffice가 필요합니다. SOURCE_PRESERVE_SOFFICE로 설치 경로를 지정하세요.')
    return found


def office_profile(path):
    path.mkdir()
    # Fresh profile; document macros, active embedded content and external
    # document retrieval are disabled independently of the user's office profile.
    (path/'registrymodifications.xcu').write_text('''<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop><prop oor:name="DisableActiveContent" oor:op="fuse"><value>true</value></prop><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item><item oor:path="/org.openoffice.Inet/Settings"><prop oor:name="ooInetProxyType" oor:op="fuse"><value>2</value></prop><prop oor:name="ooInetHTTPProxyName" oor:op="fuse"><value>127.0.0.1</value></prop><prop oor:name="ooInetHTTPProxyPort" oor:op="fuse"><value>9</value></prop><prop oor:name="ooInetHTTPSProxyName" oor:op="fuse"><value>127.0.0.1</value></prop><prop oor:name="ooInetHTTPSProxyPort" oor:op="fuse"><value>9</value></prop><prop oor:name="ooInetNoProxy" oor:op="fuse"><value></value></prop></item><item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>0</value></prop></item><item oor:path="/org.openoffice.Office.Calc/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>0</value></prop></item></oor:items>''')


def convert_office(source,outdir,target,profile):
    executable=soffice_path();office_profile(profile)
    command=[str(executable),'-env:UserInstallation='+profile.as_uri(),'--headless','--nologo','--nodefault','--nofirststartwizard','--convert-to',target,'--outdir',str(outdir),str(source)]
    log=run_local(command,timeout=75)
    output=outdir/(source.stem+'.'+target.split(':',1)[0])
    if not output.is_file() or not 0<output.stat().st_size<=MAX_INPUT:raise ConversionError('Office 변환 결과가 없거나 크기 상한을 초과했습니다: '+log[-600:])
    return output,log


def extract_legacy(data,result,assets):
    from .adapters import extract
    from .html_extract import extract_html
    from .network import ResourceLoader
    kind,inventory,flags=inspect_ole(data,validate_hwp=True)
    result.metadata['ole_streams']=assets.json('ole-streams.json',inventory,'original-ole')
    if kind=='legacy_binary':raise ConversionError('이 OLE 파일의 문서 종류는 지원하지 않습니다.')
    with tempfile.TemporaryDirectory(prefix='source-preserve-legacy-') as directory:
        work=Path(directory);source=work/('input.'+kind);source.write_bytes(data)
        if kind=='hwp':
            with olefile.OleFileIO(io.BytesIO(data)) as ole:
                if ole.exists('PrvText'):
                    preview=ole.openstream('PrvText').read()
                    raw_preview=assets.add(preview,'hwp-native-preview.utf16','PrvText')
                    text=preview.decode('utf-16le',errors='replace')
                    result.metadata['hwp_native_preview']={'raw_asset':raw_preview,'raw_sha256':sha256(preview),'text_asset':assets.add(text.encode(),'hwp-native-preview.txt','PrvText-decoded'),
                                                           'encoding':'utf-16le','scope':'HWP 내장 텍스트 미리보기. <> 등 표/개체 표식은 보이는 글자와 구별.'}
            output=work/'html'
            command=[sys.executable,'-c','from hwp5.hwp5html import main; main()','--output',str(output),str(source)]
            log=run_local(command,timeout=60)
            if re.search(r'(?m)^(?:ERROR|CRITICAL)|Traceback|ParseError',log):raise ConversionError('HWP 파서가 본문 해석 오류를 보고했습니다: '+log[-600:])
            main=output/'index.xhtml'
            if not main.is_file() or main.stat().st_size>MAX_INPUT:raise ConversionError('HWP HTML 본문을 만들지 못했습니다: '+log[-600:])
            model=output/'document-model.xml'
            run_local([sys.executable,'-c','from hwp5.xmlmodel import Hwp5File; import sys; h=Hwp5File(sys.argv[1]); f=open(sys.argv[2],"wb"); h.xmlevents().dump(f); f.close(); h.close()',str(source),str(model)],timeout=60)
            files=[p for p in output.rglob('*') if p.is_file()]
            if len(files)>15000 or sum(p.stat().st_size for p in files)>MAX_EXPANDED:raise ConversionError('HWP 출력 자산 상한을 초과했습니다.')
            refs={}
            for file in files:
                if file.is_symlink() or not file.resolve().is_relative_to(output.resolve()):raise ConversionError('HWP 변환 자산 경로가 잘못됐습니다.')
                refs[file.relative_to(output).as_posix()]=assets.add(file.read_bytes(),file.name,'hwp-derived/'+file.relative_to(output).as_posix())
            loader=ResourceLoader(assets,download=False);loader.cache.update(refs)
            body=BeautifulSoup(main.read_text(encoding='utf-8'),'html.parser')
            tree=ET.fromstring(model.read_bytes());parents={c:p for p in tree.iter() for c in p}
            notes=[]
            paragraphs_by_text={}
            for paragraph in body.find_all('p'):paragraphs_by_text.setdefault(''.join(paragraph.get_text().split()),[]).append(paragraph)
            linked_parents={}
            for note in tree.iter():
                if note.tag not in ('FootNote','EndNote'):continue
                text='\n'.join(n.text or '' for n in note.iter() if n.tag=='Text')
                kind='footnote' if note.tag=='FootNote' else 'endnote';number=note.get('number',str(len(notes)+1));ident='hwp-'+kind+'-'+number
                parent=parents.get(note)
                while parent is not None and parent.tag!='Paragraph':parent=parents.get(parent)
                def inline_text(node):
                    if node.tag in ('FootNote','EndNote'):return ''
                    return (node.text or '') if node.tag=='Text' else ''.join(inline_text(c) for c in node)
                context=inline_text(parent) if parent is not None else ''
                paragraph=linked_parents.get(id(parent))
                if paragraph is None and context:
                    candidates=paragraphs_by_text.get(''.join(context.split()),[])
                    paragraph=candidates.pop(0) if len(candidates)==1 else None
                    if paragraph is not None:linked_parents[id(parent)]=paragraph
                if paragraph is not None:
                    marker=body.new_tag('sup');link=body.new_tag('a',href='#'+ident,id=ident+'-ref');link.string='['+number+']';marker.append(link);paragraph.append(marker)
                notes.append({'kind':kind,'number':number,'id':ident,'text':text,'parent_text':context,'reference_linked':paragraph is not None})
            if notes:
                section=body.new_tag('section');heading=body.new_tag('h2');heading.string='각주·미주';section.append(heading)
                for note in notes:
                    paragraph=body.new_tag('p',id=note['id']);paragraph.string=('각주 ' if note['kind']=='footnote' else '미주 ')+note['number']+': '+note['text']
                    if note['reference_linked']:
                        link=body.new_tag('a',href='#'+note['id']+'-ref');link.string=' ↩';paragraph.append(link)
                    section.append(paragraph)
                body.body.append(section);result.metadata['hwp_notes']=notes
                if any(not note['reference_linked'] for note in notes):result.warn('hwp_note_reference_unresolved','일부 각주/미주 연결 문단이 모호해 연결을 추정하지 않았습니다. 본문과 주석 내용·원본 XML은 보존했습니다.')
            normalized=lambda value:''.join(unicodedata.normalize('NFC',value).split())
            chunks=Counter(normalized(n.text or '') for n in tree.iter('Text') if normalized(n.text or ''))
            visible=normalized(body.get_text())
            missing=[{'text':text,'required_count':count,'actual_count':visible.count(text)} for text,count in chunks.items() if visible.count(text)<count]
            native_tables=sum(1 for n in tree.iter('TableControl'))
            rendered_tables=len(body.find_all('table'))
            coverage={'model_text_chunks':sum(chunks.values()),'missing_text_chunks':missing,'model_tables':native_tables,'rendered_tables':rendered_tables,
                      'scope':'로컬 파서 XML과 변환 본문의 내용 대조. 원본 의미·시각 완전성 인증과 다름.'}
            result.metadata['hwp_coverage']=coverage
            if missing or native_tables!=rendered_tables:result.warn('hwp_conversion_incomplete','HWP 모델 XML과 변환 본문의 글자/표 개수에 차이가 있습니다. 원본 XML·부분 결과를 보존했습니다.')
            # The styled HTML is a portable derived view, not just an inert
            # source dump with now-broken bindata/styles relative addresses.
            for node in body.find_all(['img','link']):
                attribute='src' if node.name=='img' else 'href'
                original=node.get(attribute,'')
                if original in refs:node[attribute]=Path(refs[original]).name
            loader.cache.update({Path(value).name:value for value in refs.values()})
            derived=assets.add(str(body).encode(),'expanded-hwp-body.xhtml','hwp-derived-body')
            extract_html(str(body),result,assets,loader,'hwp-body')
            result.metadata['legacy_conversion']={'tool':'pyhwp','version':'0.1b15','format':'xhtml','derived':refs['index.xhtml'],'flags':flags,'original_sha256':sha256(data)}
            result.metadata['legacy_conversion'].update(body_asset=derived,model_xml=refs['document-model.xml'])
            result.warn('hwp_conversion_scope','HWP5 본문·표·이미지를 로컬 파서로 변환했습니다. 파서의 미지원 개체·글꼴·페이지 배치는 원본 대조 대상입니다.')
        else:
            target={'doc':'docx','xls':'xlsx','ppt':'pptx'}[kind]
            generated,log=convert_office(source,work,target,work/'profile')
            derived=assets.add(generated.read_bytes(),generated.name,'libreoffice-derived')
            nested=extract(generated.read_bytes(),generated.name,assets,download=False)
            result.units=nested.units;result.warnings.extend(nested.warnings)
            result.metadata.update(nested.metadata)
            result.metadata['legacy_conversion']={'tool':'LibreOffice','format':target,'derived':derived,'original_sha256':sha256(data),'log':log[:2000]}
            for unit in result.units:unit.source_locator='converted:'+target+'/'+unit.source_locator
            result.warn('legacy_conversion_scope','구형 '+kind.upper()+'를 격리된 로컬 Office 프로필에서 '+target.upper()+'로 변환해 본문을 추출했습니다. 변환 정의·수식/캐시·자산과 원본을 보관하며 글꼴·레이아웃·변환 손실은 별도 대조 대상입니다.')
