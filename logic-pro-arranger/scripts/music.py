#!/usr/bin/env python3
"""Thin skill entry point; the C CLI owns queries and dispatches model work."""
import os
import shutil
import sys
sys.dont_write_bytecode = True
from pathlib import Path


def main():
    override = os.environ.get('MUSIC_EXECUTABLE')
    adjacent = Path(__file__).resolve().parents[1] / 'tools/music-understanding/build/music'
    binary = override or (str(adjacent) if adjacent.is_file() else None)
    if not binary:
        print('缺少 music 核心：运行 skill/tools/music-understanding/scripts/bootstrap.sh core，或设置 MUSIC_EXECUTABLE。', file=sys.stderr)
        return 1
    os.execv(binary, [binary, *sys.argv[1:]])


if __name__ == '__main__':
    raise SystemExit(main())
