#!/usr/bin/env bash
# macOS/Linux launcher. Reuses an already running llama-server (8080, then 8090).
set -euo pipefail
cd "$(dirname "$0")"

port=""
for p in ${LLAMA_PORT:-8080 8090}; do
  if curl -fsS --max-time 5 "http://127.0.0.1:$p/v1/models" >/dev/null 2>&1; then port=$p; break; fi
done
if [ -z "$port" ]; then
  echo "No llama-server answered on 127.0.0.1:8080 or :8090 (/v1/models). Start it first." >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
fi

export LLM_BASE_URL="http://127.0.0.1:$port/v1"
echo "Using $LLM_BASE_URL — open http://127.0.0.1:${APP_PORT:-8502}"
exec .venv/bin/python -m streamlit run app.py --server.address 127.0.0.1 \
  --server.port "${APP_PORT:-8502}" --browser.gatherUsageStats false
