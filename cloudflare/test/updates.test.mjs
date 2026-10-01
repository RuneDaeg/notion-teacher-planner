import test from 'node:test';
import assert from 'node:assert/strict';
import {UPDATE_CATALOG, validateUpdateTargets, inspectStudentHistory, applyStudentHistory} from '../src/updates.mjs';

const id = value => `10000000-0000-4000-8000-${String(value).padStart(12, '0')}`;
const manifest = {root_page_id: id(1)};
const targets = {version: 1, students_data_source_id: id(2), counseling_data_source_id: id(3), student_relation_property_id: 'st%3A1'};
const field = (id, name, type, settings = {}) => ({id, name, type, [type]: settings});
function fixture({dual = false, rows = 0, customName = '상담 기록', onPatch, onQuery, sectionDepth = 0} = {}) {
  const objects = new Map(), calls = [], saves = [];
  const put = (kind, value) => objects.set(`/${kind}/${value.id}`, value);
  put('pages', {object: 'page', id: id(1), parent: {type: 'workspace', workspace: true}});
  let parent = id(1);
  for (let n = 0; n < sectionDepth; n++) {
    put('pages', {object: 'page', id: id(20 + n), parent: {type: 'page_id', page_id: parent}});
    parent = id(20 + n);
  }
  const students = {object: 'data_source', id: id(2), parent: {type: 'database_id', database_id: id(4)}, properties: {이름: field('title', '이름', 'title')}};
  const counseling = {object: 'data_source', id: id(3), parent: {type: 'database_id', database_id: id(5)}, properties: {이름: field('title', '이름', 'title'), 학생: field('st%3A1', '학생', 'relation', {type: 'single_property', single_property: {}, data_source_id: id(2), database_id: id(4)})}};
  for (const [database, source] of [[id(4), students], [id(5), counseling]]) {
    put('databases', {object: 'database', id: database, parent: {type: 'page_id', page_id: parent}, data_sources: [{id: source.id, name: '가상 자료'}]});
    put('data_sources', source);
  }
  function makeDual(name = customName) {
    counseling.properties.학생.relation = {type: 'dual_property', data_source_id: id(2), database_id: id(4), dual_property: {synced_property_id: 'back%3A1', synced_property_name: name}};
    students.properties[name] = field('back%3A1', name, 'relation', {type: 'dual_property', data_source_id: id(3), database_id: id(5), dual_property: {synced_property_id: 'st%3A1', synced_property_name: '학생'}});
  }
  function rename(name) {
    const old = Object.entries(students.properties).find(([, property]) => property.id === 'back%3A1');
    delete students.properties[old[0]];
    students.properties[name] = {...old[1], name};
    counseling.properties.학생.relation.dual_property.synced_property_name = name;
  }
  if (dual) makeDual();
  let queryCount = 0, patchCount = 0, latest;
  const notion = {async request(method, path, body) {
    calls.push({method, path, body: structuredClone(body)});
    if (method === 'GET') {
      const object = objects.get(path);
      assert.ok(object, `unexpected ${path}`);
      return structuredClone(object);
    }
    if (method === 'POST') {
      assert.equal(path, `/data_sources/${id(3)}/query?filter_properties=st%3A1`);
      assert.deepEqual(body, {page_size: 1});
      queryCount++;
      const override = onQuery?.(queryCount);
      return override ?? {object: 'list', results: rows ? [{object: 'page', id: id(99), properties: {}}] : [], has_more: false, next_cursor: null};
    }
    assert.equal(method, 'PATCH');
    patchCount++;
    const context = {makeDual, rename, students, counseling, saves, patchCount};
    await onPatch?.('before', context);
    assert.equal(latest?.stage, 'pending', 'a durable pending journal must precede every remote write');
    if (path === `/data_sources/${id(3)}`) {
      assert.equal(latest.operation, 'convert');
      assert.deepEqual(body, {properties: {'st%3A1': {relation: {data_source_id: id(2), type: 'dual_property', dual_property: {}}}}});
      makeDual('자동 생성된 관계');
    } else {
      assert.equal(path, `/data_sources/${id(2)}`);
      assert.equal(latest.operation, 'rename');
      assert.deepEqual(body, {properties: {'back%3A1': {name: '상담 기록'}}});
      rename('상담 기록');
    }
    const response = structuredClone(objects.get(path));
    await onPatch?.('after', context);
    return response;
  }};
  const save = async journal => {latest = structuredClone(journal); saves.push(latest);};
  return {notion, students, counseling, objects, calls, saves, save, rename, makeDual,
    get latest() {return latest;}, get patchCount() {return patchCount;}, get queryCount() {return queryCount;}};
}

test('catalog publishes a specific first release, and targets accept only IDs with no extra state or secrets', () => {
  assert.equal(UPDATE_CATALOG[0].id, 'student-history-v1');
  assert.equal(UPDATE_CATALOG[0].version, '2026.10.1');
  assert.deepEqual(validateUpdateTargets(targets), targets);
  assert.deepEqual(validateUpdateTargets({...targets, students_data_source_id: id(2).replaceAll('-', '').toUpperCase()}), targets);
  for (const bad of [{...targets, api_key: 'private'}, {...targets, version: 2}, {...targets, students_data_source_id: id(3)}, {...targets, students_data_source_id: 'https://notion.so/' + id(2)}, {...targets, student_relation_property_id: 'name\n'}, {...targets, student_relation_property_id: '__proto__'}, {...targets, student_relation_property_id: 'x'.repeat(129)}]) assert.throws(() => validateUpdateTargets(bad));
});
test('already-linked dual relation is adopted using IDs without rows, renames, or layout writes', async () => {
  const f = fixture({dual: true, rows: 20, customName: '선생님이 붙인 이력 이름'});
  assert.deepEqual(await inspectStudentHistory(f.notion, manifest, targets), {status: 'schema_applied', reason: 'schema_verified', manual_layout_required: true, reverse_property_id: 'back%3A1'});
  f.calls.length = 0;
  const result = await applyStudentHistory(f.notion, manifest, targets, null, f.save);
  assert.equal(result.stage, 'schema_applied');
  assert.equal(result.manual_layout_required, true);
  assert.equal(f.queryCount, 0);
  assert.equal(f.patchCount, 0);
  assert.ok(f.students.properties['선생님이 붙인 이력 이름']);
  assert.equal(f.saves.length, 1);
});
test('populated single-direction sources are held with no record reads beyond one filtered existence row', async () => {
  const f = fixture({rows: 1});
  assert.equal((await inspectStudentHistory(f.notion, manifest, targets)).reason, 'existing_records');
  const result = await applyStudentHistory(f.notion, manifest, targets, null, f.save);
  assert.equal(result.stage, 'assistance_required');
  assert.equal(result.reason, 'existing_records');
  assert.equal(f.patchCount, 0);
  assert.doesNotMatch(JSON.stringify(f.saves), /properties|private|title/);
});
test('an empty source converts only its exact forward property and renames only the newly generated reciprocal', async () => {
  const f = fixture({sectionDepth: 4}); // Nine ancestry reads + ten operation calls = nineteen.
  const result = await applyStudentHistory(f.notion, manifest, targets, null, f.save);
  assert.equal(result.stage, 'schema_applied');
  assert.equal(f.patchCount, 2);
  assert.equal(f.queryCount, 2);
  assert.equal(f.calls.length, 19);
  assert.deepEqual(f.saves[0].before_property_ids, ['title']);
  assert.equal(f.saves[0].operation, 'convert');
  assert.ok(f.students.properties['상담 기록']);
  assert.equal(result.reverse_property_id, 'back%3A1');
  assert.equal(result.operation, null);
});
test('a row appearing in the final existence check prevents conversion', async () => {
  const f = fixture({onQuery: count => count === 2 ? {results: [{id: id(99)}], has_more: false, next_cursor: null} : undefined});
  const result = await applyStudentHistory(f.notion, manifest, targets, null, f.save);
  assert.equal(result.reason, 'existing_records');
  assert.equal(f.patchCount, 0);
});
test('incomplete or malformed empty responses do not authorize a schema mutation', async () => {
  for (const result of [{results: [], has_more: true, next_cursor: 'more'}, {results: [], has_more: false, next_cursor: 'more'}, {results: []}, {results: null, has_more: false}]) {
    const f = fixture({onQuery: () => result});
    await assert.rejects(() => applyStudentHistory(f.notion, manifest, targets, null, f.save));
    assert.equal(f.patchCount, 0);
  }
});
test('same-name unrelated properties hold both single and otherwise valid dual schemas', async () => {
  for (const dual of [false, true]) {
    const f = fixture({dual, customName: '나의 이력'});
    f.students.properties['상담 기록'] = field('unrelated', '상담 기록', 'rich_text');
    const result = await applyStudentHistory(f.notion, manifest, targets, null, f.save);
    assert.equal(result.reason, 'name_conflict');
    assert.equal(f.patchCount, 0);
    assert.equal(f.queryCount, 0);
  }
});
test('matching names alone cannot identify a reciprocal or repair a changed source relation', async () => {
  for (const mutate of [f => {f.students.properties['상담 기록'].relation.dual_property.synced_property_id = 'other';}, f => {f.counseling.properties.학생.id = 'other';}, f => {f.counseling.properties.학생.relation.data_source_id = id(90);}, f => {f.students.properties['상담 기록'].relation.data_source_id = id(90);}]) {
    const f = fixture({dual: true}); mutate(f);
    const result = await applyStudentHistory(f.notion, manifest, targets, null, f.save);
    assert.equal(result.stage, 'assistance_required');
    assert.equal(f.patchCount, 0);
  }
});
test('both real source databases must descend from the approved root', async () => {
  for (const source of [id(4), id(5)]) {
    const f = fixture();
    f.objects.get(`/databases/${source}`).parent = {type: 'workspace', workspace: true};
    await assert.rejects(() => applyStudentHistory(f.notion, manifest, targets, null, f.save), error => error.reason === 'outside_root');
    assert.equal(f.patchCount, 0);
  }
  const wrong = fixture();
  wrong.objects.get(`/databases/${id(4)}`).data_sources = [{id: id(92)}];
  await assert.rejects(() => inspectStudentHistory(wrong.notion, manifest, targets), error => error.reason === 'invalid_response');
});
test('excessive or cyclic parent structures stop before any mutation and within the request cap', async () => {
  const deep = fixture({sectionDepth: 5});
  await assert.rejects(() => applyStudentHistory(deep.notion, manifest, targets, null, deep.save));
  assert.ok(deep.calls.length <= 9);
  const cycle = fixture({sectionDepth: 1});
  cycle.objects.get(`/pages/${id(20)}`).parent.page_id = id(20);
  await assert.rejects(() => inspectStudentHistory(cycle.notion, manifest, targets), error => error.reason === 'outside_root');
});
test('a lost conversion response is reconciled by IDs without another conversion or a guessed rename', async () => {
  let failOnce = true;
  const f = fixture({onPatch: (phase, context) => {if (phase === 'after' && context.patchCount === 1 && failOnce) {failOnce = false; throw new Error('private provider data');}}});
  const first = await applyStudentHistory(f.notion, manifest, targets, null, f.save);
  assert.equal(first.stage, 'pending');
  assert.equal(first.reason, 'write_unconfirmed');
  f.rename('나중에 바꾼 이름');
  f.calls.length = 0;
  const result = await applyStudentHistory(f.notion, manifest, targets, first, f.save);
  assert.equal(result.stage, 'schema_applied');
  assert.equal(f.patchCount, 1);
  assert.ok(f.students.properties['나중에 바꾼 이름']);
  assert.ok(f.calls.every(call => call.method === 'GET'));
  assert.doesNotMatch(JSON.stringify(f.saves), /private provider data/);
});
test('a journaled conversion with no verified reciprocal never repeats the unknown write', async () => {
  const f = fixture({onPatch: phase => {if (phase === 'before') throw new Error('network uncertain');}});
  const first = await applyStudentHistory(f.notion, manifest, targets, null, f.save);
  assert.equal(first.stage, 'pending');
  const second = await applyStudentHistory(f.notion, manifest, targets, first, f.save);
  assert.equal(second.reason, 'unconfirmed_conversion');
  assert.equal(f.patchCount, 1);
  const third = await applyStudentHistory(f.notion, manifest, targets, second, f.save);
  assert.equal(third.reason, 'unconfirmed_conversion');
  assert.equal(f.patchCount, 1);
});
test('a preexisting property cannot be adopted as a newly generated reverse after interruption', async () => {
  const f = fixture({onPatch: phase => {if (phase === 'before') throw new Error('uncertain');}});
  f.students.properties.기존 = field('back%3A1', '기존', 'rich_text');
  const first = await applyStudentHistory(f.notion, manifest, targets, null, f.save);
  delete f.students.properties.기존; f.makeDual('이름만 바꿈');
  const result = await applyStudentHistory(f.notion, manifest, targets, first, f.save);
  assert.equal(result.reason, 'ambiguous_conversion');
  assert.equal(f.patchCount, 1);
});
test('a concurrent custom rename after conversion is preserved', async () => {
  const f = fixture({onPatch: (phase, context) => {if (phase === 'after' && context.patchCount === 1) context.rename('교사가 정한 이름');}});
  const result = await applyStudentHistory(f.notion, manifest, targets, null, f.save);
  assert.equal(result.stage, 'schema_applied');
  assert.ok(f.students.properties['교사가 정한 이름']);
  assert.equal(f.patchCount, 1);
});
test('an unconfirmed rename is reconciled without renaming again', async () => {
  const f = fixture({onPatch: (phase, context) => {if (phase === 'after' && context.patchCount === 2) throw new Error('unknown');}});
  const first = await applyStudentHistory(f.notion, manifest, targets, null, f.save);
  assert.equal(first.operation, 'rename');
  assert.equal(first.stage, 'pending');
  const result = await applyStudentHistory(f.notion, manifest, targets, first, f.save);
  assert.equal(result.stage, 'schema_applied');
  assert.equal(f.patchCount, 2);
});
test('durable journal failure blocks the remote write, and changed target binding cannot reuse a journal', async () => {
  const f = fixture();
  await assert.rejects(() => applyStudentHistory(f.notion, manifest, targets, null, async () => {throw new Error('storage failure');}));
  assert.equal(f.patchCount, 0);
  const good = fixture({dual: true});
  const applied = await applyStudentHistory(good.notion, manifest, targets, null, good.save);
  await assert.rejects(() => applyStudentHistory(good.notion, manifest, targets, {...applied, binding: {...applied.binding, root_page_id: id(92)}}, good.save), error => error.reason === 'journal_mismatch');
});
test('an applied schema removed by a teacher is not recreated on repeated application', async () => {
  const f = fixture({dual: true});
  const applied = await applyStudentHistory(f.notion, manifest, targets, null, f.save);
  f.counseling.properties.학생.relation = {data_source_id: id(2), type: 'single_property', single_property: {}};
  delete f.students.properties['상담 기록'];
  const first = await applyStudentHistory(f.notion, manifest, targets, applied, f.save);
  const second = await applyStudentHistory(f.notion, manifest, targets, first, f.save);
  assert.equal(first.reason, 'applied_schema_changed');
  assert.equal(second.reason, 'applied_schema_changed');
  assert.equal(f.patchCount, 0);
});
test('a fully completed journal can be verified, and completion cannot authorize recreation', async () => {
  const f = fixture({dual: true});
  const applied = await applyStudentHistory(f.notion, manifest, targets, null, f.save);
  const complete = {...applied, stage: 'complete', layout_confirmed: true};
  assert.equal((await applyStudentHistory(f.notion, manifest, targets, complete, f.save)).stage, 'schema_applied');
  f.counseling.properties.학생.relation = {data_source_id: id(2), type: 'single_property', single_property: {}};
  delete f.students.properties['상담 기록'];
  assert.equal((await applyStudentHistory(f.notion, manifest, targets, complete, f.save)).reason, 'applied_schema_changed');
  assert.equal(f.patchCount, 0);
});
