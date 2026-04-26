#!/bin/zsh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
LAUNCHER_FILE="$PROJECT_DIR/launcher/personal-home.html"

has_listener() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

run_in_terminal() {
  local command="$1"
  osascript <<EOF
tell application "Terminal"
  activate
  do script "$command"
end tell
EOF
}

if ! has_listener 3000 && ! has_listener 8000; then
  run_in_terminal "cd '$PROJECT_DIR' && npm run dev"
elif ! has_listener 3000; then
  run_in_terminal "cd '$PROJECT_DIR/apps/web' && npm run dev"
elif ! has_listener 8000; then
  run_in_terminal "cd '$PROJECT_DIR/apps/api' && ../../.venv/bin/uvicorn app.main:app --reload"
fi

open "$LAUNCHER_FILE"
