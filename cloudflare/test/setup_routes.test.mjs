import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import worker from '../src/index.mjs';

const base = 'https://planner.example.com';

test('setup routes serve the public questionnaire without a database, session or OAuth credentials', async () => {
  for (const path of ['/setup', '/setup/', '/setup?from=start']) {
    const requests = [];
    const response = await worker.fetch(new Request(base + path, {headers: {'Accept': 'text/html'}}), {
      get DB() {assert.fail('The setup questionnaire must not access connection storage');},
      ASSETS: {async fetch(request) {
        requests.push(request);
        return new Response('<title>교무수첩 시작하기</title>', {headers: {'Content-Type': 'text/html'}});
      }}
    });
    assert.equal(response.status, 200);
    assert.equal(requests.length, 1);
    const assetURL = new URL(requests[0].url);
    assert.equal(assetURL.pathname, '/setup.html');
    assert.equal(assetURL.search, new URL(base + path).search);
    assert.equal(requests[0].headers.get('Accept'), 'text/html');
    assert.match(await response.text(), /교무수첩 시작하기/);
    assert.match(response.headers.get('Content-Security-Policy'), /script-src 'self'/);
    assert.equal(response.headers.get('Referrer-Policy'), 'no-referrer');
    assert.equal(response.headers.get('X-Content-Type-Options'), 'nosniff');
    assert.equal(response.headers.get('Set-Cookie'), null);
  }
});

test('setup routing preserves HEAD and leaves the existing root, connection and other static paths alone', async () => {
  for (const [path, expected] of [
    ['/setup/', '/setup.html'], ['/', '/'], ['/connect?reauthorize=1', '/index.html?reauthorize=1'],
    ['/index.html', '/index.html'], ['/setup-model.mjs', '/setup-model.mjs'], ['/setup/example', '/setup/example']
  ]) {
    let assetRequest;
    const response = await worker.fetch(new Request(base + path, {method: 'HEAD'}), {
      ASSETS: {async fetch(request) {assetRequest = request; return new Response(null, {status: 200});}}
    });
    assert.equal(response.status, 200);
    assert.equal(assetRequest.url, base + expected);
    assert.equal(assetRequest.method, 'HEAD');
    assert.equal(await response.text(), '');
  }
});

test('existing API routes still use the API session checks instead of the setup assets', async () => {
  let assetCalls = 0;
  const response = await worker.fetch(new Request(base + '/api/status'), {
    DB: {prepare() {assert.fail('An unauthenticated request must not query installations');}},
    PUBLIC_BASE_URL: base,
    NOTION_CLIENT_ID: 'test-client',
    NOTION_CLIENT_SECRET: 'test-secret',
    TOKEN_ENCRYPTION_KEY: Buffer.alloc(32, 19).toString('base64url'),
    ASSETS: {async fetch() {assetCalls++; return new Response('static');}}
  });
  assert.equal(response.status, 400);
  assert.equal(assetCalls, 0);
  assert.match(response.headers.get('Content-Type'), /application\/json/);
  assert.match(response.headers.get('Cache-Control'), /no-store/);
});

test('public guide routes and PDF work without a connection or installation session', async () => {
  for (const [path, expected] of [
    ['/guide', '/guide.html'], ['/guide/', '/guide.html'],
    ['/guide?from=community', '/guide.html?from=community'],
    ['/teacher-planner-guide.pdf', '/teacher-planner-guide.pdf']
  ]) {
    let requested;
    const response = await worker.fetch(new Request(base + path), {
      get DB() {assert.fail('Reading the guide must not access installation storage');},
      ASSETS: {async fetch(request) {requested = request.url; return new Response('guide');}}
    });
    assert.equal(requested, base + expected);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('Set-Cookie'), null);
  }
});

test('Firebase setup rewrites precede the connection fallback while keeping the API function route first', async () => {
  const {hosting} = JSON.parse(await readFile(new URL('../../firebase.json', import.meta.url), 'utf8'));
  const resolve = path => hosting.rewrites.find(rule => rule.source === path || rule.source === '**' ||
    (rule.source === '/api/**' && path.startsWith('/api/')));
  assert.equal(hosting.public, 'cloud/web');
  assert.equal(resolve('/setup').destination, '/setup.html');
  assert.equal(resolve('/setup/').destination, '/setup.html');
  assert.equal(resolve('/guide').destination, '/guide.html');
  assert.equal(resolve('/guide/').destination, '/guide.html');
  assert.equal(resolve('/connect').destination, '/index.html');
  assert.equal(resolve('/').destination, '/index.html');
  assert.equal(resolve('/api/status').function.functionId, 'planner_api');
});
