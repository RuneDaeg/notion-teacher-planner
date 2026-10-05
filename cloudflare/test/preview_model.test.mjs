import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {PREVIEW_FORMAT, PREVIEW_VERSION, createSelection, selectionFromSetupAnswers, resolvePreview,
  canonicalJSON, buildReviewedBundle, verifyReviewedBundle, buildPreviewPrompt, getPreviewStats,
  movePreviewSection, setPreviewRowRatio, setPreviewRowColumns, resizePreviewColumn, reconcilePreviewOverrides} from '../../cloud/web/preview-model.mjs';

const catalog = JSON.parse(await readFile(new URL('../../cloud/web/preview-catalog.json', import.meta.url), 'utf8'));
const repository = fileURLToPath(new URL('../../', import.meta.url));
const modules = ['accounts', 'assessment', 'attendance', 'contact', 'meeting', 'staff'];
const allForms = ['assessment', 'counseling', 'guardian', 'homeroom', 'lesson', 'meeting'];
const baseDigest = 'c83b626d0e68c54974e168e9490020e7f6bcddc20c605a2f62757997b786a97c';
const version2Digest = 'd802313b15a775c72181b79c43ca5610d9a6643365ebd4cf09bc1b212a95daa5';
const legacyDigest = 'df636473880331d93c25c82d85d61a583a6ceea58638ad182c88a14af7f290ce';
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
  assert.deepEqual(bundle.layout_overrides, {});
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

test('version 1 reviewed bundles remain strict imports and can be upgraded without placement changes', async () => {
  const bundle = await buildReviewedBundle(catalog);
  const legacy = structuredClone(bundle);
  legacy.version = 1;
  delete legacy.layout_overrides;
  const signed = rehash(legacy);
  assert.equal(signed.bundle_digest, legacyDigest);
  assert.deepEqual(await verifyReviewedBundle(catalog, signed), {valid: true, errors: []});
  assert.match(buildPreviewPrompt(signed), /버전 1/);
  for (const mutate of [
    changed => { changed.layout_overrides = {}; },
    changed => { changed.pages.home.rows.reverse(); },
    changed => { changed.version = 3; },
  ]) {
    const changed = structuredClone(signed);
    mutate(changed);
    assert.equal((await verifyReviewedBundle(catalog, rehash(changed))).valid, false);
  }
  assert.deepEqual((await buildReviewedBundle(catalog, signed.selection)).pages, signed.pages);
});

test('version 2 imports keep their original hash and reject newly allowed v3 widths and column counts', async () => {
  const bundle = await buildReviewedBundle(catalog);
  const legacy = rehash({...bundle, version: 2});
  assert.equal(legacy.bundle_digest, version2Digest);
  assert.deepEqual(await verifyReviewedBundle(catalog, legacy), {valid: true, errors: []});
  assert.match(buildPreviewPrompt(legacy), /버전 2/);
  const original = setPreviewRowRatio(catalog, createSelection(), {}, 'home', 'work', [45, 55]);
  const legacyCustom = rehash({...await buildReviewedBundle(catalog, createSelection(), original), version: 2});
  assert.equal((await verifyReviewedBundle(catalog, legacyCustom)).valid, true);
  for (const overrides of [
    setPreviewRowRatio(catalog, createSelection(), {}, 'home', 'work', [30, 70]),
    setPreviewRowColumns(catalog, createSelection(), {}, 'home', 'work', 3),
  ]) {
    const current = await buildReviewedBundle(catalog, createSelection(), overrides);
    assert.equal((await verifyReviewedBundle(catalog, current)).valid, true);
    assert.equal((await verifyReviewedBundle(catalog, rehash({...current, version: 2}))).valid, false);
  }
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

const flattened = rows => rows.flatMap(row => row.columns.flatMap(column => column.sections));
const move = (overrides, section, target, position, page = 'home', selection = createSelection()) =>
  movePreviewSection(catalog, selection, overrides, {page, section, target, position});

test('drag placements and width choices preserve every section, source, view, and fixed page setting', async () => {
  const selection = createSelection(), initial = resolvePreview(catalog, selection);
  let overrides = move({}, 'schedule', 'briefing', 'row-after');
  assert.equal(overrides.home[3].columns[0].sections[0], 'schedule');
  const copied = structuredClone(overrides);
  overrides = move(overrides, 'meals', 'progress', 'left');
  assert.deepEqual(copied.home[3].columns[0].sections, ['schedule'], 'input must not be mutated');
  const row = overrides.home.find(item => item.columns.some(column => column.sections.includes('progress')));
  assert.deepEqual(row.columns, [{ratio: 50, sections: ['meals']}, {ratio: 50, sections: ['progress']}]);
  overrides = setPreviewRowRatio(catalog, selection, overrides, 'home', row.id, [45, 55]);
  overrides = move(overrides, 'deadlines', 'tasks', 'before');
  assert.deepEqual(overrides.home.find(item => item.id === 'work').columns, [{ratio: 100, sections: ['deadlines', 'tasks']}]);
  const bundle = await buildReviewedBundle(catalog, selection, overrides);
  assert.notEqual(bundle.bundle_digest, baseDigest);
  assert.deepEqual(bundle.pages.home.rows, overrides.home);
  assert.deepEqual(bundle.pages.home.rows.slice(0, 2), initial.pages.home.rows.slice(0, 2));
  assert.deepEqual([...flattened(bundle.pages.home.rows)].sort(), Object.keys(initial.pages.home.sections).sort());
  assert.deepEqual(bundle.pages.home.sections, initial.pages.home.sections);
  assert.deepEqual(bundle.views, initial.views);
  assert.deepEqual(bundle.page_settings, initial.page_settings);
  assert.deepEqual(await verifyReviewedBundle(catalog, bundle), {valid: true, errors: []});
  assert.match(buildPreviewPrompt(bundle), /layout_overrides/);
  assert.match(buildPreviewPrompt(bundle), /verify-layout --snapshot .*--reviewed-bundle/);
});

test('moving to a new row cleans empty columns and rows, preserves cards, and allocates deterministic ids', () => {
  let overrides = move({}, 'meals', 'tasks', 'row-after');
  assert.deepEqual(overrides.home.find(row => row.id === 'work').columns, [{ratio: 100, sections: ['tasks', 'deadlines']}]);
  overrides = move(overrides, 'quick', 'schedule', 'row-after');
  const quick = overrides.home.find(row => row.columns[0].sections.includes('quick'));
  assert.deepEqual(quick.columns, [{ratio: 100, sections: ['quick']}]);
  assert.equal(quick.id, 'drag-2');
  overrides = move(overrides, 'schedule', 'briefing', 'after');
  assert.equal(overrides.home.some(row => row.id === 'schedule'), false);
  assert.deepEqual(overrides.home.find(row => row.id === 'briefing').columns[0].sections, ['briefing', 'schedule']);
  assert.ok(overrides.home.every(row => row.columns.every(column => column.sections.length)));
  assert.equal(new Set(overrides.home.map(row => row.id)).size, overrides.home.length);
  assert.deepEqual(move(overrides, 'schedule', 'schedule', 'before'), overrides);
  const right = move({}, 'progress', 'briefing', 'right');
  assert.deepEqual(right.home.find(row => row.id === 'briefing').columns.map(column => column.sections), [['briefing'], ['progress']]);
});

test('side placement inserts next to the target column, retaining stacked sections and up to four columns', () => {
  let overrides = move({}, 'progress', 'tasks', 'right');
  let row = overrides.home.find(item => item.id === 'work');
  assert.deepEqual(row.columns.map(column => column.sections), [['meals'], ['tasks', 'deadlines'], ['progress']]);
  assert.deepEqual(row.columns.map(column => column.ratio), [33.333333, 33.333333, 33.333334]);
  overrides = move(overrides, 'counseling', 'tasks', 'left');
  row = overrides.home.find(item => item.id === 'work');
  assert.deepEqual(row.columns.map(column => column.sections), [['meals'], ['counseling'], ['tasks', 'deadlines'], ['progress']]);
  assert.deepEqual(row.columns.map(column => column.ratio), [25, 25, 25, 25]);
  assert.throws(() => move(overrides, 'schedule', 'tasks', 'right'), /네 열/);
  const reordered = move(overrides, 'progress', 'meals', 'left');
  assert.deepEqual(reordered.home.find(item => item.id === 'work').columns.map(column => column.sections),
    [['progress'], ['meals'], ['counseling'], ['tasks', 'deadlines']]);
  const original = structuredClone(overrides);
  const stacked = move(overrides, 'tasks', 'meals', 'before');
  assert.deepEqual(stacked.home.find(item => item.id === 'work').columns.map(column => column.sections),
    [['tasks', 'meals'], ['counseling'], ['deadlines'], ['progress']]);
  assert.deepEqual(overrides, original);
});

test('split and merge preserve reading order and deterministically balance populated columns', () => {
  let overrides = setPreviewRowColumns(catalog, createSelection(), {}, 'home', 'work', 2);
  assert.deepEqual(overrides.home.find(row => row.id === 'work').columns, [
    {ratio: 50, sections: ['meals', 'tasks']}, {ratio: 50, sections: ['deadlines']},
  ]);
  overrides = move(overrides, 'progress', 'deadlines', 'after');
  const original = structuredClone(overrides);
  overrides = setPreviewRowColumns(catalog, createSelection(), overrides, 'home', 'work', 4);
  assert.deepEqual(overrides.home.find(row => row.id === 'work').columns.map(column => column.sections),
    [['meals'], ['tasks'], ['deadlines'], ['progress']]);
  const merged = setPreviewRowColumns(catalog, createSelection(), overrides, 'home', 'work', 1);
  assert.deepEqual(merged.home.find(row => row.id === 'work').columns, [{ratio: 100, sections: ['meals', 'tasks', 'deadlines', 'progress']}]);
  assert.deepEqual(original.home.find(row => row.id === 'work').columns[0].sections, ['meals', 'tasks']);
  for (const count of [0, 5, 1.5, true, '2', NaN]) assert.throws(() => setPreviewRowColumns(catalog, createSelection(), {}, 'home', 'work', count));
  assert.throws(() => setPreviewRowColumns(catalog, createSelection(), {}, 'home', 'work', 4), /빈 열/);
  assert.throws(() => setPreviewRowColumns(catalog, createSelection(), {}, 'home', 'quick', 2));
  assert.throws(() => setPreviewRowColumns(catalog, createSelection(), {}, 'missing', 'work', 2));
});

test('custom ratios validate four populated columns, exact precision, bounds, and sum', () => {
  let overrides = move({}, 'progress', 'deadlines', 'after');
  overrides = setPreviewRowColumns(catalog, createSelection(), overrides, 'home', 'work', 4);
  const changed = setPreviewRowRatio(catalog, createSelection(), overrides, 'home', 'work', [10, 20.125, 30.125, 39.75]);
  assert.deepEqual(changed.home.find(row => row.id === 'work').columns.map(column => column.ratio), [10, 20.125, 30.125, 39.75]);
  for (const ratios of [[10, 10, 10, 71], [9.999999, 20, 30, 40.000001], [10.0000001, 20, 30, 39.9999999],
    [NaN, 20, 30, 40], [Infinity, 20, 30, 40], [true, 20, 30, 49], [10, 20, 70], [], null]) {
    assert.throws(() => setPreviewRowRatio(catalog, createSelection(), overrides, 'home', 'work', ratios));
  }
});

test('resizing a column keeps the others above their floor and preserves their relative excess widths', () => {
  const selection = createSelection();
  let overrides = setPreviewRowColumns(catalog, selection, {}, 'home', 'work', 3);
  overrides = setPreviewRowRatio(catalog, selection, overrides, 'home', 'work', [20, 50, 30]);
  const original = structuredClone(overrides);
  const resized = resizePreviewColumn(catalog, selection, overrides, 'home', 'work', 0, 40);
  assert.deepEqual(resized.home.find(row => row.id === 'work').columns.map(column => column.ratio), [40, 36.666667, 23.333333]);
  assert.deepEqual(overrides, original);
  overrides = move(overrides, 'progress', 'tasks', 'right');
  overrides = resizePreviewColumn(catalog, selection, overrides, 'home', 'work', 0, 70);
  assert.deepEqual(overrides.home.find(row => row.id === 'work').columns.map(column => column.ratio), [70, 10, 10, 10]);
  overrides = resizePreviewColumn(catalog, selection, overrides, 'home', 'work', 0, 10);
  assert.deepEqual(overrides.home.find(row => row.id === 'work').columns.map(column => column.ratio), [10, 30, 30, 30]);
  for (const percent of [9.999999, 70.000001, 50.1234567, Infinity, NaN, '25', true]) {
    assert.throws(() => resizePreviewColumn(catalog, selection, overrides, 'home', 'work', 0, percent));
  }
  for (const index of [-1, 4, 1.5, true, '0']) assert.throws(() => resizePreviewColumn(catalog, selection, overrides, 'home', 'work', index, 25));
  assert.throws(() => resizePreviewColumn(catalog, selection, {}, 'home', 'briefing', 0, 40));
  assert.equal(resizePreviewColumn(catalog, selection, {}, 'home', 'briefing', 0, 100).home.find(row => row.id === 'briefing').columns[0].ratio, 100);
});

test('invalid moves cannot move anchors, put cards inside columns, duplicate sections, or create nested layouts', () => {
  for (const [section, target, position] of [
    ['nav', 'briefing', 'row-after'], ['briefing', 'intro', 'row-after'], ['quick', 'tasks', 'before'],
    ['tasks', 'quick', 'left'], ['missing', 'briefing', 'before'],
    ['schedule', 'resources', 'before'], ['schedule', 'briefing', 'arbitrary'],
  ]) assert.throws(() => move({}, section, target, position), undefined, `${section}/${target}/${position}`);
  assert.throws(() => movePreviewSection(catalog, createSelection(), {}, {page: 'home', section: ['briefing'], target: 'tasks', position: 'before'}));
  assert.throws(() => setPreviewRowRatio(catalog, createSelection(), {}, 'home', 'work', [5, 95]));
  assert.throws(() => setPreviewRowRatio(catalog, createSelection(), {}, 'home', 'briefing', [50, 50]));
  assert.throws(() => setPreviewRowRatio(catalog, createSelection(), {}, 'missing', 'work', [40, 60]));
});

test('reviewed layout validation rejects malformed overrides even with matching page rows and recomputed checksums', async () => {
  const base = await buildReviewedBundle(catalog);
  const rows = structuredClone(base.pages.home.rows);
  const invalidRows = [
    candidate => { candidate[0].id = 'different-anchor'; },
    candidate => { candidate[2].id = candidate[3].id; },
    candidate => { candidate[2].id = '<script>'; },
    candidate => { candidate[2].id = 'briefing\n'; },
    candidate => { candidate[2].id = 'x'.repeat(65); },
    candidate => { candidate[2].extra = true; },
    candidate => { candidate[2].columns[0].extra = true; },
    candidate => { candidate[2].columns[0].ratio = true; },
    candidate => { candidate[2].columns[0].ratio = 50; },
    candidate => { candidate[2].columns[0].sections = []; },
    candidate => { candidate[2].columns[0].sections = ['quick']; },
    candidate => { candidate[2].columns[0].sections.push('resources'); },
    candidate => { candidate.pop(); },
    candidate => { const quick = candidate.find(row => row.id === 'quick'); quick.columns[0].sections.push('briefing'); candidate.splice(2, 1); },
    candidate => { const work = candidate.find(row => row.id === 'work'); work.columns = [{ratio: 30, sections: ['meals']}, {ratio: 30, sections: ['tasks']}, {ratio: 30, sections: ['deadlines']}]; },
    candidate => { const work = candidate.find(row => row.id === 'work'); work.columns[0].ratio = 9; work.columns[1].ratio = 91; },
  ];
  for (const mutate of invalidRows) {
    const changed = structuredClone(rows);
    mutate(changed);
    assert.throws(() => resolvePreview(catalog, createSelection(), {home: changed}));
    const modified = structuredClone(base);
    modified.layout_overrides = {home: changed};
    modified.pages.home.rows = changed;
    assert.equal((await verifyReviewedBundle(catalog, rehash(modified))).valid, false);
  }
  for (const overrides of [null, [], 'bad', {unknown: rows}]) assert.throws(() => resolvePreview(catalog, createSelection(), overrides));
});

test('optional module changes retain custom placement, prune removed sections and add new ones without implicit consent', () => {
  const all = createSelection({modules, forms: allForms, homeroom: true, school_links: true});
  let overrides = move({}, 'schedule', 'briefing', 'row-after', 'home', all);
  overrides = move(overrides, 'assessment', 'progress', 'after', 'home', all);
  const core = reconcilePreviewOverrides(catalog, createSelection(), overrides);
  assert.deepEqual(core.home[3].columns[0].sections, ['schedule']);
  assert.ok(!flattened(core.home).includes('assessment'));
  assert.ok(!flattened(core.home).includes('attendance'));
  assert.ok(!flattened(core.home).includes('forms'));
  assert.ok(core.home.every(row => row.columns.length > 1 || row.columns[0].ratio === 100));
  const restored = reconcilePreviewOverrides(catalog, all, core);
  assert.deepEqual(restored.home[3].columns[0].sections, ['schedule']);
  for (const key of ['assessment', 'attendance', 'forms']) assert.equal(flattened(restored.home).filter(section => section === key).length, 1);
  assert.deepEqual(reconcilePreviewOverrides(catalog, all, {}), {});
});

test('removing optional columns normalizes surviving multi-column ratios without losing other sections', () => {
  const all = createSelection({modules: ['assessment', 'attendance']});
  let overrides = move({}, 'assessment', 'tasks', 'right', 'home', all);
  overrides = move(overrides, 'attendance', 'meals', 'left', 'home', all);
  const three = reconcilePreviewOverrides(catalog, createSelection({modules: ['assessment']}), overrides);
  assert.deepEqual(three.home.find(row => row.id === 'work').columns.map(column => column.ratio), [33.333333, 33.333333, 33.333334]);
  assert.deepEqual(three.home.find(row => row.id === 'work').columns.map(column => column.sections), [['meals'], ['tasks', 'deadlines'], ['assessment']]);
  const two = reconcilePreviewOverrides(catalog, createSelection(), overrides);
  assert.deepEqual(two.home.find(row => row.id === 'work').columns.map(column => column.ratio), [50, 50]);
});

test('v3 three and four column exports with arbitrary widths compile identically in Python', async () => {
  const selection = createSelection(), cases = [];
  let overrides = setPreviewRowColumns(catalog, selection, {}, 'home', 'work', 3);
  cases.push({selection, overrides});
  overrides = setPreviewRowRatio(catalog, selection, overrides, 'home', 'work', [20.123456, 30.654321, 49.222223]);
  cases.push({selection, overrides});
  overrides = move(overrides, 'progress', 'tasks', 'right');
  cases.push({selection, overrides});
  overrides = setPreviewRowRatio(catalog, selection, overrides, 'home', 'work', [10, 20.5, 30.123456, 39.376544]);
  cases.push({selection, overrides});
  overrides = resizePreviewColumn(catalog, selection, overrides, 'home', 'work', 2, 55.123456);
  cases.push({selection, overrides});
  const expected = JSON.parse(execFileSync(process.env.PYTHON ?? 'python3', ['-c', [
    'import json, sys', 'from teacher_planner.preview_bundle import create_bundle, validate_bundle',
    'cases = json.load(sys.stdin)', 'bundles = [create_bundle(c["selection"], c["overrides"]) for c in cases]',
    'for bundle in bundles: validate_bundle(bundle)', 'print(json.dumps(bundles))',
  ].join('\n')], {cwd: repository, input: JSON.stringify(cases), encoding: 'utf8'}));
  const bundles = await Promise.all(cases.map(item => buildReviewedBundle(catalog, item.selection, item.overrides)));
  assert.deepEqual(bundles, expected);
  for (const bundle of bundles) assert.deepEqual(await verifyReviewedBundle(catalog, bundle), {valid: true, errors: []});
});

test('custom drag and ratio bundle digests match the Python compiler for all selected modules', async () => {
  const cases = [];
  for (let mask = 0; mask < 64; mask += 1) {
    const chosenModules = modules.filter((key, index) => mask & (1 << index));
    const selection = createSelection({modules: chosenModules, forms: eligibleForms(chosenModules, true), homeroom: true});
    let overrides = move({}, 'schedule', 'briefing', 'row-after', 'home', selection);
    overrides = move(overrides, 'meals', 'matrix', mask & 1 ? 'left' : 'right', 'home', selection);
    const matrixRow = overrides.home.find(row => row.columns.some(column => column.sections.includes('matrix')));
    const ratios = [[50, 50], [40, 60], [60, 40], [55, 45], [45, 55]][mask % 5];
    overrides = setPreviewRowRatio(catalog, selection, overrides, 'home', matrixRow.id, ratios);
    overrides = move(overrides, 'para', 'archive', 'row-after', 'planning', selection);
    cases.push({selection, overrides});
  }
  const expected = JSON.parse(execFileSync(process.env.PYTHON ?? 'python3', ['-c', [
    'import json, sys', 'from teacher_planner.preview_bundle import create_bundle',
    'cases = json.load(sys.stdin)',
    'print(json.dumps([create_bundle(c["selection"], c["overrides"])["bundle_digest"] for c in cases]))',
  ].join('\n')], {cwd: repository, input: JSON.stringify(cases), encoding: 'utf8'}));
  const bundles = await Promise.all(cases.map(item => buildReviewedBundle(catalog, item.selection, item.overrides)));
  assert.deepEqual(bundles.map(bundle => bundle.bundle_digest), expected);
});
