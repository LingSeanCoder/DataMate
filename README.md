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

## 🚀 Hướng dẫn chạy Docker
Hệ thống đã được đóng gói hoàn chỉnh. Để chạy:

```bash
# Build image
docker build -t datamate-submission .

# Chạy inference (giả sử dữ liệu test nằm ở /path/to/test_data)
docker run --gpus all --rm \
  -v /path/to/test_data:/data \
  -v /path/to/result_dir:/result \
  -e LOCAL_LLM_BASE_URL=http://host.docker.internal:8000/v1 \
  datamate-submission
```

Kết quả sẽ được ghi ra file `/result/submission.jsonl` (và log chi tiết ở `/result/logs/`).

> **Lưu ý:** `llama-server` chạy trên máy host (hoặc container riêng) và mở cổng `8000`.
> Nếu dùng mạng Docker nội bộ, thay `host.docker.internal` bằng tên service tương ứng.

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
