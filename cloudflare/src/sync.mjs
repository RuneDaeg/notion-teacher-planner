/** Bounded NEIS/Notion engine. Host owns per-installation leases and durable D1 saves.
 * No deletes, automatic legacy merges, credential persistence or local-state access.
 * Caps bound network use, not Workers Free CPU time; measure CPU before deployment.
 */
// Leave room for up to three NEIS calls, one OAuth call and host D1 checkpoints.
export const LIMITS = Object.freeze({ notionRequests: 19, deadlineMs: 45000, requestMs: 10000,
  calendarRows: 2000, agendaPages: 8, childPages: 4, skipRecords: 100,
  responseBytes: 1000000, stateBytes: 600000 });
export const SOURCE_MARKER = 'teacher-planner:neis-source:v1:';
const CALENDAR_URL = 'https://open.neis.go.kr/portal/data/service/selectServicePage.do?infId=OPEN17220190722175038389180&infSeq=2';
const MEALS_URL = 'https://open.neis.go.kr/portal/data/service/selectServicePage.do?infId=OPEN17320190722180924242823&infSeq=2';
const IDS = ['root_page_id', 'agenda_data_source_id', 'meals_block_id', 'status_block_id'];
const REQUIRED = {이름:'title', 일정:'date', 학년도:'number', 종류:'select', 상태:'select', '업무 분류':'select', '외부 ID':'rich_text', 보관:'checkbox'};
const GRADE_FIELDS = ['ONE_GRADE_EVENT_YN','TW_GRADE_EVENT_YN','THREE_GRADE_EVENT_YN','FR_GRADE_EVENT_YN','FIV_GRADE_EVENT_YN','SIX_GRADE_EVENT_YN'];
const MEAL_NAMES = {'1':'조식','2':'중식','3':'석식'};
const clone = value => structuredClone(value);
const fail = message => { throw new SyncError(message); };
// Locale-independent cursor ordering, including non-BMP text, matches Python.
function compare(a,b) { const left=Array.from(a),right=Array.from(b);for(let i=0;i<Math.min(left.length,right.length);i++){const difference=left[i].codePointAt(0)-right[i].codePointAt(0);if(difference)return difference;}return left.length-right.length; }
export class SyncError extends Error {
  constructor(message, {retryable=false, status=null}={}) { super(message); this.name='SyncError'; this.retryable=retryable; this.status=status; }
}
function stable(value) {
  if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+stable(value[key])).join(',') + '}';
  return JSON.stringify(value);
}
async function hash(value) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(typeof value==='string' ? value : stable(value)));
  return [...new Uint8Array(bytes)].map(value=>value.toString(16).padStart(2,'0')).join('');
}
export async function eventId(office, school, day, title, night, course) {
  return 'neis:' + await hash(JSON.stringify([office,school,day,title,night,course]));
}
function uuid(value) {
  if (typeof value!=='string' || !/^(?:[a-f\d]{32}|[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12})$/i.test(value) || /^0+$/.test(value.replaceAll('-',''))) fail('Notion 식별자를 확인하세요.');
  return value.replaceAll('-','').toLowerCase();
}
function same(a,b) { return typeof a==='string' && typeof b==='string' && a.replaceAll('-','').toLowerCase()===b.replaceAll('-','').toLowerCase(); }
function date(value) {
  if (typeof value!=='string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(Date.parse(value+'T00:00:00Z')) || new Date(value+'T00:00:00Z').toISOString().slice(0,10)!==value) fail('유효한 날짜가 필요합니다.');
  return value;
}
function shift(value, amount) { return new Date(Date.parse(value+'T00:00:00Z')+amount*86400000).toISOString().slice(0,10); }
function yearRange(year) { return [`${year}-03-01`,shift(`${year+1}-03-01`,-1)]; }
function school(manifest) {
  if (manifest?.version!==1 || !/^[A-Z]\d{2}$/.test(manifest.office_code) || !/^\d{7}$/.test(manifest.school_code) || typeof manifest.school_name!=='string' || !manifest.school_name.trim() || manifest.school_name.length>200 || !Number.isInteger(manifest.academic_year) || manifest.academic_year<1900 || manifest.academic_year>9998) fail('학교·학년도 연결 정보를 확인하세요.');
}
function text(value, max=2000, required=false) {
  if (typeof value!=='string' || value.length>max || (required&&!value.trim())) fail('원본 문자열 형식 또는 길이를 확인하세요.');
  return value;
}
function rich(value) { const result=[]; for(let i=0;i<value.length;i+=1900) result.push({type:'text',text:{content:value.slice(i,i+1900)}}); return result; }
function richValue(items=[]) {
  const parts=[];
  for(const item of items) {
    const value=item.plain_text??item.text?.content??'', url=item.text?.link?.url??null;
    if(parts.length && parts.at(-1)[1]===url) parts.at(-1)[0]+=value; else parts.push([value,url]);
  }
  return parts;
}
function plain(block) { return richValue(block.callout?.rich_text).map(x=>x[0]).join(''); }
function title(page, name='이름') { const p=page.properties?.[name]; return (p?.title??p?.rich_text??[]).map(x=>x.plain_text??x.text?.content??'').join(''); }
function active(object, kind) { if(!object?.id || object.archived || object.in_trash || object.object!==kind) fail('Notion 대상의 유형·휴지통 상태를 확인하세요.'); return object; }
async function readJson(response) {
  let body='';
  if(response.body?.getReader) {
    const reader=response.body.getReader(), decoder=new TextDecoder(); let size=0;
    try { for(;;) { const {done,value}=await reader.read(); if(done)break; size+=value.byteLength; if(size>LIMITS.responseBytes) {await reader.cancel();fail('응답 크기가 Cloudflare 시범 운영 한도를 넘었습니다.');} body+=decoder.decode(value,{stream:true}); } body+=decoder.decode(); }
    finally { reader.releaseLock(); }
  } else { body=await response.text(); if(new TextEncoder().encode(body).length>LIMITS.responseBytes) fail('응답 크기가 한도를 넘었습니다.'); }
  try { return JSON.parse(body.replace(/^\uFEFF/,'')); } catch { throw new SyncError('서비스가 올바른 JSON을 반환하지 않았습니다.',{retryable:true}); }
}
export function createNotionClient(token, fetchFn=fetch) {
  if(typeof token!=='string'||!token) fail('Notion 연결 토큰이 필요합니다.');
  const until=Date.now()+LIMITS.deadlineMs; let calls=0;
  return { get calls(){return calls;}, async request(method,path,payload) {
    if(!['GET','POST','PATCH'].includes(method)||typeof path!=='string'||!path.startsWith('/')||path.startsWith('//')||path.includes('://')) fail('Notion 요청 경로를 확인하세요.');
    if(calls>=LIMITS.notionRequests||Date.now()>=until) throw new SyncError('이번 실행의 요청 한도에 도달했습니다.',{retryable:true});
    calls++;
    let response;
    try { response=await fetchFn('https://api.notion.com/v1'+path,{method,redirect:'error',signal:AbortSignal.timeout(Math.max(1,Math.min(LIMITS.requestMs,until-Date.now()))),headers:{Authorization:'Bearer '+token,'Notion-Version':'2026-03-11','Content-Type':'application/json'},...(payload===undefined?{}:{body:JSON.stringify(payload)})}); }
    catch { throw new SyncError('Notion 응답을 확정할 수 없습니다.',{retryable:true}); }
    if(!response.ok) throw new SyncError('Notion 요청을 완료하지 못했습니다.',{status:response.status,retryable:response.status===429||response.status>=500});
    try { return await readJson(response); } catch(error) { if(error instanceof SyncError)throw error;throw new SyncError('Notion 응답을 끝까지 읽지 못했습니다.',{retryable:true}); }
  }};
}
export async function validateTargets(notion, manifest) {
  school(manifest); if(new Set(IDS.map(key=>uuid(manifest[key]))).size!==IDS.length) fail('Notion 대상 식별자를 구분하세요.');
  const cache=new Map();
  async function get(kind,id) {
    const key=kind+uuid(id);
    if(!cache.has(key)) { if(cache.size>=16) fail('Notion 부모 구조가 지원 깊이를 넘었습니다.'); const endpoint={page:'pages',block:'blocks',database:'databases',data_source:'data_sources'}[kind]; const obj=active(await notion.request('GET',`/${endpoint}/${id}`),kind); if(!same(obj.id,id))fail('Notion 응답 식별자가 다릅니다.'); cache.set(key,obj); }
    return cache.get(key);
  }
  const root=await get('page',manifest.root_page_id);
  async function inside(obj) {
    const visited=new Set();
    for(let i=0;i<12;i++) { if(same(obj.id,root.id))return; const key=obj.object+uuid(obj.id); if(visited.has(key))break; visited.add(key); const type=obj.parent?.type; if(!['page_id','block_id','database_id','data_source_id'].includes(type))break; obj=await get(type.replace('_id',''),obj.parent[type]); }
    fail('갱신 대상이 승인한 교무수첩 루트 밖에 있습니다.');
  }
  const agenda=await get('data_source',manifest.agenda_data_source_id); await inside(agenda);
  for(const [name,type]of Object.entries(REQUIRED)) if(agenda.properties?.[name]?.type!==type)fail('업무·일정 필수 속성 유형을 확인하세요.');
  const result={root,agenda};
  for(const name of ['meals','status']) { const block=await get('block',manifest[name+'_block_id']); if(block.type!=='callout'||block.has_children)fail('급식·상태는 하위 내용 없는 전용 콜아웃이어야 합니다.'); await inside(block); result[name]=block; }
  return result;
}
function cleanHtml(value) {
  value=text(value??'',50000).replace(/&(?:amp|lt|gt|quot|apos|nbsp);|&#(?:x[\da-f]+|\d+);/gi, token=> {
    const names={'&amp;':'&','&lt;':'<','&gt;':'>','&quot;':'"','&apos;':"'",'&nbsp;':' '}; if(names[token.toLowerCase()])return names[token.toLowerCase()];
    const n=parseInt(token.slice(token[2]?.toLowerCase()==='x'?3:2,-1),token[2]?.toLowerCase()==='x'?16:10); return n>0&&n<=0x10ffff?String.fromCodePoint(n):'';
  });
  return value.replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1\s*>/gi,'').replace(/<\/?(?:br|p|div|li)\b[^>]*>/gi,'\n').replace(/<[^>]*>/g,'').replace(/\r/g,'').split('\n').map(line=>line.replace(/[^\S\n]+/g,' ').trim()).filter(Boolean).join('\n');
}
function neisPage(payload, service, index, maximum) {
  if(payload?.RESULT) { if(payload.RESULT.CODE==='INFO-200'&&index===1&&!payload[service])return [0,[]]; if(payload.RESULT.CODE==='ERROR-337')throw new SyncError('NEIS 일일 조회 한도에 도달했습니다.',{retryable:true}); fail('NEIS 조회가 오류 또는 불완전한 빈 결과를 반환했습니다.'); }
  const parts=payload?.[service]; if(!Array.isArray(parts))fail('NEIS 응답 구조를 확인하세요.');
  const heads=parts.filter(p=>p&&'head'in p), data=parts.filter(p=>p&&'row'in p);
  if(heads.length!==1||data.length!==1||!Array.isArray(heads[0].head)||!Array.isArray(data[0].row))fail('NEIS 머리말·행이 누락되었습니다.');
  const counts=heads[0].head.filter(p=>'list_total_count'in p), results=heads[0].head.filter(p=>'RESULT'in p);
  if(counts.length!==1||results.length!==1||!/^\d+$/.test(String(counts[0].list_total_count)))fail('NEIS 전체 건수를 확인하세요.');
  const total=Number(counts[0].list_total_count),code=results[0].RESULT?.CODE;
  if(code==='ERROR-337')throw new SyncError('NEIS 일일 조회 한도에 도달했습니다.',{retryable:true});
  if(total>maximum||!['INFO-000','INFO-200'].includes(code)||(code==='INFO-200'&&(index!==1||total||data[0].row.length)))fail('NEIS 오류 또는 시범 운영 자료 한도를 확인하세요.');
  return [total,data[0].row];
}
export async function fetchSchoolSnapshot(manifest, day, apiKey, fetchFn=fetch) {
  school(manifest); date(day); const [start,end]=yearRange(manifest.academic_year);
  if(day<start||day>end)fail('조회 날짜가 수첩 학년도 밖입니다.');
  if(typeof apiKey!=='string'||!apiKey.trim()||apiKey.length>512||/^sample(?:[ _]key)?$/i.test(apiKey))fail('발급받은 NEIS 인증키가 필요합니다.');
  const until=Date.now()+25000;
  async function load(service,query,index) {
    if(Date.now()>=until)throw new SyncError('NEIS 조회 시간이 이번 실행 한도를 넘었습니다.',{retryable:true});
    let response;
    try { response=await fetchFn('https://open.neis.go.kr/hub/'+service+'?'+new URLSearchParams({KEY:apiKey,Type:'json',pSize:'1000',ATPT_OFCDC_SC_CODE:manifest.office_code,SD_SCHUL_CODE:manifest.school_code,...query,pIndex:String(index)}),{redirect:'error',signal:AbortSignal.timeout(Math.max(1,Math.min(10000,until-Date.now()))),headers:{Accept:'*/*'}}); }
    catch { throw new SyncError('NEIS 조회에 실패했습니다.',{retryable:true}); }
    if(!response.ok)throw new SyncError('NEIS 서버 조회에 실패했습니다.',{retryable:response.status===429||response.status>=500});
    try { return await readJson(response); } catch(error) { if(error instanceof SyncError)throw error;throw new SyncError('NEIS 응답을 끝까지 읽지 못했습니다.',{retryable:true}); }
  }
  const rows=[],seen=new Map(); let expected=null,received=0,duplicates=0;
  for(let index=1;index<=2;index++) {
    const [total,raws]=neisPage(await load('SchoolSchedule',{AA_FROM_YMD:start.replaceAll('-',''),AA_TO_YMD:end.replaceAll('-','')},index),'SchoolSchedule',index,LIMITS.calendarRows);
    if(expected===null)expected=total;
    if(total!==expected||raws.length!==Math.min(1000,total-received))fail('NEIS 페이지가 불완전합니다. 일부 행사를 반영하지 않습니다.');
    for(const raw of raws) {
      if(raw?.ATPT_OFCDC_SC_CODE!==manifest.office_code||String(raw.SD_SCHUL_CODE)!==manifest.school_code||String(raw.AY)!==String(manifest.academic_year)||!/^\d{8}$/.test(raw.AA_YMD))fail('NEIS 학교·학년도·날짜가 요청과 다릅니다.');
      const when=date(raw.AA_YMD.slice(0,4)+'-'+raw.AA_YMD.slice(4,6)+'-'+raw.AA_YMD.slice(6));
      const name=text(raw.SCHUL_NM,200,true).trim(); if(name!==manifest.school_name||when<start||when>end)fail('NEIS 학교명 또는 날짜가 다릅니다.');
      const row={date:when,title:text(raw.EVENT_NM,1000,true).trim(),description:text(raw.EVENT_CNTNT??'',100000).trim(),school_name:name,course:text(raw.SCHUL_CRSE_SC_NM,100).trim(),day_night:text(raw.DGHT_CRSE_SC_NM,100).trim(),day_type:text(raw.SBTR_DD_SC_NM??'',100).trim(),grades:[]};
      for(let n=0;n<GRADE_FIELDS.length;n++) { const value=raw[GRADE_FIELDS[n]]; if(!['Y','N','*','',null].includes(value))fail('NEIS 대상 학년 정보가 잘못되었습니다.'); if(value==='Y')row.grades.push(n+1); }
      if(raw.LOAD_DTM)row.updated_at=text(raw.LOAD_DTM,100).trim();
      row.external_id=await eventId(manifest.office_code,manifest.school_code,when,row.title,row.day_night,row.course);
      const previous=seen.get(row.external_id), signature=stable(raw);
      if(previous) { if(previous.index!==index||previous.signature!==signature)fail('NEIS 행사 식별값이 중복됩니다.'); duplicates++; } else { seen.set(row.external_id,{index,signature}); rows.push(row); }
    }
    received+=raws.length; if(received===expected)break;
  }
  if(received!==expected)fail('NEIS 전체 조회를 완료하지 못했습니다.');
  const [mealCount,mealRows]=neisPage(await load('mealServiceDietInfo',{MLSV_YMD:day.replaceAll('-','')},1),'mealServiceDietInfo',1,3);
  if(mealRows.length!==mealCount)fail('급식 조회가 불완전합니다.');
  const meals=[],codes=new Set();
  for(const raw of mealRows) {
    const code=raw?.MMEAL_SC_CODE;
    if(raw.ATPT_OFCDC_SC_CODE!==manifest.office_code||String(raw.SD_SCHUL_CODE)!==manifest.school_code||raw.MLSV_YMD!==day.replaceAll('-','')||raw.SCHUL_NM!==manifest.school_name||!MEAL_NAMES[code]||codes.has(code)||cleanHtml(raw.MMEAL_SC_NM)!==MEAL_NAMES[code])fail('급식 학교·날짜·식사 구분을 확인하세요.');
    codes.add(code); const row={meal_code:code,meal_name:MEAL_NAMES[code],menu:cleanHtml(raw.DDISH_NM),calories:cleanHtml(raw.CAL_INFO),origin:cleanHtml(raw.ORPLC_INFO),nutrition:cleanHtml(raw.NTR_INFO),updated_at:cleanHtml(raw.LOAD_DTM)};
    if(!row.menu)fail('급식 메뉴가 비었습니다.'); meals.push(row);
  }
  const shared={office_code:manifest.office_code,school_code:manifest.school_code,school_name:manifest.school_name,fetched_at:new Date().toISOString()};
  return {calendar:{...shared,source:'neis',source_url:CALENDAR_URL,academic_year:manifest.academic_year,start,end,source_row_count:received,duplicate_row_count:duplicates,rows:rows.sort((a,b)=>compare(a.date,b.date)||compare(a.title,b.title)||compare(a.course,b.course)||compare(a.day_night,b.day_night))},meals:{...shared,source:'neis-meals',source_url:MEALS_URL,date:day,rows:meals.sort((a,b)=>compare(a.meal_code,b.meal_code))}};
}
export async function groupedSchedule(snapshot, manifest) {
  school(manifest); const [start,end]=yearRange(manifest.academic_year);
  if(snapshot?.source!=='neis'||snapshot.academic_year!==manifest.academic_year||snapshot.office_code!==manifest.office_code||snapshot.school_code!==manifest.school_code||snapshot.start!==start||snapshot.end!==end||!Array.isArray(snapshot.rows)||snapshot.rows.length>LIMITS.calendarRows)fail('완전한 학년도 NEIS 원본이 필요합니다.');
  if(snapshot.rows.length&&snapshot.school_name!==manifest.school_name)fail('원본 학교명이 다릅니다.');
  const rows=[],seen=new Set();
  for(const raw of snapshot.rows) {
    const row=clone(raw); date(row.date);
    if(row.date<start||row.date>end||row.school_name!==manifest.school_name)fail('행사 학교명·날짜가 다릅니다.');
    for(const key of ['title','description','course','day_night'])text(row[key],key==='description'?100000:2000,key==='title');
    row.day_type=text(row.day_type??''); row.updated_at=text(row.updated_at??'');
    if(!Array.isArray(row.grades)||row.grades.some(n=>!Number.isInteger(n)||n<1||n>12))fail('대상 학년 배열을 확인하세요.'); row.grades=[...new Set(row.grades)].sort((a,b)=>a-b);
    const id=await eventId(manifest.office_code,manifest.school_code,row.date,row.title,row.day_night,row.course);
    if(row.external_id!==id||seen.has(id))fail('행사 외부 ID가 다르거나 중복됩니다.'); seen.add(id); rows.push(row);
  }
  rows.sort((a,b)=>compare(a.title,b.title)||compare(a.course,b.course)||compare(a.day_night,b.day_night)||compare(a.date,b.date));
  const groups=[];
  for(const row of rows) { const last=groups.at(-1); if(last&&last.title===row.title&&last.course===row.course&&last.day_night===row.day_night&&shift(last.end??last.date,1)===row.date) {last.source_rows.push(row);last.source_ids.push(row.external_id);last.end=row.date;} else groups.push({...row,end:null,source_rows:[row],source_ids:[row.external_id]}); }
  return groups.sort((a,b)=>compare(a.date,b.date)||compare(a.title,b.title)||compare(a.course,b.course)||compare(a.day_night,b.day_night));
}
function eventPage(page, manifest) { active(page,'page'); if(!same(page.parent?.data_source_id,manifest.agenda_data_source_id)||page.properties?.['학년도']?.number!==manifest.academic_year)fail('기존 행사 부모·학년도가 다릅니다.'); }
async function pages(notion,ds,filter) {
  const result=[],seen=new Set(); let cursor;
  for(let i=0;i<LIMITS.agendaPages;i++) { const response=await notion.request('POST',`/data_sources/${ds}/query`,{page_size:100,filter,...(cursor?{start_cursor:cursor}:{})}); if(!Array.isArray(response.results))fail('행사 목록 응답을 확인하세요.'); result.push(...response.results); if(!response.has_more)return result; if(typeof response.next_cursor!=='string'||seen.has(response.next_cursor))fail('행사 목록 페이지가 반복됩니다.'); cursor=response.next_cursor;seen.add(cursor); }
  fail('기존 NEIS 행사가 800개 조회 한도를 넘었습니다. 자동 변경을 중단합니다.');
}
function planGroups(rows, old, state, manifest) {
  const saved=state.groups??{},claimed=new Set(),plan=[];
  for(const input of rows) {
    const row=clone(input), members=new Set(row.source_ids),matches=Object.entries(saved).filter(([,group])=>group.source_ids.some(id=>members.has(id)));
    if(matches.length>1)fail('기존 기간 행사가 연결됩니다. 수동 통합을 먼저 확인하세요.');
    let key,previous;
    if(matches.length) {
      [key,previous]=matches[0];
      if(!previous.source_ids.every(id=>members.has(id))||previous.title!==row.title||previous.course!==row.course||previous.day_night!==row.day_night)fail('기존 기간이 축소·분리되었거나 식별 정보가 바뀌었습니다.');
      if(!old[key]||!same(old[key].id,previous.page_id))fail('기존 대표 행사 페이지가 사라졌습니다.');
    } else {
      const candidates=row.source_ids.filter(id=>old[id]);
      for(const id of candidates)eventPage(old[id],manifest);
      const activeIds=candidates.filter(id=>!old[id].properties?.['보관']?.checkbox);
      let possible=activeIds.length?activeIds:candidates;
      if(possible.length>1) {
        if(activeIds.length)fail('활성 일별 행사가 여러 개 있습니다. 먼저 별도 통합하세요.');
        possible=possible.filter(id=>{const span=old[id].properties?.['일정']?.date;return span?.start===row.date&&(span.end??span.start)===(row.end??row.date);});
        if(possible.length!==1)fail('보관된 기간의 대표 페이지가 모호합니다.');
      }
      key=possible[0]??row.external_id;
      if(old[key]) {
        const page=old[key],span=page.properties?.['일정']?.date;
        if(title(page)!==row.title||!span)fail('기존 행사 제목·날짜를 확인하세요.'); date(span.start);date(span.end??span.start);
        if(span.start<row.date||(span.end??span.start)>(row.end??row.date)||span.start>(span.end??span.start)||!row.source_rows.some(source=>source.external_id===key&&source.date>=span.start&&source.date<=(span.end??span.start)))fail('기존 기간이 원본보다 넓거나 대표 ID가 다릅니다.');
      }
    }
    if(claimed.has(key))fail('기존 기간 행사가 분리되었습니다.');claimed.add(key);
    const archives=[];
    for(const source of row.source_rows) {
      const id=source.external_id;if(id===key)continue;const page=old[id],known=previous?.archived_pages?.find(a=>a.external_id===id);
      if(known&&(!page||!same(page.id,known.id)))fail('이전 일별 원본이 사라졌습니다.');
      if(page) {eventPage(page,manifest);if(!page.properties?.['보관']?.checkbox)fail('기존 일별 페이지는 자동 보관하지 않습니다.');archives.push({external_id:id,id:page.id,date:source.date});}
    }
    if(old[key])eventPage(old[key],manifest);
    row.external_id=key;row.archived_pages=archives;
    const group={source_ids:row.source_ids,start:row.date,end:row.end??row.date,title:row.title,course:row.course,day_night:row.day_night,page_id:old[key]?.id??null,archived_pages:archives};
    plan.push({key,row,group,existing:old[key]});
  }
  return plan;
}
function sourceInfo(snapshot,row,marked=true) {
  let body=(marked?SOURCE_MARKER+row.external_id+'\n':'')+'NEIS 학사일정\n학교: '+row.school_name+'\n교육청 / 학교 코드: '+snapshot.office_code+' / '+snapshot.school_code+'\n행사: '+row.title+'\n';
  if(row.source_rows.length>1)body+='기간: '+row.date+' ~ '+row.end+' (종료일 포함)\n연속된 일별 행사 '+row.source_rows.length+'건을 한 일정으로 표시합니다.\n\n';
  body+=row.source_rows.map(source=>'날짜: '+source.date+'\n대상: '+(source.grades.length?source.grades.join(', ')+'학년':'제공된 학년 정보 없음')+'\n학교 과정: '+(source.course||'제공된 정보 없음')+'\n주야 과정: '+(source.day_night||'제공된 정보 없음')+'\n수업 구분: '+(source.day_type||'제공된 정보 없음')+'\n내용: '+(source.description||'제공된 내용 없음')+'\n'+(source.updated_at?'원본 수정일: '+source.updated_at+'\n':'')).join('\n');
  const rt=rich(body);
  if(row.archived_pages?.length) {rt.push(...rich('\n통합 전 일별 페이지 (보관함):\n'));for(const archive of row.archived_pages)rt.push({type:'text',text:{content:archive.date+' 원본 페이지\n',link:{url:'https://www.notion.so/'+archive.id.replaceAll('-','')}}});}
  rt.push(...rich('출처: '),{type:'text',text:{content:'NEIS 학사일정 Open API',link:{url:CALENDAR_URL}}});
  if(rt.length>100||new TextEncoder().encode(JSON.stringify(rt)).length>350000)fail('행사 원본 안내가 Notion 크기 한도를 넘었습니다.');
  return {object:'block',type:'callout',callout:{rich_text:rt,icon:{type:'emoji',emoji:'🏫'},color:'gray_background'}};
}
async function findInfo(notion,page,key,legacy=null,saved=null) {
  const found=[],seen=new Set();let cursor;
  for(let i=0;i<LIMITS.childPages;i++) {
    const response=await notion.request('GET',`/blocks/${page}/children?page_size=100`+(cursor?'&start_cursor='+encodeURIComponent(cursor):''));
    if(!Array.isArray(response.results))fail('행사 본문 응답을 확인하세요.');
    for(const block of response.results)if(block.type==='callout'&&!block.archived&&!block.in_trash&&(plain(block).split('\n')[0]===SOURCE_MARKER+key||(legacy&&stable(richValue(block.callout.rich_text))===stable(richValue(legacy.callout.rich_text))))) {
      const type=block.parent?.type;if(!['page_id','block_id'].includes(type)||!same(block.parent[type],page)||block.has_children)fail('원본 안내의 위치·하위 메모를 확인하세요.');found.push(block);
    }
    if(!response.has_more) {if(found.length>1||(saved&&(!found[0]||!same(found[0].id,saved))))fail('원본 안내가 중복·삭제·이동되었습니다.');return found[0]??null;}
    if(typeof response.next_cursor!=='string'||seen.has(response.next_cursor))fail('본문 페이지가 반복됩니다.');cursor=response.next_cursor;seen.add(cursor);
  }
  fail('행사 본문이 400블록 조회 한도를 넘었습니다.');
}
function properties(row,manifest,isNew=false) {
  const props={이름:{title:rich(row.title)},일정:{date:{start:row.date,end:row.end??null}}};
  if(isNew)Object.assign(props,{학년도:{number:manifest.academic_year},종류:{select:{name:'행사'}},상태:{select:{name:'예정'}},'업무 분류':{select:{name:'행정'}},'외부 ID':{rich_text:rich(row.external_id)},보관:{checkbox:false}});
  return props;
}
function changedProps(page,desired) {
  const changed={};for(const [name,value]of Object.entries(desired)) {const old=page.properties?.[name];let equal=false;
    if('title'in value)equal=stable(richValue(old?.title))===stable(richValue(value.title));
    else if('rich_text'in value)equal=stable(richValue(old?.rich_text))===stable(richValue(value.rich_text));
    else if('date'in value)equal=old?.date?.start===value.date.start&&(old.date.end??null)===(value.date.end??null);
    else if('select'in value)equal=old?.select?.name===value.select.name;
    else equal=old?.[Object.keys(value)[0]]===Object.values(value)[0];
    if(!equal)changed[name]=value;
  }return changed;
}
function mealInfo(snapshot,manifest,day) {
  const [start,end]=yearRange(manifest.academic_year);date(day);
  if(snapshot?.source!=='neis-meals'||snapshot.office_code!==manifest.office_code||snapshot.school_code!==manifest.school_code||snapshot.date!==day||day<start||day>end||!Array.isArray(snapshot.rows)||snapshot.rows.length>3||typeof snapshot.fetched_at!=='string'||!/(?:Z|[+-]\d{2}:\d{2})$/.test(snapshot.fetched_at)||!Number.isFinite(Date.parse(snapshot.fetched_at)))fail('오늘의 완전한 급식 원본이 필요합니다.');
  if(snapshot.rows.length&&snapshot.school_name!==manifest.school_name)fail('급식 학교명이 다릅니다.');
  const checked=new Date(Date.parse(snapshot.fetched_at)+9*3600000).toISOString().slice(0,16).replace('T',' ')+' KST';
  const lines=['오늘의 급식 · '+day,manifest.school_name,'조회: '+checked,''],seen=new Set();
  for(const row of snapshot.rows) { if(!MEAL_NAMES[row.meal_code]||seen.has(row.meal_code)||row.meal_name!==MEAL_NAMES[row.meal_code])fail('급식 식사 구분이 다르거나 중복됩니다.');seen.add(row.meal_code);lines.push(row.meal_name,text(row.menu,20000,true));for(const [key,label]of [['calories','열량'],['origin','원산지'],['nutrition','영양정보']]){const value=text(row[key]??'',20000);if(value)lines.push(label+': '+value);}lines.push(''); }
  if(!snapshot.rows.length)lines.push('해당 날짜에 공개된 급식 정보가 없습니다.','미등록·미제공일 수 있으므로 급식 미실시로 단정하지 않습니다.');
  lines.push('메뉴의 알레르기 번호는 원문 표시입니다. 번호가 없다고 알레르기 성분이 없다는 뜻은 아닙니다.','마지막 조회 결과입니다. 표시 날짜를 확인하세요.');
  const rt=rich(lines.join('\n'));rt.push({type:'text',text:{content:'\nNEIS 급식식단정보',link:{url:MEALS_URL}}});if(rt.length>100)fail('급식 표시 내용이 한도를 넘었습니다.');return rt;
}
export async function syncStep({notion,manifest,snapshot,state,saveState,day}) {
  if(!state||Array.isArray(state)||typeof state!=='object'||typeof saveState!=='function')fail('지속 저장 상태가 필요합니다.');
  school(manifest);date(day);
  const meal=mealInfo(snapshot?.meals,manifest,day),rows=await groupedSchedule(snapshot?.calendar,manifest);
  const binding=await hash(manifest);if(state.binding&&state.binding!==binding)fail('상태가 다른 수첩 연결에 속합니다.');
  const targets=await validateTargets(notion,manifest);
  async function save() {if(new TextEncoder().encode(JSON.stringify(state)).length>LIMITS.stateBytes)fail('설치 상태가 시범 운영 한도를 넘었습니다.');await saveState(clone(state));}
  state.binding=binding;
  async function patch(path,payload) {state.mutation={path,hash:await hash(payload)};await save();await notion.request('PATCH',path,payload);delete state.mutation;await save();}
  async function recover() {
    const p=state.pending;if(!p)return;
    let page;
    if(p.kind==='page') {const found=await pages(notion,manifest.agenda_data_source_id,{property:'외부 ID',rich_text:{equals:p.key}});if(found.length!==1||title(found[0],'외부 ID')!==p.key)fail('생성 결과가 불확실합니다. 외부 ID를 확인하기 전에는 다시 만들지 않습니다.');page=found[0];if(Object.keys(changedProps(page,p.properties)).length)fail('생성 후보 내용이 요청과 다릅니다.');}
    else if(p.kind==='info')page=await notion.request('GET','/pages/'+p.page_id);else fail('생성 체크포인트가 잘못되었습니다.');
    eventPage(page,manifest);if(title(page,'외부 ID')!==p.key)fail('생성 후보 외부 ID가 다릅니다.');
    const block=await findInfo(notion,page.id,p.key);if(!block||await hash(richValue(block.callout.rich_text))!==p.info_hash)fail('생성 원본 블록을 확인할 수 없습니다. 다시 추가하지 않습니다.');
    state.groups??={};state.records??={};state.groups[p.key]={...p.group,page_id:page.id};state.records[p.key]={page_id:page.id,info_block_id:block.id,source_hash:p.source_hash};delete state.pending;await save();
  }
  async function create(p,method,path,payload) {
    state.pending=p;await save();
    try {await notion.request(method,path,payload);}catch(error){if([400,401,403,404,429].includes(error.status)){delete state.pending;await save();}throw error;}
    await recover();
  }
  if(state.pending) {await recover();return {done:false,state};}
  const sourceHash=await hash({rows:snapshot.calendar.rows,start:snapshot.calendar.start,end:snapshot.calendar.end}),count=snapshot.calendar.rows.length;
  const calendarChanged=state.calendar_hash!==sourceHash||state.calendar_count!==count;
  // Publish today's meal independently of a potentially multi-day calendar pass.
  // A meal-only step leaves the existing request/checkpoint bound unchanged.
  const current=new Date(Date.now()+9*3600000).toISOString().slice(0,10);
  if(day!==current)fail('작업 날짜가 바뀌었습니다. 오늘의 급식을 다시 조회하세요.');
  if(state.meals_date!==day) {
    if(stable(richValue(targets.meals.callout.rich_text))!==stable(richValue(meal))) {
      await patch('/blocks/'+manifest.meals_block_id,{callout:{rich_text:meal}});
    }
    state.meals_date=day;
    state.meals_checked_at=snapshot.meals.fetched_at;
    delete state.mutation;
    await save();
    if(calendarChanged&&rows.length)return {done:false,state};
  }
  if(calendarChanged) {
    const old={};for(const page of await pages(notion,manifest.agenda_data_source_id,{property:'외부 ID',rich_text:{starts_with:'neis:'}})) {const key=title(page,'외부 ID');if(!key.startsWith('neis:'))continue;if(old[key])fail('같은 외부 ID의 행사가 중복됩니다.');old[key]=page;}
    const plan=planGroups(rows,old,state,manifest);
    if(state.cycle?.source_hash!==sourceHash)state.cycle={source_hash:sourceHash,cursor:0};
    let cursor=state.cycle.cursor;
    if(!Number.isInteger(cursor)||cursor<0||cursor>plan.length)fail('행사 작업 위치가 잘못되었습니다.');
    let examined=0;
    while(cursor<plan.length&&examined<LIMITS.skipRecords) {
      const {key,row,group,existing}=plan[cursor],record=state.records?.[key];
      if(!existing||!record?.info_block_id||!same(record.page_id,existing.id)
          ||Object.keys(changedProps(existing,properties(row,manifest))).length
          ||record.source_hash!==await hash(row))break;
      // The queried live page and unchanged source agree. Do not reread its
      // owned callout simply because another event changed the annual hash.
      state.groups[key]={...group,page_id:existing.id};
      cursor++;
      examined++;
    }
    state.cycle.cursor=cursor;
    if(examined===LIMITS.skipRecords&&cursor<plan.length) {
      await save();
      return {done:false,state};
    }
    if(cursor<plan.length) {
      const item=plan[cursor],{key,row,group,existing}=item,info=sourceInfo(snapshot.calendar,row),sourceHashItem=await hash(row),record=state.records?.[key];
      const pending={key,group,source_hash:sourceHashItem,info_hash:await hash(richValue(info.callout.rich_text))};
      if(!existing) {const props=properties(row,manifest,true);await create({...pending,kind:'page',properties:props},'POST','/pages',{parent:{type:'data_source_id',data_source_id:manifest.agenda_data_source_id},properties:props,children:[info]});}
      else {
        if(record?.page_id&&!same(record.page_id,existing.id))fail('대표 페이지 ID가 달라졌습니다.');
        const block=await findInfo(notion,existing.id,key,sourceInfo(snapshot.calendar,row,false),record?.info_block_id);
        const props=changedProps(existing,properties(row,manifest));if(Object.keys(props).length)await patch('/pages/'+existing.id,{properties:props});
        if(block) {if(stable(richValue(block.callout.rich_text))!==stable(richValue(info.callout.rich_text)))await patch('/blocks/'+block.id,{callout:{rich_text:info.callout.rich_text}});state.groups??={};state.records??={};state.groups[key]={...group,page_id:existing.id};state.records[key]={page_id:existing.id,info_block_id:block.id,source_hash:sourceHashItem};}
        else await create({...pending,kind:'info',page_id:existing.id},'PATCH','/blocks/'+existing.id+'/children',{children:[info],position:{type:'start'}});
      }
      state.cycle.cursor=cursor+1;delete state.mutation;await save();return {done:false,state};
    }
    state.calendar_hash=sourceHash;state.calendar_count=count;state.calendar_checked_at=snapshot.calendar.fetched_at;delete state.cycle;await save();
  }
  state.completed_at=new Date().toISOString();delete state.mutation;await save();
  return {done:true,state};
}
