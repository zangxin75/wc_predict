/* ============================================================
   2026 世界杯预测 - 前端逻辑
   轮询 /api/* → 渲染 → 比分变化琥珀脉冲
   ============================================================ */

// ── 48 队旗帜 emoji(ISO 国家代码)──────────────────
const FLAG = {
  Algeria:'🇩🇿',Argentina:'🇦🇷',Australia:'🇦🇺',Austria:'🇦🇹',
  Belgium:'🇧🇪','Bosnia and Herzegovina':'🇧🇦',Brazil:'🇧🇷',Canada:'🇨🇦',
  'Cape Verde':'🇨🇻',Colombia:'🇨🇴',Croatia:'🇭🇷','Curaçao':'🇨🇼',
  'Czech Republic':'🇨🇿','DR Congo':'🇨🇩',Ecuador:'🇪🇨',Egypt:'🇪🇬',
  England:'🏴󠁧󠁢󠁥󠁮󠁧󠁿',France:'🇫🇷',Germany:'🇩🇪',Ghana:'🇬🇭',
  Haiti:'🇭🇹',Iran:'🇮🇷',Iraq:'🇮🇶','Ivory Coast':'🇨🇮',Japan:'🇯🇵',
  Jordan:'🇯🇴',Mexico:'🇲🇽',Morocco:'🇲🇦',Netherlands:'🇳🇱',
  'New Zealand':'🇳🇿',Norway:'🇳🇴',Panama:'🇵🇦',Paraguay:'🇵🇾',
  Portugal:'🇵🇹',Qatar:'🇶🇦','Saudi Arabia':'🇸🇦',Scotland:'🏴󠁧󠁢󠁳󠁣󠁴󠁿',
  Senegal:'🇸🇳','South Africa':'🇿🇦','South Korea':'🇰🇷',Spain:'🇪🇸',
  Sweden:'🇸🇪',Switzerland:'🇨🇭',Tunisia:'🇹🇳',Turkey:'🇹🇷',
  'United States':'🇺🇸',Uruguay:'🇺🇾',Uzbekistan:'🇺🇿'
};
const STAGE_ZH = {Group:'小组赛',R32:'32强',R16:'16强',QF:'1/4决赛',SF:'半决赛',Final:'决赛'};
const WEEKDAY = ['周日','周一','周二','周三','周四','周五','周六'];
const RESULT_ZH = {home_win:'主胜',draw:'平',away_win:'客胜'};

// ── 状态 ──────────────────────────────────────────
let pollSec = 30;
let countdown = 30;
let tickTimer = null;
let prevScoreMap = {};      // key -> "h-a" 用于检测比分变化
let firstRender = true;     // 首次渲染(带 staggered 动画)
let activeStage = 'all';
let histExpanded = false;  // 历史赛果折叠区展开状态
let cache = { schedule:[], status:{}, champions:[], groups:{}, backtest:null, market:null, value:null };

// ── 工具 ──────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const flag = (team) => FLAG[team] || '🏳️';
const matchKey = (m) => `${m.date}|${m.home}|${m.away}`;
const scoreSig = (m) => m.status === 'finished' ? `${m.score[0]}-${m.score[1]}` : (m.status);

async function getJSON(url) {
  const r = await fetch(url, {cache: 'no-store'});
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

function fmtDate(iso) {
  const d = new Date(iso + 'T00:00:00');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return { label: `${mm}.${dd}`, weekday: WEEKDAY[d.getDay()] };
}

// ── 状态栏渲染 ────────────────────────────────────
function renderStatus() {
  const s = cache.status || {};
  const dot = $('liveDot'), lbl = $('statusLabel');
  dot.className = 'live-dot';
  if (s.running) { dot.classList.add('busy'); lbl.textContent = '更新中…'; }
  else if (s.success === false) { dot.classList.add('err'); lbl.textContent = '更新失败(用缓存)'; }
  else if (s.success) { dot.classList.add('ok'); lbl.textContent = '实时在线'; }
  else { lbl.textContent = '连接中…'; }

  $('lastUpdated').textContent = s.last_updated ? s.last_updated.slice(11, 16) : '-';
  if (s.error) lbl.textContent = s.error.slice(0, 20);
}

// ── 赛程渲染 ──────────────────────────────────────
function renderSchedule() {
  const list = $('scheduleList');
  let matches = cache.schedule || [];
  if (activeStage !== 'all') matches = matches.filter(m => m.stage === activeStage);
  if (!matches.length) { list.innerHTML = '<div class="placeholder">无符合筛选的比赛</div>'; return; }

  // 按日期分组(保持升序)
  const byDay = {};
  for (const m of matches) (byDay[m.date] ||= []).push(m);
  const days = Object.keys(byDay).sort();

  // 当天及未来 → 主视图;历史(date < 今天)→ 折叠区(默认收起)
  const today = new Date().toLocaleDateString('en-CA');
  const futureDays = days.filter(d => d >= today);
  const pastDays = days.filter(d => d < today);

  const animClass = firstRender ? '' : ' no-anim';
  let html = `<div class="schedule-list${animClass}">`;
  let cardIdx = 0;
  const renderDayBlock = (day) => {
    const f = fmtDate(day);
    let h = `<div class="day-block">`;
    h += `<div class="day-head"><span class="day-date">${f.label}</span>`
       + `<span class="day-weekday">${f.weekday}</span>`
       + `<span class="day-count">${byDay[day].length} 场</span></div>`;
    for (const m of byDay[day]) {
      const changed = prevScoreMap[matchKey(m)] !== undefined
                      && prevScoreMap[matchKey(m)] !== scoreSig(m);
      const flashCls = changed ? ' flash' : '';
      const delay = firstRender ? ` style="animation-delay:${Math.min(cardIdx * 35, 800)}ms"` : '';
      h += renderCard(m, flashCls, delay);
      cardIdx++;
    }
    return h + `</div>`;
  };

  // 历史战绩(顶部:新用户首先看到系统能力,带命中率摘要)
  if (pastDays.length) {
    let fin = 0, winHit = 0, scoreHit = 0;
    for (const d of pastDays) for (const m of byDay[d]) {
      if (m.status === 'finished' && m.score && m.probs) {
        fin++;
        if (probHit(m.probs, m.result)) winHit++;
        const ps = m.top_score;
        if (ps && ps[0] === m.score[0] && ps[1] === m.score[1]) scoreHit++;
      }
    }
    const histCount = pastDays.reduce((n, d) => n + byDay[d].length, 0);
    const bodyStyle = histExpanded ? '' : ' style="display:none"';
    const label = histExpanded ? '收起历史预测' : '查看预测明细';
    const arrow = histExpanded ? '▴' : '▾';
    const winPct = fin ? Math.round(winHit / fin * 100) : 0;
    html += `<div class="hist-fold hist-top">`
         + `<button class="hist-toggle" type="button">`
         + `<span class="hist-title">过往战绩</span>`
         + `<span class="hist-stats">已打 ${fin} 场 · 胜负命中 <b>${winHit}</b>(${winPct}%) · 比分精准 <b>${scoreHit}</b></span>`
         + `<span class="hist-label">${label}</span><span class="hist-arrow">${arrow}</span>`
         + `</button>`
         + `<div class="hist-body"${bodyStyle}>`;
    for (const day of pastDays) html += renderDayBlock(day);
    html += `</div></div>`;
  }

  for (const day of futureDays) html += renderDayBlock(day);

  html += `</div>`;
  list.innerHTML = html;

  // 更新变化检测表
  for (const m of matches) prevScoreMap[matchKey(m)] = scoreSig(m);
  firstRender = false;
}

function renderCard(m, flashCls, delayStyle) {
  const stageZh = STAGE_ZH[m.stage] || m.stage;
  const groupTag = m.group ? `${stageZh} · ${m.group}组` : stageZh;
  const homeTeam = teamBlock(m.home, m.home_zh, m.elo_h, 'home');
  const awayTeam = teamBlock(m.away, m.away_zh, m.elo_a, 'away');
  const center = renderCenter(m);
  return `<article class="match-card${flashCls}" data-status="${m.status}"`
    + ` data-date="${esc(m.date)}" data-home="${esc(m.home)}" data-away="${esc(m.away)}"${delayStyle}>`
    + `<div class="match-meta"><span class="stage-tag">${esc(groupTag)}</span>${m.kickoff_bj ? '<span class="ko-tag" title="北京时间开球">北京 ' + esc(m.kickoff_bj) + '</span>' : ''}`
    + `<span class="city-tag">${esc(m.city || '')}</span></div>`
    + `${homeTeam}${center}${awayTeam}</article>`;
}

function teamBlock(en, zh, elo, side) {
  return `<div class="team ${side}">`
    + (side === 'home' ? `<div class="name-block"><div class="name">${esc(zh)}</div><div class="elo">Elo ${elo ?? '-'}</div></div><span class="flag">${flag(en)}</span>`
                        : `<span class="flag">${flag(en)}</span><div class="name-block"><div class="name">${esc(zh)}</div><div class="elo">Elo ${elo ?? '-'}</div></div>`)
    + `</div>`;
}

// 48 方块概率条(官方 48 单元 DNA - 48 个参赛国)+ 三态百分比标签
// 三重编码(色弱/快读冗余):① 三档金明度 ② 方块填充数 ③ 数字
// prefix='drawer' 用抽屉尺寸/精度,decimals 控制百分比小数位
function probCells(probs, prefix, decimals, asOdds) {
  const [ph, pd] = probs;
  const TOTAL = 48;
  const nHome = Math.round(ph * TOTAL);
  const nDraw = Math.round(pd * TOTAL);
  let cells = '';
  for (let i = 0; i < TOTAL; i++) {
    let cls = 'away';
    if (i < nHome) cls = 'home';
    else if (i < nHome + nDraw) cls = 'draw';
    cells += `<i class="cell ${cls}"></i>`;
  }
  const barCls = prefix ? `${prefix}-probbar` : 'probbar';
  const lblCls = prefix ? `${prefix}-problabels` : 'problabels';
  const fmt = (v) => {
    if (asOdds) {
      const odds = v > 0 ? (1 / v).toFixed(2) : '-';
      return prefix === 'drawer' ? odds : `赔率${odds}`;
    }
    return decimals ? (v * 100).toFixed(decimals) : Math.round(v * 100);
  };
  const lbl = (cls, key, val) =>
    `<span class="pl ${cls}"><span class="pk">${key}</span><span class="pv">${fmt(val)}</span></span>`;
  return `<div class="${barCls}">${cells}</div>`
    + `<div class="${lblCls}">${lbl('home','主',ph)}${lbl('draw','平',pd)}${lbl('away','客',probs[2])}</div>`;
}

// 命中判定:三态 argmax 是否等于实际结果
function probHit(probs, result) {
  const [ph, pd, pa] = probs;
  const argmax = (ph >= pd && ph >= pa) ? 'home_win'
               : (pa >= pd && pa >= ph) ? 'away_win' : 'draw';
  return argmax === result;
}

// 概率来源徽标:odds=收盘报价 devig 后(更准),elo=Elo 推断
function srcBadge(m) {
  if (m.source === 'odds') return '<span class="src-badge odds" title="收盘报价 devig 后概率(108场回测 Δ=−0.066 优于 Elo)">📊报价</span>';
  return '<span class="src-badge elo" title="Elo 推断概率">Elo</span>';
}

function renderCenter(m) {
  if (m.status === 'finished' && m.score) {
    const [h, a] = m.score;
    const cls = h > a ? 'win-home' : (a > h ? 'win-away' : '');
    const resultLabel = RESULT_ZH[m.result] || '';
    // 有预测:真实比分 + 赛前预测(48方块) + 命中标记
    if (m.probs) {
      const hit = probHit(m.probs, m.result);
      const ps = m.top_score;
      const scoreHit = ps && ps[0] === h && ps[1] === a;
      const mark = scoreHit ? '比分精准' : (hit ? '胜负命中' : `实际${resultLabel}`);
      const markCls = scoreHit ? 'precise' : (hit ? 'hit' : 'miss');
      const predStr = ps ? `<span class="pred-score"><i>预测</i>${ps[0]}-${ps[1]}</span>` : '';
      return `<div class="center-cell">`
        + `<div class="score-big ${cls}">${h}<span class="dash">-</span>${a}</div>`
        + predStr
        + probCells(m.probs, '', 0, true)
        + `<div class="topscore"><span class="hitmark ${markCls}">${mark}</span>${srcBadge(m)}</div>`
        + `</div>`;
    }
    return `<div class="center-cell"><div class="score-big ${cls}">${h}<span class="dash">-</span>${a}</div>`
      + `<div class="topscore"><span class="tsval">${resultLabel}</span></div></div>`;
  }
  if (!m.probs) {
    return `<div class="center-cell"><span class="no-elo">Elo 缺失</span></div>`;
  }
  const ts = m.top_score ? `预测 ${m.top_score[0]}-${m.top_score[1]}` : '';
  return `<div class="center-cell">`
    + probCells(m.probs, '', 0, true)
    + `<div class="topscore">${ts}${srcBadge(m)}</div></div>`;
}

// ── 夺冠榜渲染 ────────────────────────────────────
function renderChampions() {
  const list = $('champList');
  const ranking = cache.champions || [];
  if (!ranking.length) {
    list.innerHTML = '<div class="placeholder">夺冠榜计算中(蒙特卡洛 10000 次,约 1-2 分钟)…</div>';
    return;
  }
  const top = ranking[0].prob;
  let html = '<div class="champ-list">';
  ranking.forEach((c, i) => {
    const rankCls = i === 0 ? 'top1' : i === 1 ? 'top2' : i === 2 ? 'top3' : '';
    const widthPct = (c.prob / top * 100).toFixed(1);
    const delay = ` style="animation-delay:${i * 30}ms"`;
    html += `<div class="champ-row ${rankCls}"${delay}>`
      + `<span class="rank">${i + 1}</span>`
      + `<span class="flag">${flag(c.team)}</span>`
      + `<span class="cname">${esc(c.team_zh)}</span>`
      + `<div class="champ-bar"><div class="champ-bar-fill" style="width:${widthPct}%"></div></div>`
      + `<span class="champ-pct">${(c.prob * 100).toFixed(2)}%</span></div>`;
  });
  html += '</div>';
  list.innerHTML = html;
  $('champBuiltAt').textContent = cache.status?.champions_built_at
    ? `更新于 ${cache.status.champions_built_at.slice(11, 16)}` : '10,000 次模拟';
}

// ── 小组榜渲染 ────────────────────────────────────
function renderGroups() {
  const grid = $('groupsGrid');
  const groups = cache.groups || {};
  const keys = Object.keys(groups).sort();
  if (!keys.length) { grid.innerHTML = '<div class="placeholder">载入小组榜…</div>'; return; }
  let html = '';
  for (const g of keys) {
    const rows = groups[g];
    html += `<div class="group-card"><div class="group-card-head">`
      + `<span class="group-letter">${g}</span><span class="group-label">GROUP ${g}</span></div>`
      + `<table class="group-table"><thead><tr>`
      + `<th>队</th><th>已打</th><th>积分</th><th>期望</th><th>净胜</th></tr></thead><tbody>`;
    for (const r of rows) {
      html += `<tr><td><div class="team-cell"><span class="tflag">${flag(r.team)}</span>`
        + `<span class="tname">${esc(r.team_zh)}</span></div></td>`
        + `<td>${r.played}</td><td class="pts-cell">${r.pts}</td>`
        + `<td class="exp">${r.exp_pts.toFixed(1)}</td>`
        + `<td class="pts-cell">${r.gd > 0 ? '+' : ''}${r.gd}</td></tr>`;
    }
    html += `</tbody></table></div>`;
  }
  grid.innerHTML = html;
}

// ── 单场详情抽屉 ──────────────────────────────────
// ── 历史回测渲染(2018/2022 赛前 Elo vs 真实结果)──────────
function renderBacktest() {
  const wrap = $('backtestList');
  const data = cache.backtest;
  if (!data || !data.tournaments) {
    wrap.innerHTML = '<div class="placeholder">回测计算中(赛前 Elo 重建,约 10 秒)…</div>';
    return;
  }
  let html = '<div class="bt-wrap">';
  for (const t of data.tournaments) {
    const rate = (t.hit_rate != null) ? (t.hit_rate * 100).toFixed(1) : '-';
    const sRate = (t.score_hit_rate != null) ? (t.score_hit_rate * 100).toFixed(1) : '-';
    html += '<section class="bt-block">'
      + '<div class="bt-head">'
      + `<span class="bt-name">${esc(t.name)}</span>`
      + `<span class="bt-rate">${rate}<small>%</small><i>胜负</i></span>`
      + `<span class="bt-subrate">精确比分 <b>${sRate}%</b> <small>${t.score_hits}/${t.counted}</small></span>`
      + `<span class="bt-meta">共 ${t.count} 场</span>`
      + '</div><div class="bt-matches">';
    for (const m of t.matches) {
      const [h, a] = m.score;
      const resZh = RESULT_ZH[m.result] || '';
      const hitCls = m.hit === true ? 'ok' : (m.hit === false ? 'no' : 'na');
      const ps = m.pred_score;
      const scoreHit = m.score_hit === true;
      const mark = scoreHit ? '★ 精确'
        : (m.hit === true ? '✓ 胜负'
        : (m.hit === false ? '✗ ' + resZh : '—'));
      const markCls = scoreHit ? 'exact' : hitCls;
      const psHtml = ps ? `<span class="bt-pscore ${scoreHit?'exact':''}">${ps[0]}-${ps[1]}</span>` : '<span class="bt-pscore">—</span>';
      const probs = m.probs
        ? `<span class="bt-probs">主${Math.round(m.probs[0]*100)}·平${Math.round(m.probs[1]*100)}·客${Math.round(m.probs[2]*100)} ${srcBadge(m)}</span>`
        : '<span class="bt-probs">Elo 缺失</span>';
      html += `<div class="bt-row ${hitCls}">`
        + `<span class="bt-date">${esc(m.date.slice(5))}</span>`
        + `<span class="bt-team">${flag(m.home)} ${esc(m.home_zh)}</span>`
        + `<span class="bt-scoreline">预${psHtml}<span class="bt-real">真${h}-${a}</span></span>`
        + `<span class="bt-team right">${esc(m.away_zh)} ${flag(m.away)}</span>`
        + probs
        + `<span class="bt-mark ${markCls}">${mark}</span></div>`;
    }
    html += '</div></section>';
  }
  html += '</div>';
  wrap.innerHTML = html;
}

// ── 数据来源面板渲染 ──────────────────────────────
function renderMarket() {
  const d = cache.market;
  if (!d) return;
  const s = d.summary;
  const pct = n => s.total ? (n / s.total * 100).toFixed(0) : 0;
  $('mktSummary').innerHTML =
    `共 ${s.total} 场 · ` +
    `<b class="mkt-tag mkt-a">A</b> 仅1X2反演 ${s.mode_a} 场(${pct(s.mode_a)}%) · ` +
    `<b class="mkt-tag mkt-e">Elo</b> 纯 Elo ${s.elo} 场(${pct(s.elo)}%) · ` +
    (s.mode_b ? `<b class="mkt-tag mkt-b">B</b> 1X2+OU反演 ${s.mode_b} 场 · ` : '');
  $('vbNote') && ($('vbNote').textContent = '');  // noop safety
  const tagHtml = m => `<b class="mkt-tag mkt-${m.toLowerCase()}">${m}</b>`;
  const tri = p => p ? `主${pct2(p[0])} 平${pct2(p[1])} 客${pct2(p[2])}` : '—';
  const pct2 = x => (x * 100).toFixed(0) + '%';
  const rows = d.matches.map(m => {
    if (m.mode === 'Elo') {
      return `<div class="mkt-row elo">
        <span class="mkt-tag-wrap">${tagHtml(m.mode)}</span>
        <span class="mkt-match">${m.home_zh} vs ${m.away_zh}</span>
        <span class="mkt-src">无市场报价,Elo 推断</span>
        <span class="mkt-prob">${tri(m.prob_elo)}</span>
      </div>`;
    }
    const src = m.mode === 'B'
      ? `1X2 ${m.odds_1x2.join('/')} + OU${m.ou_line} ${(m.ou||[]).join('/')}`
      : `1X2 ${m.odds_1x2.join('/')}`;
    return `<div class="mkt-row inv">
      <span class="mkt-tag-wrap">${tagHtml(m.mode)}</span>
      <span class="mkt-match">${m.home_zh} vs ${m.away_zh}</span>
      <span class="mkt-src">${esc(src)}</span>
      <span class="mkt-prob"><span class="mkt-pinv">反演 ${tri(m.prob_inverted)}</span><span class="mkt-pelo">Elo ${tri(m.prob_elo)}</span></span>
    </div>`;
  });
  $('mktList').innerHTML = rows.join('');
}

async function openDrawer(date, home, away) {
  $('drawerTitle').textContent = '比赛详情';
  $('drawerBody').innerHTML = '<div class="placeholder">载入…</div>';
  $('drawer').classList.add('open');
  $('drawerScrim').classList.add('open');
  $('drawer').setAttribute('aria-hidden', 'false');
  // 反思开关状态(localStorage 持久化,默认关)
  const storeKey = `wc_reflect_${date}_${home}_${away}`;
  let refl = false;
  try { refl = localStorage.getItem(storeKey) === '1'; } catch {}
  try {
    const url = `api/match?date=${encodeURIComponent(date)}&home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}`
      + (refl ? '&with_reflection=1' : '');
    const m = await getJSON(url);
    renderDrawer(m);
  } catch (e) {
    $('drawerBody').innerHTML = `<div class="placeholder">加载失败: ${esc(e.message)}</div>`;
  }
}
function renderValue() {
  const d = cache.value;
  if (!d) return;
  // 顶部警示
  $('vbNote').textContent = d.calibration_note || '';
  // 分布摘要
  const dist = d.edge_distribution;
  if (dist && dist.n) {
    const pct = x => (x * 100).toFixed(1) + '%';
    $('vbSummary').innerHTML =
      `<div class="vb-stat">扫描 <b>${dist.n}</b> 场(max edge)</div>` +
      `<div class="vb-stat">平均 <b>${pct(dist.mean)}</b> · 中位 <b>${pct(dist.median)}</b> · 最大 <b>${pct(dist.max)}</b></div>` +
      `<div class="vb-stat">阈值 ≥1% ${dist.thresholds['1%']} 场 · ≥3% ${dist.thresholds['3%']} 场 · ≥5% ${dist.thresholds['5%']} 场 · ≥10% ${dist.thresholds['10%']} 场</div>` +
      `<div class="vb-stat vb-warn">⚠ ${d.n_value_bets} 场 ≥3% edge,但大多在冷门队(Elo 数据稀疏),实际不可信</div>`;
  } else {
    $('vbSummary').innerHTML = '<div class="placeholder">无 odds 数据,稍后再试</div>';
  }
  // Top 20 表格
  const fmt = x => (x * 100).toFixed(0) + '%';
  const edgeClass = e => e >= 0.10 ? 'edge-huge' : e >= 0.05 ? 'edge-strong' : 'edge-weak';
  const rows = (d.upcoming || []).map(v => {
    const elo = v.p_elo.map(fmt).join('/');
    const mkt = v.p_mkt.map(fmt).join('/');
    return `<div class="vb-row ${edgeClass(v.max_edge)}">
      <span class="vb-date">${v.date}</span>
      <span class="vb-match">${v.home_zh} vs ${v.away_zh}</span>
      <span class="vb-elo">Elo ${elo}</span>
      <span class="vb-mkt">市场 ${mkt}</span>
      <span class="vb-edge">${(v.max_edge*100).toFixed(1)}%</span>
      <span class="vb-pick"><b>${v.picked}</b> @ ${v.odds.toFixed(2)}</span>
      <span class="vb-kelly">Kelly ${(v.kelly*100).toFixed(1)}%</span>
    </div>`;
  }).join('');
  $('vbList').innerHTML = rows || '<div class="placeholder">未赛 ≥3% 价值机会 0 场</div>';
}

function closeDrawer() {
  $('drawer').classList.remove('open');
  $('drawerScrim').classList.remove('open');
  $('drawer').setAttribute('aria-hidden', 'true');
}
function renderDrawer(m) {
  const stageZh = STAGE_ZH[m.stage] || m.stage;
  let html = `<div class="drawer-section"><h4>${esc(stageZh)} · ${esc(m.city || '')}</h4>`;
  html += `<div class="drawer-teams">`
    + `<div class="drawer-team"><span class="flag">${flag(m.home)}</span><span class="name">${esc(m.home_zh)}</span><span class="elo">Elo ${m.elo_h ?? '-'}</span></div>`
    + `<div class="drawer-vs">VS</div>`
    + `<div class="drawer-team"><span class="flag">${flag(m.away)}</span><span class="name">${esc(m.away_zh)}</span><span class="elo">Elo ${m.elo_a ?? '-'}</span></div>`
    + `</div></div>`;

  if (m.status === 'finished' && m.score) {
    html += `<div class="drawer-section"><h4>最终比分</h4>`
      + `<div class="scoreline"><span class="num">${m.score[0]}</span><span class="num dash">:</span><span class="num">${m.score[1]}</span></div>`
      + `<div class="stat-row"><span class="k">结果</span><span class="v">${esc(RESULT_ZH[m.result] || '')}</span></div></div>`;
  }
  if (m.probs) {
    html += `<div class="drawer-section"><h4>三态概率</h4>`
      + probCells(m.probs, 'drawer', 1)
      + `</div>`;
  }
  if (m.lam_h != null) {
    html += `<div class="drawer-section"><h4>模型参数</h4>`
      + `<div class="stat-row"><span class="k">主队预期进球 λ</span><span class="v">${m.lam_h}</span></div>`
      + `<div class="stat-row"><span class="k">客队预期进球 λ</span><span class="v">${m.lam_a}</span></div>`
      + `<div class="stat-row"><span class="k">主场加成</span><span class="v">${m.host_adv ? '+' + m.host_adv : '中立'}</span></div></div>`;
  }
  if (m.mode) {
    const MODE_DESC = {B:'赔率 + 大小球(最准)', A:'仅胜负赔率', Elo:'历史实力分(无赔率)'};
    const MODE_LABEL = {B:'B', A:'A', Elo:'Elo'};
    html += `<div class="drawer-section"><h4>数据来源</h4>`
      + `<div class="stat-row"><span class="k">预测依据</span><span class="v src-mode">`
      + `<b class="mkt-tag mkt-${m.mode.toLowerCase()}">${MODE_LABEL[m.mode]}</b> ${esc(MODE_DESC[m.mode])}</span></div>`;
    if (m.odds_1x2) {
      html += `<div class="stat-row"><span class="k">BetExplorer 胜负赔率</span><span class="v mono">${m.odds_1x2.join(' / ')}</span></div>`;
    }
    if (m.ou) {
      html += `<div class="stat-row"><span class="k">BetExplorer 大小球赔率</span><span class="v mono">≥3 球 ${m.ou[0]} / ≤2 球 ${m.ou[1]}</span></div>`;
    }
    if (m.lam_inverted && m.lam_elo) {
      html += `<div class="stat-row"><span class="k">市场推算 预期进球</span><span class="v mono">主队 ${m.lam_inverted[0]} / 客队 ${m.lam_inverted[1]}</span></div>`
        + `<div class="stat-row"><span class="k">实力分推算 预期进球</span><span class="v mono dim">主队 ${m.lam_elo[0]} / 客队 ${m.lam_elo[1]}</span></div>`;
    } else if (m.lam_elo) {
      html += `<div class="stat-row"><span class="k">实力分推算 预期进球</span><span class="v mono">主队 ${m.lam_elo[0]} / 客队 ${m.lam_elo[1]}</span></div>`;
    }
    html += `</div>`;
  }
  if (m.top5 && m.top5.length) {
    const maxp = m.top5[0][2];
    html += `<div class="drawer-section"><h4>Top5 比分概率</h4><div class="top5-list">`;
    for (const [h, a, p] of m.top5) {
      html += `<div class="top5-row"><span class="sc">${h}-${a}</span>`
        + `<div class="bar5"><div class="fill5" style="width:${p / maxp * 100}%"></div></div>`
        + `<span class="p5">${(p * 100).toFixed(2)}%</span></div>`;
    }
    html += `</div></div>`;
  }
  // AI 反思(实验):默认关,toggle 切换会重拉 with_reflection=1 重算预测
  let reflectStoreKey = null;
  let reflectStored = false;
  if (m.reflection && m.reflection.has_any) {
    const r = m.reflection;
    reflectStoreKey = `wc_reflect_${m.date}_${m.home}_${m.away}`;
    try { reflectStored = localStorage.getItem(reflectStoreKey) === '1'; } catch {}
    const adj = m.reflection_adj || {home: 0, away: 0};
    const modeBadge = reflectStored
      ? `<span class="ref-mode-badge on">反思预测 主 ${adj.home >= 0 ? '+' : ''}${adj.home} / 客 ${adj.away >= 0 ? '+' : ''}${adj.away}</span>`
      : `<span class="ref-mode-badge off">基础预测</span>`;
    html += `<div class="drawer-section reflection-section">`
      + `<div class="reflection-head">`
      + `<h4>AI 反思 <span class="badge-exp">实验</span></h4>`
      + `<label class="switch" title="开启后用队伍历史特征调整 Elo 重算预测(市场驱动场 probs 不变)">`
      + `<input type="checkbox" id="reflectToggle">`
      + `<span class="slider"></span></label></div>`
      + `<div class="reflection-mode-line">${modeBadge}</div>`
      + `<div class="reflection-summary">${esc(r.summary || '')}</div>`
      + `<div class="reflection-body">`;
    if (r.home_archive) html += renderTeamArchive(r.home_archive, 'home');
    if (r.away_archive) html += renderTeamArchive(r.away_archive, 'away');
    html += `</div></div>`;
  }
  // 赛前情报(web search):默认仅按钮,点击才调 LLM
  html += `<div class="drawer-section intel-section">`
    + `<div class="intel-head">`
    + `<h4>赛前情报 <span class="badge-ws">联网搜索</span></h4>`
    + `<button class="intel-btn" id="intelBtn">获取赛前情报</button></div>`
    + `<div id="intelBody" class="intel-body"></div></div>`;
  // 历史交手(web search):默认仅按钮,点击才调 LLM
  html += `<div class="drawer-section h2h-section">`
    + `<div class="h2h-head">`
    + `<h4>历史交手 <span class="badge-ws">联网搜索</span></h4>`
    + `<button class="h2h-btn" id="h2hBtn">查询历史交手</button></div>`
    + `<div id="h2hBody" class="h2h-body"></div></div>`;
  $('drawerBody').innerHTML = html;
  // 反思 toggle:写 localStorage + 重拉 API(带 with_reflection=1)
  const toggle = $('reflectToggle');
  if (toggle && reflectStoreKey) {
    toggle.checked = reflectStored;
    toggle.addEventListener('change', () => {
      try { localStorage.setItem(reflectStoreKey, toggle.checked ? '1' : '0'); } catch {}
      openDrawer(m.date, m.home, m.away);
    });
  }
  // 赛前情报按钮:点击触发 web search
  const intelBtn = $('intelBtn');
  if (intelBtn) {
    intelBtn.addEventListener('click', () => fetchIntel(m.date, m.home, m.away));
  }
  // 历史交手按钮:点击触发 web search
  const h2hBtn = $('h2hBtn');
  if (h2hBtn) {
    h2hBtn.addEventListener('click', () => fetchH2H(m.date, m.home, m.away));
  }
}

// ── 赛前情报(web search)─────────────────────────
async function fetchIntel(date, home, away) {
  const btn = $('intelBtn');
  const body = $('intelBody');
  if (btn) { btn.disabled = true; btn.textContent = '搜索中…(约 5 秒)'; }
  if (body) body.innerHTML = '<div class="placeholder">智谱 GLM-4 联网搜索赛前情报…</div>';
  try {
    const url = `api/match?date=${encodeURIComponent(date)}&home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}&with_intel=1`;
    const m = await getJSON(url);
    renderIntel(m.intel);
  } catch (e) {
    if (body) body.innerHTML = `<div class="placeholder">加载失败: ${esc(e.message)}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '重新获取赛前情报'; }
  }
}

function renderIntel(intel) {
  const body = $('intelBody');
  if (!body) return;
  if (!intel) {
    body.innerHTML = '<div class="placeholder">无情报数据</div>';
    return;
  }
  if (intel.error) {
    body.innerHTML = `<div class="placeholder">✗ ${esc(intel.error)}</div>`;
    return;
  }
  const CONF = {high:'高可信', medium:'中可信', low:'低可信'};
  const IMPACT = {high:'重大影响', medium:'中等', low:'轻微'};
  let h = '';
  // 伤停
  if (intel.injuries && intel.injuries.length) {
    h += '<div class="intel-subhead">伤停 / 缺阵</div><ul class="intel-list">';
    for (const inj of intel.injuries) {
      const cls = inj.impact || 'medium';
      h += `<li class="intel-inj"><span class="intel-team">${esc(inj.team === intel._home ? '主' : '客')}</span>`
        + `<span class="intel-player">${esc(inj.player)}</span>`
        + `<span class="intel-reason">${esc(inj.reason || '')}</span>`
        + `<span class="intel-impact impact-${esc(cls)}">${esc(IMPACT[cls] || cls)}</span></li>`;
    }
    h += '</ul>';
  }
  // 近期战绩
  if (intel.form_home || intel.form_away) {
    h += '<div class="intel-grid">';
    if (intel.form_home) {
      h += `<div class="intel-form"><div class="intel-form-team">主队近期</div>`
        + `<div class="intel-form-wdl">${renderWDL(intel.form_home.recent5)}</div>`
        + `<div class="intel-form-note">${esc(intel.form_home.note || '')}</div></div>`;
    }
    if (intel.form_away) {
      h += `<div class="intel-form"><div class="intel-form-team">客队近期</div>`
        + `<div class="intel-form-wdl">${renderWDL(intel.form_away.recent5)}</div>`
        + `<div class="intel-form-note">${esc(intel.form_away.note || '')}</div></div>`;
    }
    h += '</div>';
  }
  // 战术要点
  if (intel.tactical_notes && intel.tactical_notes.length) {
    h += '<div class="intel-subhead">战术要点</div><ul class="intel-notes">';
    for (const n of intel.tactical_notes) h += `<li>${esc(n)}</li>`;
    h += '</ul>';
  }
  // 媒体关注
  if (intel.headline_concerns && intel.headline_concerns.length) {
    h += '<div class="intel-subhead">媒体关注</div><ul class="intel-notes">';
    for (const n of intel.headline_concerns) h += `<li>${esc(n)}</li>`;
    h += '</ul>';
  }
  // 预测暗示
  if (intel.prediction_hint) {
    h += `<div class="intel-hint">⚖ ${esc(intel.prediction_hint)}</div>`;
  }
  // 元信息
  h += `<div class="intel-meta">`
    + `<span class="conf-${esc(intel.confidence || 'low')}">${esc(CONF[intel.confidence] || '?')}</span>`
    + (intel.search_summary ? `<span class="intel-search-summary">${esc(intel.search_summary)}</span>` : '')
    + `</div>`;
  body.innerHTML = h;
}

function renderWDL(s) {
  if (!s) return '';
  return s.split('').map(c => {
    const m = {W: 'win', D: 'draw', L: 'loss'};
    return `<span class="wdl-${m[c] || 'draw'}">${c}</span>`;
  }).join('');
}

// ── 历史交手(web search)─────────────────────────
async function fetchH2H(date, home, away) {
  const btn = $('h2hBtn');
  const body = $('h2hBody');
  if (btn) { btn.disabled = true; btn.textContent = '搜索中…(约 5 秒)'; }
  if (body) body.innerHTML = '<div class="placeholder">智谱 GLM-4 联网搜索历史交手…</div>';
  try {
    const url = `api/match?date=${encodeURIComponent(date)}&home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}&with_h2h=1`;
    const m = await getJSON(url);
    renderH2H(m.h2h);
  } catch (e) {
    if (body) body.innerHTML = `<div class="placeholder">加载失败: ${esc(e.message)}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '重新查询历史交手'; }
  }
}

function renderH2H(h2h) {
  const body = $('h2hBody');
  if (!body) return;
  if (!h2h) {
    body.innerHTML = '<div class="placeholder">无交手数据</div>';
    return;
  }
  if (h2h.error) {
    body.innerHTML = `<div class="placeholder">✗ ${esc(h2h.error)}</div>`;
    return;
  }
  const CONF = {high:'高可信', medium:'中可信', low:'低可信'};
  let h = '';
  if (h2h.summary) {
    h += `<div class="h2h-summary">${esc(h2h.summary)}</div>`;
  }
  if (h2h.recent_trend) {
    h += `<div class="h2h-trend">📈 ${esc(h2h.recent_trend)}</div>`;
  }
  const matches = h2h.matches || [];
  if (matches.length) {
    h += '<div class="h2h-subhead">交锋明细</div><ul class="h2h-list">';
    for (const m of matches) {
      let score = '-';
      if (Array.isArray(m.score) && m.score.length >= 2) {
        score = `${m.score[0]}-${m.score[1]}`;
      } else if (typeof m.score === 'string') {
        const nums = m.score.match(/\d+/g);
        if (nums && nums.length >= 2) score = `${nums[0]}-${nums[1]}`;
      }
      h += `<li class="h2h-row">`
        + `<span class="h2h-date mono">${esc(m.date || '')}</span>`
        + `<span class="h2h-comp">${esc(m.competition || '')}</span>`
        + `<span class="h2h-score mono">${esc(score)}</span>`
        + `<span class="h2h-venue">${esc(m.venue || '')}</span>`
        + `</li>`;
    }
    h += '</ul>';
  }
  h += `<div class="h2h-meta">`
    + `<span class="conf-${esc(h2h.confidence || 'low')}">${esc(CONF[h2h.confidence] || '?')}</span>`
    + `</div>`;
  body.innerHTML = h || '<div class="placeholder">无交手数据</div>';
}

// ── 队伍档案渲染(反思系统子模块) ─────────────────
function renderTeamArchive(arch, side) {
  const CONF = {high:'高可信', medium:'中可信', low:'低可信'};
  let h = `<div class="ref-team ref-${side}">`
    + `<div class="ref-team-head"><span class="ref-team-flag">${flag(arch.team || arch.name)}</span>`
    + `<span class="ref-team-name">${esc(arch.name_zh || arch.team || arch.name)}</span></div>`;
  const adj = arch.elo_adjustments || [];
  if (adj.length) {
    h += `<div class="ref-subhead">长期特征(可迁移)</div>`;
    for (const a of adj) {
      const sign = a.value > 0 ? '+' : '';
      h += `<div class="ref-adj">`
        + `<span class="ref-adj-key mono">${esc(a.key)}</span>`
        + `<span class="ref-adj-val">${sign}${a.value}</span>`
        + `<span class="ref-adj-conf conf-${esc(a.confidence)}">${esc(CONF[a.confidence] || '?')}</span>`
        + `<span class="ref-adj-note">${esc(a.note || '')}</span></div>`;
    }
  }
  const les = arch.recent_lessons || [];
  if (les.length) {
    h += `<div class="ref-subhead">历史错场归因</div>`;
    for (const l of les) {
      h += `<div class="ref-lesson ${l.reusable ? 'reusable' : 'oneshot'}">`
        + `<div class="ref-lesson-head">`
        + `<span class="ref-lesson-date mono">${esc(l.date)}</span>`
        + `<span class="ref-lesson-vs">vs ${esc(l.vs)} <small>${esc(l.tournament || '')}</small></span>`
        + `<span class="ref-lesson-score mono">预 ${esc(l.prediction)} / 真 ${esc(l.actual)}</span>`
        + `<span class="ref-lesson-tag ${l.reusable ? 'tag-reusable' : 'tag-oneshot'}">${l.reusable ? '✓ 可迁移' : '一次性'}</span>`
        + `</div><ul class="ref-causes">`;
      for (const c of (l.causes || [])) h += `<li>${esc(c)}</li>`;
      h += `</ul></div>`;
    }
  }
  h += `</div>`;
  return h;
}

// ── 轮询主循环 ────────────────────────────────────
async function poll() {
  try {
    const [sched, st] = await Promise.all([
      getJSON('api/schedule'),
      getJSON('api/status'),
    ]);
    cache.schedule = sched.matches;
    cache.status = st;
    renderStatus();
    renderSchedule();
    if ($('panel-champions').classList.contains('active')) {
      cache.champions = (await getJSON('api/champions?top=20')).ranking;
      renderChampions();
    }
    if ($('panel-groups').classList.contains('active')) {
      cache.groups = await getJSON('api/groups');
      renderGroups();
    }
  } catch (e) {
    console.warn('poll 失败(保留旧数据):', e);
    const lbl = $('statusLabel'); lbl.textContent = '连接断开(用缓存)';
    $('liveDot').className = 'live-dot err';
  }
}

function tick() {
  countdown--;
  if (countdown <= 0) {
    countdown = pollSec;
    poll();
  }
  $('countdown').textContent = countdown + 's';
  $('footStamp').textContent = new Date().toTimeString().slice(0, 8);
}

// ── 事件绑定 ──────────────────────────────────────
function bindEvents() {
  // tab 切换
  document.querySelectorAll('.tab').forEach(t => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      t.classList.add('active');
      $('panel-' + t.dataset.tab).classList.add('active');
      // 切到夺冠/小组时立即拉一次
      if (t.dataset.tab === 'champions') {
        getJSON('api/champions?top=20').then(d => { cache.champions = d.ranking; renderChampions(); }).catch(()=>{});
      } else if (t.dataset.tab === 'groups') {
        getJSON('api/groups').then(d => { cache.groups = d; renderGroups(); }).catch(()=>{});
      } else if (t.dataset.tab === 'backtest') {
        if (!cache.backtest) {
          getJSON('api/backtest').then(d => { cache.backtest = d; renderBacktest(); }).catch(()=>{});
        } else { renderBacktest(); }
      } else if (t.dataset.tab === 'market') {
        if (!cache.market) {
          getJSON('api/market').then(d => { cache.market = d; renderMarket(); }).catch(()=>{});
        } else { renderMarket(); }
      } else if (t.dataset.tab === 'value') {
        if (!cache.value) {
          getJSON('api/value-bets?min_edge=0.03').then(d => { cache.value = d; renderValue(); }).catch(()=>{});
        } else { renderValue(); }
      }
    });
  });
  // 阶段筛选
  $('stageFilter').addEventListener('click', e => {
    const b = e.target.closest('.chip'); if (!b) return;
    document.querySelectorAll('#stageFilter .chip').forEach(c => c.classList.remove('active'));
    b.classList.add('active');
    activeStage = b.dataset.stage;
    firstRender = true;   // 筛选切换重新带动画
    renderSchedule();
  });
  // 手动刷新
  $('refreshBtn').addEventListener('click', async () => {
    const btn = $('refreshBtn');
    btn.classList.add('spinning');
    btn.disabled = true;
    try {
      await fetch('api/refresh', {method: 'POST'});
      countdown = 3;   // 稍等后台处理
    } catch (e) { console.warn(e); }
    setTimeout(() => { btn.classList.remove('spinning'); btn.disabled = false; }, 2500);
  });
  // 卡片点击 → 抽屉(事件委托)
  $('scheduleList').addEventListener('click', e => {
    const btn = e.target.closest('.hist-toggle');
    if (btn) { histExpanded = !histExpanded; renderSchedule(); return; }
    const card = e.target.closest('.match-card'); if (!card) return;
    openDrawer(card.dataset.date, card.dataset.home, card.dataset.away);
  });
  // 抽屉关闭
  $('drawerClose').addEventListener('click', closeDrawer);
  $('drawerScrim').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });
}

// ── 启动 ──────────────────────────────────────────
async function start() {
  bindEvents();
  try { const cfg = await getJSON('api/config'); pollSec = cfg.poll_sec || 30; } catch {}
  countdown = pollSec;
  await poll();
  tickTimer = setInterval(tick, 1000);
}
start();
