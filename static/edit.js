'use strict';
const editState = {session:null, original:null, overlay:null, points:[], maskJob:null, ready:false, busy:false, opening:false, job:null, timer:null, sequence:0};
const editControls = ['edit-file','edit-region','edit-threshold','edit-clear-points','edit-select','edit-prompt','edit-render'];

function editPixelPoint(event, canvas, size) {
  const rect = canvas.getBoundingClientRect();
  return {x:Math.max(0, Math.min(size.width-1, Math.floor((event.clientX-rect.left)*size.width/rect.width))),
    y:Math.max(0, Math.min(size.height-1, Math.floor((event.clientY-rect.top)*size.height/rect.height)))};
}
function editInvalidate() {
  editState.maskJob = null; editState.ready = false; editState.overlay = null;
  $('edit-confirm').checked = false; $('edit-confirm').disabled = true; $('edit-render').disabled = true;
  $('edit-mask-info').textContent = '需要重新定位';
}
function editBusy(busy) {
  editState.busy = busy;
  editControls.forEach(id => $(id).disabled = busy);
  $('edit-confirm').disabled = busy || !editState.ready;
  $('edit-render').disabled = busy || !editState.ready || !$('edit-confirm').checked;
}
function editDraw() {
  if (!editState.original) return;
  const canvas = $('edit-canvas'), ctx = canvas.getContext('2d');
  ctx.clearRect(0,0,canvas.width,canvas.height); ctx.drawImage(editState.original,0,0);
  if (editState.overlay && $('edit-show-mask').checked) ctx.drawImage(editState.overlay,0,0);
  for (const point of editState.points) {
    ctx.beginPath(); ctx.arc(point.x,point.y,Math.max(5,canvas.width/130),0,2*Math.PI);
    ctx.fillStyle = point.kind === 'positive' ? '#91e86d' : '#ff6969'; ctx.fill();
    ctx.lineWidth = Math.max(2,canvas.width/500); ctx.strokeStyle='#fff'; ctx.stroke();
  }
}
function editImage(url) {
  return new Promise((resolve,reject)=>{const img = new Image(); img.onload=()=>resolve(img);img.onerror=()=>reject(new Error('图片读取失败，请检查 ComfyUI 连接。'));img.src=url;});
}
async function editOpen(session) {
  const original = await editImage(session.image_url);
  clearTimeout(editState.timer); editState.sequence++;
  editState.session=session;editState.original=original;editState.points=[];editState.job=null;
  editInvalidate();
  const canvas=$('edit-canvas');canvas.width=session.width;canvas.height=session.height;canvas.hidden=false;$('edit-empty').hidden=true;
  $('edit-source-info').textContent=`原图 ${session.width} × ${session.height} · WAI Illustrious SDXL`;
  $('edit-results').replaceChildren();$('edit-status').textContent='';$('edit-resume').hidden=true;
  localStorage.setItem('studio-edit-id', session.id);
  editDraw();editBusy(editState.opening);page('edit');
}
async function editUpload(file) {
  const form=new FormData();form.append('image',file,file.name||'original.png');
  const response=await fetch('/api/edit/upload',{method:'POST',headers:{'X-Studio-Token':state.token},body:form});
  const session=await response.json();if(!response.ok)throw new Error(session.error||'上传失败。');
  await editOpen(session);
}
function editRequest() {
  if(!editState.session)throw new Error('请先上传原图。');
  return {edit_id:editState.session.id,region:$('edit-region').value,description:$('edit-description').value,preserve:$('edit-preserve').value};
}
async function editMask(job, sequence) {
  if(!job.mask_ready)throw new Error('选区尚未验证，请重新定位。');
  const image=await editImage('/api/images/'+job.id+'/0');
  if(sequence!==editState.sequence)return;
  const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;
  if(image.width!==editState.session.width||image.height!==editState.session.height)throw new Error('选区与原图尺寸不一致，请重新定位。');
  const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);const data=ctx.getImageData(0,0,image.width,image.height);
  let count=0;
  for(let i=0;i<data.data.length;i+=4){const alpha=data.data[i];if(alpha>=128)count++;data.data[i]=116;data.data[i+1]=230;data.data[i+2]=104;data.data[i+3]=Math.round(alpha*.45);}
  if(!count)throw new Error('没有识别到修改区域，请调整部位、点选或阈值。');
  ctx.putImageData(data,0,0);editState.overlay=canvas;editState.maskJob=job.id;editState.ready=true;
  $('edit-confirm').checked=false;$('edit-mask-info').textContent='选区约 '+Math.round(100*count/(image.width*image.height))+'%';editDraw();
}
function editResult(job) {
  const box=$('edit-results');box.replaceChildren();
  job.images.forEach((record,index)=>{const url='/api/images/'+job.id+'/'+index;
    const img=element('img','result-img');img.src=url;img.alt='WAI 局部重绘结果';
    img.addEventListener('click',()=>{$('lightbox-image').src=url;$('lightbox').showModal();});
    const link=element('a','button secondary','下载修改结果');link.href=url;link.download=record.filename;box.append(img,link);
  });
  box.append(element('p','hint','WAI Illustrious SDXL · 种子 '+job.values.seed));
}
async function editPoll() {
  clearTimeout(editState.timer);
  const id=editState.job,sequence=editState.sequence;if(!id)return;
  try {
    const job=await api('jobs/'+id);if(id!==editState.job||sequence!==editState.sequence)return;
    $('edit-status').textContent=(statusLabels[job.state]||job.state)+(job.progress?' · '+job.progress.value+'/'+job.progress.max:'')+(job.error||job.notice?'\n'+(job.error||job.notice):'');
    if(['done','failed','unknown','interrupted'].includes(job.state)) {
      if(job.state==='done') {
        if(job.edit_purpose==='selection')await editMask(job,sequence);else editResult(job);
      }
      if(sequence!==editState.sequence)return;
      editBusy(false);$('edit-resume').hidden=!(job.state==='unknown'&&job.prompt_id);
      return;
    }
  } catch(error){if(sequence!==editState.sequence)return;$('edit-status').textContent=error.message;
    // An image/selection validation error is terminal; a network query error keeps the same job.
    if(error.message.includes('选区')||error.message.includes('修改区域')||error.message.includes('图片读取')){editBusy(false);return;}}
  if(id===editState.job&&sequence===editState.sequence)editState.timer=setTimeout(editPoll,2000);
}
function editTrack(job) {
  clearTimeout(editState.timer);editState.sequence++;editState.job=job.id;editBusy(true);editPoll();
}
async function editOpenJob(job) {
  if(editState.busy)throw new Error('当前改图任务仍在执行，请完成后再切换。');
  editState.opening=true;editBusy(true);
  try {
  const session=await api('edit/'+job.edit_id);await editOpen(session);
  $('edit-region').value=session.region||'';$('edit-description').value=session.description||'';$('edit-preserve').value=session.preserve||'';
  editState.points=[...(session.positive_coords||[]).map(p=>({...p,kind:'positive'})),...(session.negative_coords||[]).map(p=>({...p,kind:'negative'}))];editDraw();
  const latest=session.selection_job_id;
  if(latest){const selection=await editOptionalJob(latest);if(selection?.state==='done'&&selection.mask_ready)await editMask(selection,editState.sequence);}
  if(job.edit_purpose==='render'){$('edit-positive').value=job.positive_prompt;$('edit-negative').value=job.negative_prompt;}
  if(job.edit_purpose==='selection'&&job.id!==latest){const selection=await editOptionalJob(latest);if(selection)job=selection;}
  const latestOperation=editLatestJob(session);
  if(latestOperation&&latestOperation!==job.id){const active=await editOptionalJob(latestOperation);if(active&&!['done','failed','unknown','interrupted'].includes(active.state))job=active;}
  editTrack(job);
  }catch(error){editBusy(false);throw error;}finally{editState.opening=false;}
}
function editLatestJob(session) { return session.latest_job_id || session.selection_job_id || session.render_job_id; }
async function editOptionalJob(id) { try{return await api('jobs/'+id);}catch(error){if(error.status===404)return null;throw error;} }
async function editAction(callback) {
  if(editState.busy)return;
  const sequence=editState.sequence;
  editBusy(true);
  try{await callback();}catch(error){toast(error.message,true);}
  finally{if(sequence===editState.sequence||!editState.busy)editBusy(false);}
}
window.EditPage={
  openJob:editOpenJob,
  async fromHistory(job){if(editState.busy)throw new Error('请先完成当前改图任务。');editState.opening=true;editBusy(true);
    try{const response=await fetch('/api/images/'+job.id+'/0');if(!response.ok)throw new Error('历史图片无法读取。');await editUpload(new File([await response.blob()],'history.png',{type:'image/png'}));}
    finally{editState.opening=false;editBusy(false);}},
  async init(){
    $('edit-file').addEventListener('change',()=>editAction(async()=>{const file=$('edit-file').files[0];if(file)await editUpload(file);editState.busy=false;}));
    ['edit-region','edit-threshold'].forEach(id=>$(id).addEventListener('input',()=>{editInvalidate();editDraw();}));
    $('edit-clear-points').addEventListener('click',()=>{editState.points=[];editInvalidate();editDraw();});
    $('edit-canvas').addEventListener('click',event=>{if(editState.busy||!editState.session)return;const kind=$('edit-point-mode').value;
      if(editState.points.filter(p=>p.kind===kind).length>=40){toast('每类点选最多 40 个。',true);return;}
      editState.points.push({...editPixelPoint(event,$('edit-canvas'),editState.session),kind});editInvalidate();editDraw();});
    $('edit-show-mask').addEventListener('change',editDraw);
    $('edit-confirm').addEventListener('change',()=>editBusy(editState.busy));
    $('edit-select').addEventListener('click',()=>editAction(async()=>{const body=editRequest();editInvalidate();
      body.threshold=Number($('edit-threshold').value);for(const kind of ['positive','negative'])body[kind+'_coords']=editState.points.filter(p=>p.kind===kind).map(({x,y})=>({x,y}));
      const job=await api('edit/select','POST',body);editTrack(job);}));
    $('edit-prompt').addEventListener('click',()=>editAction(async()=>{try{const result=await api('edit/prompt','POST',editRequest());$('edit-positive').value=result.positive_prompt;$('edit-negative').value=result.negative_prompt;window.CharacterPage.showMatched('edit-character-matches',result.matched_characters||[]);}finally{editState.busy=false;}}));
    $('edit-render').addEventListener('click',()=>editAction(async()=>{if(!editState.ready||!$('edit-confirm').checked)throw new Error('请确认当前选区。');
      const body={...editRequest(),mask_job_id:editState.maskJob,confirmed:true,positive_prompt:$('edit-positive').value,negative_prompt:$('edit-negative').value};
      for(const name of ['denoise','steps','cfg','grow','blur'])body[name]=Number($('edit-'+name).value);
      const job=await api('edit/render','POST',body);editTrack(job);}));
    $('edit-resume').addEventListener('click',()=>editAction(async()=>{const job=await api('jobs/'+editState.job+'/resume','POST',{});$('edit-resume').hidden=true;editTrack(job);}));
    const id=localStorage.getItem('studio-edit-id');
    if(id){try{const session=await api('edit/'+id);const jobId=editLatestJob(session);
      const job=jobId?await editOptionalJob(jobId):null;
      if(job)await editOpenJob(job);else await editOpen(session);
      page('create');}catch{localStorage.removeItem('studio-edit-id');editBusy(false);}}
    editBusy(editState.busy);
  }
};
