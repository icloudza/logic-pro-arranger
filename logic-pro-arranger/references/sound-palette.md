# 音色与合成起点

按乐句选择起音、延音、动态响应与音区。预置名称是候选，安装和发声用 `logic_patches.py installed` 与试听确认。参数按素材调整。

**主奏家族表**：

| 家族 | 起音 | 亮度 | 余音 | 候选 |
|---|---|---|---|---|
| 原声三角钢琴 | 快 | 中亮 | 长 | 施坦威大钢琴、好奇之锤、放大钢琴 |
| 处理 / 空间钢琴 | 快 | 中，带空间 | 长 | 柔和共鸣钢琴、平行地球钢琴、小型放大原声钢琴 |
| 磁带 / 立式 / 毛毡钢琴 | 快 | 暗 | 中 | 破损磁带钢琴、旧时录音键盘、复古立式钢琴 |
| 电钢 | 快、圆 | 暖 | 中 | 经典手提箱 Mk IV、沃立舍经典、闪烁电钢琴 |
| mallet / 铃 | 快、硬 | 亮 | 长 | 颤音琴、马林巴琴、典雅铃铛、天蓝水晶合成铃铛 |
| 合成拨弦 | 快 | 可调 | 短 | 轻触弹拨、忧伤弹拨、温暖前奏弹拨 |
| 原声拨弦 | 快 | 中 | 中 | 原声吉他、古典原声吉他、古筝、琵琶 |
| 合成 lead | 中 | 亮 | 持续 | 主室锯齿波主音、70 年代模拟主音、电玩广场主音 |
| 吹管 / 拉弦 | 慢 | 中 | 持续 | 魔音琴长笛、二胡、录音室小提琴 1 |

## 合成器音色配方

参数都是「教程推测」的起点，来自复刻教程，不是制作人本人的工程。单位因合成器而异：Alchemy 的 Detune 是音分，Serum 是 0–1 的刻度，有的旋钮是 0–100，不能直接互换，写参数时带上单位和合成器名。

**MIDI 能替代的合成器动作**（音色里的 LFO 和包络我们改不到时）：

| 想要 | MIDI 里怎么写 |
|---|---|
| 侧链泵感 | `t.pump(bars, "轻" / "中" / "重")`（CC11，见 [MIDI 控制](instruments/midi-controls.md)） |
| 门限、切碎（future bass、trance gate） | 直接写成 1/16 短音（时值 50–70%）或 3-3-2 stab |
| 起音的音高包络（"啾"的上滑、"咚"的下坠） | note-on 同一 tick 预弯 ±2，30–60 ms 内回 0（[MIDI 控制](instruments/midi-controls.md) 的 `glide`） |
| 滑音（glide） | 单音音色上让前后音重叠 10–30 tick（808 20–60） |
| 滤波扫频 | CC74 渐变（先确认音色响应），不响应就用 CC11 + 力度近似 |
| LFO 变速（wobble 的"说话感"） | 同一个音按 1/8、1/16、三连重新起音，配合力度起伏 |

**1. Supersaw**（NCS、progressive、big room、future bass、国风电音的和弦与大 lead）
- 构成：锯齿 7 声 unison，Detune 15–25 音分（Serum 刻度 0.12–0.20；0–100 旋钮约 25–35），Blend 约 75%（Detune 和 Blend 都拉满会丢掉中心、发薄）；低八度 5 声一层、低 6–10 dB；高八度一层只在 drop 加、高通 2 kHz。整组高通 100–150 Hz，低通 2–3 kHz（柔）到全开（亮）；A 0–20 ms、S 100%、R 200–600 ms；加宽只用一种（Chorus、Ensemble、大混响三选一）。
- Logic：Alchemy（Source A 选 VA Saw，Unison 7；Logic 11 起有 Wide Unison；Source B 同波形 −12）；简版用 Retro Synth 的 Wavetable 引擎（Voice Stacking 8、Voice Detune 约 0.55）。预置：经典超级锯齿波、超级锯齿地狱、强音锯齿波、硬失谐锯齿波。
- MIDI：和弦根音放 C3–C4（48–60），顶音 ≤ C6（84）；音色已经带八度层，就别再写八度叠音；100 Hz 以下交给 sub；泵感 `pump("轻")` 或 `pump("中")`。抖音伤感短片默认不用。

**2. Pluck**（Walker 式八分 pluck、琶音、tropical、future house）
- 构成：锯齿（或锯齿 + 方波，可加 5–10% 噪声让起音更脆），unison 3、detune 10–15%；低通 300–800 Hz，滤波包络量 60–80%（约 350 Hz 扫到 2.5 kHz）；A 0–5 ms、D 150–400 ms（Walker 起点 220 ms）、S 0、R 90–250 ms；附点八分延迟 20–30% + 混响 1.5–2.5 s、15–25%。
- Logic：Retro Synth 的 Analog 引擎（Filter Env Amount 拉满，Decay 200–250 ms，Sustain 0）；或 ES2（Filter 1 截止拉到底，Router 里 Env→Cutoff 推满，Env2 Sustain 0，用 Decay 定尾巴）。预置：轻触弹拨、忧伤弹拨、温暖前奏弹拨、简单短弹拨、轻柔钟声弹拨。
- MIDI：音长影响不大，亮度靠力度（拍头 +10，16 分琶音 [+12, −4, +4, −4]）；60–84；叠在钢琴上时低 6–10 dB，只加闪光。

**3. 锯齿 lead**（Walker、Avicii、Muse 式大旋律）
- 构成：锯齿 ×2，unison 5–7、detune 20–30 音分，再叠方波（同八度或 +12，低 6 dB）；A 0–5 ms、D 400 ms、S 70%、R 200 ms；低通 6–9 kHz、共振 10%；滑音 30–60 ms（只在连奏时起作用）；300 ms 后加 5–6 Hz、10–20 音分的颤音。
- Logic：Alchemy（Source A 选 VA Saw，Voices 7，Detune 约 25 音分；Source B 选 VA Square、+12、−6 dB；Glide 40 ms）；ES2（Osc1 锯齿 + Osc2 方波，开 Unison）；Retro Synth Analog（Mono / Legato）。预置：主室锯齿波主音、圣歌主音、叠加超级锯齿波主音、柔和锯齿波主音。
- MIDI：单音，前后音重叠 10–30 tick 才会滑；C5–C6 最穿透，伤感的可以下到 A4（69）；比人声副歌高 4–10 半音，或只在人声空档出现。

**4. 方波 / 芯片 lead**（K-391、Marshmello、Porter Robinson、kawaii、游戏）
- 构成：方波（脉宽 50%，kawaii 25–50%），unison 1–2、detune ≤ 10 音分；A 0、D 150 ms、S 80%、R 80 ms；滑音 15–25 ms（芯片味）到 50–80 ms（Marshmello）。Marshmello 式再叠锯齿 +12，起音音高 +12 → 0（30–60 ms）；Porter《Sad Machine》式：方波 + 方波 +19 半音 −5 音分（30%），带通滤波，D 1.9 s、S 40%。
- Logic：Retro Synth Analog（方波，Voices 设 Mono，开 Glide）；ES2 选 Pulse。预置：电玩广场主音、切割方形合成主音、柔和广场主音、芯片音乐主音。
- MIDI：起音上滑用预弯（+2 → 0）；芯片琶音直接写成 1/16 音符，不靠插件 Arpeggiator（可控、可检查）；hook 音域 ≤ 12 半音（Marshmello 式目标 10）。

**5. 柔和 pad**
- 构成：2–3 个锯齿 ±8 音分（波表或三角波更暖），24 dB 低通 1.5–3 kHz、开键盘跟随；律动型 A 0.05–0.1 s、S 85–90%、R 1.5 s；氛围型 A 1–2 s（冥想类 ≥ 2 s）、R 3–8 s；0.1–2 Hz 的慢 LFO 推截止或波表位置；Chorus / Ensemble + 长混响；高通 150–250 Hz。
- Logic：Alchemy（三个 saw 用 Fine 微调失谐、左右展开，A 0.1 s、S 88%、R 1.5 s，LFO 6 Hz、14% 做颤音，Ensemble + ChromaVerb）；Retro Synth Wavetable（Voice Stacking 8、Voice Detune 0.55）。预置：经典背景音、经典模拟背景音、天鹅绒感合成背景音、云层之上、温暖潮汐；温暖合成背景音 在短片里被否过。
- MIDI：3–4 音的基本和弦，48–72；用 `pad()` 让共同音延长、`legato` 提前；起伏写 CC11。抖音短片不用；纯音乐也别默认铺满全曲。

**6. Reese**（DnB、wave、暗色 future bass、midtempo）
- 构成：两个锯齿相互失谐 ±15–30 音分，低通约 650 Hz、共振约 14%，单音；chorus 约 50%、轻饱和、1/8 同步的三角 LFO 轻扫截止。Reese 层高通 80–120 Hz，下面另放 sine sub（失谐的声部在 100 Hz 以下会互相抵消）。wave 风格低通到 1–3 kHz，让它"哭"而不是"吼"。
- Logic：Retro Synth Analog（两个 saw 失谐，低通 650 Hz，Mono）或 ES2。预置：大宽幅里斯。
- MIDI：长音为主，1–2 小节一个音，根音 E1–A1；Reese 层和 sub 写同一条线。

**7. Wobble**（dubstep、melodic dubstep）
- 构成：锯齿降一个八度（可用低 1–2 个八度的 sine 去 FM 它），低通从约 200 Hz 起，LFO 扫 150 Hz → 1–2 kHz，速率 1/4 慢、1/8 经典、1/16 快，可用三连；失真放在滤波后，切 300–500 Hz、压 3–5 kHz；sub 独立，不过滤波器。
- Logic：Alchemy 或 ES2（LFO 同步 → Filter Cutoff）。预置：大规模摇摆贝斯、未来震荡波贝斯、震荡波渐变贝斯。
- MIDI：LFO 速率改不了，就用音符写"说话感"：同一个音按 1/8、1/16、三连重新起音，配合力度；别和鼓完全同步。伤感短片别用。

**8. 808**（trap、drill、phonk、hip-hop，future bass 的"808 drop"）
- 构成：sine（小音箱用 triangle），音高包络 +24 半音、D 60 ms（慢曲 120 ms）做出"咚"；trap 音量 A 0、D 约 3 s、S 0、R 300 ms；drill D 1 s、S 80–100%；Mono + Legato，滑音 60–100 ms（trap）或 200–300 ms（drill）；Overdrive 约 25%、Mix 50–70%，后接约 400 Hz 低通，或只饱和 200 Hz–2 kHz。
- Logic：ES2（sine，Router 里 Env→Pitch +24、D 60 ms，Mono + Glide）；或在 Quick Sampler / Sampler 里放 808 采样，先找基频、设对 Root Key（看延音段的音高，不看开头那一下"咔"）。预置：808 贝斯、滑行 808 低音、强悍 808 低音、毁灭 808 低音。
- MIDI：根音 28–35；滑音和与底鼓的关系见 [乐器写法](instruments/families.md)。

**9. Sub**
- 构成：sine（或 triangle），Mono、1 声部、不失谐；A 1–10 ms（50 ms 会跟不上底鼓）、S 100%、R 50–100 ms；190 Hz 以上滚降，30 Hz 以下切掉；只弹根音。
- Logic：ES2 或 Retro Synth 单振荡器。预置：深沉超低音、饱满低音、深沉合成贝斯（先确认它不是纯正弦：纯正弦在手机上是隐形的）。
- MIDI：28–35；同一条线 +12 再叠一层锯齿或方波的谐波层（高通 150 Hz，低 6–10 dB）；和底鼓相撞的那一下静音或侧链。抖音短片默认不要。

**10. Organ bass**（90 年代 house、M1 式）「教程推测」，没找到能核实的原始参数
- 构成：sine 16' + sine 或方波 8'（可加 5⅓'），起音一个 5–10 ms 的 click，A 0、D 200–400 ms、S 30–50%、R 50–100 ms，单音、不开 unison。
- Logic：Vintage B3 拉杆 80 8000 000（只开 16' 和 8'），要更厚用 88 8000 000，关 Leslie、开 Percussion；或 ES2 / Retro Synth 的 sine + 方波。预置：经典浩室音乐风琴、脉冲风琴贝斯。
- MIDI：36–48，八度跳的 16 分切分型；B3 不写 24–35；力度不起作用就写 CC11。

**11. 和弦 stab**（house、rave、techno、progressive）
- 构成：老派 house：一个音色里三个振荡器 0 / +3 / +7（按一个键出一个小三和弦），24 dB 低通由包络推开，A 0、D 150–300 ms、S 0–10%、R 80–200 ms，加 Reverb 和 Dimension；techno：方波、带通加高共振（"squelch"）、三连节奏、轻过载；rave：E4 G4 B4 叠 E3、E2，重采样后 12 dB 低通、R 约 1.5 s。
- Logic：ES2 / Retro Synth，或重采样后放进 Sampler。预置：经典和弦、芝加哥和弦、底特律和弦、经典锐舞和弦。
- MIDI：顶音在 C5 附近，最低 ≥ C3；时值 16 分的 50–80%，力度 95–120、第一下最重；"按一个键出和弦"的音色只写单音，否则会和弦叠和弦；progressive house 的附点 3-3-2（起音在 0 / 0.75 / 1.5 拍）。

**12. Future bass 和弦**（Illenium、Marshmello、CORSAK、kawaii）
- 构成：锯齿 2–3 层，unison 7–9、detune 20–30 音分，截止 4–7 kHz、共振 25–35%，A 0–10 ms、R 0.8–1.5 s；第二层方波 3–5 声、低 6 dB 补身体；整组高通 100–200 Hz、150 Hz 处 −3 dB；OTT 或多段压缩。排位去根音、要宽：三音五音放 C3–G3，色彩音放 C4–G5，根音交给 sub。
- Logic：Alchemy supersaw。预置：日出和弦、闪亮和弦、经典超级锯齿波。
- MIDI：节奏直接写进 MIDI——1/16 门限（每个 16 分一个短和弦，时值 50–70%）或 3-3-2 stab；和弦进入时整组预弯 +2 → 0（弯音作用于整轨，和弦一起滑没问题）；侧链 `pump("重")`。音色自带门限或泵感就不要再写。

**13. 类人声 lead**（元音、合唱，切片的替代）
- 构成：Alchemy 的元音 / 共振峰波表，或合唱"啊、哦" pad；真人声切片放进 Quick Sampler（Classic 或 Slice 模式，Loop 开，Glide 30–80 ms，Mono / Legato，起音下滑 −1 ~ −3 半音 30 ms），升调后用 Vocal Transformer 把共振峰回调 −2 ~ −4，保住人味。切片：过零点切，淡入 2–5 ms、淡出 5–20 ms，高通 150–200 Hz，移调 ±4 半音以内最自然。
- 预置：嗓音主音、快速人声、呼吸声音（用户评价：当主旋律"缺少情感"，只做垫底或占位）；空气合唱扫弦、经典合唱、魔音琴合唱。
- MIDI：主情绪交给真人声（vocals-chops.md）；合成人声只做和声垫或第二层。

**14. 铃、拨铃**（FM 铃、玻璃、音乐盒）
- 构成：FM 用非整数比（如 1 : 3.5，金属感；整数比是有音高的电钢、贝斯），调制包络 A 0–10 ms、D 0.5–1 s，并且要比载波包络短（否则一直刺），载波 R 长；或 sine + 高次谐波 + 快衰减；高通 300–400 Hz；附点八分延迟。
- Logic：Retro Synth 的 FM 引擎、EFM1、Alchemy。预置：天蓝水晶合成铃铛、鸣响铁琴、典雅铃铛、玻璃铃铛、温柔正弦铃铛、简单方形铃声、小型弹拨音盒。
- MIDI：72–96，只放在句头句尾，或把副歌骨干音 +12 / +24；力度 50–85（FM 高音区刺，上限 80）；比主旋律低 8–14 dB。

**15. Riser、downlifter、impact**
- 构成：噪声 riser：白噪声 + 带通 200 Hz → 8 kHz（或低通 60–120 Hz → 16 kHz），电平 −18 → −12 → −6 dBFS，Q 2–3 → 5–7。音高 riser：长 build 升 12–24 半音，短 build 升 5–12；合成 riser 比噪声低约 6 dB。downlifter 反过来（音高下行、低通关闭、音量渐出，1–4 小节）。impact = crash（110–127）+ 大底鼓 + sub drop（G1–C2 在 1–2 拍内滑到 C1 附近）+ 2–4 s 混响（高通 200 Hz）；反向镲的尾巴精确对齐 drop 第 1 拍。
- Logic：Remix FX（Tape Stop、Repeater）、Step FX。预置：切割提升器、同时音提升器、和弦音量调节提升器、噪声扫弦、铙钹调节器、瑟瑟轮（下降）、等离子降落、永不停歇提升器、宇宙冲击。
- MIDI：一个长音 + CC74 或弯音上行（弯音要音色范围 ≥ 12，否则改写成半音阶 pluck 一级一级往上爬）；drop 前的留空窗口里不能有 note_on；每 8 小节最多 1–2 个明显的 FX；sub drop 不和 808 / sub 同时响。

**16. 磁带 / lofi 处理**
- 构成：失谐 5–10 音分 + 0.3–1 Hz 的音高 LFO（wow & flutter 15–20%）→ 磁带饱和 25–30% → 旋律类低通 4–6 kHz（整体 8–10 kHz；更暗的一档 3 kHz）→ 鼓 bitcrush 8–12 bit → 黑胶或磁带噪声 −30 ~ −24 dB。毛毡钢琴：力度上限 75、下限 30，低通 6 kHz，150–250 Hz +2 dB，500 Hz −2 ~ −3 dB，Room 1.2–1.6 s。
- Logic：ChromaGlow（Magnetic 或 Retro Tube，需要 Apple 芯片）、Tape Delay（带 flutter）、Bitcrusher、Channel EQ、MIDI FX 的 Velocity Processor、Overdrive。预置：破损磁带钢琴、旧时录音键盘、旧 VHS 磁带合成器、低保真弹拨。
- MIDI：不量化到 100%；键盘整体晚 10–30 ms（`push_ms`）；鼓 swing 55–60%；不写 127。

## GM 占位 → 导入后换成什么

导入状态表见 [GM 导入](instruments/gm-programs.md)。候选中文名写进每轨的 `patch`（check_midi.py 会列成换音色清单），导入后照着搜。

| 角色 | 占位 program | 导入后换成（候选） | 换完还要做 |
|---|---|---|---|
| 钢琴、电钢、风琴 | 0 | 施坦威大钢琴、经典手提箱 Mk IV、音轮风琴 | 钢琴写 CC64；风琴最低写 36 |
| 钟、铃、音乐盒、mallet | 10 | 鸣响铁琴、天蓝水晶合成铃铛、颤音琴、马林巴琴 | — |
| 吉他、古筝、琵琶、竖琴 | 25 | 原声吉他、古筝、琵琶、空间竖琴 | 古筝、琵琶要弯音就单独一轨，并请用户设弯音范围 |
| 合成 lead、pluck、琶音、切片导引轨 | 81 | 轻触弹拨、主室锯齿波主音、电玩广场主音 | 自带琶音器的预置别配写好的琶音 |
| 贝斯、808、sub | 33 或 81 | 深沉合成贝斯、经典击弦浩室贝斯、808 贝斯、厚重灵魂电贝斯 | 核对区域移调 |
| 弦乐 | 48 或 89 | 国王十字、录音室大提琴、真实弦乐、魔音琴小提琴 | 录音室弦乐器写 CC1；组音色的轨用通道 1 |
| 铜管、木管 | 89 | 录音室长号 1、录音室小号 1、魔音琴铜管乐、魔音琴长笛 | 按音源的实际映射写表情 CC |
| pad、合唱、合成人声 | 89 | 经典背景音、经典合唱、嗓音主音 | 按声部作用选择 |
| 鼓 | 第 10 通道 | 模拟烟雾、根源、南加风情、Roland TR-808、Roland TR-909 | 换成 DMD 鼓组后，下面的轨号会重排，轨名不变 |
