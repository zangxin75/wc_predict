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
let cache = { schedule:[], status:{}, champions:[], groups:{} };

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

  const animClass = firstRender ? '' : ' no-anim';
  let html = `<div class="schedule-list${animClass}">`;
  let cardIdx = 0;
  for (const day of days) {
    const f = fmtDate(day);
    html += `<div class="day-block">`;
    html += `<div class="day-head"><span class="day-date">${f.label}</span>`
         + `<span class="day-weekday">${f.weekday}</span>`
         + `<span class="day-count">${byDay[day].length} 场</span></div>`;
    for (const m of byDay[day]) {
      const changed = prevScoreMap[matchKey(m)] !== undefined
                      && prevScoreMap[matchKey(m)] !== scoreSig(m);
      const flashCls = changed ? ' flash' : '';
      const delay = firstRender ? ` style="animation-delay:${Math.min(cardIdx * 35, 800)}ms"` : '';
      html += renderCard(m, flashCls, delay);
      cardIdx++;
    }
    html += `</div>`;
  }
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
    + `<div class="match-meta"><span class="stage-tag">${esc(groupTag)}</span>`
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
function probCells(probs, prefix, decimals) {
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
  const fmt = (v) => decimals ? (v * 100).toFixed(decimals) : Math.round(v * 100);
  const lbl = (cls, key, val) =>
    `<span class="pl ${cls}"><span class="pk">${key}</span><span class="pv">${fmt(val)}</span></span>`;
  return `<div class="${barCls}">${cells}</div>`
    + `<div class="${lblCls}">${lbl('home','主',ph)}${lbl('draw','平',pd)}${lbl('away','客',probs[2])}</div>`;
}

function renderCenter(m) {
  if (m.status === 'finished' && m.score) {
    const [h, a] = m.score;
    const cls = h > a ? 'win-home' : (a > h ? 'win-away' : '');
    return `<div class="center-cell"><div class="score-big ${cls}">${h}<span class="dash">-</span>${a}</div>`
      + `<div class="topscore"><span class="tsval">${RESULT_ZH[m.result] || ''}</span></div></div>`;
  }
  if (!m.probs) {
    return `<div class="center-cell"><span class="no-elo">Elo 缺失</span></div>`;
  }
  const ts = m.top_score ? `预测 ${m.top_score[0]}-${m.top_score[1]}` : '';
  return `<div class="center-cell">`
    + probCells(m.probs)
    + `<div class="topscore">${ts}</div></div>`;
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
async function openDrawer(date, home, away) {
  $('drawerTitle').textContent = '比赛详情';
  $('drawerBody').innerHTML = '<div class="placeholder">载入…</div>';
  $('drawer').classList.add('open');
  $('drawerScrim').classList.add('open');
  $('drawer').setAttribute('aria-hidden', 'false');
  try {
    const m = await getJSON(`/api/match?date=${encodeURIComponent(date)}&home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}`);
    renderDrawer(m);
  } catch (e) {
    $('drawerBody').innerHTML = `<div class="placeholder">加载失败: ${esc(e.message)}</div>`;
  }
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
  $('drawerBody').innerHTML = html;
}

// ── 轮询主循环 ────────────────────────────────────
async function poll() {
  try {
    const [sched, st] = await Promise.all([
      getJSON('/api/schedule'),
      getJSON('/api/status'),
    ]);
    cache.schedule = sched.matches;
    cache.status = st;
    renderStatus();
    renderSchedule();
    if ($('panel-champions').classList.contains('active')) {
      cache.champions = (await getJSON('/api/champions?top=20')).ranking;
      renderChampions();
    }
    if ($('panel-groups').classList.contains('active')) {
      cache.groups = await getJSON('/api/groups');
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
        getJSON('/api/champions?top=20').then(d => { cache.champions = d.ranking; renderChampions(); }).catch(()=>{});
      } else if (t.dataset.tab === 'groups') {
        getJSON('/api/groups').then(d => { cache.groups = d; renderGroups(); }).catch(()=>{});
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
      await fetch('/api/refresh', {method: 'POST'});
      countdown = 3;   // 稍等后台处理
    } catch (e) { console.warn(e); }
    setTimeout(() => { btn.classList.remove('spinning'); btn.disabled = false; }, 2500);
  });
  // 卡片点击 → 抽屉(事件委托)
  $('scheduleList').addEventListener('click', e => {
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
  try { const cfg = await getJSON('/api/config'); pollSec = cfg.poll_sec || 30; } catch {}
  countdown = pollSec;
  await poll();
  tickTimer = setInterval(tick, 1000);
}
start();
