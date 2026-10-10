# tests/test_leagues_frontend.py
"""前端:页面可访问、i18n 6 语言 key 齐套、版本号已 bump。"""
import json
import os

from fastapi.testclient import TestClient

from webapp.app import app

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "webapp", "static")

NEW_KEYS = ["nav.leagues", "nav.wc", "leagues.title", "leagues.tab.matches",
            "leagues.tab.standings", "leagues.tab.sim", "leagues.tab.value",
            "leagues.odds", "leagues.score", "leagues.pro_only", "leagues.not_ready",
            "leagues.updated", "leagues.low_conf", "leagues.title_race",
            "leagues.ucl_spots", "leagues.relegation", "leagues.edge",
            "leagues.pick", "leagues.kelly"]


def test_leagues_page_served():
    r = TestClient(app).get("/leagues")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "leagues.js" in r.text and "i18n.js" in r.text


def test_i18n_key_parity_all_locales():
    for locale in ("en", "zh-CN", "es", "pt", "ar", "fr"):
        with open(os.path.join(STATIC, "i18n", f"{locale}.json"), encoding="utf-8") as f:
            d = json.load(f)
        missing = [k for k in NEW_KEYS if k not in d or not str(d[k]).strip()]
        assert not missing, f"{locale} 缺 key: {missing}"


def test_i18n_ver_bumped():
    with open(os.path.join(STATIC, "i18n.js"), encoding="utf-8") as f:
        src = f.read()
    assert "I18N_VER = 8" in src


def test_index_html_has_leagues_nav_and_bumped_script():
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        html = f.read()
    assert 'href="/leagues"' in html
    assert "i18n.js?v=25" in html


def test_leagues_html_matches_index_versions():
    """静态资源缓存防线:leagues.html 引用的 i18n.js ?v= 与 index.html 一致。"""
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        idx = f.read()
    with open(os.path.join(STATIC, "leagues.html"), encoding="utf-8") as f:
        lg = f.read()
    import re
    m_idx = re.search(r'i18n\.js\?v=(\d+)', idx)
    m_lg = re.search(r'i18n\.js\?v=(\d+)', lg)
    assert m_idx and m_lg and m_idx.group(1) == m_lg.group(1)
