#!/usr/bin/env bash
set -euo pipefail

SCRIPT_SOURCE="${BASH_SOURCE[0]}"
SCRIPT_PATH=$(readlink -f "$SCRIPT_SOURCE")
SCRIPT_DIR=$(dirname "$SCRIPT_PATH")
PROXY_URL="http://localhost:4000"

usage() {
  echo "Usage: $0 [--model MODEL] [--api-key VT_API_KEY] [claude args...]"
  echo ""
  echo "Options:"
  echo "  --model MODEL       Model to use (default: \$VT_MODEL or Kimi-K2.5)"
  echo "  --api-key KEY       VT API key (default: \$VT_API_KEY from environment)"
  echo ""
  echo "Any additional arguments are passed to claude."
  exit 1
}

# Defaults
MODEL="${VT_MODEL:-Kimi-K2.5}"
API_KEY="${VT_API_KEY:-}"
MASTER_KEY="${LITELLM_MASTER_KEY:-}"
CLAUDE_ARGS=()

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)   MODEL="$2"; shift 2 ;;
    --api-key) API_KEY="$2"; shift 2 ;;
    --help|-h) usage ;;
    *)         CLAUDE_ARGS+=("$1"); shift ;;
  esac
done

if [[ -z "$API_KEY" ]]; then
  echo "Warning: VT_API_KEY not set. VT ARC models will not work; Ollama models (e.g. glm-4.7-flash) are unaffected."
fi

if [[ -z "$MASTER_KEY" ]]; then
  echo "Error: LITELLM_MASTER_KEY not set. Export LITELLM_MASTER_KEY in your environment."
  exit 1
fi

cd "$SCRIPT_DIR"

# Start proxy with the user's credentials
echo "Starting LiteLLM proxy..."
VT_API_KEY="$API_KEY" LITELLM_MASTER_KEY="$MASTER_KEY" docker compose up -d

echo "Waiting for proxy to be ready..."
for i in $(seq 1 30); do
  if curl -sf "$PROXY_URL/health" -H "Authorization: Bearer $MASTER_KEY" > /dev/null 2>&1; then
    echo "Proxy is ready."
    break
  fi
  if [[ "$i" -eq 30 ]]; then
    echo "Proxy did not become ready in time. Check: docker compose logs"
    exit 1
  fi
  sleep 1
done

export ANTHROPIC_BASE_URL="$PROXY_URL"
export ANTHROPIC_AUTH_TOKEN="$MASTER_KEY"

echo "Launching Claude Code with model: $MODEL"
claude --model "$MODEL" "${CLAUDE_ARGS[@]+"${CLAUDE_ARGS[@]}"}"
