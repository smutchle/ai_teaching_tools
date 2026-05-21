#!/usr/bin/env bash
# Foreground launcher for the ai_exam Streamlit observation UI.
# Port 8550 (other sibling apps: 7123, 7654, 8543, 8581, 9501, 9998).

set -e
cd "$(dirname "$0")"

CONDA_PATH="/opt/anaconda3"
if [ -f "$CONDA_PATH/etc/profile.d/conda.sh" ]; then
  . "$CONDA_PATH/etc/profile.d/conda.sh"
else
  echo "Error: conda.sh not found in $CONDA_PATH"
  exit 1
fi
conda activate genai || { echo "Error: failed to activate conda env 'genai'"; exit 1; }

echo "[app] Starting Streamlit on port 8550..."
exec streamlit run ui/streamlit_app.py --server.port 8550 --server.headless true
