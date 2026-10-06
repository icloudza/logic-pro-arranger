"""Optional upstream inference adapters. Missing backends never masquerade as results."""
import importlib.metadata
import importlib.util
import json
import os
import sys
import hashlib
from functools import lru_cache
from .tool_support import cached_digest as digest
from pathlib import Path

import numpy as np

from .audio import command, binary
from .features import array_ref, event, note_name


class BackendUnavailable(RuntimeError):
    pass


def require(module, extra):
    if importlib.util.find_spec(module) is None:
        raise BackendUnavailable(f'未安装 {module}；安装 music-understanding[{extra}] 后重试')


def device(requested='auto'):
    import torch
    if requested != 'auto':
        return requested
    if torch.backends.mps.is_available():
        return 'mps'
    if torch.cuda.is_available():
        return 'cuda'
    return 'cpu'


def versions(names):
    result = {}
    for name in names:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def beat_model(y, sr, origin, folder, requested_device='auto', checkpoint='final0'):
    require('beat_this', 'beats')
    from beat_this.inference import Audio2Beats
    import torch
    selected = device(requested_device)
    tracker = Audio2Beats(checkpoint_path=checkpoint, device=selected, dbn=False)
    beats, downbeats = tracker(y, sr)
    beats = np.asarray(beats); downbeats = np.asarray(downbeats)
    beats = beats[(beats >= 0) & (beats < len(y) / sr)] + origin
    downbeats = downbeats[(downbeats >= 0) & (downbeats < len(y) / sr)] + origin
    bpm = float(60 / np.median(np.diff(beats))) if len(beats) > 1 else None
    model_file = Path(torch.hub.get_dir()) / 'checkpoints' / f'beat_this-{checkpoint}.ckpt'
    if Path(checkpoint).is_file():
        model_file = Path(checkpoint)
    model_hash = digest(model_file) if model_file.exists() else None
    intervals = np.diff(beats)
    tempo_curve = np.column_stack([beats[1:], 60 / np.maximum(intervals, 1e-9)])
    result = {'backend': 'beat-this', 'device': selected, 'model_sha256': model_hash,
              'model': checkpoint, 'bpm': bpm, 'beats': beats.tolist(), 'downbeats': downbeats.tolist(),
              'candidates': [x for x in [bpm / 2, bpm, bpm * 2] if 30 <= x <= 300] if bpm else [],
              'tempo_curve': array_ref(folder, 'tempo', tempo_curve),
              'grid_probability': None, 'reasons': ['tempo_ambiguous', 'uncalibrated_downbeats']}
    ev = [event('beat', t, t, {'beat_index': i,
         'downbeat': bool(len(downbeats) and np.min(np.abs(downbeats - t)) < .03)},
         'beat-this:' + checkpoint, error_s=.02, reasons=['model_resolution_not_error_bound']) for i, t in enumerate(beats)]
    return result, ev


def basic_pitch(audio_path, origin, duration, folder, backend="auto"):
    if backend == 'coreml' and sys.platform != 'darwin':
        raise BackendUnavailable('Core ML 音符推理仅支持 macOS，请选择 onnx')
    require('basic_pitch', 'notes')
    from basic_pitch.inference import predict, Model
    import basic_pitch as bp
    package = Path(bp.__file__).parent
    fallback = None; loader = None; selected = 'onnx'
    if backend == 'coreml' or (backend == 'auto' and sys.platform == 'darwin'):
        try:
            require('coremltools', 'notes')
            import coremltools as ct
            candidates = list(package.rglob('*.mlpackage'))
            if not candidates: raise BackendUnavailable('安装包缺少 Core ML 权重')
            path = candidates[0]
            class CoreMLModel(Model):
                def __init__(self, path):
                    self.model_type = Model.MODEL_TYPES.COREML
                    self.model = ct.models.MLModel(str(path), compute_units=ct.ComputeUnit.ALL)
            loader = CoreMLModel(path); selected = 'coreml'
        except Exception as e:
            if backend == 'coreml': raise BackendUnavailable('Core ML 加载失败：' + str(e)) from e
            fallback = 'Core ML unavailable: ' + str(e)
    if loader is None:
        require('onnxruntime', 'notes')
        candidates = list(package.rglob('*.onnx'))
        if not candidates: raise BackendUnavailable('Basic Pitch 安装包缺少 ONNX 权重')
        path = candidates[0]; loader = Model(str(path))
    h = hashlib.sha256()
    for f in sorted(path.rglob('*')) if path.is_dir() else [path]:
        if f.is_file():
            h.update(str(f.relative_to(path) if path.is_dir() else f.name).encode()); h.update(digest(f).encode())
    model_hash = h.hexdigest()
    output, midi, notes = predict(str(audio_path), model_or_model_path=loader)
    algorithm = 'basic-pitch-' + selected
    raw = {}
    for k, v in output.items():
        raw[k] = array_ref(folder, 'basic_pitch_' + k, np.asarray(v))
    events = []
    for index, item in enumerate(notes):
        t0, t1, pitch, amplitude = item[:4]
        if t0 >= duration or t1 <= 0:
            continue
        events.append(event('note', origin + max(0, t0), origin + min(duration, t1),
            {'midi': int(pitch), 'note': note_name(pitch), 'activation': float(amplitude),
             'pitch_bends': array_ref(folder, f'note_{index}_bends', np.asarray(item[4])) if len(item) > 4 and item[4] is not None else None},
            algorithm, reasons=['single_instrument_assumption', 'uncalibrated']))
    for high in events:
        for low in events:
            interval = high['value']['midi'] - low['value']['midi']
            if interval in (12, 24, 36) and abs(high['t0'] - low['t0']) < .04 and \
               high['t1'] - high['t0'] < .3 and high['t1'] < low['t1'] and \
               high['value']['activation'] < low['value']['activation'] * .6:
                high['reasons'].append('possible_upper_harmonic')
                break
    midi.write(str(Path(folder) / 'notes.mid'))
    return {'backend': algorithm, 'model_sha256': model_hash, 'note_count': len(events),
            'compute_policy': 'CoreML ALL; hardware chosen by OS' if selected == 'coreml' else 'ONNX CPU',
            'fallback_reason': fallback,
            'raw': raw, 'midi': str(Path(folder) / 'notes.mid'),
            'midi_time_origin_s': origin, 'limitations': ['single instrument polyphony; not instrument separation']}, events


def crepe(y, sr, origin, folder, requested_device='auto'):
    require('torchcrepe', 'beats')
    import torch
    import torchcrepe
    import librosa
    selected = device(requested_device)
    signal = librosa.resample(y, orig_sr=sr, target_sr=16000)
    f0, periodicity = torchcrepe.predict(torch.tensor(signal).unsqueeze(0), 16000, 160,
        32, 2000, 'full', batch_size=256, device=selected, return_periodicity=True)
    f0 = f0.detach().cpu().numpy()[0]; periodicity = periodicity.detach().cpu().numpy()[0]
    t = np.arange(len(f0)) * .01
    rms = np.array([np.sqrt(np.mean(signal[max(0, int((v - .025) * 16000)):min(len(signal), int((v + .025) * 16000))] ** 2))
                    if int(v * 16000) < len(signal) else 0 for v in t])
    valid = (periodicity > .7) & (rms > max(float(rms.max()) * .05, 1e-5))
    values = librosa.hz_to_midi(f0[valid])
    root = None
    if len(values) >= 12 and np.ptp(np.percentile(values, [10, 90])) < .65:
        mid = float(np.median(values))
        root = {'midi': mid, 'nearest_midi': round(mid), 'note': note_name(mid), 'status': 'candidate_not_auditory_verified'}
    ref = array_ref(folder, 'crepe_f0', np.column_stack([f0, periodicity, valid]), 100, origin)
    weights = list(Path(torchcrepe.__file__).parent.rglob('full.pth'))
    result = {'backend': 'torchcrepe', 'device': selected, 'root': root, 'contour': ref,
              'periodic_coverage': float(valid.mean()),
              'model_sha256': digest(weights[0]) if weights else None}
    return result, [event('root_candidate', origin, origin + len(y) / sr, root, 'torchcrepe',
                         reasons=['uncalibrated'])] if root else []


@lru_cache(maxsize=1)
def clap_model(checkpoint, modification_ns):
    require('laion_clap', 'perception')
    import laion_clap
    model = laion_clap.CLAP_Module(enable_fusion=False, amodel='HTSAT-base', device='cpu')
    model.load_ckpt(str(checkpoint), verbose=False)
    model.eval()
    return model


def get_clap(checkpoint):
    if not checkpoint or not Path(checkpoint).is_file():
        raise BackendUnavailable('CLAP 需要 --clap-model 指定本地 HTSAT-base checkpoint')
    path = Path(checkpoint).resolve()
    return clap_model(str(path), path.stat().st_mtime_ns)


def clap(audio_path, origin, duration, folder, checkpoint, texts=()):
    from .audio import load, mono
    import torch
    model = get_clap(checkpoint)
    x, sr, _ = load(audio_path, sr=48000); y, _ = mono(x)
    embeddings = []; spans = []
    # Deterministic 10-second windows, never the upstream random long-file crop.
    with torch.inference_mode():
        for start in range(0, len(y), 480000):
            chunk = y[start:start + 480000]
            embeddings.append(model.get_audio_embedding_from_data(x=chunk[None, :], use_tensor=False)[0])
            spans.append([origin + start / sr, origin + (start + len(chunk)) / sr])
        embedding = np.mean(embeddings, axis=0)
        embedding /= max(np.linalg.norm(embedding), 1e-10)
        result = {'backend': 'laion-clap', 'model_sha256': digest(checkpoint),
                  'embedding': array_ref(folder, 'clap', embedding),
                  'window_embeddings': array_ref(folder, 'clap_windows', embeddings), 'windows': spans,
                  'aggregation': 'normalized mean; semantic search uses best window',
                  'tags': [], 'calibrated': False}
        if texts:
            te = model.get_text_embedding(list(texts), use_tensor=False)
            scores = te @ embedding / (np.linalg.norm(te, axis=1) * np.linalg.norm(embedding) + 1e-10)
            result['tags'] = sorted([{'text': t, 'cosine_score': float(s)} for t, s in zip(texts, scores)],
                                    key=lambda x: -x['cosine_score'])[:5]
    return result, [event('perception', origin, origin + duration, result, 'laion-clap', reasons=['zero_shot_uncalibrated'])]


def separate(audio_path, origin, duration, folder, model_name='htdemucs.yaml'):
    require('audio_separator', 'separation')
    from audio_separator.separator import Separator
    sep = Separator(output_dir=str(folder), output_format='WAV', normalization_threshold=1.0,
                    amplification_threshold=0.0, model_file_dir=str(Path.home() / '.cache/music-understanding/models/separation'))
    sep.load_model(model_filename=model_name)
    files = sep.separate(str(audio_path))
    result = {'backend': 'audio-separator', 'model': model_name, 'stems': []}
    import soundfile as sf
    for name in files:
        path = Path(name)
        if not path.is_absolute():
            path = Path(folder) / path
        info = sf.info(path)
        result['stems'].append({'path': str(path), 'sha256': digest(path), 'sample_rate': info.samplerate,
            'duration_s': info.duration, 'origin_s': origin,
            'timing_status': 'unverified_model_delay', 'part': path.stem})
    return result, [event('separated_source', origin, origin + duration, stem, 'audio-separator:' + model_name,
        part=stem['part'], reasons=['separation_bleed', 'model_delay_unverified']) for stem in result['stems']]


def transcribe(audio_path, origin, duration, folder, model_path, executable='whisper-cli'):
    if not model_path or not Path(model_path).is_file():
        raise BackendUnavailable('需要 --whisper-model 指定 whisper.cpp 模型')
    exe = binary(executable)
    wav = Path(folder) / 'whisper_input.wav'
    command([binary('ffmpeg'), '-v', 'error', '-y', '-i', audio_path, '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', wav])
    target = Path(folder) / 'whisper'
    command([exe, '-m', model_path, '-f', wav, '-oj', '-of', target])
    data = json.loads(target.with_suffix('.json').read_text())
    events = []
    for seg in data.get('transcription', []):
        offsets = seg['offsets']
        events.append(event('text_candidate', origin + offsets['from'] / 1000,
            min(origin + duration, origin + offsets['to'] / 1000), {'text': seg['text']},
            'whisper.cpp', reasons=['singing_not_validated', 'asr_unverified']))
    return {'backend': 'whisper.cpp', 'model_sha256': digest(model_path), 'segments': len(events)}, events


def capabilities():
    checks = [('rhythm:beat-this', 'beat_this', 'beats'), ('notes', 'basic_pitch', 'notes'),
              ('pitch:torchcrepe', 'torchcrepe', 'beats'), ('perception', 'laion_clap', 'perception'),
              ('stems', 'audio_separator', 'separation')]
    return [{'capability': name, 'installed': importlib.util.find_spec(module) is not None,
             'extra': extra, 'runtime_checked_by_this_command': False} for name, module, extra in checks]
