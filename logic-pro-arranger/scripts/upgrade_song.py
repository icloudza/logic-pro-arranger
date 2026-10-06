#!/usr/bin/env python3
"""将内嵌 logicsong 更新到当前版本，另存脚本，保留 SPEC 与 compose。"""
import argparse
import ast
from pathlib import Path
import sys
sys.dont_write_bytecode = True
import re
import uuid
from tool_support import write_text, embedded_library
from logicsong import LIB_VERSION

# 这些版本的人味抖动按绝对位置定位；升级后保留，才能重新生成出与原作相同的 MIDI。
ABSOLUTE_JITTER_VERSIONS = (None, "0.2.0")
ANCHOR_LINE = re.compile(r'^_JITTER_ANCHOR\s*=.*\n?', re.M)


def jitter_anchor(source, choice):
    """返回升级后应写入的抖动定位；None 表示使用库默认（section）。"""
    if choice == 'section':
        return None
    current = ANCHOR_LINE.search(source)
    if current:
        return ast.literal_eval(current.group(0).split('=', 1)[1].strip())
    version = re.search(r'''^LIB_VERSION\s*=\s*["']([^"']+)["']''', source, re.M)
    return 'absolute' if (version.group(1) if version else None) in ABSOLUTE_JITTER_VERSIONS else None


def upgrade(source, anchor_choice='auto'):
    ast.parse(source)
    anchor = jitter_anchor(source, anchor_choice)
    source = ANCHOR_LINE.sub('', source)
    start, end = '# ==== LIB START ====', '# ==== LIB END ===='
    body = embedded_library()
    if start in source and end in source:
        before, rest = source.split(start, 1)
        _, after = rest.split(end, 1)
    else:
        marker = re.search(r'^# .*?(?:内嵌工具库|工具库（来自 logic-pro-arranger/scripts/logicsong.py).*$', source, re.M)
        tree = ast.parse(source)
        entry = next((n for n in tree.body if isinstance(n, ast.If) and
                      isinstance(n.test, ast.Compare) and isinstance(n.test.left, ast.Name) and
                      n.test.left.id == '__name__'), None)
        if not marker or not entry or not any(isinstance(n, ast.FunctionDef) and n.name == 'compose' for n in tree.body):
            raise ValueError('不是可识别的 logicsong 内嵌脚本；需人工迁移自定义生成器，未写文件')
        boundary = sum(len(x) for x in source.splitlines(keepends=True)[:entry.lineno-1])
        if boundary <= marker.end():
            raise ValueError('内嵌库边界无效')
        before, after = source[:marker.start()], source[boundary:]
    updated = before + start + '\n' + body + '\n' + end + '\n' + after
    tree = ast.parse(updated)
    if not any(isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "_SONG_BUILD_ID" for t in n.targets) for n in tree.body):
        at = updated.index(start)
        updated = updated[:at] + f"_SONG_BUILD_ID = {uuid.uuid4().hex!r}\n" + updated[at:]
    if anchor:
        at = updated.index(start)
        updated = updated[:at] + f"_JITTER_ANCHOR = {anchor!r}\n" + updated[at:]
    ast.parse(updated)
    return updated


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('script', type=Path)
    ap.add_argument('--out', type=Path, required=True, help='新脚本路径；不覆盖原脚本或已有文件')
    ap.add_argument('--jitter-anchor', choices=['auto', 'section'], default='auto',
                    help='auto：旧版脚本保留绝对位置抖动，重新生成与原 MIDI 相同；'
                         'section：改用段内定位，人味抖动会变化一次，之后插删段落不影响其他段')
    args = ap.parse_args()
    source = args.script.read_text(encoding='utf-8')
    anchor = jitter_anchor(source, args.jitter_anchor)
    write_text(args.out, upgrade(source, args.jitter_anchor), args.script)
    print(f'{args.out}：logicsong {LIB_VERSION}；原脚本保留，重新生成并检查 MIDI 后再采用')
    if anchor == 'absolute':
        print('  保留旧版人味抖动定位，重新生成应与原 MIDI 一致；之后插删段落会改变其后各段的抖动。'
              '需要段内定位时用 --jitter-anchor section 另存，并以那次新生成的 MIDI 作为锁定对照。')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, SyntaxError) as exc:
        sys.exit(f'错误：{exc}')
