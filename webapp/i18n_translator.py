"""webapp/i18n_translator.py — 共享翻译层,把中文 LLM 散文翻成 6 语言版本。

核心函数:
  translate_zh_to(text, locale) -> str
  translate_batch_zh_to(texts, locale) -> list[str]
  translate_object(obj, locale, namespace='') -> obj   (递归翻译 dict/list 里的中文字符串值)

复用 websearch_client._call_zhipu(enable_search=False) 直连智谱 GLM-4。

缓存:
  - 内存 dict:{(text_hash, locale): translation}
  - 磁盘:data/i18n_cache/{namespace}__{locale}.json,key=text_hash,value=translation

缓存键用 SHA1(text).hexdigest()[:16] —— 同一段中文 + 同一 locale 永不重复调 API。
"""
import os
import re
import json
import time
import hashlib
from pathlib import Path

# 用相对导入,避开 websearch_client 的 web_search 工具调用,只复用 _call_zhipu
from webapp.websearch_client import _call_zhipu, _sanitize_str

# ── 配置 ──────────────────────────────────────────────
SUPPORTED_LOCALES = ["en", "zh-CN", "es", "pt", "ar", "fr"]
LOCALE_NAMES = {
    "en": "English",
    "zh-CN": "Simplified Chinese",
    "es": "Spanish",
    "pt": "Portuguese",
    "ar": "Arabic",
    "fr": "French",
}

# data 目录在仓库根下,放缓存
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
CACHE_DIR = PROJECT_ROOT / "data" / "i18n_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 进程内内存缓存
_MEM_CACHE: dict = {}

# 需要跳过的字段(非中文,或结构化)
_SKIP_KEYS = {
    "team", "name", "name_zh", "vs", "home", "away",
    "date", "updated", "fetched_at", "confidence",
    "score", "recent5", "impact", "venue", "reusable",
    "miss_type", "llm_source", "prediction", "actual",
    "web_search_used", "key_events", "minute", "status",
}


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _is_chinese(text: str) -> bool:
    """粗判:含中文字符 → 需要翻译;否则跳过。"""
    if not text:
        return False
    for ch in text:
        if "一" <= ch <= "鿿":
            return True
    return False


def _cache_path(namespace: str, locale: str) -> Path:
    safe_ns = re.sub(r"[^a-zA-Z0-9_-]", "_", namespace) or "_default"
    return CACHE_DIR / f"{safe_ns}__{locale}.json"


def _load_disk_cache(namespace: str, locale: str) -> dict:
    """惰性加载磁盘缓存到内存(同一进程同一 namespace+locale 只读一次)。"""
    key = f"{namespace}::{locale}"
    if key in _MEM_CACHE:
        return _MEM_CACHE[key]
    path = _cache_path(namespace, locale)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                _MEM_CACHE[key] = json.load(f)
            return _MEM_CACHE[key]
        except (OSError, json.JSONDecodeError):
            pass
    _MEM_CACHE[key] = {}
    return _MEM_CACHE[key]


def _save_disk_cache(namespace: str, locale: str):
    """把内存缓存写回磁盘。"""
    key = f"{namespace}::{locale}"
    data = _MEM_CACHE.get(key, {})
    if not data:
        return
    path = _cache_path(namespace, locale)
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.replace(path)
    except OSError as e:
        print(f"⚠ 写缓存失败 {path}: {e}")


# ── 单条翻译 ──────────────────────────────────────────
_PROMPT_SINGLE = """Translate the following football analysis from Chinese to {locale_name}.
Preserve proper nouns (player names, team names, tournament names) verbatim.
Output ONLY the translation, no quotes, no preamble, no explanation.

Chinese:
{text}"""


def _call_translate(prompt: str, max_tokens: int, timeout: int = 90, retries: int = 2) -> dict:
    """翻译专用调用:更长超时 + 简单重试。"""
    for attempt in range(retries + 1):
        res = _call_zhipu(prompt, max_tokens=max_tokens, temperature=0.2,
                           enable_search=False, timeout=timeout)
        if "error" not in res and res.get("content"):
            return res
        if attempt < retries:
            time.sleep(2.0 * (attempt + 1))
    return res


def translate_zh_to(text: str, locale: str, namespace: str = "default") -> str:
    """单条翻译。zh-CN 或非中文文本直接返回原文。失败 fallback 到原文。"""
    if not text or locale == "zh-CN" or not _is_chinese(text):
        return text

    cache = _load_disk_cache(namespace, locale)
    h = _hash(text)
    if h in cache:
        return cache[h]

    locale_name = LOCALE_NAMES.get(locale, locale)
    # F-05: 用 <input> 包裹文本,提示模型仅作为翻译源;限长 + 控制字符过滤。
    prompt = _PROMPT_SINGLE.format(
        locale_name=locale_name,
        text=f"<input>\n{_sanitize_str(text, max_len=1500)}\n</input>",
    )
    res = _call_translate(prompt, max_tokens=400)
    if "error" in res or not res.get("content"):
        return text
    out = res["content"].strip()
    # 去掉可能的引号包裹
    if out.startswith('"') and out.endswith('"'):
        out = out[1:-1]
    cache[h] = out
    _save_disk_cache(namespace, locale)
    return out


# ── 批量翻译 ──────────────────────────────────────────
_PROMPT_BATCH = """Translate the following numbered football analysis sentences from Chinese to {locale_name}.
Preserve proper nouns (player names, team names, tournament names) verbatim.
Output each translation on its own line, prefixed with the same number, nothing else.

Example input:
1. 巴西队防守稳固
2. 阿根廷前锋状态火热

Example output (English):
1. Brazil's defense is solid
2. Argentina's striker is in hot form

Now translate:

{lines}"""


def translate_batch_zh_to(texts: list, locale: str, namespace: str = "default") -> list:
    """批量翻译:输入 list[str],输出 list[str]。空字符串/非中文 保留原文。
    一次 GLM 调用翻一批(默认 ~25 条),显著快于逐条调用。
    """
    if not texts:
        return []
    if locale == "zh-CN":
        return list(texts)

    # 先过缓存:已翻译的直接取,未翻译的拼成一次 GLM 调用
    cache = _load_disk_cache(namespace, locale)
    out = [None] * len(texts)
    pending_idx = []
    pending_texts = []
    for i, t in enumerate(texts):
        if not t or not _is_chinese(t):
            out[i] = t  # 原文/空,直接保留
            continue
        h = _hash(t)
        if h in cache:
            out[i] = cache[h]
        else:
            pending_idx.append(i)
            pending_texts.append(t)

    if not pending_texts:
        return out

    locale_name = LOCALE_NAMES.get(locale, locale)
    # F-05: 每条原文走 _sanitize_str 过滤控制字符 + 阻断 prompt-break;整体包 <input> 标签
    lines = "\n".join(
        f"{j+1}. {_sanitize_str(t, max_len=500)}" for j, t in enumerate(pending_texts)
    )
    prompt = _PROMPT_BATCH.format(locale_name=locale_name, lines=f"<input>\n{lines}\n</input>")
    res = _call_translate(prompt, max_tokens=min(8000, 200 * len(pending_texts) + 500),
                           timeout=120)
    if "error" in res or not res.get("content"):
        # 失败 → 全部 fallback 原文,但仍占位让函数返回完整列表
        for i, t in zip(pending_idx, pending_texts):
            out[i] = t
        return out

    parsed = _parse_numbered(res["content"], expected=len(pending_texts))
    if parsed is None:
        # 解析失败 → fallback
        for i, t in zip(pending_idx, pending_texts):
            out[i] = t
        return out

    # 写缓存
    for i, t, tr in zip(pending_idx, pending_texts, parsed):
        out[i] = tr
        cache[_hash(t)] = tr
    _save_disk_cache(namespace, locale)
    return out


_NUM_RE = re.compile(r"^\s*(\d+)\s*[\.\):、]\s*(.+?)\s*$", re.MULTILINE)


def _parse_numbered(content: str, expected: int) -> list:
    """解析 '1. xxx\\n2. yyy' 格式。返回 list[str] 或 None。"""
    matches = _NUM_RE.findall(content)
    if not matches:
        return None
    by_idx = {int(n): text.strip() for n, text in matches}
    out = []
    for i in range(1, expected + 1):
        out.append(by_idx.get(i, ""))
    # 若缺太多,视为解析失败
    if sum(1 for x in out if x) < expected * 0.5:
        return None
    # 缺的位置填空字符串
    return [x if x else "" for x in out]


# ── 递归翻译 dict/list ────────────────────────────────
def translate_object(obj, locale: str, namespace: str = "default") -> object:
    """递归翻译 dict/list 中的中文字符串值。

    规则:
      - dict: 递归处理 value;若 key 在 _SKIP_KEYS,value 不翻译(但若 value 还是 dict/list 也照走)
      - list: 每个元素递归
      - str: 若 _is_chinese → 翻译,否则保留
      - 非 str/dict/list: 原样返回

    不修改原对象,返回新对象。
    """
    if locale == "zh-CN":
        return obj
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            if isinstance(v, str):
                # 不在 _SKIP_KEYS 才翻译(例如 "search_summary" 要翻)
                if k not in _SKIP_KEYS and _is_chinese(v):
                    new[k] = translate_zh_to(v, locale, namespace)
                else:
                    new[k] = v
            else:
                new[k] = translate_object(v, locale, namespace)
        return new
    if isinstance(obj, list):
        return [translate_object(x, locale, namespace) for x in obj]
    return obj


def translate_object_batch(obj, locale: str, namespace: str = "default") -> object:
    """translate_object 的批优化版:先收集所有中文句子,一次 GLM 调用翻译,再回填。

    对结构稳定但嵌套深的 object(比如 web search 完整响应)省 5-10 倍 API 调用。
    """
    if locale == "zh-CN":
        return obj

    # 1) 收集所有需要翻译的中文字符串,记录路径
    paths = []     # list of (parent_ref, key_or_index, original_text)
    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str):
                    if k not in _SKIP_KEYS and _is_chinese(v):
                        paths.append((node, k, v))
                else:
                    _walk(v)
        elif isinstance(node, list):
            for i, x in enumerate(node):
                if isinstance(x, str):
                    if _is_chinese(x):
                        paths.append((node, i, x))
                else:
                    _walk(x)
    _walk(obj)

    if not paths:
        return obj

    # 2) 用 cache 过滤:已翻译的直接回填,剩下的批翻
    cache = _load_disk_cache(namespace, locale)
    pending = []
    pending_paths = []
    for parent, key, text in paths:
        h = _hash(text)
        if h in cache:
            parent[key] = cache[h]
        else:
            pending.append(text)
            pending_paths.append((parent, key))

    if not pending:
        return obj

    # 分批(每批 ~25 条)
    BATCH = 25
    for batch_start in range(0, len(pending), BATCH):
        batch = pending[batch_start:batch_start + BATCH]
        batch_paths = pending_paths[batch_start:batch_start + BATCH]
        translated = translate_batch_zh_to(batch, locale, namespace)
        for (parent, key), tr in zip(batch_paths, translated):
            parent[key] = tr
    return obj


# ── per-match 缓存层(整场 intel / h2h 一次性本地化) ─────
MATCH_CACHE_DIR = PROJECT_ROOT / "data" / "match_i18n_cache"
MATCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_MATCH_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


def _match_slug(*parts: str) -> str:
    """拼出文件名安全的 match key(剔除所有非字母数字)。"""
    raw = "_".join(str(p) for p in parts if p)
    return _MATCH_SAFE_RE.sub("_", raw) or "_unknown"


def _match_cache_path(namespace: str, match_key: str, locale: str) -> Path:
    return MATCH_CACHE_DIR / f"{namespace}__{match_key}__{locale}.json"


def _load_match_cache(namespace: str, match_key: str, locale: str):
    path = _match_cache_path(namespace, match_key, locale)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save_match_cache(namespace: str, match_key: str, locale: str, payload) -> None:
    path = _match_cache_path(namespace, match_key, locale)
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.replace(path)
    except OSError as e:
        print(f"⚠ 写 match 缓存失败 {path}: {e}")


def localize_intel_cached(intel, home: str, away: str, date: str, locale: str = "zh-CN"):
    """把 websearch_client.search_pre_match_briefing() 的中文结果按 locale 一次性本地化。

    输入:从 websearch_client 拿到的原始 dict(包含 form_home/form_away/headline 等字段)
    行为:
      - locale == 'zh-CN' 或原始非中文 → 直接返回原对象
      - 否则查 data/match_i18n_cache/intel__{slug}__{locale}.json,命中即返回
      - 未命中 → translate_object_batch() 翻译,写盘,返回

    命名空间:`intel_{home}_vs_{away}` —— 同一对阵的不同比赛日共享翻译结果。
    """
    if intel is None:
        return intel
    if not isinstance(intel, dict):
        return intel
    if locale == "zh-CN":
        return intel
    # 命中标记:websearch 客户端已翻译过的不再二次处理
    if intel.get("_locale") == locale:
        return intel

    namespace = f"intel_{_match_slug(home, away)}"
    match_key = _match_slug(date, home, away)
    cached = _load_match_cache(namespace, match_key, locale)
    if cached is not None:
        return cached

    translated = translate_object_batch(intel, locale, namespace)
    translated["_locale"] = locale
    _save_match_cache(namespace, match_key, locale, translated)
    return translated


def localize_h2h_cached(h2h, home: str, away: str, locale: str = "zh-CN"):
    """h2h 版本,不带 date(交手记录无比赛日归属)。"""
    if h2h is None:
        return h2h
    if not isinstance(h2h, dict):
        return h2h
    if locale == "zh-CN":
        return h2h
    if h2h.get("_locale") == locale:
        return h2h

    namespace = f"h2h_{_match_slug(home, away)}"
    match_key = _match_slug(home, away)
    cached = _load_match_cache(namespace, match_key, locale)
    if cached is not None:
        return cached

    translated = translate_object_batch(h2h, locale, namespace)
    translated["_locale"] = locale
    _save_match_cache(namespace, match_key, locale, translated)
    return translated
