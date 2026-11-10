# Chạy DataMate trên Google Colab (Phase 6 — đo LLM)

Không cần cài gì trên máy cá nhân, không cần pull code: notebook tự tải code mới nhất từ GitHub.
Cần: tài khoản Google, ~10 GB trống trên Google Drive, và bộ dữ liệu cuộc thi.

## 1. Chuẩn bị dữ liệu trên Google Drive (làm 1 lần)

1. Nén thư mục `DATASET` thành **`DATASET.zip`**. Bên trong zip phải có đúng cấu trúc:
   ```
   train/
     data/            (30 thư mục biz_101 … biz_130)
     questions.jsonl
     answers.jsonl    (đáp án train — bắt buộc, dùng để chấm LLM)
     calendar.csv
     rulebook.md
   public_test/
     data/            (14 thư mục dev_001 … dev_014)
     questions.jsonl
     calendar.csv
     rulebook.md
   ```
   Kiểm tra: mở zip ra phải thấy ngay `train/` và `public_test/`, **không** bị lồng thêm một thư mục `DATASET/` bên ngoài.
2. Vào Google Drive → tạo thư mục **`DATAMATE`** ngay ở **My Drive** → tải `DATASET.zip` lên đó.
   Đường dẫn cuối cùng phải là: `My Drive/DATAMATE/DATASET.zip`.

## 2. Mở notebook

1. Vào https://colab.research.google.com → **File → Open notebook → GitHub**.
2. Dán `LingSeanCoder/DataMate` → Enter → chọn **`colab_phase6.ipynb`**.
3. **Runtime → Change runtime type → Hardware accelerator: GPU** (T4 bản miễn phí là đủ; L4/A100 nhanh hơn) → Save.

## 3. Chạy

**Runtime → Run all** (hoặc chạy lần lượt từng ô bằng Shift+Enter). Khi ô Drive hỏi quyền → chọn tài khoản có `DATASET.zip` → Allow.

| Ô | Làm gì | Thời gian ước lượng (T4) |
|---|---|---|
| `nvidia-smi` | Kiểm tra có GPU (phải thấy Tesla T4 / L4 / A100) | vài giây |
| Drive + unzip | Gắn Drive, giải nén dữ liệu vào `/content/DATASET` | 1–3 phút |
| Clone + pip | Tải code mới nhất, cài thư viện | 1–2 phút |
| Cài llama.cpp (cách A) | Cài server LLM bản CUDA | 1–3 phút |
| Tải model | Qwen2.5-Coder-14B-Instruct Q4_K_M (~9 GB) | 5–15 phút |
| Bật server | Chờ tới khi in **`server sẵn sàng`** | 1–3 phút |
| Build DB + chạy chỉ-luật | Dựng DB 44 shop, chạy luật train + public, in điểm train (kỳ vọng ~2961/3000) | 20–40 phút |
| Chạy có LLM | Train 1 lần, public 2 lần (chỉ câu luật bỏ trống mới gọi LLM) | 10–30 phút |
| Báo cáo | In kết quả, chép file về Drive | vài giây |

Không đóng tab trong lúc chạy. Bản Colab miễn phí có thể ngắt nếu để máy treo quá lâu; nếu bị ngắt, chạy lại từ đầu (dữ liệu trên Drive vẫn còn).

## 4. Gửi lại kết quả

Ô cuối in:
- `TRAIN: N câu đi LLM | đúng … sai … trống … => điểm LLM thêm …` và từng câu kèm đáp án LLM, gold, câu SQL.
- `PUBLIC: N câu đi LLM | 2 lần chạy khác nhau ở … câu | thời gian (giây): {...}` và từng câu public đi LLM.

Gửi lại **toàn bộ phần chữ in ở ô cuối** (copy dán), hoặc chia sẻ thư mục **`My Drive/DATAMATE/phase6_out/`** (có các file `out_*.jsonl`, `log_*.jsonl`, `server.log`).

## 5. Gặp lỗi thì sao

| Triệu chứng | Cách xử lý |
|---|---|
| Ô `nvidia-smi` báo lỗi / không có GPU | Chưa chọn GPU ở bước 2.3, hoặc hết hạn mức GPU miễn phí → đợi hoặc dùng tài khoản khác |
| `unzip: cannot find …DATASET.zip` | Sai tên/thư mục: phải đúng `My Drive/DATAMATE/DATASET.zip` |
| `ls /content/DATASET` không thấy `train`, `public_test` | Zip bị lồng thư mục `DATASET/` → nén lại theo mục 1 |
| Ô cài llama.cpp (cách A) lỗi, hoặc server không lên (ô bật server in log lỗi) | Mở ô **Cách B**, bỏ dấu `#` ở dòng lệnh, chạy (build ~10 phút), sửa `USE_LLAMA_CPP_BINARY = True`, rồi chạy lại ô bật server và các ô sau |
| Log server có `out of memory` | Ở ô bật server đổi `16384` thành `8192` (cả hai chỗ `-c` / `--n_ctx`), chạy lại ô đó |
| Điểm train chỉ-luật khác xa 2961 | Kiểm tra `answers.jsonl` có trong `train/`; gửi lại phần in của ô đó |
| Chạy rất lâu ở ô có LLM | Bình thường nếu là T4; để chạy tiếp, đừng tắt tab |

## Ghi chú

- Code lấy từ nhánh `main` lúc chạy (ô clone in ra mã commit — gửi kèm mã này).
- Notebook chỉ đọc dữ liệu, không ghi gì lên GitHub. Không chia sẻ `phase6_out/` công khai: trong đó có đáp án public test.
