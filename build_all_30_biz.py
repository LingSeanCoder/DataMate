import os
import shutil
import sqlite3
from data_loader import build_database
import time

# 1. XÓA THƯ MỤC CŨ ĐỂ LÀM LẠI TỪ ĐẦU
DB_DIR = "tmp_db"
if os.path.exists(DB_DIR):
    print(f"Đang xóa thư mục cũ: {DB_DIR}...")
    shutil.rmtree(DB_DIR)
os.makedirs(DB_DIR, exist_ok=True)

# 2. BUILD LẠI TOÀN BỘ 30 BIZ
DATA_DIR = "data"
biz_list = [f"biz_{i}" for i in range(101, 131)]

print("🚀 BẮT ĐẦU BUILD LẠI TOÀN BỘ 30 BIZ VỚI CODE MỚI...\n")
start_time = time.time()

success_count = 0
for biz in biz_list:
    biz_path = os.path.join(DATA_DIR, biz)
    db_path = os.path.join(DB_DIR, f"{biz}.db")

    if not os.path.exists(biz_path):
        continue

    try:
        print(f"⏳ Đang build {biz}...", end=" ")
        build_database(biz_path, db_path, verbose=False)

        # Không DISTINCT toàn bảng: dòng trùng ở bảng không có mã đơn là 2 lần bán thật (§3.3); data_loader đã dedupe trong từng đơn.
        print("✅ Xong")
        success_count += 1
    except Exception as e:
        print(f"❌ LỖI: {e}")

elapsed = time.time() - start_time
print(f"\n🏆 HOÀN TẤT: Build thành công {success_count}/{len(biz_list)} biz trong {elapsed/60:.1f} phút.")
