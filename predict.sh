#!/bin/bash
set -euo pipefail
cd /code
mkdir -p /result/logs /result/tmp_db
model=/code/models/qwen2.5-coder-14b-instruct
test -s "$model/manifest.json" || { echo "Missing prepared model: $model" >&2; exit 1; }
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 NCCL_P2P_DISABLE=1
export LOCAL_LLM_BASE_URL=http://127.0.0.1:8000/v1
export LOCAL_LLM_MODEL=datamate-qwen
unset LOCAL_LLM_FALLBACK_MODEL NIM_FALLBACK_MODEL
python -m vllm.entrypoints.openai.api_server \
  --model "$model" --served-model-name "$LOCAL_LLM_MODEL" \
  --host 127.0.0.1 --port 8000 --tensor-parallel-size 2 \
  --dtype bfloat16 --max-model-len 16384 --gpu-memory-utilization 0.90 \
  --seed 42 > /result/logs/vllm.log 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true' EXIT
ready=0
for ((i=0; i<900; i++)); do
  if ! kill -0 "$server_pid" 2>/dev/null; then
    echo "vLLM exited during startup; see /result/logs/vllm.log" >&2
    exit 1
  fi
  if python -c 'import json,urllib.request; r=urllib.request.urlopen("http://127.0.0.1:8000/v1/models",timeout=2); assert any(x["id"] == "datamate-qwen" for x in json.load(r)["data"])' 2>/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
if (( ! ready )); then
  echo "vLLM did not become ready within 900 seconds; see /result/logs/vllm.log" >&2
  exit 1
fi
python src/main.py --data-root /data/data --questions /data/questions.jsonl \
  --out /result/submission.jsonl --log /result/logs/run_log.jsonl \
  --db-dir /result/tmp_db --overwrite --workers 4 --itpm 10000000
if ! kill -0 "$server_pid" 2>/dev/null; then
  echo "vLLM exited during inference; see /result/logs/vllm.log" >&2
  exit 1
fi
