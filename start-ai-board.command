#!/bin/zsh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
LAUNCHER_FILE="$PROJECT_DIR/launcher/personal-home.html"

has_listener() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

start_screen() {
  local name="$1"
  local command="$2"

  screen -S "$name" -X quit >/dev/null 2>&1 || true
  screen -dmS "$name" zsh -lc "$command"
}

screen -wipe >/dev/null 2>&1 || true

if ! has_listener 8000; then
  start_screen "openclass-api" "cd '$PROJECT_DIR' && ./scripts/keep-api-up.sh > /tmp/openclass-api.log 2>&1"
fi

if ! has_listener 3000; then
  if [[ ! -f "$PROJECT_DIR/apps/web/.next/BUILD_ID" ]]; then
    npm run build:web
  fi
  start_screen "openclass-web" "cd '$PROJECT_DIR' && ./scripts/keep-web-up.sh > /tmp/openclass-web.log 2>&1"
fi

open "$LAUNCHER_FILE"
