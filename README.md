# DataMate - Zalo AI Challenge 2026

## 📌 Giới thiệu
Đây là mã nguồn giải pháp cho bài toán Text-to-SQL (DataMate). Hệ thống sử dụng kiến trúc Agent Schema-Agnostic, có khả năng tự động nhận diện cấu trúc bảng/cột của bất kỳ doanh nghiệp nào mà không cần hardcode. Agent tự động sinh Dynamic Few-shot để hướng dẫn mô hình ngôn ngữ viết câu lệnh SQLite chính xác.

## 🧠 Khai báo Model (Theo Rule 2)
Hệ thống chỉ sử dụng **DUY NHẤT 1 model** tại thời điểm inference:
- **Model:** `Qwen2.5-Coder-14B-Instruct` (định dạng GGUF, lượng tử hóa Q4_K_M).
- **Số lượng tham số:** 14 tỷ (< 15 tỷ, thỏa mãn điều kiện cuộc thi).
- **Engine:** `llama.cpp` (biên dịch từ source với CUDA hỗ trợ kiến trúc Blackwell RTX 5090).

## ⚙️ Kiến trúc Pipeline
1. **Data Loading:** Đọc file Excel/CSV, làm sạch dữ liệu (tiền tệ, ngày tháng) và đẩy vào SQLite (`data_loader.py`).
2. **Schema Linking:** Quét DB để tự động nhận diện vai trò các bảng (Bảng Đơn hàng, Bảng Danh mục, Bảng Lô hàng...) dựa trên kiểu dữ liệu và giao giá trị.
3. **Prompting:** Sinh System Prompt động kèm Few-shot mẫu dựa trên schema thực tế của từng biz.
4. **Execution:** Gọi Local LLM Server (`llama-server`) qua API OpenAI-compatible để sinh SQL, chạy thử trên SQLite, tự động sửa lỗi (self-correction) và format JSON kết quả.

### Bảng chuẩn `std_*` (tạo tự động cuối `build_database`, `std_layer.py`)
Cùng tên cột cho mọi shop; đã áp luật trạng thái/doanh thu/dedupe (datamate.md §3, §4.1). Ưu tiên query 2 bảng này thay vì bảng gốc.

| Bảng | Cột | Ghi chú |
|---|---|---|
| `std_orders` (1 dòng/đơn) | `order_id, order_date` (YYYY-MM-DD), `order_time` (HH:MM:SS), `status_raw, status_norm, is_valid, customer_id, customer_name, pay_method, revenue, n_lines` | `status_norm` ∈ `hoàn thành`/`huỷ`/`trả hàng`/`đang giao`; đơn có bất kỳ dòng trả = `trả hàng`. `is_valid=1` = không huỷ, không trả (đang giao vẫn tính). `revenue` = tổng thành tiền dòng hàng (đã trừ giảm giá dòng, chưa trừ giảm cả đơn, không ship, không VAT, có phí phục vụ). `pay_method` là giá trị gốc, chưa chuẩn hoá. |
| `std_lines` (1 dòng/dòng hàng) | `order_id, line_no, product_id, product_name, unit, qty, qty_base, line_revenue, line_cost, is_service_fee` | `qty_base` = số lượng quy về đơn vị cơ bản. `product_name` đã gom các cách viết hoa/thường/dấu. `is_service_fee=1` là "Phí phục vụ": tính vào doanh thu, không xếp hạng mặt hàng. `line_cost = qty × giá vốn ghi trên dòng` (NULL nếu shop không có). |
| `_meta_std` | `role, source` | Cột gốc nào được dùng cho từng vai trò, để debug. |

Ví dụ: doanh thu tháng 5/2025 = `SELECT SUM(revenue) FROM std_orders WHERE is_valid=1 AND order_date BETWEEN '2025-05-01' AND '2025-05-31'`.
Kiểm tra với đáp án train (không cần LLM): `python check_std.py --answers answers.jsonl`.

## 🚀 Hướng dẫn chạy Docker

> Chạy thử từng bước cho thành viên (Docker hoặc Python, có lệnh so kết quả): xem [`TEST.md`](TEST.md).

Image hiện tại là **bản chỉ chạy luật** (không cần GPU, không cần mạng): mỗi câu được trả lời bằng luật + `metrics.py` trên bảng `std_*`; câu luật không chắc thì bỏ trống. Bản có LLM sẽ thêm sau khi đo xong Phase 6 (`colab_phase6.ipynb`).

```bash
docker build -t datamate-submission .                       # máy chấm x86: thêm --platform linux/amd64
docker run --rm --network=none \
  -v /duong/dan/bo_de:/data:ro \
  -v /duong/dan/ket_qua:/result \
  datamate-submission
```

- `/data` là **bộ đề nguyên**: `questions.jsonl`, `data/<biz>/...`, `rulebook.md`, `calendar.csv` (rulebook và lịch của bộ đề được ưu tiên hơn bản trong image). Nếu chỉ mount các thư mục shop thì thêm `-e QUESTIONS=/duong/dan/questions.jsonl`.
- Kết quả: `/result/submission.jsonl` (mỗi dòng `{"id","type","answer"}`), log ở `/result/logs/`, DB tạm ở `/result/tmp_db/`.
- Đã thử: 1000 câu public, `--network=none`, 6,2 phút trên MacBook Air M3 (CPU), kết quả trùng khớp chạy ngoài Docker.
- Biến môi trường: `WORKERS` (mặc định 4), `USE_LLM=1` (chỉ dùng khi image có llama-server — chưa có).

## 🖥️ Chạy trực tiếp (không Docker)

```bash
# 1. Cài thư viện
pip install -r requirements.txt

# 2. Khởi động llama-server với model GGUF
llama-server -m /path/to/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8000 -c 8192 -ngl 99

# 3. Chạy inference
python main.py --data-root ./data --skip-build \
  --out ./submission.jsonl --log ./run_log.jsonl
```

Chạy thử nhanh 5 câu mỗi biz trước khi chạy full:

```bash
python main.py --data-root ./data --limit-per-biz 5 --overwrite --show-sql
```

## 🔧 Biến môi trường

| Biến | Ý nghĩa |
|---|---|
| `LOCAL_LLM_BASE_URL` | Base URL của `llama-server` (mặc định `http://localhost:8000/v1`) |
| `LOCAL_LLM_API_KEY` | API key cho server local (mặc định `local`) |
| `LOCAL_LLM_MODEL` | Tên model báo cho server (mặc định `local`) |
| `NIM_API_KEY` | (Dự phòng) API key NVIDIA NIM nếu không dùng server local |
| `DATAMATE_PROMPT_BUDGET` | Trần token input mỗi câu (mặc định 6300) |

## 📁 Cấu trúc thư mục

```
.
├── agent.py          # Agent: schema-agnostic detection, prompt, SQL, self-correction
├── data_loader.py    # Đọc Excel/CSV/SQLite → chuẩn hoá → 1 DB SQLite mỗi biz
├── main.py           # Chạy batch 3000 câu, resume từ submission.jsonl
├── requirements.txt  # Thư viện Python cần thiết
├── rulebook.md       # Quy tắc nghiệp vụ chung (doanh thu, thời gian, format)
├── Dockerfile        # Đóng gói image chạy inference
└── .dockerignore
```

## 📦 File `.dockerignore` (Cực kỳ quan trọng)
Để tránh việc Docker copy nhầm những file rác vào trong Image (làm phình dung lượng), repo đã có sẵn `.dockerignore` (ngang hàng với `Dockerfile`):

```text
data/
tmp_db/
.env
*.jsonl
*.xlsx
*.log
check_schema.py
debug.py
check_score.py
*.report.json
.schema_cache.json
__pycache__/
*.pyc
```

## 👥 Thành viên
[Thành viên 1]

[Thành viên 2]
