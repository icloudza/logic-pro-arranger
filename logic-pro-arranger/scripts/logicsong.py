#!/usr/bin/env python3
"""logicsong —— 给 Logic Pro 用的多轨 MIDI 生成小库（只用标准库，Python 3.8+）。

一首歌的生成脚本 = SPEC（编曲规格）+ compose(song)（写音符）+ 本库。
new_song.py 会把本库从 "LIB START" 到 "LIB END" 整段复制进生成脚本，
所以每首歌的脚本都能脱离 skill 目录独立运行、多年后还能重新生成。

约定
- 音高：int（MIDI 号）或科学音名 "C#5" / "Bb3"（C4 = 60；Logic 界面把 60 显示成 C3）。
- 位置：bar 从 1 开始；beat 是从该小节起点算的拍数偏移（0 = 第一拍，1.5 = 第二拍后半）。
- 时值：单位是拍（四分音符 = 1）。
- 通道：1–16（鼓 = 10）。
- 人味抖动由 (种子, 轨, 位置, 音高) 的哈希决定，不用顺序随机数：
  改一段不会让别的段落的抖动跟着变，锁定段落能逐音符保持不变。

自测：python3 logicsong.py --selftest
"""
# ==== LIB START ====
import argparse
import math
import os
import struct
import sys
sys.dont_write_bytecode = True
import zlib
import tempfile
import json
from fractions import Fraction
from tool_support import output_path

LIB_VERSION = "0.2.0-r3"
# 人味抖动的定位：section = 段名 + 段内位置（插删段落不改变其他段）；
# absolute = 0.2.0 及更早版本的绝对位置，升级旧作时保持原 MIDI 不变。
JITTER_ANCHORS = ("section", "absolute")

def _number(value, name, low=None, high=None, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} 必须是有限数：{value!r}")
    if (integer and int(value) != value) or (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(f"{name} 超出范围 {low}..{high}：{value!r}")
    return int(value) if integer else value

def _data7(value, name):
    return _number(value, name, 0, 127, True)

def _title(value):
    if not isinstance(value, str) or not value.strip() or value in (".", "..") or any(c in value for c in "/\\:*?\"<>|") or any(ord(c) < 32 for c in value):
        raise ValueError("title 必须是文件名，不能包含路径或控制字符")
    return value

PPQ = 480
_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

GM_DRUMS = {"kick2": 35, "kick": 36, "rim": 37, "snare": 38, "clap": 39, "snare2": 40, "tom_floor": 41,
            "hh": 42, "tom_low": 43, "hh_pedal": 44, "tom_mid": 45, "oh": 46, "tom_mid2": 47, "tom_hi": 48,
            "crash": 49, "tom_hi2": 50, "ride": 51, "china": 52, "ride_bell": 53, "tamb": 54, "splash": 55,
            "cowbell": 56, "crash2": 57, "ride2": 59, "shaker": 70, "claves": 75, "wood_hi": 76, "wood_lo": 77}


# ---------------------------------------------------------------- 音高、音阶、和弦
def nn(p):
    """'C#5' / 'Db5' / 'Bb3' / 61 → MIDI 号（科学音名，C4 = 60）。"""
    if isinstance(p, int):
        return _data7(p, "音高")
    s = str(p).strip()
    if not s or s[0].upper() not in _PC:
        raise ValueError(f"看不懂的音名：{p!r}（例：C#5、Bb3、60）")
    i, acc = 1, 0
    while i < len(s) and s[i] in "#b♯♭":
        acc += 1 if s[i] in "#♯" else -1
        i += 1
    try:
        octave = int(s[i:])
    except ValueError:
        raise ValueError(f"音名缺八度：{p!r}（例：C#5）") from None
    n = _PC[s[0].upper()] + acc + (octave + 1) * 12
    if not 0 <= n <= 127:
        raise ValueError(f"音高超出 MIDI 范围：{p!r} = {n}")
    return n


def pname(n):
    """MIDI 号 → 科学音名（升号写法）。"""
    return f"{_NAMES[n % 12]}{n // 12 - 1}"


def _root_pc(s):
    """解析开头的音名（如 C#、Bb），返回 (音级, 用掉的字符数)。"""
    if not s or s[0].upper() not in _PC:
        raise ValueError(f"看不懂的根音：{s!r}")
    pc, i = _PC[s[0].upper()], 1
    while i < len(s) and s[i] in "#b♯♭":
        pc += 1 if s[i] in "#♯" else -1
        i += 1
    return pc % 12, i


_SCALES = {
    "major": (0, 2, 4, 5, 7, 9, 11), "ionian": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10), "aeolian": (0, 2, 3, 5, 7, 8, 10),
    "harmonic minor": (0, 2, 3, 5, 7, 8, 11), "melodic minor": (0, 2, 3, 5, 7, 9, 11),
    "dorian": (0, 2, 3, 5, 7, 9, 10), "phrygian": (0, 1, 3, 5, 7, 8, 10), "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10), "locrian": (0, 1, 3, 5, 6, 8, 10),
    "pentatonic": (0, 2, 4, 7, 9), "major pentatonic": (0, 2, 4, 7, 9), "minor pentatonic": (0, 3, 5, 7, 10),
    "宫": (0, 2, 4, 7, 9), "商": (0, 2, 5, 7, 10), "角": (0, 3, 5, 8, 10), "徵": (0, 2, 5, 7, 9), "羽": (0, 3, 5, 7, 10),
}


def parse_key(key):
    """'C# minor' / 'A harmonic minor' / 'D dorian' / 'G 宫' → (主音音级, 音阶名)。"""
    s = str(key).strip()
    pc, i = _root_pc(s)
    mode = s[i:].strip().lower() or "major"
    if mode not in _SCALES:
        raise ValueError(f"不支持的调式：{key!r}；可用：{', '.join(_SCALES)}")
    return pc, mode


def scale_pcs(key):
    pc, mode = parse_key(key)
    return [(pc + iv) % 12 for iv in _SCALES[mode]]


def scale_notes(key, lo, hi):
    """lo..hi 之间属于该调的全部 MIDI 号（升序）。"""
    pcs = set(scale_pcs(key))
    return [n for n in range(nn(lo), nn(hi) + 1) if n % 12 in pcs]


def step(pitch, n, key):
    """在调内移动 n 个音级（模进用）：step('E5', -1, 'A minor') → D5。不在调内的音先就近归位。"""
    p = nn(pitch)
    notes = scale_notes(key, 0, 127)
    if p not in notes:
        p = min(notes, key=lambda x: (abs(x - p), x))
    i = notes.index(p) + n
    if not 0 <= i < len(notes):
        raise ValueError("模进超出 MIDI 范围")
    return notes[i]


def key_signature(key):
    """调号 meta 用的 (sf, mi)。调式按其关系大小调折算。"""
    _, mode = parse_key(key)
    tonic = str(key).strip().split()[0].replace("♯", "#").replace("♭", "b")
    minorish = mode in ("minor", "aeolian", "harmonic minor", "melodic minor", "羽", "minor pentatonic")
    fifth = {"C": 0, "D": 2, "E": 4, "F": -1, "G": 1, "A": 3, "B": 5}[tonic[0].upper()]
    fifth += 7 * (tonic[1:].count("#") - tonic[1:].count("b"))
    fifth += -3 if minorish else {"dorian": -2, "phrygian": -4, "lydian": 1,
                                "mixolydian": -1, "locrian": -5, "商": -2, "角": -4, "徵": -1}.get(mode, 0)
    while fifth > 7:
        fifth -= 12
    while fifth < -7:
        fifth += 12
    return fifth, int(minorish)


_QUAL = {
    "": (0, 4, 7), "maj": (0, 4, 7), "M": (0, 4, 7), "m": (0, 3, 7), "min": (0, 3, 7), "-": (0, 3, 7),
    "dim": (0, 3, 6), "aug": (0, 4, 8), "+": (0, 4, 8), "5": (0, 7),
    "sus2": (0, 2, 7), "sus4": (0, 5, 7), "sus": (0, 5, 7),
    "6": (0, 4, 7, 9), "m6": (0, 3, 7, 9), "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "M7": (0, 4, 7, 11),
    "m7": (0, 3, 7, 10), "min7": (0, 3, 7, 10), "m7b5": (0, 3, 6, 10), "dim7": (0, 3, 6, 9), "mmaj7": (0, 3, 7, 11),
    "7sus4": (0, 5, 7, 10), "add9": (0, 4, 7, 14), "madd9": (0, 3, 7, 14), "add2": (0, 2, 4, 7),
    "9": (0, 4, 7, 10, 14), "maj9": (0, 4, 7, 11, 14), "m9": (0, 3, 7, 10, 14), "6/9": (0, 4, 7, 9, 14),
    "7b9": (0, 4, 7, 10, 13), "7#9": (0, 4, 7, 10, 15), "7b13": (0, 4, 7, 10, 20), "11": (0, 4, 7, 10, 14, 17),
    "m11": (0, 3, 7, 10, 14, 17), "13": (0, 4, 7, 10, 14, 21), "maj13": (0, 4, 7, 11, 14, 21),
}


def parse_chord(sym):
    """'C#m7' / 'Bbmaj7' / 'Gsus4' / 'C/E' / 'Am(add9)' → (根音音级, 音程元组, 低音音级或 None)。"""
    s = str(sym).strip().replace("(", "").replace(")", "").replace("△", "maj").replace("ø7", "m7b5").replace("ø", "m7b5").replace("°7", "dim7").replace("°", "dim")
    bass = None
    if "/" in s and not s.endswith("6/9"):
        s, b = s.rsplit("/", 1)
        bass, _ = _root_pc(b)
    root, i = _root_pc(s)
    q = s[i:]
    if q not in _QUAL:
        raise ValueError(f"不支持的和弦写法：{sym!r}；后缀可用：{', '.join(k for k in _QUAL if k)}")
    return root, _QUAL[q], bass


def chord_pcs(sym):
    root, ivs, bass = parse_chord(sym)
    pcs = {(root + iv) % 12 for iv in ivs}
    if bass is not None:
        pcs.add(bass)
    return pcs


def chord_notes(sym, octave=4):
    """原位排列（根音在给定八度），斜线和弦把低音放在最下面。"""
    root, ivs, bass = parse_chord(sym)
    base = root + (octave + 1) * 12
    notes = [base + iv for iv in ivs]
    if bass is not None:
        b = bass + (octave + 1) * 12
        while b >= notes[0]:
            b -= 12
        notes.insert(0, b)
    return notes


def voicing(sym, low, high, prev=None, size=None):
    """在 [low, high] 里给和弦找一个密集排列；有 prev 时选和上一个排列移动最少的（声部进行）。"""
    low, high = nn(low), nn(high)
    root, ivs, bass = parse_chord(sym)
    order = [(root + iv) % 12 for iv in ivs]          # 根、三、五/七、延伸音，按重要性
    seen, pcs = set(), []
    for pc in order:
        if pc not in seen:
            seen.add(pc)
            pcs.append(pc)
    size = size or min(len(pcs), 4)
    if size < len(pcs):
        # Keep the named extension, third and seventh; omit fifth, then inner extensions.
        priorities = [ivs[-1]] + [iv for iv in ivs if iv in (3, 4, 10, 11)] + [0]
        priorities += list(reversed(ivs))
        keep = list(dict.fromkeys((root + iv) % 12 for iv in priorities))[:size]
        pcs = [pc for pc in pcs if pc in keep]
    cands = []
    for start in range(low, high + 1):
        if start % 12 not in pcs:
            continue
        rest = [p for p in pcs if p != start % 12]
        v, cur = [start], start
        for _ in rest:
            nxt = min((cur + ((p - cur) % 12 or 12)) for p in rest if (p not in [x % 12 for x in v]))
            v.append(nxt)
            cur = nxt
        if v[-1] <= high:
            cands.append(v)
    if not cands:
        raise ValueError(f"{sym} 在 {pname(low)}–{pname(high)} 里排不下 {size} 个音，放宽音域或减少音数")
    if prev:
        prev = sorted(nn(x) for x in prev)

        def cost(v):
            a, b = sorted(v), prev
            k = min(len(a), len(b))
            return sum(abs(a[i] - b[i]) for i in range(k)) + abs(len(a) - len(b)) * 6
        return min(cands, key=cost)
    mid = (low + high) / 2
    third = pcs[1] if len(pcs) > 1 else pcs[0]
    # 没有上一个和弦时：靠近音域中间，并尽量让根音或三音在最下面（7 音/9 音在底听起来像转位）
    return min(cands, key=lambda v: abs(sum(v) / len(v) - mid) + (0 if v[0] % 12 in (pcs[0], third) else 6))


ROLES = {"3", "5", "7", "8", "10", "12", "15"}


def role_pitch(sym, role, lo=40):
    """和弦 + 角色 → MIDI 号（伴奏型、贝斯型用）。R = 根音（斜线和弦取低音），落在 lo..lo+11；
    3/5/7/8/10/12/15 相对根音（sus 和弦的 3 取挂留音）；别的字符串当固定音名（"E5"）。"""
    if role != "R" and role not in ROLES:
        return nn(role)
    root, ivs, bass = parse_chord(sym)
    if role == "R":
        return lo + ((root if bass is None else bass) - lo) % 12
    r = lo + (root - lo) % 12
    third = next((i for i in ivs if i in (3, 4)), ivs[1] if len(ivs) > 1 else 4)
    fifth = next((i for i in ivs if i in (6, 7, 8)), 7)
    sev = next((i for i in ivs if i in (9, 10, 11)), 12)
    return r + {"3": third, "5": fifth, "7": sev, "8": 12, "10": 12 + third, "12": 12 + fifth, "15": 24}[role]


# ---------------------------------------------------------------- 工具
def _h01(*parts):
    """稳定的伪随机数 [-1, 1)：同样的输入永远同样的输出。"""
    return zlib.crc32(":".join(str(p) for p in parts).encode()) / 2 ** 31 - 1.0


def _vlq(n):
    n = _number(n, "VLQ", 0, 0x0FFFFFFF, True)
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def _meta(kind, data):
    return bytes([0xFF, kind]) + _vlq(len(data)) + data


def _chunk(events):
    """events: [(tick, 优先级, bytes)]；同一 tick 上 note-off(0) 先于控制器(1) 先于 note-on(2)。"""
    events.sort(key=lambda e: (e[0], e[1]))
    body, last = bytearray(), 0
    for t, _, data in events:
        _number(t, "事件 tick", 0, integer=True)
        if data and 0x80 <= data[0] <= 0xEF:
            size = 2 if data[0] & 0xF0 in (0xC0, 0xD0) else 3
            if len(data) != size or any(v > 127 for v in data[1:]):
                raise ValueError("非法 MIDI 数据字节")
        body += _vlq(t - last) + data
        last = t
    body += _vlq(0) + b"\xFF\x2F\x00"
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


# ---------------------------------------------------------------- 轨
class Track:
    def __init__(self, song, cfg):
        self.song, self.cfg = song, cfg
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.channel = _number(cfg.get("channel", 1), "channel", 1, 16, True)
        if not 1 <= self.channel <= 16:
            raise ValueError(f"轨 {self.id}: channel 要在 1–16（鼓 = 10）")
        self.is_drum = self.channel == 10
        self.program = cfg.get("program", 0)
        if self.program is not None:
            _data7(self.program, "program")
        self.range = [nn(x) for x in cfg["range"]] if cfg.get("range") else None
        self.push_ms = float(cfg.get("push_ms", 0))       # 正 = 靠后（拖），负 = 抢拍
        self.jitter_ms = float(cfg.get("jitter_ms", 0))   # 小于 5ms 基本听不出来，别当成人味
        self.vel_jitter = float(cfg.get("vel_jitter", 0))
        self.init_cc = {7: cfg.get("volume", 100), 10: cfg.get("pan", 64), 91: cfg.get("reverb", 40), 64: 0,
                        11: cfg.get("expression", 127)}
        for num, val in self.init_cc.items():
            _data7(val, f"CC{num}")
        self.nominal = {}
        self.notes = []            # [start_tick, end_tick, pitch, velocity]
        self.ctrl = []             # [(tick, bytes)]
        self.out_of_range = []

    # ---- 写音符
    def note(self, bar, beat, dur, pitch, vel=80, push_ms=None, human=True):
        _number(dur, "音符时长", 1 / PPQ)
        _number(vel, "力度", 1, 127, True)
        p, t = nn(pitch), self.song.tick(bar, beat)
        if self.song.jitter_anchor == "absolute":
            musical = (t,)
        else:
            sec = self.song.section_at(bar)
            musical = (sec["name"], t - self.song.tick(sec["start"]))
        off = self.push_ms if push_ms is None else push_ms
        if human and self.jitter_ms:
            off += _h01(self.song.seed, self.id, *musical, p, "t") * self.jitter_ms
        start = max(0, t + self.song.ms_to_ticks(off, t))
        v = vel + (_h01(self.song.seed, self.id, *musical, p, "v") * self.vel_jitter if human and self.vel_jitter else 0)
        end = start + max(1, int(round(dur * PPQ)))
        self.notes.append([start, end, p, max(1, min(127, int(round(v))))])
        self.nominal[(start, p)] = t
        if self.range and not self.range[0] <= p <= self.range[1]:
            self.out_of_range.append((bar, beat, p))

    def chord(self, bar, beat, dur, pitches, vel=80, strum_ms=0, up=True, low=48, high=72, prev=None):
        """pitches 可以是音高列表，也可以是和弦名（自动在 low–high 里排列）。strum_ms = 扫弦相邻两音的间隔。"""
        notes = voicing(pitches, low, high, prev) if isinstance(pitches, str) else [nn(x) for x in pitches]
        order = sorted(notes) if up else sorted(notes, reverse=True)
        for i, p in enumerate(order):
            self.note(bar, beat, dur, p, vel, push_ms=(self.push_ms + i * strum_ms))
        return notes

    def line(self, bar, beat, text, vel=80, accent=8, arc=0):
        """连续写旋律："E5:1.5 D5:0.5 r:1 C5:1 | B4:2 ..."。
        记号：音名:拍长[:力度][:偏移毫秒]，力度可留空（"E5:1::+15" = 默认力度、晚 15ms），r = 休止；
        "|" 表示这里应正好是小节线（对不上就报错，防止节奏算错）。
        强拍（每小节第一拍）自动 +accent 力度；arc>0 时整句力度做一个弧线（中段最多 +arc，两头 0），
        写乐句的"推上去再收"。返回结束位置 (bar, beat)。"""
        self.song.tick(bar, beat)
        toks = [t for t in text.split() if t != "|"]
        total = sum(float(Fraction(t.split(":")[1])) for t in toks) or 1.0
        pos0 = pos = (bar - 1) * self.song.beats_per_bar + beat
        for tok in text.split():
            if tok == "|":
                if abs(pos / self.song.beats_per_bar - round(pos / self.song.beats_per_bar)) > 1e-6:
                    b, bt = self.song.split_pos(pos)
                    raise ValueError(f"轨 {self.id}: '|' 处不在小节线上（现在是第 {b} 小节第 {bt:g} 拍后）")
                continue
            parts = tok.split(":")
            d = _number(float(Fraction(parts[1])), "时值", 1 / PPQ)
            if parts[0].lower() != "r":
                b, bt = self.song.split_pos(pos)
                v = int(parts[2]) if len(parts) > 2 and parts[2] else vel + (accent if abs(bt) < 1e-6 else 0)
                if arc:
                    v += arc * math.sin(math.pi * min(1.0, (pos - pos0) / total))
                off = float(parts[3]) if len(parts) > 3 and parts[3] else None
                self.note(b, bt, d, parts[0], int(round(v)), push_ms=None if off is None else self.push_ms + off)
            pos += d
        return self.song.split_pos(pos)

    def pad(self, bars, low, high, vel=60, size=None, legato=0.0):
        """长音和弦（铺底/弦乐）：按段落和弦逐个排列，和上一个和弦共同的音不重按、直接延长（声部进行更顺）。
        legato>0 时每个新音提前这么多拍进（慢起音音色用）。返回最后一个排列。"""
        _number(vel, "力度", 1, 127, True)
        held, prev = {}, None                      # pitch -> 音符（list，可延长）
        for b in bars:
            for beat, sym, dur in self.song.chords_in(b):
                if not sym or sym == "%":
                    continue
                v = voicing(sym, low, high, prev, size)
                t0, t1 = self.song.tick(b, beat), self.song.tick(b, beat + dur)
                for p in list(held):
                    if p not in v or held[p][1] < t0:
                        held.pop(p)
                for p in v:
                    if p in held:
                        held[p][1] = t1                 # 共同音：延长
                    else:
                        start = max(0, t0 - int(round(legato * PPQ)))
                        n = [start, t1, p, int(vel)]
                        self.notes.append(n)
                        held[p] = n
                prev = v
        return prev

    def grid(self, bar, pattern, pitch, vel=90, step=0.25, dur=None, accent=15, ghost=30):
        """鼓/音型格子：'x' 正常、'X' 重音(+accent)、'o' 幽灵音(力度=ghost)、'.' 或 '-' 休止；空格和 '|' 忽略。
        默认一格 = 十六分音符；超过一小节会自动接到下一小节。"""
        p = GM_DRUMS.get(pitch, None) if isinstance(pitch, str) and pitch in GM_DRUMS else nn(pitch)
        cells = [c for c in pattern if c not in " |"]
        for i, c in enumerate(cells):
            if c in ".-":
                continue
            pos = (bar - 1) * self.song.beats_per_bar + i * step
            b, bt = self.song.split_pos(pos)
            v = vel + accent if c == "X" else ghost if c == "o" else vel
            self.note(b, bt, dur or step * 0.9, p, v)

    def hit(self, bar, beat, drum, vel=90, dur=0.25):
        """单击一个鼓件：drum 用 GM_DRUMS 里的名字（kick/snare/clap/hh/oh/crash…）或 MIDI 号。"""
        self.note(bar, beat, dur, GM_DRUMS[drum] if isinstance(drum, str) else drum, vel)

    def groove(self, bar, pat, drum, vel=90, swing=50, push=0, human=False, accent=15, ghost=30, step=0.25, start=0):
        """grid() 加强版（references/rhythm-grooves/notation.md）：swing% 把偶数步推后（50 = 直）；
        push = 这一行的固定偏移 ms（正 = 靠后，替代整轨 push_ms）；human=True 才吃这一轨的 jitter_ms
        （kick、crash 守网格，hat、鬼音才抖）。drum 可写鼓件名 / MIDI 号 / 音名。"""
        p = GM_DRUMS.get(drum, drum) if isinstance(drum, str) else drum
        cells = [c for c in pat if c not in " |"]
        for i, c in enumerate(cells):
            if c in ".-":
                continue
            beat = start + i * step + ((swing / 50 - 1) * step if i % 2 else 0)
            v = vel + accent if c == "X" else ghost if c == "o" else vel
            self.note(bar, beat, step * 0.9, p, v, push_ms=push, human=human)

    def roll(self, bar, beat, n, step, drum="snare", v0=55, v1=105):
        """滚奏：从 (bar, beat) 起 n 下、间隔 step 拍（1/4 = 16 分，1/8 = 32 分，1/6 = 16 分三连），力度 v0 → v1。"""
        for k in range(n):
            self.hit(bar, beat + k * step, drum, round(v0 + (v1 - v0) * k / max(1, n - 1)), dur=step * 0.8)

    def comp(self, bar, rows, vel=70, lo=40, rh=(60, 76), gate=0.9, mono=False, prev=None):
        """角色网格 → 伴奏 / 低音（按 SPEC 段落和弦）。rows = {"R": "x...x...", "5": "..x...x.", "RH": "....x..."}；
        每个音延到下一个起音 × gate（mono=True 看所有行，否则只看本行）；X = +12、o = −20；
        "RH" 行 = 右手密集和弦（rh 音域，跟着 prev 走声部）。返回右手排列，下一次传回 prev。"""
        rows = {r: [c for c in p if c not in " |"] for r, p in rows.items()}
        ev = sorted((i, r) for r, cells in rows.items() for i, c in enumerate(cells) if c not in ".-")
        size = max(len(c) for c in rows.values())
        for i, r in ev:
            nxt = [j for j, r2 in ev if j > i and (mono or r2 == r)]
            beat, dur = i * 0.25, ((nxt[0] if nxt else size) - i) * 0.25 * gate
            sym, c = self.song.chord(bar, beat), rows[r][i]
            if not sym or sym == "%":
                continue
            v = vel + 12 if c == "X" else vel - 20 if c == "o" else vel
            if r == "RH":
                prev = self.chord(bar, beat, dur, sym, v, low=rh[0], high=rh[1], prev=prev)
            else:
                self.note(bar, beat, dur, role_pitch(sym, r, lo), v)
        return prev

    def strum(self, bar, pat, vel=85, ms=12, gate=0.92, alldown=False, prev=None):
        """扫弦（按 SPEC 段落和弦）：有偶数步起音就按 16 分摆臂（奇数步下、偶数步上），否则按 8 分（拍点下、反拍上）。
        下扫 = 低音弦根音 + 和弦、低 → 高；上扫 = 上面 3 个音、高 → 低、力度 −15；o = 切音（极短）。返回排列。"""
        cells = [c for c in pat if c not in " |"]
        six = any(c not in ".-" for c in cells[1::2])
        hits = [i for i, c in enumerate(cells) if c not in ".-"]
        for k, i in enumerate(hits):
            sym = self.song.chord(bar, i * 0.25)
            if not sym or sym == "%":
                continue
            prev = voicing(sym, 55, 72, prev)
            full = [role_pitch(sym, "R", 40)] + prev
            if cells[i] == "o":
                self.chord(bar, i * 0.25, 0.06, full, 62, strum_ms=2)
                continue
            dur = ((hits[k + 1] if k + 1 < len(hits) else len(cells)) - i) * 0.25 * gate
            down = alldown or (i % 2 == 0 if six else i % 4 == 0)
            v = vel + (10 if cells[i] == "X" else 0) - (0 if down else 15)
            self.chord(bar, i * 0.25, dur, full if down else prev[-3:], v, strum_ms=ms if down else ms * 0.6, up=down)
        return prev

    # ---- 控制器
    def cc(self, bar, beat, num, value):
        t = self.song.tick(bar, beat)
        self.ctrl.append((t, bytes([0xB0 | (self.channel - 1), _data7(num, "CC 编号"), int(round(_number(value, "CC 值", 0, 127)))])))

    def ramp(self, num, start, end, v0, v1, step=0.125, curve=1.0):
        """CC 渐变：start/end = (bar, beat)；curve>1 先慢后快，<1 先快后慢，"s" = S 形（两头缓、中间快，弦乐 swell 用）。"""
        t0, t1 = self.song.tick(*start), self.song.tick(*end)
        _data7(num, "CC 编号")
        _number(v0, "CC 起值", 0, 127)
        _number(v1, "CC 终值", 0, 127)
        _number(step, "渐变步长", 1 / PPQ)
        if curve != "s":
            _number(curve, "curve", 1e-9)
        if t1 < t0:
            raise ValueError("渐变终点早于起点")
        n = max(1, int((t1 - t0) / (step * PPQ)))
        for i in range(n + 1):
            x = i / n
            x = x * x * (3 - 2 * x) if curve == "s" else x ** curve
            self.ctrl.append((t0 + (t1 - t0) * i // n,
                              bytes([0xB0 | (self.channel - 1), num, max(0, min(127, int(round(v0 + (v1 - v0) * x))))])))

    def pump(self, bars, shape="中", cc=11, end=127):
        """用 CC11 模拟侧链泵感（MIDI 层面没有真侧链）：每拍拍点瞬间压低，到八分反拍回到高位。
        shape：轻(85→127) / 中(55→124) / 重(40→120)。用在铺底、贝斯、和弦、琶音上，鼓和主旋律不用；
        音色要响应 CC11。最后一小节之后写回 end。"""
        a = {"轻": (85, 105, 120, 127), "中": (55, 85, 110, 124), "重": (40, 70, 100, 120)}[shape]
        bars = list(bars)
        for b in bars:
            for k in range(int(self.song.beats_per_bar)):
                for t in range(0, PPQ, 30):
                    j, f = divmod(t, PPQ // 4)
                    nxt = a[j + 1] if j < 3 else a[3]
                    self.cc(b, k + t / PPQ, cc, a[j] + (nxt - a[j]) * f / (PPQ // 4))
        if bars:
            self.cc(max(bars) + 1, 0, cc, end)

    def bend(self, bar, beat, semitones, bend_range=2):
        """弯音：semitones 相对于弯音范围（默认 ±2 半音）。"""
        _number(bend_range, "bend_range", 1e-9)
        _number(semitones, "弯音半音", -bend_range, bend_range)
        v = int(round(8192 + max(-1, min(1, semitones / bend_range)) * 8191))
        v = max(0, min(16383, v))
        self.ctrl.append((self.song.tick(bar, beat), bytes([0xE0 | (self.channel - 1), v & 0x7F, v >> 7])))

    def pedal(self, bar, beat=0, down=True):
        """钢琴延音踏板 CC64。换和弦时先抬再踩：pedal(b, 0, False) 后紧跟 pedal(b, 0.03)。"""
        self.cc(bar, beat, 64, 127 if down else 0)

    def repedal(self, bars, lift=10, press=60):
        """每个小节换一次踏板：小节线前 lift tick 抬起，小节线后 press tick（默认 1/8 拍）再踩下。
        踩得太早会把上一和弦还没松开的拖尾音接住、糊进新和弦（check_midi 的 T13 专查这个）；
        所以上一和弦的音要在 press 之前结束，push 为负的轨更要留意。"""
        _number(lift, "lift", 0, integer=True)
        _number(press, "press", 0, integer=True)
        for b in bars:
            t = self.song.tick(b, 0)
            ch = 0xB0 | (self.channel - 1)
            self.ctrl.append((max(0, t - lift), bytes([ch, 64, 0])))
            self.ctrl.append((t + press, bytes([ch, 64, 127])))

    # ---- 输出
    def _events(self):
        ev, ch = [], self.channel - 1
        ev.append((0, 1, _meta(0x03, self.name.encode("utf-8"))))
        if self.program is not None:
            ev.append((0, 1, bytes([0xC0 | ch, _data7(self.program, "program")])))
        for num, val in self.init_cc.items():
            ev.append((0, 1, bytes([0xB0 | ch, _data7(num, "CC 编号"), _data7(val, "CC 值")])))
        for t, data in self.ctrl:
            ev.append((t, 1, data))
        by_pitch = {}
        for n in sorted(self.notes):
            _number(n[0], "音符起点", 0, integer=True)
            _number(n[1], "音符终点", n[0] + 1, integer=True)
            _data7(n[2], "移调后音高")
            _number(n[3], "力度", 1, 127, True)
            lst = by_pitch.setdefault(n[2], [])
            if lst and lst[-1][0] == n[0]:             # 同一时刻同音高写了两次：合并成一个（取长的、响的）
                lst[-1][1], lst[-1][3] = max(lst[-1][1], n[1]), max(lst[-1][3], n[3])
            else:
                lst.append(list(n))
        for lst in by_pitch.values():                  # 同音高重叠会吞音：前一个截到后一个之前
            for a, b in zip(lst, lst[1:]):
                if a[1] > b[0]:
                    a[1] = max(a[0] + 1, b[0])
            for s, e, p, v in lst:
                nominal = self.nominal.get((s, p), s)
                if nominal != s:
                    ev.append((s, 1, _meta(0x7F, b"LSN1:" + json.dumps([p, nominal-s]).encode())))
                ev.append((s, 2, bytes([0x90 | ch, p, v])))
                ev.append((e, 0, bytes([0x80 | ch, p, 0])))
        return ev


# ---------------------------------------------------------------- 歌
class Song:
    def __init__(self, spec, script_path=None):
        self.spec = spec
        self.script_path = script_path
        self.build_id = globals().get("_SONG_BUILD_ID") or (os.path.realpath(script_path) if script_path else None)
        self.title = _title(spec.get("title", "未命名"))
        self.seed = spec.get("seed", 20261005)
        self.jitter_anchor = globals().get("_JITTER_ANCHOR", "section")
        if self.jitter_anchor not in JITTER_ANCHORS:
            raise ValueError(f"_JITTER_ANCHOR 只能是 {' / '.join(JITTER_ANCHORS)}")
        meter = spec.get("meter", [4, 4])
        self.meter = (_number(meter[0], "拍号分子", 1, 32, True), _number(meter[1], "拍号分母", 1, 32, True))
        if self.meter[1] not in (1, 2, 4, 8, 16, 32):
            raise ValueError("拍号分母必须是 1/2/4/8/16/32")
        self.beats_per_bar = self.meter[0] * 4 / self.meter[1]
        self.bar_ticks = int(round(PPQ * self.beats_per_bar))
        self.key = spec.get("key")
        if self.key:
            parse_key(self.key)
        self.tempo_map = self._parse_tempo(spec.get("tempo_map") or [[1, spec["bpm"]]])
        if self.tempo_map[0][0] != (1, 0.0):
            raise ValueError("tempo_map 第一项必须从第 1 小节第 0 拍开始")
        # 段落：只写长度，起始小节自动算（改前奏长度不用手动挪后面的小节号）
        self.sections, start, names = [], 1, set()
        for s in spec["sections"]:
            _number(s["bars"], "段落小节数", 1, integer=True)
            if s["name"] in names:
                raise ValueError(f"段落名重复：{s['name']}（第二遍请写成 副歌2 这样）")
            names.add(s["name"])
            sec = dict(s, start=start, end=start + int(s["bars"]) - 1)
            sec["_chords"] = self._parse_chords(s.get("chords", ""), int(s["bars"]), s["name"])
            self.sections.append(sec)
            start += int(s["bars"])
        self.total_bars = start - 1
        self.tracks, self._order, self.warnings = {}, [], []
        for cfg in spec["tracks"]:
            if cfg["id"] in self.tracks:
                raise ValueError(f"轨 id 重复：{cfg['id']}")
            for k in ("enter", "exit"):
                if cfg.get(k) not in (None, "end", "start") and cfg[k] not in names:
                    self.warnings.append(f"轨 {cfg['id']} 的 {k}={cfg[k]!r} 不是段落名（只是计划信息，不影响生成）")
            self.tracks[cfg["id"]] = Track(self, cfg)
            self._order.append(cfg["id"])
        chans = {}
        for t in self.tracks.values():
            chans.setdefault(t.channel, []).append(t.id)
        self.shared_channels = {c: ids for c, ids in chans.items() if len(ids) > 1}

    def _parse_tempo(self, tm):
        """tempo_map 项：[小节, BPM]、[小节, BPM, 拍]，或渐变 {"ramp": [起小节, 止小节], "from": 72, "to": 64, "step": 1}
        （渐变按 step 拍一个点，从起小节第 0 拍到止小节第 0 拍线性变化）。返回 [((bar, beat), bpm)] 升序。"""
        pts = []
        for it in tm:
            if isinstance(it, dict):
                b0, b1 = it["ramp"]
                step = _number(float(it.get("step", 1)), "速度步长", 1 / PPQ)
                if b1 <= b0:
                    raise ValueError("速度渐变终点必须晚于起点")
                n = max(1, int(round((b1 - b0) * self.beats_per_bar / step)))
                for i in range(n + 1):
                    pos = (b0 - 1) * self.beats_per_bar + i * step
                    bar = int(pos // self.beats_per_bar) + 1
                    pts.append(((bar, round(pos - (bar - 1) * self.beats_per_bar, 6)),
                                float(it["from"]) + (float(it["to"]) - float(it["from"])) * i / n))
            else:
                pts.append(((int(it[0]), float(it[2]) if len(it) > 2 else 0.0), float(it[1])))
        for (bar, beat), bpm in pts:
            self.tick(bar, beat)
            _number(bpm, "BPM", 4, 999)
        pts.sort(key=lambda x: x[0])
        return pts

    # ---- 和弦
    def _parse_chords(self, text, bars, name):
        groups = [g.strip() for g in str(text).split("|")] if text else []
        while groups and not groups[-1]:
            groups.pop()
        out, previous = [], None
        if len(groups) > bars:
            raise ValueError(f"{name}: 和弦小节数超过段落长度")
        for g in groups:
            if g == "%":
                if not out or not out[-1]:
                    raise ValueError(f"{name}: % 前没有可重复的小节")
                out.append(list(out[-1]))
                continue
            toks, cur, beat = g.split(), [], 0.0
            explicit = any(":" in t for t in toks)
            for tok in toks:
                fields = tok.rsplit(":", 1)
                sym = fields[0]
                d = float(fields[1]) if len(fields) == 2 else (self.beats_per_bar - beat if explicit else self.beats_per_bar / len(toks))
                _number(d, "和弦时长", 1 / PPQ)
                if beat + d > self.beats_per_bar + 1e-9:
                    raise ValueError(f"{name}: 和弦时长超过一小节：{g}")
                if sym == "%":
                    if previous is None:
                        raise ValueError(f"{name}: % 前没有和弦")
                    sym = previous
                parse_chord(sym)
                cur.append((beat, sym, d))
                previous, beat = sym, beat + d
            out.append(cur)
        return out

    def chords_in(self, bar):
        """该小节的 [(拍, 和弦, 拍长)]；段落和弦比小节数少时循环使用。"""
        sec = self.section_at(bar)
        ch = sec["_chords"]
        return ch[(bar - sec["start"]) % len(ch)] if ch else []

    def chord(self, bar, beat=0.0):
        cur = None
        for b, sym, d in self.chords_in(bar):
            if b <= beat + 1e-9 and beat < b + d - 1e-9:
                cur = sym
        return cur

    # ---- 位置与时间
    def section(self, name):
        for s in self.sections:
            if s["name"] == name:
                return s
        raise KeyError(f"没有叫 {name!r} 的段落；有：{', '.join(s['name'] for s in self.sections)}")

    def start(self, name):
        return self.section(name)["start"]

    def bars(self, name):
        s = self.section(name)
        return list(range(s["start"], s["end"] + 1))

    def section_at(self, bar):
        for s in self.sections:
            if s["start"] <= bar <= s["end"]:
                return s
        return self.sections[-1]

    def tick(self, bar, beat=0.0):
        _number(bar, "小节", 1, integer=True)
        _number(beat, "拍位")
        tick = (bar - 1) * self.bar_ticks + beat * PPQ
        _number(tick, "时间位置（弱起需预留起始小节）", 0)
        return int(round(tick))

    def split_pos(self, beats_from_start):
        b = int(math.floor(beats_from_start / self.beats_per_bar + 1e-9))
        return b + 1, beats_from_start - b * self.beats_per_bar

    def bpm_at(self, tick):
        bpm = self.tempo_map[0][1]
        for (bar, beat), v in self.tempo_map:
            if self.tick(bar, beat) <= tick:
                bpm = v
        return bpm

    def ms_to_ticks(self, ms, tick=0):
        return int(round(ms * self.bpm_at(tick) * PPQ / 60000))

    def seconds(self, bar, beat=0.0):
        """从开头到 (bar, beat) 的秒数（考虑变速）。"""
        target, sec = self.tick(bar, beat), 0.0
        pts = [(self.tick(b, bt), v) for (b, bt), v in self.tempo_map] + [(10 ** 12, None)]
        for (t0, bpm), (t1, _) in zip(pts, pts[1:]):
            if target <= t0:
                break
            sec += (min(target, t1) - t0) / PPQ * 60 / bpm
        return sec

    def duration(self):
        return self.seconds(self.total_bars + 1)

    def track(self, tid):
        if tid not in self.tracks:
            raise KeyError(f"没有 id 为 {tid!r} 的轨；有：{', '.join(self._order)}")
        return self.tracks[tid]

    # ---- 写文件
    def _finish(self):
        for t in self.tracks.values():                 # copy_of：同样的音符给另一个音色轨，用来在 Logic 里比较
            src = t.cfg.get("copy_of")
            if src:
                s = self.track(src)
                tr = int(t.cfg.get("transpose", 0))
                t.notes = [[a, b, p + tr, v] for a, b, p, v in s.notes]
                t.nominal = {(a, p + tr): n for (a, p), n in s.nominal.items()}
                t.ctrl = [(tk, bytes([d[0] & 0xF0 | (t.channel - 1)]) + d[1:]) for tk, d in s.ctrl]

    def write(self, out_dir=None, force=False):
        self._finish()
        out_dir = out_dir or (os.path.dirname(os.path.abspath(self.script_path)) if self.script_path else ".")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{self.title}.mid")
        cond = [(0, 1, _meta(0x03, self.title.encode("utf-8"))),
                (0, 1, _meta(0x58, bytes([self.meter[0], int(math.log2(self.meter[1])), 24, 8])))]
        cond.append((0, 1, _meta(0x7F, b"LSM1:" + json.dumps({"bars": self.total_bars, "version": LIB_VERSION, "producer": self.build_id}).encode())))
        if self.key:
            sf, mi = key_signature(self.key)
            cond.append((0, 1, _meta(0x59, struct.pack(">bB", sf, mi))))
        for (bar, beat), bpm in self.tempo_map:
            cond.append((self.tick(bar, beat), 1, _meta(0x51, int(round(60_000_000 / bpm)).to_bytes(3, "big"))))
        for s in self.sections:
            cond.append((self.tick(s["start"]), 1, _meta(0x06, s["name"].encode("utf-8"))))
        chunks = [_chunk(cond)] + [_chunk(self.tracks[i]._events()) for i in self._order]
        data = b"MThd" + struct.pack(">IHHH", 6, 1, len(chunks), PPQ) + b"".join(chunks)
        owner = "logicsong:" + self.build_id if self.build_id else None
        with output_path(path, self.script_path, force=force, owner=owner) as temp:
            temp.write_bytes(data)
        return path

    def report(self, path):
        """打印摘要和快速提醒（完整检查请跑 check_midi.py）。"""
        dur = self.duration()
        print(f"写出 {path}")
        print(f"  {self.total_bars} 小节 ≈ {dur:.1f}s（{int(dur // 60)}:{dur % 60:04.1f}）"
              f"  {self.tempo_map[0][1]:g} BPM  {self.meter[0]}/{self.meter[1]}  {self.key or '未写调'}")
        print("  段落：" + "  ".join(f"{s['name']}@{s['start']}({s['bars']})" for s in self.sections))
        warn = list(self.warnings)
        tgt = self.spec.get("target_seconds")
        if tgt and not tgt[0] <= dur <= tgt[1]:
            warn.append(f"时长 {dur:.1f}s 不在目标 {tgt[0]}–{tgt[1]}s 内")
        for i in self._order:
            t = self.tracks[i]
            ps = [n[2] for n in t.notes]
            last = max((n[1] for n in t.notes), default=0)
            rng = f"{pname(min(ps))}–{pname(max(ps))}" if ps else "—"
            first_bar = min(n[0] for n in t.notes) // self.bar_ticks + 1 if ps else None
            print(f"  {t.name:<8} ch{t.channel:<2} prog={'鼓' if t.is_drum else t.program}  音符 {len(ps):4d}  "
                  f"{rng:<9} 进 {first_bar or '—'}  出 {(last - 1) // self.bar_ticks + 1 if ps else '—'}")
            if not ps:
                warn.append(f"{t.name} 没有任何音符")
            if t.out_of_range:
                b, bt, p = t.out_of_range[0]
                warn.append(f"{t.name}: {len(t.out_of_range)} 个音超出声明音域，第一个在 {b} 小节 {bt:g} 拍 {pname(p)}")
            if last > self.tick(self.total_bars + 1) + 4 * PPQ:
                warn.append(f"{t.name}: 有音符超出段落表的总长（{self.total_bars} 小节）")
        for c, ids in self.shared_channels.items():
            warn.append(f"通道 {c} 被多条轨共用：{', '.join(ids)}")
        for w in warn:
            print("  ⚠ " + w)
        if not warn:
            print("  快速检查无提醒。完整检查：python3 <skill>/scripts/check_midi.py 这首.mid --script 生成脚本.py")


def main(spec, compose, script_file):
    ap = argparse.ArgumentParser(description=f"生成 {spec.get('title', '')}.mid")
    ap.add_argument("--out", help="输出目录（默认：脚本所在目录）")
    ap.add_argument("--force", action="store_true", help="覆盖已有 MIDI；自己上次生成且未被外部修改的产物无需此参数")
    ap.add_argument("--version", action="version", version=LIB_VERSION)
    a = ap.parse_args()
    song = Song(spec, script_file)
    try:
        compose(song)
        song.report(song.write(a.out, force=a.force))
    except (ValueError, OSError) as exc:
        ap.exit(2, f"错误：{exc}\n")
    return song
# ==== LIB END ====


def _selftest():
    import tempfile
    assert nn("C4") == 60 and nn("C#5") == 73 and nn("Bb3") == 58 and pname(61) == "C#4"
    assert step("E5", -1, "A minor") == nn("D5") and step("G#4", 1, "A harmonic minor") == nn("A4")
    assert chord_pcs("C#m") == {1, 4, 8} and chord_pcs("C/E") == {0, 4, 7}
    assert key_signature("C# minor") == (4, 1) and key_signature("Bb major") == (-2, 0)
    assert key_signature("A minor") == (0, 1) and key_signature("D dorian") == (0, 0)
    v1 = voicing("Am", 57, 72)
    v2 = voicing("F", 57, 72, prev=v1)
    assert all(57 <= x <= 72 for x in v1 + v2) and chord_pcs("F") >= {x % 12 for x in v2}
    spec = {"title": "selftest", "bpm": 120, "key": "A minor", "target_seconds": [8, 9],
            "sections": [{"name": "A", "bars": 2, "chords": "Am | F G"}, {"name": "B", "bars": 2, "chords": "C"}],
            "tracks": [{"id": "p", "program": 0, "channel": 1, "range": ["C4", "C6"], "push_ms": -10},
                       {"id": "p2", "channel": 2, "copy_of": "p"}, {"id": "d", "channel": 10}]}

    def compose(s):
        p = s.track("p")
        end = p.line(1, 0, "E5:1 D5:0.5 C5:0.5 r:2 | A4:4")
        assert end == (3, 0.0), end
        p.chord(3, 0, 4, "C", 70, low=55, high=72)
        p.ramp(11, (1, 0), (2, 0), 60, 110)
        s.track("d").grid(1, "x...x...x...x...", "kick", 100)
        assert s.chord(2, 2) == "G" and s.chord(4) == "C" and s.bars("B") == [3, 4]
    s = Song(spec, None)
    compose(s)
    with tempfile.TemporaryDirectory() as d:
        path = s.write(d)
        data = open(path, "rb").read()
    assert data[:4] == b"MThd" and data.count(b"MTrk") == 4
    assert abs(s.duration() - 8.0) < 1e-6
    # push_ms=-10 → 120BPM 下约 -10 tick
    assert min(n[0] for n in s.track("p").notes) == 0 and s.track("p").notes[1][0] == PPQ - s.ms_to_ticks(10)
    # m7b5 三音排列要保留 ♭5（省根音）
    assert {x % 12 for x in voicing("Bm7b5", 55, 76, size=3)} == {2, 5, 9}
    # 渐慢：第 3 小节起 120 → 60，每拍一个点；line 的力度弧线与逐音偏移；pad 共同音不重按
    spec2 = dict(spec, title="selftest2", tempo_map=[[1, 120], {"ramp": [3, 4], "from": 120, "to": 60, "step": 1}],
                 tracks=[{"id": "p", "channel": 1}, {"id": "q", "channel": 2}])
    s2 = Song(spec2, None)
    assert len(s2.tempo_map) == 6 and s2.tempo_map[-1][1] == 60.0 and s2.tempo_map[2] == ((3, 1.0), 105.0)
    assert abs(s2.seconds(3) - 4.0) < 1e-6 and s2.duration() > 8.0
    q = s2.track("q")
    q.line(1, 0, "C5:1 D5:1::+20 E5:1 F5:1", vel=70, accent=0, arc=10)
    vs = [n[3] for n in q.notes]
    assert vs[0] == 70 and max(vs) > 70 and q.notes[1][0] == PPQ + s2.ms_to_ticks(20)
    v = s2.track("p").pad([1, 2], 55, 72, 60)
    held = sorted(s2.track("p").notes)
    assert any(n[1] > s2.tick(2) for n in held if n[0] < s2.tick(2)), "共同音应延长跨过小节线"
    spec3 = {"title": "selftest3", "bpm": 100, "key": "A minor",
             "sections": [{"name": "A", "bars": 2, "chords": "Am | F G"}],
             "tracks": [{"id": "d", "channel": 10, "jitter_ms": 10}, {"id": "p", "channel": 1}, {"id": "g", "channel": 2}]}
    s3 = Song(spec3)
    d3, p3, g3 = s3.track("d"), s3.track("p"), s3.track("g")
    d3.groove(1, "x... x... x... x...", "kick", 90)
    d3.groove(1, ".x.x .x.x .x.x .x.x", "hh", 60, swing=58, human=True)
    assert d3.notes[0][0] == 0 and len(d3.notes) == 12
    d3.roll(2, 2, 8, 0.25, "snare", 50, 100)
    assert d3.notes[-1][3] == 100
    pv = p3.comp(1, {"R": "x.......x.......", "RH": "....x.......x..."}, 70, lo=40)
    assert role_pitch("Am", "R", 40) == 45 and role_pitch("C/E", "R", 40) == 40 and role_pitch("Am", "5", 40) == 52
    gv = g3.strum(2, "x.x.x.x.x.x.x.x.", 85)
    assert gv and len(g3.notes) > 8
    print("selftest ok（临时文件已清理）")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(__doc__)
