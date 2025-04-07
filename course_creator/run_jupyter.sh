#!/bin/bash

# Path to conda installation - modify if needed
CONDA_PATH="$HOME/anaconda3"

# Source conda.sh to initialize conda in the shell
if [ -f "$CONDA_PATH/etc/profile.d/conda.sh" ]; then
    . "$CONDA_PATH/etc/profile.d/conda.sh"
else
    echo "Error: conda.sh not found in $CONDA_PATH"
    exit 1
fi

# Environment name (change 'base' to your environment name if needed)
ENVIRONMENT="genai"

# Jupyter settings
PORT=9090
NOTEBOOK_DIR="$HOME/course"  # Change this to your preferred directory

# Create notebook directory if it doesn't exist
mkdir -p "$NOTEBOOK_DIR"

# Activate conda environment
conda activate $ENVIRONMENT

# Check if activation was successful
if [ $? -ne 0 ]; then
    echo "Error: Failed to activate conda environment '$ENVIRONMENT'"
    exit 1
fi

# Launch Jupyter notebook
# --no-browser: Don't open a browser window
# --ip=0.0.0.0: Allow external connections
# --port: Specify port number
# --notebook-dir: Working directory for notebooks
echo "Starting Jupyter notebook in directory: $NOTEBOOK_DIR"
jupyter notebook --no-browser \
                --ip=0.0.0.0 \
                --port=$PORT \
                --notebook-dir="$NOTEBOOK_DIR" \
                --NotebookApp.token='' \
                --NotebookApp.password='' \
                >> "$HOME/jupyter.log" 2>&1 &

# Store the process ID
echo $! > "$HOME/jupyter.pid"

echo "Jupyter notebook started on port $PORT"
echo "PID stored in $HOME/jupyter.pid"
echo "Logs available in $HOME/jupyter.log"
