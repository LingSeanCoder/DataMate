# TÀI LIỆU CHUYỂN GIAO NGHIỆP VỤ & KỸ THUẬT TOÀN DIỆN (MASTER HANDOFF)
**Cuộc thi:** Zalo AI Challenge 2026 — Bài toán DataMate  
**Mục đích:** Làm tài liệu tham chiếu duy nhất (Single Source of Truth) cho các thành viên trong nhóm (Teammates) và làm System Prompt / Knowledge Base cho các AI Agents thực thi viết code truy vấn dữ liệu.

---

## 1. TỔNG QUAN & NGUYÊN TẮC TỐI THƯỢNG (CORE PRINCIPLES)

### 1.1. Mốc thời gian ("Hôm nay" - `as_of_date`)
*   Mỗi câu hỏi trong `questions.jsonl` đều có trường `as_of_date` (Định dạng `YYYY-MM-DD`). Đây chính là ngày **"hôm nay"** của câu hỏi đó.
*   **Luật chặn tương lai:** File dữ liệu của shop có thể chứa các bản ghi sau ngày `as_of_date`. Đối với các câu hỏi tính theo "hôm nay", mọi bản ghi phát sinh sau `as_of_date` coi như **chưa xảy ra** (phải lọc bỏ hoàn toàn `date <= as_of_date`).
*   **Ngoại lệ duy nhất về trạng thái:** Trạng thái `huỷ` hoặc `trả hàng` của một đơn hàng được lấy theo trạng thái ghi trên đơn đó, **kể cả khi phiếu trả hàng/huỷ đơn được ghi nhận vào một ngày sau `as_of_date`**.

### 1.2. Hệ thống tính điểm
*   Trả lời đúng: **+1 điểm**.
*   Trả lời sai (sai giá trị hoặc sai format): **-1 điểm**.
*   Bỏ trống (`{"type": "abstain"}` hoặc không nộp dòng đó): **0 điểm**.
*   *Chiến lược:* Nếu không chắc chắn hoặc code lỗi, thà trả về `abstain` để bảo toàn điểm số.

---

## 2. QUY CHUẨN ĐỊNH DẠNG ĐẦU RA (STRICT OUTPUT SCHEMA)

Mỗi dòng trong `submission.jsonl` phải có cấu trúc: `{"id": "...", "type": "...", "answer": ...}`.  
**Lưu ý sống còn:** Sai format là tính SAI (-1 điểm). Đáp án chỉ ghi **giá trị**, tuyệt đối không kèm đơn vị (`đ`, `VND`, `cái`, `ly`...), không kèm ký hiệu `%`, không kèm nhãn.

| Type | Format chuẩn | Quy tắc làm tròn & Chi tiết | Ví dụ Đúng | Ví dụ Sai |
| :--- | :--- | :--- | :--- | :--- |
| **`number`** | JSON number | - Không dấu phân cách nghìn.<br>- **Tiền tệ** (doanh thu, nợ, giá, trung bình tiền...): Làm tròn về số nguyên (đồng), nửa đồng (`0.5`) làm tròn lên.<br>- **Số trung bình khác** (không phải tiền): Làm tròn 2 chữ số thập phân. | `4820000`, `18.6` | `"4,82 triệu"`, `"4.820.000"`, `"4820000"` |
| **`percent`** | JSON number | - Ghi giá trị phần trăm (12.4% -> ghi `12.4`).<br>- Làm tròn **2 chữ số thập phân**.<br>- Giảm thì ghi số âm. | `12.4`, `-5.32` | `"12,4%"`, `0.124` |
| **`date`** | JSON string | - Ngày cụ thể: `"YYYY-MM-DD"`.<br>- Câu hỏi theo tháng: `"YYYY-MM"`. | `"2026-08-15"`, `"2025-09"` | `"15/08/2026"`, `"tháng 9/2025"` |
| **`boolean`** | JSON boolean | - Chỉ dùng `true` hoặc `false`. | `true`, `false` | `"có"`, `"không"`, `1` |
| **`string`** | JSON string | - Tên nguyên bản trong dữ liệu, đủ dấu tiếng Việt.<br>- Chấp nhận mọi cách viết tên có trong file của shop, hoặc trả về **Mã** (Mã SP, Mã KH, Mã NV).<br>- Tên khách chép kèm SĐT như trong file vẫn được chấp nhận. | `"Sting dâu 330ml"`, `"SP001"` | `"Sting dầu 330ml"`, `["Sting dâu 330ml"]` |
| **`list`** | JSON array | - Có thứ tự. Dù chỉ có 1 phần tử vẫn phải để trong mảng `[...]`.<br>- Câu hỏi hỏi nhiều số (VD: "nợ thêm bao nhiêu, trả được bao nhiêu") thì trả về list các số theo đúng thứ tự câu hỏi nêu. | `["Cà phê sữa"]`, `[130500, 43500]` | `"Cà phê sữa, Bạc xỉu"`, `"3 thùng 10 chai"` |
| **`table`** | JSON object | - Cấu trúc: `{"columns": [...], "rows": [[...], ...]}`.<br>- Cột đầu là tên đối tượng, các cột sau là số đo theo đúng thứ tự câu hỏi.<br>- Ô chứa số phải là JSON number (làm tròn như luật `number`).<br>- Thứ tự các dòng trong `rows` không ảnh hưởng kết quả chấm. | `{"columns": ["khach_hang", "so_tien_no"], "rows": [["Chị Lan", 3200000]]}` | Ô ghi `"3.200.000đ"` |
| **`unanswerable`**| JSON object | - Cấu trúc: `{"type": "unanswerable", "reason": "..."}` (Hoặc `"answer": null` như trong tập train, trường `reason` nếu có phải là chuỗi string). | `{"id": "...", "type": "unanswerable", "reason": "Thiếu dữ liệu"}` | Trả về `0` khi kỳ hỏi nằm ngoài dữ liệu. |

### Quy tắc sắp xếp (Sorting & Tie-breaking) cho `string` và `list`:
1.  **Top K:** Xếp theo tiêu chí câu hỏi **giảm dần**.
2.  **Câu hỏi "Liệt kê":**
    *   Nếu có số đo: Xếp theo số đo **giảm dần**.
    *   Nếu liệt kê theo hạn dùng: Hạn **sớm nhất** xếp trước.
    *   Nếu chỉ lọc theo điều kiện (không có số đo): Xếp theo **Mã tăng dần**.
3.  **Khi bằng nhau (Tie-breaker):** Hai đối tượng có cùng chỉ số thì xếp theo **Mã tăng dần** (nếu shop không có mã thì xếp theo **Tên tăng dần A-Z**). Nếu câu hỏi chỉ hỏi 1 đối tượng (top 1), lấy đối tượng đứng đầu sau khi đã phân xử bằng mã/tên.

---

## 3. LUẬT XỬ LÝ FILE & LÀM SẠCH DỮ LIỆU (DATA CLEANING & PARSING)

Trước khi tính toán, hệ thống phải xử lý các đặc thù cấu trúc file sau:
1.  **Ô để trống (Merged/Empty Cells) ở các dòng liên tiếp:** Trong các bảng chi tiết đơn hàng, nếu các dòng sau để trống thông tin (Khách hàng, Ghi chú, Trạng thái, Ngày...), chúng **mang cùng giá trị với dòng đầu tiên của nhóm/đơn đó** (Thực hiện `ffill` theo từng đơn hàng). Tuyệt đối không hiểu ô trống đó là "khách lẻ" hay "đơn hoàn thành".
2.  **Trạng thái đơn hàng:** Mỗi đơn chỉ có 1 trong 4 trạng thái: `hoàn thành`, `huỷ`, `trả hàng`, `đang giao`.
    *   *Sổ không có cột trạng thái:* Trạng thái đơn sẽ được ghi ở cột **Ghi chú**. Nếu ghi chú không nhắc gì đến trạng thái (huỷ/trả/đang giao), mặc định đó là đơn **hoàn thành**.
3.  **Xử lý dòng trùng lặp (Duplicate Rows):**
    *   Hai dòng của **cùng một đơn** mà giống hệt nhau ở mọi ô -> **Lỗi xuất file**, chỉ tính 1 lần (`drop_duplicates`).
    *   Hai dòng giống hệt nhau nhưng của **hai đơn khác nhau**, hoặc trong **bảng không ghi theo đơn** (không có mã đơn) -> Là hai lần bán thực tế, **phải tính cả hai**.
4.  **Ngày không ghi năm:** Lấy theo năm của tên Sheet chứa dòng đó.
5.  **Phân giải tên gọi / Nhãn (Entity Resolution):** Các cách viết tắt, không dấu, hoa/thường trong file hay trong câu hỏi chỉ cùng một thứ thì gom là một. Nếu file có Mã (Mã SP, Mã KH, Mã NV) thì **luôn ưu tiên group/join theo Mã**.
6.  **Đơn vị tính (Unit Conversion):** Mỗi mặt hàng có 1 **đơn vị cơ bản** (nhỏ nhất) và có thể có các **đơn vị lớn** (thùng, hộp, lốc, xe...). Hệ số quy đổi đọc ở bảng quy đổi hoặc `README` của shop. Đơn vị không có dòng quy đổi nào thì chính là đơn vị cơ bản (hệ số = 1).

---

## 4. BỘ LUẬT NGHIỆP VỤ CỐT LÕI (CORE BUSINESS LOGIC)

### 4.1. Đơn hàng và Doanh thu
*   **Đơn hợp lệ (Tính vào doanh thu & Đếm số đơn):** Chỉ lấy đơn **không huỷ** và **không trả hàng**. 
    *   *Cực kỳ lưu ý:* Đơn có trả hàng, **kể cả chỉ trả một phần**, bị coi là đơn trả hàng -> **Bỏ cả đơn**. Đơn trả hàng ở kỳ sau (thậm chí sau `as_of_date`) vẫn bị loại khỏi doanh thu của kỳ đặt đơn.
    *   Đơn `đang giao` **vẫn được tính** vào doanh thu và số đơn.
*   **Thời점 ghi nhận đơn:** Luôn tính theo **ngày đặt** (ngày tạo đơn), không tính theo ngày giao hàng hay ngày thu tiền.
*   **Định nghĩa "Doanh thu một đơn" (Tiền hàng / Tiền bán / Bán được bao nhiêu tiền / Thu về bao nhiêu tiền):**
    *   `Doanh thu = Tổng thành tiền các dòng hàng (đã trừ chiết khấu/giảm giá trên từng dòng)`.
    *   **KHÔNG** trừ giảm giá trên cả đơn (khuyến mãi tổng đơn, voucher shop, dùng điểm).
    *   **KHÔNG** cộng thuế VAT.
    *   **KHÔNG** gồm phí giao hàng (phí ship).
    *   *Lưu ý về "Phí phục vụ":* Dòng "Phí phục vụ" **không phải mặt hàng** (không đếm số lượng, không đưa vào xếp hạng mặt hàng), nhưng **VẪN TÍNH vào doanh thu** (kể cả khi làm mẫu số tính tỉ trọng nhóm hàng).
*   **Đơn bị huỷ & Tỉ lệ đơn huỷ:**
    *   Đơn bị huỷ là đơn có trạng thái `huỷ` (Đơn trả hàng KHÔNG phải đơn huỷ).
    *   `Tỉ lệ đơn huỷ (%) = (Số đơn huỷ đặt trong kỳ / Tổng MỌI ĐƠN đặt trong kỳ) * 100`. Mẫu số bao gồm tất cả các trạng thái: hoàn thành, huỷ, trả hàng, đang giao.
*   **Doanh thu theo Hình thức thanh toán:**
    *   Tính theo hình thức **ghi trên đơn lúc mua**, không tính theo các lần trả nợ về sau.
    *   Đơn ghi nợ (mua chịu) không tính vào tiền mặt hay chuyển khoản.
    *   Đơn **COD** là một hình thức riêng biệt, không gộp vào tiền mặt.
*   **Báo cáo tiền mặt theo ca:** Chỉ ghi nhận **tiền mặt thực thu**, tính theo **giờ thu tiền**, bao gồm cả tiền khách trả nợ. (Lưu ý: Khi hỏi Doanh thu/Số đơn theo ca thì vẫn tính từ bảng Đơn hàng theo giờ đặt đơn, không lấy từ báo cáo ca).
*   **Doanh thu theo Nhóm khách:**
    *   Lấy theo cột nhóm khách. Đơn không ghi khách (khách vãng lai / khách lẻ) **không thuộc nhóm nào** (kể cả khi câu hỏi hỏi nhóm "lẻ").
    *   Khi tính tỉ trọng (%) doanh thu của một nhóm khách: Mẫu số là **tổng doanh thu cả shop** cùng kỳ (bao gồm cả đơn khách vãng lai).
*   **So sánh & Tăng trưởng:**
    *   `Phần trăm thay đổi / Tăng trưởng = ((Kỳ hỏi - Kỳ trước) / Kỳ trước) * 100`. Nếu giảm thì ra **số âm**.
    *   `Chênh lệch / Hơn kém bao nhiêu (về số lượng/tiền) = Kỳ hỏi - Kỳ so sánh`. Ít hơn thì ra **số âm**, giữ nguyên đơn vị đo.

### 4.2. Quy ước Thời gian (Time Expressions)
*   **Ngày X, hôm nay, hôm qua:** Chỉ tính các phát sinh **trong** ngày đó.
*   **Đến hết ngày X, tính đến hôm nay:** Hỏi về số còn lại (còn nợ, còn tồn, chưa giao...). Phải tính luỹ kế mọi phát sinh **từ đầu dữ liệu đến hết ngày X** (Không phải chỉ tính trong tháng chứa ngày X).
*   **Từ X đến Y / Nhóm X–Y:** Bao gồm cả biên X và Y (`X <= val <= Y`).
*   **Quá N, hơn N, trên N, dưới N:** Không gồm N (`>` hoặc `<`). **Từ N trở lên:** Gồm cả N (`>=`).
*   **N ngày gần nhất:** N ngày liên tiếp tính **kể cả hôm nay** (`[as_of_date - (N-1), as_of_date]`).
*   **Tuần:** Bắt đầu từ **Thứ Hai** đến Chủ Nhật.
    *   *Tuần này:* Từ Thứ Hai của tuần chứa `as_of_date` đến hết `as_of_date`.
    *   *Tuần trước / Tuần rồi:* Trọn vẹn 7 ngày (Thứ Hai đến Chủ Nhật) của tuần liền trước.
*   **Tháng M/Y:** Từ ngày 1 đến hết ngày cuối cùng của tháng đó.
*   **Tháng này & So sánh cùng kỳ tháng trước:**
    *   *Tháng này:* Từ ngày 1 của tháng chứa `as_of_date` đến hết `as_of_date` (Ví dụ: 01/08 đến 15/08).
    *   *So với cùng kỳ tháng trước:* Lấy **cùng số ngày** của tháng liền trước (từ ngày 1 đến hết ngày trùng số với `as_of_date`, ví dụ: 01/07 đến 15/07). Nếu tháng trước không có ngày đó (VD: 31/03 so với tháng 2) thì lấy đến hết ngày cuối cùng của tháng trước (28/02 hoặc 29/02).
*   **Từ đầu năm / Từ đầu năm tới nay:** Từ `01/01` của năm chứa `as_of_date` đến hết `as_of_date`.
*   **Tháng M, Quý Q không ghi năm:** Mặc định là của **năm nay** (năm của `as_of_date`) nếu tháng/quý đó không nằm sau `as_of_date`. Nếu nằm sau `as_of_date` thì lấy của **năm trước**. (Quý 1: T1-T3, Quý 2: T4-T6, Quý 3: T7-T9, Quý 4: T10-T12).
*   **Dịp Tết / Mùa Tết năm Y:**
    *   Bắt đầu từ ngày dương lịch ứng với **23 tháng Chạp** (năm âm lịch `Y - 1`) đến hết ngày dương lịch ứng với **mùng 10 tháng Giêng** (năm âm lịch `Y`). Tra cứu trong file `calendar.csv`.
    *   *So với trước Tết:* So với khoảng thời gian có **cùng số ngày** (thường là 17 hoặc 18 ngày) nằm ngay liền trước ngày 23 tháng Chạp.
*   **Ca sáng, Ca chiều:** Tính theo giờ tạo đơn.
    *   *Ca sáng:* `06:00:00` đến `13:59:59`.
    *   *Ca chiều:* `14:00:00` đến `21:59:59`.
    *   *So sánh ca chiều với ca sáng:* Mốc (mẫu số) luôn là **ca sáng** (`(Chiều - Sáng) / Sáng * 100`).
*   **Trung bình mỗi ngày thứ X của kỳ** (VD: "trung bình mỗi thứ Tư trong tháng 3"): Chia tổng số đo cho **tổng số ngày thứ X có trong lịch của kỳ đó**, kể cả những ngày thứ X cửa hàng không có đơn/phiếu nào.

### 4.3. Định nghĩa Xếp hạng & Chỉ số (Ranking & Metrics)
*   **Nhóm hàng:** Lọc đúng tên nhóm câu hỏi nêu trong cột Nhóm hàng. Không tự ý gộp các nhóm có tên gần giống nhau.
*   **Bán chạy nhất / Bán nhiều nhất / Hot nhất:** Xếp theo **tổng số lượng quy về đơn vị cơ bản** (bỏ đơn huỷ, trả). Không xếp theo số dòng hay doanh thu. Các mặt hàng khác đơn vị cơ bản vẫn đem số lượng đó ra xếp chung.
*   **Bán được bao nhiêu `<đơn vị>`** (VD: "bán được bao nhiêu gói/chai/ly"): Chỉ cộng số lượng (đã quy về đơn vị cơ bản) của những mặt hàng **có đơn vị cơ bản đúng bằng `<đơn vị>`** (kể cả khi dòng đơn hàng bán theo đơn vị lớn như thùng/hộp). Mặt hàng có đơn vị cơ bản khác thì bỏ qua.
*   **Hàng lời nhất:** Xếp theo **tổng lãi gộp** của mặt hàng.
*   **Mặt hàng được ưa chuộng nhất:** Mặt hàng xuất hiện trong **nhiều đơn hợp lệ nhất** trong kỳ (đếm `count(distinct order_id)`).
*   **Xác định Khách hàng:**
    *   *Shop có mã khách:* Mỗi mã là 1 khách (trùng tên nhưng khác mã vẫn là 2 khách). Đơn không ghi mã (`khách lẻ` / trống) không phải là khách -> **Không đếm và không xếp hạng**.
    *   *Shop không có mã khách:* Xác định theo tên ghi trên đơn. Hai tên chỉ khác cách viết (hoa/thường, viết tắt) là 1 khách; khác chữ hoặc khác số là 2 khách. Đơn ghi `khách lẻ` không tính.
    *   *Khách đã mua trong kỳ:* Khách có ít nhất 1 đơn hợp lệ (không huỷ, không trả) trong kỳ.
*   **Khách mua nhiều tiền nhất:** Xếp theo tổng doanh thu (§4.1).
*   **Khách trung thành nhất:** Khách có **nhiều ngày mua hàng nhất** (đếm số ngày distinct có đơn hợp lệ) trong kỳ.
*   **Khách sộp nhất:** Khách có **giá trị trung bình một đơn** cao nhất trong kỳ, **chỉ xét những khách có từ 3 đơn hợp lệ trở lên** trong kỳ.
*   **Khách hay đổi ý nhất:** Khách có **nhiều đơn bị huỷ nhất** trong kỳ.
*   **Nhân viên bán giỏi nhất:** Xếp theo tổng doanh thu (§4.1), không theo số đơn.
*   **Nhân viên / Thợ chăm chỉ nhất:** Người có **nhiều ngày làm việc nhất** trong kỳ (ngày làm việc = ngày có ít nhất 1 đơn hoặc 1 phiếu, kể cả phiếu bảo hành, do người đó bán hoặc làm).
*   **Ngày đắt khách nhất:** Ngày có **nhiều đơn hợp lệ nhất** trong kỳ (khác với ngày có doanh thu cao nhất).
*   **Giờ cao điểm:** Khung 1 giờ tròn theo giờ tạo đơn (từ `H:00` đến `H:59`) có nhiều đơn hợp lệ nhất trong kỳ. Đáp án trả về `H` (giờ bắt đầu khung, kiểu `number`, VD: `8`, `19`).

### 4.4. Công nợ (Debt Management)
*   **Đơn tính vào công nợ:** Đơn **không huỷ**. **Đơn trả hàng VẪN TÍNH** nếu trên sổ vẫn còn nợ.
*   **Loại trừ COD & TMĐT:** Đơn trả bằng `COD` hoặc bán qua `sàn TMĐT` **tuyệt đối không phải khách nợ** (kể cả khi chưa thu được tiền) -> Bỏ hoàn toàn khỏi công nợ và tuổi nợ.
*   **Nợ của một đơn tại ngày X:**
    *   `Nợ còn lại = Tổng đơn khách phải trả (đã trừ giảm giá cả đơn, CỘNG phí giao hàng) - Tổng các khoản đã trả tính đến hết ngày X`.
    *   Tổng nợ = Cộng các đơn có `Nợ còn lại > 0`.
    *   *Lưu ý khách lẻ:* Đơn của `khách lẻ` (không tên/mã) nếu còn nợ thì **vẫn cộng vào Tổng nợ chung của cửa hàng**, nhưng **không có dòng** trong bảng xếp hạng nợ theo khách.
*   **Tuổi nợ:** Tính bằng số ngày từ **Ngày đặt đơn** đến ngày hỏi `X` (`X - order_date`). Đơn đặt hôm nay có tuổi nợ = `0` ngày, đặt hôm qua = `1` ngày (Không tính theo hạn thanh toán).
*   **Nợ thêm vs. Trả nợ (Thu nợ) trong kỳ:** Chỉ xét các đơn **ghi nợ** (mua chịu) và không huỷ.
    *   **Nợ thêm:** Phần tổng đơn **chưa trả ngay trong ngày mua**, của các đơn được đặt trong kỳ. (Tiền trả ngay trong ngày mua được tính là trả lúc mua, không phải trả nợ).
    *   **Trả nợ / Thu nợ:** Các khoản thanh toán diễn ra **sau ngày mua** (`payment_date > order_date`) có ngày trả nằm trong kỳ, tính trên mọi đơn ghi nợ (kể cả trả cho đơn đặt từ kỳ trước, hoặc đơn trả hàng). Tiền hoàn trả hàng không trừ vào thu nợ.

### 4.5. Kho, Nhập hàng, Giá vốn & Lợi nhuận
*   **Tiền nhập hàng trong kỳ:** Tổng tiền các phiếu nhập có **ngày phiếu** trong kỳ (bỏ phiếu huỷ). Các phiếu/dòng ghi **"nhập đầu kỳ"** được coi như phiếu nhập bình thường: nếu ngày phiếu nằm trong kỳ hỏi thì vẫn cộng vào.
*   **Nhà cung cấp nhập nhiều nhất vs. Nhà cung cấp chính:**
    *   *Nhập nhiều (tiền) nhất:* Tổng tiền nhập cao nhất trong kỳ.
    *   *Nhà cung cấp chính:* Có **số lượng phiếu nhập nhiều nhất** trong kỳ (bỏ phiếu huỷ).
*   **Giá nhập bình quân một mặt hàng trong tháng:** Tính bình quân gia quyền **theo số lượng nhập** (`Tổng tiền nhập mặt hàng đó trong tháng / Tổng số lượng nhập theo đơn vị nhập`), không phải trung bình cộng đơn thuần các mức giá. Bỏ phiếu huỷ.
*   **Nhập trừ bán (Chênh lệch nhập và bán):** Phần bán chỉ lấy số lượng của các đơn hợp lệ (tính vào doanh thu), sau đó quy đổi về **cùng đơn vị** với câu hỏi yêu cầu (hoặc đơn vị nhập) rồi lấy `Nhập - Bán`.
*   **Tồn kho theo đơn vị lớn:** Trả về `list` 2 phần tử: `[số đơn vị lớn nguyên, số đơn vị cơ bản lẻ]`. Nếu không có phần lẻ thì số thứ hai là `0`. Với hàng có nhiều cấp quy đổi (VD: Thùng -> Hộp -> Viên), nếu hỏi tồn bao nhiêu Thùng và lẻ, phần lẻ vẫn phải quy về **đơn vị cơ bản (Viên)**, không đổi sang đơn vị ở giữa (Hộp).
*   **Giá trị hàng tồn kho:** Bằng `Số lượng tồn cuối ngày hôm nay (đơn vị cơ bản) * Giá vốn 1 đơn vị cơ bản ghi trong Danh mục hàng`. Chỉ cộng các mặt hàng có tồn `> 0`.
*   **Mặt hàng hết hàng (cháy hàng / đứt hàng) cuối ngày:** Chỉ xét mặt hàng **mua về để bán** (bỏ qua các món pha chế, chế biến tại chỗ) mà lúc **hết ngày** đó có tồn kho `= 0`. (Hết giữa ngày nhưng nhập thêm trước cuối ngày thì không tính).
*   **Mặt hàng ế (bán chậm / không bán được) trong kỳ:** Là mặt hàng cửa hàng **đã từng bán được ít nhất 1 lần trước kỳ hỏi** (trong đơn hợp lệ), nhưng trong suốt kỳ hỏi không có bất kỳ đơn hợp lệ nào mua mặt hàng đó.
*   **Giá bán mặt hàng X vào ngày D:** Là đơn giá ghi trên dòng bán X của các đơn đặt ngày D, tính cho **1 đơn vị cơ bản**, **trước** khi trừ chiết khấu dòng. *Đặc biệt:* Tính cả dòng của đơn huỷ và đơn trả hàng.
*   **Hiệu ứng tăng giá:**
    *   *Xác định ngày tăng giá D:* Ngày mà giá mới cao hơn giá liền trước (theo bảng giá). Nếu tăng lên cùng một mức giá nhiều lần thì lấy **lần đầu tiên**.
    *   *Cách tính:* So sánh tổng số lượng bán (quy về đơn vị cơ bản, chỉ tính đơn hợp lệ) trong **N ngày kể từ D** (`[D, D + N - 1]`, tức tính cả ngày D) với **N ngày ngay trước D** (`[D - N, D - 1]`).
*   **Giá vốn & Lãi gộp & Lãi ròng:**
    *   `Giá vốn của mỗi dòng bán = Số lượng bán * Giá vốn ghi sẵn trên dòng bán đó` (cùng đơn vị với đơn giá). **Tuyệt đối không** tự tính lại giá vốn từ phiếu nhập hay theo lô/FIFO.
    *   `Lãi gộp = Doanh thu (§4.1) - Tổng giá vốn của hàng đã bán (trong các đơn hợp lệ)`.
    *   `Lãi ròng một tháng = Lãi gộp của tháng - Mọi khoản chi phí ghi trong sổ chi phí của tháng đó` (kể cả chi phí sửa chữa, thay thiết bị...).

---

## 5. LUẬT ĐẶC THÙ THEO TỪNG NGÀNH HÀNG (DOMAIN-SPECIFIC RULES)
*Khi luật ngành khác với luật chung ở Mục 4, **bắt buộc ưu tiên áp dụng luật ngành**.*

### 5.1. Ngành Cà phê / F&B
*   **Size M, Size L:** Xác định bằng các món có tên kết thúc bằng `(M)` hoặc `(L)` (không phân biệt hoa/thường).
*   **Topping ghi chung ô với món:** Món chính và Topping được coi là **hai mặt hàng độc lập**. Số lượng và tiền của topping phải tách ra để cộng vào mặt hàng Topping tương ứng (cùng loại với các dòng topping ghi rời), **không** cộng vào số lượng hay doanh thu của món chính.

### 5.2. Ngành Nhà thuốc (Pharmacy)
*   **Thuốc kê đơn vs. Không kê đơn:** Dựa vào cột đánh dấu kê đơn trong danh mục thuốc. Mọi sản phẩm còn lại là hàng không kê đơn. Tỉ lệ thuốc kê đơn tính theo **doanh thu**.
*   **Doanh thu theo Hoạt chất:** Chỉ cộng doanh thu các thuốc có cột hoạt chất ghi **đúng y hệt như vậy** trong danh mục. Thuốc phối hợp nhiều hoạt chất (VD: `Paracetamol + Phenylephrin + Clorpheniramin`) được coi là **một hoạt chất/loại riêng**, không được cộng vào hoạt chất đơn lẻ (`Paracetamol`).
*   **Lô gần hết hạn nhất (cận date nhất):** Trong các lô **còn tồn (> 0)** và **chưa quá hạn** tại ngày `as_of_date` (`expiry_date >= as_of_date`), lấy lô có hạn dùng sớm nhất.
*   **Hàng hết hạn trong N ngày tới & Tiền vốn đọng:**
    *   Tính tại hôm nay `D` (`as_of_date`): Xét các lô còn tồn có hạn dùng rơi vào khoảng `[D, D + N]` (từ hôm nay đến hết ngày `D + N`).
    *   **Tiền vốn của lô tồn:** Bằng `Số lượng còn tồn của lô * Giá nhập của CHÍNH LÔ ĐÓ (quy về đơn vị cơ bản)`. Không dùng giá vốn bình quân trong danh mục.

### 5.3. Ngành Vật liệu xây dựng (Construction Materials)
*   **Giao hàng nhiều đợt:** Tính đến hết `as_of_date`.
    *   **Số đợt giao trong tháng X:** Đếm các đợt có **ngày giao** rơi vào tháng X.
    *   Mỗi đợt giao của mỗi đơn tính là 1 đợt. **Kể cả khi đơn hàng đó sau này bị trả hàng**, các đợt đã chở giao vẫn được tính bình thường.

### 5.4. Ngành Mẹ và Bé (Mom & Baby)
*   **Tiền giảm voucher, tiền giảm dùng điểm, phí ship:** Luôn trả về **số dương**. Cộng theo **ngày của hoá đơn**, chỉ tính trên các hoá đơn hợp lệ (không huỷ, không trả).
*   **Số sữa, Size bỉm:** Chỉ lấy theo cột `Số / Size` trong bảng Danh mục. Mặt hàng nào để trống cột `Số / Size` thì **không xếp vào bất kỳ số hay size nào** (dù trong tên mặt hàng có ghi chữ L, XL hay số 1, 2...).
*   **Tỉ trọng (%) của 1 size bỉm hoặc 1 hãng bỉm:** Chia số lượng bịch của size/hãng đó cho **tổng số bịch của MỌI mặt hàng thuộc nhóm "bỉm tã"** bán ra cùng kỳ.
*   **Khách thành viên mới trong tháng:** Đếm số khách trong danh sách có `Ngày đăng ký` nằm trong tháng đó, **kể cả những khách đăng ký xong chưa mua đơn nào**.

### 5.5. Ngành Sửa xe (Motorcycle Repair)
*   **Lượt xe / Số phiếu:** Mỗi dòng trong sổ sửa xe là **1 lượt xe (1 phiếu)**. Một biển số xe tới 2 lần tính là 2 lượt. Khi đếm tổng số phiếu / số lượt xe của tiệm hoặc của một thợ: **Tính cả phiếu bảo hành**.
*   **Thợ làm nhiều nhất:** Xếp theo tổng **số lượt xe** (bao gồm cả phiếu bảo hành thợ đó làm).
*   **Tiền trung bình một phiếu:** Bằng `Tổng doanh thu / Số phiếu CÓ THU TIỀN` (Không đưa phiếu bảo hành vào mẫu số).
*   **Phân loại công việc trong ô "Làm gì":**
    *   Một phiếu có làm việc X thì tính là 1 lượt làm việc X. **Phiếu bảo hành KHÔNG tính** là lượt làm công việc đó.
    *   Chữ `Nhớt` ghi trơn -> Hiểu là **nhớt máy** (không phải nhớt láp / dầu láp).
    *   Chữ `Ruột` ghi trơn -> Hiểu là **thay ruột mới** (không phải vá ruột).
    *   **Sửa điện:** Không bao gồm việc thay bóng đèn và thay ắc quy.
*   **Dòng xe & Biển số:**
    *   Tên dòng xe chỉ khác hoa/thường hoặc viết tắt là cùng 1 dòng.
    *   **Số chiếc xe** (hoặc bao nhiêu xe khác nhau): Đếm số **biển số khác nhau** (`count(distinct plate)`), tính cả xe chỉ tới bảo hành.
*   **Khách quen (Xe tới nhiều lần):** Xác định theo **biển số**. Chỉ đếm các phiếu **có thu tiền** (bỏ qua phiếu bảo hành). `Xe khách quen = Biển số có từ 2 phiếu có thu tiền trở lên trong kỳ`. Nếu hỏi "xe nào", trả về biển số.
*   **Tỉ lệ bảo hành:** Bằng `(Số phiếu bảo hành có ngày trong kỳ / Số phiếu có thu tiền trong cùng kỳ) * 100`. Phiếu bảo hành của xe sửa từ kỳ trước vẫn tính vào tử số.

### 5.6. Ngành Cửa hàng Điện thoại (Mobile Shop)
*   **Thu máy cũ (Trade-in):**
    *   **Tiền thu máy cũ** KHÔNG bị trừ khỏi Doanh thu (Doanh thu vẫn tính đủ giá bán máy mới).
    *   **Khách phải trả:** Bằng `Tiền đơn hàng sau khi trừ khuyến mãi/bớt VÀ trừ tiền thu máy cũ` (bao gồm cả phần khách còn nợ lại).
    *   Tổng tiền khách phải trả và tổng tiền thu máy cũ trong kỳ chỉ tính trên đơn hợp lệ (không huỷ, không trả). Máy khách mang tới đổi không phải là máy bán ra.
*   **Trả góp (Installments):**
    *   Đơn trả góp bản chất là **đơn ghi nợ (§4.4)** dù shop có thể ghi ở sổ trả góp riêng. Mọi câu hỏi về khách nợ, mua chịu, trả nợ, thu nợ đều **phải gộp cả đơn trả góp**.
    *   *Doanh thu:* Đã tính đủ 100% giá máy vào ngày mua. Tiền góp hàng kỳ **không** tính vào doanh thu.
    *   *Tiền trả trước:* Tính là tiền trả ngay lúc mua (không phải tiền góp, không tính vào thu nợ).
    *   *Phần góp chưa trả:* Là tiền khách còn nợ. *Tiền góp mỗi kỳ (sau ngày mua):* Là tiền khách trả nợ (tính vào thu nợ).
    *   *Kỳ góp quá hạn:* Kỳ góp có `Ngày hạn < as_of_date` (trước ngày hỏi) mà chưa góp đủ. Kỳ có hạn trùng đúng ngày hỏi (`== as_of_date`) thì **chưa quá hạn** (nhưng nếu câu hỏi hỏi "tới hạn, tính luôn kỳ hạn hôm nay" thì lấy `<= as_of_date`).
*   **Bảo hành:** `Tỉ lệ bảo hành của một quý (%) = (Số lượt bảo hành có ngày nhận trong quý / Tổng số lượng MÁY bán ra trong quý (bỏ đơn huỷ, trả)) * 100`. Lượt bảo hành của máy bán từ quý trước vẫn tính vào tử số.
*   **Dòng máy & Hãng:**
    *   **Dòng máy:** Là phần tên mặt hàng đứng trước dung lượng `...GB` (VD: `iPhone 13 128GB` -> Dòng máy là `iPhone 13`). Nếu tên không có `...GB` thì lấy cả tên (Lưu ý: `Nokia 105 4G` thì `4G` là mạng di động, không phải dung lượng, nên dòng máy là `Nokia 105 4G`).
    *   **Hãng:** Là chữ đầu tiên của tên mặt hàng. Riêng máy `iPhone` thì hãng là **`Apple`**.

---

## 6. NHỮNG CÂU HỎI KHÔNG TRẢ LỜI ĐƯỢC (`unanswerable`)

Hệ thống **bắt buộc** trả về `{"type": "unanswerable", "reason": "..."}` trong các trường hợp sau:
1.  **Hỏi cảm tính, định tính không có trong dữ liệu:** VD: *"Nhân viên nào lành nghề/thân thiện/tận tâm nhất?"*, *"Món nào ngon nhất?"*, *"Mặt hàng nào chất lượng tốt nhất?"*, *"Nhà cung cấp nào uy tín/đáng tin cậy nhất?"*, *"Khách nào trung thực/khó tính nhất?"*.
2.  **Hỏi kiến thức y khoa / bên ngoài:** VD: *"Thuốc này người lớn uống mấy viên?"*, *"Bà bầu dùng thuốc X được không?"*.
3.  **Hỏi thông tin mà Schema của Shop đó không ghi nhận:**
    *   Hỏi giới tính (*"khách nữ chiếm bao nhiêu % doanh thu"*) nhưng bảng khách hàng không có cột Giới tính.
    *   Hỏi địa chỉ (*"khách ở đường Nguyễn Văn Linh"*) nhưng shop không lưu địa chỉ khách.
    *   Hỏi lý do huỷ đơn / trả hàng (*"huỷ vì giao trễ"*, *"trả vì mua nhầm"*) nhưng sổ không có cột lý do hoặc ghi chú không ghi.
    *   Hỏi công nợ, tồn kho, giá vốn, hạn sử dụng ở các shop chỉ có duy nhất bảng Sổ bán hàng (không phát hành file kho/công nợ/nhập hàng).
4.  **Kỳ hỏi nằm HOÀN TOÀN ngoài phạm vi thời gian dữ liệu của shop:**
    *   Ví dụ: Dữ liệu của shop chỉ bắt đầu từ `01/04/2025`, nhưng câu hỏi hỏi doanh thu `tháng 2/2025` hoặc hỏi so sánh `tháng 4/2025 với tháng liền trước (tháng 3/2025)`. Vì tháng 3/2025 hoàn toàn không có dữ liệu nên không thể tính % tăng trưởng -> Trả về `unanswerable` (Tuyệt đối không cho doanh thu tháng 3 bằng 0).
    *   *Lưu ý:* Nếu kỳ hỏi có **một phần** nằm trong khoảng ngày có dữ liệu của shop thì vẫn tính bình thường trên phần dữ liệu hiện có.

---

## 7. CHECKLIST QUY TRÌNH XỬ LÝ DÀNH CHO AI AGENT (PIPELINE WORKFLOW)

Khi nhận một câu hỏi `(id, business_id, question, as_of_date)`:
1.  **Bước 1 (Inspect Schema):** Đọc `README` (nếu có) và tên các file/cột của `business_id`. Xác định shop thuộc ngành nào (Cà phê, Thuốc, VLXD, Sửa xe, Điện thoại, Mẹ & Bé, hay Tạp hoá) để nạp luật Mục 5.
2.  **Bước 2 (Check Unanswerable):** Đối chiếu câu hỏi với Mục 6. Kiểm tra xem cột cần thiết có tồn tại không, hoặc kỳ thời gian hỏi có nằm hoàn toàn trước ngày `min(date)` của toàn bộ dữ liệu shop không.
3.  **Bước 3 (Clean & Filter by `as_of_date`):**
    *   Thực hiện `ffill` cho các dòng chi tiết đơn hàng bị trống thông tin chung.
    *   Xoá dòng duplicate hoàn toàn trong cùng 1 đơn.
    *   Lọc toàn bộ bảng giao dịch theo `date <= as_of_date` (nhưng giữ lại trạng thái đơn).
4.  **Bước 4 (Apply Business Logic):**
    *   Xác định đúng bộ lọc trạng thái đơn (Doanh thu/Số đơn: bỏ `huỷ` & `trả hàng`; Công nợ: chỉ bỏ `huỷ` và bỏ `COD`/`TMĐT`; Tỉ lệ huỷ: lấy mọi đơn làm mẫu số; Giá bán ngày D: lấy mọi đơn).
    *   Thực hiện quy đổi đơn vị cơ bản nếu tính số lượng bán chạy hoặc tồn kho.
5.  **Bước 5 (Format & Validate Output):**
    *   Kiểm tra kỹ kiểu dữ liệu trả về (`number`, `percent`, `date`, `boolean`, `string`, `list`, `table`, `unanswerable`).
    *   Áp dụng quy tắc làm tròn (`round(val + 1e-9)` cho tiền tệ về đồng nguyên; `round(val, 2)` cho percent).
    *   Kiểm tra thứ tự sắp xếp và tie-breaker (theo Mã tăng dần).