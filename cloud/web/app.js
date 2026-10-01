'use strict';
const el = id => document.getElementById(id);
let manifest, session, updateTargets, pendingConnection;
const bundleKey='planner-update-link-v1';
const updateStates={available:'미적용',pending:'진행 중 · 다시 확인 가능',schema_applied:'관계 연결 확인 · 화면 설정 대기',
  assistance_required:'AI와 확인 필요',complete:'적용 완료 · 화면 확인 기록됨'};
const updateHelp={existing_records:'기존 상담이 있어 자동으로 관계를 바꾸지 않았습니다. AI에게 기록을 보존하며 적용하도록 요청하세요.',
  layout_confirmed:'관계 연결을 서버에서 확인했고, 화면은 사용자가 확인한 것으로 기록했습니다.'};
const messages = {active:'자동 갱신이 연결되어 있습니다.',waiting:'연결되었습니다. 갱신을 기다리고 있습니다.',
  syncing:'급식과 학사일정을 갱신하고 있습니다.',paused:'자동 갱신이 일시 정지되어 있습니다.',
  retrying:'일시적인 오류로 다시 시도하고 있습니다. 기존 내용은 유지됩니다.',
  attention:'설정이나 원본 자료 확인이 필요합니다. 설치를 도운 AI에게 확인을 요청해 주세요.',
  quota_wait:'오늘의 처리 한도에 도달해 다음 날 순차 갱신을 기다리고 있습니다. 기존 내용은 유지됩니다.',
  reconnect:'Notion 접근 권한을 다시 연결해 주세요.',academic_year_ended:'수첩 학년도가 끝나 자동 갱신을 중지했습니다.'};
async function api(path, body, headers = {}) {
  const response = await fetch('/api/' + path, {method:body ? 'POST':'GET',credentials:'same-origin',
    headers:{'Content-Type':'application/json',...headers},...(body ? {body:JSON.stringify(body)}:{})});
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(result.error || '연결을 확인해 주세요.'),{status:response.status});
  return result;
}
function displaySchool(data) {el('school').textContent = data.school_name;el('year').textContent = data.academic_year + '학년도';}
async function refresh() {
  session = await api('status');displaySchool(session);
  if(manifest && !session.notion_url.replaceAll('-','').endsWith(manifest.root_page_id.replaceAll('-',''))) updateTargets=null;
  el('badge').textContent = session.enabled ? '연결됨':'중지됨';
  el('message').textContent = messages[session.status] || '연결 상태를 확인하고 있습니다.';
  el('last').textContent = session.last_success_at ? new Date(session.last_success_at*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}) : '아직 갱신하지 않았습니다';
  el('notion').href = session.notion_url;el('notion').hidden = false;
  el('toggle').hidden = false;el('toggle').textContent = session.enabled ? '자동 갱신 일시 정지':'자동 갱신 다시 시작';
  el('connect').hidden = true;
  try {await refreshUpdates();}
  catch(error) {el('update-message').textContent=error.status===404 ? '이 연결에서는 업데이트 센터를 지원하지 않습니다. 아래 안내에 따라 AI와 기존 수첩을 업데이트하세요.' : '업데이트 상태를 불러오지 못했습니다. 잠시 후 다시 열어 주세요.';}
}
async function refreshUpdates() {
  const data=await api('updates');el('releases').replaceChildren();
  el('update-message').textContent='원하는 업데이트를 선택해 적용하세요. 적용 중에는 관련 데이터베이스 편집을 잠시 멈춰 주세요.';
  for (const release of data.releases) {
    const card=document.createElement('article');card.className='release';
    const title=document.createElement('h3');title.textContent=release.title+' · '+release.version;
    const description=document.createElement('p');description.textContent=release.summary;
    const state=document.createElement('p');state.className='state';state.textContent=updateStates[release.status] || '상태 확인 필요';
    card.append(title,description,state);
    if(release.updated_at) {const date=document.createElement('small');date.textContent='마지막 확인: '+new Date(release.updated_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'});card.append(date);}
    if(release.reason) {const reason=document.createElement('p');reason.textContent=updateHelp[release.reason] ||
      (release.status==='assistance_required' ? '기존 구성과 변경 결과를 확인해야 합니다. 아래 AI 요청문을 사용하세요.' : '학생 페이지의 상담 목록과 날짜 정렬을 Notion에서 확인하세요.');card.append(reason);}
    const actions=document.createElement('div');actions.className='actions';
    const apply=document.createElement('button');apply.type='button';
    apply.textContent=release.registered ? '적용 상태 다시 확인' : '이 업데이트 적용';
    apply.disabled=!release.registered && !updateTargets;
    if(apply.disabled) {const note=document.createElement('p');note.textContent='이전 수첩은 설치를 도운 AI에게 업데이트 링크 추가를 요청해 주세요. 기존 페이지를 그대로 사용합니다.';card.append(note);}
    apply.addEventListener('click',()=>runUpdate(apply,'updates/apply',{update_id:release.id,...(!release.registered ? {targets:updateTargets}:{})}));
    actions.append(apply);card.append(actions);
    if(release.status==='schema_applied') {
      const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';
      label.append(check,document.createTextNode('학생 페이지에서 해당 학생의 지난 상담이 보이고, 상담일 내림차순인지 확인했습니다.'));
      const confirm=document.createElement('button');confirm.type='button';confirm.className='secondary';confirm.textContent='화면 확인 완료 기록';confirm.disabled=true;
      check.addEventListener('change',()=>confirm.disabled=!check.checked);
      confirm.addEventListener('click',()=>runUpdate(confirm,'updates/confirm-layout',{update_id:release.id,layout_confirmed:true}));
      card.append(label,confirm);
    }
    const prompt=document.createElement('button');prompt.type='button';prompt.className='secondary';prompt.textContent='AI에게 요청할 문장 복사';
    prompt.addEventListener('click',async()=>{
      const text='https://github.com/RuneDaeg/notion-teacher-planner 의 docs/UPDATES.md를 읽고 내 기존 교무수첩에 '+release.id+' 업데이트를 적용해 줘. 기존 기록과 설치 상태를 보존하고, 정확한 ID와 현재 적용 상태를 확인해 줘. 학생별 상담 보기와 정렬까지 검증하고 홈에 업데이트 확인 링크를 연결해 줘. 수첩 주소: '+session.notion_url;
      try {await navigator.clipboard.writeText(text);prompt.textContent='복사했습니다';}catch {const area=document.createElement('textarea');area.readOnly=true;area.value=text;card.append(area);area.select();}
    });actions.append(prompt);el('releases').append(card);
  }
}
async function runUpdate(button,path,data) {
  button.disabled=true;
  try {await api(path,data,{'X-CSRF-Token':session.csrf});await refreshUpdates();}
  catch(error) {
    el('update-message').textContent=error.status===401 ? 'Notion 접근을 다시 연결한 뒤 같은 업데이트를 확인해 주세요.' : error.message;
    if(error.status===401 && session?.reconnect_url) {el('reconnect').href=session.reconnect_url;el('reconnect').hidden=false;}
    button.disabled=false;
  }
}
el('connect').addEventListener('click', async () => {
  el('connect').disabled = true;
  try {
    const {authorize_url:url} = await api('start',manifest);
    const target = new URL(url);
    if (target.origin !== 'https://api.notion.com' || target.pathname !== '/v1/oauth/authorize') throw new Error('연결 주소를 확인해 주세요.');
    location.assign(url);
  } catch (error) {el('message').textContent=error.message;el('connect').disabled=false;}
});
el('toggle').addEventListener('click', async () => {
  el('toggle').disabled=true;
  try {await api('enabled',{enabled:!session.enabled},{'X-CSRF-Token':session.csrf});await refresh();}
  catch(error) {el('message').textContent=error.message;}
  finally {el('toggle').disabled=false;}
});
(async () => {
  const reauthorize=new URLSearchParams(location.search).has('reauthorize');
  try {pendingConnection=JSON.parse(sessionStorage.getItem(bundleKey));}catch {pendingConnection=null;}
  if (location.hash.length > 1) {
    try {
      const raw=location.hash.slice(1);
      if (raw.length>10000 || !/^[A-Za-z0-9_-]+$/.test(raw)) throw new Error();
      const encoded=raw.replace(/-/g,'+').replace(/_/g,'/');
      const payload=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Uint8Array.from(atob(encoded+'='.repeat((4-encoded.length%4)%4)),c=>c.charCodeAt(0))));
      manifest=payload.connection || payload;
      updateTargets=payload.connection ? payload.updates :
        (pendingConnection?.manifest?.root_page_id===manifest.root_page_id ? pendingConnection.updateTargets : null);
      if (manifest.version!==1 || typeof manifest.school_name!=='string' || !Number.isInteger(manifest.academic_year)) throw new Error();
      displaySchool(manifest);el('connect').hidden=false;
      el('message').textContent='학교 정보가 준비되었습니다. Notion에서 기존 교무수첩의 접근을 허용해 주세요.';
      el('badge').textContent='연결 준비';
      history.replaceState(null,'',location.pathname);
      pendingConnection={manifest,updateTargets};
      try {sessionStorage.setItem(bundleKey,JSON.stringify(pendingConnection));}catch { /* OAuth may need the link reopened if storage is unavailable. */ }
      // A matching authenticated planner can use an update link without reconnecting.
      try {
        const current=await api('status');
        if(!reauthorize && current.notion_url.replaceAll('-','').endsWith(manifest.root_page_id.replaceAll('-',''))) {await refresh();}
      }catch { /* Keep the explicit Notion connection button. */ }
      return;
    } catch {el('message').textContent='설치 정보가 유효하지 않습니다. 수첩의 연결 링크를 다시 열어 주세요.';return;}
  }
  if(pendingConnection) {
    manifest=pendingConnection.manifest;updateTargets=pendingConnection.updateTargets;
  }
  try {await refresh();}
  catch {
    el('badge').textContent='연결 대기';
    el('message').textContent=new URLSearchParams(location.search).get('result')==='cancelled'
      ? 'Notion 연결을 취소했습니다. 수첩의 연결 링크에서 다시 시작할 수 있습니다.'
      : '교무수첩 안의 ‘자동 갱신 연결’ 링크로 들어와 주세요. 연결 정보는 설치할 때 자동으로 준비됩니다.';
    if(manifest) {displaySchool(manifest);el('connect').hidden=false;}
  }
})();
