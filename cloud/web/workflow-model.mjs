// Pure selection contract shared by the gallery, questionnaire and preview.
// No storage, network, Notion writes, or inferred consent live in this module.
export const WORKFLOW_STORAGE_KEY = 'teacher-planner-workflows-v1';
export const STORAGE_KEY = WORKFLOW_STORAGE_KEY;
export const WORKFLOW_VERSION = 1;
const MODULES = ['accounts', 'assessment', 'attendance', 'contact', 'meeting', 'staff'];
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const clone = value => JSON.parse(JSON.stringify(value));
const same = (left, right) => JSON.stringify([...left].sort()) === JSON.stringify([...right].sort());

function checkedCatalog(value) {
  const catalog = value?.workflows ?? value;
  if (!record(catalog) || catalog.version !== WORKFLOW_VERSION || !Array.isArray(catalog.features) || !record(catalog.presets)) {
    throw new Error('기능 명세를 불러온 뒤 선택해주세요.');
  }
  const keys = catalog.features.map(feature => feature.key);
  if (keys.length !== 9 || new Set(keys).size !== keys.length || keys.some(key => typeof key !== 'string') ||
      catalog.features.some(feature => !Array.isArray(feature.requires_modules) || !Array.isArray(feature.optional_modules) ||
        [...feature.requires_modules, ...feature.optional_modules].some(key => !MODULES.includes(key)))) {
    throw new Error('기능 명세가 올바르지 않습니다.');
  }
  for (const preset of ['basic', 'recommended', 'custom']) {
    const item = catalog.presets[preset];
    if (!record(item) || !Array.isArray(item.features) || !Array.isArray(item.modules) ||
        item.features.some(key => !keys.includes(key)) || item.modules.some(key => !MODULES.includes(key))) {
      throw new Error('기본·추천·커스텀 명세가 필요합니다.');
    }
  }
  return catalog;
}

export function normalizeWorkflowSelection(catalog, selection, modules) {
  const source = checkedCatalog(catalog);
  if (!record(selection) || Object.keys(selection).sort().join(',') !== 'features,preset' ||
      !['basic', 'recommended', 'custom'].includes(selection.preset) || !Array.isArray(selection.features)) {
    throw new Error('기능 설정에는 preset과 features만 지정해주세요.');
  }
  const keys = source.features.map(feature => feature.key);
  if (selection.features.some(key => typeof key !== 'string' || !keys.includes(key)) || new Set(selection.features).size !== selection.features.length) {
    throw new Error('목록의 기능을 중복 없이 선택해주세요.');
  }
  if (selection.preset !== 'custom' && !same(selection.features, source.presets[selection.preset].features)) {
    throw new Error('기본·추천 설정의 기능 목록이 다릅니다. 직접 변경하려면 커스텀을 선택해주세요.');
  }
  const result = {preset: selection.preset, features: [...selection.features].sort()};
  if (modules !== undefined) {
    const enabled = Array.isArray(modules) ? modules : record(modules) ? Object.keys(modules).filter(key => modules[key] === true) : [];
    const required = requiredWorkflowModules(source, result);
    const missing = required.filter(key => !enabled.includes(key));
    if (missing.length) throw new Error(`선택한 연결에 필요한 기능을 함께 선택해주세요: ${missing.join(', ')}.`);
  }
  return result;
}

export function validateWorkflowSelection(catalog, selection, modules) {
  try { normalizeWorkflowSelection(catalog, selection, modules); return []; }
  catch (error) { return [{field: 'workflows', message: error.message}]; }
}

export function createWorkflowSelection(catalog, preset = 'basic', features) {
  const source = checkedCatalog(catalog);
  if (!Object.hasOwn(source.presets, preset)) throw new Error('기본·추천·커스텀 중 선택해주세요.');
  return normalizeWorkflowSelection(source, {preset, features: features ?? source.presets[preset].features});
}

export function requiredWorkflowModules(catalog, selection) {
  const source = checkedCatalog(catalog), choice = normalizeWorkflowSelection(source, selection);
  const required = new Set(source.presets[choice.preset].modules);
  for (const feature of source.features.filter(item => choice.features.includes(item.key))) {
    for (const key of feature.requires_modules) required.add(key);
  }
  return [...required].sort();
}

export function workflowSummary(catalog, selection) {
  const source = checkedCatalog(catalog), choice = normalizeWorkflowSelection(source, selection);
  return {preset: choice.preset, title: source.presets[choice.preset].title,
    features: clone(source.features.filter(item => choice.features.includes(item.key))),
    modules: requiredWorkflowModules(source, choice), featureCount: choice.features.length};
}

export function applyWorkflowsToSetupAnswers(catalog, answers, selection) {
  const choice = normalizeWorkflowSelection(catalog, selection), result = {...answers};
  // This helper is called only after the user explicitly selects a preset or
  // confirms custom choices with their displayed dependency modules.
  const modules = new Set(answers.moduleChoice === 'select' && Array.isArray(answers.modules) ? answers.modules : []);
  for (const key of requiredWorkflowModules(catalog, choice)) modules.add(key);
  result.modules = [...modules].sort();
  result.moduleChoice = result.modules.length ? 'select' : (answers.moduleChoice === 'later' ? 'later' : 'none');
  result.workflows = choice;
  return result;
}

export function workflowPromptLines(catalog, selection, {mode = 'preview'} = {}) {
  const choice = normalizeWorkflowSelection(catalog, selection), summary = workflowSummary(catalog, choice);
  const configPath = mode === 'edit' ? '.local/workflow-change-config.json' : '.local/config.json';
  return [
    `선택한 관계형·보기 설정: ${summary.title}. 정확한 기능 ID: ${choice.features.join(', ') || '없음'}.`,
    `선택한 기능의 계획 값(새 설치 config에 사용; 기존 수첩은 원래 config/state를 보존하고 별도 변경 계획에 기록): ${JSON.stringify({workflows: choice})}`,
    'docs/WORKFLOWS.md와 teacher_planner/workflow_catalog.json을 읽고 workflows의 preset·features를 위 적용 범위에 맞게 기록하세요. 선택하지 않은 연결·추가 DB·집계는 새로 만들지 마세요. 기존에 설치된 미선택 기능이나 기록을 삭제하라는 뜻은 아닙니다.',
    ...summary.features.map(feature => `- ${feature.key}: ${feature.title} — ${feature.description}`),
    `필요한 선택 모듈: ${summary.modules.join(', ') || '없음'}. 상담·학생·학급·수업의 기본 원본과 현재 켜진 선택 모듈을 재사용하세요. 선택 모듈이 빠져 있으면 불일치를 먼저 해결하고 임의로 켜지 마세요.`,
    ...(mode === 'install' ? [] : ['기존 수첩을 수정한다면 원래 config와 설치 state는 보존하세요. 현재 확인한 설정과 요청받은 workflows를 별도 .local/workflow-change-config.json에 복사해 읽기 전용 workflow-plan에만 사용하세요. 변경한 설정으로 install을 다시 실행하거나 원래 설치 상태를 교체하지 마세요.']),
    `python -m teacher_planner workflow-plan --config ${configPath}으로 선택한 관계·현재 페이지 필터·보기·집계 계획을 확인하세요.${mode === 'preview' ? ' 기존 수첩은 이 명령의 config 경로를 .local/workflow-change-config.json으로 바꾸세요.' : ''} 관계 대상 원본을 만든 뒤 관계를 연결하고, 학생·학급·프로젝트 등 기록 페이지의 보기는 현재 페이지 관계로 필터링하세요. 다른 학생·학급의 기록이 섞이지 않는지 실제 기록과 필터를 재조회하세요. 필터는 공유 권한을 대신하지 않습니다.`,
    '기존 관계를 바꿀 때는 전체 연결을 비공개로 백업하고 docs/UPDATES.md의 재검증 절차를 따르세요. 이름만으로 학생·교직원을 자동 연결하지 마세요. 제출·평가 결과는 학생 한 명과 해당 항목 하나의 조합당 한 행으로 관리하며, 없는 기록을 제출·채점 완료로 계산하지 마세요.',
    '웹의 설명·가상 예시는 동작 설명입니다. 선택 또는 미리보기 확인만으로 실제 Notion 관계·보기·템플릿이 설치되거나 검증된 것은 아닙니다. 스키마 설치와 기록별 보기·필터·집계의 실제 확인을 구분하고 미완료 사항을 남기세요.',
  ];
}
