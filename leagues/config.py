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
    """'2627' → (2026, 2027);'9900' → (1999, 2000)(≥50 视为 19xx)"""
    y1 = int(season[:2])
    start = 1900 + y1 if y1 >= 50 else 2000 + y1
    return start, start + 1


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
