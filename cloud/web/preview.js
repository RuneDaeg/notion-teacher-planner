import {MODULES, FORMS} from './setup-model.mjs';
import {createSelection, selectionFromSetupAnswers, resolvePreview, buildReviewedBundle, verifyReviewedBundle, buildPreviewPrompt} from './preview-model.mjs';

const $ = id => document.getElementById(id);
const PAGE_NAMES = {home:'홈',classroom:'학급 · 상담',teaching:'수업 · 시간표',planning:'캘린더 · PARA'};
const PAGE_ICONS = {home:'📒',classroom:'👥',teaching:'📚',planning:'🗂️'};
const STORAGE_KEY = 'teacher-planner-preview-v1';
let catalog, selection = createSelection(), plan, reviewedBundle, activePage = 'home', revision = 0;
let visited = new Set(), htmlStyles = '', viewChoices = {};
const node = (tag, text, className) => {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
};

function message(text) { $('options-status').textContent = text; }
function issue(text) { $('preview-error').textContent = text; $('preview-error').hidden = false; }
function save() {
  try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify({version:1,selection})); }
  catch { message('이 브라우저에서는 임시 저장이 되지 않습니다. 배치 파일로 보관해 주세요.'); }
}

function setupSelection() {
  const raw = sessionStorage.getItem('teacher-planner-setup-v1');
  if (!raw || raw.length > 40000) return null;
  const saved = JSON.parse(raw);
  if (![1,2].includes(saved.version) || !saved.answers) return null;
  return selectionFromSetupAnswers(saved.answers);
}

function formAvailable(form) {
  return (!form.module || selection.modules.includes(form.module)) && (!form.homeroom || selection.homeroom);
}

function controls() {
  for (const item of MODULES) $('preview-module-' + item.key).checked = selection.modules.includes(item.key);
  for (const form of FORMS) {
    const input = $('preview-form-' + form.key);
    input.checked = selection.forms.includes(form.key); input.disabled = !formAvailable(form);
  }
  $('preview-homeroom').checked = selection.homeroom;
  $('preview-school-links').checked = selection.school_links;
  $('preview-periods').value = selection.periods;
}

function rowClass(ratios) {
  const result = ['preview-row','columns-' + ratios.length];
  if (ratios.join(',') === '40,60') result.push('row-ratio-40-60');
  else if (ratios.join(',') === '55,45') result.push('row-ratio-55-45');
  else if (ratios.some(ratio => Math.abs(ratio - 100 / ratios.length) > .00001)) throw new Error('이 배치의 열 비율을 표시하는 코드가 필요합니다.');
  return result.join(' ');
}

function sourceList(sources) {
  const wrap = node('div', undefined, 'source-links');
  for (const source of sources) wrap.append(node('span', source.title));
  return wrap;
}

function selectedDatabases() { return catalog.databases.filter(db => db.module === 'core' || selection.modules.includes(db.module)); }

function nativeTable(headers, rows = []) {
  const wrap = node('div', undefined, 'native-table-wrap');
  const table = node('table', undefined, 'native-table'), thead = node('thead'), head = node('tr'), body = node('tbody');
  for (const title of headers) { const th = node('th', title); th.scope = 'col'; head.append(th); }
  thead.append(head);
  for (const row of rows) { const tr = node('tr'); for (const value of row) tr.append(node('td', value)); body.append(tr); }
  if (!rows.length) { const tr = node('tr'), td = node('td', '기록이 쌓이면 이곳에서 확인할 수 있어요.', 'empty-row'); td.colSpan = Math.max(headers.length,1); tr.append(td); body.append(tr); }
  table.append(thead, body); wrap.append(table); return wrap;
}

function databaseContent(view) {
  const wrap = node('div');
  if (view.type === 'calendar') {
    const head = node('div', undefined, 'native-calendar-head');
    head.append(node('span', view.range === 'week' ? '주간 보기' : '월간 보기'), node('span', '날짜 속성 · ' + view.date));
    const grid = node('div', undefined, 'native-calendar' + (view.range === 'week' ? ' week' : ''));
    for (const day of ['월','화','수','목','금','토','일']) grid.append(node('span', day));
    for (let i = 0; i < (view.range === 'week' ? 7 : 35); i++) grid.append(node('div'));
    grid.setAttribute('aria-label','일정이 없는 캘린더의 배치 예시');
    wrap.append(head, grid);
  } else if (view.type === 'board') {
    const board = node('div', undefined, 'native-board');
    const db = catalog.databases.find(item => item.key === view.source);
    const groups = db?.properties[view.group]?.options || ['그룹별 보기'];
    for (const group of groups) { const col = node('div', undefined, 'native-board-col'); col.append(node('strong', group), node('span', '기록 없음')); board.append(col); }
    wrap.append(board);
  } else if (view.type === 'gallery') {
    const gallery = node('div', undefined, 'native-gallery');
    for (let i=0;i<3;i++) gallery.append(node('div', '카드 표시 영역'));
    wrap.append(gallery, node('p','기록을 추가하면 카드로 나타납니다.','preview-caption'));
  } else {
    const db = catalog.databases.find(item => item.key === view.source);
    wrap.append(nativeTable(view.show?.length ? view.show : Object.keys(db.properties).slice(0,6)));
  }
  const source = catalog.databases.find(item => item.key === view.source);
  wrap.append(node('p', '연결 원본 · ' + (source?.title || view.source), 'database-origin'));
  return wrap;
}

function databaseSection(pageKey, sectionKey, spec) {
  const wrap = node('div');
  const views = spec.view_keys.filter(key => plan.views[key]);
  if (!views.length) throw new Error('선택한 보기의 원본을 찾을 수 없습니다.');
  const cacheKey = pageKey + ':' + sectionKey;
  const current = views.includes(viewChoices[cacheKey]) ? viewChoices[cacheKey] : (views.includes(spec.default_view) ? spec.default_view : views[0]);
  const tabs = node('div', undefined, 'native-tabs'); tabs.setAttribute('role','tablist'); tabs.setAttribute('aria-label',spec.title + ' 보기');
  const content = node('div'); content.id = 'preview-view-' + pageKey + '-' + sectionKey; content.setAttribute('role','tabpanel');
  function select(key) {
    viewChoices[cacheKey] = key;
    for (const button of tabs.children) { button.setAttribute('aria-selected',String(button.dataset.view === key)); button.tabIndex = button.dataset.view === key ? 0 : -1; }
    content.replaceChildren(databaseContent(plan.views[key]));
    content.setAttribute('aria-labelledby','preview-tab-' + pageKey + '-' + sectionKey + '-' + key);
  }
  for (const key of views) {
    const button = node('button', plan.views[key].name); button.type = 'button'; button.dataset.view = key;
    button.id = 'preview-tab-' + pageKey + '-' + sectionKey + '-' + key;
    button.setAttribute('role','tab'); button.setAttribute('aria-controls',content.id);
    button.addEventListener('click', () => select(key));
    button.addEventListener('keydown', event => {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault(); const index = views.indexOf(key);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? views.length-1 : (index+(event.key === 'ArrowRight' ? 1 : -1)+views.length)%views.length;
      select(views[next]); tabs.children[next].focus();
    });
    tabs.append(button);
  }
  select(current); wrap.append(tabs,content); return wrap;
}

function cards(spec, sectionKey) {
  const labels = {
    quick: [['새 상담 기록','학생과의 대화를 남겨요'],['할 일 정리','업무와 마감을 확인해요'],['수업 준비','교과별 기록을 이어가요'],['자료 찾기','수업·업무 자료를 모아요']],
    notes: [['아침 조회','오늘 전달할 내용을 적어요'],['오후 종례','하루를 돌아보고 내일을 준비해요']],
    para: [['🚀 Projects','끝이 있는 프로젝트'],['🌱 Areas','꾸준히 관리할 영역'],['📁 Resources','다시 찾아볼 자료'],['📦 Archives','완료한 기록의 보관함']],
    school: [],
  };
  if (selection.modules.includes('staff')) labels.school.push(['교직원 연락망','업무 연락처와 담당자']);
  if (selection.modules.includes('accounts')) labels.school.push(['학교 계정','서비스·계정 ID·관리 담당']);
  if (selection.modules.includes('meeting') || selection.school_links) labels.school.push(['학교 업무 링크','회의록·등록할 업무 사이트']);
  const items = labels[sectionKey];
  if (!items?.length || items.length < spec.cards.min || items.length > spec.cards.max) throw new Error('카드 구성과 미리보기 명세가 다릅니다.');
  const group = node('div', undefined, rowClass(items.map(() => 100/items.length))+' notion-cards');
  for (const [title,description] of items) { const card = node('div',undefined,'native-callout');card.append(node('strong',title),node('p',description));group.append(card); }
  return group;
}

function section(pageKey,key,spec) {
  const section = node('section',undefined,'notion-section'); section.dataset.section = key;
  if (key === 'nav') {
    const nav = node('div',undefined,'notion-nav');
    for (const [page,title] of Object.entries(PAGE_NAMES)) { const button=node('button',PAGE_ICONS[page]+' '+title);button.type='button';button.addEventListener('click',()=>showPage(page));nav.append(button); }
    section.append(nav); return section;
  }
  if (key === 'intro') {section.append(node('p','[학년도]학년도 · [선생님] · [담당 교과와 학급]','notion-intro'));return section;}
  if (!spec.toggle) section.append(node('h3',spec.title));
  let content;
  if (spec.view_keys) content = databaseSection(pageKey,key,spec);
  else if (spec.cards) content = cards(spec,key);
  else if (key === 'matrix') {
    content = node('div');
    const table = nativeTable(catalog.contract.timetable.columns,Array.from({length:selection.periods},(_,i)=>[String(i+1),'—','','','','','']));
    table.querySelector('table').classList.add('timetable');
    content.append(table,node('p','같은 주간 시간표를 홈과 교과 페이지에 함께 표시합니다. 교시 시각과 실제 수업은 연결 후 채워집니다.','preview-caption'));
  } else if (key === 'briefing') { content = node('div',undefined,'native-callout');content.append(node('strong','💡 오늘의 교무실 브리핑'),node('p','오늘 챙길 일과 전달 사항을 적는 공간입니다.')); }
  else if (key === 'meals') {content=node('div',undefined,'native-callout');content.append(node('strong','🍚 오늘의 중식'),node('p','학교 급식을 연결하면 중식 메뉴와 기준 날짜를 표시합니다.'));}
  else if (key === 'forms') content = sourceList(FORMS.filter(form=>selection.forms.includes(form.key)).map(form=>({title:form.label.replace(/^[①-⑥]\s*/, '')})));
  else if (key === 'source_list' || key === 'archive_links') content = sourceList(selectedDatabases());
  else if (key === 'document_storage') content=sourceList([{title:'학급 경영 & 학생 상담'},{title:'교과 진도표 & 시간표'},{title:'학사 캘린더 & PARA'},{title:'운영 자료'},{title:'학생 기록'},...(selection.forms.length?[{title:'양식 모음'}]:[])]);
  else content=node('p','기능 설정 후 표시합니다.','preview-caption');
  if (spec.toggle) {const details=node('details',undefined,'native-toggle');details.append(node('summary',spec.title),content);section.append(details);}
  else section.append(content);
  return section;
}

function renderPage(pageKey) {
  const definition = plan.pages[pageKey], page = node('article',undefined,'notebook-page');
  page.id='notebook-'+pageKey;page.setAttribute('role','tabpanel');page.setAttribute('aria-labelledby','page-tab-'+pageKey);
  page.append(node('div',PAGE_ICONS[pageKey],'page-icon'),node('h2',definition.title,'notion-title'));
  for (const row of definition.rows) {
    const group=node('div',undefined,rowClass(row.columns.map(col=>col.ratio)));group.dataset.row=row.id;
    for (const col of row.columns) {const column=node('div',undefined,'preview-column');column.dataset.ratio=String(col.ratio);for(const key of col.sections)column.append(section(pageKey,key,definition.sections[key]));group.append(column);}
    page.append(group);
  }
  return page;
}

function showPage(key,scroll=true) {
  activePage=key;visited.add(key);
  for(const page of Object.keys(PAGE_NAMES)) {
    $('notebook-'+page).hidden=page!==key;
    const button=$('page-tab-'+page);button.setAttribute('aria-selected',String(page===key));button.tabIndex=page===key?0:-1;
    button.querySelector('.visited-mark').textContent=visited.has(page)?'✓':'';
  }
  if(scroll)$('preview-frame').scrollTo({top:0,left:0});
  updateReview();
}

function updateReview() {
  $('page-progress').textContent=visited.size+' / 4 확인';
  $('review-confirm').disabled=visited.size!==4 || !reviewedBundle;
  const ready=visited.size===4 && !!reviewedBundle && $('review-confirm').checked;
  for(const id of ['download-bundle','copy-preview-prompt'])$(id).disabled=!ready;
  $('download-preview-html').disabled=!reviewedBundle || !htmlStyles;
  $('review-progress').textContent=visited.size===4?'네 화면을 살펴봤습니다. 구성이 맞으면 위 항목을 체크해 주세요.':`아직 ${4-visited.size}개 화면이 남았어요. 위쪽 화면 탭을 눌러 확인해 주세요.`;
  if(!ready){$('preview-prompt-output').value='';$('handoff-status').textContent='';}
  else $('preview-prompt-output').value=buildPreviewPrompt(reviewedBundle);
}

async function refresh() {
  const current=++revision;reviewedBundle=null;visited=new Set();viewChoices={};
  $('review-confirm').checked=false;$('preview-error').hidden=true;$('handoff-status').textContent='';$('preview-prompt-output').value='';
  try {
    plan=resolvePreview(catalog,selection);
    controls();$('notebook-pages').replaceChildren(...Object.keys(PAGE_NAMES).map(renderPage));
    showPage(activePage,false);
    const bundle=await buildReviewedBundle(catalog,selection);
    if(current!==revision)return;
    reviewedBundle=bundle;updateReview();
    $('bundle-summary').textContent=`공통 배치 ${catalog.contract.version} · 네 화면 · 설계 ${bundle.bundle_digest.slice(0,12)}`;
  }catch(error){issue(error.message || '미리보기를 준비하지 못했습니다.');$('notebook-pages').replaceChildren();updateReview();}
}

function changed() {
  const allowed=new Set(FORMS.filter(form=>formAvailable(form)).map(form=>form.key));
  const removed=selection.forms.filter(key=>!allowed.has(key));selection.forms=selection.forms.filter(key=>allowed.has(key));
  if(removed.length)message('사용할 수 없는 양식을 제외했습니다. 필요한 기능이나 담임 설정을 먼저 선택해 주세요.');
  else message('구성을 바꾸면 네 화면의 확인 상태가 초기화됩니다.');
  save();void refresh();
}

function download(content,type,name) {
  const url=URL.createObjectURL(new Blob([content],{type})),link=node('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function readyPrompt() {
  if(!reviewedBundle || visited.size!==4 || !$('review-confirm').checked)return null;
  const prompt=buildPreviewPrompt(reviewedBundle);$('preview-prompt-output').value=prompt;return prompt;
}

async function init() {
  const response=await fetch('/preview-catalog.json');if(!response.ok)throw new Error('공통 배치 파일을 불러오지 못했습니다. 새로고침해 주세요.');
  catalog=await response.json();
  try {
    const fromSetup=new URLSearchParams(location.search).get('from')==='setup' ? setupSelection() : null;
    const stored=sessionStorage.getItem(STORAGE_KEY);
    if(fromSetup){selection=fromSetup;save();$('selection-origin').textContent='방금 작성한 설치 설정으로 미리 봅니다.';}
    else if(stored && stored.length<12000){const saved=JSON.parse(stored);if(saved.version===1){selection=createSelection(saved.selection);resolvePreview(catalog,selection);$('selection-origin').textContent='이 탭에서 살펴보던 구성입니다.';}}
    else {const loaded=setupSelection();if(loaded){selection=loaded;$('selection-origin').textContent='같은 탭에서 작성한 설치 설정을 사용합니다.';}}
  }catch{selection=createSelection();message('저장된 설정을 읽지 못해 기본 구성으로 시작합니다.');}
  for(const item of MODULES){const label=node('label',undefined,'preview-check'),input=node('input');input.type='checkbox';input.id='preview-module-'+item.key;label.append(input,node('span',item.label));input.addEventListener('change',()=>{selection.modules=input.checked?[...selection.modules,item.key].sort():selection.modules.filter(key=>key!==item.key);changed();});$('module-options').append(label);}
  for(const form of FORMS){const label=node('label',undefined,'preview-check'),input=node('input');input.type='checkbox';input.id='preview-form-'+form.key;label.append(input,node('span',form.label));input.addEventListener('change',()=>{selection.forms=input.checked?[...selection.forms,form.key].sort():selection.forms.filter(key=>key!==form.key);changed();});$('form-options').append(label);}
  for(const [id,key] of [['preview-homeroom','homeroom'],['preview-school-links','school_links']])$(id).addEventListener('change',()=>{selection[key]=$(id).checked;changed();});
  $('preview-periods').addEventListener('change',()=>{const value=Number($('preview-periods').value);if(!Number.isInteger(value)||value<1||value>20){$('preview-periods').value=selection.periods;message('교시 수는 1~20 사이 정수로 입력해 주세요.');return;}selection.periods=value;changed();});
  const pageKeys=Object.keys(PAGE_NAMES);
  for(const [key,name] of Object.entries(PAGE_NAMES)){
    const button=node('button',name);button.type='button';button.id='page-tab-'+key;button.setAttribute('role','tab');button.setAttribute('aria-controls','notebook-'+key);button.append(node('span','','visited-mark'));
    button.addEventListener('click',()=>showPage(key));
    button.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const index=pageKeys.indexOf(key),next=event.key==='Home'?0:event.key==='End'?3:(index+(event.key==='ArrowRight'?1:-1)+4)%4;showPage(pageKeys[next]);$('page-tab-'+pageKeys[next]).focus();});$('preview-tabs').append(button);
  }
  $('review-confirm').addEventListener('change',updateReview);
  $('load-setup').addEventListener('click',()=>{try{const loaded=setupSelection();if(!loaded){message('이 탭에 설치 답변이 없어요. 상단의 설치 정보 입력에서 먼저 작성할 수 있습니다.');return;}selection=loaded;message('기능·양식·교시 설정만 불러왔습니다. 학교·개인 정보는 이 미리보기에 가져오지 않습니다.');save();void refresh();}catch{message('설치 답변을 읽지 못했습니다. 설정을 확인해 주세요.');}});
  $('load-bundle').addEventListener('change',async()=>{
    const file=$('load-bundle').files[0];if(!file)return;
    try{if(file.size>2*1024*1024)throw new Error('2MB 이하의 배치 JSON 파일을 선택해 주세요.');const bundle=JSON.parse(await file.text()),report=await verifyReviewedBundle(catalog,bundle);if(!report.valid)throw new Error('현재 공통 배치와 맞지 않거나 변경된 파일입니다. 새로 미리 보고 저장해 주세요.');selection=createSelection(bundle.selection);message('저장한 배치를 불러왔습니다. 적용 요청 전에 네 화면을 다시 확인해 주세요.');save();await refresh();}catch(error){message(error.message || '배치 파일을 읽지 못했습니다.');}finally{$('load-bundle').value='';}
  });
  $('download-bundle').addEventListener('click',()=>{if(!readyPrompt())return;download(JSON.stringify(reviewedBundle,null,2)+'\n','application/json;charset=utf-8','reviewed-layout.json');$('handoff-status').textContent='배치 파일 저장을 요청했습니다. 이어서 요청문을 복사하고, AI에게 파일도 함께 첨부해 주세요.';});
  $('copy-preview-prompt').addEventListener('click',async()=>{const prompt=readyPrompt();if(!prompt)return;try{await navigator.clipboard.writeText(prompt);if(prompt!==buildPreviewPrompt(reviewedBundle))return;$('handoff-status').textContent='요청문을 복사했습니다. reviewed-layout.json 파일을 함께 첨부해 AI에게 전달하세요.';}catch{if(!reviewedBundle)return;$('preview-prompt-details').open=true;$('preview-prompt-output').focus();$('preview-prompt-output').select();$('handoff-status').textContent='자동 복사가 되지 않아 요청문을 선택했습니다. Ctrl+C 또는 ⌘C로 복사해 주세요.';}});
  $('download-preview-html').addEventListener('click',()=>{
    if(!reviewedBundle||!htmlStyles)return;
    const container=node('div');
    // Exports always use the reviewed defaults, not transient tab/toggle exploration.
    const oldChoices=viewChoices;viewChoices={};
    for(const key of pageKeys){const page=renderPage(key);page.removeAttribute('role');page.removeAttribute('aria-labelledby');container.append(page);}
    viewChoices=oldChoices;
    for(const button of container.querySelectorAll('button'))button.replaceWith(node('span',button.textContent));
    const json=node('pre',JSON.stringify(reviewedBundle,null,2),'bundle-code');
    const heading=node('header',undefined,'export-heading');heading.append(node('h1','교무수첩 · 검토용 배치'),node('p','이 HTML은 배치 미리보기입니다. 실제 기록·개인 설정은 포함하지 않습니다. Notion에는 같은 reviewed-layout.json과 적용 요청문을 사용하세요.'),node('p','설계 '+reviewedBundle.bundle_digest));
    const details=node('details');details.append(node('summary','동일한 배치 JSON 확인'),json);
    const html='<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>교무수첩 배치 미리보기</title><style>'+htmlStyles+'</style></head><body class="export-preview">'+heading.outerHTML+container.outerHTML+details.outerHTML+'<footer>made by 여광재(온양고) · made with <a href="https://dorms.school">DoRms</a></footer></body></html>';
    download(html,'text/html;charset=utf-8','teacher-planner-preview.html');$('handoff-status').textContent='네 화면의 기본 배치를 HTML로 저장했습니다. 이 파일은 보관·열람용입니다.';
  });
  await refresh();$('preview-loading').hidden=true;$('preview-shell').hidden=false;
  try{const css=await Promise.all(['/setup.css','/preview.css'].map(async path=>{const r=await fetch(path);if(!r.ok)throw new Error('style');return r.text();}));htmlStyles=css.join('\n');updateReview();}catch{message('HTML 저장용 스타일을 불러오지 못했습니다. 배치 파일과 요청문은 사용할 수 있습니다.');}
}
init().catch(error=>{$('preview-loading').hidden=true;issue(error.message || '미리보기를 준비하지 못했습니다. 새로고침해 주세요.');});
