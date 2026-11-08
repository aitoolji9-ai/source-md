import json

import pymupdf
import pytest

from source_preserve_md.cli import main
from source_preserve_md.model import ConversionError
from source_preserve_md.engine import validate_bundle


def books(path, count=2):
    path.mkdir()
    for n in range(count):
        pdf=pymupdf.open()
        for i in range(3):
            page=pdf.new_page();page.insert_text((40,60),f'BOOK {n+1} PAGE {i+1}')
        pdf.save(path/f'{n+1}.pdf');pdf.close()


def test_one_batch_checkpoint_resumes_all_books_without_restarting_completed_pages(tmp_path):
    from source_preserve_md.pdf_batch import start_batch,resume_batch,status_batch
    inputs=tmp_path/'inputs';books(inputs);root=tmp_path/'batch'
    first=start_batch(inputs,root,max_pages=2)
    assert not first['complete'] and first['checkpointed_pages']==2
    before=(root/'01/checkpoint/pages/0001.json').read_bytes()
    final=resume_batch(root)
    assert final['complete'] and final['books_complete']==2 and final['checkpointed_pages']==6
    assert before==(root/'01/checkpoint/pages/0001.json').read_bytes()
    for n in ['01','02']:
        assert validate_bundle(root/n)['passed']
        state=json.loads((root/n/'pdf-job.json').read_text())
        assert [e['page'] for e in state['history']]==[1,2,3]
    snapshot={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert status_batch(root)['complete'] and resume_batch(root)['complete']
    assert snapshot=={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_batch_plan_rejects_changed_original_before_any_new_page(tmp_path):
    from source_preserve_md.pdf_batch import start_batch,resume_batch
    inputs=tmp_path/'inputs';books(inputs);root=tmp_path/'out';start_batch(inputs,root,max_pages=1)
    original=inputs/'2.pdf';original.write_bytes(original.read_bytes()+b'changed')
    with pytest.raises(ConversionError,match='원본|source'):resume_batch(root)
    assert len(json.loads((root/'01/pdf-job.json').read_text())['pages'])==1


def test_batch_status_detects_active_writer_without_changing_files(tmp_path):
    from source_preserve_md.pdf_batch import start_batch,status_batch,resume_batch
    from source_preserve_md.pdf_job import exclusive
    inputs=tmp_path/'inputs';books(inputs);root=tmp_path/'out';start_batch(inputs,root,max_pages=1)
    with exclusive(root):
        assert status_batch(root)['writer_active']
        with pytest.raises(ConversionError,match='다른 실행'):resume_batch(root)


def test_cli_batch_status_resume_share_one_root(tmp_path,capsys):
    inputs=tmp_path/'inputs';books(inputs);root=tmp_path/'out'
    assert main(['batch',str(inputs),'--out',str(root),'--max-pages','2'])==3
    assert not json.loads(capsys.readouterr().out)['complete']
    assert main(['status',str(root)])==0
    assert json.loads(capsys.readouterr().out)['total_pages']==6
    assert main(['resume',str(root)])==0
    assert json.loads(capsys.readouterr().out)['complete']


def test_one_book_error_does_not_erase_later_work_or_repeat_success(tmp_path,monkeypatch):
    from source_preserve_md import pdf_batch
    inputs=tmp_path/'inputs';books(inputs);root=tmp_path/'out'
    original=pdf_batch.start_pdf
    def fail_first(data,filename,*args,**kwargs):
        if filename=='1.pdf':raise ConversionError('fixture input unavailable')
        return original(data,filename,*args,**kwargs)
    monkeypatch.setattr(pdf_batch,'start_pdf',fail_first)
    first=pdf_batch.start_batch(inputs,root)
    assert not first['complete'] and first['books'][0]['error'] and first['books'][1]['complete']
    second_bytes=(root/'02/00_원본복원.md').read_bytes()
    monkeypatch.setattr(pdf_batch,'start_pdf',original)
    assert pdf_batch.resume_batch(root)['complete']
    assert second_bytes==(root/'02/00_원본복원.md').read_bytes()


def test_batch_rejects_changed_plan_or_child_binding(tmp_path):
    from source_preserve_md.pdf_batch import start_batch,resume_batch
    inputs=tmp_path/'inputs';books(inputs);root=tmp_path/'out';start_batch(inputs,root,max_pages=1)
    path=root/'batch-job.json';before=path.read_bytes();plan=json.loads(before)
    plan['books'][0]['output']='../outside';path.write_text(json.dumps(plan))
    with pytest.raises(ConversionError,match='계획 해시'):resume_batch(root)
    path.write_bytes(before);child=root/'01/pdf-job.json';state=json.loads(child.read_text());state['source']['sha256']='0'*64;child.write_text(json.dumps(state))
    with pytest.raises(ConversionError,match='원본 계획'):resume_batch(root)
