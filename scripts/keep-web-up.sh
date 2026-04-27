#!/bin/zsh

set -u

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_PREFIX="[openclass-web]"

cd "$PROJECT_DIR"

export NEXT_TELEMETRY_DISABLED=1
export NODE_ENV=production

while true; do
  if [[ ! -f "$PROJECT_DIR/apps/web/.next/BUILD_ID" ]]; then
    echo "$LOG_PREFIX $(date '+%Y-%m-%d %H:%M:%S') missing production build; building first"
    npm run build:web
  fi

  echo "$LOG_PREFIX $(date '+%Y-%m-%d %H:%M:%S') starting Next.js production server on :3000"
  npm --prefix apps/web run start
  exit_code=$?
  echo "$LOG_PREFIX $(date '+%Y-%m-%d %H:%M:%S') server exited with code $exit_code; restarting in 5s"
  sleep 5
done
