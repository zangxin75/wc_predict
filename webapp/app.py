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

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from webapp import cache, updater, payments

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


_market_cache = None
_market_lock = threading.Lock()


@app.get("/api/market")
def get_market():
    """数据来源面板:每场预测的数据源(市场反演λ模式A/B vs Elo)+ 反演λ对比。

    静态(随 ou_wc.csv/odds_wc.csv 变),首次请求计算后内存缓存。
    """
    global _market_cache
    if _market_cache is None:
        with _market_lock:
            if _market_cache is None:
                from webapp import predictor
                _market_cache = predictor.market_coverage()
    return _market_cache


_value_bets_cache = None
_value_bets_lock = threading.Lock()


@app.get("/api/value-bets")
def get_value_bets(min_edge: float = Query(0.03, ge=0.0, le=0.5,
                                            description="最小 edge 阈值(默认 3%%)"),
                    x_user_email: str = Query(default="", alias="x_user_email")):
    """价值下注面板(纯 Elo vs 市场 devig)— B 方案。Pro 功能,需订阅。

    返回:
      - edge_distribution: max edge 分布统计(平均/分位/各阈值场数)
      - upcoming: 未赛价值机会 Top 20(按 Kelly 降序),含 Elo/Mkt 三态 + 推荐下注
      - calibration_note: 已知结论(Elo 已被最优校准,与市场 corr +0.87)

    静态(随 odds_wc.csv 变),首次请求计算后内存缓存。如需重算可重启服务。
    """
    if not payments.is_paid(x_user_email):
        raise HTTPException(402, "Pro feature. Subscribe at /api/create-checkout-session")
    global _value_bets_cache
    if _value_bets_cache is None or _value_bets_cache.get("min_edge") != min_edge:
        with _value_bets_lock:
            if _value_bets_cache is None or _value_bets_cache.get("min_edge") != min_edge:
                import csv as _csv
                import compute_elo as _ce
                import compute_odds as _co
                from webapp import predictor as _pred
                from datetime import date as _date
                elo, _, _ = _ce.build_elo(os.path.join(HERE, "..", "results.csv"))
                odds_map = _co.load_odds()
                ZH = ["主胜", "平", "客胜"]

                def _devig(oh, od, oa):
                    if not (oh and od and oa) or oh <= 1 or od <= 1 or oa <= 1:
                        return None
                    s = 1.0 / oh + 1.0 / od + 1.0 / oa
                    return (1.0 / oh / s, 1.0 / od / s, 1.0 / oa / s)

                played, upcoming = [], []
                today = _date.today().isoformat()
                live_p = os.path.join(HERE, "..", "wc2026_live.csv")
                sched_p = os.path.join(HERE, "..", "wc2026_schedule.csv")
                played_keys = set()
                if os.path.exists(live_p):
                    with open(live_p, newline="", encoding="utf-8") as f:
                        for r in _csv.DictReader(f):
                            try:
                                played_keys.add((r["home_team"], r["away_team"]))
                            except KeyError:
                                continue
                if os.path.exists(sched_p):
                    with open(sched_p, newline="", encoding="utf-8") as f:
                        for r in _csv.DictReader(f):
                            try:
                                hs = int(r.get("home_score") or 0)
                                as_ = int(r.get("away_score") or 0)
                            except ValueError:
                                hs = as_ = 0
                            if r["date"] >= today and hs == 0 and as_ == 0:
                                if (r["home_team"], r["away_team"]) in played_keys:
                                    continue
                                upcoming.append((r["date"], r["home_team"], r["away_team"],
                                                  r.get("stage", "")))

                all_edges, value_bets = [], []
                for date_, home, away, stage in upcoming:
                    pe = _pred.predict_one(home, away, elo, with_reflection=False)
                    if not pe:
                        continue
                    ot = _co.lookup_odds(odds_map, home, away, 2026)
                    if not ot:
                        continue
                    oh, od, oa = ot
                    pm = _devig(oh, od, oa)
                    if not pm:
                        continue
                    edges = [pe["probs"][i] - pm[i] for i in range(3)]
                    bi = max(range(3), key=lambda i: edges[i])
                    odds = [oh, od, oa][bi]
                    p = pe["probs"][bi]
                    b = odds - 1
                    kelly = max(0, (b * p - (1 - p)) / b) if b > 0 else 0
                    rec = {
                        "date": date_, "home": home, "away": away, "stage": stage,
                        "home_zh": _pred.EN_TO_ZH.get(home, home),
                        "away_zh": _pred.EN_TO_ZH.get(away, away),
                        "p_elo": list(pe["probs"]),
                        "p_mkt": list(pm),
                        "edges": edges,
                        "max_edge": max(edges),
                        "picked": ZH[bi],
                        "odds": odds,
                        "kelly": kelly,
                    }
                    all_edges.append(max(edges))
                    if max(edges) >= min_edge:
                        value_bets.append(rec)
                value_bets.sort(key=lambda x: -x["kelly"])

                dist = {}
                if all_edges:
                    dist = {
                        "n": len(all_edges),
                        "mean": sum(all_edges) / len(all_edges),
                        "median": sorted(all_edges)[len(all_edges) // 2],
                        "max": max(all_edges),
                        "min": min(all_edges),
                        "thresholds": {
                            f"{int(t*100)}%": sum(1 for e in all_edges if e >= t)
                            for t in [0.01, 0.02, 0.03, 0.05, 0.10]
                        },
                    }
                _value_bets_cache = {
                    "min_edge": min_edge,
                    "calibration_note": (
                        "Elo 已校准最优(corr +0.87 vs 市场)。"
                        "扫描出的大 edge 多在冷门队(Elo 数据稀疏),实际不可信。"
                        "价值在监控 edge 漂移,真信号会持续 ≥ 5% 在主流对阵上。"
                    ),
                    "edge_distribution": dist,
                    "upcoming": value_bets[:20],
                    "n_value_bets": len(value_bets),
                }
    return _value_bets_cache


@app.get("/api/match")
def get_match(date: str = Query(...), home: str = Query(...), away: str = Query(...),
              with_reflection: int = Query(0), with_intel: int = Query(0),
              with_h2h: int = Query(0),
              locale: str = Query("zh-CN"),
              x_user_email: str = Query(default="", alias="x_user_email")):
    """单场详情(含 top5 比分概率、Elo、预期进球、AI 反思数据、赛前情报、历史交手)。

    with_reflection=1: 从队伍档案调整 Elo 重算预测(第二步)。
    with_intel=1: 智谱 GLM-4 联网搜索赛前情报(只读,~5s)。
    with_h2h=1: 智谱 GLM-4 联网搜索历史交手(只读,~5s)。
    with_reflection/with_intel/with_h2h 为 Pro 功能,需订阅。
    locale: UI 语言(en/zh-CN/es/pt/ar/fr)。
    """
    # Pro 功能 gating
    if (with_reflection or with_intel or with_h2h) and not payments.is_paid(x_user_email):
        out = {"reflection": {"has_any": False, "summary_parts": []}}
        if with_reflection:
            out["reflection"]["error"] = "Pro feature. Subscribe to unlock AI Reflection."
        if with_intel:
            out["intel"] = {"error": "Pro feature. Subscribe to unlock pre-match intel."}
        if with_h2h:
            out["h2h"] = {"error": "Pro feature. Subscribe to unlock H2H history."}
        return out

    base = None
    for m in cache.get_schedule():
        if m["date"] == date and m["home"] == home and m["away"] == away:
            base = m
            break
    if base is None:
        raise HTTPException(status_code=404, detail=f"未找到: {date} {home} vs {away}")
    out = dict(base)
    try:
        from webapp import reflection
        out["reflection"] = reflection.get_match_reflection(home, away, locale=locale)
    except Exception as e:
        out["reflection"] = {"has_any": False, "summary_parts": [], "error": str(e)}

    # 第二步:开反思时重算预测(覆盖 Elo 相关字段)
    if with_reflection and out["reflection"].get("has_any") and payments.is_paid(x_user_email):
        try:
            from webapp import predictor
            import compute_odds
            year = int(date[:4]) if date[:4].isdigit() else None
            elo = cache.get_elo() or {}
            try:
                odds_map = compute_odds.load_odds()
            except Exception:
                odds_map = {}
            try:
                ou_map = compute_odds.load_ou()
            except Exception:
                ou_map = {}
            pred = predictor.predict_one(home, away, elo, odds_map=odds_map,
                                         year=year, ou_map=ou_map,
                                         with_reflection=True)
            if pred:
                # 覆盖预测相关字段(probs/lam/top_score/top5/mode/...)
                for k in ("probs", "top_score", "top5", "lam_h", "lam_a",
                          "host_adv", "neutral", "source", "mode",
                          "odds_1x2", "ou", "ou_line", "lam_inverted",
                          "lam_elo", "reflection_adj"):
                    if k in pred:
                        out[k] = pred[k]
        except Exception as e:
            out["reflection_error"] = str(e)

    # 第三步:开 intel 时调 web search 拉赛前情报(只读,不影响预测)
    if with_intel:
        try:
            from webapp import websearch_client
            from webapp.i18n_translator import localize_intel_cached
            raw = websearch_client.search_pre_match_briefing(
                home, away, date,
                home_zh=base.get("home_zh"),
                away_zh=base.get("away_zh"),
            )
            out["intel"] = localize_intel_cached(raw, home, away, date, locale=locale)
        except Exception as e:
            out["intel"] = {"error": str(e)}

    # 第四步:开 h2h 时调 web search 拉历史交手(只读,纯展示)
    if with_h2h:
        try:
            from webapp import websearch_client
            from webapp.i18n_translator import localize_h2h_cached
            raw = websearch_client.search_head_to_head(
                home, away,
                home_zh=base.get("home_zh"),
                away_zh=base.get("away_zh"),
            )
            out["h2h"] = localize_h2h_cached(raw, home, away, locale=locale)
        except Exception as e:
            out["h2h"] = {"error": str(e)}

    return out


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


# ── Stripe 订阅 ────────────────────────────────────────
@app.post("/api/create-checkout-session")
def create_checkout(email: str = Query(...), price_id: str = Query(...),
                     origin: str = Query(default="http://localhost:8000"),
                     locale: str = Query(default="auto", description="语言代码,如 zh-CN/es/pt/ar/fr/en")):
    """创建 Stripe Checkout Session,返回支付 URL。

    locale 映射到 Stripe 支持的 Checkout 语言,使支付页面与前端语言一致。
    """
    if not payments.STRIPE_SECRET_KEY:
        raise HTTPException(503, "Stripe not configured")
    try:
        url = payments.create_checkout_session(email, price_id, origin, locale=locale)
        return {"url": url}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request):
    """Stripe Webhook 回调(处理 checkout.session.completed 等)。"""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if not sig:
        raise HTTPException(400, "missing stripe-signature")
    return payments.handle_webhook(payload, sig)


@app.get("/api/subscription-status")
def subscription_status(email: str = Query(default="")):
    """查询邮箱的订阅状态。"""
    return {"is_paid": payments.is_paid(email), "email": email}


@app.post("/api/refresh")
def refresh(force_monte_carlo: bool = Query(False, description="是否顺带刷新夺冠榜(慢)")):
    """手动触发立即更新(唤醒后台线程,不等 15 分钟)。"""
    status = updater.trigger_now(force_monte_carlo=force_monte_carlo)
    return {"triggered": True, "status": status}


# 静态文件(CSS/JS)。index.html 由根路由单独处理。
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
