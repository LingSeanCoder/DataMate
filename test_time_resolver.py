"""Test time_resolver trên câu thật của train. Kỳ vọng tính TAY theo rulebook §2 (không chép từ output).

    python test_time_resolver.py
"""
from __future__ import annotations

import sys

from time_resolver import resolve

N = None
# (id hoặc "synthetic", as_of, câu hỏi, [(start, end), ...] theo thứ tự xuất hiện trong câu)
CASES = [
    # hôm nay / hôm qua
    ("q_002d03ebbded", "2025-04-30", "Tổng doanh thu trong ngày hôm nay là bao nhiêu?", [("2025-04-30", "2025-04-30")]),
    ("q_0139627bccae", "2025-10-18", "check gium hnay cua hang co bao nhiu don", [("2025-10-18", "2025-10-18")]),
    ("q_0504f61cae69", "2025-10-03", "e bua qua cua hang ban duoc may don", [("2025-10-02", "2025-10-02")]),
    ("q_05bf323fdee2", "2026-05-11", "hom qua cua hang ban duoc bao nhiu tien", [("2026-05-10", "2026-05-10")]),
    # đến hết ngày X: luỹ kế từ đầu dữ liệu
    ("q_0194d8d10f17", "2026-08-19", "Tính đến hết ngày 19/08/2026, có bao nhiêu khách còn nợ cửa hàng?", [(N, "2026-08-19")]),
    ("q_026fa17395ed", "2026-05-25", "Tính đến hết ngày 25/05/2026, khách hàng còn nợ cửa hàng tổng cộng bao nhiêu tiền?", [(N, "2026-05-25")]),
    # ngày cụ thể
    ("q_01a252a82858", "2026-06-02", "Ngày 14/07/2025 cửa hàng bán Cà phê muối (L) với đơn giá bao nhiêu một ly?", [("2025-07-14", "2025-07-14")]),
    # N ngày gần nhất = [as_of-N+1, as_of]
    ("q_01b914589609", "2025-02-19", "Cho tôi biết dthu 30 ngày gần nhất", [("2025-01-21", "2025-02-19")]),
    ("q_10e64d599cf5", "2026-03-01", "doanh thu 30 ngày gần nhất của cửa hangf là bao nhiêu", [("2026-01-31", "2026-03-01")]),  # T2/2026 có 28 ngày
    # tuần này: thứ Hai -> as_of. 2026-08-19 là T4, 2026-05-22 là T6
    ("q_019ec94d9799", "2026-08-19", "3 mặt hàng bán chạy nhất tuần này là những mặt hàng nào?", [("2026-08-17", "2026-08-19")]),
    ("q_07fd86b575da", "2026-05-22", "Cho tôi 3 mặt hàng bán chạy nhất tuần này.", [("2026-05-18", "2026-05-22")]),
    # tuần trước: trọn T2-CN tuần liền trước. 2025-06-20 T6, 2026-06-18 T5
    ("q_0283688e44df", "2025-06-20", "Cho hỏi tuần trước riêng ca chiều bán được tổng cộng bao nhiêu tiền?", [("2025-06-09", "2025-06-15")]),
    ("q_04293d2ed907", "2026-06-18", "Ca chiều tuần trước doanh thu được bao nhiêu?", [("2026-06-08", "2026-06-14")]),
    # tháng này = ngày 1 -> as_of
    ("q_001883280e83", "2026-09-07", "tháng này tổng giá trị các phiếu nhập hàng là bao nhiu", [("2026-09-01", "2026-09-07")]),
    ("q_00195e1e4049", "2026-05-25", "Thuốc có hoạt chất Natri clorid tháng này bán được tổng cộng bao nhiêu tiền?", [("2026-05-01", "2026-05-25")]),
    # cùng kỳ tháng trước = cùng số ngày
    ("q_09581d4bcf0b", "2026-09-07", "Từ đầu tháng này đến hết hôm nay, doanh thu hơn hay kém cùng kỳ tháng trước mấy %?",
     [("2026-09-01", "2026-09-07"), ("2026-08-01", "2026-08-07")]),
    ("synthetic", "2026-03-31", "doanh thu tháng này so với cùng kỳ tháng trước tăng bao nhiêu %",
     [("2026-03-01", "2026-03-31"), ("2026-02-01", "2026-02-28")]),  # tháng trước không có ngày 31 -> hết tháng
    # tháng trước (đó) neo theo kỳ được hỏi
    ("q_0872d9f34f31", "2026-06-02", "Cho mình hỏi so với tháng trước đó, doanh thu tháng 2/2026 tăng hay giảm mấy phần trăm?",
     [("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-28")]),
    ("q_112c84a433a5", "2026-06-16", "Doanh thu tháng 5/2026 tăng hay giảm bao nhiêu phần trăm so với tháng trước?",
     [("2026-05-01", "2026-05-31"), ("2026-04-01", "2026-04-30")]),
    # cùng kỳ năm trước neo theo kỳ được hỏi
    ("q_046b262bd6a7", "2026-07-28", "so cùng kỳ năm trước, doanh thu thangs 5/2026 tawng/giamr mấy %",
     [("2025-05-01", "2025-05-31"), ("2026-05-01", "2026-05-31")]),
    # tháng M/Y, tháng M năm Y
    ("q_00558c1410ef", "2026-06-24", "tỉ lệ huỷ đơn tháng 6/2025 là nhiêu %", [("2025-06-01", "2025-06-30")]),
    ("q_02a28b3629be", "2026-07-09", "Cho hỏi số đơn bị huỷ trong tháng 8 năm 2025 là bao nhiêu?", [("2025-08-01", "2025-08-31")]),
    # tháng/quý không năm: năm nay nếu không sau hôm nay, ngược lại năm trước
    ("synthetic", "2026-06-10", "doanh thu tháng 9 là bao nhiêu", [("2025-09-01", "2025-09-30")]),
    ("synthetic", "2026-06-10", "doanh thu quý 1 bao nhiêu", [("2026-01-01", "2026-03-31")]),
    # quý
    ("q_026fabde15fc", "2026-07-09", "Cho hỏi tổng doanh thu quý 2/2026 là bao nhiêu?", [("2026-04-01", "2026-06-30")]),
    ("q_09840cd43a2c", "2026-07-31", "trong quy 2 nam 2026 co tong cong bnhiu don", [("2026-04-01", "2026-06-30")]),
    # từ đầu năm đến hết hôm nay
    ("q_0ea59e7dddea", "2025-10-18", "Cho mình hỏi số đơn từ đầu năm tới hết hôm nay là bao nhiêu?", [("2025-01-01", "2025-10-18")]),
    ("q_137ebe6e6af4", "2026-03-08", "Tính từ đầu năm đến hết hôm nay, cửa hàng đã có tổng cộng bao nhiêu đơn hàng?", [("2026-01-01", "2026-03-08")]),
    # Tết 2026: 23/12 âm 2025 = 2026-02-10, mùng 10/1 âm 2026 = 2026-02-26 (17 ngày); trước Tết = 17 ngày ngay trước
    ("q_0fbd522a4f2f", "2026-09-14", "Dịp Tết âm lịch 2026 có doanh thu hơn hay kém 17 ngày trước Tết bao nhiêu %?",
     [("2026-02-10", "2026-02-26"), ("2026-01-24", "2026-02-09")]),
    ("q_4cca5b4efafc", "2026-06-28", "Dịp Tết 2026, mặt hàng nào bán chạy nhất ạ?", [("2026-02-10", "2026-02-26")]),
    # N tháng liền nhau / liên tiếp -> từng tháng, cũ -> mới
    ("q_0183529f9d61", "2026-05-25", "dthu 3 tháng liền nhau kết thúc ở tháng 6/2025 là bao nhiêu mỗi tháng, từ tháng cũ đến tháng mới",
     [("2025-04-01", "2025-04-30"), ("2025-05-01", "2025-05-31"), ("2025-06-01", "2025-06-30")]),
    ("q_313dfc683558", "2026-08-05", "Liệt kê doanh thu 3 tháng liên tiếp từ tháng 2/2025 đến tháng 4/2025, theo thứ tự thời gian.",
     [("2025-02-01", "2025-02-28"), ("2025-03-01", "2025-03-31"), ("2025-04-01", "2025-04-30")]),
    # N ngày kể từ D = [D, D+N-1]; N ngày ngay trước = [D-N, D-1]
    ("q_02b5f208ee57", "2026-05-25", "Từ ngày 16/03/2025, Fugacar 500mg hộp 1 viên tăng giá lên 23.000đ. So 7 ngày kể từ ngày 16/03/2025 "
     "với 7 ngày ngay trước đó, số viên bán ra thay đổi bao nhiêu phần trăm nhỉ?",
     [("2025-03-16", "2025-03-22"), ("2025-03-09", "2025-03-15")]),
    ("q_0a8ec3050fe7", "2026-08-05", "pin panasonic aa đổi giá từ 22.000đ lên 23.000đ vào ngày 02/06/2026. 30 ngày kể từ ngày đó bán được "
     "nhìu hay ít hơn 30 ngày liền trước bao nhiêu %, tính theo số lượng",
     [("2026-06-02", "2026-07-01"), ("2026-05-03", "2026-06-01")]),
    # không ghi ngày tăng -> metric tự tìm D từ lịch sử giá
    ("q_0d3c74a07021", "2026-05-25", "Acemuc Kids 200mg hộp 30 gói tăng giá lên 3.200đ: so 7 ngày kể từ ngày tăng với 7 ngày ngay trước "
     "thì số gói bán ra tăng hay giảm bao nhiêu % nhỉ?", [(N, N), (N, N)]),
    # từ D1 đến hết D2
    ("q_05aca83bd353", "2026-05-16", "Trong giai đoạn từ 20/10/2025 đến hết 26/10/2025, bao nhiêu mặt hàng bị ế nhỉ?", [("2025-10-20", "2025-10-26")]),
    ("q_0ed6031586cc", "2026-06-28", "tiền vốn đang nằm ở các lô còn tồn có hạn dùng từ 28/06/2026 đến hết 26/09/2026 là bnhiu vậy",
     [("2026-06-28", "2026-09-26")]),
]


def main() -> int:
    fails = 0
    for qid, as_of, question, expected in CASES:
        got = [(p["start"], p["end"]) for p in resolve(question, as_of)]
        ok = got == expected
        fails += not ok
        if not ok:
            print(f"FAIL {qid} as_of={as_of}\n  {question}\n  expected {expected}\n  got      {got}")
    print(f"\n=== time_resolver: {len(CASES) - fails}/{len(CASES)} pass ({sum(c[0] != 'synthetic' for c in CASES)} câu thật) ===")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
