"""Hàm tính số liệu trên bảng std_* theo rulebook (không LLM, deterministic).

Mọi hàm nhận `conn` (sqlite3, đã build std_*), kỳ `start`/`end` ('YYYY-MM-DD', start=None = từ đầu dữ liệu)
và `as_of`: bản ghi sau hôm nay coi như chưa xảy ra (rulebook đầu file). Trả số thô (chưa làm tròn);
formatter.shape_answer lo làm tròn/format. Kỳ nằm hoàn toàn ngoài dữ liệu -> raise NoData (=> unanswerable, §7).

Bộ lọc dùng chung (`F`): product (tên), category, brand, size, unit (đơn vị cơ bản), channel, payment, employee,
customer_group, customer_key, shift ('sang'|'chieu'), ingredient (hoạt chất), prescription (True/False).
Giá trị lọc là list (OR trong cùng thuộc tính) hoặc 1 giá trị.
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from datetime import date, timedelta

from data_loader import strip_diacritics


class NoData(Exception):
    """Kỳ hỏi nằm hoàn toàn ngoài khoảng dữ liệu, hoặc shop không ghi loại thông tin cần (§7)."""


class Unsure(Exception):
    """Dữ liệu có nhưng chưa tính chắc được (vd nợ chỉ có ảnh chụp cuối dữ liệu mà hỏi ngày trước đó) -> bỏ trống."""


LINE_FILTERS = ("product", "category", "exclude_category", "brand", "size", "unit", "ingredient", "prescription")
SHIFTS = {"sang": ("06:00:00", "13:59:59"), "chieu": ("14:00:00", "21:59:59")}


def _list(v):
    return None if v in (None, [], "") else (list(v) if isinstance(v, (list, tuple, set)) else [v])


def _ukey(s) -> str:
    return strip_diacritics(str(s or "")).lower().strip()


def _cols(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def data_span(conn, as_of: str) -> tuple[str, str]:
    lo, hi = conn.execute("SELECT MIN(order_date), MAX(order_date) FROM std_orders WHERE order_date <= ?", (as_of,)).fetchone()
    if lo is None:
        raise NoData("không có đơn")
    return lo, hi


def clip(conn, start, end, as_of: str) -> tuple[str, str]:
    """Kỳ ∩ [đầu dữ liệu, hôm nay]; kỳ hoàn toàn ngoài dữ liệu -> NoData (§7)."""
    lo, hi = data_span(conn, as_of)
    s = max(start or lo, lo)
    e = min(end or as_of, as_of)
    if s > e or s > hi or (start and end and end < lo):
        raise NoData(f"kỳ {start}..{end} ngoài dữ liệu {lo}..{hi}")
    return s, e


# ----------------------------- bộ lọc ------------------------------------------------

def _where_orders(conn, F: dict, args: list) -> list[str]:
    w = []
    for key, col in (("channel", "o.channel"), ("payment", "o.pay_method"), ("employee", "o.employee_id"),
                     ("customer_key", "o.customer_key")):
        vals = _list(F.get(key))
        if vals:
            w.append(f"{col} IN ({','.join('?' * len(vals))})")
            args += vals
    groups = _list(F.get("customer_group"))
    if groups:
        w.append(f"o.customer_key IN (SELECT customer_key FROM std_customers WHERE customer_group IN ({','.join('?' * len(groups))}))")
        args += groups
    if F.get("shift"):
        if not conn.execute("SELECT 1 FROM std_orders WHERE order_time IS NOT NULL AND order_time != '' LIMIT 1").fetchone():
            raise NoData("shop không ghi giờ đơn")
        a, b = SHIFTS[F["shift"]]
        w.append("substr(o.order_time, 1, 8) BETWEEN ? AND ?")
        args += [a, b]
    return w


def _where_lines(conn, F: dict, args: list) -> list[str]:
    w = []
    names = _list(F.get("product"))
    if names:
        w.append(f"l.product_name IN ({','.join('?' * len(names))})")
        args += names
    excl = _list(F.get("exclude_category"))
    if excl:
        w.append(f"COALESCE(lower(l.category), '') NOT IN ({','.join('?' * len(excl))})")
        args += [c.lower() for c in excl]
    cats = _list(F.get("category"))
    if cats:
        w.append(f"lower(l.category) IN ({','.join('?' * len(cats))})")
        args += [c.lower() for c in cats]
    for key, col in (("brand", "brand"), ("size", "size"), ("ingredient", "active_ingredient")):
        vals = _list(F.get(key))
        if vals:
            w.append(f"l.product_name IN (SELECT product_name FROM std_products WHERE {col} IN ({','.join('?' * len(vals))}))")
            args += vals
    if F.get("prescription") is not None:
        w.append(f"l.product_name {'' if F['prescription'] else 'NOT '}IN "
                 "(SELECT product_name FROM std_products WHERE is_prescription = 1)")
    units = _list(F.get("unit"))
    if units and base_units(conn):  # shop không ghi đơn vị: đơn vị trong câu chỉ là cách đếm, không lọc
        ids = [n for n, u in base_units(conn).items() if _ukey(u) in {_ukey(x) for x in units}]
        w.append(f"l.product_name IN ({','.join('?' * len(ids)) or 'NULL'})")
        args += ids
    return w


_BU_CACHE: dict[str, dict] = {}


def base_units(conn) -> dict[str, str]:
    """tên hàng -> đơn vị cơ bản: danh mục nếu có, không thì đơn vị của dòng bán có qty == qty_base (mã hàng có thể trống).
    Cache theo file DB (DB chỉ đọc trong lúc trả lời)."""
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    if path and path in _BU_CACHE:
        return _BU_CACHE[path]
    out = {}
    if "base_unit" in _cols(conn, "std_products"):
        out.update(conn.execute("SELECT product_name, base_unit FROM std_products WHERE base_unit IS NOT NULL AND base_unit != ''").fetchall())
    for p, u in conn.execute("SELECT product_name, unit FROM std_lines WHERE qty = qty_base AND unit IS NOT NULL "
                             "GROUP BY product_name, unit ORDER BY COUNT(*) DESC"):
        out.setdefault(p, u)
    if path:
        _BU_CACHE[path] = out  # chỉ lưu khi đã điền xong: main.py chạy nhiều luồng
    return out


def _has_line_filter(F: dict) -> bool:
    return any(F.get(k) not in (None, [], "") for k in LINE_FILTERS)


# ----------------------------- đơn / dòng ------------------------------------------

def _order_rows(conn, start, end, as_of, F: dict, valid_only=True):
    s, e = clip(conn, start, end, as_of)
    args = [s, e]
    w = ["o.order_date BETWEEN ? AND ?"] + (["o.is_valid = 1"] if valid_only else []) + _where_orders(conn, F, args)
    return " AND ".join(w), args


def revenue(conn, start, end, as_of, **F) -> float:
    """§1: doanh thu đơn không huỷ/không trả. Lọc mặt hàng/nhóm -> cộng thành tiền dòng (gồm phí phục vụ nếu không lọc)."""
    w, args = _order_rows(conn, start, end, as_of, F)
    if _has_line_filter(F):
        args2 = list(args)
        lw = _where_lines(conn, F, args2)
        return conn.execute(f"SELECT COALESCE(SUM(l.line_revenue), 0) FROM std_lines l JOIN std_orders o USING(order_id) "
                            f"WHERE {w} AND {' AND '.join(lw)}", args2).fetchone()[0]
    return conn.execute(f"SELECT COALESCE(SUM(o.revenue), 0) FROM std_orders o WHERE {w}", args).fetchone()[0]


def count_orders(conn, start, end, as_of, min_amount=None, **F) -> int:
    """§1 số đơn hợp lệ; lọc mặt hàng = số đơn có ít nhất 1 dòng khớp; min_amount: tiền hàng >= ngưỡng."""
    w, args = _order_rows(conn, start, end, as_of, F)
    if min_amount is not None:
        w += " AND o.revenue >= ?"
        args.append(min_amount)
    if _has_line_filter(F):
        lw = _where_lines(conn, F, args)
        w += f" AND o.order_id IN (SELECT l.order_id FROM std_lines l WHERE {' AND '.join(lw)})"
    return conn.execute(f"SELECT COUNT(*) FROM std_orders o WHERE {w}", args).fetchone()[0]


def avg_per_order(conn, start, end, as_of, **F) -> float:
    n = count_orders(conn, start, end, as_of, **F)
    if not n:
        raise NoData("không có đơn")
    return revenue(conn, start, end, as_of, **F) / n


def qty(conn, start, end, as_of, **F) -> float:
    """§3: số lượng quy về đơn vị cơ bản, đơn hợp lệ, bỏ phí phục vụ."""
    w, args = _order_rows(conn, start, end, as_of, F)
    lw = _where_lines(conn, F, args)
    return conn.execute(f"SELECT COALESCE(SUM(l.qty_base), 0) FROM std_lines l JOIN std_orders o USING(order_id) "
                        f"WHERE {w} AND l.is_service_fee = 0 AND COALESCE(l.line_kind, 'item') = 'item' {''.join(' AND ' + x for x in lw)}", args).fetchone()[0]


def cancel_rate(conn, start, end, as_of, **F) -> float:
    """§1: % đơn huỷ trên mọi đơn đặt trong kỳ (mọi trạng thái)."""
    w, args = _order_rows(conn, start, end, as_of, F, valid_only=False)
    n, c = conn.execute(f"SELECT COUNT(*), SUM(o.status_norm = 'huỷ') FROM std_orders o WHERE {w}", args).fetchone()
    if not n:
        raise NoData("không có đơn")
    return 100.0 * (c or 0) / n


def cancel_count(conn, start, end, as_of, **F) -> int:
    w, args = _order_rows(conn, start, end, as_of, F, valid_only=False)
    return conn.execute(f"SELECT COUNT(*) FROM std_orders o WHERE {w} AND o.status_norm = 'huỷ'", args).fetchone()[0]


def profit_gross(conn, start, end, as_of, **F) -> float:
    """§5: doanh thu − giá vốn hàng đã bán (giá vốn ghi trên dòng bán), đơn hợp lệ."""
    w, args = _order_rows(conn, start, end, as_of, F)
    lw = _where_lines(conn, F, args)
    if conn.execute("SELECT COUNT(line_cost) FROM std_lines").fetchone()[0] == 0:
        raise NoData("shop không ghi giá vốn")
    return conn.execute(f"SELECT COALESCE(SUM(l.line_revenue - COALESCE(l.line_cost, 0)), 0) FROM std_lines l "
                        f"JOIN std_orders o USING(order_id) WHERE {w} {''.join(' AND ' + x for x in lw)}", args).fetchone()[0]


def pct_change(cur: float, base: float) -> float:
    """§1: (kỳ hỏi − kỳ so) / kỳ so × 100; giảm ra số âm."""
    if not base:
        raise NoData("kỳ so bằng 0")
    return (cur - base) * 100.0 / base


def share(part: float, whole: float) -> float:
    if not whole:
        raise NoData("mẫu số bằng 0")
    return part * 100.0 / whole


def best_period(conn, start, end, as_of, grain="day", measure="revenue", lowest=False, **F) -> str:
    """Ngày/tháng có doanh thu (hoặc số đơn) cao nhất trong kỳ; bằng nhau lấy sớm nhất."""
    w, args = _order_rows(conn, start, end, as_of, F)
    key = "o.order_date" if grain == "day" else "substr(o.order_date, 1, 7)"
    val = "SUM(o.revenue)" if measure == "revenue" else "COUNT(*)"
    if _has_line_filter(F):
        lw = _where_lines(conn, F, args)
        val = "SUM(l.line_revenue)" if measure == "revenue" else "COUNT(DISTINCT o.order_id)"
        sql = f"SELECT {key}, {val} FROM std_lines l JOIN std_orders o USING(order_id) WHERE {w} AND {' AND '.join(lw)} GROUP BY 1"
    else:
        sql = f"SELECT {key}, {val} FROM std_orders o WHERE {w} GROUP BY 1"
    rows = conn.execute(sql, args).fetchall()
    if not rows:
        raise NoData("không có đơn")
    return sorted(rows, key=lambda r: ((r[1] if lowest else -r[1]), r[0]))[0][0]


def series(conn, periods: list[tuple[str, str]], as_of, fn=revenue, **F) -> list:
    return [fn(conn, s, e, as_of, **F) for s, e in periods]


# ----------------------------- xếp hạng ---------------------------------------------

def top_products(conn, start, end, as_of, k=1, by="qty", lowest=False, **F) -> list[dict]:
    """§3: bán chạy theo qty_base; 'nhiều tiền' theo doanh thu dòng; 'lời nhất' theo lãi gộp. Bỏ phí phục vụ."""
    w, args = _order_rows(conn, start, end, as_of, F)
    lw = _where_lines(conn, F, args)
    val = {"qty": "SUM(l.qty_base)", "revenue": "SUM(l.line_revenue)",
           "profit": "SUM(l.line_revenue - COALESCE(l.line_cost, 0))"}[by]  # line_cost = qty × giá vốn/đơn vị bán (std)
    rows = conn.execute(f"SELECT l.product_name, MIN(l.product_id), {val} FROM std_lines l JOIN std_orders o USING(order_id) "
                        f"WHERE {w} AND l.is_service_fee = 0 AND COALESCE(l.line_kind, 'item') = 'item' {''.join(' AND ' + x for x in lw)} GROUP BY l.product_name",
                        args).fetchall()
    items = [{"name": n, "code": c, "value": v} for n, c, v in rows]
    return _rank(items, k, lowest)


def _rank(items, k, lowest=False):
    items = sorted(items, key=lambda it: ((it["value"] if lowest else -it["value"]), str(it.get("code") or it["name"])))
    return items[:k] if k else items


def top_customers(conn, start, end, as_of, k=1, by="revenue", **F) -> list[dict]:
    """§3: mỗi customer_key là một khách; khách lẻ (không key) không xếp hạng."""
    w, args = _order_rows(conn, start, end, as_of, F)
    val = "SUM(o.revenue)" if by == "revenue" else "COUNT(*)"
    rows = conn.execute(f"SELECT o.customer_key, COALESCE(MIN(c.customer_name), MIN(o.customer_name)), MIN(o.customer_id), {val} "
                        f"FROM std_orders o LEFT JOIN std_customers c USING(customer_key) "
                        f"WHERE {w} AND o.customer_key IS NOT NULL AND o.customer_key != '' GROUP BY o.customer_key", args).fetchall()
    # "Vương Phương Loan - 0900 000 001" -> tên (§9 nhận cả hai, gold ghi tên)
    return _rank([{"name": re.sub(r"\s*[-–(,]\s*\+?[\d .]{8,}\)?\s*$", "", n) if n else n, "code": cid or key, "key": key, "value": v}
                  for key, n, cid, v in rows], k)


def _need_customers(conn):
    if not conn.execute("SELECT 1 FROM std_orders WHERE customer_key IS NOT NULL AND customer_key != '' LIMIT 1").fetchone():
        raise NoData("shop không ghi khách")


def customers_with_orders(conn, start, end, as_of, min_orders=1, strict=False, **F) -> int:
    _need_customers(conn)
    w, args = _order_rows(conn, start, end, as_of, F)
    op = ">" if strict else ">="
    return conn.execute(f"SELECT COUNT(*) FROM (SELECT o.customer_key FROM std_orders o WHERE {w} AND o.customer_key IS NOT NULL "
                        f"AND o.customer_key != '' GROUP BY 1 HAVING COUNT(*) {op} ?)", args + [min_orders]).fetchone()[0]


def top_employees(conn, start, end, as_of, k=1, **F) -> list[dict]:
    """§3: nhân viên bán giỏi nhất theo doanh thu."""
    w, args = _order_rows(conn, start, end, as_of, F)
    rows = conn.execute(f"SELECT o.employee_id, SUM(o.revenue) FROM std_orders o WHERE {w} AND o.employee_id IS NOT NULL "
                        "AND o.employee_id != '' GROUP BY 1", args).fetchall()
    if not rows:
        raise NoData("shop không ghi nhân viên")
    return _rank([{"name": e, "code": e, "value": v} for e, v in rows], k)


def products_in_n_orders(conn, start, end, as_of, n: int, strict=False, **F) -> int:
    """Số mặt hàng có mặt trong >= n đơn hợp lệ khác nhau."""
    w, args = _order_rows(conn, start, end, as_of, F)
    op = ">" if strict else ">="
    return conn.execute(f"SELECT COUNT(*) FROM (SELECT l.product_name FROM std_lines l JOIN std_orders o USING(order_id) "
                        f"WHERE {w} AND l.is_service_fee = 0 AND COALESCE(l.line_kind, 'item') = 'item' GROUP BY 1 HAVING COUNT(DISTINCT o.order_id) {op} ?)",
                        args + [n]).fetchone()[0]


def unsold_items(conn, start, end, as_of, **F) -> list[dict]:
    """§5: mặt hàng đã từng bán trước kỳ (đơn hợp lệ) mà trong kỳ không có đơn hợp lệ nào. Thứ tự theo mã."""
    s, e = clip(conn, start, end, as_of)
    args = [s]
    lw = _where_lines(conn, F, args)
    before = conn.execute("SELECT l.product_name, MIN(l.product_id) FROM std_lines l JOIN std_orders o USING(order_id) "
                          f"WHERE o.is_valid = 1 AND o.order_date < ? AND l.is_service_fee = 0 AND COALESCE(l.line_kind, 'item') = 'item' {''.join(' AND ' + x for x in lw)} "
                          "GROUP BY 1", args).fetchall()
    sold = {r[0] for r in conn.execute("SELECT DISTINCT l.product_name FROM std_lines l JOIN std_orders o USING(order_id) "
                                       "WHERE o.is_valid = 1 AND o.order_date BETWEEN ? AND ?", (s, e))}
    return sorted(({"name": n, "code": c} for n, c in before if n not in sold), key=lambda d: str(d["code"] or d["name"]))


def day_count(conn, start, end, as_of, threshold: float, op=">=", measure="revenue", **F) -> int:
    """Số ngày trong kỳ có doanh thu/số đơn thoả ngưỡng (ngày không có đơn = 0)."""
    s, e = clip(conn, start, end, as_of)
    w, args = _order_rows(conn, s, e, as_of, F)
    val = "SUM(o.revenue)" if measure == "revenue" else "COUNT(*)"
    by_day = dict(conn.execute(f"SELECT o.order_date, {val} FROM std_orders o WHERE {w} GROUP BY 1", args).fetchall())
    d0, d1 = date.fromisoformat(s), date.fromisoformat(e)
    days = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    cmp = {">=": lambda v: v >= threshold, ">": lambda v: v > threshold, "<": lambda v: v < threshold,
           "<=": lambda v: v <= threshold}[op]
    return sum(cmp(by_day.get(d, 0)) for d in days)


def unit_price(conn, product: str, day: str) -> float:
    """§5: đơn giá một đơn vị cơ bản trên dòng bán ngày D, trước chiết khấu dòng, tính cả đơn huỷ/trả.
    shortcut: std_lines không giữ đơn giá gốc -> lấy giá cao nhất (dòng không chiết khấu); thêm cột unit_price nếu sai."""
    v = conn.execute("SELECT MAX(l.line_revenue / NULLIF(l.qty_base, 0)) FROM std_lines l JOIN std_orders o USING(order_id) "
                     "WHERE l.product_name = ? AND o.order_date = ?", (product, day)).fetchone()[0]
    if v is None:
        raise NoData("không bán ngày đó")
    return v


# ----------------------------- tăng giá ---------------------------------------------

def price_hike_change(conn, product: str, n: int, as_of, hike_date: str | None = None, new_price: float | None = None,
                      metric="qty") -> float:
    """§5: so số lượng N ngày kể từ D (kể cả D) với N ngày ngay trước D. D cho sẵn hoặc tìm theo giá trên dòng bán."""
    if hike_date is None:
        hike_date = find_hike_date(conn, product, as_of, new_price)
    d = date.fromisoformat(hike_date)
    after = qty(conn, d.isoformat(), (d + timedelta(days=n - 1)).isoformat(), as_of, product=product)
    before = qty(conn, (d - timedelta(days=n)).isoformat(), (d - timedelta(days=1)).isoformat(), as_of, product=product)
    return pct_change(after, before)


def _price_history(conn, product: str) -> list[tuple[str, float]]:
    """Bảng giá / lịch sử giá: [(ngày áp dụng, giá mới)] theo thời gian; khớp hàng theo tên hoặc mã."""
    pid = conn.execute("SELECT product_id FROM std_products WHERE product_name = ?", (product,)).fetchone()
    keys = {_ukey(product)} | ({_ukey(pid[0])} if pid and pid[0] else set())
    for (t,) in conn.execute("SELECT DISTINCT table_name FROM _meta_columns WHERE replace(column_name, '_', '') LIKE '%ngayapdung%'"):
        cols = [r[0] for r in conn.execute("SELECT column_name FROM _meta_columns WHERE table_name = ?", (t,))]
        day = next(c for c in cols if "ngay" in c and "ap" in c)
        price = next((c for c in cols if re.sub("_", "", c) in ("giamoi", "giabanle", "giaban", "gia")), None)
        if not price:
            continue
        out = []
        for c in cols:
            if c in (day, price):
                continue
            rows = conn.execute(f'SELECT "{c}", "{day}", "{price}" FROM "{t}"').fetchall()
            out = sorted((str(d)[:10], float(p)) for r, d, p in rows if _ukey(r) in keys and p is not None and d)
            if out:
                return out
    return []


def find_hike_date(conn, product: str, as_of, new_price: float | None = None) -> str:
    """§5: lần tăng giá = ngày D giá mới cao hơn giá liền trước (theo bảng giá); cùng một giá nhiều lần thì lần đầu.
    Không có bảng giá thì dò đơn giá trên dòng bán."""
    hist = [(d, p) for d, p in _price_history(conn, product) if d <= as_of]
    for (d0, p0), (d1, p1) in zip(hist, hist[1:]):
        if p1 > p0 and (new_price is None or abs(p1 - new_price) < 1):
            return d1
    rows = conn.execute("SELECT o.order_date, MAX(l.line_revenue / NULLIF(l.qty_base, 0)) FROM std_lines l JOIN std_orders o "
                        "USING(order_id) WHERE l.product_name = ? AND o.order_date <= ? GROUP BY 1 ORDER BY 1",
                        (product, as_of)).fetchall()
    prev = None
    for day, p in rows:
        if p is None:
            continue
        p = round(p)
        if prev is not None and p > prev and (new_price is None or abs(p - new_price) < 1):
            return day
        prev = p if prev is None or p >= prev else prev
    raise NoData("không thấy ngày tăng giá")


# ----------------------------- nhập hàng, tồn, nợ ----------------------------------

def _need(conn, table: str):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone() or \
            not conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone():
        raise NoData(f"shop không có {table}")


def purchase_total(conn, start, end, as_of, supplier=None, product=None) -> float:
    """§5: tổng tiền phiếu nhập có ngày phiếu trong kỳ, bỏ phiếu huỷ."""
    _need(conn, "std_purchases")
    e = min(end or as_of, as_of)
    args = [start or "0000", e]
    w = "purchase_date BETWEEN ? AND ? AND COALESCE(is_cancelled, 0) = 0"
    for key, col in (("supplier", supplier), ("product", product)):
        vals = _list(col)
        if vals:
            w += f" AND {'supplier' if key == 'supplier' else 'product_name'} IN ({','.join('?' * len(vals))})"
            args += vals
    return conn.execute(f"SELECT COALESCE(SUM(COALESCE(line_total, qty * unit_cost)), 0) FROM std_purchases WHERE {w}",
                        args).fetchone()[0]


def top_suppliers(conn, start, end, as_of, k=1) -> list[dict]:
    _need(conn, "std_purchases")
    rows = conn.execute("SELECT supplier, SUM(COALESCE(line_total, qty * unit_cost)) FROM std_purchases WHERE purchase_date "
                        "BETWEEN ? AND ? AND COALESCE(is_cancelled, 0) = 0 AND supplier IS NOT NULL GROUP BY 1",
                        (start or "0000", min(end or as_of, as_of))).fetchall()
    return _rank([{"name": s, "code": s, "value": v} for s, v in rows], k)


def purchase_avg_price(conn, product: str, start, end, as_of) -> float:
    """§5: bình quân theo số lượng nhập (theo đơn vị nhập), bỏ phiếu huỷ."""
    _need(conn, "std_purchases")
    q, t = conn.execute("SELECT SUM(qty), SUM(COALESCE(line_total, qty * unit_cost)) FROM std_purchases WHERE product_name = ? "
                        "AND purchase_date BETWEEN ? AND ? AND COALESCE(is_cancelled, 0) = 0",
                        (product, start or "0000", min(end or as_of, as_of))).fetchone()
    if not q:
        raise NoData("không có phiếu nhập")
    if t is None:
        raise NoData("phiếu nhập không ghi giá")
    return t / q


_UF_CACHE: dict = {}


def unit_factor(conn, product: str, unit: str) -> float | None:
    """Số đơn vị cơ bản trong 1 `unit` của mặt hàng (§10): bảng quy đổi, không có thì từ dòng bán; đơn vị cơ bản = 1."""
    key = (conn.execute("PRAGMA database_list").fetchone()[2], product, unit)
    if key not in _UF_CACHE:
        _UF_CACHE[key] = _unit_factor(conn, product, unit)
    return _UF_CACHE[key]


def _unit_factor(conn, product: str, unit: str) -> float | None:
    if _ukey(unit) == _ukey(base_units(conn).get(product)):
        return 1.0
    try:
        for sp, big, f in conn.execute("SELECT san_pham, don_vi_lon, he_so FROM _quy_doi"):
            if _ukey(sp) == _ukey(product) and _ukey(big) == _ukey(unit) and f:
                return float(f)
    except sqlite3.Error:
        pass
    r = conn.execute("SELECT qty_base / qty FROM std_lines WHERE product_name = ? AND qty > 0 AND qty_base > 0 AND unit IS NOT NULL "
                     "AND lower(unit) = lower(?) LIMIT 1", (product, unit)).fetchone()
    return r[0] if r else None


def stock_at(conn, product: str, day: str, as_of) -> float:
    """§2/§5: tồn (đơn vị cơ bản) lúc hết ngày `day`. Sổ thẻ kho nếu có; không thì ảnh chụp tồn cuối dữ liệu
    lùi về: + bán (đơn không huỷ) sau `day` − nhập (phiếu không huỷ) sau `day`."""
    day = min(day, as_of)
    key = _ukey(product)
    led = _ledger(conn)
    if led is not None:
        pid = conn.execute("SELECT product_id FROM std_products WHERE product_name = ?", (product,)).fetchone()
        hist = led.get(key) or (led.get(_ukey(pid[0])) if pid and pid[0] else None)
        bal = [b for ts, b in (hist or []) if ts[:10] <= day]
        if bal:
            return bal[-1]
    has_stock = conn.execute("SELECT 1 FROM sqlite_master WHERE name='std_stock'").fetchone()
    if not has_stock and conn.execute("SELECT 1 FROM sqlite_master WHERE name='std_purchases'").fetchone():
        return _stock_from_flows(conn, product, day)
    _need(conn, "std_stock")
    pid = conn.execute("SELECT product_id FROM std_products WHERE product_name = ?", (product,)).fetchone()
    snap = [(q, d) for n, i, q, d in conn.execute("SELECT product_name, product_id, qty_on_hand_base, snapshot_date FROM std_stock")
            if _ukey(n) == key or (pid and pid[0] and i == pid[0])]
    if not snap or snap[0][0] is None:
        raise NoData("không có tồn của mặt hàng")
    qty_end, snap_day = snap[0]
    if snap_day and snap_day > day:
        sold = conn.execute("SELECT COALESCE(SUM(l.qty_base), 0) FROM std_lines l JOIN std_orders o USING(order_id) "
                            "WHERE l.product_name = ? AND o.status_norm != 'huỷ' AND o.order_date > ? AND o.order_date <= ?",
                            (product, day, snap_day)).fetchone()[0]
        bought = 0.0
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='std_purchases'").fetchone():
            for q, u in conn.execute("SELECT qty, unit FROM std_purchases WHERE product_name = ? AND COALESCE(is_cancelled, 0) = 0 "
                                     "AND purchase_date > ? AND purchase_date <= ?", (product, day, snap_day)):
                bought += (q or 0) * (unit_factor(conn, product, u) or 1)
        qty_end = qty_end + sold - bought
    return qty_end


_LEDGER_CACHE: dict[str, dict | None] = {}


def _ledger(conn) -> dict | None:
    """Sổ thẻ kho: {khoá hàng: [(thời điểm, tồn sau phát sinh)] theo thời gian}; cache theo file DB."""
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    if path in _LEDGER_CACHE:
        return _LEDGER_CACHE[path]
    out = None
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='std_stock_ledger'").fetchone():
        out = defaultdict(list)
        for ref, ts, b in conn.execute("SELECT product_ref, ts, balance_base FROM std_stock_ledger ORDER BY ts"):
            out[_ukey(ref)].append((str(ts), b))
    _LEDGER_CACHE[path] = out
    return out


def _stock_from_flows(conn, product: str, day: str) -> float:
    """Không có bảng tồn: nhập (quy đơn vị cơ bản, phiếu không huỷ) − bán (đơn không huỷ; có bán theo lô thì trừ phần trả lại)."""
    bought = sum((q or 0) * (unit_factor(conn, product, u) or 1) for q, u in conn.execute(
        "SELECT qty, unit FROM std_purchases WHERE product_name = ? AND COALESCE(is_cancelled, 0) = 0 AND purchase_date <= ?",
        (product, day)))
    if not bought:
        raise NoData("không có phiếu nhập của mặt hàng")
    pid = conn.execute("SELECT product_id FROM std_products WHERE product_name = ?", (product,)).fetchone()
    if pid and pid[0] and conn.execute("SELECT 1 FROM sqlite_master WHERE name='std_lot_sales'").fetchone():
        sold = conn.execute("SELECT SUM(s.qty - COALESCE(s.qty_returned, 0)) FROM std_lot_sales s JOIN std_lots l USING(lot_id) "
                            "JOIN std_orders o USING(order_id) WHERE l.product_ref = ? AND o.status_norm != 'huỷ' AND o.order_date <= ?",
                            (pid[0], day)).fetchone()[0]
        if sold is not None:
            return bought - sold
    sold = conn.execute("SELECT COALESCE(SUM(l.qty_base), 0) FROM std_lines l JOIN std_orders o USING(order_id) "
                        "WHERE l.product_name = ? AND o.status_norm != 'huỷ' AND o.order_date <= ?", (product, day)).fetchone()[0]
    return bought - sold


def stock_out_count(conn, day: str, as_of) -> int:
    """§5: số mặt hàng mua về để bán (có phiếu nhập hoặc có trong bảng tồn) mà tồn lúc hết ngày = 0."""
    names = [r[0] for r in conn.execute("SELECT product_name FROM std_products")]
    n = 0
    for name in names:
        try:
            q = stock_at(conn, name, day, as_of)
        except NoData:
            continue
        n += q is not None and abs(q) < 1e-9
    return n


def stock_value(conn, day: str, as_of) -> float:
    """§5: Σ tồn (>0) × giá vốn một đơn vị cơ bản trong danh mục."""
    if not any(conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()
               for t in ("std_stock", "std_stock_ledger", "std_purchases", "std_lots")):
        raise NoData("shop không có dữ liệu kho")
    total = 0.0
    for name, cost in conn.execute("SELECT product_name, cost_price FROM std_products WHERE cost_price IS NOT NULL"):
        try:
            q = stock_at(conn, name, day, as_of)
        except NoData:
            continue
        if q and q > 0:
            total += q * cost
    return total


def lots_in_stock(conn, day: str, as_of) -> list[dict]:
    """Lô còn tồn lúc hết ngày `day` (đơn vị cơ bản): nhập − bán theo lô (đơn không huỷ, trừ phần trả lại)."""
    _need(conn, "std_lots")
    day = min(day, as_of)
    names = dict(conn.execute("SELECT product_id, product_name FROM std_products WHERE product_id IS NOT NULL").fetchall())
    sold = defaultdict(float)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='std_lot_sales'").fetchone():
        for lot, q in conn.execute("SELECT s.lot_id, SUM(s.qty - COALESCE(s.qty_returned, 0)) FROM std_lot_sales s JOIN std_orders o "
                                   "USING(order_id) WHERE o.status_norm != 'huỷ' AND o.order_date <= ? GROUP BY 1", (day,)):
            sold[lot] = q or 0
    cost = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='std_purchases'").fetchone():
        for doc, pid, name, uc, u in conn.execute("SELECT DISTINCT purchase_id, product_id, product_name, unit_cost, unit FROM std_purchases"):
            if uc:
                cost[(doc, pid)] = uc / (unit_factor(conn, name, u) or 1) if u else uc
    pdate = dict(conn.execute("SELECT purchase_id, MIN(purchase_date) FROM std_purchases GROUP BY 1").fetchall()) \
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='std_purchases'").fetchone() else {}
    out = []
    for lot, ref, exp, qin, uc, doc in conn.execute("SELECT lot_id, product_ref, expiry_date, qty_in, unit_cost, purchase_id FROM std_lots"):
        if pdate.get(doc) and pdate[doc] > day:
            continue  # lô nhập sau ngày hỏi
        rest = (qin or 0) - sold[lot]
        if rest > 0:
            out.append({"lot": lot, "code": ref, "name": names.get(ref, ref), "expiry": exp, "rest": rest,
                        "cost": uc if uc else cost.get((doc, ref))})
    return out


def nearest_expiry(conn, product: str, day: str, as_of) -> str:
    """§6 nhà thuốc: lô còn tồn, chưa quá hạn tại hôm nay, hạn sớm nhất."""
    lots = [l for l in lots_in_stock(conn, day, as_of) if l["name"] == product and l["expiry"] and l["expiry"] >= day]
    if not lots:
        raise NoData("không có lô còn tồn")
    return min(l["expiry"] for l in lots)


def expiring(conn, start: str, end: str, as_of) -> list[dict]:
    """Lô còn tồn (tại `start` = hôm nay) có hạn dùng trong [start, end]."""
    return [l for l in lots_in_stock(conn, start, as_of) if l["expiry"] and start <= l["expiry"] <= end]


def _is_cod(m) -> bool:
    return "cod" in _ukey(m) or "thu ho" in _ukey(m)


def _is_debt_method(m) -> bool:
    k = _ukey(m)
    return k in ("ghi no", "debt", "no", "cong no", "mua chiu", "tra sau")


def _is_marketplace(ch) -> bool:
    k = _ukey(ch)
    return "tmdt" in k or "san" in k.split() or any(x in k for x in ("shopee", "lazada", "tiki", "tiktok"))


def debt_orders(conn, upto: str, as_of) -> list[dict]:
    """§4: đơn không huỷ, không COD/TMĐT, đặt đến hết X; còn nợ = tổng đơn phải trả − tiền trả đến hết X (> 0)."""
    _need(conn, "std_order_money")
    upto = min(upto, as_of)
    has = lambda t: conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone() is not None  # noqa: E731
    if not has("std_payments"):
        if has("std_debt_book"):
            return _debt_from_books(conn, upto)
        snap = conn.execute("SELECT COUNT(debt_snapshot) FROM std_order_money").fetchone()[0]
        last = conn.execute("SELECT MAX(order_date) FROM std_orders").fetchone()[0]
        if not snap or upto < last:
            raise Unsure("không có phiếu thu; nợ chỉ có ảnh chụp cuối dữ liệu")
    paid = defaultdict(float)
    snapshot = not has("std_payments")
    if snapshot:  # ảnh chụp "còn nợ" ở ngày cuối dữ liệu: nợ = due − (due − snapshot)
        for oid, due, snap in conn.execute("SELECT order_id, amount_due, debt_snapshot FROM std_order_money"):
            paid[oid] = (due or 0) - (snap or 0)
    else:
        for oid, amt in conn.execute("SELECT order_id, SUM(amount) FROM std_payments WHERE paid_date <= ? GROUP BY 1", (upto,)):
            paid[oid] = amt or 0
    out = []
    for oid, d, key, name, pm, ch, due in conn.execute(
            "SELECT o.order_id, o.order_date, o.customer_key, COALESCE(c.customer_name, o.customer_name), o.pay_method, o.channel, "
            "m.amount_due FROM std_orders o JOIN std_order_money m USING(order_id) LEFT JOIN std_customers c USING(customer_key) "
            "WHERE o.status_norm != 'huỷ' AND o.order_date <= ?", (upto,)):
        if _is_cod(pm) or _is_marketplace(ch) or due is None:
            continue
        rest = due - paid[oid]
        if rest > 0.5:
            out.append({"order_id": oid, "date": d, "key": key or None, "name": name, "rest": rest,
                        "age": (date.fromisoformat(upto) - date.fromisoformat(d)).days})
    return out


def _debt_from_books(conn, upto: str) -> list[dict]:
    """Shop ghi sổ nợ tay (+ sổ góp): nợ đơn = nợ sau lúc mua − các lần trả đến hết X; góp còn lại cũng là nợ (luật public)."""
    out, X = [], date.fromisoformat(upto)
    paid = defaultdict(float)
    for ref, d, a in conn.execute("SELECT order_ref, paid_date, amount FROM std_debt_book_payments"):
        if d and str(d)[:10] <= upto:
            paid[ref] += a or 0
    for ref, d, cust, owe in conn.execute("SELECT order_ref, buy_date, customer, debt_after_purchase FROM std_debt_book"):
        if d and d[:10] <= upto and (owe or 0) - paid[ref] > 0.5:
            out.append({"order_id": ref, "date": d[:10], "key": _ukey(cust) or None, "name": cust, "rest": owe - paid[ref],
                        "age": (X - date.fromisoformat(d[:10])).days})
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='std_installments'").fetchone():
        buy = dict(conn.execute("SELECT contract_id, buy_date FROM std_installments").fetchall())
        for x in installment_status(conn, upto, upto):
            if x["rest"] > 0.5:
                out.append({"order_id": x["id"], "date": buy[x["id"]][:10], "key": _ukey(x["customer"]) or None,
                            "name": x["customer"], "rest": x["rest"], "age": (X - date.fromisoformat(buy[x["id"]][:10])).days})
    return out


def debt_total(conn, upto: str, as_of, min_age: int | None = None) -> float:
    """Tổng còn nợ tại hết ngày upto; min_age: chỉ đơn có tuổi nợ > min_age ngày ('quá N ngày')."""
    return sum(o["rest"] for o in debt_orders(conn, upto, as_of) if min_age is None or o["age"] > min_age)


def debt_buckets(conn, upto: str, as_of, edges=(7, 15)) -> list[float]:
    """Tuổi nợ 0–7, 8–15, trên 15 ngày (mặc định)."""
    out = [0.0] * (len(edges) + 1)
    for o in debt_orders(conn, upto, as_of):
        out[sum(o["age"] > e for e in edges)] += o["rest"]
    return out


def debt_by_customer(conn, upto: str, as_of) -> list[dict]:
    """§4: nợ theo khách (khách lẻ không có dòng), giảm dần, bằng nhau theo mã."""
    agg: dict = {}
    for o in debt_orders(conn, upto, as_of):
        if o["key"]:
            it = agg.setdefault(o["key"], {"name": o["name"], "code": o["key"], "value": 0.0})
            it["value"] += o["rest"]
    return _rank(list(agg.values()), None)


def debt_flow(conn, start, end, as_of, customer_key=None) -> tuple[float, float]:
    """§4: (nợ thêm, trả nợ) trong kỳ — chỉ đơn ghi nợ, không huỷ. Nợ thêm: phần chưa trả trong ngày mua của đơn đặt trong kỳ.
    Trả nợ: các khoản trả SAU ngày mua có ngày trả trong kỳ (mọi đơn ghi nợ, kể cả đơn kỳ trước)."""
    _need(conn, "std_order_money")
    s, e = start or "0000", min(end or as_of, as_of)
    new = repaid = 0.0
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='std_payments'").fetchone():
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='std_debt_book'").fetchone() or customer_key:
            raise Unsure("không có phiếu thu")  # shortcut: sổ nợ tay không có mã khách, chưa lọc theo khách
        new = sum(o or 0 for (o,) in conn.execute("SELECT debt_after_purchase FROM std_debt_book WHERE buy_date BETWEEN ? AND ?", (s, e)))
        repaid = sum(a or 0 for (a,) in conn.execute("SELECT amount FROM std_debt_book_payments p JOIN std_debt_book b USING(order_ref) "
                                                     "WHERE substr(p.paid_date, 1, 10) BETWEEN ? AND ? AND p.paid_date > b.buy_date", (s, e)))
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='std_installment_payments'").fetchone():  # tiền góp = trả nợ
            repaid += sum(a or 0 for (a,) in conn.execute("SELECT amount FROM std_installment_payments WHERE substr(paid_date, 1, 10) "
                                                         "BETWEEN ? AND ?", (s, e)))
        return new, repaid
    rows = conn.execute("SELECT o.order_id, o.order_date, o.pay_method, o.customer_key, m.amount_due FROM std_orders o "
                        "JOIN std_order_money m USING(order_id) WHERE o.status_norm != 'huỷ' AND o.order_date <= ?", (e,)).fetchall()
    pays = defaultdict(list)
    for oid, d, amt in conn.execute("SELECT order_id, paid_date, amount FROM std_payments WHERE paid_date <= ?", (e,)):
        pays[oid].append((d, amt or 0))
    for oid, d, pm, key, due in rows:
        if not _is_debt_method(pm) or (customer_key and key != customer_key):
            continue
        if s <= d <= e:
            new += (due or 0) - sum(a for pd_, a in pays[oid] if pd_ <= d)
        repaid += sum(a for pd_, a in pays[oid] if pd_ > d and s <= pd_ <= e)
    return new, repaid


# ----------------------------- sửa xe (rulebook public §Sửa xe) ---------------------------

# việc chuẩn -> mẫu (trên từng phần ô "Làm gì" đã bỏ dấu). Thứ tự quan trọng: láp/hộp số trước nhớt máy, vá trước ruột.
REPAIR_JOBS = [
    ("nhot lap", r"\blap\b|hop so"),
    ("va ruot", r"^va\b"),
    ("thay ruot", r"\bruot\b"),
    ("nhot may", r"nhot|castrol|motul|shell|^nhot hang"),
    ("sua dien", r"sua dien|lam dien"),
    ("bao duong", r"^bd(tq)?$|bao duong"),
    ("kim phun", r"kim phun"),
    ("rua xe", r"^rua( xe)?$"),
    ("lam noi", r"\bnoi\b"),
    ("bo thang truoc", r"(bo|ma phanh|bo thang) truoc"),
    ("bo thang sau", r"(bo|ma phanh|bo thang) sau"),
    ("loc gio", r"loc gio"),
    ("sen", r"(chinh|can|tang) sen"),
    ("bugi", r"bu ?gi"),
    ("bong den", r"bong den|den pha"),
    ("vo xe", r"\bvo\b"),
    ("curoa", r"cu ?roa"),
    ("nhong xich", r"nhong|\bnsd\b"),
    ("nuoc mat", r"nuoc (lam )?mat"),
    ("ac quy", r"ac quy|^binh|thay binh"),
]


def repair_jobs(jobs: str) -> set[str]:
    out = set()
    for part in re.split(r"\s*[+,;/&]\s*|\s+va\s+(?=\S)", _ukey(jobs)):
        part = part.strip()
        for name, pat in REPAIR_JOBS:
            if re.search(pat, part):
                out.add(name)
                break
    return out


def _repairs(conn, start, end, as_of, mechanic=None) -> list[dict]:
    _need(conn, "std_repairs")
    s, e = start or "0000", min(end or as_of, as_of)
    rows = conn.execute("SELECT ticket_id, repair_date, plate, model, jobs, mechanic, amount, is_warranty FROM std_repairs "
                        "WHERE repair_date BETWEEN ? AND ?", (s, e)).fetchall()
    mech = set(_list(mechanic) or [])
    return [{"id": t, "date": d, "plate": p, "model": m, "jobs": j, "mechanic": me, "amount": a or 0, "warranty": bool(w)}
            for t, d, p, m, j, me, a, w in rows if not mech or me in mech]


def repair_count(conn, start, end, as_of, mechanic=None, job=None, paid_only=False) -> int:
    """Số phiếu/lượt xe (kể cả bảo hành); job: lượt có việc đó (không tính phiếu bảo hành)."""
    rs = _repairs(conn, start, end, as_of, mechanic)
    if job:
        return sum(1 for r in rs if not r["warranty"] and job in repair_jobs(r["jobs"]))
    return sum(1 for r in rs if not (paid_only and r["warranty"]))


def repair_revenue(conn, start, end, as_of, mechanic=None) -> float:
    return sum(r["amount"] for r in _repairs(conn, start, end, as_of, mechanic))


def repair_avg_ticket(conn, start, end, as_of, mechanic=None) -> float:
    """Tiền trung bình một phiếu: chia số phiếu có thu tiền (không tính bảo hành)."""
    rs = [r for r in _repairs(conn, start, end, as_of, mechanic) if not r["warranty"]]
    if not rs:
        raise NoData("không có phiếu")
    return sum(r["amount"] for r in rs) / len(rs)


def commission_rates(conn) -> dict[str, float]:
    """Thợ -> % hoa hồng, từ bảng danh sách thợ (cột tên khớp tên thợ trong std_repairs, cột chứa 'hoa hồng')."""
    mechs = {r[0] for r in conn.execute("SELECT DISTINCT mechanic FROM std_repairs WHERE mechanic IS NOT NULL")}
    for (t,) in conn.execute("SELECT DISTINCT table_name FROM _meta_columns WHERE replace(column_name, '_', '') LIKE '%hoahong%'"):
        pct = conn.execute("SELECT column_name FROM _meta_columns WHERE table_name = ? AND replace(column_name, '_', '') LIKE '%hoahong%'",
                           (t,)).fetchone()[0]
        for (c,) in conn.execute("SELECT column_name FROM _meta_columns WHERE table_name = ?", (t,)):
            rows = conn.execute(f'SELECT "{c}", "{pct}" FROM "{t}"').fetchall()
            if sum(str(n) in mechs for n, _ in rows) >= max(1, len(rows) // 2):
                return {str(n): float(p) for n, p in rows if p is not None}
    raise NoData("không có bảng hoa hồng")


def mechanic_table(conn, start, end, as_of, by="commission") -> list[dict]:
    """Xếp thợ theo hoa hồng (% × tổng tiền phiếu trong tháng) / tiền / số lượt / số ngày làm việc."""
    rs = _repairs(conn, start, end, as_of)
    agg = defaultdict(lambda: {"amount": 0.0, "count": 0, "days": set()})
    for r in rs:
        if r["mechanic"]:
            a = agg[r["mechanic"]]
            a["amount"] += r["amount"]
            a["count"] += 1
            a["days"].add(r["date"])
    rates = commission_rates(conn) if by == "commission" else {}
    items = []
    for m, a in agg.items():
        v = {"commission": a["amount"] * rates.get(m, 0) / 100, "amount": a["amount"], "count": a["count"],
             "days": len(a["days"])}[by]
        items.append({"name": m, "code": m, "value": v})
    return _rank(items, None)


def repair_top_model(conn, start, end, as_of, by="amount") -> list[dict]:
    agg = defaultdict(float)
    for r in _repairs(conn, start, end, as_of):
        if r["model"]:
            agg[r["model"]] += r["amount"] if by == "amount" else 1
    return _rank([{"name": m, "code": m, "value": v} for m, v in agg.items()], None)


def repair_vehicles(conn, start, end, as_of, min_paid=None, model=None) -> int:
    """Số chiếc xe = số biển số khác nhau (kể cả xe chỉ tới bảo hành); min_paid: xe có >= N phiếu có thu tiền."""
    rs = [r for r in _repairs(conn, start, end, as_of) if not model or r["model"] in model]
    if min_paid is None:
        return len({r["plate"] for r in rs if r["plate"]})
    cnt = defaultdict(int)
    for r in rs:
        if r["plate"] and not r["warranty"]:
            cnt[r["plate"]] += 1
    return sum(v >= min_paid for v in cnt.values())


def warranty_rate(conn, start, end, as_of) -> float:
    rs = _repairs(conn, start, end, as_of)
    paid = sum(not r["warranty"] for r in rs)
    return share(sum(r["warranty"] for r in rs), paid)


def weekday_avg(conn, start, end, as_of, weekday: int, measure="repairs") -> float:
    """Trung bình mỗi ngày thứ X: chia số ngày thứ X trong kỳ (kể cả ngày không có phiếu/đơn). weekday: 0 = thứ Hai."""
    s, e = date.fromisoformat(start), date.fromisoformat(min(end, as_of))
    days = [(s + timedelta(days=i)) for i in range((e - s).days + 1)]
    days = {d.isoformat() for d in days if d.weekday() == weekday}
    if not days:
        raise NoData("không có ngày thứ đó")
    if measure == "repairs":
        tot = sum(1 for r in _repairs(conn, start, end, as_of) if r["date"] in days)
    elif measure == "orders":
        tot = sum(1 for (d,) in conn.execute("SELECT order_date FROM std_orders WHERE is_valid = 1 AND order_date BETWEEN ? AND ?",
                                             (start, min(end, as_of))) if d in days)
    else:
        tot = sum(v for d, v in conn.execute("SELECT order_date, revenue FROM std_orders WHERE is_valid = 1 AND order_date BETWEEN ? AND ?",
                                             (start, min(end, as_of))) if d in days)
    return tot / len(days)


# ----------------------------- cửa hàng điện thoại (rulebook public §Cửa hàng điện thoại) ---------------

def _valid_orders(conn, start, end, as_of) -> list[str]:
    w, args = _order_rows(conn, start, end, as_of, {})
    return [r[0] for r in conn.execute(f"SELECT o.order_id FROM std_orders o WHERE {w}", args)]


def tradein_total(conn, start, end, as_of) -> float:
    """Tiền thu máy cũ của đơn không huỷ, không trả trong kỳ (theo ngày đơn)."""
    _need(conn, "std_tradeins")
    ids = set(_valid_orders(conn, start, end, as_of))
    return sum(v or 0 for oid, v in conn.execute("SELECT order_id, tradein_value FROM std_tradeins") if str(oid) in ids)


def tradein_order_share(conn, start, end, as_of) -> float:
    """% đơn (hợp lệ) có thu máy cũ."""
    _need(conn, "std_tradeins")
    ids = set(_valid_orders(conn, start, end, as_of))
    with_t = {str(o) for (o,) in conn.execute("SELECT DISTINCT order_id FROM std_tradeins")} & ids
    return share(len(with_t), len(ids))


def customer_pays(conn, start, end, as_of, avg=False) -> float:
    """Khách phải trả = tiền đơn sau bớt (+ ship) và sau trừ máy cũ, gồm phần còn nợ; đơn hợp lệ."""
    _need(conn, "std_order_money")
    ids = _valid_orders(conn, start, end, as_of)
    due = dict(conn.execute("SELECT order_id, amount_due FROM std_order_money").fetchall())
    trade = defaultdict(float)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='std_tradeins'").fetchone():
        for o, v in conn.execute("SELECT order_id, tradein_value FROM std_tradeins"):
            trade[str(o)] += v or 0
    tot = sum((due.get(o) or 0) - trade[o] for o in ids)
    if avg:
        if not ids:
            raise NoData("không có đơn")
        return tot / len(ids)
    return tot


def _add_months(d: date, n: int, day: int) -> date:
    y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    import calendar
    return date(y, m, min(int(day), calendar.monthrange(y, m)[1]))


def installment_status(conn, day: str, as_of) -> list[dict]:
    """Mỗi hợp đồng góp tại hết ngày `day`: còn chưa góp (cả kỳ chưa tới hạn), số kỳ tới hạn (hạn <= day),
    tiền quá hạn (kỳ có hạn TRƯỚC day mà chưa góp đủ)."""
    _need(conn, "std_installments")
    day = min(day, as_of)
    d = date.fromisoformat(day)
    paid = defaultdict(float)
    for cid, pd_, a in conn.execute("SELECT contract_id, paid_date, amount FROM std_installment_payments"):
        if pd_ and pd_[:10] <= day:
            paid[cid] += a or 0
    out = []
    for cid, buy, cust, price, pre, n, monthly, due_day in conn.execute(
            "SELECT contract_id, buy_date, customer, price, prepaid, n_terms, monthly, due_day FROM std_installments"):
        if not buy or buy > day:
            continue
        b = date.fromisoformat(buy[:10])
        dues = [_add_months(b, i + 1, due_day or b.day) for i in range(int(n or 0))]
        rest = (price or 0) - (pre or 0) - paid[cid]
        overdue = max(0.0, sum(monthly or 0 for x in dues if x < d) - paid[cid])
        out.append({"id": cid, "customer": cust, "rest": max(rest, 0.0), "due_terms": sum(x <= d for x in dues),
                    "overdue": overdue})
    return out


def warranty_visits(conn, start, end, as_of, model=None) -> list[dict]:
    _need(conn, "std_warranties")
    s, e = start or "0000", min(end or as_of, as_of)
    return [{"date": d, "imei": i, "model": m, "buy": b} for d, i, m, b in conn.execute(
        "SELECT service_date, imei, model, buy_date FROM std_warranties WHERE service_date BETWEEN ? AND ?", (s, e))
        if not model or _ukey(m) == _ukey(model)]


def phones_sold(conn, start, end, as_of) -> float:
    """Số máy bán (máy mới + máy cũ), đơn không huỷ, không trả."""
    cats = [c for (c,) in conn.execute("SELECT DISTINCT category FROM std_lines WHERE category IS NOT NULL") if "may" in _ukey(c)]
    if not cats:
        raise NoData("không phân loại được máy")
    return qty(conn, start, end, as_of, category=cats)


# ----------------------------- mẹ & bé (rulebook public §Mẹ và bé) -----------------------------

def adjust_total(conn, start, end, as_of, kind: str) -> float:
    """Tiền giảm voucher / giảm nhờ dùng điểm / phí ship: số dương, theo ngày hoá đơn, hoá đơn không huỷ, không trả.
    kind: 'voucher' | 'diem' | 'ship' — khớp theo tên dòng điều chỉnh."""
    w, args = _order_rows(conn, start, end, as_of, {})
    rows = conn.execute(f"SELECT l.product_name, l.adjust_amount, l.line_revenue FROM std_lines l JOIN std_orders o USING(order_id) "
                        f"WHERE {w} AND l.line_kind = 'adjustment'", args).fetchall()
    pat = {"voucher": r"voucher|vau ?cho|giam gia", "diem": r"diem", "ship": r"ship|giao hang|van chuyen"}[kind]
    hit = [abs(a if a is not None else (r or 0)) for n, a, r in rows if re.search(pat, _ukey(n))]
    if not rows:
        raise NoData("shop không ghi dòng điều chỉnh")
    return sum(hit)


def new_members(conn, start, end, as_of) -> int:
    """Khách có ngày đăng ký trong kỳ, kể cả khách chưa mua."""
    s, e = start or "0000", min(end or as_of, as_of)
    n = conn.execute("SELECT COUNT(*) FROM std_customers WHERE substr(register_date, 1, 10) BETWEEN ? AND ?", (s, e)).fetchone()[0]
    if not conn.execute("SELECT COUNT(register_date) FROM std_customers").fetchone()[0]:
        raise NoData("shop không ghi ngày đăng ký")
    return n


def top_brands(conn, start, end, as_of, k=1, by="qty", **F) -> list[dict]:
    w, args = _order_rows(conn, start, end, as_of, F)
    lw = _where_lines(conn, F, args)
    val = "SUM(l.qty_base)" if by == "qty" else "SUM(l.line_revenue)"
    rows = conn.execute(f"SELECT p.brand, {val} FROM std_lines l JOIN std_orders o USING(order_id) JOIN std_products p "
                        f"ON p.product_name = l.product_name WHERE {w} AND p.brand IS NOT NULL {''.join(' AND ' + x for x in lw)} "
                        "GROUP BY 1", args).fetchall()
    return _rank([{"name": b, "code": b, "value": v} for b, v in rows], k)


# ----------------------------- giao nhiều đợt (VLXD) -----------------------------

def delivery_rounds(conn, start, end, as_of) -> int:
    """Số đợt giao có ngày giao trong kỳ (tới hết hôm nay); mỗi đợt của mỗi đơn một đợt, kể cả đơn sau đó trả hàng."""
    _need(conn, "std_deliveries")
    s, e = start or "0000", min(end or as_of, as_of)
    return conn.execute("SELECT COUNT(*) FROM (SELECT DISTINCT d.order_id, d.round FROM std_deliveries d JOIN std_orders o USING(order_id) "
                        "WHERE d.deliver_date BETWEEN ? AND ? AND o.status_norm != 'huỷ')", (s, e)).fetchone()[0]


def delivered(conn, order_start, order_end, as_of, product=None, min_rounds=None) -> float:
    """Đơn đặt trong kỳ: tổng số lượng (đơn vị cơ bản) đã chở tới hết hôm nay; min_rounds: số đơn đã giao >= N đợt."""
    _need(conn, "std_deliveries")
    rows = conn.execute("SELECT d.order_id, d.round, d.product_name, d.qty_base FROM std_deliveries d JOIN std_orders o USING(order_id) "
                        "WHERE o.order_date BETWEEN ? AND ? AND d.deliver_date <= ? AND o.status_norm != 'huỷ'",
                        (order_start, order_end, as_of)).fetchall()
    if min_rounds:
        rounds = defaultdict(set)
        for oid, r, _, _ in rows:
            rounds[oid].add(r)
        return sum(len(v) >= min_rounds for v in rounds.values())
    return sum(q or 0 for _, _, p, q in rows if product is None or p == product)


# ----------------------------- xếp hạng theo luật public §3 -----------------------------

def customer_rank(conn, start, end, as_of, by: str, k=1) -> list[dict]:
    """by: 'days' (trung thành: nhiều ngày có mua), 'cancels' (đổi ý: nhiều đơn huỷ), 'avg3' (sộp: TB đơn cao nhất, >= 3 đơn)."""
    valid_only = by != "cancels"
    w, args = _order_rows(conn, start, end, as_of, {}, valid_only=valid_only)
    val = {"days": "COUNT(DISTINCT o.order_date)", "cancels": "SUM(o.status_norm = 'huỷ')", "avg3": "AVG(o.revenue)"}[by]
    having = " HAVING COUNT(*) >= 3" if by == "avg3" else ""
    rows = conn.execute(f"SELECT o.customer_key, COALESCE(MIN(c.customer_name), MIN(o.customer_name)), MIN(o.customer_id), {val} "
                        f"FROM std_orders o LEFT JOIN std_customers c USING(customer_key) WHERE {w} AND o.customer_key IS NOT NULL "
                        f"AND o.customer_key != '' GROUP BY o.customer_key{having}", args).fetchall()
    return _rank([{"name": n, "code": cid or key, "value": v or 0} for key, n, cid, v in rows], k)


def products_by_orders(conn, start, end, as_of, k=1) -> list[dict]:
    """Ưa chuộng nhất: có mặt trong nhiều đơn nhất."""
    w, args = _order_rows(conn, start, end, as_of, {})
    rows = conn.execute(f"SELECT l.product_name, MIN(l.product_id), COUNT(DISTINCT o.order_id) FROM std_lines l JOIN std_orders o "
                        f"USING(order_id) WHERE {w} AND l.is_service_fee = 0 AND COALESCE(l.line_kind, 'item') = 'item' GROUP BY 1",
                        args).fetchall()
    return _rank([{"name": n, "code": c, "value": v} for n, c, v in rows], k)


def peak_hour(conn, start, end, as_of) -> int:
    """Khung một giờ tròn theo giờ tạo đơn có nhiều đơn nhất; đáp án là giờ bắt đầu."""
    w, args = _order_rows(conn, start, end, as_of, {})
    rows = conn.execute(f"SELECT CAST(substr(o.order_time, 1, 2) AS INTEGER), COUNT(*) FROM std_orders o WHERE {w} "
                        "AND o.order_time IS NOT NULL AND o.order_time != '' GROUP BY 1", args).fetchall()
    if not rows:
        raise NoData("shop không ghi giờ đơn")
    return sorted(rows, key=lambda r: (-r[1], r[0]))[0][0]


def main_supplier(conn, start, end, as_of) -> str:
    """Nhà cung cấp chính: nhiều phiếu nhập nhất trong kỳ, bỏ phiếu huỷ."""
    _need(conn, "std_purchases")
    rows = conn.execute("SELECT supplier, COUNT(DISTINCT purchase_id) FROM std_purchases WHERE purchase_date BETWEEN ? AND ? "
                        "AND COALESCE(is_cancelled, 0) = 0 AND supplier IS NOT NULL GROUP BY 1",
                        (start or "0000", min(end or as_of, as_of))).fetchall()
    if not rows:
        raise NoData("không có phiếu nhập")
    return sorted(rows, key=lambda r: (-r[1], r[0]))[0][0]


def employee_days(conn, start, end, as_of, k=1) -> list[dict]:
    """Chăm chỉ nhất: nhiều ngày làm việc nhất (ngày có ít nhất một đơn người đó bán)."""
    w, args = _order_rows(conn, start, end, as_of, {}, valid_only=False)
    rows = conn.execute(f"SELECT o.employee_id, COUNT(DISTINCT o.order_date) FROM std_orders o WHERE {w} AND o.employee_id IS NOT NULL "
                        "AND o.employee_id != '' GROUP BY 1", args).fetchall()
    if not rows:
        raise NoData("shop không ghi nhân viên")
    return _rank([{"name": e, "code": e, "value": v} for e, v in rows], k)


def purchase_qty(conn, product: str, start, end, as_of) -> float:
    """Số lượng nhập (đơn vị cơ bản), phiếu không huỷ, ngày phiếu trong kỳ."""
    _need(conn, "std_purchases")
    return sum((q or 0) * (unit_factor(conn, product, u) or 1) for q, u in conn.execute(
        "SELECT qty, unit FROM std_purchases WHERE product_name = ? AND COALESCE(is_cancelled, 0) = 0 AND purchase_date BETWEEN ? AND ?",
        (product, start or "0000", min(end or as_of, as_of))))


def import_minus_sales(conn, product: str, start, end, as_of) -> float:
    """§5: nhập trừ bán — phần bán là số lượng của đơn tính doanh thu, cùng đơn vị (cơ bản) với phần nhập."""
    return purchase_qty(conn, product, start, end, as_of) - qty(conn, start, end, as_of, product=product)


def expenses(conn, start, end, as_of) -> float:
    """Tổng mọi khoản trong sổ chi phí có ngày trong kỳ (bảng tên có 'chi phí', cột tiền + cột ngày)."""
    s, e = start or "0000", min(end or as_of, as_of)
    for (t,) in conn.execute("SELECT DISTINCT table_name FROM _meta_columns WHERE replace(table_name, '_', '') LIKE '%chiphi%'"):
        cols = [r[0] for r in conn.execute("SELECT column_name FROM _meta_columns WHERE table_name = ?", (t,))]
        amt = next((c for c in cols if re.search(r"tien|so_?tien|chi$|amount", c) and not re.search(r"ngay|ghi|muc|nhom", c)), None)
        day = next((c for c in cols if re.search(r"ngay|date|thoi_?gian", c)), None)
        if amt and day:
            return conn.execute(f'SELECT COALESCE(SUM("{amt}"), 0) FROM "{t}" WHERE substr("{day}", 1, 10) BETWEEN ? AND ?',
                                (s, e)).fetchone()[0]
    raise NoData("shop không có sổ chi phí")


def profit_net(conn, start, end, as_of) -> float:
    """Lãi ròng = lãi gộp − mọi khoản trong sổ chi phí của kỳ."""
    return profit_gross(conn, start, end, as_of) - expenses(conn, start, end, as_of)
