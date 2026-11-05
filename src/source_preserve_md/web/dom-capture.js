(collectTabs=>{
  const key='source-preserve-dom-capture';
  if(document.getElementById(key))return;
  const host=document.createElement('section');host.id=key;
  host.style.cssText='position:fixed;right:16px;bottom:16px;z-index:2147483647';
  const shadow=host.attachShadow({mode:'open'});
  shadow.innerHTML=`<style>:host{font:14px system-ui;color:#17212b}section{width:330px;background:white;border:2px solid #31766a;border-radius:12px;padding:16px;box-shadow:0 5px 25px #0004}label{display:block;margin:10px 0}input{max-width:100%;box-sizing:border-box}button{margin:5px 4px 5px 0;padding:8px;cursor:pointer}p{line-height:1.5}</style><section aria-label="원문결 DOM 저장"><strong>원문결 · 열린 탭 상태 저장</strong><p>원본 HTML을 선택하고 탭 자동 수집을 누르세요. 필요한 상태를 직접 저장할 수도 있습니다. 이 도우미는 외부로 전송하지 않습니다.</p><label>원본 HTML 파일<input type="file" accept=".html,.htm,.xhtml" id="source"></label><label>상태 이름<input id="label" value="Initial"></label><button id="auto">탭 자동 수집</button><button id="add">현재 상태 저장</button><button id="export" disabled>DOM JSON 받기</button><button id="close">닫기</button><p id="status" role="status">원본 파일과 저장할 상태가 필요합니다.</p></section>`;
  document.body.append(host);
  const $=id=>shadow.getElementById(id),frames=[];
  let automaticReport=null;
  let sourceHash='',sourceName='';
  const status=text=>{$('status').textContent=text;$('export').disabled=!sourceHash||!frames.length;};
  $('source').onchange=async()=>{
    sourceHash='';status('원본 해시 확인 중');
    try{const file=$('source').files[0];if(!file||file.size>100*1024*1024)throw Error('HTML 파일은 100MB까지 선택하세요.');
      const digest=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());
      sourceHash=Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('');sourceName=file.name;
      status(`원본: ${sourceName} · 저장 상태 ${frames.length}개`);
    }catch(e){status(String(e));}
  };
  $('add').onclick=()=>{
    if(frames.length>=64){status('상태 64개 상한입니다. 새 묶음으로 나누세요.');return;}
    const copy=document.documentElement.cloneNode(true);copy.querySelector('#'+key)?.remove();
    const html=copy.outerHTML;
    if(new TextEncoder().encode(html).length>20*1024*1024){status('상태 20MB 상한입니다.');return;}
    frames.push({state:'view-'+frames.length,label:$('label').value||String(frames.length),url:location.href,frame_index:0,html});
    status(`원본: ${sourceName||'선택 필요'} · 저장 상태 ${frames.length}개`);
  };
  $('auto').onclick=async()=>{
    $('auto').disabled=true;$('add').disabled=true;$('export').disabled=true;
    status('탭을 찾아 본문을 자동 수집하는 중');
    try{
      automaticReport=await collectTabs({onProgress:step=>status(`자동 수집: ${step.label} · ${step.states}개 상태`)});
      frames.splice(0,frames.length,...automaticReport.states);
      const unresolved=automaticReport.outcomes.filter(x=>!['captured','already-selected','scroll-end','frame-recovered'].includes(x.status));
      status(`자동 수집 완료 · ${frames.length}개 상태 · 확인 항목 ${unresolved.length}개`);
    }catch(e){status('자동 수집 실패: '+String(e));}
    finally{$('auto').disabled=false;$('add').disabled=false;}
  };
  $('export').onclick=()=>{
    if(!sourceHash||!frames.length)return;
    const payload={schema_version:1,kind:'source-preserve-dom',source_sha256:sourceHash,source_filename:sourceName,source_url:location.href,captured_at:new Date().toISOString(),frames,automatic_tabs:automaticReport?{...automaticReport,states:undefined}:undefined};
    const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');
    a.href=url;a.download=sourceName+'.dom.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);status(`내보냄 · ${frames.length}개 상태. 원본 HTML과 JSON을 함께 변환하세요.`);
  };
  $('close').onclick=()=>host.remove();
})
