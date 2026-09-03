# ClassScribe 架构

## 系统边界

ClassScribe 只有两个产品形态：课堂文件转录工作台和 IBus 语音输入。二者共享配置、
协议、canonical timeline、模型注册表、GPU 调度与本地安全约束，但绝不共享一套含糊
的推理策略。

```text
浏览器 ── HTTP/SSE @ 127.0.0.1 ── classscribe-core
                                      │
                                      ├─ SQLite / 作业与审计（阶段 2）
                                      └─ MessagePack RPC / Unix socket
                                           ├─ 音频／结构 worker
                                           ├─ 独立 ASR worker
                                           └─ 对齐／说话人 worker

薄 classscribe-ibus-engine ── Unix socket ── classscribe-dictationd
                                                │
XDG Portal GlobalShortcuts companion ──────────┘
                                                └─ 共享 GPU 调度器
```

阶段 1 实现进程启动边界；阶段 2 加入 SQLite/Alembic、持久状态机、本地安全、审计、
诊断和恢复；阶段 4 完成 canonical 音频、QC、VAD/LID 和切片；阶段 5 已完成 versioned
registry、MessagePack UDS RPC、worker supervision 和单 GPU 调度；阶段 6 已接入 MOSS
结构转录、pyannote 回退、job-local speaker stitching 和逐窗结构持久化；阶段 7 已完成
FireRed/ARK/Qwen/Granite/MOSS Preview/Fun-ASR 的离线三语正文适配、统一证据和候选持久化。
阶段 8 已完成四域质量门、条件复核、受约束 token DP、confusion network 和逐 token
provenance；阶段 9 已完成课程术语/滚动上下文、三语严格标点、最终时间优先级和六格式
分层导出；阶段 10 已用持久 checkpoint、版本化 API/SSE 和完整 Web 工作台贯通课堂形态。
阶段 11 已贯通薄 IBus engine、dictationd、PipeWire/FireRedVAD、Nemotron cache-aware streaming、
FireRedLID、最终确认、priority-0 GPU lease 和 Portal GlobalShortcuts；实时 PCM 只在 dictationd 与
隔离 worker 的有界内存中存在，不进入课堂数据库或 canonical 文件时间轴。
完整边界见 `docs/body-asr.md`、`docs/quality-and-consensus.md`、
`docs/postprocessing-and-exports.md`、`docs/classroom-api-webui.md` 与 `docs/ibus.md`；
dependency-light health 不伪装模型推理能力。

## 进程职责与禁止边界

| 进程 | 唯一职责 | 禁止事项 |
|---|---|---|
| `classscribe-core` | 本地 Web API、业务、作业编排、数据库、质量决策和导出 | 不导入具体模型库，不持有实时麦克风状态，不联网推理 |
| 模型 worker | 在独立环境加载一个模型族，经版本化协议处理请求 | 不访问 core 数据库，不决定全局选模／GPU 次序，不导入 core 包 |
| `classscribe-dictationd` | PipeWire/GStreamer 采音、流式状态、分块和最终确认 | 不实现 IBus UI，不复制模型注册表或调度逻辑 |
| `classscribe-ibus-engine` | IBus preedit、commit、候选窗和属性菜单 | 不导入 PyTorch/CUDA/模型库，不直接采音 |
| hotkey portal companion | 通过 XDG Desktop Portal `GlobalShortcuts` 注册 Wayland 快捷键 | 不全局抓键，不加载音频或模型运行时 |
| Web 前端 | 显示上传、模型选择、进度、波形、候选、可选校对和导出 | 不实现第二套质量、时间轴或模型选择逻辑 |

`scripts/check_architecture.py` 在 CI 中以 AST 和清单检查这些静态边界。运行期权限、
socket 身份验证和网络隔离在阶段 2、3 加固。

## 技术栈与依赖所有权

- 核心：Python 3.12、FastAPI/Uvicorn、SQLAlchemy 2/Alembic/SQLite WAL、msgspec，
  由根 `uv.lock` 固定。
- worker：每个 `workers/<name>` 是独立 Python 3.12 工程并有自己的 `uv.lock`；
  Transformers、NeMo、vLLM 等只能出现在对应 worker 环境。
- 前端：React、TypeScript strict、Vite、TanStack Query、Zustand、WaveSurfer.js；测试用
  Vitest 和 Playwright，依赖由根 `pnpm-lock.yaml` 固定。
- 桌面：PyGObject/IBus、GStreamer/PipeWire 和 Desktop Portal 属于 Fedora/RPM 系统
  集成依赖，不进入核心模型环境。
- 质量门：Ruff、mypy strict、pytest/Hypothesis/coverage、ESLint、Prettier、Vitest、
  Playwright 和 pre-commit。

当前模型 family 为 `moss_td`、`firered`、`granite`、`qwen`、`ark`、`moss_en`、
`nemotron`、`pyannote`、`funasr_experimental`、`voxtral`、`vibevoice`。能力、revision、
资源和缺陷来自 registry；core 只启动对应 entry 的 worker，不能导入这些目录。

结构层位于 `backend/classscribe/structure`，只依赖共享 RPC、配置、canonical timeline 和
数据库模型，不导入任何模型运行时。MOSS 粗文本存入 `structure_segments`，和可采用的
`transcript_segments` 物理分离；pyannote regular/exclusive/embedding 只作为说话人证据。
完整数据流和保守匹配门见 `docs/structure-and-speakers.md`。

正文层位于 `backend/classscribe/asr`，只查询 registry 并经 RPC 调用隔离 worker。它不会导入
模型库，也不会把 MOSS 结构文本提升为最终文本。worker 内共享的 batch contract 强制手动语言、
单 WAV 片段、确定性时长上限和提示词无插入权限；候选在阶段 8 质量共识前均未采用。

质量层位于 `backend/classscribe/quality`，只使用候选证据、声学/QC 上下文和明确术语读音；
循环/静音幻觉/脚本/时间硬错误在共识前拒绝。共识层位于
`backend/classscribe/consensus`，只做短片段 token 动态规划和时间列投票。架构门禁专门禁止
在该层使用长字符串 `SequenceMatcher`。

后处理分为 `terminology`、`punctuation`、`alignment` 和 `exports` 四个 core 包。术语只能
通过显式证据把 alias 确定性替换到 smart 层；标点持久化前必须证明非标点字符序列不变；
对齐按 native→MOSS→gated Qwen→VAD 选择并保留候选 provenance；导出只消费分层文字和最终
绝对 token 时间。FireRedPunc/Qwen ForcedAligner 的模型调用留在既有隔离 worker。

课堂编排位于 `backend/classscribe/classroom`。全局和逐片段 checkpoint 都委托给阶段 4～9
模块的 handler；缺失 handler 必须失败，禁止用空操作推进状态。结构阶段后按当前活动 segment
动态建立 checkpoint，拆分/合并或局部重试不会覆盖历史证据。SSE broker 只发布状态和运行指标，
不成为权威状态源；SQLite 始终是恢复依据。

## 数据根与生命周期

| XDG 根 | 内容 | 生命周期 |
|---|---|---|
| `~/.config/classscribe` | `config.yaml`、模型注册表、profiles、API token | 用户配置；不得自动删除 |
| `~/.local/share/classscribe` | SQLite、jobs、glossaries、benchmarks | 权威数据；决策记录和最终导出不得静默删除 |
| `~/.cache/classscribe` | 模型、重采样、波形峰值、临时文件 | 可按明确策略清理；不得作为唯一权威来源 |
| `~/.local/state/classscribe` | 本地日志和 crash reports | 可轮换；默认不记录转录正文 |
| `$XDG_RUNTIME_DIR/classscribe` | Unix sockets 和短生命周期锁 | 会话级；退出后可重建 |

应用自有目录必须为当前用户所有、非符号链接且权限 `0700`。原始上传在 job 的
`source/` 中只读保存并计算 SHA-256；阶段 4 生成 16 kHz 单声道 PCM S16LE
`audio_master.wav`。`derived/`、`candidates/` 可按配置清理，数据库决策和 `exports/`
不可静默删除。

## 内外通信

- 浏览器只访问 loopback HTTP 与 SSE；默认 token 必须开启，写请求还须提交 CSRF header。
- core 与 worker 只通过 Unix-domain socket 和共享的版本化 MessagePack 协议通信。
- IBus engine 只通过本地 socket 连接 dictationd。
- dictationd 的控制面使用带 revision 的有界 JSON-line UDS；实时模型面复用 worker MessagePack
  RPC。Portal companion 只向控制 socket 发 begin/release/toggle，不接触 PCM。

### IBus resident worker ownership

`classscribe-core` 的 `DictationWorkerSupervisor` 是 IBus 常驻 worker 的唯一进程 owner。它只消费
完整性已验证的 active revision 和已经 frozen provision 的 worker 环境，在配置的 VRAM+margin
总预算内选择流式 ASR，并通过 Bubblewrap 启动 ASR/VAD/LID。core 原子发布 owner-only
`resident-workers.json`；dictationd 只在 idle 会话边界读取并验证同 UID、同 runtime `workers/`
socket 后选路。安装、profile 应用/回滚和 core shutdown 都经过 supervisor 的 stop/unload/refresh，
因此 systemd 启动不依赖外部手工预加载，也没有第二个模型生命周期实现。
- 推理路径没有外网客户端。用户主动模型安装／更新使用阶段 3 的隔离事务和离线 resolver。

## 阶段依赖门

```text
协议／canonical timeline → 数据库／状态机 → 音频／QC／VAD／LID
→ 模型注册表／worker 契约 → GPU 调度 → 结构层／speaker stitching
→ 三语正文适配器 → 质量／fallback／共识／术语／标点
→ forced alignment／最终时间轴 → 导出 → WebUI
→ IBus／dictationd／portal → 模型管理／RPM／完整基准
```

并行工作只能建立在已经冻结的上游接口上。CI 的结构检查确保基础目录和边界仍存在；
`docs/requirements-traceability.md` 记录每项产品要求的实际验证阶段。
