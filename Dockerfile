FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    LOCAL_LLM_BASE_URL=http://host.docker.internal:8000/v1 \
    LOCAL_LLM_API_KEY=local \
    LOCAL_LLM_MODEL=local

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py data_loader.py main.py rulebook.md ./

# /data  : thư mục chứa các biz (mount từ host)
# /result: nơi ghi submission.jsonl + log + DB tạm
RUN mkdir -p /data /result/logs /result/tmp_db

ENTRYPOINT ["sh", "-c", "mkdir -p /result/logs /result/tmp_db && exec python main.py --data-root /data --out /result/submission.jsonl --log /result/logs/run_log.jsonl --db-dir /result/tmp_db \"$@\"", "--"]
