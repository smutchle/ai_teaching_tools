"""
Shuffles the order of `answers` within each question across every qbank/quiz
JSON in data/ and updates `correct_indices` accordingly. Text content is never
modified — only the index positions change. Run repeatedly is safe (idempotent
distribution-wise: each run reshuffles).

Usage:
    python scripts/shuffle_answer_positions.py
    python scripts/shuffle_answer_positions.py --dry-run
    python scripts/shuffle_answer_positions.py --seed 42
"""
import argparse
import json
import random
import shutil
from collections import Counter
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def shuffle_question(q: dict, rng: random.Random) -> tuple[bool, list[int]]:
    """Shuffle answers in a single question. Returns (changed, new_correct_indices)."""
    answers = q.get("answers")
    correct = q.get("correct_indices")
    if not isinstance(answers, list) or not isinstance(correct, list):
        return False, correct or []
    n = len(answers)
    if n == 0:
        return False, correct

    perm = list(range(n))
    rng.shuffle(perm)
    # perm[new_idx] = old_idx, so build inverse: old_idx -> new_idx
    inverse = [0] * n
    for new_idx, old_idx in enumerate(perm):
        inverse[old_idx] = new_idx

    new_answers = [answers[old_idx] for old_idx in perm]
    new_correct = sorted(inverse[c] for c in correct if 0 <= c < n)

    changed = new_answers != answers or new_correct != correct
    q["answers"] = new_answers
    q["correct_indices"] = new_correct
    return changed, new_correct


def process_file(path: Path, rng: random.Random, dry_run: bool) -> dict:
    with open(path) as f:
        data = json.load(f)
    questions = data.get("questions", [])
    before = Counter(c for q in questions for c in q.get("correct_indices", []))

    changed_count = 0
    for q in questions:
        changed, _ = shuffle_question(q, rng)
        if changed:
            changed_count += 1

    after = Counter(c for q in questions for c in q.get("correct_indices", []))

    if not dry_run:
        backup = path.with_suffix(path.suffix + ".bak")
        if not backup.exists():
            shutil.copy2(path, backup)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    return {
        "path": str(path.name),
        "questions": len(questions),
        "changed": changed_count,
        "before": dict(before),
        "after": dict(after),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Don't write files")
    ap.add_argument("--seed", type=int, default=None, help="Random seed (for reproducibility)")
    args = ap.parse_args()

    rng = random.Random(args.seed)

    targets = sorted(
        list(DATA_DIR.glob("qbank_*.json"))
        + list(DATA_DIR.glob("quiz_*_bank.json"))
    )
    if not targets:
        print(f"No qbank or quiz files found in {DATA_DIR}")
        return

    print(f"Processing {len(targets)} file(s){' (dry run)' if args.dry_run else ''}:\n")
    for path in targets:
        result = process_file(path, rng, args.dry_run)
        print(f"  {result['path']}")
        print(f"    questions: {result['questions']}, changed: {result['changed']}")
        print(f"    correct_indices distribution before: {result['before']}")
        print(f"    correct_indices distribution after:  {result['after']}\n")

    if args.dry_run:
        print("Dry run — no files modified.")
    else:
        print("Done. Originals saved as <name>.bak (only on first run).")


if __name__ == "__main__":
    main()
