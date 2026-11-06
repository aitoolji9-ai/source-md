"""stdlib-only checks run before the application can be installed."""
import importlib.util
import shutil
import sys
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('install_preflight_test',ROOT/'src/source_preserve_md/preflight.py')
preflight=importlib.util.module_from_spec(spec);spec.loader.exec_module(preflight)
Usage=namedtuple('Usage','total used free')


class InstallationGateTests(unittest.TestCase):
    def inspect(self,system='Darwin',machine='arm64',free=5*1024**3,compiler=None,version=(3,12)):
        with mock.patch.object(preflight.platform,'system',return_value=system), mock.patch.object(preflight.platform,'machine',return_value=machine), mock.patch.object(preflight.shutil,'disk_usage',return_value=Usage(10*1024**3,0,free)), mock.patch.object(preflight,'find_swift',return_value=(compiler,'test compiler' if compiler else 'missing')), mock.patch.object(preflight.sys,'version_info',version):
            return preflight.inspect_environment(ROOT)

    def test_non_mac_is_refused_before_native_probe(self):
        for system in ('Linux','Windows'):
            with self.subTest(system=system), mock.patch.object(preflight.subprocess,'run') as run:
                result=self.inspect(system=system)
                self.assertFalse(result['passed']);run.assert_not_called()
                self.assertFalse(next(c for c in result['checks'] if c['name']=='macos')['passed'])

    def test_missing_swift_is_not_a_warning_only(self):
        result=self.inspect(compiler=None)
        self.assertFalse(result['passed'])
        self.assertFalse(next(c for c in result['checks'] if c['name']=='swift')['passed'])

    def test_low_disk_old_python_and_unsupported_cpu_are_refused(self):
        for kwargs,name in [({'free':1},'disk_space'),({'version':(3,11)},'python'),({'machine':'i386'},'architecture')]:
            with self.subTest(name=name):
                result=self.inspect(**kwargs);self.assertFalse(result['passed'])
                self.assertFalse(next(c for c in result['checks'] if c['name']==name)['passed'])

    def test_native_ocr_failure_and_timeout_prevent_installation(self):
        compiler=shutil.which('swiftc') or sys.executable
        for error in (RuntimeError('native failure'),preflight.subprocess.TimeoutExpired('swiftc',1)):
            with self.subTest(error=str(error)),mock.patch.object(preflight.subprocess,'run',side_effect=error):
                result=self.inspect(compiler=compiler)
                self.assertFalse(result['passed'])
                self.assertFalse(next(c for c in result['checks'] if c['name']=='vision_ocr')['passed'])

    def test_skipped_native_probe_never_approves_install(self):
        with mock.patch.object(preflight.platform,'system',return_value='Darwin'),mock.patch.object(preflight,'find_swift',return_value=(sys.executable,'test compiler')):
            self.assertFalse(preflight.inspect_environment(ROOT,native=False)['passed'])

    def test_cached_binary_cannot_replace_missing_developer_tools(self):
        with mock.patch.object(preflight.subprocess,'run',return_value=mock.Mock(returncode=1,stdout='',stderr='missing developer directory')):
            compiler,detail=preflight.find_swift()
        self.assertIsNone(compiler);self.assertIn('developer tools missing',detail)

    def test_pep517_source_backend_also_refuses_non_mac(self):
        spec=importlib.util.spec_from_file_location('checked_build_test',ROOT/'_source_md_build.py')
        backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)
        with mock.patch('platform.system',return_value='Linux'):
            with self.assertRaisesRegex(RuntimeError,'필수 설치 환경 미충족'):
                backend.get_requires_for_build_wheel()
            with self.assertRaisesRegex(RuntimeError,'필수 설치 환경 미충족'):
                backend.get_requires_for_build_editable()


if __name__=='__main__':unittest.main()
