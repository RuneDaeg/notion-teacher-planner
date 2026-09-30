import {TokenBox, ConnectionError, exchange, nonce, origin, readJSON, validateManifest} from './security.mjs';

const COOKIE = '__Host-planner';
const NO_CACHE = {'Cache-Control':'private, no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'};
const json = (data, status = 200, headers = {}) => new Response(JSON.stringify(data), {status, headers:{'Content-Type':'application/json; charset=utf-8',...NO_CACHE,...headers}});
const cookie = (value, age) => `${COOKIE}=${value}; Path=/; Max-Age=${age}; HttpOnly; Secure; SameSite=Lax`;
function readCookie(request) {
  const candidates = (request.headers.get('Cookie') || '').split(';').map(x=>x.trim()).filter(x=>x.startsWith(COOKIE+'='));
  if (candidates.length !== 1) throw new ConnectionError('session');
  return candidates[0].slice(COOKIE.length+1);
}
const redirect = (path, session, age = 3600) => new Response(null, {status:303, headers:{...NO_CACHE,'Location':path,'Set-Cookie':cookie(session,age)}});

export function createAPI({store,publicUrl,clientId,clientSecret,encryptionKey,clientFactory,targetValidator,
    exchangeFn=exchange,now=()=>Date.now()}) {
  const base = origin(publicUrl);
  const box = new TokenBox(encryptionKey,now);
  const callback = base+'/api/callback';
  function sameOrigin(request) {if (request.headers.get('Origin') !== base) throw new ConnectionError('origin',403);}
  async function body(request) {
    if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get('Content-Type') || '')) throw new ConnectionError();
    return await readJSON(request,12000);
  }
  async function session(request) {
    const current = await box.open('browser',readCookie(request),3600);
    const doc = await store.get(current.installation_id);
    if (!doc || doc.owner_id !== current.owner_id || typeof current.csrf !== 'string') throw new ConnectionError('session',401);
    return {current,doc};
  }
  return async function handle(request) {
    try {
      const url = new URL(request.url);
      if (url.origin !== base) throw new ConnectionError('origin',403);
      if (url.pathname === '/api/start' && request.method === 'POST') {
        sameOrigin(request);
        const manifest = validateManifest(await body(request));
        const csrf = nonce();
        const state = await box.seal('oauth',{manifest,nonce:csrf});
        const authorize = new URL('https://api.notion.com/v1/oauth/authorize');
        authorize.search = new URLSearchParams({client_id:clientId,response_type:'code',owner:'user',redirect_uri:callback,state}).toString();
        return json({authorize_url:authorize.href},200,{'Set-Cookie':cookie(await box.seal('pending',{nonce:csrf}),600)});
      }
      if (url.pathname === '/api/callback' && request.method === 'GET') {
        const pending = await box.open('pending',readCookie(request),600);
        const state = await box.open('oauth',url.searchParams.get('state'),600);
        if (!pending.nonce || pending.nonce !== state.nonce) throw new ConnectionError();
        if (url.searchParams.has('error')) return redirect('/connect?result=cancelled','',0);
        const code = url.searchParams.get('code');
        if (!code || code.length > 4096) throw new ConnectionError();
        const manifest = validateManifest(state.manifest);
        const tokens = await exchangeFn(clientId,clientSecret,{grant_type:'authorization_code',code,redirect_uri:callback});
        const owner = tokens.owner?.user?.id;
        if (!owner || !tokens.workspace_id || !tokens.bot_id || tokens.duplicated_template_id) throw new ConnectionError();
        await targetValidator(clientFactory(tokens.access_token),manifest);
        const credentials = {access_token:tokens.access_token,refresh_token:tokens.refresh_token};
        if (Number.isFinite(tokens.expires_in) && tokens.expires_in > 0) credentials.expires_at = now()+tokens.expires_in*1000;
        const id = await store.register(manifest,{owner_id:owner,workspace_id:tokens.workspace_id,bot_id:tokens.bot_id},await box.seal('notion',credentials));
        // Registration schedules the durable next_due value. No public worker trigger exists.
        return redirect('/connect?result=connected',await box.seal('browser',{installation_id:id,owner_id:owner,csrf:nonce()}));
      }
      if (url.pathname === '/api/status' && request.method === 'GET') {
        const {current,doc} = await session(request);
        return json({school_name:doc.manifest.school_name,academic_year:doc.manifest.academic_year,
          enabled:doc.enabled,status:doc.status,last_success_at:doc.last_success_at ? doc.last_success_at/1000 : null,
          schedule:'매일 오전 7시부터 순차 갱신 · 한국 시간',csrf:current.csrf,
          notion_url:'https://www.notion.so/'+doc.manifest.root_page_id});
      }
      if (url.pathname === '/api/enabled' && request.method === 'POST') {
        sameOrigin(request);
        const {current} = await session(request);
        if (request.headers.get('X-CSRF-Token') !== current.csrf) throw new ConnectionError('csrf',403);
        const data = await body(request);
        if (!data || typeof data.enabled !== 'boolean' || Object.keys(data).length !== 1) throw new ConnectionError();
        await store.setEnabled(current.installation_id,current.owner_id,data.enabled);
        return json({ok:true});
      }
      return json({error:'요청 경로를 확인하세요.'},404);
    } catch (error) {
      // Error bodies and provider messages can contain authorization codes and private IDs.
      const status = [400,401,403,409,429,503].includes(error.status) ? error.status : 400;
      const message = status === 409 ? '갱신 중이거나 등록 가능한 수첩 수를 넘었습니다. 잠시 후 다시 확인해 주세요.'
        : status === 503 || status === 429 ? '연결 서버가 잠시 응답하지 않습니다. 수첩의 연결 링크에서 다시 시도해 주세요.'
        : '연결을 확인하지 못했습니다. 설치 정보와 Notion 접근 허용을 확인하고 다시 시도하세요.';
      return json({error:message},status);
    }
  };
}
