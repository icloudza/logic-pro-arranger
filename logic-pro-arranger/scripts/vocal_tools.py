#!/usr/bin/env python3
"""vocal_tools.py —— 人声分轨工具：测量、变速对齐、按和弦挑字切片、让切片唱旋律。

人声分析、对齐与切片
  info       人声能不能改音高？测有声帧比例、中位音高、音域、字头数量
  align      把人声变速到目标 BPM（保持音高与共振峰），补静音让原第一个强拍落在指定小节
  chops      按和弦音匹配度挑字，按节奏型摆成人声切片（可口吃重复、结尾拖长、整体移调）
  find-loops 在 Logic 自带 Apple Loops 里找人声类循环
  syllables  自动找"音高稳定的字"：起止时间、中位音高、稳定度、响度
  sing       用这些字移调/拉长，唱出给定旋律（--notes 或 MIDI 文件）

依赖
  Python 3.9+、numpy；与 analyze_audio.py 放在同一目录（共用解码/写 WAV/onset 函数）
  ffmpeg/ffprobe：解码任何格式（缺失时只能读 WAV）
  rubberband：变速/移调（-3 -F，保持音高与共振峰）。缺失时 align/sing/--repitch 直接报错，
              不用会改变音高的方法凑合
  aubioonset（可选）：字头检测；缺失时退回 numpy 谱通量找峰
  sox（可选）：高通/混响/限幅效果链；缺失时只输出干声

用法示例
  python3 vocal_tools.py info 人声.wav
  python3 vocal_tools.py align --src 人声.wav --src-bpm 133.83 --downbeat 0.663 --keep-end 16.16 \\
          --bpm 126 --at-bar 5 --total-bars 18 --out 人声_126.wav
  python3 vocal_tools.py chops --src 人声_126.wav --bpm 126 --chords "14:F 15:G 16:Am 17:Am" \\
          --stutter-bar 17 --stutter "2.5-3.5:0.25" --tail-chord Am --tail-bar 18 --out 切片.wav --verbose
  python3 vocal_tools.py find-loops chop
  python3 vocal_tools.py syllables "Timeless Chop Vox 01.caf" --json 字表.json
  python3 vocal_tools.py sing --src "Timeless Chop Vox 01.caf" --syllables 字表.json --bpm 104 \\
          --notes "1:0:0.5:E5 1:0.5:0.5:D#5 1:1:1:C#5" --riser-bar 4 --out 切片旋律.wav
所有输出都与第 1 小节对齐（在 Logic 里拖到第 1 小节开头）。输出不能覆盖输入文件。
"""
import argparse
import json
import math
import os
import re
import struct
import subprocess
import sys
sys.dont_write_bytecode = True
import tempfile
import warnings
from concurrent.futures import ThreadPoolExecutor

from tool_support import LazyNumpy, write_text
np = LazyNumpy()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True            # 不在 skill 目录里留 __pycache__
from analyze_audio import (NOTE, ZhHelp, db, die, find_tool, guard_out, load_audio, note_name,  # noqa: E402
                           onset_env, pick_peaks, resample, stft_mag, write_wav, zh_parser)

ASR = 22050                               # 音高/字头分析用的采样率
CHOP_PATTERN = "0:0.6,0.75:0.6,1.5:0.8,2.5:0.45,3:0.45,3.5:0.5"         # 参考实现的 3-3-2 节奏
CHOP_FX = "highpass=150 reverb=40,50,70,100,10,-3 limit=7"
SING_FX = "highpass=180 reverb=50,50,90,100,20,-2 norm=-2"
LOOP_DIRS = ["~/Music/Logic Pro Library.bundle/Apple Loops", "/Library/Audio/Apple Loops",
             "~/Library/Audio/Apple Loops"]


# ================================================================ 小工具
def pos(t, bpm, bpb=4):
    """秒 → "第 n 小节第 b 拍"（从第 1 小节开头算）。"""
    beats = round(t * bpm / 60, 6)                              # 去掉浮点误差（3.9999999 拍）
    return f"第 {int(beats // bpb) + 1} 小节第 {beats % bpb + 1:.2f} 拍"


def envelope(n, sr, fin=0.004, fout=0.03):
    env = np.ones(n, np.float32)
    a, b = min(n // 2, int(fin * sr)), min(n // 2, int(fout * sr))
    if a:
        env[:a] = np.linspace(0, 1, a)
    if b:
        env[-b:] = np.linspace(1, 0, b)
    return env


def rms(x):
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0


def parse_note(s):
    from logicsong import nn
    return nn(int(s) if str(s).strip().isdigit() else s)


def parse_chord(name):
    from logicsong import parse_chord as parse
    root, intervals, bass = parse(name)
    tones = list(dict.fromkeys((root + i) % 12 for i in intervals))
    return tones + ([bass] if bass is not None and bass not in tones else [])


def out_length(content_end, bar_s, total_bars, tail):
    base = total_bars * bar_s if total_bars else content_end
    if total_bars and content_end > base + 1e-3:
        print(f"注意：内容超出第 {total_bars} 小节结尾 {content_end - base:.2f} s，没有截断")
    return max(base + tail, content_end)


def assemble(pieces, sr, length_s, channels):
    """把 (起始秒, 片段) 叠加到一条从第 1 小节开头算起的轨道上。"""
    n = max([int(round(length_s * sr))] + [int(t * sr) + len(p) for t, p in pieces])
    mix = np.zeros((n, channels), np.float32)
    for t, p in pieces:
        i0 = int(t * sr)
        k = min(len(p), len(mix) - i0)
        if k > 0:
            mix[i0:i0 + k] += p[:k] if p.shape[1] == channels else np.repeat(p[:k, :1], channels, axis=1)
    return mix


# ================================================================ 外部命令：rubberband / sox / aubio
def rubberband(x, sr, args, tmp):
    """用 rubberband 处理内存中的音频（中间文件为 32 位浮点 WAV）。不支持 -3 的旧版本退回 R2 引擎。"""
    rb = find_tool("rubberband")
    if not rb:
        die("缺少 rubberband：变速/移调必须保持音高和共振峰，本工具不提供会改变音高的降级方案。"
            "请先安装：brew install rubberband")
    src, dst = os.path.join(tmp, "rb_in.wav"), os.path.join(tmp, "rb_out.wav")
    write_wav(src, x, sr, 32, force=True)
    r = subprocess.run([rb] + args + [src, dst], capture_output=True, text=True)
    if r.returncode and "-3" in args:
        print("提示：这个 rubberband 不支持 -3（R3 引擎），改用 R2 引擎", file=sys.stderr)
        r = subprocess.run([rb] + [a for a in args if a != "-3"] + [src, dst], capture_output=True, text=True)
    if r.returncode:
        die(f"rubberband 失败：{r.stderr.strip()[-400:]}")
    return load_audio(dst)[1]


def sox_effects(spec):
    """--fx 小语言 → sox 效果参数。highpass=Hz lowpass=Hz reverb=R[,HF,房间,立体声,预延迟ms,湿声dB]
    limit=dB(gain -l) norm=dB(gain -n) gain=dB；none 表示不加效果。"""
    if spec.strip().lower() in ("", "none", "off", "dry"):
        return []
    eff = []
    for item in spec.split():
        k, _, val = item.partition("=")
        v = [s for s in val.split(",") if s]
        if not v:
            die(f"--fx 参数缺少数值：{item}")
        if k in ("highpass", "lowpass"):
            eff += [k, v[0]]
        elif k == "reverb":
            eff += ["reverb"] + (v if len(v) > 1 else v + ["50", "70", "100", "10", "-3"])
        elif k in ("limit", "norm", "gain"):
            eff += ["gain"] + {"limit": ["-l"], "norm": ["-n"], "gain": []}[k] + [v[0]]
        else:
            die(f"--fx 不认识 {k}；可用 highpass= lowpass= reverb= limit= norm= gain=，或 none")
    return eff


def finish(dry, sr, fx, out, tmp):
    """加效果并写 24-bit WAV。sox 可用时走效果链；不可用时只写干声（峰值压到 -1 dBFS）。"""
    eff, sox = sox_effects(fx), find_tool("sox")
    if eff and sox:
        d = os.path.join(tmp, "dry.wav")
        write_wav(d, dry, sr, 32)
        r = subprocess.run([sox, d, "-b", "24", out] + eff, capture_output=True, text=True)
        if r.returncode:
            die(f"sox 效果链失败：{r.stderr.strip()[-400:]}")
        return "sox " + " ".join(eff)
    if eff:
        print("提示：没有找到 sox，只输出干声（没有高通/混响/限幅）。brew install sox 后重跑即可带效果。")
    pk = float(np.abs(dry).max()) if len(dry) else 0.0
    write_wav(out, dry * (min(1.0, 10 ** (-1 / 20) / pk) if pk > 0 else 1.0), sr, 24)
    return "干声（未加效果）"


def detect_onsets(mono, sr):
    """字头：优先 aubioonset（参数同参考实现：hfc、阈值 0.3、静音 -55 dB、最小间隔 0.09 s，
    在 22.05 kHz 单声道上跑）；没有 aubio 时用 numpy 谱通量找峰。返回 (秒列表, 方法)。"""
    y = resample(np.asarray(mono, np.float32), sr, ASR)
    exe = find_tool("aubioonset")
    if exe:
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "mono.wav")
            write_wav(p, y, ASR, 16)
            r = subprocess.run([exe, "-i", p, "-O", "hfc", "-t", "0.3", "-s", "-55", "-M", "0.09"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                return [float(v) for v in r.stdout.split() if re.fullmatch(r"[\d.]+", v)], "aubioonset"
    hop = 256
    S = stft_mag(y, 1024, hop)
    frame_db = 10 * np.log10(np.convolve(y.astype(np.float64) ** 2, np.ones(1024) / 1024, "same")[::hop] + 1e-12)
    pk = pick_peaks(onset_env(S, ASR), ASR / hop, min_gap=0.09, delta=0.3)
    return ([i * hop / ASR for i in pk if frame_db[min(i, len(frame_db) - 1)] > -55],
            "numpy 谱通量（没有 aubioonset 时的降级方案）")


# ================================================================ 基频（简化 YIN）
def yin(y, sr=ASR, fmin=32.0, fmax=1000.0, hop=256, W=1024, thr=0.1):
    """简化 YIN：差分函数 → 累积均值归一化（CMNDF）→ 第一个低于阈值的谷 + 抛物线插值。
    返回 (f0 Hz, 非周期度 0–1, 帧 RMS, 帧中心秒)。非周期度越小音高越清晰（判定阈值见 voicing）。"""
    tmax, tmin = int(sr / fmin), max(2, int(sr / fmax))
    L = W + tmax
    y = np.pad(np.asarray(y, np.float32), (0, max(0, L - len(y))))
    n = 1 + (len(y) - L) // hop
    nfft = 1 << int(math.ceil(math.log2(2 * L)))
    tau = np.arange(tmax + 1)
    f0, ap, fr_rms = np.zeros(n), np.ones(n), np.zeros(n)
    for s in range(0, n, 512):
        m = min(512, n - s)
        fr = np.lib.stride_tricks.as_strided(y[s * hop:], (m, L), (y.strides[0] * hop, y.strides[0]))
        r = np.fft.irfft(np.conj(np.fft.rfft(fr[:, :W], nfft)) * np.fft.rfft(fr, nfft), nfft)[:, :tmax + 1]
        cs = np.concatenate([np.zeros((m, 1)), np.cumsum(fr.astype(np.float64) ** 2, axis=1)], axis=1)
        d = cs[:, W:W + 1] + (cs[:, tau + W] - cs[:, tau]) - 2 * r
        d[:, 0] = 0
        cmn = np.ones_like(d)
        cmn[:, 1:] = d[:, 1:] * tau[1:] / (np.cumsum(d[:, 1:], axis=1) + 1e-12)
        sub = cmn[:, tmin:]
        below = sub < thr
        i = np.where(below.any(axis=1), below.argmax(axis=1), sub.argmin(axis=1))
        rows = np.arange(m)
        for _ in range(tmax):                                   # 顺坡走到谷底
            nxt = np.minimum(i + 1, sub.shape[1] - 1)
            move = sub[rows, nxt] < sub[rows, i]
            if not move.any():
                break
            i = np.where(move, nxt, i)
        a, b, c = sub[rows, np.maximum(i - 1, 0)], sub[rows, i], sub[rows, np.minimum(i + 1, sub.shape[1] - 1)]
        off = np.clip(0.5 * (a - c) / (a - 2 * b + c - 1e-12), -0.5, 0.5)
        f0[s:s + m] = sr / (i + tmin + off)
        ap[s:s + m] = np.clip(b, 0, 1)
        fr_rms[s:s + m] = np.sqrt(cs[:, W] / W)
    return f0, ap, fr_rms, (np.arange(n) * hop + W / 2) / sr


def voicing(y, sr=ASR, thr=0.25):
    """逐帧：基频、是否有声音（高于最响处 -25 dB）、是否清晰有声（非周期度 < thr 且 32–1000 Hz）。
    thr 是周期性阈值，不能单独判断人声或乐器。"""
    f0, ap, fr_rms, t = yin(y, sr)
    active = fr_rms > max(np.percentile(fr_rms, 99) * 10 ** (-25 / 20), 1e-4)
    clear = active & (ap < thr) & (f0 >= 32) & (f0 < 1000)
    return f0, ap, fr_rms, t, active, clear


# ================================================================ info
def cmd_info(a):
    sr, x = load_audio(a.file)
    y = resample(x.mean(axis=1), sr, ASR)
    f0, ap, fr_rms, t, active, clear = voicing(y)
    act_s = max(active.sum() * 256 / ASR, 1e-3)
    ratio = clear.sum() / max(1, active.sum())
    syl = find_syllables(x, sr)
    stable = min(1.0, sum(s["dur"] for s in syl) / act_s)
    ons, method = detect_onsets(y, ASR)
    print(f"==== 人声测量：{os.path.basename(a.file)} ====")
    print(f"时长 {len(x) / sr:.2f} s | {sr} Hz | {x.shape[1]} 声道 | 峰值 {db(np.abs(x).max()):.1f} dBFS")
    print(f"有声音的帧（高于最响处 -25 dB）{active.mean() * 100:.0f}%，约 {act_s:.1f} s")
    print(f"清晰有声帧比例 {ratio * 100:.0f}%（YIN 非周期度 < 0.25；干净的 Apple Loops 人声约 65–70%，"
          f"多层叠唱/说话常在 30% 以下）")
    if clear.sum() >= 5:
        midi = 69 + 12 * np.log2(f0[clear] / 440)
        med, lo, hi = np.median(midi), np.percentile(midi, 5), np.percentile(midi, 95)
        print(f"中位音高 {note_name(med)}（MIDI {med:.2f}）| 音域 {note_name(lo)}–{note_name(hi)}"
              f"（MIDI {lo:.1f}–{hi:.1f}，5%–95% 分位，C4=60）")
    else:
        print("几乎没有清晰有声的帧，测不出可靠的音高")
    print(f"音高稳定的字 {len(syl)} 个，覆盖有声音时长的 {stable * 100:.0f}%（标准差 < 30 音分、≥ 0.12 s；"
          f"详见 syllables 子命令）")
    print(f"字头 {len(ons)} 个（{method}），约 {len(ons) / act_s:.1f} 个/秒（按有声音的时长算）")
    if ratio >= 0.5 and stable >= 0.45:
        tip = "适合改音高切片：音高清晰稳定，可以用 chops --repitch 或 syllables + sing 移调（仍建议让用户试听）。"
    elif ratio < 0.35 or stable < 0.3:
        tip = (f"不适合改音高：清晰有声 {ratio * 100:.0f}%、稳定的字只覆盖 {stable * 100:.0f}%（多层人声叠在一起、"
               "说话、气声或混响重），音高不稳。切片保持原调，用 chops 按和弦音匹配挑字；"
               "要让切片唱旋律，改用 Apple Loops 人声（find-loops / syllables / sing）。")
    else:
        tip = "勉强：小幅移调（±2 半音内）也许可用，务必让用户试听；更稳的是 chops 按和弦挑字、不改音高。"
    print("建议：" + tip)


# ================================================================ align
def do_align(src, src_bpm, downbeat, bpm, at_bar, keep_start, keep_end, bpb, tmp):
    """截取 → rubberband 变速（ratio = src_bpm / bpm）→ 计算前置静音。返回 (sr, 片段, lead 秒, ratio, 小节秒)。"""
    sr, x = load_audio(src)
    dur = len(x) / sr
    ks, ke = keep_start or 0.0, dur if keep_end is None else min(keep_end, dur)
    if ke - ks < 0.1:
        die(f"保留段太短：{ks:.3f}–{ke:.3f} s")
    seg = x[int(round(ks * sr)):int(round(ke * sr))].copy()
    seg *= envelope(len(seg), sr, 0.005 if ks > 0 else 0, 0.1 if ke < dur - 1e-3 else 0)[:, None]
    ratio = src_bpm / bpm
    if abs(ratio - 1) > 1e-6:
        seg = rubberband(seg, sr, ["-3", "-F", "-t", f"{ratio:.6f}"], tmp)
    bar_s = bpb * 60 / bpm
    off = (downbeat - ks) * ratio
    lead = (at_bar - 1) * bar_s - off
    if lead < 0:
        die(f"原第一个强拍在拉伸后距保留段起点 {off:.3f} s，放到第 {at_bar} 小节（{(at_bar - 1) * bar_s:.3f} s）"
            f"需要的前置静音是负数（{lead:.3f} s），也就是强拍会落在第 1 小节之前。\n"
            f"请把 --at-bar 至少设为 {math.ceil(off / bar_s) + 1}，或加大 --keep-start 去掉强拍前的部分。")
    return sr, seg, lead, ratio, bar_s, ke


def cmd_align(a):
    guard_out(a.out, a.src)
    with tempfile.TemporaryDirectory() as tmp:
        sr, seg, lead, ratio, bar_s, ke = do_align(a.src, a.src_bpm, a.downbeat, a.bpm, a.at_bar, a.keep_start,
                                                   a.keep_end, a.beats_per_bar, tmp)
    end = lead + len(seg) / sr
    out = assemble([(lead, seg)], sr, out_length(end, bar_s, a.total_bars, a.tail), seg.shape[1])
    pk = float(np.abs(out).max())
    gain = min(1.0, 10 ** (-1 / 20) / pk) if pk > 0 else 1.0
    write_wav(a.out, out * gain, sr, 24)
    ks = a.keep_start or 0.0
    word = f"放慢 {ratio:.4f} 倍" if ratio > 1 else (f"加快 {1 / ratio:.4f} 倍" if ratio < 1 else "不变速")
    print(f"源：{a.src}（保留 {ks:.3f}–{ke:.3f} s，{ke - ks:.3f} s → 拉伸后 {len(seg) / sr:.3f} s）")
    how = "rubberband -3 -F，音高与共振峰不变" if abs(ratio - 1) > 1e-6 else "没有调用 rubberband"
    print(f"速度：{a.src_bpm} → {a.bpm} BPM，{word}（时长 ×{ratio:.4f}；{how}）")
    print(f"原第一个强拍：源 {a.downbeat:.3f} s → 拉伸后距保留段起点 {(a.downbeat - ks) * ratio:.3f} s")
    print(f"前置静音 lead = ({a.at_bar}-1) × {bar_s:.4f} − {(a.downbeat - ks) * ratio:.4f} = {lead:.4f} s")
    print(f"对齐结果：原第一个强拍在 {(a.at_bar - 1) * bar_s:.3f} s = 第 {a.at_bar} 小节第 1 拍；人声区域 "
          f"{lead:.3f} s（{pos(lead, a.bpm, a.beats_per_bar)}）→ {end:.3f} s（{pos(end, a.bpm, a.beats_per_bar)}）")
    print(f"输出：{a.out}  {len(out) / sr:.3f} s，{sr} Hz，{out.shape[1]} 声道，24-bit，"
          f"峰值 {db(pk * gain):.1f} dBFS（增益 {db(gain):+.1f} dB）")
    print(f"用法：Logic 项目速度设为 {a.bpm} BPM（{a.beats_per_bar}/4 拍），把文件拖到第 1 小节开头。")


# ================================================================ chops
def parse_chords(spec):
    out = {}
    for tok in spec.replace(",", " ").split():
        b, _, c = tok.partition(":")
        if not b.isdigit() or not c:
            die(f"--chords 格式是 小节:和弦，例如 \"14:F 15:G 16:Am\"，这里有问题：{tok}")
        parse_chord(c)
        out[int(b)] = c
    return out


def parse_slots(spec, what="--pattern"):
    try:
        return [tuple(float(v) for v in item.split(":")) for item in spec.split(",") if item.strip()]
    except ValueError:
        die(f"{what} 格式是 拍位:拍长,拍位:拍长…，例如 \"{CHOP_PATTERN}\"")


def parse_stutter(spec):
    out = []
    for item in spec.split(","):
        m = re.fullmatch(r"\s*([\d.]+)-([\d.]+):([\d.]+)\s*", item)
        if not m or float(m.group(3)) <= 0:
            die(f"--stutter 格式是 起拍-止拍:间隔拍，例如 \"2.5-3.5:0.25\"，这里有问题：{item}")
        out.append(tuple(float(v) for v in m.groups()))
    return out


def bar_slots(pattern, stutters):
    """一个小节的槽位：(拍位, 拍长, 是否口吃)。口吃区间内的原槽位被 step 间隔的重复替换（长度 step×0.88）。"""
    slots = [(b, d, False) for b, d in pattern if not any(lo <= b < hi for lo, hi, _ in stutters)]
    for lo, hi, step in stutters:
        slots += [(b, step * 0.88, True) for b in np.arange(lo, hi - 1e-9, step)]
    return sorted(slots)


class ChromaTool:
    """参考实现的字头 chroma：字头后 0.02–0.21 s 每 30 ms 一帧（4096 点），150–1500 Hz 局部峰的能量归到音级。"""

    def __init__(self, mono, sr):
        self.mono, self.sr = mono, sr
        self.n = 4096 if sr <= 50000 else 8192
        self.win = np.hanning(self.n)
        f = np.fft.rfftfreq(self.n, 1 / sr)
        self.band = np.where((f > 150) & (f < 1500))[0]
        self.pcs = np.round(69 + 12 * np.log2(f[self.band] / 440)).astype(int) % 12

    def __call__(self, o, dur=0.3):
        ch = np.zeros(12)
        for t in np.arange(o + 0.02, o + dur - 0.09, 0.03):
            fr = self.mono[int(t * self.sr): int(t * self.sr) + self.n]
            if len(fr) < self.n:
                break
            P = np.abs(np.fft.rfft(fr * self.win))
            pk = self.band[(P[self.band] > P[self.band - 1]) & (P[self.band] > P[self.band + 1])]
            np.add.at(ch, self.pcs[np.searchsorted(self.band, pk)], P[pk] ** 2)
        return ch / (ch.sum() + 1e-12)


def cut(v, sr, o, dur_s, target, fout=0.03):
    """从字头前 10 ms 起切 dur_s 秒，RMS 拉平（增益上限 4 倍），4 ms 淡入 / fout 淡出。"""
    i0 = max(0, int((o - 0.01) * sr))
    piece = v[i0:i0 + int(dur_s * sr)].copy()
    piece *= min(4.0, target / (rms(piece) + 1e-6))
    return piece * envelope(len(piece), sr, 0.004, fout)[:, None]


def cmd_chops(a):
    guard_out(a.out, a.src)
    if not 0 <= a.min_score <= 1 or a.candidates < 1:
        die("--min-score 须在 0–1，--candidates 须为正整数")
    chords = parse_chords(a.chords)
    pattern = parse_slots(a.pattern)
    stutters = parse_stutter(a.stutter) if a.stutter else []
    st_bars = {int(b) for b in a.stutter_bar.split(",")} if a.stutter_bar else set()
    if bool(stutters) != bool(st_bars):
        die("--stutter 和 --stutter-bar 要一起给，例如 --stutter-bar 17 --stutter \"2.5-3.5:0.25\"")
    beat_s, bar_s = 60 / a.bpm, a.beats_per_bar * 60 / a.bpm
    with tempfile.TemporaryDirectory() as tmp:
        raw = [a.src_bpm, a.downbeat, a.at_bar]
        if any(v is not None for v in raw):
            if any(v is None for v in raw):
                die("原始（未对齐）人声要同时给 --src-bpm、--downbeat、--at-bar；已对齐的人声三个都不要给")
            sr, seg, lead, ratio, _, _ = do_align(a.src, a.src_bpm, a.downbeat, a.bpm, a.at_bar, a.keep_start,
                                                  a.keep_end, a.beats_per_bar, tmp)
            v = assemble([(lead, seg)], sr, lead + len(seg) / sr, seg.shape[1])
            print(f"先对齐：时长 ×{ratio:.4f}，前置静音 {lead:.3f} s → 原第一个强拍在第 {a.at_bar} 小节")
        else:
            sr, v = load_audio(a.src)
        pk = float(np.abs(v).max())
        if pk < 1e-6:
            die("人声是静音")
        v = v / pk                                               # 归一到峰值 1，和参考实现的 0 dBFS 分轨同一尺度
        mono = v.mean(axis=1)
        ons, method = detect_onsets(mono, sr)
        chroma = ChromaTool(mono, sr)
        n3 = int(0.3 * sr)
        cands = [(o, chroma(o)) for o in ons
                 if len(mono[int(o * sr):int(o * sr) + n3]) == n3 and rms(mono[int(o * sr):int(o * sr) + n3]) > 0.02]
        if not cands:
            die("没有找到可用的字头（每个字需要 0.3 s 且不能太轻）")
        print(f"字头 {len(ons)} 个（{method}），可用候选 {len(cands)} 个")

        def best(chord, k):
            tones = sorted({(t - a.repitch) % 12 for t in parse_chord(chord)})
            ranked = sorted((c for c in cands if c[1][tones].sum() >= a.min_score), key=lambda c: -c[1][tones].sum())[:k]
            if not ranked:
                die(f"{chord}: 没有达到最低匹配分 {a.min_score:g} 的切片；请换素材或指定更合适的音节")
            if a.verbose:
                names = " ".join(NOTE[t] for t in parse_chord(chord))
                print(f"  {chord:<6}（{names}）选中:", " ".join(f"{o:6.2f}s({c[tones].sum():.2f})" for o, c in ranked))
            return [o for o, _ in sorted(ranked)]                 # 按原唱先后排，保留语句感

        pieces = []
        for b in sorted(chords):
            pool = best(chords[b], a.candidates)
            stutter = None
            slots = bar_slots(pattern, stutters if b in st_bars else [])
            used = []
            for j, (beat, dur, is_st) in enumerate(slots):
                if is_st:
                    o = stutter = stutter or pool[0]
                else:
                    o = pool[j % len(pool)]
                pieces.append(((b - 1) * bar_s + beat * beat_s, cut(v, sr, o, dur * beat_s, 0.12)))
                used.append(f"{beat:g}拍←{o:.2f}s")
            print(f"第 {b} 小节 {chords[b]}：{len(slots)} 个切片  " + "  ".join(used))
        tail_bar = a.tail_bar or ((max(chords) + 1) if a.tail_chord else None)
        if tail_bar:
            tc = a.tail_chord or chords[max(chords)]
            o = best(tc, 1)[0]
            n = int(a.tail_len * sr)
            piece = v[max(0, int((o - 0.01) * sr)): max(0, int((o - 0.01) * sr)) + n].copy()
            piece *= min(4.0, 0.10 / (rms(piece) + 1e-6)) * (np.linspace(1, 0, len(piece)) ** 1.5)[:, None]
            piece *= envelope(len(piece), sr, 0.004, 0)[:, None]
            pieces.append(((tail_bar - 1) * bar_s, piece))
            print(f"结尾：第 {tail_bar} 小节 {tc} 拖长切片 {a.tail_len:.1f} s ← {o:.2f}s")
        end = max(t + len(p) / sr for t, p in pieces)
        dry = assemble(pieces, sr, out_length(end, bar_s, a.total_bars, a.tail), v.shape[1]) * 0.7
        if a.repitch:
            dry = rubberband(dry, sr, ["-3", "-F", "-p", str(a.repitch)], tmp)
            print(f"整体移调 {a.repitch:+d} 半音（rubberband -3 -F；挑字时已按移调后的音级匹配和弦）")
        how = finish(dry, sr, a.fx, a.out, tmp)
    print(f"输出：{a.out}  {len(dry) / sr:.3f} s，24-bit，{how}；与第 1 小节对齐（{a.bpm} BPM）")


# ================================================================ find-loops
def quick_duration(path):
    from tool_support import probe_audio
    try:
        data = probe_audio(path)
        return float(data['format']['duration'])
    except (ValueError, RuntimeError, KeyError, OSError):
        return None


def cmd_find_loops(a):
    hits = []
    for d in map(os.path.expanduser, LOOP_DIRS):
        for root, _, files in os.walk(d):
            for f in files:
                p = os.path.join(root, f)
                if (f.lower().endswith((".caf", ".aif", ".aiff", ".wav", ".m4a"))
                        and re.search(r"vox|vocal|voice|choir|chop", f, re.I)
                        and all(k.lower() in p.lower() for k in a.keywords)):
                    hits.append(p)
    hits.sort()
    if not hits:
        print("没有找到人声类 Apple Loops。可在 Logic「设置 > 声音资源」下载更多素材，或换个关键词。")
        print("搜索位置：" + "；".join(LOOP_DIRS))
        return
    with ThreadPoolExecutor(8) as ex:
        durs = list(ex.map(quick_duration, hits))
    for p, d in zip(hits, durs):
        flag = "" if re.search(r"vox|vocal|voice|choir", os.path.basename(p), re.I) else "  [只匹配 Chop，可能不是人声]"
        print(f"{'?' if d is None else f'{d:6.2f}'} s  {p}{flag}")
    print(f"共 {len(hits)} 个。提示：Apple Loops 可免版税用于你自己的作品（不能把素材本身单独再分发），"
          "比切原唱更安全；原唱有版权。下一步：syllables 找音高稳定的字。")


# ================================================================ syllables
def find_syllables(x, sr, min_dur=0.12, max_std=30.0, all_segments=False):
    """自动找"音高稳定的字"。YIN 逐帧基频，5 帧中值平滑；非周期度 < 0.2，或 < 0.5 但音高连续（混响/叠唱的
    素材常见）的帧算有声。在停顿、字头、音高跳变（> 0.7 半音）处切段，每段取中间 70% 的帧统计中位音高和
    标准差（音分）。去掉比最响的字轻 12 dB 以上的段（多是混响尾巴）。返回 dict 列表（秒，相对素材开头）。"""
    y = resample(x.mean(axis=1), sr, ASR)
    f0, ap, fr_rms, t, active, _ = voicing(y)
    ok = active & (f0 >= 32) & (f0 < 1000)
    raw = np.where(ok, 69 + 12 * np.log2(np.maximum(f0, 1) / 440), np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)                  # 全是 NaN 的窗口会告警，忽略
        mf = np.nanmedian(np.stack([np.roll(raw, s) for s in (-2, -1, 0, 1, 2)]), axis=0)
        steady = (np.abs(raw - mf) < 0.3) & (np.abs(np.diff(mf, prepend=mf[0])) < 0.3)
    voiced = ok & ((ap < 0.2) | ((ap < 0.5) & steady))
    m = np.where(voiced, mf, np.nan)
    ons, _ = detect_onsets(y, ASR)
    on_frame = {int(round((o * ASR - 512) / 256)): o for o in ons}   # 帧 k 的中心在 (256k + 512)/ASR 秒
    segs, cur, why = [], [], "gap"
    for k in np.where(voiced)[0]:
        new = None
        if cur:
            new = ("gap" if k - cur[-1] > 3 else "onset" if k in on_frame
                   else "pitch" if abs(m[k] - np.median(m[cur[-5:]])) > 0.7 else None)
        if new:
            segs.append((cur, why))
            cur, why = [], new
        cur.append(k)
    if cur:
        segs.append((cur, why))
    out, prev_end = [], 0.0
    for seg, why in segs:
        start = t[seg[0]] - 0.02
        if why == "onset":
            start = on_frame[seg[0]] - 0.01
        elif why == "gap":                                             # 往前找最近的字头，把辅音包进来
            near = [o for o in ons if start - 0.12 <= o <= start + 0.03]
            start = near[-1] - 0.01 if near else start
        start = max(start, prev_end, 0.0)
        end = t[seg[-1]] + 0.03
        if out and out[-1]["end"] > start:
            out[-1]["end"] = round(start, 3)
            out[-1]["dur"] = round(out[-1]["end"] - out[-1]["start"], 3)
        prev_end = end
        k0 = int(len(seg) * 0.15)
        core = m[seg[k0:len(seg) - k0] if len(seg) - 2 * k0 >= 1 else seg]
        med, std = float(np.median(core)), float(np.std(core) * 100)
        out.append({"start": round(start, 3), "end": round(end, 3), "dur": round(end - start, 3),
                    "midi": round(med, 2), "note": note_name(med), "cents": int(round((med - round(med)) * 100)),
                    "std_cents": round(std, 1), "rms_db": round(db(rms(x[int(start * sr):int(end * sr)])), 1)})
    if all_segments or not out:
        return out
    loud = max(s["rms_db"] for s in out)
    return [s for s in out if s["dur"] >= min_dur and s["std_cents"] <= max_std and s["rms_db"] >= loud - 12]


def print_syllables(syl):
    print("  #   起点     终点    时长   音高（MIDI）        稳定度   响度")
    for i, s in enumerate(syl, 1):
        print(f"{i:3d} {s['start']:7.3f}  {s['end']:7.3f}  {s['dur']:5.2f}s  {s['note']:<4}{s['cents']:+4d} 音分"
              f"（{s['midi']:6.2f}） ±{s['std_cents']:4.1f} 音分  {s['rms_db']:6.1f} dB")


def cmd_syllables(a):
    if a.json:
        guard_out(a.json, a.file)
    sr, x = load_audio(a.file)
    syl = find_syllables(x, sr, a.min_dur, a.max_std)
    print(f"==== 音高稳定的字：{os.path.basename(a.file)}（{len(x) / sr:.2f} s）====")
    print(f"条件：时长 ≥ {a.min_dur} s，音高标准差 < {a.max_std} 音分（取每段中间 70% 的帧）")
    if not syl:
        print("没有找到满足条件的字。可放宽 --max-std / --min-dur，或换一个音高更清楚的素材。")
    else:
        print_syllables(syl)
        notes = sorted({s["note"] for s in syl}, key=lambda n: [s["midi"] for s in syl if s["note"] == n][0])
        print(f"共 {len(syl)} 个字，音高：{', '.join(notes)}。sing 会挑移调幅度最小的字唱每个音。")
    if a.json:
        write_text(a.json, json.dumps({"source": os.path.abspath(a.file), "sr": sr, "min_dur": a.min_dur, "max_std": a.max_std,
                       "syllables": syl}, ensure_ascii=False, indent=1), a.file)
        print(f"已写出：{a.json}")


# ================================================================ sing
def read_midi(path, track=None):
    """返回 (音符[(起始拍, 拍长, MIDI, 力度0–1)], 首个 BPM 或 None)。"""
    with open(path, 'rb') as handle:
        return parse_smf(handle.read(), path, track)


def parse_smf(data, path, track):
    from check_midi import Midi
    midi = Midi(path, data=data)
    if any(t['unclosed'] for t in midi.tracks):
        raise ValueError('MIDI 有音符缺少 note-off')
    tracks = [(t['name'], [(start/midi.ppq, (end-start)/midi.ppq, pitch, vel/127)
               for start,end,ch,pitch,vel in t['notes']]) for t in midi.tracks]
    tempo = 60e6/midi.tempos[0][1] if midi.has_tempo else None
    with_notes = [(k, n, ns) for k, (n, ns) in enumerate(tracks, 1) if ns]
    if not with_notes:
        die(f"MIDI 里没有音符：{path}")
    if track is None:
        pick = with_notes[0]
        if len(with_notes) > 1:
            print("MIDI 里有多条带音符的轨：" + "；".join(f"{k}:{n or '(无名)'}({len(ns)} 个音)" for k, n, ns in with_notes)
                  + f"。用第 {pick[0]} 条，可用 --track 指定")
    else:
        pick = next((w for w in with_notes if str(w[0]) == track or track.lower() in w[1].lower()), None)
        if not pick:
            die(f"找不到轨道 {track}；可选：" + "；".join(f"{k}:{n}" for k, n, _ in with_notes))
    return pick[2], tempo


def parse_notes(spec, bpb):
    """--notes "小节:拍:拍长:音名[:力度]" → [(起始拍, 拍长, 音高, 力度0–1)]。小节从 1、拍从 0 算。"""
    out = []
    for tok in spec.replace(",", " ").split():
        f = tok.split(":")
        if len(f) not in (4, 5):
            die(f"--notes 每项是 小节:拍:拍长:音名[:力度]，例如 5:0:0.5:C#5，这里有问题：{tok}")
        vel = float(f[4]) if len(f) == 5 else 1.0
        out.append(((int(f[0]) - 1) * bpb + float(f[1]), float(f[2]), parse_note(f[3]), vel / 127 if vel > 1 else vel))
    return sorted(out)


def load_syllable_table(path):
    """读 syllables 的 JSON，或手写的 [[起点, 终点, MIDI], …] 列表；太短（< 30 ms）的条目丢掉。"""
    try:
        with open(path, encoding="utf-8") as f:
            j = json.load(f)
        items = j["syllables"] if isinstance(j, dict) else j
        syl = [(float(s["start"]), float(s["end"]), float(s["midi"])) if isinstance(s, dict)
               else tuple(map(float, s[:3])) for s in items]
    except (ValueError, KeyError, TypeError, IndexError) as e:
        die(f"字表格式不对：{path}（{e}）。应为 syllables --json 的输出，或 [[起点, 终点, MIDI], …]")
    return [s for s in syl if s[1] - s[0] >= 0.03]


def cmd_sing(a):
    guard_out(a.out, a.src, a.syllables, a.midi)
    if bool(a.notes) == bool(a.midi):
        die("旋律用 --notes 或 --midi 二选一")
    bpb = a.beats_per_bar
    if a.notes:
        notes, midi_bpm = parse_notes(a.notes, bpb), None
    else:
        notes, midi_bpm = read_midi(a.midi, a.track)
    bpm = a.bpm or midi_bpm or die("需要 --bpm（MIDI 里也没有速度信息）")
    if a.bpm and midi_bpm and abs(midi_bpm - a.bpm) > 0.01:
        print(f"注意：MIDI 里的速度是 {midi_bpm:.2f} BPM，按 --bpm {a.bpm} 以拍为单位摆放")
    beat_s, bar_s = 60 / bpm, bpb * 60 / bpm
    sr, x = load_audio(a.src)
    if a.syllables:
        syl = load_syllable_table(a.syllables)
        print(f"字表：{a.syllables}（{len(syl)} 个字）")
    else:
        found = find_syllables(x, sr, a.min_dur, a.max_std)
        syl = [(s["start"], s["end"], s["midi"]) for s in found]
        print(f"没给 --syllables，自动找到 {len(syl)} 个音高稳定的字：")
        print_syllables(found)
    if not syl:
        die("素材里没有可用的字（可放宽 --max-std / --min-dur，或换素材）")
    cache, pieces, used, uses, errs = {}, [], set(), {}, []
    with tempfile.TemporaryDirectory() as tmp:
        for start_beat, beats, target, vel in notes:
            need = beats * beat_s + 0.05
            # 选字：移调幅度每 3 半音一档 → 需要拉长超过 1.3 倍的往后排；
            # 同一档里只在"移调差不多"（比最小移调多不到 1 半音）的字之间轮流（用得少的优先），
            # 都要拉长超过 1.3 倍时（长音）挑最长的字，少截短
            def klass(k):
                return int(abs(target - syl[k][2]) // 3), need / (syl[k][1] - syl[k][0]) > 1.3
            best = min(klass(k) for k in range(len(syl)))
            cands = [k for k in range(len(syl)) if klass(k) == best]
            lo = min(abs(target - syl[k][2]) for k in cands)
            near = [k for k in cands if abs(target - syl[k][2]) <= lo + 1.0]
            if best[1]:
                i = max(near, key=lambda k: (syl[k][1] - syl[k][0], -uses.get(k, 0)))
            else:
                i = min(near, key=lambda k: (uses.get(k, 0), abs(target - syl[k][2]), k))
            uses[i] = uses.get(i, 0) + 1
            s0, s1, m = syl[i]
            want = need / (s1 - s0)
            ratio = min(a.max_stretch, max(1.0, want))
            key = (i, round(target - m, 2), round(ratio, 2))
            if key not in cache:
                seg = x[int(s0 * sr):int(s1 * sr)]
                args = ["-3", "-F"] + (["-p", f"{target - m:.3f}"] if abs(target - m) > 0.005 else []) + \
                       (["-t", f"{ratio:.3f}"] if ratio > 1.0 else [])
                cache[key] = rubberband(seg, sr, args, tmp) if len(args) > 2 else seg.copy()
            piece = cache[key][:int(need * sr)].copy()
            check = ""
            if a.verify:                                       # 渲染后实测音高（搜索范围限在目标音附近，防八度误判）
                mono = piece.mean(axis=1) if piece.ndim > 1 else piece
                y = resample(mono[:, None], sr, ASR)[:, 0] if sr != ASR else mono
                hz = 440 * 2 ** ((target - 69) / 12)
                f0, ap, fr, _ = yin(y, ASR, fmin=max(50.0, hz / 1.6), fmax=min(2000.0, hz * 1.6))
                ok = (ap < 0.3) & (fr > fr.max() * 0.1) if len(fr) else []
                if len(fr) and ok.sum() >= 3:
                    cents = 1200 * math.log2(float(np.median(f0[ok])) / hz)
                    errs.append(cents)
                    check = f"  实测 {cents:+.0f} 音分" + ("  ⚠" if abs(cents) > 35 else "")
                else:
                    check = "  实测：太短/不清楚，没测出"
            piece *= vel * 0.15 / (rms(piece) + 1e-6)
            piece *= envelope(len(piece), sr, 0.004, 0.06)[:, None]
            t0 = start_beat * beat_s
            pieces.append((t0, piece))
            used.add(i)
            short = f"，截短（需要 ×{want:.2f}，上限 ×{a.max_stretch}）" if want > a.max_stretch else ""
            print(f"  {pos(t0, bpm, bpb)}  {note_name(target):<4}({target}) {round(beats, 3):g} 拍 ← 字#{i + 1} "
                  f"[{s0:.2f}–{s1:.2f}s {note_name(m)} {m:.2f}]  移调 {target - m:+.2f} 半音  拉长 ×{ratio:.2f}{short}{check}")
        if a.riser_bar:
            n = int(bar_s * sr)
            noise = np.random.default_rng(1).standard_normal((n, 2)).astype(np.float32)
            noise = np.diff(noise, axis=0, prepend=0) * 0.5                  # 粗略高通，留嘶嘶声
            pieces.append(((a.riser_bar - 1) * bar_s, noise * (np.linspace(0, 1, n) ** 2.5)[:, None] * 0.08))
            print(f"  第 {a.riser_bar} 小节：白噪声上升音（riser）")
        end = max(t + len(p) / sr for t, p in pieces)
        dry = assemble(pieces, sr, out_length(end, bar_s, a.total_bars, a.tail), 2) * 0.5
        how = finish(dry, sr, a.fx, a.out, tmp)
    print(f"共 {len(notes)} 个音，用了 {len(used)} 个不同的字，rubberband 渲染 {len(cache)} 次（其余复用缓存）")
    if a.verify:
        if errs:
            big = sum(abs(c) > 35 for c in errs)
            print(f"音准实测：测出 {len(errs)}/{len(notes)} 个音，误差中位 {float(np.median(np.abs(errs))):.0f} 音分，"
                  f"最大 {max(abs(c) for c in errs):.0f} 音分，超过 ±35 音分的 {big} 个"
                  + ("（这些字多半带滑音：换素材或 syllables --max-std 20）" if big else ""))
        else:
            print("音准实测：一个都没测出（音太短或素材混响太重）")
    print(f"输出：{a.out}  {len(dry) / sr:.3f} s，24-bit 立体声，{how}；与第 1 小节对齐（{bpm:g} BPM）")


# ================================================================ 命令行
def main():
    p = zh_parser(argparse.ArgumentParser(prog="vocal_tools.py", add_help=False, formatter_class=ZhHelp,
                                          description=__doc__.split("\n\n")[1]))
    sub = p.add_subparsers(dest="cmd", metavar="子命令", title="子命令")

    def add(name, helptext, desc):
        return zh_parser(sub.add_parser(name, help=helptext, description=desc, add_help=False, formatter_class=ZhHelp))

    s = add("info", "测量人声：有声帧比例、中位音高、音域、字头数，给出能否改音高的建议",
            "用简化 YIN 逐帧测基频，统计清晰有声帧比例（非周期度 < 0.25）、中位音高和音域（C4=60）、"
            "音高稳定的字覆盖多少时长，再数字头，判断适不适合给切片改音高。")
    s.add_argument("file", help="人声音频（任何 ffmpeg 能读的格式）")
    s.set_defaults(func=cmd_info)

    def align_args(s, required):
        s.add_argument("--src-bpm", type=float, required=required, help="源人声的 BPM")
        s.add_argument("--downbeat", type=float, required=required, help="源文件里原第一个强拍的时间（秒，源文件时间轴）")
        s.add_argument("--at-bar", type=int, required=required, help="让原第一个强拍落在第几小节")
        s.add_argument("--keep-start", type=float, default=0.0, help="保留段起点（秒，默认 0）")
        s.add_argument("--keep-end", type=float, help="保留段终点（秒，默认到文件结尾）")

    s = add("align", "变速到目标 BPM 并补静音，让原第一个强拍落在指定小节",
            "截取 → rubberband -3 -F -t (src_bpm/bpm) 变速（音高、共振峰不变）→ 前置静音\n"
            "lead = (at_bar-1) × 每小节秒数 − (downbeat−keep_start) × ratio。输出 24-bit WAV，峰值不超过 -1 dBFS。")
    s.add_argument("--src", required=True, help="源人声")
    align_args(s, True)
    s.add_argument("--bpm", type=float, required=True, help="目标 BPM（Logic 项目速度）")
    s.add_argument("--beats-per-bar", type=int, default=4, help="每小节几拍（默认 4）")
    s.add_argument("--total-bars", type=int, help="整首小节数：输出至少这么长（不截断人声）")
    s.add_argument("--tail", type=float, default=0.0, help="在结尾再补几秒静音（默认 0）")
    s.add_argument("--out", required=True, help="输出 WAV（不能与输入相同）")
    s.set_defaults(func=cmd_align)

    s = add("chops", "按和弦音匹配度挑字，按节奏型摆成人声切片",
            "找字头（aubioonset，缺失时用 numpy）→ 每个字头算 150–1500 Hz 峰值 chroma → 每个和弦选匹配度最高的前 N 个字，"
            "按原唱先后顺序放进节奏槽位 → RMS 拉平（≤4 倍）、4 ms 淡入/30 ms 淡出 → sox 效果。\n"
            "--src 是已对齐的人声；如果是原始人声，同时给 --src-bpm --downbeat --at-bar，会先内部对齐。")
    s.add_argument("--src", required=True, help="人声（已对齐到第 1 小节，或原始人声 + 对齐参数）")
    align_args(s, False)
    s.add_argument("--bpm", type=float, required=True, help="目标 BPM")
    s.add_argument("--beats-per-bar", type=int, default=4, help="每小节几拍（默认 4）")
    s.add_argument("--chords", required=True, help="小节:和弦，例如 \"14:F 15:G 16:Am 17:Am\"")
    s.add_argument("--pattern", default=CHOP_PATTERN, help=f"节奏槽位 拍位:拍长,…（默认 3-3-2：{CHOP_PATTERN}）")
    s.add_argument("--stutter-bar", help="做口吃重复的小节，可逗号分隔多个，例如 17")
    s.add_argument("--stutter", help="口吃区间 起拍-止拍:间隔拍，例如 \"2.5-3.5:0.25\"（同一个字反复）")
    s.add_argument("--tail-chord", help="结尾拖长切片用哪个和弦挑字（默认最后一个和弦）")
    s.add_argument("--tail-bar", type=int, help="结尾拖长切片放在第几小节（默认最后一个和弦小节 + 1）")
    s.add_argument("--tail-len", type=float, default=1.2, help="结尾拖长切片长度（秒，默认 1.2）")
    s.add_argument("--fx", default=CHOP_FX, help=f"效果链（sox），none 为干声。默认：{CHOP_FX}")
    s.add_argument("--repitch", type=int, default=0, help="整体移调半音数（rubberband -p，默认 0 不改）")
    s.add_argument("--min-score", type=float, default=0.5, help="和弦音级能量占比下限（0–1，默认 0.5；不是正确率）")
    s.add_argument("--candidates", type=int, default=5, help="每个和弦选前几个字（默认 5）")
    s.add_argument("--total-bars", type=int, help="整首小节数：输出至少这么长")
    s.add_argument("--tail", type=float, default=4.0, help="结尾留给混响的秒数（默认 4）")
    s.add_argument("--out", required=True, help="输出 WAV（不能与输入相同）")
    s.add_argument("--verbose", action="store_true", help="打印每个和弦选中的字头时间和匹配分数")
    s.set_defaults(func=cmd_chops)

    s = add("find-loops", "在 Logic 自带 Apple Loops 里找人声类循环（文件名含 Vox/Vocal/Voice/Choir/Chop）",
            "搜索 " + "、".join(LOOP_DIRS) + "，列出完整路径和时长。Apple Loops 可免版税用于自己的作品。")
    s.add_argument("keywords", nargs="*", help="再按关键词过滤（匹配完整路径，不分大小写，多个词需同时出现）")
    s.set_defaults(func=cmd_find_loops)

    s = add("syllables", "自动找音高稳定的字：起止、中位音高、稳定度、响度",
            "按字头/停顿/音高跳变切段，每段用简化 YIN 逐帧测基频，取中间 70% 的帧算中位音高（浮点 MIDI）"
            "和标准差（音分），只保留稳定的段。")
    s.add_argument("file", help="素材（例如 Apple Loop .caf）")
    s.add_argument("--json", help="另存 JSON，供 sing --syllables 使用")
    s.add_argument("--min-dur", type=float, default=0.12, help="最短时长（秒，默认 0.12）")
    s.add_argument("--max-std", type=float, default=30.0, help="音高标准差上限（音分，默认 30）")
    s.set_defaults(func=cmd_syllables)

    s = add("sing", "让切片唱出给定旋律（--notes 或 MIDI）",
            "每个音挑移调幅度最小的字（每 3 半音一档，同档轮流用），rubberband -3 -F -p 移到目标音高，"
            "音符比字长时 -t 拉长（不超过 --max-stretch，超了就截短）；同一组合只渲染一次。\n"
            "RMS 拉平、4 ms 淡入 / 60 ms 淡出，按小节摆好，sox 加高通和混响。输出与第 1 小节对齐。")
    s.add_argument("--src", required=True, help="素材音频（例如 Apple Loop .caf）")
    s.add_argument("--syllables", help="syllables 生成的 JSON（或手写 [[起点,终点,MIDI],…]）；不给就自动找")
    s.add_argument("--bpm", type=float, help="速度（用 --midi 时默认取 MIDI 里的速度）")
    s.add_argument("--beats-per-bar", type=int, default=4, help="每小节几拍（默认 4）")
    s.add_argument("--notes", help="旋律：小节:拍:拍长:音名[:力度]，空格分隔，例如 \"5:0:0.5:C#5 5:0.5:0.5:D#5\"；"
                                   "小节从 1、拍从 0 算，C4=60，力度 0–1 或 1–127")
    s.add_argument("--midi", help="从 MIDI 文件读旋律（与 --notes 二选一）")
    s.add_argument("--track", help="MIDI 轨道名（包含即可）或序号")
    s.add_argument("--riser-bar", type=int, help="在这一小节加白噪声上升音（蓄力）")
    s.add_argument("--max-stretch", type=float, default=1.6, help="单个字最多拉长几倍（默认 1.6）")
    s.add_argument("--min-dur", type=float, default=0.12, help="自动找字时的最短时长（默认 0.12 s）")
    s.add_argument("--max-std", type=float, default=30.0, help="自动找字时的音高标准差上限（默认 30 音分）")
    s.add_argument("--fx", default=SING_FX, help=f"效果链（sox），none 为干声。默认：{SING_FX}")
    s.add_argument("--total-bars", type=int, help="整首小节数：输出至少这么长")
    s.add_argument("--tail", type=float, default=4.0, help="结尾留给混响的秒数（默认 4）")
    s.add_argument("--verify", action="store_true", help="渲染后逐音实测音高（只在目标音附近搜索，避免八度误判）")
    s.add_argument("--out", required=True, help="输出 WAV（不能与输入相同）")
    s.set_defaults(func=cmd_sing)

    p.epilog = "依赖：numpy、ffmpeg/ffprobe；变速/移调需要 rubberband；效果链需要 sox；起音检测可选 aubioonset。"
    a = p.parse_args()
    if not getattr(a, "func", None):
        p.print_help()
        sys.exit(1)
    for k in ("bpm", "src_bpm", "max_stretch", "tail_len"):
        if getattr(a, k, None) is not None and getattr(a, k) <= 0:
            die(f"--{k.replace('_', '-')} 必须大于 0")
    if getattr(a, "candidates", 1) < 1:
        die("--candidates 至少为 1")
    a.func(a)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as exc:
        sys.exit(f"错误：{exc}")
