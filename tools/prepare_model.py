#!/usr/bin/env python3
"""Download the fixed inference snapshot before building the offline image."""
import argparse
import hashlib
import json
from pathlib import Path


REPO = "Qwen/Qwen2.5-Coder-14B-Instruct"
REVISION = "aedcc2d42b622764e023cf882b6652e646b95671"
DEST = Path(__file__).resolve().parents[1] / "models" / "qwen2.5-coder-14b-instruct"


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=REPO, revision=REVISION, local_dir=DEST)
    index = json.loads((DEST / "model.safetensors.index.json").read_text())
    required = set(index["weight_map"].values()) | {
        "config.json", "tokenizer.json", "tokenizer_config.json", "LICENSE"
    }
    missing = [name for name in sorted(required) if not (DEST / name).is_file()]
    if missing:
        raise RuntimeError(f"Incomplete model snapshot: {missing}")
    files = []
    for path in sorted(DEST.iterdir()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        files.append({"path": path.name, "size_bytes": path.stat().st_size, "sha256": digest.hexdigest()})
    (DEST / "manifest.json").write_text(json.dumps({"source": REPO, "revision": REVISION, "files": files}, indent=2) + "\n")
    print(f"Prepared {DEST}; {len(files)} files recorded in manifest.json")


if __name__ == "__main__":
    main()
