import {FEATURE_CATALOG, LOCATIONS, VIEWS, createEditAnswers, applyFeature, validateEditAnswers, buildEditPrompt} from './edit-model.mjs';

const $ = id => document.getElementById(id);
const STORAGE_KEY = 'teacher-planner-edit-v1';
const limits = {notebookUrl:2000, goal:1500, fields:2000, details:4000};
const fields = ['notebookUrl', 'goal', 'location', 'view', 'fields', 'details'];
let answers = createEditAnswers(), drafts = {}, generatedPrompt = '';

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function validDraft(id, raw) {
  const clean = applyFeature(id);
  for (const [key, max] of Object.entries(limits)) {
    if (typeof raw?.[key] === 'string') clean[key] = raw[key].slice(0, max);
  }
  if (LOCATIONS.some(item => item.id === raw?.location)) clean.location = raw.location;
  if (VIEWS.some(item => item.id === raw?.view)) clean.view = raw.view;
  return clean;
}

function restore() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw || raw.length > 100000) return;
    const saved = JSON.parse(raw);
    if (saved?.version !== 1) return;
    for (const feature of FEATURE_CATALOG) {
      if (saved.drafts?.[feature.id]) drafts[feature.id] = validDraft(feature.id, saved.drafts[feature.id]);
    }
    if (FEATURE_CATALOG.some(item => item.id === saved.answers?.featureId)) answers = validDraft(saved.answers.featureId, saved.answers);
    $('edit-draft-status').textContent = '이 탭에 저장된 초안을 불러왔습니다.';
  } catch { $('edit-draft-status').textContent = '임시 저장을 사용할 수 없습니다. 떠나기 전에 요청문을 저장해 주세요.'; }
}

function save() {
  drafts[answers.featureId] = {...answers};
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({version:1, answers, drafts}));
    $('edit-draft-status').textContent = '이 탭에 임시 저장됨 · 입력 내용은 서버로 보내지 않습니다.';
  } catch { $('edit-draft-status').textContent = '임시 저장을 사용할 수 없습니다. 떠나기 전에 요청문을 저장해 주세요.'; }
}

function invalidate() {
  generatedPrompt = '';
  $('edit-prompt-output').value = '';
  $('prompt-result').hidden = true;
  $('ready-hint').hidden = false;
  $('edit-copy-status').textContent = '';
  $('edit-errors').hidden = true;
  for (const key of fields) $(key).removeAttribute('aria-invalid');
}

function renderSummary() {
  const feature = FEATURE_CATALOG.find(item => item.id === answers.featureId);
  $('request-heading').textContent = feature.title;
  $('request-description').textContent = feature.description;
  $('summary-location').textContent = LOCATIONS.find(item => item.id === answers.location)?.label;
  $('summary-view').textContent = VIEWS.find(item => item.id === answers.view)?.label;
  $('behavior-summary').replaceChildren(...feature.summary.map(text => el('li', text)));
  $('custom-summary').textContent = [answers.goal, answers.details].filter(value => value.trim()).join('\n');
  $('custom-summary-wrap').hidden = !$('custom-summary').textContent;
}

function renderAnswers() {
  for (const key of fields) $(key).value = answers[key];
  for (const input of document.querySelectorAll('input[name="featureId"]')) input.checked = input.value === answers.featureId;
  const custom = answers.featureId === 'custom';
  $('goal-optional').textContent = custom ? '필수' : '선택';
  $('goal').required = custom;
  const examples = {
    custom: '예: 동아리 활동을 날짜별로 기록하고, 학생별 참여 내역을 볼 수 있게 해 주세요.',
    'counseling-history': '예: 학생 페이지에서 아직 끝내지 않은 상담 후속 조치도 함께 보고 싶어요.',
    'missing-submissions': '예: 우리 반의 이번 주 과제만 보고, 제출하면 이 목록에서 빠지게 해 주세요.',
    'weekly-tasks': '예: P1 업무를 맨 위에 보여주고, 마감이 지난 일도 따로 보고 싶어요.',
    'meeting-actions': '예: 학년 협의회에서 나온 할 일을 회의록 아래에서 담당자별로 보고 싶어요.',
    'reusable-form': '예: 학생 상담일지 양식에 상담 목적, 관찰 내용, 합의 사항, 다음 확인일을 넣어 주세요.',
    'dashboard-layout': '예: 홈에서 이번 주 업무를 먼저 보이게 하고, 관리용 목록은 접어 주세요.',
    'lesson-progress': '예: 2학년 문학의 반별 완료 차시와 다음에 진행할 단원을 비교하고 싶어요.',
  };
  $('goal').placeholder = examples[answers.featureId];
  $('goal-help').textContent = custom ? '어떤 일을 기록하고, 무엇을 확인하고 싶은지 적어 주세요. 실제 학생 정보는 필요하지 않습니다.' : '기본 기능에 덧붙일 내용만 적어도 됩니다. 실제 학생 이름·상담 내용 대신 필요한 기능을 적어 주세요.';
  renderSummary();
}

function selectFeature(id) {
  if (id === answers.featureId) return;
  drafts[answers.featureId] = {...answers};
  const notebookUrl = answers.notebookUrl;
  answers = {...(drafts[id] || applyFeature(id)), notebookUrl};
  invalidate();
  renderAnswers();
  save();
}

function renderCatalog() {
  for (const feature of FEATURE_CATALOG) {
    const label = el('label', undefined, 'feature-choice');
    const radio = el('input'); radio.type = 'radio'; radio.name = 'featureId'; radio.value = feature.id; radio.id = `feature-${feature.id}`;
    const icon = el('span', feature.icon, 'feature-icon'); icon.setAttribute('aria-hidden', 'true');
    const copy = el('span', undefined, 'feature-copy');
    const title = el('span', feature.title, 'feature-title'); title.id = `${radio.id}-title`;
    const description = el('span', feature.description, 'feature-description'); description.id = `${radio.id}-description`;
    radio.setAttribute('aria-labelledby', title.id); radio.setAttribute('aria-describedby', description.id);
    copy.append(title, description); label.append(radio, icon, copy);
    radio.addEventListener('change', () => { if (radio.checked) selectFeature(feature.id); });
    $('feature-options').append(label);
  }
  for (const [id, options] of [['location', LOCATIONS], ['view', VIEWS]]) {
    for (const item of options) { const option = el('option', item.label); option.value = item.id; $(id).append(option); }
  }
}

function showErrors(errors) {
  const list = el('ul');
  const focus = key => {
    if (['location', 'view', 'fields', 'details'].includes(key)) $('fine-tune').open = true;
    ($(key) || $('feature-options').querySelector('input')).focus();
  };
  for (const error of errors) {
    const item = el('li'); const button = el('button', error.message); button.type = 'button';
    button.addEventListener('click', () => focus(error.field)); item.append(button); list.append(item);
    $(error.field)?.setAttribute('aria-invalid', 'true');
  }
  $('edit-errors').replaceChildren(el('strong', '이 부분을 확인해 주세요.'), list);
  $('edit-errors').hidden = false;
  focus(errors[0].field);
}

function generate() {
  const errors = validateEditAnswers(answers);
  if (errors.length) { showErrors(errors); return; }
  generatedPrompt = buildEditPrompt(answers);
  $('edit-prompt-output').value = generatedPrompt;
  $('prompt-result').hidden = false;
  $('ready-hint').hidden = true;
  $('edit-errors').hidden = true;
  $('edit-copy-status').textContent = '이제 복사해서 사용하는 AI에게 붙여넣으세요.';
  $('prompt-details').open = false;
  $('request-heading').focus({preventScroll:true});
  $('request-heading').scrollIntoView({block:'start', behavior:'auto'});
}

$('edit-form').addEventListener('submit', event => { event.preventDefault(); generate(); });
for (const key of fields) {
  $(key).addEventListener(['view', 'location'].includes(key) ? 'change' : 'input', () => {
    answers[key] = $(key).value;
    invalidate(); save(); renderSummary();
  });
}
$('copy-edit-prompt').addEventListener('click', async () => {
  if (!generatedPrompt) return;
  const promptToCopy = generatedPrompt;
  try {
    await navigator.clipboard.writeText(promptToCopy);
    if (generatedPrompt === promptToCopy) $('edit-copy-status').textContent = '복사했어요. Notion에 연결된 AI 대화창에 붙여넣으세요.';
  } catch {
    if (generatedPrompt !== promptToCopy) return;
    $('prompt-details').open = true;
    $('edit-prompt-output').focus(); $('edit-prompt-output').select();
    $('edit-copy-status').textContent = '자동 복사를 사용할 수 없어요. 선택된 요청문을 Ctrl+C 또는 ⌘C로 복사하거나 TXT로 저장해 주세요.';
  }
});
$('download-edit-prompt').addEventListener('click', () => {
  if (!generatedPrompt) return;
  const url = URL.createObjectURL(new Blob([generatedPrompt], {type:'text/plain;charset=utf-8'}));
  const link = el('a'); link.href = url; link.download = `notion-teacher-planner-edit-${answers.featureId}.txt`;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  $('edit-copy-status').textContent = 'TXT 저장을 요청했어요. 내려받은 파일을 AI에게 첨부해도 됩니다.';
});
$('edit-reset').addEventListener('click', () => { $('edit-reset-confirm').hidden = false; $('edit-reset-yes').focus(); });
$('edit-reset-no').addEventListener('click', () => { $('edit-reset-confirm').hidden = true; $('edit-reset').focus(); });
$('edit-reset-yes').addEventListener('click', () => {
  let removed = true;
  try { sessionStorage.removeItem(STORAGE_KEY); } catch { removed = false; }
  answers = createEditAnswers(); drafts = {}; invalidate(); renderAnswers();
  $('edit-reset-confirm').hidden = true;
  $('fine-tune').open = false;
  $('edit-draft-status').textContent = removed ? '초안을 모두 지웠습니다.' : '화면을 초기화했습니다. 브라우저의 임시 저장은 지우지 못했으므로 공용 기기라면 탭을 닫아 주세요.';
  $('feature-options').querySelector('input').focus();
});
renderCatalog(); restore(); renderAnswers();
