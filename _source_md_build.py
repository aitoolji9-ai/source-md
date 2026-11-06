"""PEP 517/660 backend: source installs cannot omit mandatory native checks."""
from pathlib import Path
import importlib.util


def _checked_backend():
    path=Path(__file__).parent/'src/source_preserve_md/preflight.py'
    spec=importlib.util.spec_from_file_location('_source_md_install_preflight',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.require_environment(Path(__file__).parent)
    from hatchling import build
    return build


def get_requires_for_build_wheel(config_settings=None):return _checked_backend().get_requires_for_build_wheel(config_settings)
def get_requires_for_build_editable(config_settings=None):return _checked_backend().get_requires_for_build_editable(config_settings)
def get_requires_for_build_sdist(config_settings=None):return _checked_backend().get_requires_for_build_sdist(config_settings)
def prepare_metadata_for_build_wheel(metadata_directory,config_settings=None):return _checked_backend().prepare_metadata_for_build_wheel(metadata_directory,config_settings)
def prepare_metadata_for_build_editable(metadata_directory,config_settings=None):return _checked_backend().prepare_metadata_for_build_editable(metadata_directory,config_settings)
def build_wheel(wheel_directory,config_settings=None,metadata_directory=None):return _checked_backend().build_wheel(wheel_directory,config_settings,metadata_directory)
def build_editable(wheel_directory,config_settings=None,metadata_directory=None):return _checked_backend().build_editable(wheel_directory,config_settings,metadata_directory)
def build_sdist(sdist_directory,config_settings=None):return _checked_backend().build_sdist(sdist_directory,config_settings)
