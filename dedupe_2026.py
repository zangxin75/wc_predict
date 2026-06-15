"""dedupe_2026.py — 一次性清洗 results.csv 的 2026 世界杯重复日期行

问题:历史手动回写时,4 场比赛各被填了 2 个日期(比分对、日期错一份),导致
verify_2026 等按 date 筛选的工具读到重复场(16 场而非 12 场)。
  South Korea vs Czech 2-1  → [6/11, 6/12](ESPN=6/12)
  United States vs Paraguay 4-1 → [6/12, 6/13](ESPN=6/13)
  Haiti vs Scotland 0-1     → [6/13, 6/14](ESPN=6/14)
  Australia vs Turkey 2-0   → [6/13, 6/14](ESPN=6/14)

修法:以 wc2026_live.csv(ESPN 权威)日期为准,对 (home,away,hs,as_) 相同但
日期不同的行,删除非 ESPN 日期的副本。严格限定 tournament=FIFA World Cup 且
date>=2026-06-11,不触碰历史数据。

安全:默认 dry-run,--apply 才真删。删除前打印每行,可核查。

用法:
    python dedupe_2026.py            # dry-run 看要删的行
    python dedupe_2026.py --apply    # 真删
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.csv")
LIVE = os.path.join(HERE, "wc2026_live.csv")


def load_esp_dates():
    """读 ESPN 权威日期:{(home,away,hs,as_): date}。"""
    esp = {}
    if not os.path.exists(LIVE):
        return esp
    with open(LIVE, newline="", encoding="utf-8") as f:
        for r in csv.reader(f):
            if r[0] == "date" or len(r) < 5:
                continue
            esp[(r[1], r[2], r[3], r[4])] = r[0]
    return esp


def find_dupes(rows, esp_dates):
    """返回 (to_delete_indices, keep_info):要删的行索引 + 保留说明。"""
    idx_by_key = defaultdict(list)
    for i, r in enumerate(rows):
        if len(r) < 6:
            continue
        if r[5] != "FIFA World Cup" or r[0] < "2026-06-11":
            continue
        idx_by_key[(r[1], r[2], r[3], r[4])].append(i)
    to_delete = []
    info = []
    for key, idxs in idx_by_key.items():
        if len(idxs) < 2:
            continue
        esp_date = esp_dates.get(key)
        if not esp_date:
            info.append((key, idxs, None, "无ESPN对照,跳过(不删)"))
            continue
        # 删除日期 != ESPN 的行
        for i in idxs:
            if rows[i][0] != esp_date:
                to_delete.append(i)
                info.append((key, i, rows[i][0],
                             f"删(非ESPN日期),保留 ESPN={esp_date}"))
    return to_delete, info


def main():
    ap = argparse.ArgumentParser(description="清洗 2026 世界杯重复日期行")
    ap.add_argument("--apply", action="store_true", help="真删(默认 dry-run)")
    args = ap.parse_args()

    with open(RESULTS, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        rows = list(reader)

    esp = load_esp_dates()
    to_del, info = find_dupes(rows, esp)

    print("=" * 64)
    print(f"  2026 重复日期清洗  [{'APPLY' if args.apply else 'DRY-RUN'}]")
    print("=" * 64)
    print(f"  results.csv 共 {len(rows)} 行,ESPN 权威日期 {len(esp)} 场")
    print(f"  待删除行:{len(to_del)}")
    for key, i, old_date, note in info:
        if note.startswith("删"):
            r = rows[i]
            print(f"    删 {old_date} {r[1]} {r[3]}-{r[4]} {r[2]}  ({note})")
    skip = [x for x in info if x[3].startswith("无ESPN")]
    for key, idxs, _, note in skip:
        print(f"    跳过 {key[0]} {key[2]}-{key[3]} {key[1]}  ({note})")

    if not to_del:
        print("  无需删除。")
        print("=" * 64)
        return
    if args.apply:
        keep = [r for i, r in enumerate(rows) if i not in set(to_del)]
        with open(RESULTS, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if header:
                w.writerow(header)
            w.writerows(keep)
        print(f"  ✓ 已删除 {len(to_del)} 行,{len(rows)} → {len(keep)} 行")
        print(f"  重算 Elo 生效:python compute_elo.py 或 run_live.py")
    else:
        print(f"  [dry-run] 加 --apply 真删")
    print("=" * 64)


if __name__ == "__main__":
    main()
