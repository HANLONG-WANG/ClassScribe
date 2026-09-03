# 音频导入、QC、VAD、LID 与切片

## Canonical master 与原件

`FFmpegMediaPipeline` 接受 WAV、FLAC、MP3、M4A/AAC、OGG、Opus、MP4、MOV、MKV 和
WebM。入口拒绝不存在、symlink、非普通文件、8 GiB 以上、90 分钟以上、超过 8 声道或
无音轨的输入。`ffprobe` 只读取第一个音频流；解析失败返回稳定错误，不把任意 stderr 或
用户文件内容写入日志。

原件以 UUID 物理名复制到 job `source/`，流式计算 SHA-256、fsync、原子 rename，最终
权限 0400；用户文件名只保留为显示元数据。原件从不被 master 覆盖。标准化命令固定：

```text
ffmpeg -nostdin -hide_banner -loglevel error -threads 2 -i <source>
  -map 0:a:0 -vn -ac 1 -ar 16000 -c:a pcm_s16le -y <staging.wav>
```

多声道等权模式增加显式 `pan=mono` 等增益求和；最佳通道模式使用 QC 选中的单一通道。
没有 `loudnorm`、denoise、AEC 或其他增强滤镜。输出在同目录 staging，必须经 Python WAV
头复核为 16 kHz、mono、16-bit、uncompressed 后，才以 `os.replace` 发布为只读
`audio_master.wav`。master 实际 frame count 是后续唯一时长与 sample timeline；输入
容器 duration 只用于中断差异警告。

## 音频质量报告

`PCMQualityAnalyzer` 以 100 ms window 和有限大小读取块处理，不把 90 分钟 PCM 全部载入
内存。多声道逐路记录 peak、RMS、近似 LUFS、clipping ratio、DC offset、振幅静音/语音
占比、粗略 SNR、背景音乐概率；选路评分奖励非静音/RMS，并惩罚削波和 DC。VAD 完成后
用 speech region 的精确 sample 占比替换初始振幅估计。

报告同时保存输入与 master SHA-256、输入格式/codec/sample rate/channels、canonical
duration_samples，以及容器时长与 master 相差 0.5 秒以上的
`abnormal_interruption_or_duration_mismatch`。削波、DC、极低语音和音乐都只形成 warning/
路由特征，不擅自拒绝录音。未来增强音轨必须是独立派生版本，并通过阶段 12 A/B gold
证据后才可能成为默认。

## VAD

`FireRedVADAdapter` 是默认名字和接口；`SileroVADAdapter`、`WebRTCVADAdapter` 是显式
fallback/回归对照，不会被悄悄当成 FireRed 结果。三者消费带 raw speech probability 的
绝对 `VADFrame`，以相同状态机支持 batch (`streaming=false`) 与 streaming
(`streaming=true`)；输出 `SpeechRegionResult` 保存绝对半开 span、最大 raw score、
acoustic class 和实际 source。

状态机验证 frame 单调且在 master 范围内，合并短静音、丢弃过短 speech、加受控 padding，
并禁止 padding 大到让已经流式发出的区间随后重叠。frame 时间绝不因分块归零。

## 两种不可混用的切片

`StructureWindow` 与 `TranscriptChunk` 是不同 dataclass，没有通用含糊 `Segment`：

- 结构窗口默认 12 分钟、相邻至少重叠 4 秒、末窗至少 4 分钟；显存实测安全容量只能从
  20/30/60/90 分钟候选中上调（低于 4 分钟拒绝）。90 分钟最后一个 window 精确结束在
  86,400,000 samples。
- 正文句段的 `core_span` 目标 18 秒、自然范围 8～30 秒。候选边界严格按 speaker change、
  300～800 ms/更长自然停顿、句末韵律/粗标点、language switch 优先；同级再选最接近
  target 且置信更高者。
- 无自然边界时 `core_span` 在 30 秒 hard max 切分，但左右 `audio_span` 各保留默认 1 秒
  （可配范围 0.8～1.5 秒），所以不存在无重叠硬切。core spans 无缺口且唯一，context
  spans 可控重叠并始终在 master 内。

`plan_boundary_dedup()` 只按 normalized token suffix/prefix 与绝对 token time alignment
识别重复，再保留质量较高的一侧；不删除固定字符数。结构窗口还提供 overlap speaker
embedding 的一对一 cosine matching 原语，使后一窗 local label 可延续既有 global label；
阶段 6 会写入实际 speaker spans。

## 手动语言与自动/混合语言

手动 `zh`／`ja`／`en` 直接产生覆盖整个 master 的单一 language span，decision 明记
`global_lid_bypassed=true`，传给支持提示的模型分别是 `zh`、`Japanese`、`English`。
该路径不构造也不调用 LID backend，句内英文词或外来语不会改变手动模式。

自动/混合才调用 `FireRedLIDAdapter`：固定 5 秒 window、2.5 秒 step，原始 zh/ja/en
概率逐窗保留。初始语言和任何切换都要求连续两个 window 对同一候选达到 p>=0.80；单窗
高分或后续低分只清除 pending，不改当前语言。切换边界回看最近 speech region 间的静音
点。中英或日英切换的 decision 分别保存 unified multilingual routing hint，避免把单个术语
拆给独立英语模型。最终 language spans 连续覆盖 master，DB 同时保存 raw confidence 和
完整 decision/observations。

## 持久化与恢复

`AudioArtifactRepository` 在短事务内幂等替换 `speech_regions` 和 `language_spans`，写前
验证 start/end 单调且不超过 recording.duration_samples；语言 spans 还必须从 0 连续覆盖
到 master 终点。失败发生在 delete 前，因此旧结果保留。

阶段顺序由持久 checkpoint 决定：canonical master → quality report → speech regions →
language spans → structure/text slices。`next_audio_stage()` 重启后只读取 completed
checkpoint，从第一个未完成项继续；不通过“表为空”猜测，因为合法静音录音可以没有
speech region。
