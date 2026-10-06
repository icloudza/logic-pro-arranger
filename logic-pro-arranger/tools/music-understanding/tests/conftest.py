"""Test this source tree, not an installed copy; workers started by the C CLI inherit the same path."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / 'src')
sys.path.insert(0, SRC)
os.environ['PYTHONPATH'] = os.pathsep.join(filter(None, [SRC, os.environ.get('PYTHONPATH')]))
os.environ.setdefault('MUSIC_ROOT', str(ROOT))
