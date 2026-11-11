"""Phase 0.3: gợi ý vai trò của từng bảng (chỉ theo tên cột bỏ dấu, kiểu, độ duy nhất) — không theo tên biz.

    python schema_roles.py --db-dir tmp_db_dev [--db-dir tmp_db] [--detail]
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path

from std_layer import _is_order_id, _is_product_code, _is_product_name, _key, _line_money_rank, _date_rank, Col

# vai trò -> (gợi ý tên bảng/cột bắt buộc có, mô tả)
ROLE_HINTS = {
    "payments": ("thanhtoan", "phieuthu", "soquy", "thutien", "tienthu", "sotienthu"),
    "lots": ("hsd", "handung", "hansudung", "hethan", "malo", "solo"),
    "debts": ("conno", "congno", "duno", "hanno", "hantra", "hanthanhtoan", "songayduocno"),
    "employees": ("nhanvien", "tennv", "manv", "tho", "nguoiban", "thungan"),
    "shift": ("baocaoca", "macalam", "calam", "ca"),
    "installments": ("tragop", "kygop", "gop", "trasau"),
    "warranty": ("baohanh",),
    "tradein": ("maycu", "thucu", "trogia", "thumay"),
    "purchases": ("nhap", "nhacungcap", "ncc"),
    "stock": ("tonkho", "toncuoi", "tonthuc", "ton"),
    "price_history": ("giamoi", "giacu", "ngayapdung", "lichsugia", "banggia"),
    "expenses": ("chiphi", "khoanchi", "muc"),
    "delivery": ("giaohang", "langiao", "luotgiao", "dotgiao", "bienso", "nguoicho", "ngaycho"),
    "repair_log": ("lamgi", "bienso", "dongxe", "phieusua", "suaxe"),
    "customers": ("khachhang", "makh", "sodienthoai", "dienthoai", "ngaydangky", "dangky"),
    "conversion": ("quydoi", "heso", "tilequydoi", "donvilon"),
    "mom_baby": ("sosize", "size", "voucher", "phiship", "diem"),
}


def table_roles(conn, t: str, cols: list[Col], rows: int) -> list[str]:
    tkey = _key(t)
    keys = {k for c in cols for k in c.keys} | {tkey}
    has = lambda *ws: any(w in k for k in keys for w in ws)  # noqa: E731
    roles = []
    date = any(_date_rank(c) >= 2 for c in cols)
    money = any(_line_money_rank(c) for c in cols)
    uniq_id = any(_is_order_id(c) and c.n_distinct >= 0.95 * rows for c in cols)
    rep_id = any(_is_order_id(c) and 0 < c.n_distinct < 0.95 * rows for c in cols)
    qty = any(c.kind == "number" and c.starts("soluong", "sl", "qty") for c in cols)
    prod = any(_is_product_code(c) or _is_product_name(c) for c in cols)
    if date and money and uniq_id and not has("nhap"):
        roles.append("orders")
    if (rep_id or not uniq_id) and prod and (qty or money) and not has("nhap", "tonkho", "giaohang", "quydoi"):
        roles.append("order_lines" + ("(+date: flat)" if date else ""))
    if prod and not date and not money and not qty:
        roles.append("catalog")
    for role, ws in ROLE_HINTS.items():
        if has(*ws):
            roles.append(role)
    return roles or ["?"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db-dir", action="append", required=True)
    p.add_argument("--detail", action="store_true")
    a = p.parse_args()
    role_biz: dict[str, set] = defaultdict(set)
    for d in a.db_dir:
        for db in sorted(Path(d).glob("*.db")):
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            cols: dict[str, list[Col]] = defaultdict(list)
            for t, n, o, k, nd, top in conn.execute(
                    "SELECT table_name, column_name, original_name, kind, n_distinct, top_values FROM _meta_columns"):
                cols[t].append(Col(t, n, o, k, nd, top))
            sizes = dict(conn.execute("SELECT table_name, row_count FROM _meta_tables"))
            if a.detail:
                print(f"== {db.stem}")
            for t in sorted(cols):
                r = table_roles(conn, t, cols[t], sizes.get(t, 0))
                for role in r:
                    role_biz[role.split("(")[0]].add(db.stem)
                if a.detail:
                    print(f"   {t:<32}{sizes.get(t, 0):>8}  {', '.join(r):<45} cols: {', '.join(c.name for c in cols[t])[:110]}")
            conn.close()
    print(f"\n{'vai trò':<16}{'số biz':>7}  biz")
    for role, bs in sorted(role_biz.items(), key=lambda kv: -len(kv[1])):
        print(f"{role:<16}{len(bs):>7}  {', '.join(sorted(bs))[:150]}")


if __name__ == "__main__":
    main()
