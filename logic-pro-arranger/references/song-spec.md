# 生成脚本与 SPEC

生成脚本内嵌 `logicsong`，可在有 Python 的环境独立生成 MIDI。SPEC 必须为可由 `ast.literal_eval` 读取的字面量；检查器不执行生成脚本来读取配置。

## 必需字段

```python
SPEC = {
    "title": "草稿", "bpm": 88, "meter": [4, 4], "key": "F minor",
    "sections": [
        {"name": "主题", "bars": 6, "chords": "Fm | Db"},
        {"name": "回应", "bars": 5, "chords": "Bbm | C7"},
    ],
    "tracks": [{"id": "lead", "name": "主奏", "role": "主旋律", "program": 0, "channel": 1}],
}
```

这是语法示例，不是曲式模板。`new_song.py` 默认仅一条主奏和一个空段；`--meter 6/8` 可选拍号；`--target-seconds 40,50` 只声明目标时长，不改变所写段落。

`sections` 的起始位置由长度累加，段名唯一；`chords` 可留空。`|` 分小节，`Fm Db` 平分一小节，`Fm:3 C7:1` 指定四分音符拍长；`%` 延用本段前一小节，避免在段首用 `%`。和弦串比段落短时循环。全曲只支持一个拍号。

## 可选字段

| 字段 | 用途 |
|---|---|
| `format` / `style` | 用途/风格标签，兼容旧格式名；不触发固定编制和审美限制 |
| `tempo_map` | 如 `[[1, 88], {"ramp": [9, 11], "from": 88, "to": 80}]`，覆盖 bpm 的固定速度 |
| `target_seconds` | 明确要求的时长区间；只检查 |
| `seed` | 人味抖动的种子；修改旧作时保留，避免无关变化 |
| `card` / `intent` | 可选创作笔记，没有必填审美字段 |
| `hooks` | 指明希望测量的乐句位置，见下例 |
| `locked` | 修改范围约束，如 `["lead@主题", "*@回应"]` |
| `source` / `vocal` / `speech` | 素材路径、原始速度、处理方式和音频起点等工程笔记 |
| `intro` / `outro` / `fills` / `subtractions` | 可选结构注记；不要求作品具备这些元素 |
| `exemptions` | 兼容旧检查说明，不能隐藏 ERROR |

轨道可设置 `range`（音名或 MIDI 号）、`volume/pan/reverb`（CC7/10/91）、`push_ms`（固定偏移）、`jitter_ms/vel_jitter`（抖动）、`patch`（Logic 音色名）。`enter/exit` 只是计划注记，不会自动裁剪音符。`copy_of` 复制另一轨，可带 `transpose`；需要比较音色才使用，导入后避免同时播放候选。

## 可选旋律候选

```python
"hooks": {"main": {"track": "lead", "first": "主题", "returns": ["回应"]}},
"hook_candidates": {
    "A": {"line": "C5:1 Eb5:1 F5:2", "note": "短句；后面保留停顿"},
    "B": {"line": "C5:2 G4:1 F4:1", "chords": "Dbmaj7", "note": "同起点，改变落点"},
},
"hook_pick": "A",
```

`line` 与 `t.line()` 同语法，按乐句需要写任意长度。`chords` 可覆盖候选试听的和声，不填则从 hook 首次出现段取；`key`、`bpm` 也可单独覆盖。`program` 仅在 `preview.py --candidate-programs` 时用于音色对比。默认统一钢琴；`--solo` 不加和声。

这些候选只用于试听，不自动写进成品。选定后在 `compose()` 中写入对应 `line`，并同步其和弦、调和速度；后续再现按作品发展安排，不由预览器复制。

## 常用 API

```python
t = song.track("lead")
t.line(song.start("主题"), 0, "C5:1 Eb5:1 F5:2", vel=76)
# 音名:拍长[:力度][:偏移ms]；r 休止；| 检查小节线
# t.chord(bar, beat, dur, "Fm7", vel=65, low=48, high=72, prev=上一排列)
# t.pad(song.bars("主题"), low=48, high=72, vel=60)
# d.grid(bar, "x... x... x... x...", "kick", 70)
# d.groove(bar, ".x.x .x.x .x.x .x.x", "hh", 60, swing=56)
# t.ramp(11, (1, 0), (3, 0), 45, 90); t.repedal(song.bars("主题"))
```

音色、鼓型、伴奏工具详见 [instruments.md](instruments.md)、[rhythm-grooves.md](rhythm-grooves.md) 与脚本 API 注释。`locked` 的非 `轨@段落` 文字只供人工复核。
