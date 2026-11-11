# Todo: 1/20 → 20/20

Quy ước: mỗi task xong khi **check trên gold train của cả họ câu hỏi** đạt ngưỡng, không chỉ 20 câu.

## Phase 1 — Nền (đo được, format đúng)
- [ ] **T0 Eval harness**: `eval_offline.py --ids <file>` chạy metric library không cần LLM, in bảng đúng/sai theo họ. Thêm file `tasks/baseline20.txt` (20 id).
  - Chấp nhận: chạy 20 câu < 10 giây; in họ + pred + gold.
- [ ] **T1 Time resolver** (`time_resolver.py`): hôm nay, hôm qua, tuần này/trước, tháng này (+ cùng kỳ tháng trước), tháng M không năm, quý, N ngày gần nhất, đến hết ngày X, Tết (calendar.csv).
  - Chấp nhận: ≥ 40 unit test lấy đúng ví dụ §4.2; pass hết.
- [ ] **T2 Formatter §2**: tiền làm tròn đồng (0.5 lên), percent 2 số, date `YYYY-MM-DD`/`YYYY-MM`, list/table, sort + tie-break mã tăng dần.
  - Chấp nhận: unit test từng type; dùng chung cho metric library và đường SQL cũ.
- [ ] **T3 Router + metric doanh thu / số lượng / top-k / huỷ**: LLM trả JSON `{intent, product, period, k, ...}`; Python tính trên std_*.
  - Chấp nhận: họ doanh thu ≥ 95%, số lượng ≥ 90%, top-k ≥ 90%, tỉ lệ huỷ ≥ 95% trên train (offline với slot từ regex/LLM).
- [ ] **CP1**: 00c4, 09ae, 0183, 0fc3, 0d56, 0743, 019e đúng trên Colab.

## Phase 2 — Họ nghiệp vụ còn thiếu (mỗi task = std table + hàm + test)
- [ ] **T4 Hoạt chất** (§5.2): map product_id → hoạt chất, so khớp y hệt (không cộng thuốc phối hợp). Ứng: 00195, 0f62.
- [ ] **T5 Tăng giá** (§4.5): ngày tăng = lần đầu giá mới > giá trước (lịch sử giá hoặc ngày ghi trong câu); so qty_base [D, D+N-1] vs [D-N, D-1]. Ứng: 02b5, 0d3c, 0bf1.
- [ ] **T6 Công nợ** (§4.4): `std_payments`; nợ đơn = phải trả − đã trả đến hết X; loại huỷ/COD/TMĐT; tuổi nợ. Ứng: 026f, 073a.
- [ ] **T7 Nhập hàng** (§4.5): `std_purchases`; tổng tiền theo ngày phiếu, bỏ huỷ, giá bình quân gia quyền theo SL. Ứng: 05d6, 0266.
- [ ] **T8 Tồn kho** (§4.5): `std_stock` tồn cuối ngày (đơn vị cơ bản) + quy đổi `[chẵn, lẻ]` + giá trị theo giá vốn danh mục (chỉ tồn > 0). Ứng: 0794, 0a6d.
- [ ] **T9b 6 họ bổ sung sau Gate 0** (số thật từ count_intents.py): `dem` 12% (đếm đơn/khách/ngày), `percent` 9.7% (tỉ lệ huỷ, tỉ trọng), `so_sanh` 5.5% (tăng trưởng % giữa 2 kỳ), `tb_don` 4.2% (doanh thu/số đơn), `tra_ngay` 4.7% (ngày/tháng có chỉ số cao nhất), `top_khach` 3.1%.
- [ ] **T9 Lô hạn dùng** (§5.2): `std_lots` tồn lô, HSD ≥ as_of, lấy sớm nhất. Ứng: 0eef.
  - Chấp nhận chung T4–T9: họ tương ứng ≥ 85% trên train; `check_std.py` thêm mục cho họ đó.
- [ ] **CP2**: `eval_offline.py --ids tasks/baseline20.txt` = 19–20/20.

## Phase 3 — An toàn điểm + chạy thật
- [ ] **T10 Abstain/unanswerable gate** (§6): kỳ hỏi ngoài data span, cột không tồn tại, câu định tính → unanswerable; lỗi/kết quả rỗng/router không chắc → abstain. Không bao giờ đoán unanswerable khi std có dữ liệu.
  - Chấp nhận: 70 câu unanswerable train ≥ 90% đúng, false-unanswerable trên phần còn lại < 1%.
- [ ] **T11 Colab + Docker**: notebook chạy `--rebuild`, llama-server Qwen 14B, temperature 0; ghi log; đo thời gian/câu.
  - Chấp nhận: 20/20 trên Colab; 150 câu (5/biz) ≥ baseline cũ; ước lượng full test < 6 giờ.
- [ ] **CP3**: báo điểm 20 câu + 150 câu, commit, push.

## Ai làm gì (gợi ý)
- Bạn (data): T0, T1, T6–T9, std tables.
- Text-to-SQL: T2, T3 router/prompt, T10.
- Fine-tune: dataset (câu hỏi → JSON intent/slots) từ 3000 câu train cho router ở T3.
