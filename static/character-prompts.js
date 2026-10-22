'use strict';
window.CharacterPrompts={mount(host,{element:node,onchange,toast}){
  let cards=[],presets=[];
  const list=node('div','character-prompt-list');
  const add=node('button','button secondary','＋ 添加角色');add.type='button';host.append(add,list);
  host.append(node('p','hint','最多五名角色；各自的提示词、追加词和负面词独立提交。暂用画面中心位置。'));
  function button(parent,label,fn){const b=node('button','text-button',label);b.type='button';b.onclick=fn;parent.append(b);return b;}
  function draw(){
    list.replaceChildren();add.disabled=cards.length>=5;
    cards.forEach((c,index)=>{
      const box=node('section','character-prompt-card');box.dataset.characterId=c.id;list.append(box);
      const head=node('div','card-heading');head.append(node('strong','',`角色 ${index+1} · ${c.name||'自定义'}`));box.append(head);
      const tools=node('div','character-card-tools');head.append(tools);
      button(tools,'↑',()=>{[cards[index-1],cards[index]]=[cards[index],cards[index-1]];draw();onchange();}).disabled=index===0;
      button(tools,'↓',()=>{[cards[index],cards[index+1]]=[cards[index+1],cards[index]];draw();onchange();}).disabled=index===cards.length-1;
      button(tools,c.enabled?'停用':'启用',()=>{c.enabled=!c.enabled;draw();onchange();});
      button(tools,'移除',()=>{cards=cards.filter(r=>r!==c);draw();onchange();});
      box.classList.toggle('is-disabled',!c.enabled);
      const select=node('select');select.setAttribute('aria-label',`角色 ${index+1} 预设`);const empty=node('option','','选择角色预设（替换本角色）');empty.value='';select.append(empty);
      for(const p of presets){const option=node('option','',p.name);option.value=p.id;select.append(option);}box.append(select);
      select.onchange=()=>{const p=presets.find(p=>p.id===select.value);if(!p)return;c.name=p.name;c.positive=p.text||'';c.negative=p.negative_text||'';draw();onchange();};
      const tabs=node('div','character-tabs');tabs.setAttribute('role','tablist');box.append(tabs);
      const panels=[];
      for(const [key,label] of [['positive','角色提示词'],['direction','角色追加词'],['negative','角色负面词']]){
        const tab=button(tabs,label,()=>{c.tab=key;for(const [k,b,p] of panels){b.setAttribute('aria-selected',String(k===key));p.hidden=k!==key;}});tab.setAttribute('role','tab');
        const panel=node('div');panel.setAttribute('role','tabpanel');const input=node('textarea');input.rows=5;input.value=c[key]||'';input.setAttribute('aria-label',`角色 ${index+1} ${label}`);input.dataset.characterField=key;panel.append(input);box.append(panel);
        input.oninput=()=>{c[key]=input.value;onchange();};tab.setAttribute('aria-selected',String((c.tab||'positive')===key));panel.hidden=(c.tab||'positive')!==key;panels.push([key,tab,panel]);
      }
    });
  }
  add.onclick=()=>{if(cards.length>=5)return;cards.push({id:crypto.randomUUID(),name:'自定义',positive:'',direction:'',negative:'',enabled:true});draw();onchange();};
  return {snapshot:()=>cards.map(c=>({...c})),setPresets(items){presets=items;draw();},load(items){cards=items.slice(0,5).map(c=>({id:c.id||c.character_id||crypto.randomUUID(),name:c.name||'自定义',positive:c.positive||'',direction:c.direction||'',negative:c.negative||'',enabled:c.enabled!==false,tab:c.tab||'positive'}));draw();},prompt(){const enabled=cards.filter(c=>c.enabled);for(const c of enabled)if(!c.positive.trim())throw Error('请填写启用角色的提示词，或将该角色停用。');return enabled.map(c=>({character_id:c.id,positive:c.positive,direction:c.direction,negative:c.negative,center:{x:.5,y:.5}}));},preview(){return cards.filter(c=>c.enabled).map(c=>({positive:[c.positive,c.direction].filter(Boolean).join('\n'),negative:c.negative}));}};
}};

// Add clear controls to editable prompt fields, including dynamically created cards.
window.PromptClearButtons={install(){
  function scan(){for(const input of document.querySelectorAll('.prompt-section textarea,.preset-dialog textarea,#positive-prompt,#negative-prompt')){
    if(input.readOnly||input.closest('[hidden]'))continue;if(input.dataset.clearButton)continue;input.dataset.clearButton='true';
    const row=document.createElement('div');row.className='prompt-clear-row';const button=document.createElement('button');button.type='button';button.className='text-button';button.textContent='清空';button.setAttribute('aria-label','清空 '+(input.getAttribute('aria-label')||input.id||'提示词'));row.append(button);if(input.parentElement.tagName==='LABEL')input.parentElement.before(row);else input.before(row);
    button.onclick=()=>{input.value='';input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();};
  }
  for(const row of document.querySelectorAll('.prompt-clear-row')){const hidden=!!(row.nextElementSibling?.readOnly||row.nextElementSibling?.hidden);if(row.hidden!==hidden)row.hidden=hidden;}
  }
  scan();new MutationObserver(scan).observe(document.querySelector('main'),{childList:true,subtree:true,attributes:true,attributeFilter:['readonly','hidden']});
}};

