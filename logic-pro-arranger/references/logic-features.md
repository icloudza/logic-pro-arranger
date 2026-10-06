# Logic 功能与任务选择

按当前安装版本与可用工具核对功能；本文件不维护版本发布时间线或安装状态。中文菜单及本机导入经验见 [logic-creator-studio.md](logic-creator-studio.md)。

| 当前问题 | 可检查的功能 | 验证点 |
|---|---|---|
| 快速试伴奏关系 | Session Players / 鼓手 | 是否跟随正确和弦、律动和段落；需要精细修改时检查转 MIDI 的结果 |
| 找现有音频的节拍 | Smart Tempo、Flex Time | 半速/倍速、首拍与后续漂移，不只看一个 BPM |
| 调整演唱音高 | Flex Pitch / Vocal Transformer | 辅音、共振峰、颤音与过渡是否自然 |
| 分离混音中的声部 | Stem Splitter | 分离伪影，特别是瞬态和混响尾；不等于原始分轨 |
| 演奏或重排切片 | Quick Sampler | 根音、映射、包络、切点、淡入淡出与实际触发 |
| 调整鼓与低频 | Step Sequencer / Drum Machine Designer | 鼓位映射、swing、音长与 kit 的实际音色 |
| 调整表情 | CC、自动化、音源 articulation | 音源是否响应指定参数；仅写 CC 不保证声音变化 |
| 比较编制与版本 | Track Stack、静音/独奏、另存版本 | 候选不要同时响；比较时匹配音量 |
| 导出与响度处理 | Bounce、Mastering Assistant、限幅器 | 范围、旁通、尾音、输出规格和实际测量 |

使用现有可用的自动化工具，不假设旧环境中的 MCP 命令一定存在。操作前读取实际 UI 状态，完成后验证结果；只有具体无法自动完成的步骤才转交用户。
