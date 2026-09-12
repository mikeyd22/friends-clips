#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
python_bin="${PYTHON_BIN:-python3}"

"$python_bin" -m pip install -r "$repo_root/requirements.txt"

if ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y ffmpeg
  elif command -v brew >/dev/null 2>&1; then
    brew install ffmpeg
  else
    echo "ffmpeg is required but no supported package manager was found" >&2
    exit 1
  fi
fi

if command -v apt-get >/dev/null 2>&1; then
  apt-get install -y fonts-noto-cjk >/dev/null 2>&1 || true
fi

chmod +x "$repo_root/scripts/transcribe_compat.py"
echo "Friends Clips dependencies are ready."
