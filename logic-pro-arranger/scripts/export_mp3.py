#!/usr/bin/env python3
"""把 Logic 并轨出来的 AIFF/WAV 转成发行用的文件，并量响度。需要 ffmpeg。

用法：
  python3 export_mp3.py 导出/歌名.aif --title 歌名            # → 导出/歌名.mp3（320k，44.1kHz，-1dB 余量）
  python3 export_mp3.py 导出/歌名.aif --title 歌名 --wav16    # 另出 44.1kHz/16bit WAV（平台上传用，带抖动）
  python3 export_mp3.py 导出/歌名.aif --measure                # 只量：整体响度 LUFS、真峰值、时长
  python3 export_mp3.py 导出/歌名.aif --title 歌名 --loudnorm -14   # 转码时把整体响度拉到 -14 LUFS（编码前真峰值目标 -1.5 dBTP；编码后以实测为准）

规则：输出已存在就不覆盖（发行过的母带不能被意外替换），要覆盖加 --force；原始 AIFF 永远保留当母带。
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True
from tool_support import find_tool, guard_out, output_path

RESAMPLE = "aresample=44100:resampler=swr:filter_size=64:phase_shift=10:cutoff=0.97"


def ffmpeg():
    exe = find_tool("ffmpeg")
    if not exe:
        sys.exit("没有 ffmpeg：brew install ffmpeg")
    return exe


def measure(path):
    r = subprocess.run([ffmpeg(), "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", tail)
    lra = re.search(r"LRA:\s+([\d.]+) LU", tail)
    tp = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", tail)
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    secs = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3)) if dur else None
    return {"lufs": float(i.group(1)) if i else None, "lra": float(lra.group(1)) if lra else None,
            "true_peak": tp.group(1) if tp else None, "seconds": secs}


def show(path):
    m = measure(path)
    d = f"{int(m['seconds'] // 60)}:{m['seconds'] % 60:04.1f}" if m["seconds"] else "?"
    print(f"  {os.path.basename(path)}：{m['lufs']} LUFS（整体）  LRA {m['lra']} LU  真峰值 {m['true_peak']} dBTP  时长 {d}")
    if m["true_peak"] not in (None, "-inf") and float(m["true_peak"]) > -1.0:
        print("  ⚠ 真峰值高于 -1 dBTP，转码后可能削波；可以加大 --gain 的余量")


def loudnorm_filter(src, target):
    """两遍 loudnorm：第一遍量，第二遍按实测值线性拉到目标。"""
    r = subprocess.run([ffmpeg(), "-hide_banner", "-nostats", "-i", src, "-af",
                        f"loudnorm=I={target}:TP=-1.5:LRA=20:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    js = r.stderr[r.stderr.rfind("{"):r.stderr.rfind("}") + 1]
    import json
    d = json.loads(js)
    best = float(d["input_i"]) + (-1.5 - float(d["input_tp"]))
    print(f"  实测 {d['input_i']} LUFS / 真峰值 {d['input_tp']} dBTP → 纯线性（不压动态）最多到 {best:.1f} LUFS")
    return (f"loudnorm=I={target}:TP=-1.5:LRA=20:measured_I={d['input_i']}:measured_TP={d['input_tp']}:"
            f"measured_LRA={d['input_lra']}:measured_thresh={d['input_thresh']}:offset={d['target_offset']}:linear=true"
            f":print_format=json")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit("ffmpeg 失败：\n" + r.stderr[-2000:])
    if '"normalization_type" : "dynamic"' in r.stderr:
        print("  ⚠ 线性增益会超过真峰值上限，loudnorm 改用了动态模式（压了一点动态）；不想要就把目标设到上面那个「纯线性最多」的值")


def main():
    ap = argparse.ArgumentParser(description="AIFF/WAV → MP3 320k（可选 44.1/16 WAV），并量响度")
    ap.add_argument("src")
    ap.add_argument("--title")
    ap.add_argument("--artist")
    ap.add_argument("--out", help="MP3 输出路径（默认与源文件同目录同名 .mp3）")
    ap.add_argument("--gain", type=float, default=-1.0, help="转码前的增益 dB（默认 -1，留余量防削波）")
    ap.add_argument("--wav16", action="store_true", help="另出 44.1kHz/16bit WAV（三角抖动），写在 MP3 旁：<MP3 名>_44k16.wav")
    ap.add_argument("--measure", action="store_true", help="只量响度，不转码")
    ap.add_argument("--loudnorm", type=float, help="把整体响度拉到这个 LUFS（如 -14），代替 --gain")
    ap.add_argument("--force", action="store_true", help="允许覆盖已存在的输出")
    ap.epilog = "依赖：ffmpeg（含 libmp3lame）；Python 仅用标准库。"
    a = ap.parse_args()
    if not os.path.exists(a.src):
        sys.exit(f"找不到：{a.src}")
    if a.measure:
        show(a.src)
        return
    mp3 = a.out or os.path.splitext(a.src)[0] + ".mp3"
    # WAV 跟随 MP3 的目录与文件名：未给 --out 时仍在源文件旁。
    outs = [mp3] + ([os.path.splitext(mp3)[0] + "_44k16.wav"] if a.wav16 else [])
    for o in outs:
        guard_out(o, a.src, *(p for p in outs if p != o), force=a.force)
    meta = []
    if a.title:
        meta += ["-metadata", f"title={a.title}"]
    if a.artist:
        meta += ["-metadata", f"artist={a.artist}"]
    pre = f"{loudnorm_filter(a.src, a.loudnorm)}," if a.loudnorm is not None else f"volume={a.gain}dB,"
    with output_path(mp3, a.src, force=a.force) as temp:
        run([ffmpeg(), "-hide_banner", "-y", "-i", a.src, "-af", f"{pre}{RESAMPLE}",
             "-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3", *meta, "-f", "mp3", str(temp)])
    print(f"写出 {mp3}")
    if a.wav16:
        with output_path(outs[1], a.src, force=a.force) as temp:
            run([ffmpeg(), "-hide_banner", "-y", "-i", a.src, "-af",
                 f"{pre}{RESAMPLE}:osf=s16:dither_method=triangular", "-c:a", "pcm_s16le", str(temp)])
        print(f"写出 {outs[1]}")
    print("响度：")
    show(a.src)
    for o in outs:
        show(o)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as exc:
        sys.exit(f"错误：{exc}")
