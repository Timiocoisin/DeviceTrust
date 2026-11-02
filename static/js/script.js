/* 设备授权管理平台 · 控制台脚本 */
(function () {
  'use strict';

  // Sidebar mode is a visible switch; collapsed/expanded is a separate action.
  const sidebar = document.querySelector('.sidebar');
  const sidebarCloseBtn = document.querySelector('#sidebar-close');
  const sidebarLockBtn = document.querySelector('#lock-icon');
  function syncSidebarState() {
    const fixed = sidebar.classList.contains('locked');
    const collapsed = sidebar.classList.contains('close');
    sidebar.classList.toggle('hoverable',!fixed);
    sidebarLockBtn.setAttribute('aria-checked',String(fixed));
    sidebarLockBtn.setAttribute('title',fixed ? '已固定，点击切换为悬停展开' : '悬停展开，点击固定侧栏');
    document.getElementById('sidebarModeStatus').textContent = fixed ? '已固定 · 保持展开' : '未固定 · 悬停展开';
    sidebarCloseBtn.setAttribute('aria-label',collapsed ? '展开侧栏' : '收起侧栏');
    sidebarCloseBtn.setAttribute('title',collapsed ? '展开侧栏' : '收起侧栏');
    sidebarCloseBtn.setAttribute('aria-expanded',String(!collapsed));
  }
  sidebarLockBtn?.addEventListener('click',() => {
    const fixed = !sidebar.classList.contains('locked');
    sidebar.classList.toggle('locked',fixed);
    if (fixed) sidebar.classList.remove('close');
    syncSidebarState();
  });
  sidebarCloseBtn?.addEventListener('click',() => {
    sidebar.classList.toggle('close');
    syncSidebarState();
  });
  sidebar?.addEventListener('mouseleave',() => {
    if (!sidebar.classList.contains('locked')) { sidebar.classList.add('close'); syncSidebarState(); }
  });
  sidebar?.addEventListener('mouseenter',() => {
    if (!sidebar.classList.contains('locked')) { sidebar.classList.remove('close'); syncSidebarState(); }
  });
  if (window.innerWidth < 500) { sidebar.classList.add('close'); sidebar.classList.remove('locked'); }
  syncSidebarState();

  async function renderListRequest(task, selector, columns, retry) {
    const el = document.querySelector(selector);
    if (el && (!el.children.length || el.querySelector('[data-load-error]'))) el.innerHTML = `<tr><td colspan="${columns}" class="empty-cell"><div class="list-loading">正在加载当前页…</div></td></tr>`;
    try { await task(); }
    catch(error) {
      if (error.message === '未登录' || error.message === '业务线已切换') return;
      if (el) {
        el.innerHTML = `<tr><td colspan="${columns}" class="empty-cell"><div class="list-error" data-load-error>${V3UI.icon('error')}<strong>暂时无法获取数据</strong><span>查询条件已保留，请重试或稍后返回。</span><button class="btn-sm" type="button">重新加载</button></div></td></tr>`;
        el.querySelector('button').onclick = retry;
      }
    }
  }
  async function loadInfo() { return renderListRequest(loadInfoData, '#infoTable tbody', 8, loadInfo); }
  async function loadAuthLogs() { return renderListRequest(loadAuthLogsData, '#authLogTable tbody', 9, loadAuthLogs); }
  async function loadCpuIds() { return renderListRequest(loadCpuIdsData, '#cpuTable tbody', 8, loadCpuIds); }
  async function loadDeviceStatus() { return renderListRequest(loadDeviceStatusData, '#deviceTable tbody', 8, loadDeviceStatus); }
  async function loadCpuHistory() { return renderListRequest(loadCpuHistoryData, '#historyBody', 7, loadCpuHistory); }

  // ── 工具函数 ──
  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s ?? '';
    return d.innerHTML;
  }

  function showToast(msg) {
    const el = document.getElementById('toast');
    if (!el) return;
    clearTimeout(el._hideTimer);
    el.innerHTML = V3UI.icon('info') + `<span>${esc(msg)}</span>`;
    el.classList.remove('hidden');
    el._hideTimer = setTimeout(() => el.classList.add('hidden'), 3500);
  }

  const TENANT_META = {
    lvshui: { code: 'lvshui', name: '铝水', tag: '铝水工作空间' },
    yuanyi: { code: 'yuanyi', name: '元一', tag: '元一工作空间' },
  };
  const TENANT_KEY = 'auth_console_tenant';
  let currentTenant = localStorage.getItem(TENANT_KEY) || 'lvshui';
  if (!TENANT_META[currentTenant]) currentTenant = 'lvshui';

  function withTenant(url) {
    const sep = url.includes('?') ? '&' : '?';
    return `${url}${sep}tenant=${encodeURIComponent(currentTenant)}`;
  }

  async function api(url, opts = {}) {
    const requestedTenant = currentTenant;
    const headers = new Headers(opts.headers || {});
    headers.set('X-Tenant', currentTenant);
    const res = await fetch(withTenant(url), { ...opts, headers });
    if (res.status === 401) {
      window.location.href = '/';
      throw new Error('未登录');
    }
    const readJson = res.json.bind(res);
    res.json = async () => {
      const data = await readJson();
      if (requestedTenant !== currentTenant) throw new Error('业务线已切换');
      return data;
    };
    return res;
  }

  async function mutate(url, options) {
    try {
      const response = await api(url, options);
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        showToast(body.message || '操作失败，请重试');
        return null;
      }
      return response;
    } catch(error) {
      if (error.message !== '未登录' && error.message !== '业务线已切换') showToast('网络异常，请稍后重试');
      return null;
    }
  }

  function syncTenantUI() {
    const meta = TENANT_META[currentTenant] || TENANT_META.lvshui;
    document.querySelectorAll('.tenant-switch__btn').forEach((btn) => {
      const active = btn.dataset.tenant === currentTenant;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-checked', active ? 'true' : 'false');
    });
    const tag = document.getElementById('tenantTag');
    if (tag) tag.textContent = meta.tag;
    const badge = document.getElementById('opsTenantBadge');
    if (badge) badge.textContent = meta.name + '业务线';
    document.title = `${meta.name} · 设备授权管理平台`;
    document.querySelectorAll('[data-current-tenant]').forEach(el => el.textContent = meta.name);
  }

  function reloadActiveTenantData() {
    feedMaxId = 0;
    knownFingerprints.clear();
    V3UI.close('historyModal');
    const feed = document.getElementById('authFeed');
    if (feed) feed.innerHTML = '';
    const lastAuth = document.getElementById('opsLastAuth');
    if (lastAuth) lastAuth.textContent = '暂无';
    const updates = [refreshKpis(),
    loadTrendChart(document.querySelector('.chart-tab.is-active')?.dataset.days || 7),
    loadFailReasonChart(),
    loadPolicyPanel(),
    loadAuthFeed()];
    if (activeTab === 'records') { document.querySelector('#infoTable tbody').replaceChildren(); updates.push(loadInfo()); }
    if (activeTab === 'authlogs') { document.querySelector('#authLogTable tbody').replaceChildren(); updates.push(loadAuthLogs()); }
    if (activeTab === 'whitelist') { document.querySelector('#cpuTable tbody').replaceChildren(); updates.push(loadCpuIds()); }
    if (activeTab === 'devices') { document.querySelector('#deviceTable tbody').replaceChildren(); document.getElementById('deviceTree').replaceChildren(); updates.push(loadDeviceStatus()); }
    return Promise.allSettled(updates);
  }

  document.querySelectorAll('.tenant-switch__btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const next = btn.dataset.tenant;
      if (!TENANT_META[next] || next === currentTenant) return;
      currentTenant = next;
      localStorage.setItem(TENANT_KEY, currentTenant);
      syncTenantUI();
      showToast(`已切换到${TENANT_META[currentTenant].name}`);
      reloadActiveTenantData();
      sendLiveSubscribe();
    });
  });
  syncTenantUI();

  // ── Tab 切换 ──
  let activeTab = 'overview';
  const tabLinks = document.querySelectorAll('[data-tab]');
  const tabMeta = {
    overview: { title: '数据概览', desc: '集中查看设备授权状态与接入记录' },
    records: { title: '授权记录', desc: '查看授权成功的终端硬件信息，追溯每一次设备接入' },
    authlogs: { title: '授权日志', desc: '查询全部授权请求，追踪结果与硬件变化' },
    whitelist: { title: 'CPU 白名单', desc: '管理允许接入的 CPU 身份、状态与授权有效期' },
    devices: { title: '设备管理', desc: '基于硬件指纹管理物理设备，检查状态与授权历史' },
  };

  function setPageHeader(tab) {
    const meta = tabMeta[tab] || tabMeta.overview;
    const titleEl = document.getElementById('pageTitle');
    const descEl = document.getElementById('pageDesc');
    if (titleEl) titleEl.textContent = meta.title;
    if (descEl) descEl.textContent = meta.desc;
    document.getElementById('breadcrumbTitle').textContent = meta.title;
    document.querySelectorAll('[data-page-action]').forEach(el => el.classList.toggle('hidden', el.dataset.pageAction !== tab));
  }

  tabLinks.forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      const tab = link.dataset.tab;
      activeTab = tab;
      setPageHeader(tab);
      tabLinks.forEach(l => l.classList.toggle('is-active', l.dataset.tab === tab));
      document.querySelectorAll('.tab-panel').forEach(p => {
        p.classList.toggle('hidden', p.id !== `tab-${tab}`);
      });
      if (tab === 'devices') loadDeviceStatus();
      if (tab === 'authlogs') loadAuthLogs();
      if (tab === 'records') loadInfo();
      if (tab === 'whitelist') loadCpuIds();
  });
});

  // ── 时钟 ──
  function updateTime() {
    const now = new Date();
    const t = [now.getHours(), now.getMinutes(), now.getSeconds()]
      .map(n => String(n).padStart(2, '0')).join(':');
    const el = document.querySelector('.current-time');
    if (el) el.textContent = t;
    const lastAuth = document.getElementById('opsLastAuth');
    if (lastAuth?.dataset.ts) {
      lastAuth.textContent = formatRelativeTime(lastAuth.dataset.ts);
    }
  }
  updateTime();
  setInterval(updateTime, 1000);

  // ── KPI / 值班台信号 ──
  function formatRelativeTime(dateTimeStr) {
    if (!dateTimeStr) return '暂无';
    const ts = Date.parse(String(dateTimeStr).replace(' ', 'T'));
    if (Number.isNaN(ts)) return dateTimeStr;
    const diff = Math.max(0, Math.floor((Date.now() - ts) / 1000));
    if (diff < 10) return '刚刚';
    if (diff < 60) return `${diff} 秒前`;
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
    return `${Math.floor(diff / 86400)} 天前`;
  }

  async function refreshKpis() {
    try {
      const [cpu, auth, today, failed, unknown, newDev] = await Promise.all([
        api('/api/cpu_count').then(r => r.json()),
        api('/api/authorized_count').then(r => r.json()),
        api('/api/today_authorized_count').then(r => r.json()),
        api('/api/today_failed_count').then(r => r.json()),
        api('/api/unknown_devices_count').then(r => r.json()),
        api('/api/today_new_devices_count').then(r => r.json()),
      ]);
      const count = value => Number(value || 0).toLocaleString('en-US');
      document.querySelector('.cpu-count').textContent = count(cpu.cpu_count);
      document.querySelector('.authorized-count').textContent = count(auth.authorized_count);
      document.querySelector('.today-authorized-count').textContent = count(today.today_authorized_count);
      document.querySelector('.today-failed-count').textContent = count(failed.today_failed_count);
      const newEl = document.querySelector('.today-new-devices-count');
      if (newEl) newEl.textContent = newDev.today_new_devices_count;
      const unk = document.querySelector('.unknown-devices-count');
      if (unk) unk.textContent = `未知设备 ${unknown.unknown_devices_count} 台`;

      const ok = Number(today.today_authorized_count) || 0;
      const fail = Number(failed.today_failed_count) || 0;
      const total = ok + fail;
      const rateEl = document.getElementById('opsSuccessRate');
      if (rateEl) {
        rateEl.textContent = total === 0 ? '—' : `${((ok / total) * 100).toFixed(1)}%`;
      }
    } catch (_) { /* ignore */ }
  }
  refreshKpis();
  // KPI 以 WebSocket 增量更新为主，30 秒校正一次
  setInterval(refreshKpis, 30000);

  async function loadTrendChart(days) {
    try { await loadTrendChartData(days); }
    catch(error) { if (error.message !== '业务线已切换' && error.message !== '未登录') showToast('图表暂时无法加载，请稍后刷新'); }
  }

  async function loadFailReasonChart() {
    try { await loadFailReasonChartData(); }
    catch(error) { if (error.message !== '业务线已切换' && error.message !== '未登录') showToast('图表暂时无法加载，请稍后刷新'); }
  }

  // ── 图表 ──
  let trendChart, failReasonChart;
  let trendDays = 7;

  const chartGrid = '#EAF0F7';
  const chartFont = { family: "'Microsoft YaHei', sans-serif", size: 11 };
  const chartScales = {
    x: { grid: { display: false }, ticks: { font: chartFont, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
    y: { beginAtZero: true, grid: { color: chartGrid, drawBorder: false }, ticks: { maxTicksLimit: 4, font: chartFont, padding: 8 }, border: { display: false } },
  };
  function displayDate(value) {
    if (!value) return '—';
    const [date,time] = String(value).split(' ');
    return `<span class="date-cell">${esc(date)}${time ? `<span>${esc(time)}</span>` : ''}</span>`;
  }
  function shortFp(fp) {
    if (!fp) return '—';
    return fp.length > 8 ? `${fp.slice(0, 8)}…` : fp;
  }

  function feedItemHtml(item, isNew) {
    const ok = item.result === 'success';
    const fp = item.device_fingerprint;
    const newBadge = (isNew || item.is_new_device)
      ? '<span class="feed-badge feed-badge--new">新设备</span>' : '';
    const hwBadge = item.hardware_changed
      ? '<span class="feed-badge feed-badge--warn">硬件变更</span>' : '';
    return `<div class="feed-item feed-item--${ok ? 'success' : 'fail'}">
      <time class="feed-item__time" title="${esc(item.DateTime)}">${esc((item.DateTime || '').split(' ')[1] || item.DateTime)}</time>
      <code class="feed-item__cpu" title="${esc(fp || '')}">${esc(item.CPU_ID || '—')}</code>
      <code>${esc(item.IP || '—')}</code>
      <span><span class="feed-badge feed-badge--${ok ? 'ok' : 'fail'}">${ok ? '成功' : '失败'}</span></span>
      <span class="feed-item__marks">${newBadge}${hwBadge}${!newBadge && !hwBadge ? '<span class="badge badge--neutral">既有设备</span>' : ''}</span>
      <p class="feed-item__reason" title="${esc(item.reason || '')}">${esc(item.reason || '—')}</p>
    </div>`;
  }

  // Shared HTML tooltip keeps chart feedback aligned with the console surfaces.
  function renderChartTooltip({chart, tooltip}) {
    const id = `${chart.canvas.id}-tooltip`;
    let card = document.getElementById(id);
    if (!card) {
      card = document.createElement('div'); card.id = id;
      card.className = 'chart-tooltip'; card.setAttribute('role','tooltip');
      document.body.appendChild(card);
    }
    if (!tooltip.opacity) { card.style.opacity='0'; card.setAttribute('aria-hidden','true'); return; }
    const title = (tooltip.title || []).join(' ');
    card.innerHTML = `<div class="chart-tooltip__date">${esc(title)}</div><div class="chart-tooltip__rows">${(tooltip.dataPoints || []).map(point => {
      const colors = point.dataset.borderColor;
      const fills = point.dataset.backgroundColor;
      const color = chart.config.type === 'doughnut' ? (Array.isArray(fills) ? fills[point.dataIndex] : '#4768EF') : (typeof colors === 'string' ? colors : '#4768EF');
      return `<div class="chart-tooltip__row"><span class="chart-tooltip__label"><i style="background:${esc(color)}"></i>${esc(point.dataset.label || '失败次数')}</span><span><strong>${esc(Number(point.raw).toLocaleString('en-US'))}</strong><small>次</small></span></div>`;
    }).join('')}</div>`;
    const bounds = chart.canvas.getBoundingClientRect(), box=card.getBoundingClientRect();
    let x=bounds.left+tooltip.caretX+16;
    if (x+box.width>window.innerWidth-12) x=bounds.left+tooltip.caretX-box.width-16;
    card.style.left=`${Math.max(12,Math.min(x,window.innerWidth-box.width-12))}px`;
    card.style.top=`${Math.max(12,Math.min(bounds.top+tooltip.caretY-box.height/2,window.innerHeight-box.height-12))}px`;
    card.style.opacity='1'; card.setAttribute('aria-hidden','false');
  }
  document.addEventListener('scroll',() => document.querySelectorAll('.chart-tooltip').forEach(el => {el.style.opacity='0';el.setAttribute('aria-hidden','true');}),true);

  async function loadTrendChartData(days) {
    trendDays = days;
    const data = await api(`/api/auth_trends?days=${days}`).then(r => r.json());
    const values = [...data.success, ...data.fail].map(Number);
    const peak = Math.max(1,...values);
    const magnitude = 10 ** Math.floor(Math.log10(peak));
    const step = Math.ceil(peak / 3 / magnitude * 10) * magnitude / 10;
    const mini = document.getElementById('successSparkline');
    if (mini) {
      const points = data.success.slice(-7).map(Number),low=Math.min(...points),high=Math.max(...points);
      mini.querySelector('path').setAttribute('d',points.map((n,i)=>`${i?'L':'M'}${i * 150 / Math.max(1,points.length-1)},${42 - (n-low) / Math.max(1,high-low)*38}`).join(' '));
    }
    const ctx = document.getElementById('trendChart');
    if (!ctx) return;
    if (trendChart) { document.getElementById('trendChart-tooltip')?.remove(); trendChart.destroy(); }
    trendChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: data.labels,
        datasets: [
          {
            label: '成功', data: data.success, tension: 0, fill: true,
            borderColor: '#4768EF', borderWidth: 2.5, pointRadius: 3, pointHoverRadius: 5,
            backgroundColor: (c) => {
              const { chart } = c;
              const g = chart.ctx.createLinearGradient(0, 0, 0, chart.height);
              g.addColorStop(0, 'rgba(71,104,239,0.18)');
              g.addColorStop(1, 'rgba(71,104,239,0)');
              return g;
            },
          },
          {
            label: '失败', data: data.fail, tension: 0, fill: true,
            borderColor: '#DF6676', borderWidth: 2.5, pointRadius: 3, pointHoverRadius: 5,
            backgroundColor: (c) => {
              const { chart } = c;
              const g = chart.ctx.createLinearGradient(0, 0, 0, chart.height);
              g.addColorStop(0, 'rgba(201,74,74,0.18)');
              g.addColorStop(1, 'rgba(201,74,74,0)');
              return g;
            },
          },
        ],
      },
      options: {
        responsive: true,
        animation: false,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          tooltip: {enabled:false,external:renderChartTooltip},
          legend: {
            display: false,
            labels: { usePointStyle: true, pointStyle: 'circle', padding: 16, font: chartFont },
          },
        },
        scales: {...chartScales,y:{...chartScales.y,max:Math.ceil(peak/step)*step,ticks:{...chartScales.y.ticks,stepSize:step}}},
      },
    });
  }

  async function loadFailReasonChartData() {
    const data = await api('/api/auth_fail_reasons?days=7').then(r => r.json());
    const ctx = document.getElementById('failReasonChart');
    if (!ctx) return;
    if (failReasonChart) { document.getElementById('failReasonChart-tooltip')?.remove(); failReasonChart.destroy(); }
    const colors = ['#4768EF','#A295E7','#F1BF86','#C7D7F6'];
    document.getElementById('failureTotal').textContent = data.data.reduce((a,b) => a + Number(b),0);
    document.getElementById('failureLegend').innerHTML = data.labels.map((label,i) => `<div><i style="background:${colors[i % colors.length]}"></i><span>${esc(label)}</span><strong>${esc(data.data[i])}</strong></div>`).join('');
    failReasonChart = new Chart(ctx, {
      type:'doughnut', data:{labels:data.labels,datasets:[{data:data.data,backgroundColor:colors,borderColor:'#fff',borderWidth:3,hoverOffset:3}]},
      options:{responsive:true,maintainAspectRatio:false,cutout:'73%',animation:false,plugins:{legend:{display:false},tooltip:{enabled:false,external:renderChartTooltip}}}
    });
  }

  async function loadPolicyPanel() {
    const el = document.getElementById('policyTags');
    if (!el) return;
    el.innerHTML = '<p class="policy-loading">正在加载策略…</p>';
    el.setAttribute('aria-busy','true');
    try {
      const response = await api('/api/policy');
      if (!response.ok) throw new Error('策略加载失败');
      const p = await response.json();
      const windowLabel = p.rate_limit_window_seconds === 60 ? '分钟' : `${p.rate_limit_window_seconds} 秒`;
      const rows = [
        ['单设备日授权上限', p.daily_auth_limit_per_device ? `${p.daily_auth_limit_per_device} 次 / 天` : '不限'],
        ['IP 请求限流',`${p.rate_limit_max_per_ip} 次 / ${windowLabel}`],
        ['CPU 请求限流',`${p.rate_limit_max_per_cpu} 次 / ${windowLabel}`],
        ['授权码有效期',`${Number((p.auth_code_max_age_seconds / 3600).toFixed(2))} 小时`],
        ['授权日志保留',`${p.log_retention_days} 天`],
      ];
      el.innerHTML = `<dl class="policy-details">${rows.map(([name,value]) => `<div><dt>${esc(name)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`;
    } catch(error) {
      if (error.message === '未登录' || error.message === '业务线已切换') return;
      el.innerHTML = '<div class="policy-error"><p>暂时无法获取授权策略</p><button type="button" class="btn-sm btn-outline">重新加载</button></div>';
      el.querySelector('button').onclick = loadPolicyPanel;
    } finally { el.setAttribute('aria-busy','false'); }
  }
  document.getElementById('viewPolicy')?.addEventListener('click',() => {
    V3UI.open('policyModal'); loadPolicyPanel();
  });

  document.querySelectorAll('.chart-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.chart-tab').forEach(b => b.classList.remove('is-active'));
      btn.classList.add('is-active');
      loadTrendChart(parseInt(btn.dataset.days, 10));
  });
});

  if (typeof Chart !== 'undefined') {
    loadTrendChart(7);
    loadFailReasonChart();
    loadPolicyPanel();
    setInterval(() => {
      loadTrendChart(trendDays);
      loadFailReasonChart();
    }, 30000);
  }

  // ── 实时授权动态 ──
  let feedMaxId = 0;
  const knownFingerprints = new Set();

  async function loadAuthFeed() {
    const url = feedMaxId ? `/api/auth_feed?since_id=${feedMaxId}` : '/api/auth_feed';
    try {
      const data = await api(url).then(r => r.json());
      const feed = document.getElementById('authFeed');
      if (!feed) return;

      const newestItem = data.feed?.[0] || null;

      if (!feedMaxId) {
        feed.innerHTML = data.feed.map(item => {
          if (item.device_fingerprint) knownFingerprints.add(item.device_fingerprint);
          return feedItemHtml(item, false);
        }).join('') || `<div class="feed-empty">${V3UI.icon('empty')}暂无授权动态</div>`;
      } else if (data.feed.length) {
        data.feed.reverse().forEach(item => {
          const fp = item.device_fingerprint;
          const isNew = item.is_new_device || (fp && !knownFingerprints.has(fp));
          if (fp) knownFingerprints.add(fp);
          if (isNew && item.result === 'success') {
            showToast(`新设备首次授权：${item.CPU_ID}（${shortFp(fp)}）`);
          }
          const el = document.createElement('div');
          el.innerHTML = feedItemHtml(item, isNew);
          feed.prepend(el.firstElementChild);
        });
      }
      if (data.max_id) feedMaxId = data.max_id;

      const lastAuth = document.getElementById('opsLastAuth');
      if (lastAuth) {
        if (newestItem?.DateTime) {
          lastAuth.textContent = formatRelativeTime(newestItem.DateTime);
          lastAuth.dataset.ts = newestItem.DateTime;
        } else if (!feed.querySelector('.feed-item')) {
          lastAuth.textContent = '暂无';
          delete lastAuth.dataset.ts;
        }
      }
    } catch (_) { /* ignore */ }
  }
  function setLiveStatus(state, text) {
    const el = document.getElementById('liveStatus');
    if (!el) return;
    el.dataset.state = state;
    el.textContent = text;
  }

  function bumpKpisFromAuth(item) {
    const readCount = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return 0;
      return Number(String(el.textContent).replace(/,/g, '')) || 0;
    };
    const writeCount = (sel, n) => {
      const el = document.querySelector(sel);
      if (el) el.textContent = Number(n).toLocaleString('en-US');
    };
    if (item.result === 'success') {
      writeCount('.today-authorized-count', readCount('.today-authorized-count') + 1);
      writeCount('.authorized-count', readCount('.authorized-count') + 1);
      if (item.is_new_device) {
        writeCount('.today-new-devices-count', readCount('.today-new-devices-count') + 1);
      }
    } else {
      writeCount('.today-failed-count', readCount('.today-failed-count') + 1);
    }
    const ok = readCount('.today-authorized-count');
    const fail = readCount('.today-failed-count');
    const total = ok + fail;
    const rateEl = document.getElementById('opsSuccessRate');
    if (rateEl) rateEl.textContent = total === 0 ? '—' : `${((ok / total) * 100).toFixed(1)}%`;
  }

  function applyAuthLiveItem(item) {
    if (!item) return;
    if (item.tenant && item.tenant !== currentTenant) return;
    const feed = document.getElementById('authFeed');
    if (!feed) return;

    const id = Number(item.id || 0);
    if (id && id <= feedMaxId) return;
    if (id) feedMaxId = Math.max(feedMaxId, id);

    feed.querySelector('.feed-empty')?.remove();
    const fp = item.device_fingerprint;
    const isNew = !!(item.is_new_device || (fp && !knownFingerprints.has(fp)));
    if (fp) knownFingerprints.add(fp);
    if (isNew && item.result === 'success') {
      showToast(`新设备首次授权：${item.CPU_ID}（${shortFp(fp)}）`);
    }
    const wrap = document.createElement('div');
    wrap.innerHTML = feedItemHtml(item, isNew);
    if (wrap.firstElementChild) feed.prepend(wrap.firstElementChild);
    while (feed.querySelectorAll('.feed-item').length > 40) {
      feed.lastElementChild?.remove();
    }

    const lastAuth = document.getElementById('opsLastAuth');
    if (lastAuth && item.DateTime) {
      lastAuth.textContent = formatRelativeTime(item.DateTime);
      lastAuth.dataset.ts = item.DateTime;
    }
    bumpKpisFromAuth(item);
  }

  let liveSocket = null;
  let liveReconnectTimer = null;
  let livePingTimer = null;
  let feedFallbackTimer = null;

  function startFeedFallback() {
    if (feedFallbackTimer) return;
    feedFallbackTimer = setInterval(() => {
      loadAuthFeed();
      refreshKpis();
    }, 5000);
  }

  function stopFeedFallback() {
    if (!feedFallbackTimer) return;
    clearInterval(feedFallbackTimer);
    feedFallbackTimer = null;
  }

  function sendLiveSubscribe() {
    if (liveSocket?.readyState === WebSocket.OPEN) {
      liveSocket.send(JSON.stringify({ type: 'subscribe', tenant: currentTenant }));
    }
  }

  function connectLive() {
    if (liveReconnectTimer) {
      clearTimeout(liveReconnectTimer);
      liveReconnectTimer = null;
    }
    if (liveSocket && (liveSocket.readyState === WebSocket.OPEN || liveSocket.readyState === WebSocket.CONNECTING)) {
      sendLiveSubscribe();
      return;
    }
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    setLiveStatus('connecting', '实时连接中…');
    try {
      liveSocket = new WebSocket(`${proto}//${location.host}/ws/live`);
    } catch (_) {
      setLiveStatus('offline', '实时断开 · 回退轮询');
      startFeedFallback();
      liveReconnectTimer = setTimeout(connectLive, 3000);
      return;
    }
    liveSocket.addEventListener('open', () => {
      setLiveStatus('online', '实时推送');
      stopFeedFallback();
      sendLiveSubscribe();
      if (livePingTimer) clearInterval(livePingTimer);
      livePingTimer = setInterval(() => {
        if (liveSocket?.readyState === WebSocket.OPEN) {
          liveSocket.send(JSON.stringify({ type: 'ping' }));
        }
      }, 25000);
    });
    liveSocket.addEventListener('message', (ev) => {
      let data;
      try { data = JSON.parse(ev.data); } catch (_) { return; }
      if (data.type === 'auth' && data.item) applyAuthLiveItem(data.item);
      if (data.type === 'error' && data.error === '未登录') {
        setLiveStatus('offline', '未登录');
      }
    });
    liveSocket.addEventListener('close', () => {
      setLiveStatus('offline', '实时重连中…');
      if (livePingTimer) {
        clearInterval(livePingTimer);
        livePingTimer = null;
      }
      startFeedFallback();
      liveReconnectTimer = setTimeout(connectLive, 2000);
    });
    liveSocket.addEventListener('error', () => {
      try { liveSocket.close(); } catch (_) { /* ignore */ }
    });
  }

  loadAuthFeed().finally(() => connectLive());

  // ── 分页状态 ──
  const pages = { info: 1, logs: 1, cpu: 1, devices: 1, history: 1 };
  let historyCpuId = '';

  function getPager(table) {
    return document.querySelector(`.pager[data-table="${table}"]`);
  }

  function getPageSize(table) {
    const pager = getPager(table);
    return parseInt(pager?.querySelector('[data-page-size]')?.value || '10', 10);
  }

  function closeAllPageSizePickers() {
    document.querySelectorAll('.page-size-picker.is-open').forEach(picker => {
      picker.classList.remove('is-open');
      picker.querySelector('.page-size-picker__trigger')
        ?.setAttribute('aria-expanded', 'false');
    });
  }

  function syncPageSizePicker(select) {
    const picker = select.closest('.page-size-picker');
    if (!picker) return;
    const trigger = picker.querySelector('.page-size-picker__trigger');
    const label = select.options[select.selectedIndex]?.textContent || select.value;
    if (trigger) trigger.textContent = label;
    picker.querySelectorAll('.page-size-picker__option').forEach(opt => {
      opt.classList.toggle('is-selected', opt.dataset.value === select.value);
      opt.setAttribute('aria-selected', opt.dataset.value === select.value ? 'true' : 'false');
    });
  }

  function initPageSizePickers() {
    document.querySelectorAll('select[data-page-size]').forEach(select => {
      if (select.dataset.enhanced) return;
      select.dataset.enhanced = '1';

      const picker = document.createElement('div');
      picker.className = 'page-size-picker';

      const trigger = document.createElement('button');
      trigger.type = 'button';
      trigger.className = 'page-size-picker__trigger';
      trigger.setAttribute('aria-haspopup', 'listbox');
      trigger.setAttribute('aria-expanded', 'false');

      const menu = document.createElement('ul');
      menu.className = 'page-size-picker__menu';
      menu.setAttribute('role', 'listbox');

      Array.from(select.options).forEach(opt => {
        const li = document.createElement('li');
        li.className = 'page-size-picker__option';
        li.setAttribute('role', 'option');
        li.dataset.value = opt.value;
        li.textContent = opt.textContent;
        if (opt.selected) {
          li.classList.add('is-selected');
          li.setAttribute('aria-selected', 'true');
          trigger.textContent = opt.textContent;
        }
        li.addEventListener('click', e => {
          e.stopPropagation();
          select.value = opt.value;
          syncPageSizePicker(select);
          closeAllPageSizePickers();
          select.dispatchEvent(new Event('change', { bubbles: true }));
        });
        menu.appendChild(li);
      });

      trigger.addEventListener('click', e => {
        e.stopPropagation();
        const wasOpen = picker.classList.contains('is-open');
        closeAllPageSizePickers();
        if (!wasOpen) {
          picker.classList.add('is-open');
          trigger.setAttribute('aria-expanded', 'true');
        }
      });

      trigger.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
          closeAllPageSizePickers();
          return;
        }
        if (!picker.classList.contains('is-open')) {
          if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            closeAllPageSizePickers();
            picker.classList.add('is-open');
            trigger.setAttribute('aria-expanded', 'true');
          }
    return;
  }
        const options = [...menu.querySelectorAll('.page-size-picker__option')];
        let idx = options.findIndex(o => o.classList.contains('is-selected'));
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          idx = Math.min(idx + 1, options.length - 1);
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          idx = Math.max(idx - 1, 0);
        } else if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          options[idx]?.click();
          return;
      } else {
          return;
        }
        select.value = options[idx].dataset.value;
        syncPageSizePicker(select);
      });

      select.classList.add('page-size-picker__native');
      select.tabIndex = -1;
      select.setAttribute('aria-hidden', 'true');

      const wrap = select.closest('.select-wrap');
      if (wrap) {
        wrap.replaceWith(picker);
      }
      picker.appendChild(select);
      picker.appendChild(trigger);
      picker.appendChild(menu);
    });

    document.addEventListener('click', closeAllPageSizePickers);
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') closeAllPageSizePickers();
    });
  }

  initPageSizePickers();

  function setupPager(table, onPage) {
    const pager = getPager(table);
    if (!pager) return;
    pager.querySelector('.btn-prev')?.addEventListener('click', () => {
      if (pages[table] > 1) { pages[table]--; onPage(); }
    });
    pager.querySelector('.btn-next')?.addEventListener('click', () => {
      const total = parseInt(pager.dataset.totalPages || '1', 10);
      if (pages[table] < total) { pages[table]++; onPage(); }
    });
    pager.querySelector('[data-page-size]')?.addEventListener('change', () => {
      pages[table] = 1;
      onPage();
    });
  }

  function updatePager(table, current, total, totalCount) {
    const pager = getPager(table);
    if (!pager) return;
    pager.dataset.totalPages = String(total);
    pager.querySelector('.page-info').textContent = `第 ${current} / ${total} 页`;
    const totalEl = pager.querySelector('[data-total]');
    if (totalEl) totalEl.textContent = totalCount == null ? `共 ${total} 页` : `共 ${totalCount} 条`;
    pager.querySelector('.btn-prev').disabled = current <= 1;
    pager.querySelector('.btn-next').disabled = current >= total;
    pages[table] = current;
  }

  // ── 授权记录 ──
  async function loadInfoData() {
    const pageSize = getPageSize('info');
    const params = new URLSearchParams({
      page: pages.info,
      pageSize,
      q: document.getElementById('info-q')?.value || '',
      cpu_id: document.getElementById('info-cpu')?.value || '',
      ip: document.getElementById('info-ip')?.value || '',
      start: document.getElementById('info-start')?.value || '',
      end: document.getElementById('info-end')?.value || '',
    });
    const data = await api(`/api/info?${params}`).then(r => r.json());
    const tbody = document.querySelector('#infoTable tbody');
    tbody.innerHTML = data.info_data.map(row => `
      <tr>
        <td>${displayDate(row.DateTime)}</td>
        <td>${esc(row.IP || '—')}</td>
        <td><button type="button" class="link-btn cpu-link" data-cpu="${esc(row.CPU_ID)}">${esc(row.CPU_ID || '—')}</button></td>
        <td>${esc(row.Motherboard_ID || '—')}</td>
        <td>${esc(row.Disk_ID || '—')}</td>
        <td>${esc(row.Memory_ID || '—')}</td>
        <td><code title="${esc(row.device_fingerprint || '')}">${esc(shortFp(row.device_fingerprint))}</code></td>
        <td>${row.is_new_device ? '<span class="badge badge--new">新</span>' : '—'}</td>
      </tr>`).join('') || '<tr><td colspan="8" class="empty-cell">暂无数据</td></tr>';
    updatePager('info', data.current_page, data.total_pages, data.total_count);
    bindCpuLinks();
  }

  document.getElementById('info-search')?.addEventListener('click', () => { pages.info = 1; loadInfo(); });
  document.getElementById('info-export')?.addEventListener('click', () => {
    const params = new URLSearchParams({
      q: document.getElementById('info-q')?.value || '',
      cpu_id: document.getElementById('info-cpu')?.value || '',
      ip: document.getElementById('info-ip')?.value || '',
      start: document.getElementById('info-start')?.value || '',
      end: document.getElementById('info-end')?.value || '',
    });
    window.open(withTenant(`/api/info/export?${params}`), '_blank');
  });
  setupPager('info', loadInfo);

  // ── 授权日志 ──
  async function loadAuthLogsData() {
    const pageSize = getPageSize('logs');
    const params = new URLSearchParams({
      page: pages.logs,
      pageSize,
      cpu_id: document.getElementById('log-cpu')?.value || '',
      ip: document.getElementById('log-ip')?.value || '',
      result: document.getElementById('log-result')?.value || '',
      start: document.getElementById('log-start')?.value || '',
      end: document.getElementById('log-end')?.value || '',
    });
    const data = await api(`/api/auth_logs?${params}`).then(r => r.json());
    const tbody = document.querySelector('#authLogTable tbody');
    tbody.innerHTML = data.logs.map(row => `
      <tr>
        <td>${displayDate(row.DateTime)}</td>
        <td><button type="button" class="link-btn cpu-link" data-cpu="${esc(row.CPU_ID)}">${esc(row.CPU_ID || '—')}</button></td>
        <td>${esc(row.IP || '—')}</td>
        <td><span class="badge badge--${row.result}">${row.result === 'success' ? '成功' : '失败'}</span></td>
        <td>${esc(row.reason || '—')}</td>
        <td>${row.returned_auth ? '是' : '否'}</td>
        <td>${row.hardware_changed ? '<span class="badge badge--stale">有变更</span>' : '无'}</td>
        <td><code title="${esc(row.device_fingerprint || '')}">${esc(shortFp(row.device_fingerprint))}</code></td>
        <td>${row.is_new_device ? '<span class="badge badge--new">新</span>' : '—'}</td>
      </tr>`).join('') || '<tr><td colspan="9" class="empty-cell">暂无数据</td></tr>';
    updatePager('logs', data.current_page, data.total_pages, data.total_count);
    bindCpuLinks();
  }

  document.getElementById('log-search')?.addEventListener('click', () => { pages.logs = 1; loadAuthLogs(); });
  document.getElementById('log-export')?.addEventListener('click', () => {
    const params = new URLSearchParams({
      cpu_id: document.getElementById('log-cpu')?.value || '',
      ip: document.getElementById('log-ip')?.value || '',
      result: document.getElementById('log-result')?.value || '',
      start: document.getElementById('log-start')?.value || '',
      end: document.getElementById('log-end')?.value || '',
    });
    window.open(withTenant(`/api/auth_logs/export?${params}`), '_blank');
  });
  setupPager('logs', loadAuthLogs);

  // ── CPU 白名单 ──
  const stateLabels = { enabled: '已启用', disabled: '已禁用' };

  async function loadCpuIdsData() {
    const pageSize = getPageSize('cpu');
    const q = document.getElementById('cpu-q')?.value || '';
    const data = await api(`/api/cpu_ids?page=${pages.cpu}&pageSize=${pageSize}&q=${encodeURIComponent(q)}`).then(r => r.json());
    const tbody = document.querySelector('#cpuTable tbody');
    tbody.innerHTML = data.cpu_ids.map(row => `
      <tr data-id="${row.id}">
        <td><button type="button" class="link-btn cpu-link" data-cpu="${esc(row.CPU_Processor_ID)}">${esc(row.CPU_Processor_ID)}</button></td>
        <td><span class="badge badge--${row.status}">${stateLabels[row.status] || row.status}</span></td>
        <td class="editable-note" data-id="${row.id}" title="双击编辑"><button type="button" class="cell-edit" aria-label="编辑备注">${esc(row.note || '—')}${V3UI.icon('edit')}</button></td>
        <td>${esc(row.expires_at || '永久有效')}</td>
        <td>${displayDate(row.first_auth_at)}</td>
        <td>${displayDate(row.last_auth_at)}</td>
        <td>${row.auth_count || 0}</td>
        <td class="actions">
          <button type="button" class="btn-xs btn-toggle" data-id="${row.id}" data-status="${row.status}">${row.status === 'enabled' ? '禁用' : '启用'}</button>
          <button type="button" class="btn-xs btn-del" data-id="${row.id}">删除</button>
        </td>
      </tr>`).join('') || '<tr><td colspan="8" class="empty-cell">暂无数据</td></tr>';
    updatePager('cpu', data.current_page, data.total_pages, data.total_count);
    bindCpuLinks();
    bindCpuActions();
  }

  function bindCpuActions() {
    document.querySelectorAll('.btn-toggle').forEach(btn => {
      btn.onclick = async () => {
        const id = btn.dataset.id;
        const newStatus = btn.dataset.status === 'enabled' ? 'disabled' : 'enabled';
        if (!await V3UI.confirm(newStatus === 'disabled' ? '禁用这个 CPU？' : '启用这个 CPU？', btn.closest('tr').querySelector('.cpu-link').textContent, newStatus === 'disabled')) return;
        const response = await mutate(`/api/cpu_id/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: newStatus }),
        });
        if (!response) return;
        loadCpuIds();
      };
    });
    document.querySelectorAll('.btn-del').forEach(btn => {
      btn.onclick = async () => {
        if (!await V3UI.confirm('删除这个 CPU？', btn.closest('tr').querySelector('.cpu-link').textContent, true)) return;
        const response = await mutate(`/api/cpu_id/${btn.dataset.id}`, { method: 'DELETE' });
        if (!response) return;
        loadCpuIds();
      };
    });
    document.querySelectorAll('.editable-note').forEach(cell => {
      const edit = () => {
        if (cell.querySelector('input')) return;
        const old = cell.querySelector('button')?.textContent || cell.textContent;
        const input = document.createElement('input'); input.className='inline-note'; input.value=old === '—' ? '' : old;
        input.setAttribute('aria-label','备注，Enter 保存，Escape 取消'); cell.replaceChildren(input); input.focus();
        input.addEventListener('keydown', async e => {
          if (e.key === 'Escape') { loadCpuIds(); return; }
          if (e.key !== 'Enter') return;
          input.disabled=true;
          try { const res = await api(`/api/cpu_id/${cell.dataset.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({note:input.value})});
            if (!res.ok) throw new Error('备注更新失败'); loadCpuIds();
          } catch(error) { input.disabled=false; showToast(error.message); }
        });
      };
      cell.ondblclick = edit; cell.querySelector('button')?.addEventListener('click',edit);
    });
  }

  document.getElementById('addCpuId')?.addEventListener('click', async () => {
    const cpu_id = document.getElementById('newCpuId').value.trim();
    const note = document.getElementById('newCpuNote').value.trim();
    const expires = document.getElementById('newCpuExpires').value;
    if (!cpu_id) { showToast('请填写 CPU_ID'); document.getElementById('newCpuId').focus(); return; }
    const button = document.getElementById('addCpuId'); button.disabled=true;
    try {
      const response = await mutate('/api/add_cpu_id',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({cpu_id,note,expires_at:expires || null})
      });
      if (!response) return;
      document.getElementById('newCpuId').value='';
      document.getElementById('newCpuNote').value='';
      document.getElementById('newCpuExpires').value='';
      showToast('CPU 添加成功'); V3UI.close('addCpuModal'); loadCpuIds();
    } finally { button.disabled=false; }
  });

  let pendingImportFile = null;
  document.getElementById('cpuImportFile')?.addEventListener('change', e => {
    pendingImportFile = e.target.files[0] || null;
    if (pendingImportFile && !pendingImportFile.name.toLowerCase().endsWith('.csv')) {
      showToast('请选择 .csv 格式的文件'); pendingImportFile = null;
    }
    document.getElementById('selectedImportFile').textContent = pendingImportFile?.name || '尚未选择文件';
    document.getElementById('startImport').disabled = !pendingImportFile;
  });
  document.getElementById('startImport')?.addEventListener('click', async () => {
    if (!pendingImportFile) return;
    const button = document.getElementById('startImport'); button.disabled = true; button.textContent = '导入中…';
    const fd = new FormData(); fd.append('file',pendingImportFile);
    try {
      const res = await api('/api/cpu_ids/import',{method:'POST',body:fd});
      const body = await res.json();
      if (!res.ok) throw new Error(body.message || '导入失败，请重试');
      V3UI.close('importModal');
      document.getElementById('importResultMessage').textContent = body.message;
      document.getElementById('importAdded').textContent = body.message?.match(/新增\s*(\d+)/)?.[1] || '—';
      document.getElementById('importSkipped').textContent = body.message?.match(/跳过\s*(\d+)/)?.[1] || '—';
      V3UI.open('importResultModal');
      document.getElementById('cpuImportFile').value = ''; pendingImportFile=null;
      document.getElementById('selectedImportFile').textContent = '尚未选择文件';
      loadCpuIds();
    } catch(error) { showToast(error.message); }
    finally { button.textContent='开始导入'; button.disabled=!pendingImportFile; }
  });

  document.getElementById('cpu-search')?.addEventListener('click', () => { pages.cpu = 1; loadCpuIds(); });
  document.getElementById('cpu-export')?.addEventListener('click', () => window.open(withTenant('/api/cpu_ids/export'), '_blank'));
  setupPager('cpu', loadCpuIds);
  setInterval(() => { if (activeTab === 'whitelist' && !document.querySelector('.inline-note')) loadCpuIds(); }, 5000);

  // ── 设备管理（指纹） ──
  let deviceView = 'flat';
  const deviceStateMap = {
    active: { label: '近期活跃', cls: 'active' },
    never: { label: '从未授权', cls: 'never' },
    stale: { label: '长期未授权', cls: 'stale' },
    blocked: { label: '已封禁', cls: 'disabled' },
  };

  function renderDeviceSummary(counts) {
    const summary = document.getElementById('statusSummary');
    if (!summary) return;
    summary.innerHTML = Object.entries(counts || {}).map(([k, v]) => {
      const m = deviceStateMap[k] || { label: k, cls: k };
      return `<div class="status-card status-card--${m.cls}">${V3UI.icon(k === 'blocked' ? 'ban' : 'devices')}<span class="status-num">${v}</span><span class="status-label">${m.label}</span></div>`;
    }).join('');
  }

  function deviceRowHtml(row) {
    const m = deviceStateMap[row.state] || { label: row.state, cls: row.state };
    const fp = row.device_fingerprint;
    const alias = row.alias || '—';
    return `<tr data-fp="${esc(fp)}">
      <td class="editable-alias" data-fp="${esc(fp)}" title="双击编辑别名"><button type="button" class="cell-edit" aria-label="编辑设备别名">${esc(alias)}${V3UI.icon('edit')}</button></td>
      <td><code title="${esc(fp)}">${esc(shortFp(fp))}</code></td>
      <td><code class="mono-sm">${esc(row.CPU_ID || '—')}</code></td>
      <td><span class="badge badge--${m.cls}">${m.label}</span></td>
      <td>${displayDate(row.first_seen_at)}</td>
      <td>${displayDate(row.last_seen_at)}</td>
      <td>${row.auth_count || 0}</td>
      <td class="actions">
        <button type="button" class="btn-xs btn-history" data-cpu="${esc(row.CPU_ID)}" data-fp="${esc(fp)}">历史</button>
        <button type="button" class="btn-xs btn-block" data-fp="${esc(fp)}" data-blocked="${row.status === 'blocked' ? '1' : '0'}">${row.status === 'blocked' ? '解封' : '封禁'}</button>
      </td>
    </tr>`;
  }

  function renderDeviceTree(groups) {
    const el = document.getElementById('deviceTree');
    if (!el) return;
    el.innerHTML = groups.map(g => `
      <details class="device-tree__group" open>
        <summary class="device-tree__summary">
          <code class="mono-sm">${esc(g.cpu_id)}</code>
          ${g.cpu_note ? `<span class="device-tree__note">${esc(g.cpu_note)}</span>` : ''}
          <span class="device-tree__count">${g.device_count} 台</span>
        </summary>
        <div class="device-tree__children">
          ${g.devices.map(d => {
            const m = deviceStateMap[d.state] || { label: d.state, cls: d.state };
            return `<div class="device-tree__item" data-fp="${esc(d.device_fingerprint)}">
              <div class="device-tree__item-head">
                <strong class="editable-alias" data-fp="${esc(d.device_fingerprint)}" title="双击编辑">${esc(d.alias || shortFp(d.device_fingerprint))}</strong>
                <span class="badge badge--${m.cls}">${m.label}</span>
              </div>
              <div class="device-tree__meta">
                <code title="${esc(d.device_fingerprint)}">${esc(shortFp(d.device_fingerprint))}</code>
                <span>最近 ${esc(d.last_seen_at || '—')}</span>
                <span>${d.auth_count || 0} 次</span>
              </div>
              <div class="device-tree__actions">
                <button type="button" class="btn-xs btn-history" data-cpu="${esc(d.CPU_ID)}" data-fp="${esc(d.device_fingerprint)}">历史</button>
                <button type="button" class="btn-xs btn-block" data-fp="${esc(d.device_fingerprint)}" data-blocked="${d.status === 'blocked' ? '1' : '0'}">${d.status === 'blocked' ? '解封' : '封禁'}</button>
              </div>
            </div>`;
          }).join('')}
        </div>
      </details>`).join('') || '<p class="empty-cell">暂无设备</p>';
    bindDeviceActions();
  }

  async function loadDeviceStatusData() {
    const pageSize = getPageSize('devices');
    const q = encodeURIComponent(document.getElementById('device-q')?.value || '');
    const data = await api(
      `/api/devices?view=${deviceView}&page=${pages.devices}&pageSize=${pageSize}&q=${q}`
    ).then(r => r.json());
    if (!data.counts) throw new Error('设备列表加载失败');
    renderDeviceSummary(data.counts);

    const flatWrap = document.getElementById('deviceFlatWrap');
    const treeEl = document.getElementById('deviceTree');
    if (deviceView === 'tree') {
      flatWrap?.classList.add('hidden');
      treeEl?.classList.remove('hidden');
      renderDeviceTree(data.tree || []);
      updatePager('devices', data.current_page, data.total_pages, data.total_count);
    } else {
      flatWrap?.classList.remove('hidden');
      treeEl?.classList.add('hidden');
      const tbody = document.querySelector('#deviceTable tbody');
      tbody.innerHTML = (data.devices || []).map(deviceRowHtml).join('')
        || '<tr><td colspan="8" class="empty-cell">暂无设备，授权成功后将自动登记</td></tr>';
      updatePager('devices', data.current_page, data.total_pages, data.total_count);
      bindDeviceActions();
    }
  }

  async function patchDevice(fp, body) {
    const response = await mutate(`/api/device/${encodeURIComponent(fp)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response) return;
    showToast('设备信息已更新');
    loadDeviceStatus();
  }

  function bindDeviceActions() {
    document.querySelectorAll('.btn-history').forEach(btn => {
      btn.onclick = () => showCpuHistory(btn.dataset.cpu, btn.dataset.fp);
    });
    document.querySelectorAll('.btn-block').forEach(btn => {
      btn.onclick = async () => {
        const blocked = btn.dataset.blocked === '1';
        const action = blocked ? '解封' : '封禁';
        if (!await V3UI.confirm(`${action}这台设备？`,btn.dataset.fp,!blocked)) return;
        await patchDevice(btn.dataset.fp, { status: blocked ? 'enabled' : 'blocked' });
      };
    });
    document.querySelectorAll('.editable-alias').forEach(cell => {
      const edit = async () => {
        const fp = cell.dataset.fp;
        const alias = await V3UI.editAlias(cell.textContent === '—' ? '' : cell.textContent.trim());
        if (alias === null) return;
        await patchDevice(fp, { alias });
      };
      cell.ondblclick = edit;
      cell.querySelector('button')?.addEventListener('click',edit);
    });
  }

  document.querySelectorAll('.view-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('is-active'));
      btn.classList.add('is-active');
      deviceView = btn.dataset.view;
      pages.devices = 1;
      loadDeviceStatus();
    });
  });
  document.getElementById('device-search')?.addEventListener('click', () => {
    pages.devices = 1;
    loadDeviceStatus();
  });
  setupPager('devices', loadDeviceStatus);

  // ── 设备历史弹窗 ──
  let historyFingerprint = '';

  async function loadCpuHistoryData() {
    if (!historyCpuId) return;
    const pageSize = getPageSize('history');
    const fpParam = historyFingerprint
      ? `&fingerprint=${encodeURIComponent(historyFingerprint)}` : '';
    const data = await api(
      `/api/cpu/${encodeURIComponent(historyCpuId)}/history?page=${pages.history}&pageSize=${pageSize}${fpParam}`
    ).then(r => r.json());
    const titleFp = historyFingerprint ? ` · ${shortFp(historyFingerprint)}` : '';
    document.getElementById('historyTitle').textContent = '设备授权历史';
    document.getElementById('historySubtitle').textContent = `CPU_ID ${historyCpuId}${titleFp}`;
    document.getElementById('historyNote').textContent = data.meta?.note || '';
    document.getElementById('historyStats').innerHTML = `
      <span>累计成功授权<strong>${data.total_count} 次</strong></span>
      <span>首次 <strong>${esc(data.first_auth || '—')}</strong></span>
      <span>最近 <strong>${esc(data.last_auth || '—')}</strong></span>
`;
    document.getElementById('historyBody').innerHTML = data.records.map((r, i) => {
      const prev = data.records[i + 1];
      let hwFlag = '';
      if (r.is_new_device) {
        hwFlag = ' <span class="hw-change">新设备</span>';
      } else if (prev && r.device_fingerprint && prev.device_fingerprint
        && r.device_fingerprint !== prev.device_fingerprint) {
        hwFlag = ' <span class="hw-change">硬件变更</span>';
      }
      return `<tr>
        <td>${displayDate(r.DateTime)}</td>
        <td>${esc(r.IP || '—')}</td>
        <td>${esc(r.Motherboard_ID || '—')}</td>
        <td>${esc(r.Disk_ID || '—')}</td>
        <td>${esc(r.Memory_ID || '—')}</td>
        <td><code title="${esc(r.device_fingerprint || '')}">${esc(shortFp(r.device_fingerprint))}</code></td>
        <td>${r.is_new_device ? '<span class="badge badge--new">新</span>' : '—'}</td>
      </tr>`;
    }).join('') || '<tr><td colspan="7" class="empty-cell">暂无授权记录</td></tr>';
    updatePager('history', data.current_page, data.total_pages, data.total_count);
  }

  function showCpuHistory(cpuId, fingerprint = '') {
    historyCpuId = cpuId;
    historyFingerprint = fingerprint || '';
    pages.history = 1;
    V3UI.open('historyModal');
    loadCpuHistory();
  }

  setupPager('history', loadCpuHistory);

  function bindCpuLinks() {
    document.querySelectorAll('.cpu-link').forEach(btn => {
      btn.onclick = () => showCpuHistory(btn.dataset.cpu);
    });
  }

  document.querySelector('[data-close-history]')?.addEventListener('click', () => V3UI.close('historyModal'));
  document.getElementById('closeHistory')?.addEventListener('click', () => {
    V3UI.close('historyModal');
  });
  document.querySelector('#historyModal .modal__backdrop')?.addEventListener('click', () => {
    V3UI.close('historyModal');
  });

  // ── 修改密码 ──
  const passwordModal = document.getElementById('passwordModal');
  const passwordForm = document.getElementById('passwordForm');
  const passwordAlert = document.getElementById('passwordAlert');

  function setPasswordAlert(message, type = 'error') {
    if (!passwordAlert) return;
    if (!message) {
      passwordAlert.textContent = '';
      passwordAlert.classList.add('hidden');
      passwordAlert.classList.remove('auth__alert--error', 'auth__alert--success');
      return;
    }
    passwordAlert.textContent = message;
    passwordAlert.classList.remove('hidden', 'auth__alert--error', 'auth__alert--success');
    passwordAlert.classList.add(type === 'success' ? 'auth__alert--success' : 'auth__alert--error');
  }

  function openPasswordModal() {
    passwordForm?.reset();
    setPasswordAlert('');
    V3UI.open('passwordModal');
    document.getElementById('oldPassword')?.focus();
  }

  function closePasswordModal() {
    V3UI.close('passwordModal');
    passwordForm?.reset();
    setPasswordAlert('');
  }

  function validatePasswordStrength(password) {
    return /^(?=.*[0-9])(?=.*[a-z])(?=.*[A-Z])(?=.*[\W_]).{8,}$/.test(password);
  }

  document.getElementById('change-password-button')?.addEventListener('click', openPasswordModal);
  document.getElementById('closePassword')?.addEventListener('click', closePasswordModal);
  document.getElementById('cancelPassword')?.addEventListener('click', closePasswordModal);
  document.querySelector('#passwordModal [data-close-password]')?.addEventListener('click', closePasswordModal);

  passwordForm?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const oldPassword = document.getElementById('oldPassword')?.value || '';
    const newPassword = document.getElementById('newPassword')?.value || '';
    const confirmPassword = document.getElementById('confirmPassword')?.value || '';

    if (!oldPassword || !newPassword || !confirmPassword) {
      setPasswordAlert('请填写当前密码和新密码');
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordAlert('两次输入的新密码不一致');
      document.getElementById('confirmPassword')?.focus();
      return;
    }
    if (oldPassword === newPassword) {
      setPasswordAlert('新密码不能与当前密码相同');
      return;
    }
    if (!validatePasswordStrength(newPassword)) {
      setPasswordAlert('新密码须至少 8 位，且包含大小写字母、数字与特殊符号');
      document.getElementById('newPassword')?.focus();
      return;
    }

    const submitBtn = document.getElementById('submitPassword');
    if (submitBtn) submitBtn.disabled = true;
    try {
      const res = await api('/api/change_password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          old_password: oldPassword,
          new_password: newPassword,
          confirm_password: confirmPassword,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setPasswordAlert(body.message || '修改失败，请重试');
        return;
      }
      setPasswordAlert(body.message || '密码修改成功', 'success');
      showToast('密码已修改，即将退出重新登录');
      setTimeout(() => { window.location.href = '/logout'; }, 1200);
    } catch (_) {
      setPasswordAlert('网络异常，请稍后重试');
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });

  // ── 退出 ──
  document.getElementById('logout-button')?.addEventListener('click', async e => {
    e.preventDefault();
    if (await V3UI.confirm('退出当前账户？')) window.location.href = '/logout';
  });

  document.getElementById('manualRefresh')?.addEventListener('click', async () => {
    const button = document.getElementById('manualRefresh');
    button.disabled = true; button.setAttribute('aria-busy','true');
    const label = button.querySelector('span'); label.textContent = '刷新中';
    try { await reloadActiveTenantData(); }
    finally { button.disabled = false; button.setAttribute('aria-busy','false'); label.textContent = '刷新'; }
  });

  // 轮询当前 tab 数据
  setInterval(() => {
    if (activeTab === 'records') loadInfo();
    if (activeTab === 'authlogs') loadAuthLogs();
    if (activeTab === 'devices') loadDeviceStatus();
  }, 3000);

})();


