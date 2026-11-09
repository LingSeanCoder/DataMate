# ==========================================================
# FILE: test_full_A_nhanh.py (Test không xuất .db)
# ==========================================================
import os
from pathlib import Path
import pandas as pd
import time

# Import hàm làm sạch từ file data_loader chính thức của mày
from data_loader import _collect_slices, _group_slices, clean_table

DATA_DIR = r"data"  # Sửa lại đường dẫn nếu cần

def check_duplicates_in_df(df):
    """Đếm số dòng trùng trong 1 DataFrame"""
    return len(df) - len(df.drop_duplicates())

def main():
    data_path = Path(DATA_DIR)
    biz_folders = sorted([d for d in data_path.iterdir() if d.is_dir() and d.name.startswith("biz_")])

    print(f"🚀 BẮT ĐẦU QUÉT SIÊU TỐC {len(biz_folders)} BIZ (Không xuất .db)\n")

    total_dirty = 0
    start_time = time.time()

    for biz_dir in biz_folders:
        biz_name = biz_dir.name
        print(f"--- {biz_name} ---")

        biz_start = time.time()

        try:
            # Đọc file và dò header (tái sử dụng code của data_loader)
            report = {"files": [], "warnings": []}
            slices_start = time.time()
            try:
                slices = _collect_slices(biz_dir, report)
                slices_elapsed = time.time() - slices_start
                print(f"    [Đọc file: {slices_elapsed:.2f}s | {len(slices)} slice]")
            except Exception as e:
                slices_elapsed = time.time() - slices_start
                print(f"    [Đọc file: {slices_elapsed:.2f}s | LỖI: {e}]")
                continue

            grouped = _group_slices(slices)

            biz_dirty = 0
            for raw_name, items in grouped:
                # Gộp các slice
                frame = pd.concat([i.frame for i in items], ignore_index=True, sort=False)
                columns = list(items[0].columns)
                frame = frame.reindex(columns=columns)

                # CHẠY HÀM LÀM SẠCH CỦA MÀY
                originals = list(items[0].original)
                clean_df, stats = clean_table(frame, columns, originals=originals)

                # Đếm xem còn trùng không
                if len(clean_df) > 0:
                    remaining = check_duplicates_in_df(clean_df)
                    if remaining > 0:
                        print(f"  ❌ Bảng '{raw_name}': Vẫn còn {remaining:,} dòng trùng!")
                        biz_dirty += remaining

            biz_elapsed = time.time() - biz_start
            if biz_dirty == 0:
                print(f"  ✅ SẠCH ({biz_elapsed:.1f}s)")
            else:
                total_dirty += biz_dirty
                print(f"  ⚠️  Còn {biz_dirty:,} dòng trùng ({biz_elapsed:.1f}s)")

        except Exception as e:
            biz_elapsed = time.time() - biz_start
            print(f"  ❌ LỖI: {e} ({biz_elapsed:.1f}s)")

    elapsed = time.time() - start_time
    print("\n" + "="*40)
    print(f"⏱️  Thời gian tổng: {elapsed:.1f}s")
    if total_dirty == 0:
        print("🏆 TẤT CẢ CÁC BIZ ĐỀU SẠCH! Code của mày bất tử!")
    else:
        print(f"⚠️  Còn tổng cộng {total_dirty:,} dòng trùng trên toàn hệ thống.")
    print("="*40)

if __name__ == "__main__":
    main()
