#!/usr/bin/env python3
"""建立可重现的 MIDI 草稿；format 仅为标签，不预设曲式、音色候选或时长。

  python3 new_song.py --title 草稿 --key 'F minor' --bpm 88 \
      --sections '主题:6:4:Fm | Db,回应:5:5:Bbm | C7'
不指定 sections 时建一个空白 8 小节工作区。已有文件默认不覆盖。
"""
import argparse
import math
import os
import pprint
import uuid
import re
import sys
sys.dont_write_bytecode = True
from tool_support import write_text, embedded_library

HERE = os.path.dirname(os.path.abspath(__file__))
FORMATS = ('custom', 'douyin-clip', 'full-song', 'speech-underscore', 'remix-variant')


def build(title, fmt, style, key, bpm, lib_code=None, sections=None, meter=(4, 4), target=None):
    """创建空白 SPEC 与独立生成脚本。"""
    if not math.isfinite(bpm) or not 4 <= bpm <= 999:
        raise ValueError('BPM 必须是 4–999 的有限数')
    if len(meter) != 2 or not 1 <= meter[0] <= 32 or meter[1] not in (1, 2, 4, 8, 16, 32):
        raise ValueError('拍号需为正数，分母为 1/2/4/8/16/32')
    if target and (len(target) != 2 or not all(math.isfinite(x) for x in target) or not 0 < target[0] <= target[1]):
        raise ValueError('目标时长需为正数区间：下限,上限')
    sections = sections if sections is not None else [('草稿', 8, 5, '')]
    if not sections or any(not n.strip() or b < 1 or not 0 <= e <= 10 for n, b, e, c in sections):
        raise ValueError('段落名不能为空，小节数需为正，能量需在 0–10')
    if len({n for n, _, _, _ in sections}) != len(sections):
        raise ValueError('段落名不能重复')
    spec = {
        'title': title, 'format': fmt, 'style': style, 'bpm': bpm,
        'meter': list(meter), 'key': key, 'seed': 20261006,
        'sections': [dict(name=n, bars=b, energy=e, chords=c) for n, b, e, c in sections],
        'tracks': [dict(id='lead', name='主奏', role='主旋律', program=0, channel=1)],
        'hooks': {}, 'locked': [],
    }
    if target:
        spec['target_seconds'] = list(target)
    # 验证和弦与拍号，避免写出不能运行的骨架。
    sys.path.insert(0, HERE)
    import logicsong as L
    L.Song(spec)
    build.notes = ['空白工作区；按材料修改段落、添加声部。format 不设置音乐规则。']
    lib_code = embedded_library(lib_code)
    body = pprint.pformat(spec, sort_dicts=False, width=100)
    return f'''#!/usr/bin/env python3
# 生成：python3 本脚本.py [--out 输出目录]
# SPEC 是可编辑的工程参数。空和弦表示尚未配和声，不自动填主和弦。
_SONG_BUILD_ID = {uuid.uuid4().hex!r}
SPEC = {body}


def compose(song):
    # 在这里写音乐；以下只是 API 说明，不自动生成旋律：
    # lead = song.track('lead')
    # lead.line(song.start('段名'), 0, 'C5:1 E5:1 G5:2')
    # t.chord(bar, beat, dur, 'Am7', vel=65, low=48, high=72, prev=previous)
    # 可选 hook_candidates/多轨/对照试听见 references/song-spec.md。
    pass


# 内嵌工具库，生成脚本可独立运行。
# ==== LIB START ====
{lib_code}
# ==== LIB END ====

if __name__ == '__main__':
    main(SPEC, compose, __file__)
'''


def main():
    ap = argparse.ArgumentParser(description='创建空白 MIDI 草稿；不预设曲式')
    ap.add_argument('--title', required=True)
    ap.add_argument('--format', default='custom', choices=FORMATS, help='用途标签，不改变结构')
    ap.add_argument('--style', default='', help='可选风格说明或参考资料 id')
    ap.add_argument('--key', required=True)
    ap.add_argument('--bpm', type=float, required=True, help='四分音符 BPM')
    ap.add_argument('--meter', default='4/4', help='如 3/4、6/8；全曲一个拍号')
    ap.add_argument('--sections', help='名:小节[:能量[:和弦]]，逗号分段；不自动伸缩')
    ap.add_argument('--target-seconds', help='可选时长范围，例如 40,50；只检查，不重排结构')
    ap.add_argument('--root', help='输出根目录；默认 ~/Documents/Logic，若不存在用当前目录')
    ap.add_argument('--force', action='store_true', help='覆盖同名生成脚本')
    a = ap.parse_args()
    if not a.title.strip() or a.title in ('.', '..') or re.search(r'[/\\:*?"<>|\x00-\x1f]', a.title):
        ap.error('歌名为空或包含路径/控制字符')
    sys.path.insert(0, HERE)
    sys.dont_write_bytecode = True
    import logicsong as L
    try:
        L.parse_key(a.key)
        meter = tuple(int(x) for x in a.meter.split('/'))
        target = [float(x) for x in a.target_seconds.split(',')] if a.target_seconds else None
        sections = None
        if a.sections is not None:
            sections = []
            for item in a.sections.split(','):
                ps = item.strip().split(':', 3)
                if len(ps) < 2:
                    raise ValueError('段落写法：名:小节[:能量[:和弦]]')
                try:
                    bars = int(ps[1])
                    energy = int(ps[2]) if len(ps) > 2 and ps[2] else 5
                except ValueError:
                    raise ValueError(f'段落“{item.strip()}”：写法是 名:小节[:能量[:和弦]]，小节和能量（0–10）是整数，'
                                     f'和弦写在第 4 项，例如 {ps[0].strip()}:4:5:Am | F') from None
                sections.append((ps[0].strip(), bars, energy, ps[3] if len(ps) > 3 else ''))
        code = build(a.title, a.format, a.style, a.key, a.bpm, None, sections, meter, target)
    except (ValueError, TypeError, IndexError) as e:
        ap.error(str(e))
    root = a.root or os.path.expanduser('~/Documents/Logic')
    if not a.root and not os.path.isdir(root):
        root = os.getcwd()
    folder = os.path.join(root, a.title)
    path = os.path.join(folder, a.title + '_生成脚本.py')
    created = not os.path.exists(folder)
    os.makedirs(folder, exist_ok=True)
    try:
        write_text(path, code, force=a.force)
    except OSError as exc:
        if created:
            try:
                os.rmdir(folder)
            except OSError:
                pass
        ap.error(str(exc))
    print(f'已建：{path}\n下一步：按作品写 compose()，添加需要的声部，再生成 MIDI 和试听。')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError) as exc:
        sys.exit(f"错误：{exc}")
