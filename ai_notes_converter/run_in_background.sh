#!/bin/bash
# Script to run the Note Converter app in the background on port 9998

# Check if streamlit is installed
if ! command -v streamlit &> /dev/null
then
    echo "Error: streamlit is not installed or not in PATH"
    echo "Install with: pip install streamlit"
    exit 1
fi

# Check if the app file exists
if [ ! -f "notes_converter.py" ]; then
    echo "Error: notes_converter.py not found in current directory"
    exit 1
fi

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "Warning: .env file not found. Make sure CLAUDE_API_KEY is set."
fi

# Kill any existing streamlit process on port 9998
echo "Checking for existing processes on port 9998..."
PID=$(lsof -ti:9998)
if [ ! -z "$PID" ]; then
    echo "Killing existing process on port 9998 (PID: $PID)"
    kill -9 $PID
    sleep 2
fi

# Start streamlit in the background
echo "Starting Note Converter on port 9998..."
nohup streamlit run notes_converter.py --server.port 9998 --server.headless true > streamlit.log 2>&1 &

# Get the PID
APP_PID=$!
echo $APP_PID > .streamlit.pid

echo "✓ Note Converter started successfully!"
echo "  PID: $APP_PID"
echo "  Port: 9998"
echo "  URL: http://localhost:9998"
echo "  Log file: streamlit.log"
echo ""
echo "To stop the app, run: kill $APP_PID"
echo "Or use: ./stop_app.sh"
