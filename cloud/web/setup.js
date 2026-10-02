import {QUESTIONNAIRE_VERSION, QUESTIONS, MODULES, FORMS, createAnswers, eligibleForms, validateStep, buildPrompt} from './setup-model.mjs';

const $ = id => document.getElementById(id);
const STORAGE_KEY = 'teacher-planner-setup-v1';
const STEPS = ['학교와 학년도', '수업과 설치 위치', '기능과 양식', '시간표와 학교 소식', '마무리 설정'];
const SHORT_STEPS = ['학교 · 학년도', '수업 · 위치', '기능 · 양식', '학교 연동', '마무리'];
let answers = createAnswers(), step = 0, highestStep = 0, errors = [];

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function restore() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw || raw.length > 40000) return;
    const saved = JSON.parse(raw);
    // Keep v1 drafts in the same tab; the allowlist below drops the old semester answer.
    if (![1, QUESTIONNAIRE_VERSION].includes(saved.version) || !saved.answers || typeof saved.answers !== 'object') return;
    const defaults = createAnswers();
    for (const key of Object.keys(defaults)) {
      const value = saved.answers[key];
      if (Array.isArray(defaults[key]) && Array.isArray(value)) answers[key] = value.filter(v => typeof v === 'string').slice(0, 6);
      else if (typeof value === typeof defaults[key]) answers[key] = typeof value === 'string' ? value.slice(0, 6000) : value;
    }
    step = Number.isInteger(saved.step) ? Math.max(0, Math.min(saved.step, 4)) : 0;
    highestStep = Number.isInteger(saved.highestStep) ? Math.max(step, Math.min(saved.highestStep, 4)) : step;
    $('draft-status').textContent = saved.version === 1 ? '기존 답변을 불러왔습니다. 학기 선택 없이 학년도별 수첩으로 준비합니다.' : '이 탭에 저장된 답변을 불러왔습니다.';
  } catch { $('draft-status').textContent = '이 브라우저에서는 임시 저장을 사용할 수 없습니다.'; }
}

function save() {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({version: QUESTIONNAIRE_VERSION, answers, step, highestStep}));
    $('draft-status').textContent = '이 탭에 임시 저장됨 · 입력 내용은 서버로 보내지 않습니다.';
  } catch { $('draft-status').textContent = '임시 저장을 사용할 수 없습니다. 이 화면에서 계속 작성해 주세요.'; }
}

function setAnswer(field, value, redraw = false) {
  answers[field] = value;
  $('change-notice').textContent = '';
  if (field === 'modules' || field === 'moduleChoice' || field === 'homeroom' || field === 'homeroomClass') {
    const eligible = new Set(eligibleForms(answers).map(item => item.key));
    const removed = answers.forms.filter(key => !eligible.has(key));
    if (removed.length) {
      answers.forms = answers.forms.filter(key => eligible.has(key));
      $('change-notice').textContent = '설정이 바뀌어 사용할 수 없는 양식을 선택에서 제외했습니다. 기능과 양식 단계에서 다시 확인해 주세요.';
    }
  }
  if (!answers.forms.length) answers.formMode = '';
  // A generated prompt is always rebuilt from current answers, never reused after an edit.
  $('prompt-output').value = '';
  $('copy-status').textContent = '';
  errors = errors.filter(error => error.field !== field);
  save();
  renderSummary();
  if (redraw) {
    const focusId = document.activeElement?.id;
    renderQuestions();
    showErrors();
    if (focusId) $(focusId)?.focus({preventScroll: true});
  } else {
    const control = $(field);
    control?.removeAttribute('aria-invalid');
    $(`${field}-error`)?.remove();
    showErrors();
  }
}

function question(id, title, compact = false) {
  const q = QUESTIONS[id];
  const section = el('section', undefined, 'question');
  const heading = el('div', undefined, 'question-header');
  heading.append(el('span', q.id, 'question-id'), el('h3', title));
  section.append(heading, el('p', q.prompt.split('①')[0].trim(), 'question-prompt'));
  if (compact) {
    const box = el('div', undefined, 'sub-question');
    box.append(section);
    return {container: box, body: section};
  }
  return {container: section, body: section};
}

function field(key, label, {placeholder = '', optional = false, type = 'text', options, help, disabled = false, maxLength = 200, rows = 3} = {}) {
  const box = el('div', undefined, 'field');
  const caption = el('label', label); caption.htmlFor = key;
  if (optional) caption.append(el('span', '선택', 'optional'));
  let input;
  if (options) {
    input = el('select');
    for (const item of options) {
      const option = el('option', typeof item === 'string' ? item : item.label);
      option.value = typeof item === 'string' ? item : item.value;
      input.append(option);
    }
  } else if (type === 'textarea') {
    input = el('textarea'); input.rows = rows;
  } else {
    input = el('input'); input.type = type;
    if (type === 'number') {
      input.min = key === 'academicYear' ? '2000' : '1';
      input.max = key === 'academicYear' ? '2200' : '20';
      input.step = '1';
    }
  }
  input.id = key; input.name = key; input.value = answers[key]; input.disabled = disabled;
  input.maxLength = maxLength; input.placeholder = placeholder;
  if (!optional && !disabled) input.setAttribute('aria-required', 'true');
  if (help) { input.setAttribute('aria-describedby', `${key}-help`); }
  input.addEventListener(options ? 'change' : 'input', () => setAnswer(key, input.value));
  box.append(caption, input);
  if (help) { const hint = el('p', help, 'field-help'); hint.id = `${key}-help`; box.append(hint); }
  return box;
}

function grid(...children) { const node = el('div', undefined, 'field-grid'); node.append(...children); return node; }

function check(key, label) {
  const wrapper = el('label', undefined, 'checkline');
  const input = el('input'); input.type = 'checkbox'; input.id = key; input.checked = answers[key];
  input.addEventListener('change', () => setAnswer(key, input.checked, true));
  wrapper.append(input, el('span', label)); return wrapper;
}

function radios(key, options, {inline = false, redraw = false, label = ''} = {}) {
  const group = el('div', undefined, `radio-group${inline ? ' inline' : ''}`);
  group.setAttribute('role', 'radiogroup'); group.setAttribute('aria-label', label || key); group.id = key;
  for (const option of options) {
    const item = el('label', undefined, 'option');
    const input = el('input'); input.type = 'radio'; input.name = key; input.value = option.value;
    input.id = `${key}-${option.value}`; input.checked = answers[key] === option.value;
    input.addEventListener('change', () => { if (input.checked) setAnswer(key, option.value, redraw); });
    const copy = el('span'); copy.append(el('span', option.label, 'option-label'));
    if (option.description) copy.append(el('span', option.description, 'option-desc'));
    item.append(input, copy); group.append(item);
  }
  return group;
}

function choices(type) {
  const isForms = type === 'forms', catalog = isForms ? FORMS : MODULES;
  const choiceField = isForms ? 'formChoice' : 'moduleChoice';
  const allowed = new Set((isForms ? eligibleForms(answers) : catalog).map(item => item.key));
  const group = el('div');
  const tools = el('div', undefined, 'choice-tools');
  const actions = [
    {value: 'select', label: isForms ? '사용 가능한 양식 모두' : '모두 선택'},
    {value: 'none', label: '없음'}, {value: 'later', label: '나중에'}
  ];
  for (const action of actions) {
    const button = el('button', action.label); button.type = 'button';
    button.id = `${type}-${action.value}`;
    const allSelected = allowed.size > 0 && answers[type].length === allowed.size;
    const active = answers[choiceField] === action.value && (action.value !== 'select' || allSelected);
    button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
    button.addEventListener('click', () => {
      answers[type] = action.value === 'select' ? catalog.filter(item => allowed.has(item.key)).map(item => item.key) : [];
      setAnswer(choiceField, action.value, true);
    });
    tools.append(button);
  }
  const options = el('div', undefined, 'option-grid'); options.id = type;
  options.setAttribute('role', 'group'); options.setAttribute('aria-label', isForms ? '빈 기록 양식 선택' : '추가 기능 선택');
  for (const item of catalog) {
    const option = el('label', undefined, 'option');
    const input = el('input'); input.type = 'checkbox'; input.id = `${type}-${item.key}`;
    input.checked = answers[type].includes(item.key); input.disabled = !allowed.has(item.key);
    input.addEventListener('change', () => {
      answers[choiceField] = 'select';
      setAnswer(type, input.checked ? [...answers[type], item.key] : answers[type].filter(key => key !== item.key), true);
    });
    const text = el('span'); text.append(el('span', item.label, 'option-label'));
    const description = !allowed.has(item.key) ? (item.homeroom ? '담임 학급 설정이 필요합니다.' : `${MODULES.find(m => m.key === item.module)?.label.replace(/^[①-⑥]\s*/, '') || '해당 기능'}을 먼저 선택해 주세요.`) : item.description;
    if (description) text.append(el('span', description, 'option-desc'));
    option.append(input, text); options.append(option);
  }
  group.append(tools, options);
  return group;
}

function renderQuestions() {
  const host = $('questions'); host.replaceChildren();
  if (step === 0) {
    const school = question('Q01', '학교를 알려주세요');
    school.body.append(grid(field('region', '지역', {placeholder: '예: 충남', disabled: answers.schoolDeferred}), field('schoolName', '학교명', {placeholder: '예: 예시고등학교', disabled: answers.schoolDeferred})), check('schoolDeferred', '지역·학교명은 나중에 입력할게요'), field('schoolLevel', '학교급', {options: [{value: '', label: '학교급 선택'}, '초등학교', '중학교', '고등학교', '특수학교', '기타']}));
    const term = question('Q02', '어느 학년도를 준비하시나요?');
    term.body.append(field('academicYear', '학년도', {type: 'number', placeholder: '예: 2026', help: '한 학년도에 수첩 하나를 사용해요. 3월부터 다음 해 2월까지, 1·2학기 기록을 함께 관리합니다.'}), field('teacher', '표시할 이름 또는 별칭', {optional: true, placeholder: '교사', maxLength: 100, help: '실명 대신 별칭을 사용해도 괜찮아요. 시간은 한국 시간으로 표시합니다.'}));
    host.append(school.container, term.container);
  } else if (step === 1) {
    const teaching = question('Q03', '담당 수업과 담임 학급');
    teaching.body.append(field('subjects', '담당 교과', {placeholder: '예: 통합과학, 물리학', maxLength: 1000}), field('classes', '담당 학년·반', {type: 'textarea', placeholder: '예: 1학년 1~3반, 2학년 1반\n교과마다 담당 반이 다르면 함께 적어주세요.', maxLength: 2000}), radios('homeroom', [{value: 'yes', label: '담임'}, {value: 'no', label: '비담임'}], {inline: true, redraw: true, label: '담임 여부'}));
    if (answers.homeroom === 'yes') teaching.body.append(field('homeroomClass', '담임 학급', {placeholder: '예: 1학년 2반'}));
    const location = question('Q04', 'Notion에서 만들 위치');
    location.body.append(radios('notionLocation', [{value: 'link', label: '상위 페이지 링크 입력'}, {value: 'help', label: '위치 선택 도움받기'}], {redraw: true, label: 'Notion 생성 위치'}));
    if (answers.notionLocation === 'link') location.body.append(field('notionUrl', '상위 Notion 페이지 주소', {type: 'url', placeholder: 'https://www.notion.so/…', maxLength: 2048}));
    location.body.append(radios('sharing', [{value: 'private', label: '개인용'}, {value: 'staff', label: '지정한 교직원과 함께'}], {inline: true, label: '수첩 사용 범위'}), el('p', '공유 권한은 자동으로 변경하지 않습니다. 실제 페이지 접근 범위는 설치할 때 확인해요.', 'hint'));
    host.append(teaching.container, location.container);
  } else if (step === 2) {
    const modules = question('Q05', '필요한 기능만 더하세요');
    modules.body.append(choices('modules'));
    const forms = question('Q06', '자주 쓰는 기록 양식');
    forms.body.append(choices('forms'));
    if (answers.formChoice === 'select' && answers.forms.length) {
      const mode = question('Q06N', '양식 사용 방식', true);
      mode.body.append(radios('formMode', QUESTIONS.Q06N.options, {label: '양식 사용 방식'}));
      forms.body.append(mode.container);
    }
    host.append(modules.container, forms.container);
  } else if (step === 3) {
    const timetable = question('Q07', '시간표를 준비하는 방법');
    timetable.body.append(radios('timetable', QUESTIONS.Q07.options, {redraw: true, label: '시간표 준비 방식'}));
    if (answers.timetable === 'comcigan') {
      const codes = question('Q07C', '컴시간 학교와 교사', true);
      codes.body.append(grid(field('comciganSchoolCode', '컴시간 학교 코드', {disabled: answers.comciganHelp, placeholder: '학교 코드'}), field('comciganTeacherId', '선생님 번호', {disabled: answers.comciganHelp, placeholder: '교사 번호'})), check('comciganHelp', '번호 확인에 도움이 필요해요'), el('p', '학교와 선생님 번호는 NEIS 코드와 달라요. 컴시간 연동은 비공식 웹 조회이며, 아래 하루 한 번 자동 갱신에는 포함되지 않습니다.', 'hint'));
      timetable.body.append(codes.container);
    }
    if (answers.timetable === 'file') {
      const file = question('Q07F', '파일은 AI에게 전달해 주세요', true);
      file.body.append(el('p', '이 웹에서는 파일을 업로드하지 않습니다. 요청문과 함께 AI에 파일을 첨부하세요. 파일이 없다면 먼저 빈 표를 만들고 가져오기를 나중에 진행할 수 있습니다.', 'hint'));
      timetable.body.append(file.container);
    }
    if (answers.timetable) {
      const times = question('Q07T', '표시할 교시와 시각', true);
      times.body.append(field('periods', '하루 최대 교시', {type: 'number'}), field('periodTimes', '교시별 시작·종료 시각', {type: 'textarea', optional: true, placeholder: '예: 1교시 08:40~09:30\n2교시 09:40~10:30', maxLength: 2000, help: '비워 두면 시각을 추정하지 않아요. 파일 가져오기에는 실제 시작·종료 시각이 필요합니다.'}));
      timetable.body.append(times.container);
    }
    const neis = question('Q08', '오늘의 중식과 학사일정');
    neis.body.append(radios('neis', QUESTIONS.Q08.options.map(option => ({...option, description: option.value === 'daily' ? '수첩 생성 후 공용 서비스의 Notion 연결을 승인해요.' : ''})), {redraw: true, label: '급식과 학사일정 가져오기'}));
    if (answers.neis && answers.neis !== 'later' && (answers.schoolDeferred || !answers.schoolName.trim() || !answers.region.trim())) {
      const missing = el('div', undefined, 'callout'); missing.append(el('strong', '학교 정보를 먼저 알려주세요.'));
      const button = el('button', '학교와 학년도로 돌아가기', 'button text-button'); button.type = 'button'; button.addEventListener('click', () => go(0));
      missing.append(el('p', '중식과 학사일정을 가져오려면 지역과 학교명이 필요합니다.'), button); neis.body.append(missing);
    }
    host.append(timetable.container, neis.container);
  } else {
    const demo = question('Q09', '빈 수첩 또는 가상 예시');
    demo.body.append(radios('demo', QUESTIONS.Q09.options.map(option => ({...option, description: option.value === 'empty' ? '선생님의 기록으로 채워 갈 수 있어요.' : '실제 학생 정보가 아닌 가상 기록이 포함됩니다.'})), {label: '처음 넣을 기록'}));
    const title = question('Q10', '나만의 이름과 바로가기');
    title.body.append(field('title', '수첩 제목', {optional: true, maxLength: 100, placeholder: '교무수첩 데스크'}), field('bookmarks', '자주 쓰는 업무 사이트', {type: 'textarea', optional: true, maxLength: 3000, placeholder: '예: 학교 홈페이지 https://…\n한 줄에 이름과 주소를 적어주세요.', help: '토큰이나 비밀번호가 포함된 주소는 입력하지 마세요.'}));
    const next = el('div', undefined, 'callout'); next.append(el('strong', '여기까지 입력하면 준비 끝!'), el('p', '다음 화면에서 선택 내용을 확인하고 AI용 요청문을 복사할 수 있습니다. Notion 페이지 생성은 요청문을 받은 AI가 진행해요.'));
    host.append(demo.container, title.container, next);
  }
}

function showErrors() {
  const box = $('form-errors'); box.replaceChildren(); box.hidden = errors.length === 0;
  if (!errors.length) return;
  box.append(el('strong', '다음 항목을 확인해 주세요.'));
  const list = el('ul');
  for (const error of errors) {
    const item = el('li'), button = el('button', error.message); button.type = 'button';
    button.addEventListener('click', () => {
      let target = $(error.field);
      if (!target && error.field === 'moduleChoice') target = $('modules');
      if (!target && error.field === 'formChoice') target = $('forms');
      if (target && !target.matches('input, select, textarea, button')) target = target.querySelector('input:not(:disabled), button');
      if (target) target.focus();
      else if (['region','schoolName','schoolDeferred'].includes(error.field)) go(0);
    });
    item.append(button); list.append(item);
    const input = $(error.field);
    if (input) input.setAttribute('aria-invalid', 'true');
  }
  box.append(list);
}

function renderSummary() {
  $('preview-title').textContent = answers.title.trim() || '나의 교무수첩';
  $('preview-school').textContent = [answers.schoolDeferred ? '' : answers.schoolName, answers.academicYear ? `${answers.academicYear}학년도` : ''].filter(Boolean).join(' · ') || '학교와 학년도를 선택해 주세요.';
  const summary = $('selection-summary'); summary.replaceChildren();
  for (const line of ['학생 명단 · 상담 기록', '할 일 · 주간/월간 캘린더', '시간표 · 수업 진도 · PARA']) summary.append(el('div', line, 'selection-item'));
  const selected = answers.moduleChoice === 'select' ? answers.modules.length : 0;
  const selectedForms = answers.formChoice === 'select' ? answers.forms.length : 0;
  summary.append(el('div', `추가 기능 ${selected}개 · 기록 양식 ${selectedForms}개`, 'selection-count'));
}

function render() {
  $('wizard').hidden = false; $('result').hidden = true;
  $('step-title').textContent = STEPS[step];
  $('step-count').textContent = `STEP ${String(step + 1).padStart(2,'0')} / 05`;
  $('progress').value = step + 1;
  $('next').textContent = step === 4 ? '요청문 만들기 →' : '다음 단계 →';
  $('previous').disabled = step === 0;
  const nav = $('step-nav'); nav.replaceChildren();
  for (const [index, title] of SHORT_STEPS.entries()) {
    const button = el('button', undefined, 'step-button'); button.type = 'button';
    const complete = index < step;
    button.classList.toggle('active', index === step); button.classList.toggle('complete', complete);
    button.disabled = index > highestStep;
    if (index === step) button.setAttribute('aria-current', 'step');
    button.append(el('span', complete ? '✓' : String(index + 1).padStart(2,'0'), 'number'), el('span', title));
    button.addEventListener('click', () => go(index)); nav.append(button);
  }
  renderQuestions(); renderSummary(); showErrors();
}

function go(index) { step = index; errors = []; save(); render(); $('step-title').focus(); }

function summaryRow(label, value) { const row = el('div'); row.append(el('dt', label), el('dd', value)); return row; }

function showResult(prompt) {
  $('wizard').hidden = true; $('result').hidden = false;
  const summary = $('result-summary'); summary.replaceChildren();
  summary.append(summaryRow('학교 · 학년도', `${answers.schoolDeferred ? '학교는 나중에 확인' : answers.schoolName} · ${answers.academicYear}학년도`), summaryRow('담당 수업', `${answers.subjects} / ${answers.classes}`), summaryRow('추가 기능', answers.modules.length ? MODULES.filter(m => answers.modules.includes(m.key)).map(m => m.label.replace(/^[①-⑥]\s*/, '')).join(', ') : '없음'), summaryRow('기록 양식', answers.forms.length ? FORMS.filter(f => answers.forms.includes(f.key)).map(f => f.label.replace(/^[①-⑥]\s*/, '')).join(', ') : '없음'), summaryRow('학교 소식', {daily:'중식·학사일정 하루 한 번 갱신 요청',meal:'필요할 때 중식',calendar:'필요할 때 학사일정','manual-both':'필요할 때 중식·학사일정',later:'나중에 연결'}[answers.neis]), summaryRow('설치 위치', answers.notionLocation === 'help' ? 'AI와 위치 선택' : answers.notionUrl));
  $('prompt-output').value = prompt;
  $('result-title').focus();
}

$('setup-form').addEventListener('submit', event => {
  event.preventDefault(); errors = validateStep(answers, step);
  if (errors.length) { showErrors(); $('form-errors').setAttribute('tabindex', '-1'); $('form-errors').focus(); return; }
  if (step < 4) { highestStep = Math.max(highestStep, step + 1); go(step + 1); return; }
  for (let index = 0; index < 5; index++) {
    const remaining = validateStep(answers, index);
    if (remaining.length) { step = index; errors = remaining; render(); $('step-title').focus(); return; }
  }
  try { showResult(buildPrompt(answers)); }
  catch (error) { errors = error.errors || [{field: 'title', message: '입력 내용을 다시 확인해 주세요.'}]; showErrors(); }
});
$('previous').addEventListener('click', () => { if (step > 0) go(step - 1); });
$('edit-answers').addEventListener('click', () => go(0));
$('copy-prompt').addEventListener('click', async () => {
  const output = $('prompt-output');
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable');
    await navigator.clipboard.writeText(output.value);
    $('copy-status').textContent = '복사했습니다. Notion을 편집할 수 있는 AI에 붙여넣어 주세요.';
  } catch {
    document.querySelector('.prompt-details').open = true;
    output.focus(); output.select();
    $('copy-status').textContent = '자동 복사를 사용할 수 없습니다. 선택된 요청문을 직접 복사해 주세요. (Ctrl+C / ⌘C)';
  }
});
$('reset').addEventListener('click', () => { $('reset-confirm').hidden = false; $('reset-yes').focus(); });
$('reset-no').addEventListener('click', () => { $('reset-confirm').hidden = true; $('reset').focus(); });
$('reset-yes').addEventListener('click', () => {
  answers = createAnswers(); step = 0; highestStep = 0; errors = [];
  try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* Clearing the current form remains available. */ }
  $('reset-confirm').hidden = true; $('prompt-output').value = ''; $('copy-status').textContent = ''; $('change-notice').textContent = '';
  $('draft-status').textContent = '입력 내용과 임시 저장을 지웠습니다.';
  render(); $('step-title').focus();
});
restore(); render();
