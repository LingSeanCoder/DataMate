"""Định tuyến bằng regex (không LLM) -> metrics.py -> câu trả lời. Dùng để kiểm chéo metrics trên gold train (Phase 3);
Phase 4 thay phần đoán intent/slot bằng LLM, giữ nguyên metrics.

    python run_rules.py --data ../DATASET/train --db-dir tmp_db --out sub_rules.jsonl \
        [--answers answers.jsonl] [--intent revenue] [--biz biz_106] [--wrong 40]

Không chắc thì trả abstain (0 điểm) thay vì đoán.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections import defaultdict

import metrics as M
from count_intents import classify, norm as cnorm
from entity import extract_entities
from formatter import FormatError, shape_answer
from time_resolver import resolve

ABSTAIN = {"type": "abstain"}
UNIT_RX = r"(ly|dia|noi|cuc|bo|chai|hop|goi|vien|cai|lon|bao(?! nhieu)|thung|vi|tuyp|kg|m3|phan(?! tram)|to|suat|cay|cuon|tam|thanh|binh|bich|chiec|met|xe|tan|may|lo|ong|mieng|bo|m)"


def _filters(ent: dict, qn: str) -> dict:
    F = {k: ent.get(k) for k in ("category", "brand", "channel", "payment", "employee", "customer_group")}
    F["size"] = ent.get("size") if ent.get("size") and not re.fullmatch(r"[ML]", ent["size"][0]) else None
    if ent.get("size") and re.fullmatch(r"[ML]", ent["size"][0]):  # cà phê: size = tên món kết thúc (M)/(L)
        F["size_suffix"] = ent["size"][0]
    if ent.get("product") and ent.get("category") and ent["product"]["confidence"] < 0.9:
        ent["product"] = None  # "máy mới, máy cũ ... điện thoại" là nhóm, không phải "Giá đỡ điện thoại"
    if ent.get("product"):
        F["product"] = ent["product"]["name"]
        F["category"] = F["brand"] = F["size"] = None  # đã có tên hàng cụ thể
    if ent.get("hoat_chat") and not ent.get("product"):  # tên hàng đã cụ thể hơn hoạt chất (hàng không phải thuốc không có hoạt chất)
        F["ingredient"] = ent["hoat_chat"]
    if re.search(r"(khong|hong) (can )?(ke )?don|khong ke don|tru (cac )?(thuoc )?ke don|ngoai (thuoc )?ke don", qn):
        F["prescription"] = False
    elif re.search(r"ke don", qn):
        F["prescription"] = True
    m = re.search(r"\bca (sang|chieu)\b", qn)
    if m:
        F["shift"] = m.group(1)
    if not ent.get("product"):  # có tên hàng thì đơn vị chỉ là cách đếm của hàng đó
        mu = re.search(r"\b(?:bao nhieu|may|so|tong so|tong cong) " + UNIT_RX + r"\b", qn)  # câu đã chuẩn hoá teencode
        F["unit"] = ent.get("unit") or (mu.group(1) if mu and mu.group(1) != "may" else None)
    m = re.search(r"tinh theo " + UNIT_RX + r"\b", qn)
    if m and not ent.get("product"):
        F["unit"] = m.group(1)
    if ent.get("customer"):
        F["customer_name"] = ent["customer"]
    # "không kể phụ kiện", "trừ nhóm X": nhóm nêu ra là nhóm bị loại
    if F.get("category") and re.search(r"(khong ke|khong tinh|tru|ngoai) (nhom |cac |hang )?" + re.escape(cnorm(F["category"][0])), qn):
        F["exclude_category"], F["category"] = F["category"], None
    return F


def _measure(qn: str) -> str:
    if re.search(r"(bao nhieu|nhieu|may|so|tong so|tong cong|tong) (don|bill|hoa don|phieu)\b|so don\b", qn):
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


_MEASURABLE = re.compile(r"\b(nhieu|it|cao|thap|lon|nho|chay|hot|loi|lai|dat|re|gan|som|muon|dong|lau|trung thanh|sop|doi y|"
                         r"cham chi|ua chuong|ban gioi|dat khach|can date|moi|cu)\b|nhieu|chay|tien|doanh|don")  # lỗi gõ dính chữ


def _qualitative(qn: str) -> bool:
    """'ai/nào … X nhất' mà X không đo được bằng dữ liệu (thân thiện, nhiệt tình, tốt, ngon, trung thực...) -> câu định tính (§7)."""
    if not re.search(r"\b(ai|nao|mo|gi|chi)\b", qn):
        return False
    for m in re.finditer(r"((?:\w+ ){0,4})(\w+) nhat\b", qn):
        if not _MEASURABLE.search(m.group(1) + m.group(2)):
            return True
    return False


def answer(q: dict, db: str) -> dict:
    text, as_of = q["question"], q["as_of_date"]
    qn = cnorm(text)
    intent = classify(text)[0]
    if intent != "unanswerable" and _qualitative(qn):
        return {"type": "unanswerable", "reason": "câu định tính (so sánh không đo được)"}
    if intent == "unanswerable":
        if re.search(r"\bvi\b|ly do|vi sao|tai sao", qn):  # hỏi lý do huỷ/trả: chỉ unanswerable khi shop không ghi lý do
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            has_reason = c.execute("SELECT 1 FROM _meta_columns WHERE replace(column_name, '_', '') LIKE '%nguyennhan%' "
                                   "OR replace(column_name, '_', '') LIKE '%lydo%' LIMIT 1").fetchone()
            c.close()
            if has_reason:
                return ABSTAIN  # shortcut: chưa tính theo lý do; làm khi có câu train/gold dạng này
        return {"type": "unanswerable", "reason": "câu định tính / dữ liệu không ghi"}
    pers = [p for p in resolve(text, as_of, os.environ.get("DATAMATE_CALENDAR", "calendar.csv")) if p["kind"] not in ("event_window",)]
    ent = extract_entities(text, db)
    F = _filters(ent, qn)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if F.get("size_suffix"):
            F["product"] = _size_products(conn, F.pop("size_suffix"), F)
        line = re.match(r"(.+?)\s+\d+\s*GB\b", F["product"]) if isinstance(F.get("product"), str) else None
        if line and not re.search(r"\d+ ?gb", qn):  # luật public: dòng máy = phần tên trước dung lượng -> mọi biến thể
            F["product"] = [n for (n,) in conn.execute("SELECT DISTINCT product_name FROM std_lines WHERE product_name LIKE ?",
                                                      (line.group(1) + " %GB%",))
                            if re.match(r"(.+?)\s+\d+\s*GB\b", n).group(1) == line.group(1)]
            ent["product"] = {**ent["product"], "name": F["product"][0]} if F["product"] else ent["product"]
        if re.search(r"ngay phai tra|\bhan (phai )?tra\b|\bhan thanh toan", qn) and re.search(r"(bao nhieu|may|so) don", qn):
            return _due_count(conn, qn, as_of, pers)
        return _dispatch(conn, intent, text, qn, as_of, pers, ent, F)
    except M.Unsure:
        return ABSTAIN
    except M.NoData as e:
        return {"type": "unanswerable", "reason": str(e)}
    except (sqlite3.Error, FormatError, KeyError, ValueError, TypeError, ZeroDivisionError):
        return ABSTAIN
    finally:
        conn.close()


def _main_periods(pers):
    return [p for p in pers if p.get("role") != "compare"]


def _amount(txt: str) -> float | None:
    txt = re.sub(r"(?<=\d)\s*(d|dong|vnd)(?![a-z])", " ", txt)  # "3.200đ" -> "3.200"
    m = re.search(r"(\d[\d.,]*)\s*(k|nghin|ngan|trieu|tr|ty|cu)?(?![a-z\d])", txt)
    if not m:
        return None
    raw = m.group(1).rstrip(".,")
    v = float(raw.replace(".", "").replace(",", ".")) if re.search(r"\.\d{3}", raw) or "," in raw else float(raw)
    return v * {"k": 1e3, "nghin": 1e3, "ngan": 1e3, "trieu": 1e6, "tr": 1e6, "ty": 1e9, "cu": 1e6}.get(m.group(2) or "", 1)


def _clean(F: dict) -> dict:
    return {kk: v for kk, v in F.items() if kk != "customer_name" and v not in (None, [], "")}


def _due_count(conn, qn, as_of, pers) -> dict:
    """Sổ hạn nợ: số đơn (đặt đến hết X, không huỷ) có ngày phải trả muộn hơn X (hoặc đã quá hạn nếu câu hỏi 'quá hạn')."""
    day = next((p["end"] for p in pers if p["kind"] in ("upto", "day")), None) or as_of
    row = conn.execute("SELECT table_name, column_name FROM _meta_columns WHERE replace(column_name, '_', '') LIKE '%phaitra%' "
                       "OR replace(column_name, '_', '') LIKE '%hanthanhtoan%' OR replace(column_name, '_', '') LIKE '%hantra%' LIMIT 1").fetchone()
    if not row:
        raise M.NoData("shop không ghi hạn trả")
    t, due = row
    ids = {o: d for o, d in conn.execute("SELECT order_id, order_date FROM std_orders WHERE status_norm != 'huỷ' AND order_date <= ?", (day,))}
    cols = [c for (c,) in conn.execute("SELECT column_name FROM _meta_columns WHERE table_name = ?", (t,)) if c != due]
    oc = max(cols, key=lambda c: sum(str(v) in ids for (v,) in conn.execute(f'SELECT "{c}" FROM "{t}"')))
    rows = [(str(o), str(d)[:10]) for o, d in conn.execute(f'SELECT "{oc}", "{due}" FROM "{t}"') if str(o) in ids and d]
    late = re.search(r"qua han|tre han", qn)
    return shape_answer(sum(1 for o, d in rows if (d < day if late else d > day)), "number")


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


Q_JOBS = [(r"dau lap|nhot lap|nhot hop so", "nhot lap"), (r"\bva\b( ruot| xe)?", "va ruot"), (r"thay ruot", "thay ruot"),
          (r"sua dien|lam dien", "sua dien"), (r"thay nhot|nhot may", "nhot may"), (r"bao duong", "bao duong"),
          (r"rua xe", "rua xe"), (r"kim phun", "kim phun"), (r"\bnoi\b", "lam noi"), (r"bo thang truoc|ma phanh truoc", "bo thang truoc"),
          (r"bo thang sau|ma phanh sau", "bo thang sau"), (r"loc gio", "loc gio"), (r"\bsen\b", "sen"), (r"bu ?gi", "bugi"),
          (r"bong den", "bong den"), (r"thay vo|vo xe", "vo xe"), (r"cu ?roa", "curoa"), (r"nhong|xich", "nhong xich"),
          (r"nuoc (lam )?mat", "nuoc mat"), (r"ac quy|thay binh", "ac quy")]
WEEKDAYS = {"hai": 0, "2": 0, "ba": 1, "3": 1, "tu": 2, "4": 2, "nam": 3, "5": 3, "sau": 4, "6": 4, "bay": 5, "7": 5}


def _weekday(qn: str) -> int | None:
    if re.search(r"chu nhat|\bcn\b", qn):
        return 6
    m = re.search(r"\bthu (hai|ba|tu|nam|sau|bay|[2-7])\b", qn)
    return WEEKDAYS[m.group(1)] if m else None


def _repair(conn, qn, as_of, main, span, ent, k, plural, pct) -> dict:
    mech = ent.get("employee")
    wd = _weekday(qn)
    if wd is not None and re.search(r"trung binh|binh quan", qn) and main:
        return shape_answer(M.weekday_avg(conn, *span, as_of, wd), "number")
    if re.search(r"hoa hong|hue hong", qn):
        rows = M.mechanic_table(conn, *span, as_of, by="commission")
        if mech:
            return shape_answer(next((r["value"] for r in rows if r["name"] in mech), 0), "number", money=True)
        if re.search(r"bang|tung tho|moi tho|cac tho", qn):
            return shape_answer([[r["name"], r["value"]] for r in rows], "table", money=True, columns=["tho", "hoa_hong"])
        return shape_answer(rows[0]["name"], "string")
    if re.search(r"trung binh|binh quan", qn) and re.search(r"phieu|luot", qn) and not re.search(r"(bao nhieu|may) (luot|phieu)", qn):
        return shape_answer(M.repair_avg_ticket(conn, *span, as_of, mech), "number", money=True)
    by = "days" if re.search(r"cham chi|sieng", qn) else "amount" if re.search(r"tien|doanh thu", qn) else "count"
    if not mech and re.search(r"\b(tho|ai)\b .{0,40}nhat", qn):
        rows = M.mechanic_table(conn, *span, as_of, by=by)
        return shape_answer([r["name"] for r in rows[: k or 1]], "list") if plural else shape_answer(rows[0]["name"], "string")
    if re.search(r"bao hanh", qn) and not re.search(r"khong tinh .{0,20}bao hanh", qn):
        if pct:
            return shape_answer(M.warranty_rate(conn, *span, as_of), "percent")
        n = sum(r["warranty"] for r in M._repairs(conn, *span, as_of, mech))
        return shape_answer(n, "number")
    if re.search(r"dong xe (nao|mo|gi)", qn):
        rows = M.repair_top_model(conn, *span, as_of, by="amount" if re.search(r"tien", qn) else "count")
        return shape_answer(rows[0]["name"], "string")
    if re.search(r"khach quen|toi nhieu lan|quay lai .{0,10}lan|tu 2 lan|2 lan tro len", qn):
        return shape_answer(M.repair_vehicles(conn, *span, as_of, min_paid=2), "number")
    models = [m for (m,) in conn.execute("SELECT DISTINCT model FROM std_repairs WHERE model IS NOT NULL")
              if re.search(rf"\b{re.escape(cnorm(m))}\b", qn)]
    if re.search(r"xe .{0,30}khac nhau|bao nhieu (chiec )?xe|may (chiec )?xe|chiec .{0,20}khac nhau|moi xe tinh mot lan", qn):
        return shape_answer(M.repair_vehicles(conn, *span, as_of, model=models or None), "number")
    job = next((j for pat, j in Q_JOBS if re.search(pat, qn)), None)
    if re.search(r"luot|phieu", qn) and not re.search(r"\btien\b|doanh thu", qn):
        return shape_answer(M.repair_count(conn, *span, as_of, mech, job=job), "number")
    if mech and re.search(r"\btien\b|doanh thu", qn):
        return shape_answer(M.repair_revenue(conn, *span, as_of, mech), "number", money=True)
    return None  # câu chung (doanh thu, so sánh, ngày cao nhất...) đi luồng thường trên std_orders


def _has_rows(conn, table: str) -> bool:
    try:
        return conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is not None
    except sqlite3.Error:
        return False


def _phone_shop(conn, qn, as_of, pers, main, span, pct) -> dict | None:
    """Thu máy cũ, khách phải trả, trả góp, bảo hành máy (rulebook public §Cửa hàng điện thoại)."""
    upto = next((p["end"] for p in pers if p["kind"] in ("upto", "day")), None) or as_of
    if _has_rows(conn, "std_installments") and re.search(r"\bgop\b|tra gop", qn) and not re.search(r"gop (het|ca|chung|tat)|gop mat", qn):
        st = M.installment_status(conn, upto, as_of)
        names = {M._ukey(x["customer"]) for x in st if x["customer"]}
        who = next((n for n in sorted(names, key=len, reverse=True) if f" {n} " in f" {qn} "), None)
        if who:
            mine = [x for x in st if M._ukey(x["customer"]) == who]
            if re.search(r"toi han|den han", qn) and re.search(r"may ky|bao nhieu ky", qn):
                return shape_answer(sum(x["due_terms"] for x in mine), "number")
            return shape_answer(sum(x["rest"] for x in mine), "number", money=True)
        if re.search(r"qua han|cham gop|tre han", qn):
            if re.search(r"(bao nhieu|may|mi|so) (khach|nguoi|ngui)", qn):
                return shape_answer(sum(x["overdue"] > 0 for x in st), "number")
            return shape_answer(sum(x["overdue"] for x in st), "number", money=True)
        return shape_answer(sum(x["rest"] for x in st), "number", money=True)
    if _has_rows(conn, "std_warranties") and re.search(r"bao hanh", qn) and main:
        if re.search(r"dong may (nao|mo|gi)", qn):
            cnt = defaultdict(int)
            for v in M.warranty_visits(conn, *span, as_of):
                cnt[v["model"]] += 1
            return shape_answer(sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[0][0], "string")
        if re.search(r"may ban (ra )?(trong )?thang|ban (ra )?thang .{0,30}(mang|dem|toi)", qn):
            mo = next((p for p in main if p["kind"] == "month"), None)
            if mo is None:
                return ABSTAIN
            s0, e0 = mo["start"], mo["end"]
            imeis = {v["imei"] for v in M.warranty_visits(conn, None, as_of, as_of) if v["buy"] and s0 <= v["buy"][:10] <= e0}
            return shape_answer(len(imeis), "number")
        n = len(M.warranty_visits(conn, *span, as_of))
        if pct or re.search(r"so voi so may", qn):
            return shape_answer(M.share(n, M.phones_sold(conn, *span, as_of)), "percent")
        return shape_answer(n, "number")
    m = re.search(r"\bmay ([a-z]+) chiem", qn)  # "máy Samsung chiếm % tổng số máy bán": hãng = chữ đầu tên hàng (iPhone = Apple)
    if main and m and pct:
        brand = m.group(1)
        cats = [c for (c,) in conn.execute("SELECT DISTINCT category FROM std_lines WHERE category IS NOT NULL") if "may" in M._ukey(c)]
        names = [n for (n,) in conn.execute("SELECT DISTINCT product_name FROM std_lines WHERE category IN (%s)" % ",".join("?" * len(cats)), cats)
                 if (M._ukey(n).split() or [""])[0] == brand or (brand == "apple" and M._ukey(n).startswith("iphone"))]
        if cats and names:
            return shape_answer(M.share(M.qty(conn, *span, as_of, product=names), M.phones_sold(conn, *span, as_of)), "percent")
    if main and re.search(r"khach phai tra", qn):
        avg = bool(re.search(r"trung binh|binh quan|moi don", qn))
        return shape_answer(M.customer_pays(conn, *span, as_of, avg=avg), "number", money=True)
    if main and _has_rows(conn, "std_tradeins") and re.search(r"may cu", qn) and re.search(r"\b(thu|tru)\b", qn) and not re.search(r"trung binh moi may", qn):
        if pct:
            return shape_answer(M.tradein_order_share(conn, *span, as_of), "percent")
        return shape_answer(M.tradein_total(conn, *span, as_of), "number", money=True)
    return None


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

    # Tiệm sửa xe: mọi câu đi qua sổ sửa xe.
    if main and conn.execute("SELECT 1 FROM sqlite_master WHERE name='std_repairs'").fetchone() and \
            conn.execute("SELECT COUNT(*) FROM std_repairs").fetchone()[0] > 0:
        r = _repair(conn, qn, as_of, main, span, ent, k, plural, pct)
        if r is not None:
            return r

    r = _phone_shop(conn, qn, as_of, pers, main, span, pct)
    if r is not None:
        return r

    # Voucher / dùng điểm / phí ship, khách đăng ký mới (mẹ & bé).
    if main and re.search(r"voucher|vau ?cho|dung diem|tru diem|phi ship|tien ship", qn):
        kind = "ship" if re.search(r"ship", qn) else "diem" if re.search(r"diem", qn) else "voucher"
        return shape_answer(M.adjust_total(conn, *span, as_of, kind), "number", money=True)
    if main and re.search(r"thanh vien moi|dang ky moi|dang ky thanh vien", qn):
        return shape_answer(M.new_members(conn, *span, as_of), "number")
    # Hãng nào bán nhiều nhất.
    if main and re.search(r"\b(hang|thuong hieu) nao\b.{0,40}nhat|nhat .{0,20}\b(hang|thuong hieu) nao\b", qn):
        items = M.top_brands(conn, *span, as_of, k=k or 1, by="revenue" if re.search(r"tien|doanh thu", qn) else "qty",
                             **{kk: v for kk, v in _clean(F).items() if kk not in ("unit",)})
        if items:
            names = [it["name"] for it in items]
            return shape_answer(names, "list") if plural else shape_answer(names[0], "string")

    # Giao hàng nhiều đợt.
    if main and _has_rows(conn, "std_deliveries") and re.search(r"dot (giao|hang)|cho di|da giao|da (duoc )?cho|\d+ dot tro len|xe .{0,10}(giao|cho)|luot giao", qn):
        mo = next((p for p in main if p["kind"] in ("month", "quarter", "range", "month_to_date")), main[0])
        if re.search(r"don (dat|ban)|khach dat", qn):  # "các đơn đặt tháng X, tới hôm nay đã chở ..."
            m2 = re.search(r"(\d+) dot tro len|tu (\d+) dot", qn)
            if m2:
                return shape_answer(M.delivered(conn, mo["start"], mo["end"], as_of, min_rounds=int(m2.group(1) or m2.group(2))), "number")
            if ent.get("product"):
                name = ent["product"]["name"]
                q = M.delivered(conn, mo["start"], mo["end"], as_of, product=name)
                units = re.findall(r"(?:bao nhieu|may|so) " + UNIT_RX + r"\b", qn)
                f = M.unit_factor(conn, name, units[0]) if units else 1
                return shape_answer(q / f if f else q, "number")
            return ABSTAIN
        if ent.get("product"):
            return ABSTAIN
        return shape_answer(M.delivery_rounds(conn, mo["start"], mo["end"], as_of), "number")

    # Nhập trừ bán / số lượng nhập (đơn vị cơ bản).
    if main and ent.get("product") and re.search(r"\bnhap\b", qn):
        name = ent["product"]["name"]
        units = re.findall(r"(?:bao nhieu|may|so) " + UNIT_RX + r"\b", qn)
        f = (M.unit_factor(conn, name, units[0]) or 1) if units else 1
        if re.search(r"nhap tru (so )?ban|tru (so .{0,10})?ban", qn):
            return shape_answer(M.import_minus_sales(conn, name, *span, as_of) / f, "number")
        if units and not re.search(r"tien|gia", qn):
            return shape_answer(M.purchase_qty(conn, name, *span, as_of) / f, "number")

    if re.search(r"gio cao diem|dong khach nhat .{0,15}gio|may gio .{0,20}dong|khung gio", qn):
        s0, e0 = span if main else (None, as_of)
        return shape_answer(M.peak_hour(conn, s0, e0, as_of), "number")

    # Mặt hàng nào doanh thu/số lượng cao nhất (kể cả trong một nhóm).
    if main and re.search(r"\b(mat hang|mon|san pham|hang) nao\b.{0,50}(cao|nhieu|lon)( \w+)? nhat", qn) and \
            not re.search(r"loi|lai|\bkhach\b", qn) and intent not in ("ranking_qty", "ranking_revenue"):
        intent = "ranking_revenue" if re.search(r"doanh thu|tien", qn) else "ranking_qty"

    # Xếp hạng theo luật public §3.
    if main:
        one = lambda items: (shape_answer([it["name"] for it in items], "list") if plural  # noqa: E731
                             else shape_answer(items[0]["name"], "string")) if items else ABSTAIN
        if re.search(r"khach .{0,30}trung thanh nhat|trung thanh nhat", qn):
            return one(M.customer_rank(conn, *span, as_of, "days", k or 1))
        if re.search(r"doi y nhat|hay doi y", qn):
            return one(M.customer_rank(conn, *span, as_of, "cancels", k or 1))
        if re.search(r"khach .{0,20}\bsop\b|\bsop nhat", qn):
            return one(M.customer_rank(conn, *span, as_of, "avg3", k or 1))
        if re.search(r"ua chuong nhat|duoc ua chuong", qn):
            return one(M.products_by_orders(conn, *span, as_of, k or 1))
        if re.search(r"dat khach nhat", qn):
            return shape_answer(M.best_period(conn, *span, as_of, grain="day", measure="orders"), "date")
        if re.search(r"nha cung cap chinh", qn):
            return shape_answer(M.main_supplier(conn, *span, as_of), "string")
        if re.search(r"nhan vien .{0,30}(cham chi|sieng)|(cham chi|sieng) nhat", qn):
            return one(M.employee_days(conn, *span, as_of, k or 1))

    # Công nợ (§4).
    if intent.startswith("debt") or re.search(r"\bno them|tra (bot )?no|thu (hoi |duoc )?(tien )?(cong )?no|con no|cong no|"
                                              r"chua tra (het|xong)|con thieu|tuoi no", qn):
        return _debt(conn, qn, as_of, pers, main, ent, k)

    # Lô hạn dùng (§6 nhà thuốc).
    if re.search(r"het han|han dung|han su dung|\bhsd\b|\blo hang\b|\bcac lo\b|can date", qn) and not re.search(r"ban (duoc|ra)|doanh thu", qn) \
            and not re.search(r"(het|chay|dut) hang", qn):
        if re.search(r"gan het han nhat|het han (som|gan) nhat|han (som|gan) nhat|can date", qn):
            if not ent.get("product"):
                return ABSTAIN
            day = next((p["end"] for p in pers if p["kind"] in ("upto", "day")), None) or as_of
            return shape_answer(M.nearest_expiry(conn, ent["product"]["name"], day, as_of), "date")
        rng = next((p for p in pers if p["kind"] in ("next_days", "range") and p["start"] and p["end"]), None)
        if rng is None:  # "từ nay đến hết ngày X"
            up = next((p for p in pers if p["kind"] == "upto"), None)
            rng = {"start": as_of, "end": up["end"]} if up else None
        if rng is None:
            return ABSTAIN
        lots = sorted(M.expiring(conn, rng["start"], rng["end"], as_of), key=lambda l: (l["expiry"], str(l["code"])))
        if re.search(r"tien von|gia tri|bao nhieu tien", qn):
            if any(l["cost"] is None for l in lots):
                return ABSTAIN
            return shape_answer(sum(l["rest"] * l["cost"] for l in lots), "number", money=True)
        names = list(dict.fromkeys(l["name"] for l in lots))
        if re.search(r"(bao nhieu|may|so) lo hang\b", qn):
            return shape_answer(len(lots), "number")
        if re.search(r"(bao nhieu|may|so) (mat hang|san pham|thuoc|loai)", qn):
            return shape_answer(len(names), "number")
        return shape_answer(names, "list")

    # Số mặt hàng hết hàng cuối ngày (§5: tồn = 0 lúc hết ngày).
    if re.search(r"(bao nhieu|may|so) (mat hang|san pham|mon) .{0,20}(het|chay|dut) hang|(het|chay|dut) hang (bao nhieu|may) (mat hang|san pham|mon)", qn):
        day = next((p["end"] for p in pers if p["kind"] in ("upto", "day")), None) or as_of
        return shape_answer(M.stock_out_count(conn, day, as_of), "number")

    # Tồn kho cuối ngày (§5).
    if re.search(r"\bton\b|ton kho|trong kho|con lai may|con (lai )?(bao nhieu|may)|chan.{0,25}\ble\b", qn) and \
            not re.search(r"\bno\b|lo hang|han dung|het han|\bgop\b|\bnhap\b|ton (bao )?nhieu tien", qn):
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
    m = None if re.search(r"\bkhach\b", qn) else re.search(r"\b(mat hang|san pham|mon|hang)\b.{0,40}(?:it nhat|tu|≥|>=|tren|o) ?(\d+) ?(?:don|bill)", qn)
    if m and main and re.search(r"(bao nhieu|may|so|dem)", qn):
        n = int(m.group(2))
        return shape_answer(M.products_in_n_orders(conn, *span, as_of, n, strict=bool(re.search(r"tren \d", qn))), "number")

    # Khách mua nhiều đơn/lần nhất.
    if main and re.search(r"khach (hang )?nao .{0,30}nhieu (don|lan)|khach .{0,20}nhieu (don|lan) nhat", qn):
        items = M.top_customers(conn, *span, as_of, k=k or 1, by="orders")
        if not items:
            raise M.NoData("không có khách")
        names = [it["name"] for it in items]
        return shape_answer(names, "list") if plural else shape_answer(names[0], "string")

    # Số khách mua >= N đơn (và % trên số khách đã mua).
    m = re.search(r"khach.{0,70}(?:it nhat|tu|tren|≥|>=) ?(\d+) ?(?:don|lan)", qn)
    if m and main and not re.search(r"\bno\b", qn):
        n = int(m.group(1))
        strict = bool(re.search(r"tren \d", qn))
        cnt = M.customers_with_orders(conn, *span, as_of, min_orders=n, strict=strict)
        if pct:
            return shape_answer(M.share(cnt, M.customers_with_orders(conn, *span, as_of, min_orders=1)), "percent")
        return shape_answer(cnt, "number")

    # Khách theo đường/địa chỉ: shop không ghi địa chỉ -> unanswerable (§7).
    if intent == "+customer_address" or re.search(r"khach (hang )?(o |nha o )?duong [a-z]", qn):
        has_addr = conn.execute("SELECT 1 FROM _meta_columns WHERE column_name LIKE '%dia%chi%' OR column_name LIKE '%addr%' "
                                "OR column_name LIKE '%duong%' LIMIT 1").fetchone()
        return ABSTAIN if has_addr else {"type": "unanswerable", "reason": "shop không ghi địa chỉ khách"}

    # Hỏi theo nhóm khách mà shop không ghi nhóm khách -> unanswerable (§7).
    if re.search(r"khach (hang )?(nhom|thuoc nhom) |nhom khach", qn) and not ent.get("customer_group") and \
            not conn.execute("SELECT 1 FROM std_customers WHERE customer_group IS NOT NULL AND customer_group != '' LIMIT 1").fetchone():
        return {"type": "unanswerable", "reason": "shop không ghi nhóm khách"}
    # Hỏi theo nhóm hàng mà shop không có cột nhóm hàng -> unanswerable (§2 "Nhóm hàng: theo cột nhóm hàng").
    if re.search(r"\bnhom (hang )?(?!khach)[a-z]", qn) and not ent.get("category") and \
            not conn.execute("SELECT 1 FROM std_lines WHERE category IS NOT NULL AND category != '' LIMIT 1").fetchone():
        return {"type": "unanswerable", "reason": "shop không ghi nhóm hàng"}

    # Lãi ròng = lãi gộp − chi phí.
    if main and re.search(r"(lai|loi) (rong|that)|loi nhuan rong|tru (het )?chi phi", qn):
        return shape_answer(M.profit_net(conn, *span, as_of), "number", money=True)

    # Hỏi theo thanh toán / kênh mà shop không ghi -> unanswerable (§7).
    for col, rx in (("pay_method", r"tien mat|chuyen khoan|\bcod\b|vi dien tu|xi o di"), ("channel", r"tai quay|zalo|qua dien thoai|san tmdt|thuong mai dien tu")):
        if re.search(rx, qn) and not conn.execute(f"SELECT 1 FROM std_orders WHERE {col} IS NOT NULL AND {col} != '' LIMIT 1").fetchone():
            return {"type": "unanswerable", "reason": f"shop không ghi {col}"}

    # Lãi gộp cả kỳ.
    if main and re.search(r"(loi|lai) gop|loi nhuan gop|laix gop", qn) and not re.search(r"nhat", qn):
        return shape_answer(M.profit_gross(conn, *span, as_of, **_clean(F)), "number", money=True)

    # Hàng lời nhất (lãi gộp, §5).
    if main and re.search(r"(loi|lai)( nhuan)?( gop)?.{0,15}(nhieu |lon |cao )?nhat", qn) and re.search(r"hang|mat hang|san pham|mon|\bsp\b", qn):
        items = M.top_products(conn, *span, as_of, k=k or 1, by="profit")
        names = [it["name"] for it in items]
        return shape_answer(names, "list") if plural else shape_answer(names[0], "string")

    # Đếm số ngày doanh thu/số đơn đạt ngưỡng.
    m = re.search(r"(?:dem|so|bao nhieu|may) ngay .{0,70}?(tu|≥|>=|tren|hon|dat|it nhat|duoi|<) ?([\d.,]+ ?(?:k|nghin|ngan|trieu|tr|ty|cu)?)"
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

    if intent == "comparison_percent" or (intent != "price_hike" and not re.search(r"(len|tang|doi) gia", qn) and comp and main and (pct or re.search(r"hon hay kem|cao hay thap|tang hay giam|len hay xuong", qn))):
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
        grain = "month" if re.search(r"thang (nao|mo)\b", qn) else "day"
        if grain == "day" and not re.search(r"(ngay|hom) (nao|mo)\b", qn):
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
        m = re.search(r"(?:dat|tu|tren|it nhat|≥|>=) ?([\d.,]+) ?(k|nghin|ngan|trieu|tr|cu|dong|d)?\b", qn)
        min_amount = None
        if m and re.search(r"tien hang|gia tri|tong tien|doanh thu", qn):
            min_amount = float(m.group(1).replace(".", "").replace(",", "."))
            min_amount *= {"k": 1e3, "nghin": 1e3, "ngan": 1e3, "trieu": 1e6, "tr": 1e6, "cu": 1e6}.get(m.group(2) or "", 1)
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

    if intent not in ("unanswerable", "+customer_address"):  # mọi họ còn lại: số đo chung có bộ lọc
        if not main:
            return ABSTAIN
        measure = _measure(qn)
        if re.search(r"(trung binh|binh quan) moi (may|chiec|cai)", qn):  # giá bán TB một máy = doanh thu / số máy
            Fv = _clean(F)
            n = _value(conn, "qty", *span, as_of, Fv)
            return shape_answer(_value(conn, "revenue", *span, as_of, Fv) / n if n else None, "number", money=True)
        if re.search(r"don gia|gia ban", qn):
            return ABSTAIN
        Fv = {kk: v for kk, v in F.items() if kk != "customer_name" and v not in (None, [], "")}
        # nhiều giá trị của một thuộc tính ("tại quầy ... điện thoại") -> list theo thứ tự câu
        multi = next((kk for kk, v in Fv.items() if isinstance(v, list) and len(v) > 1 and kk != "product"), None)
        together = re.search(r"\bgop (ca|het|chung|tat)|tinh ca|ca .{0,15}(lan|va|voi) .{0,15}|tat ca", qn)
        if multi and not pct and not together:
            vals = [_value(conn, measure, *span, as_of, {**Fv, multi: [v]}) for v in Fv[multi]]
            return shape_answer(vals, "list", money=measure == "revenue")
        if len(main) > 1:
            vals = [_value(conn, measure, p["start"], p["end"], as_of, Fv) for p in main]
            return shape_answer(vals, "list", money=measure == "revenue")
        val = _value(conn, measure, *span, as_of, Fv)
        if pct:
            if not Fv:
                return ABSTAIN
            whole_f = {}
            if measure == "qty" and (Fv.get("brand") or Fv.get("size")):  # % size/hãng chia tổng bịch cùng nhóm (bỉm tã)
                whole_f = {kk: v for kk, v in Fv.items() if kk in ("category", "unit")}
            if re.search(r"\b(tien|doanh thu|so|tong)( ban)? may\b(?! (moi|cu)\b)", qn):  # "% tiền bán máy": mẫu số chỉ máy mới + máy cũ
                cats = [c for (c,) in conn.execute("SELECT DISTINCT category FROM std_lines WHERE category IS NOT NULL") if "may" in M._ukey(c)]
                if cats:
                    whole_f = {"category": cats}
            whole = _value(conn, measure, *span, as_of, whole_f)
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
