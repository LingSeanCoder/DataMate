FROM vllm/vllm-openai:v0.10.1.1

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 NCCL_P2P_DISABLE=1
WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir --no-deps nvidia-nccl-cu12==2.27.7
COPY models/qwen2.5-coder-14b-instruct/ /code/models/qwen2.5-coder-14b-instruct/
RUN test -s /code/models/qwen2.5-coder-14b-instruct/manifest.json && \
    python -c 'import json,pathlib; p=pathlib.Path("/code/models/qwen2.5-coder-14b-instruct"); m=json.loads((p/"manifest.json").read_text()); assert m["revision"] == "aedcc2d42b622764e023cf882b6652e646b95671"; assert all((p/f["path"]).is_file() and (p/f["path"]).stat().st_size == f["size_bytes"] for f in m["files"])'
COPY src/ src/
COPY data/ training_code/data/
COPY tools/ tools/
COPY calendar.csv rulebook.md README.md predict.sh ./
RUN mkdir -p /data /result /code/training_code && \
    python -c 'import hashlib,json,pathlib; p=pathlib.Path("/code/training_code/data"); files=[{"path":str(f), "size_bytes":f.stat().st_size,"sha256":hashlib.sha256(f.read_bytes()).hexdigest()} for f in sorted(p.rglob("*")) if f.is_file()]; pathlib.Path("/code/training_code/data_manifest.json").write_text(json.dumps({"source":"Repository data/ (original source/version not recorded)", "splits_counts":"Not recorded; question/answer files absent from repository", "files":files},ensure_ascii=False,indent=2)+"\n")'
ENTRYPOINT []
CMD ["bash", "/code/predict.sh"]
