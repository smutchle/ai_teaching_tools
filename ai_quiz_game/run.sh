#!/bin/bash
cd "$(dirname "$0")"
pkill -f "streamlit run ai_quiz_game_app.py" 2>/dev/null && echo "Stopped existing instance." || true
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate genai
streamlit run ai_quiz_game_app.py --server.port 8543
