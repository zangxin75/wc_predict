# webapp/leagues_updater.py
"""联赛后台更新线程 —— 复刻 updater.py 的容错铁律,与 WC updater 完全独立。

流程:新鲜度补抓(fetch_all_if_stale)→ 重建统一 Elo 池 → 逐联赛
DC 拟合 + rebuild + standings → 写 leagues_cache。单联赛失败隔离:
一个联赛炸了只记 error,其余联赛照常更新;Elo 池失败则本轮整体放弃
(旧快照保留)。UCL 在 Task 16 接入。
"""
import threading
import traceback

from leagues import build, config
from webapp import leagues_cache

REFRESH_MIN = 60

_wake = threading.Event()
_stop = threading.Event()


def update_once():
    """跑一轮完整更新。任何异常内部消化,绝不抛出(容错铁律)。"""
    leagues_cache.set_status(running=True)
    errors = []
    try:
        report = build.fetch_all_if_stale()
        errors.extend(report.get("errors", []))
    except Exception as e:
        errors.append(f"fetch: {e}")
        traceback.print_exc()
    try:
        elo, _ = build.build_elo_pool()
    except Exception as e:
        traceback.print_exc()
        leagues_cache.set_status(running=False, last_error=f"elo: {e}")
        leagues_cache.save()
        return
    for code in config.LEAGUE_CODES:
        try:
            build.rebuild_dc(code)
            payload = build.rebuild(code, elo=elo)
            standings = build.compute_standings(build.load_results(code), payload["season"])
            sim = None
            try:
                from leagues import season_sim
                sim = season_sim.simulate_season(code)
            except Exception as e:
                errors.append(f"{code} sim: {e}")
            leagues_cache.set_league(code, predictions=payload, standings=standings, sim=sim)
        except Exception as e:
            errors.append(f"{code}: {e}")
            traceback.print_exc()
    # UCL:借用制 + TheOddsAPI 赔率(配额敏感,失败静默降级为无赔率)
    try:
        from leagues import fetch_odds_ucl
        ucl_odds = fetch_odds_ucl.fetch_ucl_odds()
    except Exception as e:
        errors.append(f"ucl_odds: {e}")
        ucl_odds = {}
    try:
        payload = build.rebuild_ucl(elo=elo, ucl_odds=ucl_odds)
        leagues_cache.set_league("UCL", predictions=payload, standings=[])
    except Exception as e:
        errors.append(f"UCL: {e}")
        traceback.print_exc()
    leagues_cache.set_status(running=False,
                             last_error="; ".join(errors) if errors else None,
                             last_success=None if errors else "ok")
    leagues_cache.save()


def loop():
    """后台主循环:立即跑一轮,之后每 REFRESH_MIN 分钟一轮;trigger_now 可提前唤醒。"""
    print(f"[leagues_updater] 后台循环启动(间隔 {REFRESH_MIN} 分钟)")
    while not _stop.is_set():
        try:
            update_once()
        except Exception:
            traceback.print_exc()          # 双保险:update_once 内部已消化
        _wake.wait(timeout=REFRESH_MIN * 60)
        _wake.clear()
    print("[leagues_updater] 后台循环退出")


def trigger_now():
    """唤醒 sleep 中的循环立即跑一轮(手动刷新用)。"""
    _wake.set()
    return leagues_cache.get_status()


def stop():
    """优雅停机(测试/关服用)。"""
    _stop.set()
    _wake.set()
