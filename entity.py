"""Rút thực thể từ câu hỏi, khớp với giá trị THẬT trong DB (std_lines, std_orders, bảng thuốc). Không LLM.

    extract_entities(question, db_path) -> {
        "product": {"name", "id", "confidence"} | None,   # tên đúng như trong dữ liệu
        "products": [...top 3 ứng viên...],
        "hoat_chat": str | None,                          # khớp NGUYÊN chuỗi hoạt chất (§6 nhà thuốc)
        "customer": {"name", "id"} | None,
        "top_k": int | None, "unit": str | None, "size": "M" | "L" | None,
    }
Khớp tên hàng: điểm theo độ hiếm (IDF) của từ trong tên; size (M)/(L) là token bắt buộc.
"""
from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from functools import lru_cache

from data_loader import strip_diacritics

UNITS = ("ly", "chai", "hop", "goi", "vien", "cai", "lon", "bao", "thung", "vi", "tuyp", "kg", "m3", "dia", "noi",
         "cuc", "bo", "phan", "to", "suat", "cay", "cuon", "tam", "thanh", "binh", "met", "m2", "xe", "tan", "loc", "bich")
_SIZE_RE = re.compile(r"\((m|l|s|xl)\)\s*$", re.I)


def _norm(s) -> str:
    s = strip_diacritics(str(s or "")).lower()
    s = re.sub(r"(\d)([a-z])", r"\1 \2", s)
    s = re.sub(r"([a-z])(\d)", r"\1 \2", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


_COMMON_WORDS = {"binh", "do", "sua", "cham", "quan", "suc", "khan", "an", "tam", "nuoc", "may", "hang", "phu", "thuc", "cac", "giam",
                 "vat", "dung", "thuoc", "mat", "tim", "ca", "rau", "com", "lau", "mon", "banh", "cafe", "gia", "tien", "don", "ban",
                 "the", "hop", "lan", "ngay", "thang", "khach", "nhom", "loai", "tong", "chi", "phi", "hai", "kem", "dau", "giay"}


def _norm_d(s) -> str:
    """Như _norm nhưng giữ dấu: 'trà' ≠ 'trả', 'gỏi' ≠ 'gọi', 'lẻ' ≠ 'lệ'."""
    return re.sub(r"[\W_]+", " ", str(s or "").lower()).strip()


def _tokens(s) -> list[str]:
    return _norm(s).split()


@lru_cache(maxsize=64)
def _catalog(db_path: str) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        prods = conn.execute(
            "SELECT product_name, MIN(product_id) FROM std_lines WHERE product_name IS NOT NULL AND is_service_fee = 0 AND COALESCE(line_kind, 'item') = 'item' "
            "GROUP BY product_name").fetchall() if _has(conn, "std_lines") else []
        custs = conn.execute(
            "SELECT DISTINCT customer_name, customer_id FROM std_orders WHERE customer_name IS NOT NULL OR customer_id IS NOT NULL"
        ).fetchall() if _has(conn, "std_orders") else []
        if _has(conn, "std_customers"):  # đơn chỉ ghi mã khách: tên nằm ở bảng khách
            custs += conn.execute("SELECT customer_name, customer_id FROM std_customers WHERE customer_name IS NOT NULL").fetchall()
        ingredients = distinct_safe(conn, "SELECT DISTINCT active_ingredient FROM std_products")
        for t, c in conn.execute("SELECT table_name, column_name FROM _meta_columns"):
            if "hoatchat" in re.sub(r"[^a-z]", "", strip_diacritics(c).lower()):
                ingredients += [r[0] for r in conn.execute(f'SELECT DISTINCT "{c}" FROM "{t}" WHERE "{c}" IS NOT NULL')]
        # Giá trị thật của các thuộc tính để khớp câu hỏi (nhóm, hãng, size, nhân viên/thợ, NCC, kênh, thanh toán).
        def distinct(sql: str) -> list[str]:
            try:
                return [str(r[0]) for r in conn.execute(sql) if r[0] not in (None, "")]
            except sqlite3.Error:
                return []
        attrs = {
            "category": distinct("SELECT DISTINCT category FROM std_products") + distinct("SELECT DISTINCT category FROM std_lines"),
            "brand": distinct("SELECT DISTINCT brand FROM std_products"),
            "size": distinct("SELECT DISTINCT size FROM std_products"),
            "employee": distinct("SELECT DISTINCT employee_id FROM std_orders") + distinct("SELECT DISTINCT mechanic FROM std_repairs"),
            "supplier": distinct("SELECT DISTINCT supplier FROM std_purchases"),
            "channel": distinct("SELECT DISTINCT channel FROM std_orders"),
            "payment": distinct("SELECT DISTINCT pay_method FROM std_orders"),
            "customer_group": distinct("SELECT DISTINCT customer_group FROM std_customers"),
        }
        aliases = {a: _code_names(conn, attrs[a]) for a in ("employee", "supplier")}
    finally:
        conn.close()
    df = Counter(tok for name, _ in prods for tok in set(_tokens(name)))
    n = max(len(prods), 1)
    return {
        "products": [(name, pid, _tokens(name)) for name, pid in prods],
        "idf": {tok: math.log((n + 1) / (c + 0.5)) for tok, c in df.items()},
        "customers": custs,
        "ingredients": sorted(set(ingredients), key=lambda s: -len(s)),
        "attrs": {k: sorted(set(v), key=lambda s: -len(s)) for k, v in attrs.items()},
        "aliases": aliases,
    }


# Cách gọi trong câu hỏi -> nhóm giá trị (khớp với giá trị thật đã bỏ dấu của kênh/thanh toán).
SYNONYMS = {
    "channel": [
        ("tai quay", "tai tiem", "tai cua hang", "truc tiep", "store", "offline"),
        ("qua dien thoai", "dien thoai", "phone", "goi dien"),
        ("zalo oa", "zalo"),
        ("san thuong mai dien tu", "san tmdt", "tmdt"),
    ],
    "payment": [
        ("tien mat", "cash", "tm", "tien tuoi"),
        ("chuyen khoan", "transfer", "ck"),
        ("vi dien tu", "ewallet", "momo"),
        ("thu ho cod", "thu ho", "cod", "xi o di"),
        ("ghi no", "debt"),
    ],
}


def _code_names(conn, codes: list[str]) -> dict[str, str]:
    """Bảng danh mục (nhân viên, NCC...) có cột mã trùng mã trong std_* -> {tên: mã} từ cột tên cùng dòng."""
    codes = set(codes)
    if not codes or not all(re.fullmatch(r"[A-Za-z]+\d+", c) for c in codes):
        return {}
    out = {}
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'std%' AND name NOT LIKE '\\_%' ESCAPE '\\'"):
        try:
            if conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] > 2000:
                continue  # chỉ bảng danh mục nhỏ
            cur = conn.execute(f'SELECT * FROM "{t}"')
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        except sqlite3.Error:
            continue
        for i in range(len(cols)):
            if sum(str(r[i]) in codes for r in rows) * 2 < len(rows) or not rows:
                continue
            for j in range(len(cols)):
                vals = [r[j] for r in rows]
                if j != i and all(isinstance(v, str) and " " in v.strip() and not re.search(r"\d", v) for v in vals):
                    out.update({v.strip(): str(r[i]) for v, r in zip(vals, rows)})
    return out


def distinct_safe(conn, sql: str) -> list[str]:
    try:
        return [str(r[0]) for r in conn.execute(sql) if r[0] not in (None, "")]
    except sqlite3.Error:
        return []


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone() is not None


def _tok_hit(tok: str, qset: set[str], qtoks: list[str]) -> bool:
    if tok in qset:
        return True
    if len(tok) < 4 or tok.isdigit():
        return False  # từ ngắn/số phải khớp đúng (10mg ≠ 20mg, m ≠ l)
    return any(len(q) >= 3 and SequenceMatcher(None, tok, q).ratio() >= 0.85 for q in qtoks)


def _adjacent(toks: list[str], qtoks: list[str], qset: set[str]) -> bool:
    """Có 2 từ liền nhau của tên cũng đứng liền nhau trong câu."""
    pos = [{i for i, q in enumerate(qtoks) if _tok_hit(t, {q}, [q])} for t in toks]
    return any(i + 1 in b for a, b in zip(pos, pos[1:]) for i in a)


def match_product(question: str, db_path: str) -> list[dict]:
    cat = _catalog(db_path)
    qtoks = _tokens(question)
    qset = set(qtoks)
    q_size = re.search(r"\bsize (m|l)\b|\((m|l)\)", _norm_keep_paren(question))
    out = []
    for name, pid, toks in cat["products"]:
        if not toks:
            continue
        size = _SIZE_RE.search(name)
        if size and q_size and (q_size.group(1) or q_size.group(2)) != size.group(1).lower():
            continue  # hỏi (L) thì không lấy (M)
        idf = cat["idf"]
        total = sum(idf.get(t, 1.0) for t in toks)
        hit = [t for t in toks if _tok_hit(t, qset, qtoks)]
        if size and size.group(1).lower() not in qset:
            continue  # tên có size mà câu không nói size đó
        score = sum(idf.get(t, 1.0) for t in hit) / total if total else 0
        rare_hit = max((idf.get(t, 1.0) for t in hit), default=0)
        # từ của tên rải rác trong câu ("từ ... bao nhiêu" ≠ "Bao tử xào") thì bỏ
        if score >= 0.6 and rare_hit >= 1.0 and (len(toks) < 2 or _adjacent(toks, qtoks, qset)):
            out.append({"name": name, "id": pid, "confidence": round(score, 3), "_n": len(hit)})
    out.sort(key=lambda d: (-d["confidence"], -d["_n"], d["name"]))
    for d in out:
        d.pop("_n")
    return out[:3]


def _norm_keep_paren(s: str) -> str:
    return re.sub(r"\s+", " ", strip_diacritics(s).lower())


def extract_entities(question: str, db_path: str) -> dict:
    qn = " " + _norm(question) + " "
    cat = _catalog(db_path)
    products = match_product(question, db_path)

    ing = next((s for s in cat["ingredients"] if f" {_norm(s)} " in qn), None)

    customer = None
    for name, cid in cat["customers"]:
        if cid and re.search(rf"\b{re.escape(str(cid).lower())}\b", qn):
            customer = {"name": name, "id": cid}
            break
        toks = _tokens(name)
        if len(toks) >= 2 and name and name.strip().lower() not in ("le", "khach le") and f" {' '.join(toks)} " in qn:
            if customer is None or len(name) > len(customer["name"] or ""):
                customer = {"name": name, "id": cid}

    k = re.search(r"\btop ?(\d+)\b|(?<![\d/.-])\b(\d{1,2}) (?:mat hang|mon|san pham|sp|thuoc|khach|nhan vien|ngay|hang|loai)\b", qn)
    unit = re.search(r"\b(?:bao nhieu|may|so|total may|tong so) (" + "|".join(UNITS) + r")\b(?! tram)", qn)
    size = re.search(r"\bsize (m|l)\b", _norm_keep_paren(question))
    # câu có dấu thì khớp giữ dấu, câu gõ không dấu thì khớp bỏ dấu
    nm = _norm_d if strip_diacritics(question) != question else _norm
    q_m = " " + nm(question) + " "
    in_product = " ".join(nm(p["name"]) for p in products[:1])
    found = {}
    for attr, values in cat["attrs"].items():
        # (vị trí trong câu, giá trị thật): khớp nguyên cụm đã bỏ dấu, tên trong danh mục -> mã, từ đồng nghĩa
        hits = [(qn.find(f" {_norm(v)} "), v) for v in values if len(nm(v)) >= 2 and f" {nm(v)} " in q_m]
        if attr == "category" and not hits:  # gọi tắt bằng chữ đầu riêng của một nhóm: "bỉm" -> "bỉm tã"
            first = defaultdict(list)
            for v in values:
                if len(_tokens(v)) >= 2:
                    first[nm(v).split()[0]].append(v)
            hits = [(qn.find(f" {_norm(w)} "), vs[0]) for w, vs in first.items()
                    if len(vs) == 1 and len(_norm(w)) >= 3 and f" {w} " in q_m and _norm(w) not in _COMMON_WORDS]
        hits += [(qn.find(f" {_norm(n)} "), c) for n, c in cat["aliases"].get(attr, {}).items() if f" {nm(n)} " in q_m]
        for group in SYNONYMS.get(attr, []):
            pos = min((qn.find(f" {p} ") for p in group if f" {p} " in qn), default=-1)
            if pos >= 0:
                hits += [(pos, v) for v in values if _norm(v) in group]
        if not hits and attr == "employee":
            given = {}
            for v in values:
                toks = _tokens(v)
                if toks:
                    given.setdefault(toks[-1], []).append(v)
            hits = [(qn.find(f" {g} "), vs[0]) for g, vs in given.items() if len(vs) == 1 and len(g) >= 2 and f" {g} " in qn
                    and re.search(rf"\b(tho|nhan vien|nv|anh|chi|a|c|em|ban)\s+(\w+\s+){{0,2}}{g}\b", qn)]
        # cụm dài đã khớp thì bỏ cụm ngắn nằm trong nó (vd 'Size XL' với 'Size L')
        ordered = []
        for _, v in sorted(hits, key=lambda h: (h[0], -len(h[1]))):
            said_group = re.search(rf"\b(nhom|loai|danh muc)( hang)? {re.escape(_norm(v))}\b", qn)
            if attr == "category" and nm is _norm and len(_tokens(v)) == 1 and not said_group:
                continue  # câu không dấu: 'tra' (trà/trả) một từ chỉ là nhóm khi có 'nhóm' đứng trước
            if attr == "category" and said_group and in_product:
                products, in_product = [], ""  # "nhóm hàng hải sản" thắng "Lẩu nấm hải sản"
            if f" {nm(v)} " in f" {in_product} ":
                continue  # 'bia' trong 'Bia Heineken 330ml' là tên hàng, không phải lọc nhóm
            if v not in ordered and not any(f" {_norm(v)} " in f" {_norm(o)} " for o in ordered):
                ordered.append(v)
        if attr == "payment":  # 'ghi nợ thêm' là công nợ; lọc thanh toán phải có 'trả/thanh toán/đơn ...' đứng trước
            ordered = [v for v in ordered if any(re.search(rf"\b(tra|thanh toan|don|bang)\b( \w+){{0,3}} {re.escape(p)}\b", qn)
                                                 for g in SYNONYMS["payment"] if _norm(v) in g for p in g) or
                       re.search(rf"\b(tra|thanh toan|don|bang)\b( \w+){{0,3}} {re.escape(_norm(v))}\b", qn)]
        found[attr] = ordered or None
    return {
        **{k: v for k, v in found.items() if k != "size"},
        "product": products[0] if products else None,
        "products": products,
        "hoat_chat": ing,
        "customer": customer,
        "top_k": int(k.group(1) or k.group(2)) if k else None,
        "unit": unit.group(1) if unit else None,
        "size": found["size"] or ([size.group(1).upper()] if size else None),
    }


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(extract_entities(sys.argv[1], sys.argv[2]), ensure_ascii=False, indent=1))
