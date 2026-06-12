#!/bin/zsh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
LAUNCHER_FILE="$PROJECT_DIR/launcher/ai-board-launcher.html"

has_listener() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

chmod +x "$PROJECT_DIR/scripts/keep-web-up.sh" "$PROJECT_DIR/scripts/keep-api-up.sh" \
  "$PROJECT_DIR/scripts/install-launch-agents.sh" "$PROJECT_DIR/scripts/build-web-if-stale.sh"

"$PROJECT_DIR/scripts/build-web-if-stale.sh" "$PROJECT_DIR"

"$PROJECT_DIR/scripts/install-launch-agents.sh"

if ! has_listener 3000 || ! has_listener 8000; then
  sleep 2
fi

open "$LAUNCHER_FILE"
