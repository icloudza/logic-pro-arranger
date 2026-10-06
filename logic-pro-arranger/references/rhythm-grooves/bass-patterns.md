### bass 型 B1–B12

连续型用 `b.comp(bar, rows, vel, lo=28, mono=True, gate=…)`；要休止、死音、滑音的用 `t.line` / `t.note`。按本曲所需选择。

```python
b.comp(bar, {"R": "x... .... x... ...."}, 86, lo=28, mono=True, gate=0.95)    # B1 根音长音（抒情、前奏、低频"进来"的第一步）
# B2 流行根音八分 + 半音引导音（流行 / 摇滚，结合）：拍点 104、其他正拍 92、反拍 72；最后一个八分留给引导音（只放弱位）
b.comp(bar, {"R": "X.o. x.o. x.o. x..."}, 92, lo=28, mono=True, gate=0.85)
nx = role_pitch(song.chord(bar + 1), "R", 28)
b.note(bar, 3.5, 0.4, nx - 1 if nx > 28 else nx + 1, 76)       # 下方半音；目标是 E1 时改从上方
b.comp(bar, {"R": "x... .... x... ....", "5": ".... x... .... x..."}, 88, lo=28, mono=True)    # B3 根五（country、抒情）
# B4 八度跳（disco、funk-pop、四踩副歌）：低音正拍 102、高音反拍 90，断奏 50–60%
b.comp(bar, {"R": "X... X... X... X...", "8": "..x. ..x. ..x. ..x."}, 90, lo=28, mono=True, gate=0.55)
# B5 walking（爵士、摇摆、桥段）：第 1 拍根音，2、3 拍和弦音，第 4 拍是下一根音的下方半音
r, r2 = song.chord(bar), song.chord(bar + 1)
b.line(bar, 0, " ".join(f"{pname(p)}:1" for p in (role_pitch(r, "R", 36), role_pitch(r, "3", 36),
       role_pitch(r, "5", 36), role_pitch(r2, "R", 36) - 1)), vel=90, accent=4)
# B6 house / trance 反拍（互答）：每音约 90 ms（gate 0.2；要饱满到 180 ms 用 0.4）；techno / psy 改 ".xXx" 16 分滚动（每拍第 1 格留给 kick）
b.comp(bar, {"R": "..x. ..x. ..x. ..x."}, 100, lo=28, mono=True, gate=0.2)
# B7 slap house（互答）：八度上的音比低音重 10–15，门限 60–75%；门限写满就变成闷的 deep house bass
b.comp(bar, {"R": "..x. .x.. .x.. ....", "8": "...X ..X. ..X. ..X.", "5": ".... .... .... .x.."}, 96, lo=28, mono=True, gate=0.7)
# B8 越南鼓滚动【推断】：反拍根音 + 后一个 16 分的高八度轻音，每音 60–80 ms
b.comp(bar, {"R": "..X. ..X. ..X. ..X.", "8": "...o ...o ...o ...o"}, 92, lo=28, mono=True, gate=0.6)
# B9 funk slap（E 小调演示写法）：拇指低音锁 kick、勾弦高八度落反拍、死音 = 同音力度 35、≤ 30 ms
b.line(bar, 0, "E1:0.5:112 E2:0.25:100 E1:0.06:35 r:0.19 G1:0.5:100 G2:0.25:96 A1:0.25:90 E1:0.25:108 "
               "E1:0.06:35 r:0.19 E2:0.25:100 D2:0.25:92 E1:0.25:104 E2:0.25:96 E1:0.06:35 r:0.19 B1:0.25:88")
b.comp(bar, {"R": "x..x ..x. x..x ..x."}, 92, lo=28, mono=True, gate=0.75)    # B10 tresillo 3+3+2（R&B、拉丁流行、dembow、tropical sub）
# B11 抒情经过音（Am F C G）：根音 3 拍 + 第 4 拍级进或半音走到下一根音
b.line(bar, 0, "A1:3 G1:1 | F1:2 A1:1 B1:1 | C2:3 A1:1 | G1:3 G#1:1", vel=86, accent=6)
# B12 amapiano log drum（单独一轨；音色带 +12→0 的 30–60 ms 音高包络）：两小节一问一答，音长 1–2 格，不当 808 长音用
ld.line(bar, 0, "A1:0.5 r:0.25 A1:0.25 r:0.5 C2:0.5 r:0.5 E2:0.25 D2:0.25 r:1 | "
                "r:0.5 A1:0.5 r:0.5 G1:0.25 r:0.25 A1:0.5 r:0.5 E2:0.25 D2:0.25 C2:0.25 r:0.25", vel=96)
```

- Jersey、phonk 的 808 = 把 kick 网格换成音高：`b808.comp(bar, {"R": KICK}, 100, lo=28, mono=True, gate=0.95)`。DnB sub 起音跟 kick（第 1 步长 8 格、第 11 步 4 格、第 15 步 2 格可走经过音）；UKG sub 每 2 小节至少一次滑音（40–80 ms）。B5、B9、B11、B12 是演示写法，音高要按本首的和弦和调重写；引导音、半音放第 4 拍或拍与拍之间（弱位），放 1、3 拍会撞和弦。
