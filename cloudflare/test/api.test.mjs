import test from 'node:test';
import assert from 'node:assert/strict';
import {createAPI} from '../src/api.mjs';
import {TokenBox,base64url,exchange,origin,validateManifest,ConnectionError,RetryableConnectionError} from '../src/security.mjs';
import {SyncError} from '../src/sync.mjs';
import {UPDATE_CATALOG,validateUpdateTargets} from '../src/updates.mjs';

const key = base64url(new Uint8Array(32).fill(19));
const base = 'https://planner.example.com';
const ids = [1,2,3,4].map(n=>`11111111-1111-4111-8111-${String(n).padStart(12,'0')}`);
const manifest = {version:1,office_code:'X10',school_code:'1234567',school_name:'가상고등학교',academic_year:2026,
  root_page_id:ids[0],agenda_data_source_id:ids[1],meals_block_id:ids[2],status_block_id:ids[3]};
const cookieOnly = response => response.headers.get('Set-Cookie').split(';')[0];
function fixture(overrides={}) {
  let time = 1790000000000, doc, written = 0, validated = 0, updates=[], locked=false;
  const now = () => time;
  const store = {
    async register(m,identity,credentials) {written++;doc={manifest:m,...identity,credentials,enabled:true,status:'waiting'};return 'installation';},
    async get(id) {return id==='installation'? doc:null;},
    async setEnabled(id,owner,enabled) {assert.equal(id,'installation');assert.equal(owner,'teacher');doc.enabled=enabled;doc.status=enabled?'waiting':'paused';},
    async getUpdates(id) {assert.equal(id,'installation');return structuredClone(updates);},
    async claimUpdate(id,owner) {assert.equal(id,'installation');assert.equal(owner,doc.owner_id);assert.equal(locked,false);locked=true;return {lease:{id,owner},doc};},
    async assertUpdateLease() {assert.ok(locked);},
    async saveUpdate(lease,id,targets,journal,status) {assert.ok(locked);updates=[{update_id:id,targets:structuredClone(targets),journal:structuredClone(journal),status,updated_at:time}];},
    async releaseUpdate() {locked=false;}
  };
  const api = createAPI({store,publicUrl:base,clientId:'client',clientSecret:'secret',encryptionKey:key,now,
    clientFactory:token=>({token}),targetValidator:async (client,m)=>{assert.equal(client.token,'access');assert.deepEqual(m,manifest);validated++;},
    exchangeFn:async ()=>({access_token:'access',refresh_token:'refresh',owner:{user:{id:'teacher'}},workspace_id:'workspace',bot_id:'bot'}),...overrides});
  const request = (path,{data,cookie,csrf,origin:requestOrigin=base,method}={}) => new Request(base+path,{method:method||(data?'POST':'GET'),headers:{'Origin':requestOrigin,'Content-Type':'application/json',...(cookie?{Cookie:cookie}:{}),...(csrf?{'X-CSRF-Token':csrf}:{})},...(data?{body:JSON.stringify(data)}:{})});
  async function start() {
    const response = await api(request('/api/start',{data:manifest}));assert.equal(response.status,200);
    const auth = new URL((await response.json()).authorize_url);
    assert.equal(auth.origin,'https://api.notion.com');assert.equal(auth.searchParams.get('redirect_uri'),base+'/api/callback');
    return {cookie:cookieOnly(response),state:auth.searchParams.get('state'),response};
  }
  async function complete(started) {return api(request('/api/callback?'+new URLSearchParams({code:'code',state:started.state}),{cookie:started.cookie}));}
  return {api,request,start,complete,get doc(){return doc;},get written(){return written;},get validated(){return validated;},get updates(){return updates;},get locked(){return locked;},advance:ms=>time+=ms};
}

const updateTargets={version:1,students_data_source_id:'11111111-1111-4111-8111-000000000005',
  counseling_data_source_id:'11111111-1111-4111-8111-000000000006',student_relation_property_id:'studentProp'};
function updateFixture(overrides={}) {
  let applications=0,checks=0;
  const engine={catalog:UPDATE_CATALOG,validate:validateUpdateTargets,
    inspect:async()=>{checks++;return {status:'schema_applied',reverse_property_id:'reverse'};},
    apply:async(_n,_m,_t,journal,save)=>{applications++;const result={...journal,stage:'schema_applied',reason:'schema_verified',reverse_property_id:'reverse'};await save(result);return result;},...overrides};
  const f=fixture({updateEngine:engine});
  return {...f,get doc(){return f.doc;},get updates(){return f.updates;},get locked(){return f.locked;},
    get applications(){return applications;},get checks(){return checks;}};
}
async function updateSession(f) {const cookie=cookieOnly(await f.complete(await f.start()));const status=await (await f.api(f.request('/api/status',{cookie}))).json();return {cookie,csrf:status.csrf};}
test('updates require session, ownership, CSRF and exact target input before any Notion action',async()=>{
  const f=updateFixture(),auth=await updateSession(f),data={update_id:'student-history-v1',targets:updateTargets};
  for(const opts of [{data},{...auth,data,csrf:'bad'},{...auth,data,origin:'https://evil.example'},{...auth,data:{...data,secret:'do-not-save'}},
    {...auth,data:{...data,update_id:'arbitrary'}},{...auth,data:{...data,targets:{...updateTargets,token:'private'}}}]) {
    assert.ok((await f.api(f.request('/api/updates/apply',opts))).status>=400);
    assert.equal(f.applications,0);assert.equal(f.locked,false);
  }
  f.doc.owner_id='different';assert.equal((await f.api(f.request('/api/updates',{...auth}))).status,401);
});
test('selective apply leaves layout pending, confirmation rechecks schema, repeat keeps completion',async()=>{
  const f=updateFixture(),auth=await updateSession(f),id='student-history-v1',before=structuredClone(f.doc);
  let view=await (await f.api(f.request('/api/updates',auth))).json();assert.equal(view.releases[0].status,'available');
  const early=await f.api(f.request('/api/updates/confirm-layout',{...auth,data:{update_id:id,layout_confirmed:true}}));assert.equal(early.status,409);
  const result=await f.api(f.request('/api/updates/apply',{...auth,data:{update_id:id,targets:updateTargets}}));
  assert.equal(result.status,200);assert.equal((await result.json()).status,'schema_applied');assert.deepEqual(f.doc,before);
  const confirm=await f.api(f.request('/api/updates/confirm-layout',{...auth,data:{update_id:id,layout_confirmed:true}}));
  assert.equal(confirm.status,200);assert.equal(f.checks,1);assert.equal(f.updates[0].journal.layout_confirmation.method,'teacher_confirmed');
  const recheck=await f.api(f.request('/api/updates/apply',{...auth,data:{update_id:id}}));assert.equal((await recheck.json()).status,'complete');
  view=await (await f.api(f.request('/api/updates',auth))).json();assert.equal(view.releases[0].status,'complete');
  assert.equal(view.releases[0].targets,undefined);assert.doesNotMatch(JSON.stringify(view),/studentProp|access_token|credentials/);
  assert.equal(f.locked,false);
});
test('registered update targets cannot be silently rebound and uncertain writes remain resumable',async()=>{
  const f=updateFixture({apply:async(_n,_m,_t,_j,save)=>{await save({stage:'pending',reason:'write_unconfirmed'});throw new Error('private credentials');}}),auth=await updateSession(f);
  const id='student-history-v1';const failed=await f.api(f.request('/api/updates/apply',{...auth,data:{update_id:id,targets:updateTargets}}));
  assert.doesNotMatch(await failed.text(),/private credentials/);assert.equal(f.updates[0].status,'pending');assert.equal(f.locked,false);
  const changed=await f.api(f.request('/api/updates/apply',{...auth,data:{update_id:id,targets:{...updateTargets,student_relation_property_id:'another'}}}));
  assert.equal(changed.status,409);assert.equal(f.updates[0].targets.student_relation_property_id,'studentProp');
});
test('an expired Notion token requests reconnection without rotating or mutating the update',async()=>{
  const f=updateFixture(),auth=await updateSession(f),box=new TokenBox(key,()=>1790000000000);
  f.doc.credentials=await box.seal('notion',{access_token:'access',refresh_token:'refresh',expires_at:1790000000000});
  const result=await f.api(f.request('/api/updates/apply',{...auth,data:{update_id:'student-history-v1',targets:updateTargets}}));
  assert.equal(result.status,401);assert.equal(f.applications,0);assert.equal(f.locked,false);assert.equal(f.updates.length,0);
});
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
test('callback failures expose only fixed diagnostic phase and code at each boundary',async()=>{
  const privateText='provider secret: ntn_private-token; code=private-auth-code; owner=private-user-id';
  const cases=[
    {phase:'session',error_code:'invalid_session',status:400,session:true},
    {phase:'exchange',error_code:'token_exchange',status:503,overrides:{exchangeFn:async()=>{const error=new RetryableConnectionError();error.message=privateText;throw error;}}},
    {phase:'identity',error_code:'identity_missing',status:400,overrides:{exchangeFn:async()=>({access_token:privateText,refresh_token:privateText,owner:{user:{}},workspace_id:privateText,bot_id:privateText})}},
    {phase:'targets',error_code:'target_validation',status:403,overrides:{targetValidator:async()=>{throw new SyncError(privateText,{status:403});}}},
    {phase:'register',error_code:'registration_failed',status:409,overrides:{store:{register:async()=>{throw new ConnectionError(privateText,409);}}}},
  ];
  for(const item of cases) {
    const f=fixture(item.overrides),started=await f.start();
    const response=await f.complete(item.session?{...started,cookie:'__Host-planner=private-invalid-cookie'}:started);
    assert.equal(response.status,item.status,item.phase);
    assert.equal(response.headers.get('Cache-Control'),'private, no-store');
    assert.equal(response.headers.get('Set-Cookie'),null);
    const result=await response.json();
    assert.deepEqual(Object.keys(result).sort(),['error','error_code','phase']);
    assert.equal(result.phase,item.phase);assert.equal(result.error_code,item.error_code);
    assert.doesNotMatch(JSON.stringify(result),/ntn_private|private-auth|private-user|private-invalid|provider secret/);
    assert.equal(f.written,0);
  }
});
test('only the exact local callout structure error gets a safe actionable diagnostic',async()=>{
  const message='급식·상태 전용 콜아웃 구조를 확인하세요.';
  for(const [error,expected] of [
    [new SyncError(message),'callout_structure'],
    [new SyncError(message+' private-target-id'),'target_validation'],
    [new Error(message),'target_validation'],
  ]) {
    const f=fixture({targetValidator:async()=>{throw error;}});
    const response=await f.complete(await f.start());assert.equal(response.status,400);
    const result=await response.json();assert.equal(result.phase,'targets');assert.equal(result.error_code,expected);
    if(expected==='callout_structure') assert.equal(result.error,'급식·상태 영역은 본문만 있는 콜아웃 또는 빈 콜아웃 안에 문단 하나만 있는 형태로 준비한 뒤 다시 연결하세요.');
    else assert.doesNotMatch(result.error,/콜아웃|private-target/);
    assert.equal(f.written,0);
  }
});
test('expired and cross-session callbacks stop before token exchange without diagnostics leaking to other routes',async()=>{
  let exchanges=0;
  const f=fixture({exchangeFn:async()=>{exchanges++;throw new Error('must not exchange');}});
  const a=await f.start(),b=await f.start();
  assert.deepEqual((await (await f.complete({state:a.state,cookie:b.cookie})).json()).phase,'session');
  f.advance(601000);
  const expired=await (await f.complete(a)).json();assert.equal(expired.error_code,'invalid_session');assert.equal(exchanges,0);
  const other=await (await f.api(f.request('/api/status'))).json();
  assert.deepEqual(Object.keys(other),['error']);
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
  assert.equal(options.redirect,'manual');
  assert.equal(options.headers['Notion-Version'],'2026-03-11');
});
test('code exchange accepts nullable or omitted refresh tokens without weakening access or refresh-grant validation',async()=>{
  for(const refresh of [{},{refresh_token:null},{refresh_token:'refresh'}]) {
    const tokens={access_token:'access',...refresh};
    assert.deepEqual(await exchange('client','secret',{grant_type:'authorization_code'},async()=>Response.json(tokens)),tokens);
    const f=fixture({exchangeFn:()=>exchange('client','secret',{grant_type:'authorization_code'},async()=>Response.json({...tokens,owner:{user:{id:'teacher'}},workspace_id:'workspace',bot_id:'bot'}))});
    assert.equal((await f.complete(await f.start())).status,303);assert.equal(f.written,1);
  }
  for(const access of [undefined,null,'',123,'a'.repeat(4097)]) {
    await assert.rejects(()=>exchange('client','secret',{grant_type:'authorization_code'},async()=>Response.json({access_token:access,refresh_token:null})),error=>error.exchangeCode==='invalid_access_token');
  }
  for(const refresh of [undefined,null,'',123,'r'.repeat(4097)]) {
    await assert.rejects(()=>exchange('client','secret',{grant_type:'refresh_token'},async()=>Response.json({access_token:'access',refresh_token:refresh})),error=>error.exchangeCode==='invalid_refresh_token'&&error.code==='reconnect');
  }
  await assert.rejects(()=>exchange('client','secret',{grant_type:'authorization_code'},async()=>Response.json({access_token:'access',refresh_token:''})),error=>error.exchangeCode==='invalid_refresh_token');
});
test('callback token diagnostics are bounded allowlisted classifications, never provider bodies or descriptions',async()=>{
  const privateText='private-token private-code private-owner';
  const cases=[
    ['transport',()=>{throw new Error(privateText);}],
    ['redirect',()=>new Response(privateText,{status:302,headers:{Location:'https://private-target.example/private-code'}})],
    ['invalid_client',()=>Response.json({error:'invalid_client',error_description:privateText},{status:401})],
    ['invalid_grant',()=>Response.json({code:'invalid_grant',message:privateText},{status:400})],
    ['missing_version',()=>Response.json({code:'missing_version',message:privateText},{status:400})],
    ['http_error',()=>Response.json({error:privateText,message:privateText},{status:400})],
    ['http_error',()=>new Response(privateText.repeat(1000),{status:400})],
    ['invalid_response',()=>new Response(privateText)],
    ['invalid_access_token',()=>Response.json({refresh_token:privateText})],
    ['invalid_refresh_token',()=>Response.json({access_token:privateText,refresh_token:123})],
    ['rate_limited',()=>new Response(privateText,{status:429})],
    ['server_error',()=>new Response(privateText,{status:503})],
  ];
  for(const [code,fetcher] of cases) {
    let calls=0;
    const f=fixture({exchangeFn:(client,secret,body)=>exchange(client,secret,body,async()=>{calls++;return fetcher();})});
    const response=await f.complete(await f.start()),result=await response.json();
    assert.equal(result.phase,'exchange');assert.equal(result.error_code,'token_exchange_'+code);
    assert.equal(response.status,['rate_limited','server_error'].includes(code)?503:400);
    assert.doesNotMatch(JSON.stringify(result),/private-token|private-code|private-owner|error_description|message/);
    assert.equal(calls,1);assert.equal(f.written,0);assert.equal(f.validated,0);
  }
  for(const error of [Object.assign(new Error(privateText),{exchangeCode:'invalid_client'}),Object.assign(new ConnectionError(privateText),{exchangeCode:privateText})]) {
    const f=fixture({exchangeFn:async()=>{throw error;}}),result=await (await f.complete(await f.start())).json();
    assert.equal(result.error_code,'token_exchange');assert.doesNotMatch(JSON.stringify(result),/private-token/);
  }
});
test('service origin disallows credentials, paths, local/IP destinations and query strings',()=>{
  assert.equal(origin(base),base);
  for(const bad of ['http://example.com','https://a:b@example.com','https://example.com/path','https://localhost','https://127.0.0.1','https://example.com?key=x','https://example.com:443']) assert.throws(()=>origin(bad));
});

test('layout confirmation rejects a replaced reciprocal even if current schema is valid',async()=>{
  const f=updateFixture({inspect:async()=>({status:'schema_applied',reverse_property_id:'replaced'})}),auth=await updateSession(f);
  await f.api(f.request('/api/updates/apply',{...auth,data:{update_id:'student-history-v1',targets:updateTargets}}));
  const response=await f.api(f.request('/api/updates/confirm-layout',{...auth,data:{update_id:'student-history-v1',layout_confirmed:true}}));
  assert.equal(response.status,409);assert.equal(f.updates[0].status,'schema_applied');assert.equal(f.updates[0].journal.layout_confirmation,undefined);
});
test('recheck interruption after verified checkpoint retains prior layout confirmation',async()=>{
  let interrupt=false;
  const f=updateFixture({apply:async(_n,_m,_t,journal,save)=>{
    const next={...journal,stage:'schema_applied',reverse_property_id:'reverse',reason:'schema_verified'};
    await save(next);if(interrupt)throw new Error('lost response');return next;
  }}),auth=await updateSession(f),id='student-history-v1';
  await f.api(f.request('/api/updates/apply',{...auth,data:{update_id:id,targets:updateTargets}}));
  await f.api(f.request('/api/updates/confirm-layout',{...auth,data:{update_id:id,layout_confirmed:true}}));
  const confirmation=structuredClone(f.updates[0].journal.layout_confirmation);interrupt=true;
  const failed=await f.api(f.request('/api/updates/apply',{...auth,data:{update_id:id}}));assert.ok(failed.status>=400);
  assert.equal(f.updates[0].status,'complete');assert.deepEqual(f.updates[0].journal.layout_confirmation,confirmation);assert.equal(f.locked,false);
});
