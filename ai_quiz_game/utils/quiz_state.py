import json
import random
import string
import time
from datetime import datetime, timezone
from pathlib import Path
from filelock import FileLock

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def generate_quiz_id():
    return "".join(random.choices(string.digits, k=6))


# ── Question Bank (reusable pool, independent of any quiz) ───────────────────

def qbank_path(bank_id):
    return DATA_DIR / f"qbank_{bank_id}.json"


def save_question_bank(bank_id, data):
    p = qbank_path(bank_id)
    lock = FileLock(str(p) + ".lock")
    with lock:
        with open(p, "w") as f:
            json.dump(data, f, indent=2)


def load_question_bank(bank_id):
    p = qbank_path(bank_id)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def list_question_banks():
    """Return list of bank summary dicts sorted by name."""
    banks = []
    for p in DATA_DIR.glob("qbank_*.json"):
        try:
            with open(p) as f:
                data = json.load(f)
            banks.append({
                "bank_id": data["bank_id"],
                "name": data["name"],
                "total_questions": len(data["questions"]),
                "created_at": data.get("created_at", ""),
            })
        except Exception:
            pass
    return sorted(banks, key=lambda x: x["name"])


def bank_path(quiz_id):
    return DATA_DIR / f"quiz_{quiz_id}_bank.json"


def session_path(quiz_id):
    return DATA_DIR / f"session_{quiz_id}.json"


def quiz_exists(quiz_id):
    return bank_path(quiz_id).exists()


def session_exists(quiz_id):
    return session_path(quiz_id).exists()


def load_bank(quiz_id):
    p = bank_path(quiz_id)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def save_bank(quiz_id, data):
    p = bank_path(quiz_id)
    lock = FileLock(str(p) + ".lock")
    with lock:
        with open(p, "w") as f:
            json.dump(data, f, indent=2)


def _read_session(quiz_id):
    p = session_path(quiz_id)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def _write_session(quiz_id, data):
    p = session_path(quiz_id)
    with open(p, "w") as f:
        json.dump(data, f)


def load_session(quiz_id):
    p = session_path(quiz_id)
    if not p.exists():
        return None
    lock = FileLock(str(p) + ".lock")
    with lock:
        return _read_session(quiz_id)


def create_session(quiz_id):
    session = {
        "quiz_id": quiz_id,
        "status": "lobby",
        "current_question_idx": 0,
        "question_start_time": None,
        "participants": {},
    }
    p = session_path(quiz_id)
    lock = FileLock(str(p) + ".lock")
    with lock:
        _write_session(quiz_id, session)
    return session


def add_participant(quiz_id, participant_id, name, emoji):
    p = session_path(quiz_id)
    lock = FileLock(str(p) + ".lock")
    with lock:
        session = _read_session(quiz_id)
        if session is None:
            return None
        if participant_id not in session["participants"]:
            session["participants"][participant_id] = {
                "name": name,
                "emoji": emoji,
                "score": 0,
                "answers": {},
            }
        _write_session(quiz_id, session)
    return session


def submit_answer(quiz_id, participant_id, answer_indices, time_taken, points):
    p = session_path(quiz_id)
    lock = FileLock(str(p) + ".lock")
    with lock:
        session = _read_session(quiz_id)
        if session is None:
            return
        q_idx = str(session["current_question_idx"])
        p_data = session["participants"].get(participant_id)
        if p_data is not None and q_idx not in p_data["answers"]:
            p_data["answers"][q_idx] = {
                "answer_indices": answer_indices,
                "time_taken": time_taken,
                "points": points,
            }
            p_data["score"] += points
        _write_session(quiz_id, session)


def transition_status(quiz_id, new_status, **kwargs):
    p = session_path(quiz_id)
    lock = FileLock(str(p) + ".lock")
    with lock:
        session = _read_session(quiz_id)
        if session is None:
            return None
        session["status"] = new_status
        for k, v in kwargs.items():
            session[k] = v
        _write_session(quiz_id, session)
    return session


def get_leaderboard(session, top_n=5):
    participants = session.get("participants", {})
    sorted_p = sorted(
        participants.values(),
        key=lambda x: x["score"],
        reverse=True,
    )
    return sorted_p[:top_n]


def calculate_points(time_taken, time_limit):
    """1000 max, 100 minimum for correct answer."""
    if time_taken >= time_limit:
        return 100
    fraction = 1.0 - (time_taken / time_limit)
    return max(100, round(100 + 900 * fraction))
