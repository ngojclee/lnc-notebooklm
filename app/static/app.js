const $=s=>document.querySelector(s);
const PAGE_NAMES={status:'Status',credentials:'Credentials',inbox:'Inbox',keys:'API keys',settings:'Settings'};

async function api(path,opts={}){
  const r=await fetch(path,{headers:{'content-type':'application/json'},...opts});
  let j;try{j=await r.json()}catch{j={ok:false,error:'http_'+r.status}}
  if(r.status===401&&location.pathname!=='/login.html'){location='/login.html'}
  return j;
}

/* ---------- toast ---------- */
function toast(msg,type=''){
  const t=document.createElement('div');t.className='toast '+type;
  t.innerHTML='<div class="msg"></div><button class="x" aria-label="dismiss">×</button>';
  t.querySelector('.msg').textContent=msg;
  t.querySelector('.x').onclick=()=>t.remove();
  $('#toasts').append(t);setTimeout(()=>t.remove(),4500);
}

/* ---------- confirm modal ---------- */
function confirmDlg(title,body,okLabel='Confirm',danger=true){
  return new Promise(res=>{
    const back=document.createElement('div');back.className='modal-back';
    back.innerHTML=`<div class="modal-card"><h3></h3><p></p>
      <div class="acts"><button class="btn" data-x>Cancel</button>
      <button class="btn ${danger?'btn-danger':'btn-primary'}" data-ok></button></div></div>`;
    back.querySelector('h3').textContent=title;
    back.querySelector('p').textContent=body;
    back.querySelector('[data-ok]').textContent=okLabel;
    const done=v=>{back.remove();res(v)};
    back.onclick=e=>{if(e.target===back)done(false)};
    back.querySelector('[data-x]').onclick=()=>done(false);
    back.querySelector('[data-ok]').onclick=()=>done(true);
    document.body.append(back);
    back.querySelector('[data-ok]').focus();
  });
}

/* ---------- helpers ---------- */
function copyChip(text){
  return `<span class="copy-chip"><code>${esc(text)}</code><button class="copy-btn" title="Copy">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button></span>`;
}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function badge(txt,cls){return `<span class="badge ${cls}">${txt}</span>`}
document.addEventListener('click',async e=>{
  const b=e.target.closest('.copy-btn');if(!b)return;
  const code=b.parentElement.querySelector('code');
  try{await navigator.clipboard.writeText(code.textContent);toast('Copied.','ok')}catch{toast('Copy failed.','err')}
});

/* ---------- nav ---------- */
function nav(){
  const h=location.hash||'#status';
  document.querySelectorAll('.page').forEach(p=>p.classList.toggle('on','#'+p.id===h));
  document.querySelectorAll('.side a[href^="#"]').forEach(a=>a.classList.toggle('on',a.getAttribute('href')===h));
  const name=PAGE_NAMES[h.slice(1)]||'Status';
  $('#crumb').innerHTML='<b>'+name+'</b>';
}
addEventListener('hashchange',nav);

/* ---------- status ---------- */
async function loadStatus(){
  const j=await api('/api/status');
  const el=$('#status-body');
  if(!j.ok){el.innerHTML='<div class="empty">Unavailable: '+esc(j.error||'error')+'</div>';return}
  const n=j.nblm||{};
  const authOk=!!n.authenticated;
  if($('#authuser-in')&&document.activeElement!==$('#authuser-in'))$('#authuser-in').value=n.authuser??0;
  $('#ver').textContent='v'+j.version;
  $('#health').className='pill '+(j.ok?'ok':'bad');
  $('#health').innerHTML='<span class="dot"></span>'+(j.ok?'online':'down');
  $('#cred-state').innerHTML=authOk?badge('configured','b-ok'):badge('missing cookies','b-warn');
  el.innerHTML=`
    <div class="stat-grid" style="margin-bottom:16px">
      <div class="stat"><div class="l">Auth state</div><div class="n">${authOk?badge('ready','b-ok'):badge('needs cookies','b-warn')}</div></div>
      <div class="stat"><div class="l">Cookie age</div><div class="n">${n.cookie_age_days!=null?n.cookie_age_days+' d':'—'}</div></div>
      <div class="stat"><div class="l">Inbox files</div><div class="n">${j.inbox_files??0}</div></div>
      <div class="stat"><div class="l">Google account</div><div class="n" style="font-size:13px;font-weight:500">${esc(n.account||'—')} <span class="muted">· authuser ${n.authuser??0}${n.base_host?' · '+esc(n.base_host):''}</span></div></div>
    </div>
    <dl class="kv">
      <dt>Service</dt><dd>${esc(j.service)} ${esc(j.version)}</dd>
      <dt>MCP endpoint</dt><dd>${copyChip(j.mcp_endpoint)}</dd>
    </dl>`;
}
async function loadUsage(){
  const j=await api('/api/admin/usage');
  const el=$('#usage-body');
  if(!j.ok){el.innerHTML='<div class="empty">'+(esc(j.error)||'unavailable')+'</div>';return}
  const u=j.usage;
  if(u&&typeof u==='object'){
    const rows=Object.entries(u).map(([k,v])=>`<dt>${esc(k)}</dt><dd>${typeof v==='object'?'<code>'+esc(JSON.stringify(v))+'</code>':esc(v??'—')}</dd>`).join('');
    el.innerHTML='<dl class="kv">'+rows+'</dl>';
  }else el.innerHTML='<pre class="code-block">'+esc(JSON.stringify(u,null,1))+'</pre>';
}

/* ---------- settings ---------- */
async function loadSettings(){
  const j=await api('/api/settings');
  const s=j.settings||{};
  $('#rpc-overrides').value=JSON.stringify(s.rpc_overrides||{},null,1);
  const pb=s.public_base_url||'';
  if(pb!=='(auto from request)')$('#set-public-base').value=pb;
  const env=s.env||{};
  const setState=(id,v)=>{const el=$(id);el.className='badge '+(v==='unset'?'b-dim':'b-ok');el.textContent=v};
  setState('#st-bearer',env.CONNECTOR_BEARER||'unset');
  setState('#st-secret',env.DELEGATION_SECRET||'unset');
  const rows={
    'data dir':s.data_dir,
    'public base url':s.public_base_url,
    'allowed hosts':(s.allowed_hosts||[]).join(', ')||'* (any)',
    'management key':env.MANAGEMENT_KEY,
    'LNC_API_KEYS':env.LNC_API_KEYS,
    'connector bearer':env.CONNECTOR_BEARER,
    'delegation secret':env.DELEGATION_SECRET,
    'nlm data path':env.NOTEBOOKLM_MCP_CLI_PATH,
    'headless refresh':env.NOTEBOOKLM_DISABLE_HEADLESS_REFRESH==='1'?'disabled':'enabled',
    'default notebook':s.default_notebook_id||'—',
  };
  $('#settings-body').innerHTML=Object.entries(rows).map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v??'—')}</dd>`).join('');
}

/* ---------- files ---------- */
async function loadFiles(){
  const j=await api('/api/files');
  const tb=$('#files-body');
  if(!j.ok||!j.files?.length){tb.innerHTML='<tr><td colspan="5" class="empty">Inbox is empty — upload a file to add it as a NotebookLM source.</td></tr>';return}
  tb.innerHTML=j.files.map(f=>`<tr>
    <td><span class="file-ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg></span></td>
    <td>${esc(f.name)}</td>
    <td class="num">${(f.size/1024).toFixed(1)} KB</td>
    <td>${copyChip(f.path)}</td>
    <td><div class="row-actions"><button class="btn btn-sm btn-danger" data-del="${esc(f.name)}">Delete</button></div></td></tr>`).join('');
}
$('#files-body').onclick=async ev=>{
  const name=ev.target.dataset?.del;if(!name)return;
  if(!await confirmDlg('Delete file',`Remove "${name}" from the inbox?`,'Delete'))return;
  const j=await api('/api/files/'+encodeURIComponent(name),{method:'DELETE'});
  toast(j.ok?'File deleted.':'Delete failed: '+(j.error||''),j.ok?'ok':'err');
  loadFiles();
};
$('#file-in').onchange=async ev=>{
  const f=ev.target.files[0];if(!f)return;
  const fd=new FormData();fd.append('file',f);
  const r=await fetch('/api/files',{method:'POST',body:fd});const j=await r.json();
  toast(j.ok?`Uploaded ${f.name}.`:'Upload failed: '+(j.error||r.status),j.ok?'ok':'err');
  loadFiles();ev.target.value='';
};

/* ---------- keys ---------- */
async function loadKeys(){
  const j=await api('/api/admin/keys');
  const tb=$('#keys-body');
  if(!j.ok){tb.innerHTML='<tr><td colspan="6" class="empty">Failed: '+esc(j.error)+'</td></tr>';return}
  const rows=j.keys.map(r=>`<tr><td>${esc(r.label||'')}</td><td><code>${esc(r.prefix)}…</code></td>
    <td>${r.source==='env'?badge('env','b-plum'):badge('ui','b-dim')}</td>
    <td>${r.enabled?badge('enabled','b-ok'):badge('disabled','b-dim')}</td>
    <td>${new Date((r.created||0)*1000).toLocaleDateString()}</td>
    <td><div class="row-actions">
      <button class="btn btn-sm" data-tgl="${r.id}">${r.enabled?'Disable':'Enable'}</button>
      <button class="btn btn-sm btn-danger" data-del="${r.id}">Revoke</button></div></td></tr>`);
  let pending=0;
  for(const e of j.env||[]){
    if(e.adopted)continue;
    if(e.revoked)rows.push(`<tr><td>—</td><td><code>${esc(e.prefix)}…</code></td>
      <td>${badge('env','b-plum')}</td><td>${badge('revoked','b-err')}</td><td>—</td><td></td></tr>`);
    else{pending++;rows.push(`<tr><td>—</td><td><code>${esc(e.prefix)}…</code></td>
      <td>${badge('env','b-plum')}</td><td>${badge('unmanaged','b-warn')}</td><td>—</td><td></td></tr>`)}
  }
  if(!rows.length)rows.push('<tr><td colspan="6" class="empty">No keys yet — create one above or set LNC_API_KEYS.</td></tr>');
  tb.innerHTML=rows.join('');
  $('#adopt-env').hidden=!pending;
}
$('#adopt-env').onclick=async()=>{
  const j=await api('/api/admin/keys/adopt',{method:'POST',body:'{}'});
  if(j.ok){toast(`Adopted ${j.adopted} env key(s) — now managed here.`,'ok');$('#key-result').hidden=true}
  else toast('Adopt failed: '+(j.error||''),'err');
  loadKeys();
};
$('#create-key').onclick=async()=>{
  const j=await api('/api/admin/keys',{method:'POST',body:JSON.stringify({label:$('#key-label').value})});
  const el=$('#key-result');el.hidden=false;
  if(j.ok){el.className='alert alert-info';
    el.innerHTML='New key — shown once, copy it now: '+copyChip(j.key);
    $('#key-label').value=''}
  else{el.className='alert alert-danger';el.textContent='Failed: '+j.error}
  loadKeys();
};
$('#keys-body').onclick=async ev=>{
  const t=ev.target.dataset?.tgl,d=ev.target.dataset?.del;
  if(t){await api('/api/admin/keys/'+t+'/toggle',{method:'POST'});loadKeys()}
  if(d&&await confirmDlg('Revoke API key','Agents using this key lose access immediately. This cannot be undone.','Revoke')){
    const j=await api('/api/admin/keys/'+d,{method:'DELETE'});
    toast(j.ok?'Key revoked.':'Revoke failed: '+(j.error||''),j.ok?'ok':'err');loadKeys();
  }
};

/* ---------- credentials / settings actions ---------- */
$('#save-cookies').onclick=async()=>{
  const el=$('#cred-result');el.hidden=false;el.className='alert alert-info';el.textContent='Saving…';
  const j=await api('/api/admin/credentials',{method:'POST',body:JSON.stringify({cookies:$('#cookie-in').value,authuser:Number($('#authuser-in').value||0)})});
  const who=j.account?` — ${j.account} (authuser ${j.authuser}, ${j.base_host})`:'';
  if(j.ok){el.className='alert alert-success';el.textContent='Cookies saved and validated'+who;$('#cookie-in').value=''}
  else{el.className='alert alert-danger';el.textContent='Failed: '+(j.detail||j.error||'unknown error')+(j.missing?' — missing '+j.missing.join(', '):'')+(j.hint?' — '+j.hint:'')+who}
  loadStatus();
};
$('#refresh-cookies').onclick=async()=>{
  const el=$('#cred-result');el.hidden=false;el.className='alert alert-info';el.textContent='Rotating…';
  const j=await api('/api/admin/refresh',{method:'POST',body:'{}'});
  el.className='alert '+(j.ok?'alert-success':'alert-danger');
  el.textContent=j.ok?'Cookies rotated.':'Rotation failed: '+(j.detail||j.error||'');
  loadStatus();
};
$('#validate-live').onclick=async()=>{
  const r=await api('/api/admin/validate',{method:'POST',body:'{}'});await loadStatus();
  toast(r.ok?'Live auth OK — upstream accepted the session.':'Live auth failed: '+(r.detail||r.error||''),r.ok?'ok':'err');
};
$('#save-settings').onclick=async()=>{
  const body={public_base_url:$('#set-public-base').value.trim()};
  const cb=$('#set-conn-bearer').value.trim();if(cb)body.connector_bearer=cb;
  const ds=$('#set-del-secret').value.trim();if(ds)body.delegation_secret=ds;
  const j=await api('/api/admin/settings',{method:'POST',body:JSON.stringify(body)});
  if(j.ok){$('#set-conn-bearer').value='';$('#set-del-secret').value='';toast('Settings saved.','ok');loadSettings()}
  else toast('Save failed: '+j.error,'err');
};
$('#save-overrides').onclick=async()=>{
  let v=null;const raw=$('#rpc-overrides').value.trim();
  if(raw){try{v=JSON.parse(raw)}catch{toast('Invalid JSON.','err');return}}
  const j=await api('/api/admin/rpc-overrides',{method:'POST',body:JSON.stringify({overrides:v})});
  toast(j.ok?'Overrides saved.':'Save failed: '+j.error,j.ok?'ok':'err');loadSettings();
};
$('#mint-key').onclick=async()=>{
  const j=await api('/api/admin/mint-key',{method:'POST',body:'{}'});
  location.hash='#keys';
  const el=$('#key-result');
  if(j.ok){el.hidden=false;el.className='alert alert-info';
    el.innerHTML='New key — shown once, copy it now: '+copyChip(j.key)}
  else{el.hidden=false;el.className='alert alert-danger';el.textContent='Mint failed: '+(j.error||'')}
  loadKeys();
};

$('#signout').onclick=e=>{e.preventDefault();api('/api/auth/logout',{method:'POST'}).then(()=>location='/login.html')};
$('#refresh-status').onclick=loadStatus;
$('#refresh-usage').onclick=loadUsage;
$('#refresh-files').onclick=loadFiles;
$('#refresh-keys').onclick=loadKeys;

nav();loadStatus();loadUsage();loadSettings();loadFiles();loadKeys();
