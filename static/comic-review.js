'use strict';
window.ComicReviewPage = (() => {
  let api, el, toast, state, busy=false, timer, orphanPage=1, initialized=false, activeTab="pages";
  const selectedVersions={}, drafts={}, checked=new Set();
  const $=id=>document.getElementById('review-'+id);
  const statusName={pending:'待审核',approved:'已通过 · 当前选用',changes_requested:'需修改'};
  const natural=(a,b)=>a.localeCompare(b,'zh-CN',{numeric:true});
  function make(tag,cls,text){return el(tag,cls,text);}
  function action(label,fn){const b=make('button','button secondary',label);b.type='button';b.onclick=()=>run(fn);return b;}
  async function run(fn){if(busy)return;busy=true;document.querySelectorAll('#comic-review-board button,#comic-review-board select').forEach(b=>b.disabled=true);try{await fn();}catch(e){toast(e.message,true);}finally{busy=false;render();}}
  function options(id,values){const s=$(id),old=s.value;s.replaceChildren();for(const v of [...new Set(values)].sort(natural)){const n=String(v).match(/^(?:CH|P)?0*(\d+)$/i);const label=n&&id==='chapter'?`第 ${Number(n[1])} 话`:n&&id==='page'?`第 ${Number(n[1])} 页`:v||'未注明台本版本';const o=make('option','',label);o.value=v;s.append(o);}if([...s.options].some(o=>o.value===old))s.value=old;}
  function chapterPanels(){return state.panels.filter(p=>p.project===$('project').value&&p.chapter===$('chapter').value&&String(p.script_version||'')===$('version').value);}
  function chosen(p){return p.candidates.find(a=>a.status==='approved');}
  function scopes(){options('project',[...state.panels,...(state.page_catalog||[])].map(p=>p.project));const a=[...state.panels,...(state.page_catalog||[])].filter(p=>p.project===$('project').value);options('chapter',a.map(p=>p.chapter));options('version',a.filter(p=>p.chapter===$('chapter').value).map(p=>p.script_version||''));const panels=chapterPanels();const names=panels.filter(p=>!$('unfinished').checked||!chosen(p)).map(p=>p.page);const empty=(state.page_catalog||[]).filter(p=>p.project===$('project').value&&p.chapter===$('chapter').value&&p.script_version===$('version').value&&!panels.some(x=>x.page===p.page)).map(p=>p.page);options('page',[...names,...empty]);}
  async function reload(){state=await api('comic-review');render();}
  function showEditor(panel){return editImage(null,panel);}
  async function decide(a,status,note){state=await api('comic-review/decision','POST',{job_id:a.job_id,image_index:a.image_index,status,note:note.value,revision:state.revision});}
  function imageCard(a,panel){
    const card=make('article','review-image '+(a.status==='approved'?'is-approved':''));
    const img=make('img','review-thumb');img.src=a.url;img.alt=`${panel?.page||'未归档'} ${panel?.panel||''}候选图片`;img.loading='lazy';
    const link=make('a');link.href=a.url;link.target='_blank';link.rel='noopener';link.append(img);card.append(link);
    card.append(make('strong','',statusName[a.status]||'待审核'),make('p','hint',a.provider==='upload'?`上传图片 · ${a.created_at}`:`${a.values?.MODEL_NAME||a.provider} · Seed ${a.values?.seed??'未记录'} · ${a.created_at}`));
    if(!panel){const pick=make('input');pick.type='checkbox';pick.checked=checked.has(a.job_id+':'+a.image_index);pick.onchange=()=>{const k=a.job_id+':'+a.image_index;pick.checked?checked.add(k):checked.delete(k);renderOrphans();};const label=make('label','check-label','选择归档');label.prepend(pick);card.append(label);card.append(make('p','hint',(a.work_tags||[]).join(' · ')));}
    const note=make('textarea');note.rows=2;note.placeholder='修改意见（可留空）';note.value=a.note||'';note.setAttribute('aria-label','图片审核意见');card.append(note);
    const buttons=make('div','action-row');
    if(panel){
      buttons.append(action(a.status==='approved'?'撤销通过':'通过并选用',()=>decide(a,a.status==='approved'?'pending':'approved',note)),action('需修改',()=>decide(a,'changes_requested',note)));
      const retry=action('修改参数／生成新版',()=>editImage(a,panel));retry.disabled=!a.regenerable;buttons.append(retry);
    }else{
      const target=make('select');target.setAttribute('aria-label','归档到分镜');
      for(const p of chapterPanels()){const o=make('option','',`${p.page} / ${p.panel}`);o.value=p.id;target.append(o);}card.append(target);
      const bind=action('归档到所选分镜',async()=>{state=await api('comic-review/bind','POST',{job_id:a.job_id,image_index:a.image_index,panel_id:target.value,revision:state.revision});});bind.disabled=!target.options.length;buttons.append(bind);
    }
    buttons.append(action('查看实际提示词与参数',async()=>{const j=await api('jobs/'+a.job_id);const d=make('dialog','preset-dialog');const pre=make('pre','panel-request',JSON.stringify({job_id:j.id,image_index:a.image_index,nai_prompt:j.nai_prompt,positive_prompt:j.positive_prompt,negative_prompt:j.negative_prompt,values:j.values},null,2));const close=make('button','button secondary','关闭');close.onclick=()=>d.close();d.append(close,pre);document.body.append(d);d.onclose=()=>d.remove();d.showModal();}));
    card.append(buttons);return card;
  }
  function render(){
    if(!state)return;document.querySelectorAll('#comic-review-board button,#comic-review-board select').forEach(b=>b.disabled=false);scopes();
    if(!initialized&&state.panels.length){
      let pref;try{pref=JSON.parse(localStorage.getItem('comic-review-location')||'null');}catch{}
      const first=state.panels.find(p=>p.script_version);
      pref=pref||first;
      if(pref)for(const id of ['project','chapter','version','page']){const value=id==='version'?pref.script_version:pref[id];if([...$(id).options].some(o=>o.value===value)){$(id).value=value;scopes();}}
      initialized=true;
    }
    const panels=chapterPanels(),pages=[...new Set(panels.map(p=>p.page))].sort(natural),page=$('page').value;
    const completed=panels.filter(chosen).length;const pagePanels=panels.filter(p=>p.page===page);const visiblePages=[...$('page').options].map(o=>o.value);
    $('summary').textContent=`${$('chapter').selectedOptions[0]?.textContent||'未选话'} · ${$('version').value||'未注明版本'} · 本话已选用 ${completed}/${panels.length} 格 · 当前页 ${pagePanels.filter(chosen).length}/${pagePanels.length} 格`;
    $('prev').disabled=busy||visiblePages.indexOf(page)<=0;$('next').disabled=busy||visiblePages.indexOf(page)<0||visiblePages.indexOf(page)>=visiblePages.length-1;
    const savedEditors=new Map([...document.querySelectorAll('.review-edit')].filter(n=>n.dataset.source).map(n=>[n.id,n]));
    const host=$('panels');host.replaceChildren();
    if(!state.panels.length)host.append(make('p','hint','请先导入分镜台本 JSON，或在下方新建单格。'));
    else if(!pagePanels.length)host.append(make('p','hint','当前页没有分镜，可点击「编辑台本／分页」添加。'));
    for(const p of pagePanels){
      const box=make('section','card review-panel');box.append(make('h2','',`${p.page} / ${p.panel} ${chosen(p)?'✓ 已选用':'· 待完成'}`),make('p','review-brief',p.visible_description||'暂无画面说明'));
      const dialogue=typeof p.dialogue==='string'?p.dialogue:JSON.stringify(p.dialogue||[],null,2);if(dialogue)box.append(make('pre','review-dialogue','对白（后期排字）：\n'+dialogue));
      box.append(action('编辑分镜台本',()=>openScript(p.id)),action('编写提示词／生成首张',()=>showEditor(p)));
      const upload=make('input');upload.type='file';upload.accept='image/png,image/jpeg,image/webp';upload.multiple=true;upload.hidden=true;
      const drop=make('div','review-upload','拖入图片到 '+p.page+' / '+p.panel+'，或点击上传（PNG / JPG / WebP，单张 ≤15MB）');drop.tabIndex=0;drop.setAttribute('role','button');
      drop.onclick=()=>upload.click();drop.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();upload.click();}};drop.ondragover=e=>{e.preventDefault();drop.classList.add('dragover');};drop.ondragleave=()=>drop.classList.remove('dragover');
      const send=files=>run(async()=>{for(const file of files){if(file.size>15*1024*1024)throw Error('单张图片不能超过15MB');const image=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});state=await api('comic-review/upload','POST',{panel_id:p.id,filename:file.name,image,request_id:crypto.randomUUID()});}toast('图片已上传到 '+p.page+' / '+p.panel+'，等待审核');});
      upload.onchange=()=>send([...upload.files]);drop.ondrop=e=>{e.preventDefault();drop.classList.remove('dragover');send([...e.dataTransfer.files]);};box.append(upload,drop);
      if(p.active_jobs?.length)box.append(make('p','hint','生成任务：'+p.active_jobs.map(j=>`${j.state}${j.error?' · '+j.error:''}`).join('；')));
      if(!p.candidates.length)box.append(make('p','hint','此格尚无候选图，可生成首张，或从下方待归档图片中关联。'));
      const grid=make('div','review-workspace'),view=make('div','review-view'),edit=make('div','review-edit');
      const list=[...p.candidates].sort((a,b)=>(b.status==='approved')-(a.status==='approved'));
      const current=list.find(a=>a.job_id+':'+a.image_index===selectedVersions[p.id])||list[0];
      const strip=make('div','review-versions');for(const a of list){const b=action(a.status==='approved'?'✓ 已选用':'查看版本',()=>{selectedVersions[p.id]=a.job_id+':'+a.image_index;});const thumb=make('img');thumb.src=a.url;thumb.loading='lazy';b.prepend(thumb);strip.append(b);}
      edit.id='review-edit-'+p.id;
      if(current){view.append(imageCard(current,p));edit.id='review-edit-'+p.id;edit.append(make('p','hint','点击「修改参数／生成新版」载入此图的实际设置。'));const ref=chosen(p);if(ref&&ref!==current){const compare=make('details');compare.append(make('summary','','并排对比当前选用图'));const pair=make('div','review-compare');for(const [item,label] of [[ref,'已选用'],[current,'当前查看']]){const figure=make('figure');const im=make('img');im.src=item.url;figure.append(im,make('figcaption','',label));pair.append(figure);}compare.append(pair);view.append(compare);}}
      view.append(strip);grid.append(view,edit);box.append(grid);host.append(box);
    }
    for(const [id,node] of savedEditors){const target=document.getElementById(id);if(target&&node.dataset.source===selectedVersions[id.slice('review-edit-'.length)])target.replaceWith(node);}
    renderOrphans();
    $('panels').hidden=activeTab!=='pages';$('library').hidden=activeTab!=='library';$('page-nav').hidden=activeTab!=='pages';
    const nav=$('page-nav');nav.replaceChildren();for(const p of panels){const b=action(`${p.page} / ${p.panel} ${chosen(p)?'✓':'○'}`,()=>{$('page').value=p.page;});const thumb=chosen(p)||p.candidates[0];if(thumb){const im=make('img');im.src=thumb.url;im.loading='lazy';im.alt='';b.prepend(im);}if(p.page===page)b.classList.add('is-current');nav.append(b);}
    if(busy)document.querySelectorAll('#comic-review-board button,#comic-review-board select').forEach(b=>b.disabled=true);
  }

  function scopeInfo(){return {project:$('project').value,chapter:$('chapter').value,script_version:$('version').value};}
  function openScript(panelId){window.ComicScriptEditor.open({api,element:el,toast,state,scope:scopeInfo(),panelId,onSaved:data=>{state=data;render();}});}
  function field(host,label,value,change,type='text'){
    const wrap=make('label','review-field',label),input=make(type==='textarea'?'textarea':'input');
    if(type!=='textarea')input.type=type;else input.rows=3;
    input.value=value??'';input.oninput=()=>change(type==='number'?Number(input.value):input.value);
    input.setAttribute('aria-label',label);wrap.append(input);host.append(wrap);return input;
  }
  async function editImage(a,p){
    const key=a?a.job_id+':'+a.image_index:'panel:'+p.id;selectedVersions[p.id]=key;
    const j=a?await api('jobs/'+a.job_id):{nai_prompt:p.nai_prompt,values:p.values};
    if(!drafts[key]){let saved;try{saved=JSON.parse(localStorage.getItem('review-draft:'+key));}catch{}
      drafts[key]=saved||{nai_prompt:structuredClone(j.nai_prompt||{base_positive:j.positive_prompt||'',base_negative:j.negative_prompt||'',characters:[]}),values:structuredClone(j.values),mode:'txt2img',random_seed:true,strength:.35};}
    render();const host=document.getElementById('review-edit-'+p.id);host.replaceChildren();host.dataset.source=key;
    const d=drafts[key],save=()=>{localStorage.setItem('review-draft:'+key,JSON.stringify(d));};
    host.append(make('h3','',a?'微调当前版本':'当前分镜生成栏'));
    const chatHost=make('div');host.append(chatHost);
    const chatCurrent=()=>({sections:structuredClone(d.chat_sections||{style:{positive:'',negative:''},task:{positive:d.nai_prompt.base_positive,negative:d.nai_prompt.base_negative},scene:{positive:'',negative:''},action:{positive:'',negative:''}}),characters:structuredClone(d.nai_prompt.characters||[]),locked_sections:d.locked_sections||[],locked_characters:(d.nai_prompt.characters||[]).map(c=>c.character_id)});
    window.NaiChat.mount(chatHost,{api,element:el,toast,key:'comic:'+key,getCurrent:chatCurrent,getContext:()=>JSON.stringify({visual:p.visible_description,camera:p.camera_notes,dialogue:p.dialogue}),apply:async result=>{d.chat_sections=structuredClone(result.sections);d.nai_prompt={base_positive:['style','task','scene','action'].map(k=>result.sections[k].positive).filter(x=>x!=='').join('\n'),base_negative:['style','task','scene','action'].map(k=>result.sections[k].negative).filter(x=>x!=='').join('\n'),characters:structuredClone(result.characters)};save();await editImage(a,p);}});
    const mode=make('select');mode.setAttribute('aria-label','生成模式');for(const [v,t] of (a?[['txt2img','修改提示词重新生成'],['img2img','以当前图重绘']]:[['txt2img','生成首张图片']])){const o=make('option','',t);o.value=v;mode.append(o);}mode.value=d.mode;host.append(mode);
    const strength=field(host,'Strength',d.strength,v=>{d.strength=v;save();},'number');strength.min=0;strength.max=1;strength.step=.05;strength.parentElement.hidden=d.mode!=='img2img';
    mode.onchange=()=>{d.mode=mode.value;strength.parentElement.hidden=d.mode!=='img2img';save();};
    const presetTargets=[],presetButtons=[];
    function presetPair(parent,label,object,positiveKey,negativeKey,kind){
      const row=make('div','action-row');
      const button=make('button','text-button',label+' · 选择预设');button.type='button';row.append(button);parent.append(row);
      const positive=field(parent,label,object[positiveKey],v=>{object[positiveKey]=v;if(object===d.nai_prompt)d.chat_sections=null;save();},'textarea');
      const negative=negativeKey=== 'base_negative'
        ?field(parent,'主负面词',object[negativeKey],v=>{object[negativeKey]=v;d.chat_sections=null;save();},'textarea')
        :null;
      positive.id='review-preset-'+p.id+'-'+presetTargets.length;
      if(negative)negative.id=positive.id+'-negative';
      const target={id:positive.id,negativeId:negative?.id,label};
      presetTargets.push(target);presetButtons.push({button,target,kind});return target;
    }
    presetPair(host,'主提示词',d.nai_prompt,'base_positive','base_negative','');
    (d.nai_prompt.characters||[]).forEach((c,i)=>{
      const section=make('details');section.open=true;section.append(make('summary','',`角色 ${i+1}`));
      const character=presetPair(section,`角色 ${i+1} 提示词`,c,'positive',null,'character');
      const direction=presetPair(section,`角色 ${i+1} 动作追加词`,c,'direction',null,'action');
      const neg=field(section,`角色 ${i+1} 负面词`,c.negative,v=>{c.negative=v;save();},'textarea');
      neg.id=character.id+'-negative';character.negativeId=neg.id;direction.negativeId=neg.id;
      host.append(section);
    });
    const presetHost=make('div','review-presets');host.append(presetHost);
    const library=window.PromptPresets.mount(presetHost,{api,element:el,toast,targets:presetTargets,compact:true,onInsert:id=>{if(id===presetTargets[0].id)d.locked_sections=['task'];save();}});
    const picker=presetHost.querySelector('dialog');for(const controls of [...presetHost.children].filter(n=>n.classList.contains('action-row')))picker.append(controls);
    for(const {button,target,kind} of presetButtons)button.onclick=()=>library.open(target.id,kind);
    const params=make('div','review-param-grid');host.append(params);
    for(const [k,t,step,min,max] of [['width','宽度',64,64,2048],['height','高度',64,64,2048],['steps','步数',1,1,50],['cfg_scale','CFG',.1,0,10],['seed','Seed',1,0,4294967295]]){const n=field(params,t,d.values[k],v=>{d.values[k]=v;save();},'number');n.step=step;n.min=min;n.max=max;}
    const random=make('input');random.type='checkbox';random.checked=d.random_seed;random.onchange=()=>{d.random_seed=random.checked;save();};const rl=make('label','check-label','随机种子');rl.prepend(random);host.append(rl);
    const adv=make('details');adv.append(make('summary','','高级参数'));host.append(adv);
    for(const [k,t,choices] of [['MODEL_NAME','模型',['nai-diffusion-5-full','nai-diffusion-5-curated','nai-diffusion-4-5-full','nai-diffusion-4-5-curated','nai-diffusion-4-full','nai-diffusion-4-curated-preview','nai-diffusion-3']],['sampler_name','采样器',['k_euler_ancestral','k_euler','k_dpmpp_2m','k_dpmpp_sde','k_dpmpp_2s_ancestral','ddim_v3']],['scheduler','调度器',['karras','native','exponential','polyexponential']]]){const l=make('label','review-field',t),sel=make('select');for(const v of choices){const o=make('option','',v);o.value=v;sel.append(o);}sel.value=d.values[k];sel.onchange=()=>{d.values[k]=sel.value;save();};l.append(sel);adv.append(l);}
    const rescale=field(adv,'CFG Rescale',d.values.cfg_rescale,v=>{d.values.cfg_rescale=v;save();},'number');rescale.min=0;rescale.max=1;rescale.step=.05;
    const controls=make('div','action-row review-edit-actions');
    controls.append(action(a?'恢复当前图片参数':'恢复分镜原参数',async()=>{delete drafts[key];localStorage.removeItem('review-draft:'+key);await editImage(a,p);}),action(a?'生成新版':'生成首张',async()=>{
      for(const input of host.querySelectorAll('input[type=number]'))if(!input.reportValidity())return;
      const body={...(a?{job_id:a.job_id,image_index:a.image_index}:{panel_id:p.id,panel_revision:p.revision}),...structuredClone({nai_prompt:d.nai_prompt,values:d.values,mode:d.mode,random_seed:d.random_seed,strength:d.strength})};
      const signature=JSON.stringify(body),pendingKey='review-submit:'+key;let pending;try{pending=JSON.parse(localStorage.getItem(pendingKey));}catch{}
      if(pending&&pending.signature!==signature){if(!confirm('上一笔提交尚未确认。请先检查任务记录，确定需要按修改后的内容另建任务吗？'))return;pending=null;}
      if(!pending){pending={signature,request_id:crypto.randomUUID()};localStorage.setItem(pendingKey,JSON.stringify(pending));}
      const job=await api(a?'comic-review/regenerate':'comic-review/generate','POST',{...body,request_id:pending.request_id});localStorage.removeItem(pendingKey);toast('新版已加入生成队列');await reload();
    }));host.append(controls);
  }
  function setupLibrary(){
    const f=$('filters');
    for(const [id,label,type] of [['search','搜索描述／提示词／文件名／编号','search'],['date-from','开始日期','date'],['date-to','结束日期','date']]){const i=field(f,label,'',()=>{},type);i.id='review-'+id;i.oninput=()=>{orphanPage=1;renderOrphans();};}
    for(const [id,label] of [['tag','作品'],['provider','来源'],['model','模型'],['sort','排序'],['size','每页']]){const l=make('label','review-field',label),sel=make('select');sel.id='review-'+id;l.append(sel);f.append(l);sel.onchange=()=>{orphanPage=1;renderOrphans();};}
    for(const [id,values] of [['sort',[['new','最新优先'],['old','最早优先']]],['size',[['12','12 张'],['24','24 张'],['48','48 张']]]])for(const [v,t] of values){const o=make('option','',t);o.value=v;$(id).append(o);}
    const target=make('select');target.id='review-bind-target';target.setAttribute('aria-label','批量归档目标');$('batch').append(target,action('归档所选图片',async()=>{
      if(!target.value||!checked.size){toast('请选择图片及目标页格');return;}
      for(const key of [...checked]){const a=state.unassigned.find(a=>a.job_id+':'+a.image_index===key);if(a){state=await api('comic-review/bind','POST',{job_id:a.job_id,image_index:a.image_index,panel_id:target.value,revision:state.revision});checked.delete(key);}}
      toast('所选图片已归档');
    }),action('清除筛选',()=>{for(const id of ['search','date-from','date-to','tag','provider','model'])$(id).value='';orphanPage=1;}),action('取消选择',()=>checked.clear()));
  }
  function renderOrphans(){
    if(!state)return;
    for(const [id,values] of [['tag',state.unassigned.flatMap(a=>a.work_tags||[])],['provider',state.unassigned.map(a=>a.provider)],['model',state.unassigned.map(a=>a.values?.MODEL_NAME||'')]]){
      const sel=$(id),old=sel.value;sel.replaceChildren();const all=make('option','','全部');all.value='';sel.append(all);if(id==='tag'){const o=make('option','','未分类');o.value='__none__';sel.append(o);}for(const v of [...new Set(values)].filter(Boolean).sort(natural)){const o=make('option','',v);o.value=v;sel.append(o);}sel.value=[...sel.options].some(o=>o.value===old)?old:'';
    }
    const target=$('bind-target'),old=target.value;target.replaceChildren();for(const p of chapterPanels()){const o=make('option','',`${p.project} / ${p.chapter} / ${p.page} / ${p.panel}`);o.value=p.id;target.append(o);}if([...target.options].some(o=>o.value===old))target.value=old;
    const q=$('search').value.toLowerCase(),tag=$('tag').value;
    const day=a=>{const t=new Date(a.created_at);return isNaN(t)?'':new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(t);};
    const list=state.unassigned.filter(a=>(!q||[a.description,a.search_prompt,a.filename,a.job_id].join(' ').toLowerCase().includes(q))&&(!tag||(tag==='__none__'?!(a.work_tags||[]).length:(a.work_tags||[]).includes(tag)))&&(!$('provider').value||a.provider===$('provider').value)&&(!$('model').value||a.values?.MODEL_NAME===$('model').value)&&(!$('date-from').value||day(a)>=$('date-from').value)&&(!$('date-to').value||day(a)<=$('date-to').value));
    list.sort((a,b)=>((Date.parse(b.created_at)||0)-(Date.parse(a.created_at)||0))*($('sort').value==='old'?-1:1)||natural(a.job_id,b.job_id)||a.image_index-b.image_index);
    const size=Number($('size').value)||12,last=Math.max(1,Math.ceil(list.length/size));orphanPage=Math.max(1,Math.min(orphanPage,last));
    $('orphan-count').textContent=`待归档图片 · 筛选 ${list.length} / 共 ${state.unassigned.length} · 已选 ${checked.size}`;
    const host=$('orphans');host.replaceChildren();for(const a of list.slice((orphanPage-1)*size,orphanPage*size))host.append(imageCard(a,null));if(!list.length)host.append(make('p','hint','没有符合条件的图片。'));
    $('orphan-page').textContent=`${orphanPage} / ${last}`;$('orphan-prev').disabled=orphanPage<=1;$('orphan-next').disabled=orphanPage>=last;
  }
  async function init(deps){
    ({api,element:el,toast}=deps);const host=document.getElementById('comic-review-board');
    host.innerHTML='<div class="card"><div class="review-toolbar"><label>项目<select id="review-project"></select></label><label>话<select id="review-chapter"></select></label><label>台本版本<select id="review-version"></select></label><label>页<select id="review-page"></select></label></div><div class="action-row" id="review-actions"></div><label class="check-label"><input type="checkbox" id="review-unfinished">只看未完成页</label><p id="review-summary" class="hint" role="status" aria-live="polite"></p><div class="character-pagination"><button id="review-prev" class="text-button">上一页</button><button id="review-next" class="text-button">下一页</button></div></div><div class="action-row"><button type="button" id="review-tab-pages" class="button">按页选片</button><button type="button" id="review-tab-library" class="button secondary">待归档图库</button></div><div class="review-page-layout"><nav id="review-page-nav"></nav><div id="review-panels"></div></div><section class="card" id="review-library"><h2 id="review-orphan-count">待归档图片</h2><div id="review-filters" class="review-toolbar"></div><div id="review-batch" class="action-row"></div><p class="hint">不会根据文件名猜测归属。选好话与台本版本，再关联到具体页格。</p><div id="review-orphans" class="review-candidates"></div><div class="character-pagination"><button id="review-orphan-prev" class="text-button">上一批</button><span id="review-orphan-page"></span><button id="review-orphan-next" class="text-button">下一批</button></div></section>';
    const file=make('input');file.type='file';file.accept='.json,application/json';file.hidden=true;host.append(file);
    file.onchange=()=>run(async()=>{if(!file.files[0])return;if(file.files[0].size>900000)throw Error('台本文件过大，请按话导入（小于900KB）。');const data=JSON.parse(await file.files[0].text());if(!confirm('导入此台本的页格？同版本已有分镜保留，不覆盖。'))return;state=await api('comic-review/import','POST',data);file.value='';});
    $('actions').append(action('编辑台本／分页',()=>openScript()),action('导出 Skill 分镜包',()=>{const link=make('a');link.href='/api/comic-editor/export?'+new URLSearchParams(scopeInfo());link.download='comic-storyboard.zip';link.click();}),action('导入台本 JSON',()=>file.click()),action('刷新',reload),action('导出本话选用清单',async()=>{const all=await api('comic-review/selection');const matches=p=>p.project===$('project').value&&p.chapter===$('chapter').value&&String(p.script_version||'')===$('version').value;all.selected=all.selected.filter(matches);all.missing=all.missing.filter(matches);const blob=new Blob([JSON.stringify(all,null,2)],{type:'application/json'});const a=make('a');a.href=URL.createObjectURL(blob);a.download=`选用清单-${$('chapter').value}-${$('version').value}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}));
    const remember=()=>localStorage.setItem('comic-review-location',JSON.stringify({project:$('project').value,chapter:$('chapter').value,script_version:$('version').value,page:$('page').value}));
    for(const id of ['project','chapter','version','page','unfinished'])$(id).onchange=()=>{render();remember();};
    const step=d=>{const s=$('page'),opts=[...s.options].map(o=>o.value);const i=opts.indexOf(s.value)+d;if(i>=0&&i<opts.length){s.value=opts[i];render();remember();}};
    $('prev').onclick=()=>step(-1);$('next').onclick=()=>step(1);
    $('orphan-prev').onclick=()=>{orphanPage--;render();};$('orphan-next').onclick=()=>{orphanPage++;render();};
    setupLibrary();
    $('tab-pages').onclick=()=>{activeTab='pages';render();};$('tab-library').onclick=()=>{activeTab='library';render();};
    await reload();
  }
  async function open(){await reload();clearInterval(timer);timer=setInterval(async()=>{if(!busy&&!document.activeElement?.closest('#comic-review-board textarea,#comic-review-board input')&&document.getElementById('page-panels').classList.contains('active')&&state.panels.some(p=>p.active_jobs?.some(j=>!['failed','unknown','interrupted'].includes(j.state)))){try{await reload();}catch{}}},5000);}
  async function restoreJob(job){await reload();const p=state.panels.find(p=>p.id===job.panel_id);if(!p)throw Error('该分镜已删除，请从待归档图库找回图片。');for(const id of ['project','chapter','version','page']){$(id).value=id==='version'?p.script_version:p[id];scopes();}render();const a=p.candidates.find(a=>a.job_id===job.id);await editImage(a||null,p);}
  return {init,open,restoreJob};
})();
