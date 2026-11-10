"""
agent.py — DataMate SQL Agent (NVIDIA NIM + SQLite), SCHEMA-AGNOSTIC.

Không biết trước bất kỳ tên bảng/cột nào. Mọi thứ lấy từ metadata do
`data_loader.py` ghi vào DB:

    _meta_tables(table_name, source, row_count)
    _meta_columns(table_name, column_name, original_name, kind,
                  n_distinct, min_value, max_value, top_values)
    _meta_info(key, value)
    _quy_doi(san_pham, don_vi_lon, he_so, don_vi_co_ban)
    _meta_readme(content)

Luồng 1 câu hỏi:
  1. Dựng system prompt gồm:
       - README.md của CHÍNH doanh nghiệp đó (mô tả nghiệp vụ, quy ước).
       - Schema từ metadata: bảng, cột, kiểu, khoảng giá trị, và giá trị
         DISTINCT của các cột phân loại (tức "đã tự chạy SELECT DISTINCT").
       - Rulebook chung (doanh thu, thời gian, làm tròn, format đáp án).
  2. LLM trả {"sql": "...", "type": "..."} (temperature = 0).
  3. Chạy SQL read-only. Lỗi -> gửi lỗi lại cho LLM sửa, tối đa 3 lần.
  4. Kết quả rỗng -> rà lại 1 lần, vẫn rỗng -> abstain.
  5. Ép kiểu đáp án -> {"id", "type", "answer"}.

Dùng như thư viện:
    agent = DataMateAgent(db_path="tmp_db/biz_103.db",
                          readme_text=open("data/biz_103/README.md").read())
    answer, debug = agent.ask("Doanh thu tháng 6/2025?", "2026-06-24")
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
import random
import re
import sqlite3
import sys
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

try:
    from rapidfuzz import fuzz, process
    _RAPIDFUZZ_AVAILABLE = True
except Exception:  # pragma: no cover
    fuzz = None
    process = None
    _RAPIDFUZZ_AVAILABLE = False

from data_loader import parse_number, slug, sql_quote, strip_diacritics

# --------------------------------------------------------------------------
# Cấu hình
# --------------------------------------------------------------------------

DEFAULT_MODEL = "Qwen2.5-Coder-14B-Instruct"
MAX_ROWS = 5000
QUERY_TIMEOUT_SEC = 25
DEFAULT_PROMPT_BUDGET = 6300   # token input ước lượng (NIM rate limit cao hơn Groq)
CHARS_PER_TOKEN = 2.3

VI_WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]

MONEY_WORDS = (
    "tiền", "doanh thu", "doanh số", "giá", "lãi", "nợ", "vốn", "nhập hàng",
    "chi phí", "lương", "phí", "thu về", "giá trị", "doanh thu",
)

FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|"
    r"pragma|vacuum|reindex|trigger|transaction|commit|rollback)\b",
    re.IGNORECASE,
)

# Mục rulebook luôn cần (doanh thu, thời gian, không trả lời được, format).
# Từ dừng khi tính điểm schema linking (tránh khớp nhầm "nhất", "bao nhiêu"...).
_LINK_STOPWORDS = {
    "nhat", "bao", "nhieu", "nao", "gi", "cua", "cho", "khong", "duoc",
    "nay", "do", "khi", "voi", "tren", "duoi", "tat", "ca", "hien", "tai",
    "theo", "trong", "ngoai", "mot", "hai", "ba", "bon", "nam", "sau",
    "bay", "tam", "chin", "muoi", "hay", "hoac", "va", "thi", "la", "co",
}

ALWAYS_SECTIONS = ("1", "2", "7", "8", "9", "10")
SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "3": ("nhất", "top", "xếp hạng", "bán chạy", "hot", "khách", "nhân viên",
          "giỏi", "lời", "nhiều tiền", "trung bình"),
    "4": ("nợ", "công nợ", "tuổi nợ", "thu nợ", "trả nợ", "cod"),
    "5": ("kho", "tồn", "nhập", "giá vốn", "lãi", "hết hàng", "ế", "giá bán",
          "tăng giá", "lô", "chi phí", "hạn dùng"),
    "6": ("kê đơn", "hoạt chất", "thuốc", "vật liệu", "cà phê", "size"),
}

# Few-shot dự phòng khi DB không đủ cột để sinh ví dụ động.
# Cố ý KHÔNG chứa tên bảng/cột cụ thể nào: DB quá nghèo cột thì thà dạy
# phương pháp còn hơn dạy tên bảng không tồn tại (dễ gây bịa schema).
_STATIC_FEW_SHOT = (
    "VÍ DỤ MẪU (FEW-SHOT):\n"
    "- Đếm đơn trong 1 ngày: đếm DISTINCT cột mã đơn của bảng giao dịch "
    "có cột ngày, lọc ngày bằng LIKE 'YYYY-MM-DD%'.\n"
    "- Doanh thu 1 tháng: SUM cột tổng tiền của bảng giao dịch, lọc "
    ">= 'YYYY-MM-01' AND < ngày 1 tháng sau, loại trạng thái hủy/trả.\n"
    "- Doanh thu theo mặt hàng: nếu bảng giao dịch có cột tên hàng thì "
    "LIKE '%tên%' ngay trên đó; nếu chỉ có mã hàng thì JOIN sang bảng "
    "danh mục (có mã + tên) rồi LIKE trên cột tên.\n"
    "- Hạn sử dụng: tìm bảng có cột hạn dùng, nối sang bảng danh mục để "
    "lọc tên hàng, rồi ORDER BY cột hạn dùng ASC LIMIT 1.\n"
)


# --------------------------------------------------------------------------
# Chuẩn hóa câu hỏi: viết tắt chat / telex / thiếu dấu -> từ chuẩn
# --------------------------------------------------------------------------
# Chỉ thay cụm từ đã biết. KHÔNG lower() toàn câu để giữ tên riêng và tên
# sản phẩm. Các cụm dài được thay trước cụm ngắn (xem _compile_phrase_rules).
_Q_PHRASES: dict[str, str] = {
    # --- thời gian ---
    "hôm wa": "hôm qua",
    "hom wa": "hôm qua",
    "hum qua": "hôm qua",
    "hom qua": "hôm qua",
    "hom roi": "hôm qua",
    "hôm wa rồi": "hôm qua",
    "hôm ni": "hôm nay",
    "hom ni": "hôm nay",
    "hom nay": "hôm nay",
    "hôm nay": "hôm nay",
    "tuan trc": "tuần trước",
    "tuần trc": "tuần trước",
    "tuan truoc": "tuần trước",
    "tuần truoc": "tuần trước",
    "tuan roi": "tuần trước",
    "tuan rồi": "tuần trước",
    "tuần rồi": "tuần trước",
    "tuan nay": "tuần này",
    "tuần nay": "tuần này",
    "thang trc": "tháng trước",
    "tháng trc": "tháng trước",
    "thang truoc": "tháng trước",
    "tháng truoc": "tháng trước",
    "thang roi": "tháng trước",
    "tháng rồi": "tháng trước",
    "thang rồi": "tháng trước",
    "tháng nay": "tháng này",
    "thang nay": "tháng này",
    "thang sau": "tháng sau",
    "tháng sau": "tháng sau",
    "nam trc": "năm trước",
    "năm trc": "năm trước",
    "nam truoc": "năm trước",
    "năm truoc": "năm trước",
    "nam roi": "năm trước",
    "năm nay": "năm này",
    "nam nay": "năm này",
    "quy trc": "quý trước",
    "quý trc": "quý trước",
    "quy truoc": "quý trước",
    "quy roi": "quý trước",
    "quy nay": "quý này",
    "quý nay": "quý này",
    "cung ky": "cùng kỳ",
    "cùng ky": "cùng kỳ",
    "ky nay": "kỳ này",
    "ky truoc": "kỳ trước",
    "ky roi": "kỳ trước",
    "ca sang": "ca sáng",
    "ca chieu": "ca chiều",
    "ca toi": "ca tối",
    "ca dem": "ca đêm",
    "dau thang": "đầu tháng",
    "đầu thang": "đầu tháng",
    "cuoi thang": "cuối tháng",
    "cuối thang": "cuối tháng",
    "giua thang": "giữa tháng",
    "giữa thang": "giữa tháng",
    "dau nam": "đầu năm",
    "cuoi nam": "cuối năm",
    "dau ky": "đầu kỳ",
    "cuoi ky": "cuối kỳ",
    "moi nhat": "mới nhất",
    "mới nhat": "mới nhất",
    "cu nhat": "cũ nhất",
    "cũ nhat": "cũ nhất",
    "gan nhat": "gần nhất",
    "gần nhat": "gần nhất",
    "xa nhat": "xa nhất",
    "sap het han": "sắp hết hạn",
    "sắp het han": "sắp hết hạn",
    "sap hsd": "sắp hết hạn",
    "het han": "hết hạn",
    "hết han": "hết hạn",
    "han su dung": "hạn sử dụng",
    "hạn su dung": "hạn sử dụng",
    "han dung": "hạn sử dụng",
    "hansudung": "hạn sử dụng",
    "ngay san xuat": "ngày sản xuất",
    "nsx": "ngày sản xuất",
    "ngay het han": "ngày hết hạn",
    "ngay nhap": "ngày nhập",
    "ngay ban": "ngày bán",
    "ngay lap": "ngày lập",
    "ngay tao": "ngày tạo",
    "ngay giao": "ngày giao",
    "ngay dat": "ngày đặt",
    # --- doanh thu / tiền ---
    "dthu": "doanh thu",
    "dthú": "doanh thu",
    "dth": "doanh thu",
    "dtthu": "doanh thu",
    "doanhthu": "doanh thu",
    "doanh-thu": "doanh thu",
    "doang thu": "doanh thu",
    "doanh thú": "doanh thu",
    "tong thu": "tổng thu",
    "tong chi": "tổng chi",
    "tongcong": "tổng cộng",
    "tong cong": "tổng cộng",
    "tongtien": "tổng tiền",
    "tong tien": "tổng tiền",
    "tong so": "tổng số",
    "tongso": "tổng số",
    "thanh tien": "thành tiền",
    "thanhtien": "thành tiền",
    "thanh toan": "thanh toán",
    "thanhtoan": "thanh toán",
    "tien mat": "tiền mặt",
    "tienmat": "tiền mặt",
    "chuyen khoan": "chuyển khoản",
    "chiet khau": "chiết khấu",
    "chietkhau": "chiết khấu",
    "giam gia": "giảm giá",
    "giamgia": "giảm giá",
    "gia ban": "giá bán",
    "giaban": "giá bán",
    "gia nhap": "giá nhập",
    "gianhap": "giá nhập",
    "gia von": "giá vốn",
    "giavon": "giá vốn",
    "don gia": "đơn giá",
    "dongia": "đơn giá",
    "loi nhuan": "lợi nhuận",
    "loinhuan": "lợi nhuận",
    "chi phi": "chi phí",
    "chiphi": "chi phí",
    "thu nhap": "thu nhập",
    "thunhap": "thu nhập",
    "cong no": "công nợ",
    "congno": "công nợ",
    "con no": "còn nợ",
    "conno": "còn nợ",
    "khach no": "khách nợ",
    "khachno": "khách nợ",
    "phai thu": "phải thu",
    "phaithu": "phải thu",
    "phai tra": "phải trả",
    "phaitra": "phải trả",
    "dat coc": "đặt cọc",
    # --- số lượng / đơn hàng ---
    "so luong": "số lượng",
    "soluong": "số lượng",
    "so don": "số đơn",
    "sodon": "số đơn",
    "don hang": "đơn hàng",
    "donhang": "đơn hàng",
    "hoa don": "hóa đơn",
    "hoadon": "hóa đơn",
    "hdon": "hóa đơn",
    "ma don": "mã đơn",
    "madon": "mã đơn",
    "ma hang": "mã hàng",
    "mahang": "mã hàng",
    "ma sp": "mã sản phẩm",
    "san pham": "sản phẩm",
    "spham": "sản phẩm",
    "sản pham": "sản phẩm",
    "mat hang": "mặt hàng",
    "mathang": "mặt hàng",
    "khach hang": "khách hàng",
    "khachhang": "khách hàng",
    "kh hang": "khách hàng",
    "nha cung cap": "nhà cung cấp",
    "nhacungcap": "nhà cung cấp",
    "nhan vien": "nhân viên",
    "cua hang": "cửa hàng",
    "cuahang": "cửa hàng",
    "nhap hang": "nhập hàng",
    "nhaphang": "nhập hàng",
    "ban hang": "bán hàng",
    "banhang": "bán hàng",
    "ton kho": "tồn kho",
    "tonkho": "tồn kho",
    "ton dau ky": "tồn đầu kỳ",
    "ton cuoi ky": "tồn cuối kỳ",
    "nhap kho": "nhập kho",
    "nhapkho": "nhập kho",
    "xuat kho": "xuất kho",
    "xuatkho": "xuất kho",
    "phieu nhap": "phiếu nhập",
    "phieu xuat": "phiếu xuất",
    "lo hang": "lô hàng",
    "lohang": "lô hàng",
    "het hang": "hết hàng",
    "hethang": "hết hàng",
    "con lai": "còn lại",
    "conlai": "còn lại",
    "ban duoc": "bán được",
    "banduoc": "bán được",
    "mua vao": "mua vào",
    "ban ra": "bán ra",
    # --- thống kê / so sánh ---
    "cao nhat": "cao nhất",
    "caonhat": "cao nhất",
    "thap nhat": "thấp nhất",
    "lon nhat": "lớn nhất",
    "nho nhat": "nhỏ nhất",
    "nhieu nhat": "nhiều nhất",
    "nhiều nhat": "nhiều nhất",
    "nhìu nhất": "nhiều nhất",
    "it nhat": "ít nhất",
    "ban chay nhat": "bán chạy nhất",
    "ban nhieu nhat": "bán nhiều nhất",
    "chay nhat": "chạy nhất",
    "trung binh": "trung bình",
    "trungbinh": "trung bình",
    "binh quan": "bình quân",
    "binhquan": "bình quân",
    "ti le": "tỷ lệ",
    "tỉ lệ": "tỷ lệ",
    "ty le": "tỷ lệ",
    "tile": "tỷ lệ",
    "tyle": "tỷ lệ",
    "phan tram": "phần trăm",
    "phantram": "phần trăm",
    "chenh lech": "chênh lệch",
    "chenhlech": "chênh lệch",
    "so voi": "so với",
    "sovoi": "so với",
    "tung thang": "từng tháng",
    "tungthang": "từng tháng",
    "tung quy": "từng quý",
    "tung ngay": "từng ngày",
    "tung nam": "từng năm",
    "theo thang": "theo tháng",
    "theothang": "theo tháng",
    "theo quy": "theo quý",
    "theo ngay": "theo ngày",
    "theo nam": "theo năm",
    "moi thang": "mỗi tháng",
    "moi ngay": "mỗi ngày",
    "moi quy": "mỗi quý",
    "moi nam": "mỗi năm",
    "tat ca": "tất cả",
    "tatca": "tất cả",
    "toan bo": "toàn bộ",
    "bao nhieu": "bao nhiêu",
    "bao nhiu": "bao nhiêu",
    "bao nhìu": "bao nhiêu",
    "bao nhjeu": "bao nhiêu",
    "baonhieu": "bao nhiêu",
    "baonhiu": "bao nhiêu",
    "bnhiu": "bao nhiêu",
    "bnhieu": "bao nhiêu",
    "bn nhiêu": "bao nhiêu",
    "bao nhiêu": "bao nhiêu",
    "may cai": "mấy cái",
    "may hop": "mấy hộp",
    "may chai": "mấy chai",
    "may lon": "mấy lon",
    "may ly": "mấy ly",
    "may vien": "mấy viên",
    "may don": "mấy đơn",
    "may khach": "mấy khách",
    "may nguoi": "mấy người",
    "may thang": "mấy tháng",
    "may ngay": "mấy ngày",
    "may san pham": "mấy sản phẩm",
    "may mat hang": "mấy mặt hàng",
    "may loai": "mấy loại",
    # --- từ chat ---
    "cho hoi": "cho hỏi",
    "cho minh hoi": "cho mình hỏi",
    "giup minh": "giúp mình",
    "giup em": "giúp em",
    "lam on": "làm ơn",
    "vui long": "vui lòng",
    "dien thoai": "điện thoại",
    "ddieenj thoai": "điện thoại",
    "ddieenj thoại": "điện thoại",
    "dieenj thoai": "điện thoại",
    "dieenj thoại": "điện thoại",
    "vaayj": "vậy",
    "zay": "vậy",
    "zậy": "vậy",
}

_Q_WORDS: dict[str, str] = {
    # viết tắt
    "hqua": "hôm qua",
    "hqa": "hôm qua",
    "hnay": "hôm nay",
    "sp": "sản phẩm",
    "spham": "sản phẩm",
    "kh": "không",
    "ko": "không",
    "k": "không",
    "khg": "không",
    "khong": "không",
    "hok": "không",
    "hem": "không",
    "hông": "không",
    "khum": "không",
    "dc": "được",
    "đc": "được",
    "dk": "được",
    "đk": "được",
    "duoc": "được",
    "vs": "với",
    "voi": "với",
    "j": "gì",
    "z": "gì",
    "jd": "gì",
    "gi": "gì",
    "bjt": "biết",
    "bit": "biết",
    "biet": "biết",
    "hiu": "hiểu",
    "hỉu": "hiểu",
    "gium": "giùm",
    "dum": "giùm",
    "dùm": "giùm",
    "giup": "giúp",
    "gjup": "giúp",
    "hoi": "hỏi",
    "coi": "xem",
    "bn": "bao nhiêu",
    "nhiu": "nhiều",
    "nhìu": "nhiều",
    "nhjeu": "nhiều",
    "nhieu": "nhiều",
    "nv": "nhân viên",
    "ncc": "nhà cung cấp",
    "hd": "hóa đơn",
    "sl": "số lượng",
    "dg": "đơn giá",
    "gv": "giá vốn",
    "ln": "lợi nhuận",
    "cp": "chi phí",
    "tb": "trung bình",
    "pct": "phần trăm",
    "hsd": "hạn sử dụng",
    "nsx": "ngày sản xuất",
    "nhap": "nhập",
    "xuat": "xuất",
    # thiếu dấu (chỉ các từ nghiệp vụ rõ nghĩa)
    "thang": "tháng",
    "ngay": "ngày",
    "tuan": "tuần",
    "quy": "quý",
    "don": "đơn",
    "hang": "hàng",
    "tien": "tiền",
    "tong": "tổng",
    "nhat": "nhất",
    "khach": "khách",
    "het": "hết",
    "nao": "nào",
    "den": "đến",
    "tang": "tăng",
    "giam": "giảm",
    "cac": "các",
    "phan": "phần",
    "tram": "trăm",
    "tinh": "tính",
    "hop": "hộp",
    "thuoc": "thuốc",
    "vien": "viên",
    "binh": "bình",
    "cuoi": "cuối",
    "giua": "giữa",
    "truoc": "trước",
    "con": "còn",
    "tat": "tất",
    "mot": "một",
    "cung": "cùng",
    "roi": "rồi",
}

# Cụm từ bị bỏ hẳn (trợ từ cuối câu) — không mang nghĩa với SQL.
_Q_DROP = ("ạ", "nhỉ", "nhé", "nhe", "ak", "àk")

# Từ hàm/đơn vị tính/common words — không coi là tên sản phẩm khi đứng 1-2 từ.
_PRODUCT_STOPWORDS: set[str] = {
    "cua", "cửa", "hang", "hàng", "cua hang", "cửa hàng", "ban", "bán",
    "mua", "tien", "tiền", "doanh", "thu", "loi", "lợi", "nhuan", "nhuận",
    "so", "số", "don", "đơn", "hoa", "hóa", "don hang", "đơn hàng", "hoa don", "hóa đơn",
    "khach", "khách", "nhan", "nhân", "vien", "viên", "ncc", "nha cung cap", "nha cung cấp",
    "vien", "viên", "hop", "hộp", "chai", "lop", "lọ", "goi", "gói", "vi", "vỉ",
    "loc", "lốc", "kg", "cai", "cái", "ly", "ly", "lon", "lon", "bo", "bộ",
    "thang", "tháng", "ngay", "ngày", "tuan", "tuần", "quy", "quý", "nam", "năm",
    "nhat", "nhất", "nhieu", "nhiều", "it", "ít", "cao", "thap", "thấp",
    "trung", "binh", "trung bình", "binh quan", "bình quân",
    "tat", "tat ca", "tatca", "tất cả", "toan", "toan bo", "toàn bộ",
    "nao", "nao", "nào", "gi", "gì", "j", "z", "dau", "đầu", "cuoi", "cuối",
    "giua", "giữa", "truoc", "trước", "sau", "nay", "này", "trc", "trước",
}

_Q_REGEX_RULES: list[tuple[re.Pattern[str], str]] = [
    # "nam 2025" -> "năm 2025" (không đụng "nam" = giới tính)
    (re.compile(r"(?<!\w)nam\s+(?=\d{4}(?!\d))", re.IGNORECASE), "năm "),
    # telex: dd -> đ
    (re.compile(r"dd(?=[a-zà-ỹ])", re.IGNORECASE), "đ"),
]


def _compile_phrase_rules(
    mapping: dict[str, str],
) -> list[tuple[re.Pattern[str], str]]:
    """Biên dịch map cụm từ thành regex có ranh giới từ, dài trước ngắn sau."""
    rules: list[tuple[re.Pattern[str], str]] = []
    for key in sorted(mapping, key=len, reverse=True):
        body = r"\s+".join(re.escape(part) for part in key.split())
        rules.append(
            (re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE), mapping[key])
        )
    return rules


_Q_PHRASE_RULES = _compile_phrase_rules(_Q_PHRASES)
_Q_WORD_RULES = _compile_phrase_rules(_Q_WORDS)
_Q_DROP_RULES = [
    re.compile(rf"(?<!\w){re.escape(w)}(?!\w)", re.IGNORECASE) for w in _Q_DROP
]


def normalize_question(text: str) -> str:
    """Chuẩn hóa câu hỏi tiếng Việt trước khi đưa vào prompt.

    - NFC + gộp khoảng trắng.
    - Mở viết tắt chat/telex: dthu -> doanh thu, hqua -> hôm qua, bn -> bao nhiêu...
    - Khôi phục dấu cho một số từ nghiệp vụ (thang -> tháng...).
    - KHÔNG lower() để giữ tên riêng / tên sản phẩm.
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFC", str(text)).replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # Lặp tới điểm cố định: một số câu bị nhiều lớp viết tắt chồng nhau
    # (vd "tuan roi" -> "tuần rồi" -> "tuần trước").
    for _ in range(3):
        before = s
        for pattern, repl in _Q_PHRASE_RULES:
            s = pattern.sub(repl, s)
        for pattern, repl in _Q_WORD_RULES:
            s = pattern.sub(repl, s)
        for pattern, repl in _Q_REGEX_RULES:
            s = pattern.sub(repl, s)
        for pattern in _Q_DROP_RULES:
            s = pattern.sub(" ", s)
        s = re.sub(r"\s+", " ", s).strip()
        if s == before:
            break
    return re.sub(r"\s+([,?.!%:;])", r"\1", s).strip()



# --------------------------------------------------------------------------
# Rate limiter dùng chung
# --------------------------------------------------------------------------

class TokenRateLimiter:
    """Giữ tổng input-token ước lượng trong 1 phút không vượt hạn của NVIDIA NIM."""

    def __init__(self, tokens_per_minute: int = 6800, safety: float = 0.92) -> None:
        self.limit = max(1000, int(tokens_per_minute * safety))
        self.window = 60.0
        self.events: deque[tuple[float, int]] = deque()
        self.lock = threading.Lock()

    def acquire(self, tokens: int) -> None:
        tokens = max(1, int(tokens))
        while True:
            with self.lock:
                now = time.monotonic()
                while self.events and now - self.events[0][0] >= self.window:
                    self.events.popleft()
                used = sum(t for _, t in self.events)
                if used + tokens <= self.limit or not self.events:
                    self.events.append((now, tokens))
                    return
                wait = self.window - (now - self.events[0][0]) + 0.5
            time.sleep(max(1.0, wait))

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return max(1, int(len(text) / CHARS_PER_TOKEN))


# --------------------------------------------------------------------------
# Tiện ích JSON / SQL
# --------------------------------------------------------------------------

def _strip_string_literals(sql: str) -> str:
    return re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", "''", sql)


def assert_safe_select(sql: str) -> str:
    """Chỉ cho phép đúng 1 câu SELECT/WITH. Chặn mọi lệnh ghi."""
    s = str(sql).strip()
    if not s:
        raise ValueError("SQL rỗng.")
    if s.endswith(";"):
        s = s[:-1].strip()
    bare = _strip_string_literals(s)
    if ";" in bare:
        raise ValueError("Chỉ được chạy một câu SQL duy nhất.")
    if not re.match(r"^(select|with)\b", bare, re.IGNORECASE):
        raise ValueError("Chỉ cho phép câu lệnh SELECT hoặc WITH ... SELECT.")
    if FORBIDDEN_SQL.search(bare):
        raise ValueError("SQL chứa lệnh bị cấm (chỉ được đọc dữ liệu).")
    return s


def extract_json_object(text: str) -> dict:
    """Parse JSON từ output LLM, chịu được ```json ... ``` và text thừa."""
    if text is None:
        raise ValueError("LLM trả về rỗng.")
    text = str(text).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    candidates: list[str] = [text]
    start = text.find("{")
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    break

    for cand in candidates:
        try:
            value = json.loads(cand)
            if isinstance(value, dict):
                return value
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            value = ast.literal_eval(cand)
            if isinstance(value, dict):
                return value
        except (ValueError, SyntaxError):
            pass
    raise ValueError(f"Không parse được JSON từ LLM: {text[:300]}")


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return str(value)
    try:
        if hasattr(value, "item"):
            return _jsonable(value.item())
    except Exception:  # noqa: BLE001
        pass
    return str(value)


def _resolve_aux_file(path: str | Path | None, name: str) -> Path | None:
    """Tìm file phụ trợ (rulebook/calendar) chịu được nhiều cách bố trí thư mục."""
    if path:
        p = Path(path)
        if p.is_file():
            return p
    for candidate in (
        Path(name),
        Path("..") / name,
        Path("..") / ".." / "train" / "train" / name,
        Path("..") / "train" / "train" / name,
    ):
        if candidate.is_file():
            return candidate
    return None


@dataclass
class SQLOutcome:
    rows: list[list[Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    error: str | None = None
    truncated: bool = False


@dataclass
class ColumnMeta:
    name: str
    original: str
    kind: str
    n_distinct: int
    min_value: str | None
    max_value: str | None
    top_values: list[str]


@dataclass
class TableMeta:
    name: str
    row_count: int
    source: str
    columns: list[ColumnMeta] = field(default_factory=list)


# --------------------------------------------------------------------------
# Agent
# --------------------------------------------------------------------------

STD_GUIDE = """## Bảng chuẩn std_* (CHỈ dùng các bảng này)
- std_orders: 1 dòng/đơn. order_date 'YYYY-MM-DD', order_time 'HH:MM:SS', status_norm (hoàn thành|huỷ|trả hàng|đang giao),
  is_valid=1 khi đơn tính doanh thu (không huỷ, không trả), revenue = doanh thu đơn đã đúng rulebook, customer_key (NULL = khách lẻ),
  customer_name, pay_method, channel, employee_id.
- std_lines: dòng hàng, nối std_orders qua order_id. qty_base = số lượng quy về đơn vị cơ bản, line_revenue = thành tiền dòng,
  line_cost = giá vốn cả dòng, category = nhóm hàng, line_kind ('item' | 'adjustment' = voucher/điểm/ship), is_service_fee.
- std_products: product_name, category, brand, size, active_ingredient, is_prescription, cost_price, base_unit.
- std_customers: customer_key, customer_name, customer_group, register_date.
- Các bảng khác nếu có: std_order_money (amount_due, paid_total), std_payments (paid_date, amount), std_purchases (phiếu nhập),
  std_stock, std_lots, std_installments, std_tradeins, std_warranties, std_repairs, std_deliveries.
- Doanh thu: SUM(revenue) FROM std_orders WHERE is_valid=1. Số lượng bán: SUM(qty_base) dòng của đơn is_valid=1."""


class DataMateAgent:
    def __init__(
        self,
        db_path: str | Path,
        readme_text: str | None = None,
        rulebook_path: str | Path | None = None,
        calendar_path: str | Path | None = None,
        model: str | None = None,
        api_key: str | None = None,
        max_fix_attempts: int = 3,
        prompt_budget: int | None = None,
        max_tokens: int = 1200,
        rate_limiter: TokenRateLimiter | None = None,
        review_empty: bool = True,
        full_rulebook: bool = False,
        verbose: bool = False,
        std_only: bool = False,
    ) -> None:
        load_dotenv()
        self.std_only = std_only
        self.db_path = Path(db_path)
        if not self.db_path.is_file():
            raise FileNotFoundError(f"Không thấy database: {self.db_path}")

        self.model = model or os.getenv("LOCAL_LLM_MODEL") or os.getenv("NIM_MODEL", DEFAULT_MODEL)
        self.fallback_model = (
            os.getenv("LOCAL_LLM_FALLBACK_MODEL")
            or os.getenv("NIM_FALLBACK_MODEL")
            or None
        )
        key = api_key or os.getenv("LOCAL_LLM_API_KEY") or os.getenv("NIM_API_KEY", "")

        local_base = os.getenv("LOCAL_LLM_BASE_URL", "").strip()
        if local_base:
            # Local llama.cpp server — không cần key thật
            base_url = local_base
            key = key or "local"
        else:
            # Fallback NIM (dùng khi test online)
            if not key:
                raise ValueError(
                    "Thiếu NIM_API_KEY và LOCAL_LLM_BASE_URL. "
                    "Set LOCAL_LLM_BASE_URL=http://localhost:8000/v1 cho offline."
                )
            base_url = "https://integrate.api.nvidia.com/v1"

        self.client = OpenAI(base_url=base_url, api_key=key)
        self.max_tokens = max_tokens
        self.max_fix_attempts = max(1, max_fix_attempts)
        self.prompt_budget = int(
            prompt_budget
            or os.getenv("DATAMATE_PROMPT_BUDGET", DEFAULT_PROMPT_BUDGET)
        )
        self.rate_limiter = rate_limiter
        self.review_empty = review_empty
        self.full_rulebook = full_rulebook or os.getenv("DATAMATE_FULL_RULEBOOK") == "1"
        self.verbose = verbose

        self.readme_text = (readme_text or "").strip()
        self.rulebook_text = self._read_text(
            _resolve_aux_file(rulebook_path, "rulebook.md")
        )
        if not self.rulebook_text and self.verbose:
            print("[agent] CẢNH BÁO: không tìm thấy rulebook.md — prompt sẽ thiếu luật chấm.",
                  file=sys.stderr)
        self.calendar_path = _resolve_aux_file(calendar_path, "calendar.csv") \
            or Path(calendar_path or "calendar.csv")
        self._tet_cache: dict[int, tuple[str, str] | None] = {}

        self.tables: list[TableMeta] = []
        self.meta_info: dict[str, str] = {}
        self.known_tables: set[str] = set()
        self.known_columns: set[str] = set()
        self._load_metadata()
        self._product_candidates = self._collect_product_candidates()
        self._rulebook_sections = self._split_rulebook()

    # --------------------------- metadata --------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _load_metadata(self) -> None:
        conn = self._connect()
        try:
            cols_by_table: dict[str, list[ColumnMeta]] = {}
            for r in conn.execute(
                "SELECT table_name, column_name, original_name, kind, n_distinct, "
                "min_value, max_value, top_values FROM _meta_columns"
            ):
                top: list[str] = []
                if r["top_values"]:
                    try:
                        top = [str(v) for v in json.loads(r["top_values"])]
                    except (json.JSONDecodeError, TypeError):
                        top = []
                cols_by_table.setdefault(r["table_name"], []).append(
                    ColumnMeta(
                        name=r["column_name"],
                        original=r["original_name"] or r["column_name"],
                        kind=r["kind"] or "text",
                        n_distinct=r["n_distinct"] or 0,
                        min_value=r["min_value"],
                        max_value=r["max_value"],
                        top_values=top,
                    )
                )
            for r in conn.execute(
                "SELECT table_name, source, row_count FROM _meta_tables ORDER BY table_name"
            ):
                name = r["table_name"]
                self.tables.append(
                    TableMeta(
                        name=name,
                        row_count=r["row_count"] or 0,
                        source=r["source"] or "",
                        columns=cols_by_table.get(name, []),
                    )
                )
            try:
                for r in conn.execute("SELECT key, value FROM _meta_info"):
                    self.meta_info[r["key"]] = r["value"]
            except sqlite3.Error:
                pass
            # Nếu README chưa được truyền vào, đọc bản lưu trong DB.
            if not self.readme_text:
                try:
                    row = conn.execute(
                        "SELECT content FROM _meta_readme LIMIT 1"
                    ).fetchone()
                    if row and row["content"]:
                        self.readme_text = str(row["content"])
                except sqlite3.Error:
                    pass
        finally:
            conn.close()

        if self.std_only:
            self._use_std_tables()
        self.known_tables = {t.name for t in self.tables}
        self.known_columns = {
            c.name for t in self.tables for c in t.columns
        } | {"_quy_doi"}

    def _use_std_tables(self) -> None:
        """Chỉ cho LLM thấy các bảng chuẩn std_* (tên cột cố định, đã làm sạch) + ghi chú nghĩa cột."""
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'std\\_%' ESCAPE '\\' "
                                                "ORDER BY name")]
            if not names:
                return
            tables = []
            for t in names:
                n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                cols = []
                for _, c, typ, *_ in conn.execute(f'PRAGMA table_info("{t}")'):
                    nd, lo, hi = conn.execute(f'SELECT COUNT(DISTINCT "{c}"), MIN("{c}"), MAX("{c}") FROM "{t}"').fetchone()
                    top = [str(r[0]) for r in conn.execute(
                        f'SELECT "{c}" FROM "{t}" WHERE "{c}" IS NOT NULL GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 12')] \
                        if nd and nd <= 30 else []
                    kind = "number" if typ.upper() in ("REAL", "INTEGER", "FLOAT") else "text"
                    cols.append(ColumnMeta(name=c, original=c, kind=kind, n_distinct=nd or 0,
                                           min_value=None if lo is None else str(lo), max_value=None if hi is None else str(hi),
                                           top_values=top))
                tables.append(TableMeta(name=t, row_count=n, source="std", columns=cols))
            self.tables = tables
            self.readme_text = STD_GUIDE + "\n\n" + self.readme_text
        finally:
            conn.close()

    # --------------------------- đọc file --------------------------------

    @staticmethod
    def _read_text(path: Path | None) -> str:
        if path is None or not path.is_file():
            return ""
        for enc in ("utf-8-sig", "utf-8", "cp1258", "latin-1"):
            try:
                return path.read_text(encoding=enc)
            except (UnicodeDecodeError, OSError):
                continue
        return ""

    # --------------------------- schema text -----------------------------

    def _filter_relevant_tables(self, question: str) -> list[TableMeta]:
        """Schema linking: chỉ giữ các bảng có khả năng liên quan tới câu hỏi."""
        if not self.tables:
            return []

        q = strip_diacritics(question).lower()
        q_words = {t for t in re.split(r"[^a-z0-9]+", q) if t}
        q_tokens = {t for t in q_words if len(t) >= 3} - _LINK_STOPWORDS

        money_words = {
            "doanh", "thu", "tien", "gia", "ban", "nhap", "chi", "phi",
            "loi", "nhuan", "cong", "no", "thanh", "toan", "chiet", "khau",
            "luong", "von", "lai", "phai", "tra", "thuong",
        }
        date_words = {"ngay", "thang", "quy", "nam", "ky"}
        wants_money = bool(q_words & money_words) or any(
            p in q
            for p in ("doanh thu", "chi phi", "loi nhuan", "cong no", "thanh tien")
        )
        wants_date = bool(q_words & date_words) or "tu ngay" in q or "den ngay" in q

        money_col_tokens = {
            "tien", "thanh", "doanh", "thu", "gia", "price", "amount",
            "total", "revenue", "cost", "discount", "luong", "phi", "lai",
            "von", "chiet", "khau", "cong", "phai", "thuong", "tienmat",
        }

        def _tokens(text: str) -> set[str]:
            return {
                t for t in re.split(r"[^a-z0-9]+", strip_diacritics(text).lower())
                if len(t) >= 3
            }

        scored: list[tuple[int, TableMeta]] = []
        for table in self.tables:
            score = 0

            tname = strip_diacritics(table.name).lower()
            tparts = [p for p in re.split(r"[^a-z0-9]+", tname) if p]
            if tparts and " ".join(tparts) in q:
                score += 4
            tok_hits = {p for p in tparts if len(p) >= 3} & q_tokens
            if len(tparts) == 1 and len(tparts[0]) >= 5:
                tok_hits |= {t for t in q_tokens if t in tparts[0]}
            score += 2 * min(len(tok_hits), 2)

            n_date = n_money = 0
            for col in table.columns:
                for nm in {col.name, col.original or ""}:
                    nm_norm = strip_diacritics(nm).lower()
                    parts = [p for p in re.split(r"[^a-z0-9]+", nm_norm) if p]
                    if len(parts) > 1:
                        if " ".join(parts) in q:
                            score += 3
                    elif parts and parts[0] in q_words:
                        score += 3
                    hits = {p for p in parts if len(p) >= 3} & q_tokens
                    if len(parts) == 1 and len(parts[0]) >= 5:
                        # Tên cột dính (vd "hansudung") -> khớp theo âm tiết con.
                        hits |= {t for t in q_tokens if t in parts[0]}
                    score += 2 * min(len(hits), 2)
                if col.kind == "date":
                    n_date += 1
                if col.kind == "number" and (_tokens(col.name) & money_col_tokens):
                    n_money += 1

            if wants_money and n_money:
                score += 2
            if wants_date and n_date:
                score += 2

            scored.append((score, table))

        matched = [(s, t) for s, t in scored if s > 0]
        if not matched:
            return list(self.tables)

        matched.sort(key=lambda pair: (-pair[0], -pair[1].row_count))
        # Luôn giữ các bảng cấu trúc (đơn / danh mục / chi tiết / lô / thanh toán)
        # vì câu hỏi thường cần JOIN chúng dù tên bảng không khớp từ khoá.
        try:
            must = self._structural_tables()
        except Exception:  # noqa: BLE001 - không để schema linking làm hỏng ask()
            must = []
        result: list[TableMeta] = list(must)
        limit = min(8, max(6, len(must) + 2))
        for _, table in matched:
            if len(result) >= limit:
                break
            if all(t.name != table.name for t in result):
                result.append(table)
        return result or list(self.tables)

    def _build_schema_text(
        self,
        level: int = 0,
        tables: list[TableMeta] | None = None,
    ) -> str:
        """0 = đầy đủ; 1 = DISTINCT ngắn, bỏ min/max; 2 = tên+kiểu; 3 = chỉ tên."""
        lines: list[str] = []
        for table in (tables if tables is not None else self.tables):
            if level >= 3:
                lines.append(
                    f'- "{table.name}" ({table.row_count} dòng): '
                    + ", ".join(c.name for c in table.columns)
                )
                continue

            lines.append(f'- BẢNG "{table.name}" — {table.row_count} dòng')
            for col in table.columns:
                parts = [f"    {col.name}:{col.kind}"]
                if col.original and slug(col.original) != col.name:
                    parts.append(f'[{col.original}]')
                name_l = col.name.lower()
                is_key = "id" in name_l or "ma_" in name_l or "code" in name_l
                if level <= 1 and col.top_values and not is_key:
                    cap = 10 if level == 0 else 5
                    shown = col.top_values[:cap]
                    suffix = ", ..." if len(col.top_values) > cap else ""
                    parts.append(
                        "DISTINCT=(" + ", ".join(f'"{v}"' for v in shown) + suffix + ")"
                    )
                elif col.kind == "date" and col.min_value is not None:
                    # Luôn giữ khoảng ngày: thiếu nó LLM dễ abstain sai.
                    parts.append(f"{str(col.min_value)[:10]}..{str(col.max_value)[:10]}")
                elif level == 0 and col.min_value is not None:
                    parts.append(f"{col.min_value}..{col.max_value}")
                lines.append(" ".join(parts))
        return "\n".join(lines)

    def _data_span(self) -> tuple[str, str] | None:
        """Khoảng ngày bắt đầu / kết thúc của toàn bộ DB (lấy từ metadata)."""
        mins: list[str] = []
        maxs: list[str] = []
        for table in self.tables:
            for col in table.columns:
                if col.kind == "date" and col.min_value and col.max_value:
                    mins.append(str(col.min_value)[:10])
                    maxs.append(str(col.max_value)[:10])
        if not mins:
            return None
        return (min(mins), max(maxs))

    def _build_quydoi_note(self) -> str:
        conn = self._connect()
        try:
            n = conn.execute("SELECT COUNT(*) FROM _quy_doi").fetchone()[0]
        except sqlite3.Error:
            n = 0
        finally:
            conn.close()
        if not n:
            return ""
        return (
            f"- Bảng `_quy_doi(san_pham, don_vi_lon, he_so, don_vi_co_ban)` có {n} dòng "
            "quy đổi đơn vị lấy từ README. Khi câu hỏi yêu cầu đơn vị cơ bản, JOIN bảng "
            "này (theo tên sản phẩm) và nhân số lượng với he_so.\n"
        )

    # ----------------------- few-shot động -------------------------------

    @staticmethod
    def _ident_key(text: str) -> str:
        """Bỏ dấu + bỏ ký tự phân cách để so khớp tên cột kiểu 'ma_hoa_don'/'mahoadon'."""
        return re.sub(r"[^a-z0-9]", "", strip_diacritics(text or "").lower())

    def _find_col(
        self,
        table: TableMeta,
        needles: tuple[str, ...],
        kinds: tuple[str, ...] = (),
    ) -> ColumnMeta | None:
        for col in table.columns:
            if kinds and col.kind not in kinds:
                continue
            key = self._ident_key(col.name) + self._ident_key(col.original)
            if any(n in key for n in needles):
                return col
        return None

    def _status_condition(self, col: ColumnMeta | None, alias: str = "") -> str:
        """Điều kiện loại trạng thái huỷ/trả, dùng đúng chuỗi có trong DISTINCT."""
        if col is None:
            return ""
        bad = [str(v) for v in col.top_values or [] if str(v).strip() and self._is_bad_status(str(v))]
        if not bad:
            return ""
        prefix = f"{alias}." if alias else ""
        vals = ", ".join("'" + v.replace("'", "''") + "'" for v in bad)
        return f"{prefix}{col.name} NOT IN ({vals})"

    @staticmethod
    def _is_bad_status(value: str) -> bool:
        tokens = re.findall(r"[a-z0-9]+", strip_diacritics(value).lower())
        words = set(tokens)
        if words & {
            "cancel", "cancelled", "canceled", "return", "returned",
            "refund", "refunded", "void", "huy",
        }:
            return True
        if "tra" in words and ("hang" in words or "lai" in words or "hoan" in words):
            return True
        if "doi" in words and "tra" in words:
            return True
        compact = "".join(tokens)  # giữ đúng thứ tự chữ, không dùng set
        return any(
            k in compact
            for k in (
                "trahang", "tralai", "hoantra", "hoanhang", "doitra",
                "khonggiaoduoc", "khongnhanhang", "giaothatbai",
                "thatbai", "failed", "undeliver", "huygiao",
            )
        )

    def _status_shape(self, table: TableMeta) -> ColumnMeta | None:
        col = self._find_col(
            table, ("trangthai", "tinhtrang", "status", "ketqua", "tinhtrangdon")
        )
        if col:
            return col
        for c in table.columns:
            if c.kind != "text" or not c.top_values:
                continue
            if c.n_distinct and c.n_distinct > 20:
                continue
            if any(self._is_bad_status(str(v)) for v in c.top_values):
                return c
        return None

    @staticmethod
    def _month_label(first_day: str) -> str:
        try:
            return f"tháng {int(first_day[5:7])}/{first_day[:4]}"
        except (ValueError, IndexError):
            return "tháng 12"

    @staticmethod
    def _month_bounds(day: str) -> tuple[str, str]:
        try:
            d = date.fromisoformat(day)
        except ValueError:
            return day, day
        first = d.replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        return first.isoformat(), (nxt - timedelta(days=1)).isoformat()

    # -------------- nhận diện cấu trúc (schema-agnostic) ----------------
    #
    # KHÔNG dựa vào tên bảng cụ thể. Chỉ dựa vào:
    #   1. Vai trò của CỘT: mã (ma/id/code/sku...), tên (ten/name/mon/noi_dung...),
    #      ngày (kind=date), tiền (tong/thanh/so_tien...), hạn dùng (hsd/han_su_dung...).
    #   2. Quan hệ GIÁ TRỊ THẬT giữa các cột (FK phát hiện bằng giao tập giá trị,
    #      nên "ma_hang" vẫn nối được với "ma_sp" dù tên khác nhau).
    #   3. Từ vựng nghiệp vụ chung (bán / nhập / chi phí / khách / nhà cung cấp...).
    #
    # Kết quả cache trong self._shape_cache, self._fk_cache, self._val_cache.

    _MONEY_STRONG = (
        "thanhtien", "thanhtoan", "tongtien", "tongthanhtoan", "tongcong",
        "tongbill", "tonggiatri", "sotien", "sotienthu", "tienthu",
        "doanhthu", "doanhso", "trigia", "giatri", "amount", "total",
        "revenue", "tienhang", "tienban", "tienphai",
    )
    _MONEY_WEAK = (
        "gia", "price", "dongia", "giaban", "gianhap", "giavon",
        "giabanle", "gianiemyet", "cost", "unitprice",
    )
    _EXPIRY_HINTS = (
        "hansudung", "handung", "hsd", "hanthu", "hethan", "ngayhethan",
        "expiry", "expiredate", "expire", "bestbefore",
    )
    _CATEGORY_HINTS = (
        "danhmuc", "nhomhang", "loaihang", "loaihanghoa", "phanloai",
        "category", "nhom", "loaihang", "type",
    )
    _SALE_HINTS = (
        "banhang", "banle", "hoadon", "donhang", "order", "sale",
        "invoice", "receipt", "giaodich", "transaction", "pos",
    )
    _IMPORT_HINTS = ("nhap", "purchase", "import", "supplier", "nhacungcap")
    _EXPENSE_HINTS = ("chiphi", "expense", "salary", "luong")
    _PARTY_HINTS = (
        "khachhang", "khach", "customer", "nhanvien", "employee",
        "nhacungcap", "supplier", "vendor", "chinhanh", "branch",
        "cuahang", "store",
    )
    _DETAIL_HINTS = ("chitiet", "detail", "line")
    _PAYMENT_HINTS = ("thanhtoan", "payment", "phieuthu", "thutien", "pay")
    _PRODUCT_HINTS = (
        "sanpham", "hanghoa", "mathang", "danhmuc", "product", "item",
        "catalog", "danhsachhang",
    )

    @staticmethod
    def _hint(key: str, words: tuple[str, ...]) -> bool:
        return any(w in key for w in words)

    def _ident_pair(self, col: ColumnMeta) -> tuple[str, str]:
        return (
            self._ident_key(col.name),
            self._ident_key(col.original or ""),
        )

    def _name_quality(self, col: ColumnMeta) -> int:
        """0 = không phải cột tên hàng; 3 = tên rõ ràng; 2 = tên nghiệp vụ."""
        if col.kind != "text":
            return 0
        best = 0
        for k in self._ident_pair(col):
            if not k:
                continue
            # Tên người/tổ chức không phải tên hàng.
            if any(w in k for w in (
                "tenkh", "tennv", "tennhanvien", "tenncc", "tennhacungcap",
                "tencongty", "tencuahang", "customer", "employee", "supplier",
            )):
                return 0
            if k in {"ten", "name", "title"} or k.startswith("ten"):
                best = max(best, 3)
            elif k in {
                "mon", "product", "item", "mathang", "hanghoa", "sanpham",
                "thuoc", "dichvu", "service",
            }:
                best = max(best, 2)
            elif "noidung" in k or k.startswith("chitiet"):
                best = max(best, 2)
            elif k.startswith(("mota", "description", "label")):
                best = max(best, 1)
        return best

    def _code_quality(self, col: ColumnMeta) -> int:
        """0 = không phải cột mã; 3 = mã sản phẩm; 2 = mã chung; 1 = mã phụ (barcode...)."""
        if col.kind != "text":
            return 0
        best = 0
        for k in self._ident_pair(col):
            if not k:
                continue
            if "barcode" in k or "mavach" in k or k.endswith(("ean", "imei", "serial")):
                best = max(best, 1)
            elif k in {"id", "ma", "code", "sku"}:
                best = max(best, 3 if k == "sku" else 2)
            elif k.startswith(("sku", "masp", "mahang", "masanpham", "mamathang", "mahh")):
                best = max(best, 3)
            elif k.startswith((
                "ma", "id", "code", "key", "ref", "sophieu", "sohd", "sohoadon",
                "sodon", "sobill", "maphieu", "machungtu", "order", "invoice",
                "receipt", "trans", "magd", "magiaodich", "bien",
            )):
                best = max(best, 2)
            elif k.endswith(("id", "code", "sku")):
                best = max(best, 2)
            elif k.endswith("no") and len(k) <= 6:
                best = max(best, 1)
        return best

    def _money_strength(self, col: ColumnMeta) -> int:
        """0 = không phải tiền; 1 = đơn giá; 2 = tổng/thành tiền."""
        if col.kind != "number":
            return 0
        for k in self._ident_pair(col):
            if not k:
                continue
            if any(w in k for w in self._MONEY_STRONG):
                return 2
            if k.startswith(("tong", "total")) or k in {"tong", "total", "amount", "sum"}:
                return 2
        for k in self._ident_pair(col):
            if not k:
                continue
            # "gia" là tiền tố/hậu tố của ĐƠN GIÁ; tránh khớp nhầm "giao" (giao hàng).
            if k.startswith((
                "gia", "dongia", "giaban", "gianhap", "giavon", "giagoc",
                "giathitruong", "price", "cost", "unitprice",
            )) or k.endswith("gia"):
                return 1
            if any(w in k for w in ("tien", "money", "value")):
                return 2
        return 0

    def _money_col(self, table: TableMeta, min_strength: int = 1) -> ColumnMeta | None:
        best: tuple | None = None
        for col in table.columns:
            strength = self._money_strength(col)
            if strength < min_strength:
                continue
            k = self._ident_key(col.name)
            # Doanh thu BẮT BUỘC dùng cột tiền sau giảm (tong_sau_giam_gia / thanh_tien / tong_tien),
            # TUYỆT ĐỐI KHÔNG dùng tiền gốc (tong_tien_hang / *tien_hang).
            if "tienhang" in k:
                pref = -1  # cấm chọn nếu có cột nào khác
            elif "thanhtoan" in k or "phaitra" in k:
                pref = 3
            elif k.startswith(("tong", "total")) or k in {"tong", "total", "amount"}:
                pref = 2
            elif "thanh" in k:
                pref = 1
            else:
                pref = 0
            rank = (strength, pref)
            if best is None or rank > best[0]:
                best = (rank, col)
        return best[1] if best else None

    def _qty_col(self, table: TableMeta) -> ColumnMeta | None:
        for col in table.columns:
            if col.kind != "number":
                continue
            k = self._ident_key(col.name)
            if any(w in k for w in ("soluong", "sl", "quantity", "qty", "slg")):
                return col
        return None

    @staticmethod
    def _has_time(col: ColumnMeta) -> bool:
        values = [col.min_value, col.max_value, *(col.top_values or [])]
        return any(re.search(r"[ T]\d{1,2}:\d{2}", str(v or "")) for v in values)

    def _date_col(self, table: TableMeta) -> ColumnMeta | None:
        best: tuple | None = None
        for col in table.columns:
            if col.kind != "date":
                continue
            k = self._ident_key(col.name)
            score = 3
            if "ngay" in k:
                score += 1
            if not self._has_time(col):
                score += 2
            if col.min_value and col.max_value:
                score += 1
            if best is None or score > best[0]:
                best = (score, col)
        return best[1] if best else None

    def _expiry_col(self, table: TableMeta) -> ColumnMeta | None:
        for col in table.columns:
            for k in self._ident_pair(col):
                if k and any(w in k for w in self._EXPIRY_HINTS):
                    return col
        return None

    def _is_lot_col(self, col: ColumnMeta) -> bool:
        k = self._ident_key(col.name)
        return (
            k in {"lo", "malo", "solo", "lot", "lotid"}
            or k.startswith(("malo", "solo", "lotid"))
            or k.endswith(("lot", "batch"))
        )

    def _order_id_col(self, table: TableMeta) -> ColumnMeta | None:
        best: tuple | None = None
        for col in table.columns:
            if self._code_quality(col) == 0:
                continue
            k = self._ident_key(col.name)
            if any(w in k for w in (
                "khach", "customer", "nhanvien", "employee", "cuahang", "chinhanh",
                "store", "branch", "nhacungcap", "supplier", "sanpham", "hanghoa",
                "mathang", "product", "malo", "makh", "manv", "mancc",
            )):
                continue
            ratio = (col.n_distinct or 0) / max(table.row_count, 1)
            score = ratio * 10
            if any(w in k for w in (
                "hoadon", "donhang", "order", "bill", "invoice", "receipt",
                "phieu", "chungtu", "giaodich", "trans", "gd", "hd",
            )):
                score += 5
            if best is None or score > best[0]:
                best = (score, col)
        return best[1] if best else None

    def _customer_col(self, table: TableMeta) -> ColumnMeta | None:
        for col in table.columns:
            if self._code_quality(col) == 0:
                continue
            k = self._ident_key(col.name)
            if any(w in k for w in ("makhach", "makh", "customer", "khachhang")):
                return col
        return None

    # ----- FK phát hiện bằng tên HOẶC bằng giao tập giá trị thật ---------

    def _fk_overlap(
        self,
        child_table: str,
        child_col: str,
        parent_table: str,
        parent_col: str,
    ) -> bool:
        key = (child_table, child_col, parent_table, parent_col)
        cache = getattr(self, "_fk_cache", None)
        if cache is None:
            cache = self._fk_cache = {}
        if key in cache:
            return cache[key]
        conn = self._connect()
        try:
            sql = (
                f"SELECT COUNT(*) FROM ("
                f"SELECT DISTINCT {sql_quote(child_col)} AS v FROM {sql_quote(child_table)} "
                f"WHERE {sql_quote(child_col)} IS NOT NULL LIMIT 200"
                f") WHERE v IN (SELECT {sql_quote(parent_col)} "
                f"FROM {sql_quote(parent_table)})"
            )
            n = int(conn.execute(sql).fetchone()[0])
        except sqlite3.Error:
            n = 0
        finally:
            conn.close()
        cache[key] = n >= 2
        return cache[key]

    def _fk_col(
        self,
        child: TableMeta,
        parent: TableMeta,
        parent_col: ColumnMeta,
    ) -> ColumnMeta | None:
        pkey = self._ident_key(parent_col.name)
        candidates = [c for c in child.columns if c.kind == "text"]
        # 1) khớp tên chuẩn hoá (ma_hang ~ mahanghoa)
        for col in candidates:
            ckey = self._ident_key(col.name)
            if ckey == pkey:
                return col
            if (
                len(ckey) >= 5 and len(pkey) >= 5
                and (ckey.startswith(pkey) or pkey.startswith(ckey))
            ):
                return col
        # 2) khớp giá trị thật
        for col in candidates:
            if self._fk_overlap(child.name, col.name, parent.name, parent_col.name):
                return col
        return None

    # ------------------------- các "shape" ------------------------------

    def _order_table(self) -> tuple | None:
        """Bảng đơn hàng chính: có cột NGÀY + cột TIỀN (ưu tiên có mã đơn & trạng thái)."""
        cache = getattr(self, "_shape_cache", None)
        if cache is None:
            cache = self._shape_cache = {}
        if "order" in cache:
            return cache["order"]

        best: tuple | None = None
        for table in self.tables:
            if table.name == "_quy_doi":
                continue
            dcol = self._date_col(table)
            if dcol is None:
                continue
            mcol = self._money_col(table)
            if mcol is None:
                continue
            mstr = self._money_strength(mcol)
            scol = self._status_shape(table)
            oid = self._order_id_col(table)
            cust = self._customer_col(table)
            tkey = self._ident_key(table.name)
            score = 4 + 2 * mstr
            score += 3 if scol else 0
            score += 3 if oid else 0
            score += 1 if cust else 0
            score += 2 if self._hint(tkey, self._SALE_HINTS) else 0
            score -= 3 if self._hint(tkey, self._IMPORT_HINTS) else 0
            score -= 3 if self._hint(tkey, self._EXPENSE_HINTS) else 0
            score += 1 if table.row_count >= 100 else 0
            if best is None or score > best[0]:
                best = (score, table, oid, dcol, scol, mcol)
        cache["order"] = best
        return best

    def _catalog_table(self, order: tuple | None) -> tuple | None:
        """Bảng danh mục: có cột MÃ + cột TÊN, không phải bảng giao dịch có ngày."""
        cache = getattr(self, "_shape_cache", None)
        if cache is None:
            cache = self._shape_cache = {}
        if "catalog" in cache:
            return cache["catalog"]

        best: tuple | None = None
        for table in self.tables:
            if table.name == "_quy_doi":
                continue
            if order and table.name == order[1].name:
                continue
            # Bảng giao dịch thật (có cột tiền tổng) thì không phải danh mục.
            if self._money_col(table, min_strength=2) is not None:
                continue
            # Cột ngày chỉ được là ngày TẠO/CẬP NHẬT; ngày giao dịch → không phải danh mục.
            dcol = self._date_col(table)
            if dcol is not None:
                dkey = self._ident_key(dcol.name)
                if not any(w in dkey for w in (
                    "tao", "taoluc", "created", "updated", "modified", "ngayudpate",
                )):
                    continue
            names = [c for c in table.columns if self._name_quality(c) > 0]
            codes = [c for c in table.columns if self._code_quality(c) > 0]
            if not names or not codes:
                continue
            tkey = self._ident_key(table.name)
            if self._hint(tkey, self._PARTY_HINTS):
                continue
            name_col = max(names, key=self._name_quality)
            code_col = max(codes, key=self._code_quality)
            # Bảng con của đơn (mã của nó trùng mã đơn) không phải danh mục.
            if order and order[2] is not None and self._fk_overlap(
                table.name, code_col.name, order[1].name, order[2].name
            ):
                continue
            cat_col = next(
                (c for c in table.columns if c.kind == "text"
                 and self._hint(self._ident_key(c.name), self._CATEGORY_HINTS)),
                None,
            )
            unit_col = next(
                (c for c in table.columns
                 if self._hint(self._ident_key(c.name), ("donvi", "dvt", "unit", "uom", "dvi"))),
                None,
            )
            price_col = next(
                (c for c in table.columns if self._money_strength(c) >= 1), None
            )
            score = 3 * self._name_quality(name_col) + 2 * self._code_quality(code_col)
            score += 2 if cat_col else 0
            score += 1 if unit_col else 0
            score += 1 if price_col else 0
            score += 2 if self._hint(tkey, self._PRODUCT_HINTS) else 0
            if best is None or score > best[0]:
                best = (score, table, name_col, code_col, cat_col)
        cache["catalog"] = best
        return best

    def _detail_table(
        self,
        order: tuple | None,
        catalog: tuple | None,
    ) -> tuple | None:
        """Bảng chi tiết: có FK tới mã đơn + (tiền hoặc số lượng) + FK tới mã sản phẩm."""
        cache = getattr(self, "_shape_cache", None)
        if cache is None:
            cache = self._shape_cache = {}
        if "detail" in cache:
            return cache["detail"]
        if not order:
            cache["detail"] = None
            return None

        _, otable, oid, _dcol, _scol, _omoney = order
        best: tuple | None = None
        for table in self.tables:
            if table.name in (otable.name, "_quy_doi"):
                continue
            if catalog and table.name == catalog[1].name:
                continue
            money = self._money_col(table)
            if money is None:
                # Bảng chi tiết phải có tiền; bảng chỉ có số lượng (giao hàng/tồn kho)
                # không dùng để tính doanh thu.
                continue
            qty = self._qty_col(table)
            ofk = self._fk_col(table, otable, oid) if oid else None
            if ofk is None:
                continue
            pfk = self._fk_col(table, catalog[1], catalog[3]) if catalog else None
            tkey = self._ident_key(table.name)
            has_name = any(self._name_quality(c) > 0 for c in table.columns)
            detail_hint = self._hint(tkey, self._DETAIL_HINTS)
            if pfk is None and not has_name and not detail_hint:
                continue
            score = 4 + (2 if money else 0) + (2 if pfk else 0)
            score += 2 if detail_hint else 0
            score += 1 if qty else 0
            if best is None or score > best[0]:
                best = (score, table, money or qty, ofk, pfk)
        cache["detail"] = best[1:] if best else None
        return cache["detail"]

    def _lot_table(self, catalog: tuple | None) -> tuple | None:
        """Bảng lô/tồn kho: có cột hạn dùng, nối được tới mã sản phẩm."""
        cache = getattr(self, "_shape_cache", None)
        if cache is None:
            cache = self._shape_cache = {}
        if "lot" in cache:
            return cache["lot"]

        best: tuple | None = None
        for table in self.tables:
            if table.name == "_quy_doi":
                continue
            exp = self._expiry_col(table)
            if exp is None:
                continue
            pfk = self._fk_col(table, catalog[1], catalog[3]) if catalog else None
            pname = next((c for c in table.columns if self._name_quality(c) > 0), None)
            if pfk is None and pname is None:
                continue
            tkey = self._ident_key(table.name)
            score = 5 + (2 if pfk else 0)
            score += 2 if any(self._is_lot_col(c) for c in table.columns) else 0
            score += 2 if self._hint(tkey, ("lohang", "tonkho", "lot", "stock", "inventory", "batch")) else 0
            if best is None or score > best[0]:
                best = (score, table, exp, pfk, pname)
        cache["lot"] = best[1:] if best else None
        return cache["lot"]

    def _payment_table(
        self,
        order: tuple | None,
        detail: tuple | None = None,
        catalog: tuple | None = None,
        lot: tuple | None = None,
    ) -> tuple | None:
        """Bảng thanh toán/thu tiền: có tiền + FK tới mã đơn, không phải bảng trả hàng."""
        cache = getattr(self, "_shape_cache", None)
        if cache is None:
            cache = self._shape_cache = {}
        if "payment" in cache:
            return cache["payment"]
        if not order:
            cache["payment"] = None
            return None

        skip_names = {"_quy_doi", order[1].name}
        for item in (detail, lot):
            if item:
                skip_names.add(item[0].name)
        if catalog:
            skip_names.add(catalog[1].name)

        _, otable, oid, _dcol, _scol, _omoney = order
        best: tuple | None = None
        for table in self.tables:
            if table.name in skip_names:
                continue
            money = self._money_col(table)
            if money is None:
                continue
            fk = self._fk_col(table, otable, oid) if oid else None
            if fk is None:
                continue
            tkey = self._ident_key(table.name)
            if self._hint(tkey, ("trahang", "return", "refund", "chiphi", "expense")):
                continue
            has_date = self._date_col(table) is not None
            is_payment_hint = self._hint(tkey, self._PAYMENT_HINTS)
            # Bảng thanh toán thật thường có ngày thu HOẶC tên gợi ý thu tiền.
            if not has_date and not is_payment_hint:
                continue
            score = 5 + (2 if is_payment_hint else 0) + (1 if has_date else 0)
            if best is None or score > best[0]:
                best = (score, table, money, fk)
        cache["payment"] = best[1:] if best else None
        return cache["payment"]

    def _structural_tables(self) -> list[TableMeta]:
        """Các bảng cấu trúc quan trọng, luôn giữ trong schema linking."""
        order = self._order_table()
        catalog = self._catalog_table(order)
        detail = self._detail_table(order, catalog)
        lot = self._lot_table(catalog)
        payment = self._payment_table(order, detail, catalog, lot)
        result: list[TableMeta] = []
        for table in (
            order[1] if order else None,
            catalog[1] if catalog else None,
            detail[0] if detail else None,
            lot[0] if lot else None,
            payment[0] if payment else None,
        ):
            if table is None:
                continue
            if all(t.name != table.name for t in result):
                result.append(table)
        return result

    # ------------------------- tiện ích ngày -----------------------------

    def _latest_day(self, col: ColumnMeta) -> str:
        for raw in (col.max_value, *(col.top_values or [])):
            m = re.match(r"(\d{4}-\d{2}-\d{2})", str(raw or ""))
            if m:
                return m.group(1)
        return "2025-12-31"

    def _day_filter(self, col: ColumnMeta, day: str, alias: str = "") -> str:
        prefix = f"{alias}." if alias else ""
        if self._has_time(col):
            return f"{prefix}{col.name} LIKE '{day}%'"
        return f"{prefix}{col.name} = '{day}'"

    def _month_range(self, day: str) -> tuple[str, str]:
        first, last = self._month_bounds(day)
        try:
            nxt = (date.fromisoformat(last) + timedelta(days=1)).isoformat()
        except ValueError:
            nxt = last
        return first, nxt

    @staticmethod
    def _month_filter(col: ColumnMeta, first: str, nxt: str, alias: str = "") -> str:
        prefix = f"{alias}." if alias else ""
        name = f"{prefix}{col.name}"
        return f"{name} >= '{first}' AND {name} < '{nxt}'"

    # ------------------------- few-shot động -----------------------------

    def _debt_example(
        self,
        order: tuple | None,
        payment: tuple | None = None,
    ) -> str | None:
        """Công nợ = tổng đã mua - tổng đã trả (KHÔNG đếm khách)."""
        strong = {"conno", "conphaithu", "nohientai", "congno", "duno", "nokhachhang"}
        order_table_name = order[1].name if order else None
        for table in self.tables:
            for col in table.columns:
                if col.kind != "number":
                    continue
                if self._ident_key(col.name) in strong:
                    return (
                        '- Hỏi: "Tổng công nợ khách còn nợ"\n'
                        f"  SQL: SELECT SUM({col.name}) FROM {table.name} "
                        f"WHERE {col.name} > 0\n  Type: number"
                    )
        soft = {"chuatra", "conlai", "no"}
        for table in self.tables:
            if table.name != order_table_name and self._customer_col(table) is None:
                continue
            for col in table.columns:
                if col.kind != "number":
                    continue
                if self._ident_key(col.name) in soft:
                    return (
                        '- Hỏi: "Tổng công nợ khách còn nợ"\n'
                        f"  SQL: SELECT SUM({col.name}) FROM {table.name} "
                        f"WHERE {col.name} > 0\n  Type: number"
                    )
        if order and payment:
            _, otable, oid, _d, scol, mcol = order
            ptable, pmoney, pfk = payment
            if mcol and oid and pfk:
                status = self._status_condition(scol)
                buy = f"SELECT SUM({mcol.name}) FROM {otable.name}"
                if status:
                    buy += f" WHERE {status}"
                return (
                    '- Hỏi: "Khách còn nợ bao nhiêu" (nợ = tổng đã mua - tổng đã trả)\n'
                    f"  SQL: SELECT ({buy}) - "
                    f"(SELECT SUM({pmoney.name}) FROM {ptable.name})\n  Type: number"
                )
        return None

    def _build_few_shot(self) -> str:
        """Sinh ví dụ mẫu bằng ĐÚNG tên bảng/cột mà DB này thực sự có."""
        cached = getattr(self, "_few_shot_cache", None)
        if cached is not None:
            return cached

        order = self._order_table()
        catalog = self._catalog_table(order)
        detail = self._detail_table(order, catalog)
        lot = self._lot_table(catalog)
        payment = self._payment_table(order, detail, catalog, lot)

        examples: list[str] = []

        def add(label: str, sql: str, kind: str = "") -> None:
            suffix = f"\n  Type: {kind}" if kind else ""
            examples.append(f'- Hỏi: "{label}"\n  SQL: {sql}{suffix}')

        if order:
            _, otable, oid, dcol, scol, mcol = order
            day = self._latest_day(dcol)
            status = self._status_condition(scol)

            # 1) Đếm đơn trong 1 ngày -> number (COUNT DISTINCT, không COUNT(*)).
            if oid:
                sql = f"SELECT COUNT(DISTINCT {oid.name}) FROM {otable.name}"
            else:
                sql = f"SELECT COUNT(*) FROM {otable.name}"
            wheres = [w for w in (self._day_filter(dcol, day), status) if w]
            if wheres:
                sql += " WHERE " + " AND ".join(wheres)
            add(f"Số đơn hàng ngày {day} (hôm qua)", sql, "number")

            # 1b) Tỷ lệ đơn hủy/trả = số đơn xấu / tổng đơn * 100 -> percent.
            if scol:
                bad = [
                    str(v) for v in (scol.top_values or [])
                    if str(v).strip() and self._is_bad_status(str(v))
                ]
                if bad:
                    vals = ", ".join("'" + v.replace("'", "''") + "'" for v in bad)
                    if oid:
                        numer = (
                            f"COUNT(DISTINCT CASE WHEN {scol.name} IN ({vals}) "
                            f"THEN {oid.name} END)"
                        )
                        denom = f"COUNT(DISTINCT {oid.name})"
                    else:
                        numer = (
                            f"SUM(CASE WHEN {scol.name} IN ({vals}) THEN 1 ELSE 0 END)"
                        )
                        denom = "COUNT(*)"
                    add(
                        "Tỷ lệ đơn hủy/trả (%)",
                        f"SELECT CAST({numer} AS FLOAT) / NULLIF({denom}, 0) * 100 "
                        f"FROM {otable.name}",
                        "percent",
                    )

            # 2) Công nợ.
            debt = self._debt_example(order, payment)
            if debt:
                examples.append(debt)

            first, nxt = self._month_range(day)
            label = self._month_label(first)
            month_where = self._month_filter(dcol, first, nxt)
            month_scope = " AND ".join(w for w in (month_where, status) if w)

            # 3) Doanh thu tháng.
            if mcol:
                add(
                    f"Doanh thu {label}",
                    f"SELECT SUM({mcol.name}) FROM {otable.name} WHERE {month_scope}",
                    "number",
                )

            # 4) Nếu có bảng chi tiết + danh mục: dạy JOIN chuẩn.
            if detail and catalog and oid:
                dtable, dmoney, ofk, pfk = detail
                _, ptable, pname, pcode, _pcat = catalog
                base = (
                    f"FROM {dtable.name} AS ct "
                    f"JOIN {otable.name} AS dh ON ct.{ofk.name} = dh.{oid.name}"
                )
                join_where = " AND ".join(
                    w for w in (
                        self._month_filter(dcol, first, nxt, "dh"),
                        self._status_condition(scol, "dh"),
                    ) if w
                )
                if pfk and dmoney:
                    base += f" JOIN {ptable.name} AS sp ON ct.{pfk.name} = sp.{pcode.name}"
                    add(
                        f"Top 5 mặt hàng bán chạy {label}",
                        f"SELECT sp.{pname.name}, SUM(ct.{dmoney.name}) AS doanh_thu {base} "
                        f"WHERE {join_where} GROUP BY sp.{pname.name} "
                        "ORDER BY doanh_thu DESC LIMIT 5",
                        "list",
                    )
                    add(
                        f"Mặt hàng bán chạy nhất {label}",
                        f"SELECT sp.{pname.name} {base} WHERE {join_where} "
                        f"GROUP BY sp.{pname.name} "
                        f"ORDER BY SUM(ct.{dmoney.name}) DESC LIMIT 1",
                        "string",
                    )
                    add(
                        f"Doanh thu của <tên hàng> {label}",
                        f"SELECT SUM(ct.{dmoney.name}) {base} "
                        f"WHERE sp.{pname.name} LIKE '%tên hàng%' AND {join_where}",
                        "number",
                    )
                elif dmoney:
                    add(
                        f"Doanh thu {label} (từ chi tiết đơn)",
                        f"SELECT SUM(ct.{dmoney.name}) {base} WHERE {join_where}",
                        "number",
                    )
            elif not detail:
                # 5) Bảng phẳng: tên hàng nằm ngay trên bảng chính.
                name_on_order = next(
                    (c for c in otable.columns if self._name_quality(c) > 0), None
                )
                if name_on_order and mcol:
                    add(
                        f"Doanh thu của <tên hàng> {label}",
                        f"SELECT SUM({mcol.name}) FROM {otable.name} "
                        f"WHERE {name_on_order.name} LIKE '%tên hàng%' AND {month_scope}",
                        "number",
                    )

            # 6) Doanh thu theo từng tháng -> list.
            # Bảng chi tiết: join hóa đơn chỉ để lấy ngày/trạng thái (many-to-one, không nhân dòng).
            if detail and oid:
                dtable, dmoney, ofk, _pfk = detail
                if dmoney:
                    monthly = (
                        f"SELECT strftime('%Y-%m', dh.{dcol.name}) AS thang, "
                        f"SUM(ct.{dmoney.name}) FROM {dtable.name} AS ct "
                        f"JOIN {otable.name} AS dh ON ct.{ofk.name} = dh.{oid.name}"
                    )
                    dstatus = self._status_condition(scol, "dh")
                    if dstatus:
                        monthly += f" WHERE {dstatus}"
                    monthly += " GROUP BY thang ORDER BY thang"
                    add("Doanh thu từng tháng", monthly, "list")
            elif not detail and mcol:
                add(
                    "Doanh thu từng tháng",
                    f"SELECT strftime('%Y-%m', {dcol.name}) AS thang, SUM({mcol.name}) "
                    f"FROM {otable.name}"
                    + (f" WHERE {status}" if status else "")
                    + " GROUP BY thang ORDER BY thang",
                    "list",
                )

        # 7) Lô/tồn kho + danh mục: tên hàng phải JOIN qua mã.
        if lot:
            ltable, exp, pfk, pname = lot
            if catalog and pfk:
                _, ptable, pname_c, pcode, _ = catalog
                add(
                    "Mặt hàng X gần hết hạn nhất (hạn dùng ngày nào)",
                    f"SELECT lh.{exp.name} FROM {ltable.name} AS lh "
                    f"JOIN {ptable.name} AS sp ON lh.{pfk.name} = sp.{pcode.name} "
                    f"WHERE sp.{pname_c.name} LIKE '%tên hàng%' "
                    f"ORDER BY lh.{exp.name} ASC LIMIT 1",
                    "date",
                )
            elif pname:
                add(
                    "Mặt hàng X gần hết hạn nhất (hạn dùng ngày nào)",
                    f"SELECT {exp.name} FROM {ltable.name} "
                    f"WHERE {pname.name} LIKE '%tên hàng%' "
                    f"ORDER BY {exp.name} ASC LIMIT 1",
                    "date",
                )

        if examples:
            text = (
                "VÍ DỤ MẪU (FEW-SHOT — dùng đúng tên bảng/cột của cửa hàng này):\n"
                + "\n".join(examples)
                + "\nLưu ý bắt buộc: (1) Cột ngày tháng CHỈ ở bảng hóa đơn/nhập hàng, KHÔNG ở bảng "
                  "chi tiết; khi lọc ngày trên chi tiết phải JOIN với bảng hóa đơn và dùng cột ngày của "
                  "bảng hóa đơn. (2) Doanh thu BẮT BUỘC dùng cột tiền sau giảm (tong_sau_giam_gia / "
                  "thanh_tien / tong_tien), TUYỆT ĐỐI KHÔNG dùng tong_tien_hang. Tên bảng/cột lấy đúng "
                  "từ DATABASE bên trên; với câu hỏi khác, thay bảng/cột tương ứng. "
                  "(type: ...) là kiểu trả về bắt buộc cho đúng dạng câu hỏi đó.\n"
            )
        else:
            text = _STATIC_FEW_SHOT
        self._few_shot_cache = text
        return text

    # --------------------------- rulebook --------------------------------

    def _split_rulebook(self) -> dict[str, str]:
        if not self.rulebook_text.strip():
            return {}
        sections: dict[str, str] = {}
        current = "0"
        buffer: list[str] = []
        for line in self.rulebook_text.splitlines():
            m = re.match(r"^##\s+(\d+)\.", line)
            if m:
                sections[current] = "\n".join(buffer).strip()
                current, buffer = m.group(1), [line]
            else:
                buffer.append(line)
        sections[current] = "\n".join(buffer).strip()
        sections.pop("0", None)
        return sections

    def _select_rulebook(self, question: str) -> str:
        if self.full_rulebook or not self._rulebook_sections:
            return self.rulebook_text
        q = question.lower()
        wanted = list(ALWAYS_SECTIONS)
        for key, kws in SECTION_KEYWORDS.items():
            if any(k in q for k in kws):
                wanted.append(key)
        chunks = []
        for key in dict.fromkeys(wanted):
            if key in self._rulebook_sections:
                chunks.append(self._rulebook_sections[key])
        return "\n\n".join(chunks)

    @staticmethod
    def _drop_rulebook_section(text: str, key: str) -> str:
        """Bỏ hẳn mục `## key.` khỏi chuỗi rulebook đã chọn."""
        if not text:
            return text
        out: list[str] = []
        skipping = False
        for line in text.splitlines():
            m = re.match(r"^##\s+(\d+)\.", line)
            if m:
                skipping = m.group(1) == key
            if not skipping:
                out.append(line)
        return "\n".join(out).strip()

    # --------------------------- prompt ----------------------------------

    def _instructions(self) -> str:
        return (
            "Bạn là chuyên gia SQL cho SQLite. Đọc câu hỏi tiếng Việt, viết ĐÚNG MỘT câu SELECT.\n"
            "QUY TẮC SINH TỬ (vi phạm = sai đáp án):\n"
            "1) KHÔNG BỎ CUỘC & KHÔNG BỊA TÊN CỘT: LUÔN thử SELECT trước; không chắc tên → LIKE "
            "'%tên%' trên cột TEXT (ten/name/mon/noi_dung/mo_ta...); không chắc kỳ → quét cả tháng. "
            "Chỉ UNANSWERABLE khi SQL ra 0 dòng hoặc schema KHÔNG có cột chứa thông tin được hỏi "
            "(vd hỏi 'lợi nhuận' mà không có cột giá vốn).\n"
            "2) NGÀY: cột ngày tháng (ngay_ban, thoi_gian, ...) CHỈ NẰM Ở BẢNG HÓA ĐƠN (header) "
            "hoặc BẢNG NHẬP HÀNG; KHÔNG được tìm cột ngày trong bảng CHI TIẾT "
            "(danhsachhoadonchitiet, banhang_chitiethoadon...). Khi lọc theo ngày trên dữ liệu chi "
            "tiết, BẮT BUỘC phải JOIN bảng chi tiết với bảng hóa đơn và dùng cột ngày của bảng hóa đơn. "
            "Cột ngày có thể kèm giờ → so 1 ngày dùng LIKE 'YYYY-MM-DD%'; lọc tháng dùng "
            ">= 'YYYY-MM-01' AND < ngày 1 tháng sau; tháng M = ngày 1..cuối tháng. KHÔNG dùng "
            "= 'YYYY-MM-DD' hay <= 'YYYY-MM-DD' vì sẽ mất dữ liệu trong ngày.\n"
            "3) CÔNG NỢ: nợ = tổng đã mua - tổng đã trả; có bảng thu tiền → SUM(số tiền thu), "
            "không → SUM cột còn nợ. CẤM chỉ COUNT(DISTINCT khách).\n"
            "4) TÊN HÀNG: bảng lô/tồn kho thường chỉ có MÃ (cột lo/ma_lô chứa mã lô, KHÔNG chứa "
            "tên hàng); KHÔNG được LIKE tên hàng trên cột lo. Phải JOIN bảng danh mục (có mã + tên) "
            "qua mã sản phẩm, rồi LIKE trên cột tên của bảng danh mục. Không có bảng danh mục thì "
            "LIKE trên cột tên ngay trong bảng đang dùng.\n"
            "5) ĐẾM/SỐ LƯỢNG = type number: đếm đơn → COUNT(DISTINCT mã đơn); hỏi 'bao nhiêu "
            "viên/chai/hộp/lon/ly/cái/kg/gói' → SUM(cột số lượng) trên bảng chi tiết, KHÔNG SUM "
            "tiền; CẤM COUNT(*) nếu mỗi đơn có nhiều dòng; SUM số lượng: GROUP BY mã đơn + mã "
            "sản phẩm (lấy MAX) trước khi cộng để tránh đếm trùng dòng cùng đơn.\n"
            "6) DOANH THU: khi hỏi 'bán được bao nhiêu tiền' / doanh thu, BẮT BUỘC dùng cột tiền "
            "sau giảm giá: tong_sau_giam_gia, thanh_tien, hoặc tong_tien. TUYỆT ĐỐI KHÔNG dùng "
            "cột tong_tien_hang (tiền gốc). Nếu SUM trên bảng hóa đơn khi có JOIN thì bọc "
            "subquery/GROUP BY để không nhân bản dòng. KHÔNG SUM đơn giá. Hỏi 'lời/lãi/lợi nhuận' "
            "→ SUM((đơn giá - giá vốn) * số lượng) nếu có cột giá vốn, không có thì UNANSWERABLE.\n"
            "7) TRẠNG THÁI: chỉ lấy hoàn thành/xong/completed/đang giao/shipping; LOẠI hủy/"
            "cancelled/returned/đổi trả/trả hàng; dùng đúng chuỗi có trong DISTINCT.\n"
            "8) TYPE: đếm → number; %/tỷ lệ → (A / B) * 100.0, type percent; 'những gì'/'danh sách'/"
            "top k/'từng tháng' (nhiều giá trị cùng loại) → list (mảng 1 chiều, CẤM kèm tên cột); "
            "'ngày nào' → date 'YYYY-MM-DD'; hỏi 1 đối tượng (ai/tên gì/mặt hàng nào) → string; "
            "table CHỈ khi hỏi rõ ≥2 thông tin khác loại (vd 'tên VÀ số lượng').\n"
            "9) HẠN SỬ DỤNG: tìm bảng có cột hạn dùng (hansudung/hsd/expiry_date); nếu bảng đó "
            "chỉ có mã sản phẩm thì JOIN bảng danh mục để lọc tên; trả type date.\n"
            'Chỉ trả JSON: {"sql": "SELECT ...", "type": "number|percent|date|string|boolean|list|table", "reason": "..."} '
            'hoặc {"sql": "UNANSWERABLE", "type": "unanswerable", "reason": "..."}.\n'
        )

    def _build_system_prompt(self, question: str, level: int = 0) -> str:
        schema = self._build_schema_text(
            level, tables=self._filter_relevant_tables(question)
        )

        parts: list[str] = [self._instructions()]

        readme = self.readme_text.strip()
        if readme:
            limit = {0: 6000, 1: 2500, 2: 1500, 3: 800}.get(level, 800)
            if len(readme) > limit:
                readme = readme[:limit] + "\n...(README bị lược bớt)"
            parts.append(
                "\n===================== MÔ TẢ NGHIỆP VỤ (README CỦA CỬA HÀNG) =====================\n"
                "Đây là mô tả nghiệp vụ của cửa hàng này:\n"
                f"{readme}\n"
                "Hãy dựa vào đây để hiểu ý nghĩa các bảng và cột.\n"
            )

        info = []
        span = self._data_span()
        if span:
            info.append(
                f"KHOẢNG NGÀY CÓ DỮ LIỆU: {span[0]} → {span[1]}. "
                "Kỳ nằm hoàn toàn trong khoảng này thì PHẢI tính, không được "
                "trả UNANSWERABLE."
            )
        if self.meta_info.get("data_end_date"):
            info.append(f"Dữ liệu có đến hết: {self.meta_info['data_end_date']}.")
        quydoi = self._build_quydoi_note()
        if quydoi:
            info.append(quydoi.rstrip())
        info_block = ("\n" + "\n".join(info) + "\n") if info else ""

        parts.append(
            "\n===================== DATABASE (SQLite) =====================\n"
            f"{schema}\n{info_block}"
        )

        if level <= 1:
            rules = self._select_rulebook(question)
            if level == 1 and self._rulebook_sections:
                # Mục 9 (định dạng đáp án) đã được code xử lý -> bỏ để tiết kiệm token.
                rules = self._drop_rulebook_section(rules, "9")
            if rules:
                parts.append(
                    "\n===================== RULEBOOK CHUNG =====================\n"
                    f"{rules}\n"
                )
        elif level == 2:
            # Chỉ giữ các mục cốt lõi: doanh thu, thời gian, không trả lời được, cách ghi file.
            chunks = [
                self._rulebook_sections[k]
                for k in ("1", "2", "7", "8")
                if k in self._rulebook_sections
            ]
            if chunks:
                parts.append(
                    "\n===================== RULEBOOK CHUNG =====================\n"
                    + "\n\n".join(chunks)
                    + "\n"
                )

        few = self._build_few_shot()
        if few:
            parts.append("\n" + few)

        parts.append(
            "\n===================== ĐỊNH DẠNG TRẢ VỀ =====================\n"
            "Chỉ trả về MỘT JSON object, không giải thích ngoài JSON:\n"
            '{"sql": "SELECT ...", "type": "number|percent|date|string|boolean|list|table", '
            '"reason": "..."}\n'
            "Nếu dữ liệu không đủ để trả lời (kỳ ngoài khoảng dữ liệu, shop không ghi "
            'thông tin đó): {"sql": "UNANSWERABLE", "type": "unanswerable", "reason": "..."}\n'
        )
        return "".join(parts)

    def _compose_with_budget(self, question: str) -> tuple[str, int]:
        """Chọn mức chi tiết schema cao nhất còn nằm trong ngân sách token."""
        budget_chars = int(self.prompt_budget * CHARS_PER_TOKEN)
        prompt = ""
        for level in (0, 1, 2, 3):
            prompt = self._build_system_prompt(question, level=level)
            if len(prompt) <= budget_chars:
                return prompt, int(len(prompt) / CHARS_PER_TOKEN)
        return prompt, int(len(prompt) / CHARS_PER_TOKEN)

    # --------------------------- ngày tháng ------------------------------

    def build_date_context(self, as_of_date: str | None) -> str:
        d = self._parse_as_of(as_of_date)
        if d is None:
            return ("Không có as_of_date — nếu câu hỏi dùng thời gian tương đối "
                    "hãy trả UNANSWERABLE.")

        def rng(a: date, b: date) -> str:
            return f"{a.isoformat()} → {b.isoformat()}"

        week_start = d - timedelta(days=d.weekday())
        last_week_start = week_start - timedelta(days=7)
        last_week_end = week_start - timedelta(days=1)
        month_start = d.replace(day=1)
        prev_month_end = month_start - timedelta(days=1)
        prev_month_start = prev_month_end.replace(day=1)
        same_day_prev_month = min(d.day, prev_month_end.day)
        prev_month_same = prev_month_start.replace(day=same_day_prev_month)

        q = (d.month - 1) // 3 + 1
        q_start = date(d.year, 3 * (q - 1) + 1, 1)
        q_end = date(d.year + (q == 4), 3 * q % 12 + 1, 1) - timedelta(days=1)
        pq = q - 1 or 4
        pq_year = d.year if q > 1 else d.year - 1
        pq_start = date(pq_year, 3 * (pq - 1) + 1, 1)
        pq_end = q_start - timedelta(days=1)

        lines = [
            f"- Hôm nay = {d.isoformat()} ({VI_WEEKDAYS[d.weekday()]}); "
            f"hôm qua = {(d - timedelta(days=1)).isoformat()}.",
            f"- Tuần này (thứ Hai → hôm nay): {rng(week_start, d)}.",
            f"- Tuần trước / tuần rồi (cả tuần): {rng(last_week_start, last_week_end)}.",
            f"- Tháng này (1 → hôm nay): {rng(month_start, d)}.",
            f"- Tháng trước (cả tháng): {rng(prev_month_start, prev_month_end)}.",
            f"- Tháng trước cùng số ngày (1 → ngày {same_day_prev_month}): "
            f"{rng(prev_month_start, prev_month_same)}.",
            f"- Quý này: {rng(q_start, q_end)}. Quý trước: {rng(pq_start, pq_end)}.",
            f"- Từ đầu năm đến nay: {rng(date(d.year, 1, 1), d)}.",
            f"- 7 ngày gần nhất: {rng(d - timedelta(days=6), d)}. "
            f"30 ngày gần nhất: {rng(d - timedelta(days=29), d)}.",
            f"- 'N ngày tới' = từ {d.isoformat()} cộng thêm N ngày.",
            "- Quý Q của năm Y = ngày 1 tháng (3Q-2) đến hết tháng 3Q "
            "(Q1=1-3, Q2=4-6, Q3=7-9, Q4=10-12).",
        ]
        tet = self._tet_context(d)
        if tet:
            lines.append("- " + tet)
        lines.append(
            "LƯU Ý: cột ngày là chuỗi 'YYYY-MM-DD'; so sánh trực tiếp. "
            "Không dùng CURRENT_DATE/date('now')."
        )
        return "\n".join(lines)

    @staticmethod
    def _parse_as_of(value: str | None) -> date | None:
        if not value:
            return None
        s = str(value).strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(s[:10], fmt).date()
            except ValueError:
                continue
        return None

    def _tet_context(self, d: date) -> str:
        if not self.calendar_path.is_file():
            return ""
        year = d.year
        if year not in self._tet_cache:
            try:
                self._tet_cache[year] = self._compute_tet(year)
            except Exception:  # noqa: BLE001
                self._tet_cache[year] = None
        pair = self._tet_cache[year]
        return f"Tết {year}: {pair[0]} → {pair[1]}." if pair else ""

    def _compute_tet(self, year: int) -> tuple[str, str] | None:
        import csv

        raw_rows: list[dict[str, str]] = []
        for enc in ("utf-8-sig", "utf-8", "cp1258", "latin-1"):
            try:
                with open(self.calendar_path, "r", encoding=enc, newline="") as fh:
                    raw_rows = [dict(r) for r in csv.DictReader(fh)]
                break
            except (OSError, UnicodeDecodeError, csv.Error):
                continue
        if not raw_rows:
            return None

        keys = list(raw_rows[0].keys())
        solar_key = day_key = month_key = lunar_year_key = None
        for k in keys:
            s = slug(k)
            if solar_key is None and (("duong" in s) or s in ("date", "solar", "solardate")
                                      or ("date" in s and "am" not in s)):
                solar_key = k
            if day_key is None and "ngay" in s and ("am" in s or "lunar" in s):
                day_key = k
            if month_key is None and "thang" in s and ("am" in s or "lunar" in s):
                month_key = k
            if lunar_year_key is None and "nam" in s and ("am" in s or "lunar" in s):
                lunar_year_key = k
        if not (solar_key and day_key and month_key):
            return None

        def solar_date(v: str) -> str | None:
            s = str(v).strip()
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
                try:
                    return datetime.strptime(s[:10], fmt).date().isoformat()
                except ValueError:
                    continue
            return None

        start = end = None
        for row in raw_rows:
            try:
                day = int(float(str(row[day_key]).strip()))
                month = int(float(str(row[month_key]).strip()))
                ly = int(float(str(row[lunar_year_key]).strip())) if lunar_year_key else None
            except (ValueError, TypeError):
                continue
            sd = solar_date(row[solar_key])
            if not sd:
                continue
            if month == 12 and day == 23 and (ly is None or ly == year - 1):
                start = sd if start is None else min(start, sd)
            if month == 1 and day == 10 and (ly is None or ly == year):
                end = sd if end is None else max(end, sd)
        return (start, end) if start and end and start <= end else None

    # --------------------------- gọi LLM ----------------------------------

    def _call_llm(self, messages: list[dict], json_mode: bool = True) -> str:
        models = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            models.append(self.fallback_model)
        last_err: Exception | None = None
        for idx, model in enumerate(models):
            try:
                text = self._request_llm(messages, model, json_mode)
                if model != self.model:
                    if self.verbose:
                        print(f"[agent] Chuyển sang model dự phòng: {model}")
                    self.model = model
                return text
            except RuntimeError as exc:
                last_err = exc
                if idx == len(models) - 1 or "PROMPT_TOO_LARGE" in str(exc):
                    raise
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if idx == len(models) - 1:
                    break
        raise RuntimeError(f"Gọi LLM thất bại sau nhiều lần: {last_err}")

    def _request_llm(
        self,
        messages: list[dict],
        model: str,
        json_mode: bool = True,
    ) -> str:
        if self.rate_limiter is not None:
            total_chars = sum(len(str(m.get("content", ""))) for m in messages)
            self.rate_limiter.acquire(max(1, int(total_chars / CHARS_PER_TOKEN)))

        delay = 2.0
        last_err: Exception | None = None
        for attempt in range(5):
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0,
                    "max_tokens": self.max_tokens,
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = self.client.chat.completions.create(**kwargs)
                content = resp.choices[0].message.content
                if not content or not str(content).strip():
                    raise ValueError("LLM trả về nội dung rỗng.")
                return str(content)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                text = str(exc)
                low = text.lower()
                status = getattr(exc, "status_code", None)
                # NIM có thể không hỗ trợ response_format hoặc trả về rỗng
                # khi dùng structured output -> tắt json_mode và thử lại.
                if ("response_format" in low and json_mode) or (
                    json_mode and "nội dung rỗng" in low
                ):
                    json_mode = False
                    continue
                if (status == 413 or "request too large" in low
                        or "tokens per minute" in low):
                    raise RuntimeError("PROMPT_TOO_LARGE: " + text[:300]) from exc
                if "model" in low and ("not found" in low or "does not exist" in low):
                    raise RuntimeError(
                        f"Model '{model}' không khả dụng. "
                        f"Đổi NIM_MODEL/LOCAL_LLM_MODEL. Lỗi gốc: {text[:200]}"
                    ) from exc
                retryable = (
                    status in (408, 409, 429, 500, 502, 503, 504)
                    or any(k in low for k in ("rate limit", "timeout", "temporarily",
                                              "overloaded"))
                )
                if attempt == 4 or not retryable:
                    break
                sleep_for = delay + random.uniform(0, 0.5)
                if self.verbose:
                    print(f"[agent] Lỗi API ({status}): {text[:100]} — "
                          f"thử lại sau {sleep_for:.1f}s")
                time.sleep(sleep_for)
                delay *= 2
        raise RuntimeError(f"Gọi model '{model}' thất bại: {last_err}")

    # --------------------------- chạy SQL ---------------------------------

    def _validate_quoted_identifiers(self, sql: str) -> None:
        """SQLite coi "TenCot" lạ là chuỗi -> SUM ra 0. Chặn trước cho chắc.

        Bỏ qua bí danh cột (sau AS) vì bí danh không phải identifier của bảng.
        """
        probe = re.sub(r'\bAS\s+"(?:[^"]|"")*"', "AS _alias_", sql, flags=re.IGNORECASE)
        # Bí danh viết tắt: SUM(x) "Tổng tiền"
        probe = re.sub(r'\)\s*"(?:[^"]|"")*"', ") _alias_", probe)
        for token in re.findall(r'"([^"]+)"', probe):
            token = token.replace('""', '"')
            if token in self.known_tables or token in self.known_columns:
                continue
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token) and token.lower() in (
                "select", "from", "where", "group", "order", "by", "as", "on",
                "join", "left", "inner", "outer", "limit", "having", "and", "or",
                "not", "null", "desc", "asc", "distinct", "case", "when", "then",
                "else", "end", "cast", "as", "real", "int", "text", "date",
            ):
                continue
            raise ValueError(
                f'Cột/bảng "{token}" không tồn tại trong database. '
                f"Các cột hợp lệ: {', '.join(sorted(self.known_columns)[:80])}"
            )

    def _execute_sql(self, sql: str) -> SQLOutcome:
        try:
            safe_sql = assert_safe_select(sql)
            self._validate_quoted_identifiers(safe_sql)
        except ValueError as exc:
            return SQLOutcome(error=str(exc))

        conn = sqlite3.connect(self.db_path, timeout=30)
        deadline = time.monotonic() + QUERY_TIMEOUT_SEC
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.set_progress_handler(
                lambda: 1 if time.monotonic() > deadline else 0, 20000
            )
            cur = conn.execute(safe_sql)
            columns = [d[0] for d in (cur.description or [])]
            rows = cur.fetchmany(MAX_ROWS + 1)
            truncated = len(rows) > MAX_ROWS
            if truncated:
                rows = rows[:MAX_ROWS]
            return SQLOutcome(rows=[list(r) for r in rows], columns=columns,
                              truncated=truncated)
        except sqlite3.Error as exc:
            return SQLOutcome(error=f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            return SQLOutcome(error=f"{type(exc).__name__}: {exc}")
        finally:
            conn.close()

    # --------------------------- format đáp án ----------------------------

    @staticmethod
    def _is_money_question(question: str) -> bool:
        q = question.lower()
        return any(w in q for w in MONEY_WORDS)

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float, Decimal)):
            f = float(value)
            return None if (math.isnan(f) or math.isinf(f)) else f
        if isinstance(value, str):
            return parse_number(value.strip().replace("%", ""))
        return None

    def _round_number(self, value: Any, question: str) -> int | float | None:
        if isinstance(value, bool):
            return value
        f = self._coerce_float(value)
        if f is None:
            return None
        if abs(f - round(f)) < 1e-9:
            return int(round(f))
        if self._is_money_question(question) or abs(f) >= 100000:
            return int(Decimal(str(f)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return float(Decimal(str(f)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def _format_date(self, value: Any, question: str) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            s = value.strftime("%Y-%m-%d")
        elif isinstance(value, date):
            s = value.isoformat()
        else:
            s = str(value).strip()
            if not s:
                return None
            if re.match(r"^\d{4}-\d{2}-\d{2}", s):
                s = s[:10]
            elif re.match(r"^\d{4}-\d{2}$", s):
                return s
            else:
                parsed = self._parse_as_of(s)
                if parsed is None:
                    return s
                s = parsed.isoformat()
        q = question.lower()
        if ("tháng" in q or "thang" in q) and "ngày" not in q and "ngay" not in q:
            return s[:7]
        return s[:10]

    def _format_percent(self, value: Any, question: str, sql: str) -> float | None:
        f = self._coerce_float(value)
        if f is None:
            return None
        if 0 < abs(f) < 1 and ("%" in question or "phần trăm" in question.lower()):
            if not re.search(r"\b100\b", sql):
                f *= 100
        return float(Decimal(str(f)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def _infer_type(self, question: str, n_rows: int, n_cols: int) -> str:
        q = question.lower()
        # Câu hỏi chỉ hỏi 1 thông tin (danh sách / top k / từng tháng...) → list
        if n_cols == 1 and any(k in q for k in (
            "top", "danh sách", "là gì", "những gì", "từng tháng", "từng quý",
            "từng ngày", "từng năm", "theo tháng", "theo quý", "mỗi tháng",
            "mỗi quý", "mỗi ngày", "mỗi năm", "bao nhiêu từng",
        )):
            return "list"
        if n_cols > 2 or (n_rows > 1 and n_cols > 1):
            return "table"
        if n_rows > 1:
            return "list"
        if "%" in q or "phần trăm" in q or "tỷ lệ" in q or "tỉ lệ" in q or "tỉ trọng" in q:
            return "percent"
        if "ngày nào" in q or "khi nào" in q or q.strip().startswith("ngày"):
            return "date"
        if ("có phải" in q or "đúng không" in q or "phải không" in q
                or ("có " in q and " không" in q and "bao nhiêu" not in q
                    and "không có" not in q)):
            return "boolean"
        return "number"

    def format_answer(
        self, outcome: SQLOutcome, hint_type: str | None, question: str, sql: str
    ) -> dict:
        rows = outcome.rows
        cols = [str(c) for c in outcome.columns]
        n_rows, n_cols = len(rows), len(cols)
        if n_rows == 0 or n_cols == 0:
            return {"type": "abstain"}

        hint = (hint_type or "").strip().lower()
        if hint not in ("number", "percent", "date", "string", "boolean", "list", "table"):
            hint = self._infer_type(question, n_rows, n_cols)

        # Lớp phòng ngừa: câu hỏi kiểu danh sách/top nhưng LLM trả table
        # → chỉ giữ cột đầu (tên đối tượng) và trả list 1 chiều.
        q_lower = question.lower()
        list_question = any(k in q_lower for k in (
            "top", "danh sách", "liệt kê", "kể tên", "là gì", "những gì",
            "những", "từng tháng", "từng quý", "từng ngày", "từng năm",
            "theo tháng", "theo quý", "theo ngày", "mỗi tháng", "mỗi quý",
            "mỗi ngày", "mỗi năm", "bao nhiêu từng",
        ))
        asks_two_things = any(
            k in q_lower for k in ("và", "cùng", "chi tiết", "cả hai")
        )
        # Hỏi đúng 1 đối tượng duy nhất ("mặt hàng nào ... nhất") → string.
        asks_single = (
            "nhất" in q_lower
            and any(k in q_lower for k in (
                "mặt hàng nào", "sản phẩm nào", "món nào", "loại nào",
                "khách nào", "người nào", " ai ", "ai ",
            ))
            and not any(k in q_lower for k in ("top", "những", "các", "danh sách"))
        )
        if hint in ("table", "list") and asks_single:
            hint = "string"
        elif hint == "table" and list_question and not asks_two_things:
            hint = "list"

        # Lớp phòng ngừa: LLM trả list nhưng câu hỏi hỏi "ngày nào/khi nào/hạn dùng" → date
        if hint == "list" and n_cols == 1 and n_rows >= 1:
            q_lower = question.lower()
            if any(k in q_lower for k in ("ngày nào", "khi nào", "hạn dùng", "hết hạn", "ngay hết")):
                hint = "date"

        # Lớp phòng ngừa: LLM trả list với nhiều cột nhưng câu hỏi chỉ hỏi 1 thông tin
        # → chỉ lấy cột đầu (thường là tên đối tượng), trả về list 1 chiều
        if hint == "list" and n_cols > 1:
            q_lower = question.lower()
            has_multi_info = any(k in q_lower for k in ("và", "cùng", "chi tiết", "cả hai"))
            if not has_multi_info:
                # Thường lấy cột đầu (tên đối tượng). Nếu cột đầu là nhãn kỳ
                # (2025-06 / 2025-06-01) thì lấy cột số ngay sau nó.
                col_idx = 0
                head = [r[0] for r in rows[:5] if r[0] is not None]
                if head and all(
                    isinstance(v, str)
                    and re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", v.strip())
                    for v in head
                ):
                    col_idx = min(1, n_cols - 1)
                items = []
                for row in rows:
                    cell = row[col_idx]
                    if cell is None:
                        continue
                    if isinstance(cell, (int, float, Decimal, bool)):
                        items.append(self._round_number(cell, question))
                    else:
                        items.append(_jsonable(cell))
                return {"type": "list", "answer": items} if items else {"type": "abstain"}

        if hint == "table":
            table_rows = []
            for row in rows:
                cells = []
                for cell in row:
                    if isinstance(cell, str):
                        cells.append(cell.strip() or None)
                    elif isinstance(cell, bool):
                        cells.append(cell)
                    elif isinstance(cell, (int, float, Decimal)):
                        cells.append(self._round_number(cell, question))
                    else:
                        cells.append(_jsonable(cell))
                table_rows.append(cells)
            return {"type": "table", "answer": {"columns": cols, "rows": table_rows}}

        if hint == "list":
            if n_cols == 1:
                items = []
                for (cell,) in rows:
                    if cell is None:
                        continue
                    # Nếu là dạng date (đã được chuyển từ list do hỏi ngày nào/hạn dùng)
                    if hint == "date":
                        ds = self._format_date(cell, question)
                        items.append(ds if ds else str(cell))
                    elif isinstance(cell, (int, float, Decimal, bool)):
                        items.append(self._round_number(cell, question))
                    else:
                        items.append(_jsonable(cell))
                return {"type": "list", "answer": items} if items else {"type": "abstain"}
            items = [
                [
                    self._round_number(c, question)
                    if isinstance(c, (int, float, Decimal)) else _jsonable(c)
                    for c in row
                ]
                for row in rows
            ]
            return {"type": "list", "answer": items}

        if hint == "string":
            value = rows[0][0]
            s = "" if value is None else str(value).strip()
            return {"type": "string", "answer": s} if s else {"type": "abstain"}

        if n_rows == 1 and n_cols == 1:
            value = rows[0][0]
        elif n_rows > 1 and n_cols == 1:
            items = [
                self._round_number(r[0], question)
                if isinstance(r[0], (int, float, Decimal)) else _jsonable(r[0])
                for r in rows
            ]
            return {"type": "list", "answer": items}
        else:
            if n_cols == 1:
                return {"type": "list", "answer": [_jsonable(r[0]) for r in rows]}
            return {
                "type": "table",
                "answer": {"columns": cols,
                           "rows": [[_jsonable(c) for c in r] for r in rows]},
            }

        if value is None:
            return {"type": "abstain"}

        if hint == "number":
            num = self._round_number(value, question)
            if num is None:
                s = str(value).strip()
                return {"type": "string", "answer": s} if s else {"type": "abstain"}
            return {"type": "number", "answer": num}

        if hint == "percent":
            pct = self._format_percent(value, question, sql)
            return {"type": "percent", "answer": pct} if pct is not None else {"type": "abstain"}

        if hint == "date":
            ds = self._format_date(value, question)
            return {"type": "date", "answer": ds} if ds else {"type": "abstain"}

        if hint == "boolean":
            if isinstance(value, bool):
                b: bool | None = value
            elif isinstance(value, (int, float)):
                b = bool(value)
            else:
                v = str(value).strip().lower()
                b = True if v in ("true", "1", "yes", "có", "co", "đúng") else (
                    False if v in ("false", "0", "no", "không", "khong", "sai") else None
                )
            return {"type": "boolean", "answer": b} if b is not None else {"type": "abstain"}

        if isinstance(value, float) and abs(value - round(value)) < 1e-9:
            value = int(round(value))
        s = str(value).strip()
        return {"type": "string", "answer": s} if s else {"type": "abstain"}

    # --------------------------- fuzzy product-name correction ---------------------------

    def _is_product_name_column(self, col: ColumnMeta) -> bool:
        """Xác định cột text nào là cột chứa tên sản phẩm (để fuzzy match)."""
        if col.kind != "text":
            return False
        k = col.name.lower()
        # Các cột rõ ràng là tên sản phẩm / tên hàng / nội dung mô tả hàng
        if any(tok in k for tok in (
            "ten_hang", "ten_sp", "tensanpham", "ten_san_pham", "ten_thuoc",
            "ten_mon", "san_pham", "sanpham", "mat_hang", "mathang", "thuoc",
            "mon", "noi_dung", "ten_hang_hoa", "tenhang",
        )):
            # Loại trừ cột tên người (khách, nhân viên, nhà cung cấp)
            if any(tok in k for tok in ("khach", "kh_hang", "nhan_vien", "nv_", "ncc", "nha_cung")):
                return False
            return True
        return False

    @staticmethod
    def _norm_fuzzy(s: str) -> str:
        """Chuẩn hóa để fuzzy match: bỏ dấu, lowercase, xóa ký hiệu."""
        s = strip_diacritics(s).lower()
        s = re.sub(r"[^\w\s]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _collect_product_candidates(self) -> list[str]:
        """Thu thập danh sách tên sản phẩm chuẩn từ top_values (hoặc DISTINCT nếu thiếu).

        Cột tên sản phẩm thường có n_distinct cao → top_values thường rỗng. Khi đó
        ta quét SELECT DISTINCT trực tiếp từ DB (giới hạn 150 giá trị/cột) để có
        danh sách mẫu đủ fuzzy match.
        """
        seen: set[str] = set()
        out: list[str] = []
        for t in self.tables:
            for c in t.columns:
                if not self._is_product_name_column(c):
                    continue
                vals: list[str] = [str(v).strip() for v in (c.top_values or []) if str(v).strip()]
                if len(vals) < 3:
                    # Bổ sung bằng DISTINCT trực tiếp từ DB
                    try:
                        conn = self._connect()
                        try:
                            rows = conn.execute(
                                f'SELECT DISTINCT "{c.name}" FROM "{t.name}" '
                                f'WHERE "{c.name}" IS NOT NULL AND "{c.name}"!="" LIMIT 150'
                            ).fetchall()
                            vals = [str(r[0]).strip() for r in rows if r[0] is not None and str(r[0]).strip()]
                        finally:
                            conn.close()
                    except sqlite3.Error:
                        pass
                for v in vals:
                    norm = self._norm_fuzzy(v)
                    if not norm or norm in seen:
                        continue
                    seen.add(norm)
                    out.append(v)
                if len(out) >= 400:
                    return out
        return out

    def _fuzzy_correct_product_names(self, question: str) -> str:
        """Thay thế tên sản phẩm trong câu hỏi bằng tên chuẩn trong DB (fuzzy > 90).

        Chỉ xét cụm 2-5 từ (tránh match từ đơn như 'cửa hàng', 'viên', 'tiền').
        Yêu cầu WRatio > 90 VÀ cụm phải trùng ít nhất 1 từ nghĩa với tên chuẩn
        (tránh 'cửa hàng' trùng 'BÁNH CROISSANT'). Tối đa 3 thay thế/câu.
        Giữ nguyên câu hỏi nếu không match đủ chắc.
        """
        if not _RAPIDFUZZ_AVAILABLE or not question:
            return question
        candidates = self._product_candidates
        if not candidates:
            return question
        norm_candidates = [self._norm_fuzzy(c) for c in candidates]
        norm_candidates = [n for n in norm_candidates if len(n) >= 3]
        if not norm_candidates:
            return question

        tokens = question.split()
        n_tokens = len(tokens)
        replacements: dict[int, tuple[str, int]] = {}
        used = [False] * n_tokens
        done = 0
        max_replace = 3

        max_n = min(5, n_tokens)
        for n in range(max_n, 1, -1):  # chỉ 2-5 từ, bỏ từ đơn
            for i in range(0, n_tokens - n + 1):
                if done >= max_replace:
                    break
                if any(used[i:i + n]):
                    continue
                phrase = " ".join(tokens[i:i + n])
                norm_phrase = self._norm_fuzzy(phrase)
                if not norm_phrase or len(norm_phrase) < 4:
                    continue
                # Bỏ cụm toàn từ hàm (stopwords) - không phải tên hàng
                phrase_words = set(norm_phrase.split())
                if phrase_words <= _PRODUCT_STOPWORDS:
                    continue
                match = process.extractOne(norm_phrase, norm_candidates, scorer=fuzz.WRatio)
                if not match:
                    continue
                score = float(match[1])
                if score <= 90:
                    continue
                norm_val = match[0]
                # Chỉ thay thế khi tên chuẩn không dài hơn cụm hỏi quá nhiều
                # (tránh 'Zyrtec 10mg hộp 1 vỉ' -> tên dài, gây lặp đuôi 'x 10 viên').
                if len(norm_val) > len(norm_phrase) * 1.25:
                    continue
                # Bắt buộc trùng ít nhất 2 từ nghĩa (tránh mác đơn như 'viên x 10 viên'
                # khớp toàn tên 'Zyrtec 10mg hộp 1 vỉ x 10 viên' qua một từ 'vien').
                cand_words = set(norm_val.split())
                sig_phrase = {w for w in norm_phrase.split() if not w.isdigit() and len(w) >= 3}
                sig_cand = {w for w in cand_words if not w.isdigit() and len(w) >= 3}
                if len(sig_phrase & sig_cand) < 2:
                    continue
                try:
                    cand_idx = norm_candidates.index(norm_val)
                except ValueError:
                    continue
                replacement = candidates[cand_idx]
                if replacement == phrase:
                    continue
                replacements[i] = (replacement, n)
                for j in range(i, i + n):
                    used[j] = True
                done += 1
            if done >= max_replace:
                break

        if not replacements:
            return question

        # Xây dựng lại câu hỏi, áp dụng replacement từ trái sang phải
        result: list[str] = []
        i = 0
        while i < n_tokens:
            if i in replacements:
                repl, n = replacements[i]
                result.append(repl)
                i += n
            else:
                result.append(tokens[i])
                i += 1
        return " ".join(result)

    # --------------------------- vòng lặp chính ---------------------------

    def ask(self, question: str, as_of_date: str | None = None,
            qid: str | None = None) -> tuple[dict, dict]:
        """Trả về (submission_dict, debug_dict). Không raise vì lỗi một câu."""
        started = time.monotonic()
        raw_question = question
        # Mở viết tắt / telex / thiếu dấu TRƯỚC khi dùng cho mọi bước phía sau
        # (lọc bảng, chọn rulebook, type, và prompt gửi LLM).
        question = normalize_question(question)
        # Fuzzy-modern tên sản phẩm theo dữ liệu thực trong DB (top_values)
        question = self._fuzzy_correct_product_names(question)
        debug: dict = {
            "id": qid,
            "question": raw_question,
            "normalized_question": question,
            "fuzzy_question": question,
            "as_of_date": as_of_date,
            "attempts": [],
            "final_sql": None,
            "llm": None,
            "note": "",
            "prompt_tokens_est": 0,
        }

        try:
            date_ctx = self.build_date_context(as_of_date)
            user_prompt = (
                f"CÂU HỎI: {question}\n\n"
                f"NGÀY MỐC (as_of_date): {as_of_date}\n\n"
                f"CÁC MỐC THỜI GIAN ĐÃ TÍNH SẴN:\n{date_ctx}\n\n"
                "Hãy trả về MỘT JSON object thuần khiết (không markdown code block, không text giải thích)\n"
                'có dạng {"sql": "...", "type": "...", "reason": "..."} hoặc {"sql": "UNANSWERABLE", ...}.'
            )

            system_prompt, est = self._compose_with_budget(question)
            debug["prompt_tokens_est"] = est
            level = 0
            messages: list[dict] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            reviewed_empty = False
            fix_count = 0

            while True:
                try:
                    raw = self._call_llm(messages)
                except RuntimeError as exc:
                    if "PROMPT_TOO_LARGE" in str(exc) and level < 3:
                        level += 1
                        system_prompt = self._build_system_prompt(question, level=level)
                        debug["prompt_tokens_est"] = int(len(system_prompt) / CHARS_PER_TOKEN)
                        messages[0] = {"role": "system", "content": system_prompt}
                        continue
                    raise

                debug["llm"] = raw
                try:
                    parsed = extract_json_object(raw)
                except ValueError as exc:
                    if fix_count >= self.max_fix_attempts:
                        debug["note"] = f"JSON lỗi: {exc}"
                        return {"id": qid, "type": "abstain"}, self._finish(debug, started)
                    fix_count += 1
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({
                        "role": "user",
                        "content": "Output không phải JSON hợp lệ. Chỉ trả đúng một "
                                   'JSON object dạng {"sql": "...", "type": "..."} '
                                   'hoặc {"sql": "UNANSWERABLE", ...}.',
                    })
                    continue

                sql = str(parsed.get("sql", "")).strip()
                hint_type = str(parsed.get("type", "")).strip().lower() or None
                reason = str(parsed.get("reason", "")).strip()

                if not sql or sql.upper().startswith("UNANSWERABLE") or hint_type == "unanswerable":
                    debug["note"] = "LLM báo không trả lời được: " + (reason or "không đủ dữ liệu")
                    return (
                        {"id": qid, "type": "unanswerable",
                         "reason": reason or "Dữ liệu phát hành không đủ để trả lời câu hỏi."},
                        self._finish(debug, started),
                    )

                debug["final_sql"] = sql
                outcome = self._execute_sql(sql)

                if outcome.error:
                    debug["attempts"].append({"sql": sql, "error": outcome.error})
                    if fix_count >= self.max_fix_attempts:
                        debug["note"] = f"SQL lỗi sau {fix_count} lần sửa: {outcome.error}"
                        return {"id": qid, "type": "abstain"}, self._finish(debug, started)
                    fix_count += 1
                    hint = ""
                    if "no such" in outcome.error.lower():
                        hint = (
                            "\nDANH SÁCH TÊN HỢP LỆ (chỉ dùng đúng tên này):\n"
                            + self._build_schema_text(
                                3, self._filter_relevant_tables(question)
                            )
                        )
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({
                        "role": "user",
                        "content": (
                            f"Code SQL sau bị lỗi: {sql}\n"
                            f"Lỗi: {outcome.error}\n"
                            f"{hint}\n"
                            'Hãy sửa lại. Chỉ trả JSON {"sql": "...", "type": "..."}. '
                            "Kiểm tra kỹ tên bảng/cột và cú pháp SQLite."
                        ),
                    })
                    continue

                has_data = any(
                    cell is not None for row in outcome.rows for cell in row
                )
                if not has_data:
                    if self.review_empty and not reviewed_empty and fix_count < self.max_fix_attempts:
                        reviewed_empty = True
                        fix_count += 1
                        messages.append({"role": "assistant", "content": raw})
                        messages.append({
                            "role": "user",
                            "content": (
                                f"SQL chạy được nhưng trả về 0 dòng: {sql}\n"
                                "Kiểm tra lại điều kiện ngày/trạng thái/tên (đối chiếu đúng "
                                "giá trị DISTINCT). Nếu đúng là không có dữ liệu hoặc kỳ nằm "
                                'ngoài khoảng dữ liệu thì trả {"sql": "UNANSWERABLE", '
                                '"type": "unanswerable", "reason": "..."}. Nếu sai thì sửa SQL.'
                            ),
                        })
                        continue
                    debug["note"] = "SQL trả về rỗng."
                    return {"id": qid, "type": "abstain"}, self._finish(debug, started)

                answer = self.format_answer(outcome, hint_type, question, sql)
                debug["columns"] = outcome.columns
                debug["row_count"] = len(outcome.rows)
                if outcome.truncated:
                    debug["note"] = f"Kết quả bị cắt ở {MAX_ROWS} dòng."
                answer["id"] = qid
                return answer, self._finish(debug, started)

        except Exception as exc:  # noqa: BLE001
            debug["note"] = f"Lỗi xử lý: {type(exc).__name__}: {exc}"
            return {"id": qid, "type": "abstain"}, self._finish(debug, started)

    @staticmethod
    def _finish(debug: dict, started: float) -> dict:
        debug["elapsed_sec"] = round(time.monotonic() - started, 2)
        return debug


# --------------------------------------------------------------------------
# CLI test nhanh
# --------------------------------------------------------------------------

def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="Test DataMate agent với 1 câu hỏi")
    parser.add_argument("question", help="Câu hỏi tiếng Việt")
    parser.add_argument("--db", required=True, help="DB của doanh nghiệp")
    parser.add_argument("--readme", default=None, help="README.md của doanh nghiệp")
    parser.add_argument("--date", default=date.today().isoformat(), help="as_of_date")
    parser.add_argument("--rulebook", default="rulebook.md")
    parser.add_argument("--calendar", default="calendar.csv")
    parser.add_argument("--model", default=None)
    parser.add_argument("--full-rulebook", action="store_true")
    parser.add_argument("--show-debug", action="store_true")
    args = parser.parse_args(argv)

    agent = DataMateAgent(
        db_path=args.db,
        readme_text=Path(args.readme).read_text(encoding="utf-8", errors="replace")
        if args.readme else None,
        rulebook_path=args.rulebook,
        calendar_path=args.calendar,
        model=args.model,
        full_rulebook=args.full_rulebook,
        verbose=True,
    )
    answer, debug = agent.ask(args.question, args.date)
    print(json.dumps(answer, ensure_ascii=False, indent=2))
    if args.show_debug:
        print(json.dumps(debug, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
