# Fedora IBus 语音输入

## 组件与边界

```text
 F9 / IBus property ── classscribe-ibus-engine ── JSON-line UDS ─┐
                                                                 ├─ classscribe-dictationd
Portal GlobalShortcuts ─ classscribe-hotkey-portal ──────────────┘
                                                                     │
 PipeWire → 16 kHz mono S16LE → FireRed streaming VAD → ASR worker ─┤
 FireRedLID worker ──────────────────────────────────────────────────┤
 core worker supervisor + GPULeaseManager priority 0 ───────────────┘
```

- `classscribe-ibus-engine` 是薄 PyGObject/IBus 客户端，只处理 F9、Esc、数字候选键、
  property、preedit、lookup table、auxiliary status 和最终 commit；不导入模型、Torch 或 CUDA。
- `classscribe-dictationd` 独占实时会话、麦克风、VAD、流式识别、分块、最终确认和 GPU 租约。
- `classscribe-core` 是常驻 worker 的唯一 owner：只从已安装、哈希验证且环境已 provision 的固定
  revision 启动 Bubblewrap worker，并原子发布 `resident-workers.json`。dictationd 在每个 idle→arming
  边界重新读取该清单，不自行安装依赖、加载模型或下载权重。
- `classscribe-hotkey-portal` 只使用 XDG Desktop Portal `GlobalShortcuts`。Portal 不可用时写出
  诊断并保留 `ClassScribe Voice` 输入源，不回退到 Wayland/X11 全局抓键。
- 模型 worker 通过既有 MessagePack UDS 协议运行。dictationd 与 engine/portal 的控制面使用
  有界 JSON-line UDS；目录 `0700`、socket `0600`，客户端拒绝 symlink、非 socket 和异 UID owner。

可执行入口和默认 socket：

```bash
classscribe-dictationd \
  --worker-manifest "$XDG_RUNTIME_DIR/classscribe/resident-workers.json" \
  --scheduler-socket "$XDG_RUNTIME_DIR/classscribe/gpu-lease.sock"
ibus-engine-classscribe --socket "$XDG_RUNTIME_DIR/classscribe/dictation.sock"
classscribe-hotkey --socket "$XDG_RUNTIME_DIR/classscribe/dictation.sock"
```

`--energy-vad` 和 `--without-gpu-lease` 只用于明确的诊断/回退运行；生产默认要求 FireRed streaming
VAD 与 core 的唯一租约服务。RPM 安装 component XML、三个 systemd user unit、GStreamer
PipeWire/base/good 插件和 portal 依赖。

`--model-worker MODEL_ID=SOCKET`、显式 VAD/LID/accuracy socket 只保留给合同测试和人工诊断；
正常 systemd 路径完全由 core 清单路由。清单必须是当前 UID 拥有、非 symlink、普通文件、`0600`、
不超过 64 KiB；其中每个 socket 必须直接位于同一 runtime 的 `workers/`、为当前 UID 的真实 socket
且无 group/other 权限。另一个同用户、0600、至多 64 KiB 的 `resident-model-catalog.json` 仅列出
已安装的流式模型 ID，供冷启动时的选单展示；该目录不代表已有可用 worker，不授权推理。

## 按需加载与可选预热

core 先完成数据库、调度接口和任务恢复初始化，然后开放 WebUI；启动时不会等待模型权重加载。
默认仅在后台读取安装元数据，既不启动模型进程，也不对全部权重做 SHA-256 扫描。

```yaml
ibus:
  enabled: true
  prewarm_on_startup: false
  idle_unload_seconds: 60
```

- `enabled: false` 阻止启动预热、手动预热和听写准备；安装或 profile 变更也不会触发模型加载。
- 默认按实际听写请求的语言、档位和手动模型选择，仅加载一条流式路线。没有合格本机排名时使用
  bootstrap 顺序；最高精度的非流式确认模型只在真正需要确认时切入。
- 先确认 ASR、FireRedVAD、运行环境齐全；自动/混合语言还要求 FireRedLID。只有这些检查通过后，
  才在后台线程中对本次所需文件逐个完整校验，再加载 CPU 辅助模型和 ASR。任一步失败或取消会清理
  已启动的进程，`ready=false`，不会留下无法组成可用链路的 ASR。
- 每次准备中的同一模型只完整校验一次。预热模型在版本及文件元数据未变化、进程存活且配置相同时
  复用；复用前检查文件集合、设备/inode、大小、mtime/ctime。变化、升级、删除、回滚或 profile 更新
  会使复用失效，下一次加载重新完整校验。手动文件校验始终读取全部权重。
- IBus 页面提供“预热默认模型”和“释放模型”，显示检查依赖、校验文件、加载、就绪及失败原因。
  `prewarm_on_startup: true` 开启可选后台预热；课堂任务运行时不进行后台预热。
- 听写结束后相同配置的 worker 默认保留 60 秒，便于下一次复用，空闲超时自动释放。
  `idle_unload_seconds: 0` 表示听写结束立即释放；关闭 core 也会清理后台任务与 worker。

“常驻”不表示和课堂重型 worker 并存：课堂推理启动前会取消预热并卸载 resident；听写 begin 等待
课堂 job 到安全 checkpoint 后再准备。听写 end 恢复课堂调度，课堂需要 GPU 时立即释放缓存的 worker。
活动听写期间安装/profile 刷新仅登记失效，不替换当前 socket；结束后释放，下一次按新配置准备。
所有这些路径只解析已有环境，不安装 Python 依赖、不下载模型。

## 状态机与输入语义

```text
IDLE → ARMING → LISTENING ↔ INTERIM_UPDATE → FINALIZING
                                               ↓
                              CANDIDATE_SELECT（按需）
                                               ↓
                                 COMMITTING → IDLE
任意活动状态 ── Esc/故障 ── ERROR/取消 → IDLE（不 commit）
```

默认 F9 为按住说话：按下 `begin`，松开 `release`。切换模式中 F9 每次发送 `toggle`；Portal
在 Activated 时读取实时配置，只有 hold 模式才在 Deactivated 时释放。Esc 取消且不提交；候选窗
中 1～9 选择对应候选。其他普通键始终返回未处理，由当前应用接收。dictationd 不可达或返回错误时，
engine 清除不稳定 preedit、隐藏候选、显示短错误，并继续透传普通键。

属性菜单含：中文/日本語/English/Auto、快速/平衡/最高精度、自动最佳/具体预加载模型、按住/单击、
临时结果显示/隐藏、自动/仅句末/关闭标点。具体模型项来自 daemon 的 `available_models`，配置时再次
验证，不能选择只在注册表出现但尚未预加载的模型。

interim 只调用 `update_preedit_text`；已稳定字符保持不动，不稳定尾部用 underline attribute。
客户端不支持属性时仍能显示普通 preedit。最终结果才调用 `commit_text`；多个候选使用
`update_lookup_table`，通常由第一候选自动完成。

## 采音、缓存与自动分块

GStreamer pipeline 固定为 `pipewiresrc`，经 `audioconvert`、`audioresample` 转成 16 kHz、mono、
S16LE。appsink 的任意大小 buffer 会重组为精确 20 ms（320 sample/640 byte）帧；队列有界，
overflow、bus error/EOS 和麦克风断开都进入不提交的安全失败。内存 PCM 随会话/分块清除，默认不写
磁盘、不建立听写历史。

三个分块层级：

1. 模型 chunk 为 80/160/320/560/1120 ms；Nemotron worker 使用模型的
   `CacheAwareStreamingAudioBuffer` 与 `conformer_stream_step`，选择模型声明的右 attention
   context 0/1/3/6/13，只增量预处理新 PCM，并跨 chunk 保存 audio buffer、attention channel
   cache、convolution time cache、cache length、RNNT hypothesis 和 predictor output，不累计重算历史。
2. 有声段至少 3 秒后，650 ms（可配置 300～2000 ms）自然停顿形成 semantic endpoint；语义段不
   超过 20 秒。
3. 连续无停顿达到 25 秒（可配置 20～30 秒）形成 hard chunk，保留 2 秒（1.5～2.5 秒）PCM 重叠。

每块携带单调 `chunk_seq`、绝对 16 kHz sample、context/core 范围和原因。hard chunk 关闭旧 worker
stream、打开新 stream，再按连续 20 ms 帧重放精确 overlap；semantic endpoint 清空音频重叠。
输出合并使用绝对 sample 交叠和 token 等价关系，不按固定字符数裁剪。超过一分钟仍只保留最近
1～5 个已确认句段作为 rolling context；默认 45 秒后逐段提交稳定前缀，最近尾部保留重识别。
五分钟 20 ms 固定回归覆盖多个 hard/semantic chunk，并逐 token 证明既不重复也不漏词。

## 最终确认、语言与标点

- `fast`：排空同一缓存流的不足一块尾部（补零只提供右上下文，不扩展绝对 sample），将缓存结果
  标为稳定；不执行完整历史重算。
- `balanced`：同一常驻模型对当前完整稳定句段做一次最终重解码。
- `accuracy`：把当前句段 PCM 经有界 `transcribe_pcm` 送到语言专用 batch/final worker；主流不
  flush。若流式与最终模型可在安全余量内双驻留则直接调用；否则 core 在松键后停止流式 GPU
  worker、加载固定 revision 的最终模型并原子刷新路由。ARMING 只在确需切换时提前暴露本机
  benchmark 或显存规模推导的 `expected_accuracy_load_ms`，不伪装为快速确认。

手动语言始终强制。Auto 在稳定句段边界融合主 ASR 的语言/概率与 FireRedLID；缺失 detector 不算
零票，两个 detector 同时存在时取同语言均值。候选连续两个窗口均达到 0.80 才切换，低置信维持
上一语言。中文/英文紧密 code-switch 保持统一会话；当前为日语且结果只表现为英文术语、缩写或
片假名外来词时不切换到英语。语言状态跨自动块保持，但新模型 stream 使用已稳定语言提示。

标点模式在 timed token 与候选上采用同一个变换：automatic 保留模型标点；sentence-end 只保留
已有句末标点；off 删除标点。任何模式都不改变非标点字符序列。

## Portal、诊断与兼容

Portal companion 先订阅带随机 handle token 的 Request response，再调用 `CreateSession` 和
`BindShortcuts`，并处理 `Activated/Deactivated`。每次结果原子写到
`$XDG_RUNTIME_DIR/classscribe/portal-status.json`（`0600`）；core 的已认证
`GET /api/v1/ibus/status` 汇合 daemon 状态、配置、预加载模型与 portal 诊断，设置页每秒刷新。

`config/ibus-compatibility.v1.json` 固定 GTK/GNOME Text Editor、Qt/KWrite/Kate、Firefox、Chromium、
VS Code、GNOME Console、Konsole、LibreOffice Writer 八类客户及其 attribute/fallback 期望。
自动合同测试对每一项验证“带属性 preedit 或普通 preedit → lookup → 单次 commit → 错误后透传”。
真实 Wayland/X11、GNOME/KDE 和已安装应用的版本/结果由阶段 12 的桌面验收 runner 记录，不能用
未运行的应用伪造通过状态。

## 故障与隐私

dictationd、worker socket、VAD/ASR/LID、GPU OOM、音频队列、麦克风、Portal 任一故障均遵守同一
原则：停止音频、abort/close stream、释放 priority-0 lease、清空 preedit/候选且不 commit。
daemon 控制 socket 和 GPU lease socket 都有超时、响应大小上限、同 UID 检查；portal 状态读取也
拒绝 symlink、异 UID、非普通文件及超过 16 KiB 的内容。日志/诊断只含状态、延迟、模型 ID 和错误
码，不含麦克风 PCM 或听写正文。
