#!/bin/bash
# Regenerate the frontend API client from a locally served OpenAPI spec.
# Usage: bash scripts/dev_codegen.sh (from the repo root, venv + nvm assumed)
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
export PATH="$HOME/.nvm/versions/node/v24.18.0/bin:$PATH"
# openapi-ts pulls the spec from localhost:8000 (see frontend config).
BONDLENS_SKIP_DB=1 python -m uvicorn backend.app:app --port 8000 &>/tmp/codegen_uvicorn.log &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT
for _ in $(seq 30); do
    curl -sf http://127.0.0.1:8000/openapi.json -o /dev/null && break
    sleep 0.5
done
cd frontend
npm run codegen
