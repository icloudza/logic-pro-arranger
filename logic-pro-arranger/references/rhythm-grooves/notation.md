## 记号与写法

### 网格行与三层力度

- 一格 = 16 分音符，16 格 = 一小节 4/4；步号 1–16：拍点 1/5/9/13，反拍（&）3/7/11/15，e/a 是偶数步。空格和 `|` 被忽略。
- `X` = vel + accent（默认 +15）· `x` = vel · `o` = 鬼音，力度 = ghost（**绝对值**，默认 30，不跟 vel 走）· `.` 或 `-` = 空。一次调用就是三层力度；hi-hat 这类要 4–5 层的，同一鼓件拆两三行、每行一层，每格只在一行出现。
- 本文网格行：`鼓件名  格子  vel [aN] [gN] [sN] [pN] [h]`，一行一个鼓件，力度写在行尾。
  `hh  X.x. x.x. X.x. x.x.  72 a12` = `d.grid(bar, "X.x. x.x. X.x. x.x.", "hh", 72, accent=12)`。
  带 `s`（swing%）、`p`（push 毫秒）、`h`（吃这一轨的 jitter）的行用 本文工具函数中的 `d.groove()`：
  `hh  .o.o .o.o .o.o .o.o  0 g45 s58 h` = `d.groove(bar, ".o.o .o.o .o.o .o.o", "hh", 0, ghost=45, swing=58, human=True)`。
- **原声风格（鼓轨设了 jitter_ms）所有行都用 d.groove()**：没写 `h` 的行守网格；`d.grid()`、`d.hit()` 会让 kick、crash 也跟着抖。32 格 = 两小节（drill、UKG 这类两小节型，每两小节调一次）；12 格三连写 `step=1/3`；6/8 用 `step=0.5` 的 6 格。
- 鼓件名：kick2 35 · kick 36 · rim 37 · snare 38 · clap 39 · snare2 40 · tom_floor 41 · hh 42 · tom_low 43 · hh_pedal 44 · tom_mid 45 · oh 46 · tom_mid2 47 · tom_hi 48 · crash 49 · tom_hi2 50 · ride 51 · china 52 · ride_bell 53 · tamb 54 · splash 55 · cowbell 56 · crash2 57 · ride2 59 · shaker 70 · claves 75 · wood_hi 76 · wood_lo 77；表外的号直接写数字：康加 62 闷 / 63 开 / 64 低，timbale 65/66，cabasa 69。

⚠️ 和别处资料不同：这里 `o` 是鬼音不是开镲（开镲是单独一行 `oh`），`-` 是空不是延音；同一格同鼓件写两次会合并成一个。**hh 和 oh 不写在同一格**：42/44/46 是互斥组，开镲会被同时的闭镲掐掉，开镲多长由下一个闭镲决定。
⚠️ DMD 电子套件（模拟烟雾、坚硬贝斯浩室）通常只在 36–51 有声音，shaker / tamb / cowbell / 康加可能没声：生成后逐件试听，没声就换原声套件（根源、南加风情）或删掉那一行。

### 半拍、三连、6/8

- 下列半拍例子用网格 BPM 记谱，军鼓在第 9 步，体感约为网格的一半。半速工程也能表达同样音乐，但需把时值、roll 和同步效果一起换算。
- 三连：`step=1/3` 一小节 12 格（8 分三连：shuffle、12/8 感），`step=1/6` 24 格（16 分三连 roll）。16 格上"每 3 格一下"是附点 16 分，不是三连。
- 6/8：SPEC `"meter": [6, 8]`，`bpm` 是**四分音符**速度（♩.=60 写 90）；一小节 = 3 拍 = 12 个 16 分格或 6 个八分格。

### 工具函数（已内置在 logicsong：轨的方法 + `role_pitch()`；下面所有网格和伴奏型都靠它们）

```python
d.groove(bar, pat, drum, vel, swing=50, push=0, human=False, accent=15, ghost=30, step=0.25, start=0)
#   grid() 加强版：swing% 把偶数步推后（50 = 直）；push = 这一行的固定偏移 ms（正 = 靠后，替代整轨 push_ms）；
#   human=True 才吃这一轨的 jitter_ms（kick、crash 守网格，hat、鬼音才抖）；drum 可写鼓件名 / MIDI 号 / 音名
d.roll(bar, beat, n, step, drum="snare", v0=55, v1=105)
#   滚奏：从 (bar, beat) 起 n 下、间隔 step 拍（1/4 = 16 分，1/8 = 32 分，1/6 = 16 分三连，1/12 = 32 分三连），力度 v0 → v1
role_pitch(sym, role, lo=40)
#   和弦 + 角色 → MIDI 号：R = 根音（斜线和弦取低音）落在 lo..lo+11；3/5/7/8/10/12/15 相对根音；别的字符串当音名（"E5"）
prev = t.comp(bar, rows, vel=70, lo=40, rh=(60, 76), gate=0.9, mono=False, prev=prev)
#   角色网格 → 伴奏 / 低音：rows = {"R": "x...x...", "5": "..x...x.", "RH": "....x..."}；每个音延到下一个起音 × gate
#   （mono=True 看所有行）；X = +12、o = −20；"RH" 行 = 右手密集和弦（rh 音域，跟着 prev 走声部）。返回右手排列
prev = g.strum(bar, pat, vel=85, ms=12, gate=0.92, alldown=False, prev=prev)
#   扫弦：有偶数步起音就按 16 分摆臂（奇数步下、偶数步上），否则按 8 分（拍点下、反拍上）；
#   下扫 = 低音弦根音 + 和弦、低 → 高；上扫 = 上面 3 个音、高 → 低、力度 −15；o = 切音
```

- `comp` / `strum` 用 SPEC 段落和弦，没写和弦的段落不能用。power chord 把和弦写成 `E5`、`C5`（只有根五）。
