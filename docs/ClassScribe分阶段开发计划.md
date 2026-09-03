# ClassScribe 分阶段开发计划与完整技术蓝图

> **用途**：Fedora 本地离线的中／日／英语课堂转录系统与 IBus 语音输入法  
> **目标硬件**：NVIDIA GeForce RTX 4070（按常见 12 GB 显存设计）、充足 CPU 与内存  
> **事实核查截止日期**：2026-09-03  
> **开发性质**：从零实现，不复用此前试验工程  
> **使用范围**：个人使用；运行时不得上传音频或文本到云端  
> **文档定位**：本文件是原始绿地开发蓝图的完整分阶段替代版。执行时无需再参照原文件；原蓝图全部 40 节内容与技术细节均已纳入相应阶段。

---

## 执行方式与阶段门规则

本计划使用 **14 个阶段（阶段 0～13）**。阶段编号表达技术依赖，不表达工期；每个阶段可以拆成任意数量的提交、迭代或个人工作日。允许并行开发，但只有当所依赖阶段的接口、数据模型和不变量已经冻结并通过验收时，才可合并到主干。

必须遵守以下规则：

1. **阶段完成以退出条件为准，不以“代码大致写完”为准。** 每一阶段的交付物、测试和退出条件必须全部满足；暂时不能满足的项目必须登记为阻塞项，不能静默带入下一阶段。
2. **基础约束不能被上层绕过。** WebUI、IBus 和具体模型 worker 不得自行建立第二套时间轴、模型选择逻辑、日志格式、目录规则或 GPU 调度。
3. **每阶段都要更新需求—测试追踪矩阵。** 原蓝图中的每一项要求必须能定位到实现提交、测试或明确的运行时检查。
4. **模型与阈值是可更新配置，协议、时间轴、可追溯性和隐私是长期不变量。** 首次安装的“自动最佳”只是 bootstrap，稳定排名必须由本机 gold benchmark 决定。
5. **所有阶段默认离线验收。** 除用户主动安装/更新模型外，测试时应阻断外网，证明运行期不会隐式下载或上传。
6. **不以人工校对掩盖自动流水线缺陷。** 课堂任务必须能在无人干预时完成并导出；校对页只用于增强和审计。

### 阶段依赖主链

```text
阶段 0  产品与质量合同
  ↓
阶段 1  架构 / 仓库 / 配置 / canonical timeline
  ↓
阶段 2  数据库 / 状态机 / 安全 / 恢复
  ↓
阶段 3  Fedora / RPM / systemd / 模型安装与离线
  ↓
阶段 4  音频导入 / QC / VAD / LID / 切片
  ↓
阶段 5  模型注册表 / Worker RPC / GPU 调度
  ↓
阶段 6  结构转录 / 说话人
  ↓
阶段 7  中日英正文模型
  ↓
阶段 8  QA / fallback / 时间对齐共识
  ↓
阶段 9  词典 / 标点 / 对齐 / 导出核心
  ↓
阶段 10  课堂流水线 / API / WebUI
  ↓
阶段 11  IBus / 流式长听写 / Portal 快捷键
  ↓
阶段 12  Gold benchmark / 校准 / 全测试 / 性能
  ↓
阶段 13  发布加固 / RPM / 文档 / 最终交付
```

---

## 阶段总览与原蓝图覆盖矩阵

| 阶段 | 核心结果 | 纳入的原蓝图章节 |
|---|---|---|
| 阶段 0 | 冻结产品边界、质量承诺与工程原则 | 1.项目目标与已确定决策、2.成功标准、3.设计原则 |
| 阶段 1 | 建立总体架构、仓库骨架、配置系统与 canonical timeline | 4.总体架构、5.建议技术栈、9.仓库结构、10.本机目录与数据生命周期、35.配置示例、36.实现依赖顺序（不是里程碑或时间表） |
| 阶段 2 | 实现核心服务、数据库、持久化状态机、安全、日志与故障恢复 | 25.数据库设计、30.安全与隐私、31.日志、诊断与可观测性、32.故障恢复 |
| 阶段 3 | 建立 Fedora 运行环境、RPM/systemd 骨架与可验证的离线模型安装机制 | 28.Fedora 环境与安装、29.模型安装、固定版本与离线运行 |
| 阶段 4 | 实现音频导入、质量分析、VAD、LID、切片与边界基础设施 | 13.音频导入与质量分析、14.VAD、切片与边界处理、15.语言选择与混合语言路由 |
| 阶段 5 | 实现模型候选池、版本化注册表、Worker RPC 与单 GPU 调度器 | 6.模型事实与候选池、7.模型注册表、8.Worker 协议、11.GPU 调度与显存策略 |
| 阶段 6 | 实现课堂结构转录、长窗口拼接与说话人归属 | 16.结构转录与说话人 |
| 阶段 7 | 实现中文、日语、英语的语言专用正文识别适配器 | 17.语言专用正文识别 |
| 阶段 8 | 实现自动质量门控、第二/第三模型复核与时间对齐共识 | 18.自动质量检测、19.自动复核与共识算法 |
| 阶段 9 | 实现课程词典、严格标点、文本分层、精细对齐与导出核心 | 20.课程词典与上下文、21.标点、大小写与格式、22.时间轴与强制对齐 |
| 阶段 10 | 贯通课堂自动流水线、版本化后端 API 与完整 WebUI | 12.课堂转录完整流水线、23.WebUI 功能、24.后端 API |
| 阶段 11 | 实现 Fedora IBus 语音输入法、流式分块与 Wayland 全局快捷键 | 26.IBus 语音输入法详细设计 |
| 阶段 12 | 建立 Gold 基准、置信度校准、自动选模、全层测试与性能验收 | 27.基准、校准与自动选模、33.测试计划、34.性能目标与验收阈值 |
| 阶段 13 | 完成发布加固、风险闭环、文档、RPM 与最终交付 | 37.主要风险与应对、38.完成时应具备的交付物、39.最终架构选择摘要、40.官方资料与事实来源 |

> 覆盖校验：原蓝图第 1～40 节各出现且仅出现一次；每节正文完整保留在对应阶段的“完整技术规范”中。阶段任务、交付物和退出条件是在原内容之上增加的执行组织，不替代或删减技术规范。

---

## 阶段 0：冻结产品边界、质量承诺与工程原则

### 阶段目标

把两个产品形态、隐私边界、语言与模型选择语义、自动化质量目标、不可承诺事项和全局设计原则固化为不可被后续实现随意改变的工程合同。

### 进入条件

- [ ] 无前置代码要求；以 Fedora、RTX 4070（常见 12 GB）、本机离线运行和从零开发为既定条件。
- [ ] 确认课堂录音最长约 90 分钟，通常 1～2 名说话人，IBus 单次通常少于 1 分钟但必须支持超长连续输入。

### 实施任务

- [ ] 建立产品需求文档、架构决策记录（ADR）和需求—测试追踪矩阵。
- [ ] 把课堂工作台与 IBus 的共享基础设施和不同推理策略明确拆开，禁止以后用一个含糊的“转录模式”混合两者。
- [ ] 固化四种语言选择：中文、日语、英语、自动/混合；固化“自动最佳”“手动主模型”“严格单模型测试”的准确语义。
- [ ] 固化忠实转录版、智能纠正版、用户版的关系，以及无法可靠识别时的三种语言标记。
- [ ] 把“无需人工阻塞即可完成最高质量自动结果”写成验收要求，人工校对只能是可选增强。
- [ ] 把运行期完全本机、默认无外网、不得遥测和不得上传音频/文本写成安全不变量。
- [ ] 把时间轴优先、短片段复核、原生能力优先、模型可替换、单 GPU 有序调度、可追溯自动化、标点与改字分离、本机基准决定最好写入代码评审清单。
- [ ] 把“不承诺零 CER/WER、不承诺重叠语音绝对正确、不把宣传数字当本机排名、不允许 LLM 自由改写忠实层”写入 README 和用户界面措辞规范。

### 阶段交付物

- [ ] 冻结版需求规格、ADR 集、术语表和需求—测试追踪矩阵。
- [ ] 产品模式、语言模式、模型选择、文本层、未识别标记和隐私策略的统一枚举/配置草案。
- [ ] 首版验收清单，后续每一阶段都必须回溯到本阶段的成功标准。

### 退出条件

- [ ] 每一条“必须实现”都能对应至少一个自动测试或人工验收步骤。
- [ ] 所有不可承诺事项已经进入对外文案与工程规范，没有“100% 准确”之类错误目标。
- [ ] 后续阶段不得再对产品边界和核心不变量作隐式假设。

### 本阶段完整技术规范

> 以下内容是本阶段必须实现的完整约束。代码、配置、测试和文档不得只实现上面的摘要而忽略本节细节。

#### 原蓝图第 1 节：项目目标与已确定决策

本软件包含两个共享基础设施、但推理策略不同的产品形态：

1. **课堂文件转录工作台**
   - 输入最长约 90 分钟的音频或视频。
   - 支持中文、日语、英语及自动／混合语言模式。
   - 通常为 1～2 名说话人；小组讨论时允许更多说话人。
   - 基本不存在多人同时讲话，但系统仍需检测并标记重叠语音。
   - 自动完成时间轴、说话人、正文、标点、术语纠正和导出。
   - 人工校对页面保留，但不是任务完成或导出的必经步骤。

2. **IBus 语音输入法**
   - 仅面向 Fedora/IBus。
   - 支持中文、日语、英语和自动／混合语言。
   - 默认“按住说话、实时显示预编辑文本、松开后确认并提交”。
   - 同时提供专用 `ClassScribe Voice` IBus 输入源和 Wayland 可用的全局快捷键。
   - 单次通常少于 1 分钟；持续说话超出模型安全长度时，必须自动分块、稳定提交、保持上下文并避免重复或漏字。

已确定的产品策略：

- 所有正式推理均在本机完成；联网仅用于用户主动安装或更新模型。
- 课堂转录与 IBus 可使用不同模型。
- 语言选择提供：`中文`、`日语`、`英语`、`自动/混合`。
- 每种语言提供 `自动最佳` 以及可手动切换的候选模型。
- 手动选择模型时，默认含义是“指定主模型”；自动质量检查仍可调用备用模型。另设“严格单模型测试”开关。
- 中文默认简体普通话，允许中英混说；繁体输出通过独立转换层提供。
- 同时保存“忠实转录版”和“智能纠正版”。
- 无法可靠识别的区间不编造内容，分别输出：
  - 中文：`［听不清 00:12:34.200–00:12:37.500］`
  - 日语：`［聞き取り不明 00:12:34.200–00:12:37.500］`
  - 英语：`[inaudible 00:12:34.200–00:12:37.500]`
- 课堂默认保留匿名说话人标签，允许改名。
- 软件应做到“**无需人工阻塞即可获得当前系统能生成的最高质量结果**”，但不能把任何 ASR 宣称为所有录音条件下逐字 100% 正确。

---

#### 原蓝图第 2 节：成功标准

##### 2.1 必须实现

- 90 分钟录音可断点续跑；服务重启后不丢任务状态。
- 任意语言模式都能完成自动转录并直接导出，不要求进入校对页面。
- 时间戳单调、在音频范围内、不会把多个模型的一整段不同文本错误地标到同一个“时间点”。
- 不再使用“比较几个长字符串并选最像者”的共识算法。
- 循环重复、静音幻觉、语言脚本错误、异常字符率和时间轴异常会被自动拦截。
- 所有自动采用文字都有来源记录：模型、版本、音频区间、规则、质量分和修改差异。
- IBus 连续说话超过模型窗口时自动切段；边界处不重复、不丢词。
- 运行时默认无外网请求；音频、文本、课程词典和日志均留在本机。

##### 2.2 不作为承诺

- 不承诺任何音频都达到零 CER/WER。
- 不承诺重叠语音中每个说话人的每个字都能正确归属。
- 不将厂商在不同数据集上的宣传数字直接当作本机课堂录音的最终排名。
- 不允许“为了文本看起来完整”而让语言模型自由改写忠实转录版。

---

#### 原蓝图第 3 节：设计原则

1. **时间轴优先**：所有候选文本必须先绑定到真实、可追溯的音频区间，之后才允许比较。
2. **短片段复核**：语言模型型 ASR 只处理自然边界内的短句或短段，不把 90 分钟或 120 秒长文本直接相互投票。
3. **原生能力优先**：有原生时间戳、置信度或流式缓存时优先使用；外部强制对齐只在质量门控通过后运行。
4. **模型可替换**：模型名称、版本和能力全部进入注册表，不写死在业务流水线中。
5. **单 GPU 有序调度**：RTX 4070 默认仅允许一个重型推理任务占用 GPU；IBus 拥有最高优先级。
6. **可追溯自动化**：自动共识可以组合不同模型的词，但每个词必须有候选来源或确定性的术语规则来源。
7. **标点与改字分离**：标点恢复不得偷偷改变词语；术语纠正必须进入另一文本层并保留差异。
8. **本机基准决定“最好”**：初始默认值来自公开事实，稳定默认值由用户自己的课堂与听写基准决定。

---

---

## 阶段 1：建立总体架构、仓库骨架、配置系统与 canonical timeline

### 阶段目标

先建立稳定的进程边界、代码仓库、共享协议位置、XDG 数据布局、配置模式和以 16 kHz sample index 为核心的时间轴，使后续模型、WebUI 与 IBus 都建立在同一基础上。

### 进入条件

- [ ] 阶段 0 的产品合同已冻结。
- [ ] 开发机已确认使用 Fedora，代码仓库为空或只包含初始化文件。

### 实施任务

- [ ] 创建 monorepo，并严格按完整仓库结构建立 backend、frontend、ibus、workers、protocol、benchmarks、packaging、docs、tests 等目录。
- [ ] 建立 Python 3.12 核心工程、uv 锁文件、Node/pnpm 前端工程和统一 pre-commit；此阶段只建立依赖边界，不把任何模型深度学习依赖放入核心环境。
- [ ] 实现进程边界骨架：classscribe-core、classscribe-dictationd、薄 IBus engine、hotkey portal 伴随进程，以及每模型独立 worker 的启动接口。
- [ ] 定义 canonical timeline：所有内部时间均为 16,000 samples/s 的 int64 start_sample/end_sample；禁止在持久化层累加浮点秒数。
- [ ] 实现 XDG 路径解析、目录创建、权限设置和受限数据根目录验证。
- [ ] 建立 versioned config schema，载入完整配置示例并支持默认值、用户覆盖、环境变量覆盖和配置迁移。
- [ ] 建立 protocol/schema、protocol/python 和 contract_tests 的空骨架，为后续 MessagePack RPC 保留版本字段。
- [ ] 写入 docs/architecture.md、timeline.md、model-registry.md、worker-protocol.md、ibus.md、benchmark.md、troubleshooting.md 的首版目录。
- [ ] 将原蓝图的实现依赖顺序转换为阶段门：协议与时间轴先于数据库、音频、模型、WebUI 和 IBus；并行开发只能在依赖已经冻结后进行。
- [ ] 为 core、worker、dictationd、IBus engine 定义不允许跨越的依赖规则，并用静态检查或 CI 脚本阻止核心环境导入具体模型库。

### 阶段交付物

- [ ] 可安装依赖并通过 lint/typecheck 的仓库骨架与锁文件。
- [ ] 可被单元测试加载的配置模型、XDG 路径模块和 canonical timeline 工具。
- [ ] 四类进程的最小启动/健康检查骨架及协议版本常量。
- [ ] 基础架构文档和阶段依赖图。

### 退出条件

- [ ] 90 分钟 sample↔ms 往返测试无累计漂移，所有区间满足 int64 约束。
- [ ] 核心环境不依赖任何具体 Transformers/NeMo/vLLM/远程模型代码。
- [ ] 配置示例可以完整解析，XDG 目录只在允许位置创建。
- [ ] 仓库结构、进程职责和后续依赖顺序已经在 CI 中可验证。

### 本阶段完整技术规范

> 以下内容是本阶段必须实现的完整约束。代码、配置、测试和文档不得只实现上面的摘要而忽略本节细节。

#### 原蓝图第 4 节：总体架构

```text
┌──────────────────────────────────────────────────────────────┐
│                         Web 浏览器                            │
│ 上传 / 模型选择 / 进度 / 波形 / 候选 / 可选校对 / 导出      │
└──────────────────────────────┬───────────────────────────────┘
                               │ HTTP + SSE (127.0.0.1)
┌──────────────────────────────▼───────────────────────────────┐
│ classscribe-core                                              │
│ FastAPI / 作业编排 / SQLite / 质量控制 / 决策 / 导出         │
└─────────────┬────────────────┬─────────────────┬──────────────┘
              │                │                 │
              │ MessagePack RPC over Unix Domain Socket         │
              │                │                 │
┌─────────────▼──────┐ ┌───────▼────────┐ ┌──────▼─────────────┐
│ 音频与结构 Worker │ │ ASR Worker 池   │ │ 对齐/说话人 Worker│
│ FFmpeg/VAD/LID    │ │ 每模型独立环境  │ │ MOSS/Qwen/pyannote│
└────────────────────┘ └────────────────┘ └────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ classscribe-ibus-engine（薄 IBus 引擎，不加载模型）          │
│ preedit / commit / 候选窗 / 属性菜单                         │
└──────────────────────────────┬───────────────────────────────┘
                               │ Unix Domain Socket
┌──────────────────────────────▼───────────────────────────────┐
│ classscribe-dictationd                                        │
│ PipeWire 采音 / 流式 VAD / 流式 ASR / 自动分块 / 最终确认    │
│ 与 classscribe-core 共用模型注册表和 GPU 调度器               │
└──────────────────────────────────────────────────────────────┘
```

##### 4.1 进程边界

- `classscribe-core`：不安装大型 GPU 模型依赖，只负责业务、数据库、任务编排和 Web API。
- `classscribe-dictationd`：持有实时会话、麦克风和流式模型状态。
- 每个模型族使用单独 worker 和独立 Python 虚拟环境，避免 Transformers、NeMo、vLLM、远程模型代码之间的版本冲突。
- IBus 引擎只负责输入法协议与界面，不直接加载 PyTorch/CUDA。
- 全局快捷键由单独的桌面伴随进程通过 XDG Desktop Portal `GlobalShortcuts` 注册；Wayland 下不使用不可靠的全局键盘抓取。

---

#### 原蓝图第 5 节：建议技术栈

##### 5.1 核心与后端

- Python 3.12，按进程独立锁定版本。
- FastAPI、Uvicorn。
- SQLAlchemy 2、Alembic、SQLite WAL。
- `msgspec` 或 MessagePack：Unix Socket worker 协议。
- FFmpeg/FFprobe、libsndfile、soxr。
- `uv`：Python 版本、虚拟环境和锁文件管理。
- systemd user services：核心服务、听写守护进程、全局快捷键伴随进程。

##### 5.2 Web 前端

- React、TypeScript、Vite。
- TanStack Query：服务状态和缓存。
- Zustand 或等价轻量状态管理。
- WaveSurfer.js：音频波形、区域与点击跳转。
- SSE：批处理任务进度。
- Vitest、Playwright。

##### 5.3 IBus 与桌面

- PyGObject + IBus 1.0 API。
- GStreamer + PipeWire 音频源，统一处理采样率、声道、低延迟缓冲。
- XDG Desktop Portal GlobalShortcuts：Wayland/GNOME/KDE 的全局按键。
- RPM 作为最终本机安装形式；不以 Flatpak 作为主打包方式，避免 IBus、GPU、模型目录和宿主音频集成复杂化。

##### 5.4 代码质量

- Python：Ruff、mypy 严格模式、pytest、Hypothesis、coverage。
- TypeScript：strict、ESLint、Prettier、Vitest、Playwright。
- pre-commit。
- 所有 worker 共享同一份版本化协议规范和契约测试。

---

#### 原蓝图第 9 节：仓库结构

```text
classscribe/
├── README.md
├── LICENSES/
├── pyproject.toml
├── uv.lock
├── pnpm-lock.yaml
├── docs/
│   ├── architecture.md
│   ├── model-registry.md
│   ├── worker-protocol.md
│   ├── timeline.md
│   ├── ibus.md
│   ├── benchmark.md
│   └── troubleshooting.md
├── protocol/
│   ├── schema/
│   ├── python/
│   └── contract_tests/
├── backend/
│   └── classscribe/
│       ├── api/
│       ├── db/
│       ├── jobs/
│       ├── scheduler/
│       ├── audio/
│       ├── timeline/
│       ├── quality/
│       ├── consensus/
│       ├── terminology/
│       ├── punctuation/
│       ├── exports/
│       └── models/
├── frontend/
│   ├── src/
│   └── tests/
├── ibus/
│   ├── engine/
│   ├── dictationd/
│   ├── hotkey_portal/
│   └── component/classscribe.xml
├── workers/
│   ├── moss_td/
│   ├── firered/
│   ├── granite/
│   ├── qwen/
│   ├── ark/
│   ├── moss_en/
│   ├── nemotron/
│   ├── pyannote/
│   └── funasr_experimental/
├── benchmarks/
│   ├── runner/
│   ├── normalizers/
│   ├── scorers/
│   └── manifests/
├── packaging/
│   ├── rpm/
│   ├── systemd/
│   └── desktop/
├── scripts/
└── tests/
    ├── unit/
    ├── integration/
    ├── e2e/
    ├── audio_fixtures/
    └── golden/
```

每个 `workers/<name>/` 自带独立：

```text
pyproject.toml
uv.lock
worker.py
adapter.py
healthcheck.py
README.md
```

核心环境不得依赖这些 worker 的具体深度学习库。

---

#### 原蓝图第 10 节：本机目录与数据生命周期

遵循 XDG 目录：

```text
~/.config/classscribe/
  config.yaml
  model-registry.yaml
  profiles.yaml
  api-token

~/.local/share/classscribe/
  classscribe.sqlite3
  jobs/<job-id>/
    source/
    audio_master.wav
    derived/
    candidates/
    exports/
  glossaries/
  benchmarks/

~/.cache/classscribe/
  models/
  resampled/
  waveform-peaks/
  tmp/

~/.local/state/classscribe/
  logs/
  crash-reports/
```

规则：

- 原始上传文件只读保存并计算 SHA-256。
- 统一生成 16 kHz、单声道、PCM S16LE 的 `audio_master.wav`，其 sample index 是全系统 canonical timeline。
- 所有时间内部存储为 `start_sample`、`end_sample`（16 kHz int64），显示和导出时才换算为毫秒。
- 中间片段不各自重置时间；worker 返回局部时间时必须加上请求的绝对 sample offset。
- 可配置自动清理缓存和冗余候选，数据库决策记录及最终导出不得静默删除。

---

#### 原蓝图第 35 节：配置示例

```yaml
server:
  host: 127.0.0.1
  port: 8765
  require_token: true

privacy:
  runtime_offline: true
  save_dictation_audio: false
  log_transcript_text: false

hardware:
  gpu_device: 0
  max_vram_mb: 10500
  one_heavy_worker: true
  ibus_priority: 0

classroom:
  default_language: ja
  default_profile: auto_best
  structure_model: moss_td_0_9b
  structure_window_seconds: 720
  structure_overlap_seconds: 4
  text_segment_target_seconds: 18
  text_segment_max_seconds: 30
  text_overlap_seconds: 1.0
  expected_speakers: auto
  speaker_typical: 2
  speaker_max: 12
  auto_export: [json, markdown, srt, vtt]

quality:
  second_model_threshold: 0.82
  third_model_threshold: 0.62
  reject_repetition: true
  reject_script_mismatch: true
  require_alignment_gate: true
  unresolved_policy: inaudible_marker

ibus:
  enabled: true
  activation: hold
  show_interim: true
  default_language: ja
  default_profile: balanced
  stream_chunk_ms: 560
  semantic_endpoint_silence_ms: 650
  hard_chunk_seconds: 25
  overlap_seconds: 2.0
  rolling_context_segments: 3
  stable_prefix_commit_after_seconds: 45

profiles:
  classroom:
    zh:
      primary: firered_asr2_aed
      fallbacks: [ark_asr_3b, qwen3_asr_1_7b]
      punctuation: firered_punc
    ja:
      primary: granite_speech_4_1_2b
      fallbacks: [qwen3_asr_1_7b, ark_asr_3b]
      punctuation: strict_ja_punc
    en:
      primary: moss_transcribe_preview_2b
      fallbacks: [ark_asr_3b, granite_speech_4_1_2b]
      punctuation: native_then_firered
```

阈值只能作为初始值；运行 benchmark 后由校准结果更新。

---

#### 原蓝图第 36 节：实现依赖顺序（不是里程碑或时间表）

为了避免后续返工，代码依赖关系应按下列方向建立：

```text
协议与 canonical timeline
  ↓
数据库与状态机
  ↓
音频导入/QC/VAD/LID
  ↓
模型注册表与单 worker 契约
  ↓
GPU 调度器
  ↓
MOSS 结构层与 speaker stitching
  ↓
三种语言的单模型正文适配器
  ↓
质量检测、fallback、共识、术语和标点
  ↓
ForcedAligner 与最终时间轴
  ↓
导出
  ↓
WebUI
  ↓
IBus engine、dictationd、portal 快捷键
  ↓
模型管理器、RPM、完整基准与回归
```

并行开发可以进行，但不得跳过协议、时间轴和数据模型直接堆 WebUI；此前的主要错误正来自缺少这些基础约束。

---

---

## 阶段 2：实现核心服务、数据库、持久化状态机、安全、日志与故障恢复

### 阶段目标

完成不依赖真实大模型的业务核心，使任务、片段、候选、决策、词典、模型安装和基准记录都能持久化、审计、恢复并安全地通过本地服务访问。

### 进入条件

- [ ] 阶段 1 的仓库、配置、XDG 路径和 canonical timeline 已通过测试。
- [ ] 数据库迁移和 API 服务可以在不安装模型的环境中运行。

### 实施任务

- [ ] 使用 SQLAlchemy 2、Alembic 和 SQLite WAL 建立完整数据库表、外键、索引、枚举和迁移。
- [ ] 实现 recordings、jobs、speech_regions、language_spans、speaker_spans、transcript_segments、asr_candidates、token_spans、decision_events、glossaries、model_installations、benchmark_runs、dictation_sessions 等完整数据模型。
- [ ] 实现可重入作业状态机、阶段检查点、逐片段事务、取消/暂停/恢复/重试语义和服务重启后的恢复扫描。
- [ ] 实现 transcript_segments.version 乐观锁、自动/人工修改审计、候选软删除或版本链，禁止删除已采用候选。
- [ ] 实现 256-bit 本地 API token、0600 权限、loopback 绑定、SameSite/CSRF 或 Authorization 防护。
- [ ] 实现 UUID 路径、上传解码资源限制、数据根目录规范化验证和 worker 可访问路径白名单。
- [ ] 实现结构化日志字段、默认正文脱敏、错误码体系和脱敏诊断包生成框架。
- [ ] 实现本地诊断数据模型：系统、GPU、FFmpeg、PipeWire、IBus、portal、worker、模型、GPU 租约和最近错误。
- [ ] 实现批处理故障恢复、worker 崩溃局部重试、原子导出写入，以及 IBus Socket/麦克风/GPU 错误的安全回退接口。
- [ ] 实现三级数据删除：只删派生文件、删任务、安全清空全部本地数据；不得静默删除数据库决策和最终导出。

### 阶段交付物

- [ ] 完整 Alembic 初始迁移和数据库模型。
- [ ] 可重入 job/segment 状态机、审计服务和乐观锁。
- [ ] 安全中间件、受限路径模块、结构化日志和诊断 API 骨架。
- [ ] 故障注入测试夹具和恢复策略文档。

### 退出条件

- [ ] 模拟服务重启后，未完成任务从第一个未完成片段继续，不丢状态。
- [ ] 浏览器旧版本编辑会得到明确冲突，而不是覆盖新数据。
- [ ] 任意系统路径、非法文件 ID、无 token 写请求均被拒绝。
- [ ] 日志和诊断包默认不包含完整转录正文、下载凭证或秘密。

### 本阶段完整技术规范

> 以下内容是本阶段必须实现的完整约束。代码、配置、测试和文档不得只实现上面的摘要而忽略本节细节。

#### 原蓝图第 25 节：数据库设计

SQLite 使用 WAL、外键和迁移。

##### 25.1 核心表

###### `recordings`

```text
id, source_name, source_sha256, source_path,
duration_samples, sample_rate, channels,
created_at, audio_qc_json
```

###### `jobs`

```text
id, recording_id, language_mode, profile_id,
status, stage, progress, error_code, error_detail,
created_at, started_at, completed_at
```

###### `speech_regions`

```text
id, job_id, start_sample, end_sample,
vad_score, acoustic_class, source
```

###### `language_spans`

```text
id, job_id, start_sample, end_sample,
language, confidence_raw, decision_json
```

###### `speaker_spans`

```text
id, job_id, start_sample, end_sample,
speaker_global_id, speaker_local_id, overlap,
source_model, confidence
```

###### `transcript_segments`

```text
id, job_id, start_sample, end_sample,
speaker_id, language,
raw_text, faithful_text, smart_corrected_text, user_text,
auto_final_source, quality_score,
timing_quality, review_status, version
```

###### `asr_candidates`

```text
id, segment_id, model_id, model_revision,
raw_text, normalized_text,
confidence_raw, confidence_calibrated,
quality_features_json, warnings_json,
decode_config_json, inference_metrics_json
```

###### `token_spans`

```text
id, candidate_id/null, segment_id,
start_sample, end_sample, token, normalized_token,
confidence, provenance_json
```

###### `decision_events`

```text
id, segment_id, event_type, input_json,
output_json, rule_version, created_at
```

###### `glossaries` / `glossary_terms`

保存 canonical、reading、aliases、language、weight、source 和用户确认状态。

###### `model_installations`

保存 repository、revision、local_path、sha256、依赖环境、显存实测和健康状态。

###### `benchmark_runs` / `benchmark_items`

保存 gold、预测、指标、硬件、参数和模型排名。

###### `dictation_sessions`

仅保存用户允许保留的听写会话；默认不永久保存原始麦克风音频。

##### 25.2 乐观锁与审计

- `transcript_segments.version` 防止浏览器旧页面覆盖新编辑。
- 每次自动或人工修改写入 `decision_events`。
- 数据库不直接删除已采用候选；使用软删除或版本链。

---

#### 原蓝图第 30 节：安全与隐私

- API 绑定 loopback；生成 256-bit 随机 token，权限 `0600`。
- WebUI 由同一后端提供，启用 SameSite Cookie/CSRF 或 Authorization token。
- 上传文件名不用于文件系统路径；使用 UUID。
- 对上传大小、时长、格式和解码资源设置限制，防止压缩炸弹。
- worker 仅接收位于批准数据根目录内的规范化路径。
- 日志默认不写完整转录文本，不写 Hugging Face token。
- 不包含遥测和崩溃自动上传。
- 麦克风录音默认只在内存/临时文件中存在，commit 后清理；用户可显式开启听写历史。
- 模型下载凭证放入系统 keyring 或权限受限环境文件，不写进仓库和普通配置。
- 数据删除提供“只删派生文件”“删任务”“安全清空全部本地数据”三种级别。

---

#### 原蓝图第 31 节：日志、诊断与可观测性

##### 31.1 结构化日志

字段：

```text
timestamp, level, service, request_id, job_id, segment_id,
model_id, stage, duration_ms, vram_mb, error_code
```

不默认包含原始正文。

##### 31.2 本地诊断页

显示：

- Fedora、内核、桌面、Wayland/X11。
- GPU、驱动、显存、CUDA runtime。
- FFmpeg、PipeWire、IBus、portal 后端。
- 各 worker 环境、模型版本和健康检查。
- 当前 GPU 租约和队列。
- 最近错误和可导出的脱敏诊断包。

##### 31.3 指标

- 每阶段耗时。
- worker 加载/卸载次数。
- OOM、重复拦截、fallback 次数。
- 自动采用率、低置信度比例。
- IBus p50/p95 延迟。

---

#### 原蓝图第 32 节：故障恢复

##### 32.1 批处理

- 每个片段独立事务提交。
- 作业状态机可重入；重启后从第一个未完成片段继续。
- worker 崩溃只重试当前片段。
- 每个参数组合限制重试次数。
- 导出使用临时文件后原子 rename。

##### 32.2 IBus

- dictationd 不可用时，IBus 引擎显示错误并将普通按键原样交给应用。
- 麦克风断开时结束当前 preedit，不提交垃圾文本。
- GPU OOM 时回退小模型或 CPU 备用，并显示状态。
- Socket 断开后当前 preedit 可取消，不让半句静默提交。

##### 32.3 时间轴异常

- 候选时间不合法时整份候选标记无效。
- 对齐失败使用结构粗时间，不伪造精细词时间。
- 相邻窗口重叠无法去重时保留两候选并标记冲突，不静默丢内容。

---

---

## 阶段 3：建立 Fedora 运行环境、RPM/systemd 骨架与可验证的离线模型安装机制

### 阶段目标

把系统依赖、普通用户服务、RPM 安装位置、模型 revision 固定、完整性校验、远程代码隔离和运行期离线约束做成可重复安装的基础设施。

### 进入条件

- [ ] 阶段 2 的 core 服务可在无模型环境启动。
- [ ] 已有 model_installations 表和受限数据目录。

### 实施任务

- [ ] 实现只检查并报告的 Fedora 依赖检测器，覆盖 NVIDIA 驱动、nvidia-smi、FFmpeg、PipeWire、WirePlumber、GStreamer、IBus、PyGObject、编译工具、uv、Node LTS 和 pnpm。
- [ ] 为每个 worker 设计独立 uv 环境和锁文件，分别验证 torch.cuda.is_available()，不要求完整 CUDA Toolkit。
- [ ] 建立 RPM spec、desktop 文件、图标、IBus component XML 和三个 systemd user service 的安装骨架。
- [ ] systemd 服务全部以普通用户运行，Restart=on-failure 并设置重启限速；保持 SELinux enforcing。
- [ ] 实现模型管理器：用户主动下载、临时目录、完整性校验、固定 revision、原子移动、磁盘/下载量展示、依赖环境记录和短音频健康检查。
- [ ] 为 trust_remote_code 模型固定提交 SHA、保存代码快照与文件哈希，升级时展示 diff 或变更文件列表。
- [ ] 在模型安装完成后启用 HF_HUB_OFFLINE=1 与 TRANSFORMERS_OFFLINE=1；任务期缺失文件必须明确报错，禁止隐式联网。
- [ ] 把凭证存入系统 keyring 或权限受限环境文件，禁止进入仓库和普通配置。
- [ ] 实现桌面入口：启动 core、等待健康检查、打开 127.0.0.1:8765。
- [ ] 建立基础 RPM 安装/卸载/升级测试，并验证模型和用户数据不进入 RPM。

### 阶段交付物

- [ ] Fedora 依赖检查器、RPM spec 初版、systemd user service 和 desktop/IBus 安装骨架。
- [ ] 带 revision/hash/健康检查/原子安装/删除/回滚能力的模型管理后端。
- [ ] 离线运行环境变量、远程代码快照策略和供应链审计记录格式。

### 退出条件

- [ ] 基础 RPM 可在测试机安装，core 仅监听 127.0.0.1:8765，服务以普通用户运行。
- [ ] 模型未完整安装时不会联网补文件，而是返回明确错误。
- [ ] 模型下载、校验、原子安装、健康检查、删除和回滚均可被自动测试。
- [ ] SELinux 无需关闭，用户数据和模型目录不会被 RPM 覆盖。

### 本阶段完整技术规范

> 以下内容是本阶段必须实现的完整约束。代码、配置、测试和文档不得只实现上面的摘要而忽略本节细节。

#### 原蓝图第 28 节：Fedora 环境与安装

##### 28.1 系统依赖

安装器检查而不是盲目修改系统：

- NVIDIA 驱动和 `nvidia-smi`。
- FFmpeg/FFprobe；常见专有编解码可通过 RPM Fusion 的完整 FFmpeg 获得。
- PipeWire、WirePlumber、GStreamer 及常用插件。
- IBus、PyGObject、GTK/GLib introspection。
- gcc/g++、make、cmake、pkg-config、git。
- `uv`、当前 Node LTS、pnpm。

不要求安装完整 CUDA Toolkit；优先使用与驱动兼容的 PyTorch/vLLM/NeMo wheel。每个 worker 独立验证 `torch.cuda.is_available()`。

##### 28.2 安装产物

RPM 应安装：

```text
/usr/libexec/ibus-engine-classscribe
/usr/share/ibus/component/classscribe.xml
/usr/lib/systemd/user/classscribe-core.service
/usr/lib/systemd/user/classscribe-dictationd.service
/usr/lib/systemd/user/classscribe-hotkey.service
/usr/share/applications/classscribe.desktop
/usr/share/icons/hicolor/.../classscribe.*
```

模型和用户数据不放进 RPM。

##### 28.3 systemd user services

- 默认只启动 core；dictationd 可按 IBus 激活或登录预热。
- `Restart=on-failure`，设置合理重启限速。
- 保持 SELinux enforcing；不要要求用户关闭 SELinux。
- 服务仅以普通用户运行。

##### 28.4 Web 服务

```text
listen: 127.0.0.1
port: 8765
```

桌面入口启动服务、等待健康检查、打开浏览器。

---

#### 原蓝图第 29 节：模型安装、固定版本与离线运行

##### 29.1 模型管理器

- 用户主动点击安装。
- 下载到临时目录，校验完整性后原子移动。
- 固定 Hugging Face/ModelScope revision，不运行时自动拉取 `main`。
- 显示预计磁盘、下载量和依赖环境。
- 安装后运行真实短音频健康检查，不只检查文件存在。

##### 29.2 远程模型代码

MOSS、ARK 等模型可能使用 `trust_remote_code=True`。处理方式：

- 固定提交 SHA。
- 将所需代码快照保存在模型目录。
- 记录文件哈希。
- 升级前展示 diff 或至少列出变更文件。
- worker 不允许访问数据根目录之外的任意路径。

##### 29.3 离线模式

模型安装完成后，运行服务设置：

```text
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

禁止 worker 在任务中隐式下载新文件。缺失文件时明确报“模型未完整安装”。

---

---

## 阶段 4：实现音频导入、质量分析、VAD、LID、切片与边界基础设施

### 阶段目标

把任意常见课堂音视频可靠转换为统一 master timeline，并生成可追溯的语音区间、结构窗口、正文句段和语言 span，为后续所有模型提供一致输入。

### 进入条件

- [ ] 阶段 2 的 recordings/jobs/speech_regions/language_spans 可持久化。
- [ ] 阶段 3 的 FFmpeg、音频库和 worker 安装基础可用。

### 实施任务

- [ ] 支持 WAV、FLAC、MP3、M4A/AAC、OGG、Opus、MP4、MOV、MKV、WebM，并保存只读原始文件及 SHA-256。
- [ ] 用 FFmpeg 生成 16 kHz、单声道、PCM S16LE 的 audio_master.wav，所有片段继续使用绝对 sample offset。
- [ ] 实现多声道 RMS、削波、语音占比分析，支持最佳通道或等权下混；不默认做降噪、回声消除或激进响度归一化。
- [ ] 实现完整音频质量报告：时长、编码、峰值、RMS/LUFS、削波、DC offset、静音/语音、粗略 SNR、背景音乐概率和异常中断。
- [ ] 实现 FireRedVAD 默认适配器以及 Silero/WebRTC 回退接口，支持流式与非流式。
- [ ] 明确区分“结构窗口”和“正文句段”两套数据类型，禁止在代码中复用一个含糊 segment 类型。
- [ ] 实现 12 分钟默认结构窗口、4 秒重叠、4 分钟最小窗口，以及 20/30/60/90 分钟自适应候选。
- [ ] 实现 8～30 秒正文句段，按说话人变化、300～800 ms 停顿、句末韵律/粗标点和语言切换点切分。
- [ ] 无自然停顿时按 hard max 切分，两侧保留 0.8～1.5 秒重叠，并建立基于时间/文本对齐的去重原语。
- [ ] 实现手动语言模式完全绕过全局猜测；自动/混合模式使用 5 秒窗口、2.5 秒步长、连续两窗 p>=0.80 的滞回。
- [ ] 保存每个 language span 的原始概率与决策，防止英文单词、缩写、日语外来语引发过度切换。

### 阶段交付物

- [ ] 音视频导入器、FFprobe/FFmpeg 标准化流水线和 audio QC 报告。
- [ ] VAD/LID 接口、speech_regions/language_spans 持久化和结构/正文切片器。
- [ ] 边界重叠、绝对 sample offset、去重和语言滞回的单元/属性测试。

### 退出条件

- [ ] 90 分钟输入可稳定生成 master、QC、VAD、LID 和切片，并可重启续跑。
- [ ] 任何输出区间均在音频范围内、单调且不因子片段重置时间。
- [ ] 无重叠硬切被测试禁止；长连续语音覆盖不丢失。
- [ ] 手动语言不被 LID 改写，自动/混合不会因单个术语频繁切换。

### 本阶段完整技术规范

> 以下内容是本阶段必须实现的完整约束。代码、配置、测试和文档不得只实现上面的摘要而忽略本节细节。

#### 原蓝图第 13 节：音频导入与质量分析

##### 13.1 支持格式

通过 FFmpeg 支持常见音视频：WAV、FLAC、MP3、M4A/AAC、OGG、Opus、MP4、MOV、MKV、WebM。

##### 13.2 标准化

```bash
ffmpeg -i input \
  -map 0:a:0 \
  -ac 1 -ar 16000 \
  -c:a pcm_s16le \
  audio_master.wav
```

- 不默认做降噪、回声消除或激进响度归一化，因为这些处理可能损坏辅音和专有名词线索。
- 保留原始文件；增强音轨只作为可选派生版本，并参与 A/B 基准后才能作为默认。
- 多声道录音先分析各通道 RMS、削波和语音占比，允许选择最佳通道或等权下混。

##### 13.3 自动质量报告

至少记录：

- 时长、采样率、声道、编码。
- 峰值、RMS/LUFS、削波比例、DC offset。
- 静音比例、语音比例。
- 粗略 SNR、背景音乐概率、异常中断。
- 输入音频与 master 的 SHA-256。

质量报告只用于警告和路由，不擅自拒绝清晰录音。

---

#### 原蓝图第 14 节：VAD、切片与边界处理

##### 14.1 默认 VAD

FireRedVAD 作为初始默认，原因是它官方支持流式/非流式和 100+ 语言；Silero/WebRTC 仅作为回退或回归对照。

##### 14.2 两类窗口

1. **结构窗口**：交给 MOSS-TD，用于全局说话人和粗时间轴。
2. **正文句段**：交给语言专用 ASR，用于高精度文本。

不能把二者混为一个 `segment` 概念。

##### 14.3 90 分钟结构窗口

MOSS 官方声明可单次处理到 90 分钟，但 RTX 4070 的实际长序列显存必须实测。初始工程决策：

```text
默认结构窗口：12 分钟
相邻重叠：4 秒
最小窗口：4 分钟
显存充足时可自适应增加：20、30、60、90 分钟
```

这是本项目的安全策略，不是对模型官方上限的修改。

跨窗口处理：

- 用重叠区文本和说话人 embedding 对齐窗口。
- 重叠区只保留质量较高的一份，绝不简单拼接造成重复。
- 说话人标签通过 embedding 匹配延续，例如窗口二的 `S01` 可以映射为全局 `S02`。

##### 14.4 正文句段

目标长度：8～30 秒；优先在以下位置切分：

1. 说话人变化。
2. 300～800 ms 自然停顿。
3. 句末韵律和已有粗标点。
4. 语言切换点。

若连续语音没有自然停顿：

- 达到 hard max 后切分。
- 两侧保留 0.8～1.5 秒音频重叠。
- 使用时间戳和文本对齐去重，而不是删除固定字符数。

禁止无重叠硬切导致词被截断。

---

#### 原蓝图第 15 节：语言选择与混合语言路由

##### 15.1 手动模式

- 中文、日语、英语模式完全绕过全局语言猜测。
- 向支持语言提示的模型明确传入 `zh`、`Japanese`、`English`。
- 仍允许句内常见外来词和中英混说；不得因单个英文技术词立刻切换整段语言。

##### 15.2 自动/混合模式

使用 FireRedLID 做 3～8 秒滑动窗口识别，采用滞回机制：

```text
候选语言需要连续多个窗口超过阈值才正式切换；
低于阈值时维持上一语言；
单个英文词、缩写或日语外来语不单独触发路由切换。
```

建议初始参数：

- 窗口 5 秒，步长 2.5 秒。
- 两个连续窗口都达到 `p >= 0.80` 才切换。
- 语言切换边界回看最近的静音点。
- 中文与英语紧密 code-switch 时优先 FireRedASR2-AED 或 Qwen 统一识别，而不是拆得过碎。
- 日英混合优先 Qwen、Granite 或 MOSS，避免把英文术语当成独立英语句段。

保存每个 language span 的原始概率与决策过程。

---

---

## 阶段 5：实现模型候选池、版本化注册表、Worker RPC 与单 GPU 调度器

### 阶段目标

把所有候选模型变成可安装、可替换、可测量、可隔离运行的 worker，并在 12 GB 单 GPU 上实现有优先级、可抢占、可恢复的有序推理。

### 进入条件

- [ ] 阶段 1 的 protocol 骨架、阶段 2 的模型安装记录、阶段 3 的独立 worker 环境、阶段 4 的标准音频输入已经可用。
- [ ] 至少准备一段中/日/英短音频和一段静音作为 worker 健康测试。

### 实施任务

- [ ] 把完整模型能力矩阵和初始自动最佳配置录入 versioned model registry，模型名、仓库、revision、语言、任务、模式、采样率、dtype、后端、安全窗口、显存、已知缺陷都不得写死在业务逻辑。
- [ ] 实现注册表新增、禁用、升级、回滚、按语言/场景排序，以及安装状态、校验哈希、依赖锁、基准结果和禁用原因。
- [ ] 实现 MessagePack RPC over Unix Domain Socket；批处理传只读路径和 sample 范围，实时传 16 kHz 单声道 PCM 小帧并加 4 字节长度前缀。
- [ ] 实现 health、capabilities、load、unload、transcribe_batch、stream_open/push/flush/close、align、vad、lid、diarize、cancel 全套标准方法。
- [ ] 所有请求携带 request_id、job_id、deadline_ms、priority、协议版本；所有响应携带固定模型 revision、原始/规范化文本、绝对时间、性能指标和 warnings。
- [ ] 建立每个 workers/<name>/ 的 pyproject.toml、uv.lock、worker.py、adapter.py、healthcheck.py、README.md 模板和契约测试。
- [ ] 实现 GPULeaseManager 的 0/10/20/30/40 优先级，课堂按句段边界可抢占，IBus 开始时停止派发新课堂 GPU 片段。
- [ ] 实现 fast/balanced/accuracy 常驻策略、同模型复用、顺序加载课堂候选、NVML 显存/峰值/OOM 记录和安全余量。
- [ ] 实现有限 OOM 恢复链：清理、batch=1、缩窗、低内存 attention、dtype/量化、小模型、记录失败；禁止同参数无限重试。
- [ ] 明确原始置信度不可跨模型直接比较，只保存 raw 值；统一 quality_probability 等待阶段 12 在本机 gold 上校准。
- [ ] 为 MOSS、FireRed、Granite、Qwen、ARK、MOSS EN、Nemotron、pyannote、FunASR experimental 建立独立 worker 项目，即使某些模型默认关闭也要有注册和禁用理由。

### 阶段交付物

- [ ] 完整 model-registry.yaml schema、初始候选数据和管理 API。
- [ ] 版本化 Worker RPC 实现、Python 客户端/服务端库和全 worker 契约套件。
- [ ] GPULeaseManager、NVML 监控、常驻策略与 OOM 有限恢复。
- [ ] 至少一个真实模型 worker 的端到端 load→transcribe→unload 验证。

### 退出条件

- [ ] 业务代码中不存在按语言直接 load 具体模型的硬编码分支。
- [ ] worker 可独立崩溃、取消、超时和卸载，核心服务不崩溃且显存可释放。
- [ ] IBus 优先级模拟能够在课堂片段边界抢占并恢复。
- [ ] 模型能力、revision、显存、安全窗口和已知缺陷均可由注册表查询。

### 本阶段完整技术规范

> 以下内容是本阶段必须实现的完整约束。代码、配置、测试和文档不得只实现上面的摘要而忽略本节细节。

#### 原蓝图第 6 节：模型事实与候选池

下面是**初始候选池**，不是永远固定的冠军名单。模型注册表必须允许新增、禁用、升级、回滚和按语言重新排序。

##### 6.1 能力矩阵

| 模型 | 主要语言 | 建议用途 | 原生流式 | 原生时间/说话人 | 标点/大小写 | 热词/上下文 | RTX 4070 定位 |
|---|---|---|---|---|---|---|---|
| MOSS-Transcribe-Diarize 0.9B | 中/日/英及更多 | 课堂结构、说话人、粗时间轴、结构候选 | 否 | 片段时间戳＋说话人 | 随模型输出，需质检 | 支持 | 默认结构模型；90 分钟单次是官方能力声明，本机仍采用自适应窗口 |
| FireRedASR2-AED | 中文、英语、中英混说 | 中文课堂正文；中文/英文高质量复核 | 非核心用途 | 词级时间戳＋置信度 | 配套 FireRedPunc 支持中英 | 无统一热词承诺，外部词典补偿 | 1B+，适合作为中文默认候选 |
| FireRedASR2-LLM | 中文、英语、中英混说 | 中文极致实验模式 | 否 | 不作为主时间轴 | 外部标点 | — | 8B+，12 GB 显存不作为稳定默认 |
| Granite Speech 4.1 2B | 日语、英语及部分欧洲语言 | 日语正文、标点、关键词偏置 | 否 | 基础版不作为主时间轴 | 支持标点/truecasing 提示 | 支持关键词列表 | 日语第一优先实测对象 |
| Qwen3-ASR 1.7B | 中/日/英及 52 种语言/方言 | 三语统一高精度候选、短段复核、IBus 平衡模式 | 支持 | 流式无时间戳；可配 ForcedAligner | 模型输出，需质检 | 支持上下文 | 适合 12 GB；明确指定语言 |
| Qwen3-ASR 0.6B | 中/日/英及更多 | IBus 低延迟、快速候选 | 支持 | 同上 | 模型输出 | 支持上下文 | 流式速度优先 |
| Qwen3-ForcedAligner 0.6B | 中/日/英等 11 种语言 | 最终文字的词/字符时间对齐 | 不适用 | 最长约 5 分钟文本-语音对齐 | 不修改文字 | 不适用 | 仅对短片段且质量门控通过后使用 |
| ARK-ASR-3B | 中/日/英等 19 种语言 | 三语高精度候选，尤其英语/中文 | 否 | 无可靠原生时间轴依赖 | 需单独评估 | 提示能力有限 | 3B，需测显存和短段长度 |
| MOSS-Transcribe-preview-2B | 英语 | 英语课堂正文候选 | 否 | 无主时间轴 | 需单独标点层 | 无核心热词能力 | 约 2.4B；英语初始高精度候选 |
| Nemotron 3.5 ASR Streaming 0.6B | 中/日/英等 40 locale | IBus 三语流式、临时文本 | 是，缓存式 | 不依赖其作为课堂主时间轴 | 原生标点/大小写 | 显式语言或自动识别 | 真流式、低延迟，适合常驻 |
| Nemotron Speech Streaming EN 0.6B | 英语 | 英语 IBus 默认流式候选 | 是，缓存式 | 不作为课堂主时间轴 | 原生标点/大小写 | — | 英语低延迟优先 |
| Granite Speech 5.0 TurboCTC 470M | 英语 | 英语超快模式、CPU/GPU 备用 | 可做增量分块 | CTC 可扩展对齐 | 需外部标点检查 | — | 很轻，精度模式不默认 |
| Voxtral Mini 4B Realtime 2602 | 中/日/英等 13 种语言 | 实验性统一流式 | 是 | 不作为课堂主结构 | 模型输出 | — | 官方 BF16 服务要求至少 16 GB，12 GB 仅允许量化实验 |
| VibeVoice-ASR-Streaming 1.5B | 中/日/英等 10 种语言 | 实验性流式“谁说了什么” | 是 | 流式说话人归属 | 模型输出 | 支持热词 | 模型卡显示约 3B 参数；发布极新，默认关闭 |
| pyannote Community-1 | 语言无关 | MOSS 失败或结构异常时的说话人回退 | 否 | 说话人分离 | 不适用 | 不适用 | CPU/GPU 回退；支持 exclusive diarization |
| Fun-ASR-Nano | 中/日/英 | 专家测试、热词对照 | 支持部分场景 | 视检查点而定 | 用户实测日语标点较好 | 支持热词 | 默认关闭；必须启用循环输出检测 |

##### 6.2 初始“自动最佳”配置

稳定默认最终由本机基准自动重写。首次安装时采用以下 bootstrap 配置：

###### 课堂结构层

```text
主结构：MOSS-Transcribe-Diarize 0.9B
回退结构：FireRedVAD + pyannote Community-1
最终精细对齐：Qwen3-ForcedAligner 0.6B（只对通过门控的短段）
```

###### 中文课堂正文

```text
主候选：FireRedASR2-AED
第二候选：ARK-ASR-3B
统一备用：Qwen3-ASR-1.7B
结构候选：MOSS-TD 自身文本
标点：FireRedPunc
```

FireRed 官方仅把中文、英语和中英混说列为 FireRedASR2 的 ASR 支持范围，因此不得用于日语正文。

###### 日语课堂正文

```text
主候选：Granite Speech 4.1 2B
第二候选：Qwen3-ASR-1.7B（强制 Japanese）
第三候选：ARK-ASR-3B
结构候选：MOSS-TD 自身文本
实验候选：Qwen3-ASR-1.7B-JA
专家候选：Fun-ASR-Nano（默认关闭）
```

日语主模型必须由用户录音基准在 Granite、Qwen、ARK 之间决胜。初始把 Granite 放在前面，是因为其官方能力直接包括日语、标点和关键词偏置，而不是因为已有独立证据证明它在所有日语课堂上绝对第一。

###### 英语课堂正文

```text
主候选：MOSS-Transcribe-preview-2B
第二候选：ARK-ASR-3B
术语强化候选：Granite Speech 4.1 2B
统一备用：Qwen3-ASR-1.7B
标点/大小写：优先模型原生结果；必要时 FireRedPunc
```

###### IBus

| 语言 | 快速模式 | 平衡模式 | 最高精度确认 |
|---|---|---|---|
| 中文 | Nemotron 3.5 0.6B 或 Qwen3 0.6B | Qwen3 1.7B | FireRedASR2-AED 对稳定句段重算 |
| 日语 | Nemotron 3.5 0.6B | Qwen3 1.7B | Granite 4.1 2B 或 Qwen3 1.7B 本机优胜者重算 |
| 英语 | Nemotron EN 0.6B | Nemotron EN 0.6B / Qwen3 1.7B | MOSS Preview 2B 或 ARK 3B 本机优胜者重算 |
| 自动/混合 | Nemotron 3.5 0.6B | Qwen3 1.7B | 根据稳定 LID 结果逐句路由 |

最高精度确认模型只有在显存预检允许常驻或用户接受较长松键等待时启用。否则使用同一常驻流式模型进行最终重解码，避免每次听写都热切换大型模型。

---

#### 原蓝图第 7 节：模型注册表

业务代码不得通过 `if language == "ja": load_xxx` 写死模型。所有模型进入版本化注册表。

示例：

```yaml
schema_version: 1
models:
  - id: moss_td_0_9b
    display_name: MOSS Transcribe-Diarize 0.9B
    provider: OpenMOSS-Team
    repository: OpenMOSS-Team/MOSS-Transcribe-Diarize
    revision: "<固定提交 SHA>"
    worker: moss_td
    languages: [zh, ja, en, auto]
    tasks: [asr, timestamps, diarization, hotwords, events]
    modes: [batch]
    trust_remote_code: true
    dtype: bfloat16
    safe_operating_window_seconds: 720
    vendor_claimed_max_seconds: 5400
    vram_class: medium
    enabled_by_default: true

  - id: firered_asr2_aed
    display_name: FireRedASR2-AED
    provider: FireRedTeam
    repository: FireRedTeam/FireRedASR2-AED
    revision: "<固定提交 SHA>"
    worker: firered
    languages: [zh, en, zh_en]
    tasks: [asr, word_timestamps, confidence]
    modes: [batch, final_decode]
    input_sample_rate: 16000
    safe_operating_window_seconds: 30
    vram_class: medium

  - id: qwen3_asr_1_7b
    display_name: Qwen3-ASR 1.7B
    provider: Qwen
    repository: Qwen/Qwen3-ASR-1.7B-hf
    revision: "<固定提交 SHA>"
    worker: qwen
    languages: [zh, ja, en, auto]
    tasks: [asr, lid, context]
    modes: [batch, streaming, final_decode]
    stream_backend: vllm
    stream_timestamps: false
    safe_operating_window_seconds: 120
    vram_class: medium
```

每条注册信息必须包含：

- 模型仓库与固定 revision。
- 支持语言、任务、运行模式。
- 输入采样率、推荐 dtype、推理后端。
- 本项目实测的安全片段长度，而不是只记录厂商上限。
- 模型是否返回词时间、片段时间、置信度、logprobs、标点、说话人。
- 预估与实测显存。
- 安装状态、校验哈希、依赖锁文件。
- 本机各基准集上的结果和推荐排名。
- 已知缺陷和禁用原因。

---

#### 原蓝图第 8 节：Worker 协议

##### 8.1 传输方式

- 控制与小数据：MessagePack RPC over Unix Domain Socket。
- 批处理音频：传递受限数据目录内的只读文件路径和起止 sample，不复制大块音频。
- 实时音频：通过同一 Socket 发送 16 kHz 单声道 PCM 小帧；每条消息使用 4 字节长度前缀。
- 所有请求带 `request_id`、`job_id`、`deadline_ms`、`priority` 和协议版本。

##### 8.2 标准方法

```text
health
capabilities
load
unload
transcribe_batch
stream_open
stream_push
stream_flush
stream_close
align
vad
lid
diarize
cancel
```

##### 8.3 标准 ASR 请求

```json
{
  "audio_path": "/home/user/.local/share/classscribe/jobs/.../audio_master.wav",
  "start_sample": 1920000,
  "end_sample": 2320000,
  "sample_rate": 16000,
  "language": "ja",
  "hotwords": ["沖縄科学技術大学院大学", "ヤマトタチバナ"],
  "context_before": "前の確定文...",
  "decode": {
    "temperature": 0.0,
    "beam_size": 1,
    "max_tokens": 512
  }
}
```

##### 8.4 标准响应

```json
{
  "model_id": "qwen3_asr_1_7b",
  "model_revision": "...",
  "language": "ja",
  "raw_text": "...",
  "normalized_text": "...",
  "segments": [
    {
      "start_sample": 1924000,
      "end_sample": 2080000,
      "text": "...",
      "confidence_raw": null,
      "words": []
    }
  ],
  "metrics": {
    "inference_ms": 841,
    "peak_vram_mb": 5240,
    "rtf": 0.34
  },
  "warnings": []
}
```

原始置信度不得跨模型直接比较。系统必须在本机 gold 数据上对每个模型、语言和场景分别校准后，才产生统一的 `quality_probability`。

---

#### 原蓝图第 11 节：GPU 调度与显存策略

##### 11.1 单 GPU 租约

实现 `GPULeaseManager`：

```text
优先级 0：IBus 正在录音/确认
优先级 10：用户在 WebUI 主动试听或重跑某句
优先级 20：课堂主转写
优先级 30：备用模型复核
优先级 40：后台基准或预下载验证
```

- 课堂任务以句段为最小可抢占单元；每处理完一个片段检查是否有 IBus 请求。
- IBus 开始时停止派发新的课堂 GPU 片段。
- 显存不足时卸载课堂模型，加载听写模型；听写结束并空闲一段时间后恢复课堂任务。
- 同一模型同时适用于课堂和 IBus 时复用 worker，避免无意义热切换。
- 使用 NVML 记录当前显存、峰值和 OOM 历史；为 CUDA 上下文保留安全余量，不把 12 GB 全部分配给权重。

##### 11.2 模型常驻策略

- `fast`：只常驻一个流式小模型。
- `balanced`：常驻一个可同时做流式和最终解码的模型，例如 Qwen3-ASR 1.7B。
- `accuracy`：若显存预检通过，常驻流式模型和语言专用最终模型；否则松键时热切换，并在界面显示预计等待。
- 课堂多模型不并行驻留；顺序加载、批量处理目标片段、退出释放显存。

##### 11.3 OOM 自动恢复

按顺序执行：

1. 清理已退出 worker 和 CUDA cache。
2. 减小批量到 1。
3. 缩短当前音频窗口。
4. 使用 SDPA/Flash Attention 支持的低内存实现。
5. 切换 BF16/FP16 或已验证量化版本。
6. 切换到较小候选模型。
7. 记录失败，不丢整个任务。

禁止捕获 OOM 后无限重试同一参数。

---

---

## 阶段 6：实现课堂结构转录、长窗口拼接与说话人归属

### 阶段目标

用 MOSS-TD 建立“谁在何时说话”的全局结构基线，并在 90 分钟录音上可靠处理窗口重叠、speaker stitching、回退 diarization 和真实重叠语音标记。

### 进入条件

- [ ] 阶段 4 已生成结构窗口和正文候选边界。
- [ ] 阶段 5 的 MOSS-TD 与 pyannote worker、GPU 调度和注册表可用。

### 实施任务

- [ ] 实现 MOSS-TD 结构请求和响应适配，保存 start_sample/end_sample、speaker_local、结构文本和 acoustic_events。
- [ ] 把 MOSS 文本仅作为粗时间轴、初始说话人、共识候选和短段边界参考，禁止无条件当作最终正文。
- [ ] 实现 expected_speakers=auto、prior_min=1、prior_typical=2、max_speakers=12、allow_overlap=true 配置。
- [ ] 普通课堂允许“通常 1～2 人”约束，小组讨论允许 auto 或大致人数。
- [ ] MOSS 结构异常时运行 pyannote Community-1；优先 exclusive diarization，真实重叠标 overlap=true。
- [ ] 为每个局部说话人提取多个高质量、无重叠 embedding，维护全局 centroid，并综合余弦距离、重叠区同人约束和时间相邻约束。
- [ ] 匹配不可靠时创建新 speaker，禁止为了标签数好看而强行合并。
- [ ] 实现跨结构窗口重叠文本和说话人去重，质量较高者保留，不能简单拼接。
- [ ] 支持 WebUI 以后将 SPEAKER_01 改名为“老师”，但不改原始 diarization；默认不做跨课程永久身份识别。

### 阶段交付物

- [ ] MOSS-TD 结构 worker 生产适配、speaker_spans 持久化和结构异常判定。
- [ ] pyannote 回退、exclusive/overlap 映射和 speaker stitching 算法。
- [ ] 跨窗口说话人/文本重叠测试夹具与可视化诊断数据。

### 退出条件

- [ ] 90 分钟结构处理可按 12 分钟窗口完成，重叠区无重复文本。
- [ ] 跨窗口 speaker ID switch 可测量，低可靠匹配不会被强制合并。
- [ ] MOSS 或 pyannote 失败只影响对应窗口/片段，可重试或保留粗结构。
- [ ] 每个最终结构片段都拥有可追溯来源和绝对 sample 区间。

### 本阶段完整技术规范

> 以下内容是本阶段必须实现的完整约束。代码、配置、测试和文档不得只实现上面的摘要而忽略本节细节。

#### 原蓝图第 16 节：结构转录与说话人

##### 16.1 MOSS-TD 的角色

MOSS-TD 输出以下结构基线：

```json
{
  "start_sample": 120000,
  "end_sample": 248000,
  "speaker_local": "S01",
  "text": "...",
  "acoustic_events": []
}
```

它的文本不是无条件最终正文，而是：

- 粗时间轴来源。
- 初始说话人来源。
- 一个可参与共识的 ASR 候选。
- 后续短段提取的边界参考。

##### 16.2 说话人策略

默认配置：

```text
expected_speakers: auto
prior_min: 1
prior_typical: 2
max_speakers: 12
allow_overlap: true
```

- 普通课堂可让用户选择“通常 1～2 人”，以抑制过度分裂。
- 小组讨论切换为 `auto` 或设定大致人数。
- MOSS 结构异常时运行 pyannote Community-1。
- 因重叠语音基本不存在，最终映射优先使用 exclusive diarization；检测到真实重叠时给片段加 `overlap=true`，不强行伪造唯一说话人。
- WebUI 允许把 `SPEAKER_01` 改为“老师”，此映射不改变原始 diarization 数据。

##### 16.3 跨窗口 speaker stitching

- 为每个局部说话人抽取多个高质量无重叠语音 embedding。
- 计算与全局 speaker centroid 的余弦距离。
- 加入重叠区同一说话人约束和时间相邻约束。
- 距离不够可靠时创建新 speaker，不强行合并。
- 用户改名后，后续任务可选用本地声纹模板，但默认不建立跨课程永久身份识别。

---

---

## 阶段 7：实现中文、日语、英语的语言专用正文识别适配器

### 阶段目标

按照完整候选优先级实现三种语言的单模型正文识别和统一规范，使每个自然句段都可得到可追溯候选，并为后续自动复核准备模型多样性。

### 进入条件

- [ ] 阶段 5 的模型注册和 Worker RPC 已稳定，相关模型可以按需安装。
- [ ] 阶段 6 的结构片段、说话人和自然正文句段已经存在。

### 实施任务

- [ ] 中文实现 FireRedASR2-AED 主模型、ARK 第二候选、Qwen 统一备用和 MOSS 结构候选；保存词/字符时间戳与原始置信度。
- [ ] 中文中英混说保持英文原样，繁体仅通过 OpenCC 在忠实简体层之后生成独立显示/导出变体。
- [ ] 日语实现 Granite 4.1 2B 主候选，按官方建议使用英语任务提示并附课程关键词；Qwen3 1.7B 强制 Japanese，ARK 为第三候选。
- [ ] 实现 Qwen3-ASR-1.7B-JA 实验候选和 Fun-ASR-Nano 专家候选，后者默认关闭并强制通过循环检测。
- [ ] 英语实现 MOSS Preview 2B 主候选、ARK 第二候选、Granite 术语强化、Qwen 统一备用。
- [ ] 把课程术语、人物、地名、组织、植物、药物、公式读法等读音信息传入支持的模型，但不允许热词无声学证据插入。
- [ ] 所有生成式模型默认 temperature=0、do_sample=false，记录完整 decode 参数和随机种子。
- [ ] 按音频时长限制输出 token 数；默认 batch size 1，不把不同长度音频混批，任何吞吐优化都必须经过重复/漏句回归。
- [ ] 手动语言模式明确传入 zh/Japanese/English；FireRedASR2 禁止用于日语正文。

### 阶段交付物

- [ ] 三语主模型和全部规定候选的 worker 适配器、配置 profile 和候选持久化。
- [ ] 统一 ASR 请求构造、decode 记录、语言强制和 OpenCC 变体模块。
- [ ] 每语言至少一组真实句段的端到端识别测试。

### 退出条件

- [ ] 中文、日语、英语手动模式均可在自然句段上生成候选、时间与性能指标。
- [ ] 模型语言支持范围被注册表和运行时共同验证，FireRed 不会被错误路由到日语。
- [ ] 所有候选包含 model_id、revision、decode、绝对音频区间和 warnings。
- [ ] 长输出、随机采样和异长混批造成的循环风险已被默认配置阻断。

### 本阶段完整技术规范

> 以下内容是本阶段必须实现的完整约束。代码、配置、测试和文档不得只实现上面的摘要而忽略本节细节。

#### 原蓝图第 17 节：语言专用正文识别

##### 17.1 中文

- 主模型 FireRedASR2-AED，每个自然句段运行一次。
- 获取原始文本、字符/词时间戳和模型置信度。
- 中英混说时保持原始英文，不强制中文化。
- 可疑片段调用 ARK 或 Qwen 复核。
- 中文繁体不是重新识别；在忠实简体层之后使用 OpenCC 产生独立显示/导出变体。

##### 17.2 日语

- 初始主模型 Granite 4.1 2B，使用官方建议的英语任务提示并附课程关键词。
- 第二候选 Qwen3-ASR 1.7B，明确 `language=Japanese`。
- 第三候选 ARK-ASR-3B。
- 对人名、地名、组织、植物、药物、公式读法等，词典同时保存标准写法和读音。
- Fun-ASR 仅在用户手动启用或 benchmark 证明对某课程有优势时运行；输出经过严格循环检测。

##### 17.3 英语

- 初始主模型 MOSS Preview 2B。
- 第二候选 ARK-ASR-3B。
- 课程术语密集时将 Granite 4.1 2B 提升为主候选或同时复核。
- 大小写、缩写和标点作为独立质量指标；不能只看去标点 WER。

##### 17.4 解码规范

- 生成式模型默认 `temperature=0`、`do_sample=false`。
- 记录完整 decode 参数和随机种子。
- 对输出长度设置与音频时长相关的上限，防止无限重复。
- 不同长度音频不混在会诱发重复的批次中；默认 batch size 1，吞吐优化必须经过回归测试。

---

---

## 阶段 8：实现自动质量门控、第二/第三模型复核与时间对齐共识

### 阶段目标

用可解释、多维、时间绑定的质量体系取代长字符串投票，自动拦截循环、静音幻觉、脚本错误、覆盖不足和时间异常，并只在可疑片段调用备用模型。

### 进入条件

- [ ] 阶段 7 已能为同一 canonical 片段生成至少一个主候选和可选备用候选。
- [ ] 候选、token、decision_events 和质量特征可持久化。

### 实施任务

- [ ] 实现声学/模型、文本、时间和多模型四类质量特征，原始置信度只作为模型内特征。
- [ ] 实现字符/词速、n-gram 重复、压缩率、非法字符、脚本匹配、标点密度、数字单位、覆盖率、时间单调/重叠/倒序等检查。
- [ ] 实现最短循环周期、3～10 token n-gram 重复上限、字符/有声秒上限和前缀长期不增长检测。
- [ ] 循环候选在进入共识前即拒绝，缩短片段并换模型重跑；固定覆盖“あなたはだれですか”无限重复样例。
- [ ] 按完整条件触发第二模型：低统一质量、重复/幻觉、脚本异常、时间覆盖不足、疑似术语错误、与 MOSS 分歧过大、低 SNR/远场。
- [ ] 按完整条件触发第三模型：高价值 token 冲突、两模型低置信、同音异写、数字/单位/否定/姓名冲突。
- [ ] 把所有候选限制到同一 canonical 音频区间，优先使用词时间；无词时间时执行受约束动态规划。
- [ ] 建立字符/词 confusion network，权重综合本机校准可靠度、token 校准置信度、声学覆盖、课程词典和语言/格式合法性。
- [ ] 最终 token 必须来自至少一个 ASR 候选，新增标准写法只能来自明确术语规则；每 token 保存 provenance。
- [ ] 实现中文汉字+拼音、日语原文+reading+假名归一、英语词+可选音素的语言特定比较；大小写/标点单独评分。
- [ ] 无可靠答案时选择当前最可信候选并低置信标记，或写入对应 inaudible 标记；禁止第三生成模型按流畅度补写。
- [ ] 在代码审查和测试中禁止 SequenceMatcher 长段“选最长/居中字符串”共识。

### 阶段交付物

- [ ] 质量特征提取器、候选有效性判定、第二/第三模型路由器。
- [ ] 循环/幻觉拦截器、受约束对齐、confusion network 和 token provenance。
- [ ] 语言特定归一/读音比较库与 decision_events 完整记录。

### 退出条件

- [ ] 循环、静音长文本、脚本错误和非法时间候选不会进入最终稿。
- [ ] 同一最终句不会把多个长文本错误标到一个时间点。
- [ ] 自动共识的每个 token 都能追溯到候选或确定性术语规则。
- [ ] 第二/第三模型只在规定条件触发，正常片段不会无条件多模型重算。

### 本阶段完整技术规范

> 以下内容是本阶段必须实现的完整约束。代码、配置、测试和文档不得只实现上面的摘要而忽略本节细节。

#### 原蓝图第 18 节：自动质量检测

每个候选片段计算多维质量特征。

##### 18.1 声学与模型特征

- 模型原始置信度、token logprob、CTC/RNNT posterior（若有）。
- no-speech 概率或 VAD speech ratio。
- 音频 SNR、削波、音量。
- 词时间戳覆盖率。

##### 18.2 文本特征

- 每秒字符/词数是否异常。
- 重复 n-gram、重复句、压缩率。
- 非法字符、替换字符 `�`、连续 `??`。
- 语言脚本匹配：日语片段却大量中文无假名、英语片段大量非拉丁字符等。
- 标点密度、句尾标点、括号/引号配对。
- 数字和单位异常。
- 是否只覆盖音频前半段。

##### 18.3 时间特征

- 时间戳是否单调。
- 是否超出请求区间。
- 相邻词是否大面积重叠或倒序。
- 音频后半段仍有语音但文本时间覆盖已经结束。
- 文本长度与有声时长是否严重不匹配。

##### 18.4 多模型特征

- 字符/词级编辑距离。
- 中文拼音、日语读音、英语音素层相似度。
- 专有名词位置是否发生模型分歧。
- 模型是否同意“有内容”或“静音”。

##### 18.5 循环输出拦截

至少实现：

- 同一 3～10 token n-gram 重复次数上限。
- 最短循环周期检测。
- 输出字符数/有声秒数上限。
- 新增文本与前缀相似度长期不增长检测。
- 发现循环后拒绝候选，缩短片段并换模型重跑。

用户此前遇到的“あなたはだれですか”无限重复应在进入共识前被判定为无效候选。

---

#### 原蓝图第 19 节：自动复核与共识算法

##### 19.1 触发第二模型的条件

满足任一条件：

- 统一质量分低于阈值。
- 重复/幻觉检测触发。
- 语言脚本异常。
- 时间覆盖不足。
- 课程关键词疑似被错误识别。
- 主模型与 MOSS 结构文本分歧过大。
- 音频被标记为低 SNR 或远场。

##### 19.2 触发第三模型的条件

- 第二模型仍与主模型在高价值 token 上冲突。
- 两模型都低置信度。
- 读音相同但汉字/专名写法不同。
- 数字、单位、否定词、姓名等关键内容冲突。

##### 19.3 时间对齐的共识

1. 把各候选限制在相同 canonical 音频区间。
2. 对可用词时间做初始对齐；没有词时间时进行受约束动态规划。
3. 建立字符/词 confusion network。
4. 每个候选 token 的权重包括：
   - 该模型在此语言/场景的本机校准可靠度。
   - 此 token 的校准置信度。
   - 声学时间覆盖。
   - 课程词典匹配。
   - 语言和格式合法性。
5. 只允许采用至少由一个 ASR 候选产生的 token；新增标准写法只能来自明确的术语规则。
6. 每个最终 token 保存 provenance。

不能仅按 `SequenceMatcher` 计算整段文本相似度并选最长或“居中”文本。

##### 19.4 语言特定比较

- 中文：汉字层和拼音层双重比较；“同音不同字”进入术语决策，不直接判为完全不同音频内容。
- 日语：原文层、假名 reading 层和片假名/平假名归一层比较；例如 `鳥羽市` 与 `飛ばし` 的读音接近，但标准写法由课程上下文决定。
- 英语：单词层和可选音素层比较；大小写、标点另行评分。

##### 19.5 无可靠答案时

- 不让第三个生成模型凭语言流畅度“补完整”。
- 输出当前最可信候选并标记低置信度，或使用语言对应的 `inaudible` 标记。
- 自动导出仍继续完成。

---

---

## 阶段 9：实现课程词典、严格标点、文本分层、精细对齐与导出核心

### 阶段目标

在不破坏忠实层的前提下完成课程术语纠正、三语标点/大小写、精细词句时间轴、字幕分行和多版本输出。

### 进入条件

- [ ] 阶段 8 已能生成通过质量门控、带 provenance 的自动最终候选。
- [ ] 阶段 2 的 glossaries、token_spans、decision_events 和 transcript_segments 文本层字段可用。

### 实施任务

- [ ] 实现课程配置 YAML、canonical/reading/aliases/weight/source/确认状态，并支持手工、TXT/Markdown/CSV、本地 PPTX/PDF/讲义/教科书和历史确认文本来源。
- [ ] 自动抽取只产生低权重建议词，禁止未经声学/读音支持强行注入普通词。
- [ ] 实现确定性纠正规则，将修正只写入 smart_corrected_text，不覆盖 faithful_text。
- [ ] 实现滚动上下文：最近 1～3 个已确认句段和相关 top-K 关键词，按课程/章节/语言/近期频率排序，不传整堂 90 分钟文本。
- [ ] 实现 raw_text、faithful_text、smart_corrected_text、user_text 四层，导出优先 user→smart→faithful。
- [ ] 中文使用 FireRedPunc，结合 VAD 停顿/说话人变化/语气；简繁转换仅为最终变体。
- [ ] 英语优先原生大小写/标点，异常时 FireRedPunc，词典保护缩写、产品名、姓名；报告 case-sensitive WER 与 punctuation F1。
- [ ] 日语依次使用 Granite 标点边界、声学边界、边界投射、必要时标点序列标注器，并处理 。、？！「」（）配对和密度。
- [ ] 所有标点模块强制 strip_punctuation_and_spacing(before)==strip_punctuation_and_spacing(after)，失败即拒绝并回退。
- [ ] 实现 canonical 时间来源优先级：可靠原生词时间→MOSS 结构时间→Qwen ForcedAligner→VAD 粗时间。
- [ ] 只对小于安全上限、覆盖合理、无循环/漏句/异常字符、语言一致、长度比例正常的短段运行 Qwen ForcedAligner。
- [ ] 对齐失败标 coarse_timing 并保留 MOSS/VAD 粗时间，禁止把错误文本强行压入时间轴。
- [ ] 实现 SRT/VTT 的最终句/词时间、语言化阅读速度和最多两行分行，不在专名或数字单位中间任意断开。
- [ ] 实现 TXT、Markdown、JSON、SRT、VTT、CSV 导出器，分别支持忠实、智能纠正和用户版本。

### 阶段交付物

- [ ] 课程词典/材料导入、确定性术语纠正和上下文选择器。
- [ ] 三语标点与格式模块、严格不改字属性测试。
- [ ] Qwen ForcedAligner 适配、对齐门控、时间验证和 coarse fallback。
- [ ] 六种导出格式与逐句版/可阅读段落版。

### 退出条件

- [ ] faithful_text 不会被术语或自由语言模型改写；所有智能修正有差异和规则来源。
- [ ] 任何通过的标点结果都满足去标点字符序列不变。
- [ ] 所有最终时间单调、在范围内、无负时长；失败时明确降级为粗时间。
- [ ] 字幕使用最终句/词时间而非大 VAD 块，所有版本均可直接导出。

### 本阶段完整技术规范

> 以下内容是本阶段必须实现的完整约束。代码、配置、测试和文档不得只实现上面的摘要而忽略本节细节。

#### 原蓝图第 20 节：课程词典与上下文

##### 20.1 课程配置文件

```yaml
course_id: bio_2026_fall
language: ja
name: 生物学概論
instructors:
  - canonical: 田中太郎
    reading: たなかたろう
terms:
  - canonical: ヤマトタチバナ
    reading: やまとたちばな
    aliases: [大和橘, 大和立花]
    weight: 1.0
  - canonical: 沖縄科学技術大学院大学
    reading: おきなわかがくぎじゅつだいがくいんだいがく
    aliases: [OIST]
    weight: 1.0
```

##### 20.2 词典来源

- 用户手工录入。
- TXT/Markdown/CSV。
- 课程 PPTX、PDF、讲义和教科书章节的本地抽取。
- 历史已确认转录中的高频专有名词。

自动抽取只产生“建议词”，用户不确认也可使用低权重；不得把普通词误当热词强行注入。

##### 20.3 确定性纠正规则

- 候选读音与词典读音高度一致。
- 词典标准词在当前课程高权重。
- 该音频位置至少一个模型输出了标准词或别名，或声学/读音对齐充分支持。
- 修正写入 `smart_corrected_text`，不覆盖 `faithful_text`。

##### 20.4 上下文长度控制

- 不把整个 90 分钟历史文本塞进每次推理。
- 传递最近 1～3 个已确认句段和与当前片段相关的 top-K 关键词。
- 关键词按课程、章节、当前语言和最近出现频率排序。
- 防止长热词列表诱发幻觉：无声学支持的热词不能自动插入。

---

#### 原蓝图第 21 节：标点、大小写与格式

##### 21.1 三层文本

每个片段至少保存：

```text
raw_text             模型原始输出
faithful_text        只做可逆规范化和严格标点，不改词
smart_corrected_text 允许确定性术语/同音字修正
user_text             用户可选修改；为空时导出 smart_corrected_text
```

##### 21.2 中文

- FireRedPunc 作为默认标点恢复器。
- 结合 VAD 停顿、说话人变化和语气检测校正句号/问号。
- 简繁转换发生在最终显示变体，不回写忠实层。

##### 21.3 英语

- 优先采用模型原生大小写和标点。
- 原生结果异常时运行 FireRedPunc。
- 缩写、产品名、姓名由词典保护。
- 单独计算 case-sensitive WER 和 punctuation F1。

##### 21.4 日语

日语不能再假设“模型自然会给好标点”。采用以下顺序：

1. Granite 标点版输出作为一个标点边界候选。
2. 利用停顿、说话人变化和句末表达产生声学边界候选。
3. 将边界投射到最终选定的日语字符序列。
4. 必要时运行“标点序列标注器”，输出边界标签，而不是自由生成整段文字。
5. 处理 `。 、 ？ ！ 「」 （）` 的配对和密度。

严格校验：

```python
strip_punctuation_and_spacing(before) == strip_punctuation_and_spacing(after)
```

校验失败则拒绝本次标点结果并回退，保证标点模块不改字。

##### 21.5 段落

- 长停顿、章节提示、说话人变化和语义边界共同决定段落。
- 段落结构不改变字幕的原始词时间。
- 导出“逐句版”和“可阅读段落版”两套格式。

---

#### 原蓝图第 22 节：时间轴与强制对齐

##### 22.1 canonical timeline

- `audio_master.wav` 固定 16,000 samples/s。
- 所有时间内部使用 int64 sample index。
- `ms = sample * 1000 / 16000` 仅用于显示。
- 禁止累加浮点秒数，避免 90 分钟后漂移。

##### 22.2 时间来源优先级

```text
1. 可靠原生词时间戳（例如 FireRed AED）
2. MOSS 原生结构片段时间
3. Qwen ForcedAligner 对最终文字的精细对齐
4. VAD 边界粗时间（最后回退）
```

##### 22.3 ForcedAligner 门控

仅在以下条件同时满足时使用：

- 片段小于 aligner 的安全上限；本项目通常小于 30 秒。
- 文本覆盖率合理。
- 没有循环、明显漏句或异常字符。
- 文本语言与音频语言一致。
- 字符/词数与有声时长比例正常。

ForcedAligner 不负责纠正文本；错误或截断文本不得被强行安放到时间轴。

##### 22.4 对齐验证

- start/end 单调且在音频区间内。
- 单词持续时间不为负。
- 大多数有声区间有文本覆盖。
- 相邻句间隙与 VAD 基本一致。
- 对齐代价过高时标记为 `coarse_timing`，保留 MOSS/VAD 片段时间。

##### 22.5 字幕生成

SRT/VTT 必须使用最终句/词时间，而不是最初的大 VAD 块。字幕分行规则按语言配置：

- 中文/日语：字符数和阅读速度限制。
- 英语：词数、CPS 和自然短语边界。
- 每条字幕最多两行；不把一句任意切在专名或数字单位中间。

---

---

## 阶段 10：贯通课堂自动流水线、版本化后端 API 与完整 WebUI

### 阶段目标

把前述基础设施贯通成用户可操作的课堂转录工作台：上传后无需人工阻塞即可完成结构、正文、复核、标点、对齐和自动导出，同时保留透明校对能力。

### 进入条件

- [ ] 阶段 4～9 的导入、结构、正文、QA、术语、标点、对齐和导出模块均可单独运行。
- [ ] 阶段 2 的状态机、安全和数据库已稳定。

### 实施任务

- [ ] 实现完整持久化流水线：上传与校验→标准化→QC→VAD→手动/LID→MOSS 结构→8～30 秒句段→主 ASR→质量门控→备用模型→时间对齐共识→术语→标点→ForcedAligner→最终验证→自动导出→可选校对。
- [ ] 每阶段、每片段写入可重入状态，支持取消、暂停、恢复、单片段重试和故障点恢复。
- [ ] 实现 /api/v1 下完整任务、转录、模型/配置、词典/材料、导出/基准端点，并为所有写端点执行 token/CSRF 校验。
- [ ] 实现 React+TypeScript+Vite 前端，使用 TanStack Query、Zustand、WaveSurfer.js、SSE、Vitest、Playwright。
- [ ] 上传页提供语言、课程词典、说话人数、自动最佳/手动主模型、快速/平衡/最高精度/严格单模型，以及输出选项。
- [ ] 任务页显示真实阶段、模型、片段、速度、显存，并提供暂停/继续/取消/重试；IBus 抢占时显示安全边界暂停。
- [ ] 转录页同步波形、说话人轨道、语言轨道和文本；默认显示自动最终稿，可切 raw/faithful/smart/user。
- [ ] 支持展开候选、质量原因和 provenance，低置信筛选、点击跳音频、框选重跑、说话人改名、术语/文本编辑、自动保存、撤销/重做和审计。
- [ ] 模型管理页展示安装、版本、大小、哈希、能力、显存、下载/验证/删除/回滚、本机排名；仅基准通过后允许设为自动最佳。
- [ ] 导出页面支持 TXT/MD/JSON/SRT/VTT/CSV 以及忠实/智能/用户版本。
- [ ] 生成 OpenAPI 文档和 API 契约测试；文件 ID 永远不接受任意系统路径。

### 阶段交付物

- [ ] 可用的 classscribe-core FastAPI 服务、完整 /api/v1 和 OpenAPI。
- [ ] 上传页、任务页、转录校对页、模型管理页、词典页、设置/诊断页和导出界面。
- [ ] 课堂任务端到端自动流水线、SSE 进度和无人工阻塞自动导出。

### 退出条件

- [ ] 用户能从浏览器导入 90 分钟录音、选择语言/模型/课程并获得自动导出，不必打开校对页。
- [ ] 每个句段只绑定真实音频区间，候选和最终 token 来源可查看。
- [ ] 任务暂停/恢复、服务重启、片段失败和 IBus 抢占均在界面准确反映。
- [ ] 前端端到端测试覆盖上传、任务、校对、模型、词典和导出主流程。

### 本阶段完整技术规范

> 以下内容是本阶段必须实现的完整约束。代码、配置、测试和文档不得只实现上面的摘要而忽略本节细节。

#### 原蓝图第 12 节：课堂转录完整流水线

```text
上传与校验
  ↓
音频抽取与无损标准化
  ↓
音频质量分析
  ↓
FireRedVAD 语音区间
  ↓
语言模式：手动直接指定；自动/混合执行 FireRedLID
  ↓
MOSS-TD 结构转录：说话人 + 粗片段 + 时间
  ↓
把结构片段细化为 8～30 秒自然句段
  ↓
按语言配置运行主 ASR
  ↓
自动质量门控
  ├─ 合格：进入术语/标点/对齐
  └─ 可疑：调用第二模型；必要时第三模型
  ↓
时间对齐的字符/词级共识
  ↓
课程词典与确定性术语纠正
  ↓
严格标点恢复
  ↓
Qwen ForcedAligner 精细时间戳（通过门控才运行）
  ↓
时间轴、说话人和覆盖率最终验证
  ↓
自动生成忠实版、智能纠正版与导出
  ↓
可选人工校对
```

每个阶段都持久化状态，允许取消、重试单个片段和从故障点恢复。

---

#### 原蓝图第 23 节：WebUI 功能

##### 23.1 上传页

- 拖放音频/视频。
- 语言：中文／日语／英语／自动混合。
- 课程配置和词典。
- 说话人数：自动、1、2、3～4、5+。
- 模型：自动最佳或手动主模型。
- 模式：快速、平衡、最高精度、严格单模型测试。
- 是否生成忠实版、智能纠正版、说话人、字幕。

##### 23.2 任务页

显示真实阶段而不是笼统百分比：

```text
音频校验 → VAD → LID → 结构 → 主 ASR → 自动复核 → 标点 → 对齐 → 导出
```

- 当前模型、片段编号、处理速度、显存。
- 暂停、继续、取消、重试失败片段。
- IBus 抢占 GPU 时显示“批处理已在安全边界暂停”。

##### 23.3 转录页

- 波形、说话人轨道、语言轨道、文本区域同步。
- 每个句段只对应其真实时间范围。
- 默认显示自动最终稿；可切换 raw/faithful/smart/user。
- 展开某句查看各模型候选、质量原因和 provenance。
- 低置信度句可筛选，但不要求逐个处理。
- 点击文本跳到音频；框选音频可重跑当前句。
- 修改说话人姓名、术语、文本。
- 保存自动；有完整撤销/重做和审计历史。

##### 23.4 模型管理页

- 已安装、可安装、版本、大小、哈希、能力、显存实测。
- 下载、验证、删除、回滚。
- 每种语言和场景的本机 benchmark 排名。
- “设为自动最佳”只在基准通过后允许。

##### 23.5 导出

至少支持：

- TXT、Markdown。
- JSON（完整结构、候选和 provenance）。
- SRT、VTT。
- CSV（时间、说话人、文本、质量分）。
- 忠实版、智能纠正版、用户版分别导出。

---

#### 原蓝图第 24 节：后端 API

建议版本化路径 `/api/v1`。

##### 24.1 任务与录音

```text
POST   /recordings
GET    /recordings/{id}
POST   /jobs
GET    /jobs/{id}
GET    /jobs/{id}/events          SSE
POST   /jobs/{id}/pause
POST   /jobs/{id}/resume
POST   /jobs/{id}/cancel
POST   /jobs/{id}/retry
POST   /jobs/{id}/retry-segment/{segment_id}
```

##### 24.2 转录

```text
GET    /jobs/{id}/transcript
GET    /segments/{id}
PATCH  /segments/{id}
GET    /segments/{id}/candidates
POST   /segments/{id}/adopt-candidate
POST   /segments/{id}/rerun
POST   /segments/{id}/split
POST   /segments/{id}/merge
```

##### 24.3 模型和配置

```text
GET    /models
POST   /models/{id}/install
POST   /models/{id}/verify
DELETE /models/{id}
GET    /profiles
PUT    /profiles/{language}/{scenario}
GET    /settings
PUT    /settings
```

##### 24.4 词典与材料

```text
GET    /glossaries
POST   /glossaries
POST   /glossaries/{id}/documents
PUT    /glossaries/{id}/terms
DELETE /glossaries/{id}/terms/{term_id}
```

##### 24.5 导出与基准

```text
POST   /jobs/{id}/exports
GET    /exports/{id}
POST   /benchmarks
GET    /benchmarks/{id}
GET    /benchmarks/{id}/results
POST   /benchmarks/{id}/apply-ranking
```

所有写接口验证本地 API token 和 CSRF；文件 ID 不直接接受任意系统路径。

---

---

## 阶段 11：实现 Fedora IBus 语音输入法、流式分块与 Wayland 全局快捷键

### 阶段目标

完成薄 IBus engine、dictationd 和 portal hotkey 三组件，在中文/日语/英语/自动模式下提供稳定 preedit、自动长语音分块、最终确认和应用兼容。

### 进入条件

- [ ] 阶段 5 的 Worker streaming RPC 和 GPU 租约可用。
- [ ] 阶段 7～9 的语言模型、QA、术语、标点和文本去重原语可复用。
- [ ] 阶段 3 的 IBus component/systemd/RPM 骨架已安装。

### 实施任务

- [ ] 实现 ibus-engine-classscribe，仅处理按键、property、update_preedit_text、commit_text、update_lookup_table，不加载 PyTorch/CUDA。
- [ ] 实现 classscribe-dictationd，持有 PipeWire 麦克风、流式 VAD、流式 ASR、自动分段、最终重解码和 GPULeaseManager 通信。
- [ ] 实现 classscribe-hotkey，通过 XDG Desktop Portal GlobalShortcuts 注册 Wayland/GNOME/KDE 全局快捷键，不以 X11 grab 为主方案。
- [ ] 实现 IDLE→ARMING→LISTENING/INTERIM_UPDATE→FINALIZING→可选 CANDIDATE_SELECT→COMMITTING→IDLE 状态机和 ERROR 安全回退。
- [ ] 实现语言、模式、模型、按住/单击、临时结果、标点的 IBus 属性菜单。
- [ ] interim 只进入 preedit；不稳定尾部显示属性，稳定前缀不跳动；最终才 commit；候选窗可数字键选择但默认自动第一候选。
- [ ] 使用 GStreamer pipewiresrc，转 16 kHz mono S16LE，20～40 ms 帧送 streaming VAD；默认不保存麦克风原始音频。
- [ ] 实现流式模型 chunk、3～20 秒语义句段、20～30 秒 hard chunk 三层分块，hard chunk 重叠 1.5～2.5 秒。
- [ ] 连续超过 1 分钟时保持 rolling context、稳定前缀分段提交、未提交尾部、语言/模型/词典状态、chunk_seq 和绝对 sample；用时间+token 对齐去重。
- [ ] 禁止按固定 N 字符删除边界；稳定前缀提交与 overlap 去重必须复用 sample-based 原语。
- [ ] 实现快速、平衡、最高精度三种松键确认；最高精度未常驻且热切换慢时提前显示。
- [ ] 手动语言始终强制；自动模式结合 Nemotron/Qwen LID 与 FireRedLID，只在稳定句段边界切换；中英混说保持统一会话，日语英文术语不切英语模型。
- [ ] 验证 GTK、Qt、Firefox、Chromium、VS Code、GNOME Console、Konsole、LibreOffice；不支持 preedit 属性时回退普通 preedit。
- [ ] 处理 dictationd 不可用、麦克风断开、GPU OOM、Socket 断开和取消听写，任何错误不得崩溃 IBus 或吞普通键盘输入。

### 阶段交付物

- [ ] IBus engine、dictationd、hotkey portal 三个可运行组件和 component XML。
- [ ] 完整状态机、属性菜单、preedit/commit/lookup 行为和音频流。
- [ ] 长听写自动分块、stable-prefix、rolling-context、时间/token 去重实现。
- [ ] 多应用、Wayland/X11、GNOME/KDE 兼容测试结果。

### 退出条件

- [ ] 按住说话能显示 interim，松键最终提交；取消不提交 preedit。
- [ ] 连续 5 分钟固定测试跨多次自动分块无重复/漏词。
- [ ] 课堂任务能在片段安全边界被 IBus 抢占并恢复。
- [ ] 任何守护进程、麦克风、GPU 或 portal 错误都不导致 IBus daemon 或当前应用崩溃。

### 本阶段完整技术规范

> 以下内容是本阶段必须实现的完整约束。代码、配置、测试和文档不得只实现上面的摘要而忽略本节细节。

#### 原蓝图第 26 节：IBus 语音输入法详细设计

##### 26.1 组件

```text
ibus-engine-classscribe
  - 处理按键和 IBus property
  - update_preedit_text
  - commit_text
  - update_lookup_table
  - 不加载模型

classscribe-dictationd
  - PipeWire 麦克风
  - 流式 VAD
  - 流式 ASR
  - 分段和最终重解码
  - 与 GPULeaseManager 通信

classscribe-hotkey
  - XDG Desktop Portal GlobalShortcuts
  - 在未切换到 ClassScribe 输入源时也可启动/停止听写
```

##### 26.2 状态机

```text
IDLE
  ↓ 按住快捷键
ARMING
  ↓ 麦克风/GPU 就绪
LISTENING
  ↔ INTERIM_UPDATE
  ↓ 松键、终点或安全分块
FINALIZING
  ↓
CANDIDATE_SELECT（可选，通常自动跳过）
  ↓
COMMITTING
  ↓
IDLE
```

错误进入 `ERROR`，显示短状态并回到 `IDLE`；不能让 IBus 进程崩溃或吞掉普通键盘输入。

##### 26.3 IBus 属性菜单

在语言栏提供：

```text
语言：中文 / 日本語 / English / Auto
模式：快速 / 平衡 / 最高精度
模型：自动最佳 / 具体已安装模型
启动：按住说话 / 单击开始-结束
临时结果：显示 / 隐藏
标点：自动 / 仅句末 / 关闭
```

##### 26.4 实时输出

- interim 结果进入 `update_preedit_text`，不直接写入应用。
- 当前不稳定尾部使用下划线或不同属性显示。
- 稳定前缀不再反复跳动。
- 最终结果通过 `commit_text` 提交。
- 多个候选可用 `update_lookup_table` 显示，数字键选择；默认自动选第一候选，不要求用户操作。

##### 26.5 采音

- GStreamer `pipewiresrc` 获取系统默认麦克风。
- 转成 16 kHz、单声道、S16LE 小帧。
- 20～40 ms 一帧送给 streaming VAD。
- 显示明确录音状态；可选开始/结束提示音。
- 输入法不默认保存原始麦克风录音。

##### 26.6 自动分块

三个层级：

1. **流式模型 chunk**：80～1120 ms，由模型要求决定。
2. **语义句段**：通常 3～20 秒，在自然停顿处完成一次稳定解码。
3. **安全 hard chunk**：连续无停顿达到 20～30 秒时强制切分，保留 1.5～2.5 秒重叠。

对持续超过 1 分钟的输入：

- 维护 rolling context，而不是把全部历史重新送入模型。
- 已稳定且通过质量门控的前缀分段提交。
- 始终保留最近一小段未提交尾部，用于边界重解码和去重。
- 语言、模型和词典状态跨块保持。
- 每个块生成 `chunk_seq` 和绝对样本编号；合并时以时间和 token 对齐去重。
- 不使用“删除前一个输出最后 N 个字符”这类脆弱规则。

##### 26.7 松键后的最终确认

提供三种配置：

- 快速：同一流式模型 flush 后提交，目标中位等待不超过约 0.5 秒。
- 平衡：同一常驻模型对完整稳定句段重新解码，目标中位约 1.2 秒以内。
- 最高精度：语言专用模型重算；目标 p95 可放宽到约 2.5 秒，实际由本机 benchmark 决定。

如果最高精度模型未常驻且预计热切换过慢，界面必须提前显示，不可假装是低延迟模式。

##### 26.8 语言路由

- 手动模式始终强制语言。
- 自动模式由 Nemotron/Qwen 自身 LID 与 FireRedLID 句段结果共同决定。
- 语言只在稳定句段边界切换。
- 中文中英混说优先保持一个 FireRed/Qwen 会话。
- 日语中的英文术语不单独触发英语模型。

##### 26.9 应用兼容

验证至少覆盖：

- GTK：GNOME Text Editor。
- Qt：KWrite/Kate。
- 浏览器：Firefox、Chromium。
- Electron：VS Code。
- 终端：GNOME Console、Konsole。
- LibreOffice。

对不支持某些 preedit 属性的客户端，回退为普通 preedit 文本，不影响提交。

##### 26.10 全局快捷键

- 使用 XDG Desktop Portal GlobalShortcuts 创建会话和绑定。
- 不直接使用 X11 grab 作为 Wayland 主方案。
- 专用 IBus 输入源无需全局快捷键也可工作。
- 快捷键冲突或 portal 后端不支持时，在设置页给出诊断，并保留 IBus 输入源入口。

---

---

## 阶段 12：建立 Gold 基准、置信度校准、自动选模、全层测试与性能验收

### 阶段目标

用用户自己的课堂与听写数据决定每语言/场景“自动最佳”，并通过单元、属性、契约、集成、端到端和固定回归测试验证准确性、时间轴、性能、显存和可靠性。

### 进入条件

- [ ] 阶段 10 的课堂产品和阶段 11 的 IBus 产品均可端到端运行。
- [ ] 所有候选模型可通过统一 benchmark runner 调用并记录 revision、硬件和参数。

### 实施任务

- [ ] 每种语言建立 5～10 分钟起步、逐步扩展到 30 分钟以上的代表课堂 gold；建立 50～100 条 IBus 近讲听写并包含超过 1 分钟连续输入。
- [ ] 保存人工校正文本、说话人、句边界和课程术语，使用规定 JSONL manifest。
- [ ] 实现中文/日语 CER、英语 WER、原始/规范化双指标、术语 F1、数字/单位/否定/姓名正确率。
- [ ] 实现标点 P/R/F1、PER、英语 capitalization F1、括号/引号错误；实现漏句、静音幻觉、循环、覆盖率和异常字符指标。
- [ ] 实现词/句边界误差、250/500 ms 句边界 F1、DER/JER、speaker-attributed CER/WER、跨窗口 speaker switch。
- [ ] 实现 RTF、90 分钟总时长、峰值显存/内存、IBus interim/commit median/p95、加载/抢占恢复时间。
- [ ] 统一中文/日语数字、全半角、标点规范；英语同时报告 lowercase/no-punctuation WER 和原始可读指标，禁止不同规范直接比较。
- [ ] 为每模型/语言/场景用 isotonic regression 或 Platt scaling 校准；训练/验证/测试分离，模型更新后重新校准。
- [ ] 按正文错误、术语、漏句/幻觉、时间轴、标点、性能和显存约束计算综合分；课堂/IBus与中/日/英分别排名。
- [ ] 执行全部单元、Hypothesis 属性、Worker 契约、集成、90 分钟 E2E、多说话人、三语切换、纯静音、背景音乐、录音中断和多桌面/应用测试。
- [ ] 固定回归 Cohere 式只取第一块、Fun-ASR 无限重复、无标点/??、三模型同一时间点、错误文本强制对齐、VAD 无重叠截词等样例。
- [ ] 以完整性能/验收阈值核验课堂和 IBus；任何新模型或依赖升级必须重跑相应基准与固定回归。
- [ ] 仅在基准通过后应用自动排名；初始 bootstrap 顺序可以被本机结果重写并保留可回滚记录。

### 阶段交付物

- [ ] 中/日/英课堂与 IBus gold 数据、manifest、normalizer、scorer 和 benchmark runner。
- [ ] 置信度校准模型、自动排名报告和 profile 应用/回滚机制。
- [ ] 完整测试矩阵、固定回归音频、性能报告和硬件/参数记录。

### 退出条件

- [ ] 每种语言与场景都有可复现本机排名，自动最佳不再依赖公开宣传数字。
- [ ] 课堂 90 分钟任务、IBus 5 分钟长听写、抢占、OOM、重启和异常输入全部通过验收。
- [ ] 时间轴结构错误为 0，循环候选不进入最终稿，静音长文本被拒绝。
- [ ] 具体 CER/WER 目标来自 gold 数据而非虚构；所有阈值和校准版本可追溯。

### 本阶段完整技术规范

> 以下内容是本阶段必须实现的完整约束。代码、配置、测试和文档不得只实现上面的摘要而忽略本节细节。

#### 原蓝图第 27 节：基准、校准与自动选模

“自动最佳”必须来自本机可复现基准。

##### 27.1 Gold 数据

每种语言至少准备：

- 课堂代表录音 5～10 分钟起步，逐步扩展到 30 分钟以上。
- 包含清晰讲课、较远距离、学生提问、专业术语、数字和专名。
- IBus 近讲听写 50～100 条，含短句、长句、停顿和连续超过 1 分钟的测试。
- 人工校正文本、说话人和句边界。

JSONL 示例：

```json
{"audio":"ja/class01.wav","start_ms":0,"end_ms":18400,"language":"ja","speaker":"teacher","text":"今日は人工知能について説明します。","terms":["人工知能"]}
```

##### 27.2 指标

###### 正文

- 中文/日语：CER。
- 英语：WER。
- 原始指标和规范化指标同时报告。
- 专有名词/课程术语 precision、recall、F1。
- 数字、单位、否定词和姓名正确率。

###### 标点和格式

- Punctuation precision/recall/F1。
- Punctuation Error Rate。
- 英语 capitalization F1。
- 引号/括号配对错误数。

###### 幻觉和覆盖

- 漏句率。
- 静音幻觉数/小时。
- 循环重复触发数/小时。
- 有声时间覆盖率。
- 异常字符率。

###### 时间和说话人

- 词边界平均绝对误差。
- 句边界 F1（容差 250/500 ms）。
- DER/JER。
- speaker-attributed CER/WER。
- 跨结构窗口 speaker ID switch 数。

###### 性能

- RTF、90 分钟总处理时间。
- 峰值显存、峰值系统内存。
- IBus interim 延迟、松键到 commit 的 median/p95。
- 模型加载时间和抢占恢复时间。

##### 27.3 统一规范

- 中文和日语必须明确是否去标点、数字是否统一、全半角如何处理。
- 英语同时报告 lowercase/no-punctuation WER 和原始可读文本指标。
- 不允许拿一个模型的去标点 CER 与另一个模型的原始标点文本直接比较。

##### 27.4 置信度校准

- 每个模型/语言/场景独立校准。
- 使用 isotonic regression 或 Platt scaling，把原始置信度和质量特征映射到“该片段低于给定错误阈值的概率”。
- 训练集和验证集分开；避免用测试集同时定阈值和报结果。
- 模型更新后重新校准。

##### 27.5 自动排名

自动最佳不只按 CER/WER：

```text
综合分 = 正文错误率
       + 术语错误惩罚
       + 漏句/幻觉高额惩罚
       + 时间轴错误惩罚
       + 标点错误
       + 性能与显存约束
```

课堂和 IBus 分别排名；中文、日语、英语分别排名。

---

#### 原蓝图第 33 节：测试计划

##### 33.1 单元测试

- 时间 sample/ms 换算与 90 分钟无漂移。
- VAD 区间合并、重叠和 hard split。
- 语言滞回。
- 中/日/英规范化。
- 标点不改字不变量。
- 重复循环检测。
- 词典读音匹配。
- confusion network 与 provenance。
- SRT/VTT 边界和分行。
- 数据库迁移和乐观锁。

##### 33.2 属性测试

- 任意合法区间经切分、重叠、合并后覆盖不丢失。
- 所有最终时间满足 `0 <= start <= end <= duration`。
- 标点模块对任意 Unicode 输入都保持去标点字符序列不变。
- 去重算法不会删除不在重叠时间内的 token。

##### 33.3 Worker 契约测试

所有 worker 必须通过同一套：

- capability schema。
- 取消和 deadline。
- 空音频、纯静音、极短音频。
- 非法语言。
- 超长片段。
- Unicode 和特殊标点。
- 进程退出后的显存释放。

##### 33.4 集成测试

- 每种语言至少一个真实模型。
- MOSS 结构 → 主 ASR → fallback → 对齐 → 导出。
- 任务暂停/恢复、服务重启、worker 崩溃。
- IBus 抢占正在运行的课堂任务。
- 模型缺失、离线、磁盘满、显存不足。

##### 33.5 端到端测试

- 90 分钟真实课堂。
- 1、2、5+ 说话人。
- 中文中英混说、日英术语、三语连续切换。
- 纯静音、背景音乐、录音中断。
- IBus 连续 5 分钟，验证多次自动分块无重复/漏词。
- Wayland GNOME/KDE 与 X11。
- GTK、Qt、Firefox、Chromium、Electron、终端、LibreOffice。

##### 33.6 回归测试

把用户已经发现的错误全部做成固定样例：

- Cohere 式长音频只取第一块。
- Fun-ASR 短句无限重复。
- 无标点或只出现 `??`。
- 三模型长文本被错误标在同一时间节点。
- 错误文本被 ForcedAligner 强行压入时间轴。
- VAD 无重叠硬切造成词截断。

任何新模型或依赖升级都必须重跑这些样例。

---

#### 原蓝图第 34 节：性能目标与验收阈值

以下是工程目标，不是模型准确率保证。

##### 34.1 课堂

- 90 分钟任务可自动完成，期间不要求人工点击。
- 任意异常片段可局部重试，不重跑整堂课。
- 时间轴结构错误数为 0：无倒序、负时间、超范围。
- 循环重复候选不得进入最终稿。
- 静音区不得自动生成长文本；发现则拒绝并重跑。
- 单模型默认批量目标 RTF 小于 1；多模型最高精度模式的总耗时按实测显示，不作虚假“实时”承诺。
- 峰值显存保持安全余量，连续任务无 CUDA OOM 泄漏。

##### 34.2 IBus

- interim 首次可见文本：目标 p50 < 600 ms。
- 平衡模式松键到 commit：目标 p50 < 1.2 s，p95 < 2.5 s。
- 连续超过 1 分钟自动分块；固定测试中边界重复/漏词为 0。
- 任何错误不应导致 IBus daemon 或当前应用崩溃。
- 取消听写不得提交 preedit。

##### 34.3 自动质量

- 自动流水线相对本机最优单模型不能显著恶化主要 CER/WER。
- 在术语集、漏句率、循环/幻觉率上应优于单模型。
- 低置信度可标记但不阻止导出。
- 所有最终 token 可追溯。

具体 CER/WER 目标应在建立用户 gold 数据后填写，不能凭公开数据虚构。

---

---

## 阶段 13：完成发布加固、风险闭环、文档、RPM 与最终交付

### 阶段目标

关闭已知风险，完成可重现安装与用户文档，核对全部交付物和官方事实来源，并以最终架构原则审计系统没有退回“万能模型”或“长文本投票”。

### 进入条件

- [ ] 阶段 12 的完整基准、测试和性能验收已经通过或所有已知偏差都有明确记录和禁用策略。
- [ ] 候选模型 revision、许可证、哈希、worker 环境和自动排名已经冻结到发布清单。

### 实施任务

- [ ] 逐项关闭主要风险表：公开榜单偏差、MOSS 显存、依赖冲突、GPU 争用、循环/幻觉、日语标点、错误强制对齐、speaker stitching、LID 过切、模型更新、remote code、Wayland 快捷键、IBus 边界重复。
- [ ] 完成 Fedora RPM spec、可安装 RPM、三个 systemd user service、IBus component/XML、desktop、图标和升级/卸载测试。
- [ ] 冻结完整源码、uv/pnpm lockfiles、协议 schema、数据库迁移、模型 registry/revision lock 和许可证清单。
- [ ] 完成 WebUI、OpenAPI、worker 协议、时间轴、共识、词典、标点、说话人和 benchmark 算法文档。
- [ ] 完成用户手册：安装、模型下载、课堂转录、IBus、词典、校对、导出、排错、数据删除和离线隐私。
- [ ] 完成诊断页、脱敏诊断包、固定回归音频和预期结果。
- [ ] 执行最终架构审计：课堂必须是 MOSS 结构→语言专用句段→QA 复核→时间对齐共识→术语→严格标点→门控对齐→自动导出；IBus 必须是 PipeWire/VAD→常驻流式 preedit→自动分块→可选专用确认→commit。
- [ ] 核对模型/APIs 的官方来源与 2026-09-03 事实截止日期；发布时所有 revision 固定，后续更新必须走升级/回滚流程。
- [ ] 对照阶段 0 的所有成功标准、阶段 12 的性能阈值和完整交付物清单进行发布签核。

### 阶段交付物

- [ ] 完整源码、可重现 lockfiles、模型注册表与 revision lock。
- [ ] Fedora RPM、systemd services、IBus 输入源、WebUI/API/OpenAPI 和全部导出器。
- [ ] benchmark runner、测试/回归资产、诊断工具、算法文档和用户手册。
- [ ] 发布清单、风险关闭记录、已知限制和官方资料索引。

### 退出条件

- [ ] 完成时应具备的全部交付物逐项存在且可在干净 Fedora 环境复现。
- [ ] 运行期默认无外网请求，音频、文本、词典、日志和听写数据均按隐私规则留在本机。
- [ ] 所有最终 token、模型选择、自动修正和时间来源均可追溯。
- [ ] 系统未来新增更好模型时只需新增 worker、注册表条目和基准，不必重写业务流水线。

### 本阶段完整技术规范

> 以下内容是本阶段必须实现的完整约束。代码、配置、测试和文档不得只实现上面的摘要而忽略本节细节。

#### 原蓝图第 37 节：主要风险与应对

| 风险 | 应对 |
|---|---|
| 公开榜单不代表真实课堂 | 本机 gold benchmark、分语言/场景排名 |
| MOSS 90 分钟在 12 GB 上显存不足 | 自适应 12 分钟结构窗口、重叠和 speaker stitching |
| 多模型依赖冲突 | 每个模型独立 uv 环境和进程 |
| IBus 与课堂争夺 GPU | 优先级租约、句段边界抢占、模型常驻策略 |
| 生成式模型循环/幻觉 | 长度上限、n-gram 循环检测、静音/覆盖率门控、备用模型 |
| 日语标点不稳定 | 独立边界标注层、严格不改字校验 |
| 强制对齐把错误文字压进时间轴 | 对齐前质量门控、失败保留粗时间 |
| 结构窗口切换造成 speaker ID 跳变 | embedding stitching、重叠区约束、允许不确定的新 speaker |
| 自动混合语言过度切换 | LID 滞回、自然停顿边界、术语不触发整段切换 |
| 模型更新破坏行为 | revision pin、完整回归、可回滚 |
| `trust_remote_code` 供应链风险 | 固定提交、哈希、隔离 worker、离线运行 |
| Wayland 全局快捷键限制 | XDG Desktop Portal GlobalShortcuts；IBus 输入源作为稳定兜底 |
| IBus 长听写边界重复 | sample-based overlap、token 时间对齐、稳定前缀机制 |

---

#### 原蓝图第 38 节：完成时应具备的交付物

- 完整源码与可重现 lockfiles。
- 模型注册表、模型安装器和 revision lock。
- Fedora RPM spec 与可安装 RPM。
- 三个 systemd user service。
- IBus component XML、图标和输入法属性菜单。
- WebUI、后端 API 和 OpenAPI 文档。
- Worker 协议文档和契约测试套件。
- 中/日/英课堂与 IBus benchmark runner。
- 时间轴、共识、术语、标点、说话人算法说明。
- 全部数据库迁移。
- TXT/MD/JSON/SRT/VTT/CSV 导出器。
- 故障诊断页与脱敏诊断包。
- 用户手册：安装、模型下载、课堂转录、IBus、词典、校对、导出、排错。
- 固定回归音频与预期结果，包含已发现的循环和时间轴错误。

---

#### 原蓝图第 39 节：最终架构选择摘要

本项目不采用“一个万能模型处理所有问题”，也不采用“多个长文本整体投票”。最终设计是：

```text
课堂：
MOSS-TD 建立谁在何时说话
→ 语言专用最佳模型逐句识别
→ 自动 QA 只复核可疑句
→ 时间对齐的共识和术语纠正
→ 严格标点
→ 通过门控后精细对齐
→ 无人工阻塞自动导出

IBus：
PipeWire + 流式 VAD
→ 常驻低延迟三语模型显示 preedit
→ 自然停顿/安全上限自动分块
→ 需要时语言专用模型最终确认
→ IBus commit_text
```

模型默认不是永久常量。最重要的长期机制是：

```text
统一模型协议
+ 本机 benchmark
+ 每语言/场景校准
+ 可追溯自动决策
+ 严格时间轴
+ 可插拔注册表
```

这能在未来出现更好的中文、日语或英语模型时，只新增 worker 和基准，不需要重写整个软件。

---

#### 原蓝图第 40 节：官方资料与事实来源

以下资料用于本计划中的模型/API事实；访问和核查日期为 2026-09-03。模型发布后仍可能更新，因此实现时必须固定 revision。

1. [FireRedASR2S 官方仓库](https://github.com/FireRedTeam/FireRedASR2S)  
   FireRedASR2 的中/英支持范围、AED 词级时间戳与置信度、FireRedVAD、FireRedLID、FireRedPunc。

2. [Qwen3-ASR 官方仓库](https://github.com/QwenLM/Qwen3-ASR)  
   0.6B/1.7B、52 种语言/方言、离线/流式、ForcedAligner 最长约 5 分钟与 11 种语言。

3. [MOSS-Transcribe-Diarize 0.9B 模型卡](https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize)  
   50+ 语言、最长 90 分钟单次输入、时间戳、说话人、热词和结构输出。

4. [Granite Speech 4.1 2B 模型卡](https://huggingface.co/ibm-granite/granite-speech-4.1-2b)  
   日语 ASR、标点/大小写提示、关键词偏置和约 2B 参数。

5. [ARK-ASR-3B 模型卡](https://huggingface.co/Audio8/ARK-ASR-3B)  
   19 种语言、3B 规模以及模型卡报告的中/英基准。

6. [MOSS-Transcribe-preview-2B 模型卡](https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-preview-2B)  
   英语用途、约 2.4B 参数与 Open ASR Leaderboard 模型卡结果。

7. [Nemotron 3.5 ASR Streaming Multilingual 0.6B](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)  
   600M、缓存式流式、40 locale、中/日/英、原生标点和可调 chunk。

8. [Nemotron Speech Streaming EN 0.6B](https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b)  
   英语缓存式流式、80/160/560/1120 ms chunk、标点与大小写。

9. [Granite Speech 5.0 TurboCTC 470M](https://huggingface.co/ibm-granite/granite-speech-5.0-470m-turboctc)  
   英语 470M CTC、低延迟/高吞吐定位。

10. [Voxtral Mini 4B Realtime 2602](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602)  
    13 种语言、原生流式、可调延迟，以及官方 BF16 服务至少 16 GB GPU 的说明。

11. [VibeVoice-ASR-Streaming 1.5B 模型卡](https://huggingface.co/microsoft/VibeVoice-ASR-Streaming-1.5B)  
    流式说话人归属、10 种语言、热词；该模型在模型卡文件信息中约为 3B 参数。

12. [VibeVoice-ASR-Streaming 技术报告](https://arxiv.org/abs/2609.02812)  
    新发布流式 speaker-attributed ASR 的方法、延迟和研究限制。

13. [IBus.Engine API](https://lazka.github.io/pgi-docs/IBus-1.0/classes/Engine.html)  
    `process_key_event`、`update_preedit_text`、`commit_text`、`update_lookup_table` 等输入法能力。

14. [XDG Desktop Portal GlobalShortcuts](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html)  
    Wayland 下由应用注册全局快捷键会话的标准接口。

15. [IBus 官方仓库](https://github.com/ibus/ibus)  
    IBus 框架和 engine/component 基础。

16. [Fedora Workstation 文档](https://docs.fedoraproject.org/en-US/workstation-docs/)  
    Fedora 桌面所用 systemd、Wayland、PipeWire 等基础环境。

17. [pyannote speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1)  
    说话人分离回退及 exclusive diarization。

---

**文档结束。**

---

## 跨阶段最终签核清单

在阶段 13 结束前，必须再次按下面的顺序做一次全系统签核：

- [ ] 产品合同：课堂与 IBus 两种形态、四种语言模式、自动最佳/手动主模型/严格单模型、忠实/智能/用户文本层、无人工阻塞导出均与阶段 0 一致。
- [ ] 时间轴：全系统仅使用 16 kHz int64 sample index 作为 canonical timeline；没有浮点累积、局部时间未加 offset、倒序、负时间或超范围。
- [ ] 自动质量：循环、静音幻觉、语言脚本错误、异常字符、覆盖不足和时间异常在共识前拦截；无长字符串整体投票。
- [ ] 可追溯性：所有自动采用 token、术语纠正、标点边界、时间来源、模型/版本/参数、fallback 和人工修改都有 provenance/decision_events。
- [ ] GPU：单重型 worker、有序租约、IBus 最高优先、有限 OOM 恢复、显存安全余量和无 CUDA 上下文泄漏。
- [ ] 课堂：90 分钟任务可重启续跑、局部重试、自动完成并直接导出六种格式。
- [ ] IBus：Wayland Portal 快捷键与专用输入源均可用，preedit/commit 正确，连续长听写分块无固定字符裁剪、无重复漏词。
- [ ] 隐私：loopback、本地 token、离线环境变量、无遥测、日志脱敏、麦克风默认不持久化、三级删除均通过测试。
- [ ] 基准：中文/日语/英语与课堂/IBus 分开排名；规范统一；校准训练/验证/测试分离；模型更新后会触发重测。
- [ ] Fedora 发布：RPM、systemd user services、IBus component、desktop、SELinux enforcing、干净机安装/升级/卸载全部通过。
- [ ] 文档与资产：源码、lockfiles、迁移、协议、OpenAPI、算法说明、用户手册、诊断包、gold/回归音频和官方来源齐全。

**执行结论**：只有所有阶段退出条件和本签核清单全部通过，ClassScribe 才可视为完成。若某个候选模型暂不可用，可以在注册表中明确禁用并记录原因，但不得删除对应能力、接口、基准入口或回滚机制。
