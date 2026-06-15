"""update_live.py — 2026 已打结果回写 results.csv(去重 + NA 补全)

数据流:fetch_wc2026 → wc2026_live.csv → update_live → 更新 results.csv
回写后重跑 compute_elo / compute_ad 即可刷新参数(预测随之更新)。

三层处理(按 (date, home, away) key 匹配):
  1. key 不存在 → 追加新行(新比赛)
  2. key 存在且现有比分是 NA → 补真实比分(占位行已打,核心修复)
  3. key 存在且有真实比分:
     · 与 ESPN 一致 → 跳过(幂等)
     · 与 ESPN 冲突 → 报警(数据不一致,不静默覆盖)

去重保证幂等(重复跑不重复 append/重复更新)。NA 补全是关键:results.csv 整届
赛程预填了 NA 占位行,比赛打完后必须能"穿透"NA 写入真实比分,否则 Elo 永远
漏掉这场(预测偏差累积)。

安全:默认 dry-run(只报告),--apply 才真写入 results.csv。

用法:
    python update_live.py            # dry-run,看新增+补全数
    python update_live.py --apply    # 真回写(追加新行 + 补 NA + 报冲突)
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


def _is_na(row):
    """该行比分是否为 NA 占位(未打)。"""
    return len(row) < 5 or row[3] == "NA" or row[4] == "NA"


def diff_live(results_path=RESULTS, live_path=LIVE):
    """对比 live(ESPN 已打)与 results,返回 (new_rows, na_updates, conflicts, dup_count)。

    new_rows:    key 不存在 → 追加新行
    na_updates:  key 存在但现有比分 NA → 补真实比分 (key, full_live_row)
    conflicts:   key 存在有真实比分但与 ESPN 不一致 → (key, res_score, live_score)
    dup_count:   key 存在且比分一致 → 跳过(幂等)
    """
    res_rows = _read_data_rows(results_path)
    res_map = {(r[0], r[1], r[2]): r for r in res_rows if len(r) >= 3}

    new, na_upd, conflicts, dup = [], [], [], 0
    seen = set()
    for row in _read_data_rows(live_path):
        if len(row) < 5 or _is_na(row):   # live 里 NA 不该出现(已打才有比分),防御
            continue
        key = (row[0], row[1], row[2])
        if key in seen:                   # live 内部去重
            continue
        seen.add(key)
        if key not in res_map:
            new.append(row)
        elif _is_na(res_map[key]):
            na_upd.append((key, row))
        else:
            res_score = f"{res_map[key][3]}-{res_map[key][4]}"
            live_score = f"{row[3]}-{row[4]}"
            if res_score == live_score:
                dup += 1
            else:
                conflicts.append((key, res_score, live_score))
    return new, na_upd, conflicts, dup


def append_new(new_rows, results_path=RESULTS):
    """append 新行到 results.csv。返回写入行数。"""
    if not new_rows:
        return 0
    with open(results_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for row in new_rows:
            w.writerow(row)
    return len(new_rows)


def apply_na_updates(na_updates, results_path=RESULTS):
    """在 results.csv 原位补 NA 行的真实比分。返回更新行数。

    实现:读全部 → 替换匹配行(比分+城市列)→ 整体重写。NA 行其余列(如 city
    占位空值)也一并补 ESPN 数据。
    """
    if not na_updates:
        return 0
    upd_map = {k: row for k, row in na_updates}
    with open(results_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        rows = list(reader)
    n = 0
    for i, row in enumerate(rows):
        if len(row) < 3:
            continue
        key = (row[0], row[1], row[2])
        if key in upd_map and _is_na(row):
            rows[i] = upd_map[key]
            n += 1
    with open(results_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if header:
            w.writerow(header)
        w.writerows(rows)
    return n


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

    new, na_upd, conflicts, dup = diff_live(args.results, args.live)
    print("=" * 56)
    print(f"  2026 已打结果回写  [{('APPLY' if args.apply else 'DRY-RUN')}]")
    print("=" * 56)
    print(f"  新增(append):{len(new)}  补 NA:{len(na_upd)}  "
          f"冲突:{len(conflicts)}  幂等跳过:{dup}")
    if new:
        print(f"  新增(前 5):")
        for row in new[:5]:
            print(f"    {row[0]} {row[1]} {row[3]}-{row[4]} {row[2]}")
    if na_upd:
        print(f"  NA 补全(前 5):")
        for (k, row) in na_upd[:5]:
            print(f"    {k[0]} {k[1]} {row[3]}-{row[4]} {k[2]}")
    if conflicts:
        print(f"  ⚠ 冲突(results vs ESPN 比分不一致,不覆盖,需人工核查):")
        for (k, rs, ls) in conflicts:
            print(f"    {k[0]} {k[1]} vs {k[2]}:results={rs} ESPN={ls}")
    if not (new or na_upd):
        print("  无需更新(数据已是最新)。")
        print("=" * 56)
        return
    if args.apply:
        n1 = append_new(new, args.results)
        n2 = apply_na_updates(na_upd, args.results)
        print(f"  ✓ 追加 {n1} 场,补全 {n2} 场 NA → {os.path.basename(args.results)}")
        print(f"  刷新参数:重跑 compute_elo(build_elo)/ compute_ad 即生效")
    else:
        print(f"  [dry-run] 加 --apply 真写入 results.csv")
    print("=" * 56)


if __name__ == "__main__":
    main()
