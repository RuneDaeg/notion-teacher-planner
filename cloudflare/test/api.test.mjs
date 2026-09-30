import test from 'node:test';
import assert from 'node:assert/strict';
import {createAPI} from '../src/api.mjs';
import {TokenBox,base64url,exchange,origin,validateManifest,ConnectionError,RetryableConnectionError} from '../src/security.mjs';

const key = base64url(new Uint8Array(32).fill(19));
const base = 'https://planner.example.com';
const ids = [1,2,3,4].map(n=>`11111111-1111-4111-8111-${String(n).padStart(12,'0')}`);
const manifest = {version:1,office_code:'X10',school_code:'1234567',school_name:'가상고등학교',academic_year:2026,
  root_page_id:ids[0],agenda_data_source_id:ids[1],meals_block_id:ids[2],status_block_id:ids[3]};
const cookieOnly = response => response.headers.get('Set-Cookie').split(';')[0];
function fixture() {
  let time = 1790000000000, doc, written = 0, validated = 0;
  const now = () => time;
  const store = {
    async register(m,identity,credentials) {written++;doc={manifest:m,...identity,credentials,enabled:true,status:'waiting'};return 'installation';},
    async get(id) {return id==='installation'? doc:null;},
    async setEnabled(id,owner,enabled) {assert.equal(id,'installation');assert.equal(owner,'teacher');doc.enabled=enabled;doc.status=enabled?'waiting':'paused';}
  };
  const api = createAPI({store,publicUrl:base,clientId:'client',clientSecret:'secret',encryptionKey:key,now,
    clientFactory:token=>({token}),targetValidator:async (client,m)=>{assert.equal(client.token,'access');assert.deepEqual(m,manifest);validated++;},
    exchangeFn:async ()=>({access_token:'access',refresh_token:'refresh',owner:{user:{id:'teacher'}},workspace_id:'workspace',bot_id:'bot'})});
  const request = (path,{data,cookie,csrf,origin:requestOrigin=base,method}={}) => new Request(base+path,{method:method||(data?'POST':'GET'),headers:{'Origin':requestOrigin,'Content-Type':'application/json',...(cookie?{Cookie:cookie}:{}),...(csrf?{'X-CSRF-Token':csrf}:{})},...(data?{body:JSON.stringify(data)}:{})});
  async function start() {
    const response = await api(request('/api/start',{data:manifest}));assert.equal(response.status,200);
    const auth = new URL((await response.json()).authorize_url);
    assert.equal(auth.origin,'https://api.notion.com');assert.equal(auth.searchParams.get('redirect_uri'),base+'/api/callback');
    return {cookie:cookieOnly(response),state:auth.searchParams.get('state'),response};
  }
  async function complete(started) {return api(request('/api/callback?'+new URLSearchParams({code:'code',state:started.state}),{cookie:started.cookie}));}
  return {api,request,start,complete,get doc(){return doc;},get written(){return written;},get validated(){return validated;},advance:ms=>time+=ms};
}
test('enrollment validates selected targets, stores encrypted tokens, reports pending until actual success',async()=>{
  const f=fixture();const started=await f.start();assert.equal(f.written,0);
  assert.match(started.response.headers.get('Set-Cookie'),/HttpOnly; Secure; SameSite=Lax/);
  const response=await f.complete(started);assert.equal(response.status,303);assert.equal(f.validated,1);assert.equal(f.written,1);
  assert.ok(!f.doc.credentials.includes('access'));const box=new TokenBox(key,()=>1790000000000);
  assert.deepEqual(await box.open('notion',f.doc.credentials),{access_token:'access',refresh_token:'refresh'});
  const status=await f.api(f.request('/api/status',{cookie:cookieOnly(response)}));
  const view=await status.json();assert.equal(view.status,'waiting');assert.equal(view.last_success_at,null);
  assert.equal(view.enabled,true);assert.equal(view.access_token,undefined);assert.equal(view.credentials,undefined);
  const stopped=await f.api(f.request('/api/enabled',{cookie:cookieOnly(response),csrf:view.csrf,data:{enabled:false}}));
  assert.equal(stopped.status,200);assert.equal(f.doc.enabled,false);
});
test('CSRF, cross-origin start and cross-session OAuth state cannot register or pause',async()=>{
  const f=fixture();assert.equal((await f.api(f.request('/api/start',{data:manifest,origin:'https://evil.example'}))).status,403);
  const a=await f.start(),b=await f.start();assert.equal((await f.complete({state:a.state,cookie:b.cookie})).status,400);assert.equal(f.written,0);
  const connected=await f.complete(a);
  assert.equal((await f.api(f.request('/api/enabled',{cookie:cookieOnly(connected),data:{enabled:false}}))).status,403);assert.equal(f.doc.enabled,true);
});
test('expired OAuth state is rejected and cancellation clears pending cookie',async()=>{
  const f=fixture(),started=await f.start();f.advance(601000);assert.equal((await f.complete(started)).status,400);assert.equal(f.written,0);
  const fresh=await f.start();
  const cancel=await f.api(f.request('/api/callback?error=access_denied&state='+encodeURIComponent(fresh.state),{cookie:fresh.cookie}));assert.equal(cancel.headers.get('Location'),'/connect?result=cancelled');assert.match(cancel.headers.get('Set-Cookie'),/Max-Age=0/);
  const connected=await f.complete(await f.start());
  const unsolicited=await f.api(f.request('/api/callback?error=access_denied',{cookie:cookieOnly(connected)}));
  assert.equal(unsolicited.headers.get('Set-Cookie'),null);
});
test('public API has no endpoint to trigger a sync or bypass ownership',async()=>{
  const f=fixture();assert.equal((await f.api(f.request('/api/sync',{data:{}}))).status,404);
  assert.equal((await f.api(f.request('/api/status'))).status,400);
  const connected=await f.complete(await f.start());f.doc.owner_id='another';
  assert.equal((await f.api(f.request('/api/status',{cookie:cookieOnly(connected)}))).status,401);
});
test('manifest allowlist rejects secrets, duplicate targets and malformed school identifiers',()=>{
  assert.deepEqual(validateManifest(manifest),manifest);
  for(const bad of [{...manifest,api_key:'private'}, {...manifest,status_block_id:ids[0]}, {...manifest,school_code:1234567}, {...manifest,school_name:'ntn_example'}, {...manifest,academic_year:'2026'}, {...manifest,root_page_id:'https://notion.so/'+ids[0]}]) assert.throws(()=>validateManifest(bad));
});
test('AES-GCM purposes, tampering and TTL are enforced',async()=>{
  let time=100000;const box=new TokenBox(key,()=>time),sealed=await box.seal('pending',{nonce:'hello'});
  assert.deepEqual(await box.open('pending',sealed,10),{nonce:'hello'});
  await assert.rejects(()=>box.open('browser',sealed));
  await assert.rejects(()=>box.open('pending',sealed.slice(0,-5)+'aaaaa'));
  time+=11000;await assert.rejects(()=>box.open('pending',sealed,10));
  assert.throws(()=>new TokenBox('invalid'));
});
test('OAuth provider errors never expose tokens or retry an ambiguous refresh',async()=>{
  let calls=0;
  const fn=async()=>{calls++;throw new Error('secret response');};
  await assert.rejects(()=>exchange('client','secret',{grant_type:'refresh_token',refresh_token:'private'},fn),e=>e.code==='reconnect'&&!e.message.includes('secret'));
  assert.equal(calls,1);
  await assert.rejects(()=>exchange('client','secret',{},async()=>new Response('secret provider body',{status:503})),RetryableConnectionError);
  await assert.rejects(()=>exchange('client','secret',{},async()=>new Response('x'.repeat(66000))),ConnectionError);
  let options;
  await exchange('client','secret',{},async(url,opts)=>{assert.equal(url,'https://api.notion.com/v1/oauth/token');options=opts;return Response.json({access_token:'a',refresh_token:'r'});});
  assert.equal(options.redirect,'error');
});
test('service origin disallows credentials, paths, local/IP destinations and query strings',()=>{
  assert.equal(origin(base),base);
  for(const bad of ['http://example.com','https://a:b@example.com','https://example.com/path','https://localhost','https://127.0.0.1','https://example.com?key=x','https://example.com:443']) assert.throws(()=>origin(bad));
});
