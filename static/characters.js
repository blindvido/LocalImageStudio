'use strict';
const characterState = {records: [], id: null, busy: false, timers: {}, versions: {}, page: 1, pageSize: 10, query: ''};
function characterMessage(message) { $('character-message').textContent = message; }
function characterBusy(busy) {
  characterState.busy = busy;
  document.querySelectorAll('#character-editor input, #character-editor textarea, #character-editor button').forEach(node => node.disabled = busy);
  $('character-delete').disabled = busy || !characterState.id;
  $('character-new').disabled = busy;
}
function characterRead() {
  return {name: $('character-name').value.trim(), aliases: $('character-aliases').value.split(/[,，、\n]/).map(x => x.trim()).filter(Boolean),
    appearance: $('character-appearance').value.trim(), clothing: $('character-clothing').value.trim(), negative_prompt: $('character-negative').value.trim(),
    notes: $('character-notes').value.trim(), enabled: $('character-enabled').checked};
}
function characterLoad(record = {}) {
  if (characterState.busy) { toast('人物操作仍在进行，请稍候。', true); return; }
  characterState.id = record.id || null;
  for (const [field, id] of Object.entries({name:'name', appearance:'appearance', clothing:'clothing', negative_prompt:'negative', notes:'notes'})) $('character-'+id).value = record[field] || '';
  $('character-aliases').value = (record.aliases || []).join('、');
  $('character-enabled').checked = record.enabled !== false;
  $('character-description').value = '';
  $('character-editor-title').textContent = record.id ? '编辑 · '+record.name : '创建人物';
  characterMessage(record.id ? '修改后点击保存，后续创作会使用新设定。' : '尚未保存；填写或生成固定外形后，确认保存。');
  characterBusy(false); characterList();
}
function characterList() {
  const list = $('character-list'); list.replaceChildren();
  const query = characterState.query.trim().toLocaleLowerCase();
  const records = characterState.records.filter(record => !query || [record.name, ...(record.aliases || []), record.appearance, record.clothing, record.negative_prompt, record.notes].some(value => String(value || '').toLocaleLowerCase().includes(query)));
  const pages = Math.max(1, Math.ceil(records.length / characterState.pageSize));
  characterState.page = Math.max(1, Math.min(characterState.page, pages));
  $('character-count').textContent = query ? '找到 '+records.length+' 位 · 共 '+characterState.records.length+' 位人物' : '共 '+records.length+' 位人物';
  $('character-page').textContent = characterState.page+' / '+pages;
  $('character-prev').disabled = characterState.page === 1;
  $('character-next').disabled = characterState.page === pages;
  if (!records.length) list.append(element('p', 'hint', query ? '没有找到匹配的人物，请更换关键词。' : '还没有人物。先创建一位，之后就能用名字唤起人设。'));
  const start = (characterState.page - 1) * characterState.pageSize;
  for (const record of records.slice(start, start + characterState.pageSize)) {
    const button = element('button', 'workflow-row'+(record.id === characterState.id ? ' active' : ''));
    button.append(element('strong','',record.name), element('small','',(record.enabled !== false ? '已启用' : '已停用') + (record.aliases?.length ? ' · '+record.aliases.join('、') : '')));
    button.addEventListener('click', () => characterLoad(record)); list.append(button);
  }
  list.scrollTop = 0;
}
function characterShowMatched(id, records) {
  $(id).textContent = records.length ? '已匹配人物：'+records.map(x=>x.name).join('、')+' · 生成提示词时带入固定外形和默认服装' : '未匹配人物；可在人物库保存名字和别名。';
}
async function characterPreview(id, text) {
  clearTimeout(characterState.timers[id]);
  const version = characterState.versions[id] = (characterState.versions[id] || 0)+1;
  if (!text.trim()) { $(id).textContent='输入已保存的人物名字，自动带入人设。'; return; }
  characterState.timers[id] = setTimeout(async () => {
    try {
      const result = await api('characters/match','POST',{description:text});
      if (characterState.versions[id] === version) characterShowMatched(id,result.characters);
    } catch (e) { if (characterState.versions[id] === version) $(id).textContent='人物匹配暂时不可用：'+e.message; }
  },250);
}
function characterRefreshPreview() {
  characterPreview('character-matches',$('description').value);
  characterPreview('edit-character-matches',$('edit-description').value+'\n'+$('edit-preserve').value);
}
async function characterAction(callback) {
  if (characterState.busy) return;
  characterBusy(true);
  try { await callback(); } catch(e) { characterMessage(e.message); toast(e.message,true); }
  finally { characterBusy(false); }
}
window.CharacterPage = {
  showMatched: characterShowMatched,
  refreshPreview: characterRefreshPreview,
  async reload() { characterState.records=await api('characters'); characterList(); characterRefreshPreview(); },
  async init() {
    await this.reload(); characterLoad();
    $('character-search').addEventListener('input', event => { characterState.query = event.target.value; characterState.page = 1; characterList(); });
    $('character-prev').addEventListener('click', () => { characterState.page--; characterList(); });
    $('character-next').addEventListener('click', () => { characterState.page++; characterList(); });
    $('description').addEventListener('input',characterRefreshPreview);
    ['edit-description','edit-preserve'].forEach(id=>$(id).addEventListener('input',characterRefreshPreview));
    $('character-new').addEventListener('click',()=>characterLoad());
    $('character-from-create').addEventListener('click',()=>{if(characterState.busy)return;characterLoad();$('character-description').value=$('description').value;page('characters');});
    $('character-save').addEventListener('click',()=>characterAction(async()=>{
      const record=await api('characters'+(characterState.id?'/'+characterState.id:''),characterState.id?'PUT':'POST',characterRead());
      characterState.id=record.id;await this.reload();$('character-editor-title').textContent='编辑 · '+record.name;characterMessage('人物已保存。现在可以在创作描述中直接使用“'+record.name+'”。');toast('人物已保存。');
    }));
    $('character-draft').addEventListener('click',()=>characterAction(async()=>{
      characterMessage('正在整理固定外形…');
      const draft=await api('characters/draft','POST',{name:$('character-name').value,description:$('character-description').value});
      $('character-appearance').value=draft.appearance;characterMessage('外形草稿已生成，尚未保存。请检查锚点，并单独填写默认服装。');
    }));
    $('character-export').addEventListener('click',()=>downloadJson((characterRead().name||'人物')+'.json',characterRead()));
    $('character-delete').addEventListener('click',()=>{if(!characterState.id||!confirm('删除这位人物？已有作品和任务中的人设快照仍会保留。'))return;characterAction(async()=>{await api('characters/'+characterState.id,'DELETE');characterState.id=null;await this.reload();characterState.busy=false;characterLoad();toast('人物已删除。');});});
  }
};
