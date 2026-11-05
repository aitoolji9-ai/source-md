"""Import observed browser DOM without executing scripts in saved local HTML."""
from __future__ import annotations

import json
from pathlib import Path

from .html_extract import escape, extract_html
from .model import ConversionError, Extraction, MAX_INPUT, sha256
from .network import ResourceLoader


def load_snapshot(value, source_bytes):
    if isinstance(value,dict):
        payload=json.dumps(value,ensure_ascii=False).encode()
    elif isinstance(value,bytes):payload=value
    else:
        path=Path(value).expanduser()
        if not path.is_file() or path.stat().st_size>MAX_INPUT:raise ConversionError('DOM 스냅샷이 없거나 크기 상한을 초과했습니다.')
        payload=path.read_bytes()
    if len(payload)>MAX_INPUT:raise ConversionError('DOM 스냅샷 크기 상한을 초과했습니다.')
    try:data=json.loads(payload)
    except (ValueError,UnicodeDecodeError) as error:raise ConversionError('DOM 스냅샷 JSON을 읽지 못했습니다.') from error
    if not isinstance(data,dict) or type(data.get('schema_version')) is not int or data['schema_version']!=1:raise ConversionError('지원하는 DOM 스냅샷 schema_version은 정수 1입니다.')
    if data.get('source_sha256')!=sha256(source_bytes):raise ConversionError('DOM 스냅샷의 원본 해시가 입력 HTML과 다릅니다.')
    for name in ('source_url','captured_at'):
        if name in data and not isinstance(data[name],str):raise ConversionError('DOM 스냅샷 메타데이터는 문자열이어야 합니다.')
    if data.get('automatic_tabs') is not None:
        report=data['automatic_tabs']
        if not isinstance(report,dict) or not isinstance(report.get('outcomes'),list) or any(not isinstance(item,dict) or not isinstance(item.get('status'),str) for item in report['outcomes']):
            raise ConversionError('자동 탭 보고서는 상태 목록 outcomes를 가진 객체여야 합니다.')
    frames=data.get('frames')
    if not isinstance(frames,list) or not 1<=len(frames)<=64:raise ConversionError('DOM 스냅샷은 1~64개 상태/프레임을 담아야 합니다.')
    for frame in frames:
        if not isinstance(frame,dict) or not isinstance(frame.get('html'),str):raise ConversionError('DOM 프레임에 HTML 문자열이 필요합니다.')
        if len(frame['html'].encode())>20*1024*1024:raise ConversionError('DOM 프레임 개별 20MB 상한을 초과했습니다.')
        for name in ('label','state','url'):
            if name in frame and not isinstance(frame[name],str):raise ConversionError('DOM 프레임 메타데이터는 문자열이어야 합니다.')
        if type(frame.get('frame_index',0)) is not int or not 0<=frame.get('frame_index',0)<=63:
            raise ConversionError('DOM frame_index는 0~63 정수여야 합니다.')
    return data,payload


def apply_snapshot(static, assets, data, payload, *, download=True, selector='', mode='observed_snapshot', main_selector_only=False):
    if static.format!='html':raise ConversionError('DOM 스냅샷은 원본 HTML과 함께 입력해야 합니다.')
    assets.json('static-extraction.json',static.structure(),'original-html')
    snapshot_asset=assets.add(payload,'browser-dom-snapshot.json','supplied-browser-observation')
    result=Extraction(static.title,'html',metadata=dict(static.metadata))
    states=[]
    for index,frame in enumerate(data['frames']):
        locator=f'view:{index}/frame:{frame.get("frame_index",0)}'
        html_asset=assets.add(frame['html'].encode(),f'view-{index:03d}-dom.html',locator)
        url=frame.get('url') or data.get('source_url','')
        selected=selector if not main_selector_only or frame.get('frame_index',0)==0 else ''
        extract_html(frame['html'],result,assets,ResourceLoader(assets,base_url=url,download=download),locator,url,selected,anchor_prefix=f'view-{index}-')
        unit=result.units[-1]
        unit.label='브라우저 상태: '+escape(frame.get('label',frame.get('state',str(index))))
        unit.metadata.update(dom_html=html_asset,browser_state=frame.get('state',str(index)),browser_state_label=frame.get('label',''))
        states.append({'index':index,'label':frame.get('label',''),'state':frame.get('state',str(index)),'html_sha256':sha256(frame['html'].encode()),'html_asset':html_asset,'unit_id':unit.id})
    result.metadata['dom_capture']={'mode':mode,'snapshot_asset':snapshot_asset,'source_sha256':data['source_sha256'],
                                   'source_url':data.get('source_url',''),'captured_at':data.get('captured_at',''),'states':states,
                                   'scope':'제공된 브라우저 상태의 DOM 변환. 해시는 출처 묶음의 무결성이며 사이트 전체성 인증과 다릅니다.'}
    result.warn('dom_snapshot_scope','관측된 브라우저 DOM 상태를 변환했습니다. 보이지 않은 탭·가상 목록·상태의 전체성은 별도 대조가 필요합니다.')
    if data.get('automatic_tabs'):
        report=data['automatic_tabs']
        result.metadata['dom_capture']['automatic_tabs']=report
        if isinstance(report,dict):
            for item in report.get('outcomes',[]):
                if item.get('status') not in ('captured','already-selected','scroll-end','frame-recovered'):
                    code='automatic_tab_incomplete' if item.get('status') in ('error','limit','scroll-limit','frame-error','frame-inaccessible','timeout-or-unchanged') or item.get('reason')=='not-visited' else 'automatic_tab_excluded'
                    result.warn(code,'자동 탭 확인 항목: '+str(item))
    return result
