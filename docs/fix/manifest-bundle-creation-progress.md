# Model Manifest Bundle 分阶段开发与进度计划

> 文档状态：阶段 A～F、全量矩阵和工程验收均已闭合；用户明确授权的 release-blocking waiver 已经
> source/RPM/攻击矩阵验证，动态状态为 `ready_with_waivers` 且退出码 0。`source_license`、
> `phase12_acceptance`、`desktop_matrix` 三项原始检查继续为失败，不得表述为真实证据通过。
>
> 建立日期：2026-09-04
>
> 事实基线：2026-09-04 的仓库状态；上游事实仍以 registry 的 `facts_as_of: 2026-09-03` 为准
>
> 原始设计来源：`docs/fix/manifest-bundle-creation.md`
>
> 使用方式：本文件是后续实施、验收和进度记录的唯一工作计划；执行时无需再参照原始设计来源。

## 1. 计划目标与执行纪律

本计划负责把 ClassScribe 模型 manifest bundle 从缺失状态修复到可发布、可安装、可审计状态。最终结果
不是简单提交若干 JSON，而是一条确定性的供应链：发布工程在联网环境中从固定 Hugging Face commit
生成并复核 manifest；应用运行时只读取随版本发布的只读 bundle；用户明确确认后按 manifest 下载模型；
只有真实离线健康检查通过后才激活 revision。

本文列出的 `discover`、`generate`、`verify` 等生成和验证命令是计划新增的接口。在对应实现合并前，
它们不是当前仓库已有能力，不得把命令示例误记为已经可用。

### 1.1 状态定义

- `未开始`：没有实施证据；文档设计或已有相邻能力不算完成。
- `进行中`：已有实现或验证工作，但仍有未完成任务或退出条件。
- `阻塞`：存在明确外部条件、授权、硬件或前置合同阻塞；必须记录原因、影响和解除条件。
- `已完成`：本阶段全部任务、交付物、测试和退出条件均有可复核证据。
- `需回归`：后续变更使原证据失效；完成回归前不得继续视为已完成。

### 1.2 进度记录规则

1. 阶段完成以退出条件为准，不以代码已提交、文件已生成或 manifest 存在为准。
2. 每完成一个任务，勾选对应任务 ID，并在该阶段的“进度记录”表追加日期、提交或变更、验证命令、
   结果和遗留问题；不得覆盖旧记录。
3. 联网发现/生成、离线验证、真实模型健康检查和 RPM installed-tree 验证必须分别留证，不能用单元测试
   相互替代。
4. 真实模型证据必须记录 model ID、固定 revision、manifest SHA、目标 Fedora 环境、CPU/GPU 路由、
   健康音频、结果和日期；不得伪造 benchmark、显存或准确率结论。
5. 凭证、Authorization header、HF token、用户名、本机绝对路径和 cache 路径不得进入本文件或测试日志。
6. 后续阶段若修改 registry、revision lock、license inventory、worker lock、selection、schema、bundle、
   loader 或安装合同，必须把受影响阶段标记为 `需回归` 并重新运行相应门禁。
7. 任何无法满足的条目必须明确登记为阻塞项，不能静默跳过，也不能因 manifest 已生成而放行发布。

### 1.3 总体进度

| 阶段 | 状态 | 开始日期 | 完成日期 | 当前结论 |
|---|---|---|---|---|
| 阶段 A：合同修复 | 已完成 | 2026-09-04 | 2026-09-06 | 新 Nemotron bundle 下全仓 477 项行为、真实 RPM 生命周期、Ruff、严格 mypy、architecture 与 diff 门全部通过 |
| 阶段 B：发布工具 | 已完成 | 2026-09-04 | 2026-09-06 | Nemotron新lock对应的第三次约140GB双clean generation、21个production JSON发布、独立验证与B-01～B-35完整回归均通过 |
| 阶段 C：小模型端到端 | 已完成 | 2026-09-05 | 2026-09-06 | 新 production bundle 下以保留的 Whisper active/legacy 环境完成独立断网新 core 真实重启健康回归 |
| 阶段 D：API 与 WebUI | 已完成 | 2026-09-05 | 2026-09-06 | 新 production bundle 下既定 66 项后端与 Node 24 前端五门全部通过 |
| 阶段 E：生产模型 bundle | 已完成 | 2026-09-05 | 2026-09-06 | 20项bundle及当前14个实际使用模型CPU首次/独立断网健康均闭合；GPU子矩阵因driver不可用继续独立fail closed，不以CPU冒充 |
| 阶段 F：发布闭环 | 已完成 | 2026-09-06 | 2026-09-06 | release inventory/check、双bundle RPM installed tree、文档、离线verifier及489项根矩阵全部闭合；发布仍因源码许可、Phase 12和桌面门blocked |

### 1.4 依赖主链

```text
阶段 A：合同修复
  ↓
阶段 B：发布工具
  ↓
阶段 C：Whisper Tiny 小模型端到端
  ↓
阶段 D：API 与 WebUI
  ↓
阶段 E：20 模型生产 bundle 与真实验收
  ↓
阶段 F：发布、RPM、文档与最终门禁
```

第一批提交可以按第 14 节所列范围组织，但不能改变上述阶段退出条件。特别是“单模型 bundle”只可用于
阶段 C 的受控 fixture/测试注册表验证；生产 bundle 始终必须恰好覆盖正式 registry 的 20 个 model ID，
且在阶段 E 完成前不得宣称生产 bundle 完整。

## 2. 当前基线与问题定义

### 2.1 已有能力

当前代码已经具备以下基础，实施时应复用而不是另建平行机制：

- `config/model-registry.v1.yaml` 登记 20 个模型，包含固定 repository、40 字符 commit、worker、
  trust policy、许可证关联和 enabled 状态。
- `config/model-revisions.lock.json` 冻结模型身份与 revision。
- `config/model-licenses.v1.json` 冻结许可证披露和 gated 条款要求。
- `protocol/schema/v1/model-manifest.schema.json` 定义单模型 manifest v1。
- `ModelManager` 实现一次性确认 token、磁盘预检、staging、逐文件哈希校验、供应链审计、真实健康
  检查、原子激活、回滚和删除。
- `HuggingFaceDownloader` 只下载 manifest 指定的固定 revision 文件，并限制 HTTPS host 和 redirect。
- RPM 会整体复制 `config/`，因此未来加入 `config/model-manifests/` 后无需单独维护模型权重包。

### 2.2 当前缺失与合同矛盾

1. 仓库中没有任何生产单模型 manifest，也没有 bundle 索引。
2. 没有联网的发布期生成器、离线 bundle verifier 或模型文件选择策略。
3. WebUI 要求用户从文件系统上传任意 manifest JSON；安装 API 接收整份 manifest，而不是加载发布方
   冻结的 bundle。
4. CLI 没有模型 manifest 生成、校验或批量状态检查入口。
5. `release/release-manifest.v1.json` 没有把单模型 manifest 或 bundle 索引列为必须工件，发布检查也
   没有验证 registry、revision lock、license inventory 与 manifest bundle 的一一对应关系。
6. 开发进度文档声称 20 个模型的逐文件 manifest 已冻结，但实际工件不存在。
7. `ManifestFile` 要求 `kind=remote_code` 的文件必须位于 `code/`，而 downloader 同时把同一个 `path`
   用作上游路径和本地安装路径。MOSS Preview 等 worker 又按上游原始布局从模型根读取代码和模板。
   这三个合同目前互相冲突。
8. 注册表中的部分 disabled 模型没有真实推理实现，不能通过现有 load → infer → unload 健康检查；
   即使有 manifest，也不能被宣传为可安装模型。

### 2.3 修复范围

本计划负责：

- manifest/bundle 格式、生成、验证和发布；
- remote-code 文件合同修复；
- 内置 manifest 的后端加载和安装 API 接入；
- WebUI 模型安装入口改造；
- RPM、release check、测试和文档同步；
- 对 enabled 模型进行真实安装健康验收。

本计划不负责：

- 下载或提交模型权重到 Git/RPM；
- 绕过 gated 模型条款；
- 为尚无实现的 FireRedASR2-LLM、Voxtral 或 VibeVoice 补写推理 worker；
- 伪造本机 benchmark、显存或准确率结论；
- 把模型安装并入普通任务恢复或运行时自动下载。

## 3. 目标工件与不可变数据合同

### 3.1 最终目录

完成后必须具备以下目录和文件：

```text
config/
├── model-file-selection.v1.yaml
└── model-manifests/
    └── v1/
        ├── bundle.v1.json
        ├── moss_td_0_9b.json
        ├── firered_asr2_aed.json
        ├── firered_asr2_llm.json
        ├── firered_vad.json
        ├── firered_lid.json
        ├── firered_punc.json
        ├── granite_speech_4_1_2b.json
        ├── qwen3_asr_1_7b.json
        ├── qwen3_asr_0_6b.json
        ├── qwen3_forced_aligner_0_6b.json
        ├── ark_asr_3b.json
        ├── moss_transcribe_preview_2b.json
        ├── nemotron_3_5_asr_streaming_0_6b.json
        ├── nemotron_speech_streaming_en_0_6b.json
        ├── granite_speech_5_0_turboctc_470m.json
        ├── voxtral_mini_4b_realtime_2602.json
        ├── vibevoice_asr_streaming_1_5b.json
        ├── pyannote_community_1.json
        ├── fun_asr_nano_2512.json
        └── whisper_tiny_reference.json
```

bundle 必须覆盖注册表全部 20 个模型，以保证供应链 inventory 完整。普通用户能否安装仍由注册表
`enabled`、worker 实现状态和额外安装策略共同决定；不得仅因“manifest 存在”推导模型可运行或可安装。

### 3.2 Remote code 合同：保留上游原始布局

本轮固定采用以下 v1 方案：

- `ManifestFile.path` 同时表示上游 repository 内路径和安装 revision 内路径；
- 所有文件必须保留上游原始相对布局；
- `kind=remote_code` 可以位于任意安全相对路径，不再强制 `code/` 前缀；
- remote code 的识别依赖显式 `kind`、逐文件 SHA-256、manifest SHA-256 和安装审计；
- worker 仍只在无网络 Bubblewrap 中读取单个只读模型 revision。

这是 v1 的最小兼容修复。若未来必须维持 `code/` 审计目录，应设计 manifest v2，分离
`source_path`、`audit_path` 和 `runtime_path`，并为运行时物化副本增加独立哈希；该 v2 方案不在本轮采用。

本轮必须同步修改：

- `backend/classscribe/models/manager.py`：删除 remote-code 路径前缀限制；
- `protocol/schema/v1/model-manifest.schema.json`：文档描述明确 `path` 保留上游布局；
- `docs/model-installation.md`：删除“remote code 必须位于 code/”的陈述；
- 相关 manager/download/schema 合同测试。

### 3.3 发布工具与运行时依赖隔离

`huggingface_hub` 只用于联网发布工具，不得加入 core 或 worker 的生产依赖。新增独立工具工程：

```text
tools/model-manifest/
├── pyproject.toml
├── uv.lock
└── classscribe_manifest_tool/
    ├── __init__.py
    ├── cli.py
    ├── generator.py
    ├── selection.py
    └── verifier.py
```

根目录薄脚本可以转发到该工具，但不能复制实现。该工具的 lock 必须进入 release dependency lock 检查。

### 3.4 人工策展的文件选择合同

生成器不得根据扩展名自动决定模型 payload。上游仓库常同时包含 PyTorch、ONNX、GGUF、不同精度、
多个 checkpoint 或训练材料；盲目抓取整个仓库会造成巨大浪费或错误 runtime。

`config/model-file-selection.v1.yaml` 必须为每个模型定义人工审核的精确 include 集合。pattern 只允许用于
首次发现，冻结输出必须展开成精确路径。建议格式：

```yaml
schema_version: 1
models:
  qwen3_asr_0_6b:
    include:
      - config.json
      - generation_config.json
      - preprocessor_config.json
      - tokenizer.json
      - tokenizer_config.json
      - model.safetensors.index.json
      - model-00001-of-00002.safetensors
      - model-00002-of-00002.safetensors
    kinds:
      config.json: config
      generation_config.json: config
      preprocessor_config.json: config
      tokenizer.json: tokenizer
      tokenizer_config.json: tokenizer
      model.safetensors.index.json: model
      model-00001-of-00002.safetensors: model
      model-00002-of-00002.safetensors: model
    exclude: []
```

上述文件名只展示格式；实际 selection 必须以固定 revision 的真实 tree 和实际 worker 加载行为为准。

### 3.5 单模型 manifest v1 合同

继续使用 manifest v1 核心字段：

```json
{
  "manifest_version": 1,
  "model_id": "qwen3_asr_0_6b",
  "repository": "Qwen/Qwen3-ASR-0.6B",
  "revision": "5eb144179a02acc5e5ba31e748d22b0cf3e303b0",
  "worker": "qwen",
  "license_id": "Apache-2.0",
  "license_url": "https://huggingface.co/Qwen/Qwen3-ASR-0.6B",
  "requires_terms_acceptance": false,
  "trust_remote_code": false,
  "estimated_download_bytes": 0,
  "installed_size_bytes": 0,
  "environment": {
    "python": "3.12",
    "runtime_backend": "qwen_asr_transformers",
    "dtype": "bfloat16",
    "worker_lock": "workers/qwen/uv.lock",
    "worker_lock_sha256": "ef7d017bad97b5f521a4c137d7fec50bfc8dfdcc454a11a6699a111ba29809ce"
  },
  "files": []
}
```

示例中的空 `files` 和零大小只用于展示字段；生产生成器必须保证：

- `revision` 是注册表和 revision lock 中相同的完整 commit；
- repository、worker、trust policy 与注册表完全一致；
- license 三元组与许可证 inventory 完全一致；
- `files` 非空、按 Unicode 路径升序排列且无重复；
- `installed_size_bytes` 等于所有文件大小之和；
- 当前 downloader 没有额外压缩/转换阶段，因此 `estimated_download_bytes` 初始也取该和；
- environment 全部是字符串，至少披露 Python、runtime backend、dtype、worker lock 路径和 lock SHA；
- JSON 使用 UTF-8、LF、排序 key、固定缩进并以单个换行结束，从而产生可复现 manifest SHA。

### 3.6 Bundle 索引合同

新增 `protocol/schema/v1/model-manifest-bundle.schema.json`，索引格式：

```json
{
  "schema_version": 1,
  "registry_revision": 1,
  "facts_as_of": "2026-09-03",
  "generated_at": "2026-09-04T00:00:00Z",
  "generator": {
    "name": "classscribe-model-manifest",
    "version": "1",
    "lock_sha256": "<64 lowercase hex>"
  },
  "manifests": [
    {
      "model_id": "qwen3_asr_0_6b",
      "path": "qwen3_asr_0_6b.json",
      "sha256": "<64 lowercase hex>"
    }
  ]
}
```

`generated_at` 只记录生成批次时间，不参与文件内容选择。对同一批次重新生成以验证可复现性时，必须通过
显式 `SOURCE_DATE_EPOCH` 固定它。

生产索引必须满足：

- 恰好包含 registry 的 20 个 model ID；
- 每个 ID 唯一，按 model ID 排序；
- path 是 bundle 目录内的安全单层文件名；
- SHA-256 对应 manifest 规范化后的实际字节；
- 不携带 token、用户名、本机路径、cache 路径或下载者身份。

## 4. 阶段 A：合同修复

状态：**已完成**

### 4.1 阶段目标

修复 remote-code 的三方合同冲突，冻结 bundle schema 与后端数据模型，并用基础单元测试证明原始布局
兼容性与路径安全性可同时成立。

### 4.2 进入条件

- [x] 第 2 节基线已经重新核对，确认本阶段没有被其他变更部分实现。
- [x] v1 继续采用第 3.2 节“保留上游原始布局”决策；不引入 manifest v2。

### 4.3 实施任务

- [x] `A-01` 修改 `backend/classscribe/models/manager.py`，删除 `kind=remote_code` 必须位于 `code/`
  的限制；继续拒绝空路径、绝对路径、`..`、审计保留文件名及其他不安全路径。
- [x] `A-02` 保持 `ManifestFile.path` 同时作为上游路径和本地 revision 路径，不进行重写、搬移或复制；
  保持 `trust_remote_code=true` 必须至少有一个显式 `remote_code` 文件，反向也必须成立。
- [x] `A-03` 更新 `protocol/schema/v1/model-manifest.schema.json` 的说明，明确 `path` 保留上游相对布局；
  schema 与 Python 模型必须继续一致。
- [x] `A-04` 新增 `protocol/schema/v1/model-manifest-bundle.schema.json`，编码第 3.6 节全部字段、排序、
  唯一性、安全单层路径和 SHA 格式约束；无法仅由 JSON Schema 表达的全量对应关系交给 loader/verifier。
- [x] `A-05` 在 `backend/classscribe/models/manifests.py` 建立 `ManifestBundleIndex`、
  `ManifestBundleEntry` 等只读数据模型的基础合同；完整加载与注入在阶段 C 完成。
- [x] `A-06` 更新 `docs/model-installation.md`，删除旧 `code/` 前缀要求，写明 remote code 依靠 kind、
  逐文件哈希、manifest SHA、供应链审计和无网络 Bubblewrap 隔离。
- [x] `A-07` 扩展 manager/download/schema 合同测试：安全根级或嵌套上游 remote-code 路径可通过；
  绝对路径、`..`、symlink、control filename、重复路径等攻击仍失败。
- [x] `A-08` 为 bundle schema/数据模型增加基础测试，覆盖重复 ID、非法 SHA、非单层 path、路径逃逸、
  缺少字段、额外字段和排序错误。

### 4.4 交付物

- [x] 更新后的 manifest v1 路径合同和文档。
- [x] bundle v1 JSON Schema。
- [x] bundle index/entry 基础数据模型。
- [x] manager/download/schema/bundle 合同测试。

### 4.5 验证要求

- [x] manifest 文件安全路径、排序、重复和 SHA/size 校验均有测试。
- [x] remote code 可保留安全的上游根路径或嵌套路径。
- [x] 绝对路径、`..`、symlink 和 control filename 仍被拒绝。
- [x] 现有单元、集成、schema/contract 回归全部通过。
- [x] `git diff --check` 通过。

### 4.6 退出条件

现有测试通过；新增测试证明 remote code 能保留原始安全布局，同时所有路径攻击仍被拒绝。

### 4.7 进度记录

| 日期 | 状态/任务 ID | 变更或结论 | 验证命令与结果 | 提交/证据 | 阻塞与下一步 |
|---|---|---|---|---|---|
| 2026-09-04 | 未开始 | 仅建立执行计划，未修改实现 | 未运行实施验证 | 本文件 | 从 `A-01` 开始 |
| 2026-09-04 | 完成 `A-01`；阶段进行中 | 删除 `ManifestFile` 对 `remote_code` 的 `code/` 前缀限制，并将缺少 remote-code 的错误改为布局无关措辞；重新核对基线，确认 bundle schema、loader、selection 和 manifests 仍不存在，v1 保留上游原始布局 | `env UV_CACHE_DIR=/tmp/classscribe-uv-cache uv run python -c "..."`：通过，根级 `modeling.py` 与嵌套 `pkg/modeling.py` 均可构造为 `remote_code` | 工作树差异；未提交 | 下一步重新读取并执行 `A-02`；正式攻击面覆盖在 `A-07` 完成 |
| 2026-09-04 | 完成 `A-02`；阶段进行中 | 核对并保持 downloader URL、目标 revision 路径和审计 manifest 均直接使用 `ManifestFile.path`，无重写/搬移/复制；`trust_remote_code` 与显式 `remote_code` 的双向约束保持不变 | 定向 pytest 首次因错误文本断言不兼容为 `1 failed, 2 passed`，调整为布局无关且兼容的措辞后重跑：`3 passed in 0.19s` | `backend/classscribe/models/manager.py`；未提交 | 下一步重新读取并执行 `A-03` |
| 2026-09-04 | 完成 `A-03`；阶段进行中 | manifest v1 schema 的 `files[].path` 说明现明确：路径从上游原样保留并在安装 revision 中不变使用，`remote_code` 可位于任意安全根级或嵌套路径 | `uv run python -m json.tool protocol/schema/v1/model-manifest.schema.json`：通过；`uv run pytest tests/contracts/test_protocol_schema_alignment.py -q`：`3 passed in 0.17s` | `protocol/schema/v1/model-manifest.schema.json`；未提交 | 下一步重新读取并执行 `A-04` |
| 2026-09-04 | 完成 `A-04`；阶段进行中 | 新增 bundle v1 JSON Schema，严格限定版本、registry revision、日期/时间、固定 generator 身份、lock/member SHA、成员字段及安全单层 JSON 文件名；声明按 model ID 唯一和 Unicode 排序，跨项语义留给数据模型/verifier 强制 | `uv run python -m json.tool protocol/schema/v1/model-manifest-bundle.schema.json`：通过；约束探针首次因 shell 展开 `$defs` 为 `KeyError`，修正引用后通过，确认合法成员名接受且逃逸、嵌套路径、保留索引名拒绝 | `protocol/schema/v1/model-manifest-bundle.schema.json`；未提交 | 下一步重新读取并执行 `A-05` |
| 2026-09-04 | 完成 `A-05`；阶段进行中 | 新增 frozen/extra-forbid 的 `ManifestBundleEntry`、`ManifestBundleGenerator`、`ManifestBundleIndex` 并导出；强制安全字段、固定 generator、UTC 时间、成员按 ID 排序、ID/path 唯一；完整文件加载和交叉合同仍留待阶段 C | 初次验证发现 Pydantic 不支持 lookahead 且导出排序不符 ruff，改为 schema `not` + Python validator 并整理导出后：ruff 通过；mypy 通过；只读模型构造探针通过；bundle schema JSON 解析通过 | `backend/classscribe/models/manifests.py`、`backend/classscribe/models/__init__.py`、bundle schema；未提交 | 下一步重新读取并执行 `A-06` |
| 2026-09-04 | 完成 `A-06`；阶段进行中 | 安装文档删除 `code/` 前缀要求，明确 path 同时表示上游和安装位置且不重写，并记录显式 kind、逐文件 SHA、manifest SHA、供应链审计和无网络 Bubblewrap 的 remote-code 信任边界 | 旧前缀要求检索：无匹配（`rg` exit 1，符合预期）；新合同五项检索：命中；`git diff --check`：通过 | `docs/model-installation.md`；未提交 | 下一步重新读取并执行 `A-07` |
| 2026-09-04 | 完成 `A-07`；阶段进行中 | 强化 manifest/Python 路径合同为规范 POSIX 相对路径，拒绝绝对、`..`、`.`、重复分隔、反斜杠、尾斜杠、控制字符、超长和审计 control filename；增加 kind、Unicode 排序、重复路径双向 trust 约束及 downloader/payload symlink 测试；根级与嵌套 remote code 完整安装后保持原路径 | `uv run pytest tests/unit/test_model_manager.py tests/unit/test_model_download.py tests/contracts/test_model_manifest_schemas.py -q`：`36 passed in 0.25s`；ruff 通过；mypy 通过；`git diff --check` 通过 | manager、manifest schema、3 个测试文件及 API fixture；未提交 | 下一步重新读取并执行 `A-08`，随后按阶段 A 全量门禁回归 |
| 2026-09-04 | 完成 `A-08`；阶段进行中 | 增加 bundle schema/冻结模型合同测试，覆盖有效只读索引、重复 ID/path、generator/member 非法 SHA、非单层/逃逸/保留路径、缺字段、额外字段和排序错误 | 定向 pytest 首次因测试插入位置错误为 `1 failed, 10 passed`，修正后 `11 passed in 0.15s`；ruff 通过；mypy 通过 | `tests/contracts/test_model_manifest_schemas.py`；未提交 | 下一步重新核对阶段 A 交付物、验证要求与退出条件并执行全量门禁 |
| 2026-09-04 | 阶段 A 门禁阻塞 | 更新 architecture checker 识别 bundle 的 `schema_version`；确认 sandbox 会使最小 `asyncio.to_thread` 探针退出挂起，改在沙箱外回归；全部实现与非 RPM 测试已通过 | 沙箱外全量：`413 passed, 1 skipped, 1 failed`，唯一失败为 RPM 生命周期的 `.rpm.lock` 权限；单元/schema/contract：`330 passed in 9.05s`；非 RPM 集成：`83 passed, 1 skipped, 1 deselected in 5.68s`；ruff 全仓通过；mypy `220 source files` 通过；`git diff --check` 通过 | 测试输出与工作树；未提交 | Fedora RPM 6.0.2 即使自定义可写 dbpath 也拒绝非 root transaction；`sudo -n` 要求密码。需在具备 root 的目标/CI 环境运行 `test_rpm_install_upgrade_remove_preserves_user_data`，通过后才能完成阶段 A 并进入 B |
| 2026-09-04 | 阶段 A 已完成 | 保持原始 RPM 测试和真实事务不变，以非特权用户命名空间将当前用户映射为命名空间内 root，解除宿主 RPM 6 对事务锁的环境限制；阶段 A 全部任务、交付物、测试与退出条件闭环 | `unshare --user --map-root-user env UV_CACHE_DIR=/tmp/classscribe-uv-cache uv run pytest tests/integration/test_stage3_packaging.py::test_rpm_install_upgrade_remove_preserves_user_data -q`：`1 passed in 7.00s`；结合上一条已通过的 `330` 个单元/schema/contract 与 `83` 个非 RPM 集成用例，现有测试集合全部有通过证据；全仓 ruff、mypy 与 `git diff --check` 保持通过 | 原始 RPM 生命周期测试输出与既有回归输出；未提交 | 无；进入阶段 B，先重新读取并执行 `B-01` |
| 2026-09-05 | 阶段 A 标记需回归 | C-08 为保证 loader 返回的 manifest 深层只读，将 environment 冻结为只读 mapping，并相应把 `ModelManifest.as_dict()` 改为显式生成公开副本；该变更触及 A 阶段的 manager/manifest 序列化合同，旧证据暂不单独沿用 | 首次只读测试暴露 `dataclasses.asdict()` 不能 deepcopy `mappingproxy`，结果为 `1 failed, 33 passed`；修复后进入回归 | manager 序列化与 loader 只读变更；未提交 | 重跑 manager/download/schema/API 受影响门禁，全部通过后恢复阶段 A |
| 2026-09-05 | 阶段 A 回归完成 | `ModelManifest.as_dict()` 继续输出与原合同相同的完整可变字典副本，loader 内存对象则保持不可变；remote-code、安全路径、download、schema 与 API 序列化行为未退化 | manager/download/manifest schema/protocol alignment `48 passed`；API integration 默认沙箱在四项后因既知 asyncio thread 限制挂起并终止，按既有边界在沙箱外原测试 `6 passed in 0.86s`；ruff、严格 mypy 与 `git diff --check` 通过 | 受影响 A 合同回归输出；未提交 | 阶段 A 恢复已完成；继续 C-08 登记 |
| 2026-09-05 | 阶段 A 标记需回归 | C-11 在服务层新增 bundle-only 预检并将既有用户-manifest 预检抽取为共享校验函数，触及已有安装 API 行为；阶段 D 尚未开始，不能假定旧接口仍通过 | C-11 新路径定向通过；原公开安装路径待单独回归 | service 安装预检重构；未提交 | 在既有 API 上重跑 request→confirm→health 原测试后恢复 |
| 2026-09-05 | 阶段 A 回归完成 | 共享预检函数保持旧公开接口的二次确认、下载、健康检查和激活行为，C-11 的新 bundle-only 入口未使既有安装合同退化 | 原 `test_model_install_requires_second_confirmation_and_runs_health_inference` 在沙箱外通过：`1 passed in 0.44s`；C-11/loader 组合 `16 passed`；静态检查与 diff 门通过 | 原安装 API 回归及 C-11 新测试；未提交 | 阶段 A 恢复已完成；继续 C-11 登记 |
| 2026-09-05 | 阶段 A 标记需回归 | C-14 的真实 fresh-XDG provisioning 证明共享 `uv sync --frozen --no-editable` 合同不能构建复制后的本地 `classscribe-protocol`：隔离 build environment 缺少 Hatchling，且 uv 的 extra build dependencies 配置需要显式 preview；已为全部 worker 声明相同 protocol 构建依赖，并让 provisioner 启用 preview | 两次真实 C-14 尝试均在进入 Bubblewrap 前失败，错误为 `ModuleNotFoundError: hatchling`；第二次还确认 uv 未启用 preview 时不会应用该配置；当前 worker lock 尚无工作树差异，完整 lock/hash 与受影响门禁待验证 | `backend/classscribe/models/environment.py`、全部 worker `pyproject.toml`、环境单测；未提交 | 先逐 worker 执行 frozen lock 检查并核对 registry lock SHA，再回归环境/健康/manager 与静态门；全部通过后才能恢复阶段 A 并继续真实 C-14 |
| 2026-09-05 | 阶段 A 回归完成 | 统一 provisioner 现以 preview 应用每个 worker 显式声明的 `classscribe-protocol` Hatchling 构建依赖；`--frozen --no-dev --no-editable` 保持，11 个 worker resolution 与 lock bytes 均未变化，registry 及 Whisper manifest 的 lock pin 继续精确匹配 | 11 个 `uv lock --check --project workers/<id>` 全部通过；实际 SHA 与 registry 全量匹配；环境单测 `2 passed`；默认沙箱组合测试在前 5 项后因既知 asyncio 边界挂起并终止、不计结果，随后在既有沙箱外边界运行原 health/inference/resident/manager 测试 `46 passed`；ruff、严格 mypy 与 `git diff --check` 通过 | provisioner、11 个 worker build 配置、原运行时测试与 lock/hash 输出；未提交 | 阶段 A 恢复已完成；继续 C-14 的真实 fresh-XDG provisioning 与断网 Bubblewrap 生命周期，不以本次单元回归替代真实证据 |
| 2026-09-05 | 阶段 A 再次标记需回归 | 第三次真实 C-14 证明前一条单元/lock 回归没有覆盖 uv 配置发现语义：统一环境的 `UV_NO_CONFIG=1` 会禁用 worker `pyproject.toml` 中的 extra build dependency，因此真实 `--no-editable` 构建仍缺少 Hatchling | 真实固定 revision 下载再次成功；`uv sync --frozen --no-dev --no-editable --preview` 仍以 `ModuleNotFoundError: hatchling` 失败，未进入 Bubblewrap；protocol 自身单独 PEP 517 build 在 no-config 与正常配置下均成功，说明故障限定于 sync 本地路径依赖路径 | 第三次真实 C-14 输出、uv 官方配置语义与本地构建探针；未提交 | 改由 provisioner 生成并显式指定唯一可信 `uv.toml`，移除会禁用该文件的 `UV_NO_CONFIG`；重跑真实 provisioning 与全部受影响门禁后才能恢复阶段 A |
| 2026-09-05 | 阶段 A 最终回归完成 | 最终实现由 uv 依据 frozen project/`requires-python` 创建目标 3.12 环境，使用只包含 protocol build dependency 的显式可信 `uv.toml` 替代全部配置发现，sync 后把已解析的同版本解释器原子复制为非 symlink；sandbox 同时支持 `bin/python + worker.py` 与 production `.venv/bin/python + worker.py` 两种受限布局。真实 C-14 已验证该组合可用 | 最终受影响原测试：worker environment、sandbox、health、inference、resident、manager、worker process 共 `64 passed in 1.49s`；ruff、严格 mypy、`git diff --check` 通过；worker pyproject/lock 均无差异，既有 11 lock checks 与实际 SHA 对照继续有效 | 最终 provisioner/sandbox/health 实现、真实 C-14 和原回归输出；未提交 | 阶段 A 恢复已完成；按顺序重新读取并执行 C-15 |
| 2026-09-05 | 阶段 A 标记需回归 | C-16 在更长但合法的 fresh-XDG runtime 根重启时，health checker 以 `health-<model_id>-<uuid>` 构造 AF_UNIX socket，超过内核路径上限；原短路径测试与 C-14/C-15 未覆盖该边界 | 新 core 进程在独立无网络 user/net namespace 中先完成 active、supply-chain、manifest、四文件及 aggregate 离线复验，但 worker transport 以 `AF_UNIX path too long` 失败，未执行重启后 inference | C-16 首次真实输出；未提交 | 用不含 model ID 的短随机 socket/output identity 修复，补充长 runtime 根回归并重跑 health/worker-process 门后恢复阶段 A |
| 2026-09-05 | 阶段 A 长路径 transport 回归完成 | health 临时名称移除 model ID 并缩短；WorkerProcess 从已打开的受限 runtime 目录 fd 构造短 procfs client alias，sandbox worker 仍只使用内部短路径，且 host 目录 fd 不传入 Bubblewrap。真实长 host path RPC 与 C-16 重启均通过 | 环境、sandbox、health、inference、resident、manager、worker-process 最终组合 `66 passed in 1.57s`；ruff、严格 mypy（4 source files）和 `git diff --check` 通过；worker locks 与 pyproject 无变化 | 最终 transport/health 实现、长路径集成测试、C-16 真实输出；未提交 | 阶段 A 恢复已完成；重新读取并执行 C-17 |
| 2026-09-05 | 阶段 A 标记需回归 | D-01 按冻结的新合同把 `ModelInstallRequest` 从客户端 manifest 改为空对象，并让公开 route 调用 bundle-only 预检；这会按预期使原“上传 manifest”API 测试失效，因此既有 API 合同证据不能继续视为当前通过 | 新定向测试尚待运行；原测试待在 D-03 更新为“拒绝 manifest”后全量回归 | D-01 schema/route 与 API test；未提交 | 先完成 D-01～D-03 的安全合同和测试，再重跑 API/manager/architecture 门并恢复阶段 A |
| 2026-09-05 | 阶段 A API 合同回归完成 | 原二阶段安装回归已迁移到新的 bundle-only 空对象合同；确认 token、真实 manager 下载/验证/health/active 行为不变，同时任意客户端 manifest 在 schema 层拒绝。完整受影响 API、manager 与 architecture tests 全部通过 | `tests/integration/test_api_v1.py`、`tests/unit/test_model_manager.py`、architecture 共 `39 passed in 4.25s`；ruff、严格 mypy（9 source files）与 `git diff --check` 通过 | D-01～D-03 实现与原合同回归；未提交 | 阶段 A 恢复已完成；继续 D-04 |

| 2026-09-05 | 阶段 A 标记需回归 | TurboCTC 固定 `preprocessor_config.json` 的 `auto_map` 明确引用 `processing_ctc_conformer.CtcConformerProcessor`，但 registry 冻结 `trust_remote_code=false`；这违反 manifest remote-code 双向合同与阶段 E 通用闭包规则 | 固定 commit 小文件下载成功；JSON 解析确认 auto_map；尚未修改 registry 或运行回归 | `/tmp` Granite 审阅文件与 registry 对照；未提交 | 将该模型 trust policy 修正为 true，并重跑 registry/schema/contracts、manager、release 与架构门；通过前阶段 A 不恢复 |
| 2026-09-05 | 阶段 A 回归完成 | `granite_speech_5_0_turboctc_470m` 的 registry `trust_remote_code` 修正为 true，与固定 preprocessor auto_map 和 manifest remote-code 双向合同一致；revision、worker、enabled 与当前 worker_not_implemented 状态均未改变 | 首轮合同/registry/manager/release/architecture `52 passed`；完整 contracts+registry/loader/support/manager/release `80 passed in 1.85s`；Ruff、严格 mypy `123 source files`、architecture `1 passed` 与 `git diff --check` 全通过 | registry trust policy、固定内容审计与阶段 A 回归输出；未提交 | 阶段 A 恢复已完成；依赖顺序进入阶段 B，完成 Granite selection 与工具全回归 |

| 2026-09-05 | 阶段 A 标记需回归 | E-PY-04 已被真实固定内容触发：Community-1 的 `embedding/README.md` 明确指向外部 Hugging Face repository，不能继续用 manifest 单一 repository/license 表达全部来源。拟为 manifest v1 增加可选、严格排序的 component-source provenance，保留主 repository 作为实际下载来源；单来源 manifest 字节不改变 | gated tree/config/README 下载成功；embedding 外部来源说明与历史 SHA 审计完成；尚未修改 schema/模型 | pyannote 固定文本、source API 与影响面审计；未提交 | 先实现 schema 与运行时不可变模型的路径/revision/license/唯一性约束及攻击测试；通过前 A 不恢复 |

| 2026-09-05 | 阶段 A component-source 回归失败 | 可选多来源模型与 schema 攻击测试已加入；首轮定向测试发现 `ModelManifest.as_dict()` 对嵌套 component files 使用 `asdict` 后保留 tuple，而 `from_dict()` 只接受 JSON array，导致模型自身序列化结果不能回读。其余路径、身份、排序、唯一性与 copied/derived 语义测试通过 | `uv run pytest tests/unit/test_model_manager.py tests/contracts/test_model_manifest_schemas.py -q`：`1 failed, 52 passed`；失败为 `test_manifest_component_source_is_frozen_round_trippable_and_audited` | manager/schema/tests 与失败输出；未提交 | 显式把嵌套 files 序列化为 JSON list，重跑同一门；通过前不恢复 A |

| 2026-09-05 | 阶段 A component-source 行为修复；静态门失败 | component source 嵌套 files 已显式序列化为 JSON list，自身 round-trip、安装审计与全部攻击测试通过；定向 Ruff 随后发现 models package 新导出的三个类型未按字母序排列，组合命令因此未执行 mypy | 定向 pytest：`53 passed`；Ruff：`I001` 1 项，mypy 未运行 | manager/models export 与门禁输出；未提交 | 只调整 import 顺序，随后重跑 Ruff、mypy、pytest 与 diff；通过前不恢复 A |

| 2026-09-05 | 阶段 A component-source 合同实现完成；待完整回归 | manifest v1 增加仅在非空时输出的严格 component source provenance；运行时不可变模型验证固定 source repository/revision、relationship、license、路径/hash/size、排序/唯一性、installed-file 归属及 copied 字节一致性。单来源 manifest 输出保持无新增字段；安装 audit 原样保留 provenance | 定向 pytest：`53 passed`；Ruff、mypy（2 files）、`git diff --check` 全通过 | schema、manager 模型、exports 与攻击测试；未提交 | A 仍保持需回归，待 B/C/D 实现稳定后运行阶段 A 全量门；下一步实施 B 的冻结输入与 generator/verifier 交叉验证 |

| 2026-09-05 | 阶段 A/C 完整 Python 回归宿主 RPM 失败 | component-source 实现后的仓库级全测仅有既知 Fedora RPM 6 非 root transaction lock 失败；其余 466 项通过，real-model worker 因未设置显式本地 checkpoint 按合同跳过。全量 Ruff 与 mypy 223 source files 通过。不能把非 RPM 通过项冒充全绿 | `uv run pytest -q`：`1 failed, 466 passed, 1 skipped`，唯一失败 `.rpm.lock Permission denied`；Ruff/mypy全绿 | 全仓门输出；未提交 | 按风险登记已验证的 `unshare --user --map-root-user` 边界单独运行真实 RPM install/upgrade/remove；通过后再结合 466 项证据关闭 A/C |

| 2026-09-05 | 阶段 A/C RPM user-namespace 回归失败 | 按此前成功的精确 `unshare --user --map-root-user` 命令重跑原始真实 RPM 生命周期，本次仍在 `rpm --initdb` 创建 transaction lock 时 permission denied；测试未修改、未跳过。旧成功证据不足以覆盖当前工作树/环境的新回归 | 单项 RPM pytest：`1 failed in 7.03s`，`.rpm.lock Permission denied` | 原始 RPM 生命周期输出；未提交 | 运行最小 namespace UID/目录写入/RPM 版本探针定位环境变化；A/C 保持需回归，不声明全绿 |

| 2026-09-05 | 阶段 A/C RPM 最小探针与 strace 失败 | namespace 内 `id` 为 root、RPM 6.0.2、SELinux disabled；0700 临时根/0755 rpmdb 可正常创建普通文件，但 `rpm --initdb` 仍单独拒绝 `.rpm.lock`。随后尝试 syscall trace 时系统无 `strace`，exit 127，未取得 syscall 证据 | 最小探针显示普通写成功但 RPM lock denied；`unshare ... strace`：`No such file or directory` | 环境诊断输出；未提交 | 不安装额外诊断包，改用 RPM 自带 `-vv`；继续穷尽不修改原测试的安全执行边界 |

| 2026-09-05 | 阶段 A/C RPM 6 锁边界定位完成；测试 fixture 待修正 | 上游 rpm issue #3886 记录 6.0 在 transaction lock 未预置时的回归。最小实验证明：在嵌套 user namespace 即使预置 0600 lock 仍 denied；同一私有 db/lock 在当前普通用户边界 `rpm --initdb` 成功。因此不再依赖嵌套 namespace；测试将在新建私有 db 后预置 0600 `.rpm.lock`，随后继续执行未模拟的真实 init/install/upgrade/remove | nested precreated lock 仍 denied；外层普通用户同目录 `rpm --initdb` exit 0 | RPM 6.0.2 探针与上游 issue #3886；未提交 | 修改测试 fixture 仅建立 RPM 6 的锁前置，重跑原始完整生命周期与全仓相关门；A/C 保持需回归 |

| 2026-09-05 | 阶段 A/C RPM 预置锁首次生命周期仍失败 | 测试 fixture 预置 0600 `.rpm.lock` 后，在 escalated uv 执行边界运行完整 RPM 生命周期仍于 initdb permission denied；这与同一外层普通用户手工 initdb 成功不一致，表明执行 sandbox/namespace 也是变量。未跳过或伪造后续事务 | 单项 RPM pytest：`1 failed in 7.39s` | fixture 与失败输出；未提交 | 用默认 sandbox + `/tmp` 专用 UV cache 重跑同一原测试，匹配已成功的外层用户边界；A/C 保持需回归 |

| 2026-09-05 | 阶段 A component-source 完整回归完成 | schema/manager/bundle 模型的 component provenance 合同及攻击矩阵已闭合；仓库级测试除显式 real-model checkpoint 项按合同跳过外全部有通过证据。RPM 6 私有 db 预置 transaction lock 后，在默认 sandbox 普通用户边界完成真实 init/install/upgrade/remove；escalated/nested namespace 的环境失败保留为诊断记录，不作为产品失败 | 全仓 `466 passed, 1 skipped` 加独立 RPM `1 passed in 7.15s`；Ruff全仓、mypy `223 source files`；architecture test/script、schema/manager 定向与 `git diff --check` 全通过 | A schema/model/tests、RPM fixture 与完整门输出；未提交 | 阶段 A 恢复已完成；B、D已完成，下一步重跑 C 的真实 Whisper bundle→安装→离线 health→重启链 |

| 2026-09-05 | 阶段 A 标记需回归；FireRed worker lock 缺失运行依赖 | 真实 VAD load 证明固定 `fireredasr2s@4e7d9aaf…` 的包级初始化及 VAD/LID feature extractor 均直接需要 `kaldi_native_fbank`；上游 `requirements.txt` 冻结为 1.15，但上游 `pyproject.toml` 与本地 worker direct dependencies 漏列。当前 registry 的 worker-lock SHA 因而对应一个无法加载已宣称 capability 的环境 | provisioned env 直接 import 完整 traceback：`ModuleNotFoundError: No module named 'kaldi_native_fbank'`；上游固定 checkout requirements 与 VAD/LID source import 对照 | 真实 worker 安装树与固定上游源码；未提交 | 为 worker 增加直接 `kaldi-native-fbank==1.15`、更新 frozen lock 及 registry 全部 FireRed lock SHA；重跑 A 环境/registry/worker/static 全门后才能恢复 |

| 2026-09-05 | FireRed 依赖首次冻结失败；上游 1.15 不支持 Python 3.12 | 按固定上游 requirements 原样加入 `kaldi-native-fbank==1.15` 后运行 uv resolver，发现该版本仅有 cp36～cp311 wheels，与 ClassScribe worker 的 `requires-python >=3.12,<3.13` 不可解；lock 命令 exit 1，`uv.lock` 未更新。不得强制使用不匹配 ABI、降级共享 Python 合同或把失败环境标为完整 | `uv lock --project workers/firered`：`No solution found`；可用 ABI `cp36m, cp37m, cp38, cp39, cp310, cp311`，缺 cp312 | resolver 失败输出与未变 lock；未提交 | 查询官方 PyPI 发布文件，选择支持 cp312 的最小兼容版本；对固定 FireRed VAD/LID API 做真实 import/load/infer 后再冻结。当前 pyproject 暂含未解 1.15，A～D/E 回退保持 |

| 2026-09-05 | FireRed Python 3.12 direct dependency 已冻结；A 定向行为通过 | 官方发布文件确认 `kaldi-native-fbank` 首个 CPython 3.12 wheel 版本为 1.19.0；worker 直接固定该版本并生成 68-package lock，registry 五个 FireRed 条目同步到实际 lock SHA。新 lock-addressed 环境安装 66 包后，固定上游的 VAD/LID/Punc/ASR 类均可直接 import；新增仓库回归同时约束 pyproject direct pin、lock 根依赖与解析版本 | `uv lock --check --project workers/firered` exit 0；worker-environment/registry `9 passed`；新环境 direct import 输出 `firered imports: OK`；lock SHA `14543cb7df6705b7ec82ef75e4a265bf2edf9e7f8b4e9465560ac4114b26f445` | FireRed pyproject/lock、registry、worker environment test 与真实 provisioned env；未提交 | Ruff/mypy 首轮因默认 UV cache 位于只读目录而各 exit 2，未执行静态分析；改用项目内 task-specific cache 原样重跑，再运行完整 A 受影响门 |

| 2026-09-05 | FireRed A 静态门首次运行环境失败 | 对新增回归分别启动 Ruff 与 mypy 时，uv 在只读默认 cache 下无法创建临时锁文件；两条命令均在工具启动前退出，没有代码诊断，不能计为通过 | Ruff、mypy 各 exit 2：`Read-only file system` at default uv cache temporary path | 静态门失败输出；未提交 | 设置现有项目内专用 UV cache 重跑相同 Ruff/mypy；A 保持需回归 |

| 2026-09-05 | FireRed A 定向回归首轮格式/路径失败 | 使用项目内专用 UV cache 后 mypy 通过；Ruff 准确发现新增 lock 元数据断言一行 101 字符。并行的行为组合因误写不存在的 `tests/unit/test_health.py` 而在收集前 exit 4、零测试运行，不能计为门禁证据 | mypy 1 file 通过；Ruff `E501` 1 项；pytest `file or directory not found`、`no tests ran` | 定向门输出与真实测试路径检索；未提交 | 折行后重跑 Ruff；组合改用仓库真实 `tests/unit/test_model_health.py` 与 `tests/integration/test_process_health.py`，A 保持需回归 |

| 2026-09-05 | FireRed A 组合回归出现预期旧 bundle 失败并挂起 | 折行后的 Ruff 已通过；受影响行为组合运行到 72% 时显示 1 项失败，随后超过 2 分钟无输出，人工中断 exit 130，未取得 pytest traceback，不能把其余点号计为完整通过。按收集顺序首个失败位于 manifest/bundle 组，高概率为 registry 新 worker-lock SHA 对旧生产 manifest 的 fail-closed 拒绝；必须用 `-x -vv` 单独确认而非猜测 | Ruff通过；组合输出 `.............F... [72%]` 后挂起；Ctrl-C exit 130 | 组合回归会话；未提交 | 拆分 manifest、health、worker 测试组；先取得旧 bundle 失败的精确诊断，再在新 bundle 生成后完成组合回归，A/B/C/D 保持需回归 |

| 2026-09-05 | 旧 production bundle 防漂移拒绝已确认；worker 组合另行挂起 | `test_load_builtin_manifest_bundle_uses_the_production_revision_lock` 精确拒绝旧 `firered_asr2_aed` manifest 的 environment，因为其仍冻结旧 worker-lock SHA；这验证回退与重新生成是必要的，不是实现回归。独立 model health/process health 9 项通过。三个 worker adapter 测试文件的组合运行 60 秒仍零输出，人工中断 exit 130，尚不能计通过 | manifest `1 failed, 1 passed`，错误 `model manifest environment differs from registry: firered_asr2_aed`；health `9 passed`；worker 组合 Ctrl-C exit 130 | fail-closed traceback、拆分门输出；未提交 | 先单文件定位 worker 测试挂起；随后双生成新 20-model bundle 并让内置 loader 测试转绿 |

| 2026-09-05 | FireRed 相关 adapter 回归通过；sandbox 挂起定位为执行边界 | 同一 Qwen/Nemotron/FireRed streaming、FireRed punctuation/body-ASR 16 项在外层普通用户边界 0.39 秒全部通过；受限 sandbox 内首项稳定卡在 `asyncio.to_thread`，不是测试断言或 FireRed 依赖失败。保留两次 sandbox Ctrl-C 记录，不用外层通过抹除环境差异 | 外层 `pytest`：`16 passed in 0.39s` | 三个 worker adapter 测试文件；未提交 | A 的 worker/health/registry定向门已通过；保持需回归直到新 production bundle 和完整仓库门完成 |

| 2026-09-05 | 全仓门盘点命令 UV cache 输入遗漏 | 定位 RPM 生命周期测试成功；同一只读盘点中的 `uv run pytest --collect-only` 遗漏项目内 UV cache，uv 在只读默认 cache 创建锁文件失败，未进行测试收集、不能计证据 | `rg` 定位 `tests/integration/test_stage3_packaging.py`；collect-only exit 2 `Read-only file system` | 盘点输出；未提交 | 后续所有 uv 命令显式使用项目内 task-specific cache；外层全仓排除 RPM 单项，RPM 仍在默认 sandbox 单独运行 |

| 2026-09-05 | 阶段 A FireRed dependency/bundle 完整回归完成 | FireRed worker 直接固定 CPython 3.12 可用的 fbank runtime，新 lock 与 registry/五个 manifest 一致；真实 provisioned env 的 VAD/LID/Punc/ASR imports 成功。新 bundle 内置 loader、防漂移、manager/health/worker adapter 与全仓测试闭合；RPM 6 生命周期继续在默认 sandbox 的真实普通用户边界通过 | 全仓（排除 RPM 单项）`471 passed, 1 expected skip, 1 deselected`；RPM `1 passed in 7.21s`；定向 consumer 72、worker adapter 16；Ruff、mypy 224 files、architecture、diff全绿 | worker pyproject/lock、registry、新 manifests/tests 与完整门输出；未提交 | 阶段 A 恢复已完成；B 已完成，C 仍需新 bundle Whisper 真实链，D 待重跑前端全门后恢复 |

| 2026-09-05 | A/C sandbox 回退计划补丁首次失败 | 尝试一次同步更新总体、A/C章节、E入口和Whisper表格时，长表格行上下文空格不一致导致 `apply_patch` verification failed；补丁整体没有写入，随后改为章节级短上下文 | `apply_patch` verification failed；只读复核确认状态仍为编辑前值 | 计划编辑失败输出；未提交 | 分块更新状态、记录与 E checkbox，完成后才修改 sandbox 代码 |

| 2026-09-05 | 阶段 A/C 标记需回归；sandbox synthetic HOME 合同将改变 | FireRedVAD 真实 load 证明 `--clearenv` 后缺 HOME 会使依赖调用 `getpwuid(1000)` 失败；最小 Bubblewrap 对照中无 HOME 的 `Path.home()` 同样失败，而只增加 tmpfs 内 `/home/classscribe` 与 `HOME=/home/classscribe` 即成功。方案不挂载主机 `/etc/passwd`、用户名或真实家目录 | 无 HOME 探针 exit 1；synthetic HOME 探针 exit 0，仅输出 `/home/classscribe` | 最小隔离探针与 WorkerSandbox 影响审计；未提交 | 修改共享命令并增加“不挂载主机 home/passwd + 合成 HOME 可用”测试；通过 A 门后第四次 VAD，且恢复 C 前重跑新命令下 Whisper 离线/重启最小链 |

| 2026-09-05 | 阶段 A synthetic HOME 回归完成 | WorkerSandbox 在 `--clearenv` 后只设置 `/home/classscribe` 合成 HOME，并在私有 mount namespace 内创建对应 tmpfs 目录；仍不挂载主机 home、passwd 或 group。命令构造测试、实际最小 Bubblewrap probe、model health、全仓线程/进程/socket 与默认 sandbox RPM 生命周期全部通过 | sandbox/health `7 passed`；无 HOME probe 失败且 synthetic HOME probe 成功；全仓 `471 passed, 1 expected skip, 1 deselected`；RPM `1 passed in 7.21s`；Ruff、mypy 224 files、diff全绿 | worker_sandbox/tests 与完整门输出；未提交 | 阶段 A 恢复已完成；C 仍需在新 sandbox 命令下重做 Whisper 真实离线/重启链，E 8.2 第一项保持未勾选 |

| 2026-09-05 | 阶段 A/C 再次标记需回归；synthetic HOME 不覆盖直接 NSS lookup | synthetic HOME 下 Whisper 完整通过，但 FireRedVAD 第四次真实 load 仍以 `getpwuid(): uid not found: 1000` 失败，证明底层依赖直接查询 UID 而非只调用 HOME/expanduser。不得挂载主机 `/etc/passwd`；下一版 sandbox identity 视图会改变共享命令，故 A/C 与 E/Whisper 证据再次回退 | 第四次 VAD health exit 1；manager清理候选且无 active；错误仍为 KeyError getpwuid(1000) | 真实 FireRedVAD 输出与 sandbox 行为对照；未提交 | 先取得 Bubblewrap 内未截断 traceback；设计只含合成用户/组、固定 home/shell 的只读 passwd/group 文件，补身份/不泄漏测试并完成 A回归 |

| 2026-09-05 | FireRed getpwuid 根因精确定位；最小合成环境对照 load 通过 | 私有诊断目录按 production manifest重新下载并复核 VAD 4 文件；未捕获 traceback 显示 `transformers→torch._dynamo→torch._inductor` 调用 `getpass.getuser()`，因 USER/LOGNAME 被 clearenv 后才回退 `pwd.getpwuid(1000)`。同一 Bubblewrap/payload仅加入 `USER=LOGNAME=classscribe` 后 VAD from_pretrained 成功，同时 HOME仍为tmpfs `/home/classscribe`；不需 passwd/group mount | 诊断 payload 4 files/4,654,184 bytes、manifest `e8067bf0…174a`；原命令 traceback定位 cache_dir_utils/getpass；对照输出 `classscribe /home/classscribe FireRedVAD load OK` | 固定 payload、完整 traceback与对照隔离 load；未提交 | 生产 sandbox增加合成 USER/LOGNAME，测试断言三项身份变量且不出现主机 identity/passwd；运行 A全门后再复验Whisper与第五次VAD |

| 2026-09-05 | 阶段 A 合成 USER/LOGNAME 回归完成 | WorkerSandbox 现在在清空环境后固定 `HOME=/home/classscribe`、`USER=LOGNAME=classscribe`，TorchInductor cache命名无需NSS；主机 home、用户名、passwd/group均不可见。固定 VAD from_pretrained真实隔离 load、命令测试、health、全仓线程/进程/socket与RPM真实生命周期全部通过 | VAD对照load exit 0；sandbox/health `7 passed`；全仓 `471 passed, 1 expected skip, 1 deselected`；RPM `1 passed in 7.10s`；Ruff、mypy 224、diff全绿 | worker_sandbox/tests、诊断 payload与完整门输出；未提交 | 阶段 A恢复已完成；C仍需在最终identity命令下重验Whisper首次/重启，之后才恢复E入口并进行VAD第五次完整链 |

| 2026-09-05 | 阶段 A 新 Qwen bundle 完整回归完成 | 在两个 Qwen ASR runtime backend 修正并重新生成 production bundle 后，内置 loader、全部 consumers、合同/攻击面及 worker/API 组合由全仓测试重新覆盖；唯一 skip 仍是要求显式本地 checkpoint 的通用真实模型测试，未把它计作本轮真实验收。RPM 6 生命周期继续在计划规定的默认沙箱普通用户边界独立通过 | 全仓排除 RPM 单项：`471 passed, 1 skipped, 1 deselected in 17.65s`；RPM：`1 passed in 7.26s`；Ruff 全仓、严格 mypy `224 source files`、architecture script、`git diff --check` 全通过 | 新 production bundle 下的根项目完整门输出；未提交 | 阶段 A 恢复已完成；A/B 进入条件满足，严格进入阶段 C 的新 bundle Whisper fresh-XDG 安装、断网健康与独立重启复验 |
| 2026-09-05 | 阶段A Granite lock/new bundle完整回归完成 | 在Granite direct TorchAudio依赖、新worker lock和重新生成的20项production bundle下，内置loader、全部consumer、合同/攻击面、worker/API与RPM生命周期由全仓测试重新覆盖。唯一skip仍是要求显式本地checkpoint的通用真实模型测试，未把它计作本轮阶段E真实验收 | 全仓排除RPM单项：`472 passed, 1 skipped, 1 deselected in 17.51s`；RPM `1 passed in 7.46s`；Ruff全仓、严格mypy `224 source files`、architecture script与`git diff --check`全通过 | 新production bundle下的根项目完整门输出；未提交 | A-01～A-08、4.5与4.6当前闭合，阶段A恢复已完成；A/B进入条件满足，严格进入C的新bundle Whisper fresh-XDG安装、断网健康与独立重启复验 |
| 2026-09-06 | 阶段 A Nemotron target-class 修复后完整回归闭合 | Nemotron adapter 对 archive target 的受限动态恢复未改变 manifest、bundle、registry、lock 或安装合同；全仓行为、真实 RPM 生命周期、项目级静态分析和架构边界均重新覆盖，恶意 target 拒绝测试包含在本轮中 | 全仓排除真实模型与 RPM 单项：`476 passed, 2 deselected in 16.91s`；RPM `1 passed in 7.22s`；Ruff通过；严格 mypy `224 source files`；architecture pytest `1 passed`、script `OK`；diff通过 | Nemotron adapter/tests 与完整 A 门输出；未提交 | 阶段 A 恢复已完成；B 已完成，严格进入 C，以保留的 Whisper 安装树和 legacy-compatible 环境执行独立断网重启回归 |
| 2026-09-06 | 阶段 A 新 Nemotron bundle 全量行为通过；宿主 RPM 边界复现 | 新 `55069eea…1357` production bundle 下，排除显式真实模型和单独 RPM 事务的全仓测试全部通过；宿主普通用户直接运行 RPM 6 生命周期时，仅在自定义可写 dbpath 创建事务锁失败，属于计划已记录并已有 user-namespace 解法的环境权限边界，不计产品回归通过 | 全仓 `477 passed, 2 deselected in 17.49s`；宿主 RPM `1 failed in 7.48s`，错误 `can't create transaction lock .../.rpm.lock (Permission denied)` | 新 production bundle 下的根项目输出；未提交 | 保持 A 为需回归；用 `unshare --user --map-root-user` 原样重跑同一 RPM 用例，随后完成 Ruff/mypy/architecture/diff 全门 |
| 2026-09-06 | 阶段 A 首轮静态/RPM复验未闭合 | 项目源码 Ruff、architecture pytest/script 与 diff 已通过；严格 mypy 新发现 Nemotron fake `AudioBuffer.__iter__` 缺少返回类型，属于测试代码静态合同缺口。既有 user-namespace RPM 命令本轮仍在 `.rpm.lock` 处被 RPM 6.0.2 拒绝，需改用隔离的专用临时根继续确认当前 mount/ownership 边界 | Ruff `All checks passed!`；mypy `1 error in 1 file (checked 224 source files)`；architecture `1 passed`、script `OK`、diff通过；user-namespace RPM `1 failed in 6.90s`，同一 transaction lock 权限错误 | 静态与 RPM 重跑输出；未提交 | 为迭代器补精确返回类型并重跑定向/mypy；诊断并重跑真实 RPM 生命周期，二者通过后才恢复 A |
| 2026-09-06 | 阶段 A 局部修复与 RPM 边界通过；等待最终全门 | 为 Nemotron fake audio buffer 的迭代器补上精确类型，不改变测试或产品运行语义；定向 worker 与严格静态检查通过。RPM 用例在计划约定的默认受限沙箱普通用户边界成功，确认外层/user-namespace 的两次锁失败是本轮执行边界差异，而非 packaging 退化 | streaming workers `4 passed in 0.22s`；Ruff通过；mypy `224 source files`；默认沙箱 RPM `1 passed in 7.14s` | 测试类型注解、RPM 生命周期与静态输出；未提交 | 在当前修改后工作树重跑全仓非真实模型/非 RPM 行为、项目源码 Ruff、architecture 与 diff；全绿后恢复 A |
| 2026-09-06 | 阶段 A 新 Nemotron production bundle 最终回归闭合 | 在类型注解修复后的同一工作树重跑完整行为门；新 bundle 的 loader、consumer、攻击面、worker/API 和 packaging 合同全部通过。结合本轮已通过的独立 RPM 生命周期与严格 mypy，A-01～A-08、4.5 和 4.6 再次全部闭合 | 全仓 `477 passed, 2 deselected in 17.01s`；RPM `1 passed in 7.14s`；项目源码 Ruff `All checks passed!`；mypy `224 source files`；architecture pytest `1 passed`、script `OK`；diff通过 | 新 production bundle、测试类型注解与完整 A 门输出；未提交 | 阶段 A 恢复已完成；B 已完成，严格进入阶段 C 的独立断网 Whisper 回归 |

## 5. 阶段 B：发布工具

状态：**已完成**

### 5.1 阶段目标

建立与运行时依赖完全隔离的联网发现/生成工具和完全离线的 verifier，以人工 selection 为唯一 payload
决策来源，并确保确定性生成、双重生成、实际字节哈希和凭证脱敏。

### 5.2 进入条件

- [x] 阶段 A 已完成。
- [x] bundle/manifest schema、规范化 JSON 字节格式和 remote-code 路径语义已经冻结。
- [x] 发布者理解 `discover`/`generate` 联网、`verify` 离线的边界。

### 5.3 工具工程与输入

- [x] `B-01` 创建第 3.3 节所列 `tools/model-manifest/` 独立工程、CLI 模块和 frozen `uv.lock`。
- [x] `B-02` 只在该工具工程引入 `huggingface_hub`；确认 core 和所有 worker 生产依赖未增加它。
- [x] `B-03` 如增加根目录薄脚本，只允许转发到工具工程，不得复制 generator/verifier 实现。
- [x] `B-04` 将工具 lock 纳入 release dependency lock 检查。
- [x] `B-05` 为 `config/model-file-selection.v1.yaml` 建立 schema/loader，并落实第 3.4 节精确 include、
  显式 kinds、可审计 exclude 规则。

生成器固定输入为：

- `config/model-registry.v1.yaml`；
- `config/model-revisions.lock.json`；
- `config/model-licenses.v1.json`；
- `config/model-file-selection.v1.yaml`；
- 每个 worker 的 `uv.lock`；
- 可选 `HF_TOKEN`，只用于 gated repository。

### 5.4 凭证合同

- [x] `B-06` 凭证只能从安全环境或权限为 0600 的文件读取。
- [x] `B-07` token 只放入 Authorization header，不写入异常消息、日志、manifest、bundle 或临时路径名。
- [x] `B-08` pyannote 访问必须先由真实用户接受上游访问条件，不得绕过 gated 条款。
- [x] `B-09` 用 fixture 覆盖成功、403、redirect、异常和清理失败路径，证明 token 均不会泄漏。

### 5.5 `discover`：固定 revision 的仓库树发现

对每个模型执行：

- [x] `B-10` 调用 Hugging Face repository tree API，明确传入 registry 的完整 commit revision，
  并递归枚举文件。
- [x] `B-11` 拒绝 revision 未解析到同一 commit、repository 不存在或 gated 权限不足。
- [x] `B-12` 保存发现报告到临时工作区，供发布者选择文件；发现报告不得进入运行时 bundle。
- [x] `B-13` 将 selection 中每个精确路径与 tree 对照，拒绝缺失、多余或大小无法确认的条目。

Hugging Face 普通 `blob_id` 是 Git OID，不是本项目所需的 SHA-256；LFS 项虽可能提供 SHA-256，仍须
统一对实际下载文件字节复算，避免为 Git、LFS、Xet 建立互不一致的信任路径。

### 5.6 `generate`：下载、校验与哈希

- [x] `B-14` 使用 `mktemp` 创建权限 0700 的临时根，不使用用户模型运行缓存作为生成目录。
- [x] `B-15` 按 repository、完整 revision 和精确 allowlist 下载，保留上游目录结构。
- [x] `B-16` 删除下载工具生成的 `.cache/huggingface/` 元数据，不把它列入 payload。
- [x] `B-17` 拒绝 symlink、异常 hardlink、FIFO、device、socket、绝对路径、`..`、重复路径和大小超限。
- [x] `B-18` 对每个普通文件流式计算 SHA-256 和实际字节数。
- [x] `B-19` 将实际文件集合与 selection 精确比较，拒绝任何未声明文件或缺失文件。
- [x] `B-20` 按 selection 显式映射设置 kind；未分类文件不得默认为 model，必须失败并要求发布者决定。
- [x] `B-21` 按 Unicode 路径排序文件列表，计算大小字段，并按第 3.5 节生成规范化 manifest。
- [x] `B-22` 清理临时目录；清理失败应报告，但不得泄漏 token。

官方 `huggingface_hub` 支持以完整 revision 下载、用 allow pattern 过滤并在 `local_dir` 保留原始目录结构：
<https://huggingface.co/docs/huggingface_hub/en/guides/download>。

### 5.7 确定性与双重生成

正式冻结前必须在两个空临时目录中连续生成两次：

- [x] `B-23` 两次下载解析到相同 commit。
- [x] `B-24` 所有 payload 文件的 size/SHA 相同。
- [x] `B-25` 除非显式改变 `SOURCE_DATE_EPOCH`，两次 manifest 和 bundle 字节完全相同。
- [x] `B-26` 若不一致，停止发布，并输出只包含路径和哈希差异的报告。

### 5.8 `verify` 与目标 CLI

- [x] `B-27` verifier 必须在完全离线环境中验证 schema、路径、排序、成员 SHA、成员集合、交叉合同、
  worker lock 及不含秘密/本机信息；验证过程不得访问网络。
- [x] `B-28` CLI 提供生成、验证和批量状态检查入口，解决当前 CLI 缺口。
- [x] `B-29` 实现以下目标命令接口，参数与语义保持不变：

```bash
uv run --project tools/model-manifest classscribe-model-manifest discover \
  --registry config/model-registry.v1.yaml \
  --selection config/model-file-selection.v1.yaml \
  --output /tmp/classscribe-model-discovery

uv run --project tools/model-manifest classscribe-model-manifest generate \
  --registry config/model-registry.v1.yaml \
  --revisions config/model-revisions.lock.json \
  --licenses config/model-licenses.v1.json \
  --selection config/model-file-selection.v1.yaml \
  --output config/model-manifests/v1

uv run --offline --frozen --project tools/model-manifest \
  classscribe-model-manifest verify \
  --bundle config/model-manifests/v1/bundle.v1.json
```

`discover` 和 `generate` 是明确联网的发布操作；`verify` 必须完全离线运行。

### 5.9 测试与交付物

- [x] `B-30` 单元测试覆盖普通 Git、LFS、Xet 均按实际字节哈希。
- [x] `B-31` 测试 selection 精确闭包、未分类文件、权重 index 漏 shard 和额外文件拒绝。
- [x] `B-32` 测试两次生成字节一致，`SOURCE_DATE_EPOCH` 控制批次时间。
- [x] `B-33` 测试 token 不进入输出、日志或异常文本。
- [x] `B-34` 使用本地假 Hugging Face server 模拟固定 revision、LFS redirect、短写、超长、
  跨 host redirect、revision 漂移和 gated 403。
- [x] `B-35` 离线 verifier 覆盖 bundle schema、manifest SHA、缺件、额外项、重复 ID、路径逃逸，
  并在任一 byte 被修改时失败。

### 5.10 退出条件

使用本地 fixture repository 可以确定性生成 bundle；离线 verifier 能发现任意篡改；联网工具依赖没有
进入 core/worker；任何测试和错误路径均不泄漏凭证。

### 5.11 进度记录

| 日期 | 状态/任务 ID | 变更或结论 | 验证命令与结果 | 提交/证据 | 阻塞与下一步 |
|---|---|---|---|---|---|
| 2026-09-04 | 未开始 | 仅建立执行计划，未创建工具工程 | 未运行实施验证 | 本文件 | 阶段 A 完成后开始 `B-01` |
| 2026-09-04 | 完成 `B-01`；阶段进行中 | 建立独立 `tools/model-manifest/` 可安装工程、CLI 入口以及 generator/selection/verifier 边界模块，生成只含该工程本身的 frozen `uv.lock`；未提前暴露尚未实现的子命令 | `uv lock --check --project tools/model-manifest`：通过；首次离线构建因缓存缺少 hatchling 失败，联网完成一次 frozen 构建后，`uv run --offline --frozen --project tools/model-manifest classscribe-model-manifest --help` 与三个边界模块导入均通过；ruff 与 `git diff --check` 通过 | 独立工具工程与 lock；未提交 | 下一步重新读取并执行 `B-02`，仅在该工具引入 `huggingface_hub` |
| 2026-09-04 | 完成 `B-02`；阶段进行中 | 仅在独立 manifest 工具声明 `huggingface-hub>=0.34,<2` 并冻结为 1.30.0；根 core 与全部 worker 的 `pyproject.toml` 均未新增直接依赖，根和 worker locks 未改动 | `uv lock --check --project tools/model-manifest`：通过；`rg 'huggingface[-_]hub' --glob pyproject.toml` 仅命中工具工程；frozen sync 后离线导入输出 `1.30.0`；工具 ruff 与 `git diff --check` 通过 | `tools/model-manifest/pyproject.toml`、`uv.lock`；未提交 | 下一步重新读取并执行 `B-03` |
| 2026-09-04 | 完成 `B-03`；阶段进行中 | 审计决定不增加根目录薄脚本；CLI 唯一入口保留在独立工具工程，因而不存在复制 generator/verifier 实现的平行路径 | 排除工具工程和计划文件后检索 `classscribe-model-manifest`/`classscribe_manifest_tool`：仅有设计文档、运行时 schema/model/test 的固定 generator 身份引用，没有根脚本或复制实现 | 仓库检索结果；未提交 | 下一步重新读取并执行 `B-04` |
| 2026-09-04 | 完成 `B-04`；阶段进行中 | 在 release manifest 冻结工具 lock SHA，并让 source release 的 `dependency_lock_hashes` 门校验安全相对路径、普通文件、lowercase SHA 与实际字节；installed release 仍只检查已安装 worker locks，不错误要求 RPM 携带发布工具 | `uv run pytest tests/unit/test_release_check.py -q`：`5 passed`，含 lock 正常、字节漂移和缺少 pin；定向 ruff、mypy 通过；release manifest JSON 解析与 `git diff --check` 通过 | `release/release-manifest.v1.json`、`backend/classscribe/release_check.py`、release-check 测试；未提交 | 下一步重新读取并执行 `B-05` |
| 2026-09-04 | 完成 `B-05`；阶段进行中 | 新增 selection v1 JSON Schema、严格安全 YAML loader 和尚待真实 discovery 填充的空生产 inventory；任何实际条目必须使用非空、排序、唯一、无 glob 的精确 include，kinds 必须与 include 一一对应，exclude 必须为排序唯一且不重叠的精确审计路径；拒绝重复 YAML key、额外字段和不安全路径。将 PyYAML 声明为工具直接依赖后同步更新并重验 `B-04` 的工具 lock SHA pin | selection/release 定向测试 `16 passed`；工具 ruff、selection mypy、schema JSON 解析、生产空 inventory 加载、`uv lock --check` 与 `git diff --check` 均通过 | selection schema/YAML/loader/tests、工具 pyproject/lock、release lock pin；未提交 | 下一步重新读取并执行 `B-06`；生产 selection 只在阶段 C/E 的固定 revision 真实发现后填充 |
| 2026-09-04 | 完成 `B-06`；阶段进行中 | 建立 HF token 单一读取边界：允许无 token 或仅一个显式来源；文件通过 `O_NOFOLLOW` 打开并要求普通文件、当前用户所有、严格 0600、UTF-8、大小受限，环境/文件的空白或控制字符 token 与双重来源均失败 | credentials 定向测试 `8 passed`，覆盖环境、无 token、0600 文件、来源冲突、0640、symlink、空白/空值；定向 ruff、mypy 与 `git diff --check` 通过 | `credentials.py` 及测试；未提交 | 下一步重新读取并执行 `B-07` |
| 2026-09-04 | 完成 `B-07`；阶段进行中 | 收窄 token 传播接口：唯一传输表示为只读 `Authorization: Bearer` header；诊断输出统一替换已知 token 和 Bearer header；manifest、bundle 与临时路径写入前可 fail-closed 断言 token 不存在，所有拒绝错误本身不包含秘密 | credentials 定向测试扩展为 `14 passed`，覆盖 header、诊断脱敏、三类输出拒绝与公开输出接受；定向 ruff、mypy 与 `git diff --check` 通过 | credentials helper 及测试；未提交 | 下一步重新读取并执行 `B-08` |
| 2026-09-04 | 完成 `B-08`；阶段进行中 | gated policy 由 license inventory 驱动并在网络请求前同时要求“用户已在上游接受条款”的精确 repository 声明和授权 token；没有自动接受、403 绕过或仅凭 token 放行路径；当前 inventory 核对为只有 pyannote Community-1 需接受条款 | credentials 定向测试扩展为 `17 passed`，覆盖缺接受声明、缺 token、双条件满足、公开仓库和当前 inventory；定向 ruff、mypy 与 `git diff --check` 通过 | gated access policy 及测试；未提交 | 下一步重新读取并执行 `B-09` |
| 2026-09-04 | 完成 `B-09`；阶段进行中 | 新增 credential-safe HTTPS JSON boundary 和固定前缀 0700 临时工作区；HTTPX fixture 直接确认 origin 只在 Authorization header 收到 token、跨 host redirect 自动剥离该 header，403/transport/operation/cleanup 失败的对外错误全部脱敏。HTTPX 与隔离的 dev 工具均只加入工具工程，lock SHA pin 已同步回归 | 独立工具全测 `34 passed`，其中 B-09 五类路径定向 `6 passed`；工具 ruff、严格 mypy、`uv lock --check`、release-check `5 passed` 与 `git diff --check` 全部通过 | network/workspace helper、fixtures、独立 dev 配置、工具 lock/release pin；未提交 | 下一步重新读取并执行 `B-10` |
| 2026-09-04 | 完成 `B-10`；阶段进行中 | 新增固定 revision tree discovery：调用 `HfApi.list_repo_tree` 时显式传完整 40 位 commit、`recursive=True`、model repo type，并禁用隐式本机 token；只收集 RepoFile，按 Unicode path 排序并保留 size/Git OID/LFS/Xet 元数据供人工策展，不把这些上游 OID 当最终 payload SHA | 独立工具全测 `36 passed`；fixture 精确断言 API 参数并确认目录被忽略、文件排序；工具 ruff、严格 mypy（13 files）和 `git diff --check` 通过 | `discovery.py`、selection 共用路径校验及 discovery tests；未提交 | 下一步重新读取并执行 `B-11` |
| 2026-09-04 | 完成 `B-11`；阶段进行中 | tree 前先用同一完整 revision 查询 model info，并要求 resolved SHA 精确相等；不同 commit、revision 404、repository 404、gated/401/403 均以稳定类型 fail closed，禁止 symbolic fallback、匿名重试和原始上游秘密错误透传 | 独立工具全测 `40 passed`，新增 resolved mismatch 与三类 Hub 错误测试；工具 ruff、严格 mypy（13 files）及 `git diff --check` 通过 | discovery resolution/error policy 及 tests；未提交 | 下一步重新读取并执行 `B-12` |
| 2026-09-04 | 完成 `B-12`；阶段进行中 | discovery 报告固定写为 `*.discovery.json`、`purpose=selection_review_only`，在显式 0700 审核目录中以 0600 临时文件原子替换；格式与生产 manifest 分离且只含上游公开元数据，写入前检查路径和内容无 token | 独立工具全测 `42 passed`；新增报告确定字节、权限、命名/字段隔离和 secret 拒绝测试；工具 ruff、严格 mypy（13 files）及 `git diff --check` 通过 | discovery report writer 及 tests；未提交 | 下一步重新读取并执行 `B-13` |
| 2026-09-04 | 完成 `B-13`；阶段进行中 | selection include 与 exclude 必须恰好分区整个 fixed-revision tree；任一声明路径缺失、tree 额外未分类、重复 discovery path 或选中文件 size 不可确认均 fail closed，成功结果严格按 include 顺序返回供 allowlist 使用 | 独立工具全测 `46 passed`；新增精确闭包成功及 missing/extra/unknown-size 测试；工具 ruff、严格 mypy（13 files）与 `git diff --check` 通过 | selection/tree cross-check 及 tests；未提交 | 下一步重新读取并执行 `B-14` |
| 2026-09-04 | 完成 `B-14`；阶段进行中 | generator 工作区固定通过 `tempfile.mkdtemp` 在系统临时根创建，目录 0700、固定无秘密前缀，不读取或复用 XDG/HF/模型运行 cache；正常退出自动删除 | 独立工具全测 `47 passed`；诱导设置 `XDG_DATA_HOME`/`HF_HOME` 后仍验证系统临时根、0700、路径不含 token 与退出删除；工具 ruff、严格 mypy（14 files）及 `git diff --check` 通过 | generator workspace 入口及 test；未提交 | 下一步重新读取并执行 `B-15` |
| 2026-09-04 | 完成 `B-15`；阶段进行中 | `snapshot_download` 调用固定 model repository、40 位 commit、selection 精确 allowlist、私有 workspace 内 local/cache 目录与 force download；禁用隐式 token/cache-only，认证只经 header；要求 downloader 返回指定 payload root，上游嵌套布局不重写 | 独立工具全测 `48 passed`；fake downloader 精确断言全部调用参数，并验证 `config.json` 与 `nested/weights.bin` 原路径落盘；工具 ruff、严格 mypy（14 files）及 `git diff --check` 通过 | selected snapshot downloader 及 test；未提交 | 下一步重新读取并执行 `B-16` |
| 2026-09-04 | 完成 `B-16`；阶段进行中 | 下载后仅移除真实 payload 根下 `.cache/huggingface/`，空 `.cache` 一并删除；其他 cache 内容保留供后续精确闭包拒绝，`.cache` 或 metadata 为 symlink/非目录时 fail closed，不跟随或触及外部目标 | 独立工具全测 `51 passed`；新增完整 metadata 删除、其他 cache 保留和 symlink 外部 marker 保全测试；工具 ruff、严格 mypy（14 files）及 `git diff --check` 通过 | metadata cleanup 及 tests；未提交 | 下一步重新读取并执行 `B-17` |
| 2026-09-04 | 完成 `B-17`；阶段进行中 | payload scanner 要求绝对规范且非 symlink 的真实根，逐层 `scandir/lstat` 且不跟随链接；仅接受普通目录/单链接普通文件，拒绝 symlink、hardlink/重复 inode、FIFO/socket/字符与块设备、非规范/逃逸路径和重复路径；单文件及总大小上限直接来自 discovery 已确认 size | 独立工具全测 `59 passed`；覆盖正常排序、symlink 外部保全、hardlink、FIFO、socket/device mode、非规范根与 size overrun；工具 ruff、严格 mypy（14 files）及 `git diff --check` 通过 | payload scanner 及 tests；未提交 | 下一步重新读取并执行 `B-18` |
| 2026-09-04 | 完成 `B-18`；阶段进行中 | 对扫描后的排序文件逐层用 dir-fd + `O_NOFOLLOW` 安全重开，校验 device/inode/size 未漂移，以 1 MiB 固定块循环计算实际 SHA-256 与读取字节数，结束后再次核对 inode/size/mtime；拒绝扫描后替换或哈希中变化 | 独立工具全测 `62 passed`；覆盖多块读取、空文件、扫描后增长和 symlink 替换且外部内容保全；工具 ruff、严格 mypy（14 files）及 `git diff --check` 通过 | streaming payload hasher 及 tests；未提交 | 下一步重新读取并执行 `B-19` |
| 2026-09-05 | 完成 `B-19`；阶段进行中 | 实际哈希文件集合必须与 selection include 全等，并与 `B-13` 的 selected discovery 元数据逐项一致；缺件、额外文件、重复实际 path 和 short/long size mismatch 均 fail closed，成功结果按 include 顺序返回 | 独立工具全测 `66 passed`；新增完整匹配及 missing/extra/short-write 测试；工具 ruff、严格 mypy（14 files）与 `git diff --check` 通过 | payload/selection closure validator 及 tests；未提交 | 下一步重新读取并执行 `B-20` |
| 2026-09-05 | 完成 `B-20`；阶段进行中 | 每个已哈希文件只从 selection 的完整显式 mapping 取得 kind，要求文件顺序/集合与 include 完全一致、kind key 无缺漏且值在冻结枚举内；实现中不存在 `model` 默认推断 | 独立工具全测 `68 passed`；新增显式 config/remote_code 映射与缺 kind 拒绝测试；工具 ruff、严格 mypy（14 files）与 `git diff --check` 通过 | classified payload model/function 及 tests；未提交 | 下一步重新读取并执行 `B-21` |
| 2026-09-05 | 完成 `B-21`；阶段进行中 | 新增不可变 manifest metadata/document 与 canonical writer；严格校验 identity、完整 commit、license HTTPS、terms/trust bool、五个必需 environment 字段和 worker lock SHA、Unicode 排序唯一的实际 payload、size/SHA/kind 及 remote-code 双向政策；estimated/installed size 均由实际字节求和，JSON 固定 UTF-8/LF/sorted keys/indent 2/单换行并写前查 secret | 独立工具全测 `71 passed`，canonical 输出直接通过现有 manifest v1 Draft 2020-12 schema；工具 ruff、严格 mypy（14 files）、release-check `5 passed`、frozen lock 与 `git diff --check` 通过；jsonschema 与 types 仅加入独立工具，`B-04` lock SHA pin 同步回归 | manifest builder/writer/tests、工具 lock/release pin；未提交 | 下一步重新读取并执行 `B-22` |
| 2026-09-05 | 完成 `B-22`；阶段进行中 | 复用单一 private workspace context：正常、operation exception 和 cleanup exception 均尝试删除整个下载/cache/payload 临时根；清理失败显式报错并经 token redactor，避免平行清理路径 | 定向重跑正常删除与清理失败脱敏：`2 passed`；此前独立工具全测 `71 passed` 已覆盖 operation error；`git diff --check` 通过 | workspace lifecycle 与既有 fixtures；未提交 | 下一步重新读取并执行 `B-23` |
| 2026-09-05 | 完成 `B-23`；阶段进行中 | 双重生成比较先要求请求 revision 为完整 commit，且两个 clean run 的 resolved revision 均精确等于该 commit；任一边漂移在 payload 比较前立即失败 | generator 定向测试 `26 passed`，含双 run 同 commit 成功与第二 run 漂移失败；工具 ruff、严格 mypy（14 files）通过 | double-generation revision gate 及 test；未提交 | 下一步重新读取并执行 `B-24` |
| 2026-09-05 | 完成 `B-24`；阶段进行中 | 双 run 只按实际下载字节得到的 path/size/SHA-256 比较，要求文件集合全等、无重复且每个 size/hash 相同；不使用 Git OID、LFS metadata hash 或 Xet hash 替代 | generator 定向测试 `27 passed`，含完整一致、缺件和 SHA 漂移失败；工具 ruff、严格 mypy（14 files）及 `git diff --check` 通过 | double-generation payload gate 及 test；未提交 | 下一步重新读取并执行 `B-25` |
| 2026-09-05 | 完成 `B-25`；阶段进行中 | `SOURCE_DATE_EPOCH` 必须显式为有效非负整数并唯一决定 UTC `generated_at`；bundle 成员按 model ID 排序，path 固定 `<model_id>.json`，成员 SHA 与 generator lock SHA 由实际字节计算；双 run 要求 manifest 集合/字节及 bundle 字节全等 | 独立工具全测 `75 passed`；相同 epoch 且不同 mapping 插入顺序生成字节相同，改变 epoch 生成不同 bundle；manifest/bundle 均通过 Draft 2020-12 schema；工具 ruff、严格 mypy（14 files）及 `git diff --check` 通过 | canonical bundle/double-output gate 及 tests；未提交 | 下一步重新读取并执行 `B-26` |
| 2026-09-05 | 完成 `B-26`；阶段进行中 | 统一 double-run publication gate 在 revision、payload、manifest 或 bundle 任一漂移时抛出阻断异常；确定性 JSON report 只含逻辑相对 path 与 first/second SHA-256（缺件为 null），不含原始内容、size、repository、token 或本机路径；revision 值本身也只以 SHA 表达 | 独立工具全测 `78 passed`；覆盖 payload/manifest/bundle 组合差异、revision 漂移和完全一致放行；工具 ruff、严格 mypy（14 files）及 `git diff --check` 通过 | generation mismatch gate/report 及 tests；未提交 | 下一步重新读取并执行 `B-27` |
| 2026-09-05 | 完成 `B-27`；阶段进行中 | 实现完全离线 verifier：校验 bundle/manifest/registry/selection schema、canonical JSON、排序唯一/安全路径、目录与 registry/revision/license/selection 成员全等、实际成员/tool-lock/worker-lock SHA、五方字段与 file kind/size 交叉合同，并拒绝 token、Authorization、本机绝对/cache 路径；模块不导入网络/HF 能力。B-05 YAML loader 抽取为共享严格入口后全测回归 | verifier 本地完整 fixture `6 passed`，含 socket 封锁下成功、成员 byte 篡改、额外成员、secret/local value 与 worker lock 漂移；独立工具全测 `84 passed`；ruff、严格 mypy（15 files）及 `git diff --check` 通过 | offline verifier、完整 fixture tests 与共享 YAML loader；未提交 | 下一步重新读取并执行 `B-28` |
| 2026-09-05 | 完成 `B-28`；阶段进行中 | CLI 现提供 `discover`、`generate`、`verify`、`status` 四个入口；生成入口加载并交叉核对冻结输入，在两个干净工作区执行全模型生成，以既有路径/SHA-only 门阻断漂移，原子写出后立即离线复核；批量状态入口在无 bundle 时也能按排序返回 registry 全部模型且不访问网络 | 独立工具全测 `93 passed`，新增四命令 parser/dispatch、真实 20 模型离线 status、generate 编排链、双工作区清理及 payload 漂移报告；实际 `status` 命令返回 20 项；工具 ruff、严格 mypy（17 files）与 `git diff --check` 通过 | `cli.py`、`inputs.py`、generator 编排与 CLI/generator tests；未提交 | 下一步重新读取并执行 `B-29`，固定计划列出的三条目标命令及其联网/离线语义 |
| 2026-09-05 | 完成 `B-29`；阶段进行中 | 精确实现计划列出的 `discover --registry --selection --output`、`generate --registry --revisions --licenses --selection --output` 与 `verify --bundle` 命令面；帮助文本明确前两者为联网发布操作、verify 为离线本地操作；未用仍为空的生产 selection 虚构真实发现/生成结果 | 四级 CLI help 均通过并显示精确参数；socket 封锁下从 CLI 执行完整 fixture `verify --bundle` 通过；discover fixture 确认固定 revision 与 selection-review-only 报告语义；独立工具全测 `95 passed`；offline/frozen lock、ruff、严格 mypy（17 files）及 `git diff --check` 通过 | CLI 与 CLI/verifier tests；未提交 | 下一步重新读取并执行 `B-30`，补齐 Git/LFS/Xet 实际字节哈希覆盖 |
| 2026-09-05 | 完成 `B-30`；阶段进行中 | 新增三来源单元用例，分别构造普通 Git blob、LFS pointer SHA 和 Xet hash 元数据，统一扫描并流式哈希下载后的普通文件；最终 digest 只由实际 bytes 决定，不信任 Git OID、LFS SHA 或 Xet hash | 三来源定向测试 `1 passed`；独立工具全测 `96 passed`；ruff、严格 mypy（17 files）及 `git diff --check` 通过 | generator 三来源实际字节哈希 test；未提交 | 下一步重新读取并执行 `B-31`，补齐 selection 精确闭包与权重 index shard 证据 |
| 2026-09-05 | 完成 `B-31`；阶段进行中 | 在既有 discovery 未分类项和实际 payload 缺失/额外项 fail-closed 基础上，新增对下载后 `kind=model` 的 `*.index.json` 安全受限解析；要求非空 `weight_map` 的 shard 引用均为安全精确路径、包含于 selection 且显式归类为 model，禁止用 exclude 隐藏被引用 shard | 完整 index 闭包、漏 shard、逃逸引用、错误 kind 新增用例通过；generator 定向 `38 passed`，独立工具全测 `99 passed`；ruff、严格 mypy（17 files）及 `git diff --check` 通过 | weight-index closure validator 与 generator/discovery 既有 tests；未提交 | 下一步重新读取并执行 `B-32`，验证完整双生成字节与批次时间控制 |
| 2026-09-05 | 完成 `B-32`；阶段进行中 | 将确定性证据提升到完整生成编排：同一固定 epoch 的两次外层生成返回完全相同 manifest/bundle bytes；epoch 改变时 payload 与 manifest 不变、仅 bundle 批次字节和时间变化；每次调用内部仍执行两个全新空工作区 | 双生成/epoch 定向 `3 passed`，含 6 个互异工作区均清理；独立工具全测 `99 passed`；ruff、严格 mypy（17 files）及 `git diff --check` 通过 | generator orchestration 与 canonical bundle tests；未提交 | 下一步重新读取并执行 `B-33`，覆盖输出、日志和异常的 token 泄漏路径 |
| 2026-09-05 | 完成 `B-33`；阶段进行中 | 完整生成在携带 token 时仅把认证传给 downloader header，manifest/bundle bytes 和漂移报告不含 token；CLI 顶层对环境 token 与 Authorization 形式统一脱敏，stdout、stderr、日志和异常均不泄漏；并重跑成功、403、跨 host redirect、transport、operation 与 cleanup 既有失败路径 | 凭证/CLI/生成错误路径定向 `26 passed`；独立工具全测 `100 passed`；ruff、严格 mypy（17 files）及 `git diff --check` 通过 | credentials/network/workspace/generator/CLI tests；未提交 | 下一步重新读取并执行 `B-34`，建立本地假 Hub 的完整网络行为矩阵 |
| 2026-09-05 | 完成 `B-34`；阶段进行中 | 新增实际监听 loopback 的本地假 Hugging Face server；官方 `HfApi` 路径验证固定 commit/tree、resolved revision 漂移和 gated 403，下载边界验证同 host LFS redirect、跨 host Authorization 剥离、短写与超长 payload 的 size fail-closed | 默认沙箱因禁止创建 loopback socket 为 `7 errors (EPERM)`；在获准的 loopback 执行边界原测试 `7 passed in 3.89s`；同边界独立工具全测 `107 passed in 3.81s`；ruff、严格 mypy（18 files）及 `git diff --check` 通过 | `test_fake_hub_server.py` 的本地 HTTP fixture 与真实网络行为证据；未提交 | 下一步重新读取并执行 `B-35`，补齐 verifier 全攻击矩阵 |
| 2026-09-05 | 完成 `B-35`；阶段进行中 | 扩展完全离线 verifier 攻击矩阵：bundle/manifest schema 与 canonical bytes、成员 SHA、缺件、额外项、重复 ID、逃逸/嵌套路径、generator lock byte 和任一成员 byte 漂移均 fail closed；成功 CLI 路径继续在 socket 封锁下通过 | verifier 定向 `14 passed`；含本地假 Hub 的独立工具全测 `114 passed in 3.84s`；ruff、严格 mypy（18 files）及 `git diff --check` 通过 | verifier tests 与既有离线 socket blockade fixture；未提交 | B-01～B-35 均已勾选；下一步重新读取并逐项审计阶段 B 交付物及 5.10 退出条件，未审计前不标记阶段完成 |
| 2026-09-05 | 阶段 B 已完成 | 补齐同一一模型本地 fixture 的真实冻结输入加载→两次干净生成→原子写出→socket 封锁下离线 verifier 闭环；逐项复核 B-01～B-35、5.10 退出条件、工具 lock pin 与依赖边界，未生成或冒充生产 bundle | 本地完整链定向 `1 passed`；含 loopback 假 Hub 的工具全测 `116 passed in 3.85s`；release-lock `5 passed`；architecture boundaries OK；offline/frozen lock 33 packages；ruff、严格 mypy（18 files）、`git diff --check` 通过；本轮 core/worker pyproject 与 locks 无差异 | 阶段 B 工具、schemas、selection、tests 与门禁输出；未提交 | 无阶段 B 阻塞；进入阶段 C 前重新读取其进入条件，从 `C-01` 的 Whisper Tiny 固定 commit 真实 discovery 开始 |
| 2026-09-05 | 阶段 B 标记需回归 | 阶段 C 的 C-02 首次把真实 Whisper Tiny 条目写入生产 selection；依计划 1.2(6)，原先“空 production inventory 可加载”的 B-05/B-27/B-28 证据不再视为当前证据，阶段 B 暂时回退 | 新 selection 与真实 discovery tree 精确分区探针通过；selection/discovery 定向 `23 passed`；全阶段门禁尚待重跑 | `config/model-file-selection.v1.yaml` 与 selection test；未提交 | 重跑含本地假 Hub 的全部 manifest-tool tests、offline/frozen lock、ruff、严格 mypy；通过后恢复阶段 B，不提前勾选 C-02 |
| 2026-09-05 | 阶段 B 回归完成 | Whisper Tiny production selection 填充后，重新验证 loader、CLI status、fixture verifier、生成器及全部攻击/网络矩阵；新条目没有削弱 selection 精确性或 B 阶段退出条件 | 含 loopback 假 Hub 的工具全测 `116 passed in 3.86s`；selection/discovery `23 passed`；release-lock `5 passed`；architecture boundaries OK；offline/frozen lock 33 packages；ruff、严格 mypy（18 files）与 `git diff --check` 通过 | B 全量回归输出；未提交 | 阶段 B 恢复已完成；继续 C-02 任务记录 |
| 2026-09-05 | 阶段 B 标记需回归 | D-12 要求离线 verifier 与服务、UI 一样明确区分 manifest、worker 实现、普通安装资格和 registry enabled；该改动会扩展 verifier/status 合同，原 B-27/B-28 输出证据不再视为当前证据 | 尚未修改实现或运行新门禁 | D-12 任务边界审计；未提交 | 完成共享 worker 支持合同与 verifier 四状态测试后，重跑全部 manifest-tool、offline/frozen lock、ruff、严格 mypy及 diff 门，再恢复阶段 B |
| 2026-09-05 | 阶段 B 回归完成 | verifier 与 status 现从共享只读支持清单计算独立的 manifest/worker/installable/enabled；完整 bundle verify 输出也携带逐模型四状态，support 与 registry 漂移归一为稳定离线验证错误。生成、攻击矩阵和联网边界均未退化 | manifest-tool 全量 `118 passed in 3.90s`；tool lock `33 packages`；offline/frozen imports、Ruff、tool 严格 mypy `11 source files`、architecture 与 `git diff --check` 通过；真实无 bundle status 返回排序 20 项并证明 enabled 与 worker/installable 不等价 | D-12 verifier/support 实现与 B 全量回归输出；未提交 | 阶段 B 恢复已完成；继续 D-12 UI/API 闭环登记 |
| 2026-09-05 | 阶段 B 标记需回归 | 阶段 E 实测发现全量 `discover` 会在排序中的 pyannote gated 条目前停止，使其后的 Qwen、VibeVoice、Voxtral、Whisper 等公开固定 revision 无法按 8.12 继续独立发现。拟为 discover 增加可重复 `--model-id` 过滤，但仍先加载并交叉验证完整冻结输入、拒绝未知或重复 ID，且不改变既有无过滤接口语义 | 尚未修改实现；真实全量 discovery 已在 gated 明示同意门前 fail closed | 阶段 E discovery 输出与 B-08/B-10～B-13/B-28/B-29 合同审计；未提交 | 实现/测试选择性 discovery 后重跑 manifest-tool 全量、offline/frozen、lock、ruff、严格 mypy、架构和 diff；恢复 B 前暂停进一步阶段 E 产物变更 |
| 2026-09-05 | 阶段 B 回归完成 | `discover` 新增可重复 `--model-id`，但仍先读取并交叉验证完整 registry/revision/license/selection/worker-lock 输入；过滤仅从已验证 registry 模型中选择，重复或未知 ID 在网络前失败，无参数时继续遍历全部模型。该能力让阶段 E 在单一 gated 项阻塞时继续独立公开模型，不放宽 B-08 | 新增 CLI 定向 `11 passed`；manifest-tool 全量 `120 passed in 3.90s`；frozen lock `33 packages`、offline/frozen CLI、Ruff、严格 mypy `19 source files`、release-lock `5 passed`、architecture `1 passed` 与 `git diff --check` 全通过 | CLI 与测试、阶段 B 全回归输出；未提交 | 阶段 B 恢复已完成；阶段 E 可用显式 model ID 继续 pyannote 之后的公开 fixed-revision discovery |
| 2026-09-05 | 阶段 B 标记需回归 | 阶段 E 将基于 6 个 FireRed fixed-revision discovery 报告首次扩充 production selection；该输入变化影响 B-05/B-13 与 generator/verifier 当前证据。策展保持精确 include/kinds/exclude，不添加 glob，不把未用权重格式纳入闭包 | 尚未修改 selection；已完成 tree/worker 直接访问审计 | FireRed discovery 报告与 worker adapter 对照；未提交 | 写入 FireRed selection，逐模型对 discovery tree 验证精确分区后跑 manifest-tool 全量和离线/静态/锁/架构门；恢复 B 前不生成生产 manifest |
| 2026-09-05 | 阶段 B 回归进行中；过时 selection 测试失败 | 5 个 FireRed selection 已通过真实 fixed-revision tree 的 include/exclude 精确分区，但全量回归发现 `test_production_selection_contains_only_reviewed_whisper_tiny_closure` 仍硬编码阶段 C 的单模型集合，未随阶段 E 的受审阅集合演进 | 选择性真实 discovery 5/5 成功；manifest-tool `1 failed, 119 passed`，唯一失败为 production selection 集合仍期望仅 Whisper | FireRed selection 与测试失败输出；未提交 | 更新测试为当前已人工审阅的 FireRed+Whisper 精确集合，并加入 VAD 双目录、Punc PyTorch-only、AED/LID/LLM 最低闭包断言；全门通过前不恢复 B |
| 2026-09-05 | 阶段 B 回归完成 | production selection 测试现明确列出当前受审阅的 5 个 FireRed 模型与 Whisper，逐项约束 AED/LID 最低文件、VAD 双目录、Punc PyTorch-only 闭包和 LLM 四 shard/index；不再用阶段 C 的“仅 Whisper”断言阻碍阶段 E，但新增模型仍必须显式加入受审阅集合 | FireRed 真实 discovery/selection 5/5；manifest-tool 全量 `120 passed in 3.91s`；offline/frozen status 显示仅 6 项 selection frozen；frozen lock `33 packages`、Ruff、严格 mypy `19 source files`、release-lock `5 passed`、architecture `1 passed` 与 `git diff --check` 全通过 | selection/test 与阶段 B 回归输出；未提交 | 阶段 B 恢复已完成；继续阶段 E 后续家族策展，任何再改 selection 仍按 1.2(6) 回退并复验 |
| 2026-09-05 | 阶段 B 标记需回归 | 阶段 E 将增加 MOSS TD、MOSS Preview 与 ARK 的 production selection；固定 commit 小文件审计确认 auto_map、相对 import、worker 直接 processor/template 访问和权重 index shard 闭包。所有动态 Python 与模板将显式标为 remote_code | 尚未修改 selection；18 个固定 commit 小文件下载成功并完成引用检索，不含权重或凭证 | `/tmp` remote-code 审阅目录、discovery reports、worker adapter；未提交 | 写入精确 include/kinds/exclude，真实 tree 复验三项后重跑 B 全门；完成前暂停生产 manifest 生成 |
| 2026-09-05 | 阶段 B 回归完成 | production selection 增加 ARK、MOSS TD、MOSS Preview；每项动态 `.py` 和 `.jinja` 均显式分类为 remote_code，配置、processor、tokenizer、index 与全部 shard 闭包保持原布局。受审阅集合测试扩展为 9 项并约束 auto_map/相对 import/worker 直访关键文件 | 三模型真实 fixed-revision discovery/selection 精确分区；manifest-tool `120 passed in 3.93s`；offline/frozen selection 9 项；frozen lock、Ruff、严格 mypy `19 source files`、release-lock `5 passed`、architecture `1 passed` 与 `git diff --check` 全通过 | selection/test、固定内容审计与 B 回归输出；未提交 | 阶段 B 恢复已完成；继续 Qwen 家族策展，后续 selection 变化仍独立回归 |
| 2026-09-05 | 阶段 B 标记需回归 | 阶段 E 将增加 Qwen3 ASR 1.7B、ASR 0.6B 与 ForcedAligner 的三个独立 production selection；真实 tree 显示无动态 Python，各自闭包由 config/preprocessor/chat template/tokenizer 和单一权重格式组成，1.7B index 引用两 shard | 尚未修改 selection；三份 fixed-revision discovery 报告与 qwen worker 路径已审计 | Qwen discovery/worker audit；未提交 | 写入三个精确闭包、真实 tree 复验并跑 B 全门；完成前暂停 manifest 生成 |
| 2026-09-05 | 阶段 B 回归完成 | production selection 增加 Qwen ASR 0.6B、ASR 1.7B 与 ForcedAligner 三个互不共享的条目；各自保留 config/generation/preprocessor/chat-template/tokenizer 和唯一权重格式，1.7B index 与两 shard 同时纳入，无 remote_code。测试明确拒绝虚构 JA 模型 ID | 三模型真实 fixed-revision discovery/selection 精确分区；manifest-tool `120 passed in 3.93s`；offline/frozen selection 12 项；frozen lock、Ruff、严格 mypy `19 source files`、release-lock `5 passed`、architecture `1 passed` 与 `git diff --check` 全通过 | selection/test 与 B 回归输出；未提交 | 阶段 B 恢复已完成；继续 Granite 家族策展 |

| 2026-09-05 | 阶段 B 标记需回归 | 同一 TurboCTC auto_map/trust 漂移将改变 release frozen input 与 Granite production selection；generator 要求 `has_remote_code == trust_remote_code`，不能在 registry=false 时完整表达所需 processor | 固定 config 审计已证明冲突；尚未修改 registry/selection | Granite discovery/config 与 generator 合同；未提交 | 修正 registry 和 Granite selection 后，真实 tree 精确分区并运行 manifest-tool 全量、offline/frozen、lock、Ruff/mypy、release-lock、架构与 diff |
| 2026-09-05 | 阶段 B 回归完成 | Granite 4.1 selection 只保留 Transformers 实际主 index 的三 shard 与 processor/tokenizer/config，排除未被 index/config/worker 引用的额外权重、签名、样例和评测；TurboCTC selection 与修正后的 trust=true 一致，只把 fixed preprocessor auto_map 所需 `processing_ctc_conformer.py` 标为 remote_code，未引用旧代码列入 exclude | 两模型真实 fixed-revision discovery/selection 精确分区；manifest-tool `120 passed in 3.94s`；offline/frozen 完整 frozen-input 读取显示 14 selections/Turbo trust=true；frozen lock、Ruff、严格 mypy `19 source files`、release-lock `5 passed`、architecture `1 passed` 与 `git diff --check` 全通过 | registry/selection/test 与 B 回归输出；未提交 | 阶段 B 恢复已完成；按依赖顺序回归阶段 C loader/Whisper 关键链，再回归 D 状态/API/WebUI |
| 2026-09-05 | 阶段 B 标记需回归 | 阶段 E 将增加两个 Nemotron production selection；worker 只接受根目录恰好一个 `.nemo` 并用 NeMo restore，真实 tree 各有唯一独立 archive，同时存在 safetensors/GGUF 变体，后两类必须显式排除 | 尚未修改 selection；两份 fixed-revision tree 与 worker archive 访问已审计 | Nemotron discovery/worker audit；未提交 | 每项只 include 自身根级 `.nemo`、其余完整 exclude，真实 tree 复验后跑 B 全门 |
| 2026-09-05 | 阶段 B 回归完成 | production selection 增加两个 Nemotron 独立条目，每项 include 仅为自身根级 `.nemo`，所有 safetensors、GGUF、config/tokenizer/docs/图表均显式 exclude；测试约束唯一 archive、model kind 及两类副本排除 | 两模型真实 fixed-revision discovery/selection 精确分区；manifest-tool `120 passed in 3.94s`；offline/frozen selection 16 项；frozen lock、Ruff、严格 mypy `19 source files`、release-lock `5 passed`、architecture `1 passed` 与 `git diff --check` 全通过 | selection/test 与 B 回归输出；未提交 | 阶段 B 恢复已完成；进入 pyannote gated/多来源审计，未获真实条款确认前不访问或冻结其闭包 |

| 2026-09-05 | 阶段 B 状态编辑两次失败后已标记需回归 | 两次尝试以长表格行作上下文追加记录时均因精确匹配失败，`apply_patch` 整体退出且没有部分写入；改用短上下文后，总体 B、阶段 B 与 E 的 A～D 入口已成功回退。阶段 E 将增加 FunASR、Voxtral、VibeVoice 三个 production selection | 两次 `apply_patch` verification failed；短上下文状态补丁成功。固定 config/index/模型卡证明 FunASR 单 Transformers 权重、VibeVoice index 三 shard、Voxtral HF model/config/processor/tekken 闭包 | 计划状态与 `/tmp` 固定内容审计；未提交 | 写入精确 include/kinds/exclude，真实 tree 复验三项并运行 B 全门；恢复前不生成 production manifest |

| 2026-09-05 | 阶段 B 回归准备；离线探针文件名错误 | 为定位 frozen-input loader，组合检查中的 `rg` 成功指出真实实现位于 `inputs.py`，但随后误读不存在的 `frozen_inputs.py`，命令 exit 2；本次不计离线/frozen 门证据 | `sed: can't read .../frozen_inputs.py: No such file or directory` | 检查失败输出；未提交 | 读取真实 `inputs.py` 后运行完整 B 门，保持阶段需回归 |

| 2026-09-05 | 阶段 B 回归完成 | production selection 增加 FunASR、VibeVoice 与 Voxtral，当前覆盖 19 个可公开审计模型。FunASR 仅保留单一 Transformers 权重与 processor/tokenizer/template；VibeVoice index 的三 shard 全部纳入；Voxtral 依据 registry vLLM 和固定模型卡选择 HF model/config/processor/tekken，明确排除 consolidated/params 原生副本。disabled/worker 未实现状态未被 selection 改变 | selection 定向 `11 passed`；三模型真实 fixed-revision discovery 精确分区；manifest-tool `120 passed in 3.44s`；offline/frozen 输出 `20 19`；lock `33 packages`、Ruff、严格 mypy `19 source files`、release-lock `5 passed`、architecture `1 passed`、`git diff --check` 全通过 | production selection、selection tests、固定内容与 discovery reports；未提交 | 阶段 B 恢复已完成；回到阶段 E，复核 8.3 通用闭包后评估 gated pyannote 之外可继续的工作 |

| 2026-09-05 | 阶段 B 标记需回归 | 多来源 provenance 必须由冻结输入产生而非生成器猜测：revision lock 将记录 model→component repository/revision/relationship/source file SHA/size 与 installed/source path，license inventory 覆盖主仓库与 component 仓库；generator 在固定 source revision 验证文件身份，verifier 离线逐字段交叉验证。实际 payload 仍只下载主 Community-1 仓库内嵌字节 | 当前 tool/input/verifier 只允许 registry repository 的精确 license 集合，无法表达该来源；尚未修改 | 冻结输入与工具影响审计；未提交 | 实现严格解析、生成/验证及篡改测试，再跑完整 B 门；通过前 B 不恢复 |

| 2026-09-05 | 阶段 B component-source 输入审计路径失败 | 读取冻结输入实现与配置时误请求不存在的 `tools/model-manifest/tests/test_inputs.py`；同一命令其余 `sed`/`rg` 仍成功显示当前 inputs、revision/license/source inventory 与 release-check 入口，但本次不计完整测试影响面证据 | `sed: can't read tools/model-manifest/tests/test_inputs.py: No such file or directory` | 只读审计失败输出；未提交 | 用 `rg --files tools/model-manifest/tests` 定位真实测试，再实施冻结输入扩展；B 保持需回归 |

| 2026-09-05 | 阶段 B component-source 冻结输入首轮静态失败 | revision lock、license inventory 与 source inventory 已加入 WeSpeaker 固定来源；loader 初步接受 component source 并保持既有 fixture 行为。定向工具行为测试全过，但 Ruff 发现一行 101 字符，组合命令因此未执行严格 mypy | generator/verifier 定向 pytest：`56 passed`；Ruff：`E501` 1 项，mypy 未运行 | inputs、三份冻结 inventory 与门禁输出；未提交 | 折行并补齐 component-source 输入攻击矩阵，随后重跑定向行为与严格静态门；B 保持需回归 |

| 2026-09-05 | 阶段 B component-source 输入行为通过；严格 mypy 失败 | 新增正向冻结与 source identity/path/SHA/size/selection/license 攻击矩阵后全部行为测试通过，Ruff 通过；严格 mypy 发现 exact-key helper 的可变 dict 参数存在 4 处泛型不变性错误，组合命令未进入 diff 检查 | 定向 pytest：`66 passed`；Ruff 通过；mypy：4 个 `arg-type` | inputs/tests 与门禁输出；未提交 | 把只读 helper 参数收窄为协变 `Mapping`，重跑行为、Ruff、严格 mypy 与 diff；B 保持需回归 |

| 2026-09-05 | 阶段 B component-source 输入严格 mypy 二次失败 | 将 helper 从 mutable dict 改为 `Mapping[object, object]` 后，键类型仍因 `str` 不能扩宽到 `object` 而保留同样 4 个 `arg-type`；行为 66 项和 Ruff 继续通过，diff 因 `&&` 未运行 | 定向 pytest：`66 passed`；Ruff 通过；mypy：同 4 个 `arg-type` | 输入 helper 类型修正尝试与输出；未提交 | 按真实输入把 helper 改为 `Mapping[str, object]`，完整重跑；B 保持需回归 |

| 2026-09-05 | 阶段 B component-source 冻结输入完成；待 generator/verifier | 既有 revision lock 以可选顶层 component_sources 冻结 model→source repository/revision/relationship 与 installed/source path、source SHA/size；license/source inventory 精确加入 WeSpeaker CC-BY-4.0。loader 拒绝非固定 commit、不安全路径/哈希/大小、选择外路径、主仓同源、重复/乱序和缺失/额外 license，并把许可证事实注入只读 ReleaseModel | 定向 pytest：`66 passed`；Ruff、严格 mypy（2 files）、`git diff --check` 全通过 | inputs、revision/license/source inventory 与攻击 tests；未提交 | 下一步 generator 在两个 clean run 中发现固定 source revision 并核对 LFS SHA/size，随后把 provenance 写入 manifest；B 保持需回归 |

| 2026-09-05 | 阶段 B generator 测试构造探针环境失败 | 已从既有测试读取 RepoFile 的 LFS fixture 构造；随后误用系统 `python` introspect `huggingface_hub`，该依赖按 B-02 只存在独立 manifest-tool 环境，因此 import 失败。本次探针不计验证证据，且确认 core 环境未意外获得发布工具依赖 | 系统 `python`：`ModuleNotFoundError: No module named 'huggingface_hub'` | 只读测试审计与失败输出；未提交 | 后续相关测试/探针统一使用 `uv run --project tools/model-manifest`；继续 source discovery 生成测试 |

| 2026-09-05 | 阶段 B generator component-source 首轮失败 | 新增 manifest provenance 与双 clean-run source discovery 测试时，补丁把 `build_model_manifest` 的返回块错误落到 `validate_component_source_discovery` 尾部，导致现有及新增生成路径返回 `None`；Ruff 同时发现对应未使用/未定义变量和一处导入排序 | generator 定向 pytest：`6 failed, 36 passed`；Ruff：5 项，严格 mypy未运行 | generator/tests 与失败输出；未提交 | 把 ModelManifestDocument 返回恢复到 build 函数，删除 discovery helper 中误置块并排序 import；B 保持需回归 |

| 2026-09-05 | 阶段 B component-source generator 完成；待 verifier/release check | generator 对每个 component source 在两个 clean run 中用固定 repository+40-char revision 递归 discovery，要求冻结 source path 存在且 size/LFS SHA-256 精确一致；来源若 gated 复用同一明示接受+token 门。manifest 仅在非空时规范化输出 repository/revision/relationship/license/files，并强制 copied 与实际 payload 字节一致、derived 允许差异；单来源 bytes 不变 | generator 定向 pytest：`42 passed`；Ruff、严格 mypy（2 files）通过 | generator/input types 与 source discovery/schema/determinism tests；未提交 | 下一步离线 verifier 逐字段对照 revision/license/selection/manifest，并加入缺失、额外、篡改、copied 不一致攻击；B 保持需回归 |

| 2026-09-05 | 阶段 B component-source verifier 首轮回归失败 | 多来源正向与 identity/file/missing/extra/copied 篡改均按预期 fail closed；但 verifier 复用冻结 input loader 后，一个既有 worker-lock 漂移用例虽仍拒绝，却把稳定错误从 `worker lock SHA-256 differs` 改成内部 `worker dependency lock SHA differs`，造成 1 项兼容回归。Ruff/严格 mypy通过 | verifier 定向 pytest：`1 failed, 39 passed`；Ruff、严格 mypy（2 files）通过 | verifier/tests 与失败输出；未提交 | 在 verifier 边界把该内部输入错误映射回既有稳定诊断，重跑完整 verifier 矩阵；B 保持需回归 |

| 2026-09-05 | 阶段 B component-source 离线 verifier 完成；待 release inventory | verifier 在 socket-independent 冻结输入边界解析 component sources，要求 manifest 仅在非空时出现且逐字段等于 revision+license 输入；installed/source path、source SHA/size、relationship、terms 与 copied payload 一致性均离线验证。缺失、额外及 identity/file 任一字段篡改即失败，既有 worker-lock 稳定诊断保持 | verifier 定向 pytest：`40 passed`；Ruff、严格 mypy（2 files）、`git diff --check` 全通过 | verifier/inputs 与多来源篡改 tests；未提交 | 下一步扩展 source/installed release check 对 component repository 与 revision lock 的严格校验，再运行完整 B 门 |

| 2026-09-05 | 阶段 B release-check 编辑失败 | 尝试一次补丁同时更新 release-check 的调用、revision component 校验与 license union，但补丁内重复声明同一目标文件，`apply_patch` verification failed；整体未写入，没有部分修改 | `apply_patch`：`multiple operations target .../release_check.py` | 编辑失败输出；未提交 | 合并为单一 Update File 补丁后重试；B 保持需回归 |

| 2026-09-05 | 阶段 B release-check 行为通过；mypy 失败 | source/installed release check 已覆盖 component revision 结构与 license union，7 项定向行为通过、Ruff通过；mypy 无法从安全路径 helper 的 bool 返回推导两个局部值必为 str，报 2 个 append `arg-type`，diff 因组合短路未运行 | release-check pytest：`7 passed`；Ruff 通过；mypy 2 项 | release_check/tests 与门禁输出；未提交 | 在已通过安全检查后增加显式 `isinstance(str)` 断言，重跑全部定向门；B 保持需回归 |

| 2026-09-05 | 阶段 B component-source release inventory 完成；待完整 B 回归 | source 与 installed-tree release check 现在读取同一 revision lock，严格验证 component source 顶层/行/文件字段、model/repository/commit/relationship、安全路径、SHA/size、排序唯一与 installed path 不重叠；model-license 与 LICENSES map 精确覆盖 registry 主仓库和全部 component 仓库 | release-check 定向 pytest：`7 passed`；Ruff、mypy（2 files）、`git diff --check` 全通过 | release_check、revision/license/source inventory 与 tests；未提交 | 下一步运行 manifest-tool 全量、offline/frozen、lock、release-lock、architecture 和静态门；只有全绿才恢复 B |

| 2026-09-05 | 阶段 B 完整回归大部通过；架构测试路径失败 | manifest-tool 全量、严格静态、frozen/offline lock 与 release-lock 均通过；架构组合误用不存在的 `tests/contracts/test_architecture_boundaries.py`，pytest exit 4，因 `&&` 后续 architecture script 与 diff 未运行。本次不能作为架构门证据 | tool `146 passed in 4.17s`；Ruff/mypy 19 files；lock 33 packages 与 offline CLI；release-check `7 passed`；架构测试路径 not found | 完整门输出与错误命令；未提交 | 用 `rg --files tests` 定位真实 architecture test 后重跑 script/test/diff；通过才恢复 B |

| 2026-09-05 | 阶段 B component-source 回归完成 | 完整复核 B-01～B-35：五类冻结输入中 revision/license 表达 component provenance；generator 双次固定 source discovery 校验 source LFS SHA/size；规范化 manifest 输出；离线 verifier 覆盖逐字段与 copied payload；工具依赖仍隔离，凭证门未放宽。单来源 Whisper manifest 的 checked-in SHA 未改变 | manifest-tool `146 passed in 4.17s`；Ruff、严格 mypy `19 source files`；frozen lock `33 packages`、offline/frozen CLI；release-check `7 passed`；architecture test `1 passed`、script `OK`、`git diff --check` 通过 | B 工具、冻结 inputs/inventories、verifier/release tests 与门禁输出；未提交 | 阶段 B 恢复已完成；下一步 C loader/supply-chain 解析并交叉验证 component source，A/C/D 仍需回归 |

| 2026-09-05 | 阶段 B 标记需回归 | production 真实生成暴露 B-31 权重 index 闭包只覆盖根级 index：嵌套 `Qwen2-7B-Instruct/model.safetensors.index.json` 内的 shard 名按 index 所在目录相对解析，但当前实现错误地直接与仓库根 selection 比较。selection 本身已精确包含全部四 shard，故不得通过修改上游内容或删除 index 绕过 | `firered_asr2_llm` 16 文件下载完成后生成 exit 2；错误列出四个无父目录 shard 名；selection 明确包含相同父目录下 index 与全部四 shard | production fail-closed 输出、B-31 实现/测试缺口审计；未提交 | 修复为 POSIX 父目录相对解析，拒绝绝对路径、`.`/`..`、反斜线和逃逸；增加嵌套成功及嵌套逃逸/错误 kind/漏 shard 测试，然后重跑 B 全门 |

| 2026-09-05 | 阶段 B 回归命令路径输入错误 | 嵌套 index 修复后的 generator 定向 43 项、manifest-tool 全量 147 项、Ruff、mypy、frozen lock、release tests 与 diff 均通过；并行组合中误输入不存在的 `tests/architecture/test_boundaries.py`，pytest exit 4、未运行 architecture 测试，本命令不计门禁证据 | 错误命令输出：`ERROR: file or directory not found: tests/architecture/test_boundaries.py`；其余门分别为 `43 passed`、`147 passed`、Ruff 通过、mypy 19 files、lock 33 packages、release `7 passed` | 回归输出与追加失败记录；未提交 | 用 `rg --files` 定位仓库真实 architecture test/script 路径并重跑；通过前 B 继续保持需回归 |

| 2026-09-05 | 阶段 B 嵌套 weight-index 回归完成 | generator 现在把 index `weight_map` 中每个安全相对 shard 路径解析到该 index 的 POSIX 父目录，再与 selection/kind 精确比对；根级行为不变。新增 nested 成功、`..` 逃逸拒绝及“同名根级 shard 不得误匹配”回归，既有漏 shard/错误 kind/非严格 JSON 保持 fail closed | generator `45 passed`；manifest-tool 全量 `149 passed in 4.14s`；Ruff、严格 mypy 19 files、frozen lock 33 packages、release `7 passed`、真实 architecture test `1 passed`、script `OK`、`git diff --check` 全通过 | generator/tests 与完整 B 门输出；未提交 | 阶段 B 恢复已完成；清理失败 run 的私有临时工作区后，从头重跑 production 20-model double generation |

| 2026-09-05 | 阶段 B 完成状态编辑纠正 | 完整 B 门通过后的状态补丁因上下文过短，曾误把阶段 A 标题状态改为已完成并让 B 标题保留需回归；总体表未错。发现后立即用带章节标题的精确上下文恢复 A=需回归、B=已完成，未改变任务证据 | 三处状态只读核对发现不一致；精确 `apply_patch` 成功 | 计划状态纠正；未提交 | B 完成证据有效；继续 C，多阶段最终状态核对纳入每次恢复动作 |

| 2026-09-05 | 阶段 B 20 项 selection 回归完成 | pyannote Community-1 固定 commit 的 10 文件按 config 离线引用冻结为 5 个 include（config、embedding、PLDA 两文件、segmentation）和 5 个显式 exclude；真实 gated discovery 证明精确闭包，报告权限符合私有要求。完整工具、静态、lock、release-check 与架构门全绿 | gated discovery model_count=1、10 files/33,695,573 bytes；selection `11 passed`；tool `146 passed in 4.16s`；Ruff/mypy 19 files、lock 33 packages、offline CLI；release/architecture `8 passed`、script OK、diff通过 | production selection、私有 discovery report 与 B 全门输出；未提交 | 阶段 B 恢复完成；A～D再次全部完成。返回 E-PY，实际生成/安装前仍不勾选 E-PY-01/02 |

| 2026-09-05 | 阶段 B 标记需回归；FireRed lock 将改变生产 manifest | 修复 FireRed frozen dependency 后，5 个使用同一 worker 的 manifest environment `worker_lock_sha256` 与 bundle member SHA 必须改变；当前已发布 bundle 将不再通过冻结输入 verifier，不能手改 manifest 或沿用旧 bundle SHA | 依赖传播审计：AED/LLM/VAD/LID/Punc 共用 `workers/firered/uv.lock`；generator/loader/verifier 均严格绑定 registry SHA | 当前 lock/registry/manifests 对照；未提交 | 更新 lock/registry 后重跑 A；随后从发布工具双生成受影响 production（最终仍验证完整 20 项）并跑 B 全门，恢复前 E-M02～06/M21 回退 |

| 2026-09-05 | 新 bundle 后 B 全门首轮受限 socket 失败 | 工具 149 项中 142 项通过，7 个本地 fake-Hub 测试在 fixture 建立 `127.0.0.1` HTTP server 时被当前受限 sandbox 的 socket policy 拒绝，均为 setup error、没有执行断言；同轮工具 lock、Ruff、严格 mypy、release-check、architecture test/script 均通过 | tool `142 passed, 7 errors`，共同错误 `PermissionError: Operation not permitted` at `ThreadingHTTPServer`；lock 33、Ruff、mypy 11 files、release/architecture `8 passed`、script `OK` | B 门输出；未提交 | 在允许 loopback 的外层普通用户边界原样重跑完整 149 项；通过后再恢复 B，保留本轮环境失败记录 |

| 2026-09-05 | 阶段 B 新 FireRed lock/bundle 回归完成 | 在外层普通用户边界允许 loopback 后，本地 fake-Hub 的 fixed revision、gated 403、同/跨 host redirect、短写和超长攻击测试全部通过；结合新 production 双 run、独立 offline/frozen verifier、工具 lock/静态、release 与 architecture 证据，B-01～B-35 当前合同完整闭合 | manifest-tool `149 passed in 4.15s`；独立 verifier 20 项；lock 33、Ruff、严格 mypy 11 files、release/architecture `8 passed`、script `OK` | 新 bundle、B 全门与保留的受限 sandbox 失败记录；未提交 | 阶段 B 恢复已完成；继续 A/C/D 默认 bundle consumer 与全仓回归 |

| 2026-09-05 | 阶段B Qwen backend/new bundle完整回归完成 | 两个Qwen ASR的冻结backend改为真实Transformers实现后，按B-23～B-29完成全部20模型双clean fixed-revision生成；独立offline/frozen verifier验证新bundle、成员SHA和五方合同。随后重新运行B-01～B-35完整工具、fake-Hub攻击矩阵、锁、静态、release与架构门，未发现revision/payload/manifest漂移或凭证泄漏；只有两个Qwen manifest及bundle按预期改变 | production generate exit 0；independent verify `status=verified`/20；bundle `08a44567715a7726f5cc699c106931c517d58645593ee3e82b4637ca2d7b99b3`；tool `149 passed in 4.17s`；lock 33、Ruff、mypy 19 files、release/architecture `8 passed`、script `OK`、diff通过 | 新production工件、生成/verifier与完整B门输出；未提交 | B-01～B-35及退出条件当前闭合，阶段B恢复已完成；依赖顺序进入A全量consumer/合同回归，B工件后续若再变必须再次回退 |
| 2026-09-05 | 阶段B Granite lock/new bundle完整回归完成 | Granite直接TorchAudio依赖改变共享worker lock后，按B-23～B-29完成全部20模型双clean fixed-revision生成；独立offline/frozen verifier和附加审计验证新bundle、20成员SHA、五方合同、两个Granite新environment、18项不变与pyannote provenance。随后重跑B-01～B-35完整fake-Hub攻击矩阵、锁、静态、release和architecture门，未发现漂移或秘密泄漏 | production generate exit 0；independent verify `status=verified`/20；bundle `45fd541defaf0d1d80d8ce011a01394c140a5412758c85aafeaad371c167e0b6`；tool `149 passed in 4.18s`；lock 33、Ruff、mypy 19 files、release/architecture `8 passed in 3.47s`、script `OK`、diff通过 | 新production工件、生成/verifier/附加审计与完整B门输出；未提交 | B-01～B-35及5.10当前闭合，阶段B恢复已完成；严格按依赖进入A全量consumer/合同回归，B输入或工件后续若改变必须再次回退 |
| 2026-09-05 | B/A章节状态补丁目标歧义并已立即纠正 | 恢复B状态的首次补丁以非唯一`状态：需回归`作hunk，实际提前把章节A改为已完成而章节B仍为需回归；顶部总表未受影响，且没有把该短暂文档状态用于跳过测试或完成判断。即时`rg`检出后改用阶段标题作唯一上下文，将A恢复需回归并把B精确设为已完成 | 错误后盘点显示A章节已完成/B章节需回归；纠正补丁后应为A需回归/B已完成 | 计划状态差异、即时纠正补丁；未提交 | 重新读取A的任务、验证要求与退出条件，运行完整A回归；通过前不得恢复A |

| 2026-09-06 | 阶段B Nemotron Git lock/new bundle完整回归完成 | 在第三次完整20模型双clean生成和独立offline/frozen验证后，重新覆盖B-01～B-35全部发布工具、fake-Hub攻击矩阵、凭证脱敏、冻结输入、lock、静态、release及architecture门；新bundle 20成员与21文件附加审计亦已通过，未发现revision/payload/manifest漂移、本机路径或秘密泄漏 | production generate exit 0；independent verify `status=verified`/20；bundle `55069eea4fd82af5132a708ce1658217a652d4af16003352f7f15593e5c11357`；tool `149 passed in 4.16s`；lock 33、Ruff、严格mypy 19 files、release/architecture `8 passed in 3.40s`、script `OK`、diff通过 | 新production工件、generator/verifier/附加审计与完整B门输出；未提交 | B-01～B-35及5.10当前闭合，阶段B恢复已完成；严格按依赖进入A全量consumer/合同回归，B输入或工件后续若再变必须再次回退 |

## 6. 阶段 C：Whisper Tiny 小模型端到端

状态：**已完成**

### 6.1 阶段目标

先用 CPU 小模型 `whisper_tiny_reference` 证明“内置 bundle → 请求安装 → 固定 revision 下载 → 逐文件
验证 → 无网络 worker 健康检查 → 原子激活 → 重启后离线复验”的完整供应链，不开放其他模型安装。

阶段 C 的单模型验证必须使用受控的一模型 registry/license/revision fixture，或与正式 20 模型合同等价的
测试注入；不得降低正式 loader 的“生产 bundle 恰好覆盖 registry 全部 20 个 ID”要求，也不得把测试
fixture 发布为生产 bundle。

### 6.2 进入条件

- [x] 阶段 A、B 已完成。
- [x] `whisper_tiny_reference` 固定 revision 仍与 registry/revision lock 一致。
- [x] 联网生成只在发布工具环境中进行；运行时健康验证可在无网络环境完成。

### 6.3 Whisper Tiny selection 与 manifest

- [x] `C-01` 对 Whisper Tiny 固定 commit 执行 tree discovery，人工审核 worker 实际加载闭包。
- [x] `C-02` 在 `config/model-file-selection.v1.yaml` 冻结精确路径和 kind，不包含其他权重格式、cache、
  训练材料或未使用资源。
- [x] `C-03` 下载实际文件字节并计算 size/SHA，生成规范化 `whisper_tiny_reference.json`。
- [x] `C-04` 通过双重生成证明 manifest 字节可复现，并记录 manifest SHA。

### 6.4 内置 bundle loader

新增并完成 `backend/classscribe/models/manifests.py`：

- [x] `C-05` 实现 `ManifestBundleIndex`、`ManifestBundleEntry` 数据模型。
- [x] `C-06` 实现 `load_manifest_bundle(path, registry, revisions, licenses)`。
- [x] `C-07` 验证 manifest 自身 SHA，以及 bundle/registry/revision/license/worker-lock 的全量一致性。
- [x] `C-08` 提供 `manifest(model_id)` 只读查询，禁止调用者修改已加载 manifest。
- [x] `C-09` 对 bundle、成员文件和 worker lock 执行安全路径、普通文件和非 symlink 校验。
- [x] `C-10` 任一 registry/revision/license/trust/worker/worker-lock 漂移均 fail closed。

默认服务最终必须从
`resource_path("config/model-manifests/v1/bundle.v1.json")` 加载 bundle 并注入 `ClassScribeService`。
缺少 bundle、SHA 不符或交叉验证失败时，服务健康状态必须明确报告模型安装功能不可用；不得静默退回
用户上传任意 manifest。阶段 C 可以先通过依赖注入的一模型 fixture 验证该路径，正式资源在阶段 E 补齐。

### 6.5 端到端安装任务

- [x] `C-11` 用内置/注入的只读 manifest 发起预检，不从用户文件系统读取 manifest。
- [x] `C-12` 验证一次性确认 token、预计下载量、安装量、临时空间、许可证、环境和 remote-code
  披露仍完整保留。
- [x] `C-13` 在全新 XDG 目录按固定 revision 下载，仅允许 manifest 文件集合。
- [x] `C-14` 在网络 namespace 被取消且 Hugging Face offline flags 强制为 1 的 Bubblewrap 环境执行
  load → 对应任务 infer → unload。
- [x] `C-15` 要求健康响应非空、有意义且符合协议；成功后验证 active revision 与
  `supply-chain.json`。
- [x] `C-16` 重启 core 后，在离线环境再次推理并复验 manifest、文件与 aggregate hash。
- [x] `C-17` 健康失败时清理新 revision 并保留/恢复旧 active；安装不得进入普通任务恢复或触发自动下载。

### 6.6 测试与证据

- [x] 从 bundle 取 manifest 完成 request → confirm → download → verify → health → active 全流程。
- [x] manifest SHA 被篡改时在联网前失败。
- [x] packaged resource root 和 source resource root 行为一致。
- [x] worker lock 变化后旧环境不能被运行时复用。
- [x] 全新 XDG 环境测试不提供用户 manifest 文件。
- [x] 真实 CPU 测试记录固定 revision、manifest SHA、音频输入、离线环境、推理响应和重启复验结果。

### 6.7 退出条件

在全新 XDG 环境中，不提供用户 manifest 文件即可安装、重启并离线复验 Whisper Tiny；受控测试 bundle
不得削弱生产 20 模型覆盖要求；其他模型安装入口仍未开放。

### 6.8 进度记录

| 日期 | 状态/任务 ID | 变更或结论 | 验证命令与结果 | 提交/证据 | 阻塞与下一步 |
|---|---|---|---|---|---|
| 2026-09-04 | 未开始 | 尚无 Whisper Tiny 真实 manifest 或端到端证据 | 未运行实施验证 | 本文件 | 阶段 B 完成后开始 `C-01` |
| 2026-09-05 | 阶段 C 进入条件完成；阶段进行中 | 复核阶段 A/B 均已完成；通过发布工具冻结输入 loader 交叉核对 Whisper Tiny 的 registry/revision/license/worker lock，固定身份为 `Systran/faster-whisper-tiny@d90ca5fe260221311c53c58e660288d3deb8d356`、worker `moss_en`；联网只存在于独立发布工具，真实 worker 已使用 `local_files_only=True` 的 CPU int8 加载路径 | `uv run --offline --frozen --project tools/model-manifest python` 冻结输入探针输出目标 model/repository/revision/worker/backend/lock SHA 并通过；阶段 B 退出证据 `116 passed` 保持 | registry、revision lock、license、worker lock 与 `moss_en` adapter；未提交 | 下一步重新读取并执行 `C-01`，对固定 commit 做真实 tree discovery 与 worker 闭包审核 |
| 2026-09-05 | 完成 `C-01`；阶段进行中 | 发布工具真实解析 Whisper Tiny 固定 commit，递归 tree 恰为 `.gitattributes`、`README.md`、`config.json`、`model.bin`、`tokenizer.json`、`vocabulary.txt`；审核 `moss_en` adapter 与已锁定 faster-whisper 实现，确认本地 CPU int8 加载必须保留后四个运行文件，`preprocessor_config.json` 在该 tree 中不存在且实现有确定默认值，前两个仅为仓库元数据/说明 | 联网 discovery 成功且 resolved revision 精确一致；私有 selection-review 报告 SHA-256 `37d08e8b1700c5ef3093f49f5eaca4a6f12e3e6705a067796be3a2b08441c835`；固定 tree/运行闭包探针通过；报告目录/文件权限分别 0700/0600 | 真实上游 tree 报告、`workers/moss_en/adapter.py`、锁定 faster-whisper `utils.py`/`transcribe.py`；未提交 | 下一步重新读取并执行 `C-02`，只冻结四个运行文件并显式 exclude 两个非运行文件 |
| 2026-09-05 | 完成 `C-02`；阶段进行中 | production selection 仅冻结审核后的四个精确运行文件：config、CTranslate2 model、local tokenizer 与 vocabulary；`.gitattributes`、README 明确进入 exclude，无 glob、cache、训练材料或其他权重格式；include+kinds+exclude 与 C-01 的六文件 tree 恰好分区 | selection/discovery 定向 `23 passed`；真实报告闭包探针输出 4 个 include、总发现大小 78,203,619 bytes；selection schema 与严格 loader 通过；依计划触发并完成阶段 B 全量回归 `116 passed`；ruff、严格 mypy 与 `git diff --check` 通过 | `config/model-file-selection.v1.yaml`、selection test、C-01 discovery 报告；未提交 | 下一步重新读取并执行 `C-03`，下载四个实际文件、复算 SHA/size 并生成规范化 manifest |
| 2026-09-05 | 完成 `C-03`；阶段进行中 | 在私有空临时工作区按固定 commit 与四文件 allowlist 完成真实下载，清除下载元数据后从安全重开的实际普通文件流式复算 size/SHA，并生成规范化 `whisper_tiny_reference.json`；未生成或发布一模型 `bundle.v1.json`，正式 20 模型覆盖合同保持不变 | resolved revision 精确为冻结 commit；实际 payload 共 78,203,619 bytes；manifest schema、canonical UTF-8/LF、大小求和、冻结 registry/revision/license/selection/worker-lock 交叉合同及无秘密/本机路径探针通过；checked-in bytes 与生成 bytes 相同；generator 定向 `39 passed`，ruff、严格 mypy（18 files）与 `git diff --check` 通过 | `config/model-manifests/v1/whisper_tiny_reference.json`；本次 manifest SHA-256 `2d805d29c6ec3465a0824d2d87c121c3b16514856bdbdaee3d0b90750c16eba8`；未提交 | 下一步重新读取并执行 `C-04`，从另一个空工作区重新下载并要求 payload 与 manifest 字节完全相同后再登记可复现性证据 |
| 2026-09-05 | 完成 `C-04`；阶段进行中 | 使用正式生成编排与受控一模型输入，在两个彼此独立的空私有工作区重新执行 fixed-revision discovery、四文件下载、实际字节哈希和 canonical manifest 生成；内置 revision/payload/manifest 双生成门通过，结果还与 C-03 checked-in bytes 逐字一致；仅在内存构造测试索引且未发布一模型 bundle | 两次 clean generation 均成功，每次 payload 为 78,203,619 bytes、manifest 为 1,425 bytes；复现 manifest SHA-256 均为 `2d805d29c6ec3465a0824d2d87c121c3b16514856bdbdaee3d0b90750c16eba8`；生成工作区由工具生命周期自动清理 | 正式 `generate_release_bundle` 双重生成门、checked-in Whisper Tiny manifest；未提交 | 下一步重新读取并执行 `C-05`，核对阶段 A 已建立的只读 bundle 数据模型是否完整满足本任务后再决定最小改动 |
| 2026-09-05 | 完成 `C-05`；阶段进行中 | 复用阶段 A 已建立且本轮未失效的只读 bundle 数据模型：entry、generator、index 均为 frozen/extra-forbid Pydantic 合同，覆盖固定 schema/generator 版本、SHA 格式、安全单层成员名、保留名、成员非空、排序及 ID/path 唯一性；通过公共 models 包导出，无需建立第二套模型 | bundle/manifest 合同 `11 passed`；定向 ruff 与严格 mypy（2 files）通过；`git diff --check` 通过 | `backend/classscribe/models/manifests.py`、`backend/classscribe/models/__init__.py` 及合同测试；未提交 | 下一步重新读取并执行 `C-06`，实现 `load_manifest_bundle(path, registry, revisions, licenses)`；C-07～C-10 的深层校验不提前标记 |
| 2026-09-05 | 完成 `C-06`；阶段进行中 | 新增 `LoadedManifestBundle` 与 `load_manifest_bundle(path, registry, revisions, licenses)`：严格解析 bundle index 和全部成员为现有只读数据合同，核对 registry revision/facts 日期与 inventory 覆盖；受控一模型 fixture 只有与同一模型 registry 配套时可加载，面对正式 registry 时必须恰好覆盖全部 20 个 ID，不存在测试模式降级开关 | loader 定向 `2 passed`，覆盖受控成功路径与一模型 bundle 对 production registry 的拒绝；定向 ruff、严格 mypy（2 files）及 `git diff --check` 通过 | `backend/classscribe/models/manifests.py`、公共导出与 `tests/unit/test_model_manifests.py`；未提交 | 下一步重新读取并执行 `C-07`，增加实际成员 SHA、完整 revision/license/manifest/environment 与 worker lock 字节一致性验证 |
| 2026-09-05 | 完成 `C-07`；阶段进行中 | loader 要求 bundle 与成员均为 canonical UTF-8/LF JSON；从成员实际 bytes 复算 SHA 后再解析，要求 entry 文件名与 ID 对应，并把每个 manifest 的 repository/revision/worker/trust、license 三元组、runtime backend/dtype/worker lock 环境、大小总计与 registry/revision/license inventory 全量绑定；最后读取真实 worker lock bytes 复算 SHA | loader 定向 `3 passed`：受控完整成功、成员 byte 篡改在使用前失败、worker lock byte 漂移失败；定向 ruff、严格 mypy（2 files）及 `git diff --check` 通过 | loader 深层交叉合同与 `tests/unit/test_model_manifests.py`；未提交 | 下一步重新读取并执行 `C-08`，提供按 model ID 的只读查询并证明返回对象及嵌套 environment 不可修改 |
| 2026-09-05 | 完成 `C-08`；阶段进行中 | `LoadedManifestBundle.manifest(model_id)` 按 ID 返回已校验的 frozen `ModelManifest`，未知 ID 明确抛 `KeyError`；成员 tuple、文件 dataclass 与 environment 只读 mapping 均不可原地修改，`as_dict()` 返回独立公开副本且不反向污染 bundle | loader+manager 定向 `34 passed`；触及 manager 后完成阶段 A 受影响回归：合同组 `48 passed`、API integration 沙箱外 `6 passed`；ruff、严格 mypy（3 files）及 `git diff --check` 通过 | loader 查询/深层冻结、manager 显式序列化与 tests；未提交 | 下一步重新读取并执行 `C-09`，用安全打开与攻击测试覆盖 bundle、成员及 worker lock 的路径、普通文件和非 symlink 要求 |
| 2026-09-05 | 完成 `C-09`；阶段进行中 | bundle index、每个 manifest 成员与 worker lock 统一从推导出的只读资源根用 dir-fd 逐层打开，目录和文件均使用 `O_NOFOLLOW`；打开文件前后核对 ordinary/single-link、device/inode/size/mtime 并限制读取大小，拒绝非标准内置布局、逃逸、symlink、特殊文件、hardlink 与读时替换 | loader 安全文件测试 `8 passed`，覆盖三类资源各自 symlink、manifest FIFO、manifest hardlink、受控成功与既有 SHA/只读合同；定向 ruff、严格 mypy（2 files）及 `git diff --check` 通过 | runtime loader 安全读取边界与攻击 tests；未提交 | 下一步重新读取并执行 `C-10`，逐类注入 registry/revision/license/trust/worker/worker-lock 漂移并确认全部在返回 bundle 前失败 |
| 2026-09-05 | 完成 `C-10`；阶段进行中 | 为 loader 建立逐类冻结合同漂移矩阵：bundle/registry revision、模型 revision、revision lock 单边变化、license、trust policy、worker identity 与 worker-lock 声明任一改变都在返回 `LoadedManifestBundle` 前失败；不存在按字段忽略或 fallback 路径 | loader 全矩阵 `15 passed`，其中 7 类参数化 drift 全部得到预期 fail-closed 错误；定向 ruff、严格 mypy（2 files）及 `git diff --check` 通过 | loader 交叉合同与 drift tests；未提交 | 下一步重新读取并执行 `C-11`，审计 `ClassScribeService` 安装预检接口并通过受控 bundle 注入消除用户文件 manifest 来源 |
| 2026-09-05 | 完成 `C-11`；阶段进行中 | `ClassScribeService` 支持注入 `LoadedManifestBundle` 并新增只接收 model ID 的 `request_bundled_model_install`；预检 manifest 只能由 bundle 查询取得，bundle 缺失或成员不存在立即以完整性错误失败，不会读取用户文件或回退到客户端内容；公开 HTTP schema 的最终切换仍严格留给阶段 D | bundle-only 预检与 loader 组合 `16 passed`；缺少 bundle member 的 production registry 其他模型在预检前失败；既有 request→confirm→health API 沙箱外回归 `1 passed`；ruff、严格 mypy（2 files）及 `git diff --check` 通过 | service bundle 注入、新内部预检入口与 integration test；未提交 | 下一步重新读取并执行 `C-12`，扩充 bundle 预检响应/测试以完整证明一次性 token、两类容量、临时空间、license、environment 与 remote-code 披露 |
| 2026-09-05 | 完成 `C-12`；阶段进行中 | bundle-only 预检响应保留随机一次性 confirmation token、预计下载/安装 bytes、峰值所需与当前可用空间、license ID/URL/terms、完整 environment；新增索引中已验证的 manifest SHA 及显式 remote-code 文件计数/路径。Whisper Tiny 无 remote code，准确披露为 0 与空列表而非省略 | bundle 预检、loader 与一次性 token 定向 `17 passed`；断言 SHA、token 长度、两类容量、空间裕量、license、environment 和 remote-code 全字段；ruff、严格 mypy（3 files）及 `git diff --check` 通过 | service preflight disclosure、bundle SHA query 与 integration/manager tests；未提交 | 下一步重新读取并执行 `C-13`，在全新 XDG 根用真实 downloader 下载固定 Whisper revision，并证明网络请求/落盘集合只含 manifest 四文件 |
| 2026-09-05 | 完成 `C-13`；阶段进行中 | 使用 core 运行时 `HuggingFaceDownloader`，在全新且自动清理的 XDG data/cache/config/runtime 根按 manifest repository 与 40 字符固定 commit 下载；请求集合只来自四个 `ManifestFile`，下载后枚举落盘普通文件并逐个复算 size/SHA，不执行健康检查或激活 | 真实联网下载成功，resolved receipt 保持 `d90ca5fe260221311c53c58e660288d3deb8d356`，恰好 4 文件、78,203,619 bytes，集合及全部实际 SHA 与 manifest 全等；`activated=false` | C-03 manifest、运行时 downloader 与本次 fresh-XDG 实际下载输出；未提交 | 下一步重新读取并执行 `C-14`，准备健康 WAV 与真实模型目录，在取消网络 namespace、HF offline flags=1 的 Bubblewrap 中验证 load→infer→unload |
| 2026-09-05 | `C-14` 进行中；尚未完成 | 审计真实生命周期时先修复 health checker：无论 inference 成功与否，已 load 的 worker 都必须显式执行 `unload`，失败则健康检查失败。随后两次真实 fresh-XDG 尝试都在 worker 环境 provisioning 阶段暴露共享构建合同缺陷，尚未进入 Bubblewrap，不能把单元生命周期或下载成功冒充 C-14 证据 | health 生命周期单测 `3 passed`，精确观察 `load → transcribe_batch → unload`；ruff、严格 mypy 通过。真实 provisioning 两次均因复制的本地 protocol 在隔离构建中缺少 Hatchling 失败；已落盘 extra build dependency 与 preview 修复，尚待 lock/回归/真实重试 | health checker/测试、worker provisioner、全部 worker build 配置；未提交 | C-14 保持未勾选；先完成阶段 A 受影响回归，再重跑真实断网 Bubblewrap 生命周期 |
| 2026-09-05 | `C-14` 继续进行；第三次真实尝试未完成 | 固定 Whisper revision 与四文件实际字节再次下载成功；worker sync 因 `UV_NO_CONFIG=1` 禁用项目 extra build dependency，仍在 Bubblewrap 前失败。该失败使刚完成的阶段 A provisioning 回归再次失效，C-14 继续不勾选 | 实际 sync 调用了 frozen/no-dev/no-editable/preview，但 `classscribe-protocol` 构建后端仍无法导入 Hatchling；protocol 独立 build 成功，且 uv 官方文档确认 `--config-file` 可替代全部自动发现配置 | 第三次真实运行输出与官方配置文件语义；未提交 | 用 provisioner 生成的单一可信 `uv.toml` 重试；只有真实 RPC 记录为 load→infer→unload 才完成 C-14 |
| 2026-09-05 | 完成 `C-14`；阶段进行中 | 第四次尝试继续证明显式配置单独不足；详细复现最终定位 Fedora 默认 Python 3.14 与 worker Python 3.12 下，stdlib `venv --copies` 使 uv 0.12 构建 backend 选择错误 base interpreter。改为由 uv 按 frozen project 创建 3.12 环境、sync 后原子复制其解释器，并修正 production `.venv/bin/python` + project `worker.py` 的 sandbox 根布局；第五次真实运行成功进入断网 Bubblewrap并完成生命周期 | 真实 `whisper_tiny_reference`：`Systran/faster-whisper-tiny@d90ca5fe260221311c53c58e660288d3deb8d356`，manifest SHA `2d805d29c6ec3465a0824d2d87c121c3b16514856bdbdaee3d0b90750c16eba8`，payload 78,203,619 bytes；Fedora Linux 44、kernel `7.1.12-200.fc44.x86_64`、CPU/faster-whisper int8；健康 WAV 60,056 frames@16 kHz mono PCM、SHA `afaeea6aa6c04dab00068008f2513669e5e4d20e994a6196ff8bcf11e3b30de4`；Bubblewrap `--unshare-all`，三项 HF offline flag 均为 1；真实记录 RPC 为 `load → transcribe_batch → unload`，`healthy=true`、backend `faster_whisper_cpu_int8` | 生产 downloader/provisioner/sandbox/worker/health checker 真实输出；worker lock `workers/moss_en/uv.lock@ac392045030024e28a7ec9103fce0a73f96021d4880324d8e770a2fd6e17890c`；未提交 | C-14 已完成；先完成阶段 A 最终受影响回归，再重新读取 C-15。尚未验证 active revision 或 `supply-chain.json`，不得提前勾选 C-15 |
| 2026-09-05 | 完成 `C-15`；阶段进行中 | 在新的受限安装根经真实 `ModelManager.request_user_install → install_confirmed` 完成固定下载、hash、Bubblewrap 健康与原子激活；透明记录器取得实际协议解码后的 `RPCResponse`，响应非空且有意义，随后由 manager 再解析 active 与 supply-chain 并复算 payload | 真实响应：language `en`、1 segment、raw/normalized 非空，normalized 摘要为 `Class crime verify real speech recognition.`；生命周期仍为 `load → transcribe_batch → unload`，CPU int8/offline；`active.json` format 1 指向冻结 revision；`supply-chain.json` format 1、manifest 全等、downloaded bytes 78,203,619、health=true；aggregate SHA `2b0d289c7d1dd35f4293592741289355270909ed2b8eb4767fcc0f3cbb90d44b`；`resolve_for_runtime` 复验通过 | Fedora Linux 44 / kernel `7.1.12-200.fc44.x86_64`；manifest SHA `2d805d29c6ec3465a0824d2d87c121c3b16514856bdbdaee3d0b90750c16eba8`；健康 WAV SHA `afaeea6aa6c04dab00068008f2513669e5e4d20e994a6196ff8bcf11e3b30de4`；生产 manager/downloader/health/sandbox/worker 输出；未提交 | C-15 已完成；保留本次安装树，重新读取并执行 C-16 的 core 重启与完全离线复验 |
| 2026-09-05 | `C-16` 进行中；首次重启复验未完成 | 新 Python/core 进程在独立 user/net namespace 中确认外网不可达，且在不调用 downloader、不允许运行 provisioning runner 的条件下，先从 C-15 安装树重新解析 active/supply-chain，四个 manifest 文件 size/SHA 与 aggregate 均通过；随后 health worker 因 Unix socket path 过长而在 inference 前失败 | `active` revision、嵌入 manifest、4 文件及 aggregate SHA `2b0d289c7d1dd35f4293592741289355270909ed2b8eb4767fcc0f3cbb90d44b` 已离线复验；重启推理结果为 `worker transport failed: AF_UNIX path too long`，不计通过；安装树保留供修复后同条件重试 | C-15 保留安装树与 C-16 新进程输出；未提交 | C-16 保持未勾选；缩短 health socket identity、补长路径测试并完成阶段 A 回归后重试同一离线安装树 |
| 2026-09-05 | 完成 `C-16`；阶段进行中 | transport 改为由 core 持有 runtime 目录 fd 并经短 `/proc/self/fd/<fd>/<name>` 路径连接；sandbox worker 仍只见 `/run/classscribe/<name>`，目录 fd 不传入 Bubblewrap。修复后在全新 core 进程、独立 user/net namespace、禁止 downloader 且 provisioning runner 设为 fail-fast 的条件下，复用 C-15 同一安装树完成第二次离线推理 | 外网探针不可达；环境按 lock 地址直接复用且 runner 未调用；active revision 精确一致；4 个文件 size/SHA、checked-in 与嵌入 manifest、aggregate SHA `2b0d289c7d1dd35f4293592741289355270909ed2b8eb4767fcc0f3cbb90d44b`、supply-chain 前后字节语义均一致；真实第二次 `RPCResponse` 为 language `en`、1 segment、normalized `Class crime verify real speech recognition.`；`load → transcribe_batch → unload`、CPU int8、runtime offline 全通过；临时安装树成功后清理 | manifest SHA `2d805d29c6ec3465a0824d2d87c121c3b16514856bdbdaee3d0b90750c16eba8`；长 host socket 真实 transport 定向 `18 passed`；C-16 新进程输出；未提交 | C-16 已完成；先完成阶段 A 的最终 WorkerProcess 受影响回归，再重新读取并执行 C-17 |
| 2026-09-05 | 完成 `C-17`；阶段进行中 | 健康失败清理逻辑先比较当前与旧 active，未发生切换时不再重写旧指针；失败升级测试逐字节确认 `active.json` 未变、旧 revision 集合未变、新 revision 与 staging 均消失，且 downloader 只调用一次。运行时缺失/篡改测试继续以禁用 socket 证明只本地失败；架构门新增约束，普通 `jobs`/`classroom` 执行与恢复代码不得调用安装入口或构造 Hugging Face downloader | manager/job state/classroom/API/architecture 受影响回归 `46 passed in 4.50s`；其中 C-17 定向组合 `11 passed in 3.96s`；ruff、严格 mypy（manager 与 architecture checker）及 `git diff --check` 通过 | manager 失败回滚、C-17 单测、architecture gate；未提交 | C-17 已完成且阶段 A 保持已完成；阶段 C 仍不得退出，下一步按 6.6 顺序复验“从 bundle 完成完整安装链”并补齐未勾选证据 |
| 2026-09-05 | 阶段 C 测试证据 1/6 完成；阶段进行中 | 新增受控 `LoadedManifestBundle` 集成路径，由 `request_bundled_model_install(model_id)` 取得只读 manifest 和索引 SHA，再用一次性 token 确认；追踪 downloader 与 health 次序，最终从本地重新解析 active、逐文件/aggregate 校验的供应链记录及数据库健康状态。测试 manifest 与 production registry 身份一致，但仅通过依赖注入存在，未写入生产 bundle，未放宽 20 模型 loader 合同 | bundle 完整事务与既有 bundle preflight `2 passed in 0.47s`；ruff、严格 mypy及 `git diff --check` 通过 | `tests/integration/test_api_v1.py` bundle request→confirm→download→verify→health→active 集成测试；未提交 | 下一步按 6.6 顺序验证 manifest SHA 篡改必须在联网前失败 |
| 2026-09-05 | 阶段 C 测试证据 2/6 完成；阶段进行中 | 在受控 bundle 成员保持索引旧 SHA 的同时篡改 manifest 实际字节，并将全局 socket 构造替换为 fail-fast 记录器；loader 在成员 SHA-256 比对处拒绝，网络尝试计数保持 0。该路径发生在 service、安装计划和 downloader 之前 | manifest SHA 篡改与 production coverage 定向 `2 passed in 0.21s`；ruff、严格 mypy（loader/test）及 `git diff --check` 通过 | `tests/unit/test_model_manifests.py`；未提交 | 下一步按 6.6 顺序验证 packaged resource root 与 source resource root 行为一致 |
| 2026-09-05 | 阶段 C 测试证据 3/6 完成；阶段进行中 | 从同一受控 source-layout 根按 RPM `%install` 的 `config`/`workers` 复制语义构造 `/usr/share/classscribe` 形状的 staged packaged root；两者均经 `CLASSSCRIBE_RESOURCE_ROOT` 与正式 loader 解析，index、manifest 深层值相同；随后分别做同样的成员字节漂移，异常类型与消息完全一致。此项验证 staged package 布局，不冒充尚属阶段 F 的正式 RPM 安装验收 | source/packaged root 与既有 resource override 定向 `2 passed in 0.18s`；ruff、严格 mypy及 `git diff --check` 通过 | `tests/unit/test_model_manifests.py`、`backend/classscribe/resources.py` 既有 resolver、RPM 布局合同；未提交 | 下一步按 6.6 顺序验证 worker lock 变化后旧环境不能被运行时复用 |
| 2026-09-05 | 阶段 C 测试证据 4/6 完成；阶段进行中 | 新测试先按 lock A 生成并成功解析环境，再将 source `uv.lock` 改为不同字节/不同 SHA；运行期 `resolve()` 立即报告新 lock 尚未 provision，不能返回仍存在的旧目录。只有显式再次 `ensure()` 才生成 lock B 地址的新环境，随后 runtime 解析新目录；runner 总计两次，旧环境从未被新 lock 命中 | worker environment 全文件 `3 passed in 0.15s`；ruff、严格 mypy及 `git diff --check` 通过 | `tests/unit/test_worker_environment.py`；未提交 | 下一步按 6.6 顺序验证全新 XDG 环境不提供用户 manifest 文件 |
| 2026-09-05 | 阶段 C 测试证据 5/6 完成；阶段进行中 | bundle 完整事务测试显式核对 config/data/cache/runtime 四个 XDG 根全部位于新建临时 home；安装前与 active/供应链复验后均递归确认不存在用户 manifest 文件。manifest 唯一来源为依赖注入的只读 bundle；XDG 中只落盘模型 payload、`supply-chain.json` 与 active 指针 | fresh-XDG bundle 完整事务 `1 passed in 0.42s`；ruff、严格 mypy及 `git diff --check` 通过 | `tests/integration/test_api_v1.py`；未提交 | 下一步按 6.6 最后一项汇总并复验真实 CPU 的固定 revision、manifest SHA、音频、离线环境、响应与重启证据 |
| 2026-09-05 | 阶段 C 测试证据 6/6 完成；阶段进行中 | 逐字段审计 C-14～C-16 的真实记录：固定 `Systran/faster-whisper-tiny@d90ca5fe260221311c53c58e660288d3deb8d356`；manifest SHA `2d805d29c6ec3465a0824d2d87c121c3b16514856bdbdaee3d0b90750c16eba8`；健康 WAV SHA `afaeea6aa6c04dab00068008f2513669e5e4d20e994a6196ff8bcf11e3b30de4`、60,056 frames/16 kHz/mono PCM；Bubblewrap 与三项 HF offline flag；真实 `RPCResponse` 的非空 normalized 文本与 segment；新 core/断网 namespace 下同 active、四文件、aggregate、供应链与第二次推理。当前重新计算 checked-in manifest 与 worker lock SHA 均仍匹配原记录 | 本轮静态复算 manifest SHA、worker-lock SHA、revision、环境与四文件总 bytes 全部匹配；真实执行结果沿用同日已登记且未被后续相关改动失效的 C-14/C-15/C-16 输出，不把 fixture 测试替代真实证据 | C-14～C-16 真实生产 downloader/provisioner/Bubblewrap/worker 记录及当前 checked-in manifest/lock；未提交 | 6.6 六项齐备；下一步运行阶段 C 全量相关测试与静态门，并逐条复核 6.7 后才能标记阶段完成 |
| 2026-09-05 | 阶段 C 已完成 | 逐项复核 C-01～C-17、6.6 六项测试证据和 6.7：fresh-XDG 无用户 manifest 的 Whisper Tiny 固定下载、真实离线健康、active/supply-chain 与新 core 离线复验已成立；受控一模型 bundle 对 production registry 明确失败，未削弱 20 模型全覆盖；本阶段新增 bundle 路径仅含 Whisper，其他 19 个 model ID 均在预检前因成员缺失而拒绝。阶段 D 仍需移除公开 API 的既有客户端-manifest 合同 | 阶段 C core 相关全量 `120 passed in 7.37s`；独立 manifest-tool 全测 `117 passed in 3.92s`；ruff、严格 mypy（16 source files）、architecture boundaries 与 `git diff --check` 全通过 | 阶段 C 实现、tests、C-14～C-16 真实记录；未提交 | 无阶段 C 阻塞；下一步重新读取阶段 D 进入条件与 `D-01`，不得把阶段 D 尚未完成的公开 API/WebUI 改造算入阶段 C |

| 2026-09-05 | 阶段 C 标记需回归 | production registry trust 字段即将修正，loader 的 registry↔manifest trust 交叉验证输入发生变化；虽不改变 Whisper 固定条目，原 production 覆盖/篡改证据仍需复验 | 尚未修改实现或运行回归 | Granite 固定内容审计与 loader 影响分析；未提交 | registry 修正后重跑 production coverage/交叉合同与 Whisper bundle 关键安装、离线健康、重启链；通过前不恢复阶段 C |
| 2026-09-05 | 阶段 C 回归准备；测试节点检索命令失败 | 首次节点枚举在 zsh 中使用不存在的 `tests/integration/test_model*` glob，shell 以 `no matches found` 终止第二段检索；前一段计划记录检索成功，但本命令不构成任何测试结果 | zsh exit 1：`no matches found: tests/integration/test_model*` | 命令失败输出；未提交 | 改用 `rg --files`/真实文件名枚举，不使用未解析 glob；随后运行 loader 与 Whisper 关键链回归 |
| 2026-09-05 | 阶段 C 回归完成 | 在 TurboCTC trust 修正后的完整 production registry 上重跑 loader 覆盖/篡改、下载、manager、health、environment、sandbox、inference、resident、worker transport 与 bundle API 安装事务。真实模型测试因当前没有保留的本地 checkpoint 正确 skip，不计新通过；复算确认 Whisper 固定身份、manifest SHA 与 moss_en lock SHA 与 C-14～C-16 同日真实证据逐字一致，本轮没有修改其 manifest、lock、adapter、provisioner、sandbox 或 health 实现，故既有真实 CPU/断网/重启证据未失效 | 组合 `89 passed, 1 skipped in 2.44s`，skip 原因明确为未设置本地真实模型路径；Whisper manifest SHA `2d805d29...c16eba8`、worker lock SHA `ac392045...17890c` 与原记录一致；阶段 A 严格 mypy/Ruff/architecture 与 B 全工具门已通过 | loader/manager/worker/API 回归、SHA 复算、既有 C-14～C-16 真实输出；未提交 | 阶段 C 恢复已完成；按依赖顺序回归阶段 D 的 API/status/WebUI 矩阵，仍不得把 skip 当作新真实验收 |

| 2026-09-05 | 阶段 C 标记需回归 | runtime bundle loader 与 `ModelManifest` 将读取 component-source provenance 并对 revision lock/license inventory 全量核对；安装后的 `supply-chain.json` 必须保留该来源审计。既有 Whisper 单来源 manifest 不应新增字段或改变 SHA，但需在最终实现上回归 | 当前 loader 严格拒绝 manifest 额外字段且要求 license repositories 恰等于 registry 主仓库；尚未修改 | loader/manager 影响审计；未提交 | 完成不可变解析、冻结交叉验证、安装审计和攻击测试后回归 C 关键链 |

| 2026-09-05 | 阶段 C component-source loader 首轮失败 | loader 已解析并冻结 component provenance；定向矩阵中 60 项通过。把 manifest relationship 篡改为 copied 时，运行时模型正确在更早的实际 payload/source 字节不一致门拒绝，测试却只接受后续 `component sources differ` 文本，造成 1 项失败；Ruff 另报一处 SIM300，mypy因组合短路未运行 | loader/manager pytest：`1 failed, 60 passed`；Ruff：`SIM300` 1 项 | manifests/tests 与失败输出；未提交 | 测试为该攻击断言最早的 copied-integrity 诊断，其余仍断言 frozen drift；改写条件风格并重跑；C 保持需回归 |

| 2026-09-05 | 阶段 C component-source loader/supply-chain 实现完成；待完整回归 | built-in loader 接受 base 或 base+非空 component_sources 两种严格字段集，解析 revision lock 的 component rows，以 license inventory 补全来源许可并逐字段比较 manifest；拒绝 extra/missing source、主仓同源、未知模型、乱序/重复、路径/hash/size/类型和 copied 字节不一致。受控单模型 fixture 过滤无关 source；安装 `supply-chain.json` 保留完整 provenance；checked-in Whisper manifest SHA 仍为 `2d805d29…` | loader/manager 定向 pytest：`61 passed`；Ruff、mypy（2 files）、`git diff --check` 全通过；Whisper SHA 只读复核不变 | manifests/manager/controlled fixtures 与攻击 tests；未提交 | C 保持需回归，待 D 披露稳定后重跑完整 bundle→install→health→restart 关键链；下一步 D API/UI 只读披露 component sources |

| 2026-09-05 | 阶段 C component-source 回归真实 C-15 通过 | 在全新受限安装根重新执行 production downloader→manager→fresh frozen worker env→Bubblewrap health→atomic active；固定 Whisper revision、4 文件 78,203,619 bytes、manifest SHA 与旧证据一致。新 InstallationPlan component_sources 字段对单来源为空，不改变下载；supply-chain manifest 全等，真实 RPC 非空且 lifecycle 为 load→transcribe_batch→unload，CPU int8/runtime offline | 真实脚本 exit 0；health=true；aggregate `2b0d289c…d44b`；音频 60,056 frames@16k，SHA `afaeea6a…de4`；Fedora 44/kernel 7.1.12 | 本轮新真实安装树、active/audit/RPC 输出；未提交 | 安装树保留给新进程 C-16；下一步在独立无网络 namespace 禁止 provisioning/downloader，复验 active/files/aggregate/audit 和第二次 inference |

| 2026-09-05 | 阶段 C component-source 回归真实 C-16 通过 | 新 Python/core 进程在独立 user/net namespace 中确认外网不可达，禁止 provisioning runner 并复用冻结环境；重新解析 active 与 supply-chain，4 文件 size/SHA、aggregate、嵌入/checked-in manifest 全等，再次完成离线 Bubblewrap inference。响应与 C-15 一致、lifecycle 完整，脚本成功清理本轮临时安装树 | 真实重启脚本 exit 0；network unreachable、provisioning runner 未调用；health=true、runtime_offline、aggregate/manifest SHA一致、RPC非空 | 本轮新 C-16 进程输出；未提交 | 下一步跑 C 的 loader/download/manager/health/environment/sandbox/worker/API 组合回归并复核 6.7；全绿后恢复 C |

| 2026-09-05 | 阶段 C component-source 完整回归完成 | 逐项复核 C-01～C-17、6.6 与 6.7：受控 loader 严格处理多来源且不削弱 production 20-ID 覆盖；checked-in 单来源 Whisper bytes/SHA 未变。新 fresh-XDG C-15 完成固定下载、离线 health、active/audit，新进程断网 C-16 复验文件/aggregate/supply-chain 与第二次 inference；客户端无需 manifest 文件 | C 组合 `102 passed, 1 skipped in 3.03s`；skip 为需显式 checkpoint 的通用测试，另有本轮真实 C-15/C-16 两次 exit 0；全仓 `466 passed`、Ruff/mypy 223 files、B tool 146、D 63+17、RPM 1 与 diff证据齐备 | C loader/supply-chain、真实 Whisper 输出与完整回归；未提交 | 阶段 C 恢复已完成；A～D 全部完成，E 三项进入条件全部满足，返回 E-PY-04/selection 与生产 bundle |

| 2026-09-05 | 阶段 C 标记需回归；正式 bundle 默认注入缺口 | production bundle 生成后按 6.4 默认服务合同准备 E/8.12 Whisper 安装，代码审计发现 `build_default_service()` 创建 registry/licenses/manager/downloader/health checker，但没有调用 `load_manifest_bundle(resource_path(...))` 或向 `ClassScribeService` 传入 `manifest_bundle`。此前受控注入测试只允许阶段 C 过渡，不能替代正式资源存在后的默认行为；继续手工注入会绕过冻结合同 | `backend/classscribe/api/runtime.py` 只读审计：默认 service 构造没有 `manifest_bundle=`；全仓检索 `load_manifest_bundle` 仅有 loader 定义/导出和测试调用 | production bundle 后的默认 composition 审计；未提交 | 在默认服务启动时 fail-closed 加载 production bundle、revision lock 与 license inventory并注入；补 source resource 默认服务测试和缺件/篡改启动失败测试，随后回归 C/D/A 相关门再继续 E 健康 |

| 2026-09-05 | 默认 bundle 注入行为通过；静态测试门失败 | 增加安全读取同一 resource root revision lock 的 built-in loader，并让默认 service 在任何 XDG 写入前加载/全量验证 production bundle 后注入。source 默认 service、bundle 缺失和篡改 fail-closed 三类行为连同 loader 测试均通过；随后 Ruff 发现新测试导入块空行，mypy 发现测试经 `runtime.resource_path` 访问未显式导出符号，各 1 项，本次静态组合不计全绿 | 定向 pytest `25 passed`；Ruff `I001` 1 项；mypy `attr-defined` 1 项；`git diff --check` 通过 | runtime/loader/exports/tests 与失败输出；未提交 | 仅修正测试导入与 monkeypatch 目标，不改变产品行为；重跑 25 项和静态门，通过前 C/D 保持需回归 |

| 2026-09-05 | C/D 消费者组合失败且默认沙箱退出挂起 | 静态问题修正后定向 25 项/Ruff/mypy 全绿；扩大到 schema、loader、runtime、support、manager、download、health、environment、sandbox、worker transport、API、architecture 的组合时，在 58% 后显示 14 个失败标记，随后连续 90 秒无 traceback/收尾，符合此前默认 sandbox 的 asyncio/thread 退出挂起边界。为避免无限占用发送 Ctrl-C，exit 130；既不计通过，也不在缺少 traceback 时猜测产品根因 | 组合点阵：`... [58%] ...F.FFFFFFFFFFFF...`；三次 30 秒轮询无新输出；Ctrl-C exit 130 | 组合会话与中断记录；未提交 | 按文件拆分，先运行纯 unit/schema 定位失败文件，再在既有允许的 worker/API 执行边界单独运行异步消费者并取得完整 traceback；C/D 保持需回归 |

| 2026-09-05 | 阶段 C 默认 production bundle composition 回归完成 | 默认 service 现在在任何 XDG 写入前，从同一 immutable resource root 安全读取 revision lock、registry、licenses、20-member bundle 与 worker locks，完成全量交叉验证后注入 service；bundle 缺失/篡改直接使启动失败，不静默退回。source 默认 composition 实际加载 20 项并正确披露 pyannote provenance/disabled 状态。拆分回归确认先前组合失败全部来自默认 sandbox 的 socket/subprocess 边界 | source 默认/缺失/篡改定向共 `25 passed`；纯 schema/loader/runtime/manager/support/download/health/environment/sandbox/architecture `96 passed`；允许边界 worker process `14 passed`、API `13 passed`；Ruff、mypy、diff 全绿 | built-in bundle loader、runtime composition、exports/tests 与拆分回归输出；未提交 | 阶段 C 恢复已完成；正式 bundle 已可由默认产品路径使用，恢复 D 后返回 E/8.12 Whisper 真实安装 |

| 2026-09-05 | 阶段 C 标记需回归；production bundle SHA 将变化 | Whisper 自身 manifest/worker/payload 未变，但其 8.12 证据绑定当前 production bundle SHA；FireRed lock 修复会产生新的 bundle bytes，默认 service 启动也将拒绝旧 bundle 与新 registry。按冻结合同撤销旧 bundle 下的 E/Whisper 完成标记，不重用旧 SHA 冒充新 bundle | 当前 Whisper manifest SHA保持 `2d805d29…16eba8`，但 bundle SHA `88a57122…e0816` 将在 FireRed成员变更后失效 | 依赖传播审计与旧真实 Whisper证据；未提交 | 新 bundle 生成/默认加载全绿后，以新 bundle 重做 Whisper bundle/active/supply-chain/离线推理最小复验，再恢复 C/E第1项 |

| 2026-09-05 | 新 bundle 下 Whisper fresh-XDG 安装/离线健康通过；重启待验 | 默认 service 加载新 20-member bundle；普通入口仍因 registry disabled 返回 `MODEL_INSTALL_BLOCKED`，验收路径只从同一只读 bundle 获取 manifest并执行一次性 preflight。固定 revision 四文件下载、实际 SHA/size、全新 frozen moss_en 环境、无网络 Bubblewrap load→transcribe_batch→unload、原子 active 与 supply-chain/aggregate 复验全部成功 | bundle SHA `5cee79a2226e257596f7d66014b4ff3951c1f1f66485c1c08a4890c96e1fc11a`；manifest SHA `2d805d29c6ec3465a0824d2d87c121c3b16514856bdbdaee3d0b90750c16eba8`；revision `d90ca5fe260221311c53c58e660288d3deb8d356`；4 files/78,203,619 bytes；aggregate `2b0d289c…d44b`；健康 WAV 57,983 frames@16k mono SHA `46c25c55…6cc6`；CPU int8、runtime offline、VRAM 0 | 新私有 XDG 安装树、默认 service/downloader/provisioner/Bubblewrap/worker 输出；未提交 | C/8.12第1项尚不恢复；在独立新 Python/core + user/net namespace 中禁止 downloader/provisioning，复验同一 active/files/audit/aggregate 并取得第二次非空推理 |

| 2026-09-05 | 阶段 C 新 bundle 回归完成 | 全新 Python/core 进程在独立 user+network namespace 中确认外网不可达；未调用 downloader，fail-fast provisioning runner 未触发，直接复用 lock-addressed moss_en 环境和同一 active 安装树。重新验证 bundle/manifest、四文件、aggregate、supply-chain 后完成第二次 Bubblewrap load→transcribe_batch→unload，返回非空协议响应 | network probe `OSError`；bundle SHA `5cee79a2…fc11a`；manifest SHA `2d805d29…6eba8`；active revision `d90ca5fe…d356`；aggregate `2b0d289c…d44b`；normalized `Class crime verify real speech recognition.`、1 segment、CPU int8；provisioning runner=false、download=false、verify=true | 新 bundle 的 fresh-XDG 安装树与断网重启进程输出；未提交 | 阶段 C 恢复已完成；A～D 全部完成，恢复 E 8.2 第一项与 8.12 第1项后清理可再生临时树，继续 FireRedVAD/LID |

| 2026-09-05 | synthetic HOME 后 Whisper 通用重启 harness 首次失败；协议详情被裸断言遮蔽 | fresh-XDG 首次安装/离线健康已通过；新通用重启脚本在独立 user/net namespace 中成功 load 后得到 `response.ok=false`，但裸 `assert response.ok` 只留下 AssertionError，没有记录 error code/detail，故不能判断请求构造或产品失败，也不计重启通过。finally 已执行 unload/stop，安装树保留 | restart script exit 1 at `assert response.ok`；无协议错误详情 | 通用临时 harness traceback；未提交 | 改为在失败时输出 `error_code/error_detail`，同一安装树、断网和 fail-fast provisioning 条件重跑；C 保持需回归 |

| 2026-09-05 | synthetic HOME 后 Whisper 重启第二次失败；临时源 WAV 权限不符合协议 | 增强诊断后确认 worker load 成功，inference 被协议前置校验拒绝为 `batch audio must be read-only`。首次安装使用 recording store 的只读副本所以已通过；通用重启 harness 却直接绑定保留的可写源 WAV。finally 再次完成 unload/stop，未发生模型或环境变化 | restart exit 1；error `invalid_request: batch audio must be read-only` | 协议详情与临时文件权限审计；未提交 | 将临时健康 WAV 权限收紧为 0400并在 harness 启动时断言，再以完全相同断网/无 provisioning 条件重跑；C 保持需回归 |

| 2026-09-05 | 阶段 C synthetic HOME 回归完成 | 使用新私有 XDG 根在 synthetic HOME WorkerSandbox 下重新完成默认 bundle→disabled 阻止→验收 preflight→固定下载→frozen provisioning→离线 health→active/audit；随后独立新 core + user/net namespace 以 0400 健康 WAV、禁止 downloader/provisioning，再次完成 load→transcribe_batch→unload。前两次通用 harness 失败分别保留为诊断缺口与 WAV 权限前置证据 | bundle `5cee79a2…fc11a`；manifest `2d805d29…6eba8`；revision `d90ca5fe…d356`；aggregate `2b0d289c…d44b`；WAV SHA `bbeaef1d…9939f`/91,939 frames；重启 network `OSError`、runner=false、normalized `Classcribe verify real speech recognition and language identification.`、1 segment、CPU int8/VRAM 0 | synthetic HOME 新安装树、第三次重启输出与 A 全仓门；未提交 | 阶段 C 恢复已完成；A～D 全部完成，恢复 E 8.2 第一项与 8.12 第1项后清理 Whisper 临时树，继续 VAD第四次尝试 |

| 2026-09-05 | 阶段 C 最终 synthetic identity 回归完成 | 在最终 `HOME=/home/classscribe`、`USER=LOGNAME=classscribe` 命令下再次创建 fresh-XDG；默认20项bundle、disabled普通入口、验收preflight、固定四文件下载、frozen环境、离线health、active/audit全部通过。独立新core+user/net namespace随后禁止下载/provisioning，复验同一安装树并再次得到非空ASR响应 | bundle `5cee79a2…fc11a`；manifest `2d805d29…6eba8`；aggregate `2b0d289c…d44b`；WAV `bbeaef1d…9939f`/91,939 frames；重启 network `OSError`、runner=false、normalized `Classcribe verify real speech recognition and language identification.`、1 segment、CPU int8/VRAM 0 | 最终sandbox的fresh安装/重启输出与A全仓门；未提交 | 阶段C恢复已完成；A～D全部完成，恢复E入口/Whisper后清理临时树并继续VAD第五次完整链 |

| 2026-09-05 | 新 Qwen bundle 下 Whisper fresh-XDG 安装与离线健康通过；重启待验 | 默认 service 加载重新生成的20项production bundle；disabled普通入口仍以`MODEL_INSTALL_BLOCKED`拒绝，验收路径仅从同一只读bundle取manifest。全新私有XDG完成固定四文件下载、实际SHA/size、frozen moss_en环境、最终synthetic identity下的无网络Bubblewrap load→transcribe_batch→unload、原子active与supply-chain复验 | bundle `08a44567…9b3`/20；manifest `2d805d29…6eba8`；revision `d90ca5fe…d356`；4 files/78,203,619 bytes；aggregate `2b0d289c…d44b`；健康WAV `bbeaef1d…9939f`/91,939 frames@16k mono；health=true/backend `faster_whisper_cpu_int8`/runtime_offline=true/VRAM 0；preflight required 173,184,454、available 333,618,462,720 bytes | 新0700 XDG安装树、默认service/downloader/provisioner/Bubblewrap/worker输出；未提交 | C尚不恢复：立即在独立新core及user+network namespace中禁止下载/provisioning，复验同一active/files/aggregate/audit并取得第二次非空协议推理 |

| 2026-09-05 | 阶段 C 新 Qwen bundle 回归完成 | 独立新 Python/core 进程在新的 user+network namespace 中确认外网不可达；不调用 downloader，fail-fast provisioning runner 未触发，直接复用 lock-addressed moss_en 环境与同一 active。重新验证20项bundle、manifest、四文件、aggregate和supply-chain后完成第二次Bubblewrap load→transcribe_batch→unload并返回有意义的协议响应；客户端未提供manifest文件 | bundle `08a44567…9b3`/20；manifest `2d805d29…6eba8`；active revision `d90ca5fe…d356`；aggregate `2b0d289c…d44b`；network `OSError`、download=false、runner=false、verify=true；normalized `Classcribe verify real speech recognition and language identification.`、1 segment、CPU int8/VRAM 0；同轮全仓 `471 passed, 1 skipped, 1 deselected`及RPM/静态门全绿 | 新bundle的fresh-XDG安装树、独立断网重启输出与阶段A全门；未提交 | C-01～C-17、6.6与6.7当前闭合，阶段C恢复已完成；按依赖顺序进入D的API/status/WebUI完整回归 |
| 2026-09-05 | Granite新bundle C回归准备；临时harness Ruff失败且组合exit被覆盖 | 两个验收harness已更新为新bundle SHA并创建新的0700私有XDG根；健康WAV仍为0400且SHA不变。下载前的Ruff对两个临时脚本各报1个`I001`导入块顺序错误；同一顺序组合随后执行的`sha256sum`成功使整条shell最终exit为0，因此组合exit不能用作静态通过证据，尚未执行任何模型下载或安装 | Ruff：2个`I001`；XDG根及5个子目录均0700；WAV `bbeaef1d…9939f`、183,956 bytes、mode 0400；组合最终exit 0不代表Ruff成功 | 新私有XDG空根、临时harness与静态失败输出；未提交 | 先查看Ruff只读修复diff并用patch纠正导入顺序，单独Ruff通过后才运行新bundle Whisper fresh-XDG安装；C保持需回归 |
| 2026-09-05 | Granite新bundle下Whisper fresh-XDG安装与离线健康通过；重启待验 | 两个临时harness移除多余空行后Ruff单独通过；默认service加载新20项production bundle，disabled普通入口仍以`MODEL_INSTALL_BLOCKED`拒绝，验收路径仅从同一只读bundle取manifest。全新0700私有XDG完成固定四文件下载、实际SHA/size、fresh frozen moss_en环境、最终synthetic identity下无网络Bubblewrap load→transcribe_batch→unload、原子active及supply-chain复验 | bundle `45fd541d…e0b6`/20；manifest `2d805d29…6eba8`；revision `d90ca5fe…d356`；4 files/78,203,619 bytes；aggregate `2b0d289c…d44b`；WAV `bbeaef1d…9939f`/91,939 frames@16k mono；health=true/backend `faster_whisper_cpu_int8`/runtime_offline=true/VRAM 0；preflight required 173,184,454、available 327,121,055,744 bytes | 新私有XDG active/supply-chain、默认service/downloader/provisioner/Bubblewrap/worker输出；未提交 | C尚不恢复：立即在独立新core及user+network namespace中禁止downloader/provisioning，复验同一active/files/aggregate/audit并取得第二次非空协议推理 |
| 2026-09-05 | 阶段C Granite新bundle回归完成 | 独立新Python/core进程在新的user+network namespace中确认外网不可达；不调用downloader，fail-fast provisioning runner未触发，直接复用lock-addressed moss_en环境与同一active。重新验证20项bundle、manifest、四文件、aggregate和supply-chain后完成第二次Bubblewrap load→transcribe_batch→unload并返回有意义的非空响应；客户端未提供manifest文件 | bundle `45fd541d…e0b6`/20；manifest `2d805d29…6eba8`；active revision `d90ca5fe…d356`；aggregate `2b0d289c…d44b`；network `OSError`、download=false、runner=false、verify=true；normalized `Classcribe verify real speech recognition and language identification.`、1 segment、CPU int8/VRAM 0；同轮全仓`472 passed, 1 skipped, 1 deselected`及RPM/静态门全绿 | 新bundle的fresh-XDG安装树、独立断网重启输出与阶段A全门；未提交 | C-01～C-17、6.6与6.7当前闭合，阶段C恢复已完成；按依赖顺序进入D的API/status/WebUI完整回归 |
| 2026-09-06 | 阶段 C Nemotron源码修复后独立断网回归闭合 | 在24G RAM/2G swap scope中启动独立新Python/core及user+network namespace，确认外网不可达；只读复用保留的Whisper active与同源legacy lock-only MOSS环境，没有下载或重新provision，真实完成load→transcribe_batch→unload | restart exit 0、network_blocked=true；bundle `45fd541d…e0b6`；health=true/backend `faster_whisper_cpu_int8`/runtime_offline=true/VRAM 0；active revision `d90ca5fe…d356` | 保留的Granite-bundle Whisper安装树、独立断网重启输出；未提交 | 阶段 C 恢复已完成；A～C已完成，严格进入D的66项后端与Node24前端五门回归 |
| 2026-09-06 | 阶段 C 新 Nemotron production bundle 独立断网回归闭合 | 在24G/2G scope中启动全新 Python/core 与独立 user+network namespace，确认外网不可达；默认 service 验证新20项bundle，manager只读复验保留的active、四文件和supply-chain，provisioner仅resolve同源legacy lock-only MOSS环境。真实Bubblewrap完成load→transcribe_batch→unload，无下载、无环境重建 | restart exit 0、network_blocked=true；bundle `55069eea…1357`；manifest `2d805d29…6eba8`；active revision `d90ca5fe…d356`；health=true/backend `faster_whisper_cpu_int8`/runtime_offline=true/CPU int8/VRAM 0；scope `Result=success`、staging为空、无残留worker | 保留Whisper安装树、独立断网新core与默认bundle/worker输出；未提交 | C-01～C-17、6.6与6.7当前闭合，阶段 C 恢复已完成；严格进入阶段 D 的后端与 Node 24 前端五门回归 |

## 7. 阶段 D：API 与 WebUI

状态：**已完成**

### 7.1 阶段目标

把普通安装入口从“客户端上传任意 manifest”改为“客户端只提交 model ID，服务从只读 bundle 取
manifest”，并让模型列表和 WebUI 明确区分 manifest、worker、安装策略与 enabled 状态。

### 7.2 进入条件

- [x] 阶段 C 的 loader 与一模型完整安装链已验证。
- [x] 普通安装入口的安全边界已冻结；BYOM 不混入本轮普通接口。

### 7.3 API 合同

- [x] `D-01` 将普通安装预检接口固定为：

```http
POST /api/v1/models/{model_id}/install
Content-Type: application/json

{}
```

- [x] `D-02` 服务只依据 model ID 从只读 bundle 取 manifest，再执行现有 request/confirm 两阶段流程。
- [x] `D-03` 普通接口忽略或明确拒绝客户端提交的任意 manifest；测试证明用户不能替换发布 manifest。
- [x] `D-04` 预检响应继续包含 manifest SHA、下载量、安装量、临时空间、许可证、remote-code 变更
  摘要和环境披露。
- [x] `D-05` 保持确认接口的一次性 token、健康录音、语言、ForcedAligner 精确文本和 gated 条款确认。
- [x] `D-06` 建议新增以下只读接口：

```http
GET /api/v1/models/{model_id}/manifest
```

该接口只返回公开 manifest 及其 SHA，不得返回确认 token、HF token 或本机路径。

若未来支持 BYOM，应使用独立专家接口和独立策略，例如：

```http
POST /api/v1/expert/models/{model_id}/install-custom
```

本轮不得实现或继续保留普通安装入口接受任意 manifest 的行为。

### 7.4 模型列表与安装能力状态

- [x] `D-07` 为 `GET /models` 增加 `manifest_available`。
- [x] `D-08` 增加 `manifest_sha256`。
- [x] `D-09` 增加 `estimated_download_bytes` 和 `installed_size_bytes`。
- [x] `D-10` 增加 `worker_implemented`。
- [x] `D-11` 增加 `installable` 和 `install_block_reason`。
- [x] `D-12` verifier、服务和 UI 明确区分 `manifest_available`、`worker_implemented`、
  `installable`、`enabled`，禁止用一个布尔值混淆四种状态。

`installable` 至少要求：manifest 完整、worker 支持该模型 ID、依赖 lock 存在、registry policy 允许。
`enabled` 继续只表示自动候选资格，不能与 `installable` 互相替代。

### 7.5 WebUI 模型页

修改 `frontend/src/pages/ModelsPage.tsx`：

- [x] `D-13` 删除 manifest 文件选择器和浏览器端 `JSON.parse`。
- [x] `D-14` “准备安装”直接 POST model ID。
- [x] `D-15` 显示 manifest SHA、下载大小、安装大小、remote-code 文件数和安装阻塞原因。
- [x] `D-16` `installable=false` 时禁用按钮并展示具体原因。
- [x] `D-17` `enabled=false` 但可供专家安装的模型必须先经过单独的专家启用流程。
- [x] `D-18` confirm 阶段保留一次性 token、健康录音、语言、ForcedAligner 精确文本和 gated 条款确认。
- [x] `D-19` 安装错误不得显示 HF token、Authorization header 或完整本机路径。

### 7.6 健康录音入口

- [x] `D-20` 模型页允许上传或选择已有合规短 WAV，避免用户从其他页面手工抄 UUID。
- [x] `D-21` 上传后先验证非空、16 kHz、mono、16-bit PCM、最长 15 秒，再允许用于确认。
- [x] `D-22` 保存/回填合规健康 WAV 的 UUID，同时不泄漏本机绝对路径。

### 7.7 测试要求

- [x] API 测试证明客户端 manifest 被忽略/拒绝，安装实际只使用 bundle 内容。
- [x] disabled、未实现、无 manifest、可安装和 enabled 状态组合得到正确字段与阻塞原因。
- [x] 前端不再出现 manifest 文件 input。
- [x] installable 模型可发起预检。
- [x] disabled/未实现模型显示阻塞原因且不能安装。
- [x] gated 条款未接受时不能 confirm。
- [x] ForcedAligner 缺精确文本时不能 confirm。
- [x] 健康 WAV 上传、校验、选择与 UUID 回填完整。
- [x] 下载量、空间、manifest SHA 和 remote-code 披露正确。
- [x] 安装错误脱敏测试覆盖 HF token、Authorization header 和本机路径。

### 7.8 退出条件

浏览器端不能替换发布 manifest；普通用户可以通过 model ID 完成受支持模型的预检与安装；不支持模型在
下载前得到稳定、具体且不泄密的阻塞说明。

### 7.9 进度记录

| 日期 | 状态/任务 ID | 变更或结论 | 验证命令与结果 | 提交/证据 | 阻塞与下一步 |
|---|---|---|---|---|---|
| 2026-09-04 | 未开始 | 当前 API/WebUI 仍由客户端提交 manifest | 未运行实施验证 | 本文件 | 阶段 C 完成后开始 `D-01` |
| 2026-09-05 | 阶段 D 进入条件完成；开始 `D-01` | 阶段 C loader、一模型 bundle 注入完整事务、真实 Whisper 安装与离线重启均已验证；本阶段普通入口明确只接受 model ID/空对象，客户端 manifest 不作为 BYOM 继续保留，未来 BYOM 只能走独立专家策略 | 阶段 C 退出回归：core `120 passed`、manifest-tool `117 passed` 及静态门全通过 | 阶段 C 完整证据与阶段 D 冻结接口合同；未提交 | 将 `ModelInstallRequest` 固定为空且 extra-forbid，并让路由能以 `{}` 发起预检；随后按序验证 D-02/D-03 |
| 2026-09-05 | 完成 `D-01`；阶段进行中 | `ModelInstallRequest` 现在是继承 `APIModel(extra=forbid)` 的空对象，公开 `POST /models/{model_id}/install` 接受 `{}`；route 已切到既有 bundle-only service 入口以使空请求可执行。此实现同时为 D-02 提供代码基础，但尚未按 D-02/D-03 独立复验或标记 | 真实 ASGI 空对象请求返回 202：定向 `1 passed in 0.44s`；ruff、严格 mypy（schemas/routes）及 `git diff --check` 通过 | API schema、route 与 integration test；未提交 | 下一步重新读取并执行 D-02，证明 service 只按 model ID 查询只读 bundle；阶段 A API 回归仍待 D-03 后统一关闭 |
| 2026-09-05 | 完成 `D-02`；阶段进行中 | 公开 route 仅把 path `model_id` 传给 `request_bundled_model_install`；service 只从注入的 `LoadedManifestBundle.manifest(model_id)` 取得 manifest，再复用 manager 的 request/confirm 事务。删除无调用方的公开 `request_model_install(model_id, manifest)` 旁路，只保留内部接收已验证 bundle 对象的共享 helper | HTTP 空对象 bundle preflight 与 bundle 完整安装事务 `2 passed in 0.48s`；ruff、严格 mypy（service/routes）、代码引用审计及 `git diff --check` 通过 | service/route 与 bundle integration tests；未提交 | 下一步重新读取并执行 D-03，增加 HTTP 级客户端 manifest 拒绝与不可替换 bundle 的证明；阶段 A API 回归仍待关闭 |
| 2026-09-05 | 完成 `D-03`；阶段进行中 | 普通请求 schema 对 `manifest` 及任何额外字段明确返回 422；攻击测试提交替换 repository/revision 的完整 manifest 被拒，随后同 model ID 的 `{}` 预检仍返回 bundle 中的固定 revision 与 SHA。原二阶段 API 集成测试也已改用只读 bundle，不再保留普通 BYOM 行为 | D-03 定向 `3 passed in 0.62s`；完整受影响 API/manager/architecture 回归 `39 passed in 4.25s`；ruff、严格 mypy与 `git diff --check` 通过 | API schema/route/service 与 integration tests；未提交 | D-03 完成且阶段 A 回归关闭；下一步重新读取并执行 D-04，复验全部预检披露字段 |
| 2026-09-05 | 完成 `D-04`；阶段进行中 | HTTP 级测试同时覆盖无 remote code 的真实 Whisper manifest 与含 remote code 的受控 MOSS manifest；响应逐字段核对 bundle manifest SHA、estimated download、installed size、required/available 临时空间、license ID/URL/terms、完整 environment，以及 remote-code 文件计数与原始路径摘要 | 两类 bundle HTTP preflight `2 passed in 0.56s`；ruff、严格 mypy（API test/service）及 `git diff --check` 通过 | 既有 service disclosure 与扩充的 API assertions；未提交 | 下一步重新读取并执行 D-05，验证确认 token、健康录音/语言、ForcedAligner 精确文本及 gated 条款合同 |
| 2026-09-05 | 完成 `D-05`；阶段进行中 | confirm service 在消费 token 或调用 downloader 前要求 alignment task 提供非空精确文本；同一 token 可在补齐文本后继续使用。受控 ForcedAligner 测试确认语言/精确文本传入 health callback；pyannote gated 测试确认未接受条款时 downloader 为 0，重新预检并明确接受后才安装。既有一次性 token、模型绑定、录音与普通 ASR 确认合同保持 | D-05 定向 `5 passed in 0.65s`；完整 API/manager/health 回归 `44 passed in 1.32s`；ruff、严格 mypy（9 source files）及 `git diff --check` 通过 | service confirm 前置校验与 integration/manager/health tests；未提交 | D-05 完成且阶段 A 保持已完成；下一步重新读取 D-06，新增只读公开 manifest 接口并验证无 token/本机路径泄漏 |
| 2026-09-05 | 完成 `D-06`；阶段进行中 | 新增只读 `GET /models/{model_id}/manifest`，并让读取与安装预检共用 `_bundled_manifest`，保证 model ID、成员存在性及 SHA 选择语义一致。响应顶层严格为 model ID、索引 SHA 与公开 manifest；测试精确比对响应且排除 confirmation token、`HF_TOKEN` 与临时本机路径 | 只读 endpoint 与 bundle 安装事务定向 `2 passed in 0.52s`；ruff、严格 mypy（service/routes/test）及 `git diff --check` 通过 | API service/route/integration test；未提交 | 下一步重新读取并执行 D-07，为模型列表增加独立 `manifest_available` 状态 |
| 2026-09-05 | 完成 `D-07`；阶段进行中 | `GET /models` 每项新增独立 `manifest_available`，仅当注入 bundle 同时可查询 manifest 与索引 SHA 时为 true；无 bundle 或任一成员缺失均为 false，不参考 enabled/experimental/安装数据库。受控 bundle 中 Whisper 为 true，同为 enabled 的 MOSS 因无 bundle 成员为 false | HTTP 模型列表定向 `1 passed in 0.48s`；ruff、严格 mypy及 `git diff --check` 通过 | service list 状态与 integration assertion；未提交 | 下一步重新读取并执行 D-08，增加可空 `manifest_sha256` 且只在 manifest_available 时返回索引值 |
| 2026-09-05 | 完成 `D-08`；阶段进行中 | `GET /models` 新增 `manifest_sha256`；service 每个模型只查询一次 bundle 的 manifest+index SHA 元组，完整可用时返回索引 SHA，任一缺失时与 `manifest_available=false` 同时返回 null。Whisper 值与实际 checked-in manifest 字节 SHA 精确相等，无成员 MOSS 为 null | HTTP 模型列表定向 `1 passed in 0.47s`；ruff、严格 mypy及 `git diff --check` 通过 | service manifest metadata helper 与 integration assertion；未提交 | 下一步重新读取并执行 D-09，让两类 size 只来自 bundle manifest，不沿用占位值 |
| 2026-09-05 | 完成 `D-09`；阶段进行中 | `GET /models` 的 `estimated_download_bytes` 与 `installed_size_bytes` 现均从同一次 bundle manifest 查询取得；没有 manifest 时两者明确为 null。移除原本无条件为 null 的安装大小占位，保持 VRAM 估算为独立字段 | HTTP 模型列表定向 `1 passed in 0.48s`；ruff、严格 mypy及 `git diff --check` 通过 | service list payload 与 integration assertions；未提交 | 下一步重新读取并执行 D-10，以 worker 实际支持模型 ID 与安全 source/lock/entrypoint 为依据增加 `worker_implemented` |
| 2026-09-05 | 完成 `D-10`；阶段进行中 | 新增审计型 model-ID→worker 支持矩阵，列出 16 个现有真实 adapter 支持项；FireRed LLM、Granite TurboCTC、Voxtral、VibeVoice 四个仅有缺失/边界实现的模型明确为 false。`worker_implemented` 同时要求映射与 registry worker 一致，且 adapter/healthcheck/pyproject/lock/entrypoint 全为非 symlink 普通文件；因此仅有 worker 目录不算实现 | 支持矩阵、安全缺件/symlink 与 HTTP 列表 `3 passed in 0.57s`；ruff、严格 mypy（4 source files）及 `git diff --check` 通过 | `models/support.py`、unit/integration tests；未提交 | 下一步重新读取并执行 D-11，组合 manifest、worker、lock 与 registry policy 给出 installable/稳定阻塞原因 |
| 2026-09-05 | 完成 `D-11`；阶段进行中 | 新增共享 `model_installability`：普通安装依次要求 registry enabled 且非 experimental、真实 worker 支持、安全完整 bundle manifest、非 symlink dependency lock 且实际 SHA 与 registry 一致；返回 `installable` 与稳定 reason code。列表与预检共用此判定，预检失败返回新 `MODEL_INSTALL_BLOCKED` 且不创建 token/调用 downloader。Whisper 因 policy disabled、MOSS 因 manifest missing、enabled TurboCTC 因 worker 未实现分别得到不同原因；受控 enabled MOSS 完整条件为 true 并可安装 | 首轮 6 项测试通过但 ruff 发现 test import 排序错误，未计完成；修正后 D-11 组合 `6 passed in 0.69s`，ruff、严格 mypy（5 source files）及 `git diff --check` 全通过 | errors/support/service 与 unit/integration tests；未提交 | 下一步重新读取并执行 D-12，把四个状态同步到 verifier 与 UI 类型/呈现并验证互不混淆 |
| 2026-09-05 | `D-12` 进行中；首轮门禁失败 | 新增共享 model→worker 支持清单，core/verifier 均从该清单判定 worker 实现；verifier verify/status 与 UI 已分别呈现四状态，但尚未完成验证 | 首轮：Ruff 通过；工具定向 `1 failed, 2 passed`，失败因 support 漂移抛出裸 `ValueError` 而非稳定 `BundleVerificationError`；后端命令因写错 pytest 节点名未收集（exit 4）；前端 `pnpm` 命令不存在（exit 127） | D-12 工作树与失败输出；未提交 | 统一 verifier 错误边界，查明正确后端节点与仓库可用前端执行入口后重跑；未通过前不勾选 D-12 |
| 2026-09-05 | `D-12` 进行中；功能定向通过、类型检查入口失败 | verifier 已把共享支持合同错误归一为稳定 `BundleVerificationError`；改用仓库实际可用的 npm 脚本，四状态前端测试通过 | 后端 `4 passed`；verifier `3 passed`；前端 `7 passed`；TypeScript typecheck 通过；一次从根 uv 环境跨工程运行 mypy 因看不到工具专属 httpx/HF/jsonschema 依赖而报 9 个 import 错误，不计通过 | D-12 定向输出；未提交 | 分别从根工程和 manifest-tool frozen 工程运行严格 mypy，并继续完整前端与 Stage B 全量回归 |
| 2026-09-05 | `D-12` 进行中；静态门部分通过 | core 严格 mypy、独立 manifest-tool 严格 mypy、Ruff 和 ESLint 均通过；共享合同及两套读取实现没有类型/导入边界错误 | core mypy `1 source file`、tool mypy `11 source files`、Ruff、ESLint 通过；Prettier 检查仅报告新增 `frontend/tests/App.test.tsx` 格式不符（exit 1） | 静态检查输出；未提交 | 仅格式化该测试文件并重跑完整前端 check；随后执行 Stage B 全量回归 |
| 2026-09-05 | `D-12` 进行中；Stage B 功能回归通过 | 格式化新增前端测试；独立工具的 verifier/status、生成器、攻击矩阵与本地假 Hub 全量回归通过，受影响 API/support 回归也通过 | manifest-tool `118 passed in 3.90s`；API/support `13 passed in 1.27s`；tool lock check 与共享 JSON 解析通过；前端聚合 `npm run check` 因其内部硬编码调用环境中不存在的 `pnpm` 而 exit 127，未计通过 | 全量功能回归输出；未提交 | 用 npm 分别执行聚合脚本列出的五项门禁；补跑 architecture、offline/frozen 与 diff 后再决定 D-12/Stage B 状态 |
| 2026-09-05 | 完成 `D-12`；阶段进行中 | 新增共享、排序且严格读取的 model→worker 支持清单；core 与离线工具均要求映射匹配及完整非 symlink worker source。verifier verify/status、服务列表和 UI 模型卡分别输出/显示 `manifest_available`、`worker_implemented`、`installable`、`enabled`，并保留稳定阻塞原因；实测 enabled=true 可同时 manifest=false、worker=false 或 installable=false，disabled Whisper 仍可 worker=true，证明四者未混用 | 后端定向及受影响全量分别 `4 passed`、`13 passed`；manifest-tool 定向 `3 passed`、全量 `118 passed`；前端 lint/format/typecheck/test `7 passed`/build 全通过；core/tool 严格 mypy、Ruff、architecture、offline/frozen、tool lock、JSON 解析及 `git diff --check` 全通过。聚合 npm check 因脚本内部缺 `pnpm` 失败，已由同一五项 npm 门禁逐项通过替代并保留失败记录 | shared support contract、core/tool readers、API/UI types and presentation、tests；未提交 | D-12 完成且 Stage B 回归关闭；下一步重新读取并执行 D-13，删除 manifest 文件选择器和浏览器端 JSON.parse |
| 2026-09-05 | 完成 `D-13`；阶段进行中 | 删除普通模型页的 manifest upload mutation、文件读取函数、JSON 解析、file input 与 file-button；保留只读 bundle 状态和确认流程，其后的 model-ID 预检按钮按 D-14 单独接入 | 前端 test `7 passed`，新增 DOM 断言确认无 file input；lint、Prettier、typecheck 与 `git diff --check` 通过；源码禁用项检索无匹配（`rg` exit 1，符合预期） | `ModelsPage.tsx` 与 App test；未提交 | 下一步重新读取并执行 D-14，让“准备安装”按钮只向固定 model-ID endpoint 发送空对象 |
| 2026-09-05 | `D-14` 进行中；行为通过、格式门失败 | “准备安装”已按 model ID 调用固定 endpoint 且请求体仅为 `{}`；测试捕获实际 fetch 请求并确认没有客户端 manifest | 前端 test `8 passed`、ESLint、typecheck 与 diff 通过；Prettier 仅报告新增 App test 排版不符（exit 1） | D-14 frontend implementation/test output；未提交 | 格式化单个测试文件并重跑门禁，通过前不勾选 D-14 |
| 2026-09-05 | 完成 `D-14`；阶段进行中 | 模型页恢复“准备安装”标准按钮，mutation 参数只有 model ID，固定 POST `/models/{id}/install` 且 body 精确为 `{}`；成功后继续展示服务返回的二阶段确认计划，未恢复文件读取或 manifest 字段 | 前端 test `8 passed`，请求捕获断言 endpoint/method/body；ESLint、typecheck、Prettier、production build 与 `git diff --check` 通过；源码检索只显示 verify/install 的空对象请求 | `ModelsPage.tsx` 与 App test；未提交 | 下一步重新读取并执行 D-15，补齐 manifest SHA、两类 size、remote-code 数量和阻塞原因呈现 |
| 2026-09-05 | `D-15` 进行中；功能通过、格式门失败 | 服务列表从同一只读 manifest 计算 remote-code 文件数；UI 已显示完整 manifest SHA、预计下载、安装大小、remote-code 数量和稳定阻塞 reason code，并把已安装 artifact SHA 单独命名 | API 定向 `2 passed`；前端 test `8 passed`、ESLint、typecheck、Python Ruff 通过；Prettier 仅报告 App test 新断言排版不符（exit 1） | D-15 service/type/UI/test output；未提交 | 格式化单个测试文件并重跑受影响 API、前端门禁和 diff；通过前不勾选 D-15 |
| 2026-09-05 | 完成 `D-15`；阶段进行中 | `GET /models` 新增可空 remote-code 文件数并只由 bundle manifest 的显式 kind 计算；模型卡显示完整 manifest SHA、两类字节大小、remote-code 数量及 install block reason，缺 manifest 时均保持明确空值，不借用安装 artifact SHA | 受影响 API/support `13 passed in 1.29s`；前端 test `8 passed`、Prettier 与 production build 通过；service 严格 mypy、既有 ESLint/typecheck/Python Ruff 及 `git diff --check` 通过 | service payload、ModelInfo、ModelsPage 与 tests；未提交 | 下一步重新读取并执行 D-16，在 installable=false 时禁用预检按钮并明确展示具体原因 |
| 2026-09-05 | `D-16` 进行中；行为通过、格式门失败 | 预检按钮现由 `installable` 独立控制，阻塞时通过 aria 关联中文具体说明与稳定 reason code；enabled 不参与替代判断 | 前端 test `8 passed`、ESLint、typecheck 与 diff 通过；Prettier 仅报告 App test 新断言排版不符（exit 1） | D-16 UI/test output；未提交 | 格式化单个测试文件并重跑门禁，通过前不勾选 D-16 |
| 2026-09-05 | 完成 `D-16`；阶段进行中 | `installable=false` 时“准备安装”禁用；按钮以 `aria-describedby` 指向该模型的具体中文阻塞说明并同时显示稳定 reason code。测试 fixture 刻意为 enabled=true、manifest=true、worker=false，确认 enabled 不会错误放行 | 前端 test `8 passed`；ESLint、typecheck、Prettier、production build 与 `git diff --check` 通过 | ModelsPage installability control and App test；未提交 | 下一步重新读取并执行 D-17，审计 disabled 模型的专家启用边界，禁止普通入口暗中启用 |
| 2026-09-05 | 完成 `D-17`；阶段进行中 | 对 enabled=false 且 manifest/worker 均具备的潜在专家候选，UI 明确提示必须先走独立专家启用流程，普通按钮保持禁用并同时关联 policy blocker。依 7.3，本轮不虚构尚未定义权限模型的专家/BYOM endpoint；后端普通预检继续以 `registry_policy_disabled` 在下载前 fail closed | 前端 test `8 passed`，disabled+manifest+worker fixture 的提示与禁用按钮通过；API disabled preflight `1 passed in 0.44s`；ESLint、Prettier、typecheck 与 `git diff --check` 通过 | ModelsPage expert boundary、App/API existing tests；未提交 | 下一步重新读取并执行 D-18，复验 WebUI confirm 是否完整保留 token、录音、语言、aligner 文本与 gated 条款 |
| 2026-09-05 | `D-18` 进行中；行为通过、lint 失败 | 浏览器测试已完成预检→填写录音/语言/精确文本/条款→confirm 并捕获请求；后端既有一次性 token、aligner 与 gated 门保持通过 | 前端 test `8 passed`、Prettier/typecheck/diff 通过；后端 confirm 定向 `4 passed`；ESLint 因测试对泛型 BodyInit 使用默认字符串化报 `no-base-to-string`（exit 1） | D-18 browser/API/manager test output；未提交 | 测试先断言 body 为 string 再 JSON 解析，重跑全部前端门禁；通过前不勾选 D-18 |
| 2026-09-05 | 完成 `D-18`；阶段进行中 | 浏览器从预检响应保留一次性 confirmation token，并在 confirm JSON 中精确发送健康录音 UUID、手选语言、ForcedAligner 精确文本与 gated terms=true；条款未勾选或未选录音时按钮保持禁用。测试严格要求请求 body 本身是 JSON string | 前端 test `8 passed`；ESLint、Prettier、typecheck、production build 与 `git diff --check` 通过；后端 aligner/gated/single-use/model-binding 定向 `4 passed` | ModelsPage confirm flow、App/API/manager tests；未提交 | 下一步重新读取并执行 D-19，建立安装错误在 UI/API 边界的 token、Authorization 与本机路径脱敏测试 |
| 2026-09-05 | `D-19` 进行中；前端通过、后端测试构造失败 | 后端 API 序列化与浏览器 API client 均增加 token/header/绝对路径脱敏；前端错误测试通过 | 前端 test `9 passed`、ESLint、Prettier、typecheck 通过；Python Ruff/严格 mypy 通过；后端定向 `1 failed, 1 passed`，失败为新测试向 ASGI helper 传字符串 body，路由前即 400 parsing error，未验证异常处理器 | D-19 redaction implementation/test output；未提交 | 按 request helper 既有字节 body 合同修正测试并重跑；通过前不勾选 D-19 |
| 2026-09-05 | 完成 `D-19`；阶段进行中 | 所有 `ClassScribeError` 在 API 序列化前移除 Authorization/Bearer、HF token 和 POSIX/Windows 完整本机路径；浏览器 client 在显示服务 detail 前再次执行同类脱敏，install mutation 的错误只显示处理后消息。安全的既有 blocker detail 保持不变 | 修正测试 body 为 bytes 后 API redaction/preflight `2 passed`；既有 observability privacy `3 passed`；前端 test `9 passed`、lint/Prettier/typecheck/production build 通过；Python Ruff、严格 mypy 与 `git diff --check` 通过 | errors/app/api client/ModelsPage and backend/frontend tests；未提交 | 下一步重新读取并执行 D-20，为模型页提供健康 WAV 上传或已有录音选择，移除手抄 UUID 依赖 |
| 2026-09-05 | 完成 `D-20`；阶段进行中 | 新增认证的 `GET /recordings`，按新到旧返回与单项接口相同的公开元数据且不含 source path；模型确认区以选择框列出现有录音，并提供仅接受 WAV 的上传入口，上传成功会刷新清单。移除手工 UUID 文本框；严格 WAV 字节合同按下一项 D-21 完成 | recordings collection API `1 passed`；前端 test `9 passed`，现有录音 option 可选并驱动既有 confirm；Python Ruff/严格 mypy、前端 lint/Prettier/typecheck 通过 | routes/service、ModelsPage 与 API/App tests；未提交 | 下一步重新读取并执行 D-21，在上传前按 WAV header 严格验证非空、16 kHz、mono、16-bit PCM 和 15 秒上限，并过滤现有清单 |
| 2026-09-05 | `D-21` 进行中；行为通过、静态门失败 | 浏览器严格解析实际 RIFF/WAVE fmt/data bytes；服务端独立解析实际上传字节并记录 eligibility，confirm 在 token 消费/下载前拒绝不合规录音；已有清单只显示服务端标记合规且 metadata 满足限制的项目 | 服务端定向 `2 passed`；前端 WAV 矩阵 8 项、全前端 `17 passed`；mypy、Prettier、typecheck 通过；Ruff 因一条错误文本 104 列失败，ESLint 因测试对 ASCII FourCC 使用 string spread 触发 Unicode 安全规则失败 | D-21 implementation/test outputs；未提交 | 折行 Python 文本并改用索引写入 ASCII FourCC，重跑全门禁；通过前不勾选 D-21 |
| 2026-09-05 | 完成 `D-21`；阶段进行中 | 浏览器直接解析 RIFF/WAVE chunk 和 fmt/data 实际 bytes，要求 PCM format=1、16 kHz、mono、16-bit、block/byte rate 一致、非空帧且最多 240000 samples；服务端用 Python wave 独立复核实际 bytes、声明 metadata 与压缩类型并记录 eligibility。confirm 在 token 消费/下载前拒绝不合规录音且同 token 可改用有效录音 | 前端 WAV 8 项矩阵及全前端 `17 passed`；API/manager 受影响全量 `43 passed in 1.46s`；Ruff、严格 mypy、ESLint、Prettier、typecheck、production build 与 `git diff --check` 通过 | `healthWav.ts`、service QC/confirm gate、frontend/backend tests；未提交 | 下一步重新读取并执行 D-22，验证上传响应 UUID 自动保存/回填且公开录音元数据和错误均不泄漏本机路径 |

| 2026-09-05 | `D-22` 进行中；首轮复验命令环境失败 | 上传成功后已把服务端响应的录音对象写入 recordings query cache 并立即选择其 UUID；浏览器测试已加入真实合规 WAV 上传、UUID 回填及无本机路径断言，但尚未取得有效门禁结论 | 前端四项命令均因误在仓库根目录执行、缺少根 `package.json` 而 exit 254；后端定向命令因受限环境无法写 `~/.cache/uv` 而 exit 2；`git diff --check` 通过 | D-22 工作树与失败输出；未提交 | 改在 `frontend/` 运行 npm 门禁，并通过已批准的 uv 执行边界重跑后端定向测试；全部通过前不勾选 D-22 |

| 2026-09-05 | `D-22` 进行中；前端上传行为测试失败 | D-22 实现已进入有效前端门禁；静态检查和生产构建通过，但上传事件未到 mutation，因事件处理器要求 `FileList.item()`、测试提供的标准索引 files 对象没有该方法，导致新 UUID 未回填 | 前端 `1 failed, 16 passed`，另有 1 个未处理 TypeError；ESLint、Prettier、typecheck、production build 通过 | D-22 frontend test output；未提交 | 将文件读取改为兼容真实 FileList 与测试 DOM 的索引访问，重跑全部前端门禁；通过前不勾选 D-22 |

| 2026-09-05 | `D-22` 进行中；上传行为通过、lint 失败 | 文件选择已改用 FileList 索引访问，真实上传→服务端 UUID→query cache→选中项链路通过；类型系统确认该表达式只可能为 File 或 undefined，因此保留的 null 判断被 lint 判为冗余 | 前端 test `17 passed`、Prettier、typecheck、production build 通过；ESLint `no-unnecessary-condition` 1 项失败 | D-22 frontend output；未提交 | 删除不可能的 null 分支并重跑完整门禁；全部通过前不勾选 D-22 |

| 2026-09-05 | 完成 `D-22`；阶段任务完成、进入测试矩阵审计 | 合规 WAV 上传成功后直接把服务端返回的公开 Recording 写入 query cache、去重并自动选择其 UUID，confirm 随后发送该 UUID；响应/DOM 只呈现原始文件名和公开 QC，不含服务端 source path 或本机绝对路径。文件 input 同时兼容真实 FileList 索引语义 | 前端全量 `17 passed`，覆盖真实 RIFF/WAVE 上传、响应 UUID 回填、confirm body 与路径排除；recordings collection/后端错误脱敏 `2 passed`；ESLint、Prettier、typecheck、production build 与 `git diff --check` 全通过 | ModelsPage、App browser test、既有 API privacy test；未提交 | D-01～D-22 均完成；下一步逐条复读 7.7，运行组合矩阵并只在证据覆盖后勾选，随后审计 7.8 退出条件 |

| 2026-09-05 | 阶段 D 已完成 | 逐项复核 D-01～D-22 与 7.7：客户端 manifest HTTP 攻击被 422 拒绝且 bundle 内容不可替换；disabled、worker 缺失、manifest 缺失、installable 与 enabled 组合保持独立；WebUI 无普通 manifest input，可安装模型按 model ID 完成预检/上传/confirm；不支持模型在下载前得到稳定具体 reason；gated/aligner/WAV/披露/脱敏均有独立证据。7.8 三项退出语义全部成立 | 后端 API/support/manager/health/privacy 组合 `53 passed in 1.70s`；manifest-tool verifier/status `26 passed`；前端 test `17 passed`、ESLint、Prettier、typecheck、production build全通过；严格 mypy `123 source files`、Ruff、architecture `1 passed` 与 `git diff --check` 通过 | 阶段 D 实现、API/browser/unit/verifier tests 与进度记录；未提交 | 无阶段 D 阻塞；下一步重新读取阶段 E 进入条件和 `E-01`，不得将阶段 C 的单模型受控 bundle 当作 20 模型生产 bundle |

| 2026-09-05 | 阶段 D 标记需回归 | registry trust policy 修正会影响 API 使用的冻结模型事实与未来 Granite manifest 披露；尽管 TurboCTC worker 仍未实现且普通安装继续 fail closed，原状态/预检证据需在最终输入上复验 | 尚未修改实现或运行回归 | Granite 固定内容审计与 API 影响分析；未提交 | 修正后重跑 API/support/manager/health/privacy、前端状态/安装矩阵和静态门；通过前不恢复阶段 D |
| 2026-09-05 | 阶段 D 回归完成 | TurboCTC trust=true 后，服务/verifier 仍从独立 worker 支持合同判定其 `worker_implemented=false`、`installable=false`，`enabled=true` 不会放行；bundle-only、预检/确认、状态组合、WAV 与脱敏合同保持。WebUI 四状态与普通安装门未退化 | API/support/manager/health/privacy `53 passed in 1.71s`；前端 `17 passed`、ESLint、Prettier、typecheck、production build全通过；阶段 B manifest-tool `120 passed` 覆盖 verifier/status；阶段 A Ruff、严格 mypy `123 source files`、architecture 与本轮 `git diff --check` 通过 | D API/WebUI 回归与最终 registry/support 状态；未提交 | 阶段 D 恢复已完成；A～D 再次全部完成，阶段 E 可继续家族 selection |

| 2026-09-05 | 阶段 D 标记需回归 | pyannote 预检不能只披露主仓库 CC-BY-4.0；component source 的 repository/revision/relationship/license 必须通过现有只读 bundle 流入安装计划/API/UI，且继续禁止客户端替换、保持 gated 二次确认与四状态独立 | 当前安装计划/API/UI 只披露主 manifest 许可证与 remote-code 数量；尚未修改 | service/schema/frontend 影响审计；未提交 | 在 A～C 合同确定后增加只读披露与浏览器/API 测试，完整 D 门通过前不恢复 |

| 2026-09-05 | 阶段 D component-source 披露行为通过；格式/静态门失败 | 安装计划、模型列表 API 与 WebUI 已显示 component repository/revision/relationship/license，客户端请求结构未增加 provenance 输入；API 13 项和前端 17 项行为通过。Prettier 报 ModelsPage 格式差异并使 typecheck/build 未运行；Python Ruff 报 API test import 排序并使 mypy未运行 | API `13 passed`；frontend `17 passed`、ESLint通过、Prettier失败；Ruff `I001` 1 项 | manager/service/API/UI/tests 与门禁输出；未提交 | 机械格式化页面并调整 import，随后重跑全部前后端门；D 保持需回归 |

| 2026-09-05 | 阶段 D component-source 前端全门通过；后端 mypy 失败 | 格式修复后前端测试、lint、Prettier、typecheck、production build 全绿；后端 13 项与 Ruff 通过。mypy 对异构 manifest fixture 推断 repository 索引为 object，不能作为 `dict[str, ModelLicense]` 键，报 1 项，属于测试类型显式性问题 | frontend `17 passed` + 全静态/build；backend `13 passed`、Ruff通过、mypy 1 个 `dict-item` | API/UI/tests 与输出；未提交 | 对 fixture repository key 显式 `cast(str, …)`，重跑后端门和 diff；D 保持需回归 |

| 2026-09-05 | 阶段 D component-source 只读披露实现完成；待组合回归 | InstallationPlan、models API 与 install preflight 现在从只读 bundle 输出 component repository/revision/relationship/license/terms/files；service 再次对 component license inventory fail closed。WebUI 显示来源数及安装确认中的来源、关系、完整 revision 和许可证链接；普通 request/confirm schema 未增加 provenance 字段，客户端不能替换。主许可证、gated 二次确认与四状态保持独立 | API `13 passed`；frontend `17 passed`、ESLint、Prettier、typecheck、production build；Python Ruff、mypy（3 files）、`git diff --check` 全通过 | manager/service/api types/UI/backend+browser tests；未提交 | D 保持需回归，下一步跑 API/support/manager/health/privacy 组合与架构门；全绿后恢复 D |

| 2026-09-05 | 阶段 D component-source 回归完成 | 逐项复核 D-01～D-22 与 7.7/7.8：新增 provenance 只读披露不扩张客户端输入；bundle-only preflight、manifest 替换拒绝、四状态、disabled/未实现门、gated/aligner/WAV、remote-code、空间/environment 与错误脱敏均保持。component source 缺失/不匹配许可证在下载前拒绝 | API/support/manager/health/privacy `63 passed in 1.73s`；frontend `17 passed` + ESLint/Prettier/typecheck/build；architecture `1 passed` + script `OK`；Python定向 Ruff/mypy 与 `git diff --check` 全通过；B verifier 全量证据仍有效 | D API/WebUI/component disclosure 与组合门输出；未提交 | 阶段 D 恢复已完成；A/C 仍需完整回归，完成后才满足 E 8.2 第一项 |

| 2026-09-05 | 阶段 D 标记需回归；默认 API 无 production bundle | production 20-member bundle 已存在且离线验证通过，但默认 runtime composition 未向 service 注入它，导致真实默认 `/models` 与 bundle-only install preflight 无法披露/使用正式 manifest。D 的受控 service fixture 证据不能替代默认应用组合 | `build_default_service()` 与 `ClassScribeService._manifest_metadata()` 对照：后者在 bundle=None 时返回 unavailable；默认构造未传 bundle | runtime/API composition 审计；未提交 | 与 C 同步补默认 fail-closed bundle 加载；回归默认 models/preflight、缺失/篡改 bundle 启动失败及现有 API/security/status 门，通过前 D 不恢复 |

| 2026-09-05 | 阶段 D 默认 API bundle 回归完成 | 默认 ClassScribeService 已持有 verified production bundle；真实 `models()` 输出中 manifest/provenance 可用，disabled 与 worker-not-implemented 仍不可安装。缺件/篡改在 service 构造前失败；现有 bundle-only request/confirm、替换拒绝、四状态、安全及 disclosure API 原测试全部通过 | 默认 runtime 测试含 source 20 项/缺件/篡改通过；API `13 passed in 1.41s`；相关 support/manager/loader 组合全绿；Ruff/mypy/architecture/diff通过 | runtime/service/API 消费者回归；未提交 | 阶段 D 恢复已完成；前端字节未改变，既有 17 项+build证据不失效；返回 E 健康矩阵 |

| 2026-09-05 | 阶段 D 标记需回归；API/status 将拒绝旧 bundle | 默认 API 已 fail-closed 绑定 production bundle；FireRed registry lock SHA 更新后，旧 bundle 不能加载，models/preflight/status 证据必须对新 bundle 重跑。前端代码与 provenance/四状态语义本身不变 | runtime loader 的 manifest environment/worker-lock SHA 严格比较合同 | composition/API 影响审计；未提交 | 新 bundle 后重跑默认 service、API/support/安全/静态门；通过前 D 与 E 8.2 第一项保持回退 |

| 2026-09-05 | 新 bundle consumer 组合回归命令/执行边界失败 | 内置 loader、runtime、manager、support、health 与 API 组合显示约 83 项通过后超过 60 秒无输出，人工中断 exit 130，不能把点号计为完整门。并行 Ruff/diff 通过；误用 `mypy .` 使无 package 的多个 worker `adapter.py` 被视为同一顶层模块，报 duplicate module，属于命令形状错误且未完成静态分析 | consumer/API 组合 Ctrl-C exit 130；mypy exit 2 `Duplicate module named adapter`；Ruff/diff通过 | 组合回归与静态命令输出；未提交 | 按文件组拆分定位挂起；mypy 改回仓库既定 source 列表/显式 package bases，A/C/D 保持需回归 |

| 2026-09-05 | D 前端全门首次启动命令失败 | 为避免仅凭“本轮未编辑前端”沿用旧证据，决定重新运行完整前端 check；当前 shell 直接调用 `pnpm check` 立即 exit 127 且无输出，说明 pnpm shim 不在 PATH，没有执行任何 lint/test/build | `pnpm check` exit 127、零输出 | 前端门启动失败；未提交 | 使用仓库 packageManager 声明对应的 `corepack pnpm check` 重跑；D 保持需回归 |

| 2026-09-05 | D 前端全门第二次启动命令失败；Node 24 工具链已定位 | 当前 PATH 下直接调用 `corepack pnpm check` 仍 exit 127、零输出；只读盘点随后确认系统 Node 为 v22，而仓库要求 Node 24，并定位到用户工具链的 Node v24.17.0 与其 corepack。没有安装、更新或改动 node_modules | corepack exit 127；系统 `node v22.23.1`；固定工具链 `v24.17.0/bin/node` 与 corepack 存在 | 工具链路径盘点；未提交 | 仅为命令 PATH 前置已存在的 Node v24.17.0 bin，再运行 `corepack pnpm check`；D 保持需回归 |

| 2026-09-05 | D 前端全门第三次启动失败；Corepack cache 只读 | Node 24 生效后，Corepack 试图在只读用户 cache 下创建 pnpm v1 cache 并 exit 1；仍未执行 lint/test/build。仓库 `node_modules` 与五个直接工具入口已存在，无需写用户目录或下载依赖 | Node v24.17.0；corepack `ENOENT ... .cache/node/corepack/v1` | Corepack失败输出与本地工具盘点；未提交 | 在相同 Node 24 下用 `npm run lint`、`format:check`、`typecheck`、`test`、`build` 逐项执行 package.json 中与 check 完全相同的五步；D 保持需回归 |

| 2026-09-05 | 阶段 D 新 production bundle 回归完成 | 默认 service 在新 bundle 下加载 20 项，manifest/provenance、disabled、worker-not-implemented 与普通安装资格继续独立；内置 loader、缺失/篡改、preflight/request/confirm、安全与 API 全门通过。Node 24 下直接执行 package.json 的五个 check 子步骤，前端行为、格式、类型和 production build 全绿 | pure consumer 72；API `13 passed`；全仓 `471 passed, 1 expected skip` 加 RPM 1；前端 `17 passed`、ESLint、Prettier、typecheck、Vite build；Ruff、mypy 224、architecture、diff全绿 | runtime/API/UI 与完整门输出；未提交 | 阶段 D 恢复已完成；A/B/D 已完成，C 仍需新 bundle Whisper 真实离线链，E 8.2 第一项继续未勾选 |

| 2026-09-05 | 阶段 D 新 Qwen bundle 完整回归完成 | 在阶段C的新bundle真实链闭合后，默认runtime重新加载20项production bundle；models、manifest GET、bundle-only preflight/confirm、manifest替换拒绝、四状态、component provenance、disabled/未实现门、WAV及错误脱敏保持。前端仍无manifest input，完整安装交互与披露未退化 | runtime/API/status/manager/health/privacy `66 passed in 1.85s`；前端 `17 passed`；ESLint、Prettier、typecheck、Vite production build全通过；同轮全仓/Ruff/mypy/architecture/diff证据全绿 | 新bundle下后端消费者、API与浏览器完整门输出；未提交 | D-01～D-22、7.7与7.8当前闭合，阶段D恢复已完成；A～D均完成，恢复E 8.2第一项并进入8.12第3项Qwen3-ASR-0.6B CPU真实链 |
| 2026-09-05 | Granite新bundle D回归准备；记忆中的Node 24路径不存在 | 已用collect-only精确确认默认runtime/API/status/manager/health/privacy既定文件组仍为66项；前端执行前尝试复用记忆中的mise路径，但该`node`/`npm`位置实际不存在，直接版本命令exit 127。没有运行任何前端门、安装或更新依赖，也未改变`node_modules` | 后端collect-only：`66 tests collected`；错误路径的`ls`失败，`node --version` exit 127 | D文件组清单与Node路径失败输出；未提交 | 从当前shell和用户工具目录只读定位真实Node v24.17.0；确认后用其PATH运行package.json五个既定子门，D保持需回归 |
| 2026-09-05 | Granite新bundle D后端组合在受限sandbox挂起并终止；前端三门通过 | 真实Node路径已定位为`.nvm/versions/node/v24.17.0/bin`；ESLint、Prettier和Vitest在该版本下通过。并行的既定66项后端组合在受限sandbox只输出14个点后超过90秒无进展，人工发送Ctrl-C后exit 130；这些点号不计完整门，未修改测试或产品代码 | 前端lint/format exit 0、Vitest `17 passed`；后端受限副本exit 130、无summary | 前端三门与受限后端session 44237输出；未提交 | 在允许其既有本机socket/namespace边界的外层普通用户环境原样重跑同一66项；另串行完成typecheck和production build，D保持需回归 |
| 2026-09-05 | 阶段D Granite新bundle完整回归完成 | 在阶段C新bundle真实链闭合后，默认runtime重新加载20项production bundle；models、manifest GET、bundle-only preflight/confirm、manifest替换拒绝、四状态、component provenance、disabled/未实现门、WAV及错误脱敏保持。受限sandbox挂起的同一后端组合在外层边界快速完成；前端仍无manifest input，完整交互与披露未退化 | runtime/API/status/manager/health/privacy `66 passed in 1.89s`；前端`17 passed`、ESLint、Prettier、typecheck、Vite production build全通过；同轮全仓`472 passed, 1 skipped, 1 deselected`、RPM、Ruff/mypy/architecture/diff全绿 | 新bundle下后端消费者、API、浏览器和production build完整门输出；未提交 | D-01～D-22、7.7与7.8当前闭合，阶段D恢复已完成；A～D均完成，恢复E 8.2第一项并严格返回8.12第4项日语primary Granite production真实链 |
| 2026-09-06 | 阶段 D Nemotron target-class 修复后完整回归闭合 | 默认runtime继续加载相同20项production bundle；worker源码修复未改变bundle-only安装、四状态、provenance、健康WAV、错误脱敏或前端模型交互。后端既定组合在外层边界完成，Node 24五门逐项全绿 | 后端runtime/API/support/manager/health/privacy `66 passed in 1.80s`；前端ESLint、Prettier、typecheck、Vitest `17 passed`、Vite production build全部通过；diff通过 | Nemotron修复后的后端消费者、API和浏览器完整门输出；未提交 | D-01～D-22、7.7与7.8继续闭合；A～D全部恢复，阶段E从暂停恢复进行中，严格重试8.12第8项Nemotron 3.5 |
| 2026-09-06 | 阶段 D 新 Nemotron production bundle 完整回归闭合 | 默认runtime在新bundle下加载20项；models、manifest GET、bundle-only preflight/confirm、客户端manifest替换拒绝、四状态、component provenance、disabled/未实现门、WAV及错误脱敏合同均保持。Node 24 前端仍无manifest输入且完整交互与披露未退化 | 后端runtime/API/support/manager/health/privacy `66 passed in 1.85s`；Node `v24.17.0`；前端Vitest `17 passed`、ESLint、Prettier、typecheck、Vite production build全通过；diff通过 | 新 production bundle 下的后端消费者、API、浏览器与production build输出；未提交 | D-01～D-22、7.7与7.8当前闭合；A～D全部完成，严格返回阶段E第8项两个Nemotron正式验收 |

## 8. 阶段 E：生产模型 bundle 与真实验收

状态：**已完成**

### 8.1 阶段目标

按人工策展策略为 registry 全部 20 个 model ID 生成生产 manifest，完成五方合同一致性验证，并按风险
顺序对当前实际使用模型执行真实目标 Fedora 健康验收；manifest 存在不自动改变 enabled/installable。

### 8.2 进入条件

- [x] 阶段 A～D 已完成。
- [x] 发布者具备所需网络、磁盘和必要的真实 gated 授权。
- [x] 目标 Fedora CPU 环境已用于全部14个实际使用模型的分批健康检查；GPU driver不可用已在风险表
  独立fail closed，不以CPU结果伪造GPU/VRAM证据。

### 8.3 所有模型共用的 Transformers 闭包规则

对通用 Transformers 模型，必须从 config、processor/tokenizer 配置和权重 index 推导完整闭包：

- [x] `config.json` 以及其引用的专用 config；
- [x] tokenizer vocab/model/merges、tokenizer config、special tokens；
- [x] processor、feature extractor、preprocessor config；
- [x] generation config；
- [x] 选择的一种 runtime 权重格式；
- [x] index 的 `weight_map` 引用的全部 shard；
- [x] config `auto_map` 和 processor 动态加载引用的全部 Python 文件；
- [x] worker 显式打开的模板或额外资源。

不得同时包含未使用的 ONNX、TensorFlow、Flax、GGUF 或其他精度副本。

### 8.4 FireRed 家族

- [x] `E-FR-01` `firered_vad` selection 至少包含 `cmvn.ark`、`model.pth.tar`；如发布 streaming
  子目录布局，必须完整保留 `VAD/`、`Stream-VAD/` 与 worker 期望一致的结构。
- [x] `E-FR-02` `firered_lid` 至少包含 `cmvn.ark`、`model.pth.tar`、`dict.txt`。
- [x] `E-FR-03` `firered_asr2_aed` 至少包含 `model.pth.tar`、`cmvn.ark`、`dict.txt`、
  `train_bpe1000.model`。
- [x] `E-FR-04` `firered_punc` 至少包含 `config.yaml`、`model.pth.tar`、
  `chinese-bert-wwm-ext_vocab.txt`、`out_dict` 以及完整 `chinese-lert-base/` 运行闭包。
- [x] `E-FR-05` 上述最低集合之外，继续通过真实 load/infer 行为确认全部传递依赖。
- [x] `E-FR-06` `firered_asr2_llm` 生成供应链 manifest 供 inventory 使用；在真实 LLM worker 和
  目标硬件验收完成前保持 disabled 且普通安装入口不可用。

### 8.5 MOSS 与 ARK remote code

- [x] `E-RA-01` `moss_td_0_9b`、`moss_transcribe_preview_2b`、`ark_asr_3b` 的所有 Python 动态
  代码、模板和自定义 processor 文件保留上游布局并标为 `remote_code`。
- [x] `E-RA-02` 解析 `auto_map`、动态 processor 引用和 worker 直接文件访问，不只按 `.py` 扩展名
  猜测闭包。
- [x] `E-RA-03` 健康检查在网络 namespace 被取消且 Hugging Face offline flags 强制为 1 的环境完成。

### 8.6 Qwen

- [x] `E-QW-01` 为两个 ASR checkpoint 与 ForcedAligner 分别生成独立 manifest。
- [x] `E-QW-02` 分别保留各自 tokenizer/processor/权重闭包。
- [x] `E-QW-03` 不创建不存在的 `Qwen3-ASR-1.7B-JA` 上游身份。
- [x] `E-QW-04` ForcedAligner 健康检查提供与短音频一致的精确参考文本和受支持语言。

### 8.7 Granite

- [x] `E-GR-01` `granite_speech_4_1_2b` 与 `granite_speech_5_0_turboctc_470m` 独立冻结。
- [x] `E-GR-02` 只选择 worker 实际使用的 PyTorch/Transformers 权重格式。
- [x] `E-GR-03` TurboCTC 当前未进入 bootstrap ranking；manifest 存在不等于默认加载。

### 8.8 Nemotron

- [x] `E-NE-01` 每个 manifest 安装目录最终恰好包含一个可被 worker 找到的根级 `.nemo` archive。
- [x] `E-NE-02` 不得同时安装多个 `.nemo` 变体。
- [x] `E-NE-03` 两个 checkpoint 独立生成 manifest，不共享或改名 archive。

### 8.9 pyannote gated 与多来源闭包

- [x] `E-PY-01` `pyannote_community_1` 的生成和安装均要求真实授权；token 不进入 bundle。
- [x] `E-PY-02` 验证 `config.yaml` 引用的全部离线组件均被包含，运行时不得解析远程模型 ID。
- [x] `E-PY-03` 对嵌套组件来源和许可证单独审计。
- [x] `E-PY-04` 若嵌套组件来自其他 repository，先扩展供应链合同以表达多来源，不能将其错误归因于
  Community-1 单一 repository。
- [x] `E-PY-05` 多来源能力未实现前，不得通过手工复制嵌套 cache 伪造单仓库 manifest，也不得标记
  pyannote manifest 完整或可安装。

### 8.10 disabled 与实验模型状态

- [x] `E-DIS-01` `fun_asr_nano_2512` 和 `whisper_tiny_reference` 可以生成并验证 manifest，但继续
  保持 disabled。
- [x] `E-DIS-02` `firered_asr2_llm`、`voxtral_mini_4b_realtime_2602`、
  `vibevoice_asr_streaming_1_5b` 在真实 worker 未实现前只进入供应链 inventory，不进入普通安装操作。
- [x] `E-DIS-03` 对每个模型分别记录 `manifest_available`、`worker_implemented`、`installable`、
  `enabled`，禁止用 manifest 存在推断后三者。

### 8.11 20 个 manifest 的生成清单

- [x] `E-M01` `moss_td_0_9b.json`
- [x] `E-M02` `firered_asr2_aed.json`
- [x] `E-M03` `firered_asr2_llm.json`
- [x] `E-M04` `firered_vad.json`
- [x] `E-M05` `firered_lid.json`
- [x] `E-M06` `firered_punc.json`
- [x] `E-M07` `granite_speech_4_1_2b.json`
- [x] `E-M08` `qwen3_asr_1_7b.json`
- [x] `E-M09` `qwen3_asr_0_6b.json`
- [x] `E-M10` `qwen3_forced_aligner_0_6b.json`
- [x] `E-M11` `ark_asr_3b.json`
- [x] `E-M12` `moss_transcribe_preview_2b.json`
- [x] `E-M13` `nemotron_3_5_asr_streaming_0_6b.json`
- [x] `E-M14` `nemotron_speech_streaming_en_0_6b.json`
- [x] `E-M15` `granite_speech_5_0_turboctc_470m.json`
- [x] `E-M16` `voxtral_mini_4b_realtime_2602.json`
- [x] `E-M17` `vibevoice_asr_streaming_1_5b.json`
- [x] `E-M18` `pyannote_community_1.json`
- [x] `E-M19` `fun_asr_nano_2512.json`
- [x] `E-M20` `whisper_tiny_reference.json`
- [x] `E-M21` 生成 `bundle.v1.json`，恰好包含以上 20 个唯一 ID，按 model ID 排序并验证成员 SHA。

### 8.12 真实模型验收顺序

必须按以下顺序降低风险；某一项阻塞时记录阻塞并评估是否可继续不依赖它的后续项，但不得跳过最终门：

1. [x] `whisper_tiny_reference`：CPU 小模型，验证真实 bundle → 下载 → 离线 worker 生命周期。
2. [x] FireRedVAD、FireRedLID：CPU 辅助模型。
3. [x] `qwen3_asr_0_6b`：较小 ASR 与 IBus 路径。
4. [x] 中文、日语、英语三个课堂 primary。
5. [x] MOSS 结构、ARK 和其他 fallback。
6. [x] ForcedAligner、FireRedPunc。
7. [x] pyannote gated 与离线嵌套依赖。
8. [x] Nemotron 两个 streaming checkpoint。
9. [x] TurboCTC 和 FunASR 实验项：前者`worker_not_implemented`、后者`registry_policy_disabled`，
   均在下载前阻止且不虚构健康证据。
10. [x] FireRed LLM、Voxtral、VibeVoice：均为`registry_policy_disabled`且worker未实现，只保留供应链
    inventory，不执行不可能成立的健康验收。

对每个 enabled 模型，必须在目标 Fedora 环境完成并留证：

- [x] bundle SHA 验证；
- [x] 安装预检与空间披露；
- [x] 固定 revision 下载；
- [x] 无网络 Bubblewrap load → 对应任务 infer → unload；
- [x] 非空、有意义且符合协议的响应；
- [x] active revision 和 `supply-chain.json` 复验；
- [x] 重启 core 后离线再次推理；
- [x] 实测 VRAM/CPU 路由记录，但不把单次结果冒充正式 benchmark。

### 8.13 阶段测试

- [x] selection 对每个模型精确闭包且无未分类、额外或缺失文件。
- [x] 所有 manifest 的 repository/revision/worker/trust/license/environment/lock 与冻结输入一致。
- [x] bundle verifier 离线验证恰好 20 个成员。
- [x] 所有 remote code 都显式分类、逐文件哈希并在 UI/API 披露。
- [x] 对当前实际使用的 14 个模型执行原计划要求的真实健康验收；TurboCTC 与 disabled 项另行记录准确
  状态，不因 registry 中的 enabled 字段或 manifest 存在而虚构健康证据。
- [x] pyannote 只有在多来源闭包问题解决后才标记 manifest 完整。

### 8.14 退出条件

registry、revision locks、license inventory、selection、bundle 和 worker support 五方完全一致；bundle
恰好覆盖 20 个 model ID；所有 enabled 且进入当前 profile 的模型均通过目标机健康检查；未实现、disabled、
未完成多来源审计或未验收模型不能显示为普通可安装。

### 8.15 逐模型证据登记表

| model ID | manifest SHA | manifest_available | worker_implemented | installable | enabled | 固定 revision 下载 | 离线 health | 重启复验 | CPU/GPU/VRAM | 阻塞原因/证据 |
|---|---|---:|---:|---:|---:|---|---|---|---|---|
| moss_td_0_9b | `2e364b702da1dfc8bca35d292cb578fc6c31795ec72121fed7c697f38c9bd913` | 是 | 是 | 是 | 是 | 双 run及production安装通过 | 24G/2G scope内CPU结构健康通过 | 独立断网新core通过 | CPU float32；VRAM 0 MiB；GPU待验 | revision `704aa4a9…5b15`、16 files/1,833,089,258 bytes、aggregate `e4fa9968…2421`；backend `transformers_moss_transcribe_diarize` |
| firered_asr2_aed | `b762acfbf217087e23dc79eca1f98754c88e7e158d8ff90d8c6968c0d2e5755b` | 是 | 是 | 是 | 是 | 双 run及安装通过 | 中文CPU最终identity通过 | 独立断网新core通过 | CPU float32；VRAM 0 MiB；GPU待验 | revision `2304afed…0b64ec`、4 files/4,731,890,696 bytes、aggregate `f300b3e4…c349`；重启精确转写“今天我们学习光合作用” |
| firered_asr2_llm | `1eb3e170cffd04ce8368716955d12b2059451395c80add3f473657b5829c3879` | 是 | 否 | 否 | 否 | 双 run 通过 | 不适用 | 不适用 | 不适用 | `registry_policy_disabled`；真实 LLM worker 未实现 |
| firered_vad | `e8067bf09fd90b66a056dcae8493c852ff4abd09bdadaa00111660c89aea174a` | 是 | 是 | 是 | 是 | 双 run 及安装通过 | 最终 synthetic identity 通过 | 独立断网新 core 通过 | CPU；VRAM 未报告 | revision `7990aacc…b8c`、4 files/4,654,184 bytes、aggregate `52737a63…bea7`；重启 1 个 VAD segment、13.05 ms |
| firered_lid | `1670bdef7cc7f84b2d1972d4667484ef17c2e6a4769e62d8231acf536ecbedbc` | 是 | 是 | 是 | 是 | 双 run 及安装通过 | 最终 synthetic identity 通过 | 独立断网新 core 通过 | CPU；VRAM 未报告 | revision `1bb4d285…c11b`、3 files/3,550,105,508 bytes、aggregate `e513cd96…987b`；重启 `en=0.467`、1 segment、574.343 ms |
| firered_punc | `98153f5694bbc0d09484f25fa1417329c53618d4db8180b6e5e481387c92dd6e` | 是 | 是 | 是 | 是 | 双 run及production安装通过 | 24G/2G scope内CPU英语标点健康通过 | 独立断网新core通过 | CPU float32；VRAM不适用；GPU待验 | revision `e448fd96…28da`、11 files/818,896,071 bytes、aggregate `293d9820…dd3c`；backend `fireredpunc` |
| granite_speech_4_1_2b | `7ca6adadf1a0c81e3694156e017b3c91f524b8b882f9b954973e511f9064427e` | 是 | 是 | 是 | 是 | 新lock下双 run及production安装通过 | 24G/2G scope内CPU真实日语健康通过 | 独立断网新core通过 | CPU float32；VRAM 0 MiB；GPU待验 | revision `de575db6…bc546`、14 files/4,636,316,501 bytes、aggregate `88a5b402…eef`；首次超时/主机压力证据保留，受控首次与重启均未复现 |
| qwen3_asr_1_7b | `a56f3fcc7b6474f4853da296d3b8fba1b7a3ba9dedafcf85f8a790427f935f49` | 是 | 是 | 是 | 是 | 新双 run及production安装通过 | 24G/2G scope内CPU英语健康通过 | 独立断网新core通过 | CPU float32；VRAM 0 MiB；GPU待验 | revision `7278e1e7…6e5`、10 files/4,703,055,333 bytes、aggregate `2c001cd3…0f37`；backend `qwen_asr_transformers` |
| qwen3_asr_0_6b | `50f7366f9206e96a9d5f66000418492601eb7af235bfd01f8146813800ffbe0e` | 是 | 是 | 是 | 是 | 新双 run 及安装通过 | CPU最终identity通过 | 独立断网新core通过 | CPU float32；VRAM 0 MiB；GPU待验 | revision `5eb14417…03b0`、8 files/1,880,560,703 bytes、aggregate `94ca9503…b8db`；backend `qwen_asr_transformers`，重启1 segment；GPU矩阵仍等待driver |
| qwen3_forced_aligner_0_6b | `613e1af895d44e2598a90c9435fc84447298210a9baea57e5afd1889dda17702` | 是 | 是 | 是 | 是 | 双 run及production安装通过 | 24G/2G scope内CPU精确英语align通过 | 独立断网新core通过 | CPU float32；VRAM不适用；GPU待验 | revision `c7cbfc20…62b7`、8 files/1,840,013,484 bytes、aggregate `c55bd247…649ed`；backend `qwen3_forced_aligner`；source env `bae6f79c…45b95` |
| ark_asr_3b | `4601414756b4b47da4f816d1cf0c851948647a61f92c7f0629455edad0f3e05d` | 是 | 是 | 是 | 是 | 双 run及production安装通过 | 24G/2G scope内CPU英语健康通过 | 独立断网新core通过 | CPU float32；VRAM 0 MiB；GPU待验 | revision `1e28271b…09ed3`、18 files/8,142,995,244 bytes、aggregate `bb82b526…49ed`；backend `transformers_arkasr` |
| moss_transcribe_preview_2b | `78797aaaa515ff283f48535b7d4f985bf164bb7933d42d35bc31770cdf023a83` | 是 | 是 | 是 | 是 | 双 run及production安装通过 | 24G/2G scope内CPU英语健康通过 | 独立断网新core通过 | CPU float32；VRAM 0 MiB；GPU待验 | revision `c98175cb…9f780`、14 files/4,853,759,602 bytes、aggregate `b4713a5c…6300`；backend `transformers_moss_preview` |
| nemotron_3_5_asr_streaming_0_6b | `4df2371465afb83453a540980daf304c426e8fa67c29c68434c25fd93b831362` | 是 | 是 | 是 | 是 | 新Git lock下双 run及正式production安装通过 | 正式CPU batch与cache streaming通过 | 独立断网新core batch与新worker streaming通过 | CPU；VRAM未报告 | revision `1c8deaec…395d`、1 file/2,368,284,501 bytes、aggregate `30ce624f…a5d8`；stream 11 pushes/10 nonempty、fast cached flush；backend `nemo_cached_streaming` |
| nemotron_speech_streaming_en_0_6b | `494f48f80b7b569b6584f717713197b33b683e4912dfa3bb1dfd88d49c53c4c1` | 是 | 是 | 是 | 是 | 新Git lock下双 run及正式production安装通过 | 正式CPU batch与cache streaming通过 | 独立断网新core batch与新worker streaming通过 | CPU；VRAM未报告 | revision `ebe59e5a…ed50`、1 file/2,473,041,920 bytes、aggregate `cccfcfb6…65b3`；stream 11 pushes/7 nonempty、fast cached flush；backend `nemo_cached_streaming` |
| granite_speech_5_0_turboctc_470m | `e886354a819b9b868f12fd9d0c3582a0a08e6b04d10ac43aa10b84b3b14b4fbd` | 是 | 否 | 否 | 是 | 新lock下双 run 通过 | 不适用 | 不适用 | 不适用 | `worker_not_implemented`；未进入 bootstrap ranking |
| voxtral_mini_4b_realtime_2602 | `67282a2ad05f70871e92306e782ce0d58f15cdafef12b3e301c9def602f1e26b` | 是 | 否 | 否 | 否 | 双 run 通过 | 不适用 | 不适用 | 不适用 | `registry_policy_disabled`；真实 worker 未实现 |
| vibevoice_asr_streaming_1_5b | `50ee47bf50db6e811e0e42e642f48ab405a6be02dd41a4a43391fc070cc4b933` | 是 | 否 | 否 | 否 | 双 run 通过 | 不适用 | 不适用 | 不适用 | `registry_policy_disabled`；真实 worker 未实现 |
| pyannote_community_1 | `179c9160ebaaa22687971e9271eecf0bf6158d590e50b798f8a53e709c6a6a0e` | 是 | 是 | 是 | 是 | gated双run及production安装通过 | 24G/2G scope内CPU diarize通过 | 无token独立断网新core通过 | CPU float32；VRAM 0 MiB；GPU待验 | revision `3533c8cf…54ee`、5 installed files/32,821,421 bytes、aggregate `9010691d…ad82`；WeSpeaker derived component已离线验证；backend `pyannote_audio_community_1` |
| fun_asr_nano_2512 | `375e3c770e1da4ed1c35e97d22841de98d8871cd72fa6f3cf07aae685707aa66` | 是 | 是 | 否 | 否 | 双 run 通过 | 待实验验收 | — | — | `registry_policy_disabled`；实验项 |
| whisper_tiny_reference | `2d805d29c6ec3465a0824d2d87c121c3b16514856bdbdaee3d0b90750c16eba8` | 是 | 是 | 否 | 否 | 双 run 及新bundle安装通过 | 最终synthetic identity通过 | 新bundle独立断网core通过 | CPU int8；VRAM 0 MiB | bundle `55069eea…1357`；`registry_policy_disabled`；验收链通过，普通安装仍阻止 |

> 表中的 `worker_implemented=待核验` 不等同于已实现；必须由模型 ID 支持矩阵和真实 load → infer → unload
> 证据更新。`enabled` 初值来自当前 registry，只表示自动候选资格。

### 8.16 阶段进度记录

| 日期 | 状态/任务 ID | 变更或结论 | 验证命令与结果 | 提交/证据 | 阻塞与下一步 |
|---|---|---|---|---|---|
| 2026-09-04 | 未开始 | 20 个生产 manifest 均不存在 | 未运行实施验证 | 本文件 | 阶段 D 完成后按 8.12 顺序执行 |
| 2026-09-05 | 阶段 E 开始；进入条件部分满足 | 阶段 A～D 已完成；项目盘可用约 328 GiB、`/tmp` 可用约 9.4 GiB，满足开始 discovery 的本地空间条件；仅确认 HF 凭证变量存在，尚未证明 pyannote gated 实际授权。主机存在 `nvidia-smi`，但无法与 NVIDIA driver 通信，因此 GPU 目标机健康验收当前不可执行 | `df -h` 成功；凭证仅输出 present=yes、不读取值；`nvidia-smi --query-gpu` exit 9 | 阶段 D 完成记录与本机环境盘点；未提交 | 保持 8.2 后两项未勾选；先读取 CLI 并验证网络，从不依赖 GPU/gated 的生产 discovery 开始；GPU/gated 最终门保持 fail closed |
| 2026-09-05 | 阶段 E discovery 进行中；gated 明示同意边界阻塞 | 使用真实凭证环境对冻结 repository/revision 启动联网递归 discovery，但未传 `--accept-gated-repository`，工具在 gated 模型前拒绝继续；错误仅为稳定授权说明，不包含 token。不会代替用户声明接受上游条款，也不把 pyannote 标为完整 | `classscribe-model-manifest discover` exit 2：`gated repository access requires ... explicitly confirm that acceptance` | `/tmp` 私有 discovery 审阅目录（不进入 bundle/提交）；未提交 | 盘点停止前已生成的公开模型报告；继续不依赖 gated/GPU 的 selection 策展。pyannote 需用户真实接受条款并明确确认后才能重跑，最终门保持阻塞 |
| 2026-09-05 | 阶段 E discovery 恢复；阶段 B 回归关闭 | 盘点临时目录确认停止前已生成 13 个公开模型报告，目录/文件权限分别为 0700/0600。为继续独立项而增加的选择性 discovery 已完成全阶段 B 回归；未读取、输出或持久化凭证值 | 13 个报告路径盘点；CLI 定向 `11 passed`、manifest-tool 全量 `120 passed` 及 B 全静态/离线/锁/架构门通过 | `/tmp` 私有报告与选择性 CLI；未提交 | 对排序在 pyannote 之后的 6 个公开模型显式运行 fixed-revision discovery；pyannote 本身继续保持 gated 阻塞 |
| 2026-09-05 | FireRed selection 进行中；阶段 B 回归未闭合 | 19 个公开模型 discovery 已齐；按 worker 直接访问和上游完整树写入 AED、LLM inventory、LID、Punc、VAD 的精确 selection。VAD 同时保留 VAD/Stream-VAD，Punc 排除未使用 TF 副本并保留本地 PyTorch tokenizer/config/weight 闭包 | 5 个 FireRed 固定 revision 真实 discovery/selection 分区通过；阶段 B 全量因 1 条仍要求“仅 Whisper”的过时测试失败（119 其余通过） | production selection、`/tmp` discovery reports、worker audit；未提交 | 修正阶段感知测试并完成 B 回归；未通过前不勾选 E-FR 条目或生成 manifest |

| 2026-09-05 | 完成 `E-FR-01`～`E-FR-04`；阶段进行中 | FireRedVAD selection 同时完整保留 `VAD/` 与 `Stream-VAD/` 的 cmvn/model 并明确排除未使用 AED 副本；LID 与 AED 覆盖 adapter 强制文件；Punc 包含 root config/model/vocab/out_dict 和 `chinese-lert-base/` 的 PyTorch config/tokenizer/weight 闭包，明确排除 TF 权重与文档。LLM selection 已策展但 manifest 尚未生成 | 5 个模型真实 fixed-revision discovery 对 selection 精确分区；manifest-tool `120 passed`，offline/frozen/lock/Ruff/mypy/release-lock/architecture/diff 全门通过 | production selection、selection tests、临时 discovery reports；未提交 | `E-FR-05` 等到真实模型 load/infer 才可勾选；`E-FR-06` 等到 disabled LLM manifest 实际生成。下一步按 8.5 审计 MOSS/ARK 动态代码、模板、processor 与 worker 直接访问 |

| 2026-09-05 | 完成 `E-RA-01`～`E-RA-02`；阶段进行中 | ARK/MOSS 两仓的 config/processor auto_map、代码相对 import、MOSS Preview 动态模板 loader 与三个 worker 的直接文件访问均从固定 commit 内容核验；selection 保留全部 Python、`.jinja` 模板、processor/tokenizer/config、权重 index 与所有 shard 的原始布局，并把所有动态代码/模板显式标为 remote_code | 18 个固定 commit 小文件下载/解析成功；三模型真实 discovery 精确分区；manifest-tool `120 passed` 及 offline/frozen/lock/Ruff/mypy/release-lock/architecture/diff 全门通过 | `/tmp` 审阅文件、production selection、tests；未提交 | `E-RA-03` 必须等待实际权重和无网络 Bubblewrap 健康验收；下一步按 8.6 审计 Qwen 两个 ASR 与 ForcedAligner 的独立闭包 |

| 2026-09-05 | 完成 `E-QW-01`～`E-QW-03` 的独立 production selection 合同；阶段进行中 | 两个 Qwen ASR checkpoint 与 ForcedAligner 各自拥有独立精确 selection，未共享权重或重写身份；0.6B/Aligner 各保留单一 safetensors，1.7B 保留 index 与两个 shard；三者各自包含 tokenizer/preprocessor/chat template/config。registry、revision lock 与 selection 均无虚构 `Qwen3-ASR-1.7B-JA` | 三模型真实 fixed-revision discovery 精确分区；manifest-tool `120 passed` 及 offline/frozen/lock/Ruff/mypy/release-lock/architecture/diff 全门通过 | production selection、tests、临时 reports；未提交 | `E-QW-04` 等真实 ForcedAligner 健康验收；下一步按 8.7 独立策展 Granite 4.1 与 TurboCTC |

| 2026-09-05 | 阶段 E 进度更正：`E-QW-01` 回退，已完成项仅 `E-QW-02`～`E-QW-03` | 上一条将“已有三个独立 production selection”错误等同于 E-QW-01 要求的“三个独立 manifest”；实际尚未运行 generate，故立即撤销 QW-01 勾选且不删除原记录。QW-02 的三套独立 tokenizer/processor/权重闭包与 QW-03 的真实身份约束证据不受影响 | 复读 8.6 任务原文并盘点 `config/model-manifests/v1`：尚无三个 Qwen production manifest | 本更正记录与未勾选 QW-01；未提交 | 等完整 20 项 selection 和 gated 策略闭合后实际生成/验证 manifest，届时才可完成 QW-01；当前继续 Granite selection |

| 2026-09-05 | Granite selection 阻塞；发现冻结 trust policy 错误 | Granite 4.1 可使用内建 Transformers 闭包；TurboCTC 的固定 preprocessor auto_map 明确要求本仓库 custom processor，且其代码直接解析 `tokenizer.json`。在 registry `trust_remote_code=false` 下无法同时满足完整闭包与 manifest 双向 remote-code 合同，因此未写入 Granite selection、未勾选 E-GR 项 | 两个固定 revision tree 已发现；10 个固定小文件下载成功；JSON/代码引用审计确认冲突 | `/tmp` Granite 审阅文件、registry/generator 合同；未提交 | 先修正 TurboCTC trust policy 并闭合 A～D 回归；之后纳入 custom processor 及其传递依赖，保持 worker_not_implemented/非默认状态 |

| 2026-09-05 | 完成 `E-GR-02`～`E-GR-03`；阶段进行中 | Granite 4.1 只选择主 Transformers safetensors index 的三 shard，排除未引用的 out_llm 副本；TurboCTC 只选择单一 safetensors 与 fixed preprocessor auto_map 所需 remote processor。其 registry trust 已修正，但 worker support 清单仍明确 false，未进入 bootstrap ranking/普通安装；enabled 不改变该事实 | 两模型真实 fixed-revision tree 精确分区；A 合同组合 `80 passed`、B 工具 `120 passed`、C `89 passed, 1 skipped`（真实路径缺失，未冒充通过）、D 后端 `53 passed`/前端 `17 passed` 与全静态门通过 | registry/selection/support/API/tests；未提交 | `E-GR-01` 等两个 production manifest 实际生成后再勾选；下一步按 8.8 策展 Nemotron 两个独立 `.nemo` archive |

| 2026-09-05 | 完成 `E-NE-01`～`E-NE-03`；阶段进行中 | 两个 Nemotron selection 各自只安装一个未改名的根级 `.nemo` archive，与 worker 的 `glob("*.nemo")`+恰好一项合同一致；没有共享 archive，也不同时携带 safetensors/GGUF 变体 | 两模型真实 fixed-revision tree 精确分区；manifest-tool `120 passed` 及 offline/frozen/lock/Ruff/mypy/release-lock/architecture/diff 全门通过 | production selection、worker audit、tests；未提交 | 下一步按 8.9 审计 pyannote；真实 gated 条款未经用户明确确认，E-PY-01 和完整 manifest 保持阻塞，先检查多来源合同能力 |

| 2026-09-05 | pyannote 审计进行中；固定子文件公开读取失败 | 官方模型卡确认必须由用户接受条件并使用 token，且离线方式是复制整个 repository 后从本地路径加载；官方 gated tree 预览显示 `embedding/`、`plda/`、`segmentation/`。尝试读取固定 commit 的 config/组件 README 被浏览安全/访问边界拒绝，故不能审计嵌套来源、许可证或完整文件集合，也不采用标记为 unofficial 的镜像替代证据 | 官方页面检索成功；固定 commit 三个子文件 open 均返回 non-retryable safe-open error | 官方 Hugging Face 模型卡/tree 预览与失败输出；未提交 | E-PY-01～05 全部保持未勾选；未获用户真实条款接受确认前不传 acceptance flag。多来源 schema 不在缺少真实固定内容时臆造 |

| 2026-09-05 | 8.10 公开模型闭包审计；受限网络下载失败 | 为判定 Voxtral 两套同尺寸权重中的唯一运行格式，并核对 VibeVoice index 与 FunASR processor/tokenizer 引用，尝试下载三个仓库固定 revision 的 10 个小文件；受限执行环境无法解析 Hugging Face 域名，未产生可用审计文件，本次不计通过 | 10 个 `curl --fail --location` 均 exit 6：`Could not resolve host: huggingface.co` | 空的 `/tmp` 审阅目标路径与失败输出；未提交 | 按既定网络权限仅重试相同固定 commit 小文件；成功前不写 selection、不勾选 E-DIS 项 |

| 2026-09-05 | 8.10 固定内容下载部分成功；一次路径输入错误 | 网络权限下 Voxtral 四项、FunASR 三项及 VibeVoice config/index 下载成功；VibeVoice discovery tree 实际为 `preprocessor_config.json`，本次误请求不存在的 `processor_config.json` 返回 404，因此该项不计下载证据，也不把 404 解释为上游缺件 | 9 个固定 revision 小文件 exit 0；错误 VibeVoice URL exit 22/HTTP 404 | `/tmp` 固定内容审阅文件与失败输出；未提交 | 按真实 discovery tree 补取 `preprocessor_config.json`，随后解析 config/index/模型卡与唯一权重格式 |

| 2026-09-05 | 完成 8.3 通用 Transformers 闭包；阶段进行中 | 19 个公开 fixed-revision 模型中适用的 Transformers 条目已按 config、tokenizer、processor、generation、单一 runtime 权重、index 全 shard、auto_map/dynamic code 及 worker 直访资源完整策展；未使用的 TF/GGUF/原生重复权重与文档/评测资源显式排除。pyannote 的 pipeline/多来源闭包仍由 8.9 独立阻塞管理 | 各家族固定小文件与 worker 审计；19 份真实 discovery 报告；三项最新精确分区；selection `11 passed`、manifest-tool `120 passed`、offline/frozen `20 19` 及 B 全门通过 | production selection、tests、`/tmp` 审阅/报告；未提交 | E-DIS-01/02 仍等待实际 manifest；下一步复读 generate 完整 selection 合同及 8.9/8.11 依赖，确认是否存在不依赖 pyannote 的后续工作 |

| 2026-09-05 | 完成 `E-DIS-03`；阶段进行中 | 在完全离线、frozen 工具环境中分别登记 20 个模型的 manifest_available、worker_implemented、installable、enabled 与稳定阻塞原因。当前 bundle 缺失使全部 manifest/installable 为 false；16 个真实 worker 为 true，FireRed LLM、TurboCTC、Voxtral、VibeVoice 为 false；19 项 selection frozen，pyannote 尚未冻结。enabled 没有被用来推断其他状态 | `classscribe-model-manifest status` exit 0，`bundle=missing`，返回排序后的 20 项四状态；HF/Transformers offline flags 为 1 | 离线 status 输出与更新后的 8.15 表；未提交 | E-DIS-01/02 等实际 manifest 生成后再完成；四状态表届时以 verified bundle 结果更新 |

| 2026-09-05 | 阶段 E 阻塞；完整 selection fail-closed 已证实 | 19 个公开模型 selection、通用闭包与四状态审计完成后，pyannote 仍是唯一缺失 selection。未传 gated 接受标志、仅指向临时输出的 generate 探针在任何下载/写出前拒绝 19/20 输入；临时 bundle 目录不存在。阶段 F 的完整 bundle 进入条件因此未满足。另有目标机 `nvidia-smi` 无法连接 driver，后续 GPU/VRAM 健康矩阵不能执行 | generate 探针返回 `selection IDs differ from model registry`；`test ! -e` 证明无临时输出；`git diff --check` 通过。先前目标机 `nvidia-smi --query-gpu` exit 9 | 19 份公开 discovery、production selection、四状态表与 fail-closed 输出；未提交 | 解除条件一：用户确认已在官方 pyannote Community-1 页面接受条款并明确允许使用 `--accept-gated-repository pyannote/speaker-diarization-community-1`；解除条件二：提供 `nvidia-smi` 可用的目标 Fedora NVIDIA 环境。未满足前 E-PY、E-M01～21、真实健康矩阵、E 退出与阶段 F 均不得勾选 |

| 2026-09-05 | 阶段 E 外部状态复核；GPU 阻塞解除 | 自动续跑先从磁盘复读完整计划并核对工作树。目标机 NVIDIA 状态已由先前 driver 通信失败变为可用：Fedora 44、内核 7.1.12，RTX 4070、驱动 610.57.04、12,282 MiB、compute capability 8.9；据此完成 8.2 的目标 CPU/GPU 环境进入条件。默认本地 Hugging Face cache 中不存在 pyannote Community-1 snapshot，无法在不新增授权的情况下继续内容审计 | `nvidia-smi --query-gpu` exit 0；`/etc/os-release` 与 `uname -r` 读取成功；只读 cache presence 探针输出 absent；`git diff --check` 通过 | 本机环境输出与计划复核；未提交 | E 仍因用户未明确确认已接受 pyannote 上游条款而阻塞；一旦确认，先执行 E-PY-01 固定 revision discovery/多来源审计，再生成完整 bundle，并使用现已可用 GPU 按 8.12 验收 |

| 2026-09-05 | 阶段 E 阻塞复核；外部授权仍缺失 | 再次从磁盘核对 E-PY-01～05、E-M01～21、8.12 与阶段 F 入口；工作树没有新增 pyannote selection/bundle，默认本地模型 cache 仍无 Community-1 snapshot，也没有用户明确确认已接受上游条款。GPU 条件已解除，不再列为当前阻塞。完整生成必须先满足 gated 明示接受，不能通过直接 API、手工 cache 或 19 项子 bundle 绕过 | 计划/工作树只读审计 exit 0；cache presence 仍为 absent；`git diff --check` 通过 | 本轮阻塞审计记录；未提交 | 同一外部授权阻塞已连续复核且无可按阶段顺序继续的独立任务。等待用户完成官方条款并明确授权传入 gated acceptance flag；收到后从 E-PY-01 恢复，不重做已完成的 19 项公开 selection |

| 2026-09-05 | 阶段 E 恢复；收到 gated 明示授权 | 用户明确声明已接受 `pyannote/speaker-diarization-community-1` 上游条款，并授权发布工具传入对应 `--accept-gated-repository`。阶段 E 从 blocked 恢复进行中；该声明只解除发布期访问前置条件，不等同于实际仓库访问、闭包完整、多来源合同或健康通过，token 仍不得进入任何工件/日志 | 用户本轮明确确认；工作树与 `git diff --check` 基线通过 | 本条授权审计记录；未提交 | 重新执行 E-PY-01：仅对固定 model ID/revision 做 gated discovery；访问成功且无秘密泄漏后再勾选 8.2 gated 进入条件，并继续 E-PY-02～05 |

| 2026-09-05 | 阶段 E 进入条件全部满足；pyannote gated discovery 成功 | 使用用户明确授权的 repository 名称执行选择性 discovery，工具先交叉校验完整冻结输入，再访问固定 commit；resolved revision 与 registry 完全一致，私有 0600 报告记录 10 个文件、总计 33,695,573 bytes，未输出 token。此前网络、项目磁盘及目标 Fedora CPU/GPU 证据仍有效，因此完成 8.2 第二项，三个进入条件现均满足 | gated `discover --model-id pyannote_community_1 --accept-gated-repository …` exit 0，`model_count=1`；报告权限 0600、revision/tree/size 探针通过 | 私有 discovery report、用户授权与环境证据；未提交 | E-PY-01 仍等待实际生成/安装授权链；下一步下载固定 commit 的 config/组件说明，审计 E-PY-02～04 的离线引用、来源和许可证 |

| 2026-09-05 | E-PY-03 来源历史审计部分失败；独立 gated 边界确认 | 为匹配 Community-1 内嵌 segmentation 权重的原始 LFS SHA，查询两个可能来源仓库的 commit history；`pyannote/segmentation-3.0` 与 `pyannote/segmentation` 均返回 403。Community-1 的用户授权不扩张为这两个独立 repository 的条款接受，未重试、未绕过。embedding 来源仓库及 Community-1 自身历史查询成功 | 两个 segmentation commit-history 请求均 exit 22/HTTP 403；其余两个请求 exit 0；错误输出无 token | 固定来源审计请求与失败记录；未提交 | 保持 E-PY-03/04 未勾选；先从可访问的 embedding/Community 历史匹配来源。若 segmentation 来源必须访问独立 gated 仓库，再明确登记新的用户授权解除条件 |

| 2026-09-05 | E-PY-04 合同审计；source inventory 路径输入错误 | 检查 release license 映射时误读不存在的 `config/source-licenses.v1.json`，`jq` 失败；同一组合中的只读 `rg` 仍定位到 release/model-license 与 revision-lock 测试，但本命令不计 source inventory 证据 | `jq: Could not open file ...: No such file or directory`；后续 `rg` 有输出 | 审计失败输出；未提交 | 用 `rg --files` 定位真实 source inventory，再完成影响面设计；实施前仍不得修改合同或勾选 E-PY-04 |

| 2026-09-05 | 完成 `E-PY-03`～`E-PY-05`；阶段进行中 | 固定 Community README 将 embedding 明确指向 WeSpeaker 仓库；其当前固定 source file 与 Community 内嵌文件 size/SHA 不同且全部历史无 byte-exact match，故冻结为 `derived` 而非 `copied`。WeSpeaker 固定 commit/LFS SHA/size 与 CC-BY-4.0 独立登记；PLDA README 的作者归属与 Community 主许可证保留，segmentation 固定模型卡仅引用论文且未声明独立仓库。两个另行 gated segmentation 候选的 403 不被 Community 授权扩张。manifest/loader/generator/verifier/release/API/UI 多来源合同全门通过，未手工拼 cache | 固定 README/config、Community/WeSpeaker history 与 LFS evidence；A/B/C/D 当前全绿；gated selection 精确分区 | revision/license/source inventories、component-source 实现与既有失败记录；未提交 | E-PY-01/02 等待实际 20-manifest 生成、安装与真实 pyannote 离线 health；下一步执行 production 双重生成 |

| 2026-09-05 | production 双重生成失败；阶段 E 保持进行中 | 完整 20 模型生成在 `firered_asr2_llm` 的 16 文件 clean run 下载完成后 fail closed；selection 已包含 `Qwen2-7B-Instruct/model.safetensors.index.json` 及其 4 个同目录 shard，但 index 闭包校验把 JSON 内相对 shard 名错误地按仓库根路径比对，报告四个 shard 在 selection 外。生成命令 exit 2，正式 bundle 未写入，本次不计任何 production manifest 证据 | `generate --accept-gated-repository pyannote/speaker-diarization-community-1`：`weight index references shards outside selection: ['model-00001-of-00004.safetensors', 'model-00002-of-00004.safetensors', 'model-00003-of-00004.safetensors', 'model-00004-of-00004.safetensors']`；selection 行 68～72 证明实际包含带父目录路径的 index 与四 shard | 生产生成会话、精确 selection 与 fail-closed 输出；未提交 | 修复 generator 对嵌套 weight index 的 POSIX 相对路径解析并增加攻击/回归测试；该变更触及阶段 B 冻结生成合同，先标记 B 需回归，完整通过后才重新从 production 双 run 开始 |

| 2026-09-05 | production 20-model 双重生成成功；独立验收待继续 | 修复 B-31 并恢复阶段 B 后，从空私有工作区重新对 registry 全部 20 项执行两个 clean run；每项固定 revision、实际 payload、manifest bytes 及最终 bundle bytes 一致，生成器原子写出 20 个 manifest 与 bundle，并在命令内置离线复核中返回全部 `manifest_available=true`。disabled/未实现项仍按 registry/support 输出不可安装，不因 manifest 存在而提升状态 | 完整 `generate` exit 0，`model_count=20`、`status=verified`；bundle SHA `88a57122ea743a225cf8f8cb990bff1c7e477be1a3098d86fffea7249f3e0816`；Whisper manifest SHA 仍为 `2d805d29…16eba8`；pyannote manifest SHA `179c9160…a6a0e` | production `config/model-manifests/v1/` 原子写出与完整命令输出；未提交 | 立即运行独立 offline/frozen verifier，核对恰好 21 个 JSON 文件、成员实际 SHA、canonical bytes、pyannote 5 文件与 component provenance；全部通过后再勾选 E-M/E-DIS/manifest 相关任务并更新 8.15 |

| 2026-09-05 | 完成 `E-M01`～`E-M21`、`E-FR-06`、`E-QW-01`、`E-GR-01`、`E-DIS-01`～`E-DIS-02`；阶段进行中 | 独立 offline/frozen verifier 从正式 bundle 重新加载五方冻结输入并验证 canonical bytes、20 个唯一排序成员、成员实际 SHA、锁、selection 和四状态；目录恰好为 20 manifest 加 1 bundle。pyannote manifest 仅含审核的 5 个运行文件，并包含 WeSpeaker 固定 source revision/file SHA/size、`derived` 关系和独立许可证；token 未进入工件。FunASR/Whisper 保持 disabled，LLM/Voxtral/VibeVoice/TurboCTC 不可安装 | `verify --bundle config/model-manifests/v1/bundle.v1.json` 在 offline/frozen 环境 exit 0，`status=verified`、`model_count=20`；实际 bundle SHA `88a57122ea743a225cf8f8cb990bff1c7e477be1a3098d86fffea7249f3e0816`；21 个 JSON 均 0644；pyannote file_count=5/component_sources=1 | production manifests/bundle、独立 verifier 与 8.15 四状态表；未提交 | manifest 生成清单已闭合；E-PY-01/02 仍需真实授权安装与无网络运行。按 8.12 顺序先用 production bundle 重验 Whisper 下载→离线生命周期→重启，不以前一单模型 bundle 证据替代 |

| 2026-09-05 | 阶段 E 健康矩阵暂停；8.2 第一进入条件回退 | 在启动 8.12 第 1 项前发现 production 默认服务未加载/注入正式 bundle，故 C/D 状态已回退为需回归，8.2 的“A～D 已完成”不再成立。已生成并独立验证的 20 manifests/bundle 字节证据不受影响，但不得借助手工 fixture 注入继续冒充默认产品健康链 | runtime composition 只读审计；默认 service 没有 `manifest_bundle=`，而 service 在 bundle=None 时稳定报告 manifest unavailable | C/D 回归记录与本条 E gate 回退；未提交 | 先按 C/D 冻结合同完成默认 fail-closed bundle composition 及 source/installed tests，恢复 A～D 后再从 Whisper production bundle 安装继续；不重做已完成且未失效的双生成 |

| 2026-09-05 | 阶段 E 8.2 第一进入条件恢复 | 默认 product composition 已从正式 resource root fail-closed 加载/注入 production bundle；source 20 项、缺件/篡改拒绝、完整 API/worker consumer 与静态门通过，C/D 恢复已完成。A/B 原证据未受该 composition 修复影响，8.2 三项再次全部满足 | C/D 拆分回归：纯消费者 96、worker 14、API 13，定向 25，静态/architecture/diff 全绿 | runtime composition 与 C/D 完成记录；未提交 | 继续 8.12 第 1 项，必须走默认已验证 bundle，不使用受控一模型注入 |

| 2026-09-05 | 阶段 E GPU 环境再次失效；8.2 第三项回退 | 在开始 Whisper production 安装前复核工具链，`espeak-ng`、`ffmpeg`、`bwrap` 均存在，但同一目标机 `nvidia-smi` 从此前可用再次变为无法与 NVIDIA driver 通信。组合命令在该 exit 9 处停止，后续磁盘子命令未执行且不计证据。GPU 最终验收门重新 fail closed；不依赖 GPU 的 Whisper/FireRed CPU 项可继续独立验收 | `command -v` 三项成功；`nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader` exit 9；无后续 `df` 输出 | 目标机环境复核与失败输出；未提交 | 记录风险并继续 8.12 的 CPU 顺序项；GPU 路由/VRAM 模型在 driver 恢复前不得勾选或冒充 CPU 等价证据，阶段 E 最终退出保持阻塞 |

| 2026-09-05 | 8.12-1 Whisper production 首次安装与离线健康通过；重启待验 | 在 fresh XDG 下由默认 service 加载 20-member production bundle；先确认普通入口因 registry disabled 返回 `MODEL_INSTALL_BLOCKED`，再由验收 harness 仅从同一只读 bundle 取得 manifest，执行一次性预检、固定 revision 四文件下载、实际 SHA/size、frozen worker provisioning、无网络 Bubblewrap load→transcribe_batch→unload、原子 active 与 supply-chain 复验。未改变 disabled 策略 | bundle SHA `88a57122ea743a225cf8f8cb990bff1c7e477be1a3098d86fffea7249f3e0816`/20 项；manifest SHA `2d805d29c6ec3465a0824d2d87c121c3b16514856bdbdaee3d0b90750c16eba8`；revision `d90ca5fe260221311c53c58e660288d3deb8d356`；4 files/78,203,619 bytes；WAV 57,983 frames@16k mono SHA `46c25c55…6cc6`；health=true、backend `faster_whisper_cpu_int8`、runtime_offline=true、measured_vram=0；aggregate `2b0d289c…d44b` | production默认 service/downloader/manager/provisioner/Bubblewrap/worker 输出；保留私有安装树；未提交 | 本项尚不勾选：在独立新 Python/core 且 user+net namespace 无外网条件复用同一环境/安装树，禁止 downloader 与 provisioning，复验 bundle/active/files/aggregate/audit并取得第二次非空协议响应 |

| 2026-09-05 | 完成 8.12 第 1 项 `whisper_tiny_reference` | 在全新 Python/core 进程及独立 user+network namespace 中，外网连接探针失败；默认 service 重新验证 production bundle，manager 从同一安装树复验 active、4 文件、aggregate 与 supply-chain，provisioner 只 `resolve` 已冻结环境、不执行 sync/downloader；SandboxedModelInvoker 再次执行 Bubblewrap load→transcribe_batch→unload，返回协议身份/版本一致、非空文本与 segment | network probe `OSError`；bundle SHA `88a57122…e0816`/20；manifest SHA `2d805d29…16eba8`；active revision `d90ca5fe…d356`；aggregate `2b0d289c…d44b`；response ok、normalized `Class crime verify real speech recognition.`、1 segment、backend `faster_whisper_cpu_int8`；verify_model=true | 同一 fresh-XDG 安装树、独立 namespace 重启输出；未提交 | 8.12 第 1 项闭合；清理私有 Whisper 安装/音频/XDG 后，按顺序进入 FireRedVAD、FireRedLID CPU 辅助模型 |

| 2026-09-05 | 8.12-2 FireRedVAD 首次安装失败；临时 cache 配额耗尽 | production bundle 的 VAD 预检及固定四文件下载已开始，随后 frozen FireRed worker 首次 provisioning 真实解析/下载锁定依赖；在解压 `torch==2.10.0` 的 `libtorch_cuda.so` 时，错误地沿用容量较小的 `/tmp/classscribe-uv-cache`，触发 `Disk quota exceeded`。未进入 Bubblewrap/load/infer，不计健康通过；不能以 VAD CPU 路由为由删减 lock 中 CUDA wheel | uv sync exit 1：`failed to flush ... /tmp/classscribe-uv-cache/.../libtorch_cuda.so: Disk quota exceeded (os error 122)`；service/manager traceback止于 `WorkerEnvironmentProvisioner.ensure` | 真实 FireRed provisioning 失败输出；未提交 | 核对 manager 已清理失败候选 revision且无 active，盘点 `/tmp` cache；把 task-specific UV cache 改到项目临时根后从 production preflight/download 重试，同一失败 token 已单次消费不得复用 |

| 2026-09-05 | 8.12-2 FireRedVAD 第二次安装失败；锁定 worker 缺少 VAD runtime | 改用项目盘 task-specific cache 后，frozen FireRed worker 成功构建固定 Git dependency 并安装 65 个包，彻底越过 quota；真实无网络 Bubblewrap 随后在 `load` 阶段返回 `model_load_failed: isolated FireRed worker lacks the VAD runtime`，未进入 VAD infer。manager 再次按健康失败事务清理候选 revision；本次不计通过 | worker provisioning `Prepared 65 packages`、`Installed 65 packages`；实际 health exit 1，最终 `ClassScribeError: model load failed ... isolated FireRed worker lacks the VAD runtime` | production model/worker 真实输出；未提交 | 审计锁定 `fireredasr2s@4e7d9aaf…` 安装树的模块导出与 adapter import；若 worker capability/support 合同错误，先回退受影响已完成阶段并修复/回归，再重试 VAD，不跳到 LID 冒充顺序完成 |

| 2026-09-05 | 8.12-2 根因确认；E manifest/bundle 与 Whisper 证据回退 | 固定上游 checkout 的 `requirements.txt` 明确含 `kaldi_native_fbank==1.15`，VAD `audio_feat.py`、LID `feat.py` 和包级 ASR 初始化均直接 import；当前上游 pyproject 漏列且本地 worker 未补 direct dependency。修复必然改变 FireRed lock/registry/5 manifests/bundle，因此 E-M02～06、E-M21、E-FR-06、E-DIS-02 和旧 bundle 下 Whisper 第1项均按规则撤销，不删除旧成功记录 | direct import traceback与固定 source/requirements 审计；状态/checkbox 精确回退 | 上游 fixed checkout、本地 worker lock传播与计划更新；未提交 | 先更新/冻结依赖并完成 A～D 回归，再双生成新 production bundle；之后按新 SHA 重验 Whisper，再第三次执行 VAD，不在旧 bundle 上继续 LID |

| 2026-09-05 | FireRed 新 lock 下 production 20-model 双重生成成功；独立验证待执行 | 使用固定 `SOURCE_DATE_EPOCH=1788451200`、两个 clean payload run 和用户明确授权的 Community-1 gated 标志重新生成全部 20 项；生成器内置交叉验证返回 `status=verified`。新 worker-lock 只改变 5 个 FireRed manifest 及 bundle，另外 15 个 manifest（含 pyannote/Whisper）SHA 与上一批完全一致；嵌套 FireRed LLM index 的真实 16 文件双 run 再次闭合 | 完整联网 `generate` exit 0、`model_count=20`；bundle SHA `5cee79a2226e257596f7d66014b4ff3951c1f1f66485c1c08a4890c96e1fc11a`；FireRed AED/LLM/LID/Punc/VAD SHA 分别为 `b762acfb…5755b`、`1eb3e170…3879`、`1670bdef…edbc`、`98153f56…dd6e`、`e8067bf0…174a` | 新 production manifest/bundle 与完整生成输出；未提交 | 尚不恢复 B/E 清单：立即运行独立 offline/frozen verifier，核对目录恰好 21 JSON、实际 SHA/canonical bytes、五个 FireRed lock 字段及 pyannote component provenance |

| 2026-09-05 | 新 bundle 独立离线验证完成；恢复 `E-M02`～`E-M06`、`E-M21`、`E-FR-06`、`E-DIS-02` 与 manifest 阶段门 | 完全 offline/frozen 的独立 verifier 从正式 bundle 重新加载冻结输入，验证 20 个排序唯一成员、实际成员 SHA、canonical bytes、五个 FireRed manifest 的新 lock SHA和四状态；目录恰好 21 个 0644 JSON。pyannote 保持 5 个运行文件及 1 个 WeSpeaker `derived` source，固定 source revision/SHA/size/CC-BY-4.0 未变 | verifier exit 0、`status=verified`、`model_count=20`、bundle SHA `5cee79a2226e257596f7d66014b4ff3951c1f1f66485c1c08a4890c96e1fc11a`；目录闭包/权限/全 SHA 探针通过；五个 FireRed worker-lock 均为 `14543cb7…f445` | production bundle、独立 verifier、更新后的全长 8.15 SHA 表；未提交 | 下一步跑完整 B 工具与 A/C/D consumer/static 门；通过后恢复 A～D 和 8.2 第一项，再在新 bundle 下重验 Whisper |

| 2026-09-05 | 完成新 bundle 下 8.12 第1项 `whisper_tiny_reference`；E 8.2 第一项恢复 | fresh-XDG 首次链验证普通入口 disabled 阻止后，只从新只读 bundle 取得 manifest，完成固定四文件下载、hash、frozen provisioning、断网 Bubblewrap inference、active/audit。独立新 core + user/net namespace 随后禁止下载与 provisioning，再次复验并取得非空协议响应；A～D 全部恢复完成 | bundle `5cee79a2…fc11a`/20；manifest `2d805d29…6eba8`；revision `d90ca5fe…d356`；4 files/78,203,619 bytes；aggregate `2b0d289c…d44b`；WAV `46c25c55…6cc6`、57,983 frames；两次 CPU int8/runtime offline，重启 normalized `Class crime verify real speech recognition.`、1 segment | 默认 product chain、私有安装树与独立断网重启输出；未提交 | 清理可再生 Whisper 临时树；按 8.12 顺序继续 FireRedVAD、FireRedLID，GPU 进入条件仍因 driver 不可用未满足 |

| 2026-09-05 | 8.12-2 FireRedVAD 第三次安装失败；sandbox 缺当前 UID identity | 新 FireRed lock 环境已越过此前 `kaldi_native_fbank` 缺失并进入真实 Bubblewrap `load`；FireRedVAD 初始化随即因 sandbox 内 `getpwuid(1000)` 无记录而失败。manager 事务再次清理候选 revision、无 active，不能计 VAD 健康通过。该错误限定为隔离运行时 identity 合同，不是 payload/manifest/worker lock 漂移 | 新 bundle/manifest 固定下载后 health exit 1：`FireRedVAD initialization failed: KeyError: 'getpwuid(): uid not found: 1000'` | 第三次真实 VAD 安装 traceback 与清理后安装根；未提交 | 审计 WorkerSandbox 的 `/etc` 与 HOME/UID 映射，设计不泄漏主机用户名/家目录的最小 passwd/group 视图并补单测；若修改共享 sandbox，立即把 A/C 标记需回归并在第四次 VAD 前完成相关门 |

| 2026-09-05 | 8.12-2 FireRedVAD 第四次安装失败；synthetic HOME 仍不足 | 在 synthetic HOME 版 WorkerSandbox、新 bundle、新 fbank lock 和 0400 健康 WAV下重跑完整普通预检/固定下载/health；VAD 初始化仍直接报 `getpwuid(1000)`，说明调用者绕过 HOME 并查询 NSS。manager再次清理候选、无 active。此失败使共享 sandbox identity 合同和同命令下 Whisper证据再次回退 | 第四次 VAD install exit 1；`FireRedVAD initialization failed: KeyError: 'getpwuid(): uid not found: 1000'` | 第四次真实链、无残留 active 与 A/C回退；未提交 | 下载一个仅用于诊断的固定 VAD payload，直接在 Bubblewrap 内运行 uncaught from_pretrained以取得完整 traceback；之后实现最小 synthetic passwd/group，完成 A/C 回归再第五次重试 |

| 2026-09-05 | VAD traceback 诊断首次代码路径输入错误 | 为创建仅诊断的固定 payload，误读不存在的 `backend/classscribe/models/downloader.py`；符号检索命令因该路径 exit 2，未读取实现、未下载任何文件。随后用 `rg --files` 定位真实模块为 `models/download.py` | `rg`/`sed` 对不存在路径 exit 2；真实符号定位成功 | 只读诊断失败输出；未提交 | 从真实 download 模块使用 production downloader与同一 manifest创建私有诊断 payload，继续取得完整 sandbox traceback |

| 2026-09-05 | 8.12-2 FireRedVAD 第五次首次安装与离线健康通过；重启待验 | 最终 `HOME=/home/classscribe`、`USER=LOGNAME=classscribe` sandbox identity 下，production bundle VAD 固定四文件、frozen FireRed环境及无网络 Bubblewrap load→infer→unload 完成；工具回传因输出总量超过上下文被截断，未把不可见输出当作证据，改由 committed active、supply-chain 与数据库健康行交叉确认事务成功 | active revision `7990aaccc6b7aec1e527743bd30201f2c4a03b8c`；4 files/4,654,184 bytes；aggregate `52737a637920448785f9c86c5e39b759b429d9ba426eb398fd38dc0c5108bea7`；health `healthy=true`、detail `actual short-audio inference completed`、backend `firered_vad`、runtime_offline=true；WAV `bbeaef1d…9939f`/91,939 frames | 同一 fresh-XDG active/supply-chain/SQLite 安装树；未提交 | 本项尚不勾选：在独立新 core 及 user+net namespace 中禁止 downloader/provisioning，复验同一安装树并取得非空 VAD 协议响应；之后继续 FireRedLID |

| 2026-09-05 | VAD 提交状态只读诊断字段假设失败 | 首次审阅 supply-chain 时错误假设顶层存在 `model_id/revision/bundle_sha256/manifest_sha256/sources`，`jq` 因缺失字段输出 null/0；随后又查询数据库不存在的 `status` 与 bundle/manifest列，SQLite 在 prepare 阶段失败。本命令不计供应链/数据库证据 | `jq` 返回假设字段 null、source_count 0；SQLite `no such column: status` | 只读失败输出；未提交 | 读取完整 supply-chain 和 `PRAGMA table_info(model_installations)` 后按真实 schema 复核；真实字段显示 revision、aggregate、health/environment 与数据库 healthy 行一致 |

| 2026-09-05 | FireRedVAD 单模型验收闭合；8.12 第 2 项继续等待 LID | 独立新 core 进程置于 user+network namespace，强制 HF/Transformers/Datasets offline；默认 service 复验 20 项 bundle、active、四文件 aggregate 与审计，现有 frozen 环境只 resolve 不 sync，Bubblewrap 再次 load→VAD infer→unload，返回有意义的 1 个 segment | network probe `OSError`；bundle `5cee79a2…fc11a`/20；manifest `e8067bf0…174a`；revision `7990aacc…b8c`；aggregate `52737a63…bea7`；download=false、provisioning runner=false、verify_model=true、segment_count=1、backend `firered_vad` | 同一安装树独立离线重启输出；未提交 | VAD 已完整闭合但 8.12 第 2 项要求 VAD+LID，故不勾选；立即用同一 production bundle/最终 sandbox identity 执行 FireRedLID 首次安装与独立重启 |

| 2026-09-05 | 8.12-2 FireRedLID 首次安装与离线健康通过；重启待验 | 默认 production service 对可安装 enabled LID 完成预检、固定 revision 三文件下载、实际 SHA/size、复用 frozen FireRed 环境、无网络 Bubblewrap load→语言识别 infer→unload、原子 active 与 supply-chain 复验 | bundle `5cee79a2…fc11a`/20；manifest `1670bdef…edbc`；revision `1bb4d285c8456429385d9c0810300df4297bc11b`；3 files/3,550,105,508 bytes；aggregate `e513cd963b6911b1d928afcfcfe56dfae9bf9428f7bb9e30cfd5c476685c987b`；health=true/backend `firered_lid`/runtime_offline=true；预检 required 7,810,232,117、available 337,739,309,056 bytes | 同一 fresh-XDG active/supply-chain 与首次健康输出；未提交 | 本项尚不勾选：在独立新 core 及 user+net namespace 中禁止 downloader/provisioning，复验同一安装树并取得非空 LID 协议响应 |

| 2026-09-05 | 完成 8.12 第 2 项 FireRedVAD、FireRedLID CPU 辅助模型 | LID 在独立新 core/user+network namespace 中复验 bundle、active、三文件 aggregate 与审计；frozen 环境只 resolve 不 sync，Bubblewrap 再次 load→LID infer→unload并返回语言概率。结合上一条 VAD 的同等级证据，两个 CPU 辅助模型均完成首次安装和冷重启闭环 | LID network `OSError`；bundle `5cee79a2…fc11a`/20；manifest `1670bdef…edbc`；revision `1bb4d285…c11b`；aggregate `e513cd96…987b`；download=false、provisioning=false、verify=true；`language_probabilities.en=0.467`、1 segment、574.343 ms。VAD 同条件 1 segment、13.05 ms | 两个同一 fresh-XDG 安装树及各自独立断网重启输出；未提交 | 8.12 第 2 项勾选；下一步严格进入第 3 项 `qwen3_asr_0_6b`，GPU driver 风险在执行前重新复核，若不可用则按真实 worker 设备合同登记阻塞而不使用 CPU 冒充 |

| 2026-09-05 | 8.12 第 3 项 Qwen 入口阻塞；目标 GPU driver 不可用 | 复核 production manifest 确认 0.6B 声明 `dtype=bfloat16`、`runtime_backend=qwen_asr_vllm`；同一目标 Fedora 主机的 `nvidia-smi` 当前 exit 9，无法与 NVIDIA driver 通信。因此未启动 1,880,560,703-byte 下载或健康事务，也未用 CPU fallback 冒充 GPU load/infer/VRAM 证据 | manifest 实际字节读取成功；`nvidia-smi --query-gpu=name,memory.total,compute_cap,driver_version` exit 9 | production manifest 与目标机检查；未提交 | 保持第 3 项未勾选；从真实 registry/adapter/health 代码确认是否强制 GPU。若确认，则登记外部阻塞并按 8.12 规则评估继续不依赖该 GPU 项的后续 CPU 工作，最终 E 门仍 fail closed |

| 2026-09-05 | Qwen 设备合同只读审计路径输入失败 | 首次组合检索误用不存在的 `config/model-registry.v1.json` 和 `workers/qwen/classscribe_worker_qwen/adapter.py`；`rg` 与 `sed` 分别报路径不存在。其余真实 manifest、测试检索和 NVIDIA 检查输出不被算作这两个文件的证据 | `rg: config/model-registry.v1.json: No such file or directory`；`sed: can't read workers/qwen/classscribe_worker_qwen/adapter.py` | 只读失败输出；未提交 | 改读仓库真实 `config/model-registry.v1.yaml` 与 `workers/qwen/adapter.py`，再判定设备/CPU fallback 合同 |

| 2026-09-05 | A～D/Qwen bundle 精确回退；冻结 runtime backend 错误确认 | 真实 registry、worker依赖与完整 adapter 审计确认两个 Qwen ASR 条目唯一实现为官方 `qwen-asr` 的 Transformers `Qwen3ASRModel.from_pretrained`；load 与 inference 均报告 `qwen_asr_transformers`，worker lock 不含 vLLM。registry/manifest 的 `qwen_asr_vllm` 因而是错误供应链环境声明。adapter 的 `auto` 在 CUDA 不可用时明确使用 CPU/float32，故可继续 CPU 健康但不能冒充 GPU/VRAM | `workers/qwen/adapter.py` load lines 249～287、response line 441；`workers/qwen/pyproject.toml`/`uv.lock` 无 vLLM；registry 两条均错标；目标 `nvidia-smi` 仍 exit 9 | 冻结合同影响审计与精确 checkbox/status 回退；未提交 | 修正两个 registry runtime backend 为 `qwen_asr_transformers`，增加 registry↔adapter 防回归测试；完成 A 定向门后重新完整 production 双生成与 B 独立验证，再恢复 C/D/Whisper，最后重启第 3 项 Qwen CPU 真实验收 |

| 2026-09-05 | Qwen A 定向回归部分通过；两条门禁命令输入错误 | registry 两条已改为真实 `qwen_asr_transformers`，adapter测试同时绑定 registry backend并断言无CUDA时 CPU/float32；行为 6 项及严格 mypy通过。随后误把 YAML 传给只解析 Python 的 Ruff，得到467个伪语法诊断；又误写不存在的 `tests/contracts/test_config_schemas.py`，pytest exit 4且零收集。这两条不计失败代码证据也不计门禁通过 | 行为 `6 passed`；mypy 1 source通过；错误 Ruff exit 1/467 diagnostics；错误 pytest exit 4/no tests ran；diff check通过 | registry/test工作树与完整输出；未提交 | Ruff只检查修改的Python；registry由真实 `test_model_registry.py`/schema loader验证，manifest合同使用存在的 `tests/contracts/test_model_manifest_schemas.py`，并确认旧bundle fail-closed |

| 2026-09-05 | Qwen A registry/schema 门通过；旧 bundle 探针命令转义失败 | 修正后的 Ruff 与 registry/schema 18项全通过；但用于证明旧bundle被拒绝的 `python -c` 将转义换行作为字面字符交给解释器，SyntaxError exit 1，loader根本未调用，不能算fail-closed证据 | Ruff通过；pytest `18 passed`；探针 `SyntaxError: unexpected character after line continuation character` | 定向门与失败输出；未提交 | 用无多行转义的 `pytest.raises(ValueError, load_builtin_manifest_bundle, ...)` 单行探针重新调用真实loader并断言environment drift |

| 2026-09-05 | Qwen旧bundle fail-closed确认；生成说明路径输入失败 | 单行真实loader探针因 `qwen3_asr_0_6b` environment drift 捕获预期 ValueError并exit 0；production双生成前盘点单轮约69.8GB、项目盘约312GiB可用、旧生成临时目录为空。随后误读不存在的 `tools/model-manifest/README.md`，`sed` exit 2；CLI help与进度文档内冻结命令仍成功读取 | loader输出 `model manifest environment differs from registry: qwen3_asr_0_6b`；空间/空目录探针通过；错误README `No such file or directory`；generate help exit 0 | fail-closed与本地空间/CLI输出；未提交 | 删除已确认0-byte的精确私有临时目录；使用进度文档B-29冻结接口、固定epoch及用户授权启动完整production generate |

| 2026-09-05 | 旧生成临时目录清理首次失败；0-byte锁文件已定位 | 先前 `du=0` 被错误解释为空目录；精确 `rmdir` 返回Directory not empty，串联的不存在断言未执行。只读find随后确认唯一内容是生成器遗留的0-byte、mode 0666、项目内 `uv-de488ad5dc35fee4.lock`，无payload、凭证或模型字节 | `rmdir` exit 1；`find`恰好列出目录自身与单一0-byte普通lock；`du -a`均0 | 精确私有临时路径与只读清单；未提交 | 用patch删除唯一0-byte lock后只对该精确目录执行rmdir并验证不存在；不使用递归或宽路径删除 |

| 2026-09-05 | 旧生成临时目录精确清理完成；Qwen production再生成就绪 | 仅删除已审计的0-byte uv lock，再对其唯一空父目录执行rmdir；不存在断言通过。单轮旧manifest估计总量69,817,267,873 bytes，项目盘约312GiB可用，可承载生成器两次clean payload | 精确`rmdir`与`test ! -e` exit 0；未使用递归删除 | 清理输出与B-29冻结命令；未提交 | 以 `SOURCE_DATE_EPOCH=1788451200`、完整五方输入及用户授权的Community-1 gated flag启动production generate |

| 2026-09-05 | Qwen修正后的完整production生成被权限审查拒绝 | 按B-29冻结接口、固定epoch与用户Community-1授权请求联网生成；权限审查判定该命令将执行约140GB双clean传输并直接重写20个manifest加bundle，现有gated条款授权未明确覆盖这一广泛覆盖副作用，因此在进程创建前拒绝。没有网络请求、下载、临时payload或production文件写入；不得绕过审查 | `exec_command`未启动，返回`Rejected`：需用户在获知风险后明确批准完整下载与覆盖；旧bundle仍按loader fail-closed | 权限审查输出与未变production工件；未提交 | 外部权限解除条件：用户明确批准联网下载约140GB并以重新验证的20模型生成结果覆盖现有21个production JSON；获批后原样重试，不改走间接/绕过路径 |

| 2026-09-05 | Qwen冻结合同示例同步；manifest-tool回归命令工作目录错误 | 唯一进度文档3.5的Qwen示例已从已确认错误的`qwen_asr_vllm`同步为adapter真实`qwen_asr_transformers`，避免计划自身继续传播过时合同。随后从仓库根以`--project tools/model-manifest`调用pytest，pytest仍选择根测试配置并载入根`tests/conftest.py`，因工具环境不含core包而在收集前失败；该命令不计工具回归证据。同批根Ruff通过 | 错误工具pytest：`ModuleNotFoundError: No module named 'classscribe'`；根`uv run ruff check .`通过 | 计划合同示例、错误命令和Ruff输出；未提交 | 在`tools/model-manifest`工作目录用其frozen环境重跑完整pytest；若本地fake-Hub loopback被sandbox拒绝，保留失败并在已批准外层边界原样重跑 |

| 2026-09-05 | manifest-tool受限沙箱回归部分通过；静态命令再次使用错误布局 | 在工具工作目录的完整pytest已正确收集，142项通过，7个fake-Hub测试均在fixture创建`127.0.0.1` socket时被沙箱`EPERM`阻止，未执行各自测试体；这7项不计通过。并行锁检查通过。mypy与Ruff命令误假设工具采用`src/`布局，实际包位于`classscribe_manifest_tool/`，分别因路径不存在exit 2/1，不计静态证据 | pytest `142 passed, 7 errors`，7项同为`PermissionError: [Errno 1] Operation not permitted`；lock `Resolved 33 packages`；错误mypy/Ruff均报告`src`不存在 | 工具完整收集、sandbox错误、目录盘点与lock输出；未提交 | 在允许loopback的外层边界原样重跑完整pytest；以`classscribe_manifest_tool tests`为真实路径重跑严格mypy和Ruff |

| 2026-09-05 | Qwen修正后不依赖生成的本地门禁通过；production工件仍待重生成 | 在允许仅本机loopback的外层边界，manifest-tool fixed-revision/gated/redirect/短写/超长攻击矩阵与其余测试全部通过；工具真实包路径的Ruff、严格mypy和frozen lock均通过。registry测试新增同时约束0.6B与1.7B的backend集合，和adapter load返回值测试互补；旧两个Qwen manifest仍保留旧值并由loader拒绝，未手改或冒充新生成结果 | manifest-tool `149 passed in 4.16s`；lock `33 packages`；tool mypy `19 source files`、Ruff通过；registry `5 passed`；修改Python Ruff、`git diff --check`通过 | Qwen registry/tests、工具完整本地门输出；未提交 | 这些证据不能恢复B：仍须获得明确授权后执行约140GB完整双生成、独立offline/frozen verifier，再按依赖顺序恢复A～D和新bundle下Whisper真实链 |

| 2026-09-05 | Qwen worker组合沙箱运行未取得结果；架构测试路径错误复发 | 三个worker集成文件在受限沙箱运行30秒未返回任何pytest输出，工具交回后台结果但本次编排遗漏保存session ID；随后只读进程盘点确认无残留精确pytest进程，因此不能恢复、终止或把该运行算作任何证据。并行严格mypy全仓通过；架构命令再次误用历史不存在路径`tests/contracts/test_architecture_boundaries.py`，pytest exit 4，重复了本文件已记录过的同类路径错误 | worker组合：30秒无输出/无可恢复session/之后无匹配进程；mypy `224 source files`；架构错误exit 4/no tests ran；真实路径重新定位为`tests/unit/test_architecture.py`及`scripts/check_architecture.py` | 沙箱运行、进程盘点、真实路径检索；未提交 | 在外层边界重跑同一worker组合；运行真实architecture test和script，后续异步编排必须保留session_id再返回摘要 |

| 2026-09-05 | Qwen修正的可执行本地回归闭合；等待production生成授权 | 在外层普通用户边界重跑Qwen所在的streaming/body-ASR/postprocess三组原集成文件，全部通过；真实architecture pytest及静态检查脚本通过。结合registry/schema、成对backend断言、adapter返回值与CPU/float32参数、全仓Ruff/mypy、manifest-tool全量及旧bundle fail-closed，Qwen代码/冻结registry修正已有完整本地证据，但任何production manifest仍未被手工修改 | worker集成 `16 passed in 0.47s`；architecture test `1 passed`、script `OK`；全仓mypy `224 source files`；`git diff --check`通过 | Qwen adapter/registry/tests和本地全门；未提交 | 唯一下一步是获明确授权后按冻结B-29接口执行约140GB双clean下载并覆盖21个production JSON；完成独立verifier前A～D与E-M08/09/21保持需回归/未勾选 |

| 2026-09-05 | 第三轮授权阻塞复核中的loader探针参数错误 | 重新复读计划并核对生成进程、空间与旧工件时，单行loader探针只传registry，遗漏必需的bundle path和licenses，函数入口即以`TypeError`拒绝；没有读取bundle成员，因此该命令不计旧bundle fail-closed证据。同期空间和工件SHA只读结果有效 | 错误探针：`load_builtin_manifest_bundle() missing 2 required positional arguments: 'registry' and 'licenses'`；可用空间334,178,562,048 bytes；旧bundle/Qwen SHA仍为`5cee79a2…fc11a`、`f7b5852c…053d`、`b30c546c…b00b` | 复读、只读空间/工件输出与参数失败；未提交 | 按真实`(path, registry, licenses)`签名重跑loader，并用不自匹配的进程检索确认没有生成会话；取得证据后再完成连续阻塞审计 |

| 2026-09-05 | 阶段E与持续目标正式标记外部授权阻塞 | 第三次连续goal turn复核同一条件：没有用户对约140GB双clean下载及覆盖21个production JSON的明确授权，没有运行中的generator；真实loader仍因Qwen environment drift拒绝旧bundle。所有不依赖重生成的Qwen修正、tool/worker/static/architecture门均已闭合，E-M08/09/21、A～D回归、Whisper新bundle复验和F均依赖生成输出，无法继续有意义推进且不得绕过审批 | 正确loader探针：`ValueError: model manifest environment differs from registry: qwen3_asr_0_6b`；generator进程检索无输出；旧工件SHA未变；前序tool `149 passed`、worker `16 passed`、mypy 224、Ruff/architecture/diff通过 | 唯一计划、当前进程/工件/loader状态与连续三轮阻塞审计；未提交 | 解除条件：用户明确授权联网下载约140GB，并允许验证后覆盖`config/model-manifests/v1`现有20个manifest及bundle共21个production JSON；收到授权后恢复目标并原样执行B-29生成，不重开已闭合本地工作 |

| 2026-09-05 | 完整production生成授权已明确；阶段E恢复进行中 | 用户逐字明确授权联网下载约140GB的两次clean generation，并允许验证通过后覆盖`config/model-manifests/v1`现有20个manifest和bundle共21个production JSON；此前权限解除条件完全满足。复读B-23～29、E-QW/E-M/8.12及退出门后确认必须原样执行完整生成，不缩小为Qwen单项或手工工件。空间复核发现默认`/tmp`仅约10GB、项目盘约333GB，因此使用项目内0700专用`TMPDIR`承载生成器的私有临时工作区，不复用运行时模型cache | 用户本轮明确授权；B-29接口复读；`df -B1`项目盘333,636,431,872 bytes可用、`/tmp`10,157,498,368 bytes；生成器`tempfile.mkdtemp`遵循`TMPDIR`且内部目录0700/退出清理 | 用户授权、计划与生成器实现/空间审计；未提交 | 创建单一项目内0700临时根，以固定`SOURCE_DATE_EPOCH=1788451200`和Community-1 gated flag启动完整generate；持续轮询，失败立即记录，成功后先独立offline/frozen verify再更新E-M08/09/21 |

| 2026-09-05 | production生成运行中；一次轮询包装语法失败 | 获授权的B-29完整生成已在持续session 81982运行；首个18文件模型双run完成，下一4文件模型第一轮完成并进入第二轮。一次只读轮询的JavaScript对象键误写多余引号，调用在解析阶段以`SyntaxError: Unexpected string`失败，没有执行`write_stdin`、没有向子进程发送字符，也未改变/终止生成 | 生成session此前持续返回进度且无失败；错误轮询在wrapper解析前退出、wall time 0 | 同一生成session与观察层错误；未提交 | 不重启生成；修正参数语法并继续轮询session 81982，最终结果只以该session明确exit code为准 |

| 2026-09-05 | Qwen修正后的production 20模型双重生成成功；独立验证待执行 | 使用用户明确授权、固定`SOURCE_DATE_EPOCH=1788451200`、完整五方输入、Community-1 gated flag和项目盘0700私有临时根，对全部20模型完成两个clean run；每项fixed revision、实际payload、manifest bytes及最终bundle bytes一致，命令内置验证返回全部manifest可用。原子覆盖仅在双run闭合后发生；Qwen两个ASR environment现为真实`qwen_asr_transformers`。除这两个Qwen manifest与bundle外，其余18个manifest SHA保持不变 | 完整联网generate session 81982 exit 0、`status=verified`、`model_count=20`；bundle SHA `08a44567715a7726f5cc699c106931c517d58645593ee3e82b4637ca2d7b99b3`；Qwen 0.6B `50f7366f9206e96a9d5f66000418492601eb7af235bfd01f8146813800ffbe0e`、1.7B `a56f3fcc7b6474f4853da296d3b8fba1b7a3ba9dedafcf85f8a790427f935f49` | 新production 20 manifests/bundle、完整生成输出；未提交 | 暂不勾选E-QW-01/E-M08/E-M09/E-M21或恢复B；立即在完全offline/frozen工具环境运行独立verifier，并核对目录恰好21 JSON、canonical/实际SHA、Qwen environment及pyannote provenance |

| 2026-09-05 | 恢复`E-QW-01`、`E-M08`、`E-M09`、`E-M21`及manifest阶段门；B全回归待执行 | 独立于生成进程的offline/frozen verifier从正式bundle重新加载全部冻结输入，验证canonical bytes、20个排序唯一成员、成员实际SHA、selection、registry/revision/license/worker-lock和四状态。目录恰好21个普通0644 JSON；两个Qwen environment均为`qwen_asr_transformers`并绑定同一真实qwen lock。pyannote保持5个运行文件和1个WeSpeaker `derived` component source，无token或本机路径进入工件 | 独立verify exit 0、`status=verified`、`model_count=20`、bundle SHA `08a44567715a7726f5cc699c106931c517d58645593ee3e82b4637ca2d7b99b3`；目录闭包/权限、Qwen environment、pyannote provenance探针全通过 | 新production bundle、独立verifier、更新后的8.15 Qwen行；未提交 | E生成清单重新闭合；先清理生成包装临时根并运行完整B工具/lock/静态/release/architecture门，全部通过才恢复B，再按A→C→D依赖顺序回归 |

| 2026-09-05 | production生成私有临时根精确清理完成 | 生成器两个clean workspace均已由其生命周期删除；项目内包装根盘点仅剩一个0-byte、普通0666 uv锁文件，无payload、cache、凭证或模型字节。用patch删除该唯一文件后仅对已知空父目录执行`rmdir`，成功释放包装路径；未使用递归或宽目标删除 | `find`唯一项`uv-de488ad5dc35fee4.lock f 666 0`；精确patch删除与`rmdir` exit 0 | 私有临时根清单与清理输出；未提交 | 运行阶段B完整manifest-tool、frozen lock、Ruff/mypy、release-check、architecture及diff门；通过前B保持需回归 |

| 2026-09-05 | 8.12-3 Qwen执行前GPU复核；首次组合命令exit被覆盖 | 首次把`nvidia-smi`、空间与目录盘点放在同一shell命令中顺序执行；NVIDIA-SMI虽输出driver通信失败，但整条命令因最后的只读`find`成功而exit 0，故该组合exit不能作为GPU状态证据。随后把`nvidia-smi`单独原样运行，取得真实exit 9；约333.5GB可用空间的独立输出仍有效 | 首次组合输出含driver失败但最终exit 0；独立`nvidia-smi --query-gpu=…` exit 9 | 目标Fedora GPU探针与命令边界纠正；未提交 | GPU/VRAM子矩阵继续阻塞且不得用CPU冒充；按adapter已冻结的`auto→CPU/float32`合同继续Qwen3-ASR-0.6B CPU首次安装及断网重启，最终E门仍等待GPU恢复 |

| 2026-09-05 | 8.12-3 Qwen3-ASR-0.6B首次安装与CPU离线健康通过；重启待验 | 默认service从新production bundle对enabled/installable模型完成普通预检、固定revision八文件下载和实际SHA/size校验；新建lock-addressed frozen Qwen环境后，最终synthetic identity Bubblewrap在无网络运行时完成load→ASR infer→unload，原子active、aggregate与supply-chain manifest复验通过。adapter按冻结设备合同在不可用CUDA下落到CPU，backend为真实`qwen_asr_transformers`，不计GPU验收 | bundle `08a44567…9b3`/20；manifest `50f7366f…be0e`；revision `5eb144179a02acc5e5ba31e748d22b0cf3e303b0`；8 files/1,880,560,703 bytes；aggregate `94ca9503…b8db`；health=true/runtime_offline=true/backend `qwen_asr_transformers`；CPU/VRAM 0；preflight required 4,137,233,546、available 333,536,362,496 bytes；WAV `bbeaef1d…9939f`/91,939 frames | 阶段E私有XDG active/supply-chain、downloader/provisioner/Bubblewrap/worker输出；未提交 | 本项尚不勾选：在独立新core及user+network namespace中禁止downloader/provisioning，复验同一active/files/audit/aggregate并取得第二次非空Qwen ASR响应；GPU子矩阵仍阻塞 |

| 2026-09-05 | 完成8.12第3项Qwen3-ASR-0.6B CPU真实验收 | 独立新core/user+network namespace复验同一20项bundle、active八文件、aggregate与供应链；frozen Qwen环境直接复用且runner未调用，第二次Bubblewrap load→ASR infer→unload返回有意义的非空转写。load明确offline=true，真实backend与新manifest一致；CPU结果不替代仍缺失的GPU/VRAM证据 | network `OSError`；bundle `08a44567…9b3`；manifest `50f7366f…be0e`；revision `5eb14417…03b0`；aggregate `94ca9503…b8db`；download=false、runner=false、verify=true；normalized `Classify, verify, real speech recognition and language identification.`、1 segment、inference 1275.437 ms；CPU/VRAM 0 | 同一阶段E私有XDG安装树与独立断网重启输出；未提交 | 8.12第3项勾选；GPU风险保持阻塞，下一步严格进入第4项中文、日语、英语三个课堂primary，先从registry/profile解析实际model ID和语言路由后逐项执行 |

| 2026-09-05 | 8.12-4中文primary FireRedASR2-AED首次安装与CPU离线健康通过；重启待验 | classroom.zh profile首位模型经默认bundle-only入口完成预检、固定revision四文件下载、实际SHA/size、复用frozen FireRed环境、最终synthetic identity Bubblewrap无网络load→中文ASR infer→unload、原子active及供应链复验。adapter确认CUDA不可用时`use_gpu=false/use_half=false`，本次为CPU真实路由，不计GPU证据 | bundle `08a44567…9b3`；manifest `b762acfb…755b`；revision `2304afed56eacfee6256dee5937ed22ffa0b64ec`；4 files/4,731,890,696 bytes；aggregate `f300b3e4…c349`；health=true/runtime_offline=true/backend `fireredasr2s_aed`；CPU/VRAM 0；中文WAV `52a11ff3…899f`/66,564 frames；preflight required 10,410,159,531、available 331,851,993,088 bytes | 阶段E私有XDG active/supply-chain与真实worker输出；未提交 | 中文primary尚不完成：在独立新core/user+network namespace禁止下载/provisioning，复验同一active与第二次非空协议推理；通过后才进入日语primary |

| 2026-09-05 | 完成8.12-4中文primary FireRedASR2-AED CPU真实验收 | 独立新core/user+network namespace确认外网不可达，不调用downloader且fail-fast provisioning runner未触发；同一active四文件、aggregate、manifest与supply-chain复验后，第二次Bubblewrap load→中文ASR infer→unload返回与健康文本语义一致的非空转写。load明确offline=true，CPU结果不替代GPU证据 | network `OSError`；bundle `08a44567…9b3`；manifest `b762acfb…755b`；revision `2304afed…0b64ec`；aggregate `f300b3e4…c349`；download=false、runner=false、verify=true；normalized `今天我们学习光合作用`、1 segment、inference 1296.719 ms；CPU/VRAM 0 | 同一阶段E私有XDG安装树及独立断网重启输出；未提交 | 中文primary闭合；8.12第4项尚不勾选，严格进入日语primary `granite_speech_4_1_2b` |

| 2026-09-05 | 8.12-4日语primary Granite首次真实load失败；A～D与Granite工件回退 | 固定revision候选四个大权重分片等实际下载完成，frozen Granite环境按当前lock成功创建；Bubblewrap load在`AutoProcessor`实例化`GraniteSpeechFeatureExtractor`时明确失败，因为worker direct dependencies/lock没有`torchaudio`。manager按失败合同删除整个staging候选，正式revisions为空、无active，未影响其他模型。该依赖修复会改变Granite lock、registry两个Granite条目、两个manifest与bundle，故立即把A～D、E-M07/M15/M21及manifest/verifier阶段门标记需回归/未完成 | install session exit 1；`MODEL_HEALTH_CHECK_FAILED`→`model_load_failed`→`ImportError: GraniteSpeechFeatureExtractor requires the torchaudio library`；清理探针：Granite `revisions/`空、`.staging/`空；旧lock `47bbe1ee…b45`、旧manifest `b1c1b119…269b`、bundle `08a44567…9b3` | 真实默认service/manager/downloader/provisioner/Bubblewrap traceback与清理盘点；未提交 | 在Granite pyproject加入与解析Torch匹配的直接torchaudio依赖，重锁并新增防漂移测试；真实import/processor/load/infer通过后同步registry lock SHA。production JSON禁止手改，须重新完整双生成/独立验证；额外约140GB再生成是否仍在原授权范围需在执行前审查 |

| 2026-09-05 | Granite首次torchaudio固定版本重锁失败；兼容合同修正 | 首次假设应与已解析`torch==2.14.0`同号，直接固定`torchaudio==2.14.0`；resolver证明PyPI无该版本，lock未更新，不能计依赖修复。随后核对TorchAudio官方兼容矩阵：2.11采用stable ABI，支持PyTorch 2.11及未来版本且支持Python 3.12；因此保持既有Torch 2.14解析并把direct dependency改为真实存在的`torchaudio==2.11.0`，测试同步约束该组合 | `uv lock --project workers/granite` exit 1：`there is no version of torchaudio==2.14.0`；官方安装矩阵显示TorchAudio 2.11↔PyTorch 2.11+、Python 3.10～3.14 | resolver失败输出、官方TorchAudio installation/compatibility matrix、pyproject/test修正；未提交 | 以2.11.0重锁；若解析通过，创建新lock-addressed环境并先真实import/AutoProcessor，再重跑完整Granite安装健康；A～D保持需回归 |

| 2026-09-05 | Granite TorchAudio frozen dependency已解析；真实环境待验 | 依据官方stable-ABI合同，Granite worker直接固定`torchaudio==2.11.0`并保持已解析`torch==2.14.0`；新lock为68包，registry的Granite 4.1与TurboCTC两个共享worker条目同步实际lock SHA。新增回归同时约束pyproject direct pin、lock中Torch/TorchAudio版本、worker根依赖及requires-dist，防止未来遗漏。production两个Granite manifest仍是旧lock，loader应继续fail closed，未手改JSON | `uv lock --project workers/granite`：Resolved 68/Added torchaudio 2.11.0；lock SHA `6851d5ef4d39956290e5207530db80e9390e68bf1ed1c5d17dcb80b37f4483af`；`uv lock --check`通过；新增测试`1 passed`；Ruff通过 | Granite pyproject/uv.lock、registry、worker-environment test；未提交 | 创建新lock-addressed Granite环境，真实import torch/torchaudio/GraniteSpeechFeatureExtractor；再确认旧bundle按environment drift拒绝。A～D保持需回归，production JSON等待重新双生成 |

| 2026-09-05 | Granite真实诊断harness首次静态失败；未下载 | 为在production重生成前验证新worker环境，新增只使用旧manifest payload身份但明确`diagnostic_only`、不激活的临时真实健康脚本。首次Ruff在下载前发现类级RPC调用记录未标`ClassVar`，exit 1；随后因shell使用顺序分隔而执行的只读manifest盘点有效，但不能把整个组合算作诊断通过 | Ruff `RUF012 Mutable default value for class attribute`；只读盘点14 files/4,636,316,501 bytes、旧manifest lock `47bbe1ee…b45` | 临时harness静态输出；未提交 | 加`ClassVar`后先单独重跑Ruff；通过才允许固定payload诊断下载与新lock环境的Bubblewrap CPU load/infer/unload |

| 2026-09-05 | Granite新TorchAudio环境真实诊断生命周期通过；不计production验收 | 新lock-addressed环境真实import通过后，使用旧manifest中未变化的固定repository/revision/file identity在项目内0700临时目录重新下载14文件并逐字节复核；以新registry entry和新环境执行最终synthetic identity下的CPU Bubblewrap load→日语ASR infer→unload，成功越过原`torchaudio`缺失点。响应符合协议且非空，但合成日语的转写准确性较差，故本记录只证明依赖与生命周期修复，不勾选Granite真实production验收。临时4.4GiB payload在成功后自动清理并验证不存在 | diagnostic exit 0；14 files/4,636,316,501 bytes；revision `de575db64086f84fdc79da4932d1076e965bc546`；旧manifest SHA `b1c1b119…269b`；新worker env `6851d5ef…83af`；backend `transformers_granite_speech`、runtime_offline=true、lifecycle完整、1 segment；清理与`git diff --check`通过 | 临时诊断harness、新lock环境、真实worker输出及清理探针；未提交 | 跑worker-environment/registry/Granite adapter及静态定向门；随后唯一production路径是重新双生成并独立验证两个Granite manifest与bundle，未完成前loader继续fail closed、A～D不恢复 |

| 2026-09-05 | Granite依赖修复本地可执行门闭合；等待production再生成授权 | 新lock环境真实import与不激活诊断生命周期通过后，Granite worker environment、registry和body-ASR adapter定向回归全部通过；严格静态与lock门无问题。旧production bundle由真实loader精确拒绝`granite_speech_4_1_2b` environment drift，证明不能继续默认service或手改production JSON。再次按B-29完整生成将新增约140GB两次clean传输并再次覆盖21个JSON，累计超出用户上一轮所描述的一次操作，执行前需要新的明确授权 | 定向pytest `21 passed in 1.54s`；Ruff全仓、严格mypy `224 source files`、Granite lock 68 packages、`git diff --check`全通过；loader探针exit 0并捕获预期`ValueError model manifest environment differs from registry: granite_speech_4_1_2b` | Granite lock/registry/tests、真实诊断与本地门输出；未提交 | 解除条件：用户明确授权再一次约140GB完整双clean generation，并允许独立验证后再次覆盖现有20 manifests+bundle共21个production JSON；获批后原样运行，不用旧manifest或局部手改绕过 |

| 2026-09-05 | Granite风险/进度计划更新补丁首次失败 | 尝试在同一`apply_patch`调用中分别追加阶段E进度并更新风险表，却重复声明同一目标文件；patch验证以`multiple operations target`拒绝，整体未写入，没有部分状态或风险行改变 | `apply_patch` verification failed；只读上下文未改变 | 计划编辑失败输出；未提交 | 合并为单一Update File多hunk补丁并重试；完成后才向用户请求新增生成授权 |

| 2026-09-05 | 第二轮新增生成授权阻塞审计；全部不依赖生成的工具门闭合 | 重新读取唯一计划与当前工作树，确认没有可恢复的generator进程；Granite新lock为`6851d5ef…83af`，两个旧Granite manifest及bundle SHA保持不变并由loader fail closed，项目盘约327GB可用。manifest-tool完整fake-Hub fixed-revision/LFS/redirect/短写/超长/gated攻击矩阵及静态/锁再次全绿，说明剩余缺口不是工具实现或本地环境，而是新的完整生成操作授权 | generator进程检索无输出；tool `149 passed in 4.22s`；lock 33 packages、Ruff、严格mypy `19 source files`、architecture script、`git diff --check`全通过；可用327,163,199,488 bytes；旧bundle `08a44567…9b3` | 唯一计划、进程/空间/工件SHA与完整工具门；未提交 | 同一解除条件第二次连续成立：需用户明确授权新增一次约140GB两次clean generation及验证后再次覆盖21个production JSON；本轮不把工具绿灯冒充B完成，也不启动未授权下载 |

| 2026-09-05 | 第三轮新增生成授权阻塞审计；持续目标正式阻塞 | 第三次连续goal turn复核同一外部条件：用户尚未授权Granite修复后的新增一次约140GB双clean generation及再次覆盖21个production JSON；没有运行中generator。新Granite lock、真实import/诊断生命周期、定向/工具/静态门均已闭合，但正式bundle仍冻结旧lock并由loader精确拒绝，A～D、日语/英语primary及后续E/F无法在不绕过供应链合同的情况下继续 | generator进程检索无输出；lock `6851d5ef…83af`；旧Granite manifests `b1c1b119…269b`/`b232d135…8374`、bundle `08a44567…9b3`；loader `ValueError model manifest environment differs from registry: granite_speech_4_1_2b`；前序tool149/定向21/静态全绿 | 唯一计划、当前进程/工件/loader状态与连续三轮阻塞审计；未提交 | 解除条件不变：用户明确授权新增一次约140GB完整双clean generation，并允许独立验证通过后再次覆盖20 manifests+bundle共21个production JSON；收到授权后恢复目标并原样执行，不重开已闭合本地工作 |
| 2026-09-05 | 新增完整production再生成授权已解除；阶段E恢复进行中 | 用户明确授权新增一次约140GB的完整双clean generation，并允许独立验证通过后再次覆盖现有20个manifest与`bundle.v1.json`共21个production JSON；此前连续三轮的同一外部授权阻塞已解除。执行仍须固定`SOURCE_DATE_EPOCH=1788451200`、完整五方输入、Community-1 gated声明和项目盘0700私有`TMPDIR`，不得局部生成、手改或在独立验证前视为成功 | 用户本轮逐字授权；Granite新lock `6851d5ef…83af`；旧production bundle继续fail closed直至完整生成原子覆盖与offline/frozen独立验证完成 | 唯一计划、用户授权与冻结B-29命令；未提交 | 创建精确的项目内0700临时根并启动完整generate；持续轮询同一进程，exit 0后先独立verify和21文件闭包/权限/SHA/provenance审计，再恢复E-M07/E-M15/E-M21与B→A→C→D回归 |
| 2026-09-05 | Granite修复后的production 20模型双重生成成功；独立验证待执行 | 使用本轮明确授权、固定`SOURCE_DATE_EPOCH=1788451200`、完整五方输入、Community-1 gated声明和项目盘0700私有临时根，对全部20模型完成两个clean run；每项fixed revision实际payload、manifest bytes及最终bundle bytes一致，生成命令内置验证返回全部manifest可用。双run闭合后才原子覆盖21个production JSON；除两个共享新Granite worker lock的manifest与bundle外，其余18个manifest SHA保持不变 | 完整联网generate session 40572 exit 0、`status=verified`、`model_count=20`；bundle `45fd541defaf0d1d80d8ce011a01394c140a5412758c85aafeaad371c167e0b6`；Granite 4.1 `7ca6adadf1a0c81e3694156e017b3c91f524b8b882f9b954973e511f9064427e`；TurboCTC `e886354a819b9b868f12fd9d0c3582a0a08e6b04d10ac43aa10b84b3b14b4fbd` | 新production 20 manifests/bundle与完整生成输出；未提交 | 暂不恢复E-M07/E-M15/E-M21、manifest阶段门或B；立即在完全offline/frozen工具环境运行独立verifier，再核对目录恰好21个普通0644 JSON、实际/canonical SHA、Granite environment、其余18项不变及pyannote provenance |
| 2026-09-05 | 独立verifier通过；附加只读审计探针首次失败 | 生成进程之外的offline/frozen verifier重新加载正式bundle及全部冻结输入并成功验证20成员、实际/canonical SHA和四状态。随后组合的目录清单与`sha256sum`成功证明21个普通0644 JSON及新SHA，但`jq`错误假设bundle含顶层`models`并误用不存在的`.manifest.v1.json`文件名，后半段失败；没有写入或改变production工件，不能把该组合命令记为完整附加审计通过 | 独立verify exit 0、`status=verified`、`model_count=20`、bundle `45fd541d…e0b6`；附加命令目录/SHA输出有效，`jq: Cannot iterate over null`及三个`Could not open file`，组合exit非零 | verifier输出、21文件清单与失败jq输出；未提交 | 先读取实际bundle/manifest顶层键，再用真实`manifests`字段和现有`*.json`路径核对两个Granite新environment、18项不变及pyannote provenance；完成前仍不恢复勾选项 |
| 2026-09-05 | 恢复`E-M07`、`E-M15`、`E-M21`及manifest/verifier阶段门；B全回归待执行 | 修正只读探针后确认正式目录恰好21个普通0644 JSON；bundle的20个model ID唯一且已排序，所有成员实际SHA等于索引。两个Granite manifest独立且均绑定新worker lock `6851d5ef…83af`，其余18个manifest SHA逐项等于上一bundle；pyannote保持5个运行文件和1个固定WeSpeaker `derived` component source，未出现Authorization/Bearer或本机路径 | 独立offline/frozen verify exit 0、`status=verified`、`model_count=20`、bundle `45fd541d…e0b6`；修正后的附加审计exit 0：`audit_ok files=21 manifests=20 unchanged=18 granite_lock=6851d5ef pyannote_files=5 component_sources=1` | 新production bundle、独立verifier、目录/权限/SHA/environment/provenance审计及更新后的8.15 Granite行；未提交 | E生成清单重新闭合；先精确清理生成包装临时根，并运行完整B工具/lock/静态/release/architecture门，全部通过才恢复B，再按A→C→D依赖顺序回归 |
| 2026-09-05 | 第二次production生成私有临时根精确清理完成 | 生成器两个clean workspace均已由其生命周期删除；项目内包装根盘点仅剩一个0-byte普通0666 uv锁文件，无payload、cache、凭证或模型字节。用patch精确删除该唯一文件后，仅对已知空父目录执行`rmdir`并验证路径不存在；未使用递归或宽目标删除 | `find`唯一项`uv-de488ad5dc35fee4.lock f 666 0`；精确patch删除、`rmdir`与`test ! -e`均exit 0 | 私有临时根清单与清理输出；未提交 | 按B-01～B-35及5.10运行完整manifest-tool、frozen lock、Ruff/mypy、release-check、architecture与diff门；通过前B保持需回归 |
| 2026-09-05 | 8.12-4日语primary Granite新production首次健康失败 | A～D与新bundle门闭合后，默认service从`45fd541d…e0b6`读取新Granite manifest，完成固定14文件下载/校验并复用新TorchAudio lock环境；真实worker不再报缺依赖，而是在CPU首次日语inference期间超过RPC deadline，manager按`MODEL_HEALTH_CHECK_FAILED`拒绝激活。本次不能计health或重启通过，且bundle/registry/lock未改变，A～D证据不回退 | install session 89267 exit 1；`worker inference failed: ProtocolError: RPC deadline exceeded`；新manifest `7ca6adad…427e`、新worker lock `6851d5ef…83af` | 默认service/manager/downloader/provisioner/Bubblewrap worker失败输出；未提交 | 立即核对candidate/staging/active清理和残留进程；读取健康RPC deadline及Granite CPU加载/推理路径，区分真实超时合同与卡死。未取得有意义响应前8.12第4项保持未勾选，GPU另列阻塞 |
| 2026-09-05 | 主机内存恢复后的计划记录补丁首次失败 | 尝试以完整上一记录文本追加恢复与dtype探针结果，但hunk与磁盘上的最后一句不一致，`apply_patch`整体拒绝且无部分写入；没有影响production工件或执行模型 | `apply_patch verification failed: Failed to find expected lines` | 计划编辑失败输出；未提交 | 改用`## 9`短且唯一边界和独立top-row hunk追加，保留本失败记录 |
| 2026-09-05 | 主机内存压力恢复；Granite轻量dtype探针路径失败 | 用户确认内存压力已不存在并要求继续；复核为约27GiB available、swap 0，Granite candidate仍为空，持续目标恢复active。代码审计确认健康RPC deadline为600秒，而Granite adapter在CPU路径把manifest的`bfloat16`改为`float32`；CPU无硬件BF16 flag。首次小张量探针错误地在lock-addressed venv下再拼`.venv/bin/python`，exit 127且未执行Torch，不能算dtype支持证据 | `free -h`：31GiB总RAM、3.2GiB used、27GiB available、swap 0；错误probe `env: …/.venv/bin/python: No such file or directory`；adapter/registry只读审计 | 用户恢复指示、内存复核、源码与失败探针输出；未提交 | 读取真实venv布局及Granite adapter测试；用正确解释器做小张量能力探针。不得原样重跑float32模型，先冻结可测试的资源安全策略 |

| 2026-09-05 | 用户明确授权Granite CPU float32受控重试 | 用户进一步明确允许再次运行Granite float32路径并要求按原计划继续；当前复核为约28GiB available、swap 0，candidate仍为空。使用真实lock环境的正确解释器完成64元素BF16小张量探针，证明Torch能执行该dtype，但这不改变adapter在CPU上明确选择`float32`的production合同，也不据此替代健康证据。为避免单次失败再次拖垮整机，重试仅增加进程级RAM/swap硬上限，不改变模型、dtype、音频、bundle、worker或600秒健康deadline | 用户本轮明确授权；`free -h`：31GiB总RAM、3.0GiB used、28GiB available、swap 0；正确probe：Torch `2.14.0+cu130`、`torch.bfloat16`、64.0、AVX2；bundle `45fd541d…e0b6` | 用户指示、内存复核、真实worker venv轻量探针与候选清理状态；未提交 | 先以无害命令验证宿主资源隔离可用，再在同一默认service和新production bundle下重跑未改动的Granite CPU float32日语install→load→infer→unload；持续观察内存并如实记录成功、deadline或受限OOM |

| 2026-09-05 | Granite float32重试资源隔离验证通过；真实链启动 | `systemd-run --user --scope`已确认可为单次验收创建独立scope并应用`MemoryMax=24G`、`MemorySwapMax=2G`，无害命令exit 0。该限制不修改ClassScribe服务、manifest、worker或模型配置，仅为宿主故障边界；现按用户授权启动与首次失败相同的默认service Granite日语production链 | `systemd-run … /usr/bin/true` exit 0；重试前28GiB available、swap 0 | 资源隔离验证输出与唯一计划；未提交 | 在同一scope参数下运行`/tmp/classscribe-e-install-model.py`，轮询单一进程并监控宿主内存；完成后立即记录manager/health/candidate/active事实 |

| 2026-09-05 | Granite重试前一次性验收脚本已被系统清理 | 在真正启动重型进程前的只读前置核对发现两个`/tmp/classscribe-e-*-model.py`均不存在，串联检查exit 1；只读日语WAV仍存在且模型缓存/worker环境未受影响，因此没有发生下载、load或infer，不能计作Granite运行失败 | `ls`：两个`/tmp`脚本`No such file or directory`；`ja-health.wav`仍为299674 bytes、0400 | 前置文件检查输出；未提交 | 从当前仓库service/manager公开合同重建等价的一次性install和restart脚本，以Ruff/编译检查通过后再执行已验证的资源scope命令 |

| 2026-09-05 | 重建验收脚本首次静态检查被只读默认uv缓存拒绝 | 两个一次性脚本已用当前默认service、bundle SHA和公开安装/重启健康合同重建；首次`uv run --offline --frozen ruff`在实际检查前因sandbox内默认`~/.cache/uv`只读而exit 2，串联命令未执行Ruff、编译或WAV核验，没有模型操作或production写入 | `Could not acquire lock`、`Read-only file system … /home/hubery-fedora/.cache/uv/.tmp…` | 临时脚本与uv失败输出；未提交 | 显式使用项目内既有私有`UV_CACHE_DIR`重跑完全相同的Ruff、编译及WAV SHA/mode前置门；通过后才启动模型 |

| 2026-09-05 | 重建验收脚本第二次静态检查发现import排序 | 使用可写私有uv缓存后Ruff实际执行并以两个`I001`拒绝脚本import分组；因串联fail-fast，编译和WAV核验未执行，仍没有启动下载、模型load或infer | Ruff exit 1：两个脚本各1个`I001 Import block is un-sorted or un-formatted` | 临时脚本与Ruff输出；未提交 | 用`apply_patch`按Ruff期望修正两个import块并原样重跑全部前置门；通过前不运行重型进程 |

| 2026-09-05 | Granite一次性验收脚本与输入前置门通过 | 两个脚本现从当前默认service执行bundle-only request/confirm、真实health与active verify，并为断网重启直接解析现有active和冻结worker环境；固定新bundle SHA。Ruff和Python编译均通过，日语WAV SHA、0400模式和大小保持冻结 | Ruff `All checks passed!`；`py_compile` exit 0；WAV `af288eca…afaa`、mode 400、299674 bytes | 临时脚本、production bundle与只读WAV；未提交 | 在已验证的24G RAM/2G swap scope中启动联网默认安装链；同一session持续轮询并观察主机内存 |

| 2026-09-05 | 8.12-4日语primary Granite受控首次安装与CPU离线健康通过；重启待验 | 用户明确授权的原CPU float32路径在仅提供宿主故障边界的24G RAM/2G swap scope中完成。默认service从新20项bundle执行预检、固定revision 14文件下载/校验、复用新TorchAudio lock环境，并在Bubblewrap无网络运行时完成load→日语ASR infer→unload；manager原子激活后`verify_model`再次通过。首次超时/内存压力没有复现，但该项仍须独立断网新core复验才能闭合 | install session 10613 exit 0；bundle `45fd541d…e0b6`；manifest `7ca6adad…427e`；revision `de575db6…bc546`；14 files/4,636,316,501 bytes；aggregate `88a5b402…eef`；health=true、backend `transformers_granite_speech`、runtime_offline=true、CPU float32/VRAM 0；完成瞬间18GiB available、swap 0 | 默认service/downloader/manager、frozen Granite环境、Bubblewrap worker与受控scope输出；未提交 | 确认scope退出后无残留worker且内存回落；随后在独立新core/user+network namespace中禁止下载并只resolve frozen环境，复用同一active完成第二次load→infer→unload |

| 2026-09-05 | Granite首次健康后清理与宿主恢复确认 | 安装scope退出后进程表没有Granite worker、验收脚本或模型ID残留；`.staging`为空，唯一active指向完整固定revision，14个payload文件及`supply-chain.json`均为0600。主机回落至约27GiB available且swap 0，故可进入独立断网重启，不沿用首次Python/core进程 | `ps … | rg`无输出；`free -h` 3.2GiB used/27GiB available/swap 0；active/revision目录0700、文件0600；staging无条目 | 进程、内存、安装树与暂存目录只读盘点；未提交 | 在同一资源scope内启动新`uv run python`，外层`unshare --user --map-root-user --net`切断网络，只resolve现有worker环境和active后执行第二次真实健康生命周期 |

| 2026-09-05 | 日语primary Granite CPU float32首次与断网重启闭合 | 新Python/core在独立user+network namespace中确认外网不可达，默认service重新验证同一production bundle；manager直接解析既有active，provisioner仅`resolve` lock-addressed Granite环境，不下载或sync。第二次Bubblewrap load→日语ASR infer→unload返回有意义响应，backend与manifest一致，scope exit 0；结合上一条首次安装证据，日语primary CPU验收完整闭合 | restart exit 0、network_blocked=true；bundle `45fd541d…e0b6`；revision `de575db6…bc546`；worker lock `6851d5ef…83af`；health=true、detail `actual short-audio inference completed`、backend `transformers_granite_speech`、runtime_offline=true、CPU float32/VRAM 0 | 同一active安装树、独立断网新core、frozen worker resolve与受控scope输出；未提交 | 8.12第4项仍不勾选：中文、日语已闭合，下一步从`classroom.en`冻结profile解析英语primary并执行同等级首次安装与独立断网重启；GPU另列阻塞 |

| 2026-09-05 | 8.12-4英语primary路由与冻结输入确认 | `classroom.en`冻结排序的首位模型为`moss_transcribe_preview_2b`；production manifest固定14文件、revision、4,853,759,602-byte下载/安装估算及moss_en lock。复用已验证的16kHz mono 16-bit英语健康WAV，保持0400；不会用Granite日语结果替代英语primary | registry `classroom.en: [moss_transcribe_preview_2b, …]`；manifest revision `c98175cb…9f780`、14 files、worker lock `ac392045…890c`；WAV `bbeaef1d…9939f`、183956 bytes、mode 400 | registry、production manifest与冻结英语WAV只读审计；未提交 | 把通用install harness的预检结果在确认前脱敏输出并静态复验；随后以同一24G/2G scope执行MOSS Preview首次安装与英语健康 |

| 2026-09-05 | 8.12-4英语primary MOSS Preview首次安装与CPU离线健康通过；重启待验 | 通用harness在确认前披露空间且未输出confirmation token；默认service从新bundle完成固定revision 14文件下载/校验，新建lock-addressed moss_en环境，并在Bubblewrap无网络运行时完成load→英语ASR infer→unload。manager原子激活及`verify_model`通过；adapter源码确认CUDA不可用时选择CPU float32，本次VRAM 0，不替代GPU证据 | install session 89423 exit 0；preflight required 10,678,271,124、available 319,458,353,152 bytes；bundle `45fd541d…e0b6`；manifest `78797aaa…a83`；revision `c98175cb…9f780`；14 files/4,853,759,602 bytes；aggregate `b4713a5c…6300`；health=true/backend `transformers_moss_preview`/runtime_offline=true/VRAM 0 | 默认service/downloader/manager、frozen moss_en provision、Bubblewrap worker及scope输出；未提交 | staging与相关进程已确认无残留，主机27GiB available/swap 0；以独立新core/user+network namespace只resolve现有active和worker环境，执行第二次英语健康生命周期 |

| 2026-09-05 | 完成8.12第4项中文、日语、英语课堂primary CPU真实验收 | 英语MOSS Preview在独立新Python/core及user+network namespace中确认外网不可达，默认service复验同一bundle，直接resolve既有active和lock-addressed moss_en环境；第二次Bubblewrap load→英语ASR infer→unload返回有意义响应且scope exit 0。结合已闭合的中文FireRedASR2-AED与日语Granite，同一production bundle下三路课堂primary的CPU首次/断网重启均完成；均不冒充GPU证据 | MOSS restart exit 0、network_blocked=true；bundle `45fd541d…e0b6`；revision `c98175cb…9f780`；worker lock `ac392045…890c`；health=true/backend `transformers_moss_preview`/runtime_offline=true/CPU float32/VRAM 0；中文与日语证据见前序记录 | 三个primary安装树、独立断网重启输出与8.15逐模型证据；未提交 | 勾选8.12第4项；按顺序进入第5项MOSS结构、ARK和其他fallback，先解析该组当前enabled/worker/profile覆盖并选择未验收模型顺序；GPU子矩阵继续保持阻塞 |

| 2026-09-05 | 8.12第5项模型闭包与执行顺序确认 | 冻结profiles表明本项尚未验收的实际覆盖为MOSS结构`moss_td_0_9b`、ARK fallback `ark_asr_3b`及auto/多语言fallback `qwen3_asr_1_7b`，按计划语义依次执行；三者worker均实现且enabled/installable。每项CPU adapter都在CUDA不可用时明确选择float32，故继续保留GPU子矩阵阻塞而不把CPU健康冒充VRAM证据 | MOSS TD 16 files/1,833,089,258 bytes/lock `cc719712…73a2`；ARK 18 files/8,142,995,244 bytes/lock `2eb49626…afea`；Qwen1.7B 10 files/4,703,055,333 bytes/lock `ef7d017b…09ce`；当前315,528,577,024 bytes可用、27GiB RAM available/swap 0 | registry profiles、三个production manifests、adapter CPU dtype分支及宿主状态；未提交 | 先执行MOSS TD默认预检、固定下载和英语短音频CPU健康，再独立断网重启；闭合后依次ARK、Qwen1.7B |

| 2026-09-05 | 8.12-5 MOSS TD首次安装与CPU离线健康通过；重启待验 | 默认service预检披露后完成固定revision 16文件下载/校验，并从冻结lock新建moss_td环境；依赖中的固定Git revision在显式联网安装阶段取得，实际worker健康阶段保持offline。Bubblewrap完成load→结构/ASR batch infer→unload，manager原子激活及`verify_model`通过；scope退出后staging/进程无残留，宿主27GiB available/swap 0 | install session 60408 exit 0；preflight required 4,032,796,367、available 315,528,491,008 bytes；bundle `45fd541d…e0b6`；manifest `2e364b70…d913`；revision `704aa4a9…5b15`；16 files/1,833,089,258 bytes；aggregate `e4fa9968…2421`；health=true/backend `transformers_moss_transcribe_diarize`/runtime_offline=true/CPU float32/VRAM 0 | 默认service、production下载/校验、frozen provision、Bubblewrap与清理盘点；未提交 | 在独立新core/user+network namespace只resolve现有active与moss_td环境，执行第二次真实结构/ASR健康；通过后继续ARK |

| 2026-09-05 | MOSS TD CPU首次与独立断网重启闭合 | 独立新Python/core及user+network namespace确认外网不可达；默认service复验同一bundle，manager解析既有active，provisioner仅resolve lock-addressed moss_td环境。第二次Bubblewrap load→结构/ASR batch infer→unload返回有意义响应，backend/runtime offline/CPU路由与首次一致；本模型证据闭合 | restart exit 0、network_blocked=true；bundle `45fd541d…e0b6`；revision `704aa4a9…5b15`；worker lock `cc719712…73a2`；health=true/backend `transformers_moss_transcribe_diarize`/runtime_offline=true/CPU float32/VRAM 0 | MOSS TD active安装树、独立断网新core及frozen worker resolve输出；未提交 | 8.12第5项整体仍不勾选；严格继续`ark_asr_3b`默认预检、固定下载、CPU健康与独立断网重启 |

| 2026-09-05 | 8.12-5 ARK首次安装与CPU离线健康通过；重启待验 | 默认service预检披露后完成固定revision 18文件下载/校验，并从冻结lock新建ARK环境；Bubblewrap无网络运行时完成load→英语ASR infer→unload，manager原子激活及`verify_model`通过。24G/2G scope成功提供故障边界，没有达到上限；完成时staging为空、27GiB available，swap短暂保留40MiB且无内存压力 | install session 47409 exit 0；preflight required 17,914,589,536、available 314,031,386,624 bytes；bundle `45fd541d…e0b6`；manifest `46014147…3e05`；revision `1e28271b…09ed3`；18 files/8,142,995,244 bytes；aggregate `bb82b526…49ed`；health=true/backend `transformers_arkasr`/runtime_offline=true/CPU float32/VRAM 0 | 默认service、production下载/校验、frozen ARK provision、Bubblewrap、scope及宿主状态；未提交 | 确认无残留worker后，在独立新core/user+network namespace只resolve既有active与ARK环境执行第二次健康；通过后继续Qwen1.7B |

| 2026-09-05 | ARK CPU首次与独立断网重启闭合 | 首次scope退出后无ARK worker/脚本残留且staging为空，swap回落至2MiB；独立新Python/core及user+network namespace确认外网不可达。默认service复验bundle，manager直接解析active，provisioner仅resolve冻结ARK环境；第二次Bubblewrap load→英语ASR infer→unload返回有意义响应并exit 0 | restart exit 0、network_blocked=true；bundle `45fd541d…e0b6`；revision `1e28271b…09ed3`；worker lock `2eb49626…afea`；health=true/backend `transformers_arkasr`/runtime_offline=true/CPU float32/VRAM 0 | ARK active安装树、独立断网新core、frozen worker resolve与宿主清理输出；未提交 | 8.12第5项整体仍不勾选；继续最后一个fallback `qwen3_asr_1_7b`的默认安装/CPU健康和独立断网重启 |

| 2026-09-05 | Qwen1.7B成功记录的首次计划补丁失败 | 尝试同时更新8.15行并追加首次健康记录时，把Qwen manifest SHA误写成MOSS manifest SHA，`apply_patch`因找不到预期行整体拒绝；production工件与实际健康结果不受影响，文档无部分写入 | `apply_patch verification failed: Failed to find expected lines`；真实Qwen manifest仍为`a56f3fcc…35f49` | 计划补丁失败输出与只读行复核；未提交 | 使用磁盘上的精确Qwen行和短边界重试，同一补丁保留本失败并登记真实成功；完成后才启动断网重启 |

| 2026-09-05 | 8.12-5 Qwen3-ASR-1.7B首次安装与CPU离线健康通过；重启待验 | 默认service完成预检、固定revision 10文件下载/实际SHA校验，并复用同一冻结Qwen worker lock；Bubblewrap无网络运行时完成load→英语ASR infer→unload，manager原子激活及`verify_model`通过。adapter在CUDA不可用时选择CPU float32；scope内没有资源失败，staging为空 | install session 50494 exit 0；preflight required 10,346,721,732、available 307,452,928,000 bytes；bundle `45fd541d…e0b6`；manifest `a56f3fcc…35f49`；revision `7278e1e7…6e5`；10 files/4,703,055,333 bytes；aggregate `2c001cd3…0f37`；health=true/backend `qwen_asr_transformers`/runtime_offline=true/CPU float32/VRAM 0 | 默认service、production下载/校验、frozen Qwen环境、Bubblewrap与scope输出；未提交 | 在独立新core/user+network namespace只resolve既有active与Qwen环境执行第二次健康；成功后勾选8.12第5项并进入ForcedAligner/FireRedPunc |

| 2026-09-05 | 完成8.12第5项MOSS结构、ARK及Qwen1.7B fallback CPU真实验收 | Qwen1.7B在独立新Python/core及user+network namespace中确认外网不可达，默认service复验bundle后仅resolve既有active与冻结Qwen环境；第二次Bubblewrap load→英语ASR infer→unload返回有意义响应，backend/runtime offline/CPU路由一致。结合MOSS TD与ARK同等级证据，本组三个未验收模型均完成首次安装与断网重启 | Qwen restart exit 0、network_blocked=true；bundle `45fd541d…e0b6`；revision `7278e1e7…6e5`；worker lock `ef7d017b…09ce`；health=true/backend `qwen_asr_transformers`/runtime_offline=true/CPU float32/VRAM 0；MOSS TD与ARK见前序记录 | 三个fallback active安装树、独立断网新core及8.15证据；未提交 | 勾选8.12第5项；严格进入第6项ForcedAligner与FireRedPunc，分别准备精确参考文本和标点文本健康输入并完成首次/断网重启 |

| 2026-09-05 | 8.12第6项冻结输入与顺序确认 | ForcedAligner固定8文件、1,840,013,484 bytes并复用已冻结Qwen环境；安装确认必须提供与0400英语WAV对应的精确文本`ClassScribe verify real speech recognition and language identification.`，不得用普通ASR无文本路径。FireRedPunc固定11文件、818,896,071 bytes并复用FireRed环境，使用同一文本触发`punctuate`协议。两者均无gated条款 | aligner revision `c7cbfc20…62b7`/manifest `613e1af8…702`/Qwen lock `ef7d017b…09ce`；punc revision `e448fd96…28da`/manifest `98153f56…dd6e`/FireRed lock `14543cb7…f445`；当前303,615,627,264 bytes可用、28GiB RAM available | 两个production manifests、health请求选择逻辑与冻结WAV/参考文本；未提交 | 先执行aligner默认预检、固定下载、CPU align健康与独立断网重启；再执行FireRedPunc同等级两次生命周期 |

| 2026-09-05 | 8.12-6 ForcedAligner首次production健康失败 | 默认service预检与固定8文件下载完成，使用明确的精确英语参考文本进入真实CPU align；worker load成功但adapter拒绝上游返回，因为结果中没有当前实现要求的token list。manager按`MODEL_HEALTH_CHECK_FAILED`拒绝激活；这是协议/解析失败而非资源问题，主机28GiB available且swap未增长。本次不能计health或重启通过 | install session 60215 exit 1；preflight required 4,048,029,664、available 303,616,184,320 bytes；错误`model inference failed: internal_error: Qwen alignment output has no token list`；bundle `45fd541d…e0b6`、manifest `613e1af8…702` | 默认service/downloader/manager、Qwen worker真实alignment失败输出；未提交 | 立即确认candidate/staging/active与残留进程清理；审计Qwen官方固定版本的alignment返回结构及adapter/tests，先构造不联网的真实返回诊断，修复必须保持严格协议且补回归，随后按影响传播回归A～D再重试 |

| 2026-09-05 | ForcedAligner candidate清理与官方结果结构根因确认；A～D标记需回归 | 失败后模型目录只剩空0700 `revisions/`，`.staging`和相关进程均为空，主机28GiB available；冻结Qwen环境中直接读取当前`qwen-asr`实现，确认`align()`声明返回`List[ForcedAlignResult]`且每项token位于`.items`。adapter现只接受原生list、mapping的`timestamps/words`或对象`.timestamps`，集成测试也只模拟旧式list，故精确根因为受支持官方结构漏解析 | 清理盘点无candidate/active/进程；官方运行环境`inspect.getsource`显示`ForcedAlignResult(items=items)`；adapter `_alignment_words`未处理`items`；现有测试返回`list[list[SimpleNamespace]]` | 真实冻结依赖源码、adapter/tests与清理/内存输出；未提交 | 在保持逐token类型、时间单调和非空校验的前提下支持mapping/object `items`，把集成fixture改成官方形状并补拒绝测试；随后按B→A→C→D完整回归，全部恢复后重新下载并健康验收 |

| 2026-09-05 | ForcedAligner定向回归首次在受限sandbox挂起 | adapter已增加object/mapping `.items`选择且保留逐token类型、非空、时间单调和音频边界校验；集成fixture改为真实`ForcedAlignResult(items=[…])`形状。随后定向pytest在受限sandbox连续约120秒无任何输出，进程探针在该边界亦不可见；主动发送Ctrl-C后exit 130。串联的Ruff/mypy/diff未执行，不能计测试结果 | pytest session 9735连续四次30秒轮询无输出；Ctrl-C exit 130 | 修改后的adapter/test与挂起session输出；未提交 | 复用此前D后端同类sandbox阻塞处置，在外层边界原样运行同一定向pytest；结束后再独立运行静态门，任何真实断言失败立即记录 |

| 2026-09-05 | ForcedAligner官方`.items`定向测试通过；首次窄mypy调用方式失败 | 同一定向集成测试在外层边界立即通过2项，证明先前只有sandbox挂起；Ruff随后通过。把非package worker文件与package测试文件同时作为窄mypy位置参数导致同一源码被解析为`adapter`和`workers.qwen.adapter`两个模块，mypy在类型检查前exit 2；串联diff未执行。这是调用拓扑错误，不能计静态失败或通过 | 外层pytest `2 passed in 0.11s`；Ruff `All checks passed!`；mypy `Source file found twice under different module names: adapter and workers.qwen.adapter` | 定向测试、Ruff与错误mypy输出；未提交 | 按计划冻结的全项目package-root mypy命令而非混合窄路径重跑，并独立执行diff；通过后进入完整B→A→C→D回归 |

| 2026-09-06 | ForcedAligner解析修复静态门与阶段B完整回归闭合 | 按根配置运行严格mypy而非错误的混合窄路径，224个source无问题且diff通过。随后完整manifest-tool fake-Hub攻击矩阵、frozen lock、工具Ruff/mypy/离线CLI、production bundle离线verifier、release与architecture门全部通过；worker源码修复没有改变dependency lock、manifest或bundle字节 | 定向worker `2 passed`；根mypy `224 source files`；tool `149 passed in 4.25s`、lock 33、tool mypy 19、Ruff；offline verify `status=verified`/20、bundle `45fd541d…e0b6`；release/architecture `8 passed in 4.02s`、script `OK`、diff通过 | Qwen adapter/test、完整B工具与production bundle只读验证输出；未提交 | 阶段B恢复已完成；严格进入阶段A全仓consumer/合同/RPM/静态/architecture回归，通过后再恢复C/D并重试ForcedAligner |

| 2026-09-06 | 阶段A全部行为/RPM通过；首次全仓Ruff误扫阶段E冻结payload | 外层普通用户边界的全仓非RPM测试全部通过，默认sandbox普通用户RPM生命周期亦通过。随后`ruff check .`递归进入项目内阶段E私有安装树，扫描ARK/MOSS等manifest固定的上游remote-code payload并报告297个供应商风格项；这些文件不属于仓库源码且必须保持下载SHA，不能修改。Ruff exit 1使串联mypy/architecture/diff未执行，A暂不恢复 | 全仓`472 passed, 1 skipped, 1 deselected in 17.66s`；RPM `1 passed in 7.10s`；错误Ruff 297项均位于`.e-firered-health.4yUYwE/xdg-cache/.../models/*/revisions/*`，包括ARK/MOSS frozen remote code | 全仓/RPM输出与错误Ruff路径归因；未提交 | 保留冻结payload字节；以显式排除阶段E和阶段C证据根的全仓Ruff重跑项目源码，再执行根mypy、真实architecture test/script与diff，全部通过才恢复A |

| 2026-09-06 | 阶段A ForcedAligner解析修复完整回归闭合 | 保持阶段C/E私有安装树中的上游冻结payload不变，以显式排除仅存证根的Ruff覆盖全部项目源码；严格mypy、真实architecture test/script与diff全绿。结合上一条全仓及RPM行为结果，Qwen `.items`支持没有退化manager、API、其他worker、打包或架构合同 | 全仓`472 passed, 1 skipped, 1 deselected`；RPM `1 passed`；项目源码Ruff `All checks passed!`；mypy `224 source files`；architecture `1 passed`、script `OK`；diff通过 | Qwen adapter/test、根项目完整测试与静态门；未提交 | 阶段A恢复已完成；使用保留的新bundle fresh-XDG Whisper安装树在独立断网新core再跑一次C-16，然后完整D后端/前端门 |

| 2026-09-06 | 阶段C ForcedAligner修复后回归闭合 | 保留的新bundle fresh-XDG Whisper安装树在独立新Python/core及user+network namespace中确认外网不可达；默认service重新验证20项bundle，manager解析既有active，provisioner仅resolve冻结moss_en环境。真实Bubblewrap再次完成load→英语ASR infer→unload，证明Qwen adapter局部修复未退化通用安装/健康/重启合同 | restart exit 0、network_blocked=true；bundle `45fd541d…e0b6`；Whisper revision `d90ca5fe…d356`；worker lock `ac392045…890c`；health=true/backend `faster_whisper_cpu_int8`/runtime_offline=true/CPU int8/VRAM 0；同轮全仓测试已覆盖C-17清理 | 保留fresh-XDG安装树、独立断网新core与阶段A全仓输出；未提交 | 阶段C恢复已完成；运行阶段D后端66项及前端17项、lint/format/type/build完整门，通过后恢复D并重试ForcedAligner |

| 2026-09-06 | 阶段D回归命令定位首次只读shell引号失败 | 为检索既有66项后端与17项前端冻结命令，`rg`命令字符串内混入未闭合双引号，zsh在执行任何读取前以`unmatched \"`退出；未运行测试、未修改文件 | zsh exit非零：`unmatched \"` | 命令失败输出；未提交 | 使用只含单引号的无歧义`rg`模式重新定位既有D命令与frontend scripts；不得把本次失败计为D证据 |

| 2026-09-06 | ForcedAligner `.items`修复后的阶段D行为门通过；重试前发现旧worker副本缓存风险 | 既定66项默认runtime/API/status/manager/health/privacy回归与Node24前端17项、lint/format/type/build全部通过。真实重试前比较当前Qwen adapter和阶段E已provisioned副本，SHA不同，确认现有`WorkerEnvironmentProvisioner`只用dependency lock SHA作为目录键；adapter源码改变而lock不变时`ensure/resolve`会静默复用旧副本，直接重试仍会跑旧解析。不能手工覆盖或删除缓存来冒充升级正确 | 后端`66 passed in 1.88s`；前端`17 passed`及五门全绿；source adapter `e05b8717…a6ba`，旧副本`b60a13e8…f79f`，`cmp` exit 1；diff通过 | D完整门与当前/缓存adapter SHA只读对比；未提交 | 将worker环境同时绑定dependency lock和被复制的worker/protocol source identity，让源码变化自动选择新immutable环境且旧环境保留；补source变化阻止reuse测试，随后重新回归A～D再重试 |

| 2026-09-06 | worker source-addressing首次补丁上下文失败 | 尝试一次性修改provisioner与测试时，补丁对文件尾部ignore helper的预期文本与磁盘实际内容不一致，`apply_patch`整体拒绝且无部分写入；复核草案同时发现一个无意义的临时测试占位断言，未进入工作树 | `apply_patch verification failed: Failed to find expected lines … environment.py` | 补丁失败输出；未提交 | 精确读取environment.py全段和测试当前内容，删除占位断言，分成实现与测试两个可验证补丁后重试 |

| 2026-09-06 | source-addressed实现落盘；首次组合定向测试再次在受限sandbox挂起 | provisioner现以`lock SHA/source SHA`两级immutable路径创建环境，complete同时绑定二者；source fingerprint规范化覆盖复制的worker、protocol与build config，跳过与copytree一致的cache目录并拒绝symlink/special file。新增source变化使resolve fail closed、ensure创建新副本且保留旧环境的测试。组合pytest先输出6个点后在已知postprocess sandbox边界停滞，30秒后Ctrl-C exit 130；串联Ruff/mypy/diff未执行，点号不计完整结果 | pytest session 31693输出`......`后挂起；Ctrl-C exit 130 | environment实现、worker-environment/postprocess tests与session输出；未提交 | 在外层边界原样重跑两个测试文件取得summary，再独立执行Ruff/全根mypy/diff；若有真实断言失败立即记录并修复 |

| 2026-09-06 | source-addressed定向行为与静态门通过；升级兼容性仍待收敛 | 同一worker-environment与Qwen postprocess组合在外层边界立即完成，源码变化选择新目录、旧副本保留、官方`.items`解析均通过；相关Ruff、根严格mypy及diff也通过。复核路径迁移后发现，现实现会让所有旧版`lock-only` active环境在应用升级后直接无法`resolve`，即便该worker源码没有变化；该升级合同尚未被定向测试覆盖，不能据此恢复A～D或重试真实模型 | 外层pytest `8 passed in 0.18s`；Ruff `All checks passed!`；mypy `224 source files`；diff通过；旧环境布局为`<worker>/<lock>`而新目标为`<worker>/<lock>/<source>` | provisioner实现、定向测试/静态输出与旧安装树只读审计；未提交 | 审计sandbox命令对环境路径的实际使用，补兼容迁移或等价的离线安全策略；必须证明未改源码的旧active仍可运行、已改源码的Qwen不会复用旧adapter，再按B→A→C→D回归 |

| 2026-09-06 | lock-only兼容实现定向行为通过；首次并行静态命令受只读uv cache阻断 | `resolve/ensure`现仅在旧complete绑定同一worker/lock且旧副本worker+protocol实际源码与当前source digest一致时只读复用；Qwen源码变化会拒绝旧副本并在用户确认健康阶段建立新source子目录。source walker改为剪枝`.venv`以避免扫描大型依赖树，并在复制后复算digest防竞态。新增两个legacy合同测试后外层行为全绿；随后受限sandbox并行Ruff/mypy均因用户uv cache只读而在分析前exit 2，diff单独通过，不能把静态门计为完成 | 外层pytest `10 passed in 0.20s`；Ruff/mypy均报`Could not acquire lock ... ~/.cache/uv ... Read-only file system`；diff exit 0 | environment兼容实现、legacy tests与命令输出；未提交 | 使用项目外可写的私有`UV_CACHE_DIR`原样重跑Ruff和根mypy；通过后对保留Whisper legacy环境与Qwen stale环境做只读resolve实证，再进入完整回归 |

| 2026-09-06 | 可写uv cache下静态门暴露两处局部问题 | Ruff实际执行后发现source walker的一条错误文本101列，mypy发现循环变量`name`先为目录字符串、后被复用为编码bytes，导致赋值与hash update类型错误；均局限于新helper，行为测试证据不受影响，但静态门真实失败 | Ruff `E501` 1项；根mypy `2 errors in 1 file (checked 224 source files)` | `environment.py::_source_sha256`与可写临时uv cache输出；未提交 | 折行错误文本并将编码后的相对名改用独立变量，随后重跑10项行为、Ruff、根mypy与diff |

| 2026-09-06 | source-addressed与旧lock-only兼容定向门闭合 | 修正局部格式和变量类型后，10项行为、相关Ruff、根严格mypy与diff全部通过。新合同同时证明：新环境绑定lock+实际复制源码；source变化不会runtime复用；相同源码的旧lock-only副本只读兼容且`ensure`不执行runner；变化后的legacy副本保留并生成新的immutable source子目录 | 外层pytest `10 passed in 0.19s`；Ruff `All checks passed!`；mypy `224 source files`；diff exit 0 | environment/Qwen adapter及两组tests；未提交 | 对保留的真实Whisper legacy环境和Qwen陈旧环境执行只读resolve探针；结果符合预期后按B→A→C→D跑完整回归 |

| 2026-09-06 | 真实旧环境兼容/陈旧拒绝探针符合合同 | 使用当前source tree与两个保留的production证据根只读调用新provisioner：未改源码的MOSS English/Whisper worker成功解析原`<worker>/<lock>`环境；adapter已改的Qwen旧副本以`MODEL_HEALTH_CHECK_FAILED`拒绝，没有执行`ensure`、uv、下载或文件写入。这证明兼容分支没有把Qwen陈旧代码重新放行 | probe exit 0；Whisper解析lock `ac392045…890c`旧路径；Qwen旧lock `ef7d017b…09ce`返回预期blocked | 两个保留worker environment与只读Python探针；未提交 | worker cache风险的定向解除条件已满足；按唯一计划顺序立即重跑完整B→A→C→D，四门全绿后才启动ForcedAligner下载与新Qwen环境创建 |

| 2026-09-06 | 阶段B回归除独立verifier调用目录外通过 | manifest-tool完整149项、本地fake-Hub攻击矩阵、frozen/offline lock、工具Ruff/mypy、release与architecture门及diff全部通过。独立verifier从工具子目录以`../../config/...`调用，虽指向真实文件，但其冻结路径合同以当前工作目录推导repository root，因而在读取bundle前明确拒绝；这是调用工作目录错误，不能计verifier通过 | tool `149 passed in 4.14s`；lock 33；Ruff通过；mypy 19；release/architecture `8 passed in 3.39s`、script `OK`、diff通过；错误verify exit 2：`bundle must use the frozen config/model-manifests/v1 path` | B完整门与错误CLI输出；未提交 | 从repository root使用冻结接口`uv run --offline --frozen --project tools/model-manifest ... --bundle config/model-manifests/v1/bundle.v1.json`重跑独立verifier；成功后恢复B |

| 2026-09-06 | 阶段B source-addressed兼容修复后完整回归闭合 | 从repository root按B-29冻结接口重跑独立offline/frozen verifier，正式20成员bundle、成员实际SHA、五方输入、worker locks和四状态全部通过；结合上一条完整工具/静态/release/architecture证据，environment runtime实现变化未影响生成器、冻结JSON或离线验证合同 | verify exit 0、`status=verified`、`model_count=20`、bundle `45fd541d…e0b6`；tool `149 passed`、lock 33、mypy 19、Ruff、release/architecture `8 passed`、script `OK`、diff通过 | production bundle只读验证与B全门输出；未提交 | 阶段B恢复已完成；严格进入阶段A全仓行为、RPM、项目源码静态与architecture回归 |

| 2026-09-06 | 阶段A worker source identity修复后完整回归闭合 | 全仓除显式真实模型和单独RPM事务外的全部测试均通过，且这次保留stage3中的desktop/systemd与SELinux静态合同；RPM 6真实build→install→upgrade→erase生命周期另行通过。只对阶段C/E冻结payload证据根做Ruff排除，项目源码、根严格mypy、architecture test/script及diff全绿 | 全仓`475 passed, 2 deselected in 17.09s`（仅real-model marker与RPM单项）；RPM `1 passed in 7.02s`；Ruff通过；mypy `224 source files`；architecture `1 passed`、script `OK`；diff通过 | 新environment/Qwen实现、全仓/RPM/静态输出；未提交 | 阶段A恢复已完成；使用保留Whisper legacy环境在独立断网新core执行C-16，证明兼容分支经过真实Bubblewrap生命周期后再恢复C |

| 2026-09-06 | 阶段C legacy兼容真实断网生命周期闭合 | 保留的production Whisper active在独立新Python/core与user+network namespace中确认外网不可达；默认service复验20项bundle，provisioner从旧`<worker>/<lock>`布局只读核对实际worker/protocol源码相同，未执行uv或下载，随后真实Bubblewrap再次完成load→英语ASR infer→unload | restart exit 0、network_blocked=true；bundle `45fd541d…e0b6`；revision `d90ca5fe…d356`；worker lock `ac392045…890c`旧路径；health=true/backend `faster_whisper_cpu_int8`/runtime_offline=true/CPU int8/VRAM 0 | 保留fresh-XDG安装树、新core/namespace与真实worker输出；未提交 | 阶段C恢复已完成；运行D既定66项默认runtime/API/status/manager/health/privacy和Node24前端五门，通过后恢复D并继续ForcedAligner |

| 2026-09-06 | 阶段D source identity修复后完整回归闭合 | 默认runtime重新加载20项production bundle；models、bundle-only preflight/confirm、只读provenance、四状态、disabled/未实现门、WAV/错误脱敏及manager/health均未退化。Node24下前端仍无manifest输入，完整交互、格式、类型、测试和production build全部通过 | 后端既定组合`66 passed in 1.79s`；前端`17 passed`；ESLint、Prettier、typecheck、Vite build全绿；diff通过；同轮A/B/C证据均已恢复 | runtime/API/UI测试与production build输出；未提交 | 阶段D恢复已完成，A～D全部完成；解除E暂停并在24G RAM/2G swap scope下重跑ForcedAligner首次production安装，必须生成新source-addressed Qwen环境且旧副本保持不变 |

| 2026-09-06 | 8.12-6 ForcedAligner修复后首次production安装与CPU离线健康通过；重启待验 | 默认service再次完成预检、固定8文件下载/实际SHA校验；provisioner没有复用旧Qwen副本，而在相同dependency lock下创建`bae6f79c…45b95` source-addressed新环境。24G/2G scope内Bubblewrap以明确CPU设备加载float32 aligner，精确英语参考文本经官方`.items`结构解析后产生有意义token timing，load→align→unload、原子激活和`verify_model`均成功；旧lock-only副本保留 | install exit 0；preflight required 4,048,029,664、available 303,561,494,528 bytes；bundle `45fd541d…e0b6`；manifest `613e1af8…702`；revision `c7cbfc20…62b7`；8 files/1,840,013,484 bytes；aggregate `c55bd247…649ed`；health=true/backend `qwen3_forced_aligner`/runtime_offline=true/CPU float32；new source `bae6f79c…45b95` | production默认安装、frozen下载/新环境、真实Bubblewrap输出及active/supply-chain验证；未提交 | 在独立新core/user+network namespace只读resolve active与新source环境，使用同一0400 WAV/精确文本执行第二次load→align→unload；通过后完成E-QW-04并继续FireRedPunc |

| 2026-09-06 | 完成`E-QW-04`与ForcedAligner首次/独立断网重启验收 | 独立新Python/core及user+network namespace确认外网不可达；默认service复验bundle与active，provisioner精确解析`lock/source`新环境且没有运行uv，第二次Bubblewrap用同一0400英语WAV和逐字精确文本完成load→align→unload。当前、新环境adapter SHA一致，旧adapter SHA不同且旧副本仍原样保留；staging和worker无残留，宿主约28GiB available | restart exit 0、network_blocked=true；health=true/backend `qwen3_forced_aligner`/runtime_offline=true/CPU float32；current/new adapter `e05b8717…a6ba`，legacy `b60a13e8…f79f`；MemAvailable 29,457,228 KiB；diff通过 | active/supply-chain、新/旧Qwen环境、独立断网输出与清理探针；未提交 | ForcedAligner与两项代码风险均闭合；8.12第6项仍等待FireRedPunc，下一步使用同一精确英语文本执行其首次production安装和独立断网重启 |

| 2026-09-06 | 8.12-6 FireRedPunc首次production安装与CPU离线健康通过；重启待验 | 默认service完成预检、固定11文件下载/实际SHA校验，并只读复用源码未变的旧FireRed lock环境；24G/2G scope内Bubblewrap以CPU float32加载Punc，对无标点英语健康文本执行严格`punctuate`协议，返回有意义结果后完成unload、原子激活与`verify_model` | install exit 0；preflight required 1,801,571,356、available 301,940,047,872 bytes；bundle `45fd541d…e0b6`；manifest `98153f56…dd6e`；revision `e448fd96…28da`；11 files/818,896,071 bytes；aggregate `293d9820…dd3c`；health=true/backend `fireredpunc`/runtime_offline=true/CPU float32 | production默认安装、frozen下载、legacy-compatible FireRed环境与真实Bubblewrap输出；未提交 | 在独立新core/user+network namespace用同一0400 WAV/文本执行第二次load→punctuate→unload并复验active/supply-chain；通过后勾选8.12第6项并继续第7项pyannote |

| 2026-09-06 | 完成8.12第6项、`E-FR-05`与`E-RA-03` | FireRedPunc在独立新Python/core及user+network namespace确认外网不可达，默认service只读复验active、supply-chain和同源legacy FireRed环境，第二次Bubblewrap完成load→punctuate→unload。结合此前VAD/LID/AED真实链，FireRed四个已实现模型的策展闭包均经行为确认；结合MOSS TD/Preview与ARK各自独立断网重启，remote-code第3项也已满足 | Punc restart exit 0、network_blocked=true、health=true/backend `fireredpunc`/runtime_offline=true/CPU float32；bundle `45fd541d…e0b6`、lock `14543cb7…f445`；此前MOSS/ARK证据见8.15及记录 | FireRed四模型、MOSS/ARK与本次Punc独立断网输出；未提交 | 8.12第6项闭合；严格进入第7项pyannote gated与离线嵌套依赖，先确认凭证只以安全文件/环境存在且安装harness显式传递用户已授权terms flag，不读取或记录token |

| 2026-09-06 | 8.12-7 pyannote安装凭证与条款harness准备完成 | 当前进程存在HF token，但目标fresh-XDG没有凭证文件；为让production默认service使用既定安全入口，在不读取或输出token值的情况下通过`RestrictedCredentialEnvironment`原子写入唯一临时`model-download.env`，权限0600。通用安装harness新增仅由`E_TERMS_ACCEPTED=1`显式控制的terms布尔值；其他模型默认仍为false，未把条款确认写入production代码或bundle | 安全探针`process_hf_token=set`、目标credential absent；harness py_compile exit 0；创建后`mode=600`、`size_nonzero=True`，未输出内容 | 临时私有XDG凭证、用户本轮/前轮明确Community-1授权与安装harness；未提交 | 以`E_TERMS_ACCEPTED=1`运行pyannote默认预检、主仓库+固定derived组件下载、CPU离线diarize健康；成功或失败后均精确删除本次临时凭证文件，再进行独立断网重启 |

| 2026-09-06 | 8.12-7 pyannote gated首次production安装与CPU离线健康通过；重启待验 | 使用用户明确授权和临时0600凭证，默认service预检后从Community-1固定commit取得4个主源文件，并按manifest把固定WeSpeaker派生权重校验/安装到`embedding/`，最终5文件逐字节通过。新source-addressed pyannote环境在24G/2G scope内完成Bubblewrap load→diarize→unload、原子激活和`verify_model`；运行期offline且CPU/VRAM 0。安装结束后已精确删除本次创建的临时credential文件并验证不存在，token未写入bundle/计划/日志 | install exit 0；preflight required 82,420,058、available 301,121,863,680 bytes；bundle `45fd541d…e0b6`；manifest `179c9160…6a0e`；revision `3533c8cf…54ee`；5 installed files/32,821,421 bytes；aggregate `9010691d…ad82`；health=true/backend `pyannote_audio_community_1`/runtime_offline=true/CPU float32/VRAM 0；credential removed | production gated下载、多来源manifest、new worker env、Bubblewrap、active/supply-chain与安全清理输出；未提交 | 在无credential、独立新core/user+network namespace中只读resolve active与环境，执行第二次load→diarize→unload；通过后完成E-PY-01/02与8.12第7项 |

| 2026-09-06 | 完成`E-PY-01`～`E-PY-02`与8.12第7项 | 删除临时credential后，独立新Python/core显式移除`HF_TOKEN`并进入user+network namespace，外网连接失败；默认service仍只读解析active与新`lock/source`环境，第二次Bubblewrap从本地`config.yaml`、segmentation、PLDA与derived embedding完成load→diarize→unload，证明运行时不解析远程模型ID且闭包自足 | restart exit 0、network_blocked=true、无token/无credential；health=true/backend `pyannote_audio_community_1`/runtime_offline=true/CPU float32/VRAM 0；source env `34d5c696…294fa` | 无凭证独立断网重启、active/supply-chain与多来源installed tree；未提交 | pyannote gated/嵌套依赖闭合；严格进入8.12第8项两个Nemotron streaming checkpoint，先读取各manifest字节与worker CPU健康合同，再逐个首次安装/断网重启 |

| 2026-09-06 | 8.12-8 Nemotron 3.5首次production健康失败 | 默认service预检、固定单一2.37GB `.nemo` archive下载/哈希和新source-addressed NeMo环境均完成；24G/2G scope内真实Bubblewrap在load阶段调用冻结adapter的`ASRModel.restore_from`，NeMo 2.7.3尝试实例化抽象基类并报缺少`setup_training_data/setup_validation_data`，manager拒绝激活。本次没有infer/unload成功证据，不能计健康；错误是adapter restore入口与冻结NeMo API不兼容，不是内存压力 | install exit 1；preflight required 5,210,225,902、available 301,017,198,592 bytes；revision `1c8deaec…395d`；错误`TypeError: Can't instantiate abstract class ASRModel...`；bundle/manifest/lock未变 | production默认下载、frozen Nemotron环境和真实load失败输出；未提交 | 先确认candidate/staging/active与残留进程清理；直接审计冻结NeMo 2.7.3的`.nemo`恢复API和archive内target class，修复必须对两个独立archive通用并补测试；按影响传播回归A～D后再重试 |

| 2026-09-06 | Nemotron target-class恢复实现行为通过；首次根mypy失败 | 依据NVIDIA NeMo官方固定模式，adapter先以`ASRModel.restore_from(..., return_config=True)`读取archive配置，再只允许`nemo.collections.asr.models.*` target、导入并验证为ASRModel子类，最后由具体类restore；同时补恶意target拒绝。streaming定向4项与Ruff/diff通过；根mypy将经过`isinstance(..., type)`收窄的值视为普通`type`，对其动态`restore_from`报1项attr-defined，静态门未完成 | 定向`4 passed in 0.23s`；Ruff通过；mypy `1 error in 1 file (checked 224 source files)`；diff通过 | Nemotron adapter/streaming tests与冻结NeMo源码/官方示例；未提交 | 对已运行时验证的动态class使用显式`cast(Any, model_type)`调用restore，重跑定向、Ruff、根mypy/diff；全绿后按B→A→C→D恢复四门 |

| 2026-09-06 | Nemotron具体archive target恢复定向门闭合 | 动态target在前缀、class类型和ASRModel子类三层运行时校验后，仅用`cast(Any, ...)`表达NeMo动态classmethod给静态分析器；没有放宽实际执行条件。修正后成功streaming与恶意target拒绝共4项、Ruff、根严格mypy及diff全部通过 | 定向`4 passed in 0.20s`；Ruff通过；mypy `224 source files`；diff通过 | Nemotron adapter/tests与NVIDIA官方`return_config→target→import_class_by_path→concrete.restore_from`模式；未提交 | 依照影响传播完整重跑B→A→C→D；全部恢复后再次下载Nemotron 3.5并确认真实archive target、load/batch及额外streaming生命周期 |

| 2026-09-06 | 阶段B Nemotron恢复修复后完整回归闭合 | worker adapter源码变化没有改变dependency lock、manifest或bundle；完整manifest-tool fake-Hub攻击矩阵、frozen lock、工具Ruff/mypy、独立offline verifier、release与architecture门全部通过 | tool `149 passed in 4.38s`；lock 33；Ruff通过；mypy 19；verify `status=verified`/20、bundle `45fd541d…e0b6`；release/architecture `8 passed`、script `OK`、diff通过 | B全门与production bundle只读验证；未提交 | 阶段B恢复已完成；进入阶段A全仓行为、RPM、项目源码静态与architecture回归 |
| 2026-09-06 | 8.12-8 Nemotron 3.5 target-class修复后第二次production健康失败 | A～D恢复后，默认service重新完成预检、固定2.37GB archive下载，并按新source digest构建冻结NeMo环境；具体target恢复已越过抽象`ASRModel`实例化错误，但archive配置指向`nemo.collections.asr.models.rnnt_bpe_models_prompt`，冻结`nemo-toolkit==2.7.3`没有该模块，manager再次在load阶段拒绝激活。本次没有infer/unload成功证据，不能计健康 | install exit 1；preflight required 5,210,225,902、available 300,741,947,392 bytes；manifest `be37e4c1…83c4`；revision `1c8deaec…395d`；错误`ModuleNotFoundError: No module named 'nemo.collections.asr.models.rnnt_bpe_models_prompt'`；24G/2G scope未触发内存失败 | production固定下载、新source-addressed环境和真实具体target import失败输出；未提交 | 立即核对candidate/staging/worker清理；读取archive config target并审计NVIDIA官方NeMo版本/模块历史，修复必须同时约束两个checkpoint且补测试；若dependency lock或adapter改变，按影响传播重新标记并回归A～D后重试 |
| 2026-09-06 | Nemotron冻结dependency闭包根因确认；A～D标记需回归 | 失败后model candidate/staging/worker均为空且无active；新source环境保留。冻结2.7.3确有hybrid prompt模块但没有archive要求的RNNT-only prompt模块。NVIDIA模型卡要求从NeMo Git main安装；NVIDIA TensorRT参考容器更精确声明PyPI 2.7.3尚未发布该类，并固定`c9040511b`。GitHub API将其解析为完整commit `c9040511b2dbefe64767d9b8853b3a20d63a2cd2`，固定源码包含`EncDecRNNTBPEModelWithPrompt`。同时把6.3GB uv cache从16GB tmpfs迁到工作区磁盘，恢复约28GiB available | 官方commit源码读取成功；PyPI环境模块盘点确认缺失；candidate/staging/worker清理通过；`/tmp`从6.4GB降到69MB、MemAvailable约28GiB | NVIDIA官方model card、TensorRT参考Dockerfile、固定NeMo commit源码与本地冻结环境；未提交 | Nemotron worker改为完整SHA固定的官方NeMo Git source并重锁，补pyproject/lock不漂移测试；lock变化传播到registry、两个manifest和bundle，故A～D立即回退，全部恢复后再重试真实模型 |
| 2026-09-06 | Nemotron官方Git source首次重锁失败 | pyproject已把NeMo改为完整commit固定的3.1.0 source；uv读取成功但在多平台统一解析时发现NeMo对非Linux的PyTorch CPU index与worker默认PyPI来源冲突，严格拒绝生成lock。现有`uv.lock`字节未被接受为新闭包，不更新registry或production JSON | `uv lock --project workers/nemotron` exit 1；错误`Requirements contain conflicting indexes for package torch`，分支为Python 3.12且非emscripten/windows | Nemotron pyproject、uv resolver失败输出；未提交 | 该worker运行合同仅支持目标Fedora/Linux；在`tool.uv.environments`显式限定`sys_platform == 'linux'`后重锁，仍由uv解析完整依赖，不使用官方容器的`pip --no-deps`旁路 |
| 2026-09-06 | Nemotron Linux限定重锁启动失败 | 首次插入`tool.uv.environments`时把父表写在既有`tool.uv.sources`子表之后，TOML解析器以duplicate key拒绝，未进入依赖解析且lock未改变 | `uv lock` exit 2；TOML line 23 duplicate `[tool.uv]` | pyproject与解析错误输出；未提交 | 仅把`[tool.uv]`父表移到`[tool.uv.sources]`之前，保持commit和Linux限定语义不变，再次重锁 |
| 2026-09-06 | Nemotron Linux限定重锁第二次启动失败 | 只读行号复核发现文件末尾原有`[tool.uv] package=false`，此前移动的新父表仍与它重复；uv再次在解析阶段拒绝，依赖和lock均未变化 | `uv lock` exit 2；line 23 duplicate `[tool.uv]`；行号盘点确认两个父表 | pyproject与解析错误输出；未提交 | 合并`package=false`和Linux environments为唯一`[tool.uv]`，删除末尾重复表后重跑 |
| 2026-09-06 | Nemotron官方Git dependency lock已冻结 | 唯一`tool.uv`配置把解析目标明确限定Linux，并由uv完整解析NVIDIA commit而非`--no-deps`覆盖；新lock把NeMo固定为`3.1.0+c9040511b2`和完整git SHA，依赖总数由190降至143。新增回归逐字段约束pyproject exact version/source/Linux环境及lock的完整resolved commit，防止退回缺类的PyPI 2.7.3或漂移main | `uv lock`成功：143 packages；`uv lock --check`通过；新lock SHA `b2f372c42831303a4decd4c6f358191b3373c7206acc5a23478280e651610505`；定向测试`1 passed`；Ruff/diff通过 | Nemotron pyproject/uv.lock与worker-environment回归；未提交 | 把新lock SHA同步到registry两个Nemotron条目；旧production bundle随即必须fail closed，再按B合同完整双clean generation和独立验证后才能覆盖21个production JSON |
| 2026-09-06 | Nemotron新lock真实环境诊断harness首次静态失败 | 为在触发下一轮production生成前先证明Git source可真实构建，创建只调用正式provisioner的临时harness；Ruff发现lock SHA计算行101字符，未运行provisioning | Ruff `E501` 1项；没有下载、环境或production变更 | 临时harness与静态输出；未提交 | 折行后Ruff单独通过，再在24G/2G scope内构建新lock/source环境并于独立断网进程导入archive要求的target类 |
| 2026-09-06 | Nemotron新lock本地闭包完成；新增production生成授权阻塞 | 新lock/source环境由正式provisioner在24G/2G scope内构建完成，随后独立user/network namespace确认外网不可达并成功导入archive要求的RNNT prompt target、ASR基类和streaming buffer。worker/registry/streaming回归及全仓静态门通过；旧bundle只在首个Nemotron environment drift处fail closed。所有无需重生成的工作已闭合，但registry lock变化要求第三次独立的约140GB完整双clean generation，超出此前两次逐次授权，不能自行扩大传输与21个production JSON覆盖范围 | environment 142 packages、NeMo `3.1.0+c9040511b2`、network_blocked=true；定向`18 passed in 1.14s`；Ruff、mypy `224 source files`、diff通过；loader预期错误`model manifest environment differs from registry: nemotron_3_5_asr_streaming_0_6b` | 新worker环境、独立断网import、定向/静态输出与旧bundle防漂移证据；未提交 | 外部解除条件：用户明确授权新增一次约140GB完整双clean generation，并允许独立验证通过后再次覆盖20个manifest和`bundle.v1.json`共21个production JSON；获批后按B-29原样生成，不局部手改或复用payload绕过clean合同 |
| 2026-09-06 | Nemotron 3.5新Git闭包非production真实诊断越过load、batch infer失败 | 在不修改旧bundle/active/21个production JSON的临时隔离模型根中，仅在内存把旧manifest的environment SHA替换为新registry lock；正式manager/downloader/provisioner于24G/2G scope下载并校验2.37GB固定archive，具体RNNT prompt target成功restore、freeze、CPU load。健康batch进入真实`transcribe`后因adapter未传模型必需的语言prompt，NeMo把缺省值解析为`None`并拒绝；失败不是依赖、网络、内存或archive问题。临时模型根退出时已清理 | 诊断exit 1；真实load成功；错误`ValueError: Unknown prompt key: 'None'`，可用键含`en-US`/`zh-CN`；MemAvailable约28GiB，24G/2G scope未触发上限；production bundle仍按旧environment fail closed | `/tmp/classscribe-nemotron-diagnostic.py`、新Git环境与固定Nemotron 3.5 archive真实运行输出；未提交 | 批处理仅对具备`set_inference_prompt`的prompt模型传`target_lang=_prompt_language(language)`，并证明普通English checkpoint不接收额外参数；定向/静态通过后以同一非production诊断复验load→batch infer→unload，再继续真实streaming诊断 |
| 2026-09-06 | Nemotron prompt-aware batch修复定向完成 | batch re-decode现在仅在加载模型暴露`set_inference_prompt`时把请求语言映射为NeMo locale并传入`target_lang`；普通English模型继续只接收NeMo通用参数。集成测试在同一真实dispatch合同中先证明prompt模型收到`en-US`，再移除prompt能力并证明无额外字段，且原有cache streaming提示和生命周期保持不变 | sandbox外Nemotron定向`2 passed, 2 deselected in 0.18s`；Ruff通过；adapter mypy通过 | Nemotron adapter与streaming integration test；未提交 | 执行diff门；随后让adapter新source digest使用同一Git lock真实重建/复用环境，并在24G/2G隔离临时根中重跑固定2.37GB archive的load→batch infer→unload |
| 2026-09-06 | Nemotron prompt-aware batch第二次真实诊断仍失败 | 新adapter source digest的142包冻结环境成功构建，真实archive再次restore并进入batch；即使`transcribe(target_lang="en-US")`已传入，NeMo的Lhotse prompt dataset仍从自动生成的cut读取到显式`language=None`，该值覆盖dataloader的default language并再次报同一unknown prompt key。临时模型根已清理，未激活模型或修改production工件；24G/2G上限未触发 | 诊断exit 1；新source环境`46e073522ed7…`；错误仍为`Unknown prompt key: 'None'`；targeted/Ruff/mypy证据本身有效但未覆盖真实Lhotse输入层 | 第二次非production真实诊断输出、新source-addressed environment及固定NeMo data/manifest实现；未提交 | 逐行审计Git commit的`_setup_transcribe_dataloader`、Lhotse prompt dataset和自动manifest字段优先级；修复必须使用官方支持配置把cut language设为locale或禁用Lhotse，同时保持普通English模型兼容，补回归后第三次真实诊断 |
| 2026-09-06 | Nemotron prompt batch输入层修复定向完成 | 真实NeMo源码确认基类为字符串输入生成的临时manifest不含语言，而prompt RNNT默认Lhotse dataset读取supervision language；改为仅对prompt模型取得其官方`RNNTPromptTranscribeConfig`，固定batch/hypothesis、`use_lhotse=false`及locale。非Lhotse batch不产生错误prompt index，并由模型`_transcribe_forward`使用明确target；English普通模型仍无override config。集成测试精确验证四个config字段及普通模型无override | sandbox外Nemotron定向`2 passed, 2 deselected in 0.17s`；Ruff、adapter mypy、diff通过 | adapter/test与固定NeMo transcription/RNNT prompt源码；未提交 | 第三次非production真实诊断；若load→batch infer→unload通过，立即记录并构造不触碰production的真实cache streaming生命周期探针 |
| 2026-09-06 | Nemotron 3.5非production真实batch生命周期闭合 | 第三次固定archive诊断使用新source digest冻结环境；prompt RNNT具体target成功restore，非Lhotse batch以`en-US`明确prompt完成真实推理，health checker在runtime offline Bubblewrap中完成load→transcribe_batch→unload并报告健康。manager完成实际SHA/aggregate校验与临时active解析；production bundle/active/21个JSON仍未修改。成功后仅把已校验revision以同盘硬链接保留在专用诊断目录，供紧接着的streaming探针避免第四次下载 | exit 0；142 packages；source digest `106b184bd720…`；2,368,284,501 bytes；payload SHA `210214ed…6a74`；aggregate `30ce624f…a5d8`；health=true/backend `nemo_batch_redecode`/runtime_offline=true/CPU/VRAM 0；24G/2G scope未触发上限 | 第三次非production默认manager/downloader/provisioner/Bubblewrap诊断与retained hardlink tree；未提交 | 使用保留revision及同一冻结环境启动全新worker，真实执行load→stream_open→多次stream_push→stream_flush→stream_close→unload；通过或失败均立即登记，然后精确清理2.37GB诊断副本 |
| 2026-09-06 | Nemotron 3.5非production真实cached streaming首个push失败 | 新Bubblewrap worker成功load并stream_open；首个560ms PCM块进入真实`conformer_stream_step`后发生prompt/时间维张量不匹配，RPC稳定返回internal error，harness清理worker与streaming scratch但保留已校验archive供修复复验。没有batch回退、production写入或资源上限事件，故8.12第8项仍不能完成 | streaming exit 1；错误`expanded size ... (56) must match ... (9) ... Target [128, 56], Tensor [128, 9]`；失败发生在首个push的`nemo_cached_streaming`路径；24G/2G scope未触发上限 | 真实fixed-archive/new-Git worker、11块streaming harness与RPC错误输出；未提交 | 获取worker完整trace并审计`PromptStreamingMixin._apply_prompt_to_encoded`、`conformer_stream_step`输出形状及adapter的FeatureBuffer/chunk配置；修复必须保留真正cache stepping，定向测试加入张量形状合同后复验同一保留archive |
| 2026-09-06 | Nemotron streaming张量故障精确定位 | 同一archive/冻结环境在user+network namespace直接复现，完整cause chain证明异常尚未进入encoder或prompt融合，而发生于旧`StreamingFeatureBufferer._update_feature_buffer`：adapter为560ms块配置的buffer只预处理出9个mel帧，却试图填入56帧槽。NVIDIA固定模型卡明确560ms必须选择attention context `[56,6]`，固定commit官方脚本先设置该context，再使用`CacheAwareStreamingAudioBuffer`按模型计算的first/steady chunk、shift、pre-encode cache迭代；当前adapter既未选择context又误用了旧feature helper | 直接trace exit 1；精确栈为adapter update→NeMo `_convert_buffer_to_features`→`_update_feature_buffer`；模型卡列出80/160/320/560/1120ms与right context 0/1/3/6/13；官方固定脚本使用`set_default_att_context_size`+`CacheAwareStreamingAudioBuffer`+`conformer_stream_step` | NVIDIA固定revision模型卡、NeMo commit `c9040511…`官方streaming脚本及断网直接trace；未提交 | adapter改用官方cache-aware audio buffer：按请求chunk映射并验证模型支持的attention context，每stream保存buffer/cache/hypothesis/predictor；真实输入只增量预处理新PCM，仍用cache step且不累计重解历史。更新fake shape合同、两份协议文档后定向/静态与保留archive复验 |
| 2026-09-06 | 官方cache-aware buffer首轮定向门失败 | adapter已切换为按80/160/320/560/1120ms选择模型声明context，并用官方audio buffer增量append/iterate；首轮测试未进入行为断言，因为根测试venv本就不安装NumPy而fake未注入，两个load均在import numpy处失败。Ruff另正确拒绝fake Encoder的可变class list；组合mypy同时给adapter和导入它的test导致既有namespace模块双名，不是类型错误。diff通过，不能计修复完成 | pytest `2 failed, 2 deselected`，共同根因`ModuleNotFoundError: numpy`；Ruff `RUF012` 1项；组合mypy module-name collision；diff通过 | cache-aware adapter/test首轮输出；未提交 | 增加依赖无关的最小NumPy fake并把Encoder可变状态移入实例；按既定边界分别运行adapter mypy与测试定向/Ruff，全部通过后才执行真实archive |
| 2026-09-06 | 官方cache-aware buffer第二轮定向仅断言失败 | 注入最小NumPy fake并修复可变class状态后，恶意target拒绝测试通过，主生命周期的load/batch/open/两次push/flush/close/unload行为也完成；唯一失败是测试仍期待第三次audio append，但该场景未请求`confirmation=fast`，因此按既有合同走batch final re-decode而只发生两次cached append。Ruff、adapter mypy和diff通过，未出现实现异常 | pytest `1 failed, 1 passed, 2 deselected`；差异为期望3次append、实际2次；Ruff/mypy/diff通过 | 第二轮定向输出；未提交 | 把测试flush显式设为`fast`，使其真正覆盖尾块cache append与`fast_cached_flush`，再重跑定向/静态 |
| 2026-09-06 | 官方cache-aware adapter定向门闭合 | 测试现显式请求fast flush，精确覆盖模型声明context选择、首块stream id=-1、后续同stream增量append、首步/后续drop、attention/convolution cache、previous hypothesis/predictor、fast尾块和普通English batch无prompt override；恶意archive target仍拒绝。定向、Ruff、adapter mypy和diff全部通过 | sandbox外`2 passed, 2 deselected in 0.18s`；Ruff通过；mypy通过；diff通过 | Nemotron adapter与streaming integration tests；未提交 | 用保留的实际SHA archive让正式provisioner生成新source-addressed环境，在24G/2G scope/Bubblewrap中重跑11块560ms真实cache lifecycle；真实通过前不更新两份协议文档或阶段状态 |
| 2026-09-06 | Nemotron 3.5真实cache-aware streaming生命周期首次闭合；语言tag待清理 | 新source-addressed 142包环境在Bubblewrap/24G/2G scope中以模型声明的560ms `[56,6]` context成功load；91,939-sample英语WAV拆为11个增量PCM块，10次push得到非空结果，fast flush、close及unload均完成，backend严格为`nemo_cached_streaming`且未触发batch重解或资源上限。实际最终文本仍含模型自动附加的`<en-US>`特殊tag；NVIDIA官方脚本提供`decoding.set_strip_lang_tags(true)`，产品输出不应泄露控制tag，故技术流式闭合但8.12项暂不勾选 | exit 0；source digest `3224ab1e4e73…`；push_count=11/nonempty=10；fast_cached_flush=true；final `Class. <en-US> Mary. <en-US>`；CPU/VRAM 0 | 保留archive、正式provisioner、新worker/Bubblewrap streaming harness与NVIDIA固定模型卡；未提交 | 仅对prompt模型在load后调用官方decoder strip-language-tags开关并测试；再次复验真实batch与streaming输出均无`<locale>`控制tag，之后更新协议文档并清理2.37GB诊断副本 |
| 2026-09-06 | Nemotron官方语言tag清理定向完成 | prompt模型restore后必须暴露官方decoder `set_strip_lang_tags`并启用；缺失时load fail closed，普通English模型不进入该分支。既有测试现同时验证strip开关、非Lhotse batch prompt、context/audio buffer/cache/fast flush及恶意target拒绝 | sandbox外`2 passed, 2 deselected in 0.18s`；Ruff、adapter mypy、diff通过 | Nemotron adapter与integration test；未提交 | 扩展保留archive harness，在同一新worker load后先执行真实batch并断言无tag，再执行11块真实streaming并断言无tag；双路径成功后才更新文档与清理archive |
| 2026-09-06 | Nemotron语言tag清理后的真实双路径复验部分失败 | 最新source-addressed环境成功构建并加载同一固定archive；真实batch在启用官方decoder tag清理后返回非空且无`<en-US>`，随后11块560ms cache-aware流式调用全部RPC成功、fast flush仍报告真实cache backend，但push与flush均没有可发布文本，harness按严格断言exit 1。没有内存、swap、下载、production写入或batch回退问题；保留archive继续只用于定位 | exit 1；142 packages；错误`streaming lifecycle returned no meaningful text`；batch断言已通过；backend/fast-flush断言已通过；24G/2G scope未触发上限 | 扩展后的真实WorkerProcess/Bubblewrap双路径harness与新source环境输出；未提交 | 记录每个push/flush原始响应并对照固定NeMo官方streaming脚本的`best_hypotheses`/`transcriptions`选择及tag清理调用时机；修复须保持batch无tag、流式非空、真实cache stepping三项同时成立，再复验同一archive |
| 2026-09-06 | Nemotron无tag真实batch+cache streaming逐块复验通过；稳定性待重复 | 在同一固定archive、同一source environment和同一24G/2G边界下加入只读逐响应日志后重跑；batch返回完整英语句且无locale tag，11块560ms增量流式中7次push非空，fast flush保留最终非空文本且无tag，close/unload成功。全过程仍为官方audio buffer与cache state stepping，没有batch fallback。不过紧邻的上一轮相同语义输入曾全空，必须再独立重复一次排除不稳定后才更新协议文档或清理archive | exit 0；source digest `2e3ac92fce5c…`；batch `Class Ribe verifies real speech recognition and language identification.`；push_count=11/nonempty=7；final `Sprach. Winter.`；backend `nemo_cached_streaming`、fast_cached_flush=true；24G/2G scope未触发上限 | 真实WorkerProcess/Bubblewrap逐块输出、固定WAV/archive与冻结环境；未提交 | 不改实现，以完全相同harness再跑一次独立load→batch→11 push→fast flush→close→unload；若仍通过则把首次全空保留为诊断历史并更新协议文档，若复现则继续定位随机源/模型状态 |
| 2026-09-06 | Nemotron无tag真实双路径独立重复通过 | 完全相同的固定WAV/archive、source environment和24G/2G边界下，再启动全新worker并独立完成load→batch→open→11 push→fast flush→close→unload；batch再次给出同一非空无tag文本，10次push非空，flush为非空无tag且严格报告cache backend。两次成功运行的流式词面不同，结合此前一次全空，说明CPU实时解码输出存在非确定性，不能作为准确率或性能benchmark；但连续两次独立生命周期已证明依赖、官方buffer、cache carry、fast final与tag清理合同可执行 | exit 0；source digest `2e3ac92fce5c…`；batch同前；push_count=11/nonempty=10；final `Class. Scarry a.`；backend `nemo_cached_streaming`、fast_cached_flush=true；24G/2G scope未触发上限 | 第二次独立真实WorkerProcess/Bubblewrap逐块输出；未提交 | 更新worker protocol/IBus/阶段进度文档为`CacheAwareStreamingAudioBuffer`与模型声明context；随后跑定向、Ruff、mypy、diff门并精确清理2.37GB诊断副本；production 8.12仍须新bundle授权后按正式manifest安装/断网复验 |
| 2026-09-06 | Nemotron cache-aware实现、文档与本地门闭合 | 三份实现说明已与真实路径一致：五档chunk只选择模型声明的right context，官方audio buffer只增量接收新PCM，各stream独立保存buffer及attention/convolution/length/RNNT/predictor状态，fast仅处理补齐尾块。最终定向测试同时覆盖prompt/tag、context/buffer/cache/fast和恶意target拒绝；静态与diff门全绿。该完成只关闭无需production重生成的实现与非production实模诊断，不把CPU词面或旧bundle冒充8.12正式验收 | pytest `2 passed, 2 deselected in 0.16s`；Ruff通过；adapter严格mypy通过；`git diff --check`通过；真实双路径连续两次exit 0 | `workers/nemotron/adapter.py`、streaming integration tests、`docs/worker-protocol.md`、`docs/ibus.md`、阶段开发进度文档及真实输出；未提交 | 确认无残留worker后精确删除专用`nemotron-diagnostic-retained`和可能的streaming scratch；随后审计唯一计划剩余可执行项。新bundle/正式Nemotron双checkpoint仍等待第三次约140GB生成与21 JSON覆盖明确授权 |
| 2026-09-06 | Nemotron专用真实诊断payload清理完成 | 确认没有运行中的Nemotron Python worker，streaming scratch已由harness清除；随后仅删除本轮创建且已完成取证的`nemotron-diagnostic-retained`目录，包括2,368,284,501-byte archive与1,821-byte supply-chain副本。production模型根、worker环境cache、bundle与21个JSON均未触碰；该删除不可恢复，但原fixed revision仍可在获得下一轮完整生成授权后由官方源重取和校验 | 删除前目录2.3GiB、archive mode0600/nlink1；删除exit 0；删除后retained与streaming scratch均不存在；证据根现159GiB | 精确删除前后探针与进程盘点；未提交 | 读取8.12/阶段门/测试矩阵当前状态，继续所有不依赖新production bundle的可执行回归；若不存在，则保持旧bundle fail closed并向用户报告第三轮生成的精确授权边界 |
| 2026-09-06 | 剩余门审计与English Nemotron非production诊断准备完成 | A～D最终回归必须消费与新Nemotron lock一致的bundle，旧bundle按合同应拒绝；F进入条件依赖E，不能越序。第二个English Nemotron checkpoint尚可在不写production的条件下先证明新Git环境与同一adapter兼容，因此把既有临时manager诊断器参数化为精确model ID：只读取旧manifest的repository/revision/file SHA，在内存替换新lock，下载至0700临时根并由正式manager/health/Bubblewrap验证，成功后仅暂存同盘副本供流式复验 | English fixed revision `ebe59e5a…ed50`、1 file/2,473,041,920 bytes、payload SHA `28363805…c9cd`；临时harness Ruff、严格mypy、py_compile全通过；production未变 | 唯一计划门审计、旧manifest固定身份与参数化临时harness；未提交 | 在24G/2G scope中联网执行English checkpoint request→download/hash→新环境→CPU offline health→active resolve；成功/失败立即登记，绝不写21个production JSON |
| 2026-09-06 | English Nemotron非production真实batch健康闭合 | 参数化诊断器从官方固定revision实际下载单一archive并逐字节匹配旧manifest payload身份；仅在内存替换新Git lock后，正式manager/provisioner/health checker完成预检、临时安装、Bubblewrap无网络load→真实英语batch infer→unload、临时active解析及aggregate复验。结果健康且CPU执行，未修改production bundle/active/21个JSON；已校验revision暂以同盘硬链接保留供紧接的流式验证 | exit 0；2,473,041,920 bytes；payload SHA `283638054c44…c9cd`；aggregate `cccfcfb69eea…65b3`；revision `ebe59e5a…ed50`；health=true、backend `nemo_batch_redecode`、runtime_offline=true、CPU/VRAM 0；24G/2G scope未触发上限 | English fixed archive、新Git/source-addressed环境、正式manager/health/Bubblewrap输出；未提交 | 参数化真实streaming harness指向本English retained revision，启动新worker执行load→batch→11块560ms push→fast flush→close→unload；通过后立即登记并精确清理2.47GB副本 |
| 2026-09-06 | English Nemotron非production真实cache streaming闭合 | 用保留且已校验的固定archive启动全新Bubblewrap worker；普通English模型不进入prompt/tag专用分支，但与3.5共用官方cache-aware实现。真实英语batch非空；91,939 samples按11块560ms增量输入，7次push非空，fast flush返回非空最终文本并严格报告cache backend，随后close/unload成功。没有batch fallback、production写入或资源上限事件；这证明新Git lock/source adapter同时兼容两个Nemotron checkpoint，但仍不替代新bundle下的正式安装及独立断网重启 | exit 0；source digest `2e3ac92fce5c…`；batch `Class tribe verifies real speech recognition and language identification.`；push_count=11/nonempty=7；final `Class very cleanly and with a.`；backend `nemo_cached_streaming`、fast_cached_flush=true；CPU/VRAM 0 | English fixed archive、正式WorkerProcess/Bubblewrap逐块输出；未提交 | 确认无残留worker，精确删除2.47GB English retained副本；随后再跑最终定向/static/diff与旧bundle fail-closed探针，若无其他独立项则进入第三次production生成授权边界 |
| 2026-09-06 | English Nemotron专用诊断payload清理完成 | 确认English真实worker已unload/退出，harness streaming scratch不存在；随后仅删除本轮创建的`nemotron_speech_streaming_en_0_6b-diagnostic-retained`，包括2,473,041,920-byte archive和1,824-byte supply-chain副本。production模型、21个JSON和source-addressed worker环境均保持；删除不可恢复，固定源可在后续正式生成中重取 | 删除前目录2.4GiB、archive mode0600/nlink1；删除exit 0；删除后retained/scratch均不存在且无Nemotron Python worker；证据根159GiB | 精确删除前后与进程探针；未提交 | 最终运行Nemotron定向/Ruff/mypy/diff、worker lock冻结和旧bundle精确fail-closed探针；根据结果更新阶段阻塞边界 |
| 2026-09-06 | Nemotron两checkpoint本地闭包最终门通过；第三次production生成授权为唯一下一步 | prompt-aware 3.5与普通English两个fixed archive均已在新Git lock/source adapter上完成真实batch和cache-aware streaming非production生命周期，临时payload全部清理。最终定向、静态、冻结lock及diff门全绿；真实loader继续精确拒绝仍冻结旧Nemotron environment的production bundle，证明A～D回归、正式8.12安装/断网重启和F均不能在旧bundle上诚实继续。当前21个production JSON保持原样，未使用局部手改或旧payload绕过双clean合同 | pytest `2 passed, 2 deselected in 0.16s`；Ruff通过；严格mypy 2 files通过；Nemotron lock `143 packages`且SHA `b2f372c4…0505`；diff通过；fail-closed probe exit 0并捕获`model manifest environment differs from registry: nemotron_3_5_asr_streaming_0_6b`；21 files全0644；旧bundle `45fd541d…e0b6`；约28GiB memory available、项目盘272GiB available | 两checkpoint真实诊断、最终本地门、production目录/资源盘点与loader输出；未提交 | 外部解除条件仍是第三次逐次明确授权：新增约140GB完整双clean generation，并允许独立验证通过后再次覆盖现有20 manifests与`bundle.v1.json`共21个production JSON。获批后按B-29原样执行，再依序独立offline/frozen验证、A→B→C→D全回归、两个Nemotron正式安装/断网重启及E/F |
| 2026-09-06 | 第三次production授权第一轮连续阻塞复核 | 自动续跑后从头完整复读唯一计划至EOF，并只读核对当前工作树、production目录、资源和进程。没有生成器或Nemotron诊断进程；旧bundle及两个Nemotron manifest仍冻结旧worker lock，真实loader在首个Nemotron精确fail closed。磁盘和内存均充足，故当前不是资源或上次Granite内存压力阻塞；A～D、正式Nemotron验收和F仍共同依赖第三次完整生成，不能用既往两次授权外推 | 计划1808行完整复读；bundle `45fd541d…e0b6`；新lock `b2f372c4…0505`、旧manifest lock `5206e492…1a39`；loader exit 1且精确报`model manifest environment differs from registry: nemotron_3_5_asr_streaming_0_6b`；21个JSON全0644；generator进程检索无输出；项目盘291,837,980,672 bytes可用、MemAvailable 30,376,943,616 bytes | 唯一计划、production/lock SHA、目录权限、资源/进程与loader只读输出；未提交 | 同一外部解除条件第一轮连续成立：需用户明确授权第三次新增约140GB完整双clean generation，并允许独立验证通过后再次覆盖20 manifests与bundle共21个production JSON；未获批不启动下载或覆盖 |
| 2026-09-06 | 第三次production授权第二轮连续阻塞复核 | 重新读取总体状态、最新阶段E记录与F进入条件并复核权威现场；仍无generator/Nemotron诊断进程，production bundle和新Nemotron lock字节未变，21个JSON权限闭包不变。真实loader再次在同一Nemotron environment drift处拒绝。未发现可在严格阶段依赖下独立推进的新任务，且当前资源充足，阻塞仍仅是第三次大流量生成及广泛覆盖的明确授权 | bundle `45fd541d…e0b6`；Nemotron lock `b2f372c4…0505`；loader exit 1并报`model manifest environment differs from registry: nemotron_3_5_asr_streaming_0_6b`；21个JSON全0644；进程检索无输出；项目盘291,839,438,848 bytes可用、MemAvailable 30,344,761,344 bytes | 唯一计划、production/lock SHA、权限、进程/资源与loader当前输出；未提交 | 同一解除条件第二轮连续成立：需用户明确授权第三次新增约140GB完整双clean generation，并允许独立验证通过后再次覆盖20 manifests与bundle共21个production JSON；本轮不启动下载、不手改production工件、不越序进入F |
| 2026-09-06 | 第三次production授权第三轮连续阻塞审计；持续目标正式阻塞 | 第三次连续goal turn复核同一外部条件：没有用户对Nemotron修复后的第三次约140GB完整双clean generation及再次覆盖21个production JSON的明确授权，也没有可轮询的生成/诊断进程。旧bundle、新lock、21文件权限和阶段依赖均未变化，真实loader仍精确拒绝首个Nemotron environment drift；项目磁盘与内存充足，Granite float32历史风险不构成本轮阻塞。所有不依赖重生成的两个Nemotron checkpoint真实batch/cache streaming、定向、静态与lock门已闭合，不能继续有意义推进或越序进入F | bundle `45fd541d…e0b6`；Nemotron lock `b2f372c4…0505`；loader exit 1并报`model manifest environment differs from registry: nemotron_3_5_asr_streaming_0_6b`；21个JSON全0644；进程检索无输出；项目盘291,838,955,520 bytes可用、MemAvailable 30,341,685,248 bytes | 唯一计划、连续三轮阻塞记录、production/lock SHA、权限、进程/资源与loader当前输出；未提交 | 解除条件不变：用户明确授权第三次新增约140GB完整双clean generation，并允许独立验证通过后再次覆盖20 manifests与`bundle.v1.json`共21个production JSON；收到授权后恢复目标并按B-29原样执行，不重开已闭合本地工作 |
| 2026-09-06 | 第三次production生成授权解除；阶段B/E恢复进行中 | 用户明确指示今后无需再为同类操作手动授权、直接开始且不得因授权停下；该指示覆盖当前第三次约140GB完整双clean generation、独立验证通过后覆盖20 manifests与bundle共21个production JSON，也作为后续同类计划内生成/覆盖的持续授权。此前外部阻塞解除；生成仍严格使用固定五方输入、`SOURCE_DATE_EPOCH=1788451200`、Community-1 gated声明、两个空clean workspace和原子发布，不扩大到计划外操作 | 用户本轮明确持续授权；生成前bundle `45fd541d…e0b6`、Nemotron lock `b2f372c4…0505`；项目盘291,844,861,952 bytes、MemAvailable 30,052,909,056 bytes；无既有generator进程 | 用户指示、B-29接口与生成前资源/进程审计；未提交 | 创建项目内0700私有临时根，在24G RAM/2G swap scope中启动完整generate并持续轮询；exit 0后先独立offline/frozen verify及21文件SHA/权限/provenance审计，再恢复阶段B并按A→C→D回归 |
| 2026-09-06 | 第三次完整production双clean generation运行中 | 使用固定五方输入、`SOURCE_DATE_EPOCH=1788451200`、用户已接受的Community-1 gated声明及项目内0700私有`TMPDIR`，在`MemoryMax=24G`/`MemorySwapMax=2G`用户scope中启动B-29完整generate；当前首个18文件模型已开始第一轮实际下载。命令保持原子发布合同，尚未覆盖production JSON，不能计生成成功 | 持续session 21497；systemd scope `run-p54322-i72512.scope`；初始进度18 files 1/18并进入8.13GB reconstruction；启动前旧bundle `45fd541d…e0b6` | B-29真实联网生成进程、scope与初始输出；未提交 | 持续轮询同一session并监控资源；仅以明确exit 0、`status=verified`和`model_count=20`判定生成成功，失败立即登记且不重启冒充clean双run |
| 2026-09-06 | 生成scope首次资源探针受限；外层只读复核成功 | 首次从受限sandbox调用`systemctl --user show`因无法连接user bus而exit 1，只是观察层失败，没有向生成session发送字符或改变scope；随后在允许的只读执行边界对同一unit重试，确认仍active/running且内存/swap显著低于硬上限 | 首次`Failed to connect to user scope bus ... Operation not permitted`；重试exit 0：MemoryCurrent 10,066,112,512、MemoryPeak 10,365,956,096、MemorySwapCurrent/Peak 0 | 同一session 21497、scope `run-p54322-i72512.scope`与两次监控输出；未提交 | 不重启生成，继续轮询同一session；后续systemd资源探针直接使用只读外层边界 |
| 2026-09-06 | 第三次完整production双clean generation成功；独立验证待执行 | 同一受控session完整处理20个固定revision模型，每项均完成两个clean workspace的实际下载、重建与字节一致性检查；生成器以exit 0和`status=verified`结束并原子发布20个manifest及bundle。新production目录恰有21个普通JSON且全为0644；本条只记录生成成功，不提前恢复B/E门 | session 21497 exit 0；新bundle `55069eea4fd82af5132a708ce1658217a652d4af16003352f7f15593e5c11357`、20成员；Nemotron 3.5 manifest `4df2371465af…1362`、English manifest `494f48f80b7b…c4c1`；scope观测峰值15,047,180,288 bytes、swap peak 0；结束后私有TMPDIR仅余0-byte uv lock | B-29真实终止输出、新production SHA/数量/权限、scope与精确TMPDIR盘点；未提交 | 立即运行独立`--offline --frozen verify`并审计bundle成员SHA、五方合同、两个Nemotron新environment、18个非Nemotron稳定性及pyannote多来源provenance；全部通过后才恢复阶段B并精确清理TMPDIR |
| 2026-09-06 | 第三次production独立离线验证与附加审计通过；B完整回归待执行 | 在生成进程退出后的独立工具进程中以`--offline --frozen`验证新bundle，20成员、manifest实际SHA、registry/revision/license/worker/状态合同全部通过；两个Nemotron manifest均精确冻结新Git worker lock，除Nemotron外18个manifest与上一production证据表逐项无差异；pyannote仍为5个installed文件加1个`derived` WeSpeaker来源。目录恰有21个普通0644 JSON，排序/唯一、隐私扫描均通过。两条最初只读审计表达式分别因jq管道优先级和旧表行范围选择错误退出，修正表达式后通过，不是工件失败 | independent verify exit 0、`status=verified`、`model_count=20`、bundle `55069eea…1357`；20/20 `sha256sum -c` OK；bundle sorted=true/unique=true；Nemotron lock实际/两manifest均为`b2f372c4…0505`；18项diff为空；pyannote `5 + 1 derived`且gated=true；privacy scan clean | 独立verifier输出、bundle/lock/旧证据表diff、目录与provenance审计；未提交 | 精确清理仅含0-byte uv lock的私有TMPDIR；随后重跑B-01～B-35完整工具/fake-Hub/lock/static/release/architecture/diff门，全部通过后恢复阶段B，再严格按A→C→D执行 |
| 2026-09-06 | 第三次生成私有TMPDIR精确清理完成 | 结束后目录仅含生成器遗留的0-byte uv lock；先以精确文件补丁删除该锁，再对已知空目录执行`rmdir`成功。没有递归删除、没有触及共享uv cache、production JSON或其他证据树 | 删除目标`.manifest-generation-20260906.1ppPy3/uv-de488ad5dc35fee4.lock`为0 bytes；`rmdir` exit 0 | 生成后盘点、精确删除与空目录移除输出；未提交 | 执行阶段B完整回归门 |
| 2026-09-06 | E进度登记补丁首次上下文失配 | 尝试同时更新8.15行并在阶段E表尾登记正式Nemotron安装时，末尾锚点仍引用阶段B表中的同名记录，`apply_patch`整体校验失败且没有部分写入；工件、active和运行证据均未改变 | `apply_patch verification failed`；随后只读定位阶段E实际表尾 | 计划编辑失败输出；未提交 | 分成精确8.15单行替换与阶段E表尾插入两个补丁，不重复模型运行 |
| 2026-09-06 | 8.12-8 Nemotron 3.5 新bundle正式首次安装与CPU离线batch健康通过；重启/streaming待验 | 默认service从新production bundle完成预检，以固定revision下载并逐字节校验唯一根级`.nemo` archive；复用同Git lock/source闭包后，24G/2G scope内真实无网络Bubblewrap完成具体prompt RNNT target load→英语batch infer→unload，随后原子激活并由`verify_model`复验。没有使用旧manifest或内存替换 | preflight required 5,210,225,902、available 291,827,261,440 bytes；bundle `55069eea…1357`；manifest `4df23714…1362`；revision `1c8deaec…395d`；1 file/2,368,284,501 bytes；aggregate `30ce624f…a5d8`；health=true/backend `nemo_batch_redecode`/runtime_offline=true | 正式production downloader、manager、Git worker环境、Bubblewrap与active输出；未提交 | 本模型尚不闭合：独立新core/user+network namespace重跑batch，再从正式active执行真实cache-aware stream lifecycle；通过后更新8.15并继续English checkpoint |
| 2026-09-06 | Nemotron 3.5 正式独立断网batch通过；首次streaming harness路径错误 | 独立新core/user+network namespace从正式active和现有source环境成功完成第二次load→batch infer→unload，network blocked且不下载/重建。随后临时streaming harness为适配UUID宿主文件名错误改写了沙箱内固定`/input/audio`挂载名，worker在load后正确以`invalid_request`拒绝不存在路径；未进入batch/streaming推理，不计流式通过。finally清理scratch/worker，无active变化 | restart exit 0；bundle `55069eea…1357`；health=true/backend `nemo_batch_redecode`/runtime_offline=true；scope观测MemoryPeak 8,666,804,224 bytes、swap 0；streaming exit 1：`audio_path must name an existing regular non-symlink file` | 正式active的断网新core输出、临时harness失败与清理盘点；未提交 | 恢复沙箱固定`/input/audio`路径，保留只`resolve`现有环境与正式active语义，Ruff/mypy后原样重跑cache-aware生命周期 |
| 2026-09-06 | Nemotron 3.5 正式production batch、独立断网重启与cache streaming完整闭合 | 恢复WorkerSandbox固定只读音频挂载名后，临时harness静态门通过；全新user+network namespace仅resolve既有Git source环境并从正式active启动worker。真实batch返回非空无tag文本，11个560ms增量push中10次非空，fast flush严格报告cached backend，随后close/unload成功；没有batch fallback、网络、provisioning或残留worker | streaming exit 0；batch `Class Ribe verifies real speech recognition and language identification.`；push_count=11/nonempty=10；final `Class.`；backend `nemo_cached_streaming`、fast_cached_flush=true；scope观测MemoryPeak 8,645,492,736 bytes、swap 0；payload SHA `210214ed…6a74`、aggregate `30ce624f…a5d8`、staging/scratch为空 | 正式active、独立断网新worker逐块响应、supply-chain/active/payload与清理审计；未提交 | 本checkpoint闭合；严格继续`nemotron_speech_streaming_en_0_6b`正式预检、固定下载、CPU batch、独立断网重启和cache streaming同等级验收 |
| 2026-09-06 | 8.12-8 English Nemotron 新bundle正式首次安装与CPU离线batch健康通过；重启/streaming待验 | 默认service从新production bundle预检，以固定revision下载并逐字节校验唯一根级`.nemo` archive；同一Git lock/source闭包在24G/2G scope内完成无网络Bubblewrap load→英语batch infer→unload、原子active与`verify_model`。普通English模型不进入prompt/tag专用分支；没有使用非production内存替换 | preflight required 5,440,692,224、available 289,458,880,512 bytes；bundle `55069eea…1357`；manifest `494f48f8…c4c1`；revision `ebe59e5a…ed50`；1 file/2,473,041,920 bytes；aggregate `cccfcfb6…65b3`；health=true/backend `nemo_batch_redecode`/runtime_offline=true | 正式production downloader、manager、Git worker环境、Bubblewrap与active输出；未提交 | 本模型尚不闭合：独立新core/user+network namespace重跑batch，再从正式active执行真实cache-aware stream lifecycle |
| 2026-09-06 | 完成8.12第8项与当前14个实际使用模型CPU真实健康矩阵 | English checkpoint在独立新core/user+network namespace中先从正式active完成第二次batch，再由全新worker仅resolve现有Git环境执行真实batch+11块560ms cache streaming；7次push非空，fast flush非空且严格为cached backend，close/unload成功。active、唯一archive SHA、aggregate、supply-chain、staging/scratch与残留进程审计均通过。结合3.5同等级证据，两个Nemotron及当前14个实际使用模型的CPU首次/断网重启均闭合；CPU词面不冒充准确率或性能benchmark | restart exit 0、network_blocked=true、health=true/backend `nemo_batch_redecode`；streaming exit 0；batch `Class tribe verifies real speech recognition and language identification.`；push_count=11/nonempty=7；final `Class very cleanly and with a.`；backend `nemo_cached_streaming`、fast_cached_flush=true；payload SHA `28363805…c9cd`、aggregate `cccfcfb6…65b3`；两个scope Result=success | English正式active、独立断网batch/逐块stream输出、supply-chain/payload与清理审计；未提交 | 复核8.12第9/10项仅含worker未实现或registry disabled模型的准确fail-closed状态，以及GPU子矩阵边界；满足E退出条件后进入F |
| 2026-09-06 | 阶段 E 完成 | production目录恰含20个manifest与bundle且独立offline/frozen verifier通过；当前实际使用的14个模型在同一目标Fedora均已有固定revision首次安装、无网络对应任务infer、active/supply-chain及独立重启证据。默认service再次确认TurboCTC为worker未实现、FunASR/Whisper为disabled、FireRed LLM/Voxtral/VibeVoice为disabled且worker未实现，全部在下载前fail closed。GPU driver仍不可用，作为独立GPU子矩阵风险保留，不影响CPU健康与8.14要求的当前profile闭合，也不被CPU结果冒充 | bundle `55069eea…1357`、independent verifier 20/20；E证据根恰有14个active；两个Nemotron正式batch/streaming/restart均exit 0；状态探针逐项返回`manifest_available=true`且预期`worker_implemented/installable/enabled/reason`；`nvidia-smi`仍exit 9 | production bundle、8.15全表、14个active、默认service状态探针与GPU风险记录；未提交 | 8.3～8.14退出语义闭合，阶段E完成；进入阶段F，release readiness仍须按F-32/33保持与准确率/桌面/GPU真实门独立 |

## 9. 阶段 F：发布闭环

状态：**已完成**

### 9.1 阶段目标

把完整 bundle 纳入 release inventory、release check 和 RPM installed tree，完成 source/installed-tree
双重验证、全套测试和文档同步；manifest 工程完成本身不得解除准确率、桌面或真实 Phase 12 发布门。

### 9.2 进入条件

- [x] 阶段 E 的 20 模型 bundle 已生成并通过离线 verifier。
- [x] 所有 enabled 且进入当前 profile 的模型健康证据已具备；不能具备的项目已按 fail-closed 记录阻塞。
- [x] pyannote 多来源、gated 授权及 disabled/未实现模型状态均已准确表达。

### 9.3 Release inventory 与 release check

更新 `release/release-manifest.v1.json`，至少加入：

- [x] `F-01` `protocol/schema/v1/model-manifest-bundle.schema.json`；
- [x] `F-02` `config/model-file-selection.v1.yaml`；
- [x] `F-03` `config/model-manifests/v1/bundle.v1.json`；
- [x] `F-04` 20 个单模型 manifest，或由 bundle verifier 安全展开的完整列表；
- [x] `F-05` manifest tool 的 `pyproject.toml` 和 `uv.lock`；
- [x] `F-06` bundle loader 和 verifier 源码。

- [x] `F-07` release check 从 bundle 索引安全解析成员并逐个验证，不得只检查目录存在。
- [x] `F-08` release check 验证 registry、revision lock、license inventory、selection、worker lock 与 bundle
  的一一对应关系。
- [x] `F-09` 删除任一 manifest、修改任一 byte、更改 registry revision、添加额外成员或改变成员 SHA
  都必须阻止发布。
- [x] `F-10` manifest tool 的 frozen lock 进入依赖锁检查。

### 9.4 RPM

RPM 当前 `cp -a config` 已可携带 bundle，无需新增权重包。新增 RPM 测试必须验证 installed tree：

- [x] `F-11` bundle 索引存在且不可写；
- [x] `F-12` 20 个 manifest 齐全；
- [x] `F-13` loader 可以从 `/usr/share/classscribe` 解析；
- [x] `F-14` RPM 不包含模型权重、HF token、Hugging Face cache 或生成临时目录；
- [x] `F-15` upgrade 后新 bundle 生效，用户 XDG 模型 revision 不被删除；
- [x] `F-16` install/upgrade/erase 均保留现有用户模型和其他用户数据；
- [x] `F-17` installed-tree bundle 与 source tree 字节和哈希一致。

### 9.5 文档同步

- [x] `F-18` 更新 `docs/model-installation.md`：内置 bundle、remote-code 原始布局、生成/安装权限边界。
- [x] `F-19` 更新 `docs/user-guide.md`：用户不再选择 manifest 文件。
- [x] `F-20` 更新 `docs/openapi.md`：新的 request/response。
- [x] `F-21` 更新 `docs/security.md`：发布期生成器联网与运行期离线边界。
- [x] `F-22` 更新 `docs/known-limitations.md`：真实模型未验收前继续保留阻塞。
- [x] `F-23` 更新 `docs/ClassScribe分阶段开发进度.md`：先纠正“manifest 已冻结”的不实状态，只有
  完成后才恢复为已完成。
- [x] `F-24` 更新 `docs/requirements-traceability.md`：把 bundle、生成器、verifier、RPM 和真实健康
  检查映射到测试证据。

### 9.6 发布与回归测试

- [x] `F-25` RPM install/upgrade/erase 保留现有用户模型。
- [x] `F-26` installed-tree bundle 完整且与 source tree 哈希一致。
- [x] `F-27` release check 在删除任一 manifest、修改任一 byte 或更改 registry revision 时失败。
- [x] `F-28` bundle verifier 在完全离线环境运行并通过。
- [x] `F-29` bundle 升级展示 added/removed/changed/remote-code-changed 文件 diff；升级失败恢复旧 active。
- [x] `F-30` packaged resource root 与 source resource root 的加载、校验和错误语义一致。
- [x] `F-31` 重跑单元、合同、集成、前端、RPM、发布检查、静态检查和全仓回归。
- [x] `F-32` release readiness 的原始 Phase 12 检查在没有真实模型验收时保持 false，不因 manifest
  存在自动变成通过；没有精确有效 waiver 时整体仍保持 blocked。
- [x] `F-33` 只有真实 Phase 12 数据才能把原始检查改为通过；显式 waiver 只能改变 effective gate 和
  自动退出码，并以 `ready_with_waivers` 与真实全绿区分。
- [x] `F-34` 新增独立、规范化的 release-waiver 工件，并由 release manifest 以路径和 SHA-256 固定；
  waiver 必须绑定当前 release version、facts cutoff 和 registry revision，且只能精确列出用户授权的
  `source_license`、`phase12_acceptance`、`desktop_matrix`，禁止 wildcard、额外 gate 或宽泛继承。
- [x] `F-35` release checker 同时输出原始 `checks`、waiver 后 `effective_checks`、`waived_gates` 和
  `blocking_reasons`；三项原始检查及其事实原因不得改写为通过。只有 waiver 完整有效且覆盖全部剩余
  失败时返回独立状态 `ready_with_waivers`，CLI 才以退出码 0 放行；无 waiver 的全绿仍为 `ready`。
- [x] `F-36` source tree 与 installed tree 必须读取同字节 waiver，RPM inventory 包含该工件；篡改、
  symlink、缺失、SHA 不符、版本/事实/registry 漂移、acknowledgement 缺失或越权 gate 均 fail closed。
- [x] `F-37` 更新冻结 readiness、release/RPM 测试和相关发布文档，并重跑受影响定向门及第 10 节完整
  测试矩阵；waiver 只解除自动 release blocking，不授予源码再分发权、不声称 Phase 12 已通过，也不
  声称桌面兼容性已验证。

### 9.7 退出条件

删除或篡改任意 bundle 成员都会阻止发布；RPM 不携带权重或凭证；source tree、RPM installed tree 与
release inventory 的 bundle 字节一致；用户数据升级和卸载合同保持；完整测试通过。非 manifest 发布门
的原始证据仍须 fail closed；若采用 waiver，只能由版本绑定且完整验证的显式工件解除自动阻塞，并以
`ready_with_waivers` 与无 waiver 的真实全绿 `ready` 明确区分。

### 9.8 进度记录

| 日期 | 状态/任务 ID | 变更或结论 | 验证命令与结果 | 提交/证据 | 阻塞与下一步 |
|---|---|---|---|---|---|
| 2026-09-04 | 未开始 | 当前 release inventory/RPM 尚未纳入生产 bundle | 未运行实施验证 | 本文件 | 阶段 E 完成后开始 `F-01` |
| 2026-09-06 | F release/RPM 首轮功能通过，静态门失败 | release inventory 与 release check 已纳入 schema、selection、bundle、20成员、tool lock、loader/verifier，并新增7类篡改阻断；RPM测试已验证不同旧bundle到production bundle的真实upgrade、21个JSON逐字节一致、0644、无权重/cache/token路径及XDG哨兵保留。功能测试通过，但严格mypy发现RPM测试复用`path`变量造成2个类型错误；组合命令末尾成功不能作为静态通过 | release unit + RPM `18 passed in 7.57s`；Ruff通过；mypy `2 errors` | release checker、inventory、unit/RPM tests；未提交 | 重命名循环变量后单独重跑mypy与全部定向门；未通过前不勾选F任务 |
| 2026-09-06 | F-08 selection 独立release gate首轮静态失败 | 复读任务发现release check此前只盘点selection存在，故新增重复键拒绝、严格结构/安全路径和20模型include/kind逐项对照；Ruff通过，但mypy无法从布尔路径助手自动收窄4处可空/Any值，未计完成 | Ruff通过；严格mypy `4 errors`、命令fail fast未运行后续pytest | release checker与selection攻击测试；未提交 | 对已验证分支显式cast为`list[str]`/`dict[str,str]`并为bundle使用具体类型，然后单独重跑 |
| 2026-09-06 | F 最终矩阵首轮受限边界失败 | 并行启动root与manifest-tool全量时，受限sandbox禁止创建Unix/INET socket：root clean-XDG core在lease Unix socket bind处首个失败；tool的7个local fake-Hub用例在`127.0.0.1` bind处报EPERM，其余142项通过。没有代码合同失败，也不能把部分通过计作全量通过 | root fail-fast `1 failed, 23 passed`；tool `142 passed, 7 errors`；均为`PermissionError: Operation not permitted` | 最终矩阵首轮输出；未提交 | 在允许本机socket的边界原样重跑；tool已重跑为`149 passed in 4.15s`，root全量待执行 |
| 2026-09-06 | F 根全量外层仅RPM6环境失败 | 允许本机socket后489项完整执行；所有非RPM行为与两个packaging静态测试通过，唯一失败仍为已登记的外层Fedora RPM6自定义db `.rpm.lock` permission。新增双bundle RPM生命周期在默认隔离边界此前已真实通过，不能用外层487项冒充全绿 | `1 failed, 487 passed, 1 skipped in 24.47s`；唯一失败`test_rpm_install_upgrade_remove_preserves_user_data`/rpm exit255 | 根全量输出与既有RPM风险记录；未提交 | 外层重跑排除整个3项packaging文件，默认边界单独重跑该文件3项；两结果合并覆盖原489项 |
| 2026-09-06 | F-01～F-33与阶段F全部完成 | release inventory/check逐项验证bundle、selection、registry/revision/license/worker locks和tool lock；9类篡改/缺件阻断。双bundle RPM真实install→upgrade→erase验证21 JSON、0644、source字节、无权重/凭证/cache/temp及XDG保留。六份文档、静态readiness与追踪矩阵同步；Phase12/desktop/source-license仍独立blocked | release/RPM定向`20 passed`；root按权限拆分`485 passed, 1 expected skip`+RPM/packaging`3 passed`；tool`149 passed`；frontend`17 passed`+五门；offline verifier 20/SHA`55069eea…1357`；Ruff、mypy 224+19、architecture、root/tool locks、JSON、diff全绿 | production bundle、release/RPM实现、文档、最终矩阵；未提交 | 阶段F完成；manifest修复目标闭合。正式发布签核仍等待源码许可证、真实Phase12质量/性能与桌面矩阵，不得以本工程结果解除 |
| 2026-09-06 | F 标记需回归；开始 `F-34` | 用户明确授权一次 release-blocking waiver，精确跳过 `source_license`、`phase12_acceptance`、`desktop_matrix`。该策略变化触及 release checker、冻结 readiness、release inventory 与 RPM installed-tree 合同；原始三项失败和详细原因必须保留，不得直接翻转 `checks` 或伪造证据 | 已完整复读唯一计划与现有 source/installed checker、CLI、readiness、release manifest、RPM 测试；尚未修改产品实现或取得新测试结果 | 用户明确授权、当前 7/10 blocked 基线与本计划；未提交 | 先实现严格 waiver 工件/解析/报告模型和攻击测试；通过定向门后更新 installed-tree/RPM 与文档，再跑完整矩阵。完成前 F 保持需回归、总体不得声明完成 |
| 2026-09-06 | 完成 `F-34`～`F-35`；F-36进行中 | 新增规范化waiver并由release manifest固定路径/SHA；精确绑定0.1.0、facts cutoff、registry revision与三项授权gate。checker保留三项raw=false及详细原因，另输出全true的effective checks、waived gates、空blocking reasons和独立`ready_with_waivers`；CLI只对`ready`或该状态exit 0。无waiver/篡改/越权仍fail closed | release unit `29 passed`，含缺失、symlink、SHA、canonical、version、registry、额外gate、ack、authorization、gate inventory攻击；Ruff、严格mypy 4 files、动态source checker exit 0、diff通过 | waiver工件、release manifest/checker/CLI/readiness/tests；未提交 | 完成installed-tree必需项和RPM路径/字节验证，并增加installed waiver篡改证据；之后同步发布文档与完整回归。F继续需回归 |
| 2026-09-06 | 完成 `F-36`；F-37进行中 | installed checker把waiver列为必需普通文件并从`/usr/share/classscribe`读取同一固定路径/SHA。RPM install后逐字节/0644验证，原地篡改使状态立即回到blocked且三项effective=false，恢复source bytes后才重新放行；upgrade后仍为同一waiver并保留用户XDG数据。文档检索首条命令误在双引号中使用backtick触发shell替换，修正为单引号后无过时blocked状态匹配 | packaging/RPM `3 passed in 7.23s`；release unit `29 passed`；Ruff/mypy 4 files全绿；修正后的过时状态检索exit 1（无匹配） | installed required inventory、RPM expected paths/tamper test与六份发布文档；未提交 | 运行JSON/waiver SHA/frozen readiness一致性、受影响release/RPM组合与第10节完整根/tool/frontend/lock/architecture矩阵；全绿前F保持需回归 |
| 2026-09-06 | 完成 `F-37`；阶段F与总体waiver门闭合 | 冻结readiness、README、安全/限制/风险/追踪/阶段文档均明确raw与effective差异；新增工程gate不可被waiver覆盖测试。最终根、RPM、tool、Node24前端、静态、架构、17份lock、JSON、离线bundle及动态source release全绿。动态结果保留三项raw=false和五条原始原因，effective 10/10、blocking为空、waiver SHA有效，返回独立`ready_with_waivers`/exit 0 | root `499 passed, 1 expected skip` + RPM/packaging `3 passed`；tool `149 passed`；frontend `17 passed`+lint/format/type/build；Ruff、mypy 224+19、architecture `1 passed`+script OK、17 locks、JSON、diff全绿；offline verifier 20/SHA`55069eea…1357`；动态release exit 0；waiver/RPM定向最终`34 passed` | waiver/checker/CLI/readiness/release manifest、source+installed tests、文档与最终矩阵；未提交 | F-34～F-37全部完成，阶段F恢复已完成。总体自动发布门按用户明确授权以`ready_with_waivers`闭合；三项原始限制及GPU风险继续公开，不宣称为真实证据通过 |

## 10. 全量测试矩阵

本节是跨阶段总表。阶段内勾选不能替代本节的最终回归；本节每项都必须映射到具体测试文件、测试名和
最近一次结果。

### 10.1 单元与合同测试

1. [x] manifest 文件安全路径、排序、重复和 SHA/size 校验。
2. [x] remote code 可保留安全的上游根路径，仍拒绝绝对路径、`..`、symlink 和 control filename。
3. [x] bundle 索引 schema、manifest SHA、缺件、额外项、重复 ID 和路径逃逸。
4. [x] registry/revision/license/trust/worker/worker-lock 任一漂移均 fail closed。
5. [x] generator 对普通 Git、LFS 和 Xet 下载结果统一按实际字节哈希。
6. [x] selection 精确闭包、未分类文件、index 漏 shard 和额外文件拒绝。
7. [x] 连续生成两次产生字节一致结果。
8. [x] token 不进入输出、日志或异常文本。
9. [x] API 忽略/拒绝客户端提交的任意 manifest，并只使用 bundle 内容。
10. [x] disabled、未实现、无 manifest 和可安装模型的状态区分。

### 10.2 集成测试

- [x] 使用本地假 Hugging Face server 模拟固定 revision、LFS redirect、短写、超长、跨 host redirect、
  revision 漂移和 gated 403。
- [x] 从 bundle 取 manifest 完成 request → confirm → download → verify → health → active 全流程。
- [x] manifest SHA 被篡改时在联网前失败。
- [x] bundle 升级后展示文件 diff，失败恢复旧 active。
- [x] worker lock 变化后旧环境不能被运行时复用。
- [x] packaged resource root 和 source resource root 行为一致。

### 10.3 前端测试

- [x] 不再出现 manifest 文件 input。
- [x] installable 模型可发起预检。
- [x] disabled/未实现模型展示阻塞原因且不能安装。
- [x] gated 条款未接受时不能 confirm。
- [x] ForcedAligner 缺精确文本时不能 confirm。
- [x] 健康 WAV 上传与 UUID 回填。
- [x] 下载量、空间、manifest SHA 和 remote-code 披露正确。

### 10.4 RPM 与发布测试

- [x] RPM install/upgrade/erase 保留现有用户模型。
- [x] installed-tree bundle 完整且与 source tree 哈希一致。
- [x] release check 在删除任一 manifest、修改任一 byte 或更改 registry revision 时失败。
- [x] bundle verifier 能在完全离线环境运行。
- [x] release readiness 的原始 Phase 12 检查在没有真实模型验收时保持 false；manifest 不会使其通过，
  没有精确有效 waiver 时整体仍保持 blocked。

### 10.5 真实模型验收的统一证据模板

每个需要验收的模型复制并填写以下记录；不得只写“通过”：

```text
model_id:
repository:
revision:
manifest_sha256:
bundle_sha256 或 bundle 版本:
selection 审核人/日期:
目标 Fedora 版本与内核:
worker_lock 路径/SHA:
CPU/GPU 路由:
实测 VRAM（如适用，仅作为本次结果）:
健康 WAV 标识、SHA 与语言:
ForcedAligner 精确文本（如适用）:
gated 条款接受证据（如适用，不记录 token）:
预检下载量/安装量/临时空间:
固定 revision 与 resolved revision 对照:
断网/Bubblewrap 条件:
load 结果:
infer 结果摘要及协议校验:
unload 结果:
active 与 supply-chain.json 复验:
core 重启后离线复验:
执行日期:
命令/测试名:
证据路径或提交:
结论与遗留限制:
```

## 11. 总体验收标准

本修复的 manifest 工程验收只有以下条件同时满足才算闭合；总体完成仍须同时满足第 13 节全部发布门：

- [x] bundle 恰好覆盖注册表 20 个模型；
- [x] 每个 manifest 与 revision lock、license inventory、worker lock 和 registry 完全一致；
- [x] 所有文件的 size/SHA 来自固定 commit 的实际下载字节；
- [x] 文件选择经过人工策展且保持 worker 所需的原始布局；
- [x] remote code 被正确分类、哈希、展示并在无网络 sandbox 中运行；
- [x] 普通 API 和 WebUI 不接受用户替换发布 manifest；
- [x] 安装仍要求用户明确预检和一次性二次确认；
- [x] pyannote 条款和 token 处理满足 gated 要求；
- [x] 14 个当前使用模型均有真实目标机健康证据；
- [x] 未实现/disabled 模型不会显示为普通可安装；
- [x] source tree、RPM installed tree 和 release inventory 的 bundle 字节一致；
- [x] 离线 verifier、单元、集成、前端、RPM 和发布测试全部通过；
- [x] 文档不再声称缺失或未验收的工件已经完成。

## 12. 风险登记与缓解

| 风险 | 影响 | 必须执行的缓解 | 当前状态 |
|---|---|---|---|
| 上游固定 commit 删除或 gated 访问撤销 | 无法重新生成或安装 | 保留冻结 manifest 和许可证证据；发布前验证可访问性；不得绕过授权 | 已评估并缓解；未来上游撤销仍需发布前复核 |
| 仓库含多种权重格式 | 下载/磁盘膨胀或加载错误 | 人工 selection；权重 index 闭包检查；拒绝默认全仓下载 | 已缓解并验证 |
| Git/LFS/Xet 哈希语义混淆 | manifest 哈希错误 | 统一下载实际字节后流式 SHA-256 | 已缓解并验证 |
| remote-code 布局被重写 | Transformers 离线加载失败 | 保留上游相对布局，以 kind 和 sandbox 隔离 | 已缓解并验证 |
| pyannote 引用其他 repository | 单仓库 downloader 无法表达完整来源 | 审计传递依赖；必要时先设计多来源 manifest；不手工拼 cache | 已用固定多来源 provenance 解除 |
| manifest 存在但 worker 未实现 | 用户下载大量文件后健康失败 | 独立 `worker_implemented/installable/enabled` 状态，并继续单列 `manifest_available`；安装前阻止 | 已缓解并验证 |
| 生成器泄漏 token | 凭证暴露 | token 只进 header；日志脱敏；fixture 测试异常路径 | 已缓解并验证 |
| release check 只检查目录存在 | 缺件仍误通过 | 从索引展开并验证每个 manifest SHA 与交叉合同 | 已缓解并验证 |
| 上游相同 commit 返回不同字节 | 供应链异常 | 空缓存双重生成；字节不一致立即阻止发布 | 三次完整双 clean generation 均已验证 |
| 显式 release waiver 覆盖仍失败的原始门 | 源码许可权利、Phase 12 质量/性能和桌面兼容性可能被误解为已经取得或验证 | waiver 只改变 automated release blocking；以版本、facts cutoff、registry revision、精确 gate 清单和 SHA 固定；持续公开 raw checks、原因及免责声明；缺失、篡改、越权或任何非授权失败均 fail closed | 原始风险未解除；用户仅明确接受其对本次自动发布阻塞的影响，动态状态为 `ready_with_waivers` |

发生风险时在下表追加记录，不覆盖风险定义：

| 日期 | 风险 | 触发证据 | 影响阶段/模型 | 处理决定 | 解除条件 | 状态 |
|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — |
| 2026-09-04 | RPM 6 非 root 自定义数据库事务受限 | `rpm --dbpath <临时目录>/rpmdb --initdb` 返回 `.rpm.lock (Permission denied)`；目录可由同一用户正常写入；`sudo -n` 要求密码 | 阶段 A 全量集成门；阶段 F RPM 生命周期门 | 不跳过、不把 413 个通过项冒充全绿；阶段 A 标记阻塞，保留独立非 RPM 回归证据 | 在具备 root/sudo 授权的 Fedora 环境重跑 RPM 生命周期用例并通过 | 阻塞 |
| 2026-09-04 | RPM 6 非 root 自定义数据库事务受限解除 | 原始 RPM 生命周期用例在 `unshare --user --map-root-user` 的非特权用户命名空间中执行真实 install/upgrade/remove 事务并通过；未修改、模拟或跳过测试 | 阶段 A；阶段 F 仍须按 installed-tree 新合同重新验证 | 将用户命名空间内 root 作为本地 RPM 事务测试边界，保留宿主非 root 直接运行失败的环境事实 | 阶段 A 已解除；阶段 F 新增 RPM 合同完成后重跑其全部门禁 | 已解除 |
| 2026-09-05 | worker frozen no-editable provisioning 缺少本地 protocol 构建依赖 | C-14 两次真实 fresh-XDG 尝试均在 Bubblewrap 启动前失败；复制后的 `classscribe-protocol` 需要 Hatchling，而 uv 0.12 的 extra build dependencies 仍要求 preview | 阶段 A worker 环境合同；阶段 C C-14；共享全部 worker | 为每个 worker 声明 `classscribe-protocol` 的 Hatchling extra build dependency，并让统一 provisioner 执行 frozen sync 时启用 preview；不修改已冻结 resolution | 所有 worker `uv lock --check`、registry/manifest lock SHA、环境与健康回归通过，且真实断网 Bubblewrap load→infer→unload 成功 | 处理中 |
| 2026-09-05 | worker frozen no-editable provisioning 合同回归完成 | 11 个 worker frozen lock 均通过检查且实际 SHA 与 registry/Whisper manifest 保持一致；受影响环境和运行时原测试、静态门全部通过 | 阶段 A 已恢复；阶段 C C-14 仍需真实证明 provisioning 与 Bubblewrap 生命周期 | 保留统一 preview + extra build dependency 修复；继续用同一真实 fresh-XDG 流程验证 | 真实断网 Bubblewrap load→infer→unload 成功 | 部分解除；C-14 待验 |
| 2026-09-05 | worker provisioning 与 C-14 真实生命周期风险解除 | 详细诊断定位并修复 `venv --copies` 在 Fedora 默认 Python 3.14/目标 3.12 组合下导致 uv backend 解释器错配的问题；可信显式 config、uv 创建目标环境、sync 后解释器复制及 production sandbox 布局均由真实链覆盖 | 阶段 C C-14 已完成；阶段 A 仍须对最终实现重跑受影响回归后恢复 | 保留失败记录；对最终实现运行环境、sandbox、health、inference、resident、manager、静态及 lock/hash 门 | 最终受影响回归全部通过 | 真实风险已解除；回归待关闭 |
| 2026-09-05 | worker provisioning 回归风险完全解除 | 最终实现通过真实断网 C-14，随后受影响环境、sandbox、health、inference、resident、manager 与 worker-process 原测试共 64 项全部通过；worker locks 与冻结 SHA 不变 | 阶段 A 已恢复；阶段 C 可继续 C-15 | 无需进一步缓解；保留诊断与失败历史供审计 | 已满足 | 已解除 |
| 2026-09-05 | health worker AF_UNIX 路径超限 | C-16 在合法但较长的 fresh-XDG runtime 根重启，`health-<model_id>-<uuid>.sock` 超过 AF_UNIX 路径限制 | 阶段 A health/worker 合同；阶段 C C-16 | socket/output identity 改为固定短前缀加随机 UUID；增加长 runtime 根单测并以同一真实安装树重试 | 长路径原测试与真实离线重启 inference 均通过 | 处理中 |
| 2026-09-05 | health worker AF_UNIX 路径风险真实解除 | core 使用已打开 runtime 目录 fd 的短 procfs alias 连接，不把目录 fd 传给 Bubblewrap；worker 继续在 sandbox 内短路径建 socket。超过 AF_UNIX host 路径限制的真实 worker transport 测试和 C-16 同安装树重启均通过 | 阶段 C C-16 已完成；阶段 A 待最终受影响回归 | 重跑所有 WorkerProcess 消费者与静态门后恢复阶段 A | 最终受影响回归全部通过 | 真实风险已解除；回归待关闭 |
| 2026-09-05 | health worker AF_UNIX 路径回归关闭 | 最终 WorkerProcess 消费者组合 66 项与全部相关静态门通过；没有把 host runtime fd 传入 sandbox 子进程 | 阶段 A 恢复已完成；阶段 C C-16 证据保持 | 无 | 已满足 | 已解除 |
| 2026-09-05 | 目标 Fedora NVIDIA driver 状态再次失效 | 同一 RTX 4070 主机此前 `nvidia-smi` 成功，production bundle 后复核再次 exit 9：无法与 NVIDIA driver 通信；`espeak-ng`、`ffmpeg`、`bwrap` 可用 | 阶段 E 8.2 GPU 进入条件及所有 GPU/VRAM 真实健康项；CPU Whisper/FireRed 不受影响 | 回退 8.2 第三项并继续独立 CPU 验收；不使用历史瞬时可用输出或 CPU fallback 冒充 GPU 证据 | NVIDIA driver 恢复稳定，`nvidia-smi` 与实际 GPU worker load/infer/unload 均成功 | 阻塞 GPU 子矩阵；CPU 可继续 |
| 2026-09-05 | Qwen ASR冻结runtime backend与实际worker不一致 | registry与旧production manifest声明`qwen_asr_vllm`，但固定worker只依赖官方`qwen-asr`并调用`Qwen3ASRModel.from_pretrained`，load/infer均报告`qwen_asr_transformers`，lock无vLLM | 阶段A～D、两个Qwen ASR manifest、bundle及阶段E真实验收 | registry两条改为真实backend并增加registry↔adapter测试；旧bundle保持fail closed，禁止手改manifest，须重新完整双生成和独立验证 | 新20模型bundle双生成、离线验证、A～D门禁与新bundle下Whisper链全部通过 | 已解除；Qwen 0.6B CPU首次/断网重启真实链亦通过，GPU另列独立风险 |
| 2026-09-05 | 完整production再生成包含大流量与广泛覆盖副作用 | 权限审查在进程创建前拒绝：生成器将执行约140GB双clean固定revision传输，并覆盖现有20个单模型manifest和bundle共21个JSON；用户当前只明确授权pyannote gated条款标志 | 阶段B、A/C/D回归、阶段E及后续F全部不能在旧bundle上继续 | 不绕过、不以staging或手改工件规避审查；保留旧bundle fail-closed并继续不依赖生成的本地门禁 | 用户在获知约140GB下载及21个production JSON覆盖后明确授权该完整操作 | 已解除；用户已逐字确认完整下载与覆盖 |
| 2026-09-05 | Granite worker冻结环境缺少TorchAudio运行依赖 | 日语primary固定payload与frozen环境均完成后，真实Bubblewrap load在`GraniteSpeechFeatureExtractor`报`torchaudio`缺失；manager清理候选且无active。首次假设不存在的2.14同号版本重锁亦fail closed | 阶段A～D、Granite 4.1/TurboCTC两个manifest、bundle、阶段E日语primary及后续F | 依据官方stable ABI矩阵直接固定`torchaudio==2.11.0`，生成新lock/registry SHA并加防漂移测试；新环境import和4.64GB真实诊断CPU生命周期已通过，旧bundle保持拒绝 | 再次完整20模型双clean generation、独立离线verifier、A～D回归及新bundle下Granite默认安装/断网重启通过 | 生成、独立验证及A～D回归已解除；新bundle production Granite验收待执行 |
| 2026-09-05 | Granite CPU首次production inference耗尽主机内存/交换并卡死 | worker越过TorchAudio缺失后在CPU float32首次日语推理超过RPC deadline；用户同期观测内存爆满死机/卡死，失败后仍见swap 7.9/8.0GiB占用 | 阶段E日语primary、后续重型CPU验收与整机可用性；不改变已验证bundle/A～D | 用户明确授权后，以进程级24G RAM/2G swap硬上限原样复验模型、dtype、音频、bundle、worker和deadline；首次安装健康及独立断网新core重启均成功，进程退出后无残留且swap保持0 | 同一active完成两次load→infer→unload，backend/runtime offline/CPU路由一致，并确认宿主恢复 | CPU风险已解除；GPU子矩阵仍独立阻塞 |
| 2026-09-05 | Qwen ForcedAligner官方结果结构未被adapter解析 | 冻结`qwen-asr`返回`ForcedAlignResult.items`，adapter只接受旧式list/`timestamps`/`words`，真实健康因此在有效模型输出后报无token list | 阶段D需回归；阶段E第6项暂停，bundle/manifest/lock及已闭合模型不变；A/B/C已恢复 | 支持object/mapping的`items`同时保留token类型、非空与单调边界校验；用官方形状fixture及恶意/错误形状回归 | 定向worker测试、全量B/A/C/D门和同一production ForcedAligner首次/断网重启均通过 | 已解除；精确英语align两次真实成功 |
| 2026-09-06 | worker环境只按dependency lock寻址会复用陈旧adapter源码 | provisioner复制worker/protocol源码到cache，但target/complete只绑定`uv.lock` SHA；应用升级只改adapter时，旧环境继续运行旧代码。Qwen当前source与缓存副本SHA实证不同 | 阶段A～D再次需回归；阶段E ForcedAligner重试会虚假重复失败；production bundle/manifest/lock本身不变 | 环境identity同时包含lock SHA和规范化worker/protocol source SHA；源码变化选择新immutable子目录，旧目录不覆盖；补source-change resolve fail closed/ensure reprovision测试 | 新环境路径自动生成、旧环境保留且不可被resolve；全量A～D与真实ForcedAligner首次/断网重启通过 | 已解除；同源legacy兼容与陈旧Qwen拒绝均经真实环境证明 |
| 2026-09-06 | Nemotron 3.5 archive target未发布于冻结PyPI NeMo 2.7.3 | 真实archive要求`rnnt_bpe_models_prompt`；PyPI 2.7.3缺模块。NVIDIA模型卡要求Git main，NVIDIA参考容器明确同一缺口并固定commit。官方完整commit环境已构建且离线导入成功，但worker lock/registry变化使production bundle失效 | 阶段A～D需回归；阶段E两个Nemotron checkpoint及后续F；新增完整生成约140GB与21个JSON覆盖 | 固定完整NVIDIA commit `c9040511b2dbefe64767d9b8853b3a20d63a2cd2`，Linux-only完整uv解析、lock/source寻址及防漂移测试；禁止浮动main和`--no-deps`旁路 | 新bundle双clean generation、独立offline verifier、A～D全门及两个Nemotron首次/独立断网流式生命周期均成功 | 依赖闭包、生成及独立验证已解除；A～D全门与两个checkpoint正式验收待执行 |

## 13. 发布门与总体完成声明

总体完成声明必须逐项回答。无 waiver 时三项原始检查必须全部真实通过；采用本次明确授权的 waiver 时，
原始事实保持失败，但可在 `F-34`～`F-37` 与独立 `ready_with_waivers` 门全部通过后解除自动发布阻塞：

- [x] 阶段 A～F 均为 `已完成`，不存在未登记的跳过项。
- [x] 第 10 节全量测试矩阵均有最近一次可复核结果。
- [x] 第 11 节总体验收标准全部勾选。
- [x] 第 12 节高影响风险均已解除、控制或由本次明确 waiver 接受其自动发布影响；未把仍存在的原始风险伪写为关闭。
- [x] 生产 bundle 的 20 个成员、bundle SHA 和 release inventory 有冻结证据。
- [x] 真实模型记录没有把一次 VRAM/CPU 结果冒充 benchmark。
- [x] Phase 12 准确率/性能和桌面验收门只由其自身真实数据解除。
原始事实（waiver 不改变、也不勾成通过）：

- `source_license=false`：当前源码和自制 SVG 仍为 `NOASSERTION`，且未取得版权方再分发授权。
- `phase12_acceptance=false`：当前仍缺私有中/日/英 gold、真实长时质量/性能及完整结构化证据。
- `desktop_matrix=false`：当前 GNOME/KDE × Wayland/X11 与八类应用的真实交互矩阵尚未完成。

- [x] release-waiver 工件精确覆盖上述三项，完整保留原始失败、法律/质量/兼容性免责声明，并通过 source、
  installed-tree、篡改和 RPM 回归。
- [x] 动态 `scripts/validate_release.py --root .` 返回 `status=ready_with_waivers` 且退出码为 0；原始门
  仍为 7/10，effective gates 为 10/10，`blocking_reasons=[]`。

工程与 waiver 签核记录：

| 记录日期 | bundle/generator 版本 | bundle SHA | 验证摘要 | 已知限制 | 记录人/证据 |
|---|---|---|---|---|---|
| 2026-09-06 | v1 / classscribe-model-manifest 1 | `55069eea4fd82af5132a708ce1658217a652d4af16003352f7f15593e5c11357` | 20/20离线验证；14模型CPU生命周期；source/RPM/release一致；全矩阵通过 | 源码许可、Phase 12质量/性能、GPU与桌面实机门仍blocked | 自动验证记录及本计划逐项证据；不替代正式发布授权签字 |
| 2026-09-06 | v1 / release waiver 1 | `55069eea4fd82af5132a708ce1658217a652d4af16003352f7f15593e5c11357` | waiver SHA `b5a6f6fd…a8bf5`；raw 7/10、effective 10/10；source/RPM tamper矩阵与全回归通过；动态exit 0 | 三项raw失败、GPU、许可权利和实机质量/桌面限制均继续公开；状态不是无waiver的`ready` | 用户明确授权与`release/release-waivers.v1.json`；`ready_with_waivers` |

## 14. 建议的首个实现批次

第一批提交应只包含可独立审核的基础设施，不下载全部大模型：

1. [x] remote-code 路径合同修复及测试；
2. [x] bundle schema、loader 和离线 verifier；
3. [x] 独立 manifest tool 骨架和 lock；
4. [x] selection schema；
5. [x] Whisper Tiny 的真实 manifest 与端到端测试；
6. [x] release check 和 RPM 对单模型 bundle 的验证。

第 6 项所称单模型 bundle 只用于与一模型 fixture registry 配套的基础设施验证；正式发布合同仍要求 20
个成员。该批通过后，再按第 8.12 节顺序扩展到生产模型，从而先证明供应链设计正确，再承担多模型的大量
下载、gated 授权和 GPU 健康验收成本。

## 15. 原计划信息覆盖矩阵

本矩阵用于防止实施时遗漏原计划信息；“落点”均指本文件内部，不要求回看原文件。

| 原计划章节 | 本文件落点 | 覆盖内容 |
|---|---|---|
| 1. 文档目的 | 1 | 确定性供应链、发布期联网、运行期只读 bundle、用户确认、离线健康后激活、命令尚未实现 |
| 2.1 已有能力 | 2.1 | registry/locks/licenses/schema/manager/downloader/RPM 基础 |
| 2.2 缺失与矛盾 | 2.2 | 8 项现状缺口和 remote-code 三方冲突 |
| 2.3 修复边界 | 2.3 | 6 项负责范围与 5 项非范围 |
| 3. 目标状态 | 3.1、8.11 | selection、bundle 与 20 个 manifest 完整目录及 inventory/installability 区分 |
| 4.1 Remote code | 3.2、4 | v1 原始布局方案、v2 非本轮方案、代码/schema/docs/tests 修改 |
| 4.2 依赖隔离 | 3.3、5.3 | 独立工具目录、薄脚本限制、lock 发布检查 |
| 4.3 人工策展 | 3.4、5、8.3 | 精确 include/kinds/exclude、pattern 限定和示例 |
| 5.1 单模型 manifest | 3.5、5.6 | 完整 JSON 示例、字段与确定性规则 |
| 5.2 Bundle 索引 | 3.6、4、8.11 | schema、完整 JSON 示例、SOURCE_DATE_EPOCH、20 ID、排序/路径/SHA/隐私 |
| 6.1 输入和凭证 | 5.3、5.4 | 五类固定输入、可选 HF_TOKEN、0600/header/pyannote 条款 |
| 6.2 仓库树发现 | 5.5 | 完整 commit、递归 tree、失败条件、临时报告、selection 对照、Git/LFS 哈希说明 |
| 6.3 下载与哈希 | 5.6 | 0700 临时根、allowlist、cache 清理、特殊文件拒绝、实际 SHA/size、kind、排序、清理、官方链接 |
| 6.4 双重生成 | 5.7 | commit、payload、字节一致性和差异报告 |
| 6.5 命令 | 5.8 | discover/generate/offline verify 完整接口和联网边界 |
| 7.1 通用 Transformers | 8.3 | config/tokenizer/processor/权重/index/remote code/额外资源闭包和格式排他 |
| 7.2 FireRed | 8.4 | VAD/LID/AED/Punc 最低文件、目录布局、LLM disabled 策略 |
| 7.3 MOSS/ARK | 8.5 | 原始 remote-code 布局、动态引用解析、无网络健康 |
| 7.4 Qwen | 8.6 | 三个独立 manifest、不虚构 JA identity、aligner 文本/语言 |
| 7.5 Granite | 8.7 | 两个独立 manifest、单一运行格式、TurboCTC ranking 状态 |
| 7.6 Nemotron | 8.8 | 根级单一 `.nemo`、不共享/改名 |
| 7.7 pyannote | 5.4、8.9 | gated 授权、离线嵌套闭包、多来源和禁止拼 cache |
| 7.8 disabled | 7.4、8.10、8.15 | 四状态区分及五个 disabled/未实现模型策略 |
| 8.1 Bundle loader | 6.4 | 模型、加载函数、成员 SHA、全量交叉验证、查询、路径、resource 注入、失败健康状态 |
| 8.2 API | 7.3 | 按 ID 安装、两阶段响应、只读 manifest GET、未来专家 BYOM 隔离 |
| 8.3 列表状态 | 7.4 | 7 个新增字段/状态、installable 最低条件、enabled 独立语义 |
| 9. WebUI | 7.5、7.6 | 移除上传/JSON.parse、展示与禁用、专家流程、confirm、错误脱敏、WAV 上传/校验 |
| 10.1 Release inventory | 9.3 | schema/selection/bundle/20 manifests/tool lock/loader/verifier 及索引展开 |
| 10.2 RPM | 9.4 | config 打包、只读索引、20 manifests、`/usr/share`、禁止权重/秘密/cache/temp、升级保留 XDG |
| 10.3 文档 | 9.5 | 7 个文档及各自更新内容 |
| 11.1 单元测试 | 10.1 | 原 10 项全部保留 |
| 11.2 集成测试 | 10.2 | 假 HF server、完整链、篡改、升级恢复、lock、资源根 |
| 11.3 前端测试 | 10.3 | 原 7 项全部保留 |
| 11.4 RPM/发布测试 | 10.4 | 生命周期、树一致、篡改/registry 漂移、离线、Phase 12 blocked |
| 11.5 真实模型验收 | 8.12、10.5 | 10 级顺序、enabled 模型 8 项证据和记录模板 |
| 12. 分阶段顺序 | 4～9 | A～F 原任务、交付物和退出条件全部展开 |
| 13. 验收标准 | 11 | 原 13 项完成标准全部保留 |
| 14. 风险与缓解 | 12 | 原 9 项风险、影响与缓解全部保留，并增加进度栏 |
| 15. 首个实现批次 | 14 | 原 6 项批次范围、后续扩展顺序和成本理由 |

## 16. 文档变更记录

| 日期 | 变更 | 影响阶段 | 作者/证据 |
|---|---|---|---|
| 2026-09-04 | 将完整修复设计转换为可执行的 A～F 阶段计划；建立任务 ID、阶段门、测试矩阵、逐模型证据表、风险和进度记录；未实施任何代码 | 全部 | 本文件 |
| 2026-09-06 | A～F全部完成；冻结20模型production bundle与release/RPM/文档/真实健康及最终矩阵证据；外部发布门保持blocked | 全部 | bundle `55069eea…1357`、F最终矩阵与签核记录 |
| 2026-09-06 | 纠正总体完成语义：A～F仅表示manifest工程闭合；动态release readiness为7/10，源码许可、Phase 12与桌面矩阵三门未满足，因此总体目标保持阻塞 | 总体发布门 | 动态release checker、冻结readiness与第13节未勾选项 |
| 2026-09-06 | 用户明确授权一次release-blocking waiver，精确跳过source license、Phase 12 acceptance与desktop matrix；新增F-34～F-37并将F回退为需回归，原始失败不得改写 | F、总体发布门 | 用户授权、现有7/10 blocked基线与waiver合同 |
| 2026-09-06 | F-34～F-37、source/installed waiver攻击矩阵与完整最终回归通过；动态release状态为`ready_with_waivers`/exit 0，raw三项仍false | F、总体发布门 | waiver SHA`b5a6f6fd…a8bf5`、root 499+1 skip/RPM 3/tool149/frontend17/静态与release输出 |
