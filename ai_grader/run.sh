#!/usr/bin/env bash
# Launch the AI Grader Streamlit app in the ai_grader conda env.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source /opt/anaconda3/etc/profile.d/conda.sh
conda activate ai_grader
cd "$HERE"
exec streamlit run app.py --server.port 3718 "$@"
