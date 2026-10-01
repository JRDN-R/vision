#!/bin/bash
set -e
cd "$(dirname "$0")/.."
trap 'echo "Vision could not start. See vision-youtube/README.md. Install Python 3.12 and Deno first."; read -r -p "Press Return to close."' ERR
if [ ! -x .venv/bin/python ]; then
  python3.12 -m venv .venv
fi
if [ ! -f .venv/vision-helper-ready ]; then
  .venv/bin/python -m pip install -r vision-youtube/requirements.txt
  touch .venv/vision-helper-ready
fi
.venv/bin/python vision-youtube/server.py --open "$@"
