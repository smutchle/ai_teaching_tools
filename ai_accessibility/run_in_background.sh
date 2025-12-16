#!/bin/bash

# WCAG Accessibility Converter - Background Runner
# Activates conda genai, kills any previous instance, and starts on port 9997

PORT=9997
APP_NAME="ai_access_app.py"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Kill any existing Streamlit instance running our app on the port
echo "Checking for existing Streamlit process on port $PORT..."
# Find PIDs that are both listening on the port AND are streamlit/python processes for our app
for PID in $(lsof -ti:$PORT 2>/dev/null); do
    # Check if this PID is a streamlit or python process
    CMDLINE=$(ps -p $PID -o args= 2>/dev/null)
    if [[ "$CMDLINE" == *"streamlit"* ]] || [[ "$CMDLINE" == *"$APP_NAME"* ]]; then
        echo "Killing existing Streamlit process (PID: $PID) on port $PORT..."
        kill -9 $PID 2>/dev/null
        sleep 1
    fi
done

# Activate conda environment
echo "Activating conda genai environment..."
source /opt/anaconda3/etc/profile.d/conda.sh
conda activate genai

# Change to script directory
cd "$SCRIPT_DIR"

# Start Streamlit in background
echo "Starting $APP_NAME on port $PORT..."
nohup streamlit run "$APP_NAME" --server.port $PORT --server.headless true > streamlit.log 2>&1 &

echo "App started in background (PID: $!)"
echo "Access at: http://localhost:$PORT"
echo "Logs: $SCRIPT_DIR/streamlit.log"
