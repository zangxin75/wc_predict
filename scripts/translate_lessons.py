#!/usr/bin/env python3
"""scripts/translate_lessons.py — 离线批翻译队伍档案 (causes[] + adjustment notes)。

用法:
  python scripts/translate_lessons.py                  # 全部 48 队 × 5 locale
  python scripts/translate_lessons.py --locale en      # 只跑英文
  python scripts/translate_lessons.py --team Brazil    # 只跑单队(测试用)
  python scripts/translate_lessons.py --dry-run        # 只统计会跑多少,不实际翻译
  python scripts/translate_lessons.py --sleep 1.5      # 每批 GLM 调用间隔(秒)

输入:teams/<Team>.yaml
输出:teams/i18n/<team-slug>.json,结构:
  {
    "updated": "2026-06-20",
    "locales": {
      "en": {"causes": {"<hash16>": "<英文>", ...}, "notes": {...}},
      ...
    }
  }

增量:已翻译的 hash 直接跳过,只翻译新增内容。断点续传安全。
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

# 让脚本能从仓库根目录 import webapp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webapp.i18n_translator import (
    translate_batch_zh_to,
    _is_chinese,
    _hash,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEAMS_DIR = PROJECT_ROOT / "teams"
I18N_DIR = TEAMS_DIR / "i18n"
NAMESPACE = "teams_lessons"   # 缓存 namespace,与运行时 reflection 复用同一磁盘缓存

# 默认目标 locale(除 zh-CN 外)
DEFAULT_LOCALES = ["en", "es", "pt", "ar", "fr"]


def _slug(name: str) -> str:
    """把队名变成合法文件名:Bosnia and Herzegovina → Bosnia_and_Herzegovina"""
    return name.replace(" ", "_").replace("/", "_")


def _load_team(path: Path):
    with open(path, encoding="utf-8") as f:
        d = yaml.safe_load(f) or {}
    team = d.get("name") or path.stem
    return team, d


def _extract_chinese(team_data: dict) -> tuple:
    """从队伍档案提取需要翻译的字符串,返回 (causes_texts, notes_texts)。"""
    causes = []
    for les in (team_data.get("recent_lessons") or []):
        for c in (les.get("causes") or []):
            if c and _is_chinese(c):
                causes.append(c)
    notes = []
    for adj in (team_data.get("elo_adjustments") or []):
        n = adj.get("note")
        if n and _is_chinese(n):
            notes.append(n)
    # 去重(保序)
    causes = list(dict.fromkeys(causes))
    notes = list(dict.fromkeys(notes))
    return causes, notes


def _load_existing_sidecar(team_slug: str) -> dict:
    path = I18N_DIR / f"{team_slug}.json"
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {"updated": "", "locales": {}}


def _save_sidecar(team_slug: str, data: dict):
    I18N_DIR.mkdir(parents=True, exist_ok=True)
    path = I18N_DIR / f"{team_slug}.json"
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.replace(path)


def _flatten_counts(teams_data):
    """统计所有队伍的 causes / notes 数(用于 dry-run 报告)。"""
    total_causes = 0
    total_notes = 0
    teams_with_content = 0
    for _, d in teams_data:
        c, n = _extract_chinese(d)
        if c or n:
            teams_with_content += 1
        total_causes += len(c)
        total_notes += len(n)
    return total_causes, total_notes, teams_with_content


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--locale", action="append", help="目标 locale(可多次),默认全部非中文")
    p.add_argument("--team", help="只跑指定队名(如 'Brazil')")
    p.add_argument("--dry-run", action="store_true", help="只统计,不实际翻译")
    p.add_argument("--sleep", type=float, default=1.5, help="每批 GLM 调用间隔(秒),默认 1.5")
    p.add_argument("--batch-size", type=int, default=25, help="每批翻译条数,默认 25")
    args = p.parse_args()

    locales = args.locale or DEFAULT_LOCALES
    bad = [l for l in locales if l not in ("en", "es", "pt", "ar", "fr")]
    if bad:
        print(f"✗ 不支持的 locale: {bad}(只支持 en/es/pt/ar/fr)")
        sys.exit(1)

    # 收集所有队伍
    yaml_files = sorted(TEAMS_DIR.glob("*.yaml"))
    if not yaml_files:
        print(f"✗ 没找到 yaml:{TEAMS_DIR}")
        sys.exit(1)

    teams_data = []
    for yf in yaml_files:
        try:
            team, data = _load_team(yf)
            if args.team and team.lower() != args.team.lower():
                continue
            teams_data.append((team, data, yf.stem))
        except Exception as e:
            print(f"⚠ 跳过 {yf.name}: {e}")

    if not teams_data:
        print(f"✗ 没匹配到队伍:{args.team or '(全部)'}")
        sys.exit(1)

    total_causes, total_notes, teams_with_content = _flatten_counts(
        [(t, d) for t, d, _ in teams_data]
    )
    print(f"📊 概况:{len(teams_data)} 队,{teams_with_content} 队有内容")
    print(f"   - 553 causes  估算(实际 {total_causes} 条)")
    print(f"   - {total_notes} 条 adjustment notes")
    print(f"   - 目标 locale:{locales}")
    print(f"   - 预计 GLM 调用:每 locale ≤ "
          f"{(total_causes + total_notes) // args.batch_size + len(locales)} 次")
    print()

    if args.dry_run:
        return

    # 实际翻译
    overall_start = time.time()
    total_api_calls = 0
    translated_pairs = 0
    cached_pairs = 0

    for locale in locales:
        print(f"\n🌐 === {locale} ===")
        loc_start = time.time()
        loc_calls = 0
        loc_translated = 0
        loc_cached = 0

        for team, data, stem in teams_data:
            causes, notes = _extract_chinese(data)
            if not causes and not notes:
                continue

            slug = _slug(team)
            sidecar = _load_existing_sidecar(slug)
            existing_locale = sidecar.setdefault("locales", {}).setdefault(locale, {})
            existing_causes = existing_locale.setdefault("causes", {})
            existing_notes = existing_locale.setdefault("notes", {})

            # 哪些还需要翻译?(sidecar key = SHA1(text)[:16])
            todo_causes = [c for c in causes if _hash(c) not in existing_causes]
            todo_notes = [n for n in notes if _hash(n) not in existing_notes]
            todo = todo_causes + todo_notes
            cached = len(causes) + len(notes) - len(todo)

            if not todo:
                print(f"   ✓ {team}: 全部已翻译({cached} 条 cached)")
                loc_cached += cached
                _save_sidecar(slug, sidecar)
                continue

            # 批翻
            for batch_start in range(0, len(todo), args.batch_size):
                batch = todo[batch_start:batch_start + args.batch_size]
                is_note_batch = batch_start >= len(todo_causes)
                ns = f"{NAMESPACE}_notes" if batch_start >= len(todo_causes) else f"{NAMESPACE}_causes"
                tr = translate_batch_zh_to(batch, locale, namespace=ns)

                # 写回 sidecar(causes 先,notes 后)
                if batch_start < len(todo_causes):
                    chunk_end = min(batch_start + args.batch_size, len(todo_causes))
                    for orig, translated in zip(todo[batch_start:chunk_end], tr):
                        existing_causes[_hash(orig)] = translated
                else:
                    note_offset = batch_start - len(todo_causes)
                    chunk_end = min(note_offset + len(batch), len(todo_notes))
                    for orig, translated in zip(todo_notes[note_offset:chunk_end], tr):
                        existing_notes[_hash(orig)] = translated

                loc_calls += 1
                loc_translated += len(batch)
                time.sleep(args.sleep)

            sidecar["updated"] = time.strftime("%Y-%m-%d")
            _save_sidecar(slug, sidecar)
            loc_cached += cached
            print(f"   • {team}: +{len(todo)} 翻译({cached} cached)")

        loc_elapsed = time.time() - loc_start
        total_api_calls += loc_calls
        translated_pairs += loc_translated
        cached_pairs += loc_cached
        print(f"   → {locale}:{loc_calls} GLM calls,{loc_translated} 新译,{loc_cached} 缓存命中,"
              f"用时 {loc_elapsed:.1f}s")

    overall = time.time() - overall_start
    print(f"\n✅ 完成")
    print(f"   总 GLM 调用:{total_api_calls}")
    print(f"   新译条数:{translated_pairs}")
    print(f"   缓存命中:{cached_pairs}")
    print(f"   总用时:{overall / 60:.1f} 分钟")
    print(f"   输出:{I18N_DIR}/<team>.json")


if __name__ == "__main__":
    main()
