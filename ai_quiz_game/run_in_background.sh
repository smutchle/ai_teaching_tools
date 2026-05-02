#!/bin/bash
cd "$(dirname "$0")"
pkill -f "streamlit run Home.py" 2>/dev/null && echo "Stopped existing instance." || true
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate genai
nohup streamlit run Home.py --server.port 8543 > quizblast.log 2>&1 &
echo "QuizBlast started (PID $!) on port 8543. Logs: quizblast.log"
