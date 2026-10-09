"""Test phân loại status live/die (P1) — chống báo DIE OAN khi cookie/mạng lỗi.

Bối cảnh: poller từng làm `new_status = "live" if res["alive"] else "die"`, mà
check_uid trả cả "exists"/"error"/"cookie_invalid"/"unknown" với alive=False ->
mọi lỗi cookie/proxy biến thành "acc bị khoá" cho TOÀN BỘ watch trong 1 vòng.
"""
import pathlib

import pytest

from app import poller as pollermod


@pytest.mark.parametrize("status,alive", [
    ("exists", False), ("error", False), ("cookie_invalid", None),
    ("unknown", False), ("", False), ("Error", False), ("EXISTS", False),
    ("Cookie_Invalid", None),
])
def test_status_chua_ket_luan(status, alive):
    assert pollermod._conclusive_fb_status(status, alive) is None


@pytest.mark.parametrize("status,alive,expect", [
    ("live", True, "live"),
    ("dead", False, "die"),
    ("die", False, "die"),
    ("disabled", False, "die"),
    ("checkpoint_282", False, "die"),
    ("checkpoint_956", False, "die"),
    ("checkpoint", False, "die"),
])
def test_status_dut_khoat(status, alive, expect):
    assert pollermod._conclusive_fb_status(status, alive) == expect


def test_helper_da_duoc_noi_vao_cac_vong_check():
    """Chống hồi quy: helper phải được GỌI ở các vòng check, không chỉ định nghĩa."""
    src = pathlib.Path(pollermod.__file__).read_text(encoding="utf-8")
    # 1 lần định nghĩa (_conclusive_fb_status) + >=3 lần gọi (watch, fb track, daily report)
    assert src.count("_conclusive_fb_status(") >= 4, (
        "helper chưa được nối vào _check_fb_watches/_check_fb_accounts/báo cáo ngày")
