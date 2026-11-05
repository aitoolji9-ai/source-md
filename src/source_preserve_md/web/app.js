const $ = (id) => document.getElementById(id);
let mode = 'file', selectedFiles = [], currentJob = null, config = {}, busy = false;

function setMode(value) {
  mode = value;
  for (const name of ['file', 'url']) {
    $(`${name}-pane`).hidden = name !== value;
    $(`${name}-tab`).classList.toggle('selected', name === value);
    $(`${name}-tab`).setAttribute('aria-selected', String(name === value));
  }
  updateInput();
}
function updateInput() {
  $('file-list').replaceChildren();
  selectedFiles.forEach((file, index) => {
    const row = document.createElement('div'); row.className = 'file-row';
    const name = document.createElement('span'); name.textContent = file.name;
    const size = document.createElement('small'); size.textContent = file.size > 1024 * 1024 ? `${(file.size / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(file.size / 1024)} KB`;
    const remove = document.createElement('button'); remove.textContent = '×'; remove.setAttribute('aria-label', `${file.name} 제거`);
    remove.onclick = () => { selectedFiles.splice(index, 1); updateInput(); };
    row.append(name, size, remove); $('file-list').append(row);
  });
  const valid = mode === 'file' ? selectedFiles.length > 0 : /^https?:\/\//.test($('url').value.trim());
  $('convert').disabled = !valid || busy;
  $('input-hint').textContent = valid ? (mode === 'file' ? `${selectedFiles.length}개 파일 · 원본과 함께 새 폴더에 보관` : '한 페이지 · 원본과 직접 자산 보관') : '원본을 선택하면 시작할 수 있습니다.';
}
function error(message) { $('error').hidden = !message; $('error').textContent = message || ''; }
async function request(url, options = {}) {
  const response = await fetch(url, options);
  let data; try { data = await response.json(); } catch { throw new Error(`서버 응답을 읽지 못했습니다 (HTTP ${response.status}).`); }
  if (!response.ok) throw new Error(data.error || `요청 실패 (HTTP ${response.status})`);
  return data;
}
async function history() {
  const jobs = await request('/api/jobs');
  $('history').replaceChildren();
  if (!jobs.length) { const p = document.createElement('p'); p.className = 'muted'; p.textContent = '아직 보관한 문서가 없습니다.'; $('history').append(p); }
  jobs.forEach(job => { const button = document.createElement('button'); button.textContent = job.title; button.title = job.title;
    const details = document.createElement('small'); details.textContent = `${job.format.toUpperCase()} · ${job.units}개 단위 · 확인 ${job.warnings}건`; button.append(details);
    button.onclick = () => showJob(job.id).catch(e => error(e.message)); $('history').append(button);
  });
}
function switchView(name) {
  for (const view of ['preview', 'markdown', 'report', 'structure']) $(`${view}-view`).hidden = view !== name;
  document.querySelectorAll('[data-view]').forEach(button => { const selected = button.dataset.view === name; button.classList.toggle('selected', selected); button.setAttribute('aria-selected', String(selected)); });
}
function setIntegrity(integrity) {
  $('stat-integrity').textContent = integrity.passed ? '검사 통과' : '불일치 발견';
  $('stat-integrity').classList.toggle('warn', !integrity.passed);
}
async function showJob(id) {
  const job = await request(`/api/jobs/${encodeURIComponent(id)}`); currentJob = id;
  $('result').hidden = false; $('result-title').textContent = job.manifest.source.filename;
  $('output-path').textContent = job.output;
  $('stat-format').textContent = job.manifest.source.format.toUpperCase();
  $('stat-units').textContent = `${job.manifest.units.length}개`;
  $('stat-warnings').textContent = `${job.manifest.unresolved.length}건`;
  $('stat-warnings').classList.toggle('warn', job.manifest.unresolved.length > 0);
  $('warning-count').textContent = job.manifest.unresolved.length;
  setIntegrity(job.integrity);
  // HTML is rendered and allowlisted by the local server; source HTML is never inserted.
  $('preview-view').innerHTML = job.preview;
  $('markdown-view').textContent = job.markdown;
  $('report-view').textContent = job.report;
  $('structure-view').textContent = JSON.stringify(job.structure, null, 2);
  $('download-md').href = `/api/jobs/${encodeURIComponent(id)}/files/${encodeURIComponent('00_원본복원.md')}`;
  $('open-review').href=`/api/jobs/${encodeURIComponent(id)}/review`;
  $('download-zip').href = `/api/jobs/${encodeURIComponent(id)}/zip`;
  switchView('preview');
  $('result').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
async function convert() {
  if (busy) return;
  busy = true; updateInput(); error(''); $('progress').hidden = false;
  const items = mode === 'file' ? [...selectedFiles] : [$('url').value.trim()];
  const snapshot=$('dom-snapshot').files[0];
  if(snapshot&&(mode!=='file'||items.length!==1)){
    busy=false;$('progress').hidden=true;updateInput();error('DOM JSON은 원본 HTML 한 파일과 함께 입력하세요.');return;
  }
  const failures = []; let lastJob;
  for (let index = 0; index < items.length; index++) {
    const item = items[index];
    $('progress-text').textContent = `${index + 1}/${items.length} · ${typeof item === 'string' ? item : item.name} · 원본 수집·구조 추출·저장 검사`;
    try {
      let body, headers = { 'X-Local-Token': config.token };
      const opts = { encoding: $('encoding').value, dynamic: mode === 'url' && $('dynamic').checked,auto_tabs:$('auto-tabs').checked, download_assets: $('download-assets').checked, selector: $('selector').value.trim(), ocr: $('ocr').checked ? 'auto' : 'off' };
      if (typeof item === 'string') { body = JSON.stringify({ url: item, ...opts }); headers['Content-Type'] = 'application/json'; }
      else { body = new FormData(); body.set('file', item); if(snapshot)body.set('dom_snapshot',snapshot); Object.entries(opts).forEach(([k, v]) => body.set(k, v)); }
      const result = await request('/api/convert', { method: 'POST', body, headers }); lastJob = result.id;
    } catch (e) { failures.push(`${typeof item === 'string' ? item : item.name}: ${e.message}`); }
  }
  busy = false; $('progress').hidden = true; updateInput();
  try { await history(); if (lastJob) await showJob(lastJob); } catch (e) { failures.push(e.message); }
  if (failures.length) error(failures.join('\n'));
}
$('file-tab').onclick = () => setMode('file'); $('url-tab').onclick = () => setMode('url');
$('url').addEventListener('input', updateInput);
$('files').onchange = () => { selectedFiles = [...$('files').files]; updateInput(); };
$('drop-zone').onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); $('files').click(); } };
for (const type of ['dragenter', 'dragover']) $('drop-zone').addEventListener(type, e => { e.preventDefault(); $('drop-zone').classList.add('drag'); });
for (const type of ['dragleave', 'drop']) $('drop-zone').addEventListener(type, e => { e.preventDefault(); $('drop-zone').classList.remove('drag'); });
$('drop-zone').addEventListener('drop', e => { selectedFiles = [...e.dataTransfer.files]; updateInput(); });
$('convert').onclick = convert;
$('correct-job').onclick=()=>{if(currentJob)$('correction-file').click();};
$('correction-file').onchange=async()=>{
  const file=$('correction-file').files[0];if(!file||!currentJob)return;
  $('correct-job').disabled=true;error('');
  try{const body=new FormData();body.set('ocr_corrections',file);
    const result=await request(`/api/jobs/${encodeURIComponent(currentJob)}/correct`,{method:'POST',body,headers:{'X-Local-Token':config.token}});
    await history();await showJob(result.id);
  }catch(e){error(e.message);}finally{$('correct-job').disabled=false;$('correction-file').value='';}
};
$('refresh').onclick = () => history().catch(e => error(e.message));
$('new-job').onclick = () => { $('result').hidden = true; error(''); window.scrollTo({ top: 0, behavior: 'smooth' }); };
document.querySelectorAll('[data-view]').forEach(button => button.onclick = () => switchView(button.dataset.view));
$('validate').onclick = async () => {
  if (!currentJob) return;
  $('validate').disabled = true;
  try { const result = await request(`/api/jobs/${encodeURIComponent(currentJob)}/validate`, { method: 'POST', headers: { 'X-Local-Token': config.token } }); setIntegrity(result); $('validate').textContent = result.passed ? '재검사 통과' : '불일치 발견'; }
  catch (e) { error(e.message); }
  finally { $('validate').disabled = false; }
};
(async () => { try { config = await request('/api/config'); await history(); } catch (e) { error(`로컬 프로그램 연결 실패: ${e.message}`); } })();

