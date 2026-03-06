#!/usr/bin/env bash
# ── Course Creator — background mode: Neo4j + Streamlit ──────────────────────

# Load .env if present
if [ -f "$(dirname "$0")/.env" ]; then
  export $(grep -v '^#' "$(dirname "$0")/.env" | xargs) 2>/dev/null || true
fi

NEO4J_CONTAINER="${NEO4J_CONTAINER:-course_creator_neo4j}"
NEO4J_HTTP_PORT="${NEO4J_HTTP_PORT:-7475}"
NEO4J_BOLT_PORT="${NEO4J_BOLT_PORT:-7688}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-course_creator}"

# ── Start dedicated Neo4j instance ───────────────────────────────────────────
if docker ps --format '{{.Names}}' | grep -q "^${NEO4J_CONTAINER}$"; then
  echo "[neo4j] Container '${NEO4J_CONTAINER}' already running."
else
  if docker ps -a --format '{{.Names}}' | grep -q "^${NEO4J_CONTAINER}$"; then
    echo "[neo4j] Restarting existing container '${NEO4J_CONTAINER}'..."
    docker start "${NEO4J_CONTAINER}"
  else
    echo "[neo4j] Creating new container '${NEO4J_CONTAINER}'..."
    docker run -d \
      --name "${NEO4J_CONTAINER}" \
      -p "${NEO4J_HTTP_PORT}:7474" \
      -p "${NEO4J_BOLT_PORT}:7687" \
      -e NEO4J_AUTH="neo4j/${NEO4J_PASSWORD}" \
      -e NEO4J_PLUGINS='["apoc"]' \
      -e NEO4J_dbms_memory_heap_initial__size=512m \
      -e NEO4J_dbms_memory_heap_max__size=1G \
      neo4j:5-community
  fi
  echo "[neo4j] Waiting for Neo4j to be ready..."
  for i in $(seq 1 30); do
    if docker exec "${NEO4J_CONTAINER}" cypher-shell -u neo4j -p "${NEO4J_PASSWORD}" "RETURN 1;" > /dev/null 2>&1; then
      echo "[neo4j] Ready."
      break
    fi
    sleep 2
  done
fi

# ── (Re)start Streamlit ───────────────────────────────────────────────────────
pkill -f "streamlit run app_course.py" 2>/dev/null || true
sleep 1
echo "[app] Starting Streamlit on port 9501 (background)..."
nohup streamlit run app_course.py --server.port=9501 >> nohup.out 2>&1 &
echo "[app] PID $!"
