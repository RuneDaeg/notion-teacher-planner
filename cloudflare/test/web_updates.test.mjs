import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

// Exercise the shipped browser script through its DOM events and HTTP requests.
// The stub deliberately has no app-specific logic; initial visibility comes from HTML.
const script = new vm.Script(await readFile(new URL('../../cloud/web/app.js', import.meta.url), 'utf8'));
const html = await readFile(new URL('../../cloud/web/index.html', import.meta.url), 'utf8');
const base = 'https://planner.example.com';
const rootA = '10000000-0000-4000-8000-000000000001';
const rootB = '10000000-0000-4000-8000-000000000002';
const manifest = {version: 1, office_code: 'X10', school_code: '1234567', school_name: '가상고등학교', academic_year: 2026,
  root_page_id: rootA, agenda_data_source_id: '10000000-0000-4000-8000-000000000003',
  meals_block_id: '10000000-0000-4000-8000-000000000004', status_block_id: '10000000-0000-4000-8000-000000000005'};
const targets = {version: 1, students_data_source_id: '10000000-0000-4000-8000-000000000006',
  counseling_data_source_id: '10000000-0000-4000-8000-000000000007', student_relation_property_id: 'student%3A1'};
const encode = value => Buffer.from(JSON.stringify(value)).toString('base64url');
const status = (overrides = {}) => ({school_name: manifest.school_name, academic_year: 2026, enabled: true,
  status: 'active', last_success_at: 1790000000, csrf: 'session-csrf', notion_url: 'https://notion.so/' + rootA.replaceAll('-', ''), ...overrides});
const release = (overrides = {}) => ({id: 'student-history-v1', title: '학생별 상담 이력 연결', version: '2026.10.1',
  summary: '기존 기록을 보존하며 관계를 확인합니다.', status: 'available', registered: false, ...overrides});

class Element {
  constructor(tagName) {this.tagName = tagName.toUpperCase(); this.hidden = false; this.disabled = false; this.children = []; this.listeners = new Map(); this._text = '';}
  set textContent(text) {this._text = String(text); this.children = [];}
  get textContent() {return this._text + this.children.map(child => child.textContent ?? '').join('');}
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this._text = ''; this.children = children;}
  addEventListener(type, callback) {const callbacks = this.listeners.get(type) ?? []; callbacks.push(callback); this.listeners.set(type, callbacks);}
  async click() {if (!this.disabled) for (const callback of this.listeners.get('click') ?? []) await callback({target: this});}
  select() {}
}
function descendants(element) {return element.children.flatMap(child => [child, ...descendants(child)]);}
function action(page, text) {
  const button = descendants(page.el('releases')).find(element => element.tagName === 'BUTTON' && element.textContent === text);
  assert.ok(button, `Expected visible release action: ${text}`);
  return button;
}
async function browser({path = '/connect', pending, route}) {
  const nodes = new Map();
  for (const match of html.matchAll(/<([a-z][\w-]*)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const element = new Element(match[1]);
    element.hidden = /\bhidden(?:\s|=|$)/.test(match[2]);
    nodes.set(match[3], element);
  }
  const el = id => {assert.ok(nodes.has(id), `Unknown element ${id}`); return nodes.get(id);};
  const document = {getElementById: el, createElement: tag => new Element(tag), createTextNode: text => ({textContent: text, children: []})};
  const storage = new Map(pending ? [['planner-update-link-v1', JSON.stringify(pending)]] : []);
  let currentURL = new URL(path, base);
  const navigations = [], replacements = [], requests = [];
  const location = {get pathname() {return currentURL.pathname;}, get search() {return currentURL.search;}, get hash() {return currentURL.hash;}, assign(url) {navigations.push(url);}};
  const history = {replaceState(_state, _unused, path) {currentURL = new URL(path, currentURL); replacements.push(currentURL.href);}};
  const fetch = async (url, options) => {
    const request = {path: url, method: options.method, headers: options.headers, credentials: options.credentials,
      body: options.body === undefined ? undefined : JSON.parse(options.body)};
    requests.push(request);
    const result = await route(request, requests);
    assert.ok(result, `Unhandled request ${request.method} ${url}`);
    return Response.json(result.body, {status: result.status ?? 200});
  };
  await script.runInNewContext({document, location, history, fetch, URL, URLSearchParams, TextDecoder, Uint8Array, atob,
    sessionStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value)},
    navigator: {clipboard: {writeText: async () => {}}}});
  return {el, requests, navigations, replacements, location, storage};
}

test('legacy service without updates remains connected and its daily-sync toggle still works', async () => {
  let enabled = true;
  const page = await browser({route: async request => {
    if (request.path === '/api/status') return {body: status({enabled})};
    if (request.path === '/api/updates') return {status: 404, body: {error: 'Not found'}};
    if (request.path === '/api/enabled') {
      assert.equal(request.method, 'POST');
      assert.equal(request.headers['X-CSRF-Token'], 'session-csrf');
      assert.deepEqual(request.body, {enabled: false});
      enabled = request.body.enabled;
      return {body: {enabled}};
    }
  }});
  assert.equal(page.el('badge').textContent, '연결됨');
  assert.equal(page.el('connect').hidden, true);
  assert.equal(page.el('toggle').hidden, false);
  assert.equal(page.el('notion').hidden, false);
  assert.match(page.el('update-message').textContent, /업데이트 센터를 지원하지 않습니다/);
  await page.el('toggle').click();
  assert.equal(enabled, false);
  assert.equal(page.el('badge').textContent, '중지됨');
  assert.equal(page.el('toggle').disabled, false);
  assert.equal(page.el('connect').hidden, true);
});

test('expired Notion credentials during an update expose a usable reconnect link while the browser session stays connected', async () => {
  const reconnectURL = base + '/connect?reauthorize=1#' + encode(manifest);
  const page = await browser({route: async request => {
    if (request.path === '/api/status') return {body: status({reconnect_url: reconnectURL})};
    if (request.path === '/api/updates') return {body: {releases: [release({registered: true})]}};
    if (request.path === '/api/updates/apply') {
      assert.deepEqual(request.body, {update_id: 'student-history-v1'});
      assert.equal(request.headers['X-CSRF-Token'], 'session-csrf');
      return {status: 401, body: {error: 'Notion 권한을 다시 연결하세요.'}};
    }
  }});
  assert.equal(page.el('reconnect').hidden, true);
  const button = action(page, '적용 상태 다시 확인');
  await button.click();
  assert.equal(page.el('reconnect').hidden, false);
  assert.equal(page.el('reconnect').href, reconnectURL);
  assert.match(page.el('update-message').textContent, /다시 연결/);
  assert.equal(button.disabled, false);
  assert.equal(page.el('toggle').hidden, false);
  assert.equal(page.el('connect').hidden, true);
});

test('explicit reauthorization keeps the connect action after history removes its query flag', async () => {
  const authURL = 'https://api.notion.com/v1/oauth/authorize?client_id=example&response_type=code';
  const page = await browser({path: '/connect?reauthorize=1#' + encode(manifest), route: async request => {
    if (request.path === '/api/status') return {body: status()};
    if (request.path === '/api/start') {
      assert.deepEqual(request.body, manifest);
      return {body: {authorize_url: authURL}};
    }
    assert.fail('Reauthorization must not enter the already-connected updates branch');
  }});
  assert.deepEqual(page.replacements, [base + '/connect']);
  assert.equal(page.location.search, '');
  assert.equal(page.location.hash, '');
  assert.equal(page.el('connect').hidden, false);
  assert.equal(page.el('toggle').hidden, true);
  assert.deepEqual(page.requests.map(request => request.path), ['/api/status']);
  await page.el('connect').click();
  assert.deepEqual(page.navigations, [authURL]);
});

test('a bundle for the authenticated root enables application with precisely its registered targets', async () => {
  const page = await browser({path: '/connect#' + encode({connection: manifest, updates: targets}), route: async request => {
    if (request.path === '/api/status') return {body: status()};
    if (request.path === '/api/updates') return {body: {releases: [release()]}};
    if (request.path === '/api/updates/apply') {
      assert.deepEqual(request.body, {update_id: 'student-history-v1', targets});
      assert.equal(request.headers['X-CSRF-Token'], 'session-csrf');
      return {body: {status: 'schema_applied'}};
    }
  }});
  assert.equal(page.el('connect').hidden, true);
  const button = action(page, '이 업데이트 적용');
  assert.equal(button.disabled, false);
  await button.click();
  assert.equal(page.requests.filter(request => request.path === '/api/updates/apply').length, 1);
  assert.ok(page.requests.every(request => request.credentials === 'same-origin'));
});

test('stored targets for another root never enable application to the current browser session', async () => {
  const page = await browser({pending: {manifest, updateTargets: targets}, route: async request => {
    if (request.path === '/api/status') return {body: status({notion_url: 'https://notion.so/' + rootB.replaceAll('-', '')})};
    if (request.path === '/api/updates') return {body: {releases: [release()]}};
    assert.fail('Targets from the other planner must not be submitted');
  }});
  assert.equal(page.el('toggle').hidden, false);
  const button = action(page, '이 업데이트 적용');
  assert.equal(button.disabled, true);
  await button.click();
  assert.equal(page.requests.filter(request => request.method === 'POST').length, 0);
});

test('a new plain connection link cannot inherit another root’s stored update targets', async () => {
  const other = {...manifest, root_page_id: rootB};
  const page = await browser({path: '/connect#' + encode(other), pending: {manifest, updateTargets: targets}, route: async request => {
    if (request.path === '/api/status') return {body: status({notion_url: 'https://notion.so/' + rootB})};
    if (request.path === '/api/updates') return {body: {releases: [release()]}};
    assert.fail('No update mutation is authorized by this link');
  }});
  assert.equal(page.el('connect').hidden, true);
  assert.equal(action(page, '이 업데이트 적용').disabled, true);
  const saved = JSON.parse(page.storage.get('planner-update-link-v1'));
  assert.equal(saved.manifest.root_page_id, rootB);
  assert.equal(saved.updateTargets, null);
});

test('a bundle for another planner stays at the explicit connection step', async () => {
  const page = await browser({path: '/connect#' + encode({connection: manifest, updates: targets}), route: async request => {
    if (request.path === '/api/status') return {body: status({notion_url: 'https://notion.so/' + rootB})};
    assert.fail('Another planner’s session cannot load the bundle’s update actions');
  }});
  assert.equal(page.el('connect').hidden, false);
  assert.equal(page.el('toggle').hidden, true);
  assert.equal(page.el('releases').children.length, 0);
  assert.deepEqual(page.requests.map(request => request.path), ['/api/status']);
});
