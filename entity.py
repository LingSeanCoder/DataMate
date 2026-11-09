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
from collections import Counter
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


def _tokens(s) -> list[str]:
    return _norm(s).split()


@lru_cache(maxsize=64)
def _catalog(db_path: str) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        prods = conn.execute(
            "SELECT product_name, MIN(product_id) FROM std_lines WHERE product_name IS NOT NULL AND is_service_fee = 0 "
            "GROUP BY product_name").fetchall() if _has(conn, "std_lines") else []
        custs = conn.execute(
            "SELECT DISTINCT customer_name, customer_id FROM std_orders WHERE customer_name IS NOT NULL OR customer_id IS NOT NULL"
        ).fetchall() if _has(conn, "std_orders") else []
        ingredients = []
        for t, c in conn.execute("SELECT table_name, column_name FROM _meta_columns"):
            if "hoatchat" in re.sub(r"[^a-z]", "", strip_diacritics(c).lower()):
                ingredients += [r[0] for r in conn.execute(f'SELECT DISTINCT "{c}" FROM "{t}" WHERE "{c}" IS NOT NULL')]
    finally:
        conn.close()
    df = Counter(tok for name, _ in prods for tok in set(_tokens(name)))
    n = max(len(prods), 1)
    return {
        "products": [(name, pid, _tokens(name)) for name, pid in prods],
        "idf": {tok: math.log((n + 1) / (c + 0.5)) for tok, c in df.items()},
        "customers": custs,
        "ingredients": sorted(set(ingredients), key=lambda s: -len(s)),
    }


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone() is not None


def _tok_hit(tok: str, qset: set[str], qtoks: list[str]) -> bool:
    if tok in qset:
        return True
    if len(tok) < 4 or tok.isdigit():
        return False  # từ ngắn/số phải khớp đúng (10mg ≠ 20mg, m ≠ l)
    return any(len(q) >= 3 and SequenceMatcher(None, tok, q).ratio() >= 0.85 for q in qtoks)


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
        if score >= 0.6 and rare_hit >= 1.0:
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

    k = re.search(r"\btop ?(\d+)\b|\b(\d+) (?:mat hang|mon|san pham|sp|thuoc|khach|nhan vien|ngay|hang|loai)\b", qn)
    unit = re.search(r"\b(?:bao nhieu|may|so|total may|tong so) (" + "|".join(UNITS) + r")\b", qn)
    size = re.search(r"\bsize (m|l)\b", _norm_keep_paren(question))
    return {
        "product": products[0] if products else None,
        "products": products,
        "hoat_chat": ing,
        "customer": customer,
        "top_k": int(k.group(1) or k.group(2)) if k else None,
        "unit": unit.group(1) if unit else None,
        "size": size.group(1).upper() if size else None,
    }


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(extract_entities(sys.argv[1], sys.argv[2]), ensure_ascii=False, indent=1))
