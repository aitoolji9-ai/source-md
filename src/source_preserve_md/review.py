"""Evidence-linked review and explicit, source-bound OCR corrections."""
from __future__ import annotations

import copy
import html
import json
from pathlib import Path

from .model import ConversionError,MAX_INPUT,sha256,md_url

REVIEW='04_원본대조.html'


def local_file(root,relative):
    path=(root/relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():raise ConversionError('대조 자산이 묶음 폴더 밖이거나 존재하지 않습니다.')
    return path


def load_corrections(value,source):
    if not isinstance(value,(dict,bytes)) and Path(value).expanduser().stat().st_size>2*1024*1024:raise ConversionError('교정 JSON은 2MB까지 입력할 수 있습니다.')
    payload=json.dumps(value,ensure_ascii=False).encode() if isinstance(value,dict) else value if isinstance(value,bytes) else Path(value).expanduser().read_bytes()
    if len(payload)>2*1024*1024:raise ConversionError('교정 JSON은 2MB까지 입력할 수 있습니다.')
    try:data=json.loads(payload)
    except (ValueError,UnicodeError) as error:raise ConversionError('교정 JSON을 읽지 못했습니다.') from error
    if not isinstance(data,dict) or type(data.get('schema_version')) is not int or data['schema_version']!=1 or data.get('source_sha256')!=sha256(source):raise ConversionError('교정 JSON의 버전 또는 원본 해시가 입력과 다릅니다.')
    actions=data.get('actions')
    if not isinstance(actions,list) or not 1<=len(actions)<=1000:raise ConversionError('교정은 1~1000개 항목으로 입력하세요.')
    for action in actions:
        if not isinstance(action,dict) or action.get('kind') not in ('line','cell') or not isinstance(action.get('target'),str):raise ConversionError('교정 종류·OCR 대상이 잘못됐습니다.')
        for key in ('expected','replacement'):
            if not isinstance(action.get(key),str) or len(action[key])>10000:raise ConversionError('교정 원문과 대체문은 10000자 이하 문자열이어야 합니다.')
        keys=('line',) if action['kind']=='line' else ('table','row','column')
        if any(type(action.get(k)) is not int or action[k]<0 for k in keys):raise ConversionError('교정 위치는 0부터 시작하는 정수여야 합니다.')
    return data,payload


def apply_corrections(result,assets,data,payload):
    from .ocr_refine import render_report
    staged={}
    for action in data['actions']:
        target=action['target']
        if target not in staged:
            unit=next((u for u in result.units if u.metadata.get('ocr_data') and json.loads(local_file(assets.root,u.metadata['ocr_data']).read_text()).get('source_locator')==target),None)
            if unit is None:raise ConversionError('교정할 OCR 대상을 찾지 못했습니다: '+target)
            staged[target]=[unit,json.loads(local_file(assets.root,unit.metadata['ocr_data']).read_text()),[]]
        unit,raw,changes=staged[target];layout=raw['refinement']
        try:
            if action['kind']=='line':
                block=next(b for b in layout['blocks'] if b.get('kind')=='text' and b.get('source_observation')==action['line'])
                current=block['text']
                if current!=action['expected']:raise ConversionError('OCR 원문이 교정 파일의 예상 원문과 다릅니다.')
                block['text']=action['replacement'];block['human_corrected']=True
            else:
                table=layout['tables'][action['table']]
                cell=next(c for c in table['cells'] if c['row']==action['row'] and c['column']==action['column'])
                if cell['text']!=action['expected']:raise ConversionError('표 셀 원문이 교정 파일의 예상 원문과 다릅니다.')
                cell['before_human_correction']=cell['text'];cell['text']=action['replacement'];cell['status']='human_corrected'
                table['values'][cell['row']][cell['column']]=cell['text']
                table['review_cells']=[c for c in table.get('review_cells',[]) if (c['row']-1,c['column']-1)!=(cell['row'],cell['column'])]
                table['unresolved_cells']=len(table['review_cells'])
            changes.append(action)
        except (IndexError,KeyError,StopIteration) as error:raise ConversionError('교정 위치에 대응하는 OCR 줄/셀이 없습니다.') from error
    correction_asset=assets.add(payload,'supplied-ocr-corrections.json','human-supplied-corrections')
    for target,(unit,raw,changes) in staged.items():
        layout=raw['refinement'];old=layout['markdown'];render_report(layout)
        marker='\n\n### [이미지 문자 — 로컬 OCR, 원본 대조 필요]\n\n'
        if marker not in unit.markdown:raise ConversionError('기존 OCR 본문 범위를 찾지 못했습니다.')
        prefix,tail=unit.markdown.rsplit(marker,1)
        if not tail.startswith(old):raise ConversionError('기존 OCR 본문과 인식 데이터가 일치하지 않습니다.')
        tail=layout['markdown']+tail[len(old):]
        old_asset=unit.metadata['ocr_data']
        raw['accepted_text']=layout['text'];raw['human_corrections']={'actions':changes,'input_asset':correction_asset,'previous_ocr_asset':old_asset,'scope':'사용자 제공 교정. 자동 정확도/시각 검증 인증과 다름.'}
        updated=assets.json('human-corrected-ocr.json',raw,target)
        unit.markdown=prefix+marker+tail.replace(old_asset,updated)
        unit.text=(unit.metadata.get('direct_text','')+'\n'+raw['accepted_text']).strip()
        unit.metadata.update(ocr_data=updated,ocr_text=raw['accepted_text'],ocr_tables=layout['tables'],human_corrections=raw['human_corrections'])
        for item in result.metadata.get('ocr',{}).get('targets',[]):
            if item.get('source_locator')==target:item.update(data=updated,human_corrections=len(changes))
    result.metadata['corrections']={'input_asset':correction_asset,'actions':len(data['actions']),'kind':'human-supplied','original_ocr_preserved':True}
    result.warn('human_ocr_corrections','사용자가 원본 대조 후 제공한 교정 입력을 적용했습니다. 이전 인식·후보·교정 이력과 원본을 함께 보존합니다.')


def write_review(result,assets,source,checks, *, save=True):
    items=[]
    for unit in result.units:
        if not unit.metadata.get('ocr_data'):continue
        raw=json.loads(local_file(assets.root,unit.metadata['ocr_data']).read_text());layout=raw.get('refinement',{})
        base={'target':raw['source_locator'],'unit':unit.id,'image':raw.get('source_image',''),'width':raw.get('width',1),'height':raw.get('height',1)}
        for block in layout.get('blocks',[]):
            if block['kind']=='text':items.append(dict(base,kind='line',line=block['source_observation'],text=block['text'],bbox=block['bbox']))
        for index,table in enumerate(layout.get('tables',[])):
            for cell in table['cells']:
                if cell.get('rowspan',1)>0:items.append(dict(base,kind='cell',table=index,row=cell['row'],column=cell['column'],text=cell['text'],bbox=cell['bbox'],status=cell['status']))
    # A JSON data block cannot close the surrounding HTML script element.
    encoded=json.dumps({'source_sha256':source['sha256'],'items':items},ensure_ascii=False).replace('<','\\u003c')
    e=lambda text:html.escape(str(text),quote=True)
    warnings=''.join('<li>'+e(w['source_locator'])+' · '+e(w['code'])+' · '+e(w['message'])+'</li>' for w in result.warnings)
    references=''
    for unit in result.units:
        image=unit.metadata.get('page_image') or unit.metadata.get('image')
        if image:references+='<details><summary>'+e(unit.label)+' 원본 이미지</summary><img src="'+e(image)+'"></details>'
    equations=''.join('<section><h2>수식 '+str(i+1)+'</h2><pre>'+e(eq['linear_math'])+'</pre><a href="'+e(eq['source_xml'])+'">원본 수식 XML</a><p>미지원 구조: '+e(', '.join(eq.get('unsupported_tags',[])) or '검출 없음')+'</p></section>' for i,eq in enumerate(result.metadata.get('equations',[])))
    references+=equations
    converted=result.metadata.get('legacy_conversion',{})
    if converted:
        reference=converted.get('body_asset') or converted.get('derived')
        references+='<section><h2>로컬 변환 참조본</h2><p><a href="'+e(reference)+'">'+e(converted.get('tool',''))+' 변환 참조본</a></p><p>원본 파일과 함께 대조하는 파생 자료입니다. 원본 화면과의 완전 동일성 인증은 아닙니다.</p></section>'
    if result.metadata.get('hwp_native_preview'):
        references+='<p><a href="'+e(result.metadata['hwp_native_preview']['text_asset'])+'">HWP 내장 텍스트 미리보기</a> — 표·개체의 &lt;&gt; 표식은 원문 글자와 구별합니다.</p>'
    page='''<!doctype html><html lang="ko"><meta charset="utf-8"><title>원문 대조와 OCR 교정</title><style>'''+(Path(__file__).parent/'web/review.css').read_text()+'''</style><h1>원문 대조와 OCR 교정</h1><p>원본과 좌표별 인식 결과를 대조하세요. 바뀐 항목만 교정 JSON으로 내려받아 같은 원본을 다시 변환할 수 있습니다. 원본·기존 인식 데이터는 유지됩니다. 자동 정확도 인증은 아닙니다.</p><p><a href="'''+e(source['path'])+'''">원본 파일</a> · <a href="00_원본복원.md">MD</a> · <a href="03_변환검증보고서.md">검증 보고서</a></p><button id="save">변경 항목의 교정 JSON 받기</button><p id="status"></p><main id="items"></main><details><summary>현재 제한과 미확인</summary><ul>'''+warnings+'''</ul></details>'''+references+'''<script type="application/json" id="data">'''+encoded+'''</script><script>
'''+(Path(__file__).parent/'web/review.js').read_text()+'''
</script></html>'''
    if save:(assets.root/REVIEW).write_text(page,encoding='utf-8')
    result.metadata['review_document']=REVIEW
    return page
