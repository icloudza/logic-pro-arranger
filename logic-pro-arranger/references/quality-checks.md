# 验证范围

```bash
python3 scripts/check_midi.py 新.mid --script 新_生成脚本.py
python3 scripts/check_midi.py 新.mid --script 新_生成脚本.py --compare 旧.mid
python3 scripts/melody_metrics.py 新.mid --track 主奏 --script 新_生成脚本.py
```

- `ERROR`：文件或事件损坏、空 MIDI、显式约束失败；退出码 1。
- `WARN`：需核对的音域、踏板、通道或演奏风险。
- `INFO`：旋律、声部、密度等描述统计，按听感决定是否调整。

`SPEC.locked` 写 `轨@段落`；`--compare` 精确比较音符起止、音高、力度、通道、program、控制器初值及变化、弯音、速度和拍号。`=` 表示相同，`Δ` 表示变化；锁定项变化报 ERROR。

比较范围是 MIDI 事件，不含 Logic 插件、外部音频、音源设置或工程自动化。GM 试听用于判断乐句和结构；最终音色、演奏与混音需在目标环境回听。
