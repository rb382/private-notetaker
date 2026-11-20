#!/usr/bin/env bash
# Starts Private Notetaker at http://127.0.0.1:8765 (add --no-browser to skip opening a browser).
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then echo "Run ./setup.sh first."; exit 1; fi
exec .venv/bin/python -m notetaker "$@"
