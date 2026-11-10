# HANDOFF — DataMate (Zalo AI Challenge 2026) — cập nhật 2026-11-11 (tối)

**Goal:** trả lời câu hỏi số liệu bán hàng theo rulebook; nộp Docker (offline, ≤15B, 2×RTX 5090, ≤6h) **trước 16/11/2026**. Mỗi bước: báo cáo, commit + push `main` (đã được cho phép), hỏi trước khi sang việc lớn tiếp theo. Trả lời tiếng Việt. Lịch sử cũ: `tasks/HANDOFF.md`.

**Done (đã push, commit cuối `64dfdab`):**
- Phase 0–5, 7 (bản chỉ-luật): std_* (`std_layer.py`, `std_extra.py`), `time_resolver.py`, `entity.py`, `metrics.py`, router `run_rules.py`, `main.py` (luật trước, LLM fallback), Docker `Dockerfile` + `run.sh` (`/data` → `/result/submission.jsonl`, đã thử `--network=none`, amd64).
- Phase 6 xong: LLM trên train 3 đúng / 9 sai → **tắt LLM, nộp `--no-llm`**. `main.py` chặn mọi answer null → abstain.
- **Public leaderboard lần 1 (chỉ-luật): 0.598** = điểm ròng (đúng − sai)/1000, tức ~795 đúng / 197 sai (~80% đúng), KHÔNG phải overfit 98%→60%.
- Sửa sau điểm đầu: nợ ghi bằng chữ trong Note (`std_extra.debt_from_note`, dev_008); hỏi nhóm hàng mà shop không có cột nhóm → unanswerable (§7, dev_002/004); "từ khi X lên 35.000đ" = tăng giá (`count_intents.py`).
- Chấm tay mẫu 3 (70 câu, 5/biz, ghi ở `tasks/public_check_50.md` – gitignore): 65/70 trước sửa. Sửa: topping ghi chung ô thành mặt hàng riêng (`std_layer.split_toppings` + `_canon_names`, dev_002); "củ" = triệu (`run_rules._amount` + 2 regex ngưỡng); "từ đầu tháng đến hết hôm nay" = tháng này (`time_resolver.py`, test mới); "% tiền bán máy" mẫu số chỉ máy mới + cũ (`run_rules.py`). Rà họ tồn kho: public KHÔNG có câu chẵn/lẻ; sửa "cháy hàng"/"đứt hàng" = hết hàng (2 câu dev_006); 3 câu giá trị tồn + 3 câu hết hàng đã kiểm tay đúng.
- P1 (2026-11-11 tối): vòng 0 quét bất thường 1000 câu → sửa 10; vòng 1 (70 câu) 66/70 → sửa 4 loại (+2 câu ngoài mẫu); vòng 2 (70 câu) **70/70 → dừng chấm**. Tổng 5 mẫu (310 câu) + quét: độ đúng hiện ước ~95%+. Train **2962/3000**.
- Hiện tại: train **2961/3000** (điểm 2934); public 0 câu bỏ trống, 30 unanswerable. Tổng 3 mẫu chấm tay (170 câu) → độ đúng ~88–93%, lỗi rải rác, không có họ nào nên abstain.

**In progress:** file nộp bản mới đã tạo: `../out_public_rules_v2.jsonl` (ngoài repo, 1000 dòng, đủ id, 0 null, code `c38bc29` + vòng 2 không đổi code) — chờ người dùng nộp leaderboard và báo điểm.

**Next steps:**
1. Chạy public chỉ-luật → nộp leaderboard, ghi điểm vào đây (kỳ vọng > 0.598).
2. (Đã dừng chấm mẫu: vòng 2 = 70/70.) Nếu chấm thêm: script pandas đọc bảng thô trong `tmp_db_dev/dev_XXX.db`, lọc trạng thái thô, bỏ cả đơn có dòng huỷ/trả, gộp tên bỏ dấu; loại id đã có trong `tasks/public_check_50.md`.
3. Phase 7: khớp quy định nộp của BTC (mount path, tên file, registry hay .tar) — chỉ sửa `run.sh`; trước nộp build `--platform linux/amd64`, chạy `--network=none` trên public, kiểm 1000 dòng.

**Decisions:** LLM tắt (lỗ trên train). Thông tin shop không ghi → unanswerable, không suy từ cột khác (§7). Nợ chỉ có ảnh chụp cuối dữ liệu mà hỏi ngày trước → bỏ trống (`metrics.Unsure`). Topping: số phần + tiền trong ngoặc là của topping, món giữ SL dòng, tiền món = tiền dòng − tiền topping.

**Tried & failed:** tách topping trước bước gom tên làm lệch index với bảng thô → giữ tách ở cuối rồi gom tên lại. `parse_number` quá chặt hỏng "2 bao 25kg". Regex thiếu `\b` gây khớp nhầm — sau mỗi sửa PHẢI chạy lại train.

**Verify with:**
```
python main.py --data-root ../DATASET/public_test/data --questions ../DATASET/public_test/questions.jsonl --db-dir tmp_db_dev --no-llm --overwrite --out <out.jsonl> --log <log.jsonl>   # thêm --rebuild nếu đổi std_layer/std_extra; ~5 phút
.venv/bin/python -W ignore run_rules.py --answers ../DATASET/train/answers.jsonl --wrong 0 | tail -1   # kỳ vọng 2961/3000 (std_extra đổi: chạy `python std_extra.py tmp_db/*.db` trước)
.venv/bin/python test_time_resolver.py; .venv/bin/python test_time_public.py; .venv/bin/python -W ignore test_phase2.py
.venv/bin/python -W ignore validate_std.py --db-dir tmp_db_dev   # 14/14
```
**Lưu ý:** MacBook Air M3 16GB, không chạy song song nhiều build. Repo GitHub PUBLIC: không commit đáp án public, đường dẫn máy, SĐT. Push từng bị chặn vì email riêng tư (GH007) — nếu lặp lại, hỏi người dùng.

**Open questions:** (1) quy định nộp Docker của BTC? (2) điểm leaderboard của bản mới?
