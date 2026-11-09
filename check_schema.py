import sqlite3
import pandas as pd

DB_PATH = "business_data.db"

def get_schema():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Lấy danh sách các bảng
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]

    schema_info = {}

    for table in tables:
        # Lấy tên các cột và kiểu dữ liệu
        cursor.execute(f"PRAGMA table_info({table});")
        columns = cursor.fetchall()
        col_names = [col[1] for col in columns]

        # Lấy 3 dòng dữ liệu mẫu để xem format (ngày tháng, số tiền...)
        df = pd.read_sql_query(f"SELECT * FROM {table} LIMIT 3", conn)

        schema_info[table] = {
            "columns": col_names,
            "sample_data": df.to_dict(orient='records')
        }

    conn.close()
    return schema_info

if __name__ == "__main__":
    schema = get_schema()

    for table_name, info in schema.items():
        print(f"\n--- BẢNG: {table_name} ---")
        print(f"Các cột: {', '.join(info['columns'])}")
        print(f"Dữ liệu mẫu (3 dòng):")
        for row in info['sample_data']:
            print(f"  {row}")
