# 官方资料与冻结事实索引

计划的事实截止日为 **2026-09-03**；发布加固在 2026-09-04 再次访问以下官方仓库／模型卡，
但不会把 cutoff 之后的 floating `main` 自动纳入产品。运行与安装一律使用
`config/model-revisions.lock.json` 的完整 commit SHA。公开指标仅用于候选发现，不替代本机 gold。

| 能力 | 官方资料 | 本项目冻结结论 |
|---|---|---|
| FireRed ASR/VAD/LID/Punc | [FireRedASR2S](https://github.com/FireRedTeam/FireRedASR2S)、[FireRedVAD](https://huggingface.co/FireRedTeam/FireRedVAD)、[FireRedLID](https://huggingface.co/FireRedTeam/FireRedLID)、[FireRedPunc](https://huggingface.co/FireRedTeam/FireRedPunc)、[FireRedASR2-LLM](https://huggingface.co/FireRedTeam/FireRedASR2-LLM) | ASR 为中/英/中英混说；AED 有时间/置信；Punc 中英；各模型独立 revision 和许可记录 |
| Qwen ASR/align | [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)、[Qwen3 ForcedAligner 0.6B](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B) | 0.6B/1.7B、52 语言/方言；ForcedAligner 11 语言且最长约 5 分钟 |
| Fun-ASR experimental | [Fun-ASR-Nano-2512](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512-hf) | 固定模型卡 revision，仅专家试验；循环与日语标点验收前禁用 |
| 真实生命周期 reference | [faster-whisper-tiny](https://huggingface.co/Systran/faster-whisper-tiny) | 39M CTranslate2 CPU int8 reference，只验证真实 load→infer→unload，不参与排名 |
| MOSS 长音频结构 | [MOSS Transcribe-Diarize](https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize) | 0.9B、50+ 语言、最长 90 分钟声明、speaker/time/hotword；本机仍切 12 分钟窗 |
| Granite 4.1 | [Granite Speech 4.1 2B](https://huggingface.co/ibm-granite/granite-speech-4.1-2b) | 包含日语 ASR、标点/大小写提示和关键词偏置 |
| ARK | [ARK-ASR-3B](https://huggingface.co/Audio8/ARK-ASR-3B) | 3B、19 语言；公开 leaderboard 只作 bootstrap |
| MOSS English | [MOSS Transcribe preview 2B](https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-preview-2B) | 英语、约 2.4B、Apache-2.0、custom code 必须哈希隔离 |
| NVIDIA multilingual streaming | [Nemotron 3.5 ASR Streaming 0.6B](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b) | cache-aware、40 locale、原生标点/大小写；OpenMDW-1.1 |
| NVIDIA English streaming | [Nemotron Speech Streaming EN 0.6B](https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b) | 英语 cache-aware streaming；NVIDIA Open Model License |
| Granite TurboCTC | [Granite Speech 5.0 470M TurboCTC](https://huggingface.co/ibm-granite/granite-speech-5.0-470m-turboctc) | 英语 CTC 轻量候选；使用 Apache-2.0 版而非 `-nc` 版 |
| Voxtral realtime | [Voxtral Mini 4B Realtime 2602](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602) | 13 语言、BF16、Apache-2.0；12 GB 默认禁用 |
| VibeVoice streaming | [VibeVoice ASR Streaming 1.5B](https://huggingface.co/microsoft/VibeVoice-ASR-Streaming-1.5B) | 10 语言、streaming、MIT；稳定性验收前禁用 |
| pyannote diarization | [speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) | 16 kHz mono、exclusive diarization、可离线；CC-BY-4.0 且需接受访问条件 |
| IBus engine | [IBus 官方仓库](https://github.com/ibus/ibus) | 使用 update preedit、lookup 和 commit signal；薄 engine 不加载模型 |
| Wayland shortcuts | [XDG Desktop Portal GlobalShortcuts](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html) | session/bind/Activated/Deactivated；输入源作为兜底 |
| Node 工具链 | [Node.js Release schedule](https://github.com/nodejs/Release/blob/main/schedule.json) | 2026-09-03 Node 24 为 Active LTS；frontend engines 固定 `>=24 <25` |
| Fedora 桌面 | [Fedora Workstation 文档](https://docs.fedoraproject.org/en-US/workstation-docs/) | systemd user、PipeWire、Wayland/IBus 以干净 Fedora 实机再验收 |

每次升级流程必须：显式更新 registry revision → 更新本文件事实说明和 revision lock → 下载完整
manifest 与 remote-code hash → 独立 worker 健康检查 → 全回归 → 对受影响语言/场景重跑私有
benchmark → 用户选择应用新排名。失败时 registry 和 active revision 均可回滚。
