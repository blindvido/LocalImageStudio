'use strict';
(function(root) {
  // Preserve every preset byte; only add a newline between existing text and records.
  function combine(existing, texts) {
    return [existing, ...texts].filter(x => x !== '').join('\n');
  }
  if (typeof module !== 'undefined') module.exports = {combine};
  if (!root.document) return;
  const categories = [['style','画师串／画风'],['task','任务／构图'],['scene','场景'],['character','人物'],['action','动作']];
  root.PromptPresets = {mount(host, {api, element:node, toast, targets, compact=false, managerOnly=false,onInsert=()=>{}}) {
    let dialog, editorDialog;
    function openEditor() { editor.open=true; if(editorDialog) { editorDialog.showModal(); } else { details.open=true; } }
    let records = [], selected = new Set(), editing = '', loading = false;
    const details = node('details', 'card'); details.append(node('summary','','提示词预设库')); host.append(details);
    const quick=node('div','preset-quick'), picks=node('div','preset-picks');
    if(compact) { host.prepend(quick);host.append(picks); details.querySelector('summary').textContent='管理预设 · 新增、编辑、搜索'; }
    const hint = node('p','hint','多选后按勾选顺序组合，正负词分别追加到对应输入框；原文保留，不调用提示词 API。'); details.append(hint);if(managerOnly)hint.textContent='在此新增或编辑预设，关闭后在对应分栏中选择。人物库内容可另存为预设。';
    function field(label, tag='input') {
      const box=node('label','panel-field',label), input=node(tag); box.append(input);details.append(box);return input;
    }
    function choices(select, items) {
      for(const [value,text] of items) {const option=node('option','',text);option.value=value;select.append(option);}
    }
    const search=field('搜索名称或内容'), filter=field('分类','select'); choices(filter,[['','全部'],...categories]);
    const list=node('div','preset-list'); details.append(list);
    const target=field('插入到','select');choices(target,targets.map(t=>[t.id,t.label]));
    const preview=field('组合后正面 Prompt（只读）','textarea');preview.rows=5;preview.readOnly=true;
    const negativePreview=field('组合后负面 Prompt（只读）','textarea');negativePreview.rows=3;negativePreview.readOnly=true;
    function updatePreview() {
      const destination=document.getElementById(target.value);
      const negativeDestination=document.getElementById(targets.find(t=>t.id===target.value)?.negativeId);
      negativePreview.value=combine(negativeDestination?.value||'',[...selected].map(id=>records.find(r=>r.id===id)?.negative_text||''));
      preview.value=combine(destination?.value || '', [...selected].map(id=>records.find(r=>r.id===id)?.text).filter(x=>x!==undefined));
    }
    function draw() {
      list.replaceChildren();
      if(compact) {
        quick.replaceChildren();picks.replaceChildren();
        for(const [key,title] of categories) {
          const label=node('label','panel-field',title),select=node('select');
          select.setAttribute('aria-label',title+'预设');
          choices(select,[['','选择预设…'],...records.filter(r=>r.category===key).map(r=>[r.id,r.name])]);
          select.onchange=()=>{if(select.value)selected.add(select.value);draw();};label.append(select);quick.append(label);
        }
        for(const id of selected) { const r=records.find(r=>r.id===id);if(!r)continue;
          const chip=node('button','button secondary',r.name+' ×');chip.type='button';chip.onclick=()=>{selected.delete(id);draw();};picks.append(chip);
        }
      }
      const query=search.value.toLocaleLowerCase();
      for(const r of records.filter(r=>(!filter.value||r.category===filter.value)&&(!query||(r.name+'\n'+r.text+'\n'+(r.negative_text||'')).toLocaleLowerCase().includes(query)))) {
        const row=node('div','preset-row');const label=node('label','check-label',`${categories.find(c=>c[0]===r.category)[1]} · ${r.name}`);
        const check=node('input');check.type='checkbox';check.checked=selected.has(r.id);
        check.onchange=()=>{if(check.checked)selected.add(r.id);else selected.delete(r.id);draw();};if(!managerOnly)label.prepend(check);row.append(label);
        const edit=node('button','button secondary',r.library?'另存为预设':'编辑');edit.type='button';edit.onclick=()=>{editing=r.library?'':r.id;name.value=r.library?r.name.replace('人物库 · ',''):r.name;category.value=r.category;text.value=r.text;negativeText.value=r.negative_text||'';openEditor();};row.append(edit);list.append(row);
      }
      if(!list.childNodes.length)list.append(node('p','hint','没有匹配的预设。'));
      updatePreview();
    }
    async function reload() {
      const [presets,characters]=await Promise.all([api('prompt-presets'),api('characters')]);
      records=[...presets,...characters.filter(c=>c.enabled!==false).map(c=>({
        id:'library:'+c.id,name:'人物库 · '+c.name,category:'character',
        text:combine(c.appearance||'',[c.clothing||'']),negative_text:c.negative_prompt||'',library:true
      }))];selected=new Set([...selected].filter(id=>records.some(r=>r.id===id)));draw();}
    function button(parent,title,fn) {
      const b=node('button','button secondary',title);b.type='button';b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){toast(e.message,true);}finally{b.disabled=false;}};parent.append(b);return b;
    }
    const actions=node('div','action-row');details.append(actions);
    button(actions,'追加所选预设',()=>{
      if(!selected.size)throw new Error('请先勾选预设。');
      const destination=document.getElementById(target.value);
      if(!destination||destination.disabled)throw new Error('目标输入框暂不可编辑。');
      const targetInfo=targets.find(t=>t.id===target.value);
      if(targetInfo.enabled&&!targetInfo.enabled())throw new Error('请先为此人物槽绑定角色。');
      const negativeDestination=document.getElementById(targetInfo.negativeId);
      if([...selected].some(id=>records.find(r=>r.id===id)?.negative_text)&&(!negativeDestination||negativeDestination.disabled))throw new Error('对应负面输入框暂不可编辑。');
      updatePreview();if(negativeDestination){negativeDestination.value=negativePreview.value;negativeDestination.dispatchEvent(new Event('input',{bubbles:true}));}destination.value=preview.value;destination.dispatchEvent(new Event('input',{bubbles:true}));onInsert(target.value);selected.clear();draw();if(dialog?.open)dialog.close();toast('已原样追加到输入框。');
    });
    button(actions,'将目标输入框保存为新预设',()=>{editing='';name.value='';text.value=document.getElementById(target.value)?.value||'';negativeText.value=document.getElementById(targets.find(t=>t.id===target.value)?.negativeId)?.value||'';openEditor();name.focus();});
    const editor=node('details');editor.append(node('summary','','新增／编辑预设'));details.append(editor);
    function editField(title,tag='input') {const label=node('label','panel-field',title),input=node(tag);label.append(input);editor.append(label);return input;}
    const name=editField('预设名称'),category=editField('预设分类','select'),text=editField('正面提示词原文','textarea'),negativeText=editField('负面提示词原文','textarea');choices(category,categories);text.rows=5;negativeText.rows=3;
    const editActions=node('div','action-row');editor.append(editActions);
    button(editActions,'新建',()=>{editing='';name.value='';text.value='';negativeText.value='';category.value='style';openEditor();});
    button(editActions,'保存预设',async()=>{await api(editing?'prompt-presets/'+editing:'prompt-presets',editing?'PUT':'POST',{name:name.value,category:category.value,text:text.value,negative_text:negativeText.value});editing='';name.value='';text.value='';negativeText.value='';await reload();toast('预设已保存到本机。');});
    button(editActions,'删除当前预设',async()=>{if(!editing)throw new Error('请先点击一个预设的“编辑”。');if(!confirm('删除预设“'+name.value+'”？'))return;await api('prompt-presets/'+editing,'DELETE');editing='';name.value='';text.value='';negativeText.value='';await reload();toast('预设已删除，输入框内容保持不变。');});
    if(compact) {
      function popup(title) {
        const box=node('dialog','preset-dialog');box.setAttribute('aria-label',title);
        const heading=node('div','card-heading');heading.append(node('h2','',title));
        button(heading,'关闭',()=>box.close());box.append(heading);host.append(box);
        box.addEventListener('click',e=>{if(e.target===box){const r=box.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)box.close();}});
        return box;
      }
      dialog=popup('管理预设');
      editorDialog=popup('新增／编辑预设');
      const previewDialog=popup('组合后的 Prompt');previewDialog.append(preview.parentElement,negativePreview.parentElement);
      details.open=true;details.querySelector('summary').hidden=true;dialog.append(details);
      editor.open=true;editor.querySelector('summary').hidden=true;editorDialog.append(editor);
      const controls=node('div','action-row');
      button(controls,'预览组合 Prompt',()=>{updatePreview();previewDialog.showModal();});
      button(controls,'管理预设',async()=>{dialog.showModal();await reload();});
      button(controls,'新增预设',()=>{editing='';name.value='';text.value='';negativeText.value='';category.value='style';openEditor();});
      host.append(target.parentElement,controls,actions);
      if(targets.length===1)target.parentElement.hidden=true;
      reload().catch(e=>toast(e.message,true));

    }
    search.oninput=draw;filter.onchange=draw;target.onchange=updatePreview;
    for(const t of targets) {document.getElementById(t.id)?.addEventListener('input',updatePreview);document.getElementById(t.negativeId)?.addEventListener('input',updatePreview);}
    details.addEventListener('toggle',async()=>{if(!details.open||loading)return;loading=true;try{await reload();}catch(e){toast(e.message,true);}finally{loading=false;}});
    // Programmatic panel/history loads also change the target value.
    details.addEventListener('focusin',updatePreview);
    return {updatePreview,reload,
      open(id,kind) {target.value=id;if(kind!==undefined)filter.value=kind;updatePreview();dialog.showModal();reload().catch(e=>toast(e.message,true));},
      save(id,kind) {target.value=id;editing='';name.value='';category.value=kind;text.value=document.getElementById(id)?.value||'';negativeText.value=document.getElementById(targets.find(t=>t.id===id)?.negativeId)?.value||'';openEditor();}
    };
  }};
})(typeof window !== 'undefined' ? window : globalThis);
