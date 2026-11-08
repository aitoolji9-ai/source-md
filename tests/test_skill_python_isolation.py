from pathlib import Path
import json
import os
import subprocess
import sys
import pytest


@pytest.mark.parametrize('poison_cwd',[False,True])
def test_installed_skill_ignores_host_pythonpath_but_preserves_cwd(tmp_path,poison_cwd):
    from source_preserve_md.integration import install_skill
    skill=tmp_path/'skill';install_skill(skill)
    poison=tmp_path/'host-python';poison.mkdir()
    (poison/'source_preserve_md.py').write_text("raise RuntimeError('HOST PYTHONPATH LEAKED')\n")
    # A local filename must not shadow the installed engine either.
    cwd=tmp_path/'documents';cwd.mkdir()
    if poison_cwd:(cwd/'source_preserve_md.py').write_text("raise RuntimeError('CALLER CWD LEAKED')\n")
    (cwd/'source.md').write_text('# Exact source\n')
    env={**os.environ,'PYTHONPATH':str(poison)}
    result=subprocess.run([sys.executable,str(skill/'scripts/run.py'),'convert','source.md','--out','converted','--ocr','off'],
                          cwd=cwd,env=env,text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert (cwd/'converted/00_원본복원.md').read_bytes()==(cwd/'source.md').read_bytes()
    binding=json.loads((skill/'.engine.json').read_text())
    assert binding['python']==sys.executable
