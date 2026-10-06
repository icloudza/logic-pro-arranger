import argparse
import contextlib
import json
import sys
from pathlib import Path

from .audio import AUDIO_EXTENSIONS
from .tool_support import find_tool
from .engine import TASKS, BRANCHES, analyze, plan, summary
from .models import capabilities
from .store import Store


def parser():
    p = argparse.ArgumentParser(description='本地音乐事件分析。stdout 为 JSON，模型日志写 stderr。')
    p.add_argument('--home', type=Path, help='数据库与缓存目录（默认 ~/.cache/music-understanding/data）')
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('doctor', help='检查已安装后端；安装不等于已验证')
    q = sub.add_parser('inspect'); q.add_argument('file')
    q = sub.add_parser('analyze'); q.add_argument('file'); q.add_argument('--branch', choices=['auto'] + list(BRANCHES), default='auto')
    q.add_argument('--tasks', help='逗号分隔：' + ','.join(TASKS))
    q.add_argument('--from', dest='start', type=float, default=0); q.add_argument('--to', dest='end', type=float)
    q.add_argument('--beat-backend', choices=['librosa', 'beat-this'], default='librosa')
    q.add_argument('--pitch-backend', choices=['yin-c', 'pyin', 'torchcrepe', 'ensemble'], default='yin-c')
    q.add_argument('--device', choices=['auto', 'cpu', 'mps', 'cuda'], default='auto')
    q.add_argument('--bpm', type=float); q.add_argument('--fmin', type=float, default=32); q.add_argument('--fmax', type=float, default=2093)
    q.add_argument('--part', default='mix'); q.add_argument('--clap-model'); q.add_argument('--tags', nargs='*', default=[])
    q.add_argument('--whisper-model'); q.add_argument('--whisper-executable', default='whisper-cli')
    q.add_argument('--notes-backend', choices=['auto', 'coreml', 'onnx'], default='auto')
    q.add_argument('--sounds-backend', choices=['auto', 'apple', 'clap'], default='auto')
    q.add_argument('--speech-backend', choices=['auto', 'apple', 'whisper'], default='auto')
    q.add_argument('--locale', default='zh-CN'); q.add_argument('--install-apple-assets', action='store_true')
    q.add_argument('--separation-model', default='htdemucs.yaml')
    q = sub.add_parser('index'); q.add_argument('root', type=Path); q.add_argument('--tasks', help='默认只建文件索引；显式开启逐文件分析')
    q.add_argument('--limit', type=int); q.add_argument('--clap-model')
    q = sub.add_parser('query'); q.add_argument('asset'); q.add_argument('view', choices=['summary', 'events', 'evidence', 'runs'])
    q.add_argument('--from', dest='start', type=float, default=0); q.add_argument('--to', dest='end', type=float, default=float('inf'))
    q.add_argument('--part', '--stem', dest='part'); q.add_argument('--type', dest='kind'); q.add_argument('--task', help='限定任务；保留该任务的原始音符假设'); q.add_argument('--id', dest='event_id')
    q.add_argument('--limit', type=int, default=50); q.add_argument('--offset', type=int, default=0)
    for name in ('audio', 'render'):
        q = sub.add_parser(name); q.add_argument('asset'); q.add_argument('--out', type=Path, required=True)
        q.add_argument('--from', dest='start', type=float, default=0); q.add_argument('--to', dest='end', type=float)
        if name == 'audio': q.add_argument('--overlay', choices=['beats', 'notes'])
        else: q.add_argument('--layers', default='f0,beats,notes')
    q = sub.add_parser('export'); q.add_argument('asset'); q.add_argument('--out', type=Path, required=True)
    q = sub.add_parser('compare'); q.add_argument('a'); q.add_argument('b'); q.add_argument('--mode', choices=['identity', 'timbre', 'melody', 'rhythm'], default='timbre')
    q = sub.add_parser('search'); q.add_argument('--text', default=''); q.add_argument('--similar'); q.add_argument('--limit', type=int, default=12); q.add_argument('--semantic'); q.add_argument('--clap-model')
    q = sub.add_parser('correct'); q.add_argument('asset'); q.add_argument('--root-midi', type=float, required=True); q.add_argument('--reason', required=True)
    return p


def execute(args, store):
    if args.cmd == 'doctor':
        import shutil
        from .models import versions
        from .apple import capabilities as apple_capabilities
        return {'ffmpeg': find_tool('ffmpeg'), 'ffprobe': find_tool('ffprobe'), 'backends': capabilities(),
                'versions': versions(['librosa', 'numpy', 'torch', 'basic-pitch', 'beat-this', 'laion-clap']),
                'apple': apple_capabilities(), 'home': str(store.root)}, 0
    if args.cmd == 'inspect':
        asset = store.register(args.file)
        return {'asset': asset, 'suggested_plan': plan(asset)}, 0
    if args.cmd == 'analyze':
        options = {k: getattr(args, k) for k in ['beat_backend', 'pitch_backend', 'device', 'bpm', 'fmin', 'fmax',
                   'part', 'clap_model', 'tags', 'whisper_model', 'whisper_executable', 'separation_model',
                   'sounds_backend', 'speech_backend', 'notes_backend', 'locale', 'install_apple_assets']}
        if not 0 < args.fmin < args.fmax <= 11000:
            raise ValueError('音高范围要求 0 < fmin < fmax <= 11000')
        if args.bpm is not None and not 20 <= args.bpm <= 400:
            raise ValueError('BPM 应在 20–400 内')
        result = analyze(store, args.file, args.branch, args.tasks.split(',') if args.tasks else None, args.start, args.end, options)
        # Keep detailed arrays and model logs out of the default agent response.
        result['tasks'] = [{k: v for k, v in t.items() if k != 'summary'} | {'reason': t['summary'].get('reason')} for t in result['tasks']]
        return result, 0 if result['status'] == 'complete' else 2
    if args.cmd == 'index':
        root = args.root.expanduser().resolve()
        if not root.is_dir(): raise ValueError('目录不存在')
        if root == store.root or root in store.root.parents: raise ValueError('缓存目录必须在采样库外')
        files = sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink() and p.suffix.lower() in AUDIO_EXTENSIONS
                       and not any(x.startswith('.') for x in p.relative_to(root).parts))
        if args.limit is not None:
            if args.limit < 0: raise ValueError('limit 不可小于 0')
            files = files[:args.limit]
        ids = set(); failures = []
        for i, file in enumerate(files):
            try:
                asset = store.register(file, decode_audio=bool(args.tasks)); ids.add(asset['id'])
                if args.tasks:
                    result = analyze(store, asset['id'], tasks=args.tasks.split(','), options={'clap_model': args.clap_model} if args.clap_model else {})
                    if result['status'] != 'complete': failures.append({'path': str(file), 'status': 'partial'})
            except Exception as e:
                failures.append({'path': str(file), 'error': str(e)})
            if (i + 1) % 100 == 0: print(f'已索引 {i + 1}/{len(files)}', file=sys.stderr)
        return {'files': len(files), 'unique_audio_assets': len(ids), 'duplicate_paths': len(files) - len(ids) - len(failures),
                'failures': failures}, 2 if failures else 0
    if args.cmd == 'search':
        from .compare import search, semantic_search
        if args.semantic:
            if not args.clap_model: raise ValueError('--semantic 需要 --clap-model')
            return semantic_search(store, args.semantic, args.clap_model, max(0, args.limit)), 0
        return search(store, args.text, args.similar, max(0, args.limit)), 0
    if args.cmd == 'compare':
        from .compare import compare
        return compare(store, args.a, args.b, args.mode), 0
    asset_id = store.resolve(args.asset)
    if args.cmd == 'query':
        if args.view == 'summary': return summary(store, asset_id), 0
        if args.view == 'runs': return store.runs(asset_id), 0
        if args.view == 'evidence':
            if not args.event_id: raise ValueError('evidence 需要 --id')
            item = store.evidence(args.event_id)
            if item['asset_id'] != asset_id: raise ValueError('该事件不属于指定素材')
            return item, 0
        limit = max(1, min(args.limit, 1000))
        ev = store.events(asset_id, args.start, args.end, args.part, args.kind, limit + 1, max(0, args.offset), args.task)
        return {'events': ev[:limit], 'next_offset': args.offset + limit if len(ev) > limit else None}, 0
    if args.cmd == 'correct': return store.correct(asset_id, 'root_midi', args.root_midi, args.reason), 0
    from . import views
    if args.cmd == 'audio': return views.audio(store, asset_id, args.out, args.start, args.end, args.overlay), 0
    if args.cmd == 'render': return views.render(store, asset_id, args.out, args.start, args.end, args.layers.split(',')), 0
    if args.cmd == 'export': return views.export(store, asset_id, args.out), 0
    raise ValueError(args.cmd)


def main():
    args = parser().parse_args()
    store = None
    try:
        # Validate source/output separation before creating the SQLite file.
        if args.cmd in ('index', 'inspect', 'analyze'):
            import os
            cache = Path(args.home or os.environ.get('MUSIC_HOME', '~/.cache/music-understanding/data')).expanduser().resolve()
            source = Path(args.root if args.cmd == 'index' else args.file).expanduser().resolve()
            if source.exists():
                protected = source if source.is_dir() else source.parent
                if cache == protected or protected in cache.parents:
                    raise ValueError('缓存目录必须在采样库外')
        store = Store(args.home)
        with contextlib.redirect_stdout(sys.stderr):
            result, code = execute(args, store)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return code
    except (ValueError, RuntimeError, OSError) as e:
        print(json.dumps({'error': str(e)}, ensure_ascii=False))
        return 1
    finally:
        if store: store.close()


if __name__ == '__main__':
    raise SystemExit(main())
