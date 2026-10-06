#!/usr/bin/env python3
"""查 Logic 音色的中英文名（Logic 中文界面的资源库搜索要用中文名）。只用标准库。

用法：
  python3 logic_patches.py search 钢琴                 # 中英文子串都行，不分大小写
  python3 logic_patches.py search "synth bass" --type InsP --limit 40
  python3 logic_patches.py zh "Steinway Grand Piano"   # 英文 → 中文
  python3 logic_patches.py en 施坦威大钢琴              # 中文 → 英文
  python3 logic_patches.py installed 弦乐               # 这台机器上实际有哪些音色文件（.patch），带中文名

数据来自 Logic 自带的名称表 Item.strings（3 万多条）：
  InsP = 乐器音色（软件乐器轨用）  AdiP = 音频轨音色/效果  caf = Apple Loops 循环乐段
⚠ 名称表里有 ≠ 已下载安装。是否可用，要在 Logic 资源库里搜一下才算数。
可用环境变量 LOGIC_ITEM_STRINGS 指定文件路径。
"""
import argparse
import glob
import os
import plistlib
import sys
sys.dont_write_bytecode = True

CANDIDATES = [
    "/Applications/Logic Pro Creator Studio.app/Contents/Resources/Library.bundle/ContentDatabaseV01.db/zh_CN.lproj/Item.strings",
    "/Applications/Logic Pro.app/Contents/Resources/Library.bundle/ContentDatabaseV01.db/zh_CN.lproj/Item.strings",
    "/Applications/Logic Pro X.app/Contents/Resources/Library.bundle/ContentDatabaseV01.db/zh_CN.lproj/Item.strings",
]
TYPE_NAMES = {"InsP": "乐器音色", "AdiP": "音频音色/效果", "caf": "循环乐段"}


def find_table():
    env = os.environ.get("LOGIC_ITEM_STRINGS")
    paths = ([env] if env else []) + CANDIDATES + glob.glob(
        "/Applications/Logic*.app/Contents/Resources/Library.bundle/ContentDatabaseV01.db/zh_CN.lproj/Item.strings")
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def load():
    p = find_table()
    if not p:
        sys.exit("找不到 Logic 的 Item.strings（这台机器没装 Logic，或在云端沙盒里）。"
                 "这种情况下写英文音色描述，让用户在 Logic 资源库里搜。")
    with open(p, "rb") as f:
        d = plistlib.load(f)
    rows = []
    for k, zh in d.items():
        if "|" in k:
            kind, en = k.split("|", 1)
        elif "/" in k:
            kind, en = k.split("/", 1)
        else:
            kind, en = "?", k
        rows.append((kind, en, zh))
    return rows


def installed(rows, q, limit):
    """扫描音色文件：~/Music/Logic Pro Library.bundle（下载的声音资源）和 App 自带的 Patches/UniversalPatches。
    采样类音色（钢琴、弦乐、原声鼓）多在下载资源里；合成器/鼓机音色多在 App 自带里。"""
    zh = {en: z for kind, en, z in rows if kind == "InsP"}
    roots = [(os.path.expanduser("~/Music/Logic Pro Library.bundle/Patches/Instrument"), "已下载资源")]
    for app in glob.glob("/Applications/Logic*.app"):
        for sub in ("Contents/Resources/Patches/Instrument", "Contents/Resources/UniversalPatches/Instrument"):
            roots.append((os.path.join(app, sub), "App 自带"))
    seen, hits = set(), []
    for root, src in roots:
        for path in glob.glob(os.path.join(root, "**", "*.patch"), recursive=True):
            en = os.path.splitext(os.path.basename(path))[0]
            cat = os.path.relpath(os.path.dirname(path), root)
            if en in seen:
                continue
            name_zh = zh.get(en, "")
            if q and q.lower() not in (en + name_zh + cat).lower():
                continue
            seen.add(en)
            hits.append((cat, name_zh or "（无中文名）", en, src))
    hits.sort()
    for cat, z, en, src in hits[:limit]:
        print(f"{z}\t{en}\t[{cat}]\t{src}")
    print(f"— 共 {len(hits)} 个" + (f"，只显示前 {limit} 个" if len(hits) > limit else "")
          + "。有 .patch 文件 ≠ 采样一定装全（App 自带的采样类音色可能还要下载），最终以资源库里搜到、能出声为准")


def main():
    ap = argparse.ArgumentParser(description="Logic 音色中英文名查询")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="中英文子串搜索")
    s.add_argument("q")
    s.add_argument("--type", choices=["InsP", "AdiP", "caf", "all"], default="InsP", help="默认只看乐器音色 InsP")
    s.add_argument("--limit", type=int, default=30)
    z = sub.add_parser("zh", help="英文名 → 中文名")
    z.add_argument("name")
    e = sub.add_parser("en", help="中文名 → 英文名")
    e.add_argument("name")
    i = sub.add_parser("installed", help="这台机器上实际有的音色文件（可按中英文或分类过滤）")
    i.add_argument("q", nargs="?", default="")
    i.add_argument("--limit", type=int, default=60)
    a = ap.parse_args()
    rows = load()
    if a.cmd == "installed":
        installed(rows, a.q, a.limit)
        return
    if a.cmd == "search":
        q = a.q.lower()
        hits = [r for r in rows if (a.type == "all" or r[0] == a.type) and (q in r[1].lower() or q in r[2].lower())]
        hits.sort(key=lambda r: (len(r[2]), r[2]))
        for kind, en, zh in hits[:a.limit]:
            print(f"{zh}\t{en}\t[{TYPE_NAMES.get(kind, kind)}]")
        print(f"— 共 {len(hits)} 条" + (f"，只显示前 {a.limit} 条" if len(hits) > a.limit else ""))
    else:
        col_in, col_out = (1, 2) if a.cmd == "zh" else (2, 1)
        exact = [r for r in rows if r[col_in].lower() == a.name.lower()]
        hits = exact or [r for r in rows if a.name.lower() in r[col_in].lower()][:20]
        if not hits:
            sys.exit("没找到；试试 search 子命令做模糊搜索")
        for r in hits:
            print(f"{r[col_out]}\t[{TYPE_NAMES.get(r[0], r[0])}]\t({r[col_in]})")


if __name__ == "__main__":
    main()
