'use strict';
window.PromptSections = {mount(host,{api,element:node,toast}) {
  const parts=[['style','画风'],['task','任务／构图'],['character','人物'],['scene','场景'],['action','动作']];
  const fields={}, selectors={}, selections={}, chips={}, priorities={}, key='studio.nai.prompt-sections.v1';
  let active=false, records=[], manager, characterCards;
  const join=values=>values.filter(v=>v!=='').join('\n');
  const orderedParts=()=>parts.filter(([id])=>id!=='character').slice().sort((a,b)=>Number(priorities[a[0]].value)-Number(priorities[b[0]].value));
  function snapshot(){return Object.fromEntries(parts.map(([id])=>[id,{positive:fields[id].positive.value,negative:fields[id].negative.value,selected:selections[id],...(id==='character'?{cards:characterCards.snapshot()}:{priority:Number(priorities[id].value)})}]));}
  function sync(){
    if(!active)return;
    document.getElementById('positive-prompt').value=join([...orderedParts().map(([id])=>fields[id].positive.value),...characterCards.preview().map(c=>c.positive)]);
    document.getElementById('negative-prompt').value=join([...orderedParts().map(([id])=>fields[id].negative.value),...characterCards.preview().map(c=>c.negative)]);
    try{localStorage.setItem(key,JSON.stringify(snapshot()));}catch{toast('浏览器无法保存分段草稿，本次编辑仍可使用。',true);}
  }
  function button(parent,title,fn){const b=node('button','button secondary',title);b.type='button';b.onclick=()=>Promise.resolve().then(fn).catch(e=>toast(e.message,true));parent.append(b);return b;}
  function removeBlock(value, block) {
    if(!block)return value;
    const start=value.indexOf(block);if(start<0)return value;
    let end=start+block.length,from=start;
    if(value[end]==='\n')end++;else if(from>0&&value[from-1]==='\n')from--;
    return value.slice(0,from)+value.slice(end);
  }
  function removeSelection(id,chosen) {
    for(const polarity of ['positive','negative']) {
      const input=fields[id][polarity];
      input.value=id==='character'?removeBlock(input.value,chosen[polarity]):'';
    }
    selections[id]=selections[id].filter(r=>r.id!==chosen.id);fillChoices();sync();
  }
  function fillChoices(){for(const [id] of parts){
    const select=selectors[id];select.replaceChildren();
    const empty=node('option','',id==='character'?'添加人物（可多选）…':'不使用预设／清除选择');empty.value='';select.append(empty);
    for(const r of records.filter(r=>r.category===id)){const option=node('option','',r.name);option.value=r.id;option.disabled=id==='character'&&selections[id].some(s=>s.id===r.id);select.append(option);}
    if(id!=='character'&&selections[id][0]) {
      const chosen=selections[id][0];if(!records.some(r=>r.id===chosen.id)){const option=node('option','',chosen.name+'（已保存草稿）');option.value=chosen.id;select.append(option);}select.value=chosen.id;
    }
    chips[id].replaceChildren();for(const chosen of selections[id]){const chip=button(chips[id],chosen.name+' ×',()=>removeSelection(id,chosen));chip.setAttribute('aria-label','移除'+chosen.name);}
  }}
  async function reload(){const [presets,characters]=await Promise.all([api('prompt-presets'),api('characters')]);records=[...presets,...characters.filter(c=>c.enabled!==false).map(c=>({id:'library:'+c.id,category:'character',name:'人物库 · '+c.name,text:join([c.appearance||'',c.clothing||'']),negative_text:c.negative_prompt||''}))];fillChoices();characterCards.setPresets(records.filter(r=>r.category==='character'));}
  host.append(node('p','hint','画风、任务、场景、动作各选一个预设，换选会替换该段。人物在独立卡片中编辑，可添加、停用或移除。正负词分别组合。'));
  host.append(node('p','hint','优先级数字越小，公共提示词排列越靠前；同级保持原顺序。画风默认最高。此设置仅调整顺序，不改写权重，不保证模型严格按等级执行。人物仍独立发送，不参与公共词排序。'));
  for(const [id,title] of parts){
    const section=node('section','prompt-section');section.dataset.section=id;section.append(node('h3','',title+'提示词'));host.append(section);
    if(id!=='character'){
      const label=node('label','prompt-priority',title+' · 排列优先级'),priority=node('select');priority.id='nai-section-'+id+'-priority';label.htmlFor=priority.id;priority.setAttribute('aria-label',title+'排列优先级');
      for(const [value,text] of [['1','1 · 最高'],['2','2 · 较高'],['3','3 · 普通'],['4','4 · 较低'],['5','5 · 最低']]){const option=node('option','',text);option.value=value;priority.append(option);}
      priority.value=id==='style'?'1':'3';priority.onchange=sync;priorities[id]=priority;label.append(priority);section.append(label);
    }
    const select=node('select');select.setAttribute('aria-label',title+'预设');selectors[id]=select;section.append(select);
    fields[id]={};selections[id]=[];chips[id]=node('div','preset-picks');section.append(chips[id]);
    for(const [polarity,label] of [['positive','正面'],['negative','负面']]){const box=node('label','panel-field',title+' · '+label),input=node('textarea');input.id='nai-section-'+id+'-'+polarity;input.rows=polarity==='positive'?3:2;box.htmlFor=input.id;box.append(input);section.append(box);fields[id][polarity]=input;input.addEventListener('input',sync);}
    select.onchange=()=>{
      const r=records.find(r=>r.id===select.value);
      if(!r){if(id!=='character'){fields[id].positive.value='';fields[id].negative.value='';selections[id]=[];}fillChoices();sync();return;}
      const chosen={id:r.id,name:r.name,positive:r.text||'',negative:r.negative_text||''};
      if(id==='character'){
        if(selections[id].some(s=>s.id===r.id))return;
        fields[id].positive.value=join([fields[id].positive.value,chosen.positive]);fields[id].negative.value=join([fields[id].negative.value,chosen.negative]);selections[id].push(chosen);
      }else{fields[id].positive.value=chosen.positive;fields[id].negative.value=chosen.negative;selections[id]=[chosen];}
      fillChoices();sync();
    };
    const actions=node('div','action-row');section.append(actions);
    button(actions,'管理／编辑预设',()=>manager.open(fields[id].positive.id,id));
    button(actions,'保存本段为预设',()=>manager.save(fields[id].positive.id,id));
  }
  const characterSection=host.querySelector('[data-section=character]');
  for(const child of [...characterSection.children])child.hidden=true;
  characterSection.append(node('h3','','角色提示词'));
  characterCards=window.CharacterPrompts.mount(characterSection,{element:node,onchange:sync,toast});
  const library=node('div','section-preset-manager');host.append(library);
  manager=window.PromptPresets.mount(library,{api,element:node,toast,compact:true,managerOnly:true,targets:parts.map(([id,title])=>({id:fields[id].positive.id,negativeId:fields[id].negative.id,label:title}))});
  button(characterSection,'管理角色预设',()=>manager.open(fields.character.positive.id,'character'));
  library.addEventListener('close',()=>reload().catch(e=>toast(e.message,true)),true);
  try{const saved=JSON.parse(localStorage.getItem(key)||'null');if(saved)for(const [id] of parts){
    if(id!=='character'&&[1,2,3,4,5].includes(saved[id]?.priority))priorities[id].value=String(saved[id].priority);
    for(const polarity of ['positive','negative'])fields[id][polarity].value=typeof saved[id]?.[polarity]==='string'?saved[id][polarity]:'';
    selections[id]=Array.isArray(saved[id]?.selected)?saved[id].selected.filter(r=>typeof r.id==='string'&&typeof r.name==='string'&&typeof r.positive==='string'&&typeof r.negative==='string'):[];
    if(id!=='character')selections[id]=selections[id].slice(0,1);
    else if(Array.isArray(saved[id]?.cards))characterCards.load(saved[id].cards);
    else if(fields[id].positive.value||fields[id].negative.value)characterCards.load([{name:'原人物草稿（可手动拆分）',positive:fields[id].positive.value,negative:fields[id].negative.value}]);
  }}catch{toast('分段草稿无法读取，已保留浏览器原记录。',true);}
  reload().catch(e=>toast(e.message,true));
  return {sync,chatContext(){return {sections:Object.fromEntries(orderedParts().map(([id])=>[id,{positive:fields[id].positive.value,negative:fields[id].negative.value}])),characters:characterCards.prompt(),locked_sections:orderedParts().filter(([id])=>selections[id].length).map(([id])=>id),locked_characters:characterCards.prompt().map(c=>c.character_id),ui_snapshot:snapshot()};},applyChat(data){if(data.ui_snapshot){for(const [id] of parts){const item=data.ui_snapshot[id];if(id==='character'){characterCards.load(item.cards||[]);}else{fields[id].positive.value=item.positive;fields[id].negative.value=item.negative;selections[id]=item.selected;priorities[id].value=item.priority;}}}else{for(const [id] of orderedParts()){fields[id].positive.value=data.sections[id].positive;fields[id].negative.value=data.sections[id].negative;if(!selections[id].some(s=>s.positive===fields[id].positive.value&&s.negative===fields[id].negative.value))selections[id]=[];}const oldCards=characterCards.snapshot();characterCards.load(data.characters.map(c=>({...oldCards.find(old=>old.id===c.character_id),...c,id:c.character_id})));}fillChoices();sync();},structured(){return {base_positive:join(orderedParts().map(([id])=>fields[id].positive.value)),base_negative:join(orderedParts().map(([id])=>fields[id].negative.value)),characters:characterCards.prompt()};},activate(enabled){active=enabled;for(const id of ['positive-prompt','negative-prompt']){const input=document.getElementById(id);input.readOnly=enabled;document.querySelector('label[for="'+id+'"]').textContent=enabled?(id==='positive-prompt'?'完整正面 Prompt（自动组合）':'完整负面 Prompt（自动组合）'):(id==='positive-prompt'?'正面提示词':'负面提示词');}if(enabled)sync();},importFull(positive,negative,characters=[]){characterCards.load(characters);for(const [id] of parts){selections[id]=[];fields[id].positive.value=id==='task'?positive||'':'';fields[id].negative.value=id==='task'?negative||'':'';}fillChoices();sync();}};
}};
