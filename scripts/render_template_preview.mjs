#!/usr/bin/env node
/**
 * Maintainer-only authored previews of four native Notion pages. No browser,
 * remote assets, screenshots, student data, or production dependency is used.
 * PYTHON=python3 node scripts/render_template_preview.mjs [home-output.png]
 * Requires @napi-rs/canvas + a Korean system font. CANVAS_MODULE may point to an
 * installed module. Other page PNGs are saved beside home-output.png.
 */
import {createRequire} from 'node:module';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {dirname, resolve} from 'node:path';
import {existsSync, mkdirSync, writeFileSync} from 'node:fs';

const require=createRequire(import.meta.url);
const {createCanvas,GlobalFonts}=require(process.env.CANVAS_MODULE || '@napi-rs/canvas');
const emojiFont='/System/Library/Fonts/Apple Color Emoji.ttc';
if(existsSync(emojiFont)) GlobalFonts.registerFromPath(emojiFont,'Apple Color Emoji');
const root=resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source=String.raw`
import json
from pathlib import Path
from teacher_planner.dashboard import layout_spec, matrix_table, quick_links
from teacher_planner.model import blueprint
c=json.loads(Path('config.example.json').read_text(encoding='utf-8'))
keys=['timetable','lessons','todo','archive','resources','students','counseling','contacts','staff','accounts','meetings','agenda','areas','projects']
links={key:'https://example.invalid/'+key for key in keys}
print(json.dumps({'spec':layout_spec(),'blueprint':blueprint(),'config':c,'matrix':matrix_table(c),'quick':quick_links(c,links)},ensure_ascii=False))
`;
const result=spawnSync(process.env.PYTHON || 'python3',['-c',source],{cwd:root,encoding:'utf8',maxBuffer:2*1024*1024});
if(result.status!==0) throw new Error(result.stderr || 'Could not export public template definitions.');
const data=JSON.parse(result.stdout),pages=data.spec.workspace?.pages;
if(!Array.isArray(pages) || pages.length!==4) throw new Error('Four workspace pages are required.');
const views=new Map(data.blueprint.views.map(v=>[v.key,v]));
const dbs=new Map(data.blueprint.databases.map(d=>[d.key,d]));
for(const page of pages) for(const section of page.sections) for(const key of section.views) {
  if(!views.has(key)) throw new Error('Preview references a missing view: '+key);
}

const W=1600,M=90,FULL=W-2*M,GAP=22;
const FONT='"Apple SD Gothic Neo", "Noto Sans KR", "Apple Color Emoji", sans-serif';
const T={canvas:'#FAFAF9',sidebar:'#F7F6F5',border:'#E9E9E7',text:'#37352F',muted:'#787774',white:'#FFFFFF'};
const COLOR={default:T.white,gray_background:T.sidebar,blue_background:'#E8F3FF',purple_background:'#F5EEFD',
  red_background:'#FFEEEE',green_background:'#EEF6EE',yellow_background:'#FFF8E4',orange_background:'#FFF1E6',
  pink_background:'#F9EDF2',brown_background:'#F3EEEA'};
const richText=values=>(values || []).map(v=>v.text?.content || '').join('');

class Preview {
  constructor(page) {
    this.page=page;this.canvas=createCanvas(W,4000);this.ctx=this.canvas.getContext('2d');this.y=0;
    this.ctx.fillStyle=T.canvas;this.ctx.fillRect(0,0,W,4000);
  }
  font(size=18,bold=false) {this.ctx.font=`${bold?700:400} ${size}px ${FONT}`;}
  text(value,x,y,size=18,color=T.text,bold=false) {
    this.font(size,bold);this.ctx.fillStyle=color;this.ctx.textBaseline='top';this.ctx.fillText(String(value),x,y);
  }
  width(value,size=18,bold=false) {this.font(size,bold);return this.ctx.measureText(value).width;}
  wrap(value,width,size=18) {
    this.font(size);const result=[];
    for(const paragraph of String(value).split('\n')) {
      let line='';for(const char of paragraph) {
        if(line && this.ctx.measureText(line+char).width>width) {result.push(line);line=char;} else line+=char;
      }result.push(line);
    }return result;
  }
  paragraph(value,x,y,width,size=18,color=T.text,lineHeight=26,bold=false) {
    const lines=this.wrap(value,width,size);lines.forEach((line,i)=>this.text(line,x,y+i*lineHeight,size,color,bold));
    return y+lines.length*lineHeight;
  }
  rect(x,y,w,h,color=T.white,radius=7,stroke=true) {
    const c=this.ctx;c.beginPath();c.roundRect(x,y,w,h,radius);c.fillStyle=color;c.fill();
    if(stroke) {c.strokeStyle=T.border;c.lineWidth=1;c.stroke();}
  }
  rule(x,y,w) {const c=this.ctx;c.strokeStyle=T.border;c.lineWidth=1;c.beginPath();c.moveTo(x,y+.5);c.lineTo(x+w,y+.5);c.stroke();}
  heading(section) {
    const color=COLOR[section.color] || T.sidebar;
    this.rect(M,this.y,FULL,37,color,3,false);
    this.text(section.title,M+12,this.y+6,25,T.text,true);this.y+=51;
  }
  header() {
    this.ctx.fillStyle=T.sidebar;this.ctx.fillRect(0,0,W,58);
    this.text('교무수첩  /  '+this.page.title,36,19,17,T.muted);
    this.text('Notion 구성 미리보기 · 예시',W-332,19,15,T.muted);
    this.text(this.page.icon+'  '+this.page.title,M,96,43,T.text,true);
    this.text(this.page.description,M,151,20,T.muted);
    this.text('기본 템플릿의 빈 상태를 표현한 예시입니다. 실제 Notion 화면 캡처가 아닙니다.',M,185,15,T.muted);
    let x=M;
    pages.forEach((page,i)=>{
      const active=page.key===this.page.key,label=page.icon+' '+page.title;
      if(i) {this.text('/',x,232,17,T.muted);x+=24;}
      this.text(label,x,230,17,T.text,active);
      const width=this.width(label,17,active);this.rule(x,254,width);x+=width+22;
    });
    const c=data.config;
    this.text(`${c.academic_year}학년도 ${c.semester || 1}학기 · ${c.teacher} · ${c.subjects.join(' / ')}`,M,279,17,T.muted);
    this.y=329;
  }
  callouts(items,height=91) {
    const width=(FULL-(items.length-1)*GAP)/items.length;
    items.forEach((item,i)=>{
      const x=M+i*(width+GAP);this.rect(x,this.y,width,height,COLOR[item.color] || T.sidebar);
      this.text(item.title,x+18,this.y+17,20,T.text,true);
      if(item.body) this.paragraph(item.body,x+18,this.y+48,width-36,16,T.muted,23);
    });this.y+=height;
  }
  tabs(variants,x,y,width) {
    let cursor=x+16;
    variants.forEach((variant,i)=>{
      const label=(variant.type==='calendar'?'▦  ':variant.type==='board'?'▥  ':variant.type==='gallery'?'▧  ':'▤  ')+variant.name;
      const size=16,tabWidth=this.width(label,size,i===0)+28;
      if(cursor+tabWidth>x+width-15) throw new Error('Preview tabs overflow: '+variant.key);
      this.text(label,cursor,y+14,size,i===0?T.text:T.muted,i===0);
      if(i===0) {this.ctx.fillStyle=T.text;this.ctx.fillRect(cursor,y+41,tabWidth-18,2);}
      cursor+=tabWidth;
    });this.rule(x,y+44,width);
  }
  linkedViews(section) {
    const variants=section.views.map(key=>views.get(key));
    if(!variants.length) return;
    if(variants.some(v=>v.source!==variants[0].source)) throw new Error('Tabbed views must share one data source.');
    const selected=variants[0],definition=dbs.get(selected.source),y=this.y,type=selected.type;
    const height=type==='calendar'?206:type==='board'?192:155;
    this.rect(M,y,FULL,height);this.tabs(variants,M,y,FULL);
    if(type==='calendar') {
      const cw=FULL/7;
      ['월','화','수','목','금','토','일'].forEach((day,i)=>{
        this.text(day,M+i*cw+16,y+64,16,T.muted);
        if(i) {const c=this.ctx;c.strokeStyle=T.border;c.beginPath();c.moveTo(M+i*cw,y+55);c.lineTo(M+i*cw,y+height);c.stroke();}
      });this.rule(M,y+91,FULL);
    } else if(type==='board') {
      const property=definition.properties[selected.group];
      let groups=property?.options || (selected.group==='학급'?data.config.classes.map(c=>c.name):['미지정']);
      groups=groups.slice(0,4);const width=(FULL-40-(groups.length-1)*18)/groups.length;
      groups.forEach((group,i)=>{
        const x=M+20+i*(width+18);this.rect(x,y+61,width,112,T.sidebar,5,false);
        this.text(group,x+13,y+76,17,T.text,true);this.text('+ 새 항목',x+13,y+128,15,T.muted);
      });
    } else {
      let headers=selected.show || Object.keys(definition.properties).filter(k=>!['학년도','보관','외부 ID'].includes(k));
      const hasMoreColumns=headers.length>6;
      headers=headers.slice(0,6);let x=M;
      headers.forEach((name,i)=>{
        const width=FULL*(i===0?.30:.70/Math.max(1,headers.length-1));
        this.ctx.fillStyle=T.sidebar;this.ctx.fillRect(x,y+45,width,36);
        this.text(name,x+14,y+57,15,T.muted);this.rule(x,y+81,width);
        if(i) {const c=this.ctx;c.strokeStyle=T.border;c.beginPath();c.moveTo(x,y+45);c.lineTo(x,y+height);c.stroke();}x+=width;
      });this.text('+ 새 항목',M+15,y+104,16,T.muted);
      if(hasMoreColumns) {
        const hint='추가 속성은 가로 스크롤로 확인';
        this.text(hint,M+FULL-this.width(hint,13)-17,y+126,13,T.muted);
      }
    }this.y+=height;
  }
  matrix() {
    const left=(FULL-GAP)*.625,right=FULL-left-GAP,rightX=M+left+GAP;
    const cells=data.matrix.table.children.map(r=>r.table_row.cells);
    const widths=[68,98,...Array(5).fill((left-166)/5)];let y=this.y;
    this.rect(M,y,left,40,COLOR.blue_background);this.text('수업 시간표',M+15,y+11,21,T.text,true);y+=53;
    for(let ri=0;ri<cells.length;ri++) {
      let x=M;const h=ri?41:40;
      cells[ri].forEach((values,ci)=>{
        this.rect(x,y,widths[ci],h,ri?T.white:T.sidebar,0);
        const label=richText(values),lines=this.wrap(label,widths[ci]-10,15);
        lines.forEach((line,i)=>this.text(line,x+(widths[ci]-this.width(line,15))/2,y+11+i*17,15,ci===1?T.muted:T.text,ri===0||ci===0));x+=widths[ci];
      });y+=h;
    }
    this.rect(rightX,this.y,right,40,COLOR.purple_background);this.text('빠른 동작 / 즐겨찾기',rightX+15,this.y+11,21,T.text,true);
    let quickY=this.y+57;
    for(const block of data.quick) {
      if(block.type==='divider') {this.rule(rightX,quickY+3,right);quickY+=19;continue;}
      const label=richText(block[block.type]?.rich_text);
      if(block.type.startsWith('heading')) {this.text(label,rightX+11,quickY,19,T.text,true);quickY+=32;continue;}
      const hasLink=block[block.type].rich_text.some(r=>r.text?.link);
      quickY=this.paragraph((hasLink?'↗  ':'')+label,rightX+11,quickY,right-22,16,hasLink?T.text:T.muted,23)+6;
    }
    this.y=Math.max(y,quickY)+18;
    this.rect(M,this.y,FULL,63,COLOR.blue_background);
    this.paragraph('컴시간에서 가져온 수업은 아래 기록의 동기화 시각에서 확인합니다. 변경·휴강은 시간표 기록의 탭에서 확인하세요.',M+18,this.y+19,FULL-36,17,T.text,24);this.y+=63;
  }
  staticSection(section) {
    if(section.kind==='briefing') {
      this.rect(M,this.y,FULL,84,T.sidebar);
      this.text('오늘 전달할 내용, 회의 준비, 확인할 일을 여기에 적으세요.',M+20,this.y+28,20,T.text);this.y+=84;
    } else if(section.kind==='quick') {
      this.callouts([{title:'✍️ 상담 기록 열기'},{title:'📝 조회·종례 메모'},
        {title:data.config.modules.attendance?'👥 출결 확인':'👥 학생 명부'},
        {title:data.config.modules.assessment?'📋 평가·채점':'📋 수업 진도'}],65);
    } else if(section.kind==='notes') {
      this.callouts([{title:'조회 메모',body:'날짜와 전달 내용을 적으세요.'},
        {title:'종례 메모',body:'날짜와 확인할 내용을 적으세요.'},
        {title:'후속 관찰',body:'학생별 기록은 상담 DB에 연결하세요.',color:'yellow_background'}],106);
    } else if(section.kind==='school') {
      for(const label of ['교직원 연락망','학교 계정 관리','회의록']) {this.text('↗  '+label,M+9,this.y,18,T.text);this.y+=32;}
      for(const label of ['NEIS 나이스','K-에듀파인','공유 페이지','설문·제출 안내']) {
        this.text(label+' · 내 링크 설정',M+9,this.y,17,T.muted);this.y+=29;
      }
    } else if(section.kind==='para') {
      this.callouts([{title:'Projects',body:'기한과 완료 기준이 있는 일',color:'purple_background'},
        {title:'Areas',body:'꾸준히 관리할 담당 영역',color:'green_background'},
        {title:'Resources',body:'다시 꺼내 쓸 수업·업무 자료',color:'blue_background'},
        {title:'Archives',body:'보관한 업무와 자료',color:'gray_background'}],97);
    } else if(section.kind==='archives') {
      this.rect(M,this.y,FULL,61,T.white);this.text('▶  표별 보관함 열기',M+18,this.y+20,19,T.text);this.y+=61;
    } else if(section.kind==='matrix') this.matrix();
    else throw new Error('Unknown preview section kind: '+section.kind);
  }
  draw() {
    this.header();
    for(const section of this.page.sections) {
      this.heading(section);
      if(section.kind==='views') this.linkedViews(section);else this.staticSection(section);
      this.y+=34;
    }
    this.rule(M,this.y,FULL);
    this.text('빈 표·직접 작성 메모의 구성 예시입니다. 실제 학생 정보, 수업 기록, 계정 정보는 포함하지 않았습니다.',M,this.y+24,15,T.muted);
    const height=Math.ceil(this.y+86);if(height>4000) throw new Error('Preview exceeds canvas height.');
    const out=createCanvas(W,height);out.getContext('2d').drawImage(this.canvas,0,0);return out.toBuffer('image/png');
  }
}

const homeOutput=resolve(process.argv[2] || resolve(root,'docs/template-preview.png'));
const outputDir=dirname(homeOutput);mkdirSync(outputDir,{recursive:true});
const filenames={home:homeOutput,classroom:resolve(outputDir,'classroom-preview.png'),
  teaching:resolve(outputDir,'teaching-preview.png'),planning:resolve(outputDir,'planning-preview.png')};
for(const page of pages) {
  const output=filenames[page.key];if(!output) throw new Error('Unknown preview page key: '+page.key);
  writeFileSync(output,new Preview(page).draw());console.log(output);
}
