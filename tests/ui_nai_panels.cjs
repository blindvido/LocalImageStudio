// Run against ui_panel_server.py (temporary data only). Never submit to a paid upstream.
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8191');
  await page.waitForSelector('#panel-recover',{state:'attached'});
  assert.deepEqual(errors,[],'panel editor must boot without DOM errors');
  await page.evaluate(async()=>{const b=await(await fetch('/api/bootstrap')).json();for(const [name,appearance]of [['玲','girl, blue hair, green eyes'],['哲','boy, silver hair, green eyes']])await fetch('/api/characters',{method:'POST',headers:{'Content-Type':'application/json','X-Studio-Token':b.token},body:JSON.stringify({name,appearance})});});
  await page.locator('[data-page="panels"]').click();
  await page.waitForFunction(()=>document.querySelector('#panel-0-person').options.length===3);
  assert.equal(await page.locator('#panel-MODEL_NAME').inputValue(),'nai-diffusion-5-full','new panels should inherit the configured V5 default');
  const persons=await page.locator('#panel-0-person option').evaluateAll(os=>os.map(o=>o.value));
  await page.locator('#panel-0-person').selectOption(persons[1]);await page.locator('#panel-1-person').selectOption(persons[2]);
  await page.locator('#panel-0-positive').fill('girl, blue hair, green eyes');
  await page.locator('#panel-0-direction').fill('turning her head toward screen right, slightly raised eyebrow');
  await page.locator('#panel-0-position').selectOption('0.3');await page.locator('#panel-1-position').selectOption('0.7');
  await page.locator('#panel-positive').fill('1girl, 1boy, anime coloring, ruins');
  await page.locator('#panel-description').fill('原世界玲自然地和哲说话');await page.locator('#panel-notes').fill('SECRET_FUTURE');
  await page.locator('#panel-save').click();await page.waitForFunction(()=>document.querySelector('#panel-record').textContent.includes('已保存'));
  await page.locator('#panel-swap').click();assert.match(await page.locator('#panel-0-positive').inputValue(),/blue hair/);assert.equal(await page.locator('#panel-0-x').inputValue(),'0.7');
  await page.locator('#panel-preview-button').click();await page.waitForFunction(()=>document.querySelector('#panel-preview').textContent.includes('char_captions'));
  const preview=JSON.parse(await page.locator('#panel-preview').textContent());assert.equal(preview.parameters.v4_prompt.caption.char_captions.length,2);assert.equal(preview.parameters.v4_prompt.caption.char_captions[0].char_caption,'girl, blue hair, green eyes, turning her head toward screen right, slightly raised eyebrow');assert.ok(!JSON.stringify(preview).includes('SECRET_FUTURE'));
  const original=await page.locator('#panel-positive').inputValue();
  await page.route('**/api/nai/prompt-draft',r=>r.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'草稿人物错误'})}));
  await page.locator('#panel-draft').click();await page.waitForFunction(()=>!document.querySelector('#panel-draft').disabled);assert.equal(await page.locator('#panel-positive').inputValue(),original);
  const ids=await page.locator('#panel-saved option').evaluateAll(os=>os.map(o=>o.value).filter(Boolean));assert.equal(ids.length,1);
  const record=await page.evaluate(async id=>(await(await fetch('/api/panels/'+id)).json()),ids[0]);
  const historical={id:'historical-panel-job',provider:'novelai',panel_id:record.id,panel_revision:record.revision,request_id:'historical-safe-id',parent_job_id:'',
    state:'done',images:[],error:'',created_at:new Date().toISOString(),updated_at:new Date().toISOString(),description:record.visible_description,
    positive_prompt:record.nai_prompt.base_positive,negative_prompt:record.nai_prompt.base_negative,nai_prompt:record.nai_prompt,values:record.values,
    panel_snapshot:{id:record.id,revision:record.revision,project:record.project,chapter:record.chapter,page:record.page,panel:record.panel,visible_description:record.visible_description,nai_prompt:record.nai_prompt,values:record.values,character_snapshots:record.character_snapshots},
    workflow:{name:'NAI',template:'tags'},workflow_id:'novelai'};
  await page.evaluate(async job=>{const b=await(await fetch('/api/bootstrap')).json();await fetch('/api/test/seed-job',{method:'POST',headers:{'Content-Type':'application/json','X-Studio-Token':b.token},body:JSON.stringify({job})});},historical);
  const changed={...record,private_notes:'SECRET_FUTURE',nai_prompt:{...record.nai_prompt,characters:record.nai_prompt.characters.slice(1)}};
  const belleId=record.nai_prompt.characters[0].character_id;
  await page.evaluate(async ({changed,belleId})=>{const b=await(await fetch('/api/bootstrap')).json(),headers={'Content-Type':'application/json','X-Studio-Token':b.token};await fetch('/api/panels/'+changed.id,{method:'PUT',headers,body:JSON.stringify(changed)});await fetch('/api/characters/'+belleId,{method:'DELETE',headers});},{changed,belleId});
  await page.reload();await page.waitForSelector('#panel-recover',{state:'attached'});await page.locator('[data-page="panels"]').click();
  await page.locator('#panel-saved').selectOption(ids[0]);
  assert.equal(await page.locator('#panel-0-person option').count(),2,'removed historical character should not be listed in the current panel');
  await page.getByRole('button',{name:'载入此版本继续修改'}).click();await page.waitForFunction(()=>document.querySelector('#panel-0-person').value&&document.querySelector('#panel-record').textContent.includes('修订 4'));
  assert.equal(await page.locator('#panel-0-positive').inputValue(),'girl, blue hair, green eyes');assert.equal(await page.locator('#panel-0-direction').inputValue(),'turning her head toward screen right, slightly raised eyebrow');assert.equal(await page.locator('#panel-0-x').inputValue(),'0.7');assert.equal(await page.locator('#panel-notes').inputValue(),'SECRET_FUTURE');
  await page.screenshot({path:path.join(__dirname,'../verification/nai-panels/editor-desktop.png'),fullPage:true});
  await page.setViewportSize({width:430,height:900});await page.screenshot({path:path.join(__dirname,'../verification/nai-panels/editor-mobile.png'),fullPage:true});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'editor should fit narrow screens');
  assert.deepEqual(errors,[]);console.log('Real browser: boot, save, swap, preview, invalid draft, reload, narrow layout passed');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
