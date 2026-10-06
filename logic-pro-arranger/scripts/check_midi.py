#!/usr/bin/env python3
"""MIDI 技术检查、编曲观察与锁定段落比较（标准库）。
ERROR 导致退出码 1；音乐统计不构成审美评分。历史比较用 --history 开启。
"""
import argparse
import ast
import json
import os
import statistics as st
import struct
import sys
sys.dont_write_bytecode = True

TOMS = {41, 43, 45, 47, 48, 50}
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
SEV_ORDER = {"ERROR": 0, "WARN": 1, "INFO": 2, "豁免": 3, "OK": 4, "NOT RUN": 5}


def pname(n):
    return f"{NAMES[n % 12]}{n // 12 - 1}"


# ---------------------------------------------------------------- 读 SMF
def _vlq(d, i):
    v = 0
    for _ in range(4):
        if i >= len(d):
            raise ValueError("MIDI 截断：VLQ")
        byte = d[i]
        i += 1
        v = (v << 7) | (byte & 127)
        if not byte & 128:
            return v, i
    raise ValueError("MIDI VLQ 超过 4 字节")


def _parse_track(d):
    ev, i, t, run = [], 0, 0, None
    while i < len(d):
        dt, i = _vlq(d, i)
        t += dt
        if i >= len(d):
            raise ValueError("MIDI 截断：事件状态")
        b = d[i]
        if b == 0xFF:
            run = None
            if i + 1 >= len(d):
                raise ValueError("MIDI 截断：meta 类型")
            kind = d[i + 1]
            ln, j = _vlq(d, i + 2)
            if j + ln > len(d):
                raise ValueError("MIDI 截断：meta 内容")
            payload = d[j:j + ln]
            lengths = {0x2F: 0, 0x51: 3, 0x58: 4, 0x59: 2}
            if kind in lengths and ln != lengths[kind]:
                raise ValueError("MIDI meta 长度错误")
            if kind == 0x51 and int.from_bytes(payload, "big") == 0:
                raise ValueError("MIDI tempo 不能为零")
            if kind == 0x58 and (payload[0] == 0 or payload[1] > 7):
                raise ValueError("MIDI 拍号无效")
            if kind == 0x59 and (not -7 <= struct.unpack('b', payload[:1])[0] <= 7 or payload[1] > 1):
                raise ValueError("MIDI 调号无效")
            ev.append((t, "meta", kind, payload))
            i = j + ln
            if kind == 0x2F:
                if i != len(d):
                    raise ValueError("MIDI 轨道结束后仍有数据")
                return ev, t
            continue
        if b in (0xF0, 0xF7):
            run = None
            ln, j = _vlq(d, i + 1)
            i = j + ln
            if i > len(d):
                raise ValueError("MIDI 截断：SysEx")
            continue
        if b & 0x80:
            if not 0x80 <= b <= 0xEF:
                raise ValueError(f"非法 MIDI 状态字节：{b:#x}")
            run = b
            i += 1
        if run is None:
            raise ValueError("MIDI 数据损坏（缺状态字节）")
        hi, ch = run & 0xF0, run & 0x0F
        size = 1 if hi in (0xC0, 0xD0) else 2
        if i + size > len(d):
            raise ValueError("MIDI 截断：数据字节")
        payload = tuple(d[i:i + size])
        if any(v > 127 for v in payload):
            raise ValueError("MIDI 数据字节必须在 0–127")
        ev.append((t, hi, ch, payload))
        i += size
    raise ValueError("MIDI 轨道缺少 End of Track")


class Midi:
    def __init__(self, path, data=None):
        self.path = path
        if data is None:
            with open(path, "rb") as handle:
                data = handle.read()
        if len(data) < 14 or data[:4] != b"MThd":
            raise ValueError(f"不是 MIDI 文件：{path}")
        hlen = struct.unpack(">I", data[4:8])[0]
        self.fmt, ntrk, self.ppq = struct.unpack(">HHH", data[8:14])
        if hlen < 6 or 8 + hlen > len(data) or self.fmt not in (0, 1) or ntrk < 1 or (self.fmt == 0 and ntrk != 1) or self.ppq == 0:
            raise ValueError("MIDI 文件头无效或不支持此格式")
        if self.ppq & 0x8000:
            raise ValueError("不支持 SMPTE 时间格式")
        pos, raw = 8 + hlen, []
        while pos < len(data) and len(raw) < ntrk:
            if pos + 8 > len(data):
                raise ValueError("MIDI 截断：轨道头")
            ln = struct.unpack(">I", data[pos + 4:pos + 8])[0]
            if pos + 8 + ln > len(data):
                raise ValueError("MIDI 截断：轨道数据")
            if data[pos:pos + 4] == b"MTrk":
                raw.append(_parse_track(data[pos + 8:pos + 8 + ln]))
            pos += 8 + ln
        if len(raw) != ntrk or pos != len(data):
            raise ValueError("MIDI 轨道数量或文件长度不符")
        self.tempos, self.timesigs, self.keysigs, self.markers = [], [], [], []
        self.declared_bars = None
        self.producer = None
        self.tracks = []
        for idx, (events, end) in enumerate(raw):
            tr = {"index": idx, "name": "", "notes": [], "programs": [], "cc": {}, "bends": 0,
                  "events": events, "channels": set(), "overlaps": 0, "dups": 0, "unclosed": 0, "end": end}
            open_ = {}
            for t, kind, a, b in events:
                if kind == "meta":
                    if a == 0x7F and b.startswith(b"LSM1:"):
                        metadata = json.loads(b[5:])
                        value = metadata.get("bars")
                        self.producer = metadata.get("producer")
                        if not isinstance(value, int) or not 1 <= value <= 100000:
                            raise ValueError("MIDI 段落长度元数据无效")
                        self.declared_bars = value
                    elif a == 0x51:
                        self.tempos.append((t, int.from_bytes(b, "big")))
                    elif a == 0x58:
                        self.timesigs.append((t, b[0], 2 ** b[1]))
                    elif a == 0x59:
                        self.keysigs.append((t, struct.unpack(">b", b[:1])[0], b[1]))
                    elif a == 0x06:
                        self.markers.append((t, b.decode("utf-8", "replace")))
                    elif a == 0x03 and not tr["name"]:
                        tr["name"] = b.decode("utf-8", "replace")
                    continue
                ch = a
                tr["channels"].add(ch + 1)
                if kind == 0x90 and b[1] > 0:
                    key = (ch, b[0])
                    if open_.get(key):
                        if any(s0 == t for s0, _ in open_[key]):
                            tr["dups"] += 1
                        else:
                            tr["overlaps"] += 1
                    open_.setdefault(key, []).append((t, b[1]))
                elif kind in (0x80, 0x90):
                    key = (ch, b[0])
                    if open_.get(key):
                        s, v = open_[key].pop(0)
                        tr["notes"].append((s, t, ch + 1, b[0], v))
                elif kind == 0xC0:
                    tr["programs"].append((t, ch + 1, b[0]))
                elif kind == 0xB0:
                    tr["cc"].setdefault(b[0], []).append((t, ch + 1, b[1]))
                elif kind == 0xE0:
                    tr["bends"] += 1
            for key, lst in open_.items():
                for s, v in lst:
                    tr["unclosed"] += 1
                    tr["notes"].append((s, end, key[0] + 1, key[1], v))
            tr["notes"].sort()
            self.tracks.append(tr)
        self.tempos.sort()
        self.has_tempo = bool(self.tempos)
        if not self.tempos or self.tempos[0][0] > 0:
            self.tempos.insert(0, (0, 500000))
        self.timesigs.sort()
        self.markers.sort()
        self.end_tick = max([n[1] for tr in self.tracks for n in tr["notes"]] + [0])
        self._bars()

    def _bars(self):
        """每小节起点 tick（支持拍号变化）。"""
        sigs = self.timesigs or [(0, 4, 4)]
        if sigs[0][0] > 0:
            sigs = [(0, 4, 4)] + sigs
        starts, t, k = [], 0, 0
        limit = max(self.end_tick, self.markers[-1][0] if self.markers else 0) + 1
        while t <= limit or len(starts) < 2:
            while k + 1 < len(sigs) and sigs[k + 1][0] <= t:
                k += 1
            starts.append(t)
            num, den = sigs[k][1], sigs[k][2]
            t += int(self.ppq * num * 4 / den)
        self.bar_starts = starts

    def bar_of(self, tick):
        lo, hi = 0, len(self.bar_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.bar_starts[mid] <= tick:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    def tick_of_bar(self, bar):
        while bar - 1 >= len(self.bar_starts):
            self.bar_starts.append(self.bar_starts[-1] + (self.bar_starts[-1] - self.bar_starts[-2]))
        return self.bar_starts[bar - 1]

    def seconds(self, tick):
        sec, pts = 0.0, self.tempos + [(10 ** 12, None)]
        for (t0, us), (t1, _) in zip(pts, pts[1:]):
            if tick <= t0:
                break
            sec += (min(tick, t1) - t0) / self.ppq * us / 1e6
        return sec

    def bpm_at(self, tick):
        us = self.tempos[0][1]
        for t, u in self.tempos:
            if t <= tick:
                us = u
        return 60e6 / us


# ---------------------------------------------------------------- SPEC
def load_spec(path):
    """从生成脚本里读出 SPEC 字典（只接受纯字面量，不执行脚本）。也接受 .json。"""
    if path.endswith(".json"):
        return json.load(open(path, encoding="utf-8")), None
    tree = ast.parse(open(path, encoding="utf-8").read())
    version = None
    from logicsong import LIB_VERSION
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'LIB_VERSION' for t in node.targets):
            try: version = ast.literal_eval(node.value)
            except (ValueError, TypeError): pass
    version_note = None if version == LIB_VERSION else f"内嵌库版本 {version or '未标记'}，当前 {LIB_VERSION}；logicsong 内嵌脚本可用 upgrade_song.py 另存升级，自定义生成器需人工迁移"

    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "SPEC" for t in node.targets):
            try:
                return ast.literal_eval(node.value), version_note
            except ValueError:
                return None, "SPEC 里有变量或表达式（不是纯字面量），SPEC 相关检查没跑"
    return None, "脚本里没找到 SPEC"


# ---------------------------------------------------------------- 分析
def bar_tol(m, tick):
    """小节线前约 100 ms 内的起音（人味抖动、push 提前、弦乐提前量）算到下一小节。"""
    return max(m.ppq // 8, int(round(0.1 * m.bpm_at(tick) * m.ppq / 60)))


def analyze(m, spec):
    rep = {"file": m.path, "ppq": m.ppq, "format": m.fmt}
    rep["tempo"] = [{"bar": m.bar_of(t), "bpm": round(60e6 / us, 2)} for t, us in m.tempos]
    rep["timesig"] = [{"bar": m.bar_of(t), "sig": f"{n}/{d}"} for t, n, d in m.timesigs]
    rep["keysig"] = [{"sf": sf, "minor": bool(mi)} for _, sf, mi in m.keysigs]
    # 段落：优先 marker，没有就用 SPEC
    secs = []
    if m.markers:
        for t, name in m.markers:
            secs.append({"name": name, "start": m.bar_of(t)})
    elif spec and spec.get("sections"):
        b = 1
        for s in spec["sections"]:
            secs.append({"name": s["name"], "start": b})
            b += int(s["bars"])
    ends = [n[1] for tr in m.tracks for n in tr["notes"]]
    last_bar = m.bar_of(max(0, max(ends) - bar_tol(m, max(ends)))) if ends else 1
    spec_total = sum(int(s["bars"]) for s in spec["sections"]) if spec and spec.get("sections") else None
    total_bars = spec_total or m.declared_bars or last_bar
    rep["_total_inferred"] = not (spec_total or m.declared_bars)
    for i, s in enumerate(secs):
        s["end"] = (secs[i + 1]["start"] - 1) if i + 1 < len(secs) else max(total_bars, s["start"])
        s["bars"] = s["end"] - s["start"] + 1
    rep["sections"] = secs
    rep["total_bars"] = total_bars
    rep["seconds_by_bars"] = round(m.seconds(m.tick_of_bar(total_bars + 1)), 2)
    rep["seconds_last_note"] = round(m.seconds(m.end_tick), 2)

    tracks = []
    for tr in m.tracks:
        if not tr["notes"] and not tr["programs"]:
            continue
        notes = tr["notes"]
        drum = 10 in tr["channels"] and all(n[2] == 10 for n in notes)
        prog = next((p for _, c, p in tr["programs"] if c != 10), None)
        vel = [n[4] for n in notes]
        grid = m.ppq // 4
        on_grid, tight, offs = 0, 0, []
        for s, *_ in notes:
            if s % (m.ppq // 3) == 0 or s % (m.ppq // 6) == 0:      # 三连音格子上的算"在格子上"
                on_grid += 1
                tight += 1
                continue
            r = s % grid
            d = r if r <= grid / 2 else r - grid
            ms = d / m.ppq * 60000 / m.bpm_at(s)
            on_grid += d == 0
            tight += abs(ms) <= 5
            if abs(d) <= grid * 0.35:
                offs.append(ms)
        last_end = max((n[1] for n in notes), default=0)
        tracks.append({
            "name": tr["name"] or f"轨{tr['index']}", "index": tr["index"], "channels": sorted(tr["channels"]),
            "drum": drum, "program": prog, "notes": len(notes),
            "range": [min(n[3] for n in notes), max(n[3] for n in notes)] if notes else None,
            "enter": m.bar_of(notes[0][0] + bar_tol(m, notes[0][0])) if notes else None,
            "exit": m.bar_of(max(0, last_end - bar_tol(m, last_end))) if notes else None,
            "vel_mean": round(st.mean(vel), 1) if vel else None,
            "vel_sd": round(st.pstdev(vel), 1) if vel else None,
            "vel_max": max(vel) if vel else None,
            "cc": {str(k): len(v) for k, v in sorted(tr["cc"].items())},
            "grid_share": round(on_grid / len(notes), 3) if notes else None,
            "tight_share": round(tight / len(notes), 3) if notes else None,
            "mean_offset_ms": round(st.mean(offs), 1) if offs else None,
            "overlaps": tr["overlaps"], "dups": tr["dups"], "unclosed": tr["unclosed"], "bends": tr["bends"],
            "_notes": notes,
            "_cc": sorted((t, num, v) for num, lst in tr["cc"].items() for t, ch, v in lst),
        })
    rep["tracks"] = tracks

    # 每小节能量与在场轨。copy_of 候选轨（音符完全相同）只算一次；只有延音、没有新起音的小节权重低
    nb = total_bars
    bar_e, bar_active = [0.0] * (nb + 2), [set() for _ in range(nb + 2)]
    seen = set()
    for t in tracks:
        key = tuple((n[0], n[1], n[3]) for n in t["_notes"])
        if not key or key in seen:
            continue
        seen.add(key)
        per_bar = {}
        last_on = -10 ** 9
        for s, e, ch, p, v in t["_notes"]:
            b0 = m.bar_of(s + bar_tol(m, s))
            b1 = max(b0, m.bar_of(max(s, e - bar_tol(m, e))))
            new_event = s - last_on > m.ppq // 16            # 和弦里同时按下的音算一个起音事件
            last_on = s if new_event else last_on
            for b in range(b0, min(b1, nb) + 1):
                d = per_bar.setdefault(b, [0, []])
                d[1].append(v)
                if b == b0 and new_event:
                    d[0] += 1
        for b, (on, vs) in per_bar.items():
            if 1 <= b <= nb:
                bar_active[b].add(t["name"])
                w = 0.1 if on == 0 else 0.15 + 0.85 * min(on / 8, 1)
                bar_e[b] += w * (st.mean(vs) / 127)
    for s in secs:
        rng = [b for b in range(s["start"], s["end"] + 1) if 1 <= b <= nb]
        s["_energy_raw"] = st.mean([bar_e[b] for b in rng]) if rng else 0
        s["active"] = sorted(set().union(*[bar_active[b] for b in rng])) if rng else []
    mx = max([s["_energy_raw"] for s in secs] + [1e-9])
    for s in secs:
        s["energy"] = round(10 * s["_energy_raw"] / mx, 1)
    rep["_bar_active"], rep["_bar_e"] = bar_active, bar_e
    return rep


# ---------------------------------------------------------------- 检查
def run_checks(m, rep, spec, fmt, script=None, midi=None, history=False):
    out = []
    ex = (spec or {}).get("exemptions") or {}

    def add(cid, sev, msg, fix=""):
        if sev in ("WARN", "INFO") and cid in ex and ex[cid]:
            out.append({"id": cid, "sev": "豁免", "msg": msg, "why": ex[cid]})
        else:
            out.append({"id": cid, "sev": sev, "msg": msg, "fix": fix})

    secs, tracks = rep["sections"], rep["tracks"]
    music = [t for t in tracks if t["notes"]]
    melodic = [t for t in music if not t["drum"]]
    drums = [t for t in music if t["drum"]]
    nb = rep["total_bars"]

    # 描述结构，不从格式名或统计数推导创作要求。
    if not music:
        add("T0", "ERROR", "MIDI 没有可播放的音符", "在 compose() 中写入音乐后重新生成")
    if secs:
        add("S1", "INFO", "段落：" + "、".join(f"{s['name']} {s['bars']} 小节" for s in secs))
        add("S2", "INFO", "密度/力度代理值：" + " → ".join(f"{s['name']} {s['energy']}" for s in secs),
            "该值不等于听感能量，也不规定必须有回落")
    if music:
        add("S3", "INFO", "声部进退：" + "、".join(f"{t['name']} {t['enter']}–{t['exit']}" for t in music))

    # ---------- K / M / V：参数卡、旋律、和最近几首撞车
    card_checks(spec, add)
    melody_checks(m, rep, spec, fmt, add)
    if history:
        variety_checks(m, spec, script, midi, add)

    # ---------- T：技术与 Logic 导入
    for t in melodic:
        p = t["program"]
        profile = (spec or {}).get('logic_import', {})
        if p in profile.get('silent_programs', []):
            add('T1', 'WARN', f"{t['name']}: 本机记录此 program 曾导入为无声外部轨，需核对")
        detail = profile.get('program_notes', {}).get(str(p))
        if detail:
            add('T2', 'WARN', f"{t['name']}: {detail}")
        if p is None:
            add("T10", "INFO", f"{t['name']}: 没有 program change（导入后默认音色）")
    for t in music:
        if t["overlaps"]:
            add("T3", "WARN", f"{t['name']}: {t['overlaps']} 处同音高音符重叠（后一个音会把前一个截断或粘连）",
                "前一个音截短到后一个之前（logicsong 写文件时会自动处理）")
        if t["dups"]:
            add("T3", "WARN", f"{t['name']}: {t['dups']} 处同一时刻同音高写了两次（重复触发/1 tick 碎音）",
                "去掉重复：常见于左手和弦与旋律加厚撞在同一个音上")
        if t["unclosed"]:
            add("T3", "ERROR", f"{t['name']}: {t['unclosed']} 个音没有 note-off")
    for t in drums:
        bad = sorted({n[3] for n in t["_notes"] if not 35 <= n[3] <= 81})
        if bad:
            add("T4", "WARN", f"{t['name']}: 鼓轨有 GM 鼓位以外的音 {bad}", "DMD 鼓组的扩展音位要逐个试听确认")
    spec_tracks = {x.get("name", x.get("id")): x for x in (spec or {}).get("tracks", [])}
    for t in melodic:
        cfg = spec_tracks.get(t["name"])
        if cfg and cfg.get("range"):
            lo, hi = (_nn(x) for x in cfg["range"])
            out_ = [n for n in t["_notes"] if not lo <= n[3] <= hi]
            if out_:
                add("T5", "WARN", f"{t['name']}: {len(out_)} 个音超出声明音域 {pname(lo)}–{pname(hi)}"
                    f"（如 {m.bar_of(out_[0][0])} 小节 {pname(out_[0][3])}）")
        tiny = [n for n in t["_notes"] if n[1] - n[0] < 10]
        if tiny:
            add("T6", "WARN", f"{t['name']}: {len(tiny)} 个极短音（<10 tick）")
    missing = [x for x, ok in (("速度", m.has_tempo),
                               ("拍号", m.timesigs), ("调号", m.keysigs), ("段落 marker", m.markers)) if not ok]
    add("T7", "WARN" if missing else "OK", ("缺少：" + "、".join(missing)) if missing else "速度/拍号/调号/marker 齐全")
    tgt = (spec or {}).get("target_seconds")
    if tgt:
        dur = rep["seconds_by_bars"]
        add("T8", "OK" if tgt[0] <= dur <= tgt[1] else "WARN", f"时长 {dur:.1f}s（目标 {tgt[0]}–{tgt[1]}s）")
    if spec and spec.get("sections") and m.markers:
        want, b = [], 1
        for s in spec["sections"]:
            want.append((s["name"], b))
            b += int(s["bars"])
        got = [(s["name"], s["start"]) for s in secs]
        add("T9", "OK" if want == got else "ERROR", "marker 与 SPEC 段落表一致" if want == got else
            f"marker 与 SPEC 不一致：SPEC {want} / MIDI {got}", "" if want == got else "重新运行生成脚本")
    chan = {}
    for t in melodic:
        for c in t["channels"]:
            chan.setdefault(c, set()).add(t["program"])
    for c, progs in chan.items():
        if len(progs) > 1:
            add("T11", "WARN", f"通道 {c} 上有多个不同 program：{sorted(p for p in progs if p is not None)}")
    for t in music:
        nm = t["name"].lower()
        if not t["drum"] and any(k in nm for k in ("鼓", "drum", "kit")):
            add("T12", "INFO", f"{t['name']}: 名字像鼓但不在第 10 通道")

    for t in melodic:                                   # T13 踏板糊音
        clash = pedal_clashes(m, t)
        if clash:
            b, beat, a, _ = clash[0]
            add("T13", "WARN", f"{t['name']}: {len(clash)} 处重踩踏板时接住了上一和弦的音（不属于新和弦，会糊）"
                f"（如 {b} 小节第 {beat:g} 拍的 {pname(a)}）",
                "重踩延后到新和弦起音之后（logicsong 的 repedal 默认 +60 tick），上一和弦的音在重踩前结束")
    low = low_spacing(m, melodic)
    if low:
        add("H1", "INFO", f"低音区音程太窄 {len(low)} 处（如 " + "；".join(low[:3]) + "）",
            "低音区两个同时响的音要拉开：C3 以下至少五度，B♭1 以下只用八度")
    rub = semitone_rubs(m, melodic)
    if rub:
        add("H2", "INFO", f"长音之间的小二度/小九度摩擦 {len(rub)} 处（如 " + "；".join(rub[:3]) + "）",
            "是刻意的色彩就不用管；否则把其中一个音换成和弦音或缩短")

    out.sort(key=lambda x: (SEV_ORDER.get(x["sev"], 9), x["id"]))
    return out


# ---------------------------------------------------------------- K / M / V
def _style(spec):
    return str((spec or {}).get("style") or ((spec or {}).get("card") or {}).get("style") or "").lower()


def card_checks(spec, add):
    """只提醒未处理的占位文字；card 不是必填问卷。"""
    if spec:
        todo = json.dumps(spec, ensure_ascii=False).count("TODO")
        if todo:
            add("K1", "INFO", f"SPEC 中有 {todo} 处 TODO，交付前确认是否仍有未完成内容")


def _hook_windows(m, rep, spec):
    """SPEC.hooks 每个 hook 首次出现的窗口（首次出现段）→ [(hook id, 单旋律线, 起始小节)]。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import melody_metrics as MM
    out = []
    secs = {s["name"]: s for s in rep["sections"]}
    for hid, h in ((spec or {}).get("hooks") or {}).items():
        if not isinstance(h, dict):
            continue
        names = [_track_name(spec, x) for x in (h.get("tracks") or [h.get("track")]) if x]
        hts = [t for t in rep["tracks"] if t["name"] in names and t["notes"]]
        sec = secs.get(h.get("first"))
        if not hts or not sec:
            continue
        b1 = sec["end"]
        t0, t1 = m.tick_of_bar(sec["start"]), m.tick_of_bar(b1 + 1)
        notes = [n for t in hts for n in t["_notes"] if t0 - bar_tol(m, t0) <= n[0] < t1 - bar_tol(m, t1)]
        line = MM.mono_line(notes, m.ppq)
        if len(line) >= 2:
            out.append((hid, line, sec["start"]))
    return out


def melody_checks(m, rep, spec, fmt, add):
    """M 系列：旋律量化。统计不判断旋律质量。
    hook 指标在每个 SPEC.hooks 的首次出现段里算（首次出现段，顶声部）；M2/M16/M17 用整条主旋律轨。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import melody_metrics as MM
    style = _style(spec)
    card = (spec or {}).get("card") or {}
    hk = card.get("hook") or {}
    chords = MM.ChordMap(spec) if spec else None
    key = (spec or {}).get("key")
    bar_ticks = m.tick_of_bar(2) - m.tick_of_bar(1)
    wins = _hook_windows(m, rep, spec)
    if not wins:
        add("M0", "NOT RUN", "SPEC.hooks 没写 track/first，或首次出现段里没有音：hook 指标没算",
            "在 SPEC.hooks 里写 {track 或 tracks, first, returns}")
    locked = set(str(x) for x in ((spec or {}).get("locked") or []))
    hooks = (spec or {}).get("hooks") or {}

    def is_locked(hid):
        """hook 首次出现的段落被锁定了（用户认可、不许改）：这段的旋律问题只提示，不再要求改。"""
        h = hooks.get(hid) or {}
        sec = h.get("first")
        ids = h.get("tracks") or ([h["track"]] if h.get("track") else [])
        names = set(ids) | {_track_name(spec, x) for x in ids}
        return any(f"{n}@{sec}" in locked or f"{n}@*" in locked for n in names) or f"*@{sec}" in locked
    for hid, line, start in wins:
        lk = is_locked(hid)
        for cid, sev, msg, *fix in MM.hook_checks(hid, line, m.ppq, bar_ticks, chords, key, card, style, fmt, m.seconds):
            if lk and sev in ("WARN", "ERROR"):
                sev, msg = "INFO", msg + "（锁定段落，只提示）"
            add(cid, sev, msg, fix[0] if fix else "")
    melodic = [t for t in rep["tracks"] if t["notes"] and not t["drum"]]
    lead = _lead_track(melodic, spec) if melodic else None
    if lead and lead["notes"] >= 8:
        line = MM.mono_line(lead["_notes"], m.ppq)
        for cid, sev, msg, *fix in MM.song_checks(line, m.ppq, bar_ticks, rep["sections"], card, m.seconds):
            add(cid, sev, msg, fix[0] if fix else "")


def variety_checks(m, spec, script, midi, add):
    """V1–V6：和用户最近几首比较（song_history.py）。不在用户的歌曲目录里（如沙盒）时不跑。"""
    path = script or midi
    if not path:
        add("V0", "NOT RUN", "没给 --script，撞车检查没跑")
        return
    folder = os.path.dirname(os.path.abspath(path))
    root = os.path.dirname(folder)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import song_history as SH
        hist = SH.discover(root)
        new = SH.song_features(folder, script, midi)
        res = SH.run_check(new, hist)
    except Exception as e:
        add("V0", "NOT RUN", f"撞车检查出错：{e}")
        return
    for c in res:
        add(c["id"], c["sev"], c["msg"], c.get("fix", ""))


from logicsong import nn as _nn


def _track_name(spec, tid):
    for t in (spec or {}).get("tracks", []):
        if t.get("id") == tid:
            return t.get("name", tid)
    return tid


def _lead_track(melodic, spec):
    for t in (spec or {}).get("tracks", []):
        if "旋律" in str(t.get("role", "")) and not t.get("copy_of"):
            for x in melodic:
                if x["name"] == t.get("name", t.get("id")):
                    return x
    return max(melodic, key=lambda t: (t["range"][1] if t["range"] else 0, t["notes"])) if melodic else None




def q16(m, rel):
    return int(round(rel / (m.ppq / 4)))










def pedal_clashes(m, t):
    """踏板"误接"：重新踩下踏板时，上一和弦里本该马上松开的音（在踩下后 1/16 拍以内才结束）被踏板接住，
    而这个音又不属于新和弦——它会一直糊在新和弦里。常见原因：连奏拖尾 + push 偏移，或重踩太早。"""
    ev = sorted((tk, v) for tk, num, v in t["_cc"] if num == 64)
    downs = [tk for i, (tk, v) in enumerate(ev) if v >= 64 and (i == 0 or ev[i - 1][1] < 64)]
    out = []
    for d in downs:
        caught = [n for n in t["_notes"] if n[0] < d - 5 and d + 5 < n[1] <= d + m.ppq // 4]
        if not caught:
            continue
        new_pcs = {n[3] % 12 for n in t["_notes"] if d - m.ppq // 4 <= n[0] < d + m.ppq}
        for n in caught:
            if new_pcs and n[3] % 12 not in new_pcs:
                b = m.bar_of(d)
                out.append((b, round((d - m.tick_of_bar(b)) / m.ppq + 1, 2), n[3], None))
                break
    return out


def low_spacing(m, melodic):
    """低音区（两个音都低于 C4）同时响的两个音间距太窄。经验下限按下方音：
    ≥E3 不限；C3–D#3 小三度；A#2–B2 大三度；F2–A2 纯五度；A#1–E2 纯五度；更低只用八度。"""
    need = [(52, 0), (48, 3), (46, 4), (41, 7), (34, 7), (0, 12)]
    notes = sorted((n[0], n[1], n[3]) for t in melodic for n in t["_notes"] if n[3] < 60)
    out, seen = [], set()
    for i, (s, e, p) in enumerate(notes):
        for s2, e2, p2 in notes[i + 1:]:
            if s2 >= e:
                break
            lo, hi = min(p, p2), max(p, p2)
            if lo == hi or lo >= 52 or min(e, e2) - s2 < m.ppq // 2:
                continue
            req = next(r for th, r in need if lo >= th)
            if hi - lo < req:
                b = m.bar_of(s2)
                key = (b, lo, hi)
                if key not in seen:
                    seen.add(key)
                    out.append(f"{b} 小节 {pname(lo)}+{pname(hi)}")
    return out


def semitone_rubs(m, melodic):
    """两个同时延续 ≥ 1 拍的音成小二度/小九度（跨轨也算）。"""
    notes = sorted((n[0], n[1], n[3]) for t in melodic for n in t["_notes"] if n[1] - n[0] >= m.ppq)
    out, seen = [], set()
    for i, (s, e, p) in enumerate(notes):
        for s2, e2, p2 in notes[i + 1:]:
            if s2 >= e:
                break
            if abs(p - p2) % 12 == 1 and min(e, e2) - max(s, s2) >= m.ppq:     # 小二度/小九度（大七度常是 maj7/add9 色彩，不报）
                b = m.bar_of(max(s, s2))
                key = (b, min(p, p2) % 12, max(p, p2) % 12)
                if key not in seen:
                    seen.add(key)
                    out.append(f"{b} 小节 {pname(min(p, p2))}/{pname(max(p, p2))}")
    return out


# ---------------------------------------------------------------- 对比两个版本
def compare(new, old, rep_new, rep_old, spec):
    """锁定比较采用精确演奏事件、控制状态与速度；任何变化都要报告。"""
    from fractions import Fraction
    # 0.2.0-r2 之前的 MIDI 没有名义位置（LSN1）与总小节数（LSM1）：两边按同一口径归段，
    # 段落结构相同、旧版没有音符起于新版总长之后时，沿用有记录一方的总长：尾音或尾部空白不算段落长度改动。
    use_nominal = new.declared_bars is not None and old.declared_bars is not None
    if rep_old.get("_total_inferred") and not rep_new.get("_total_inferred"):
        layout = lambda rep: [(s["name"], s["start"]) for s in rep["sections"]]
        last_start = max((n[0] for tr in old.tracks for n in tr["notes"]), default=0)
        if rep_old["sections"] and layout(rep_old) == layout(rep_new) and old.bar_of(last_start) <= rep_new["total_bars"]:
            rep_old = dict(rep_old, total_bars=rep_new["total_bars"],
                           sections=rep_old["sections"][:-1] + [dict(rep_old["sections"][-1], end=rep_new["sections"][-1]["end"])])
    def cells(m, rep):
        out = {}
        secs = rep["sections"] or [{"name": "全曲", "start": 1, "end": rep["total_bars"]}]
        names = {}
        for tr in m.tracks:
            if not tr["notes"] and not tr["programs"] and not tr["channels"]:
                continue
            name = tr["name"] or f"Track {tr['index']}"
            names[name] = names.get(name, 0) + 1
            identity = name if names[name] == 1 else f"{name}#{names[name]}"
            nominal = {}
            for tick, kind, sub, data in tr['events'] if use_nominal else ():
                if kind == 'meta' and sub == 0x7F and data.startswith(b'LSN1:'):
                    pitch, shift = json.loads(data[5:])
                    nominal[(tick, pitch)] = tick + shift
            for sec in secs:
                t0, t1 = m.tick_of_bar(sec["start"]), m.tick_of_bar(sec["end"] + 1)
                q = lambda t: Fraction(t, m.ppq)
                notes = sorted((q(a-t0), q(b-a), ch, p, v) for a,b,ch,p,v in tr["notes"] if nominal.get((a,p), a) < t1 and nominal.get((a,p), a) + (b-a) > t0)
                state, controls = {}, []
                channel_events = sorted((tk, source['index'], ordinal, kind, ch, data)
                    for source in m.tracks for ordinal, (tk, kind, ch, data) in enumerate(source['events'])
                    if kind != 'meta' and ch + 1 in tr['channels'])
                for tk, _, _, kind, ch, data in channel_events:
                    if kind == "meta" or kind in (0x80, 0x90):
                        continue
                    if tk <= t0:
                        key = (kind, ch, data[0] if kind in (0xB0, 0xA0) else -1)
                        state[key] = data
                    if t0 < tk < t1:
                        controls.append((q(tk-t0), kind, ch, data))
                # 未写出与显式写出复位默认值等效（CC1=0、CC11=127、CC64=0）；较早的库省略了其中一些初值。
                state = {k: v for k, v in state.items() if not (k[0] == 0xB0 and tuple(v) in ((1, 0), (11, 127), (64, 0)))}
                def timing(seq):
                    prior = [it[1:] for it in seq if it[0] <= t0]
                    return (prior[-1:] , [(q(it[0]-t0), it[1:]) for it in seq if t0 < it[0] < t1])
                out[(identity, sec["name"])] = (notes, sorted(state.items()), controls,
                                              timing(m.tempos), timing(m.timesigs), q(t1-t0))
        return out
    a, b = cells(new, rep_new), cells(old, rep_old)
    table = {}
    for k in sorted(set(a) | set(b)):
        x, y = a.get(k), b.get(k)
        if x is None or y is None:
            table[k] = "新增" if y is None else "删除"
        elif x == y:
            table[k] = "="
        else:
            labels = ("音符/力度", "控制初值/program", "控制事件", "速度", "拍号", "段落长度")
            table[k] = "Δ(" + ",".join(n for n,u,v in zip(labels,x,y) if u != v) + ")"
    locks, res = (spec or {}).get("locked") or [], []
    for item in locks:
        if "@" not in str(item):
            res.append({"lock": item, "sev": "INFO", "msg": "不是 轨@段落 写法，没法自动核对（人工确认）"})
            continue
        tr, sec = str(item).split("@", 1)
        keys = [k for k in table if (tr == "*" or k[0] == tr or k[0] == _track_name(spec, tr))
                and (sec == "*" or k[1] == sec)]
        if not keys:
            res.append({"lock": item, "sev": "WARN", "msg": "在两个文件里都找不到这个轨/段落"})
            continue
        bad = [k for k in keys if table[k] != "="]
        res.append({"lock": item, "sev": "ERROR" if bad else "OK",
                    "msg": ("被改动：" + "，".join(f"{k[0]}@{k[1]} {table[k]}" for k in bad)) if bad else "未改动"})
    changed = [k for k, v in table.items() if v != "="]
    return table, res, changed


# ---------------------------------------------------------------- 输出
def bar(x, n=20):
    return "█" * int(round(x / 10 * n)) + "·" * (n - int(round(x / 10 * n)))


def print_report(rep, checks, spec, spec_note, fmt):
    print(f"== 概况：{os.path.basename(rep['file'])} ==")
    tempos = "，".join(f"{t['bpm']:g}@{t['bar']}" for t in rep["tempo"])
    sig = rep["timesig"][0]["sig"] if rep["timesig"] else "?"
    key = ""
    if rep["keysig"]:
        k = rep["keysig"][0]
        key = f"调号 {k['sf']:+d}{'（小调）' if k['minor'] else '（大调）'}"
    print(f"PPQ {rep['ppq']}  速度 {tempos} BPM  拍号 {sig}  {key}  格式 {fmt or '未指定'}")
    print(f"{rep['total_bars']} 小节 ≈ {rep['seconds_by_bars']:.1f}s（最后一个音结束于 {rep['seconds_last_note']:.1f}s）")
    if spec_note:
        print("⚠ " + spec_note)
    print("\n== 轨道 ==")
    print(f"{'名称':<10}{'通道':<6}{'音色':<7}{'音符':>5}  {'音域':<11}{'进':>4}{'出':>4}  {'力度均/差/最大':<14}{'卡网格':>6}{'偏移':>8}  CC")
    for t in rep["tracks"]:
        rng = f"{pname(t['range'][0])}–{pname(t['range'][1])}" if t["range"] else "—"
        prog = "鼓" if t["drum"] else str(t["program"])
        vel = f"{t['vel_mean']}/{t['vel_sd']}/{t['vel_max']}" if t["notes"] else "—"
        g = f"{t['grid_share']:.0%}" if t["grid_share"] is not None else "—"
        off = f"{t['mean_offset_ms']:+.1f}ms" if t["mean_offset_ms"] is not None else "—"
        cc = " ".join(f"{k}×{v}" for k, v in t["cc"].items() if v > 1 or k not in ("7", "10", "91", "64"))
        print(f"{t['name'][:9]:<10}{','.join(map(str, t['channels'])):<6}{prog:<7}{t['notes']:>5}  {rng:<11}"
              f"{t['enter'] or '—':>4}{t['exit'] or '—':>4}  {vel:<14}{g:>6}{off:>8}  {cc}")
    if rep["sections"]:
        print("\n== 段落能量（0–10，音符密度×力度×在场轨数的代理值）==")
        for s in rep["sections"]:
            print(f"{s['name'][:8]:<9}{s['start']:>4}–{s['end']:<4}{s['bars']:>3}小节  {bar(s['energy'])} {s['energy']:>4}"
                  f"  在场 {len(s['active'])}")
    print("\n== 检查 ==")
    for c in checks:
        line = f"[{c['sev']}] {c['id']} {c['msg']}"
        if c.get("why"):
            line += f"  ← 理由：{c['why']}"
        elif c.get("fix") and c["sev"] in ("ERROR", "WARN", "INFO"):
            line += f"\n        → {c['fix']}"
        print(line)
    if spec and spec.get("tracks"):
        rows = [(t.get("name", t["id"]), t.get("program"), t.get("patch")) for t in spec["tracks"] if t.get("patch")]
        if rows:
            print("\n== 换音色清单（Logic 里逐轨换；中文名可用 logic_patches.py 查）==")
            for n, p, c in rows:
                print(f"  {n}: 占位 program {p if p is not None else '鼓'} → 候选 {' / '.join(map(str, c))}")
    n_err = sum(c["sev"] == "ERROR" for c in checks)
    n_warn = sum(c["sev"] == "WARN" for c in checks)
    print(f"\n合计：ERROR {n_err}  WARN {n_warn}  （技术检查与统计观察；不代表听感验收）")
    return n_err


def main():
    ap = argparse.ArgumentParser(description="MIDI 技术检查、音乐观察与版本对比")
    ap.add_argument("midi")
    ap.add_argument("--script", help="生成脚本（读其中的 SPEC）或 SPEC 的 .json")
    ap.add_argument("--logic-profile", help="可选的本机导入记录 JSON")
    ap.add_argument("--format", choices=["douyin-clip", "full-song", "speech-underscore", "remix-variant", "custom"],
                    help="默认取 SPEC.format")
    ap.add_argument("--compare", help="旧版本 .mid：逐段逐轨对比，并核对 SPEC.locked 的 轨@段落")
    ap.add_argument("--json", help="另存 JSON 报告")
    hg = ap.add_mutually_exclusive_group()
    hg.add_argument("--history", action="store_true", help="与同目录旧作比较，只作观察")
    hg.add_argument("--no-history", action="store_true", help="兼容旧命令：不查历史（默认）")
    a = ap.parse_args()

    spec, note = (None, None)
    if a.script:
        spec, note = load_spec(a.script)
    from tool_support import config_dir
    profile = a.logic_profile or (config_dir() / 'logic-import.json')
    if a.logic_profile or profile.is_file():
        with open(profile, encoding="utf-8") as handle:
            spec = dict(spec or {}, logic_import=json.load(handle))
    fmt = a.format or (spec or {}).get("format")
    m = Midi(a.midi)
    rep = analyze(m, spec)
    checks = run_checks(m, rep, spec, fmt, script=a.script, midi=a.midi, history=a.history)
    n_err = print_report(rep, checks, spec, note, fmt)
    out = {k: v for k, v in rep.items() if not k.startswith("_")}
    out["tracks"] = [{k: v for k, v in t.items() if not k.startswith("_")} for t in rep["tracks"]]
    out["sections"] = [{k: v for k, v in s.items() if not k.startswith("_")} for s in rep["sections"]]
    out["checks"] = checks
    if a.compare:
        mo = Midi(a.compare)
        ro = analyze(mo, None)          # 旧版按它自己的 marker 和长度算，不套新版 SPEC 的段落表
        table, locks, changed = compare(m, mo, rep, ro, spec)
        print(f"\n== 与 {os.path.basename(a.compare)} 对比（= 相同，Δ 表示演奏或控制状态改变）==")
        secs = [s["name"] for s in rep["sections"]] or ["全曲"]
        names = sorted({k[0] for k in table}, key=lambda n: [t["name"] for t in rep["tracks"]].index(n)
                       if n in [t["name"] for t in rep["tracks"]] else 99)
        print(f"{'':<10}" + "".join(f"{s[:6]:<9}" for s in secs))
        for n in names:
            print(f"{n[:9]:<10}" + "".join(f"{table.get((n, s), '—'):<9}" for s in secs))
        print("改动位置：" + ("，".join(f"{k[0]}@{k[1]}" for k in changed) if changed else "没有任何改动"))
        for r in locks:
            print(f"[{r['sev']}] 锁定 {r['lock']}：{r['msg']}")
            n_err += r["sev"] == "ERROR"
        out["compare"] = {"changed": [f"{k[0]}@{k[1]}" for k in changed], "locks": locks,
                          "table": {f"{k[0]}@{k[1]}": v for k, v in table.items()}}
    if a.json:
        from tool_support import write_text
        write_text(a.json, json.dumps(out, ensure_ascii=False, indent=1, default=list), a.midi, a.script, a.compare, owner="check_midi:" + (str(m.producer) if m.producer else os.path.realpath(a.midi)))
    sys.exit(1 if n_err else 0)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, SyntaxError, TypeError, KeyError, IndexError, struct.error) as exc:
        print(f"[ERROR] 输入或输出无效：{exc}", file=sys.stderr)
        sys.exit(2)
