# leagues/fetch_odds_ucl.py
"""TheOddsAPI 欧冠赔率(配额敏感:仅 updater 低频调用,月 <20 次)。

  · sport key 钉死 soccer_uefa_champs_league(spec v2 勘误,Task 1 测试已 pin)
  · API key 用 env THE_ODDS_API_KEY(与 WC fetch_odds_api.py 同一变量)
  · h2h 市场跨博彩商取均价;队名一律 canonical() 归一(Review Focus 4)
  · 无 key / 请求失败 → {}(静默降级:预测照常,只是没赔率)
"""
import os

import requests

from . import config

API_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
TIMEOUT = 30


def fetch_ucl_odds(api_key=None):
    """返回 {(home, away): {"b365h","b365d","b365a"}}(h2h 均价,键已 canonical)。"""
    key = (api_key or os.environ.get("THE_ODDS_API_KEY", "")).strip()
    if not key:
        return {}
    sport = config.LEAGUES["UCL"]["odds_sport_key"]
    r = requests.get(API_URL.format(sport=sport),
                     params={"apiKey": key, "regions": "eu", "markets": "h2h"},
                     timeout=TIMEOUT)
    r.raise_for_status()
    aliases = config.load_aliases()
    out = {}
    for ev in r.json():
        home = config.canonical(ev.get("home_team", ""), aliases)
        away = config.canonical(ev.get("away_team", ""), aliases)
        hs, ds, as_ = [], [], []
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk.get("key") != "h2h":
                    continue
                for oc in mk.get("outcomes", []):
                    nm = config.canonical(oc.get("name", ""), aliases)
                    if nm == home:
                        hs.append(oc["price"])
                    elif nm == away:
                        as_.append(oc["price"])
                    elif oc.get("name") == "Draw":
                        ds.append(oc["price"])
        if home and away and hs and ds and as_:
            out[(home, away)] = {"b365h": sum(hs) / len(hs),
                                 "b365d": sum(ds) / len(ds),
                                 "b365a": sum(as_) / len(as_)}
    return out
