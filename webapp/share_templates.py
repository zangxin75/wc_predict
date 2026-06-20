"""share_templates.py — 动态生成 X 分享模板

数据每次调用时从 cache + teams.json 拉,保证内容不过期。
locale 决定队名翻译 + 模板文案语言。
"""

import json
import os
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TEAMS_I18N_PATH = os.path.join(HERE, "static", "i18n", "teams.json")
PROJECT_URL = "http://aijialidu.ticp.vip/wc/"

# 模板骨架(每语言一份),字段用 {name} 占位符运行时填
TEMPLATE_SKELETONS = {
    "en": {
        "a": (
            "🏆 2026 World Cup just kicked off.\n\n"
            "3 matches my predictor is watching next:\n"
            "  • {m1_home} vs {m1_away} ({m1_date})\n"
            "  • {m2_home} vs {m2_away} ({m2_date})\n"
            "  • {m3_home} vs {m3_away} ({m3_date})\n\n"
            "Backtest 128 WC games: 53.1% hit. +0.87 corr vs bookies.\n\n"
            "Free tier → try it\n\n"
        ) + PROJECT_URL,
        "b": (
            "WC2026 predictor I trust (after testing):\n\n"
            "▸ 10,000 Monte Carlo sims per match\n"
            "▸ Elo + Dixon-Coles, calibrated +0.87 vs market\n"
            "▸ Backtest 2018+2022: 53.1% argmax hit (128 games)\n"
            "▸ 100 matches covered, 68 upcoming\n\n"
            "AI pre-match intel. Value bet scanner. Free →\n\n"
        ) + PROJECT_URL,
        "c": (
            "{c_when}: {c_home} vs {c_away}. Market says {c_odds} odds.\n\n"
            "This predictor says {c_home} has {c_prob} chance.\n\n"
            "I don't bet blind. I bet with data. Free →\n\n"
        ) + PROJECT_URL,
    },
    "zh-CN": {
        "a": (
            "🏆 2026 世界杯开打。\n\n"
            "预测器接下来盯的 3 场:\n"
            "  • {m1_home} vs {m1_away}({m1_date})\n"
            "  • {m2_home} vs {m2_away}({m2_date})\n"
            "  • {m3_home} vs {m3_away}({m3_date})\n\n"
            "回测 128 场 WC:53.1% 命中。+0.87 与庄家校准。\n\n"
            "免费试 →\n\n"
        ) + PROJECT_URL,
        "b": (
            "我信任的这个世界杯预测器(测试后):\n\n"
            "▸ 每场 10,000 次蒙特卡洛模拟\n"
            "▸ Elo + Dixon-Coles,与市场校准 +0.87\n"
            "▸ 回测 2018+2022:53.1% argmax 命中(128 场)\n"
            "▸ 100 场比赛全覆盖,68 场未赛\n\n"
            "AI 赛前情报。价值下注扫描器。免费 →\n\n"
        ) + PROJECT_URL,
        "c": (
            "{c_when}:{c_home} vs {c_away}开打。庄家开 {c_odds} 赔率。\n\n"
            "这个预测器说 {c_home} 胜率 {c_prob}。\n\n"
            "我不盲赌。我用数据赌。免费试 →\n\n"
        ) + PROJECT_URL,
    },
    "es": {
        "a": (
            "🏆 Mundial 2026 acaba de arrancar.\n\n"
            "3 partidos que mi predictor vigila:\n"
            "  • {m1_home} vs {m1_away} ({m1_date})\n"
            "  • {m2_home} vs {m2_away} ({m2_date})\n"
            "  • {m3_home} vs {m3_away} ({m3_date})\n\n"
            "Backtest 128 partidos WC: 53.1% acierto. +0.87 corr vs casas.\n\n"
            "Gratis → pruébalo\n\n"
        ) + PROJECT_URL,
        "b": (
            "Predictor del Mundial 2026 en el que confío (tras probar):\n\n"
            "▸ 10,000 simulaciones Monte Carlo por partido\n"
            "▸ Elo + Dixon-Coles, calibrado +0.87 vs mercado\n"
            "▸ Backtest 2018+2022: 53.1% acierto argmax (128 partidos)\n"
            "▸ 100 partidos cubiertos, 68 por jugar\n\n"
            "Intel previa con IA. Escáner de value bets. Gratis →\n\n"
        ) + PROJECT_URL,
        "c": (
            "{c_when}: {c_home} vs {c_away}. Mercado ofrece {c_odds}.\n\n"
            "Este predictor dice que {c_home} tiene {c_prob} de probabilidad.\n\n"
            "No apuesto a ciegas. Apuesto con datos. Gratis →\n\n"
        ) + PROJECT_URL,
    },
    "pt": {
        "a": (
            "🏆 Copa 2026 acabou de começar.\n\n"
            "3 jogos que meu predictor está de olho:\n"
            "  • {m1_home} vs {m1_away} ({m1_date})\n"
            "  • {m2_home} vs {m2_away} ({m2_date})\n"
            "  • {m3_home} vs {m3_away} ({m3_date})\n\n"
            "Backtest 128 jogos WC: 53.1% acerto. +0.87 corr vs casas.\n\n"
            "Grátis → testa aí\n\n"
        ) + PROJECT_URL,
        "b": (
            "Predictor da Copa 2026 em que confio (depois de testar):\n\n"
            "▸ 10.000 simulações Monte Carlo por jogo\n"
            "▸ Elo + Dixon-Coles, calibrado +0.87 vs mercado\n"
            "▸ Backtest 2018+2022: 53.1% acerto argmax (128 jogos)\n"
            "▸ 100 jogos cobertos, 68 por jogar\n\n"
            "Intel pré-jogo com IA. Scanner de value bets. Grátis →\n\n"
        ) + PROJECT_URL,
        "c": (
            "{c_when}: {c_home} vs {c_away}. Mercado oferece {c_odds}.\n\n"
            "Esse predictor diz que {c_home} tem {c_prob} de chance.\n\n"
            "Não aposto no escuro. Aposto com dados. Grátis →\n\n"
        ) + PROJECT_URL,
    },
    "fr": {
        "a": (
            "🏆 CDM 2026 vient de démarrer.\n\n"
            "3 matchs suivis par mon predictor :\n"
            "  • {m1_home} vs {m1_away} ({m1_date})\n"
            "  • {m2_home} vs {m2_away} ({m2_date})\n"
            "  • {m3_home} vs {m3_away} ({m3_date})\n\n"
            "Backtest 128 matchs WC : 53.1% de réussite. +0.87 corr vs bookmakers.\n\n"
            "Gratuit → essaie\n\n"
        ) + PROJECT_URL,
        "b": (
            "Predictor CDM 2026 auquel je fais confiance (après test) :\n\n"
            "▸ 10 000 simulations Monte Carlo par match\n"
            "▸ Elo + Dixon-Coles, calibré +0.87 vs marché\n"
            "▸ Backtest 2018+2022 : 53.1% argmax (128 matchs)\n"
            "▸ 100 matchs couverts, 68 à venir\n\n"
            "Intel pré-match IA. Scanner value bets. Gratuit →\n\n"
        ) + PROJECT_URL,
        "c": (
            "{c_when} : {c_home} vs {c_away}. Marché : {c_odds}.\n\n"
            "Ce predictor donne à {c_home} {c_prob} de chances.\n\n"
            "Je ne parie pas à l'aveugle. Je parie avec les données. Gratuit →\n\n"
        ) + PROJECT_URL,
    },
    "ar": {
        "a": (
            "🏆 كأس العالم 2026 انطلقت.\n\n"
            "3 مباريات يراقبها المتنبئ:\n"
            "  • {m1_home} vs {m1_away} ({m1_date})\n"
            "  • {m2_home} vs {m2_away} ({m2_date})\n"
            "  • {m3_home} vs {m3_away} ({m3_date})\n\n"
            "اختبار رجعي 128 مباراة كأس عالم: 53.1% إصابة. +0.87 corr vs المراهنات.\n\n"
            "مجاني → جرّبه\n\n"
        ) + PROJECT_URL,
        "b": (
            "متنبئ كأس العالم 2026 أثق به (بعد الاختبار):\n\n"
            "▸ 10,000 محاكاة Monte Carlo لكل مباراة\n"
            "▸ Elo + Dixon-Coles، معايرة +0.87 مقابل السوق\n"
            "▸ اختبار رجعي 2018+2022: 53.1% إصابة argmax (128 مباراة)\n"
            "▸ 100 مباراة مغطاة، 68 قادمة\n\n"
            "معلومات ما قبل المباراة بالذكاء الاصطناعي. ماسح value bets. مجاني ←\n\n"
        ) + PROJECT_URL,
        "c": (
            "{c_when}: {c_home} vs {c_away}. السوق يقدم {c_odds}.\n\n"
            "هذا المتنبئ يقول أن {c_home} احتمال {c_prob}.\n\n"
            "لا أراهن أعمى. أراهن بالبيانات. مجاني →\n\n"
        ) + PROJECT_URL,
    },
}

# 翻译目标 → i18n key 映射
LOCALE_ALIASES = {"zh": "zh-CN", "zh-cn": "zh-CN"}


def _load_teams_i18n() -> dict:
    try:
        with open(TEAMS_I18N_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


_TEAMS_I18N = _load_teams_i18n()


def _locale_team(en_name: str, locale: str) -> str:
    """队名本地化:zh-CN → home_zh 优先,其他 → teams.json[en][locale]."""
    if not en_name:
        return ""
    if locale == "en":
        return en_name
    if locale.startswith("zh"):
        # 调用方应优先传 home_zh,但模板接口只用 en,这里走 teams.json
        tr = _TEAMS_I18N.get(en_name, {})
        return tr.get("zh-CN", en_name)
    tr = _TEAMS_I18N.get(en_name, {})
    return tr.get(locale, en_name)


def _resolve_locale(locale: str | None) -> str:
    if not locale:
        return "en"
    locale = locale.strip()
    locale = LOCALE_ALIASES.get(locale.lower(), locale)
    if locale not in TEMPLATE_SKELETONS:
        return "en"
    return locale


def _format_date(date_iso: str, locale: str) -> str:
    """日期格式:Jun 25 / 6月25日 / 25 jun / 25 juin / 25 jun etc."""
    if not date_iso:
        return ""
    try:
        d = datetime.strptime(date_iso, "%Y-%m-%d")
    except ValueError:
        return date_iso
    if locale.startswith("zh"):
        return f"{d.month}月{d.day}日"
    month_en = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
                "Sep", "Oct", "Nov", "Dec"][d.month - 1]
    if locale == "es":
        month_es = ["ene", "feb", "mar", "abr", "may", "jun",
                    "jul", "ago", "sep", "oct", "nov", "dic"][d.month - 1]
        return f"{d.day} {month_es}"
    if locale == "pt":
        month_pt = ["jan", "fev", "mar", "abr", "mai", "jun",
                    "jul", "ago", "set", "out", "nov", "dez"][d.month - 1]
        return f"{d.day} {month_pt}"
    if locale == "fr":
        month_fr = ["janv", "févr", "mars", "avr", "mai", "juin",
                    "juil", "août", "sept", "oct", "nov", "déc"][d.month - 1]
        return f"{d.day} {month_fr}"
    if locale == "ar":
        month_ar = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
                    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"][d.month - 1]
        return f"{d.day} {month_ar}"
    return f"{month_en} {d.day}"


def _days_until(date_iso: str, locale: str = "en") -> str:
    """距今天数(本地化短语:今天/明天/N 天后)。"""
    if not date_iso:
        return ""
    try:
        d = date.fromisoformat(date_iso)
        today = date.today()
        delta = (d - today).days
    except ValueError:
        return ""
    if delta <= 0:
        # locale-aware "today"
        today_words = {"en": "today", "zh-CN": "今天", "es": "hoy",
                       "pt": "hoje", "fr": "aujourd'hui", "ar": "اليوم"}
        return today_words.get(locale, "today")
    if delta == 1:
        tomorrow_words = {"en": "tomorrow", "zh-CN": "明天", "es": "mañana",
                          "pt": "amanhã", "fr": "demain", "ar": "غداً"}
        return tomorrow_words.get(locale, "tomorrow")
    # N 天后 / in N days
    return str(delta)


def _home_zh_name(match: dict, locale: str) -> str:
    """优先 home_zh,fallback 走 teams.json 本地化。"""
    home = match.get("home", "")
    if locale.startswith("zh"):
        return match.get("home_zh") or _locale_team(home, locale)
    return _locale_team(home, locale)


def _away_name(match: dict, locale: str) -> str:
    away = match.get("away", "")
    if locale.startswith("zh"):
        return match.get("away_zh") or _locale_team(away, locale)
    return _locale_team(away, locale)


def _flatten_odds(odds_map: dict | None) -> dict:
    """load_odds() 返回 {(year, home, away): (oh, od, oa)}。
    share 模板只需要按 (home, away) 查 → 在调用方按当前届(year=schedule 的 year)再索引。
    这里提供扁平化助手:取 (year, h, a) 拆成 {(h, a): tuple, ...},后写覆盖前写。
    """
    if not odds_map:
        return {}
    flat = {}
    for k, v in odds_map.items():
        if len(k) == 3:
            _, h, a = k
            flat[(h, a)] = v
    return flat


def build_share_templates(schedule: list, locale: str | None = None,
                          odds_map: dict | None = None) -> dict:
    """生成 3 个分享模板。

    schedule: cache.get_schedule() 返回的富化赛程
    odds_map: 可选 — 接受两种格式:
      • {(year, home, away): (oh, od, oa)}  ← load_odds() 原样
      • {(home, away): (oh, od, oa)}        ← 已被 flatten
    """
    loc = _resolve_locale(locale)
    skel = TEMPLATE_SKELETONS[loc]
    today_iso = date.today().isoformat()

    flat_odds = _flatten_odds(odds_map)

    # 接下来未赛(按日期+时间)
    upcoming = sorted(
        [m for m in schedule if m.get("status") != "finished"],
        key=lambda m: (m.get("date", "9999"), m.get("kickoff_bj", "99:99")),
    )

    # 模板 A:Top 3(若不足则填空)
    a = skel["a"]
    for i in range(1, 4):
        if i - 1 < len(upcoming):
            m = upcoming[i - 1]
            a = a.replace(f"{{m{i}_home}}", _home_zh_name(m, loc))
            a = a.replace(f"{{m{i}_away}}", _away_name(m, loc))
            a = a.replace(f"{{m{i}_date}}", _format_date(m.get("date", ""), loc))
        else:
            a = a.replace(f"{{m{i}_home}}", "TBD")
            a = a.replace(f"{{m{i}_away}}", "TBD")
            a = a.replace(f"{{m{i}_date}}", "TBD")

    # 模板 B:静态
    b = skel["b"]

    # 模板 C:挑下一场(优先有 odds 的)
    c = skel["c"]
    if upcoming:
        # 优先挑有赔率的最近一场
        pick = None
        for m in upcoming:
            key = (m.get("home"), m.get("away"))
            if flat_odds.get(key):
                pick = m
                break
        if pick is None:
            pick = upcoming[0]
        home_name = _home_zh_name(pick, loc)
        away_name = _away_name(pick, loc)
        when_str = _days_until(pick.get("date", ""), loc)
        # odds
        odds_str = "—"
        prob_str = "—"
        ot = flat_odds.get((pick.get("home"), pick.get("away")))
        if ot and ot[0]:
            odds_str = f"{ot[0]:.2f}"
            implied = 1.0 / ot[0] if ot[0] > 0 else 0
            prob_str = f"{implied * 100:.0f}%"
        c = c.replace("{c_home}", home_name)
        c = c.replace("{c_away}", away_name)
        c = c.replace("{c_when}", when_str)
        c = c.replace("{c_odds}", odds_str)
        c = c.replace("{c_prob}", prob_str)
    else:
        c = "WC2026 — " + PROJECT_URL

    return {
        "locale": loc,
        "templates": [
            {"id": "a", "text": a},
            {"id": "b", "text": b},
            {"id": "c", "text": c},
        ],
        "project_url": PROJECT_URL,
    }