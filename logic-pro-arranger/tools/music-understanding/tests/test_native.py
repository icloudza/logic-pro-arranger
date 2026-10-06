"""The C binary must agree with independent NumPy/Python observations."""
import hashlib
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from music_understanding.engine import analyze, summary
from music_understanding.store import Store

BINARY = Path(__file__).resolve().parents[1] / 'build/music'
SANITIZED = BINARY.with_name('music-sanitize')


def invoke(*args, binary=BINARY, ok=True):
    result = subprocess.run([str(binary), *map(str, args)], capture_output=True, text=True)
    if ok:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, result.stdout
    assert 'runtime error:' not in result.stderr and 'ERROR: AddressSanitizer' not in result.stderr
    return json.loads(result.stdout)


def same_json(a, b):
    # cJSON emits 15 or 17 significant digits; do not demand byte equality of floats.
    if isinstance(a, dict) and isinstance(b, dict):
        assert a.keys() == b.keys()
        for key in a: same_json(a[key], b[key])
    elif isinstance(a, list) and isinstance(b, list):
        assert len(a) == len(b)
        for x, y in zip(a, b): same_json(x, y)
    elif isinstance(a, (float, int)) and isinstance(b, (float, int)):
        assert a == pytest.approx(b, rel=1e-14, abs=1e-14)
    else: assert a == b


@pytest.mark.parametrize('midi', [24, 36, 48, 60, 72, 84])
def test_c_pitch_known_frequency(tmp_path, midi):
    sr = 22050; t = np.arange(sr) / sr
    y = (.3 * np.sin(2 * np.pi * (440 * 2 ** ((midi - 69) / 12)) * t)).astype('float32')
    path = tmp_path / 'signal.wav'; sf.write(path, y, sr, subtype='FLOAT')
    r = invoke('dsp', path, '--pitch', '--fmin', 30)
    assert abs(r['root']['midi'] - midi) < .05
    assert abs(r['rms_dbfs'] - 20 * np.log10(np.sqrt(np.mean(y.astype('float64') ** 2)))) < 1e-8
    assert invoke('hash', path)['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('kind', ['silence', 'noise', 'antiphase'])
def test_c_signal_abstention_and_stereo(tmp_path, kind):
    sr = 22050
    y = np.random.default_rng(73).normal(0, .15, sr) if kind == 'noise' else np.zeros(sr)
    if kind == 'antiphase':
        t = np.arange(sr) / sr; sine = .4 * np.sin(2 * np.pi * 440 * t)
        y = np.column_stack([sine, -sine])
    path = tmp_path / 'signal.wav'; sf.write(path, y, sr, subtype='FLOAT')
    r = invoke('dsp', path, '--pitch', binary=SANITIZED)
    if kind == 'antiphase':
        assert abs(r['root']['midi'] - 69) < .05
        assert 'cancellation' in r['mono_policy']
    else:
        assert r['root'] is None


def test_native_python_shared_catalog_and_conflicts(tmp_path):
    inputs = tmp_path / 'samples'; inputs.mkdir()
    path = inputs / '元数据.wav'
    sr = 22050; sf.write(path, .3 * np.sin(2 * np.pi * 440 * np.arange(sr) / sr), sr, subtype='FLOAT')
    raw = path.read_bytes(); chunk = b'smpl' + struct.pack('<I9I', 36, 0, 0, 0, 60, 0, 0, 0, 0, 0)
    raw = raw[:4] + struct.pack('<I', len(raw) + len(chunk) - 8) + raw[8:] + chunk; path.write_bytes(raw)
    home = tmp_path / 'cache'
    first = invoke('--home', home, 'inspect', path)
    assert first['metadata']['embedded']['root_midi'] == 60
    store = Store(home)
    try:
        result = analyze(store, first['id'], tasks=['envelope', 'pitch'])
        assert result['status'] == 'complete'
        asset = result['asset_id']; store.correct(asset, 'root_midi', 69, 'test')
        expected = summary(store, asset)
        assert 'conflicts' in expected
        same_json(invoke('--home', home, 'query', asset, 'summary'), expected)
        same_json(invoke('--home', home, 'query', asset, 'runs'), store.runs(asset))
        native = invoke('--home', home, 'query', asset, 'events', '--limit', 1)
        same_json(native['events'], store.events(asset, limit=1))
        assert native['next_offset'] == 1
        event = native['events'][0]
        same_json(invoke('--home', home, 'query', asset, 'evidence', '--id', event['id']), event)
        assert invoke('--home', home, 'search', '--text', '元数据')['matches'][0]['asset_id'] == asset
        assert path.read_bytes() == raw
    finally:
        store.close()


def test_cache_guard_resolves_symlinks_before_writing(tmp_path):
    inputs = tmp_path / 'samples'; inputs.mkdir()
    alias = tmp_path / 'alias'; alias.symlink_to(inputs, target_is_directory=True)
    r = invoke('--home', alias / 'cache', 'index', inputs, ok=False)
    assert '采样库外' in r['error']
    assert not (inputs / 'cache').exists()


def test_sanitized_malformed_file_and_numeric_bounds(tmp_path):
    path = tmp_path / 'bad.wav'; path.write_bytes(b'RIFF\xFF\xFF\xFF\xFFWAVEdata\xff\xff\xff\xff')
    invoke('dsp', path, '--pitch', binary=SANITIZED, ok=False)
    home = tmp_path / 'cache'
    for val in ['1e300', '-1', 'nan', '1.1']:
        invoke('--home', home, 'search', '--limit', val, binary=SANITIZED, ok=False)


def test_native_index_deduplicates_and_skips_hidden(tmp_path):
    source = tmp_path / 'samples'; source.mkdir()
    hidden = source / '.private'; hidden.mkdir()
    y = np.sin(2 * np.pi * 220 * np.arange(22050) / 22050) * .3
    first = source / 'one.wav'; sf.write(first, y, 22050)
    (source / 'two.wav').write_bytes(first.read_bytes())
    (hidden / 'three.wav').write_bytes(first.read_bytes())
    result = invoke('--home', tmp_path / 'db', 'index', source)
    assert result['files'] == 2 and result['unique_audio_assets'] == 1 and result['duplicate_paths'] == 1
    rejected = invoke('--home', source / 'db', 'inspect', first, ok=False)
    assert '采样库外' in rejected['error'] and not (source / 'db').exists()
    invoke('--home', tmp_path / 'db', 'query', '123', 'events', '--typo', 'bad', ok=False)
