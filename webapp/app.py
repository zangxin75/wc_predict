"""app.py — FastAPI 应用(lifespan 起后台线程 + 路由 + 静态挂载)

启动:
    cd wc_predict
    uvicorn webapp.app:app --reload --port 8000
    浏览器: http://localhost:8000

数据流:
    lifespan → cache.load_or_build()(同步快)→ 起后台 updater 线程(15min 循环)
    API 路由 → 只读 cache.get_*()(微秒级,绝不重算)
    后台线程 → fetch→update→build_elo→build_all→cache.atomic_replace

路由函数一律普通 def:FastAPI 自动丢线程池跑同步代码,不阻塞事件循环。
"""

import os
import sys
import threading
from contextlib import asynccontextmanager

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from webapp import cache, updater

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")

# ── 配置旋钮 ──────────────────────────────────────────────────────
POLL_SEC = 30                # 前端轮询间隔(秒)—— 注入 app.js
# 后台循环间隔见 updater.REFRESH_MIN


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动:载入/构建缓存 + 起后台更新线程。关闭:优雅停机。"""
    print("[app] 启动:载入缓存...")
    cache.load_or_build()
    t = threading.Thread(target=updater.loop, kwargs={"interval_min": updater.REFRESH_MIN},
                         daemon=True, name="updater")
    t.start()
    print("[app] 后台更新线程已启动")
    yield
    updater.stop()
    print("[app] 关闭")


app = FastAPI(
    title="2026 世界杯预测系统",
    description="Elo + Dixon-Coles + 蒙特卡洛 的实时预测面板(后台自动抓取 ESPN → 重算 → 刷新)",
    version="1.0",
    lifespan=lifespan,
)

# CORS(开发便利;本地工具全开放)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 页面 ──────────────────────────────────────────────────────────
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ── API ───────────────────────────────────────────────────────────
@app.get("/api/config")
def get_config():
    """前端配置(轮询间隔等)。"""
    return {"poll_sec": POLL_SEC}


@app.get("/api/status")
def get_status():
    """后台线程状态:上次更新/成功否/下次预计/新增场次。"""
    return cache.get_status()


@app.get("/api/schedule")
def get_schedule(stage: str | None = Query(None, description="按阶段过滤:Group/R32/R16/QF/SF/Final")):
    """全部赛程富化视图。每场含 status/score/probs/top_score/group/stage/city。

    可选 ?stage=Group 只看小组赛。
    """
    sched = cache.get_schedule()
    if stage:
        sched = [m for m in sched if m.get("stage") == stage]
    return {"count": len(sched), "matches": sched}


@app.get("/api/groups")
def get_groups():
    """12 组积分榜:已打真实积分 + 未打预测期望积分。"""
    return cache.get_groups()


@app.get("/api/champions")
def get_champions(top: int = Query(15, ge=1, le=48)):
    """夺冠概率榜(蒙特卡洛)。?top=N 限制返回条数。"""
    champs = cache.get_champions()
    return {"count": len(champs), "built_at": cache.get_status().get("champions_built_at"),
            "ranking": champs[:top]}


@app.get("/api/match")
def get_match(date: str = Query(...), home: str = Query(...), away: str = Query(...)):
    """单场详情(含 top5 比分概率、Elo、预期进球)。用 query 参数避免队名编码问题。"""
    for m in cache.get_schedule():
        if m["date"] == date and m["home"] == home and m["away"] == away:
            return m
    raise HTTPException(status_code=404, detail=f"未找到: {date} {home} vs {away}")


_backtest_cache = None
_backtest_lock = threading.Lock()


@app.get("/api/backtest")
def get_backtest():
    """2018/2022 世界杯正赛回测:赛前 Elo 预测 vs 真实结果 + argmax 命中率。

    静态历史数据,首次请求计算(两届赛前 Elo 重建约十秒)后永久内存缓存。
    """
    global _backtest_cache
    if _backtest_cache is None:
        with _backtest_lock:
            if _backtest_cache is None:
                from webapp import predictor
                _backtest_cache = predictor.build_backtest()
    return _backtest_cache


@app.post("/api/refresh")
def refresh(force_monte_carlo: bool = Query(False, description="是否顺带刷新夺冠榜(慢)")):
    """手动触发立即更新(唤醒后台线程,不等 15 分钟)。"""
    status = updater.trigger_now(force_monte_carlo=force_monte_carlo)
    return {"triggered": True, "status": status}


# 静态文件(CSS/JS)。index.html 由根路由单独处理。
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
