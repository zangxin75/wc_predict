"""
fetch_results.py — 下载国际足球历史比分数据集

数据：1872-至今 所有国家队正式比赛 + 友谊赛，含比分、赛事、城市、中立场地标记。
来源：martj42/international_results (master 分支 results.csv，约 3.7MB / 4.7 万场)
用途：喂给 wc_predict.py 做 Elo 重建 / 泊松参数校准 / XGBoost 训练。

零依赖，仅用 Python 标准库（与 wc_predict.py 保持一致）。用法：
    python fetch_results.py
产出：脚本同目录 results.csv
"""

import csv
import os
import ssl
import sys
import urllib.request
from collections import Counter

# Windows 控制台默认 GBK，强制 UTF-8：队名/城市含大量非 ASCII 字符，否则 print 崩溃
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "results.csv")
EXPECTED_HEADER = ["date", "home_team", "away_team", "home_score",
                   "away_score", "tournament", "city", "country", "neutral"]
UA = "MiroFishBot/1.0 (+research; wc_predict)"


def download():
    print(f"↓ 下载  {URL}")
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp, open(OUT, "wb") as f:
        f.write(resp.read())
    print(f"  已保存  {OUT}  ({os.path.getsize(OUT) / 1024 / 1024:.2f} MB)")
    return OUT


def validate(path):
    """校验 CSV：列头一致 + 行数合理，防止上游结构变更后静默产出脏数据。"""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        if header != EXPECTED_HEADER:
            raise ValueError(
                "列头不匹配！数据集可能已变更结构。\n"
                f"  期望：{EXPECTED_HEADER}\n  实际：{header}"
            )
        rows = list(reader)
    if len(rows) < 40000:
        raise ValueError(f"行数异常（{len(rows)}），期望 4 万+，下载可能不完整。")
    print(f"  校验通过：9 列，{len(rows):,} 场比赛")
    return rows


def summarize(rows):
    dates = [r[0] for r in rows]
    print(f"  日期范围：{min(dates)} ~ {max(dates)}")

    tournaments = Counter(r[5] for r in rows)
    print(f"  赛事种类：{len(tournaments)} 种")

    wc_keys = [k for k in tournaments if "World Cup" in k]
    wc_total = sum(tournaments[k] for k in wc_keys)
    print(f"  世界杯相关：{wc_total} 场")
    for k in sorted(wc_keys, key=lambda x: -tournaments[x])[:5]:
        print(f"      {tournaments[k]:>5}  {k}")

    neutral = sum(1 for r in rows if r[8].strip().upper() == "TRUE")
    print(f"  中立场地：{neutral:,} 场（世界杯预测主要用这类数据）")


def main():
    print("=" * 56)
    print("  国际足球历史比分数据集 下载")
    print("=" * 56)
    rows = validate(download())
    print("-" * 56)
    summarize(rows)
    print("=" * 56)
    print("✓ 完成。下一步可用 results.csv 重建 Elo / 校准泊松参数。")


if __name__ == "__main__":
    main()
