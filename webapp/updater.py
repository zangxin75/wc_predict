"""updater.py — 后台更新器(定时循环 + 容错 + 手动触发)

职责:每 N 分钟跑一次完整闭环 ——
  fetch_wc2026(子进程)→ update_live(函数,拿 added)→ 重建 Elo → 重算赛程
  → atomic_replace 刷新缓存 → save 持久化。若夺冠榜过期则补跑 monte_carlo。

容错铁律:
  · fetch 失败 = warning(没新数据就用旧的继续,不影响预测)
  · update/build 失败 = error(终止本次,缓存保留上一成功快照)
  · 任何未预期异常被 loop 捕获,绝不让后台线程退出、绝不拖垮服务

触发:loop(定时) + trigger_now(/api/refresh 手动唤醒 sleep 中的循环)。
"""

import datetime
import os
import subprocess
import sys
import threading

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from webapp import cache, predictor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REFRESH_MIN = 15          # 后台循环间隔(分钟)
MONTE_CARLO_SIMS = 10000  # 复冠榜蒙特卡洛模拟次数

_wake = threading.Event()   # trigger_now 唤醒用
_stop = threading.Event()   # 优雅停机用(测试/关闭)


def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _status_ok(added, msg="ok"):
    return {
        "last_updated": _now_iso(),
        "success": True,
        "next_update": (datetime.datetime.now()
                        + datetime.timedelta(minutes=REFRESH_MIN)).isoformat(timespec="seconds"),
        "error": None,
        "added": added,
        "running": False,
        "note": msg,
    }


def _status_fail(err, added=0):
    return {
        "last_updated": _now_iso(),
        "success": False,
        "next_update": (datetime.datetime.now()
                        + datetime.timedelta(minutes=REFRESH_MIN)).isoformat(timespec="seconds"),
        "error": str(err),
        "added": added,
        "running": False,
    }


def _run_fetch():
    """子进程跑 fetch_wc2026.py。返回 rc(0=成功)。失败不抛(网络偶发)。"""
    try:
        rc = subprocess.call([sys.executable, "fetch_wc2026.py"],
                             cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        return rc
    except Exception as e:
        print(f"[updater] fetch 异常: {e}")
        return -1


def update_once(force_monte_carlo=False):
    """跑一次完整闭环。任一步失败捕获并记 status,不抛出。

    force_monte_carlo:/api/refresh 可强制刷新夺冠榜(否则按 TTL)。
    """
    cache.atomic_replace(status={"running": True})
    added = 0
    try:
        # 1) fetch(子进程;失败仅 warning,用旧数据继续)
        rc = _run_fetch()
        if rc != 0:
            print(f"[updater] fetch 返回 rc={rc}(warning,用现有数据继续)")

        # 2) update_live(函数调用,拿 added)
        import update_live
        new, na_upd, conflicts, dup = update_live.diff_live()
        if new:
            update_live.append_new(new)
        if na_upd:
            update_live.apply_na_updates(na_upd)
        added = len(new) + len(na_upd)
        if conflicts:
            print(f"[updater] ⚠ {len(conflicts)} 场比分冲突(results vs ESPN),已跳过")

        # 3) 重建 Elo(今天为 cutoff)+ 重算赛程
        elo = predictor.build_elo_for_today()
        sched, groups = predictor.build_all(elo)
        cache.atomic_replace(elo=elo, schedule=sched, groups=groups,
                             status=_status_ok(added))
        cache.save()
        print(f"[updater] ✓ 更新完成:新增/补全 {added} 场,赛程 {len(sched)} 场")

        # 4) monte_carlo(按 TTL,或强制)—— 成本高,单独 try(失败不影响 schedule)
        if force_monte_carlo or cache.need_monte_carlo():
            print("[updater] 跑 monte_carlo(夺冠榜)...")
            champs = predictor.run_monte_carlo(MONTE_CARLO_SIMS)
            cache.atomic_replace(champions=champs,
                                 champions_built_at=_now_iso())
            cache.save()
            top3 = ", ".join(f"{c['team_zh']}{c['prob']*100:.1f}%"
                             for c in champs[:3])
            print(f"[updater] ✓ 复冠榜刷新:Top3 = {top3}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        cache.atomic_replace(status=_status_fail(e, added))
        print(f"[updater] ✗ 更新失败: {e}")


def loop(interval_min=REFRESH_MIN):
    """后台主循环。启动后立即跑一次,之后每 interval_min 跑一次。

    trigger_now() 通过 _wake Event 唤醒 sleep 中的循环(手动刷新)。
    """
    print(f"[updater] 后台循环启动(间隔 {interval_min} 分钟)")
    while not _stop.is_set():
        update_once()
        # 等待 interval 或被 _wake 提前唤醒
        woken = _wake.wait(timeout=interval_min * 60)
        if woken:
            _wake.clear()
            print("[updater] 收到手动刷新信号,立即更新")
    print("[updater] 后台循环退出")


def trigger_now(force_monte_carlo=False):
    """/api/refresh 调用:唤醒 sleep 中的循环立即跑一次。

    返回当前 status(前端可立即展示"刷新中")。force_monte_carlo=True 会顺带
    刷新夺冠榜(用户显式要最新夺冠概率时)。
    """
    # 用一个标记让被唤醒的 update_once 知道是否强制 monte_carlo
    # (简化:trigger 走 loop 路径,不强制;强制走同步路径见下)
    if force_monte_carlo:
        # 同步跑(用户在等),但限时不阻塞太久——monte_carlo 在后台线程内
        threading.Thread(target=update_once, args=(True,), daemon=True).start()
    else:
        _wake.set()
    return cache.get_status()


def stop():
    """优雅停机(测试用)。"""
    _stop.set()
    _wake.set()
