# Logic Pro 编曲 skill

入口：[SKILL.md](logic-pro-arranger/SKILL.md)。skill 包含 MIDI 工具、音频工具源码、按需参考和测试；个人记录位于独立 Git 目录 `~/.config/logic-pro-arranger/`。

## 安装与打包

只用 MIDI 工具：复制 `logic-pro-arranger/`，使用 Python 3.9+。音频分析需在目标机器安装依赖与编译 C 核心：

```bash
cd logic-pro-arranger
sh tools/music-understanding/scripts/bootstrap.sh core
# 可选神经模型依赖：将 core 换成 models
python3 scripts/music.py doctor
```

需要 Python 3.11、make、C 编译器、ffmpeg/ffprobe。macOS 原生桥接需要 Xcode 命令行工具，C 核心使用系统 SQLite/CommonCrypto；Linux 需要 SQLite/OpenSSL 开发库、pkg-config。非 macOS 跳过 Apple 框架。worker 默认使用`~/.cache/music-understanding/runtime-py311`；自定义环境用 `MUSIC_PYTHON`。

分发用源码包，排除本机层、构建、虚拟环境、缓存与软链：

```bash
python3 package_skill.py --out /tmp/logic-pro-arranger.zip
```

在另一台机器解压后重新运行 bootstrap；不复制二进制或 venv。模型权重和采样不进包。

## 验证

```bash
make -C logic-pro-arranger/tools/music-understanding test PYTHON="$HOME/.cache/music-understanding/runtime-py311/bin/python"
$HOME/.cache/music-understanding/runtime-py311/bin/python -m pytest logic-pro-arranger/tests -q
```

测试始终针对源码树：`tests/conftest.py` 设置 `PYTHONPATH` 与 `MUSIC_ROOT`，由 C 核心启动的 worker 也使用源码，改动 `src/` 后无需重装即可测试。runtime 是普通安装，实际运行 skill 前仍需重新执行 bootstrap，让 runtime 使用新代码。

测试使用临时合成音频与 MIDI。覆盖文件保护、字节解析、锁定比较、音频时序、采样格式、后端选择与工具入口。模型运行成功不代表旋律审美或真实音频识别已验收。

修复记录见 [CHANGELOG.md](CHANGELOG.md)，验证证据见 [AUDIT-VALIDATION.md](AUDIT-VALIDATION.md)。第三方声明见 [NOTICE.md](logic-pro-arranger/NOTICE.md) 与 [THIRD_PARTY.md](logic-pro-arranger/tools/music-understanding/THIRD_PARTY.md)。
