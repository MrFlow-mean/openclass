#!/bin/zsh

set -u

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_PREFIX="[openclass-api]"

cd "$PROJECT_DIR"

while true; do
  echo "$LOG_PREFIX $(date '+%Y-%m-%d %H:%M:%S') starting FastAPI server on :8000"
  .venv/bin/python -m uvicorn app.main:app --reload --app-dir apps/api
  exit_code=$?
  echo "$LOG_PREFIX $(date '+%Y-%m-%d %H:%M:%S') server exited with code $exit_code; restarting in 5s"
  sleep 5
done
