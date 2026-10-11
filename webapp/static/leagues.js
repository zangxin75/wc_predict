/* leagues.js — 联赛板块前端:联赛切换 + 赛程/积分/推演/价值 四个子页。
   数据:/api/leagues*(Task 11);免费用户 odds=null(API 已剥 market),渲染 "***"。
   Pro 状态与 WC 一致:调 /api/whoami 取 is_paid;value 非 Pro 返回 402 → Pro 墙。 */
(() => {
  const POLL_MS = 60000;
  const state = { leagues: [], code: null, tab: 'matches', isPaid: false, data: {} };

  // URL ↔ 视图同步:?league=pl&tab=standings 直达;点击后地址栏随之更新(不重载)
  function applyRoute() {
    try {
      const q = new URLSearchParams(location.search);
      const lg = q.get('league') || '';
      if (/^[A-Za-z0-9_-]{1,12}$/.test(lg)) state.code = lg.toUpperCase();
      const tb = q.get('tab');
      if (['matches', 'standings', 'sim', 'value'].includes(tb)) state.tab = tb;
    } catch (e) {}
  }
  function syncRoute() {
    try {
      const u = new URL(location.href);
      if (state.code) u.searchParams.set('league', state.code); else u.searchParams.delete('league');
      if (state.tab !== 'matches') u.searchParams.set('tab', state.tab); else u.searchParams.delete('tab');
      history.replaceState(null, '', u);
    } catch (e) {}
  }
  let countdown = POLL_MS / 1000;

  // ── 状态栏(WC 页同款:live-dot / 上次更新 / 倒计时)──
  function setStatus(kind, labelKey) {
    const dot = $('liveDot'), label = $('statusLabel');
    if (!dot || !label) return;
    dot.className = 'live-dot ' + kind;
    label.textContent = tt(labelKey);
    if (kind) label.removeAttribute('data-i18n');
  }
  function stampUpdated() {
    const el = $('lastUpdated');
    if (el) el.textContent = new Date().toTimeString().slice(0, 8);
  }

  const $ = (id) => document.getElementById(id);
  const tt = (k) => (typeof I18N !== "undefined" && I18N.t) ? I18N.t(k) : k;
  const cn = (n) => (typeof I18N !== "undefined" && I18N.localClub) ? I18N.localClub(n) : n;
  const pct = (v) => v == null ? '–' : (v * 100).toFixed(1) + '%';
  const oddsTxt = (v) => v == null ? `<span class="lg-mask">***</span>` : v.toFixed(2);

  async function api(path) {
    const r = await fetch(path);
    if (r.status === 402) return { _pro: true };
    if (!r.ok) throw new Error(`${path} → ${r.status}`);
    return r.json();
  }

  async function boot() {
    applyRoute();
    try {
      const me = await api('/api/whoami');
      state.isPaid = !!me.is_paid;
    } catch (e) { state.isPaid = false; }
    const pb = $('proBtn'), pg = $('proBadge');
    // 第一阶段限时免费:全量开放,Upgrade 按钮隐藏;PRO 徽章仅作种子用户标识
    if (pb) pb.hidden = true;
    if (pg) pg.hidden = !state.isPaid;
    bindProModal();
    if (typeof I18N !== "undefined") { await I18N.ready; await I18N.loadClubs(); }  // 俱乐部译名就绪后再渲染
    bindRefreshBtn();
    await refreshLeagues();
    loadSchedule();
    setInterval(async () => {
      countdown -= 1;
      if (countdown <= 0) { countdown = POLL_MS / 1000; await tick(); }
      const cd = $('countdown');
      if (cd) cd.textContent = countdown + 's';
    }, 1000);
  }

  // ── Pro 弹窗(联赛页自有:邮箱恢复 + 邀请码,不跳世界杯页)──
  function proStatus(msgKey, isErr) {
    const s = $('lgProStatus');
    if (!s) return;
    s.hidden = false;
    s.textContent = tt(msgKey);
    s.style.color = isErr ? 'var(--signal-err, #e06c75)' : 'var(--signal-ok, #7ec97e)';
  }
  function closeProModal() {
    const m = $('lgProModal'), sc = $('lgProScrim');
    if (m) m.hidden = true;
    if (sc) sc.hidden = true;
    const s = $('lgProStatus'); if (s) s.hidden = true;
  }
  function bindProModal() {
    const btn = $('proBtn'), m = $('lgProModal'), sc = $('lgProScrim');
    if (!btn || !m) return;
    btn.addEventListener('click', () => { m.hidden = false; if (sc) sc.hidden = false; });
    const closeBtn = $('lgProClose');
    if (closeBtn) closeBtn.addEventListener('click', closeProModal);
    if (sc) sc.addEventListener('click', closeProModal);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeProModal(); });

    const emailBtn = $('lgEmailLoginBtn');
    if (emailBtn) emailBtn.addEventListener('click', async () => {
      const email = ($('lgEmail') ? $('lgEmail').value : '').trim();
      if (!email || !email.includes('@')) return proStatus('sub.invite_invalid_email', true);
      try {
        const r = await fetch(`/api/email-login?email=${encodeURIComponent(email)}`, { method: 'POST' });
        if (!r.ok) throw new Error(String(r.status));
        const j = await r.json();
        if (j.is_paid) { proStatus('sub.email_login_success', false); setTimeout(() => location.reload(), 800); }
        else proStatus('sub.email_login_not_pro', true);
      } catch (e) { proStatus('sub.email_login_error', true); }
    });

    const invBtn = $('lgInviteBtn');
    if (invBtn) invBtn.addEventListener('click', async () => {
      const email = ($('lgEmail') ? $('lgEmail').value : '').trim();
      const code = ($('lgInvite') ? $('lgInvite').value : '').trim();
      if (!code) return proStatus('sub.invite_need_email', true);
      if (!email || !email.includes('@')) return proStatus('sub.invite_need_email', true);
      try {
        const r = await fetch(`/api/redeem-invite?email=${encodeURIComponent(email)}&code=${encodeURIComponent(code)}`, { method: 'POST' });
        if (!r.ok) {
          const key = r.status === 404 ? 'sub.invite_not_found' : r.status === 409 ? 'sub.invite_already_used' : 'sub.invite_error';
          return proStatus(key, true);
        }
        proStatus('sub.invite_success', false);
        setTimeout(() => location.reload(), 800);
      } catch (e) { proStatus('sub.invite_error', true); }
    });
  }

  function bindRefreshBtn() {
    const btn = $('refreshBtn');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      setStatus('busy', 'status.updating');
      try { await fetch('/api/refresh', { method: 'POST' }); } catch (e) { /* 触发失败不阻塞本地刷新 */ }
      countdown = POLL_MS / 1000;
      await tick();
    });
  }

  async function refreshLeagues() {
    try {
      const d = await api('/api/leagues');
      state.leagues = d.leagues;
      if (!state.code || !state.leagues.some(l => l.code === state.code && l.available)) {
        const first = state.leagues.find(l => l.available);
        state.code = first ? first.code : null;
      }
      renderTabs();
      await loadTab();
      setStatus('ok', 'status.live');
      stampUpdated();
    } catch (e) {
      setStatus('err', 'status.failed');
      $('content').innerHTML = `<p class="lg-status">${tt('leagues.not_ready')}</p>`;
    }
  }

  async function tick() { await refreshLeagues(); loadSchedule(); }

  // ── 今日/未来赛程聚合板(/api/leagues/schedule,跨联赛按开赛时间排)──
  async function loadSchedule() {
    const box = $('todayBoard'), groups = $('todayGroups');
    if (!box || !groups) return;
    try {
      const d = await api('/api/leagues/schedule?days=3');
      if (!d.matches || !d.matches.length) { box.hidden = true; return; }
      const zh = typeof I18N !== 'undefined' && I18N.current === 'zh-CN';
      const byDate = {};
      for (const m of d.matches) (byDate[m.date] = byDate[m.date] || []).push(m);
      const today = d.today;
      let html = '';
      for (const date of Object.keys(byDate).sort()) {
        const tomorrow = new Date(today + 'T12:00');
        tomorrow.setDate(tomorrow.getDate() + 1);
        const tstr = tomorrow.toISOString().slice(0, 10);
        const label = date === today ? tt('leagues.today')
          : date === tstr ? tt('leagues.tomorrow')
          : date.slice(5);
        const rows = byDate[date].map(m => {
          const p = m.probs || {};
          const lgb = `<button class="lg-chip" data-code="${m.league}" title="${m.league_name}">${zh ? m.league_name_zh : m.league_name}</button>`;
          const low = m.low_confidence ? ` <span class="lg-low">⚠</span>` : '';
          const fav = Math.max(p.home || 0, p.draw || 0, p.away || 0);
          const prob = (v) => `<span class="${v === fav ? 'lg-fav' : ''}">${pct(v)}</span>`;
          const odds = m.odds ? `<span class="lg-today-odds">${(m.odds.b365h != null ? m.odds.b365h : '–')} / ${(m.odds.b365d != null ? m.odds.b365d : '–')} / ${(m.odds.b365a != null ? m.odds.b365a : '–')}</span>` : '';
          return `<div class="lg-today-row" data-code="${m.league}">
            <span class="lg-today-time mono">${m.time || ''}</span>${lgb}
            <span class="teams">${cn(m.home)} – ${cn(m.away)}${low}</span>
            <span class="lg-today-probs mono">${prob(p.home)} ${prob(p.draw)} ${prob(p.away)}</span>
            ${odds}</div>`;
        }).join('');
        html += `<h3 class="lg-today-date">${label}<span class="lg-today-count">${byDate[date].length}</span></h3>${rows}`;
      }
      groups.innerHTML = html;
      box.hidden = false;
      const tc = $('todayCount');
      if (tc) tc.textContent = String(d.matches.length);
      try { box.open = localStorage.getItem('fb_today_open') === '1'; } catch (e) {}
      box.ontoggle = () => { try { localStorage.setItem('fb_today_open', box.open ? '1' : '0'); } catch (e) {} };
      groups.querySelectorAll('.lg-today-row,.lg-chip').forEach(el =>
        el.onclick = () => { state.code = el.dataset.code; state.tab = 'matches'; renderTabs(); loadTab(); syncRoute(); document.body.scrollIntoView({ behavior: 'smooth', block: 'start' }); });
    } catch (e) { box.hidden = true; }
  }

  function scrollToContent() {
    const c = $('content');
    if (c) c.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function renderTabs() {
    const zh = typeof I18N !== 'undefined' && I18N.current === 'zh-CN';
    $('leagueTabs').innerHTML = state.leagues.map(l =>
      `<button class="lg-tab ${l.code === state.code ? 'active' : ''}"
        data-code="${l.code}" ${l.available ? '' : 'disabled'}>${zh ? l.name_zh : l.name}</button>`
    ).join('');
    $('leagueTabs').querySelectorAll('button[data-code]').forEach(b =>
      b.onclick = () => { state.code = b.dataset.code; renderTabs(); loadTab(); syncRoute(); scrollToContent(); });
    const subs = ['matches', 'standings', 'sim', 'value'];
    $('subTabs').innerHTML = subs.map(s =>
      `<button class="${s === state.tab ? 'active' : ''}" data-tab="${s}">${tt('leagues.tab.' + s)}</button>`
    ).join('');
    $('subTabs').querySelectorAll('button').forEach(b =>
      b.onclick = () => { state.tab = b.dataset.tab; renderTabs(); loadTab(); syncRoute(); scrollToContent(); });
  }

  async function loadTab() {
    if (!state.code) return;
    const c = $('content');
    try {
      if (state.tab === 'matches') {
        const d = await api(`/api/leagues/${state.code}/matches`);
        state.data = d; c.innerHTML = renderMatches(d);
      } else if (state.tab === 'standings') {
        const d = await api(`/api/leagues/${state.code}/standings`);
        c.innerHTML = renderStandings(d.standings);
      } else if (state.tab === 'sim') {
        const d = await api(`/api/leagues/${state.code}/sim`);
        c.innerHTML = renderSim(d.sim);
      } else {
        const d = await api(`/api/leagues/${state.code}/value`);
        c.innerHTML = d._pro ? proWall() : renderValue(d.values);
      }
    } catch (e) {
      c.innerHTML = `<p class="lg-status">${tt('leagues.not_ready')}</p>`;
    }
  }

  function renderMatches(d) {
    const row = (m) => {
      const isPost = m.status === 'post';
      const score = isPost ? `<span class="lg-score">${m.gh}–${m.ga}</span>` : '';
      const low = m.low_confidence ? ` <span class="lg-low">⚠ ${tt('leagues.low_conf')}</span>` : '';
      const probs = isPost ? '' :
        `<td>${pct(m.probs.home)}</td><td>${pct(m.probs.draw)}</td><td>${pct(m.probs.away)}</td>`;
      const odds = isPost ? '<td colspan="3" class="lg-c-odds"></td>' :
        `<td class="lg-c-odds">${oddsTxt(m.odds && m.odds.b365h)}</td><td class="lg-c-odds">${oddsTxt(m.odds && m.odds.b365d)}</td>
         <td class="lg-c-odds">${oddsTxt(m.odds && m.odds.b365a)}</td>`;
      const top = (!isPost && m.top_scores) ?
        m.top_scores.slice(0, 3).map(([s, p]) => `${s} ${pct(p)}`).join(' ') : '';
      return `<tr><td>${m.date}</td><td class="teams">${cn(m.home)} vs ${cn(m.away)}${low}</td>
        <td>${score}</td>${probs}${odds}<td class="lg-c-top">${top}</td></tr>`;
    };
    const thead = `<thead><tr><th></th><th></th><th>${tt('leagues.score')}</th>
      <th>1</th><th>X</th><th>2</th><th colspan="3" class="lg-c-odds">${tt('leagues.odds')}</th>
      <th class="lg-c-top">${tt('leagues.score')}</th></tr></thead>`;
    const upcoming = d.matches.filter(m => m.status !== 'post');
    const post = d.matches.filter(m => m.status === 'post')
      .sort((a, b) => (b.date + (b.time || '')).localeCompare(a.date + (a.time || '')));
    const upcomingTable = `<div class="lg-scroll"><table class="lg">${thead}<tbody>${upcoming.map(row).join('')}</tbody></table></div>`;
    const postBlock = post.length ? `
      <details class="lg-post">
        <summary><span class="lg-fold-label">${tt('leagues.finished')} (${post.length})</span><span class="lg-fold-arrow">▾</span></summary>
        <div class="lg-scroll"><table class="lg">${thead}<tbody>${post.map(row).join('')}</tbody></table></div>
      </details>` : '';
    return `${postBlock}${upcomingTable}
      <p class="lg-status">${tt('leagues.updated')}: ${d.generated_at || ''}</p>`;
  }

  function renderStandings(rows) {
    return `<div class="lg-scroll"><table class="lg"><thead><tr><th>#</th><th></th>
      <th>P</th><th>W</th><th>D</th><th>L</th><th>GF</th><th>GA</th><th>GD</th><th>Pts</th>
      </tr></thead><tbody>${rows.map((t, i) =>
        `<tr><td>${i + 1}</td><td class="teams">${cn(t.team)}</td><td>${t.p}</td><td>${t.w}</td>
         <td>${t.d}</td><td>${t.l}</td><td>${t.gf}</td><td>${t.ga}</td>
         <td>${t.gd}</td><td><b>${t.pts}</b></td></tr>`).join('')}</tbody></table></div>`;
  }

  function renderSim(sim) {
    if (!sim || !sim.teams) return `<p class="lg-status">${tt('leagues.not_ready')}</p>`;
    const rows = Object.entries(sim.teams)
      .sort((a, b) => b[1].title - a[1].title)
      .map(([team, p]) => `<tr><td class="teams">${cn(team)}</td>
        <td>${pct(p.title)}</td><td>${pct(p.ucl)}</td><td>${pct(p.releg)}</td></tr>`).join('');
    return `<div class="lg-scroll"><table class="lg"><thead><tr><th></th><th>${tt('leagues.title_race')}</th>
      <th>${tt('leagues.ucl_spots')}</th><th>${tt('leagues.relegation')}</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <p class="lg-status">n=${sim.n_sims}</p>`;
  }

  function renderValue(values) {
    if (!values.length) return `<p class="lg-status">–</p>`;
    return `<div class="lg-scroll"><table class="lg"><thead><tr><th></th><th></th><th>${tt('leagues.pick')}</th>
      <th>${tt('leagues.edge')}</th><th>${tt('leagues.odds')}</th><th>${tt('leagues.kelly')}</th>
      </tr></thead><tbody>${values.map(v =>
        `<tr><td>${v.date}</td><td class="teams">${cn(v.home)} vs ${cn(v.away)}</td>
         <td>${v.pick}</td><td>${pct(v.max_edge)}</td>
         <td>${(v.odds[v.pick] || 0).toFixed(2)}</td><td>${pct(v.kelly)}</td></tr>`).join('')}
      </tbody></table></div>`;
  }

  function proWall() {
    return `<p class="lg-status"><a class="lg-pro" href="/#pro">✨ ${tt('leagues.pro_only')}</a></p>`;
  }

  // ── 语言切换器(与 app.js bindLangSwitcher 同构,作用于联赛页)──
  function bindLangSwitcher() {
    const btn = $('langBtn'), menu = $('langMenu');
    if (!btn || !menu || typeof I18N === "undefined") return;
    const paint = () => {
      menu.innerHTML = I18N.LOCALES.map(code => {
        const m = I18N.META[code];
        return `<button class="lang-opt${code === I18N.current ? ' active' : ''}" data-lang="${code}" type="button" role="option">`
          + `<span class="opt-flag">${m.flag}</span><span>${m.name}</span></button>`;
      }).join('');
      menu.querySelectorAll('.lang-opt').forEach(opt =>
        opt.onclick = () => {
          const code = opt.getAttribute('data-lang');
          if (code) { I18N.switchTo(code, true); closeMenu(); }
        });
      $('langCurrent').textContent = `${I18N.META[I18N.current].flag} ${I18N.META[I18N.current].name}`;
    };
    const closeMenu = () => { menu.hidden = true; btn.setAttribute('aria-expanded', 'false'); };
    btn.addEventListener('click', e => {
      e.stopPropagation();
      if (menu.hidden) { menu.hidden = false; btn.setAttribute('aria-expanded', 'true'); }
      else closeMenu();
    });
    document.addEventListener('click', e => {
      if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) closeMenu();
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });
    paint();
    // 语言切换 → 译名/标签全量重渲染
    document.addEventListener('i18n:changed', () => {
      paint();
      if (state.code) { renderTabs(); loadTab(); }
    });
  }

  document.addEventListener('DOMContentLoaded', async () => {
    await boot();
    bindLangSwitcher();
  });
})();
