"""Phân loại thô câu hỏi theo họ intent (plan v3) bằng keyword/regex — không LLM.

Mỗi câu nhận 1 intent chính theo thứ tự ưu tiên trong RULES (họ hẹp xét trước).
So khớp trên câu đã bỏ dấu + chuẩn hoá teencode (bnhiu, jùm, th5/2026, đh, o đờ, tháng ni...).
Họ đánh dấu "+" là họ có trong dữ liệu nhưng chưa có trong danh sách 40 họ của plan v3.

    python count_intents.py --questions ../DATASET/public_test/questions.jsonl [--answers answers.jsonl] [--show other]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict

from data_loader import strip_diacritics

# Teencode/viết tắt -> dạng chuẩn (áp trên chuỗi đã bỏ dấu, lower).
_TELEX_STEMS = {"thang", "tinh", "ngay", "cua", "no", "sang", "hang", "tien", "ban", "don", "nhat", "het", "nhap", "moi",
                "quan", "quy", "cao", "trong", "mon", "khach", "bao", "nhieu", "duoc", "doanh", "thu", "tong", "chieu", "gia"}


def _telex(m: re.Match) -> str:
    """Gõ Telex không bật bộ gõ: nhaats->nhat, mooix->moi, heets->het, tieenf->tien, thangs->thang, cuar->cua."""
    w = m.group(0)
    d = re.sub(r"(aa|ee|oo|dd)", lambda x: x.group(1)[0], w)
    d = re.sub(r"([aou])w", r"\1", d)  # ă/ơ/ư: mawtj, dduwowcj
    if d != w and len(d) > 2 and d[-1] in "sfrxj":
        return d[:-1]
    if d == w and w[-1] in "sfrxj" and w[:-1] in _TELEX_STEMS:
        return w[:-1]
    return d


TEEN = [
    (r"≥|>=", " tu "), (r"≤|<=", " den "),
    (r"[^\w\s/%.,:+-]", " "),  # emoji, ký hiệu lạ
    (r"\b[a-z]+\b", _telex),
    (r"\b(bao nhiu|bnhiu|bnhieu|bao nhieeu|bnh|bn|nhiu)\b", "bao nhieu"),
    (r"(?<!bao )\bnhieu\b(?! (nhat|hon|don|lan|ngay|khach|tien|ly|loai|nguoi))", "bao nhieu"),
    (r"\b(dc|đc|duoj|duowc|duocj|dduoc)\b", "duoc"), (r"\b(jum|gium|dum)\b", "giup"),
    (r"\b(nhut|nhatt)\b", "nhat"), (r"\b(nhiu|nhiuu)\b", "nhieu"),
    (r"\b(dh|đh|o do|order)\b", "don"), (r"\b(ddown|down)\b", "don"), (r"\bbest ?seller\b", "ban chay nhat"), (r"\bchay nhat\b", "ban chay nhat"), (r"\bdthu\b", "doanh thu"),
    (r"\b(hnay|hum nay|hoom nay|bua nay|bua ni|hom ni)\b", "hom nay"), (r"\b(hqua|bua qua|hum qua)\b", "hom qua"),
    (r"\b(ni)\b", "nay"), (r"\b(trc|trk)\b", "truoc"), (r"\b(rui|roi)\b", "roi"),
    (r"\b(mik|mjk|minh)\b", "minh"), (r"\b(ko|k|hok|hong|khum|hem)\b(?=\s|$|\?)", "khong"),
    (r"\b(trog|trg)\b", "trong"), (r"\b(kac|cac)\b", "cac"), (r"\b(kua|cuar)\b", "cua"), (r"\bcko\b", "cho"),
    (r"\b(bin quan|bnh quan|bq)\b", "binh quan"), (r"\b(trung bih|trung binhf|tb)\b", "trung binh"),
    (r"\bnv\b", "nhan vien"), (r"\bncc\b", "nha cung cap"), (r"\bsp\b", "san pham"), (r"\bkh\b", "khach"),
    (r"(\s(z|zay|zi|vay|ha|nha|nhe|a|ak|ah|ru|rua|the|hen|nhi|ta|day|ne))+\s*\??\s*$", " "),  # trợ từ cuối câu
    (r"\b(tieng|tieenf|tien`)\b", "tien"), (r"\b(duco|ducc)\b", "duoc"), (r"\bbua\b", "ngay"),
    (r"\b(danh hang ve|cat hang)\b", "nhap hang"), (r"\bnhan dien\b", "nhan vien"), (r"\bdoanhthu\b", "doanh thu"), (r"\b(thieu tien|con thieu)\b", "con no"), (r"\bdup\b", "giup"), (r"\b(vau cho|vau chơ)\b", "voucher"), (r"\bti ne\b", "ti le"),
    (r"\bth\s*(\d{1,2})\b", r"thang \1"), (r"\bt(\d{1,2})(?=/)", r"thang \1"), (r"\bth\b", "thang"),
    (r"\s+", " "),
]

UNIT = r"(ly|dia|noi|cuc|bo|chai|hop|goi|vien|cai|lon|bao|thung|vi|tuyp|kg|m3|phan|to|suat|cay|cuon|tam|thanh|binh|bich|may|chiec|met|xe|tan)"

RULES: list[tuple[str, str]] = [
    ("unanswerable", r"(than thien|nhiet tinh|chu dao|le phep|tan tam|kho tinh|trung thuc|dang tin cay|uy tin|de thuong|"
                     r"lanh nghe|vui ve|dep trai|xinh) |(ngon|chat luong|dep) nhat|co nen |nen (chon|mua|dung|nhap)|"
                     r"(ba bau|tre em|tre so sinh|cho con bu|phu nu co thai).*(dung|uong|an)|lieu dung|uong may|"
                     r"tac dung phu|chua (benh|dau)|thoi tiet|gioi tinh|khach (nu|nam)\b|dia chi|ly do|vi sao|tai sao"),
    ("+customer_address", r"khach (o|duong|tai|khu|phuong|quan) [a-z]"),
    ("installment", r"tra gop|ky gop|(?<!dong )(?<!loi )(?<!lai )\bgop\b|cham gop"),
    ("tradein", r"\bmay cu\b|\bthu cu\b|tro gia|thu lai may|doi may cu"),
    ("+customer_pays", r"khach phai tra"),
    ("warranty", r"bao hanh"),
    ("+repair", r"\btho\b|phieu sua|luot xe|bien so|lam gi|thay nhot|hoa hong|dong xe|sua (xe|dien)|va ruot|thay ruot"),
    ("lot_expiry_range", r"het han trong|han dung (tu|trong)|(\d+) ngay toi.*(het han|han)|sap het han.*(\d+) ngay"),
    ("lot_expiry", r"het han|han dung|han su dung|can date|\bhsd\b|\blo\b"),
    ("prescription_drug", r"ke don|hoat chat"),
    ("debt_by_age", r"(\bno\b|cong no).*(qua|tren|hon) \d+ ngay|tuoi no|dat qua \d+ ngay"),
    ("debt_top", r"(khach|ai) no nhieu nhat|\d+ khach no|khach no .*nhieu nhat|liet ke .*khach no"),
    ("debt_customer_count", r"(bao nhieu|may|so) khach (con |dang |co )?(no|cong no)|khach no chua tra xong|khach co cong no"),
    ("debt_total", r"\bno\b|cong no|chua thu|ghi no|thu no|tra no|mua chiu"),
    ("purchase_by_supplier", r"nha cung cap"),
    ("purchase_avg_price", r"gia nhap (binh quan|trung binh)"),
    ("+import_minus_sales", r"nhap tru ban|nhap .*chenh|chenh lech nhap"),
    ("purchase_total", r"\bnhap\b"),
    ("stock_value", r"(gia tri|tien) .{0,20}(ton|trong kho)|ton kho .{0,20}(von|gia tri)|tien von .{0,20}lo"),
    ("+stock_qty", r"\bton\b|ton kho|trong kho|con may|het hang|chay hang|dut hang"),
    ("price_hike", r"tang gia|len gia|doi gia|giam gia xuong|ngay tang"),
    ("+cancel_rate", r"\bhuy\b"),
    ("+voucher_ship", r"voucher|phi ship|phi giao|dung diem|giam diem|tien giam"),
    ("profit_net", r"lai rong|loi nhuan rong|tru (het )?chi phi"),
    ("profit_gross", r"lai gop|loi nhuan|\blai\b|loi nhat|\bloi\b"),
    ("brand_size_pct", r"(size|hang|bim|sua|may|dong may|hang may) .{0,40}chiem .{0,20}(%|phan tram)|so bich"),
    ("customer_n_orders_pct", r"khach .{0,40}(it nhat|tu|tren) \d+ don.{0,40}(%|phan tram)"),
    ("customer_n_orders", r"khach .{0,30}(it nhat|tu|tren) \d+ don|khach .{0,30}bao nhieu don|khach .{0,30}mua \d+ don"),
    ("customer_top", r"khach .{0,40}nhat|khach (sop|trung thanh|doi y|quen)|ai la khach|ai mua"),
    ("+new_member", r"thanh vien moi|dang ky"),
    ("count_customers", r"(bao nhieu|may|so|tong) khach"),
    ("product_in_n_orders", r"xuat hien .{0,30}\d+ don|uu chuong nhat|co mat trong"),
    ("+unsold_items", r"(mat hang|hang|mon|san pham|thuoc) (bi )?e\b|\be han\b|\be ca\b|khong ban (ra |duoc )|ban hong|ban khong duoc|chua ban duoc|khong co don nao"),
    ("+delivery", r"dot giao|dot hang|cho giao|cho di|luot giao|chua giao|da giao"),
    ("day_threshold_count", r"(bao nhieu|may) ngay .{0,40}(tu|tro len|dat|tren|duoi)"),
    ("daily_max", r"(ngay|hom|thang|tuan|gio) nao|dat khach nhat|gio cao diem|thu may"),
    ("daily_avg", r"(trung binh|binh quan) moi (ngay|thu|chu nhat)|moi ngay (trung binh|binh quan)"),
    ("avg_per_order", r"(trung binh|binh quan|gia tri) .{0,25}(don|bill|hoa don|phieu)"),
    ("revenue_by_shift", r"\bca (sang|chieu|toi)\b|bao cao ca"),
    ("revenue_by_payment", r"tien mat|chuyen khoan|\bcod\b|vi dien tu|quet the|hinh thuc thanh toan|tra bang|tien tuoi|momo"),
    ("revenue_by_channel", r"tai quay|zalo|dien thoai|online|shopee|lazada|tiktok|\bsan\b(?! pham)|kenh|grab|giao di|facebook|web"),
    ("revenue_by_employee", r"nhan vien"),
    ("revenue_by_category", r"\bnhom\b|\bloai\b|size (m|l)\b"),
    ("comparison_percent", r"so voi|hon hay kem|tang hay giam|tang truong|len hay xuong|thay doi|chenh|so (thang|tuan|nam|ky)"),
    ("boolean_yesno", r"(co|da|duoc) .{0,60}khong\s*\??$|phai khong|dung khong|hay khong|tang lien tuc|giam lien tuc"),
    ("ranking_revenue", r"(nhieu tien|doanh thu (cao|lon)|thu ve nhieu) nhat|top .{0,20}doanh thu"),
    ("ranking_qty", r"ban chay|nhieu nhat|it nhat|\btop ?\d|\b\d+ (mat hang|mon|san pham|thuoc)|xep hang|hot nhat"),
    ("qty_of_product", r"(bao nhieu|may|so|tong so) " + UNIT + r"\b|so luong|ban (duoc|ra) .{0,30}" + UNIT + r"\b"),
    ("count_orders", r"(bao nhieu|may|so|tong|tong so|tong cong) (don|bill|hoa don|phieu)|so don"),
    ("revenue", r"doanh thu|doanh so|tien ban|tien hang|bao nhieu tien|thu ve|mang ve|ban (duoc|ra) .{0,20}tien|revenue|total"),
]
COMPILED = [(name, re.compile(p)) for name, p in RULES]


def norm(text: str) -> str:
    s = " " + strip_diacritics(str(text)).lower() + " "
    for pat, rep in TEEN:
        s = re.sub(pat, rep, s)
    return s.strip()


def classify(question: str) -> tuple[str, list[str]]:
    q = norm(question)
    hits = [name for name, rx in COMPILED if rx.search(q)]
    return (hits[0] if hits else "other"), hits


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--questions", default="questions.jsonl")
    p.add_argument("--answers", default=None, help="tuỳ chọn: in phân bố type gold theo intent")
    p.add_argument("--show", default=None, help="in 20 câu mẫu của intent này")
    a = p.parse_args()

    qs = [json.loads(l) for l in open(a.questions, encoding="utf-8") if l.strip()]
    gold = {}
    if a.answers:
        gold = {json.loads(l)["id"]: json.loads(l)["type"] for l in open(a.answers, encoding="utf-8") if l.strip()}

    primary, by_type, by_biz = Counter(), defaultdict(Counter), defaultdict(set)
    samples: dict[str, list[str]] = defaultdict(list)
    for q in qs:
        intent, _ = classify(q["question"])
        primary[intent] += 1
        by_biz[intent].add(q["business_id"])
        if gold:
            by_type[intent][gold.get(q["id"], "?")] += 1
        if len(samples[intent]) < 20:
            samples[intent].append(f"{q['business_id']} | {q['question']}")

    n = len(qs)
    cum = 0
    print(f"{'#':>3} {'intent':<24}{'count':>6}{'%':>7}{'cộng dồn':>10}  biz" + ("   gold types" if gold else ""))
    for i, (name, c) in enumerate(primary.most_common(), 1):
        cum += c
        types = ", ".join(f"{t}:{k}" for t, k in by_type[name].most_common()) if gold else ""
        print(f"{i:>3} {name:<24}{c:>6}{100 * c / n:>6.1f}%{100 * cum / n:>9.1f}%  {len(by_biz[name]):>2}   {types}")
    top20 = sum(c for _, c in primary.most_common(20))
    print(f"TOTAL {n} | số họ có câu: {len(primary)} | top-20 họ chiếm {100 * top20 / n:.1f}%")
    if a.show:
        print(f"\n--- mẫu '{a.show}' ---")
        print("\n".join(samples[a.show]))


if __name__ == "__main__":
    main()
