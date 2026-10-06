"""Deterministic measurements and explicitly uncalibrated musical hypotheses."""
import math
from pathlib import Path

import librosa
import numpy as np
from scipy.ndimage import median_filter, uniform_filter1d
from scipy.signal import find_peaks

SR = 22050
HOP = 256
NOTE = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def note_name(midi):
    m = int(round(midi))
    return f'{NOTE[m % 12]}{m // 12 - 1}'


def db(x):
    return float(20 * np.log10(max(float(x), 1e-12)))


def event(kind, t0, t1, value, source, part='mix', reasons=(), error_s=None):
    return {'type': kind, 't0': float(t0), 't1': float(t1), 'part': part, 'value': value,
            'source': source, 'evidence_kind': 'measurement' if kind in ('envelope', 'onset', 'spectrum') else 'estimate',
            'probability': None, 'calibrated': False, 'time_error_s': error_s,
            'reasons': list(reasons)}


def array_ref(folder, name, values, rate=None, origin=0):
    p = Path(folder) / f'{name}.npy'
    np.save(p, np.asarray(values), allow_pickle=False)
    return {'path': str(p), 'shape': list(np.shape(values)), 'dtype': str(np.asarray(values).dtype),
            'frame_rate': rate, 'origin_s': origin}


def measure(y, sr, origin, folder):
    dur = len(y) / sr
    peak = float(np.abs(y).max())
    rms_total = float(np.sqrt(np.mean(y.astype('float64') ** 2)))
    frame = 1024; hop = HOP
    # Peak/clipping use every original sample/channel; RMS envelope uses channel power.
    energy = np.sqrt(np.mean(y.astype("float64") ** 2, axis=1)) if y.ndim > 1 else y
    rms = librosa.feature.rms(y=energy, frame_length=frame, hop_length=hop)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop) + origin
    active = rms > max(rms.max() * .01, 1e-5)
    ix = np.flatnonzero(active)
    effective = [max(origin, float(times[ix[0]] - frame / sr / 2)),
                 min(origin + dur, float(times[ix[-1]] + frame / sr / 2))] if len(ix) else None
    peak_at = int(np.argmax(np.max(np.abs(y), axis=1) if y.ndim > 1 else np.abs(y))) / sr + origin
    value = {'measurement_domain': 'original_decoded_samples_all_channels', 'sample_rate': sr,
             'peak_dbfs': db(peak), 'rms_dbfs': db(rms_total), 'effective_span': effective,
             'peak_at_s': peak_at, 'duration_s': dur, 'silence': peak < 1e-5,
             'clipping_sample_fraction': float(np.mean(np.abs(y) >= 1)),
             'amplitude_envelope': array_ref(folder, 'rms', rms, sr / hop, origin),
             'effective_threshold': 'max(-40 dB relative to peak frame RMS, -100 dBFS)',
             'attack_to_peak_s': max(0, peak_at - effective[0]) if effective else None}
    return value, [event('envelope', origin, origin + dur, value, 'rms-v1', error_s=frame / sr / 2)]


def timbre(y, sr, origin, folder):
    s = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    mel = librosa.feature.melspectrogram(S=s ** 2, sr=sr, n_mels=40)
    active = np.sum(s * s, axis=0) > max(np.max(np.sum(s * s, axis=0)) * .001, 1e-12)
    if not active.any():
        return {'status': 'no_signal', 'descriptor': None}, []
    shape = np.log(np.mean(mel[:, active], axis=1) + 1e-10)
    shape -= shape.mean()
    shape /= max(float(np.linalg.norm(shape)), 1e-10)
    value = {'descriptor': shape.tolist(), 'descriptor_type': 'relative_log_mel_40_v1',
             'centroid_hz_median': float(np.median(librosa.feature.spectral_centroid(S=s, sr=sr)[:, active])),
             'flatness_median': float(np.median(librosa.feature.spectral_flatness(S=s)[:, active])),
             'interpretation': 'spectral shape only; not semantic or perceptual identity',
             'spectrogram': array_ref(folder, 'log_mel', np.log1p(mel).astype('float32'), sr / HOP, origin)}
    return value, [event('spectrum', origin, origin + len(y) / sr, value, 'librosa-logmel-v1')]


def pitch(y, sr, origin, folder, fmin=32, fmax=2093):
    duration = len(y) / sr
    frame = 2048
    if len(y) < frame or np.max(np.abs(y)) < 1e-5:
        return {'status': 'insufficient_signal', 'root': None}, []
    f0, voiced, probability = librosa.pyin(y, fmin=fmin, fmax=min(fmax, sr / 2 - 1),
                                         sr=sr, frame_length=frame, hop_length=HOP, fill_na=np.nan)
    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=HOP)[0][:len(f0)]
    valid = voiced & (probability >= .15) & (rms > max(rms.max() * .05, 1e-5))
    return pitch_summary(f0, probability, valid, sr / HOP, origin, duration, folder,
                         'librosa.pyin-v2', frame / sr, fmin, fmax)


def pitch_summary(f0, probability, valid, rate, origin, duration, folder, backend, window, fmin, fmax):
    hop_s = 1 / rate
    midis = np.full(len(f0), np.nan)
    midis[valid] = librosa.hz_to_midi(f0[valid])
    times = np.arange(len(f0)) / rate + origin
    contour = array_ref(folder, 'f0', np.column_stack([f0, probability, valid]).astype('float32'), rate, origin)
    # Stable root estimation excludes the attack, but preserves every contour sample.
    stable_mask = valid & (times >= origin + min(.15, duration * .2))
    values = midis[stable_mask]
    reasons = ['monophonic_assumption']
    root = None
    coverage = float(valid.mean())
    if len(values) >= 6:
        median = float(np.median(values))
        spread = float(np.percentile(values, 90) - np.percentile(values, 10))
        if spread <= .65 and len(values) * hop_s >= .12:
            root = {'midi': median, 'nearest_midi': round(median), 'note': note_name(median),
                    'cents_from_equal_temperament': (median - round(median)) * 100,
                    'spread_10_90_cents': spread * 100, 'status': 'candidate_not_auditory_verified'}
        else:
            reasons.append('pitch_varies_or_multiple_sources')
    else:
        reasons.append('insufficient_periodic_frames')
    if coverage < .4:
        reasons.append('low_periodic_coverage')
    notes = []
    # Segment only supported frames. Unobserved gaps are not declared rests.
    rounded = np.full(len(midis), -999, dtype=int)
    rounded[valid] = np.rint(midis[valid]).astype(int)
    smooth = median_filter(rounded, size=3)
    smooth[~valid] = -999
    edges = np.r_[0, np.flatnonzero(np.diff(smooth)) + 1, len(smooth)]
    for a, b in zip(edges[:-1], edges[1:]):
        if smooth[a] == -999 or (b - a) * hop_s < .07:
            continue
        t0 = max(origin, times[a] - hop_s / 2)
        t1 = min(origin + duration, times[min(b, len(times) - 1)] + hop_s / 2)
        note = float(np.nanmedian(midis[a:b]))
        notes.append(event('note', t0, t1, {'midi': note, 'note': note_name(note),
            'median_voiced_score': float(np.median(probability[a:b])), 'contour': contour},
            backend, error_s=window / 2, reasons=['monophonic_assumption']))
    summary = {'backend': backend, 'status': 'candidate' if root else 'undetermined', 'root': root,
               'periodic_coverage': coverage, 'contour': contour, 'reasons': reasons,
               'frequency_range_hz': [fmin, fmax], 'frame_window_s': window,
               'hop_s': hop_s, 'voicing_score_is_probability_of_correct_pitch': False}
    if root:
        notes.insert(0, event('root_candidate', origin, origin + duration, root, backend, reasons=reasons))
    return summary, notes


def onsets(y, sr, origin, folder):
    """Energy attacks with sample-domain backtracking; sustained pitch is not an onset."""
    power = uniform_filter1d(np.asarray(y, dtype='float64') ** 2, max(1, round(.032 * sr)), mode='constant')
    rms = np.sqrt(np.maximum(power, 0))
    hop = max(1, round(.004 * sr))
    sampled = rms[::hop]
    floor = max(float(rms.max()) * .01, 1e-5)
    log = np.log(np.maximum(sampled, floor))
    span = 5  # 20 ms on each side of a candidate
    padded = np.pad(log, span, mode='edge')
    novelty = np.maximum(0, padded[2*span:] - padded[:-2*span])
    peaks, _ = find_peaks(novelty, height=np.log(1.8), prominence=.3, distance=max(1, round(.06 * sr / hop)))
    candidates = [int(p * hop) for p in peaks]
    active = np.flatnonzero(np.abs(y) > max(float(np.max(np.abs(y))) * .02, 1e-5))
    if len(active): candidates.insert(0, int(active[0]))
    times = []
    for at in sorted(candidates):
        lo, hi = max(0, at-round(.05*sr)), min(len(y), at+round(.05*sr))
        pre = y[max(0, lo-round(.03*sr)):lo]
        baseline = float(np.sqrt(np.mean(pre ** 2))) if len(pre) else 0.
        threshold = max(float(np.max(np.abs(y[lo:hi]))) * .08, baseline * 2.5, 1e-5)
        crossed = np.flatnonzero(np.abs(y[lo:hi]) >= threshold)
        if not len(crossed): continue
        t = (lo + int(crossed[0])) / sr + origin
        if not times or t - times[-1] >= .06:
            times.append(t)
    values = [event('onset', t, t, {'detector': 'energy_attack_backtrack', 'class': 'unclassified'},
                    'energy-onset-v2', error_s=None, reasons=['threshold_dependent_attack', 'may_miss_legato_or_soft_attacks']) for t in times]
    return {'count': len(times), 'times': times, 'resolution_s': 1/sr,
            'limitations': ['energy_attacks_only', 'threshold_dependent_not_an_error_bound'],
            'strength': array_ref(folder, 'onset', novelty, sr/hop, origin)}, values


def rhythm(y, sr, origin, folder, bpm=None):
    if len(y) / sr < 1 or np.max(np.abs(y)) < 1e-5:
        return {'status': 'insufficient_signal', 'candidates': [], 'beats': []}, []
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    if np.max(env) < 1e-6:
        return {'status': 'insufficient_onsets', 'candidates': [], 'beats': []}, []
    tempo, frames = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=HOP, bpm=bpm, trim=False)
    primary = float(np.asarray(tempo).reshape(-1)[0])
    candidates = sorted(set(round(x, 3) for x in [primary, primary / 2, primary * 2] if 30 <= x <= 300))
    times = librosa.frames_to_time(frames, sr=sr, hop_length=HOP)
    times = times[times < len(y) / sr] + origin
    ev = [event('beat', t, t, {'beat_index': i, 'downbeat': None}, 'librosa.beat-v1',
                error_s=1024 / sr, reasons=['tempo_ambiguous', 'meter_unknown']) for i, t in enumerate(times)]
    return {'status': 'candidate', 'bpm': primary, 'candidates': candidates,
            'bpm_source': 'user' if bpm else 'estimate', 'beats': times.tolist(),
            'grid': None, 'reasons': ['tempo_ambiguous', 'meter_unknown']}, ev


def loop(y, sr, origin, folder, rhythm_result):
    candidates = []
    times = rhythm_result.get('beats', [])
    duration = len(y) / sr
    bpm_values = rhythm_result.get('candidates', [])
    for tempo in bpm_values:
        period = 60 / tempo
        for begin in ([origin] + times[:2]):
            beat_count = int(math.floor((origin + duration - begin) / period + .04))
            if beat_count < 2:
                continue
            end = begin + beat_count * period
            if end > origin + duration + .03:
                continue
            end = min(end, origin + duration)
            a = round((begin - origin) * sr); b = min(len(y) - 1, round((end - origin) * sr))
            jump = abs(float(y[a]) - float(y[b]))
            candidates.append({'t0': begin, 't1': end, 'beats': beat_count, 'bpm': tempo,
                               'remaining_tail_s': origin + duration - end, 'boundary_jump': jump,
                               'status': 'grid_proposal_not_verified_loop'})
    return {'candidates': candidates, 'usable_for_alignment': rhythm_result.get('usable_for_alignment', False),
            'reasons': rhythm_result.get('reasons', []),
            'reason': 'compare repeated content and audition seam before use'}, [
        event('loop_candidate', c['t0'], c['t1'], c, 'grid-loop-v1', reasons=['grid_unverified']) for c in candidates]


def harmony(y, sr, origin, folder):
    if len(y) / sr < .2 or np.max(np.abs(y)) < 1e-5:
        return {'status': 'insufficient_signal'}, []
    chroma = librosa.feature.chroma_stft(y=y, sr=sr, n_fft=4096, hop_length=512)
    rms = librosa.feature.rms(y=y, frame_length=4096, hop_length=512)[0]
    templates = []
    for root in range(12):
        for quality, degrees in [('maj', [0, 4, 7]), ('min', [0, 3, 7])]:
            v = np.zeros(12); v[(root + np.array(degrees)) % 12] = 1 / np.sqrt(3)
            templates.append((NOTE[root] + ':' + quality, v))
    events = []
    step = max(1, round(sr / 512))
    for a in range(0, chroma.shape[1], step):
        b = min(a + step, chroma.shape[1])
        if rms[a:b].max() < max(rms.max() * .02, 1e-5):
            continue
        v = np.mean(chroma[:, a:b], axis=1); v /= max(np.linalg.norm(v), 1e-10)
        ranks = sorted([(name, float(v @ t)) for name, t in templates], key=lambda x: -x[1])[:3]
        events.append(event('chord_candidate', origin + a * 512 / sr,
            min(origin + len(y) / sr, origin + b * 512 / sr),
            {'candidates': [{'chord': n, 'cosine_score': s} for n, s in ranks], 'bass_root': None},
            'chroma-triad-template-v1', reasons=['limited_chord_vocabulary', 'not_calibrated'], error_s=4096 / sr / 2))
    return {'status': 'baseline_candidates', 'vocabulary': '24 major/minor triads; no inversions/extensions',
            'chroma': array_ref(folder, 'chroma', chroma.astype('float32'), sr / 512, origin)}, events


def structure(y, sr, origin, folder):
    hop = 1024
    if len(y) / sr < 6:
        return {'status': 'insufficient_context', 'boundaries': []}, []
    # Spectral novelty baseline; labels are intentionally absent.
    mel = librosa.feature.melspectrogram(y=y, sr=sr, n_fft=2048, hop_length=hop, n_mels=32)
    log = librosa.power_to_db(mel + 1e-12)
    width = max(2, round(sr / hop))
    novel = np.zeros(log.shape[1])
    for i in range(width, len(novel) - width):
        novel[i] = np.linalg.norm(log[:, i:i + width].mean(axis=1) - log[:, i - width:i].mean(axis=1))
    peaks, props = find_peaks(novel, distance=max(1, round(3 * sr / hop)), prominence=max(8., np.std(novel)))
    times = (peaks * hop / sr + origin).tolist()
    ev = [event('boundary', t, t, {'novelty': float(novel[p]), 'label': None}, 'logmel-novelty-v1',
                reasons=['boundary_candidate', 'functional_label_unknown'], error_s=1.) for p, t in zip(peaks, times)]
    return {'boundaries': times, 'labels': None, 'method': 'spectral novelty baseline',
            'novelty': array_ref(folder, 'novelty', novel.astype('float32'), sr / hop, origin)}, ev
