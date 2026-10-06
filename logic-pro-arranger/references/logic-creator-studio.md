# Logic 工程操作

## 打开与导入

应用名称和工程路径以当前环境为准。打开 MIDI 前先读取当前文档与未保存状态：先确认进程存在，再读文档；直接 `tell application` 可能启动应用。

```bash
osascript -e 'tell application "System Events" to count (processes whose bundle identifier is "com.apple.mobilelogic")'
osascript -e 'tell application "Logic Pro Creator Studio" to get name of documents'
osascript -e 'tell application "Logic Pro Creator Studio" to get modified of front document'
```

有未保存内容先保留当前工程；双击或打开新 MIDI 可能替换当前文档。将 MIDI 导入新工程或指定工程，核对速度、拍号、段落 marker、轨道通道与移调。打开后读取播放状态，需要时点停止。

音色安装、导入异常的本机记录见可选 `~/.config/logic-pro-arranger/logic-environment.md`。

## 音色与播放

```bash
python3 scripts/logic_patches.py installed 关键词
```

`.patch` 存在只证明有预置，加载后确认采样可用、音符能发声。导入成外部 MIDI 轨时，换成软件乐器并选择音源。核对区域移调、音域、鼓键映射、CC 响应和踏板。

换音色前选中目标轨；载入 Track Stack 后重新读取轨道列表。候选轨逐条独奏比较，避免相同旋律叠加。UI 操作以当前可见标签和状态为准。

## 音频与保存

人声、切片、分轨交付时注明原文件强拍时间、目标 BPM 和放置小节。拖入后核对工程时间零点、Flex/Smart Tempo 是否再次伸缩音频。

保存为新的 `.logicx`，确认名称、路径及音频资源是否随工程保存。重新打开后核对轨道、区域和缺失资源。

## 并轨

确定范围、独奏/静音、效果旁通、循环边界和尾音。无损母带与试听分别命名。转码和测量见 [mix-export.md](mix-export.md)。

## 用 UI 换音色

1. 单击目标轨图标，确认选中；点轨名可能进入改名。
2. 资源库搜索框先点清空，再输入当前语言的音色名。中文界面先用中文搜索；不要用回车提交，结果通常实时刷新。名称可用 `logic_patches.py` 查询。
3. 在当前结果中双击音色，重新读取轨头、通道条和资源库名称；避免复用旧坐标。DMD 不一定更改轨名。
4. 加载堆栈后重新读取轨道行和编号，逐条独奏候选，确认 MIDI、区域移调、音源输出。

## 自动化故障排查

下表是 Creator Studio 操作中观察到的失败模式；先检查当前状态；新的版本和复现日期经用户同意后记入外部本机记录。

| 现象 | 检查与处理 |
|---|---|
| 资源库无结果 | 清空后用中文名重搜；避免回车插入换行 |
| 单击结果或分类列表无效 | 改用搜索结果并双击 |
| 清空搜索后轨名成输入框 | 在轨名输入框按 Esc，再继续搜索 |
| 并轨菜单变灰 | 检查隐藏的“选取项目”窗口与当前文档；保存后关闭并重开 `.logicx` |
| DMD / 总和堆栈导致轨号变化 | 重新读取轨道列表，以当前行与名称定位 |
| DMD 换好但轨名未变 | 用通道条或资源库顶部确认 |
| 后台改轨名未生效 | 重新读回；可在 MIDI 的轨名 meta 中预写 |
| 后台拖动、静音没反应 | 检查窗口焦点、工具能力与读回状态；确实受限时说明需手动完成的具体动作 |
| `record_sequence` 时间错位 | 曾出现首音移至第 1 小节、整体压缩及残留“前往位置”窗口；先做短片段验证，失败时导入完整新 MIDI |
| `channels_exhausted` | 用 `logic_system health`（若可用）检查 CoreMIDI 端口；不要反复重试，核对占用和实际静音状态 |
| `set_instrument` 返回 `header_unreadable` | 改用资源库 UI 换音色并读回 |
| 打开后自动播放 | 点击走带停止并确认已停止 |

检查并轨菜单启用状态：

```bash
osascript -e 'tell application "System Events" to tell process "Logic Pro Creator Studio" to get enabled of menu item "并轨" of menu 1 of menu bar item "文件" of menu bar 1'
```

program 40/42/48 静音、33 区域 +12 等实测导入差异保存在外部 `logic-import.json`；检查器自动载入，并提示重新核对当前环境。界面工具的权限或能力以本次返回为准。
