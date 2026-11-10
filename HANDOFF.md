# HANDOFF — DataMate (Zalo AI Challenge 2026) — cập nhật 2026-11-11

**Goal:** trả lời câu hỏi số liệu bán hàng theo rulebook; nộp Docker (offline, ≤15B, 2×RTX 5090, ≤6h) **trước 16/11/2026**. Làm theo plan v3, xong mỗi phase: báo cáo (Tasks / Verify / Files / Blockers), commit + push `main` (người dùng đã cho phép), rồi HỎI trước khi sang phase sau. Trả lời tiếng Việt. Chi tiết lịch sử: `tasks/HANDOFF.md`.

**Done (đã push, commit cuối `2fcaae6` + commit handoff này):**
- Phase 0–3: tầng std_* (`std_layer.py`, `std_extra.py`), `time_resolver.py`, `entity.py`, `metrics.py`, router regex `run_rules.py`.
- Phase 4: `main.py` = luật trước, chỉ câu bỏ trống mới gọi LLM (`agent.py`, `std_only=True`); `--no-llm`; build gọi luôn `std_extra`; `rulebook.md` = bản public (train → `rulebook_train.md`), main ưu tiên `<data-root>/../rulebook.md`.
- Chấm tay 100 câu public trên bảng thô (file chỉ ở máy, gitignore: `tasks/public_check_50.md`): đúng 84% / 88% TRƯỚC sửa → độ đúng public thật ~84–88% (train 98%). Đã sửa 14 lỗi tìm được.
- Phase 5 unanswerable: train 70/70 (+ luật tổng quát: 'ai/nào … X nhất' mà X không đo được → định tính; không phụ thuộc danh sách từ train), 0 gắn nhầm. **Train 2961/3000, điểm 2934; public 992/1000 có đáp án.**
- Phase 7 (bản chỉ-luật): `Dockerfile` (python:3.11-slim) + `run.sh`: mount bộ đề nguyên vào `/data` (questions.jsonl, data/, rulebook.md, calendar.csv) → `/result/submission.jsonl`, log `/result/logs/`. Thử `--network=none` trên 1000 câu public: 6,2 phút, trùng khớp 1000/1000 với chạy ngoài Docker; bản `linux/amd64` build + chạy được. README đã cập nhật phần Docker. Hướng dẫn Colab cho thành viên: `COLAB.md`.

**2026-11-11 — điểm public đầu tiên:** nộp chỉ-luật được **0.598**. Đây là điểm ròng (đúng − sai)/1000, không phải độ đúng: 992 câu có đáp án → khoảng 795 đúng / 197 sai (~80% đúng), khớp ước lượng chấm tay 84–88%. LLM trên train: 3 đúng / 9 sai → **tắt LLM** (`--no-llm`). Đã sửa: (a) `main.py` chặn mọi answer null → abstain; (b) `std_extra.debt_from_note`: sổ ghi nợ bằng chữ trong Note (dev_008) → còn nợ; (c) hỏi theo nhóm hàng mà shop không có cột nhóm (dev_002, dev_004) → unanswerable (§7); (d) `count_intents` nhận "từ khi X lên 35.000đ" là tăng giá. Public: 8 câu bỏ trống → 0; train vẫn 2961/3000.

**In progress — Phase 6 (chờ Tài chạy Colab):** đã có `colab_phase6.ipynb` (llama.cpp server CUDA + Qwen2.5-Coder-14B Q4_K_M; build DB; chạy chỉ-luật và có-LLM trên train/public; báo cáo LLM trên câu luật bỏ trống so với gold, thời gian, tính tất định 2 lần chạy). Đã thử nhánh LLM end-to-end bằng LLM giả ở máy (8 câu đi LLM, chạy thông). Agent gửi seed=42; main ưu tiên `calendar.csv` đi kèm bộ đề. **Đang chờ thành viên (Tài) chạy notebook theo `COLAB.md`** và gửi lại `MyDrive/DATAMATE/phase6_out/` hoặc bảng in ở ô cuối.

**Next steps:**
0. Điểm nằm ở ~197 câu sai đang trả lời: chấm tay mẫu public theo từng biz (raw table) để tìm lỗi hệ thống; họ câu nào đúng < 50% thì cho abstain.
1. Phase 6: đọc kết quả notebook → LLM chỉ được bật nếu điểm LLM thêm trên train > 0 (đúng > sai) và 2 lần chạy public giống nhau; nếu không thì nộp `--no-llm`. Chấm tay các câu public đi LLM (khoảng 8 câu).
2. Phase 7 còn lại: (a) khớp quy định nộp của BTC (đường dẫn mount input/output, tên file, cách nộp image: registry hay .tar) — chỉ cần sửa `run.sh`; (b) nếu Phase 6 bật LLM: image CUDA 12.8+ (nvidia/cuda), build llama-server với `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120` cho RTX 5090, chép model GGUF vào image, `run.sh` bật server rồi chạy `main.py` không `--no-llm` (`USE_LLM=1`); không thử được trên Mac.
3. Trước nộp: build `--platform linux/amd64`, chạy `--network=none` trên public, kiểm 1000 dòng.

**Decisions:** không làm router LLM (luật phủ 99% public; LLM chỉ là fallback, đo ở Phase 6). Thứ tự phase 5→6→7 theo ý người dùng (tôi từng đề xuất Docker trước vì hạn gần). Nợ chỉ có ảnh chụp cuối dữ liệu mà hỏi ngày trước → bỏ trống (`metrics.Unsure`). Shop không ghi loại thông tin → unanswerable.

**Tried & failed:** sửa `parse_number` quá chặt làm hỏng "2 bao 25kg" (đã đổi thành chỉ loại chuỗi có "="). Cache `base_units` đăng ký trước khi điền → đua luồng (đã sửa). Nhiều lần regex thiếu `\b` gây khớp nhầm ("hồng"→"không", "phần trăm"→"hạn trả", "bao nhiêu"→đơn vị "bao") — sau mỗi sửa PHẢI chạy lại train.

**Verify with:**
```
# DB phải build bằng code mới (sau pull): python main.py --data-root ../DATASET/public_test/data --questions ../DATASET/public_test/questions.jsonl --db-dir tmp_db_dev --rebuild --no-llm  (train: --db-dir tmp_db)
.venv/bin/python -W ignore run_rules.py --answers <answers.jsonl train> --wrong 0 | tail -1   # kỳ vọng 2961/3000
.venv/bin/python test_time_resolver.py; .venv/bin/python test_time_public.py; .venv/bin/python -W ignore test_phase2.py
.venv/bin/python -W ignore validate_std.py --db-dir tmp_db_dev   # 14/14
```
**Lưu ý môi trường:** máy MacBook Air M3 16GB không quạt, không có llama-server/model → LLM chỉ chạy được trên Colab. Không chạy song song nhiều lệnh build. Repo GitHub là PUBLIC: không commit đáp án public test, đường dẫn máy, SĐT.

**Open questions:** (1) quy định nộp Docker của BTC (mount path, output, cách nộp)? (2) kết quả Colab Phase 6 của Tài?
