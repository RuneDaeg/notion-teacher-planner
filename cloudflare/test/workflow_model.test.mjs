import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {createWorkflowSelection, normalizeWorkflowSelection, validateWorkflowSelection,
  requiredWorkflowModules, applyWorkflowsToSetupAnswers, workflowSummary} from '../../cloud/web/workflow-model.mjs';
import {createAnswers, buildPrompt, summarizeAnswers, validateStep} from '../../cloud/web/setup-model.mjs';
import {applyFeature, buildEditPrompt} from '../../cloud/web/edit-model.mjs';
import {createSelection, selectionFromSetupAnswers, buildReviewedBundle, verifyReviewedBundle,
  buildPreviewPrompt, resolvePreview, canonicalJSON} from '../../cloud/web/preview-model.mjs';
const catalog = JSON.parse(await readFile(new URL('../../cloud/web/workflow-catalog.json', import.meta.url), 'utf8'));
const preview = JSON.parse(await readFile(new URL('../../cloud/web/preview-catalog.json', import.meta.url), 'utf8'));
const root = fileURLToPath(new URL('../../', import.meta.url));
const dataFrom = text => JSON.parse(text.match(/```json\n([\s\S]*)\n```$/)[1]);
const complete = () => ({...createAnswers(),region:'충남',schoolName:'가상고',schoolLevel:'고등학교',academicYear:'2027',subjects:'과학',classes:'1학년 1반',homeroom:'no',notionLocation:'help',sharing:'private',moduleChoice:'none',formChoice:'none',timetable:'empty',neis:'later',demo:'empty'});
const rehash = value => {const {bundle_digest, ...body} = value; return {...body,bundle_digest:createHash('sha256').update(canonicalJSON(body)).digest('hex')};};

test('three presets select exact features and transparent module dependencies', () => {
  const basic = createWorkflowSelection(catalog,'basic');
  const recommended = createWorkflowSelection(catalog,'recommended');
  assert.equal(basic.features.length,3);
  assert.deepEqual(requiredWorkflowModules(catalog,basic),[]);
  assert.equal(recommended.features.length,7);
  assert.deepEqual(requiredWorkflowModules(catalog,recommended),['assessment','attendance','contact','meeting']);
  assert.deepEqual(createWorkflowSelection(catalog,'custom'),{preset:'custom',features:[]});
  assert.throws(()=>createWorkflowSelection(catalog,'basic',['student_history']),/커스텀/);
  assert.throws(()=>normalizeWorkflowSelection(catalog,{preset:'custom',features:['student_history'],token:'hidden'}));
});

test('dependencies are required only for selected workflows; optional branches do not enable modules', () => {
  for (const key of ['student_history','class_hub','lesson_links','followups','para_hub']) {
    assert.deepEqual(validateWorkflowSelection(catalog,createWorkflowSelection(catalog,'custom',[key]),[]),[]);
  }
  for (const [key,module] of [['submissions','attendance'],['assessment_scope','assessment'],['assessment_results','assessment'],['staff_links','staff']]) {
    const choice=createWorkflowSelection(catalog,'custom',[key]);
    assert.equal(validateWorkflowSelection(catalog,choice,[])[0].field,'workflows');
    assert.deepEqual(validateWorkflowSelection(catalog,choice,[module]),[]);
  }
  for (const choice of [{preset:'custom',features:['bad']},{preset:'custom',features:['class_hub','class_hub']},{preset:'recommended',features:[]},null]) {
    assert.equal(validateWorkflowSelection(catalog,choice).length,1);
  }
});

test('explicit choice preserves independent answers, includes dependencies, and travels to installation JSON', () => {
  const original=complete(), choice=createWorkflowSelection(catalog,'recommended');
  const answers=applyWorkflowsToSetupAnswers(catalog,original,choice);
  assert.deepEqual(applyWorkflowsToSetupAnswers(catalog, {...original, modules:['staff']}, createWorkflowSelection(catalog,'basic')).modules, []);
  assert.equal(original.workflows,null);
  assert.deepEqual(original.modules,[]);
  assert.equal(answers.moduleChoice,'select');
  assert.equal(answers.schoolName,original.schoolName);
  assert.deepEqual(validateStep(answers,2,catalog),[]);
  const prompt=buildPrompt(answers,catalog), data=dataFrom(prompt);
  assert.deepEqual(data.Q05W,choice);
  assert.deepEqual(summarizeAnswers(answers,catalog).Q05W,choice);
  assert.match(prompt,/workflow-plan --config/);
  assert.match(prompt,/현재 페이지 관계로 필터링/);
  assert.match(prompt,/실제 Notion 관계·보기·템플릿이 설치되거나 검증된 것은 아닙니다/);
  assert.throws(()=>buildPrompt({...answers,modules:[]},catalog));
});

test('legacy answers remain unselected and unknown fields do not cross prompt boundaries', () => {
  const answers=complete();delete answers.workflows;
  assert.equal(dataFrom(buildPrompt({...answers,workflowSecret:'never-copy'})).Q05W,undefined);
  assert.equal(selectionFromSetupAnswers(answers).workflows,undefined);
  const draft=applyFeature('counseling-history');
  assert.equal(dataFrom(buildEditPrompt(draft)).workflows,undefined);
  assert.ok(!buildPrompt({...answers,workflowSecret:'never-copy'}).includes('never-copy'));
});

test('an existing notebook can receive workflows alone without an unrelated feature', () => {
  const choice=createWorkflowSelection(catalog,'custom',['assessment_results','staff_links']);
  const prompt=buildEditPrompt({...applyFeature('workflows'),workflows:choice},catalog);
  const data=dataFrom(prompt);
  assert.equal(data.feature_id,'workflows');
  assert.deepEqual(data.workflows,choice);
  assert.match(prompt,/새 설치가 아닙니다/);
  assert.match(prompt,/workflow-plan --config \.local\/workflow-change-config\.json/);
  assert.match(prompt,/원래 config와 설치 state는 보존/);
  assert.match(prompt,/install을 다시 실행하거나 원래 설치 상태를 교체하지/);
  assert.match(prompt,/선택하지 않은 연결·추가 DB·집계는 새로 만들지/);
  assert.throws(()=>buildEditPrompt(applyFeature('workflows'),catalog));
});

test('reviewed v4 binds workflow selection and catalog descriptions, without changing physical layout', async () => {
  const choice=createWorkflowSelection(catalog,'recommended');
  const selection=createSelection({modules:requiredWorkflowModules(catalog,choice),workflows:choice},catalog);
  const bundle=await buildReviewedBundle(preview,selection);
  assert.equal(bundle.version,4);
  assert.deepEqual(bundle.workflow_databases.map(item => item.key),['submission_items']);
  assert.equal(preview.workflow_schema_digest.length,64);
  assert.deepEqual(bundle.workflow_summary,workflowSummary(catalog,choice));
  const plain=createSelection({modules:selection.modules});
  assert.deepEqual(bundle.pages,resolvePreview(preview,plain).pages);
  assert.deepEqual(await verifyReviewedBundle(preview,bundle),{valid:true,errors:[]});
  assert.ok(buildPreviewPrompt(bundle,catalog).includes('assessment_scope'));
  assert.equal((await verifyReviewedBundle(preview,rehash({...bundle,version:3}))).valid,false);
  const tampered=structuredClone(bundle);tampered.workflow_summary.features[0].description='changed';
  assert.equal((await verifyReviewedBundle(preview,rehash(tampered))).valid,false);
  const sourceChanged=structuredClone(bundle);sourceChanged.workflow_databases[0].properties['학급'].target='staff';
  assert.equal((await verifyReviewedBundle(preview,rehash(sourceChanged))).valid,false);
  const choiceChanged=structuredClone(bundle);choiceChanged.selection.workflows.features.pop();
  assert.equal((await verifyReviewedBundle(preview,rehash(choiceChanged))).valid,false);
});

test('all nine workflow selectors have identical Python and browser canonical bundles', async () => {
  const choices=['basic','recommended'].map(preset=>createWorkflowSelection(catalog,preset));
  choices.push(createWorkflowSelection(catalog,'custom'));
  for (const feature of catalog.features) choices.push(createWorkflowSelection(catalog,'custom',[feature.key]));
  choices.push(createWorkflowSelection(catalog,'custom',catalog.features.map(item=>item.key)));
  const selections=choices.map(workflows=>createSelection({modules:requiredWorkflowModules(catalog,workflows),workflows},catalog));
  const expected=JSON.parse(execFileSync(process.env.PYTHON??'python3',['-c','import json,sys\nfrom teacher_planner.preview_bundle import create_bundle\nprint(json.dumps([create_bundle(s) for s in json.load(sys.stdin)]))'],{cwd:root,input:JSON.stringify(selections),encoding:'utf8',maxBuffer:5*1024*1024}));
  const actual=await Promise.all(selections.map(selection=>buildReviewedBundle(preview,selection)));
  assert.deepEqual(actual,expected);
});
