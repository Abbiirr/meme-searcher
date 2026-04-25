#!/usr/bin/env bash
set -euo pipefail

cd /app/backend

bash start.sh &
webui_pid=$!

cleanup() {
  if kill -0 "$webui_pid" >/dev/null 2>&1; then
    kill "$webui_pid" >/dev/null 2>&1 || true
    wait "$webui_pid" || true
  fi
}

trap cleanup INT TERM

if ! python /app/vidsearch_open_webui/provision.py; then
  echo "Open WebUI provision step failed; continuing with the running server." >&2
fi

wait "$webui_pid"
