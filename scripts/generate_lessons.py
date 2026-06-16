#!/usr/bin/env python3
"""scripts/generate_lessons.py — 扫 backtest 错场 → LLM 归因 → 更新队伍档案 yaml。

用法:
  python scripts/generate_lessons.py                       # 跑 2022 backtest 错场,前 5 场
  python scripts/generate_lessons.py --tournament 2018     # 跑 2018 错场
  python scripts/generate_lessons.py --max 20              # 限量 20 场
  python scripts/generate_lessons.py --sleep 3             # 每场间隔 3 秒

需要 LITELLM_MASTER_KEY(从词源效能 .env 读)。
"""
import argparse
import os
import sys
import time
import json
import requests

# 让脚本能从仓库根目录 import webapp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webapp import lessons_fetcher as lf


def load_backtest_misses(tournament_filter, max_count):
    """从本地 /api/backtest 拉错场列表。返回 [{date, home, away, home_zh, away_zh, score, pred}]。"""
    try:
        r = requests.get("http://localhost:8000/api/backtest", timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"✗ 拉 /api/backtest 失败: {e}")
        return []

    out = []
    for t in data.get("tournaments", []):
        name = t.get("name", "")
        if tournament_filter and tournament_filter not in name:
            continue
        for m in t.get("matches", []):
            if m.get("hit") is False:
                out.append({
                    "tournament": name,
                    "date": m["date"],
                    "home": m["home"],
                    "away": m["away"],
                    "home_zh": m.get("home_zh", m["home"]),
                    "away_zh": m.get("away_zh", m["away"]),
                    "score": m["score"],
                    "pred": m.get("pred_score") or [None, None],
                })
                if len(out) >= max_count:
                    return out
    return out


def already_has_lesson(team, date, vs):
    """检查 teams/{team}.yaml 是否已有 (date, vs) 组合的 lesson,避免重复跑。"""
    from pathlib import Path
    path = Path(lf.TEAMS_DIR) / f"{team}.yaml"
    if not path.exists():
        return False
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for les in data.get("recent_lessons", []):
            if les.get("date") == date and les.get("vs") == team:
                return True
            # vs 是对手名,team 是己方;检查己方档案里 vs==对手 的 lesson
        # 重新精确判:对手名要匹配
        for les in data.get("recent_lessons", []):
            if les.get("date") == date and vs in str(les.get("vs", "")):
                return True
        return False
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="LLM 自动错场归因 → 队伍档案 yaml")
    ap.add_argument("--tournament", default="2022", help="赛事过滤(默认 2022)")
    ap.add_argument("--max", type=int, default=5, help="最多处理多少场(默认 5)")
    ap.add_argument("--sleep", type=float, default=2.0, help="每场间隔秒数(默认 2)")
    args = ap.parse_args()

    # 读 LITELLM_MASTER_KEY(从词源效能 .env)
    env_path = "/home/zx/project/词源效能/token-efficiency/.env"
    if os.path.exists(env_path) and not os.environ.get("LITELLM_MASTER_KEY"):
        with open(env_path) as f:
            for line in f:
                if line.startswith("LITELLM_MASTER_KEY="):
                    os.environ["LITELLM_MASTER_KEY"] = line.split("=", 1)[1].strip()
                    break
    if not os.environ.get("LITELLM_MASTER_KEY"):
        print("✗ 未找到 LITELLM_MASTER_KEY,请在 env 中设置")
        sys.exit(1)

    misses = load_backtest_misses(args.tournament, args.max)
    if not misses:
        print(f"未找到 {args.tournament} backtest 错场")
        return

    print(f"=== 处理 {len(misses)} 场 {args.tournament} 错场 ===\n")
    success = 0
    for i, m in enumerate(misses, 1):
        date, home, away = m["date"], m["home"], m["away"]
        score, pred = m["score"], m["pred"]
        print(f"[{i}/{len(misses)}] {date} {m['home_zh']} {score[0]}-{score[1]} {m['away_zh']}(预测 {pred[0]}-{pred[1]})")

        # 跳过已有 lesson 的(任意一方有就跳过)
        if already_has_lesson(home, date, away) or already_has_lesson(away, date, home):
            print("  → 已有 lesson,跳过")
            continue

        lesson = lf.fetch_lesson_for_match(
            home, away, date, pred, score,
            tournament=f"{m['tournament']} 错场",
        )
        if "error" in lesson:
            print(f"  ✗ LLM 失败: {lesson['error'][:80]}")
            time.sleep(args.sleep)
            continue

        print(f"  → causes({len(lesson.get('causes', []))} 条), reusable={lesson.get('reusable')}, conf={lesson.get('confidence')}")

        # 双方都写 lesson
        for team, opp in [(home, away), (away, home)]:
            try:
                p = lf.update_team_yaml(
                    team, lesson, date, opp, pred, score,
                    tournament=f"{m['tournament']} 错场",
                )
                print(f"  ✓ 写入 {p.name}")
            except Exception as e:
                print(f"  ✗ 写 {team} 失败: {e}")

        success += 1
        time.sleep(args.sleep)

    print(f"\n=== 完成:成功 {success}/{len(misses)} ===")


if __name__ == "__main__":
    main()
