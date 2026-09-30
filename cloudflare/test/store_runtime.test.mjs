import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { Store, BusyError, LeaseError, LEASE_MS, koreaDay, morning } from '../src/store.mjs';
import { runCron, schoolCacheKey } from '../src/runtime.mjs';
import { createNotionClient as realClient, syncStep as realStep, eventId, LIMITS } from '../src/sync.mjs';

// Real SQLite executes production SQL. This shim only supplies D1's binding,
// async result shapes and atomic batch transaction; it is not a cloud test.
class D1 {
  constructor() { this.sql = new DatabaseSync(':memory:'); this.sql.exec(readFileSync(new URL('../migrations/0001_initial.sql', import.meta.url), 'utf8')); }
  prepare(sql) {
    const db = this.sql;
    return { bind(...args) {
      return {
        async first() { return db.prepare(sql).get(...args) ?? null; },
        async run() { return { success: true, meta: db.prepare(sql).run(...args) }; },
        allSync() { return { success: true, results: db.prepare(sql).all(...args) }; },
      };
    } };
  }
  async batch(statements) {
    this.sql.exec('BEGIN');
    try { const result = statements.map(statement => statement.allSync()); this.sql.exec('COMMIT'); return result; }
    catch (error) { this.sql.exec('ROLLBACK'); throw error; }
  }
}
const fakeId = number => `12345678-1234-4123-8123-${String(number).padStart(12, '0')}`;
function manifest(number = 1) { return { version: 1, office_code: 'B10', school_code: '1234567', school_name: '가상학교', academic_year: 2026,
  root_page_id: fakeId(number), agenda_data_source_id: fakeId(101), meals_block_id: fakeId(102), status_block_id: fakeId(103) }; }
const identity = { owner_id: 'owner', workspace_id: 'workspace', bot_id: 'bot' };
const statusTarget = (m=manifest()) => ({id:m.status_block_id,type:'callout',container_id:m.status_block_id});
const error = (status, retryable = false) => Object.assign(new Error('PRIVATE_DO_NOT_LOG'), { status, retryable });
function fixture(options = {}) {
  const clock = { value: Date.parse('2026-09-30T07:00:00+09:00') }, db = new D1();
  const store = new Store(db, { now: () => clock.value, ...options });
  const calls = { snapshot: 0, step: 0, rotate: 0, access: [], writes: [] };
  const env = { DB: db, PUBLIC_BASE_URL: 'https://planner.example.com', NEIS_API_KEY: 'fake-neis-test-key' };
  const dependencies = { store, now: () => clock.value,
    decrypt: async value => value === 'encrypted' ? { access_token: 'access', refresh_token: 'refresh' } : JSON.parse(value),
    encrypt: async value => JSON.stringify(value),
    exchangeTokens: async () => { calls.rotate++; return { access_token: 'new-access', refresh_token: 'new-refresh', expires_in: 3600 }; },
    fetchSchoolSnapshot: async (_manifest, day) => { calls.snapshot++; return { calendar: { rows: [] }, meals: { date: day } }; },
    createNotionClient: access => { calls.access.push(access); return { request: async (...args) => calls.writes.push(args) }; },
    syncStep: async ({ state, saveState, manifest }) => { calls.step++; state.count = (state.count || 0) + 1; await saveState(state); return { done: true, state, statusTarget:statusTarget(manifest) }; },
  };
  return { db, store, clock, calls, env, dependencies, run: () => runCron(env, dependencies),
    register: (number = 1) => store.register(manifest(number), identity, 'encrypted') };
}
async function claim(f) { const global = await f.store.claimGlobal(); const work = await f.store.claimDue(global); return { global, ...work }; }

test('installation cap is atomic across competing enrollments; reauthorization preserves state', async () => {
  const f = fixture({ maxInstallations: 2 });
  const results = await Promise.allSettled(Array.from({ length: 12 }, (_, i) => f.register(i + 1)));
  assert.equal(results.filter(value => value.status === 'fulfilled').length, 2);
  const id = results.find(value => value.status === 'fulfilled').value;
  const doc = await f.store.get(id);
  f.db.sql.prepare('UPDATE planner_installations SET state_json = ? WHERE id = ?').run('{"pending":{"key":"keep"}}', id);
  await f.store.register(doc.manifest, identity, 'replacement-encrypted');
  assert.equal((await f.store.get(id)).state.pending.key, 'keep');
  await assert.rejects(f.store.register(doc.manifest, { ...identity, owner_id: 'other' }, 'forbidden'));
  await assert.rejects(f.store.register({ ...doc.manifest, school_code: '7654321' }, identity, 'forbidden'));
  assert.equal((await f.store.get(id)).credentials, 'replacement-encrypted');
});

test('strict resource bounds reject nonpositive, nonfinite and oversized configuration', () => {
  for (const value of [0, -1, NaN, Infinity, 'invalid', 10001]) assert.throws(() => fixture({ maxDailySteps: value }));
  for (const value of [0, -1, NaN, Infinity, 'invalid', 5001]) assert.throws(() => fixture({ maxInstallations: value }));
});

test('one global owner and one budget reservation survive concurrent claims', async () => {
  const f = fixture(); await f.register();
  const globals = await Promise.all(Array.from({ length: 10 }, () => f.store.claimGlobal()));
  assert.equal(globals.filter(Boolean).length, 1);
  const global = globals.find(Boolean);
  const works = await Promise.all(Array.from({ length: 10 }, () => f.store.claimDue(global)));
  assert.equal(works.filter(Boolean).length, 1);
  assert.equal(f.db.sql.prepare('SELECT steps FROM planner_scheduler').get().steps, 1);
});

test('expired worker cannot save or finish after a new fenced owner claims', async () => {
  const f = fixture(); const id = await f.register();
  const old = await claim(f);
  await f.store.saveState(old.lease, { pending: { key: 'preserve' } });
  f.clock.value += LEASE_MS + 1;
  const fresh = await claim(f);
  await assert.rejects(f.store.saveState(old.lease, { pending: null }), LeaseError);
  await assert.rejects(f.store.finish(old.lease, { done: true }), LeaseError);
  await f.store.releaseGlobal(old.global);
  await f.store.saveState(fresh.lease, { pending: { key: 'new-owner' } });
  assert.equal((await f.store.get(id)).state.pending.key, 'new-owner');
});

test('pause and reauthorization are blocked during a lease; paused notebooks are not claimed', async () => {
  const f = fixture(); const id = await f.register(); const work = await claim(f);
  await assert.rejects(f.store.setEnabled(id, identity.owner_id, false), BusyError);
  await assert.rejects(f.register(), BusyError);
  await f.store.finish(work.lease); await f.store.releaseGlobal(work.global);
  await assert.rejects(f.store.setEnabled(id, 'wrong-owner', false));
  await f.store.setEnabled(id, identity.owner_id, false);
  assert.equal((await f.run()).status, 'idle_or_budget');
  assert.equal(f.calls.step, 0);
  await f.store.setEnabled(id, identity.owner_id, true);
  assert.equal((await f.run()).status, 'complete');
});

test('a completed notebook runs once per Korean day and next daily run waits for 07:00', async () => {
  const f = fixture(); const id = await f.register();
  assert.equal((await f.run()).status, 'complete');
  f.clock.value += 60_000;
  assert.equal((await f.run()).status, 'idle_or_budget');
  await f.store.setEnabled(id, identity.owner_id, false);
  await f.store.setEnabled(id, identity.owner_id, true);
  assert.equal((await f.run()).status, 'idle_or_budget');
  f.clock.value = Date.parse('2026-10-01T06:59:00+09:00');
  assert.equal((await f.run()).status, 'idle_or_budget');
  f.clock.value += 60_000;
  assert.equal((await f.run()).status, 'complete');
  assert.equal(f.calls.step, 2); assert.equal(f.calls.snapshot, 2); assert.equal(f.calls.rotate, 0);
  assert.equal((await f.store.get(id)).last_success_day, '2026-10-01');
});

test('initial enrollment and its bounded continuation can run before 07:00', async () => {
  const f = fixture(); f.clock.value = Date.parse('2026-09-30T05:00:00+09:00');
  const id = await f.register();
  f.dependencies.syncStep = async ({ state, saveState }) => { state.count = (state.count || 0) + 1; await saveState(state); return { done: state.count === 2, state, statusTarget:statusTarget() }; };
  assert.equal((await f.run()).status, 'continued'); f.clock.value += 60_000;
  assert.equal((await f.run()).status, 'complete');
  await f.store.setEnabled(id, identity.owner_id, true);
  assert.equal((await f.store.get(id)).next_due, morning('2026-10-01'));
  f.clock.value = morning('2026-10-01') - 60_000;
  assert.equal((await f.run()).status, 'idle_or_budget');
});

test('daily quota defers continuation without erasing pending or claiming success', async () => {
  const f = fixture({ maxDailySteps: 1 }); const id = await f.register();
  f.dependencies.syncStep = async ({ state, saveState }) => { state.pending = { key: 'uncertain' }; await saveState(state); return { done: false, state }; };
  assert.equal((await f.run()).status, 'continued'); f.clock.value += 60_000;
  assert.equal((await f.run()).status, 'idle_or_budget');
  let doc = await f.store.get(id);
  assert.equal(doc.status, 'quota_wait'); assert.equal(doc.last_success_day, null); assert.equal(doc.state.pending.key, 'uncertain');
  assert.equal(doc.next_due, morning('2026-10-01'));
  f.clock.value = morning('2026-10-01');
  assert.equal((await f.run()).status, 'continued');
  assert.equal(f.db.sql.prepare('SELECT steps FROM planner_scheduler').get().steps, 1);
});

test('three transient failures defer to next day; pending persists and success resets failures', async () => {
  const f = fixture(); const id = await f.register();
  f.dependencies.syncStep = async ({ state, saveState }) => { state.pending = { key: 'maybe-written' }; await saveState(state); throw error(503); };
  for (const [index, expected] of ['retrying', 'retrying', 'retry_tomorrow'].entries()) {
    assert.equal((await f.run()).status, expected);
    const doc = await f.store.get(id);
    assert.equal(doc.failures, index + 1); assert.equal(doc.state.pending.key, 'maybe-written');
    assert.equal(doc.last_success_at, null); f.clock.value = doc.next_due;
  }
  f.dependencies.syncStep = async ({ state }) => ({ done: true, state, statusTarget:statusTarget() });
  assert.equal((await f.run()).status, 'complete');
  assert.equal((await f.store.get(id)).failures, 0);
});

test('successful partial progress resets consecutive retry counter', async () => {
  const f = fixture(); const id = await f.register();
  f.dependencies.syncStep = async () => { throw error(429); };
  await f.run(); f.clock.value = (await f.store.get(id)).next_due;
  f.dependencies.syncStep = async ({ state }) => ({ done: false, state });
  assert.equal((await f.run()).status, 'continued');
  assert.equal((await f.store.get(id)).failures, 0);
});

test('a step finishing after the minute remains eligible at the next Cron tick', async () => {
  const f = fixture(); const initial = f.clock.value, id = await f.register();
  f.dependencies.syncStep = async ({ state }) => { f.calls.step++; f.clock.value += 2000; return { done: false, state }; };
  assert.equal((await f.run()).status, 'continued');
  assert.equal((await f.store.get(id)).next_due, initial + 2000);
  f.clock.value = initial + 60_000;
  assert.equal((await f.run()).status, 'continued');
  assert.equal(f.calls.step, 2);
});

test('three early enrollment failures defer to tomorrow, not another attempt at 07:00 today', async () => {
  const f = fixture(); f.clock.value = Date.parse('2026-09-30T05:00:00+09:00');
  const id = await f.register();
  f.dependencies.syncStep = async () => { throw error(503); };
  for (let i = 0; i < 3; i++) { await f.run(); f.clock.value = (await f.store.get(id)).next_due; }
  assert.equal(f.clock.value, morning('2026-10-01'));
  assert.equal((await f.store.get(id)).last_success_at, null);
});

test('oversized durable state is rejected without replacing the saved checkpoint', async () => {
  const f = fixture(); const id = await f.register(); const work = await claim(f);
  await f.store.saveState(work.lease, { pending: 'keep' });
  await assert.rejects(f.store.saveState(work.lease, { pending: 'x'.repeat(600_000) }));
  assert.deepEqual((await f.store.get(id)).state, { pending: 'keep' });
});

test('permanent source conflicts stop until explicit resume and do not mark success', async () => {
  const f = fixture(); const id = await f.register();
  f.dependencies.syncStep = async () => { throw error(400); };
  assert.equal((await f.run()).status, 'attention');
  const doc = await f.store.get(id);
  assert.equal(doc.enabled, false); assert.equal(doc.last_success_at, null);
  assert.equal((await f.run()).status, 'idle_or_budget');
});

test('public snapshots are shared per school/year/day and expired cache cleanup is bounded', async () => {
  const f = fixture(); await f.register(); await f.register(2);
  assert.equal((await f.run()).status, 'complete'); assert.equal((await f.run()).status, 'complete');
  assert.equal(f.calls.snapshot, 1);
  assert.notEqual(await schoolCacheKey(manifest(), '2026-09-30'), await schoolCacheKey(manifest(), '2026-10-01'));
  assert.notEqual(await schoolCacheKey(manifest(), '2026-09-30'), await schoolCacheKey({ ...manifest(), academic_year: 2027 }, '2026-09-30'));
  for (let i = 0; i < 12; i++) f.db.sql.prepare('INSERT INTO planner_school_cache VALUES (?, ?, ?)').run('expired-' + i, '{}', 0);
  await f.store.cleanup();
  assert.equal(f.db.sql.prepare('SELECT count(*) count FROM planner_school_cache WHERE expires_at = 0').get().count, 8);
  const plan = f.db.sql.prepare('EXPLAIN QUERY PLAN SELECT id FROM planner_installations WHERE enabled = 1 AND next_due <= ? ORDER BY next_due, id LIMIT 1').all(f.clock.value);
  assert.match(plan.map(row => row.detail).join(' '), /planner_due/);
});

test('401 rotates once and resumes later; other successful steps do not rotate', async () => {
  const f = fixture(); const id = await f.register(); let attempts = 0;
  f.dependencies.syncStep = async ({ state, saveState }) => { attempts++; state.pending = { key: 'retain' }; await saveState(state); if (attempts === 1) throw error(401); return { done: true, state, statusTarget:statusTarget() }; };
  assert.equal((await f.run()).status, 'token_refreshed');
  assert.equal(attempts, 1); assert.equal(f.calls.rotate, 1);
  assert.equal((await f.store.get(id)).state.pending.key, 'retain');
  f.clock.value += 60_000;
  assert.equal((await f.run()).status, 'complete');
  assert.deepEqual(f.calls.access, ['access', 'new-access']); assert.equal(f.calls.rotate, 1);
});

test('unknown token rotation outcome is never replayed and durable pending remains', async () => {
  const f = fixture(); const id = await f.register();
  f.dependencies.decrypt = async () => ({ access_token: 'expired', refresh_token: 'refresh', expires_at: f.clock.value - 1 });
  f.dependencies.exchangeTokens = async () => { f.calls.rotate++; throw new Error('PRIVATE_TRANSPORT_FAILURE'); };
  assert.equal((await f.run()).status, 'reconnect');
  assert.equal((await f.store.get(id)).rotation_pending, true);
  await f.store.setEnabled(id, identity.owner_id, true);
  assert.equal((await f.run()).status, 'reconnect');
  assert.equal(f.calls.rotate, 1); assert.equal(f.calls.step, 0);
});

test('definite OAuth server failure clears rotation flag and uses bounded retry', async () => {
  const f = fixture(); const id = await f.register();
  f.dependencies.decrypt = async () => ({ access_token: 'expired', refresh_token: 'refresh', expires_at: f.clock.value - 1 });
  f.dependencies.exchangeTokens = async () => { throw error(503); };
  assert.equal((await f.run()).status, 'retrying');
  assert.equal((await f.store.get(id)).rotation_pending, false);
  assert.equal((await f.store.get(id)).credentials, 'encrypted');
});

test('failed credential checkpoint after rotation requires reconnect without remote writes', async () => {
  const f = fixture(); const id = await f.register();
  f.dependencies.decrypt = async () => ({ access_token: 'expired', refresh_token: 'refresh', expires_at: f.clock.value - 1 });
  f.store.saveCredentials = async () => { throw new Error('storage uncertain'); };
  assert.equal((await f.run()).status, 'reconnect');
  assert.equal((await f.store.get(id)).rotation_pending, true);
  assert.equal(f.calls.step, 0); assert.equal(f.calls.writes.length, 0);
});

test('checkpoint failure stops the engine before its mutation and preserves old state', async () => {
  const f = fixture(); const id = await f.register(); let remoteWrites = 0;
  f.store.saveState = async () => { throw new LeaseError(); };
  f.dependencies.syncStep = async ({ saveState }) => { await saveState({ pending: 'new' }); remoteWrites++; return { done: false }; };
  assert.equal((await f.run()).status, 'lease_lost');
  assert.equal(remoteWrites, 0); assert.deepEqual((await f.store.get(id)).state, {});
});

test('academic year end disables without fetching or claiming success', async () => {
  const f = fixture(); const id = await f.register(); f.clock.value = morning('2027-03-01');
  assert.equal((await f.run()).status, 'academic_year_ended');
  assert.equal(f.calls.snapshot, 0); assert.equal((await f.store.get(id)).last_success_day, null);
});

test('a Korean day rollover invalidates an outstanding write fence', async () => {
  const f = fixture(); await f.register(); const work = await claim(f);
  f.clock.value = Date.parse('2026-10-01T00:00:00+09:00');
  assert.equal(koreaDay(f.clock.value), '2026-10-01');
  await assert.rejects(f.store.saveState(work.lease, { overwrite: true }), LeaseError);
});

for(const native of [false,true]) test(`real engine + bounded Notion client + SQLite recover a lost creation (${native?'native paragraph':'flat callout'} targets)`, async () => {
  const f = fixture(), day = koreaDay(Date.now());
  const year = Number(day.slice(0, 4)) - (day.slice(5, 7) < '03' ? 1 : 0);
  f.clock.value = morning(day);
  const m = { ...manifest(), academic_year: year }, id = await f.store.register(m, identity, 'encrypted');
  const rich = value => [{ type: 'text', text: { content: value } }];
  const text = parts => parts.map(part => part.text?.content || part.plain_text || '').join('');
  const schema = Object.fromEntries(Object.entries({ 이름: 'title', 일정: 'date', 학년도: 'number', 종류: 'select', 상태: 'select',
    '업무 분류': 'select', '외부 ID': 'rich_text', 보관: 'checkbox' }).map(([name, type]) => [name, { type }]));
  const objects = new Map(), children = new Map();
  objects.set('/pages/' + m.root_page_id, { id: m.root_page_id, object: 'page', parent: { type: 'workspace', workspace: true } });
  objects.set('/databases/' + fakeId(200), { id: fakeId(200), object: 'database', parent: { type: 'page_id', page_id: m.root_page_id } });
  objects.set('/data_sources/' + m.agenda_data_source_id, { id: m.agenda_data_source_id, object: 'data_source', properties: schema,
    parent: { type: 'database_id', database_id: fakeId(200) } });
  for (const key of ['meals_block_id', 'status_block_id']) objects.set('/blocks/' + m[key], { id: m[key], object: 'block', type: 'callout',
    has_children: false, parent: { type: 'page_id', page_id: m.root_page_id }, callout: { rich_text: rich('미조회') } });
  const targetIds={meals:m.meals_block_id,status:m.status_block_id};
  if(native) {
    // Match the real notebook: meal callout in a column, status at page root.
    objects.set('/blocks/'+fakeId(201),{id:fakeId(201),object:'block',type:'column_list',has_children:true,parent:{type:'page_id',page_id:m.root_page_id}});
    objects.set('/blocks/'+fakeId(202),{id:fakeId(202),object:'block',type:'column',has_children:true,parent:{type:'block_id',block_id:fakeId(201)}});
    objects.get('/blocks/'+m.meals_block_id).parent={type:'block_id',block_id:fakeId(202)};
    for(const [index,name] of ['meals','status'].entries()) {
      const container=objects.get('/blocks/'+m[name+'_block_id']), childId=fakeId(210+index);
      container.callout.rich_text=[];container.has_children=true;
      children.set(container.id,[childId]);targetIds[name]=childId;
      objects.set('/blocks/'+childId,{id:childId,object:'block',type:'paragraph',has_children:false,archived:false,in_trash:false,
        parent:{type:'block_id',block_id:container.id},paragraph:{rich_text:rich('서버 연결 준비')}});
    }
  }
  const clients=[],requests=[],originalContainers=native?structuredClone([objects.get('/blocks/'+m.meals_block_id),objects.get('/blocks/'+m.status_block_id)]):null;
  let serial = 300, createCount = 0, loseResponse = true;
  const fetchFn = async (url, options) => {
    const parsed = new URL(url), path = parsed.pathname.replace('/v1', ''), method = options.method;
    const body = options.body ? JSON.parse(options.body) : undefined;
    requests.push([method,path,body]);
    const saved = (await f.store.get(id)).state;
    let result;
    if (method === 'GET' && path.endsWith('/children')) result = { results: (children.get(path.split('/')[2]) || []).map(key => objects.get('/blocks/' + key)), has_more: false };
    else if (method === 'GET') { result = objects.get(path); assert.ok(result, path); }
    else if (method === 'POST' && path.endsWith('/query')) {
      const filter = body.filter.rich_text;
      result = { results: [...objects.values()].filter(page => page.object === 'page' && page.parent?.data_source_id === m.agenda_data_source_id)
        .filter(page => filter.equals ? text(page.properties['외부 ID'].rich_text) === filter.equals : text(page.properties['외부 ID'].rich_text).startsWith(filter.starts_with)), has_more: false };
    } else if (method === 'POST' && path === '/pages') {
      assert.equal(saved.pending.kind, 'page', 'D1 pending must commit before a real client create request');
      createCount++;
      result = { id: fakeId(serial++), object: 'page', parent: body.parent, properties: body.properties };
      objects.set('/pages/' + result.id, result);
      children.set(result.id, []);
      for (const child of body.children) {
        const block = { ...child, id: fakeId(serial++), object: 'block', has_children: false, parent: { type: 'page_id', page_id: result.id } };
        objects.set('/blocks/' + block.id, block); children.get(result.id).push(block.id);
      }
      if (loseResponse) { loseResponse = false; throw new TypeError('simulated lost response'); }
    } else if (method === 'PATCH') {
      if (path === '/blocks/' + targetIds.status) assert.equal(saved.meals_date, day, 'status follows durable engine completion');
      else assert.ok(saved.mutation, 'D1 mutation must commit before a real client patch request');
      result = objects.get(path); assert.ok(result);
      if (body.callout) Object.assign(result.callout, body.callout);
      if (body.paragraph) Object.assign(result.paragraph, body.paragraph);
      if (body.properties) Object.assign(result.properties, body.properties);
    } else assert.fail('Unexpected bounded request');
    return Response.json(result);
  };
  const rows = [];
  for (const date of [`${year}-03-02`, `${year}-03-03`]) rows.push({ date, title: '가상 연속 행사', description: date + ' 설명', grades: [1, 2],
    school_name: m.school_name, course: '고등학교', day_night: '주간', day_type: '해당없음',
    external_id: await eventId(m.office_code, m.school_code, date, '가상 연속 행사', '주간', '고등학교') });
  const metadata = { office_code: m.office_code, school_code: m.school_code, school_name: m.school_name, fetched_at: new Date().toISOString() };
  f.dependencies.fetchSchoolSnapshot = async () => ({ calendar: { ...metadata, source: 'neis', academic_year: year, start: `${year}-03-01`,
    end: new Date(Date.UTC(year + 1, 2, 1) - 86400000).toISOString().slice(0, 10), rows }, meals: { ...metadata, source: 'neis-meals', date: day,
    rows: [{ meal_code: '2', meal_name: '중식', menu: '가상국 (1.2.5)' }] } });
  f.dependencies.createNotionClient = access => {const client=realClient(access,fetchFn);clients.push(client);return client;};
  f.dependencies.syncStep = realStep;
  assert.equal((await f.run()).status, 'continued');
  assert.equal(createCount, 0, 'today meals are displayed before the calendar backlog');
  assert.equal((await f.store.get(id)).state.meals_date, day);
  f.clock.value = (await f.store.get(id)).next_due;
  assert.equal((await f.run()).status, 'retrying');
  assert.equal((await f.store.get(id)).state.pending.kind, 'page');
  const outcomes = [];
  for (let attempt = 0; attempt < 5; attempt++) {
    f.clock.value = (await f.store.get(id)).next_due;
    const result = await f.run(); outcomes.push(result.status);
    if (result.status === 'complete') break;
  }
  assert.deepEqual(outcomes, ['continued', 'complete']);
  assert.equal(createCount, 1);
  const stored = await f.store.get(id), groups = Object.values(stored.state.groups);
  assert.equal(groups.length, 1); assert.equal(groups[0].source_ids.length, 2);
  assert.equal(stored.last_success_day, day); assert.equal(stored.state.pending, undefined);
  const page = objects.get('/pages/' + groups[0].page_id);
  assert.deepEqual(page.properties.일정.date, { start: `${year}-03-02`, end: `${year}-03-03` });
  assert.match(text(objects.get('/blocks/' + targetIds.meals)[native?'paragraph':'callout'].rich_text), /1\.2\.5/);
  assert.match(text(objects.get('/blocks/' + targetIds.status)[native?'paragraph':'callout'].rich_text), /반영 완료/);
  assert.ok(clients.every(client=>client.calls<=LIMITS.notionRequests));
  assert.equal(LIMITS.notionRequests,19);assert.equal(LIMITS.deadlineMs,45000);
  if(native) {
    assert.deepEqual([objects.get('/blocks/'+m.meals_block_id),objects.get('/blocks/'+m.status_block_id)],originalContainers);
    assert.equal(requests.filter(([method,path])=>method==='GET'&&[m.meals_block_id,m.status_block_id].some(id=>path===`/blocks/${id}/children`)).length,clients.length*2);
    assert.equal(requests.filter(([method,path])=>method==='PATCH'&&[m.meals_block_id,m.status_block_id].some(id=>path===`/blocks/${id}`)).length,0);
    assert.equal(requests.filter(([method,path])=>method==='PATCH'&&path==='/blocks/'+targetIds.status).length,1);
  }
});

test('runtime fails closed without a current bound status target',async()=>{
  for(const target of [undefined,{...statusTarget(),container_id:fakeId(999)},{...statusTarget(),id:fakeId(999)},{...statusTarget(),type:'paragraph'}]) {
    const f=fixture(),id=await f.register();
    f.dependencies.syncStep=async({state})=>({done:true,state,statusTarget:target});
    assert.equal((await f.run()).status,'attention');
    assert.equal(f.calls.writes.length,0);assert.equal((await f.store.get(id)).last_success_day,null);
  }
});
