import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {PREVIEW_FORMAT, PREVIEW_VERSION, createSelection, selectionFromSetupAnswers, resolvePreview,
  canonicalJSON, buildReviewedBundle, verifyReviewedBundle, buildPreviewPrompt, getPreviewStats} from '../../cloud/web/preview-model.mjs';

const catalog = JSON.parse(await readFile(new URL('../../cloud/web/preview-catalog.json', import.meta.url), 'utf8'));
const repository = fileURLToPath(new URL('../../', import.meta.url));
const modules = ['accounts', 'assessment', 'attendance', 'contact', 'meeting', 'staff'];
const allForms = ['assessment', 'counseling', 'guardian', 'homeroom', 'lesson', 'meeting'];
const baseDigest = 'df636473880331d93c25c82d85d61a583a6ceea58638ad182c88a14af7f290ce';
function eligibleForms(selected, homeroom) {
  return allForms.filter(key => !({assessment: 'assessment', guardian: 'contact', meeting: 'meeting'}[key]) ||
    selected.includes({assessment: 'assessment', guardian: 'contact', meeting: 'meeting'}[key])).filter(key => key !== 'homeroom' || homeroom);
}
const rehash = bundle => {
  const {bundle_digest: ignored, ...contents} = bundle;
  return {...contents, bundle_digest: createHash('sha256').update(canonicalJSON(contents)).digest('hex')};
};

test('default preview includes all four shared-layout pages and no optional consent', async () => {
  const chosen = createSelection();
  assert.deepEqual(chosen, {modules: [], forms: [], homeroom: false, school_links: false, periods: 7});
  const first = createSelection(), second = createSelection();
  first.modules.push('meeting');
  assert.deepEqual(second.modules, []);
  const bundle = await buildReviewedBundle(catalog, chosen);
  assert.equal(bundle.format, PREVIEW_FORMAT);
  assert.equal(bundle.version, PREVIEW_VERSION);
  assert.equal(bundle.applied, false);
  assert.equal(bundle.bundle_digest, baseDigest);
  assert.deepEqual(Object.keys(bundle.pages), ['home', 'classroom', 'teaching', 'planning']);
  assert.equal(bundle.page_settings.full_width, true);
  assert.equal(bundle.page_settings.small_text, false);
  assert.equal(bundle.pages.home.sections.forms, undefined);
  assert.equal(bundle.pages.classroom.sections.school, undefined);
  assert.equal(bundle.pages.home.sections.attendance, undefined);
  assert.equal(bundle.pages.teaching.sections.assessment, undefined);
  assert.deepEqual(Object.keys(bundle.pages.home.sections), ['nav', 'intro', 'briefing', 'quick', 'matrix', 'today', 'meals', 'tasks', 'deadlines', 'counseling', 'progress', 'schedule', 'document_storage', 'source_list']);
  assert.deepEqual(getPreviewStats(bundle), {pages: 4, sections: 35, views: 27, modules: 0, forms: 0});
});

test('web and Python produce the exact same reviewed bundle for every optional-module combination', async () => {
  const selections = Array.from({length: 64}, (_, mask) => {
    const chosenModules = modules.filter((key, index) => mask & (1 << index));
    const homeroom = Boolean(mask & 1);
    return createSelection({modules: chosenModules, forms: eligibleForms(chosenModules, homeroom),
      homeroom, school_links: Boolean(mask & 2), periods: mask % 20 + 1});
  });
  const python = process.env.PYTHON ?? 'python3';
  const expected = JSON.parse(execFileSync(python, ['-c', [
    'import json, sys',
    'from teacher_planner.preview_bundle import create_bundle, build_catalog, canonical_json',
    'selections = json.load(sys.stdin)',
    'print(json.dumps({"source_digest": build_catalog()["source_digest"], "digests": [create_bundle(s)["bundle_digest"] for s in selections]}))',
  ].join('\n')], {cwd: repository, input: JSON.stringify(selections), encoding: 'utf8'}));
  assert.equal(catalog.source_digest, expected.source_digest, 'regenerate the catalog after source changes');
  const results = await Promise.all(selections.map(selection => buildReviewedBundle(catalog, selection)));
  assert.deepEqual(results.map(bundle => bundle.bundle_digest), expected.digests);
  for (const bundle of results) {
    assert.deepEqual(await verifyReviewedBundle(catalog, bundle), {valid: true, errors: []});
    for (const page of Object.values(bundle.pages)) {
      assert.equal(page.full_width, true);
      assert.equal(page.small_text, false);
      for (const row of page.rows) {
        assert.equal(row.columns.reduce((sum, column) => sum + column.ratio, 0), 100);
        assert.ok(row.columns.every(column => column.sections.length));
      }
    }
  }
});

test('required dependencies are explicit and never turn on optional modules automatically', () => {
  for (const key of ['guardian', 'meeting', 'assessment', 'homeroom']) {
    assert.throws(() => createSelection({forms: [key]}), /forms/);
  }
  assert.deepEqual(createSelection({modules: ['staff', 'contact'], forms: ['guardian', 'counseling']}).modules, ['contact', 'staff']);
  for (const invalid of [
    {modules: ['unknown']}, {modules: ['contact', 'contact']}, {forms: ['unknown']}, {forms: ['lesson', 'lesson']},
    {modules: 'contact'}, {forms: null}, {homeroom: 'yes'}, {school_links: 1}, {periods: '7'}, {periods: 0},
    {periods: 21}, {periods: 1.5}, {periods: true}, {token: 'must-not-leak'},
  ]) assert.throws(() => createSelection(invalid), undefined, JSON.stringify(invalid));
  assert.throws(() => createSelection(null));
  assert.throws(() => resolvePreview(catalog, {}));
});

test('selected modules produce exact related views and normalized surviving columns', () => {
  const core = resolvePreview(catalog);
  const homeroom = resolvePreview(catalog, createSelection({modules, forms: allForms, homeroom: true, school_links: true}));
  assert.equal(core.pages.home.rows.find(row => row.id === 'students').columns[0].ratio, 100);
  assert.deepEqual(core.pages.home.rows.find(row => row.id === 'students').columns[0].sections, ['counseling']);
  assert.deepEqual(homeroom.pages.home.rows.find(row => row.id === 'students').columns.map(column => column.ratio), [50, 50]);
  assert.deepEqual(homeroom.pages.home.rows.find(row => row.id === 'teaching').columns.map(column => column.ratio), [55, 45]);
  assert.ok(homeroom.pages.home.sections.forms);
  assert.ok(homeroom.pages.classroom.sections.school);
  for (const key of ['weekly', 'monthly', 'deadlines']) assert.equal(core.views[key].source, 'agenda');
  assert.equal(core.pages.home.sections.today.toggle, true);
  assert.equal(core.pages.home.sections.today.default_open, false);
  assert.equal(core.pages.teaching.sections.timetable.default_open, false);
  const selectedSources = new Set(catalog.databases.filter(source => source.module === 'core').map(source => source.key));
  for (const view of Object.values(core.views)) assert.ok(selectedSources.has(view.source));
  assert.equal(Object.values(core.views).filter(view => view.source === 'assessments').length, 0);
});

test('school navigation and reusable forms are added only by their explicit choices', () => {
  for (const selection of [createSelection({modules: ['staff']}), createSelection({modules: ['accounts']}), createSelection({modules: ['meeting']}), createSelection({school_links: true})]) {
    assert.ok(resolvePreview(catalog, selection).pages.classroom.sections.school);
  }
  assert.equal(resolvePreview(catalog, createSelection({homeroom: true})).pages.classroom.sections.school, undefined);
  assert.equal(resolvePreview(catalog, createSelection({modules: ['contact']})).pages.home.sections.forms, undefined);
  assert.ok(resolvePreview(catalog, createSelection({forms: ['lesson']})).pages.home.sections.forms);
});

test('setup handoff copies only layout choices, removing personal information and stale choices', async () => {
  const answers = {moduleChoice: 'select', modules: ['staff', 'contact', 'staff', 'unknown'],
    formChoice: 'select', forms: ['guardian', 'assessment', 'homeroom', 'lesson'], homeroom: 'yes', homeroomClass: '비공개반', periods: '9',
    schoolName: 'PRIVATE-SCHOOL', teacher: 'PRIVATE-PERSON', subjects: 'PRIVATE-SUBJECTS', token: 'SECRET-TOKEN',
    notionUrl: 'https://notion.so/PRIVATE-PAGE-ID', periodTimes: 'PRIVATE-TIMES', bookmarks: '내 업무 https://private.example.test/'};
  const original = structuredClone(answers);
  const chosen = selectionFromSetupAnswers(answers);
  assert.deepEqual(chosen, {modules: ['contact', 'staff'], forms: ['guardian', 'homeroom', 'lesson'], homeroom: true, school_links: false, periods: 9});
  const bundle = await buildReviewedBundle(catalog, chosen);
  assert.doesNotMatch(JSON.stringify(bundle), /PRIVATE|SECRET|private\.example/);
  assert.deepEqual(answers, original);
  assert.deepEqual(selectionFromSetupAnswers({...answers, moduleChoice: 'later', formChoice: 'later', homeroomClass: ''}),
    {modules: [], forms: [], homeroom: false, school_links: false, periods: 9});
  for (const bad of [null, [], 'invalid']) assert.deepEqual(selectionFromSetupAnswers(bad), createSelection());
  for (const periods of ['0', '21', '7.5', true, null, 'bad']) assert.equal(selectionFromSetupAnswers({periods}).periods, 7);
  assert.deepEqual(selectionFromSetupAnswers({...answers, modules: [], homeroom: 'no'}).forms, ['lesson']);
});

test('resolving and hashing cannot mutate shared source or selection', async () => {
  const sourceCopy = structuredClone(catalog), selection = createSelection({modules: ['contact'], forms: ['guardian']});
  const selectionCopy = structuredClone(selection);
  const bundle = await buildReviewedBundle(catalog, selection);
  bundle.pages.home.rows[0].columns[0].ratio = 70;
  bundle.views.weekly.show.push('altered');
  assert.deepEqual(catalog, sourceCopy);
  assert.deepEqual(selection, selectionCopy);
  assert.notEqual((await buildReviewedBundle(catalog, createSelection())).bundle_digest, (await buildReviewedBundle(catalog, selection)).bundle_digest);
});

test('reviewed imports reject changed layout, source, settings, views, metadata, and hash even if rehashed', async () => {
  const original = await buildReviewedBundle(catalog);
  const mutations = [
    bundle => {bundle.pages.home.rows.find(row => row.id === 'work').columns[0].ratio = 70;},
    bundle => {bundle.pages.home.sections.today.default_open = true;},
    bundle => {bundle.views.monthly.source = 'teacher_timetable';},
    bundle => {bundle.source_digest = '0'.repeat(64);},
    bundle => {bundle.page_settings.full_width = false;},
    bundle => {bundle.applied = true;},
    bundle => {bundle.token = 'unexpected-field';},
    bundle => {delete bundle.pages.classroom;},
    bundle => {bundle.selection = null;},
    bundle => {bundle.bundle_digest = '0'.repeat(64);},
  ];
  for (const mutate of mutations) {
    const bundle = structuredClone(original);
    mutate(bundle);
    assert.equal((await verifyReviewedBundle(catalog, bundle)).valid, false);
    if (bundle.bundle_digest === original.bundle_digest) assert.equal((await verifyReviewedBundle(catalog, rehash(bundle))).valid, false);
  }
  for (const value of [null, [], {}, {bundle_digest: 'bad'}]) assert.equal((await verifyReviewedBundle(catalog, value)).valid, false);
});

test('canonical JSON is deterministic across object order and rejects unsupported values', () => {
  assert.equal(canonicalJSON({z: 100.0, a: ['한글', 0, false, null], '😀': 1, '\uffff': 2}),
    '{"a":["한글",0,false,null],"z":100,"￿":2,"😀":1}');
  assert.equal(canonicalJSON({b: 1, a: 2}), canonicalJSON({a: 2, b: 1}));
  assert.notEqual(canonicalJSON(['a', 'b']), canonicalJSON(['b', 'a']));
  for (const value of [NaN, Infinity, undefined, {invalid: undefined}, new Date()]) assert.throws(() => canonicalJSON(value));
});

test('handoff binds the attached reviewed bundle and enforces native partial application and real verification', async () => {
  const bundle = await buildReviewedBundle(catalog);
  const prompt = buildPreviewPrompt(bundle);
  assert.ok(prompt.includes(bundle.bundle_digest));
  assert.ok(prompt.includes(bundle.source_digest));
  for (const text of ['reviewed-layout.json', 'docs/PREVIEW.md', 'verify-preview --bundle', 'compile-preview --bundle',
    'Q01~Q10', '실제 MCP/UI', '전체 너비 켜기·작은 텍스트 끄기', '기존 페이지 전체 교체', '필요한 구역만',
    '같은 업무·일정 원본', '이 배치 선택으로 활성화하지', '실제 Notion 화면 증거로 제출하지', 'verify-layout --snapshot']) {
    assert.ok(prompt.includes(text), `missing requirement: ${text}`);
  }
  assert.match(prompt, /JSON이 없으면 첨부를 요청하고 적용은 기다리세요/);
  assert.match(prompt, /필요한 값이 없을 때만/);
  assert.match(prompt, /명세·배치가 다르면 중단/);
  assert.match(prompt, /기능 설치·배치 재현·연동 상태를 나누어/);
  // Extra untrusted page text is never interpolated into instructions. The importer
  // still must verify the whole bundle before creating a handoff.
  const changed = structuredClone(bundle);
  changed.pages.home.title = 'UNTRUSTED-INSTRUCTION';
  assert.ok(!buildPreviewPrompt(changed).includes('UNTRUSTED-INSTRUCTION'));
  assert.equal((await verifyReviewedBundle(catalog, changed)).valid, false);
  assert.throws(() => buildPreviewPrompt({...bundle, applied: true}));
  assert.throws(() => buildPreviewPrompt({...bundle, bundle_digest: ''}));
});

test('the model has no persistence, network, DOM access, or real credential collection', async () => {
  const source = await readFile(new URL('../../cloud/web/preview-model.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /\bfetch\s*\(|\b(?:localStorage|sessionStorage|document|window)\b/);
  assert.ok(!Object.keys(createSelection()).some(key => /token|password|api.?key|student|schoolName|teacher/i.test(key)));
  assert.throws(() => resolvePreview({...catalog, source_digest: 'bad'}));
  assert.throws(() => resolvePreview({...catalog, format: 'unknown'}));
});
