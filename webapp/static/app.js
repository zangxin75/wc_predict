/* ============================================================
   2026 世界杯预测 - 前端逻辑
   轮询 /api/* → 渲染 → 比分变化琥珀脉冲
   多语言:所有 UI 文案走 t() 函数(由 i18n.js 提供)
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

// ── 状态 ──────────────────────────────────────────
let pollSec = 30;
let countdown = 30;
let tickTimer = null;
let prevScoreMap = {};      // key -> "h-a" 用于检测比分变化
let firstRender = true;     // 首次渲染(带 staggered 动画)
let activeStage = 'all';
let histExpanded = false;   // 历史赛果折叠区展开状态
let cache = { schedule:[], status:{}, champions:[], groups:{}, backtest:null, market:null, value:null };
let reflectOn = false;      // 抽屉内反思模式状态
// ── 订阅状态 ──────────────────────────────────────
let userEmail = '';         // localStorage wc_email
let isPaid = false;         // 缓存订阅状态
let subChecked = false;     // 是否已检查过
let _currentPricing = null; // /api/pricing 返回的动态报价(打开 modal 时拉)

// ── 工具 ──────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const t = I18N.t;
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const flag = (team) => FLAG[team] || '🏳️';
const matchKey = (m) => `${m.date}|${m.home}|${m.away}`;
const scoreSig = (m) => m.status === 'finished' ? `${m.score[0]}-${m.score[1]}` : (m.status);

// ── 取 t() 的阶段/星期/结果映射(动态,跟随语言)──────
const STAGE_KEY = { Group:'stage.group', R32:'stage.r32', R16:'stage.r16', QF:'stage.qf', SF:'stage.sf', Final:'stage.final' };
const STAGE_ZH = () => Object.fromEntries(Object.entries(STAGE_KEY).map(([k, v]) => [k, t(v)]));
const RESULT_KEY = { home_win:'result.home_win', draw:'result.draw', away_win:'result.away_win' };
const RESULT_ZH = () => Object.fromEntries(Object.entries(RESULT_KEY).map(([k, v]) => [k, t(v)]));
const WEEKDAY = () => [0,1,2,3,4,5,6].map(i => t(`weekday.${i}`));

// ── 队名本地化(按当前语言)──────────────────
const teamName = (m, side) => {
  if (!m) return '';
  if (I18N.current === 'en') return m[side];
  if (I18N.current === 'zh-CN') return m[side + '_zh'] || m[side];
  return I18N.localTeam(m[side]);
};
// ── 本地时区日期/时间(BJ → user tz)──────────
const localDate = (m) => m ? I18N.localDate(m.date, m.kickoff_bj) : '';
const localTime = (m) => m ? I18N.localTime(m.date, m.kickoff_bj) : '';

async function getJSON(url, opts = {}) {
  // F-02: credentials: 'include' 让浏览器带上 session cookie
  const r = await fetch(url, { cache: 'no-store', credentials: 'include', ...opts });
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

function fmtDate(iso) {
  const d = new Date(iso + 'T00:00:00');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return { label: `${mm}.${dd}`, weekday: WEEKDAY()[d.getDay()] };
}

// ── 订阅 (F-02 修复:从 localStorage email 改为服务端 session) ────────
// 保留 userEmail 变量以兼容旧调用点;新代码优先用 /api/whoami
function emailSuffix() {
  // 兼容保留(返回空串);后端会从 session cookie 自动读 email
  return '';
}

async function checkSubscription() {
  // F-02: 从服务端 /api/whoami 读当前 session 用户
  try {
    const d = await getJSON('api/whoami');
    userEmail = d.email || '';
    isPaid = !!d.is_paid;
    subChecked = true;
    const proBtn = $('proBtn');
    const proBadge = $('proBadge');
    if (isPaid) {
      if (proBtn) proBtn.hidden = true;
      if (proBadge) proBadge.hidden = false;
    } else {
      if (proBtn) proBtn.hidden = false;
      if (proBadge) proBadge.hidden = true;
    }
    return isPaid;
  } catch { return false; }
}

function initSubscription() {
  // F-02: 启动时先 call /api/whoami 同步 session 状态
  checkSubscription();
  // 检测 checkout=success 回跳 → 用 session_id 兑换 session cookie
  const params = new URLSearchParams(window.location.search);
  if (params.get('checkout') === 'success') {
    const sessionId = params.get('session_id');
    if (sessionId) {
      // POST /api/claim-session → 服务端查 Stripe, 写入 session, 下发 cookie
      getJSON(`api/claim-session?session_id=${encodeURIComponent(sessionId)}`, { method: 'POST' })
        .then(d => {
          if (d && d.ok) {
            userEmail = d.email;
            isPaid = !!d.is_paid;
            subChecked = true;
            const proBtn = $('proBtn'); if (proBtn) proBtn.hidden = isPaid;
            const proBadge = $('proBadge'); if (proBadge) proBadge.hidden = !isPaid;
            const s = $('upgradeStatus');
            if (s) { s.hidden = false; s.textContent = t('sub.unlocked'); s.className = 'upgrade-status ok'; }
          }
        })
        .catch(e => console.warn('[claim-session]', e));
    } else {
      // 旧版 success_url 不带 session_id: 走 fallback, 让用户重新登录
      console.warn('[checkout] no session_id in URL; please log in again');
    }
    window.history.replaceState({}, '', window.location.pathname);
  }
  // Pro 按钮事件
  const proBtn = $('proBtn');
  if (proBtn) proBtn.addEventListener('click', openUpgrade);
  bindUpgradeModal();
}

function bindUpgradeModal() {
  $('upgradeClose')?.addEventListener('click', closeUpgrade);
  $('upgradeScrim')?.addEventListener('click', closeUpgrade);
  $('planTournament')?.addEventListener('click', () => handleCheckout('tournament'));
  $('redeemInviteBtn')?.addEventListener('click', handleRedeemInvite);
  // 场次明细 折叠/展开
  $('matchListToggle')?.addEventListener('click', () => {
    const list = $('matchList');
    const tog = $('matchListToggle');
    if (!list || !tog) return;
    const willShow = list.hidden;
    list.hidden = !willShow;
    tog.setAttribute('aria-expanded', String(willShow));
  });
  // 价格过期后手动刷新
  $('pricingLockRefresh')?.addEventListener('click', () => loadPricing());
  const emailInput = $('upgradeEmail');
  if (emailInput) {
    if (userEmail) emailInput.value = userEmail;
    emailInput.addEventListener('input', () => { const e = $('upgradeError'); if (e) e.hidden = true; });
  }
  const inviteInput = $('inviteCode');
  if (inviteInput) {
    inviteInput.addEventListener('input', () => {
      inviteInput.value = inviteInput.value.toUpperCase();
      const e = $('upgradeError'); if (e) e.hidden = true;
    });
    inviteInput.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') { ev.preventDefault(); handleRedeemInvite(); }
    });
  }
  // 分享 X → 24h 邀请码
  bindSharePanel();
}


function bindSharePanel() {
  const tog = $('shareToggleBtn');
  const panel = $('sharePanel');
  if (!tog || !panel) return;
  tog.addEventListener('click', () => {
    const willShow = panel.hidden;
    panel.hidden = !willShow;
    tog.setAttribute('aria-expanded', String(willShow));
    if (willShow) {
      // 打开时填充模板(从 i18n 读 share.template)
      const tpl = $('shareTemplate');
      if (tpl && !tpl.value) {
        tpl.value = (typeof t === 'function') ? t('share.template') : '';
      }
    }
  });
  $('shareCopyBtn')?.addEventListener('click', async () => {
    const tpl = $('shareTemplate');
    if (!tpl) return;
    try {
      await navigator.clipboard.writeText(tpl.value);
      const btn = $('shareCopyBtn');
      if (btn) {
        const orig = btn.textContent;
        btn.textContent = (typeof t === 'function') ? t('share.copied') : 'Copied!';
        setTimeout(() => { btn.textContent = orig; }, 1500);
      }
    } catch (e) {
      tpl.select();
      document.execCommand && document.execCommand('copy');
    }
  });
  $('shareVerifyBtn')?.addEventListener('click', handleShareVerify);
  $('shareUrlInput')?.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); handleShareVerify(); }
  });
  $('shareUseCodeBtn')?.addEventListener('click', handleShareUseCode);
}


let _shareLatestCode = '';

async function handleShareVerify() {
  const urlInput = $('shareUrlInput');
  const errEl = $('upgradeError');
  const resultEl = $('shareResult');
  const codeEl = $('shareCodeDisplay');
  const labelEl = $('shareResultLabel');
  const useBtn = $('shareUseCodeBtn');
  const btn = $('shareVerifyBtn');
  if (!urlInput || !btn) return;
  if (errEl) errEl.hidden = true;
  const url = urlInput.value.trim();
  const email = $('upgradeEmail')?.value.trim() || userEmail || '';
  if (!url) {
    if (errEl) {
      errEl.textContent = (typeof t === 'function') ? t('share.error_empty_url') : 'Paste X URL';
      errEl.hidden = false;
    }
    return;
  }
  if (!email || !email.includes('@')) {
    if (errEl) {
      errEl.textContent = (typeof t === 'function') ? t('share.error_invalid_email') : 'Enter valid email';
      errEl.hidden = false;
    }
    return;
  }
  btn.disabled = true;
  try {
    const r = await fetch('api/share/verify?' + new URLSearchParams({
      platform: 'x', url, email,
    }).toString(), { method: 'POST' });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) {
      const detail = data.detail || 'verify_failed';
      const errKey = {
        invalid_email: 'share.error_invalid_email',
        invalid_x_url: 'share.error_invalid_url',
        unrecognized_url: 'share.error_unrecognized',
        platform_mismatch: 'share.error_unrecognized',
        http_404: 'share.error_verify_failed',
        rate_limited: 'share.error_rate_limited',
        empty_url: 'share.error_empty_url',
        network_error: 'share.error_network',
        timeout: 'share.error_network',
      }[detail] || 'share.error_verify_failed';
      if (errEl) {
        errEl.textContent = (typeof t === 'function') ? t(errKey) : detail;
        errEl.hidden = false;
      }
      return;
    }
    _shareLatestCode = data.code;
    if (codeEl) codeEl.textContent = data.code;
    if (useBtn) useBtn.hidden = false;
    if (resultEl) resultEl.hidden = false;
    if (labelEl) {
      const key = data.reused ? 'share.success_reused' : 'share.success_new';
      let msg = (typeof t === 'function') ? t(key) : '';
      if (data.reused && data.expires_at) {
        const until = new Date(data.expires_at);
        msg = msg.replace('{time}', until.toLocaleString());
      }
      labelEl.textContent = msg;
    }
  } catch (e) {
    if (errEl) {
      errEl.textContent = (typeof t === 'function') ? t('share.error_network') : 'Network error';
      errEl.hidden = false;
    }
  } finally {
    btn.disabled = false;
  }
}


async function handleShareUseCode() {
  const inv = $('inviteCode');
  const errEl = $('upgradeError');
  if (!_shareLatestCode) return;
  if (inv) {
    inv.value = _shareLatestCode;
    inv.focus();
  }
  // 直接 redeem
  await handleRedeemInvite();
}

async function openUpgrade() {
  const modal = $('upgradeModal');
  const scrim = $('upgradeScrim');
  if (modal) {
    modal.removeAttribute('aria-hidden');
    modal.hidden = false;
  }
  if (scrim) scrim.hidden = false;
  const s = $('upgradeStatus');
  if (s) s.hidden = true;
  const e = $('upgradeError');
  if (e) e.hidden = true;
  // 清空邀请码输入(防上次残留)
  const inv = $('inviteCode');
  if (inv) inv.value = '';
  const rBtn = $('redeemInviteBtn');
  if (rBtn) rBtn.disabled = false;
  const locale = (typeof I18N !== 'undefined' && I18N.current) || 'en';
  applyUpgrade(locale);
  const emailInput = $('upgradeEmail');
  if (emailInput) { if (userEmail) emailInput.value = userEmail; requestAnimationFrame(() => emailInput.focus()); }
  // 拉最新定价注入按钮 + 提示(异步,失败也无所谓,fallback 到 i18n 占位文案)
  loadPricing();
}

let _pricingLockTimer = null;  // 倒计时 setInterval id

async function loadPricing() {
  const locale = (typeof I18N !== 'undefined' && I18N.current) || 'en';
  try {
    const r = await fetch(`api/pricing?locale=${encodeURIComponent(locale)}`);
    if (!r.ok) throw new Error(`pricing fetch HTTP ${r.status}`);
    _currentPricing = await r.json();
    injectPricingUI(_currentPricing);
  } catch (e) {
    console.warn('[pricing] load failed:', e);
    _currentPricing = null;
    injectPricingUI(null);  // 渲染错误态,按钮置灰
  }
}

function injectPricingUI(p) {
  const planName = $('planTournamentName');
  const planAction = $('planTournamentAction');
  const hint = $('pricingHint');
  const matchToggle = $('matchListToggle');
  const matchToggleLabel = $('matchListToggleLabel');
  const matchList = $('matchList');
  const lockEl = $('pricingLock');
  const lockTime = $('pricingLockTime');
  const lockRefresh = $('pricingLockRefresh');
  if (!p) {
    if (planName) planName.textContent = '—';
    if (planAction) planAction.textContent = '—';
    if (hint) hint.textContent = '';
    if (matchToggleLabel) matchToggleLabel.textContent = '—';
    if (matchList) { matchList.hidden = true; matchList.innerHTML = ''; }
    if (lockEl) lockEl.hidden = true;
    if (_pricingLockTimer) { clearInterval(_pricingLockTimer); _pricingLockTimer = null; }
    const btn = $('planTournament');
    if (btn) btn.disabled = true;
    return;
  }
  const remaining = p.remaining_matches;
  const current = p.current_price.toFixed(2);
  const full = p.full_price.toFixed(2);
  const unit = p.unit_price.toFixed(2);
  const sym = p.currency_symbol;
  const savings = p.savings_pct;
  if (planName) planName.textContent = t('sub.remaining_label_fmt', { remaining, unit, symbol: sym });
  if (planAction) planAction.textContent = t('sub.pay_remaining_fmt', { current, symbol: sym });
  if (hint) hint.textContent = t('sub.pricing_hint_fmt', { full, savings, symbol: sym });
  // 场次列表(最多展示 10 场,折叠/展开)
  if (matchToggleLabel) {
    matchToggleLabel.textContent = t('sub.matches_included', { n: remaining });
  }
  if (matchList && p.upcoming_sample) {
    const show = p.upcoming_sample.slice(0, 10);
    matchList.innerHTML = show.map(m => {
      const teams = localeStartsZh() ? `${m.home_zh} vs ${m.away_zh}` : `${m.home} vs ${m.away}`;
      return `<div class="row"><span class="date">${m.kickoff_bj || m.date}</span><span>${escapeHtml(teams)}</span></div>`;
    }).join('') + (remaining > show.length
      ? `<div class="more">${escapeHtml(t('sub.matches_more', { n: remaining - show.length }))}</div>`
      : '');
  }
  // 价格锁定倒计时
  if (lockEl && p.pricing_locked_until) {
    lockEl.hidden = false;
    if (_pricingLockTimer) clearInterval(_pricingLockTimer);
    _pricingLockTimer = setInterval(() => updateLockUI(p), 1000);
    updateLockUI(p);
  }
  const btn = $('planTournament');
  if (btn) btn.disabled = false;
}

function updateLockUI(p) {
  const lockEl = $('pricingLock');
  const lockTime = $('pricingLockTime');
  const lockRefresh = $('pricingLockRefresh');
  const btn = $('planTournament');
  if (!lockEl || !p.pricing_locked_until) return;
  const expiry = new Date(p.pricing_locked_until).getTime();
  const ms = expiry - Date.now();
  if (ms <= 0) {
    lockEl.classList.add('expired');
    if (lockTime) lockTime.textContent = t('sub.locked_expired');
    if (lockRefresh) { lockRefresh.hidden = false; lockRefresh.textContent = t('sub.locked_refresh'); }
    if (btn) btn.disabled = true;
    if (_pricingLockTimer) { clearInterval(_pricingLockTimer); _pricingLockTimer = null; }
  } else {
    lockEl.classList.remove('expired');
    if (lockRefresh) lockRefresh.hidden = true;
    if (btn) btn.disabled = false;
    const min = Math.floor(ms / 60000);
    const sec = Math.floor((ms % 60000) / 1000);
    if (lockTime) lockTime.textContent = t('sub.locked_remaining', { time: `${min}:${String(sec).padStart(2, '0')}` });
  }
}

function localeStartsZh() {
  return (typeof I18N !== 'undefined' && I18N.current || 'en').toLowerCase().startsWith('zh');
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function refreshPricingUI() {
  if (_currentPricing) injectPricingUI(_currentPricing);
}

// 升级弹窗 title/desc 多语言文案(按钮价格由 /api/pricing 动态注入,这里不再写死)
const UPGRADE_I18N = {
  'zh-CN': { title:'支持本项目', desc:'解锁全部 Pro 功能：AI 反思调优、联网赛前情报、历史交手、价值下注、赔率预测、市场盘口价' },
  'es':    { title:'Apoyar el proyecto', desc:'Desbloquear todo Pro: Reflexión AI, Intel Web, H2H, Value Bets, Cuotas, Líneas de Mercado' },
  'pt':    { title:'Apoiar o projeto', desc:'Desbloquear tudo Pro: Reflexão AI, Intel Web, H2H, Value Bets, Odds, Linhas de Mercado' },
  'fr':    { title:'Soutenir le projet', desc:'Débloquer tout Pro: Réflexion AI, Intel Web, H2H, Value Bets, Cotes, Lignes de Marché' },
  'ar':    { title:'ادعم المشروع', desc:'فتح كل Pro: تفكير AI، معلومات ويب، H2H، رهانات القيمة، الاحتمالات، أسعار السوق' },
  'en':    { title:'Support the Project', desc:'Unlock all Pro features: AI Reflection, Web Intel, H2H History, Value Bets, Odds Prediction, Market Lines' },
};

function applyUpgrade(locale) {
  const d = UPGRADE_I18N[locale] || UPGRADE_I18N.en;
  const titleEl = $('upgradeTitle');
  if (titleEl) titleEl.textContent = d.title;
  const descEl = document.querySelector('.upgrade-desc');
  if (descEl) descEl.textContent = d.desc;
  // planTournament 按钮价格由 injectPricingUI 根据 /api/pricing 动态填充,这里不动
}

async function fetchI18nAndShow(locale) {
  try {
    const r = await fetch('static/i18n/' + locale + '.json');
    if (!r.ok) return false;
    const dict = await r.json();
    const titleEl = $('upgradeTitle');
    if (titleEl) titleEl.textContent = dict['sub.upgrade_title'] || 'Support the Project';
    const descEl = document.querySelector('.upgrade-desc');
    if (descEl) descEl.textContent = dict['sub.upgrade_desc'] || '';
    // planTournament 按钮价格由 injectPricingUI 根据 /api/pricing 动态填充,这里不动
    return true;
  } catch { return false; }
}

function closeUpgrade() {
  const modal = $('upgradeModal');
  const scrim = $('upgradeScrim');
  if (modal) { modal.hidden = true; modal.setAttribute('aria-hidden', 'true'); }
  if (scrim) scrim.hidden = true;
  // 关弹窗时停掉倒计时,下次打开重新拉(避免在后台空转)
  if (_pricingLockTimer) { clearInterval(_pricingLockTimer); _pricingLockTimer = null; }
}

async function handleCheckout(plan) {
  const email = $('upgradeEmail').value.trim();
  if (!email || !email.includes('@')) {
    const e = $('upgradeError');
    if (e) { e.hidden = false; e.textContent = 'Please enter a valid email'; }
    return;
  }
  try { localStorage.setItem('wc_email', email); } catch {}
  userEmail = email;
  const priceIdMap = { tournament: 'price_tournament' };
  const base = window.location.origin + window.location.pathname.replace(/\/$/, '');
  const origin = base;
  const locale = (typeof I18N !== 'undefined' && I18N.current) || 'auto';
  const remaining = _currentPricing?.remaining_matches || 0;
  const btn = $('planTournament');
  if (btn) btn.disabled = true;
  try {
    const url = `api/create-checkout-session?email=${encodeURIComponent(email)}&price_id=${priceIdMap[plan] || 'price_tournament'}&origin=${encodeURIComponent(origin)}&locale=${encodeURIComponent(locale)}&remaining_count=${remaining}`;
    const r = await fetch(url, { method: 'POST' });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    if (data.url) { window.location.href = data.url; }
    else { throw new Error('no url'); }
  } catch (err) {
    const e = $('upgradeError');
    if (e) { e.hidden = false; e.textContent = t('sub.checkout_error') + ' ' + err.message; }
    if (btn) btn.disabled = false;
  }
}

async function handleRedeemInvite() {
  const email = $('upgradeEmail')?.value.trim() || userEmail;
  const code = $('inviteCode')?.value.trim() || '';
  const errEl = $('upgradeError');
  if (!email || !email.includes('@')) {
    if (errEl) { errEl.hidden = false; errEl.textContent = t('sub.invite_need_email'); }
    return;
  }
  if (!code) {
    if (errEl) { errEl.hidden = false; errEl.textContent = t('sub.invite_invalid_format'); }
    return;
  }
  const btn = $('redeemInviteBtn');
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`api/redeem-invite?email=${encodeURIComponent(email)}&code=${encodeURIComponent(code)}`, { method: 'POST' });
    let data;
    try { data = await r.json(); } catch { data = {}; }
    if (!r.ok || !data.ok) {
      const errKey = ({
        invalid_email: 'sub.invite_invalid_email',
        not_found: 'sub.invite_not_found',
        already_used: 'sub.invite_already_used',
      })[data.detail] || 'sub.invite_error';
      throw new Error(t(errKey));
    }
    // 成功:持久化邮箱 + 切付费状态 + 刷新 Pro gate
    userEmail = data.email || email;
    try { localStorage.setItem('wc_email', userEmail); } catch {}
    isPaid = true;
    const s = $('upgradeStatus');
    if (s) { s.hidden = false; s.textContent = t('sub.unlocked'); s.className = 'upgrade-status ok'; }
    if (btn) btn.textContent = t('sub.invite_success');
    setTimeout(() => {
      closeUpgrade();
      // 恢复按钮文案以便下次打开
      if (btn) { btn.disabled = false; btn.textContent = ''; }
      const planAction = btn?.querySelector('.plan-action');
      const planName = btn?.querySelector('.plan-name');
      if (planName) planName.textContent = t('sub.invite_label');
      if (planAction) planAction.textContent = t('sub.invite_action');
      // 刷掉付费墙:数据来源 / 价值下注 / 抽屉
      document.querySelectorAll('.pro-gate-drawer').forEach(d => d.remove());
      renderProGate();
      renderMarket();
      renderValue();
      renderStatus();
    }, 1200);
  } catch (err) {
    if (errEl) { errEl.hidden = false; errEl.textContent = err.message; }
    if (btn) btn.disabled = false;
  }
}

// ── 状态栏渲染 ────────────────────────────────────
function renderStatus() {
  const s = cache.status || {};
  const dot = $('liveDot'), lbl = $('statusLabel');
  dot.className = 'live-dot';
  if (s.running) { dot.classList.add('busy'); lbl.textContent = t('status.updating'); }
  else if (s.success === false) { dot.classList.add('err'); lbl.textContent = t('status.failed'); }
  else if (s.success) { dot.classList.add('ok'); lbl.textContent = t('status.live'); }
  else { lbl.textContent = t('status.connecting'); }

  $('lastUpdated').textContent = s.last_updated ? s.last_updated.slice(11, 16) : '-';
  if (s.error) lbl.textContent = s.error.slice(0, 20);
}

// ── 赛程渲染 ──────────────────────────────────────
function renderSchedule() {
  const list = $('scheduleList');
  let matches = cache.schedule || [];
  if (activeStage !== 'all') matches = matches.filter(m => m.stage === activeStage);
  if (!matches.length) { list.innerHTML = `<div class="placeholder">${t('empty.no_match')}</div>`; return; }

  // 按本地时区日期分组(保持升序)
  const byDay = {};
  for (const m of matches) (byDay[localDate(m)] ||= []).push(m);
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
       + `<span class="day-count">${t('schedule.match_count', { n: byDay[day].length })}</span></div>`;
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
    const label = histExpanded ? t('hist.collapse') : t('hist.expand');
    const arrow = histExpanded ? '▴' : '▾';
    const winPct = fin ? Math.round(winHit / fin * 100) : 0;
    const histTitle = t('hist.title');
    const histStats = `${t('hist.played', { n: fin })} · `
      + t('hist.win_hit', { n: winHit, pct: winPct + '%' }) + ' · '
      + t('hist.score_hit', { n: scoreHit });
    html += `<div class="hist-fold hist-top">`
         + `<button class="hist-toggle" type="button">`
         + `<span class="hist-title">${histTitle}</span>`
         + `<span class="hist-stats">${histStats}</span>`
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
  const stageZh = STAGE_ZH()[m.stage] || m.stage;
  const groupTag = m.group ? `${stageZh} · ${m.group}` : stageZh;
  const homeTeam = teamBlock(m.home, teamName(m, 'home'), m.elo_h, 'home');
  const awayTeam = teamBlock(m.away, teamName(m, 'away'), m.elo_a, 'away');
  const center = renderCenter(m);
  const localK = localTime(m);
  const bjK = m.kickoff_bj;
  const koTitle = bjK && localK !== bjK
    ? `${t('schedule.kickoff_bj')}: ${bjK} → ${I18N.tz || 'local'}: ${localK}`
    : t('schedule.kickoff_bj');
  return `<article class="match-card${flashCls}" data-status="${m.status}"`
    + ` data-date="${esc(m.date)}" data-home="${esc(m.home)}" data-away="${esc(m.away)}"${delayStyle}>`
    + `<div class="match-meta"><span class="stage-tag">${esc(groupTag)}</span>${localK ? `<span class="ko-tag" title="${esc(koTitle)}">${esc(localK)}</span>` : ''}`
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
// prefix='drawer' 用抽屉尺寸/精度,decimals 控制百分比小数位
function probCells(probs, prefix, decimals, asOdds, masked) {
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
    if (masked) return '*';
    if (asOdds) {
      const odds = v > 0 ? (1 / v).toFixed(2) : '-';
      return prefix === 'drawer' ? odds : `${t('result.odds_prefix', { odds })}`;
    }
    return decimals ? (v * 100).toFixed(decimals) : Math.round(v * 100);
  };
  const lbl = (cls, key, val) =>
    `<span class="pl ${cls}"><span class="pk">${t(key)}</span><span class="pv">${fmt(val)}</span></span>`;
  return `<div class="${barCls}">${cells}</div>`
    + `<div class="${lblCls}">${lbl('home','result.home_win',ph)}${lbl('draw','result.draw',pd)}${lbl('away','result.away_win',probs[2])}</div>`;
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
  if (m.source === 'odds') return `<span class="src-badge odds" title="${t('src.odds_badge')}">📊${t('src.tag_b')}</span>`;
  return `<span class="src-badge elo" title="${t('src.elo_badge')}">${t('src.tag_e')}</span>`;
}

function renderCenter(m) {
  if (m.status === 'finished' && m.score) {
    const [h, a] = m.score;
    const cls = h > a ? 'win-home' : (a > h ? 'win-away' : '');
    const resultLabel = RESULT_ZH()[m.result] || '';
    // 有预测:真实比分 + 赛前预测(48方块) + 命中标记
    if (m.probs) {
      const hit = probHit(m.probs, m.result);
      const ps = m.top_score;
      const scoreHit = ps && ps[0] === h && ps[1] === a;
      const mark = scoreHit ? t('mark.precise') : (hit ? t('mark.hit') : t('mark.miss_actual', { result: resultLabel }));
      const markCls = scoreHit ? 'precise' : (hit ? 'hit' : 'miss');
      const predStr = ps ? `<span class="pred-score"><i>${t('schedule.predicted')}</i>${ps[0]}-${ps[1]}</span>` : '';
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
    return `<div class="center-cell"><span class="no-elo">${t('card.elo_missing')}</span></div>`;
  }
  const ts = m.top_score ? `${t('schedule.predicted')} ${m.top_score[0]}-${m.top_score[1]}` : '';
  // 未开赛:未付费 → 数字变 *,不显示预测比分
  const masked = !isPaid && m.status !== 'finished';
  if (masked) {
    return `<div class="center-cell">`
      + probCells(m.probs, '', 0, true, true)
      + `<div class="topscore">${srcBadge(m)}</div></div>`;
  }
  return `<div class="center-cell">`
    + probCells(m.probs, '', 0, true)
    + `<div class="topscore">${ts}${srcBadge(m)}</div></div>`;
}

// ── 夺冠榜渲染 ────────────────────────────────────
function renderChampions() {
  const list = $('champList');
  const ranking = cache.champions || [];
  if (!ranking.length) {
    list.innerHTML = `<div class="placeholder">${t('empty.loading_champs')}</div>`;
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
    ? t('champions.updated_at', { time: cache.status.champions_built_at.slice(11, 16) })
    : t('champions.simulations');
}

// ── 小组榜渲染 ────────────────────────────────────
function renderGroups() {
  const grid = $('groupsGrid');
  const groups = cache.groups || {};
  const keys = Object.keys(groups).sort();
  if (!keys.length) { grid.innerHTML = `<div class="placeholder">${t('empty.loading_groups')}</div>`; return; }
  let html = '';
  for (const g of keys) {
    const rows = groups[g];
    html += `<div class="group-card"><div class="group-card-head">`
      + `<span class="group-letter">${g}</span><span class="group-label">${t('groups.label')} ${g}</span></div>`
      + `<table class="group-table"><thead><tr>`
      + `<th>${t('groups.col.team')}</th><th>${t('groups.col.played')}</th><th>${t('groups.col.points')}</th><th>${t('groups.col.expected')}</th><th>${t('groups.col.gd')}</th></tr></thead><tbody>`;
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

// ── 历史回测渲染(2018/2022 赛前 Elo vs 真实结果)──────────
function renderBacktest() {
  const wrap = $('backtestList');
  const data = cache.backtest;
  if (!data || !data.tournaments) {
    wrap.innerHTML = `<div class="placeholder">${t('empty.loading_backtest')}</div>`;
    return;
  }
  let html = '<div class="bt-wrap">';
  for (const tdata of data.tournaments) {
    const rate = (tdata.hit_rate != null) ? (tdata.hit_rate * 100).toFixed(1) : '-';
    const sRate = (tdata.score_hit_rate != null) ? (tdata.score_hit_rate * 100).toFixed(1) : '-';
    html += '<section class="bt-block">'
      + '<div class="bt-head">'
      + `<span class="bt-name">${esc(tdata.name)}</span>`
      + `<span class="bt-rate">${rate}<small>%</small><i>${t('backtest.win_rate_short')}</i></span>`
      + `<span class="bt-subrate">${t('backtest.exact_short')} <b>${sRate}%</b> <small>${tdata.score_hits}/${tdata.counted}</small></span>`
      + `<span class="bt-meta">${t('backtest.matches_total', { n: tdata.count })}</span>`
      + '</div><div class="bt-matches">';
    for (const m of tdata.matches) {
      const [h, a] = m.score;
      const resZh = RESULT_ZH()[m.result] || '';
      const hitCls = m.hit === true ? 'ok' : (m.hit === false ? 'no' : 'na');
      const ps = m.pred_score;
      const scoreHit = m.score_hit === true;
      const dash = t('card.dash');
      const mark = scoreHit ? t('card.star_exact')
        : (m.hit === true ? t('card.win_hit')
        : (m.hit === false ? t('card.loss_prefix', { result: resZh }) : dash));
      const markCls = scoreHit ? 'exact' : hitCls;
      const psHtml = ps ? `<span class="bt-pscore ${scoreHit?'exact':''}">${ps[0]}-${ps[1]}</span>` : `<span class="bt-pscore">${dash}</span>`;
      const probs = m.probs
        ? `<span class="bt-probs">${t('bt.tri_probs', { a: Math.round(m.probs[0]*100), b: Math.round(m.probs[1]*100), c: Math.round(m.probs[2]*100) })} ${srcBadge(m)}</span>`
        : `<span class="bt-probs">${t('bt.elo_missing')}</span>`;
      html += `<div class="bt-row ${hitCls}">`
        + `<span class="bt-date">${esc(m.date.slice(5))}</span>`
        + `<span class="bt-team">${flag(m.home)} ${esc(teamName(m, 'home'))}</span>`
        + `<span class="bt-scoreline">${t('backtest.pred')}${psHtml}<span class="bt-real">${t('backtest.actual')}${h}-${a}</span></span>`
        + `<span class="bt-team right">${esc(teamName(m, 'away'))} ${flag(m.away)}</span>`
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
  // 未付费用户只展示已结束赛事
  const finishedKeys = new Set();
  if (!isPaid) {
    (cache.schedule || []).filter(s => s.status === 'finished').forEach(s =>
      finishedKeys.add(`${s.date}|${s.home}|${s.away}`));
  }
  const showAll = isPaid;
  const matches = showAll ? d.matches : d.matches.filter(m =>
    finishedKeys.has(`${m.date}|${m.home}|${m.away}`));
  if (!matches.length) {
    $('mktSummary').innerHTML = `<span class="dim">${t('empty.no_finished')}</span>`;
    $('mktList').innerHTML = '';
    return;
  }
  const cnt = {B:0, A:0, Elo:0};
  matches.forEach(m => cnt[m.mode]++);
  const pct = n => matches.length ? (n / matches.length * 100).toFixed(0) : 0;
  $('mktSummary').innerHTML =
    `${t('market.total_summary', { total: matches.length })} · ` +
    `<b class="mkt-tag mkt-a">A</b> ${t('market.mode_a_count', { n: cnt.A, pct: pct(cnt.A) })} · ` +
    `<b class="mkt-tag mkt-e">Elo</b> ${t('market.elo_count', { n: cnt.Elo, pct: pct(cnt.Elo) })} · ` +
    (cnt.B ? `<b class="mkt-tag mkt-b">B</b> ${t('market.mode_b_count', { n: cnt.B })} · ` : '')
    + (!showAll ? `<span class="dim"> (${t('market.free_tier_note')})</span>` : '');
  $('vbNote') && ($('vbNote').textContent = '');
  const tagHtml = m => `<b class="mkt-tag mkt-${m.toLowerCase()}">${m}</b>`;
  const tri = p => p ? `${t('result.h_short')}${pct2(p[0])} ${t('result.d_short')}${pct2(p[1])} ${t('result.a_short')}${pct2(p[2])}` : '—';
  const pct2 = x => (x * 100).toFixed(0) + '%';
  const rows = matches.map(m => {
    if (m.mode === 'Elo') {
      return `<div class="mkt-row elo">
        <span class="mkt-tag-wrap">${tagHtml(m.mode)}</span>
        <span class="mkt-match">${esc(teamName(m, 'home'))} vs ${esc(teamName(m, 'away'))}</span>
        <span class="mkt-src">${t('market.no_odds')}</span>
        <span class="mkt-prob">${tri(m.prob_elo)}</span>
      </div>`;
    }
    const src = m.mode === 'B'
      ? `${t('market.odds_1x2', { odds: m.odds_1x2.join('/') })} + ${t('market.odds_ou', { line: m.ou_line, odds: (m.ou||[]).join('/') })}`
      : t('market.odds_1x2', { odds: m.odds_1x2.join('/') });
    return `<div class="mkt-row inv">
      <span class="mkt-tag-wrap">${tagHtml(m.mode)}</span>
      <span class="mkt-match">${esc(teamName(m, 'home'))} vs ${esc(teamName(m, 'away'))}</span>
      <span class="mkt-src">${esc(src)}</span>
      <span class="mkt-prob"><span class="mkt-pinv">${t('market.inverted')} ${tri(m.prob_inverted)}</span><span class="mkt-pelo">${t('market.elo_short')} ${tri(m.prob_elo)}</span></span>
    </div>`;
  });
  $('mktList').innerHTML = rows.join('');
}

async function openDrawer(date, home, away) {
  $('drawerTitle').textContent = t('drawer.title');
  $('drawerBody').innerHTML = `<div class="placeholder">${t('empty.loading_match')}</div>`;
  $('drawer').classList.add('open');
  $('drawerScrim').classList.add('open');
  $('drawer').setAttribute('aria-hidden', 'false');
  // 反思开关状态(localStorage 持久化,默认关)
  const storeKey = `wc_reflect_${date}_${home}_${away}`;
  let refl = false;
  try { refl = localStorage.getItem(storeKey) === '1'; } catch {}
  try {
    const url = `api/match?date=${encodeURIComponent(date)}&home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}&locale=${encodeURIComponent(I18N.current)}`
      + (refl ? '&with_reflection=1' : '') + emailSuffix();
    const m = await getJSON(url);
    renderDrawer(m);
  } catch (e) {
    $('drawerBody').innerHTML = `<div class="placeholder">${t('empty.load_failed', { err: e.message })}</div>`;
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
      `<div class="vb-stat">${t('value.scan_n', { n: dist.n })}</div>` +
      `<div class="vb-stat">${t('value.dist', { avg: pct(dist.mean), med: pct(dist.median), max: pct(dist.max) })}</div>` +
      `<div class="vb-stat">${t('value.thresholds', { a: dist.thresholds['1%'], b: dist.thresholds['3%'], c: dist.thresholds['5%'], d: dist.thresholds['10%'] })}</div>` +
      `<div class="vb-stat vb-warn">${t('value.warn', { n: d.n_value_bets })}</div>`;
  } else {
    $('vbSummary').innerHTML = `<div class="placeholder">${t('value.no_odds')}</div>`;
  }
  // Top 20 表格
  const fmt = x => (x * 100).toFixed(0) + '%';
  const edgeClass = e => e >= 0.10 ? 'edge-huge' : e >= 0.05 ? 'edge-strong' : 'edge-weak';
  const rows = (d.upcoming || []).map(v => {
    const elo = v.p_elo.map(fmt).join('/');
    const mkt = v.p_mkt.map(fmt).join('/');
    return `<div class="vb-row ${edgeClass(v.max_edge)}">
      <span class="vb-date">${v.date}</span>
      <span class="vb-match">${esc(teamName(v, 'home'))} vs ${esc(teamName(v, 'away'))}</span>
      <span class="vb-elo">${t('value.elo_pct', { p: elo })}</span>
      <span class="vb-mkt">${t('value.mkt_pct', { p: mkt })}</span>
      <span class="vb-edge">${(v.max_edge*100).toFixed(1)}%</span>
      <span class="vb-pick"><b>${v.picked}</b> @ ${v.odds.toFixed(2)}</span>
      <span class="vb-kelly">${t('value.kelly')} ${(v.kelly*100).toFixed(1)}%</span>
    </div>`;
  }).join('');
  $('vbList').innerHTML = rows || `<div class="placeholder">${t('value.empty')}</div>`;
}

function closeDrawer() {
  $('drawer').classList.remove('open');
  $('drawerScrim').classList.remove('open');
  $('drawer').setAttribute('aria-hidden', 'true');
}
function renderDrawer(m) {
  const isFinished = m.status === 'finished';
  const drawerMasked = !isPaid && !isFinished;
  const stageZh = STAGE_ZH()[m.stage] || m.stage;
  let html = `<div class="drawer-section"><h4>${esc(stageZh)} · ${esc(m.city || '')}</h4>`;
  html += `<div class="drawer-teams">`
    + `<div class="drawer-team"><span class="flag">${flag(m.home)}</span><span class="name">${esc(teamName(m, 'home'))}</span><span class="elo">Elo ${m.elo_h ?? '-'}</span></div>`
    + `<div class="drawer-vs">VS</div>`
    + `<div class="drawer-team"><span class="flag">${flag(m.away)}</span><span class="name">${esc(teamName(m, 'away'))}</span><span class="elo">Elo ${m.elo_a ?? '-'}</span></div>`
    + `</div></div>`;

  if (m.status === 'finished' && m.score) {
    html += `<div class="drawer-section"><h4>${t('drawer.final_score')}</h4>`
      + `<div class="scoreline"><span class="num">${m.score[0]}</span><span class="num dash">:</span><span class="num">${m.score[1]}</span></div>`
      + `<div class="stat-row"><span class="k">${t('drawer.result')}</span><span class="v">${esc(RESULT_ZH()[m.result] || '')}</span></div></div>`;
  }
  if (m.probs) {
    html += `<div class="drawer-section"><h4>${t('drawer.probs')}</h4>`
      + probCells(m.probs, 'drawer', 1, false, drawerMasked)
      + `</div>`;
  }
  if (m.lam_h != null) {
    html += `<div class="drawer-section"><h4>${t('drawer.model_params')}</h4>`
      + `<div class="stat-row"><span class="k">${t('drawer.lam_home')}</span><span class="v">${drawerMasked ? '*' : m.lam_h}</span></div>`
      + `<div class="stat-row"><span class="k">${t('drawer.lam_away')}</span><span class="v">${drawerMasked ? '*' : m.lam_a}</span></div>`
      + `<div class="stat-row"><span class="k">${t('drawer.host_adv')}</span><span class="v">${drawerMasked ? '*' : (m.host_adv ? '+' + m.host_adv : t('drawer.neutral'))}</span></div></div>`;
  }
  if (m.mode && !drawerMasked) {
    const MODE_DESC = { B:'mode.b_desc', A:'mode.a_desc', Elo:'mode.elo_desc' };
    const MODE_LABEL = { B:t('src.tag_b'), A:t('src.tag_a'), Elo:t('src.tag_e') };
    html += `<div class="drawer-section"><h4>${t('drawer.data_source')}</h4>`
      + `<div class="stat-row"><span class="k">${t('drawer.prediction_basis')}</span><span class="v src-mode">`
      + `<b class="mkt-tag mkt-${m.mode.toLowerCase()}">${MODE_LABEL[m.mode]}</b> ${t(MODE_DESC[m.mode])}</span></div>`;
    if (m.odds_1x2) {
      html += `<div class="stat-row"><span class="k">${t('drawer.odds_1x2')}</span><span class="v mono">${drawerMasked ? '* / * / *' : m.odds_1x2.join(' / ')}</span></div>`;
    }
    if (m.ou) {
      html += `<div class="stat-row"><span class="k">${t('drawer.odds_ou')}</span><span class="v mono">${drawerMasked ? '* / *' : t('drawer.ou_format', { over: m.ou[0], under: m.ou[1] })}</span></div>`;
    }
    if (m.lam_inverted && m.lam_elo) {
      html += `<div class="stat-row"><span class="k">${t('drawer.lam_market')}</span><span class="v mono">${drawerMasked ? '*' : t('drawer.lam_market_row', { h: m.lam_inverted[0], a: m.lam_inverted[1] })}</span></div>`
        + `<div class="stat-row"><span class="k">${t('drawer.lam_elo')}</span><span class="v mono dim">${drawerMasked ? '*' : t('drawer.lam_elo_row_dim', { h: m.lam_elo[0], a: m.lam_elo[1] })}</span></div>`;
    } else if (m.lam_elo) {
      html += `<div class="stat-row"><span class="k">${t('drawer.lam_elo')}</span><span class="v mono">${drawerMasked ? '*' : t('drawer.lam_elo_row', { h: m.lam_elo[0], a: m.lam_elo[1] })}</span></div>`;
    }
    html += `</div>`;
  }
  if (m.top5 && m.top5.length) {
    const maxp = m.top5[0][2];
    html += `<div class="drawer-section"><h4>${t('drawer.top5')}</h4><div class="top5-list">`;
    for (const [h, a, p] of m.top5) {
      html += `<div class="top5-row"><span class="sc">${h}-${a}</span>`
        + `<div class="bar5"><div class="fill5" style="width:${drawerMasked ? 0 : p / maxp * 100}%"></div></div>`
        + `<span class="p5">${drawerMasked ? '*' : (p * 100).toFixed(2) + '%'}</span></div>`;
    }
    html += `</div></div>`;
  }
  // AI 反思(实验):默认关,Pro 功能
  let reflectStoreKey = null;
  let reflectStored = false;
  if (m.reflection && m.reflection.has_any && (isPaid || isFinished)) {
    const r = m.reflection;
    reflectStoreKey = `wc_reflect_${m.date}_${m.home}_${m.away}`;
    try { reflectStored = localStorage.getItem(reflectStoreKey) === '1'; } catch {}
    const adj = m.reflection_adj || { home: 0, away: 0 };
    const modeBadge = reflectStored
      ? `<span class="ref-mode-badge on">${t('drawer.reflection_on', { home: (adj.home >= 0 ? '+' : '') + adj.home, away: (adj.away >= 0 ? '+' : '') + adj.away })}</span>`
      : `<span class="ref-mode-badge off">${t('drawer.reflection_basic')}</span>`;
    html += `<div class="drawer-section reflection-section">`
      + `<div class="reflection-head">`
      + `<h4>${t('drawer.reflection_title')} <span class="badge-exp">${t('drawer.reflection_badge')}</span></h4>`
      + `<label class="switch" title="${t('drawer.reflection_toggle_title')}">`
      + `<input type="checkbox" id="reflectToggle">`
      + `<span class="slider"></span></label></div>`
      + `<div class="reflection-mode-line">${modeBadge}</div>`
      + `<div class="reflection-summary">${esc(formatReflectionSummary(r.summary_parts))}</div>`
      + `<div class="reflection-body">`;
    if (r.home_archive) html += renderTeamArchive(r.home_archive, 'home', r.home_translated);
    if (r.away_archive) html += renderTeamArchive(r.away_archive, 'away', r.away_translated);
    html += `</div></div>`;
  }
  // 赛前情报 + 历史交手(已结束免费,未开赛 Pro)
  if (isPaid || isFinished) {
    html += `<div class="drawer-section intel-section">`
      + `<div class="intel-head">`
      + `<h4>${t('drawer.intel_title')} <span class="badge-ws">${t('drawer.intel_badge')}</span></h4>`
      + `<button class="intel-btn" id="intelBtn">${t('drawer.intel_fetch')}</button></div>`
      + `<div id="intelBody" class="intel-body"></div></div>`;
    html += `<div class="drawer-section h2h-section">`
      + `<div class="h2h-head">`
      + `<h4>${t('drawer.h2h_title')} <span class="badge-ws">${t('drawer.h2h_badge')}</span></h4>`
      + `<button class="h2h-btn" id="h2hBtn">${t('drawer.h2h_fetch')}</button></div>`
      + `<div id="h2hBody" class="h2h-body"></div></div>`;
  } else {
    html += `<div class="drawer-section pro-gate-drawer"><div class="pro-gate-drawer-inner">`
      + `<span class="pro-gate-icon-sm">⭐</span>`
      + `<span>${t('sub.pro_feature')}</span>`
      + `<button class="pro-gate-btn-sm" type="button">${t('sub.upgrade')}</button>`
      + `</div><div class="pro-gate-features" style="margin-top:6px">${t('sub.pro_features')}</div></div>`;
  }
  $('drawerBody').innerHTML = html;
  // 抽屉 Pro gate 按钮
  const gateBtn = $('drawerBody')?.querySelector('.pro-gate-btn-sm');
  if (gateBtn) gateBtn.addEventListener('click', openUpgrade);
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
  if (btn) { btn.disabled = true; btn.textContent = t('intel.fetch_loading'); }
  if (body) body.innerHTML = `<div class="placeholder">${t('drawer.intel_loading')}</div>`;
  try {
    const url = `api/match?date=${encodeURIComponent(date)}&home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}&with_intel=1&locale=${encodeURIComponent(I18N.current)}${emailSuffix()}`;
    const m = await getJSON(url);
    renderIntel(m.intel);
  } catch (e) {
    if (body) body.innerHTML = `<div class="placeholder">${t('empty.load_failed', { err: e.message })}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = t('drawer.intel_refetch'); }
  }
}

function renderIntel(intel) {
  const body = $('intelBody');
  if (!body) return;
  if (!intel) {
    body.innerHTML = `<div class="placeholder">${t('drawer.intel_empty')}</div>`;
    return;
  }
  if (intel.error) {
    body.innerHTML = `<div class="placeholder">✗ ${esc(intel.error)}</div>`;
    return;
  }
  const CONF = { high:'intel.conf.high', medium:'intel.conf.medium', low:'intel.conf.low' };
  const IMPACT = { high:'intel.impact.high', medium:'intel.impact.medium', low:'intel.impact.low' };
  let h = '';
  // 伤停
  if (intel.injuries && intel.injuries.length) {
    h += `<div class="intel-subhead">${t('intel.injuries')}</div><ul class="intel-list">`;
    for (const inj of intel.injuries) {
      const cls = inj.impact || 'medium';
      h += `<li class="intel-inj"><span class="intel-team">${esc(inj.team === intel._home ? t('intel.team.home') : t('intel.team.away'))}</span>`
        + `<span class="intel-player">${esc(inj.player)}</span>`
        + `<span class="intel-reason">${esc(inj.reason || '')}</span>`
        + `<span class="intel-impact impact-${esc(cls)}">${t(IMPACT[cls] || cls)}</span></li>`;
    }
    h += '</ul>';
  }
  // 近期战绩
  if (intel.form_home || intel.form_away) {
    h += '<div class="intel-grid">';
    if (intel.form_home) {
      h += `<div class="intel-form"><div class="intel-form-team">${t('intel.form_home')}</div>`
        + `<div class="intel-form-wdl">${renderWDL(intel.form_home.recent5)}</div>`
        + `<div class="intel-form-note">${esc(intel.form_home.note || '')}</div></div>`;
    }
    if (intel.form_away) {
      h += `<div class="intel-form"><div class="intel-form-team">${t('intel.form_away')}</div>`
        + `<div class="intel-form-wdl">${renderWDL(intel.form_away.recent5)}</div>`
        + `<div class="intel-form-note">${esc(intel.form_away.note || '')}</div></div>`;
    }
    h += '</div>';
  }
  // 战术要点
  if (intel.tactical_notes && intel.tactical_notes.length) {
    h += `<div class="intel-subhead">${t('intel.tactical')}</div><ul class="intel-notes">`;
    for (const n of intel.tactical_notes) h += `<li>${esc(n)}</li>`;
    h += '</ul>';
  }
  // 媒体关注
  if (intel.headline_concerns && intel.headline_concerns.length) {
    h += `<div class="intel-subhead">${t('intel.media')}</div><ul class="intel-notes">`;
    for (const n of intel.headline_concerns) h += `<li>${esc(n)}</li>`;
    h += '</ul>';
  }
  // 预测暗示
  if (intel.prediction_hint) {
    h += `<div class="intel-hint">${t('intel.hint', { hint: intel.prediction_hint })}</div>`;
  }
  // 元信息
  h += `<div class="intel-meta">`
    + `<span class="conf-${esc(intel.confidence || 'low')}">${t(CONF[intel.confidence] || 'intel.conf.low')}</span>`
    + (intel.search_summary ? `<span class="intel-search-summary">${esc(intel.search_summary)}</span>` : '')
    + `</div>`;
  body.innerHTML = h;
}

function renderWDL(s) {
  if (!s) return '';
  return s.split('').map(c => {
    const m = { W: 'win', D: 'draw', L: 'loss' };
    return `<span class="wdl-${m[c] || 'draw'}">${c}</span>`;
  }).join('');
}

// ── 历史交手(web search)─────────────────────────
async function fetchH2H(date, home, away) {
  const btn = $('h2hBtn');
  const body = $('h2hBody');
  if (btn) { btn.disabled = true; btn.textContent = t('h2h.fetch_loading'); }
  if (body) body.innerHTML = `<div class="placeholder">${t('drawer.h2h_loading')}</div>`;
  try {
    const url = `api/match?date=${encodeURIComponent(date)}&home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}&with_h2h=1&locale=${encodeURIComponent(I18N.current)}${emailSuffix()}`;
    const m = await getJSON(url);
    renderH2H(m.h2h);
  } catch (e) {
    if (body) body.innerHTML = `<div class="placeholder">${t('empty.load_failed', { err: e.message })}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = t('drawer.h2h_refetch'); }
  }
}

function renderH2H(h2h) {
  const body = $('h2hBody');
  if (!body) return;
  if (!h2h) {
    body.innerHTML = `<div class="placeholder">${t('drawer.h2h_empty')}</div>`;
    return;
  }
  if (h2h.error) {
    body.innerHTML = `<div class="placeholder">✗ ${esc(h2h.error)}</div>`;
    return;
  }
  const CONF = { high:'intel.conf.high', medium:'intel.conf.medium', low:'intel.conf.low' };
  let h = '';
  if (h2h.summary) {
    h += `<div class="h2h-summary">${esc(h2h.summary)}</div>`;
  }
  if (h2h.recent_trend) {
    h += `<div class="h2h-trend">📈 ${esc(h2h.recent_trend)}</div>`;
  }
  const matches = h2h.matches || [];
  if (matches.length) {
    h += `<div class="h2h-subhead">${t('h2h.matches_label')}</div><ul class="h2h-list">`;
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
    + `<span class="conf-${esc(h2h.confidence || 'low')}">${t(CONF[h2h.confidence] || 'intel.conf.low')}</span>`
    + `</div>`;
  body.innerHTML = h || `<div class="placeholder">${t('drawer.h2h_empty')}</div>`;
}

// ── 反思 summary 格式化(按当前语言)─────────────
function formatReflectionSummary(parts) {
  if (!parts || !parts.length) return '';
  const sep = t('ref.summary_sep');
  return parts.map(p => {
    const name = I18N.current === 'zh-CN' ? (p.name_zh || p.team_en) : (p.team_en || p.name_zh);
    return t('ref.summary_team', { name, adj: p.n_adj, les: p.n_les });
  }).join(sep);
}

// ── 反思中的队伍名(按当前语言)────────────────────
function archTeamName(arch) {
  if (!arch) return '';
  if (I18N.current === 'zh-CN') return arch.name_zh || arch.team || arch.name;
  if (I18N.current === 'en') return arch.team || arch.name || arch.name_zh;
  return I18N.localTeam(arch.team || arch.name) || arch.name_zh;
}

// ── 队伍档案渲染(反思系统子模块) ─────────────────
function renderTeamArchive(arch, side, translated) {
  const CONF = { high:'intel.conf.high', medium:'intel.conf.medium', low:'intel.conf.low' };
  const showOrig = I18N.current !== 'zh-CN' && !translated;   // 缺翻译时才显示 "原文" 徽章
  const origNote = showOrig ? `<span class="ref-orig-note">${t('ref.original_lang_note')}</span>` : '';
  let h = `<div class="ref-team ref-${side}">`
    + `<div class="ref-team-head"><span class="ref-team-flag">${flag(arch.team || arch.name)}</span>`
    + `<span class="ref-team-name">${esc(archTeamName(arch))}</span>`
    + `${origNote}</div>`;
  const adj = arch.elo_adjustments || [];
  const adjNotesTr = (translated && translated.notes) || [];
  if (adj.length) {
    h += `<div class="ref-subhead">${t('ref.adjustments')}</div>`;
    for (let i = 0; i < adj.length; i++) {
      const a = adj[i];
      const sign = a.value > 0 ? '+' : '';
      const noteTxt = (translated && adjNotesTr[i]) ? adjNotesTr[i] : (a.note || '');
      h += `<div class="ref-adj">`
        + `<span class="ref-adj-key mono">${esc(a.key)}</span>`
        + `<span class="ref-adj-val">${sign}${a.value}</span>`
        + `<span class="ref-adj-conf conf-${esc(a.confidence)}">${t(CONF[a.confidence] || 'intel.conf.low')}</span>`
        + `<span class="ref-adj-note">${esc(noteTxt)}</span></div>`;
    }
  }
  const les = arch.recent_lessons || [];
  const lesTr = (translated && translated.lessons) || [];
  if (les.length) {
    h += `<div class="ref-subhead">${t('ref.lessons')}</div>`;
    for (let i = 0; i < les.length; i++) {
      const l = les[i];
      const lesTrI = lesTr[i] || {};
      const causesTr = lesTrI.causes || [];
      const vsName = I18N.current === 'zh-CN' ? (I18N.localTeam(l.vs) || l.vs)
                                              : I18N.localTeam(l.vs);
      h += `<div class="ref-lesson ${l.reusable ? 'reusable' : 'oneshot'}">`
        + `<div class="ref-lesson-head">`
        + `<span class="ref-lesson-date mono">${esc(l.date)}</span>`
        + `<span class="ref-lesson-vs">vs ${esc(vsName)} <small>${esc(l.tournament || '')}</small></span>`
        + `<span class="ref-lesson-score mono">${t('backtest.pred')} ${esc(l.prediction)} / ${t('backtest.actual')} ${esc(l.actual)}</span>`
        + `<span class="ref-lesson-tag ${l.reusable ? 'tag-reusable' : 'tag-oneshot'}">${l.reusable ? t('ref.reusable') : t('ref.oneshot')}</span>`
        + `</div><ul class="ref-causes">`;
      const causes = l.causes || [];
      for (let ci = 0; ci < causes.length; ci++) {
        const txt = causesTr[ci] || causes[ci];
        h += `<li>${esc(txt)}</li>`;
      }
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
    const lbl = $('statusLabel'); lbl.textContent = t('status.disconnected');
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

// ── 语言切换器 UI 逻辑(HTML 中已有按钮)──────────
function bindLangSwitcher() {
  const btn = $('langBtn');
  const menu = $('langMenu');
  if (!btn || !menu) return;
  // 渲染菜单(i18n.js 已暴露 LOCALES/META)
  function paintMenu() {
    menu.innerHTML = I18N.LOCALES.map(code => {
      const m = I18N.META[code];
      const active = code === I18N.current ? ' active' : '';
      return `<button class="lang-opt${active}" data-lang="${code}" type="button" role="option">`
        + `<span class="opt-flag">${m.flag}</span><span>${m.name}</span></button>`;
    }).join('');
    menu.querySelectorAll('.lang-opt').forEach(opt => {
      opt.addEventListener('click', () => {
        const code = opt.getAttribute('data-lang');
        if (code) {
          I18N.switchTo(code, true);
          try { localStorage.setItem('wc_locale_user', '1'); } catch {}
          updateLangBtn();
          closeLangMenu();
        }
      });
    });
  }
  function updateLangBtn() {
    const m = I18N.META[I18N.current];
    $('langCurrent').textContent = `${m.flag} ${m.name}`;
  }
  function openLangMenu() {
    menu.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
  }
  function closeLangMenu() {
    menu.hidden = true;
    btn.setAttribute('aria-expanded', 'false');
  }
  btn.addEventListener('click', e => {
    e.stopPropagation();
    if (menu.hidden) openLangMenu(); else closeLangMenu();
  });
  document.addEventListener('click', e => {
    if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) closeLangMenu();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeLangMenu(); });
  // 监听 i18n 变更 → 重渲染所有动态内容
  document.addEventListener('i18n:changed', () => {
    updateLangBtn();
    paintMenu();
    I18N.applyDOM();
    if (cache.schedule.length) renderSchedule();
    if (cache.status && Object.keys(cache.status).length) renderStatus();
    if (cache.champions && cache.champions.length) renderChampions();
    if (cache.groups && Object.keys(cache.groups).length) renderGroups();
    if (cache.backtest) renderBacktest();
    if (cache.market) renderMarket();
    if (cache.value) renderValue();
    // 刷新 Pro gate / 无付费 banner 文案
    document.querySelectorAll('.pro-gate-drawer').forEach(drawer => {
      const inner = drawer.querySelector('.pro-gate-drawer-inner');
      if (inner) {
        const spans = inner.querySelectorAll('span');
        if (spans[1]) spans[1].textContent = t('sub.pro_feature') + ' · ' + t('sub.upgrade_prompt');
        const btn = inner.querySelector('.pro-gate-btn-sm');
        if (btn) btn.textContent = t('sub.upgrade');
      }
      const features = drawer.querySelector('.pro-gate-features');
      if (features) features.innerHTML = t('sub.pro_features');
    });
    // 无付费价值页:重绘已结束赛事列表 + 刷新标题/副标题
    if ($('panel-value')?.querySelector('.bt-row')) {
      const pv = $('panel-value');
      const title = pv.querySelector('.panel-title');
      if (title) title.textContent = t('value.title');
      const sub = pv.querySelector('.vb-stat');
      if (sub) sub.textContent = t('value.unpaid_subtitle');
      renderValueUnpaid();
    }
  });
  paintMenu();
  updateLangBtn();
}

// ── 事件绑定 ──────────────────────────────────────
function bindEvents() {
  // tab 切换
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      $('panel-' + tab.dataset.tab).classList.add('active');
      // 切到夺冠/小组时立即拉一次
      if (tab.dataset.tab === 'champions') {
        getJSON('api/champions?top=20').then(d => { cache.champions = d.ranking; renderChampions(); }).catch(()=>{});
      } else if (tab.dataset.tab === 'groups') {
        getJSON('api/groups').then(d => { cache.groups = d; renderGroups(); }).catch(()=>{});
      } else if (tab.dataset.tab === 'backtest') {
        if (!cache.backtest) {
          getJSON('api/backtest').then(d => { cache.backtest = d; renderBacktest(); }).catch(()=>{});
        } else { renderBacktest(); }
      } else if (tab.dataset.tab === 'market') {
        loadMarketTab();
      } else if (tab.dataset.tab === 'value') {
        loadValueTab();
      }
    });
  });

  function loadMarketTab() {
    if (!isPaid) {
      // Prepend upgrade banner once
      if (!$('panel-market')?.querySelector('.pro-gate-drawer')) {
        const banner = document.createElement('div');
        banner.className = 'pro-gate-drawer';
        banner.style.marginBottom = '16px';
        banner.innerHTML = `<div class="pro-gate-drawer-inner">`
          + `<span class="pro-gate-icon-sm">⭐</span>`
          + `<span>${esc(t('sub.pro_feature'))} · ${esc(t('sub.upgrade_prompt'))}</span>`
          + `<button class="pro-gate-btn-sm" type="button">${esc(t('sub.upgrade'))}</button>`
          + `</div><div class="pro-gate-features" style="margin-top:6px">${t('sub.pro_features')}</div>`;
        $('panel-market').insertAdjacentElement('afterbegin', banner);
        banner.querySelector('.pro-gate-btn-sm')?.addEventListener('click', openUpgrade);
      }
    }
    if (!cache.market) {
      const wrap = $('mktList');
      if (wrap) wrap.innerHTML = `<div class="placeholder">${esc(t('empty.loading_market'))}</div>`;
      getJSON('api/market').then(d => { cache.market = d; renderMarket(); }).catch(()=>{});
    } else { renderMarket(); }
  }

  function loadValueTab() {
    if (!isPaid) {
      showValueUnpaid();
      return;
    }
    if (!cache.value) {
      getJSON('api/value-bets?min_edge=0.03' + emailSuffix()).then(d => { cache.value = d; renderValue(); }).catch(()=>{});
    } else { renderValue(); }
  }
  function showValueUnpaid() {
    const panel = $('panel-value');
    panel.innerHTML = ''
      + `<div class="pro-gate-drawer" style="margin-bottom:16px">`
      + `<div class="pro-gate-drawer-inner">`
      + `<span class="pro-gate-icon-sm">⭐</span>`
      + `<span>${esc(t('sub.pro_feature'))} · ${esc(t('sub.upgrade_prompt'))}</span>`
      + `<button class="pro-gate-btn-sm" type="button">${esc(t('sub.upgrade'))}</button>`
      + `</div><div class="pro-gate-features" style="margin-top:6px">${t('sub.pro_features')}</div></div>`
      + `<h2 class="panel-title">${esc(t('value.title'))}</h2>`
      + `<p class="vb-stat" style="margin:0 0 12px">${t('value.unpaid_subtitle')}</p>`
      + `<div id="vbList" class="bt-wrap"><div class="placeholder">${esc(t('empty.loading'))}</div></div>`;
    panel.querySelector('.pro-gate-btn-sm')?.addEventListener('click', openUpgrade);
    renderValueUnpaid();
  }
  function renderValueUnpaid() {
    const wrap = $('vbList');
    if (!wrap) return;
    const finished = (cache.schedule || []).filter(m => m.status === 'finished' && m.probs);
    if (!finished.length) {
      wrap.innerHTML = `<div class="placeholder">${esc(t('empty.no_finished'))}</div>`;
      return;
    }
    const zh = RESULT_ZH();
    const rows = finished.map(m => {
      const [h, a] = m.score || [0, 0];
      const result = h > a ? 'H' : (h < a ? 'A' : 'D');
      const predResult = m.probs ? (m.probs[0] > m.probs[1] && m.probs[0] > m.probs[2] ? 'H'
        : m.probs[2] > m.probs[1] && m.probs[2] > m.probs[0] ? 'A' : 'D') : '-';
      const hit = result === predResult;
      const ps = m.top_score;
      const psHtml = ps ? `<span class="bt-pscore">${ps[0]}-${ps[1]}</span>` : `<span class="bt-pscore">-</span>`;
      const hitCls = hit ? 'ok' : 'no';
      const mark = hit ? t('card.win_hit') : t('card.loss_prefix', { result: zh[result] || result });
      return `<div class="bt-row ${hitCls}">
        <span class="bt-date">${esc(m.date.slice(5))}</span>
        <span class="bt-team">${flag(m.home)} ${esc(teamName(m, 'home'))}</span>
        <span class="bt-scoreline">${t('backtest.pred')}${psHtml}<span class="bt-real">${t('backtest.actual')}${h}-${a}</span></span>
        <span class="bt-team right">${esc(teamName(m, 'away'))} ${flag(m.away)}</span>
        <span class="bt-mark ${hitCls}">${mark}</span>
      </div>`;
    }).join('');
    wrap.innerHTML = rows;
  }

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
      await fetch('api/refresh', { method: 'POST' });
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
  await I18N.ready;  // 等待 i18n.js 加载完翻译(避免 race)
  bindEvents();
  bindLangSwitcher();
  try { const cfg = await getJSON('api/config'); pollSec = cfg.poll_sec || 30; } catch {}
  countdown = pollSec;
  await poll();
  initSubscription();  // 异步订阅检查(不阻塞)
  tickTimer = setInterval(tick, 1000);
}
start();
