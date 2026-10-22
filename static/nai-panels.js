'use strict';
(function (root) {
  const clone = value => structuredClone(value);
  function swapPositions(characters) {
    const result = clone(characters);
    if (result.length >= 2) [result[0].center, result[1].center] = [result[1].center, result[0].center];
    return result;
  }
  function createDelivery(body, previous) {
    const signature = JSON.stringify(body);
    return previous?.signature === signature ? previous : {signature, body:{...clone(body),request_id:crypto.randomUUID()}};
  }
  function applyDraft(current, draft) {
    if (typeof draft?.base_positive !== 'string' || !draft.base_positive.trim() || typeof draft.base_negative !== 'string' || !Array.isArray(draft.characters) || JSON.stringify(draft.characters.map(c=>c.character_id)) !== JSON.stringify(current.characters.map(c=>c.character_id))) throw new Error('人物提示词草稿格式错误，原稿已保留。');
    return clone(draft);
  }
  async function pollOnce(state, api, show) {
    const id = state.panelId, sequence = ++state.sequence;
    const jobs = await api('jobs');
    if (state.panelId !== id || state.sequence !== sequence) return;
    show(jobs.filter(j=>j.panel_id === id));
  }
  const exported = {swapPositions, createDelivery, pollOnce, applyDraft};
  if (typeof module !== 'undefined') module.exports = exported;
  if (!root.document) return;
  let api, node, toast, saved=null, people=[], list=[], dirty=false, busy=false, delivery=null, timer, jobs=[];
  const polling={panelId:'',sequence:0};
  const $=id=>document.getElementById('panel-'+id);
  const models=['nai-diffusion-4-5-full','nai-diffusion-4-5-curated','nai-diffusion-5-full','nai-diffusion-5-curated','nai-diffusion-4-full','nai-diffusion-4-curated-preview','nai-diffusion-3'];
  const controls=[];
  function input(parent,id,label,value='',type='text',rows) {
    const box=node('div','panel-field');const caption=node('label','',label);caption.htmlFor='panel-'+id;
    const field=node(rows?'textarea':'input');field.id='panel-'+id;
    if(rows) field.rows=rows;else field.type=type;
    field.value=value;box.append(caption,field);parent.append(box);controls.push(field);return field;
  }
  function select(parent,id,label,choices) {
    const box=node('div','panel-field');const caption=node('label','',label);caption.htmlFor='panel-'+id;
    const field=node('select');field.id='panel-'+id;
    for(const [value,labelText] of choices){const option=node('option','',labelText);option.value=value;field.append(option);}
    box.append(caption,field);parent.append(box);controls.push(field);return field;
  }
  function button(parent,id,label,callback) {
    const b=node('button','button secondary',label);b.id='panel-'+id;
    b.addEventListener('click',async()=>{if(busy)return;setBusy(true);try{await callback();}catch(e){toast(e.message,true);}finally{setBusy(false);}});
    parent.append(b);controls.push(b);return b;
  }
  function setBusy(value){busy=value;controls.forEach(c=>c.disabled=value);}
  function slot(i) {
    const id=$(i+'-person').value;
    return id?{character_id:id,positive:$(i+'-positive').value,direction:$(i+'-direction').value,negative:$(i+'-negative').value,center:{x:Number($(i+'-x').value),y:Number($(i+'-y').value)}}:null;
  }
  function readPrompt(){return {base_positive:$('positive').value,base_negative:$('negative').value,characters:Array.from({length:5},(_,i)=>slot(i)).filter(Boolean)};}
  function writePrompt(p){
    $('positive').value=p.base_positive;$('negative').value=p.base_negative;
    for(let i=0;i<5;i++){const c=p.characters[i];$(i+'-person').value=c?.character_id||'';$(i+'-positive').value=c?.positive||'';$(i+'-direction').value=c?.direction||'';$(i+'-negative').value=c?.negative||'';$(i+'-x').value=c?.center.x??(i?.7:.3);$(i+'-y').value=c?.center.y??.5;syncPosition(i);}
  }
  function syncPosition(i){const x=Number($(i+'-x').value);$(i+'-position').value=[.3,.5,.7].includes(x)?String(x):'custom';}
  function read(){
    const values={};for(const key of ['MODEL_NAME','sampler_name','scheduler'])values[key]=$(key).value;
    for(const key of ['width','height','steps','cfg_scale','seed','cfg_rescale'])values[key]=Number($(key).value);
    return {...(saved||{}),project:$('project').value,chapter:$('chapter').value,page:$('page').value,panel:$('number').value,visible_description:$('description').value,private_notes:$('notes').value,nai_prompt:readPrompt(),values};
  }
  function setPeople(){
    const records=new Map(people.map(p=>[p.id,p]));for(const p of saved?.character_snapshots||[])if(!records.has(p.id))records.set(p.id,p);
    for(let i=0;i<5;i++){const s=$(i+'-person'),current=s.value;s.replaceChildren();const empty=node('option','','不用此人物槽');empty.value='';s.append(empty);for(const p of records.values()){const o=node('option','',p.name);o.value=p.id;s.append(o);}s.value=current;}
  }
  function setPanel(record){
    clearTimeout(timer);polling.sequence++;saved=record?clone(record):null;polling.panelId=record?.id||'';setPeople();
    for(const key of ['project','chapter','page'])$(key).value=record?.[key]||({project:'坠落',chapter:'01',page:'02'}[key]);
    $('number').value=record?.panel||'01';$('description').value=record?.visible_description||'';$('notes').value=record?.private_notes||'';
    writePrompt(record?.nai_prompt||{base_positive:'anime coloring, clean lineart, cel shading, no text',base_negative:'monochrome, greyscale, text, watermark, duplicate, extra arms',characters:[]});
    const defaults={MODEL_NAME:'nai-diffusion-5-full',width:832,height:1216,steps:28,cfg_scale:5,seed:135791357,sampler_name:'k_euler_ancestral',scheduler:'karras',cfg_rescale:0};
    for(const [k,v] of Object.entries(record?.values||defaults))if($(k))$(k).value=v;
    $('parent').value='';$('preview').textContent='生成前可预览实际发送内容。';dirty=false;jobs=[];showJobs([]);restoreDelivery();
    $('record').textContent=record?'已保存 · 修订 '+record.revision:'新格 · 尚未保存';
    if(record)refresh().catch(e=>toast(e.message,true));
  }
  async function save(){
    if(saved&&!dirty)return saved;
    const data=read();saved=await api(saved?'panels/'+saved.id:'panels',saved?'PUT':'POST',data);
    polling.panelId=saved.id;dirty=false;$('record').textContent='已保存 · 修订 '+saved.revision;
    await reloadList();return saved;
  }
  async function reloadList(){
    list=await api('panels');const select=$('saved');select.replaceChildren();const first=node('option','','选择已保存单格');first.value='';select.append(first);
    for(const p of list){const o=node('option','',`${p.project} / 第${p.chapter}话 P${p.page}-${p.panel} · 修订${p.revision}`);o.value=p.id;select.append(o);}select.value=saved?.id||'';
  }
  function storeDelivery(){localStorage.setItem('nai-panel-delivery',JSON.stringify(delivery));$('recover').hidden=!delivery;}
  function restoreDelivery(){
    delivery=null;try{const old=JSON.parse(localStorage.getItem('nai-panel-delivery')||'null');if(old?.body?.panel_id===saved?.id)delivery=old;}catch{}
    $('recover').hidden=!delivery;
  }
  async function send(body){
    $('status').textContent='正在提交；网络中断时请使用“查询上次提交”。';
    const job=await api('jobs','POST',body);$('status').textContent='任务 '+job.id+' · '+job.state;
    await refresh();
  }
  async function fromHistory(job){
    if(!job.panel_id)throw new Error('此任务没有关联单格。');
    if(dirty)await save();
    const panel=await api('panels/'+job.panel_id);
    const restored=await api('panels/'+job.panel_id,'PUT',{revision:panel.revision,restore_job_id:job.id});
    setPanel(restored);$('parent').value=job.id;await reloadList();
    toast('已载入完整版本及人物快照，可以继续修改。');
  }
  async function generate(random){
    await save();if(!saved.nai_prompt.base_positive.trim())throw new Error('请填写全局正面词。');
    // Keep the uncertain delivery intact until the caller explicitly creates a new attempt.
    if(delivery){const old=await api('jobs');const submitted=old.find(j=>j.request_id===delivery.body.request_id);if(!submitted)throw new Error('上次提交尚未确认，请先点击“查询上次提交”，避免重复付费。');if(submitted.state==='unknown')throw new Error('上次请求结果未知，请先检查服务商记录和额度；记录会保留。');}
    const body={provider:'novelai',panel_id:saved.id,panel_revision:saved.revision,use_api:false,random_seed:random,parent_job_id:$('parent').value};
    // An intentional new attempt always gets a new identity, even with unchanged settings.
    delivery=createDelivery(body,null);storeDelivery();await send(delivery.body);
  }
  async function refresh(){
    clearTimeout(timer);if(!polling.panelId)return;
    await pollOnce(polling,api,showJobs);
    if(jobs.some(j=>!['done','failed','unknown','interrupted'].includes(j.state)))timer=setTimeout(()=>refresh().catch(e=>{ $('status').textContent=e.message;timer=setTimeout(()=>refresh().catch(()=>{}),4000);}),2000);
  }
  function showJobs(items){
    jobs=items.sort((a,b)=>b.created_at.localeCompare(a.created_at));const host=$('history');host.replaceChildren();
    $('status').textContent=items.length?'最近任务：'+items[0].state:'此格尚无生成版本。';
    for(const job of jobs){
      const card=node('article','card panel-version');card.append(node('h3','',`${job.values.MODEL_NAME} · 种子 ${job.values.seed}`),node('p','hint',`${job.state} · ${job.id}${saved?.selected_job_id===job.id?' · 已选定':''}`));
      if(job.error)card.append(node('p','hint',job.error));
      if(job.images.length){const img=node('img','panel-result');img.src='/api/images/'+job.id+'/0';img.alt=job.description||'单格生成结果';card.append(img);const a=node('a','text-button','打开原图');a.href=img.src;a.target='_blank';a.rel='noopener';card.append(a);}
      const actions=node('div','action-row');
      const restore=node('button','button secondary','载入此版本继续修改');restore.onclick=async()=>{if(busy)return;setBusy(true);try{await fromHistory(job);}catch(e){toast(e.message,true);}finally{setBusy(false);}};actions.append(restore);
      const choose=node('button','button secondary','前往本页审核');choose.onclick=()=>{window.ComicReviewPage.open().catch(e=>toast(e.message,true));document.getElementById('comic-review-board').scrollIntoView({behavior:'smooth'});};actions.append(choose);
      const compare=node('input');compare.type='checkbox';compare.dataset.job=job.id;compare.setAttribute('aria-label','对比版本 '+job.id);compare.onchange=()=>showComparison();const label=node('label','check-label','加入对比');label.prepend(compare);actions.append(label);card.append(actions);host.append(card);
    }
    $('compare').replaceChildren();
  }
  function showComparison(){
    const ids=[...$('history').querySelectorAll('input:checked')].map(c=>c.dataset.job);if(ids.length>2){toast('一次比较两张图片。',true);const check=$('history').querySelector(`input[data-job="${ids.at(-1)}"]`);check.checked=false;ids.pop();}
    const host=$('compare');host.replaceChildren();for(const id of ids){const job=jobs.find(j=>j.id===id);if(!job.images.length)continue;const figure=node('figure');const img=node('img','panel-result');img.src='/api/images/'+id+'/0';img.alt='对比版本 '+id;figure.append(img,node('figcaption','hint',job.values.MODEL_NAME+' · 种子 '+job.values.seed));host.append(figure);}
  }
  async function init(deps){
    ({api,element:node,toast}=deps);const host=document.getElementById('nai-panel-editor');
    const toolbar=node('div','card');select(toolbar,'saved','载入单格',[]);button(toolbar,'new','新建一格',()=>{if(dirty&&!confirm('尚有未保存修改，放弃这些修改并新建？'))return;setPanel(null);});const usage=node('p','hint','正在读取 NAI 当日额度…');usage.id='panel-usage';toolbar.append(usage);host.append(toolbar);
    const grid=node('div','studio-grid'),left=node('div','creation-column'),right=node('div','result-column');grid.append(left,right);host.append(grid);
    const scene=node('div','card');left.append(scene);const row=node('div','field-grid');scene.append(row);for(const [id,label,v] of [['project','项目','坠落'],['chapter','话','01'],['page','页','02'],['number','格','01']])input(row,id,label,v);
    input(scene,'description','可见画面描述','','text',4);input(scene,'notes','导演／剧情笔记（只保存在本机，不发送）','','text',2);scene.append(node('p','hint','将未来身份等秘密写在笔记里。提示词只描绘当前镜头能看到的内容。'));
    const prompt=node('div','card');left.append(prompt);input(prompt,'positive','全局正面词','','text',3);input(prompt,'negative','全局负面词','','text',2);
    for(let i=0;i<5;i++){
      const box=node('fieldset','panel-character');prompt.append(box);
      const legend=node('legend','','人物 '+String.fromCharCode(65+i));box.append(legend);
      select(box,i+'-person','绑定人物',[]);input(box,i+'-positive','固定外观与服装锚点','','text',4);input(box,i+'-direction','本格动作、表情与视线','','text',3);input(box,i+'-negative','此人物需要避免的特征','','text',2);
      select(box,i+'-position','位置预设',[['0.3','左'],['0.5','中'],['0.7','右'],['custom','自定义坐标']]);const coords=node('div','field-grid');box.append(coords);
      input(coords,i+'-x','横向坐标',i?.7:.3,'number');input(coords,i+'-y','纵向坐标',.5,'number');
      for(const axis of ['x','y']){$(i+'-'+axis).min=0;$(i+'-'+axis).max=1;$(i+'-'+axis).step=.01;}
    }
    window.PromptPresets.mount(prompt,{api,element:node,toast,targets:[{id:'panel-positive',negativeId:'panel-negative',label:'主提示词'},...Array.from({length:5},(_,i)=>({id:`panel-${i}-positive`,negativeId:`panel-${i}-negative`,label:`角色${i+1}`,enabled:()=>!!$(i+'-person').value}))]});
    button(prompt,'swap','交换前两人位置',()=>{writePrompt({...readPrompt(),characters:swapPositions(readPrompt().characters)});dirty=true;});
    button(prompt,'draft','按描述生成提示词草稿',async()=>{const data=read();const result=await api('nai/prompt-draft','POST',{visible_description:data.visible_description,nai_prompt:data.nai_prompt,panel_id:saved?.id});writePrompt(applyDraft(data.nai_prompt,result));dirty=true;toast('草稿已生成，请检查后再生图。');});
    const params=node('div','card');left.append(params);select(params,'MODEL_NAME','模型',models.map(m=>[m,m]));const fields=node('div','field-grid');params.append(fields);
    for(const [k,l,v] of [['width','宽',832],['height','高',1216],['steps','步数',28],['cfg_scale','提示引导',5],['seed','种子',135791357],['cfg_rescale','引导重缩放',0]])input(fields,k,l,v,'number');$('cfg_scale').step='any';$('cfg_rescale').step='any';
    select(params,'sampler_name','采样器',['k_euler_ancestral','k_euler','k_dpmpp_2m','k_dpmpp_sde','k_dpmpp_2s_ancestral','ddim_v3'].map(s=>[s,s]));select(params,'scheduler','噪声计划',['karras','native','exponential','polyexponential'].map(s=>[s,s]));input(params,'parent','父版本任务 ID（可留空）');
    button(params,'save','保存此格',save);button(params,'preview-button','预览实际生成请求',async()=>{await save();$('preview').textContent=JSON.stringify(await api('nai/preview','POST',{panel_id:saved.id,panel_revision:saved.revision}),null,2);});
    button(params,'generate-fixed','保持当前种子生成',()=>generate(false));button(params,'generate-random','换种子生成',()=>generate(true));button(params,'recover','查询上次提交',async()=>{if(delivery)await send(delivery.body);});
    const info=node('p','hint');info.id='panel-record';params.append(info);
    const result=node('div','card');right.append(result);result.append(node('h2','','生成版本'));const status=node('p');status.id='panel-status';status.setAttribute('role','status');status.setAttribute('aria-live','polite');result.append(status);button(result,'refresh','刷新任务状态',refresh);
    const comparison=node('div','panel-comparison');comparison.id='panel-compare';right.append(comparison);const history=node('div');history.id='panel-history';right.append(history);
    const details=node('details','card');const summary=node('summary','','实际请求（不含凭证）');const preview=node('pre','panel-request');preview.id='panel-preview';details.append(summary,preview);right.append(details);
    const refs=node('div','card');refs.append(node('h2','','人设图 · 仅供查看'),node('p','hint','当前未作为图片生成条件发送。'));right.append(refs);try{for(const r of await api('nai/references')){const img=node('img','panel-reference');img.src=r.url;img.alt=r.name;refs.append(img);}}catch{}
    host.addEventListener('input',event=>{if(event.target.id.startsWith('panel-')&&event.target.id!=='panel-saved'&&event.target.matches('input,textarea,select'))dirty=true;});
    $('saved').addEventListener('change',()=>{if(busy)return;if(dirty&&!confirm('尚有未保存修改，放弃并载入其他格？')){$('saved').value=saved?.id||'';return;}setPanel(list.find(p=>p.id===$('saved').value)||null);});
    for(let i=0;i<5;i++){
      $(i+'-person').addEventListener('change',()=>{const p=people.find(p=>p.id===$(i+'-person').value)||saved?.character_snapshots.find(p=>p.id===$(i+'-person').value);$(i+'-positive').value=p?[p.appearance,p.clothing].filter(Boolean).join(', '):'';$(i+'-direction').value='';$(i+'-negative').value=p?.negative_prompt||'';dirty=true;});
      $(i+'-position').addEventListener('change',()=>{if($(i+'-position').value!=='custom')$(i+'-x').value=$(i+'-position').value;dirty=true;});
      $(i+'-x').addEventListener('input',()=>syncPosition(i));
    }
    people=await api('characters');await reloadList();setPanel(null);
  }
  async function open(){if(!api)return;people=await api('characters');const active=Array.from({length:5},(_,i)=>slot(i));setPeople();active.forEach((c,i)=>{if(c)$(i+'-person').value=c.character_id;});await reloadList();const usage=await api('nai/usage');$('usage').textContent=`上海时间 ${usage.date}：今日记录 ${usage.count} 次（含失败和未知请求的保守计数）；上一张返回后至少间隔 ${usage.minimum_interval_seconds} 秒。每日数量仅作记录，模型额度请按服务说明确认。`;}
  root.NaiPanelPage={init,open,restoreJob:fromHistory,fromHistory,async loadPanel(id){if(busy)return;if(dirty&&!confirm('放弃尚未保存的单格修改？'))return;setPanel(await api('panels/'+id));},...exported};
})(typeof window!=='undefined'?window:globalThis);


