#!/bin/bash
# Script to stop the Note Converter app running in the background

# Check if PID file exists
if [ -f ".streamlit.pid" ]; then
    PID=$(cat .streamlit.pid)

    # Check if process is still running
    if ps -p $PID > /dev/null 2>&1; then
        echo "Stopping Note Converter (PID: $PID)..."
        kill $PID

        # Wait a moment and force kill if still running
        sleep 2
        if ps -p $PID > /dev/null 2>&1; then
            echo "Force stopping..."
            kill -9 $PID
        fi

        echo "✓ Note Converter stopped"
    else
        echo "Note: Process $PID is not running"
    fi

    # Clean up PID file
    rm .streamlit.pid
else
    # Try to find and kill by port
    PID=$(lsof -ti:9998)
    if [ ! -z "$PID" ]; then
        echo "Found process on port 9998 (PID: $PID)"
        echo "Stopping..."
        kill -9 $PID
        echo "✓ Process stopped"
    else
        echo "No Note Converter process found running on port 9998"
    fi
fi
