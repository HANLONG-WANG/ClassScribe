# 模型注册表

`config/model-registry.v1.yaml` 是内置 bootstrap 注册表；首次运行把它原子复制到
`${XDG_CONFIG_HOME}/classscribe/model-registry.yaml`，之后所有用户启用、禁用、升级、
回滚和本机排名都只修改用户副本。业务流水线只能按 profile、语言、任务和 mode 查询
`ModelRegistry.candidates()`，不得通过语言分支导入具体模型。

## 当前事实快照

注册表 `schema_version: 1`、事实截止 `2026-09-03`，收录原计划 16 个候选、FireRedVAD、
FireRedLID、FireRedPunc 三个必需伴随模型，以及一个永不进入自动排名的 Whisper Tiny
真实 worker 生命周期验证模型，共 20 项。每项都包含：

- 上游仓库和 40 字符 immutable commit revision；
- worker、语言、任务、batch/streaming/final-decode mode；
- 16 kHz 单声道 PCM 输入、后端、dtype、remote-code 信任状态；
- 本项目安全窗口、可区分的厂商上限和七项原生响应能力；
- 预估／实测 VRAM、安全余量、逐 worker 依赖锁路径和 SHA-256；
- 安装状态、工件 aggregate SHA-256、本机 benchmark 数据和排名来源；
- 已知缺陷、实验状态、是否启用，以及禁用原因。

revision 在 2026-09-03 通过各模型官方 Hugging Face Git remote 的 `HEAD` 固定；gated
pyannote revision 取自其公开官方 metadata API。能力事实来自相应官方模型卡：
[MOSS TD](https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize)、
[FireRedASR2](https://huggingface.co/FireRedTeam/FireRedASR2-AED)、
[Granite Speech 4.1](https://huggingface.co/ibm-granite/granite-speech-4.1-2b)、
[Qwen3 ASR/Aligner](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B)、
[ARK-ASR](https://huggingface.co/Audio8/ARK-ASR-3B)、
[Nemotron 3.5](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)、
[Granite TurboCTC](https://huggingface.co/ibm-granite/granite-speech-5.0-470m-turboctc)、
[Voxtral](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602)、
[VibeVoice](https://huggingface.co/microsoft/VibeVoice-ASR-Streaming-1.5B)、
[pyannote Community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) 和
[Fun-ASR](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512-hf)。公开 benchmark 数字
不进入本机结果，也不产生“最好”结论。

## Bootstrap profiles

profile 明确区分课堂结构、最终对齐、中／日／英／自动正文、标点，以及 IBus 四语言的
fast/balanced/accuracy。顺序与开发计划 §6.2 一致：中文首先 FireRed AED，日语首先
Granite 4.1，英语首先 MOSS Preview；稳定顺序必须在阶段 12 用本机 gold 改写。

默认禁用 FireRed LLM（8B+）、Voxtral BF16（官方要求超出目标 12 GB）、极新 VibeVoice、
需要循环检测的 Fun-ASR，以及 reference Whisper。禁用项仍完整可查询，但普通候选查询
不会返回；启用必须记录明确原因并再次通过安装、VRAM 和健康检查。

阶段 7 已按这些 profile 实现 batch adapter。独立的 `Qwen3-ASR-1.7B-JA` 上游 checkpoint
不存在，因此实验 flavor 复用已固定的官方 1.7B revision并强制 Japanese；这避免注册表出现
不可验证的 repository。Fun-ASR worker 固定官方原生支持提交，并在 adapter 和 registry 两层
维持 expert-only/default-off。各 worker lock 改动后，其所有共享引用均同步更新 SHA-256。

## 管理和失败语义

`RegistryStore` 提供 add、disable、enable、upgrade、rollback、set-ranking、
mark-installation 和 record-benchmark。每次写入都：

1. 完整 Pydantic/schema 校验；
2. 增加 `registry_revision` 并追加 before-state history；
3. 在 0700 配置目录写 0600 临时文件、fsync 后原子替换。

升级立即把安装状态重置为 `not_installed`；只有 revision、工件 SHA-256 和本机安装记录
完全一致才能标成 installed。加载时可以对全部 worker lock 做 SHA-256 复验。未知 profile、
未知模型、重复排名、缺少禁用原因、漂移 lock 或非完整 revision 都安全失败。

模型 raw confidence/logprob 只作为原始响应保存。注册表和 worker 都拒绝
`quality_probability`；该统一概率只有阶段 12 按模型×语言×场景在本机 gold 上校准后才能
产生。
