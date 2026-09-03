# Canonical timeline

## 不变量

- 唯一内部时基是 `16,000 samples/s` 的音频主文件 sample index。
- 所有持久化与协议区间使用 `start_sample`、`end_sample`，类型是非负 signed int64。
- 区间统一采用半开语义 `[start_sample, end_sample)`；空区间允许，反向区间禁止。
- 不在数据库、消息或循环中累加浮点秒数。秒和毫秒只可出现在输入解析或显示／导出
  边界。
- worker 处理切片时请求必须携带绝对 offset；局部 sample 返回 core 前必须加回该
  offset。中间片段不得重新定义全局零点。

## 精确换算

16 kHz 下每毫秒正好是 16 samples：

```text
sample = millisecond × 16
exact_millisecond = sample / 16
```

`classscribe.timeline.samples_to_milliseconds` 返回 `Fraction`，因此任意 sample 都能无损
往返；输入不落在 sample 边界时明确报错。整数显示毫秒只在最终格式化时进行一次
round-half-up，绝不写回 canonical timeline。

90 分钟的终点固定为：

```text
90 × 60 × 16,000 = 86,400,000 samples = 5,400,000 ms
```

这仍远低于 int64 上限。单元与属性测试覆盖 0～90 分钟的任意 sample 精确往返、长
序列无累计漂移、int64 边界、单调区间和局部／绝对 offset。

## 音频主文件

阶段 4 已将每个输入归一化为单声道、16 kHz、PCM S16LE `audio_master.wav`。只有这个
主文件定义时间轴；容器时间戳、原始视频帧率和各模型内部步长都必须显式映射到它。
结构窗口、VAD/LID span 与正文 chunk 的实现和边界规则见 [音频流水线](audio-pipeline.md)。

## 导出规则

JSON 保留整数 samples，并可附带派生显示毫秒。SRT/VTT/Markdown 使用最终格式化的
毫秒时间，且必须通过单调、范围和非重叠策略校验。听不清标记中的范围来自同一格式化
路径，不能由模型文本自行生成。
