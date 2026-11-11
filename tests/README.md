# tests/ — Test tự động

Chạy từ thư mục gốc repo; mỗi file in `N/N pass`.

| File | Kiểm tra | Kỳ vọng |
|---|---|---|
| `test_time_resolver.py` | `time_resolver` trên 34 câu train thật (kỳ vọng tính tay). | 37/37 |
| `test_time_public.py` | `time_resolver` trên câu public có teencode. | 60/60 |
| `test_phase2.py` | `formatter` (§9) + `entity` (khớp tên thật trong DB; cần `tmp_db/` đã build). | 14/14 + 14/14 + 30/30 |

```bash
python tests/test_time_resolver.py && python tests/test_time_public.py && python -W ignore tests/test_phase2.py
```
