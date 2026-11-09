"""Bảng std phụ (gọi sau build_std): tiền của đơn, phiếu thu, nhập hàng. Phát hiện theo vai trò cột, không theo biz.

std_order_money : order_id, amount_due (khách phải trả cả đơn), paid_total (đã trả, ảnh chụp cuối dữ liệu),
                  debt_snapshot (còn nợ, ảnh chụp cuối dữ liệu), ship_fee, order_discount
std_payments    : order_id, paid_at (YYYY-MM-DD[ HH:MM:SS]), paid_date, amount, method
std_purchases   : purchase_id, purchase_date, supplier, product_name, qty, unit, unit_cost, line_total,
                  doc_total, is_cancelled, note
"""
from __future__ import annotations

import re
import sqlite3

import pandas as pd

from data_loader import sql_quote, strip_diacritics
from std_layer import (Col, _best, _id_str, _date_rank, _first, _is_order_id, _is_product_code, _is_product_name, _key, _load_cols,
                       _read, _split_dt)


def _money(c: Col) -> bool:
    return c.kind == "number"


def _pick(cs: list[Col], role: str | tuple, hints: tuple = (), kind: str | None = None) -> Col | None:
    roles = (role,) if isinstance(role, str) else role
    ok = (lambda c: kind is None or c.kind == kind)
    return _first(cs, lambda c: ok(c) and c.role in roles) or \
        (_first(cs, lambda c: ok(c) and c.role in (None, "other") and c.has(*hints)) if hints else None)


def _std_ids(conn) -> set[str]:
    try:
        return {str(r[0]) for r in conn.execute("SELECT order_id FROM std_orders")}
    except sqlite3.Error:
        return set()


def _fk_to_orders(df: pd.DataFrame, cs: list[Col], ids: set[str], min_ratio: float = 0.5) -> Col | None:
    cs = [c for c in cs if c.kind in ("text", "number") and (c.kind == "text" or _is_order_id(c))]
    """Cột text có giá trị nằm trong std_orders.order_id nhiều nhất (≥ min_ratio số giá trị khác rỗng)."""
    best = None
    for c in cs:
        vals = _id_str(df[c.name].dropna())
        if vals.empty:
            continue
        r = vals.isin(ids).mean()
        if r >= min_ratio and (best is None or r > best[0]):
            best = (r, c)
    return best[1] if best else None


# ----------------------------- tiền của đơn -----------------------------------

def build_order_money(conn, cols, meta: dict) -> int:
    ids = _std_ids(conn)
    if not ids:
        return 0
    frames = []
    for t in (meta.get("header_table"), meta.get("line_table")):
        if not t or t not in cols:
            continue
        cs, df = cols[t], _read(conn, t)
        fk = _fk_to_orders(df, cs, ids)
        if fk is None:
            continue
        pick = {
            "amount_due": _pick(cs, "amount_due", ("tongthanhtoan", "phaitra", "phaithu", "tongbill", "cantra", "tongsaugiam", "sausaugiam", "tongtiensau"), "number"),
            "paid_total": _pick(cs, "paid", ("khachtra", "dathu", "datra", "dathanhtoan", "khachdua"), "number"),
            "debt_snapshot": _pick(cs, "debt_remaining", ("conno", "congno", "duno", "nohientai", "chuatra", "conlai"), "number"),
            "ship_fee": _pick(cs, (), ("phiship", "phigiao", "phivanchuyen", "ship"), "number"),
            "order_discount": _first(cs, lambda c: c.kind == "number" and c.desc and (c.role == "order_level" or
                                     (c.role == "discount" and t == meta.get("header_table")))
                                     and re.match(r"(giam gia|tien giam|chiet khau)", strip_diacritics(c.desc).lower())
                                     and "dong" not in strip_diacritics(c.desc).lower())
            or (_pick(cs, (), ("giamgiahd", "giamgiadon", "tiengiam", "giamgia", "chietkhau"), "number")
                if t == meta.get("header_table") else None),
        }
        if pick["amount_due"] is None and t == meta.get("header_table"):  # cột đúng tên "Tổng tiền" (khác "Tổng tiền hàng")
            pick["amount_due"] = _first(cs, lambda c: c.kind == "number" and bool(c.keys & {"tongtien", "tongcong"}))
        if pick["amount_due"] is None and t == meta.get("header_table"):  # "Thành tiền: tổng tiền đơn" ở bảng đơn
            pick["amount_due"] = _first(cs, lambda c: c.kind == "number" and c.role == "order_level"
                                        and c.desc and re.match(r"tong tien (cua )?(ca )?don", strip_diacritics(c.desc).lower()))
        pick = {k: v for k, v in pick.items() if v is not None}
        if not pick:
            continue
        sub = df[[fk.name] + [c.name for c in pick.values()]].copy()
        sub.columns = ["order_id"] + list(pick)
        sub["order_id"] = _id_str(sub["order_id"])
        frames.append(sub.groupby("order_id").first())  # cột cấp đơn lặp lại mọi dòng -> lấy 1
        meta.update({f"money.{k}": f"{t}.{c.name}" for k, c in pick.items()})
    if not frames:
        return 0
    out = frames[0]
    for f in frames[1:]:
        out = out.combine_first(f)
    for k in ("amount_due", "paid_total", "debt_snapshot", "ship_fee", "order_discount"):
        if k not in out:
            out[k] = None
    out = out.reset_index()[["order_id", "amount_due", "paid_total", "debt_snapshot", "ship_fee", "order_discount"]]
    if out["amount_due"].isna().all():  # §4: tổng đơn = tiền hàng − giảm giá cả đơn + phí giao
        rev = dict(conn.execute("SELECT order_id, revenue FROM std_orders").fetchall())
        out["amount_due"] = (out["order_id"].map(rev).astype(float) - pd.to_numeric(out["order_discount"], errors="coerce").fillna(0)
                             + pd.to_numeric(out["ship_fee"], errors="coerce").fillna(0))
        meta["money.amount_due"] = "(tính: doanh thu − giảm đơn + ship)"
    conn.execute("DROP TABLE IF EXISTS std_order_money")
    out.to_sql("std_order_money", conn, index=False)
    return len(out)


# ----------------------------- phiếu thu ---------------------------------------

_PAY_TABLE = ("thanhtoan", "phieuthu", "soquy", "thutien", "payment", "dulieubanhang")


def build_payments(conn, cols, meta: dict) -> int:
    ids = _std_ids(conn)
    skip = {meta.get("header_table"), meta.get("line_table")}
    best = None
    for t, cs in cols.items():
        if t in skip:
            continue
        if any(c.role in ("qty", "qty_base") or (c.role in (None, "other") and c.has("soluong")) for c in cs):
            continue  # bảng dòng hàng/nhập, không phải phiếu thu
        amt = _pick(cs, ("paid", "line_revenue"), ("sotienthu", "tienthu", "sotien", "giatri", "tien", "amount"), "number")
        dt = _best(cs, lambda c: 3 if c.kind == "date" else
                   (2 if c.kind == "text" and c.has("thoidiem", "thoigian", "ngay", "date", "time") else 0))
        if not (amt and dt):
            continue
        df = _read(conn, t)
        fk = _fk_to_orders(df, cs, ids, 0.6)
        if fk is None:
            continue
        score = _id_str(df[fk.name]).isin(ids).mean() + (0.5 if any(w in _key(t) for w in _PAY_TABLE) else 0)
        if best is None or score > best[0]:
            best = (score, t, df, cs, fk, amt, dt)
    if best is None:
        return 0
    _, t, df, cs, fk, amt, dt = best
    method = _pick(cs, "pay", ("phuongthuc", "pttt", "hinhthuc", "loaitt", "httt"), "text")
    pay = pd.DataFrame({"order_id": _id_str(df[fk.name]), "paid_at": df[dt.name].astype("string"),
                        "amount": pd.to_numeric(df[amt.name], errors="coerce"),
                        "method": df[method.name] if method else None})
    pay["paid_date"] = pay["paid_at"].str.slice(0, 10)
    conn.execute("DROP TABLE IF EXISTS std_payments")
    pay[["order_id", "paid_at", "paid_date", "amount", "method"]].to_sql("std_payments", conn, index=False)
    meta.update({"payments.table": t, "payments.order_id": fk.name, "payments.amount": amt.name, "payments.date": dt.name,
                 "payments.method": method.name if method else ""})
    return len(pay)


# ----------------------------- nhập hàng ---------------------------------------

def build_purchases(conn, cols, meta: dict) -> int:
    """Gộp bảng nhập (đầu phiếu + chi tiết nếu tách) thành 1 dòng / dòng nhập."""
    cand = []
    for t, cs in cols.items():
        tk = _key(t)
        desc_hit = any(c.desc and "nhap" in strip_diacritics(c.desc).lower() for c in cs)
        if not ("nhap" in tk or "purchase" in tk or desc_hit):
            continue
        cand.append(t)
    if not cand:
        return 0
    parts = []
    for t in cand:
        cs, df = cols[t], _read(conn, t)
        doc = _first(cs, lambda c: c.kind == "text" and (c.has("phieunhap", "sophieu", "manhap", "madonnhap", "sochungtu")
                                                        or (c.desc and "ma phieu nhap" in strip_diacritics(c.desc).lower())))
        parts.append((t, cs, df, doc))
    # bảng có cột ngày = đầu phiếu (hoặc phẳng); bảng không ngày = chi tiết, nối qua mã phiếu
    heads = [p for p in parts if _best(p[1], _date_rank) is not None]
    details = [p for p in parts if _best(p[1], _date_rank) is None]
    if not heads:
        return 0
    rows = []
    for t, cs, df, doc in heads:
        d = _best(cs, _date_rank)
        base = df.copy()
        if doc is not None:
            for dt_, dcs, ddf, ddoc in details:
                if ddoc is not None and ddf[ddoc.name].astype(str).isin(set(df[doc.name].astype(str))).mean() > 0.5:
                    base = ddf.merge(df, left_on=ddoc.name, right_on=doc.name, how="left", suffixes=("", "_h"))
                    cs = dcs + [c for c in cs if c.name not in {x.name for x in dcs}]
                    break
        name = _first(cs, _is_product_name) or _first(cs, _is_product_code) or \
            _first(cs, lambda c: c.kind == "text" and c.has("product", "tenhang", "mathang", "hanghoa") and not c.has("cuahang", "khach"))
        qty = _first(cs, lambda c: c.kind == "number" and c.has("soluong", "slnhap", "sl"))
        unit = _first(cs, lambda c: c.kind == "text" and c.has("dvt", "donvi"))
        price = _first(cs, lambda c: c.kind == "number" and c.has("gianhap", "dongia", "dongianhap"))
        total = _first(cs, lambda c: c.kind == "number" and c.has("thanhtien") and not c.has("phieu"))
        doc_total = _first(cs, lambda c: c.kind == "number" and c.has("tongtienphieu", "cantrancc", "tonggiatrinhap", "tongphieu"))
        supp = _first(cs, lambda c: c.kind == "text" and c.has("nhacungcap", "ncc"))
        status = _first(cs, lambda c: c.kind == "text" and c.has("trangthai", "tinhtrang"))
        note = _first(cs, lambda c: c.kind == "text" and c.has("ghichu", "luuy", "note", "diengiai"))
        get = (lambda c: base[c.name] if c is not None and c.name in base else None)
        qv, pv = pd.to_numeric(get(qty), errors="coerce") if qty else None, pd.to_numeric(get(price), errors="coerce") if price else None
        lt = pd.to_numeric(get(total), errors="coerce") if total else (qv * pv if qty and price else None)
        st = get(status)
        cancelled = st.map(lambda v: int("huy" in _key(v))) if st is not None else 0
        rows.append(pd.DataFrame({
            "purchase_id": get(doc), "purchase_date": _split_dt(base[d.name])[0], "supplier": get(supp),
            "product_name": get(name), "qty": qv, "unit": get(unit), "unit_cost": pv, "line_total": lt,
            "doc_total": pd.to_numeric(get(doc_total), errors="coerce") if doc_total else None,
            "is_cancelled": cancelled, "note": get(note)}))
        meta[f"purchases.{t}"] = ", ".join(f"{k}={v.name}" for k, v in (("doc", doc), ("date", d), ("supplier", supp), ("name", name),
                                                                          ("qty", qty), ("price", price), ("total", total)) if v is not None)
    out = pd.concat(rows, ignore_index=True)
    try:  # cột hàng là MÃ -> đổi ra tên theo std_products (giữ mã ở product_id)
        pm = dict(conn.execute("SELECT product_id, product_name FROM std_products WHERE product_id IS NOT NULL").fetchall())
        out["product_id"] = out["product_name"].where(out["product_name"].astype(str).isin(set(map(str, pm))))
        out["product_name"] = out["product_name"].map(lambda v: pm.get(v, pm.get(str(v), v)))
    except sqlite3.Error:
        out["product_id"] = None
    conn.execute("DROP TABLE IF EXISTS std_purchases")
    out.to_sql("std_purchases", conn, index=False)
    return len(out)


# ----------------------------- sổ phụ (điện thoại, dịch vụ) -----------------------

def _col(cs, *hints, kind=None, exclude=()):
    return _first(cs, lambda c: (kind is None or c.kind == kind) and c.has(*hints) and not c.has(*exclude))


def _ref_series(df: pd.DataFrame, c: Col | None) -> pd.Series | None:
    """Mã tham chiếu đơn: số ('33.0' -> '33') hoặc chữ chứa số ('STT 34' -> '34')."""
    if c is None:
        return None
    return df[c.name].map(lambda v: (m.group(0) if (m := re.search(r"\d+", str(v).replace(".0", ""))) else None)
                          if v is not None and str(v) != "nan" else None)


def build_installments(conn, cols, meta: dict) -> int:
    """Sổ góp: dòng đầu = hợp đồng (giá, trả trước, số kỳ, góp/tháng, ngày hạn), các dòng sau = lần góp."""
    for t, cs in cols.items():
        n_terms = _col(cs, "soky", kind="number")
        monthly = _col(cs, "gopthang", "gopmoithang", "tienkygop", kind="number")
        if not (n_terms and monthly):
            continue
        df = _read(conn, t)
        ref = _col(cs, "stt", "sohd", "madon", "trucuastt") or _col(cs, "ghichu")
        key = _ref_series(df, ref) if ref is not None else None
        head = df[n_terms.name].notna()
        if key is None:  # không có mã: dòng đầu hợp đồng mở nhóm mới
            key = head.cumsum().astype(str)
        key = key.ffill()
        date = _best(cs, lambda c: 3 if c.kind == "date" and c.has("ngay") and not c.has("gop", "tra", "han") else 0)
        g = lambda *h, kind=None: _col(cs, *h, kind=kind)  # noqa: E731
        cust, phone, item = g("khach", kind="text"), g("sdt", "dienthoai", kind="text"), g("may", "hang", "sanpham", kind="text")
        price, prepaid = g("gia", "tongtien", kind="number"), g("tratruoc", kind="number")
        due_day = g("hangop", "ngayhan", kind="number")
        pay_d, pay_a = _col(cs, "ngaygop", "ngaytra", kind="date"), _col(cs, "dagop", "datra", "sotiengop", kind="number")
        hd = df[head]
        contracts = pd.DataFrame({
            "contract_id": key[head].values, "buy_date": _split_dt(hd[date.name])[0].values if date else None,
            "customer": hd[cust.name].values if cust else None, "phone": hd[phone.name].values if phone else None,
            "item": hd[item.name].values if item else None, "price": hd[price.name].values if price else None,
            "prepaid": hd[prepaid.name].values if prepaid else None, "n_terms": hd[n_terms.name].values,
            "monthly": hd[monthly.name].values, "due_day": hd[due_day.name].values if due_day else None})
        pays = pd.DataFrame({"contract_id": key, "paid_date": _split_dt(df[pay_d.name])[0] if pay_d else None,
                             "amount": pd.to_numeric(df[pay_a.name], errors="coerce") if pay_a else None}).dropna(subset=["amount"])
        for name, frame in (("std_installments", contracts), ("std_installment_payments", pays)):
            conn.execute(f"DROP TABLE IF EXISTS {name}")
            frame.to_sql(name, conn, index=False)
        meta["installments.table"] = t
        return len(contracts)
    return 0


def build_debt_book(conn, cols, meta: dict) -> int:
    """Sổ nợ tay (tách khỏi đơn): dòng đầu = khoản nợ sau lúc mua, các dòng sau = lần trả."""
    for t, cs in cols.items():
        debt = _col(cs, "no", "conno", "sono", kind="number", exclude=("datra", "phaitra"))
        paid = _col(cs, "datra", "tientra", kind="number")
        pay_d = _col(cs, "ngaytra", kind="date")
        if not (debt and paid and pay_d) or debt.has("gop"):
            continue
        if t in (meta.get("header_table"), meta.get("line_table")):
            continue
        df = _read(conn, t)
        ref = _col(cs, "ghichu", "stt", "sohd", "madon")
        head = df[debt.name].notna()
        key = (_ref_series(df, ref).ffill() if ref is not None else None)
        key = key if key is not None and key.notna().mean() > 0.9 else head.cumsum().astype(str)
        date = _best(cs, lambda c: 3 if c.kind == "date" and not c.has("tra") else 0)
        cust, phone = _col(cs, "khach", kind="text"), _col(cs, "sdt", "dienthoai", kind="text")
        hd = df[head]
        book = pd.DataFrame({"order_ref": key[head].values, "buy_date": _split_dt(hd[date.name])[0].values if date else None,
                             "customer": hd[cust.name].values if cust else None, "phone": hd[phone.name].values if phone else None,
                             "debt_after_purchase": hd[debt.name].values})
        pays = pd.DataFrame({"order_ref": key, "paid_date": _split_dt(df[pay_d.name])[0],
                             "amount": pd.to_numeric(df[paid.name], errors="coerce")}).dropna(subset=["amount"])
        for name, frame in (("std_debt_book", book), ("std_debt_book_payments", pays)):
            conn.execute(f"DROP TABLE IF EXISTS {name}")
            frame.to_sql(name, conn, index=False)
        meta["debt_book.table"] = t
        return len(book)
    return 0


def build_tradeins(conn, cols, meta: dict) -> int:
    """Thu máy cũ: bảng riêng (giá thu + STT đơn bị trừ) hoặc cột tiền thu máy cũ trên sổ bán."""
    from data_loader import parse_number
    ids = _std_ids(conn)
    frames = []
    for t, cs in cols.items():
        val = _col(cs, "giathu", "thumaycu", "tienthumay", "trogia")
        if val is None:
            continue
        df = _read(conn, t)
        ref = _col(cs, "truvao", "stt", "sohd", "madon") if t != meta.get("line_table") else \
            next((c for c in cs if c.name == meta.get("line.order_id")), None)
        if ref is None:
            continue
        fr = pd.DataFrame({"order_id": _ref_series(df, ref) if t != meta.get("line_table") else _id_str(df[ref.name]).ffill(),
                           "tradein_value": df[val.name].map(lambda v: parse_number(v) if v is not None else None),
                           "device": df[_col(cs, "maythu", "maycu").name] if _col(cs, "maythu", "maycu") else None,
                           "trade_date": _split_dt(df[_best(cs, _date_rank).name])[0] if _best(cs, _date_rank) else None})
        fr = fr.dropna(subset=["tradein_value"]).drop_duplicates()
        if ids and fr["order_id"].isin(ids).mean() > 0.5:
            frames.append(fr)
            meta[f"tradein.{t}"] = f"{ref.name}, {val.name}"
    if not frames:
        return 0
    out = pd.concat(frames).drop_duplicates(subset=["order_id", "tradein_value"])
    conn.execute("DROP TABLE IF EXISTS std_tradeins")
    out.to_sql("std_tradeins", conn, index=False)
    return len(out)


def build_warranties(conn, cols, meta: dict) -> int:
    """Sổ bảo hành thiết bị (mỗi lượt mang máy tới một dòng). Sổ sửa xe đã có std_repairs.is_warranty."""
    from std_layer import canon_models
    for t, cs in cols.items():
        if "baohanh" not in _key(t) and not (_col(cs, "ngaynhan", exclude=("ngaynhanviec",)) and _col(cs, "loi", "xuly", "imei")):
            continue
        d = _col(cs, "ngaynhan", kind="date") or _best(cs, _date_rank)
        model = _col(cs, "may", "dongmay", "sanpham", kind="text", exclude=("maythu",))
        if d is None:
            continue
        df = _read(conn, t)
        w = pd.DataFrame({"service_date": _split_dt(df[d.name])[0],
                          "customer": df[_col(cs, "khach").name] if _col(cs, "khach") else None,
                          "phone": df[_col(cs, "sdt").name] if _col(cs, "sdt") else None,
                          "model_raw": df[model.name] if model else None,
                          "imei": df[_col(cs, "imei").name].map(lambda v: None if v is None else str(int(v)) if isinstance(v, float) else str(v))
                          if _col(cs, "imei") else None,
                          "buy_date": _split_dt(df[_col(cs, "ngaymua").name])[0] if _col(cs, "ngaymua") else None,
                          "issue": df[_col(cs, "loi").name] if _col(cs, "loi") else None,
                          "action": df[_col(cs, "xuly").name] if _col(cs, "xuly") else None})
        if model:
            cm = canon_models(w["model_raw"].dropna().unique().tolist())
            w["model"] = w["model_raw"].map(lambda v: cm.get(v, v))
        conn.execute("DROP TABLE IF EXISTS std_warranties")
        w.to_sql("std_warranties", conn, index=False)
        meta["warranty.table"] = t
        return len(w)
    return 0


# ----------------------------- khách hàng ---------------------------------------

_WALKIN = {"", "le", "khachle", "kl", "khachvanglai", "vanglai", "khach", "none", "nan"}


def customer_key(cid, name) -> str | None:
    """§3: có mã khách thì theo mã; không thì theo tên (chỉ khác hoa/thường/dấu/khoảng trắng là một) + SĐT nếu có.
    Khách lẻ / không ghi -> None (không phải khách)."""
    if cid is not None and str(cid).strip() not in ("", "nan", "None"):
        return "id:" + str(cid).strip()
    if name is None:
        return None
    raw = str(name)
    digits = re.sub(r"\D", "", raw)
    letters = re.sub(r"[^a-z]", "", strip_diacritics(re.sub(r"\d", "", raw)).lower())
    if letters in _WALKIN:
        return None
    return "nm:" + letters + (("|" + digits[-9:]) if len(digits) >= 9 else "")


def _strip_phone(x: str) -> str:
    """'Dương Hùng Trung - 0328 548 455', 'Đặng Hải Nhung (0913885954)' -> tên."""
    return re.sub(r"\s*[-–(,]?\s*\(?\+?\d[\d .]{7,}\)?\s*$", "", x).strip()


def _best_name(s: pd.Series):
    """Cách viết phổ biến nhất, ưu tiên bản có dấu ('Hứa Bích An' hơn 'Hua Bich An')."""
    s = s.dropna()
    if s.empty:
        return None
    vc = s.value_counts()
    accented = [n for n in vc.index if str(n) != strip_diacritics(str(n))]
    return accented[0] if accented else vc.index[0]


def build_customers(conn, cols, meta: dict) -> int:
    o = pd.read_sql_query("SELECT rowid AS rid, customer_id, customer_name FROM std_orders", conn)
    if o.empty:
        return 0
    has_ids = o["customer_id"].notna().any()
    o["customer_key"] = [customer_key(i if has_ids else None, n) for i, n in zip(o["customer_id"], o["customer_name"])]
    # ghi customer_key vào std_orders
    cur = pd.read_sql_query("SELECT * FROM std_orders", conn)
    cur["customer_key"] = o["customer_key"].values
    conn.execute("DROP TABLE std_orders")
    cur.to_sql("std_orders", conn, index=False)
    conn.execute("CREATE INDEX ix_std_orders_date ON std_orders(order_date)")
    named = cur.dropna(subset=["customer_key"])
    cust = named.groupby("customer_key").agg(
        customer_id=("customer_id", "first"),
        customer_name=("customer_name", _best_name)).reset_index()
    for c in ("customer_group", "phone", "register_date"):
        cust[c] = None
    # thuộc tính từ bảng khách (nối theo mã, không có mã thì theo tên)
    for t, cs in cols.items():
        if t in (meta.get("header_table"), meta.get("line_table")):
            continue
        grp = _first(cs, lambda c: c.kind == "text" and (c.role == "customer_group" or c.has("nhomkhach", "loaikhach", "nhomkh", "hangkhach", "hangthanhvien")
                                                         or bool(c.keys & {"hang", "nhom", "capdo"})))
        reg = _first(cs, lambda c: c.kind == "date" and c.has("dangky", "ngaytao", "taoluc"))
        ph = _first(cs, lambda c: c.kind == "text" and c.has("sdt", "dienthoai", "phone", "didong"))
        nm = _first(cs, lambda c: c.kind == "text" and c.role in (None, "other", "customer_name", "product_name") and
                    c.has("tenkhach", "khachhangten", "hoten", "tenkh", "customername", "khachhang") and not c.has("ma", "id", "loai"))
        if not (grp or reg or ph):
            continue
        df = _read(conn, t)
        key = None
        for c in cs:
            if c.kind != "text":
                continue
            if has_ids:
                k = df[c.name].map(lambda v: customer_key(v, None))
            else:
                k = [customer_key(None, n) for n in df[c.name]]
                k = pd.Series(k)
            hit = pd.Series(k).isin(set(cust["customer_key"])).sum()
            if hit >= 5 and (key is None or hit > key[0]):
                key = (hit, pd.Series(k).values)
        if key is None:
            continue
        df["_k"] = key[1]
        df = df.drop_duplicates("_k").set_index("_k")
        for field, c in (("customer_group", grp), ("register_date", reg), ("phone", ph)):
            if c is not None:
                cust[field] = cust[field].where(cust[field].notna(), cust["customer_key"].map(df[c.name]))
        if nm is None:  # tên cột lạ ('kh', 'tengoi', 'khach_le_si'): nhận theo giá trị — tên người 2–5 chữ, không số, gần như duy nhất
            def _looks_name(col):
                v = df[col].dropna().astype(str)
                return len(v) > 0 and v.nunique() >= 0.5 * len(v) and \
                    v.map(lambda x: 2 <= len(_strip_phone(x).split()) <= 5 and not re.search(r"\d", _strip_phone(x))).mean() >= 0.8
            nm = _first(sorted(cs, key=lambda c: -c.n_distinct), lambda c: c.kind == "text" and _looks_name(c.name))
        if nm is not None and has_ids:  # tên chuẩn trong bảng khách (đơn chỉ ghi mã, hoặc ghi nhiều cách viết)
            names = df[nm.name].map(lambda x: _strip_phone(str(x)) if x is not None and x == x else None)
            cust["customer_name"] = cust["customer_key"].map(names).where(lambda x: x.notna(), cust["customer_name"])
        meta["customers.attrs_table"] = t
    # khách đăng ký thành viên nhưng chưa mua (rulebook mẹ & bé: thành viên mới tính cả khách chưa mua)
    conn.execute("DROP TABLE IF EXISTS std_customers")
    cust.to_sql("std_customers", conn, index=False)
    meta["customers.key"] = "customer_id" if has_ids else "tên+SĐT"
    return len(cust)


# ----------------------------- kho: tồn, lô ---------------------------------------

_STOCK_EXCL = ("dinhmuc", "lonnhat", "nhonhat", "toithieu", "toida", "tonmin", "tonmax")


def build_stock(conn, cols, meta: dict) -> int:
    end = None
    try:
        end = conn.execute("SELECT value FROM _meta_info WHERE key = 'data_end_date'").fetchone()[0] or None
    except (sqlite3.Error, TypeError):
        pass
    snaps, ledger = [], None
    for t, cs in cols.items():
        q = _first(cs, lambda c: c.kind == "number" and not c.has(*_STOCK_EXCL) and (
            c.has("toncuoi", "tonthuc", "tonkho", "soluongton") or any(k == "ton" or (k.startswith("ton") and not k.startswith("tong"))
                                                                     for k in c.keys)))
        prod = _first(cs, _is_product_name) or _first(cs, _is_product_code)
        if not (q and prod):
            continue
        d = _best(cs, lambda c: 3 if c.kind == "date" and c.has("phatsinh", "ngay", "thoigian") else 0)
        df = _read(conn, t)
        code = _first(cs, _is_product_code)
        if d is not None and len(df) > 3 * df[prod.name].nunique():  # sổ thẻ kho: tồn sau mỗi phát sinh
            ledger = pd.DataFrame({"product_ref": df[prod.name], "ts": df[d.name].astype("string"),
                                   "balance_base": pd.to_numeric(df[q.name], errors="coerce")})
            meta["stock.ledger"] = f"{t}.{q.name}"
            continue
        unit = _first(cs, lambda c: c.kind == "text" and (c.role == "base_unit" or c.has("dvt", "donvi", "dvgoc")))
        snaps.append(pd.DataFrame({"product_id": df[code.name] if code else None,
                                   "product_name": df[_first(cs, _is_product_name).name] if _first(cs, _is_product_name) else None,
                                   "qty_on_hand_base": pd.to_numeric(df[q.name], errors="coerce"),
                                   "unit": df[unit.name] if unit else None, "snapshot_date": end}))
        meta[f"stock.{t}"] = q.name
    n = 0
    if snaps:
        out = pd.concat(snaps, ignore_index=True)
        pm = dict(conn.execute("SELECT product_id, product_name FROM std_products WHERE product_id IS NOT NULL").fetchall()) \
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='std_products'").fetchone() else {}
        out["product_name"] = out["product_name"].where(out["product_name"].notna(), out["product_id"].map(pm))
        conn.execute("DROP TABLE IF EXISTS std_stock")
        out.to_sql("std_stock", conn, index=False)
        n = len(out)
    if ledger is not None:
        conn.execute("DROP TABLE IF EXISTS std_stock_ledger")
        ledger.to_sql("std_stock_ledger", conn, index=False)
    return n


def build_lots(conn, cols, meta: dict) -> int:
    """Lô nhập (mã lô + hạn dùng + hàng) và bán theo lô (mã lô + mã đơn + số lượng)."""
    ids = _std_ids(conn)
    lots, sales = None, None
    for t, cs in cols.items():
        lot = _first(cs, lambda c: c.kind == "text" and (any(k in ("lo", "malo", "solo") for k in c.keys) or c.has("malo", "solo")))
        if lot is None:
            continue
        exp = _first(cs, lambda c: c.kind == "date" and c.has("hsd", "handung", "hansudung", "hethan"))
        df = _read(conn, t)
        if exp is not None and lots is None:
            prod = _first(cs, _is_product_code) or _first(cs, _is_product_name)
            qty = _first(cs, lambda c: c.kind == "number" and c.has("soluong", "sl", "nhap") and not c.has("gia"))
            cost = _first(cs, lambda c: c.kind == "number" and c.has("gianhap", "dongianhap", "dongia"))
            doc = _first(cs, lambda c: c.kind == "text" and c.has("phieunhap", "donnhap", "maphieu", "sophieu"))
            lots = pd.DataFrame({"lot_id": df[lot.name], "product_ref": df[prod.name] if prod else None,
                                 "expiry_date": _split_dt(df[exp.name])[0],
                                 "qty_in": pd.to_numeric(df[qty.name], errors="coerce") if qty else None,
                                 "qty_in_is_base": int(bool(qty and qty.has("quydoi", "coban"))),
                                 "unit_cost": pd.to_numeric(df[cost.name], errors="coerce") if cost else None,
                                 "purchase_id": df[doc.name] if doc else None})
            meta["lots.table"] = t
        elif exp is None and sales is None:
            fk = _fk_to_orders(df, cs, ids, 0.5)
            qty = _first(cs, lambda c: c.kind == "number" and (c.role in ("qty", "qty_base") or c.has("soluong", "sl"))
                         and not c.has("tra"))
            ret = _first(cs, lambda c: c.kind == "number" and c.has("tra"))
            if fk is not None and qty is not None:
                sales = pd.DataFrame({"order_id": _id_str(df[fk.name]), "lot_id": df[lot.name],
                                      "qty": pd.to_numeric(df[qty.name], errors="coerce"),
                                      "qty_returned": pd.to_numeric(df[ret.name], errors="coerce") if ret else 0})
                meta["lot_sales.table"] = t
    for name, fr in (("std_lots", lots), ("std_lot_sales", sales)):
        if fr is not None:
            conn.execute(f"DROP TABLE IF EXISTS {name}")
            fr.to_sql(name, conn, index=False)
    return 0 if lots is None else len(lots)


def build_extra(conn: sqlite3.Connection) -> dict:
    cols = _load_cols(conn)
    meta = dict(conn.execute("SELECT role, source FROM _meta_std").fetchall())
    # xoá bảng phụ của lần chạy trước: bảng nào lần này không phát hiện được thì không được sót lại
    core = {"std_orders", "std_lines", "std_products", "std_repairs"}
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'std_%'").fetchall():
        if t not in core:
            conn.execute(f"DROP TABLE {t}")
    meta = {k: v for k, v in meta.items() if not k.startswith(("money.", "payments.", "purchases.", "installments.", "debt_book.",
                                                               "tradein.", "warranty.", "customers.", "stock.", "lots.", "lot_sales.", "n_"))}
    extra = {"n_order_money": build_order_money(conn, cols, meta), "n_payments": build_payments(conn, cols, meta),
             "n_purchases": build_purchases(conn, cols, meta), "n_installments": build_installments(conn, cols, meta),
             "n_debt_book": build_debt_book(conn, cols, meta), "n_tradeins": build_tradeins(conn, cols, meta),
             "n_warranties": build_warranties(conn, cols, meta), "n_customers": build_customers(conn, cols, meta),
             "n_stock": build_stock(conn, cols, meta), "n_lots": build_lots(conn, cols, meta)}
    meta.update({k: str(v) for k, v in extra.items()})
    conn.execute("DROP TABLE IF EXISTS _meta_std")
    pd.DataFrame(sorted(meta.items()), columns=["role", "source"]).to_sql("_meta_std", conn, index=False)
    conn.commit()
    return {k: v for k, v in meta.items() if k.startswith(("money.", "payments.", "n_", "installments.", "debt_book.",
                                                            "tradein.", "warranty.", "customers.", "stock.",
                                                            "lots.", "lot_sales."))}


if __name__ == "__main__":
    import sys

    for path in sys.argv[1:]:
        with sqlite3.connect(path) as c:
            print(path, build_extra(c))
