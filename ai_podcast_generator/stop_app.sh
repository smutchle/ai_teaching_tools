#!/bin/bash
# Script to stop the AI Podcast Generator running in the background

PORT=8544

cd "$(dirname "$0")" || exit 1

# Only listening sockets owned by us, and only if the process really is
# streamlit. A bare `lsof -ti:$PORT` also matches client connections to the
# port (e.g. VS Code remote port forwarding), which must never be killed.
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

if [ -f ".streamlit.pid" ]; then
    PID=$(cat .streamlit.pid)

    if ! ps -p "$PID" -o args= 2>/dev/null | grep -q "streamlit"; then
        # Stale pid file - the PID may have been recycled by an unrelated process
        echo "Note: PID $PID is not a streamlit process, ignoring stale .streamlit.pid"
        rm .streamlit.pid
        exit 0
    fi

    if ps -p $PID > /dev/null 2>&1; then
        echo "Stopping AI Podcast Generator (PID: $PID)..."
        kill $PID

        sleep 2
        if ps -p $PID > /dev/null 2>&1; then
            echo "Force stopping..."
            kill -9 $PID
        fi

        echo "✓ AI Podcast Generator stopped"
    else
        echo "Note: Process $PID is not running"
    fi

    rm .streamlit.pid
else
    PIDS=$(find_server_pids)
    if [ -n "$PIDS" ]; then
        echo "Found streamlit listening on port $PORT (PID: $PIDS)"
        echo "Stopping..."
        kill $PIDS 2>/dev/null
        for _ in 1 2 3 4 5; do
            sleep 1
            [ -z "$(find_server_pids)" ] && break
        done
        PIDS=$(find_server_pids)
        if [ -n "$PIDS" ]; then
            echo "Force stopping (PID: $PIDS)"
            kill -9 $PIDS 2>/dev/null
        fi
        echo "✓ Process stopped"
    else
        echo "No AI Podcast Generator process found running on port $PORT"
    fi
fi
