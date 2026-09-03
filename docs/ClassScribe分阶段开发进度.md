# ClassScribe 分阶段开发进度

> 记录规则：阶段只有在实施任务、交付物和退出条件全部有证据后才标记为完成。后续
> 阶段若改变既有证据，必须重新运行该阶段验证并在此追加记录。

## 总览

| 阶段 | 状态 | 完成日期 | 验证摘要 |
|---|---|---|---|
| 阶段 0：产品与质量合同 | 已完成 | 2026-09-03 | 7 项标准库合同测试通过；46/46 条需求进入追踪矩阵 |
| 阶段 1：架构／仓库／配置／时间轴 | 已完成 | 2026-09-03 | 54 项 Python 测试、80.76% 覆盖率、前端测试／构建和全部静态门通过 |
| 阶段 2：数据库／状态机／安全／恢复 | 已完成 | 2026-09-03 | 92 项测试、87.34% 覆盖率；迁移/恢复/并发/安全/脱敏/删除全部通过 |
| 阶段 3：Fedora／RPM／systemd／模型安装与离线 | 已完成 | 2026-09-03 | 124 项回归、85.57% 覆盖率；32 项阶段验收含双版本 RPM 生命周期与 clean-XDG 启动 |
| 阶段 4：音频／QC／VAD／LID／切片 | 已完成 | 2026-09-03 | 165 项回归、85.41% 覆盖率；41 项音频验收含 11 格式真实 FFmpeg 与 90 分钟属性 |
| 阶段 5：模型注册表／Worker RPC／GPU 调度 | 已完成 | 2026-09-03 | 202 项回归、83.21% 覆盖率；49 项阶段验收含 11 worker UDS 和真实神经 ASR 生命周期 |
| 阶段 6：结构转录／说话人 | 已完成 | 2026-09-04 | 230 项回归、83.66% 覆盖率；40 项结构验收含 90 分钟窗口、真实 overlap、保守 stitching 与生产 adapter 路径 |
| 阶段 7：中日英正文模型 | 已完成 | 2026-09-04 | 256 项回归、83.11% 覆盖率；31 项阶段验收含三语真实语音 WAV 与六类生产 adapter 路径 |
| 阶段 8：QA／fallback／时间对齐共识 | 已完成 | 2026-09-04 | 278 项回归、83.70% 覆盖率；23 项阶段验收含固定循环、条件复核、token DP 和逐 token 审计 |
| 阶段 9：词典／标点／对齐／导出核心 | 已完成 | 2026-09-04 | 301 项回归、82.77% 覆盖率；36 项专项覆盖全材料源、三语严格标点、gated aligner、六格式分层导出 |
| 阶段 10：课堂流水线／API／WebUI | 已完成 | 2026-09-04 | 完整 API 合同、3 项 UI 测试；真实 Chromium 实际覆盖导入→任务→校对→词典→模型→导出 |
| 阶段 11：IBus／流式长听写／Portal 快捷键 | 已完成 | 2026-09-04 | 流式/长听写、按语言双驻留或热切换 accuracy、Portal/IBus 错误降级均有专项证据 |
| 阶段 12：Gold benchmark／校准／全测试／性能 | 实现完成，实机验收阻塞 | — | 337 项自动回归通过、1 项真实模型按设计 skip；缺私人 gold、固定权重、可用 CUDA 与完整桌面矩阵 |
| 阶段 13：发布加固／RPM／文档／最终交付 | 工程实现完成，发布签核阻塞 | 2026-09-04 | 9 个发布门中 6 个通过；源码许可、Phase 12 实机验收、桌面矩阵 fail-closed |

## 阶段 0：冻结产品边界、质量承诺与工程原则

状态：**已完成**（2026-09-03）

### 完成交付物

- `docs/product-requirements.md`：冻结 46 条带稳定编号的课堂、IBus、模式、文本层、
  质量、隐私、工程原则和非承诺要求，并定义变更控制。
- `docs/adr/0001`～`0004`：记录产品形态分离、本地运行、证据保真和模型／GPU 策略。
- `docs/glossary.md`：统一产品模式、语言模式、模型选择、文本层、时间轴、质量门、
  fallback、gold benchmark 等术语。
- `docs/requirements-traceability.md`：46/46 条需求均映射到当前合同证据、自动测试或
  人工验收步骤以及计划完成阶段。
- `docs/acceptance-checklist.md`：建立每阶段通用门及课堂、IBus、质量、模式、隐私的
  产品级验收清单。
- `config/product-contract.v1.json` 与 `protocol/schema/v1/product-contract.schema.json`：
  冻结产品形态、四种语言、三种选模语义、三层文本、听不清标记和隐私策略。
- `README.md`、`docs/ui-copy-guidelines.md` 与 PR 模板：明确自动完成语义、准确率限制、
  禁止生成式补写，并把八项设计原则纳入评审。

### 退出条件证据

- 命令：`python -m unittest discover -s tests -p 'test_*.py' -v`
- 结果：7 项测试通过。测试验证机器合同／schema 一致性、稳定枚举、三种听不清标记、
  仅本地隐私策略、46 条需求全量追踪、必要交付物以及对外限制措辞。
- 命令：`git diff --check`
- 结果：通过，无空白错误。
- 阻塞项：无。

### 后续约束

阶段 0 只证明产品合同已经冻结，不提前宣称后续运行时能力已经实现。追踪矩阵中的
计划证据必须随各阶段落地为实际测试；任何核心边界变更都需要新 ADR 和合同版本。

## 阶段 1：建立总体架构、仓库骨架、配置系统与 canonical timeline

状态：**已完成**（2026-09-03）

### 完成交付物

- 建立计划规定的 backend、frontend、ibus、9 个独立 workers、protocol、benchmarks、
  packaging、docs、scripts 和分层 tests 完整 monorepo。
- 建立 Python 3.12 核心工程、根 `uv.lock`、共享协议独立工程、三个桌面进程工程和
  9 个 worker 独立 `pyproject.toml`／`uv.lock`；worker 环境均可 frozen sync。
- 建立 React/TypeScript strict/Vite 前端工程、`pnpm-lock.yaml`、ESLint、Prettier、
  Vitest、Playwright 依赖和生产构建。
- 实现 `classscribe.timeline`：16 kHz 非负 int64 sample、半开 `AudioSpan`、精确
  `Fraction` 毫秒换算、显示边界舍入和局部／绝对 offset。
- 实现 `classscribe.paths`：XDG 默认和覆盖、全目录布局、0700／owner／非 symlink
  检查、data root traversal／symlink escape 防护及 job 目录解析。
- 实现 config v1 Pydantic 模型、JSON schema、完整默认／示例 YAML、默认 < 用户 <
  环境变量覆盖，以及 v0→v1 迁移和未来版本拒绝。
- 实现 core、dictationd、薄 IBus engine、GlobalShortcuts portal 及全部 worker 的
  版本化健康入口；IBus engine 明确不加载模型。
- 建立 v1 协议 envelope／schema、架构、时间轴、模型注册表、worker、IBus、benchmark、
  troubleshooting 和阶段门文档。
- 建立 CI、pre-commit 和 `scripts/check_architecture.py`，阻止 core／桌面进程导入具体
  模型库或 worker 私有代码，并检查完整仓库结构。

### 退出条件证据

- Python：54 项 pytest 全部通过；pytest-cov 总覆盖率 80.76%，超过 80% 门槛。
- 时间轴：Hypothesis 覆盖 0～90 分钟任意 sample 精确 sample↔ms 往返；90 分钟固定
  为 86,400,000 samples／5,400,000 ms；长序列无浮点累计。
- 静态质量：Ruff check/format、mypy strict（核心 39 个文件和每个 worker 3 个入口）
  及 architecture boundaries 全部通过。
- 前端：ESLint、Prettier、TypeScript project build、Vitest（1 项）、Vite production
  build 全部通过。
- 依赖与启动：根工程、共享协议、3 个桌面进程、9 个 worker 共 14 个 uv lock 均通过
  `uv lock --check`；隔离工程可 `uv sync --frozen`；core 配置与健康检查成功。
- `git diff --check`：通过。
- 阻塞项：无。

### 后续约束

健康骨架只证明进程和协议边界，未宣称模型或 RPC 已实现。后续数据库、音频、模型、
WebUI 与 IBus 必须消费本阶段冻结的 config v1、protocol v1 和 canonical timeline，
不得新建浮点持久化时间轴、私有模型选择或绕过共享调度器。

## 阶段 2：实现核心服务、数据库、持久化状态机、安全、日志与故障恢复

状态：**已完成**（2026-09-03）

### 完成交付物

- 完成 SQLAlchemy 2 metadata 与 Alembic 初始迁移，覆盖 recordings、jobs、
  job_checkpoints、三类 span、segments、candidates、tokens、decisions、glossaries、模型安装、
  benchmark 和 dictation 共 16 张业务表；启用 WAL、外键、FULL synchronous 和索引／约束。
- 完成可重入 job/checkpoint 状态机：pause/resume/cancel/retry、参数 SHA-256 重试限额、
  逐片段独立事务、worker crash 局部回滚和重启后首个未完成片段扫描。
- 完成 `transcript_segments.version` ORM/条件更新双重乐观锁、自动／人工 decision event、
  三层文本权限、候选软删除／版本链和已采用候选删除保护。
- 完成 256-bit API token、0600/owner/non-symlink 验证、loopback 与 Authorization bearer
  ASGI 中间件、安全响应头，以及只允许 loopback host/token 的 config 不变量。
- 完成 UUID job/upload 路径、90 分钟／8 GiB／格式／声道／采样率／FFmpeg 资源限制，
  和 worker canonical path 白名单；系统路径、symlink、traversal 与非法 ID 均拒绝。
- 完成独立 0600 模型下载凭证环境文件；普通配置、日志和诊断不保存凭证。
- 完成结构化隐私日志、本地 metrics、系统/GPU/FFmpeg/PipeWire/IBus/portal/worker/model/
  GPU 租约与错误诊断模型，以及 0600 原子脱敏诊断 zip 和受 token 保护的诊断 API。
- 完成原子输出、候选时间异常整份失效、对齐失败保留粗时间、窗口去重冲突保留候选，
  以及 dictationd/mic/GPU/socket 四类 IBus 安全回退接口。
- 完成听写历史 opt-in 和临时麦克风清理；完成 derived-only、job、确认式 all-local-data
  三级删除，并保护 derived-only 下的数据库决策和最终导出。
- 新增 `docs/database.md`、`security.md`、`recovery.md`、`diagnostics.md`。

### 退出条件证据

- `pytest --cov`：92 项全部通过，总覆盖率 87.34%（门槛 80%）。
- Alembic：初始迁移 upgrade → metadata `check` 无差异 → downgrade 往返通过；完整表集合、
  规范必需列、外键、timeline CHECK 和关键索引均被测试核对。
- 恢复：文件型 SQLite 模拟 engine dispose/restart 后，从第二个未完成片段继续，首片段
  completed 和 attempt 计数未丢；worker 故障只回滚当前片段。
- 并发：两个 Session 模拟旧浏览器编辑，旧 version 获得
  `SEGMENT_VERSION_CONFLICT`；ORM 直接并发也触发 `StaleDataError`。
- 安全：任意 `/etc/passwd` 路径、非法 UUID、symlink、非 loopback、无 token 写请求和
  错误 token 均被拒绝；token/凭证/诊断包权限被校验。
- 隐私：日志和诊断 zip 的全文、Bearer、HF token 均被替换为 `[REDACTED]`；默认听写
  不产生数据库记录并删除临时音频。
- 静态门：Ruff、Ruff format（128 files）、mypy strict（62 files）、architecture check
  全部通过；`git diff --check` 通过。
- 阻塞项：无。

### 后续约束

所有后续作业处理必须通过 checkpoint 的短事务和审计服务写入，API 不能绕过乐观锁；
worker 只能接收 `SecureFileLocator` 批准的路径。实际模型下载、systemd 沙箱和完整断网
验收在阶段 3 实现，当前的 `model_installations` 只提供持久化合同。

## 阶段 3：建立 Fedora 运行环境、RPM/systemd 骨架与可验证的离线模型安装机制

状态：**已完成**（2026-09-03）

### 完成交付物

- 实现 `classscribe-doctor` 只读 JSON 检测器，覆盖 NVIDIA driver／nvidia-smi、
  FFmpeg／FFprobe、PipeWire、WirePlumber、GStreamer 及常用 element、IBus、PyGObject、
  GLib／GTK／IBus introspection、全部编译工具、Git、uv、Bubblewrap、Node Active LTS 和
  pnpm；探测器没有安装或系统修改命令，GPU 缺失保持 CPU 可用。
- 依据 Node.js 官方 release schedule 在 2026-09-03 固定当前 Active LTS 24，并区分
  Maintenance LTS 22 和尚未进入 LTS 的 26；文档要求每次发布复核。
- 9 个 worker 均在自己的 frozen uv 环境提供 `--cuda-check`，动态检查各环境中的
  `torch.cuda.is_available()`、wheel CUDA build 和 device count，明确不要求 CUDA Toolkit。
- 完成可构建 noarch RPM spec、SVG 图标、desktop、IBus component、受控启动脚本和三个
  systemd user unit。规范路径全部进入 RPM；模型、录音、导出、设置、凭证及任何 XDG
  用户目录均不进入包。
- 三个 unit 均以 user manager 当前普通用户运行，设置 `Restart=on-failure`、重启限速、
  `UMask=0077`、offline 环境、只读系统/no-new-privileges 及网络边界；无 `User=root`、
  `setenforce`、permissive 或 SELinux 禁用逻辑。默认只启 core。
- desktop launcher 启动 core，轮询固定 `http://127.0.0.1:8765/healthz`，健康后才打开
  loopback URL，超时明确失败。
- 实现 Model Manifest v1 schema 和事务型 `ModelManager`：一次性 10 分钟用户确认、下载／
  磁盘／环境展示、同文件系统 0700 staging、完整 commit revision、resolved revision
  核对、逐文件 size/SHA-256、额外文件/symlink 拒绝、aggregate hash 与原子发布。
- `trust_remote_code` 必须把代码存入 `code/` 快照并逐文件哈希；供应链 JSON 记录 manifest、
  来源、下载量、依赖环境、全部 hash、健康音频 hash/格式、实际健康结果和 VRAM；升级前
  生成 added/removed/changed/remote-code-changed 清单。
- 安装发布后必须用非空 16 kHz 16-bit PCM 短音频执行实际 worker 健康回调，失败清理新
  revision 并恢复旧 active；成功持久化 `model_installations`。支持完整性复验、原子回滚和
  非活动 revision 删除，禁止删除 active revision。
- 运行期 resolver 不持有 downloader 或网络客户端，缺失／篡改／审计损坏明确返回
  `MODEL_NOT_FULLY_INSTALLED`；代码、RPM 启动脚本和 systemd 均强制 HF/Transformers/
  Datasets offline。
- 实现 Bubblewrap remote-code worker 启动合同：取消全部 namespace、清空环境、不挂载
  home/完整数据根，只读挂载单个 worker/model/input，只写精确 output/socket，并仅允许
  GPU 租约列举的 NVIDIA character device。
- 新增 `docs/fedora-installation.md`、`docs/model-installation.md`，同步 model registry、
  security、README、架构门和需求追踪矩阵。

### 退出条件证据

- 阶段 3 专项：32 项通过，覆盖 9 个独立 CUDA probe、一次性用户确认、空间展示、坏
  revision／resolved revision／SHA、staging 清理、原子安装、DB 记录、短音频健康失败、
  旧版恢复、升级 diff、回滚、删除、运行时缺失/篡改断网失败、symlink 和 Bubblewrap 路径。
- RPM：实际 `rpmbuild` 生成 0.1.0 与 0.1.1 noarch 包；独立 RPM DB 使用普通用户可执行的
  root 重定位依次 install → upgrade → erase。全部规定路径存在，升级／卸载不改变模拟
  home 中 sentinel，模型/XDG 路径不在 `rpm -qlp` 清单。
- clean-XDG：真实 core 进程在全新 config/data/cache/state/runtime 启动并仅访问
  `127.0.0.1:8765/healthz`；生成的全部应用根为 0700、token 为 0600；该项因执行沙箱
  禁止普通进程 bind，使用获准的 loopback 测试单独运行。
- 全仓 Python：不含上述需 bind 的单项时 123 项全部通过，覆盖率 85.57%（门槛 80%）；
  单独 loopback E2E 1 项通过，合计 124 项。
- 静态与依赖：Ruff check/format、mypy strict（71 files）、architecture boundaries、根工程
  加共享协议/3 桌面进程/9 workers 共 14 份 `uv lock --offline --check`、desktop 文件和
  IBus XML 校验、`git diff --check` 全部通过。
- 前端回归：ESLint、Prettier、TypeScript、Vitest（1 项）和 Vite production build 通过。
- 当前 Fedora 开发容器的 doctor 如实报告主机 NVIDIA userspace、PyGObject venv、Node 24
  和全局 pnpm 尚未就绪，且未修改系统；这些是部署机依赖报告，不影响只读检测器、RPM
  内容和 CPU/offline 安全失败的完成。正式发布机的 SELinux Enforcing 实机验收在阶段 13
  重复执行，代码和包从未要求关闭 SELinux。
- 阻塞项：无。

### 后续约束

模型注册表和阶段 5 scheduler 必须使用本阶段的 manifest、runtime resolver、安装 DB、
offline environment 和 `WorkerSandbox`，不得增加运行期下载分支或直接运行 remote code。
阶段 13 必须在干净 Fedora + SELinux Enforcing 主机重复正常（非重定位）RPM 生命周期；
若出现 AVC，应修正 label/policy，禁止把关闭 SELinux 作为解决方法。

## 阶段 4：实现音频导入、质量分析、VAD、LID、切片与边界基础设施

状态：**已完成**（2026-09-03）

### 完成交付物

- 实现 `FFmpegMediaPipeline`，完整允许 WAV、FLAC、MP3、M4A/AAC、OGG、Opus、MP4、
  MOV、MKV、WebM；拒绝非法 suffix、symlink、非普通文件、8 GiB/90 分钟/8 声道上限和
  无法解析的音轨，并用稳定 error code 安全失败。
- 原件以 UUID 物理名复制到 0700 job source，流式 SHA-256、fsync、原子 rename 后设为
  0400；显示文件名仅作为元数据，master 永不覆盖原件。
- FFmpeg 明确选择 `0:a:0`，生成单声道、16 kHz、PCM S16LE staged WAV；Python 复核 header
  与实际 frame count 后原子发布只读 `audio_master.wav`。多声道支持最佳通道或显式等权
  `pan` 下混，不包含 denoise、AEC、loudnorm 或其他默认增强。
- 实现流式、内存有界 `PCMQualityAnalyzer`：时长/采样率/声道/codec/format、peak、RMS、
  近似 LUFS、clipping、DC offset、振幅与 VAD 静音/语音占比、粗 SNR、背景音乐概率、
  输入/master 双 SHA 和异常中断。所有质量项只 warning/路由，不擅自拒绝清晰录音。
- 实现默认 `FireRedVADAdapter` 以及名字和结果明确的 Silero/WebRTC fallback；统一 batch/
  streaming backend flag 与状态机，保存 raw score/source，校验 frame 绝对范围与单调性，
  合并短静音、过滤短 speech、限制 padding，输出绝对 `SpeechRegionResult`。
- `StructureWindow` 和 `TranscriptChunk` 为不同强类型：结构层默认 12 分钟/4 秒重叠/4 分钟
  末窗，并只按安全实测上调 20/30/60/90 分钟；正文 `core_span` 为 8～30 秒，按 speaker、
  300～800 ms/长停顿、句末、语言顺序选自然边界。
- 连续语音无自然边界时在 30 秒 core hard max 切分，左右 `audio_span` 各保留默认 1 秒
  （配置强制 0.8～1.5 秒），core 无缺口、context 有重叠且不越界。实现 token text +
  absolute time 去重计划，禁止固定字符删除；重叠候选保留质量更高侧。
- 实现 overlap speaker embedding 的 deterministic cosine 一对一匹配，使后一结构窗 local
  label 可映射到既有 global label，实际 speaker span 在阶段 6 接入。
- 实现 FireRedLID 5 秒窗口/2.5 秒步长和连续两窗 `p>=0.80` 滞回。手动 zh/ja/en 完全
  不调用 LID 并保存模型 prompt；自动/混合初始语言和切换均需双窗证据，低分保持当前，
  单个英文术语不切换，边界回看静音，并保存每窗 zh/ja/en raw probability、规则、转移和
  中英/日英 unified routing hint。
- `AudioPreprocessor` 串联 import→channel QC→master→VAD→LID/manual→两类切片并用最终 VAD
  更新 QC 占比；`AudioArtifactRepository` 以短事务幂等持久 speech/language spans，写前
  验证绝对范围、双端单调和语言全时轴连续，失败保留旧结果。
- 定义 canonical master、QC、VAD、LID、slice 五个 durable checkpoint；重启通过 completed
  checkpoint 找首个未完成阶段，不以空 speech 表误判静音任务。
- 新增 `docs/audio-pipeline.md`，同步 timeline、architecture、架构检查与需求追踪矩阵。

### 退出条件证据

- 阶段 4 专项 41 项通过：实际 FFmpeg 对全部 11 种格式/container 逐一 encode/probe/
  normalize；真实 stereo 最佳通道和 anti-phase 等权下混；0400 原件/master、双 SHA、QC
  指标/告警、坏 suffix/symlink/缺 ffprobe 安全失败。
- VAD/LID：batch/streaming 等价和 backend mode、FireRed/fallback source、local-time reset
  拒绝；三种手动语言零 LID、5s/2.5s、双窗阈值、单英语窗不切日语、静音回看、raw 概率
  保存均通过。
- 90 分钟：模拟 canonical master 精确为 86,400,000 samples，一次完成 VAD、2159 个滑窗
  LID、结构窗和连续 speech 正文切片；首尾精确、所有区间不越界。Hypothesis 覆盖任意
  1～86,400,000 samples 的结构窗单调、无缺口和绝对 offset。
- 边界：95 秒无停顿 speech 的所有 core 连续且 <=30 秒，每个 hard boundary 都有双侧
  context；自然边界优先级、time/text dedup、speaker embedding 跨窗映射通过。
- 持久化/恢复：speech/language 二次写入无重复；越界/非单调事务回滚保留旧数据；文件型
  SQLite 在 90 分钟 job 完成 master/QC 后 dispose/restart，从 VAD checkpoint 继续。
- 全仓 Python：需 loopback bind 的 stage 3 单项分开运行；其余 164 项全部通过，覆盖率
  85.41%（门槛 80%）；loopback E2E 1 项另行通过，合计 165 项。
- 静态/依赖：Ruff check/format（152 files）、mypy strict（83 files）、architecture、14 份
  frozen uv lock、desktop/IBus XML、`git diff --check` 全部通过；前端完整 check 通过。
- 阻塞项：无。

### 后续约束

阶段 5 worker RPC 只能接收 canonical `audio_master.wav` 的受控路径与绝对 sample range；
VAD/LID raw score 不得冒充跨模型统一置信度。阶段 6/8 必须复用结构 overlap、speaker match、
正文 core/context 和 boundary dedup 原语，不能创建另一套浮点秒切片或无重叠 hard cut。

## 阶段 5：实现模型候选池、版本化注册表、Worker RPC 与单 GPU 调度器

状态：**已完成**（2026-09-03）

### 完成交付物

- 完成 `config/model-registry.v1.yaml` 与 JSON schema：原计划 16 个候选、FireRedVAD/LID/
  Punc 三个必需伴随模型和一个 disabled real-model smoke checkpoint 共 20 项；全部 repository
  使用 2026-09-03 从官方 Git remote/API 核实的 40 字符 commit SHA。
- 每条 entry 完整记录语言、任务、batch/stream/final mode、16 kHz PCM 输入、dtype/backend、
  remote-code、安全／厂商窗口、七项响应能力、预估／实测 VRAM、安全余量、worker lock 及
  SHA-256、安装/工件 hash、benchmark source/ranking、缺陷和禁用原因。
- bootstrap profile 完整实现课堂结构/回退/对齐、中文 FireRed→ARK→Qwen、日语 Granite→
  Qwen→ARK、英语 MOSS Preview→ARK→Granite→Qwen，以及 IBus 四语言 fast/balanced/accuracy；
  选择器只按 registry capability 查询，没有语言到具体模型的业务硬编码。
- `RegistryStore` 完成 add/disable/enable/upgrade/rollback/rank/install/benchmark，写入 0600
  临时文件、fsync、原子替换并保留 before-state history；依赖 lock 漂移和 revision/hash
  不一致均安全失败。
- 完成 v1 MessagePack UDS RPC：network-order 4-byte prefix、16 MiB 总帧限制、实时 1 秒
  PCM 限制、0700 parent/0600 socket/same-UID peer；批任务只接受显式 data root 内 non-symlink
  只读文件和绝对 sample range。
- 完成 health/capabilities/load/unload/transcribe_batch/stream open-push-flush-close/align/vad/
  lid/diarize/cancel 全 method、mandatory request metadata、fixed revision response、raw/normalized
  text、绝对 segments、metrics/warnings 和稳定 error code；嵌套 `quality_probability` 在阶段 12
  前被拒绝。
- `WorkerProcess` 完成离线环境启动、socket ready、stderr 隔离、deadline、跨连接 cancel、
  crash 检测、终止升级和清理。原 9 个 family 外补齐计划候选要求的 `voxtral`/`vibevoice`，
  11 个 worker 均具六项模板文件、独立 lock 和相同 RPC 生命周期。
- `GPULeaseManager` 完成 0/10/20/30/40 单 owner FIFO；IBus 开始立即阻止新课堂 dispatch，
  已运行课堂只在 segment boundary 让出；结束后恢复，并支持同 model resident reuse。
- 完成 fast/balanced/accuracy registry-driven residency 和 VRAM margin preflight、ctypes NVML
  current/peak/OOM history、严格顺序多候选 executor，以及 cleanup→batch1→短窗→SDPA→已验证
  dtype/quant→registry 小模型→有限失败的唯一参数 OOM 恢复。
- 在 `moss_en` 隔离环境实现 local-files-only 的 CTranslate2 Whisper Tiny reference adapter。
  它使用固定 revision、CPU int8 和真实神经权重，仅用于验收，不属于任何生产 ranking。
- 新增/更新 `docs/model-registry.md`、`worker-protocol.md`、`gpu-scheduling.md`、architecture、
  troubleshooting 和需求—测试追踪矩阵。

### 退出条件证据

- 阶段 5 专项 49 项全部通过：registry 完整性/lock SHA/排序/原子 mutation/rollback；4-byte
  frame、畸形 payload、受控路径、实时 PCM、响应时间轴、deadline/cancel；11 worker 实际
  子进程 UDS load/health/capabilities/cancel/unload 和 crash 隔离；lease 抢占/resume/reuse、
  三 residency policy、顺序候选、NVML fake telemetry、OOM 有限唯一尝试。
- 真实模型：主动下载的 `Systran/faster-whisper-tiny@d90ca5fe...`（约 78 MB）在 worker 内
  `local_files_only=True` 加载；离线生成并规范化测试语音，经真实 UDS transcribe 返回非空
  文本、绝对有界 segment 和 raw model confidence，随后 unload，health 确认未加载。
- 全仓回归按所需权限拆分：socket/loopback/真实模型 suite 199 项通过且覆盖率 83.21%；RPM
  生命周期因外层提权上下文不能创建普通用户 RPM DB，单独在正常 sandbox 用户上下文 3 项
  通过；合计 202 项零失败。覆盖率门槛为 80%。
- 静态/依赖：Ruff check、Ruff format（175 files）、mypy strict（96 files）、architecture、
  desktop/XML、`git diff --check` 全部通过；根/协议/3 桌面/11 workers 共 16 份 frozen uv lock
  离线检查通过。
- 前端回归：ESLint、Prettier、TypeScript、Vitest（1 项）和 Vite production build 通过。
- 阻塞项：无。

### 后续约束

阶段 6/7 的具体 adapter 必须在现有 worker environment 内实现，并继续返回 registry 固定
revision 和绝对 sample；不能把 reference smoke model 加入 auto-best。阶段 8 只能保存 raw
confidence/logprob，不得跨模型直接比较；阶段 12 才可产生校准 probability。课堂候选必须
逐一驻留，阶段 11 dictation 必须调用 `begin_dictation`／`segment_boundary`／`end_dictation`，不得
另建 GPU 锁或中途截断课堂句段。

## 阶段 6：实现课堂结构转录、长窗口拼接与说话人归属

状态：**已完成**（2026-09-04）

### 完成交付物

- 建立 `classscribe.structure` 的 MOSS typed request/response：固定 16 kHz 绝对 sample、
  window ordinal、model/revision/request provenance、speaker local、粗文本和 acoustic events；
  RPC、领域对象与数据库均强制
  `text_role=coarse_timeline_consensus_candidate_boundary_reference` 和
  `adopted_as_final=false`，结构文本不能冒充最终正文。
- 完成 `ordinary_class/group_discussion` 配置以及 `auto/1..12` expected、min/typical/max 和
  overlap 约束；普通课堂 typical 只能 1～2，小组讨论可用 auto 或大致人数。
- 实现 MOSS 结构异常判定：worker 失败、speech 无 segment、覆盖率过低、人数越界、重复、
  大量空文本和禁用 overlap 时仍检测到重叠；每窗 MOSS/pyannote 各最多两次尝试。
- 在 `moss_td` 独立环境完成官方实现固定 commit 的 production adapter：本地完整 snapshot、
  官方 message/generate/parser API、SDPA、GPU bfloat16/CPU float32、canonical 窗口裁切、
  token callback 取消、hotword/人数/时间/声学事件 prompt、metrics 和安全 unload。
- 在 `pyannote` 独立环境完成 Community-1 production adapter：exact 或 min/max 人数、regular
  与 exclusive track、speaker embeddings、GPU/CPU、本地 snapshot 和安全 unload。真实 overlap
  从 exclusive 持久轨和 embedding 支持区间精确减去，不为 overlap 伪造唯一说话人。
- 为每个局部说话人选最多三个至少 1 秒的高质量无重叠支持，维护加权归一化 global
  centroid；综合 cosine、候选 margin、时间相邻、重叠区文本+绝对时间同人约束并保持一对一。
  observation 不足、低质量、歧义和冲突均新建 `SPEAKER_nn`；仅 overlap 的参与者也取得新的
  job-local 匿名身份，而不会因无可靠声纹被丢弃或强行合并。
- 跨窗口 dedup 只在绝对时间相交且文本高度相似时发生，按结构质量、文本信息量和稳定顺序
  选择整段胜者，从不拼接字符串。输出候选分数、决策原因、重叠 link、ID switch/rate、异常、
  路径、重试与 dedup 的 JSON-ready 可视化诊断数据。
- speaker change、300～800 ms pause、长停顿和粗句末回接阶段 4 正文切片器，形成 8～30 秒
  自然正文句段；无自然边界的 hard cut 继续保留双侧 1 秒上下文。
- 新增 `structure_segments`、`speaker_display_names` 及增强 `speaker_spans` 的 Alembic 迁移。
  逐窗 replace 原子且不影响其它窗口；所有最终结构段有 model/revision/request/window/source
  index、绝对 sample 和 fallback provenance。显示名仅 job scope，改名不修改原 diarization，
  默认不做跨课程身份识别。
- 新增 `docs/structure-and-speakers.md`，同步 architecture、database、worker protocol、模型
  worker README、architecture gate 和需求—测试追踪矩阵。

### 退出条件证据

- 阶段 6 结构相关 40 项通过：MOSS contract/异常、pyannote exclusive/真实 overlap、多个
  无重叠 embedding、global centroid、余弦/margin/邻接/约束、一对一与低可靠新建、质量胜者
  去重、DB 原子窗口/改名/约束、两个 production adapter 的 load→infer→unload 路径。
- 90 分钟精确为 86,400,000 samples，生成 8 个默认 12 分钟结构窗；相邻 4 秒区域产生
  7 个 dedup 决定且每份重复文本仅保留一次，全部 15 个最终结构段具绝对 provenance，
  speaker switch 为 0 并可计算 rate；同时形成满足 8～30 秒及 hard-cut context 的正文句段。
- 故障注入证明：首窗 MOSS 与 pyannote 连续失败只把首窗降级为可重试 VAD 粗结构，下一窗
  正常完成；MOSS 异常而 pyannote 成功保留粗文本角色并替换说话人；逐窗数据库校验失败回滚
  不删除其它窗口。
- 全仓回归按环境限制拆分：socket/thread suite 229 项通过、1 项真实模型按未配置本地
  checkpoint 正常 skip；RPM 生命周期在普通 sandbox 独立 1 项通过；合计 230 项零失败，
  覆盖率 83.66%（门槛 80%）。提权上下文中的 RPM DB 权限失败已由普通用户运行结果覆盖。
- 静态/依赖：Ruff check、Ruff format（189 files）、mypy strict（109 files）、两个生产
  worker 独立 strict mypy、architecture、根/MOSS/pyannote frozen uv locks、JSON schema
  解析均通过。前端 ESLint、Prettier、TypeScript、Vitest 和 Vite build 全部通过。
- 阻塞项：无。真实 MOSS/Community-1 权重安装、本机 VRAM/RTF 与 90 分钟 gold 质量不伪装
  为本阶段证据，按计划由阶段 12 的本机 benchmark gate 验收。

### 后续约束

阶段 7 必须消费 `StructurePipelineResult.transcript_chunks` 的自然正文 core/audio context，
并用结构说话人作为归属证据；不得直接采用 MOSS 粗文本。阶段 8 的多候选决策必须保留
当前 model/revision/raw confidence 和结构 provenance，不得把 bootstrap cosine 或 raw score
称为校准正确率。阶段 10 的 WebUI 改名只能写 job-local display mapping。

## 阶段 7：实现中文、日语、英语的语言专用正文识别适配器

状态：**已完成**（2026-09-04）

### 完成交付物

- 建立 `classscribe.asr` 统一领域合同、registry-driven planner 和串行 pipeline：中文
  FireRedASR2-AED→ARK→Qwen、日语 Granite 4.1 2B→Qwen forced Japanese→ARK、英语
  MOSS Preview 2B→ARK→Granite→Qwen；MOSS 结构候选始终为非正文参考。
- 完成 FireRed、ARK、Qwen、Granite、MOSS Preview 和 Fun-ASR 六类离线生产 adapter，
  均只加载完整本地 snapshot，物理裁切 canonical WAV，回传绝对 sample 区间、性能指标、
  warnings 和注册表固定的 model/revision。FireRed 保留原生字/词时间和 raw confidence；
  生成式路径保存准确请求区间而不伪造词时。
- `body-asr-v1` 请求在 core 和 worker 双层强制手动 `zh/ja/en`、单条 batch、
  `temperature=0`、`do_sample=false`、固定 seed、禁止异长混批，并以音频时长计算
  token/字符上限。JSON schema 和 Python 协议均拒绝隐式语言、抽样、越界 core 和越界词时。
- 读音提示覆盖课程术语、人物、地名、组织、植物、药物和公式读法；只传入官方支持的
  context/keyword 通道，提示文本明确禁止无声学证据插入。Granite 使用官方英文任务提示；
  Qwen 显式传入 `Chinese/Japanese/English`；FireRed core 和 worker 均拒绝日语。
- 未发现上游独立 `Qwen3-ASR-1.7B-JA` checkpoint，因此实验项如实实现为同一固定
  Qwen 1.7B revision 的显式 forced-Japanese route，未伪造模型身份。Fun-ASR 只允许
  `course_expert` + explicit enable，且在候选返回前强制四连 token n-gram 循环检测。
- `ASRCandidateRepository` 以短事务幂等建立自然正文段，追加 raw/normalized 文本、
  raw confidence、decode、metrics、warnings、token 绝对时间和 provenance；重跑保留
  supersession 链，所有候选初始不采纳且不产生跨模型校准概率。
- `TraditionalChineseConverter` 只从忠实简体层执行 OpenCC `s2t`，产生独立显示/导出
  变体，并检查内嵌 ASCII/英文序列不被改写。新增 `docs/body-asr.md`，同步架构、
  数据库、worker 协议、模型注册表和需求追踪文档。

### 退出条件证据

- 阶段 7 专项 31 项全部通过：三语候选顺序/手动语言/实验门控，统一请求、
  OpenCC 变体、候选持久化、绝对词时和 raw confidence；用 espeak-ng + FFmpeg 产生
  真实中/日/英语音 WAV，穿过六类生产 adapter 的本地 snapshot load→精确裁剪→
  infer→unload 路径，并覆盖官方 prompt、强制语言、英文嵌入、单批、长输出和循环拒绝。
- 全仓回归按环境限制拆分：需 socket/thread 权限的 suite 为 254 项通过、1 项按未配置
  用户本地固定权重正常 skip；RPM 生命周期在普通 sandbox 独立 1 项通过，合计 256 项零失败。
  覆盖率 83.11%，超过 80% 门槛。
- 静态/依赖：Ruff check/format（199 files）、mypy strict（118 files）、architecture boundaries、
  根/协议/3 桌面进程/11 workers 共 16 份 frozen uv lock 离线检查、JSON schema 解析全部通过。
  前端 ESLint、Prettier、TypeScript、Vitest 和 Vite production build 全部通过。
- 阻塞项：无。仓库不分发大模型权重；实际固定权重 GPU 准确率、VRAM、RTF 和课程
  gold 排名按计划由阶段 12 benchmark 验收，本阶段未以测试替身伪装真实权重结果。

### 后续约束

阶段 8 只能在同一 canonical 音频区间上对候选做质量门控、受约束对齐和 token 共识；
不得比较未校准 raw confidence，不得用长字符串投票或生成模型补写，也不得让未通过循环、
静音幻觉、脚本或时间检查的候选进入最终稿。每个最终 token 必须可追溯到 ASR 候选或确定性
术语规则。

## 阶段 8：实现自动质量门控、第二/第三模型复核与时间对齐共识

状态：**已完成**（2026-09-04）

### 完成交付物

- 建立 `classscribe.quality` 四域特征合同：声学/模型保留 raw confidence、token
  logprob、CTC/RNNT posterior、no-speech、VAD、SNR、clipping、RMS 和远场；文本计算
  字/词速、重复、压缩、字符/脚本、标点/成对、大小写和数字单位；时间检查单调、越界、
  重叠/倒序、有声覆盖和后半段漏词；多模型保存词/字、读音/音素、专名位置和
  内容/静音分歧。
- 完成共识前硬拒绝：3～10 token n-gram 超限、至少四轮最短循环周期、重复句/
  压缩率、24 字符/有声秒上限、decode prefix 无新意停滞、静音长文本、非法字符、
  脚本和词时错误均使 candidate 无效。固定 `あなたはだれですか` 无限重复在测试中
  必然拦截。
- 循环重试把原 canonical 片段分为两个连续、更短、完整覆盖的窗口，强制换模型；
  `merge_retry_pieces` 和 review pipeline 拒绝丢窗、乱序或沿用已拒绝模型的结果。
- `ReviewRouter` 消费既有 0.82/0.62 配置阈值：只在低质量、重复/幻觉、脚本、
  时间覆盖、术语、MOSS 分歧、低 SNR/远场时运行第二模型；只在高价值 token（数字、
  单位、否定、姓名/术语）冲突、两者都低质量、同音异写或内容/静音分歧时运行
  第三模型。正常片段的第二/第三 callback 零调用。
- 完成中文汉字+词典拼音、日语原文+reading+片假名/平假名归一、英语 word+可选音素
  的双层比较；大小写和标点保持独立特征。专名出现位置也进入冲突证据。
- 建立 `classscribe.consensus` token-time-first 受约束 DP 与 confusion network：所有候选（含已
  拒绝证据）必须同 canonical 区间/手动语言；优先原生词时，无词时文本以 token DP
  对齐；最终列使用单调、无重叠的绝对 sample 区间。
- 时间列权重组合本机校准 reliability、校准 token confidence、声学覆盖、词典和脚本/
  格式合法性；未校准时固定中性 0.5，raw confidence 不进入跨模型加权。每个最终 token
  保存 candidate/model/revision/source index/原时间/vote weight/列/校准来源。新增标准写法
  只能由候选 alias 命中的显式确定性术语 rule 产生。
- 无可靠列共识时只选一个当前最可信候选并标 low confidence；全部候选无效/无内容时
  产生带实际起止时间的中/日/英 inaudible marker，绝不让第三生成模型按流畅度补写。
- `ConsensusRepository` 把质量、复核路由和最终共识分别记为三类完整
  `decision_events`，更新 candidate validity/质量 JSON，并把当前最终 token 连同可验证
  provenance 写入 `token_spans`。伪造或跨 segment candidate ID 使短事务整体失败。
- 新增 `docs/quality-and-consensus.md`，同步 architecture、database、requirements traceability 和
  architecture gate；共识目录被门禁禁止引入长段 `SequenceMatcher`。

### 退出条件证据

- 阶段 8 专项 23 项全部通过：固定无限日语循环、静音幻觉、字符/脚本/时间错误、
  完整二/三模型条件、正常零 fallback、三语读音/音素、原生时间 + 无时间 DP、校准权重、
  确定性术语、单候选/inaudible fallback、同区间约束和数据库 provenance/审计。
- 全仓回归按环境限制拆分：socket/thread suite 276 项通过、1 项因未配置用户本地固定权重
  正常 skip；RPM 生命周期在普通 sandbox 单独 1 项通过，合计 278 项零失败。覆盖率
  83.70%，超过 80% 门槛。
- 静态/依赖：Ruff check/format（215 files）、mypy strict（133 files）、architecture boundaries、
  `git diff --check`、根/协议/3 桌面进程/11 workers 共 16 份 frozen uv lock 离线检查全部通过。
  前端 ESLint、Prettier、TypeScript、Vitest 和 Vite production build 全部通过。
- 阻塞项：无。本阶段未把 bootstrap 门分或 raw model confidence 伪装成校准概率；实际本机
  reliability/token calibration 及阈值调整仍由阶段 12 gold benchmark 产生。

### 后续约束

阶段 9 的术语纠正只能写 `smart_corrected_text`，不得覆盖本阶段产生的 `faithful_text`；
标点必须通过去标点字符序列不变门。Forced aligner 只能处理通过循环/脚本/覆盖/长度门的
短段；任何对齐失败必须回退 MOSS/VAD 粗时而不强压文本。

## 阶段 9：实现课程词典、三语标点、最终对齐与六格式导出核心

状态：**已完成**（2026-09-04）

### 完成交付物

- 建立 version 1 课程 YAML schema 和 `classscribe.terminology`：保存 course/instructor/
  term 的 canonical、reading、aliases、weight、source、confirmation、语言和章节；支持手工、
  TXT/Markdown/CSV、本地 PPTX/PDF、讲义、教科书与历史已确认来源。所有文件有限大小、普通且
  非符号链接；PPTX 只读 slide XML，PDF 只经本地 `pdftotext`。
- 自动材料抽取只返回不超过 0.3 权重的 suggested term，不直接改写或插入文字。滚动上下文
  只取当前课程/语言最近一至三个已确认句段和按章节、近期频率、权重排序的 top-K 术语，有
  总字符上限并显式标 `bias_only`/禁止无声学支持插入。
- 确定性术语规则只在高权重/已确认词、忠实层实际 alias、且候选输出 canonical/alias 或读音+
  声学充分支持时执行 alias→canonical；仅写 `smart_corrected_text`，完整记录位置、前后差异、
  rule ID 和来源，永不覆盖 `faithful_text`/`user_text`。
- 建立中文 FireRedPunc+VAD 停顿/说话人/语气、英文 native-first+FireRed fallback+缩写/产品/
  姓名保护、日语 Granite 边界投射→声学→必要 tagger 的三语标点模块。每个 proposal 和数据库
  写入都强制非标点字符序列精确相等；异常立即拒绝回退。英语另报 case-sensitive WER 与
  punctuation F1；日语检查 `。 、 ？ ！ 「」 （）` 配对和密度。
- FireRed worker 按上游 `FireRedPunc.process([text])` 加载完整本地 snapshot，新增严格
  `punctuate` RPC；原文/proposal 分开返回。繁体中文通过既有 OpenCC 仅在最终显示/导出转换，
  不回写忠实简体层。
- 建立 `classscribe.alignment`：固定 reliable native word→MOSS structure→gated Qwen
  ForcedAligner→VAD coarse 优先级。门控同时检查小于 `min(registry, 30s)`、覆盖、循环/漏句/
  异常字符、手动/声学语言和文字/有声比例；验证 token 单调、正时长、范围、文字不变、VAD
  覆盖、alignment cost 与 gap 偏差。失败明确保留 MOSS/VAD 粗时并写 `coarse_timing`。
- Qwen worker 按上游 `Qwen3ForcedAligner.align` 对物理 canonical crop 工作，局部秒转绝对
  sample，输入文字原样回传；新增 `final-align-v1` 强协议合同。最终 token 时间更新保留所有
  candidate provenance 和审计事件。
- 建立 TXT、Markdown、JSON、SRT、VTT、CSV 六格式原子导出，支持 faithful/smart/user 和
  逐句/可读段落；user 空时按 user→smart→faithful 显式回退。字幕只用最终词时间，按中日
  字符/CPS、英文词数/CPS/句末切 cue，最多两行，专名 `protected_group` 与数字+单位不可拆。
- 新增 `docs/postprocessing-and-exports.md`，同步 architecture、database、worker protocol、
  requirements traceability 和 architecture gate。

### 退出条件证据

- 阶段 9 专项 36 项全部通过：课程 YAML 和全部本地材料源、低权重建议、上下文长度、无声学
  插入拒绝、smart-only 差异、Hypothesis 标点字符恒等、三语 fallback/保护/投射、英语双指标、
  forced-align 门与优先级/高成本/改字拒绝/coarse fallback、worker canonical crop、四层数据库
  审计、六格式/三层/段落/字幕最终时间。
- 全仓回归按环境限制拆分：socket/thread suite 297 项通过、1 项因未配置用户本地固定权重
  正常 skip；RPM 生命周期 3 项在普通 sandbox 通过，合计 301 项零失败。覆盖率 82.77%，超过
  80% 门槛。
- 静态/依赖：Ruff check/format（205 files）、mypy strict（160 files）、11 个 worker 各自
  strict mypy、architecture boundaries、`git diff --check`、JSON schema 与根/协议/3 桌面进程/
  11 workers 共 16 份 frozen uv lock 离线检查全部通过。前端 ESLint、Prettier、TypeScript、
  Vitest 和 Vite production build 全部通过。
- 阻塞项：无。真实 FireRedPunc/Qwen ForcedAligner 大权重、实际课堂 gold 阈值和本机性能未用
  测试替身伪装，按计划由阶段 12 的本机 benchmark gate 验收。

### 后续约束

阶段 10 的自动流水线必须消费本阶段的显式门控结果和四层输出，不能把 Web/API 变成第二套
术语、标点或时间逻辑。所有写 API 必须继续执行 token/CSRF，文件只接受受控 ID；自动导出
不得等待用户打开校对页，SSE/UI 必须如实呈现 coarse timing、低置信和实际模型/阶段。

## 阶段 10：贯通课堂自动流水线、版本化后端 API 与完整 WebUI

状态：**已完成**（2026-09-04）

### 完成交付物

- 建立 `classscribe.classroom` 持久编排器：上传校验、master、QC、VAD、LID、MOSS、逐片段
  主 ASR/质量复核/术语/标点/对齐/最终验证和自动导出均为可重入 checkpoint；结构完成后按活动
  segment 动态创建，支持暂停、继续、取消、重启恢复、局部重试与 IBus 安全边界抢占。
- 完成 `/api/v1` 录音/媒体、作业/SSE、转录/候选/provenance/拆分合并/撤销重做、模型生命周期、
  benchmark 门控 profile、设置、词典/材料/术语、六格式导出和 benchmark 接口；OpenAPI surface
  有精确契约测试，所有 API 要 bearer，所有写操作额外要 CSRF，文件仅用 UUID 受控解析。
- Alembic 增加 job options、活动 segment/supersession、材料、导出 artifact、profile/settings，
  并把 checkpoint 唯一约束正确扩展到 segment 范围；upgrade/check/downgrade 往返通过。
- React 工作台完成上传、任务、同步波形转录、模型、词典、导出、设置/诊断和 IBus 页面；使用
  TanStack Query、Zustand、WaveSurfer 和带 bearer 的 fetch SSE，支持真实运行指标、四文本层、
  低置信筛选、候选/审计、说话人改名、700 ms 保存、撤销/重做与 authenticated download。
- 新增 `docs/classroom-api-webui.md`，并同步 architecture/database/security/recovery、需求追踪和
  架构路径门。

### 退出条件证据

- 阶段 10 专项 8 项通过：完整 API/OpenAPI、安全、上传到作业、token provenance、编辑/说话人/
  审计、候选采用、词典材料、设置/模型、原子导出/下载、benchmark；多片段流水线 19 checkpoint、
  重入、恢复、局部重试、IBus 安全暂停和自动导出顺序均验证。
- 全仓回归按环境限制拆分：主 suite 301 项通过、1 项因无用户本地固定权重按设计 skip；RPM
  生命周期 3 项普通 sandbox 通过，合计 304 项零失败，覆盖率 80.77%（门槛 80%）。
- 前端 ESLint、TypeScript strict、Prettier、Vite production build、Vitest 3 项和真实 Chromium
  Playwright 2 项全部通过；主场景实际提交 WAV、创建任务、打开转录、自动保存校对、创建词典/
  术语、调用模型验证并生成导出，降级场景验证 IBus 诊断失败仍不破坏界面。
- Ruff check/format、mypy strict（168 source files）、architecture boundaries、Alembic metadata
  check 和 `git diff --check` 全部通过。
- 阻塞项：无。真实大模型权重和 90 分钟本机性能不由 API fixture 冒充，将由阶段 12 的固定
  gold、已安装 revision 和硬件记录执行。

### 后续约束

阶段 11 的 IBus engine 必须保持薄，只经 dictationd 获取 preedit/commit/candidates；麦克风默认
不保存，长听写只能按 sample/time+token 去重。阶段 12 必须在真实已安装权重上重跑课堂 90 分钟
与 IBus 5 分钟验收，并只把完成 benchmark 的 profile 用作自动最佳。

## 阶段 11：实现 Fedora IBus 语音输入法、流式分块与 Wayland 全局快捷键

状态：**已完成**（2026-09-04）

### 完成交付物

- 完成三进程桌面运行时：薄 `ibus-engine-classscribe` 仅处理 IBus key/property/
  preedit/lookup/commit；`classscribe-dictationd` 独占麦克风、VAD、ASR 与租约；
  `classscribe-hotkey` 通过 Portal GlobalShortcuts 提供 Wayland 全局按住/松开语义。
- 完成 IDLE→ARMING→LISTENING/INTERIM_UPDATE→FINALIZING→CANDIDATE_SELECT→
  COMMITTING→IDLE 状态机，Esc/麦克风/worker/socket/GPU 异常均清空 preedit、不
  commit、释放租约并回到 idle；ARMING 在慢速 GPU/麦克风获取前就可见。
- 完成语言、快速/平衡/最高精度、自动最佳/多个具体预加载模型、按住/切换、
  interim 显示和标点 property；多模型 router 只在已预加载 UDS 间选路，不在
  dictationd 内加载 Torch/CUDA。
- GStreamer `pipewiresrc` 管线固定为 16 kHz/mono/S16LE，将任意 appsink buffer 重组
  为精确 20 ms 帧；默认使用预加载 FireRed streaming VAD，内存 PCM 随会话清除且
  不持久化。
- 完成模型 chunk、3～20 秒语义句段和 20～30 秒 hard chunk 三层分块；保留
  1.5～2.5 秒 overlap、绝对 sample、`chunk_seq`、rolling context 和未提交尾部。
  stable-prefix 和合并按 token+绝对时间，没有固定字符删除；5 分钟固定回归证明
  300 个 token 跨多块不丢不重。
- Nemotron adapter 使用 `StreamingFeatureBufferer`/`conformer_stream_step`，跨 chunk 保存
  attention/convolution/cache-length/RNNT hypothesis/predictor output；fast 只排空右上下文，
  balanced 重解当前句段。accuracy 按语言选择 batch/final worker：显存足够时双驻留，否则
  松键后由 core 卸载流式 GPU worker 再热切换；batch-only 模型经有界内存 PCM→临时 WAV 桥运行。
- Auto 语言在稳定边界融合主 ASR 与 FireRedLID，缺失 detector 不当作零票，连续
  两窗达到 0.80 才切换；日语英文术语保持日语会话。三种标点模式保证非标点
  字符序列不变。
- core GPU IPC 只接受同 UID、0600 UDS；begin 响应会等待所有运行中课堂任务到达
  checkpoint 并真正变为 PAUSED。听写期间新任务只记为 deferred；end 仅恢复由
  此机制暂停/延后的任务，不恢复用户原本暂停的任务。
- 交付 GTK/Qt/Firefox/Chromium/Electron/terminal/LibreOffice 八类兼容合同和 plain-preedit
  fallback；Portal 状态与 daemon 配置/模型经已认证 `/api/v1/ibus/status` 显示。
  WebUI 对离线或不完整响应也保持“按住说话”界面可用。
- 扩充 RPM component、systemd user unit 和 GStreamer/Portal 依赖；新增 `docs/ibus.md`
  并同步 architecture/security/recovery/diagnostics/worker protocol/需求追踪。

### 退出条件证据

- 听写专项通过：按住/松开/preedit/候选/提交/取消、5 分钟分块、稳定前缀、
  双 LID、按语言 accuracy 常驻/热切换、batch-only PCM 桥、多预加载模型路由、20 ms 帧、
  Portal fallback、同 UID GPU IPC 及“响应等待安全边界”。课堂 pipeline 另验证运行任务暂停、新任务
  deferred 和精确恢复。
- 全仓主 suite 323 项通过、1 项真实模型因未配置用户本地 checkpoint 正常 skip；
  RPM 生命周期 3 项在普通 sandbox 通过，合计 326 项零失败。核心+协议分支覆盖率
  80.08%，达到 80% 门槛。
- Ruff check/format（264 files）、mypy strict（180 source files）、11 个 worker 独立
  strict mypy、architecture boundary、16 份 frozen uv lock 离线检查和所有健康入口通过。
- 前端 ESLint、Prettier、TypeScript、Vitest 3 项、Vite production build 和真实 Chromium
  Playwright E2E 2 项全部通过。
- 开发容器的 system Python 可导入 PyGObject/IBus，GStreamer PipeWire 元件可用；当前
  venv 仍如实报告 GI 不可导入、Node 22/pnpm 和 NVIDIA userspace/driver 不匹配。八类应用
  的真实 GNOME/KDE/Wayland/X11 矩阵将由阶段 12 runner 只记实际可用项，不伪造未安装
  应用的通过记录。
- `git diff --check` 通过。阻塞项：无（真实权重的精度/延迟与多桌面实机数据属于
  阶段 12 的基准交付，本阶段未用 fixture 伪装该结果）。

### 后续约束

阶段 12 必须用已安装 revision 和用户人工 gold 产生分语言/场景的校准与排名；
不得把合成音频、未安装桌面应用或 bootstrap 顺序写成本机真实精度/兼容结果。
任何模型更新都必须使当前 calibration/ranking 失效并在恢复 `auto_best` 前重跑相应基准。

## 阶段 12：建立 Gold 基准、校准、自动选模与全层验收

状态：**实现完成，实机退出条件阻塞**（2026-09-04）

### 已完成的工程交付

- 建立严格私有 gold/prediction JSONL 合同、schema 和 loader：canonical PCM 16 kHz mono S16LE
  WAV、绝对 sample、train/validation/test、不跨音频 split、人工文本/词时/句界/speaker/voiced span、
  术语/entity/tag；拒绝 traversal、任一级 symlink、重复 ID、乱序注释和超过真实音频的 range。
- 完成中日 raw/normalized CER、英语 raw/normalized WER、术语 P/R/F1、数字/单位/否定/姓名、
  标点 P/R/F1/PER、capitalization、括号、漏句、静音幻觉、循环、覆盖/异常字符、词/句边界、
  DER/JER、speaker-attributed error、跨窗 switch 和完整性能指标。IBus percentile 在整个 test split
  上计算并强制 observation coverage，缺 runtime/latency/provenance 的候选不能进入排名。
- 每个 model revision × language × classroom/IBus 独立使用 train 拟合 isotonic/Platt、validation
  Brier 选型、test Brier 只报告；工件绑定 model/manifest/train hash。registry revision 更新会清空
  校准/排名，profile 仅接受精确顺序、生产 gold、真实模型、非合成且有 manifest hash 的完成 run，
  并支持 profile/ranking 回滚。
- 完成统一 async runner、原子 0700/0600 报告和数据库短事务持久化；六个语言/产品组必须全部有
  eligible 排名。综合分先执行 timeline/loop/silence/provenance/RTF/IBus latency 硬门，再比较正文、
  术语、漏句/幻觉、说话人、标点、性能和 VRAM。
- 冻结六个历史回归、11-worker×10-case 合同矩阵、桌面 inventory/evidence 合并器、结构化测试证据
  合同和 Phase 12 fail-closed 验收器。简单布尔值、未安装应用、fixture、合成 gold 或缺少证据 hash
  均不能成为发布通过。
- 完成 `docs/benchmark.md`、`docs/test-matrix.md`、normalizer/metric policy、gold/prediction schema、
  test evidence 模板和当前诚实的 incomplete acceptance/desktop 报告。

### 自动验证证据

- 全仓 Python/协议 suite 共 338 项：非 RPM 与 RPM 的 split 运行合计 337 passed，真实固定权重
  测试因未设置 `CLASSSCRIBE_REAL_MODEL_PATH` 正常 skip；覆盖率 80.32%。RPM 生命周期在普通
  sandbox 3/3 通过（提升权限运行时临时目录 UID 不同会使 rpmdb lock 被拒绝，已按既有约束拆跑）。
- Ruff check/format（281 files）、mypy strict（194 source files）、11 worker 各自 strict mypy、
  architecture boundary、`git diff --check` 和根/协议/三桌面进程/11 worker 共 16 份 frozen uv lock
  离线检查通过。
- 前端 ESLint、Prettier、TypeScript、Vitest 3 项和 Vite production build 通过。

### 未满足的退出条件与禁用策略

- 仓库和当前 XDG 数据区没有用户的中/日/英人工课堂与 IBus gold，也没有 90 分钟真实课堂、
  50～100 条/语言近讲或 1/2/5+ speaker 数据；不生成替代内容。
- 所有生产 checkpoint 仍为 `not_installed`，真实模型集成测试保持 skip；本机 NVIDIA
  userspace/driver 不匹配，因此不声称 CUDA RTF/VRAM/OOM/抢占数据。
- 当前桌面只安装部分 GNOME 应用；Wayland KDE、X11 GNOME/KDE 与全部八类应用交互尚未执行，
  `ibus-desktop-compatibility.json` 保持 `incomplete`。
- 因此 `phase12-acceptance.json` 保持 `incomplete`，不会产生或应用 `local_gold` auto-best，产品
  继续使用明确标记为 bootstrap 的 profile。阶段 13 只能在记录这些偏差并阻止发布签名的条件下
  继续加固，不能把阶段 12 标为完成。

## 阶段 13：完成发布加固、风险闭环、文档、RPM 与最终交付

状态：**工程实现完成，发布签核阻塞**（2026-09-04）

### 已完成的工程交付

- 冻结 `release/release-manifest.v1.json`、20 个模型的 revision/逐文件 manifest/许可证披露、
  根与 16 个 Python 工程 lock、pnpm lock、协议 schema、迁移、RPM 和文档 inventory；
  `classscribe-release-check` 对缺件、lock hash、registry/revision/license 漂移和架构绑定 fail closed。
- 模型安装改为两步确认：预检只返回许可证/remote-code/空间/diff/一次性 token，确认才允许固定
  Hugging Face host+commit+文件的下载。下载器拒绝凭证 URL、跨 host redirect、symlink、短写、
  大小/hash 不符；真实短 WAV 完成离线 load→infer→unload 后才切换 active revision。
- worker 环境复制源码与共享协议，使用 frozen/no-dev/no-editable 创建；生产运行只 `resolve()`
  已完成环境，绝不在课堂或听写路径 `uv sync`。Bubblewrap 取消 worker 网络，只读挂载一个模型/
  一个输入，写入精确 output/socket，并仅暴露枚举的 NVIDIA character device。
- 生产 `ProductionStageRunner` 已绑定课堂全部 13 checkpoint：真实音频/QC/VAD/LID、MOSS 结构、
  主/备 ASR、质量与校准共识、术语、严格标点、门控对齐、最终验证和六格式自动导出。服务 preflight
  在创建 Job 前解析已安装 revision/环境；本机有效 profile/calibration 进入真实候选顺序与 token
  概率/provenance，revision 或 manifest 变化立即回退 bootstrap。
- core 新增 `DictationWorkerSupervisor`，在 VRAM+margin 总预算内常驻已安装流式 ASR，并以 CPU
  常驻 VAD/LID；profile 安装/应用/回滚触发刷新。v1 handoff 清单和 socket 均受 same-UID、非
  symlink、owner-only、路径/大小约束；dictationd 在 idle 会话边界重新解析 `zh/ja/en/auto ×
  fast/balanced/accuracy`，从不加载模型库。预算不足的相容 profile 显式复用已加载模型。
- 完成 `classscribe-doctor`、脱敏诊断包、静态 WebUI/RPM installed-tree 校验、固定回归音频物化、
  风险台账、已知限制、官方来源、用户/安装/API/算法/协议文档。RPM 双版本 install→upgrade→erase
  合同保留用户 XDG 数据，三个普通用户 service、IBus component、desktop 和图标均进入清单。
- 最终架构门验证课堂仍为“结构→语言正文→可疑复核→时间对齐共识→术语→严格标点→门控对齐
  →自动导出”，IBus 仍为“PipeWire/VAD→常驻流式 preedit→自动分块→可选确认→commit”；新增
  模型只需 worker+registry+benchmark，不需要复制业务流水线。

### 自动验证证据

- 全仓按运行权限拆分合计 387 passed、1 skipped、零失败：非 RPM 的 core/protocol/IBus/worker/
  API/生产流水线共 384 passed，真实固定 checkpoint 因未设置 `CLASSSCRIBE_REAL_MODEL_PATH` 按
  设计 skip；RPM 三项另在普通 UID sandbox 通过。statement+branch 总覆盖率 80.16%，超过 80% 门。
- Ruff format/check（310 files）、mypy strict（218 source files）、11 个 worker 各自三入口 strict
  mypy、architecture boundary 和 `git diff --check` 通过；根/协议/3 桌面进程/11 worker 共 16 份
  uv lock 均以 offline `uv lock --check` 通过。
- 前端 Prettier、ESLint、TypeScript project build、Vitest 3 项、Vite production build 和真实
  Chromium Playwright E2E 2 项通过；core、三个桌面进程和 11 worker 的 health entry 全部通过。
- 统一 `classscribe-doctor` 在当前 Fedora 44 主机成功生成依赖+脱敏诊断，但如实返回 1：项目 venv
  缺 GI namespace、系统 Node 22 为 Maintenance LTS、无全局 pnpm，且 NVIDIA driver/userspace
  无法通信。它们与生产 checkpoint/桌面矩阵一起保留为实机发布环境阻塞，不由本地 node_modules
  或系统 Python 的局部可用性掩盖。
- `release/release-readiness.json` 保存九门机器结果，validator 预期以状态码 2 返回 `blocked`；
  六个工程门为 true，三个外部签核门为 false。

### 发布门结果与未满足退出条件

- `artifact_inventory`、`model_revision_lock`、`model_license_inventory`、
  `dependency_lock_hashes`、`final_architecture`、`production_runtime_binding` 六门通过。
- `source_license` 未通过：仓库没有版权方提供的 ClassScribe 源码和自制 SVG 许可证，inventory
  保持 `NOASSERTION` 且明确 `release_blocking=true`；实现者不能代替版权方选择授权。
- `phase12_acceptance` 未通过：缺用户私有中/日/英 gold、已安装固定生产权重、可用 CUDA 驱动和
  90 分钟/近讲/多说话人实测，报告保持 `incomplete`。
- `desktop_matrix` 未通过：GNOME/KDE × Wayland/X11 × 八类应用没有全部在已安装实机执行，报告
  保持 `incomplete`。
- 因此阶段 13 的源码工程已经交付，但计划规定的“干净 Fedora 可复现 + 所有实机性能/桌面条件 +
  可再分发许可证”退出条件不能诚实标记完成，正式 RPM 不得签名或发布。解除只能补充外部证据/
  版权方决定并重新运行 Phase 12、桌面和 release validator，不能修改状态文字绕过。
