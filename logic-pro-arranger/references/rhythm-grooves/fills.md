### 六种 fill（写在 fill 那一小节，本小节其他行的对应格要空出来）

```
# F1 一拍军鼓（流行、摇滚）：90 / 72 / 90 / 108
snare     .... .... .... xoxX   90 a18 g72
# F2 两拍通鼓下行（抒情、摇滚副歌前）：84 → 108
tom_hi2   .... .... xx.. ....   84
tom_hi    .... .... ..xx ....   90
tom_mid   .... .... .... xx..   96
tom_low   .... .... .... ..x.   102
tom_floor .... .... .... ...x   108
# F3 一拍空（卡点、副歌入口）：本小节所有行的 13–16 格写成 "...."，下一小节第 1 拍 kick + crash 104–112
# F4 一小节 16 分（摇滚大 fill：snare → 高 → 中 → 低，kick 保持四分）：90 → 114
snare     xxxx .... .... ....   90
tom_hi2   .... xxxx .... ....   96
tom_mid2  .... .... xxxx ....   102
tom_low   .... .... .... xxxX   106 a8
kick      x... x... x... x...   96
```

```python
# F5 EDM snare build（8 小节；短片压成 4 小节，每种密度 1 小节）：四分 → 八分 → 16 分 → 32 分，力度 60 → 124
for k, bar in enumerate(build_bars):
    step = [1, 1, .5, .5, .25, .25, .125, .125][k]
    n = int((4 if k < 7 else 3) / step)                 # 最后一小节只写前 3 拍，第 4 拍留空
    d.roll(bar, 0, n, step, "snare", 60 + 8 * k, 67 + 8 * k)
# F6 trap / drill 收尾：最后 2 拍删掉 kick 和 808，hh 换 32 分 roll，末拍 snare 16 分三连
d.roll(bar, 2, 8, 1/8, "hh", 55, 105); d.roll(bar, 3, 6, 1/6, "snare", 70, 110)
```

- 其他：DnB 末拍 snare 16 分夹鬼音；Jersey 末拍 kick 16 分三连 6 下（90→110）；lofi 用 kick 双击或 snare 前 15 ms 倚音；"半拍下沉"——段落最后一小节 snare 只留第 9 步、hat 全停，副歌前吸一口气。
