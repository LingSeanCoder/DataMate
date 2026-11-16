# DataMate - Zalo AI Challenge 2026

## 📌 Giới thiệu
Image dùng luật và Python/SQLite trong `src/` trước; khi luật trả `abstain`, `src/agent.py` gọi vLLM nội bộ để sinh SQL, thực thi truy vấn chỉ đọc và định dạng đáp án. `predict.sh` tự khởi động server GPU, chờ model sẵn sàng rồi chạy pipeline; không gọi API bên ngoài. Không phải mọi câu hỏi đều gọi model. Lỗi server lúc khởi động làm tiến trình thất bại, log ở `/result/logs/vllm.log`.

Model inference duy nhất: [Qwen/Qwen2.5-Coder-14B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-14B-Instruct), revision `aedcc2d42b622764e023cf882b6652e646b95671`, phát hành 06/11/2024, Apache-2.0, 14.770.033.664 tham số (dưới giới hạn 15B). BF16, tensor parallel 2 GPU, context 16384, seed 42, temperature 0; tokenizer và sáu shard phải nằm trong image. Server dùng vLLM 0.10.1.1/CUDA 12.8, theo stack Blackwell được guideline khuyến nghị. Đây là checkpoint gốc, không có adapter trong pipeline.

**Chưa có weights trong repo/image:** phải tải và build theo hướng dẫn dưới đây. Chưa kiểm thử image hoặc inference GPU tại đây; không xem cấu hình là bằng chứng chạy thành công.

Trong image: `/code/src/` là mã inference, `/code/models/qwen2.5-coder-14b-instruct/` chứa checkpoint và manifest SHA-256, `/code/training_code/data/` là bản sao dữ liệu mẫu trong repo, `/code/training_code/data_manifest.json` liệt kê đường dẫn, kích thước và SHA-256 từng file dữ liệu. Dữ liệu được mô tả là 30 cửa hàng train, nhưng nguồn/version gốc, số mẫu train/validation, câu hỏi và đáp án train không được ghi nhận đầy đủ. Chưa có bằng chứng xác minh việc huấn luyện hoặc tạo dữ liệu trong lịch sử dự án; đội phải xác minh và bổ sung dữ liệu, mã, cấu hình, log, dependency và checkpoint nếu thực tế có sử dụng. Không dùng dữ liệu hoặc đáp án public/private test để train. Provenance và bằng chứng training chưa xác minh là blocker nộp.

### Bảng chuẩn `std_*` (tạo tự động cuối `build_database`, `src/std_layer.py`)
Cùng tên cột cho mọi shop; đã áp luật trạng thái/doanh thu/dedupe (rulebook §3, §4.1). Ưu tiên query 2 bảng này thay vì bảng gốc.

| Bảng | Cột | Ghi chú |
|---|---|---|
| `std_orders` (1 dòng/đơn) | `order_id, order_date` (YYYY-MM-DD), `order_time` (HH:MM:SS), `status_raw, status_norm, is_valid, customer_id, customer_name, pay_method, revenue, n_lines` | `status_norm` ∈ `hoàn thành`/`huỷ`/`trả hàng`/`đang giao`; đơn có bất kỳ dòng trả = `trả hàng`. `is_valid=1` = không huỷ, không trả (đang giao vẫn tính). `revenue` = tổng thành tiền dòng hàng (đã trừ giảm giá dòng, chưa trừ giảm cả đơn, không ship, không VAT, có phí phục vụ). `pay_method` là giá trị gốc, chưa chuẩn hoá. |
| `std_lines` (1 dòng/dòng hàng) | `order_id, line_no, product_id, product_name, unit, qty, qty_base, line_revenue, line_cost, is_service_fee` | `qty_base` = số lượng quy về đơn vị cơ bản. `product_name` đã gom các cách viết hoa/thường/dấu. `is_service_fee=1` là "Phí phục vụ": tính vào doanh thu, không xếp hạng mặt hàng. `line_cost = qty × giá vốn ghi trên dòng` (NULL nếu shop không có). |
| `_meta_std` | `role, source` | Cột gốc nào được dùng cho từng vai trò, để debug. |

Ví dụ: doanh thu tháng 5/2025 = `SELECT SUM(revenue) FROM std_orders WHERE is_valid=1 AND order_date BETWEEN '2025-05-01' AND '2025-05-31'`.
Kiểm tra với đáp án train (không cần LLM): `python tools/check_std.py --answers answers.jsonl`.

## Chuẩn bị và chạy GPU offline

Trên máy có mạng và đủ dung lượng trống (khoảng 30 GB weights, thêm chỗ cho image/cache/export), từ gốc repo:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install 'huggingface_hub==0.34.4'
python tools/prepare_model.py
docker build --platform linux/amd64 -t zaic2026_datamate_team:final .
```

Script tải đúng revision và tạo manifest SHA-256; build thất bại nếu thiếu thư mục hoặc manifest. Sau build mọi inference assets phải nằm trong image, không mount checkpoint từ host. Docker build cần mạng để lấy base image/dependencies. Dùng host Linux amd64 với hai RTX 5090, driver phù hợp CUDA 12.8 (R570+), Docker và NVIDIA Container Toolkit. Build cross-platform trên ARM không chứng minh GPU hoạt động.

Chạy hai lần bằng bộ đề đầy đủ (`questions.jsonl`, `data/`, `rulebook.md`, `calendar.csv`), đầu vào read-only và mạng tắt:

```bash
mkdir -p out1 out2
for out in out1 out2; do
  docker run --rm --gpus '"device=0,1"' --network none --shm-size=16g \
    --entrypoint bash -v /absolute/path/to/public_test:/data:ro \
    -v "$(pwd)/$out:/result" zaic2026_datamate_team:final /code/predict.sh
done
```

Startup cho phép tối đa 900 giây; kiểm tra `out1/logs/vllm.log` nếu thất bại. Toàn bộ loading và inference phải dưới 6 giờ. Máy CPU có thể kiểm thử riêng pipeline luật bằng `python src/main.py --data-root data --questions /path/to/questions.jsonl --out /tmp/submission.jsonl --log /tmp/run_log.jsonl --db-dir /tmp/datamate-db --overwrite --no-llm`; đây không phải đường chạy image nộp hoặc kiểm thử model.

Kiểm tra đủ câu và log, so sánh theo ID (thứ tự kết quả có thể khác):

```bash
python -c 'import json; from pathlib import Path; load=lambda p:[json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]; q=load("/absolute/path/to/public_test/questions.jsonl"); a=load("out1/submission.jsonl"); b=load("out2/submission.jsonl"); l=load("out1/logs/run_log.jsonl"); ids=lambda rows:[str(r["id"]) for r in rows]; assert len(ids(q))==len(set(ids(q))) and set(ids(q))==set(ids(a))==set(ids(b))==set(ids(l)) and len(q)==len(a)==len(b)==len(l); assert all(isinstance(r["elapsed_ms"],int) and r["elapsed_ms"]>=0 and "thinking" in r and "answer" in r for r in l); assert {str(r["id"]):r for r in a}=={str(r["id"]):r for r in b}; print("Coverage, logs and repeatability OK")'
```

Kiểm tra này không chứng minh điểm, runtime trên máy BTC, tính hợp lệ provenance hay đầy đủ artifacts. Trước khi nộp phải chạy thực tế hai GPU offline, kiểm tra thời gian 6 giờ và tính ổn định/PA, image `linux/amd64` và artifacts. Đổi `team` thành tên team chữ thường. Export bằng `set -o pipefail; docker save zaic2026_datamate_team:final | gzip > zaic2026_datamate_team.tar.gz`; kiểm tra `gzip -dc zaic2026_datamate_team.tar.gz | wc -c` không vượt 100 GB, tạo MD5 bằng `md5 -r` (macOS) hoặc `md5sum` (Linux), rồi gửi archive, checksum và kích thước tar theo guideline.

## 📁 Cấu trúc thư mục

```
.
├── src/            Mã nguồn chính (chạy cả trong Docker)      → src/README.md
├── tools/          Công cụ chấm điểm / kiểm tra dữ liệu       → tools/README.md
├── tests/          Test tự động (không cần mạng, không GPU)   → tests/README.md
├── docs/           Tài liệu kiến trúc, runbook, handoff       → docs/README.md
├── data/           Dữ liệu mẫu 30 cửa hàng (bộ train)         → data/README.md
├── rulebook.md     Luật nghiệp vụ của bộ đề (đọc lúc chạy)
├── calendar.csv    Lịch ngày lễ/sự kiện (đọc lúc chạy)
├── predict.sh      Điểm vào inference của Docker
├── Dockerfile / .dockerignore / requirements.txt
└── docs/index.html Trang HTML xem nhanh toàn bộ hướng dẫn
```

Mọi lệnh chạy từ **thư mục gốc repo** (vì `calendar.csv`, `rulebook.md` đọc theo thư mục hiện tại).

## 🔎 Xem hướng dẫn dễ hơn
Mở `docs/index.html` bằng trình duyệt (không cần server).
