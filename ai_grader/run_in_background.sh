#!/usr/bin/env bash
# Launch the AI Grader Streamlit app in the background on port 3718.
# Logs to app.log; PID saved to app.pid. Stop with: kill "$(cat app.pid)"
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source /opt/anaconda3/etc/profile.d/conda.sh
conda activate ai_grader
cd "$HERE"

PORT=3718
LOG="$HERE/app.log"
PIDFILE="$HERE/app.pid"

# If an instance is already running, refuse to start a second.
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "AI Grader already running (PID $(cat "$PIDFILE")) on port $PORT."
    exit 0
fi

nohup streamlit run app.py \
    --server.port "$PORT" \
    --server.headless true \
    "$@" > "$LOG" 2>&1 &

echo $! > "$PIDFILE"
echo "AI Grader started in background (PID $(cat "$PIDFILE")) on http://localhost:$PORT"
echo "  logs: $LOG"
echo "  stop: kill \$(cat \"$PIDFILE\")"
