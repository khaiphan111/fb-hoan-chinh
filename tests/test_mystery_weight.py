"""Test hộp mù theo tỷ trọng — chạy trên SQLite TẠM, không đụng data.db thật."""
from collections import Counter


def _mk_cat(db, name, weight, eligible=1, n_stock=5):
    cid = db.acc_category_add(name, 100000, 24, "desc", "Acc Facebook")
    assert cid > 0
    rows = [{"uid": f"{name}_{i}", "password": "mk"} for i in range(n_stock)]
    added, _ = db.acc_stock_add_batch(cid, rows, "test")
    assert added == n_stock
    assert db.acc_category_update(cid, mystery_eligible=eligible,
                                 mystery_weight=weight)
    return cid


def test_mystery_weight_column_default(tdb):
    db = tdb
    cid = db.acc_category_add("WDef", 100000, 24, "", "Acc Facebook")
    c = db.acc_category_get(cid)
    assert int(c["mystery_weight"] or 0) == 100


def test_mystery_weights_pct(tdb):
    db = tdb
    _mk_cat(db, "WA", 70)
    _mk_cat(db, "WB", 30)
    ws = db.acc_mystery_weights()
    assert len(ws) == 2
    d = {w["name"]: w for w in ws}
    assert d["WA"]["weight"] == 70 and d["WA"]["pct"] == 70.0
    assert d["WB"]["weight"] == 30 and d["WB"]["pct"] == 30.0


def test_mystery_pick_weighted(tdb):
    db = tdb
    c1 = _mk_cat(db, "WA", 70)
    c2 = _mk_cat(db, "WB", 30)
    cnt = Counter()
    for _ in range(1200):
        cands = db.acc_mystery_pick_candidates(1, ())
        assert cands, "phai co ung vien"
        cnt[cands[0]["cat_id"]] += 1
    # ky vong ~840/360, cho lech 120
    assert abs(cnt[c1] - 840) < 120, cnt
    assert abs(cnt[c2] - 360) < 120, cnt


def test_mystery_pick_skips_empty_cat(tdb):
    db = tdb
    c1 = _mk_cat(db, "WA", 90, n_stock=3)
    _mk_cat(db, "WB", 10, n_stock=0)  # het hang -> bi bo qua
    for _ in range(50):
        cands = db.acc_mystery_pick_candidates(3, ())
        assert cands
        assert all(r["cat_id"] == c1 for r in cands)


def test_mystery_pick_exclude_ids(tdb):
    db = tdb
    c1 = _mk_cat(db, "WA", 100, n_stock=2)
    cands = db.acc_mystery_pick_candidates(5, ())
    assert len(cands) == 2
    seen = [r["id"] for r in cands]
    assert db.acc_mystery_pick_candidates(5, seen) == []


def test_parse_step_pick_weight():
    import importlib
    sm = importlib.import_module("app.shop_menu")
    ok, v, _ = sm._parse_step("pick_weight", "250")
    assert ok and v == 250
    ok, v, _ = sm._parse_step("pick_weight", "0")
    assert not ok
    ok, v, _ = sm._parse_step("pick_weight", "abc")
    assert not ok
    ok, v, _ = sm._parse_step("pick_weight", "100001")
    assert not ok


def test_mystery_weight_flow_registered():
    import importlib
    sm = importlib.import_module("app.shop_menu")
    fl = sm.FLOWS.get("mystery_weight")
    assert fl, "thieu flow mystery_weight"
    assert fl["handler"] == "on_hopmutile"
    assert fl["build"]([3, 250]) == "/hopmutile 3 250"
    items = dict(sm.GROUPS["price"][1])
    assert "mystery_weight" in items
