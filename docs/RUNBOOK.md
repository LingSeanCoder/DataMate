# DataMate — Hướng dẫn chạy (kiến trúc Schema-Agnostic)

Code **không hard-code** tên file, tên bảng hay tên cột. Mỗi doanh nghiệp có
thư mục riêng, mỗi doanh nghiệp có một DB SQLite riêng.

```
data/
  biz_101/  ban_hang.csv                         README.md
  biz_102/  DonHang_ChiTiet.xlsx  DanhSachQuyDoi.xlsx  README.md
  biz_105/  SoBanHang_1.xlsx  BangGia.xlsx  TonKho.xlsx  README.md
  biz_123/  pos.sqlite                           README.md
  ...
```

---

## 0. Luồng trả lời (từ Phase 4)

Mỗi câu: `run_rules.answer()` (regex + `time_resolver` + `entity` + `metrics.py` trên bảng `std_*`) trả lời trước;
chỉ câu luật **bỏ trống** mới gọi LLM (`agent.ask`, chỉ thấy bảng `std_*`). Không có server LLM thì câu đó bỏ trống (0 điểm).
`rulebook.md` cạnh thư mục `data/` của bộ đề được ưu tiên hơn `rulebook.md` trong repo (repo giữ bản public; bản train: `rulebook_train.md`).

```bash
# chỉ luật (không cần GPU): build DB + std + trả lời
python src/main.py --data-root ../DATASET/public_test/data --questions ../DATASET/public_test/questions.jsonl \
  --db-dir tmp_db_dev --out submission.jsonl --overwrite --workers 4 --no-llm
# có LLM dự phòng: export LOCAL_LLM_BASE_URL=http://localhost:8000/v1 rồi bỏ --no-llm
```

## 1. Cài đặt

```powershell
pip install -r requirements.txt
```

## 2. Cấu hình API key

```powershell
Copy-Item .env.example .env
# mở .env và điền GROQ_API_KEY
```

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `GROQ_API_KEY` | — | Bắt buộc |
| `GROQ_MODEL` | `qwen/qwen3.8-27b` | Model sinh SQL |
| `DATAMATE_PROMPT_BUDGET` | `6300` | Trần input-token mỗi câu (free tier = 7000/phút) |
| `DATAMATE_FULL_RULEBOOK` | `0` | `1` = nhồi toàn bộ rulebook (cần tier trả phí) |

---

## 3. Chạy toàn bộ tập train

```powershell
python src/main.py `
  --data-root ..\..\train\train\data `
  --questions ..\..\train\train\questions.jsonl `
  --out submission.jsonl `
  --db-dir tmp_db
```

Lần đầu sẽ build DB cho từng biz (mất vài phút), sau đó chạy từng câu hỏi.
Kết quả:

- `submission.jsonl` — mỗi dòng `{"id", "type", "answer"}`
- `run_log.jsonl` — SQL, lỗi, số lần sửa, token ước lượng của từng câu
- `tmp_db/biz_XXX.db` — DB của từng doanh nghiệp
- `tmp_db/biz_XXX.report.json` — thống kê làm sạch

Chạy lại để **resume**: các `id` đã có trong `submission.jsonl` sẽ bị bỏ qua.
Muốn chạy lại từ đầu: thêm `--overwrite`.

### Test nhanh trước khi chạy full

```powershell
# 3 câu đầu của mỗi biz, chỉ 2 biz, in SQL ra màn hình
python src/main.py --data-root ..\..\train\train\data `
  --questions ..\..\train\train\questions.jsonl `
  --biz biz_101,biz_123 --limit-per-biz 3 --overwrite --show-sql --workers 1
```

---

## 4. Build DB lẻ (debug một doanh nghiệp)

```powershell
python src/data_loader.py --biz-dir ..\..\train\train\data\biz_103 --db tmp_db\biz_103.db
```

Build tất cả nhưng không chạy câu hỏi:

```powershell
python src/data_loader.py --data-root ..\..\train\train\data --out-dir tmp_db
```

Xem nhanh schema + giá trị phân loại:

```powershell
python -c "import sqlite3;c=sqlite3.connect('tmp_db/biz_103.db');[print(r) for r in c.execute('SELECT table_name,column_name,kind,top_values FROM _meta_columns LIMIT 40')]"
```

Hỏi thử 1 câu:

```powershell
python src/agent.py "Doanh thu tháng 6/2025 là bao nhiêu?" `
  --db tmp_db\biz_103.db `
  --readme ..\..\train\train\data\biz_103\README.md `
  --date 2026-06-24 --show-debug
```

---

## 5. Các cờ của `main.py`

| Cờ | Tác dụng |
|---|---|
| `--data-root` | Thư mục chứa các `biz_*` (mặc định `./data`) |
| `--questions` | File câu hỏi JSONL |
| `--out` | File nộp bài (mặc định `submission.jsonl`) |
| `--log` | File log debug (mặc định `run_log.jsonl`) |
| `--db-dir` | Thư mục DB tạm từng biz (mặc định `tmp_db`) |
| `--biz a,b` | Chỉ chạy các business này |
| `--workers N` | Số luồng mỗi biz (mặc định 2) |
| `--itpm N` | Giới hạn input token/phút (mặc định 6800) |
| `--prompt-budget N` | Trần token input mỗi câu (mặc định 6300) |
| `--limit N` | Chỉ chạy N câu đầu (tổng) |
| `--limit-per-biz N` | Chỉ chạy N câu đầu của mỗi biz |
| `--rebuild` | Build lại DB dù đã có |
| `--skip-build` | Không build, dùng DB có sẵn |
| `--overwrite` | Xoá submission/log cũ, chạy từ đầu |
| `--no-review-empty` | Không rà lại khi SQL trả 0 dòng |
| `--show-sql` | In SQL từng câu |
| `--full-rulebook` | Nhồi toàn bộ rulebook (cần tier trả phí) |
| `--no-llm` | Chỉ chạy luật + metrics; câu luật không chắc thì bỏ trống |
| `--verbose` | Log chi tiết |

---

## 6. Loader hoạt động thế nào

1. **Quét file**: mọi `.xlsx/.xls/.xlsm/.csv/.txt/.sqlite/.db` trong thư mục biz.
2. **Dò header**: chấm điểm 12 dòng đầu (số ô có dữ liệu + độ phân biệt − phạt ô
   giống số/ngày). Tự bỏ dòng tiêu đề, dòng trống, header 2 tầng kiểu merged cell
   (ví dụ `SoBanHang_1.xlsx` có 2 dòng header).
3. **Gom bảng**: các sheet/file có **cùng bộ cột** được gộp làm một bảng logic —
   sheet theo tháng (`T01_2025`, `Thang 02 2025`…) và file bị tách đuôi
   `_1`, `_2`, `_2025`, `_2026` đều nhập chung.
   Workbook có nhiều bảng khác schema (ví dụ `Bao_cao_ban_hang.xlsx` chứa 4 danh
   sách) thì mỗi sheet là một bảng riêng.
4. **Làm sạch tổng quát**:
   - Tên cột có `Ngày / Thời gian / Hạn / Giờ` → parse ngày, lưu `YYYY-MM-DD`
     (hỗ trợ `DD/MM/YYYY`, ISO, `1 thg 6 2025`, và Excel serial `45689`).
   - Tên cột có `Tiền / Giá / NCC / Tổng / Nợ / Phí / Giảm…` → parse tiền
     (`90.000đ`, `7,100`, `1,105,000`, `1,500.0000`, `25k`, `(1.200)`).
   - Cột có `%` → lưu theo điểm phần trăm.
   - Cột số lượng → tách số đầu chuỗi (`1 kg` → 1).
   - Bỏ dòng rác, dòng lặp header, dòng tổng / cộng dồn (`Tổng ngày 01/04`,
     `Tổng tháng 5/2026`, `Cộng ngày …`) dù nhãn nằm ở cột nào, ô trống.
     Nhãn tổng được so khớp **cả ô** nên tên khách kiểu "Tổng công ty…" hay
     "Tong Huong Chau" không bị xoá nhầm.
   - **Điền ô trống theo nhóm đơn** (Rulebook mục 8): trong cùng một mã đơn, nếu
     một cột chỉ có đúng một giá trị khác rỗng thì điền xuống các dòng trống.
   - **Loại trùng**: chỉ chạy khi bảng có cột khoá đơn. Hai dòng giống hệt nhau
     của cùng một đơn là lỗi export, tính một lần; bảng không ghi theo đơn thì
     giữ nguyên (vì hai dòng giống nhau vẫn là hai lần bán).
5. **Metadata cho agent** (bảng `_meta_*` trong DB):
   - `_meta_tables`: tên bảng, nguồn, số dòng.
   - `_meta_columns`: tên cột, **tên gốc tiếng Việt**, kiểu, khoảng min–max, và
     **danh sách giá trị DISTINCT** của cột phân loại (≤ 25 giá trị).
   - `_meta_info`: ngày cuối dữ liệu đọc từ README.
   - `_quy_doi`: bảng quy đổi đơn vị **parse từ README**.
   - `_meta_readme`: README đã rút gọn (đã tách bảng quy đổi).

> Việc "tự chạy `SELECT DISTINCT` để xem dữ liệu thật" được thực hiện **trước**
> khi gọi LLM: loader chạy DISTINCT và nhét kết quả vào prompt. LLM chỉ được
> dùng đúng các giá trị đó, không tự bịa trạng thái/đơn vị.

---

## 7. Agent hoạt động thế nào

System prompt gồm 4 khối:

1. **Mô tả nghiệp vụ** — nguyên văn `README.md` của chính biz đó, kèm câu:
   *"Đây là mô tả nghiệp vụ của cửa hàng này: … Hãy dựa vào đây để hiểu ý nghĩa
   các bảng và cột."*
2. **Database** — schema từ metadata + `DISTINCT=(...)` + khoảng ngày + ghi chú
   về bảng `_quy_doi`.
3. **Rulebook chung** — doanh thu, thời gian, công nợ, làm tròn, format đáp án
   (các mục liên quan đến câu hỏi; `--full-rulebook` để nhồi tất cả).
4. **Định dạng trả về** — `{"sql": "...", "type": "..."}` hoặc `UNANSWERABLE`.

Sau đó: chạy SQL read-only → lỗi thì gửi lỗi lại cho LLM sửa (tối đa 3 lần) →
0 dòng thì rà lại 1 lần → vẫn rỗng thì `abstain` → ép kiểu đáp án.

Không còn bảng view hay cột trạng thái hard-code nào. Mọi thứ suy ra từ dữ liệu
và README của từng doanh nghiệp.

---

## 8. Tốc độ

Free tier Groq giới hạn **7.000 input-token/phút**; mỗi câu tốn ~5–6k token nên
thực tế khoảng **1 câu/phút**. 3.000 câu ≈ 50 giờ.

Tăng tốc:

```powershell
# Nếu đã nâng tier Groq
python src/main.py --itpm 200000 --workers 6 --full-rulebook
```

Hoặc chạy song song nhiều tiến trình, mỗi tiến trình một nhóm biz:

```powershell
python src/main.py --biz biz_101,biz_102,biz_103 --out sub_a.jsonl --log log_a.jsonl
python src/main.py --biz biz_104,biz_105,biz_106 --out sub_b.jsonl --log log_b.jsonl
# rồi nối các file submission lại
```

> Nếu chạy nhiều tiến trình cùng một API key, tổng token/phút vẫn bị tính chung
> nên không nhanh hơn; hãy dùng nhiều key hoặc nâng tier.

---

## 9. Kiểm tra độ chính xác trên tập train

Tập train có `answers.jsonl`. So sánh nhanh:

```powershell
python - <<'PY'
import json
gold = {json.loads(l)["id"]: json.loads(l) for l in open("../../train/train/answers.jsonl", encoding="utf-8-sig")}
pred = [json.loads(l) for l in open("submission.jsonl", encoding="utf-8")]
ok = sum(1 for p in pred if gold.get(p["id"], {}).get("answer") == p.get("answer")
         and gold.get(p["id"], {}).get("type") == p.get("type"))
print(f"{ok}/{len(pred)} = {ok/max(1,len(pred)):.1%}")
PY
```

---

## 10. Ghi chú kỹ thuật

- `setup_db.py` và `business_data.db` cũ **đã lỗi thời** — xoá được. Toàn bộ dữ
  liệu giờ do `data_loader.py` sinh ra theo từng biz.
- `README.md` là nguồn tri thức nghiệp vụ quan trọng nhất. Đừng đặt file hướng
  dẫn này tên là `README.md` trong thư mục biz (agent sẽ đọc nhầm thành mô tả
  nghiệp vụ).
- SQLite coi `"CotKhongTonTai"` là chuỗi, khiến `SUM` trả 0. Agent chặn trước
  bằng danh sách identifier hợp lệ lấy từ metadata.
- Console Windows mặc định cp1252; cả 3 file đã ép stdout/stderr sang UTF-8 nên
  `--help` và log tiếng Việt chạy bình thường.
