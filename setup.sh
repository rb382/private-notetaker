#!/usr/bin/env bash
# Private Notetaker - setup for macOS, Linux and ChromeOS (Linux environment).
#   ./setup.sh               install, and download the default speech model (small.en)
#   ./setup.sh --parakeet    also install NVIDIA Parakeet v3 (onnx-asr)
#   ./setup.sh --no-model    skip the model download (it then happens on first use)
set -euo pipefail
cd "$(dirname "$0")"

PARAKEET=0; MODEL=1
for arg in "$@"; do
  case "$arg" in
    --parakeet) PARAKEET=1 ;;
    --no-model) MODEL=0 ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

PY=""
for cand in python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,14) else 1)'; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10-3.13 is needed."
  echo "  macOS:  brew install python@3.12"
  echo "  Debian/Ubuntu/ChromeOS:  sudo apt install python3 python3-venv"
  exit 1
fi
echo "Using $($PY --version)"

if [ ! -d .venv ]; then
  if ! "$PY" -m venv .venv; then
    echo "Could not create a virtual environment. On Debian/Ubuntu/ChromeOS run: sudo apt install python3-venv"
    exit 1
  fi
fi
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt
if [ "$PARAKEET" = 1 ]; then
  .venv/bin/python -m pip install -r requirements-parakeet.txt
fi

if ! .venv/bin/python -c "import sounddevice" >/dev/null 2>&1; then
  echo
  echo "Note: the microphone library (PortAudio) is missing. File mode still works."
  echo "  Debian/Ubuntu/ChromeOS:  sudo apt install libportaudio2"
fi

if [ "$MODEL" = 1 ]; then
  echo "Downloading the speech model small.en (about 500 MB, once)..."
  .venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('small.en', device='cpu', compute_type='int8'); print('Speech model ready.')"
fi

chmod +x start.sh
echo
echo "Done. Start the app with ./start.sh"
echo "Or process a file:  .venv/bin/python -m notetaker.cli process path/to/meeting.wav"
