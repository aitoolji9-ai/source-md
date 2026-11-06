import json
from source_preserve_md.cli import main


def test_failed_required_runtime_leaves_no_conversion_output(tmp_path,monkeypatch,capsys):
    source=tmp_path/'source.md';source.write_text('# Keep source')
    def fail(*args,**kwargs):raise RuntimeError('필수 설치 환경 미충족: Swift missing')
    monkeypatch.setattr('source_preserve_md.preflight.require_environment',fail)
    assert main(['convert',str(source),'--ocr','off','--out',str(tmp_path/'out')])==2
    assert not (tmp_path/'out').exists() and source.read_text()=='# Keep source'
    assert json.loads(capsys.readouterr().out)['status']=='failed'


def test_failed_required_runtime_cannot_install_skill(tmp_path,monkeypatch):
    from source_preserve_md.integration import install_skill
    from source_preserve_md.model import ConversionError
    import pytest
    def fail(*args,**kwargs):raise RuntimeError('필수 설치 환경 미충족: OCR failure')
    monkeypatch.setattr('source_preserve_md.preflight.require_environment',fail)
    with pytest.raises(ConversionError,match='OCR failure'):install_skill(tmp_path/'skill')
    assert not (tmp_path/'skill').exists()
