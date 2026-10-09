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


def _get(url, retries=RETRIES):
    """带重试的 GET,返回去 BOM 文本。retries 次都失败抛最后一个异常。

    fetch_league_history 用 retries=1:单季一次失败即跳过(测试契约,
    避免整季回填被坏季节拖慢 3 倍)。
    """
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            return r.content.decode("utf-8-sig")
        except Exception as e:
            last = e
            if i < retries - 1:
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
            rows, _ = normalize.parse_fd_results(_get(url, retries=1), s)
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
