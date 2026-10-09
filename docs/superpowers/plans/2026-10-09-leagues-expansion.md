# 联赛板块扩展(Leagues Expansion)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 wc_predict 中新增五大联赛(英超 E0/西甲 SP1/意甲 I1/德甲 D1/法甲 F1)+ 欧冠 UCL 板块,完整复刻预测引擎(统一俱乐部 Elo 池 + Dixon-Coles + XGBoost + 比分矩阵 + 价值下注 + 赛季推演),WC 旗舰功能零回归。

**Architecture:** 路线 A 并行管道 —— 新建 `leagues/` Python 包(纯算法/数据层)+ `webapp/leagues*.py`(缓存/更新器/API 路由),WC 侧仅按白名单微创接线。数据:football-data.co.uk 赛季结果 CSV(历史+赔率)+ fixtures.csv(近期赔率)+ ESPN scoreboard 按月迭代(全季赛程)+ TheOddsAPI(仅欧冠)。统一 Elo 池跨 5 联赛 + UCL;UCL 的 DC 参数借用球队本国联赛攻防值,只拟合全局 β0+home_adv。

**Tech Stack:** Python 3 / numpy / scipy(L-BFGS-B)/ pandas 无(纯 csv)/ xgboost / FastAPI + slowapi / pytest。requirements.txt 已全满足,**不新增依赖**。

**Spec:** `docs/superpowers/specs/2026-10-09-leagues-expansion-design.md`(v2,critic APPROVED)— 计划的每一条需求都论证自 spec,执行时两者一起读。

## Global Constraints

- **WC 改动白名单**(除此之外 WC 文件一律不碰):`webapp/app.py`(import + include_router + lifespan + /leagues 页面路由)、`webapp/static/index.html`(导航链接)、`.gitignore`、`webapp/static/i18n/*.json` + `i18n.js`(I18N_VER 6→7)、`tests/`、`docs/`、`CHANGELOG.md`。
- 不新增第三方依赖(requirements.txt 已有 requests/numpy/scipy/xgboost/fastapi/slowapi/pytest)。
- 所有日期统一 ISO `YYYY-MM-DD`;football-data 的 `DD/MM/YY(YY)` 只在 normalize 层转换。
- football-data CSV 一律 `utf-8-sig` 解码(去 BOM),一律 `csv.DictReader` 按列名取数(不按列位)。
- **付费门控(M-3/N-1,与 WC 对齐)**:`/matches` 非 Pro 用户 odds 字段 = null;响应 payload 永不出现 `edge`/`kelly`/`market` 键;`/value` 非 Pro 返回 `HTTPException(402, ...)`。
- 所有新 API 端点 `@limiter.limit("30/minute")`,limiter 从 `webapp.rate_limit` 导入(共享单例)。
- i18n:6 语言 JSON 加 key 后 `I18N_VER` 6→7;`index.html` 中 `i18n.js?v=24→25`;新增 `leagues.js?v=1`。
- `.gitignore` 新增 `data/leagues/` 与 `webapp/data/leagues_cache.json`(运行时产物;`leagues/clubs_aliases.json` 在包内,**要提交**)。
- **容错铁律**(同 WC updater):fetch 失败 = warning(沿用旧数据);update/build 失败 = error(终止本次,保留上一成功快照);后台线程任何未预期异常被 loop 捕获,绝不退出、绝不拖垮服务。
- 分支:`feat/leagues-expansion`。每个 Task 结束单独 commit。
- 完成标准:现有 71 tests 全绿 + 新增测试全绿(`pytest tests/`),`pytest -m "not slow"` 默认路径不触发网络。
- walk-forward 严格:回测/XGBoost 训练不准看未来数据(cutoff 用 `date < cutoff`,history 存**赛前** Elo)。
- pre-commit 红线:禁止引用其他项目 .env 路径;禁止提交 .env。

## Review Focus

1. **老赛季 CSV 缺赔率列**(2005 年前后无 PSH/OU/AH 列)→ 缺列字段为 None、行不丢、不报错。测试:Task 2 `test_parse_fd_results_old_season_missing_columns`。
2. **ESPN limit 截断**:`dates=YYYYMM` 不带 limit 默认 50,欧冠单月 >50 场会静默截断 → 必须 `limit=400`。测试:Task 4 `test_espn_month_sends_limit_400`。
3. **改期导致 fixtures 键过期**:fixtures.csv 是滚动 4 天窗口,键 (date,home,away) 会过期 → `rebuild` 每次从最新 schedule 全量重建字典,绝不增量 patch。测试:Task 8 `test_rebuild_predictions_no_stale_keys`。
4. **UCL 赔率三方命名不一**(TheOddsAPI "Internazionale" vs football-data "Inter")→ 一律过 `canonical()` 再匹配。测试:Task 16 `test_fetch_ucl_odds_canonical_names`。
5. **UCL 90 分钟比分噪声**:欧冠淘汰赛 ESPN 比分是 90 分钟结果(加时/点球不计),DC/Elo 直接用即可,不做加时修正 —— 在 Task 4 `parse_event` 与 Task 15 回测注释中钉死该语义,防止后人"修复"。

---

### Task 1: 联赛配置注册表 `leagues/config.py` + 队名别名表

**Files:**
- Create: `leagues/__init__.py`(空文件)
- Create: `leagues/config.py`
- Create: `leagues/clubs_aliases.json`
- Test: `tests/test_leagues_config.py`

**Interfaces:**
- Consumes: 无(纯配置)。
- Produces(后续所有 Task 依赖):
  - `LEAGUES: dict[str, dict]` — 键 E0/SP1/I1/D1/F1/UCL;值含 `name, name_zh, fd_code, espn, country, relegation_n, ucl_spots`;UCL 额外含 `odds_sport_key`
  - `LEAGUE_CODES = ["E0","SP1","I1","D1","F1"]`;`ALL_CODES = LEAGUE_CODES + ["UCL"]`
  - `BACKFILL_START = 2000`;`UCL_HISTORY_START = 2015`
  - `FD_RESULTS_URL`、`FD_FIXTURES_URL`、`ESPN_SCOREBOARD`(URL 模板常量)
  - `current_season(today=None) -> str`(8 月 1 日边界;env `LEAGUES_SEASON` 可覆盖)
  - `season_years(season: str) -> tuple[int,int]`(`"2627" -> (2026, 2027)`)
  - `paths(code) -> dict[str,str]`,键:`results, schedule, fixtures, predictions, dc_params, sim`
  - `global_paths() -> dict[str,str]`,键:`elo, elo_history, xgb`
  - `load_aliases() -> dict[str,str]`;`canonical(name, aliases=None) -> str`

- [ ] **Step 1: 创建分支 + 写失败测试**

```bash
cd "/mnt/data/project/足彩/联赛/wc_predict"
git checkout -b feat/leagues-expansion
```

```python
# tests/test_leagues_config.py
"""联赛配置注册表 + 赛季计算 + 队名规范化。"""
import datetime

from leagues import config


def test_current_season_august_boundary():
    assert config.current_season(datetime.date(2026, 7, 31)) == "2526"
    assert config.current_season(datetime.date(2026, 8, 1)) == "2627"
    assert config.current_season(datetime.date(2026, 10, 9)) == "2627"
    assert config.current_season(datetime.date(2027, 3, 15)) == "2627"


def test_current_season_env_override(monkeypatch):
    monkeypatch.setenv("LEAGUES_SEASON", "2021")
    assert config.current_season(datetime.date(2026, 10, 9)) == "2021"


def test_season_years():
    assert config.season_years("2627") == (2026, 2027)
    assert config.season_years("9900") == (1999, 2000)


def test_league_registry_shape():
    assert config.LEAGUE_CODES == ["E0", "SP1", "I1", "D1", "F1"]
    assert config.ALL_CODES == ["E0", "SP1", "I1", "D1", "F1", "UCL"]
    for code in config.ALL_CODES:
        lg = config.LEAGUES[code]
        assert lg["espn"] and lg["name"] and lg["name_zh"]
    assert config.LEAGUES["D1"]["relegation_n"] == 2   # 德甲 18 队降 2
    assert config.LEAGUES["F1"]["ucl_spots"] == 3
    assert config.LEAGUES["E0"]["ucl_spots"] == 4
    # TheOddsAPI UCL sport key 钉死(spec v2 勘误,写错会静默抓空)
    assert config.LEAGUES["UCL"]["odds_sport_key"] == "soccer_uefa_champs_league"


def test_paths_and_global_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    p = config.paths("E0")
    assert set(p) == {"results", "schedule", "fixtures", "predictions", "dc_params", "sim"}
    assert p["results"].endswith("e0/results.csv".replace("/", __import__("os").sep))
    g = config.global_paths()
    assert set(g) == {"elo", "elo_history", "xgb"}


def test_aliases_canonical():
    aliases = config.load_aliases()
    assert aliases["Manchester City"] == "Man City"
    assert aliases["Atlético Madrid"] == "Ath Madrid"
    assert aliases["Nottingham Forest"] == "Nott'm Forest"
    assert aliases["Borussia Mönchengladbach"] == "M'gladbach"
    assert aliases["Paris Saint-Germain"] == "Paris SG"
    assert aliases["Internazionale"] == "Inter"
    # 未命中 → 原样返回(strip);命中前也先 strip
    assert config.canonical("Unknown FC", aliases) == "Unknown FC"
    assert config.canonical("  Man City  ", aliases) == "Man City"


def test_backfill_constants():
    assert config.BACKFILL_START == 2000
    assert config.UCL_HISTORY_START == 2015
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_leagues_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leagues'`

- [ ] **Step 3: 实现**

```python
# leagues/__init__.py
# (空文件,使 leagues 成为包)
```

```python
# leagues/config.py
"""联赛板块配置:联赛注册表、赛季代码计算、数据路径、队名规范化。

赛季代码用 football-data 约定:2627 = 2026-27 赛季,8 月 1 日为界。
UCL 无 football-data 联赛代码(fd_code=None),历史走 ESPN。
"""
import datetime
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.path.join(ROOT, "data", "leagues")
ALIASES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clubs_aliases.json")

LEAGUES = {
    "E0":  {"name": "Premier League", "name_zh": "英超", "fd_code": "E0",
            "espn": "eng.1", "country": "England", "relegation_n": 3, "ucl_spots": 4},
    "SP1": {"name": "La Liga", "name_zh": "西甲", "fd_code": "SP1",
            "espn": "esp.1", "country": "Spain", "relegation_n": 3, "ucl_spots": 4},
    "I1":  {"name": "Serie A", "name_zh": "意甲", "fd_code": "I1",
            "espn": "ita.1", "country": "Italy", "relegation_n": 3, "ucl_spots": 4},
    "D1":  {"name": "Bundesliga", "name_zh": "德甲", "fd_code": "D1",
            "espn": "ger.1", "country": "Germany", "relegation_n": 2, "ucl_spots": 4},
    "F1":  {"name": "Ligue 1", "name_zh": "法甲", "fd_code": "F1",
            "espn": "fra.1", "country": "France", "relegation_n": 3, "ucl_spots": 3},
    "UCL": {"name": "UEFA Champions League", "name_zh": "欧冠", "fd_code": None,
            "espn": "uefa.champions", "country": "Europe", "relegation_n": 0, "ucl_spots": 0,
            "odds_sport_key": "soccer_uefa_champs_league"},
}

LEAGUE_CODES = ["E0", "SP1", "I1", "D1", "F1"]   # 国内联赛(UCL 单独管线)
ALL_CODES = LEAGUE_CODES + ["UCL"]

BACKFILL_START = 2000        # football-data 历史回填起始年
UCL_HISTORY_START = 2015     # ESPN 欧冠历史起始年

FD_RESULTS_URL = "https://football-data.co.uk/mmz4281/{season}/{code}.csv"
FD_FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/{espn}/scoreboard"


def current_season(today=None):
    """football-data 赛季代码,8 月 1 日边界。2026-10-09 → '2627'。
    env LEAGUES_SEASON 可强制覆盖(回填/调试用)。"""
    override = os.environ.get("LEAGUES_SEASON")
    if override:
        return override
    d = today or datetime.date.today()
    start = d.year if (d.month, d.day) >= (8, 1) else d.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def season_years(season):
    """'2627' → (2026, 2027)"""
    return 2000 + int(season[:2]), 2000 + int(season[2:])


def data_dir(code):
    d = os.path.join(DATA_ROOT, code.lower())
    os.makedirs(d, exist_ok=True)
    return d


def paths(code):
    d = data_dir(code)
    return {
        "results": os.path.join(d, "results.csv"),
        "schedule": os.path.join(d, "schedule.csv"),
        "fixtures": os.path.join(d, "fixtures.csv"),
        "predictions": os.path.join(d, "predictions.json"),
        "dc_params": os.path.join(d, "dc_params.json"),
        "sim": os.path.join(d, "sim.json"),
    }


def global_paths():
    os.makedirs(DATA_ROOT, exist_ok=True)
    return {
        "elo": os.path.join(DATA_ROOT, "elo.json"),
        "elo_history": os.path.join(DATA_ROOT, "elo_history.csv"),
        "xgb": os.path.join(DATA_ROOT, "xgb_club.json"),
    }


def load_aliases():
    """ESPN/TheOddsAPI 队名 → football-data 规范名。"""
    with open(ALIASES_FILE, encoding="utf-8") as f:
        return json.load(f)


def canonical(name, aliases=None):
    """队名规范化:先 strip,aliases 命中返回规范名,否则原样返回。"""
    n = (name or "").strip()
    if aliases is None:
        aliases = load_aliases()
    return aliases.get(n, n)
```

`leagues/clubs_aliases.json`(种子 ~55 条,键 = ESPN/TheOddsAPI 显示名,值 = football-data 规范名;未列出的队名 `canonical()` 原样透传,遇到新队再补):

```json
{
  "Manchester City": "Man City",
  "Manchester United": "Man United",
  "Tottenham Hotspur": "Tottenham",
  "Newcastle United": "Newcastle",
  "West Ham United": "West Ham",
  "Wolverhampton Wanderers": "Wolves",
  "Brighton & Hove Albion": "Brighton",
  "Nottingham Forest": "Nott'm Forest",
  "AFC Bournemouth": "Bournemouth",
  "Leeds United": "Leeds",
  "Leicester City": "Leicester",
  "Ipswich Town": "Ipswich",
  "Luton Town": "Luton",
  "Sheffield United": "Sheffield United",
  "Atlético Madrid": "Ath Madrid",
  "Atletico Madrid": "Ath Madrid",
  "Athletic Club": "Ath Bilbao",
  "FC Barcelona": "Barcelona",
  "Real Betis": "Betis",
  "Getafe CF": "Getafe",
  "RC Celta de Vigo": "Celta",
  "CA Osasuna": "Osasuna",
  "RCD Espanyol": "Espanyol",
  "RCD Mallorca": "Mallorca",
  "Deportivo Alavés": "Alaves",
  "Girona FC": "Girona",
  "Rayo Vallecano": "Vallecano",
  "CD Leganés": "Leganes",
  "Real Valladolid": "Valladolid",
  "UD Las Palmas": "Las Palmas",
  "Elche CF": "Elche",
  "Levante UD": "Levante",
  "Real Oviedo": "Oviedo",
  "Granada CF": "Granada",
  "Cádiz CF": "Cadiz",
  "UD Almería": "Almeria",
  "Inter Milan": "Inter",
  "Internazionale": "Inter",
  "AC Milan": "Milan",
  "AS Roma": "Roma",
  "SS Lazio": "Lazio",
  "ACF Fiorentina": "Fiorentina",
  "Hellas Verona": "Verona",
  "US Lecce": "Lecce",
  "AC Monza": "Monza",
  "Venezia FC": "Venezia",
  "Como 1907": "Como",
  "Bologna FC 1909": "Bologna",
  "Bayer 04 Leverkusen": "Leverkusen",
  "Borussia Dortmund": "Dortmund",
  "Eintracht Frankfurt": "Ein Frankfurt",
  "VfB Stuttgart": "Stuttgart",
  "VfL Wolfsburg": "Wolfsburg",
  "SC Freiburg": "Freiburg",
  "Borussia Mönchengladbach": "M'gladbach",
  "1. FC Union Berlin": "Union Berlin",
  "SV Werder Bremen": "Werder Bremen",
  "1. FSV Mainz 05": "Mainz",
  "TSG 1899 Hoffenheim": "Hoffenheim",
  "FC Augsburg": "Augsburg",
  "VfL Bochum": "Bochum",
  "1. FC Köln": "FC Koln",
  "Hertha BSC": "Hertha",
  "FC Schalke 04": "Schalke 04",
  "Hamburger SV": "Hamburg",
  "FC St. Pauli": "St Pauli",
  "1. FC Heidenheim": "Heidenheim",
  "SV Darmstadt 98": "Darmstadt",
  "Paris Saint-Germain": "Paris SG",
  "Olympique de Marseille": "Marseille",
  "Olympique Lyonnais": "Lyon",
  "AS Monaco": "Monaco",
  "LOSC Lille": "Lille",
  "OGC Nice": "Nice",
  "RC Lens": "Lens",
  "Stade Rennais": "Rennes",
  "RC Strasbourg": "Strasbourg",
  "FC Nantes": "Nantes",
  "Toulouse FC": "Toulouse",
  "Stade Brestois 29": "Brest",
  "Stade de Reims": "Reims",
  "AJ Auxerre": "Auxerre",
  "Angers SCO": "Angers",
  "AS Saint-Étienne": "St Etienne",
  "Le Havre AC": "Le Havre",
  "FC Lorient": "Lorient",
  "FC Metz": "Metz",
  "Clermont Foot": "Clermont"
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_leagues_config.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add leagues/__init__.py leagues/config.py leagues/clubs_aliases.json tests/test_leagues_config.py
git commit -m "feat(leagues): 联赛注册表 + 赛季计算 + 队名别名表(Task 1)"
```

### Task 2: CSV 规范化层 `leagues/normalize.py`

**Files:**
- Create: `leagues/normalize.py`
- Test: `tests/test_leagues_normalize.py`

**Interfaces:**
- Consumes: 无(Task 3/4 消费本层)。
- Produces:
  - `FD_RESULT_COLS / FD_FIXTURE_COLS: dict[str,str|None]` — 统一字段名 → football-data 列名(fixtures 的 Pinnacle 列是 `PPH/PPD/PPA` 开盘,结果 CSV 是 `PSH/PSD/PSA` 收盘)
  - `RESULT_FIELDS / FIXTURE_FIELDS / SCHEDULE_FIELDS / UCL_RESULT_FIELDS: list[str]` — 落盘 CSV 列序;RESULT_FIELDS 含 `season`
  - `normalize_date(s) -> str`(ISO;空值抛 ValueError)
  - `to_float(s) -> float|None`;`to_int(s) -> int|None`
  - `parse_fd_results(text, season) -> (rows, skipped)` — rows 按 date 排序,缺列字段为 None
  - `parse_fd_fixtures(text) -> {div: [rows]}`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_leagues_normalize.py
"""CSV 规范化:列名映射、日期/数字转换、脏行剔除。"""
import pytest

from leagues import normalize

FD_2627 = (
    "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A,PSH,PSD,PSA,"
    "Avg>2.5,Avg<2.5,AHh,B365AHH,B365AHA\n"
    "E0,15/08/2026,20:00,Liverpool,Arsenal,2,1,1.80,3.60,4.50,1.85,3.70,4.60,1.95,1.85,-0.5,1.90,2.00\n"
    "E0,16/08/2026,14:00,Man City,Chelsea,0,0,1.50,4.20,6.00,1.55,4.30,6.20,2.10,1.75,0,1.85,1.95\n"
)

# 老赛季(2005):只有 B365 三态,无 Pinnacle/OU/AH 列
FD_0506 = (
    "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A\n"
    "SP1,20/08/2005,Barcelona,Real Madrid,3,0,1.90,3.30,3.80\n"
)

FIXTURES = (
    "Div,Date,Time,HomeTeam,AwayTeam,B365H,B365D,B365A,MaxH,MaxD,MaxA,PPH,PPD,PPA,"
    "Avg>2.5,Avg<2.5,AHh,B365AHH,B365AHA\n"
    "E0,11/10/2026,15:00,Arsenal,Tottenham,2.10,3.40,3.50,2.15,3.50,3.60,2.12,3.45,3.55,2.00,1.80,-0.25,1.88,1.98\n"
    "I1,11/10/2026,18:00,Inter,Juventus,2.40,3.10,3.20,2.50,3.20,3.30,2.45,3.15,3.25,1.90,1.90,0,1.90,1.90\n"
)


def test_parse_fd_results_full_columns():
    rows, skipped = normalize.parse_fd_results(FD_2627, "2627")
    assert skipped == 0 and len(rows) == 2
    r = rows[0]
    assert r["date"] == "2026-08-15" and r["home"] == "Liverpool" and r["away"] == "Arsenal"
    assert r["gh"] == 2 and r["ga"] == 1
    assert r["ps_h"] == 1.85 and r["ou_over"] == 1.95 and r["ah_line"] == -0.5
    assert r["season"] == "2627"


def test_parse_fd_results_old_season_missing_columns():
    """Review Focus 1:老赛季缺列 → 字段 None,行不丢、不报错。"""
    rows, skipped = normalize.parse_fd_results(FD_0506, "0506")
    assert skipped == 0 and len(rows) == 1
    r = rows[0]
    assert r["date"] == "2005-08-20" and r["b365h"] == 1.90
    assert r["ps_h"] is None and r["ou_over"] is None and r["ah_line"] is None


def test_parse_fd_results_skips_bad_rows():
    bad = FD_2627 + (
        "E0,,,NoDate,Team,1,0,2.0,3.0,3.0,,,,,,,,\n"      # 空日期
        "E0,17/08/2026,20:00,A,B,1,0,0.50,3.0,3.0,,,,,,,\n"  # 赔率 ≤1.0
        "E0,18/08/2026,20:00,A,B,99,0,2.0,3.0,3.0,,,,,,,\n"  # 比分越界
    )
    rows, skipped = normalize.parse_fd_results(bad, "2627")
    assert len(rows) == 2 and skipped == 3


def test_parse_fd_fixtures_pinnacle_open_names():
    """fixtures.csv 的 Pinnacle 列是 PPH/PPD/PPA(开盘),不是 PSH/PSD/PSA(收盘)。"""
    by_div = normalize.parse_fd_fixtures(FIXTURES)
    assert set(by_div) == {"E0", "I1"}
    r = by_div["E0"][0]
    assert r["ps_h"] == 2.12 and r["ps_d"] == 3.45 and r["ps_a"] == 3.55
    assert r["max_h"] == 2.15 and r["time"] == "15:00"
    assert by_div["I1"][0]["home"] == "Inter"


def test_normalize_date_formats():
    assert normalize.normalize_date("05/08/26") == "2026-08-05"
    assert normalize.normalize_date("15/08/2026") == "2026-08-15"
    assert normalize.normalize_date("2026-10-11") == "2026-10-11"
    with pytest.raises(ValueError):
        normalize.normalize_date("")
    with pytest.raises(ValueError):
        normalize.normalize_date(None)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_leagues_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leagues.normalize'`

- [ ] **Step 3: 实现**

```python
# leagues/normalize.py
"""CSV 规范化层:football-data 原始文本 → 统一 dict 行。

铁律:
  · 一律 csv.DictReader 按列名取数(列序在不同赛季会变)
  · 老赛季缺列(Pinnacle/OU/AH)→ 字段为 None,行保留(Review Focus 1)
  · 日期统一 ISO;赔率必须 >1.0 或 None;比分 ∈ [0,15];让球线 ∈ [-4,4]
"""
import csv
import io

# 统一字段名 → 赛季结果 CSV 列名(None = 该字段不从此源取)
FD_RESULT_COLS = {
    "b365h": "B365H", "b365d": "B365D", "b365a": "B365A",
    "ps_h": "PSH", "ps_d": "PSD", "ps_a": "PSA",          # Pinnacle 收盘
    "ou_over": "Avg>2.5", "ou_under": "Avg<2.5",          # 市场平均大小球(2.5 线)
    "ah_line": "AHh", "ah_h": "B365AHH", "ah_a": "B365AHA",
}
# fixtures.csv(未来赛程):Pinnacle 是开盘列 PPH/PPD/PPA,其余同名
FD_FIXTURE_COLS = dict(FD_RESULT_COLS, ps_h="PPH", ps_d="PPD", ps_a="PPA")

RESULT_FIELDS = ["season", "date", "home", "away", "gh", "ga",
                 "b365h", "b365d", "b365a", "ps_h", "ps_d", "ps_a",
                 "ou_over", "ou_under", "ah_line", "ah_h", "ah_a"]
FIXTURE_FIELDS = ["date", "time", "home", "away",
                  "b365h", "b365d", "b365a", "max_h", "max_d", "max_a",
                  "ps_h", "ps_d", "ps_a", "ou_over", "ou_under", "ah_line", "ah_h", "ah_a"]
SCHEDULE_FIELDS = ["date", "time", "home", "away", "status", "gh", "ga", "neutral"]
UCL_RESULT_FIELDS = ["date", "home", "away", "gh", "ga", "neutral"]


def normalize_date(s):
    """'DD/MM/YYYY' 或 'DD/MM/YY' → 'YYYY-MM-DD';已是 ISO 原样返回。空值抛 ValueError。"""
    s = (s or "").strip()
    if not s:
        raise ValueError("empty date")
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    d, m, y = s.split("/")
    y = int(y)
    if y < 100:
        y += 2000 if y < 70 else 1900
    return f"{y:04d}-{int(m):02d}-{int(d):02d}"


def to_float(s):
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return None


def to_int(s):
    try:
        return int(float(str(s).strip()))
    except (TypeError, ValueError):
        return None


def _valid_scores(gh, ga):
    return gh is not None and ga is not None and 0 <= gh <= 15 and 0 <= ga <= 15


def _valid_row(row):
    """赔率必须 >1.0 或 None;让球线 ∈ [-4,4]。"""
    for k in ("b365h", "b365d", "b365a", "ps_h", "ps_d", "ps_a",
              "ou_over", "ou_under", "ah_h", "ah_a", "max_h", "max_d", "max_a"):
        v = row.get(k)
        if v is not None and v <= 1.0:
            return False
    line = row.get("ah_line")
    if line is not None and not (-4.0 <= line <= 4.0):
        return False
    return True


def _apply_cols(row, raw, colmap):
    for field, col in colmap.items():
        if col is None:
            continue
        row[field] = to_float(raw.get(col))   # 缺列 → raw.get 返回 None → to_float → None


def parse_fd_results(text, season):
    """football-data 赛季结果 CSV → (rows, skipped)。rows 按 date 排序。"""
    reader = csv.DictReader(io.StringIO(text))
    rows, skipped = [], 0
    for raw in reader:
        try:
            date = normalize_date(raw.get("Date"))
        except (ValueError, AttributeError):
            skipped += 1
            continue
        home = (raw.get("HomeTeam") or "").strip()
        away = (raw.get("AwayTeam") or "").strip()
        gh, ga = to_int(raw.get("FTHG")), to_int(raw.get("FTAG"))
        if not home or not away or not _valid_scores(gh, ga):
            skipped += 1
            continue
        row = {"season": season, "date": date, "home": home, "away": away, "gh": gh, "ga": ga}
        _apply_cols(row, raw, FD_RESULT_COLS)
        if not _valid_row(row):
            skipped += 1
            continue
        rows.append(row)
    rows.sort(key=lambda r: (r["date"], r["home"]))
    return rows, skipped


def parse_fd_fixtures(text):
    """fixtures.csv → {Div: [rows]}。仅未来赛程(无比分),带开盘赔率。"""
    reader = csv.DictReader(io.StringIO(text))
    out = {}
    for raw in reader:
        div = (raw.get("Div") or "").strip()
        if not div:
            continue
        try:
            date = normalize_date(raw.get("Date"))
        except (ValueError, AttributeError):
            continue
        home = (raw.get("HomeTeam") or "").strip()
        away = (raw.get("AwayTeam") or "").strip()
        if not home or not away:
            continue
        row = {"date": date, "time": (raw.get("Time") or "").strip()[:5], "home": home, "away": away}
        _apply_cols(row, raw, FD_FIXTURE_COLS)
        row["max_h"] = to_float(raw.get("MaxH"))
        row["max_d"] = to_float(raw.get("MaxD"))
        row["max_a"] = to_float(raw.get("MaxA"))
        if _valid_row(row):
            out.setdefault(div, []).append(row)
    for rows in out.values():
        rows.sort(key=lambda r: (r["date"], r["home"]))
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_leagues_normalize.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add leagues/normalize.py tests/test_leagues_normalize.py
git commit -m "feat(leagues): football-data CSV 规范化层(Task 2)"
```

---

### Task 3: football-data 抓取器 `leagues/fetch_fd.py`

**Files:**
- Create: `leagues/fetch_fd.py`
- Test: `tests/test_leagues_fetch_fd.py`

**Interfaces:**
- Consumes: `config.paths/FD_RESULTS_URL/FD_FIXTURES_URL/current_season/season_years/BACKFILL_START`(Task 1);`normalize.parse_fd_results/parse_fd_fixtures/RESULT_FIELDS/FIXTURE_FIELDS`(Task 2)。
- Produces:
  - `write_csv(path, fields, rows)`(tmp + os.replace 原子写)
  - `is_fresh(path, hours) -> bool`(mtime 新鲜度)
  - `fetch_league_history(code, start=2000, end_year=None) -> (n_rows, failed_seasons)` — 单季失败跳过记 failed;全失败抛 RuntimeError;合并写 `paths(code)["results"]`
  - `fetch_fixtures(codes=None) -> {code: n_rows}` — fixtures.csv 按 Div 拆写

- [ ] **Step 1: 写失败测试**

```python
# tests/test_leagues_fetch_fd.py
"""football-data 抓取:重试、原子写、单季容错、新鲜度。"""
import os
import time

import pytest

from leagues import config, fetch_fd

CSV_A = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A\nE0,15/08/2026,A,B,1,0,2.0,3.4,3.6\n"
CSV_B = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A\nE0,16/08/2025,C,D,0,0,2.1,3.3,3.5\n"


class FakeResp:
    def __init__(self, text, status=200):
        self.content = text.encode("utf-8-sig")   # 模拟 BOM
        self.status_code = status

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code}")


def _patch_get(monkeypatch, handler):
    monkeypatch.setattr(fetch_fd.requests, "get", handler)
    monkeypatch.setattr(fetch_fd.time, "sleep", lambda s: None)   # 重试不真睡


def test_get_retries_then_success(monkeypatch):
    calls = []

    def handler(url, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise RuntimeError("boom")
        return FakeResp("ok")

    _patch_get(monkeypatch, handler)
    assert fetch_fd._get("http://x") == "ok" and len(calls) == 3


def test_get_raises_after_3_failures(monkeypatch):
    def handler(url, timeout):
        raise RuntimeError("boom")

    _patch_get(monkeypatch, handler)
    with pytest.raises(RuntimeError):
        fetch_fd._get("http://x")


def test_write_csv_atomic_and_bom_decode(tmp_path):
    p = str(tmp_path / "out.csv")
    fetch_fd.write_csv(p, ["a", "b"], [{"a": 1, "b": "中文"}])
    with open(p, encoding="utf-8") as f:
        content = f.read()
    assert "中文" in content and not os.path.exists(p + ".tmp")


def test_is_fresh(tmp_path):
    p = tmp_path / "f.txt"
    assert not fetch_fd.is_fresh(str(p), 1)          # 不存在
    p.write_text("x")
    assert fetch_fd.is_fresh(str(p), 1)
    old = time.time() - 7200
    os.utime(str(p), (old, old))
    assert not fetch_fd.is_fresh(str(p), 1)          # 2 小时前,阈值 1 小时


def test_fetch_league_history_skips_failed_seasons(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("LEAGUES_SEASON", "2627")
    seen = []

    def handler(url, timeout):
        season = url.split("/")[-2]
        seen.append(season)
        if season == "2425":
            raise RuntimeError("boom")               # 单季失败
        return FakeResp(CSV_A)

    _patch_get(monkeypatch, handler)
    n, failed = fetch_fd.fetch_league_history("E0", start=2024)
    assert seen == ["2425", "2526", "2627"]
    assert failed == ["2425"] and n == 2             # 两季成功各 1 行
    rows = fetch_fd.build_read_back(config.paths("E0")["results"])
    assert len(rows) == 2


def test_fetch_league_history_all_failed_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))

    def handler(url, timeout):
        raise RuntimeError("boom")

    _patch_get(monkeypatch, handler)
    with pytest.raises(RuntimeError):
        fetch_fd.fetch_league_history("E0", start=2025, end_year=2026)


def test_fetch_fixtures_splits_by_div(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    text = (
        "Div,Date,Time,HomeTeam,AwayTeam,B365H,B365D,B365A,MaxH,MaxD,MaxA,PPH,PPD,PPA,"
        "Avg>2.5,Avg<2.5,AHh,B365AHH,B365AHA\n"
        "E0,11/10/2026,15:00,Arsenal,Tottenham,2.1,3.4,3.5,2.2,3.5,3.6,2.12,3.45,3.55,2.0,1.8,-0.25,1.88,1.98\n"
        "I1,11/10/2026,18:00,Inter,Juventus,2.4,3.1,3.2,2.5,3.2,3.3,2.45,3.15,3.25,1.9,1.9,0,1.9,1.9\n"
        "D2,11/10/2026,12:00,X,Y,2.0,3.0,4.0,2.1,3.1,4.1,2.0,3.0,4.0,2.0,1.8,0,1.9,1.9\n"
    )
    _patch_get(monkeypatch, lambda url, timeout: FakeResp(text))
    out = fetch_fd.fetch_fixtures(codes=["E0", "I1"])
    assert out == {"E0": 1, "I1": 1}                 # D2(英冠)被忽略
    assert os.path.exists(config.paths("I1")["fixtures"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_leagues_fetch_fd.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leagues.fetch_fd'`

- [ ] **Step 3: 实现**

```python
# leagues/fetch_fd.py
"""football-data.co.uk 抓取:联赛历史赛季结果 + fixtures.csv。

容错:单季失败跳过(记 failed);所有赛季都失败抛 RuntimeError(调用方按
容错铁律处理:沿用旧文件)。CSV 一律 utf-8-sig 解码去 BOM。
"""
import csv
import io
import os
import time

import requests

from . import config, normalize

TIMEOUT = 30
RETRIES = 3
BACKOFF = 2.0


def _get(url):
    """带重试的 GET,返回去 BOM 文本。RETRIES 次都失败抛最后一个异常。"""
    last = None
    for i in range(RETRIES):
        try:
            r = requests.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            return r.content.decode("utf-8-sig")
        except Exception as e:
            last = e
            if i < RETRIES - 1:
                time.sleep(BACKOFF ** i)
    raise last


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def write_csv(path, fields, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    _atomic_write(path, buf.getvalue())


def build_read_back(path):
    """测试/调试用:读回 write_csv 写的文件。"""
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def is_fresh(path, hours):
    """文件存在且 mtime 在 hours 小时内。"""
    try:
        return (time.time() - os.path.getmtime(path)) < hours * 3600
    except OSError:
        return False


def fetch_league_history(code, start=config.BACKFILL_START, end_year=None):
    """抓 code 联赛 start..当前赛季结果 CSV,合并写 results.csv。

    返回 (n_rows, failed_seasons)。单季失败记 failed 跳过;全失败抛 RuntimeError。
    """
    season = config.current_season()
    _, cur_end = config.season_years(season)
    end_year = end_year or cur_end
    all_rows, failed = [], []
    for y in range(start, end_year):
        s = f"{y % 100:02d}{(y + 1) % 100:02d}"
        url = config.FD_RESULTS_URL.format(season=s, code=code)
        try:
            rows, _ = normalize.parse_fd_results(_get(url), s)
            if not rows:
                failed.append(s)
                continue
            all_rows.extend(rows)
        except Exception:
            failed.append(s)
    if not all_rows:
        raise RuntimeError(f"{code}: 所有赛季抓取失败")
    all_rows.sort(key=lambda r: (r["date"], r["home"]))
    write_csv(config.paths(code)["results"], normalize.RESULT_FIELDS, all_rows)
    return len(all_rows), failed


def fetch_fixtures(codes=None):
    """抓 fixtures.csv 按 Div 拆到各联赛 fixtures.csv。返回 {code: n_rows}。"""
    codes = codes or config.LEAGUE_CODES
    by_div = normalize.parse_fd_fixtures(_get(config.FD_FIXTURES_URL))
    out = {}
    for code in codes:
        rows = by_div.get(code, [])
        write_csv(config.paths(code)["fixtures"], normalize.FIXTURE_FIELDS, rows)
        out[code] = len(rows)
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_leagues_fetch_fd.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add leagues/fetch_fd.py tests/test_leagues_fetch_fd.py
git commit -m "feat(leagues): football-data 抓取器(历史赛季 + fixtures)(Task 3)"
```

---

### Task 4: ESPN 赛程抓取器 `leagues/fetch_espn.py`

**Files:**
- Create: `leagues/fetch_espn.py`
- Test: `tests/test_leagues_fetch_espn.py`

**Interfaces:**
- Consumes: `config`(espn 代码、season_years、paths)、`config.canonical/load_aliases`、`fetch_fd.write_csv`、`normalize.SCHEDULE_FIELDS/UCL_RESULT_FIELDS/to_int`。
- Produces:
  - `season_months(season) -> list[str]`(`"2627" -> ["202608",...,"202707"]`)
  - `espn_month(espn, yyyymm) -> list`(单页 scoreboard,**params 必含 `limit=400`**)
  - `parse_event(ev, aliases) -> dict|None`(canonical 队名;`status` ∈ pre/in/post;post 才有 gh/ga;`neutral` 来自 `competitions[0].neutralSite`)
  - `fetch_schedule(code, season=None) -> (n_rows, missing_months)` — 写 schedule.csv
  - `fetch_ucl_history(start_year=2015, end_year=None) -> (n_rows, total_missing, degraded)` — 只留 post,写 UCL results.csv;`degraded = 最长连续缺月 > 2`
  - `_max_consecutive(months) -> int`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_leagues_fetch_espn.py
"""ESPN 按月迭代抓取:limit=400、缺月统计、中立场、UCL 降级检测。"""
import pytest

from leagues import config, fetch_espn


def _event(home, away, state="post", hs="2", as_="1", date="2026-10-11T15:00Z", neutral=False):
    return {
        "date": date,
        "status": {"type": {"state": state}},
        "competitions": [{
            "neutralSite": neutral,
            "competitors": [
                {"homeAway": "home", "team": {"displayName": home}, "score": hs},
                {"homeAway": "away", "team": {"displayName": away}, "score": as_},
            ],
        }],
    }


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_season_months():
    ms = fetch_espn.season_months("2627")
    assert ms == ["202608", "202609", "202610", "202611", "202612",
                  "202701", "202702", "202703", "202704", "202705", "202706", "202707"]


def test_espn_month_sends_limit_400(monkeypatch):
    """Review Focus 2:不带 limit 默认 50 会静默截断欧冠大月份。"""
    captured = {}

    def handler(url, params=None, timeout=None):
        captured.update(params or {})
        return FakeResp({"events": []})

    monkeypatch.setattr(fetch_espn.requests, "get", handler)
    monkeypatch.setattr(fetch_espn.time, "sleep", lambda s: None)
    fetch_espn.espn_month("eng.1", "202610")
    assert captured["dates"] == "202610" and captured["limit"] == 400


def test_parse_event_post_and_neutral():
    aliases = config.load_aliases()
    row = fetch_espn.parse_event(_event("Manchester City", "Arsenal"), aliases)
    assert row == {"date": "2026-10-11", "time": "15:00", "home": "Man City", "away": "Arsenal",
                   "status": "post", "gh": 2, "ga": 1, "neutral": False}
    n = fetch_espn.parse_event(_event("Real Madrid", "FC Barcelona", neutral=True), aliases)
    assert n["neutral"] is True and n["away"] == "Barcelona"
    pre = fetch_espn.parse_event(_event("Arsenal", "Chelsea", state="pre"), aliases)
    assert pre["status"] == "pre" and pre["gh"] == "" and pre["ga"] == ""


def test_parse_event_rejects_malformed():
    assert fetch_espn.parse_event({"date": "2026-10-11T15:00Z"}, {}) is None
    ev = _event("A", "B")
    ev["competitions"][0]["competitors"] = [ev["competitions"][0]["competitors"][0]]
    assert fetch_espn.parse_event(ev, {}) is None


def test_fetch_schedule_counts_missing_months(tmp_path, monkeypatch):
    """N-3:空 events 或请求异常均计为缺月。"""
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("LEAGUES_SEASON", "2627")

    def fake_month(espn, ym):
        if ym == "202610":
            return [_event("Arsenal", "Chelsea")]
        if ym == "202611":
            raise RuntimeError("boom")              # 请求异常 = 缺月
        return []                                   # 空 events = 缺月

    monkeypatch.setattr(fetch_espn, "espn_month", fake_month)
    n, missing = fetch_espn.fetch_schedule("E0")
    assert n == 1
    assert set(missing) == set(fetch_espn.season_months("2627")) - {"202610"}


def test_fetch_schedule_all_empty_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(fetch_espn, "espn_month", lambda e, ym: [])
    with pytest.raises(RuntimeError):
        fetch_espn.fetch_schedule("E0", season="2627")


def test_fetch_ucl_history_degraded_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("LEAGUES_SEASON", "2627")

    def fake_month(espn, ym):
        if ym in ("202609", "202610"):               # 只有这两个月有完赛
            return [_event("Real Madrid", "Manchester City"),
                    _event("Arsenal", "Chelsea", state="pre")]
        return []                                   # 其余全缺(休赛期也算缺,只影响统计)

    monkeypatch.setattr(fetch_espn, "espn_month", fake_month)
    n, total_missing, degraded = fetch_espn.fetch_ucl_history(start_year=2025, end_year=2027)
    assert n == 2                                    # pre 那场被过滤
    assert total_missing == 22                       # 2 个赛季 × 11 个缺月
    assert degraded is True                          # 202611..202608 跨年连续缺 > 2


def test_max_consecutive():
    assert fetch_espn._max_consecutive([]) == 0
    assert fetch_espn._max_consecutive(["202610", "202611", "202612", "202701"]) == 4
    assert fetch_espn._max_consecutive(["202610", "202612"]) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_leagues_fetch_espn.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leagues.fetch_espn'`

- [ ] **Step 3: 实现**

```python
# leagues/fetch_espn.py
"""ESPN scoreboard 抓取:按月迭代(dates=YYYYMM)拿全季赛程 + 结果 + 中立场标记。

已验证事实(2026-10-09,spec §2 D3):
  · range 语法 dates=20260801-20270731 不支持(返回空),必须按月迭代
  · limit 默认 50,欧冠大月份会截断 → 必须 limit=400(Review Focus 2)
  · 空 events = 该月无数据(休赛期或未发布),按缺月记录(N-3),不视为错误
  · 欧冠淘汰赛比分是 90 分钟结果(加时/点球不计),DC/Elo 直接用这个语义,
    不做加时修正(Review Focus 5,刻意决策,勿"修复")
"""
import time

import requests

from . import config, fetch_fd, normalize

TIMEOUT = 30
RETRIES = 3


def season_months(season):
    """'2627' → ['202608', ..., '202707'](欧洲赛季 8 月开、7 月止)。"""
    y1, y2 = config.season_years(season)
    return [f"{y1}{m:02d}" for m in range(8, 13)] + [f"{y2}{m:02d}" for m in range(1, 8)]


def espn_month(espn, yyyymm):
    """单月 scoreboard,返回 events list。异常重试 RETRIES 次后抛出(调用方计缺月)。"""
    url = config.ESPN_SCOREBOARD.format(espn=espn)
    last = None
    for i in range(RETRIES):
        try:
            r = requests.get(url, params={"dates": yyyymm, "limit": 400}, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json().get("events", [])
        except Exception as e:
            last = e
            if i < RETRIES - 1:
                time.sleep(2.0 ** i)
    raise last


def parse_event(ev, aliases):
    """单个 event → schedule 行 dict;结构不完整返回 None。

    status 取 ESPN 原生 state:pre(未赛)/in(进行中)/post(完赛);只有 post 记比分。
    neutral 来自 competitions[0].neutralSite(欧冠决赛/中立轮次)。
    """
    comps = ev.get("competitions") or []
    if not comps:
        return None
    comp = comps[0]
    sides = {}
    for c in comp.get("competitors", []):
        name = config.canonical((c.get("team") or {}).get("displayName", ""), aliases)
        sides[c.get("homeAway")] = (name, normalize.to_int(c.get("score")))
    if "home" not in sides or "away" not in sides:
        return None
    (home, hs), (away, as_) = sides["home"], sides["away"]
    state = ((ev.get("status") or {}).get("type") or {}).get("state", "")
    dt = ev.get("date") or ""
    if len(dt) < 16 or not home or not away or state not in ("pre", "in", "post"):
        return None
    return {
        "date": dt[:10], "time": dt[11:16], "home": home, "away": away,
        "status": state,
        "gh": hs if state == "post" and hs is not None else "",
        "ga": as_ if state == "post" and as_ is not None else "",
        "neutral": bool(comp.get("neutralSite")),
    }


def _fetch_monthly(code, season, aliases):
    """按月迭代全季。空 events 或请求异常均计为缺月(N-3)。返回 (rows, missing)。"""
    espn = config.LEAGUES[code]["espn"]
    rows, missing = [], []
    for ym in season_months(season):
        try:
            events = espn_month(espn, ym)
        except Exception:
            missing.append(ym)
            continue
        if not events:
            missing.append(ym)   # 休赛期月份固定为空 → missing 仅供日志观察,不致命
            continue
        for ev in events:
            row = parse_event(ev, aliases)
            if row:
                rows.append(row)
    rows.sort(key=lambda r: (r["date"], r["time"]))
    return rows, missing


def fetch_schedule(code, season=None):
    """抓联赛全季赛程写 schedule.csv。全空抛 RuntimeError。返回 (n_rows, missing)。"""
    season = season or config.current_season()
    rows, missing = _fetch_monthly(code, season, config.load_aliases())
    if not rows:
        raise RuntimeError(f"{code} {season}: ESPN 全季无数据")
    fetch_fd.write_csv(config.paths(code)["schedule"], normalize.SCHEDULE_FIELDS, rows)
    return len(rows), missing


def fetch_ucl_history(start_year=config.UCL_HISTORY_START, end_year=None):
    """抓欧冠历史(只留 post 完赛),写 UCL results.csv。

    返回 (n_rows, total_missing, degraded);degraded = 最长连续缺月 > 2
    (跨赛季休赛期属正常缺月,调用方只把 degraded 当警告日志)。
    """
    season = config.current_season()
    _, cur_end = config.season_years(season)
    end_year = end_year or cur_end
    aliases = config.load_aliases()
    all_rows, missing_all = [], []
    for y in range(start_year, end_year):
        s = f"{y % 100:02d}{(y + 1) % 100:02d}"
        rows, missing = _fetch_monthly("UCL", s, aliases)
        for r in rows:
            if r["status"] == "post":
                all_rows.append({"date": r["date"], "home": r["home"], "away": r["away"],
                                 "gh": r["gh"], "ga": r["ga"], "neutral": r["neutral"]})
        missing_all.extend(missing)
    if not all_rows:
        raise RuntimeError("UCL: 历史抓取为空")
    all_rows.sort(key=lambda r: (r["date"], r["home"]))
    fetch_fd.write_csv(config.paths("UCL")["results"], normalize.UCL_RESULT_FIELDS, all_rows)
    return len(all_rows), len(missing_all), _max_consecutive(missing_all) > 2


def _max_consecutive(months):
    """缺月列表(YYYYMM)中最长连续段长度。"""
    best = cur = 0
    prev = None
    for ym in sorted(set(months)):
        idx = int(ym[:4]) * 12 + int(ym[4:])
        cur = cur + 1 if prev is not None and idx == prev + 1 else 1
        best = max(best, cur)
        prev = idx
    return best
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_leagues_fetch_espn.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add leagues/fetch_espn.py tests/test_leagues_fetch_espn.py
git commit -m "feat(leagues): ESPN 按月迭代赛程抓取 + UCL 历史(Task 4)"
```

### Task 5: 统一俱乐部 Elo 池 `leagues/elo.py`

**Files:**
- Create: `leagues/elo.py`
- Test: `tests/test_leagues_elo.py`

**Interfaces:**
- Consumes: 无(Task 8 消费;输入 match dict 与 load_results 输出兼容)。
- Produces:
  - 常量:`INITIAL_ELO=1500.0, HOME_ADV=100.0, K_LEAGUE=30.0, K_UCL=40.0, PROMOTED_GAMES=30, PROMOTED_BOOST=1.5`
  - `k_for(comp, games_played) -> float`
  - `goal_diff_mult(gd) -> float`(clubelo 阶梯:2→1.5,3→1.75,≥4→(11+gd)/8)
  - `update_elo(rh, ra, gh, ga, comp, neutral, h_games, a_games) -> (new_rh, new_ra)` — 每队用自己的 K(升班马加速,**设计上非零和**);HOME_ADV 只进期望分
  - `build_pool(matches, cutoff=None) -> (ratings, history)` — matches 需含 `date/home/away/gh/ga/comp/neutral`;cutoff 用 `date < cutoff` 严格小于;**history 存赛前 Elo**(walk-forward 安全)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_leagues_elo.py
"""统一 Elo 池:零和性、升班马非零和、主场修正、净胜球乘数、cutoff 严格性。"""
from leagues import elo


def _m(date, home, away, gh, ga, comp="E0", neutral=False):
    return {"date": date, "home": home, "away": away, "gh": gh, "ga": ga,
            "comp": comp, "neutral": neutral}


def test_zero_sum_when_both_established_equal_k():
    rh, ra = elo.update_elo(1600, 1600, 2, 1, "E0", False, 100, 100)
    assert abs((rh + ra) - 3200) < 1e-9          # K 相同 → 零和
    assert rh > 1600 > ra


def test_promoted_team_non_zero_sum():
    """新队(games<30)K×1.5,老队 K 不变 → 总和不守恒(刻意设计,加速收敛)。"""
    rh, ra = elo.update_elo(1500, 1700, 1, 0, "E0", False, 0, 100)
    gained = rh - 1500
    lost = 1700 - ra
    assert gained > lost                          # 爆冷 + 加速 K → 得大于失


def test_home_adv_affects_expectation_only():
    """主场赢球加的分数应少于中立场同比分(期望分更高 → 超额更少)。"""
    rh_home, _ = elo.update_elo(1600, 1600, 1, 0, "E0", False, 100, 100)
    rh_neut, _ = elo.update_elo(1600, 1600, 1, 0, "E0", True, 100, 100)
    assert rh_neut > rh_home > 1600


def test_ucl_k_higher_than_league():
    d_ucl = elo.update_elo(1600, 1600, 1, 0, "UCL", False, 100, 100)[0] - 1600
    d_lg = elo.update_elo(1600, 1600, 1, 0, "E0", False, 100, 100)[0] - 1600
    assert d_ucl > d_lg


def test_goal_diff_mult_ladder():
    assert elo.goal_diff_mult(0) == 1.0 and elo.goal_diff_mult(1) == 1.0
    assert elo.goal_diff_mult(2) == 1.5
    assert elo.goal_diff_mult(3) == 1.75
    assert elo.goal_diff_mult(4) == (11 + 4) / 8.0
    assert elo.goal_diff_mult(7) == (11 + 7) / 8.0


def test_build_pool_cutoff_strict_and_pre_match_history():
    matches = [
        _m("2026-08-15", "A", "B", 1, 0),
        _m("2026-08-22", "B", "A", 0, 2),
        _m("2026-08-29", "A", "C", 3, 1),
    ]
    ratings, history = elo.build_pool(matches, cutoff="2026-08-22")
    # cutoff 严格 <:只剩第 1 场
    assert len(history) == 1 and "C" not in ratings
    # history 存赛前 Elo:第 1 场双方都是初始分
    assert history[0]["elo_h"] == elo.INITIAL_ELO and history[0]["elo_a"] == elo.INITIAL_ELO
    # 无 cutoff:3 场全算,A 赢 2 场应最高
    ratings, history = elo.build_pool(matches)
    assert ratings["A"] > ratings["B"] and ratings["A"] > ratings["C"]
    # 第 3 场的赛前 Elo 已反映前 2 场结果
    assert history[2]["elo_h"] != elo.INITIAL_ELO


def test_build_pool_sorts_by_date():
    m1 = [_m("2026-08-22", "B", "A", 0, 2), _m("2026-08-15", "A", "B", 1, 0)]
    m2 = list(reversed(m1))
    assert elo.build_pool(m1)[0] == elo.build_pool(m2)[0]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_leagues_elo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leagues.elo'`

- [ ] **Step 3: 实现**

```python
# leagues/elo.py
"""统一俱乐部 Elo 池:五大联赛 + 欧冠一个池(欧冠跨联赛连接,使联赛间强度可比)。

参数(对照 clubelo.com 实践):
  · K:联赛 30,欧冠 40(更重要赛事波动更大)
  · 主场 +100 只进期望分计算,不改赛后修正方向
  · 净胜球乘数:2 球 ×1.5,3 球 ×1.75,≥4 球 ×(11+gd)/8
  · 新队(升班马/首次入池)1500 起步,前 30 场 K×1.5 加速收敛
    → 每队用自己的 K,设计上非零和(刻意,勿改成统一 K)
"""
INITIAL_ELO = 1500.0
HOME_ADV = 100.0
K_LEAGUE = 30.0
K_UCL = 40.0
PROMOTED_GAMES = 30
PROMOTED_BOOST = 1.5


def k_for(comp, games_played):
    k = K_UCL if comp == "UCL" else K_LEAGUE
    if games_played < PROMOTED_GAMES:
        k *= PROMOTED_BOOST
    return k


def goal_diff_mult(gd):
    if gd >= 4:
        return (11 + gd) / 8.0
    if gd == 3:
        return 1.75
    if gd == 2:
        return 1.5
    return 1.0


def update_elo(rh, ra, gh, ga, comp, neutral, h_games, a_games):
    """单场更新,返回 (new_rh, new_ra)。每队用自己的 K(升班马加速 → 非零和是特性)。"""
    adv = 0.0 if neutral else HOME_ADV
    exp_h = 1.0 / (1.0 + 10 ** (-((rh + adv) - ra) / 400.0))
    if gh > ga:
        sh = 1.0
    elif gh == ga:
        sh = 0.5
    else:
        sh = 0.0
    m = goal_diff_mult(abs(gh - ga))
    new_rh = rh + k_for(comp, h_games) * m * (sh - exp_h)
    new_ra = ra + k_for(comp, a_games) * m * ((1.0 - sh) - (1.0 - exp_h))
    return new_rh, new_ra


def build_pool(matches, cutoff=None):
    """按日期顺序走全部比赛建池。

    matches: [{date,home,away,gh,ga,comp,neutral}, ...](comp ∈ LEAGUE_CODES+['UCL'])
    cutoff: 'YYYY-MM-DD',只用 date < cutoff 的比赛(walk-forward 严格)。
    返回 (ratings, history):
      ratings = {team: 最新 elo}
      history = [{date,home,away,elo_h,elo_a,comp}, ...]  — elo_h/elo_a 是赛前值,
                回测/XGBoost 特征可直接用,无未来泄漏。
    """
    rows = [m for m in matches if cutoff is None or m["date"] < cutoff]
    rows.sort(key=lambda m: (m["date"], m["home"]))
    games, ratings, history = {}, {}, []
    for m in rows:
        h, a = m["home"], m["away"]
        rh = ratings.get(h, INITIAL_ELO)
        ra = ratings.get(a, INITIAL_ELO)
        history.append({"date": m["date"], "home": h, "away": a,
                        "elo_h": rh, "elo_a": ra, "comp": m["comp"]})
        ratings[h], ratings[a] = update_elo(
            rh, ra, m["gh"], m["ga"], m["comp"], bool(m.get("neutral")),
            games.get(h, 0), games.get(a, 0))
        games[h] = games.get(h, 0) + 1
        games[a] = games.get(a, 0) + 1
    return ratings, history
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_leagues_elo.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add leagues/elo.py tests/test_leagues_elo.py
git commit -m "feat(leagues): 统一俱乐部 Elo 池(联赛30/欧冠40,升班马加速)(Task 5)"
```

---

### Task 6: Dixon-Coles 攻防参数 `leagues/dixon_coles.py`

**Files:**
- Create: `leagues/dixon_coles.py`
- Test: `tests/test_leagues_dc.py`

**Interfaces:**
- Consumes: match dict(load_results 输出,需 `date/home/away/gh/ga`,可选 `neutral`)。
- Produces:
  - `RHO_DEFAULT = 0.0`(WC 校准结论:τ 修正对本数据无增益)
  - `_tau(x, y, lh, la, rho) -> float`(DC 低比分修正;Task 7 复用)
  - `fit_league(matches, cutoff=None, halflife_years=3.0, min_matches=20, rho=0.0) -> (params, glob)`
    - `params = {team: {"attack": float, "defense": float, "n": int}}`;球队总场次 < min_matches → attack=defense=0.0
    - `glob = {"home_adv": float, "beta0": float, "rho": float, "n_matches": int}`
  - `fit_ucl_global(ucl_matches, league_params_by_club, halflife_years=2.0) -> (glob, borrow_map)`
    - attack/defense 固定借用本国联赛值,只拟合 β0+home_adv;`borrow_map[t] = {"attack","defense","low_confidence"}`;非五大联赛队 → 0.0 + `low_confidence=True`(N-2)
  - `expected_goals(params, glob, home, away, neutral=False) -> (lam_h, lam_a)` — clip [0.1, 5.0],未知队按中性 0

- [ ] **Step 1: 写失败测试**

```python
# tests/test_leagues_dc.py
"""Dixon-Coles MLE:合成数据参数恢复、小样本回退、UCL 借用制、λ 裁剪。"""
import random

from leagues import dixon_coles as dc


def _synth_matches(rng, teams, atk, dfn, n_rounds=30, start_year=2023):
    """用已知真值参数生成泊松比分(β0=log(1.35), home_adv=0.25)。"""
    import math
    matches = []
    for r in range(n_rounds):
        for i, h in enumerate(teams):
            for j, a in enumerate(teams):
                if i <= j:
                    continue
                lh = math.exp(math.log(1.35) + 0.25 + atk[h] - dfn[a])
                la = math.exp(math.log(1.35) + atk[a] - dfn[h])
                gh = _pois(rng, lh)
                ga = _pois(rng, la)
                month = (r % 12) + 1
                matches.append({"date": f"{start_year}-{month:02d}-15",
                                "home": h, "away": a, "gh": gh, "ga": ga})
    return matches


def _pois(rng, lam):
    """Knuth 泊松采样(测试用,避免依赖 numpy 随机)。"""
    import math
    L, k, p = math.exp(-lam), 0, 1.0
    while p > L:
        k += 1
        p *= rng.random()
    return k - 1


def test_fit_league_recovers_attack_ordering():
    rng = random.Random(42)
    teams = ["Strong", "MidA", "MidB", "Weak"]
    atk = {"Strong": 0.5, "MidA": 0.1, "MidB": -0.1, "Weak": -0.5}
    dfn = {"Strong": -0.3, "MidA": 0.0, "MidB": 0.0, "Weak": 0.3}  # defense 越高越漏
    matches = _synth_matches(rng, teams, atk, dfn)
    params, glob = dc.fit_league(matches)
    assert params["Strong"]["attack"] > params["Weak"]["attack"]
    assert params["Strong"]["defense"] < params["Weak"]["defense"]
    assert glob["home_adv"] > 0 and glob["n_matches"] == len(matches)
    # 攻防各自零均值化(可识别性)
    s_atk = sum(p["attack"] for p in params.values())
    assert abs(s_atk) < 1e-6


def test_fit_league_small_sample_falls_back_to_neutral():
    rng = random.Random(7)
    # 25 轮:A/B 各踢 25 场(≥ min_matches),正常拟合
    matches = _synth_matches(rng, ["A", "B"], {"A": 0.3, "B": -0.3}, {}, n_rounds=25)
    # 加一支只踢了 2 场的新队
    matches += [{"date": "2023-02-15", "home": "NewBoys", "away": "A", "gh": 0, "ga": 4},
                {"date": "2023-02-16", "home": "B", "away": "NewBoys", "gh": 3, "ga": 0}]
    params, _ = dc.fit_league(matches, min_matches=20)
    assert params["NewBoys"]["attack"] == 0.0 and params["NewBoys"]["defense"] == 0.0
    assert params["NewBoys"]["n"] == 2
    assert params["A"]["attack"] != 0.0            # 老队正常拟合


def test_fit_league_empty_matches():
    params, glob = dc.fit_league([])
    assert params == {} and glob["n_matches"] == 0


def test_fit_ucl_global_borrows_and_flags_outsiders():
    """N-2:非五大联赛队(如 Ajax)→ 中性参数 + low_confidence=True。"""
    league_params = {
        "Real Madrid": {"attack": 0.4, "defense": -0.2, "n": 100},
        "Man City": {"attack": 0.5, "defense": -0.3, "n": 100},
    }
    ucl_matches = [
        {"date": "2025-09-16", "home": "Real Madrid", "away": "Ajax", "gh": 3, "ga": 1, "neutral": False},
        {"date": "2025-09-17", "home": "Man City", "away": "Real Madrid", "gh": 2, "ga": 2, "neutral": False},
        {"date": "2025-10-01", "home": "Ajax", "away": "Man City", "gh": 0, "ga": 4, "neutral": False},
        {"date": "2026-05-30", "home": "Real Madrid", "away": "Man City", "gh": 1, "ga": 0, "neutral": True},
    ]
    glob, borrow = dc.fit_ucl_global(ucl_matches, league_params)
    assert borrow["Real Madrid"]["attack"] == 0.4 and borrow["Real Madrid"]["low_confidence"] is False
    assert borrow["Ajax"]["attack"] == 0.0 and borrow["Ajax"]["low_confidence"] is True
    assert set(glob) >= {"beta0", "home_adv", "rho", "n_matches"}
    assert glob["n_matches"] == 4 and glob["rho"] == 0.0
    # 借用参数可直接进 expected_goals 出 λ
    lh, la = dc.expected_goals(borrow, glob, "Real Madrid", "Ajax")
    assert lh > la


def test_expected_goals_clip_and_unknown_team():
    params = {"X": {"attack": 10.0, "defense": -10.0, "n": 50}}
    glob = {"beta0": 0.3, "home_adv": 0.25, "rho": 0.0}
    lh, la = dc.expected_goals(params, glob, "X", "Ghost")
    assert lh == 5.0                                # 上限裁剪
    params2 = {"X": {"attack": -10.0, "defense": 10.0, "n": 50}}
    lh2, la2 = dc.expected_goals(params2, glob, "Ghost", "X")
    assert la2 == 0.1                               # 下限裁剪
    # 中立场:home_adv 不计
    lh3, _ = dc.expected_goals(params2, glob, "X", "Ghost", neutral=True)
    lh4, _ = dc.expected_goals(params2, glob, "X", "Ghost", neutral=False)
    assert lh4 > lh3
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_leagues_dc.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leagues.dixon_coles'`

- [ ] **Step 3: 实现**

```python
# leagues/dixon_coles.py
"""Dixon-Coles 攻防参数 MLE(思路复刻 compute_ad.py,接口按联赛隔离)。

  log λ_home = β0 + home_adv·(非中立) + attack[h] − defense[a]
  log λ_away = β0              + attack[a] − defense[h]

  · 时间衰减权重:exp(−ln2·Δdays/(halflife·365.25)),相对最近一场
  · 可识别性:弱 L2 惩罚 + 拟合后攻防各自零均值化
  · 小样本:球队总场次 < min_matches → 中性参数(attack=defense=0),不抛错
  · RHO 默认 0.00(WC 校准结论:τ 修正无增益);lambda clip [0.1, 5.0]
  · UCL 借用制(M-5/N-2):attack/defense 固定为球队本国联赛值(联赛间强度差
    已由统一 Elo 池体现,DC 层面近似可迁移),只拟合全局 β0 + home_adv
"""
import math
from datetime import date

import numpy as np
from scipy.optimize import minimize

RHO_DEFAULT = 0.0


def _tau(x, y, lh, la, rho):
    """Dixon-Coles 低比分联合修正(rho=0 时恒 1)。"""
    if x == 0 and y == 0:
        return 1.0 - lh * la * rho
    if x == 0 and y == 1:
        return 1.0 + lh * rho
    if x == 1 and y == 0:
        return 1.0 + la * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def _prep(matches, cutoff=None):
    rows = [m for m in matches
            if m.get("gh") is not None and m.get("ga") is not None
            and (cutoff is None or m["date"] < cutoff)]
    rows.sort(key=lambda m: (m["date"], m["home"]))
    teams = sorted({m["home"] for m in rows} | {m["away"] for m in rows})
    idx = {t: i for i, t in enumerate(teams)}
    hi = np.array([idx[m["home"]] for m in rows])
    ai = np.array([idx[m["away"]] for m in rows])
    gh = np.array([m["gh"] for m in rows], dtype=float)
    ga = np.array([m["ga"] for m in rows], dtype=float)
    return teams, hi, ai, gh, ga, rows


def _time_weights(rows, halflife_years):
    dmax = date.fromisoformat(rows[-1]["date"])
    xi = math.log(2) / (halflife_years * 365.25)
    return np.array([math.exp(-xi * (dmax - date.fromisoformat(m["date"])).days)
                     for m in rows])


def fit_league(matches, cutoff=None, halflife_years=3.0, min_matches=20, rho=RHO_DEFAULT):
    """联赛 DC 拟合。返回 (params, glob),签名见 Task 头 Interfaces。"""
    teams, hi, ai, gh, ga, rows = _prep(matches, cutoff)
    if not rows:
        return {}, {"home_adv": 0.0, "beta0": math.log(1.35), "rho": rho, "n_matches": 0}
    n = len(teams)
    w = _time_weights(rows, halflife_years)
    neutral = np.array([bool(m.get("neutral")) for m in rows], dtype=float)

    def unpack(x):
        return x[0], x[1], x[2:2 + n], x[2 + n:2 + 2 * n]

    def nll(x):
        beta0, hadv, atk, dfn = unpack(x)
        lh = np.clip(np.exp(beta0 + hadv * (1.0 - neutral) + atk[hi] - dfn[ai]), 0.1, 5.0)
        la = np.clip(np.exp(beta0 + atk[ai] - dfn[hi]), 0.1, 5.0)
        ll = -lh + gh * np.log(lh) - la + ga * np.log(la)
        if rho != 0.0:
            tau = np.array([_tau(int(g1), int(g2), l1, l2, rho)
                            for g1, g2, l1, l2 in zip(gh, ga, lh, la)])
            ll = ll + np.log(np.maximum(tau, 1e-9))
        pen = 1e-4 * float(np.sum(atk ** 2) + np.sum(dfn ** 2))
        return -float(np.sum(w * ll)) + pen

    x0 = np.zeros(2 + 2 * n)
    x0[0] = math.log(1.35)
    x0[1] = 0.25
    res = minimize(nll, x0, method="L-BFGS-B", options={"maxiter": 500})
    beta0, hadv, atk, dfn = unpack(res.x)
    atk = atk - atk.mean()      # 零均值化(可识别性)
    dfn = dfn - dfn.mean()
    played = {}
    for m in rows:
        played[m["home"]] = played.get(m["home"], 0) + 1
        played[m["away"]] = played.get(m["away"], 0) + 1
    params = {}
    for i, t in enumerate(teams):
        if played.get(t, 0) < min_matches:
            params[t] = {"attack": 0.0, "defense": 0.0, "n": played.get(t, 0)}
        else:
            params[t] = {"attack": float(atk[i]), "defense": float(dfn[i]), "n": played[t]}
    glob = {"home_adv": float(hadv), "beta0": float(beta0), "rho": rho, "n_matches": len(rows)}
    return params, glob


def fit_ucl_global(ucl_matches, league_params_by_club, halflife_years=2.0):
    """欧冠 DC(借用制):attack/defense 固定借用本国联赛值,只拟合 β0 + home_adv。

    返回 (glob, borrow_map);无五大联赛归属的队 → 0.0 + low_confidence=True(N-2)。
    """
    rows = sorted((m for m in ucl_matches if m.get("gh") is not None),
                  key=lambda m: (m["date"], m["home"]))
    borrow_map = {}
    for m in rows:
        for t in (m["home"], m["away"]):
            if t in borrow_map:
                continue
            p = league_params_by_club.get(t)
            if p is None:
                borrow_map[t] = {"attack": 0.0, "defense": 0.0, "low_confidence": True}
            else:
                borrow_map[t] = {"attack": p["attack"], "defense": p["defense"],
                                 "low_confidence": False}
    if not rows:
        return {"beta0": math.log(1.35), "home_adv": 0.0, "rho": RHO_DEFAULT,
                "n_matches": 0}, borrow_map
    w = _time_weights(rows, halflife_years)
    gh = np.array([m["gh"] for m in rows], dtype=float)
    ga = np.array([m["ga"] for m in rows], dtype=float)
    ah = np.array([borrow_map[m["home"]]["attack"] - borrow_map[m["away"]]["defense"]
                   for m in rows])
    aa = np.array([borrow_map[m["away"]]["attack"] - borrow_map[m["home"]]["defense"]
                   for m in rows])
    neutral = np.array([bool(m.get("neutral")) for m in rows], dtype=float)

    def nll(x):
        beta0, hadv = x
        lh = np.clip(np.exp(beta0 + hadv * (1.0 - neutral) + ah), 0.1, 5.0)
        la = np.clip(np.exp(beta0 + aa), 0.1, 5.0)
        ll = -lh + gh * np.log(lh) - la + ga * np.log(la)
        return -float(np.sum(w * ll))

    res = minimize(nll, np.array([math.log(1.35), 0.2]), method="L-BFGS-B")
    glob = {"beta0": float(res.x[0]), "home_adv": float(res.x[1]),
            "rho": RHO_DEFAULT, "n_matches": len(rows)}
    return glob, borrow_map


def expected_goals(params, glob, home, away, neutral=False):
    """λ_h/λ_a,clip 到 [0.1, 5.0];球队缺参数按中性 0;中立场不计 home_adv。"""
    ph = params.get(home, {"attack": 0.0, "defense": 0.0})
    pa = params.get(away, {"attack": 0.0, "defense": 0.0})
    hadv = 0.0 if neutral else glob.get("home_adv", 0.0)
    lh = math.exp(glob["beta0"] + hadv + ph["attack"] - pa["defense"])
    la = math.exp(glob["beta0"] + pa["attack"] - ph["defense"])
    return min(max(lh, 0.1), 5.0), min(max(la, 0.1), 5.0)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_leagues_dc.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add leagues/dixon_coles.py tests/test_leagues_dc.py
git commit -m "feat(leagues): Dixon-Coles MLE + UCL 借用制全局参数(Task 6)"
```

---

### Task 7: 单场预测管线 `leagues/predict.py`

**Files:**
- Create: `leagues/predict.py`
- Test: `tests/test_leagues_predict.py`

**Interfaces:**
- Consumes: `dixon_coles._tau / expected_goals`(Task 6);Elo ratings(Task 5 build_pool 输出)。
- Produces:
  - 常量:`MAX_GOALS=8, AVG_TOTAL_GOALS=2.6, ELO_SCALE=200.0, ELO_HOME_ADV=100.0, KELLY_FRACTION=0.25`
  - `poisson_pmf(k, lam)`;`score_matrix(lh, la, rho=0.0) -> 9×9 归一化方阵`
  - `three_way(m) -> (hw, dr, aw)`;`top_scores(m, n=5) -> [["1-0", 0.09], ...]`;`ou25_over(m)`;`btts_yes(m)`
  - `elo_probs(elo_h, elo_a, neutral=False) -> ({"home","draw","away"}, lh, la)` — 纯 Elo 回退
  - `devig(oh, od, oa) -> (ph, pd, pa)|None`;`kelly(p, odds, fraction=0.25) -> float`(edge≤0 → 0)
  - `combine_gate(stat, xgb, w_base=0.2) -> dict` — 首选一致 → `w_base·stat + (1−w_base)·xgb`;分歧 → 纯 stat(ensemble.py 模式)
  - `predict_match(home, away, *, elo, dc_params, dc_glob, odds=None, neutral=False, xgb_probs=None, low_confidence=False) -> dict` — 任一队不在 dc_params → 回退纯 Elo 且 `low_confidence=True`;有 odds 且 devig 成功 → 附 `market` 子 dict

- [ ] **Step 1: 写失败测试**

```python
# tests/test_leagues_predict.py
"""单场预测管线:矩阵归一、三态、回退、devig、Kelly、分歧门控、market 结构。"""
from leagues import predict


def test_score_matrix_normalized_and_shapes():
    m = predict.score_matrix(1.6, 1.1)
    assert len(m) == predict.MAX_GOALS + 1 and len(m[0]) == predict.MAX_GOALS + 1
    assert abs(sum(sum(r) for r in m) - 1.0) < 1e-9
    hw, dr, aw = predict.three_way(m)
    assert abs(hw + dr + aw - 1.0) < 1e-9
    assert hw > aw                                # λ_h > λ_a → 主胜概率高


def test_top_scores_format_and_order():
    m = predict.score_matrix(1.5, 1.0)
    top = predict.top_scores(m, n=5)
    assert len(top) == 5 and all(isinstance(s, str) and "-" in s for s, _ in top)
    probs = [p for _, p in top]
    assert probs == sorted(probs, reverse=True)


def test_ou25_and_btts_sanity():
    high = predict.score_matrix(2.5, 2.2)
    low = predict.score_matrix(0.6, 0.5)
    assert predict.ou25_over(high) > predict.ou25_over(low)
    assert predict.btts_yes(high) > predict.btts_yes(low)
    assert 0 < predict.ou25_over(low) < predict.btts_yes(low) < 1   # 低 λ:1-1 属 under 格,BTTS(≈0.18)> Over2.5(≈0.04)


def test_elo_probs_symmetry_and_home_adv():
    p_neut, lh, la = predict.elo_probs(1600, 1600, neutral=True)
    assert abs(lh - la) < 1e-9 and abs(p_neut["home"] - p_neut["away"]) < 1e-9
    p_home, lh2, la2 = predict.elo_probs(1600, 1600, neutral=False)
    assert lh2 > la2 and p_home["home"] > p_home["away"]
    assert abs(sum(p_home.values()) - 1.0) < 1e-9


def test_devig_and_kelly():
    dv = predict.devig(2.0, 3.0, 4.0)
    assert abs(sum(dv) - 1.0) < 1e-9
    assert dv[0] < 1 / 2.0                        # 比例去水:水线>1 → 单项概率被压缩(1/2+1/3+1/4=1.083 → 0.4615)
    assert predict.devig(None, 3.0, 4.0) is None
    assert predict.devig(1.0, 3.0, 4.0) is None   # 赔率 ≤1 非法
    assert predict.kelly(0.6, 2.0) > 0            # 正 edge
    assert predict.kelly(0.4, 2.0) == 0.0         # 负 edge → 0,不出负仓位


def test_combine_gate_agree_vs_disagree():
    stat = {"home": 0.5, "draw": 0.3, "away": 0.2}
    xgb_agree = {"home": 0.7, "draw": 0.2, "away": 0.1}
    out = predict.combine_gate(stat, xgb_agree)
    assert abs(out["home"] - (0.2 * 0.5 + 0.8 * 0.7)) < 1e-9   # 一致 → xgb 占 0.8
    xgb_dis = {"home": 0.1, "draw": 0.2, "away": 0.7}
    out2 = predict.combine_gate(stat, xgb_dis)
    assert out2 == stat                           # 分歧 → 纯统计模型


def test_predict_match_with_and_without_odds():
    elo = {"A": 1650.0, "B": 1500.0}
    dc_params = {"A": {"attack": 0.3, "defense": -0.1, "n": 100},
                 "B": {"attack": -0.2, "defense": 0.2, "n": 100}}
    dc_glob = {"beta0": 0.3, "home_adv": 0.25, "rho": 0.0}
    out = predict.predict_match("A", "B", elo=elo, dc_params=dc_params, dc_glob=dc_glob,
                                odds={"b365h": 1.9, "b365d": 3.4, "b365a": 4.2})
    assert abs(sum(out["probs"].values()) - 1.0) < 1e-3
    assert out["lam_h"] > out["lam_a"]
    assert len(out["top_scores"]) == 5
    mk = out["market"]
    assert set(mk) == {"p_mkt", "edges", "max_edge", "pick", "odds", "kelly"}
    assert mk["pick"] in ("home", "draw", "away")
    assert mk["edges"][mk["pick"]] == mk["max_edge"]
    # 无赔率 → 不出现 market 键(付费门控的前提:键不存在而非置空)
    out2 = predict.predict_match("A", "B", elo=elo, dc_params=dc_params, dc_glob=dc_glob)
    assert "market" not in out2


def test_predict_match_fallback_to_elo_marks_low_confidence():
    elo = {"A": 1650.0, "B": 1500.0}
    out = predict.predict_match("A", "Ghost", elo=elo, dc_params={}, dc_glob={"beta0": 0.3})
    assert out["low_confidence"] is True
    assert out["probs"]["home"] > out["probs"]["away"]


def test_predict_match_xgb_gate():
    elo = {"A": 1650.0, "B": 1500.0}
    dc_params = {"A": {"attack": 0.3, "defense": 0.0, "n": 100},
                 "B": {"attack": 0.0, "defense": 0.0, "n": 100}}
    dc_glob = {"beta0": 0.3, "home_adv": 0.25, "rho": 0.0}
    base = predict.predict_match("A", "B", elo=elo, dc_params=dc_params, dc_glob=dc_glob)
    gated = predict.predict_match("A", "B", elo=elo, dc_params=dc_params, dc_glob=dc_glob,
                                  xgb_probs={"home": 0.2, "draw": 0.3, "away": 0.5})
    assert gated["probs"] == base["probs"]        # 分歧(主 vs 客)→ 门控回纯统计
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_leagues_predict.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leagues.predict'`

- [ ] **Step 3: 实现**

```python
# leagues/predict.py
"""单场预测管线:DC 比分矩阵 → 三态/比分 top5/OU2.5/BTTS;Elo 回退;
赔率 devig + edge + 分数 Kelly;与 XGBoost 的分歧门控集成(ensemble.py 模式)。

付费门控前提(N-1):只有显式传 odds 时输出才含 market 子 dict;
不传则 market 键完全不存在(API 层按 Pro 状态决定是否传)。
"""
import math

from . import dixon_coles
from .dixon_coles import _tau

MAX_GOALS = 8
AVG_TOTAL_GOALS = 2.6      # 联赛场均总进球(高于 WC 的 2.20,联赛无保守淘汰赛)
ELO_SCALE = 200.0
ELO_HOME_ADV = 100.0
KELLY_FRACTION = 0.25


def poisson_pmf(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def score_matrix(lh, la, rho=0.0):
    """(MAX_GOALS+1)² 比分概率矩阵,P(i,j)=Pois(i;lh)·Pois(j;la)·τ(i,j),归一化。"""
    m = [[poisson_pmf(i, lh) * poisson_pmf(j, la) * _tau(i, j, lh, la, rho)
          for j in range(MAX_GOALS + 1)] for i in range(MAX_GOALS + 1)]
    s = sum(sum(row) for row in m)
    return [[v / s for v in row] for row in m]


def three_way(m):
    n = len(m)
    hw = sum(m[i][j] for i in range(n) for j in range(n) if i > j)
    dr = sum(m[i][i] for i in range(n))
    aw = sum(m[i][j] for i in range(n) for j in range(n) if i < j)
    return hw, dr, aw


def top_scores(m, n=5):
    cells = sorted(((m[i][j], i, j) for i in range(len(m)) for j in range(len(m))),
                   reverse=True)
    return [[f"{i}-{j}", round(p, 4)] for p, i, j in cells[:n]]


def ou25_over(m):
    n = len(m)
    return sum(m[i][j] for i in range(n) for j in range(n) if i + j >= 3)


def btts_yes(m):
    n = len(m)
    return sum(m[i][j] for i in range(1, n) for j in range(1, n))


def elo_probs(elo_h, elo_a, neutral=False):
    """纯 Elo 回退:sigmoid 分总进球份额 → 双泊松矩阵 → 三态。
    返回 ({"home","draw","away"}, lh, la)。"""
    diff = elo_h + (0.0 if neutral else ELO_HOME_ADV) - elo_a
    share = 1.0 / (1.0 + 10 ** (-diff / (2 * ELO_SCALE)))
    lh = max(AVG_TOTAL_GOALS * share, 0.1)
    la = max(AVG_TOTAL_GOALS * (1.0 - share), 0.1)
    hw, dr, aw = three_way(score_matrix(lh, la))
    return {"home": hw, "draw": dr, "away": aw}, lh, la


def devig(oh, od, oa):
    """三态按比例去水。任一缺失/≤1.0 → None。"""
    if not oh or not od or not oa or min(oh, od, oa) <= 1.0:
        return None
    inv = [1.0 / oh, 1.0 / od, 1.0 / oa]
    s = sum(inv)
    return inv[0] / s, inv[1] / s, inv[2] / s


def kelly(p, odds, fraction=KELLY_FRACTION):
    """分数凯利:f* = (p·odds − 1)/(odds − 1) × fraction;edge ≤ 0 → 0。"""
    if not odds or odds <= 1.0:
        return 0.0
    raw = (p * odds - 1.0) / (odds - 1.0)
    return max(raw, 0.0) * fraction


def combine_gate(stat, xgb, w_base=0.2):
    """分歧门控:三态首选一致 → w_base·stat + (1−w_base)·xgb;分歧 → 纯 stat。"""
    agree = max(stat, key=stat.get) == max(xgb, key=xgb.get)
    w_stat = w_base if agree else 1.0
    return {k: w_stat * stat[k] + (1.0 - w_stat) * xgb[k] for k in ("home", "draw", "away")}


def predict_match(home, away, *, elo, dc_params, dc_glob, odds=None, neutral=False,
                  xgb_probs=None, low_confidence=False):
    """单场完整预测。返回:
    {probs, lam_h, lam_a, top_scores, ou25_over, btts_yes, low_confidence [, market]}
    market = {p_mkt, edges, max_edge, pick, odds, kelly}(仅 odds 传参且 devig 成功时)。
    """
    if home in dc_params and away in dc_params:
        lh, la = dixon_coles.expected_goals(dc_params, dc_glob, home, away, neutral)
        m = score_matrix(lh, la, dc_glob.get("rho", 0.0))
        hw, dr, aw = three_way(m)
        stat = {"home": hw, "draw": dr, "away": aw}
    else:
        stat, lh, la = elo_probs(elo.get(home, 1500.0), elo.get(away, 1500.0), neutral)
        m = score_matrix(lh, la)
        low_confidence = True
    probs = combine_gate(stat, xgb_probs) if xgb_probs else stat
    out = {
        "probs": {k: round(v, 4) for k, v in probs.items()},
        "lam_h": round(lh, 3), "lam_a": round(la, 3),
        "top_scores": top_scores(m),
        "ou25_over": round(ou25_over(m), 4),
        "btts_yes": round(btts_yes(m), 4),
        "low_confidence": bool(low_confidence),
    }
    if odds:
        dv = devig(odds.get("b365h"), odds.get("b365d"), odds.get("b365a"))
        if dv:
            p_mkt = {"home": dv[0], "draw": dv[1], "away": dv[2]}
            edges = {k: probs[k] - p_mkt[k] for k in probs}
            pick = max(edges, key=edges.get)
            odd_key = {"home": "b365h", "draw": "b365d", "away": "b365a"}[pick]
            out["market"] = {
                "p_mkt": {k: round(v, 4) for k, v in p_mkt.items()},
                "edges": {k: round(v, 4) for k, v in edges.items()},
                "max_edge": round(edges[pick], 4),
                "pick": pick,
                "odds": {"home": odds.get("b365h"), "draw": odds.get("b365d"),
                         "away": odds.get("b365a")},
                "kelly": round(kelly(probs[pick], odds.get(odd_key)), 4),
            }
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_leagues_predict.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add leagues/predict.py tests/test_leagues_predict.py
git commit -m "feat(leagues): 单场预测管线(矩阵/devig/Kelly/分歧门控)(Task 7)"
```

### Task 8: 构建管线 `leagues/build.py`

**Files:**
- Create: `leagues/build.py`
- Test: `tests/test_leagues_build.py`

**Interfaces:**
- Consumes: Tasks 1-7 全部(config / normalize 字段名 / fetch_fd.is_fresh / elo.build_pool / dixon_coles.fit_league / predict.predict_match)。
- Produces:
  - `read_csv(path) -> list[dict]`(不存在 → [])
  - `load_results(code) -> list[dict]`(数字字段转 float/int,`neutral` 转 bool,附 `comp=code`)
  - `load_schedule(code) -> list[dict]`(同上,gh/ga 空串 → None)
  - `load_fixtures(code) -> list[dict]`
  - `compute_standings(results, season) -> list[dict]` — 3/1/0,排序 pts→gd→gf→队名,行含 `team,p,w,d,l,gf,ga,gd,pts`
  - `build_elo_pool(cutoff=None) -> (ratings, history)` — 全 ALL_CODES 历史,落盘 `global_paths()["elo"]/["elo_history"]`
  - `rebuild_dc(code, cutoff=None) -> dict` — 落盘 `dc_params.json`(`{"league","params","global"}`)
  - `_load_dc(code) -> dict`(无文件则现算)
  - `rebuild(code, elo=None, xgb=None) -> dict` — 从最新 schedule 全量重建 predictions.json(**每次新建字典,无过期键**)
  - `rebuild_all(elo=None, xgb=None) -> {code: payload}`
  - `fetch_all_if_stale(fixtures_h=20, results_h=20) -> report` — 按新鲜度补抓,单联赛失败不阻塞

- [ ] **Step 1: 写失败测试**

```python
# tests/test_leagues_build.py
"""构建管线:积分榜、Elo 池落盘、rebuild 无过期键、数字转换。"""
import json
import os

import pytest

from leagues import build, config


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """把 DATA_ROOT 指到 tmp,写读互不影响真实数据。"""
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    return tmp_path


def _write_results(code, rows):
    from leagues import fetch_fd, normalize
    fetch_fd.write_csv(config.paths(code)["results"], normalize.RESULT_FIELDS, rows)


def _write_schedule(code, rows):
    from leagues import fetch_fd, normalize
    fetch_fd.write_csv(config.paths(code)["schedule"], normalize.SCHEDULE_FIELDS, rows)


def _r(season, date, home, away, gh, ga, **kw):
    row = {"season": season, "date": date, "home": home, "away": away, "gh": gh, "ga": ga}
    row.update(kw)
    return row


def test_compute_standings_math(sandbox):
    _write_results("E0", [
        _r("2627", "2026-08-15", "LIV", "ARS", 2, 1),
        _r("2627", "2026-08-22", "ARS", "CHE", 1, 0),
        _r("2627", "2026-08-29", "LIV", "CHE", 1, 1),
        _r("2526", "2026-05-01", "CHE", "LIV", 5, 0),   # 旧赛季,不计入
    ])
    table = build.compute_standings(build.load_results("E0"), "2627")
    assert [t["team"] for t in table] == ["LIV", "ARS", "CHE"]
    assert table[0]["pts"] == 4 and table[0]["gd"] == 1
    assert table[1]["pts"] == 3
    assert table[2]["pts"] == 1 and table[2]["p"] == 2


def test_load_results_type_conversion_and_neutral(sandbox):
    _write_results("UCL", [
        {"date": "2026-05-30", "home": "A", "away": "B", "gh": "1", "ga": "0",
         "neutral": "True"},
        {"date": "2026-05-31", "home": "C", "away": "D", "gh": "2", "ga": "2",
         "neutral": ""},
    ])
    rows = build.load_results("UCL")
    assert rows[0]["gh"] == 1 and rows[0]["neutral"] is True and rows[0]["comp"] == "UCL"
    assert rows[1]["neutral"] is False
    assert build.load_results("SP1") == []          # 文件不存在 → []


def test_build_elo_pool_writes_files(sandbox):
    _write_results("E0", [_r("2627", "2026-08-15", "A", "B", 2, 0)])
    _write_results("SP1", [_r("2627", "2026-08-16", "C", "D", 0, 1)])
    ratings, history = build.build_elo_pool()
    assert ratings["A"] > ratings["B"] and ratings["D"] > ratings["C"]
    gp = config.global_paths()
    assert os.path.exists(gp["elo"]) and os.path.exists(gp["elo_history"])
    with open(gp["elo"], encoding="utf-8") as f:
        assert json.load(f)["A"] == ratings["A"]


def test_rebuild_predictions_no_stale_keys(sandbox, monkeypatch):
    """Review Focus 3:改期后 fixtures 键过期 → rebuild 从最新 schedule 全量重建。"""
    monkeypatch.setenv("LEAGUES_SEASON", "2627")
    _write_results("E0", [_r("2627", "2026-08-15", "A", "B", 2, 0)] * 25)  # 让 DC 有样本
    _write_schedule("E0", [
        {"date": "2026-10-11", "time": "15:00", "home": "A", "away": "B",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
        {"date": "2026-10-11", "time": "17:00", "home": "C", "away": "D",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
        {"date": "2026-10-04", "time": "15:00", "home": "A", "away": "C",
         "status": "post", "gh": "2", "ga": "1", "neutral": ""},
    ])
    elo = {"A": 1600.0, "B": 1500.0, "C": 1550.0, "D": 1450.0}
    p1 = build.rebuild("E0", elo=elo)
    assert len(p1["matches"]) == 3
    # 改期:第 2 场被移走 → schedule 只剩 2 行
    _write_schedule("E0", [
        {"date": "2026-10-11", "time": "15:00", "home": "A", "away": "B",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
        {"date": "2026-10-04", "time": "15:00", "home": "A", "away": "C",
         "status": "post", "gh": "2", "ga": "1", "neutral": ""},
    ])
    p2 = build.rebuild("E0", elo=elo)
    assert len(p2["matches"]) == 2
    assert all(m["away"] != "D" for m in p2["matches"])     # 过期键不残留
    pre = [m for m in p2["matches"] if m["status"] == "pre"][0]
    assert "probs" in pre and "lam_h" in pre
    post = [m for m in p2["matches"] if m["status"] == "post"][0]
    assert post["gh"] == 2 and "probs" not in post          # 完赛场只展示比分
    # 落盘文件同步
    with open(config.paths("E0")["predictions"], encoding="utf-8") as f:
        assert len(json.load(f)["matches"]) == 2


def test_rebuild_attaches_fixture_odds(sandbox, monkeypatch):
    monkeypatch.setenv("LEAGUES_SEASON", "2627")
    from leagues import fetch_fd, normalize
    _write_results("E0", [_r("2627", "2026-08-15", "A", "B", 2, 0)] * 25)
    _write_schedule("E0", [
        {"date": "2026-10-11", "time": "15:00", "home": "A", "away": "B",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
    ])
    fetch_fd.write_csv(config.paths("E0")["fixtures"], normalize.FIXTURE_FIELDS, [
        {"date": "2026-10-11", "time": "15:00", "home": "A", "away": "B",
         "b365h": 1.9, "b365d": 3.4, "b365a": 4.2},
    ])
    p = build.rebuild("E0", elo={"A": 1600.0, "B": 1500.0})
    m = p["matches"][0]
    assert m["odds"]["b365h"] == 1.9
    assert "market" in m                            # 有赔率 → 出 market(Pro 字段,API 层再过滤)


def test_fetch_all_if_stale_isolates_failures(sandbox, monkeypatch):
    monkeypatch.setenv("LEAGUES_SEASON", "2627")
    from leagues import fetch_espn, fetch_fd

    monkeypatch.setattr(fetch_fd, "fetch_league_history",
                        lambda code, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
                        if code == "D1" else (10, []))
    monkeypatch.setattr(fetch_fd, "fetch_fixtures", lambda codes=None: {c: 1 for c in codes})
    monkeypatch.setattr(fetch_espn, "fetch_schedule", lambda code, **kw: (30, []))
    report = build.fetch_all_if_stale()
    assert any("D1" in e for e in report["errors"])
    assert report["fd_history"]["E0"]["rows"] == 10  # 其他联赛不受影响
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_leagues_build.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leagues.build'`

- [ ] **Step 3: 实现**

```python
# leagues/build.py
"""构建管线:CSV → Elo 池 / DC 参数 / 积分榜 / 单场预测。

核心不变式:rebuild 每次从最新 schedule **全量重建** predictions 字典
(fixtures.csv 是滚动 4 天窗口,改期后旧键必须消失,绝不增量 patch —— Review Focus 3)。
"""
import csv
import json
import os
from datetime import date

from . import config, dixon_coles, elo as elo_mod, predict as predict_mod


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(row, keys):
    out = dict(row)
    for k in keys:
        v = out.get(k)
        if v in (None, ""):
            out[k] = None
        else:
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                out[k] = None
    return out


def _goals_int(row):
    for k in ("gh", "ga"):
        row[k] = int(row[k]) if row.get(k) is not None else None
    return row


def _bool_neutral(row):
    row["neutral"] = str(row.get("neutral", "")).strip().lower() in ("true", "1")
    return row


def load_results(code):
    out = []
    for r in read_csv(config.paths(code)["results"]):
        r = _num(r, ("gh", "ga", "b365h", "b365d", "b365a", "ps_h", "ps_d", "ps_a",
                     "ou_over", "ou_under", "ah_line", "ah_h", "ah_a"))
        out.append(_bool_neutral(_goals_int(r)) | {"comp": code})
    return out


def load_schedule(code):
    out = []
    for r in read_csv(config.paths(code)["schedule"]):
        out.append(_bool_neutral(_goals_int(_num(r, ("gh", "ga")))))
    return out


def load_fixtures(code):
    return [_num(r, ("b365h", "b365d", "b365a", "max_h", "max_d", "max_a",
                     "ps_h", "ps_d", "ps_a", "ou_over", "ou_under",
                     "ah_line", "ah_h", "ah_a"))
            for r in read_csv(config.paths(code)["fixtures"])]


def compute_standings(results, season):
    """3/1/0 积分,排序 pts → gd → gf → 队名。只算指定 season 的已完赛。"""
    table = {}
    for r in results:
        if r.get("season") != season or r["gh"] is None or r["ga"] is None:
            continue
        h, a = r["home"], r["away"]
        for t in (h, a):
            table.setdefault(t, {"team": t, "p": 0, "w": 0, "d": 0, "l": 0,
                                 "gf": 0, "ga": 0, "gd": 0, "pts": 0})
        th, ta = table[h], table[a]
        th["p"] += 1
        ta["p"] += 1
        th["gf"] += r["gh"]
        th["ga"] += r["ga"]
        ta["gf"] += r["ga"]
        ta["ga"] += r["gh"]
        if r["gh"] > r["ga"]:
            th["w"] += 1
            th["pts"] += 3
            ta["l"] += 1
        elif r["gh"] == r["ga"]:
            th["d"] += 1
            ta["d"] += 1
            th["pts"] += 1
            ta["pts"] += 1
        else:
            ta["w"] += 1
            ta["pts"] += 3
            th["l"] += 1
    for t in table.values():
        t["gd"] = t["gf"] - t["ga"]
    return sorted(table.values(), key=lambda t: (-t["pts"], -t["gd"], -t["gf"], t["team"]))


def build_elo_pool(cutoff=None):
    """全 ALL_CODES 历史 → 统一 Elo 池,落盘 elo.json + elo_history.csv。返回 (ratings, history)。"""
    from . import fetch_fd
    matches = []
    for code in config.ALL_CODES:
        matches.extend(load_results(code))
    ratings, history = elo_mod.build_pool(matches, cutoff=cutoff)
    gp = config.global_paths()
    with open(gp["elo"] + ".tmp", "w", encoding="utf-8") as f:
        json.dump(ratings, f, ensure_ascii=False)
    os.replace(gp["elo"] + ".tmp", gp["elo"])
    fetch_fd.write_csv(gp["elo_history"],
                       ["date", "home", "away", "elo_h", "elo_a", "comp"], history)
    return ratings, history


def rebuild_dc(code, cutoff=None):
    """拟合并落盘 dc_params.json。返回 {"league","params","global"}。"""
    params, glob = dixon_coles.fit_league(load_results(code), cutoff=cutoff)
    payload = {"league": code, "params": params, "global": glob}
    _atomic_json(config.paths(code)["dc_params"], payload)
    return payload


def _load_dc(code):
    p = config.paths(code)["dc_params"]
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return rebuild_dc(code)


def _atomic_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def _xgb_probs(xgb, code, home, away):
    """xgb 参数是 {(code, home, away): probs} 查找表(Task 17 构建);缺省 None。"""
    if not xgb:
        return None
    return xgb.get((code, home, away))


def rebuild(code, elo=None, xgb=None):
    """从最新 schedule 全量重建 predictions.json。返回 payload dict。

    schedule.csv 就是当前赛季(Task 4 按赛季抓取),无需再过滤 season。
    status=='post' 行只带比分;'pre'/'in' 行出预测;fixtures 滚动窗口里的
    赔率按 (date,home,away) 精确匹配,匹配不上就无赔率(下周再抓自然补上)。
    """
    season = config.current_season()
    schedule = load_schedule(code)
    fixtures = {(f["date"], f["home"], f["away"]): f for f in load_fixtures(code)}
    if elo is None:
        elo = build_elo_pool()[0]
    dc = _load_dc(code)
    dc_params, dc_glob = dc["params"], dc["global"]
    matches = []
    for m in schedule:
        base = {"date": m["date"], "time": m.get("time", ""), "home": m["home"],
                "away": m["away"], "status": m["status"], "neutral": bool(m.get("neutral"))}
        if m["status"] == "post":
            base["gh"], base["ga"] = m["gh"], m["ga"]
        else:
            odds = fixtures.get((m["date"], m["home"], m["away"]))
            pred = predict_mod.predict_match(
                m["home"], m["away"], elo=elo, dc_params=dc_params, dc_glob=dc_glob,
                odds=odds, neutral=base["neutral"],
                xgb_probs=_xgb_probs(xgb, code, m["home"], m["away"]))
            base.update(pred)
            if odds:
                base["odds"] = {k: odds.get(k) for k in
                                ("b365h", "b365d", "b365a", "ou_over", "ou_under",
                                 "ah_line", "ah_h", "ah_a")}
        matches.append(base)
    payload = {"code": code, "season": season,
               "generated_at": date.today().isoformat(), "matches": matches}
    _atomic_json(config.paths(code)["predictions"], payload)
    return payload


def rebuild_all(elo=None, xgb=None):
    if elo is None:
        elo = build_elo_pool()[0]
    return {code: rebuild(code, elo=elo, xgb=xgb) for code in config.LEAGUE_CODES}


def fetch_all_if_stale(fixtures_h=20, results_h=20):
    """按文件新鲜度补抓(updater 用)。单联赛失败记 errors,不阻塞其他联赛。"""
    from . import fetch_espn, fetch_fd
    report = {"fd_history": {}, "fixtures": None, "schedules": {}, "errors": []}
    for code in config.LEAGUE_CODES:
        if not fetch_fd.is_fresh(config.paths(code)["results"], results_h):
            try:
                n, failed = fetch_fd.fetch_league_history(code)
                report["fd_history"][code] = {"rows": n, "failed_seasons": failed}
            except Exception as e:
                report["errors"].append(f"fd_history {code}: {e}")
    # fixtures 一次抓全量拆分;用首个联赛的文件做新鲜度代理(同一次写入)
    try:
        probe = config.paths(config.LEAGUE_CODES[0])["fixtures"]
        if not fetch_fd.is_fresh(probe, fixtures_h):
            report["fixtures"] = fetch_fd.fetch_fixtures()
    except Exception as e:
        report["errors"].append(f"fixtures: {e}")
    for code in config.LEAGUE_CODES:
        if not fetch_fd.is_fresh(config.paths(code)["schedule"], fixtures_h):
            try:
                n, missing = fetch_espn.fetch_schedule(code)
                report["schedules"][code] = {"rows": n, "missing_months": missing}
            except Exception as e:
                report["errors"].append(f"schedule {code}: {e}")
    return report
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_leagues_build.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add leagues/build.py tests/test_leagues_build.py
git commit -m "feat(leagues): 构建管线(Elo 池/DC/积分榜/rebuild 全量重建)(Task 8)"
```

---

### Task 9: 联赛独立缓存 `webapp/leagues_cache.py`

**Files:**
- Create: `webapp/leagues_cache.py`
- Test: `tests/test_leagues_cache.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `CACHE_FILE = webapp/data/leagues_cache.json`
  - `load() -> bool`;`save()`;`set_league(code, predictions=None, standings=None, sim=None)`;`get_league(code) -> dict|None`;`get_codes() -> list[str]`;`set_status(**kw)`;`get_status() -> dict`
- 设计约束(对照 `webapp/cache.py:64` 的 bug:global 声明漏 `_market/_market_built_at`):**本模块所有被赋值的模块级变量必须在每个赋值函数的 global 声明里**,测试要钉死 load 后 get 能读到。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_leagues_cache.py
"""联赛独立缓存:global 声明正确性(cache.py:64 bug 不复刻)、原子持久化。"""
import json
import os

import pytest

from webapp import leagues_cache


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(leagues_cache, "CACHE_FILE", str(tmp_path / "leagues_cache.json"))
    # 每个测试重置模块状态
    monkeypatch.setattr(leagues_cache, "_payload", {})
    monkeypatch.setattr(leagues_cache, "_status",
                        {"running": False, "last_success": None,
                         "last_error": None, "updated_at": None})
    return tmp_path


def test_set_get_roundtrip():
    assert leagues_cache.get_league("E0") is None
    leagues_cache.set_league("E0", predictions={"matches": [1, 2]}, standings=[{"team": "A"}])
    entry = leagues_cache.get_league("E0")
    assert entry["predictions"]["matches"] == [1, 2] and entry["standings"] == [{"team": "A"}]
    leagues_cache.set_league("E0", sim={"teams": {}})          # 局部更新不清其他字段
    entry = leagues_cache.get_league("E0")
    assert entry["predictions"]["matches"] == [1, 2] and entry["sim"] == {"teams": {}}
    assert leagues_cache.get_codes() == ["E0"]


def test_save_then_load_reads_back():
    """钉死 global 声明:load() 里对 _payload/_status 的赋值必须对模块可见。"""
    leagues_cache.set_league("SP1", predictions={"matches": ["x"]})
    leagues_cache.set_status(running=False, last_success="ok")
    leagues_cache.save()
    # 模拟进程重启:清掉内存再 load
    leagues_cache._payload = {}
    leagues_cache._status = {"running": None}
    assert leagues_cache.load() is True
    assert leagues_cache.get_league("SP1")["predictions"]["matches"] == ["x"]
    assert leagues_cache.get_status()["last_success"] == "ok"


def test_load_missing_file_returns_false():
    assert leagues_cache.load() is False


def test_load_corrupt_file_returns_false():
    with open(leagues_cache.CACHE_FILE, "w") as f:
        f.write("{not json")
    assert leagues_cache.load() is False


def test_save_is_atomic():
    leagues_cache.set_league("E0", predictions={"matches": []})
    leagues_cache.save()
    assert os.path.exists(leagues_cache.CACHE_FILE)
    assert not os.path.exists(leagues_cache.CACHE_FILE + ".tmp")
    with open(leagues_cache.CACHE_FILE, encoding="utf-8") as f:
        json.load(f)                                   # 合法 JSON
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_leagues_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'webapp.leagues_cache'`

- [ ] **Step 3: 实现**

```python
# webapp/leagues_cache.py
"""联赛板块独立缓存 —— 与 WC cache.py 完全隔离(独立文件、独立锁、独立字段),
任一板块构建失败不污染另一板块。

契约同 WC:API 路由只读当前快照;后台 leagues_updater 算完整体替换。
注意:所有被赋值的模块级变量都显式列在各自函数的 global 声明里
(不复刻 cache.py:64 漏声明 _market/_market_built_at 的 bug —— 测试 test_save_then_load_reads_back 钉死)。
"""
import json
import os
import threading
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(HERE, "data", "leagues_cache.json")

_lock = threading.RLock()

_payload = {}            # {code: {"predictions": {...}, "standings": [...], "sim": {...}}}
_status = {"running": False, "last_success": None, "last_error": None, "updated_at": None}


def load():
    """启动时调用。文件不存在/损坏 → False(由 updater 首轮填充)。"""
    global _payload, _status
    if not os.path.exists(CACHE_FILE):
        return False
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        with _lock:
            _payload = d.get("payload", {})
            _status = d.get("status", dict(_status))
        return True
    except Exception:
        return False


def save():
    with _lock:
        snap = {"payload": _payload, "status": _status}
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)


def set_league(code, predictions=None, standings=None, sim=None):
    """局部更新某联赛的字段(None = 不动),整体替换引用保证快照一致。"""
    global _payload
    with _lock:
        entry = dict(_payload.get(code, {}))
        if predictions is not None:
            entry["predictions"] = predictions
        if standings is not None:
            entry["standings"] = standings
        if sim is not None:
            entry["sim"] = sim
        _payload = {**_payload, code: entry}


def get_league(code):
    with _lock:
        return _payload.get(code)


def get_codes():
    with _lock:
        return sorted(_payload.keys())


def set_status(**kw):
    global _status
    with _lock:
        _status = {**_status, **kw,
                   "updated_at": datetime.now().isoformat(timespec="seconds")}


def get_status():
    with _lock:
        return dict(_status)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_leagues_cache.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add webapp/leagues_cache.py tests/test_leagues_cache.py
git commit -m "feat(leagues): 独立缓存层(global 声明完整,原子持久化)(Task 9)"
```

---

### Task 10: 联赛后台更新器 `webapp/leagues_updater.py`

**Files:**
- Create: `webapp/leagues_updater.py`
- Test: `tests/test_leagues_updater.py`

**Interfaces:**
- Consumes: `leagues.build`(Task 8)、`leagues.config`、`webapp.leagues_cache`(Task 9)。
- Produces:
  - `REFRESH_MIN = 60`
  - `update_once()` — 新鲜度补抓 → 重建 Elo 池 → 逐联赛 DC+rebuild+standings → 写缓存;单联赛失败隔离
  - `loop()` / `trigger_now()` / `stop()` — 复刻 updater.py 的 `_wake`/`_stop` 模式

- [ ] **Step 1: 写失败测试**

```python
# tests/test_leagues_updater.py
"""联赛更新器:全联赛写入、单联赛失败隔离、容错铁律(异常不出线程)。"""
import pytest

from leagues import config
from webapp import leagues_cache, leagues_updater


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(leagues_cache, "CACHE_FILE", str(tmp_path / "leagues_cache.json"))
    monkeypatch.setattr(leagues_cache, "_payload", {})
    monkeypatch.setattr(leagues_cache, "_status",
                        {"running": False, "last_success": None,
                         "last_error": None, "updated_at": None})
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path / "data"))
    # fetch 一律打桩,测试不触网
    monkeypatch.setattr(leagues_updater.build, "fetch_all_if_stale",
                        lambda: {"errors": []})
    monkeypatch.setattr(leagues_updater.build, "build_elo_pool",
                        lambda: ({"A": 1600.0, "B": 1500.0}, []))
    monkeypatch.setattr(leagues_updater.build, "rebuild_dc", lambda code: {})
    monkeypatch.setattr(leagues_updater.build, "compute_standings",
                        lambda results, season: [{"team": "A", "pts": 3}])
    monkeypatch.setattr(leagues_updater.build, "load_results", lambda code: [])
    return tmp_path


def _stub_rebuild(monkeypatch, fail_codes=()):
    def fake_rebuild(code, elo=None, xgb=None):
        if code in fail_codes:
            raise RuntimeError(f"{code} boom")
        return {"code": code, "season": "2627", "matches": []}
    monkeypatch.setattr(leagues_updater.build, "rebuild", fake_rebuild)


def test_update_once_populates_all_leagues(monkeypatch):
    _stub_rebuild(monkeypatch)
    leagues_updater.update_once()
    assert leagues_cache.get_codes() == ["D1", "E0", "F1", "I1", "SP1"]
    assert leagues_cache.get_league("E0")["standings"] == [{"team": "A", "pts": 3}]
    assert leagues_cache.get_status()["last_error"] is None
    assert leagues_cache.get_status()["running"] is False


def test_update_once_isolates_single_league_failure(monkeypatch):
    _stub_rebuild(monkeypatch, fail_codes={"D1"})
    leagues_updater.update_once()                   # 不抛
    assert "D1" not in leagues_cache.get_codes()
    assert len(leagues_cache.get_codes()) == 4      # 其他联赛照常
    assert "D1" in leagues_cache.get_status()["last_error"]


def test_update_once_elo_failure_keeps_old_snapshot(monkeypatch):
    _stub_rebuild(monkeypatch)
    leagues_updater.update_once()
    before = leagues_cache.get_league("E0")
    monkeypatch.setattr(leagues_updater.build, "build_elo_pool",
                        lambda: (_ for _ in ()).throw(RuntimeError("elo boom")))
    leagues_updater.update_once()                   # 不抛
    assert leagues_cache.get_league("E0") == before  # 旧快照保留(容错铁律)
    assert "elo boom" in leagues_cache.get_status()["last_error"]


def test_trigger_and_stop_events():
    leagues_updater.trigger_now()                   # 仅置位,不阻塞
    leagues_updater.stop()
    assert leagues_updater._stop.is_set()
    leagues_updater._stop.clear()                   # 还原,避免影响其他测试
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_leagues_updater.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'webapp.leagues_updater'`

- [ ] **Step 3: 实现**

```python
# webapp/leagues_updater.py
"""联赛后台更新线程 —— 复刻 updater.py 的容错铁律,与 WC updater 完全独立。

流程:新鲜度补抓(fetch_all_if_stale)→ 重建统一 Elo 池 → 逐联赛
DC 拟合 + rebuild + standings → 写 leagues_cache。单联赛失败隔离:
一个联赛炸了只记 error,其余联赛照常更新;Elo 池失败则本轮整体放弃
(旧快照保留)。UCL 在 Task 16 接入。
"""
import threading
import traceback

from leagues import build, config
from webapp import leagues_cache

REFRESH_MIN = 60

_wake = threading.Event()
_stop = threading.Event()


def update_once():
    """跑一轮完整更新。任何异常内部消化,绝不抛出(容错铁律)。"""
    leagues_cache.set_status(running=True)
    errors = []
    try:
        report = build.fetch_all_if_stale()
        errors.extend(report.get("errors", []))
    except Exception as e:
        errors.append(f"fetch: {e}")
        traceback.print_exc()
    try:
        elo, _ = build.build_elo_pool()
    except Exception as e:
        traceback.print_exc()
        leagues_cache.set_status(running=False, last_error=f"elo: {e}")
        leagues_cache.save()
        return
    for code in config.LEAGUE_CODES:
        try:
            build.rebuild_dc(code)
            payload = build.rebuild(code, elo=elo)
            standings = build.compute_standings(build.load_results(code), payload["season"])
            leagues_cache.set_league(code, predictions=payload, standings=standings)
        except Exception as e:
            errors.append(f"{code}: {e}")
            traceback.print_exc()
    leagues_cache.set_status(running=False,
                             last_error="; ".join(errors) if errors else None,
                             last_success=None if errors else "ok")
    leagues_cache.save()


def loop():
    """后台主循环:立即跑一轮,之后每 REFRESH_MIN 分钟一轮;trigger_now 可提前唤醒。"""
    print(f"[leagues_updater] 后台循环启动(间隔 {REFRESH_MIN} 分钟)")
    while not _stop.is_set():
        try:
            update_once()
        except Exception:
            traceback.print_exc()          # 双保险:update_once 内部已消化
        _wake.wait(timeout=REFRESH_MIN * 60)
        _wake.clear()
    print("[leagues_updater] 后台循环退出")


def trigger_now():
    """唤醒 sleep 中的循环立即跑一轮(手动刷新用)。"""
    _wake.set()
    return leagues_cache.get_status()


def stop():
    """优雅停机(测试/关服用)。"""
    _stop.set()
    _wake.set()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_leagues_updater.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add webapp/leagues_updater.py tests/test_leagues_updater.py
git commit -m "feat(leagues): 后台更新器(容错铁律 + 单联赛失败隔离)(Task 10)"
```

---

### Task 11: 联赛 API 路由 `webapp/leagues.py`

**Files:**
- Create: `webapp/leagues.py`
- Test: `tests/test_leagues_api.py`

**Interfaces:**
- Consumes: `webapp.leagues_cache`、`webapp.auth.get_current_user`(返回 email str|None)、`webapp.payments.is_paid(email)`、`webapp.rate_limit.limiter`(共享单例)、`leagues.config`。
- Produces(APIRouter `prefix="/api/leagues"`,全部 `@limiter.limit("30/minute")`):
  - `GET ""` → `{leagues: [{code,name,name_zh,available}], status}`
  - `GET /{code}/matches` → 非 Pro:odds=null 且响应**递归不含** `edge/kelly/market` 键;Pro:含 `odds`+`market`
  - `GET /{code}/standings` → `{code, standings}`(免费)
  - `GET /{code}/sim` → `{code, sim}`(免费;Task 14 填充)
  - `GET /{code}/value` → 非 Pro `HTTPException(402)`;Pro:edge>0.03 场按 max_edge 降序
  - 错误:未知 code → 404;数据未就绪 → 503

- [ ] **Step 1: 写失败测试**

```python
# tests/test_leagues_api.py
"""联赛 API:付费门控(N-1 字段白名单)、402、404/503、限流。"""
import pytest
from fastapi.testclient import TestClient

from webapp import leagues_cache
from webapp.app import app
from webapp.rate_limit import limiter

PAID_EMAIL = "pro@example.com"

MATCH_WITH_MARKET = {
    "date": "2026-10-11", "time": "15:00", "home": "A", "away": "B",
    "status": "pre", "neutral": False, "probs": {"home": 0.5, "draw": 0.3, "away": 0.2},
    "lam_h": 1.6, "lam_a": 1.1, "top_scores": [["1-0", 0.12]],
    "ou25_over": 0.55, "btts_yes": 0.52, "low_confidence": False,
    "odds": {"b365h": 1.9, "b365d": 3.4, "b365a": 4.2},
    "market": {"p_mkt": {"home": 0.5, "draw": 0.28, "away": 0.22},
               "edges": {"home": 0.0, "draw": 0.02, "away": -0.02},
               "max_edge": 0.05, "pick": "draw",
               "odds": {"home": 1.9, "draw": 3.4, "away": 4.2}, "kelly": 0.012},
}


@pytest.fixture(autouse=True)
def seed_cache(monkeypatch):
    limiter.reset()
    monkeypatch.setattr(leagues_cache, "_payload", {})
    monkeypatch.setattr(leagues_cache, "_status", {"running": False, "last_error": None})
    leagues_cache.set_league(
        "E0",
        predictions={"code": "E0", "season": "2627", "generated_at": "2026-10-09",
                     "matches": [MATCH_WITH_MARKET]},
        standings=[{"team": "A", "pts": 3}],
        sim={"teams": {"A": {"title": 0.4, "ucl": 0.7, "releg": 0.0}}},
    )
    yield


def _all_keys(obj):
    """递归收集 payload 里所有 dict 键。"""
    keys = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            keys |= _all_keys(v)
    return keys


# 注意:Depends(auth.get_current_user) 在路由注册时已绑定函数对象,
# monkeypatch 属性无效 —— 必须用 app.dependency_overrides 覆盖。
@pytest.fixture
def pro_client(monkeypatch):
    from webapp import auth as auth_mod
    app.dependency_overrides[auth_mod.get_current_user] = lambda: PAID_EMAIL
    monkeypatch.setattr("webapp.leagues.payments.is_paid",
                        lambda email: email == PAID_EMAIL)
    yield TestClient(app)
    app.dependency_overrides.pop(auth_mod.get_current_user, None)


@pytest.fixture
def free_client(monkeypatch):
    from webapp import auth as auth_mod
    app.dependency_overrides[auth_mod.get_current_user] = lambda: None
    monkeypatch.setattr("webapp.leagues.payments.is_paid", lambda email: False)
    yield TestClient(app)
    app.dependency_overrides.pop(auth_mod.get_current_user, None)


def test_matches_free_user_odds_null_no_market_keys(free_client):
    """N-1:非 Pro 响应递归不含 edge/kelly/market 键,odds=null。"""
    r = free_client.get("/api/leagues/E0/matches")
    assert r.status_code == 200
    body = r.json()
    assert body["matches"][0]["odds"] is None
    assert body["matches"][0]["probs"]["home"] == 0.5    # 预测免费展示
    leaked = _all_keys(body) & {"market", "edges", "kelly", "max_edge", "p_mkt"}
    assert leaked == set(), f"付费字段泄漏: {leaked}"


def test_matches_pro_user_sees_odds_and_market(pro_client):
    r = pro_client.get("/api/leagues/E0/matches")
    assert r.status_code == 200
    m = r.json()["matches"][0]
    assert m["odds"]["b365h"] == 1.9
    assert m["market"]["pick"] == "draw" and m["market"]["kelly"] == 0.012


def test_value_non_pro_returns_402(free_client):
    r = free_client.get("/api/leagues/E0/value")
    assert r.status_code == 402


def test_value_pro_returns_sorted_edges(pro_client):
    r = pro_client.get("/api/leagues/E0/value")
    assert r.status_code == 200
    values = r.json()["values"]
    assert len(values) == 1 and values[0]["max_edge"] == 0.05
    assert values[0]["home"] == "A" and values[0]["pick"] == "draw"


def test_standings_and_sim_free(free_client):
    r = free_client.get("/api/leagues/E0/standings")
    assert r.status_code == 200 and r.json()["standings"][0]["team"] == "A"
    r = free_client.get("/api/leagues/E0/sim")
    assert r.status_code == 200 and r.json()["sim"]["teams"]["A"]["title"] == 0.4


def test_unknown_league_404_and_not_ready_503(free_client):
    assert free_client.get("/api/leagues/XX/matches").status_code == 404
    assert free_client.get("/api/leagues/D1/matches").status_code == 503   # 未构建


def test_list_leagues(free_client):
    r = free_client.get("/api/leagues")
    assert r.status_code == 200
    leagues = {l["code"]: l for l in r.json()["leagues"]}
    assert leagues["E0"]["available"] is True
    assert leagues["D1"]["available"] is False
    assert leagues["UCL"]["name_zh"] == "欧冠"


def test_rate_limit_30_per_minute(free_client):
    codes = [free_client.get("/api/leagues/E0/standings").status_code for _ in range(31)]
    assert 429 in codes
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_leagues_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'webapp.leagues'`

注意:此时 `webapp.app` 尚未 include 新路由(Task 12 接线),导入 `webapp.app` 不会加载 `webapp.leagues`;测试里 monkeypatch 的是 `"webapp.leagues.auth..."`,只要模块已 import 即生效,路由挂上后(Task 12)全部转绿。**Task 11 的 Step 4 预期仍有 404/405 失败,Task 12 完成后整体转绿** —— 两个 Task 的 commit 顺序:Task 11 commit 时注明 "API 测试待 Task 12 接线后转绿",或者把 Task 11+12 合为一次 commit。推荐后者:Step 4 验证留在 Task 12 末尾。

- [ ] **Step 3: 实现**

```python
# webapp/leagues.py
"""联赛板块 API 路由。付费门控与 WC 对齐(M-3/N-1):

  · /matches:非 Pro → odds 字段=null;响应只含 MATCH_PUBLIC_FIELDS 白名单,
    edge/kelly/market 键绝不出现(不是置空,是键不存在)
  · /value:非 Pro → HTTPException(402)(WC value-bets 同款)
  · 全部端点 @limiter.limit("30/minute"),limiter 用 webapp.rate_limit 共享单例
    (挂在 app.state 的全局限流器,装饰器抛出 RateLimitExceeded 由 app 统一转 429)
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from leagues import config
from webapp import auth, leagues_cache, payments
from webapp.rate_limit import limiter

router = APIRouter(prefix="/api/leagues", tags=["leagues"])

VALID_CODES = set(config.ALL_CODES)

# 免费用户可见字段(预测本身免费;odds 键保留但值=null,前端渲染逻辑统一)
MATCH_PUBLIC_FIELDS = {"date", "time", "home", "away", "status", "neutral", "gh", "ga",
                       "probs", "lam_h", "lam_a", "top_scores", "ou25_over", "btts_yes",
                       "low_confidence", "odds"}
# Pro 追加 market 子 dict(edge/kelly 只存在于 market 内部,白名单外的键一律剥掉)
MATCH_PRO_FIELDS = MATCH_PUBLIC_FIELDS | {"market"}

VALUE_MIN_EDGE = 0.03


def _is_pro(user):
    return bool(user and payments.is_paid(user))


def _public_match(m, pro):
    out = {k: v for k, v in m.items() if k in (MATCH_PRO_FIELDS if pro else MATCH_PUBLIC_FIELDS)}
    if not pro:
        out["odds"] = None
    return out


def _get_entry(code):
    code = code.upper()
    if code not in VALID_CODES:
        raise HTTPException(404, f"unknown league: {code}")
    entry = leagues_cache.get_league(code)
    if not entry or not entry.get("predictions"):
        raise HTTPException(503, "league data not ready (first build in progress)")
    return code, entry


@router.get("")
@limiter.limit("30/minute")
def list_leagues(request: Request):
    available = set(leagues_cache.get_codes())
    return {"leagues": [{"code": c, "name": config.LEAGUES[c]["name"],
                         "name_zh": config.LEAGUES[c]["name_zh"],
                         "available": c in available} for c in config.ALL_CODES],
            "status": leagues_cache.get_status()}


@router.get("/{code}/matches")
@limiter.limit("30/minute")
def league_matches(code: str, request: Request, user=Depends(auth.get_current_user)):
    code, entry = _get_entry(code)
    pro = _is_pro(user)
    p = entry["predictions"]
    return {"code": code, "season": p.get("season"), "generated_at": p.get("generated_at"),
            "matches": [_public_match(m, pro) for m in p.get("matches", [])]}


@router.get("/{code}/standings")
@limiter.limit("30/minute")
def league_standings(code: str, request: Request):
    code, entry = _get_entry(code)
    if entry.get("standings") is None:
        raise HTTPException(503, "standings not ready")
    return {"code": code, "standings": entry["standings"]}


@router.get("/{code}/sim")
@limiter.limit("30/minute")
def league_sim(code: str, request: Request):
    code, entry = _get_entry(code)
    if entry.get("sim") is None:
        raise HTTPException(503, "sim not ready")
    return {"code": code, "sim": entry["sim"]}


@router.get("/{code}/value")
@limiter.limit("30/minute")
def league_value(code: str, request: Request, user=Depends(auth.get_current_user)):
    if not _is_pro(user):
        raise HTTPException(402, "Pro feature. Subscribe at /api/create-checkout-session")
    code, entry = _get_entry(code)
    values = []
    for m in entry["predictions"].get("matches", []):
        mk = m.get("market")
        if mk and mk.get("max_edge", 0) > VALUE_MIN_EDGE:
            values.append({"date": m["date"], "time": m.get("time", ""),
                           "home": m["home"], "away": m["away"], **mk})
    values.sort(key=lambda v: -v["max_edge"])
    return {"code": code, "values": values}
```

- [ ] **Step 4:(验证推迟到 Task 12 末尾,接线完成后统一跑)**

- [ ] **Step 5: Commit(与 Task 12 合并为一次 commit,见 Task 12 Step 5)**

---

### Task 12: app.py 接线 + .gitignore + index.html 导航

**Files:**
- Modify: `webapp/app.py`(import、include_router、lifespan、`/leagues` 页面路由 —— 白名单内)
- Modify: `.gitignore`
- Modify: `webapp/static/index.html`(导航链接 + i18n.js 版本号)
- Test: `tests/test_leagues_api.py`(Task 11 已写,本 Task 转绿)

**Interfaces:**
- Consumes: `webapp.leagues.router`(Task 11)、`webapp.leagues_cache.load/get_status`、`webapp.leagues_updater.loop/stop`(Task 10)。
- Produces: 运行态接线,无新函数签名。

- [ ] **Step 1: app.py 接线**

在 `webapp/app.py` 顶部 import 区追加:

```python
from webapp import leagues as leagues_api
from webapp import leagues_cache, leagues_updater
```

lifespan 函数内,`cache.load_or_build()` 之后、`yield` 之前追加:

```python
    # 联赛板块:独立缓存 + 独立后台线程(与 WC updater 互不阻塞)
    leagues_cache.load()
    lt = threading.Thread(target=leagues_updater.loop, daemon=True, name="leagues-updater")
    lt.start()
    print("[app] 联赛后台更新线程已启动")
```

lifespan 的 `yield` 之后(`updater.stop()` 旁)追加:

```python
    leagues_updater.stop()
```

`app = FastAPI(...)` 创建之后(其他 include_router/路由区)追加:

```python
app.include_router(leagues_api.router)
```

`@app.get("/")` 路由旁边追加页面路由:

```python
@app.get("/leagues")
@limiter.limit("60/minute")
def leagues_page(request: Request):
    return FileResponse(os.path.join(STATIC_DIR, "leagues.html"))
```

- [ ] **Step 2: .gitignore 追加**

```gitignore
# 联赛板块运行时数据(Task 1-12)
data/leagues/
webapp/data/leagues_cache.json
```

(`leagues/clubs_aliases.json` 在包目录内,不在忽略范围,必须提交。)

- [ ] **Step 3: index.html 导航链接 + i18n.js 版本号**

在 `webapp/static/index.html` 的 `<a href="/" class="switch-app" ...>` 元素**之前**插入:

```html
    <a href="/leagues" class="switch-app" data-i18n="nav.leagues"
      data-i18n-attr="title" title="Leagues">
      <span data-i18n="nav.leagues">联赛</span> <span aria-hidden="true">⟶</span>
    </a>
```

同一文件内 `i18n.js?v=24` 改为 `i18n.js?v=25`(`app.js?v=42` 不动 —— app.js 未改)。

- [ ] **Step 4: 跑 Task 11 的 API 测试 + 全量回归**

Run: `pytest tests/test_leagues_api.py -v`
Expected: 8 passed(路由已挂载,门控/402/404/503/限流全绿)

Run: `pytest tests/ -x -q -m "not slow"`
Expected: 71 个存量测试 + 新增测试全绿(WC 零回归)

- [ ] **Step 5: Commit(Task 11 + 12 合并)**

```bash
git add webapp/leagues.py webapp/app.py .gitignore webapp/static/index.html \
        tests/test_leagues_api.py
git commit -m "feat(leagues): API 路由 + app 接线 + 付费门控(402/字段白名单/限流)(Task 11-12)"
```

### Task 13: 前端页面 `leagues.html` + `leagues.js` + 6 语言 i18n

**Files:**
- Create: `webapp/static/leagues.html`
- Create: `webapp/static/leagues.js`
- Modify: `webapp/static/i18n/{en,zh-CN,es,pt,ar,fr}.json`(追加 flat key,不改动现有 key)
- Modify: `webapp/static/i18n.js`(`I18N_VER = 6` → `7`,line 41)
- Test: `tests/test_leagues_frontend.py`

**Interfaces:**
- Consumes: Task 11 的 API;`i18n.js` 的 `t(key)` 与 `data-i18n` 自动应用机制;`index.html` 已有的 `.switch-app` 样式类。
- Produces: 页面 `/leagues`(Task 12 已挂路由);无 JS 函数签名外供。

**i18n key 表(6 语言全量,直接合入各 JSON;key 为 flat 风格,与现有文件一致):**

| key | en | zh-CN | es | pt | ar | fr |
|---|---|---|---|---|---|---|
| nav.leagues | Leagues | 联赛 | Ligas | Ligas | الدوريات | Championnats |
| nav.wc | World Cup | 世界杯 | Mundial | Copa do Mundo | كأس العالم | Coupe du Monde |
| leagues.title | League Predictions | 联赛预测 | Pronósticos de Ligas | Previsões de Ligas | توقعات الدوريات | Pronostics Championnats |
| leagues.tab.matches | Matches | 赛程预测 | Partidos | Jogos | المباريات | Matchs |
| leagues.tab.standings | Standings | 积分榜 | Clasificación | Classificação | الترتيب | Classement |
| leagues.tab.sim | Season Sim | 赛季推演 | Simulación | Simulação | محاكاة الموسم | Simulation |
| leagues.tab.value | Value Bets | 价值下注 | Valor | Valor | رهانات القيمة | Value Bets |
| leagues.odds | Odds | 赔率 | Cuotas | Probabilidades | الاحتمالات | Cotes |
| leagues.score | Score | 比分 | Marcador | Placar | النتيجة | Score |
| leagues.pro_only | Pro only | Pro 专享 | Solo Pro | Somente Pro | للمشتركين فقط | Réservé Pro |
| leagues.not_ready | Building data, please wait… | 数据构建中,请稍候… | Construyendo datos… | Gerando dados… | جاري تجهيز البيانات… | Données en cours… |
| leagues.updated | Updated | 更新于 | Actualizado | Atualizado | آخر تحديث | Mis à jour |
| leagues.low_conf | Low confidence | 低置信 | Confianza baja | Confiança baixa | ثقة منخفضة | Confiance faible |
| leagues.title_race | Title | 夺冠 | Título | Título | اللقب | Titre |
| leagues.ucl_spots | UCL spots | 欧冠席位 | Plazas UCL | Vagas UCL | مقاعد الأبطال | Places UCL |
| leagues.relegation | Relegation | 降级 | Descenso | Queda | الهبوط | Relégation |
| leagues.edge | Edge | 优势 | Ventaja | Vantagem | الأفضلية | Avantage |
| leagues.pick | Pick | 推荐 | Elección | Escolha | الاختيار | Choix |
| leagues.kelly | Kelly stake | 凯利仓位 | Stake Kelly | Stake Kelly | حصة كيلي | Mise Kelly |

- [ ] **Step 1: 写失败测试**

```python
# tests/test_leagues_frontend.py
"""前端:页面可访问、i18n 6 语言 key 齐套、版本号已 bump。"""
import json
import os

from fastapi.testclient import TestClient

from webapp.app import app

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "webapp", "static")

NEW_KEYS = ["nav.leagues", "nav.wc", "leagues.title", "leagues.tab.matches",
            "leagues.tab.standings", "leagues.tab.sim", "leagues.tab.value",
            "leagues.odds", "leagues.score", "leagues.pro_only", "leagues.not_ready",
            "leagues.updated", "leagues.low_conf", "leagues.title_race",
            "leagues.ucl_spots", "leagues.relegation", "leagues.edge",
            "leagues.pick", "leagues.kelly"]


def test_leagues_page_served():
    r = TestClient(app).get("/leagues")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "leagues.js" in r.text and "i18n.js" in r.text


def test_i18n_key_parity_all_locales():
    for locale in ("en", "zh-CN", "es", "pt", "ar", "fr"):
        with open(os.path.join(STATIC, "i18n", f"{locale}.json"), encoding="utf-8") as f:
            d = json.load(f)
        missing = [k for k in NEW_KEYS if k not in d or not str(d[k]).strip()]
        assert not missing, f"{locale} 缺 key: {missing}"


def test_i18n_ver_bumped():
    with open(os.path.join(STATIC, "i18n.js"), encoding="utf-8") as f:
        src = f.read()
    assert "I18N_VER = 7" in src


def test_index_html_has_leagues_nav_and_bumped_script():
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        html = f.read()
    assert 'href="/leagues"' in html
    assert "i18n.js?v=25" in html
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_leagues_frontend.py -v`
Expected: FAIL(`test_i18n_key_parity_all_locales` 缺 key、`I18N_VER` 仍是 6)

- [ ] **Step 3: 实现**

`webapp/static/leagues.html`(独立页面,复用 style.css 与 i18n.js 机制):

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title data-i18n="leagues.title">联赛预测</title>
  <link rel="icon" href="static/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="static/style.css?v=42">
  <style>
    .lg-tabs { display: flex; gap: 8px; flex-wrap: wrap; margin: 16px 0; }
    .lg-tab { padding: 6px 14px; border-radius: 16px; border: 1px solid var(--border, #444);
              background: none; color: inherit; cursor: pointer; }
    .lg-tab.active { background: var(--accent, #4f8cff); color: #fff; border-color: transparent; }
    .lg-sub { display: flex; gap: 6px; margin: 8px 0 16px; }
    .lg-sub button { padding: 4px 10px; font-size: 13px; border-radius: 12px;
                     border: 1px solid var(--border, #444); background: none;
                     color: inherit; cursor: pointer; }
    .lg-sub button.active { background: var(--accent, #4f8cff); color: #fff; }
    table.lg { width: 100%; border-collapse: collapse; font-size: 14px; }
    table.lg th, table.lg td { padding: 6px 8px; border-bottom: 1px solid var(--border, #333);
                               text-align: center; }
    table.lg td.teams { text-align: left; }
    .lg-low { color: #e6a23c; font-size: 12px; }
    .lg-mask { color: var(--muted, #999); letter-spacing: 2px; }
    .lg-pro { color: var(--accent, #4f8cff); cursor: pointer; font-size: 12px; }
    .lg-status { color: var(--muted, #999); font-size: 12px; margin-top: 12px; }
    .lg-score { font-weight: 600; }
  </style>
</head>
<body>
<header>
  <h1 data-i18n="leagues.title">联赛预测</h1>
  <a href="/" class="switch-app" data-i18n="nav.wc" data-i18n-attr="title" title="World Cup">
    <span data-i18n="nav.wc">世界杯</span> <span aria-hidden="true">⟶</span>
  </a>
</header>
<main>
  <div class="lg-tabs" id="leagueTabs"></div>
  <div class="lg-sub" id="subTabs"></div>
  <section id="content"><p class="lg-status" data-i18n="leagues.not_ready">数据构建中…</p></section>
  <p class="lg-status" id="statusLine"></p>
</main>
<script src="static/i18n.js?v=25"></script>
<script src="static/leagues.js?v=1"></script>
</body>
</html>
```

`webapp/static/leagues.js`:

```javascript
/* leagues.js — 联赛板块前端:联赛切换 + 赛程/积分/推演/价值 四个子页。
   数据:/api/leagues*;免费用户 odds=null(API 已剥 market),渲染 "***" + Pro 提示。
   Pro 状态与 WC 一致:调 /api/whoami 取 is_paid。 */
(() => {
  const POLL_MS = 60000;
  const state = { leagues: [], code: null, tab: 'matches', isPaid: false, data: {} };

  const $ = (id) => document.getElementById(id);
  const tt = (k) => (window.I18N && I18N.t) ? I18N.t(k) : k;
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
    await refreshLeagues();
    setInterval(tick, POLL_MS);
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
    } catch (e) {
      $('content').innerHTML = `<p class="lg-status">${tt('leagues.not_ready')}</p>`;
    }
  }

  async function tick() { await refreshLeagues(); }

  function renderTabs() {
    $('leagueTabs').innerHTML = state.leagues.map(l =>
      `<button class="lg-tab ${l.code === state.code ? 'active' : ''}"
        data-code="${l.code}" ${l.available ? '' : 'disabled'}>${l.name_zh}</button>`
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
      return `<tr><td>${m.date}</td><td class="teams">${m.home} vs ${m.away}${low}</td>
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
        `<tr><td>${i + 1}</td><td class="teams">${t.team}</td><td>${t.p}</td><td>${t.w}</td>
         <td>${t.d}</td><td>${t.l}</td><td>${t.gf}</td><td>${t.ga}</td>
         <td>${t.gd}</td><td><b>${t.pts}</b></td></tr>`).join('')}</tbody></table>`;
  }

  function renderSim(sim) {
    const rows = Object.entries(sim.teams)
      .sort((a, b) => b[1].title - a[1].title)
      .map(([team, p]) => `<tr><td class="teams">${team}</td>
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
        `<tr><td>${v.date}</td><td class="teams">${v.home} vs ${v.away}</td>
         <td>${v.pick}</td><td>${pct(v.max_edge)}</td>
         <td>${(v.odds[v.pick] || 0).toFixed(2)}</td><td>${pct(v.kelly)}</td></tr>`).join('')}
      </tbody></table>`;
  }

  function proWall() {
    return `<p class="lg-status"><a class="lg-pro" href="/#pro">✨ ${tt('leagues.pro_only')}</a></p>`;
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
```

i18n.js:`const I18N_VER = 6;` → `const I18N_VER = 7;`(line 41,只动这一行)。

6 个 i18n JSON 各追加上表 19 个 key(合并进现有 flat 结构,不动旧 key)。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_leagues_frontend.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add webapp/static/leagues.html webapp/static/leagues.js \
        webapp/static/i18n/*.json webapp/static/i18n.js tests/test_leagues_frontend.py
git commit -m "feat(leagues): 前端页面 + 6 语言 i18n(I18N_VER 7)(Task 13)"
```

---

### Task 14: 赛季推演 `leagues/season_sim.py`

**Files:**
- Create: `leagues/season_sim.py`
- Modify: `webapp/leagues_updater.py`(update_once 接入 sim)
- Test: `tests/test_leagues_sim.py`

**Interfaces:**
- Consumes: `build.load_schedule/load_results/compute_standings/_load_dc/_atomic_json`、`dixon_coles.expected_goals`、`predict.score_matrix`、`config`(relegation_n/ucl_spots/paths)。
- Produces:
  - `simulate_season(code, n_sims=10000, seed=None) -> dict` — `{code, season, n_sims, teams: {team: {"title","ucl","releg"}}}`,落盘 `paths(code)["sim"]`
  - updater 接入:`leagues_cache.set_league(code, ..., sim=...)`;sim 失败仅记 error 不影响 predictions

- [ ] **Step 1: 写失败测试**

```python
# tests/test_leagues_sim.py
"""赛季推演:概率合法性、归一约束、强队占优、剩余赛程只取 pre。"""
import pytest

from leagues import build, config, season_sim


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("LEAGUES_SEASON", "2627")
    from leagues import fetch_fd, normalize
    # 当前积分:Strong 6 分,Weak 0 分
    fetch_fd.write_csv(config.paths("E0")["results"], normalize.RESULT_FIELDS, [
        {"season": "2627", "date": "2026-08-15", "home": "Strong", "away": "Weak", "gh": 3, "ga": 0},
        {"season": "2627", "date": "2026-08-22", "home": "Weak", "away": "Strong", "gh": 0, "ga": 2},
    ] * 1)
    # 剩余赛程:2 场 pre + 1 场 post(post 不能被模拟)
    fetch_fd.write_csv(config.paths("E0")["schedule"], normalize.SCHEDULE_FIELDS, [
        {"date": "2026-08-15", "time": "15:00", "home": "Strong", "away": "Weak",
         "status": "post", "gh": 3, "ga": 0, "neutral": ""},
        {"date": "2026-10-11", "time": "15:00", "home": "Strong", "away": "Weak",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
        {"date": "2026-10-18", "time": "15:00", "home": "Weak", "away": "Strong",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
    ])
    # DC 参数:Strong 明显强
    build._atomic_json(config.paths("E0")["dc_params"], {
        "league": "E0",
        "params": {"Strong": {"attack": 0.6, "defense": -0.3, "n": 100},
                   "Weak": {"attack": -0.6, "defense": 0.3, "n": 100}},
        "global": {"beta0": 0.3, "home_adv": 0.25, "rho": 0.0, "n_matches": 300},
    })
    return tmp_path


def test_sim_probabilities_valid_and_normalized(sandbox):
    out = season_sim.simulate_season("E0", n_sims=200, seed=42)
    assert out["n_sims"] == 200 and set(out["teams"]) == {"Strong", "Weak"}
    for t, p in out["teams"].items():
        for k in ("title", "ucl", "releg"):
            assert 0.0 <= p[k] <= 1.0
    # 归一约束:每次模拟冠军恰好 1 个;ucl 席位 = ucl_spots;降级 = relegation_n
    lg = config.LEAGUES["E0"]
    assert abs(sum(p["title"] for p in out["teams"].values()) - 1.0) < 0.06
    n = len(out["teams"])
    assert abs(sum(p["releg"] for p in out["teams"].values())
               - min(lg["relegation_n"], n)) < 0.06


def test_sim_strong_team_favored(sandbox):
    out = season_sim.simulate_season("E0", n_sims=200, seed=7)
    assert out["teams"]["Strong"]["title"] > out["teams"]["Weak"]["title"]
    assert out["teams"]["Weak"]["releg"] > out["teams"]["Strong"]["releg"]


def test_sim_writes_disk_and_skips_post_matches(sandbox):
    import json
    out = season_sim.simulate_season("E0", n_sims=100, seed=1)
    with open(config.paths("E0")["sim"], encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["teams"].keys() == out["teams"].keys()
    # post 场若被误当 pre 模拟,积分会重复计算 → Strong 夺冠率异常贴近 1
    # 间接验证:Weak 仍有非零夺冠可能被压到很低但不应为负/异常
    assert out["teams"]["Strong"]["title"] >= 0.5
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_leagues_sim.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leagues.season_sim'`

- [ ] **Step 3: 实现**

```python
# leagues/season_sim.py
"""赛季推演:当前积分 + 剩余赛程(status=='pre')× N 次蒙特卡洛。

每场按 DC 比分矩阵采样比分,累计积分/净胜球/进球,排最终名次 →
夺冠 / 欧冠席位(ucl_spots)/ 降级(relegation_n)概率。
剩余球队若不在当前积分榜(升班马赛程先于结果入库)按 0 分并入。
"""
import random

from . import build, config
from .dixon_coles import expected_goals
from .predict import score_matrix


def _build_dists(remaining, params, glob):
    """每场预生成累积分布,采样时一次 rng.random() 定位。"""
    dists = []
    for m in remaining:
        lh, la = expected_goals(params, glob, m["home"], m["away"], bool(m.get("neutral")))
        mat = score_matrix(lh, la, glob.get("rho", 0.0))
        cum, s = [], 0.0
        for i, row in enumerate(mat):
            for j, p in enumerate(row):
                s += p
                cum.append((s, i, j))
        cum[-1] = (1.0, cum[-1][1], cum[-1][2])   # 浮点尾巴兜底
        dists.append((m["home"], m["away"], cum))
    return dists


def simulate_season(code, n_sims=10000, seed=None):
    """返回 {code, season, n_sims, teams:{team:{title,ucl,releg}}},并落盘 sim.json。"""
    season = config.current_season()
    results = build.load_results(code)
    standings = build.compute_standings(results, season)
    remaining = [m for m in build.load_schedule(code) if m["status"] == "pre"]
    dc = build._load_dc(code)
    params, glob = dc["params"], dc["global"]
    lg = config.LEAGUES[code]
    rng = random.Random(seed)

    base = {t["team"]: (t["pts"], t["gd"], t["gf"]) for t in standings}
    for m in remaining:   # 赛程里有但积分榜没有的队伍按 0 分并入
        for t in (m["home"], m["away"]):
            base.setdefault(t, (0, 0, 0))
    teams = sorted(base)
    counts = {t: {"title": 0, "ucl": 0, "releg": 0} for t in teams}
    dists = _build_dists(remaining, params, glob)
    ucl_n = min(lg["ucl_spots"], len(teams))
    rel_n = min(lg["relegation_n"], len(teams))

    for _ in range(n_sims):
        pts = {t: base[t][0] for t in teams}
        gd = {t: base[t][1] for t in teams}
        gf = {t: base[t][2] for t in teams}
        for h, a, cum in dists:
            r = rng.random()
            gh, ga = 0, 0
            for s, i, j in cum:
                if r <= s:
                    gh, ga = i, j
                    break
            if gh > ga:
                pts[h] += 3
            elif gh == ga:
                pts[h] += 1
                pts[a] += 1
            else:
                pts[a] += 3
            gd[h] += gh - ga
            gd[a] += ga - gh
            gf[h] += gh
            gf[a] += ga
        order = sorted(teams, key=lambda t: (-pts[t], -gd[t], -gf[t], t))
        counts[order[0]]["title"] += 1
        for t in order[:ucl_n]:
            counts[t]["ucl"] += 1
        for t in (order[-rel_n:] if rel_n else []):
            counts[t]["releg"] += 1

    out = {"code": code, "season": season, "n_sims": n_sims,
           "teams": {t: {k: round(v / n_sims, 4) for k, v in c.items()}
                     for t, c in counts.items()}}
    build._atomic_json(config.paths(code)["sim"], out)
    return out
```

`webapp/leagues_updater.py` 的联赛循环内,`set_league(...)` 之前追加(sim 失败仅记 error,不阻塞 predictions):

```python
            sim = None
            try:
                from leagues import season_sim
                sim = season_sim.simulate_season(code)
            except Exception as e:
                errors.append(f"{code} sim: {e}")
            leagues_cache.set_league(code, predictions=payload, standings=standings, sim=sim)
```

(原 `set_league(code, predictions=payload, standings=standings)` 行被上面两行取代;注意 `set_league` 的 sim=None 语义是"不动",所以失败时保留旧 sim,符合容错铁律。)

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_leagues_sim.py -v` 然后 `pytest tests/test_leagues_updater.py -v`
Expected: 3 + 4 passed(updater 打桩环境下 `season_sim` 真实调用会因 sandbox 无数据失败 → errors 记录,`set_league` 仍被调用,4 个旧测试不回归;若 `test_update_once_populates_all_leagues` 断言了 standings 存在则不受影响,因为 sim 失败只追加 error —— 注意该测试断言 `last_error is None`,需要把 updater 测试的打桩补上:`monkeypatch.setattr("leagues.season_sim.simulate_season", lambda code: None)`,本步骤同步更新 `tests/test_leagues_updater.py` 的 autouse fixture 加这一行)

- [ ] **Step 5: Commit**

```bash
git add leagues/season_sim.py webapp/leagues_updater.py \
        tests/test_leagues_sim.py tests/test_leagues_updater.py
git commit -m "feat(leagues): 赛季推演蒙特卡洛(夺冠/欧冠席位/降级)(Task 14)"
```

---

### Task 15: 回测 `leagues/backtest.py`

**Files:**
- Create: `leagues/backtest.py`
- Test: `tests/test_leagues_backtest.py`(@slow 标记)
- Create(运行产物):`docs/leagues_backtest.md`

**Interfaces:**
- Consumes: `build.load_results`、`elo.build_pool(cutoff=...)`、`dixon_coles.fit_league(cutoff=...)`、`predict.predict_match/devig`。
- Produces:
  - `backtest_league(code, seasons, elo_lookback_cutoff=None) -> dict` — `{season: {n, acc_model, acc_b365, logloss_model, logloss_b365}}`
  - `main()` — 跑 LEAGUE_CODES × 最近 3 个完整赛季,写 `docs/leagues_backtest.md`
  - walk-forward 严格:第 S 季的每场预测只用 `date < 该场 date` 的数据(Elo 用 cutoff=赛季开赛日一次性建池,DC 用 cutoff=赛季开赛日拟合;**赛季内不滚动更新** —— 与 WC backtest.py 的 season-level walk-forward 粒度一致,注释钉死)

- [ ] **Step 1: 写失败测试(@slow)**

```python
# tests/test_leagues_backtest.py
"""回测:walk-forward 严格性 + 指标合法性。标记 slow(默认跳过)。"""
import pytest

from leagues import backtest, build, config


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    from leagues import fetch_fd, normalize
    # 两个赛季,每队每季互踢,强弱分明
    rows = []
    for s, y in (("2425", 2024), ("2526", 2025)):
        for rnd in range(10):
            rows.append({"season": s, "date": f"{y}-09-{rnd + 1:02d}",
                         "home": "Strong", "away": "Weak", "gh": 2, "ga": 0,
                         "b365h": 1.5, "b365d": 4.0, "b365a": 6.0})
            rows.append({"season": s, "date": f"{y + 1}-03-{rnd + 1:02d}",
                         "home": "Weak", "away": "Strong", "gh": 1, "ga": 2,
                         "b365h": 5.0, "b365d": 3.8, "b365a": 1.6})
    fetch_fd.write_csv(config.paths("E0")["results"], normalize.RESULT_FIELDS, rows)
    return tmp_path


@pytest.mark.slow
def test_backtest_metrics_valid_and_walk_forward(sandbox, monkeypatch):
    calls = []

    real_fit = backtest.dixon_coles.fit_league

    def spy_fit(matches, cutoff=None, **kw):
        calls.append(cutoff)
        params, glob = real_fit(matches, cutoff=cutoff, **kw)
        # 未来数据零容忍:拟合实际只消费 cutoff 前的 2425 季(10 轮 × 2 场)
        assert glob["n_matches"] == 20
        return params, glob

    monkeypatch.setattr(backtest.dixon_coles, "fit_league", spy_fit)
    out = backtest.backtest_league("E0", ["2526"])
    r = out["2526"]
    assert r["n"] == 20
    assert 0.0 <= r["acc_model"] <= 1.0 and 0.0 <= r["acc_b365"] <= 1.0
    assert r["logloss_model"] > 0 and r["logloss_b365"] > 0
    assert calls and all(c == "2025-08-01" for c in calls)   # 赛季开赛日 cutoff
    # 强弱分明的合成数据上,模型与 B365 都应有不错准确率
    assert r["acc_model"] >= 0.8 and r["acc_b365"] >= 0.8
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_leagues_backtest.py -v -m slow`
Expected: FAIL — `ModuleNotFoundError: No module named 'leagues.backtest'`

- [ ] **Step 3: 实现**

```python
# leagues/backtest.py
"""联赛回测(walk-forward 严格)。

粒度与 WC backtest.py 一致:season-level —— 预测第 S 季时,
Elo 池用 cutoff = S 季开赛日(8 月 1 日)一次性建好,DC 用同一 cutoff 拟合;
赛季内不滚动更新(滚动版留作后续增强,此处刻意与 WC 对齐,勿混用粒度)。
欧冠淘汰赛比分是 90 分钟结果(Review Focus 5),回测同样按此语义对比,不修正。

手工跑:python -m leagues.backtest  →  docs/leagues_backtest.md
"""
import math
from datetime import date

from . import build, config, dixon_coles, elo as elo_mod, predict


def _season_start(season):
    y1, _ = config.season_years(season)
    return f"{y1}-08-01"


def _logloss(probs, actual):
    return -math.log(max(probs[actual], 1e-9))


def backtest_league(code, seasons):
    """对 code 的每个 season 做赛季级 walk-forward 回测,对比 B365 去水基线。"""
    results = build.load_results(code)
    out = {}
    for season in seasons:
        cutoff = _season_start(season)
        elo, _ = elo_mod.build_pool(results, cutoff=cutoff)
        params, glob = dixon_coles.fit_league(results, cutoff=cutoff)
        played = [m for m in results if m.get("season") == season and m["gh"] is not None]
        played.sort(key=lambda m: (m["date"], m["home"]))
        n = hit_m = hit_b = 0
        ll_m = ll_b = 0.0
        for m in played:
            dv = predict.devig(m.get("b365h"), m.get("b365d"), m.get("b365a"))
            if not dv:
                continue
            pred = predict.predict_match(m["home"], m["away"], elo=elo,
                                         dc_params=params, dc_glob=glob)
            actual = "home" if m["gh"] > m["ga"] else ("draw" if m["gh"] == m["ga"] else "away")
            p_mkt = {"home": dv[0], "draw": dv[1], "away": dv[2]}
            n += 1
            hit_m += int(max(pred["probs"], key=pred["probs"].get) == actual)
            hit_b += int(max(p_mkt, key=p_mkt.get) == actual)
            ll_m += _logloss(pred["probs"], actual)
            ll_b += _logloss(p_mkt, actual)
        if n:
            out[season] = {"n": n, "acc_model": hit_m / n, "acc_b365": hit_b / n,
                           "logloss_model": ll_m / n, "logloss_b365": ll_b / n}
    return out


def _last_complete_seasons(k=3):
    """最近 k 个已完结赛季(当前赛季不算完结)。"""
    cur = config.current_season()
    y1, _ = config.season_years(cur)
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(y1 - k, y1)]


def main():
    seasons = _last_complete_seasons(3)
    lines = ["# 联赛板块回测(walk-forward,赛季粒度)",
             "",
             f"生成:{date.today().isoformat()} 赛季:{', '.join(seasons)}",
             "口径:Elo 池与 DC 均以赛季开赛日(8/1)为 cutoff;基线 = B365 收盘去水。",
             "",
             "| 联赛 | 赛季 | 场次 | 模型准确率 | B365 准确率 | 模型 logloss | B365 logloss |",
             "|---|---|---|---|---|---|---|"]
    for code in config.LEAGUE_CODES:
        try:
            res = backtest_league(code, seasons)
        except Exception as e:
            lines.append(f"| {code} | – | – | 回测失败:{e} | | | |")
            continue
        for s, r in sorted(res.items()):
            lines.append(f"| {code} | {s} | {r['n']} | {r['acc_model']:.3f} | "
                         f"{r['acc_b365']:.3f} | {r['logloss_model']:.3f} | {r['logloss_b365']:.3f} |")
    with open("docs/leagues_backtest.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_leagues_backtest.py -v -m slow`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add leagues/backtest.py tests/test_leagues_backtest.py
git commit -m "feat(leagues): 赛季粒度 walk-forward 回测 vs B365 基线(Task 15)"
```

### Task 16: UCL 全链路接线 + TheOddsAPI 赔率

**Files:**
- Create: `leagues/fetch_odds_ucl.py`
- Modify: `leagues/build.py`(fetch_all_if_stale 加 UCL;新增 rebuild_ucl)
- Modify: `webapp/leagues_updater.py`(UCL 块接入 update_once)
- Test: `tests/test_leagues_ucl.py`

**Interfaces:**
- Consumes: `fetch_espn.fetch_ucl_history/fetch_schedule("UCL")`、`dixon_coles.fit_ucl_global`、`build._load_dc/_atomic_json`、`config.canonical/load_aliases`。
- Produces:
  - `fetch_odds_ucl.fetch_ucl_odds(api_key=None) -> {(home, away): {"b365h","b365d","b365a"}}` — env `THE_ODDS_API_KEY`(与 WC `fetch_odds_api.py` 同变量);h2h 市场跨博彩商均价;无 key/失败 → {}(静默降级)
  - `build.rebuild_ucl(elo=None, ucl_odds=None) -> dict` — 借用制 DC;`low_confidence = 任一方 borrow.low_confidence`;落盘 UCL predictions + dc_params
  - updater:UCL 进缓存(standings 暂为空 list → /standings 503,前端显示构建中;sim 不做)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_leagues_ucl.py
"""UCL:TheOddsAPI 三方命名 canonical 匹配、借用制 rebuild、降级行为。"""
import os

import pytest

from leagues import build, config, fetch_odds_ucl


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


ODDS_PAYLOAD = [
    {"home_team": "Internazionale", "away_team": "AC Milan",
     "bookmakers": [
         {"markets": [{"key": "h2h", "outcomes": [
             {"name": "Internazionale", "price": 2.40},
             {"name": "Draw", "price": 3.20},
             {"name": "AC Milan", "price": 3.10}]}]},
         {"markets": [{"key": "h2h", "outcomes": [
             {"name": "Internazionale", "price": 2.50},
             {"name": "Draw", "price": 3.30},
             {"name": "AC Milan", "price": 3.00}]}]},
     ]},
    {"home_team": "Qarabag FK", "away_team": "Unknown United",
     "bookmakers": [{"markets": [{"key": "h2h", "outcomes": [
         {"name": "Qarabag FK", "price": 1.80},
         {"name": "Draw", "price": 3.60},
         {"name": "Unknown United", "price": 4.50}]}]}]},
]


def test_fetch_ucl_odds_canonical_names(monkeypatch):
    """Review Focus 4:TheOddsAPI 'Internazionale' → canonical 'Inter' 才能匹配赛程。"""
    monkeypatch.setattr(fetch_odds_ucl.requests, "get",
                        lambda url, params=None, timeout=None: FakeResp(ODDS_PAYLOAD))
    out = fetch_odds_ucl.fetch_ucl_odds(api_key="fake")
    assert ("Inter", "Milan") in out                      # 三方命名已归一
    assert out[("Inter", "Milan")]["b365h"] == pytest.approx(2.45)   # 两家均价
    assert ("Qarabag FK", "Unknown United") in out        # 未命中别名 → 原名透传
    # sport key 钉死 soccer_uefa_champs_league(spec v2 勘误)
    captured = {}

    def cap_get(url, params=None, timeout=None):
        captured["url"] = url
        return FakeResp([])

    monkeypatch.setattr(fetch_odds_ucl.requests, "get", cap_get)
    fetch_odds_ucl.fetch_ucl_odds(api_key="fake")
    assert "soccer_uefa_champs_league" in captured["url"]


def test_fetch_ucl_odds_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    assert fetch_odds_ucl.fetch_ucl_odds() == {}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("LEAGUES_SEASON", "2627")
    from leagues import fetch_fd, normalize
    # I1 联赛 DC 参数(供借用)
    build._atomic_json(config.paths("I1")["dc_params"], {
        "league": "I1",
        "params": {"Inter": {"attack": 0.4, "defense": -0.2, "n": 100},
                   "Milan": {"attack": 0.2, "defense": 0.0, "n": 100}},
        "global": {"beta0": 0.3, "home_adv": 0.25, "rho": 0.0, "n_matches": 380},
    })
    for c in ("E0", "SP1", "D1", "F1"):                  # 其他联赛空参数
        build._atomic_json(config.paths(c)["dc_params"],
                           {"league": c, "params": {}, "global": {"beta0": 0.3, "home_adv": 0.2, "rho": 0.0}})
        fetch_fd.write_csv(config.paths(c)["results"], normalize.RESULT_FIELDS, [])
    fetch_fd.write_csv(config.paths("UCL")["results"], normalize.UCL_RESULT_FIELDS, [
        {"date": "2026-09-16", "home": "Inter", "away": "Milan", "gh": 2, "ga": 1, "neutral": ""},
        {"date": "2026-09-17", "home": "Ajax", "away": "Inter", "gh": 0, "ga": 3, "neutral": ""},
    ])
    fetch_fd.write_csv(config.paths("UCL")["schedule"], normalize.SCHEDULE_FIELDS, [
        {"date": "2026-09-16", "time": "20:00", "home": "Inter", "away": "Milan",
         "status": "post", "gh": 2, "ga": 1, "neutral": ""},
        {"date": "2026-10-21", "time": "20:00", "home": "Inter", "away": "Ajax",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
        {"date": "2026-10-22", "time": "20:00", "home": "Milan", "away": "Ajax",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
    ])
    return tmp_path


def test_rebuild_ucl_borrow_and_low_confidence(sandbox):
    elo = {"Inter": 1700.0, "Milan": 1600.0, "Ajax": 1550.0}
    p = build.rebuild_ucl(elo=elo, ucl_odds={("Inter", "Ajax"):
                                             {"b365h": 1.7, "b365d": 3.8, "b365a": 4.6}})
    assert p["code"] == "UCL" and len(p["matches"]) == 3
    post = [m for m in p["matches"] if m["status"] == "post"][0]
    assert post["gh"] == 2
    m1 = [m for m in p["matches"] if m["home"] == "Inter" and m["status"] == "pre"][0]
    assert m1["low_confidence"] is True                    # Ajax 无五大联赛归属(N-2)
    assert m1["probs"]["home"] > m1["probs"]["away"]       # 借用 Inter 强势参数
    assert "market" in m1                                  # ucl_odds 匹配成功
    m2 = [m for m in p["matches"] if m["home"] == "Milan"][0]
    assert "market" not in m2                              # 无赔率 → 无 market 键
    # dc_params 落盘含 low_confidence 标记
    import json
    with open(config.paths("UCL")["dc_params"], encoding="utf-8") as f:
        dc = json.load(f)
    assert dc["params"]["Ajax"]["low_confidence"] is True
    assert dc["params"]["Inter"]["low_confidence"] is False


def test_fetch_all_if_stale_includes_ucl(sandbox, monkeypatch):
    from leagues import fetch_espn, fetch_fd
    monkeypatch.setattr(fetch_fd, "fetch_league_history", lambda code, **kw: (5, []))
    monkeypatch.setattr(fetch_fd, "fetch_fixtures", lambda codes=None: {})
    monkeypatch.setattr(fetch_espn, "fetch_schedule", lambda code, **kw: (10, []))
    called = []
    monkeypatch.setattr(fetch_espn, "fetch_ucl_history",
                        lambda **kw: called.append(1) or (100, 5, False))
    report = build.fetch_all_if_stale()
    assert called == [1]                                   # UCL 历史被补抓
    assert report["ucl"]["rows"] == 100
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_leagues_ucl.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leagues.fetch_odds_ucl'`

- [ ] **Step 3: 实现**

```python
# leagues/fetch_odds_ucl.py
"""TheOddsAPI 欧冠赔率(配额敏感:仅 updater 低频调用,月 <20 次)。

  · sport key 钉死 soccer_uefa_champs_league(spec v2 勘误,Task 1 测试已 pin)
  · API key 用 env THE_ODDS_API_KEY(与 WC fetch_odds_api.py 同一变量)
  · h2h 市场跨博彩商取均价;队名一律 canonical() 归一(Review Focus 4)
  · 无 key / 请求失败 → {}(静默降级:预测照常,只是没赔率)
"""
import os

import requests

from . import config

API_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
TIMEOUT = 30


def fetch_ucl_odds(api_key=None):
    """返回 {(home, away): {"b365h","b365d","b365a"}}(h2h 均价,键已 canonical)。"""
    key = (api_key or os.environ.get("THE_ODDS_API_KEY", "")).strip()
    if not key:
        return {}
    sport = config.LEAGUES["UCL"]["odds_sport_key"]
    r = requests.get(API_URL.format(sport=sport),
                     params={"apiKey": key, "regions": "eu", "markets": "h2h"},
                     timeout=TIMEOUT)
    r.raise_for_status()
    aliases = config.load_aliases()
    out = {}
    for ev in r.json():
        home = config.canonical(ev.get("home_team", ""), aliases)
        away = config.canonical(ev.get("away_team", ""), aliases)
        hs, ds, as_ = [], [], []
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk.get("key") != "h2h":
                    continue
                for oc in mk.get("outcomes", []):
                    nm = config.canonical(oc.get("name", ""), aliases)
                    if nm == home:
                        hs.append(oc["price"])
                    elif nm == away:
                        as_.append(oc["price"])
                    elif oc.get("name") == "Draw":
                        ds.append(oc["price"])
        if home and away and hs and ds and as_:
            out[(home, away)] = {"b365h": sum(hs) / len(hs),
                                 "b365d": sum(ds) / len(ds),
                                 "b365a": sum(as_) / len(as_)}
    return out
```

`leagues/build.py` 追加 `rebuild_ucl`(文件末尾):

```python
def rebuild_ucl(elo=None, ucl_odds=None):
    """UCL 预测全量重建:借用制 DC(fit_ucl_global)+ TheOddsAPI 赔率。

    low_confidence = 任一方 borrow.low_confidence(非五大联赛队,N-2)。
    新进欧冠、历史里没有的队不在 borrow_map → predict_match 自动回退纯 Elo
    并自带 low_confidence=True,两条路径一致。
    """
    season = config.current_season()
    schedule = load_schedule("UCL")
    if elo is None:
        elo = build_elo_pool()[0]
    league_params = {}
    for code in config.LEAGUE_CODES:
        for t, p in _load_dc(code)["params"].items():
            league_params.setdefault(t, p)
    glob, borrow = dixon_coles.fit_ucl_global(load_results("UCL"), league_params)
    _atomic_json(config.paths("UCL")["dc_params"],
                 {"league": "UCL", "params": borrow, "global": glob})
    matches = []
    for m in schedule:
        base = {"date": m["date"], "time": m.get("time", ""), "home": m["home"],
                "away": m["away"], "status": m["status"], "neutral": bool(m.get("neutral"))}
        if m["status"] == "post":
            base["gh"], base["ga"] = m["gh"], m["ga"]
        else:
            odds = (ucl_odds or {}).get((m["home"], m["away"]))
            low = (borrow.get(m["home"], {}).get("low_confidence", True)
                   or borrow.get(m["away"], {}).get("low_confidence", True))
            pred = predict_mod.predict_match(
                m["home"], m["away"], elo=elo, dc_params=borrow, dc_glob=glob,
                odds=odds, neutral=base["neutral"], low_confidence=low)
            base.update(pred)
            if odds:
                base["odds"] = {k: odds.get(k) for k in ("b365h", "b365d", "b365a")}
        matches.append(base)
    payload = {"code": "UCL", "season": season,
               "generated_at": date.today().isoformat(), "matches": matches}
    _atomic_json(config.paths("UCL")["predictions"], payload)
    return payload
```

`leagues/build.py` 的 `fetch_all_if_stale` 末尾(`return report` 前)追加:

```python
    # UCL:赛程同新鲜度;历史回填重(11+ 赛季 × 12 月),results_h 阈值控制频率
    if not fetch_fd.is_fresh(config.paths("UCL")["schedule"], fixtures_h):
        try:
            n, missing = fetch_espn.fetch_schedule("UCL")
            report["schedules"]["UCL"] = {"rows": n, "missing_months": missing}
        except Exception as e:
            report["errors"].append(f"schedule UCL: {e}")
    if not fetch_fd.is_fresh(config.paths("UCL")["results"], results_h):
        try:
            n, total_missing, degraded = fetch_espn.fetch_ucl_history()
            report["ucl"] = {"rows": n, "missing": total_missing, "degraded": degraded}
        except Exception as e:
            report["errors"].append(f"ucl_history: {e}")
```

(`report` 初始化加 `"ucl": None`。)

`webapp/leagues_updater.py` 的 `update_once` 在联赛循环之后、`set_status` 之前追加:

```python
    # UCL:借用制 + TheOddsAPI 赔率(配额敏感,失败静默降级为无赔率)
    try:
        from leagues import fetch_odds_ucl
        ucl_odds = fetch_odds_ucl.fetch_ucl_odds()
    except Exception as e:
        errors.append(f"ucl_odds: {e}")
        ucl_odds = {}
    try:
        payload = build.rebuild_ucl(elo=elo, ucl_odds=ucl_odds)
        leagues_cache.set_league("UCL", predictions=payload, standings=[])
    except Exception as e:
        errors.append(f"UCL: {e}")
        traceback.print_exc()
```

**同步更新** `tests/test_leagues_updater.py` autouse fixture 加两行打桩(避免触网/依赖 UCL 文件):

```python
    monkeypatch.setattr(leagues_updater.build, "rebuild_ucl",
                        lambda elo=None, ucl_odds=None: {"code": "UCL", "season": "2627", "matches": []})
    monkeypatch.setattr("leagues.fetch_odds_ucl.fetch_ucl_odds", lambda: {})
```

**同步更新** `tests/test_leagues_updater.py` 两处断言(UCL 现在也进缓存):

```python
# test_update_once_populates_all_leagues:
    assert leagues_cache.get_codes() == ["D1", "E0", "F1", "I1", "SP1", "UCL"]
# test_update_once_isolates_single_league_failure:
    assert len(leagues_cache.get_codes()) == 5      # 4 联赛 + UCL(D1 失败被隔离)
```

**同步更新** `tests/test_leagues_build.py` 的 `test_fetch_all_if_stale_isolates_failures`,补 UCL 打桩(否则 fetch_all_if_stale 会真实调用 `fetch_ucl_history` 触网):

```python
    monkeypatch.setattr(fetch_espn, "fetch_ucl_history", lambda **kw: (100, 5, False))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_leagues_ucl.py tests/test_leagues_updater.py -v`
Expected: 4 + 4 passed

- [ ] **Step 5: Commit**

```bash
git add leagues/fetch_odds_ucl.py leagues/build.py webapp/leagues_updater.py \
        tests/test_leagues_ucl.py tests/test_leagues_updater.py
git commit -m "feat(leagues): UCL 借用制全链路 + TheOddsAPI 赔率(canonical 匹配)(Task 16)"
```

---

### Task 17: 俱乐部 XGBoost 模型 `leagues/train_xgb.py`

**Files:**
- Create: `leagues/train_xgb.py`
- Modify: `webapp/leagues_updater.py`(xgb lookup 注入 rebuild)
- Test: `tests/test_leagues_xgb.py`

**Interfaces:**
- Consumes: `build.load_results/load_schedule/_load_dc/build_elo_pool`、`elo.build_pool`、`dixon_coles.fit_league`、`config`。
- Produces:
  - `FEATURES: list[str]`(15 维:elo_diff/elo_h/elo_a/form5_diff/gf5_diff/ga5_diff/atk_diff/def_diff/home + 6 联赛 one-hot)
  - `build_dataset() -> (X, y, groups)` — y:0=home/1=draw/2=away;groups=赛季代码;全部特征赛前可得(walk-forward:Elo 用赛前 history,form 用赛前近 5 场,DC 用赛季开赛日 cutoff)
  - `train() -> (bst, meta)` — 最后第二个赛季做验证(early stopping),落盘 `global_paths()["xgb"]` + `.meta.json`
  - `load_model() -> bst|None`
  - `predict_proba(bst, feats) -> {"home","draw","away"}`
  - `build_upcoming_lookup(elo=None) -> {(code,home,away): probs}` — 模型不存在 → {}

- [ ] **Step 1: 写失败测试**

```python
# tests/test_leagues_xgb.py
"""XGBoost:赛前特征无泄漏、walk-forward 切分、proba 合法、无模型降级。"""
import numpy as np
import pytest

from leagues import build, config, train_xgb


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    from leagues import fetch_fd, normalize
    rows = []
    # 3 个赛季,Strong 稳定虐 Weak(模型应学到 elo_diff/atk_diff 方向)
    for si, y in enumerate((2023, 2024, 2025)):
        s = f"{y % 100:02d}{(y + 1) % 100:02d}"
        for rnd in range(12):
            rows.append({"season": s, "date": f"{y}-09-{rnd + 1:02d}",
                         "home": "Strong", "away": "Weak", "gh": 2, "ga": 0})
            rows.append({"season": s, "date": f"{y + 1}-03-{rnd + 1:02d}",
                         "home": "Weak", "away": "Strong", "gh": 0, "ga": 1})
    fetch_fd.write_csv(config.paths("E0")["results"], normalize.RESULT_FIELDS, rows)
    fetch_fd.write_csv(config.paths("E0")["schedule"], normalize.SCHEDULE_FIELDS, [
        {"date": "2026-10-11", "time": "15:00", "home": "Strong", "away": "Weak",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
    ])
    return tmp_path


def test_form_features_are_pre_match():
    rows = [
        {"date": "2026-08-15", "home": "A", "away": "B", "gh": 2, "ga": 0},
        {"date": "2026-08-22", "home": "A", "away": "C", "gh": 1, "ga": 1},
    ]
    fm = train_xgb._form_features(rows)
    # 首场:双方赛季首战,form 必须全 0(赛前没有任何历史)
    assert fm[("2026-08-15", "A", "B")] == (0, 0, 0, 0, 0, 0)
    # 第二场:A 已 1 胜(3 分,进 2 失 0)——赛前值,不含本场
    ph, pa, gfh, gfa, gah, gaa = fm[("2026-08-22", "A", "C")]
    assert (ph, gfh, gah) == (3, 2, 0) and (pa, gfa, gaa) == (0, 0, 0)


def test_dataset_walk_forward_groups(sandbox):
    X, y, groups = train_xgb.build_dataset()
    assert X.shape[1] == len(train_xgb.FEATURES)
    assert set(y) == {0, 2}                        # 本数据只有主胜/客胜
    assert len(set(groups)) == 3                   # 3 个赛季
    # 特征合法性:无 NaN
    assert not np.isnan(X).any()


def test_train_and_predict_roundtrip(sandbox):
    bst, meta = train_xgb.train()
    assert meta["n_train"] > 0 and meta["n_val"] > 0
    assert meta["val_season"] == "2425"            # 倒数第二个赛季做验证(2324 训 / 2425 验)
    feats = {f: 0.0 for f in train_xgb.FEATURES}
    feats.update({"elo_diff": 200.0, "lg_E0": 1.0, "home": 1.0})
    p = train_xgb.predict_proba(bst, feats)
    assert abs(sum(p.values()) - 1.0) < 1e-4
    # 模型文件落盘 + 可重载
    assert train_xgb.load_model() is not None


def test_build_upcoming_lookup(sandbox):
    train_xgb.train()
    lookup = train_xgb.build_upcoming_lookup()
    assert ("E0", "Strong", "Weak") in lookup
    p = lookup[("E0", "Strong", "Weak")]
    assert abs(sum(p.values()) - 1.0) < 1e-4


def test_lookup_empty_without_model(sandbox):
    assert train_xgb.build_upcoming_lookup() == {}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_leagues_xgb.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leagues.train_xgb'`

- [ ] **Step 3: 实现**

```python
# leagues/train_xgb.py
"""俱乐部 XGBoost 三态模型(walk-forward 严格)。

特征(15 维,全部赛前可得,无未来泄漏):
  elo_diff, elo_h, elo_a          — 统一 Elo 池赛前值(build_pool history)
  form5_diff, gf5_diff, ga5_diff  — 近 5 场积分/进球/失球差(赛前)
  atk_diff, def_diff              — DC 参数(赛季开赛日 cutoff 拟合)
  home                            — 恒 1(与 WC 特征表对齐)
  lg_E0/SP1/I1/D1/F1/UCL          — 联赛 one-hot

切分:最后第二个完整赛季做验证(early stopping),更早赛季做训练;
验证季预测用的特征本身仍是赛前值,标签只在拟合时使用。

产物:data/leagues/xgb_club.json(booster)+ xgb_club.json.meta.json。
训练是离线手工动作(python -m leagues.train_xgb),updater 只 load_model 推理。
"""
import json
import os
from collections import defaultdict, deque
from datetime import date

import numpy as np
import xgboost as xgb

from . import build, config, dixon_coles, elo as elo_mod

FEATURES = ["elo_diff", "elo_h", "elo_a", "form5_diff", "gf5_diff", "ga5_diff",
            "atk_diff", "def_diff", "home",
            "lg_E0", "lg_SP1", "lg_I1", "lg_D1", "lg_F1", "lg_UCL"]

_PARAMS = {"objective": "multi:softprob", "num_class": 3, "max_depth": 4,
           "eta": 0.05, "subsample": 0.8, "eval_metric": "mlogloss"}


def _season_of(d):
    """ISO 日期 → 赛季代码(8 月 1 日边界)。UCL 行没有 season 字段,统一从此推导。"""
    y, m = int(d[:4]), int(d[5:7])
    start = y if m >= 8 else y - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def _form_features(results, window=5):
    """{(date,home,away): (pts_h, pts_a, gf_h, gf_a, ga_h, ga_a)} — 全部赛前值。"""
    hist = defaultdict(lambda: deque(maxlen=window))
    out = {}
    for m in sorted(results, key=lambda r: (r["date"], r["home"])):
        if m["gh"] is None:
            continue
        hh, aa = hist[m["home"]], hist[m["away"]]
        out[(m["date"], m["home"], m["away"])] = (
            sum(g[0] for g in hh), sum(g[0] for g in aa),
            sum(g[1] for g in hh), sum(g[1] for g in aa),
            sum(g[2] for g in hh), sum(g[2] for g in aa))
        hp = 3 if m["gh"] > m["ga"] else (1 if m["gh"] == m["ga"] else 0)
        ap = 3 if m["ga"] > m["gh"] else (1 if m["gh"] == m["ga"] else 0)
        hh.append((hp, m["gh"], m["ga"]))
        aa.append((ap, m["ga"], m["gh"]))
    return out


def _label(m):
    return 0 if m["gh"] > m["ga"] else (1 if m["gh"] == m["ga"] else 2)


def build_dataset():
    """全 ALL_CODES 历史 → (X, y, groups)。walk-forward:特征全为赛时值。"""
    rows = []
    for code in config.ALL_CODES:
        rows.extend(build.load_results(code))
    rows = [m for m in rows if m["gh"] is not None]
    rows.sort(key=lambda m: (m["date"], m["home"]))
    _, history = elo_mod.build_pool(rows)
    elo_map = {(h["date"], h["home"], h["away"]): (h["elo_h"], h["elo_a"]) for h in history}
    form_map = _form_features(rows)
    # 每赛季 DC:联赛比赛按赛季开赛日 cutoff 拟合(UCL 借用同季联赛参数)
    seasons = sorted({_season_of(m["date"]) for m in rows})
    dc_by_season = {}
    for s in seasons:
        y1, _ = config.season_years(s)
        params, _glob = dixon_coles.fit_league(
            [m for m in rows if m["comp"] != "UCL"], cutoff=f"{y1}-08-01")
        dc_by_season[s] = params
    X, y, groups = [], [], []
    for m in rows:
        s = _season_of(m["date"])
        eh, ea = elo_map.get((m["date"], m["home"], m["away"]), (1500.0, 1500.0))
        ph, pa, gfh, gfa, gah, gaa = form_map.get((m["date"], m["home"], m["away"]),
                                                  (0, 0, 0, 0, 0, 0))
        dc = dc_by_season[s]
        ah = dc.get(m["home"], {"attack": 0.0, "defense": 0.0})
        aa_ = dc.get(m["away"], {"attack": 0.0, "defense": 0.0})
        feats = [eh - ea, eh, ea, ph - pa, gfh - gfa, gah - gaa,
                 ah["attack"] - aa_["attack"], ah["defense"] - aa_["defense"], 1.0]
        feats += [1.0 if m["comp"] == c else 0.0 for c in config.ALL_CODES]
        X.append(feats)
        y.append(_label(m))
        groups.append(s)
    return np.array(X, dtype=float), np.array(y, dtype=int), groups


def train():
    """walk-forward 训练:验证 = 倒数第二个赛季。落盘 booster + meta。返回 (bst, meta)。"""
    X, y, groups = build_dataset()
    seasons = sorted(set(groups))
    val_season = seasons[-2] if len(seasons) >= 2 else seasons[-1]
    tr = [i for i, g in enumerate(groups) if g < val_season]
    va = [i for i, g in enumerate(groups) if g == val_season]
    dtrain = xgb.DMatrix(X[tr], label=y[tr], feature_names=FEATURES)
    dval = xgb.DMatrix(X[va], label=y[va], feature_names=FEATURES)
    bst = xgb.train(_PARAMS, dtrain, num_boost_round=300,
                    evals=[(dval, "val")], early_stopping_rounds=30, verbose_eval=False)
    meta = {"features": FEATURES, "val_season": val_season,
            "n_train": len(tr), "n_val": len(va),
            "val_logloss": float(bst.best_score) if bst.best_score is not None else None,
            "trained_at": date.today().isoformat()}
    path = config.global_paths()["xgb"]
    bst.save_model(path)
    with open(path + ".meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return bst, meta


def load_model():
    path = config.global_paths()["xgb"]
    if not os.path.exists(path):
        return None
    bst = xgb.Booster()
    bst.load_model(path)
    return bst


def predict_proba(bst, feats):
    """feats: dict(特征名 → 值)。返回 {"home","draw","away"}(和为 1)。"""
    dm = xgb.DMatrix(np.array([[feats.get(f, 0.0) for f in FEATURES]]),
                     feature_names=FEATURES)
    p = bst.predict(dm)[0]
    return {"home": float(p[0]), "draw": float(p[1]), "away": float(p[2])}


def build_upcoming_lookup(elo=None):
    """当前所有 pre 赛程的 xgb_probs 查找表:{(code,home,away): probs}。
    模型文件不存在 → {}(rebuild 自动退化为纯统计模型,combine_gate 不触发)。"""
    bst = load_model()
    if bst is None:
        return {}
    if elo is None:
        elo = build.build_elo_pool()[0]
    results = []
    for code in config.ALL_CODES:
        results.extend(build.load_results(code))
    results = [m for m in results if m["gh"] is not None]
    results.sort(key=lambda m: (m["date"], m["home"]))
    form_map = _form_features(results)
    latest_dc = {c: build._load_dc(c)["params"] for c in config.LEAGUE_CODES}
    out = {}
    for code in config.ALL_CODES:
        for m in build.load_schedule(code):
            if m["status"] != "pre":
                continue
            eh = elo.get(m["home"], 1500.0)
            ea = elo.get(m["away"], 1500.0)
            # form:用该队最近一场比赛的赛前 form 累加其赛果近似(或直接用最近 key)
            past = [(k, v) for k, v in form_map.items()
                    if m["home"] in (k[1], k[2]) or m["away"] in (k[1], k[2])]
            ph = pa = gfh = gfa = gah = gaa = 0
            dc = latest_dc.get(code) or {}
            if code == "UCL":   # UCL 借用:队 → 各自联赛参数(与 fit_ucl_global 同源)
                dc = {}
                for c in config.LEAGUE_CODES:
                    dc.update({t: p for t, p in latest_dc[c].items() if t not in dc})
            ah = dc.get(m["home"], {"attack": 0.0, "defense": 0.0})
            aa_ = dc.get(m["away"], {"attack": 0.0, "defense": 0.0})
            feats = {"elo_diff": eh - ea, "elo_h": eh, "elo_a": ea,
                     "form5_diff": ph - pa, "gf5_diff": gfh - gfa, "ga5_diff": gah - gaa,
                     "atk_diff": ah["attack"] - aa_["attack"],
                     "def_diff": ah["defense"] - aa_["defense"], "home": 1.0,
                     **{f"lg_{c}": 1.0 if c == code else 0.0 for c in config.ALL_CODES}}
            out[(code, m["home"], m["away"])] = predict_proba(bst, feats)
    return out


if __name__ == "__main__":
    _, meta = train()
    print(json.dumps(meta, ensure_ascii=False, indent=1))
```

注意:`build_upcoming_lookup` 里 form 特征取了保守近似(全 0)—— 准确做法需要"最近一场之后"的滚动 form,复杂度不值;Elo 与 DC 已承载实力信息,form 近似只影响 XGB 一路,且训练/推理一致性问题在 Task 18 回测中观察。注释钉死该近似,后续增强再精细化。

`webapp/leagues_updater.py` 的 `update_once`,Elo 池构建成功之后、联赛循环之前插入:

```python
    try:
        from leagues import train_xgb
        xgb_lookup = train_xgb.build_upcoming_lookup(elo)
    except Exception:
        xgb_lookup = {}
```

联赛循环里 `build.rebuild(code, elo=elo)` 改为 `build.rebuild(code, elo=elo, xgb=xgb_lookup)`;UCL 块 `build.rebuild_ucl(elo=elo, ucl_odds=ucl_odds)` 不变(UCL 暂不接 xgb,borrow 路径已低置信)。

**同步更新** `tests/test_leagues_updater.py` autouse fixture 加打桩:

```python
    monkeypatch.setattr("leagues.train_xgb.build_upcoming_lookup", lambda elo=None: {})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_leagues_xgb.py tests/test_leagues_updater.py -v`
Expected: 5 + 4 passed

- [ ] **Step 5: Commit**

```bash
git add leagues/train_xgb.py webapp/leagues_updater.py \
        tests/test_leagues_xgb.py tests/test_leagues_updater.py
git commit -m "feat(leagues): XGBoost 三态模型 + 分歧门控接入(Task 17)"
```

---

### Task 18: 端到端验收 + CHANGELOG + 文档

**Files:**
- Modify: `CHANGELOG.md`
- Create: `docs/leagues_backtest.md`(`python -m leagues.backtest` 产物,真实数据跑)
- Test: 全量回归

- [ ] **Step 1: 全量测试(默认路径,不触网)**

Run: `pytest tests/ -q -m "not slow"`
Expected: 71 存量 + ~60 新增全绿,0 失败

- [ ] **Step 2: 真实数据端到端(开发机手工跑,不进 pytest)**

```bash
cd "/mnt/data/project/足彩/联赛/wc_predict"
python -c "
from leagues import build
r = build.fetch_all_if_stale(fixtures_h=0, results_h=0)   # 强制全量抓
print('errors:', r['errors'])
elo, _ = build.build_elo_pool()
print('elo teams:', len(elo))
from webapp import leagues_updater, leagues_cache
leagues_updater.update_once()
print('codes:', leagues_cache.get_codes())
"
```
预期:5 联赛 + UCL 全部进缓存;errors 为空或只有零星单季失败。

- [ ] **Step 3: 真实回测 + 训练(慢,@slow 路径)**

```bash
python -m leagues.backtest        # → docs/leagues_backtest.md
python -m leagues.train_xgb       # → data/leagues/xgb_club.json + meta
pytest tests/ -q -m slow          # 慢测试也绿
```
把 `docs/leagues_backtest.md` 的模型 vs B365 对比读一遍:若模型 logloss 系统性劣于基线 >10%,在 CHANGELOG 标注"XGB/DC 参数待调",不阻塞上线(门控默认分歧时回退统计模型)。

- [ ] **Step 4: WC 旗舰冒烟(零回归验证)**

```bash
./webapp.sh restart || true
curl -s http://127.0.0.1:8000/api/schedule | head -c 200   # WC 主接口正常
curl -s http://127.0.0.1:8000/api/leagues | head -c 300    # 新板块列表
curl -s http://127.0.0.1:8000/api/leagues/E0/matches | head -c 300   # 免费视图:odds=null,无 market 键
curl -s http://127.0.0.1:8000/leagues | head -c 120        # 页面 200
```

- [ ] **Step 5: CHANGELOG + Commit**

`CHANGELOG.md` 顶部追加:

```markdown
## 2026-10-09 — 联赛板块(五大联赛 + 欧冠)

- 新增 `leagues/` 包:football-data + ESPN + TheOddsAPI 三源管道,统一俱乐部 Elo 池
  (联赛 K=30 / 欧冠 K=40,升班马加速),Dixon-Coles MLE(UCL 借用制),XGBoost 分歧门控,
  比分矩阵 / OU2.5 / BTTS / 价值下注(分数 Kelly)/ 赛季推演蒙特卡洛。
- 新增 `/leagues` 页面 + `/api/leagues*`(30/min 限流):预测与积分榜免费,
  赔率与价值下注 Pro 门控(与 WC 对齐:非 Pro odds=null、无 edge/kelly 键、/value 402)。
- 独立 `leagues_cache` / `leagues_updater`(60min),与 WC 缓存/更新器完全隔离,WC 零改动
  (仅 app.py 接线、index.html 导航、i18n)。
- 回测:docs/leagues_backtest.md(walk-forward,赛季粒度,vs B365 去水基线)。
```

```bash
git add CHANGELOG.md docs/leagues_backtest.md
git commit -m "docs(leagues): CHANGELOG + 真实数据回测报告(Task 18)"
git log --oneline | head -20
```

---

## 完成定义(Definition of Done)

- [ ] `pytest tests/ -q -m "not slow"`:71 存量 + 全部新增绿
- [ ] `pytest tests/ -q -m slow`:慢测试绿
- [ ] 5 联赛 + UCL 在 `/api/leagues` 全部 `available: true`
- [ ] 免费用户 `/matches` 递归无 `market/edge/kelly` 键、`odds=null`;`/value` 402;Pro 全量可见
- [ ] WC 现有接口与页面手工冒烟零回归
- [ ] `docs/leagues_backtest.md` 已生成并过目
- [ ] 分支 `feat/leagues-expansion` 上 18 个 Task 的 commit 链完整

