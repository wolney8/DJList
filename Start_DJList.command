#!/bin/bash
# Finder launcher for the local DJList application.
set -u
DJLIST_FOLDER="$(cd -- "$(dirname -- "$0")" && pwd)"
cd -- "$DJLIST_FOLDER" || exit 1

DJLIST_PYTHON=""
for DJLIST_CANDIDATE in \
  "$DJLIST_FOLDER/.venv/bin/python3" \
  /opt/homebrew/bin/python3 \
  /usr/local/bin/python3 \
  "$HOME/Downloads/pyrekordbox-env/bin/python3" \
  "$(command -v python3 2>/dev/null || true)"; do
  if [ -n "$DJLIST_CANDIDATE" ] && [ -x "$DJLIST_CANDIDATE" ] && \
     "$DJLIST_CANDIDATE" -c 'import sys; raise SystemExit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
    DJLIST_PYTHON="$DJLIST_CANDIDATE"
    break
  fi
done

if [ -z "$DJLIST_PYTHON" ]; then
  printf '%s\n' 'DJList needs Python 3.9 or newer.' \
    'Install Python from https://www.python.org/downloads/macos/ then open this launcher again.'
  read -r -p 'Press Return to close. ' DJLIST_REPLY
  exit 1
fi

"$DJLIST_PYTHON" "$DJLIST_FOLDER/app.py"
DJLIST_EXIT=$?
if [ "$DJLIST_EXIT" -ne 0 ]; then
  printf '\n%s\n' 'This launcher did not start DJList. Read the message above before trying again.'
  read -r -p 'Press Return to close. ' DJLIST_REPLY
fi
exit "$DJLIST_EXIT"
