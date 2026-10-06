#!/usr/bin/env python3
"""试听小样：用 macOS 自带的 GM 音色（gs_instruments.dls）把 MIDI 或 hook 候选直接渲染成 MP3，不用开 Logic。

用于比较旋律、和声、节奏与结构。整体增益目标为峰值 −1 dBFS；最终音色需在 Logic 回听。

用法：
  python3 preview.py --hooks 歌_生成脚本.py            # 每个 hook 候选 → <歌>/试听/hook-A.mp3 …（--solo 裸线；默认简化和声）
  python3 preview.py 歌.mid                            # 整首 → <歌>/试听/<歌>_试听.mp3（copy_of 候选轨只放一条）
  python3 preview.py 歌.mid --bars 9-16 --skip 鼓      # 只渲染第 9–16 小节、不要鼓
  python3 preview.py 歌.mid --only 主奏A,和声 --out x.mp3
只能在 macOS 上用（需要 swift、ffmpeg）。沙盒里会直接说明并退出，跳过这一步即可。
"""
import argparse
import json
import math
import os
import platform
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True
from tool_support import find_tool, guard_out, output_path
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
DLS = "/System/Library/Components/CoreAudio.component/Contents/Resources/gs_instruments.dls"


def can_render():
    miss = []
    if platform.system() != "Darwin":
        miss.append("不是 macOS")
    if not shutil.which("swift"):
        miss.append("没有 swift")
    if not find_tool("ffmpeg"):
        miss.append("没有 ffmpeg")
    if not os.path.exists(DLS):
        miss.append("没有系统 GM 音色库")
    return miss


def _at(events, tick, default):
    """事件列表 [(tick, value)] 在 tick 时刻的值。"""
    v = default
    for t, x in events:
        if t <= tick:
            v = x
        else:
            break
    return v


def midi_to_doc(path, bars=None, only=None, skip=None):
    import check_midi as C
    m = C.Midi(path)
    t_lo = m.tick_of_bar(bars[0]) if bars else 0
    t_hi = m.tick_of_bar(bars[1] + 1) if bars else 10 ** 12
    t_off = m.seconds(t_lo)
    tracks, seen, names = [], set(), []
    for tr in m.tracks:
        notes = tr["notes"]
        if not notes or (only and tr["name"] not in only) or (skip and tr["name"] in skip):
            continue
        sig = tuple((n[0], n[1], n[3]) for n in notes)
        if sig in seen:                      # copy_of 的 A/B/C 候选：音符相同，只放第一条，不然音量翻倍
            continue
        seen.add(sig)
        drum = all(n[2] == 10 for n in notes)
        prog = next((p for _, c, p in tr["programs"] if c != 10), 0)
        cc = {k: sorted((t, v) for t, ch, v in lst) for k, lst in tr["cc"].items()}
        first = notes[0][0]
        vol = _at(cc.get(7, []), first, 100)
        pan = _at(cc.get(10, []), first, 64)
        ped = cc.get(64, [])
        out = []
        for s, e, ch, p, v in notes:
            if not t_lo <= s < t_hi:
                continue
            if not drum and ped and _at(ped, e, 0) >= 64:          # 踏板踩着：延到抬起
                up = next((t for t, x in ped if t > e and x < 64), e)
                e = max(e, up)
            expr = _at(cc.get(11, []), s, 127)
            vel = int(round(v * (0.35 + 0.65 * expr / 127)))
            t0 = m.seconds(s) - t_off
            t1 = m.seconds(min(e, t_hi + m.ppq * 8)) - t_off
            if drum:
                t1 = max(t1, t0 + 0.25)
            out.append({"t": round(t0, 5), "d": round(max(0.02, t1 - t0), 5), "p": p, "v": max(1, vel)})
        if out:
            tracks.append({"program": prog, "drum": drum, "volume": round((vol / 127) ** 2, 4),
                           "pan": round((pan - 64) / 64, 3), "notes": out})
            names.append(tr["name"])
    return {"sr": 44100, "tail": 2.5, "tracks": tracks}, names


def render(doc, out_path, *, owner=None, force=False, source=None):
    from tool_support import owned_output
    guard_out(out_path, source, force=force or owned_output(out_path, owner))
    miss = can_render()
    if miss:
        sys.exit("这台机器渲染不了试听（" + "、".join(miss) + "）。沙盒里跳过这一步，交付 .mid 即可。")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        ev, wav = os.path.join(td, "ev.json"), os.path.join(td, "out.wav")
        with open(ev, "w") as f:
            json.dump(doc, f)
        r = subprocess.run(["swift", os.path.join(HERE, "render_dls.swift"), ev, wav], capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(wav):
            sys.exit("渲染失败：" + (r.stdout + r.stderr)[-800:])
        # 只做整体增益（峰值拉到 -1 dBFS），不做动态压缩：安静段落保持安静，听得出能量起伏
        vd = subprocess.run([find_tool("ffmpeg"), "-v", "info", "-i", wav, "-af", "volumedetect", "-f", "null", "-"],
                            capture_output=True, text=True).stderr
        peak = next((float(l.split("max_volume:")[1].split("dB")[0]) for l in vd.splitlines() if "max_volume:" in l), -1.0)
        gain = max(-20.0, min(30.0, -1.0 - peak))
        args = [find_tool("ffmpeg"), "-v", "error", "-y", "-i", wav, "-af", f"volume={gain:.2f}dB", "-ar", "44100"]
        args += (["-c:a", "libmp3lame", "-b:a", "192k"] if out_path.lower().endswith(".mp3") else [])
        with output_path(out_path, source, force=force, owner=owner) as temp:
            r = subprocess.run(args + [str(temp)], capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit("ffmpeg 出错：" + r.stderr[-500:])
    return out_path


def hook_previews(script, out_dir=None, program=None, solo=False, candidate_programs=False, output=None, force=False):
    import check_midi as C
    import logicsong as L
    import melody_metrics as M
    spec, note = C.load_spec(script)
    if not spec:
        sys.exit(note)
    cands = {k: v for k, v in (spec.get("hook_candidates") or {}).items() if isinstance(v, dict) and v.get("line")}
    if not cands:
        sys.exit("SPEC['hook_candidates'] 里没有写好的 line（每个候选写成 t.line() 的格式）")
    hooks = spec.get("hooks") or {}
    first = next((h.get("first") for h in hooks.values() if isinstance(h, dict) and h.get("first")), None)
    base = L.Song(dict(spec, tracks=[]))
    first = first or base.sections[0]["name"]
    b0 = base.start(first)
    folder = os.path.dirname(os.path.abspath(script))
    out_dir = out_dir or os.path.join(folder, "试听")
    res = []
    for k in sorted(cands):
        c = cands[k]
        toks = [t for t in c["line"].split() if t != "|"]
        beats = sum(float(L.Fraction(t.split(":")[1])) for t in toks)
        nbars = max(1, math.ceil(beats / base.beats_per_bar - 1e-9))
        secs = []
        for i in range(nbars):
            b = b0 + i
            ch = " ".join(f"{sym}:{d:g}" for _, sym, d in base.chords_in(min(b, base.total_bars))) or ""
            secs.append({"name": f"b{i + 1}", "bars": 1, "chords": ch})
        if c.get("chords") is not None:
            secs = [{"name": "候选", "bars": nbars, "chords": c["chords"]}]
        prog = program if program is not None else (c.get("program", 0) if candidate_programs else 0)
        mini = {"title": f"hook-{k}", "bpm": c.get("bpm", spec["bpm"]), "key": c.get("key", spec.get("key")), "meter": spec.get("meter", [4, 4]),
                "tempo_map": [[1, c.get("bpm", spec["bpm"])]], "sections": secs,
                "tracks": [{"id": "lead", "name": "旋律", "program": prog, "channel": 1, "volume": 110},
                           {"id": "harm", "name": "和弦", "program": 0, "channel": 2, "volume": 70}]}
        song = L.Song(mini)
        song.track("lead").line(1, 0, c["line"])
        h, prev = song.track("harm"), None
        for b in ([] if solo else range(1, nbars + 1)):
            for beat, sym, d in song.chords_in(b):
                if sym and sym != "%":
                    prev = h.chord(b, beat, d - 0.05, sym, 50, low=48, high=67, prev=prev)
        with tempfile.TemporaryDirectory() as td:
            mid = song.write(td)
            doc, _ = midi_to_doc(mid)
            mm = C.Midi(mid)
            lead = next(t for t in mm.tracks if t["name"] == "旋律")
            met = M.metrics(M.mono_line(lead["notes"], mm.ppq), mm.ppq, song.beats_per_bar, M.ChordMap(mini), mini.get("key"))
            toks = M.contour_tokens(M.mono_line(lead["notes"], mm.ppq), mm.ppq)
        from pathlib import Path
        if output:
            target = Path(output)
            target = target if len(cands) == 1 else target.with_name(f"{target.stem}-{k}{target.suffix}")
        else:
            target = Path(out_dir) / f"hook-{k}{'-solo' if solo else ''}.mp3"
        out = render(doc, str(target), owner="hook:" + os.path.realpath(script) + ":" + k,
                     force=force, source=script)
        res.append((k, c, out, met, toks))
    print(f"== hook 候选小样（GM 音色，{'裸线' if solo else '简化和声'}；默认 {spec['bpm']} BPM，候选可覆盖）==")
    for k, c, out, met, toks in res:
        detail = f"{met.get('n', 0)} 个音"
        if met.get('range') is not None:
            detail += f"，音域 {met['range']} 半音，切分 {met['sync']} 处"
        print(f"{k}：{out}\n   {c.get('note', '')} {detail}；{c.get('bpm', spec['bpm'])} BPM")
    print("对照试听后记录选择；统计不是审美评分。用户要求直接完成时自主继续。")


def main():
    ap = argparse.ArgumentParser(description="用 macOS 自带 GM 音色渲染试听小样（MIDI 或 hook 候选）")
    ap.add_argument("midi", nargs="?", help="要渲染的 .mid")
    ap.add_argument("--hooks", help="生成脚本：把 SPEC['hook_candidates'] 每个候选渲染成小样")
    ap.add_argument("--solo", action="store_true", help="hook 只渲染旋律裸线")
    ap.add_argument("--candidate-programs", action="store_true", help="使用各候选 program；默认统一钢琴便于比较乐句")
    ap.add_argument("--program", type=int, help="hook 小样的旋律音色（GM 0 起；默认 0 钢琴）")
    ap.add_argument("--bars", help="单小节或闭区间，如 3、9-16")
    ap.add_argument("--force", action="store_true", help="覆盖已有试听输出；输入文件永不覆盖")
    ap.add_argument("--only", help="只要这些轨（逗号分隔，MIDI 里的轨名）")
    ap.add_argument("--skip", help="不要这些轨（逗号分隔）")
    ap.add_argument("--out", help="输出文件（.mp3 或 .wav）；默认 <歌>/试听/<歌>_试听.mp3")
    ap.epilog = "依赖：macOS、Swift 命令行工具、ffmpeg；Python 仅用标准库。"
    a = ap.parse_args()
    if a.hooks:
        hook_previews(a.hooks, None, a.program, a.solo, a.candidate_programs, a.out, a.force)
        return
    if not a.midi:
        ap.error("给一个 .mid，或用 --hooks 生成脚本")
    bars = None
    if a.bars:
        try:
            parts = tuple(int(x) for x in a.bars.split('-'))
            bars = (parts[0], parts[0]) if len(parts) == 1 else parts
            if len(bars) != 2 or bars[0] < 1 or bars[1] < bars[0]:
                raise ValueError()
        except ValueError:
            ap.error('--bars 需要正小节号或起止范围，如 3、9-16')
    only = set(a.only.split(",")) if a.only else None
    skip = set(a.skip.split(",")) if a.skip else None
    doc, names = midi_to_doc(a.midi, bars, only, skip)
    if not doc["tracks"]:
        sys.exit("没有可渲染的音符（检查 --only/--skip/--bars）")
    title = os.path.splitext(os.path.basename(a.midi))[0]
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.midi)), "试听", f"{title}_试听.mp3")
    import check_midi as C
    producer = C.Midi(a.midi).producer
    render(doc, out, owner="preview:" + (str(producer) if producer else os.path.realpath(a.midi)), force=a.force, source=a.midi)
    print(f"写出 {out}（{len(names)} 轨：{'、'.join(names)}；GM 音色，只听旋律/和声/结构，真实音色在 Logic 里选）")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as exc:
        sys.exit(f"错误：{exc}")
