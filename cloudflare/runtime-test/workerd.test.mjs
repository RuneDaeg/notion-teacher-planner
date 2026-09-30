import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

// Run with npm run test:runtime where local loopback listeners are permitted.
// Uses Wrangler's existing Miniflare dependency. No real tokens or external fetch.
test('actual workerd accepts every outbound request configuration and rejects redirects',async()=>{
    const {Miniflare,convertV4MiniflareOptions}=await import('miniflare');
    const source=`
      import {exchange,exchangeDiagnostic} from './security.mjs';
      import {createNotionClient,fetchSchoolSnapshot} from './sync.mjs';
      export default {async fetch(){
        const modes=[];
        const checked=(url,options)=>{
          const request=new Request(url,options);
          modes.push(request.redirect);
          if(!(options.signal instanceof AbortSignal))throw new Error('missing signal');
        };
        await exchange('test-client','test-secret',{grant_type:'authorization_code'},async(url,options)=>{
          checked(url,options);return Response.json({access_token:'test-access',refresh_token:null});
        });
        await createNotionClient('test-access',async(url,options)=>{
          checked(url,options);return Response.json({object:'page'});
        }).request('GET','/pages/test');
        const day=new Date(Date.now()+9*3600000).toISOString().slice(0,10);
        const year=Number(day.slice(0,4))-(day.slice(5,7)<'03'?1:0);
        const snapshot=await fetchSchoolSnapshot({version:1,office_code:'Z99',school_code:'0000001',school_name:'가상학교',academic_year:year},day,'test-key',async(url,options)=>{
          checked(url,options);return Response.json({RESULT:{CODE:'INFO-200'}});
        });
        let redirectCode,redirectCalls=0;
        try{await exchange('test-client','test-secret',{grant_type:'authorization_code'},async(url,options)=>{
          checked(url,options);redirectCalls++;return new Response(null,{status:302,headers:{Location:'https://never-requested.example'}});
        });}catch(error){redirectCode=exchangeDiagnostic(error);}
        return Response.json({modes,timeoutSupported:typeof AbortSignal.timeout==='function',
          calendarEmpty:snapshot.calendar.rows.length===0,redirectCode,redirectCalls});
      }};
    `;
    const modules=[{type:'ESModule',path:'/virtual/worker.mjs',contents:source}];
    for(const file of ['security.mjs','sync.mjs'])modules.push({type:'ESModule',path:'/virtual/'+file,
      contents:await readFile(new URL('../src/'+file,import.meta.url),'utf8')});
    const mf=new Miniflare(convertV4MiniflareOptions({compatibilityDate:'2026-09-30',modules,modulesRoot:'/virtual'}));
    try {
      const response=await mf.dispatchFetch('https://compatibility.example');
      assert.equal(response.status,200);
      assert.deepEqual(await response.json(),{modes:Array(5).fill('manual'),timeoutSupported:true,
        calendarEmpty:true,redirectCode:'token_exchange_redirect',redirectCalls:1});
    } finally {await mf.dispose();}
  });
