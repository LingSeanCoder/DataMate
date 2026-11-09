"""
main.py — Chạy batch toàn bộ questions.jsonl -> submission.jsonl (multi-business).

Cấu trúc dữ liệu train:
    data/
      biz_101/  ban_hang.csv    README.md
      biz_102/  DonHang_ChiTiet.xlsx ... README.md

Mỗi câu hỏi có `business_id`. Chương trình:
  1. Đọc questions.jsonl, GROUP theo business_id.
  2. Với từng business:
       - tìm thư mục `{data_root}/{business_id}`
       - build SQLite riêng vào `{db_dir}/{business_id}.db` (nếu chưa có / --rebuild /
         dữ liệu nguồn mới hơn DB)
       - đọc `README.md` NGAY TRONG thư mục đó
       - đưa DB + README cho agent, chạy các câu hỏi của biz đó
  3. Ghi submission.jsonl ngay sau mỗi câu (resume được khi bị ngắt).

Chạy:
    python main.py --data-root ../train/train/data --questions ../train/train/questions.jsonl
    python main.py --rebuild --workers 2
    python main.py --biz biz_103 --limit-per-biz 5 --show-sql
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from agent import DataMateAgent, TokenRateLimiter, DEFAULT_MODEL
from data_loader import build_database

DEFAULT_QUESTIONS = "questions.jsonl"
DEFAULT_OUTPUT = "submission.jsonl"
DEFAULT_LOG = "run_log.jsonl"
DEFAULT_DB_DIR = "tmp_db"


# --------------------------------------------------------------------------
# Đọc / ghi JSONL
# --------------------------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    """Đọc JSONL chịu được BOM, dòng trống và dòng hỏng."""
    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig", errors="replace") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                print(f"[CẢNH BÁO] {path.name}:{lineno} không phải JSON, bỏ qua.",
                      file=sys.stderr)
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _first_present(obj: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = obj.get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def normalise_question(obj: dict, index: int) -> dict:
    """Chuẩn hoá tên trường giữa các phiên bản đề bài."""
    qid = _first_present(obj, ("id", "question_id", "qid", "uid")) or f"q_{index:05d}"
    biz = _first_present(obj, ("business_id", "biz_id", "business", "biz", "shop_id"))
    question = _first_present(obj, ("question", "query", "text", "content", "q"))
    as_of = _first_present(obj, ("as_of_date", "as_of", "date", "today", "current_date"))
    return {"id": qid, "business_id": biz, "question": question, "as_of_date": as_of}


def group_by_business(questions: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for q in questions:
        groups.setdefault(q["business_id"] or "", []).append(q)
    return groups


# --------------------------------------------------------------------------
# Build DB theo business
# --------------------------------------------------------------------------

def _readme_path(biz_dir: Path) -> Path | None:
    direct = biz_dir / "README.md"
    if direct.is_file():
        return direct
    for candidate in biz_dir.iterdir():
        if candidate.is_file() and candidate.name.lower() == "readme.md":
            return candidate
    return None


def _needs_build(biz_dir: Path, db_path: Path) -> bool:
    if not db_path.is_file():
        return True
    try:
        db_mtime = db_path.stat().st_mtime
    except OSError:
        return True
    newest = 0.0
    for path in biz_dir.iterdir():
        if path.is_file() and path.suffix.lower() in (
            ".xlsx", ".xls", ".xlsm", ".csv", ".txt", ".sqlite", ".db"
        ):
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    return newest > db_mtime


def ensure_database(
    biz_id: str,
    data_root: Path,
    db_dir: Path,
    rebuild: bool,
    skip_build: bool,
) -> tuple[Path, bool, str | None]:
    """Trả về (db_path, built, warning)."""
    biz_dir = data_root / biz_id
    if not biz_dir.is_dir():
        return db_dir / f"{biz_id}.db", False, f"Không thấy thư mục {biz_dir}"

    db_path = db_dir / f"{biz_id}.db"
    if skip_build and db_path.is_file():
        return db_path, False, None
    if not rebuild and not _needs_build(biz_dir, db_path):
        return db_path, False, None

    db_dir.mkdir(parents=True, exist_ok=True)
    build_database(biz_dir, db_path, verbose=False)
    return db_path, True, None


# --------------------------------------------------------------------------
# Chạy 1 business
# --------------------------------------------------------------------------

def run_business(
    biz_id: str,
    questions: list[dict],
    data_root: Path,
    db_dir: Path,
    args: argparse.Namespace,
    limiter: TokenRateLimiter,
    write_lock: threading.Lock,
    out_fh,
    log_fh,
    done_ids: set[str],
) -> dict:
    started = time.monotonic()
    summary = {"business_id": biz_id, "questions": len(questions), "written": 0,
               "errors": 0, "built": False, "warning": None}

    if not biz_id:
        summary["warning"] = "Câu hỏi thiếu business_id"
        return summary

    try:
        db_path, built, warning = ensure_database(
            biz_id, data_root, db_dir, args.rebuild, args.skip_build
        )
        summary["built"] = built
        summary["warning"] = warning
        if warning:
            return summary
    except Exception as exc:  # noqa: BLE001
        summary["warning"] = f"Build DB lỗi: {type(exc).__name__}: {exc}"
        return summary

    biz_dir = data_root / biz_id
    readme_file = _readme_path(biz_dir)
    readme_text = (
        readme_file.read_text(encoding="utf-8", errors="replace")
        if readme_file else ""
    )

    try:
        agent = DataMateAgent(
            db_path=db_path,
            readme_text=readme_text,
            rulebook_path=args.rulebook,
            calendar_path=args.calendar,
            model=args.model,
            max_fix_attempts=args.max_fix,
            prompt_budget=args.prompt_budget,
            rate_limiter=limiter,
            review_empty=not args.no_review_empty,
            full_rulebook=args.full_rulebook,
            verbose=args.verbose,
        )
    except Exception as exc:  # noqa: BLE001
        summary["warning"] = f"Khởi tạo agent lỗi: {type(exc).__name__}: {exc}"
        return summary

    pending = [q for q in questions if q["id"] not in done_ids]
    summary["skipped"] = len(questions) - len(pending)

    def work(item: dict) -> tuple[dict, dict]:
        answer, debug = agent.ask(
            item["question"], item["as_of_date"] or None, item["id"]
        )
        return item, answer, debug

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(work, item): item for item in pending}
        for future in as_completed(futures):
            item = futures[future]
            try:
                _, answer, debug = future.result()
            except Exception as exc:  # noqa: BLE001
                answer = {"id": item["id"], "type": "abstain"}
                debug = {"id": item["id"], "note": f"{type(exc).__name__}: {exc}"}

            with write_lock:
                out_fh.write(json.dumps(answer, ensure_ascii=False) + "\n")
                out_fh.flush()
                log_record = {
                    "business_id": biz_id,
                    "id": item["id"],
                    "question": item["question"],
                    "as_of_date": item["as_of_date"],
                    "answer": answer,
                    **{k: v for k, v in debug.items() if k not in ("id", "question")},
                }
                log_fh.write(json.dumps(log_record, ensure_ascii=False, default=str) + "\n")
                log_fh.flush()
                done_ids.add(item["id"])
                summary["written"] += 1
                if answer.get("type") in ("abstain", "unanswerable"):
                    summary["errors"] += 1

                if args.show_sql and debug.get("final_sql"):
                    print(f"\n[{biz_id}] {item['id']}")
                    print("  Q:", item["question"])
                    print("  SQL:", debug["final_sql"])
                    print("  A:", json.dumps(answer, ensure_ascii=False))

    summary["elapsed_sec"] = round(time.monotonic() - started, 1)
    return summary


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="DataMate batch runner (multi-business)")
    p.add_argument("--data-root", default="./data",
                   help="Thư mục chứa các thư mục doanh nghiệp (VD: ./data)")
    p.add_argument("--questions", default=DEFAULT_QUESTIONS,
                   help="Đường dẫn questions.jsonl")
    p.add_argument("--out", default=DEFAULT_OUTPUT, help="Đường dẫn submission.jsonl")
    p.add_argument("--log", default=DEFAULT_LOG, help="Đường dẫn run_log.jsonl")
    p.add_argument("--db-dir", default=DEFAULT_DB_DIR,
                   help="Thư mục chứa DB tạm của từng biz (VD: ./tmp_db)")
    p.add_argument("--rulebook", default="rulebook.md")
    p.add_argument("--calendar", default="calendar.csv")
    p.add_argument("--model", default=None,
                   help="Tên model (mặc định: LOCAL_LLM_MODEL/NIM_MODEL/DEFAULT_MODEL)")
    p.add_argument("--workers", type=int, default=5, help="Số luồng chạy song song (NIM cho phép cao hơn Groq)")
    p.add_argument("--itpm", type=int, default=6800,
                   help="Giới hạn input token/phút của Groq (free tier: 7000)")
    p.add_argument("--prompt-budget", type=int, default=None,
                   help="Trần token input mỗi câu (mặc định 6300)")
    p.add_argument("--max-fix", type=int, default=3, help="Số lần tự sửa SQL")
    p.add_argument("--limit", type=int, default=None,
                   help="Chỉ chạy N câu đầu (tổng, để test)")
    p.add_argument("--limit-per-biz", type=int, default=None,
                   help="Chỉ chạy N câu đầu của mỗi business")
    p.add_argument("--biz", default=None,
                   help="Chỉ chạy business này (có thể lặp lại, cách nhau dấu phẩy)")
    p.add_argument("--overwrite", action="store_true",
                   help="Xoá submission cũ, chạy lại từ đầu")
    p.add_argument("--rebuild", action="store_true", help="Build lại toàn bộ DB")
    p.add_argument("--skip-build", action="store_true",
                   help="Dùng DB đã có, không build lại")
    p.add_argument("--full-rulebook", action="store_true",
                   help="Nhồi toàn bộ rulebook (cần tier trả phí)")
    p.add_argument("--no-review-empty", action="store_true",
                   help="Không rà lại khi SQL trả 0 dòng")
    p.add_argument("--show-sql", action="store_true", help="In SQL từng câu")
    p.add_argument("--verbose", action="store_true")
    return p


def run_batch(args: argparse.Namespace) -> int:
    questions_path = Path(args.questions)
    if not questions_path.is_file():
        print(f"[LỖI] Không thấy file câu hỏi: {questions_path}", file=sys.stderr)
        print("      Truyền --questions đường/dẫn/questions.jsonl", file=sys.stderr)
        return 2

    data_root = Path(args.data_root)
    if not data_root.is_dir():
        print(f"[LỖI] Không thấy thư mục dữ liệu: {data_root}", file=sys.stderr)
        return 2

    raw = read_jsonl(questions_path)
    questions = [normalise_question(obj, i + 1) for i, obj in enumerate(raw)]
    questions = [q for q in questions if q["question"]]
    for q in questions:
        if not q["business_id"]:
            print(f"[CẢNH BÁO] {q['id']} thiếu business_id.", file=sys.stderr)

    if args.biz:
        wanted = {b.strip() for b in args.biz.split(",") if b.strip()}
        questions = [q for q in questions if q["business_id"] in wanted]
    if args.limit:
        questions = questions[: args.limit]

    if not questions:
        print("[LỖI] Không có câu hỏi nào để chạy.", file=sys.stderr)
        return 2

    groups = group_by_business(questions)

    # Áp dụng --limit-per-biz SAU khi nhóm theo business_id,
    # trước khi in thông báo và chạy xử lý.
    if args.limit_per_biz:
        groups = {
            biz_id: biz_questions[: args.limit_per_biz]
            for biz_id, biz_questions in groups.items()
        }

    out_path = Path(args.out)
    log_path = Path(args.log)
    db_dir = Path(args.db_dir)

    done_ids: set[str] = set()
    if out_path.is_file() and not args.overwrite:
        for row in read_jsonl(out_path):
            if row.get("id"):
                done_ids.add(str(row["id"]))
        print(f"Resume: đã có {len(done_ids)} câu trong {out_path.name}.")

    out_mode = "w" if args.overwrite else "a"
    log_mode = "w" if args.overwrite else "a"
    if args.overwrite and out_path.exists():
        out_path.unlink()
    if args.overwrite and log_path.exists():
        log_path.unlink()

    print(f"Tổng {len(questions)} câu / {len(groups)} doanh nghiệp.")
    print(f"Data root : {data_root}")
    print(f"DB dir    : {db_dir}")
    model_name = (
        args.model
        or os.getenv("LOCAL_LLM_MODEL")
        or os.getenv("NIM_MODEL")
        or DEFAULT_MODEL
    )
    endpoint = (
        os.getenv("LOCAL_LLM_BASE_URL", "").strip()
        or "https://integrate.api.nvidia.com/v1"
    )
    print(f"Model     : {model_name}")
    print(f"Endpoint  : {endpoint}\n")

    limiter = TokenRateLimiter(args.itpm)
    write_lock = threading.Lock()
    summaries: list[dict] = []
    started = time.monotonic()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open(out_mode, encoding="utf-8") as out_fh, \
            log_path.open(log_mode, encoding="utf-8") as log_fh:
        for biz_id, biz_questions in groups.items():
            print(f"--- {biz_id or '(thiếu business_id)'}: "
                  f"{len(biz_questions)} câu ---", flush=True)
            summary = run_business(
                biz_id, biz_questions, data_root, db_dir, args, limiter,
                write_lock, out_fh, log_fh, done_ids,
            )
            summaries.append(summary)
            done = summary.get("written", 0)
            note = summary.get("warning")
            extra = f" | {note}" if note else ""
            print(f"    xong {done} câu"
                  f"{' (đã build DB)' if summary.get('built') else ''}"
                  f"{extra}", flush=True)

    elapsed = time.monotonic() - started
    total_written = sum(s.get("written", 0) for s in summaries)
    total_skipped = sum(s.get("skipped", 0) for s in summaries)
    missing = [s["business_id"] for s in summaries if s.get("warning")]
    print(f"\nHoàn tất: {total_written} câu mới, bỏ qua {total_skipped} câu đã có, "
          f"{elapsed / 60:.1f} phút.")
    if missing:
        print(f"[CẢNH BÁO] {len(missing)} business lỗi/thiếu: {', '.join(missing)}")
    print(f"Đáp án: {out_path}")
    print(f"Log   : {log_path}")
    return 0


def _force_utf8_stdio() -> None:
    """Console Windows mặc định cp1252 -> in tiếng Việt sẽ crash."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        return run_batch(args)
    except FileNotFoundError as exc:
        print(f"[LỖI] {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n[ DỪNG ] Đã Ctrl+C. Chạy lại lệnh cũ để tiếp tục (resume).",
              file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"[LỖI] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
