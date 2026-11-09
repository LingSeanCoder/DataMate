import sqlite3

conn = sqlite3.connect("business_data.db")
cursor = conn.cursor()

print("--- TÌM CÁC DÒNG RÁC TRONG THÁNG 6/2025 ---")
# 1. Nhóm theo trạng thái
cursor.execute("SELECT Trạng_thái, COUNT(*) FROM DanhSachHoaDon WHERE Thời_gian >= '2025-06-01' AND Thời_gian < '2025-07-01' GROUP BY Trạng_thái;")
print("Thống kê trạng thái:")
for row in cursor.fetchall():
    print(f"  {row[0]}: {row[1]} dòng")

# 2. Tìm các dòng có Mã hóa đơn bị lỗi (rỗng, hoặc chứa chữ 'Mã hóa đơn')
print("\nCác dòng nghi vấn (Mã hóa đơn bị lỗi):")
cursor.execute("SELECT Mã_hóa_đơn, Thời_gian, Trạng_thái FROM DanhSachHoaDon WHERE Mã_hóa_đơn IS NULL OR Mã_hóa_đơn = 'Mã hóa đơn' OR Mã_hóa_đơn = '' LIMIT 10;")
for row in cursor.fetchall():
    print(f"  Mã: '{row[0]}' | Ngày: {row[1]} | Trạng thái: '{row[2]}'")

conn.close()
