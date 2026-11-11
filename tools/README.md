# tools/ — Công cụ chấm điểm & kiểm tra

Không nằm trong Docker. Chạy từ thư mục gốc repo.

| File | Dùng để | Lệnh |
|---|---|---|
| `eval.py` | Chấm `submission.jsonl` với `answers.jsonl` theo rulebook §9 (đúng/sai/bỏ trống). | `python tools/eval.py --sub submission.jsonl --answers answers.jsonl --wrong 30` |
| `check_std.py` | Đối chiếu doanh thu `std_*` với đáp án train, không cần LLM. | `python tools/check_std.py --answers answers.jsonl` |
| `validate_std.py` | Đối soát `std_*` với cột tổng tiền do chính file ghi, cho mọi shop (kỳ vọng 14/14). | `python -W ignore tools/validate_std.py --db-dir tmp_db_dev` |
| `validate_db.py` | Kiểm định chất lượng các DB do `data_loader` tạo. | `python tools/validate_db.py` |
| `inventory.py` | Dựng DB cho mọi shop và in bảng kiểm kê schema. | `python tools/inventory.py --data-root <data> --db-dir tmp_db` |
