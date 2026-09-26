#!/usr/bin/env bash
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$REPO" || exit 1
# NOTE: the checkout path must not contain spaces -- RUN_CMD/PIP rely on word-splitting.
PY="${INKY_PYTHON:-$REPO/.venv/bin/python}"
PIP="${INKY_PIP:-$REPO/.venv/bin/pip}"
RUN_CMD="${INKY_RUN_CMD:-$PY -m kitchen_display}"
# Hash of the requirements.txt last installed *successfully*. Kept in the venv,
# so a rebuilt venv reinstalls too.
REQ_STAMP="${INKY_REQ_STAMP:-$REPO/.venv/.requirements-installed}"
REMOTE="${INKY_REMOTE:-origin}"; BRANCH="${INKY_BRANCH:-main}"
ver(){ git describe --tags --always --dirty 2>/dev/null || echo unknown; }
log(){ echo "$(date '+%F %T') update: $*"; }

# Boot readiness. A Pi Zero has no RTC and a systemd *user* unit can't wait for
# network-online.target, so wait (bounded) for NTP sync: that means the network
# is up for the fetch and the panel's date is right. Skipped where there's no
# timedatectl (a Mac); instant once synced (every restart after boot).
if command -v timedatectl >/dev/null 2>&1; then
  tries="${INKY_SYNC_TRIES:-45}"; poll="${INKY_SYNC_POLL_S:-2}"
  until [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ]; do
    tries=$((tries - 1))
    if [ "$tries" -le 0 ]; then log "clock not synced, continuing anyway"; break; fi
    sleep "$poll"
  done
fi

before=$(git rev-parse HEAD 2>/dev/null || echo none); before_ver=$(ver)
fetched=0
if git fetch --quiet --tags "$REMOTE" "$BRANCH" 2>/dev/null; then
  fetched=1
  git reset --hard --quiet FETCH_HEAD
else
  log "fetch failed (offline?), running current $before_ver"
fi
after=$(git rev-parse HEAD 2>/dev/null || echo none); after_ver=$(ver)
# Compare against what last installed, not against the pre-pull file: a pip
# failure must be retried on the next start, or the new code can never import.
req_now=$(git hash-object requirements.txt 2>/dev/null || echo none)
if [ "$req_now" != "$(cat "$REQ_STAMP" 2>/dev/null)" ]; then
  log "requirements.txt changed, reinstalling"
  if $PIP install -q -r requirements.txt; then
    mkdir -p "$(dirname "$REQ_STAMP")" && echo "$req_now" > "$REQ_STAMP"
  else
    log "pip install failed; will retry on next start"
  fi
fi
if [ "$fetched" = 1 ]; then
  if [ "$before" = "$after" ]; then
    log "weather $after_ver (up to date)"
  else
    log "weather $before_ver -> $after_ver (updated)"
  fi
fi
exec $RUN_CMD
