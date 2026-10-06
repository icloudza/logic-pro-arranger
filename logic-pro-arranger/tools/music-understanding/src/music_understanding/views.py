"""Original audio, evidence overlays and portable exports."""
import json
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf

from .audio import fresh_output, load, mono
from .store import dumps


def audio(store, asset_id, out, start=0, end=None, overlay=None):
    asset = store.ensure_audio(asset_id)
    out = fresh_output(out, [Path(p).parent for p in asset['paths']])
    if out.suffix.lower() != '.wav':
        raise ValueError('试听输出使用 .wav')
    x, sr, origin = load(asset['audio'], start, end)
    if overlay:
        events = store.events(asset_id, origin, origin + len(x) / sr, kind='beat' if overlay == 'beats' else 'note', limit=100000)
        synth = np.zeros(len(x))
        for e in events:
            a = max(0, round((e['t0'] - origin) * sr)); b = min(len(x), round((e['t1'] - origin) * sr))
            if overlay == 'beats':
                b = min(len(x), a + round(sr * .035))
                hz = 1500 if e['value'].get('downbeat') else 1000
            else:
                hz = 440 * 2 ** ((e['value']['midi'] - 69) / 12)
            if b <= a:
                continue
            phase = np.arange(b - a) / sr
            env = np.ones(b - a)
            ramp = min(round(.008 * sr), len(env) // 2)
            if ramp:
                env[:ramp] = np.linspace(0, 1, ramp); env[-ramp:] = np.linspace(1, 0, ramp)
            synth[a:b] += np.sin(phase * hz * 2 * np.pi) * env * .12
        x = x * .65 + synth[:, None]
    # FLOAT preserves peaks; no hidden normalization or clipping.
    sf.write(out, x, sr, subtype='FLOAT')
    return {'path': str(out), 'source_asset': asset_id, 'origin_s': origin, 'duration_s': len(x) / sr,
            'overlay': overlay, 'source_gain': .65 if overlay else 1.0}


def render(store, asset_id, out, start=0, end=None, layers=('f0', 'beats')):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    asset = store.ensure_audio(asset_id)
    out = fresh_output(out, [Path(p).parent for p in asset['paths']])
    x, sr, origin = load(asset['audio'], start, end)
    y, policy = mono(x)
    fig, axes = plt.subplots(3, 1, figsize=(12, 7), sharex=True, constrained_layout=True)
    stride = max(1, len(y) // 50000)
    axes[0].plot(np.arange(0, len(y), stride) / sr + origin, y[::stride], lw=.5)
    axes[0].set_ylabel('Amplitude')
    _, _, ts, im = axes[1].specgram(y, NFFT=min(1024, max(64, len(y) // 2)), Fs=sr,
                                   noverlap=min(512, max(0, len(y) // 4)), cmap='magma')
    extent = im.get_extent(); im.set_extent((extent[0] + origin, extent[1] + origin, extent[2], extent[3]))
    axes[1].set_ylim(0, min(sr / 2, 6000)); axes[1].set_ylabel('Hz')
    runs = store.latest(asset_id)
    if 'f0' in layers and runs.get('pitch', {}).get('status') == 'complete':
        ref = runs['pitch']['summary'].get('contour')
        if ref:
            data = np.load(ref['path'], allow_pickle=False)
            t = np.arange(len(data)) / ref['frame_rate'] + ref['origin_s']
            valid = (data[:, 2] > 0) & (t >= origin) & (t <= origin + len(y) / sr)
            axes[2].plot(t[valid], 69 + 12 * np.log2(data[valid, 0] / 440), '.', ms=2)
    events = store.events(asset_id, origin, origin + len(y) / sr, limit=100000)
    for e in events:
        if e['type'] == 'beat' and 'beats' in layers:
            for ax in axes:
                ax.axvline(e['t0'], color='tab:blue', alpha=.22, lw=.6)
        if e['type'] == 'note' and 'notes' in layers:
            axes[2].plot([max(origin, e['t0']), min(origin + len(y) / sr, e['t1'])],
                         [e['value']['midi']] * 2, color='tab:orange', lw=2)
    axes[2].set_ylabel('MIDI pitch'); axes[2].set_xlabel('Original decoded audio time (s)')
    axes[2].set_xlim(origin, origin + len(y) / sr)
    fig.suptitle('Measurements / model candidates (not auditory verification)')
    fig.savefig(out, dpi=140); plt.close(fig)
    return {'path': str(out), 'source_asset': asset_id, 'mono_policy': policy}


def export(store, asset_id, out):
    from .engine import summary
    asset = store.ensure_audio(asset_id)
    out = fresh_output(out, [Path(p).parent for p in asset['paths']])
    out.mkdir()
    source_dir = store.asset_dir(asset_id)
    # Export only selected complete runs, no abandoned temporary/model inputs.
    shutil.copy2(asset['audio'], out / 'source.wav')
    runs = store.latest(asset_id)
    for r in runs.values():
        p = source_dir / 'runs' / r['id']
        if p.is_dir() and r['status'] == 'complete':
            shutil.copytree(p, out / 'details' / r['id'], ignore=shutil.ignore_patterns('input.wav', 'whisper_input.wav'))
    def relative(value):
        if isinstance(value, dict):
            return {k: relative(v) for k, v in value.items()}
        if isinstance(value, list):
            return [relative(v) for v in value]
        if isinstance(value, str):
            if value == asset['audio']:
                return 'source.wav'
            prefix = str(source_dir / 'runs') + '/'
            if value.startswith(prefix):
                return 'details/' + value[len(prefix):]
        return value
    events = store.events(asset_id, limit=1000000)
    for name, value in [('summary', summary(store, asset_id)), ('timeline', [e for e in events if 'repetition' not in e['type']]),
                        ('relations', [e for e in events if 'repetition' in e['type']])]:
        (out / (name + '.json')).write_text(dumps(relative(value)), encoding='utf-8')
    manifest = {'schema': 1, 'asset_id': asset_id, 'decoded': asset['metadata']['decoded'],
                'runs': [{'id': r['id'], 'task': r['task'], 'status': r['status']} for r in runs.values()],
                'files': [str(p.relative_to(out)) for p in sorted(out.rglob('*')) if p.is_file()]}
    (out / 'manifest.json').write_text(dumps(manifest), encoding='utf-8')
    return {'path': str(out), 'events': len(events)}
