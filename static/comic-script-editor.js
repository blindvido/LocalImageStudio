'use strict';
window.ComicScriptEditor=(()=>{
 function open({api,element:el,toast,state,scope,panelId,onSaved}){
  const match=p=>['project','chapter','script_version'].every(k=>(p[k]||'')===scope[k]);
  const records=state.panels.filter(match),names=[...new Set([...records,...(state.page_catalog||[]).filter(match)].map(p=>p.page))].sort((a,b)=>a.localeCompare(b,'zh-CN',{numeric:true}));
  let pages=names.map(page=>({panels:structuredClone(records.filter(p=>p.page===page))}));
  const key='comic-script-draft:'+JSON.stringify(scope);let dirty=false,saving=false;
  const dialog=el('dialog','preset-dialog script-editor');dialog.setAttribute('aria-label','台本与分页编辑');
  const title=el('h2','',`编辑台本 · ${scope.project} / ${scope.chapter} / ${scope.script_version}`),intro=el('p','hint','页格按阅读顺序自动编号。移动分镜保留图片关联；删除只移除台本，图片退回待归档。点击保存后生效。');
  const toolbar=el('div','action-row'),host=el('div','script-pages'),footer=el('div','action-row script-footer'),status=el('span','hint','尚未修改');
  dialog.append(title,intro,toolbar,host,footer);document.body.append(dialog);
  function change(){dirty=true;status.textContent='有未保存修改';localStorage.setItem(key,JSON.stringify({revision:state.revision,pages}));}
  function button(parent,label,fn){const b=el('button','button secondary',label);b.type='button';b.onclick=()=>{if(!saving)fn();};parent.append(b);return b;}
  function input(parent,label,value,set,area=false){const l=el('label','review-field',label),n=el(area?'textarea':'input');n.value=value||'';n.setAttribute('aria-label',label);if(area)n.rows=3;n.oninput=()=>{set(n.value);change();};l.append(n);parent.append(l);return n;}
  function edit(fn){fn();change();draw();}
  function removePanel(page,p){const source=records.find(r=>r.id===p.id);const count=source?.candidates?.length||0;if(confirm(`删除此分镜？${count} 张候选图片将保留并退回待归档。`))edit(()=>page.panels.splice(page.panels.indexOf(p),1));}
  function draw(){host.replaceChildren();pages.forEach((page,i)=>{
   const section=el('details','card script-page');section.dataset.page=i;section.open=page.panels.some(p=>p.id===panelId)||i===0||i===pages.length-1;section.append(el('summary','',`P${String(i+1).padStart(2,'0')} · ${page.panels.length} 格`));const heading=el('div','action-row');
   button(heading,'在前面插入页',()=>edit(()=>pages.splice(i,0,{panels:[]})));
   const up=button(heading,'页前移',()=>edit(()=>{[pages[i-1],pages[i]]=[pages[i],pages[i-1]];}));up.disabled=i===0;
   const down=button(heading,'页后移',()=>edit(()=>{[pages[i+1],pages[i]]=[pages[i],pages[i+1]];}));down.disabled=i===pages.length-1;
   button(heading,'删除此页',()=>{const count=page.panels.reduce((n,p)=>n+(records.find(r=>r.id===p.id)?.candidates?.length||0),0);if(confirm(`删除整页和 ${page.panels.length} 格？${count} 张图片保留并退回待归档。`))edit(()=>pages.splice(i,1));});section.append(heading);
   page.panels.forEach((p,j)=>{
    const card=el('article','script-panel');if(p.id)card.dataset.panelId=p.id;
    const top=el('div','action-row');top.append(el('h4','',`K${String(j+1).padStart(2,'0')}`));
    const up=button(top,'格上移',()=>edit(()=>{[page.panels[j-1],page.panels[j]]=[page.panels[j],page.panels[j-1]];}));up.disabled=j===0;
    const down=button(top,'格下移',()=>edit(()=>{[page.panels[j+1],page.panels[j]]=[page.panels[j],page.panels[j+1]];}));down.disabled=j===page.panels.length-1;
    const move=el('select');move.setAttribute('aria-label','移动分镜到其他页');const empty=el('option','','移动到其他页…');empty.value='';move.append(empty);pages.forEach((_,n)=>{if(n!==i){const o=el('option','',`P${String(n+1).padStart(2,'0')}`);o.value=n;move.append(o);}});move.onchange=()=>{if(move.value!=='')edit(()=>{pages[Number(move.value)].panels.push(p);page.panels.splice(j,1);});};top.append(move);button(top,'删除分镜',()=>removePanel(page,p));card.append(top);
    input(card,'镜头标题',p.title,v=>p.title=v);input(card,'画面描述',p.visible_description,v=>p.visible_description=v,true);
    input(card,'镜头／视图要求',p.camera_notes,v=>p.camera_notes=v,true);input(card,'台词留白／气泡排版要求',p.lettering_notes,v=>p.lettering_notes=v,true);
    input(card,'制作备注',p.private_notes,v=>p.private_notes=v,true);
    const lines=el('div','script-lines');card.append(el('h4','','对白／旁白／音效'),lines);
    if(typeof p.dialogue==='string'){
     input(lines,'原台词文本',p.dialogue,v=>p.dialogue=v,true);
     button(lines,'将原文转为一条旁白',()=>edit(()=>p.dialogue=p.dialogue?[{speaker:'',type:'caption',text:p.dialogue}]:[]));
    }else{
     p.dialogue=p.dialogue||[];p.dialogue.forEach((line,k)=>{
      const row=el('div','script-line'),type=el('select');type.setAttribute('aria-label','台词类型');for(const [value,label] of [['dialogue','对白'],['caption','旁白'],['sfx','音效']]){const o=el('option','',label);o.value=value;type.append(o);}type.value=line.type||'dialogue';type.onchange=()=>{line.type=type.value;change();};row.append(type);
      input(row,'说话者',line.speaker,v=>line.speaker=v);input(row,'台词内容',line.text,v=>line.text=v,true);
      const up=button(row,'台词上移',()=>edit(()=>{[p.dialogue[k-1],p.dialogue[k]]=[p.dialogue[k],p.dialogue[k-1]];}));up.disabled=k===0;
      button(row,'删除台词',()=>edit(()=>p.dialogue.splice(k,1)));lines.append(row);
     });button(lines,'新增台词',()=>edit(()=>p.dialogue.push({speaker:'',type:'dialogue',text:''})));
    }
    section.append(card);
   });button(section,'＋ 新增分镜',()=>edit(()=>page.panels.push({title:'',visible_description:'',camera_notes:'',lettering_notes:'',private_notes:'',dialogue:[]})));host.append(section);
  });if(!pages.length)host.append(el('p','hint','还没有页面，请新增下一页。'));}
  button(toolbar,'＋ 新增下一页',()=>edit(()=>pages.push({panels:[]})));
  let draft;try{draft=JSON.parse(localStorage.getItem(key));}catch{}
  if(draft){button(toolbar,'恢复未保存草稿',()=>{if(draft.revision!==state.revision){toast('台本已更新，旧草稿无法直接恢复，请核对最新台本。',true);return;}pages=structuredClone(draft.pages);change();draw();});}
  function close(){if(saving)return;if(dirty&&!confirm('台本尚未保存，关闭编辑？草稿保留在本机。'))return;dialog.close();}
  button(footer,'关闭',close);
  const save=button(footer,'保存台本与分页',async()=>{
   saving=true;save.disabled=true;status.textContent='正在保存…';
   try{const minimal=pages.map(page=>({panels:page.panels.map(p=>Object.fromEntries(['id','revision','title','visible_description','camera_notes','lettering_notes','private_notes','dialogue'].filter(k=>p[k]!==undefined).map(k=>[k,p[k]])))}));const data=await api('comic-editor/save','POST',{...scope,revision:state.revision,pages:minimal});dirty=false;localStorage.removeItem(key);onSaved(data);toast('台本与分页已保存');dialog.close();}catch(e){status.textContent='保存失败，草稿已保留';toast(e.message,true);}finally{saving=false;save.disabled=false;}
  });footer.append(status);
  const leave=e=>{if(dirty){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',leave);
  dialog.oncancel=e=>{e.preventDefault();close();};dialog.onclose=()=>{window.removeEventListener('beforeunload',leave);dialog.remove();};
  draw();dialog.showModal();if(panelId)dialog.querySelector(`[data-panel-id="${CSS.escape(panelId)}"]`)?.scrollIntoView({block:'start'});
 }
 return {open};
})();
