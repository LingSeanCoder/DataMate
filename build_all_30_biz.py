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

        # --- BƯỚC QUAN TRỌNG: DỌN DẸP SQLITE LẦN CUỐI ---
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '_meta%';")
        tables = [r[0] for r in cursor.fetchall()]

        cleaned_rows = 0
        for t in tables:
            try:
                # Xóa trùng ở cấp độ SQLite (nếu có)
                cursor.execute(f'CREATE TEMP TABLE _temp AS SELECT DISTINCT * FROM "{t}"')
                cursor.execute(f'SELECT COUNT(*) FROM "{t}"')
                orig_count = cursor.fetchone()[0]
                cursor.execute('SELECT COUNT(*) FROM _temp')
                new_count = cursor.fetchone()[0]

                if orig_count > new_count:
                    cursor.execute(f'DELETE FROM "{t}"')
                    cursor.execute(f'INSERT INTO "{t}" SELECT * FROM _temp')
                    cleaned_rows += (orig_count - new_count)
                cursor.execute('DROP TABLE _temp')
            except: pass

        conn.commit()
        conn.close()
        # -----------------------------------------------

        print(f"✅ Xong (dọn thêm {cleaned_rows} dòng trùng trong SQLite)")
        success_count += 1
    except Exception as e:
        print(f"❌ LỖI: {e}")

elapsed = time.time() - start_time
print(f"\n🏆 HOÀN TẤT: Build thành công {success_count}/{len(biz_list)} biz trong {elapsed/60:.1f} phút.")
