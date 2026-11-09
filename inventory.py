"""Build DB cho mọi biz trong một thư mục data và in bảng kiểm kê schema (Phase 0.1).

    python inventory.py --data-root ../DATASET/public_test/data --db-dir tmp_db_dev [--skip-build]

Không phụ thuộc tên biz: quét mọi thư mục con có file dữ liệu.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import traceback
from pathlib import Path

from data_loader import build_database


def _q(conn, sql, default=None):
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.Error:
        return default


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-root", required=True)
    p.add_argument("--db-dir", required=True)
    p.add_argument("--skip-build", action="store_true")
    a = p.parse_args()

    root, out = Path(a.data_root), Path(a.db_dir)
    out.mkdir(parents=True, exist_ok=True)
    bizs = sorted(d for d in root.iterdir() if d.is_dir())
    rows, fails = [], []
    for d in bizs:
        db = out / f"{d.name}.db"
        n_raw = sum(1 for f in d.iterdir() if f.is_file() and f.suffix.lower() in (".xlsx", ".xls", ".csv", ".sqlite", ".db"))
        if not a.skip_build:
            t0 = time.time()
            try:
                build_database(d, db, d / "README.md" if (d / "README.md").exists() else None, verbose=False)
            except Exception as exc:  # noqa: BLE001 - báo rõ biz nào hỏng
                fails.append((d.name, repr(exc)))
                traceback.print_exc()
                continue
            secs = time.time() - t0
        else:
            secs = 0.0
        conn = sqlite3.connect(db)
        meta = dict(_q(conn, "SELECT role, source FROM _meta_std", []) or [])
        n_tables = (_q(conn, "SELECT COUNT(*) FROM _meta_tables", [[0]]) or [[0]])[0][0]
        n_orders = (_q(conn, "SELECT COUNT(*), SUM(is_valid) FROM std_orders", [[0, 0]]) or [[0, 0]])[0]
        n_lines = (_q(conn, "SELECT COUNT(*) FROM std_lines", [[0]]) or [[0]])[0][0]
        span = (_q(conn, "SELECT MIN(order_date), MAX(order_date) FROM std_orders", [[None, None]]) or [[None, None]])[0]
        report = json.loads((out / f"{d.name}.report.json").read_text(encoding="utf-8")) if (out / f"{d.name}.report.json").exists() else {}
        warns = [w for w in report.get("warnings", []) if "std" in w] + [v for k, v in meta.items() if k.startswith("warning")]
        main_t = meta.get("header_table") or meta.get("line_table") or "?"
        date_c = meta.get("order.date") or "(từ bảng dòng)"
        money_c = meta.get("line.revenue") or meta.get("order.revenue") or "?"
        rows.append((d.name, n_raw, n_tables, main_t, meta.get("line_table", ""), n_orders[0], n_orders[1] or 0, n_lines,
                     date_c, money_c, f"{span[0]}..{span[1]}", f"{secs:.0f}s", "; ".join(warns) or meta.get("error", "")))
        conn.close()

    hdr = ("biz", "raw", "bảng", "bảng đơn", "bảng dòng", "std_orders", "hợp lệ", "std_lines", "cột ngày", "cột tiền", "khoảng ngày", "build", "cảnh báo")
    print(" | ".join(hdr))
    for r in rows:
        print(" | ".join(str(x) for x in r))
    print(f"\n=== build OK {len(rows)}/{len(bizs)} ===")
    for name, err in fails:
        print(f"LỖI {name}: {err}")


if __name__ == "__main__":
    main()
