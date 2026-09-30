import {createAPI} from './api.mjs';
import {TokenBox,exchange} from './security.mjs';
import {createStore} from './store.mjs';
import {runCron} from './runtime.mjs';
import {createNotionClient,validateTargets} from './sync.mjs';

const secureHeaders = {
  'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
  'Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'
};
export default {
  async fetch(request,env) {
    const url = new URL(request.url);
    let response;
    try {
      if (url.pathname.startsWith('/api/')) {
        const store = createStore(env.DB,{maxInstallations:Number(env.MAX_INSTALLATIONS || 50),maxDailySteps:Number(env.MAX_DAILY_STEPS || 1000)});
        response = await createAPI({store,publicUrl:env.PUBLIC_BASE_URL,clientId:env.NOTION_CLIENT_ID,
          clientSecret:env.NOTION_CLIENT_SECRET,encryptionKey:env.TOKEN_ENCRYPTION_KEY,
          clientFactory:createNotionClient,targetValidator:validateTargets})(request);
      } else {
        if (url.pathname === '/connect') url.pathname = '/index.html';
        response = await env.ASSETS.fetch(new Request(url,request));
      }
    } catch {
      response = new Response(JSON.stringify({error:'연결 설정을 준비 중입니다. 잠시 후 확인해 주세요.'}),{status:503,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
    }
    response = new Response(response.body,response);
    for (const [key,value] of Object.entries(secureHeaders)) response.headers.set(key,value);
    return response;
  },
  async scheduled(event,env,ctx) {
    // Only Cloudflare's internal scheduled handler can start sync work.
    ctx.waitUntil((async () => {
      const box = new TokenBox(env.TOKEN_ENCRYPTION_KEY);
      return runCron(env,{
        encrypt:payload=>box.seal('notion',payload),decrypt:cipher=>box.open('notion',cipher),
        exchangeTokens:refresh_token=>exchange(env.NOTION_CLIENT_ID,env.NOTION_CLIENT_SECRET,{grant_type:'refresh_token',refresh_token})
      });
    })());
  }
};
