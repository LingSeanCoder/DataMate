"""Giải cách nói thời gian theo rulebook §2 (datamate.md §4.2). Không LLM, deterministic.

    resolve(question, as_of) -> list[dict]   # theo thứ tự xuất hiện trong câu
    mỗi dict: {"start": "YYYY-MM-DD" | None, "end": "YYYY-MM-DD" | None, "kind": str, "label": str,
               "confidence": float, ...}
    start=None  : "đến hết ngày X" — luỹ kế từ đầu dữ liệu (§2).
    kind="event_window": "N ngày kể từ ngày tăng" khi câu không ghi ngày; metric tự tìm ngày D, dùng "n".

Không cài "đầu/giữa/cuối tháng = 1–10/11–20/21–hết": rulebook không định nghĩa, train chỉ có
"từ đầu tháng này đến hết hôm nay" (đã cài).
"""
from __future__ import annotations

import calendar
import csv
import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from count_intents import norm as _base_norm

_EXTRA = [
    (r"\b(bua nay|hnay|hum nay)\b", "hom nay"), (r"\b(hqua|bua qua|hum qua)\b", "hom qua"),
    (r"\b(trc|trk)\b", "truoc"), (r"\bth\b(?= (truoc|nay|\d))", "thang"), (r"\bvs\b", "voi"),
    (r"\b(rui|roi)\b", "roi"), (r"\bnawm\b", "nam"), (r"\bthangs\b", "thang"), (r"\bcungf\b", "cung"),
]


_MONTH_WORDS = {"muoi mot": 11, "muoi hai": 12, "chap": 12, "gieng": 1, "mot": 1, "hai": 2, "ba": 3, "bay": 7,
                "tam": 8, "chin": 9, "muoi": 10}


def norm(text: str) -> str:
    s = _base_norm(text)
    for p, r in _EXTRA:
        s = re.sub(p, r, s)
    # "tháng ba" -> "thang 3"
    return re.sub(r"\bthang (" + "|".join(_MONTH_WORDS) + r")\b(?! \d)", lambda m: f"thang {_MONTH_WORDS[m.group(1)]}", s)


# ----------------------------- lịch -----------------------------------------

def _d(s: str) -> date:
    return date.fromisoformat(s)


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _prev_month(y: int, m: int) -> tuple[int, int]:
    return (y - 1, 12) if m == 1 else (y, m - 1)


def _year_for(month: int, as_of: date) -> int:
    """§2: tháng/quý không ghi năm = năm nay nếu không sau hôm nay, ngược lại năm trước."""
    return as_of.year if month <= as_of.month else as_of.year - 1


@lru_cache(maxsize=4)
def _tet_table(path: str) -> dict[int, tuple[date, date]]:
    """Năm âm Y -> (23 tháng Chạp năm âm Y-1, mùng 10 tháng Giêng năm âm Y), dương lịch."""
    starts, ends = {}, {}
    p = Path(path)
    if not p.is_file():
        return {}
    with p.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if str(r.get("lunar_leap", "")).lower() == "true":
                continue
            ld, lm, ly = int(r["lunar_day"]), int(r["lunar_month"]), int(r["lunar_year"])
            if lm == 12 and ld == 23:
                starts[ly + 1] = _d(r["date"])
            elif lm == 1 and ld == 10:
                ends[ly] = _d(r["date"])
    return {y: (starts[y], ends[y]) for y in starts if y in ends}


def _period(start: date | None, end: date | None, kind: str, label: str, conf: float = 1.0, **extra) -> dict:
    return {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None,
            "kind": kind, "label": label, "confidence": conf, **extra}


_DATE = r"(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{4}))?"


def _mk_date(dd: str, mm: str, yy: str | None, as_of: date) -> date | None:
    try:
        d, m = int(dd), int(mm)
        y = int(yy) if yy else _year_for(m, as_of)
        return date(y, m, d)
    except ValueError:
        return None


# ----------------------------- resolve --------------------------------------

def resolve(question: str, as_of: str, calendar_csv: str = "calendar.csv") -> list[dict]:
    today = _d(as_of[:10])
    q = norm(question)
    used = [False] * len(q)
    found: list[tuple[int, dict]] = []

    def take(m: re.Match) -> bool:
        if any(used[m.start():m.end()]):
            return False
        for i in range(m.start(), m.end()):
            used[i] = True
        return True

    def add(pos: int, per: dict) -> None:
        found.append((pos, per))

    # 1) Tết (§2): 23 tháng Chạp năm âm Y-1 -> hết mùng 10 tháng Giêng năm âm Y; "trước Tết" = cùng số ngày ngay trước.
    tet = _tet_table(str(Path(calendar_csv)))
    for m in re.finditer(r"(truoc tet|(?:dip |mua )?tet(?: am lich| nguyen dan)?)(?: nam)?(?: (\d{4}))?", q):
        if not take(m):
            continue
        y = int(m.group(2)) if m.group(2) else max((yy for yy, (s, _) in tet.items() if s <= today), default=None)
        if y not in tet:
            add(m.start(), _period(None, None, "tet", f"Tết {y}", 0.0))
            continue
        s, e = tet[y]
        if m.group(1).startswith("truoc"):
            n = (e - s).days + 1
            add(m.start(), _period(s - timedelta(days=n), s - timedelta(days=1), "before_tet", f"trước Tết {y}"))
        else:
            add(m.start(), _period(s, e, "tet", f"Tết {y}"))
            if re.search(r"truoc tet|(\d+) ngay truoc", q[m.end():]):
                n = (e - s).days + 1
                mm = re.search(r"(\d+ ngay )?truoc tet|(\d+) ngay truoc", q[m.end():])
                add(m.end() + mm.start(), _period(s - timedelta(days=n), s - timedelta(days=1), "before_tet", f"trước Tết {y}"))
                for i in range(m.end() + mm.start(), m.end() + mm.end()):
                    used[i] = True

    # 2) N tháng liền nhau kết thúc ở tháng M/Y  |  N tháng liên tiếp từ tháng A/Y đến tháng B/Y -> từng tháng.
    for m in re.finditer(r"(\d+) thang (?:lien nhau|lien tiep|lien).{0,40}?(?:ket thuc o |den |toi |tinh toi |tinh den )(?:thang )?(\d{1,2})(?:/| nam |-)(\d{4})", q):
        if not take(m):
            continue
        n, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        months = []
        for _ in range(n):
            months.append((y, mo))
            y, mo = _prev_month(y, mo)
        for y, mo in reversed(months):
            add(m.start(), _period(date(y, mo, 1), _month_end(y, mo), "month", f"tháng {mo}/{y}"))
        # bỏ các "tháng M/Y" khác trong cùng cụm (VD "từ tháng 2/2025 đến tháng 4/2025")
        for mm in re.finditer(r"thang \d{1,2}(?:/| nam )\d{4}", q[: m.end()]):
            for i in range(mm.start(), mm.end()):
                used[i] = True

    # 2b) Từ tháng A/Y đến tháng B/Y (kể cả "từ 12/25 đến 2/26") -> từng tháng; YY 2 chữ số phải > 12 để không lẫn dd/mm.
    my = r"(?:thang )?(\d{1,2})(?:/|-| nam )(\d{4}|1[3-9]|[2-9]\d)(?![\d/])"
    for m in re.finditer(r"tu " + my + r" (?:den|toi)(?: het)? " + my, q):
        y1, m1, y2, m2 = (int(m.group(i)) for i in (2, 1, 4, 3))
        y1, y2 = (y + 2000 if y < 100 else y for y in (y1, y2))
        if not (1 <= m1 <= 12 and 1 <= m2 <= 12) or (y1, m1) > (y2, m2) or not take(m):
            continue
        while (y1, m1) <= (y2, m2):
            add(m.start(), _period(date(y1, m1, 1), _month_end(y1, m1), "month", f"tháng {m1}/{y1}"))
            y1, m1 = (y1 + 1, 1) if m1 == 12 else (y1, m1 + 1)
    for m in re.finditer(r"thang sau .{0,40}?thang truoc", q):  # "tháng sau có luôn hơn tháng trước": so sánh liền kề, không phải kỳ
        take(m)

    # 3) N ngày kể từ ngày D (+ N ngày ngay trước) — hiệu ứng tăng giá (§5).
    for m in re.finditer(r"(\d+) ngay (?:ke tu|tu hom do|tu ngay do|ke tu hom do|dau)(?: ngay)?(?: " + _DATE + r")?", q):
        if not take(m):
            continue
        n = int(m.group(1))
        d = _mk_date(m.group(2), m.group(3), m.group(4), today) if m.group(2) else None
        if d is None:  # "kể từ ngày tăng/đó": tìm ngày trong câu
            d = next((x for md in re.finditer(r"(?:ngay |vao ngay |tu ngay |tu )" + _DATE, q)
                      if (x := _mk_date(md.group(1), md.group(2), md.group(3), today))), None)
        if d is None:
            add(m.start(), _period(None, None, "event_window", f"{n} ngày kể từ ngày tăng", 0.8, n=n, side="after"))
            add(m.start() + 1, _period(None, None, "event_window", f"{n} ngày ngay trước", 0.8, n=n, side="before"))
        else:
            add(m.start(), _period(d, d + timedelta(days=n - 1), "range", f"{n} ngày kể từ {d}"))
            add(m.start() + 1, _period(d - timedelta(days=n), d - timedelta(days=1), "range", f"{n} ngày trước {d}"))
        for mm in re.finditer(_DATE + r"|\d+ ngay (?:ngay )?truoc", q):  # ngày D và "N ngày trước" đã dùng
            for i in range(mm.start(), mm.end()):
                used[i] = True

    # 4) Từ D1 đến (hết) D2.
    for m in re.finditer(r"tu(?: ngay)? " + _DATE + r" den(?: het)?(?: ngay)? " + _DATE, q):
        if not take(m):
            continue
        d1 = _mk_date(m.group(1), m.group(2), m.group(3) or m.group(6), today)
        d2 = _mk_date(m.group(4), m.group(5), m.group(6), today)
        if d1 and d2:
            add(m.start(), _period(d1, d2, "range", f"{d1} → {d2}"))
    # "từ ngày 1/1 đến hết hôm nay (, năm nay)": ngày không năm = năm nay
    for m in re.finditer(r"tu(?: ngay)? " + _DATE + r"(?: nam nay)? (?:den|toi)(?: het)?(?: ngay)? hom nay(?:,? (?:doanh thu )?nam nay)?", q):
        d1 = _mk_date(m.group(1), m.group(2), m.group(3), today)
        if d1 and take(m):
            add(m.start(), _period(d1, today, "range", f"{d1} → hôm nay"))
    for m in re.finditer(r"nam nay", q):  # "năm nay" đi kèm khoảng đã ghi rõ thì không phải kỳ riêng
        if any(p["kind"] == "range" and p["end"] == today.isoformat() for _, p in found):
            take(m)

    # 5) Tháng này / từ đầu tháng này đến hết hôm nay; cùng kỳ tháng trước (§2).
    has_this_month = re.search(r"thang nay", q) is not None
    for m in re.finditer(r"(?:tu dau thang nay|thang nay|tu dau thang(?= (?:tinh den|den|toi)(?: het)?(?: ngay)? hom nay))(?: (?:tinh den|den|toi)(?: het)?(?: ngay)? hom nay)?", q):
        if not take(m):
            continue
        add(m.start(), _period(today.replace(day=1), today, "month_to_date", "tháng này"))
    for m in re.finditer(r"(?:cung ky )?thang (?:lien |ngay )?(?:truoc|roi)", q):
        if not take(m):
            continue
        py, pm = _prev_month(today.year, today.month)
        if "cung ky" in m.group(0) or has_this_month:
            end = date(py, pm, min(today.day, calendar.monthrange(py, pm)[1]))
            add(m.start(), _period(date(py, pm, 1), end, "same_period_prev_month", "cùng kỳ tháng trước"))
        else:  # "so với tháng trước (đó)": neo theo kỳ được hỏi nếu câu có kỳ cụ thể (xử lý cuối hàm)
            add(m.start(), _period(date(py, pm, 1), _month_end(py, pm), "month", "tháng trước", rel="prev_month"))
    for m in re.finditer(r"(?:cung ky|cung thang|thang \d{1,2}) nam (?:truoc|ngoai|roi)|nam (?:truoc|ngoai) cung ky", q):
        if take(m):
            add(m.start(), _period(None, None, "same_period_prev_year", "cùng kỳ năm trước", rel="prev_year"))

    # 5b) Từ đầu năm (đến hết hôm nay) / năm nay — trước bước "đến hết hôm nay".
    for m in re.finditer(r"tu dau nam(?: nay)?(?: (?:den|toi)(?: het)?(?: nay| hom nay)?)?|dau nam(?: nay)? (?:den|toi)(?: het)?(?: nay| hom nay)?|nam nay", q):
        if take(m):
            add(m.start(), _period(date(today.year, 1, 1), today, "year_to_date", "từ đầu năm"))

    # 6) Tuần (bắt đầu thứ Hai).
    monday = today - timedelta(days=today.weekday())
    for m in re.finditer(r"tuan (nay|truoc|roi|vua roi|vua qua)", q):
        if not take(m):
            continue
        if m.group(1) == "nay":
            add(m.start(), _period(monday, today, "week_to_date", "tuần này"))
        else:
            add(m.start(), _period(monday - timedelta(days=7), monday - timedelta(days=1), "week", "tuần trước"))

    # 7) N ngày gần nhất (kể cả hôm nay).
    for m in re.finditer(r"(\d+) ngay (?:gan nhat|gan day|qua|vua qua)", q):
        if take(m):
            n = int(m.group(1))
            add(m.start(), _period(today - timedelta(days=n - 1), today, "range", f"{n} ngày gần nhất"))

    # 7b) Trong N ngày tới (lô hết hạn): từ hôm nay D đến hết D + N.
    for m in re.finditer(r"(\d+) ngay (?:toi|sap toi|nua)\b", q):
        if take(m):
            n = int(m.group(1))
            add(m.start(), _period(today, today + timedelta(days=n), "next_days", f"{n} ngày tới"))

    # 8) Đến hết ngày X / tính đến hôm nay: luỹ kế từ đầu dữ liệu (§2).
    for m in re.finditer(r"(?:den het|tinh den(?: het)?|cho den(?: het)?|toi(?: het)?)(?: ngay)? (?:(hom nay|hom qua)|" + _DATE + ")", q):
        if not take(m):
            continue
        if m.group(1):
            d = today if m.group(1) == "hom nay" else today - timedelta(days=1)
        else:
            d = _mk_date(m.group(2), m.group(3), m.group(4), today)
        if d:
            add(m.start(), _period(None, d, "upto", f"đến hết {d}"))

    # 9) Hôm nay / hôm qua / hôm kia.
    for word, delta in (("hom nay", 0), ("hom qua", 1), ("hom kia", 2)):
        for m in re.finditer(word, q):
            if take(m):
                d = today - timedelta(days=delta)
                add(m.start(), _period(d, d, "day", word))

    # 10) Quý Q/Y, quý Q (không năm), quý này, quý trước.
    for m in re.finditer(r"quy (\d)(?:(?:/| nam )(\d{4}))?|quy (nay|truoc)", q):
        if not take(m):
            continue
        cur_q = (today.month - 1) // 3 + 1
        if m.group(3) == "nay":
            add(m.start(), _period(date(today.year, 3 * cur_q - 2, 1), today, "quarter_to_date", "quý này"))
            continue
        if m.group(3) == "truoc":
            qq, y = (4, today.year - 1) if cur_q == 1 else (cur_q - 1, today.year)
        else:
            qq = int(m.group(1))
            y = int(m.group(2)) if m.group(2) else _year_for(3 * qq - 2, today)
        if 1 <= qq <= 4:
            add(m.start(), _period(date(y, 3 * qq - 2, 1), _month_end(y, 3 * qq), "quarter", f"quý {qq}/{y}"))

    # 11) Tháng M/Y, tháng M năm Y, tháng M (không năm).
    for m in re.finditer(r"thang (\d{1,2})(?:(?:/| nam |-)(\d{4}))?(?![\d/])", q):
        if not take(m):
            continue
        mo = int(m.group(1))
        if not 1 <= mo <= 12:
            continue
        # "tháng 3, tháng 4, tháng 5 năm 2025": tháng không ghi năm mượn năm của tháng cuối danh sách
        shared = re.match(r"(?:\s*,?\s*(?:va\s+)?thang \d{1,2}(?![\d/]))*\s*,?\s*(?:va\s+)?thang \d{1,2}(?:/| nam |-)(\d{4})", q[m.end():])
        y = int(m.group(2)) if m.group(2) else int(shared.group(1)) if shared else _year_for(mo, today)
        add(m.start(), _period(date(y, mo, 1), _month_end(y, mo), "month", f"tháng {mo}/{y}",
                               1.0 if m.group(2) or shared else 0.9))

    # 11b) Tháng viết trơn (public test): "6/2025", "6-2025", "8/26" (MM/YY khi YY > 12, nên không lẫn ngày dd/mm).
    for m in re.finditer(r"(?<![\d/.-])(\d{1,2})[/-](\d{4}|\d{2})(?![\d/.-])", q):
        mo, yy = int(m.group(1)), m.group(2)
        if not 1 <= mo <= 12 or (len(yy) == 2 and int(yy) <= 12):
            continue
        if take(m):
            y = int(yy) if len(yy) == 4 else 2000 + int(yy)
            add(m.start(), _period(date(y, mo, 1), _month_end(y, mo), "month", f"tháng {mo}/{y}", 0.9))

    # 12) Năm Y (sau tháng/quý để không ăn "năm 2025" của "tháng 8 năm 2025").
    for m in re.finditer(r"nam (\d{4})", q):
        if take(m):
            y = int(m.group(1))
            add(m.start(), _period(date(y, 1, 1), date(y, 12, 31), "year", f"năm {y}"))

    # 13) Ngày D đứng một mình.
    for m in re.finditer(r"(?<![\d/.,x])" + _DATE + r"(?![\d/]|[.,]?[a-z])", q):
        if take(m):
            d = _mk_date(m.group(1), m.group(2), m.group(3), today)
            if d:
                add(m.start(), _period(d, d, "day", d.isoformat(), 1.0 if m.group(3) else 0.9))

    out = []
    for _, p in sorted(found, key=lambda t: t[0]):  # bỏ kỳ trùng ("Năm nay, tính từ đầu năm...")
        if p.get("rel") or not any((p["start"], p["end"]) == (o["start"], o["end"]) and p["start"] for o in out if not o.get("rel")):
            out.append(p)
    # "tới bữa ni ..., tính cả kỳ hạn đúng bữa ni": ngày lẻ trùng mốc "đến hết" là cùng một mốc
    upto_ends = {p["end"] for p in out if p["kind"] == "upto"}
    out = [p for p in out if not (p["kind"] == "day" and p["end"] in upto_ends)]
    # Kỳ so sánh tương đối ("tháng trước đó", "cùng kỳ năm trước") neo theo kỳ được hỏi đầu tiên.
    anchor = next((p for p in out if not p.get("rel") and p["start"] and p["kind"] != "upto"), None)
    for p in out:
        rel = p.pop("rel", None)
        if rel or p["kind"] in ("same_period_prev_month", "before_tet") or (p["kind"] == "range" and "trước" in p["label"]):
            p["role"] = "compare"
        if not rel or anchor is None:
            if rel == "prev_year":
                p["confidence"] = 0.0
            continue
        a_start, a_end = _d(anchor["start"]), _d(anchor["end"])
        if rel == "prev_month":
            py, pm = _prev_month(a_start.year, a_start.month)
            p.update(start=date(py, pm, 1).isoformat(), end=_month_end(py, pm).isoformat(),
                     label=f"tháng {pm}/{py}")
        else:
            def back(d: date) -> date:
                return d.replace(year=d.year - 1, day=min(d.day, calendar.monthrange(d.year - 1, d.month)[1]))
            p.update(start=back(a_start).isoformat(), end=back(a_end).isoformat())
    return out


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(resolve(sys.argv[1], sys.argv[2]), ensure_ascii=False, indent=1))
