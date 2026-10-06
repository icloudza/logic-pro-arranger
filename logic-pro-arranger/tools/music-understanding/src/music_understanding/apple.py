"""Optional Apple framework process; never load or invoke it on non-macOS."""
import json
import os
import subprocess
import sys
from pathlib import Path

from .features import event
from .models import BackendUnavailable


def helper_path():
    return Path(os.environ.get('MUSIC_APPLE_HELPER', str(Path(os.environ.get('MUSIC_ROOT', Path(__file__).resolve().parents[2])) / 'build/music-apple')))


def available():
    return sys.platform == 'darwin' and helper_path().is_file()


def call(*args, timeout=120):
    if sys.platform != 'darwin':
        raise BackendUnavailable('Apple 音频框架仅支持 macOS；可选择 CLAP/whisper.cpp 后端')
    if not helper_path().is_file():
        raise BackendUnavailable('Apple helper 未编译；运行 make all')
    try:
        p = subprocess.run([str(helper_path()), *map(str, args)], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise BackendUnavailable('Apple 后端超时；未返回可用结果') from e
    try: result = json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise BackendUnavailable('Apple 后端未返回 JSON：' + p.stderr[-500:]) from e
    if p.returncode or result.get('error'):
        raise BackendUnavailable(result.get('error', p.stderr[-500:]))
    return result


def capabilities(locale='zh-CN'):
    if sys.platform != 'darwin':
        return {'available': False, 'platform': sys.platform, 'reason': 'not_macos'}
    try: return call('capabilities', '--locale', locale, timeout=30)
    except BackendUnavailable as e: return {'available': False, 'reason': str(e)}


def sound_events(path, origin, duration):
    result = call('sounds', path, timeout=max(120, duration * 4))
    raw = result.pop('events')
    events = []
    for item in raw:
        t0 = max(0, item['t0']); t1 = min(duration, item['t1'])
        if t1 <= t0: continue
        events.append(event('sound_class_candidate', origin+t0, origin+t1,
            {'candidates': item['candidates']}, 'apple-soundanalysis:version1',
            reasons=['os_model_score_not_calibrated', 'broad_sound_classes_not_note_transcription']))
    result.update(event_count=len(events), calibrated=False,
                  status='candidate' if events else 'insufficient_context',
                  note='No zero-padding or repeated short sample is passed as original context.')
    # Keep only maximum observed score per label in the compact summary.
    tags = {}
    for e in events:
        for c in e['value']['candidates']: tags[c['label']] = max(tags.get(c['label'], 0), c['score'])
    result['tags'] = [dict(label=k, score=v) for k,v in sorted(tags.items(), key=lambda x:-x[1])[:5]]
    return result, events


def transcribe(path, origin, duration, locale='zh-CN', install_assets=False):
    args = ['speech', path, '--locale', locale]
    if install_assets: args.append('--install-assets')
    result = call(*args, timeout=max(600 if install_assets else 120, duration*4))
    raw = result.pop('events'); events=[]
    for item in raw:
        t0=max(0,item['t0']); t1=min(duration,item['t1'])
        if t1<=t0: continue
        words=[]
        for w in item.get('words',[]):
            if w['t0'] < duration and w['t1'] > 0:
                words.append(dict(w, t0=origin+max(0,w['t0']), t1=origin+min(duration,w['t1'])))
        events.append(event('text_candidate', origin+t0, origin+t1,
            {'text': item['text'], 'words': words, 'alternatives': item.get('alternatives', [])},
            'apple-speechanalyzer', reasons=['speech_transcription_not_singing_validation']))
    result['segments']=len(events)
    return result,events
