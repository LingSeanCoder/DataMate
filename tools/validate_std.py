"""Phase 1.4: đối soát std_* cho mọi biz (không cần gold).

So doanh thu std (cộng từ dòng hàng) với cột TỔNG TIỀN CẤP ĐƠN do chính file ghi (nguồn độc lập).
Lệch nhỏ là bình thường (tổng đơn có thể gồm ship / trừ giảm cả đơn); lệch > 5% -> CẢNH BÁO.

    python validate_std.py --db-dir tmp_db_dev
"""
from __future__ import annotations
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))  # dùng module trong src/

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

from std_layer import _load_cols, sql_quote

TOTAL_HINTS = ("tongtienhang", "tienhang", "tongtien", "tongphieu", "thanhtoan", "tongbill", "phaitra", "phaithu",
               "tongdon", "tongsaugiam", "thanhtien", "tongcong", "giatrihang")


def _one(conn, sql, default=None):
    try:
        return conn.execute(sql).fetchone()
    except sqlite3.Error:
        return default


def order_level_check(conn, meta: dict) -> tuple[str, float | None]:
    """Cột tổng cấp đơn khớp nhất với std revenue (đơn hợp lệ): (tên cột, % lệch)."""
    cols = _load_cols(conn)
    std = pd.read_sql_query("SELECT order_id, revenue FROM std_orders WHERE is_valid = 1", conn)
    if std.empty:
        return "", None
    std_sum = std["revenue"].sum()
    best = ("", None)
    for table_key, oid_key in (("header_table", "order.order_id"), ("line_table", "line.order_id")):
        t, oid = meta.get(table_key), meta.get(oid_key)
        if not t or not oid or t not in cols:
            continue
        for c in cols[t]:
            if c.kind != "number" or c.name == meta.get("line.revenue"):
                continue
            if not (c.role in ("order_level", "order_goods", "amount_due") or c.role in (None, "other") and c.has(*TOTAL_HINTS)):
                continue
            df = pd.read_sql_query(f"SELECT {sql_quote(oid)} AS order_id, {sql_quote(c.name)} AS v FROM {sql_quote(t)}", conn)
            per = df.dropna(subset=["v"]).groupby(df["order_id"].astype(str))["v"].first()
            tot = std["order_id"].astype(str).map(per).sum()
            if tot:
                diff = 100 * (std_sum - tot) / tot
                if best[1] is None or abs(diff) < abs(best[1]):
                    best = (f"{t}.{c.name}", diff)
    return best


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db-dir", required=True)
    a = p.parse_args()
    print(f"{'biz':<9}{'đơn':>8}{'hợp lệ':>8}{'dòng':>8}{'doanh thu std':>17}  {'đối soát cột cấp đơn':<42}{'lệch':>8}  ghi chú")
    n_ok = n_all = 0
    for db in sorted(Path(a.db_dir).glob("*.db")):
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        meta = dict(_one(conn, "SELECT 1") and conn.execute("SELECT role, source FROM _meta_std").fetchall() or [])
        o = _one(conn, "SELECT COUNT(*), SUM(is_valid), SUM(CASE WHEN is_valid=1 THEN revenue END) FROM std_orders", (0, 0, 0))
        nl = (_one(conn, "SELECT COUNT(*) FROM std_lines", (0,)) or (0,))[0]
        extra = []
        rep = _one(conn, "SELECT COUNT(*), SUM(is_warranty) FROM std_repairs")
        if rep:
            extra.append(f"std_repairs {rep[0]} phiếu ({rep[1]} bảo hành)")
        adj = _one(conn, "SELECT COUNT(*), SUM(adjust_amount) FROM std_lines WHERE line_kind = 'adjustment'")
        if adj and adj[0]:
            extra.append(f"{adj[0]} dòng điều chỉnh ({adj[1]:,.0f}đ)")
        warns = [v for k, v in meta.items() if k.startswith("warning")]
        col, diff = ("", None)
        if o and o[0]:
            col, diff = order_level_check(conn, meta)
        ok = bool(o and o[0] and o[0] >= 100 and (o[1] or 0) > 0 and (diff is None or abs(diff) <= 5))
        n_all += 1
        n_ok += ok
        flag = "" if ok else "  <-- CẢNH BÁO"
        dtxt = f"{diff:+.2f}%" if diff is not None else "-"
        print(f"{db.stem:<9}{o[0] or 0:>8}{o[1] or 0:>8}{nl:>8}{(o[2] or 0):>17,.0f}  {col or '(không có cột tổng cấp đơn)':<42}"
              f"{dtxt:>8}  {'; '.join(extra + warns)}{flag}")
        conn.close()
    print(f"\n=== {n_ok}/{n_all} biz đạt (>=100 đơn, có đơn hợp lệ, lệch cột cấp đơn <= 5% nếu có) ===")


if __name__ == "__main__":
    main()
