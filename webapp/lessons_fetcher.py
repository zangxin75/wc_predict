"""webapp/lessons_fetcher.py — LLM 自动错场归因 + 队伍档案写入(第三步)。

流程:
  fetch_lesson_for_match(home, away, date, predicted, actual, tournament)
    → 调 LiteLLM proxy(glm-4-flash)
    → LLM 基于训练知识分析错场原因(2022 等历史大赛 LLM 已充分学习)
    → 返回 {causes[], reusable, confidence, lesson}

  update_team_yaml(team, lesson, ...)
    → 读 teams/{team}.yaml(不存在则创建)
    → append recent_lessons(自动备份原文件)
    → 写回(保持中文不转义)

LLM 配置(环境变量):
  LLM_BASE     默认 http://localhost:8080/v1(词源效能 LiteLLM proxy)
  LLM_KEY      从 .env 读 LITELLM_MASTER_KEY
  LLM_MODEL    默认 glm-4-flash(便宜)

注意:
  - 当前 LLM 基于训练知识,不调实时 web search。对 2026 新场无训练数据,会失效。
  - 后续可扩展:接 GLM-4 web_search 工具,实时抓赛后报道。
"""
import os
import json
import datetime
from pathlib import Path

import requests
import yaml

HERE = Path(__file__).resolve().parent.parent
TEAMS_DIR = HERE / "teams"

# web_search 路径下 LLM 来源标签(写入 yaml.llm_source 供前端识别)
_ZHIPU_MODEL_TAG = "glm-4-flash"

LLM_BASE = os.environ.get("LLM_BASE", "http://localhost:8080/v1").rstrip("/")
LLM_KEY = os.environ.get("LLM_KEY") or os.environ.get("LITELLM_MASTER_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "glm-4-flash")

PROMPT_TEMPLATE = """你是足球比赛数据分析师。给定一场系统预测错的比赛,请基于你的足球知识分析预测为什么会错。

比赛:{home} vs {away}
日期:{date}
赛事:{tournament}
系统预测比分:{predicted}
实际比分:{actual}

请用 JSON 输出(只输出 JSON,不要 markdown 代码块包裹,不要解释文字):
{{
  "causes": ["原因1(具体到事件/球员/战术)", "原因2", "原因3"],
  "reusable": true 或 false,
  "confidence": "high" 或 "medium" 或 "low",
  "lesson": "一句话总结,可写入该队档案"
}}

判断标准:
- causes:具体原因(伤病/红牌/天气/战术压制/心理/裁判/具体球员表现等)
- reusable:
  * true = 长期特征(战术风格/抗压能力/主场优势/对某地区球队战绩)→ 可迁移到该队未来比赛
  * false = 一次性事件(具体红牌/具体伤病/单场裁判误判)→ 不可迁移
- confidence:这个归因的可信度
- lesson:一句话,如"摩洛哥高压逼抢战术在 60 分钟后体能断崖"

只输出 JSON,不要任何其他文字。"""


def fetch_lesson_for_match(home, away, date, predicted, actual, tournament="2022 卡塔尔世界杯",
                            use_web_search=False, home_zh=None, away_zh=None):
    """调 LLM 分析一场错场。返回 dict 或 {error: msg}。

    use_web_search=True 时改用智谱原生 endpoint + web_search 工具,
    LLM 基于真实搜索结果归因(对 2026 新场质量更好,慢 3-5s)。
    """
    if use_web_search:
        from webapp import websearch_client
        return websearch_client.search_after_match_insights(
            home, away, date, predicted, actual, tournament=tournament,
            home_zh=home_zh, away_zh=away_zh,
        )
    predicted_str = "-".join(str(x) for x in predicted) if isinstance(predicted, (list, tuple)) else str(predicted)
    actual_str = "-".join(str(x) for x in actual) if isinstance(actual, (list, tuple)) else str(actual)
    prompt = PROMPT_TEMPLATE.format(
        home=home, away=away, date=date, tournament=tournament,
        predicted=predicted_str, actual=actual_str,
    )
    try:
        r = requests.post(
            f"{LLM_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"},
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 400,
                "temperature": 0.3,
            },
            timeout=30,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"].strip()
        # 提取 JSON(LLM 可能包在 ```json 里或带额外文字)
        if "```" in content:
            parts = content.split("```")
            for p in parts:
                p = p.strip()
                if p.startswith("json"):
                    p = p[4:].strip()
                if p.startswith("{"):
                    content = p
                    break
        # 找到第一个 { 到最后一个 }
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            content = content[start:end + 1]
        result = json.loads(content)
        result["fetched_at"] = datetime.date.today().isoformat()
        return result
    except Exception as e:
        return {"error": str(e)}


def update_team_yaml(team, lesson, date, vs, predicted, actual,
                    tournament="2022 卡塔尔世界杯", miss_type="result"):
    """读 teams/{team}.yaml,append recent_lessons,写回。首次创建时填空骨架。

    自动备份:.yaml → .yaml.bak(仅首次创建备份,避免覆盖已有备份)。
    """
    TEAMS_DIR.mkdir(parents=True, exist_ok=True)
    path = TEAMS_DIR / f"{team}.yaml"
    today = datetime.date.today().isoformat()

    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        backup = path.with_suffix(".yaml.bak")
        if not backup.exists():
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        data = {
            "name": team,
            "name_zh": team,
            "updated": today,
            "elo_adjustments": [],
            "recent_lessons": [],
        }

    data.setdefault("recent_lessons", []).append({
        "date": date,
        "tournament": tournament,
        "vs": vs,
        "venue": "neutral",
        "prediction": "-".join(str(x) for x in predicted) if isinstance(predicted, (list, tuple)) else str(predicted),
        "actual": "-".join(str(x) for x in actual) if isinstance(actual, (list, tuple)) else str(actual),
        "miss_type": miss_type,
        "causes": lesson.get("causes", []),
        "reusable": lesson.get("reusable", False),
        "llm_source": (f"{_ZHIPU_MODEL_TAG}+web_search auto {today}"
                       if lesson.get("web_search_used")
                       else f"{LLM_MODEL} auto {today}"),
        "web_search_used": bool(lesson.get("web_search_used", False)),
        "sources": lesson.get("sources") or [],
        "search_summary": lesson.get("search_summary"),
    })
    data["updated"] = today

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    return path
