'use strict';
const $ = id => document.getElementById(id);
const state = {token: '', config: {}, workflows: [], selected: '', currentJob: null, promptShownFor: null, choices: {}, busy: false};
const labels = {width:'宽度', height:'高度', steps:'步数', cfg_scale:'CFG', seed:'种子', sampler_name:'采样器', scheduler:'调度器', MODEL_NAME:'模型', clip:'CLIP 编码器', vae:'VAE', prompt:'正面提示词', negative_prompt:'负面提示词'};
const numberNames = new Set(['width','height','steps','cfg_scale','cfg_rescale','seed']);
const statusLabels = {generating_prompt:'正在生成提示词', preparing:'准备工作流', waiting_rate_limit:'等待 NAI 安全间隔', submitting:'正在提交生图', queued:'已进入 ComfyUI 队列', running:'正在绘制', done:'创作完成', failed:'生成失败', unknown:'任务状态待确认', interrupted:'任务已中断'};
let toastTimer, pollTimer, pollSequence = 0, naiUsageSequence = 0;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function toast(text, error = false) {
  $('toast').textContent = text;
  $('toast').className = 'toast visible' + (error ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').classList.remove('visible'), error ? 9000 : 4000);
}
async function api(path, method = 'GET', data) {
  const response = await fetch('/api/' + path, {method, headers: {'Content-Type':'application/json','X-Studio-Token':state.token}, ...(data === undefined ? {} : {body:JSON.stringify(data)})});
  let result;
  try { result = await response.json(); } catch { if(response.ok)throw new Error('本地服务未返回有效数据，请查看启动日志。'); result={}; }
  if (!response.ok) { const error=new Error(result.error || '操作失败（' + response.status + '）');error.status=response.status;throw error; }
  return result;
}
function action(id, callback) {
  $(id).addEventListener('click', async () => {
    const button = $(id); button.disabled = true;
    try { await callback(); } catch (error) { toast(error.message, true); }
    finally { button.disabled = ['generate-prompt','render','one-click'].includes(id) ? state.busy : false; }
  });
}
const providerDrafts = {};
let editorProvider = 'comfyui', promptSections;
function switchCreation(provider) {
  if (provider !== editorProvider && state.busy) { toast('当前生图完成后即可切换生图页面。'); return false; }
  if (provider !== editorProvider) {
    providerDrafts[editorProvider] = {
      positive:$('positive-prompt').value, negative:$('negative-prompt').value,
      description:$('description').value, template:$('template-select').value,
      values:Object.fromEntries([...document.querySelectorAll('#parameter-fields input')].map(i=>[i.dataset.parameter,i.value])),
      random:$('random-seed').checked,
      images:$('result-images').innerHTML, meta:$('result-meta').innerHTML, status:$('job-status').innerHTML
    };
    editorProvider=provider;
    $('image-provider').value=provider; providerChanged();
    const draft=providerDrafts[provider];
    $('positive-prompt').value=draft?.positive||''; $('negative-prompt').value=draft?.negative||'';
    $('description').value=draft?.description||'';
    if(draft) { $('template-select').value=draft.template; renderParameters(currentWorkflow(),draft.values); $('random-seed').checked=draft.random; }
    $('result-images').innerHTML=draft?.images||'<div class="empty-art"><h3>使用当前提示词开始创作</h3></div>';
    $('result-meta').innerHTML=draft?.meta||''; $('job-status').innerHTML=draft?.status||'';
    $('positive-prompt').dispatchEvent(new Event('input',{bubbles:true}));
  }
  const destination=$(provider==='novelai'?'page-novelai':'page-create');
  const other=$(provider==='novelai'?'page-create':'page-novelai');
  while(other.firstChild) destination.append(other.firstChild);
  const nai=provider==='novelai';
  $('connection').hidden=nai;
  destination.querySelector('.creation-column > .card:not(.compose-card) .step').textContent=nai?'01':'02';
  destination.querySelector('.page-heading h1').textContent=nai?'NovelAI 生图':'ComfyUI 生图';
  destination.querySelector('.page-heading p').textContent=nai?'选择预设，打磨提示词，直接生成。':'选择本地工作流，编写提示词并生成。';
  destination.querySelector('.compose-card').hidden=nai;
  destination.querySelector('.tip-card').hidden=nai;
  $('one-click').hidden=nai;
  $('one-click').parentElement.nextElementSibling.hidden=nai;
  $('nai-preset-entry').hidden=!nai;
  promptSections?.activate(nai);
  $('render').classList.toggle('primary',nai);
  $('render').textContent=nai?'用当前提示词生成 · NovelAI':'用当前提示词生图';
  $('parameter-fields').parentElement.querySelector('summary span').textContent=nai?'NovelAI 参数':'工作流参数';
  $('image-provider').hidden=true; document.querySelector('label[for="image-provider"]').hidden=true;
  return true;
}
function page(name) {
  if ((name==='create'||name==='novelai') && !switchCreation(name==='novelai'?'novelai':'comfyui')) return;
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + name));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.page === name));
  $('page-label').textContent = {create:'ComfyUI 生图', novelai:'NovelAI 生图', panels:'漫画编辑站', edit:'改图模式', characters:'人物库', workflows:'工作流', history:'创作历史', settings:'连接与设置'}[name];
  if (name === 'panels') window.ComicReviewPage.open().catch(e => toast(e.message, true));
  if (name === 'history') loadHistory().catch(e => toast(e.message, true));
}
function currentWorkflow() { return state.workflows.find(w => w.id === state.selected); }
function options(select, values, selected) {
  select.replaceChildren();
  values.forEach(([value, text]) => { const option = element('option', '', text); option.value = value; select.append(option); });
  if (selected !== undefined) select.value = selected;
}
function templates(selected) {
  const entries = Object.entries(state.config.templates).map(([key,t]) => [key,t.name]);
  ['template-select','workflow-template','template-editor-select'].forEach(id => {
    const old = $(id).value;
    options($(id), entries, id === 'template-editor-select' ? (old || entries[0]?.[0]) : selected);
    if (!$(id).value && entries.length) $(id).value = entries[0][0];
  });
  editTemplate();
}
function field(parent, name, value, prefix, disabled = false, allowType = false) {
  const box = element('div');
  const input = element('input'); input.id = prefix + name; input.dataset.parameter = name;
  input.type = numberNames.has(name) || typeof value === 'number' ? 'number' : 'text';
  if (input.type === 'number') { input.step = ['cfg_scale','cfg_rescale'].includes(name) || !numberNames.has(name) ? 'any' : '1'; input.min = ['width','height','steps'].includes(name) ? '1' : '0'; }
  input.value = value ?? ''; input.disabled = disabled;
  const label = element('label', '', (labels[name] || name) + (disabled ? ' · 使用图内原值' : ''));
  label.htmlFor = input.id; box.append(label,input);
  if (state.choices[name]) {
    const list = element('datalist'); list.id = prefix + name + '-list';
    state.choices[name].forEach(v => { const option = element('option'); option.value = v; list.append(option); });
    input.setAttribute('list',list.id); box.append(list);
  }
  if (allowType && !labels[name]) {
    const select = element('select'); select.setAttribute('aria-label', name + ' 值类型');
    options(select, [['text','文本'],['number','数值']], input.type);
    select.addEventListener('change', () => input.type = select.value); box.append(select);
  }
  parent.append(box);
}
function readValues(prefix) {
  const result = {};
  document.querySelectorAll('[id^="' + prefix + '"][data-parameter]').forEach(input => {
    if (!input.disabled) {
      if (input.type === 'number') { if (input.value === '' || !Number.isFinite(Number(input.value))) throw new Error((labels[input.dataset.parameter] || input.dataset.parameter) + '需要有效数值。'); result[input.dataset.parameter] = Number(input.value); }
      else result[input.dataset.parameter] = input.value;
    }
  });
  return result;
}
function parameterNames(workflow) {
  return [...new Set([...Object.keys(labels).filter(n => !['prompt','negative_prompt'].includes(n)),...workflow.inspection.variables.filter(n => !['prompt','negative_prompt'].includes(n)),...Object.keys(workflow.mappings).filter(n => !['prompt','negative_prompt'].includes(n))])];
}
function renderParameters(workflow, override = {}) {
  $('parameter-fields').replaceChildren();
  if ($('image-provider').value === 'novelai') {
    const defaults = {width:832,height:1216,steps:28,cfg_scale:5,seed:0,MODEL_NAME:state.config.novelai_model,sampler_name:'k_euler_ancestral',scheduler:'karras',cfg_rescale:0};
    const prior = state.choices;
    state.choices = {MODEL_NAME:[...$('nai-model').options].map(o=>o.value),sampler_name:['k_euler_ancestral','k_euler','k_dpmpp_2m','k_dpmpp_sde','k_dpmpp_2s_ancestral','ddim_v3'],scheduler:['karras','native','exponential','polyexponential']};
    for (const [name,value] of Object.entries(defaults)) field($('parameter-fields'),name,override[name] ?? value,'param-',false);
    state.choices = prior;
    $('random-seed').disabled = false; $('random-seed').checked = true;
    return;
  }
  if (!workflow) return;
  const available = new Set([...workflow.inspection.variables,...Object.keys(workflow.mappings)]);
  parameterNames(workflow).forEach(name => field($('parameter-fields'),name,override[name] ?? workflow.values[name], 'param-', !available.has(name)));
  $('random-seed').checked = workflow.random_seed;
  $('random-seed').disabled = !available.has('seed');
}
function renderWorkflows(preferred = state.selected) {
  if (!state.workflows.some(w => w.id === preferred)) preferred = state.workflows[0]?.id || '';
  state.selected = preferred;
  options($('workflow-select'),state.workflows.map(w => [w.id,w.name]),preferred);
  $('workflow-list').replaceChildren();
  for (const workflow of state.workflows) {
    const button = element('button','workflow-row' + (workflow.id === preferred ? ' active' : ''));
    button.append(element('strong','',workflow.name),element('small','',Object.keys(workflow.graph).length + ' 个节点 · ' + workflow.inspection.variables.length + ' 个变量'));
    button.addEventListener('click', () => selectWorkflow(workflow.id)); $('workflow-list').append(button);
  }
  const workflow = currentWorkflow();
  $('workflow-editor').hidden = !workflow;
  if (!workflow) return;
  templates(workflow.template); renderParameters(workflow);
  $('workflow-name').value = workflow.name;
  $('fixed-positive').value = workflow.fixed_positive;
  $('fixed-negative').value = workflow.fixed_negative;
  $('workflow-random').checked = workflow.random_seed;
  $('workflow-json').value = JSON.stringify(workflow.graph,null,2);
  $('workflow-values').replaceChildren();
  parameterNames(workflow).forEach(name => field($('workflow-values'),name,workflow.values[name], 'default-', false, true));
  $('mapping-fields').replaceChildren();
  Object.keys(labels).forEach(name => {
    const box = element('div'), label = element('label','',labels[name]), select = element('select');
    select.dataset.mapping = name; select.id = 'mapping-' + name; label.htmlFor = select.id;
    options(select,[['','不映射'],...workflow.inspection.fields.map(f => [JSON.stringify([f.node,f.field]),f.node + ' · ' + f.title + ' / ' + f.field])],workflow.mappings[name] ? JSON.stringify(workflow.mappings[name]) : '');
    box.append(label,select); $('mapping-fields').append(box);
  });
  $('mapping-suggestions').textContent = '';
}
function selectWorkflow(id) {
  renderWorkflows(id); $('positive-prompt').value = ''; $('negative-prompt').value = '';
}
async function reloadWorkflows(preferred) { state.workflows = await api('workflows'); renderWorkflows(preferred); }
let connectionSequence = 0;
let modelsSequence = 0;
function connectionFingerprint() {
  return JSON.stringify([$('api-url').value.trim(), $('api-key').value, $('api-model').value.trim()]);
}
function invalidateConnection() {
  connectionSequence++;
  modelsSequence++;
  options($('api-model-select'), [['', '先获取模型列表，再选择模型']]);
  $('model-list-state').textContent = '连接信息已变化，请重新获取模型列表。';
  $('api-connection-state').textContent = '连接信息已变化，尚未测试。';
}
async function fetchModels() {
  const sequence = ++modelsSequence;
  const fingerprint = connectionFingerprint();
  const body = {api_url:$('api-url').value.trim()};
  if ($('api-key').value) body.api_key = $('api-key').value;
  $('model-list-state').textContent = '正在获取模型列表…';
  try {
    const result = await api('models', 'POST', body);
    if (sequence !== modelsSequence || fingerprint !== connectionFingerprint()) return;
    options($('api-model-select'), [['', '请选择模型'], ...result.models.map(id => [id, id])], $('api-model').value.trim());
    $('model-list-state').textContent = `已获取 ${result.models.length} 个模型。选择后点击“保存连接配置”；实际调用权限请使用“测试 API”验证。`;
  } catch (error) {
    if (sequence !== modelsSequence || fingerprint !== connectionFingerprint()) return;
    options($('api-model-select'), [['', '无法获取列表，可手动填写模型名称']]);
    $('model-list-state').textContent = error.message;
    throw error;
  }
}
async function testApiConnection() {
  await saveConfig();
  if (!state.selected) throw new Error('请先选择一个工作流。');
  const fingerprint = connectionFingerprint();
  const sequence = connectionSequence;
  const address = state.config.api_url, model = state.config.model;
  $('api-connection-state').textContent = `正在测试：${address} · ${model}`;
  try {
    await api('prompt', 'POST', {workflow_id:state.selected,description:'一朵白色的花，柔和自然光',template:$('template-select').value});
    if (sequence !== connectionSequence || fingerprint !== connectionFingerprint()) return;
    $('api-connection-state').textContent = `连接成功：${address} · ${model}。已验证文字描述生成正负提示词；图片理解能力需在改图模式中验证。`;
    toast('API 连接成功，已收到有效的正负提示词。');
  } catch (error) {
    if (sequence !== connectionSequence || fingerprint !== connectionFingerprint()) return;
    $('api-connection-state').textContent = `连接失败：${address} · ${model}。${error.message}`;
    throw error;
  }
}
function fillConfig() {
  const config = state.config;
  $('api-url').value = config.api_url; $('api-model').value = config.model;
  $('api-timeout').value = config.api_timeout; $('comfy-url').value = config.comfy_url;
  $('api-key').value = '';
  $('key-state').textContent = config.has_api_key ? '已保存密钥。留空表示保留现有密钥。' : '尚未保存密钥；本地无鉴权 API 可留空。';
  $('nai-url').value = config.novelai_url;
  $('nai-model').value = config.novelai_model;
  $('nai-fixed-positive').value = config.novelai_fixed_positive || '';
  $('nai-fixed-negative').value = config.novelai_fixed_negative || '';
  $('nai-key').value = '';
  $('nai-key-state').textContent = config.has_novelai_token ? 'Token 已保存；留空保留，无需重复复制。' : '尚未保存 Token。';
}
async function saveNovelAI(clear = false) {
  const body = {novelai_fixed_positive:$('nai-fixed-positive').value,novelai_fixed_negative:$('nai-fixed-negative').value,novelai_url:$('nai-url').value.trim(),novelai_model:$('nai-model').value,image_provider:$('image-provider').value};
  if ($('nai-key').value || clear) body.novelai_token = clear ? '' : $('nai-key').value.trim();
  state.config = await api('config','PUT',body);
  $('nai-key').value = '';
  $('nai-key-state').textContent = state.config.has_novelai_token ? 'Token 已保存；留空保留，无需重复复制。' : '尚未保存 Token。';
}
function providerChanged() {
  const nai = $('image-provider').value === 'novelai';
  const usageSequence = ++naiUsageSequence;
  $('workflow-select').disabled = nai || state.busy;
  $('provider-hint').textContent = nai ? '直连 NovelAI，图片保存到本机。正在读取上海时间的出图记录与安全间隔…' : '使用本地 ComfyUI 工作流生成图片。';
  if (nai) api('nai/usage').then(usage => {
    if (usageSequence !== naiUsageSequence || $('image-provider').value !== 'novelai') return;
    $('provider-hint').textContent = `直连 NovelAI，图片保存到本机。上海时间 ${usage.date}：今日记录 ${usage.count} 次（含失败和未知请求的保守计数）；每张返回后至少间隔 ${usage.minimum_interval_seconds} 秒。每日数量仅作记录，模型额度请按服务说明确认。`;
  }).catch(error => {
    if (usageSequence === naiUsageSequence && $('image-provider').value === 'novelai') $('provider-hint').textContent = `无法读取 NAI 额度状态：${error.message}。为避免超过限额，请检查本地服务。`;
  });
  if (nai || currentWorkflow()) renderParameters(currentWorkflow());
  if (nai && state.config.templates.tags) $('template-select').value='tags';
}
async function saveConfig(clearKey = false) {
  const body = {api_url:$('api-url').value.trim(), model:$('api-model').value.trim(), api_timeout:Number($('api-timeout').value), comfy_url:$('comfy-url').value.trim()};
  if ($('api-key').value || clearKey) body.api_key = clearKey ? '' : $('api-key').value;
  state.config = await api('config','PUT',body);
  if ('api_key' in body) connectionSequence++;
  fillConfig(); return state.config;
}
function editTemplate() {
  const template = state.config.templates[$('template-editor-select').value];
  if (template) { $('template-name').value = template.name; $('template-text').value = template.text; }
}
async function checkComfy() {
  try {
    const result = await api('comfy/status');
    $('connection').classList.add('connected'); $('connection').querySelector('span').textContent = 'ComfyUI 已连接';
    $('comfy-detail').textContent = result.devices.map(d => d.name).join('\n') || 'ComfyUI 服务已连接。';
    const oldValues = currentWorkflow() ? readValues('param-') : {};
    state.choices = (await api('comfy/options')).choices;
    if (currentWorkflow()) renderParameters(currentWorkflow(),oldValues);
    return true;
  } catch (error) {
    $('connection').classList.remove('connected'); $('connection').querySelector('span').textContent = 'ComfyUI 未连接';
    $('comfy-detail').textContent = error.message; return false;
  }
}
function setBusy(busy) {
  state.busy = busy;
  ['generate-prompt','render','one-click','image-provider'].forEach(id => $(id).disabled = busy);
  $('workflow-select').disabled = busy || $('image-provider').value === 'novelai';
}
function jobStatus(job) {
  const container = $('job-status'); container.replaceChildren();
  const box = element('div','status-box' + (['failed','unknown','interrupted'].includes(job.state) ? ' error' : ''));
  box.append(element('strong','',statusLabels[job.state] || job.state));
  const detail = job.error || job.notice;
  if (detail) box.append(element('span','status-detail',detail));
  if (job.state === 'unknown' && job.prompt_id) {
    const button = element('button','button secondary','继续查询已有任务');
    button.addEventListener('click', async () => { try { state.currentJob = (await api('jobs/' + job.id + '/resume','POST',{})).id; setBusy(true); pollJob(); } catch (e) { toast(e.message,true); } }); box.append(button);
  }
  if (job.progress && job.state === 'running') {
    const progress = element('progress'); progress.max = job.progress.max; progress.value = job.progress.value;
    box.append(progress,element('span','status-detail',job.progress.value + ' / ' + job.progress.max + ' 采样步'));
  }
  container.append(box); $('result-tag').textContent = statusLabels[job.state] || job.state;
}
function showImages(job) {
  $('result-images').replaceChildren();
  const versions = element('div','action-row redraw-versions');
  $('result-images').append(versions);
  loadRedrawVersions(job,versions).catch(e=>toast(e.message,true));
  (job.images || []).forEach((record,index) => {
    const url = '/api/images/' + job.id + '/' + index;
    const wrap = element('div','image-wrap'), img = element('img','result-img');
    img.src = url; img.alt = job.description || job.positive_prompt || '生成结果';
    img.addEventListener('click', () => { $('lightbox-image').src = url; $('lightbox').showModal(); });
    img.addEventListener('error', () => { const msg = element('p','note','图片无法读取。请确认 ComfyUI 在线且输出文件存在。'); img.replaceWith(msg); });
    const tools = element('div','image-tools'), download = element('a','', '↓ 下载原图'); download.href = url; download.download = record.filename;
    const redraw=element('button','button secondary','NAI 重绘');
    redraw.dataset.redrawJob=job.id;redraw.dataset.redrawIndex=index;
    tools.append(download,redraw); wrap.append(img,tools); $('result-images').append(wrap);
  });
  const seconds = Math.max(0,Math.round((new Date(job.updated_at) - new Date(job.created_at))/1000));
  $('result-meta').textContent = job.workflow.name + ' · 种子 ' + job.values.seed + ' · ' + seconds + ' 秒';
}
async function loadRedrawVersions(job,container) {
  const jobs=await api('jobs'), byId=new Map(jobs.map(j=>[j.id,j]));
  const root=j=>{const seen=new Set();while(j.img2img&&byId.has(j.parent_job_id)&&!seen.has(j.id)){seen.add(j.id);j=byId.get(j.parent_job_id);}return j.id;};
  const rootId=root(job), family=jobs.filter(j=>root(j)===rootId&&j.state==='done'&&j.images?.length).sort((a,b)=>a.created_at.localeCompare(b.created_at));
  if(family.length<2)return;
  family.forEach((j,i)=>{const b=element('button','button secondary',j.id===rootId?'原图':`新版 ${i} · Strength ${j.img2img?.strength}`);b.dataset.redrawView=j.id;b.disabled=j.id===job.id;container.append(b);});
}
function openNaiRedraw(jobId,index) {
  if(state.busy)throw new Error('请等待当前任务完成。');
  const dialog=element('dialog','nai-redraw-dialog');
  const title=element('h2','','NAI 重绘'), preview=element('img');preview.src=`/api/images/${jobId}/${index}`;preview.alt='重绘底图';
  const label=element('label','','Strength · 重绘强度 '), value=element('output','','0.35');
  const slider=element('input');slider.type='range';slider.min='0';slider.max='1';slider.step='0.01';slider.value='0.35';slider.setAttribute('aria-label','Strength');slider.oninput=()=>value.textContent=slider.value;label.append(value,slider);
  const hint=element('p','hint','值越高改动越大。沿用此图的提示词和人物分段，生成后保留原图与新版。尺寸会对齐到 64 的倍数。');
  const row=element('div','action-row'), cancel=element('button','button secondary','取消'), generate=element('button','button primary','生成新版');
  cancel.onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove());
  let requestId=crypto.randomUUID(), sentStrength=null;
  generate.onclick=async()=>{
    if(state.busy)return;
    const strength=Number(slider.value);
    if(sentStrength!==null&&sentStrength!==strength)requestId=crypto.randomUUID();
    sentStrength=strength; generate.disabled=true;slider.disabled=true;
    try{
      page('novelai');setBusy(true);
      const job=await api('nai/redraw','POST',{source_job_id:jobId,source_index:index,strength,request_id:requestId});
      state.currentJob=job.id;dialog.close();jobStatus(job);pollJob();
    }catch(e){setBusy(false);toast(e.message,true);generate.disabled=false;slider.disabled=false;}
  };
  row.append(cancel,generate);dialog.append(title,preview,label,hint,row);document.body.append(dialog);dialog.showModal();
}
document.addEventListener('click',async e=>{
  const redraw=e.target.closest('[data-redraw-job]'), view=e.target.closest('[data-redraw-view]');
  try{
    if(redraw)openNaiRedraw(redraw.dataset.redrawJob,Number(redraw.dataset.redrawIndex));
    if(view){if(state.busy)throw new Error('请等待当前任务完成。');const job=await api('jobs/'+view.dataset.redrawView);state.currentJob=job.id;jobStatus(job);showImages(job);}
  }catch(error){toast(error.message,true);}
});
async function pollJob() {
  clearTimeout(pollTimer);
  if (!state.currentJob) return;
  const jobId = state.currentJob, sequence = ++pollSequence;
  try {
    const job = await api('jobs/' + jobId);
    if (state.currentJob !== jobId || sequence !== pollSequence) return;
    jobStatus(job);
    if (job.use_api && job.positive_prompt && state.promptShownFor !== job.id && state.selected === job.workflow_id) {
      $('positive-prompt').value = job.positive_prompt; $('negative-prompt').value = job.negative_prompt;
      state.promptShownFor = job.id;
    }
    if (['done','failed','unknown','interrupted'].includes(job.state)) {
      setBusy(false); if (job.state === 'done') { showImages(job); toast(job.provider === 'novelai' ? '创作完成，NovelAI 图片已保存在本机。' : '创作完成，图片已保存在 ComfyUI 输出目录。'); }
      return;
    }
  } catch (error) {
    if (state.currentJob !== jobId || sequence !== pollSequence) return;
    toast(error.message,true);
  }
  if (state.currentJob === jobId && sequence === pollSequence && state.busy) pollTimer = setTimeout(pollJob,2000);
}
async function submit(useApi) {
  if($('image-provider').value==='novelai')promptSections?.sync();
  if ($('image-provider').value!=='novelai' && !currentWorkflow()) throw new Error('请先导入工作流。');
  if (!useApi && !$('positive-prompt').value.trim()) throw new Error('请先生成或填写正面提示词。');
  if (useApi && !$('description').value.trim()) throw new Error('请先描述画面。');
  let body = {workflow_id:state.selected, template:$('template-select').value, description:$('description').value,
    provider:$('image-provider').value,
    positive_prompt:$('positive-prompt').value, negative_prompt:$('negative-prompt').value, use_api:useApi,
    values:readValues('param-'), random_seed:$('random-seed').checked};
  if(body.provider==='novelai') {
    const prompt=promptSections.structured();
    if(prompt.characters.length){
      if(!prompt.base_positive.trim())throw new Error('请填写任务、画风、场景或动作中的全局提示词。');
      body={provider:'novelai',request_id:crypto.randomUUID(),use_api:false,random_seed:body.random_seed,values:body.values,nai_prompt:prompt};
    }
  }
  setBusy(true);
  try {
    const job = await api('jobs','POST',body); state.currentJob = job.id;
    $('result-images').replaceChildren(); $('result-meta').textContent = ''; jobStatus(job); pollJob();
  } catch (error) { setBusy(false); throw error; }
}
function downloadJson(name, value) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
  const link = element('a'); link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
}
async function restoreJob(job) {
  if (job.edit_id) { await window.EditPage.openJob(job); return; }
  if (state.busy) throw new Error('当前任务仍在执行，请完成后再恢复历史参数。');
  if (job.panel_id) {
    if (!job.nai_prompt || !job.request_id) throw new Error('逐格版本记录不完整，已保留在漫画逐格页。');
    page('panels'); await window.ComicReviewPage.restoreJob(job); return;
  }
  page(job.provider === 'novelai' ? 'novelai' : 'create');
  if (job.provider === 'novelai') {
    providerChanged(); renderParameters(currentWorkflow(),job.values);
    $('random-seed').checked=false; $('description').value=job.description;
    $('positive-prompt').value=job.positive_prompt; $('negative-prompt').value=job.negative_prompt;
    promptSections?.importFull(job.positive_prompt,job.negative_prompt,job.nai_prompt?.characters||[]);
    $('template-select').value=job.workflow.template; jobStatus(job);showImages(job);
    toast('已恢复 NovelAI 提示词和实际参数。');return;
  }
  let workflow = state.workflows.find(w => w.id === job.workflow_id && JSON.stringify(w.graph) === JSON.stringify(job.workflow.graph) && JSON.stringify(w.mappings) === JSON.stringify(job.workflow.mappings));
  if (!workflow) {
    workflow = await api('workflows','POST',{name:'复现 · ' + job.workflow.name,graph:job.workflow.graph});
    workflow = await api('workflows/' + workflow.id,'PUT',{...job.workflow,id:workflow.id, name:'复现 · ' + job.workflow.name});
    state.workflows = await api('workflows');
  }
  renderWorkflows(workflow.id); renderParameters(workflow,job.values);
  $('random-seed').checked = false; $('description').value = job.description;
  $('positive-prompt').value = job.positive_prompt; $('negative-prompt').value = job.negative_prompt;
  $('template-select').value = job.workflow.template;
  if (!$('template-select').value) $('template-select').selectedIndex = 0;
  page('create'); jobStatus(job); if (job.images.length) showImages(job);
  toast('已恢复提示词与实际参数；点击“用当前提示词生图”可再次生成。');
}
const historyState={jobs:[],page:1,pageSize:12,sequence:0};
const historyDateFormat=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'});
function historyDay(timestamp){
  const date=new Date(timestamp);if(Number.isNaN(date.getTime()))return '';
  const parts=Object.fromEntries(historyDateFormat.formatToParts(date).map(p=>[p.type,p.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}
async function loadHistory() {
  const sequence=++historyState.sequence;
  const jobs=await api('jobs');if(sequence!==historyState.sequence)return;
  historyState.jobs=jobs.slice().sort((a,b)=>new Date(b.created_at)-new Date(a.created_at));
  const selectedTag=$('history-work-tag').value;
  const tags=[...new Set(['绝区零','无职转生',...jobs.flatMap(j=>j.work_tags||[])])].sort((a,b)=>a.localeCompare(b,'zh-CN'));
  $('history-work-tag').replaceChildren();
  for(const [value,label] of [['','全部作品'],['__untagged__','未分类'],...tags.map(t=>['tag:'+t,t])]){const o=element('option','',label);o.value=value;$('history-work-tag').append(o);}
  if([...$('history-work-tag').options].some(o=>o.value===selectedTag))$('history-work-tag').value=selectedTag;
  renderHistory();
}
function renderHistory() {
  const start=$('history-start').value,end=$('history-end').value;
  const invalid=start&&end&&start>end;
  const tag=$('history-work-tag').value;
  const jobs=invalid?[]:historyState.jobs.filter(job=>{const day=historyDay(job.created_at),tags=job.work_tags||[];return (!start||day>=start)&&(!end||(day&&day<=end))&&(!tag||(tag==='__untagged__'?!tags.length:tags.includes(tag.slice(4))));});
  const pages=Math.max(1,Math.ceil(jobs.length/historyState.pageSize));
  historyState.page=Math.max(1,Math.min(historyState.page,pages));
  $('history-prev').disabled=historyState.page===1;
  $('history-next').disabled=historyState.page===pages;
  $('history-page').textContent=`第 ${historyState.page} / ${pages} 页`;
  $('history-summary').textContent=invalid?'开始日期不能晚于结束日期。':`共 ${jobs.length} 条记录 · 每页 ${historyState.pageSize} 条 · 最新在前`;
  $('history-list').replaceChildren();
  if (!jobs.length) { $('history-list').append(element('div','card',invalid?'请调整日期范围。':start||end||tag?'当前筛选条件下没有创作记录。':'这里还没有作品。去开始创作，记录你的第一幅画面。')); return; }
  const offset=(historyState.page-1)*historyState.pageSize;
  for (const job of jobs.slice(offset,offset+historyState.pageSize)) {
    const card = element('article','card history-card');
    if (job.images.length) { const img = element('img','history-thumb'); img.src = '/api/images/' + job.id + '/0'; img.alt = job.description || '历史作品'; img.loading = 'lazy'; img.addEventListener('error',()=>img.replaceWith(element('div','history-placeholder','图片暂时无法读取，请刷新重试'))); card.append(img); }
    else card.append(element('div','history-placeholder','✦'));
    card.append(element('h3','history-title',job.description || job.positive_prompt || '手动提示词生图'),element('p','history-meta',new Date(job.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}) + ' · ' + (statusLabels[job.state] || job.state)),element('p','history-meta',job.provider==='upload'?'上传图片 · 无生成参数':job.workflow.name + ' · 种子 ' + job.values.seed));
    if (job.error) card.append(element('p','hint',job.error));
    const tags=element('div','history-work-tags');
    for(const tag of job.work_tags||[]){const chip=element('button','text-button',tag);chip.type='button';chip.addEventListener('click',()=>{$('history-work-tag').value='tag:'+tag;historyState.page=1;renderHistory();});tags.append(chip);}
    if(!(job.work_tags||[]).length)tags.append(element('span','hint','未分类'));
    const editTags=element('button','text-button','编辑标签');editTags.type='button';editTags.addEventListener('click',async()=>{
      const value=prompt('作品标签（用逗号分隔；留空设为未分类，最多10个）',(job.work_tags||[]).join('，'));
      if(value===null)return;editTags.disabled=true;
      try{await api('jobs/'+job.id+'/work-tags','PUT',{tags:value.split(/[,，、\n]/).map(s=>s.trim()).filter(Boolean)});await loadHistory();}catch(e){toast(e.message,true);}finally{editTags.disabled=false;}
    });tags.append(editTags);card.append(tags);
    const actions = element('div','action-row'), restore = element('button','button secondary','恢复到创作'), view = element('button','button secondary','查看任务'), remove = element('button','button danger','删除记录');
    restore.disabled = state.busy || job.provider==='upload';
    restore.addEventListener('click',()=>restoreJob(job).catch(e=>toast(e.message,true)));
    view.disabled = state.busy;
    view.addEventListener('click', () => {
      if (state.busy) { toast('当前操作仍在执行，请完成后再查看历史任务。',true); return; }
      if(job.provider==='upload'){window.open('/api/images/'+job.id+'/0','_blank','noopener');return;}
      if (job.edit_id) { window.EditPage.openJob(job).catch(e=>toast(e.message,true)); return; }
      page(job.provider === 'novelai' ? 'novelai' : 'create'); state.currentJob = job.id; jobStatus(job); showImages(job);
      setBusy(!['done','failed','unknown','interrupted'].includes(job.state)); if(state.busy)pollJob();
    });
    remove.disabled = !['done','failed','unknown','interrupted'].includes(job.state);
    remove.addEventListener('click',async()=>{if(!confirm('删除这条历史记录？ComfyUI 图片文件会保留。'))return;try{await api('jobs/'+job.id,'DELETE');await loadHistory();}catch(e){toast(e.message,true);}});
    const edit = element('button','button secondary','用此图改图');
    edit.disabled = !job.images.length || job.provider==='upload';
    edit.addEventListener('click',()=>window.EditPage.fromHistory(job).catch(e=>toast(e.message,true)));
    const redraw=element('button','button secondary','NAI 重绘');redraw.dataset.redrawJob=job.id;redraw.dataset.redrawIndex=0;redraw.disabled=job.state!=='done'||!job.images.length||job.provider==='upload';
    actions.append(restore,view,redraw,edit,remove);card.append(actions);$('history-list').append(card);
  }
}
['history-start','history-end'].forEach(id=>$(id).addEventListener('change',()=>{historyState.page=1;renderHistory();}));
$('history-work-tag').addEventListener('change',()=>{historyState.page=1;renderHistory();});
$('history-clear').addEventListener('click',()=>{$('history-start').value='';$('history-end').value='';$('history-work-tag').value='';historyState.page=1;renderHistory();});
$('history-prev').addEventListener('click',()=>{historyState.page--;renderHistory();});
$('history-next').addEventListener('click',()=>{historyState.page++;renderHistory();});
async function boot() {
  const bootstrap = await api('bootstrap'); state.token = bootstrap.token; state.config = bootstrap.config;
  fillConfig(); await reloadWorkflows();
  $('image-provider').value='comfyui'; providerChanged();
  await window.EditPage.init();
  await window.CharacterPage.init();
  const presetHost=element('div');presetHost.id='nai-preset-entry';
  $('positive-prompt').parentElement.insertBefore(presetHost,document.querySelector('label[for="positive-prompt"]'));
  promptSections=window.PromptSections.mount(presetHost,{api,element,toast});
  const chatHost=element('div');chatHost.className='nai-chat-host';presetHost.prepend(chatHost);
  window.NaiChat.mount(chatHost,{api,element,toast,key:'novelai',getCurrent:()=>promptSections.chatContext(),apply:data=>promptSections.applyChat(data)});
  window.PromptClearButtons.install();
  page('create');
  await window.ComicReviewPage.init({api, element, toast});
  options($('local-source'),(await api('workflow-sources')).map(name=>[name,name]));
  await checkComfy();
  const jobs = await api('jobs');
  const active = jobs.find(j=>!['done','failed','unknown','interrupted'].includes(j.state));
  if (active) { page(active.provider==='novelai'?'novelai':'create'); state.currentJob = active.id; setBusy(true); pollJob(); }
}

document.querySelectorAll('[data-page]').forEach(b=>b.addEventListener('click',()=>page(b.dataset.page)));
document.querySelectorAll('[data-goto]').forEach(b=>b.addEventListener('click',()=>page(b.dataset.goto)));
$('connection').addEventListener('click',()=>page('settings'));
$('workflow-select').addEventListener('change',()=>selectWorkflow($('workflow-select').value));
$('image-provider').addEventListener('change',providerChanged);
action('save-nai',async()=>{await saveNovelAI();toast('NovelAI 配置已保存。');});
action('clear-nai',async()=>{await saveNovelAI(true);$('nai-result').textContent='Token 已清除，尚未测试连接。';toast('NovelAI Token 已清除。');});
action('test-nai',async()=>{await saveNovelAI();$('nai-result').textContent='正在查询标签接口…';try{const r=await api('novelai/test','POST',{});$('nai-result').textContent=r.message;}catch(e){$('nai-result').textContent=e.message;throw e;}});
$('template-editor-select').addEventListener('change',editTemplate);
$('close-lightbox').addEventListener('click',()=>$('lightbox').close());
$('example').addEventListener('click',()=>{$('description').value='雨后的街角，一位穿着白色连衣裙的女孩撑着透明雨伞，霓虹灯映在湿润的路面上，电影感光线，细腻的日系插画。';});
action('generate-prompt',async()=>{const provider=$('image-provider').value;if(provider!=='novelai'&&!state.selected)throw new Error('请先选择工作流。');setBusy(true);try{const result=await api('prompt','POST',{provider,workflow_id:state.selected,description:$('description').value,template:$('template-select').value});$('positive-prompt').value=result.positive_prompt;$('negative-prompt').value=result.negative_prompt;window.CharacterPage.showMatched('character-matches',result.matched_characters||[]);toast('提示词已生成，可以编辑或直接生图。');}finally{setBusy(false);}});
action('render',()=>submit(false)); action('one-click',()=>submit(true));
action('save-config',async()=>{await saveConfig();toast('连接配置已保存。');});
action('clear-key',async()=>{await saveConfig(true);invalidateConnection();toast('密钥已清除。');});
action('test-api', testApiConnection);
action('fetch-models', fetchModels);
['api-url','api-key'].forEach(id => $(id).addEventListener('input', invalidateConnection));
$('api-model').addEventListener('input', () => { $('api-model-select').value = $('api-model').value.trim(); $('api-connection-state').textContent = '所选模型已变化，尚未测试。'; });
$('api-model-select').addEventListener('change', () => { if ($('api-model-select').value) { $('api-model').value = $('api-model-select').value; $('api-connection-state').textContent = '所选模型已变化，尚未测试。'; } });
action('test-comfy',async()=>{await saveConfig();if(await checkComfy())toast('ComfyUI 连接成功。');else throw new Error($('comfy-detail').textContent);});
action('start-comfy',async()=>{await saveConfig();toast('正在启动本地 ComfyUI，请稍候…');const result=await api('comfy/start','POST',{});await checkComfy();toast(result.message);});
action('save-template',async()=>{const key=$('template-editor-select').value;const updated={...state.config.templates,[key]:{name:$('template-name').value.trim(),text:$('template-text').value}};state.config=await api('config','PUT',{templates:updated});templates(currentWorkflow()?.template);toast('提示词模板已保存。');});
action('add-template',async()=>{const name=prompt('新模板名称');if(!name)return;const key='custom-'+Date.now();const updated={...state.config.templates,[key]:{name,text:state.config.templates[$('template-editor-select').value].text}};state.config=await api('config','PUT',{templates:updated});templates(currentWorkflow()?.template);$('template-editor-select').value=key;editTemplate();});
action('delete-template',async()=>{const key=$('template-editor-select').value;if(Object.keys(state.config.templates).length<=1)throw new Error('至少保留一个模板。');if(state.workflows.some(w=>w.template===key))throw new Error('此模板正在被工作流使用，请先更换工作流的默认模板。');const updated={...state.config.templates};delete updated[key];state.config=await api('config','PUT',{templates:updated});templates(currentWorkflow()?.template);toast('模板已删除。');});
$('workflow-file').addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;try{const workflow=await api('workflows','POST',{name:file.name.replace(/\.json$/i,''),graph:JSON.parse(await file.text())});await reloadWorkflows(workflow.id);toast('工作流已导入。');}catch(e){toast(e.message,true);}finally{event.target.value='';}});
action('import-source',async()=>{if(!$('local-source').value)throw new Error('未找到本机工作流文件。');const workflow=await api('workflows','POST',{name:$('local-source').value.replace(/\.json$/i,''),source:$('local-source').value});await reloadWorkflows(workflow.id);toast('工作流副本已导入，请配置模型与参数。');});
action('save-workflow',async()=>{const mappings={};document.querySelectorAll('[data-mapping]').forEach(select=>{if(select.value)mappings[select.dataset.mapping]=JSON.parse(select.value);});await api('workflows/'+state.selected,'PUT',{name:$('workflow-name').value,graph:JSON.parse($('workflow-json').value),values:readValues('default-'),mappings,template:$('workflow-template').value,fixed_positive:$('fixed-positive').value,fixed_negative:$('fixed-negative').value,random_seed:$('workflow-random').checked});await reloadWorkflows(state.selected);toast('工作流配置已保存。');});
action('export-workflow',async()=>downloadJson($('workflow-name').value+'.json',JSON.parse($('workflow-json').value)));
action('delete-workflow',async()=>{if(!confirm('删除本软件中的工作流副本？原始文件会保留。'))return;await api('workflows/'+state.selected,'DELETE');await reloadWorkflows();toast('工作流副本已删除。');});
action('suggest-mappings',async()=>{const fields=currentWorkflow().inspection.fields;const suggestions=fields.filter(f=>['text','width','height','seed','noise_seed','steps','cfg','sampler_name','scheduler','ckpt_name','unet_name'].includes(f.field)).map(f=>f.node+' · '+f.title+' / '+f.field);$('mapping-suggestions').textContent=suggestions.length?'可映射字段：'+suggestions.join('；')+'。请手动确认正负提示词对应节点。':'没有找到常见字段，请查看工作流 JSON。';});
boot().catch(error=>toast(error.message,true));

window.addEventListener('nai-chat-config-changed',async()=>{try{state.config=await api('config');fillConfig();}catch(e){toast(e.message,true);}});
