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


LINE_FILTERS = ("product", "category", "brand", "size", "unit", "ingredient", "prescription")
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
    if units:
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
    if path:
        _BU_CACHE[path] = out
    if "base_unit" in _cols(conn, "std_products"):
        out.update(conn.execute("SELECT product_name, base_unit FROM std_products WHERE base_unit IS NOT NULL AND base_unit != ''").fetchall())
    for p, u in conn.execute("SELECT product_name, unit FROM std_lines WHERE qty = qty_base AND unit IS NOT NULL "
                             "GROUP BY product_name, unit ORDER BY COUNT(*) DESC"):
        out.setdefault(p, u)
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
                        f"WHERE {w} AND l.is_service_fee = 0 {''.join(' AND ' + x for x in lw)}", args).fetchone()[0]


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
                        f"WHERE {w} AND l.is_service_fee = 0 {''.join(' AND ' + x for x in lw)} GROUP BY l.product_name",
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
    # "Vương Phương Loan - 0973 189 069" -> tên (§9 nhận cả hai, gold ghi tên)
    return _rank([{"name": re.sub(r"\s*[-–(,]\s*\+?[\d .]{8,}\)?\s*$", "", n) if n else n, "code": cid or key, "key": key, "value": v}
                  for key, n, cid, v in rows], k)


def customers_with_orders(conn, start, end, as_of, min_orders=1, strict=False, **F) -> int:
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
                        f"WHERE {w} AND l.is_service_fee = 0 GROUP BY 1 HAVING COUNT(DISTINCT o.order_id) {op} ?)",
                        args + [n]).fetchone()[0]


def unsold_items(conn, start, end, as_of, **F) -> list[dict]:
    """§5: mặt hàng đã từng bán trước kỳ (đơn hợp lệ) mà trong kỳ không có đơn hợp lệ nào. Thứ tự theo mã."""
    s, e = clip(conn, start, end, as_of)
    args = [s]
    lw = _where_lines(conn, F, args)
    before = conn.execute("SELECT l.product_name, MIN(l.product_id) FROM std_lines l JOIN std_orders o USING(order_id) "
                          f"WHERE o.is_valid = 1 AND o.order_date < ? AND l.is_service_fee = 0 {''.join(' AND ' + x for x in lw)} "
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


def find_hike_date(conn, product: str, as_of, new_price: float | None = None) -> str:
    """Ngày đầu tiên đơn giá (một đơn vị cơ bản, trước chiết khấu) cao hơn giá liền trước; có new_price thì lần đầu lên giá đó."""
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
    return t / q


def unit_factor(conn, product: str, unit: str) -> float | None:
    """Số đơn vị cơ bản trong 1 `unit` của mặt hàng (§10): bảng quy đổi, không có thì từ dòng bán; đơn vị cơ bản = 1."""
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


def stock_value(conn, day: str, as_of) -> float:
    """§5: Σ tồn (>0) × giá vốn một đơn vị cơ bản trong danh mục."""
    total = 0.0
    for name, cost in conn.execute("SELECT product_name, cost_price FROM std_products WHERE cost_price IS NOT NULL"):
        try:
            q = stock_at(conn, name, day, as_of)
        except NoData:
            continue
        if q and q > 0:
            total += q * cost
    return total


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
    paid = defaultdict(float)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='std_payments'").fetchone():
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
