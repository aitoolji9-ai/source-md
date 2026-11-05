async function collectTabs(options={}) {
  const doc=options._document||document,win=doc.defaultView;
  const selectedSelector=options.tabSelector||'[role="tab"]';
  const selector=selectedSelector==='[role="tab"]'?selectedSelector+',[data-bs-toggle="tab"],[data-toggle="tab"],[data-tab-target],.tab-list [aria-controls]':selectedSelector;
  const waitMs=Math.min(30000,Math.max(100,options.tabWaitMs||5000));
  const limit=Math.min(24,Math.max(1,options.maxTabs||24));
  const context=options._context||{deadline:Date.now()+Math.min(45000,options.totalMs||45000),bytes:0,count:0,nextFrame:1,seen:new WeakMap()};
  const frameIndex=options._frameIndex||0,depth=options._depth||0;
  const ignoredId='source-preserve-dom-capture',states=[],outcomes=[],visited=new Set();
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const visible=el=>!!el&&el.isConnected&&!el.closest('[hidden],[inert],[aria-hidden="true"]')&&win.getComputedStyle(el).visibility!=='hidden'&&el.getClientRects().length>0;
  const allTabs=()=>Array.from(doc.querySelectorAll(selector));
  const key=el=>el.id?'id:'+el.id:'node:'+allTabs().indexOf(el)+':'+el.textContent.trim()+':'+el.getAttribute('aria-controls');
  const panel=el=>{const target=el.getAttribute('aria-controls')||el.getAttribute('data-bs-target')||el.getAttribute('data-target')||el.getAttribute('data-tab-target')||(el.getAttribute('href')||'');try{return doc.getElementById(decodeURIComponent(target.replace(/^#/,'')));}catch{return null;}};
  const bodyClone=()=>{const clone=doc.body.cloneNode(true);clone.querySelector('#'+ignoredId)?.remove();clone.querySelectorAll('script,style,template').forEach(n=>n.remove());return clone;};
  const bodyText=()=>bodyClone().textContent;
  const fingerprint=()=>bodyText()+'|'+Array.from(doc.images).map(n=>n.currentSrc||n.src).join('|');
  const capture=(label,state,tab=null)=>{
    const clone=doc.documentElement.cloneNode(true);clone.querySelector('#'+ignoredId)?.remove();
    const html=clone.outerHTML,size=new TextEncoder().encode(html).length;
    if(size>20*1024*1024||context.bytes+size>40*1024*1024||context.count>=64){outcomes.push({status:'limit',message:'DOM 상태 64개/개별 20MB/전체 40MB 상한',frame_index:frameIndex});return false;}
    context.bytes+=size;context.count++;states.push({html,url:win.location.href,label,state,frame_index:frameIndex,tab});return true;
  };
  const reason=el=>{
    if(!visible(el))return 'hidden';
    if(el.matches(':disabled,[aria-disabled="true"]'))return 'disabled';
    if(el.closest('form')&&el.tagName==='BUTTON'&&el.type!=='button')return 'form-submit';
    if(el.tagName==='INPUT')return 'input-control';
    if(el.closest('#'+ignoredId))return 'capture-helper';
    if(el.hasAttribute('href')){
      const url=new URL(el.getAttribute('href'),win.location.href),base=new URL(win.location.href);
      if(!['http:','https:'].includes(url.protocol)||url.origin!==base.origin||url.pathname!==base.pathname||url.search!==base.search)return 'navigation-link';
    }
    return '';
  };
  async function scrollViews(label,state){
    if(options.scroll===false)return;
    const roots=[doc.scrollingElement,...Array.from(doc.querySelectorAll('body *')).filter(el=>!el.closest('#'+ignoredId)&&visible(el)&&el.clientHeight>=80&&el.scrollHeight>el.clientHeight+8&&/auto|scroll/.test(win.getComputedStyle(el).overflowY))].filter(Boolean).slice(0,8);
    for(const [index,root] of roots.entries()){
      const start=root.scrollTop;let last=fingerprint(),ended=false,stableEnd=0;
      for(let step=0;step<32&&Date.now()<context.deadline;step++){
        const bottom=Math.max(0,root.scrollHeight-root.clientHeight);
        root.scrollTop=Math.min(bottom,root.scrollTop+Math.max(40,root.clientHeight*.8));
        await pause(250);
        const current=fingerprint(),newBottom=Math.max(0,root.scrollHeight-root.clientHeight);
        if(current!==last){if(!capture(label+' · scroll '+(index+1)+'.'+step,state+'-scroll-'+index+'-'+step))break;last=current;}
        if(root.scrollTop>=newBottom-2){stableEnd++;if(stableEnd>=2){ended=true;break;}}else stableEnd=0;
      }
      outcomes.push({status:ended?'scroll-end':'scroll-limit',frame_index:frameIndex,root:index,reached_end:ended});
      root.scrollTop=start;
      if(context.count>=64||Date.now()>=context.deadline)break;
    }
  }
  async function childFrames(label,state){
    if(options.includeFrames===false)return;
    const frames=Array.from(doc.querySelectorAll('iframe')).filter(visible);
    if(depth>=3&&frames.length){outcomes.push({status:'limit',message:'iframe 깊이 3 상한'});return;}
    for(const element of frames){
      if(context.count>=64||Date.now()>=context.deadline)break;
      try{
        const child=element.contentDocument;
        if(!child?.body){outcomes.push({status:'frame-inaccessible',url:element.src,frame_index:frameIndex});continue;}
        const before=child.documentElement.outerHTML;
        if(context.seen.get(child)===before)continue;
        context.seen.set(child,before);
        const index=context.nextFrame++;
        const captured=await collectTabs({...options,_document:child,_context:context,_frameIndex:index,_depth:depth+1});
        states.push(...captured.states.map(s=>({...s,label:label+' / frame '+index+' / '+s.label,state:state+'/frame-'+index+'/'+s.state})));
        outcomes.push(...captured.outcomes);
        context.seen.set(child,child.documentElement.outerHTML);
      }catch(error){outcomes.push({status:'frame-error',message:String(error),url:element.src});}
    }
  }
  const originalURL=new URL(win.location.href);originalURL.hash='';
  try{allTabs();}catch(error){return {states:[],outcomes:[{status:'invalid-selector',message:String(error)}]};}
  const selected=allTabs().filter(el=>el.getAttribute('aria-selected')==='true').map(key);
  if(!capture('Initial','initial'))return {states,outcomes};
  await scrollViews('Initial','initial');await childFrames('Initial','initial');
  let attempts=0;
  while(Date.now()<context.deadline&&context.count<64){
    const candidate=allTabs().find(el=>!visited.has(key(el))&&visible(el));
    if(!candidate)break;
    const identity=key(candidate),label=candidate.textContent.trim().slice(0,200)||identity;
    visited.add(identity);
    const skip=reason(candidate);
    if(skip){outcomes.push({key:identity,label,status:'skipped',reason:skip,frame_index:frameIndex});continue;}
    if(candidate.getAttribute('aria-selected')==='true'){
      outcomes.push({key:identity,label,status:'already-selected',state:'initial-or-current',frame_index:frameIndex});continue;
    }
    if(attempts>=limit){outcomes.push({status:'limit',message:'탭 전환 24개 상한'});break;}
    attempts++;
    const target=panel(candidate),beforeHTML=target?.innerHTML,beforeVisible=visible(target),beforeText=bodyText();
    const started=Date.now();let last='',stableSince=started,ready=false;
    try{
      candidate.click();
      while(Date.now()-started<waitMs&&Date.now()<context.deadline){
        await pause(100);
        const currentURL=new URL(win.location.href);currentURL.hash='';
        if(currentURL.href!==originalURL.href)throw Error('페이지 이동 감지: 이후 전환 중단');
        const now=allTabs().find(el=>key(el)===identity),current=now?panel(now):target;
        const signature=current?current.innerHTML:bodyText();
        if(signature!==last){last=signature;stableSince=Date.now();}
        const changed=current?signature!==beforeHTML||!beforeVisible&&visible(current):signature!==beforeText;
        const busy=current?.getAttribute('aria-busy')==='true'||current?.querySelector('[aria-busy="true"]');
        if(changed&&!busy&&(!current||visible(current))&&Date.now()-started>=600&&Date.now()-stableSince>=350){ready=true;break;}
      }
      if(!capture(label,'tab-'+attempts,{key:identity,label}))break;
      outcomes.push({key:identity,label,status:ready?'captured':'timeout-or-unchanged',state:'tab-'+attempts,waited_ms:Date.now()-started,frame_index:frameIndex});
      options.onProgress?.({states:context.count,label,status:outcomes.at(-1).status});
      await scrollViews(label,'tab-'+attempts);await childFrames(label,'tab-'+attempts);
    }catch(error){outcomes.push({key:identity,label,status:'error',message:String(error),frame_index:frameIndex});break;}
  }
  if(Date.now()>=context.deadline)outcomes.push({status:'limit',message:'전체 수집 45초 상한'});
  for(const el of allTabs())if(!visited.has(key(el)))outcomes.push({key:key(el),label:el.textContent.trim().slice(0,200),status:'skipped',reason:reason(el)||'not-visited',frame_index:frameIndex});
  if(options.restore!==false){for(const identity of selected){const el=allTabs().find(n=>key(n)===identity);if(el&&!reason(el)&&el.getAttribute('aria-selected')!=='true')el.click();}}
  return {states,outcomes,scope:'식별한 탭·접근 가능한 iframe·유한 스크롤의 관측 DOM. 사이트 전체성과 무한 목록의 완결을 인증하지 않음.',selector,attempts};
}
