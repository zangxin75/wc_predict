"""webapp/websearch_client.py — 智谱 GLM-4 + 原生 web_search 工具。

绕过 LiteLLM proxy(LiteLLM 把 glm 走 anthropic 协议,丢失 web_search 工具),
直连智谱原生端点 open.bigmodel.cn/api/paas/v4,用现有 GLM_API_KEY。

四个场景函数:
  - search_after_match_insights: 赛后归因(替代/补充 lessons_fetcher)
  - search_pre_match_briefing:   赛前情报(伤停/近期战绩/阵容)
  - search_live_score:           实时比分 fallback(主源失败时)
  - search_head_to_head:         历史交手(LLM 兜底补全)

每个函数:
  - timeout 30s,失败返回 {"error": str}(与 lessons_fetcher 风格一致)
  - 自动从 词源效能 .env 读 GLM_API_KEY(同 LITELLM_MASTER_KEY 模式)
"""
import os
import json
import datetime
from pathlib import Path

import requests

# 智谱原生端点(不是 LiteLLM proxy)
ZHIPU_BASE = os.environ.get("ZHIPU_BASE", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
ZHIPU_MODEL = os.environ.get("ZHIPU_MODEL", "glm-4-flash")

# GLM_API_KEY 优先 env,其次从词源效能 .env 读
ENV_PATH = "/home/zx/project/词源效能/token-efficiency/.env"


def _load_key():
    """加载 GLM_API_KEY:env 优先,否则从词源效能 .env 读。"""
    key = os.environ.get("GLM_API_KEY") or os.environ.get("ZHIPU_API_KEY")
    if key:
        return key
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                if line.startswith("GLM_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return ""


def _extract_json(content):
    """从 LLM 输出提取 JSON(同 lessons_fetcher 逻辑)。"""
    if "```" in content:
        parts = content.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                content = p
                break
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        content = content[start:end + 1]
    return json.loads(content)


def _call_zhipu(prompt, max_tokens=600, temperature=0.3, enable_search=True):
    """调智谱原生端点。返回 {content, sources[]} 或 {error}。"""
    key = _load_key()
    if not key:
        return {"error": "GLM_API_KEY 未配置(env 或 词源效能/.env)"}
    tools = [{"type": "web_search", "web_search": {"enable": True, "search_result": True}}] \
        if enable_search else []
    try:
        r = requests.post(
            f"{ZHIPU_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": ZHIPU_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "tools": tools,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=30,
        )
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        content = (msg.get("content") or "").strip()
        # 智谱 web_search 结果在 message.web_search 字段(search_result=true 时返回)
        # 实测:glm-4-flash 不返回 web_search 字段(只 glm-4-plus 才返回完整 sources)
        # 但 LLM 实际看到了搜索结果(content 中能引用),靠 search_summary 字段做审计
        sources = []
        for s in (msg.get("web_search") or []):
            sources.append({
                "title": s.get("title", ""),
                "link": s.get("link", ""),
                "snippet": (s.get("content") or "")[:120],
            })
        return {"content": content, "sources": sources, "search_enabled": enable_search}
    except Exception as e:
        return {"error": str(e)}


# ──────────────────────────────────────────────────────────────────
# 场景 1:赛后归因(增强 lessons_fetcher)
# ──────────────────────────────────────────────────────────────────
PROMPT_AFTER_MATCH = """你是足球分析师。给定一场系统预测错的比赛,请使用 web_search 工具搜索赛后报道、技术统计、关键事件,然后基于真实信息分析预测为什么错。

比赛:{home} vs {away}
日期:{date}
赛事:{tournament}
系统预测:{predicted}
实际比分:{actual}

搜索建议关键词:
- "{home_zh} {away_zh} 比赛回顾 {date}"
- "{home} {away} match report"
- "世界杯 {date} 战报"

请基于搜索结果用 JSON 输出(只输出 JSON):
{{
  "causes": ["原因1(具体到事件/球员/战术/数据,引用搜索结果)", "原因2", "原因3"],
  "reusable": true 或 false,
  "confidence": "high" 或 "medium" 或 "low",
  "lesson": "一句话总结",
  "search_summary": "搜索到的关键信息一句话概括(用于审计 LLM 是否真用了搜索)"
}}

判断标准:
- causes 必须基于搜索到的真实信息,不能凭空猜测。如果搜索结果有限,减少 causes 条数。
- reusable: true = 长期特征(战术风格/抗压/主场优势),可迁移;false = 一次性事件。
- confidence: high = 多源一致,medium = 单源或部分一致,low = 搜索结果不足。
- search_summary 不能为空(若为空说明搜索失败,confidence 应为 low)。

只输出 JSON。"""


def search_after_match_insights(home, away, date, predicted, actual,
                                  tournament="2026 美加墨世界杯",
                                  home_zh=None, away_zh=None):
    """赛后归因增强:基于真实搜索信息归因。返回 dict 或 {error}。"""
    pred_str = "-".join(str(x) for x in predicted) if isinstance(predicted, (list, tuple)) else str(predicted)
    actual_str = "-".join(str(x) for x in actual) if isinstance(actual, (list, tuple)) else str(actual)
    prompt = PROMPT_AFTER_MATCH.format(
        home=home, away=away, date=date, tournament=tournament,
        predicted=pred_str, actual=actual_str,
        home_zh=home_zh or home, away_zh=away_zh or away,
    )
    res = _call_zhipu(prompt, max_tokens=700)
    if "error" in res:
        return res
    try:
        data = _extract_json(res["content"])
        data["sources"] = res["sources"]
        # web_search_used = 调用了 web_search 工具(智谱 free 版可能不返回 sources,
        # 但 LLM 实际看到了搜索结果,通过 search_summary 字段验证)
        data["web_search_used"] = bool(res.get("search_enabled"))
        data["fetched_at"] = datetime.date.today().isoformat()
        return data
    except Exception as e:
        return {"error": f"JSON 解析失败: {e}", "raw": res["content"][:200]}


# ──────────────────────────────────────────────────────────────────
# 场景 2:赛前情报(伤停/近期战绩)
# ──────────────────────────────────────────────────────────────────
PROMPT_PRE_MATCH = """你是足球情报分析师。给定一场即将开打的比赛,请使用 web_search 工具搜索以下信息:

比赛:{home} vs {away}
日期:{date}

需要搜索的关键信息:
1. 双方近期 5 场战绩(赛事级别)
2. 双方伤停情况(关键球员伤病/红牌停赛)
3. 双方近期战术风格、阵容、训练状态
4. 历史交手记录(如有)
5. 主教练近期言论、媒体关注点

搜索关键词建议:
- "{home_zh} 世界杯 2026 阵容 伤停"
- "{away_zh} recent form injuries World Cup 2026"
- "{home_zh} {away_zh} 交手 历史"

基于搜索结果用 JSON 输出(只输出 JSON):
{{
  "injuries": [
    {{"team": "{home}", "player": "球员名", "reason": "伤病/停赛原因", "impact": "high/medium/low"}},
    ...
  ],
  "form_home": {{"recent5": "WDLWW", "note": "近期状态概述"}},
  "form_away": {{"recent5": "DLLWD", "note": "..."}},
  "tactical_notes": ["双方战术要点1", "要点2"],
  "headline_concerns": ["媒体关注焦点1", "焦点2"],
  "prediction_hint": "对胜负影响的一句话判断(如'主队伤病多客队占优')",
  "confidence": "high/medium/low",
  "search_summary": "搜索信息一句话概括"
}}

要求:
- injuries 必须基于搜索结果,没有明确信息就留空数组
- form 的 W/D/L 必须是真实近期战绩,不能猜
- confidence: high = 找到详细情报,low = 搜索信息有限

只输出 JSON。"""


def search_pre_match_briefing(home, away, date,
                                home_zh=None, away_zh=None):
    """赛前情报:伤停/战绩/战术。返回 dict 或 {error}。"""
    prompt = PROMPT_PRE_MATCH.format(
        home=home, away=away, date=date,
        home_zh=home_zh or home, away_zh=away_zh or away,
    )
    res = _call_zhipu(prompt, max_tokens=900)
    if "error" in res:
        return res
    try:
        data = _extract_json(res["content"])
        data["sources"] = res["sources"]
        data["web_search_used"] = bool(res.get("search_enabled"))
        data["fetched_at"] = datetime.date.today().isoformat()
        return data
    except Exception as e:
        return {"error": f"JSON 解析失败: {e}", "raw": res["content"][:200]}


# ──────────────────────────────────────────────────────────────────
# 场景 3:实时比分 fallback(骨架,Phase 2 接入)
# ──────────────────────────────────────────────────────────────────
PROMPT_LIVE_SCORE = """使用 web_search 工具查询以下比赛的最终比分(或当前进行状态):

{home} vs {away}
日期:{date}
赛事:2026 世界杯

搜索关键词:"{home_zh} {away_zh} 比分 {date}" 或 "World Cup {date} {home} {away} score"

只输出 JSON:
{{
  "score": [主队进球数, 客队进球数],
  "status": "finished" 或 "ongoing" 或 "not_started",
  "minute": "当前分钟(ongoing 时填,如 67',finished 填 90+)",
  "key_events": ["进球/红牌等关键事件"],
  "confidence": "high/medium/low"
}}

只输出 JSON。"""


def search_live_score(home, away, date, home_zh=None, away_zh=None):
    """实时比分 fallback。返回 dict 或 {error}。

    注意:LLM 搜索结果可能延迟或不准,仅作 ESPN 主源失败时的兜底。
    """
    prompt = PROMPT_LIVE_SCORE.format(
        home=home, away=away, date=date,
        home_zh=home_zh or home, away_zh=away_zh or away,
    )
    res = _call_zhipu(prompt, max_tokens=400)
    if "error" in res:
        return res
    try:
        data = _extract_json(res["content"])
        data["sources"] = res["sources"]
        return data
    except Exception as e:
        return {"error": f"JSON 解析失败: {e}", "raw": res["content"][:200]}


# ──────────────────────────────────────────────────────────────────
# 场景 4:历史交手(骨架,Phase 2 接入)
# ──────────────────────────────────────────────────────────────────
PROMPT_H2H = """使用 web_search 工具查询两支球队的历史交手记录。

{home} vs {away}

搜索关键词:"{home_zh} {away_zh} 交手历史" 或 "{home} vs {away} head to head"

只输出 JSON:
{{
  "matches": [
    {{"date": "YYYY-MM-DD", "competition": "赛事名", "score": [主队进球数, 客队进球数], "venue": "主场队名/中立"}},
    ...
  ],
  "summary": "一句话总战绩(如'近 10 次交锋主队 5 胜 3 平 2 负')",
  "recent_trend": "近期趋势一句话",
  "confidence": "high/medium/low"
}}

要求:
- score 必须是 JSON 数组(如 [2, 1]),不是字符串
- 列出最近 5-10 次交手,按日期倒序

只输出 JSON。"""


def search_head_to_head(home, away, home_zh=None, away_zh=None):
    """历史交手 LLM 兜底。返回 dict 或 {error}。"""
    prompt = PROMPT_H2H.format(
        home=home, away=away,
        home_zh=home_zh or home, away_zh=away_zh or away,
    )
    res = _call_zhipu(prompt, max_tokens=600)
    if "error" in res:
        return res
    try:
        data = _extract_json(res["content"])
        data["sources"] = res["sources"]
        return data
    except Exception as e:
        return {"error": f"JSON 解析失败: {e}", "raw": res["content"][:200]}
