import sqlite3

def check_reconciliation(biz_name, db_path, query):
    print(f"=== KIỂM TRA ĐỐI SOÁT TỔNG: {biz_name} ===")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(query)
        result = cursor.fetchone()
        tong_chi_tiet = result[0] or 0
        tong_hoa_don = result[1] or 0

        print(f"  Tổng chi tiết: {tong_chi_tiet:,}")
        print(f"  Tổng hóa đơn: {tong_hoa_don:,}")

        diff = abs(tong_chi_tiet - tong_hoa_don)
        if diff < 1000:
            print("  ✅ KHỚP HOÀN HẢO!\n")
        else:
            print(f"  ❌ LỆCH {diff:,}đ. Cần kiểm tra lại!\n")
    except Exception as e:
        print(f"  Lỗi query: {e}\n")
    finally:
        conn.close()

# Test biz_106
query_106 = """
SELECT
    (SELECT SUM(thanh_tien) FROM danhsachhoadonchitiet) as chi_tiet,
    (SELECT SUM(tong_sau_giam_gia) FROM danhsachhoadon) as hoa_don
"""
check_reconciliation("BIZ_106", r"tmp_db_test\biz_106.db", query_106)

# Test biz_124
query_124 = """
SELECT
    (SELECT SUM(thanh_tien) FROM banhang_chitiethoadon) as chi_tiet,
    (SELECT SUM(tong_tien) FROM banhang_hoadon) as hoa_don
"""
check_reconciliation("BIZ_124", r"tmp_db_test\biz_124.db", query_124)
