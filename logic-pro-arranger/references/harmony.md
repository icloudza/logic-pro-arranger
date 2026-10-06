# 和声与 MIDI 排列

先检查旋律长音、重音、低音与换和弦时刻的关系。旋律先行、和声先行或互相迭代都可。一个和弦、一个循环或常见进行都能成立；不因历史用过就更换。

## 可选择的变化

| 想改变的关系 | 可尝试的手法 |
|---|---|
| 旋律保留，底下移动 | 改低音转位、持续低音，或移动内声部 |
| 落点过早结束 | 延迟主和弦、更改换和弦时刻，或保留旋律长音跨过和声 |
| 连接生硬 | 保留共同音、缩小声部移动，或增加连接和弦 |
| 需要色彩转折 | 尝试借用 iv、♭VI、♭VII，副属或减七；听旋律与新音的关系 |
| 听起来拥挤 | 改排列、去掉根音/五音、减少同时持续的音，或缩短尾音 |
| 需要开放感 | 省三音、开放五度、sus 或持续音；由上下文决定调性清晰程度 |

三音/七音常能说明和弦性质，9/11/13 可以改变颜色。它们不是自动“高级”的配方；持续的小二度/小九度可作张力，也可因音色和音区造成掩蔽。没有固定的外来和弦次数、和弦音占比或禁停音。

低音区密集排列容易浑浊时，试拉开低音与其余声部，或减少持续音。就近转位帮助连接，但大跳、平行和声和开放排列也可有明确效果。和声节奏可稳定或随乐句改变，不必每小节换一个和弦。

## 用 logicsong 写和声

**SPEC 写法**：`"chords": "Am(add9) | Dm7 | Cmaj7 | G:2 E7:2"`——`|` 分小节；同一小节多个和弦平分，或写 `和弦:拍数`；`%` = 同上一小节；和弦比小节少时循环使用。括号会被去掉（Am(add9) = Amadd9），`ø` = m7b5，`△` = maj。

**支持的后缀**：（空）maj M m min - dim aug + 5 sus2 sus4 sus 6 m6 7 maj7 M7 m7 min7 m7b5 dim7 mmaj7 7sus4 add9 madd9 add2 9 maj9 m9 6/9 7b9 7#9 7b13 11 m11 13 maj13，以及斜线 `C/E`。别的写法会报错，需要时手写具体音高。

**voicing() 的脾气**（`t.chord` 和 `t.pad` 都用它）：
- 只出**密集排列**（一个八度内的各个转位），适合右手和 pad；开放排列（根–五–十、3-5-7-9）自己写音高列表，或者拆成两手。
- 默认最多 4 个音：先丢五音，再按"根、三、五、七、九、十一、十三"截断——`11`、`m11`、`13`、`maj13` 会把最有色彩的那个音丢掉，要 `size=5` 或直接写音高列表；`size=3` 保留根、三、七。
- **不排斜线低音**：C/E、G/F、Am7/D 的低音要自己写进左手或低音轨。
- add9、m9、7b9 在中音区会排成音簇（如 C4 D4 E4），想要通透就把 `low` 提到 60 以上或写列表。

**代码 1：右手就近排列 + 左手低音（含斜线和弦）**

```python
def compose(song):
    pno = song.track("piano")
    prev = None
    for b in song.bars("主歌") + song.bars("副歌"):
        for beat, sym, dur in song.chords_in(b):
            prev = pno.chord(b, beat, dur, sym, 62, low=57, high=72, prev=prev)   # 右手：就近转位
            root, _, bass = parse_chord(sym)             # 斜线和弦的低音 voicing 不排，要自己写
            lo = 41 + ((root if bass is None else bass) - 41) % 12    # 左手落在 F2–E3（41–52）
            pno.note(b, beat, dur, lo, 70)
            if bass is None and lo + 7 <= 55:
                pno.note(b, beat, dur, lo + 7, 58)       # 原位才加五度；C3(48) 以下不放三度
```

**代码 2：共同音延长的铺底**

```python
pad = song.track("pad")                  # program 用 89 占位，弦乐 40–51 导入 Logic 没声音
pad.pad(song.bars("副歌"), 52, 72, vel=56, size=3, legato=0.1)   # 共同音自动延长、不重按
pad.ramp(11, (song.start("副歌"), 0), (song.start("副歌") + 2, 0), 40, 100, curve="s")
```

**代码 3：顶音固定的十六分分解**（演示写法：音型要按歌改，别每首照抄）

```python
def arp16(t, bars, low=45, top=("B5", "C6"), swap=None, vel=58):
    """根–五–十度往上走，顶上两个固定音整段不换，给每个和弦染色。
    swap={"E7": ("B5", "G#5")}：顶音和某个和弦撞成小九度时，只在那个和弦换掉。"""
    for b in bars:
        for beat, sym, dur in t.song.chords_in(b):
            T0, T1 = (nn(x) for x in (swap or {}).get(sym, top))
            root, ivs, bass = parse_chord(sym)
            if bass is None:                                   # 原位：根–五–十度（三音高八度）
                r = low + (root - low) % 12
                mid, ten = r + 7, r + 12 + ivs[1]
            else:                                              # 斜线：低音–和弦五音–和弦根音
                r = low + (bass - low) % 12
                mid = r + ((root + 7 - r) % 12 or 12)
                ten = mid + ((root - mid) % 12 or 12)
            cell = [r, mid, ten, T0, ten, T1, mid, T0]         # 8 格一组，顶音落在反拍
            for i in range(int(round(dur * 4))):
                t.note(b, beat + i * 0.25, 0.3, cell[i % 8], vel + (8 if i % 4 == 0 else 0))
            if beat > 0:                                       # 小节中间换和弦：先抬后踩踏板
                t.pedal(b, beat - 0.01, down=False); t.pedal(b, beat + 0.06)
        t.repedal([b])

# 例：arp16(song.track("arp"), song.bars("主歌"), swap={"E7": ("B5", "G#5"), "A(add9)": ("B5", "C#6")})
```

B5 C6 这一对在 C 大调 / A 小调的 Am、F、G、C 上都成立（Dm 上 B 是 13，能用但偏亮）；Em、E7、A（Picardy）上会撞，所以要 `swap`。换调或换顶音对时，逐个和弦检查顶音与内声部关系。

**写完检查**：旋律想在强拍唱的色彩音（9、maj7）要写进和弦名（Am(add9)、Fmaj7），check_midi 才把它算作和弦音；反过来别为了凑比例乱加色彩。`song.chord(bar, beat)` 可以随时查某一拍的和弦。
