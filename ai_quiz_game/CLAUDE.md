# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

```bash
# Foreground (kills any prior instance automatically)
bash run.sh

# Background via nohup (logs to quizblast.log)
bash run_in_background.sh

# Stop
pkill -f "streamlit run ai_quiz_game_app.py"
```

Always use the `genai` conda environment. Both run scripts activate it via `conda activate genai`. Port is **8543**.

To syntax-check Python files without starting the server:
```bash
conda run -n genai python -m py_compile <file.py>
```

## Architecture

This is a Streamlit multipage app. `ai_quiz_game_app.py` is the entry point; pages live in `pages/`. Shared logic lives in `utils/`.

### Two-tier data model

**Question Banks** (`data/qbank_{id}.json`) — reusable pools generated from source materials. Named after the uploaded filename(s). Independent of any quiz.

**Quizzes** (`data/quiz_{id}_bank.json`) — a frozen N-question subset drawn from a bank. Identified by a 6-digit numeric code shared with participants.

**Sessions** (`data/session_{id}.json`) — live game state for a running quiz. Written by the host page, polled by participant pages via `time.sleep(1); st.rerun()`. Protected by `filelock` to handle concurrent writes from multiple participants.

### Real-time sync mechanism

There is no websocket or pub/sub. All pages poll the session JSON on disk every 1-2 seconds and call `st.rerun()`. The host page is the authoritative state machine — it drives status transitions (`lobby → question → answer_reveal → finished`). Participants are read-only except for submitting answers.

### Question generation pipeline

1. `utils/pdf_utils.py` extracts text from PDFs (via `pypdf`) and markdown/text files, then chunks at **40,000 chars** on paragraph boundaries.
2. `utils/llm.py` calls the selected LLM with **25 questions per full chunk**, scaling proportionally for smaller chunks (floor: 5). The system prompt strictly forbids the LLM from using training knowledge — all questions must come from the supplied material.
3. Two providers: **VT ARC** (OpenAI-compatible endpoint) and **Claude Sonnet** (Anthropic SDK). Both use the same prompt builder and JSON parser.

### Scoring

Points per correct answer: `max(100, round(100 + 900 * (1 - time_taken / time_limit)))` — 1000 max for instant answers, 100 minimum if answered at the buzzer.

### Session state pattern

Each Streamlit browser tab has its own `st.session_state`. Participant identity is a UUID stored in session state (`participant_id`), written into the session JSON on join. The host tracks `host_quiz_id` in session state. Page-to-page navigation uses `st.switch_page()`.

## Key configuration (.env)

| Variable | Purpose |
|---|---|
| `OPEN_AI_ENDPOINT` | Base URL for VT ARC OpenAI-compatible API |
| `OPEN_AI_API_KEY` | ARC API key |
| `OPEN_AI_MODEL` | ARC model name |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude Sonnet |
| `ANTHROPIC_MODEL` | Claude model ID (default: `claude-sonnet-4-6`) |

## Tuning constants

| Constant | Location | Default |
|---|---|---|
| `MAX_CHARS_PER_CHUNK` | `utils/pdf_utils.py` | 40,000 |
| `QUESTIONS_PER_CHUNK` | `utils/llm.py` | 25 |
| `MIN_QUESTIONS_PER_CHUNK` | `utils/llm.py` | 5 |
