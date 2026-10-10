/* leagues.js — 联赛板块前端:联赛切换 + 赛程/积分/推演/价值 四个子页。
   数据:/api/leagues*(Task 11);免费用户 odds=null(API 已剥 market),渲染 "***"。
   Pro 状态与 WC 一致:调 /api/whoami 取 is_paid;value 非 Pro 返回 402 → Pro 墙。 */
(() => {
  const POLL_MS = 60000;
  const state = { leagues: [], code: null, tab: 'matches', isPaid: false, data: {} };
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
    try {
      const me = await api('/api/whoami');
      state.isPaid = !!me.is_paid;
    } catch (e) { state.isPaid = false; }
    const pb = $('proBtn'), pg = $('proBadge');
    if (pb) pb.hidden = state.isPaid;
    if (pg) pg.hidden = !state.isPaid;
    if (typeof I18N !== "undefined") { await I18N.ready; await I18N.loadClubs(); }  // 俱乐部译名就绪后再渲染
    bindRefreshBtn();
    await refreshLeagues();
    setInterval(async () => {
      countdown -= 1;
      if (countdown <= 0) { countdown = POLL_MS / 1000; await tick(); }
      const cd = $('countdown');
      if (cd) cd.textContent = countdown + 's';
    }, 1000);
  }

  function bindRefreshBtn() {
    const btn = $('refreshBtn');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      setStatus('busy', 'status.updating');
      try { await api('/api/refresh'); } catch (e) { /* 触发失败不阻塞本地刷新 */ }
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

  async function tick() { await refreshLeagues(); }

  function renderTabs() {
    const zh = typeof I18N !== 'undefined' && I18N.current === 'zh-CN';
    $('leagueTabs').innerHTML = state.leagues.map(l =>
      `<button class="lg-tab ${l.code === state.code ? 'active' : ''}"
        data-code="${l.code}" ${l.available ? '' : 'disabled'}>${zh ? l.name_zh : l.name}</button>`
    ).join('');
    $('leagueTabs').querySelectorAll('button[data-code]').forEach(b =>
      b.onclick = () => { state.code = b.dataset.code; renderTabs(); loadTab(); });
    const subs = ['matches', 'standings', 'sim', 'value'];
    $('subTabs').innerHTML = subs.map(s =>
      `<button class="${s === state.tab ? 'active' : ''}" data-tab="${s}">${tt('leagues.tab.' + s)}</button>`
    ).join('');
    $('subTabs').querySelectorAll('button').forEach(b =>
      b.onclick = () => { state.tab = b.dataset.tab; renderTabs(); loadTab(); });
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
    const rows = d.matches.map(m => {
      const isPost = m.status === 'post';
      const score = isPost ? `<span class="lg-score">${m.gh}–${m.ga}</span>` : '';
      const low = m.low_confidence ? ` <span class="lg-low">⚠ ${tt('leagues.low_conf')}</span>` : '';
      const probs = isPost ? '' :
        `<td>${pct(m.probs.home)}</td><td>${pct(m.probs.draw)}</td><td>${pct(m.probs.away)}</td>`;
      const odds = isPost ? '<td colspan="3"></td>' :
        `<td>${oddsTxt(m.odds && m.odds.b365h)}</td><td>${oddsTxt(m.odds && m.odds.b365d)}</td>
         <td>${oddsTxt(m.odds && m.odds.b365a)}</td>`;
      const top = (!isPost && m.top_scores) ?
        m.top_scores.slice(0, 3).map(([s, p]) => `${s} ${pct(p)}`).join(' ') : '';
      return `<tr><td>${m.date}</td><td class="teams">${cn(m.home)} vs ${cn(m.away)}${low}</td>
        <td>${score}</td>${probs}${odds}<td>${top}</td></tr>`;
    }).join('');
    return `<table class="lg"><thead><tr><th></th><th></th><th>${tt('leagues.score')}</th>
      <th>1</th><th>X</th><th>2</th><th colspan="3">${tt('leagues.odds')}</th>
      <th>${tt('leagues.score')}</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="lg-status">${tt('leagues.updated')}: ${d.generated_at || ''}</p>`;
  }

  function renderStandings(rows) {
    return `<table class="lg"><thead><tr><th>#</th><th></th>
      <th>P</th><th>W</th><th>D</th><th>L</th><th>GF</th><th>GA</th><th>GD</th><th>Pts</th>
      </tr></thead><tbody>${rows.map((t, i) =>
        `<tr><td>${i + 1}</td><td class="teams">${cn(t.team)}</td><td>${t.p}</td><td>${t.w}</td>
         <td>${t.d}</td><td>${t.l}</td><td>${t.gf}</td><td>${t.ga}</td>
         <td>${t.gd}</td><td><b>${t.pts}</b></td></tr>`).join('')}</tbody></table>`;
  }

  function renderSim(sim) {
    if (!sim || !sim.teams) return `<p class="lg-status">${tt('leagues.not_ready')}</p>`;
    const rows = Object.entries(sim.teams)
      .sort((a, b) => b[1].title - a[1].title)
      .map(([team, p]) => `<tr><td class="teams">${cn(team)}</td>
        <td>${pct(p.title)}</td><td>${pct(p.ucl)}</td><td>${pct(p.releg)}</td></tr>`).join('');
    return `<table class="lg"><thead><tr><th></th><th>${tt('leagues.title_race')}</th>
      <th>${tt('leagues.ucl_spots')}</th><th>${tt('leagues.relegation')}</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <p class="lg-status">n=${sim.n_sims}</p>`;
  }

  function renderValue(values) {
    if (!values.length) return `<p class="lg-status">–</p>`;
    return `<table class="lg"><thead><tr><th></th><th></th><th>${tt('leagues.pick')}</th>
      <th>${tt('leagues.edge')}</th><th>${tt('leagues.odds')}</th><th>${tt('leagues.kelly')}</th>
      </tr></thead><tbody>${values.map(v =>
        `<tr><td>${v.date}</td><td class="teams">${cn(v.home)} vs ${cn(v.away)}</td>
         <td>${v.pick}</td><td>${pct(v.max_edge)}</td>
         <td>${(v.odds[v.pick] || 0).toFixed(2)}</td><td>${pct(v.kelly)}</td></tr>`).join('')}
      </tbody></table>`;
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
