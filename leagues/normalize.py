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

RESULT_FIELDS = ["season", "date", "home", "away", "gh", "ga", "neutral",
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
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
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
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
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
