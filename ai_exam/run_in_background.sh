#!/usr/bin/env bash
# Background launcher for the ai_exam Streamlit observation UI.
# Sibling-app convention: nohup, log to <app>.log, port 8550.

cd "$(dirname "$0")"
pkill -f "streamlit run ui/streamlit_app.py" 2>/dev/null && echo "Stopped existing instance." || true

source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate genai

nohup streamlit run ui/streamlit_app.py \
  --server.port 8550 \
  --server.headless true \
  > ai_exam_ui.log 2>&1 &
echo "ai_exam UI started (PID $!) on port 8550. Logs: ai_exam_ui.log"
