"""Chấm offline submission.jsonl theo rulebook §9 (không LLM).

    python eval.py --sub submission.jsonl --answers answers.jsonl [--questions questions.jsonl] [--wrong 30]

Điểm: đúng +1, sai -1, bỏ trống/abstain 0. In theo intent (count_intents) và theo biz.
"""
from __future__ import annotations
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))  # dùng module trong src/

import argparse
import json
import re
import unicodedata
from collections import defaultdict

from count_intents import classify


def _load(path: str) -> dict[str, dict]:
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                obj = json.loads(line)
                out[obj["id"]] = obj
    return out


def norm_str(v) -> str:
    """§9: không phân biệt hoa thường, khoảng trắng, dấu câu; dấu tiếng Việt vẫn phải đúng."""
    s = unicodedata.normalize("NFC", str(v)).lower()
    return re.sub(r"[\s\W_]+", "", s)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def same_value(pred, gold) -> bool:
    if _is_num(gold):
        return _is_num(pred) and abs(pred - gold) < 1e-6
    if isinstance(gold, bool):
        return isinstance(pred, bool) and pred == gold
    if isinstance(gold, str):
        return isinstance(pred, str) and norm_str(pred) == norm_str(gold)
    if isinstance(gold, list):
        return isinstance(pred, list) and len(pred) == len(gold) and all(same_value(p, g) for p, g in zip(pred, gold))
    return pred == gold


def same_table(pred, gold) -> bool:
    if not (isinstance(pred, dict) and isinstance(gold, dict)):
        return False
    pc, gc, pr, gr = pred.get("columns"), gold.get("columns"), pred.get("rows"), gold.get("rows")
    if not (isinstance(pc, list) and isinstance(pr, list)) or len(pc) != len(gc) or len(pr) != len(gr):
        return False
    # Cột so theo tên; tên không khớp thì theo vị trí.
    idx = list(range(len(gc)))
    pnames = [norm_str(c) for c in pc]
    if sorted(pnames) == sorted(norm_str(c) for c in gc):
        idx = [pnames.index(norm_str(c)) for c in gc]
    remaining = [list(r) for r in pr]
    for grow in gr:  # thứ tự dòng không quan trọng
        hit = next((i for i, prow in enumerate(remaining)
                    if len(prow) == len(grow) and all(same_value(prow[idx[j]], grow[j]) for j in range(len(grow)))), None)
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def score_one(pred: dict | None, gold: dict) -> int:
    """+1 đúng, -1 sai, 0 bỏ trống."""
    if pred is None or pred.get("type") in (None, "abstain"):
        return 0
    gtype, ptype = gold["type"], pred.get("type")
    if gtype == "unanswerable" or ptype == "unanswerable":
        return 1 if gtype == ptype else -1
    if ptype != gtype:
        return -1
    ans = pred.get("answer")
    if gtype == "table":
        return 1 if same_table(ans, gold["answer"]) else -1
    golds = [gold["answer"]] + (gold.get("alias") or [])
    return 1 if any(same_value(ans, g) for g in golds) else -1


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sub", required=True)
    p.add_argument("--answers", required=True)
    p.add_argument("--questions", default="questions.jsonl")
    p.add_argument("--wrong", type=int, default=30, help="số câu sai in ra")
    p.add_argument("--only-submitted", action="store_true", help="chỉ chấm các id có trong submission")
    a = p.parse_args()

    sub, gold = _load(a.sub), _load(a.answers)
    qs = _load(a.questions)
    ids = [i for i in gold if (i in sub or not a.only_submitted)]

    by_intent: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # đúng, sai, trống
    by_biz: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    wrong = []
    for i in ids:
        s = score_one(sub.get(i), gold[i])
        q = qs.get(i, {})
        slot = {1: 0, -1: 1, 0: 2}[s]
        by_intent[classify(q.get("question", ""))[0]][slot] += 1
        by_biz[q.get("business_id", "?")][slot] += 1
        if s == -1:
            pr = sub.get(i, {})
            wrong.append(f"XX {i} [{q.get('business_id')}] PRED {pr.get('type')}={json.dumps(pr.get('answer'), ensure_ascii=False)[:80]}"
                         f" | GOLD {gold[i]['type']}={json.dumps(gold[i].get('answer'), ensure_ascii=False)[:80]}"
                         f" | {q.get('question', '')[:90]}")

    def table(title, data):
        print(f"\n{title:<14}{'đúng':>6}{'sai':>6}{'trống':>7}{'acc':>8}{'điểm':>7}")
        for k in sorted(data, key=lambda k: -sum(data[k])):
            ok, bad, ab = data[k]
            n = ok + bad + ab
            print(f"{k:<14}{ok:>6}{bad:>6}{ab:>7}{100 * ok / n:>7.1f}%{ok - bad:>7}")

    table("intent", by_intent)
    table("biz", by_biz)
    ok = sum(v[0] for v in by_biz.values())
    bad = sum(v[1] for v in by_biz.values())
    ab = sum(v[2] for v in by_biz.values())
    print("\n".join(wrong[: a.wrong]))
    print(f"\n=== {ok}/{len(ids)} đúng ({100 * ok / max(len(ids), 1):.1f}%), sai {bad}, trống {ab}, ĐIỂM = {ok - bad} ===")


if __name__ == "__main__":
    main()
