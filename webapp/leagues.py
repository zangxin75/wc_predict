# webapp/leagues.py
"""联赛板块 API 路由。付费门控与 WC 对齐(M-3/N-1):

  · /matches:非 Pro → odds 字段=null;响应只含 MATCH_PUBLIC_FIELDS 白名单,
    edge/kelly/market 键绝不出现(不是置空,是键不存在)
  · /value:非 Pro → HTTPException(402)(WC value-bets 同款)
  · 全部端点 @limiter.limit("30/minute"),limiter 用 webapp.rate_limit 共享单例
    (挂在 app.state 的全局限流器,装饰器抛出 RateLimitExceeded 由 app 统一转 429)
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from leagues import config
from webapp import auth, leagues_cache, payments
from webapp.rate_limit import limiter

router = APIRouter(prefix="/api/leagues", tags=["leagues"])

VALID_CODES = set(config.ALL_CODES)

# 免费用户可见字段(预测本身免费;odds 键保留但值=null,前端渲染逻辑统一)
MATCH_PUBLIC_FIELDS = {"date", "time", "home", "away", "status", "neutral", "gh", "ga",
                       "probs", "lam_h", "lam_a", "top_scores", "ou25_over", "btts_yes",
                       "low_confidence", "odds"}
# Pro 追加 market 子 dict(edge/kelly 只存在于 market 内部,白名单外的键一律剥掉)
MATCH_PRO_FIELDS = MATCH_PUBLIC_FIELDS | {"market"}

VALUE_MIN_EDGE = 0.03


def _is_pro(user):
    return bool(user and payments.is_paid(user))


def _public_match(m, pro):
    out = {k: v for k, v in m.items() if k in (MATCH_PRO_FIELDS if pro else MATCH_PUBLIC_FIELDS)}
    if not pro:
        out["odds"] = None
    return out


def _get_entry(code):
    code = code.upper()
    if code not in VALID_CODES:
        raise HTTPException(404, f"unknown league: {code}")
    entry = leagues_cache.get_league(code)
    if not entry or not entry.get("predictions"):
        raise HTTPException(503, "league data not ready (first build in progress)")
    return code, entry


@router.get("")
@limiter.limit("30/minute")
def list_leagues(request: Request):
    available = set(leagues_cache.get_codes())
    return {"leagues": [{"code": c, "name": config.LEAGUES[c]["name"],
                         "name_zh": config.LEAGUES[c]["name_zh"],
                         "available": c in available} for c in config.ALL_CODES],
            "status": leagues_cache.get_status()}


@router.get("/{code}/matches")
@limiter.limit("30/minute")
def league_matches(code: str, request: Request, user=Depends(auth.get_current_user)):
    code, entry = _get_entry(code)
    pro = _is_pro(user)
    p = entry["predictions"]
    return {"code": code, "season": p.get("season"), "generated_at": p.get("generated_at"),
            "matches": [_public_match(m, pro) for m in p.get("matches", [])]}


@router.get("/{code}/standings")
@limiter.limit("30/minute")
def league_standings(code: str, request: Request):
    code, entry = _get_entry(code)
    if entry.get("standings") is None:
        raise HTTPException(503, "standings not ready")
    return {"code": code, "standings": entry["standings"]}


@router.get("/{code}/sim")
@limiter.limit("30/minute")
def league_sim(code: str, request: Request):
    code, entry = _get_entry(code)
    if entry.get("sim") is None:
        raise HTTPException(503, "sim not ready")
    return {"code": code, "sim": entry["sim"]}


@router.get("/{code}/value")
@limiter.limit("30/minute")
def league_value(code: str, request: Request, user=Depends(auth.get_current_user)):
    if not _is_pro(user):
        raise HTTPException(402, "Pro feature. Subscribe at /api/create-checkout-session")
    code, entry = _get_entry(code)
    values = []
    for m in entry["predictions"].get("matches", []):
        mk = m.get("market")
        if mk and mk.get("max_edge", 0) > VALUE_MIN_EDGE:
            values.append({"date": m["date"], "time": m.get("time", ""),
                           "home": m["home"], "away": m["away"], **mk})
    values.sort(key=lambda v: -v["max_edge"])
    return {"code": code, "values": values}
