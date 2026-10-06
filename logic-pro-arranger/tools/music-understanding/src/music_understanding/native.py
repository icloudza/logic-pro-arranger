"""ctypes boundary: Python orchestration, C17 per-frame YIN computation."""
import ctypes
import os
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np


def library_path():
    suffix = 'dylib' if sys.platform == 'darwin' else 'so'
    return Path(os.environ.get('MUSIC_DSP_LIBRARY', str(Path(os.environ.get('MUSIC_ROOT', Path(__file__).resolve().parents[2])) / 'build' / ('libmusicdsp.' + suffix))))


@lru_cache(maxsize=1)
def kernel():
    from .models import BackendUnavailable
    path = library_path()
    if not path.is_file():
        raise BackendUnavailable('C DSP 未编译；在 music-understanding 执行 make，或设置 MUSIC_DSP_LIBRARY')
    lib = ctypes.CDLL(str(path))
    fn = lib.music_pitch_frame
    fn.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.c_double,
                   ctypes.c_double, ctypes.c_double, ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double)]
    fn.restype = ctypes.c_int
    return fn


def pitch(y, sr, origin, folder, fmin=32, fmax=2093):
    from .features import pitch_summary
    fn = kernel()
    if not 0 < fmin < fmax < sr / 2:
        raise ValueError('YIN-C 音高范围无效')
    hop = max(1, round(sr * .01))
    window = round(sr * .05) + int(sr / fmin)
    # Centered windows preserve original coordinates; boundary padding is explicit.
    padded = np.pad(np.asarray(y, dtype=np.float32), (window // 2, window // 2))
    n = (len(y) - 1) // hop + 1
    f0 = np.full(n, np.nan); score = np.zeros(n); rms = np.zeros(n)
    for i in range(n):
        frame = np.ascontiguousarray(padded[i * hop:i * hop + window])
        rms[i] = np.sqrt(np.mean(frame.astype('float64') ** 2))
        hz = ctypes.c_double(); ap = ctypes.c_double()
        ret = fn(frame.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(frame), sr, fmin, fmax,
                 ctypes.byref(hz), ctypes.byref(ap))
        if ret:
            raise ValueError('YIN-C 帧长或频率范围超出支持限制')
        f0[i] = hz.value; score[i] = max(0., 1 - ap.value)
    valid = (score > .85) & (rms > max(rms.max() * .05, 1e-5)) & np.isfinite(f0)
    return pitch_summary(f0, score, valid, sr / hop, origin, len(y) / sr, folder,
                         'yin-c17-v1', window / sr, fmin, fmax)
