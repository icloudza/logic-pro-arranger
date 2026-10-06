# 第三方代码与模型

只直接收录两个原生库；原文件与许可证未改动。完整提交号和文件 SHA-256 在 `native/vendor/sources.json`。

| 收录代码 | 上游 | 许可证副本 |
|---|---|---|
| cJSON 1.7.19 | https://github.com/DaveGamble/cJSON | `native/vendor/cjson/LICENSE`（MIT） |
| dr_wav | https://github.com/mackron/dr_libs | `native/vendor/dr_libs/LICENSE`（选用 MIT No Attribution；上游同时提供 Unlicense） |

YIN 差分和累积归一化内核自行实现，没有移植 librosa/pYIN 源码。算法出处：de Cheveigné & Kawahara, *YIN, a fundamental frequency estimator for speech and music*, JASA 111(4), 2002, DOI 10.1121/1.1458024。当前实现是单音基线，不实现 pYIN 概率模型。

以下通过安装包、动态库或外部程序调用，没有复制进本项目；精确安装版本见环境锁文件。

| 依赖 | 上游 | 用途 |
|---|---|---|
| FFmpeg | https://github.com/FFmpeg/FFmpeg | 探测真实格式、统一解码 |
| SQLite | https://www.sqlite.org/ | 原生/worker 共用数据库 |
| OpenSSL | https://github.com/openssl/openssl | 原生 SHA-256 |
| librosa | https://github.com/librosa/librosa | pYIN、节拍和 MIR 基线 |
| Beat This! | https://github.com/CPJKU/beat_this | 节拍/下拍推理 |
| Basic Pitch | https://github.com/spotify/basic-pitch | ONNX 复音转录 |
| torchcrepe | https://github.com/maxrmorrison/torchcrepe | 单音 F0 交叉验证 |
| LAION CLAP | https://github.com/LAION-AI/CLAP | 音频/文字向量 |
| audio-separator | https://github.com/nomadkaraoke/python-audio-separator | 可选分离适配器，未验收 |
| whisper.cpp | https://github.com/ggml-org/whisper.cpp | 可选 ASR 适配器，未验收 |

本机 CLAP 权重来自发布者 https://huggingface.co/lukewys/laion_clap/blob/main/music_audioset_epoch_15_esc_90.14.pt ，Beat This 使用上游 `final0`；Basic Pitch 和 CREPE 权重由各自安装包提供。实际模型文件哈希写入运行记录。项目不打包模型权重或用户采样。第三方代码、权重及训练数据的许可分别适用；进程隔离不改变许可义务。

## 系统框架

Apple 桥接仅调用公开 API，不复制或分发系统模型：

- [SoundAnalysis 内置分类请求](https://developer.apple.com/documentation/soundanalysis/snclassifysoundrequest)
- [SpeechAnalyzer 文件分析](https://developer.apple.com/documentation/speech/speechanalyzer/analyzesequence(from:))
- [AssetInventory 语言资源管理](https://developer.apple.com/documentation/speech/assetinventory)

macOS 运行时通过可用性检查选择，非 macOS 完全绕开该桥接。
