/* Targeted VM regressions for the four-language website, without a browser. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const python = fs.readFileSync(path.join(root, 'build_frontend_dashboard.py'), 'utf8');
const localeSource = fs.readFileSync(path.join(__dirname, 'website_locale.js'), 'utf8');
const toggleSource = python.split('LANGUAGE_TOGGLE_JS = ')[1].split('+ r"""')[1].split('\n"""')[0];
// Apply the same adapter-owned dictionaries and dynamic labels as the page
// builder. AST reads literal source only and does not start services or import it.
const adapter = spawnSync('python3',['-c',`import ast,json,pathlib,sys
tree=ast.parse(pathlib.Path(sys.argv[1]).read_text())
values={}
for node in ast.walk(tree):
 if isinstance(node,ast.Assign):
  for target in node.targets:
   if isinstance(target,ast.Name) and target.id in ('extra_translations','dynamic_extra'):
    values[target.id]=ast.literal_eval(node.value)
print(json.dumps(values))`,path.join(__dirname,'frontend.py')],{encoding:'utf8'});
assert.equal(adapter.status,0,adapter.stderr);
const adapterValues=JSON.parse(adapter.stdout);
const adaptedToggle=toggleSource.replace('  const zhToEn = {','  const zhToEn = {\n'+Object.entries(adapterValues.extra_translations).map(([key,value])=>JSON.stringify(key)+':'+JSON.stringify(value)+',').join('\n'))
  .replace('  const translateDynamic = (text) => {','  const translateDynamic = (text) => {\n'+adapterValues.dynamic_extra);
const combined = localeSource + adaptedToggle;
let passed = 0;
function test(name, run) { run(); passed += 1; console.log(`PASS ${name}`); }

function fixture({language = 'zh', query = '?lang=zh', blockedStorage = false, stored = null} = {}) {
  const microtasks = [];
  const observers = [];
  const texts = [];
  const links = [];
  const attributes = [];
  const inputs = [];
  const toggles = [];
  let walks = 0;
  class Element {
    constructor(tag = 'p', props = {}) { this.tagName=tag.toUpperCase(); this.nodeType=1; this.attrs={}; this.children=[]; this.dataset={}; this.handlers={}; this.className=''; Object.assign(this, props); }
    closest(selector) {
      if (this.report && /poster-modal|report-modal|poster-language/.test(selector)) return this;
      if (this.toggle && /data-lang-toggle/.test(selector)) return this;
      if (selector.includes(this.tagName.toLowerCase()) && ['SCRIPT','STYLE','CODE','PRE','INPUT','TEXTAREA','SELECT','NOSCRIPT'].includes(this.tagName)) return this;
      return this.parentElement?.closest(selector) || null;
    }
    matches(selector) { return selector==='[data-lang-toggle]' && this.toggle; }
    hasAttribute(name) { return Object.hasOwn(this.attrs,name); }
    getAttribute(name) { return this.attrs[name] ?? null; }
    setAttribute(name,value) { this.attrs[name]=String(value); }
    append(child) { this.children.push(child); child.parentElement=this; }
    replaceWith(next) { const index=toggles.indexOf(this); if(index>=0) { next.toggle=true; toggles[index]=next; } }
    addEventListener(type,callback) { this.handlers[type]=callback; }
  }
  const body = new Element('body');
  const button = new Element('button',{toggle:true}); toggles.push(button);
  const node = value => { const text={nodeType:3,nodeValue:value,parentElement:new Element()}; texts.push(text); return text; };
  const document = {
    documentElement:{lang:language}, body, title:'', head:{append(){}},
    createElement:tag=>new Element(tag),
    createTreeWalker:()=>{ walks+=1; let index=-1; return {nextNode(){return ++index<texts.length;},get currentNode(){return texts[index];}};},
    querySelectorAll:selector=>selector==='[data-lang-toggle]'?toggles:selector==='[data-burn-power]'?inputs:selector==='a[href]'?links:selector==='[placeholder], [aria-label], [title]'?attributes:[],
  };
  let url = new URL(`http://127.0.0.1:18882/${query}#equation`);
  const location = {get search(){return url.search;},get href(){return url.href;},get origin(){return url.origin;}};
  const window = {dispatchEvent(){}};
  const storage = {
    getItem(){if(blockedStorage)throw new Error('Storage blocked');return stored;},
    setItem(_key,value){if(blockedStorage)throw new Error('Storage blocked');stored=value;},
  };
  const context = vm.createContext({window,document,URL,URLSearchParams,location,localStorage:storage,
    history:{replaceState(_state,_title,next){url=new URL(next,url);}},
    NodeFilter:{SHOW_TEXT:4},CustomEvent:class{},
    MutationObserver:class {constructor(callback){this.callback=callback;observers.push(this);}observe(){}},
    queueMicrotask:callback=>microtasks.push(callback),console,
  });
  return {context,window,document,texts,node,links,attributes,inputs,toggles,Element,
    start(){vm.runInContext(combined,context);},
    select(lang){toggles[0].value=lang;toggles[0].handlers.change();},
    flush(records){observers[0].callback(records);while(microtasks.length)microtasks.shift()();},
    url:()=>url, walks:()=>walks,
  };
}

test('URL language works when all optional storage is denied',()=>{
  const f=fixture({query:'?lang=ja',blockedStorage:true}); const text=f.node('核心数据'); f.start();
  assert.equal(f.document.documentElement.lang,'ja'); assert.equal(text.nodeValue,'主要データ');
  f.select('ko'); assert.equal(f.document.documentElement.lang,'ko'); assert.equal(f.url().searchParams.get('lang'),'ko');
  assert.equal(f.url().hash,'#equation');
});
test('invalid URL language falls back to a valid saved choice',()=>{
  const f=fixture({query:'?lang=invalid',stored:'ko'}); f.start(); assert.equal(f.document.documentElement.lang,'ko');
});
test('desktop/mobile navigation carries the language without storage',()=>{
  const f=fixture({query:'?lang=ja',blockedStorage:true});
  for(const href of ['/?desktop=1#rank','/m/?source=gallery#growth','#pulse','https://example.com/']){const link=new f.Element('a');link.setAttribute('href',href);f.links.push(link);}
  f.start(); assert.equal(f.links[0].getAttribute('href'),'/?desktop=1&lang=ja#rank');
  assert.equal(f.links[1].getAttribute('href'),'/m/?source=gallery&lang=ja#growth');
  assert.equal(f.links[2].getAttribute('href'),'#pulse'); assert.equal(f.links[3].getAttribute('href'),'https://example.com/');
  f.select('ko'); assert.equal(f.links[0].getAttribute('href'),'/?desktop=1&lang=ko#rank');
});
test('all four languages switch reversibly without changing report text or input',()=>{
  const f=fixture(); const value=f.node('  核心数据  '); const numeric=f.node('+1.25亿'); const report=f.node('全网总算力'); report.parentElement.report=true;
  const field=new f.Element('input',{value:'123456789'});f.inputs.push(field);f.start();
  const expected={en:'  Core Data  ',ja:'  主要データ  ',ko:'  핵심 데이터  ',zh:'  核心数据  '};
  for(let repeat=0;repeat<12;repeat++)for(const lang of ['en','ja','ko','zh']){
    f.select(lang); assert.equal(value.nodeValue,expected[lang]);assert.equal(report.nodeValue,'全网总算力');
    assert.equal(field.value,'123456789');assert.equal(numeric.nodeValue,lang==='zh'?'+1.25亿':'+125M');
  }
});
test('fresh runtime values are translated, own mutations do not cause a loop',()=>{
  const f=fixture({query:'?lang=ko'});const text=f.node('核心数据');f.start();
  text.nodeValue='每日新增地址';f.flush([{type:'characterData',target:text}]);assert.equal(text.nodeValue,'일별 신규 주소');
  const count=f.walks();f.flush([{type:'characterData',target:text}]);assert.equal(f.walks(),count);
  const fresh=f.node('每日销毁量');f.flush([{type:'childList',target:f.document.body,addedNodes:[fresh]}]);assert.equal(fresh.nodeValue,'일별 소각량');
  f.select('zh');assert.equal(text.nodeValue,'每日新增地址');assert.equal(fresh.nodeValue,'每日销毁量');
});
test('attributes and validation messages remain reversible',()=>{
  const f=fixture({query:'?lang=ja'});const input=new f.Element('input');input.setAttribute('placeholder','例如 1亿、5亿、1000万');f.attributes.push(input);
  const message=f.node('请输入有效算力');f.start();assert.equal(input.getAttribute('placeholder'),'例：100M、500M、10M');assert.equal(message.nodeValue,'有効な演算力を入力してください');
  f.select('ko');assert.equal(message.nodeValue,'유효한 연산력을 입력하세요');f.select('zh');assert.equal(input.getAttribute('placeholder'),'例如 1亿、5亿、1000万');assert.equal(message.nodeValue,'请输入有效算力');
});
test('compound history notes translate before partial numeric fallback',()=>{
  const f=fixture({query:'?lang=ko'});const text=f.node('预言机触发价为最高价的 50% 旧站历史记录按记录日期展示，采样时间不固定；新记录按北京时间08:00日结，跨期变化需留意口径差异。');f.start();
  assert(!/[\u4e00-\u9fff]/.test(text.nodeValue));assert(text.nodeValue.includes('이전 사이트 기록'));
  f.select('en');assert(!/[\u4e00-\u9fff]/.test(text.nodeValue));assert(text.nodeValue.includes('Older site records'));
});
test('English joined timestamps do not retain Chinese timezone fragments',()=>{
  const f=fixture({query:'?lang=en'});const text=f.node('距参考日 52.59天 · 数据更新 11-02 17:48 北京时间');f.start();
  assert(!/[\u4e00-\u9fff]/.test(text.nodeValue),text.nodeValue);assert(text.nodeValue.includes('11-02 17:48 Beijing time'));
});
test('signed compact numbers and units are preserved across all foreign locales',()=>{
  const f=fixture();f.start();const l=f.window.MarsWebsiteLocale;
  for(const lang of ['en','ja','ko']){
    assert.equal(l.translate('-1.25亿',lang),'-125M');assert.equal(l.translate('−1.25亿',lang),'-125M');assert.equal(l.translate('+2.345万亿',lang),'+2.345T');
    assert.equal(l.translate('4,280.43万枚',lang),'42.804M MARS');assert.equal(l.translate('73枚',lang),'73 MARS');
  }
});
test('English temporal grammar and economic labels match their meaning',()=>{
  const f=fixture();f.start();const l=f.window.MarsWebsiteLocale;
  assert.equal(l.translate('1天','en'),'1 day');assert.equal(l.translate('2天','en'),'2 days');
  assert.equal(l.translate('1小时前','en'),'1 hour ago');assert.equal(l.translate('2分钟前','en'),'2 minutes ago');
  assert.equal(l.translate('总产量','en'),'Supply Cap');assert.equal(l.translate('新增算力','en'),'Power Change');
  assert.equal(l.translate('新增算力','ja'),'演算力の増減');assert.equal(l.translate('新增算力','ko'),'연산력 증감');
});
test('automatic mobile redirects preserve explicit language, parameters and hash',()=>{
  const start=python.indexOf('      var path = window.location.pathname;');const end=python.indexOf('    }})();',start);
  const redirectSource=python.slice(start,end).replaceAll('{{','{').replaceAll('}}','}');
  for(const pathname of ['/','/m/']){
    let destination='';const window={location:{pathname,search:'?lang=ko&source=direct',hash:'#growth',replace:value=>destination=value},matchMedia:()=>({matches:true})};
    vm.runInNewContext(`(function(){${redirectSource}})();`,{window,navigator:{userAgent:'iPhone',maxTouchPoints:1},URLSearchParams});
    assert.equal(destination,'/m/index.html?lang=ko&source=direct#growth');
  }
});
test('desktop and mobile calculators validate the whole numeric input identically',()=>{
  for(const marker of ['const setupBurnCalculator = (panel) => {','const setupMobileBurnCalculator = (panel) => {']){
    const start=python.indexOf(marker);const parseStart=python.indexOf('  const parsePower = (value) => {',start);
    const parseEnd=python.indexOf('\n  };',parseStart)+5;
    const parse=vm.runInNewContext(python.slice(parseStart,parseEnd)+'\nparsePower;');
    for(const [value,expected] of [['1亿',100000000],['100M',100000000],['0.5b',500000000],['1,000,000',1000000],['1.5万',15000],['  +100 M  ',100000000],['.5k',500]])assert.equal(parse(value),expected,value);
    for(const value of ['1亿2','1e8','1,00','1,000,00','1.2.3','Infinity','NaN','-1','0','', '100Mtail','100 M 2','<script>','9'.repeat(400)])assert(Number.isNaN(parse(value)),value);
  }
});
test('expired countdown stops rewriting translated text on every timer tick',()=>{
  const source=python.split('ORACLE_COUNTDOWN_JS = r"""')[1].split('\n"""')[0];
  let writes=0;let tick;
  const makeNode=()=>({value:'',classes:new Set(),get textContent(){return this.value;},set textContent(value){this.value=value;writes++;},setAttribute(){},classList:{toggle(){}}});
  const countdown=makeNode(),note=makeNode();
  const document={querySelectorAll:selector=>selector==='[data-oracle-countdown]'?[countdown]:selector==='[data-oracle-countdown-note]'?[note]:[],addEventListener(){}};
  class FixedDate extends Date {static now(){return Date.parse('2026-11-02T00:00:00Z');}}
  vm.runInNewContext(source,{document,window:{setInterval:callback=>tick=callback,addEventListener(){}},Date:FixedDate});
  assert.equal(writes,2);assert.equal(countdown.textContent,'本期已结束');
  countdown.value='今期は終了しました';note.value='次期の公式発表待ち';
  for(let repeat=0;repeat<60;repeat++)tick();
  assert.equal(writes,2);assert.equal(countdown.textContent,'今期は終了しました');
});
console.log(`Website locale regressions: ${passed} passed`);
