"""Audio observations and musical hypotheses; no auditory quality score."""
__version__ = "0.2.0"

import os
import sys
from pathlib import Path
sys.dont_write_bytecode = True
os.environ.setdefault('NUMBA_CACHE_DIR', str(Path.home() / '.cache/music-understanding/numba'))
