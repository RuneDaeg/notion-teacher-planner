#!/usr/bin/env node
/**
 * Maintainer-only preview renderer, separate from the Python installer runtime.
 * Requires Node.js + @napi-rs/canvas and a Korean system font.
 * Run: PYTHON=python3 node scripts/render_template_preview.mjs [output.png]
 * Optional: CANVAS_MODULE=/absolute/path/to/@napi-rs/canvas
 * It reads our public manifest/block builders only; no browser or remote assets.
 */
import {createRequire} from 'node:module';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {dirname, resolve} from 'node:path';
import {mkdirSync, writeFileSync} from 'node:fs';

const require = createRequire(import.meta.url);
const {createCanvas} = require(process.env.CANVAS_MODULE || '@napi-rs/canvas');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source = String.raw`
import json
from pathlib import Path
from teacher_planner.dashboard import layout_spec, top_columns, quick_links, middle_columns, middle_content, matrix_table, matrix_title
from teacher_planner.model import blueprint
c = json.loads(Path('config.example.json').read_text(encoding='utf-8'))
keys = ['timetable','lessons','todo','archive','resources','semester_1','semester_2','resource_archive','students','counseling','contacts','staff','accounts','meetings','agenda','areas','projects']
links = {key: 'https://example.invalid/' + key for key in keys}
rows = [
    {'date':'2026-09-28','period':1,'subject':'영어','class_name':'1학년 1반','status':'예정'},
    {'date':'2026-09-29','period':3,'subject':'영어','class_name':'1학년 2반','status':'예정'},
    {'date':'2026-10-01','period':2,'subject':'영어','class_name':'2학년 1반','status':'예정'},
]
print(json.dumps({'spec':layout_spec(),'blueprint':blueprint(),'top':top_columns(c,links),'quick':quick_links(c,links),'middle':middle_columns(c),'middle_content':middle_content(c,links),'matrix':matrix_table(c,rows,'2026-09-28'),'caption':matrix_title('2026-09-28')},ensure_ascii=False))
`;
const result = spawnSync(process.env.PYTHON || 'python3', ['-c', source], {
  cwd: root, encoding: 'utf8', maxBuffer: 1024 * 1024,
});
if (result.status !== 0) throw new Error(result.stderr || 'Could not export public dashboard blocks.');
const data = JSON.parse(result.stdout);

const W = 1600, H = 2200, M = 80, GAP = 32, FULL = W - 2 * M;
const canvas = createCanvas(W, H), ctx = canvas.getContext('2d');
const ink = '#37352f', muted = '#8b8880', line = '#e9e7e3';
const colors = {
  default:'#ffffff', gray_background:'#f0efed', brown_background:'#eee5df',
  orange_background:'#fae6d4', yellow_background:'#fbf1cd', green_background:'#e1eddf',
  blue_background:'#dfebf5', purple_background:'#e9e0f1', pink_background:'#f4e0e9', red_background:'#f7e0df',
};
const FONT = '"Apple SD Gothic Neo", "Noto Sans KR", sans-serif';
ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, W, H);

function font(size=18, bold=false) { ctx.font = `${bold ? 700 : 400} ${size}px ${FONT}`; }
function text(s,x,y,size=18,color=ink,bold=false) {
  font(size,bold); ctx.fillStyle=color; ctx.textBaseline='top'; ctx.fillText(String(s),x,y);
}
function richContent(items=[]) { return items.map(x=>x.text?.content || '').join(''); }
function blockContent(block) { return richContent(block[block.type]?.rich_text); }
function wrap(s,width,size=18) {
  font(size); const lines=[];
  for (const paragraph of String(s).split('\n')) {
    let current='';
    for (const ch of paragraph) {
      if (current && ctx.measureText(current+ch).width > width) {lines.push(current); current=ch;} else current+=ch;
    }
    lines.push(current);
  }
  return lines;
}
function wrapped(s,x,y,width,size=18,color=ink,lineHeight=25,bold=false) {
  const lines=wrap(s,width,size); lines.forEach((line,i)=>text(line,x,y+i*lineHeight,size,color,bold));
  return y+lines.length*lineHeight;
}
function rule(x,y,width) {ctx.strokeStyle=line;ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x,y+.5);ctx.lineTo(x+width,y+.5);ctx.stroke();}
function bar(title,color,x,y,width,height=39) {
  ctx.fillStyle=colors[color] || '#ffffff';ctx.fillRect(x,y,width,height);
  text(title,x+12,y+9,23,ink,true);return y+height;
}
function section(key,y) {const s=data.spec.sections[key];return bar(s.title,s.color,M,y,FULL);}

function blocks(items,x,y,width,size=17,compact=false) {
  for (const block of items) {
    if (block.type==='divider') {rule(x,y+7,width);y+=compact?17:21;continue;}
    const body=block[block.type], label=blockContent(block);
    if (/^heading/.test(block.type)) {text(label,x,y+3,21,ink,true);y+=compact?32:36;continue;}
    const hasLink=body.rich_text?.some(t=>t.text?.link);
    const color=body.color==='gray' || label.includes('링크 추가') ? muted : ink;
    if (hasLink) text('↗',x,y+1,size,muted);
    y=wrapped(label,x+(hasLink?23:0),y,width-(hasLink?23:0),size,color,compact?22:24)+(compact?5:7);
  }
  return y;
}

function matrix(block,x,y,width) {
  const rows=block.table.children.map(r=>r.table_row.cells);
  const widths=[74,100,...Array(5).fill((width-174)/5)];
  let cy=y;
  rows.forEach((row,ri)=>{
    const height=ri===0?45:41;
    let cx=x;
    row.forEach((items,ci)=>{
      const background=ri===0?'gray_background':items.find(i=>i.annotations?.color?.endsWith('_background'))?.annotations.color;
      ctx.fillStyle=background ? colors[background] : '#ffffff';ctx.fillRect(cx,cy,widths[ci],height);
      ctx.strokeStyle=line;ctx.lineWidth=1;ctx.strokeRect(cx+.5,cy+.5,widths[ci],height);
      const label=richContent(items);
      const lines=wrap(label,widths[ci]-12,ri===0?15:15);
      const lineHeight=18,total=lines.length*lineHeight;
      lines.forEach((s,i)=>{
        font(ri===0?15:15,ri===0 || ci===0);
        const tw=ctx.measureText(s).width;
        text(s,cx+(widths[ci]-tw)/2,cy+(height-total)/2+i*lineHeight,15,ci===1?muted:ink,ri===0 || ci===0);
      });
      cx+=widths[ci];
    });cy+=height;
  });return cy;
}

const view = key => data.blueprint.views.find(v=>v.key===key);
const database = key => data.blueprint.databases.find(d=>d.key===key);
function emptyTable(key,x,y,width,height=67,caption=null) {
  const v=view(key), definition=database(v.source);
  text('▤  '+(caption || (key.startsWith('active_') ? definition.title : v.name)),x,y,17,ink,true);
  const top=y+27;
  const headers=(v.show || Object.keys(definition.properties).filter(n=>!['학년도','보관','외부 ID'].includes(n))).slice(0,6);
  const proportions=headers.map((_,i)=>i===0 ? .32 : .68/(headers.length-1));
  let cx=x;
  headers.forEach((name,i)=>{
    const w=width*proportions[i];
    ctx.fillStyle='#fbfaf9';ctx.fillRect(cx,top,w,29);
    ctx.strokeStyle=line;ctx.strokeRect(cx+.5,top+.5,w,29);
    text(name,cx+11,top+7,14,muted);cx+=w;
  });
  text('+ 새 항목',x+11,top+37,14,'#b1aea7');
  rule(x,top+60,width);return y+height+22;
}

function compactCalendar(key,x,y,width) {
  const v=view(key);
  ctx.fillStyle='#ffffff';ctx.fillRect(x,y,width,66);ctx.strokeStyle=line;ctx.strokeRect(x+.5,y+.5,width,66);
  text('▦  '+v.name,x+14,y+10,17,ink,true);
  const labels=key==='monthly'?['월간 일정 · 날짜가 있는 업무와 수업을 함께 확인']:['월','화','수','목','금'];
  if(labels.length===1) text(labels[0],x+14,y+38,14,muted);
  else {
    labels.forEach((label,i)=>{const sx=x+14+i*(width-28)/5;text(label,sx,y+38,14,muted);});
  }
}

// This is deliberately labelled as an illustration, never a live Notion capture.
text('기본 템플릿 미리보기 · 예시',M,53,43,ink,true);
text(data.spec.name+'  /  '+data.spec.id+' v'+data.spec.version,M,106,17,muted);
text('가상 수업 3건으로 구성한 예시이며, 실제 Notion 화면 캡처가 아닙니다.',M,133,15,muted);

const topY=179, leftW=(FULL-GAP)*data.spec.top[0].width_ratio, rightX=M+leftW+GAP, rightW=FULL-leftW-GAP;
data.spec.top.forEach((s,i)=>bar(s.title,s.color,i?rightX:M,topY,i?rightW:leftW));
text(blockContent(data.caption),M,229,15,muted);
matrix(data.matrix,M,257,leftW);
blocks(data.quick,rightX+12,235,rightW-24,17);

let y=668;
section('things',y);emptyTable('inbox',M,y+53,FULL);emptyTable('todo',M,y+145,FULL);
y=919;section('meetings',y);emptyTable('active_meetings',M,y+51,FULL);

y=1080;
const middleWidth=FULL-2*GAP;
let x=M;
data.spec.middle.forEach((s,i)=>{
  const width=middleWidth*s.width_ratio;
  bar(s.title,s.color,x,y,width);
  blocks(data.middle_content[i],x+8,y+57,width-16,i===1?18:16,i!==1);
  x+=width+GAP;
});

y=1507;section('students',y);emptyTable('active_students',M,y+52,FULL);
y=1664;section('schedule',y);
['weekly','monthly','teacher_week'].forEach((key,i)=>compactCalendar(key,M,y+52+i*78,FULL));

y=1964;section('archive',y);
emptyTable('archive_agenda',M,y+52,FULL,67,'보관한 업무·일정');
text('▶  표별 보관함',M+10,y+155,17,ink);
rule(M,2150,FULL);
text('입력 전의 빈 표와 가상 수업을 사용했습니다. 실제 반·교과·교시 시각과 즐겨찾기는 사용자 설정에 따라 달라집니다.',M,2169,13,muted);

const output=resolve(process.argv[2] || resolve(root,'docs/template-preview.png'));
mkdirSync(dirname(output),{recursive:true});writeFileSync(output,canvas.toBuffer('image/png'));
console.log(output);
