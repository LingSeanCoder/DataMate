import pandas as pd
import sqlite3
import os
import glob
import re

DATA_DIR = "."
DB_PATH = "business_data.db"

def clean_currency(val):
    if pd.isna(val): return 0
    val = str(val).replace('đ', '').replace(',', '').replace('.', '').strip()
    try:
        return float(val)
    except:
        return 0

def clean_date(val):
    if pd.isna(val): return None
    val = str(val).strip()
    if re.match(r'\d{2}/\d{2}/\d{4}', val):
        parts = val.split('/')
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    elif '-' in val:
        return val.split(' ')[0]
    return val

def build_database():
    print("Đang làm sạch dữ liệu và tạo Database...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Xóa bảng cũ nếu đã tồn tại để tránh lỗi cấu trúc
    cursor.execute("DROP TABLE IF EXISTS DanhSachHoaDonChiTiet")
    conn.commit()

    for file in glob.glob(f"{DATA_DIR}/*.xlsx"):
        filename = os.path.basename(file).replace('.xlsx', '')

        if "DanhSachHoaDon" in filename:
            df = pd.read_excel(file, header=1)
            if "ChiTiet" not in filename:
                # Xử lý bảng Hóa Đơn
                df.columns = ['Mã_hóa_đơn', 'Thời_gian', 'Mã_KH', 'Khách_hàng', 'Tổng_tiền_hàng', 'Giảm_giá', 'Tổng_sau_giảm', 'Khách_đã_trả', 'PT_thanh_toán', 'Trạng_thái', 'Thời_gian_tạo', 'Ghi_chú']
                for col in ['Tổng_tiền_hàng', 'Giảm_giá', 'Tổng_sau_giảm', 'Khách_đã_trả']:
                    df[col] = df[col].apply(clean_currency)
                df['Thời_gian'] = df['Thời_gian'].apply(clean_date)
                df.to_sql('DanhSachHoaDon', conn, if_exists='replace', index=False)
            else:
                # Xử lý bảng Chi Tiết Hóa Đơn
                df.columns = ['Mã_hóa_đơn', 'STT', 'Mã_hàng', 'Tên_hàng', 'ĐVT', 'Số_lượng', 'Đơn_giá', 'Giảm_giá', 'Thành_tiền', 'SL_quy_đổi', 'Giá_vốn']
                for col in ['Đơn_giá', 'Giảm_giá', 'Thành_tiền', 'Giá_vốn']:
                    df[col] = df[col].apply(clean_currency)
                # Dùng append để gộp chung 2 file _1 và _2 vào 1 bảng
                df.to_sql('DanhSachHoaDonChiTiet', conn, if_exists='append', index=False)

        else:
            df = pd.read_excel(file)
            df.columns = [str(c).strip().replace(" ", "_").replace("-", "_").replace("/", "_") for c in df.columns]

            if filename == "SoQuy":
                df['Giá_trị'] = df['Giá_trị'].apply(clean_currency)
                df['Thời_gian'] = df['Thời_gian'].apply(clean_date)
            elif filename == "DanhSachNhapHang":
                df['Cần_trả_NCC'] = df['Cần_trả_NCC'].apply(clean_currency)
                df['Thời_gian'] = df['Thời_gian'].apply(clean_date)
            elif filename == "DanhSachChiTietNhapHang":
                df['Đơn_giá'] = df['Đơn_giá'].apply(clean_currency)

            df.to_sql(filename, conn, if_exists='replace', index=False)

        print(f"✅ Đã xử lý xong: {filename}")

    conn.close()
    print(f"\n🎉 Database sạch đã sẵn sàng tại: {DB_PATH}")

if __name__ == "__main__":
    build_database()
