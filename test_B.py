import sqlite3
import pandas as pd
import os
from data_loader import build_database

# ==========================================
# 1. BUILD LẠI DB CỦA BIZ_106 ĐỂ TEST
# ==========================================
print("Đang build lại DB cho biz_106...")
biz_dir = r"data\biz_106"  # Sửa lại đường dẫn nếu máy mày khác
db_path = r"tmp_db_test\biz_106.db"

# Xóa file cũ nếu có để build lại từ đầu
if os.path.exists(db_path):
    os.remove(db_path)

build_database(biz_dir, db_path, verbose=False)
print("Build xong!\n")

conn = sqlite3.connect(db_path)

# ==========================================
# 2. TEST 1: KIỂM TRA ĐỊNH DẠNG NGÀY THÁNG
# ==========================================
print("=== TEST 1: KIỂM TRA ĐỊNH DẠNG NGÀY THÁNG ===")
query_date = "SELECT thoi_gian, thoi_gian_tao FROM danhsachhoadon LIMIT 5"
df_date = pd.read_sql_query(query_date, conn)
print(df_date.to_string(index=False))
# Kỳ vọng: Ngày phải hiện dạng 'YYYY-MM-DD' hoặc 'YYYY-MM-DD HH:MM:SS'

# ==========================================
# 3. TEST 2: KIỂM TRA MÃ SẢN PHẨM (MẤT SỐ 0)
# ==========================================
print("\n=== TEST 2: KIỂM TRA MÃ SẢN PHẨM (MẤT SỐ 0) ===")
query_id = "SELECT ma_hang, ten_hang FROM danhsachsanpham LIMIT 5"
df_id = pd.read_sql_query(query_id, conn)
print(df_id.to_string(index=False))
# Kỳ vọng: Cột ma_hang phải hiện dạng 'SPxxx' hoặc '001' (không bị biến thành 1)

# ==========================================
# 4. TEST 3: KIỂM TRA MÃ MỒ CÔI (STRING NORMALIZATION)
# ==========================================
print("\n=== TEST 3: KIỂM TRA MÃ MỒ CÔI (STRING NORMALIZATION) ===")
query_orphan = """
SELECT DISTINCT ct.ten_hang
FROM danhsachhoadonchitiet ct
LEFT JOIN danhsachsanpham sp ON ct.ten_hang = sp.ten_hang
WHERE sp.ten_hang IS NULL
LIMIT 10
"""
df_orphans = pd.read_sql_query(query_orphan, conn)

if df_orphans.empty:
    print("✅ TUYỆT VỜI! Không còn tên sản phẩm nào bị mồ côi (đã chuẩn hóa chuỗi thành công).")
else:
    print(f"❌ Vẫn còn tên bị mồ côi (Có thể do LLM đọc sai hoặc lỗi font):")
    print(df_orphans.to_string(index=False))

conn.close()
