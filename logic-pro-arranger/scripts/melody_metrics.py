#!/usr/bin/env python3
"""旋律描述统计与粗略轮廓检索（标准库）。
统计值不代表好听、情绪、原创性或风格质量。顶声部抽取可能误认伴奏。
"""
import argparse
import os
import sys
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))

# 简谱级数：相对"关系大调主音"的半音 → 级数文字（只认调内音）
_JIANPU = {0: "1", 2: "2", 4: "3", 5: "4", 7: "5", 9: "6", 11: "7"}
# 旧统计字段的短音型计数，仅供兼容与检索，不判断套语或质量
CLICHES = {"533": ["5", "3", "3"], "563": ["5", "6", "3"], "176": ["1", "7", "6"],
           "2362": ["2", "3", "6", "2"], "2361": ["2", "3", "6", "1"]}


def _lib():
    sys.path.insert(0, HERE)
    sys.dont_write_bytecode = True
    import logicsong as L
    return L


# ---------------------------------------------------------------- 取单旋律线
def mono_line(notes, ppq, tol_ticks=None):
    """把（可能带和弦、带伴奏的）音符变成单旋律线（skyline 顶声部）：
    同一时刻（tol 以内）起音的只留最高音；如果上面还有一个更高的旋律音在延续，下面新起的伴奏音不算旋律；
    每个音的长度截到下一个旋律起音为止。notes 接受 (s, e, p, v) 或 check_midi 的 (s, e, ch, p, v)。"""
    tol = ppq // 32 if tol_ticks is None else tol_ticks
    ns = sorted((n[0], n[1], n[-2], n[-1]) for n in notes)
    groups, cur = [], []
    for n in ns:
        if cur and n[0] - cur[0][0] > tol:
            groups.append(cur)
            cur = []
        cur.append(n)
    if cur:
        groups.append(cur)
    line = []
    for g in groups:
        top = max(g, key=lambda x: x[2])
        if line:
            ps, pe, pp, pv = line[-1]
            if pe > top[0] + ppq // 16 and pp > top[2]:      # 上面的旋律音还在响：这是伴奏
                continue
        line.append(top)
    out = []
    for i, (s, e, p, v) in enumerate(line):
        if i + 1 < len(line):
            e = min(e, line[i + 1][0]) if line[i + 1][0] > s else e
        out.append((s, max(e, s + 1), p, v))
    return out


def window(line, t0, t1):
    return [n for n in line if t0 <= n[0] < t1]


# ---------------------------------------------------------------- 和弦时间线（从 SPEC）
class ChordMap:
    """用 logicsong.Song 解析 SPEC 的段落与和弦，提供 tick → 和弦音级集合。解析失败时 ok=False。"""

    def __init__(self, spec):
        self.ok, self.song, self.key = False, None, None
        if not spec or not spec.get("sections"):
            return
        try:
            L = _lib()
            sp = dict(spec)
            sp["tracks"] = []
            self.song = L.Song(sp)
            self.L = L
            self.key = spec.get("key")
            self.ok = True
        except Exception as e:            # 旧脚本的 SPEC 不一定能解析，检查降级为 NOT RUN
            self.err = str(e)

    def sym_at(self, tick):
        if not self.ok:
            return None
        s = self.song
        bar = tick // s.bar_ticks + 1
        if bar > s.total_bars:
            return None
        beat = (tick - (bar - 1) * s.bar_ticks) / self.L.PPQ
        sym = s.chord(bar, beat)
        return None if not sym or sym == "%" else sym

    def pcs_at(self, tick):
        sym = self.sym_at(tick)
        if not sym:
            return None
        try:
            return self.L.chord_pcs(sym)
        except Exception:
            return None

    def root_at(self, tick):
        sym = self.sym_at(tick)
        if not sym:
            return None
        try:
            return self.L.parse_chord(sym)[0]
        except Exception:
            return None

    def bar_chords(self, bars):
        """这些小节第一拍的和弦名列表（给进行签名用）。"""
        if not self.ok:
            return []
        out = []
        for b in bars:
            for _, sym, _ in self.song.chords_in(b):
                if sym and sym != "%":
                    out.append(sym)
        return out


# ---------------------------------------------------------------- 调与级数
def rel_major_pc(key):
    """关系大调主音音级（简谱的"1"）。"""
    if not key:
        return None
    L = _lib()
    try:
        pc, mode = L.parse_key(key)
    except Exception:
        return None
    shift = {"minor": 3, "aeolian": 3, "harmonic minor": 3, "melodic minor": 3, "羽": 3, "minor pentatonic": 3,
             "dorian": 10, "商": 10, "phrygian": 8, "角": 8, "lydian": 7, "mixolydian": 5, "徵": 5,
             "locrian": 1}.get(mode, 0)
    return (pc + shift) % 12


def jianpu(p, one_pc):
    return _JIANPU.get((p - one_pc) % 12)


# ---------------------------------------------------------------- 指标
def metrics(line, ppq, beats_per_bar=4, chords=None, key=None, strong_beats=None):
    """line：mono_line 的结果（已截到要看的窗口）。chords：ChordMap 或 None。返回指标字典。"""
    if len(line) < 2:
        return {"n": len(line)}
    bar_ticks = int(round(ppq * beats_per_bar))
    pitches = [n[2] for n in line]
    ivs = [b - a for a, b in zip(pitches, pitches[1:])]
    n_iv = len(ivs)
    steps = sum(abs(i) <= 2 for i in ivs)
    skips = sum(3 <= abs(i) <= 4 for i in ivs)
    leaps = [k for k, i in enumerate(ivs) if abs(i) >= 5]
    recovered = sum(1 for k in leaps if k + 1 < n_iv and ivs[k + 1] * ivs[k] < 0 and abs(ivs[k + 1]) <= 4)
    span_t0, span_t1 = line[0][0], max(n[1] for n in line)
    span = max(1, span_t1 - span_t0)
    hi = max(pitches)
    peak_idx = [k for k, p in enumerate(pitches) if p == hi]
    q = ppq // 4                                      # 十六分
    # 切分：起音不在拍点上，而且持续跨过下一个拍点（抢拍 / 后半拍延续）
    sync = 0
    for s, e, p, v in line:
        rel = (s - (s // ppq) * ppq)
        r16 = int(round(rel / q)) % 4
        if r16 != 0:
            next_beat = (s // ppq + 1) * ppq
            if e >= next_beat + q // 2:
                sync += 1
    # 强拍起音分类；不将和弦外的音自动判为错误
    sb = strong_beats if strong_beats is not None else ([0, 2] if beats_per_bar == 4 else [0])
    strong_on, strong_ct, strong_color, strong_res, beat_on, beat_ct = 0, 0, 0, 0, 0, 0
    if chords is not None and getattr(chords, "ok", False):
        for k, (s, e, p, v) in enumerate(line):
            pos16 = int(round((s % bar_ticks) / q))
            if pos16 % 4 != 0:
                continue
            tick = (s // bar_ticks) * bar_ticks + pos16 * q
            pcs = chords.pcs_at(tick)
            if pcs is None:
                continue
            beat = pos16 // 4
            beat_on += 1
            is_ct = (p % 12) in pcs
            beat_ct += is_ct
            if beat in sb:
                strong_on += 1
                if is_ct:
                    strong_ct += 1
                    continue
                root = chords.root_at(tick)
                if root is not None and (p - root) % 12 == 2:
                    strong_color += 1
                    continue
                if k + 1 < len(line):
                    ns, ne, np_, nv = line[k + 1]
                    npcs = chords.pcs_at(ns)
                    if 1 <= abs(np_ - p) <= 2 and ns - s <= int(1.5 * ppq) and npcs and (np_ % 12) in npcs:
                        strong_res += 1
    # 每小节节奏签名（十六分位置），看动机复用
    bars = {}
    for s, e, p, v in line:
        b = s // bar_ticks
        bars.setdefault(b, []).append(int(round((s - b * bar_ticks) / q)))
    sigs = [frozenset(x) for x in bars.values() if len(x) >= 2]

    def jac(a, b):
        return len(a & b) / max(1, len(a | b))
    reuse = 0
    for i, a in enumerate(sigs):
        if any(jac(a, b) >= 0.7 for j, b in enumerate(sigs) if j != i):
            reuse += 1
    # 两小节完全重复（音高 + 节奏）
    exact_pairs = 0
    seqs = {}
    for s, e, p, v in line:
        b = s // bar_ticks
        seqs.setdefault(b, []).append((int(round((s - b * bar_ticks) / q)), p))
    keys = sorted(seqs)
    for a, b in zip(keys, keys[1:]):
        if seqs[a] == seqs[b]:
            exact_pairs += 1
    # 套语与句尾
    one = rel_major_pc(key)
    degs = [jianpu(p, one) for p in pitches] if one is not None else []
    cl = {}
    if degs:
        for name, pat in CLICHES.items():
            k = len(pat)
            cl[name] = sum(1 for i in range(len(degs) - k + 1) if degs[i:i + k] == pat)
    ends = phrase_ends(line, ppq)
    tonic_pc = None
    if key:
        try:
            tonic_pc = _lib().parse_key(key)[0]
        except Exception:
            tonic_pc = None
    end_on_tonic = sum(1 for n in ends if tonic_pc is not None and n[2] % 12 == tonic_pc)
    nbars = max(1, round(span / bar_ticks))
    return {
        "n": len(line), "range": hi - min(pitches), "lowest": min(pitches), "highest": hi,
        "step_ratio": round(steps / n_iv, 3), "skip_ratio": round(skips / n_iv, 3),
        "leap_ratio": round(len(leaps) / n_iv, 3), "leaps": len(leaps),
        "leap_recovered": round(recovered / len(leaps), 3) if leaps else None,
        "repeat_ratio": round(sum(i == 0 for i in ivs) / n_iv, 3),
        "peak_count": len(peak_idx), "peak_pos": round((line[peak_idx[0]][0] - span_t0) / span, 3),
        "notes_per_bar": round(len(line) / nbars, 2), "bars": nbars,
        "sync": sync, "sync_ratio": round(sync / len(line), 3),
        "strong_ct": round(strong_ct / strong_on, 3) if strong_on else None, "strong_n": strong_on,
        "strong_ok": round((strong_ct + strong_color + strong_res) / strong_on, 3) if strong_on else None,
        "strong_color": strong_color, "strong_resolved": strong_res,
        "beat_ct": round(beat_ct / beat_on, 3) if beat_on else None,
        "rhythm_reuse": round(reuse / len(sigs), 3) if sigs else None, "exact_bar_repeats": exact_pairs,
        "cliches": {k: v for k, v in cl.items() if v},
        "phrase_ends": len(ends), "ends_on_tonic": end_on_tonic,
        "banned": banned_shapes(line, ppq),
    }


def phrase_ends(line, ppq):
    """句尾：后面跟着 ≥ 1 拍休止的音，或时值 ≥ 1.5 拍的音，以及最后一个音。"""
    out = []
    for i, n in enumerate(line):
        if i + 1 == len(line):
            out.append(n)
            continue
        gap = line[i + 1][0] - n[1]
        if gap >= ppq or (n[1] - n[0]) >= int(1.5 * ppq):
            out.append(n)
    return out


def banned_shapes(line, ppq):
    """旧版命名保留兼容：检索一种上升后回落轮廓，不代表禁止使用。形状：两个上行的短音（≤ 3/4 拍）冲进一个长音（≥ 1.25 拍，
    是局部最高点），接着至少两步级进下行。返回 [(音符序号, 起始 tick, 'strict'|'loose')]。
    loose = 只有 1 个上行短音或用跳进冲上长音、再级进下行（同一家族的惯用型）。"""
    out = []
    P = [n[2] for n in line]
    D = [n[1] - n[0] for n in line]
    for i in range(len(line) - 3):
        # strict: i, i+1 短且上行，i+2 长且最高，i+3, i+4 级进下行
        if i + 4 < len(line):
            if (D[i] <= 0.75 * ppq and D[i + 1] <= 0.75 * ppq and P[i] < P[i + 1] < P[i + 2]
                    and D[i + 2] >= 1.25 * ppq and -2 <= P[i + 3] - P[i + 2] < 0 and -2 <= P[i + 4] - P[i + 3] < 0):
                out.append((i, line[i][0], "strict"))
                continue
        # loose: i 上行冲进 i+1 长音（最高），然后 i+2, i+3 级进下行
        if i + 3 < len(line):
            if (P[i] < P[i + 1] and D[i + 1] >= 1.25 * ppq and P[i + 1] - P[i] >= 1
                    and -2 <= P[i + 2] - P[i + 1] < 0 and -2 <= P[i + 3] - P[i + 2] <= 0):
                if not any(o[0] in (i - 1, i) for o in out):
                    out.append((i, line[i][0], "loose"))
    return out


# ---------------------------------------------------------------- 指纹（song_history 用）
def contour_tokens(line, ppq, n=8):
    """前 n 个音的"轮廓 + 节奏"记号：方向（R 同音 / u d 级进 / U D 跳进）+ 时值（s ≤ 八分、m ≤ 四分、l 更长）。"""
    toks = []
    seq = line[:n]
    for a, b in zip(seq, seq[1:]):
        iv = b[2] - a[2]
        d = "R" if iv == 0 else ("u" if 0 < iv <= 2 else "U" if iv > 2 else "d" if -2 <= iv < 0 else "D")
        dur = a[1] - a[0]
        r = "s" if dur <= 0.6 * ppq else "m" if dur <= 1.1 * ppq else "l"
        toks.append(d + r)
    return toks


def _lev(a, b):
    """编辑距离；方向相同或时值相同的替换只算半个差异。"""
    prev = [float(j) for j in range(len(b) + 1)]
    for i, x in enumerate(a, 1):
        cur = [float(i)]
        for j, y in enumerate(b, 1):
            sub = 0.0 if x == y else (0.5 if (x[0] == y[0] or x[1:] == y[1:]) else 1.0)
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + sub))
        prev = cur
    return prev[-1]


def similarity(tok_a, tok_b):
    """两个 hook 开头的相似度 0–1（方向和时值都比；同方向不同时值算半个差异）。"""
    if not tok_a or not tok_b:
        return 0.0
    k = min(len(tok_a), len(tok_b), 7)
    a, b = tok_a[:k], tok_b[:k]
    return round(1 - _lev(a, b) / k, 3)


# ---------------------------------------------------------------- 描述统计

def hook_checks(hid, line, ppq, bar_ticks, chords, key, card, style, fmt, seconds=None):
    """兼容旧调用接口；只描述，不套用风格比例或禁用轮廓。"""
    if len(line) < 2:
        return []
    v = metrics(line, ppq, bar_ticks / ppq, chords, key)
    out = [("M1", "INFO", f"hook「{hid}」音域 {v['range']} 半音，共 {v['n']} 个音", ""),
           ("M4", "INFO", f"同音/级进比例 {v['step_ratio']:.0%}，最高音出现 {v['peak_count']} 次，切分 {v['sync']} 处", "")]
    if v.get('strong_ct') is not None:
        out.append(("M13", "INFO", f"抽样强拍起音中和弦音占 {v['strong_ct']:.0%}（样本 {v['strong_n']}）；不设目标比例", ""))
    return out


def song_checks(line, ppq, bar_ticks, sections, card, seconds=None):
    """按给定段落报告音域；不要求副歌更高或更密。"""
    out = []
    for s in sections:
        ns = window(line, (s['start'] - 1) * bar_ticks, s['end'] * bar_ticks)
        if ns:
            lo, hi = min(n[2] for n in ns), max(n[2] for n in ns)
            out.append(("M2", "INFO", f"{s['name']}：{len(ns)} 个起音，MIDI {lo}–{hi}", ""))
    return out


# ---------------------------------------------------------------- 命令行
def main():
    sys.path.insert(0, HERE)
    sys.dont_write_bytecode = True
    import check_midi as C
    ap = argparse.ArgumentParser(description="看一条旋律的量化指标")
    ap.add_argument("midi")
    ap.add_argument("--track", required=True, help="轨名（MIDI 里的轨名，也就是 SPEC 的 name）")
    ap.add_argument("--script", help="生成脚本（读 SPEC 的调与和弦，算强拍和弦音、套语）")
    ap.add_argument("--bars", help="只看这些小节，如 1-8")
    a = ap.parse_args()
    spec = C.load_spec(a.script)[0] if a.script else None
    m = C.Midi(a.midi)
    tr = next((t for t in m.tracks if t["name"] == a.track), None)
    if not tr:
        sys.exit("没找到轨：" + a.track + "；有：" + "、".join(t["name"] for t in m.tracks if t["name"]))
    line = mono_line(tr["notes"], m.ppq)
    bpb = (m.timesigs[0][1] * 4 / m.timesigs[0][2]) if m.timesigs else 4
    if a.bars:
        b0, b1 = (int(x) for x in a.bars.split("-"))
        line = window(line, m.tick_of_bar(b0), m.tick_of_bar(b1 + 1))
    key = (spec or {}).get("key")
    res = metrics(line, m.ppq, bpb, ChordMap(spec) if spec else None, key)
    for k, v in res.items():
        print(f"{k:>16}: {v}")
    print(f"{'contour':>16}: {' '.join(contour_tokens(line, m.ppq))}")


if __name__ == "__main__":
    main()
