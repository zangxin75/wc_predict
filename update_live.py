"""update_live.py — 2026 已打结果回写 results.csv(去重保护)

数据流:fetch_wc2026 → wc2026_live.csv → update_live → append results.csv
回写后重跑 compute_elo / compute_ad 即可刷新参数(预测随之更新)。

去重:key=(date, home, away)。fetch 多次跑会拉到同样的已打比赛,三元组去重
保证幂等(重复跑不重复 append)。
安全:默认 dry-run(只报告),--apply 才真写入 results.csv。

用法:
    python update_live.py            # dry-run,看新增数
    python update_live.py --apply    # 真回写
"""

import argparse
import csv
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.csv")
LIVE = os.path.join(HERE, "wc2026_live.csv")


def _read_data_rows(path):
    """读 CSV,跳过空行和列头,返回 row 列表。文件不存在返回 []。"""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row or row[0] == "date":
                continue
            rows.append(row)
    return rows


def diff_live(results_path=RESULTS, live_path=LIVE):
    """对比 live 与 results,返回 (new_rows, dup_count)。

    new_rows:live 里有但 results 没有的(按 (date,home,away) 去重)。
    dup_count:已存在的(跳过)。
    """
    existing = set()
    for row in _read_data_rows(results_path):
        if len(row) >= 3:
            existing.add((row[0], row[1], row[2]))
    new, dup = [], 0
    for row in _read_data_rows(live_path):
        if len(row) < 3:
            continue
        key = (row[0], row[1], row[2])
        if key in existing:
            dup += 1
        else:
            new.append(row)
            existing.add(key)              # 防 live 内部自身重复
    return new, dup


def append_new(new_rows, results_path=RESULTS):
    """append 新行到 results.csv(列头已存在,纯 append)。返回写入行数。"""
    if not new_rows:
        return 0
    with open(results_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for row in new_rows:
            w.writerow(row)
    return len(new_rows)


def main():
    ap = argparse.ArgumentParser(description="2026 已打结果回写 results.csv")
    ap.add_argument("--apply", action="store_true",
                    help="真写入(默认 dry-run,只报告)")
    ap.add_argument("--results", default=RESULTS)
    ap.add_argument("--live", default=LIVE)
    args = ap.parse_args()

    if not os.path.exists(args.live):
        print("无 wc2026_live.csv,先跑:python fetch_wc2026.py")
        return

    new, dup = diff_live(args.results, args.live)
    total = len(new) + dup
    print("=" * 56)
    print(f"  2026 已打结果回写  [{('APPLY' if args.apply else 'DRY-RUN')}]")
    print("=" * 56)
    print(f"  live 共 {total} 场:新增 {len(new)},已存在 {dup}")
    if not new:
        print("  无新比赛需回写(数据已是最新)。")
        print("=" * 56)
        return
    print(f"  新增(前 5):")
    for row in new[:5]:
        print(f"    {row[0]} {row[1]} {row[3]}-{row[4]} {row[2]}")
    if args.apply:
        n = append_new(new, args.results)
        print(f"  ✓ 已回写 {n} 场到 {os.path.basename(args.results)}")
        print(f"  刷新参数:重跑 compute_elo(build_elo)/ compute_ad 即生效")
    else:
        print(f"  [dry-run] 加 --apply 真写入 results.csv")
    print("=" * 56)


if __name__ == "__main__":
    main()
