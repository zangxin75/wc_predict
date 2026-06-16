#!/usr/bin/env python3
"""scripts/scan_2026_misses.py — 扫 2026 已结束比赛 → 找预测错的 → LLM 归因 → 写队伍档案。

数据源:webapp/data/cache.json 中 status=finished 的 2026 比赛。
判断错场:probs 三态 argmax != 真实 result。
去重:teams/{team}.yaml 已有 (date, vs) lesson 的跳过。

用法:
  python scripts/scan_2026_misses.py                 # 默认跑全量
  python scripts/scan_2026_misses.py --max 5         # 限量 5 场
  python scripts/scan_2026_misses.py --dry-run       # 只列出,不调 LLM

需要 LITELLM_MASTER_KEY(从词源效能 .env 读)。
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webapp import lessons_fetcher as lf

CACHE_PATH = Path(__file__).resolve().parent.parent / "webapp" / "data" / "cache.json"
TOURNAMENT_LABEL = "2026 美加墨世界杯"


def _argmax_result(probs):
    """probs = [p_home, p_draw, p_away] → 'home_win' / 'draw' / 'away_win'(对齐 cache.result 命名)。"""
    if not probs or len(probs) < 3:
        return None
    idx = int(probs.index(max(probs)))
    return ["home_win", "draw", "away_win"][idx]


def load_2026_misses(max_count):
    """从 cache.json 拉 2026 已结束且预测错的比赛。"""
    if not CACHE_PATH.exists():
        print(f"✗ cache.json 不存在: {CACHE_PATH}")
        return []
    with open(CACHE_PATH, encoding="utf-8") as f:
        cache = json.load(f)
    sched = cache.get("schedule", [])

    out = []
    for m in sched:
        if str(m.get("date", ""))[:4] != "2026":
            continue
        if m.get("status") != "finished":
            continue
        score = m.get("score") or [None, None]
        if score[0] is None or score[1] is None:
            continue
        pred_result = _argmax_result(m.get("probs"))
        actual_result = m.get("result")
        if not pred_result or not actual_result:
            continue
        if pred_result == actual_result:
            continue  # 命中,跳过
        out.append({
            "tournament": TOURNAMENT_LABEL,
            "date": m["date"],
            "home": m["home"],
            "away": m["away"],
            "home_zh": m.get("home_zh", m["home"]),
            "away_zh": m.get("away_zh", m["away"]),
            "score": score,
            "pred": m.get("top_score") or [None, None],
            "pred_result": pred_result,
            "actual_result": actual_result,
        })
        if len(out) >= max_count:
            break
    return out


def already_has_lesson(team, date, vs):
    """检查 teams/{team}.yaml 是否已有 (date, vs) 的 lesson。"""
    path = Path(lf.TEAMS_DIR) / f"{team}.yaml"
    if not path.exists():
        return False
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for les in data.get("recent_lessons", []):
            if les.get("date") == date and vs in str(les.get("vs", "")):
                return True
        return False
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="扫 2026 已结束比赛的预测错场 → LLM 归因")
    ap.add_argument("--max", type=int, default=20, help="最多处理多少场(默认 20)")
    ap.add_argument("--sleep", type=float, default=2.0, help="每场间隔秒数(默认 2)")
    ap.add_argument("--dry-run", action="store_true", help="只列出,不调 LLM")
    args = ap.parse_args()

    env_path = "/home/zx/project/词源效能/token-efficiency/.env"
    if os.path.exists(env_path) and not os.environ.get("LITELLM_MASTER_KEY"):
        with open(env_path) as f:
            for line in f:
                if line.startswith("LITELLM_MASTER_KEY="):
                    os.environ["LITELLM_MASTER_KEY"] = line.split("=", 1)[1].strip()
                    break
    if not args.dry_run and not os.environ.get("LITELLM_MASTER_KEY"):
        print("✗ 未找到 LITELLM_MASTER_KEY")
        sys.exit(1)

    misses = load_2026_misses(args.max)
    if not misses:
        print("未找到 2026 错场(可能都已命中,或还没比赛结束)")
        return

    print(f"=== 找到 {len(misses)} 场 2026 错场 ===\n")
    if args.dry_run:
        for i, m in enumerate(misses, 1):
            print(f"[{i}] {m['date']} {m['home_zh']} {m['score'][0]}-{m['score'][1]} {m['away_zh']}"
                  f"  (系统预测 {m['pred_result']}, 实际 {m['actual_result']})")
        return

    success = 0
    for i, m in enumerate(misses, 1):
        date, home, away = m["date"], m["home"], m["away"]
        score, pred = m["score"], m["pred"]
        print(f"[{i}/{len(misses)}] {date} {m['home_zh']} {score[0]}-{score[1]} {m['away_zh']}"
              f"  (预测 {m['pred_result']}, 实际 {m['actual_result']})")

        if already_has_lesson(home, date, away) or already_has_lesson(away, date, home):
            print("  → 已有 lesson,跳过")
            continue

        lesson = lf.fetch_lesson_for_match(
            home, away, date, pred, score,
            tournament=m["tournament"],
        )
        if "error" in lesson:
            print(f"  ✗ LLM 失败: {lesson['error'][:80]}")
            time.sleep(args.sleep)
            continue

        print(f"  → causes({len(lesson.get('causes', []))} 条), reusable={lesson.get('reusable')}, conf={lesson.get('confidence')}")

        for team, opp in [(home, away), (away, home)]:
            try:
                p = lf.update_team_yaml(
                    team, lesson, date, opp, pred, score,
                    tournament=m["tournament"],
                )
                print(f"  ✓ 写入 {p.name}")
            except Exception as e:
                print(f"  ✗ 写 {team} 失败: {e}")

        success += 1
        time.sleep(args.sleep)

    print(f"\n=== 完成:成功 {success}/{len(misses)} ===")


if __name__ == "__main__":
    main()
