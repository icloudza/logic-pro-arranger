"""One decoded coordinate system. Source files are read-only."""
import hashlib
import json
import shutil
import struct
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
from .tool_support import find_tool, normalized_audio, probe_audio

AUDIO_EXTENSIONS = {'.wav', '.flac', '.mp3', '.aif', '.aiff', '.m4a', '.ogg', '.opus', '.caf', '.mp4'}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def command(args):
    result = subprocess.run([str(x) for x in args], capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace')[-2000:])
    return result.stdout


def binary(name):
    found = find_tool(name)
    if not found:
        raise RuntimeError(f'需要 {name}，请先安装并加入 PATH')
    return found


def probe(path):
    raw = probe_audio(path)
    if not raw.get('streams'):
        raise ValueError('文件不含音频流')
    stream = raw['streams'][0]
    duration = stream.get('duration', raw.get('format', {}).get('duration'))
    mismatch = Path(path).suffix.lower() == '.wav' and raw['format'].get('format_name') != 'wav'
    return {'extension_mismatch': mismatch, 'codec': stream.get('codec_name'), 'format': raw['format'].get('format_name'),
            'sample_rate': int(stream['sample_rate']), 'channels': int(stream['channels']),
            'duration_s': float(duration) if duration is not None else None,
            'container_start_s': float(stream.get('start_time', 0)),
            'bits_per_sample': stream.get('bits_per_raw_sample', stream.get('bits_per_sample')),
            'embedded': wav_tags(path)}


def wav_tags(path):
    """smpl labels are metadata, not measured pitches; inspect header, not extension."""
    result = {}
    with Path(path).open('rb') as f:
        h = f.read(12)
        if len(h) != 12 or h[:4] != b'RIFF' or h[8:] != b'WAVE':
            return result
        size = Path(path).stat().st_size
        while f.tell() + 8 <= size:
            tag, n = struct.unpack('<4sI', f.read(8))
            at = f.tell()
            if at + n > size:
                break
            if tag == b'smpl' and n >= 36:
                vals = struct.unpack('<9I', f.read(36))
                result = {'root_midi': vals[3], 'pitch_fraction': vals[4], 'loop_count': vals[7],
                          'evidence': 'file_metadata_unverified'}
            f.seek(at + n + n % 2)
    return result


@normalized_audio
def decode(source, target):
    command([binary('ffmpeg'), '-nostdin', '-v', 'error', '-n', '-i', source,
             '-map', '0:a:0', '-map_metadata', '-1', '-c:a', 'pcm_f32le', target])
    info = sf.info(target)
    if info.frames == 0:
        raise ValueError('音频解码结果为空')
    return {'sample_rate': info.samplerate, 'channels': info.channels,
            'frames': info.frames, 'duration_s': info.frames / info.samplerate,
            'coordinate': 'decoded PCM sample 0; seconds = sample / sample_rate',
            'decoder': command([binary('ffmpeg'), '-version']).decode().splitlines()[0]}


def load(path, start=0, end=None, sr=None):
    info = sf.info(path)
    stop = info.frames if end is None else min(info.frames, round(end * info.samplerate))
    begin = max(0, round(start * info.samplerate))
    if begin >= stop:
        raise ValueError('时间范围为空或超出音频')
    x, rate = sf.read(path, start=begin, stop=stop, dtype='float32', always_2d=True)
    if not np.isfinite(x).all():
        raise ValueError('音频含非有限采样值')
    if sr and rate != sr:
        import librosa
        x = librosa.resample(x, orig_sr=rate, target_sr=sr, axis=0)
        rate = sr
    return x, rate, begin / info.samplerate


def mono(x):
    y = x.mean(axis=1)
    channel_energy = np.mean(x.astype('float64') ** 2, axis=0)
    # Opposite-polarity stereo must not turn into false silence.
    if np.mean(y.astype('float64') ** 2) < max(channel_energy.max(), 1e-20) * .05:
        return x[:, int(channel_energy.argmax())], 'highest_energy_channel_due_to_cancellation'
    return y, 'channel_mean'


def fresh_output(path, protected=()):
    path = Path(path).expanduser().resolve()
    if path.exists():
        raise ValueError(f'输出已存在，拒绝覆盖：{path}')
    for src in protected:
        p = Path(src).resolve()
        if path == p or (p.is_dir() and p in path.parents):
            raise ValueError('输出不可位于源素材目录内')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
