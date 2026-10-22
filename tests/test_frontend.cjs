// Exercise the real polling function against deliberately reordered responses.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8');
const poll = source.slice(source.indexOf('async function pollJob()'),source.indexOf('async function submit('));
const history = source.slice(source.indexOf('async function loadHistory()'),source.indexOf('async function boot('));

async function staleResponseTest() {
  const pending = {}, shown = [], busy = [];
  const context = {state:{currentJob:'a',selected:'workflow',busy:true},pollSequence:0,pollTimer:null,
    api:path=>new Promise(resolve=>pending[path]=resolve),clearTimeout(){},setTimeout(){},
    jobStatus:job=>shown.push(job.id),showImages:job=>shown.push('image-'+job.id),
    setBusy:value=>busy.push(value),toast(){},$:()=>({value:''})};
  vm.createContext(context); vm.runInContext(poll,context);
  const first = context.pollJob();
  context.state.currentJob = 'b';
  const second = context.pollJob();
  pending['jobs/a']({id:'a',state:'done',images:[{}]}); await first;
  pending['jobs/b']({id:'b',state:'running'}); await second;
  assert.deepEqual(shown,['b'], 'an old response must not display another task');
  assert.deepEqual(busy,[], 'an old completed task must not unlock the active task');
}

async function newerResponseTest() {
  const responses = [], shown = [];
  const context = {state:{currentJob:'a',selected:'workflow',busy:true},pollSequence:0,pollTimer:null,
    api:()=>new Promise(resolve=>responses.push(resolve)),clearTimeout(){},setTimeout(){},
    jobStatus:job=>shown.push(job.state),showImages(){},setBusy(){},toast(){},$:()=>({value:''})};
  vm.createContext(context); vm.runInContext(poll,context);
  const first=context.pollJob(),second=context.pollJob();
  responses[1]({id:'a',state:'done',images:[]});await second;
  responses[0]({id:'a',state:'running'});await first;
  assert.deepEqual(shown,['done'],'an older response for the same task must not replace completion');
}

async function historyMustNotUnlockPromptTest() {
  let view;
  const job={id:'a',state:'done',images:[],description:'old task',created_at:'2026-10-09',workflow:{name:'test'},values:{seed:42}};
  const node=(tag,cls,text)=>{
    const result={append(){},replaceChildren(){},addEventListener(event,handler){this[event]=handler;}};
    if(text==='查看任务')view=result;
    return result;
  };
  const state={busy:true,currentJob:'a'};
  const context={state,api:async()=>[job],$:()=>node(),element:node,statusLabels:{done:'done'},
    toast(){},page(){},jobStatus(){},showImages(){},setBusy:value=>state.busy=value,pollJob(){}};
  vm.createContext(context);vm.runInContext(history,context);await context.loadHistory();
  assert.equal(view.disabled,true,'history cannot be opened while a Prompt request is pending');
  view.click();
  assert.equal(state.busy,true,'viewing an old task cannot unlock a new Prompt request');
}

(async()=>{await staleResponseTest();await newerResponseTest();await historyMustNotUnlockPromptTest();console.log('3 frontend race tests passed');})().catch(error=>{console.error(error);process.exitCode=1;});
