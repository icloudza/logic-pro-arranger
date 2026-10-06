#!/usr/bin/env python3
"""歌曲历史与撞车检查（只用标准库）。

为什么有它：用户连续否掉过几首歌，原话是"旋律和以前项目这么类似""老是这个调""乐器老是这几种"。
在用户提出相似性问题或需要回顾时使用，结果仅作线索。

用法：
  python3 song_history.py                         # 列出最近 8 首：调、速度、进行、hook 轮廓、主奏音色、鼓组
  python3 song_history.py --n 12 --root ~/Documents/Logic
  python3 song_history.py --avoid                 # 只打印可以贴进 SPEC["card"]["avoid"] 的字典
  python3 song_history.py --check 新歌_生成脚本.py [--midi 新歌.mid]   # 新歌和最近 5 首比，报告相近特征
  python3 song_history.py --json out.json

读的是 <root>/<歌名>/ 下的 *_生成脚本.py（里面的 SPEC）和 .mid。没有 SPEC 的老歌只从 MIDI 读速度、调号和旋律。
同一首歌的版本（歌名-v2、歌名-长前奏版……）在 --check 时自动排除。
"""
import argparse
import glob
import json
import os
import re
import sys
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import check_midi as C          # noqa: E402
import melody_metrics as M      # noqa: E402

SKIP_DIRS = {"skill-project", "音乐经验库-网络调研", "Resources", ".Trash"}
_DEG = {0: "1", 1: "#1", 2: "2", 3: "b3", 4: "3", 5: "4", 6: "#4", 7: "5", 8: "b6", 9: "6", 10: "b7", 11: "7"}
NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


# ---------------------------------------------------------------- 提取一首歌的特征
def _lib():
    import logicsong as L
    return L


def key_from_midi(m):
    if not m.keysigs:
        return None
    _, sf, mi = m.keysigs[0]
    major = {0: 0, 1: 7, 2: 2, 3: 9, 4: 4, 5: 11, 6: 6, 7: 1, -1: 5, -2: 10, -3: 3, -4: 8, -5: 1, -6: 6, -7: 11}[sf]
    return f"{NAMES[(major + 9) % 12]} minor" if mi else f"{NAMES[major]} major"


def key_parts(key):
    """'C# minor' → (1, 'minor')；解析不了返回 (None, None)。调式只分大/小两类来比较。"""
    if not key:
        return None, None
    try:
        pc, mode = _lib().parse_key(key)
    except Exception:
        return None, None
    minorish = mode in ("minor", "aeolian", "harmonic minor", "melodic minor", "羽", "minor pentatonic",
                        "dorian", "phrygian", "商", "角", "locrian")
    return pc, ("minor" if minorish else "major")


def prog_signature(spec, chords):
    """主段落（hook 复现段或能量最高的段）的和弦根音 → 关系大调简谱级数串，如 '4536'。"""
    if not chords.ok:
        return None, None
    one = M.rel_major_pc(spec.get("key"))
    if one is None:
        return None, None
    secs = chords.song.sections
    hooks = spec.get("hooks") or {}
    want = []
    for h in hooks.values():
        want += list(h.get("returns") or [])
    cand = sorted([s for s in secs if s["name"] in want], key=lambda s: -int(s["bars"])) or \
        sorted(secs, key=lambda s: (-float(s.get("energy", 0) or 0), -int(s["bars"])))
    sec = cand[0] if cand else secs[0]
    syms = chords.bar_chords(range(sec["start"], sec["end"] + 1))
    L = _lib()
    roots, chords = [], []
    for sym in syms:
        try:
            r, ivs, bass = L.parse_chord(sym)
        except Exception:
            continue
        d = _DEG[(r - one) % 12]
        q = "m" if (3 in ivs and 4 not in ivs and 6 not in ivs) else "°" if (3 in ivs and 6 in ivs) else ""
        if not roots or roots[-1] != d:
            roots.append(d)
            chords.append(d + q)
    if not roots:
        return None, sec["name"]
    seq = roots[:8]
    prog_signature.chords = chords[:8]
    return " ".join(seq), sec["name"]


def family_key(sig):
    """进行家族：前 4 个不同和弦当作一个循环，取字典序最小的旋转（4536 和 5364 算同一家族）。"""
    if not sig:
        return None
    s = sig.split()[:4]
    rots = [" ".join(s[i:] + s[:i]) for i in range(len(s))]
    return min(rots)


# 用户听腻的两组和弦（关系大调简谱 + 大小）：{C F G Am} 与 {F G Em Am}；1564/1645/6415/4536 都在里面
OVERUSED = [frozenset({"1", "4", "5", "6m"}), frozenset({"3m", "4", "5", "6m"})]


def chord_set(chords):
    """主段前 4 个不同和弦的集合（换顺序也算同一组）。"""
    if not chords:
        return None
    seen = []
    for c in chords:
        if c not in seen:
            seen.append(c)
    return frozenset(seen[:4]) if len(seen) >= 3 else None


def _track_by_name(m, name):
    return next((t for t in m.tracks if t["name"] == name), None)


def trim_low_pickup(line):
    """去掉开头比旋律音区低一个八度以上的音（左手低音、伴奏引子），让 hook 从真正的旋律音开始。"""
    if len(line) < 4:
        return line
    top = sorted(n[2] for n in line)[len(line) // 2:]
    mid = top[len(top) // 2]
    i = 0
    while i < len(line) - 3 and line[i][2] < mid - 12:
        i += 1
    return line[i:]


def hook_line(spec, m):
    """hook 首次出现段落里 hook 轨的顶声部，从第一个音起取 2 小节。返回 (line, 来源说明)。"""
    if spec:
        tracks = {t["id"]: t for t in spec.get("tracks", [])}
        secs, b = {}, 1
        for s in spec.get("sections", []):
            secs[s["name"]] = (b, b + int(s["bars"]) - 1)
            b += int(s["bars"])
        for hid, h in (spec.get("hooks") or {}).items():
            ids = h.get("tracks") or ([h["track"]] if h.get("track") else [])
            notes = []
            for tid in ids:
                cfg = tracks.get(tid)
                tr = _track_by_name(m, cfg.get("name", tid)) if cfg else None
                if tr:
                    notes += tr["notes"]
            first = secs.get(h.get("first"))
            if not notes or not first:
                continue
            t0, t1 = m.tick_of_bar(first[0]), m.tick_of_bar(first[1] + 1)
            line = trim_low_pickup(M.mono_line([n for n in notes if t0 <= n[0] < t1], m.ppq))
            if len(line) >= 4:
                start = line[0][0]
                bar_t = m.tick_of_bar(2) - m.tick_of_bar(1)
                return [n for n in line if n[0] < start + 2 * bar_t], f"hook「{hid}」@{h.get('first')}"
    # 兜底：音区最高、音符较多的旋律轨，从它第一个音起 2 小节
    mel = [t for t in m.tracks if t["notes"] and not all(n[2] == 10 for n in t["notes"]) and len(t["notes"]) >= 8]
    if not mel:
        return [], "没有旋律轨"
    lead = max(mel, key=lambda t: sum(n[3] for n in t["notes"]) / len(t["notes"]))
    line = trim_low_pickup(M.mono_line(lead["notes"], m.ppq))
    if not line:
        return [], "没有旋律轨"
    start = line[0][0]
    bar_t = m.tick_of_bar(2) - m.tick_of_bar(1)
    return [n for n in line if n[0] < start + 2 * bar_t], f"推测主旋律轨「{lead['name']}」"


def patches(spec):
    """(主奏候选音色, 鼓组, 其它音色)。主奏 = hook 轨 / 角色含 旋律·主题·主奏·lead 的轨，以及它们的 copy_of 候选。"""
    if not spec:
        return [], [], []
    trs = spec.get("tracks", [])
    hook_ids = set()
    for h in (spec.get("hooks") or {}).values():
        hook_ids |= set(h.get("tracks") or ([h["track"]] if h.get("track") else []))
    lead_ids = {t["id"] for t in trs if t["id"] in hook_ids or re.search(r"旋律|主题|主奏|lead", str(t.get("role", "")), re.I)}
    lead_ids |= {t["id"] for t in trs if t.get("copy_of") in lead_ids}
    # 去掉末尾的说明括号再比：「硬摇滚（失真电吉他，已下载）」和「硬摇滚」是同一个音色
    norm = lambda p: re.sub(r"\s*[（(][^（）()]*[)）]\s*$", "", str(p)).strip()
    lead, drums, other = [], [], []
    for t in trs:
        ps = [norm(p) for p in (t.get("patch") or []) if p and not str(p).startswith("TODO")]
        if int(t.get("channel", 0) or 0) == 10:
            drums += ps
        elif t["id"] in lead_ids:
            lead += ps
        else:
            other += ps
    card = spec.get("card") or {}
    pal = card.get("palette") or {}
    if isinstance(pal.get("lead"), list):
        lead += [norm(p) for p in pal["lead"] if p and not str(p).startswith("TODO")]
    dedup = lambda xs: list(dict.fromkeys(xs))
    return dedup(lead), dedup(drums), dedup(other)


def song_features(folder, script=None, midi=None):
    title = os.path.basename(os.path.normpath(folder))
    if script is None:
        cands = sorted(glob.glob(os.path.join(folder, "*_生成脚本.py")), key=os.path.getmtime)
        script = cands[-1] if cands else None
    if midi is None:
        if script:
            guess = script.replace("_生成脚本.py", ".mid")
            midi = guess if os.path.exists(guess) else None
        if midi is None:
            mids = sorted(glob.glob(os.path.join(folder, "*.mid")), key=os.path.getmtime)
            midi = mids[-1] if mids else None
    if not script and not midi:
        return None
    spec, note = C.load_spec(script) if script else (None, "没有生成脚本")
    m = None
    if midi:
        try:
            m = C.Midi(midi)
        except Exception as e:
            note = f"MIDI 读不了：{e}"
    stamp = max(os.path.getmtime(p) for p in (script, midi) if p)
    f = {"title": title, "folder": folder, "script": script, "midi": midi, "mtime": stamp,
         "name": os.path.basename(script or midi).replace("_生成脚本.py", "").replace(".mid", ""),
         "note": note, "format": (spec or {}).get("format"), "style": (spec or {}).get("style")}
    card = (spec or {}).get("card") or {}
    f["key"] = (spec or {}).get("key") or card.get("key") or (key_from_midi(m) if m else None)
    # 比较速度用体感 BPM（半拍风格记谱 150、体感 75，不该和 150 的直拍歌算撞速度）
    bpm = card["bpm"].get("feel") or card["bpm"].get("grid") if isinstance(card.get("bpm"), dict) else None
    if bpm is None:
        bpm = (spec or {}).get("bpm")
    if bpm is None and m and m.has_tempo:
        bpm = round(60e6 / m.tempos[0][1], 1)
    f["bpm"] = bpm
    chords = M.ChordMap(spec) if spec else M.ChordMap(None)
    f["progression"], f["prog_section"] = prog_signature(spec, chords) if spec else (None, None)
    if card.get("progression", {}).get("family") and not f["progression"]:
        f["progression"] = str(card["progression"]["family"])
    f["family"] = family_key(f["progression"])
    f["chord_set"] = chord_set(getattr(prog_signature, "chords", None)) if f["progression"] else None
    prog_signature.chords = None
    f["lead_patches"], f["drum_patches"], f["other_patches"] = patches(spec)
    f["hook_tokens"], f["hook_from"], f["banned"] = [], None, []
    if m:
        line, src = hook_line(spec, m)
        f["hook_from"] = src
        if line:
            f["hook_tokens"] = M.contour_tokens(line, m.ppq)
            f["hook_notes"] = " ".join(C.pname(n[2]) for n in line[:8])
            f["banned"] = [k for _, _, k in M.banned_shapes(line, m.ppq)]
            f["duration_s"] = round(m.seconds(m.end_tick), 1)
    f["hook_contour_id"] = (card.get("hook") or {}).get("contour")
    return f


def discover(root):
    out = []
    for d in sorted(glob.glob(os.path.join(root, "*", ""))):
        name = os.path.basename(os.path.normpath(d))
        if name in SKIP_DIRS or name.startswith("."):
            continue
        try:
            f = song_features(d)
        except Exception as e:
            f = {"title": name, "folder": d, "mtime": os.path.getmtime(d), "note": f"解析失败：{e}"}
        if f:
            out.append(f)
    out.sort(key=lambda x: -x["mtime"])
    return out


# ---------------------------------------------------------------- 比较
def base_title(t):
    t = re.sub(r"[-_ ]?(v\d+|V\d+)$", "", t)
    return re.split(r"[-－_]", t)[0] if not t.startswith("未命名") else "-".join(t.split("-")[:2])


def compare(new, old):
    """新歌 vs 一首旧歌：返回 {维度: 说明} 只列相同/相近的维度。"""
    same = {}
    npc, nmode = key_parts(new.get("key"))
    opc, omode = key_parts(old.get("key"))
    if npc is not None and npc == opc and nmode == omode:
        same["调"] = f"都是 {new['key']}"
    elif npc is not None and opc is not None and nmode != omode and \
            ((nmode == "minor" and (npc + 3) % 12 == opc) or (nmode == "major" and (opc + 3) % 12 == npc)):
        same["调(关系调)"] = f"{new['key']} 和 {old['key']} 是关系大小调，音集相同"
    try:
        if new.get("bpm") and old.get("bpm") and abs(float(new["bpm"]) - float(old["bpm"])) <= 4:
            same["速度"] = f"{new['bpm']} vs {old['bpm']} BPM"
    except (TypeError, ValueError):
        pass
    if new.get("family") and new.get("family") == old.get("family"):
        same["进行"] = f"同一家族 {new['progression']}（{old['title']}：{old['progression']}）"
    elif new.get("chord_set") and new.get("chord_set") == old.get("chord_set"):
        same["进行"] = f"同一组和弦换了顺序 {new['progression']}（{old['title']}：{old['progression']}）"
    if new.get("hook_tokens") and old.get("hook_tokens"):
        sim = M.similarity(new["hook_tokens"], old["hook_tokens"])
        if sim >= 0.7:
            same["hook 轮廓"] = f"相似度 {sim:.0%}（{' '.join(new['hook_tokens'][:5])} vs {' '.join(old['hook_tokens'][:5])}）"
    ov = sorted(set(new.get("lead_patches") or []) & set(old.get("lead_patches") or []))
    if ov:
        same["主奏音色"] = "、".join(ov)
    return same


def run_check(new, hist, k=5):
    """保留相似特征，取消强制换调/换音色/换三项及禁用轮廓。"""
    nb = base_title(new['title'])
    recent = [h for h in hist if h.get('folder') and
              os.path.abspath(h['folder']) != os.path.abspath(new['folder']) and
              base_title(h['title']) != nb][:k]
    if not recent:
        return [{'id': 'V0', 'sev': 'NOT RUN', 'msg': '没有可比较的旧作'}]
    out = []
    for h in recent:
        same = compare(new, h)
        if same:
            out.append({'id': 'V1', 'sev': 'INFO',
                        'msg': f"与《{h['title']}》的相同或相近特征：" + '；'.join(same.values()),
                        'fix': '粗略检索线索；试听完整乐句，不以特征数量决定重写'})
    return out or [{'id': 'V1', 'sev': 'INFO', 'msg': '当前比较维度未发现明显重合；不代表原创性结论'}]


# ---------------------------------------------------------------- 输出
def avoid_dict(hist, k=5):
    rec = hist[:k]
    return {
        "keys": list(dict.fromkeys(h["key"] for h in rec if h.get("key"))),
        "bpms": list(dict.fromkeys(h["bpm"] for h in rec if h.get("bpm"))),
        "progressions": list(dict.fromkeys(h["progression"] for h in rec if h.get("progression"))),
        "hook_shapes": list(dict.fromkeys(" ".join(h["hook_tokens"][:5]) for h in rec if h.get("hook_tokens"))),
        "patches": list(dict.fromkeys(p for h in rec[:3] for p in (h.get("lead_patches") or []) + (h.get("drum_patches") or []))),
    }


def print_table(hist, n):
    import time
    print(f"== 最近 {min(n, len(hist))} 首（按修改时间，新的在前）==")
    for h in hist[:n]:
        day = time.strftime("%m-%d", time.localtime(h["mtime"]))
        print(f"\n《{h['title']}》 {day}  {h.get('format') or ''} {h.get('style') or ''}".rstrip())
        if h.get("note") and not h.get("key"):
            print(f"  ⚠ {h['note']}")
        print(f"  调 {h.get('key') or '?'}   速度 {h.get('bpm') or '?'}   时长 {h.get('duration_s', '?')}s")
        if h.get("progression"):
            print(f"  主段进行（简谱级数）：{h['progression']}  ← {h.get('prog_section')}")
        if h.get("hook_tokens"):
            flag = "  · 旧作中出现过的轮廓" if "strict" in h.get("banned", []) else ("  · 接近惯用型" if h.get("banned") else "")
            print(f"  hook 开头：{h.get('hook_notes', '')}  轮廓 {' '.join(h['hook_tokens'][:6])}{flag}  ← {h.get('hook_from')}")
        if h.get("hook_contour_id"):
            print(f"  hook 轮廓 ID：{h['hook_contour_id']}")
        if h.get("lead_patches"):
            print(f"  主奏候选：{' / '.join(h['lead_patches'])}")
        if h.get("drum_patches"):
            print(f"  鼓组：{' / '.join(h['drum_patches'])}")
    print("\n记号：R 同音 u/d 级进上/下 U/D 跳进上/下；s ≤ 八分 m ≤ 四分 l 更长（时值是前一个音的）")


def main():
    ap = argparse.ArgumentParser(description="用户最近的歌：调、速度、进行、hook 轮廓、音色；以及新歌撞车检查")
    ap.add_argument("--root", default=os.path.expanduser("~/Documents/Logic"))
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--avoid", action="store_true", help="只打印近期特征（兼容旧 avoid 字段名，不是禁用列表）")
    ap.add_argument("--check", help="新歌的生成脚本：和最近 5 首比较")
    ap.add_argument("--midi", help="配合 --check：新歌的 .mid（默认按脚本名找）")
    ap.add_argument("--json")
    a = ap.parse_args()
    if not os.path.isdir(a.root):
        sys.exit(f"没有这个目录：{a.root}（沙盒里没有用户的歌，跳过历史检查即可）")
    hist = discover(a.root)
    res = {"recent": [{k: (sorted(v) if isinstance(v, frozenset) else v) for k, v in h.items() if k not in ("folder",)}
                      for h in hist[:a.n]],
           "avoid": avoid_dict(hist)}
    code = 0
    if a.check:
        folder = os.path.dirname(os.path.abspath(a.check))
        new = song_features(folder, a.check, a.midi)
        checks = run_check(new, hist)
        res["check"] = checks
        print(f"== 撞车检查：《{new['title']}》 调 {new.get('key')}  速度 {new.get('bpm')}  进行 {new.get('progression')} ==")
        for c in checks:
            print(f"[{c['sev']}] {c['id']} {c['msg']}" + (f"\n        → {c['fix']}" if c.get("fix") and c["sev"] in ("WARN", "INFO") else ""))
        code = 0
    elif a.avoid:
        print(json.dumps(res["avoid"], ensure_ascii=False, indent=1))
    else:
        print_table(hist, a.n)
        print("\n== 近期特征摘要（供参考，无需自动避开） ==")
        print(json.dumps(res["avoid"], ensure_ascii=False, indent=1))
    if a.json:
        from tool_support import write_text
        write_text(a.json, json.dumps(res, ensure_ascii=False, indent=1, default=str), a.check, a.midi)
    sys.exit(code)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        sys.exit(f"错误：{exc}")
