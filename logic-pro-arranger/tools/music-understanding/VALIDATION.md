# 验证范围

```bash
make test PYTHON="$HOME/.cache/music-understanding/runtime-py311/bin/python"
```

自动测试覆盖 C/NumPy 数值一致性、原始峰值、立体声抵消、损坏 WAV、音高边界、静音/噪声弃答、文件保护、缓存、查询分页、音符来源选择、起音时序与模型冲突。

C 核心同时使用 AddressSanitizer/UndefinedBehaviorSanitizer 构建执行边界测试。Apple 测试区分原生运行与模拟非 macOS 路由；Linux 真机需单独验收。

这些检查不构成真实音乐识别准确率评测。Basic Pitch 可能将泛音识别为音符，Beat This 可能判错速度/强拍；跨模型一致也不是正确性证明。需用人工标注与回听评价目标素材。
