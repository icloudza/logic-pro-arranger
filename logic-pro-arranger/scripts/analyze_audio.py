#!/usr/bin/env python3
"""analyze_audio.py —— 把音频"量"成数字：响度、速度、调性、频段、质心、立体声、能量时间线。

用途
  Claude 听不到声音，用这个脚本代替耳朵：分析参考曲（常见是抖音录屏 mp4/m4a/mp3），
  或用 --compare 把参考曲和用户导出的成品并排比较（哪个频段差多少 dB）。
  输出中文文字报告；加 --json 时同时写出 JSON。每项估计都注明可靠程度。

依赖
  Python 3.9+、numpy（不需要 scipy / librosa）
  ffmpeg / ffprobe：解码任何格式（转成 f32le 读进 numpy）并测 EBU R128 响度；
                   缺失时只能读 WAV，响度改为不带 K 加权的近似值
  aubio（可选）：速度交叉验证；`aubio tempo` 不可用时退回 aubiotrack 的拍点间隔

用法示例
  python3 analyze_audio.py 参考.mp4
  python3 analyze_audio.py 参考.m4a --start 12 --end 42 --json 参考.json
  python3 analyze_audio.py 成品.wav --bpm 104 --bars-per-row 4
  python3 analyze_audio.py 参考.mp4 --compare 成品.wav

vocal_tools.py 会从本文件导入解码、写 WAV、onset 等函数，两个文件要放在同一目录。
"""
import argparse
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
sys.dont_write_bytecode = True
import tempfile

from tool_support import LazyNumpy, find_tool, guard_out, output_path, write_text
np = LazyNumpy()

SR, HOP = 22050, 512                     # 分析采样率与帧移（约 43 帧/秒）
FR = SR / HOP
NOTE = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
BANDS = [("sub", "<60", 20, 60), ("bass", "60–250", 60, 250), ("low-mid", "250–500", 250, 500),
         ("mid", "500–2k", 500, 2000), ("high-mid", "2k–6k", 2000, 6000), ("air", ">6k", 6000, 1e9)]
KS_MAJ = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
KS_MIN = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)


# ================================================================ 通用工具（vocal_tools.py 也用）
def die(msg):
    sys.exit(f"错误：{msg}")


def db(v, floor=1e-10):
    return 20 * math.log10(max(float(v), floor))


def note_name(midi):
    m = int(round(midi))
    return f"{NOTE[m % 12]}{m // 12 - 1}"


def bar(frac, width=24):
    n = int(round(max(0.0, min(1.0, frac)) * width))
    return "█" * n + "░" * (width - n)


class ZhHelp(argparse.RawDescriptionHelpFormatter):
    def add_usage(self, usage, actions, groups, prefix=None):
        return super().add_usage(usage, actions, groups, "用法: " if prefix is None else prefix)


def zh_parser(parser):
    """argparse 自带标题换成中文，并加中文的 -h（解析器要用 add_help=False 创建）。"""
    parser._positionals.title = "位置参数"
    parser._optionals.title = "选项"
    parser.add_argument("-h", "--help", action="help", help="显示帮助并退出")
    return parser


@__import__("tool_support").normalized_audio
def probe(path):
    """时长、采样率、声道、编码。优先 ffprobe；没有时只支持 WAV。"""
    if not os.path.isfile(path):
        die(f"找不到文件：{path}")
    fp = find_tool("ffprobe")
    if fp:
        try:
            from tool_support import probe_audio
            j = probe_audio(path)
            s = j["streams"][0]
            dur = float(s.get("duration") or j.get("format", {}).get("duration") or 0)
            return {"duration": dur, "sr": int(s["sample_rate"]), "channels": int(s["channels"]),
                    "codec": s.get("codec_name", "?")}
        except (ValueError, KeyError, IndexError):
            die(f"ffprobe 读不出音频流：{path}（文件里可能没有音频）")
    sr, x = read_wav(path)
    return {"duration": len(x) / sr, "sr": sr, "channels": x.shape[1], "codec": "wav(纯 Python 读取)"}


def ff_input(path, start=None, end=None):
    args = ["-ss", f"{start:.4f}"] if start else []
    args += ["-i", path]
    if end is not None:
        args += ["-t", f"{max(0.01, end - (start or 0)):.4f}"]
    return args


@__import__("tool_support").normalized_audio
def load_audio(path, sr=None, start=None, end=None):
    """解码成 float32 (样本数, 声道≤2)。ffmpeg 优先（任何格式）；没有 ffmpeg 时只读 WAV。"""
    info = probe(path)
    sr, ch = sr or info["sr"], min(info["channels"], 2)
    ff = find_tool("ffmpeg")
    if ff:
        cmd = [ff, "-nostdin", "-v", "error"] + ff_input(path, start, end) + [
            "-map", "0:a:0", "-f", "f32le", "-ac", str(ch), "-ar", str(sr), "-"]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode:
            die(f"ffmpeg 解码失败：{path}\n{r.stderr.decode(errors='ignore')[-400:]}")
        return sr, np.frombuffer(r.stdout, "<f4").reshape(-1, ch).copy()
    if not getattr(load_audio, "warned", False):
        load_audio.warned = True
        print("提示：没有找到 ffmpeg，改用纯 Python 读 WAV（mp3/m4a/mp4 读不了，请 brew install ffmpeg）",
              file=sys.stderr)
    sr0, x = read_wav(path)
    x = x[int((start or 0) * sr0): int(end * sr0) if end else len(x), :2]
    return sr, resample(x, sr0, sr)


def read_wav(path):
    """纯 Python 读 WAV（PCM 16/24/32 位或浮点），ffmpeg 缺失时的降级方案。"""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        die(f"没有 ffmpeg 时只能读 WAV：{path}")
    pos, fmt, raw = 12, None, b""
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
            if fmt[0] == 0xFFFE and len(body) >= 26:          # WAVE_FORMAT_EXTENSIBLE
                fmt = (struct.unpack("<H", body[24:26])[0],) + fmt[1:]
        elif cid == b"data":
            raw = body
        pos += 8 + size + (size & 1)
    tag, ch, sr, _, _, bits = fmt
    raw = raw[: len(raw) // (ch * bits // 8) * ch * bits // 8]
    if tag == 3:
        x = np.frombuffer(raw, "<f4" if bits == 32 else "<f8").astype(np.float32)
    elif bits == 16:
        x = np.frombuffer(raw, "<i2") / 32768.0
    elif bits == 24:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        x = (((b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)) << 8) >> 8) / 8388608.0
    elif bits == 32:
        x = np.frombuffer(raw, "<i4") / 2147483648.0
    else:
        die(f"不支持的 WAV 位深：{bits}")
    return sr, x.astype(np.float32).reshape(-1, ch)


def resample(x, sr0, sr1):
    """FFT 重采样（只在没有 ffmpeg 或处理内存数组时用）。"""
    if sr0 == sr1 or len(x) == 0:
        return x.astype(np.float32)
    n1 = int(round(len(x) * sr1 / sr0))
    X = np.fft.rfft(x, axis=0)
    Y = np.zeros((n1 // 2 + 1,) + X.shape[1:], X.dtype)
    k = min(len(Y), len(X))
    Y[:k] = X[:k]
    return (np.fft.irfft(Y, n1, axis=0) * (n1 / len(x))).astype(np.float32)


def write_wav(path, x, sr, bits=24, *, force=False):
    """写 WAV：16/24 位整数或 32 位浮点（rubberband 中间文件用浮点，不会削波）。"""
    x = np.asarray(x, np.float32).reshape(len(x), -1)
    if bits == 32:
        data, tag = x.astype("<f4").tobytes(), 3
    else:
        full = 2 ** (bits - 1)
        q = np.clip(np.round(x * full), -full, full - 1).astype("<i4")
        data = q.astype("<i2").tobytes() if bits == 16 else q.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
        tag = 1
    ch, nb, pad = x.shape[1], bits // 8, len(data) & 1
    hdr = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(data) + pad, b"WAVE", b"fmt ", 16, tag, ch,
                      sr, sr * ch * nb, ch * nb, bits, b"data", len(data))
    with output_path(path, force=force) as temp:
        temp.write_bytes(hdr + data + b"\0" * pad)


# ================================================================ 频谱基础
def stft_mag(y, n_fft, hop):
    """单声道幅度谱 (帧, 频点) float32；第 k 帧以 k*hop 为中心。分块计算省内存。"""
    y = np.pad(np.asarray(y, np.float32), n_fft // 2)
    y = np.pad(y, (0, max(0, n_fft - len(y))))
    n = 1 + (len(y) - n_fft) // hop
    frames = np.lib.stride_tricks.as_strided(y, (n, n_fft), (y.strides[0] * hop, y.strides[0]))
    win = np.hanning(n_fft).astype(np.float32)
    out = np.empty((n, n_fft // 2 + 1), np.float32)
    for i in range(0, n, 512):
        out[i:i + 512] = np.abs(np.fft.rfft(frames[i:i + 512] * win, axis=1))
    return out


def mel_fb(n_fft, sr, n_mels=64, fmin=30.0):
    mel = lambda f: 2595 * np.log10(1 + f / 700)
    pts = 700 * (10 ** (np.linspace(mel(fmin), mel(sr / 2), n_mels + 2) / 2595) - 1)
    f = np.fft.rfftfreq(n_fft, 1 / sr)
    lo, c, hi = pts[:-2, None], pts[1:-1, None], pts[2:, None]
    return np.maximum(0, np.minimum((f - lo) / (c - lo), (hi - f) / (hi - c))).astype(np.float32)


def flux(M):
    """谱通量：dB 后取正向一阶差分，再对各频带平均。"""
    L = 20 * np.log10(np.maximum(M, M.max() * 1e-4 + 1e-12))
    return np.maximum(0, np.diff(L, axis=0, prepend=L[:1])).mean(axis=1)


def onset_env(S, sr):
    """onset 强度（mel 64 带谱通量）。S 为 stft_mag 的结果。"""
    return flux(S @ mel_fb((S.shape[1] - 1) * 2, sr).T)


def pick_peaks(o, fr, min_gap=0.05, delta=0.5):
    """onset 包络找峰：局部最大，且高于 1 秒滑动均值 + delta×标准差。返回帧号列表。"""
    w = max(1, int(round(min_gap * fr)))
    loc = np.max([np.roll(o, s) for s in range(-w, w + 1)], axis=0)
    k = max(3, int(fr))
    mean = np.convolve(o, np.ones(k) / k, mode="same")
    keep = []
    for i in np.where((o >= loc) & (o > mean + delta * o.std()) & (o > 0))[0]:
        if not keep or i - keep[-1] >= w:
            keep.append(int(i))
    return keep


# ================================================================ 各项分析
def loudness(path, start, end, x, sr):
    """ffmpeg ebur128（ITU-R BS.1770）：integrated LUFS、LRA、true peak；失败时给近似值。"""
    ff = find_tool("ffmpeg")
    for flt in ("ebur128=peak=true:framelog=verbose", "ebur128=peak=true"):
        if not ff:
            break
        r = subprocess.run([ff, "-nostdin", "-hide_banner", "-nostats"] + ff_input(path, start, end) +
                           ["-map", "0:a:0", "-af", flt, "-f", "null", "-"],
                           capture_output=True, text=True, errors="ignore")
        tail = r.stderr.rsplit("Summary:", 1)[-1] if "Summary:" in r.stderr else ""
        num = lambda pat: (lambda m: float(m.group(1)) if m else None)(re.search(pat, tail))
        if tail:
            return {"lufs": num(r"I:\s+(-?inf|-?[\d.]+) LUFS"), "lra": num(r"LRA:\s+(-?[\d.]+) LU"),
                    "true_peak": num(r"Peak:\s+(-?inf|-?[\d.]+) dBFS"), "method": "ffmpeg ebur128"}
    blk, hop = int(0.4 * sr), int(0.1 * sr)                  # BS.1770 门限法，但没有 K 加权
    z = np.array([np.mean(x[i:i + blk] ** 2) * x.shape[1] for i in range(0, max(1, len(x) - blk), hop)])
    z = z[z > 10 ** ((-70 + 0.691) / 10)]
    z = z[z > z.mean() / 10] if len(z) else z
    lufs = -0.691 + 10 * math.log10(z.mean()) if len(z) else None
    return {"lufs": lufs, "lra": None, "true_peak": None, "method": "近似（无 ffmpeg，未做 K 加权，误差约 ±2 LU）"}


def autocorr(o):
    o = o - np.convolve(o, np.ones(int(FR)) / int(FR), mode="same")     # 去掉慢变化
    ac = np.fft.irfft(np.abs(np.fft.rfft(o, 2 * len(o))) ** 2)[:len(o)]
    return ac / (ac[0] + 1e-12)


def refine_period(ac, L):
    """在 1、2、4…32 拍处的自相关峰上逐级精修拍周期（帧）。返回 (周期, 各级 BPM)。"""
    hist = []
    for k in (1, 2, 4, 8, 16, 32):
        c = k * L
        if c > 0.6 * len(ac):
            break
        w = max(2, int(min(0.03 * c, L / 5)))
        lo, hi = int(c - w), int(c + w) + 1
        i = lo + int(np.argmax(ac[lo:hi]))
        if lo < i < hi - 1:
            a, b, d = ac[i - 1], ac[i], ac[i + 1]
            L = (i + 0.5 * (a - d) / (a - 2 * b + d - 1e-12)) / k
            hist.append(60 * FR / L)
    return L, hist


def beat_phase(o, period):
    """拍周期已知时找相位：各相位把每拍位置的 onset 强度取平均，取最大。返回 (秒, 对比度)。"""
    sm = np.convolve(o, [0.25, 0.5, 0.25], mode="same")
    ks = np.arange(int((len(sm) - 1 - period) // period) + 1)
    ph = np.arange(0, period, 0.1)
    c = np.interp(ph[:, None] + ks[None, :] * period, np.arange(len(sm)), sm).mean(axis=1)
    return float(((ph[np.argmax(c)] + 1) % period) / FR), float(c.max() / (c.mean() + 1e-12))


RATIOS = [(0.5, "×1/2"), (2, "×2"), (2 / 3, "×2/3"), (1.5, "×3/2"), (0.75, "×3/4"), (4 / 3, "×4/3")]


def estimate_tempo(onset, onset_low, known_bpm=None):
    """onset 自相关测速：60–180 BPM 内按"细分+拍+两拍+一小节"打分，乘 120 BPM 为中心的先验。"""
    if len(onset) < FR * 6:
        return {"ok": False, "note": "音频太短（<6 秒），不估计速度"}
    ac = autocorr(onset)
    A = lambda L: np.interp(L, np.arange(len(ac)), ac, right=0.0)
    bpms = np.arange(60, 180.01, 0.1)
    L = 60 * FR / bpms
    raw = 0.5 * A(L / 2) + A(L) + A(2 * L) + A(4 * L)
    score = raw * np.exp(-0.5 * np.log2(bpms / 120) ** 2)
    pk = np.where((score[1:-1] > score[:-2]) & (score[1:-1] >= score[2:]))[0] + 1
    pk = list(pk[np.argsort(-score[pk])]) or [int(np.argmax(score))]
    period, hist = refine_period(ac, L[pk[0]])
    best = 60 * FR / period
    cands = []
    for i in pk[1:6]:
        b = 60 * FR / refine_period(ac, L[i])[0]
        if all(abs(b / c["bpm"] - 1) > 0.02 for c in cands) and abs(b / best - 1) > 0.02:
            rel = next((t for r, t in RATIOS if abs(b / (best * r) - 1) < 0.02), "")
            cands.append({"bpm": round(b, 2), "strength": round(float(score[i] / score[pk[0]]), 2), "relation": rel})
    clarity = float(raw[pk[0]] / 3.5)
    spread = (max(hist[-3:]) - min(hist[-3:])) / 2 if len(hist) >= 3 else None
    use_bpm = known_bpm or best
    t0, contrast = beat_phase(onset_low, 60 * FR / use_bpm)
    src = "低频(<250 Hz)鼓点"
    if contrast < 1.3:
        t0, contrast = beat_phase(onset, 60 * FR / use_bpm)
        src = "全频段 onset"
    return {"ok": True, "bpm": round(best, 2), "half": round(best / 2, 2), "double": round(best * 2, 2),
            "beat_s": round(60 / best, 4), "others": cands[:3], "clarity": round(clarity, 3),
            "spread_bpm": None if spread is None else round(spread, 3),
            "confidence": "高" if clarity >= 0.4 else ("中" if clarity >= 0.2 else "低"),
            "grid": {"bpm": round(use_bpm, 2), "first_beat_s": round(t0, 3), "contrast": round(contrast, 2),
                     "source": src}}


def aubio_tempo(mono):
    """使用 aubiotrack 的拍点间隔作独立对照。"""
    exe = find_tool("aubiotrack")
    if not exe:
        return {"bpm": None, "method": "未安装 aubiotrack，跳过交叉验证"}
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "mono.wav")
        write_wav(wav, mono, SR, 16)
        try:
            result = subprocess.run([exe, "-i", wav], capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return {"bpm": None, "method": "aubiotrack 超时"}
    if result.returncode:
        return {"bpm": None, "method": f"aubiotrack 失败（{result.returncode}）：{result.stderr.strip()[-300:]}"}
    times = [float(v) for v in result.stdout.split() if re.fullmatch(r"[\d.]+", v)]
    gaps = np.diff(times)
    if len(times) <= 8 or np.any(gaps <= 0):
        return {"bpm": None, "method": "aubiotrack 没有足够且递增的拍点"}
    return {"bpm": round(60 / float(np.median(gaps)), 2), "method": "aubiotrack 拍点间隔中位数"}


def chroma_peaks(y, n_fft=4096, hop=1024, fmin=60, fmax=2000):
    """频谱峰 chroma：只取局部峰、抛物线插值求准频率、估计调音偏移，再归到 12 个音级。
    每帧归一（响的段落不独占），安静帧不计。返回 (chroma, 低音 chroma, 调音偏移/半音)。"""
    S = stft_mag(y, n_fft, hop)
    f = np.fft.rfftfreq(n_fft, 1 / SR)
    b = np.where((f >= fmin) & (f <= fmax))[0]
    l, m, r = S[:, b - 1], S[:, b], S[:, b + 1]
    peak = (m > l) & (m >= r) & (m > S.max() * 1e-3)
    la, ma, ra = (np.log(v + 1e-9) for v in (l, m, r))
    d = np.clip(0.5 * (la - ra) / (la - 2 * ma + ra - 1e-12), -0.5, 0.5)
    midi = 69 + 12 * np.log2((b + d) * SR / n_fft / 440)
    w = np.where(peak, m, 0)
    tuning = float(np.angle(np.sum(w * np.exp(2j * np.pi * (midi - np.round(midi))))) / (2 * np.pi))
    pc = np.round(midi - tuning).astype(int) % 12
    frames = np.stack([(w * (pc == k)).sum(axis=1) for k in range(12)], axis=1)
    bass = np.stack([(w * (pc == k) * (midi < 52)).sum(axis=0).sum() for k in range(12)])
    e = frames.sum(axis=1, keepdims=True)
    frames = frames / (e + 1e-12) * (e > np.percentile(e, 20))
    return frames.sum(axis=0), bass, tuning


def estimate_key(chroma):
    """Krumhansl–Schmuckler 相关 + 音阶集合校正：先看哪组七声音阶（调号）装下最多能量，
    不在这组音阶里的调按"少装的比例"扣分，再按 KS 相关系数排序。"""
    c = chroma / (chroma.sum() + 1e-12)
    fits = np.array([c[[(r + i) % 12 for i in MAJOR_SCALE]].sum() for r in range(12)])
    keys = []
    for r in range(12):
        for mode, prof, rel in (("大调", KS_MAJ, r), ("小调", KS_MIN, (r + 3) % 12)):
            ks = float(np.corrcoef(c, np.roll(prof, r))[0, 1]) if c.std() > 1e-9 else 0.0
            keys.append({"key": f"{NOTE[r]} {mode}", "r": round(ks, 3), "scale_fit": round(float(fits[rel]), 3),
                         "score": round(ks - float(fits.max() - fits[rel]), 4)})
    keys.sort(key=lambda k: -k["score"])
    return keys, int(np.argmax(fits)), float(fits.max())


def psd(y, sr, n_fft=8192):
    S = stft_mag(y, n_fft, n_fft // 2)
    return np.fft.rfftfreq(n_fft, 1 / sr), (S.astype(np.float64) ** 2).mean(axis=0)


def band_analysis(xf, sr):
    """全采样率功率谱（Welch）积分到 6 个频段；立体声时再算每段的 Side/Mid。"""
    f, p = psd(xf[:, 0], sr)
    if xf.shape[1] == 2:
        p = (p + psd(xf[:, 1], sr)[1]) / 2
        pm, ps = psd(xf.mean(axis=1), sr)[1], psd((xf[:, 0] - xf[:, 1]) / 2, sr)[1]
    tot = p[f >= 20].sum() + 1e-20
    bands = []
    for name, label, lo, hi in BANDS:
        sel = (f >= lo) & (f < hi)
        e = p[sel].sum()
        item = {"band": name, "range": label, "pct": round(100 * e / tot, 2),
                "db": round(10 * math.log10(e / tot + 1e-12), 1)}
        if xf.shape[1] == 2:
            item["side_mid_db"] = round(10 * math.log10((ps[sel].sum() + 1e-20) / (pm[sel].sum() + 1e-20)), 1)
        bands.append(item)
    cum = np.cumsum(np.where(f >= 20, p, 0)) / tot
    q = [float(f[np.searchsorted(cum, v)]) for v in (0.25, 0.5, 0.75)]
    sm = 10 * np.log10(np.convolve(p, np.ones(31) / 31, mode="same") + 1e-20)
    ref = np.median(sm[(f > 1000) & (f < 6000)])
    above = f[(sm > ref - 45) & (f > 6000)]
    cutoff = float(above.max()) if len(above) else 6000.0
    return {"bands": bands, "quartiles_hz": [round(v) for v in q],
            "cutoff_hz": round(cutoff) if cutoff < 0.9 * sr / 2 else None}


def band_summary(ba):
    q25, q50, q75 = ba["quartiles_hz"]
    low = sum(b["pct"] for b in ba["bands"][:2])
    air = ba["bands"][5]["pct"]
    s = f"一半能量落在 {q25}–{q75} Hz（中位 {q50} Hz）；低频(<250 Hz)占 {low:.0f}%"
    s += "，很少" if low < 15 else ("，偏少" if low < 35 else ("，适中" if low < 65 else "，很重"))
    s += f"；>6 kHz 占 {air:.1f}%" + ("，高频很暗" if air < 0.5 else "")
    if ba["cutoff_hz"]:
        s += f"；约 {ba['cutoff_hz'] / 1000:.1f} kHz 以上几乎没有能量（有损编码/录屏常见，air 段会偏低）"
    return s


def centroid_frames(S, sr):
    f = np.fft.rfftfreq((S.shape[1] - 1) * 2, 1 / sr)
    e = S.sum(axis=1)
    return (S @ f) / (e + 1e-12), e


def stereo_analysis(xf):
    L, R = xf[:, 0].astype(np.float64), xf[:, 1].astype(np.float64)
    em, es = np.sum((L + R) ** 2) / 4, np.sum((L - R) ** 2) / 4
    corr = float(np.sum(L * R) / (math.sqrt(np.sum(L ** 2) * np.sum(R ** 2)) + 1e-20))
    sm = 10 * math.log10((es + 1e-20) / (em + 1e-20))
    if es < 1e-9 * (em + 1e-20):
        word = "左右完全相同（其实是单声道）"
    elif corr < 0:
        word = "反相成分多，手机外放/单声道播放会变薄"
    else:
        word = ("几乎是单声道" if sm < -20 else "偏窄" if sm < -12 else "正常宽度" if sm < -5 else "很宽")
    return {"side_mid_db": round(sm, 1), "side_pct": round(100 * es / (em + es + 1e-20), 1),
            "corr": round(corr, 3), "verdict": word}


def timeline(x, mono, cent, cent_e, onsets_t, start, bpm, bpb, bars_per_row, downbeat, source_sr=SR):
    """按小节（已知 BPM）或每 2 秒一格：RMS、onset 密度、质心、宽度。"""
    dur = len(mono) / SR
    rows, edges = [], []
    if bpm:
        bar_s = bpb * 60 / bpm
        t0 = (downbeat or 0) - (start or 0)                     # 第 1 小节在本段里的位置（可能为负）
        if t0 > 0.2:
            edges.append((0.0, t0, "弱起"))
        k = max(0, int(-t0 // (bars_per_row * bar_s)))          # --start 晚于第 1 小节时从对应的小节编号接着数
        while t0 + k * bars_per_row * bar_s < dur - 0.05:
            a = t0 + k * bars_per_row * bar_s
            b1, b2 = k * bars_per_row + 1, (k + 1) * bars_per_row
            edges.append((max(0.0, a), min(dur, a + bars_per_row * bar_s), f"{b1}-{b2}小节"))
            k += 1
    else:
        edges = [(a, min(dur, a + 2), "") for a in np.arange(0, dur - 0.05, 2.0)]
    ft = np.arange(len(cent)) * HOP / SR
    for a, b, label in edges:
        seg = mono[int(a * SR): int(b * SR)]
        sel = (ft >= a) & (ft < b)
        row = {"t0": round(a + (start or 0), 2), "t1": round(b + (start or 0), 2), "label": label,
               "rms_db": round(max(-90.0, db(np.sqrt(np.mean(seg ** 2)) if len(seg) else 0)), 1),
               "onsets_per_s": round(sum(a <= t < b for t in onsets_t) / max(b - a, 1e-3), 2),
               "centroid_hz": round(float(np.sum(cent[sel] * cent_e[sel]) / (cent_e[sel].sum() + 1e-12)))}
        if x.shape[1] == 2:
            st = x[int(a * source_sr): int(b * source_sr)]
            row["side_mid_db"] = round(10 * math.log10((np.sum((st[:, 0] - st[:, 1]) ** 2) + 1e-12) /
                                                         (np.sum((st[:, 0] + st[:, 1]) ** 2) + 1e-12)), 1)
        rows.append(row)
    return rows


def row_name(r):
    return r["label"] or f"{r['t0']:.0f}–{r['t1']:.0f}s"


def merge_rows(rs):
    """相邻的行合并成一个区间，例如 0–2s、2–4s → 0–4s。"""
    out = []
    for r in rs:
        if out and abs(out[-1][1] - r["t0"]) < 0.05:
            out[-1][1] = r["t1"]
        else:
            out.append([r["t0"], r["t1"]])
    return "、".join(f"{a:.1f}–{b:.1f}s" for a, b in out)


def structure_notes(rows):
    """从时间线推断结构：最满、变薄、明显的进入/退出（静音行不参与）。"""
    live = [r for r in rows if r["rms_db"] > -60]
    if not live:
        return ["整段几乎是静音"]
    top = max(live, key=lambda r: r["rms_db"])
    notes = [f"最满：{row_name(top)}（{top['rms_db']} dBFS，onset {top['onsets_per_s']}/s）"]
    thin = [r for r in live if r["rms_db"] <= top["rms_db"] - 6]
    if thin:
        notes.append("变薄（比最满低 6 dB 以上）：" + merge_rows(thin))
    for p, r in zip(live, live[1:]):
        d = r["rms_db"] - p["rms_db"]
        if abs(d) >= 4 and abs(p["t1"] - r["t0"]) < 0.05:
            notes.append(f"{row_name(r)} 起{'变满' if d > 0 else '变薄'} {d:+.1f} dB")
    silent = [r for r in rows if r["rms_db"] <= -60]
    if silent:
        notes.append("静音：" + merge_rows(silent))
    return notes


def analyze(path, start=None, end=None, bpm=None, bpb=4, bars_per_row=4, downbeat=0.0, use_aubio=True):
    info = probe(path)
    full_sr = info["sr"]
    _, xf = load_audio(path, full_sr, start, end)
    _, x = load_audio(path, SR, start, end)
    if len(x) < SR:
        die(f"音频太短（{len(x) / SR:.2f} 秒）：{path}")
    mono = x.mean(axis=1)
    S = stft_mag(mono, 2048, HOP)
    f = np.fft.rfftfreq(2048, 1 / SR)
    onset, onset_low = onset_env(S, SR), flux(S[:, f < 250])
    onsets_t = [i * HOP / SR for i in pick_peaks(onset, FR)]
    peak = float(np.abs(xf).max())
    rms = float(np.sqrt(np.mean(xf.astype(np.float64) ** 2)))
    res = {"file": path, "info": info, "segment": [start or 0.0, (start or 0.0) + len(x) / SR],
           "loudness": loudness(path, start, end, xf, full_sr),
           "levels": {"peak_db": round(db(peak), 2), "rms_db": round(db(rms), 2),
                      "crest_db": round(db(peak) - db(rms), 2)},
           "tempo": estimate_tempo(onset, onset_low, bpm)}
    if res["tempo"].get("ok"):
        res["tempo"]["grid"]["first_beat_s"] = round(res["tempo"]["grid"]["first_beat_s"] + (start or 0), 3)
    if use_aubio:
        res["tempo"]["aubio"] = aubio_tempo(mono)
    chroma, bass, tuning = chroma_peaks(mono)
    keys, collection, fit = estimate_key(chroma)
    res["key"] = {"top": keys[:3], "ks_best": max(keys, key=lambda k: k["r"]), "tuning_cents": round(tuning * 100),
                  "collection": f"{NOTE[collection]} 大调 / {NOTE[(collection + 9) % 12]} 小调",
                  "collection_fit": round(fit, 3),
                  "chroma": [round(float(v), 3) for v in chroma / (chroma.max() + 1e-12)],
                  "bass_top": [NOTE[i] for i in np.argsort(-bass)[:3]]}
    res["bands"] = band_analysis(xf, full_sr)
    cent, cent_e = centroid_frames(S, SR)
    live = cent_e > cent_e.max() * 10 ** (-50 / 20)
    res["centroid"] = {"mean_hz": round(float(np.sum(cent[live] * cent_e[live]) / (cent_e[live].sum() + 1e-12))),
                       "p10_hz": round(float(np.percentile(cent[live], 10))),
                       "p90_hz": round(float(np.percentile(cent[live], 90)))}
    res["stereo"] = stereo_analysis(xf) if xf.shape[1] == 2 else None
    res["timeline"] = timeline(xf, mono, cent, cent_e, onsets_t, start, bpm, bpb, bars_per_row, downbeat, full_sr)
    res["row_bars"], res["row_s"] = bars_per_row, (bars_per_row * bpb * 60 / bpm if bpm else 2.0)
    res["structure"] = structure_notes(res["timeline"])
    return res


# ================================================================ 报告
def fmt(v, unit="", nd=1):
    return "—" if v is None else f"{v:.{nd}f}{unit}"


def print_report(r, bpm_known):
    i, lo, lv = r["info"], r["loudness"], r["levels"]
    print(f"==== 音频分析：{os.path.basename(r['file'])} ====")
    print("1. 基本信息")
    print(f"   时长 {i['duration']:.2f} s（本次分析 {r['segment'][0]:.2f}–{r['segment'][1]:.2f} s）"
          f" | 采样率 {i['sr']} Hz | {i['channels']} 声道 | 编码 {i['codec']}")
    print("2. 响度")
    print(f"   Integrated {fmt(lo['lufs'], ' LUFS')} | LRA {fmt(lo['lra'], ' LU')} | True peak {fmt(lo['true_peak'], ' dBTP')}"
          f"  [{lo['method']}]")
    print(f"   样本峰值 {lv['peak_db']:.1f} dBFS | RMS {lv['rms_db']:.1f} dBFS | Crest factor {lv['crest_db']:.1f} dB")
    tips = []
    v = lo["lufs"]
    if v is not None:
        tips.append("很响（电子/抖音热门曲常见的母带响度）" if v >= -10 else "偏响" if v >= -13
                    else "在流媒体常用的 -14 LUFS 附近" if v >= -16 else "偏轻（放在别的歌旁边会显得小声）")
    if lo["true_peak"] is not None and lo["true_peak"] > -1:
        tips.append("true peak 高于 -1 dBTP，转码成 mp3/aac 后可能削波")
    c = lv["crest_db"]
    tips.append(f"crest {c:.0f} dB：" + ("压缩/限幅很重" if c < 9 else "常见的流行/电子母带动态" if c < 15
                                       else "动态较大（压缩较轻，或安静段落拉低了 RMS）"))
    print("   解读：" + "；".join(tips))
    t = r["tempo"]
    print("3. 速度")
    if not t.get("ok"):
        print("   " + t["note"])
    else:
        print(f"   最可能 {t['bpm']} BPM（拍长 {t['beat_s']} s） | 半速候选 {t['half']} | 倍速候选 {t['double']}")
        for o in t["others"]:
            print(f"   其他峰：{o['bpm']} BPM（强度为最佳的 {o['strength'] * 100:.0f}%{'，= 最佳 ' + o['relation'] if o['relation'] else ''}）")
        g = t["grid"]
        print(f"   拍点网格（按 {g['bpm']} BPM）：{g['first_beat_s']} s + n × {60 / g['bpm']:.4f} s"
              f"  [{g['source']}估计，帧移 23.2 ms，已补偿谱差分偏移；精度依素材而变；看不出哪一拍是小节第 1 拍]")
        a = t.get("aubio")
        if a:
            agree = ""
            if a["bpm"]:
                ok = any(abs(a["bpm"] / (t["bpm"] * m) - 1) < 0.03 for m in (1, 0.5, 2, 2 / 3, 1.5))
                agree = "，与本估计一致（含倍频关系）" if ok else "，与本估计不一致"
            print(f"   aubio 交叉验证：{fmt(a['bpm'], ' BPM', 2)}  [{a['method']}{agree}]")
        sp = f"，多级周期精修一致（±{t['spread_bpm']} BPM）" if t["spread_bpm"] is not None else ""
        print(f"   置信度：{t['confidence']}（节拍清晰度 {t['clarity']}，>0.4 算清晰{sp}）")
        print("   注意：电子乐常有倍频误判，半速/倍速都可能是\"对的\"；3-3-2 切分会让 ×2/3 的峰很强。"
              + (f"已按 --bpm {bpm_known} 画时间线。" if bpm_known else ""))
    k = r["key"]
    print("4. 调性（Krumhansl–Schmuckler 模板 + 音阶集合校正）")
    print(f"   音阶集合（调号）：{k['collection']} 这组音装下 {k['collection_fit'] * 100:.0f}% 的音级能量")
    for n, c in enumerate(k["top"], 1):
        print(f"   {n}. {c['key']:<6} KS 相关系数 r={c['r']:.3f}  音阶内能量 {c['scale_fit'] * 100:.0f}%")
    kb = k["ks_best"]
    if kb["key"] != k["top"][0]["key"]:
        print(f"   （单看 KS 相关最高的是 {kb['key']} r={kb['r']:.3f}，但它的音阶装下的能量更少，所以排在后面）")
    print(f"   低音最常见的音级：{', '.join(k['bass_top'])} | 调音偏移 {k['tuning_cents']:+d} 音分（相对 A=440）")
    gap = k["top"][0]["score"] - k["top"][1]["score"]
    rival = kb["key"] != k["top"][0]["key"] and kb["r"] >= k["top"][0]["r"] - 0.02
    rel = "低" if k["top"][0]["r"] < 0.6 or k["collection_fit"] < 0.65 else (
        "中" if gap < 0.05 or rival or k["collection_fit"] < 0.75 else "高")
    print(f"   可靠程度：{rel}。关系大小调（如 A 小调 / C 大调）共用一组音，主要靠主音和终止和弦区分；"
          "只看整体，转调/调式音乐会被平均掉。")
    for n in range(12):
        print(f"     {NOTE[n]:<2} {bar(k['chroma'][n], 20)} {k['chroma'][n]:.2f}")
    b = r["bands"]
    print("5. 频段能量分布")
    for it in b["bands"]:
        w = f"  宽度 S/M {it['side_mid_db']:+.1f} dB" if "side_mid_db" in it else ""
        print(f"   {it['band']:<9}{it['range']:>11} Hz {bar(it['pct'] / 100, 20)} {it['pct']:5.1f}%  {it['db']:6.1f} dB{w}")
    print("   总结：" + band_summary(b))
    ce = r["centroid"]
    rows = r["timeline"]
    print("6. 频谱质心（亮度）")
    print(f"   平均 {ce['mean_hz']} Hz，10%–90% 区间 {ce['p10_hz']}–{ce['p90_hz']} Hz（22.05 kHz 分析，只到 11 kHz）")
    live = [x for x in rows if x["rms_db"] > -60]
    if len(live) >= 2:
        hi, lo2 = max(live, key=lambda x: x["centroid_hz"]), min(live, key=lambda x: x["centroid_hz"])
        lab = lambda x: x["label"] or f"{x['t0']:.0f}–{x['t1']:.0f}s"
        print(f"   最亮：{lab(hi)}（{hi['centroid_hz']} Hz）| 最暗：{lab(lo2)}（{lo2['centroid_hz']} Hz）")
    print("7. 立体声")
    s = r["stereo"]
    if not s:
        print("   单声道文件，跳过")
    else:
        print(f"   Side/Mid {s['side_mid_db']:+.1f} dB（Side 占 {s['side_pct']}%）| L/R 相关 {s['corr']:+.3f} → {s['verdict']}")
        ws = [x["side_mid_db"] for x in live if "side_mid_db" in x]
        if ws:
            print(f"   分段宽度：最窄 {min(ws):+.1f} dB，最宽 {max(ws):+.1f} dB（见时间线 S/M 列）。手机录屏可能被压窄。")
    print("8. 能量时间线" + (f"（每行 {r['row_bars']} 小节 = {r['row_s']:.2f} s，按 --bpm）" if bpm_known
                            else "（每行 2 秒；加 --bpm 可按小节分格）"))
    top = max((x["rms_db"] for x in rows), default=0)
    for x in rows:
        lab = f"{x['label']:<10}" if x["label"] else ""
        w = f" S/M {x['side_mid_db']:+5.1f}" if "side_mid_db" in x else ""
        print(f"   {lab}{x['t0']:6.1f}–{x['t1']:6.1f}s {bar((x['rms_db'] - top + 30) / 30, 20)} "
              f"{x['rms_db']:6.1f} dB  onset {x['onsets_per_s']:4.1f}/s  质心 {x['centroid_hz']:5d} Hz{w}")
    print("   结构推断：" + "；".join(r["structure"]))


def compare(a, b):
    """并排比较 A（主文件）与 B（--compare）：响度、频段（相对各自总能量）、宽度。"""
    print(f"\n9. 对比：A = {os.path.basename(a['file'])}，B = {os.path.basename(b['file'])}（差值 = B − A）")
    sa, sb = a["stereo"] or {}, b["stereo"] or {}
    rows = [("Integrated LUFS", a["loudness"]["lufs"], b["loudness"]["lufs"]),
            ("LRA (LU)", a["loudness"]["lra"], b["loudness"]["lra"]),
            ("True peak dBTP", a["loudness"]["true_peak"], b["loudness"]["true_peak"]),
            ("RMS dBFS", a["levels"]["rms_db"], b["levels"]["rms_db"]),
            ("Crest dB", a["levels"]["crest_db"], b["levels"]["crest_db"]),
            ("宽度 S/M dB", sa.get("side_mid_db"), sb.get("side_mid_db")),
            ("L/R 相关", sa.get("corr"), sb.get("corr")),
            ("质心 Hz", a["centroid"]["mean_hz"], b["centroid"]["mean_hz"])]
    diff = {}
    for name, va, vb in rows:
        d = None if va is None or vb is None else round(vb - va, 2)
        diff[name] = d
        print(f"   {name:<16}{fmt(va, '', 2):>10}{fmt(vb, '', 2):>10}{'' if d is None else f'{d:+10.2f}'}")
    print("   频段（相对各自总能量）        A dB      B dB    B−A")
    pts = []
    for x, y in zip(a["bands"]["bands"], b["bands"]["bands"]):
        d = y["db"] - x["db"]
        diff[x["band"]] = round(d, 1)
        print(f"   {x['band']:<9}{x['range']:>11} Hz {x['db']:9.1f} {y['db']:9.1f} {d:+7.1f}")
        if abs(d) >= 2:
            pts.append((abs(d), f"{x['band']}({x['range']} Hz) B 比 A {'多' if d > 0 else '少'} {abs(d):.1f} dB"))
    ta, tb = a["tempo"], b["tempo"]
    print(f"   速度 A {ta.get('bpm', '—')} / B {tb.get('bpm', '—')} BPM | 调性 A {a['key']['top'][0]['key']}"
          f" / B {b['key']['top'][0]['key']}")
    print("   要点：")
    for _, p in sorted(pts, reverse=True):
        print("   - " + p)
    if diff["Integrated LUFS"] is not None and abs(diff["Integrated LUFS"]) >= 1:
        print(f"   - 整体响度 B 比 A {'响' if diff['Integrated LUFS'] > 0 else '轻'} {abs(diff['Integrated LUFS']):.1f} LU")
    if diff["宽度 S/M dB"] is not None and abs(diff["宽度 S/M dB"]) >= 2:
        print(f"   - 立体声 B 比 A {'宽' if diff['宽度 S/M dB'] > 0 else '窄'} {abs(diff['宽度 S/M dB']):.1f} dB（S/M）")
    if not pts:
        print("   - 各频段相差都在 2 dB 以内，整体音色平衡接近")
    print("   说明：频段是相对各自总能量的比例，排除了整体音量差；有损编码的文件 air 段天然偏低。")
    return diff


def main():
    p = zh_parser(argparse.ArgumentParser(
        prog="analyze_audio.py", add_help=False, formatter_class=ZhHelp,
        description="把音频量成数字：响度、速度、调性、频段、质心、立体声、能量时间线（中文报告）。",
        epilog="例：analyze_audio.py 参考.mp4 --compare 成品.wav --json out.json"))
    p.add_argument("file", help="要分析的音频/视频文件（mp4/m4a/mp3/wav/aiff/caf…）")
    p.add_argument("--start", type=float, help="从第几秒开始分析")
    p.add_argument("--end", type=float, help="分析到第几秒")
    p.add_argument("--bpm", type=float, help="已知 BPM：时间线按小节分格")
    p.add_argument("--beats-per-bar", type=int, default=4, help="每小节几拍（默认 4）")
    p.add_argument("--downbeat", type=float, default=0.0, help="第 1 小节第 1 拍在文件中的秒数（默认 0）")
    p.add_argument("--bars-per-row", type=int, default=4, help="时间线每行几个小节（默认 4）")
    p.add_argument("--json", help="另存 JSON 结果")
    p.add_argument("--compare", help="对比文件（例如用户成品），并排比较响度/频段/宽度")
    p.add_argument("--compare-start", type=float, help="对比文件从第几秒开始")
    p.add_argument("--compare-end", type=float, help="对比文件分析到第几秒")
    p.add_argument("--no-aubio", action="store_true", help="不跑 aubio 交叉验证")
    p.epilog = "依赖：numpy；非 WAV 解码和响度测量需要 ffmpeg/ffprobe；交叉验证可选 aubiotrack。"
    a = p.parse_args()
    if a.json:
        guard_out(a.json, a.file, a.compare)
    if a.start is not None and a.end is not None and a.end <= a.start:
        die("--end 必须大于 --start")
    res = analyze(a.file, a.start, a.end, a.bpm, a.beats_per_bar, a.bars_per_row, a.downbeat, not a.no_aubio)
    print_report(res, a.bpm)
    out = res
    if a.compare:
        res_b = analyze(a.compare, a.compare_start, a.compare_end, None, a.beats_per_bar, a.bars_per_row, 0.0,
                        use_aubio=False)
        out = {"file": res, "compare": res_b, "diff": compare(res, res_b)}
    if a.json:
        write_text(a.json, json.dumps(out, ensure_ascii=False, indent=1, default=float), a.file, a.compare)
        print(f"\n已写出 JSON：{a.json}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as exc:
        sys.exit(f"错误：{exc}")
