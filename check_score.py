import json

def load_jsonl(filepath):
    data = {}
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                obj = json.loads(line)
                data[obj['id']] = obj
    return data

def evaluate(sub_path, ans_path):
    print(f"Đang so sánh: {sub_path} với {ans_path}...")
    subs = load_jsonl(sub_path)
    anss = load_jsonl(ans_path)

    correct = 0
    wrong = 0
    abstain = 0
    wrong_details = []

    for q_id, ans in anss.items():
        if q_id not in subs:
            abstain += 1
            continue

        sub = subs[q_id]

        # Nếu mày chọn abstain hoặc bị lỗi hệ thống -> 0 điểm
        if sub.get('type') == 'abstain' or sub.get('type') == 'unanswerable':
            abstain += 1
            continue

        # So sánh (Lưu ý: Code này so sánh cơ bản, thực tế BTC chấm chặt hơn)
        if sub.get('type') == ans.get('type') and str(sub.get('answer')).strip() == str(ans.get('answer')).strip():
            correct += 1
        else:
            wrong += 1
            wrong_details.append(f"Câu {q_id}: Sub={sub.get('answer')} | Ans={ans.get('answer')}")

    total = len(anss)
    pa_score = (correct - wrong) / total if total > 0 else 0

    print(f"\n--- KẾT QUẢ ---")
    print(f"Tổng số câu : {total}")
    print(f"✅ Đúng (+1) : {correct}")
    print(f"❌ Sai (-1)  : {wrong}")
    print(f"⚪ Bỏ qua (0): {abstain}")
    print(f"👉 PA SCORE  : {pa_score:.4f} ({pa_score*100:.2f}%)")

    if wrong_details:
        print("\n--- CHI TIẾT CÂU SAI (Tối đa 5 câu) ---")
        for detail in wrong_details[:5]:
            print(detail)

if __name__ == "__main__":
    # Trỏ đến file submission vừa chạy và file đáp án gốc của BTC
    evaluate("submission.jsonl", "..\\..\\train\\train\\answers.jsonl")
