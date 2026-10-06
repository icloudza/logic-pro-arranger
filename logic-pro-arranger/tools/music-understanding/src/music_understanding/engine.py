"""Composable task graph. A failed model remains a failed task, never a silent fallback."""
import contextlib
import hashlib
import json
import sys
import tempfile
import re
from pathlib import Path

import numpy as np
import soundfile as sf

from . import features, models
from .audio import load, mono
from .tool_support import cached_digest as digest
from .store import dumps

ALIGN_PHASE_P90_MAX_S = .025
TASKS = ('envelope', 'timbre', 'onsets', 'pitch', 'rhythm', 'loop', 'notes', 'chords', 'structure', 'sounds', 'perception', 'stems', 'lyrics', 'relations')
BRANCHES = {
    'sample': ['envelope', 'timbre', 'onsets', 'pitch'],
    'drum': ['envelope', 'timbre', 'onsets'],
    'loop': ['envelope', 'timbre', 'onsets', 'rhythm', 'loop'],
    'phrase': ['envelope', 'onsets', 'notes'],
    'vocal': ['envelope', 'onsets', 'pitch'],
    'song': ['envelope', 'timbre', 'rhythm', 'chords', 'structure', 'relations'],
}


def plan(asset, branch='auto', tasks=None):
    duration = asset['metadata'].get('decoded', {}).get('duration_s', asset['metadata'].get('duration_s')) or 0
    chosen = branch
    if chosen == 'auto':
        name = ' '.join(asset['paths'])
        if re.search(r'loop|\d{2,3}\s*bpm', name, re.I):
            chosen = 'loop'
        else:
            chosen = 'sample' if duration < 12 else 'phrase' if duration < 32 else 'song'
    selected = list(dict.fromkeys(tasks or BRANCHES[chosen]))
    if tasks is None:
        from .apple import available
        if available(): selected.append('sounds')
    unknown = set(selected) - set(TASKS)
    if unknown:
        raise ValueError('未知任务：' + ', '.join(sorted(unknown)))
    for dependent, parent in [('loop', 'rhythm')]:
        if dependent in selected and parent not in selected:
            selected.insert(0, parent)
    # Dependency order independent of user argument order.
    selected = sorted(selected, key=lambda t: TASKS.index(t))
    return {'branch': chosen, 'branch_source': 'filename_and_duration_hint' if branch == 'auto' else 'user',
            'tasks': selected, 'note': 'branch is a processing suggestion, not instrument recognition'}


def implementation_hash():
    h = hashlib.sha256()
    for f in sorted(Path(__file__).parent.glob('*.py')):
        h.update(f.name.encode()); h.update(f.read_bytes())
    from .native import library_path
    native = library_path()
    if native.is_file(): h.update(native.read_bytes())
    from .apple import helper_path
    helper = helper_path()
    if sys.platform == 'darwin' and helper.is_file(): h.update(helper.read_bytes())
    return h.hexdigest()


def identity(options):
    import platform
    result = {'platform': platform.platform(), 'implementation_sha256': implementation_hash(), 'dependencies': models.versions([
        'numpy', 'scipy', 'librosa', 'soundfile', 'beat-this', 'torch', 'torchcrepe',
        'basic-pitch', 'coremltools', 'onnxruntime', 'audio-separator', 'laion-clap'])}
    for k in ('clap_model', 'whisper_model'):
        p = options.get(k)
        if p and Path(p).is_file():
            result[k + '_sha256'] = digest(p)
    # Hash locally available model checkpoints before consulting cached results.
    checkpoint = Path.home() / '.cache/torch/hub/checkpoints/beat_this-final0.ckpt'
    if checkpoint.exists():
        result['beat_this_sha256'] = digest(checkpoint)
    return result


def analyze(store, ref, branch='auto', tasks=None, start=0, end=None, options=None):
    asset_id = store.resolve(ref); asset = store.ensure_audio(asset_id)
    options = options or {}
    duration = asset['metadata']['decoded']['duration_s']
    if start < 0 or start >= duration or (end is not None and (end <= start or end > duration + 1e-6)):
        raise ValueError('分析范围应在原音时长内，且起点小于终点')
    end = duration if end is None else end
    selection = plan(asset, branch, tasks)
    original, source_sr, origin = load(asset['audio'], start=start, end=end)
    x, sr, _ = load(asset['audio'], start=start, end=end, sr=features.SR)
    y, mono_method = mono(x)
    fingerprint = identity(options)
    outputs = []; dependencies = {}
    for task in selection['tasks']:
        params = {'start': origin, 'end': end, 'options': options, 'identity': fingerprint,
                  'dependencies': {k: v['run_id'] for k, v in dependencies.items() if
                     (task == 'loop' and k == 'rhythm') or (task == 'relations' and k == 'chords')}}
        key = store.signature(asset_id, task, params)
        cached = store.cached(key)
        if cached:
            result = {'run_id': key, 'task': task, 'status': 'complete', 'summary': cached['summary'], 'cached': True}
        else:
            folder = store.asset_dir(asset_id) / 'runs' / key
            folder.mkdir(parents=True, exist_ok=True)
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    if task == 'envelope':
                        summary, ev = features.measure(original, source_sr, origin, folder)
                    else:
                        summary, ev = run_task(task, y, sr, origin, folder, asset, options, dependencies)
                for e in ev:
                    e['source'] = {'algorithm': e['source'], 'implementation_sha256': fingerprint['implementation_sha256'],
                                   'libraries': fingerprint['dependencies']}
                    if options.get('part'):
                        e['part'] = options['part']
                summary['scope'] = {'t0': origin, 't1': end, 'coordinate': 'original decoded audio seconds'}
                summary['provenance'] = params
                summary['mono_policy'] = 'original_channels' if task == 'envelope' else mono_method
                result = store.save_run(asset_id, task, key, 'complete', summary, ev)
            except models.BackendUnavailable as e:
                result = store.save_run(asset_id, task, key, 'unavailable', {'reason': str(e), 'scope': [origin, end]}, [])
            except Exception as e:
                result = store.save_run(asset_id, task, key, 'failed', {'reason': f'{type(e).__name__}: {e}', 'scope': [origin, end]}, [])
        dependencies[task] = result
        outputs.append(result)
    return {'asset_id': asset_id, 'plan': selection, 'scope': [origin, end], 'tasks': outputs,
            'status': 'complete' if all(x['status'] == 'complete' for x in outputs) else 'partial'}


def run_task(task, y, sr, origin, folder, asset, options, dependencies):
    if task == 'envelope':
        return features.measure(y, sr, origin, folder)
    if task == 'timbre':
        return features.timbre(y, sr, origin, folder)
    if task == 'onsets':
        return features.onsets(y, sr, origin, folder)
    if task == 'pitch':
        backend = options.get('pitch_backend', 'yin-c')
        from .native import pitch as native_pitch
        if backend == 'ensemble':
            yin_dir, crepe_dir = Path(folder)/'yin', Path(folder)/'crepe'
            yin_dir.mkdir(exist_ok=True); crepe_dir.mkdir(exist_ok=True)
            a, ev = native_pitch(y, sr, origin, yin_dir, options.get('fmin', 32), options.get('fmax', 2093))
            b, bev = models.crepe(y, sr, origin, crepe_dir, options.get('device', 'auto'))
            ra, rb = a.get('root'), b.get('root')
            agree = bool(ra and rb and abs(ra['midi'] - rb['midi']) < .5)
            root = ra if agree else None
            for item in ev + bev:
                if not agree: item['reasons'].append('cross_model_disagreement_or_abstention')
            for e in bev:
                if e['type'] == 'note': e['type'] = 'note_alternative'
            return {'backend': 'yin-c+torchcrepe', 'root': root, 'contour': a.get('contour'),
                    'observations': {'yin-c': a, 'torchcrepe': b}, 'models_agree': agree,
                    'probability': None, 'reasons': [] if agree else ['cross_model_disagreement_or_abstention']}, ev + bev
        if options.get('pitch_backend') == 'torchcrepe':
            return models.crepe(y, sr, origin, folder, options.get('device', 'auto'))
        pitch_fn = features.pitch if backend == 'pyin' else native_pitch
        return pitch_fn(y, sr, origin, folder, options.get('fmin', 32), options.get('fmax', 2093))
    if task == 'rhythm':
        if options.get('beat_backend', 'librosa') == 'beat-this':
            result, events = models.beat_model(y, sr, origin, folder, options.get('device', 'auto'))
            check, _ = features.rhythm(y, sr, origin, folder, options.get('bpm'))
            expected = options.get('bpm') or check.get('bpm')
            model_bpm = result.get('bpm')
            ratio = model_bpm / expected if expected and model_bpm else None
            factor = min((.5, 1., 2.), key=lambda x: abs(ratio-x)) if ratio else None
            conflict = bool(ratio and abs(ratio/factor - 1) > .04)
            beats = np.asarray(result.get('beats', []), dtype=float)
            reference = np.asarray(check.get('beats', []), dtype=float)
            # Compare the sparser metrical level against the denser one; tempo
            # agreement alone does not establish the phase or the bar downbeat.
            sparse, dense = (beats, reference) if len(beats) <= len(reference) else (reference, beats)
            errors = np.array([np.min(np.abs(dense-t)) for t in sparse]) if len(dense) else np.array([])
            phase_error = float(np.quantile(errors, .9)) if len(errors) >= 4 else None
            intervals = np.diff(beats)
            regular = bool(len(intervals) >= 3 and np.std(intervals) / np.mean(intervals) < .05)
            # Alignment shifts audio by these beats, so the tolerance is an audible-offset bound
            # (about where a vocal/drum offset starts to flam), tightened to 1/16 beat at fast tempi.
            tolerance = min(ALIGN_PHASE_P90_MAX_S, 60 / model_bpm / 16) if model_bpm else ALIGN_PHASE_P90_MAX_S
            usable = bool(not conflict and regular and phase_error is not None and phase_error <= tolerance)
            result['cross_check'] = {'backend': 'librosa_with_user_bpm' if options.get('bpm') else 'librosa',
                'bpm': expected, 'model_to_reference_factor': factor, 'conflict': conflict,
                'phase_p90_s': phase_error, 'phase_tolerance_s': tolerance, 'regular_intervals': regular}
            result['status'] = 'conflict' if conflict else 'supported_grid' if usable else 'candidate'
            result['usable_for_alignment'] = usable
            result['alignment_scope'] = 'beat_phase_only; downbeat needs musical confirmation'
            result['grid'] = {'beats': beats.tolist(), 'bpm': model_bpm} if usable else None
            if expected and not conflict:
                result['candidates'] = sorted(set(result.get('candidates', []) + [expected]))
            if not usable:
                reason = 'tempo_conflict_requires_review' if conflict else 'insufficient_phase_or_interval_agreement'
                result.setdefault('reasons', []).append(reason)
                for e in events:
                    e['type'] = 'beat_candidate'
                    e.setdefault('reasons', []).append(reason)
            # Retain model observations even when alignment is not supported.
            return result, events
        return features.rhythm(y, sr, origin, folder, options.get('bpm'))
    if task == 'loop':
        r = dependencies['rhythm']
        if r['status'] != 'complete':
            raise models.BackendUnavailable('节拍任务未完成，循环网格不可用')
        return features.loop(y, sr, origin, folder, r['summary'])
    if task == 'chords':
        return features.harmony(y, sr, origin, folder)
    if task == 'structure':
        return features.structure(y, sr, origin, folder)
    if task == 'relations':
        return repetitions(y, sr, origin, folder, dependencies)
    # All neural backends consume the same canonical, sample-accurately cropped source.
    segment = folder / 'input.wav'
    if not segment.exists():
        original, rate, actual_origin = load(asset['audio'], origin, origin + len(y) / sr)
        sf.write(segment, original, rate, subtype='FLOAT')
    if task == 'notes':
        return models.basic_pitch(segment, origin, len(y) / sr, folder, options.get('notes_backend', 'auto'))
    if task == 'sounds':
        from . import apple
        backend = options.get('sounds_backend', 'auto')
        if backend == 'apple' or (backend == 'auto' and apple.available()):
            return apple.sound_events(segment, origin, len(y) / sr)
        if options.get('clap_model'):
            tags = options.get('tags') or ['A person speaking', 'A person singing', 'A piano', 'A guitar',
                'A drum beat', 'A bass sound', 'A bell', 'A synthesizer', 'Noise', 'Silence']
            result, events = models.clap(segment, origin, len(y)/sr, folder, options['clap_model'], tags)
            result['backend_selection'] = 'explicit_clap' if backend == 'clap' else 'apple_unavailable_using_clap'
            return result, events
        raise models.BackendUnavailable('声音分类需要 macOS SoundAnalysis，或 --clap-model 指定 CLAP 权重')
    if task == 'perception':
        return models.clap(segment, origin, len(y) / sr, folder, options.get('clap_model'), options.get('tags', []))
    if task == 'stems':
        return models.separate(segment, origin, len(y) / sr, folder, options.get('separation_model', 'htdemucs.yaml'))
    if task == 'lyrics':
        from . import apple
        backend = options.get('speech_backend', 'auto')
        if backend == 'apple' or (backend == 'auto' and apple.available() and not options.get('whisper_model')):
            return apple.transcribe(segment, origin, len(y) / sr, options.get('locale', 'zh-CN'), options.get('install_apple_assets', False))
        return models.transcribe(segment, origin, len(y) / sr, folder, options.get('whisper_model'), options.get('whisper_executable', 'whisper-cli'))
    raise ValueError(task)


def repetitions(y, sr, origin, folder, dependencies):
    """Bounded time-domain proposals, useful even when no beat grid is reliable."""
    import librosa
    if len(y) / sr < 8:
        return {'status': 'insufficient_context', 'repetitions': []}, []
    hop = 1024
    chroma = librosa.feature.chroma_stft(y=y, sr=sr, n_fft=4096, hop_length=hop)
    width = round(4 * sr / hop)
    step = round(2 * sr / hop)
    proposals = []
    for a in range(0, chroma.shape[1] - width, step):
        for b in range(a + width, chroma.shape[1] - width, step):
            left = chroma[:, a:a + width]; right = chroma[:, b:b + width]
            left = left / np.maximum(np.linalg.norm(left, axis=0), 1e-10)
            right = right / np.maximum(np.linalg.norm(right, axis=0), 1e-10)
            score = float(np.mean(np.sum(left * right, axis=0)))
            if score >= .92:
                proposals.append({'a': [origin + a * hop / sr, origin + (a + width) * hop / sr],
                                  'b': [origin + b * hop / sr, origin + (b + width) * hop / sr],
                                  'chroma_cosine': score})
    proposals.sort(key=lambda v: -v['chroma_cosine']); proposals = proposals[:12]
    ev = [features.event('repetition_candidate', r['a'][0], r['b'][1], r, 'fixed-window-chroma-v1',
                         reasons=['harmonic_similarity_not_melody_identity', 'fixed_4s_windows']) for r in proposals]
    return {'status': 'candidates', 'repetitions': proposals, 'not_inferred': ['tension', 'masking', 'call_response']}, ev


def summary(store, asset_id):
    asset = store.get(asset_id)
    tasks = {}
    for name, run in store.latest(asset_id).items():
        s = {k: v for k, v in run['summary'].items() if k not in ('provenance', 'descriptor', 'raw')}
        # Large beat/contour arrays stay behind the query interface.
        for k in ('beats', 'downbeats', 'times', 'candidates', 'repetitions'):
            if isinstance(s.get(k), list) and len(s[k]) > 8:
                s[k + '_count'] = len(s[k]); s[k] = s[k][:8]
        tasks[name] = {'status': run['status'], 'run_id': run['id'], 'summary': s}
    result = {'asset': asset, 'tasks': tasks, 'corrections': store.corrections(asset_id),
              'missing_tasks': [t for t in TASKS if t not in tasks],
              'auditory_review': False, 'probabilities_calibrated': False}
    root = tasks.get('pitch', {}).get('summary', {}).get('root')
    embedded = asset['metadata'].get('embedded', {}).get('root_midi')
    if root and embedded is not None and abs(root['midi'] - embedded) > .6:
        result['conflicts'] = [{'field': 'root_midi', 'embedded': embedded,
                               'estimated': root['midi'], 'resolution': 'needs_verification'}]
    return result
