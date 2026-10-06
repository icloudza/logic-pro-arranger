---
name: logic-pro-arranger
description: 为 Logic Pro 写旋律、伴奏、beat、配乐、remix 和 DJ 版；生成多轨 MIDI、试听和可重现脚本，处理采样、人声切片、分轨、参考音频与作品修改。
---

# Logic Pro 编曲

## 创作与修改

从核心乐句或律动发展作品，结构随材料、歌词或画面组织。旋律看 [melody.md](references/melody.md)，展开看 [arrangement.md](references/arrangement.md)。需要比较时生成对照小样，按实际听感修改重音、停顿、走向与和声落点；统计仅用于定位。

修改已有作品另存版本，用 `SPEC.locked` 与 `check_midi --compare` 核对保留部分。素材只查用户提供的文件或目录。本机记录在 `$LOGIC_ARRANGER_CONFIG`（默认 `~/.config/logic-pro-arranger/`），按任务读取：`logic-environment.md` 记音色安装与发声观察，`logic-import.json` 是检查器读取的导入差异，`user-profile.md` 记用户明确表达的偏好。实测与记录不一致时，先向用户说明拟改内容和证据，经同意后再写入该目录并提交其本地 Git；skill 本体不作运行时记录。

只澄清会改变任务含义的歧义；用户要求直接完成时自主选定方向。不能实际聆听时说明验证范围，指标不作审美评分；没有 Logic 的环境不声称已验证工程听感。

## 生成与验证

以下路径相对本 `SKILL.md` 所在目录（插件包中为 `skills/logic-pro-arranger/`）。从该目录定位脚本，作品写入用户工作目录。MIDI 工具仅用 Python 标准库；音频依赖见 [music-understanding.md](references/music-understanding.md) 和各工具 `--help`。

```bash
python3 scripts/new_song.py --title 草稿 --key 'F minor' --bpm 88 --root '<用户工作目录>'
# 编辑生成脚本的 SPEC 与 compose()，写好音乐后再运行：
python3 '<作品目录>/草稿_生成脚本.py'
python3 scripts/check_midi.py '<作品目录>/草稿.mid' --script '<作品目录>/草稿_生成脚本.py'
python3 scripts/preview.py '<作品目录>/草稿.mid'
# 写好 SPEC.hook_candidates 后，可单独试听候选：
python3 scripts/preview.py --hooks '<作品目录>/草稿_生成脚本.py' --solo
```

空白脚本没有音符，检查会报 T0。API 与候选写法见 [song-spec.md](references/song-spec.md)，音名/时间/输出规则见 [conventions.md](references/conventions.md)，检查范围见 [quality-checks.md](references/quality-checks.md)。同一生成脚本、MIDI 试听或检查报告可重跑：工具核对隐藏的 `.文件名.build.json` 记录，只有自己上次写出且未被外部修改的文件允许重建。删除记录后需另存或明确使用 `--force`（生成/试听支持）；源文件与母带不作为自动重建目标。

检查器自动读取本机 `logic-import.json`，可用 `--logic-profile 文件.json` 覆盖。退出码 0=检查通过、1=检查发现 ERROR、2=输入或输出错误。`preview --bars 3` 试听单小节；`--hooks --out 候选.mp3` 多候选写为 `候选-A.mp3` 等。

生成脚本内嵌库版本用 `--version` 查看。升级时另存：

```bash
python3 scripts/upgrade_song.py 旧_生成脚本.py --out 新_生成脚本.py
```

## Logic 与音频

实际操作看 [logic-creator-studio.md](references/logic-creator-studio.md)，音色用 `logic_patches.py installed 关键词` 查询。GM 试听核对乐句与结构，最终音色在 Logic 中回听。交付音频注明 BPM、时间零点和放置小节。

音频分析、事件查询与模型安装看 [music-understanding.md](references/music-understanding.md)。只做采样文件索引时用 `sample_library.py scan --root '<用户路径>' --index '<库外索引.json>'`；`search/summary/prepare` 读取该索引，`--index` 可放在子命令前或后。

## 按需查阅

| 问题 | 资料 |
|---|---|
| 方向与选择记录 | [brief-to-card.md](references/brief-to-card.md) |
| 和声 / 调式 | [harmony.md](references/harmony.md) / [scales-modes.md](references/scales-modes.md) |
| 鼓、贝斯与伴奏型 | [rhythm-grooves.md](references/rhythm-grooves.md) |
| 乐器写法 / 合成与音色 | [instruments.md](references/instruments.md) / [sound-palette.md](references/sound-palette.md) |
| 听感反馈 | [feedback-dictionary.md](references/feedback-dictionary.md) |
| 人声、切片、分轨、变速移调 | [vocals-chops.md](references/vocals-chops.md) |
| 音频 / 艺人参考 | [reference-analysis.md](references/reference-analysis.md) / [producers.md](references/producers.md) |
| Logic 功能 / 导出 | [logic-features.md](references/logic-features.md) / [mix-export.md](references/mix-export.md) |
| 发行事项 | [release-copyright.md](references/release-copyright.md) |

风格资料只提供可选择的音乐语言，不提供必填参数：
[华语抒情](references/styles/chinese-pop-ballad.md)、[短视频流行](references/styles/douyin-pop.md)、
[国风](references/styles/guofeng.md)、[旋律电音](references/styles/edm-melodic-pop.md)、
[House](references/styles/house-family.md)、[Bass music](references/styles/bass-music.md)、
[中文 DJ](references/styles/dj-club-cn.md)、[钢琴](references/styles/piano-newage.md)、
[影视游戏](references/styles/cinematic-game.md)、[摇滚](references/styles/rock.md)、
[R&B / City Pop / 日韩流行](references/styles/rnb-citypop-jpop-kpop.md)、[嘻哈 / Lofi / 拉丁](references/styles/hiphop-lofi-latin.md)。
