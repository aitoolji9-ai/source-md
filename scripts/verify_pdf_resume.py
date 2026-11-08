"""Exercise fresh PDF conversion, checkpoint, real interruption and resume.

Inputs remain local. No fragment PDFs are created. Full transcripts and source
files stay in the requested output directory and must not be committed.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time


def verify(sources,output,interrupt=True):
    root=output.expanduser().resolve();root.mkdir(parents=True,exist_ok=False)
    receipts=[]
    for index,item in enumerate(sources,1):
        source=item.expanduser().resolve();digest=hashlib.sha256(source.read_bytes()).hexdigest()
        folder=root/f'{index:02d}';prefix=[sys.executable,'-m','source_preserve_md']
        def command(arguments,name):
            with (root/(f'{index:02d}-'+name+'.json')).open('w') as stdout,(root/(f'{index:02d}-'+name+'-progress.jsonl')).open('w') as stderr:
                return subprocess.run(prefix+arguments,cwd=root,stdout=stdout,stderr=stderr).returncode
        start=time.monotonic();exit_code=command(['convert',str(source),'--out',str(folder),'--pdf-max-pages','2'],'start')
        if exit_code not in (0,3):raise RuntimeError(f'Book {index}: initial conversion failed; inspect saved logs')
        state=json.loads((folder/'pdf-job.json').read_text());first={k:v['sha256'] for k,v in state['pages'].items()};killed=False
        if interrupt and index==1 and state['total_pages']>8:
            with (root/'interrupted-stdout.json').open('w') as stdout,(root/'interrupted-progress.jsonl').open('w') as stderr:
                process=subprocess.Popen(prefix+['resume',str(folder)],cwd=root,stdout=stdout,stderr=subprocess.PIPE,text=True,start_new_session=True)
                for line in process.stderr:
                    stderr.write(line);stderr.flush()
                    try:event=json.loads(line).get('pdf_progress',{})
                    except ValueError:continue
                    if event.get('phase')=='page_saved' and event['checkpointed_pages']>=8:
                        try:os.killpg(process.pid,signal.SIGTERM)
                        except ProcessLookupError:pass
                        break
                killed=process.wait()==-signal.SIGTERM
            if not killed:raise RuntimeError('Requested interruption was not observed')
        exit_code=command(['resume',str(folder)],'final')
        if exit_code:raise RuntimeError(f'Book {index}: resume failed; inspect saved logs')
        state=json.loads((folder/'pdf-job.json').read_text());result=json.loads((root/(f'{index:02d}-final.json')).read_text())
        assert result['processing']['complete'] and result['integrity']['passed']
        assert all(state['pages'][k]['sha256']==value for k,value in first.items())
        counts={}
        for event in state['history']:
            if event['phase']=='primary':counts[event['page']]=counts.get(event['page'],0)+1
        assert all(value==1 for value in counts.values())
        assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
        row={'book':index,'source_sha256':digest,'pages':result['units'],'committed_requests':result['ocr']['requests'],
             'complete':True,'initial_checkpoints_reused':len(first),'real_sigterm':killed,'seconds':time.monotonic()-start,
             'output':str(folder),'processing':result['processing'],'integrity':True}
        receipts.append(row);(root/'verification.json').write_text(json.dumps(receipts,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:row[k] for k in ('book','pages','committed_requests','complete','real_sigterm')},ensure_ascii=False),flush=True)
    return receipts


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources',type=Path,nargs='+');parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--no-interrupt',action='store_true')
    args=parser.parse_args();verify(args.sources,args.out,not args.no_interrupt)
