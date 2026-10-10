# webapp/leagues_cache.py
"""联赛板块独立缓存 —— 与 WC cache.py 完全隔离(独立文件、独立锁、独立字段),
任一板块构建失败不污染另一板块。

契约同 WC:API 路由只读当前快照;后台 leagues_updater 算完整体替换。
注意:所有被赋值的模块级变量都显式列在各自函数的 global 声明里
(不复刻 cache.py:64 漏声明 _market/_market_built_at 的 bug —— 测试 test_save_then_load_reads_back 钉死)。
"""
import json
import os
import threading
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(HERE, "data", "leagues_cache.json")

_lock = threading.RLock()

_payload = {}            # {code: {"predictions": {...}, "standings": [...], "sim": {...}}}
_status = {"running": False, "last_success": None, "last_error": None, "updated_at": None}


def load():
    """启动时调用。文件不存在/损坏 → False(由 updater 首轮填充)。"""
    global _payload, _status
    if not os.path.exists(CACHE_FILE):
        return False
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        with _lock:
            _payload = d.get("payload", {})
            _status = d.get("status", dict(_status))
        return True
    except Exception:
        return False


def save():
    with _lock:
        snap = {"payload": _payload, "status": _status}
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)


def set_league(code, predictions=None, standings=None, sim=None):
    """局部更新某联赛的字段(None = 不动),整体替换引用保证快照一致。"""
    global _payload
    with _lock:
        entry = dict(_payload.get(code, {}))
        if predictions is not None:
            entry["predictions"] = predictions
        if standings is not None:
            entry["standings"] = standings
        if sim is not None:
            entry["sim"] = sim
        _payload = {**_payload, code: entry}


def get_league(code):
    with _lock:
        return _payload.get(code)


def get_codes():
    with _lock:
        return sorted(_payload.keys())


def set_status(**kw):
    global _status
    with _lock:
        _status = {**_status, **kw,
                   "updated_at": datetime.now().isoformat(timespec="seconds")}


def get_status():
    with _lock:
        return dict(_status)
