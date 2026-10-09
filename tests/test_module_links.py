"""Test hồi quy: mọi tham chiếu CHÉO MODULE phải trỏ tới tên có thật.

Bối cảnh: lần refactor tách bot.py thành nhiều module đã để lại 11 lời gọi hàm/biến
KHÔNG tồn tại (config.POLL_INTERVAL, fb.fetch_fb_post_info, db.get_admin,
db.admin_audit_log, manager/log trong api.py, ...) làm chết 4 tính năng mà không ai
phát hiện vì lỗi nằm trong try/except hoặc trong asyncio task không ai await.
Test này quét AST và chặn tái phát — thêm 1 lời gọi sai là test đỏ ngay.

Chạy: python -m pytest tests/test_module_links.py -q
"""
import ast
import pathlib

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
_APP = _REPO / "botcheckv2" / "backend" / "app"


def _collect_modules():
    """Trả {tên_module: (đường_dẫn, source, is_package)}."""
    mods = {}
    for p in sorted(_APP.rglob("*.py")):
        rel = p.relative_to(_APP).as_posix()
        if rel.startswith("scratch/"):
            continue  # script dev, không thuộc runtime
        is_pkg = p.name == "__init__.py"
        name = rel[:-3].replace("/", ".")
        if is_pkg:
            name = name[: -len(".__init__")] if name.endswith(".__init__") else ""
        mods[name] = (p, p.read_text(encoding="utf-8", errors="replace"), is_pkg)
    return mods


def _top_level_names(tree):
    """Tên có thật ở cấp module (hàm/lớp/biến/import), kể cả khai báo trong if/try."""
    names = set()

    def scan_body(body):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    names.add(a.asname or a.name.split(".")[0])
            elif isinstance(node, ast.If):
                scan_body(node.body)
                scan_body(node.orelse)
            elif isinstance(node, ast.Try):
                scan_body(node.body)
                scan_body(node.orelse)
                scan_body(node.finalbody)
                for h in node.handlers:
                    scan_body(h.body)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                scan_body(node.body)

    scan_body(tree.body)
    return names


def _resolve_imported_modules(mod_name, tree, is_pkg, known):
    """{alias_local: tên_module_trong_app} — CHỈ với import trỏ tới MODULE thật.

    Cố tình bỏ qua `from .bot import manager` (manager là instance, không phải module)
    vì không kiểm tra được thuộc tính của instance bằng phân tích tĩnh — đó cũng là
    nguồn dương tính giả của các công cụ lint.
    """
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level > 0:
            if is_pkg:
                base = mod_name.split(".") if mod_name else []
            else:
                base = mod_name.split(".")[:-1]
            if node.level > 1:
                drop = node.level - 1
                base = base[:-drop] if drop <= len(base) else []
            if node.module:  # from .x import y  -> y có thể là module con x.y
                tgt_parent = ".".join([*base, node.module])
                for a in node.names:
                    if a.name == "*":
                        continue
                    full = f"{tgt_parent}.{a.name}"
                    if full in known:
                        out[a.asname or a.name] = full
            else:  # from . import y
                pkg = ".".join(base)
                for a in node.names:
                    if a.name == "*":
                        continue
                    full = f"{pkg}.{a.name}" if pkg else a.name
                    if full in known:
                        out[a.asname or a.name] = full
    return out


def _shadowed_alias_ranges(tree, aliases):
    """Vùng dòng mà alias bị BIẾN CỤC BỘ cùng tên che (vd poller có `config = json.loads(...)`).

    Trả [(lineno_bắt_đầu, lineno_kết_thúc, alias)]. Không xử lý phần này sẽ tạo dương
    tính giả đúng như công cụ phân tích đầu tiên đã mắc.
    """
    ranges = []
    for node in tree.body:  # module-level shadowing -> che cả file
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in aliases:
                    ranges.append((0, 10 ** 9, t.id))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assigned = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            assigned.add(t.id)
                elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    assigned.add(sub.target.id)
                elif isinstance(sub, (ast.For, ast.AsyncFor)) and isinstance(sub.target, ast.Name):
                    assigned.add(sub.target.id)
            for a in assigned & aliases:
                ranges.append((node.lineno, node.end_lineno or node.lineno, a))
    return ranges


def test_cross_module_references_exist():
    mods = _collect_modules()
    trees = {name: ast.parse(src, filename=name) for name, (_p, src, _i) in mods.items()}
    known = set(mods)
    defined = {name: _top_level_names(t) for name, t in trees.items()}

    problems = []
    for name, (_p, _src, is_pkg) in mods.items():
        tree = trees[name]
        aliases = _resolve_imported_modules(name, tree, is_pkg, known)
        if not aliases:
            continue
        shadow = _shadowed_alias_ranges(tree, set(aliases))
        seen = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)):
                continue
            base = node.value.id
            if base not in aliases:
                continue
            if any(a == base and lo <= node.lineno <= hi for lo, hi, a in shadow):
                continue  # alias bị biến cục bộ che trong hàm này
            tgt = aliases[base]
            attr = node.attr
            if attr in defined.get(tgt, set()):
                continue
            if f"{tgt}.{attr}" in known:  # là submodule
                continue
            key = (name, base, attr)
            if key in seen:
                continue
            seen.add(key)
            problems.append(f"{name}:{node.lineno} goi `{base}.{attr}` "
                            f"nhung {tgt} khong dinh nghia ten nay")

    if problems:
        pytest.fail("Phat hien tham chieu cheo module khong ton tai:\n  "
                    + "\n  ".join(sorted(problems)))


def test_known_linkage_fixes_still_present():
    """Các tên từng bị thiếu và đã sửa — phải tồn tại (chống revert)."""
    from app import config, db, fb

    assert hasattr(config, "POLL_INTERVAL"), "config.POLL_INTERVAL bi mat (loi B1)"
    assert hasattr(fb, "fetch_fb_post_info"), "fb.fetch_fb_post_info bi mat (loi B2)"
    assert hasattr(fb, "build_fb_post_caption"), "fb.build_fb_post_caption bi mat (loi B2)"
    assert hasattr(db, "get_admin_by_id"), "db.get_admin_by_id bi mat (loi B3)"
    assert not hasattr(db, "get_admin"), "db.get_admin van duoc goi o api.py (loi B3)"
    assert hasattr(db, "admin_audit_add"), "db.admin_audit_add bi mat (loi B4)"

    # đọc file trực tiếp (không import cả cây handlers -> tránh phụ thuộc aiogram).
    # Lưu ý: KHÔNG được kiểm tra bằng chuỗi "await _cb_answer(" vì các call site hợp lệ
    # vẫn gọi helper này; lỗi B10 là hàm TỰ GỌI CHÍNH NÓ trong thân hàm.
    tree = ast.parse((_APP / "handlers" / "pauseadm.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "_cb_answer"):
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                        and sub.func.id == "_cb_answer"):
                    raise AssertionError("_cb_answer tu goi chinh no (loi B10)")
    loan_src = (_APP / "handlers" / "loan.py").read_text(encoding="utf-8")
    assert "admin_audit_log(" not in loan_src, "loan.py goi lai db.admin_audit_log (loi B4)"
    poller_src = (_APP / "poller.py").read_text(encoding="utf-8")
    assert "_is_user_active(" not in poller_src, "poller.py goi lai _is_user_active (loi B11)"

