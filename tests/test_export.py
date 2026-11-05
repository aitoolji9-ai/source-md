"""Actual sharing export must not pick up added local/private test documents."""
import importlib.util
from pathlib import Path


def test_export_ignores_private_history_binding_and_unregistered_binary(tmp_path):
    script=Path(__file__).resolve().parents[1]/'scripts/export_repository.py'
    spec=importlib.util.spec_from_file_location('sharing_export',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    sandbox=tmp_path/'checkout';module.export(sandbox);module.ROOT=sandbox
    (sandbox/'outputs').mkdir();(sandbox/'outputs/private.txt').write_text('PRIVATE_DOCUMENT')
    (sandbox/'.env').write_text('PRIVATE_CONFIGURATION')
    (sandbox/'skills/source-preserve-md/.engine.json').write_text('PRIVATE_BINDING')
    (sandbox/'examples/legacy-inputs/private.hwp').write_bytes(b'PRIVATE_BINARY')
    output=tmp_path/'shared';module.export(output)
    assert not (output/'outputs').exists() and not (output/'.env').exists()
    assert not (output/'skills/source-preserve-md/.engine.json').exists()
    assert not (output/'examples/legacy-inputs/private.hwp').exists()
    assert (output/'examples/legacy-inputs/table.hwp').is_file()
