"""test_update_live.py — update_live 单元测试(tmp_path mock CSV)。

覆盖:去重、幂等、内部去重、append、空输入、无文件。
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import update_live

HEADER = ("date,home_team,away_team,home_score,away_score,"
          "tournament,city,country,neutral\n")


def _write(p, *matches):
    """matches: (date, home, away, hs, as) 元组。写 9 列 CSV。"""
    lines = [HEADER]
    for d, h, a, hs, as_ in matches:
        lines.append(f"{d},{h},{a},{hs},{as_},FIFA World Cup,X,Y,TRUE\n")
    p.write_text("".join(lines), encoding="utf-8")
    return str(p)


# ── UL-01:diff 区分新场与已存在 ──────────────────────────────
def test_diff_new_and_dup(tmp_path):
    res = _write(tmp_path / "r.csv", ("2020-01-01", "A", "B", 2, 1))
    liv = _write(tmp_path / "l.csv",
                 ("2020-01-01", "A", "B", 2, 1),    # 已存在
                 ("2026-06-14", "C", "D", 3, 0))    # 新
    new, dup = update_live.diff_live(res, liv)
    assert dup == 1 and len(new) == 1
    assert new[0][1] == "C"                              # 新场 home=C


# ── UL-02:append 写入正确 ───────────────────────────────────
def test_append_new_writes(tmp_path):
    res = tmp_path / "r.csv"
    res.write_text(HEADER, encoding="utf-8")
    n = update_live.append_new(
        [["2026-06-14", "C", "D", "3", "0", "FIFA World Cup", "X", "Y", "TRUE"]],
        str(res))
    assert n == 1
    rows = update_live._read_data_rows(str(res))
    assert len(rows) == 1 and rows[0][1] == "C"


# ── UL-03:幂等(回写后再 diff 无新)──────────────────────────
def test_idempotent(tmp_path):
    res = tmp_path / "r.csv"
    res.write_text(HEADER, encoding="utf-8")
    liv = _write(tmp_path / "l.csv", ("2026-06-14", "C", "D", 3, 0))
    new, _ = update_live.diff_live(str(res), liv)
    update_live.append_new(new, str(res))
    new2, _ = update_live.diff_live(str(res), liv)
    assert new2 == []                                    # 二次无新


# ── UL-04:live 内部自身重复也去重 ────────────────────────────
def test_live_internal_dedup(tmp_path):
    res = tmp_path / "r.csv"
    res.write_text(HEADER, encoding="utf-8")
    liv = _write(tmp_path / "l.csv",
                 ("2026-06-14", "C", "D", 3, 0),
                 ("2026-06-14", "C", "D", 3, 0))          # live 内重复
    new, _ = update_live.diff_live(str(res), liv)
    assert len(new) == 1                                 # 内部去重


# ── UL-05:空输入 ────────────────────────────────────────────
def test_append_empty(tmp_path):
    res = tmp_path / "r.csv"
    assert update_live.append_new([], str(res)) == 0


# ── UL-06:live 文件不存在 → 空,不抛 ─────────────────────────
def test_no_live_file(tmp_path):
    res = _write(tmp_path / "r.csv", ("2020-01-01", "A", "B", 1, 0))
    new, dup = update_live.diff_live(res, str(tmp_path / "nope.csv"))
    assert new == [] and dup == 0


# ── UL-07:results 不存在 → 全部 live 视为新 ──────────────────
def test_no_results_all_new(tmp_path):
    liv = _write(tmp_path / "l.csv",
                 ("2026-06-14", "A", "B", 1, 0),
                 ("2026-06-15", "C", "D", 2, 2))
    new, dup = update_live.diff_live(str(tmp_path / "no_res.csv"), liv)
    assert len(new) == 2 and dup == 0
