# Chạy thử DataMate trên máy bạn (bản chỉ-luật, không cần GPU)

Mục tiêu: chạy lại toàn bộ 1000 câu public trên máy bạn, ra `submission.jsonl`, rồi so với file chuẩn nhóm gửi để chắc code chạy giống nhau ở mọi máy (BTC yêu cầu chạy 2 lần phải ra cùng kết quả).
Thời gian: ~10 phút cài đặt + 6–20 phút chạy. Không cần mạng lúc chạy, không cần GPU.

## 1. Chuẩn bị

- **Code:** `git clone https://github.com/LingSeanCoder/DataMate.git` (hoặc trên GitHub bấm *Code → Download ZIP* rồi giải nén).
- **Bộ đề public** (nhóm gửi qua Drive, không có trong repo). Giải nén ra một thư mục, ví dụ `public_test`, bên trong phải có đúng:
  ```
  public_test/
    questions.jsonl
    rulebook.md
    calendar.csv
    data/            (14 thư mục dev_001 … dev_014)
  ```
- **File chuẩn để so:** `out_public_rules_v2.jsonl` (nhóm gửi kèm).
- Máy cần **một trong hai**: Docker Desktop (cách A — khuyên dùng, nhất là Windows), hoặc Python 3.9+ (cách B).

## 2A. Chạy bằng Docker (giống lúc nộp thật)

Mở Terminal (macOS/Linux) hoặc PowerShell (Windows) trong thư mục `DataMate`:

```bash
docker build --platform linux/amd64 -t datamate-submission .
```

macOS / Linux:
```bash
docker run --rm --network=none \
  -v /duong/dan/toi/public_test:/data:ro \
  -v "$(pwd)/ket_qua":/result \
  datamate-submission
```

Windows (PowerShell, viết một dòng):
```powershell
docker run --rm --network=none -v C:\duong\dan\toi\public_test:/data:ro -v ${PWD}\ket_qua:/result datamate-submission
```

Xong khi thấy dòng `Hoàn tất: 1000 câu mới ...`. Kết quả: `ket_qua/submission.jsonl`, log ở `ket_qua/logs/`.

## 2B. Chạy bằng Python (không Docker)

```bash
cd DataMate
python -m venv .venv
# macOS/Linux: source .venv/bin/activate      Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py --data-root /duong/dan/toi/public_test/data \
  --questions /duong/dan/toi/public_test/questions.jsonl \
  --db-dir tmp_db_dev --rebuild --no-llm --overwrite \
  --out submission.jsonl --log run_log.jsonl
```

Lưu ý: luôn có `--no-llm` (bản nộp không dùng LLM) và `--rebuild` ở lần chạy đầu.

## 3. So với file chuẩn

Đặt `out_public_rules_v2.jsonl` cạnh file kết quả, chạy (sửa đường dẫn nếu khác):

```bash
python -c "import json; L=lambda p:{(r:=json.loads(l))['id']:r for l in open(p,encoding='utf-8')}; a=L('out_public_rules_v2.jsonl'); b=L('ket_qua/submission.jsonl'); d=[k for k in a if a[k]!=b.get(k)]; print(len(b),'dòng; lệch',len(d)); [print(k,a[k],'|',b.get(k)) for k in d[:10]]"
```

(Chạy cách B thì thay `ket_qua/submission.jsonl` bằng `submission.jsonl`. Lệnh cần Python 3.8+.)

**Kỳ vọng:** `1000 dòng; lệch 0`.

## 4. Gửi lại cho nhóm

1. Kết quả dòng so sánh ở bước 3 (và các dòng lệch nếu có).
2. Thời gian chạy, máy gì (Windows/macOS, CPU, RAM), chạy cách A hay B.
3. Nếu lỗi: chụp màn hình đoạn lỗi cuối + gửi file `run_log.jsonl` (hoặc `ket_qua/logs/`).

## Lỗi hay gặp

| Triệu chứng | Cách xử lý |
|---|---|
| `thiếu questions.jsonl` khi chạy Docker | Mount sai thư mục: `-v` phải trỏ tới thư mục **chứa** `questions.jsonl`, không phải thư mục `data` |
| Docker báo không tìm thấy đường dẫn trên Windows | Dùng đường dẫn đầy đủ `C:\...`, không có dấu cách; hoặc bật chia sẻ ổ đĩa trong Docker Desktop |
| `ModuleNotFoundError` (cách B) | Chưa kích hoạt `.venv` hoặc chưa `pip install -r requirements.txt` |
| Chạy rất chậm / máy treo | Thêm `-e WORKERS=2` (Docker) hoặc `--workers 2` (Python) |
| Lệch vài dòng | Gửi lại các dòng lệch — có thể do khác hệ điều hành, nhóm cần biết để sửa trước khi nộp |
