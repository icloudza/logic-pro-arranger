# Music Understanding

一个本地命令行工具，按任务组合分析采样、旋律片段、循环和整曲。输出有来源、时间范围和不确定性标记的事件；查询时只取需要的部分。

C17 负责文件扫描、SHA-256、SQLite 查询、WAV 测量和 YIN 内核。Python 通过 ctypes 调用 C DSP，并在独立 worker 进程运行模型、关系分析、绘图和导出。macOS 另有一个薄 Swift 桥接进程，用于调用系统的 SoundAnalysis 和 Speech 框架。神经推理仍由 PyTorch/ONNX 的原生后端执行。当前不是实时音频插件，也不是整曲自动扒谱的成品。

## 运行

运行 `sh scripts/bootstrap.sh core` 编译并安装基础环境；可选模型用 `models`。默认环境为`~/.cache/music-understanding/runtime-py311`，可用 `MUSIC_PYTHON` 覆盖；复制到其他机器需重建。数据库、解码副本、权重及数组在用户缓存目录，源采样只读。

```bash
./build/music inspect '/path/to/audio.wav'
./build/music analyze '/path/to/audio.wav' --branch sample
./build/music query ASSET_ID summary
./build/music query ASSET_ID events --from 0.2 --to 1.5 --type note --limit 20
./build/music query ASSET_ID evidence --id EVENT_ID
./build/music audio ASSET_ID --from 0.2 --to 1.5 --overlay notes --out /tmp/review.wav
./build/music render ASSET_ID --layers f0,beats,notes --out /tmp/review.png
./build/music export ASSET_ID --out /tmp/analysis-package
```

`inspect` 的 C 入口只登记元数据，不提前解码。`analyze` 按真实格式调用 FFmpeg，保留原始采样率、声道和时间零点，再为算法生成重采样视图。ID 是源文件 SHA-256；不同编码但听起来一样的文件不会被当成字节副本。

`--home CACHE` 放在子命令前；也支持 `MUSIC_HOME`。`MUSIC_PYTHON` 覆盖 worker 解释器，`MUSIC_DSP_LIBRARY` 覆盖 C 动态库。输出已存在时拒绝覆盖。返回码：0 成功，1 输入/运行错误，2 部分分析失败；stderr 是日志，stdout 是 JSON。

## 分支和后端

分支只选择任务，没有曲式或审美模板。`--tasks` 用逗号组合、覆盖分支默认值。

| 入口/任务 | 实际行为 |
|---|---|
| `sample` | 包络、频谱、起音、单音音高 |
| `drum` | 包络、频谱、起音；不臆测 kick/snare 类别 |
| `phrase` | 包络、起音、Basic Pitch 单乐器复音转录（macOS 优先 Core ML，其他系统 ONNX） |
| `loop` | 包络、频谱、起音、节拍候选、循环切点建议 |
| `vocal` | 包络、起音、单音 F0；不默认把无音高帧当休止 |
| `song` | 包络、频谱、节拍、三和弦候选、谱变化边界、和声重复候选 |
| `perception` | CLAP 分段向量、给定文字标签排序 |
| `sounds` | macOS 原生 SoundAnalysis；非 macOS 可用显式配置的 CLAP |
| `lyrics` | macOS 26+ 的 SpeechAnalyzer 本地转写；可显式选择 whisper.cpp |
| `stems` | audio-separator 适配器，未在本机验收 |

```bash
# C 默认音高；pYIN 可作独立对照；ensemble 要求 C 与 CREPE 一致才给根音
./build/music analyze FILE --tasks pitch --pitch-backend ensemble
./build/music analyze FILE --tasks pitch --pitch-backend pyin
./build/music analyze FILE --tasks rhythm,loop --beat-backend beat-this
./build/music analyze FILE --tasks notes
./build/music analyze FILE --tasks rhythm --bpm 130
./build/music compare FILE_A FILE_B --mode melody
```

`--bpm` 是 librosa 的用户先验，不能当成测量。Beat This 与该先验或独立速度估计冲突时，保留模型候选并清空供对齐使用的网格。默认 `auto` 分支按文件名和时长选路，不是乐器识别。

## 检索采样

用户指定目录且任务需要检索时，再为该目录建立索引；没有默认采样库或磁盘自动发现流程。

```bash
./build/music index '<用户提供的目录>'
./build/music search --text 'spotless' --limit 5
./build/music index /path/to/subset --tasks timbre
./build/music search --similar FILE --limit 5

./build/music analyze FILE --tasks perception --clap-model /path/to/CLAP.pt \
  --tags 'A piano note' 'A deep bass sound' 'A metallic bell'
./build/music index /path/to/subset --tasks perception --clap-model /path/to/CLAP.pt
./build/music search --semantic 'A soft piano note' --clap-model /path/to/CLAP.pt
```

普通搜索只查文件名；`--similar` 比较已分析素材的频谱形状；`--semantic` 只检索使用同一 CLAP 权重生成的向量。CLAP 使用 HTSAT-base、固定 10 秒窗口，结果带命中时段；短音频由上游 repeatpad 处理。英文描述优先，中文检索未评测。相似分数不等于正确率。默认索引不运行模型；C 扫描遇坏文件会报错退出，已登记项目保留。

## macOS 原生能力

`doctor` 会实际检查平台、helper、声音分类模型及中文 Speech 模型状态。SoundAnalysis 自带声音分类器；SpeechAnalyzer 使用系统管理的本地语音模型。Core ML 是运行时，不自带音符转录模型，Basic Pitch 仍使用其上游权重。

```bash
./build/music doctor
./build/music analyze FILE --tasks sounds
./build/music analyze FILE --tasks notes --notes-backend coreml
./build/music analyze FILE --tasks lyrics --speech-backend apple --locale zh-CN
# 语言资源缺失时显式下载安装；已有资源不会重复下载
./build/music analyze FILE --tasks lyrics --speech-backend apple --locale zh-CN --install-apple-assets
```

macOS 默认分支会加上 `sounds`；显式 `--tasks` 保持精确选路。Apple 分类输出带原音时段和 top-5 候选，窗口由模型允许范围决定；过短音频不伪造上下文。中文语音返回文字、字词时间及备选，不作为歌词识别质量保证。

`notes --notes-backend auto` 在 macOS 优先 Core ML，允许系统选择 CPU/GPU/Neural Engine；不声称每次一定用 ANE。加载失败时记录原因并回退 ONNX，显式 `coreml` 则报不可用。

非 macOS 不编译、不加载、不启动 Apple helper，默认分析不追加 `sounds`；显式 `sounds` 可指定 `--clap-model`，语音可传 `--whisper-model`。SoundAnalysis 要 macOS 12+；SpeechAnalyzer 要 macOS 26+ 和相应 SDK。旧系统语音返回不可用，不自动调用联网识别；未取得的系统权限不会被当作已授权。`MUSIC_APPLE_HELPER` 可覆盖桥接程序路径。

## 数据与精度边界

- 秒坐标始终是原文件解码后的时间。事件带来源、算法实现 SHA、依赖版本、原始数组引用和时间分辨率；`time_error_s` 只是分析分辨率，不是统计误差界。
- 概率未校准时为 `null`，不会把 periodicity、cosine 或激活值改称置信概率。模型安装、推理成功、音乐识别正确是三件事。
- `query summary/events` 默认使用每项任务最近一次运行；局部重分析也会成为该任务的最新结果。`query runs` 保留所有运行，不自动把不同算法/不同区间拼成一条时间线。
- 根音 metadata 与算法冲突会列入 `conflicts`。`correct ASSET --root-midi 36 --reason '...'` 单独记录人工修正，不抹去原观察。
- 节拍含半速/倍速候选；还没有可靠的小节网格。和弦仅 24 个大小三和弦模板，结构仅谱变化，重复仅固定窗口 chroma 相似。不能由此宣称识别了副歌、和声张力、主旋律声部或混音遮盖。
- RMVPE、复杂和弦模型、鼓分类、跨声部对齐/遮盖/张力、音频 LLM、人工标注校准集尚未实现。分离和 whisper.cpp 适配器尚未运行验收；Apple 中文语音链路已验证。

导出包含 `manifest.json`、`summary.json`、`timeline.json`、`relations.json`、`source.wav` 和 `details/`。引用改为包内相对路径，原始源路径保留作溯源。

## 安装与验证

已验证 macOS arm64、Python 3.11、Apple clang；其他平台未运行验收。需要 FFmpeg/ffprobe、make 和 C 编译器；macOS 使用系统 SQLite/CommonCrypto，Linux 需要 pkg-config、SQLite/OpenSSL 开发库。第三方模型组合锁在 `requirements-macos-arm64-py311.lock`，不要直接套到 CUDA/Linux 环境。

```bash
./scripts/bootstrap.sh core     # C + 基础 Python，不下载神经权重
./scripts/bootstrap.sh models   # 加 Beat This、CREPE、Basic Pitch、CLAP 依赖
make sanitize
MUSIC_PYTHON="$HOME/.cache/music-understanding/runtime-py311/bin/python" \
  make test PYTHON="$HOME/.cache/music-understanding/runtime-py311/bin/python"
"$HOME/.cache/music-understanding/runtime-py311/bin/python" scripts/evaluate.py \
  --out /tmp/music-evaluation-new --models
```

Basic Pitch 在 Python 3.12/macOS 的上游 TensorFlow 限制无法满足，所以模型 worker 固定使用 3.11；Basic Pitch 的 ONNX 和 Core ML 两条路径分别验收。CREPE 的 `pkg_resources` 依赖需 setuptools <81。首次使用 Beat This/CLAP 可能下载权重或文本编码器；音频分析在本机完成。

测试与性能记录见 [VALIDATION.md](VALIDATION.md)，上游来源与许可见 [THIRD_PARTY.md](THIRD_PARTY.md)。

默认音符事件选择 `notes` 优先、`pitch` 兜底；`query events --task pitch` 读取该任务的原始假设。`note_alternative` 不进入默认音符回放。
