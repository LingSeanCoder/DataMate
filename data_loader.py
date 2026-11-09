"""
data_loader.py — Bộ nạp dữ liệu SCHEMA-AGNOSTIC cho DataMate.

Không hard-code bất kỳ tên file, tên bảng hay tên cột nào. Chỉ cần trỏ vào
thư mục của MỘT doanh nghiệp (VD: data/biz_103) là nó tự:

  1. Quét TẤT CẢ file .xlsx / .xls / .csv / .sqlite trong thư mục đó.
  2. Với Excel: đọc mọi sheet. Với mỗi sheet tự dò dòng header (bỏ dòng tiêu
     đề / dòng trống / header 2 tầng kiểu merged cell).
  3. Gom các sheet / file có CÙNG bộ cột về một bảng logic
     (sheet theo tháng, file tách đuôi _1 _2 _2025...).
  4. Làm sạch tổng quát:
       - tên cột có Ngày / Thời gian / Hạn / Giờ  -> parse date (ISO)
       - tên cột có Tiền / Giá / NCC / Tổng ...   -> parse tiền (float VND)
       - cột có "%"                               -> parse phần trăm (points)
       - cột số lượng -> tách số đầu chuỗi ("1 kg" -> 1)
       - bỏ dòng rác, dòng lặp header, ô trống
  5. Ghi tất cả vào MỘT file SQLite, kèm bảng metadata cho agent:
       _meta_tables, _meta_columns (kèm SELECT DISTINCT mẫu), _meta_info,
       _quy_doi (parse từ README).

Dùng:
    python data_loader.py --biz-dir data/biz_103 --db tmp_db/biz_103.db
    python data_loader.py --data-root data --out-dir tmp_db      # build tất cả
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sqlite3
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

# --------------------------------------------------------------------------
# Hằng số nhận diện kiểu cột
# --------------------------------------------------------------------------

# Tên cột gợi ý NGÀY. Đặt trước tiền để "Ngày trả" không bị hiểu là tiền.
DATE_HINTS = (
    "ngay", "ngày", "thoi gian", "thời gian", "thoigian", "time", "date",
    "gio", "giờ", "han", "hạn", "deadline", "ap dung", "áp dụng", "sinh",
    "created", "updated", "timestamp",
)

# Tên cột gợi ý SỐ LƯỢNG (phải xét trước tiền).
QUANTITY_HINTS = (
    "so luong", "số lượng", "soluong", "sl", "s.l", "qty", "quantity",
    "so luong quy doi", "quy doi", "quy ra", "he so", "hệ số", "ti le", "tỉ lệ",
    "stt", "so thu tu", "số thứ tự", "lan", "lần", "dot", "đợt", "thang", "tháng",
)

# Tên cột gợi ý TIỀN.
MONEY_HINTS = (
    "tien", "tiền", "gia", "giá", "amount", "price", "tong", "tổng", "thanh",
    "thành", "tri gia", "trị giá", "ncc", "thu", "chi", "no", "nợ", "luong",
    "lương", "phi", "phí", "giam", "giảm", "von", "vốn", "tra", "trả", "paid",
    "total", "doanh thu", "doanh so", "doanh số", "lai", "lãi", "value",
    "gia tri", "giá trị", "con lai", "còn lại", "du", "dư", "phai", "phải",
)

# Tên cột KHÔNG BAO GIỜ là số dù giá trị nhìn giống số.
TEXT_FORCE_HINTS = (
    "ma ", "mã", "ma_", "id", "code", "barcode", "dien thoai", "điện thoại",
    "sdt", "phone", "so dien thoai", "số điện thoại", "email", "dia chi",
    "địa chỉ", "ghi chu", "ghi chú", "note", "ten", "tên", "mo ta", "mô tả",
    "hinh anh", "hình ảnh", "url", "link", "bien so", "biển số", "so xe",
    "số xe", "plate", "cccd", "cmnd", "can cuoc", "căn cước", "so tai khoan",
    "số tài khoản", "stk", "ma vach", "mã vạch", "seri", "serial", "so lo",
    "số lô", "ma lo", "mã lô",
)

ID_HINTS = (
    "ma don", "mã đơn", "ma hoa don", "mã hóa đơn", "ma chung tu", "mã chứng từ",
    "so phieu", "số phiếu", "ma phieu", "mã phiếu", "ma bill", "bill",
    "ma hd", "mã hđ", "order", "invoice", "ma dh", "mã đh", "so ct", "số ct",
    "ma gd", "mã gd", "ma nhap", "mã nhập",
)

CURRENCY_CHARS = "đ₫$€£¥ "
CURRENCY_WORDS = ("vnd", "vnđ", "usd", "eur", "đồng", "dong")

JUNK_ROW_PATTERN = re.compile(
    r"^(tổng|tong|tổng cộng|tong cong|cộng|cong|subtotal|total|"
    r"ghi chú|ghi chu|lưu ý|luu y)\b",
    re.IGNORECASE,
)

# Nhãn của DÒNG TỔNG (cộng dồn) — phải khớp cả ô, tránh nhầm tên hàng
# kiểu "Tổng hợp vitamin" thành dòng tổng.
TOTAL_LABEL_PATTERN = re.compile(
    r"^(?:tổng|tong|cộng|cong|subtotal|total)\b"
    r"(?:\s+(?:cộng|cong|ngày|ngay|tháng|thang|quý|quy|năm|nam|tuần|tuan|"
    r"số|so|đơn|don|hóa đơn|hoa don|tiền|tien|hàng|hang|hóa|hoa|lượng|luong|"
    r"nhập|nhap|xuất|xuat|chi|thu|doanh thu|doanh số|doanh so|thanh toán|"
    r"thanh toan|phải trả|phai tra|công nợ|cong no|tồn|ton|kho|bán|ban|"
    r"tất cả|tat ca|toàn bộ|toan bo|theo))*"
    r"(?:\s*[\d/:.,\-–]+)*"
    r"\s*$",
    re.IGNORECASE,
)

MONTH_VI = re.compile(
    r"(?:ng[àa]y\s+)?(\d{1,2})\s*(?:th[áa]ng|thg|th)\s*(\d{1,2})"
    r"(?:\s*(?:n[ăa]m|[/-])?\s*(\d{4}|\d{2})(?!\d))?",
    re.IGNORECASE,
)

SUPPORTED_SUFFIXES = (".xlsx", ".xls", ".xlsm", ".csv", ".txt", ".sqlite", ".db")


# --------------------------------------------------------------------------
# Tiện ích chuỗi / số
# --------------------------------------------------------------------------

def strip_diacritics(text: str) -> str:
    """Bỏ dấu tiếng Việt: 'Giá bán' -> 'Gia ban'."""
    if not text:
        return ""
    text = str(text).replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in nfkd if unicodedata.category(ch) != "Mn")


def slug(text: Any, default: str = "col") -> str:
    """Chuẩn hoá thành định danh ASCII snake_case an toàn cho SQLite."""
    s = strip_diacritics(str(text)).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    s = re.sub(r"_+", "_", s)
    if not s:
        return default
    if s[0].isdigit():
        s = "c_" + s
    return s


def sql_quote(identifier: str) -> str:
    """Bọc identifier bằng " và escape " bên trong."""
    return '"' + str(identifier).replace('"', '""') + '"'


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    return s == "" or s.lower() in ("nan", "none", "nat", "null")


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", strip_diacritics(str(text)).lower()).strip()


def parse_number(value: Any) -> float | None:
    """Parse số kiểu Việt Nam / lẫn lộn, trả None nếu không phải số.

    Ví dụ: "90.000đ" -> 90000, "7,100" -> 7100, "1,105,000" -> 1105000,
           "1,500.0000" -> 1.5, "25k" -> 25000, "(1.200)" -> -1200,
           "1 kg" -> 1, "-53.500đ" -> -53500.
    """
    if value is None or isinstance(value, bool):
        return float(value) if isinstance(value, bool) else None
    if isinstance(value, (int, float)):
        f = float(value)
        return None if (math.isnan(f) or math.isinf(f)) else f

    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "null", "nat", "-", "--"):
        return None

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    if s.startswith("-"):
        negative, s = not negative, s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()

    raw = s.lower()
    for word in CURRENCY_WORDS:
        raw = raw.replace(word, "")
    raw = "".join(ch for ch in raw if ch not in CURRENCY_CHARS).strip()
    raw = raw.replace("%", "").strip()

    multiplier = 1.0
    for suffix, mult in (("tỷ", 1e9), ("ty", 1e9), ("triệu", 1e6),
                         ("trieu", 1e6), ("tr", 1e6), ("nghìn", 1e3),
                         ("nghin", 1e3), ("k", 1e3)):
        if raw.endswith(suffix) and len(raw) > len(suffix):
            tail = raw[: -len(suffix)].strip()
            if re.fullmatch(r"[\d.,]+", tail):
                raw, multiplier = tail, mult
                break

    # Tách số đầu chuỗi kiểu "1 kg", "2 chai" (chỉ khi phần còn lại không phải số).
    m = re.match(r"^([\d.,]+)\s*[^\d.,\s].*$", raw)
    if m:
        raw = m.group(1)
    raw = raw.replace(" ", "")
    if not raw or not re.search(r"\d", raw):
        return None
    if not re.fullmatch(r"[\d.,]+", raw):
        return None

    has_dot, has_comma = "." in raw, "," in raw
    if has_dot and has_comma:
        decimal_sep = "." if raw.rfind(".") > raw.rfind(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        raw = raw.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif has_comma:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", raw):
            raw = raw.replace(",", "")
        else:
            raw = raw.replace(",", ".")
    elif has_dot:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
            raw = raw.replace(".", "")
    try:
        result = float(raw) * multiplier
    except ValueError:
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return -result if negative else result


_EXCEL_EPOCH = date(1899, 12, 30)


def parse_date_value(value: Any) -> datetime | None:
    """Parse ngày linh hoạt. Trả datetime (có giờ) hoặc datetime 00:00."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Excel serial date (20000..60000 tương đương 1954..2064)
        if 20000 <= float(value) <= 60000:
            return datetime.combine(
                _EXCEL_EPOCH + timedelta(days=int(value)), datetime.min.time()
            )
        return None

    s = str(value).strip()
    if not s:
        return None

    # Excel serial dạng chuỗi: "45658"
    if re.fullmatch(r"\d{5}", s):
        serial = int(s)
        if 20000 <= serial <= 60000:
            return datetime.combine(
                _EXCEL_EPOCH + timedelta(days=serial), datetime.min.time()
            )

    # ISO: 2025-02-01, 2025-02-01 06:00:25, 2025-02-01T06:00:25
    m = re.match(
        r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})"
        r"(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?",
        s,
    )
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4) or 0)
        mm = int(m.group(5) or 0)
        ss = int(m.group(6) or 0)
        return _safe_dt(y, mo, d, hh, mm, ss)

    # DD/MM/YYYY hoặc DD-MM-YYYY (dayfirst)
    m = re.match(
        r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})"
        r"(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?",
        s,
    )
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000 if y < 70 else 1900
        hh = int(m.group(4) or 0)
        mm = int(m.group(5) or 0)
        ss = int(m.group(6) or 0)
        if mo > 12 and d <= 12:  # lỡ ghi MM/DD
            d, mo = mo, d
        return _safe_dt(y, mo, d, hh, mm, ss)

    # 1 thg 6 2025 / ngày 1 tháng 6 năm 2025
    m = MONTH_VI.search(s)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else datetime.now().year
        if y < 100:
            y += 2000 if y < 70 else 1900
        return _safe_dt(y, mo, d)

    # Chỉ có giờ: 06:00:25
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
    if m:
        return _safe_dt(1970, 1, 1, int(m.group(1)), int(m.group(2)),
                        int(m.group(3) or 0))
    return None


def _safe_dt(y: int, mo: int, d: int, hh: int = 0, mm: int = 0,
             ss: int = 0) -> datetime | None:
    try:
        return datetime(y, mo, d, hh, mm, ss)
    except ValueError:
        return None


def format_datetime(dt: datetime, with_time: bool) -> str:
    if with_time:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# Đọc file thô
# --------------------------------------------------------------------------

def _read_csv_raw(path: Path) -> pd.DataFrame:
    """Đọc CSV với nhiều encoding/delimiter, giữ nguyên header=None."""
    last_err: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1258", "cp1252", "latin-1"):
        try:
            with path.open("r", encoding=encoding, newline="", errors="strict") as fh:
                sample = fh.read(8192)
            delimiter = ","
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
            return pd.read_csv(
                path,
                sep=delimiter,
                header=None,
                dtype=str,
                keep_default_na=False,
                skip_blank_lines=False,
                encoding=encoding,
                engine="python",
                on_bad_lines="skip",
            )
        except (UnicodeDecodeError, pd.errors.ParserError) as exc:
            last_err = exc
            continue
    raise RuntimeError(f"Không đọc được CSV {path.name}: {last_err}")


def _read_excel_raw(path: Path) -> dict[str, pd.DataFrame]:
    """Đọc mọi sheet, giữ nguyên header=None."""
    engine = "xlrd" if path.suffix.lower() == ".xls" else None
    sheets = pd.read_excel(
        path, sheet_name=None, header=None, dtype=object, engine=engine
    )
    return {str(name): df for name, df in sheets.items()}


def _read_sqlite_raw(path: Path) -> dict[str, pd.DataFrame]:
    """Đọc mọi bảng của file sqlite nguồn."""
    result: dict[str, pd.DataFrame] = {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            try:
                result[table] = pd.read_sql_query(
                    f"SELECT * FROM {sql_quote(table)}", conn
                )
            except Exception:  # noqa: BLE001 - bảng hỏng thì bỏ qua
                continue
    finally:
        conn.close()
    return result


# --------------------------------------------------------------------------
# Dò header
# --------------------------------------------------------------------------

def _value_kind_guess(value: Any) -> str:
    if _is_blank(value):
        return "blank"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    s = str(value).strip()
    if len(s) <= 40 and parse_date_value(s) is not None and re.search(r"\d", s):
        return "date"
    if parse_number(s) is not None and re.fullmatch(r"[\d.,%\-+ ]+", s):
        return "number"
    return "text"


def detect_header_row(raw: pd.DataFrame) -> int | None:
    """Chọn dòng header tốt nhất trong 12 dòng đầu.

    Điểm = số ô có dữ liệu + số giá trị phân biệt - phạt ô trông như số/ngày.
    Nhờ vậy bỏ được dòng tiêu đề, dòng trống, và header 2 tầng (merged cell).
    """
    if raw is None or raw.empty:
        return None
    limit = min(12, len(raw))
    best_row: int | None = None
    best_score = -1e9

    for r in range(limit):
        cells = raw.iloc[r].tolist()
        filled = [c for c in cells if not _is_blank(c)]
        if len(filled) < 2:
            continue
        score = 2.0 * len(filled) + len({slug(str(c), "x") for c in filled})
        for c in filled:
            kind = _value_kind_guess(c)
            if kind in ("number", "date"):
                score -= 3.0
            elif len(str(c)) > 80:
                score -= 2.0
            if str(c).strip().startswith(("-", "+", "=")):
                score -= 1.0
        # Ưu tiên dòng mà các dòng ngay sau có nhiều dữ liệu (không phải header).
        for nxt in range(r + 1, min(r + 3, limit)):
            below = raw.iloc[nxt].tolist()
            if sum(1 for c in below if not _is_blank(c)) >= max(2, len(filled) // 2):
                score += 1.0
        if score > best_score:
            best_score, best_row = score, r

    if best_row is None:
        return None

    # Nếu "header" lại gần như toàn số/ngày thì nhiều khả năng file không có header.
    chosen = [c for c in raw.iloc[best_row].tolist() if not _is_blank(c)]
    suspicious = sum(1 for c in chosen if _value_kind_guess(c) in ("number", "date"))
    if chosen and suspicious / len(chosen) > 0.4:
        return None
    return best_row


def _build_columns(raw_header: list[Any]) -> list[str]:
    """Chuẩn hoá tên cột: slug hoá, chống trùng, đặt tên cho cột trống."""
    columns: list[str] = []
    seen: dict[str, int] = {}
    for i, cell in enumerate(raw_header):
        base = "cot_%d" % (i + 1) if _is_blank(cell) else slug(cell, f"cot_{i + 1}")
        base = base or f"cot_{i + 1}"
        if base in seen:
            seen[base] += 1
            base = f"{base}_{seen[base]}"
        else:
            seen[base] = 1
        columns.append(base)
    return columns


# --------------------------------------------------------------------------
# Làm sạch cột
# --------------------------------------------------------------------------

def _name_has(name: str, hints: tuple[str, ...]) -> bool:
    """So khớp tên cột với danh sách từ khoá, chịu được viết liền/không dấu.

    "MaHoaDon" khớp "mã hóa đơn"; "SoLuong" khớp "số lượng".
    """
    n = _norm(name)
    n_compact = re.sub(r"[\s_]+", "", n)
    for hint in hints:
        h = _norm(hint)
        if h and h in n:
            return True
        h_compact = re.sub(r"[\s_]+", "", h)
        if h_compact and h_compact in n_compact:
            return True
    return False


def _parse_date_for_inference(value: Any, allow_serial: bool) -> datetime | None:
    """Như parse_date_value nhưng có thể từ chối Excel serial trần.

    Tránh nhầm cột tiền (giá 45.689đ) thành cột ngày khi tên cột không gợi ý ngày.
    """
    if not allow_serial:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return None
        if isinstance(value, str) and re.fullmatch(r"\s*\d{5}\s*", value):
            return None
    return parse_date_value(value)


def infer_kind(series: pd.Series, column: str) -> str:
    """Suy ra kiểu dữ liệu: date / percent / number / text."""
    name = column
    values = [v for v in series.tolist() if not _is_blank(v)]
    if not values:
        return "text"

    if _name_has(name, TEXT_FORCE_HINTS):
        return "text"

    sample = values[:400]
    n = len(sample)
    allow_serial = _name_has(name, DATE_HINTS)

    # 1) Ngày: ưu tiên theo tên cột, rồi theo tỉ lệ parse được.
    date_like = sum(
        1 for v in sample if _parse_date_for_inference(v, allow_serial) is not None
    )
    if _name_has(name, DATE_HINTS) and date_like / n >= 0.5:
        return "date"
    if date_like / n >= 0.85 and not _name_has(name, QUANTITY_HINTS):
        return "date"

    # 2) Phần trăm.
    pct_like = sum(1 for v in sample if isinstance(v, str) and v.strip().endswith("%"))
    if pct_like / n >= 0.7:
        return "percent"

    # 3) Số.
    num_like = sum(1 for v in sample if parse_number(v) is not None)
    if num_like / n >= 0.85:
        # Giữ nguyên dạng text cho mã có số 0 đứng đầu.
        leading_zero = sum(
            1 for v in sample
            if isinstance(v, str) and re.fullmatch(r"0\d{3,}", v.strip())
        )
        if leading_zero / n > 0.5:
            return "text"
        return "number"
    if _name_has(name, MONEY_HINTS) and num_like / n >= 0.6:
        return "number"
    return "text"


def clean_series(series: pd.Series, kind: str) -> tuple[pd.Series, bool]:
    """Trả về (series đã làm sạch, có chứa giờ hay không)."""
    if kind == "date":
        cache: dict[str, Any] = {}

        def conv(v: Any) -> str | None:
            key = "" if v is None else str(v)
            if key in cache:
                return cache[key]
            parsed = parse_date_value(v)
            result = None if parsed is None else format_datetime(parsed, False)
            cache[key] = result
            return result

        parsed_list = [parse_date_value(v) for v in series.tolist()]
        has_time = any(
            p is not None and (p.hour or p.minute or p.second) for p in parsed_list
        )
        if has_time:
            out = [
                None if p is None else format_datetime(p, True) for p in parsed_list
            ]
        else:
            out = [conv(v) for v in series.tolist()]
        return pd.Series(out, index=series.index, dtype=object), has_time

    if kind == "percent":
        out = []
        for v in series.tolist():
            if _is_blank(v):
                out.append(None)
                continue
            text = str(v).strip()
            out.append(parse_number(text))
        return pd.Series(out, index=series.index, dtype=object), False

    if kind == "number":
        out = [None if _is_blank(v) else parse_number(v) for v in series.tolist()]
        return pd.Series(out, index=series.index, dtype=object), False

    # Text: vector hoá. Chuẩn hóa Unicode NFC + gộp khoảng trắng.
    # KHÔNG lower(): sẽ phá tên riêng / tên sản phẩm.
    text = pd.Series(
        [
            None if _is_blank(v) else re.sub(
                r"\s+", " ", unicodedata.normalize("NFC", str(v))
            ).strip()
            for v in series.tolist()
        ],
        index=series.index,
        dtype=object,
    )
    return text, False


# --------------------------------------------------------------------------
# Làm sạch bảng
# --------------------------------------------------------------------------

def _drop_junk_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if df.empty:
        return df, 0
    before = len(df)
    ncols = max(1, len(df.columns))

    # Vector hoá: đếm số ô có dữ liệu trên mỗi dòng.
    not_blank = df.notna()
    filled = not_blank.sum(axis=1)

    # Dòng hoàn toàn trống.
    junk = filled == 0

    # Dòng tiêu đề kiểu "Tổng cộng" chỉ có 1 ô ở cột đầu.
    first = df.iloc[:, 0].astype(str).str.strip()
    junk |= (filled == 1) & first.str.match(JUNK_ROW_PATTERN)
    junk |= (filled / ncols < 0.25) & first.str.match(TOTAL_LABEL_PATTERN)

    # Dòng tổng / cộng dồn nằm ở BẤT KỲ cột nào (VD: "TỔNG NGÀY 01/02" ở cột
    # tên hàng, tiền ở cột thành tiền) — chỉ xét các dòng thưa ô.
    sparse = filled / ncols <= 0.5
    if bool(sparse.any()):
        labels = df.where(not_blank, "").astype(str)
        label_hit = labels.apply(
            lambda col: col.str.strip().str.match(TOTAL_LABEL_PATTERN)
        )
        junk |= sparse & label_hit.any(axis=1)

    out = df[~junk]
    return out, before - len(out)


def _drop_header_echo(df: pd.DataFrame, columns: list[str],
                      originals: list[str] | None = None) -> tuple[pd.DataFrame, int]:
    """Xoá các dòng lặp lại đúng chuỗi header (lỗi export rất phổ biến)."""
    if df.empty:
        return df, 0
    names = {slug(c) for c in columns}
    if originals:
        names |= {slug(o) for o in originals if o}
        # So khớp nguyên văn (giữ dấu) để bắt dòng lặp header y hệt.
        names |= {str(o).strip().lower() for o in originals if o}
    names.discard("")
    if not names:
        return df, 0

    hits = pd.Series(0, index=df.index, dtype="int64")
    for col in df.columns:
        series = df[col]
        if series.dtype != object:
            continue
        hits += series.fillna("").astype(str).str.strip().str.lower().isin(names).astype("int64")

    threshold = max(2, len(columns) // 2)
    mask = hits < threshold
    out = df[mask]
    return out, len(df) - len(out)


def _detect_group_key(df: pd.DataFrame) -> str | None:
    """Tìm cột khoá đơn (mã đơn / số phiếu...) để gộp dòng trống theo đơn."""
    best = None
    best_score = 0.0
    total = max(1, len(df))
    for col in df.columns:
        name = _norm(col)
        if not _name_has(name, ID_HINTS):
            continue
        non_null = df[col].notna().sum()
        if non_null < 10:
            continue
        distinct = df[col].nunique(dropna=True)
        if distinct < 5:
            continue
        repeat_ratio = 1.0 - distinct / non_null
        if repeat_ratio > best_score:
            best_score, best = repeat_ratio, col
    return best if best_score > 0.05 else None


def _fill_blanks_within_group(df: pd.DataFrame, key: str) -> tuple[pd.DataFrame, int]:
    """Ô trống ở dòng sau của cùng một đơn = giá trị dòng đầu (Rulebook mục 8).

    Chỉ điền khi trong cùng nhóm, cột đó chỉ có DUY NHẤT một giá trị khác rỗng.
    """
    if key not in df.columns or df.empty:
        return df, 0
    filled = 0
    out = df.copy()
    for col in out.columns:
        if col == key:
            continue
        series = out[col]
        if series.isna().sum() == 0:
            continue
        grouped = series.groupby(out[key], dropna=False)
        nunique = grouped.nunique(dropna=True)
        # Chỉ những nhóm có đúng 1 giá trị khác rỗng mới được điền.
        safe_groups = set(nunique[nunique == 1].index.tolist())
        if not safe_groups:
            continue
        mask = series.isna() & out[key].isin(safe_groups)
        if not mask.any():
            continue
        mapping = grouped.first()
        out.loc[mask, col] = out.loc[mask, key].map(mapping)
        filled += int(mask.sum())
    return out, filled


def clean_table(
    df: pd.DataFrame,
    columns: list[str],
    dedupe: bool | None = None,
    originals: list[str] | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Làm sạch một bảng đã có header. Trả về (df, thống kê)."""
    stats = {
        "rows_in": len(df),
        "rows_out": 0,
        "removed_junk": 0,
        "removed_header_echo": 0,
        "removed_duplicate": 0,
        "filled_blanks": 0,
        "group_key": None,
    }
    if df.empty:
        return df, stats

    df = df.copy()
    df.columns = columns

    # 1) Bỏ cột hoàn toàn trống.
    df = df.loc[:, [c for c in df.columns if df[c].notna().any()]]
    columns = list(df.columns)

    # 2) Bỏ dòng rác / dòng lặp header.
    df, stats["removed_junk"] = _drop_junk_rows(df)
    df, stats["removed_header_echo"] = _drop_header_echo(df, columns, originals)

    # 3) Điền ô trống theo nhóm đơn (chỉ khi chắc chắn).
    key = _detect_group_key(df)
    stats["group_key"] = key
    if key:
        df, stats["filled_blanks"] = _fill_blanks_within_group(df, key)

    # 4) Loại trùng (Bắt buộc chạy + Chuẩn hóa text trước khi xóa)
    before = len(df)
    for col in df.columns:
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col]):
            df[col] = df[col].astype(str).str.strip()
            df[col] = df[col].replace({'nan': '', 'None': '', 'NaT': ''})
        elif pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].fillna(0)

    df = df.drop_duplicates().reset_index(drop=True)
    stats["removed_duplicate"] = before - len(df)

    df = df.reset_index(drop=True)
    stats["rows_out"] = len(df)
    return df, stats


# --------------------------------------------------------------------------
# Gom sheet/file thành bảng logic
# --------------------------------------------------------------------------

def _base_table_name(stem: str) -> str:
    """Bỏ đuôi _1 _2 _2025 _2026 để gộp các file bị tách."""
    name = stem
    while True:
        new = re.sub(r"_(?:\d{1,4}|20\d{2})$", "", name)
        if new == name:
            break
        name = new
    return name or stem


class _SheetSlice:
    __slots__ = ("frame", "columns", "original", "source", "sheet", "candidate", "stats")

    def __init__(self, frame, columns, original, source, sheet, candidate):
        self.frame = frame
        self.columns = columns
        self.original = original
        self.source = source
        self.sheet = sheet
        self.candidate = candidate
        self.stats = {}


GENERIC_SHEET = re.compile(r"^(sheet|trang|data|table|bang|sheet\d+)\s*\d*$", re.IGNORECASE)


def _collect_slices(biz_dir: Path, report: dict) -> list[_SheetSlice]:
    slices: list[_SheetSlice] = []
    files = sorted(
        p for p in biz_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )

    for path in files:
        suffix = path.suffix.lower()
        is_sqlite = suffix in (".sqlite", ".db")
        try:
            if suffix in (".csv", ".txt"):
                raw_sheets = {path.stem: _read_csv_raw(path)}
            elif suffix in (".xlsx", ".xls", ".xlsm"):
                raw_sheets = _read_excel_raw(path)
            elif is_sqlite:
                raw_sheets = _read_sqlite_raw(path)
            else:
                continue
        except Exception as exc:  # noqa: BLE001
            report["warnings"].append(f"Bỏ qua {path.name}: {exc}")
            continue

        parsed: list[tuple[str, pd.DataFrame, list[str], int | None, list[str]]] = []
        for sheet_name, raw in raw_sheets.items():
            if raw is None or raw.empty:
                continue

            if is_sqlite:
                # read_sql_query đã trả đúng header -> không dò lại.
                columns = _build_columns(list(raw.columns))
                original = [
                    "" if _is_blank(c) else re.sub(r"\s+", " ", str(c)).strip()
                    for c in raw.columns
                ]
                while len(original) < len(columns):
                    original.append("")
                body = raw.copy()
                body.columns = columns
                parsed.append((sheet_name, body, columns, -1, original[: len(columns)]))
                continue

            header_row = detect_header_row(raw)
            if header_row is None:
                continue
            header_cells = raw.iloc[header_row].tolist()
            columns = _build_columns(header_cells)
            original = [
                "" if _is_blank(c) else re.sub(r"\s+", " ", str(c)).strip()
                for c in header_cells
            ]
            while len(original) < len(columns):
                original.append("")
            original = original[: len(columns)]
            body = raw.iloc[header_row + 1:].reset_index(drop=True)
            if body.empty:
                continue
            body.columns = columns
            parsed.append((sheet_name, body, columns, header_row, original))

        if not parsed:
            report["files"].append(
                {"file": path.name, "kind": suffix.lstrip("."),
                 "sheets": len(raw_sheets), "slices": 0,
                 "note": "không dò được header"}
            )
            continue

        base = _base_table_name(path.stem)

        if is_sqlite:
            # Mỗi bảng sqlite là một bảng độc lập, tên theo bảng gốc.
            for sheet_name, body, columns, header_row, original in parsed:
                item = _SheetSlice(body, columns, original, path.name,
                                   sheet_name, slug(sheet_name, "bang"))
                item.stats["header_row"] = header_row
                slices.append(item)
        else:
            # Gom theo bộ cột: file tách _1/_2 và sheet theo tháng cùng schema.
            by_sig: dict[tuple[str, ...], list[tuple]] = {}
            for entry in parsed:
                by_sig.setdefault(tuple(entry[2]), []).append(entry)

            if len(by_sig) == 1:
                candidates = {sig: base for sig in by_sig}
            else:
                main_sig = max(by_sig, key=lambda s: sum(len(e[1]) for e in by_sig[s]))
                candidates = {}
                for sig, group in by_sig.items():
                    if sig == main_sig:
                        candidates[sig] = base
                    else:
                        sheet = group[0][0]
                        if GENERIC_SHEET.match(sheet or ""):
                            candidates[sig] = f"{base}__{slug(sheet, 'sheet')}"
                        else:
                            candidates[sig] = slug(sheet, "sheet")

            for sheet_name, body, columns, header_row, original in parsed:
                item = _SheetSlice(body, columns, original, path.name,
                                   sheet_name, candidates[tuple(columns)])
                item.stats["header_row"] = header_row
                slices.append(item)

        report["files"].append(
            {
                "file": path.name,
                "kind": suffix.lstrip("."),
                "sheets": len(raw_sheets),
                "slices": len(parsed),
                "multi_schema": len({tuple(e[2]) for e in parsed}) > 1,
            }
        )
    return slices


def _group_slices(slices: list[_SheetSlice]) -> list[tuple[str, list[_SheetSlice]]]:
    """Gom các slice có cùng bộ cột về một bảng."""
    groups: dict[tuple[str, ...], list[_SheetSlice]] = {}
    for item in slices:
        groups.setdefault(tuple(item.columns), []).append(item)

    result: list[tuple[str, list[_SheetSlice]]] = []
    for _, items in groups.items():
        counts: dict[str, int] = {}
        for item in items:
            counts[item.candidate] = counts.get(item.candidate, 0) + 1
        name = sorted(counts.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0][0]
        result.append((name, items))
    return result


# --------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------

def _distinct_preview(series: pd.Series, kind: str) -> tuple[int, str | None]:
    """Mô phỏng SELECT DISTINCT cho cột phân loại -> JSON gọn cho prompt."""
    if kind != "text":
        return 0, None
    non_null = series.dropna()
    if non_null.empty:
        return 0, None
    try:
        counts = non_null.value_counts()
    except TypeError:
        return 0, None
    n_distinct = int(len(counts))
    if n_distinct > 25 or n_distinct > len(non_null) * 0.5:
        return n_distinct, None
    values = [str(v)[:60] for v in counts.index.tolist()][:25]
    return n_distinct, json.dumps(values, ensure_ascii=False)


def _column_range(series: pd.Series, kind: str) -> tuple[str | None, str | None]:
    values = series.dropna()
    if values.empty:
        return None, None
    try:
        lo, hi = values.min(), values.max()
        return str(lo)[:32], str(hi)[:32]
    except TypeError:
        return None, None


# --------------------------------------------------------------------------
# Parse README
# --------------------------------------------------------------------------

CONVERSION_LINE = re.compile(
    r"^\s*-\s*(?P<item>.+?)\s*:\s*1\s+(?P<big>.+?)\s*=\s*"
    r"(?P<factor>[\d.,]+)\s*(?P<base>.+?)\s*$"
)
DATA_END = re.compile(
    r"Dữ liệu có đến hết\s*(\d{1,2})[/-](\d{1,2})[/-](\d{4})",
    re.IGNORECASE,
)


def parse_readme(readme_text: str) -> dict:
    """Tách README thành: text mô tả, bảng quy đổi, ngày hết dữ liệu."""
    conversions: list[dict] = []
    kept_lines: list[str] = []
    dropped = 0

    for line in readme_text.splitlines():
        m = CONVERSION_LINE.match(line)
        if m:
            factor = parse_number(m.group("factor"))
            if factor:
                conversions.append(
                    {
                        "san_pham": m.group("item").strip(),
                        "don_vi_lon": m.group("big").strip(),
                        "he_so": factor,
                        "don_vi_co_ban": m.group("base").strip(),
                    }
                )
                dropped += 1
                continue
        kept_lines.append(line)

    text = "\n".join(kept_lines)
    if dropped:
        text += (
            f"\n\n(Ghi chú hệ thống: {dropped} dòng quy đổi đơn vị đã được tách "
            "thành bảng `_quy_doi(san_pham, don_vi_lon, he_so, don_vi_co_ban)` "
            "trong chính database này. Hãy JOIN bảng đó khi cần đổi đơn vị.)"
        )

    data_end = None
    m = DATA_END.search(readme_text)
    if m:
        try:
            data_end = date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            data_end = None

    return {"text": text.strip(), "conversions": conversions, "data_end": data_end}


# --------------------------------------------------------------------------
# Ghi SQLite
# --------------------------------------------------------------------------

_SQL_TYPES = {"date": "TEXT", "percent": "REAL", "number": "REAL", "text": "TEXT"}


def _to_sql_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    try:
        if hasattr(value, "item"):
            return _to_sql_value(value.item())
    except Exception:  # noqa: BLE001
        pass
    return str(value)


def _create_table(conn: sqlite3.Connection, name: str,
                  columns: list[tuple[str, str]]) -> None:
    cols_sql = ", ".join(f"{sql_quote(c)} {sql_type}" for c, sql_type in columns)
    conn.execute(f"CREATE TABLE {sql_quote(name)} ({cols_sql})")


def _insert_rows(conn: sqlite3.Connection, name: str, columns: list[str],
                 frame: pd.DataFrame) -> None:
    placeholders = ", ".join("?" for _ in columns)
    col_sql = ", ".join(sql_quote(c) for c in columns)
    sql = f"INSERT INTO {sql_quote(name)} ({col_sql}) VALUES ({placeholders})"
    rows = [
        tuple(_to_sql_value(v) for v in row)
        for row in frame[columns].itertuples(index=False, name=None)
    ]
    for start in range(0, len(rows), 5000):
        conn.executemany(sql, rows[start:start + 5000])


def build_database(
    biz_dir: str | Path,
    db_path: str | Path,
    readme_path: str | Path | None = None,
    verbose: bool = True,
) -> dict:
    """Build 1 file SQLite cho 1 doanh nghiệp. Trả về report."""
    biz_dir = Path(biz_dir)
    db_path = Path(db_path)
    if not biz_dir.is_dir():
        raise FileNotFoundError(f"Không thấy thư mục doanh nghiệp: {biz_dir}")

    report: dict = {
        "business_dir": str(biz_dir),
        "db_path": str(db_path),
        "tables": {},
        "files": [],
        "warnings": [],
        "rows_in": 0,
        "rows_out": 0,
    }

    slices = _collect_slices(biz_dir, report)
    if not slices:
        raise RuntimeError(f"Không đọc được file dữ liệu nào trong {biz_dir}")

    grouped = _group_slices(slices)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    readme_file = Path(readme_path) if readme_path else biz_dir / "README.md"
    readme_text = ""
    if readme_file.is_file():
        readme_text = readme_file.read_text(encoding="utf-8", errors="replace")
    readme_info = parse_readme(readme_text)

    used_names: set[str] = set()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = OFF")
        meta_tables: list[tuple] = []
        meta_columns: list[tuple] = []

        for raw_name, items in grouped:
            table = slug(raw_name, "bang")
            base_name, suffix = table, 2
            while table in used_names:
                table = f"{base_name}_{suffix}"
                suffix += 1
            used_names.add(table)

            frame = pd.concat([i.frame for i in items], ignore_index=True, sort=False)
            columns = list(items[0].columns)
            frame = frame.reindex(columns=columns)

            originals = list(items[0].original)
            frame, stats = clean_table(frame, columns, originals=originals)
            columns = list(frame.columns)
            if frame.empty:
                report["warnings"].append(f"Bảng {table} rỗng sau làm sạch, bỏ qua.")
                continue

            kinds: dict[str, str] = {}
            for col in columns:
                kind = infer_kind(frame[col], col)
                cleaned, _ = clean_series(frame[col], kind)
                frame[col] = cleaned
                kinds[col] = kind

            # Chỉ loại trùng lần cuối, sau khi đã chuẩn hoá kiểu.
            if stats["group_key"] is not None:
                before = len(frame)
                frame = frame.drop_duplicates().reset_index(drop=True)
                stats["removed_duplicate"] += before - len(frame)
            stats["rows_out"] = len(frame)

            # ÉP XÓA TRÙNG LẦN CUỐI TRƯỚC KHI GHI VÀO SQLITE
            before = len(frame)
            frame = frame.drop_duplicates(subset=list(frame.columns), keep="first").reset_index(drop=True)
            if before - len(frame) > 0:
                print(f"  [Dọn dẹp] Đã xóa thêm {before - len(frame)} dòng trùng trước khi nạp SQLite.")

            col_defs = [(col, _SQL_TYPES[kinds[col]]) for col in columns]
            _create_table(conn, table, col_defs)
            _insert_rows(conn, table, columns, frame)

            sources = sorted({f"{i.source}[{i.sheet}]" for i in items})
            meta_tables.append(
                (table, "; ".join(sources), len(frame))
            )
            for col in columns:
                n_distinct, preview = _distinct_preview(frame[col], kinds[col])
                lo, hi = _column_range(frame[col], kinds[col]) \
                    if kinds[col] in ("date", "number") else (None, None)
                original = col
                for item in items:
                    if col in item.columns:
                        idx = list(item.columns).index(col)
                        if idx < len(item.original) and item.original[idx]:
                            original = item.original[idx]
                        break
                meta_columns.append(
                    (table, col, original, kinds[col], n_distinct, lo, hi, preview)
                )

            report["tables"][table] = {
                "rows": len(frame),
                "columns": columns,
                "kinds": kinds,
                "sources": sources,
                **{k: v for k, v in stats.items() if k != "rows_out"},
            }
            report["rows_in"] += stats.get("rows_in", 0)
            report["rows_out"] += len(frame)

            if verbose:
                print(f"  + {table}: {len(frame):,} dòng, {len(columns)} cột "
                      f"({', '.join(sources[:3])}{'...' if len(sources) > 3 else ''})")

        # ----- metadata cho agent -----
        conn.execute(
            "CREATE TABLE _meta_tables ("
            "table_name TEXT PRIMARY KEY, source TEXT, row_count INTEGER)"
        )
        conn.executemany("INSERT INTO _meta_tables VALUES (?, ?, ?)", meta_tables)
        conn.execute(
            "CREATE TABLE _meta_columns ("
            "table_name TEXT, column_name TEXT, original_name TEXT, kind TEXT, "
            "n_distinct INTEGER, min_value TEXT, max_value TEXT, top_values TEXT)"
        )
        conn.executemany(
            "INSERT INTO _meta_columns VALUES (?, ?, ?, ?, ?, ?, ?, ?)", meta_columns
        )
        conn.execute("CREATE TABLE _meta_info (key TEXT PRIMARY KEY, value TEXT)")
        info = [
            ("business_dir", str(biz_dir)),
            ("data_end_date", readme_info["data_end"] or ""),
            ("n_tables", str(len(meta_tables))),
        ]
        conn.executemany("INSERT INTO _meta_info VALUES (?, ?)", info)

        conn.execute(
            "CREATE TABLE _quy_doi (san_pham TEXT, don_vi_lon TEXT, "
            "he_so REAL, don_vi_co_ban TEXT)"
        )
        if readme_info["conversions"]:
            conn.executemany(
                "INSERT INTO _quy_doi VALUES (?, ?, ?, ?)",
                [
                    (c["san_pham"], c["don_vi_lon"], c["he_so"], c["don_vi_co_ban"])
                    for c in readme_info["conversions"]
                ],
            )

        # Lưu README đã rút gọn để agent đọc lại nếu cần.
        conn.execute("CREATE TABLE _meta_readme (content TEXT)")
        conn.execute("INSERT INTO _meta_readme VALUES (?)", (readme_info["text"],))

        conn.commit()
        report["views"] = []
        report["readme"] = {
            "found": bool(readme_text),
            "data_end_date": readme_info["data_end"],
            "conversions": len(readme_info["conversions"]),
            "text_chars": len(readme_info["text"]),
        }
    finally:
        conn.close()

    report_path = db_path.with_suffix(".report.json")
    try:
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass
    return report


def build_all(data_root: str | Path, out_dir: str | Path,
              verbose: bool = True) -> dict[str, dict]:
    """Build DB cho mọi thư mục con của data_root."""
    data_root = Path(data_root)
    out_dir = Path(out_dir)
    results: dict[str, dict] = {}
    biz_dirs = sorted(p for p in data_root.iterdir() if p.is_dir())
    for biz in biz_dirs:
        if verbose:
            print(f"\n=== {biz.name} ===")
        try:
            results[biz.name] = build_database(
                biz, out_dir / f"{biz.name}.db", verbose=verbose
            )
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"  [LỖI] {biz.name}: {type(exc).__name__}: {exc}")
            results[biz.name] = {"error": f"{type(exc).__name__}: {exc}"}
    return results


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _force_utf8_stdio() -> None:
    """Console Windows mặc định cp1252 -> in tiếng Việt sẽ crash."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Làm sạch dữ liệu (schema-agnostic) và tạo SQLite DB"
    )
    parser.add_argument("--biz-dir", default=None,
                        help="Thư mục của MỘT doanh nghiệp (VD: data/biz_103)")
    parser.add_argument("--data-root", default=None,
                        help="Thư mục chứa nhiều doanh nghiệp (VD: data)")
    parser.add_argument("--db", default=None,
                        help="Đường dẫn DB đầu ra (dùng với --biz-dir)")
    parser.add_argument("--out-dir", default="tmp_db",
                        help="Thư mục chứa DB đầu ra (dùng với --data-root)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    verbose = not args.quiet
    try:
        if args.data_root:
            results = build_all(args.data_root, args.out_dir, verbose=verbose)
            ok = sum(1 for r in results.values() if "error" not in r)
            print(f"\nHoàn tất: {ok}/{len(results)} doanh nghiệp build thành công.")
            return 0
        if not args.biz_dir:
            parser.error("Cần --biz-dir hoặc --data-root")
        db_path = args.db or str(Path(args.out_dir) / f"{Path(args.biz_dir).name}.db")
        if verbose:
            print(f"=== {args.biz_dir} ===")
        report = build_database(args.biz_dir, db_path, verbose=verbose)
        print(f"\n  Bảng: {len(report['tables'])} | "
              f"dòng: {report['rows_in']:,} -> {report['rows_out']:,}")
        print(f"  DB: {db_path}")
        if report["warnings"]:
            print("  Cảnh báo:")
            for w in report["warnings"][:10]:
                print("   -", w)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"\n[LỖI] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
