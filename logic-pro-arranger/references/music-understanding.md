# 音频分析与采样检索

## 安装

工具源码包含在 `tools/music-understanding/`。复制整个 skill 后，在目标机器编译并安装：

```bash
# 需要 Python 3.11、C 编译器和 make；macOS 原生模型桥接还需要 Xcode 命令行工具
sh tools/music-understanding/scripts/bootstrap.sh core
# 需要 CREPE、Beat This、Basic Pitch、CLAP 时安装可选模型依赖
sh tools/music-understanding/scripts/bootstrap.sh models
python3 scripts/music.py doctor
```

worker 默认使用`~/.cache/music-understanding/runtime-py311/bin/python`，可用 `MUSIC_PYTHON` 指定解释器。macOS C 核心使用系统 SQLite/CommonCrypto；Linux 构建需要 SQLite/OpenSSL 开发库和 pkg-config。音频解码需要 ffmpeg/ffprobe。运行环境通过普通 wheel 安装，移动 skill 不依赖原目录；源码升级后重跑 bootstrap。模型权重、Numba 与分析缓存放在用户缓存目录。缺运行环境明确给出安装命令，入口仅使用随包核心或显式 `MUSIC_EXECUTABLE`。

## 按问题选任务

```bash
python3 scripts/music.py analyze '<文件>' --tasks envelope,onsets,pitch
python3 scripts/music.py query '<ID>' summary
python3 scripts/music.py query '<ID>' events --from 0 --to 3 --type note --limit 12
python3 scripts/music.py audio '<ID>' --from 0 --to 3 --overlay notes --out '<新试听.wav>'
python3 scripts/music.py render '<ID>' --out '<新分析图.png>'
```

| 问题 | 任务或选项 |
|---|---|
| 原始采样峰值、包络、频谱 | `envelope,timbre` |
| 能量起音、单音根音 | `onsets,pitch`；`--pitch-backend ensemble` 对照 C YIN 与 CREPE |
| 单乐器复音转录 | `notes`，macOS 优先 Core ML，其他平台 ONNX |
| 节拍与循环候选 | `rhythm,loop`；可选 `--beat-backend beat-this` |
| 声音类别 | `sounds`：macOS SoundAnalysis；`perception`：CLAP |
| 语音转写 | `lyrics --speech-backend apple --locale zh-CN`，需要 macOS 26+ 和语言资源 |
| 按文字/音色找采样 | `search --semantic` / `search --similar`，需先分析指定素材 |

`--branch` 仅组合任务；自动分支依据文件名和时长，不能证明乐器类型。非 macOS 不加载 Apple 框架；可选择 ONNX、CLAP 或 whisper.cpp，未安装的后端明确报 unavailable。

## 解释结果

先读 summary，再查询事件；大数组通过文件引用按需取用。文件标签、测量、模型候选、人工修正保留各自来源。`probability: null` 表示未校准，分数不是正确率。

峰值/削波读取原始采样率、全部声道；重采样视图只供算法使用。削波字段统计绝对值 ≥1 的样本占比；浮点超幅不表示解码时已截平。起音检测基于能量攻击，可能漏掉连奏、柔起音；时间分辨率不等于误差保证。

默认音符查询、绘图与回听每个声部只采用一套结果：完成的 `notes` 优先于 `pitch`。`query events --task pitch` 可取原始音高假设，ensemble 的其他音符标为 `note_alternative`。`query runs` / `evidence` 保留来源追溯。

Beat This 与用户 BPM 或独立估计冲突时，输出 `status: conflict`、`beat_candidate`、原模型候选和冲突原因，停止标记可用网格。半速和倍速视为同一速度家族，保留原模型拍点和候选；只有拍间隔稳定、且与独立拍点的相位偏差 P90 不超过 25 ms（快速曲目再收紧到 1/16 拍，见 `cross_check.phase_tolerance_s`）时 `usable_for_alignment=true`，范围仅为拍点，小节第一拍仍需回听确认。`possible_upper_harmonic` 表示疑似泛音误识别。

`chords/structure/relations` 只提供基线候选；`stems`、whisper.cpp 适配器的运行结果需另行验收。详细参数见 [工具说明](../tools/music-understanding/README.md)。

## 仅索引采样文件

```bash
python3 scripts/sample_library.py scan --root '<用户目录>' --index '<库外索引.json>'
python3 scripts/sample_library.py search kick --index '<库外索引.json>'
python3 scripts/sample_library.py prepare '<相对路径>' --index '<库外索引.json>' --out '<新样本.wav>'
```

按命名提示角色/BPM，不当作听辨。SHA256 只判断字节相同。后缀与容器不符会标记 `extension_mismatch`；prepare 按实际容器解码，保留采样率/声道并另存来源记录。
