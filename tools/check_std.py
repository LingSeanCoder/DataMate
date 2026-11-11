"""Đối chiếu bảng std_orders/std_lines với đáp án train, không cần LLM.

Lấy các câu "doanh thu tháng M/YYYY" và "doanh thu trung bình một đơn tháng M/YYYY"
rồi tính bằng SQL cố định trên std_orders. Sai ở đây = lỗi tầng dữ liệu, không phải lỗi model.

    python check_std.py --answers /path/answers.jsonl [--db-dir tmp_db] [--biz biz_107] [-v]
"""
from __future__ import annotations
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))  # dùng module trong src/

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

REVENUE = re.compile(r"doanh thu|dthu|doanh số|bán (được|đc|dc) (tổng cộng )?bao nhiêu tiền", re.I)
MONTH = re.compile(r"tháng (\d{1,2})(?:/| năm )(\d{4})", re.I)
# Câu có thêm điều kiện (nhóm, khách, ca, mặt hàng...) không thuộc phạm vi kiểm tra này.
EXTRA = re.compile(
    r"nhóm|khách|nhân viên|\bca\b|hoạt chất|kê đơn|thanh toán|tiền mặt|chuyển khoản|%|so với|"
    r"của |mặt hàng|món|size|ngày|tuần|chiều|sáng|liền nhau|tăng|giảm|kênh|nguồn|loại|hãng|dòng|"
    r"nhom|quầy|zalo|online|sàn|shopee|lazada|tiktok|facebook|(số|mấy|bao nhiêu) đơn|≥|trở lên",
    re.I,
)


def round_money(x: float) -> int:
    return int(x + 0.5 + 1e-9) if x >= 0 else -int(-x + 0.5 + 1e-9)


def cases(questions: Path, answers: Path, biz: str | None):
    gold = {json.loads(l)["id"]: json.loads(l) for l in answers.open(encoding="utf-8") if l.strip()}
    for line in questions.open(encoding="utf-8"):
        if not line.strip():
            continue
        q = json.loads(line)
        text, g = q["question"], gold.get(q["id"])
        if not g or g["type"] != "number" or (biz and q["business_id"] != biz):
            continue
        m = MONTH.search(text)
        if not (REVENUE.search(text) and m) or EXTRA.search(text):
            continue
        avg = bool(re.search(r"trung b[iì]nh|b[iì]nh qu", text, re.I))
        if avg and not re.search(r"(một|1|mỗi|mooix|moi) (đơn|bill|hoá đơn|hóa đơn)", text, re.I):
            continue
        yield q, f"{int(m.group(2)):04d}-{int(m.group(1)):02d}", avg, g["answer"]


def compute(conn: sqlite3.Connection, month: str, as_of: str, avg: bool) -> float | None:
    rev, n = conn.execute(
        "SELECT SUM(revenue), COUNT(*) FROM std_orders "
        "WHERE is_valid = 1 AND substr(order_date, 1, 7) = ? AND order_date <= ?",
        (month, as_of),
    ).fetchone()
    if rev is None:
        return None
    return rev / n if avg else rev


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--questions", default="questions.jsonl")
    p.add_argument("--answers", required=True)
    p.add_argument("--db-dir", default="tmp_db")
    p.add_argument("--biz")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()

    per_biz = defaultdict(lambda: [0, 0])
    conns: dict[str, sqlite3.Connection] = {}
    for q, month, avg, gold in cases(Path(a.questions), Path(a.answers), a.biz):
        b = q["business_id"]
        db = Path(a.db_dir) / f"{b}.db"
        if b not in conns:
            conns[b] = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            val = compute(conns[b], month, q["as_of_date"], avg)
        except sqlite3.Error as e:
            val = f"ERR {e}"
        ok = isinstance(val, float) and round_money(val) == gold
        per_biz[b][0 if ok else 1] += 1
        if a.verbose and not ok:
            print(f"XX {b} {q['id']} {month}{' avg' if avg else ''}: got {val} gold {gold} | {q['question']}")

    total_ok = sum(v[0] for v in per_biz.values())
    total = sum(sum(v) for v in per_biz.values())
    for b in sorted(per_biz):
        ok, bad = per_biz[b]
        print(f"{b}: {ok}/{ok + bad}{'' if not bad else '  <--'}")
    print(f"=== std revenue check: {total_ok}/{total} ===")


if __name__ == "__main__":
    main()
