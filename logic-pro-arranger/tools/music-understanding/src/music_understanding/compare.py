import numpy as np
import librosa
from .engine import analyze


def compare(store, a, b, mode):
    left = store.resolve(a); right = store.resolve(b)
    if mode == 'identity':
        return {'same_file_bytes': left == right, 'a': left, 'b': right}
    if mode == 'timbre':
        for asset in (left, right):
            analyze(store, asset, tasks=['timbre'])
        results = [store.latest(asset)['timbre'] for asset in (left, right)]
        vectors = [r['summary'].get('descriptor') for r in results]
        if any(v is None for v in vectors):
            return {'status': 'insufficient_signal', 'a': left, 'b': right}
        return {'a': left, 'b': right, 'spectral_shape_cosine': float(np.dot(*vectors)),
                'evidence_runs': [r['id'] for r in results], 'interpretation': 'spectral shape, not semantic timbre similarity'}
    task = 'pitch' if mode == 'melody' else 'onsets'
    for asset in (left, right):
        analyze(store, asset, tasks=[task])
    events = [store.events(asset, kind='note' if mode == 'melody' else 'onset', limit=10000) for asset in (left, right)]
    if any(len(v) < 2 for v in events):
        return {'status': 'insufficient_events', 'a': left, 'b': right}
    if mode == 'melody':
        pitches = [np.array([e['value']['midi'] for e in seq]) for seq in events]
        transposition = float(np.median(pitches[1]) - np.median(pitches[0]))
        aa, bb = pitches[0][None, :], (pitches[1] - transposition)[None, :]
        cost, path = librosa.sequence.dtw(X=aa, Y=bb, metric='euclidean', global_constraints=True, band_rad=.25)
        return {'a': left, 'b': right, 'transposition_candidate_semitones': transposition,
                'aligned_pitch_error_semitones': float(cost[-1, -1] / len(path)),
                'duration_ratio': (events[1][-1]['t1'] - events[1][0]['t0']) / max(events[0][-1]['t1'] - events[0][0]['t0'], .001),
                'evidence': [[e['id'] for e in seq] for seq in events],
                'limitations': ['monophonic estimates', 'DTW alignment is not proof of repetition']}
    iois = [np.diff([e['t0'] for e in seq]) for seq in events]
    ratio = float(np.median(iois[1]) / max(np.median(iois[0]), 1e-10))
    cost, path = librosa.sequence.dtw(X=(iois[0] / np.median(iois[0]))[None, :],
        Y=(iois[1] / np.median(iois[1]))[None, :], global_constraints=True, band_rad=.25)
    return {'a': left, 'b': right, 'median_interval_ratio': ratio,
            'normalized_interval_dtw_cost': float(cost[-1, -1] / len(path)),
            'limitations': ['onset detector errors propagate', 'not calibrated similarity probability']}


def search(store, text='', similar=None, limit=12):
    if similar:
        asset = store.resolve(similar)
        analyze(store, asset, tasks=['timbre'])
        descriptor = store.latest(asset)['timbre']['summary'].get('descriptor')
        if descriptor is None:
            return {'matches': [], 'reason': 'query has no usable spectrum'}
        rows = store.db.execute('SELECT DISTINCT asset_id FROM runs WHERE task="timbre" AND status="complete"')
        candidates = []
        for row in rows:
            other = row[0]
            run = store.latest(other)['timbre']
            vector = run['summary'].get('descriptor')
            if vector is not None and other != asset:
                candidates.append({'asset_id': other, 'paths': store.get(other)['paths'],
                                   'spectral_shape_cosine': float(np.dot(descriptor, vector))})
        return {'mode': 'spectral_shape', 'matches': sorted(candidates, key=lambda x: -x['spectral_shape_cosine'])[:limit]}
    rows = store.db.execute('SELECT asset_id,path FROM locations WHERE instr(lower(path),lower(?))>0 ORDER BY path', (text,))
    found = {}
    for r in rows:
        found.setdefault(r['asset_id'], []).append(r['path'])
    return {'mode': 'filename_substring_not_semantic', 'matches': [dict(asset_id=k, paths=v) for k, v in list(found.items())[:limit]]}


def semantic_search(store, text, checkpoint, limit=12):
    from .models import get_clap
    from .audio import digest
    import torch
    checkpoint_hash = digest(checkpoint)
    rows = store.db.execute('SELECT DISTINCT asset_id FROM runs WHERE task="perception" AND status="complete"')
    candidates = []
    for row in rows:
        run = store.latest(row[0]).get('perception')
        if run['status'] != 'complete' or run['summary'].get('model_sha256') != checkpoint_hash:
            continue
        candidates.append((row[0], run['summary']))
    if not candidates:
        return {'mode': 'clap_text_audio', 'matches': [], 'reason': 'no embeddings with the selected checkpoint'}
    model = get_clap(checkpoint)
    with torch.inference_mode():
        query = model.get_text_embedding([text], use_tensor=False)[0]
    query /= max(np.linalg.norm(query), 1e-10)
    matches = []
    for asset, info in candidates:
        ref = info.get('window_embeddings', info['embedding'])
        vectors = np.atleast_2d(np.load(ref['path'], allow_pickle=False))
        scores = vectors @ query / np.maximum(np.linalg.norm(vectors, axis=1), 1e-10)
        best = int(np.argmax(scores))
        matches.append({'asset_id': asset, 'paths': store.get(asset)['paths'], 'cosine_score': float(scores[best]),
                        'span': info.get('windows', [[info['scope']['t0'], info['scope']['t1']]])[best]})
    return {'mode': 'clap_text_audio', 'query': text, 'indexed_assets': len(candidates),
            'model_sha256': checkpoint_hash, 'calibrated': False,
            'limitations': ['ranking only; not probability', 'English prompts preferred; Chinese not evaluated'],
            'matches': sorted(matches, key=lambda x: -x['cosine_score'])[:limit]}
