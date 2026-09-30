'use strict';
const el = id => document.getElementById(id);
let manifest, session;
const messages = {active:'자동 갱신이 연결되어 있습니다.',waiting:'연결되었습니다. 갱신을 기다리고 있습니다.',
  syncing:'급식과 학사일정을 갱신하고 있습니다.',paused:'자동 갱신이 일시 정지되어 있습니다.',
  retrying:'일시적인 오류로 다시 시도하고 있습니다. 기존 내용은 유지됩니다.',
  attention:'설정이나 원본 자료 확인이 필요합니다. 설치를 도운 AI에게 확인을 요청해 주세요.',
  reconnect:'Notion 접근 권한을 다시 연결해 주세요.',academic_year_ended:'수첩 학년도가 끝나 자동 갱신을 중지했습니다.'};
async function api(path, body, headers = {}) {
  const response = await fetch('/api/' + path, {method:body ? 'POST':'GET',credentials:'same-origin',
    headers:{'Content-Type':'application/json',...headers},...(body ? {body:JSON.stringify(body)}:{})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '연결을 확인해 주세요.');
  return result;
}
function displaySchool(data) {el('school').textContent = data.school_name;el('year').textContent = data.academic_year + '학년도';}
async function refresh() {
  session = await api('status');displaySchool(session);
  el('badge').textContent = session.enabled ? '연결됨':'중지됨';
  el('message').textContent = messages[session.status] || '연결 상태를 확인하고 있습니다.';
  el('last').textContent = session.last_success_at ? new Date(session.last_success_at*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}) : '아직 갱신하지 않았습니다';
  el('notion').href = session.notion_url;el('notion').hidden = false;
  el('toggle').hidden = false;el('toggle').textContent = session.enabled ? '자동 갱신 일시 정지':'자동 갱신 다시 시작';
  el('connect').hidden = true;
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
  if (location.hash.length > 1) {
    try {
      const raw=location.hash.slice(1);
      if (raw.length>10000 || !/^[A-Za-z0-9_-]+$/.test(raw)) throw new Error();
      const encoded=raw.replace(/-/g,'+').replace(/_/g,'/');
      manifest=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Uint8Array.from(atob(encoded+'='.repeat((4-encoded.length%4)%4)),c=>c.charCodeAt(0))));
      if (manifest.version!==1 || typeof manifest.school_name!=='string' || !Number.isInteger(manifest.academic_year)) throw new Error();
      displaySchool(manifest);el('connect').hidden=false;
      el('message').textContent='학교 정보가 준비되었습니다. Notion에서 기존 교무수첩의 접근을 허용해 주세요.';
      el('badge').textContent='연결 준비';
      history.replaceState(null,'',location.pathname);
      return;
    } catch {el('message').textContent='설치 정보가 유효하지 않습니다. 수첩의 연결 링크를 다시 열어 주세요.';return;}
  }
  try {await refresh();}
  catch {
    el('badge').textContent='연결 대기';
    el('message').textContent=new URLSearchParams(location.search).get('result')==='cancelled'
      ? 'Notion 연결을 취소했습니다. 수첩의 연결 링크에서 다시 시작할 수 있습니다.'
      : '교무수첩 안의 ‘자동 갱신 연결’ 링크로 들어와 주세요. 연결 정보는 설치할 때 자동으로 준비됩니다.';
  }
})();
