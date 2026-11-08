"""One finite PDF-directory job; its plan and page checkpoints outlive chat turns."""
from pathlib import Path
import json

from .model import ConversionError,sha256
from .pdf_job import atomic,exclusive,inventory_pdf,options,resume_pdf,start_pdf,status_pdf


def load(root):
    path=root/'batch-job.json'
    if path.is_symlink():raise ConversionError('일괄 작업 체크포인트 symlink를 허용하지 않습니다.')
    state=json.loads(path.read_text())
    if state.get('schema_version')!=1 or not isinstance(state.get('books'),list) or not state['books']:
        raise ConversionError('올바른 PDF 일괄 작업 계획이 아닙니다.')
    options(None,state.get('detail'))
    plan=state['books']
    expected=sha256(json.dumps(plan,ensure_ascii=False,sort_keys=True).encode())
    if state.get('plan_sha256')!=expected:raise ConversionError('일괄 작업 원본 계획 해시가 바뀌었습니다.')
    for n,book in enumerate(plan,1):
        if book.get('output')!=f'{n:02d}' or not Path(book['path']).is_absolute():
            raise ConversionError('일괄 작업 원본/출력 경로가 올바르지 않습니다.')
        target=root/book['output']
        if target.is_symlink():raise ConversionError('책 결과 symlink 경로를 허용하지 않습니다.')
    return state


def writer_active(root):
    import fcntl
    path=root/'.pdf-job.lock'
    if not path.exists():return False
    if path.is_symlink():raise ConversionError('일괄 작업 잠금 경로가 올바르지 않습니다.')
    with path.open('rb') as stream:
        try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return True
        finally:fcntl.flock(stream,fcntl.LOCK_UN)
    return False


def status_batch(folder):
    root=Path(folder).expanduser().resolve();state=load(root);rows=[]
    errors={}
    if (root/'batch-errors.json').is_file():errors=json.loads((root/'batch-errors.json').read_text())
    for book in state['books']:
        target=root/book['output']
        row={'book':book['output'],'output':str(target),'total_pages':book['pages'],
             'complete':False,'checkpointed_pages':0,'committed_requests':0,'writer_active':False}
        if (target/'pdf-job.json').is_file():
            live=status_pdf(target)
            for k in ('complete','checkpointed_pages','committed_requests','writer_active','failed_count'):row[k]=live.get(k,0)
        if book['output'] in errors:row['error']=errors[book['output']]
        rows.append(row)
    return {'kind':'pdf_batch','output':str(root),'complete':all(r['complete'] and not r.get('error') for r in rows),
            'writer_active':writer_active(root) or any(r['writer_active'] for r in rows),
            'books_complete':sum(r['complete'] and not r.get('error') for r in rows),'books_total':len(rows),
            'total_pages':sum(r['total_pages'] for r in rows),'checkpointed_pages':sum(r['checkpointed_pages'] for r in rows),
            'committed_requests':sum(r['committed_requests'] for r in rows),'books':rows,'accuracy_verified':False}


def start_batch(folder,output,*,max_pages=None,detail='text',progress=None):
    options(max_pages,detail)
    inventory=inventory_pdf(folder)
    if not inventory['files']:raise ConversionError('지정 폴더 바로 아래에 원본 PDF가 없습니다.')
    for book in inventory['files']:
        if book.get('error') or book.get('password_required') or not 1<=book.get('pages',0)<=1500:
            raise ConversionError('처리할 수 없는 원본 PDF가 있습니다: '+book['path'])
    root=Path(output).expanduser().resolve();root.mkdir(parents=True,exist_ok=False)
    plan=[{'path':b['path'],'sha256':b['sha256'],'pages':b['pages'],'output':f'{n:02d}'} for n,b in enumerate(inventory['files'],1)]
    state={'schema_version':1,'detail':detail,'books':plan,
           'plan_sha256':sha256(json.dumps(plan,ensure_ascii=False,sort_keys=True).encode())}
    with exclusive(root):
        atomic(root/'batch-job.json',state)
        return run(root,state,max_pages,progress)


def resume_batch(folder,*,max_pages=None,progress=None):
    root=Path(folder).expanduser().resolve();state=load(root);options(max_pages,state['detail'])
    with exclusive(root):return run(root,state,max_pages,progress)


def verify_plan(root,state):
    # Check every original before advancing any book. Never silently mix a new input.
    for book in state['books']:
        source=Path(book['path']);target=root/book['output']
        if source.is_file():
            if sha256(source.read_bytes())!=book['sha256']:raise ConversionError('일괄 작업 원본 source가 변경되었습니다: '+str(source))
        elif not (target/'pdf-job.json').is_file():raise ConversionError('미처리 원본 source가 없습니다: '+str(source))
        if (target/'pdf-job.json').is_file():
            child=json.loads((target/'pdf-job.json').read_text())
            if child['source']['sha256']!=book['sha256'] or child['detail']!=state['detail']:
                raise ConversionError('책 체크포인트와 일괄 작업 원본 계획이 다릅니다.')


def run(root,state,max_pages,progress):
    verify_plan(root,state);worked=0
    error_path=root/'batch-errors.json'
    old=json.loads(error_path.read_text()) if error_path.is_file() else {}
    errors=dict(old)
    for book in state['books']:
        target=root/book['output'];before=0;saved=target/'pdf-job.json'
        if saved.is_file():before=len(json.loads(saved.read_text())['history'])
        remaining=None if max_pages is None else max_pages-worked
        if remaining is not None and remaining<=0:break
        errors.pop(book['output'],None)
        def event(value):
            if progress:progress({'book':book['output'],**value})
        try:
            if saved.is_file():result=resume_pdf(target,max_pages=remaining,progress=event)
            else:
                source=Path(book['path']);result=start_pdf(source.read_bytes(),source.name,target,
                    source_path=source,max_pages=remaining,detail=state['detail'],progress=event)
            if not result['integrity']['passed'] or result['machine_checks']['passed']!=result['machine_checks']['total']:
                errors[book['output']]='책 결과 무결성/내용 자동 검사 실패'
            elif result['processing']['failed_pages'] or result['processing']['detail_pending_pages']:
                errors[book['output']]='실패한 페이지/심화 작업이 남아 있습니다.'
        except (ConversionError,OSError,ValueError,RuntimeError) as error:
            errors[book['output']]=str(error)[:500]
        finally:
            if saved.is_file():worked+=len(json.loads(saved.read_text())['history'])-before
        # Another book still gets its one finite attempt; no model polling/retry loop.
    if errors!=old:atomic(error_path,errors)
    summary=status_batch(root);summary['writer_active']=False
    return summary
