"""
validate_db.py — Kiểm định chất lượng các DB do data_loader.py tạo ra.

Đặt cạnh data/ và tmp_db/ (ngoài root). KHÔNG hard-code tên bảng / tên cột:
mọi quan hệ và công thức đều được tự dò từ dữ liệu.

Kiểm tra gì:
  1. Tổng quan & bảng rỗng
  2. Loader đã bỏ bao nhiêu dòng (từ file *.report.json)
  3. Dòng trùng hoàn toàn
  4. Null (phân biệt cột khoá và cột ghi chú)
  5. Ngày: sai định dạng, quá cũ, vượt ngày hết dữ liệu trong README
  6. Quan hệ giữa các bảng tự dò (khoá ngoại) + dòng mồ côi
  7. Đối soát tổng:  SUM(chi tiết) theo khoá  ==  cột tổng ở bảng cha ?
     -> kết luận luôn dòng trùng là THỪA hay HỢP LỆ
  8. Công thức nội bộ trong 1 bảng:  c = a + b,  c = a * b

Chạy:
    python validate_db.py                    # tất cả tmp_db/*.db
    python validate_db.py --only biz_124     # 1 doanh nghiệp
    python validate_db.py --db-dir tmp_db --out validation_report.html
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))  # dùng module trong src/

from __future__ import annotations

import argparse
import html
import itertools
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ERR, WARN, INFO, OK = "error", "warn", "info", "ok"
ICON = {ERR: "❌", WARN: "⚠️", INFO: "ℹ️", OK: "✅"}
LABEL = {ERR: "Lỗi", WARN: "Cần xem", INFO: "Lưu ý", OK: "Đạt"}

KEY_NAME = re.compile(r"(^|_)(ma|id|so_phieu|order|bill|so_ct)(_|$)")
FUTURE_OK_NAME = re.compile(r"hsd|han|expire|deadline|ap_dung")


# --------------------------------------------------------------------------
# Cấu trúc kết quả
# --------------------------------------------------------------------------

@dataclass
class Finding:
    level: str
    table: str | None
    title: str
    detail: str = ""
    hint: str = ""


@dataclass
class BizResult:
    name: str
    db_path: str
    tables: dict = field(default_factory=dict)   # table -> rows
    findings: list = field(default_factory=list)
    error: str | None = None

    def add(self, level, table, title, detail="", hint=""):
        self.findings.append(Finding(level, table, title, detail, hint))

    def count(self, level):
        return sum(1 for f in self.findings if f.level == level)

    @property
    def status(self):
        if self.error:
            return ERR
        if self.count(ERR):
            return ERR
        if self.count(WARN):
            return WARN
        return OK


# --------------------------------------------------------------------------
# Tiện ích
# --------------------------------------------------------------------------

def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def pct(a: float, b: float) -> str:
    return f"{(100.0 * a / b if b else 0):.1f}%"


def fmt_num(x) -> str:
    try:
        return f"{float(x):,.0f}" if float(x) == int(float(x)) else f"{float(x):,.2f}"
    except (TypeError, ValueError):
        return str(x)


# --------------------------------------------------------------------------
# Các bước kiểm tra
# --------------------------------------------------------------------------

def check_overview(res, tables, load_report):
    for t, df in tables.items():
        res.tables[t] = len(df)
        if df.empty:
            res.add(WARN, t, "Bảng rỗng (0 dòng)")

    rep_tables = (load_report or {}).get("tables", {})
    for t, st in rep_tables.items():
        rows_in = st.get("rows_in", 0)
        if not rows_in:
            continue
        removed = (st.get("removed_junk", 0) + st.get("removed_header_echo", 0)
                   + st.get("removed_duplicate", 0))
        if removed / rows_in > 0.2:
            res.add(
                WARN, t,
                f"Loader đã bỏ {removed:,}/{rows_in:,} dòng ({pct(removed, rows_in)})",
                f"dòng rác {st.get('removed_junk', 0):,}, lặp header "
                f"{st.get('removed_header_echo', 0):,}, trùng "
                f"{st.get('removed_duplicate', 0):,}",
                "Tỉ lệ bỏ cao: mở file gốc xem có bỏ nhầm dòng thật không.",
            )


def check_empty_conversion(res, conn, tables):
    if "_quy_doi" in {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}:
        n = conn.execute("SELECT COUNT(*) FROM _quy_doi").fetchone()[0]
        if n == 0:
            cand = [t for t, df in tables.items()
                    if any(k in t for k in ("quydoi", "quy_doi"))]
            hint = (f"Có bảng quy đổi riêng: {', '.join(cand)}." if cand
                    else "README không có dòng quy đổi đúng mẫu.")
            res.add(INFO, "_quy_doi", "Bảng _quy_doi rỗng", hint=hint)


def check_duplicates(res, tables, dup_counts):
    for t, df in tables.items():
        if df.empty:
            continue
        n = int(df.duplicated().sum())
        dup_counts[t] = n
        if n:
            res.add(
                WARN, t, f"{n:,} dòng trùng hoàn toàn ({pct(n, len(df))})",
                hint="Chưa chắc là lỗi: xem kết luận ở mục đối soát tổng bên dưới.",
            )


def check_nulls(res, tables):
    for t, df in tables.items():
        if df.empty:
            continue
        n = len(df)
        low_info = []
        for c in df.columns:
            nulls = int(df[c].isna().sum())
            if nulls == 0:
                continue
            ratio = nulls / n
            if nulls == n:
                res.add(WARN, t, f"Cột `{c}` rỗng hoàn toàn",
                        hint="Cột không có dữ liệu: loader đọc sai hay file gốc trống?")
            elif KEY_NAME.search(c):
                res.add(
                    WARN, t, f"Cột khoá `{c}` có {nulls:,} null ({pct(nulls, n)})",
                    hint="Nếu là khách lẻ/không có mã thì bình thường, nhưng agent "
                         "phải dùng LEFT JOIN để không mất dòng.",
                )
            elif ratio >= 0.5:
                low_info.append(f"{c} ({pct(nulls, n)})")
        if low_info:
            res.add(INFO, t, "Cột thưa dữ liệu (≥50% null)", "; ".join(low_info),
                    "Thường là ghi chú/mô tả ít điền, bình thường.")


def check_dates(res, tables, kinds, data_end):
    today = date.today()
    for t, df in tables.items():
        for c in df.columns:
            if kinds.get((t, c)) != "date":
                continue
            s = df[c].dropna().astype(str)
            if s.empty:
                continue
            parsed = pd.to_datetime(s, errors="coerce", format="mixed")
            bad = int(parsed.isna().sum())
            if bad:
                res.add(ERR, t, f"Cột ngày `{c}`: {bad:,} giá trị không parse được",
                        "; ".join(s[parsed.isna()].unique()[:3]))
                continue
            lo, hi = parsed.min().date(), parsed.max().date()
            problems = []
            if lo.year < 2000:
                problems.append(f"ngày nhỏ nhất {lo} quá cũ")
            if not FUTURE_OK_NAME.search(c):
                if hi > today:
                    problems.append(f"ngày lớn nhất {hi} nằm ở tương lai")
                if data_end and hi > data_end:
                    problems.append(f"ngày lớn nhất {hi} vượt ngày hết dữ liệu "
                                    f"trong README ({data_end})")
            if problems:
                res.add(WARN, t, f"Cột ngày `{c}`: " + "; ".join(problems),
                        hint="Có thể sai năm khi parse ngày (vd: dd/mm bị đảo).")
            lens = s.str.len().value_counts()
            if len(lens) > 1 and set(lens.index) <= {10, 19}:
                res.add(INFO, t, f"Cột ngày `{c}` lẫn dòng có giờ và không có giờ",
                        f"{dict(lens)}")


def check_numbers(res, tables, kinds):
    for t, df in tables.items():
        neg = []
        for c in df.columns:
            if kinds.get((t, c)) not in ("number", "percent"):
                continue
            s = pd.to_numeric(df[c], errors="coerce")
            k = int((s < 0).sum())
            if k:
                neg.append(f"{c} ({k:,})")
        if neg:
            res.add(INFO, t, "Cột số có giá trị âm", "; ".join(neg),
                    "Có thể là hoàn/trả hàng (hợp lệ) hoặc parse sai dấu.")


# ---------- quan hệ giữa các bảng ----------

def _clean_str(series: pd.Series) -> pd.Series:
    return series.dropna().astype(str).str.strip()


def find_relations(tables, kinds):
    """Tự dò quan hệ A.ca -> B.cb (cb gần như duy nhất, giá trị ca nằm trong cb)."""
    sets: dict[tuple, set] = {}
    keys: list[tuple] = []
    for t, df in tables.items():
        for c in df.columns:
            if kinds.get((t, c)) != "text":
                continue
            s = _clean_str(df[c])
            s = s[s != ""]
            if s.nunique() < 5:
                continue
            sets[(t, c)] = set(s.unique())
            if s.nunique() / len(s) >= 0.98:
                keys.append((t, c))

    rels = []
    for (ta, ca), set_a in sets.items():
        for (tb, cb) in keys:
            if ta == tb:
                continue
            set_b = sets[(tb, cb)]
            overlap = len(set_a & set_b) / len(set_a)
            if overlap < 0.5:
                continue
            rels.append({"A": ta, "ca": ca, "B": tb, "cb": cb, "overlap": overlap,
                         "set_a": set_a, "set_b": set_b})

    # Nếu 2 chiều cùng tồn tại (1-1) thì giữ chiều A là bảng nhiều dòng hơn.
    best = {}
    for r in sorted(rels, key=lambda r: -len(tables[r["A"]])):
        sig = frozenset([(r["A"], r["ca"]), (r["B"], r["cb"])])
        best.setdefault(sig, r)
    return list(best.values())


def check_relations(res, tables, rels):
    if not rels:
        res.add(INFO, None, "Không tự dò được quan hệ giữa các bảng",
                hint="Các bảng có thể độc lập, hoặc mã liên kết khác định dạng.")
        return
    for r in rels:
        A, ca, B, cb = r["A"], r["ca"], r["B"], r["cb"]
        s = _clean_str(tables[A][ca])
        total = len(s)
        orphan = s[~s.isin(r["set_b"])]
        label = f"`{A}.{ca}` → `{B}.{cb}`"
        if orphan.empty:
            res.add(OK, A, f"Quan hệ {label}: 100% khớp", f"{total:,} dòng có khoá")
            continue
        ratio = len(orphan) / total
        ex = ", ".join(orphan.unique()[:5])
        res.add(
            ERR if ratio > 0.05 else WARN, A,
            f"Quan hệ {label}: {len(orphan):,} dòng ({pct(len(orphan), total)}) "
            f"không có ở bảng gốc",
            f"Ví dụ: {ex}",
            "Mã mồ côi: dữ liệu thiếu, hoặc loader làm sạch mã hai bảng khác nhau.",
        )


# ---------- đối soát tổng ----------

def _num_cols(df, t, kinds):
    return [c for c in df.columns if kinds.get((t, c)) == "number"]


def reconcile(res, tables, kinds, rels, dup_counts):
    found_any = False
    for r in rels:
        if r["overlap"] < 0.8:
            continue
        A, ca, B, cb = r["A"], r["ca"], r["B"], r["cb"]
        dfA, dfB = tables[A], tables[B]
        na, nb = _num_cols(dfA, A, kinds), _num_cols(dfB, B, kinds)
        if not na or not nb:
            continue

        def grouped(frame):
            f = frame[[ca] + na].copy()
            f["_k"] = f[ca].astype(str).str.strip()
            return f.drop(columns=[ca]).groupby("_k")[na].sum(min_count=1)

        gb = dfB[[cb] + nb].copy()
        gb["_k"] = gb[cb].astype(str).str.strip()
        gb = gb.drop(columns=[cb]).drop_duplicates("_k").set_index("_k")

        g_raw = grouped(dfA)
        has_dup = dup_counts.get(A, 0) > 0
        g_ded = grouped(dfA.drop_duplicates()) if has_dup else None

        def rate(g, xa, xb):
            idx = g.index.intersection(gb.index)
            if len(idx) < 10:
                return None
            sa, sb = g.loc[idx, xa], gb.loc[idx, xb]
            ok = sa.notna() & sb.notna()
            if ok.sum() < 10:
                return None
            sa, sb = sa[ok], sb[ok]
            if (sa != 0).mean() < 0.1 or (sb != 0).mean() < 0.1:
                return None
            match = (sa - sb).abs() <= np.maximum(1.0, 0.001 * sb.abs())
            return match.mean(), int((~match).sum()), len(sa), sa[~match], sb[~match]

        for xb in nb:
            best = None
            for xa in na:
                out = rate(g_raw, xa, xb)
                if out and out[0] >= 0.8 and (best is None or out[0] > best[1][0]):
                    best = (xa, out)
            if not best:
                continue
            found_any = True
            xa, (rt, nbad, n, bad_a, bad_b) = best
            rule = f"SUM(`{A}.{xa}`) theo `{ca}` = `{B}.{xb}`"
            if rt >= 0.9995:
                res.add(OK, A, f"Đối soát {rule}: khớp 100%", f"{n:,} khoá so sánh")
            else:
                ex = "; ".join(
                    f"{k}: chi tiết={fmt_num(bad_a[k])} vs tổng={fmt_num(bad_b[k])}"
                    for k in list(bad_a.index)[:3]
                )
                res.add(ERR if rt < 0.95 else WARN, A,
                        f"Đối soát {rule}: khớp {rt*100:.2f}% ({nbad:,}/{n:,} khoá lệch)",
                        f"Ví dụ lệch → {ex}",
                        "Lệch có thể do dòng trùng, parse tiền sai, hoặc bị loại nhầm dòng.")

            if has_dup:
                out2 = rate(g_ded, xa, xb)
                if out2:
                    rt2 = out2[0]
                    n_dup = dup_counts[A]
                    if rt2 > rt + 0.001:
                        res.add(ERR, A,
                                f"KẾT LUẬN dòng trùng ở `{A}`: là THỪA, nên loại",
                                f"Bỏ trùng thì {rule} khớp tăng {rt*100:.2f}% → {rt2*100:.2f}% "
                                f"({n_dup:,} dòng).",
                                "Sửa loader để loại trùng bảng này (doanh thu đang bị đếm đôi).")
                    elif rt2 < rt - 0.001:
                        res.add(OK, A,
                                f"KẾT LUẬN dòng trùng ở `{A}`: là HỢP LỆ, giữ nguyên",
                                f"Bỏ trùng thì khớp giảm {rt*100:.2f}% → {rt2*100:.2f}%. "
                                "(cùng hoá đơn mua 2 dòng giống hệt).",
                                "KHÔNG loại trùng bảng này.")
                    else:
                        res.add(INFO, A,
                                f"Dòng trùng ở `{A}` không ảnh hưởng đối soát",
                                f"Khớp {rt*100:.2f}% cả khi có và không có dòng trùng.")
    if rels and not found_any:
        res.add(INFO, None, "Không tìm được cặp cột tổng để đối soát",
                hint="Các bảng có quan hệ nhưng không có cột tiền tương ứng.")


# ---------- công thức nội bộ ----------

def check_formulas(res, tables, kinds):
    for t, df in tables.items():
        if len(df) < 20:
            continue
        cols = []
        for c in _num_cols(df, t, kinds):
            arr = pd.to_numeric(df[c], errors="coerce").to_numpy(float)
            if (np.nan_to_num(arr) != 0).mean() >= 0.1:
                cols.append((c, arr))
        cols = cols[:14]
        if len(cols) < 3:
            continue
        hits = []
        for ci, (c, cv) in enumerate(cols):
            others = [x for i, x in enumerate(cols) if i != ci]
            for (a, av), (b, bv) in itertools.combinations(others, 2):
                valid = ~(np.isnan(cv) | np.isnan(av) | np.isnan(bv))
                if valid.sum() < 20:
                    continue
                x, y, z = av[valid], bv[valid], cv[valid]
                tol = np.maximum(1.0, 0.001 * np.abs(z))
                nz = (x != 0) & (y != 0)
                for op, pred in (("+", x + y), ("×", x * y)):
                    ok = np.abs(pred - z) <= tol
                    if nz.sum() < 5:
                        continue
                    rate_nz = ok[nz].mean()
                    rate_all = ok.mean()
                    if rate_nz < 0.9 or rate_all < 0.9:
                        continue
                    if op == "×" and ((z == x).mean() > 0.98 or (z == y).mean() > 0.98):
                        continue
                    bad = np.where(~ok)[0][:3]
                    ex = "; ".join(
                        f"{a}={fmt_num(x[i])}, {b}={fmt_num(y[i])}, {c}={fmt_num(z[i])}"
                        for i in bad)
                    hits.append((rate_all, f"`{c}` = `{a}` {op} `{b}`",
                                 int((~ok).sum()), int(valid.sum()), ex))
        hits.sort(key=lambda h: -h[0])
        for rate_all, rule, nbad, n, ex in hits[:6]:
            if rate_all >= 0.9995:
                res.add(OK, t, f"Công thức {rule}: đúng 100%", f"{n:,} dòng")
            else:
                res.add(WARN, t,
                        f"Công thức {rule}: đúng {rate_all*100:.2f}% ({nbad:,}/{n:,} dòng sai)",
                        f"Ví dụ sai → {ex}",
                        "Công thức gần đúng nhưng có dòng lệch: kiểm tra parse tiền/số lượng.")


# --------------------------------------------------------------------------
# Chạy cho 1 DB
# --------------------------------------------------------------------------

def validate_db(db_path: Path) -> BizResult:
    res = BizResult(name=db_path.stem, db_path=str(db_path))
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            data_names = [n for n in names if not n.startswith("_")]
            tables = {n: pd.read_sql_query(f"SELECT * FROM {q(n)}", conn)
                      for n in data_names}

            kinds = {}
            if "_meta_columns" in names:
                mc = pd.read_sql_query(
                    "SELECT table_name, column_name, kind FROM _meta_columns", conn)
                kinds = {(r.table_name, r.column_name): r.kind
                         for r in mc.itertuples(index=False)}
            else:
                res.add(WARN, None, "Thiếu bảng _meta_columns",
                        hint="DB không do data_loader.py tạo, kiểu cột sẽ đoán từ dữ liệu.")
                for t, df in tables.items():
                    for c in df.columns:
                        kinds[(t, c)] = ("number" if pd.api.types.is_numeric_dtype(df[c])
                                         else "text")

            data_end = None
            if "_meta_info" in names:
                row = conn.execute(
                    "SELECT value FROM _meta_info WHERE key='data_end_date'").fetchone()
                if row and row[0]:
                    try:
                        data_end = datetime.strptime(row[0], "%Y-%m-%d").date()
                    except ValueError:
                        pass

            load_report = None
            rp = db_path.with_suffix(".report.json")
            if rp.is_file():
                try:
                    load_report = json.loads(rp.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass

            dup_counts: dict = {}
            check_overview(res, tables, load_report)
            check_empty_conversion(res, conn, tables)
            check_duplicates(res, tables, dup_counts)
            check_nulls(res, tables)
            check_dates(res, tables, kinds, data_end)
            check_numbers(res, tables, kinds)
            rels = find_relations(tables, kinds)
            check_relations(res, tables, rels)
            reconcile(res, tables, kinds, rels, dup_counts)
            check_formulas(res, tables, kinds)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        res.error = f"{type(exc).__name__}: {exc}"
    return res


# --------------------------------------------------------------------------
# Xuất báo cáo
# --------------------------------------------------------------------------

def print_terminal(results: list[BizResult]) -> None:
    for r in results:
        print("\n" + "=" * 72)
        print(f" {ICON[r.status]} {r.name}   "
              f"(❌ {r.count(ERR)}  ⚠️ {r.count(WARN)}  ℹ️ {r.count(INFO)}  ✅ {r.count(OK)})")
        print("=" * 72)
        if r.error:
            print(f"  [LỖI KHI KIỂM TRA] {r.error}")
            continue
        shown = [f for f in r.findings if f.level in (ERR, WARN)]
        if not shown:
            print("  Không có vấn đề nào cần xử lý.")
        for f in shown[:25]:
            where = f"[{f.table}] " if f.table else ""
            print(f"  {ICON[f.level]} {where}{f.title}")
            if f.detail:
                print(f"       {f.detail}")
            if f.hint:
                print(f"       → {f.hint}")
        if len(shown) > 25:
            print(f"  ... còn {len(shown) - 25} mục nữa, xem trong file HTML.")

    print("\n" + "-" * 72)
    print(f" {'DOANH NGHIỆP':<16}{'KẾT QUẢ':<14}{'LỖI':>5}{'XEM':>6}{'ĐẠT':>6}")
    print("-" * 72)
    for r in results:
        print(f" {r.name:<16}{LABEL[r.status]:<14}{r.count(ERR):>5}"
              f"{r.count(WARN):>6}{r.count(OK):>6}")
    print("-" * 72)


CSS = """
:root{--bg:#fff;--fg:#1c1c1e;--muted:#6b6b70;--card:#f5f5f7;--line:#e2e2e6;
--err:#c62828;--warn:#b26a00;--info:#2b6cb0;--ok:#2e7d32}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#ececee;--muted:#9a9aa0;
--card:#202023;--line:#333338;--err:#ff6b6b;--warn:#ffb74d;--info:#74b0ff;--ok:#6fcf7a}}
*{box-sizing:border-box}body{margin:0;padding:24px;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;max-width:1000px;margin-inline:auto}
h1{margin:0 0 4px}h2{margin:28px 0 8px;font-size:18px}.muted{color:var(--muted)}
table{border-collapse:collapse;width:100%}th,td{padding:8px 10px;text-align:left;
border-bottom:1px solid var(--line)}th{font-size:13px;color:var(--muted);font-weight:600}
details{background:var(--card);border-radius:10px;margin:10px 0;padding:4px 14px}
summary{cursor:pointer;padding:10px 0;font-weight:600}
.f{padding:8px 0;border-top:1px solid var(--line)}.f:first-of-type{border-top:0}
.t{font-weight:600}.d{color:var(--muted);font-size:13px;margin-top:2px;word-break:break-word}
.h{font-size:13px;margin-top:2px}.tag{font-size:12px;padding:1px 7px;border-radius:99px;
border:1px solid var(--line);margin-left:6px;color:var(--muted)}
.error{color:var(--err)}.warn{color:var(--warn)}.info{color:var(--info)}.ok{color:var(--ok)}
.guide{background:var(--card);border-radius:10px;padding:10px 16px;font-size:14px}
code{background:var(--line);padding:0 4px;border-radius:4px;font-size:13px}
"""


def _finding_html(f: Finding) -> str:
    tag = f'<span class="tag">{html.escape(f.table)}</span>' if f.table else ""
    out = (f'<div class="f"><div class="t">{ICON[f.level]} '
           f'<span class="{f.level}">{html.escape(f.title)}</span>{tag}</div>')
    if f.detail:
        out += f'<div class="d">{html.escape(f.detail)}</div>'
    if f.hint:
        out += f'<div class="h">→ {html.escape(f.hint)}</div>'
    return out + "</div>"


def write_html(results: list[BizResult], path: Path) -> None:
    parts = [f"<!doctype html><html lang='vi'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Báo cáo kiểm định DB</title><style>{CSS}</style></head><body>",
             "<h1>Báo cáo kiểm định dữ liệu</h1>",
             f"<div class='muted'>Tạo lúc {datetime.now():%d/%m/%Y %H:%M} · "
             f"{len(results)} doanh nghiệp</div>",
             "<div class='guide'><b>Cách đọc:</b> ❌ Lỗi = nhiều khả năng số liệu sai, "
             "cần sửa loader · ⚠️ Cần xem = có thể bình thường, nhưng nên đối chiếu · "
             "ℹ️ Lưu ý = thông tin thêm · ✅ Đạt = đã đối soát khớp.</div>",
             "<h2>Tổng hợp</h2><table><tr><th>Doanh nghiệp</th><th>Kết quả</th>"
             "<th>❌</th><th>⚠️</th><th>✅</th><th>Bảng</th></tr>"]
    for r in results:
        parts.append(
            f"<tr><td><a href='#{r.name}'>{r.name}</a></td>"
            f"<td class='{r.status}'>{ICON[r.status]} {LABEL[r.status]}</td>"
            f"<td>{r.count(ERR)}</td><td>{r.count(WARN)}</td><td>{r.count(OK)}</td>"
            f"<td>{len(r.tables)}</td></tr>")
    parts.append("</table>")

    order = {ERR: 0, WARN: 1, INFO: 2, OK: 3}
    for r in results:
        parts.append(f"<h2 id='{r.name}'>{ICON[r.status]} {r.name}</h2>")
        if r.error:
            parts.append(f"<div class='error'>Không kiểm tra được: "
                         f"{html.escape(r.error)}</div>")
            continue
        parts.append("<div class='muted'>"
                     + " · ".join(f"{t}: {n:,} dòng" for t, n in r.tables.items())
                     + "</div>")
        for level, opened in ((ERR, True), (WARN, True), (INFO, False), (OK, False)):
            items = [f for f in r.findings if f.level == level]
            if not items:
                continue
            parts.append(f"<details{' open' if opened else ''}><summary>"
                         f"{ICON[level]} {LABEL[level]} ({len(items)})</summary>"
                         + "".join(_finding_html(f) for f in items) + "</details>")
    parts.append("</body></html>")
    path.write_text("\n".join(parts), encoding="utf-8")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def main(argv=None) -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="Kiểm định chất lượng DB của DataMate")
    ap.add_argument("--db-dir", default="tmp_db")
    ap.add_argument("--only", nargs="*", help="chỉ kiểm tra các biz này, vd: biz_124")
    ap.add_argument("--out", default="validation_report.html")
    ap.add_argument("--no-html", action="store_true")
    args = ap.parse_args(argv)

    db_dir = Path(args.db_dir)
    if not db_dir.is_dir():
        print(f"Không thấy thư mục {db_dir}", file=sys.stderr)
        return 1
    dbs = sorted(db_dir.glob("*.db"))
    if args.only:
        dbs = [p for p in dbs if p.stem in set(args.only)]
    if not dbs:
        print("Không có file .db nào để kiểm tra.", file=sys.stderr)
        return 1

    results = []
    for p in dbs:
        print(f"Đang kiểm tra {p.name} ...", flush=True)
        results.append(validate_db(p))

    print_terminal(results)
    if not args.no_html:
        out = Path(args.out)
        write_html(results, out)
        print(f"\n📄 Báo cáo chi tiết: {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
