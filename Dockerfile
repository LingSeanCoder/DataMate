# Bản chỉ chạy luật (không cần GPU, không cần mạng): luật + metrics trên bảng std_*.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py calendar.csv rulebook.md run.sh ./
RUN chmod +x run.sh && mkdir -p /data /result

ENTRYPOINT ["./run.sh"]
