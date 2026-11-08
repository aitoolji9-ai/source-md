"""Durable PDF page OCR: text first, bounded page work, source-bound resume.

No fragment PDFs, additional model downloads or external OCR calls. Working
checkpoints are verified separately from the published document manifest.
"""
from __future__ import annotations
import copy
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import tempfile
from collections import Counter

from .model import Assets,ConversionError,Extraction,Unit,sha256

SCHEMA=1


def atomic(path,value):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ConversionError("체크포인트 symlink 경로에는 저장하지 않습니다.")
    path.parent.mkdir(parents=True,exist_ok=True)
    def json_value(item):
        import pymupdf
        if isinstance(item,(pymupdf.Rect,pymupdf.Point,pymupdf.Quad)):return list(item)
        return str(item)
    data=(json.dumps(value,ensure_ascii=False,indent=2,default=json_value)+'\n').encode()
    fd,name=tempfile.mkstemp(prefix='.write-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)
    return sha256(data)


def local(root,relative):
    p=(root/relative).resolve()
    if not p.is_relative_to(root) or not p.is_file():raise ConversionError('체크포인트 경로가 없거나 결과 폴더 밖입니다.')
    return p


@contextmanager
def exclusive(root):
    import fcntl
    path=root/'.pdf-job.lock'
    if path.is_symlink():raise ConversionError('PDF 작업 잠금 경로가 올바르지 않습니다.')
    with path.open('a+b') as stream:
        try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise ConversionError('다른 실행이 이 PDF 결과를 처리 중입니다.') from error
        try:yield
        finally:fcntl.flock(stream,fcntl.LOCK_UN)


def options(max_pages,detail):
    if max_pages is not None and (type(max_pages) is not int or not 1<=max_pages<=1500):raise ConversionError('중단할 페이지 작업 수는 1~1500 정수여야 합니다.')
    if detail not in ('text','full'):raise ConversionError('PDF OCR 세부 모드는 text 또는 full이어야 합니다.')


def start_pdf(data,filename,output,*,source_path=None,url=None,max_pages=None,detail='text',progress=None):
    options(max_pages,detail)
    root=Path(output).expanduser().resolve();root.mkdir(parents=True,exist_ok=False)
    (root/'source').mkdir();(root/'source'/filename).write_bytes(data)
    state={'schema_version':SCHEMA,'phase':'initializing','source':{'path':'source/'+filename,'filename':filename,'sha256':sha256(data),'size':len(data),'url':url,'format':'pdf'},
           'original_path':str(source_path) if source_path else None,'captured_at':datetime.now(timezone.utc).isoformat(),
           'detail':detail,'generation':0,'pages':{},'history':[]}
    with exclusive(root):
        atomic(root/'pdf-job.json',state)
        return run(root,state,max_pages,progress)


def resume_pdf(folder,*,max_pages=None,progress=None):
    root=Path(folder).expanduser().resolve()
    if not root.is_dir():raise ConversionError('PDF 재개 결과 폴더가 없습니다.')
    with exclusive(root):
        try:state=json.loads(local(root,'pdf-job.json').read_text())
        except (ValueError,OSError) as error:raise ConversionError('PDF 체크포인트 상태를 읽을 수 없습니다.') from error
        if state.get('schema_version')!=SCHEMA:raise ConversionError('지원하지 않는 PDF 체크포인트 버전입니다.')
        options(max_pages,state.get('detail'))
        return run(root,state,max_pages,progress)


def verify_source(root,state):
    source=local(root,state['source']['path']);data=source.read_bytes()
    if sha256(data)!=state['source']['sha256']:raise ConversionError('보관된 원본 source 해시가 바뀌었습니다.')
    path=state.get('original_path')
    if path and Path(path).is_file() and sha256(Path(path).read_bytes())!=state['source']['sha256']:raise ConversionError('원본 source가 변경되어 PDF 재개를 중단했습니다.')
    return data


def restored(root,state):
    p=local(root,'checkpoint/native.json')
    if sha256(p.read_bytes())!=state['native_sha256']:raise ConversionError('기본 추출 체크포인트 해시가 다릅니다.')
    native=json.loads(p.read_text());structure=native['structure']
    result=Extraction(structure['title'],structure['format'],metadata=structure['metadata'],warnings=native['warnings'])
    result.units=[Unit(**u) for u in structure['units']]
    assets=Assets(root);assets.records=native['assets'];receipts={}
    for number,entry in state['pages'].items():
        try:
            index=int(number)-1
            if not 0<=index<len(result.units) or entry['checkpoint']!=f'checkpoint/pages/{index+1:04d}.json':raise ValueError('page binding')
            path=local(root,entry['checkpoint']);payload=path.read_bytes()
            if sha256(payload)!=entry['sha256']:raise ValueError('hash mismatch')
            receipt=json.loads(payload)
            if receipt['page']!=index+1 or receipt['source_sha256']!=state['source']['sha256']:raise ValueError('source binding')
        except (ValueError,KeyError) as error:raise ConversionError('페이지 체크포인트 손상 또는 원본 연결 불일치입니다.') from error
        receipts[number]=receipt;result.units[index]=Unit(**receipt['unit']);result.warnings.extend(receipt['warnings']);assets.records.extend(receipt['assets'])
    seen=set()
    for record in assets.records:
        if record['path'] in seen:continue
        seen.add(record['path']);path=local(root,record['path'])
        if sha256(path.read_bytes())!=record['sha256']:raise ConversionError('체크포인트의 완료 페이지 자산 해시가 바뀌었습니다.')
    assets.total_bytes=sum(p.stat().st_size for p in (root/'assets').iterdir() if p.is_file())
    return native,result,assets,receipts


def processing(state):
    count=state['total_pages'];pending=[i for i in range(1,count+1) if str(i) not in state['pages']]
    failed=[int(i) for i,e in state['pages'].items() if e['status']=='failed']
    details=[int(i) for i,e in state['pages'].items() if state['detail']=='full' and e['status'] not in ('failed','native') and e.get('detail_status')!='complete']
    return {'complete':not pending and not failed and not details,'primary_complete':not pending and not failed,
            'detail':state['detail'],'total_pages':count,'checkpointed_pages':len(state['pages']),
            'pending_pages':pending,'failed_pages':sorted(failed),'detail_pending_pages':sorted(details),
            'generation':state['generation'],'checkpoint':'pdf-job.json','accuracy_verified':False,
            'interrupted_attempts':len(state.get('interrupted_attempts',[])),
            'requests_in_interrupted_attempts':'unknown' if state.get('interrupted_attempts') else 0}


def publish(root,state,result,assets,receipts):
    from .engine import finish_bundle
    info=processing(state)
    targets=[]
    for number in range(1,state['total_pages']+1):
        receipt=receipts.get(str(number))
        if receipt and receipt.get('ocr'):
            targets.extend(receipt['ocr']['targets'])
        elif receipt is None and result.units[number-1].metadata.get('ocr_candidate'):
            targets.append({'source_locator':f'page:{number}','status':'pending_checkpoint'})
    summary={'mode':'auto','engine':'apple-vision','used':any(t.get('status') in ('recognized','no_additional_text_detected') for t in targets),
             'attempted':bool(state['history']),'requests':sum(e['requests'] for e in state['history']),
             'targets':targets,'accuracy_verified':False,'external_api_calls':0,
             'limits':{'scope':'one PDF page work item; all pages have a primary pass before optional full refinement',
                       'primary_requests_per_page':1,'detail_requests_per_page':256,'seconds_per_page':180,'max_image_edge':4096}}
    result.metadata.update(ocr=summary,pdf_processing=info)
    state['phase']='complete' if info['complete'] else 'checkpointed'
    atomic(root/'pdf-job.json',state)
    result.warnings=[w for w in result.warnings if w['code'] not in ('pdf_checkpoint_pending','ocr_structure_partial')]
    if targets:result.warn('ocr_structure_partial','초기 OCR와 좌표를 보존했습니다. 읽는 순서·표 의미·인식 정확도는 원본 대조가 필요합니다.')
    if not info['complete']:result.warn('pdf_checkpoint_pending','저장된 페이지는 재사용하며 미처리/실패 페이지만 resume 명령으로 이어갑니다.')
    return finish_bundle(result,assets,state['source'],state['captured_at'],summary)


def run(root,state,max_pages,progress):
    from .adapters import extract
    from .ocr import augment_ocr
    from .engine import validate_bundle
    data=verify_source(root,state)
    if state.get('inflight'):
        state.setdefault('interrupted_attempts',[]).append(state.pop('inflight'))
        atomic(root/'pdf-job.json',state)
    def event(phase,page=None):
        if progress:progress({'phase':phase,'page':page,'total_pages':state.get('total_pages'),'checkpointed_pages':len(state['pages']),'requests':sum(e['requests'] for e in state['history'])})
    if state['phase']=='initializing':
        event('native_extract')
        assets=Assets(root);assets.total_bytes=sum(p.stat().st_size for p in (root/'assets').iterdir() if p.is_file())
        result=extract(data,state['source']['filename'],assets)
        import pymupdf
        with pymupdf.open(stream=data,filetype='pdf') as doc:
            for i,page in enumerate(doc):
                result.units[i].metadata.update(raster_image_count=len(page.get_image_info()),ocr_candidate=bool(page.get_image_info()) or not result.units[i].text.strip())
        result.warnings=[w for w in result.warnings if w['code']!='image_only_page']
        state['total_pages']=len(result.units)
        native={'structure':result.structure(),'warnings':result.warnings,'assets':assets.records}
        state['native_sha256']=atomic(root/'checkpoint/native.json',native)
        state['phase']='checkpointed';atomic(root/'pdf-job.json',state);event('native_saved')
    native,result,assets,receipts=restored(root,state)
    # A completed replay is read-only and makes no OCR requests.
    if processing(state)['complete'] and (root/'manifest.json').is_file():
        manifest=json.loads((root/'manifest.json').read_text())
        if manifest.get('processing',{}).get('generation')==state['generation'] and validate_bundle(root)['passed']:
            from .engine import PRIMARY
            checks=json.loads((root/'checks.json').read_text())['checks']
            return {'output':str(root),'markdown':str(root/PRIMARY),'format':'pdf','title':result.title,'units':len(result.units),'warnings':manifest['unresolved'],'integrity':validate_bundle(root),'machine_checks':{'passed':sum(c['passed'] for c in checks),'total':len(checks)},'status':'partial','ai_calls':0,'generative_ai_calls':0,'ocr_used':manifest['ocr_used'],'ocr':manifest['ocr'],'processing':processing(state)}
    worked=0
    def commit(index,summary,status,*,detail_status=None,basic_unit=None):
        nonlocal worked
        number=str(index+1);old=receipts.get(number,{});before_native=native['structure']['units'][index]
        warnings=[w for w in result.warnings if w['source_locator']==f'page:{index+1}' and w['code'].startswith('ocr')]
        record={'page':index+1,'source_sha256':state['source']['sha256'],'unit':asdict(result.units[index]),
                'basic_unit':basic_unit or old.get('basic_unit') or asdict(result.units[index]),
                'assets':old.get('assets',[])+assets.records[asset_start:], 'warnings':warnings,'ocr':summary}
        relative=f'checkpoint/pages/{index+1:04d}.json';digest=atomic(root/relative,record)
        receipts[number]=record;state['pages'][number]={'checkpoint':relative,'sha256':digest,'status':status,'detail_status':detail_status}
        state.pop('inflight',None)
        state['generation']+=1;state['history'].append({'page':index+1,'phase':'detail' if detail_status is not None else 'primary','requests':summary['requests'] if summary else 0,'status':status})
        atomic(root/'pdf-job.json',state);worked+=1;event('page_saved',index+1)
    for index in range(state['total_pages']):
        entry=state['pages'].get(str(index+1))
        if entry and entry['status']!='failed':continue
        if max_pages is not None and worked>=max_pages:break
        result.units[index]=Unit(**copy.deepcopy(native['structure']['units'][index]))
        result.warnings=[w for w in result.warnings if not(w['source_locator']==f'page:{index+1}' and w['code'].startswith('ocr'))]
        asset_start=len(assets.records)
        if not result.units[index].metadata['ocr_candidate']:
            commit(index,None,'native');continue
        state['inflight']={'page':index+1,'phase':'primary','maximum_requests':1}
        atomic(root/'pdf-job.json',state)
        summary=augment_ocr(result,assets,'auto',data,detail='text',page_indices=[index])
        status='failed' if any(t['status']=='failed' for t in summary['targets']) else summary['targets'][0]['status']
        commit(index,summary,status)
    if not processing(state)['pending_pages'] and not processing(state)['failed_pages'] and state['detail']=='full':
        for index in range(state['total_pages']):
            entry=state['pages'][str(index+1)]
            if entry['status']=='native' or entry.get('detail_status')=='complete':continue
            if max_pages is not None and worked>=max_pages:break
            receipt=receipts[str(index+1)];basic=receipt['basic_unit'];raw=json.loads(local(root,basic['metadata']['ocr_data']).read_text()) if basic['metadata'].get('ocr_data') else None
            if raw is None:
                entry['detail_status']='complete';atomic(root/'pdf-job.json',state);continue
            result.units[index]=Unit(**copy.deepcopy(native['structure']['units'][index]))
            result.warnings=[w for w in result.warnings if not(w['source_locator']==f'page:{index+1}' and w['code'].startswith('ocr'))]
            asset_start=len(assets.records)
            state['inflight']={'page':index+1,'phase':'detail','maximum_requests':256}
            atomic(root/'pdf-job.json',state)
            summary=augment_ocr(result,assets,'auto',data,detail='full',page_indices=[index],initials={f'page:{index+1}':raw})
            incomplete=any(w['source_locator']==f'page:{index+1}' and w['code'] in ('ocr_failed','ocr_limit','ocr_refinement_failed') for w in result.warnings)
            if any(t['status']=='failed' for t in summary['targets']):
                result.units[index]=Unit(**copy.deepcopy(basic))
                summary['targets']=copy.deepcopy(receipt['ocr']['targets'])
                for target in summary['targets']:target['detail_status']='failed_keep_primary'
                summary['used']=True
            commit(index,summary,entry['status'],detail_status='incomplete' if incomplete else 'complete',basic_unit=basic)
    verify_source(root,state)
    return publish(root,state,result,assets,receipts)


def status_pdf(folder):
    """Read an atomic progress snapshot without OCR or changing the job."""
    root=Path(folder).expanduser().resolve()
    state=json.loads(local(root,'pdf-job.json').read_text())
    if state.get('schema_version')!=SCHEMA:raise ConversionError('지원하지 않는 PDF 작업 버전입니다.')
    import fcntl
    active=False;lock=local(root,'.pdf-job.lock')
    with lock.open('rb') as stream:
        try:fcntl.flock(stream,fcntl.LOCK_SH|fcntl.LOCK_NB)
        except BlockingIOError:active=True
        else:fcntl.flock(stream,fcntl.LOCK_UN)
    if state['phase']=='initializing':
        return {'phase':'initializing','writer_active':active,'complete':False,'source_sha256':state['source']['sha256'],'checkpointed_pages':0,'total_pages':None,'pending_count':None}
    info=processing(state)
    return {'phase':state['phase'],'writer_active':active,'complete':info['complete'],
            'checkpointed_pages':info['checkpointed_pages'],'total_pages':info['total_pages'],
            'pending_count':len(info['pending_pages']),'failed_count':len(info['failed_pages']),
            'failed_pages':info['failed_pages'][:20],'failed_pages_truncated':len(info['failed_pages'])>20,
            'detail_pending_count':len(info['detail_pending_pages']),
            'committed_requests':sum(e['requests'] for e in state['history']),
            'source_sha256':state['source']['sha256'],'accuracy_verified':False}


def cli_summary(result):
    summary=dict(result)
    counts=Counter(w['code'] for w in result['warnings']);representatives={}
    for w in result['warnings']:representatives.setdefault(w['code'],w)
    summary['warnings']=list(representatives.values());summary['warning_counts']=dict(counts)
    summary['ocr']={k:v for k,v in result['ocr'].items() if k!='targets'}
    summary['ocr']['target_counts']=dict(Counter(t['status'] for t in result['ocr']['targets']))
    summary['processing']=dict(result['processing'])
    for key in ('pending_pages','failed_pages','detail_pending_pages'):
        values=result['processing'][key];summary['processing'][key+'_count']=len(values);summary['processing'][key]=values[:20]
    summary['details']='Full page observations, warnings and checkpoint receipts are in manifest.json, structure.json and pdf-job.json.'
    return summary


def inventory_pdf(folder):
    """Inspect only original PDFs immediately in the requested directory."""
    import pymupdf
    from .model import MAX_INPUT
    root=Path(folder).expanduser().resolve()
    if not root.is_dir():raise ConversionError('PDF 원본 폴더가 없습니다.')
    files=[]
    for path in sorted(root.iterdir()):
        if path.suffix.lower()!='.pdf' or not path.is_file() or path.is_symlink():continue
        size=path.stat().st_size
        entry={'path':str(path),'bytes':size}
        if size>MAX_INPUT:entry['error']='input_exceeds_100MB'
        else:
            entry['sha256']=sha256(path.read_bytes())
            try:
                with pymupdf.open(path) as pdf:entry.update(pages=len(pdf),password_required=pdf.needs_pass)
            except Exception as error:entry['error']=str(error)[:200]
        files.append(entry)
    return {'directory':str(root),'recursive':False,'count':len(files),'files':files,'ocr_calls':0}
