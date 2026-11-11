# src/ — Mã nguồn chính

Pipeline: **đọc dữ liệu → bảng chuẩn `std_*` → luật + metrics → đáp án**. Bản nộp chạy `--no-llm` (chỉ luật). Chạy từ thư mục gốc repo: `python src/main.py -h`.

| File | Chức năng |
|---|---|
| `main.py` | Điểm vào. Chạy hàng loạt `questions.jsonl` → `submission.jsonl`, nhóm theo shop, resume được, chặn đáp án null. |
| `data_loader.py` | Đọc Excel/CSV của mỗi shop, làm sạch tiền/ngày, tạo 1 DB SQLite mỗi shop (`build_database`). |
| `std_layer.py` | Tạo `std_orders` (1 dòng/đơn) và `std_lines` (1 dòng/dòng hàng) với tên cột cố định cho mọi shop. |
| `std_extra.py` | Bảng phụ: tiền đơn, phiếu thu/công nợ, nhập hàng. |
| `schema_roles.py` | Đoán vai trò từng bảng/cột theo tên cột, kiểu, độ duy nhất (không theo tên shop). |
| `run_rules.py` | Bộ định tuyến regex: câu hỏi → hàm trong `metrics.py` → đáp án. Có thể chạy riêng để chấm train. |
| `metrics.py` | Hàm tính số liệu (doanh thu, top-k, tồn kho, công nợ, nhập hàng, tăng giá…). Deterministic. |
| `time_resolver.py` | Hiểu cách nói thời gian ("tháng trước", "tuần này", "Q1/2026", teencode…). |
| `entity.py` | Rút tên hàng/khách/hoạt chất từ câu hỏi và khớp với giá trị thật trong DB. |
| `formatter.py` | Định dạng đáp án theo rulebook §9 (làm tròn, đơn vị, danh sách). |
| `count_intents.py` | Phân loại thô câu hỏi theo họ intent; cũng là bộ chuẩn hoá câu dùng chung. |
| `agent.py` | Nhánh LLM text-to-SQL (Qwen qua llama-server). **Đang tắt** trong bản nộp. |

## Lệnh hay dùng
```bash
# Chạy 1000 câu public (luật, không LLM)
python src/main.py --data-root <public_test>/data --questions <public_test>/questions.jsonl \
  --db-dir tmp_db_dev --rebuild --no-llm --overwrite --out submission.jsonl --log run_log.jsonl

# Chấm luật trên train (kỳ vọng 2962/3000)
python -W ignore src/run_rules.py --answers <train>/answers.jsonl --wrong 0 | tail -1

# Dựng DB cho 1 shop
python src/data_loader.py --biz-dir data/biz_103 --db tmp_db/biz_103.db
```
