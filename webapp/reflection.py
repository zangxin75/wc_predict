"""webapp/reflection.py — AI 反思系统(实验性):队伍档案加载 + 单场反思查询。

设计分三步实施(本模块为第一步 MVP 基础设施):
  M1: teams/{Team}.yaml 队伍档案,字段:elo_adjustments / recent_lessons
  M2: 本模块加载 + 查询
  M3: /api/match 接 reflection 字段(仅展示,不改 probs)
  M4: 前端抽屉加开关,默认关,localStorage 持久化

第二步(预测层):把 elo_adjustments 转成实际 Elo 调整项,接入 predict_one
第三步(LLM 抓网):错场触发 → web search → 自动生成 lessons 写回 YAML

档案 schema(详见 teams/Morocco.yaml):
  name / name_zh / updated
  elo_adjustments: [{key, value, confidence, note}]
  recent_lessons:  [{date, tournament, vs, venue, prediction, actual,
                     miss_type, causes[], reusable, llm_source}]

i18n 翻译层:
  - teams/i18n/<team>.json 由 scripts/translate_lessons.py 预生成
  - get_match_reflection(locale) 合并 sidecar 翻译进 home_translated/away_translated
  - locale=zh-CN 时跳过翻译,直接返回 None(前端走原文 + Original: Chinese 徽章)
"""
import os
import glob
import json
import yaml

from webapp.i18n_translator import _hash as _hash_text, _is_chinese

HERE = os.path.dirname(os.path.abspath(__file__))
TEAMS_DIR = os.path.normpath(os.path.join(HERE, "..", "teams"))
I18N_DIR = os.path.normpath(os.path.join(HERE, "..", "teams", "i18n"))

_archive_cache = None   # {team_name_normalized: archive}
_mtime_seen = {}        # 跟踪文件 mtime,变更时重载
_i18n_cache = {}        # {team_slug: sidecar dict}
_i18n_mtime = {}        # {path: mtime}


def _normalize(name):
    return (name or "").strip().lower().replace(" ", "_")


def _slug(name):
    return (name or "").replace(" ", "_").replace("/", "_")


def _load_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
        team = d.get("name") or os.path.splitext(os.path.basename(path))[0]
        d["team"] = team
        return _normalize(team), d
    except (OSError, yaml.YAMLError):
        return None, None


def _maybe_reload(force=False):
    """按 mtime 增量重载(任何文件变更触发全量重建)。"""
    global _archive_cache
    if not os.path.isdir(TEAMS_DIR):
        _archive_cache = {}
        return
    files = glob.glob(os.path.join(TEAMS_DIR, "*.yaml")) + \
            glob.glob(os.path.join(TEAMS_DIR, "*.yml"))
    mtimes = {p: os.path.getmtime(p) for p in files}
    if not force and _archive_cache is not None and mtimes == _mtime_seen:
        return
    _archive_cache = {}
    _mtime_seen.clear()
    _mtime_seen.update(mtimes)
    for p in files:
        k, d = _load_file(p)
        if k and d:
            _archive_cache[k] = d


def get_team_archive(team):
    """单队档案;无档案返回 None。"""
    _maybe_reload()
    return _archive_cache.get(_normalize(team))


def _load_sidecar(team_slug: str) -> dict:
    """惰性加载单个队伍的 i18n 翻译 sidecar。按 mtime 缓存。"""
    path = os.path.join(I18N_DIR, f"{team_slug}.json")
    if not os.path.exists(path):
        return {}
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _i18n_cache.get(team_slug)
    if cached and _i18n_mtime.get(path) == mtime:
        return cached
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    _i18n_cache[team_slug] = d
    _i18n_mtime[path] = mtime
    return d


def _merge_translation(archive: dict, locale: str) -> dict | None:
    """从 sidecar 取 locale 对应的翻译,合并进 archive 结构。
    返回 {lessons: [{causes: [...translated...]}, ...], notes: [...translated...]}
    全部命中才返回;部分缺失则返回 None(前端回退原文 + Original 徽章)。

    sidecar 键 = SHA1(text)[:16](见 scripts/translate_lessons.py),value = 翻译文本。
    """
    if not archive or locale == "zh-CN":
        return None
    slug = _slug(archive.get("name") or archive.get("team"))
    sidecar = _load_sidecar(slug)
    if not sidecar:
        return None
    loc = (sidecar.get("locales") or {}).get(locale)
    if not loc:
        return None
    cause_map = loc.get("causes") or {}
    note_map = loc.get("notes") or {}

    lessons = []
    for les in (archive.get("recent_lessons") or []):
        causes_out = []
        causes_in = les.get("causes") or []
        for c in causes_in:
            if c and _is_chinese(c):
                key = _hash_text(c)
                if key in cause_map:
                    causes_out.append(cause_map[key])
                else:
                    return None  # 缺一条就整体放弃 → 前端显示原文 + Original 徽章
            else:
                causes_out.append(c)
        lessons.append({"causes": causes_out})
    notes_out = []
    for adj in (archive.get("elo_adjustments") or []):
        n = adj.get("note") or ""
        if n and _is_chinese(n):
            key = _hash_text(n)
            if key in note_map:
                notes_out.append(note_map[key])
            else:
                return None
        else:
            notes_out.append("")
    return {"lessons": lessons, "notes": notes_out}


def get_match_reflection(home, away, locale="zh-CN"):
    """单场反思数据:返回 {home_archive, away_archive, has_any, summary_parts, home_translated, away_translated, locale}。

    summary_parts: 给前端抽屉顶部一句话概述的原始数据,前端按当前语言格式化:
      [{"team_en": "France", "name_zh": "法国", "n_adj": 3, "n_les": 1}, ...]

    home_translated / away_translated: 当前 locale 下的翻译版本,结构:
      {"lessons": [{"causes": ["translated", ...]}, ...], "notes": ["translated", ...]}
    locale=zh-CN 或缺翻译时返回 None(前端继续走原文 + "Original: Chinese" 徽章)。
    """
    h = get_team_archive(home)
    a = get_team_archive(away)
    parts = []
    for team, arch in [(home, h), (away, a)]:
        if not arch:
            continue
        parts.append({
            "team_en": arch.get("name") or team,
            "name_zh": arch.get("name_zh") or team,
            "n_adj": len(arch.get("elo_adjustments") or []),
            "n_les": len(arch.get("recent_lessons") or []),
        })
    return {
        "home_archive": h,
        "away_archive": a,
        "has_any": bool(h or a),
        "summary_parts": parts,
        "home_translated": _merge_translation(h, locale),
        "away_translated": _merge_translation(a, locale),
        "locale": locale,
    }
