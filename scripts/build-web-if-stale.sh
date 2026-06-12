#!/bin/zsh

set -euo pipefail

PROJECT_DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
BUILD_DIR="$PROJECT_DIR/apps/web/.next"
BUILD_ID="$BUILD_DIR/BUILD_ID"
SOURCE_STAMP="$BUILD_DIR/openclass-source-stamp"
LOG_PREFIX="[openclass-web]"

cd "$PROJECT_DIR"

current_stamp="$(git rev-parse HEAD 2>/dev/null || printf 'nogit')"
needs_build=false
reason=""

if [[ ! -f "$BUILD_ID" ]]; then
  needs_build=true
  reason="missing production build"
elif [[ ! -f "$SOURCE_STAMP" ]] || [[ "$(cat "$SOURCE_STAMP" 2>/dev/null || true)" != "$current_stamp" ]]; then
  needs_build=true
  reason="source revision changed"
else
  source_paths=(
    "$PROJECT_DIR/apps/web/src"
    "$PROJECT_DIR/apps/web/public"
    "$PROJECT_DIR/apps/web/package.json"
    "$PROJECT_DIR/apps/web/next.config.js"
    "$PROJECT_DIR/apps/web/next.config.mjs"
    "$PROJECT_DIR/apps/web/next.config.ts"
    "$PROJECT_DIR/apps/web/postcss.config.js"
    "$PROJECT_DIR/apps/web/postcss.config.mjs"
    "$PROJECT_DIR/apps/web/tsconfig.json"
    "$PROJECT_DIR/package.json"
  )
  existing_paths=()
  for source_path in "${source_paths[@]}"; do
    [[ -e "$source_path" ]] && existing_paths+=("$source_path")
  done

  changed_file=""
  if (( ${#existing_paths[@]} > 0 )); then
    changed_file="$(find "${existing_paths[@]}" -type f -newer "$BUILD_ID" -print -quit)"
  fi
  if [[ -n "$changed_file" ]]; then
    needs_build=true
    reason="source file changed: ${changed_file#$PROJECT_DIR/}"
  fi
fi

if [[ "$needs_build" == true ]]; then
  echo "$LOG_PREFIX $(date '+%Y-%m-%d %H:%M:%S') $reason; rebuilding web"
  rm -rf "$BUILD_DIR"
  npm run build:web
  mkdir -p "$BUILD_DIR"
  printf '%s\n' "$current_stamp" > "$SOURCE_STAMP"
else
  echo "$LOG_PREFIX $(date '+%Y-%m-%d %H:%M:%S') production build is current"
fi
