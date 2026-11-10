#!/bin/sh
# Điểm vào của container. Nhận 2 kiểu mount ở DATA_DIR (mặc định /data):
#   (a) bộ đề nguyên: /data/{questions.jsonl, data/<biz>/..., rulebook.md, calendar.csv}
#   (b) chỉ các thư mục shop: /data/<biz>/... và QUESTIONS=/đường/dẫn/questions.jsonl
set -e
IN="${DATA_DIR:-/data}"
OUT="${OUT_DIR:-/result}"
if [ -f "$IN/questions.jsonl" ]; then ROOT="$IN/data"; Q="$IN/questions.jsonl"; else ROOT="$IN"; Q="${QUESTIONS:?thiếu questions.jsonl}"; fi
mkdir -p "$OUT/logs" "$OUT/tmp_db"
LLM_FLAG="--no-llm"
if [ "${USE_LLM:-0}" = "1" ]; then LLM_FLAG=""; fi  # bản có LLM: cần llama-server trong image (Phase 7b, sau kết quả Phase 6)
exec python main.py --data-root "$ROOT" --questions "$Q" --out "$OUT/submission.jsonl" --log "$OUT/logs/run_log.jsonl" \
  --db-dir "$OUT/tmp_db" --overwrite --workers "${WORKERS:-4}" $LLM_FLAG "$@"
