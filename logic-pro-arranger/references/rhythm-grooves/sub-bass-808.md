## 808：调音、kick 冲突、侧链、滑音

### 调到同调

- Quick Sampler 载入 808 后看**延音段**（不是开头的"咔"）的音高，设对 Root Key，做到"按什么键出什么音"，用 Tuner 核对（弹 F#1 = 30，频谱峰应在约 46 Hz）；808 每个和弦的第一个音是该和弦根音。kick 也调到主音或五音（DMD 套件 kick 的音高旋钮），或换短 kick（< 100 ms，只当瞬态）。MIDI 只决定 808 的音高、长度（= 延音多长）、和 kick 的相对位置、滑音；音色、饱和在 Logic 里选（sound-palette.md）。

### kick 落点时 808 让开（按顺序试）

- ① 选定关系：同 tick（808 当 kick 的低频，kick 换短）或错开 ≥ 1 个 16 分；② 长度：> 140 BPM 时 808 在下一个 kick 之前结束，trap / drill 的 fill 里 808 和 kick 一起停，不在滑音下叠 kick（会糊）；③ 侧链：808 被 kick 压 4–8 dB（MIDI 层 `pump` "中"）。

### 侧链深度（MIDI 层用 `t.pump(bars, shape)` 近似）

| 场景 | 深度 | pump |
|---|---|---|
| 轻力度编配（有琶音 / 和弦层时） | 2–4 dB | 轻 |
| NCS supersaw | 3–6 dB | 轻～中 |
| big room、和弦 pad | 6–10 dB | 中 |
| 808 ← kick | 4–8 dB | 中 |
| slap house / 巴西 bass、慢摇 offbeat bass | 8–12 dB（压不到 8 dB 没有"拍打感"） | 重 |

- pump 只写在 bass、和弦、pad、琶音上，鼓和主旋律不写；音色要响应 CC11，实际压多少 dB 取决于音色的 CC11 曲线。bass 已经躲开 kick 正拍（B6–B8）时 bass 自己只压 0–3 dB（轻或不写），重点压和弦和 pad。

### 滑音（glide / slide）两种写法

```python
# A 重叠 + 音色 legato：前一个音盖过后一个音开头 0.03–0.06 拍（480 PPQ 下 15–30 tick）
b.note(bar, 0, 2.80, "F#1", 100)            # 下一个音在 2.75 拍，多写 0.05 拍
b.note(bar, 2.75, 1.25, "F#2", 96)          # Logic 里音色设 Mono / Legato + Glide 40–120 ms，否则只是两个音同时响
# B 弯音（任何音色都能用；音色的弯音范围要和 bend_range 一致，八度滑音设 12，或先发 RPN 0,0，见 instruments.md）
b.note(bar, 2.75, 1.25, "F#1", 96)
for k in range(9):                           # 约 0.16 拍内从 0 弯到 +12
    b.bend(bar, 2.75 + k * 0.02, 12 * k / 8, bend_range=12)
b.bend(bar + 1, 0, 0, bend_range=12)         # 下一个音之前归零（同一 tick 上 note_off 先于弯音事件）
```

- 目标音程 +12、+7、+5、+3（上滑带期待，下滑带重量），先滑向和弦音；放在小节最后一个 8 分或 16 分、snare 前的空处。每 2–4 小节最多一次；drill 每 4 小节 2–3 个，前 3 小节只用根音、第 4 小节再滑。808 轨 jitter 写 0。MIDI 文件不带 glide 时间，glide 在音色上设；拿不准音色支不支持 legato 就用 B。glide 40–120 ms、重叠 15–30 tick 是「教程推测」，导入后听一遍。
