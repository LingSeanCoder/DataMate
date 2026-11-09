"""Định dạng đáp án theo rulebook §9. Dùng chung cho metric library và đường text-to-SQL.

    shape_answer(value, expected_type, money=False, month=False, sort=None) -> {"type", "answer"}
    rank(items, k=None)  -> items đã xếp: số đo giảm dần, bằng nhau theo mã tăng dần (không mã thì theo tên)

Sai kiểu không đoán bừa: raise FormatError để tầng trên retry hoặc abstain (sai = -1, bỏ trống = 0).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

TYPES = ("number", "percent", "date", "boolean", "string", "list", "table")


class FormatError(ValueError):
    pass


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def round_half_up(x: float | int | Decimal, digits: int = 0) -> int | float:
    """0.5 làm tròn lên (§9), không theo banker's rounding của round()."""
    d = Decimal(repr(x) if isinstance(x, float) else str(x))
    q = d.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return int(q) if digits == 0 else float(q)


def fmt_number(v: Any, money: bool = False) -> int | float:
    if not _is_num(v):
        raise FormatError(f"number cần số, nhận {v!r}")
    if money:
        return round_half_up(v)          # tiền: số nguyên đồng
    r = round_half_up(v, 2)               # số trung bình khác: 2 chữ số
    return int(r) if float(r).is_integer() else r


def fmt_percent(v: Any) -> float:
    if not _is_num(v):
        raise FormatError(f"percent cần số, nhận {v!r}")
    return round_half_up(v, 2)


def fmt_date(v: Any, month: bool = False) -> str:
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, date):
        return v.strftime("%Y-%m" if month else "%Y-%m-%d")
    if isinstance(v, str):
        s = v.strip()[:10]
        if len(s) >= 7 and s[4] == "-":
            try:
                if len(s) == 7 or month:
                    date.fromisoformat(s[:7] + "-01")
                    return s[:7]
                date.fromisoformat(s)
                return s
            except ValueError:
                pass
    raise FormatError(f"date cần YYYY-MM-DD/YYYY-MM, nhận {v!r}")


def fmt_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if _is_num(v) and v in (0, 1):
        return bool(v)
    raise FormatError(f"boolean cần true/false, nhận {v!r}")


def fmt_string(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        if len(v) != 1:
            raise FormatError(f"string cần 1 giá trị, nhận {len(v)}")
        v = v[0]
    if v is None or isinstance(v, (dict, bool)) or str(v).strip() == "":
        raise FormatError(f"string rỗng/không hợp lệ: {v!r}")
    return str(v).strip()


def _cell(v: Any, money: bool) -> Any:
    if _is_num(v):
        return fmt_number(v, money)
    if isinstance(v, (date, datetime)):
        return fmt_date(v)
    return v.strip() if isinstance(v, str) else v


def rank(items: list[dict], k: int | None = None, desc: bool = True) -> list[dict]:
    """items: {"name", "value", "code"?}. Số đo giảm dần (desc), bằng nhau theo mã tăng dần; không mã thì theo tên (§9)."""
    def key(it):
        v = it.get("value") or 0
        tie = it.get("code") if it.get("code") not in (None, "") else it.get("name") or ""
        return (-v if desc else v, str(tie))
    out = sorted(items, key=key)
    return out[:k] if k else out


def shape_answer(value: Any, expected_type: str, *, money: bool = False, month: bool = False,
                 columns: list[str] | None = None, reason: str | None = None) -> dict:
    """value đã là kết quả cuối (số, chuỗi, list...). list cần sắp xếp thì gọi rank() trước."""
    t = expected_type
    if t == "unanswerable":
        return {"type": "unanswerable", "reason": reason or "không đủ dữ liệu"}
    if t == "abstain":
        return {"type": "abstain"}
    if t not in TYPES:
        raise FormatError(f"type lạ: {t}")
    if t == "number":
        ans: Any = fmt_number(value, money)
    elif t == "percent":
        ans = fmt_percent(value)
    elif t == "date":
        ans = fmt_date(value, month)
    elif t == "boolean":
        ans = fmt_bool(value)
    elif t == "string":
        ans = fmt_string(value)
    elif t == "list":
        if not isinstance(value, (list, tuple)):
            raise FormatError(f"list cần mảng, nhận {value!r}")
        ans = [_cell(v, money) for v in value]
    else:  # table
        rows = value
        if not (isinstance(rows, (list, tuple)) and columns):
            raise FormatError("table cần rows + columns")
        if any(len(r) != len(columns) for r in rows):
            raise FormatError("table: số cột không khớp")
        ans = {"columns": list(columns), "rows": [[_cell(c, money) for c in r] for r in rows]}
    return {"type": t, "answer": ans}
