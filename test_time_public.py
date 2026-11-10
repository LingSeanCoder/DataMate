"""Test time_resolver trên câu PUBLIC thật (teencode). Kỳ vọng tính TAY theo rulebook public §2.

    python test_time_public.py      (mục tiêu plan v3: >= 38/40)
"""
from __future__ import annotations

import sys

from time_resolver import resolve

N = None
M = lambda y, m, last: (f"{y}-{m:02d}-01", f"{y}-{m:02d}-{last}")  # noqa: E731
CASES = [
    ("q_a33f5513b520", "2026-09-06", "Doanh thu trung bình trên một đơn tháng 11/25 là bao nhiêu?", [M(2025, 11, 30)]),
    ("q_4e280a1755de", "2026-08-31", "Lượt xe sửa điện tháng 8/25 bnhiu?", [M(2025, 8, 31)]),
    ("q_b696d918fcc6", "2026-07-22", "Nhóm chiên xào 11/25 bán được bao nhiêu tiền vậy?", [M(2025, 11, 30)]),
    ("q_ab14596b6b93", "2026-07-08", "T10/2025 dthu hơn hay kém th trước nhiêu %?", [M(2025, 10, 31), M(2025, 9, 30)]),
    ("q_0ed1ebc4fe4d", "2026-07-08", "Khách mua ít nhất 4 đơng trong tháng 11/2025 có mấy người?", [M(2025, 11, 30)]),
    ("q_1341cdede99a", "2026-08-21", "T5/2025 co may mat hang nam trong it nhat 122 don khac nhau?", [M(2025, 5, 31)]),
    ("q_e2bfd7f28a9a", "2026-06-13", "Từ đầu tháng này đến hết hôm nay, doanh thu hơn hay kém cùng kỳ tháng rồi mấy %?",
     [("2026-06-01", "2026-06-13"), ("2026-05-01", "2026-05-13")]),
    ("q_ad782d8acb44", "2025-08-14", "Doanh thu tu dau thang den het hom nay so voi cung ky thang truoc chenh bao nhieu phan tram, tang hay giam?",
     [("2025-08-01", "2025-08-14"), ("2025-07-01", "2025-07-14")]),
    ("q_1fc8004b6900", "2026-06-30", "Th này sốp bán ra bnhiu lon sữa bột số 4 z, gộp hết mọi hãng?", [("2026-06-01", "2026-06-30")]),
    ("q_8818447349a6", "2026-09-19", "Th này đơn bán qua Zalo OA dthu bnhiu, còn đơn bán tại quầy bnhiu?", [("2026-09-01", "2026-09-19")]),
    ("q_86198c3c128b", "2025-07-20", "Tuần này 5 mặt hàng nào bán chạy nhất?", [("2025-07-14", "2025-07-20")]),  # CN
    ("q_033e37dde4d5", "2026-06-25", "Tuần này bán được tổng cộng bao nhiêu gói rồi ha?", [("2026-06-22", "2026-06-25")]),
    ("q_eced47bf8f4b", "2026-05-19", "Tuần rồi doanh thu trung bình một đơn là bao nhiêu vậy?", [("2026-05-11", "2026-05-17")]),
    ("q_f0c8b2fbb77f", "2026-01-12", "Hom qua tiem ban duoc bao nhieu tien?", [("2026-01-11", "2026-01-11")]),
    ("q_585664a97754", "2025-11-02", "Hôm qua có mấy đơn rứa?", [("2025-11-01", "2025-11-01")]),
    ("q_128fd07d838e", "2025-08-18", "Tổng cộng hôm qua được mấy đơn?", [("2025-08-17", "2025-08-17")]),
    ("q_102720daf6a9", "2026-02-15", "Năm nay, tính từ đầu năm đến hết hôm nay, doanh thu của cửa hàng là bao nhiêu?", [("2026-01-01", "2026-02-15")]),
    ("q_8fef6f1e6ff0", "2026-02-20", "Hôm nay bán đc mấy đơn rồi ta?", [("2026-02-20", "2026-02-20")]),
    ("q_8e44f654589f", "2026-08-03", "Tới bữa ni khách Triệu Thu Trang tới hạn mấy kỳ góp rồi, tính cả kỳ hạn đúng bữa ni?", [(N, "2026-08-03")]),
    ("q_2bd15109007c", "2026-02-17", "30 ngày gần nhất quán bán đc tổng bnhiu lon vậy?", [("2026-01-19", "2026-02-17")]),
    ("q_3e9418af3a85", "2025-04-23", "Dthu của tiệm trong 7 ngày gần nhất được bnhiu vậy?", [("2025-04-17", "2025-04-23")]),
    ("q_c4f79f600dc0", "2026-05-08", "30 ngay gan nhat doanhthu duoc bao nhieu?", [("2026-04-09", "2026-05-08")]),
    ("q_7672cf2a7ca9", "2026-04-03", "Năm ni, tính từ đầu năm đến hết hôm nay, doanh thu của tiệm được bao nhiêu?", [("2026-01-01", "2026-04-03")]),
    ("q_08f075b54698", "2025-09-12", "Từ đầu năm tới hết hnay tiệm có tổng cộng bnhiu đh rồi?", [("2025-01-01", "2025-09-12")]),
    ("q_9e28638a40cb", "2026-04-04", "Từ đầu năm tới chừ tiệm bán được bao nhiêu tiền rồi?", [("2026-01-01", "2026-04-04")]),
    ("q_20fc83f06434", "2026-09-19", "Quý 3 năm 2025 cửa hàng bán được tổng cộng bao nhiêu tiền?", [("2025-07-01", "2025-09-30")]),
    ("q_37a4aca072a6", "2026-06-24", "Quý 4 năm 2025 tiệm chốt được bao nhiêu o đờ hả?", [("2025-10-01", "2025-12-31")]),
    ("q_a883b37bc197", "2026-09-30", "Quý 4/2025 có mấy lượt khách đem máy tới bảo hành z?", [("2025-10-01", "2025-12-31")]),
    ("q_e096ce51b13d", "2025-04-16", "Từ đầu tháng này tới hết hôm nay, doanh thu của quán hơn hay kém cùng kỳ tháng rồi mấy phần trăm?",
     [("2025-04-01", "2025-04-16"), ("2025-03-01", "2025-03-16")]),
    ("q_07b7be37628b", "2026-06-30", "Tháng này tới hôm nay, doanh thu so với cùng kỳ tháng trước thay đổi bao nhiêu phần trăm?",
     [("2026-06-01", "2026-06-30"), ("2026-05-01", "2026-05-30")]),
    ("q_78a7454fde5d", "2025-12-19", "Từ đầu tháng này tới hết hôm nay, tiền bán so cùng kỳ tháng trước lên hay xuống mấy % z?",
     [("2025-12-01", "2025-12-19"), ("2025-11-01", "2025-11-19")]),
    ("q_403bdc83bc9c", "2026-05-12", "Top 5 khách hàng có công nợ lớn nhất đến hết ngày 12/05/2026 và số nợ của từng người?", [(N, "2026-05-12")]),
    ("q_3e30ac2f9cc9", "2026-05-15", "Đến hết ngày 15/05/2026, cửa hàng còn bao nhiêu khách hàng chưa trả hết nợ?", [(N, "2026-05-15")]),
    ("q_1c0ec38b3d82", "2026-08-21", "Dịp Tết 2026 quán có doanh thu bao nhiêu thế?", [("2026-02-10", "2026-02-26")]),
    ("q_2c8d4481ca57", "2026-08-21", "Dịp Tết âm lịch 2026 so với 17 ngày trước Tết, dthu cao hay thấp hơn mấy %?",
     [("2026-02-10", "2026-02-26"), ("2026-01-24", "2026-02-09")]),
    ("q_2db4f43d56bd", "2025-03-17", "Doanh thu thang nay tinh den hom nay so voi cung ky thang truoc thi tang giam bao nhieu %?",
     [("2025-03-01", "2025-03-17"), ("2025-02-01", "2025-02-17")]),
    ("q_ea97617772d0", "2026-07-22", "Th4/2026 so voi thang lien truoc, doanh thu tang giam bao nhieu %?", [M(2026, 4, 30), M(2026, 3, 31)]),
    ("q_1a312c2f71ac", "2026-06-10", "Nước kiềm Ion Life 19L lên giá 79.500đ ngày 18/10/2025; lượng bình bán trong 30 ngày kể từ 18/10/2025 "
     "thay đổi bao nhiêu % so với 30 ngày ngay trước?", [("2025-10-18", "2025-11-16"), ("2025-09-18", "2025-10-17")]),
    ("q_837cd5892764", "2025-10-22", "Doanh thu tháng này so với cùng số ngày đầu tháng rồi tăng hay giảm mấy %?",
     [("2025-10-01", "2025-10-22"), ("2025-09-01", "2025-09-22")]),
    ("q_4d7022ecbc52", "2026-09-06", "Dthu thang 5/26 so voi thang 5/25 tang truong bnhiu %?", [M(2026, 5, 31), M(2025, 5, 31)]),
    ("q_7bc2b2afb992", "2026-08-08", "So voi thang truoc, doanh thu th5/2026 tang hay giam bao nhieu %?", [M(2026, 4, 30), M(2026, 5, 31)]),
    ("q_f9a0fa4ed236", "2026-07-22", "Đến hết ngày 22/07/2026, các khoản nợ có tuổi quá 10 ngày còn bao nhiêu tiền?", [(N, "2026-07-22")]),
    ("q_d9f743469198", "2026-07-08", "Sua dac Ong Tho 380g len 26k tu 13/11/2025, so hop ban 30 ngay tu hom do so voi 30 ngay truoc tang hay giam bn %?",
     [("2025-11-13", "2025-12-12"), ("2025-10-14", "2025-11-12")]),
    ("q_4b3bc2d8b207", "2025-12-13", "Den het ngay 13/12/2025 tiem con bao nhieu khach no chua tra xong?", [(N, "2025-12-13")]),
    # dạng bổ sung 2026-11-10
    ("q_ef01edd61255", "2026-08-21", "Tháng ba quán có tất cả bao nhiêu đơn bị huỷ thế?", [M(2026, 3, 31)]),
    ("q_0ca19cd317cb", "2026-08-08", "Kể giúp các sp còn lô tồn hết hạn trong 90 ngày tới.", [("2026-08-08", "2026-11-06")]),  # §: D..D+N
    ("q_50152c2cebd6", "2026-05-03", "Đầu năm tới chừ quán được tổng cộng mấy đơn rồi?", [("2026-01-01", "2026-05-03")]),
    ("q_dd2362109dd4", "2026-08-21", "Từ khi Bạc xỉu (M) lên 35.000 đồng/ly, 14 ngày đầu bán nhiều hay ít ly hơn 14 ngày liền trước mấy % z?",
     [(N, N), (N, N)]),  # event_window: metric tự tìm ngày tăng giá
    ("q_682e548226be", "2026-08-08", "Doanh thu 3 thang lien nhau tinh toi 3-2026, tung thang bao nhieu, xep tu thang cu toi thang gan nhat?",
     [M(2026, 1, 31), M(2026, 2, 28), M(2026, 3, 31)]),
    ("q_f09bd63b0751", "2026-07-08", "Doanh thu tu 12/25 den 2/26 co thang sau luon cao hon thang truoc khong?",
     [M(2025, 12, 31), M(2026, 1, 31), M(2026, 2, 28)]),
    ("q_f4a5eee7f524", "2026-05-10", "Từ th 1/25 tới th 3/25, dthu tháng sau đều cao hơn tháng trước hả?",
     [M(2025, 1, 31), M(2025, 2, 28), M(2025, 3, 31)]),
    ("q_964968", "2026-07-22", "Dthu tháng 3, tháng 4, tháng 5 năm 2025 lần lượt bnhiu?", [M(2025, 3, 31), M(2025, 4, 30), M(2025, 5, 31)]),
    ("q_2ac539902fe6", "2026-05-24", "Thang 2/26 so voi cung thang nam truoc, doanh thu thay doi bao nhieu %?", [M(2026, 2, 28), M(2025, 2, 28)]),
]


def main() -> int:
    fails = 0
    for qid, as_of, q, exp in CASES:
        got = [(p["start"], p["end"]) for p in resolve(q, as_of)]
        if got != exp:
            fails += 1
            print(f"FAIL {qid} as_of={as_of} | {q[:90]}\n   expected {exp}\n   got      {got}")
    print(f"\n=== time_resolver public: {len(CASES) - fails}/{len(CASES)} pass ===")
    return 1 if fails > 2 else 0


if __name__ == "__main__":
    sys.exit(main())
