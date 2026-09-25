"""Test gop bo 4 mail Outlook (mail|pass|refresh_token|client_id) trong parser nhap kho."""
import importlib.util
import re
import sys

import pytest

_BOT = None


def _load_parser():
    global _BOT
    if _BOT is None:
        # Load _smart_stock_fields ma khong chay toan bo bot.py (can aiogram...)
        src = open("botcheckv2/backend/app/bot.py", encoding="utf-8").read()
        m = re.search(
            r"(def _smart_stock_fields\(fields: list\) -> dict:.*?)\n\n\n",
            src, re.S)
        assert m, "khong tim thay _smart_stock_fields"
        ns = {}
        exec(m.group(1), ns)
        _BOT = ns["_smart_stock_fields"]
    return _BOT


RT = ("M.C537_BL2.0.U.MsaArtifacts.-CqCNi6sGIB!hHoZJXBV5xFrTmcvBArNmu5H*L*"
      "9TrVGvOvNrQUPM14vibmSNQqoQ0RoUTC81s3qUQGJy64zgkG30lgFd!GzFN1owu*civ"
      "GWTU4vlqXC4AEH85i5RPPHBusUozqTpwnUUd36qk8iSGEF06eji1Dkv8KWo2H*5L23t"
      "Ak3dJNrNruT9yEbMj7r4rP7Ur2KRpO2sNpr78GYPBV03tpZtnADl4!ZkFG7gucrM3Ro"
      "rLH9u0mfdkXAO0GCph*pveSBXAnsu8GRfcvEz")
UUID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"


def test_outlook_4tuple_merged():
    p = _load_parser()
    r = p(["LatishaKampwerth286013@outlook.com", "wvbqrm957916", RT, UUID])
    assert r["backup_mail"] == \
        f"LatishaKampwerth286013@outlook.com|wvbqrm957916|{RT}|{UUID}"
    assert r["note"] == ""
    assert r["password"] == ""


def test_outlook_4tuple_inside_full_row():
    p = _load_parser()
    r = p(["61593959792972", "fbpass", "01/01/2020",
           "a@outlook.com", "mk123", RT, UUID,
           "JBSWY3DPEHPK3PXP", "c_user=1;xs=2", "EAAGxxx"])
    assert r["uid"] == "61593959792972"
    assert r["password"] == "fbpass"
    assert r["backup_mail"] == f"a@outlook.com|mk123|{RT}|{UUID}"
    assert r["totp"] == "JBSWY3DPEHPK3PXP"
    assert r["cookie"] == "c_user=1;xs=2"
    assert r["token"] == "EAAGxxx"


def test_plain_email_not_merged():
    p = _load_parser()
    r = p(["61593959792972", "fbpass", "backup@gmail.com", "ghi chu"])
    assert r["backup_mail"] == "backup@gmail.com"
    assert r["password"] == "fbpass"
    assert r["note"] == "ghi chu"


def test_mc_token_without_uuid_not_merged():
    p = _load_parser()
    r = p(["a@outlook.com", "mk123", RT, "khong-phai-uuid"])
    # khong du bo 4 -> mail van vao backup_mail, token roi vao note (cu)
    assert r["backup_mail"] == "a@outlook.com"
    assert RT in r["note"]


def test_short_mc_not_merged():
    p = _load_parser()
    r = p(["a@outlook.com", "mk123", "M.C-ngan", UUID])
    assert r["backup_mail"] == "a@outlook.com"
    assert "M.C-ngan" in r["note"]
