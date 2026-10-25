const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const code=fs.readFileSync(require('node:path').join(__dirname,'../pharm/literature/browser_guards.js'),'utf8');
function element(text,top,extra={}) {
  return {children:[],textContent:text,innerText:text,
    getBoundingClientRect:()=>({top,left:15,right:165,bottom:top+18,width:150,height:18}),...extra};
}
function state(hints,abstracts=[],passwords=[]) {
  const context={innerHeight:800,innerWidth:1200,getComputedStyle:e=>({display:e.hidden?'none':'block',visibility:'visible',opacity:'1'}),
    document:{querySelectorAll:selector=>selector==='body *'?hints:selector==='#ChDivSummary,.abstract-text'?abstracts:selector==='input[type="password"]'?passwords:[]}};
  vm.createContext(context);return vm.runInContext(code+';cnkiPageState()',context);
}
test('offscreen CNKI verification templates do not block a readable abstract',()=>{
  const value=state([element('拖动下方拼图完成验证',-999985),element('安全验证',-999756)],[element('正常摘要',200)]);
  assert.equal(value.blocking_verification,false);assert.equal(value.abstract_available,true);
});
test('a displayed verification inside the viewport must stop collection',()=>{
  assert.equal(state([element('安全验证',200)]).blocking_verification,true);
});
test('display-none and zero-size verification templates are not visible challenges',()=>{
  assert.equal(state([element('安全验证',20,{hidden:true}),element('安全验证',0,{getBoundingClientRect:()=>({top:0,left:0,right:0,bottom:0,width:0,height:0})})]).blocking_verification,false);
});

test('hidden login templates do not request manual interaction',()=>{
  assert.equal(state([],[],[element('',-999985),element('',0,{hidden:true})]).login_required,false);
  assert.equal(state([],[element('正常摘要',100)],[element('',200)]).login_required,false);
});

test('a visible login form without readable content pauses for manual login',()=>{
  assert.equal(state([],[],[element('',200)]).login_required,true);
});
