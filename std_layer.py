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
from functools import lru_cache
import sqlite3

import pandas as pd

from data_loader import sql_quote, strip_diacritics

DONE, CANCEL, RETURN, SHIPPING = "hoàn thành", "huỷ", "trả hàng", "đang giao"


@lru_cache(maxsize=500_000)
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

# Mô tả cột trong README ("## Cột trong `File`" + "- **Tên cột**: mô tả") -> vai trò. Tín hiệu mạnh hơn tên cột:
# tên cột ở shop mới có thể lạ, nhưng mô tả dùng cùng từ vựng. So khớp trên mô tả đã bỏ dấu, theo thứ tự.
_DESC_ROLES: list[tuple[str, str]] = [
    ("empty", r"^cot trong"),
    ("order_goods", r"^tien hang cua (ca )?don|^tong tien hang cua (ca )?don(?!.*lap lai)"),
    ("debt_remaining", r"^(so )?(khach )?con no|^so (khach )?con no|^no\b|^cong no"),
    ("paid", r"^da thanh toan|^khach (da )?tra\b|^da thu|^so tien (khach )?(da )?tra"),
    ("amount_due", r"(so )?khach (phai|can) tra|^tong (tien )?(phai )?(thanh toan|phai tra)"),
    ("order_level", r"lap lai o moi dong|cua ca (don|phieu|hoa don)\b(?!.*cua ca dong)|^tong tien (cua )?(ca )?(phieu|don)|"
                    r"^khach (can|phai) tra cho ca don"),
    ("order_id", r"^(ma don|so hoa don|ma hoa don|so ct|ma bill|so don|don so|ma giao dich|ma chung tu)\b(?! nhap)|"
                 r"^so thu tu (don|hoa don)\b"),
    ("customer_id", r"^ma khach"), ("customer_name", r"^ten khach|^khach hang|^ten kh\b"),
    ("employee", r"nhan vien|^tho\b|^ma nv|nguoi ban"),
    ("product_code", r"^ma (hang|san pham|sku|mat hang|thuoc)\b"),
    ("product_name", r"^ten (hang|san pham|mon|thuoc|mat hang)\b|^ten$|^mat hang$|^hang hoa$"),
    ("qty_base", r"(da )?doi ra don vi co ban|quy ra (dv|don vi) le|so luong .*don vi co ban"),
    ("qty", r"^so luong\b(?! nhap)"),
    ("base_unit", r"^don vi co ban"), ("big_unit", r"^don vi (lon|quy doi)"), ("unit", r"^don vi tinh|^dvt\b"),
    ("line_revenue", r"^thanh tien (sau giam|cua dong)|^thanh tien$|^tien cua dong|^tri gia dong"),
    ("discount", r"giam gia|tien giam|chiet khau"),
    ("cost", r"^gia von"), ("price", r"^don gia|^gia ban|^gia 1|^gia niem yet"),
    ("status", r"^trang thai (don|xu ly)"), ("status_note", r"don huy.*ghi o day|trang thai .*ghi o (cot )?ghi chu"),
    ("channel", r"^kenh"), ("pay", r"hinh thuc thanh toan|phuong thuc thanh toan"),
    ("category", r"^nhom hang|^loai hang|^danh muc|^nhom$"),
    ("brand", r"^hang$|^hang san xuat|^thuong hieu|^nhan hieu"), ("size", r"^so ?/ ?size|^size|^so cua sua"),
    ("active_ingredient", r"hoat chat|thanh phan"), ("prescription", r"ke don|ban theo don"), ("customer_group", r"^nhom khach|^loai khach"),
    ("time", r"^gio\b|thoi diem dat don|^gio dat"), ("date", r"^ngay (ban|dat|lap|hoa don|giao dich)\b"),
]


def desc_role(desc: str | None) -> str | None:
    if not desc:
        return None
    d = re.sub(r"\s+", " ", strip_diacritics(desc).lower()).strip()
    return next((role for role, rx in _DESC_ROLES if re.search(rx, d)), "other")


def readme_columns(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """(bảng DB, _key(tên cột gốc)) -> mô tả cột trong README. Nối file README -> bảng qua _meta_tables.source."""
    try:
        text = conn.execute("SELECT content FROM _meta_readme").fetchone()[0] or ""
        sources = conn.execute("SELECT table_name, source FROM _meta_tables").fetchall()
    except (sqlite3.Error, TypeError):
        return {}
    out: dict[tuple[str, str], str] = {}
    tables: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##+\s*Cột trong `([^`]+)`(?:,\s*sheet `([^`]+)`)?", line.strip())
        if m:
            f, sheet = m.group(1).lower(), (m.group(2) or "").lower()
            tables = [t for t, src in sources if any(
                part.lower().startswith(f + "[") and (not sheet or part.lower() == f"{f}[{sheet}]")
                for part in (src or "").split("; "))]
            continue
        if line.startswith("## "):
            tables = []
            continue
        mq = re.search(r"dòng cùng \*\*(.+?)\*\*[^.]{0,30}?là (một|1) (đơn|hoá đơn|hóa đơn|phiếu)", line)
        if mq:  # cột khoá đơn khai ở phần Quy ước: áp cho mọi bảng có cột đó (chưa có mô tả riêng)
            for t, _src in sources:
                out.setdefault((t, _key(mq.group(1))), "số thứ tự đơn (hoá đơn)")
        m = re.match(r"^\s*-\s*\*\*(.+?)\*\*\s*:\s*(.+)$", line)
        if m and tables:
            for t in tables:
                out[(t, _key(m.group(1)))] = m.group(2).strip()
    return out


class Col:
    def __init__(self, table: str, name: str, original: str, kind: str, n_distinct: int, top: str,
                 desc: str | None = None):
        self.table, self.name, self.kind, self.n_distinct = table, name, kind, n_distinct or 0
        self.keys = {_key(name), _key(original)} - {""}
        self.has_time = bool(re.search(r"\d{1,2}:\d{2}", top or ""))
        self.desc = desc
        self.role = desc_role(desc)  # None = README không mô tả cột này -> dùng heuristic theo tên

    def has(self, *words: str) -> bool:
        return any(w in k for k in self.keys for w in words)

    def starts(self, *words: str) -> bool:
        return any(k.startswith(words) for k in self.keys)


_PERSON = ("kh", "khach", "nv", "nhanvien", "ncc", "nhacungcap", "customer", "employee", "supplier")


def _is_order_id(c: Col) -> bool:
    if c.role not in (None, "other"):
        return c.role == "order_id" and c.kind in ("text", "number")  # README nói STT số là mã đơn
    return c.kind == "text" and not c.has("chitiet", "phieunhap", "nhaphang") and (
        c.has("hoadon", "donhang", "bill", "giaodich", "orderid", "madon", "sodon", "donso", "sohd", "mahd", "magd",
              "phieu", "chungtu", "soct", "mact")
        or any(k in {"order", "orderid", "id"} for k in c.keys)
    )


def _is_product_code(c: Col) -> bool:
    if c.role not in (None, "other"):
        return c.role == "product_code" and c.kind == "text"
    return c.kind == "text" and c.has("mahang", "masp", "mahh", "masanpham", "mamon", "mamathang",
                                      "idsp", "productid", "sku", "mathuoc")


def _is_product_name(c: Col) -> bool:
    if c.role not in (None, "other"):
        return c.role == "product_name" and c.kind == "text"
    if c.kind != "text" or _is_product_code(c):
        return False
    if c.starts("ten") and not c.has(*[f"ten{p}" for p in _PERSON]):
        return True
    return any(k in {"mon", "hang", "hanghoa", "sanpham", "mathang", "thuoc", "product", "item",
                     "noidung", "chitiethang", "tenhang", "dichvu"} or k.startswith(("hangban", "mathang", "hanghoa"))
               for k in c.keys)


def _is_qty(c: Col) -> bool:
    if c.role not in (None, "other"):
        return c.role == "qty" and c.kind == "number"
    return c.kind == "number" and c.starts("soluong", "sl", "qty", "quantity")


def _is_base_qty(c: Col) -> bool:
    if c.role not in (None, "other"):
        return c.role == "qty_base" and c.kind == "number"
    return c.kind == "number" and c.has("quydoi", "coban", "quyra", "soluongle", "slle")


def _line_money_rank(c: Col) -> int:
    """Cột thành tiền của dòng (đã trừ giảm giá dòng). 0 = không phải."""
    if c.kind != "number":
        return 0
    if c.role not in (None, "other"):  # README: "thành tiền sau giảm" thắng; cột cấp đơn lặp lại mọi dòng thì loại
        return 10 if c.role == "line_revenue" else 0
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
    if c.kind == "date" and c.role not in (None, "other"):
        return 5 if c.role == "date" else (1 if c.role == "time" else 0)
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


def _most(cols: list[Col], pred) -> Col | None:
    """Như _first nhưng nhiều cột khớp thì lấy cột nhiều giá trị nhất ('Nhóm hàng' 9 giá trị hơn 'Loại hàng' chỉ 'Hàng hóa')."""
    hits = [c for c in cols if pred(c)]
    return max(hits, key=lambda c: c.n_distinct) if hits else None


def _best(cols: list[Col], rank) -> Col | None:
    scored = [(rank(c), i, c) for i, c in enumerate(cols) if rank(c) > 0]
    return max(scored, key=lambda t: (t[0], -t[1]))[2] if scored else None


# ----------------------------- chọn bảng -------------------------------------

def _load_cols(conn: sqlite3.Connection) -> dict[str, list[Col]]:
    out: dict[str, list[Col]] = {}
    desc = readme_columns(conn)
    for t, n, o, k, nd, top in conn.execute(
        "SELECT table_name, column_name, original_name, kind, n_distinct, "
        "COALESCE(top_values, '') || ' ' || COALESCE(max_value, '') FROM _meta_columns"
    ):
        if not t.startswith(("_", "std_")):
            out.setdefault(t, []).append(Col(t, n, o, k, nd, top, desc.get((t, _key(o))) or desc.get((t, _key(n)))))
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
                own = _first(cols[line_t], _is_order_id)  # bảng dòng đã có mã đơn -> chỉ nối qua cột đó
                link = max(((_overlap(conn, line_t, c.name, t, oid.name), c) for c in cols[line_t]
                            if c.kind == "text" and (own is None or c is own)), key=lambda x: x[0], default=(0, None))
                if link[0] < 5:
                    continue
            score = (link[0] if link else 0) + 5 * bool(_first(cs, lambda c: c.has("trangthai", "status")))
            if best is None or score > best[0]:
                best = (score, t, oid, link[1] if link else None)
    return best[1:] if best else (None, None, None)


# ----------------------------- dựng bảng -------------------------------------

def _read(conn, t: str) -> pd.DataFrame:
    return pd.read_sql_query(f"SELECT * FROM {sql_quote(t)}", conn)


_CODE_LIKE = re.compile(r"^[A-Za-z]{1,6}[-_]?\d{3,}$")


def _ffill_line_ids(df: pd.DataFrame, oid: Col, code, name, qty) -> pd.Series:
    """§3.1: mã đơn trống ở dòng sau của đơn -> lấy mã dòng trên, CHỈ với dòng có nội dung hàng.
    Dòng không có hàng (dòng tổng, chú thích) không được gắn vào đơn nào."""
    ids = _id_str(df[oid.name])
    has_item = pd.Series(False, index=df.index)
    for c in (code, name, qty):
        if c is not None:
            has_item |= df[c.name].notna()
    filled = ids.ffill()
    return ids.where(ids.notna() | ~has_item, filled)


def _id_str(s: pd.Series) -> pd.Series:
    num = pd.to_numeric(s, errors="coerce")
    filled = s.notna()
    if filled.any() and num[filled].notna().mean() > 0.95 and (num.dropna() == num.dropna().round()).all():
        return num.map(lambda v: None if pd.isna(v) else str(int(v))).astype("string")
    return s.astype("string")


def _order_like(df: pd.DataFrame, cs: list[Col]) -> Col | None:
    """Không có tên/mô tả mã đơn: cột text >=90% giá trị dạng mã (ORD000001) và lặp lại (nhiều dòng / mã)."""
    n = len(df)
    for c in cs:
        if c.kind != "text" or c.role not in (None, "other") or _is_product_code(c):
            continue
        vals = df[c.name].dropna().astype(str)
        if len(vals) < 0.9 * n or not vals.map(lambda v: bool(_CODE_LIKE.match(v))).mean() >= 0.9:
            continue
        if 0.05 * n <= vals.nunique() < 0.95 * n:
            return c
    return None


def _status_col(cs: list[Col]) -> Col | None:
    by_role = _first(cs, lambda c: c.kind == "text" and c.role in ("status", "status_note"))
    if by_role:
        return by_role
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
            if _parse_factor(f):
                conv[(_key(p), _key(u))] = _parse_factor(f)
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
    for prod, big, f, _ in _text_conversions(conn, cols):
        conv.setdefault((_key(prod), _key(big)), f)
    return conv


_CONV_TEXT = re.compile(r"(\d+(?:[.,]\d+)?)\s*([^\d=,;]+?)\s*=\s*(\d+(?:[.,]\d+)?)\s*([^\d=,;]+)")


def _text_conversions(conn, cols) -> list[tuple]:
    """Cột chữ dạng "1 thùng = 4 bịch" (có thể nhiều cặp, cách nhau , ;) -> (hàng, đv lớn, hệ số, đv cơ bản)."""
    out = []
    for t, cs in cols.items():
        prod = _first(cs, _is_product_name) or _first(cs, _is_product_code)
        texts = [c for c in cs if c.kind == "text" and (c.has("quydoi", "heso", "quycach") or c.role == "other" and c.desc
                                                         and "quy doi" in strip_diacritics(c.desc).lower())]
        if not prod or not texts:
            continue
        df = _read(conn, t)
        for c in texts:
            for p, v in zip(df[prod.name], df[c.name]):
                for m in _CONV_TEXT.finditer(str(v or "")):
                    a, b = float(m.group(1).replace(",", ".")), float(m.group(3).replace(",", "."))
                    if a and p is not None:
                        out.append((p, m.group(2).strip(), b / a, m.group(4).strip()))
    return out


def _expand_abbrev(conn, cols, line_t: str, lines: pd.DataFrame) -> str:
    """Sổ ghi tên tắt (README: "có thể ghi tắt (tra ở sheet Ghi tắt)"): bảng có 1 cột khớp >=50% tên trên dòng bán và
    1 cột tên dài hơn -> thay bằng tên đầy đủ, giữ tên gốc ở product_name_raw."""
    names = lines["product_name"].dropna()
    if names.empty:
        return ""
    keys = set(names.map(_key))
    best = None
    for t, cs in cols.items():
        if t == line_t:
            continue
        texts = [c for c in cs if c.kind == "text"]
        if len(texts) < 2 or not (any("tat" in k for c in texts for k in c.keys) or "tat" in _key(t)):
            continue
        df = _read(conn, t)
        for a in texts:
            hit = df[a.name].map(_key).isin(keys).sum()
            full = _first(texts, lambda c: c is not a and (_is_product_name(c) or c.has("tenhang", "tendaydu", "ten")))
            if full is not None and hit >= 5 and hit >= 0.5 * df[a.name].notna().sum() and (best is None or hit > best[0]):
                best = (hit, t, df, a, full)
    if best is None:
        return ""
    _, t, df, a, full = best
    mapping = dict(zip(df[a.name].map(_key), df[full.name]))
    lines["product_name_raw"] = lines["product_name"]
    lines["product_name"] = lines["product_name"].map(lambda v: mapping.get(_key(v), v) if v is not None else v)
    return f"{t}: {a.name} -> {full.name}"


def _fill_from_catalog(conn, cols, line_t: str, lines: pd.DataFrame, has_name: bool, has_code: bool) -> str:
    """Dòng bán chỉ có mã (thiếu tên) hoặc chỉ có tên (thiếu mã): lấy phần thiếu từ bảng danh mục.

    Tên cần để khớp câu hỏi; mã cần cho tie-break "bằng nhau xếp theo mã" (§9). Danh mục chọn theo
    số giá trị khớp thật, không theo tên bảng.
    """
    if has_name and has_code:
        return ""
    ids = set(lines["product_id"].dropna().astype(str)) if has_code else set()
    names = set(lines["product_name"].dropna().map(_key)) if has_name else set()
    best = None
    for t, cs in cols.items():
        if t == line_t:
            continue
        nm = _first(cs, _is_product_name)
        codes = sorted((c for c in cs if c.kind == "text" and c is not nm and (_is_product_code(c) or c.has("ma", "sku", "id"))),
                       key=lambda c: not _is_product_code(c))
        if not nm or not codes:
            continue
        df = _read(conn, t)
        nkeys = df[nm.name].map(_key)
        for cc in codes:
            # Danh mục: mỗi dòng một mã (loại bảng giao dịch như sổ giao hàng, nơi mã đơn lặp lại).
            if _is_order_id(cc) or df[cc.name].nunique() < 0.95 * len(df):
                continue
            hit = int(df[cc.name].astype(str).isin(ids).sum()) if has_code else int(nkeys.isin(names).sum())
            if hit >= 5 and (best is None or hit > best[0]):
                best = (hit, t, df, nm, cc)
    if best is None:
        return ""
    _, t, df, nm, cc = best
    if has_code:  # thiếu tên: mã -> tên
        mapping = dict(zip(df[cc.name].astype(str), df[nm.name]))
        lines["product_name"] = lines["product_id"].astype(str).map(mapping).fillna(lines["product_name"])
    else:  # thiếu mã: tên -> mã
        mapping = dict(zip(df[nm.name].map(_key), df[cc.name]))
        lines["product_id"] = lines["product_name"].map(_key).map(mapping)
    return f"{t}: {cc.name} <-> {nm.name}"


_ADJUST = re.compile(r"^(voucher|dung\d*diem|phiship|phigiao|phivanchuyen|giamgia|chietkhau|khuyenmai|tichdiem)")


def _category_from_catalog(conn, cols, line_t: str, lines: pd.DataFrame):
    """Nhóm hàng không có ở bảng dòng: lấy từ bảng danh mục khớp mã (hoặc tên) nhiều nhất."""
    ids = set(lines["product_id"].dropna().astype(str))
    names = set(lines["product_name"].dropna().map(_key))
    best = None
    for t, cs in cols.items():
        if t == line_t:
            continue
        cat = _most(cs, lambda c: c.kind == "text" and c.role == "category") or \
            _most(cs, lambda c: c.kind == "text" and c.role in (None, "other") and c.has("nhomhang", "loaihang", "danhmuc", "phanloai", "nhom")
                   and not c.has("khach")) or \
            _first(cs, lambda c: c.kind == "text" and c.role in (None, "other") and bool(c.keys & {"loai"}))  # "Loại": máy mới/cũ/phụ kiện
        if not cat:
            continue
        df = _read(conn, t)
        for c in cs:
            if c.kind != "text" or c is cat:
                continue
            vals = df[c.name].astype(str)
            hit_id = int(vals.isin(ids).sum()) if ids else 0
            hit_nm = int(df[c.name].map(_key).isin(names).sum())
            hit, by_id = max((hit_id, True), (hit_nm, False))
            if hit >= 5 and (best is None or hit > best[0]):
                best = (hit, df, c, cat, by_id)
    if best is None:
        return None
    _, df, c, cat, by_id = best
    if by_id:
        return lines["product_id"].astype(str).map(dict(zip(df[c.name].astype(str), df[cat.name])))
    return lines["product_name"].map(_key).map(dict(zip(df[c.name].map(_key), df[cat.name])))


def _parse_factor(v) -> float | None:
    """Hệ số quy đổi: số, hoặc chữ "1 thùng = 4 bịch"."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*\D*?=\s*(\d+(?:[.,]\d+)?)", str(v))
    if m:
        a, b = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
        return b / a if a else None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def _fill_quy_doi(conn, cols) -> int:
    """_quy_doi chỉ có dữ liệu từ README; nạp thêm bảng quy đổi gốc (VD DanhSachDonViTinh) để agent dùng được."""
    try:
        if conn.execute("SELECT COUNT(*) FROM _quy_doi").fetchone()[0]:
            return 0
    except sqlite3.Error:
        return 0
    rows = []
    for t, cs in cols.items():
        factor = _first(cs, lambda c: c.kind == "number" and (c.has("quydoi", "heso", "tile") or c.keys & {"bang"}))
        unit = _first(cs, lambda c: c.kind == "text" and c.role == "big_unit") or \
            _first(cs, lambda c: c.kind == "text" and c.has("donvi", "dvt", "dvi", "unit", "dvlon", "loaidv")
                   and not c.has("coban", "goc", "nho"))
        base = _first(cs, lambda c: c.kind == "text" and c.has("coban", "dvgoc", "dvnho", "donvigoc"))
        prod = _first(cs, _is_product_name) or _first(cs, _is_product_code)
        if not (factor and unit and prod) or factor.has("soluong"):
            continue
        for r in _read(conn, t).to_dict("records"):
            if r.get(factor.name) is not None and r.get(prod.name) is not None:
                rows.append((r[prod.name], r.get(unit.name), float(r[factor.name]), r.get(base.name) if base else None))
    rows += _text_conversions(conn, cols)
    conn.executemany("INSERT INTO _quy_doi VALUES (?, ?, ?, ?)", rows)
    return len(rows)


def _sanity(conn) -> list[str]:
    """Tự kiểm tra khi không có gold (tập private): lỗi dữ liệu hay gặp thì ghi cảnh báo."""
    warn = []
    n, no_date, zero = conn.execute(
        "SELECT COUNT(*), SUM(order_date IS NULL OR order_date = ''), SUM(is_valid = 1 AND revenue <= 0) FROM std_orders"
    ).fetchone()
    if n and no_date / n > 0.01:
        warn.append(f"{no_date}/{n} đơn thiếu ngày")
    if n and zero / n > 0.01:
        warn.append(f"{zero}/{n} đơn hợp lệ có revenue <= 0")
    try:
        orphan, nl = conn.execute(
            "SELECT SUM(o.order_id IS NULL), COUNT(*) FROM std_lines l LEFT JOIN std_orders o USING(order_id)"
        ).fetchone()
        if nl and orphan / nl > 0.01:
            warn.append(f"{orphan}/{nl} dòng hàng không khớp đơn nào")
    except sqlite3.Error:
        pass
    return warn


_PLATE = re.compile(r"\b\d{2}[A-Z]{1,2}\d?[- ]?\d{3}\.?\d{2}\b|\b\d{2}[A-Z]{1,2}\d?[- ]\d{4,5}\b", re.I)
_HONORIFIC = re.compile(r"^(anh|chi|chu|co|em|bac|a|c|e)\b\.?\s*", re.I)


def _given_name(v) -> str:
    """'A.Đức', 'a Đức', 'Đức', 'Lương Chi Đức' -> 'duc' (tên gọi = từ cuối, bỏ danh xưng)."""
    s = _HONORIFIC.sub("", strip_diacritics(str(v or "")).strip().lower().replace(".", ". "))
    toks = re.findall(r"[a-z]+", s)
    return toks[-1] if toks else ""


def canon_models(names: list[str]) -> dict[str, str]:
    """Gộp tên ghi tắt về tên đầy đủ duy nhất (rulebook: dòng xe/dòng máy ghi tắt là cùng một dòng).

    short -> long khi long là ứng viên DUY NHẤT dài hơn thoả: bỏ khoảng trắng bằng nhau | chữ cái đầu khớp |
    mỗi từ của short là tiền tố của một từ trong long. Không duy nhất thì giữ nguyên.
    """
    def toks(v: str) -> list[str]:
        return strip_diacritics(v.replace("α", " alpha ")).lower().split()

    uniq = sorted({n for n in names if n}, key=lambda v: (-len(v), v))
    out = {}
    for short in uniq:
        st = toks(short)
        cands = []
        for long in uniq:
            lt = toks(long)
            if long == short or len("".join(lt)) <= len("".join(st)):
                if "".join(lt) == "".join(st) and len(long) > len(short):
                    cands.append(long)  # "airblade" -> "air blade", "wave α" -> "wave alpha"
                continue
            if ("".join(st) == "".join(lt) or "".join(st) == "".join(w[0] for w in lt)
                    or all(any(w.startswith(x) for w in lt) for x in st)):
                cands.append(long)
        roots = {out.get(c, c) for c in cands}
        if len(roots) == 1:
            out[short] = roots.pop()
    return out


_PRODUCT_ATTRS = {
    # thuộc tính -> (vai trò README, gợi ý tên cột khi README không mô tả)
    "category": ("category", ("nhomhang", "loaihang", "danhmuc", "phanloai", "nhom")),
    "brand": ("brand", ("thuonghieu", "nhanhieu", "hangsx")),
    "size": ("size", ("sosize", "size")),
    "active_ingredient": ("active_ingredient", ("hoatchat", "thanhphan")),
    "is_prescription": ("prescription", ("kedon", "bantheodon", "theodonbacsi", "theodonbs")),  # không "theodon": 'giá bán theo đơn vị' 
    "cost_price": ("cost", ("giavon",)),
    "base_unit": ("base_unit", ("dvcoban", "donvicoban", "dvgoc", "donvigoc", "donvicoso")),
}


def _build_products(conn, cols, line_t: str, lines: pd.DataFrame) -> pd.DataFrame:
    """std_products: 1 dòng / mặt hàng bán ra + thuộc tính kéo từ MỌI bảng khớp mã (hoặc tên) với dòng bán."""
    prods = (lines[lines["line_kind"] == "item"].groupby("product_name", dropna=True)["product_id"]
             .agg(lambda s: s.dropna().iloc[0] if s.notna().any() else None).reset_index())
    prods["_k"] = prods["product_name"].map(_key)
    ids = set(prods["product_id"].dropna().astype(str))
    for attr in _PRODUCT_ATTRS:
        prods[attr] = None
    for t, cs in cols.items():
        if t == line_t:
            continue
        found = {}
        for attr, (role, hints) in _PRODUCT_ATTRS.items():
            c = _most(cs, lambda c: c.role == role) or _most(cs, lambda c: c.role in (None, "other") and c.has(*hints)
                                                                and not c.has("khach", "kh")
                                                                and not (attr == "is_prescription" and c.has("gia", "donvi")))
            if c:
                found[attr] = c
        if not found:
            continue
        df = _read(conn, t)
        keys = [c for c in cs if c.kind == "text" and c not in found.values()]
        best = None
        for c in keys:
            vals = df[c.name].astype(str)
            hit_id, hit_nm = int(vals.isin(ids).sum()), int(df[c.name].map(_key).isin(set(prods["_k"])).sum())
            for hit, by_id in ((hit_id, True), (hit_nm, False)):
                if hit >= 5 and (best is None or hit > best[0]):
                    best = (hit, c, by_id)
        if best is None:
            continue
        _, kc, by_id = best
        src = df.drop_duplicates(kc.name)
        jk = src[kc.name].astype(str) if by_id else src[kc.name].map(_key)
        pk = prods["product_id"].astype(str) if by_id else prods["_k"]
        for attr, c in found.items():
            mapped = pk.map(dict(zip(jk, src[c.name])))
            prods[attr] = prods[attr].where(prods[attr].notna(), mapped)
    prods["is_prescription"] = prods["is_prescription"].map(
        lambda v: None if v is None or (isinstance(v, float) and v != v) else int(_key(v) in ("1", "true", "co", "x", "yes", "10")))
    return prods.drop(columns=["_k"])


def _build_repairs(conn, cols, meta: dict) -> pd.DataFrame | None:
    """Sổ dịch vụ theo lượt xe: bảng có cột biển số (≥50% ô), cột tiền, không có cột số lượng -> std_repairs."""
    best = None
    for t, cs in cols.items():
        df = _read(conn, t)
        texts = [c for c in cs if c.kind == "text"]
        for c in texts:
            vals = df[c.name].dropna().astype(str)
            ratio = vals.map(lambda v: bool(_PLATE.search(v))).mean() if len(vals) else 0
            money = _first(cs, lambda x: x.kind == "number" and (x.role in ("line_revenue", "order_level") or x.has("tien", "thanhtien", "tongtien")))
            qty = _first(cs, lambda x: _is_qty(x) or _is_base_qty(x))
            if ratio >= 0.5 and money and not qty and (best is None or len(df) > len(best[1])):
                best = (t, df, cs, c, money)
    if best is None:
        return None
    t, df, cs, plate_c, money = best
    dcol = _best(cs, _date_rank)
    jobs = _first(cs, lambda c: c.kind == "text" and c is not plate_c and (c.has("lamgi", "congviec", "noidung", "dichvu", "hangmuc")
                                                                           or (c.desc and "viec" in strip_diacritics(c.desc).lower())))
    mech = _first(cs, lambda c: c.kind == "text" and (c.role == "employee" or c.has("tho", "nhanvien", "kythuat")))
    note = _first(cs, lambda c: c.kind == "text" and c.has("ghichu", "note"))
    raw = df[plate_c.name].astype("string")
    rep = pd.DataFrame({
        "ticket_id": range(1, len(df) + 1),
        "repair_date": _split_dt(df[dcol.name])[0] if dcol else None,
        "vehicle_raw": raw,
        "plate": raw.map(lambda v: (m.group(0).upper().replace(" ", "-") if (m := _PLATE.search(str(v))) else None)),
        "model": raw.map(lambda v: re.sub(r"\s+", " ", _PLATE.sub("", str(v))).strip().lower() or None),
        "jobs": df[jobs.name] if jobs else None,
        "mechanic_raw": df[mech.name] if mech else None,
        "amount": pd.to_numeric(df[money.name], errors="coerce"),
        "note": df[note.name] if note else None,
    })
    rep["model_raw"] = rep["model"]
    cm = canon_models(rep["model"].dropna().unique().tolist())
    rep["model"] = rep["model"].map(lambda v: cm.get(v, v))
    note_k = rep["note"].map(_key) if note else pd.Series([""] * len(rep))
    rep["is_warranty"] = (note_k.str.contains("baohanh") | (rep["amount"].fillna(0) == 0)).astype(int)
    # Biệt danh thợ -> họ tên đầy đủ trong bảng nhân sự (khớp tên gọi, chỉ khi duy nhất).
    staff = None
    for st, scs in cols.items():
        if st == t:
            continue
        nm = _first(scs, lambda c: c.kind == "text" and (c.has("hoten", "ten", "name") or c.role == "employee"))
        if nm:
            names = _read(conn, st)[nm.name].dropna().astype(str).tolist()
            given = {}
            for n in names:
                given.setdefault(_given_name(n), []).append(n)
            hit = rep["mechanic_raw"].dropna().map(_given_name).isin(given).mean() if mech else 0
            if hit >= 0.8 and (staff is None or hit > staff[0]):
                staff = (hit, {g: v[0] for g, v in given.items() if len(v) == 1}, st)
    rep["mechanic"] = rep["mechanic_raw"].map(lambda v: staff[1].get(_given_name(v), v) if staff and v is not None else v) \
        if mech else None
    conn.execute("DROP TABLE IF EXISTS std_repairs")
    rep.to_sql("std_repairs", conn, index=False)
    meta.update({"repair.table": t, "repair.plate": plate_c.name, "repair.date": dcol.name if dcol else "",
                 "repair.jobs": jobs.name if jobs else "", "repair.mechanic": mech.name if mech else "",
                 "repair.amount": money.name, "repair.staff_table": staff[2] if staff else ""})
    return rep


def build_std(conn: sqlite3.Connection) -> dict:
    cols = _load_cols(conn)
    n_conv = _fill_quy_doi(conn, cols)
    line_t = _pick_line_table(cols)
    head_t, head_oid, line_oid = _pick_header(conn, cols, line_t)
    meta: dict[str, str] = {"line_table": line_t or "", "header_table": head_t or ""}

    lines = pd.DataFrame()
    if line_t:
        cs, df = cols[line_t], _read(conn, line_t)
        oid = line_oid or _first(cs, _is_order_id) or _order_like(df, cs)
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
            # §3.1 ô gộp: mã đơn chỉ ghi ở dòng đầu đơn (VD STT) -> dòng sau thuộc đơn phía trên.
            "order_id": _ffill_line_ids(df, oid, code, name, qty) if oid else pd.Series([f"R{i}" for i in range(n)], dtype="string"),
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
        meta["line.abbrev"] = _expand_abbrev(conn, cols, line_t, lines)
        meta["line.catalog_fill"] = _fill_from_catalog(conn, cols, line_t, lines, bool(name), bool(code))
        # §3.5: tên chỉ khác hoa/thường/dấu là một mặt hàng -> gom về cách viết phổ biến nhất.
        pkey = lines["product_name"].map(_key)
        canon = lines.groupby(pkey)["product_name"].agg(lambda s: s.value_counts().idxmax() if s.notna().any() else None)
        lines["product_name"] = pkey.map(canon).where(lines["product_name"].notna())
        lines["line_cost"] = lines["qty"] * pd.to_numeric(df[cost.name], errors="coerce") if cost else None
        lines["is_service_fee"] = lines["product_name"].map(lambda v: int("phiphucvu" in _key(v)))
        # Dòng không phải hàng (Voucher, Dùng n điểm, Phí ship, giảm giá đơn): không phải doanh thu (§1),
        # không phải mặt hàng. Giữ tiền ở adjust_amount cho câu hỏi voucher/phí ship. "Phí phục vụ" vẫn là doanh thu.
        adj = lines["product_name"].map(lambda v: bool(_ADJUST.match(_key(v)))) & lines["qty"].isna()
        lines["line_kind"] = "item"
        lines.loc[lines["is_service_fee"] == 1, "line_kind"] = "service_fee"
        lines.loc[adj, "line_kind"] = "adjustment"
        lines["adjust_amount"] = lines["line_revenue"].where(adj)
        lines.loc[adj, "line_revenue"] = 0
        cat = _most(cs, lambda c: c.kind == "text" and c.role == "category") or \
            _most(cs, lambda c: c.kind == "text" and c.role in (None, "other") and c.has("nhomhang", "loaihang", "danhmuc", "phanloai"))
        lines["category"] = df[cat.name] if cat else _category_from_catalog(conn, cols, line_t, lines)
        meta["line.category"] = cat.name if cat else "(danh mục)"

        st = _status_col(cs)
        lines["_status"] = df[st.name].map(norm_status) if st else None
        # §README biz trả hàng: dòng số lượng âm cùng mã đơn = đơn có trả hàng.
        lines.loc[lines["qty"] < 0, "_status"] = RETURN
        meta["line.status"] = st.name if st else ""
        dcol = _best(cs, _date_rank)
        dcols = sorted((c for c in cs if _date_rank(c)), key=_date_rank, reverse=True)
        # Ngày đơn = cột ngày tốt nhất, ô trống thì lấy cột ngày/giờ kế tiếp.
        lines["_date"] = df[[c.name for c in dcols]].bfill(axis=1).iloc[:, 0] if dcols else None
        tcol = _first(cs, lambda c: c.role == "time") or _first(cs, lambda c: c.has_time and c is not dcol)
        lines["_time"] = df[tcol.name] if tcol else (df[dcol.name] if dcol else None)

    # ---- đơn hàng ----
    if head_t:
        cs = cols[head_t]
        df = _read(conn, head_t).drop_duplicates(head_oid.name).reset_index(drop=True)
        dcol = _best(cs, _date_rank)
        tcol = _first(cs, lambda c: c.role == "time") or _first(cs, lambda c: c.has_time and c is not dcol) or dcol
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
            money = _best(cs, lambda c: 10 if c.kind == "number" and c.role == "order_goods" else
                          (9 if c.kind == "number" and c.role in (None, "other") and c.has("tienhang") else _line_money_rank(c)))
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
        rep = _build_repairs(conn, cols, meta)
        if rep is None:
            return {"error": "không tìm thấy bảng bán hàng"}
        # Tiệm dịch vụ (sửa xe): mỗi phiếu là một "đơn" để metric doanh thu/số phiếu/theo thợ dùng chung.
        orders = pd.DataFrame({
            "order_id": rep["ticket_id"].astype(str), "order_date": rep["repair_date"], "order_time": None,
            "status_raw": rep["note"], "status_norm": DONE, "is_valid": 1, "customer_id": rep["plate"],
            "customer_name": None, "pay_method": None, "channel": None, "employee_id": rep["mechanic"],
            "revenue": rep["amount"].fillna(0), "n_lines": 1})
        for t in ("std_orders", "std_lines", "_meta_std"):
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        orders.to_sql("std_orders", conn, index=False)
        conn.execute("CREATE INDEX ix_std_orders_date ON std_orders(order_date)")
        for i, w in enumerate(_sanity(conn)):
            meta[f"warning.{i}"] = w
        pd.DataFrame(sorted(meta.items()), columns=["role", "source"]).to_sql("_meta_std", conn, index=False)
        conn.commit()
        return meta

    def pick(role: str, pred):
        return _first(src_cols, lambda c: c.kind == "text" and c.role == role) or \
            _first(src_cols, lambda c: c.kind == "text" and c.role in (None, "other") and pred(c))
    cust_id = pick("customer_id", lambda c: c.has("makh", "makhach", "customerid", "idkh", "sokh"))
    cust_nm = pick("customer_name", lambda c: c is not cust_id and (
        c.has("tenkh", "khachhang", "benmua", "doitac", "customer") or bool(c.keys & {"khach"})))
    pay = pick("pay", lambda c: c.has("phuongthuc", "pttt", "hinhthuc", "payment"))
    channel = pick("channel", lambda c: c.has("kenh", "nguon", "loaiban", "channel", "source"))
    emp = pick("employee", lambda c: c.has("nhanvien", "manv", "nguoiban", "thungan", "employee", "staff"))
    for field, c in (("customer_id", cust_id), ("customer_name", cust_nm), ("pay_method", pay),
                     ("channel", channel), ("employee_id", emp)):
        orders[field] = df[c.name].values if c else None
        meta[f"order.{field}"] = c.name if c else ""

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
                  "customer_id", "customer_name", "pay_method", "channel", "employee_id", "revenue", "n_lines"]
    line_cols = ["order_id", "line_no", "product_id", "product_name", "unit", "qty", "qty_base",
                 "line_revenue", "line_cost", "is_service_fee", "line_kind", "adjust_amount", "category"]
    if "product_name_raw" in lines:
        line_cols.append("product_name_raw")
    for t in ("std_orders", "std_lines", "_meta_std"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    orders[order_cols].to_sql("std_orders", conn, index=False)
    if line_t:
        products = _build_products(conn, cols, line_t, lines)
        cat_map = dict(zip(products["product_name"], products["category"]))
        lines["category"] = lines["category"].where(lines["category"].notna(), lines["product_name"].map(cat_map))
        conn.execute("DROP TABLE IF EXISTS std_products")
        products.to_sql("std_products", conn, index=False)
        meta["products.attrs"] = ", ".join(f"{a}:{int(products[a].notna().sum())}" for a in _PRODUCT_ATTRS)
        lines[line_cols].to_sql("std_lines", conn, index=False)
        conn.execute("CREATE INDEX ix_std_lines_order ON std_lines(order_id)")
    conn.execute("CREATE INDEX ix_std_orders_date ON std_orders(order_date)")
    meta["quy_doi.loaded_from_tables"] = str(n_conv)
    for i, w in enumerate(_sanity(conn)):
        meta[f"warning.{i}"] = w
        print(f"  [std_layer] CẢNH BÁO: {w}")
    pd.DataFrame(sorted(meta.items()), columns=["role", "source"]).to_sql("_meta_std", conn, index=False)
    conn.commit()
    return meta


if __name__ == "__main__":
    import sys

    for path in sys.argv[1:]:
        with sqlite3.connect(path) as c:
            print(path, build_std(c))
