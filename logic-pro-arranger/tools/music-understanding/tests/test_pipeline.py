import json
import subprocess
import sys

import numpy as np
import pytest
import soundfile as sf

from music_understanding.audio import digest, mono
from music_understanding.engine import analyze, summary
from music_understanding.store import Store
from music_understanding.views import audio, export


@pytest.fixture
def lab(tmp_path):
    inputs = tmp_path / 'inputs'; inputs.mkdir()
    store = Store(tmp_path / 'cache')
    def write(name, x, sr=22050):
        p = inputs / name
        sf.write(p, x, sr, subtype='FLOAT')
        return p
    yield store, write, tmp_path
    store.close()


def tone(hz=261.625565, seconds=1):
    t = np.arange(round(seconds * 22050)) / 22050
    return (.4 * np.sin(2 * np.pi * hz * t) * np.minimum(t * 30, 1)).astype('float32')


def test_pitch_and_origin(lab):
    store, write, _ = lab
    src = write('tone.wav', np.r_[np.zeros(11025), tone(seconds=1.5)])
    result = analyze(store, str(src), tasks=['pitch'], start=.5, end=2)
    assert result['status'] == 'complete', result
    root = result['tasks'][0]['summary']['root']
    assert abs(root['midi'] - 60) < .15
    events = store.events(result['asset_id'], kind='note')
    assert events and min(e['t0'] for e in events) >= .5
    assert max(e['t1'] for e in events) <= 2
    assert events[0]['probability'] is None


def test_silence_abstains(lab):
    store, write, _ = lab
    result = analyze(store, str(write('silence.wav', np.zeros(22050))), tasks=['pitch', 'rhythm', 'chords'])
    assert result['status'] == 'complete'
    assert result['tasks'][0]['summary']['root'] is None
    assert store.events(result['asset_id'], kind='note') == []
    assert store.events(result['asset_id'], kind='beat') == []


def test_duplicate_cache_changes_and_source_preservation(lab):
    store, write, root = lab
    p = write('a.wav', tone()); before = digest(p)
    a = analyze(store, str(p), tasks=['envelope'])
    q = root / 'inputs' / 'copy.wav'; q.write_bytes(p.read_bytes())
    b = analyze(store, str(q), tasks=['envelope'])
    assert a['asset_id'] == b['asset_id']
    assert b['tasks'][0]['cached'] is True
    c = analyze(store, str(p), tasks=['envelope'], start=.2)
    assert c['tasks'][0]['run_id'] != a['tasks'][0]['run_id']
    assert digest(p) == before
    assert len(store.get(a['asset_id'])['paths']) == 2


def test_real_codec_not_extension(lab):
    store, write, root = lab
    src = write('a.wav', tone())
    weird = root / 'inputs' / 'flac_named.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(src), '-f', 'flac', str(weird)], check=True)
    asset = store.register(weird)
    assert asset['metadata']['codec'] == 'flac'
    assert sf.info(asset['audio']).frames == 22050


def test_stereo_cancellation():
    y = tone()
    selected, policy = mono(np.column_stack([y, -y]))
    assert np.max(np.abs(selected)) > .3
    assert 'cancellation' in policy


def test_crop_exact_and_no_overwrite(lab):
    store, write, root = lab
    src = write('a.wav', tone(seconds=2))
    asset = store.register(src)
    out = root / 'exports' / 'crop.wav'
    audio(store, asset['id'], out, .125, .875)
    expected, _ = sf.read(src, start=round(.125 * 22050), stop=round(.875 * 22050))
    actual, _ = sf.read(out)
    np.testing.assert_array_equal(actual, expected)
    with pytest.raises(ValueError): audio(store, asset['id'], out)
    with pytest.raises(ValueError): audio(store, asset['id'], src)


def test_missing_backend_is_explicit(lab):
    store, write, _ = lab
    result = analyze(store, str(write('a.wav', tone())), tasks=['envelope', 'lyrics'], options={'speech_backend':'whisper'})
    assert result['status'] == 'partial'
    assert result['tasks'][-1]['status'] == 'unavailable'
    assert result['tasks'][0]['status'] == 'complete'


def test_correction_keeps_evidence(lab):
    store, write, _ = lab
    result = analyze(store, str(write('a.wav', tone())), tasks=['pitch'])
    asset = result['asset_id']; ids = [e['id'] for e in store.events(asset)]
    store.correct(asset, 'root_midi', 72, 'test explicit manual correction')
    report = summary(store, asset)
    assert report['corrections'][0]['value'] == 72
    assert report['tasks']['pitch']['summary']['root']['nearest_midi'] == 60
    assert [e['id'] for e in store.events(asset)] == ids


def test_portable_export(lab):
    store, write, root = lab
    result = analyze(store, str(write('a.wav', tone())), tasks=['envelope', 'pitch'])
    out = root / 'portable'
    export(store, result['asset_id'], out)
    report = json.loads((out / 'summary.json').read_text())
    contour = report['tasks']['pitch']['summary']['contour']['path']
    assert not contour.startswith('/') and (out / contour).is_file()
    assert (out / 'source.wav').is_file()
    assert json.loads((out / 'manifest.json').read_text())['asset_id'] == result['asset_id']


def test_event_pagination_and_evidence_scope(lab):
    store, write, root = lab
    result = analyze(store, str(write('a.wav', tone())), tasks=['pitch', 'envelope'])
    asset = result['asset_id']
    cmd = [sys.executable, '-m', 'music_understanding', '--home', str(store.root), 'query', asset, 'events', '--limit', '1']
    first = json.loads(subprocess.check_output(cmd, text=True))
    assert len(first['events']) == 1 and first['next_offset'] == 1
    second = json.loads(subprocess.check_output(cmd + ['--offset', '1'], text=True))
    assert first['events'][0]['id'] != second['events'][0]['id']


def test_missing_cached_array_recomputes(lab):
    from pathlib import Path
    store, write, _ = lab
    source = write('tone.wav', tone())
    a = analyze(store, str(source), tasks=['pitch'])
    ref = a['tasks'][0]['summary']['contour']['path']; Path(ref).unlink()
    b = analyze(store, str(source), tasks=['pitch'])
    assert not b['tasks'][0].get('cached') and Path(ref).exists()


def test_python_cache_guard_before_creation(lab):
    store, write, _ = lab
    source = write('tone.wav', tone()); cache = source.parent / 'danger'
    result = subprocess.run([sys.executable, '-m', 'music_understanding', '--home', str(cache),
                             'inspect', str(source)], capture_output=True, text=True)
    assert result.returncode == 1 and '采样库外' in result.stdout
    assert not cache.exists()
