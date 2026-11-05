import json
import subprocess
import sys
from pathlib import Path

import pytest

from source_preserve_md import __version__
from source_preserve_md.engine import PRIMARY, convert, validate_bundle
from source_preserve_md.integration import doctor, install_skill
from source_preserve_md.model import ConversionError
from source_preserve_md.server import create_app


def test_installed_skill_converts_relative_input_from_another_directory(tmp_path):
    target = tmp_path / 'skills with spaces/source-preserve-md'
    installed = install_skill(target)
    assert installed['version'] == __version__
    cwd = tmp_path / 'unrelated 작업'; cwd.mkdir()
    original = b'---\ntitle: Exact\n---\n\n# Literal\n\n`token`\r\n'
    (cwd/'input.md').write_bytes(original)
    ran = subprocess.run([sys.executable, str(target/'scripts/run.py'), 'convert', 'input.md',
                          '--out', 'output', '--ocr', 'off'], cwd=cwd, capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr + ran.stdout
    assert (cwd/'output'/PRIMARY).read_bytes() == original
    assert validate_bundle(cwd/'output')['passed']
    manifest = json.loads((cwd/'output/manifest.json').read_text())
    assert manifest['producer'] == 'source-preserve-md/' + __version__


def test_skill_installer_never_overwrites_existing_or_dangling_target(tmp_path):
    target = tmp_path/'existing'; target.mkdir(); (target/'mine.txt').write_text('keep')
    with pytest.raises(ConversionError, match='존재'):
        install_skill(target)
    assert (target/'mine.txt').read_text() == 'keep'
    dangling = tmp_path/'dangling'; dangling.symlink_to(tmp_path/'absent')
    with pytest.raises(ConversionError, match='존재'):
        install_skill(dangling)
    assert not (tmp_path/'absent').exists()


def test_skill_root_override_is_explicit_and_invalid_root_does_not_fall_back(tmp_path):
    import os
    target = tmp_path/'skill'; install_skill(target)
    env = dict(os.environ, SOURCE_PRESERVE_MD_ROOT=str(tmp_path/'missing'))
    ran = subprocess.run([sys.executable,str(target/'scripts/run.py'),'formats'],env=env,capture_output=True,text=True)
    assert ran.returncode == 2 and '엔진' in ran.stderr


def test_doctor_distinguishes_prerequisites_from_actual_execution():
    result = doctor()
    assert result['version'] == __version__ and result['skill_files_available']
    assert result['generative_ai_calls'] == 0
    assert result['ocr']['actual_recognition_tested'] is False
    assert result['legacy_office']['conversion_tested'] is False
    assert result['dynamic_web']['browser_launch_tested'] is False


def test_local_download_rejects_symlink_to_outside_bundle(tmp_path):
    outputs = tmp_path/'outputs'; folder = outputs/'job'
    convert('input.txt',folder,data=b'Public result',ocr='off')
    secret = tmp_path/'outside.txt'; secret.write_bytes(b'MUST_NOT_LEAK')
    (folder/'source/leak.txt').symlink_to(secret)
    client = create_app(outputs).test_client()
    assert client.get('/api/jobs/job/files/source/input.txt').data == b'Public result'
    denied = client.get('/api/jobs/job/files/source/leak.txt')
    assert denied.status_code == 404 and b'MUST_NOT_LEAK' not in denied.data


@pytest.mark.parametrize('payload', [b'{}', b'[]', b'{', b'null', b'{"schema_version":1,"source":[]}'])
def test_malformed_manifest_is_an_explicit_validation_failure(tmp_path,payload):
    from source_preserve_md.cli import main
    (tmp_path/'manifest.json').write_bytes(payload)
    result=validate_bundle(tmp_path)
    assert result['passed'] is False and result['failures'][0]['reason']=='invalid_manifest'
    assert main(['validate',str(tmp_path)])==2


def test_manifest_bad_range_types_and_external_manifest_are_rejected(tmp_path):
    folder=tmp_path/'bundle';convert('source.txt',folder,data=b'content',ocr='off')
    manifest=json.loads((folder/'manifest.json').read_text());manifest['markdown_ranges'][0]['start']=True
    (folder/'manifest.json').write_text(json.dumps(manifest))
    assert not validate_bundle(folder)['passed']
    manifest['markdown_ranges'][0]['start']=0;manifest['schema_version']=True
    (folder/'manifest.json').write_text(json.dumps(manifest))
    assert not validate_bundle(folder)['passed']
    (folder/'manifest.json').unlink();outside=tmp_path/'outside.json';outside.write_text('{}');(folder/'manifest.json').symlink_to(outside)
    assert not validate_bundle(folder)['passed']


@pytest.mark.parametrize('version',[True,1.0])
def test_snapshot_and_correction_versions_reject_non_integer_one(version):
    import hashlib
    from source_preserve_md.dom_snapshot import load_snapshot
    from source_preserve_md.review import load_corrections
    source=b'original';payload={'schema_version':version,'source_sha256':hashlib.sha256(source).hexdigest()}
    with pytest.raises(ConversionError,match='schema_version'):
        load_snapshot(payload,source)
    with pytest.raises(ConversionError,match='버전'):
        load_corrections(payload,source)
