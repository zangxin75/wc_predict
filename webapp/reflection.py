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
"""
import os
import glob
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
TEAMS_DIR = os.path.normpath(os.path.join(HERE, "..", "teams"))

_archive_cache = None   # {team_name_normalized: archive}
_mtime_seen = {}        # 跟踪文件 mtime,变更时重载


def _normalize(name):
    return (name or "").strip().lower().replace(" ", "_")


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


def get_match_reflection(home, away):
    """单场反思数据:返回 {home_archive, away_archive, has_any, summary}。

    summary 是给前端抽屉顶部一句话概述,如:
      "法国:3 条长期特征 + 1 条历史教训 / 摩洛哥:3 条特征 + 1 条教训"
    """
    h = get_team_archive(home)
    a = get_team_archive(away)
    parts = []
    for team, arch in [(home, h), (away, a)]:
        if not arch:
            continue
        n_adj = len(arch.get("elo_adjustments") or [])
        n_les = len(arch.get("recent_lessons") or [])
        parts.append(f"{arch.get('name_zh', team)}:{n_adj} 特征 + {n_les} 教训")
    return {
        "home_archive": h,
        "away_archive": a,
        "has_any": bool(h or a),
        "summary": " / ".join(parts) if parts else "",
    }
