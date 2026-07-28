#!/bin/bash
# Script to run the AI Podcast Generator in the background on port 8544
#
# Unlike the sibling apps this one needs its own conda env (podcastify), because
# Kokoro requires Python 3.11+ and numpy<2.

PORT=8544
APP="podcast_app.py"
ENV_NAME="podcastify"

cd "$(dirname "$0")" || exit 1

# Activate the dedicated env
export PATH="/opt/anaconda3/bin:$PATH"
eval "$(/opt/anaconda3/bin/conda shell.bash hook)"
conda activate "$ENV_NAME" 2>/dev/null

if [ "$CONDA_DEFAULT_ENV" != "$ENV_NAME" ]; then
    echo "Error: could not activate conda env '$ENV_NAME'"
    echo "Create it with:"
    echo "  conda create -y -n $ENV_NAME python=3.11"
    echo "  conda activate $ENV_NAME"
    echo "  pip install torch --index-url https://download.pytorch.org/whl/cu124"
    echo "  pip install -r requirements.txt"
    exit 1
fi

if ! command -v streamlit &> /dev/null; then
    echo "Error: streamlit is not installed in the $ENV_NAME env"
    echo "Install with: pip install -r requirements.txt"
    exit 1
fi

if ! command -v ffmpeg &> /dev/null; then
    echo "Error: ffmpeg not found on PATH - required to encode the MP3"
    exit 1
fi

if [ ! -f "$APP" ]; then
    echo "Error: $APP not found in $(pwd)"
    exit 1
fi

if [ ! -f ".env" ]; then
    echo "Warning: .env not found. OPEN_AI_ENDPOINT and OPEN_AI_API_KEY are required."
fi

# Find our own streamlit server on the port.
#
# Plain `lsof -ti:$PORT` also matches *client* sockets connected to the port -
# notably the VS Code remote server's port forwarding - so killing that list
# takes down the SSH session too. Restrict to listening sockets owned by us,
# then confirm the command line really is streamlit before signalling it.
#
# The -a is essential: lsof ORs its selection options by default, so without it
# `-u <uid>` matches every process you own rather than narrowing the port match.
find_server_pids() {
    local pid
    for pid in $(lsof -ti -a -sTCP:LISTEN -u "$(id -u)" -i:$PORT 2>/dev/null); do
        if ps -p "$pid" -o args= 2>/dev/null | grep -q "streamlit"; then
            echo "$pid"
        fi
    done
}

echo "Checking for existing processes on port $PORT..."
PIDS=$(find_server_pids)
if [ -n "$PIDS" ]; then
    echo "Stopping existing streamlit on port $PORT (PID: $PIDS)"
    kill $PIDS 2>/dev/null
    for _ in 1 2 3 4 5; do
        sleep 1
        [ -z "$(find_server_pids)" ] && break
    done
    PIDS=$(find_server_pids)
    if [ -n "$PIDS" ]; then
        echo "Force stopping (PID: $PIDS)"
        kill -9 $PIDS 2>/dev/null
        sleep 1
    fi
fi

echo "Starting AI Podcast Generator on port $PORT..."
nohup streamlit run "$APP" --server.port $PORT --server.headless true > streamlit.log 2>&1 &

APP_PID=$!
echo $APP_PID > .streamlit.pid

echo "✓ AI Podcast Generator started successfully!"
echo "  PID: $APP_PID"
echo "  Port: $PORT"
echo "  URL: http://localhost:$PORT"
echo "  Env: $ENV_NAME"
echo "  Log file: streamlit.log"
echo ""
echo "To stop the app, run: ./stop_app.sh"
