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
    ok, v, _ = sm._parse_step("pick_weight", "60")
    assert ok and v == 60
    ok, v, _ = sm._parse_step("pick_weight", "60%")
    assert ok and v == 60
    ok, v, _ = sm._parse_step("pick_weight", "0")
    assert not ok
    ok, v, _ = sm._parse_step("pick_weight", "abc")
    assert not ok
    ok, v, _ = sm._parse_step("pick_weight", "100")
    assert not ok


def test_mystery_weight_flow_registered():
    import importlib
    sm = importlib.import_module("app.shop_menu")
    fl = sm.FLOWS.get("mystery_weight")
    assert fl, "thieu flow mystery_weight"
    assert fl["handler"] == "on_hopmutile"
    assert fl["build"]([3, 60]) == "/hopmutile 3 60%"
    items = dict(sm.GROUPS["price"][1])
    assert "mystery_weight" in items


def test_mystery_setup_list_includes_new_cat(tdb):
    db = tdb
    c1 = _mk_cat(db, "WA", 70)          # eligible
    c2 = _mk_cat(db, "WB", 30, eligible=0)  # loai moi chua tham gia
    lst = db.acc_mystery_setup_list()
    d = {i["name"]: i for i in lst}
    assert d["WA"]["eligible"] == 1 and d["WA"]["pct"] == 100.0
    assert d["WB"]["eligible"] == 0 and d["WB"]["pct"] == 0
    assert d["WB"]["stock"] == 5
    # dat ty trong -> tu bat tham gia (nhu on_hopmutile)
    assert db.acc_category_update(c2, mystery_weight=50, mystery_eligible=1)
    d = {i["name"]: i for i in db.acc_mystery_setup_list()}
    assert d["WB"]["eligible"] == 1
    total_pct = sum(i["pct"] for i in d.values() if i["eligible"])
    assert abs(total_pct - 100.0) < 0.2


def test_mystery_set_pct_rebalance(tdb):
    db = tdb
    a = _mk_cat(db, "PA", 60)
    b = _mk_cat(db, "PB", 30)
    c = _mk_cat(db, "PC", 10)
    # dat A = 50% -> A dung 50%, B:C giu ty le 3:1 trong 50% con lai
    assert db.acc_mystery_set_pct(a, 50)
    d = {i["name"]: i for i in db.acc_mystery_weights()}
    assert d["PA"]["pct"] == 50.0
    assert abs(d["PB"]["pct"] - 37.5) < 0.2
    assert abs(d["PC"]["pct"] - 12.5) < 0.2
    total = sum(i["pct"] for i in d.values())
    assert abs(total - 100.0) < 0.3


def test_mystery_set_pct_auto_enable_and_invalid(tdb):
    db = tdb
    x = _mk_cat(db, "PX", 50, eligible=0)
    assert db.acc_mystery_set_pct(x, 25)  # tu bat tham gia
    d = {i["name"]: i for i in db.acc_mystery_setup_list()}
    assert d["PX"]["eligible"] == 1
    assert not db.acc_mystery_set_pct(x, 0)
    assert not db.acc_mystery_set_pct(x, 100)
    assert not db.acc_mystery_set_pct(999999, 50)


def test_mystery_set_multi_example(tdb):
    db = tdb
    a = _mk_cat(db, "MA", 60)
    b = _mk_cat(db, "MB", 30)
    c = _mk_cat(db, "MC", 10)
    ok, note = db.acc_mystery_set_multi({a: 56, c: 18})
    assert ok, note
    d = {i["name"]: i for i in db.acc_mystery_weights()}
    assert d["MA"]["pct"] == 56.0   # giu dung % da set
    assert d["MC"]["pct"] == 18.0   # giu dung % da set
    assert abs(d["MB"]["pct"] - 26.0) < 0.2  # phan con lai


def test_mystery_set_multi_invalid(tdb):
    db = tdb
    a = _mk_cat(db, "NA", 50)
    b = _mk_cat(db, "NB", 50)
    ok, err = db.acc_mystery_set_multi({a: 60, b: 50})
    assert not ok and "100%" in err
    ok, err = db.acc_mystery_set_multi({a: 0})
    assert not ok
    ok, err = db.acc_mystery_set_multi({999999: 50})
    assert not ok


def test_mystery_set_multi_sum100_disables_rest(tdb):
    db = tdb
    a = _mk_cat(db, "SA", 50)
    b = _mk_cat(db, "SB", 50)
    ok, note = db.acc_mystery_set_multi({a: 100 - 1, b: 1} if False else {a: 99, b: 1})
    assert ok
    # S == 100 khong con loai ngoai -> binh thuong
    d = {i["name"]: i for i in db.acc_mystery_weights()}
    assert d["SA"]["pct"] == 99.0 and d["SB"]["pct"] == 1.0
    c = _mk_cat(db, "SC", 50)
    ok, note = db.acc_mystery_set_multi({a: 60, b: 40})
    assert ok and "tắt" in note  # SC tu tat vi tong du 100%
    names = [i["name"] for i in db.acc_mystery_weights()]
    assert "SC" not in names


def test_parse_step_pct_multi():
    import importlib
    sm = importlib.import_module("app.shop_menu")
    ok, v, _ = sm._parse_step("pct_multi", "12 56\n17 18")
    assert ok and v == "12:56,17:18"
    ok, v, _ = sm._parse_step("pct_multi", "12: 56%, 17: 18%")
    assert ok and v == "12:56,17:18"
    ok, _, _ = sm._parse_step("pct_multi", "12 60\n17 50")
    assert not ok  # tong > 100
    ok, _, _ = sm._parse_step("pct_multi", "abc")
    assert not ok
    fl = sm.FLOWS.get("mystery_weight_multi")
    assert fl and fl["build"](["12:56,17:18"]) == "/hopmutilemulti 12:56,17:18"
    items = dict(sm.GROUPS["price"][1])
    assert "mystery_weight_multi" in items


def test_mystery_toggle_single(tdb):
    db = tdb
    a = _mk_cat(db, "TA", 60)
    assert db.acc_mystery_set_eligible(a, 0)
    items = {i["name"]: i for i in db.acc_mystery_setup_list()}
    assert items["TA"]["eligible"] == 0
    assert items["TA"]["pct"] == 0
    assert db.acc_mystery_set_eligible(a, 1)
    items = {i["name"]: i for i in db.acc_mystery_setup_list()}
    assert items["TA"]["eligible"] == 1
    assert items["TA"]["pct"] == 100.0  # chi 1 loai bat


def test_mystery_toggle_stall_all(tdb):
    db = tdb
    a = _mk_cat(db, "GA", 50)
    b = _mk_cat(db, "GB", 50)
    n = db.acc_mystery_set_eligible_stall("Acc Facebook", 0)
    assert n == 2
    assert all(i["eligible"] == 0 for i in db.acc_mystery_setup_list())
    n = db.acc_mystery_set_eligible_stall("Acc Facebook", 1)
    assert n == 2
    assert all(i["eligible"] == 1 for i in db.acc_mystery_setup_list())


def test_mystog_groups_dynamic(tdb):
    import importlib
    sm = importlib.import_module("app.shop_menu")
    db = tdb
    # loai moi / sap moi tu hien trong man hinh bat/tat
    cid = db.acc_category_add("Gmail moi", 50000, 24, "desc", "Gmail Moi")
    assert cid > 0
    groups = sm._mystog_stalls()
    assert "Gmail Moi" in groups
    names = [i["name"] for i in groups["Gmail Moi"]]
    assert "Gmail moi" in names
    kb = sm._mystog_stalls_kb()
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Gmail Moi" in t for t in texts)
    kb2 = sm._mystog_cats_kb("Gmail Moi")
    datas = [b.callback_data for row in kb2.inline_keyboard for b in row]
    assert any(d == f"shopm:mystog:tog:{cid}" for d in datas)
    assert any(d == "shopm:mystog:allon:Gmail Moi" for d in datas)
    assert "mystery_toggle" in sm.FLOWS
    items = dict(sm.GROUPS["price"][1])
    assert "mystery_toggle" in items


def test_mystog_callback_order():
    # Handler shopm:mystog PHẢI đứng trước handler chung shopm:,
    # nếu không callback sẽ bị nuốt (bug 2026-09-25).
    from app.handlers._order import CALLBACK_ORDER
    assert "_on_shopm_mystog" in CALLBACK_ORDER
    assert CALLBACK_ORDER.index("_on_shopm_mystog") < CALLBACK_ORDER.index("_on_shopm_cb")
