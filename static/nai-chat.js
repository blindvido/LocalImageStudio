'use strict';
window.NaiChat={mount(host,{api,element:el,toast,key,getCurrent,apply,getContext=()=>''}){
 const storageKey='studio.nai-chat:'+key;let history=[],undo=null,pending=null,working=false,settings=null;
 try{const saved=JSON.parse(localStorage.getItem(storageKey));history=saved?.history||[];undo=saved?.undo||null;pending=saved?.pending||null;}catch{}
 const box=el('section','nai-chat'),heading=el('div','card-heading');heading.append(el('h3','','AI 提示词对话'));
 const configure=el('button','text-button','API／系统预设');configure.type='button';heading.append(configure);box.append(heading);
 const preset=el('select');preset.setAttribute('aria-label','AI系统预设');box.append(preset);
 const log=el('div','nai-chat-log');log.setAttribute('role','log');log.setAttribute('aria-live','polite');box.append(log);
 const input=el('textarea');input.rows=3;input.placeholder='例如：让铃走在前面，哲回头查看终端；只改动作和镜头。';input.setAttribute('aria-label','AI对话消息');box.append(input);
 const locked=el('input');locked.type='checkbox';locked.checked=true;const label=el('label','check-label','保留已选预设和角色外形原文（角色动作仍可修改）');label.prepend(locked);box.append(label);
 const actions=el('div','action-row'),send=el('button','button secondary','发送并填入提示词'),revert=el('button','text-button','撤销上次填入'),use=el('button','text-button','填入待处理回复'),clear=el('button','text-button','清空对话');for(const b of [send,revert,use,clear]){b.type='button';actions.append(b);}box.append(actions,el('p','hint','仅整理并填入提示词；点击生图按钮才提交图片任务。API连接与现有提示词服务共用。'));host.append(box);
 function save(){try{localStorage.setItem(storageKey,JSON.stringify({history,undo,pending}));}catch{toast('对话草稿无法保存到浏览器。',true);}}
 function draw(){log.replaceChildren();for(const line of history){const p=el('p','nai-chat-message '+line.role);p.append(el('strong','',line.role==='user'?'你：':'AI：'),document.createTextNode(line.content));log.append(p);}log.scrollTop=log.scrollHeight;revert.disabled=!undo||working;use.hidden=!pending;send.disabled=working;}
 async function refresh(){settings=await api('nai/chat/settings');const old=preset.value;preset.replaceChildren();for(const [id,p] of Object.entries(settings.presets)){const o=el('option','',p.name);o.value=id;preset.append(o);}if([...preset.options].some(o=>o.value===old))preset.value=old;}
 async function fill(data,before){undo=before;pending=null;save();await apply(data);draw();}
 send.onclick=async()=>{if(working||!input.value.trim())return;working=true;draw();try{
  if(!settings)await refresh();const before=structuredClone(getCurrent()),message=input.value;
  const result=await api('nai/chat','POST',{message,preset:preset.value,current:{sections:before.sections,characters:before.characters},history:history.slice(-12),context:getContext(),locked_sections:locked.checked?(before.locked_sections||[]):[],locked_characters:locked.checked?(before.locked_characters||[]):[]});
  history=[...history,{role:'user',content:message},{role:'assistant',content:result.summary}].slice(-12);input.value='';
  if(!host.isConnected||JSON.stringify(getCurrent())!==JSON.stringify(before)){pending=result;save();toast('回复已收到；输入内容已变化，请点击「填入待处理回复」后应用。');}else{await fill(result,before);toast('AI提示词已填入，可继续编辑或生图。');}
 }catch(e){toast(e.message,true);}finally{working=false;draw();}};
 revert.onclick=async()=>{if(!undo||working)return;const old=undo;undo=null;pending=null;save();try{await apply(old);draw();toast('已恢复上次填入前的提示词。');}catch(e){undo=old;save();toast(e.message,true);}};
 use.onclick=async()=>{if(!pending||working)return;try{await fill(pending,structuredClone(getCurrent()));}catch(e){toast(e.message,true);}};
 clear.onclick=()=>{if(working)return;history=[];pending=null;save();draw();};
 configure.onclick=async()=>{try{await refresh();const dialog=el('dialog','preset-dialog');dialog.setAttribute('aria-label','AI连接与系统预设');const form=el('div');dialog.append(el('h2','','AI连接与系统预设'),el('p','hint','这里保存的API地址、模型和密钥与ComfyUI提示词服务共用。密钥只保存在本机，留空保留原值。'),form);
  const field=(name,value,type='input')=>{const l=el('label','review-field',name),n=el(type);n.value=value||'';n.setAttribute('aria-label',name);l.append(n);form.append(l);return n;};
  const url=field('API地址',settings.api_url),model=field('文字模型',settings.model),secret=field('API密钥','');secret.type='password';secret.autocomplete='new-password';secret.placeholder=settings.has_api_key?'已保存；留空保留':'未保存';
  const records=structuredClone(settings.presets),select=field('编辑系统预设','','select'),name=field('系统预设名称',''),text=field('系统预设内容','','textarea');text.rows=7;
  function load(){const item=records[select.value];name.value=item?.name||'';text.value=item?.text||'';}
  function options(chosen){select.replaceChildren();for(const [id,p] of Object.entries(records)){const o=el('option','',p.name);o.value=id;select.append(o);}if(chosen)select.value=chosen;load();}
  function commit(){if(records[select.value])records[select.value]={name:name.value,text:text.value};}
  select.onchange=load;name.oninput=commit;text.oninput=commit;options(preset.value);
  const row=el('div','action-row');dialog.append(row);function button(title,fn){const b=el('button','button secondary',title);b.type='button';b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){toast(e.message,true);}finally{b.disabled=false;}};row.append(b);}
  button('新增系统预设',()=>{commit();const id=crypto.randomUUID();records[id]={name:'新系统预设',text:''};options(id);});
  button('删除此系统预设',()=>{if(Object.keys(records).length<=1)throw Error('至少保留一个系统预设。');delete records[select.value];options();});
  button('保存设置',async()=>{commit();if(Object.values(records).some(p=>!p.name.trim()||!p.text.trim()))throw Error('预设名称和内容不能为空。');const body={api_url:url.value,model:model.value};if(secret.value)body.api_key=secret.value;await api('config','PUT',body);window.dispatchEvent(new Event('nai-chat-config-changed'));await api('nai/chat/settings','PUT',{presets:records});await refresh();dialog.close();toast('AI连接与系统预设已保存。');});button('关闭',()=>dialog.close());
  document.body.append(dialog);dialog.onclose=()=>dialog.remove();dialog.showModal();
 }catch(e){toast(e.message,true);}};
 draw();refresh().catch(e=>{const o=el('option','','请重启绘境以启用AI对话接口');preset.replaceChildren(o);});
 return {getHistory:()=>structuredClone(history)};
}};
