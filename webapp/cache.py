"""cache.py — 缓存层(带锁原子替换,API 永远只读完整快照)

核心契约:
  · API 路由只调用 get_*() 读当前快照(微秒级,绝不触发重算)
  · 后台 updater 线程算完后调 atomic_replace() 整体替换引用
  · 读到的永远是某个完整快照(旧或新),不会半新半旧

并发模型:单一 RLock 序列化所有读写。Python 引用重绑定在 GIL 下本就原子,
锁的作用是保证多字段(status/schedule/...)作为一组一起可见。API 读是微秒级,
持锁不构成瓶颈。

持久化:cache.json 主要为省 monte_carlo 的启动成本(夺冠榜算一次 2 分钟)。
启动时优先载入,后台线程随后刷新 Elo+schedule(快),champions 异步补。
"""

import datetime
import json
import os
import sys
import threading

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from webapp import predictor

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(HERE, "data", "cache.json")

# monte_carlo 过期阈值(小时):超过则后台线程重算夺冠榜
MONTE_CARLO_TTL_HOURS = 24

_lock = threading.RLock()

# 内存缓存(模块级单例,整个进程共享)
_elo = {}
_schedule = []
_groups = {}
_champions = []
_champions_built_at = None   # ISO datetime 或 None
_market = None               # 数据来源面板缓存(后台预热)
_market_built_at = None
_status = {
    "last_updated": None,
    "success": None,
    "next_update": None,
    "error": None,
    "added": 0,
    "running": False,
    "champions_built_at": None,
}


def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def load_or_build():
    """启动时调用。优先读 cache.json,缺则同步快速构建(elo+schedule+groups)。

    monte_carlo 不在此同步跑(成本高):cache.json 有则载入 champions,无则置空,
    由后台 updater 首轮异步补(见 need_monte_carlo)。
    """
    global _elo, _schedule, _groups, _champions, _champions_built_at, _status
    loaded = False
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                d = json.load(f)
            with _lock:
                _elo = d.get("elo", {})
                _schedule = d.get("schedule", [])
                _groups = d.get("groups", {})
                _champions = d.get("champions", [])
                _champions_built_at = d.get("champions_built_at")
                _market = d.get("market")
                _market_built_at = d.get("market_built_at")
                _status = dict(_status)
                _status["champions_built_at"] = _champions_built_at
                _status["last_updated"] = d.get("saved_at")
            loaded = bool(d.get("elo"))
            print(f"[cache] 从 cache.json 载入: {len(_schedule)} 场, "
                  f"champions={'有' if _champions else '无'}, "
                  f"market={'有' if _market else '无'}")
        except Exception as e:
            print(f"[cache] cache.json 载入失败({e}),全量重建")

    if not loaded:
        # 冷启动:同步快速构建(几秒)。champions 留空待后台补。
        print("[cache] 冷启动,同步构建 Elo+赛程(monte_carlo 后台补)...")
        elo = predictor.build_elo_for_today()
        sched, groups = predictor.build_all(elo)
        with _lock:
            _elo = elo
            _schedule = sched
            _groups = groups
            _champions = []
            _champions_built_at = None
            _status["last_updated"] = _now_iso()
            _status["success"] = True
            _status["champions_built_at"] = None
        # 冷启动时也预热 market(避免首次切到"数据来源"标签同步算 ~500ms)
        try:
            m = predictor.market_coverage(elo=elo)
            with _lock:
                _market = m
                _market_built_at = _now_iso()
            print("[cache] 冷启动预热 market:完成")
        except Exception as e:
            print(f"[cache] 冷启动预热 market 失败(忽略,/api/market 兜底): {e}")
        save()


def save():
    """持久化当前快照到 cache.json(主要为 champions 省启动时间)。"""
    with _lock:
        snap = {
            "saved_at": _now_iso(),
            "elo": _elo,
            "schedule": _schedule,
            "groups": _groups,
            "champions": _champions,
            "champions_built_at": _champions_built_at,
            "market": _market,
            "market_built_at": _market_built_at,
        }
    tmp = CACHE_FILE + ".tmp"
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)   # 原子替换,避免半写文件


def atomic_replace(elo=None, schedule=None, groups=None, champions=None,
                   champions_built_at=None, market=None, market_built_at=None,
                   status=None):
    """后台线程算完后,持锁整体替换传入的字段(None 表示不动)。"""
    global _elo, _schedule, _groups, _champions, _champions_built_at
    global _market, _market_built_at, _status
    with _lock:
        if elo is not None:
            _elo = elo
        if schedule is not None:
            _schedule = schedule
        if groups is not None:
            _groups = groups
        if champions is not None:
            _champions = champions
        if champions_built_at is not None:
            _champions_built_at = champions_built_at
            _status["champions_built_at"] = champions_built_at
        if market is not None:
            _market = market
        if market_built_at is not None:
            _market_built_at = market_built_at
        if status:
            for k, v in status.items():
                _status[k] = v


def need_monte_carlo():
    """是否需要(重新)跑 monte_carlo:champions 为空 或 超过 TTL。"""
    with _lock:
        if not _champions or not _champions_built_at:
            return True
        built = datetime.datetime.fromisoformat(_champions_built_at)
        age = datetime.datetime.now() - built
        return age.total_seconds() > MONTE_CARLO_TTL_HOURS * 3600


# ── 只读 getter(API 路由用,持锁返回当前快照)──────────────────────
def get_elo():
    with _lock:
        return _elo


def get_schedule():
    with _lock:
        return _schedule


def get_groups():
    with _lock:
        return _groups


def get_champions():
    with _lock:
        return _champions


def get_status():
    with _lock:
        return dict(_status)


def get_market():
    """返回数据来源面板缓存(后台 updater 预热,冷启动由 /api/market 兜底同步算)。
    None 表示尚未构建。
    """
    with _lock:
        return _market


def refresh_market(elo=None):
    """用给定的 elo 重算并替换 market 缓存;返回新缓存值。

    失败不抛(只记到 status,不影响主流程)。调用方应传 cache 当前的 elo
    以避免重建 compute_elo.build_elo 的开销。
    """
    global _market, _market_built_at
    try:
        m = predictor.market_coverage(elo=elo if elo is not None else _elo)
        with _lock:
            _market = m
            _market_built_at = _now_iso()
        return m
    except Exception as e:
        print(f"[cache] refresh_market 失败: {e}")
        return None
