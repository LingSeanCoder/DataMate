# HANDOFF — DataMate (Zalo AI Challenge 2026)

Cập nhật: 2026-11-10. Đọc file này đầu session mới rồi làm tiếp. Plan đang theo: **plan v3** (dưới). Người dùng làm một mình, yêu cầu: làm trọn từng PHASE lớn của plan v3, cuối phase dừng ở GATE, báo cáo (Tasks / Verify output thật / Files changed / Blockers) và HỎI trước khi sang phase sau.

## Bối cảnh cuộc thi
- Model: Qwen2.5-Coder-14B-Instruct GGUF Q4_K_M qua llama-server (≤15B tham số, offline, Docker `--network=none`, 2×RTX 5090, ≤6h). Dev chạy trên Google Colab. Máy local là macOS (không GPU).
- Hạn nộp Docker: **16/11/2026**. Private test 20–23/11.
- Điểm: đúng +1, sai −1, abstain 0. Deterministic: temperature=0, seed=42. Cấm logic theo question id / business id.
- Dữ liệu: `../DATASET/train/{data,questions.jsonl,answers.jsonl,rulebook.md}` (30 biz 101–130, 3000 câu, CÓ gold — cũng ở `/Users/nguyenbao/Downloads/answers.jsonl`), `../DATASET/public_test/{data,questions.jsonl,rulebook.md}` (14 biz dev_001–dev_014, **1000 câu**, KHÔNG gold).
- **rulebook public khác train** (thêm luật Mẹ&bé, Sửa xe, Điện thoại, khách sộp/trung thành/đổi ý, giờ cao điểm, topping, báo cáo ca...). `DataMate/rulebook.md` hiện là bản TRAIN → Phase 4 phải đổi sang bản public (= nội dung `datamate.md`).

## Môi trường local
- Repo: `/Users/nguyenbao/Documents/ZALOAI/DataMate`, venv `.venv/` (python 3.9). DB: `tmp_db/` (train), `tmp_db_dev/` (dev) — đều gitignore.
- Commit cuối trên `origin/main`: `c529a1a`. **Mọi việc từ plan v2/v3 sau đó CHƯA commit** (người dùng chưa trả lời câu hỏi commit).
- KHÔNG chạy song song build/std với nhau (đã từng làm hỏng DB). Chạy chuỗi tuần tự, ví dụ `/tmp/rebuild_all.sh` (nội dung bên dưới).

## Lệnh hay dùng
```bash
.venv/bin/python -W ignore inventory.py --data-root ../DATASET/public_test/data --db-dir tmp_db_dev   # build + bảng kiểm kê
.venv/bin/python -W ignore inventory.py --data-root ../DATASET/train/data --db-dir tmp_db
for f in tmp_db_dev/*.db tmp_db/*.db; do .venv/bin/python -W ignore std_extra.py $f >/dev/null; done   # bảng std phụ
.venv/bin/python -W ignore validate_std.py --db-dir tmp_db_dev      # đối soát (kỳ vọng 14/14), tmp_db: 30/30
.venv/bin/python check_std.py --answers /Users/nguyenbao/Downloads/answers.jsonl   # kỳ vọng 205/205
.venv/bin/python count_intents.py --questions ../DATASET/public_test/questions.jsonl [--show <intent>]
.venv/bin/python test_time_resolver.py       # 37/37 (chưa có teencode public)
.venv/bin/python eval.py --sub <submission.jsonl> --answers <answers.jsonl>
```
Build đầy đủ 2 bộ + std_extra + đối soát ≈ 20–25 phút.

## File chính (đều mới/sửa, chưa commit)
| File | Vai trò |
|---|---|
| `data_loader.py` | build SQLite. Đã sửa: dedupe chỉ trong đơn (§3.3), ffill ô gộp mã đơn, tiền `1tr440`/`3,185tr`, ngày thiếu năm theo tên sheet, dòng tổng theo cột mã đơn, **CSV "" → NaN**, gọi `std_layer.build_std` cuối build |
| `std_layer.py` | vai trò cột (README `_DESC_ROLES` > heuristic tên), `std_orders`/`std_lines`/`std_products`/`std_repairs`, ghi tắt (`_expand_abbrev`), `canon_models`, dòng điều chỉnh (voucher/điểm/ship) |
| `std_extra.py` | `std_order_money`, `std_payments`, `std_purchases`, `std_installments(+_payments)`, `std_debt_book(+_payments)`, `std_tradeins`, `std_warranties`, `std_customers` (+`customer_key` vào std_orders), `std_stock`, `std_stock_ledger`, `std_lots`, `std_lot_sales`. Chạy SAU build (chưa gắn vào data_loader) |
| `validate_std.py`, `inventory.py`, `schema_roles.py`, `count_intents.py` | kiểm tra/kiểm kê/phân loại câu hỏi |
| `eval.py` | chấm offline theo rulebook §9 (đã test 3000/3000, 13/13 ca biên) |
| `test_time_public.py` | **43/43** câu public thật (teencode: 11/25, T10/2025, th này, bữa ni, tới chừ, tháng liền trước, 30 ngày từ hôm đó...) |
| `time_resolver.py` + `test_time_resolver.py` | giải thời gian §2, 37/37. Đã thêm tháng trơn `6/2025`, `6-2025`, `8/26` (MM/YY khi YY>12) → public **987/1000** câu có kỳ thời gian (trước 811) |
| `formatter.py`, `entity.py`, `test_phase2.py` | **đã test 2026-11-10: formatter 14/14, entity 14/14** (entity test trên tmp_db train) |
| `check_std.py` | đối soát std với gold train (doanh thu tháng) |

## Trạng thái plan v3
- **Phase 0 ✅** (Gate 0 đã duyệt; thêm 2 họ `cancel_rate`, `repair`). Public 1000 câu, 50 họ, top-20 = 81.5%. Top: revenue 13.8, qty_of_product 8.8, count_orders 7.0, comparison_percent 6.3, revenue_by_category 5.3, avg_per_order 4.9, cancel_rate 4.1, installment 3.4, daily_max 3.3, customer_top 2.8, debt_total 2.6, repair 2.4, brand_size_pct 2.4...
- **Phase 1 ✅ — đang chờ GATE 1** (báo cáo đã gửi). Verify: dev 14/14, train 30/30, doanh thu 205/205, tỉ lệ huỷ 53/53, bán chạy 11/11.
  - **3 câu hỏi đang chờ người dùng trả lời:** (1) công nợ ở shop chỉ có ảnh chụp "còn nợ" cuối dữ liệu (dev_007/008/009) mà hỏi ngày trước đó → đề xuất abstain; (2) commit + push `main` chưa; (3) sang Phase 2?
- Phase 2–7: chưa làm.

## Việc tiếp theo (theo plan v3)
- **Phase 2 ✅ — đang chờ GATE 2** (2026-11-10, chưa commit phần cuối). time_resolver: train 37/37, public 51/51, phủ 995/1000 (5 câu còn lại không có kỳ: định tính/giờ đông/địa chỉ). Đã thêm: tháng viết chữ ("tháng ba"), "N ngày tới" = [D, D+N], "N ngày đầu" sau tăng giá → event_window, khoảng tháng "từ th5/2025 đến th7/2025"/"từ 12/25 đến 2/26" → từng tháng (trước đó sai: thiếu tháng giữa, thêm nhầm tháng trước), "N tháng liền nhau tính tới M-Y", "cùng tháng năm trước", "đầu năm tới chừ". entity: test public 30/30 (tmp_db_dev) + train 14/14; thuộc tính trả **list** theo thứ tự trong câu (cả `size`); map tên→mã nhân viên/NCC từ bảng danh mục; từ đồng nghĩa kênh/thanh toán; khớp giữ dấu khi câu có dấu (trà≠trả, gỏi≠gọi, lẻ≠lệ); bỏ nhóm nằm trong tên hàng đã khớp; thanh toán cần 'trả/đơn/bằng' đứng trước ('ghi nợ thêm' không phải lọc).
- **Phase 3 ✅ — đang chờ GATE 3** (2026-11-10, CHƯA commit). `metrics.py` (hàm tính trên std_*: doanh thu/số đơn/TB đơn/số lượng với bộ lọc chung nhóm·hãng·size·đơn vị·kênh·thanh toán·nhân viên·nhóm khách·ca·kê đơn·hoạt chất; huỷ; % thay đổi; tỉ trọng; ngày/tháng cao nhất; top hàng/khách/nhân viên/NCC; ế; đếm ngày đạt ngưỡng; khách ≥N đơn; hàng có mặt ≥N đơn; đơn giá ngày D; tăng giá; nhập hàng + giá nhập BQ; lãi gộp; công nợ đủ §4; tồn kho cuối ngày + giá trị tồn). `run_rules.py` = router regex tạm để kiểm chéo: **train 2856/3000 đúng, sai 45, trống 99, điểm 2811** (`python run_rules.py --answers <answers.jsonl>`); public 847/1000 có đáp án. Sửa std kèm theo: giờ tạo đơn (ca sáng/chiều), cột nhóm hàng nhiều giá trị nhất, hạng khách, tên khách từ bảng khách (bỏ SĐT), cờ kê đơn ('bán theo đơn' ≠ 'giá bán theo đơn vị'), tổng đơn phải trả (tự tính khi thiếu), phiếu thu có cột thời điểm dạng text. Cập nhật std không cần build lại raw: `for f in tmp_db/*.db; do python std_layer.py $f && python std_extra.py $f; done` (~2.5 phút train).
  - Chưa làm (trống): lô hạn dùng (22 câu train), đợt giao (16), trả góp/sửa xe/thu cũ/bảo hành (chủ yếu public), nhập trừ bán, tăng giá không ghi ngày khi shop có bảng lịch sử giá.
  - Rủi ro: luật regex chỉnh trên chính train → điểm train lạc quan; Phase 4 thay đoán intent/slot bằng LLM, giữ metrics.
- Phase 4: router LLM (JSON intent+slots) + tích hợp `agent.ask()`; fallback text-to-SQL chỉ dùng std_*; đổi rulebook public; `std_extra` gắn vào build.
- Phase 5 unanswerable; Phase 6 validation/benchmark Colab; Phase 7 Docker.

## Ghi chú kỹ thuật quan trọng
- README mỗi biz có mục "## Cột trong `File`" (11/14 dev, 25/30 train) → nguồn vai trò cột mạnh nhất; private test nhiều khả năng cũng có.
- Nợ có 3 kiểu: (A) phiếu thu + phải trả (dev_003/006/010) → tính được mọi ngày; (B) chỉ ảnh chụp còn nợ cuối dữ liệu (dev_007/008/009); (C) sổ nợ/sổ góp tay (dev_014). dev_004 chỉ có sổ hạn nợ.
- `count_intents.classify` dùng cho cả `eval.py` (theo intent).
- "đầu/giữa/cuối tháng" chỉ xuất hiện dạng "từ đầu tháng này đến hết hôm nay" (không cài 1–10/11–20).

## Script chạy lại toàn bộ (`/tmp/rebuild_all.sh` — tạo lại nếu /tmp bị xoá)
```bash
cd /Users/nguyenbao/Documents/ZALOAI/DataMate
.venv/bin/python -W ignore inventory.py --data-root ../DATASET/public_test/data --db-dir tmp_db_dev > /tmp/inv_dev.txt 2>&1
.venv/bin/python -W ignore inventory.py --data-root ../DATASET/train/data --db-dir tmp_db > /tmp/inv_train.txt 2>&1
for f in tmp_db_dev/*.db tmp_db/*.db; do .venv/bin/python -W ignore std_extra.py $f > /dev/null 2>>/tmp/extra_err.txt || echo "LỖI $f"; done
.venv/bin/python -W ignore validate_std.py --db-dir tmp_db_dev; .venv/bin/python -W ignore validate_std.py --db-dir tmp_db | tail -1
.venv/bin/python check_std.py --answers /Users/nguyenbao/Downloads/answers.jsonl | tail -1
```
