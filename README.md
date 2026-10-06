# Logic Pro 编曲 skill

支持 Claude Desktop / Claude Code 插件与 Codex 插件 / skill。入口：[SKILL.md](logic-pro-arranger/SKILL.md)。包含 MIDI 工具、音频工具源码、按需参考和测试；个人记录位于独立 Git 目录 `~/.config/logic-pro-arranger/`。

## Claude Desktop 插件

从 [Releases](https://github.com/icloudza/logic-pro-arranger/releases) 下载 `.plugin`，或自行打包：

```bash
python3 package_skill.py --out dist/logic-pro-arranger-0.2.1.plugin
```

在 Claude Desktop 的 **Plugins → Upload local plugin** 中选择该文件。也可将输出名设为 `.zip`，内容相同。不要上传 GitHub 的源码 ZIP 或独立 skill 包。

插件包根目录包含 `.claude-plugin/plugin.json`，技能位于 `skills/logic-pro-arranger/`，脚本、参考和音频源码一并打包。格式依据 [Claude 插件规范](https://code.claude.com/docs/en/plugins-reference)。仓库本身也可由 Claude Code 使用 `claude --plugin-dir .` 加载。

运行能力取决于执行环境：MIDI 工具需要 Python，音频工具需安装下列依赖；只有能访问 macOS 宿主机及 Logic 的环境才能操作 Logic。插件安装本身不提供桌面控制或文件访问权限。

## Codex

插件方式：添加本仓库的插件市场，然后在插件界面选择 **Logic Pro Arranger** 安装：

```bash
codex plugin marketplace add icloudza/logic-pro-arranger
```

本地安装可下载 Release 中不带 `-skill-` 的 `.zip`，解压到固定目录，再执行 `codex plugin marketplace add '<解压目录>'`。包内含 `.codex-plugin/plugin.json` 与 `.agents/plugins/marketplace.json`，使用 [Codex 支持的插件格式](https://developers.openai.com/plugins/build/plugins)。Claude 与 Codex 插件包共用内容。

独立 skill 方式：将 `-skill-` 包内的 `logic-pro-arranger/` 放到 `~/.agents/skills/`，或项目的 `.agents/skills/`。在 Codex 中用 `$logic-pro-arranger` 调用。目录依据 [Codex 技能文档](https://learn.chatgpt.com/docs/build-skills)；插件和独立 skill 选一种安装即可。

## 独立 skill 与音频依赖

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
python3 package_skill.py --format skill --out dist/logic-pro-arranger-skill.zip
```

在另一台机器解压后重新运行 bootstrap；不复制二进制或 venv。模型权重和采样不进包。

## 自动发布

同时更新 `.claude-plugin/plugin.json`、`.codex-plugin/plugin.json` 的版本和 `CHANGELOG.md`，提交后推送对应标签：

```bash
git tag v0.2.1
git push origin main v0.2.1
```

[Release workflow](.github/workflows/release.yml) 在 Python 3.9 / 3.11 上验证打包与 MIDI 工具；标签与清单版本一致后创建 Release，上传通用 `.plugin`、通用 `.zip`、独立 skill `.zip` 和 `SHA256SUMS`。预发布标签标记为 prerelease；失败可在 Actions 输入同一标签重跑，已公开的 Release 不覆盖。普通分支推送与 PR 只运行验证。

本地构建同一套发布文件：

```bash
python3 package_skill.py --release v0.2.1 --out dist/release-0.2.1
```

## 验证

```bash
claude plugin validate . --strict
make -C logic-pro-arranger/tools/music-understanding test PYTHON="$HOME/.cache/music-understanding/runtime-py311/bin/python"
$HOME/.cache/music-understanding/runtime-py311/bin/python -m pytest logic-pro-arranger/tests -q
```

插件包需解压后对解压目录执行 `claude plugin validate <目录> --strict`；打包测试检查清单、技能路径和解压后脚本运行。

测试始终针对源码树：`tests/conftest.py` 设置 `PYTHONPATH` 与 `MUSIC_ROOT`，由 C 核心启动的 worker 也使用源码，改动 `src/` 后无需重装即可测试。runtime 是普通安装，实际运行 skill 前仍需重新执行 bootstrap，让 runtime 使用新代码。

测试使用临时合成音频与 MIDI。覆盖文件保护、字节解析、锁定比较、音频时序、采样格式、后端选择与工具入口。模型运行成功不代表旋律审美或真实音频识别已验收。

修复记录见 [CHANGELOG.md](CHANGELOG.md)，验证证据见 [AUDIT-VALIDATION.md](AUDIT-VALIDATION.md)。第三方声明见 [NOTICE.md](logic-pro-arranger/NOTICE.md) 与 [THIRD_PARTY.md](logic-pro-arranger/tools/music-understanding/THIRD_PARTY.md)。
