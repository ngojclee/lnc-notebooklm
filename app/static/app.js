const $=s=>document.querySelector(s);
async function api(path,opts={}){
  const r=await fetch(path,{headers:{'content-type':'application/json'},...opts});
  let j;try{j=await r.json()}catch{j={ok:false,error:'http_'+r.status}}
  if(r.status===401&&location.pathname!=='/login.html'){location='/login.html'}
  return j;
}
function kv(obj){return '<dl class="kv">'+Object.entries(obj).map(([k,v])=>`<dt>${k}</dt><dd>${typeof v==='object'?JSON.stringify(v):v??'—'}</dd>`).join('')+'</dl>'}

// nav
function nav(){const h=location.hash||'#status';
  document.querySelectorAll('.page').forEach(p=>p.classList.toggle('on','#'+p.id===h));
  document.querySelectorAll('.topnav a').forEach(a=>a.classList.toggle('on',a.getAttribute('href')===h));}
addEventListener('hashchange',nav);

async function loadStatus(){
  const j=await api('/api/status');
  if(!j.ok){$('#status-body').innerHTML='<div class="empty">failed: '+j.error+'</div>';return}
  const n=j.nblm||{};
  $('#ver').textContent='v'+j.version;
  $('#status-body').innerHTML=kv({
    service:j.service,version:j.version,'mcp endpoint':j.mcp_endpoint,
    'auth state':n.state,'cookie age (days)':n.cookie_age_days??'—',
    'inbox files':j.inbox_files,profiles:(n.profiles||[]).join(', ')||'—'});
  $('#health').className='pill '+(j.ok?'ok':'bad');
  $('#health').textContent=j.ok?'up':'down';
}
async function loadUsage(){
  const j=await api('/api/admin/usage');
  $('#usage-body').innerHTML=j.ok?'<pre class="code-block">'+JSON.stringify(j.usage,null,1)+'</pre>'
    :'<div class="empty">'+(j.error||'unavailable')+'</div>';
}
async function loadSettings(){
  const j=await api('/api/settings');
  $('#settings-body').textContent=JSON.stringify(j.settings,null,1);
  $('#rpc-overrides').value=JSON.stringify(j.settings?.rpc_overrides||{},null,1);
  const pb=j.settings?.public_base_url||'';
  if(pb!=='(auto from request)')$('#set-public-base').value=pb;
}
async function loadFiles(){
  const j=await api('/api/files');
  const tb=$('#files-body');
  if(!j.ok||!j.files?.length){tb.innerHTML='<tr><td colspan="4" class="empty">no files yet</td></tr>';return}
  tb.innerHTML=j.files.map(f=>`<tr><td>${f.name}</td><td>${(f.size/1024).toFixed(1)} KB</td>
    <td><code>${f.path}</code></td><td><button class="btn btn-sm btn-danger" data-del="${f.name}">delete</button></td></tr>`).join('');
}

$('#save-cookies').onclick=async()=>{
  const el=$('#cred-result');el.hidden=false;el.className='alert';el.textContent='saving…';
  const j=await api('/api/admin/credentials',{method:'POST',body:JSON.stringify({cookies:$('#cookie-in').value})});
  el.className='alert '+(j.ok?'alert-success':'alert-danger');
  el.textContent=JSON.stringify(j,null,1);loadStatus();
};
$('#refresh-cookies').onclick=async()=>{
  const el=$('#cred-result');el.hidden=false;el.className='alert';el.textContent='rotating…';
  const j=await api('/api/admin/refresh',{method:'POST',body:'{}'});
  el.className='alert '+(j.ok?'alert-success':'alert-danger');el.textContent=JSON.stringify(j,null,1);loadStatus();
};
$('#validate-live').onclick=async()=>{
  $('#status-body').innerHTML='<div class="empty">validating against live API…</div>';
  const j=await api('/api/admin/validate',{method:'POST',body:'{}'});await loadStatus();
  $('#status-body').innerHTML+=(j.ok?'<div class="alert alert-success">live auth OK</div>'
    :'<div class="alert alert-danger">live auth failed: '+(j.detail||j.error)+'</div>');
};
$('#file-in').onchange=async ev=>{
  const f=ev.target.files[0];if(!f)return;
  const fd=new FormData();fd.append('file',f);
  const r=await fetch('/api/files',{method:'POST',body:fd});const j=await r.json();
  if(!j.ok)alert('upload failed: '+(j.error||r.status));loadFiles();ev.target.value='';
};
$('#files-body').onclick=async ev=>{
  const name=ev.target.dataset?.del;if(!name)return;
  await api('/api/files/'+encodeURIComponent(name),{method:'DELETE'});loadFiles();
};
$('#save-settings').onclick=async()=>{
  const body={};
  const pb=$('#set-public-base').value.trim();if(pb||pb==='')body.public_base_url=pb;
  const cb=$('#set-conn-bearer').value.trim();if(cb)body.connector_bearer=cb;
  const ds=$('#set-del-secret').value.trim();if(ds)body.delegation_secret=ds;
  const j=await api('/api/admin/settings',{method:'POST',body:JSON.stringify(body)});
  if(j.ok){$('#set-conn-bearer').value='';$('#set-del-secret').value='';alert('saved');loadSettings()}
  else alert('failed: '+j.error);
};
$('#save-overrides').onclick=async()=>{
  let v=null;const raw=$('#rpc-overrides').value.trim();
  if(raw){try{v=JSON.parse(raw)}catch{alert('invalid JSON');return}}
  const j=await api('/api/admin/rpc-overrides',{method:'POST',body:JSON.stringify({overrides:v})});
  alert(j.ok?'saved':'failed: '+j.error);loadSettings();
};
$('#mint-key').onclick=async()=>{
  const j=await api('/api/admin/mint-key',{method:'POST',body:'{}'});
  alert('key: '+j.key+'\n\n'+j.note);
};
$('#signout').onclick=()=>api('/api/auth/logout',{method:'POST'}).then(()=>location='/login.html');
$('#refresh-status').onclick=loadStatus;
$('#refresh-usage').onclick=loadUsage;
$('#refresh-files').onclick=loadFiles;

nav();loadStatus();loadUsage();loadSettings();loadFiles();
