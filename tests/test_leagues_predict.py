# tests/test_leagues_predict.py
"""单场预测管线:矩阵归一、三态、回退、devig、Kelly、分歧门控、market 结构。"""
from leagues import predict


def test_score_matrix_normalized_and_shapes():
    m = predict.score_matrix(1.6, 1.1)
    assert len(m) == predict.MAX_GOALS + 1 and len(m[0]) == predict.MAX_GOALS + 1
    assert abs(sum(sum(r) for r in m) - 1.0) < 1e-9
    hw, dr, aw = predict.three_way(m)
    assert abs(hw + dr + aw - 1.0) < 1e-9
    assert hw > aw                                # λ_h > λ_a → 主胜概率高


def test_top_scores_format_and_order():
    m = predict.score_matrix(1.5, 1.0)
    top = predict.top_scores(m, n=5)
    assert len(top) == 5 and all(isinstance(s, str) and "-" in s for s, _ in top)
    probs = [p for _, p in top]
    assert probs == sorted(probs, reverse=True)


def test_ou25_and_btts_sanity():
    high = predict.score_matrix(2.5, 2.2)
    low = predict.score_matrix(0.6, 0.5)
    assert predict.ou25_over(high) > predict.ou25_over(low)
    assert predict.btts_yes(high) > predict.btts_yes(low)
    # 低分场景 1-1(总 2 球,BTTS 但 under)主导 → BTTS > Over2.5
    assert 0 < predict.ou25_over(low) < predict.btts_yes(low) < 1


def test_elo_probs_symmetry_and_home_adv():
    p_neut, lh, la = predict.elo_probs(1600, 1600, neutral=True)
    assert abs(lh - la) < 1e-9 and abs(p_neut["home"] - p_neut["away"]) < 1e-9
    p_home, lh2, la2 = predict.elo_probs(1600, 1600, neutral=False)
    assert lh2 > la2 and p_home["home"] > p_home["away"]
    assert abs(sum(p_home.values()) - 1.0) < 1e-9


def test_devig_and_kelly():
    dv = predict.devig(2.0, 3.0, 4.0)
    assert abs(sum(dv) - 1.0) < 1e-9
    assert dv[0] < 1 / 2.0                        # 比例去水:水位 >1 → 概率被压缩
    assert predict.devig(None, 3.0, 4.0) is None
    assert predict.devig(1.0, 3.0, 4.0) is None   # 赔率 ≤1 非法
    assert predict.kelly(0.6, 2.0) > 0            # 正 edge
    assert predict.kelly(0.4, 2.0) == 0.0         # 负 edge → 0,不出负仓位


def test_combine_gate_agree_vs_disagree():
    stat = {"home": 0.5, "draw": 0.3, "away": 0.2}
    xgb_agree = {"home": 0.7, "draw": 0.2, "away": 0.1}
    out = predict.combine_gate(stat, xgb_agree)
    assert abs(out["home"] - (0.2 * 0.5 + 0.8 * 0.7)) < 1e-9   # 一致 → xgb 占 0.8
    xgb_dis = {"home": 0.1, "draw": 0.2, "away": 0.7}
    out2 = predict.combine_gate(stat, xgb_dis)
    assert out2 == stat                           # 分歧 → 纯统计模型


def test_predict_match_with_and_without_odds():
    elo = {"A": 1650.0, "B": 1500.0}
    dc_params = {"A": {"attack": 0.3, "defense": -0.1, "n": 100},
                 "B": {"attack": -0.2, "defense": 0.2, "n": 100}}
    dc_glob = {"beta0": 0.3, "home_adv": 0.25, "rho": 0.0}
    out = predict.predict_match("A", "B", elo=elo, dc_params=dc_params, dc_glob=dc_glob,
                                odds={"b365h": 1.9, "b365d": 3.4, "b365a": 4.2})
    assert abs(sum(out["probs"].values()) - 1.0) < 1e-3
    assert out["lam_h"] > out["lam_a"]
    assert len(out["top_scores"]) == 5
    mk = out["market"]
    assert set(mk) == {"p_mkt", "edges", "max_edge", "pick", "odds", "kelly"}
    assert mk["pick"] in ("home", "draw", "away")
    assert mk["edges"][mk["pick"]] == mk["max_edge"]
    # 无赔率 → 不出现 market 键(付费门控的前提:键不存在而非置空)
    out2 = predict.predict_match("A", "B", elo=elo, dc_params=dc_params, dc_glob=dc_glob)
    assert "market" not in out2


def test_predict_match_fallback_to_elo_marks_low_confidence():
    elo = {"A": 1650.0, "B": 1500.0}
    out = predict.predict_match("A", "Ghost", elo=elo, dc_params={}, dc_glob={"beta0": 0.3})
    assert out["low_confidence"] is True
    assert out["probs"]["home"] > out["probs"]["away"]


def test_predict_match_xgb_gate():
    elo = {"A": 1650.0, "B": 1500.0}
    dc_params = {"A": {"attack": 0.3, "defense": 0.0, "n": 100},
                 "B": {"attack": 0.0, "defense": 0.0, "n": 100}}
    dc_glob = {"beta0": 0.3, "home_adv": 0.25, "rho": 0.0}
    base = predict.predict_match("A", "B", elo=elo, dc_params=dc_params, dc_glob=dc_glob)
    gated = predict.predict_match("A", "B", elo=elo, dc_params=dc_params, dc_glob=dc_glob,
                                  xgb_probs={"home": 0.2, "draw": 0.3, "away": 0.5})
    assert gated["probs"] == base["probs"]        # 分歧(主 vs 客)→ 门控回纯统计
