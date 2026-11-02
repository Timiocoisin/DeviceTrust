/* Presentation-only helpers. Business requests remain in script.js. */
window.V3UI = (() => {
  const icon = name => `<svg class="icon" aria-hidden="true"><use href="/static/assets/icons.svg#${name}"/></svg>`;
  const openers = new Map();
  function open(id) {
    const el = document.getElementById(id);
    if (!el) return;
    openers.set(id, document.activeElement);
    el.classList.remove('hidden');
    document.body.classList.add('modal-open');
    requestAnimationFrame(() => el.querySelector('input:not([type=file]),button')?.focus());
  }
  function close(id) {
    document.getElementById(id)?.classList.add('hidden');
    if (!document.querySelector('.modal:not(.hidden)')) document.body.classList.remove('modal-open');
    openers.get(id)?.focus();
  }
  let confirmResolve;
  const finishConfirm = result => { close('confirmModal'); confirmResolve?.(result); confirmResolve = null; };
  function confirm(message, target = '', danger = false) {
    document.getElementById('confirmTitle').textContent = message;
    document.getElementById('confirmMessage').textContent = message.includes('删除') ? '该 CPU 将从白名单移除，历史授权记录保留。' : message.includes('封禁') && !message.includes('解封') ? '封禁后，该硬件指纹的授权请求将被拒绝。' : message.includes('禁用') ? '禁用后，该 CPU 的后续授权请求将被拒绝。' : message.includes('退出') ? '退出后需要重新登录才能访问管理控制台。' : '确认后更新当前业务线的设备状态。';
    document.getElementById('confirmTarget').textContent = target;
    document.getElementById('confirmProceed').classList.toggle('btn-danger', danger);
    open('confirmModal');
    return new Promise(resolve => { confirmResolve = resolve; });
  }
  document.querySelectorAll('[data-confirm-cancel]').forEach(el => el.addEventListener('click', () => finishConfirm(false)));
  document.getElementById('confirmProceed')?.addEventListener('click', () => finishConfirm(true));
  let aliasResolve;
  function editAlias(value) {
    document.getElementById('aliasInput').value = value;
    open('aliasModal');
    document.getElementById('aliasInput').focus();
    return new Promise(resolve => { aliasResolve = resolve; });
  }
  document.getElementById('aliasForm')?.addEventListener('submit', e => {
    e.preventDefault();
    aliasResolve?.(document.getElementById('aliasInput').value.trim());
    aliasResolve = null;
    close('aliasModal');
  });
  document.querySelectorAll('[data-dismiss]').forEach(el => el.addEventListener('click', () => {
    if (el.dataset.dismiss === 'aliasModal') { aliasResolve?.(null); aliasResolve = null; }
    close(el.dataset.dismiss);
  }));
  document.addEventListener('keydown', e => {
    const modal = [...document.querySelectorAll('.modal:not(.hidden)')].at(-1);
    if (!modal) return;
    if (e.key === 'Escape') {
      if (modal.id === 'confirmModal') finishConfirm(false);
      else if (modal.id === 'passwordModal') document.getElementById('cancelPassword')?.click();
      else if (modal.id === 'historyModal') document.getElementById('closeHistory')?.click();
      else { if (modal.id === 'aliasModal') { aliasResolve?.(null); aliasResolve = null; } close(modal.id); }
    }
    if (e.key === 'Tab') {
      const list = [...modal.querySelectorAll('button:not(:disabled),input:not([hidden]),select,a[href]')].filter(el => el.getClientRects().length);
      const first = list[0], last = list.at(-1);
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
    }
  });
  const workspace = document.getElementById('workspaceMenu'), trigger = document.getElementById('workspaceTrigger');
  const account = document.getElementById('accountMenu'), profile = document.getElementById('profileTrigger');
  function hideMenus() { workspace?.classList.add('hidden'); account?.classList.add('hidden'); trigger?.setAttribute('aria-expanded','false'); profile?.setAttribute('aria-expanded','false'); }
  trigger?.addEventListener('click', e => { e.stopPropagation(); const show = workspace.classList.contains('hidden'); hideMenus(); workspace.classList.toggle('hidden', !show); trigger.setAttribute('aria-expanded',String(show)); });
  profile?.addEventListener('click', e => { e.stopPropagation(); const show = account.classList.contains('hidden'); hideMenus(); account.classList.toggle('hidden', !show); profile.setAttribute('aria-expanded',String(show)); });
  document.addEventListener('click', hideMenus);
  document.addEventListener('keydown', e => { if(e.key === 'Escape') hideMenus(); });
  document.querySelectorAll('[data-account-action]').forEach(el => el.addEventListener('click', () => document.getElementById(el.dataset.accountAction === 'password' ? 'change-password-button' : 'logout-button').click()));
  document.getElementById('openAddCpu')?.addEventListener('click', () => open('addCpuModal'));
  document.getElementById('openImport')?.addEventListener('click', () => open('importModal'));
  document.getElementById('allLogs')?.addEventListener('click', () => document.querySelector('[data-tab=authlogs]').click());
  document.querySelectorAll('[data-reset]').forEach(button => button.addEventListener('click', () => {
    const prefix = button.dataset.reset;
    document.querySelectorAll(`[id^="${prefix}-"]`).forEach(el => { if (el.matches('input,select')) el.value = ''; });
    document.getElementById(`${prefix}-search`).click();
  }));
  document.querySelectorAll('.toolbar input').forEach(input => input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); input.closest('.toolbar')?.querySelector('[id$="-search"]')?.click(); } }));
  const upload = document.getElementById('uploadZone');
  upload?.addEventListener('dragover', e => { e.preventDefault(); upload.classList.add('is-dragging'); });
  upload?.addEventListener('dragleave', () => upload.classList.remove('is-dragging'));
  upload?.addEventListener('drop', e => {
    e.preventDefault(); upload.classList.remove('is-dragging');
    const files = e.dataTransfer.files;
    if (!files[0]?.name.toLowerCase().endsWith('.csv')) return;
    const input = document.getElementById('cpuImportFile'); input.files = files; input.dispatchEvent(new Event('change',{bubbles:true}));
  });
  // Render the SVG assets in the legacy controls without icon-font network dependencies.
  document.querySelectorAll('.view-btn').forEach(el => el.insertAdjacentHTML('afterbegin', icon(el.dataset.view === 'tree' ? 'tree' : 'list')));
  document.querySelectorAll('.modal__close').forEach(el => { el.innerHTML = icon('close'); el.setAttribute('aria-label','关闭'); });
  document.querySelectorAll('#passwordForm input[type=password]').forEach(input => {
    const wrap = document.createElement('span'); wrap.className='password-input'; input.replaceWith(wrap); wrap.appendChild(input);
    const toggle = document.createElement('button'); toggle.type='button'; toggle.setAttribute('aria-label','显示密码'); toggle.innerHTML=icon('eye-off');
    toggle.addEventListener('click', () => { const show=input.type==='password'; input.type=show?'text':'password'; toggle.innerHTML=icon(show?'eye':'eye-off'); toggle.setAttribute('aria-label',show?'隐藏密码':'显示密码'); });
    wrap.appendChild(toggle);
  });
  return {icon,open,close,confirm,editAlias};
})();
