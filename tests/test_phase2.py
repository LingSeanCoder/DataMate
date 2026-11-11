"""Test formatter (§9) + entity (khớp tên thật trong DB). Kỳ vọng viết tay.

    python test_phase2.py   (cần tmp_db/ đã build)
"""
from __future__ import annotations
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))  # dùng module trong src/

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


# 30 câu public thật (tmp_db_dev): thuộc tính khớp giá trị thật trong std_*; list theo thứ tự xuất hiện trong câu
PUBLIC_CASES = [
    ("dev_013", "Nhóm đồ dùng tháng 6/2026 bán được bao nhiêu tiền?", "category", ["đồ dùng"]),
    ("dev_005", "Tháng 2 năm 2026, nhóm vitamin khoáng chất thì mặt hàng nào thu về nhiều tiền nhất?", "category", ["vitamin khoáng chất"]),
    ("dev_008", "bé ơi th10/2025 nhóm cơm mì cháo chiếm bnhiu % tổng dthu kả wán mik, thanks e", "category", ["cơm mì cháo"]),
    ("dev_003", "Trong tháng 9 năm 2025, các mặt hàng nhóm trà sữa bán ra tổng cộng bao nhiêu ly?", "category", ["trà sữa"]),
    ("dev_003", "11-2025 nhóm trà bán đc bnhiu tiền?", "category", ["trà"]),
    ("dev_003", "Đến hết ngày 15/05/2026, cửa hàng còn bao nhiêu khách hàng chưa trả hết nợ?", "category", None),  # 'trả' ≠ 'trà'
    ("dev_008", "Tháng 5/2025 có mấy mặt hàng được gọi chong ít nhất 172 đơn?", "category", None),  # 'gọi' ≠ 'gỏi'
    ("dev_001", "Doanh thu của Bia Heineken 330ml trong tháng 7 năm 2025 là bao nhiêu?", "category", None),  # 'bia' thuộc tên hàng
    ("dev_011", "6-2025 tiệm bán được mấy chiếc điện thoại, gộp cả máy mới với máy cũ nhé?", "category", ["máy mới", "máy cũ"]),
    ("dev_001", "Nhom thuc pham thang 2/25 ban duoc bao nhieu tien?", "category", ["thực phẩm"]),
    ("dev_013", "Bỉm Bobby tháng này chiếm bao nhiêu phần trăm tổng số bịch bỉm bán ra, gộp mọi size?", "brand", ["Bobby"]),
    ("dev_013", "cko mik bít xíu, trog th3/2026 sữa bột số 2 kủa hãng nào bán đc nhìu lon nhứt jùm nha", "size", ["Số 2"]),
    ("dev_013", "Thang nay bim size L, tinh tat ca cac hang, ban duoc bao nhieu bich?", "size", ["Size L"]),
    ("dev_013", "Thang nay bim size XL chiem bao nhieu % so bich bim ban ra?", "size", ["Size XL"]),
    ("dev_002", "Tháng này quán bán ra tổng cộng mấy ly size M, cộng tất cả các món?", "size", ["M"]),
    ("dev_012", "Tho Dam Lan An thang nay lam duoc tong bao nhieu tien roi?", "employee", ["Đàm Lan An"]),
    ("dev_012", "Luong Chi Duc thang 4/26 ban tong cong bnhiu tien?", "employee", ["Lương Chi Đức"]),
    ("dev_003", "Doanh thu của nhân viên mã NV005 tháng 3/2026 là bao nhiêu?", "employee", ["NV005"]),
    ("dev_006", "cko e coi tổng tiền hàng kác đơn nv Từ Hùng Hằng bán trog th4/2025 jùm mik nha 🙏", "employee", ["NV002"]),
    ("dev_006", "10-2025 nhân viên Đinh Đạt Đức bán được bnhiu tiền vậy?", "employee", ["NV003"]),
    ("dev_010", "Thang 1/26 doanh thu don ban tai quay bao nhieu, don ban qua dien thoai bao nhieu?", "channel", ["store", "phone"]),
    ("dev_007", "Th này đơn bán qua Zalo OA dthu bnhiu, còn đơn bán tại quầy bnhiu?", "channel", ["zalo_oa", "store"]),
    ("dev_013", "alo, th5/2026 đơn bán trên sàn thương mại điện tử với đơn bán wa Zalo OA, tung ben dthu bnhiu", "channel",
     ["Sàn TMĐT", "Zalo"]),
    ("dev_013", "11/2025 tien cac don ban tai quay chiem may % tong doanh thu ca thang vay?", "channel", ["Tại tiệm"]),
    ("dev_006", "Th3/2026, dthu các đơn trả xi ô đi và các đơn trả ví điện tử lần lượt là bnhiu?", "payment", ["cod", "ewallet"]),
    ("dev_003", "Th7/2025 dthu đơn trả bằng ví điện tử với đơn trả bằng tiền mặt lần nượt bnhiu?", "payment", ["Ví điện tử", "Tiền mặt"]),
    ("dev_006", "T3/2026 Diệp Hồng Hồng ghi nợ thêm nhiêu, trả nợ lại nhiêu vậy?", "payment", None),  # công nợ, không phải lọc thanh toán
    ("dev_010", "Cho tôi biết doanh thu các đơn của khách nhóm sỉ tháng 4/2026.", "customer_group", ["sỉ"]),
    ("dev_010", "Tháng 6 năm 2025 tỉ lệ đơn huỷ là mấy % vậy?", "customer_group", None),  # 'lệ' ≠ 'lẻ'
    ("dev_003", "Tháng 9/2025 mặt hàng Topping Thạch cà phê bán được tổng cộng bao nhiêu phần?", "product", "Topping Thạch cà phê"),
]


def public_cases():
    fails = 0
    for biz, q, field, exp in PUBLIC_CASES:
        got = extract_entities(q, f"tmp_db_dev/{biz}.db")[field]
        if field == "product":
            got = got["name"] if got else None
        ok = got == exp
        fails += not ok
        print(f"  {'PASS' if ok else 'FAIL'} public[{biz}] {field}={got!r}" + ("" if ok else f" expected {exp!r} | {q[:70]}"))
    return len(PUBLIC_CASES), fails


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
    n3, f3 = public_cases()
    print(f"\n=== formatter: {n1 - f1}/{n1} pass | entity: {n2 - f2}/{n2} pass | public: {n3 - f3}/{n3} pass ===")
    sys.exit(1 if f1 or f2 or f3 else 0)
