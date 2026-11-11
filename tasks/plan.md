# Plan: DataMate từ 1/20 lên 20/20 (và tổng quát cho 3000 câu)

Ngày: 2026-11-09. Hạn Docker: 16/11. Model: Qwen2.5-Coder-14B (Colab dev, 2×5090 khi chấm).

## Mục tiêu
- 20 câu baseline (biz_106, biz_124) đúng 20/20 trên Colab.
- Không hard-code theo id câu hỏi / business id (luật 4). Mỗi bước sửa phải đúng cho **cả họ câu hỏi** trên 3000 câu train, không chỉ câu trong 20.
- Deterministic (|PA chênh| ≤ 0.03): temperature 0, không sampling.

## Chẩn đoán 20 câu (theo họ, không theo id)

| Họ câu hỏi | Câu trong 20 | Có sẵn? | Thiếu gì | Số câu train cùng họ |
|---|---|---|---|---|
| Doanh thu tháng / nhiều tháng / tăng liên tục | 00c4, 09ae, 0183, 0fc3 | std_orders ✅ | model chưa dùng std; time resolver; boolean so sánh | ~1159 |
| Số lượng 1 mặt hàng trong tháng | 0d56, 0743 | std_lines.qty_base ✅ | khớp tên hàng (fuzzy, size M/L) | ~349 |
| Top-k bán chạy tuần này | 019e | std_lines ✅ | tuần Thứ 2→as_of; tie-break mã | ~337 |
| Doanh thu theo hoạt chất | 00195, 0f62 | ❌ | join std_lines.product_id ↔ bảng thuốc, so khớp hoạt chất **y hệt** (§5.2) | ~66 |
| Hiệu ứng tăng giá (N ngày) | 02b5, 0d3c, 0bf1 | qty_base ✅ | tìm ngày tăng giá từ lịch sử giá (lần đầu); cửa sổ [D, D+N-1] vs [D-N, D-1] | ~50 |
| Công nợ đến hết ngày X | 026f, 073a | ❌ | std_payments + công thức §4.4 (loại huỷ, COD, TMĐT; cộng ship, trừ giảm cả đơn) | ~90 |
| Nhập hàng (tổng tháng, giá bình quân) | 05d6, 0266 | ❌ | std_purchases (bỏ phiếu huỷ, bình quân gia quyền theo SL) | ~86 |
| Tồn kho (hộp chẵn + lẻ, giá trị theo vốn) | 0794, 0a6d | ❌ | std_stock (tồn cuối, quy đổi, giá vốn danh mục) | ~52 |
| Lô gần hết hạn còn tồn | 0eef | ❌ | std_lots (tồn lô = nhập − bán theo lô, HSD ≥ as_of) | ~9+ |

Kết luận: 4/20 đã giải được bằng std hiện có nếu model dùng nó; 16/20 cần thêm bảng std + hàm nghiệp vụ.

## Kiến trúc đích (thay đổi tối thiểu trên agent.py hiện có)

```
câu hỏi ─► [1] Router (LLM, JSON ngắn: intent + slots)  ─► [2] Metric library (Python, deterministic, trên std_*)
                │ intent không khớp / thiếu slot                      │ ra số
                ▼                                                     ▼
          [3] Text-to-SQL cũ (agent.py) nhưng prompt chỉ có std_* + bảng phụ   ─► [4] Formatter §2 (làm tròn, type, sort, tie-break)
                                                                                  ─► [5] Abstain gate (lỗi / nghi ngờ → abstain)
```
- **[2] là nơi lấy điểm**: mỗi họ câu hỏi = 1 hàm Python có test với gold train. LLM 14B chỉ phải hiểu câu hỏi (intent + tên hàng + khoảng thời gian), không phải tự nhớ luật §4–5.
- **Time resolver** (tháng này, tuần này, N ngày gần nhất, tháng M không năm, Tết…) viết bằng Python, test riêng (§4.2). Không để LLM tính ngày.
- **Entity grounding**: khớp tên hàng/hoạt chất trong câu với giá trị thật trong std (rapidfuzz/không dấu), giữ size (M)/(L).
- Fallback [3] vẫn giữ để không mất các họ chưa có hàm.

## Đồ thị phụ thuộc

```
T0 eval harness ──┬─► T1 time resolver ─┬─► T3 router+metric doanh thu/số lượng/top-k ─► CP1
                  │                     │
T2 formatter ─────┘                     ├─► T4 hoạt chất ─┐
                                        ├─► T5 tăng giá ──┤
                  std_payments ─► T6 công nợ ────────────┤
                  std_purchases ─► T7 nhập hàng ─────────┼─► CP2 ─► T10 abstain/unanswerable ─► T11 Colab run + Docker ─► CP3
                  std_stock ─► T8 tồn kho ───────────────┤
                  std_lots ─► T9 lô hạn dùng ────────────┘
```

## Checkpoints
- **CP1** (sau T0–T3): 4 câu dễ trong 20 đúng trên Colab; check họ doanh thu/số lượng/top-k trên train ≥ 90%.
- **CP2** (sau T4–T9): 19/20 offline (gọi thẳng metric library, không LLM); mỗi họ ≥ 85% trên train.
- **CP3** (sau T10–T11): 20/20 trên Colab với Qwen 14B; chạy 150 câu (5/biz) không tụt so với lần trước; thời gian/câu đủ cho 6 giờ.

## Rủi ro
- Router 14B hiểu sai intent → giảm bằng few-shot lấy từ train (nhiều cách viết teencode) + validate slot bằng Python.
- Overfit 20 câu → mọi task đo trên cả họ câu hỏi trong 3000 câu train, tách dev theo biz.
- Thời gian: 7 ngày. Ưu tiên theo số câu train (doanh thu > số lượng/top-k > huỷ > nợ > nhập > hoạt chất > tồn > tăng giá > lô).
