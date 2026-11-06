"""Dependency-free mandatory installation checks; usable before uv sync."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MIN_FREE_BYTES = 1024**3  # Installation policy: cache + Python + environment + temporary files.


def find_swift():
    """Validate selected developer tools, not just macOS's always-present wrapper."""
    try:
        selected=subprocess.run(['xcode-select','-p'],capture_output=True,text=True,timeout=10)
        if selected.returncode or not Path(selected.stdout.strip()).is_dir():
            return None,'selected developer tools missing'
        located=subprocess.run(['xcrun','--find','swiftc'],capture_output=True,text=True,timeout=10)
        selected_compiler=Path(located.stdout.strip())
        if located.returncode or not selected_compiler.is_file():return None,'selected Swift compiler missing'
        compiler=shutil.which('swiftc')
        if not compiler:return None,'Swift compiler wrapper missing'
        version=subprocess.run([compiler,'--version'],capture_output=True,text=True,timeout=15)
        if version.returncode:return None,'Swift compiler cannot execute: '+version.stderr[-500:]
        sdk=subprocess.run([compiler,'-typecheck','-e','import Foundation\nimport Vision'],capture_output=True,text=True,timeout=30)
        if sdk.returncode:return None,'Swift/Vision SDK compile check failed: '+sdk.stderr[-1000:]
        return compiler,str(selected_compiler)+'\n'+version.stdout.strip()
    except (OSError,subprocess.TimeoutExpired) as error:return None,str(error)


def inspect_environment(directory=None, *, native=True):
    checks=[]
    def record(name,passed,detail,fix=''):
        checks.append({'name':name,'passed':bool(passed),'detail':detail,'fix':fix})
    system=platform.system()
    record('macos',system=='Darwin',system,'macOS에서 설치하세요. Linux·Windows 설치는 지원하지 않습니다.')
    record('python',sys.version_info >= (3,12),platform.python_version(),'Python 3.12 이상이 필요합니다. install.sh는 uv로 Python 3.12를 준비합니다.')
    record('architecture',platform.machine() in ('arm64','x86_64'),platform.machine(),'64비트 Apple Silicon 또는 Intel Mac이 필요합니다.')
    destination=Path(directory or Path.cwd()).expanduser().resolve()
    while not destination.exists():destination=destination.parent
    try:
        free=shutil.disk_usage(destination).free
        record('disk_space',free>=MIN_FREE_BYTES,{'free_bytes':free,'required_bytes':MIN_FREE_BYTES},'설치 작업 공간을 1GiB 이상 확보하세요.')
    except OSError as error:record('disk_space',False,str(error),'설치 폴더의 저장 공간을 확인하세요.')
    compiler,compiler_detail=find_swift() if system=='Darwin' else (None,'not macOS')
    record('swift',bool(compiler),compiler_detail,'xcode-select --install 후 설치를 완료하세요.')
    if any(not c['passed'] for c in checks):
        record('vision_ocr',False,'not_run: prerequisite failed','위 필수 환경부터 준비하세요.')
    elif native:
        try:
            source=Path(__file__).with_name('vision_ocr.swift')
            image=Path(__file__).with_name('install_probe.png')
            compiler_info=Path(compiler).stat()
            key=hashlib.sha256(source.read_bytes()+str((compiler,compiler_info.st_mtime_ns,compiler_detail,platform.mac_ver())).encode()).hexdigest()[:24]
            cache=Path.home()/'Library/Caches/source-preserve-md/preflight';cache.mkdir(parents=True,exist_ok=True)
            binary=cache/('probe-'+key)
            if not binary.is_file():
                fd,tmp=tempfile.mkstemp(prefix='probe-build-',dir=cache);os.close(fd)
                try:
                    built=subprocess.run([compiler,'-O',str(source),'-o',tmp],capture_output=True,text=True,timeout=90)
                    if built.returncode:raise RuntimeError('Swift/Vision 컴파일 실패: '+built.stderr[-1500:])
                    os.replace(tmp,binary)
                finally:Path(tmp).unlink(missing_ok=True)
            result=subprocess.run([str(binary),str(image)],capture_output=True,text=True,timeout=30)
            if result.returncode:raise RuntimeError('로컬 OCR 실인식 실패: '+result.stderr[-1500:])
            data=json.loads(result.stdout)
            text=''.join(o.get('text','') for o in data.get('observations',[]))
            if ''.join(text.upper().split())!='SOURCEMD84729원문결' or not {'ko-KR','en-US'}.issubset(data.get('supported_languages',[])):
                raise RuntimeError('한국어·영어 OCR 실행 결과가 불완전합니다.')
            record('vision_ocr',True,{'engine':data.get('engine'),'recognized':text,'supported_languages':data['supported_languages']})
        except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as error:
            record('vision_ocr',False,str(error),'macOS와 Xcode Command Line Tools를 갱신하고 preflight를 다시 실행하세요.')
    else:record('vision_ocr',False,'not_run: native=False','실인식 검사 없이 설치 승인하지 않습니다.')
    return {'passed':all(c['passed'] for c in checks),'checks':checks,'policy':'macOS + Python>=3.12 + 64-bit + Swift + Korean/English Vision OCR + 1GiB installation space',
            'native_probe_performed':native and all(c['passed'] for c in checks if c['name']!='vision_ocr')}


def require_environment(directory=None):
    result=inspect_environment(directory)
    if not result['passed']:
        details='; '.join(c['name']+': '+str(c['detail'])+' → '+c['fix'] for c in result['checks'] if not c['passed'])
        raise RuntimeError('Source MD 필수 설치 환경 미충족: '+details)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path)
    args=parser.parse_args();result=inspect_environment(args.directory)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['passed'] else 2


if __name__=='__main__':raise SystemExit(main())
