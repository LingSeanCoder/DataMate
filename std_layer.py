"""Tầng dữ liệu chuẩn: std_orders + std_lines với tên cột cố định cho mọi shop.

Model chỉ cần query 2 bảng này thay vì đoán cột tiền/trạng thái của từng shop.
Phát hiện hoàn toàn theo vai trò cột + giao tập giá trị, KHÔNG theo business id (luật 4).

std_lines : order_id, line_no, product_id, product_name, unit, qty, qty_base,
            line_revenue, line_cost, is_service_fee
std_orders: order_id, order_date (YYYY-MM-DD), order_time (HH:MM:SS), status_raw,
            status_norm (hoàn thành|huỷ|trả hàng|đang giao), is_valid, customer_id,
            customer_name, pay_method, revenue, n_lines
_meta_std : bảng nguồn/cột nguồn đã chọn cho từng vai trò (để debug).
"""
from __future__ import annotations

import re
import sqlite3

import pandas as pd

from data_loader import sql_quote, strip_diacritics

DONE, CANCEL, RETURN, SHIPPING = "hoàn thành", "huỷ", "trả hàng", "đang giao"


def _key(text) -> str:
    return re.sub(r"[^a-z0-9]", "", strip_diacritics(str(text or "")).lower())


def norm_status(value) -> str | None:
    """Trạng thái gốc -> 1 trong 4 trạng thái chuẩn; None nếu ô trống/không nói gì."""
    if value is None or str(value).strip() == "":
        return None
    w = set(re.findall(r"[a-z]+", strip_diacritics(str(value)).lower()))
    if w & {"tra", "return", "returned", "refund"} or {"hoan", "hang"} <= w:
        return RETURN
    if w & {"huy", "cancel", "cancelled", "canceled", "void"} or {"khong", "giao"} <= w or "that" in w:
        return CANCEL
    if w & {"dang", "cho", "shipping", "pending"}:
        return SHIPPING
    if w & {"xong", "hoan", "thanh", "giao", "lay", "done", "completed", "success", "ok"}:
        return DONE
    return None


def _order_status(statuses: pd.Series) -> str:
    """Đơn có bất kỳ dòng trả -> trả hàng (kể cả trả 1 phần, §4.1); rồi huỷ; rồi đang giao."""
    s = set(statuses.dropna())
    for st in (RETURN, CANCEL, SHIPPING):
        if st in s:
            return st
    return DONE


# ----------------------------- vai trò cột -----------------------------------

class Col:
    def __init__(self, table: str, name: str, original: str, kind: str, n_distinct: int, top: str):
        self.table, self.name, self.kind, self.n_distinct = table, name, kind, n_distinct or 0
        self.keys = {_key(name), _key(original)} - {""}
        self.has_time = bool(re.search(r"\d{1,2}:\d{2}", top or ""))

    def has(self, *words: str) -> bool:
        return any(w in k for k in self.keys for w in words)

    def starts(self, *words: str) -> bool:
        return any(k.startswith(words) for k in self.keys)


_PERSON = ("kh", "khach", "nv", "nhanvien", "ncc", "nhacungcap", "customer", "employee", "supplier")


def _is_order_id(c: Col) -> bool:
    return c.kind == "text" and not c.has("chitiet", "phieunhap", "nhaphang") and (
        c.has("hoadon", "donhang", "bill", "giaodich", "orderid", "madon", "sodon", "sohd", "mahd", "magd",
              "phieu", "chungtu", "soct", "mact")
        or any(k in {"order", "orderid", "id"} for k in c.keys)
    )


def _is_product_code(c: Col) -> bool:
    return c.kind == "text" and c.has("mahang", "masp", "mahh", "masanpham", "mamon", "mamathang",
                                      "idsp", "productid", "sku", "mathuoc")


def _is_product_name(c: Col) -> bool:
    if c.kind != "text" or _is_product_code(c):
        return False
    if c.starts("ten") and not c.has(*[f"ten{p}" for p in _PERSON]):
        return True
    return any(k in {"mon", "hang", "hanghoa", "sanpham", "mathang", "thuoc", "product", "item",
                     "noidung", "chitiethang", "tenhang", "dichvu"} or k.startswith(("hangban", "mathang", "hanghoa"))
               for k in c.keys)


def _is_qty(c: Col) -> bool:
    return c.kind == "number" and c.starts("soluong", "sl", "qty", "quantity")


def _is_base_qty(c: Col) -> bool:
    return c.kind == "number" and c.has("quydoi", "coban", "quyra", "soluongle", "slle")


def _line_money_rank(c: Col) -> int:
    """Cột thành tiền của dòng (đã trừ giảm giá dòng). 0 = không phải."""
    if c.kind != "number":
        return 0
    if c.has("trigia"):
        return 4
    if c.has("gia", "price", "von", "cost", "giam", "chietkhau", "ck"):
        return 0
    for rank, words in ((5, ("thanhtien",)), (4, ("trigia",)), (3, ("tongtien", "tong")),
                        (2, ("thanhtoan",)), (1, ("tien", "amount", "total"))):
        if c.has(*words):
            return rank
    return 0


def _date_rank(c: Col) -> int:
    if c.kind != "date" or c.has("sinh", "hethan", "hsd", "hansudung", "handung", "hanthanhtoan",
                                 "hantra", "hanno", "dangky", "capnhat", "update"):
        return 0
    if c.has("ngaygiao") and not c.has("giaodich"):
        return 0  # ngày giao hàng, không phải ngày đặt (§4.1)
    if c.has("ngayban", "ngaydat", "ngaygiaodich", "ngaythang", "ngaytao"):
        return 4
    if c.has("tao", "created"):
        return 1
    return 3 if c.has("ngay", "thoigian", "date", "time") else 2


def _first(cols: list[Col], pred) -> Col | None:
    return next((c for c in cols if pred(c)), None)


def _best(cols: list[Col], rank) -> Col | None:
    scored = [(rank(c), i, c) for i, c in enumerate(cols) if rank(c) > 0]
    return max(scored, key=lambda t: (t[0], -t[1]))[2] if scored else None


# ----------------------------- chọn bảng -------------------------------------

def _load_cols(conn: sqlite3.Connection) -> dict[str, list[Col]]:
    out: dict[str, list[Col]] = {}
    for t, n, o, k, nd, top in conn.execute(
        "SELECT table_name, column_name, original_name, kind, n_distinct, top_values FROM _meta_columns"
    ):
        if not t.startswith(("_", "std_")):
            out.setdefault(t, []).append(Col(t, n, o, k, nd, top))
    return out


def _overlap(conn, t1: str, c1: str, t2: str, c2: str) -> int:
    q = (f"SELECT COUNT(*) FROM (SELECT DISTINCT {sql_quote(c1)} v FROM {sql_quote(t1)} "
         f"WHERE {sql_quote(c1)} IS NOT NULL LIMIT 300) WHERE v IN "
         f"(SELECT {sql_quote(c2)} FROM {sql_quote(t2)})")
    try:
        return int(conn.execute(q).fetchone()[0])
    except sqlite3.Error:
        return 0


_NOT_SALES = ("nhap", "purchase", "import", "chiphi", "expense", "tonkho", "quydoi",
              "donvi", "giaohang", "congno", "hanno", "lohang", "theolo", "lo", "thekho", "lichsugia", "banggia")


def _pick_line_table(cols: dict[str, list[Col]]):
    best = None
    for t, cs in cols.items():
        if any(w in _key(t) for w in _NOT_SALES):
            continue
        money = _best(cs, _line_money_rank)
        qty = _first(cs, lambda c: _is_qty(c) and not _is_base_qty(c)) or _first(cs, _is_base_qty)
        prod = _first(cs, _is_product_code) or _first(cs, _is_product_name)
        if not (money and qty and prod):
            continue
        score = _line_money_rank(money) + (3 if "chitiet" in _key(t) else 0) + (1 if _first(cs, _is_order_id) else 0)
        if best is None or score > best[0]:
            best = (score, t)
    return best[1] if best else None


def _pick_header(conn, cols, line_t: str | None):
    """Bảng đơn: có ngày + mã đơn gần như duy nhất mỗi dòng; nếu có bảng dòng thì mã đơn phải khớp."""
    best = None
    for t, cs in cols.items():
        if t == line_t or any(w in _key(t) for w in _NOT_SALES + ("quy", "thanhtoan", "phieuthu", "payment")):
            continue
        rows = conn.execute(f"SELECT COUNT(*) FROM {sql_quote(t)}").fetchone()[0]
        date = _best(cs, _date_rank)
        if not date or _date_rank(date) < 2 or not rows:
            continue
        # Mã đơn: cột text gần như duy nhất mỗi dòng. Có bảng dòng thì nhận theo giao tập giá trị
        # (tên cột mã đơn rất khác nhau: ma_chung_tu, order_id, ma_bill...).
        cands = [c for c in cs if c.kind == "text" and c.n_distinct >= 0.95 * rows]
        for oid in (cands if line_t else [c for c in cands if _is_order_id(c)]):
            link = None
            if line_t:
                link = max(((_overlap(conn, line_t, c.name, t, oid.name), c) for c in cols[line_t]
                            if c.kind == "text"), key=lambda x: x[0], default=(0, None))
                if link[0] < 5:
                    continue
            score = (link[0] if link else 0) + 5 * bool(_first(cs, lambda c: c.has("trangthai", "status")))
            if best is None or score > best[0]:
                best = (score, t, oid, link[1] if link else None)
    return best[1:] if best else (None, None, None)


# ----------------------------- dựng bảng -------------------------------------

def _read(conn, t: str) -> pd.DataFrame:
    return pd.read_sql_query(f"SELECT * FROM {sql_quote(t)}", conn)


def _status_col(cs: list[Col]) -> Col | None:
    return _first(cs, lambda c: c.kind == "text" and c.has("trangthai", "status", "ketqua", "tinhtrang", "xuly")) \
        or _first(cs, lambda c: c.kind == "text" and c.has("ghichu", "note"))  # §3.2: trạng thái ghi ở Ghi chú


def _split_dt(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    s = series.astype("string")
    return s.str.slice(0, 10), s.str.extract(r"(\d{1,2}:\d{2}(?::\d{2})?)")[0]


def _conversions(conn, cols) -> dict[tuple[str, str], float]:
    """(tên/mã hàng chuẩn hoá, đơn vị chuẩn hoá) -> hệ số về đơn vị cơ bản."""
    conv: dict[tuple[str, str], float] = {}
    try:
        for p, u, f, _ in conn.execute("SELECT san_pham, don_vi_lon, he_so, don_vi_co_ban FROM _quy_doi"):
            conv[(_key(p), _key(u))] = float(f)
    except sqlite3.Error:
        pass
    for t, cs in cols.items():
        factor = _first(cs, lambda c: c.kind == "number" and (c.has("quydoi", "heso", "tile") or c.keys & {"bang"}))
        unit = _first(cs, lambda c: c.kind == "text" and c.has("donvi", "dvt", "dvi", "unit") and not c.has("coban"))
        prods = [c for c in cs if _is_product_code(c) or _is_product_name(c)]
        if not (factor and unit and prods) or factor.has("soluong"):
            continue
        for row in _read(conn, t).itertuples(index=False):
            r = row._asdict()
            if r.get(factor.name) is None:
                continue
            for p in prods:
                conv[(_key(r.get(p.name)), _key(r.get(unit.name)))] = float(r[factor.name])
    return conv


def build_std(conn: sqlite3.Connection) -> dict:
    cols = _load_cols(conn)
    line_t = _pick_line_table(cols)
    head_t, head_oid, line_oid = _pick_header(conn, cols, line_t)
    meta: dict[str, str] = {"line_table": line_t or "", "header_table": head_t or ""}

    lines = pd.DataFrame()
    if line_t:
        cs, df = cols[line_t], _read(conn, line_t)
        oid = line_oid or _first(cs, _is_order_id)
        money = _best(cs, _line_money_rank)
        qty = _first(cs, lambda c: _is_qty(c) and not _is_base_qty(c)) or _first(cs, _is_base_qty)
        base = _first(cs, lambda c: _is_base_qty(c) and c is not qty)
        code, name = _first(cs, _is_product_code), _first(cs, _is_product_name)
        unit = _first(cs, lambda c: c.kind == "text" and c.has("donvi", "dvt", "dvi", "unit"))
        cost = _first(cs, lambda c: c.kind == "number" and c.has("giavon"))
        for role, c in (("line.order_id", oid), ("line.revenue", money), ("line.qty", qty), ("line.qty_base", base),
                        ("line.product_id", code), ("line.product_name", name), ("line.unit", unit), ("line.cost", cost)):
            meta[role] = c.name if c else ""

        n = len(df)
        lines = pd.DataFrame({
            # shortcut: bảng không có mã đơn -> mỗi dòng là 1 đơn; đủ cho doanh thu, chưa đúng cho "số đơn".
            "order_id": df[oid.name].astype("string") if oid else pd.Series([f"R{i}" for i in range(n)], dtype="string"),
            "line_no": range(1, n + 1),
            "product_id": df[code.name] if code else None,
            "product_name": df[name.name] if name else (df[code.name] if code else None),
            "unit": df[unit.name] if unit else None,
            "qty": pd.to_numeric(df[qty.name], errors="coerce"),
            "line_revenue": pd.to_numeric(df[money.name], errors="coerce").fillna(0),
        })
        if base:
            lines["qty_base"] = pd.to_numeric(df[base.name], errors="coerce")
        else:
            conv = _conversions(conn, cols)
            keys = (lines["product_id"] if code else lines["product_name"]).map(_key)
            alt = lines["product_name"].map(_key)
            units = lines["unit"].map(_key) if unit else pd.Series([""] * n)
            factor = [conv.get((k, u), conv.get((a, u), 1.0)) for k, a, u in zip(keys, alt, units)]
            lines["qty_base"] = lines["qty"] * factor
        # §3.5: tên chỉ khác hoa/thường/dấu là một mặt hàng -> gom về cách viết phổ biến nhất.
        pkey = lines["product_name"].map(_key)
        canon = lines.groupby(pkey)["product_name"].agg(lambda s: s.value_counts().idxmax() if s.notna().any() else None)
        lines["product_name"] = pkey.map(canon).where(lines["product_name"].notna())
        lines["line_cost"] = lines["qty"] * pd.to_numeric(df[cost.name], errors="coerce") if cost else None
        lines["is_service_fee"] = lines["product_name"].map(lambda v: int("phiphucvu" in _key(v)))

        st = _status_col(cs)
        lines["_status"] = df[st.name].map(norm_status) if st else None
        # §README biz trả hàng: dòng số lượng âm cùng mã đơn = đơn có trả hàng.
        lines.loc[lines["qty"] < 0, "_status"] = RETURN
        meta["line.status"] = st.name if st else ""
        dcol = _best(cs, _date_rank)
        dcols = sorted((c for c in cs if _date_rank(c)), key=_date_rank, reverse=True)
        # Ngày đơn = cột ngày tốt nhất, ô trống thì lấy cột ngày/giờ kế tiếp.
        lines["_date"] = df[[c.name for c in dcols]].bfill(axis=1).iloc[:, 0] if dcols else None
        tcol = _first(cs, lambda c: c.has_time and c is not dcol)
        lines["_time"] = df[tcol.name] if tcol else (df[dcol.name] if dcol else None)

    # ---- đơn hàng ----
    if head_t:
        cs = cols[head_t]
        df = _read(conn, head_t).drop_duplicates(head_oid.name).reset_index(drop=True)
        dcol = _best(cs, _date_rank)
        tcol = _first(cs, lambda c: c.has_time and c is not dcol) or dcol
        st = _status_col(cs)
        orders = pd.DataFrame({"order_id": df[head_oid.name].astype("string")})
        orders["order_date"], _ = _split_dt(df[dcol.name])
        _, orders["order_time"] = _split_dt(df[tcol.name])
        orders["status_raw"] = df[st.name] if st else None
        orders["_status"] = df[st.name].map(norm_status) if st else None
        src_cols = cs
        meta.update({"order.order_id": head_oid.name, "order.date": dcol.name, "order.time": tcol.name,
                     "order.status": st.name if st else ""})
        if not line_t:  # chỉ có bảng đơn: doanh thu = tiền hàng (không ship, không giảm cả đơn)
            money = _best(cs, lambda c: 9 if c.kind == "number" and c.has("tienhang") else _line_money_rank(c))
            orders["revenue"] = pd.to_numeric(df[money.name], errors="coerce").fillna(0)
            meta["order.revenue"] = money.name
    elif line_t:
        g = lines.groupby("order_id", sort=False)
        orders = pd.DataFrame({"order_id": g.size().index})
        first = g.first()
        orders["order_date"], _ = _split_dt(first["_date"].reindex(orders["order_id"]).reset_index(drop=True))
        _, orders["order_time"] = _split_dt(first["_time"].reindex(orders["order_id"]).reset_index(drop=True))
        orders["status_raw"] = None
        orders["_status"] = None
        src_cols, df = cols[line_t], _read(conn, line_t).assign(order_id=lines["order_id"]).drop_duplicates("order_id")
        df = df.set_index("order_id").reindex(orders["order_id"]).reset_index(drop=True)
    else:
        return {"error": "không tìm thấy bảng bán hàng"}

    cust_id = _first(src_cols, lambda c: c.kind == "text" and c.has("makh", "makhach", "customerid", "idkh", "sokh"))
    cust_nm = _first(src_cols, lambda c: c.kind == "text" and c is not cust_id and (
        c.has("tenkh", "khachhang", "benmua", "doitac", "customer") or c.keys & {"khach"}))
    pay = _first(src_cols, lambda c: c.kind == "text" and c.has("phuongthuc", "pttt", "hinhthuc", "payment"))
    orders["customer_id"] = df[cust_id.name].values if cust_id else None
    orders["customer_name"] = df[cust_nm.name].values if cust_nm else None
    orders["pay_method"] = df[pay.name].values if pay else None
    meta.update({"order.customer_id": cust_id.name if cust_id else "",
                 "order.customer_name": cust_nm.name if cust_nm else "", "order.pay_method": pay.name if pay else ""})

    if line_t:
        g = lines.groupby("order_id")
        agg = pd.DataFrame({"revenue": g["line_revenue"].sum(), "n_lines": g.size(),
                            "_lstatus": g["_status"].agg(lambda s: _order_status(s) if s.notna().any() else None)})
        orders = orders.merge(agg, left_on="order_id", right_index=True, how="left")
        orders["revenue"] = orders["revenue"].fillna(0)
        orders["n_lines"] = orders["n_lines"].fillna(0).astype(int)
        # Trạng thái đơn = gộp trạng thái bảng đơn và mọi dòng hàng (trả > huỷ > đang giao).
        orders["status_norm"] = pd.concat([orders["_status"], orders["_lstatus"]], axis=1).apply(_order_status, axis=1)
        orders = orders.drop(columns=["_lstatus"])
    else:
        orders["n_lines"] = 0
        orders["status_norm"] = orders["_status"].map(lambda s: s or DONE)
    orders["is_valid"] = orders["status_norm"].isin([DONE, SHIPPING]).astype(int)
    if orders["status_raw"].isna().all() and line_t and meta.get("line.status"):
        raw = _read(conn, line_t)[meta["line.status"]]
        orders["status_raw"] = raw.groupby(lines["order_id"]).first().reindex(orders["order_id"]).values

    order_cols = ["order_id", "order_date", "order_time", "status_raw", "status_norm", "is_valid",
                  "customer_id", "customer_name", "pay_method", "revenue", "n_lines"]
    line_cols = ["order_id", "line_no", "product_id", "product_name", "unit", "qty", "qty_base",
                 "line_revenue", "line_cost", "is_service_fee"]
    for t in ("std_orders", "std_lines", "_meta_std"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    orders[order_cols].to_sql("std_orders", conn, index=False)
    if line_t:
        lines[line_cols].to_sql("std_lines", conn, index=False)
        conn.execute("CREATE INDEX ix_std_lines_order ON std_lines(order_id)")
    conn.execute("CREATE INDEX ix_std_orders_date ON std_orders(order_date)")
    pd.DataFrame(sorted(meta.items()), columns=["role", "source"]).to_sql("_meta_std", conn, index=False)
    conn.commit()
    return meta


if __name__ == "__main__":
    import sys

    for path in sys.argv[1:]:
        with sqlite3.connect(path) as c:
            print(path, build_std(c))
