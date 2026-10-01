/** Selective schema updates. No page contents, relation values, or layouts are written.
 * The caller owns an installation lease and durably saves each supplied journal.
 */
export const UPDATE_CATALOG = Object.freeze([Object.freeze({
  id: 'student-history-v1', version: '2026.10.1', title: '학생별 상담 이력 연결',
  summary: '기존 학생·상담 기록을 유지하며 양방향 관계를 확인합니다. 학생 화면의 이력 보기는 별도로 확인합니다.',
})]);

const UPDATE_ID = UPDATE_CATALOG[0].id;
const REVERSE_NAME = '상담 기록';
const TARGET_KEYS = ['version', 'students_data_source_id', 'counseling_data_source_id', 'student_relation_property_id'];
const MAX_CALLS = 19, MAX_TARGET_READS = 9, MAX_PARENT_HOPS = 6, MAX_JOURNAL_BYTES = 12000;
const clone = value => structuredClone(value);
class UpdateError extends Error {
  constructor(reason) { super('업데이트 대상을 확인할 수 없습니다.'); this.name = 'UpdateError'; this.reason = reason; this.status = 409; }
}
const fail = reason => { throw new UpdateError(reason); };
const plainObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
function uuid(value) {
  if (typeof value !== 'string' || !/^(?:[a-f\d]{32}|[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12})$/i.test(value) || /^0+$/.test(value.replaceAll('-', ''))) fail('invalid_targets');
  const compact = value.replaceAll('-', '').toLowerCase();
  return `${compact.slice(0, 8)}-${compact.slice(8, 12)}-${compact.slice(12, 16)}-${compact.slice(16, 20)}-${compact.slice(20)}`;
}
function same(a, b) { return typeof a === 'string' && typeof b === 'string' && a.replaceAll('-', '').toLowerCase() === b.replaceAll('-', '').toLowerCase(); }
function propertyId(value) {
  // IDs are JSON keys, never schema names or executable path fragments.
  if (typeof value !== 'string' || !/^[\x21-\x7e]{1,128}$/.test(value) || ['__proto__', 'constructor', 'prototype'].includes(value)) fail('invalid_targets');
  return value;
}
function encodedPropertyId(value) {
  // Notion returns URL-encoded property IDs. Preserve one encoding layer.
  try { return encodeURIComponent(decodeURIComponent(value)); } catch { return encodeURIComponent(value); }
}
export function validateUpdateTargets(value) {
  if (!plainObject(value) || Object.keys(value).length !== TARGET_KEYS.length || TARGET_KEYS.some(key => !Object.hasOwn(value, key)) || value.version !== 1) fail('invalid_targets');
  const result = {version: 1, students_data_source_id: uuid(value.students_data_source_id), counseling_data_source_id: uuid(value.counseling_data_source_id), student_relation_property_id: propertyId(value.student_relation_property_id)};
  if (result.students_data_source_id === result.counseling_data_source_id) fail('invalid_targets');
  return result;
}
function checkedObject(value, kind, id) {
  if (!value || value.object !== kind || !same(value.id, id) || value.archived || value.in_trash) fail('invalid_response');
  return value;
}
function properties(source) {
  if (!plainObject(source.properties)) fail('invalid_response');
  const seen = new Set();
  return Object.entries(source.properties).map(([name, property]) => {
    if (!plainObject(property) || typeof name !== 'string' || !name || property.name !== name || seen.has(propertyId(property.id))) fail('invalid_response');
    seen.add(property.id);
    return property;
  });
}
function byId(source, id) { return properties(source).find(property => property.id === id); }
function relationTarget(property, source, database) {
  return property?.type === 'relation' && same(property.relation?.data_source_id, source.id)
    && (!property.relation.database_id || same(property.relation.database_id, database));
}
function view(status, reason, reverse) {
  return {status, reason, manual_layout_required: true, ...(reverse ? {reverse_property_id: reverse.id} : {})};
}
function inspectSchema(context) {
  const {students, counseling, targets} = context;
  const forward = byId(counseling, targets.student_relation_property_id);
  if (!relationTarget(forward, students, students.parent.database_id)) return view('assistance_required', 'source_relation_changed');
  const namesake = properties(students).find(property => property.name === REVERSE_NAME);
  if (forward.relation.type === 'single_property') {
    if (namesake) return view('assistance_required', 'name_conflict');
    return view('ready', 'empty_check_required');
  }
  if (forward.relation.type !== 'dual_property') return view('assistance_required', 'source_relation_changed');
  const id = forward.relation.dual_property?.synced_property_id;
  const reverse = id && byId(students, id);
  if (namesake && namesake.id !== id) return view('assistance_required', 'name_conflict');
  if (!relationTarget(reverse, counseling, counseling.parent.database_id)
    || reverse.relation.type !== 'dual_property'
    || reverse.relation.dual_property?.synced_property_id !== forward.id
    || forward.relation.dual_property?.synced_property_name !== reverse.name
    || reverse.relation.dual_property?.synced_property_name !== forward.name) return view('assistance_required', 'reciprocal_mismatch');
  return view('schema_applied', 'schema_verified', reverse);
}
async function contextFor(notion, manifest, input) {
  const targets = validateUpdateTargets(input), rootId = uuid(manifest?.root_page_id);
  if ([targets.students_data_source_id, targets.counseling_data_source_id].includes(rootId)) fail('invalid_targets');
  let calls = 0;
  const request = async (method, path, body) => {
    if (calls >= MAX_CALLS) fail('request_limit');
    calls++;
    return notion.request(method, path, body);
  };
  const cache = new Map();
  async function get(kind, rawId) {
    const id = uuid(rawId), key = `${kind}:${id}`;
    if (!cache.has(key)) {
      if (cache.size >= MAX_TARGET_READS) fail('ancestry_limit');
      const endpoint = {page: 'pages', block: 'blocks', database: 'databases', data_source: 'data_sources'}[kind];
      if (!endpoint) fail('outside_root');
      cache.set(key, checkedObject(await request('GET', `/${endpoint}/${id}`), kind, id));
    }
    return cache.get(key);
  }
  const root = await get('page', rootId);
  async function inside(object) {
    const visited = new Set();
    for (let depth = 0; depth <= MAX_PARENT_HOPS; depth++) {
      if (object.object === 'page' && same(object.id, root.id)) return;
      const key = `${object.object}:${uuid(object.id)}`;
      if (visited.has(key)) fail('outside_root');
      visited.add(key);
      const type = object.parent?.type;
      if (!['page_id', 'block_id', 'database_id', 'data_source_id'].includes(type) || depth === MAX_PARENT_HOPS) fail('outside_root');
      object = await get(type.slice(0, -3), object.parent[type]);
    }
    fail('outside_root');
  }
  async function dataSource(id) {
    const source = await get('data_source', id);
    if (source.parent?.type !== 'database_id') fail('invalid_response');
    const database = await get('database', source.parent.database_id);
    if (!Array.isArray(database.data_sources) || database.data_sources.filter(item => same(item?.id, id)).length !== 1) fail('invalid_response');
    await inside(database);
    properties(source);
    return source;
  }
  const students = await dataSource(targets.students_data_source_id);
  const counseling = await dataSource(targets.counseling_data_source_id);
  const context = {targets, rootId, students, counseling, request};
  context.reload = async () => {
    for (const key of ['students', 'counseling']) {
      const before = context[key], after = checkedObject(await request('GET', `/data_sources/${uuid(before.id)}`), 'data_source', before.id);
      if (after.parent?.type !== 'database_id' || !same(after.parent.database_id, before.parent.database_id)) fail('outside_root');
      properties(after);
      context[key] = after;
    }
  };
  context.empty = async () => {
    const path = `/data_sources/${targets.counseling_data_source_id}/query?filter_properties=${encodedPropertyId(targets.student_relation_property_id)}`;
    const response = await request('POST', path, {page_size: 1});
    if (!Array.isArray(response?.results) || typeof response.has_more !== 'boolean' || response.results.length > 1) fail('invalid_response');
    if (response.results.length) return false;
    if (response.has_more !== false || response.next_cursor != null) fail('incomplete_query');
    return true;
  };
  return context;
}
export async function inspectStudentHistory(notion, manifest, targets) {
  const context = await contextFor(notion, manifest, targets), result = inspectSchema(context);
  if (result.status !== 'ready') return result;
  return await context.empty() ? view('ready', 'empty_database') : view('assistance_required', 'existing_records');
}

function bindingFor(context) { return {root_page_id: context.rootId, ...context.targets}; }
function sameBinding(left, right) {
  return plainObject(left) && Object.keys(left).length === Object.keys(right).length && Object.entries(right).every(([key, value]) => left[key] === value);
}
function previousJournal(input, context) {
  if (input == null || (plainObject(input) && Object.keys(input).length === 0)) return null;
  if (!plainObject(input) || input.version !== 1 || input.update_id !== UPDATE_ID || !sameBinding(input.binding, bindingFor(context))
    || !['pending', 'schema_applied', 'assistance_required', 'complete'].includes(input.stage)
    || (input.operation != null && !['convert', 'rename'].includes(input.operation))
    || (input.before_property_ids != null && (!Array.isArray(input.before_property_ids) || input.before_property_ids.length > 200 || input.before_property_ids.some(id => typeof id !== 'string')))) fail('journal_mismatch');
  return clone(input);
}
/** Applies only a proven-empty one-way relation. A populated one-way relation requires
 * the documented AI-assisted migration, including a complete relation-value backup.
 */
export async function applyStudentHistory(notion, manifest, targets, journal, save) {
  if (typeof save !== 'function') fail('journal_required');
  const context = await contextFor(notion, manifest, targets), previous = previousJournal(journal, context);
  const base = {version: 1, update_id: UPDATE_ID, binding: bindingFor(context), manual_layout_required: true};
  let current = {...(previous ?? {}), ...base};
  async function persist(fields) {
    current = {...current, ...fields};
    if (new TextEncoder().encode(JSON.stringify(current)).byteLength > MAX_JOURNAL_BYTES) fail('journal_limit');
    await save(clone(current));
    return clone(current);
  }
  const hold = reason => persist({stage: 'assistance_required', reason});
  const completed = result => persist({stage: 'schema_applied', reason: 'schema_verified', reverse_property_id: result.reverse_property_id, operation: null});
  let result = inspectSchema(context);
  if (result.status === 'assistance_required') return hold(result.reason);
  if (result.status === 'schema_applied') {
    // An unknown conversion can be adopted only if its reciprocal did not exist
    // before the operation. Never rename a property on this recovery path.
    if (previous?.operation === 'convert' && (!Array.isArray(previous.before_property_ids) || previous.before_property_ids.includes(result.reverse_property_id))) return hold('ambiguous_conversion');
    if (previous?.reverse_property_id && previous.reverse_property_id !== result.reverse_property_id) return hold('reciprocal_changed');
    return completed(result);
  }
  if (previous?.operation) return hold('unconfirmed_conversion');
  if (['schema_applied', 'complete'].includes(previous?.stage) || previous?.reverse_property_id) return hold('applied_schema_changed');
  if (!await context.empty()) return hold('existing_records');
  const before = properties(context.students).map(property => property.id);
  if (before.length > 200) return hold('schema_too_large');
  // Re-read both schemas and query without a row filter immediately before the
  // mutation. Notion has no transaction or compare-and-swap schema endpoint;
  // the teacher must pause concurrent editing while applying the update.
  await context.reload();
  result = inspectSchema(context);
  if (result.status === 'schema_applied') return completed(result);
  if (result.status !== 'ready') return hold(result.reason);
  const finalBefore = properties(context.students).map(property => property.id);
  if (JSON.stringify([...before].sort()) !== JSON.stringify([...finalBefore].sort())) return hold('schema_changed');
  if (!await context.empty()) return hold('existing_records');
  await persist({stage: 'pending', reason: 'conversion_pending', operation: 'convert', before_property_ids: finalBefore});
  let response;
  try {
    response = await context.request('PATCH', `/data_sources/${context.targets.counseling_data_source_id}`, {properties: {
      [context.targets.student_relation_property_id]: {relation: {data_source_id: context.targets.students_data_source_id, type: 'dual_property', dual_property: {}}},
    }});
  } catch {
    return persist({stage: 'pending', reason: 'write_unconfirmed'});
  }
  let generated;
  try {
    checkedObject(response, 'data_source', context.targets.counseling_data_source_id);
    const source = byId(response, context.targets.student_relation_property_id);
    if (!relationTarget(source, context.students, context.students.parent.database_id) || source.relation.type !== 'dual_property') fail('invalid_response');
    generated = source.relation.dual_property;
    propertyId(generated?.synced_property_id);
    if (typeof generated.synced_property_name !== 'string' || finalBefore.includes(generated.synced_property_id)) fail('invalid_response');
  } catch { return persist({stage: 'pending', reason: 'write_unconfirmed'}); }
  await persist({stage: 'pending', reason: 'verification_pending', reverse_property_id: generated.synced_property_id, generated_name: generated.synced_property_name});
  await context.reload();
  result = inspectSchema(context);
  if (result.status !== 'schema_applied' || result.reverse_property_id !== generated.synced_property_id) return hold('reciprocal_mismatch');
  const reverse = byId(context.students, result.reverse_property_id);
  // A user's concurrent rename is retained, including their chosen label.
  if (reverse.name === REVERSE_NAME || reverse.name !== generated.synced_property_name) return completed(result);
  await persist({stage: 'pending', reason: 'rename_pending', operation: 'rename'});
  try {
    await context.request('PATCH', `/data_sources/${context.targets.students_data_source_id}`, {properties: {[reverse.id]: {name: REVERSE_NAME}}});
  } catch { return persist({stage: 'pending', reason: 'write_unconfirmed'}); }
  await context.reload();
  result = inspectSchema(context);
  if (result.status !== 'schema_applied' || result.reverse_property_id !== reverse.id) return hold('reciprocal_mismatch');
  return completed(result);
}
