/* ============================================================
   i18n.js · 多语言运行时
   加载翻译 → 注册 t() → 应用到 DOM → 检测地区 → 持久化
   ============================================================ */

const I18N = (() => {
  // ── 支持的 6 种语言 ──────────────────────────
  const LOCALES = ['en', 'zh-CN', 'es', 'pt', 'ar', 'fr'];
  const META = {
    'en':    { name: 'English',    flag: '🇺🇸' },
    'zh-CN': { name: '简体中文',    flag: '🇨🇳' },
    'es':    { name: 'Español',    flag: '🇪🇸' },
    'pt':    { name: 'Português',  flag: '🇵🇹' },
    'ar':    { name: 'العربية',     flag: '🇸🇦' },
    'fr':    { name: 'Français',   flag: '🇫🇷' }
  };

  // ── 国家 → 语言 映射(用 ipapi.co 返回的 country_code)─
  const COUNTRY_LANG = {
    CN:'zh-CN', TW:'zh-CN', HK:'zh-CN', MO:'zh-CN', SG:'zh-CN',
    ES:'es', MX:'es', AR:'es', CL:'es', CO:'es', PE:'es', VE:'es',
    UY:'es', PY:'es', BO:'es', EC:'es', CR:'es', DO:'es', GT:'es',
    HN:'es', SV:'es', NI:'es', PA:'es', CU:'es', PR:'es',
    PT:'pt', BR:'pt', AO:'pt', MZ:'pt',
    SA:'ar', AE:'ar', EG:'ar', QA:'ar', KW:'ar', OM:'ar', BH:'ar',
    JO:'ar', LB:'ar', IQ:'ar', YE:'ar', SY:'ar', MA:'ar', TN:'ar',
    LY:'ar', SD:'ar', DZ:'ar',
    FR:'fr', BE:'fr', CH:'fr', LU:'fr', MC:'fr'
  };
  // navigator.language → 语言(ISO 639-1 兼容匹配)
  const NAV_LANG = {
    'en':'en','en-US':'en','en-GB':'en','en-AU':'en','en-CA':'en',
    'zh':'zh-CN','zh-CN':'zh-CN','zh-Hans':'zh-CN','zh-Hans-CN':'zh-CN',
    'zh-TW':'zh-CN','zh-HK':'zh-CN','zh-SG':'zh-CN',
    'es':'es','es-ES':'es','es-MX':'es','es-AR':'es',
    'pt':'pt','pt-PT':'pt','pt-BR':'pt',
    'ar':'ar','ar-SA':'ar','ar-AE':'ar','ar-EG':'ar',
    'fr':'fr','fr-FR':'fr','fr-BE':'fr','fr-CA':'fr','fr-CH':'fr'
  };

  const I18N_VER = 9;                   // 翻译缓存版本;i18n JSON 变动时+1
  let current = 'en';
  let dict = {};
  let teams = {};                       // 48 队多语言译名(由 teams.json 加载)
  let teamsLoaded = false;
  let clubs = {};                       // 联赛俱乐部译名(由 clubs.json 加载)
  let clubsLoaded = false;
  let ipPromise = null;
  let userTz = null;                    // IANA 时区,如 "Asia/Shanghai" / "America/New_York"
  let readyResolve = null;
  const ready = new Promise(r => { readyResolve = r; });

  // ── 加载翻译文件(缓存)──────────────────────
  async function loadDict(locale) {
    if (dict[locale]) return dict[locale];
    try {
      const r = await fetch(`static/i18n/${locale}.json?v=${I18N_VER}`, { cache: 'no-cache' });
      if (!r.ok) throw new Error(`load ${locale} → ${r.status}`);
      dict[locale] = await r.json();
      return dict[locale];
    } catch (e) {
      console.warn(`[i18n] load ${locale} failed, fallback to en`, e);
      if (locale !== 'en') return loadDict('en');
      return {};
    }
  }

  // ── 加载队名翻译表(只一次)────────────────
  async function loadTeams() {
    if (teamsLoaded) return teams;
    try {
      const r = await fetch(`static/i18n/teams.json?v=${I18N_VER}`, { cache: 'no-cache' });
      if (r.ok) {
        const d = await r.json();
        teams = d;
        teamsLoaded = true;
      }
    } catch (e) {
      console.warn('[i18n] teams.json load failed', e);
    }
    return teams;
  }

  // ── 加载俱乐部译名表(只一次)──────────────
  async function loadClubs() {
    if (clubsLoaded) return clubs;
    try {
      const r = await fetch(`static/i18n/clubs.json?v=${I18N_VER}`, { cache: 'no-cache' });
      if (r.ok) {
        clubs = await r.json();
        clubsLoaded = true;
      }
    } catch (e) {
      console.warn('[i18n] clubs.json load failed', e);
    }
    return clubs;
  }

  // ── 俱乐部名本地化:enName → 当前语言译名,无译名回退英文 ──
  function localClub(enName) {
    if (!enName) return '';
    if (current === 'en') return enName;
    const tr = clubs[enName];
    if (tr && tr[current]) return tr[current];
    return enName;
  }

  // ── 队名本地化:enName → 当前语言译名,fallback 链
  function localTeam(enName) {
    if (!enName) return '';
    if (current === 'en') return enName;
    if (current === 'zh-CN') {
      // 走 EN_TO_ZH 链:优先用后端 home_zh,fallback 到 teams.json
      return enName;  // 调用方应优先用 m.home_zh
    }
    const tr = teams[enName];
    if (tr && tr[current]) return tr[current];
    return enName;
  }

  // ── 时区检测(链:localStorage 用户选择 → IP → 浏览器)
  function detectTz() {
    try {
      const saved = localStorage.getItem('wc_tz');
      if (saved) return saved;
    } catch {}
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    } catch { return 'UTC'; }
  }

  // IP 检测:ipapi.co 免费,15 万次/月,带超时,返回 {country_code, timezone, languages}
  async function fetchIPLocale() {
    if (ipPromise) return ipPromise;
    ipPromise = (async () => {
      try {
        const ctrl = new AbortController();
        const t = setTimeout(() => ctrl.abort(), 3000);
        const r = await fetch('https://ipapi.co/json/', { signal: ctrl.signal });
        clearTimeout(t);
        if (!r.ok) throw new Error(`ipapi ${r.status}`);
        const data = await r.json();
        const cc = (data.country_code || '').toUpperCase();
        return {
          country_code: cc,
          language: COUNTRY_LANG[cc] || null,
          timezone: data.timezone || null
        };
      } catch (e) {
        console.warn('[i18n] IP detect failed:', e.message);
        return null;
      }
    })();
    return ipPromise;
  }

  // 把 (date, bjTime) 统一转成 BJ 时刻的 ISO 字符串。
  // API 给的 kickoff_bj 可能是 "MM-DD HH:MM" 或纯 "HH:MM",要兼容两种格式。
  function parseBJ(date, bjTime) {
    if (!bjTime) return null;
    const s = String(bjTime).trim();
    // 形式 1:"MM-DD HH:MM" 或 "YYYY-MM-DD HH:MM" — 直接当完整 BJ 时刻
    if (s.includes(' ') || s.includes('T')) {
      // 缺少年份就补 2026
      const iso = /^\d{4}/.test(s) ? s : `2026-${s}`;
      // 把空格替成 T 加上 +08:00
      return iso.replace(' ', 'T') + (iso.includes('+') ? '' : '+08:00');
    }
    // 形式 2:纯 "HH:MM" — 用 date 拼成 YYYY-MM-DDTHH:MM:00+08:00
    if (/^\d{1,2}:\d{2}$/.test(s)) {
      return `${date}T${s}:00+08:00`;
    }
    return null;
  }

  // BJ 时间(UTC+8)→ 用户本地时间 HH:MM
  function localTime(date, bjTime) {
    const iso = parseBJ(date, bjTime);
    if (!iso) return bjTime || '';
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return bjTime || '';
      return d.toLocaleTimeString(undefined, {
        hour: '2-digit', minute: '2-digit', hour12: false, timeZone: userTz || undefined
      });
    } catch { return bjTime; }
  }

  // BJ 时间 → 用户本地日期(YYYY-MM-DD),可能跨天
  function localDate(date, bjTime) {
    const iso = parseBJ(date, bjTime);
    if (!iso) return date;
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return date;
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      return `${y}-${m}-${day}`;
    } catch { return date; }
  }

  // ── t():带 {var} 占位符的翻译查找 ─────────
  function t(key, params) {
    const d = dict[current] || {};
    let s = d[key];
    if (s == null) {
      // 缺失键:回退到 en,再退回 key 本身
      if (current !== 'en') s = (dict.en || {})[key];
      if (s == null) return key;
    }
    if (params) {
      s = s.replace(/\{(\w+)\}/g, (_, k) => (params[k] != null ? params[k] : `{${k}}`));
    }
    return s;
  }

  // ── 地区检测链:localStorage → navigator → 'en' ──
  function detectLocale() {
    try {
      const saved = localStorage.getItem('wc_locale');
      if (saved && LOCALES.includes(saved)) return saved;
    } catch {}
    const nav = (navigator.language || 'en').toLowerCase();
    for (const prefix of Object.keys(NAV_LANG).sort((a, b) => b.length - a.length)) {
      if (nav === prefix.toLowerCase() || nav.startsWith(prefix.toLowerCase() + '-')) {
        return NAV_LANG[prefix];
      }
    }
    return 'en';
  }

  // ── 应用翻译到 DOM(由 data-i18n 标注的元素)─
  function applyDOM() {
    // textContent / innerHTML 翻译
    document.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.getAttribute('data-i18n');
      const attr = el.getAttribute('data-i18n-attr');   // "title|placeholder|aria-label"
      const val = t(key);
      if (attr) {
        // 多属性:data-i18n-attr="title;placeholder"
        attr.split(/[;|]/).forEach(a => {
          a = a.trim();
          if (a) el.setAttribute(a, val);
        });
      } else {
        // 含 <b> 标签:用 innerHTML,否则 textContent
        if (val.indexOf('<') >= 0) el.innerHTML = val;
        else el.textContent = val;
      }
    });

    // html lang / dir(RTL)
    const dir = current === 'ar' ? 'rtl' : 'ltr';
    document.documentElement.setAttribute('lang', current);
    document.documentElement.setAttribute('dir', dir);
  }

  // ── 注入语言切换 UI(若 HTML 中没占位)──────
  function renderSwitcher() {
    let sw = document.getElementById('langSwitcher');
    if (!sw) return;
    sw.innerHTML = LOCALES.map(code => {
      const m = META[code];
      const active = code === current ? ' active' : '';
      return `<button class="lang-opt${active}" data-lang="${code}" type="button" title="${m.name}">${m.flag} ${m.name}</button>`;
    }).join('');
    sw.querySelectorAll('.lang-opt').forEach(btn => {
      btn.addEventListener('click', () => {
        const code = btn.getAttribute('data-lang');
        if (code && code !== current) switchTo(code, true);
      });
    });
  }

  // ── 切到指定 locale(持久化)──────────────
  async function switchTo(locale, persist) {
    if (!LOCALES.includes(locale)) return;
    current = locale;
    await loadDict(locale);
    applyDOM();
    renderSwitcher();
    if (persist) {
      try { localStorage.setItem('wc_locale', locale); localStorage.setItem('wc_locale_user', '1'); } catch {}
    }
    document.dispatchEvent(new CustomEvent('i18n:changed', { detail: { locale } }));
  }

  // ── 启动:同步 init(显示基础翻译),异步 IP 修正 ──
  async function init(opts = {}) {
    // 时区先于语言确定(localStorage → 浏览器 → UTC),用 default 即可
    userTz = detectTz();
    // 队名/俱乐部翻译表后台加载(非阻塞)
    loadTeams();
    loadClubs();
    const initial = opts.locale || detectLocale();
    await switchTo(initial, false);
    if (!opts.skipIP && !localStorage.getItem('wc_locale_user')) {
      const ip = await fetchIPLocale();
      if (ip) {
        // 时区:用户没手动覆盖 + IP 提供 → 用 IP
        if (ip.timezone && !localStorage.getItem('wc_tz_user')) {
          userTz = ip.timezone;
        }
        // 语言:IP 归属地语言不同 → 切换
        if (ip.language && ip.language !== current && LOCALES.includes(ip.language)) {
          await switchTo(ip.language, false);
        }
      }
    }
    if (readyResolve) readyResolve(current);
    return current;
  }

  return {
    init, t, switchTo, ready,
    get current() { return current; },
    get tz() { return userTz; },
    get teams() { return teams; },
    localTeam, localClub, loadClubs, localTime, localDate,
    LOCALES, META, applyDOM,
    get dict() { return dict; },
  };
})();

// 启动
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => I18N.init());
} else {
  I18N.init();
}
