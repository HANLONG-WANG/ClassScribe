# Worker RPC v1

ClassScribe worker 使用 Unix-domain socket 上的 MessagePack RPC。每个 MessagePack payload
前有一个 network-byte-order 4 字节无符号长度；空帧和超过 16 MiB 的帧立即拒绝。socket
父目录为 0700、socket 为 0600，Linux `SO_PEERCRED` 要求连接端与 worker 同 UID。

## 请求与响应

每个请求必须包含 `protocol_version=1`、非空 `request_id`、`job_id`、1～86,400,000 的
`deadline_ms`、优先级 `0/10/20/30/40`、标准 method 和 map params。标准 method 完整集合为：

```text
health capabilities load unload transcribe_batch transcribe_pcm
stream_open stream_push stream_flush stream_close
lid_pcm
align punctuate vad lid diarize cancel
```

批处理方法只传绝对、非 symlink、普通、只读且位于 worker 显式 `--data-root` 下的路径，
以及有序绝对 `start_sample/end_sample`；采样率只允许 16000。实时输入是同一 socket 内
`pcm_s16le` bytes，每帧必须是完整 int16，非空且不超过 32,000 bytes（1 秒单声道）。
`lid_pcm` 是阶段 11 的实时 LID 扩展：仅接受 16 kHz mono S16LE、非负绝对起点和至多 30 秒，
返回语言概率与绝对区间。file-only LID adapter 必须在 worker 私有临时目录转换并总是清理。
`transcribe_pcm` 是松键最高精度确认桥：只接受 16 kHz mono S16LE、匹配的绝对/core 区间、
具体 `zh/ja/en`、至多 30 秒和有界 rolling context。RPC server 在 Bubblewrap 私有 `/tmp` 中
生成 0400 canonical WAV，调用同一 `body-asr-v1` batch adapter，恢复绝对 sample 后立即删除；
因此 batch-only FireRed/Granite/MOSS 最终模型无需进入 dictationd，也不会持久化麦克风音频。

成功或失败响应都返回协议版本、关联 ID、固定 `model_id` 和 40 字符 model revision。
ASR 响应还固定返回 raw/normalized text、语言、绝对 sample segments、metrics 和 warnings。
segment 的 confidence/logprob 保持模型原值，不跨模型比较。任何嵌套层出现尚未校准的
`quality_probability` 都会被协议拒绝。

阶段 6 扩展仍使用同一 v1 envelope。结构 segment 可带 `speaker_local`、`acoustic_events`
和 `overlap`；MOSS result 必须将粗文本角色设为
`coarse_timeline_consensus_candidate_boundary_reference` 且 `adopted_as_final=false`。
pyannote result 的 `exclusive_segments` 和 `embeddings` 受协议强校验：绝对有序 sample、
非空局部标签、有限向量、0～1 signal quality 和正 support count。领域 parser 还会确认
所有区间未逃出请求窗口。

阶段 7 的 `body-asr-v1` 批请求另含 `core_start_sample/core_end_sample`、显式 `language`、
`manual_language=true`、candidate role、实验门、读音 hints、完整 decode 和
`batch_items=1`。生产 ASR adapter 复验 temperature/do_sample、seed、token/字符上限、单批和
异长混批禁令，并将请求区间物理裁成临时 canonical WAV。生成式无原生时间时 segment 明确标记
`request_span_not_model_native`，不能伪称词时间。

阶段 9 增加两个强合同方法。`punctuate` 必须带 `strict-punctuation-v1`、非空文字、
`manual_language=true` 和 `zh/en`；FireRed worker 回显原文字并另返标点 proposal，core 会拒绝
任何非标点字符变化。`align` 的 `final-align-v1` 必须带最终文字、手动 `zh/ja/en`、小于 30 秒
且不超过 registry 安全窗的 canonical 区间，以及循环、漏句、字符率、语言、覆盖率和有声时长
完整质量门。Qwen worker 只对该物理裁片调用 ForcedAligner，输出绝对 sample token；raw 和
normalized text 都必须与请求最终文字相同。

## 生命周期与故障

`load` 接收本地 model path、registry model ID 和固定 revision；不会解析 `main`、访问 Hub
或隐式下载。`unload` 清空 stream/model 状态；未 load 的推理返回 `model_not_loaded`。
worker 不支持的标准 method 返回 `method_not_supported`，而不是崩溃或假造空结果。

服务端为每个 request 建立独立 task。deadline 到期会设置取消事件、取消 task 并返回
`deadline_exceeded`；`cancel` 可从另一连接按 target request ID 取消正在运行的推理。adapter
异常被转换为结构化错误，`WorkerProcess` 负责启动、健康等待、stderr 隔离、终止升级和
socket 清理，因此单 worker 崩溃不会结束 core。

所有 worker 入口统一支持：

```bash
worker.py --health-check
worker.py --cuda-check
worker.py --socket /absolute/runtime/worker.sock \
  --data-root /absolute/read-only/job-root
```

启动 RPC 时强制 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`、
`HF_DATASETS_OFFLINE=1`。当前 11 个隔离 worker family 均有自己的 `pyproject.toml`、
`uv.lock`、`worker.py`、`adapter.py`、`healthcheck.py` 和 README，并通过相同 v1 contract。

## IBus 常驻 worker 交接

core 的 `DictationWorkerSupervisor` 是常驻进程 owner。它从固定 registry revision、完整性已验证的
active model 和现成 frozen worker 环境构造 Bubblewrap 命令，完成 `health → load` 后才原子写
`${XDG_RUNTIME_DIR}/classscribe/resident-workers.json`。交接结构由
`protocol/schema/v1/resident-workers.schema.json` 固定，包含协议/schema 版本、ready、默认模型、
每个 model ID/revision/socket、12 个语言×profile 路由、VAD/LID socket，以及按语言区分的
accuracy model/revision/socket/预计加载时间、错误和生成时间。accuracy 路由有 socket 表示已
常驻；只有预计加载时间表示 core 会在松键后卸载流式 GPU worker 并热切换固定最终模型。

dictationd 不持有进程句柄，也不相信任意路径：清单须同 UID、非 symlink、`0600`、≤64 KiB；
worker socket 须是清单相邻 `workers/` 下的同 UID 真实 socket 且 `0600`。它只在无活动会话时
重新读取路由，活动会话固定使用已经选择的 client。`auto_mixed` 映射到 registry 的 `auto` profile；
具体 model ID 只能命中清单中的 route。core shutdown 会逐个 `unload`/stop 并发布 `ready=false`；
启动、load 或停止失败被隔离成结构化错误，不会留下一个虚假的 ready 清单。

Nemotron streaming 会话使用 NeMo `CacheAwareStreamingAudioBuffer` 和
`conformer_stream_step`。worker 将 80/160/320/560/1120 ms 分别映射到模型声明的右 attention
context 0/1/3/6/13，并拒绝模型未声明的组合；每个 `stream_id` 隔离保存 audio buffer、attention
channel cache、convolution time cache、cache length、previous hypothesis 与 predictor output。
每次只向 buffer 追加新 PCM，首步 `stream_id=-1` 且 `drop_extra_pre_encoded=0`，后续使用同一
stream 与模型配置。`fast` flush 只处理补齐后的尾块并保持实际绝对 sample 终点；
`balanced`/`accuracy` 可对当前完整句段重解码。close/unload 会清除所有 buffer/cache；worker
不把“每块累计重跑整个音频”冒充 cache-aware。

## 真实模型验收

`moss_en` 环境另含一个 disabled reference adapter：固定 revision 的 39M 参数 Whisper Tiny
CTranslate2 checkpoint，以 CPU int8、`local_files_only=True` 加载。它只用于证明 RPC 的
真实神经模型 load→音频推理→unload 生命周期，不属于生产 bootstrap 排名，也不能替代
计划中的语言专用模型。
