"""Định tuyến bằng regex (không LLM) -> metrics.py -> câu trả lời. Dùng để kiểm chéo metrics trên gold train (Phase 3);
Phase 4 thay phần đoán intent/slot bằng LLM, giữ nguyên metrics.

    python run_rules.py --data ../DATASET/train --db-dir tmp_db --out sub_rules.jsonl \
        [--answers answers.jsonl] [--intent revenue] [--biz biz_106] [--wrong 40]

Không chắc thì trả abstain (0 điểm) thay vì đoán.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict

import metrics as M
from count_intents import classify, norm as cnorm
from entity import extract_entities
from formatter import FormatError, shape_answer
from time_resolver import resolve

ABSTAIN = {"type": "abstain"}
UNIT_RX = r"(ly|dia|noi|cuc|bo|chai|hop|goi|vien|cai|lon|bao|thung|vi|tuyp|kg|m3|phan(?! tram)|to|suat|cay|cuon|tam|thanh|binh|bich|chiec|met|xe|tan)"


def _filters(ent: dict, qn: str) -> dict:
    F = {k: ent.get(k) for k in ("category", "brand", "channel", "payment", "employee", "customer_group")}
    F["size"] = ent.get("size") if ent.get("size") and not re.fullmatch(r"[ML]", ent["size"][0]) else None
    if ent.get("size") and re.fullmatch(r"[ML]", ent["size"][0]):  # cà phê: size = tên món kết thúc (M)/(L)
        F["size_suffix"] = ent["size"][0]
    if ent.get("product"):
        F["product"] = ent["product"]["name"]
        F["category"] = F["brand"] = F["size"] = None  # đã có tên hàng cụ thể
    if ent.get("hoat_chat"):
        F["ingredient"] = ent["hoat_chat"]
    if re.search(r"khong (can )?(ke )?don|khong ke don|tru (cac )?(thuoc )?ke don|ngoai (thuoc )?ke don", qn):
        F["prescription"] = False
    elif re.search(r"ke don", qn):
        F["prescription"] = True
    m = re.search(r"\bca (sang|chieu)\b", qn)
    if m:
        F["shift"] = m.group(1)
    if ent.get("unit") and not ent.get("product"):  # có tên hàng thì đơn vị chỉ là cách đếm của hàng đó
        F["unit"] = ent["unit"]
    m = re.search(r"tinh theo " + UNIT_RX + r"\b", qn)
    if m and not ent.get("product"):
        F["unit"] = m.group(1)
    if ent.get("customer"):
        F["customer_name"] = ent["customer"]
    return F


def _measure(qn: str) -> str:
    if re.search(r"(bao nhieu|may|so|tong so|tong cong|tong) (don|bill|hoa don|phieu)\b|so don\b", qn):
        return "orders"
    if re.search(r"(bao nhieu|may|so|tong so|tong cong|so luong) " + UNIT_RX + r"\b|so luong|ban (duoc|ra) .{0,40}\b" + UNIT_RX + r"\b(?! tien)", qn) \
            and not re.search(r"tien|doanh thu|doanh so", qn):
        return "qty"
    return "revenue"


def _value(conn, measure, s, e, as_of, F):
    F = {k: v for k, v in F.items() if v not in (None, [], "") and k not in ("customer_name",)}
    if F.pop("size_suffix", None):
        F.setdefault("product", None)
    if measure == "orders":
        return M.count_orders(conn, s, e, as_of, **F)
    if measure == "qty":
        return M.qty(conn, s, e, as_of, **F)
    return M.revenue(conn, s, e, as_of, **F)


def _size_products(conn, suffix: str, F: dict) -> list[str]:
    rows = conn.execute("SELECT DISTINCT product_name FROM std_lines").fetchall()
    return [r[0] for r in rows if r[0] and re.search(rf"\({suffix}\)\s*$", r[0], re.I)]


def answer(q: dict, db: str) -> dict:
    text, as_of = q["question"], q["as_of_date"]
    qn = cnorm(text)
    intent = classify(text)[0]
    if intent == "unanswerable":
        return {"type": "unanswerable", "reason": "câu định tính / dữ liệu không ghi"}
    pers = [p for p in resolve(text, as_of) if p["kind"] not in ("event_window",)]
    ent = extract_entities(text, db)
    F = _filters(ent, qn)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if F.get("size_suffix"):
            F["product"] = _size_products(conn, F.pop("size_suffix"), F)
        return _dispatch(conn, intent, text, qn, as_of, pers, ent, F)
    except M.NoData as e:
        return {"type": "unanswerable", "reason": str(e)}
    except (sqlite3.Error, FormatError, KeyError, ValueError, TypeError, ZeroDivisionError):
        return ABSTAIN
    finally:
        conn.close()


def _main_periods(pers):
    return [p for p in pers if p.get("role") != "compare"]


def _amount(txt: str) -> float | None:
    m = re.search(r"(\d[\d.,]*)\s*(k|nghin|ngan|trieu|tr|ty)?\b", txt)
    if not m:
        return None
    raw = m.group(1).rstrip(".,")
    v = float(raw.replace(".", "").replace(",", ".")) if re.search(r"\.\d{3}", raw) or "," in raw else float(raw)
    return v * {"k": 1e3, "nghin": 1e3, "ngan": 1e3, "trieu": 1e6, "tr": 1e6, "ty": 1e9}.get(m.group(2) or "", 1)


def _clean(F: dict) -> dict:
    return {kk: v for kk, v in F.items() if kk != "customer_name" and v not in (None, [], "")}


def _customer_key(conn, cust: dict | None) -> str | None:
    if not cust:
        return None
    r = conn.execute("SELECT customer_key FROM std_orders WHERE (customer_id = ? OR customer_name = ?) AND customer_key IS NOT NULL "
                     "LIMIT 1", (cust.get("id"), cust.get("name"))).fetchone()
    return r[0] if r else None


def _debt(conn, qn, as_of, pers, main, ent, k) -> dict:
    upto = next((p["end"] for p in pers if p["kind"] in ("upto", "day")), None) or as_of
    if re.search(r"\bno them|chiu them|tra (bot )?no|thu hoi|thu duoc .{0,25}\bno\b|khach tra .{0,12}\bno\b|tra no", qn) and main and \
            main[0]["kind"] not in ("upto",):
        new, repaid = M.debt_flow(conn, main[0]["start"], main[0]["end"], as_of, _customer_key(conn, ent.get("customer")))
        if re.search(r"\bno them|chiu them", qn) and re.search(r"\btra\b", qn):
            return shape_answer([new, repaid], "list", money=True)
        return shape_answer(new if re.search(r"\bno them|chiu them", qn) else repaid, "number", money=True)
    if re.search(r"0\s*.?\s*7", qn) and re.search(r"8\s*.?\s*15", qn):
        return shape_answer(M.debt_buckets(conn, upto, as_of), "list", money=True)
    m = re.search(r"(?:qua|hon|tren) (\d+) ngay", qn)
    if m:
        return shape_answer(M.debt_total(conn, upto, as_of, min_age=int(m.group(1))), "number", money=True)
    if re.search(r"(bao nhieu|may|so) khach", qn):
        return shape_answer(len(M.debt_by_customer(conn, upto, as_of)), "number")
    if k or re.search(r"no nhieu nhat|cong no lon nhat", qn):
        rows = [[it["name"], it["value"]] for it in M.debt_by_customer(conn, upto, as_of)[: k or 1]]
        return shape_answer(rows, "table", money=True, columns=["khach_hang", "so_tien_no"])
    return shape_answer(M.debt_total(conn, upto, as_of), "number", money=True)


def _dispatch(conn, intent, text, qn, as_of, pers, ent, F) -> dict:
    main = _main_periods(pers)
    comp = [p for p in pers if p.get("role") == "compare"]
    span = (main[0]["start"], main[-1]["end"]) if main else (None, None)
    k = ent.get("top_k")
    if k is None:
        mk = re.search(r"(?<![\d/.-])\b(\d{1,2}) (?:mat hang|mon|san pham|hang|ban chay|khach)\b", qn)
        k = int(mk.group(1)) if mk else None
    plural = bool(k and k > 1) or bool(re.search(r"nhung (mat hang|mon|san pham|khach)|cac (mat hang|mon)|liet ke", qn))
    if ent.get("product"):  # "%" trong tên thuốc ("kem 5%") không phải hỏi phần trăm
        qn = qn.replace(cnorm(ent["product"]["name"]), " ")
    pct = bool(re.search(r"%|phan tram|ti trong|ti le|chiem", qn))

    # Công nợ (§4).
    if intent.startswith("debt") or re.search(r"\bno them|tra (bot )?no|thu (hoi |duoc )?(tien )?(cong )?no|con no|cong no|"
                                              r"chua tra (het|xong)|con thieu|tuoi no", qn):
        return _debt(conn, qn, as_of, pers, main, ent, k)

    # Tồn kho cuối ngày (§5).
    if re.search(r"\bton\b|ton kho|trong kho|con lai may|con (lai )?bao nhieu", qn) and not re.search(r"\bno\b|lo |han dung|het han", qn):
        day = next((p["end"] for p in pers if p["kind"] in ("upto", "day")), None) or as_of
        if re.search(r"gia von|tien hang|gia tri", qn) and not ent.get("product"):
            return shape_answer(M.stock_value(conn, day, as_of), "number", money=True)
        if not ent.get("product"):
            return ABSTAIN
        name = ent["product"]["name"]
        q = M.stock_at(conn, name, day, as_of)
        units = re.findall(r"(?:bao nhieu|may|so) " + UNIT_RX + r"\b", qn)
        if re.search(r"\ble\b", qn) and re.search(r"chan|nguyen", qn) and units:
            f = M.unit_factor(conn, name, units[0])
            if not f:
                return ABSTAIN
            return shape_answer([int(q // f), q - f * (q // f)], "list")
        if units:
            f = M.unit_factor(conn, name, units[0])
            if not f:
                return ABSTAIN
            return shape_answer(q / f if f != 1 else q, "number")
        return shape_answer(q, "number")

    # Đơn giá bán ngày D.
    if re.search(r"don gia|gia ban|ban .{0,60}gia bao nhieu", qn) and ent.get("product") and main and main[0]["kind"] == "day":
        return shape_answer(M.unit_price(conn, ent["product"]["name"], main[0]["start"]), "number", money=True)

    # Số mặt hàng có mặt trong >= N đơn khác nhau.
    m = re.search(r"(?<!khach )\b(mat hang|san pham|mon|hang)\b.{0,40}(?:it nhat|tu|≥|>=|tren|o) ?(\d+) ?(?:don|bill)", qn)
    if m and main and re.search(r"(bao nhieu|may|so|dem)", qn):
        n = int(m.group(2))
        return shape_answer(M.products_in_n_orders(conn, *span, as_of, n, strict=bool(re.search(r"tren \d", qn))), "number")

    # Số khách mua >= N đơn (và % trên số khách đã mua).
    m = re.search(r"khach.{0,40}(?:it nhat|tu|tren|≥|>=) ?(\d+) ?(?:don|lan)", qn)
    if m and main and not re.search(r"\bno\b", qn):
        n = int(m.group(1))
        strict = bool(re.search(r"tren \d", qn))
        cnt = M.customers_with_orders(conn, *span, as_of, min_orders=n, strict=strict)
        if pct:
            return shape_answer(M.share(cnt, M.customers_with_orders(conn, *span, as_of, min_orders=1)), "percent")
        return shape_answer(cnt, "number")

    # Lãi gộp cả kỳ.
    if main and re.search(r"(loi|lai) gop|loi nhuan gop", qn) and not re.search(r"nhat", qn):
        return shape_answer(M.profit_gross(conn, *span, as_of, **_clean(F)), "number", money=True)

    # Hàng lời nhất (lãi gộp, §5).
    if main and re.search(r"(loi|lai) nhat", qn) and re.search(r"hang|mat hang|san pham|mon", qn):
        items = M.top_products(conn, *span, as_of, k=k or 1, by="profit")
        names = [it["name"] for it in items]
        return shape_answer(names, "list") if plural else shape_answer(names[0], "string")

    # Đếm số ngày doanh thu/số đơn đạt ngưỡng.
    m = re.search(r"(?:dem|so|bao nhieu|may) ngay .{0,70}?(tu|≥|>=|tren|hon|dat|it nhat|duoi|<) ?([\d.,]+ ?(?:k|nghin|ngan|trieu|tr|ty)?)"
                  r"( ?(?:dong|d)?)( don)?(.{0,12}tro len)?", qn)
    if m and main and _amount(m.group(2)) is not None:
        op = {"tu": ">=", "≥": ">=", ">=": ">=", "dat": ">=", "it nhat": ">=", "tren": ">", "hon": ">", "duoi": "<", "<": "<"}[m.group(1)]
        measure = "orders" if m.group(4) else "revenue"
        return shape_answer(M.day_count(conn, *span, as_of, _amount(m.group(2)), op=op, measure=measure, **_clean(F)), "number")

    # Câu đúng/sai về xu hướng nhiều tháng: "tháng sau luôn cao hơn tháng trước", "có phải T10 cao hơn T9 và T11 cao hơn T10".
    if len(main) >= 2 and re.search(r"(co phai|luon|deu|co|khong|hong|ha)\b", qn) and re.search(r"cao hon|thap hon|tang|giam|hon", qn) \
            and not pct:
        vals = [_value(conn, _measure(qn), p["start"], p["end"], as_of, _clean(F)) for p in main]
        down = bool(re.search(r"thap hon|giam|kem", qn)) and not re.search(r"cao hon|tang", qn)
        ok = all((b < a) if down else (b > a) for a, b in zip(vals, vals[1:]))
        return shape_answer(ok, "boolean")

    # Ca chiều so với ca sáng (mốc là ca sáng, §2).
    if re.search(r"ca chieu", qn) and re.search(r"ca sang", qn) and main:
        measure = "orders" if re.search(r"so don|\bdon\b|bill", qn) else "revenue"
        Fv = {kk: v for kk, v in _clean(F).items() if kk != "shift"}
        a = _value(conn, measure, *span, as_of, {**Fv, "shift": "chieu"})
        b = _value(conn, measure, *span, as_of, {**Fv, "shift": "sang"})
        return shape_answer(M.pct_change(a, b), "percent") if pct else shape_answer(a - b, "number", money=measure == "revenue")

    # Nhân viên bán giỏi nhất (theo doanh thu, §3).
    if main and re.search(r"(nhan vien|nguoi ban|ai ban) .{0,40}(cao nhat|nhieu nhat|gioi nhat|nhat)", qn) and \
            not re.search(r"khach", qn):
        items = M.top_employees(conn, *span, as_of, k=k or 1)
        names = [it["name"] for it in items]
        return shape_answer(names, "list") if plural else shape_answer(names[0], "string")

    if intent == "+cancel_rate":
        if not main:
            return ABSTAIN
        s, e = span
        if pct:
            return shape_answer(M.cancel_rate(conn, s, e, as_of), "percent")
        return shape_answer(M.cancel_count(conn, s, e, as_of), "number")

    if intent == "comparison_percent":
        measure = _measure(qn)
        if len(main) >= 1 and comp:
            cur, base = main[0], comp[0]
        elif len(main) >= 2:
            cur, base = main[0], main[1]
            if re.match(r"^[^\d]{0,25}\bso voi\b", qn):  # "So với tháng 4/2025, tháng 4/2026 ...": kỳ so đứng trước
                cur, base = base, cur
        else:
            return ABSTAIN
        Fv = {kk: v for kk, v in F.items() if kk != "customer_name"}
        a = _value(conn, measure, cur["start"], cur["end"], as_of, Fv)
        b = _value(conn, measure, base["start"], base["end"], as_of, Fv)
        if pct or re.search(r"bao nhieu %|may %", qn):
            return shape_answer(M.pct_change(a, b), "percent")
        return shape_answer(a - b, "number", money=measure == "revenue")

    if intent == "avg_per_order":
        if not main:
            return ABSTAIN
        Fv = {kk: v for kk, v in F.items() if kk not in ("customer_name",) and v not in (None, [], "")}
        return shape_answer(M.avg_per_order(conn, *span, as_of, **Fv), "number", money=True)

    if intent == "daily_max":
        if not main:
            return ABSTAIN
        grain = "month" if re.search(r"thang nao", qn) else "day"
        if grain == "day" and not re.search(r"ngay nao|hom nao", qn):
            return ABSTAIN
        measure = "orders" if _measure(qn) == "orders" else "revenue"
        lowest = bool(re.search(r"(thap|it) nhat", qn))
        Fv = {kk: v for kk, v in F.items() if kk != "customer_name" and v not in (None, [], "")}
        d = M.best_period(conn, *span, as_of, grain=grain, measure=measure, lowest=lowest, **Fv)
        return shape_answer(d, "date", month=grain == "month")

    if intent == "other" and re.search(r"ban chay nhat", qn):
        intent = "ranking_qty"
    if intent in ("ranking_qty", "ranking_revenue") and not (ent.get("product") and re.search(r"bao nhieu|may", qn)):
        if not main:
            return ABSTAIN
        by = "revenue" if re.search(r"nhieu tien|doanh thu|thu ve|doanh so", qn) else \
            "profit" if re.search(r"\blai\b|\bloi\b", qn) else "qty"
        lowest = bool(re.search(r"(it|cham|thap) nhat", qn))
        Fv = {kk: v for kk, v in F.items() if kk not in ("customer_name", "product") and v not in (None, [], "")}
        items = M.top_products(conn, *span, as_of, k=k or 1, by=by, lowest=lowest, **Fv)
        if not items:
            raise M.NoData("không có bán")
        names = [it["name"] for it in items]
        return shape_answer(names, "list") if plural else shape_answer(names[0], "string")

    if intent == "customer_top" and not re.search(r"\be han\b|khong ai mua", qn):
        if not main:
            return ABSTAIN
        by = "orders" if re.search(r"so don|nhieu (lan|don)|nhieu nhat .*don", qn) else "revenue"
        items = M.top_customers(conn, *span, as_of, k=k or 1, by=by)
        if not items:
            raise M.NoData("không có khách")
        names = [it["name"] for it in items]
        return shape_answer(names, "list") if plural else shape_answer(names[0], "string")

    if intent == "count_orders":
        if not main:
            return ABSTAIN
        m = re.search(r"(?:dat|tu|tren|it nhat|≥|>=) ?([\d.,]+) ?(k|nghin|ngan|trieu|tr|dong|d)?\b", qn)
        min_amount = None
        if m and re.search(r"tien hang|gia tri|tong tien|doanh thu", qn):
            min_amount = float(m.group(1).replace(".", "").replace(",", "."))
            min_amount *= {"k": 1e3, "nghin": 1e3, "ngan": 1e3, "trieu": 1e6, "tr": 1e6}.get(m.group(2) or "", 1)
        Fv = {kk: v for kk, v in F.items() if kk != "customer_name" and v not in (None, [], "")}
        return shape_answer(M.count_orders(conn, *span, as_of, min_amount=min_amount, **Fv), "number")

    if intent == "+unsold_items" or re.search(r"\be han\b|khong ai mua", qn):
        if not main:
            return ABSTAIN
        items = M.unsold_items(conn, *span, as_of, **{kk: v for kk, v in _clean(F).items() if kk in ("category",)})
        if re.search(r"(bao nhieu|may|so) (mat hang|mon|san pham|thuoc)", qn):
            return shape_answer(len(items), "number")
        return shape_answer([it["name"] for it in items], "list")

    if intent == "price_hike":
        if not ent.get("product"):
            return ABSTAIN
        m = re.search(r"\blen (?:gia )?([\d.,]+ ?(?:k|nghin|ngan|d|dong)?)", qn)
        new_price = _amount(m.group(1)) if m else None
        rng = [p for p in pers if p["kind"] == "range"]
        n_m = re.search(r"(\d+) ngay", qn)
        if not n_m:
            return ABSTAIN
        n = int(n_m.group(1))
        d = rng[0]["start"] if rng else None
        return shape_answer(M.price_hike_change(conn, ent["product"]["name"], n, as_of, hike_date=d, new_price=new_price), "percent")

    if intent in ("purchase_total", "purchase_avg_price", "purchase_by_supplier") and main:
        if intent == "purchase_avg_price" or re.search(r"(binh quan|trung binh)", qn):
            if not ent.get("product"):
                return ABSTAIN
            return shape_answer(M.purchase_avg_price(conn, ent["product"]["name"], *span, as_of), "number", money=True)
        if intent == "purchase_by_supplier" and re.search(r"nao|ai", qn):
            items = M.top_suppliers(conn, *span, as_of, k=k or 1)
            names = [it["name"] for it in items]
            return shape_answer(names, "list") if plural else shape_answer(names[0], "string")
        return shape_answer(M.purchase_total(conn, *span, as_of, supplier=F.get("supplier") or ent.get("supplier"),
                                             product=F.get("product")), "number", money=True)

    if intent in ("revenue", "qty_of_product", "revenue_by_category", "brand_size_pct", "prescription_drug", "revenue_by_channel", "revenue_by_payment",
                  "revenue_by_employee", "revenue_by_shift", "ranking_qty", "ranking_revenue"):
        if not main:
            return ABSTAIN
        measure = _measure(qn)
        if re.search(r"don gia|gia ban", qn):
            return ABSTAIN
        Fv = {kk: v for kk, v in F.items() if kk != "customer_name" and v not in (None, [], "")}
        # nhiều giá trị của một thuộc tính ("tại quầy ... điện thoại") -> list theo thứ tự câu
        multi = next((kk for kk, v in Fv.items() if isinstance(v, list) and len(v) > 1 and kk != "product"), None)
        if multi and not pct:
            vals = [_value(conn, measure, *span, as_of, {**Fv, multi: [v]}) for v in Fv[multi]]
            return shape_answer(vals, "list", money=measure == "revenue")
        if len(main) > 1:
            vals = [_value(conn, measure, p["start"], p["end"], as_of, Fv) for p in main]
            return shape_answer(vals, "list", money=measure == "revenue")
        val = _value(conn, measure, *span, as_of, Fv)
        if pct:
            if not Fv:
                return ABSTAIN
            whole = _value(conn, measure, *span, as_of, {})
            return shape_answer(M.share(val, whole), "percent")
        return shape_answer(val, "number", money=measure == "revenue")

    return ABSTAIN


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="../DATASET/train")
    p.add_argument("--db-dir", default="tmp_db")
    p.add_argument("--out", default="sub_rules.jsonl")
    p.add_argument("--answers")
    p.add_argument("--intent")
    p.add_argument("--biz")
    p.add_argument("--wrong", type=int, default=30)
    a = p.parse_args()

    qs = [json.loads(l) for l in open(f"{a.data}/questions.jsonl", encoding="utf-8") if l.strip()]
    qs = [q for q in qs if (not a.biz or q["business_id"] == a.biz) and (not a.intent or classify(q["question"])[0] == a.intent)]
    gold = {}
    if a.answers:
        gold = {json.loads(l)["id"]: json.loads(l) for l in open(a.answers, encoding="utf-8") if l.strip()}
    from eval import score_one
    stat = defaultdict(lambda: [0, 0, 0])
    wrong = []
    with open(a.out, "w", encoding="utf-8") as f:
        for q in qs:
            ans = answer(q, f"{a.db_dir}/{q['business_id']}.db")
            f.write(json.dumps({"id": q["id"], **ans}, ensure_ascii=False) + "\n")
            if q["id"] in gold:
                s = score_one(ans, gold[q["id"]])
                it = classify(q["question"])[0]
                stat[it][{1: 0, -1: 1, 0: 2}[s]] += 1
                if s == -1:
                    g = gold[q["id"]]
                    wrong.append(f"XX {q['id'][2:8]} {q['business_id']} [{it}] PRED {ans.get('type')}="
                                 f"{json.dumps(ans.get('answer'), ensure_ascii=False)[:60]} | GOLD {g['type']}="
                                 f"{json.dumps(g.get('answer'), ensure_ascii=False)[:60]} | {q['question'][:100]}")
    if gold:
        print(f"{'intent':<22}{'đúng':>6}{'sai':>6}{'trống':>7}{'acc':>8}{'điểm':>7}")
        for it in sorted(stat, key=lambda k: -sum(stat[k])):
            ok, bad, ab = stat[it]
            print(f"{it:<22}{ok:>6}{bad:>6}{ab:>7}{100 * ok / sum(stat[it]):>7.1f}%{ok - bad:>7}")
        ok, bad, ab = (sum(v[i] for v in stat.values()) for i in range(3))
        print("\n".join(wrong[: a.wrong]))
        print(f"\n=== {ok}/{len(qs)} đúng, sai {bad}, trống {ab}, ĐIỂM = {ok - bad} ===")


if __name__ == "__main__":
    main()
