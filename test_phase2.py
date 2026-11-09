"""Test formatter (§9) + entity (khớp tên thật trong DB). Kỳ vọng viết tay.

    python test_phase2.py   (cần tmp_db/ đã build)
"""
from __future__ import annotations

import sys

from entity import extract_entities
from formatter import FormatError, rank, round_half_up, shape_answer


def formatter_cases():
    cases = [
        ("tiền 0.5 làm tròn lên", lambda: shape_answer(271866000.5, "number", money=True), {"type": "number", "answer": 271866001}),
        ("tiền trung bình về đồng", lambda: shape_answer(522914.6, "number", money=True), {"type": "number", "answer": 522915}),
        ("số không phải tiền: 2 chữ số", lambda: shape_answer(18.555, "number"), {"type": "number", "answer": 18.56}),
        ("số nguyên giữ int", lambda: shape_answer(389.0, "number"), {"type": "number", "answer": 389}),
        ("percent 2 chữ số, âm giữ dấu", lambda: shape_answer(-1.425, "percent"), {"type": "percent", "answer": -1.43}),
        ("date ngày", lambda: shape_answer("2028-08-06 00:00:00", "date"), {"type": "date", "answer": "2028-08-06"}),
        ("date theo tháng", lambda: shape_answer("2025-09-14", "date", month=True), {"type": "date", "answer": "2025-09"}),
        ("boolean", lambda: shape_answer(False, "boolean"), {"type": "boolean", "answer": False}),
        ("string lấy phần tử duy nhất", lambda: shape_answer(["Latte (L)"], "string"), {"type": "string", "answer": "Latte (L)"}),
        ("list tiền làm tròn", lambda: shape_answer([287435200.0, 346554700.4], "list", money=True),
         {"type": "list", "answer": [287435200, 346554700]}),
        ("table ô số", lambda: shape_answer([["Chị Lan", 3200000.0]], "table", money=True, columns=["khach_hang", "so_tien_no"]),
         {"type": "table", "answer": {"columns": ["khach_hang", "so_tien_no"], "rows": [["Chị Lan", 3200000]]}}),
        ("rank: bằng nhau theo mã tăng dần",
         lambda: [x["name"] for x in rank([{"name": "B", "code": "SP02", "value": 5}, {"name": "A", "code": "SP03", "value": 5},
                                           {"name": "C", "code": "SP01", "value": 9}], k=2)], ["C", "B"]),
    ]
    fails = 0
    for name, fn, exp in cases:
        got = fn()
        ok = got == exp
        fails += not ok
        print(f"  {'PASS' if ok else 'FAIL'} formatter: {name}" + ("" if ok else f" -> got {got} expected {exp}"))
    # sai kiểu phải raise, không đoán
    for name, fn in [("number nhận chuỗi", lambda: shape_answer("4820000", "number")),
                     ("string nhận list 2 phần tử", lambda: shape_answer(["a", "b"], "string"))]:
        try:
            fn()
            fails += 1
            print(f"  FAIL formatter: {name} không raise")
        except FormatError:
            print(f"  PASS formatter: {name} -> FormatError")
    assert round_half_up(2.675, 2) == 2.68  # float 2.675 không bị banker's rounding
    return len(cases) + 2, fails


DB = "tmp_db/{}.db"
ENTITY_CASES = [
    # (biz, câu hỏi thật, field, kỳ vọng)
    ("biz_106", "trong thang 2 nam 2025 cua hang ban duoc may ly tra sua o long (m)", "product", "Trà sữa ô long (M)"),
    ("biz_106", "trong thang 2 nam 2025 cua hang ban duoc may ly tra sua o long (m)", "unit", "ly"),
    ("biz_106", "Tháng 5/2026 giá nhập bình quân một hộp Bánh croissant là bao nhiêu vậy?", "product", "Bánh croissant"),
    ("biz_106", "So số ly Latte (L) bán ra trong 30 ngày kể từ ngày 05/02/2026, ngày tăng giá lên 53.500đ, với 30 ngày ngay trước ngày đó",
     "product", "Latte (L)"),
    ("biz_124", "Tháng 10/2025 cửa hàng bán được tổng cộng bao nhiêu viên Zyrtec 10mg hộp 1 vỉ x 10 viên?", "product",
     "Zyrtec 10mg hộp 1 vỉ x 10 viên"),
    ("biz_124", "Cho mình hỏi theo tồn kho ngày 25/05/2026, lô Siro ăn ngon Ích Nhi Plus chai 100ml gần hết hạn nhất có hạn dùng là ngày nào?",
     "product", "Siro ăn ngon Ích Nhi Plus chai 100ml"),
    ("biz_124", "Từ ngày 16/03/2025, Fugacar 500mg hộp 1 viên tăng giá lên 23.000đ. So 7 ngày kể từ ngày 16/03/2025 với 7 ngày ngay trước đó",
     "product", "Fugacar 500mg hộp 1 viên"),
    ("biz_124", "Thuốc có hoạt chất Natri clorid tháng này bán được tổng cộng bao nhiêu tiền?", "hoat_chat", "Natri clorid"),
    ("biz_127", "tieenf bán má heo nướng nước mắm tháng 9/2025 là nhiêu", "product", "Má heo nướng nước mắm"),
    ("biz_102", "tháng 4/2026 bans Bột ngọt Ajinomoto 400g được bao nhiêu tieenf", "product", "Bột ngọt Ajinomoto 400g"),
    ("biz_104", "dthu cuar Sữa tươi trân châu đường đen (L) trong thangs 6 năm 2025 là bao nhiêu", "product",
     "Sữa tươi trân châu đường đen (L)"),
    ("biz_125", "tháng 5/2026 bán được bao nhiêu lon Bia Tiger Bạc lon", "product", "Bia Tiger Bạc lon"),  # tự tạo: không lẫn "Bia Tiger lon"
    ("biz_106", "3 mặt hàng bán chạy nhất tuần này là những mặt hàng nào?", "top_k", 3),
    ("biz_125", "tháng 5/2025 hàng nào bán chạy nhất", "product", None),  # ca âm: không nhắc mặt hàng
]


def entity_cases():
    fails = 0
    for biz, q, field, exp in ENTITY_CASES:
        ent = extract_entities(q, DB.format(biz))
        got = ent[field]
        if field == "product":
            got = got["name"] if got else None
        ok = got == exp
        fails += not ok
        print(f"  {'PASS' if ok else 'FAIL'} entity[{biz}] {field}={got!r}" + ("" if ok else f" expected {exp!r} | {q[:70]}"))
    return len(ENTITY_CASES), fails


if __name__ == "__main__":
    n1, f1 = formatter_cases()
    n2, f2 = entity_cases()
    print(f"\n=== formatter: {n1 - f1}/{n1} pass | entity: {n2 - f2}/{n2} pass ===")
    sys.exit(1 if f1 or f2 else 0)
