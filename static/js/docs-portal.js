(() => {
  'use strict';
  const page=document.querySelector('.docs-page'); if(!page)return;
  const motion=window.matchMedia('(prefers-reduced-motion: reduce)');
  const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const port=page.dataset.authorizePort||'8080';
  let language='python',endpoint='authorize',responseMode='success';
  let requestSource='',responseSource='',copyTimer;
  const requestEl=document.getElementById('referenceRequest'),responseEl=document.getElementById('referenceResponse');
  function highlight(line){
    const token=/("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\/\/[^\n]*|#[^\n]*|\b(?:import|from|const|let|await|fetch|print|true|false|null|json|requests)\b|\b\d+\b)/g;
    let result='',last=0;
    for(const m of line.matchAll(token)){
      result+=escape(line.slice(last,m.index));const t=m[0];
      let type=t.startsWith('//')||t.startsWith('#')?'comment':/^["']/.test(t)?(line.slice(m.index+t.length).trimStart().startsWith(':')?'key':'string'):/^\d/.test(t)?'number':'keyword';
      result+=`<span class="syntax-${type}">${escape(t)}</span>`;last=m.index+t.length;
    }
    return result+escape(line.slice(last));
  }
  function renderCode(el,text){
    el.innerHTML=text.split('\n').map((line,i)=>`<span data-line="${i+1}">${highlight(line)||' '}</span>`).join('');
    if(!motion.matches)el.animate([{opacity:.25,transform:'translateY(3px)'},{opacity:1,transform:'none'}],{duration:140,easing:'ease-out'});
  }
  function updateExamples(){
    const url=`http://127.0.0.1:${port}/${endpoint==='verify'?'verify_auth_code':'authorize'}`;
    const payload=endpoint==='verify'?{auth_code:'<auth_code>'}:{cpu_id:'BFEBFBFF000906EA',encrypted_info:'<encrypted_payload>',tenant:'yuanyi'};
    const json=JSON.stringify(payload,null,2);
    if(language==='python')requestSource=`import requests\n\npayload = ${json}\n\nresponse = requests.post(\n  "${url}",\n  json=payload,\n  timeout=10,\n)\n\nprint(response.json())`;
    else if(language==='curl')requestSource=`curl -X POST "${url}" \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify(payload)}'`;
    else requestSource=`const payload = ${json};\n\nconst response = await fetch(\n  "${url}",\n  {\n    method: "POST",\n    headers: {\n      "Content-Type": "application/json"\n    },\n    body: JSON.stringify(payload)\n  }\n);\n\nconsole.log(await response.json());`;
    const sample=endpoint==='verify'?(responseMode==='success'?{valid:true,payload:{cpu_id:'BFEBFBFF000906EA',motherboard_id:'MB-A621',disk_id:'WD-135822',memory_id:'RAM-8602',issued_at:'2026-11-02 09:42:35'}}:{valid:false,error:'授权码已过期'}):(responseMode==='success'?{auth_code:'<auth_code>',device_fingerprint:'d4e8a21c7b903f61',hardware_changed:false,is_new_device:true,tenant:'yuanyi'}:{error:'CPU不在白名单'});
    responseSource=JSON.stringify(sample,null,2);renderCode(requestEl,requestSource);renderCode(responseEl,responseSource);
  }
  function showStatus(text){const el=document.getElementById('copyStatus');clearTimeout(copyTimer);el.textContent=text;el.classList.add('is-visible');copyTimer=setTimeout(()=>el.classList.remove('is-visible'),1600);}
  async function copy(text,button){
    const label=button.querySelector('span');const old=label?.textContent;
    try{
      if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(text);
      else{
        const el=document.createElement('textarea');el.value=text;el.style.cssText='position:fixed;opacity:0;left:-9999px';document.body.appendChild(el);el.select();const success=document.execCommand('copy');el.remove();button.focus();if(!success)throw new Error('copy');
      }
      if(label)label.textContent='已复制';else showStatus('接口地址已复制');
      clearTimeout(button._copyTimer);
      button._copyTimer=setTimeout(()=>{if(label)label.textContent='复制代码';},1500);
    }catch(_){showStatus('未能复制，请选中代码手动复制');}
  }
  document.querySelectorAll('[data-lang]').forEach(button=>button.addEventListener('click',()=>{
    language=button.dataset.lang;document.querySelectorAll('[data-lang]').forEach(el=>{el.setAttribute('aria-selected',String(el===button));el.tabIndex=el===button?0:-1;});updateExamples();
  }));
  document.querySelectorAll('[data-response]').forEach(button=>button.addEventListener('click',()=>{
    responseMode=button.dataset.response;document.querySelectorAll('[data-response]').forEach(el=>{el.setAttribute('aria-selected',String(el===button));el.tabIndex=el===button?0:-1;});updateExamples();
  }));
  document.getElementById('copyRequest').addEventListener('click',e=>copy(requestSource,e.currentTarget));
  document.getElementById('copyResponse').addEventListener('click',e=>copy(responseSource,e.currentTarget));
  document.getElementById('copyEndpoint').addEventListener('click',e=>copy(`http://127.0.0.1:${port}/authorize`,e.currentTarget));
  // Native buttons provide keyboard navigation for both tab groups.
  document.querySelectorAll('.reference-tabs').forEach(group=>group.addEventListener('keydown',e=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key)||e.target.getAttribute('role')!=='tab')return;
    const tabs=[...group.querySelectorAll('[role=tab]')],i=tabs.indexOf(e.target);let next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
    e.preventDefault();tabs[next].focus();tabs[next].click();
  }));
  updateExamples();
  // On smaller screens place code next to its first endpoint, rather than after the entire document.
  const reference=document.querySelector('.docs-reference'),shell=document.querySelector('.docs-shell');
  const compact=window.matchMedia('(max-width:1180px)');
  function placeReference(){
    if(compact.matches)document.querySelector('#authorize .next-endpoint').after(reference);
    else shell.appendChild(reference);
  }
  placeReference();compact.addEventListener('change',placeReference);
  document.querySelectorAll('.reference-tabs [role=tab]').forEach(el=>{el.tabIndex=el.getAttribute('aria-selected')==='true'?0:-1;});
  const stage=document.getElementById('flow'),steps=[...document.querySelectorAll('.flow-step')],dots=[...document.querySelectorAll('.flow-progress i')];
  const flowCopy=[
    '设备采集 CPU、主板、磁盘、内存与 IP，作为设备身份核验的输入。',
    '将硬件字段以 | 拼接，经 AES-ECB 加密与 Base64 编码后，上报 encrypted_info。',
    '服务端执行限流、白名单、解密、设备指纹状态与单设备日限校验。',
    '校验通过后以 DES-CBC 签发 auth_code；设备保存后可调用校验接口。'
  ];
  const play=document.getElementById('playFlow'),playLabel=document.getElementById('playFlowLabel');
  let timer=null,currentStep=0;
  function stopFlow(){clearTimeout(timer);timer=null;stage.classList.remove('is-playing');play.setAttribute('aria-pressed','false');playLabel.textContent=currentStep===3?'重新播放':'播放授权流程';play.querySelector('.play-disc').textContent='▶';}
  function selectStep(index){currentStep=index;steps.forEach((step,i)=>{step.classList.toggle('is-active',i===index);step.classList.toggle('is-done',i<index);const button=step.querySelector('button');if(i===index)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');});dots.forEach((dot,i)=>{dot.classList.toggle('is-active',i===index);dot.classList.toggle('is-done',i<index);});document.getElementById('flowCounter').textContent=`0${index+1} / 04`;document.getElementById('flowDetail').textContent=flowCopy[index];}
  steps.forEach((step,i)=>step.querySelector('button').addEventListener('click',()=>{stopFlow();selectStep(i);playLabel.textContent=i===3?'重新播放':'播放授权流程';}));
  play.addEventListener('click',()=>{
    if(timer){stopFlow();return;}
    selectStep(0);play.setAttribute('aria-pressed','true');playLabel.textContent='暂停演示';play.querySelector('.play-disc').textContent='Ⅱ';if(!motion.matches)stage.classList.add('is-playing');
    function advance(){timer=setTimeout(()=>{if(currentStep<3){selectStep(currentStep+1);advance();}else stopFlow();},800);}
    advance();
  });
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stopFlow();});
  motion.addEventListener('change',()=>{stopFlow();});
  // In-page navigation follows natural document scrolling, never pins or traps the wheel.
  const toc=[...document.querySelectorAll('#tocNav a')],local=[...document.querySelectorAll('.local-toc a')];
  let scrolling=false;
  function activate(id){toc.forEach(a=>{const active=a.hash===`#${id}`;a.classList.toggle('is-active',active);if(active)a.setAttribute('aria-current','location');else a.removeAttribute('aria-current');});}
  function syncNavigation(){
    scrolling=false;let id='authorize';
    for(const a of toc){const el=document.getElementById(a.hash.slice(1));if(el&&el.getBoundingClientRect().top<=130)id=el.id;}
    if(window.scrollY<24)id='authorize';activate(id);
    const next=id==='verify'?'verify':id==='authorize'||id==='overview'||id==='flow'?'authorize':endpoint;
    if(next!==endpoint){endpoint=next;updateExamples();}
    let current=local[0]?.hash;for(const a of local){const el=document.getElementById(a.hash.slice(1));if(el?.getBoundingClientRect().top<=160)current=a.hash;}local.forEach(a=>a.classList.toggle('is-active',a.hash===current));
  }
  window.addEventListener('scroll',()=>{if(!scrolling){scrolling=true;requestAnimationFrame(syncNavigation);}},{passive:true});
  function navigate(id){const el=document.getElementById(id);if(!el)return;window.scrollTo({top:Math.max(0,el.getBoundingClientRect().top+window.scrollY-80),behavior:motion.matches?'instant':'smooth'});if(id==='verify'||id==='authorize'){endpoint=id;updateExamples();}history.replaceState(null,'',`#${id}`);activate(id);setMenu(false);}
  document.querySelectorAll('a[href^="#"]').forEach(a=>a.addEventListener('click',e=>{if(a.hash.length<2)return;e.preventDefault();navigate(a.hash.slice(1));}));
  const menu=document.getElementById('docsMenu'),tocPanel=document.querySelector('.docs-toc');
  function setMenu(show){tocPanel.classList.toggle('is-open',show);menu.setAttribute('aria-expanded',String(show));tocPanel.inert=window.innerWidth<=800&&!show;}
  const mobile=window.matchMedia('(max-width:800px)');setMenu(false);mobile.addEventListener('change',()=>setMenu(false));
  menu.addEventListener('click',()=>setMenu(!tocPanel.classList.contains('is-open')));
  document.addEventListener('click',e=>{if(!tocPanel.contains(e.target)&&!menu.contains(e.target))setMenu(false);});
  const search=document.getElementById('docsSearch'),input=document.getElementById('docsSearchInput'),results=document.getElementById('docsSearchResults');
  const searchIndex=toc.map(a=>({id:a.hash.slice(1),title:a.textContent.replace('POST','').trim(),text:document.getElementById(a.hash.slice(1))?.textContent||''}));
  function searchDocs(){const q=input.value.trim().toLowerCase(),found=searchIndex.filter(x=>!q||`${x.title} ${x.text}`.toLowerCase().includes(q));results.replaceChildren();if(!found.length){results.innerHTML='<p class="search-empty">没有找到相关内容，请换一个关键词。</p>';return;}for(const result of found){const a=document.createElement('a');a.href=`#${result.id}`;const title=document.createElement('span');title.textContent=result.title;const small=document.createElement('small');small.textContent=`接口文档 / ${result.title}`;a.append(title,small);a.addEventListener('click',e=>{e.preventDefault();search.close();navigate(result.id);});results.appendChild(a);}}
  function openSearch(){search.showModal();input.value='';searchDocs();input.focus();}
  document.getElementById('openSearch').addEventListener('click',openSearch);input.addEventListener('input',searchDocs);
  input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();results.querySelector('a')?.click();}});
  document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();if(!search.open)openSearch();}if(e.key==='Escape'){setMenu(false);}});
  search.addEventListener('click',e=>{if(e.target===search){const r=search.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)search.close();}});
  if(location.hash){const id=location.hash.slice(1);if(document.getElementById(id))requestAnimationFrame(()=>navigate(id));}
})();

