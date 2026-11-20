import {createWorkflowSelection, workflowSummary, validateWorkflowSelection} from './workflow-model.mjs';
import {element as el, loadWorkflowCatalog, readWorkflowDraft, saveWorkflowDraft, mountWorkflowPicker, MODULE_NAMES} from './workflow-picker.mjs';

const $ = id => document.getElementById(id);
let catalog, selection;
const USES = {
  student_history:'상담 전 학생의 지난 기록을 함께 확인하고 싶을 때',
  class_hub:'반을 옮겨 가며 명렬표와 진도를 확인할 때',
  lesson_links:'어느 반에서 어떤 자료로 수업했는지 찾을 때',
  followups:'회의나 상담에서 나온 할 일을 놓치고 싶지 않을 때',
  para_hub:'학부모 총회처럼 여러 업무와 자료가 묶이는 일을 할 때',
  submissions:'동의서·과제의 미제출자를 한눈에 확인할 때',
  assessment_scope:'평가 범위와 채점 기준을 수업 기록과 함께 준비할 때',
  assessment_results:'학생마다 피드백과 재확인 상태를 남기고 싶을 때',
  staff_links:'업무 인수인계와 담당자 연락을 자주 확인할 때',
};
function button(text, action) { const b = el('button',text); b.type='button'; b.addEventListener('click',action); return b; }
function table(headers, rows) {
  const wrap = el('div',undefined,'demo-table-wrap'), t=el('table',undefined,'demo-table'), h=el('thead'), tr=el('tr'), body=el('tbody');
  for (const name of headers) { const th=el('th',name); th.scope='col'; tr.append(th); } h.append(tr);
  for (const row of rows) { const r=el('tr'); for (const value of row) { const c=el('td'); c.append(value instanceof Node ? value : el('span',String(value))); r.append(c); } body.append(r); }
  if (!rows.length) { const r=el('tr'), c=el('td','해당하는 기록이 없어요.'); c.colSpan=headers.length;r.append(c);body.append(r); }
  t.append(h,body);wrap.append(t);return wrap;
}
function badge(text) {return el('span',text,'demo-status');}
function select(label, values, onChange) {
  const wrap=el('div',undefined,'demo-controls'), l=el('label',label), input=el('select');
  input.setAttribute('aria-label',label);
  for (const value of values) {const o=el('option',value);o.value=value;input.append(o);}
  input.addEventListener('change',()=>onChange(input.value)); l.append(input);wrap.append(l);return wrap;
}
function tabDemo(host, {title, choices, tabs, rows, headers, note}) {
  let choice=choices?.[0], active=tabs[0];
  host.append(el('h4',title,'demo-title'));
  const tabbar=el('div',undefined,'demo-tabs'), content=el('div'); content.setAttribute('aria-live','polite');
  const draw=()=>{content.replaceChildren(table(headers[active] || headers.default,rows(active,choice))); for (const b of tabbar.children) b.setAttribute('aria-pressed',String(b.textContent===active));};
  if (choices) host.append(select(`${title} 예시 선택`,choices,v=>{choice=v;draw();}));
  for (const tab of tabs) {const b=button(tab,()=>{active=tab;draw();});tabbar.append(b);}
  host.append(tabbar,content,el('p',note,'demo-foot'));draw();
  return function refreshVisibleRecord(tab, student) {
    if (active === tab && choice === student) draw();
  };
}
function taskDemo(host, project=false) {
  host.append(el('h4',project?'학부모 총회 · 프로젝트':'학년 협의회 · 후속 업무','demo-title'));
  const names=project?['안내문 검토','발표 자료 준비','교실 배치 점검']:['공동 활동지 검토','다음 협의 일정 정하기'];
  const done=names.map((_,i)=>i===0), meter=el('div',undefined,'demo-meter'), progress=el('progress'), value=el('span');progress.max=names.length;meter.append(progress,value);
  const body=el('div'), refresh=()=>{progress.value=done.filter(Boolean).length;value.textContent=`${progress.value} / ${names.length} 완료`;};
  const rows=names.map((name,i)=>{const input=el('input');input.type='checkbox';input.checked=done[i];input.setAttribute('aria-label',`${name} 완료`);input.addEventListener('change',()=>{done[i]=input.checked;refresh();});return[input,name,project?badge('학부모 총회'):badge('학년 협의회')];});
  body.append(table(['완료','할 일','연결된 원본'],rows));host.append(meter,body);
  if(project)host.append(el('p','담당 영역: 학급 경영   ↔   자료: 총회 안내문 · 회의록','demo-note'));
  host.append(el('p','체크해 보세요. 같은 업무의 상태를 원본 기록에서 함께 확인하는 예시입니다.','demo-foot'));refresh();
}
const pendingTab = '미제출·보완 요청';
const sampleSubmission = {
  title: '체험학습 동의서',
  due: '10/20',
  statuses: new Map([
    ['가상학생 A', '제출'],
    ['가상학생 B', '미제출'],
    ['가상학생 C', '보완 요청'],
  ]),
};
const submissionChanges = new EventTarget();

function studentHistoryRows(tab, student) {
  if (tab === pendingTab) {
    const status = sampleSubmission.statuses.get(student);
    return ['미제출', '보완 요청'].includes(status)
      ? [[sampleSubmission.due, sampleSubmission.title, badge(status)]]
      : [];
  }
  if (student === '가상학생 C') return [];
  const firstStudent = student === '가상학생 A';
  if (tab === '상담 이력') {
    return firstStudent
      ? [['10/12', '학습 계획 상담', badge('후속 확인')], ['9/24', '새 학기 목표', badge('완료')]]
      : [['10/7', '학교생활 상담', badge('완료')]];
  }
  if (tab === '연락 이력') return [[firstStudent ? '10/12' : '10/7', '상담 시간 안내', badge('연락 완료')]];
  return [['10/8', firstStudent ? '지각' : '정상 출석', badge('확인')]];
}

function studentHistoryDemo(host) {
  const refresh = tabDemo(host, {
    title: '학생 페이지 · 관련 기록',
    choices: [...sampleSubmission.statuses.keys()],
    tabs: ['상담 이력', '연락 이력', '출결', pendingTab],
    headers: {default: ['날짜', '기록', '상태'], [pendingTab]: ['마감', '제출 항목', '상태']},
    rows: studentHistoryRows,
    note: '선택한 학생의 기록을 보여요. 연락·출결·제출물 보기는 해당 모듈을 선택했을 때 구성합니다.',
  });
  submissionChanges.addEventListener('change', event => refresh(pendingTab, event.detail));
}

function submissionsDemo(host) {
  const {title, due, statuses} = sampleSubmission;
  host.append(el('h4', `${title} · 수합 현황`, 'demo-title'));
  const meter = el('div', undefined, 'demo-meter');
  const progress = el('progress');
  progress.max = statuses.size;
  progress.setAttribute('aria-label', `${title} 제출 인원`);
  const summary = el('span');
  summary.setAttribute('role', 'status');
  meter.append(progress, summary);

  function updateSummary() {
    const total = statuses.size;
    const completed = [...statuses.values()].filter(status => status === '제출' || status === '확인 완료').length;
    progress.value = completed;
    summary.textContent = `${completed} / ${total}명 제출 · ${(completed / total * 100).toFixed(1)}%`;
  }

  const rows = [...statuses].map(([student, status]) => {
    const input = el('select');
    input.setAttribute('aria-label', `${student} 제출 상태`);
    for (const option of ['미제출', '제출', '보완 요청', '확인 완료']) input.append(el('option', option));
    input.value = status;
    input.addEventListener('change', () => {
      statuses.set(student, input.value);
      updateSummary();
      submissionChanges.dispatchEvent(new CustomEvent('change', {detail: student}));
    });
    return [student, input, due];
  });
  host.append(meter, table(['대상 학생', '제출 상태', '마감'], rows),
    el('p', '상태를 바꾸면 학생별 기록에도 반영돼요. 미제출과 보완 요청은 학생별 목록에 남고, 제출과 확인 완료는 제출 인원에 포함됩니다.', 'demo-foot'));
  updateSummary();
}
function demo(key, host) {
  if(key==='student_history')studentHistoryDemo(host);
  else if(key==='class_hub')tabDemo(host,{title:'학급 페이지 · 우리 반 한눈에',choices:['2학년 1반','2학년 2반'],tabs:['학생','수업 진도','예정 평가'],headers:{default:['항목','내용','상태']},rows:(tab,c)=>tab==='학생'?[[c==='2학년 1반'?'가상학생 A':'가상학생 B','학급 명단',badge('재학')]]:tab==='수업 진도'?[['과학 · 힘과 운동',c==='2학년 1반'?'4 / 6차시':'3 / 6차시',badge('진행 중')]]:[['탐구 보고서','10/23',badge('예정')]],note:'학급을 바꿔 보세요. 시간표는 수업 화면에서 확인하고 수업을 업무 일정에 복사하지 않습니다.'});
  else if(key==='lesson_links')tabDemo(host,{title:'힘과 운동 · 수업 준비',tabs:['수업 시간표','수업 자료'],headers:{default:['연결 기록','반 / 내용']},rows:tab=>tab==='수업 시간표'?[['10/12 · 3교시','2학년 1반'],['10/13 · 2교시','2학년 2반']]:[['활동지 01','힘의 합력 탐구'],['수업 성찰','다음 차시 도입 보완']],note:'시간표와 자료를 오가며 확인해 보세요. 수업 완료는 교사가 직접 기록합니다.'});
  else if(key==='followups')taskDemo(host);
  else if(key==='para_hub')taskDemo(host,true);
  else if(key==='submissions')submissionsDemo(host);
  else if(key==='assessment_scope')tabDemo(host,{title:'탐구 보고서 · 평가 준비',tabs:['평가 범위','채점 기준'],headers:{default:['항목','연결된 자료 / 진도']},rows:tab=>tab==='평가 범위'?[['대상 단원','힘과 운동 · 1~6차시'],['대상 학급','2학년 1반 · 10/23']]:[['탐구 설계','계획의 타당성 · 40점'],['결과 해석','근거 제시 · 60점']],note:'평가에서 수업 단원과 기준표를 함께 찾아가는 예시입니다. 실제 점수는 개인별 결과 기능에서 별도로 기록합니다.'});
  else if(key==='assessment_results')tabDemo(host,{title:'학생별 평가와 피드백',choices:['가상학생 A','가상학생 B'],tabs:['평가 결과','피드백'],headers:{default:['평가','기록','상태']},rows:(tab,s)=>tab==='평가 결과'?[['탐구 보고서',s==='가상학생 A'?'85 / 100':'90 / 100',badge('채점 완료')]]:[['탐구 보고서',s==='가상학생 A'?'근거를 한 가지 더 제시하기':'다른 조건에서도 비교해 보기',badge('전달 예정')]],note:'한 행은 학생 1명 × 평가 1건입니다. 일반 평가 총괄표와 분리된 개별 기록을 사용합니다.'});
  else if(key==='staff_links')tabDemo(host,{title:'교직원별 담당 업무',choices:['가상교사 A','가상교사 B'],tabs:['담당 업무','연락 정보'],headers:{default:['항목','내용']},rows:(tab,s)=>tab==='담당 업무'?(s==='가상교사 A'?[['학년 협의회','자료 준비'],['학교 서비스','학급 홈페이지 관리']]:[['평가 협의회','기준표 검토']]):[['이름',s],['내선','000 (가상 예시)']],note:'담당자를 바꾸면 연결된 업무를 확인할 수 있어요. 기존 이름·담당 텍스트는 보존하고, 이름만으로 사람을 자동 연결하지 않습니다.'});
}
function renderCards() {
  for(const [index, item] of catalog.features.entries()) {
    const card=el('article',undefined,'feature-card');card.id='example-'+item.key;
    const text=el('div',undefined,'feature-card-copy'), number=el('span',`0${index+1}`,'feature-index'), selected=el('span','선택됨','feature-selected');selected.dataset.featureBadge=item.key;number.append(selected);
    text.append(number,el('h3',item.title),el('p',item.description),el('p','이럴 때 좋아요 · '+USES[item.key],'feature-use'));
    if (item.requires_modules.length) text.append(el('p','함께 준비: '+item.requires_modules.map(key=>MODULE_NAMES[key]).join(' · '),'field-help'));
    if (item.optional_modules.length) text.append(el('p','해당 모듈 선택 시 함께 연결: '+item.optional_modules.map(key=>MODULE_NAMES[key]).join(' · '),'field-help'));
    const label=el('label',undefined,'feature-toggle'), input=el('input');input.type='checkbox';input.dataset.featureToggle=item.key;input.setAttribute('aria-label',`${item.title} 사용하기`);
    input.addEventListener('change',()=>setSelection(createWorkflowSelection(catalog,'custom',input.checked?[...selection.features,item.key]:selection.features.filter(key=>key!==item.key))));
    label.append(input,el('span','내 수첩에서 사용하기'));text.append(label);
    const example=el('div',undefined,'feature-demo'), caption=el('div',undefined,'demo-caption'), body=el('div',undefined,'demo-body');caption.append(el('span','NOTION 구성 예시'),el('span','직접 눌러 보세요'));example.append(caption,body);demo(item.key,body);card.append(text,example);$('feature-examples').append(card);
    const link=el('a');link.href='#'+card.id;link.append(el('span',String(index+1).padStart(2,'0')),el('span',item.title));$('feature-nav').append(link);
  }
}
function setSelection(value) {
  if(validateWorkflowSelection(catalog,value).length)return;
  selection=value; const saved=saveWorkflowDraft(selection);
  mountWorkflowPicker($('workflow-picker'),catalog,selection,setSelection);
  for(const input of document.querySelectorAll('[data-feature-toggle]'))input.checked=selection.features.includes(input.dataset.featureToggle);
  for(const badge of document.querySelectorAll('[data-feature-badge]'))badge.hidden=!selection.features.includes(badge.dataset.featureBadge);
  const summary=workflowSummary(catalog,selection);$('features-selection-title').textContent=`${summary.title} · 연결 기능 ${selection.features.length}개`;
  $('features-selection-note').textContent=saved?'이 구성으로 설치 정보를 입력하거나 기존 수첩에 추가할 수 있어요.':'임시 저장이 꺼져 있어요. 다음 화면에서 구성을 다시 골라 주세요.';
}
try {
  catalog=await loadWorkflowCatalog();selection=readWorkflowDraft(catalog)||createWorkflowSelection(catalog,'basic');renderCards();setSelection(selection);
} catch(error) {
  $('features-error').textContent=error.message;$('features-error').hidden=false;$('workflow-picker').replaceChildren();$('features-selection-title').textContent='기능 목록 확인 필요';
  for(const a of document.querySelectorAll('.features-actions a')){a.removeAttribute('href');a.setAttribute('aria-disabled','true');}
}
