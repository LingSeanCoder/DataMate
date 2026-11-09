import sqlite3
import pandas as pd
import os
import glob
import re

# SỬA ĐƯỜNG DẪN NÀY TRỎ ĐẾN THƯ MỤC CHỨA CÁC FILE .DB CỦA MÀY
DB_DIR = r"tmp_db"

def check_date_format(val):
    if pd.isna(val) or val is None: return False
    val = str(val).strip()
    # Chấp nhận YYYY-MM-DD hoặc YYYY-MM-DD HH:MM:SS
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}", val))

def inspect_db(db_path):
    biz_name = os.path.basename(db_path).replace('.db', '')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '_meta%' AND name NOT LIKE 'sqlite_%';")
    tables = [r[0] for r in cursor.fetchall()]

    report = {
        "biz": biz_name,
        "duplicates": [],
        "null_warnings": [],
        "type_warnings": []
    }

    for t in tables:
        # 1. Check Duplicates (Trùng lặp)
        try:
            cursor.execute(f'SELECT COUNT(*) FROM "{t}"')
            total = cursor.fetchone()[0]
            cursor.execute(f'SELECT COUNT(*) FROM (SELECT DISTINCT * FROM "{t}")')
            distinct = cursor.fetchone()[0]
            if total != distinct:
                report["duplicates"].append(f"{t} (dư {total-distinct:,} dòng)")
        except: pass

        # 2. Check Nulls & Types
        try:
            df = pd.read_sql_query(f'SELECT * FROM "{t}" LIMIT 100', conn)
            for col in df.columns:
                col_name = col.lower()

                # --- CHECK NULLS ---
                # Bỏ qua các cột thường xuyên rỗng (mã số thuế, barcode, mã khuyến mãi, ghi chú...)
                ignore_null_keywords = ['thue', 'barcode', 'vach', 'khuyenmai', 'ghi_chu', 'mo_ta', 'dia_chi', 'email', 'dien_thoai', 'sdt', 'cccd', 'cmnd', 'ma_kh']
                is_ignore_null = any(k in col_name for k in ignore_null_keywords)

                if not is_ignore_null and any(k in col_name for k in ['ma_', 'id', 'code']):
                    cursor.execute(f'SELECT SUM(CASE WHEN "{col}" IS NULL THEN 1 ELSE 0 END), COUNT(*) FROM "{t}"')
                    null_count, total_count = cursor.fetchone()
                    if total_count > 0 and null_count / total_count > 0.9:
                        report["null_warnings"].append(f"{t}.{col} rỗng >90%")

                # --- CHECK DATE FORMAT ---
                if any(k in col_name for k in ['ngay', 'thoi_gian', 'date', 'hsd']):
                    sample_vals = df[col].dropna().tolist()
                    if sample_vals:
                        bad_dates = sum(1 for v in sample_vals if not check_date_format(v))
                        if bad_dates > 0:
                            report["type_warnings"].append(f"{t}.{col} lỗi format ngày")

                # --- CHECK NUMBER FORMAT (Chống báo động giả) ---
                # Chỉ check các cột thực sự là tiền hoặc số lượng (loại trừ mã giao dịch, loại giao dịch)
                is_money_qty = any(k in col_name for k in ['thanh_tien', 'tong_tien', 'tong_sau', 'don_gia', 'gia_ban', 'gia_nhap', 'so_luong', 'sl_', 'slg', 'soluong'])
                is_code_col = any(k in col_name for k in ['ma_', 'loai_', 'cach_', 'id'])

                if is_money_qty and not is_code_col:
                    sample_vals = df[col].dropna().tolist()
                    # Nếu có chữ cái trong cột tiền -> Lỗi
                    bad_nums = sum(1 for v in sample_vals if isinstance(v, str) and re.search(r'[a-zA-Zđ]', v))
                    if bad_nums > 0:
                        report["type_warnings"].append(f"{t}.{col} chứa chữ (lỗi Number)")
        except: pass

    conn.close()
    return report

def main():
    db_files = glob.glob(os.path.join(DB_DIR, "*.db"))
    print(f"🔍 BẮT ĐẦU KIỂM ĐỊNH TOÀN BỘ {len(db_files)} FILE DATABASE\n")

    if len(db_files) == 0:
        print("❌ Không tìm thấy file .db nào. Kiểm tra lại đường dẫn DB_DIR!")
        return

    found_any = False
    total_issues = 0
    passed_bizs = 0

    for db in sorted(db_files):
        rep = inspect_db(db)
        issues = len(rep["duplicates"]) + len(rep["null_warnings"]) + len(rep["type_warnings"])

        if issues > 0:
            total_issues += issues
            print(f"❌ {rep['biz']}: Phát hiện {issues} vấn đề!")
            # Chỉ in tối đa 5 lỗi mỗi loại để màn hình không bị trôi
            for d in rep["duplicates"][:5]: print(f"   - TRÙNG LẶP: {d}")
            for n in rep["null_warnings"][:5]: print(f"   - RỖNG: {n}")
            for t in rep["type_warnings"][:5]: print(f"   - SAI TYPE: {t}")
            if issues > 15: print("   ... (còn nhiều lỗi khác bị ẩn bớt)")
        else:
            passed_bizs += 1
            print(f"✅ {rep['biz']}: Hoàn hảo 100%")

    print(f"\n==========================================")
    if total_issues == 0:
        print(f"🏆 CHÚC MỪNG! TOÀN BỘ {passed_bizs} DATABASE ĐÃ ĐẠT CHUẨN ĐỂ ĐƯA CHO LLM ĐỌC.")
    else:
        print(f"⚠️ Có {passed_bizs}/{len(db_files)} biz sạch hoàn toàn.")
        print(f"⚠️ Còn tổng cộng {total_issues} vấn đề cần fix trên toàn hệ thống.")
    print(f"==========================================")

if __name__ == "__main__":
    main()
