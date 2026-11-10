# HANDOFF — DataMate (Zalo AI Challenge 2026) — cập nhật 2026-11-10

**Goal:** trả lời câu hỏi số liệu bán hàng theo rulebook; nộp Docker (offline, ≤15B, 2×RTX 5090, ≤6h) **trước 16/11/2026**. Làm theo plan v3, xong mỗi phase: báo cáo (Tasks / Verify / Files / Blockers), commit + push `main` (người dùng đã cho phép), rồi HỎI trước khi sang phase sau. Trả lời tiếng Việt. Chi tiết lịch sử: `tasks/HANDOFF.md`.

**Done (đã push, commit cuối `ef0a021`):**
- Phase 0–3: tầng std_* (`std_layer.py`, `std_extra.py`), `time_resolver.py`, `entity.py`, `metrics.py`, router regex `run_rules.py`.
- Phase 4: `main.py` = luật trước, chỉ câu bỏ trống mới gọi LLM (`agent.py`, `std_only=True`); `--no-llm`; build gọi luôn `std_extra`; `rulebook.md` = bản public (train → `rulebook_train.md`), main ưu tiên `<data-root>/../rulebook.md`.
- Chấm tay 100 câu public trên bảng thô (file chỉ ở máy, gitignore: `tasks/public_check_50.md`): đúng 84% / 88% TRƯỚC sửa → độ đúng public thật ~84–88% (train 98%). Đã sửa 14 lỗi tìm được.
- Phase 5 unanswerable: train 70/70 (+ luật tổng quát: 'ai/nào … X nhất' mà X không đo được → định tính; không phụ thuộc danh sách từ train), 0 gắn nhầm. **Train 2961/3000, điểm 2934; public 992/1000 có đáp án.**

**In progress:** không có. Đang chờ người dùng đồng ý chuẩn bị Phase 6.

**Next steps:**
1. Phase 6: soạn notebook Colab (cài llama.cpp server + Qwen2.5-Coder-14B-Instruct Q4_K_M, temperature 0, seed 42), chạy `main.py` có LLM; đo trên **50 câu public MỚI** (loại 100 câu đã chấm), chấm tay như trước; chỉ bật LLM (fallback/kiểm chéo) nếu tăng điểm thật.
2. Phase 7: Dockerfile chạy `main.py` (`--network=none`), test trọn quy trình trên public; có sẵn bản `--no-llm` làm phương án dự phòng.

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

**Open questions:** đồng ý chuẩn bị notebook Colab + script đo cho Phase 6 chưa?
