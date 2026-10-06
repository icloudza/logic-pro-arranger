import sys
import subprocess

import numpy as np
import pytest
import soundfile as sf

from music_understanding import apple
from music_understanding.engine import analyze, plan
from music_understanding.models import BackendUnavailable
from music_understanding.store import Store


def test_non_macos_does_not_launch_apple(monkeypatch):
    monkeypatch.setattr(apple.sys, 'platform', 'linux')
    def forbidden(*a, **k): raise AssertionError('Apple process launched on non-macOS')
    monkeypatch.setattr(apple.subprocess, 'run', forbidden)
    assert apple.capabilities()['reason'] == 'not_macos'
    assert not apple.available()
    with pytest.raises(BackendUnavailable, match='仅支持 macOS'): apple.call('sounds', '/no/file')
    selected = plan({'metadata': {'duration_s': 3}, 'paths': ['sample.wav']})
    assert 'sounds' not in selected['tasks']


def test_non_macos_explicit_sounds_reports_unavailable(tmp_path, monkeypatch):
    source = tmp_path / 'inputs'; source.mkdir()
    f = source / 'tone.wav'; sf.write(f, np.sin(2*np.pi*440*np.arange(22050)/22050)*.2, 22050)
    monkeypatch.setattr(apple.sys, 'platform', 'linux')
    store = Store(tmp_path / 'cache')
    try:
        result = analyze(store, str(f), tasks=['sounds'])
        assert result['status'] == 'partial' and result['tasks'][0]['status'] == 'unavailable'
    finally: store.close()


@pytest.mark.skipif(not apple.available(), reason='macOS helper not available')
def test_actual_soundanalysis_original_coordinates(tmp_path):
    f = tmp_path / 'signal.wav'
    y = .3 * np.sin(2*np.pi*261.625565*np.arange(22050*4)/22050)
    sf.write(f,y,22050)
    report, events = apple.sound_events(f, 5., 4.)
    assert report['backend'] == 'apple-soundanalysis' and report['label_count'] > 1
    assert events and all(5 <= e['t0'] < e['t1'] <= 9 for e in events)
    assert all(e['probability'] is None and len(e['value']['candidates']) <= 5 for e in events)
    assert all(0 <= c['score'] <= 1 for e in events for c in e['value']['candidates'])


def test_non_macos_coreml_is_explicitly_unavailable(monkeypatch):
    from music_understanding import models
    monkeypatch.setattr(models.sys, 'platform', 'linux')
    with pytest.raises(BackendUnavailable, match='Core ML'):
        models.basic_pitch('/unused', 0, 1, '/unused', backend='coreml')
