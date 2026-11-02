(() => {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const page = document.querySelector('.docs-page');
  if (!page) return;

  const FLOW_COPY = [
    '设备采集 CPU / 主板 / 磁盘 / 内存 / IP，作为后续加密与指纹计算的原始输入。',
    '将硬件字段用 | 拼接，经 AES-ECB 加密并 Base64，得到 encrypted_info。',
    'POST /authorize，提交 cpu_id + encrypted_info；元一业务线需显式传 tenant=yuanyi。',
    '服务端依次执行限流、白名单、解密、指纹策略与日限额校验。',
    '校验通过后用 DES-CBC 签发 auth_code 并返回；设备本地持久化后可再调用校验接口。',
  ];

  const SAMPLE_AUTHORIZE = {
    auth_code: '<DES+Base64>',
    device_fingerprint: 'a1b2c3d4e5f67890',
    hardware_changed: false,
    is_new_device: true,
    tenant: 'lvshui',
  };

  const SAMPLE_VERIFY = {
    valid: true,
    payload: {
      cpu_id: 'BFEBFBFF000B0671',
      motherboard_id: '...',
      disk_id: '...',
      memory_id: '...',
      issued_at: '2026-11-02 15:00:00',
    },
  };

  function pretty(obj) {
    return JSON.stringify(obj, null, 2);
  }

  function smoothTo(el) {
    if (!el) return;
    const top = el.getBoundingClientRect().top + window.scrollY - 76;
    window.scrollTo({ top, behavior: reduceMotion ? 'auto' : 'smooth' });
  }

  // ── Code tabs ──
  document.querySelectorAll('.code-tabs').forEach((tabs) => {
    const buttons = Array.from(tabs.querySelectorAll('[role="tab"]'));
    const panes = Array.from(tabs.querySelectorAll('.code-tabs__pane'));
    buttons.forEach((btn) => {
      btn.addEventListener('click', () => {
        const pane = btn.dataset.pane;
        buttons.forEach((b) => b.setAttribute('aria-selected', b === btn ? 'true' : 'false'));
        panes.forEach((p) => {
          const on = p.dataset.pane === pane;
          p.classList.toggle('is-active', on);
          if (on && p.dataset.pane === 'res') {
            const code = p.querySelector('.docs-code');
            if (code && !reduceMotion) {
              code.classList.remove('is-enter');
              void code.offsetWidth;
              code.classList.add('is-enter');
            }
          }
        });
      });
    });
  });

  // ── Copy with ripple ──
  function attachCopy(root) {
    const btn = root.querySelector(':scope > .code-frame__bar .copy-btn, :scope > .copy-btn');
    const codeEl = root.querySelector('pre.docs-code code, pre.docs-code');
    if (!btn || !codeEl || btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', async (e) => {
      const text = (codeEl.innerText || '').trim();
      const rect = btn.getBoundingClientRect();
      const ripple = document.createElement('span');
      ripple.className = 'ripple';
      const size = Math.max(rect.width, rect.height) * 1.4;
      ripple.style.width = ripple.style.height = `${size}px`;
      ripple.style.left = `${e.clientX - rect.left - size / 2}px`;
      ripple.style.top = `${e.clientY - rect.top - size / 2}px`;
      btn.appendChild(ripple);
      window.setTimeout(() => ripple.remove(), 320);
      try {
        await navigator.clipboard.writeText(text);
        btn.textContent = '已复制';
      } catch (_) {
        btn.textContent = '失败';
      }
      window.setTimeout(() => { btn.textContent = '复制'; }, 1100);
    });
  }

  document.querySelectorAll('[data-copy]').forEach(attachCopy);

  // ── Try-it panels ──
  function buildAuthorizeBody(form) {
    const fd = new FormData(form);
    const body = {
      cpu_id: String(fd.get('cpu_id') || '').trim(),
      encrypted_info: String(fd.get('encrypted_info') || '').trim(),
    };
    const tenant = String(fd.get('tenant') || '').trim();
    if (tenant) body.tenant = tenant;
    return body;
  }

  function setResponse(pre, label, payload, ok) {
    if (label) {
      label.textContent = ok ? '200 · JSON' : `${payload.status || 'ERR'} · JSON`;
      label.style.color = ok ? '#7ddea8' : '#f0a0ab';
    }
    const code = pre.querySelector('code') || pre;
    code.textContent = typeof payload === 'string' ? payload : pretty(payload);
    if (!reduceMotion) {
      pre.classList.remove('is-enter');
      void pre.offsetWidth;
      pre.classList.add('is-enter');
    }
  }

  function switchTab(tabsRoot, pane) {
    const btn = tabsRoot.querySelector(`[role="tab"][data-pane="${pane}"]`);
    btn?.click();
  }

  const authorizeForm = document.getElementById('authorizeForm');
  const authorizeReq = document.getElementById('authorizeReqPreview');
  const authorizeRes = document.getElementById('authorizeResPreview');
  const authorizeResLabel = document.getElementById('authorizeResLabel');

  function refreshAuthorizePreview() {
    if (!authorizeForm || !authorizeReq) return;
    const body = buildAuthorizeBody(authorizeForm);
    const code = authorizeReq.querySelector('code') || authorizeReq;
    code.textContent = pretty(body);
  }

  authorizeForm?.addEventListener('input', refreshAuthorizePreview);
  authorizeForm?.addEventListener('change', refreshAuthorizePreview);
  refreshAuthorizePreview();

  authorizeForm?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = buildAuthorizeBody(authorizeForm);
    const tabs = authorizeForm.closest('.try-panel')?.querySelector('.code-tabs');
    switchTab(tabs, 'res');
    setResponse(authorizeRes, authorizeResLabel, '// 请求中…', true);
    try {
      const res = await fetch('/authorize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      let data;
      try { data = await res.json(); }
      catch (_) { data = { error: '响应不是 JSON' }; }
      setResponse(authorizeRes, authorizeResLabel, { status: res.status, ...data }, res.ok);
    } catch (err) {
      setResponse(authorizeRes, authorizeResLabel, { error: String(err.message || err) }, false);
    }
  });

  document.querySelector('[data-preview="authorize"]')?.addEventListener('click', () => {
    const tabs = authorizeForm?.closest('.try-panel')?.querySelector('.code-tabs');
    switchTab(tabs, 'res');
    window.setTimeout(() => {
      setResponse(authorizeRes, authorizeResLabel, SAMPLE_AUTHORIZE, true);
    }, reduceMotion ? 0 : 160);
  });

  const verifyForm = document.getElementById('verifyForm');
  const verifyReq = document.getElementById('verifyReqPreview');
  const verifyRes = document.getElementById('verifyResPreview');
  const verifyResLabel = document.getElementById('verifyResLabel');

  function refreshVerifyPreview() {
    if (!verifyForm || !verifyReq) return;
    const fd = new FormData(verifyForm);
    const body = { auth_code: String(fd.get('auth_code') || '').trim() };
    const code = verifyReq.querySelector('code') || verifyReq;
    code.textContent = pretty(body);
  }

  verifyForm?.addEventListener('input', refreshVerifyPreview);
  refreshVerifyPreview();

  verifyForm?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(verifyForm);
    const body = { auth_code: String(fd.get('auth_code') || '').trim() };
    const tabs = verifyForm.closest('.try-panel')?.querySelector('.code-tabs');
    switchTab(tabs, 'res');
    setResponse(verifyRes, verifyResLabel, '// 请求中…', true);
    try {
      const res = await fetch('/verify_auth_code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      let data;
      try { data = await res.json(); }
      catch (_) { data = { error: '响应不是 JSON' }; }
      setResponse(verifyRes, verifyResLabel, { status: res.status, ...data }, res.ok);
    } catch (err) {
      setResponse(verifyRes, verifyResLabel, { error: String(err.message || err) }, false);
    }
  });

  document.querySelector('[data-preview="verify"]')?.addEventListener('click', () => {
    const tabs = verifyForm?.closest('.try-panel')?.querySelector('.code-tabs');
    switchTab(tabs, 'res');
    window.setTimeout(() => {
      setResponse(verifyRes, verifyResLabel, SAMPLE_VERIFY, true);
    }, reduceMotion ? 0 : 160);
  });
})();
