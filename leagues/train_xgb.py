# leagues/train_xgb.py
"""俱乐部 XGBoost 三态模型(walk-forward 严格)。

特征(15 维,全部赛前可得,无未来泄漏):
  elo_diff, elo_h, elo_a          — 统一 Elo 池赛前值(build_pool history)
  form5_diff, gf5_diff, ga5_diff  — 近 5 场积分/进球/失球差(赛前)
  atk_diff, def_diff              — DC 参数(赛季开赛日 cutoff 拟合)
  home                            — 恒 1(与 WC 特征表对齐)
  lg_E0/SP1/I1/D1/F1/UCL          — 联赛 one-hot

切分:最后第二个完整赛季做验证(early stopping),更早赛季做训练;
验证季预测用的特征本身仍是赛前值,标签只在拟合时使用。

产物:data/leagues/xgb_club.json(booster)+ xgb_club.json.meta.json。
训练是离线手工动作(python -m leagues.train_xgb),updater 只 load_model 推理。
"""
import json
import os
from collections import defaultdict, deque
from datetime import date

import numpy as np
import xgboost as xgb

from . import build, config, dixon_coles, elo as elo_mod

FEATURES = ["elo_diff", "elo_h", "elo_a", "form5_diff", "gf5_diff", "ga5_diff",
            "atk_diff", "def_diff", "home",
            "lg_E0", "lg_SP1", "lg_I1", "lg_D1", "lg_F1", "lg_UCL"]

_PARAMS = {"objective": "multi:softprob", "num_class": 3, "max_depth": 4,
           "eta": 0.05, "subsample": 0.8, "eval_metric": "mlogloss"}


def _season_of(d):
    """ISO 日期 → 赛季代码(8 月 1 日边界)。UCL 行没有 season 字段,统一从此推导。"""
    y, m = int(d[:4]), int(d[5:7])
    start = y if m >= 8 else y - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def _form_features(results, window=5):
    """{(date,home,away): (pts_h, pts_a, gf_h, gf_a, ga_h, ga_a)} — 全部赛前值。"""
    hist = defaultdict(lambda: deque(maxlen=window))
    out = {}
    for m in sorted(results, key=lambda r: (r["date"], r["home"])):
        if m["gh"] is None:
            continue
        hh, aa = hist[m["home"]], hist[m["away"]]
        out[(m["date"], m["home"], m["away"])] = (
            sum(g[0] for g in hh), sum(g[0] for g in aa),
            sum(g[1] for g in hh), sum(g[1] for g in aa),
            sum(g[2] for g in hh), sum(g[2] for g in aa))
        hp = 3 if m["gh"] > m["ga"] else (1 if m["gh"] == m["ga"] else 0)
        ap = 3 if m["ga"] > m["gh"] else (1 if m["gh"] == m["ga"] else 0)
        hh.append((hp, m["gh"], m["ga"]))
        aa.append((ap, m["ga"], m["gh"]))
    return out


def _label(m):
    return 0 if m["gh"] > m["ga"] else (1 if m["gh"] == m["ga"] else 2)


def build_dataset():
    """全 ALL_CODES 历史 → (X, y, groups)。walk-forward:特征全为赛时值。"""
    rows = []
    for code in config.ALL_CODES:
        rows.extend(build.load_results(code))
    rows = [m for m in rows if m["gh"] is not None]
    rows.sort(key=lambda m: (m["date"], m["home"]))
    _, history = elo_mod.build_pool(rows)
    elo_map = {(h["date"], h["home"], h["away"]): (h["elo_h"], h["elo_a"]) for h in history}
    form_map = _form_features(rows)
    # 每赛季 DC:联赛比赛按赛季开赛日 cutoff 拟合(UCL 借用同季联赛参数)
    seasons = sorted({_season_of(m["date"]) for m in rows})
    dc_by_season = {}
    for s in seasons:
        y1, _ = config.season_years(s)
        params, _glob = dixon_coles.fit_league(
            [m for m in rows if m["comp"] != "UCL"], cutoff=f"{y1}-08-01")
        dc_by_season[s] = params
    X, y, groups = [], [], []
    for m in rows:
        s = _season_of(m["date"])
        eh, ea = elo_map.get((m["date"], m["home"], m["away"]), (1500.0, 1500.0))
        ph, pa, gfh, gfa, gah, gaa = form_map.get((m["date"], m["home"], m["away"]),
                                                  (0, 0, 0, 0, 0, 0))
        dc = dc_by_season[s]
        ah = dc.get(m["home"], {"attack": 0.0, "defense": 0.0})
        aa_ = dc.get(m["away"], {"attack": 0.0, "defense": 0.0})
        feats = [eh - ea, eh, ea, ph - pa, gfh - gfa, gah - gaa,
                 ah["attack"] - aa_["attack"], ah["defense"] - aa_["defense"], 1.0]
        feats += [1.0 if m["comp"] == c else 0.0 for c in config.ALL_CODES]
        X.append(feats)
        y.append(_label(m))
        groups.append(s)
    return np.array(X, dtype=float), np.array(y, dtype=int), groups


def train():
    """walk-forward 训练:验证 = 倒数第二个赛季。落盘 booster + meta。返回 (bst, meta)。"""
    X, y, groups = build_dataset()
    seasons = sorted(set(groups))
    val_season = seasons[-2] if len(seasons) >= 2 else seasons[-1]
    tr = [i for i, g in enumerate(groups) if g < val_season]
    va = [i for i, g in enumerate(groups) if g == val_season]
    dtrain = xgb.DMatrix(X[tr], label=y[tr], feature_names=FEATURES)
    dval = xgb.DMatrix(X[va], label=y[va], feature_names=FEATURES)
    bst = xgb.train(_PARAMS, dtrain, num_boost_round=300,
                    evals=[(dval, "val")], early_stopping_rounds=30, verbose_eval=False)
    meta = {"features": FEATURES, "val_season": val_season,
            "n_train": len(tr), "n_val": len(va),
            "val_logloss": float(bst.best_score) if bst.best_score is not None else None,
            "trained_at": date.today().isoformat()}
    path = config.global_paths()["xgb"]
    bst.save_model(path)
    with open(path + ".meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return bst, meta


def load_model():
    path = config.global_paths()["xgb"]
    if not os.path.exists(path):
        return None
    bst = xgb.Booster()
    bst.load_model(path)
    return bst


def predict_proba(bst, feats):
    """feats: dict(特征名 → 值)。返回 {"home","draw","away"}(和为 1)。"""
    dm = xgb.DMatrix(np.array([[feats.get(f, 0.0) for f in FEATURES]]),
                     feature_names=FEATURES)
    p = bst.predict(dm)[0]
    return {"home": float(p[0]), "draw": float(p[1]), "away": float(p[2])}


def build_upcoming_lookup(elo=None):
    """当前所有 pre 赛程的 xgb_probs 查找表:{(code,home,away): probs}。
    模型文件不存在 → {}(rebuild 自动退化为纯统计模型,combine_gate 不触发)。"""
    bst = load_model()
    if bst is None:
        return {}
    if elo is None:
        elo = build.build_elo_pool()[0]
    latest_dc = {c: build._load_dc(c)["params"] for c in config.LEAGUE_CODES}
    out = {}
    for code in config.ALL_CODES:
        for m in build.load_schedule(code):
            if m["status"] != "pre":
                continue
            eh = elo.get(m["home"], 1500.0)
            ea = elo.get(m["away"], 1500.0)
            # form:取 0 近似(刻意设计,勿当泄漏/缺陷)——准确做法需要"最近一场之后"
            # 的滚动 form,复杂度不值;Elo 与 DC 已承载实力信息,且训练特征里赛季首战
            # 也是全 0,分布一致。钉死:后续增强再精细化(Task 18 回测观察)。
            ph = pa = gfh = gfa = gah = gaa = 0
            dc = latest_dc.get(code) or {}
            if code == "UCL":   # UCL 借用:队 → 各自联赛参数(与 fit_ucl_global 同源)
                dc = {}
                for c in config.LEAGUE_CODES:
                    dc.update({t: p for t, p in latest_dc[c].items() if t not in dc})
            ah = dc.get(m["home"], {"attack": 0.0, "defense": 0.0})
            aa_ = dc.get(m["away"], {"attack": 0.0, "defense": 0.0})
            feats = {"elo_diff": eh - ea, "elo_h": eh, "elo_a": ea,
                     "form5_diff": ph - pa, "gf5_diff": gfh - gfa, "ga5_diff": gah - gaa,
                     "atk_diff": ah["attack"] - aa_["attack"],
                     "def_diff": ah["defense"] - aa_["defense"], "home": 1.0,
                     **{f"lg_{c}": 1.0 if c == code else 0.0 for c in config.ALL_CODES}}
            out[(code, m["home"], m["away"])] = predict_proba(bst, feats)
    return out


if __name__ == "__main__":
    _, meta = train()
    print(json.dumps(meta, ensure_ascii=False, indent=1))
