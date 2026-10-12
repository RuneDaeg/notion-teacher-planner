import {createWorkflowSelection, requiredWorkflowModules, validateWorkflowSelection, WORKFLOW_STORAGE_KEY} from './workflow-model.mjs';

export const MODULE_NAMES = {attendance:'출결·제출물', assessment:'평가·채점', contact:'학부모 연락', meeting:'회의록', staff:'교직원 연락망', accounts:'학교 계정'};
export const PRESET_COPY = {
  basic:{tag:'가볍게 시작', detail:'학생 · 학급 · 수업 연결 3개', note:'기본 수첩의 기록을 서로 연결합니다. 추가 모듈을 켜지 않습니다.'},
  recommended:{tag:'일상 업무에 추천', detail:'기본 3개 + 후속 업무 · PARA · 제출물 · 평가 범위', note:'출결·제출물, 평가·채점, 학부모 연락, 회의록을 함께 사용합니다.'},
  custom:{tag:'필요한 것만', detail:'9개 기능 중 직접 선택', note:'학생별 평가 결과와 담당 교직원 연결도 추가할 수 있습니다.'},
};
export function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
export async function loadWorkflowCatalog() {
  const response = await fetch('/workflow-catalog.json');
  if (!response.ok) throw new Error('기능 목록을 불러오지 못했습니다. 새로고침해 주세요.');
  const catalog = await response.json();
  if (catalog.version !== 1 || !Array.isArray(catalog.features) || !catalog.presets) throw new Error('기능 목록의 버전을 확인해 주세요.');
  return catalog;
}
export function readWorkflowDraft(catalog) {
  try {
    const raw = sessionStorage.getItem(WORKFLOW_STORAGE_KEY);
    if (!raw || raw.length > 5000) return null;
    const saved = JSON.parse(raw);
    if (saved.version !== 1 || validateWorkflowSelection(catalog, saved.selection).length) return null;
    return saved.selection;
  } catch { return null; }
}
export function saveWorkflowDraft(selection) {
  try { sessionStorage.setItem(WORKFLOW_STORAGE_KEY, JSON.stringify({version:1, selection})); return true; }
  catch { return false; }
}

// Shared controls use the same canonical choices as the installer and preview.
export function mountWorkflowPicker(host, catalog, selection, onChange, {compact = false, examples = false} = {}) {
  const focusId = host.contains(document.activeElement) ? document.activeElement.id : null;
  host.replaceChildren(); host.classList.add('workflow-picker');
  if (compact) host.classList.add('compact');
  const presets = element('div', undefined, 'workflow-presets'); presets.setAttribute('role','group'); presets.setAttribute('aria-label','연결 기능 구성');
  for (const key of ['basic','recommended','custom']) {
    const spec = catalog.presets[key], copy = PRESET_COPY[key];
    const button = element('button', undefined, 'workflow-preset'); button.type = 'button'; button.dataset.preset = key;
    button.id = `${host.id}-preset-${key}`;
    button.setAttribute('aria-pressed', String(selection?.preset === key));
    button.append(element('span', copy.tag, 'preset-tag'), element('strong', spec.title), element('span', copy.detail, 'preset-detail'), element('small', copy.note));
    button.addEventListener('click', () => onChange(createWorkflowSelection(catalog, key, key === 'custom' ? (selection?.features || []) : undefined)));
    presets.append(button);
  }
  host.append(presets);
  if (selection) {
    const dependencies = requiredWorkflowModules(catalog, selection);
    const status = element('p', `${selection.features.length}개 연결 기능 선택 · 함께 필요한 모듈: ${dependencies.map(key => MODULE_NAMES[key]).join(', ') || '없음'}`, 'workflow-dependencies');
    status.setAttribute('role','status'); host.append(status);
    if (selection.preset === 'custom') {
      const tools = element('div', undefined, 'choice-tools');
      for (const [label, features] of [['9개 모두 선택', catalog.features.map(item => item.key)], ['연결 기능 선택 해제', []]]) {
        const button = element('button', label); button.type = 'button'; button.id = `${host.id}-choose-${features.length ? 'all' : 'none'}`; button.addEventListener('click', () => onChange(createWorkflowSelection(catalog,'custom',features))); tools.append(button);
      }
      host.append(tools);
      const choices = element('div', undefined, 'workflow-checks');
      for (const [index, item] of catalog.features.entries()) {
        const label = element('label', undefined, 'workflow-check'); const input = element('input'); input.type = 'checkbox'; input.checked = selection.features.includes(item.key);
        input.id = `${host.id}-feature-${item.key}`;
        input.addEventListener('change', () => onChange(createWorkflowSelection(catalog,'custom',input.checked ? [...selection.features,item.key] : selection.features.filter(key => key !== item.key))));
        label.append(input, element('span', `${index + 1}. ${item.title}`)); choices.append(label);
      }
      host.append(choices);
    }
  } else host.append(element('p','구성을 선택하면 필요한 연결과 모듈을 함께 준비합니다.','workflow-dependencies'));
  if (examples) { const a = element('a','9개 기능의 설명과 예시 보기 ↗','workflow-examples-link'); a.href = '/features'; host.append(a); }
  if (compact) host.append(element('p','연락·회의·학교 계정 연결은 해당 모듈을 사용하는 경우에 구성합니다. 이미 고른 추가 모듈은 유지되며, 필요한 모듈을 빼려면 커스텀에서 연결 기능부터 조정해 주세요.','field-help'));
  if (focusId) document.getElementById(focusId)?.focus({preventScroll:true});
}
