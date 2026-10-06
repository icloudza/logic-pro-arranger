#!/usr/bin/env python3
"""Reproducible generated-signal checks + local-library model smoke tests.
No actual-library filename or metadata is promoted to ground truth.
"""
import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from music_understanding.engine import analyze, summary
from music_understanding.store import Store
from music_understanding.audio import digest
from music_understanding import features, native


def main():
    p = argparse.ArgumentParser(); p.add_argument('--out', type=Path, required=True)
    p.add_argument('--home', type=Path); p.add_argument('--models', action='store_true')
    args = p.parse_args(); args.out.mkdir(parents=True, exist_ok=False)
    source = args.out / 'signals'; source.mkdir(); cache = args.out / 'cache'
    store = Store(cache); results = []
    for midi in (24, 36, 48, 60, 72, 84):
        sr = 22050; t = np.arange(sr * 2) / sr; hz = 440 * 2 ** ((midi - 69) / 12)
        y = (.3 * np.sin(2 * np.pi * hz * t)).astype('float32')
        path = source / f'midi-{midi}.wav'; sf.write(path, y, sr, subtype='FLOAT')
        observed = {}
        for backend in ('yin-c', 'pyin'):
            begin = time.perf_counter()
            r = analyze(store, str(path), tasks=['pitch'], options={'pitch_backend': backend, 'fmin': 30})
            result = r['tasks'][0]; root = result['summary'].get('root')
            observed[backend] = {'elapsed_s': time.perf_counter() - begin, 'status': result['status'],
                'root_midi': root['midi'] if root else None,
                'error_cents': abs(root['midi'] - midi) * 100 if root else None}
        results.append({'truth_midi': midi, 'truth_source': 'generated sinusoid', 'observations': observed})
    # Warm same input; distinguishes algorithm complexity from CLI/process startup.
    speed = {}
    for label, fn in [('yin-c', native.pitch), ('pyin', features.pitch)]:
        folder = args.out / label; folder.mkdir()
        fn(y, sr, 0, folder, 30)
        timings = []
        for _ in range(3):
            started = time.perf_counter(); fn(y, sr, 0, folder, 30); timings.append(time.perf_counter() - started)
        speed[label] = {'median_s': statistics.median(timings), 'samples_s': timings}
    asset = r['asset_id']; binary = Path(__file__).resolve().parents[1] / 'build/music'
    cold = {}
    for label, prefix in [('c', [str(binary)]), ('python', [sys.executable, '-m', 'music_understanding'])]:
        ts = []
        for _ in range(5):
            begin = time.perf_counter()
            subprocess.run(prefix + ['--home', str(cache), 'query', asset, 'summary'], check=True, stdout=subprocess.DEVNULL)
            ts.append(time.perf_counter() - begin)
        cold[label] = {'median_s': statistics.median(ts), 'samples_s': ts}
    store.close()
    real = []
    if args.models:
        live = Store(args.home)
        tests = [
            ('5333457695d047f51db059931b8fc941f5e0da0d9f091cf06558b17fb2f5154a', 'pitch', {'pitch_backend':'ensemble'}),
            ('093d2d9d18d789f4fcff43428780cf363e168e883bddde02933f4b765bef2072', 'notes', {}),
            ('9b71c74d867bd5f53d8655aebe622c329f9efd445e1426b97982c873e176366e', 'rhythm', {'beat_backend':'beat-this'})]
        for asset, task, options in tests:
            begin=time.perf_counter(); r=analyze(live,asset,tasks=[task],options=options)
            real.append({'asset_id':asset,'path':live.get(asset)['paths'][0],'task':task,'status':r['status'],
                'elapsed_s':time.perf_counter()-begin,'observation':r['tasks'][0]})
            print(task,r['status'],r['tasks'][0]['summary'].get('root',r['tasks'][0]['summary'].get('reason','')),flush=True)
        live.close()
    report={'machine':platform.platform(),'python':sys.version,'generated_pitch':results,'warm_algorithm_times':speed,
        'cold_summary_times':cold,'real_model_smoke':real,
        'limits':['Generated tones test frequency arithmetic only, not general music accuracy.',
                  'YIN and pYIN are different algorithms; timing is not a pure language comparison.',
                  'Real library results have no manually annotated ground truth or listening acceptance.']}
    (args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps({'report':str(args.out/'report.json'),'warm_s':{k:v['median_s'] for k,v in speed.items()},
          'cold_s':{k:v['median_s'] for k,v in cold.items()}},ensure_ascii=False))

if __name__=='__main__':main()
