#!/bin/zsh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCH_DIR="$HOME/.openclass-launch"
LAUNCH_BIN_DIR="$HOME/.openclass-launch-bin"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
WEB_LABEL="com.openclass.web"
API_LABEL="com.openclass.api"
WEB_TEMPLATE="$PROJECT_DIR/launchd/$WEB_LABEL.plist"
API_TEMPLATE="$PROJECT_DIR/launchd/$API_LABEL.plist"
WEB_TARGET="$LAUNCH_AGENTS_DIR/$WEB_LABEL.plist"
API_TARGET="$LAUNCH_AGENTS_DIR/$API_LABEL.plist"
NODE_BIN_DIR="$(dirname "$(command -v npm)")"

mkdir -p "$LAUNCH_AGENTS_DIR"
mkdir -p "$LAUNCH_BIN_DIR"
ln -sfn "$PROJECT_DIR" "$LAUNCH_DIR"
cp "$PROJECT_DIR/scripts/keep-web-up.sh" "$LAUNCH_BIN_DIR/keep-web-up.sh"
chmod +x "$LAUNCH_BIN_DIR/keep-web-up.sh"

install_agent() {
  local label="$1"
  local template="$2"
  local target="$3"

  sed \
    -e "s#__PROJECT_DIR__#$PROJECT_DIR#g" \
    -e "s#__LAUNCH_BIN_DIR__#$LAUNCH_BIN_DIR#g" \
    -e "s#__NODE_BIN_DIR__#$NODE_BIN_DIR#g" \
    -e "s#__LAUNCH_DIR__#$LAUNCH_DIR#g" \
    "$template" > "$target"
  launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$target"
  launchctl enable "gui/$(id -u)/$label"
  launchctl kickstart -k "gui/$(id -u)/$label"
}

install_agent "$WEB_LABEL" "$WEB_TEMPLATE" "$WEB_TARGET"
install_agent "$API_LABEL" "$API_TEMPLATE" "$API_TARGET"
