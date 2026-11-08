import hashlib
import io
import json
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from source_preserve_md.engine import convert,validate_bundle,PRIMARY
from source_preserve_md.model import ConversionError


def image_pdf(path, count=3):
    doc=pymupdf.open()
    for i in range(1,count+1):
        page=doc.new_page(width=300,height=400)
        buffer=io.BytesIO();Image.new('RGB',(300,400),(i,100,200)).save(buffer,'PNG')
        page.insert_image(page.rect,stream=buffer.getvalue())
    doc.save(path);doc.close()


def fake_ocr(monkeypatch):
    from source_preserve_md import ocr
    calls=[]
    def recognize(path,**kwargs):
        with Image.open(path) as image:page=image.getpixel((5,5))[0]
        calls.append(page)
        return {'engine':'test-double','observations':[{'text':f'PAGE MARKER {page}', 'confidence':1,'bbox':[.1,.1,.8,.2]}]}
    monkeypatch.setattr(ocr,'recognize',recognize)
    return calls


def test_pdf_checkpoint_resume_retains_completed_pages_and_merges_once(tmp_path,monkeypatch):
    calls=fake_ocr(monkeypatch);source=tmp_path/'book.pdf';image_pdf(source)
    root=tmp_path/'result';first=convert(source,root,pdf_max_pages=1)
    assert first['processing']['complete'] is False and calls==[1]
    assert validate_bundle(root)['passed']
    from source_preserve_md.pdf_job import resume_pdf
    final=resume_pdf(root)
    assert final['processing']['complete'] and calls==[1,2,3]
    text=(root/PRIMARY).read_text()
    assert text.count('PAGE MARKER 1')==1 and text.count('PAGE MARKER 2')==1 and text.count('PAGE MARKER 3')==1
    assert validate_bundle(root)['passed']
    before=(root/PRIMARY).read_bytes();resume_pdf(root)
    assert calls==[1,2,3] and (root/PRIMARY).read_bytes()==before
    assert len(list((root/'source').glob('*.pdf')))==1


def test_pdf_primary_pass_is_not_starved_by_document_request_limit(tmp_path,monkeypatch):
    from source_preserve_md import ocr
    calls=fake_ocr(monkeypatch);monkeypatch.setattr(ocr,'MAX_REQUESTS',1);monkeypatch.setattr(ocr,'MAX_TARGETS',1)
    source=tmp_path/'book.pdf';image_pdf(source,4)
    result=convert(source,tmp_path/'out')
    assert result['processing']['complete'] and calls==[1,2,3,4]
    assert result['ocr']['requests']==4
    assert not any(w['code'] in ('ocr_limit','ocr_failed') for w in result['warnings'])


def test_pdf_resume_rejects_changed_original_and_checkpoint(tmp_path,monkeypatch):
    fake_ocr(monkeypatch);source=tmp_path/'book.pdf';image_pdf(source)
    root=tmp_path/'out';convert(source,root,pdf_max_pages=1)
    from source_preserve_md.pdf_job import resume_pdf
    original=source.read_bytes();source.write_bytes(original+b'changed')
    with pytest.raises(ConversionError,match='원본|source'):resume_pdf(root)
    source.write_bytes(original)
    state=json.loads((root/'pdf-job.json').read_text());relative=state['pages']['1']['checkpoint']
    checkpoint=root/relative;checkpoint.write_bytes(checkpoint.read_bytes()+b'changed')
    with pytest.raises(ConversionError,match='체크포인트|checkpoint'):resume_pdf(root)


def test_pdf_failed_page_does_not_block_later_pages_and_resume_retries_only_failure(tmp_path,monkeypatch):
    from source_preserve_md import ocr
    calls=fake_ocr(monkeypatch);original=ocr.recognize;failed=False
    def sometimes(path,**kwargs):
        nonlocal failed
        with Image.open(path) as image:index=image.getpixel((5,5))[0]
        if index==2 and not failed:failed=True;raise RuntimeError('temporary native OCR failure')
        return original(path,**kwargs)
    monkeypatch.setattr(ocr,'recognize',sometimes)
    source=tmp_path/'book.pdf';image_pdf(source)
    root=tmp_path/'out';result=convert(source,root)
    assert not result['processing']['complete'] and calls==[1,3]
    from source_preserve_md.pdf_job import resume_pdf,status_pdf
    assert status_pdf(root)['failed_count']==1
    assert resume_pdf(root)['processing']['complete'] and calls==[1,3,2]
    assert validate_bundle(root)['passed']


def test_collapse_prone_false_grid_keeps_initial_text_without_cell_errors(monkeypatch):
    from source_preserve_md import ocr_refine
    image=Image.new('RGB',(300,400),'white')
    table={'bbox':[10,10,110,26],'row_count':2,'column_count':2,'cells':[
        {'row':r,'column':c,'rowspan':1,'colspan':1,'bbox':[10+c*50,10+r*8,60+c*50,18+r*8]}
        for r in range(2) for c in range(2)]}
    monkeypatch.setattr(ocr_refine,'detect_tables',lambda image:[table])
    obs=[{'text':'ORIGINAL TEXT','confidence':1,'bbox':[.05,.025,.3,.04]}]
    report=ocr_refine.refine_layout(image,obs,lambda *a: {'observations':[]})
    assert report['text']=='ORIGINAL TEXT' and not report['issues']
    assert report['rejected_tables'] and not report['tables']


def test_pdf_links_and_coordinates_survive_native_checkpoint(tmp_path):
    source=tmp_path/'linked.pdf';doc=pymupdf.open();page=doc.new_page()
    page.insert_text((40,60),'Native linked text')
    page.insert_link({'kind':pymupdf.LINK_URI,'from':pymupdf.Rect(30,40,160,80),'uri':'https://example.com/'})
    doc.save(source);doc.close()
    result=convert(source,tmp_path/'out')
    assert result['processing']['complete'] and validate_bundle(tmp_path/'out')['passed']
    unit=json.loads((tmp_path/'out/structure.json').read_text())['units'][0]
    assert unit['metadata']['links'][0]['uri']=='https://example.com/'
    assert unit['metadata']['links'][0]['from']==[30,40,160,80]


def test_pdf_page_text_survives_unexpected_layout_failure(tmp_path,monkeypatch):
    from source_preserve_md import ocr_refine
    fake_ocr(monkeypatch)
    # Simulate a geometry routine failing after native OCR already succeeded.
    def broken(*args,**kwargs):raise ValueError('synthetic grid geometry failure')
    table={'bbox':[10,100,70,160],'row_count':2,'column_count':2,'cells':[{'row':r,'column':c,'rowspan':1,'colspan':1,'bbox':[10+c*30,100+r*30,40+c*30,130+r*30]} for r in range(2) for c in range(2)]}
    monkeypatch.setattr(ocr_refine,'detect_tables',lambda image:[table])
    monkeypatch.setattr(ocr_refine,'_has_ink',broken)
    source=tmp_path/'book.pdf';image_pdf(source,1)
    result=convert(source,tmp_path/'out')
    assert result['processing']['complete']
    assert 'PAGE MARKER 1' in (tmp_path/'out'/PRIMARY).read_text()
    assert any(w['code']=='ocr_layout_fallback' for w in result['warnings'])


def test_pdf_resume_lock_blocks_a_second_writer(tmp_path,monkeypatch):
    fake_ocr(monkeypatch);source=tmp_path/'book.pdf';image_pdf(source)
    root=tmp_path/'out';convert(source,root,pdf_max_pages=1)
    from source_preserve_md.pdf_job import resume_pdf,exclusive
    with exclusive(root):
        with pytest.raises(ConversionError,match='다른 실행'):resume_pdf(root)
    assert resume_pdf(root)['processing']['complete']


def test_full_pdf_does_all_initial_pages_before_crop_refinement(tmp_path,monkeypatch):
    from source_preserve_md import ocr_refine
    calls=fake_ocr(monkeypatch);seen=[]
    original=ocr_refine.refine_layout
    def checked(image,observations,call,**kwargs):
        assert calls==[1,2,3]
        seen.append(True)
        return original(image,observations,call,**kwargs)
    monkeypatch.setattr(ocr_refine,'refine_layout',checked)
    source=tmp_path/'book.pdf';image_pdf(source)
    result=convert(source,tmp_path/'out',ocr_detail='full')
    assert result['processing']['complete'] and len(seen)==3 and calls==[1,2,3]


def test_status_is_readonly_and_reports_live_writer_and_saved_progress(tmp_path,monkeypatch,capsys):
    from source_preserve_md.cli import main
    from source_preserve_md.pdf_job import exclusive
    calls=fake_ocr(monkeypatch);source=tmp_path/'book.pdf';image_pdf(source)
    root=tmp_path/'out';convert(source,root,pdf_max_pages=1)
    before=(root/'pdf-job.json').read_bytes()
    with exclusive(root):
        assert main(['status',str(root)])==0
        status=json.loads(capsys.readouterr().out)
        assert status['writer_active'] and status['checkpointed_pages']==1 and status['pending_count']==2
    assert before==(root/'pdf-job.json').read_bytes() and calls==[1]


def test_pdf_cli_response_is_compact_and_points_to_full_artifacts(tmp_path,monkeypatch,capsys):
    from source_preserve_md.cli import main
    calls=fake_ocr(monkeypatch);source=tmp_path/'book.pdf';image_pdf(source,12)
    root=tmp_path/'out';convert(source,root)
    assert main(['resume',str(root)])==0
    out=capsys.readouterr().out;data=json.loads(out)
    assert len(out)<5000
    assert data['warning_counts']['ocr_review_required']==12
    assert data['ocr']['target_counts']['recognized']==12 and data['processing']['complete']
    assert calls==list(range(1,13))


def test_inventory_lists_original_top_level_pdfs_without_ocr_or_output_copies(tmp_path,monkeypatch,capsys):
    from source_preserve_md.cli import main
    source=tmp_path/'original.pdf';image_pdf(source,2)
    nested=tmp_path/'old-results';nested.mkdir();image_pdf(nested/'copied.pdf',1)
    calls=fake_ocr(monkeypatch)
    assert main(['inventory',str(tmp_path)])==0
    report=json.loads(capsys.readouterr().out)
    assert report['count']==1 and report['files'][0]['pages']==2
    assert report['files'][0]['sha256']==hashlib.sha256(source.read_bytes()).hexdigest() and not calls


def test_full_refinement_failure_keeps_primary_and_retries_no_initial_ocr(tmp_path,monkeypatch):
    from source_preserve_md import ocr_refine
    from source_preserve_md.pdf_job import resume_pdf
    calls=fake_ocr(monkeypatch);source=tmp_path/'book.pdf';image_pdf(source,1)
    original=ocr_refine.refine_layout
    def broken(*args,**kwargs):raise RuntimeError('detail-only geometry failure')
    monkeypatch.setattr(ocr_refine,'refine_layout',broken)
    root=tmp_path/'out';result=convert(source,root,ocr_detail='full')
    assert result['processing']['primary_complete'] and not result['processing']['complete']
    assert 'PAGE MARKER 1' in (root/PRIMARY).read_text() and validate_bundle(root)['passed']
    monkeypatch.setattr(ocr_refine,'refine_layout',original)
    assert resume_pdf(root)['processing']['complete'] and calls==[1]


def test_pdf_with_a_leading_comment_still_gets_a_resumable_job(tmp_path):
    source=tmp_path/'prefixed.pdf';doc=pymupdf.open();doc.new_page().insert_text((40,60),'PRESERVED PREFIX PDF')
    source.write_bytes(b'% scanner comment\n'+doc.tobytes());doc.close()
    result=convert(source,tmp_path/'out')
    assert result['processing']['complete'] and (tmp_path/'out/pdf-job.json').is_file()
    assert (tmp_path/'out/source/prefixed.pdf').read_bytes()==source.read_bytes()


def test_non_pdf_asset_named_checkpoint_remains_registered_and_tamper_detected(tmp_path):
    source=tmp_path/'original';source.mkdir();(source/'checkpoint').mkdir()
    Image.new('RGB',(12,12),'blue').save(source/'checkpoint/picture.png')
    md=source/'note.md';md.write_text('![original](checkpoint/picture.png)\n')
    root=tmp_path/'out';convert(md,root,ocr='off')
    manifest=json.loads((root/'manifest.json').read_text())
    assert 'checkpoint/picture.png' in manifest['artifacts']
    (root/'checkpoint/picture.png').write_bytes(b'tampered')
    assert not validate_bundle(root)['passed']
