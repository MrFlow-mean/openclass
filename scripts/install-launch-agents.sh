#!/bin/zsh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCH_DIR="$HOME/.openclass-launch"
LAUNCH_BIN_DIR="$HOME/.openclass-launch-bin"
WEB_RUNNER="$LAUNCH_BIN_DIR/keep-web-up.sh"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
WEB_LABEL="com.openclass.web"
API_LABEL="com.openclass.api"
WEB_TEMPLATE="$PROJECT_DIR/launchd/$WEB_LABEL.plist"
API_TEMPLATE="$PROJECT_DIR/launchd/$API_LABEL.plist"
WEB_TARGET="$LAUNCH_AGENTS_DIR/$WEB_LABEL.plist"
API_TARGET="$LAUNCH_AGENTS_DIR/$API_LABEL.plist"

mkdir -p "$LAUNCH_AGENTS_DIR"
mkdir -p "$LAUNCH_BIN_DIR"
ln -sfn "$PROJECT_DIR" "$LAUNCH_DIR"
cp "$PROJECT_DIR/scripts/keep-web-up.sh" "$WEB_RUNNER"
chmod +x "$WEB_RUNNER"

runtime_path() {
  local path_value="${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"
  local tool_path
  local tool_dir

  for tool_path in "$(command -v node 2>/dev/null || true)" "$(command -v npm 2>/dev/null || true)"; do
    [[ -n "$tool_path" ]] || continue
    tool_dir="${tool_path:h}"
    if [[ ":$path_value:" != *":$tool_dir:"* ]]; then
      path_value="$tool_dir:$path_value"
    fi
  done

  printf "%s" "$path_value"
}

RUNTIME_PATH="$(runtime_path)"

install_agent() {
  local label="$1"
  local template="$2"
  local target="$3"

  sed \
    -e "s#__PROJECT_DIR__#$PROJECT_DIR#g" \
    -e "s#__LAUNCH_DIR__#$LAUNCH_DIR#g" \
    -e "s#__WEB_RUNNER__#$WEB_RUNNER#g" \
    -e "s#__OPENCLASS_RUNTIME_PATH__#$RUNTIME_PATH#g" \
    "$template" > "$target"
  launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$target"
  launchctl enable "gui/$(id -u)/$label"
  launchctl kickstart -k "gui/$(id -u)/$label"
}

install_agent "$WEB_LABEL" "$WEB_TEMPLATE" "$WEB_TARGET"
install_agent "$API_LABEL" "$API_TEMPLATE" "$API_TARGET"
