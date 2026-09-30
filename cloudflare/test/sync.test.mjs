import test from 'node:test';
import assert from 'node:assert/strict';
import {createNotionClient,validateTargets,fetchSchoolSnapshot,syncStep,groupedSchedule,eventId,SOURCE_MARKER,LIMITS,SyncError} from '../src/sync.mjs';

const uuid=n=>n.toString(16).padStart(32,'0').replace(/^(........)(....)(....)(....)(............)$/,'$1-$2-$3-$4-$5');
const clone=value=>structuredClone(value);
const rt=value=>[{type:'text',text:{content:value}}];
const flatten=items=>items.map(item=>item.plain_text??item.text?.content??'').join('');
const day=new Date(Date.now()+9*3600000).toISOString().slice(0,10);
const year=Number(day.slice(0,4))-(day.slice(5,7)<'03'?1:0);
const start=`${year}-03-01`, end=new Date(Date.parse(`${year+1}-03-01T00:00:00Z`)-86400000).toISOString().slice(0,10);
const dayAt=n=>new Date(Date.parse(start+'T00:00:00Z')+n*86400000).toISOString().slice(0,10);
const manifest={version:1,office_code:'Z99',school_code:'0000001',school_name:'가상학교',academic_year:year,
  root_page_id:uuid(1),agenda_data_source_id:uuid(2),meals_block_id:uuid(3),status_block_id:uuid(4)};
const schema=Object.fromEntries(Object.entries({이름:'title',일정:'date',학년도:'number',종류:'select',상태:'select','업무 분류':'select','외부 ID':'rich_text',보관:'checkbox'}).map(([key,type])=>[key,{type}]));

class FakeNotion {
  constructor() {
    this.calls=[];this.objects=new Map();this.children=new Map();this.serial=100;this.saved=null;this.loss=null;this.loseBefore=false;
    const root={id:manifest.root_page_id,object:'page',parent:{type:'workspace',workspace:true}};
    const db={id:uuid(5),object:'database',parent:{type:'page_id',page_id:root.id}};
    this.put('pages',root);this.put('databases',db);this.put('data_sources',{id:manifest.agenda_data_source_id,object:'data_source',parent:{type:'database_id',database_id:db.id},properties:schema});
    for(const key of ['meals_block_id','status_block_id'])this.put('blocks',{id:manifest[key],object:'block',type:'callout',has_children:false,parent:{type:'page_id',page_id:root.id},callout:{rich_text:rt('미조회')}});
  }
  put(kind,obj){this.objects.set('/'+kind+'/'+obj.id,obj);return obj;}
  newBlock(parent,payload){const block={...clone(payload),id:uuid(this.serial++),object:'block',parent:{type:'page_id',page_id:parent},has_children:false};this.put('blocks',block);const all=this.children.get(parent)??[];all.push(block.id);this.children.set(parent,all);return block;}
  nativeCallouts() {
    const targets={};
    for(const name of ['meals','status']) {
      const parent=this.objects.get('/blocks/'+manifest[name+'_block_id']);
      parent.callout.rich_text=[];parent.has_children=true;
      const child=this.newBlock(parent.id,{type:'paragraph',paragraph:{rich_text:rt('연결 준비')},archived:false,in_trash:false});
      child.parent={type:'block_id',block_id:parent.id};targets[name]=child;
    }
    return targets;
  }
  event(row,{end=null,archived=false,notes=true}={}) {
    const page={object:'page',id:uuid(this.serial++),parent:{type:'data_source_id',data_source_id:manifest.agenda_data_source_id},properties:{이름:{title:rt(row.title)},일정:{date:{start:row.date,end}},학년도:{number:year},'외부 ID':{rich_text:rt(row.external_id)},보관:{checkbox:archived},'교사 관계':{relation:[{id:uuid(88)}]},상태:{select:{name:'검토중'}}}};
    this.put('pages',page);if(notes)this.newBlock(page.id,{type:'paragraph',paragraph:{rich_text:rt('수동 메모 유지')}});return page;
  }
  async request(method,path,payload) {
    this.calls.push([method,path,clone(payload)]);
    const creation=method==='POST'&&path==='/pages'||method==='PATCH'&&path.endsWith('/children');
    if(creation)assert.ok(this.saved?.pending,'creation must follow durable pending save');
    if(method==='PATCH'&&!creation)assert.ok(this.saved?.mutation,'patch must follow durable save');
    const lost=this.loss?.(method,path,payload);if(lost&&this.loseBefore){this.loss=null;throw new SyncError('lost',{retryable:true});}
    let result;
    if(method==='GET'&&path.includes('/children?')) {
      const id=path.split('/')[2], ids=this.children.get(id)??[];
      result={results:ids.map(id=>clone(this.objects.get('/blocks/'+id))),has_more:false};
    } else if(method==='GET') {if(!this.objects.has(path))throw new SyncError('missing',{status:404});result=clone(this.objects.get(path));}
    else if(method==='POST'&&path.endsWith('/query')) {
      const filter=payload.filter?.rich_text??{}, found=[...this.objects.values()].filter(obj=>obj.object==='page'&&obj.parent?.data_source_id===manifest.agenda_data_source_id).filter(obj=>{const value=flatten(obj.properties['외부 ID'].rich_text);return filter.equals?value===filter.equals:value.startsWith(filter.starts_with??'');});
      const offset=Number(payload.start_cursor??0),size=100;result={results:clone(found.slice(offset,offset+size)),has_more:offset+size<found.length,next_cursor:String(offset+size)};
    } else if(method==='POST'&&path==='/pages') {result=this.put('pages',{object:'page',id:uuid(this.serial++),parent:clone(payload.parent),properties:clone(payload.properties)});for(const block of payload.children??[])this.newBlock(result.id,block);}
    else if(method==='PATCH'&&path.endsWith('/children'))result={results:payload.children.map(block=>this.newBlock(path.split('/')[2],block))};
    else if(method==='PATCH') {result=this.objects.get(path);assert.ok(result);if(payload.properties)Object.assign(result.properties,clone(payload.properties));if(payload.callout)Object.assign(result.callout,clone(payload.callout));if(payload.paragraph)Object.assign(result.paragraph,clone(payload.paragraph));}
    else throw new Error('unhandled '+method+path);
    if(lost){this.loss=null;throw new SyncError('lost',{retryable:true});}
    return clone(result);
  }
  mutations(){return this.calls.filter(([method,path])=>method==='PATCH'||method==='POST'&&path==='/pages');}
}
async function fixture(events=[]) {
  const notion=new FakeNotion(),state={},saved=[];
  const snapshot={calendar:{source:'neis',office_code:'Z99',school_code:'0000001',school_name:'가상학교',academic_year:year,start,end,fetched_at:new Date().toISOString(),rows:[]},meals:{source:'neis-meals',office_code:'Z99',school_code:'0000001',school_name:'가상학교',date:day,fetched_at:new Date().toISOString(),rows:[{meal_code:'2',meal_name:'중식',menu:'가상밥\n가상국 (1.2.5)',calories:'700 Kcal',origin:'원산지',nutrition:'영양'}]}};
  async function add(when=day,title='가상 행사',description='설명',grades=[1,2]) {const row={date:when,title,description,grades,school_name:'가상학교',course:'고등학교',day_night:'주간',day_type:'해당없음'};row.external_id=await eventId('Z99','0000001',when,title,row.day_night,row.course);snapshot.calendar.rows.push(row);snapshot.calendar.rows.sort((a,b)=>a.date.localeCompare(b.date)||a.title.localeCompare(b.title));return row;}
  for(const args of events)await add(...args);
  async function saveState(value){notion.saved=clone(value);saved.push(clone(value));}
  const args={notion,manifest:clone(manifest),snapshot,state,saveState,day};
  return {...args,add,saved,step:()=>syncStep(args),async primeMeals(){assert.equal((await syncStep(args)).done,false);assert.equal(state.meals_date,day);notion.calls=[];},async finish(){for(let i=0;i<300;i++){const result=await syncStep(args);if(result.done)return result;}throw new Error('did not complete');}};
}
function apiPage(service,rows,total=rows.length,code='INFO-000') {return {[service]:[{head:[{list_total_count:total},{RESULT:{CODE:code}}]},{row:rows}]};}
function rawEvent(when=day) {return {ATPT_OFCDC_SC_CODE:'Z99',SD_SCHUL_CODE:'0000001',AY:String(year),AA_YMD:when.replaceAll('-',''),SCHUL_NM:'가상학교',EVENT_NM:'가상 행사',EVENT_CNTNT:'설명',SCHUL_CRSE_SC_NM:'고등학교',DGHT_CRSE_SC_NM:'주간',SBTR_DD_SC_NM:'해당없음',ONE_GRADE_EVENT_YN:'Y',TW_GRADE_EVENT_YN:'N',THREE_GRADE_EVENT_YN:'N',FR_GRADE_EVENT_YN:'N',FIV_GRADE_EVENT_YN:'N',SIX_GRADE_EVENT_YN:'N'};}
function rawMeal(code) {return {ATPT_OFCDC_SC_CODE:'Z99',SD_SCHUL_CODE:'0000001',MLSV_YMD:day.replaceAll('-',''),SCHUL_NM:'가상학교',MMEAL_SC_CODE:code,MMEAL_SC_NM:{1:'조식',2:'중식',3:'석식'}[code],DDISH_NM:'밥<br/>국 (1.2.5)<script>비표시</script>',CAL_INFO:'700 Kcal',ORPLC_INFO:'국내산',NTR_INFO:'단백질'};}
const jsonResponse=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});

test('event IDs exactly match Python UTF-8 compact JSON contract',async()=>assert.equal(await eventId('Z99','0000001','2026-09-30','가상 행사','주간','고등학교'),'neis:d2c624340ada54b2d28c2e0f75780602c1efbacf7c80d7ae435f0ab4ad3727f6'));
test('grouping uses exact title/course/night and adjacent calendar days while preserving details',async()=>{const f=await fixture([[dayAt(1),'행사','첫째',[1]],[dayAt(2),'행사','둘째',[2]],[dayAt(4),'행사']]);const groups=await groupedSchedule(f.snapshot.calendar,manifest);assert.equal(groups.length,2);assert.equal(groups[0].end,dayAt(2));assert.deepEqual(groups[0].source_rows.map(row=>row.grades),[[1],[2]]);assert.equal(groups[1].end,null);});
test('target validation is read only and verifies actual parents and flat dedicated blocks',async()=>{const f=await fixture();await validateTargets(f.notion,manifest);assert.equal(f.notion.mutations().length,0);for(const id of [manifest.meals_block_id,manifest.status_block_id]){const block=f.notion.objects.get('/blocks/'+id);block.has_children=true;await assert.rejects(validateTargets(f.notion,manifest),/급식·상태 전용 콜아웃 구조를 확인하세요\./);block.has_children=false;}f.notion.objects.get('/databases/'+uuid(5)).parent={type:'workspace',workspace:true};await assert.rejects(validateTargets(f.notion,manifest),/루트 밖/);});
test('schema change, trash and parent cycles stop before mutations',async()=>{const f=await fixture();f.notion.objects.get('/data_sources/'+manifest.agenda_data_source_id).properties={...schema,일정:{type:'rich_text'}};await assert.rejects(f.step(),/속성/);assert.equal(f.notion.mutations().length,0);});
test('native empty callouts resolve exactly one paragraph each with two bounded child queries',async()=>{
  const f=await fixture(),children=f.notion.nativeCallouts();
  const result=await validateTargets(f.notion,manifest);
  for(const name of ['meals','status']) {
    assert.equal(result[name].id,manifest[name+'_block_id']);
    assert.deepEqual(result[name+'Text'],{id:children[name].id,type:'paragraph',container_id:manifest[name+'_block_id'],rich_text:rt('연결 준비')});
  }
  assert.equal(f.notion.calls.filter(([,path])=>path.endsWith('/children?page_size=2')).length,2);
  assert.equal(f.notion.mutations().length,0);
});
test('native callouts reject titles, manual sibling notes, nesting and inconsistent child metadata',async()=>{
  const bad=[
    (f,c,p)=>p.callout.rich_text=rt('기존 제목/메모'),
    (f,c,p)=>p.callout.rich_text=rt(''),
    (f,c,p)=>delete p.has_children,
    (f,c)=>c.archived=true,
    (f,c)=>c.in_trash=true,
    (f,c)=>c.object='page',
    (f,c)=>c.has_children=true,
    (f,c)=>delete c.has_children,
    (f,c)=>c.type='heading_1',
    (f,c)=>c.paragraph.rich_text=null,
    (f,c)=>c.parent={type:'page_id',page_id:manifest.root_page_id},
    (f,c)=>c.parent={type:'block_id',block_id:manifest.status_block_id},
    (f,c)=>c.id=manifest.root_page_id,
    (f,c)=>c.id='not-an-id',
    (f,c,p)=>f.notion.newBlock(p.id,{type:'paragraph',paragraph:{rich_text:rt('수동 메모')}}),
  ];
  for(const mutate of bad) {
    const f=await fixture(),children=f.notion.nativeCallouts(),parent=f.notion.objects.get('/blocks/'+manifest.meals_block_id);
    mutate(f,children.meals,parent);
    await assert.rejects(f.step(),SyncError);
    assert.equal(f.notion.mutations().length,0);
  }
});
test('native callout child lists must be complete and contain exactly one active block',async()=>{
  for(const response of [{has_more:true},{has_more:undefined},{next_cursor:'unexpected'}, {results:[]},{results:null}]) {
    const f=await fixture();f.notion.nativeCallouts();
    const request=f.notion.request.bind(f.notion);
    f.notion.request=async(method,path,payload)=>{
      const result=await request(method,path,payload);
      return path===`/blocks/${manifest.meals_block_id}/children?page_size=2`?{...result,...response}:result;
    };
    await assert.rejects(f.step(),SyncError);assert.equal(f.notion.mutations().length,0);
  }
});
test('native meal write and lost response recovery only patch the verified paragraph',async()=>{
  const f=await fixture(),children=f.notion.nativeCallouts();
  const original=clone(f.notion.objects.get('/blocks/'+manifest.meals_block_id));
  f.notion.loss=(method,path)=>method==='PATCH'&&path==='/blocks/'+children.meals.id;
  await assert.rejects(f.step(),/lost/);
  assert.equal(f.state.mutation.path,'/blocks/'+children.meals.id);
  const done=await f.step();assert.equal(done.done,true);
  assert.equal(done.statusTarget.id,children.status.id);assert.equal(done.statusTarget.type,'paragraph');
  assert.match(flatten(children.meals.paragraph.rich_text),/1\.2\.5/);
  assert.deepEqual(f.notion.objects.get('/blocks/'+manifest.meals_block_id),original);
  assert.deepEqual(f.notion.mutations().map(([method,path,payload])=>[method,path,Object.keys(payload)]),[['PATCH','/blocks/'+children.meals.id,['paragraph']]]);
  assert.equal(f.state.mutation,undefined);
});
test('native paragraph moved or annotated between attempts is never reused from saved mutation',async()=>{
  const f=await fixture(),children=f.notion.nativeCallouts();
  f.notion.loss=(method,path)=>method==='PATCH'&&path==='/blocks/'+children.meals.id;
  await assert.rejects(f.step(),/lost/);
  children.meals.parent={type:'block_id',block_id:manifest.status_block_id};f.notion.calls=[];
  await assert.rejects(f.step(),SyncError);assert.equal(f.notion.mutations().length,0);
  assert.ok(f.state.mutation);
});
test('meal is published first, then one event per step; unchanged hash makes no calendar query',async()=>{const f=await fixture([[dayAt(1),'첫 행사'],[dayAt(3),'다른 행사']]);assert.equal((await f.step()).done,false);assert.equal(f.state.meals_date,day);assert.equal(f.state.completed_at,undefined);assert.equal(f.state.records,undefined);assert.equal(f.notion.calls.filter(([,p])=>p.endsWith('/query')).length,0);assert.equal((await f.step()).done,false);assert.equal(Object.keys(f.state.records).length,1);assert.equal((await f.step()).done,false);assert.equal(Object.keys(f.state.records).length,2);assert.equal((await f.step()).done,true);f.notion.calls=[];const before=f.saved.length;assert.equal((await f.step()).done,true);assert.ok(f.saved.length>before);assert.equal(f.notion.calls.filter(([,path])=>path.endsWith('/query')).length,0);assert.equal(f.notion.mutations().length,0);});
test('lost create response recovers by external ID and marker without duplicate page',async()=>{const f=await fixture([[dayAt(1)]]);await f.primeMeals();f.notion.loss=(method,path)=>method==='POST'&&path==='/pages';await assert.rejects(f.step(),/lost/);assert.ok(f.state.pending);const creates=f.notion.calls.filter(([m,p])=>m==='POST'&&p==='/pages').length;assert.equal((await f.step()).done,false);assert.equal(f.state.pending,undefined);await f.finish();assert.equal(f.notion.calls.filter(([m,p])=>m==='POST'&&p==='/pages').length,creates);});
test('ambiguous create with zero matching pages stops without blind retry',async()=>{const f=await fixture([[dayAt(1)]]);await f.primeMeals();f.notion.loss=(method,path)=>method==='POST'&&path==='/pages';f.notion.loseBefore=true;await assert.rejects(f.step());await assert.rejects(f.step(),error=>error.retryable===false&&/불확실/.test(error.message));assert.equal(f.notion.calls.filter(([m,p])=>m==='POST'&&p==='/pages').length,1);});
test('a failed durable save prevents remote creation',async()=>{const f=await fixture([[dayAt(1)]]);await f.primeMeals();await assert.rejects(syncStep({...f,saveState:async()=>{throw new Error('D1 unavailable');}}),/D1/);assert.equal(f.notion.mutations().length,0);});
test('adopts existing canonical range and archived daily originals without hiding/deleting notes',async()=>{const f=await fixture([[dayAt(1)],[dayAt(2)],[dayAt(3)]]);const [a,b,c]=f.snapshot.calendar.rows,canonical=f.notion.event(b,{end:dayAt(3)}),first=f.notion.event(a,{archived:true}),last=f.notion.event(c,{archived:true});const before=clone(canonical.properties['교사 관계']);await f.finish();assert.equal(Object.keys(f.state.records).length,1);assert.equal(f.state.groups[b.external_id].page_id,canonical.id);assert.equal(canonical.properties.일정.date.start,dayAt(1));assert.deepEqual(canonical.properties['교사 관계'],before);assert.equal(canonical.properties.상태.select.name,'검토중');assert.equal(f.notion.children.get(canonical.id).length,2);assert.equal(first.properties.보관.checkbox,true);assert.equal(last.properties.보관.checkbox,true);assert.equal(f.notion.calls.filter(([m,p])=>m==='POST'&&p==='/pages').length,0);});
test('multiple active legacy daily pages are not automatically merged or archived',async()=>{const f=await fixture([[dayAt(1)],[dayAt(2)]]);for(const row of f.snapshot.calendar.rows)f.notion.event(row);await f.primeMeals();await assert.rejects(f.step(),/별도 통합/);assert.equal(f.notion.mutations().length,0);});
test('range extension keeps original anchor and start/end while shrink stops before write',async()=>{const f=await fixture([[dayAt(2)],[dayAt(3)]]);await f.finish();const key=f.snapshot.calendar.rows[0].external_id,id=f.state.records[key].page_id;await f.add(dayAt(1));await f.finish();assert.equal(f.state.records[key].page_id,id);assert.equal(f.notion.objects.get('/pages/'+id).properties.일정.date.start,dayAt(1));f.snapshot.calendar.rows=f.snapshot.calendar.rows.filter(row=>row.date!==dayAt(2));f.notion.calls=[];await assert.rejects(f.step(),/축소·분리/);assert.equal(f.notion.mutations().length,0);});
test('bridging two saved periods stops, preserving both pages',async()=>{const f=await fixture([[dayAt(1)],[dayAt(3)]]);await f.finish();await f.add(dayAt(2));f.notion.calls=[];await assert.rejects(f.step(),/연결됩니다/);assert.equal(f.notion.mutations().length,0);});
test('edited teacher notes and arbitrary callouts remain unchanged on source update',async()=>{const f=await fixture([[dayAt(1)]]);await f.finish();const key=f.snapshot.calendar.rows[0].external_id,id=f.state.records[key].page_id,note=f.notion.newBlock(id,{type:'callout',callout:{rich_text:rt('교사 개인 메모')}});f.snapshot.calendar.rows[0].description='수정한 원본';await f.finish();assert.equal(plainText(f.notion.objects.get('/blocks/'+note.id)),'교사 개인 메모');assert.equal(f.notion.children.get(id).length,2);});
function plainText(block){return flatten(block.callout.rich_text);}
test('lost info append is recovered without a duplicate block',async()=>{const f=await fixture([[dayAt(1)]]),page=f.notion.event(f.snapshot.calendar.rows[0]);await f.primeMeals();f.notion.loss=(m,p)=>m==='PATCH'&&p.endsWith('/children');await assert.rejects(f.step(),/lost/);assert.equal(f.state.pending.kind,'info');await f.finish();assert.equal(f.notion.children.get(page.id).length,2);assert.equal(f.notion.calls.filter(([m,p])=>m==='PATCH'&&p.endsWith('/children')).length,1);});
test('owned callout containing new nested user notes is never overwritten',async()=>{const f=await fixture([[dayAt(1)]]);await f.finish();const key=f.snapshot.calendar.rows[0].external_id;f.notion.objects.get('/blocks/'+f.state.records[key].info_block_id).has_children=true;f.snapshot.calendar.rows[0].description='변경';f.notion.calls=[];await assert.rejects(f.step(),/하위 메모/);assert.equal(f.notion.mutations().length,0);});
test('duplicate external IDs, partial year and stale meals stop without changes',async()=>{const f=await fixture([[dayAt(1)]]);f.notion.event(f.snapshot.calendar.rows[0]);f.notion.event(f.snapshot.calendar.rows[0]);await f.primeMeals();await assert.rejects(f.step(),/중복/);f.snapshot.calendar.start=dayAt(1);await assert.rejects(f.step(),/완전한 학년도/);f.snapshot.calendar.start=start;f.snapshot.meals.date=dayAt(0)===day?dayAt(1):dayAt(0);await assert.rejects(f.step(),/오늘의/);assert.equal(f.notion.mutations().length,0);});
test('home renders only lunch menu and calories while retaining allergy numbers and source link',async()=>{
  const f=await fixture();
  f.snapshot.meals.rows=Object.entries({1:'조식',2:'중식',3:'석식'}).map(([code,name])=>({meal_code:code,meal_name:name,
    menu:name+' 메뉴 (1.2.5)',calories:code+'00 Kcal',origin:'원산지 상세',nutrition:'영양정보 상세'}));
  const original=clone(f.snapshot.meals);
  await f.finish();
  const block=f.notion.objects.get('/blocks/'+manifest.meals_block_id),body=plainText(block);
  assert.ok(body.startsWith('오늘의 중식 · '+day));assert.match(body,/가상학교\n조회:/);
  assert.match(body,/중식 메뉴 \(1\.2\.5\)/);assert.match(body,/열량: 200 Kcal/);
  assert.doesNotMatch(body,/조식|석식|100 Kcal|300 Kcal|원산지 상세|영양정보 상세/);
  assert.ok(block.callout.rich_text.some(item=>item.text?.content.includes('NEIS 급식식단정보')&&item.text.link?.url.startsWith('https://open.neis.go.kr/')));
  assert.deepEqual(f.snapshot.meals,original);
});
test('missing lunch is explicit even when breakfast or dinner is present and never substitutes another meal',async()=>{
  for(const codes of [[],['1'],['3'],['1','3']]) {
    const f=await fixture();f.snapshot.meals.rows=codes.map(code=>({meal_code:code,meal_name:{1:'조식',3:'석식'}[code],menu:'다른 식사 메뉴 (1.2.5)'}));
    await f.finish();const body=plainText(f.notion.objects.get('/blocks/'+manifest.meals_block_id));
    assert.ok(body.startsWith('오늘의 중식 · '+day));
    assert.match(body,/해당 날짜에 공개된 중식 정보가 없습니다\./);assert.doesNotMatch(body,/다른 식사 메뉴/);
  }
});
test('excluded meals and undisplayed fields remain validated before any Notion write',async()=>{
  for(const change of [{menu:''},{calories:123},{origin:{}},{nutrition:'x'.repeat(20001)},{meal_name:'잘못된 구분'},{meal_code:'9'},{meal_code:2,meal_name:'중식'}]) {
    const f=await fixture();f.snapshot.meals.rows.push({meal_code:'1',meal_name:'조식',menu:'조식 메뉴',...change});
    await assert.rejects(f.step(),SyncError);assert.equal(f.notion.mutations().length,0);
  }
  const duplicate=await fixture();duplicate.snapshot.meals.rows=[{meal_code:'1',meal_name:'조식',menu:'조식 메뉴'},{meal_code:'1',meal_name:'조식',menu:'다른 조식'}];
  await assert.rejects(duplicate.step(),/중복/);assert.equal(duplicate.notion.mutations().length,0);
  const lunch=await fixture();lunch.snapshot.meals.rows[0].origin=123;
  await assert.rejects(lunch.step(),SyncError);assert.equal(lunch.notion.mutations().length,0);
});
test('NEIS complete snapshot reads meals once and validates school/date/allergy data',async()=>{const urls=[];const snapshot=await fetchSchoolSnapshot(manifest,day,'fake-neis-key',async url=>{urls.push(new URL(url));return jsonResponse(url.includes('SchoolSchedule')?apiPage('SchoolSchedule',[rawEvent()]):apiPage('mealServiceDietInfo',['1','2','3'].map(rawMeal)));});assert.equal(urls.length,2);assert.equal(snapshot.calendar.rows.length,1);assert.equal(snapshot.meals.rows.length,3);assert.equal(snapshot.meals.rows[0].menu,'밥\n국 (1.2.5)');assert.ok(!JSON.stringify(snapshot).includes('fake-neis-key'));});
test('NEIS no-data is distinct from errors, partial pages and later-page gaps',async()=>{const empty=await fetchSchoolSnapshot(manifest,day,'fake-key',async()=>jsonResponse({RESULT:{CODE:'INFO-200'}}));assert.equal(empty.calendar.rows.length,0);assert.equal(empty.meals.rows.length,0);await assert.rejects(fetchSchoolSnapshot(manifest,day,'fake-key',async()=>jsonResponse({RESULT:{CODE:'ERROR-337'}})),error=>error.retryable===true);await assert.rejects(fetchSchoolSnapshot(manifest,day,'fake-key',async()=>jsonResponse(apiPage('SchoolSchedule',[rawEvent()],2))),/불완전/);let calls=0;await assert.rejects(fetchSchoolSnapshot(manifest,day,'fake-key',async()=>{calls++;return jsonResponse(calls===1?apiPage('SchoolSchedule',Array.from({length:1000},()=>rawEvent()),1001):{RESULT:{CODE:'INFO-200'}});}),/불완전/);assert.equal(calls,2);});
test('NEIS 2000-row cap and wrong schools fail before partial snapshots',async()=>{await assert.rejects(fetchSchoolSnapshot(manifest,day,'fake-key',async()=>jsonResponse(apiPage('SchoolSchedule',[],2001))),/한도/);const raw=rawEvent();raw.SD_SCHUL_CODE='7654321';await assert.rejects(fetchSchoolSnapshot(manifest,day,'fake-key',async()=>jsonResponse(apiPage('SchoolSchedule',[raw]))),/학교/);});
test('NEIS successful full pagination has at most three requests and rejects cross-page repeats',async()=>{const first=Array.from({length:1000},()=>rawEvent());const second={...rawEvent(),EVENT_NM:'다른 행사'};let calls=0;const result=await fetchSchoolSnapshot(manifest,day,'fake-key',async url=>{calls++;if(url.includes('mealService'))return jsonResponse({RESULT:{CODE:'INFO-200'}});return jsonResponse(apiPage('SchoolSchedule',new URL(url).searchParams.get('pIndex')==='1'?first:[second],1001));});assert.equal(calls,3);assert.equal(result.calendar.rows.length,2);assert.equal(result.calendar.source_row_count,1001);assert.equal(result.calendar.duplicate_row_count,999);await assert.rejects(fetchSchoolSnapshot(manifest,day,'fake-key',async url=>jsonResponse(apiPage('SchoolSchedule',new URL(url).searchParams.get('pIndex')==='1'?first:[rawEvent()],1001))),/중복/);});
test('all-archived existing period stays archived and a missing saved anchor is never recreated',async()=>{const f=await fixture([[dayAt(1)],[dayAt(2)]]),[a,b]=f.snapshot.calendar.rows,canonical=f.notion.event(a,{end:b.date,archived:true});f.notion.event(b,{archived:true});await f.finish();assert.equal(canonical.properties.보관.checkbox,true);f.notion.objects.delete('/pages/'+canonical.id);f.snapshot.calendar.rows[0].description='원본 변경';f.notion.calls=[];await assert.rejects(f.step(),/사라졌습니다/);assert.equal(f.notion.mutations().length,0);});
test('a lost known-ID PATCH is safely reconciled without appending or erasing notes',async()=>{const f=await fixture([[dayAt(1)]]);await f.finish();const key=f.snapshot.calendar.rows[0].external_id,id=f.state.records[key].info_block_id;f.snapshot.calendar.rows[0].description='수정';f.notion.loss=(m,p)=>m==='PATCH'&&p==='/blocks/'+id;await assert.rejects(f.step(),/lost/);assert.ok(f.state.mutation);const count=f.notion.calls.filter(([m,p])=>m==='PATCH'&&p==='/blocks/'+id).length;await f.finish();assert.equal(f.state.mutation,undefined);assert.equal(f.notion.calls.filter(([m,p])=>m==='PATCH'&&p==='/blocks/'+id).length,count);});
test('malformed success JSON is retryable and never exposes its raw body',async()=>{const notion=createNotionClient('fake',async()=>new Response('{broken',{status:200}));await assert.rejects(notion.request('POST','/pages',{}),error=>error.retryable===true&&!error.message.includes('broken'));});
test('Notion client enforces fixed origin, redirects, request cap and redacts errors',async()=>{const seen=[];const notion=createNotionClient('fake-notion-token',async(url,options)=>{seen.push([url,options]);return jsonResponse({ok:true});});for(let n=0;n<LIMITS.notionRequests;n++)await notion.request('GET','/pages/'+uuid(1));await assert.rejects(notion.request('GET','/pages/'+uuid(1)),error=>error.retryable===true);assert.equal(seen.length,LIMITS.notionRequests);assert.equal(seen[0][1].redirect,'manual');await assert.rejects(notion.request('GET','//evil.test'),/경로/);const broken=createNotionClient('private-token',async()=>{throw new Error('leak private-token');});await assert.rejects(broken.request('POST','/pages',{}),error=>error.retryable&&!error.message.includes('private-token'));});
test('Notion and NEIS reject redirects without a second request or exposing target URLs',async()=>{
  for(const status of [301,302,303,307,308]) {
    let calls=0;
    const fetcher=async(url,options)=>{
      calls++;assert.equal(options.redirect,'manual');assert.ok(options.signal instanceof AbortSignal);
      return new Response('private-token',{status,headers:{Location:'https://private-target.example/private-key'}});
    };
    await assert.rejects(createNotionClient('private-token',fetcher).request('POST','/pages',{}),error=>error instanceof SyncError&&!error.retryable&&/리디렉션/.test(error.message)&&!error.message.includes('private'));
    assert.equal(calls,1);calls=0;
    await assert.rejects(fetchSchoolSnapshot(manifest,day,'private-key',fetcher),error=>error instanceof SyncError&&!error.retryable&&/리디렉션/.test(error.message)&&!error.message.includes('private'));
    assert.equal(calls,1);
  }
});
test('Notion explicit 401/429 errors have runtime-compatible status',async()=>{for(const status of [401,429,503]) {const notion=createNotionClient('fake',async()=>new Response('private contents',{status}));await assert.rejects(notion.request('GET','/pages/'+uuid(1)),error=>error.status===status&&error.retryable===(status!==401)&&!error.message.includes('private contents'));}});

test('changed annual hash after partial progress skips processed events without rereading their notes',async()=>{
  const f=await fixture(Array.from({length:5},(_,i)=>[dayAt(i+1),'행사 '+i]));
  await f.primeMeals();
  await f.step();await f.step();
  const firstTwo=new Set(Object.values(f.state.records).map(record=>record.page_id));
  for(let i=2;i<5;i++) {
    f.snapshot.calendar.rows[4].description='계속 변경되는 말미 행사 '+i;
    f.notion.calls=[];
    assert.equal((await f.step()).done,false);
    assert.equal(Object.keys(f.state.records).length,i+1);
    assert.equal(f.state.cycle.cursor,i+1);
    assert.equal(f.notion.calls.filter(([method,path])=>method==='GET'&&path.includes('/children?')&&firstTwo.has(path.split('/')[2])).length,0);
    assert.equal(f.notion.calls.filter(([method,path])=>method==='PATCH'&&path==='/blocks/'+manifest.meals_block_id).length,0);
  }
  assert.equal((await f.step()).done,true);
});

test('fast skip also requires live page properties and stable page ID',async()=>{
  const f=await fixture([[dayAt(1),'앞 행사'],[dayAt(2),'뒤 행사']]);await f.finish();
  const first=f.snapshot.calendar.rows[0],firstId=f.state.records[first.external_id].page_id;
  f.notion.objects.get('/pages/'+firstId).properties.일정.date.start=dayAt(0);
  f.snapshot.calendar.rows[1].description='나중 행사의 변경';f.notion.calls=[];
  assert.equal((await f.step()).done,false);
  assert.equal(f.notion.objects.get('/pages/'+firstId).properties.일정.date.start,first.date);
  assert.ok(f.notion.calls.some(([method,path])=>method==='GET'&&path.startsWith('/blocks/'+firstId+'/children')));
  assert.equal(f.state.cycle.cursor,1);
});

test('unchanged-record fast scan is capped and checkpoints its cursor',async()=>{
  const f=await fixture(Array.from({length:102},(_,i)=>[dayAt(1),'행사 '+String(i).padStart(3,'0')]));
  await f.finish();f.snapshot.calendar.rows[101].description='마지막 행사 수정';f.notion.calls=[];
  assert.equal((await f.step()).done,false);
  assert.equal(f.state.cycle.cursor,LIMITS.skipRecords);
  assert.equal(f.notion.saved.cycle.cursor,LIMITS.skipRecords);
  assert.equal(f.notion.calls.filter(([method,path])=>method==='GET'&&path.includes('/children?')).length,0);
  assert.equal(f.notion.mutations().length,0);
  assert.equal((await f.step()).done,false);
  assert.equal(f.state.cycle.cursor,102);
  assert.equal((await f.step()).done,true);
});

test('meal is written once per day while calendar progress can continue for many steps',async()=>{
  const f=await fixture([[dayAt(1)],[dayAt(3)]]);await f.primeMeals();
  const displayed=clone(f.notion.objects.get('/blocks/'+manifest.meals_block_id));
  const checked=f.state.meals_checked_at;
  f.snapshot.meals.rows[0].menu='다른 캐시 내용';f.snapshot.meals.fetched_at=new Date(Date.now()+1000).toISOString();
  await f.finish();
  assert.deepEqual(f.notion.objects.get('/blocks/'+manifest.meals_block_id),displayed);
  assert.equal(f.state.meals_checked_at,checked);
});

test('next-day meals publish even when the previous annual pass is still incomplete',async t=>{
  const f=await fixture([[dayAt(1)],[dayAt(3)],[dayAt(5)]]);await f.primeMeals();await f.step();
  const nextDay=day===end?dayAt(20):new Date(Date.parse(day+'T00:00:00Z')+86400000).toISOString().slice(0,10);
  t.mock.method(Date,'now',()=>Date.parse(nextDay+'T12:00:00+09:00'));
  f.snapshot.meals.date=nextDay;f.snapshot.meals.fetched_at=nextDay+'T03:00:00Z';f.snapshot.meals.rows[0].menu='다음 날 급식';
  f.notion.calls=[];
  assert.equal((await syncStep({...f,day:nextDay})).done,false);
  assert.equal(f.state.meals_date,nextDay);assert.equal(Object.keys(f.state.records).length,1);
  assert.equal(f.state.completed_at,undefined);
  assert.match(plainText(f.notion.objects.get('/blocks/'+manifest.meals_block_id)),/다음 날 급식/);
  assert.equal(f.notion.calls.filter(([,path])=>path.endsWith('/query')).length,0);
});

test('lost meal response reconciles the known block before calendar mutations',async()=>{
  const f=await fixture([[dayAt(1)]]);
  f.notion.loss=(method,path)=>method==='PATCH'&&path==='/blocks/'+manifest.meals_block_id;
  await assert.rejects(f.step(),/lost/);
  assert.equal(f.state.meals_date,undefined);assert.ok(f.state.mutation);
  assert.equal((await f.step()).done,false);
  assert.equal(f.state.meals_date,day);assert.equal(f.state.mutation,undefined);
  assert.equal(f.state.records,undefined);
  assert.equal(f.notion.calls.filter(([method,path])=>method==='PATCH'&&path==='/blocks/'+manifest.meals_block_id).length,1);
});

test('pending page creation is recovered before the next day meal write',async t=>{
  const f=await fixture([[dayAt(1)]]);await f.primeMeals();
  f.notion.loss=(method,path)=>method==='POST'&&path==='/pages';
  await assert.rejects(f.step(),/lost/);
  const nextDay=day===end?dayAt(20):new Date(Date.parse(day+'T00:00:00Z')+86400000).toISOString().slice(0,10);
  t.mock.method(Date,'now',()=>Date.parse(nextDay+'T12:00:00+09:00'));
  f.snapshot.meals.date=nextDay;f.snapshot.meals.fetched_at=nextDay+'T03:00:00Z';f.notion.calls=[];
  assert.equal((await syncStep({...f,day:nextDay})).done,false);
  assert.equal(f.state.pending,undefined);assert.equal(f.state.meals_date,day);
  assert.equal(f.notion.mutations().length,0);
  assert.equal((await syncStep({...f,day:nextDay})).done,false);
  assert.equal(f.state.meals_date,nextDay);
  assert.equal((await syncStep({...f,day:nextDay})).done,true);
});
