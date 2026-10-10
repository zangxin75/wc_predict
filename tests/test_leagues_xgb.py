"""XGBoost:赛前特征无泄漏、walk-forward 切分、proba 合法、无模型降级。"""
import numpy as np
import pytest

from leagues import build, config, train_xgb


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    from leagues import fetch_fd, normalize
    rows = []
    # 3 个赛季,Strong 稳定虐 Weak(模型应学到 elo_diff/atk_diff 方向)
    for si, y in enumerate((2023, 2024, 2025)):
        s = f"{y % 100:02d}{(y + 1) % 100:02d}"
        for rnd in range(12):
            rows.append({"season": s, "date": f"{y}-09-{rnd + 1:02d}",
                         "home": "Strong", "away": "Weak", "gh": 2, "ga": 0})
            rows.append({"season": s, "date": f"{y + 1}-03-{rnd + 1:02d}",
                         "home": "Weak", "away": "Strong", "gh": 0, "ga": 1})
    fetch_fd.write_csv(config.paths("E0")["results"], normalize.RESULT_FIELDS, rows)
    fetch_fd.write_csv(config.paths("E0")["schedule"], normalize.SCHEDULE_FIELDS, [
        {"date": "2026-10-11", "time": "15:00", "home": "Strong", "away": "Weak",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
    ])
    return tmp_path


def test_form_features_are_pre_match():
    rows = [
        {"date": "2026-08-15", "home": "A", "away": "B", "gh": 2, "ga": 0},
        {"date": "2026-08-22", "home": "A", "away": "C", "gh": 1, "ga": 1},
    ]
    fm = train_xgb._form_features(rows)
    # 首场:双方赛季首战,form 必须全 0(赛前没有任何历史)
    assert fm[("2026-08-15", "A", "B")] == (0, 0, 0, 0, 0, 0)
    # 第二场:A 已 1 胜(3 分,进 2 失 0)——赛前值,不含本场
    ph, pa, gfh, gfa, gah, gaa = fm[("2026-08-22", "A", "C")]
    assert (ph, gfh, gah) == (3, 2, 0) and (pa, gfa, gaa) == (0, 0, 0)


def test_dataset_walk_forward_groups(sandbox):
    X, y, groups = train_xgb.build_dataset()
    assert X.shape[1] == len(train_xgb.FEATURES)
    assert set(y) == {0, 2}                        # 本数据只有主胜/客胜
    assert len(set(groups)) == 3                   # 3 个赛季
    # 特征合法性:无 NaN
    assert not np.isnan(X).any()


def test_train_and_predict_roundtrip(sandbox):
    bst, meta = train_xgb.train()
    assert meta["n_train"] > 0 and meta["n_val"] > 0
    assert meta["val_season"] == "2425"            # 倒数第二个赛季做验证(2324 训 / 2425 验)
    feats = {f: 0.0 for f in train_xgb.FEATURES}
    feats.update({"elo_diff": 200.0, "lg_E0": 1.0, "home": 1.0})
    p = train_xgb.predict_proba(bst, feats)
    assert abs(sum(p.values()) - 1.0) < 1e-4
    # 模型文件落盘 + 可重载
    assert train_xgb.load_model() is not None


def test_build_upcoming_lookup(sandbox):
    train_xgb.train()
    lookup = train_xgb.build_upcoming_lookup()
    assert ("E0", "Strong", "Weak") in lookup
    p = lookup[("E0", "Strong", "Weak")]
    assert abs(sum(p.values()) - 1.0) < 1e-4


def test_lookup_empty_without_model(sandbox):
    assert train_xgb.build_upcoming_lookup() == {}
