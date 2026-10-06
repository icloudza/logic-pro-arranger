#!/bin/sh
# External runtime; regular wheel install, no source-path binding.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
mode=${1:-core}
case "$mode" in core|models) ;; *) echo 'Usage: bootstrap.sh [core|models]' >&2; exit 1;; esac
runtime=${MUSIC_RUNTIME:-"$HOME/.cache/music-understanding/runtime-py311"}
python311=${MUSIC_BOOTSTRAP_PYTHON:-python3.11}
"$python311" -c 'import sys; assert sys.version_info[:2] == (3,11), "已验证模型环境为 Python 3.11"'
make all
"$python311" -m venv "$runtime"
extras=test
if [ "$mode" = models ]; then extras=beats,notes,perception,test; fi
if [ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ]; then
    "$runtime/bin/python" -m pip install -c requirements-macos-arm64-py311.lock ".[${extras}]"
else
    "$runtime/bin/python" -m pip install ".[${extras}]"
fi
"$runtime/bin/python" -m pip check
printf 'C CLI: %s/build/music\nPython: %s/bin/python\n' "$PWD" "$runtime"
if [ "$runtime" != "$HOME/.cache/music-understanding/runtime-py311" ]; then
    printf '使用自定义环境时设置 MUSIC_PYTHON=%s/bin/python\n' "$runtime"
fi
