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

from fastapi import FastAPI, HTTPException, Query, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.sessions import SessionMiddleware

from webapp import auth, cache, updater, payments
from webapp.middleware import SecurityHeadersMiddleware
from webapp.rate_limit import limiter

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

# F-06: slowapi 限流。limiter 必须挂在 app.state;exception_handler 转 429。
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS 白名单(F-01 修复:从 * 改为显式域名,防止跨站 API 调用)
# 生产域名 aijialidu.ticp.vip(主域 + www 子域 + HTTPS/HTTP 用于 nginx 重定向场景)
# 开发:localhost:8000(本机前端)、127.0.0.1:8000
ALLOWED_ORIGINS = [
    "https://aijialidu.ticp.vip",
    "http://aijialidu.ticp.vip",
    "https://www.aijialidu.ticp.vip",
    "http://www.aijialidu.ticp.vip",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    allow_credentials=True,  # 允许 session cookie 跨域(配合 P0-3 session 鉴权)
)

# Session 中间件 (F-02 修复:替代 x_user_email 查询参数鉴权)
# 注意:必须在 CORSMiddleware 之后 add, 但实际 Starlette 中间件按 LIFO 处理
# (后加的先执行),所以最后 add 的最外层。Request 先过 Session 再到 CORS,
# Set-Cookie 在 response 阶段正确生成。
app.add_middleware(
    SessionMiddleware,
    secret_key=auth.SESSION_SECRET,
    session_cookie=auth.COOKIE_NAME,
    max_age=auth.SESSION_MAX_AGE,
    same_site="lax",     # 防 CSRF 跨站 GET
    https_only=False,    # dev 允许 http;生产由 nginx 终止 TLS
    path="/",            # 整站带 cookie
)

# 安全响应头 (F-09)。Starlette 中间件按 LIFO 执行,SessionMiddleware 已是最外层,
# 这里再加一层 SecurityHeadersMiddleware 套在外面 → 所有响应都带 CSP/X-Frame 等。
app.add_middleware(SecurityHeadersMiddleware)

# F-06: slowapi 中间件(LIFO 最外层)→ 配合装饰器 @limiter.limit() 工作。
app.add_middleware(SlowAPIMiddleware)


# ── 页面 ──────────────────────────────────────────────────────────
@app.get("/")
@limiter.limit("60/minute")
def index(request: Request):
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ── API ───────────────────────────────────────────────────────────
@app.get("/api/config")
@limiter.limit("60/minute")
def get_config(request: Request):
    """前端配置(轮询间隔等)。"""
    return {"poll_sec": POLL_SEC}


@app.get("/api/status")
@limiter.limit("60/minute")
def get_status(request: Request):
    """后台线程状态:上次更新/成功否/下次预计/新增场次。"""
    return cache.get_status()


@app.get("/api/schedule")
@limiter.limit("60/minute")
def get_schedule(request: Request, stage: str | None = Query(None, description="按阶段过滤:Group/R32/R16/QF/SF/Final")):
    """全部赛程富化视图。每场含 status/score/probs/top_score/group/stage/city。

    可选 ?stage=Group 只看小组赛。
    """
    sched = cache.get_schedule()
    if stage:
        sched = [m for m in sched if m.get("stage") == stage]
    return {"count": len(sched), "matches": sched}


@app.get("/api/groups")
@limiter.limit("60/minute")
def get_groups(request: Request):
    """12 组积分榜:已打真实积分 + 未打预测期望积分。"""
    return cache.get_groups()


@app.get("/api/champions")
@limiter.limit("60/minute")
def get_champions(request: Request, top: int = Query(15, ge=1, le=48)):
    """夺冠概率榜(蒙特卡洛)。?top=N 限制返回条数。"""
    champs = cache.get_champions()
    return {"count": len(champs), "built_at": cache.get_status().get("champions_built_at"),
            "ranking": champs[:top]}


@app.get("/api/market")
@limiter.limit("60/minute")
def get_market(request: Request):
    """数据来源面板:每场预测的数据源(市场反演λ模式A/B vs Elo)+ 反演λ对比。

    优先返回 cache 预热结果(后台 updater 每 15 分钟重建一次 + 冷启动时同步预热);
    仅在 cache 为空时兜底同步计算一次(用 cache.get_elo() 复用现成 Elo,不再 rebuild),
    并回写 cache 以避免并发请求各自重算。
    """
    m = cache.get_market()
    if m is not None:
        return m
    # 兜底路径:cache 未预热(罕见,例如 cache.json 损坏)
    elo = cache.get_elo() or None
    from webapp import predictor
    m = predictor.market_coverage(elo=elo)
    cache.atomic_replace(market=m)
    return m


_value_bets_cache = None
_value_bets_lock = threading.Lock()


@app.get("/api/value-bets")
@limiter.limit("30/minute")
def get_value_bets(request: Request,
                    min_edge: float = Query(0.03, ge=0.0, le=0.5,
                                            description="最小 edge 阈值(默认 3%%)")):
    """价值下注面板(纯 Elo vs 市场 devig)— B 方案。Pro 功能,需订阅。

    返回:
      - edge_distribution: max edge 分布统计(平均/分位/各阈值场数)
      - upcoming: 未赛价值机会 Top 20(按 Kelly 降序),含 Elo/Mkt 三态 + 推荐下注
      - calibration_note: 已知结论(Elo 已被最优校准,与市场 corr +0.87)

    静态(随 odds_wc.csv 变),首次请求计算后内存缓存。如需重算可重启服务。
    """
    user = auth.get_current_user(request)
    if not payments.is_paid(user):
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
@limiter.limit("30/minute")
def get_match(request: Request,
              date: str = Query(...), home: str = Query(...), away: str = Query(...),
              with_reflection: int = Query(0), with_intel: int = Query(0),
              with_h2h: int = Query(0),
              locale: str = Query("zh-CN")):
    """单场详情(含 top5 比分概率、Elo、预期进球、AI 反思数据、赛前情报、历史交手)。

    with_reflection=1: 从队伍档案调整 Elo 重算预测(第二步)。
    with_intel=1: 智谱 GLM-4 联网搜索赛前情报(只读,~5s)。
    with_h2h=1: 智谱 GLM-4 联网搜索历史交手(只读,~5s)。
    with_reflection/with_intel/with_h2h 为 Pro 功能,需订阅。
    locale: UI 语言(en/zh-CN/es/pt/ar/fr)。
    """
    # Pro 功能 gating(已结束比赛免费)
    user = auth.get_current_user(request)
    base = None
    for m in cache.get_schedule():
        if m["date"] == date and m["home"] == home and m["away"] == away:
            base = m
            break
    is_finished = base is not None and base.get("status") == "finished"
    if (with_reflection or with_intel or with_h2h) and not payments.is_paid(user) and not is_finished:
        out = {"reflection": {"has_any": False, "summary_parts": []}}
        if with_reflection:
            out["reflection"]["error"] = "Pro feature. Subscribe to unlock AI Reflection."
        if with_intel:
            out["intel"] = {"error": "Pro feature. Subscribe to unlock pre-match intel."}
        if with_h2h:
            out["h2h"] = {"error": "Pro feature. Subscribe to unlock H2H history."}
        return out

    if base is None:
        raise HTTPException(status_code=404, detail=f"未找到: {date} {home} vs {away}")
    out = dict(base)
    try:
        from webapp import reflection
        out["reflection"] = reflection.get_match_reflection(home, away, locale=locale)
    except Exception as e:
        out["reflection"] = {"has_any": False, "summary_parts": [], "error": str(e)}

    # 第二步:开反思时重算预测(覆盖 Elo 相关字段)
    if with_reflection and out["reflection"].get("has_any") and payments.is_paid(user):
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
@limiter.limit("30/minute")
def get_backtest(request: Request):
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
@limiter.limit("10/minute")
def create_checkout(request: Request, email: str = Query(...), price_id: str = Query(...),
                     origin: str = Query(default="http://localhost:8000"),
                     locale: str = Query(default="auto", description="语言代码,如 zh-CN/es/pt/ar/fr/en"),
                     remaining_count: int = Query(default=0, ge=0, description="按场次计费:当前剩余场次")):
    """创建 Stripe Checkout Session,返回支付 URL。

    locale 映射到 Stripe 支持的 Checkout 语言,使支付页面与前端语言一致。
    remaining_count > 0 时,price_tournament 走 price_data 内嵌动态定价(按场次)。
    """
    if not payments.STRIPE_SECRET_KEY:
        raise HTTPException(503, "Stripe not configured")
    try:
        url = payments.create_checkout_session(
            email, price_id, origin, locale=locale,
            remaining_count=remaining_count if remaining_count > 0 else None,
        )
        return {"url": url}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/pricing")
@limiter.limit("60/minute")
def get_pricing(request: Request, locale: str = Query(default="en", description="UI 语言,如 zh-CN/en/es")):
    """按场次计费实时报价:总场次 / 剩余场次 / 单价 / 当前价 / 全程原价 / 节省。

    剩余场次定义:cache.schedule 中 status != 'finished' 的全部比赛(含今天进行中 + 未开赛)。
    中文区返回 CNY (¥2/场),其他返回 USD ($0.28/场,按 0.14 汇率折算)。
    同时返回 priced_at + pricing_locked_until + upcoming_sample,前端用于防纠纷对账。
    """
    import datetime as _dt
    sched = cache.get_schedule()
    total = len(sched)
    remaining_matches = [m for m in sched if m.get("status") != "finished"]
    remaining = len(remaining_matches)
    unit, currency = payments.unit_price_for_locale(locale)
    symbol = payments.currency_symbol(currency)
    full_price = round(total * unit, 2)
    current_price = round(remaining * unit, 2)
    savings_pct = round((1 - remaining / total) * 100, 1) if total else 0.0
    priced_at = _dt.datetime.now(_dt.timezone.utc)
    locked_until = priced_at + _dt.timedelta(minutes=10)
    # 列出接下来 20 场(按日期排序),让用户能看到付费包含哪些比赛
    upcoming_sorted = sorted(remaining_matches, key=lambda m: (m.get("date", ""), m.get("kickoff_bj", "")))
    upcoming_sample = [
        {
            "date": m.get("date"),
            "home": m.get("home"),
            "away": m.get("away"),
            "home_zh": m.get("home_zh"),
            "away_zh": m.get("away_zh"),
            "stage": m.get("stage"),
            "kickoff_bj": m.get("kickoff_bj"),
        }
        for m in upcoming_sorted[:20]
    ]
    return {
        "unit_price": unit,
        "currency": currency,
        "currency_symbol": symbol,
        "total_matches": total,
        "remaining_matches": remaining,
        "full_price": full_price,
        "current_price": current_price,
        "savings_pct": savings_pct,
        "saved_amount": round(full_price - current_price, 2),
        "priced_at": priced_at.isoformat(),
        "pricing_locked_until": locked_until.isoformat(),
        "count_basis": "matches with status != 'finished' (includes today in-progress and future)",
        "upcoming_sample": upcoming_sample,
    }


@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request):  # 不限速 — Stripe 内部回调
    """Stripe Webhook 回调(处理 checkout.session.completed 等)。"""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if not sig:
        raise HTTPException(400, "missing stripe-signature")
    return payments.handle_webhook(payload, sig)


@app.get("/api/subscription-status")
@limiter.limit("30/minute")
def subscription_status(request: Request):
    """查询当前 session 用户的订阅状态(优先从 session 读 email, 兼容 x_user_email)。"""
    user = auth.get_current_user(request)
    if not user:
        return {"is_paid": False, "email": ""}
    return {"is_paid": payments.is_paid(user), "email": user}


# ── Session 鉴权端点 (F-02 修复) ────────────────────────────
@app.get("/api/whoami")
@limiter.limit("60/minute")
def whoami(request: Request):
    """返回当前 session 用户信息。前端用于判断登录态 + 拉取订阅状态。"""
    user = auth.get_current_user(request)
    return {
        "email": user or "",
        "is_authenticated": bool(user),
        "is_paid": payments.is_paid(user) if user else False,
    }


@app.post("/api/claim-session")
@limiter.limit("10/minute")
def claim_session(request: Request, session_id: str = Query(..., description="Stripe checkout session id (cs_xxx)")):
    """Stripe 支付成功回跳后,前端用 session_id 兑换 session cookie。

    安全:服务端用 STRIPE_SECRET_KEY 查 Stripe Session, 拿到 customer_email,
    只有这个 email 才能开 session。攻击者无法伪造其他人的 email。
    """
    if not payments.STRIPE_SECRET_KEY:
        raise HTTPException(503, "Stripe not configured")
    try:
        import stripe as _stripe
        sess = _stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        raise HTTPException(400, f"invalid session_id: {e}")

    # 从 Stripe session 提取 email (customer_email 优先, 其次 metadata.email)
    email = (
        (sess.get("customer_email") or "").strip()
        or (sess.get("metadata", {}) or {}).get("email", "").strip()
    )
    if not email:
        raise HTTPException(400, "no email associated with this checkout session")
    email = email.lower()

    # 验证支付状态(必须 paid 才发 session)
    if sess.get("payment_status") not in ("paid", "no_payment_required"):
        # 订阅模式可能 immediate 时 payment_status 还是 unpaid, 看 mode + status
        mode = sess.get("mode", "payment")
        status = sess.get("status", "")
        if mode == "subscription" and status in ("complete", "trialing"):
            pass
        else:
            raise HTTPException(402, f"checkout not paid (status={status}, payment_status={sess.get('payment_status')})")

    # 写 session, Set-Cookie 由 SessionMiddleware 下发
    auth.set_session_user(request, email)
    return {"ok": True, "email": email, "is_paid": payments.is_paid(email)}


@app.post("/api/logout")
@limiter.limit("30/minute")
def logout(request: Request):
    """清空 session(登出)。"""
    auth.clear_session(request)
    return {"ok": True}


# ── 兼容旧版 x_user_email 鉴权: 接收到合法 cookie 时才放行 ──
# (P0-3 阶段) — 前端全部迁移到 cookie 后, 此 fallback 可删除。
# 现在的实现:get_current_user 优先 cookie, 其次 x_user_email(打 deprecation 日志)


@app.post("/api/refresh")
@limiter.limit("5/minute")
def refresh(request: Request, force_monte_carlo: bool = Query(False, description="是否顺带刷新夺冠榜(慢)")):
    """手动触发立即更新(唤醒后台线程,不等 15 分钟)。"""
    status = updater.trigger_now(force_monte_carlo=force_monte_carlo)
    return {"triggered": True, "status": status}


@app.post("/api/redeem-invite")
@limiter.limit("10/minute")
def redeem_invite(request: Request,
                   email: str = Query(..., description="用户邮箱"),
                   code: str = Query(..., description="邀请码,格式 INVITE-XXXX-XXXX")):
    """一次性邀请码解锁 Pro 权限 + 写入 session。

    成功:{ok:true, is_paid:true, email, plan:"invite"} → 前端刷新 Pro 状态 + Set-Cookie。
    失败:400 + {ok:false, error: invalid_email|not_found|already_used}。
    """
    res = payments.redeem_invite_code(email, code)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "redeem_failed"))
    # F-02: 写入 session, Set-Cookie 由 SessionMiddleware 下发
    auth.set_session_user(request, res["email"])
    return {"ok": True, "is_paid": True,
            "email": res["email"], "plan": res.get("plan", "invite")}


# ── 分享邀请码(社交媒体推广) ──────────────────────────────
# F-06: 旧 _share_rate_check 已删除,限流统一由 slowapi @limiter.limit 处理。
#      单 IP 60s 内最多 10 次(原 6 次放宽,旧 share-verifier 自身有外网 5s 延迟)。


@app.post("/api/share/verify")
@limiter.limit("10/minute")
def share_verify(request: Request,
                 platform: str = Query(...),
                 url: str = Query(...),
                 email: str = Query(...)):
    """验证社交平台分享链接 → 返回 24h 邀请码。

    Phase 1 仅支持 x(twitter.com / x.com)。
    同 (platform, email) 24h 内只发 1 个有效码(reused:true 表示返回旧码)。
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="invalid_email")

    from webapp import share_verifier
    try:
        meta = share_verifier.verify_share_url(url, platform=platform)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    source_meta = {
        "tweet_url": url,
        "handle": meta.get("handle"),
        "tweet_id": meta.get("tweet_id"),
        "author_name": meta.get("author_name"),
        "author_url": meta.get("author_url"),
    }
    res = payments.generate_share_code(platform, email, source_meta)
    return {
        "ok": True,
        "platform": meta["platform"],
        "code": res["code"],
        "expires_at": res["expires_at"],
        "reused": res["reused"],
        "status": res["status"],
    }


@app.post("/api/share/redeem")
@limiter.limit("10/minute")
def share_redeem(request: Request, email: str = Query(...),
                 code: str = Query(...)):
    """分享邀请码 redeem → 24h Pro。

    错误码 ∈ invalid_email|not_found|already_used|expired。
    """
    res = payments.redeem_share_code(email, code)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "redeem_failed"))
    return {
        "ok": True,
        "is_paid": True,
        "email": res["email"],
        "plan": res.get("plan", "share"),
        "pro_expires_at": res.get("pro_expires_at"),
    }


# 静态文件(CSS/JS)。index.html 由根路由单独处理。
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
